"""N6 法律检索节点: 三层检索(基础/行业/条款), 降级为LLM伪检索"""
# ============================================================
# 文件名称: nodes/legal_research_node.py
# 文件作用: 法律检索(旧)
# ============================================================
# 【这个文件是干什么的？】
# 法律检索(旧)
#
# 【代码逻辑主线】
# 参见各函数前的【功能】【参数】【返回值】【逻辑】说明。
#
# 【新手建议】
# 先看主函数 -> 再看辅助函数。
#

# 📜 代码文字逻辑逻辑解析
# 本文件是 AI 法律助理(LangGraph 多智能体协作)流程中的"法律检索节点(N6)",
# 是整个合同审核流水线中负责"找法"的核心节点。它根据合同文本与合同类型,
# 检索出相关的法律法规条款, 供后续风险评估节点引用。核心逻辑采用"三层降级"
# 策略, 优先使用高质量检索源, 失败或结果不足时逐层降级:
# 1) 第一层: FAISS 向量检索 —— 调用 __003__create_neo4j_database 模块的 search 函数,
#    基于预构建的 FAISS 索引(legal_embedding_faiss.index)与 id2text 映射进行语义检索,
#    返回知识图谱中的三元组文本, 质量最高;
# 2) 第二层: 本地法律文本检索 —— 若 FAISS 结果不足 3 条, 则从 __001__clawler/法律法规
#    目录下的 txt 文件中按"第X条"分块, 进行简单关键词匹配, 返回相关条款;
# 3) 第三层: LLM 伪检索(降级) —— 若前两层结果不足 2 条, 则调用 LLM 根据合同内容
#    生成 3-5 条相关法条(JSON 格式), 作为兜底;
# 最终将所有检索结果汇总为 citations 列表, 并拼装成 research_context 文本供下游使用,
# 同时根据引用数量计算质量评分(每条 20 分, 上限 100)。该节点展示了"多源检索 + 降级
# 容错"的工程化设计, 可作为任何"RAG 检索 + LLM 兜底"场景的迁移模板。
# 导入 os 模块, 用于路径拼接与文件存在性检查



import os
# 导入 json 模块, 用于解析 LLM 返回的 JSON 数组
import json
# 导入 LangChain 的 HumanMessage 类型, 用于承载 LLM 提示词
from langchain_core.messages import HumanMessage
# 导入项目统一的 LLM 实例, 封装了模型选择与调用细节
from common.llm import my_llm
# 导入项目根目录路径, 用于拼接 FAISS 索引与法律文本目录
from common.path_utils import root_dir
# 导入 AgentState 类型, 它是整个 LangGraph 图中各节点共享的状态字典(TypedDict)
from __004__langgraph_more_nodes.agent_state import AgentState


def _try_faiss_search(query, top_k=5):
    """尝试FAISS检索, 失败返回None"""
    # 使用 try/except 包裹 FAISS 检索, 失败时返回 None 触发降级
    try:
        # 延迟导入 __003__create_neo4j_database 模块中的 search 函数, 避免未安装依赖时影响模块加载
        from __003__create_neo4j_database.__003__vector_index import search
        # 拼接 FAISS 索引文件路径
        index_path = os.path.join(root_dir, "__003__create_neo4j_database",
                                  "legal_embedding_faiss.index")
        # 拼接 id2text 映射文件路径(.pkl 格式, 存储向量索引到文本的映射)
        id2text_path = os.path.join(root_dir, "__003__create_neo4j_database",
                                    "legal_embedding_faiss_id2text.pkl")
        # 仅当索引文件与映射文件均存在时, 才调用 search 函数进行检索
        if os.path.exists(index_path) and os.path.exists(id2text_path):
            return search(query, top_k=top_k, index_path=index_path, id2text_path=id2text_path)
    except Exception as e:
        # 捕获异常并打印警告日志, 提示将降级为 LLM 检索
        print(f"  ⚠️ FAISS检索失败, 降级为LLM检索: {e}")
    # 失败或文件不存在时返回 None
    return None


def _try_local_law_search(query, top_k=3):
    """从本地法律txt文件中检索相关条款"""
    # 拼接本地法律文本目录路径(__001__clawler/法律法规)
    law_dir = os.path.join(root_dir, "__001__clawler", "法律法规")
    # 若目录不存在, 直接返回空列表
    if not os.path.isdir(law_dir):
        return []

    # 初始化结果列表
    results = []
    # 简单关键词匹配
    # 将查询字符串中的中英文标点替换为空格, 并按空格分词, 保留长度大于 1 的词作为关键词
    keywords = [w for w in query.replace("，", " ").replace("。", " ").split() if len(w) > 1]
    # 若分词后无有效关键词, 则取查询前 4 个字符作为关键词
    if not keywords:
        keywords = [query[:4]]

    # 遍历法律目录下的所有文件
    for fname in os.listdir(law_dir):
        # 仅处理 .txt 文件, 跳过其他格式
        if not fname.endswith(".txt"):
            continue
        # 拼接文件完整路径
        fpath = os.path.join(law_dir, fname)
        # 使用 try/except 包裹文件读取, 跳过读取失败的文件
        try:
            # 以 utf-8 编码读取文件所有行
            with open(fpath, "r", encoding="utf-8") as f:
                lines = f.readlines()
            # 以文件名(去掉扩展名)作为法规名称
            statute_name = os.path.splitext(fname)[0]
            # 按"第X条"找相关条款, 跳过注释行(# 开头)
            # 初始化当前条款编号与条款文本为空
            current_article = ""
            current_text = ""
            # 逐行处理
            for line in lines:
                # 去除首尾空白
                line = line.strip()
                # 跳过爬虫头部注释行
                if line.startswith("#"):
                    continue
                # 判断是否为条款起始行(以"第"开头且前 10 字符内含"条")
                if line.startswith("第") and "条" in line[:10]:
                    # 在开始新条款前, 检查上一条款文本是否包含任一关键词
                    if current_text and any(k in current_text for k in keywords):
                        # 命中则追加到结果列表, content 截取前 200 字符
                        results.append({
                            "title": statute_name,
                            "article_no": current_article,
                            "content": current_text[:200],
                            "source": fname,
                        })
                    # 更新当前条款编号(取前 20 字符)与文本
                    current_article = line[:20]
                    current_text = line
                else:
                    # 非条款起始行, 追加到当前条款文本
                    current_text += line
            # 最后一条
            # 文件遍历结束后, 检查最后一条条款是否命中关键词
            if current_text and any(k in current_text for k in keywords):
                results.append({
                    "title": statute_name,
                    "article_no": current_article,
                    "content": current_text[:200],
                    "source": fname,
                })
        except Exception:
            # 读取异常时跳过当前文件, 继续处理下一个
            continue
        # 若结果数量已达到 top_k * 3, 提前终止遍历(避免结果过多)
        if len(results) >= top_k * 3:
            break

    # 返回前 top_k 条结果
    return results[:top_k]


def legal_research_node(state: AgentState):
    """
    法律检索节点: 三层降级检索相关法律法规条款。

    作用:
        根据合同文本与合同类型, 依次尝试 FAISS 向量检索、本地法律文本检索、
        LLM 伪检索, 汇总相关法条引用, 并拼装研究上下文与质量评分写入 state,
        供后续风险评估与最终报告节点使用。

    参数:
        state (AgentState): LangGraph 共享状态字典, 需包含:
            - doc_text (str): 合同正文文本
            - contract_type (str): 合同类型(如"买卖"/"租赁")

    返回值:
        AgentState: 更新后的状态字典, 新增字段:
            - citations (list[dict]): 法条引用列表
            - research_context (str): 拼装的研究上下文文本
            - quality_score (int): 检索质量评分(0-100)

    可迁移性说明:
        本节点展示了"多源检索 + 降级容错"的工程化设计, 三层检索逻辑相互独立,
        可迁移到任何 RAG 场景(如:医疗检索、技术文档检索), 只需替换检索源与提示词。
    """
    # 打印日志, 标记进入法律检索阶段
    print("开始法律检索")
    # 从 state 读取合同正文, 截取前 2000 字符(避免过长影响检索与 LLM 输入)
    doc_text = state.get("doc_text", "")[:2000]
    # 从 state 读取合同类型
    contract_type = state.get("contract_type", "")

    # 构造检索查询: 若有合同类型则拼接"类型+合同+正文前200字", 否则用正文前 300 字
    query = f"{contract_type}合同 {doc_text[:200]}" if contract_type else doc_text[:300]

    # 初始化引用列表与研究上下文为空
    citations = []
    research_context = ""

    # 第一层: FAISS向量检索
    # 打印第一层检索日志
    print("  [1] FAISS向量检索...")
    # 调用 FAISS 检索, top_k=5
    faiss_results = _try_faiss_search(query, top_k=5)
    # 若 FAISS 返回结果, 逐条转换为统一格式的 citation
    if faiss_results:
        for r in faiss_results:
            citations.append({
                # 法规名称(知识图谱中的 from_name 字段)
                "title": r.get("from_name", ""),
                # 条文编号(FAISS 结果中无, 留空)
                "article_no": "",
                # 条款文本(知识图谱中的 triple_text 字段)
                "content": r.get("triple_text", ""),
                # 来源标记为知识图谱
                "source": "知识图谱",
                # 相似度得分
                "score": r.get("score", 0),
            })

    # 第二层: 本地法律文本检索
    # 仅当 FAISS 结果不足 3 条时, 触发第二层检索
    if len(citations) < 3:
        # 打印第二层检索日志
        print("  [2] 本地法律文本检索...")
        # 调用本地法律文本检索, top_k=5
        local_results = _try_local_law_search(query, top_k=5)
        # 将本地检索结果转换为统一格式并追加到 citations
        for r in local_results:
            citations.append({
                "title": r["title"],
                "article_no": r["article_no"],
                "content": r["content"],
                "source": r["source"],
            })

    # 第三层: LLM补充检索(降级)
    # 仅当结果仍不足 2 条时, 触发 LLM 伪检索
    if len(citations) < 2:
        # 打印第三层检索日志
        print("  [3] LLM伪检索(降级)...")
        # 构造 LLM 提示词, 要求根据合同内容列出 3-5 条相关法规, 返回 JSON 数组
        prompt = f"""请根据以下合同内容, 列出3-5条最相关的法律法规条款(包括法律名称和条文编号)。
合同内容: {doc_text[:1000]}

返回JSON数组: [{{"title":"法律名称","article_no":"第X条","content":"条文内容概要"}}]
只输出JSON。"""
        # 使用 try/except 包裹 LLM 调用与 JSON 解析
        try:
            # 调用 LLM 获取回复
            resp = my_llm.invoke([HumanMessage(content=prompt)])
            # 取出回复文本并去除首尾空白
            content = resp.content.strip()
            # 若回复中包含 markdown 代码块标记, 则提取其中的 JSON 数组部分
            if "```" in content:
                # 找到第一个左方括号位置
                start = content.find("[")
                # 找到最后一个右方括号位置(含), 用于切片
                end = content.rfind("]") + 1
                content = content[start:end]
            # 将 JSON 字符串解析为 Python 列表
            llm_citations = json.loads(content)
            # 遍历 LLM 返回的每条引用, 标记来源为"LLM生成"并追加到 citations
            for c in llm_citations:
                c["source"] = "LLM生成"
                citations.append(c)
        except Exception as e:
            # 捕获异常并打印警告日志, 不影响流程
            print(f"  ⚠️ LLM检索失败: {e}")

    # 生成研究上下文
    # 若存在引用, 将前 8 条拼装为研究上下文文本, 供下游节点使用
    if citations:
        research_context = "\n\n".join([
            f"【{c.get('title', '')}】{c.get('article_no', '')}\n{c.get('content', '')}"
            for c in citations[:8]
        ])

    # 质量评分
    # 根据引用数量计算质量评分, 每条 20 分, 上限 100
    quality_score = min(100, len(citations) * 20)

    # 将引用列表写入 state
    state["citations"] = citations
    # 将研究上下文写入 state
    state["research_context"] = research_context
    # 将质量评分写入 state
    state["quality_score"] = quality_score
    # 打印检索完成日志, 含引用数量与质量分
    print(f"完成法律检索: {len(citations)} 条引用, 质量分{quality_score}")
    # 返回更新后的 state, 供 LangGraph 继续流转
    return state


# 脚本直接运行时的自测入口
if __name__ == "__main__":
    # 构造一个包含合同文本与类型的测试 state
    s = AgentState(doc_text="买卖合同违约金", contract_type="买卖")
    # 调用节点获取结果
    r = legal_research_node(s)
    # 打印引用数量, 用于人工验证检索效果
    print(f"citations: {len(r.get('citations', []))}")
