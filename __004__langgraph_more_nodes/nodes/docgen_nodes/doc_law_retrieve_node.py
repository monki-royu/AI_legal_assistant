# -*- coding: utf-8 -*-
"""文书生成 - 法条检索节点 (legal_research)
============================================

【架构位置】
    位于 doc_query_plan 之后, 与 doc_case_retrieve 并发执行
    (由 doc_parallel_retrieve_node 通过 ThreadPoolExecutor 并发编排)。

【职责】
    调用 legal_response_sync(task_type="legal_research"), 走检索子图 Path A。
    intent_decompose 自动挂载 3 源 (laws+regulations+interpretations)
    + KEYWORD_RULES 触发 industry。
    返回 law_citations / law_quality_score / law_research_context 三个字段。

【为什么是独立节点函数】
    与 parallel_dual_review_node 编排 compliance_review_node /
    contract_ai_review_node 的模式一致: 本函数是单一职责的独立节点文件,
    由 doc_parallel_retrieve_node 并发调用。它不单独注册为 graph 节点,
    只作为并发子任务的函数单元。

【读取】
    - law_retrieval_query (str): doc_query_plan 输出, 面向"找法条"的精准查询
    - input (str): 兜底

【写入】
    - law_citations (List[Dict]): 法条引用列表(每条补 source_ref 供 clause_fill 使用)
    - law_quality_score (float): 检索质量分
    - law_research_context (str): 检索上下文
"""

import time

from __004__langgraph_more_nodes.agent_state import AgentState


def doc_law_retrieve_node(state: AgentState) -> dict:
    """法条检索节点: 调 legal_research 子图, 返回 law_citations。"""
    from __004__langgraph_more_nodes.langgraph_main import legal_response_sync

    print("文书生成 [法条检索] legal_research 子图（走 Path A）")
    query = state.get("law_retrieval_query", "") or state.get("input", "")
    print(f"  查询: {str(query)[:80]}…")

    t0 = time.time()
    try:
        result = legal_response_sync(query, task_type="legal_research",
                                     retrieval_query=query)
    except Exception as e:
        print(f"  ⚠️ 法条检索子图失败: {e}，使用空结果兜底")
        result = {"citations": [], "quality_score": 0.0, "research_context": ""}

    elapsed = time.time() - t0
    citations = result.get("citations", []) or []
    quality = float(result.get("quality_score", 0) or 0)
    research_context = result.get("research_context", "") or ""

    # 给每条 citation 补 source_ref（供 clause_fill SYSTEM_PROMPT 使用）
    for idx, c in enumerate(citations):
        if isinstance(c, dict) and "source_ref" not in c:
            src_id = str(c.get("source_id") or c.get("source") or "unknown")
            if "::" in src_id:
                src_id = src_id.split("::", 1)[1]
            c["source_ref"] = f"{src_id}#{idx}"

    print(f"  完成: {len(citations)} 条法条，质量分 {quality:.0f}，耗时 {elapsed:.1f}s")
    return {
        "law_citations": citations,
        "law_quality_score": quality,
        "law_research_context": research_context,
    }


if __name__ == "__main__":
    s = AgentState(
        law_retrieval_query="房屋租赁合同解除条件 违约金上限 法律规定",
        input="我要写一份起诉状，告李四拖欠房租还不给违约金",
    )
    out = doc_law_retrieve_node(s)
    print(f"law_citations={len(out.get('law_citations', []))}")
