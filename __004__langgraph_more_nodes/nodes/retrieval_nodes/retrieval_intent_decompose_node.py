"""
检索意图分解节点（retrieval_intent_decompose_node）是整个检索链路的第一站，
承担着分析用户输入、拆解检索意图以及挂载知识源的核心职责。
该节点的设计哲学遵循"单一职责"、"降级容错"与"上游优先"三大原则，确保在不同业务场景下都能输出精准、一致的检索规划。

在整体工作流程上，该节点首先会根据用户的任务类型（task_type）挂载不同的知识源（涵盖法律法规、司法解释、类案、行业标准等）。
统一路径（所有任务走同一条路）:
- 优先复用上游规划好的 retrieval_query（contract_review/compliance_review 来自 llm_query_extract_node；
  legal_document_gen 来自 doc_query_plan_node）；
- 上游没有时用 user_input 兜底（legal_qa/legal_research/case_search 直达检索子图）；
- 然后统一做 LLM 关键词提取 + alias_normalizer 归一化。
关键词不再由上游产出，全部由本节点统一提取，按重要性从高到低排列。

此外，代码在底层设计了完善的容错与归一化机制。
alias_normalizer 负责对查询和关键词进行同义词扩展与标准化；
而在知识源映射的导入策略上，采用了优雅的降级方案——当遇到 ImportError 时，系统会自动回退到默认的5键兜底集合，
并通过 RuntimeWarning 提醒运维，既保证了服务的可用性，又避免了静默掩盖真正的配置错误。

核心逻辑 (V5 统一路径):
  所有任务走同一条路:
    1. 挂载知识源 (TASK_SOURCE_DEFAULTS + KEYWORD_RULES)
    2. base_query = 上游 retrieval_query 或 user_input 兜底
    3. LLM 关键词提取 (按任务类型分化描述, 按重要性排序)
    4. alias_normalizer 归一化
"""

import os
import re
import warnings

from common.llm import my_llm
from common.path_utils import root_dir
from common.alias_normalizer import normalize_candidates
from __004__langgraph_more_nodes.agent_state import AgentState
from langchain_core.messages import HumanMessage
# 合法 domain key 集合 (白名单) —— 唯一真相源在 retrieval_entity_recall_node._SOURCE_INDEX_MAP。
# 本模块只 import 它的 keys() 当白名单, 绝不另维护一份 index/id2text 映射。
# 正常路径同步命名; 仅 ImportError(可选依赖未装)时降级到默认 5 键并 RuntimeWarning 提醒。
try:
    from __004__langgraph_more_nodes.nodes.retrieval_nodes.retrieval_entity_recall_node import (
        _SOURCE_INDEX_MAP,
    )
    KNOWN_DOMAIN_SOURCES = frozenset(_SOURCE_INDEX_MAP.keys())
except ImportError as exc:  # 仅对「依赖/模块不可用」降级
    warnings.warn(
        "retrieval_entity_recall_node 导入失败 (ImportError: %s), "
        "KNOWN_DOMAIN_SOURCES 使用默认 5 键兜底 (laws/regulations/cases/"
        "industry_sources/interpretations). 若依赖已安装, 请检查 "
        "retrieval_entity_recall_node 顶层的 FAISS/Neo4j 初始化报错." % exc,
        category=RuntimeWarning,
        stacklevel=2,
    )
    # 默认键必须与 data/knowledge_base/index/*.index 前缀完全一致, 不引入临时 value=None 字典
    # (frozenset 字面量表达语义: 我们只关心「合法源集合」, 与映射 value 无关).
    # 使用 frozenset 而非 set 的原因：frozenset 是不可变集合，可作为模块级常量，
    # 且支持 hashable 操作（如作为 dict 的 key），适合用于「合法源集合」这种只读校验场景。
    KNOWN_DOMAIN_SOURCES = frozenset([
        "laws", "regulations", "cases", "industry_sources", "interpretations",
    ])

# 任务类型 → 知识源挂载映射 (V5 统一路径对齐用户定稿方案)
# 挂载矩阵（详细讨论见 docgen_subgraph V5 设计）:
#   ┌──────────────────────┬──────────────────────────────────┬──────────────────────────┐
#   │ 任务                 │ 默认 domain 源                    │ industry_sources         │
#   ├──────────────────────┼──────────────────────────────────┼──────────────────────────┤
#   │ contract_review      │ laws+reg+intp+cases (4 源)       │ 关键词触发               │
#   │ compliance_review    │ laws+reg+intp+cases (4 源)       │ 关键词触发               │
#   │ legal_qa             │ laws+reg+intp+cases (4 源)       │ 关键词触发               │
#   │ legal_research       │ laws+reg+intp        (3 源, 纯法规, 不含 cases/industry_sources) │ 不触发 │
#   │ case_search          │ cases                (1 源, 跳融合)                             │ 不触发 │
#   └──────────────────────┴──────────────────────────────────┴──────────────────────────┘
# 关于北大法宝付费接口:
#   - 仅当 quality_gate_retry_node 达到 MAX_QUALITY_RETRIES 仍低于阈值时, 由它写入
#     fabao_retry_eligible=True; beida_fabao_gate_node 仅在该标记为 True 时才中断询问用户,
#     随后才调用 common/mcp_beidafabao.py 里的 MCP 客户端。
TASK_SOURCE_DEFAULTS = {
    "contract_review": {
        "domain": ["laws", "regulations", "interpretations", "cases"],
    },
    "compliance_review": {
        "domain": ["laws", "regulations", "interpretations", "cases"],
    },
    "legal_research": {
        "domain": ["laws", "regulations", "interpretations"],
    },
    "case_search": {
        "domain": ["cases"],
    },
    "legal_qa": {
        "domain": ["laws", "regulations", "interpretations", "cases"],
    },
}
# 关键词触发规则: 检测到特定关键词时追加对应 DOMAIN 类知识源 (industry_sources / interpretations / regulations)
#   - industry_sources 触发收窄到「建设工程 / 金融贷款 / 房地产开发」这类真的有部门规章可引用的窄场景,
#     避免一般租赁合同/婚姻家庭/普通侵权挂 industry_sources 挤占融合 Top-12 名额。
#     描述概念时写"行业标准", 涉及 domain key 名统一写 industry_sources。
KEYWORD_RULES = [
    # 行业标准 (industry_sources) 触发: 建设工程 / 金融贷款 / 房地产开发 —— 与
    # doc_query_plan_node._load_industry_trigger_keywords() 读取的 -> industry_sources 目标完全一致;
    # 注意这里必须写 "industry_sources" (和 KNOWN_DOMAIN_SOURCES frozenset / 实际 FAISS
    # 文件名 industry_sources_faiss.index 的前缀一致), 不能写简版 "industry"。
    (["建设工程", "工程款", "承包人", "施工", "竣工验收", "质量保修", "质保期",
      "分包", "转包", "招投标", "中标", "监理", "工程造价"],
     ["industry_sources"]),
    (["贷款通则", "金融借款", "银行贷款", "放款", "催收",
      "融资租赁", "信托", "保理", "典当"],
     ["industry_sources"]),
    (["商品房买卖", "预售", "房地产开发", "容积率", "住建部", "物业管理",
      "预售许可证", "交付标准"],
     ["industry_sources"]),

    # 司法解释 / 行政法规: 刑事 / 行政 / 监管类强需求
    (["刑事", "犯罪", "刑罚", "量刑", "公诉", "羁押"],
     ["interpretations"]),
    (["行政", "处罚", "许可", "规定", "办法", "条例", "监管", "合规"],
     ["interpretations", "regulations"]),
]

def _build_source_mounts(task_type: str, user_query: str, doc_text: str = "") -> dict:
    """作用：根据任务类型和关键词规则，动态构建知识源挂载列表。
核心逻辑：
•默认源读取：从 TASK_SOURCE_DEFAULTS 字典中根据 task_type 获取默认知识源列表。如果 task_type 不存在，回退到 legal_qa 的默认源。
•关键词追加：对于多源任务（contract_review、compliance_review、legal_qa），遍历 KEYWORD_RULES 中的每一组关键词，如果用户查询或合同文本中包含任一关键词，则追加对应的额外知识源。
•合法性校验：追加的源必须同时满足两个条件：(1) 在 KNOWN_DOMAIN_SOURCES 中（确保是合法的 domain key）；(2) 尚未在 domain_sources 中（避免重复）。
•单源/不追加任务：legal_research 和 case_search 不执行关键词追加，直接返回默认源。
关键词追加的 break 语义：KEYWORD_RULES 中的每个元素是一个 (keywords_list, sources_list) 元组。内层 for kw in keywords 循环中，一旦命中第一个关键词就 break，跳出的是内层循环，外层循环继续检查下一组关键词规则。这意味着多组关键词规则可以同时触发，但每组规则内只取第一个命中的关键词。
返回值结构： - domain：最终的知识源列表（默认源 + 关键词追加的源） - mounted_sources：与 domain 相同，表示实际挂载的源
    """
    defaults = TASK_SOURCE_DEFAULTS.get(task_type, TASK_SOURCE_DEFAULTS.get("legal_qa", {}))
    domain_sources = list(defaults.get("domain", []))

    # 不做关键词追加的任务（legal_research 有 3 个源，case_search 有 1 个源，均不追加）
    if task_type in ("legal_research", "case_search"):
        return {
            "domain": domain_sources,
            "mounted_sources": list(domain_sources),
        }

    # 多源融合任务: 根据关键词追加额外 domain 源
    # （industry_sources / interpretations / regulations 三类合法 KNOWN 源）。
    extra_from_keywords = []
    combined_text = f"{user_query} {doc_text}"  # 将用户查询与合同正文拼接，使关键词匹配能同时覆盖用户意图和文档上下文
    for keywords, extra_sources in KEYWORD_RULES:
        for kw in keywords:
            if kw in combined_text:
                for src in extra_sources:
                    if src in extra_from_keywords:
                        continue
                    if src in KNOWN_DOMAIN_SOURCES and src not in domain_sources:
                        domain_sources.append(src)
                        extra_from_keywords.append(src)
                break  # break 跳出的是内层 `for kw` 循环，不会 break 外层 `for KEYWORD_RULES` 循环，
                       # 因此每个关键词组都会独立检查，只要该组内任一关键词命中就追加对应源

    mounted_sources = list(domain_sources)
    return {
        "domain": domain_sources,
        "mounted_sources": mounted_sources,
    }


# 检索意图 7 类闭集 (钉死在 __002__extract_information/__001__extract_legal_data.py
#   第 476 行的 Article 锚定关系: DEFINES/HAS_LIABILITY/HAS_PENALTY/REGULATES/HAS_CONDITION/INVOLVES)。
# 用确定性标记词分类, 不调用 LLM —— 与「关键词提取」解耦, 避免 1c 通道里那种
# 用查询字面 marker 粗略判定又被同义词逻辑纠缠的 hack。
# 优先级由具体到宽泛, 命中即返回 (definition/penalty 比 liability 更具体, 先判)。
_INTENT_MARKERS = {
    "definition": ["定义", "含义", "概念", "意思", "何为", "什么叫", "是什么",
                   "指什么", "怎么理解", "解释为"],
    "penalty":    ["处罚", "罚则", "罚款", "罚金", "惩罚"],
    "liability":  ["责任", "赔偿", "承担", "后果", "怎么赔", "如何赔", "怎么算"],
    "regulates":  ["规制", "规范", "调整", "约束"],
    "condition":  ["条件", "情形", "前提", "要件", "情况下"],
    "parties":    ["主体", "当事人", "谁可以", "由谁", "双方", "各方"],
}

def _detect_retrieval_intent(text: str) -> str:
    """返回 7 类意图之一: definition/penalty/liability/regulates/condition/parties/general。

    确定性标记词分类, 不做 LLM 调用。命中多个意图时按 _INTENT_MARKERS 顺序返回最具体的。
    """
    t = text or ""
    for intent, markers in _INTENT_MARKERS.items():
        if any(m in t for m in markers):
            return intent
    return "general"


def _detect_retrieval_intents(text: str) -> list:
    """多分面意图检测: 把查询按分面分隔符(|/｜/换行)拆成多条子问句, 逐句判定意图,
    返回去重(保序)的意图列表。

    【修复的缺陷】原 _detect_retrieval_intent 只在用户原始短输入上跑一次、返回单个字符串,
    导致上游 llm_query_extract 产出的「每条款×双视角」多分面扁平查询(retrieval_query 用 ' | '
    连接)被压成单一全局意图, 召回偏置(_dedup_with_intent_bias)只能照顾一种关系偏好。
    多子查询(如 面积不一致/押金过高/违约金调减/备案登记)本应各自带 condition/liability/
    penalty/regulatory 等意图, 这里一次性全部检出。

    【返回】保序去重后的意图列表; 无任何标记词命中时返回 ["general"]。
        若同时含具体意图与 general, 把 general 置末尾(具体意图优先)。
    """
    if not text:
        return ["general"]
    facets = re.split(r"[|｜\n]+", text)
    seen = []
    for f in facets:
        f = (f or "").strip()
        if not f:
            continue
        it = _detect_retrieval_intent(f)
        if it not in seen:
            seen.append(it)
    if not seen:
        return ["general"]
    # general 置末尾(具体意图优先), 仅当存在具体意图时
    if "general" in seen and len(seen) > 1:
        seen = [x for x in seen if x != "general"] + ["general"]
    return seen


def retrieval_intent_decompose_node(state: AgentState):
    """检索意图分解节点: 根据任务类型/合同正文/用户输入挂载知识源 + LLM 关键词提取

    统一路径（所有任务走同一条路）:
        1. 挂载知识源: 按 task_type 读 TASK_SOURCE_DEFAULTS + KEYWORD_RULES 关键词触发
        2. 确定 base_query: 优先用上游 retrieval_query (contract_review/compliance_review
           来自 llm_query_extract_node；legal_document_gen 来自 doc_query_plan_node)，
           上游缺失时用 user_input 兜底
        3. LLM 关键词提取: 从 base_query 提取关键词，按重要性从高到低排列，
           供 entity_recall 的 main_kw 使用
        4. alias_normalizer 归一化: 对 query 和 keywords 做候选扩展

    prompt 按任务类型分化描述:
        - legal_qa:       法律问答
        - legal_research:  法规查询
        - case_search:     案例检索
        - contract_review : 合同审核
        compliance_review : 合规审查
        legal_document_gen:法律文书生成
    """
    print("检索1: 意图分解 / 查询规划")

    doc_text = state.get("doc_text", "")[:8000]  # 截断至8000字符，避免过长的合同文本占用过多上下文窗口
    contract_type = state.get("contract_type", "")
    user_query = state.get("input", "")[:1000]  # 截断用户输入至1000字符，控制 LLM prompt 长度
    task_type = state.get("task_type", "")

    mounts = _build_source_mounts(task_type, user_query, doc_text)
    mounted_sources = mounts["mounted_sources"]

    print(f"  [1.1] 知识源挂载: domain={mounts['domain']}, mounted={mounted_sources}")

    # ===== 统一路径: 确定基础查询 =====
    # 优先级链: upstream retrieval_query > contract_type+doc_text > doc_text > user_query
    # 优先复用上游规划好的 retrieval_query (contract_review/compliance_review 来自
    # llm_query_extract_node; legal_document_gen 来自 doc_query_plan_node)；
    # 上游没有时用 user_input 兜底 (legal_qa/legal_research/case_search 直达检索子图)。
    upstream_q = (state.get("retrieval_query", "") or "").strip()
    if upstream_q:
        base_query = upstream_q
    elif contract_type:
        base_query = f"{contract_type}合同 {doc_text[:6000]}"
    elif doc_text:
        base_query = doc_text[:800]
    else:
        base_query = user_query[:800]

    # ===== LLM 关键词提取 (所有任务统一) =====
    # 按任务类型构造 prompt 描述
    if task_type == "legal_research":
        task_desc = "法规查询"
    elif task_type == "case_search":
        task_desc = "案例检索"
    elif task_type == "contract_review":
        task_desc = "合同审核"
    elif task_type == "compliance_review":
        task_desc = "合规审查"
    elif task_type == "legal_document_gen":
        task_desc = "法律文书生成"
    else:
        task_desc = "法律问答"

    print(f"  [1.2] {task_type} LLM 关键词提取 (task_desc={task_desc})")

    import json as _json
    retrieval_keywords = []
    try:
        prompt = (
            f"你是一名法律检索规划助手。用户的要求是{task_desc}，"
            f"请提取法律检索关键词，按与查询的相关性从高到低排列。\n"
            "\n"
            "输出格式(严格按以下一行, 不要有其他说明):\n"
            "KEYWORDS=[\"最重要的词\",\"次重要的词\",...]\n"
            f"用户输入: {base_query[:800]}"
        )
        resp = my_llm.invoke([HumanMessage(content=prompt)])
        content = resp.content.strip()
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("KEYWORDS"):
                s = line.find("[")
                e = line.rfind("]") + 1
                if s >= 0 and e > s:
                    try:
                        kws = _json.loads(line[s:e])
                        if isinstance(kws, list):
                            retrieval_keywords = [str(k) for k in kws if str(k).strip()]
                    except Exception:
                        pass
        if not retrieval_keywords:
            # LLM 返回空结果时的降级策略：用中文标点分割 + 过滤单字符词
            q = base_query.replace("，", " ").replace("。", " ")
            retrieval_keywords = [w for w in q.split() if len(w) > 1]
    except Exception:
        # LLM 调用异常时的降级策略：同上的简单分词
        q = base_query.replace("，", " ").replace("。", " ")
        retrieval_keywords = [w for w in q.split() if len(w) > 1]

    # alias_normalizer 归一化
    candidate_queries = normalize_candidates(base_query)
    normalized_keywords = []
    for kw in retrieval_keywords:
        normalized_keywords.extend(normalize_candidates(kw))
    retrieval_keywords = list(dict.fromkeys(normalized_keywords))  # dict.fromkeys 保持原始顺序的同时去重，
                                                                   # 等价于 OrderedDict 去重，比 set 去重更合适因为需要保留重要性排序

    print(f"  [1.3] 归一化: query → {len(candidate_queries)} 候选, "
          f"keywords → {len(retrieval_keywords)} 个")

    # ===== 检索意图判定 (7 类闭集, 确定性标记词, 不调 LLM) =====
    # 单值 intent: 沿用原逻辑在「用户原始短输入」上判定, 作为锚点/兼容字段保持不变;
    # 多分面 intents: 在扁平查询(每条款×双视角用 ' | ' 连接, 即 base_query, 上游
    #   retrieval_query 解析后的值)上按分面判定, 返回意图集合, 供召回偏置同时照顾多种
    #   关系偏好(如 condition+liability+penalty)。base_query 在 upstream_q 存在时即等于
    #   带 ' | ' 分隔的扁平查询; 上游无扁平查询时回退为 doc_text/user_query(无 ' | ', 单分面)。
    anchor = retrieval_keywords[0] if retrieval_keywords else ""
    intent = _detect_retrieval_intent(user_query)
    intents = _detect_retrieval_intents(base_query)
    print(f"  [1.4] 检索意图: {intent} (anchor={anchor}); 多分面意图={intents}")

    # 注: 不再返回 extra_from_keywords —— 其信息已完整包含在 mounted_sources 中
    #     (后者 = 默认源 ∪ 关键词追加源)，且该键全项目零消费方。
    return {
        "retrieval_query": base_query,
        "retrieval_candidates": candidate_queries,
        "retrieval_keywords": retrieval_keywords,
        "retrieval_intent": intent,
        "retrieval_intents": intents,
        "retrieval_anchor": anchor,
        "mounted_sources": mounted_sources,
        "domain_sources": mounts["domain"],
    }