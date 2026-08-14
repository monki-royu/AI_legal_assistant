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
    风险聚合节点函数: 合并四路(合同/合规/数值/资信)风险项, 计算综合评分与等级。

    作用:
        (1) 读取四路风险项(contract_risk_items/compliance_risk_items/numeric_risk_items/
            credit_risk_items), 统一字段结构;
        (2) 资信风险额外处理: is_counterparty(用户对立方)=True 的风险增加扣分权重
            (×1.3), 因为"对方的资信问题直接影响我方能否收到钱/货";
        (3) 按 description 前 50 字去重, 避免重复报告同一风险;
        (4) 额外读取甲乙双方的综合资信分(credit_score), 作为全局加分/扣分项:
            任一方 < 60 分 → 额外扣分; 双方均 ≥ 90 分 → 小幅加分;
        (5) 采用扣分制计算综合风险评分(0-100, 越高越安全);
        (6) 按阈值划分风险等级(Low/Medium/High), 存在 critical 风险时强制不得为 Low;
        (7) 输出 need_lawyer_review 标志。

    参数:
        state (AgentState): 共享状态, 读取 4 路风险项 + party_a_credit_info/party_b_credit_info。

    返回值:
        AgentState: 写入 merged_risk_items/overall_risk_score/risk_level/need_lawyer_review。

    可迁移性说明:
        本节点的"多源聚合 + 立场加权 + 全局分修正 + 去重 + 扣分制评分 + 等级划分"
        架构可迁移到任何多维度风险评估场景, 例如供应链风控、信贷审批等。
    """
    # 打印节点开始日志
    print("开始风险聚合(含资信)")

    # 从状态字典中读取四路风险项, 默认空列表
    contract_risks = state.get("contract_risk_items", [])
    compliance_risks = state.get("compliance_risk_items", [])
    numeric_risks = state.get("numeric_risk_items", [])
    credit_risks = state.get("credit_risk_items", [])  # 第 4 路: 资信风险(credit_check_node 输出)

    # all_risks 列表用于收集统一字段结构后的所有风险项
    all_risks = []

    # 第一路: 合同审核风险项字段统一
    for r in contract_risks:
        all_risks.append({
            "source": "合同审核",
            "clause": r.get("clause", ""),
            "severity": r.get("severity", "medium"),
            "description": r.get("description", ""),
            "suggestion": r.get("suggestion", r.get("remediation", "")),
            "legal_basis": r.get("legal_basis", ""),
            # 合同/合规/数值三路: 无"对立方加权"概念, 设为 1.0 标准权重
            "_weight_multiplier": 1.0,
        })

    # 第二路: 合规审查风险项字段统一(优先级最高, 涉及法律合规)
    for r in compliance_risks:
        all_risks.append({
            "source": "合规审查",
            "clause": r.get("clause", ""),
            "severity": r.get("severity", "medium"),
            "description": r.get("description", ""),
            "suggestion": r.get("remediation", ""),
            "legal_basis": r.get("legal_basis", ""),
            "_weight_multiplier": 1.0,
        })

    # 第三路: 数值校验风险项字段统一
    for r in numeric_risks:
        all_risks.append({
            "source": "数值校验",
            "clause": r.get("target_field", r.get("rule_id", "")),
            "severity": r.get("severity", "medium"),
            "description": r.get("description", ""),
            "suggestion": "",
            "legal_basis": r.get("legal_basis", ""),
            "_weight_multiplier": 1.0,
        })

    # 第四路: 资信审查风险项字段统一(新增) —— 含对立方加权
    # 注意: 资信节点排在 party_identify 之后, 但 risk_aggregate 在新流程中也已
    # 移到 credit_check 之后(见 langgraph_main.py 中 N8->credit_check->N7),
    # 所以此处能拿到 credit_risk_items。
    for r in credit_risks:
        # 对立方加权: 如果该资信风险来自签约对手方, 风险与我方切身利益更相关
        # 例: 用户是甲方, 乙方失信 = 我方拿不到货/款, 权重 ×1.3
        is_cp = bool(r.get("is_counterparty", False))
        multiplier = 1.3 if is_cp else 1.0
        all_risks.append({
            "source": "资信审查",
            "clause": r.get("clause", ""),
            "severity": r.get("severity", "medium"),
            "description": r.get("description", ""),
            "suggestion": r.get("suggestion", ""),
            "legal_basis": r.get("legal_basis", ""),
            # 保留两个展示字段, 便于最终报告在"资信"分类下显示甲方/乙方标签
            "party_label": r.get("party_label", ""),
            "credit_category": r.get("credit_category", ""),
            # 权重倍率: 1.3(对方) / 1.0(自身 / Unknown)
            "_weight_multiplier": multiplier,
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
    # ---------- 1) 基础分(无风险 95, 否则 100 起步扣分) ----------
    if not merged:
        overall_score = 95.0
    else:
        penalty = 0.0
        for r in merged:
            sev = r.get("severity", "medium").lower()
            weight = SEVERITY_WEIGHT.get(sev, 40)
            # 基础扣分 = 严重程度权重 × 系数(0.15)
            base_penalty = weight * 0.15
            # 乘风险项自身的权重倍率: 对立方资信风险 ×1.3, 其它 ×1.0
            # 从 r 中取 _weight_multiplier, 缺失则用默认 1.0(保证向前兼容)
            multiplier = float(r.get("_weight_multiplier", 1.0))
            penalty += base_penalty * multiplier
        overall_score = max(10.0, 100.0 - penalty)

    # ---------- 2) 全局资信分修正(基于甲乙双方 credit_score 做额外微调) ----------
    # 这能体现"即使条款写得再完美, 对方是老赖也很难全身而退"的现实情况
    extra_mod = 0.0  # 正=加分, 负=扣分
    a_score = None
    b_score = None
    a_info = state.get("party_a_credit_info") or {}
    b_info = state.get("party_b_credit_info") or {}
    if isinstance(a_info, dict) and isinstance(a_info.get("credit_score"), (int, float)):
        a_score = float(a_info["credit_score"])
    if isinstance(b_info, dict) and isinstance(b_info.get("credit_score"), (int, float)):
        b_score = float(b_info["credit_score"])
    # (a) 任一方资信分 < 60 -> 每差 10 分扣 2 分, 上限扣 10 分
    for s in [a_score, b_score]:
        if s is not None and s < 60:
            gap = (60 - s) / 10.0  # 差几个 10 分段
            extra_mod -= min(10.0, gap * 2.0)
    # (b) 双方都有分且均 >= 90 -> 加 3 分(优质对手组合, 商业环境好)
    if a_score is not None and b_score is not None and a_score >= 90 and b_score >= 90:
        extra_mod += 3.0
    # (c) 应用修正, 并钳制到 10~100
    if extra_mod != 0.0:
        overall_score = max(10.0, min(100.0, overall_score + extra_mod))

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
