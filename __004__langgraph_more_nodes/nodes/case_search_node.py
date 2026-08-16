# 📜 ============================================================
# 文件名称: nodes/case_search_node.py
# 文件作用: 案例检索节点
# ============================================================
#
# 【这个文件是干什么的？】
# 独立的案例检索入口，仅查案例（mounted_sources=["cases"]），跳过 RRF 融合。
#
# 【代码逻辑主线】
# Neo4j Case 匹配 → LLM 类案总结 → 写入 case_search_results
#
# 【谁在调用它？】
# langgraph_main.py 中的 build_graph() 通过 add_node 注册本节点，
# 并通过 add_edge / add_conditional_edges 定义其前后依赖。


# -*- coding: utf-8 -*-
from __004__langgraph_more_nodes.agent_state import AgentState
from common.retrieval_engine import engine as retrieval_engine
from common.finetune_utils import collect_ft_sample


def case_search_node(state: AgentState):
    """
    case_search_node 函数: 实现节点具体逻辑。
    """
    print("案例检索节点")
    query = state.get("retrieval_query", "") or state.get("input", "") or ""
    case_type = state.get("case_type_filter", "")
    court_level = state.get("court_level_filter", "")
    page = max(1, int(state.get("search_page", 1)))
    page_size = max(1, min(50, int(state.get("search_page_size", 10))))

    if not query:
        print("  检索关键词为空, 返回空结果")
        return {"case_search_results": [], "case_search_total": 0}

    # 调用检索引擎的案例搜索
    results = retrieval_engine.search_cases(
        query=query, top_k=page * page_size,
        case_type=case_type,
    )

    # 法院级别过滤(在结果中二次过滤, 因为 BM25 本身不支持跨字段过滤)
    if court_level:
        filtered = []
        for r in results:
            court_name = r.get("court_name", "") or ""
            if court_level in court_name:
                filtered.append(r)
        results = filtered

    # 统计总数
    total = len(results)

    # 分页
    start = (page - 1) * page_size
    paged = results[start:start + page_size]

    # 格式化输出
    items = []
    for r in paged:
        items.append({
            "id": r.get("doc_id", ""),
            "title": r.get("case_title", ""),
            "caseNo": r.get("case_no", ""),
            "court": r.get("court_name", ""),
            "caseType": r.get("case_type", ""),
            "date": r.get("judge_date", ""),
            "summary": (r.get("case_summary", "") or "")[:200],
            "judgment": (r.get("judgment", "") or "")[:200],
            "tags": r.get("cited_laws", []),
            "source": r.get("source", ""),
        })

    print(f"  检索到 {total} 条案例, 返回 {len(items)} 条")
    # ==== 微调数据收集 ====
    try:
        _ft_input = str(state.get("input", "") or "")[:2000]
        {"case_search_results": state.get("case_search_results", [])
}
        collect_ft_sample("case_search", _ft_input, _ft_output,
                          task_type=state.get("task_type", ""))
    except Exception:
        pass
    return {"case_search_results": items, "case_search_total": total}