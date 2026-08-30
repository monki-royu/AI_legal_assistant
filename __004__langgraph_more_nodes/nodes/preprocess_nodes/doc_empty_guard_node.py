# -*- coding: utf-8 -*-
"""
文档空/损坏守卫节点 (Doc Empty Guard)
====================================

【架构定位】
    位于主图层「文档路径」, 紧跟 doc_extract 之后:

        doc_extract → 【doc_empty_guard】 ─(pass)→ preprocess_subgraph
                                          └(block)→ END

    doc_extract 把上传文件解析为 doc_text; 本节点检查 doc_text 是否非空。
    文档路径信任用户上传的动作, 因此【只做空/损坏检查, 不做"是不是合同"的
    语义质疑】 —— 避免对合法上传文档做 false positive 误拦。

【拦截场景】
    - 用户没上传文件(理论上不会到这, 因为 input_source_router 已分流到文本路径);
    - 上传的文件为空 / 损坏 / 解析失败(doc_extract 未产出非空 doc_text)。

【与 text_recognize 的关系】
    两者分属文档/文本两条路径, 但职责同构: 都是"在最早可靠位置拦截空输入",
    省掉后续 preprocess(5 节点含 LLM) + retrieval(10 节点) + dual_review(2 次 LLM)。
"""

from __004__langgraph_more_nodes.agent_state import AgentState


# 文档为空/损坏时的提示文案 (不调 LLM, 纯静态)
_EMPTY_DOC_MESSAGE = (
    "⚠️ 未从您上传的文档中解析出有效内容。\n\n"
    "可能原因：\n"
    "1. 文件为空或已损坏；\n"
    "2. 文件格式暂不支持解析（支持 PDF / DOCX / TXT 等）。\n\n"
    "请确认文件完整后重新上传，或将合同全文直接粘贴到输入框提交。"
)


def doc_empty_guard_node(state: AgentState):
    """文档空/损坏守卫: 检查 doc_extract 解析出的 doc_text 是否非空。

    读取字段:
        - doc_text (str): doc_extract 解析上传文件后的纯文本

    写入字段:
        - doc_empty_flag ("pass" / "block"): 供 contract_compliance 子图 after_doc_empty_guard 分流
        - block 时: output / final_report_markdown / need_user_confirm
    """
    print("--- 文档空/损坏守卫: 检查 doc_extract 解析结果是否为空 ---")

    doc_text = str(state.get("doc_text") or "").strip()
    if not doc_text:
        print("  [文档守卫] doc_text 为空(文件空/损坏/解析失败) → 拦截, 不调用任何 LLM")
        return {
            "doc_empty_flag": "block",
            "need_user_confirm": True,
            "output": _EMPTY_DOC_MESSAGE,
            "final_report_markdown": _EMPTY_DOC_MESSAGE,
            "review_empty_input": True,
        }

    print(f"  [文档守卫] doc_text 非空({len(doc_text)}字) → 放行进预处理")
    return {"doc_empty_flag": "pass"}
