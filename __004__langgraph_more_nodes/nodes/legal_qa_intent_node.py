"""法律问答意图识别节点(仿中医zhongyi_intent_node)"""
# 📜 代码文字逻辑解析
# 本文件是 AI 法律助理(LangGraph 多智能体协作)流程中的"法律问答意图识别节点",
# 借鉴自中医项目的 zhongyi_intent_node 设计。它在意图路由阶段负责判断用户输入
# 是否属于"法律问答"意图, 即用户是否在询问法律条款、法规内容、法律概念定义、
# 行为合规性、案件适用法律等问题, 从而决定后续是否进入"法律检索 → 知识图谱问答"
# 流程。核心逻辑:1) 从 AgentState 读取用户原始输入;2) 构造一个详细的二分类提示词,
# 明确列出"是"与"否"的判定标准(询问法条/概念/合规为"是", 合同审核/合规审查/小红书
# 发布/闲聊为"否"), 要求 LLM 仅输出"是"或"否";3) 调用 LLM 获取回复;4) 对回复进行
# 严格匹配, 并辅以"以'是'开头且前 3 字不含'否'"的兜底规则;5) 异常时默认置为 False,
# 保证流程不中断;6) 将布尔结果写入 state["is_legal_qa_intent"], 供条件边进行路由。
# 该节点是典型的"LLM 二分类意图识别器"实现, 可作为任何"是否XX意图"判定场景的迁移模板。
# 导入 LangChain 的 HumanMessage 类型, 用于承载用户输入
from langchain_core.messages import HumanMessage
# 导入项目统一的 LLM 实例, 封装了模型选择与调用细节
from common.llm import my_llm
# 导入 AgentState 类型, 它是整个 LangGraph 图中各节点共享的状态字典(TypedDict)
from __004__langgraph_more_nodes.agent_state import AgentState


def legal_qa_intent_node(state: AgentState):
    """
    法律问答意图识别节点: 判断用户输入是否属于法律问答意图。

    作用:
        作为意图路由的前置判定节点, 通过 LLM 判断用户输入是否属于"法律问答"
        (询问法条/法规内容、法律概念定义、行为合规性、案件适用法律等), 并将布尔
        结果写入 state["is_legal_qa_intent"], 供后续条件边决定是否进入法律检索流程。

    参数:
        state (AgentState): LangGraph 共享状态字典, 需包含:
            - input (str): 用户原始输入文本

    返回值:
        AgentState: 更新后的状态字典, 新增 is_legal_qa_intent 字段(bool),
        True 表示用户有法律问答意图, False 表示无意图或识别失败。

    可迁移性说明:
        本节点是典型的"LLM 二分类意图识别器"实现, 通过细化提示词中的判定标准,
        可迁移到任何领域意图识别场景(如:医疗咨询、技术支持、客服分类等)。
    """
    # 打印日志, 标记进入法律问答意图识别阶段, 便于在控制台追踪节点执行顺序
    print("开始识别是否法律问答")
    # 从 state 中读取用户原始输入, 缺失时为空字符串(避免 None 导致后续拼接异常)
    user_input = state.get("input", "")

    # 构造意图分类提示词, 使用 f-string 将用户输入嵌入, 并明确"是/否"的判定标准
    prompt = f"""
    用户输入: {user_input}

    你是一个意图分类器。
    任务：判断用户是否在提出法律相关问题, 需要通过知识图谱检索法律法规/法条/法律概念来回答。
    判断标准：
    - 用户询问某法律条款/法规内容 -> 是
    - 用户询问某法律概念定义 -> 是
    - 用户询问某行为是否违法/合规 -> 是
    - 用户询问某案件类型适用法律 -> 是
    - 合同审核/合规审查任务 -> 否(这些走专门流程)
    - 小红书发布 -> 否
    - 闲聊/非法律 -> 否
    输出要求：只能输出"是"或"否"，不要输出任何解释。
    """

    # 使用 try/except 包裹 LLM 调用, 防止网络/模型异常导致整个图流程崩溃
    try:
        # 调用 LLM, 传入仅含 HumanMessage 的消息列表, 返回 AIMessage 对象
        response = my_llm.invoke([HumanMessage(content=prompt)])
        # 取出回复文本并去除首尾空白, 得到模型的标准答案
        answer = response.content.strip()
        # 严格匹配"是", 或防御性匹配"以'是'开头且前 3 字不含'否'"
        if answer == "是" or (answer.startswith("是") and "否" not in answer[:3]):
            state["is_legal_qa_intent"] = True
        else:
            # 其余情况(含"否"或非标准输出)一律视为无意图
            state["is_legal_qa_intent"] = False
    except Exception as e:
        # 捕获任意异常, 打印警告日志, 并将意图默认置为 False, 保证流程不中断
        print(f"⚠️ 意图识别失败: {e}")
        state["is_legal_qa_intent"] = False

    # 打印日志, 输出最终识别结果, 便于调试与监控
    print(f"完成法律问答意图识别: {state.get('is_legal_qa_intent')}")
    # 返回更新后的 state, 供 LangGraph 继续流转
    return state


# 脚本直接运行时的自测入口
if __name__ == "__main__":
    # 构造一个询问民法典违约金条款的测试 state, 用于验证意图识别效果
    s = AgentState(input="民法典第585条违约金是怎么规定的？")
    # 调用节点并打印识别结果
    print(legal_qa_intent_node(s).get("is_legal_qa_intent"))
