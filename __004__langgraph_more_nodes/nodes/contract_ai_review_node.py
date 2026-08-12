"""N5a 合同审核AI节点: 立场与商业条款审核, 生成合同风险项"""
# 📜 代码文字逻辑解析
# 本文件是 AI 法律助理(LangGraph 多智能体系统)中的"合同审核 AI 节点", 对应业务流程的 N5a 环节。
# 其核心职责是: 以专业合同审核律师的视角, 从用户方立场(user_side)出发, 审核合同商业条款,
# 识别风险项(权利义务对等/付款条件/违约责任/知识产权/争议解决/合同解除等), 以 JSON 数组形式
# 写入 state["contract_risk_items"]。该字段是三路风险检测(合同审核/合规审查/数值校验)之一,
# 最终会被 risk_aggregate_node 聚合去重, 形成综合风险评分与等级。
# 节点支持两种审核模式: AI_AUTO(完全 LLM 自动审核)与 CUSTOM_RULES(用户自定义规则辅助),
# 后者会将用户提供的 custom_rules 注入 prompt, 引导 LLM 重点关注用户关心的条款。
# 节点采用 "LLM 审核 + JSON 数组解析 + 代码块剥离 + 类型校验 + 异常降级" 策略, 保证输出始终为列表。


# 导入 json 模块, 用于解析 LLM 返回的 JSON 数组字符串
import json

# 从 langchain_core.messages 导入 HumanMessage, 用于构造 LLM 的用户消息
from langchain_core.messages import HumanMessage

# 从项目共享模块 common.llm 导入统一的 LLM 实例 my_llm
from common.llm import my_llm

# 从同包导入 AgentState 类型, 作为节点函数的类型注解
from __004__langgraph_more_nodes.agent_state import AgentState


def contract_ai_review_node(state: AgentState):
    """
    合同审核 AI 节点函数: 从用户立场审核合同商业条款, 生成风险项列表, 写入 state["contract_risk_items"]。

    作用:
        读取合同全文(前 5000 字)、合同类型、用户立场、审核模式、自定义规则,
        以"专业合同审核律师"角色调用 LLM 识别商业条款风险。每个风险项包含:
        条款(clause)/风险类型(risk_type)/严重程度(severity)/描述(description)/
        修改建议(suggestion)/法律依据(legal_basis)。
        支持 AI_AUTO(纯 LLM 审核)与 CUSTOM_RULES(注入用户规则)两种模式。

    参数:
        state (AgentState): LangGraph 共享状态字典。读取字段:
                            - doc_text (str, 可选): 合同全文(取前 5000 字)
                            - contract_type (str, 可选): 合同类型, 默认 "其他"
                            - user_side (str, 可选): 用户立场(A/B/Unknown), 默认 "Unknown"
                            - review_mode (str, 可选): 审核模式(AI_AUTO/CUSTOM_RULES), 默认 "AI_AUTO"
                            - custom_rules (List[str], 可选): 自定义审核规则, 仅 CUSTOM_RULES 模式使用
                            写入字段:
                            - contract_risk_items (List[Dict]): 合同风险项列表

    返回值:
        AgentState: 更新后的状态字典, 必含 "contract_risk_items" 字段(可能为空列表)。

    可迁移性说明:
        本节点的"立场化审核 + 结构化风险输出 + 多模式支持"模式可迁移到任何专业审核场景,
        例如: 招投标文件审核、学术论文审核、代码安全审核等。
        通过修改 prompt 中的审核要点与输出 schema 即可适配新业务。
        "自定义规则注入"机制提供了良好的扩展性, 允许用户在不修改代码的情况下定制审核策略。
    """
    # 打印节点开始日志
    print("开始合同AI审核")

    # 从状态字典中取出合同全文, 并切片取前 5000 字符
    # 5000 字是合同审核的主要关注范围, 平衡了覆盖度与 LLM 成本
    doc_text = state.get("doc_text", "")[:5000]

    # 取合同类型, 默认 "其他"(若上游分类节点未执行或失败)
    contract_type = state.get("contract_type", "其他")

    # 取用户立场, 默认 "Unknown"(若 party_identify_node 未执行或未识别)
    # 该值会注入 prompt, 让 LLM 从用户方立场评估条款利弊
    user_side = state.get("user_side", "Unknown")

    # 取审核模式, 默认 "AI_AUTO"(完全 LLM 自动审核)
    review_mode = state.get("review_mode", "AI_AUTO")

    # 取自定义规则列表, 默认空列表
    custom_rules = state.get("custom_rules", [])

    # 构造自定义规则提示文本, 仅在 CUSTOM_RULES 模式且有规则时启用
    rules_hint = ""
    # 若模式为 CUSTOM_RULES 且 custom_rules 非空, 则将规则格式化为提示文本
    if review_mode == "CUSTOM_RULES" and custom_rules:
        # 使用生成器表达式 + join 将规则列表转为 "- 规则1\n- 规则2" 格式
        # 前缀 "\n用户自定义审核规则:\n" 使其在 prompt 中作为独立段落呈现
        rules_hint = f"\n用户自定义审核规则:\n" + "\n".join(f"- {r}" for r in custom_rules)

    # 构造审核 prompt, 使用 f-string 嵌入合同类型、用户立场、自定义规则、合同文本
    # prompt 设计要点:
    #   (1) 角色定位"专业合同审核律师", 提升审核专业性;
    #   (2) 明示审核立场(从{user_side}方立场), 让风险判断有偏向性;
    #   (3) 列出 6 大审核要点, 引导 LLM 系统性审查;
    #   (4) 注入自定义规则(rules_hint), 实现用户定制化审核;
    #   (5) 提供 JSON schema, 强约束输出结构;
    #   (6) severity 用 critical/high/medium/low 标准化, 便于后续聚合
    prompt = f"""你是一个专业合同审核律师。请从{user_side}方立场审核以下{contract_type}合同, 识别商业条款风险。

审核要点:
1. 权利义务是否对等
2. 付款条件是否合理
3. 违约责任是否对己方不利
4. 知识产权/保密/竞业限制条款
5. 争议解决条款
6. 合同解除/终止条件
{rules_hint}

请返回JSON数组, 每个风险项包含:
{{
  "clause": "相关条款",
  "risk_type": "风险类型(商业/法律/操作)",
  "severity": "critical/high/medium/low",
  "description": "风险描述",
  "suggestion": "修改建议",
  "legal_basis": "法律依据(如《民法典》第585条)"
}}

合同文本:
{doc_text}

只输出JSON数组, 不要解释。如无风险返回 []"""

    # 使用 try-except 包裹 LLM 调用与 JSON 解析
    try:
        # 调用 LLM 进行合同审核
        resp = my_llm.invoke([HumanMessage(content=prompt)])

        # 取 LLM 输出文本并去除首尾空白
        content = resp.content.strip()

        # 代码块剥离: 若 LLM 用 ``` 包裹输出, 提取 "[" 到 "]" 之间的 JSON 数组
        # 注意: 本节点提取的是 JSON 数组(以 [ 开头), 与数值抽取节点(以 { 开头)不同
        if "```" in content:
            # find 返回第一个 "[" 的索引
            start = content.find("[")
            # rfind 返回最后一个 "]" 的索引, +1 是切片右开
            end = content.rfind("]") + 1
            # 仅在 start 有效时切片
            content = content[start:end] if start >= 0 else content

        # 将 JSON 数组字符串解析为 Python 列表
        risks = json.loads(content)

        # 类型校验: 确保 risks 是列表
        # 若 LLM 返回的是单个对象(字典)而非数组, 则视为无风险(空列表)
        # 这避免了后续聚合节点因类型不符而报错
        if not isinstance(risks, list):
            risks = []

        # 将风险项列表写入状态字典
        state["contract_risk_items"] = risks
    # 捕获所有异常
    except Exception as e:
        # 打印警告日志
        print(f"⚠️ 合同审核失败: {e}")
        # 异常时写入空列表, 保证下游节点(如 risk_aggregate_node)不会因字段缺失而报错
        state["contract_risk_items"] = []

    # 打印节点完成日志, 显示识别出的风险项数量
    print(f"完成合同AI审核: {len(state.get('contract_risk_items', []))} 个风险项")

    # 返回更新后的状态字典
    return state


# 模块自测入口: 直接运行本文件时执行, 验证合同审核逻辑
if __name__ == "__main__":
    # 构造测试状态: 提供合同文本与用户立场
    s = AgentState(doc_text="甲方购买乙方电脑, 违约金每日千分之五", user_side="A")
    # 调用节点, 以格式化 JSON 打印风险项(ensure_ascii=False 保留中文, indent=2 缩进美化)
    print(json.dumps(contract_ai_review_node(s).get("contract_risk_items"), ensure_ascii=False, indent=2))
