"""N1 意图路由节点: 识别任务类型(合同审核/合规审查/法律检索/法律问答/小红书/其他)"""
# 📜 代码文字逻辑解析
# 本文件是 AI 法律助理(LangGraph 多智能体系统)的入口节点之一,负责"意图路由"任务。
# 在 LangGraph 状态图(StateGraph)中,该节点紧随 START 之后执行,是整个工作流的"分流器"。
# 其核心职责是: 接收用户原始输入(state["input"]), 调用 LLM 对其进行意图分类,
# 输出一个任务类型标识符(task_type), 如 contract_review / compliance_review / legal_research /
# legal_qa / xiaohongshu / other, 该标识符后续会作为条件路由(intent_router_router)的判断依据,
# 决定流程走哪条业务链路。因此, 该节点的输出准确度直接决定后续节点能否被正确触发。
# 本节点采用 "LLM 主判 + 兜底模糊匹配 + 异常降级" 三层策略, 提升鲁棒性:
# (1) LLM 直接输出任务类型; (2) 若输出不在白名单内, 则用子串匹配尝试纠正;
# (3) 若仍无法识别或 LLM 异常, 则降级为 "other", 避免流程中断。
# 设计上节点函数以 AgentState(dict)为输入输出, 符合 LangGraph 的状态合并式编程范式。


# 从 langchain_core.messages 导入 HumanMessage, 用于构造对 LLM 的"用户消息"输入
# LangChain 中消息分为 HumanMessage(用户)/AIMessage(AI)/SystemMessage(系统)等,
# 这里我们以"用户消息"形式向 LLM 提交分类 prompt
from langchain_core.messages import HumanMessage

# 从项目共享模块 common.llm 导入统一的 LLM 实例 my_llm
# 项目的所有节点共用同一个 LLM 实例(便于配置统一、模型切换、日志追踪),
# my_llm 一般是 LangChain 的 BaseChatModel 实现(如 ChatOpenAI / ChatZhipuAI 等)
from common.llm import my_llm

# 从同包的 agent_state 模块导入 AgentState 类型, 该类型是 TypedDict, 作为节点函数的输入输出类型注解
# AgentState 是各节点之间传递的"共享状态总线", 包含 input/doc_text/task_type 等所有跨节点字段
from __004__langgraph_more_nodes.agent_state import AgentState


def intent_router_node(state: AgentState):
    """
    意图路由节点函数: 根据用户输入文本识别任务类型, 并写入 state["task_type"]。

    作用:
        作为 LangGraph 工作流的第一个业务节点, 读取用户的原始输入(state["input"]),
        调用 LLM 将其归类到预定义的六类任务之一, 输出英文标识符供后续条件路由使用。
        该节点是"分流器", 决定整个流程走合同审核、合规审查、法律检索、法律问答、小红书 还是 兜底分支。

    参数:
        state (AgentState): LangGraph 共享状态字典, 必须包含 "input" 字段(用户原始输入文本)。
                            本节点仅读取 state["input"], 不依赖其他字段。

    返回值:
        AgentState: 更新后的状态字典, 新增/覆盖 "task_type" 字段, 取值为以下之一:
            - "contract_review"  : 合同审核
            - "compliance_review": 合规审查
            - "legal_research"   : 法律检索
            - "legal_qa"         : 法律问答
            - "xiaohongshu"      : 小红书发布
            - "other"            : 其他(非法律相关或无法识别)

    可迁移性说明:
        本节点的"意图分类"模式可迁移到任何需要分流的多任务对话系统中。
        只需修改 prompt 中的任务类型列表和白名单集合 valid 即可适配新业务场景,
        例如客服系统(咨询/投诉/退款)、智能助手(闲聊/工具调用/搜索)等。
        LLM 调用、模糊匹配、异常降级的三层容错策略具有通用性, 推荐保留。
    """
    # 打印日志, 标记节点开始执行(便于调试与流程追踪)
    print("开始意图路由")

    # 从状态字典中取出用户输入文本, 若 "input" 键不存在则默认为空字符串
    # state.get(key, default) 是 dict 的安全取值方法, 避免 KeyError
    user_input = state.get("input", "")

    # 构造意图分类的提示词(prompt), 使用 f-string 将用户输入动态嵌入
    # prompt 设计要点:
    #   (1) 明确角色定位("法律AI助手的意图分类器"), 引导 LLM 进入正确语境;
    #   (2) 列出所有候选任务类型及中文说明, 保证 LLM 输出在白名单内;
    #   (3) 强约束输出格式("只输出任务类型英文标识, 不要解释"), 便于后续解析;
    #   (4) 提供示例("如: contract_review"), 利用 in-context learning 提升准确率
    prompt = f"""你是一个法律AI助手的意图分类器。请判断用户输入属于以下哪类任务:
- contract_review: 合同审核(用户上传或粘贴了合同, 要求审核)
- compliance_review: 合规审查(用户要求做合规检查)
- legal_research: 法律检索(用户要求查找法规/案例)
- legal_qa: 法律问答(用户提出法律问题, 要求解答)
- xiaohongshu: 小红书发布(用户要求发布小红书内容)
- other: 其他(非法律相关)

用户输入: {user_input}

只输出任务类型英文标识, 不要解释。如: contract_review"""

    # 使用 try-except 包裹 LLM 调用, 防止 LLM 服务异常导致整个流程崩溃
    try:
        # 调用 my_llm.invoke 方法, 传入包含 HumanMessage 的列表
        # LangChain 的 chat model 接受消息列表作为输入, 返回 AIMessage 对象
        # resp.content 即 LLM 生成的文本内容
        resp = my_llm.invoke([HumanMessage(content=prompt)])

        # 取 LLM 返回的文本内容, 去除首尾空白并转小写, 便于后续匹配
        task_type = resp.content.strip().lower()

        # 定义合法任务类型白名单集合, 用于校验 LLM 输出是否在预期范围内
        # 使用集合(set)而非列表, 是因为集合的 in 操作时间复杂度为 O(1), 效率更高
        valid = {"contract_review", "compliance_review", "legal_research",
                 "legal_qa", "xiaohongshu", "other"}

        # 若 LLM 输出不在白名单内, 进入模糊匹配纠正逻辑
        if task_type not in valid:
            # 遍历白名单中的每个合法类型, 检查 LLM 输出是否"包含"该子串
            # 这能处理 LLM 输出多余文本的情况, 如 "task: contract_review" 或 "contract_review。"
            for v in valid:
                if v in task_type:
                    task_type = v  # 命中则纠正为标准类型
                    break
            # for...else 语法: 当 for 循环正常结束(未 break)时执行 else 块
            # 即所有合法类型都不在 LLM 输出中, 说明输出完全不可用, 降级为 "other"
            else:
                task_type = "other"
    # 捕获所有异常(网络错误、API 限流、模型不可用等), 保证节点不会因 LLM 故障而中断流程
    except Exception as e:
        # 打印警告日志, 包含异常信息, 便于运维定位问题
        print(f"⚠️ 意图识别失败, 默认 other: {e}")
        # 异常情况下, 任务类型降级为 "other", 由后续兜底分支处理(通常直接 LLM 回答)
        task_type = "other"

    # 将识别出的任务类型写入状态字典的 "task_type" 字段
    # 该字段是后续条件路由(intent_router_router)的判断依据, 决定流程走向
    state["task_type"] = task_type

    # 打印节点完成日志, 显示识别结果(便于调试与流程追踪)
    print(f"完成意图路由: {task_type}")

    # 返回更新后的状态字典, LangGraph 会将其合并到全局状态中
    return state


# 模块自测入口: 当直接运行本文件时执行, 用于单元测试/手动验证节点逻辑
if __name__ == "__main__":
    # 构造一个测试用的 AgentState 实例, 仅包含 input 字段(模拟用户输入)
    s = AgentState(input="帮我审核这份买卖合同")
    # 调用意图路由节点, 打印返回的状态字典(应包含 task_type="contract_review")
    print(intent_router_node(s))
