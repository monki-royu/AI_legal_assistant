"""LLM 直接输出节点 —— 非法律相关问题的兜底回答路径

【QA 子图 Level 3 内部路由】
当 qa_intent_classify 判断用户问题为"非法律相关"时,
不走检索智能体路径, 直接通过本节点由 LLM 生成回答。

典型场景:
- 用户闲聊/问候: "你好"、"你是谁"
- 非法律问题: "今天天气怎么样"、"帮我写一首诗"
- 编程/技术问题: "Python 如何读取文件"

设计思想:
避免非法律问题触发完整的检索流程, 节省 API 调用开销, 提升响应速度。
"""

from langchain_core.messages import HumanMessage
from common.llm import my_llm
from __004__langgraph_more_nodes.agent_state import AgentState


def llm_direct_out_node(state: AgentState):
    """
    LLM 直接输出节点: 非法律相关问题的兜底回答。

    当 is_legal_related=False 时, 由 QA 子图内部路由到此节点。
    直接调用 LLM 生成回答, 不经过检索流程。

    写入字段:
        - output (str): LLM 生成的直接回答
        - think_process (str): 思考过程 (追加)
        - is_legal_related (bool): 保持 False
    """
    user_input = state.get("input", "")
    print("QA子图 [非法律相关] → LLM 直接回答")

    try:
        prompt = f"""你是法智引擎 AI 法律助理。用户提出了一个非法律相关的问题, 
请以友好、专业的方式回答。如果问题超出你的能力范围, 请礼貌说明。

用户问题: {user_input}

请直接回答, 无需引用法律条文。"""

        response = my_llm.invoke([HumanMessage(content=prompt)])
        output_text = response.content.strip()
    except Exception as e:
        print(f"⚠️ LLM 直接回答失败: {e}")
        output_text = "抱歉, 我暂时无法回答您的问题。如果您有法律相关的问题, 欢迎随时提问。"

    # 追加思考过程
    think = state.get("think_process", "") or ""
    think += "\n[QA子图] 非法律相关 → LLM 直接回答 (跳过检索)"

    return {
        "output": output_text,
        "think_process": think,
        "is_legal_related": False,
    }