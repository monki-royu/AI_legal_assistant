"""【文件作用】冲突消解节点 ── 当合同审核与合规审查结果冲突时，以合规为准
【逻辑】本文件实现"合规优先原则"的冲突消解逻辑：
    1. 从 state 中读取 contract_risk_items 和 compliance_risk_items
    2. 应用5条冲突消解规则（合规有否决权）
    3. 输出 merged_risk_items（含来源标签）+ can_sign（签约结论）
    4. 决定风险展示顺序（合规风险 → 商业风险）
【法律实务定位】
    - 合同审核 = 商业律师（立场化，评估可谈判的商业风险）
    - 合规审查 = 合规律师（客观中立，检查不可谈判的法律强制规定）
    - 当二者冲突时 → 合规审查有一票否决权
"""

# ============================================================
# 📦 导入模块
# ============================================================

# 导入 json 模块，用于调试打印格式化输出
import json
from typing import List, Dict

# 从同包导入 AgentState，用于类型注解
from __004__langgraph_more_nodes.agent_state import AgentState


# ============================================================
# 🔧 冲突消解规则（核心法律逻辑）
# ============================================================

# 规则1：合规 critical → 否决签约
# 法律依据：违反强制性法律规定的合同条款无效（民法典第153条）
# 效果：无论合同审核意见如何，最终结论为“不得签约”
RULE_VETO_CRITICAL = """
规则1（否决权）：
  如果 compliance_risk_items 中有任何 severity="critical" 的项，
  → can_sign = "no"（不可上诉，不可谈判）
  → 该合规风险项标记 is_overridden = True
  法律依据：民法典第153条——违反强制性规定的法律行为无效
"""

# 规则2：合规 high → 强制整改
# 法律依据：法律风险达到 high 级别但尚未构成无效，必须整改后方可签约
# 效果：即使合同审核说"可接受"，合规仍要求必须修改
RULE_MANDATORY_FIX = """
规则2（强制整改）：
  如果 compliance_risk_items 中有任何 severity="high" 的项，
  → can_sign = "conditional"（条件通过，须整改后方可签约）
  → 即使对应条款的 contract_risk_items 中 severity 更低
  → 仍以合规等级为准
"""

# 规则3：同一问题双重发现 → 以合规为准
# 法律依据：同一法律问题，合规审查（客观法律标准）优先于合同审核（商业判断）
# 效果：合并为一条，标注双重来源，但严重等级采用合规审查的判断
RULE_SAME_ISSUE = """
规则3（双重发现）：
  如果同一 clause 同时出现在 contract_risk_items 和 compliance_risk_items 中，
  → 合并为一条 merged_risk_items 项
  → severity 取合规审查的判断（更严格）
  → source 字段标注 "compliance+contract"
  → description 优先取合规审查的描述
"""

# 规则4：合规通过 + 合同有风险 → 保留合同风险
# 法律依据：合同审核发现的商业风险在合规层面没问题，但仍需告知用户
# 效果：保留 contract_risk_items 项，标注为可谈判的商业风险
RULE_KEEP_CONTRACT = """
规则4（保留商业风险）：
  如果 contract_risk_items 中有某条款的风险，
  但 compliance_risk_items 中没有对应条款的合规问题，
  → 保留该风险项
  → source 标注 "contract_only"
  → 在报告中归入"商业风险（可谈判）"分类
"""

# 规则5：结论冲突 → 合规结论优先
# 法律依据：合规审查是法律底线判断，商业利益不能凌驾于法律之上
# 效果：合同审核说"可接受" + 合规审查说"违法" → 以合规为准
RULE_CONFLICT = """
规则5（结论冲突）：
  如果 contract_risk_items 对某条款的评估为 "可接受" 或 "低风险"，
  但 compliance_risk_items 对同条款评估为 "high" 或 "critical"，
  → 以合规审查结论为准
  → 该风险项强制归入"不可谈判"分类
  → 在 conflict_log 中记录"合规优先于合同审核"
"""


# ============================================================
# 🔧 核心冲突消解函数
# ============================================================

def resolve_conflicts(
    contract_risks: List[Dict],
    compliance_risks: List[Dict]
) -> Dict:
    """
    【功能】执行冲突消解，合并合同审核与合规审查的风险项

    【参数】
        contract_risks (List[Dict]): 合同审核风险项列表
        compliance_risks (List[Dict]): 合规审查风险项列表

    【返回值】Dict 包含：
        - merged_risk_items (List[Dict]): 合并后的风险列表（含来源标签）
        - can_sign (str): 签约结论 "pass" / "conditional" / "no"
        - conflict_log (List[str]): 冲突消解记录（用于调试和追溯）
        - presentation_order (str): 展示顺序 "compliance_first"
    """

    merged: List[Dict] = []
    conflict_log: List[str] = []
    can_sign = "pass"  # 默认：可以签约

    # 构造 compliance_risks 的条款索引，便于快速查找
    # 以 clause 字段为键，存储合规风险项
    compliance_by_clause: Dict[str, Dict] = {}
    for cr in compliance_risks:
        clause = cr.get("clause", "")
        if clause:
            compliance_by_clause[clause] = cr

    # 构造 contract_risks 的条款索引
    contract_by_clause: Dict[str, Dict] = {}
    for ct in contract_risks:
        clause = ct.get("clause", "")
        if clause:
            contract_by_clause[clause] = ct

    # ============================================================
    # 规则1：合规 critical → 否决签约
    # ============================================================
    for cr in compliance_risks:
        if cr.get("severity") == "critical":
            can_sign = "no"  # 否决签约
            conflict_log.append(
                f"[规则1] 合规critical否决签约: "
                f"条款'{cr.get('clause', '未知')}' - {cr.get('description', '')}"
            )
            break  # 任一 critical 即否决，无需继续检查

    # ============================================================
    # 规则2：合规 high（且未被规则1否决） → 条件通过
    # ============================================================
    if can_sign != "no":  # 仅当未被否决时才检查 high
        for cr in compliance_risks:
            if cr.get("severity") == "high":
                can_sign = "conditional"
                conflict_log.append(
                    f"[规则2] 合规high强制整改: "
                    f"条款'{cr.get('clause', '未知')}' - {cr.get('description', '')}"
                )
                # 不 break —— 所有 high 都要记录

    # ============================================================
    # 开始合并风险项：遍历所有涉及到的条款
    # ============================================================

    # 收集所有条款（合同审核的 + 合规审查的）
    all_clauses = set(list(compliance_by_clause.keys()) + list(contract_by_clause.keys()))

    for clause in all_clauses:
        cr = compliance_by_clause.get(clause)  # 合规审查结果
        ct = contract_by_clause.get(clause)    # 合同审核结果

        if cr and ct:
            # ============================================================
            # 规则3：同一问题双重发现 → 以合规为准
            # ============================================================
            merged_item = {
                "clause": clause,
                "severity": cr.get("severity", ct.get("severity", "medium")),
                # 合规的 severity 优先（更严格）
                "description": cr.get("description", ct.get("description", "")),
                # 合规的描述优先
                "legal_basis": cr.get("legal_basis") or ct.get("legal_basis", ""),
                # 优先取合规的法条依据
                "source": "compliance+contract",
                # 标注双重来源
                "compliance_area": cr.get("compliance_area", ""),
                # 合规领域
                "remediation": cr.get("remediation") or ct.get("suggestion", ""),
                # 优先取合规的整改建议
                "is_mandatory": cr.get("severity") in ("critical", "high"),
                # 合规 critical/high → 强制整改
            }
            conflict_log.append(
                f"[规则3] 双重发现-以合规为准: "
                f"条款'{clause}' - 合规等级{cr.get('severity')} > 合同等级{ct.get('severity')}"
            )

            # ============================================================
            # 规则5：结论冲突 → 合规结论优先（已在上面体现）
            # ============================================================
            if cr.get("severity") in ("high", "critical") and ct.get("severity") in ("low", "medium"):
                merged_item["is_overridden"] = True
                conflict_log.append(
                    f"[规则5] 结论冲突-合规优先: "
                    f"条款'{clause}' - 合规{cr.get('severity')} > 合同{ct.get('severity')}"
                )

            merged.append(merged_item)

        elif cr and not ct:
            # 仅合规审查发现的问题（合同审核未发现）
            merged_item = {
                "clause": clause,
                "severity": cr.get("severity", "medium"),
                "description": cr.get("description", ""),
                "legal_basis": cr.get("legal_basis", ""),
                "source": "compliance_only",
                "compliance_area": cr.get("compliance_area", ""),
                "remediation": cr.get("remediation", ""),
                "is_mandatory": cr.get("severity") in ("critical", "high"),
                # 仅有合规发现 → 直接标记为强制
            }
            merged.append(merged_item)

        elif ct and not cr:
            # ============================================================
            # 规则4：合规通过 + 合同有风险 → 保留为商业风险
            # ============================================================
            merged_item = {
                "clause": clause,
                "severity": ct.get("severity", "medium"),
                "description": ct.get("description", ""),
                "legal_basis": ct.get("legal_basis", ""),
                "source": "contract_only",
                "suggestion": ct.get("suggestion", ""),
                "is_mandatory": False,
                # 仅有合同发现 → 可谈判的商业风险
                "risk_category": "commercial",
                # 归入"商业风险"分类
            }
            merged.append(merged_item)

    # 按严重程度排序：critical → high → medium → low
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    merged.sort(key=lambda x: severity_order.get(x.get("severity", "low"), 99))

    # 返回结果
    return {
        "merged_risk_items": merged,
        "can_sign": can_sign,
        "conflict_log": conflict_log,
        "presentation_order": "compliance_first",  # 合规风险始终优先展示
    }


# ============================================================
# 🔧 LangGraph 节点函数
# ============================================================

def conflict_resolution_node(state: AgentState) -> dict:
    """
    【功能】冲突消解节点函数：当合同审核与合规审查结果冲突时，以合规为准

    【参数】state (AgentState)：读取以下字段：
        - contract_risk_items (List[Dict], 可选): 合同审核风险项
        - compliance_risk_items (List[Dict], 可选): 合规审查风险项
    写入字段：
        - post_conflict_risk_items (List[Dict]): 冲突消解后的风险列表
        - can_sign (str): 签约结论 "pass" / "conditional" / "no"
        - conflict_log (List[str]): 冲突消解日志
        - presentation_order (str): 展示顺序 "compliance_first"

    【逻辑】见 resolve_conflicts 函数的5条规则

    【法律实务意义】
        - 确保合规审查的"否决权"在最终输出中得到体现
        - 区分"不可谈判的合规风险"和"可谈判的商业风险"
        - 最终报告中合规风险始终展示在商业风险之前
    """
    print("--- 开始冲突消解 ---")

    # 从 state 读取合同审核和合规审查的结果（可能为空列表）
    contract_risks = state.get("contract_risk_items", []) or []
    compliance_risks = state.get("compliance_risk_items", []) or []

    # 如果没有合规审查结果，直接 pass-through（如法律检索链路）
    if not compliance_risks and not contract_risks:
        print("--- 无风险项，跳过冲突消解 ---")
        state["post_conflict_risk_items"] = []
        state["can_sign"] = "pass"
        state["conflict_log"] = []
        state["presentation_order"] = "compliance_first"
        return state

    # 如果只有合规审查结果（合规审查独立链路）
    if compliance_risks and not contract_risks:
        print(f"--- 仅合规审查结果({len(compliance_risks)}项)，无冲突 ---")
        # 合规审查独立的场景：直接使用合规审查结果
        state["post_conflict_risk_items"] = compliance_risks
        # 判断签约结论
        has_critical = any(r.get("severity") == "critical" for r in compliance_risks)
        has_high = any(r.get("severity") == "high" for r in compliance_risks)
        if has_critical:
            state["can_sign"] = "no"
        elif has_high:
            state["can_sign"] = "conditional"
        else:
            state["can_sign"] = "pass"
        state["conflict_log"] = ["合规审查独立执行，无合同审核结果可冲突"]
        state["presentation_order"] = "compliance_first"
        return state

    # 如果只有合同审核结果——这在当前架构中不应出现，但防御性处理
    if contract_risks and not compliance_risks:
        print(f"--- 仅有合同审核结果({len(contract_risks)}项) ---")
        state["post_conflict_risk_items"] = contract_risks
        state["can_sign"] = "pass"
        state["conflict_log"] = ["仅合同审核执行，无合规审查结果"]
        state["presentation_order"] = "compliance_first"
        return state

    # 既有合同审核又有合规审查 → 执行冲突消解
    print(f"--- 合同审核{len(contract_risks)}项 vs 合规审查{len(compliance_risks)}项 ---")
    result = resolve_conflicts(contract_risks, compliance_risks)

    # 写入 state
    state["post_conflict_risk_items"] = result["merged_risk_items"]
    state["can_sign"] = result["can_sign"]
    state["conflict_log"] = result["conflict_log"]
    state["presentation_order"] = "compliance_first"

    print(f"--- 冲突消解完成: {len(result['merged_risk_items'])}项合并, 签约结论={result['can_sign']} ---")
    if result["conflict_log"]:
        for log in result["conflict_log"]:
            print(f"  ⚡ {log}")

    return state


# ============================================================
# 🧪 模块自测入口
# ============================================================
if __name__ == "__main__":
    # 测试场景：合同审核说"可接受" + 合规审查说"违法"
    s = AgentState(
        contract_risk_items=[
            {"clause": "第X条", "severity": "medium",
             "description": "违约金30%稍高，建议协商降低",
             "suggestion": "建议调整为20%"}
        ],
        compliance_risk_items=[
            {"clause": "第X条", "severity": "critical",
             "description": "违法收集用户个人信息提供给第三方",
             "legal_basis": "违反个保法第23条",
             "remediation": "必须删除该条款或增加用户单独同意机制"}
        ]
    )
    # 执行冲突消解
    result = conflict_resolution_node(s)
    # 打印结果
    print("\n=== 冲突消解结果 ===")
    print(f"签约结论: {result.get('can_sign')}")
    print(f"合并风险项: {json.dumps(result.get('merged_risk_items'), ensure_ascii=False, indent=2)}")
    print(f"消解日志: {json.dumps(result.get('conflict_log'), ensure_ascii=False, indent=2)}")