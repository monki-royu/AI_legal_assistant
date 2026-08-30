# -*- coding: utf-8 -*-
"""
输入来源路由节点 (Input Source Router)
=====================================

【架构定位】
    位于主图层, 承接 intent_router 的 contract_compliance 分支
    (contract_review / compliance_review), 在「文档预处理」之前做第一道分流:

        intent_router ─(contract/review)→ 【input_source_router】
                                                  │
                          ┌───────────────────────┴───────────────────────┐
                          │ 有 uploaded_doc_path(文档优先)              无文件(纯文本)
                          ▼                                                ▼
                     doc_extract                                    text_recognize
                          │                                                │
                          ▼                                  ┌─────────────┴────────────┐
                     doc_empty_guard                   合同相关/合规         非合同+合同审核
                          │                            │  (pass)              (block→END)
                    ┌─────┴─────┐                       │
                   空          非空                     │
                    │           │                       │
                   END   preprocess_subgraph ◄──────────┴──(文本归一化为 doc_text)
                          → cc_retrieval → dual_review → END

【职责边界】
    本节点【只读】 uploaded_doc_path, 【不写】 task_type(那是 intent_router 的职责),
    也【不做】任何语义判断。它只回答一个问题: "用户这次提交的主体是上传的文档, 还是
    纯文本输入?" —— 据此把后续链路导向「文档路径」或「文本路径」。

    - 文档路径: 信任用户主动上传的动作, 只做空/损坏守卫(doc_empty_guard),
                不做"是不是合同"的语义质疑(避免 false positive 误拦合法文档);
    - 文本路径: 文本风险更高(可能是"帮我看看这个条款"这类非合同内容),
                text_recognize 才做"是否合同相关"的交叉校验。

【为什么文档优先】
    若 uploaded_doc_path 与 input 同时存在, 以文档为准 —— 文档是用户经过"选择文件"
    这一显性动作提交的, 信号强于随时可变的输入框文本。

【与 intent_router 的区别】
    - intent_router: 判"用户想做什么" → 写 task_type;
    - input_source_router: 判"用户给了什么形式" → 只读 uploaded_doc_path, 不改 task_type。
    两者维度不同, 本节点是对「意图 vs 输入」一致性的第一层(形式层)分流。
"""

from __004__langgraph_more_nodes.agent_state import AgentState


def input_source_router(state: AgentState):
    """输入来源路由锚点节点。

    本节点本身是「分流锚点」: 只打印日志、不写 state(返回空 dict)。
    真正的「文档/文本」决策由主图 add_conditional_edges 的 lambda 完成 ——
    它读取 uploaded_doc_path: 有则走 doc_extract(文档路径), 无则走
    text_recognize(文本路径)。

    注意: LangGraph 的【节点函数】必须返回 dict(state 更新), 不能返回路由字符串;
    路由字符串由条件边的「判定函数」(而非节点)返回。
    """
    doc_path = state.get("uploaded_doc_path", "")
    if doc_path and str(doc_path).strip():
        print("--- 输入来源路由锚点: 检测到上传文档(决策在下游条件边) → 文档路径 ---")
    else:
        print("--- 输入来源路由锚点: 无上传文档(决策在下游条件边) → 文本路径 ---")
    return {}
