"""QA 子图内部意图分类节点 ── 判断法律问答是"法律相关"还是"非法律相关"

【新架构 · Level 3 内部路由】
    本节点是 QA 子图(legal_qa 链路)内部的第三级路由判断:
    - 在 Level 1 (小红书/非小红书) 和 Level 2 (4大路径分组) 之后,
      当任务被确定为 legal_qa 时, 进入本节点做最终内部分类。
    - 法律相关 (is_legal_related=True) → 检索智能体路径 (法规/案例检索 → 融合 → 质量门禁 → 最终回答)
    - 非法律相关 (is_legal_related=False) → LLM 直接回答路径 (兜底, 不调用检索)

【设计思想】
    不是所有"法律问答"都需要走完整的 RAG 检索流程:
    - 用户问"民法典违约金怎么规定" → 需要检索 (法律相关)
    - 用户问"你好" / "今天天气怎样" → 直接 LLM 回答 (非法律相关)
    这样可以避免不必要的检索开销, 提升响应速度。

【文件位置】nodes/legal_qa_intent_node.py
【原文件名延续】保留原文件名, 但函数名更新为 qa_intent_classify
"""

from langchain_core.messages import HumanMessage
from common.llm import my_llm
from __004__langgraph_more_nodes.agent_state import AgentState


def qa_intent_classify(state: AgentState):
    """
    QA 子图内部意图分类: 判断用户的 legal_qa 请求是"法律相关"还是"非法律相关"。

    作用:
        作为 legal_qa 链路的内部三级路由节点, 通过 LLM 判断用户输入是否涉及法律问题,
        并将布尔结果写入 state["is_legal_related"], 供 QA 子图内部条件边决定:
        - True  → 走检索智能体 (检索法规/案例 → 融合 → 质量门禁 → legal_qa_final_answer)
        - False → 走 LLM 直接回答 (llm_direct_out_node 兜底)

    参数:
        state (AgentState): LangGraph 共享状态字典, 需包含:
            - input (str): 用户原始输入文本

    返回值:
        AgentState: 更新后的状态字典, 新增 is_legal_related 字段(bool),
        True 表示用户问题法律相关, False 表示非法律相关。

    可迁移性:
        本节点是典型的"LLM 二分类意图识别器", 通过细化提示词中的判定标准,
        可迁移到任何领域意图识别场景。
    """
    print("QA子图 [Level 3] 内部意图分类: 判断法律相关/非法律相关")

    user_input = state.get("input", "")

    prompt = f"""
    用户输入: {user_input}

    你是一个法律QA子图的意图分类器。
    任务：判断用户的问题是否与法律相关，需要通过检索法律法规/法条/法律概念来回答。
    判断标准：
    - 用户询问法律条款/法规内容/法律概念定义 → 是(法律相关)
    - 用户询问行为是否违法/合规/侵权 → 是(法律相关)
    - 用户询问案件类型适用法律/判例 → 是(法律相关)
    - 用户询问合同起草/审查/修改建议 → 是(法律相关)
    - 闲聊/问候/笑话/编程/日常生活咨询 → 否(非法律相关)
    - 其他非法律领域的专业问题 → 否(非法律相关)

    输出要求：只能输出"是"或"否"，不要输出任何解释。
    """

    try:
        response = my_llm.invoke([HumanMessage(content=prompt)])
        answer = response.content.strip()

        if answer == "是" or (answer.startswith("是") and "否" not in answer[:3]):
            state["is_legal_related"] = True
        else:
            state["is_legal_related"] = False
    except Exception as e:
        print(f"⚠️ QA意图分类失败(默认非法律相关): {e}")
        state["is_legal_related"] = False

    is_legal = state.get("is_legal_related", False)
    print(f"QA子图分类结果: {'法律相关' if is_legal else '非法律相关'} (is_legal_related={is_legal})")

    return state


# 兼容旧函数名 (保留原函数名作为别名, 内部调用新函数)
def legal_qa_intent_node(state: AgentState):
    """
    【兼容层】原 legal_qa_intent_node 函数名, 内部转发到 qa_intent_classify。
    保留此函数名以避免其他模块 import 报错。
    """
    return qa_intent_classify(state)


# 脚本直接运行时的自测入口
if __name__ == "__main__":
    s = AgentState(input="民法典第585条违约金是怎么规定的？")
    result = qa_intent_classify(s)
    print(f"分类结果: {result.get('is_legal_related')}")