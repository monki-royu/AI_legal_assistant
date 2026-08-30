"""法律问答子图 (QA Subgraph)

【架构定位】
    本子图是法律问答链路 (legal_qa) 的【独立处理单元】, 二级路由判定为
    legal_qa 后进入. 内部实现三级路由:
      qa_intent_classify → [法律相关] → (嵌套 retrieval_subgraph) → final_answer
                          [非法律]   → llm_direct_out

    关键: 子图内 add_node 嵌套另一个子图 (retrieval_subgraph), 体现
    LangGraph subgraph composition 的多层嵌套能力.

【节点组成】(4 节点 / 1 子图)
    qa_intent_classify  → [条件路由]
        ├─ is_legal_related=True  → retrieval (嵌套子图) → legal_qa_final_answer
        └─ is_legal_related=False → llm_direct_out

【复用价值】
    retrieval_subgraph 在此被嵌套调用, 与合同/合规/检索路径共用同一份
    检索能力, 避免重复实现.
"""
from langgraph.graph import StateGraph, END

from __004__langgraph_more_nodes.agent_state import AgentState
from __004__langgraph_more_nodes.nodes.legal_qa_nodes.legal_qa_intent_node import qa_intent_classify
from __004__langgraph_more_nodes.nodes.legal_qa_nodes.legal_qa_final_answer_node import legal_qa_final_answer_node
from __004__langgraph_more_nodes.nodes.legal_qa_nodes.llm_direct_out_node import llm_direct_out_node

from __004__langgraph_more_nodes.subgraphs.retrieval_subgraph import build_retrieval_subgraph
from common.ouput_graph_utils import output_pic_graph
from common.path_utils import get_file_path


def _qa_intent_router(state: AgentState) -> str:
    """【QA 子图内部路由】判断法律相关性: 是 → 检索, 否 → LLM 直答

    读取:
        - is_legal_related (bool): qa_intent_classify 写入
    返回:
        "legal":     进入嵌套检索子图
        "non_legal": 进入 LLM 直接回答节点
    """
    if state.get("is_legal_related", False):
        return "legal"
    return "non_legal"


def build_qa_subgraph(checkpointer=None):
    """构建并编译 QA 子图

    Args:
        checkpointer: 可选的 MemorySaver 实例，传递给检索子图以支持 interrupt() 续跑

    内部节点:
        - qa_intent_classify: 三级分类 (法律相关/非法律相关)
        - retrieval:           嵌套检索子图 (复用)
        - legal_qa_final_answer: 法律分支最终答案
        - llm_direct_out:       非法律分支 LLM 直答

    返回:
        CompiledStateGraph
    """
    builder = StateGraph(AgentState)

    # 4 节点 / 1 子图注册
    builder.add_node("qa_intent_classify", qa_intent_classify)
    # 嵌套子图: 检索能力直接复用，传递 checkpointer 以支持 interrupt
    builder.add_node("qa_retrieval", build_retrieval_subgraph(checkpointer))
    builder.add_node("legal_qa_final_answer", legal_qa_final_answer_node)
    builder.add_node("llm_direct_out", llm_direct_out_node)

    # 入口
    builder.set_entry_point("qa_intent_classify")

    # 三级路由: 条件边分派
    builder.add_conditional_edges(
        "qa_intent_classify",
        _qa_intent_router,
        {
            "legal": "qa_retrieval",          # 走嵌套检索子图
            "non_legal": "llm_direct_out",    # 走 LLM 直答
        },
    )

    # 法律分支: 检索子图 → 最终答案生成
    builder.add_edge("qa_retrieval", "legal_qa_final_answer")
    builder.add_edge("legal_qa_final_answer", END)

    # 非法律分支: LLM 直答
    builder.add_edge("llm_direct_out", END)

    return builder.compile(checkpointer=checkpointer)


# 默认实例（向后兼容）
qa_subgraph = build_qa_subgraph()
output_pic_graph(qa_subgraph, get_file_path("__004__langgraph_more_nodes/qa_subgraph.png"))