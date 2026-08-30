# -*- coding: utf-8 -*-
"""文书生成 - 类案检索节点 (case_search)
==========================================

【架构位置】
    位于 doc_query_plan 之后, 与 doc_law_retrieve 并发执行
    (由 doc_parallel_retrieve_node 通过 ThreadPoolExecutor 并发编排)。

【职责】
    调用 legal_response_sync(task_type="case_search"), 走检索子图 Path A。
    intent_decompose 自动挂载 1 源 (cases) + skip_fusion。
    返回 similar_cases (TopN) / case_quality_score。

【读取】
    - case_retrieval_query (str): doc_query_plan 输出, 面向"找相似判决"的案情匹配
    - input (str): 兜底

【写入】
    - similar_cases (List[Dict]): 相似案例 TopN(每条补 source_ref)
    - case_quality_score (float): 检索质量分
"""

import time

from __004__langgraph_more_nodes.agent_state import AgentState

TOP_CASES = 3


def doc_case_retrieve_node(state: AgentState) -> dict:
    """类案检索节点: 调 case_search 子图, 返回 similar_cases (Top3)。"""
    from __004__langgraph_more_nodes.langgraph_main import legal_response_sync

    print(f"文书生成 [类案检索] case_search 子图（1源, skip_fusion, Top{TOP_CASES}）")
    query = state.get("case_retrieval_query", "") or state.get("input", "")
    print(f"  查询: {str(query)[:80]}…")

    t0 = time.time()
    try:
        result = legal_response_sync(query, task_type="case_search",
                                     retrieval_query=query)
    except Exception as e:
        print(f"  ⚠️ 类案检索子图失败: {e}，空结果兜底")
        result = {"citations": [], "quality_score": 0.0, "research_context": ""}

    elapsed = time.time() - t0
    citations = result.get("citations", []) or []
    quality = float(result.get("quality_score", 0) or 0)

    for idx, c in enumerate(citations):
        if isinstance(c, dict) and "source_ref" not in c:
            c["source_ref"] = f"cases#{idx}"

    top_cases = citations[:TOP_CASES]
    print(f"  完成: 命中 {len(citations)} 条，取 Top{TOP_CASES}，质量分 {quality:.0f}，耗时 {elapsed:.1f}s")
    return {
        "similar_cases": top_cases,
        "case_quality_score": quality,
    }


if __name__ == "__main__":
    s = AgentState(
        case_retrieval_query="房屋租赁合同 承租人拖欠租金 出租人解除 违约金 判决",
        input="我要写一份起诉状，告李四拖欠房租还不给违约金",
    )
    out = doc_case_retrieve_node(s)
    print(f"similar_cases={len(out.get('similar_cases', []))}")
