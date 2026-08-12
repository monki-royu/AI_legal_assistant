"""N7 风险聚合节点: 合并三路风险, 评分, 确定风险等级"""
# 📜 代码文字逻辑解析
# 本文件是 AI 法律助理(LangGraph 多智能体系统)中的"风险聚合节点", 对应业务流程的 N7 环节。
# 其核心职责是: 汇总三路风险检测的结果(合同审核/合规审查/数值校验), 进行字段统一、去重、
# 评分、定级, 最终输出综合风险评分(overall_risk_score)、风险等级(risk_level)、
# 合并后的风险清单(merged_risk_items)、是否需要律师复核(need_lawyer_review)等关键字段。
# 该节点是合同审核链路的"汇总中枢", 不调用 LLM, 纯本地计算, 性能高且结果可解释。
# 评分采用"扣分制": 基础 100 分, 每个风险按严重程度(critical/high/medium/low)扣分,
# 最终分数越高表示越安全。等级划分: Low(>=81)/Medium(61-80)/High(<60),
# 与 XMind 主架构设计一致。特殊规则: 若存在 critical 级合规风险, 等级不得为 Low(强制降级)。


# 从同包导入 AgentState 类型, 作为节点函数的类型注解
from __004__langgraph_more_nodes.agent_state import AgentState

# 定义严重程度到权重的映射字典(常量)
# 用于风险评分时的扣分计算: 权重越高, 该级别的风险扣分越多
# critical(致命): 100, high(高): 70, medium(中): 40, low(低): 10
# 这些权重值是经验设定, 可根据业务需求调整
SEVERITY_WEIGHT = {
    "critical": 100,
    "high": 70,
    "medium": 40,
    "low": 10,
}


def risk_aggregate_node(state: AgentState):
    """
    风险聚合节点函数: 合并三路风险项, 计算综合评分与等级, 写入多个状态字段。

    作用:
        (1) 读取三路风险项(contract_risk_items/compliance_risk_items/numeric_risk_items),
            统一字段结构(source/clause/severity/description/suggestion/legal_basis);
        (2) 按 description 前 50 字去重, 避免 LLM 与规则引擎重复报告同一风险;
        (3) 采用扣分制计算综合风险评分(0-100, 越高越安全);
        (4) 按阈值划分风险等级(Low/Medium/High);
        (5) 特殊规则: 存在 critical 风险时强制降级, 不得为 Low;
        (6) 输出 need_lawyer_review 标志(Medium/High 时需律师复核)。

    参数:
        state (AgentState): LangGraph 共享状态字典。读取字段:
                            - contract_risk_items (List[Dict], 可选): 合同审核风险项
                            - compliance_risk_items (List[Dict], 可选): 合规审查风险项
                            - numeric_risk_items (List[Dict], 可选): 数值校验风险项
                            写入字段:
                            - merged_risk_items (List[Dict]): 合并去重后的风险项
                            - overall_risk_score (float): 综合风险评分(0-100)
                            - risk_level (str): 风险等级(Low/Medium/High)
                            - need_lawyer_review (bool): 是否需要律师复核

    返回值:
        AgentState: 更新后的状态字典, 包含上述 4 个写入字段。

    可迁移性说明:
        本节点的"多源聚合 + 去重 + 扣分制评分 + 等级划分"架构可迁移到任何多维度风险评估场景,
        例如: 信用评分、安全审计、质量评估等。
        扣分制权重(SEVERITY_WEIGHT)与等级阈值可根据业务调整。
        "critical 强制降级"的兜底逻辑是合规场景的重要保障, 推荐保留。
    """
    # 打印节点开始日志
    print("开始风险聚合")

    # 从状态字典中读取三路风险项, 默认空列表
    contract_risks = state.get("contract_risk_items", [])
    compliance_risks = state.get("compliance_risk_items", [])
    numeric_risks = state.get("numeric_risk_items", [])

    # all_risks 列表用于收集统一字段结构后的所有风险项
    all_risks = []

    # 第一路: 合同审核风险项字段统一
    for r in contract_risks:
        all_risks.append({
            # source 标识风险来源, 便于在报告中分类展示
            "source": "合同审核",
            # clause: 相关条款, 从原风险项取, 默认空字符串
            "clause": r.get("clause", ""),
            # severity: 严重程度, 默认 medium
            "severity": r.get("severity", "medium"),
            # description: 风险描述
            "description": r.get("description", ""),
            # suggestion: 修改建议, 优先取 suggestion, 其次取 remediation(兼容字段)
            "suggestion": r.get("suggestion", r.get("remediation", "")),
            # legal_basis: 法律依据
            "legal_basis": r.get("legal_basis", ""),
        })

    # 第二路: 合规审查风险项字段统一(优先级最高, 涉及法律合规)
    for r in compliance_risks:
        all_risks.append({
            "source": "合规审查",
            "clause": r.get("clause", ""),
            "severity": r.get("severity", "medium"),
            "description": r.get("description", ""),
            # 合规风险的整改建议字段名为 remediation
            "suggestion": r.get("remediation", ""),
            "legal_basis": r.get("legal_basis", ""),
        })

    # 第三路: 数值校验风险项字段统一
    for r in numeric_risks:
        all_risks.append({
            "source": "数值校验",
            # 数值风险的"条款"字段用 target_field 或 rule_id 替代(因数值校验不针对具体条款文本)
            "clause": r.get("target_field", r.get("rule_id", "")),
            "severity": r.get("severity", "medium"),
            "description": r.get("description", ""),
            # 数值校验无修改建议, 置空
            "suggestion": "",
            "legal_basis": r.get("legal_basis", ""),
        })

    # 去重阶段: 按 description 前 50 字作为去重键
    # 三路风险可能命中同一问题(如违约金过高, 合同审核与数值校验都会报), 需去重避免重复展示
    # seen 集合记录已出现过的描述键
    seen = set()
    # merged 列表存储去重后的风险项
    merged = []
    for r in all_risks:
        # 取 description 前 50 字作为去重键(截断避免长描述差异导致无法去重)
        key = r.get("description", "")[:50]
        # 若键已存在, 跳过该风险项(重复)
        if key in seen:
            continue
        # 新键, 加入集合与结果列表
        seen.add(key)
        merged.append(r)

    # 评分阶段: 计算综合风险评分(0-100, 越高越安全)
    if not merged:
        # 无风险时, 给予 95 分(留 5 分余量, 表示"未检出风险但不保证完美")
        overall_score = 95
    else:
        # 扣分制: 基础 100 分, 每个风险按严重程度扣分
        # penalty 累计扣分值
        penalty = 0
        for r in merged:
            # 取严重程度并转小写, 兼容大小写不一致
            sev = r.get("severity", "medium").lower()
            # 从权重表取对应权重, 默认 40(medium)
            weight = SEVERITY_WEIGHT.get(sev, 40)
            # 扣分 = 权重 * 系数(0.15)
            # 系数 0.15 是经验值, 控制"单个风险的扣分力度"
            # 例: critical 风险扣 15 分(100*0.15), low 风险扣 1.5 分(10*0.15)
            penalty += weight * 0.15
        # 最终分数 = max(10, 100 - penalty), 最低不低于 10 分(避免负分)
        # max(10, ...) 保证分数始终在合理范围
        overall_score = max(10, 100 - penalty)

    # 等级划分阶段: 按阈值将分数转为等级
    # 阈值与 XMind 主架构设计一致: >=81 Low / 61-80 Medium / <60 High
    if overall_score >= 81:
        risk_level = "Low"
    elif overall_score >= 61:
        risk_level = "Medium"
    else:
        risk_level = "High"

    # 特殊规则: 若存在 critical 级风险, 等级不得为 Low(强制降级为 Medium)
    # 这是合规场景的重要保障, 避免因扣分系数不足导致 critical 风险被低估
    # any() + 生成器表达式: 检查 merged 中是否存在 severity 为 critical 的风险项
    has_critical = any(r.get("severity", "").lower() == "critical" for r in merged)
    # 若有 critical 且当前等级为 Low, 强制降级
    if has_critical and risk_level == "Low":
        # 等级降为 Medium
        risk_level = "Medium"
        # 分数也限制在 78 以下(不超过 80, 确保落入 Medium 区间)
        # min(overall_score, 78) 取较小值, 避免分数与等级不一致
        overall_score = min(overall_score, 78)

    # 将聚合结果写入状态字典
    # merged_risk_items: 去重后的风险项列表
    state["merged_risk_items"] = merged
    # overall_risk_score: 综合评分, round(..., 1) 保留 1 位小数
    state["overall_risk_score"] = round(overall_score, 1)
    # risk_level: 风险等级
    state["risk_level"] = risk_level
    # need_lawyer_review: 是否需要律师复核, 仅 Low 等级为 False, Medium/High 均为 True
    state["need_lawyer_review"] = risk_level != "Low"

    # 打印节点完成日志, 显示风险数、评分、等级
    print(f"完成风险聚合: {len(merged)} 个风险, 评分{overall_score:.1f}, 等级{risk_level}")

    # 返回更新后的状态字典
    return state


# 模块自测入口: 直接运行本文件时执行, 验证风险聚合逻辑
if __name__ == "__main__":
    # 构造测试状态: 三路各提供一个风险项, 涵盖 high/critical/medium 三种严重程度
    s = AgentState(
        contract_risk_items=[{"severity": "high", "description": "违约金过高"}],
        compliance_risk_items=[{"severity": "critical", "description": "数据合规违规"}],
        numeric_risk_items=[{"severity": "medium", "description": "预付款超80%"}],
    )
    # 调用节点, 打印综合评分与等级
    r = risk_aggregate_node(s)
    print(f"score={r['overall_risk_score']}, level={r['risk_level']}")
