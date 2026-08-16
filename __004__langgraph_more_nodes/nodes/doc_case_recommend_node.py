# -*- coding: utf-8 -*-
"""法律文书生成 - 类案推荐节点 (N_doc6)
====================================

【功能】
基于案情分析结果，从案例知识库中检索相似案例，输出给用户参考。
使用 common.retrieval_engine 的案例检索能力，按案由 + 事实描述查找最相似案例。

【流程位置】（文书生成链路中的并行分支 [5b/6]）
  [4/6] 法条校验 ─┬─→ [5/6] 风险提示 ─┐
                  └─→ [5b/6] 类案推荐（本节点，与风险提示并行）─┴─→ [6/6] 最终交付

【设计】
纯检索不涉及 LLM（No LLM），确定性高、成本低。返回案例按照相关度排序
（由检索引擎的 RRF 融合 + BM25 + FAISS 综合打分），每条附带
案号/法院/裁判日期/案情摘要/判决结果/引用法条，供用户参考同类案件的裁判结果。

【下游】
doc_final_delivery_node（最终交付节点）读取 similar_cases 组装最终交付物。
"""
# 导入 AgentState 类型：LangGraph 图中各节点共享的状态字典（TypedDict）
from __004__langgraph_more_nodes.agent_state import AgentState
# 导入 RAG 检索引擎实例（engine）：本节点用其 search_cases 检索案例知识库
from common.retrieval_engine import engine as retrieval_engine
# 导入微调数据收集工具：记录本节点的输入/输出为微调样本（可选旁路，失败静默）
from common.finetune_utils import collect_ft_sample


def doc_case_recommend_node(state: AgentState):
    """类案推荐节点: 基于案由+事实描述检索相似案例。"""
    # 【功能】基于案由与事实描述，从案例知识库检索 Top-5 相似案例并格式化输出。
    # 【参数】
    #     state (AgentState): LangGraph 共享状态字典，本节点读取：
    #         - case_summary (dict): 案情分析结果（含 case_type 案由、facts 事实列表）
    #         - dispute_type (str): 纠纷类型（case_type 缺失时的兜底案由）
    # 【返回值】
    #     dict（合并进 state），包含：
    #         - similar_cases: list[dict]，每条案例为
    #           {id, title, caseNo, court, caseType, date, summary, judgment,
    #            similarity, tags}（供最终交付节点组装"相似案例参考"小节）
    # 【逻辑】
    #     1. 读取案由（case_type 优先，其次 dispute_type）与事实描述；
    #     2. 构造检索查询串：有事实 → "案由 事实"；无事实 → 仅案由；
    #     3. 调用 retrieval_engine.search_cases(query, top_k=5, case_type=case_type)
    #        检索案例知识库（RRF+BM25+FAISS 融合打分，确定性算法，不调 LLM）；
    #     4. 遍历检索结果，把引擎返回的原始字段映射为前端友好的统一结构
    #        （英文键 → 中文语义键），并对长文本截断控制长度；
    #     5. 返回 similar_cases 列表。
    # 打印日志：标记进入文书生成第 5b 步"类案推荐（并行）"
    print("文书生成 [5b/6] 类案推荐(并行)")
    # 读取案情分析结果，缺失时兜底为空字典
    case_summary = state.get("case_summary", {}) or {}
    # 读取纠纷类型，缺失时为空字符串
    dispute_type = state.get("dispute_type", "")

    # 构造检索查询（Build Retrieval Query）
    # 案由：优先取 case_summary 中 LLM 抽取的 case_type，否则用用户填的 dispute_type
    case_type = case_summary.get("case_type", dispute_type)
    # 事实文本：把 case_summary 的 facts 列表用"；"拼接（用于检索相似案情）
    facts = "；".join(case_summary.get("facts", [])) or ""
    # 查询串：有事实则用"案由 事实"（信息更全、召回更准）；无事实则仅用案由
    query = f"{case_type} {facts}" if facts else case_type

    # 检索案例知识库（Case Retrieval）：
    # search_cases 是检索引擎的专用案例检索接口，返回按相关度排序的案例列表
    case_results = retrieval_engine.search_cases(
        query=query,          # 检索查询串
        top_k=5,              # 返回 Top-5 最相似案例
        case_type=case_type,  # 案由过滤条件（缩小检索范围）
    )
    # 初始化结果列表：存放格式化后的相似案例
    similar_cases = []
    # 遍历检索引擎返回的每条案例原始结果（c 为原始文档字典）
    for c in case_results:
        # 把引擎的原始字段映射为统一结构并截断长文本：
        # （字段映射是"适配层"设计：下游只依赖这里的稳定键名，不依赖引擎内部字段）
        similar_cases.append({
            "id": c.get("doc_id", ""),                    # 案例文档 ID
            "title": c.get("case_title", ""),             # 案例标题
            "caseNo": c.get("case_no", ""),               # 案号（如"(2023)京01民终1234号"）
            "court": c.get("court_name", ""),             # 审理法院名称
            "caseType": c.get("case_type", ""),           # 案件类型/案由
            "date": c.get("judge_date", ""),              # 裁判日期
            "summary": (c.get("case_summary", "") or "")[:300],  # 案情摘要（截取前 300 字符）
            "judgment": (c.get("judgment", "") or "")[:200],    # 判决结果（截取前 200 字符）
            "similarity": c.get("score", "N/A"),          # 相关度分数（引擎打分）
            "tags": c.get("cited_laws", []),              # 该案引用的法条列表（作标签）
        })

    # 打印日志：展示推荐的相似案例数量
    print(f"  推荐 {len(similar_cases)} 个相似案例")
    # ==== 微调数据收集 ====
    # 微调样本收集块（可选旁路）：记录本节点的输入/输出用于后续模型微调
    try:
        # 构造微调输入：取 state["input"]，转字符串并截取前 2000 字符
        _ft_input = str(state.get("input", "") or "")[:2000]
        # 注意：此行是"裸字典表达式"（bare dict expression），单独成行无任何效果，
        # 属于遗留的无操作语句，此处按原样保留，不改动逻辑。
        {"case_recommendations": state.get("case_recommendations", [])
}
        # 调用微调样本收集器（记录节点名、输入、输出、任务类型）
        collect_ft_sample("doc_case_recommend", _ft_input, _ft_output,
                          task_type=state.get("task_type", ""))
    except Exception:
        # 微调收集失败（如 _ft_output 未定义）：静默忽略，不影响主流程
        pass
    # 返回相似案例列表，供下游最终交付节点组装"相似案例参考"小节
    return {"similar_cases": similar_cases}
