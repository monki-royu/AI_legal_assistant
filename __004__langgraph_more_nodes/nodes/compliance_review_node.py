"""【文件作用】合规审查节点 ── 从法律法规合规角度审查文档，生成合规风险项
【逻辑】本文件是 AI 法律助理(LangGraph 多智能体系统)中的合规审查节点，对应业务流程的 N5b 环节。
    核心流程：
    1. 从 【state】 中读取文档文本(doc_text)和合同类型(contract_type)
    2. 以"企业合规审查专家"角色调用 LLM，对文档进行 7 大合规领域审查：
       ① 法律强制性规定违反 ② 数据合规（个保法/数安法）③ 反垄断/反不正当竞争
       ④ 税务合规（金税四期）⑤ 劳动合规 ⑥ 行业准入与资质 ⑦ 政府采购合规
    3. 要求 LLM 返回 JSON 数组格式的合规风险项
    4. 对 LLM 输出进行【代码块剥离】+【JSON 解析】+【类型校验】三重清洗
    5. 若任一环节异常，降级为空列表写入 state["compliance_risk_items"]
    6. 输出包含 clause / compliance_area / severity / description / legal_basis / remediation 六个字段
    7. 与 contract_ai_review_node（N5a）共享相同容错策略，保持代码风格一致
"""

# ============================================================
# 📦 导入模块
# ============================================================

# 导入 json 模块，用于将 LLM 返回的 JSON 数组字符串解析为 Python 列表
import json

# 从 langchain_core.messages 导入 HumanMessage（【人类消息】类）
# 用于构造对 LLM 的用户消息输入
from langchain_core.messages import HumanMessage

# 从项目共享模块 common.llm 导入统一的 LLM 实例 my_llm
from common.llm import my_llm

# 从同包导入 AgentState（【代理状态】类型），作为节点函数的类型注解
from __004__langgraph_more_nodes.agent_state import AgentState


def compliance_review_node(state: AgentState):
    """
    【功能】合规审查节点函数：从法律合规角度审查文档，生成合规风险项列表
    【参数】state (AgentState)：LangGraph 共享状态字典，读取以下字段：
                - doc_text (str, 可选)【文档全文】：从 state 读取，取前 5000 字
                - contract_type (str, 可选)【合同类型】：默认 "其他"
            写入字段：
                - compliance_risk_items (List[Dict])【合规风险项列表】：六个字段的结构化合规风险项
    【返回值】AgentState：更新后的状态字典，必含 "compliance_risk_items" 字段（可能为空列表）
    【逻辑】① 读取 state 中的输入字段 ② 构造合规审查 prompt（7 大重点）
            ③ 调用 LLM 审查 ④ 剥离代码块标记 ⑤ 解析 JSON 数组 ⑥ 类型校验
            ⑦ 写入 state ⑧ 异常时写入空列表降级
    【与 N5a 合同审核的区别】
            - 【立场】合同审核是立场化（从 user_side 出发），合规审查是客观法律合规
            - 【字段】合同审核用 suggestion（修改建议），合规审查用 remediation（整改建议）
            - 【领域】合同审核关注商业条款，合规审查关注法律法规合规
    【可迁移性】本节点的"合规视角审查 + 多领域覆盖"模式可迁移到任何合规审查场景，
            如上市公司信息披露合规、医疗广告合规、跨境数据传输合规等。
    """
    # 【步骤1】打印节点开始日志，标记进入此节点
    print("--- 开始合规审查 ---")

    # ============================================================
    # 【步骤2】从 state 中读取输入字段
    # ============================================================

    # 读取文档全文并切片取前 5000 字符
    # 【为什么取 5000 字？】与合同审核节点保持一致，平衡覆盖度与 LLM 成本
    # state.get("doc_text", "") → 若键不存在则返回空字符串
    doc_text = state.get("doc_text", "")[:5000]  # 文档文本（截断）

    # 读取合同类型，默认 "其他"
    # 注入 prompt 让 LLM 知晓文档类型，便于针对性审查
    contract_type = state.get("contract_type", "其他")  # 合同/文档类型标识

    # ============================================================
    # 【步骤3】构造合规审查 prompt（核心设计）
    # ============================================================
    # 使用 f-string 嵌入合同类型与文档文本
    # 【Prompt 设计要点】
    #   ①【角色定位】"企业合规审查专家" → 侧重法规合规而非商业合理性
    #   ②【审查重点】列出 7 大合规领域 → 覆盖主要合规风险维度
    #   ③【compliance_area 字段】标准化为数据/税务/劳动/反垄断/行业准入 → 便于分类统计
    #   ④【remediation 字段】区别于合同审核的 suggestion，强调"如何整改以达到合规"
    #   ⑤【severity 标准化】critical/high/medium/low → 与合同审核保持一致，便于后续聚合
    #   ⑥【输出约束】"只输出JSON数组，不要解释。如无风险返回 []"
    prompt = f"""你是一个企业合规审查专家。请从法律法规合规角度审查以下{contract_type}合同/文档。

合规审查重点:
1. 是否违反法律法规强制性规定
2. 数据合规(个人信息保护法/数据安全法)
3. 反垄断/反不正当竞争
4. 税务合规(金税四期)
5. 劳动合规(如涉及)
6. 行业准入与资质
7. 政府采购合规(如涉及)

请返回JSON数组, 每个合规风险项包含:
{{
  "clause": "相关条款",
  "compliance_area": "合规领域(数据/税务/劳动/反垄断/行业准入)",
  "severity": "critical/high/medium/low",
  "description": "合规风险描述",
  "legal_basis": "法律依据",
  "remediation": "整改建议"
}}

文档文本:
{doc_text}

只输出JSON数组, 不要解释。如无风险返回 []"""

    # ============================================================
    # 【步骤4】调用 LLM + 解析输出（try-except 包裹，全程容错）
    # ============================================================
    try:
        # 调用 LLM 的 invoke 方法，传入包含 HumanMessage 的消息列表
        # resp 是 AIMessage 类型，resp.content 为生成的文本
        resp = my_llm.invoke([HumanMessage(content=prompt)])

        # 取 LLM 输出文本并去除首尾空白字符
        content = resp.content.strip()  # 清洗后的 LLM 输出字符串

        # ============================================================
        # 【步骤5】代码块剥离：处理 LLM 用 ``` 包裹输出的情况
        # ============================================================
        # 逻辑与 contract_ai_review_node 完全一致，处理 JSON 数组格式
        if "```" in content:
            # find("["): 查找第一个 "[" 索引，即 JSON 数组起始位置
            start = content.find("[")
            # rfind("]"): 查找最后一个 "]" 索引，+1 为切片右边界
            end = content.rfind("]") + 1
            # 仅当 start >= 0（找到了 "["）时才切片，否则保留原内容
            content = content[start:end] if start >= 0 else content

        # ============================================================
        # 【步骤6】JSON 解析：将字符串解析为 Python 列表
        # ============================================================
        # json.loads() 将 JSON 数组字符串转为 Python list 对象
        # 若 LLM 输出不是合法 JSON，会抛出 json.JSONDecodeError 异常
        risks = json.loads(content)  # 解析后的合规风险项列表

        # ============================================================
        # 【步骤7】类型校验：确保解析结果为 list 类型
        # ============================================================
        # 防御 LLM 返回单个字典对象而非数组的情况
        if not isinstance(risks, list):
            risks = []  # 类型不符时降级为空列表

        # 将合规风险项列表写入 state["compliance_risk_items"]
        # 下游节点（如 risk_aggregate_node）会读取此字段参与聚合评分
        state["compliance_risk_items"] = risks

    # ============================================================
    # 【步骤8】异常处理：任何异常都降级为空列表
    # ============================================================
    except Exception as e:
        # 打印警告日志，包含异常信息
        print(f"⚠️ 合规审查失败: {e}")
        # 异常时写入空列表，保证下游聚合节点不会因字段缺失而报错
        state["compliance_risk_items"] = []

    # ============================================================
    # 【步骤9】打印完成日志并返回
    # ============================================================
    # 打印节点完成日志，显示识别出的合规风险项数量
    print(f"--- 完成合规审查: {len(state.get('compliance_risk_items', []))} 个风险项 ---")

    # 返回更新后的状态字典
    # LangGraph 会将返回的字典合并到全局状态中
    return state


# ============================================================
# 🧪 模块自测入口（仅在直接运行本文件时执行）
# ============================================================
if __name__ == "__main__":
    # 构造测试状态：提供一段涉及数据合规问题的文档
    # doc_text = "甲方收集用户个人信息用于营销, 不告知用户"
    s = AgentState(doc_text="甲方收集用户个人信息用于营销, 不告知用户")
    # 调用合规审查节点，获取 compliance_risk_items 并格式化打印
    # json.dumps(..., ensure_ascii=False 保留中文, indent=2 缩进美化)
    print(json.dumps(compliance_review_node(s).get("compliance_risk_items"), ensure_ascii=False, indent=2))