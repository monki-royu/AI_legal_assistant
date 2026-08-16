"""【文件作用】意图路由节点 ── 识别用户输入的任务类型，作为 LangGraph 工作流的"分流器"
【逻辑】本文件是 AI 法律助理(LangGraph 多智能体系统)的入口业务节点，紧随 START 节点执行。
    核心流程：
    1. 从【state】中读取用户原始输入文本（state["input"]）
    2. 优先检查前端是否已传入【task_type】，若有则直接使用，跳过 LLM 调用（避免误判覆盖）
    3. 若无前端传入，调用【LLM】对用户输入进行意图分类，输出六类任务标识符之一
    4. 采用"【LLM 主判】+【模糊匹配兜底】+【异常降级】"三层容错策略：
       (a) LLM 直接输出任务类型英文标识
       (b) 若 LLM 输出不在白名单内，用【子串匹配】尝试纠正
       (c) 若仍无法识别或 LLM 异常，降级为 "other" 避免流程中断
    5. 将识别结果写入 state["task_type"]，供【intent_router_router】条件路由判断流程走向
    6. 函数以【AgentState】(dict)为输入输出，符合 LangGraph 状态合并式编程范式
"""

# ============================================================
# 📦 导入模块
# ============================================================

# 从 langchain_core.messages 导入 HumanMessage（【人类消息】类）
# LangChain 的消息体系：HumanMessage（用户消息）/ AIMessage（AI 回复）/ SystemMessage（系统指令）
# 此处我们构造一条 HumanMessage 作为 LLM 的输入 prompt
from langchain_core.messages import HumanMessage

# 从项目共享模块 common.llm 导入统一的 LLM 实例 my_llm
# 【设计意图】项目中所有节点共用同一个 LLM 实例，便于配置统一管理、模型切换与日志追踪
# my_llm 通常是 LangChain 的 BaseChatModel 实现（如 ChatOpenAI / ChatZhipuAI 等）
from common.llm import my_llm

# 从同包导入 AgentState（【代理状态】类型），这是一个 TypedDict（类型化字典）
# 【作用】AgentState 是整个 LangGraph 图中各节点之间传递的"共享状态总线"
# 包含 input / doc_text / task_type / contract_risk_items 等所有跨节点字段
from __004__langgraph_more_nodes.agent_state import AgentState


def intent_router_node(state: AgentState):
    """
    【功能】意图路由节点函数：根据用户输入文本识别任务类型，并写入 state["task_type"]
    【参数】state (AgentState)：LangGraph 共享状态字典，必须包含 "input" 字段（用户原始输入文本）
                本节点仅读取 state["input"] 与 state.get("task_type")（前端传入），不依赖其他字段
    【返回值】AgentState：更新后的状态字典，新增/覆盖 "task_type" 字段
                "task_type" 取值枚举：
                - "contract_review"   → 【合同审核】用户要求审核合同
                - "compliance_review" → 【合规审查】用户要求做合规检查
                - "legal_research"    → 【法律检索】用户要求查找法规/案例
                - "legal_qa"          → 【法律问答】用户提出法律问题要求解答
                - "xiaohongshu"       → 【小红书发布】用户要求发布小红书内容
                - "other"             → 【其他】非法律相关或无法识别
    【逻辑】三层容错策略：
            1.【LLM 主判】调用 LLM 按 prompt 输出任务类型标识
            2.【模糊匹配兜底】若输出不在白名单内，遍历白名单用子串匹配纠正
            3.【异常降级】若 LLM 调用异常或完全无法识别，降级为 "other"
    【可迁移性】本节点的"意图分类 + 三层容错"模式可迁移到任何需要分流的多任务系统。
            修改 prompt 中的任务列表与 valid 白名单即可适配客服系统（咨询/投诉/退款）、
            智能助手（闲聊/工具调用/搜索）等新场景。
    """
    # 【步骤1】打印日志标记节点开始执行，便于调试与流程追踪
    print("--- 开始意图路由 ---")

    # ============================================================
    # 【步骤2】检查前端是否已传入 task_type（【优先级短路】逻辑）
    # ============================================================
    # 用 state.get("task_type", "") 安全读取，避免 KeyError
    # 【设计原因】前端可能已在界面上让用户选择任务类型，此时应直接使用避免 LLM 误判覆盖
    existing_task_type = state.get("task_type", "")  # 取出前端传入的 task_type，空串表示未传入
    # 检查是否在白名单内（使用集合 O(1) 查询），仅当存在且为有效类型时才短路返回
    if existing_task_type and existing_task_type in {"contract_review", "compliance_review", "legal_research", "legal_qa", "xiaohongshu"}:
        # 【短路返回】直接使用前端传入的类型，不调用 LLM
        print(f"--- 完成意图路由: {existing_task_type} (使用前端传入) ---")
        return {"task_type": existing_task_type}  # 返回包含 task_type 的字典，LangGraph 合并到全局状态

    # ============================================================
    # 【步骤3】从 state 中提取用户输入文本
    # ============================================================
    # state.get("input", "") 安全读取用户输入，若 "input" 键不存在则默认为空字符串
    # 【注意】空输入会导致 LLM 无法分类，最终降级为 "other"
    user_input = state.get("input", "")  # 用户原始输入字符串

    # ============================================================
    # 【步骤4】构造意图分类的 LLM Prompt（提示词）
    # ============================================================
    # 使用 f-string 将用户输入动态嵌入到 prompt 模板中
    # 【Prompt 设计要点】
    #   ① 角色定位："法律AI助手的意图分类器" → 引导 LLM 进入正确的专业语境
    #   ② 候选列表：列出全部 6 种任务类型及中文说明 → 确保 LLM 输出在白名单内
    #   ③ 输出约束："只输出任务类型英文标识，不要解释" → 便于后续解析
    #   ④ 示例提示："如: contract_review" → 利用 in-context learning（上下文学习）提升准确率
    prompt = f"""你是一个法律AI助手的意图分类器。请判断用户输入属于以下哪类任务:
- contract_review: 合同审核(用户上传或粘贴了合同, 要求审核)
- compliance_review: 合规审查(用户要求做合规检查)
- legal_research: 法律检索(用户要求查找法规/案例)
- legal_qa: 法律问答(用户提出法律问题, 要求解答)
- xiaohongshu: 小红书发布(用户要求发布小红书内容)
- other: 其他(非法律相关)

用户输入: {user_input}

只输出任务类型英文标识, 不要解释。如: contract_review"""

    # ============================================================
    # 【步骤5】调用 LLM 进行意图识别（try-except 包裹防止异常崩溃）
    # ============================================================
    try:
        # 调用 my_llm.invoke() 方法，传入包含 HumanMessage 的消息列表
        # LangChain ChatModel 接口：输入 List[BaseMessage]，输出 AIMessage
        # resp 是 AIMessage 类型对象，resp.content 即 LLM 生成的文本内容
        resp = my_llm.invoke([HumanMessage(content=prompt)])

        # 取 LLM 返回的文本内容，做两步清洗：
        #   .strip() → 去除首尾空白字符（包括换行符、空格等）
        #   .lower() → 转小写（统一大小写，便于后续匹配）
        task_type = resp.content.strip().lower()  # 清洗后的任务类型标识符

        # 定义合法任务类型的【白名单】集合
        # 【为什么用 set？】集合的 in 操作时间复杂度为 O(1)（哈希查找），列表为 O(n)，效率更高
        valid = {"contract_review", "compliance_review", "legal_research",
                 "legal_qa", "xiaohongshu", "other"}

        # ============================================================
        # 【步骤6】检查 LLM 输出是否在白名单内（【第二层】模糊匹配兜底）
        # ============================================================
        # 若 LLM 输出不在白名单内，进入模糊匹配纠正逻辑
        if task_type not in valid:
            # 遍历白名单中的每个合法类型，检查 LLM 输出是否"包含"该子串
            # 【场景示例】LLM 可能输出 "task: contract_review" 或 "contract_review。" 
            # 通过 v in task_type 子串匹配可纠正这类偏差
            for v in valid:
                if v in task_type:  # 检查合法标识 v 是否是 task_type 的子串
                    task_type = v   # 命中则纠正为标准类型
                    break           # 跳出循环，不再继续匹配
            # 【for...else 语法】当 for 循环正常结束（未被 break 中断）时执行 else 块
            # 即所有合法类型都不在 LLM 输出中，说明输出完全不可用
            else:
                task_type = "other"  # 【第三层】降级为 "other"，避免流程中断

    # 捕获所有异常（网络错误、API 限流、模型不可用、JSON 解析失败等）
    # 【设计意图】保证节点不会因 LLM 故障而中断整个 LangGraph 流程
    except Exception as e:
        # 打印警告日志，包含异常信息，便于运维定位问题
        print(f"⚠️ 意图识别失败, 默认 other: {e}")
        # 【异常降级】任务类型降级为 "other"，由后续兜底分支处理（通常直接 LLM 回答）
        task_type = "other"

    # ============================================================
    # 【步骤7】将识别结果写入 state 字典
    # ============================================================
    # state["task_type"] 是后续条件路由（intent_router_router）的判断依据
    # intent_router_router 会根据此字段值决定流程走哪条业务链路
    state["task_type"] = task_type  # 写入状态字典

    # 打印节点完成日志，显示最终识别结果（便于调试与流程追踪）
    print(f"--- 完成意图路由: {task_type} ---")

    # 返回更新后的状态字典
    # 【LangGraph 机制】返回值会被合并（merge）到全局状态中
    # 注意：我们修改了传入的 state，同时也返回它（LangGraph 两种方式都支持）
    return state


# ============================================================
# 🧪 模块自测入口（仅在直接运行本文件时执行）
# ============================================================
if __name__ == "__main__":
    # 构造一个测试用的 AgentState 实例，仅包含 "input" 字段
    # 模拟用户输入"帮我审核这份买卖合同" → 预期输出 task_type="contract_review"
    s = AgentState(input="帮我审核这份买卖合同")
    # 调用意图路由节点，打印返回的状态字典
    # 预期结果：{..., "task_type": "contract_review"}
    print(intent_router_node(s))