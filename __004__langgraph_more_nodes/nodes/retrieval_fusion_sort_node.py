"""检索子节点4: 融合排序 - RRF融合+去重+排序+生成上下文+质量分"""
from __004__langgraph_more_nodes.agent_state import AgentState

def retrieval_fusion_sort_node(state: AgentState):
    """融合排序：合并基础层与增强层结果，去重，拼装上下文，计算质量分"""
    print("检索 [4/5] 融合排序")
    base_citations = state.get("base_citations", []) or []
    enhance_citations = state.get("enhance_citations", []) or []

    # 合并并去重（按 title+article_no 作为去重key）
    seen = set()
    merged = []
    for c in base_citations + enhance_citations:
        key = f"{c.get('title', '')}|{c.get('article_no', '')}|{c.get('content', '')[:40]}"
        if key in seen:
            continue
        seen.add(key)
        merged.append(c)

    # 简单按 score 排序(有score的在前)，再保持原顺序
    try:
        merged.sort(key=lambda x: -x.get("score", 0) if isinstance(x.get("score", 0), (int, float)) else 0)
    except Exception:
        pass

    # 拼装 research_context（前8条）
    research_context = ""
    if merged:
        research_context = "\n\n".join([
            f"【{c.get('title', '')}】{c.get('article_no', '')}\n{c.get('content', '')}"
            for c in merged[:8]
        ])

    # 质量评分：每条20分，上限100
    quality_score = min(100, len(merged) * 20)

    print(f"  合并后 {len(merged)} 条引用，质量分 {quality_score}")

    return {
        "citations": merged,
        "research_context": research_context,
        "quality_score": quality_score,
    }
