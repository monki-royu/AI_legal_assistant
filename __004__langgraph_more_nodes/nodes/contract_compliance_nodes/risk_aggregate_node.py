"""【文件作用】风险聚合节点 ── 合并（合同审核/合规审查大模型结果+数值校验结果）风险项，计算综合评分与风险等级
【逻辑】本文件是 AI 法律助理(LangGraph 多智能体系统)中的风险聚合节点，对应业务流程的 N7 环节。
    该节点是合同审核链路的"汇总中枢"，纯本地计算（不调用 LLM），性能高且结果可解释。
    核心流程：
    1. 【三路归并】从 state 读取三路风险项：
        ① post_conflict_risk_items（冲突消解后风险，来自 N5d，已合并合同与合规）
        ② numeric_risk_items（数值校验风险，来自 N5c）
        ③ credit_risk_items（资信审查风险，来自 credit_check_node）
    2. 【字段统一】将三路风险项统一为相同字段结构（source / clause / severity / description / suggestion / legal_basis）
    3. 【对立方加权】资信风险中，若 is_counterparty=True（来自签约对手方），权重倍率 ×1.3
    4. 【去重】按 description 前 50 字去重，避免三路风险命中同一问题重复报告
    5. 【扣分制评分】基础 100 分，每个风险按严重程度扣分（critical=100, high=70, medium=40, low=10），乘以系数 0.15 和权重倍率
    6. 【全局资信修正】读取甲乙双方 credit_score，任一方 < 60 扣分，双方均 ≥ 90 加分
    7. 【等级划分】≥81 → Low（低风险），61~80 → Medium（中风险），<60 → High（高风险）
    8. 【强制降级规则】存在 critical 级风险时，等级不得为 Low（强制降为 Medium）
    9. 输出 overall_risk_score / risk_level / merged_risk_items / need_lawyer_review
"""

# ============================================================
# 📦 导入模块
# ============================================================

# 从同包导入 AgentState（【代理状态】类型），作为节点函数的类型注解
from __004__langgraph_more_nodes.agent_state import AgentState


# ============================================================
# 📊 全局常量定义
# ============================================================

# 定义严重程度到【扣分权重】的映射字典（常量）
# 【设计意图】用于风险评分时的扣分计算：权重越高，该级别风险扣分越多
# critical（致命）   → 100 分（直接威胁合同有效性或导致重大损失）
# high（高）          → 70 分（显著不利条款，需重点关注）
# medium（中）        → 40 分（存在风险但可协商调整）
# low（低）           → 10 分（建议性改进，非必须修改）
# 这些权重值是经验设定，可根据业务风险偏好调整
SEVERITY_WEIGHT = {
    "critical": 100,  # 致命风险权重
    "high": 70,       # 高风险权重
    "medium": 40,     # 中风险权重
    "low": 10,        # 低风险权重
}


def risk_aggregate_node(state: AgentState):
    """
    【功能】风险聚合节点函数：合并三路（冲突消解/数值/资信）风险项，计算综合评分与等级
    【参数】state (AgentState)：LangGraph 共享状态字典。
            读取字段：
                - post_conflict_risk_items (List[Dict])【冲突消解后风险】：来自 N5d（已合并合同+合规）
                - numeric_risk_items (List[Dict])【数值校验风险项】：来自 N5c
                - credit_risk_items (List[Dict])【资信审查风险项】：来自 credit_check_node
                - party_a_credit_info (Dict)【甲方资信信息】：含 credit_score 等子字段
                - party_b_credit_info (Dict)【乙方资信信息】：含 credit_score 等子字段
            写入字段：
                - merged_risk_items (List[Dict])【合并去重后的风险项列表】
                - overall_risk_score (float)【综合风险评分】：0-100，越高越安全
                - risk_level (str)【风险等级】：Low / Medium / High
                - need_lawyer_review (bool)【是否需要律师复核】
    【返回值】AgentState：更新后的状态字典
    【逻辑】
        阶段一【字段统一】：将三路风险项统一为相同结构（source/clause/severity/description/...）
        阶段二【对立方加权】：资信风险中 is_counterparty=True 的项目权重 ×1.3
        阶段三【去重】：按 description 前 50 字去重
        阶段四【评分】：扣分制，基础分 100 或 95（无风险）
        阶段五【全局资信修正】：甲乙双方 credit_score 影响最终分数
        阶段六【等级划分】：按分数阈值划分等级，critical 强制降级
    【可迁移性】本节点的"多源聚合 + 立场加权 + 全局修正 + 去重 + 扣分制评分 + 等级划分"
            架构可迁移到任何多维度风险评估场景，如供应链风控、信贷审批等。
    【架构变更说明】
        v2.0: 不再直接从 state 读取 contract_risk_items 和 compliance_risk_items，
              而是读取已由 conflict_resolution_node 合并后的 post_conflict_risk_items。
              这确保了"合规优先"的冲突消解规则在最终评分中得到贯彻。
    """
    # 【步骤1】打印节点开始日志
    print("--- 开始风险聚合(含资信) ---")

    # ============================================================
    # 阶段一：从 state 读取三路风险项
    # ============================================================

    # 第一路：冲突消解后的风险项（来自 conflict_resolution_node / N5d）
    # 已包含合同审核 + 合规审查的合并结果，source 字段标识来源
    # 合规审查单路 fallback: 当 post_conflict_risk_items 为空时（跳过 conflict_resolution）,
    # 直接消费 compliance_risk_items (合规审查已写入)
    post_conflict_risks = state.get("post_conflict_risk_items", [])
    if not post_conflict_risks:
        compliance_only = state.get("compliance_risk_items", []) or []
        if compliance_only:
            print("  [risk_aggregate] post_conflict 为空, fallback 直接消费 compliance_risk_items")
            post_conflict_risks = compliance_only

    # 第二路：数值校验风险项（来自 numeric_validate_node / N5c）
    numeric_risks = state.get("numeric_risk_items", [])  # 数值校验风险列表

    # 第三路：资信审查风险项（来自 credit_check_node）
    credit_risks = state.get("credit_risk_items", [])  # 资信审查风险列表

    # ============================================================
    # 阶段二：三路风险项字段统一
    # ============================================================

    # all_risks 列表用于收集字段统一后的所有风险项
    all_risks = []  # 统一结构后的全部风险项

    # --- 第一路：冲突消解后风险项字段统一 ---
    # 这些项已包含合同审核和合规审查的合并结果
    # source 字段标识：compliance+contract / compliance_only / contract_only
    for r in post_conflict_risks:
        all_risks.append({
            "source": r.get("source", "冲突消解"),             # 【来源】标识来源
            "clause": r.get("clause", ""),                    # 【相关条款】
            "severity": r.get("severity", "medium"),          # 【严重程度】
            "description": r.get("description", ""),          # 【风险描述】
            "suggestion": r.get("remediation") or r.get("suggestion", ""),  # 【修改建议】
            "legal_basis": r.get("legal_basis", ""),          # 【法律依据】
            "_weight_multiplier": 1.0,                        # 【权重倍率】标准权重
        })

    # --- 第二路：数值校验风险项字段统一 ---
    for r in numeric_risks:
        all_risks.append({
            "source": "数值校验",                              # 【来源】标识来自数值校验
            "clause": r.get("target_field", r.get("rule_id", "")),  # 【相关条款】
            "severity": r.get("severity", "medium"),          # 【严重程度】
            "description": r.get("description", ""),          # 【风险描述】
            "suggestion": "",                                 # 【修改建议】数值校验通常无修改建议
            "legal_basis": r.get("legal_basis", ""),          # 【法律依据】
            "_weight_multiplier": 1.0,                        # 【权重倍率】标准权重
        })

    # --- 第三路：资信审查风险项字段统一（含对立方加权） ---
    for r in credit_risks:
        is_cp = bool(r.get("is_counterparty", False))
        multiplier = 1.3 if is_cp else 1.0
        all_risks.append({
            "source": "资信审查",                              # 【来源】标识来自资信审查
            "clause": r.get("clause", ""),                    # 【相关条款】
            "severity": r.get("severity", "medium"),          # 【严重程度】
            "description": r.get("description", ""),          # 【风险描述】
            "suggestion": r.get("suggestion", ""),            # 【建议】
            "legal_basis": r.get("legal_basis", ""),          # 【法律依据】
            "party_label": r.get("party_label", ""),          # 当事人标签（甲方/乙方）
            "credit_category": r.get("credit_category", ""),  # 资信分类
            "_weight_multiplier": multiplier,                 # 【权重倍率】对立方 1.3
        })

    # ============================================================
    # 阶段三：去重（按 description 前 50 字）
    # ============================================================
    seen = set()
    merged = []
    for r in all_risks:
        key = r.get("description", "")[:50]
        if key not in seen:
            seen.add(key)
            merged.append(r)

    # ============================================================
    # 阶段四：扣分制评分
    # ============================================================

    # 基础分设定：无风险项时基础分 95，有风险项时基础分 100
    # 【设计意图】无风险项时最高分 95（留 5 分空间给后续全局修正），
    # 有风险项时起始 100 分然后逐项扣分，扣分上限 100 分。
    base_score = 100 if merged else 95

    # 扣分计算：遍历每个风险项，按严重程度扣分
    total_deduction = 0  # 累计扣分
    for r in merged:
        # 获取严重程度对应的扣分权重，若无匹配则按 medium 扣分
        weight = SEVERITY_WEIGHT.get(r.get("severity", "medium"), 40)
        # 应用权重倍率（对立方资信风险 1.3 倍扣分）
        multiplier = r.get("_weight_multiplier", 1.0)
        # 每次扣分 = 权重 × 0.15（经验系数）× 权重倍率
        total_deduction += weight * 0.15 * multiplier

    # 计算初始风险评分 = 基础分 - 累计扣分，下限为 0
    score = max(0, base_score - total_deduction)

    # ============================================================
    # 阶段五：全局资信修正
    # ============================================================

    # 读取甲乙双方的资信评分（可能不存在，默认 80 分）
    # credit_score 是 credit_check_node 写入的各方的信用评分（0-100）
    party_a_score = state.get("party_a_credit_info", {}).get("credit_score", 80)
    party_b_score = state.get("party_b_credit_info", {}).get("credit_score", 80)

    # 修正规则：
    # 甲方信用评分 < 60 → 扣 10 分（甲方自身信用差，签约风险高）
    # 乙方信用评分 < 60 → 扣 10 分（乙方信用差，收款/履约风险高）
    # 双方信用评分均 ≥ 90 → 加 5 分（强强联合，风险降低）
    if party_a_score < 60:
        score -= 10  # 甲方信用差，扣分
    if party_b_score < 60:
        score -= 10  # 乙方信用差，扣分
    if party_a_score >= 90 and party_b_score >= 90:
        score += 5   # 双方信用优秀，加分

    # 确保评分在 0-100 范围内
    score = max(0, min(100, score))

    # ============================================================
    # 阶段六：等级划分
    # ============================================================

    # 根据最终评分确定风险等级
    # ≥81 → Low（低风险），61~80 → Medium（中风险），<60 → High（高风险）
    if score >= 81:
        level = "Low"      # 低风险
    elif score >= 61:
        level = "Medium"   # 中风险
    else:
        level = "High"     # 高风险

    # 强制降级规则：存在 critical 级风险项时，等级不得为 Low
    # 【法律实务意义】即使综合评分 ≥ 81，但只要存在致命风险，
    # 就不能评为低风险，必须标记为中风险以上
    has_critical = any(r.get("severity") == "critical" for r in merged)
    if has_critical and level == "Low":
        level = "Medium"  # 强制降级到 Medium（不可评为 Low）
        print("⚡ 存在critical级别风险，等级强制降为 Medium")

    # ============================================================
    # 阶段七：确定是否需要律师复核
    # ============================================================

    # 综合评分 ≤ 60 或风险等级为 High 时，需要律师人工复核
    # 【法律实务意义】高风险或低评分时，AI 结果不可直接输出，
    # 需要执业律师审核确认后才能交付客户
    need_review = score <= 60 or level == "High"

    # ============================================================
    # 阶段八：写入 state 并返回
    # ============================================================

    # 【merged_risk_items】保存已去重、已统一字段的全部风险项列表
    state["merged_risk_items"] = merged
    # 【overall_risk_score】保存综合风险评分（0-100，越高越安全）
    state["overall_risk_score"] = round(score, 1)
    # 【risk_level】保存风险等级（Low / Medium / High）
    state["risk_level"] = level
    # 【need_lawyer_review】保存是否需要律师复核
    state["need_lawyer_review"] = need_review

    # 【can_sign】签约结论 fallback: 当 conflict_resolution 被跳过时（合规单路）,
    # 此处基于合并后的风险项自行判断, 逻辑与 conflict_resolution 单路模式一致:
    #   critical → no, high → conditional, 其他 → pass
    if not state.get("can_sign"):
        has_critical = any(r.get("severity") == "critical" for r in merged)
        has_high = any(r.get("severity") == "high" for r in merged)
        if has_critical:
            state["can_sign"] = "no"
        elif has_high:
            state["can_sign"] = "conditional"
        else:
            state["can_sign"] = "pass"
        print(f"  [risk_aggregate] can_sign fallback: {state['can_sign']}")

    # 打印节点完成日志
    print(f"--- 风险聚合完成: 评分={round(score,1)}, 等级={level}, "
          f"需复核={need_review}, 总风险项={len(merged)} ---")

    return state