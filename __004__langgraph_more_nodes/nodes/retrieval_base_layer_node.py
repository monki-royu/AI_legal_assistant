"""
检索子节点2: 基础层必查节点
================================

【设计理念】实现"横向按需挂载 + 纵向逐级降级"双层检索策略:

1. 横向按需挂载(行业增强层 + 企查查资信数据源)
   ----------------------------
   系统根据 contract_type (合同类型) 在基础层通用检索之外, 动态挂载
   特定行业的数据源. 例如:
     - 建设工程  -> 住建部标准 / 建筑法实施条例
     - 金融借贷  -> 银保监会监管规定 / 贷款通则
     - 劳动合同  -> 劳动法司法解释 / 社保缴纳规定
   这样既保证通用法规覆盖, 又针对行业特性做精准补充.

   同时, 企查查资信数据源作为"共享数据源"挂载到基础层:
     - 从 doc_text 中自动提取甲乙方企业名称(正则匹配)
     - 调用 QiChaChaClient 查询工商/司法/经营资信信息
     - 将资信摘要转换为 citation 格式, 供所有智能体复用
     - 合同审核/合规审查/法律检索 三条链路均可调用

2. 纵向逐级降级(L1 -> L2)
   ----------------------------
   L1 高精度源: FAISS 向量索引 + 知识图谱 (结构化、权威性高)
       ↓ (结果不足 3 条时降级)
   L2 关键词源: 本地法律法规 txt 全文扫描 (兜底, 覆盖面广)
       ↓ (仍不足, 由 retrieval_enhance_query_node 触发 L3 LLM 伪检索)

本节点只负责 L1 和 L2 两级; L3 由下游 enhance_query 节点处理.
企查查资信作为横向共享数据源, 与行业增强层平级, 不参与纵向降级.
"""
# 📜 代码文字逻辑解析
# 本文件是法智引擎检索智能体子图的第2个节点: 基础层必查节点.
# 它是"横向按需挂载 + 纵向逐级降级"双层策略的主要落地节点,
# 负责完成 L1(FAISS向量) 和 L2(本地法规txt) 两级纵向降级检索,
# 同时根据 contract_type 横向动态挂载行业增强层数据源.
# 新增: 企查查资信数据源作为共享数据源, 从 doc_text 提取企业名称后
# 调用 QiChaChaClient 查询, 将资信摘要转换为 citation 供下游复用.
import os
import re
import json
from common.path_utils import root_dir
from __004__langgraph_more_nodes.agent_state import AgentState


# 📜 行业增强层数据源映射表
# key   : 合同类型(与 contract_classify_node 的输出一致)
# value : 该合同类型对应的行业专属数据源名称列表
# 这些数据源在实际工程中可对接行业法规库API, 此处先以本地txt文件名形式模拟,
# 文件若不存在则自动跳过, 不影响主流程.
_INDUSTRY_ENHANCEMENT_SOURCES = {
    "建设工程": ["住建部标准", "建筑法实施条例"],
    "金融借贷": ["银保监会监管规定", "贷款通则"],
    "劳动合同": ["劳动法司法解释", "社保缴纳规定"],
    "买卖合同": ["最高人民法院买卖合同司法解释"],
    "租赁合同": ["城市房屋租赁管理办法"],
}


def _try_faiss_search(query, top_k=5):
    """
    L1 级检索: 调用 FAISS 向量索引 + 知识图谱进行高精度语义匹配.

    Parameters
    ----------
    query : str
        待检索的自然语言查询(通常为合同片段或用户问题).
    top_k : int
        期望返回的最相似结果数.

    Returns
    -------
    list[dict] | None
        命中结果列表, 每项含 from_name / triple_text / score 等字段;
        若 FAISS 索引文件缺失或调用异常, 返回 None 触发降级.
    """
    try:
        # 延迟导入避免在 FAISS 未安装时影响整个模块加载
        from __003__create_neo4j_database.__003__vector_index import search
        # 构造 FAISS 索引文件及 id2text 映射文件的绝对路径
        index_path = os.path.join(root_dir, "__003__create_neo4j_database", "legal_embedding_faiss.index")
        id2text_path = os.path.join(root_dir, "__003__create_neo4j_database", "legal_embedding_faiss_id2text.pkl")
        # 仅当两个文件都存在时才调用检索, 否则视为索引未构建, 返回 None
        if os.path.exists(index_path) and os.path.exists(id2text_path):
            return search(query, top_k=top_k, index_path=index_path, id2text_path=id2text_path)
    except Exception as e:
        # 捕获任何异常并打印告警, 不抛出, 由调用方触发 L2 降级
        print(f"  ⚠️ L1 FAISS检索失败(将降级L2): {e}")
    return None


def _try_local_law_search(query, keywords, top_k=5):
    """
    L2 级检索: 扫描本地法律法规 txt 文件, 基于关键词命中做兜底检索.

    Parameters
    ----------
    query : str
        原始查询字符串(仅用于在 keywords 为空时退化提取关键词).
    keywords : list[str]
        预先抽取的检索关键词列表(由 retrieval_intent_decompose_node 提供).
    top_k : int
        期望返回的结果数.

    Returns
    -------
    list[dict]
        命中的法律条文列表, 每项含 title / article_no / content / source.
    """
    # 本地法规库目录: __001__clawler/法律法规/*.txt
    law_dir = os.path.join(root_dir, "__001__clawler", "法律法规")
    if not os.path.isdir(law_dir):
        # 法规库不存在直接返回空, 由 L3 兜底
        return []

    results = []
    # 若上层未提供关键词, 则按标点符号切分查询字符串作为退化关键词
    if not keywords:
        keywords = [w for w in query.replace("，", " ").replace("。", " ").split() if len(w) > 1]
    # 仍为空时, 取查询字符串前4字符作为最小关键词
    if not keywords:
        keywords = [query[:4]]

    # 遍历法规库中所有 txt 文件
    for fname in os.listdir(law_dir):
        if not fname.endswith(".txt"):
            continue
        fpath = os.path.join(law_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                lines = f.readlines()
            # 文件名(去扩展名)作为法规名, 例如 "中华人民共和国民法典.txt" -> "中华人民共和国民法典"
            statute_name = os.path.splitext(fname)[0]
            current_article = ""   # 当前条款编号, 如 "第七条"
            current_text = ""      # 当前条款累积正文
            # 逐行扫描, 以"第X条"作为条款切分边界
            for line in lines:
                line = line.strip()
                if line.startswith("#"):
                    # 跳过注释/标题行
                    continue
                # 简单判定: 行首是"第"且前10字符内含"条"则视为新条款起始
                if line.startswith("第") and "条" in line[:10]:
                    # 在进入新条款前, 检查上一条款是否命中关键词
                    if current_text and any(k in current_text for k in keywords):
                        results.append({
                            "title": statute_name,
                            "article_no": current_article,
                            "content": current_text[:200],
                            "source": fname,
                        })
                    current_article = line[:20]
                    current_text = line
                else:
                    current_text += line
            # 处理文件最后一条款
            if current_text and any(k in current_text for k in keywords):
                results.append({
                    "title": statute_name,
                    "article_no": current_article,
                    "content": current_text[:200],
                    "source": fname,
                })
        except Exception:
            # 单个文件解析失败不影响其他文件
            continue
        # 已收集足够结果则提前结束扫描
        if len(results) >= top_k * 3:
            break
    return results[:top_k]


def _try_industry_source_search(query, source_name, top_k=3):
    """
    横向行业增强层检索: 在指定行业数据源中检索相关条款.

    Parameters
    ----------
    query : str
        待检索查询(取自 retrieval_query).
    source_name : str
        行业数据源名称, 例如 "住建部标准".
    top_k : int
        期望返回结果数.

    Returns
    -------
    list[dict]
        命中的行业条款列表; 若对应数据源文件不存在则返回空列表.
    """
    # 行业数据源以本地 txt 文件形式存放于 data/industry_sources/ 目录
    industry_dir = os.path.join(root_dir, "data", "industry_sources")
    # 文件名规则: {source_name}.txt
    fpath = os.path.join(industry_dir, f"{source_name}.txt")
    if not os.path.exists(fpath):
        # 数据源未配置时直接返回空, 不影响主流程
        return []

    # 简单按关键词命中扫描(从query中提取长度>1的token作为关键词)
    keywords = [w for w in query.replace("，", " ").replace("。", " ").split() if len(w) > 1]
    if not keywords:
        keywords = [query[:4]]

    results = []
    try:
        with open(fpath, "r", encoding="utf-8") as f:
            lines = f.readlines()
        current_article = ""
        current_text = ""
        for line in lines:
            line = line.strip()
            if line.startswith("#") or not line:
                continue
            if line.startswith("第") and "条" in line[:10]:
                if current_text and any(k in current_text for k in keywords):
                    results.append({
                        "title": source_name,
                        "article_no": current_article,
                        "content": current_text[:200],
                        "source": f"行业增强层·{source_name}",
                    })
                current_article = line[:20]
                current_text = line
            else:
                current_text += line
        if current_text and any(k in current_text for k in keywords):
            results.append({
                "title": source_name,
                "article_no": current_article,
                "content": current_text[:200],
                "source": f"行业增强层·{source_name}",
            })
    except Exception as e:
        print(f"  ⚠️ 行业数据源[{source_name}]读取失败: {e}")

    return results[:top_k]


# ======================================================================
# 企查查资信数据源适配器 (共享数据源, 供所有智能体复用)
# ======================================================================
# 📜 以下两个函数实现了"企查查资信检索"作为检索智能体基础层的共享数据源.
# 设计目标: 让合同审核/合规审查/法律检索三条链路都能在检索阶段获取到
#           相对方的资信信息, 而不是只在合同审核的 credit_check_node 中才能查.
# 核心流程: (1) 从 doc_text 正则提取企业名称 → (2) 调用 QiChaChaClient → (3) 转为 citation
# ======================================================================


def _extract_party_names_from_text(text: str) -> list:
    """
    从合同/文档文本中提取企业名称(正则匹配, 不依赖 LLM).

    作用:
        在检索基础层阶段, party_identify_node 尚未执行, state 中没有
        party_a/party_b 字段. 本函数通过正则表达式从 doc_text 中快速
        提取可能出现的企业名称, 供企查查查询使用.
        匹配规则覆盖常见合同写法:
          - "甲方：XXX公司" / "甲方: XXX有限公司"
          - "乙方：XXX集团" / "乙方: XXX股份有限公司"
          - "出租人：XXX" / "承租人：XXX"
          - "买方：XXX" / "卖方：XXX"
          - 独立出现的 "XXX有限公司" / "XXX股份有限公司" / "XXX集团"

    参数:
        text (str): 合同/文档全文(截取前 3000 字符即可覆盖大部分场景).

    返回值:
        list[str]: 提取到的企业名称列表(去重, 最多 4 个); 无匹配时返回空列表.
    """
    # 若文本为空直接返回空列表
    if not text:
        return []

    # 截取前 3000 字符做匹配(合同开头通常包含甲乙方信息)
    sample = text[:3000]

    # 正则模式列表: 覆盖常见合同主体表述方式
    # 每个模式中 "公司|集团|事务所|研究院|中心|厂" 等后缀确保匹配到的是机构名而非自然人
    patterns = [
        # "甲方：XXX公司" / "甲方: XXX有限公司" (中英文冒号兼容)
        r'(?:甲方|发包方|委托方|采购方|买方|出租方|出让方|许可方|聘用方)[：:]\s*([^\n，,。；;]{2,30}(?:公司|集团|事务所|研究院|中心|厂|合作社))',
        # "乙方：XXX公司" / "乙方: XXX股份有限公司"
        r'(?:乙方|承包方|受托方|供货方|卖方|承租方|受让方|被许可方|受聘方)[：:]\s*([^\n，,。；;]{2,30}(?:公司|集团|事务所|研究院|中心|厂|合作社))',
        # 独立出现的 "XXX有限公司" (不带甲方/乙方前缀, 兜底匹配)
        r'([\u4e00-\u9fa5A-Za-z（）()]{4,25}(?:有限公司|股份有限公司|有限责任公司|集团有限公司|科技有限公司|股份有限公司))',
    ]

    names = []
    seen = set()  # 去重集合

    for pattern in patterns:
        # re.findall 返回所有匹配的分组(若模式中有括号则返回括号内容)
        matches = re.findall(pattern, sample)
        for m in matches:
            # 清理首尾空白
            name = m.strip() if isinstance(m, str) else str(m).strip()
            # 过滤掉通用名(甲方/乙方/未知等) 和 过短名称(少于 4 字符)
            if name and name not in ("甲方", "乙方", "公司", "未知") and len(name) >= 4:
                if name not in seen:
                    seen.add(name)
                    names.append(name)

    # 最多返回 4 个企业名称(避免过多查询拖慢检索)
    return names[:4]


def _try_credit_source_search(doc_text: str, task_type: str) -> list:
    """
    企查查资信数据源检索: 从 doc_text 提取企业名称, 查询资信, 转为 citation.

    作用:
        作为检索基础层的"共享数据源", 与 FAISS/本地法规/行业增强层平级.
        将企查查返回的资信信息(工商/失信/被执行/经营异常/行政处罚等)
        转换为标准 citation 格式, 融入 base_citations 供下游融合排序使用.

    触发条件:
        - task_type 为 contract_review / compliance_review / legal_research 时触发
        - 且能从 doc_text 中提取到至少 1 个企业名称
        - legal_qa / xiaohongshu / other 不触发(无需资信查询)

    参数:
        doc_text (str): 合同/文档全文(从中提取企业名称).
        task_type (str): 当前任务类型, 决定是否触发资信查询.

    返回值:
        list[dict]: 资信 citation 列表, 每项含 title/article_no/content/source/score;
                    未触发或查询失败时返回空列表(不影响主流程).
    """
    # ============== 触发条件判定 ==============
    # 仅合同审核/合规审查/法律检索 三条链路需要资信查询
    _CREDIT_ENABLED_TASKS = {"contract_review", "compliance_review", "legal_research"}
    if task_type not in _CREDIT_ENABLED_TASKS:
        return []

    # 从 doc_text 提取企业名称
    party_names = _extract_party_names_from_text(doc_text)
    if not party_names:
        # 未提取到企业名称, 跳过资信查询(不报错, 不影响主流程)
        return []

    # 延迟导入 QiChaChaClient: 避免在未安装 requests 等依赖时影响整个模块加载
    try:
        from common.qichacha_client import QiChaChaClient
    except Exception as e:
        print(f"  ⚠️ 企查查客户端导入失败, 跳过资信数据源: {e}")
        return []

    # 实例化客户端(构造函数内部会读取 .env 判断是否启用真实 API)
    try:
        client = QiChaChaClient()
    except Exception as e:
        print(f"  ⚠️ 企查查客户端初始化失败, 跳过资信数据源: {e}")
        return []

    citations = []

    # 遍历提取到的企业名称, 逐个查询资信
    for name in party_names:
        try:
            # 调用企查查统一入口查询企业资信
            info = client.query_company_credit(name)
            if not info or not isinstance(info, dict):
                continue

            # ============== 将资信信息转换为 citation 格式 ==============
            # 提取关键字段, 拼装资信摘要
            basic = info.get("basic_info", {}) or {}
            credit_score = info.get("credit_score", 0)
            risk_level = info.get("risk_level", "Unknown")
            mock = info.get("mock", True)
            mode = info.get("mode", "Mock")

            # 统计负面记录条数
            dishonest_count = len(info.get("dishonest", []) or [])
            executed_count = len(info.get("executed", []) or [])
            abnormal_count = len(info.get("abnormal", []) or [])
            penalties_count = len(info.get("penalties", []) or [])

            # 经营状态
            status = basic.get("status", "未知") if isinstance(basic, dict) else "未知"
            legal_person = basic.get("legal_person", "未知") if isinstance(basic, dict) else "未知"
            registered_capital = basic.get("registered_capital", "未知") if isinstance(basic, dict) else "未知"

            # 拼装资信摘要文本(供下游 research_context 使用)
            summary_parts = [
                f"企业名称: {name}",
                f"法定代表人: {legal_person}",
                f"注册资本: {registered_capital}",
                f"经营状态: {status}",
                f"信用评分: {credit_score}/100 ({risk_level})",
                f"失信被执行人: {dishonest_count} 条",
                f"被执行人: {executed_count} 条",
                f"经营异常: {abnormal_count} 条",
                f"行政处罚: {penalties_count} 条",
                f"数据来源: {'真实API' if not mock else '模拟数据'} ({mode})",
            ]
            content = "; ".join(summary_parts)

            # 构造 citation
            citations.append({
                "title": f"企查查·{name}资信报告",
                "article_no": f"信用评分:{credit_score} 风险等级:{risk_level}",
                "content": content,
                "source": "L1·企查查资信",
                "score": float(credit_score) if isinstance(credit_score, (int, float)) else 0,
            })

        except Exception as e:
            # 单个企业查询失败不影响其他企业查询
            print(f"  ⚠️ 企查查查询[{name}]失败: {e}")
            continue

    return citations


def retrieval_base_layer_node(state: AgentState):
    """
    基础层必查节点主函数.

    执行流程:
      1. L1 FAISS向量检索(高精度)
      2. 若 L1 结果不足 3 条 -> 降级 L2 本地法律文本检索(关键词)
      3. 横向按需挂载: 根据 contract_type 调用对应行业增强层数据源
      4. 横向共享数据源: 企查查资信查询(从 doc_text 提取企业名称, 查询资信转为 citation)

    Parameters
    ----------
    state : AgentState
        共享状态, 主要读取:
          - retrieval_query     : 检索查询字符串
          - retrieval_keywords  : 检索关键词列表
          - contract_type       : 合同类型(用于行业挂载)
          - doc_text            : 文档全文(用于企查查提取企业名称)
          - task_type           : 任务类型(决定是否触发企查查)

    Returns
    -------
    dict
        写入 base_citations 字段: 基础层(L1+L2+行业增强+企查查资信)合并后的引用列表.
    """
    print("检索 [2/5] 基础层必查(横向按需挂载 + 纵向L1 FAISS / L2 本地法规 + 企查查资信)")
    base_query = state.get("retrieval_query", "")
    keywords = state.get("retrieval_keywords", [])
    contract_type = state.get("contract_type", "")
    doc_text = state.get("doc_text", "")
    task_type = state.get("task_type", "")

    citations = []

    # ============== 纵向 L1: FAISS 向量检索 ==============
    print("  [2.1] L1 FAISS向量检索(高精度)...")
    faiss_results = _try_faiss_search(base_query, top_k=5)
    if faiss_results:
        for r in faiss_results:
            citations.append({
                "title": r.get("from_name", ""),
                "article_no": "",
                "content": r.get("triple_text", ""),
                "source": "L1·知识图谱",
                "score": r.get("score", 0),
            })
        print(f"    L1 命中 {len(citations)} 条")

    # ============== 纵向 L2: 本地法律文本检索(L1不足时降级) ==============
    if len(citations) < 3:
        print(f"  [2.2] L1结果不足3条(仅{len(citations)}条), 降级 L2 本地法律文本检索...")
        local_results = _try_local_law_search(base_query, keywords, top_k=5)
        for r in local_results:
            citations.append({
                "title": r["title"],
                "article_no": r["article_no"],
                "content": r["content"],
                "source": f"L2·{r['source']}",
            })
        print(f"    L2 补充 {len(local_results)} 条, 当前累计 {len(citations)} 条")
    else:
        print(f"  [2.2] L1结果充足, 跳过 L2 降级")

    # ============== 横向按需挂载: 行业增强层 ==============
    if contract_type in _INDUSTRY_ENHANCEMENT_SOURCES:
        sources = _INDUSTRY_ENHANCEMENT_SOURCES[contract_type]
        print(f"  [2.3] 横向挂载行业增强层[{contract_type}]: {sources}")
        for source_name in sources:
            industry_results = _try_industry_source_search(base_query, source_name, top_k=3)
            if industry_results:
                citations.extend(industry_results)
                print(f"    · {source_name}: 命中 {len(industry_results)} 条")
            else:
                print(f"    · {source_name}: 未命中(数据源未配置或无匹配)")
    else:
        print(f"  [2.3] 合同类型[{contract_type}]未配置行业增强层, 跳过横向挂载")

    # ============== 横向共享数据源: 企查查资信查询 ==============
    # 从 doc_text 提取企业名称, 调用企查查查询资信, 转为 citation 融入基础层
    # 与行业增强层平级, 作为"共享数据源"供合同审核/合规审查/法律检索三条链路复用
    print("  [2.4] 横向共享数据源: 企查查资信查询...")
    credit_citations = _try_credit_source_search(doc_text, task_type)
    if credit_citations:
        citations.extend(credit_citations)
        print(f"    企查查资信: 命中 {len(credit_citations)} 条")
    else:
        print(f"    企查查资信: 未命中(任务类型不符或未提取到企业名称)")

    print(f"  基础层最终输出 {len(citations)} 条引用")
    return {"base_citations": citations}
