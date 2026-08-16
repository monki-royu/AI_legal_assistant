"""【文件作用】合同审核 AI 节点 ── 从用户立场审核合同商业条款，生成结构化风险项列表
【逻辑】本文件是 AI 法律助理(LangGraph 多智能体系统)中的合同审核节点，对应业务流程的 N5a 环节。
    核心流程：
    1. 从 【state】 中读取合同文本(doc_text)、合同类型(contract_type)、用户立场(user_side)、
       审核模式(review_mode)、自定义规则(custom_rules) 等字段
    2. 支持两种审核模式：
       - 【AI_AUTO】模式：完全由 LLM 自动审核，注入标准 prompt
       - 【CUSTOM_RULES】模式：将用户提供的自定义规则注入 prompt，引导 LLM 重点关注
    3. 调用 LLM 以"专业合同审核律师"角色进行审核，要求返回 JSON 数组格式的风险项
    4. 对 LLM 输出进行【代码块剥离】+【JSON 解析】+【类型校验】三重清洗
    5. 若任一环节异常，降级为空列表写入 state["contract_risk_items"]，保证下游不报错
    6. 输出包含 clause / risk_type / severity / description / suggestion / legal_basis 六个字段
"""

# ============================================================
# 📦 导入模块
# ============================================================

# 导入 json 模块，用于将 LLM 返回的 JSON 数组字符串解析为 Python 列表
# 【场景】LLM 以文本形式返回 JSON → json.loads() 将其转为 List[Dict]
import json

# 从 langchain_core.messages 导入 HumanMessage（【人类消息】类）
# 用于构造对 LLM 的"用户消息"输入
from langchain_core.messages import HumanMessage

# 从项目共享模块 common.llm 导入统一的 LLM 实例 my_llm
# 【设计意图】所有节点共用统一 LLM 实例，便于配置管理与日志追踪
from common.llm import my_llm

# 从同包导入 AgentState（【代理状态】类型），作为节点函数的类型注解
# AgentState 是 TypedDict，定义所有跨节点字段的类型约束
from __004__langgraph_more_nodes.agent_state import AgentState


def contract_ai_review_node(state: AgentState):
    """
    【功能】合同审核 AI 节点函数：从用户立场审核合同商业条款，生成风险项列表
    【参数】state (AgentState)：LangGraph 共享状态字典，读取以下字段：
                - doc_text (str, 可选)【合同全文】：从 state 读取，取前 5000 字
                - contract_type (str, 可选)【合同类型】：默认 "其他"
                - user_side (str, 可选)【用户立场】：A=甲方/B=乙方/Unknown=未知，默认 "Unknown"
                - review_mode (str, 可选)【审核模式】：AI_AUTO 或 CUSTOM_RULES，默认 "AI_AUTO"
                - custom_rules (List[str], 可选)【自定义规则】：仅 CUSTOM_RULES 模式使用
            写入字段：
                - contract_risk_items (List[Dict])【合同风险项列表】：六个字段的结构化风险项
    【返回值】AgentState：更新后的状态字典，必含 "contract_risk_items" 字段（可能为空列表）
    【逻辑】① 读取 state 中的输入字段 ② 根据审核模式构造不同 prompt
            ③ 调用 LLM 审核 ④ 剥离代码块标记 ⑤ 解析 JSON 数组 ⑥ 类型校验
            ⑦ 写入 state ⑧ 异常时写入空列表降级
    【可迁移性】本节点的"立场化审核 + 结构化风险输出 + 多模式支持"模式可迁移到任何专业审核场景，
            如招投标文件审核、学术论文审核、代码安全审核等。修改 prompt 中的审核要点与
            输出 schema 即可适配新业务。"自定义规则注入"提供了良好扩展性。
    """
    # 【步骤1】打印节点开始日志，标记进入此节点
    print("--- 开始合同 AI 审核 ---")

    # ============================================================
    # 【步骤2】从 state 中读取所有输入字段（带默认值安全读取）
    # ============================================================

    # 读取合同全文并切片取前 5000 字符
    # 【为什么取 5000 字？】平衡覆盖度与 LLM 成本：5000 字已覆盖合同核心条款区域
    # state.get("doc_text", "") → 若不存在该键则返回空字符串
    doc_text = state.get("doc_text", "")[:5000]  # 合同文本（截断）

    # 读取合同类型，默认 "其他"（若上游分类节点未执行或失败）
    contract_type = state.get("contract_type", "其他")  # 合同类型标识

    # 读取用户立场，默认 "Unknown"
    # 【上游】由 party_identify_node（签约方识别节点）识别甲乙双方后写入
    # 该值注入 prompt 后，LLM 会从指定立场评估条款利弊
    user_side = state.get("user_side", "Unknown")  # 用户立场：A/B/Unknown

    # 读取审核模式，默认 "AI_AUTO"
    # 【AI_AUTO】完全由 LLM 自动审核；【CUSTOM_RULES】注入用户自定义规则
    review_mode = state.get("review_mode", "AI_AUTO")  # 审核模式

    # 读取自定义规则列表，默认空列表
    # 仅在 CUSTOM_RULES 模式下使用
    custom_rules = state.get("custom_rules", [])  # 用户自定义审核规则列表

    # ============================================================
    # 【步骤3】构造自定义规则提示文本（仅 CUSTOM_RULES 模式启用）
    # ============================================================

    # 初始化自定义规则提示文本为空字符串
    rules_hint = ""
    # 仅在模式为 CUSTOM_RULES 且自定义规则列表非空时构建提示
    if review_mode == "CUSTOM_RULES" and custom_rules:
        # 使用生成器表达式 + join() 将规则列表格式化为 markdown 列表样式
        # f"- {r}" 将每条规则转为 "- 规则内容" 格式
        # "\n".join(...) 用换行符连接所有规则行
        # 前缀 "\n用户自定义审核规则:\n" 使其在 prompt 中作为独立段落呈现
        rules_hint = f"\n用户自定义审核规则:\n" + "\n".join(f"- {r}" for r in custom_rules)

    # ============================================================
    # 【步骤4】构造审核 prompt（核心设计）
    # ============================================================
    # 使用 f-string 嵌入合同类型、用户立场、自定义规则、合同文本
    # 【Prompt 设计要点】
    #   ①【角色定位】"专业合同审核律师" → 引导 LLM 进入专业审核语境
    #   ②【立场注入】"从{user_side}方立场审核" → 让风险判断有偏向性
    #   ③【审核要点】列出 6 大审核方面 → 引导 LLM 系统性全面审查
    #   ④【规则注入】{rules_hint} → 实现用户定制化审核（仅 CUSTOM_RULES 模式）
    #   ⑤【输出 Schema】提供完整 JSON 字段定义 → 强约束输出结构
    #   ⑥【severity 标准化】critical/high/medium/low → 便于后续聚合与评分
    #   ⑦【输出约束】"只输出JSON数组，不要解释。如无风险返回 []"
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

    # ============================================================
    # 【步骤5】调用 LLM + 解析输出（try-except 包裹，全程容错）
    # ============================================================
    try:
        # 调用 LLM 的 invoke 方法，传入包含 HumanMessage 的消息列表
        # resp 是 AIMessage 类型，resp.content 为生成的文本
        resp = my_llm.invoke([HumanMessage(content=prompt)])

        # 取 LLM 输出文本并去除首尾空白字符
        content = resp.content.strip()  # 清洗后的 LLM 输出字符串

        # ============================================================
        # 【步骤6】代码块剥离：处理 LLM 用 ``` 包裹输出的情况
        # ============================================================
        # 【问题】LLM 有时会用 ```json ... ``` 包裹 JSON 输出
        # 【解决】检测 "```" 标记，提取其中的 JSON 数组部分
        # 【注意】本节点提取的是 JSON 数组（以 [ 开头），区别于数值抽取节点（以 { 开头）
        if "```" in content:
            # find("["): 查找第一个 "[" 的索引位置，即 JSON 数组的起始位置
            start = content.find("[")
            # rfind("]"): 查找最后一个 "]" 的索引位置，+1 是因为切片右边界不包含
            end = content.rfind("]") + 1
            # 仅当 start >= 0（找到了 "["）时才切片，否则保留原内容
            content = content[start:end] if start >= 0 else content

        # ============================================================
        # 【步骤7】JSON 解析：将字符串解析为 Python 列表
        # ============================================================
        # json.loads() 将 JSON 数组字符串解析为 Python 的 list 对象
        # 【风险】若 LLM 输出不是合法 JSON，会抛出 json.JSONDecodeError
        risks = json.loads(content)  # 解析后的风险项列表

        # ============================================================
        # 【步骤8】类型校验：确保解析结果为 list 类型
        # ============================================================
        # 【防御场景】LLM 可能返回单个字典对象 { "clause": ... } 而非数组
        # 此时 isinstance(risks, list) 为 False，我们将其置为空列表
        # 这避免了后续聚合节点因类型不符（期望 list 拿到 dict）而报错
        if not isinstance(risks, list):
            risks = []  # 类型不符合预期时降级为空列表

        # 将最终的风险项列表写入 state["contract_risk_items"]
        # 下游节点（如 risk_aggregate_node）会读取此字段
        state["contract_risk_items"] = risks

    # ============================================================
    # 【步骤9】异常处理：任何异常都降级为空列表
    # ============================================================
    except Exception as e:
        # 打印警告日志，包含异常信息（如 JSON 解析失败、LLM 网络错误等）
        print(f"⚠️ 合同审核失败: {e}")
        # 异常时写入空列表，保证下游节点不会因字段缺失或类型错误而报错
        # 【设计原则】宁可让用户看到空结果，也不能让整个流程崩溃
        state["contract_risk_items"] = []

    # ============================================================
    # 【步骤10】打印完成日志并返回
    # ============================================================
    # 打印节点完成日志，显示识别出的风险项数量
    # len(state.get('contract_risk_items', [])) 安全获取列表长度
    print(f"--- 完成合同 AI 审核: {len(state.get('contract_risk_items', []))} 个风险项 ---")

    # 返回更新后的状态字典
    # LangGraph 会将返回的字典合并到全局状态中
    return state


# ============================================================
# 🧪 模块自测入口（仅在直接运行本文件时执行）
# ============================================================
if __name__ == "__main__":
    # 构造测试状态：提供合同文本与用户立场
    # doc_text = "甲方购买乙方电脑, 违约金每日千分之五"
    # user_side = "A"（表示以甲方立场审核）
    s = AgentState(doc_text="甲方购买乙方电脑, 违约金每日千分之五", user_side="A")
    # 调用审核节点，获取 contract_risk_items 并格式化打印
    # json.dumps(..., ensure_ascii=False 保留中文, indent=2 缩进美化)
    print(json.dumps(contract_ai_review_node(s).get("contract_risk_items"), ensure_ascii=False, indent=2))