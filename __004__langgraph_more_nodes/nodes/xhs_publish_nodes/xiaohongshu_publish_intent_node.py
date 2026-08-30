"""小红书发布意图识别节点(仿中医项目)"""
# ============================================================
# 文件名称: nodes/xiaohongshu_publish_intent_node.py
# 文件作用: 小红书发布意图识别
# ============================================================
# 【这个文件是干什么的？】
# 小红书发布意图识别
#
# 【代码逻辑主线】
# 参见各函数前的【功能】【参数】【返回值】【逻辑】说明。
#
# 【新手建议】
# 先看主函数 -> 再看辅助函数。
#

# 📜 代码文字逻辑解析
# 本文件是 AI 法律助理(LangGraph 多智能体协作)流程中的"小红书发布意图识别节点",
# 在意图路由阶段负责判断用户输入中是否包含
# "想在小红书发布笔记/内容"的意图, 从而决定后续是否进入"文案生成 → 图片生成 →
# 自动发布"的小红书发布流水线。核心逻辑:1) 从 AgentState 读取用户原始输入;
# 2) 构造一个仅要求输出"是"或"否"的二分类提示词, 将用户输入嵌入其中;
# 3) 调用 LLM 进行意图判定;4) 对 LLM 返回结果进行严格匹配, 并辅以"防御性处理"
# (当模型输出非标准答案时, 通过子串包含规则兜底);5) 异常时默认置为 False,
# 保证流程不中断;6) 将布尔结果写入 state["is_xiaohongshu_publish_intent"],
# 供条件边(conditional edge)进行路由判断。该节点是典型的"LLM 意图分类器"实现,
# 可作为任何二分类意图识别场景的迁移模板。
# 导入 LangChain 的 HumanMessage 类型, 用于承载用户输入
from langchain_core.messages import HumanMessage
# 导入项目统一的 LLM 实例, 封装了模型选择与调用细节
from common.llm import my_llm
# 导入 AgentState 类型, 它是整个 LangGraph 图中各节点共享的状态字典(TypedDict)
from __004__langgraph_more_nodes.agent_state import AgentState


def xiaohongshu_publish_intent_node(state: AgentState):
    """
    小红书发布意图识别节点: 判断用户是否想在小红书发布内容。

    作用:
        作为意图路由的前置判定节点, 通过 LLM 判断用户输入中是否包含
        "要在小红书发笔记/发内容"的意图, 并将布尔结果写入 state, 供后续
        条件边决定是否进入小红书发布流水线。

    参数:
        state (AgentState): LangGraph 共享状态字典, 需包含:
            - input (str): 用户原始输入文本

    返回值:
        AgentState: 更新后的状态字典, 新增 is_xiaohongshu_publish_intent 字段(bool),
        True 表示用户有发布意图, False 表示无意图或识别失败。

    可迁移性说明:
        本节点是典型的"LLM 二分类意图识别器"实现, 只需修改提示词中的分类标准,
        即可迁移到任何"是否XX意图"的判定场景(如:是否需要转人工、是否违规等)。
    """
    # 打印日志, 标记进入小红书意图识别阶段, 便于在控制台追踪节点执行顺序
    print("开始识别是否有发小红书的意图")
    # 从 state 中读取用户原始输入, 缺失时为空字符串(避免 None 导致后续拼接异常)
    user_input = state.get("input", "")

    # 构造意图分类提示词, 使用 f-string 将用户输入嵌入, 要求 LLM 仅输出"是"或"否"
    prompt = f"""
    用户输入: {user_input}

    你是一个意图分类器。
    任务：判断用户是否有"要在小红书发笔记/发内容"的意图。
    输出要求：只能输出"是"或"否"，不要输出任何解释或其他文字。
    """

    # 使用 try/except 包裹 LLM 调用, 防止网络/模型异常导致整个图流程崩溃
    try:
        # 调用 LLM, 传入仅含 HumanMessage 的消息列表, 返回 AIMessage 对象
        response = my_llm.invoke([HumanMessage(content=prompt)])
        # 取出回复文本并去除首尾空白, 得到模型的标准答案
        model_answer = response.content.strip()
        # 严格匹配"是": 直接置为 True
        if model_answer == "是":
            state["is_xiaohongshu_publish_intent"] = True
        # 严格匹配"否": 直接置为 False
        elif model_answer == "否":
            state["is_xiaohongshu_publish_intent"] = False
        else:
            # 防御性处理: 当模型未严格按格式输出(如"是的"/"是的，需要"等)时,
            # 通过子串包含规则兜底: 若含"是"且不含"否"则视为 True, 否则视为 False
            if "是" in model_answer and "否" not in model_answer:
                state["is_xiaohongshu_publish_intent"] = True
            else:
                state["is_xiaohongshu_publish_intent"] = False
    except Exception as e:
        # 捕获任意异常, 打印警告日志, 并将意图默认置为 False, 保证流程不中断
        print(f"⚠️ 意图识别失败: {e}")
        state["is_xiaohongshu_publish_intent"] = False

    # 打印日志, 输出最终识别结果, 便于调试与监控
    print(f"完成小红书意图识别: {state.get('is_xiaohongshu_publish_intent')}")
    # 返回更新后的 state, 供 LangGraph 继续流转
    return state


# 脚本直接运行时的自测入口
if __name__ == "__main__":
    # 构造一个包含典型小红书发布意图的测试 state
    s = AgentState(input="我想在小红书发笔记, 关于法律科普的")
    # 调用节点并打印识别结果, 用于人工验证意图分类效果
    print(xiaohongshu_publish_intent_node(s).get("is_xiaohongshu_publish_intent"))
