# 📜 ============================================================
# 文件名称: nodes/law_query_node.py
# 文件作用: 法规查询节点
# ============================================================
#
# 【这个文件是干什么的？】
# 独立的法规查询入口，仅查法规（mounted_sources=["laws"]），跳过 RRF 融合。
#
# 【代码逻辑主线】
# Neo4j 匹配 → FAISS 检索 → 本地关键词 → 写入 law_query_results
#
# 【谁在调用它？】
# langgraph_main.py 中的 build_graph() 通过 add_node 注册本节点，
# 并通过 add_edge / add_conditional_edges 定义其前后依赖。


# -*- coding: utf-8 -*-
from __004__langgraph_more_nodes.agent_state import AgentState
from common.retrieval_engine import engine as retrieval_engine
from common.finetune_utils import collect_ft_sample


def law_query_node(state: AgentState):
    """
    law_query_node 函数: 实现节点具体逻辑。
    """
    print("法规查询节点")
    query = state.get("retrieval_query", "") or state.get("input", "") or ""
    law_name = state.get("law_name_filter", "")
    page = max(1, int(state.get("search_page", 1)))
    page_size = max(1, min(100, int(state.get("search_page_size", 20))))

    if not query:
        print("  关键词为空, 返回空结果")
        return {"law_query_results": [], "law_query_total": 0}

    # 调用检索引擎的法规搜索
    results = retrieval_engine.search_laws(
        query=query, top_k=page * page_size,
    )

    # 法律名称过滤
    if law_name:
        filtered = [r for r in results if law_name in (r.get("law_name", "") or "")]
        results = filtered

    total = len(results)
    start = (page - 1) * page_size
    paged = results[start:start + page_size]

    items = []
    for r in paged:
        items.append({
            "id": r.get("doc_id", ""),
            "lawName": r.get("law_name", ""),
            "articleNo": r.get("article_no", ""),
            "chapter": r.get("chapter", ""),
            "content": r.get("content", ""),
            "effectiveDate": r.get("effective_date", ""),
            "status": r.get("status", "现行有效"),
            "source": r.get("source", ""),
        })

    print(f"  检索到 {total} 条法条, 返回 {len(items)} 条")
    # ==== 微调数据收集 ====
    try:
        _ft_input = str(state.get("input", "") or "")[:2000]
        {"law_query_results": state.get("law_query_results", [])
}
        collect_ft_sample("law_query", _ft_input, _ft_output,
                          task_type=state.get("task_type", ""))
    except Exception:
        pass
    return {"law_query_results": items, "law_query_total": total}