from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

builder = StateGraph(LegalResearchState)

# 添加节点
builder.add_node("N1_parse_and_decompose", parse_and_decompose)
builder.add_node("N2_classifier", classifier)
builder.add_node("N3_dispatcher", dispatcher)
builder.add_node("L3_statute", statute_retriever)
builder.add_node("L4_case", case_retriever)
builder.add_node("L7_private_kb", private_kb_retriever)
builder.add_node("L8_credit", credit_retriever)
builder.add_node("L4_regulatory", regulatory_retriever)
builder.add_node("L5_industry_std", industry_std_retriever)
builder.add_node("L6_national_std", national_std_retriever)
builder.add_node("L9_market", market_retriever)
builder.add_node("L10_model", model_retriever)
builder.add_node("L10_health_compliance", health_compliance_retriever)
builder.add_node("N4_clause_enhancer", clause_enhancer)
builder.add_node("N5_fuser", multi_source_fuser)
builder.add_node("N6_conflict_resolver", conflict_resolver)
builder.add_node("N7_citation_formatter", citation_formatter)
builder.add_node("N8_quality_gate", quality_gate)
builder.add_node("N9_output", output_deliver)

# 边
builder.add_edge(START, "N1_parse_and_decompose")
builder.add_edge("N1_parse_and_decompose", "N2_classifier")
builder.add_edge("N2_classifier", "N3_dispatcher")

# 并行调度：N3_dispatcher 通过 Send 动态分发
def dispatch(state):
    sends = []
    # 基础层
    sends.append(Send("L3_statute", state))
    sends.append(Send("L4_case", state))
    sends.append(Send("L7_private_kb", state))
    sends.append(Send("L8_credit", state))
    # 行业增强层
    extra = INDUSTRY_MOUNT_TABLE.get(state["confirmed_contract_type"], [])
    for node_name in extra:
        sends.append(Send(node_name, state))
    return sends

builder.add_conditional_edges("N3_dispatcher", dispatch, {
    node: node for node in ALL_RETRIEVAL_NODES
})

# 所有检索节点汇聚到条款增强层
for node in ALL_RETRIEVAL_NODES:
    builder.add_edge(node, "N4_clause_enhancer")

builder.add_edge("N4_clause_enhancer", "N5_fuser")
builder.add_edge("N5_fuser", "N6_conflict_resolver")
builder.add_edge("N6_conflict_resolver", "N7_citation_formatter")
builder.add_edge("N7_citation_formatter", "N8_quality_gate")

# 质量门禁条件路由
def quality_route(state):
    if state["needs_human"]:
        return "human_interrupt"
    elif state["quality_score"] >= 0.85:
        return "output"
    elif state["retry_count"] < 3:
        return "retry"
    else:
        return "human_interrupt"

builder.add_conditional_edges(
    "N8_quality_gate",
    quality_route,
    {
        "output": "N9_output",
        "retry": "N3_dispatcher",   # 重试（可调整参数）
        "human_interrupt": "human_interrupt_node"
    }
)

builder.add_edge("N9_output", END)

# 编译
graph = builder.compile()