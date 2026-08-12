"""通用回答节点: 非法律相关问题, LLM直接回答"""
# 📜 代码文字逻辑解析
# 本文件是 AI 法律助理(LangGraph 多智能体协作)流程中的"通用回答节点",
# 作为意图路由的兜底分支: 当用户输入既不属于合同审核、合规审查、法律检索,
# 也不属于小红书发布等专门任务时, 流程会路由到此节点, 由 LLM 直接给出回复。
# 核心逻辑十分简洁:1) 从 AgentState 中取出用户原始输入;2) 构造一个由
# SystemMessage(设定 AI 为"法智引擎"法律助理的角色与引导规则)与
# HumanMessage(用户输入)组成的消息列表;3) 调用 my_llm.invoke 同步获取 LLM 回复;
# 4) 将回复写入 state["output"];5) 用 try/except 捕获异常, 失败时写入友好错误提示,
# 保证节点不会因 LLM 调用失败而中断整个图流程。该节点展示了 LangGraph 中
# "LLM 直答节点"的最简实现范式, 可作为任何"兜底对话"场景的迁移模板。
# 导入 LangChain 的消息类型: SystemMessage 用于设定角色, HumanMessage 用于承载用户输入
from langchain_core.messages import HumanMessage, SystemMessage
# 导入项目统一的 LLM 实例, 封装了模型选择与调用细节
from common.llm import my_llm
# 导入 AgentState 类型, 它是整个 LangGraph 图中各节点共享的状态字典(TypedDict)
from __004__langgraph_more_nodes.agent_state import AgentState


def llm_direct_out_node(state: AgentState):
    """
    通用回答节点: 对非法律相关问题, 由 LLM 直接生成回复。

    作用:
        作为意图路由的兜底分支, 当用户输入不属于合同审核/合规审查/法律检索/
        小红书发布等专门任务时, 由 LLM 以"法智引擎"法律助理的身份直接回答,
        并在适当时机引导用户提出法律相关问题。

    参数:
        state (AgentState): LangGraph 共享状态字典, 需包含:
            - input (str): 用户原始输入文本

    返回值:
        AgentState: 更新后的状态字典, 新增 output 字段(字符串), 内容为 LLM 回复
        或异常时的友好错误提示。

    可迁移性说明:
        本节点是最简的"LLM 直答"范式, 只依赖 LangChain 消息类型与一个 LLM 实例,
        可直接迁移到任何需要"兜底对话/通用问答"的智能体流程, 只需修改系统提示词即可。
    """
    # 打印日志, 标记进入通用回答阶段, 便于在控制台追踪节点执行顺序
    print("开始通用回答")
    # 从 state 中读取用户原始输入, 缺失时为空字符串(避免 None 导致后续拼接异常)
    user_input = state.get("input", "")

    # 构造发送给 LLM 的消息列表, 包含系统消息(角色设定)与用户消息(实际问题)
    messages = [
        # SystemMessage: 设定 AI 的角色与行为规则, 引导其区分法律/非法律问题
        SystemMessage(content=(
            "你是法智引擎, 一个AI法律助理。"
            "如果用户问题与法律无关, 请友好回答并引导用户提出法律相关问题。"
            "如果涉及法律问题但不是合同审核/合规审查/检索任务, 请简要回答。"
        )),
        # HumanMessage: 承载用户实际输入, 作为 LLM 的待回答内容
        HumanMessage(content=user_input),
    ]

    # 使用 try/except 包裹 LLM 调用, 防止网络/模型异常导致整个图流程崩溃
    try:
        # 同步调用 LLM, 传入消息列表, 返回 AIMessage 对象
        resp = my_llm.invoke(messages)
        # 取出回复文本并去除首尾空白, 写入 state["output"] 供下游使用
        state["output"] = resp.content.strip()
    except Exception as e:
        # 捕获任意异常, 写入友好错误提示, 保证节点不抛出异常
        state["output"] = f"抱歉, 处理您的问题时出现错误: {e}"

    # 打印日志, 标记通用回答完成
    print("完成通用回答")
    # 返回更新后的 state, 供 LangGraph 继续流转
    return state


# 脚本直接运行时的自测入口
if __name__ == "__main__":
    # 构造一个包含简单用户输入的测试 state
    s = AgentState(input="你好")
    # 调用节点并打印输出, 用于人工验证 LLM 回复效果
    print(llm_direct_out_node(s).get("output"))
