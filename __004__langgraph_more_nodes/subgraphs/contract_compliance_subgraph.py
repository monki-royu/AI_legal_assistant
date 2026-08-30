# -*- coding: utf-8 -*-
"""合同合规智能体子图 (Contract Compliance Subgraph) —— 任务外壳 / 编排单元

【架构定位】
    本子图是 contract_review / compliance_review 任务的【自包含编排外壳】,
    把"输入归一化管道 + 共享预处理 + 检索复用 + 双审内核"整条链路封装进
    一个独立编译的子图, 主图只通过 add_node("contract_compliance", ...) 调度,
    从而把主图层从 13 节点精简到 7 节点。

    内部层级 (subgraph composition 多层复用):
        contract_compliance (本文件, 任务外壳)
          ├ input_source_router → doc_extract
          ├ doc_empty_guard → text_recognize       (输入归一化管道)
          ├ preprocess 子图                        (5 节点结构化预处理)
          ├ cc_retrieval 子图                      (复用 retrieval 检索底座)
          └ dual_review 子图                       (内层"风险审查核心", 保持不变)

    注: 入口分流(input_source_router)、空/损坏守卫(doc_empty_guard)、
    文本识别(text_recognize) 原在主图层, 现上移到本子图入口编排 —— 它们本就是
    完成合同/合规任务所必须的输入处理步骤, 归属任务外壳更合理。

【节点组成】
    输入归一化管道 (4 节点):
        input_source_router → doc_extract → doc_empty_guard → text_recognize
    下游子图 (3 个, 由本子图 add_node 嵌套):
        preprocess / cc_retrieval / dual_review
"""
from langgraph.graph import StateGraph, END

from __004__langgraph_more_nodes.agent_state import AgentState

# ── 输入归一化管道节点 (合同/合规任务专属, 仅本子图使用) ──
from __004__langgraph_more_nodes.nodes.preprocess_nodes.input_source_router_node import (
    input_source_router,
)
from __004__langgraph_more_nodes.nodes.preprocess_nodes.doc_extract_node import (
    doc_extract_node,
)
from __004__langgraph_more_nodes.nodes.preprocess_nodes.doc_empty_guard_node import (
    doc_empty_guard_node,
)
from __004__langgraph_more_nodes.nodes.preprocess_nodes.text_recognize_node import (
    text_recognize_node,
)

# ── 下游嵌套子图 (subgraph composition) ──
from __004__langgraph_more_nodes.subgraphs.preprocess_subgraph import (
    build_preprocess_subgraph,
)
from __004__langgraph_more_nodes.subgraphs.retrieval_subgraph import (
    build_retrieval_subgraph,
)
from __004__langgraph_more_nodes.subgraphs.dual_review_subgraph import (
    build_dual_review_subgraph,
)
from common.ouput_graph_utils import output_pic_graph
from common.path_utils import get_file_path


def after_doc_empty_guard(state: AgentState) -> str:
    """【文档路径守卫出口路由】doc_empty_guard 判定空/损坏文档 → 直接 END

    读取:
        - doc_empty_flag (str): doc_empty_guard_node 写入 "pass"/"block"

    返回:
        "preprocess": 文档非空, 进预处理子图 → cc_retrieval → dual_review
        "end":        文档为空/损坏/解析失败, 直接结束(提示用户重传)
    """
    if state.get("doc_empty_flag") == "block":
        print("⛔ [合同合规子图] 文档为空/损坏 → 跳过预处理/检索/双审, 直接结束")
        return "end"
    return "preprocess"


def after_text_recognize(state: AgentState) -> str:
    """【文本路径识别出口路由】text_recognize 判定是否合同相关 → 分流

    读取:
        - text_recognize_flag (str): text_recognize_node 写入 "pass"/"block"

    返回:
        "preprocess": 文本已归一化为 doc_text, 进预处理子图 → cc_retrieval → dual_review
        "end":        非合同 + 合同审核, 直接结束(提示用户粘贴/上传合同)
    """
    if state.get("text_recognize_flag") == "block":
        print("⛔ [合同合规子图] 文本非合同 + 合同审核 → 跳过预处理/检索/双审, 直接结束")
        return "end"
    return "preprocess"


def build_contract_compliance_subgraph(checkpointer=None):
    """构建并编译合同合规编排子图 (任务外壳)

    把"输入归一化管道 + preprocess + cc_retrieval + dual_review"封装为一个
    独立编译的子图; 检索子图必须传递 checkpointer 以支持北大法宝/企查查 interrupt。

    参数:
        checkpointer: LangGraph checkpointer 实例(SqliteSaver/MemorySaver)或 None

    返回:
        CompiledStateGraph
    """
    builder = StateGraph(AgentState)

    # ── 输入归一化管道 (4 节点, 合同/合规任务专属) ──
    builder.add_node("input_source_router", input_source_router)
    builder.add_node("doc_extract", doc_extract_node)
    builder.add_node("doc_empty_guard", doc_empty_guard_node)
    builder.add_node("text_recognize", text_recognize_node)

    # ── 下游嵌套子图 (subgraph composition) ──
    builder.add_node("preprocess", build_preprocess_subgraph())
    # 检索子图内部有北大法宝/企查查 interrupt, 必须传递 checkpointer
    builder.add_node(
        "cc_retrieval",
        build_retrieval_subgraph(checkpointer) if checkpointer is not None
        else build_retrieval_subgraph(),
    )
    builder.add_node("dual_review", build_dual_review_subgraph())

    # 入口: 输入分流 (文档/文本)
    builder.set_entry_point("input_source_router")

    # 入口分流: 有上传文档 → doc_extract; 纯文本 → text_recognize
    builder.add_conditional_edges(
        "input_source_router",
        lambda s: "doc" if (s.get("uploaded_doc_path") and str(s.get("uploaded_doc_path")).strip()) else "text",
        {
            "doc": "doc_extract",
            "text": "text_recognize",
        },
    )

    # 文档路径: doc_extract → 空/损坏守卫 → (pass→预处理 | block→END)
    builder.add_edge("doc_extract", "doc_empty_guard")
    builder.add_conditional_edges(
        "doc_empty_guard",
        after_doc_empty_guard,
        {
            "preprocess": "preprocess",   # 文档非空: 进预处理子图
            "end": END,                   # 空/损坏: 直接返回提示文案
        },
    )

    # 文本路径: text_recognize → (pass→预处理 | block→END)
    builder.add_conditional_edges(
        "text_recognize",
        after_text_recognize,
        {
            "preprocess": "preprocess",   # 文本已归一化为 doc_text: 进预处理子图
            "end": END,                   # 非合同+合同审核: 直接返回提示文案
        },
    )

    # 预处理子图 → 检索复用 → 双审 (固定边: 守卫已在子图入口前置拦截)
    builder.add_edge("preprocess", "cc_retrieval")
    builder.add_edge("cc_retrieval", "dual_review")
    builder.add_edge("dual_review", END)

    if checkpointer is not None:
        return builder.compile(checkpointer=checkpointer)
    return builder.compile()


contract_compliance_subgraph = build_contract_compliance_subgraph()
output_pic_graph(contract_compliance_subgraph, get_file_path("__004__langgraph_more_nodes/contract_compliance_subgraph.png"))
print("[docgen_subgraph V6] 图文件已输出: docgen_subgraph.png")
