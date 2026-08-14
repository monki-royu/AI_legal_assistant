"""检索子节点5: 结果输出 - 写入标准字段并打印完成日志"""
from __004__langgraph_more_nodes.agent_state import AgentState

def retrieval_output_node(state: AgentState):
    """结果输出：写入 citations/research_context/quality_score（兼容下游接口）"""
    citations = state.get("citations", []) or []
    research_context = state.get("research_context", "")
    quality_score = state.get("quality_score", 0)

    # 保证输出字段完整（下游 risk_aggregate_node 读取这些字段）
    citations_out = citations if isinstance(citations, list) else []
    ctx_out = research_context if isinstance(research_context, str) else ""
    qs_out = quality_score if isinstance(quality_score, (int, float)) else 0

    print(f"检索 [5/5] 结果输出: {len(citations_out)} 条引用, 质量分{qs_out}")

    return {
        "citations": citations_out,
        "research_context": ctx_out,
        "quality_score": qs_out,
    }
