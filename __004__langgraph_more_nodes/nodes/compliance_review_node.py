"""N5b 合规审查节点: 监管/合规视角审查, 生成合规风险项"""
# 📜 代码文字逻辑解析
# 本文件是 AI 法律助理(LangGraph 多智能体系统)中的"合规审查节点", 对应业务流程的 N5b 环节。
# 其核心职责是: 以企业合规审查专家的视角, 从法律法规合规角度审查合同/文档, 识别合规风险项,
# 以 JSON 数组形式写入 state["compliance_risk_items"]。该字段是三路风险检测(合同审核/合规审查/
# 数值校验)之一, 最终会被 risk_aggregate_node 聚合去重。
# 合规审查关注 7 大重点: 法律强制性规定、数据合规(个保法/数安法)、反垄断/反不正当竞争、
# 税务合规(金税四期)、劳动合规、行业准入与资质、政府采购合规。
# 与合同审核(N5a)的区别在于: 合同审核关注商业条款合理性(立场化), 合规审查关注法律合规性(客观性)。
# 节点采用与 contract_ai_review_node 相同的 "LLM 审核 + JSON 数组解析 + 代码块剥离 + 类型校验 +
# 异常降级" 策略, 保证输出始终为列表。为控制成本, 仅截取文档前 5000 字符。


# 导入 json 模块, 用于解析 LLM 返回的 JSON 数组字符串
import json

# 从 langchain_core.messages 导入 HumanMessage, 用于构造 LLM 的用户消息
from langchain_core.messages import HumanMessage

# 从项目共享模块 common.llm 导入统一的 LLM 实例 my_llm
from common.llm import my_llm

# 从同包导入 AgentState 类型, 作为节点函数的类型注解
from __004__langgraph_more_nodes.agent_state import AgentState


def compliance_review_node(state: AgentState):
    """
    合规审查节点函数: 从法律合规角度审查文档, 生成合规风险项, 写入 state["compliance_risk_items"]。

    作用:
        读取合同/文档全文(前 5000 字)与合同类型, 以"企业合规审查专家"角色调用 LLM 识别合规风险。
        审查覆盖 7 大合规领域: 法律强制性规定、数据合规、反垄断、税务合规、劳动合规、
        行业准入、政府采购。每个合规风险项包含:
        条款(clause)/合规领域(compliance_area)/严重程度(severity)/描述(description)/
        法律依据(legal_basis)/整改建议(remediation)。

    参数:
        state (AgentState): LangGraph 共享状态字典。读取字段:
                            - doc_text (str, 可选): 文档全文(取前 5000 字)
                            - contract_type (str, 可选): 合同类型, 默认 "其他"
                            写入字段:
                            - compliance_risk_items (List[Dict]): 合规风险项列表

    返回值:
        AgentState: 更新后的状态字典, 必含 "compliance_risk_items" 字段(可能为空列表)。

    可迁移性说明:
        本节点的"合规视角审查 + 多领域覆盖"模式可迁移到任何合规审查场景,
        例如: 上市公司信息披露合规、医疗广告合规、跨境数据传输合规等。
        通过修改 prompt 中的审查重点与合规领域分类即可适配新业务。
        与 contract_ai_review_node 共享相同的容错策略, 保持代码风格一致性。
    """
    # 打印节点开始日志
    print("开始合规审查")

    # 从状态字典中取出文档全文, 并切片取前 5000 字符
    # 与合同审核节点保持相同的截取长度, 便于对比与一致性
    doc_text = state.get("doc_text", "")[:5000]

    # 取合同类型, 默认 "其他", 注入 prompt 让 LLM 知晓文档类型
    contract_type = state.get("contract_type", "其他")

    # 构造合规审查 prompt, 使用 f-string 嵌入合同类型与文档文本
    # prompt 设计要点:
    #   (1) 角色定位"企业合规审查专家", 侧重合规而非商业;
    #   (2) 列出 7 大合规审查重点, 覆盖主要合规风险维度;
    #   (3) compliance_area 字段标准化(数据/税务/劳动/反垄断/行业准入), 便于分类统计;
    #   (4) remediation(整改建议)字段区别于合同审核的 suggestion, 强调"如何整改以达到合规";
    #   (5) severity 同样用 critical/high/medium/low, 与合同审核保持一致, 便于聚合
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

    # 使用 try-except 包裹 LLM 调用与 JSON 解析
    try:
        # 调用 LLM 进行合规审查
        resp = my_llm.invoke([HumanMessage(content=prompt)])

        # 取 LLM 输出文本并去除首尾空白
        content = resp.content.strip()

        # 代码块剥离: 若 LLM 用 ``` 包裹输出, 提取 "[" 到 "]" 之间的 JSON 数组
        # 逻辑与 contract_ai_review_node 完全一致, 处理 JSON 数组格式
        if "```" in content:
            # find 返回第一个 "[" 的索引
            start = content.find("[")
            # rfind 返回最后一个 "]" 的索引, +1 是切片右开
            end = content.rfind("]") + 1
            # 仅在 start 有效时切片
            content = content[start:end] if start >= 0 else content

        # 将 JSON 数组字符串解析为 Python 列表
        risks = json.loads(content)

        # 类型校验: 确保 risks 是列表, 否则置空
        # 防御 LLM 返回单个对象而非数组的情况
        if not isinstance(risks, list):
            risks = []

        # 将合规风险项列表写入状态字典
        state["compliance_risk_items"] = risks
    # 捕获所有异常
    except Exception as e:
        # 打印警告日志
        print(f"⚠️ 合规审查失败: {e}")
        # 异常时写入空列表, 保证下游聚合节点不会因字段缺失而报错
        state["compliance_risk_items"] = []

    # 打印节点完成日志, 显示识别出的合规风险项数量
    print(f"完成合规审查: {len(state.get('compliance_risk_items', []))} 个风险项")

    # 返回更新后的状态字典
    return state


# 模块自测入口: 直接运行本文件时执行, 验证合规审查逻辑
if __name__ == "__main__":
    # 构造测试状态: 提供一段涉及数据合规问题的文档
    s = AgentState(doc_text="甲方收集用户个人信息用于营销, 不告知用户")
    # 调用节点, 以格式化 JSON 打印合规风险项
    print(json.dumps(compliance_review_node(s).get("compliance_risk_items"), ensure_ascii=False, indent=2))
