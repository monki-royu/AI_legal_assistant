"""检索子图 — 三阶段检索架构 (实体召回→精准过滤→融合排序) + 质量门控 + 收尾链

节点 (11): intent_decompose → credit_precheck → entity_recall → precision_filter
            → fusion_ranking → quality_gate → beida_fabao → credit_check → context_pack → END
"""

from langgraph.graph import StateGraph, END
from __004__langgraph_more_nodes.agent_state import AgentState

# 节点函数导入
from __004__langgraph_more_nodes.nodes.retrieval_nodes.retrieval_intent_decompose_node import retrieval_intent_decompose_node
from __004__langgraph_more_nodes.nodes.retrieval_nodes.credit_precheck_node import credit_precheck_node
from __004__langgraph_more_nodes.nodes.retrieval_nodes.retrieval_entity_recall_node import retrieval_entity_recall_node
from __004__langgraph_more_nodes.nodes.retrieval_nodes.retrieval_precision_filter_node import retrieval_precision_filter_node
from __004__langgraph_more_nodes.nodes.retrieval_nodes.retrieval_fusion_ranking_node import retrieval_fusion_ranking_node
from __004__langgraph_more_nodes.nodes.retrieval_nodes.quality_gate_retry_node import quality_gate_retry_node
from __004__langgraph_more_nodes.nodes.retrieval_nodes.beida_fabao_gate_node import beida_fabao_gate_node
from __004__langgraph_more_nodes.nodes.retrieval_nodes.credit_check_node import credit_check_node
from __004__langgraph_more_nodes.nodes.retrieval_nodes.retrieval_output_pack_node import retrieval_output_pack_node
from common.ouput_graph_utils import output_pic_graph
from common.path_utils import get_file_path


def _quality_gate_router(state: AgentState) -> str:
    """质量门条件路由: passed→'pass'→beida_fabao, 否则→'retry'→intent_decompose"""
    if state.get("quality_gate_passed", True):
        return "pass"
    return "retry"


def build_retrieval_subgraph(checkpointer=None):
    """构建检索子图: 主链(6节点串行) + 质量门条件路由(2分支) + 收尾链(3节点)"""
    builder = StateGraph(AgentState)

    # 注册节点
    builder.add_node("retrieval_intent_decompose", retrieval_intent_decompose_node)
    builder.add_node("credit_precheck", credit_precheck_node)
    builder.add_node("retrieval_entity_recall", retrieval_entity_recall_node)
    builder.add_node("retrieval_precision_filter", retrieval_precision_filter_node)
    builder.add_node("retrieval_fusion_ranking", retrieval_fusion_ranking_node)
    builder.add_node("quality_gate_retry", quality_gate_retry_node)
    builder.add_node("beida_fabao_gate", beida_fabao_gate_node)
    builder.add_node("credit_check", credit_check_node)
    builder.add_node("context_pack", retrieval_output_pack_node)

    # 入口
    builder.set_entry_point("retrieval_intent_decompose")

    # 主链: intent → credit_precheck → entity_recall → precision_filter → fusion_ranking → quality_gate
    builder.add_edge("retrieval_intent_decompose", "credit_precheck")
    builder.add_edge("credit_precheck", "retrieval_entity_recall")
    builder.add_edge("retrieval_entity_recall", "retrieval_precision_filter")
    builder.add_edge("retrieval_precision_filter", "retrieval_fusion_ranking")
    builder.add_edge("retrieval_fusion_ranking", "quality_gate_retry")

    # 质量门条件路由: pass→beida_fabao, retry→intent_decompose (回退重试)
    builder.add_conditional_edges(
        "quality_gate_retry",
        _quality_gate_router,
        {
            "retry": "retrieval_intent_decompose",
            "pass": "beida_fabao_gate",
        },
    )

    # 收尾链: beida_fabao → credit_check → context_pack → END
    builder.add_edge("beida_fabao_gate", "credit_check")
    builder.add_conditional_edges(
        "credit_check",
        lambda state: "pack",
        {"pack": "context_pack"},
    )
    builder.add_edge("context_pack", END)

    return builder.compile(checkpointer=checkpointer)


retrieval_subgraph = build_retrieval_subgraph()
output_pic_graph(retrieval_subgraph, get_file_path("__004__langgraph_more_nodes/retrieval_subgraph.png"))