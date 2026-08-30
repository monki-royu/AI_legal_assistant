"""合同/合规预处理子图 (Preprocess Subgraph) ── 纯 5 节点

【架构定位】
    本子图是合同/合规链路 (contract_compliance) 的【文档预处理单元】。
    入口分流 (input_source_router) 与空/损坏守卫 (doc_empty_guard) /
    文本识别 (text_recognize) 已由 contract_compliance 子图在入口编排,
    本子图只负责"拿到归一化后的 doc_text 后, 做合同结构化预处理",
    供后续 retrieval + dual_review 使用。

    主图层两条路径在此汇合:
        文档路径: doc_extract → doc_empty_guard(pass) → preprocess_subgraph
        文本路径: text_recognize(pass, 已把 input 归一化为 doc_text) → preprocess_subgraph

【节点组成】(5 节点 · 完整链)
    party_identify → contract_classify
    → full_text_segment → numeric_extract → llm_query_extract

    ① party_identify      (N1): 甲乙方识别, 输出 party_a/party_b + user_side
                                 (无明确主体时输出空字符串, 不编造)
    ② contract_classify   (N2): 合同分类, LLM 判断合同类型 → contract_type
                                 (非合同文本时写 "", 不编造; 真实异常合同仍可为"其他")
    ③ full_text_segment   (N3): 全文本切分, doc_text → doc_segments
    ④ numeric_extract     (N4): 数值抽取, 单价/数量/违约金比例等 → extracted_numerics
    ⑤ llm_query_extract   (N5): 查询文本抽取, 按 task_type 构造检索查询
                                 → retrieval_query / retrieval_keywords
                                 末节点, 输出传给 retrieval 子图

【被主图复用】
    contract_compliance 路径: preprocess → retrieval → dual_review
"""

from langgraph.graph import StateGraph, END

from __004__langgraph_more_nodes.agent_state import AgentState
from __004__langgraph_more_nodes.nodes.preprocess_nodes.party_identify_node import party_identify_node
from __004__langgraph_more_nodes.nodes.preprocess_nodes.contract_classify_node import contract_classify_node
from __004__langgraph_more_nodes.nodes.preprocess_nodes.full_text_segment_node import full_text_segment_node
from __004__langgraph_more_nodes.nodes.preprocess_nodes.numeric_extract_node import numeric_extract_node
from __004__langgraph_more_nodes.nodes.preprocess_nodes.llm_query_extract_node import llm_query_extract_node
from common.ouput_graph_utils import output_pic_graph
from common.path_utils import get_file_path


def build_preprocess_subgraph():
    """构建并编译文档预处理子图 (纯 5 节点)

    内部链:
        party_identify → contract_classify
        → full_text_segment → numeric_extract → llm_query_extract → END

    写入字段:
        - party_a/party_b:     甲乙方中性身份 (允许为空, 不编造)
        - user_side:           用户立场 (A/B/Unknown)
        - contract_type:       合同类型 (LLM 分类; 非合同文本为 "")
        - doc_segments:        全文本切分单元 (preamble/clause/paragraph)
        - extracted_numerics:  数值抽取结果
        - retrieval_query / retrieval_keywords: 检索查询 (末节点产出)

    返回:
        CompiledStateGraph
    """
    builder = StateGraph(AgentState)

    # 5 节点注册
    builder.add_node("party_identify", party_identify_node)
    builder.add_node("contract_classify", contract_classify_node)
    builder.add_node("full_text_segment", full_text_segment_node)
    builder.add_node("numeric_extract", numeric_extract_node)
    builder.add_node("llm_query_extract", llm_query_extract_node)

    # 入口
    builder.set_entry_point("party_identify")

    # 主链 (5 节点串联)
    builder.add_edge("party_identify", "contract_classify")
    builder.add_edge("contract_classify", "full_text_segment")
    builder.add_edge("full_text_segment", "numeric_extract")
    builder.add_edge("numeric_extract", "llm_query_extract")
    builder.add_edge("llm_query_extract", END)

    return builder.compile()


# 默认实例
preprocess_subgraph = build_preprocess_subgraph()
output_pic_graph(preprocess_subgraph, get_file_path("__004__langgraph_more_nodes/preprocess_subgraph.png"))
