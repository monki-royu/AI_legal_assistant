"""
法律问答最终回答节点
================================

【文件作用】legal_qa 链路的最终 LLM 出答节点。

【位置】位于检索智能体(5节点子图)之后:
    retrieval_intent_decompose -> retrieval_base_layer -> retrieval_enhance_query
    -> retrieval_fusion_sort -> retrieval_output -> legal_qa_final_answer_node -> END

【核心职责】
    1. 读取检索智能体产出的 research_context / citations / quality_score;
    2. 结合用户原始问题 input, 调用 LLM 组织成自然语言答案;
    3. 将答案写入 output 与 legal_qa_answer 字段, 供前端展示。

【设计原则】
    - 检索智能体只负责"找依据", 本节点负责"组织语言回答用户";
    - 若检索无依据(quality_score 过低或 research_context 为空), 明确告知用户并建议咨询律师,
      绝不编造法条;
    - 保留 citations 引用信息, 让答案可溯源。
"""

import json
from langchain_core.messages import SystemMessage, HumanMessage
from common.llm import my_llm
from __004__langgraph_more_nodes.agent_state import AgentState


def legal_qa_final_answer_node(state: AgentState):
    """
    法律问答最终回答节点: 基于检索结果生成面向用户的自然语言答案。

    【参数】
        state (AgentState): LangGraph 共享状态字典, 读取:
            - input (str): 用户原始问题
            - research_context (str): 检索到的法规/案例上下文
            - citations (List[dict]): 结构化引用列表
            - quality_score (int/float): 检索质量分

    【返回值】
        AgentState: 写入:
            - legal_qa_answer (str): 法律问答答案(主字段)
            - output (str): 面向前端展示的通用输出
    """
    print("法律问答 [最终回答] 结合检索结果生成答案")

    user_input = state.get("input", "")
    research_context = state.get("research_context", "") or ""
    citations = state.get("citations", []) or []
    quality_score = state.get("quality_score", 0) or 0

    # 构建引用摘要(最多前 10 条)
    citation_texts = []
    for c in citations[:10]:
        title = c.get("title", "")
        article_no = c.get("article_no", "")
        content = c.get("content", "")[:150]
        source = c.get("source", "")
        citation_texts.append(f"【{title} {article_no}】(来源:{source}): {content}")
    citations_block = "\n".join(citation_texts) if citation_texts else "(未检索到引用)"

    # 检索无有效依据时, 直接无依据兜底
    if not research_context.strip() or quality_score < 20:
        print("  ⚠️ 检索未命中有效依据, 无依据兜底")
        fallback = (
            "抱歉, 当前法律知识库未检索到与您问题相关的权威依据, 无法给出有据可查的回答。\n\n"
            "建议您:\n"
            "1. 补充更具体的法律场景、主体或法规名称;\n"
            "2. 咨询专业律师获取针对性法律意见。\n\n"
            "*(法智引擎仅基于检索结果作答, 未检索到依据时不编造法条。)*"
        )
        state["legal_qa_answer"] = fallback
        state["output"] = fallback
        return state

    prompt = f"""你是法智引擎AI法律助理。请根据以下检索到的法律依据, 回答用户的法律问题。

【用户问题】
{user_input}

【检索质量分】
{quality_score}/100

【检索到的法律依据】
{research_context[:3000]}

【引用条目】
{citations_block}

要求:
1. 基于上述检索依据作答, 优先引用具体法律条文(写明法规名与条号);
2. 若涉及司法案例, 可简要说明案例要旨;
3. 不要编造检索依据中不存在的法条或结论;
4. 检索依据不足以完整回答时, 明确说明不足并建议咨询专业律师;
5. 语气专业、结构清晰, 最后加上免责声明:"本回答仅供参考, 不构成正式法律意见。"
"""

    try:
        messages = [
            SystemMessage(content="你是法智引擎AI法律助理, 提供专业、有据可查的法律解答。"),
            HumanMessage(content=prompt),
        ]
        resp = my_llm.invoke(messages)
        answer = resp.content.strip()
    except Exception as e:
        answer = f"抱歉, 生成回答时出现错误: {e}"

    state["legal_qa_answer"] = answer
    state["output"] = answer
    print("  完成法律问答最终回答生成")
    return state


if __name__ == "__main__":
    # 模块自测
    s = AgentState(
        input="违约金怎么规定？",
        research_context="【民法典第五百八十五条】当事人可以约定一方违约时应当根据违约情况向对方支付一定数额的违约金。",
        citations=[{
            "title": "民法典",
            "article_no": "第五百八十五条",
            "content": "当事人可以约定一方违约时应当根据违约情况向对方支付一定数额的违约金。",
            "source": "L1·laws",
        }],
        quality_score=80,
    )
    print(legal_qa_final_answer_node(s).get("output", "")[:300])
