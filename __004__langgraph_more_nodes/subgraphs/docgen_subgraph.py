# -*- coding: utf-8 -*-
"""文书生成子图 (Document Generation Subgraph) — V6 并发检索版
============================================================

【V6 架构（法条/类案检索封装为独立节点 + 单节点并发编排 + 澄清守卫）】

    case_analyze → [need_clarify?] ── 是 ──→ END (返回追问文案, 不生成残缺文书)
                        │ 否
                        ▼
                 template_match → query_plan
        → doc_parallel_retrieve (单节点内 ThreadPoolExecutor 并发跑法条+类案)
        → clause_fill → risk_analysis → final_delivery → END

【V6 vs V5 变化】
  - 删除 V5 的 2 个内联函数 (_law_retrieve_node / _case_retrieve_node)
  - 法条检索 / 类案检索分别封装为独立节点文件:
      nodes/docgen_nodes/doc_law_retrieve_node.py  (doc_law_retrieve_node)
      nodes/docgen_nodes/doc_case_retrieve_node.py  (doc_case_retrieve_node)
  - 新增并发编排节点 (类比 parallel_dual_review_node.py):
      nodes/docgen_nodes/doc_parallel_retrieve_node.py (parallel_docgen_retrieve_node)
      用 ThreadPoolExecutor 在单节点内并发跑上述两个检索节点,
      两者写入字段互不冲突, 合并后写回 state。
  - 图结构由 V5 的 "query_plan 真·fan-out 到两个节点再 fan-in" 收敛为
      "query_plan → doc_parallel_retrieve(单节点内部并发) → clause_fill",
      更稳健: 不依赖图运行时对 fan-out 的并行支持(compat 层按条件边首匹配, 串行)。
  - 【2026-08-29】补上澄清守卫: 新增 _clarify_router 条件边。
      need_clarify / clarify_question 两个字段此前一直被 doc_case_analyze 写入,
      但因本子图原本是 7 条 add_edge 的纯线性链、零条件边, 它们**零消费方** ——
      即便判定"信息不足"也会继续生成, 产出当事人写着"原告""被告"的残缺文书。
      现在信息不足时直接 END, 并把 clarify_question 写进 output 返回给前端。

【节点组成（7 节点，其中 2 个为并发子任务函数、仅编排节点入图）】
  doc_case_analyze → [澄清条件边] → doc_template_match → doc_query_plan
  → doc_parallel_retrieve  ← 内部并发 doc_law_retrieve + doc_case_retrieve
  → doc_clause_fill → doc_risk_analysis → doc_final_delivery → END
"""

import time

from langgraph.graph import StateGraph, END

from __004__langgraph_more_nodes.agent_state import AgentState
from __004__langgraph_more_nodes.nodes.docgen_nodes.doc_case_analyze_node import doc_case_analyze_node
from __004__langgraph_more_nodes.nodes.docgen_nodes.doc_template_match_node import doc_template_match_node
from __004__langgraph_more_nodes.nodes.docgen_nodes.doc_query_plan_node import doc_query_plan_node
from __004__langgraph_more_nodes.nodes.docgen_nodes.doc_parallel_retrieve_node import parallel_docgen_retrieve_node
from __004__langgraph_more_nodes.nodes.docgen_nodes.doc_clause_fill_node import doc_clause_fill_node
from __004__langgraph_more_nodes.nodes.docgen_nodes.doc_risk_analysis_node import doc_risk_analysis_node
from __004__langgraph_more_nodes.nodes.docgen_nodes.doc_final_delivery_node import doc_final_delivery_node
from common.ouput_graph_utils import output_pic_graph
from common.path_utils import get_file_path


def _clarify_router(state: AgentState) -> str:
    """【文书生成子图入口路由】案情信息不足 → 先追问用户, 不硬着头皮生成

    读取:
        - need_clarify (bool): doc_case_analyze_node 写入。
          LLM 判定缺少当事人/诉求等关键要素, 或兜底路径下原告/被告任一为空时置 True。

    返回:
        "clarify":  直接退出子图 (END)。此时 output 已被
                    doc_case_analyze_node 填成 clarify_question 追问文案。
        "continue": 信息充分, 继续 template_match → ... → final_delivery

    【为什么要这条边】
        原实现中 need_clarify / clarify_question 两个字段一直被写入却**零消费方**
        —— 子图是 7 条 add_edge 的纯线性链, 没有任何条件边, 所以即便判定"信息不足"
        也会继续往下生成, 最终产出一份当事人写着"原告""被告"的残缺文书。
        与其给用户一份残缺文书, 不如明确告诉他缺什么。
    """
    if state.get("need_clarify"):
        print("  [路由] 案情信息不足 → 终止文书生成, 返回追问文案")
        return "clarify"
    return "continue"


def build_docgen_subgraph():
    """构建并编译 V6 文书生成子图。

    节点连接:
        entry → case_analyze → [need_clarify?] → END (追问)
                             → [否则] template_match → query_plan
        query_plan → parallel_retrieve (内部并发法条+类案)
        parallel_retrieve → clause_fill → risk_analysis → final_delivery → END
    """
    builder = StateGraph(AgentState)

    # 7 节点注册（doc_law_retrieve / doc_case_retrieve 作为并发子任务,
    # 由 doc_parallel_retrieve 内部调用, 不单独注册入图）
    builder.add_node("doc_case_analyze", doc_case_analyze_node)
    builder.add_node("doc_template_match", doc_template_match_node)
    builder.add_node("doc_query_plan", doc_query_plan_node)
    builder.add_node("doc_parallel_retrieve", parallel_docgen_retrieve_node)
    builder.add_node("doc_clause_fill", doc_clause_fill_node)
    builder.add_node("doc_risk_analysis", doc_risk_analysis_node)
    builder.add_node("doc_final_delivery", doc_final_delivery_node)

    # 入口
    builder.set_entry_point("doc_case_analyze")

    # 澄清分支: 案情信息不足时直接 END, 不进入后续生成节点
    # (原为纯线性边 doc_case_analyze → doc_template_match, need_clarify 无处消费)
    builder.add_conditional_edges(
        "doc_case_analyze",
        _clarify_router,
        {
            "clarify": END,                     # 信息不足 → 结束, output 已是追问文案
            "continue": "doc_template_match",   # 信息充分 → 继续生成
        },
    )

    # 线性主链（前 3 节点）
    builder.add_edge("doc_template_match", "doc_query_plan")

    # 并发检索：query_plan 之后由单节点编排并发跑法条+类案检索(ThreadPoolExecutor)
    builder.add_edge("doc_query_plan", "doc_parallel_retrieve")

    # 两个检索子图都完成后，进入 clause_fill
    builder.add_edge("doc_parallel_retrieve", "doc_clause_fill")

    # 线性后链
    builder.add_edge("doc_clause_fill", "doc_risk_analysis")
    builder.add_edge("doc_risk_analysis", "doc_final_delivery")
    builder.add_edge("doc_final_delivery", END)

    return builder.compile()


# 默认实例 + 出图
docgen_subgraph = build_docgen_subgraph()
output_pic_graph(docgen_subgraph, get_file_path("__004__langgraph_more_nodes/docgen_subgraph.png"))
print("[docgen_subgraph V6] 图文件已输出: docgen_subgraph.png")
