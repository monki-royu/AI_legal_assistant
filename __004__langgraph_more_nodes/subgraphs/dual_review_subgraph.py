"""双审子图 (Dual Review Subgraph) —V5.1 并行架构

【架构定位】
    本子图是合同/合规链路的【风险审查核心】, 独立编译后被合同/合规主图节点 add_node 复用.

    V5.1架构: 并行双审 + 条件路由 (优化自 V5.0串行架构).

      - contract_review (合同审核双审):
          parallel_dual_review (并发执行 compliance + contract_ai)
          → conflict_resolution → numeric_validate
          → risk_aggregate → final_delivery
      - compliance_review (合规审查单审):
          parallel_dual_review (内部自动识别单审模式, 只执行 compliance)
          → numeric_validate → risk_aggregate → final_delivery

    【并行优化原理】
    原来串行: compliance_review(~8s) → contract_ai_review(~12s) = ~20s
    现在并发:  max(compliance, contract_ai) = ~12s (取较慢者)
    节省约 40% 双审耗时。

    为什么并发安全:
    1. contract_ai_review 对 compliance_risk_items 的依赖是「可选上下文」
       (state.get("compliance_risk_items", []) or []), 空列表不会导致功能异常
    2. 两个节点写入不同的 state 字段 (compliance_risk_items vs contract_risk_items),
       不存在竞态冲突
    3. 后续 conflict_resolution 节点统一做双路结果的冲突消解, 不依赖串行顺序

【节点组成】(优化后 5 节点, 合并了原 compliance + contract_ai)
    parallel_dual_review → [条件分支]
        ├─ (双审) conflict_resolution → numeric_validate
        └─ (合规单路) numeric_validate
    numeric_validate → risk_aggregate → final_delivery → END

    注: parallel_dual_review 节点内部用 ThreadPoolExecutor 并发执行两个 LLM 调用,
    合并且返回两个节点的增量结果。
    注: credit_check (企查查相对方资信) 不在本子图, 而在 retrieval_subgraph
    的收尾环节运行, 其产出的 credit_risk_items 经共享 state 流入本子图的
    risk_aggregate_node 参与三路聚合与对立方加权.
    注: context_pack (审查上下文打包) 已于 2026-08-23 上移为 retrieval_subgraph
    的出口节点, 本子图不再持有该节点, 直接进入审查; review_context_bundle
    由检索子图出口写入, 下游审查节点统一消费.
"""
from langgraph.graph import StateGraph, END

from __004__langgraph_more_nodes.agent_state import AgentState
from __004__langgraph_more_nodes.nodes.contract_compliance_nodes.parallel_dual_review_node import (
    parallel_dual_review_node,
)
from __004__langgraph_more_nodes.nodes.contract_compliance_nodes.conflict_resolution_node import conflict_resolution_node
from __004__langgraph_more_nodes.nodes.contract_compliance_nodes.risk_aggregate_node import risk_aggregate_node
from __004__langgraph_more_nodes.nodes.contract_compliance_nodes.numeric_validate_node import numeric_validate_node
from __004__langgraph_more_nodes.nodes.contract_compliance_nodes.final_delivery_node import final_delivery_node
from common.ouput_graph_utils import output_pic_graph
from common.path_utils import get_file_path


def _after_parallel_review(state: AgentState) -> str:
    """并行双审后条件路由: 按 task_type 分支

    - contract_review → conflict_resolution (双审结果需要冲突消解)
    - compliance_review → numeric_validate (合规单审不需要冲突消解)
    """
    task_type = state.get("task_type", "")
    if task_type == "contract_review":
        print("  [路由] 合同双审 → 进入 conflict_resolution 冲突消解")
        return "conflict_resolution"
    else:
        print("  [路由] 合规单审 → 跳过冲突消解, 直接进数值校验")
        return "numeric_validate"


def build_dual_review_subgraph():
    """构建并编译双审子图 (V4 并行架构)

    架构: parallel_dual_review → [条件路由] → ... → final_delivery
    - contract_review 任务: 走完整双审链 (含冲突消解)
    - compliance_review 任务: 跳过冲突消解, 直接进数值校验

    返回:
        CompiledStateGraph
    """
    builder = StateGraph(AgentState)

    # 5 节点注册 (并行双审节点合并了原来的 compliance_review + contract_ai_review)
    builder.add_node("parallel_dual_review", parallel_dual_review_node)
    builder.add_node("conflict_resolution", conflict_resolution_node)
    builder.add_node("numeric_validate", numeric_validate_node)
    builder.add_node("risk_aggregate", risk_aggregate_node)
    builder.add_node("final_delivery", final_delivery_node)

    # 入口: 并行双审 (自动识别单审/双审模式)
    builder.set_entry_point("parallel_dual_review")

    # 条件分支: 并行双审后按 task_type 分流
    # - contract_review → conflict_resolution (双审结果需冲突消解)
    # - compliance_review → numeric_validate (合规单审跳过冲突消解)
    builder.add_conditional_edges(
        "parallel_dual_review",
        _after_parallel_review,
        {
            "conflict_resolution": "conflict_resolution",
            "numeric_validate": "numeric_validate",
        },
    )

    # 合同审核双审链: conflict_resolution → numeric_validate
    builder.add_edge("conflict_resolution", "numeric_validate")

    # 公共主链: numeric_validate → risk_aggregate → final_delivery → END
    # (两种模式都经过 risk_aggregate, 它内部自动区分:
    #  - contract_review: 消费 conflict_resolution 合并后的 post_conflict_risk_items
    #  - compliance_review: fallback 直接消费 compliance_risk_items)
    builder.add_edge("numeric_validate", "risk_aggregate")
    builder.add_edge("risk_aggregate", "final_delivery")
    builder.add_edge("final_delivery", END)

    return builder.compile()


# 默认实例
dual_review_subgraph = build_dual_review_subgraph()
output_pic_graph(dual_review_subgraph, get_file_path("__004__langgraph_more_nodes/dual_review_subgraph.png"))
