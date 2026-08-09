"""
contract_review_graph.py - 修复版本
合同智能审核系统 LangGraph 状态图
支持方案一（标准AI审核）& 方案二（自定义规则优先）
"""

import json
from typing import TypedDict, Optional, List, Dict, Any
from enum import Enum

# ============================================================
# 0. 枚举定义
# ============================================================

class ReviewMode(Enum):
    AI_AUTO = "AI_AUTO"
    CUSTOM_RULES = "CUSTOM_RULES"

class RiskLevel(Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"

class UserDecision(Enum):
    ACCEPT = "ACCEPT"
    MODIFY = "MODIFY"
    REJECT = "REJECT"

class SourceType(Enum):
    AI = "AI"
    CUSTOM_RULE = "CUSTOM_RULE"
    DUAL = "DUAL"
    CONFLICT = "CONFLICT"

class PartyRole(Enum):
    PARTY_A = "甲方"
    PARTY_B = "乙方"
    THIRD_PARTY = "第三方"

# ============================================================
# 1. 状态定义
# ============================================================

class TextLocation(TypedDict):
    page: int
    bbox: List[float]

class RiskItem(TypedDict):
    clause_id: str
    clause_title: str
    text: str
    text_location: TextLocation
    risk_level: str
    source: str
    ai_suggestion: Optional[str]
    rule_suggestion: Optional[str]
    final_decision: Optional[str]
    user_modified_text: Optional[str]

class ParsedRule(TypedDict):
    focus_clauses: List[str]
    thresholds: Dict[str, str]
    exclude_clauses: List[str]
    risk_tolerance: Dict[str, str]

class ContractReviewState(TypedDict):
    """全局状态对象"""
    # 输入
    raw_document: str
    user_instruction: str
    custom_rules_text: Optional[str]

    # N1
    intent: str
    review_mode: str
    enable_ai_second_review: bool

    # N2
    extracted_text: str
    document_metadata: Dict[str, Any]

    # N3
    contract_type: str

    # N4
    clauses: List[Dict[str, Any]]

    # N4.5
    parsed_rules: Optional[ParsedRule]
    rule_coverage_evaluation: Optional[str]

    # N5
    ai_review_result: Optional[List[RiskItem]]
    rule_engine_result: Optional[List[RiskItem]]
    conflict_items: Optional[List[RiskItem]]

    # N6
    legal_articles: List[Dict[str, Any]]
    similar_cases: List[Dict[str, Any]]

    # N7
    aggregated_risk_items: List[RiskItem]
    risk_score: int
    structured_report: Dict[str, Any]

    # N7.5
    highlighted_document_path: Optional[str]

    # N8
    user_decisions: List[Dict[str, Any]]
    conflict_resolutions: List[Dict[str, Any]]
    party_role: str
    route_decision: str

    # N9
    revised_contract_path: Optional[str]
    review_opinion_path: Optional[str]

    # N10-N13
    email_requested: bool
    email_draft: Optional[str]
    email_confirmed: bool
    email_sent: bool
    email_log: Optional[Dict[str, Any]]

    # N14
    final_report: Optional[Dict[str, Any]]
    lawyer_referral: bool

    # 流程控制
    interrupt_reason: Optional[str]
    human_in_the_loop: bool


# ============================================================
# 2. 节点函数
# ============================================================

def node_N1_intent_router(state: ContractReviewState) -> ContractReviewState:
    """[N1] 意图识别 + 审核模式分发"""
    print("=" * 60)
    print("[N1] 意图识别与审核模式路由")
    print("=" * 60)

    user_text = state.get("user_instruction", "")
    rules_text = state.get("custom_rules_text")

    has_rules = rules_text is not None and len(rules_text.strip()) > 0
    prefer_ai = "AI审核" in user_text or "自动审核" in user_text
    prefer_custom = "自定义" in user_text or "我的规则" in user_text or "按规则" in user_text

    if prefer_custom or (has_rules and not prefer_ai):
        state["review_mode"] = ReviewMode.CUSTOM_RULES.value
        state["intent"] = "contract_review_custom"
        print(f"  → 审核模式: 自定义规则审核")
        print(f"  → 检测到用户规则: {len(rules_text)} 字符")
    else:
        state["review_mode"] = ReviewMode.AI_AUTO.value
        state["intent"] = "contract_review_auto"
        print(f"  → 审核模式: 标准AI审核")

    state["enable_ai_second_review"] = False
    state["human_in_the_loop"] = False

    return state


def node_N1_5_rule_config(state: ContractReviewState) -> ContractReviewState:
    """[N1.5] 规则配置 & 二次审核确认"""
    print("\n" + "=" * 60)
    print("[N1.5] 规则配置与二次审核确认")
    print("=" * 60)

    rules_text = state.get("custom_rules_text", "")

    parsed_rules: ParsedRule = {
        "focus_clauses": _extract_focus_clauses(rules_text),
        "thresholds": _extract_thresholds(rules_text),
        "exclude_clauses": _extract_exclusions(rules_text),
        "risk_tolerance": _extract_risk_tolerance(rules_text),
    }

    state["parsed_rules"] = parsed_rules
    coverage_eval = _evaluate_rule_coverage(parsed_rules, state.get("clauses", []))
    state["rule_coverage_evaluation"] = coverage_eval

    print(f"  → 解析规则: {json.dumps(parsed_rules, ensure_ascii=False, indent=2)}")
    print(f"  → 规则覆盖评估: {coverage_eval}")

    # 询问用户是否启用AI二次审核
    # TODO: 实际场景中此处应 interrupt 等待用户确认
    state["enable_ai_second_review"] = True
    print(f"  → AI二次审核: {'启用' if state['enable_ai_second_review'] else '关闭'}")

    return state


def node_N2_document_extractor(state: ContractReviewState) -> ContractReviewState:
    """[N2] 合同文档提取"""
    print("\n" + "=" * 60)
    print("[N2] 合同文档提取")
    print("=" * 60)

    state["extracted_text"] = "[模拟] 合同正文内容..."
    state["document_metadata"] = {
        "total_pages": 5,
        "format": "PDF",
        "has_ocr": False,
    }
    print(f"  → 提取文本: {state['document_metadata']['total_pages']} 页")
    print(f"  → 文档格式: {state['document_metadata']['format']}")

    return state


def node_N3_contract_classifier(state: ContractReviewState) -> ContractReviewState:
    """[N3] 合同类型分类"""
    print("\n" + "=" * 60)
    print("[N3] 合同类型分类")
    print("=" * 60)

    # TODO: 接入 LLM 分类
    state["contract_type"] = "劳动合同"
    print(f"  → 合同类型: {state['contract_type']}")

    return state


def node_N4_clause_splitter(state: ContractReviewState) -> ContractReviewState:
    """[N4] 条款切分"""
    print("\n" + "=" * 60)
    print("[N4] 条款切分")
    print("=" * 60)

    # TODO: 实际条款切分
    state["clauses"] = [
        {
            "clause_id": "Clause-1.1",
            "title": "合同期限",
            "text": "本合同期限为三年，自2026年1月1日起至2028年12月31日止。",
            "page": 1,
            "bbox": [100, 200, 500, 230],
        },
        {
            "clause_id": "Clause-5.2",
            "title": "违约金条款",
            "text": "任何一方违约，应向对方支付合同总金额50%的违约金。",
            "page": 3,
            "bbox": [120, 450, 480, 470],
        },
        {
            "clause_id": "Clause-7.1",
            "title": "竞业限制",
            "text": "乙方离职后两年内不得从事同类业务。",
            "page": 4,
            "bbox": [100, 300, 500, 330],
        },
    ]

    print(f"  → 切分出 {len(state['clauses'])} 个条款")
    for c in state["clauses"]:
        print(f"    [{c['clause_id']}] {c['title']} (p{c['page']})")

    return state


def node_N5_A_ai_review(state: ContractReviewState) -> ContractReviewState:
    """[N5-A] AI专家审核引擎"""
    print("\n" + "=" * 60)
    print("[N5-A] AI专家审核引擎")
    print("=" * 60)

    clauses = state.get("clauses", [])
    results: List[RiskItem] = []

    for clause in clauses:
        if "违约金" in clause["text"] and "50%" in clause["text"]:
            results.append(_make_risk_item(
                clause=clause,
                risk_level=RiskLevel.HIGH.value,
                source=SourceType.AI.value,
                ai_suggestion="违约金50%过高，建议调整为不超过实际损失的30%",
                rule_suggestion=None,
            ))
        elif "竞业" in clause["text"] and "两年" in clause["text"]:
            results.append(_make_risk_item(
                clause=clause,
                risk_level=RiskLevel.MEDIUM.value,
                source=SourceType.AI.value,
                ai_suggestion="竞业限制期2年偏长，建议缩短至1年以内",
                rule_suggestion=None,
            ))

    state["ai_review_result"] = results
    print(f"  → AI审核完成，发现 {len(results)} 处风险")
    for r in results:
        print(f"    [{r['risk_level']}] {r['clause_id']}: {r['ai_suggestion']}")

    return state


def node_N5_B_rule_engine(state: ContractReviewState) -> ContractReviewState:
    """[N5-B] 自定义规则引擎审核"""
    print("\n" + "=" * 60)
    print("[N5-B] 自定义规则引擎审核")
    print("=" * 60)

    clauses = state.get("clauses", [])
    parsed_rules = state.get("parsed_rules", {}) or {}
    results: List[RiskItem] = []

    thresholds = parsed_rules.get("thresholds", {})

    for clause in clauses:
        if "违约金" in clause["text"] and "违约金上限" in thresholds:
            limit = thresholds["违约金上限"]
            results.append(_make_risk_item(
                clause=clause,
                risk_level=RiskLevel.HIGH.value,
                source=SourceType.CUSTOM_RULE.value,
                ai_suggestion=None,
                rule_suggestion=f"违反用户规则：违约金上限{limit}",
            ))
            print(f"    [RULE HIT] {clause['clause_id']}: 违约金超限")

        elif "竞业" in clause["text"] and "竞业期" in thresholds:
            limit = thresholds["竞业期"]
            results.append(_make_risk_item(
                clause=clause,
                risk_level=RiskLevel.HIGH.value,
                source=SourceType.CUSTOM_RULE.value,
                ai_suggestion=None,
                rule_suggestion=f"违反用户规则：竞业期限制为{limit}",
            ))
            print(f"    [RULE HIT] {clause['clause_id']}: 竞业期超限")

    state["rule_engine_result"] = results
    print(f"  → 规则引擎审核完成，命中 {len(results)} 处")

    return state


def node_N5_C_ai_second_review(state: ContractReviewState) -> ContractReviewState:
    """[N5-C] AI二次审核（方案二可选）"""
    print("\n" + "=" * 60)
    print("[N5-C] AI二次审核（补充）")
    print("=" * 60)

    # 调用AI审核
    state = node_N5_A_ai_review(state)
    print(f"  → AI二次审核完成")
    return state


def node_N6_legal_retrieval(state: ContractReviewState) -> ContractReviewState:
    """[N6] 法条 + 类案检索"""
    print("\n" + "=" * 60)
    print("[N6] 法条与类案检索")
    print("=" * 60)

    review_mode = state.get("review_mode")
    parsed_rules = state.get("parsed_rules")

    keywords = ["违约金", "竞业限制", "劳动合同法"]

    if review_mode == ReviewMode.CUSTOM_RULES.value and parsed_rules:
        focus = parsed_rules.get("focus_clauses", [])
        keywords = focus + keywords
        print(f"  → 检索策略: 用户规则加权 (keywords: {keywords})")
    else:
        print(f"  → 检索策略: 默认风险关键词")

    # TODO: 接入法条库
    state["legal_articles"] = [
        {
            "title": "《劳动合同法》第25条",
            "content": "除本法第二十二条和第二十三条规定的情形外，用人单位不得与劳动者约定由劳动者承担违约金。",
            "relevance": 0.95,
        }
    ]
    state["similar_cases"] = [
        {
            "case_name": "某公司诉员工违约金纠纷案",
            "summary": "违约金约定超过实际损失30%被法院调减",
            "relevance": 0.88,
        }
    ]

    print(f"  → 检索到 {len(state['legal_articles'])} 条法条, {len(state['similar_cases'])} 个类案")
    return state


def node_N7_risk_aggregation(state: ContractReviewState) -> ContractReviewState:
    """[N7] 风险聚合与分级"""
    print("\n" + "=" * 60)
    print("[N7] 风险聚合与分级")
    print("=" * 60)

    review_mode = state.get("review_mode")
    aggregated: List[RiskItem] = []
    conflict_items: List[RiskItem] = []

    if review_mode == ReviewMode.AI_AUTO.value:
        aggregated = state.get("ai_review_result", []) or []
    else:
        rule_results = state.get("rule_engine_result", []) or []
        ai_results = state.get("ai_review_result", []) or []

        rule_map = {r["clause_id"]: r for r in rule_results}
        ai_map = {r["clause_id"]: r for r in ai_results}
        all_ids = set(list(rule_map.keys()) + list(ai_map.keys()))

        for cid in all_ids:
            rule_item = rule_map.get(cid)
            ai_item = ai_map.get(cid)

            if rule_item and ai_item:
                if rule_item["risk_level"] == ai_item["risk_level"]:
                    merged = dict(rule_item)
                    merged["source"] = SourceType.DUAL.value
                    merged["ai_suggestion"] = ai_item.get("ai_suggestion")
                    aggregated.append(merged)
                else:
                    conflict_item = dict(rule_item)
                    conflict_item["source"] = SourceType.CONFLICT.value
                    conflict_item["ai_suggestion"] = ai_item.get("ai_suggestion")
                    aggregated.append(conflict_item)
                    conflict_items.append(conflict_item)
            elif rule_item:
                aggregated.append(rule_item)
            elif ai_item:
                aggregated.append(ai_item)

    state["aggregated_risk_items"] = aggregated
    state["conflict_items"] = conflict_items if conflict_items else None

    risk_score = _calculate_risk_score(aggregated, state.get("parsed_rules"))
    state["risk_score"] = risk_score

    high_count = sum(1 for r in aggregated if r["risk_level"] == "High")
    med_count = sum(1 for r in aggregated if r["risk_level"] == "Medium")
    low_count = sum(1 for r in aggregated if r["risk_level"] == "Low")

    state["structured_report"] = {
        "contract_type": state.get("contract_type"),
        "review_mode": review_mode,
        "risk_score": risk_score,
        "total_risk_items": len(aggregated),
        "high_risk_count": high_count,
        "medium_risk_count": med_count,
        "low_risk_count": low_count,
        "risk_items": aggregated,
        "conflict_count": len(conflict_items),
        "rule_evaluation": state.get("rule_coverage_evaluation"),
        "legal_articles": state.get("legal_articles", []),
        "similar_cases": state.get("similar_cases", []),
    }

    print(f"  → 聚合完成: {len(aggregated)} 条风险项")
    print(f"  → 综合风险评分: {risk_score}/100")
    print(f"  → 高/中/低: {high_count}/{med_count}/{low_count}")
    print(f"  → 冲突项: {len(conflict_items)} 处")

    return state


def node_N7_5_highlight_renderer(state: ContractReviewState) -> ContractReviewState:
    """[N7.5] 原文坐标标红渲染"""
    print("\n" + "=" * 60)
    print("[N7.5] 原文标红渲染")
    print("=" * 60)

    risk_items = state.get("aggregated_risk_items", [])
    color_map = {"High": "🔴RED", "Medium": "🟡YELLOW", "Low": "🔵BLUE"}

    for item in risk_items:
        loc = item["text_location"]
        color = color_map.get(item["risk_level"], "⚪UNKNOWN")
        print(f"    [{color:>12}] p{loc['page']} bbox={loc['bbox']} → {item['clause_id']}")

    state["highlighted_document_path"] = "/output/highlighted_contract.pdf"
    print(f"  → 标红文档: {state['highlighted_document_path']}")

    return state


def node_N8_human_interaction(state: ContractReviewState) -> ContractReviewState:
    """[N8] 人机交互决策中心"""
    print("\n" + "=" * 60)
    print("[N8] 人机交互决策中心")
    print("=" * 60)

    review_mode = state.get("review_mode")
    risk_items = state.get("aggregated_risk_items", [])
    user_decisions = []

    for item in risk_items:
        source = item.get("source")

        if source == SourceType.CONFLICT.value:
            print(f"    [⚠️ 冲突] {item['clause_id']}")
            print(f"      AI建议: {item.get('ai_suggestion')}")
            print(f"      规则意见: {item.get('rule_suggestion')}")
            print(f"      → 强制升级人工审核")
            state["human_in_the_loop"] = True
            state["interrupt_reason"] = f"AI与自定义规则冲突: {item['clause_id']}"
            decision = {"clause_id": item["clause_id"], "decision": "MODIFY",
                       "note": "采纳规则结论，参考AI建议", "source": source}

        elif review_mode == ReviewMode.AI_AUTO.value:
            print(f"    [AI建议] {item['clause_id']}: {item.get('ai_suggestion')}")
            print(f"      → 等待用户: 采纳/修改/不采纳")
            decision = {"clause_id": item["clause_id"], "decision": "ACCEPT",
                       "note": "采纳AI建议", "source": source}
        else:
            print(f"    [规则主导] {item['clause_id']}: {item.get('rule_suggestion')}")
            if item.get("ai_suggestion"):
                print(f"      [AI参考] {item.get('ai_suggestion')}")
            print(f"      → 以规则为准")
            decision = {"clause_id": item["clause_id"], "decision": "ACCEPT",
                       "note": "执行自定义规则结论", "source": source}

        item["final_decision"] = decision["decision"]
        user_decisions.append(decision)

    state["user_decisions"] = user_decisions
    return state


def node_N8_party_identifier(state: ContractReviewState) -> ContractReviewState:
    """[N8] 甲乙方识别"""
    print("\n" + "=" * 60)
    print("[N8] 甲乙方识别")
    print("=" * 60)

    # TODO: 实际识别逻辑
    state["party_role"] = PartyRole.PARTY_B.value
    print(f"  → 用户角色: {state['party_role']}")
    return state


def node_N9a_auto_generate(state: ContractReviewState) -> ContractReviewState:
    """[N9a] 自动文书生成（低风险）"""
    print("\n" + "=" * 60)
    print("[N9a] 自动文书生成（低风险）")
    print("=" * 60)

    party = state.get("party_role", "甲方")
    state["revised_contract_path"] = f"/output/revised_contract_{party}.docx"
    state["review_opinion_path"] = f"/output/review_opinion_{party}.pdf"
    print(f"  → 立场: {party}")
    print(f"  → 修订合同: {state['revised_contract_path']}")
    print(f"  → 审查意见书: {state['review_opinion_path']}")
    return state


def node_N9b_lawyer_review(state: ContractReviewState) -> ContractReviewState:
    """[N9b] 律师轻量复核（中风险）"""
    print("\n" + "=" * 60)
    print("[N9b] 律师轻量复核（中风险）")
    print("=" * 60)

    state["human_in_the_loop"] = True
    state["interrupt_reason"] = f"中风险案件需律师复核 (score={state.get('risk_score')})"
    print(f"  → ⏸️ LangGraph Interrupt 触发")
    print(f"  → 等待律师确认...")
    return state


def node_N9c_human_interrupt(state: ContractReviewState) -> ContractReviewState:
    """[N9c] 人工中断（高风险/冲突）"""
    print("\n" + "=" * 60)
    print("[N9c] 人工中断（高风险/冲突）")
    print("=" * 60)

    state["human_in_the_loop"] = True
    state["interrupt_reason"] = "高风险或规则冲突，强制人工审核"
    print(f"  → 🛑 强制律师审核签章")
    print(f"  → 风险评分: {state.get('risk_score')}")
    print(f"  → 冲突项: {len(state.get('conflict_items', []) or [])} 处")
    return state


def node_N10_email_draft(state: ContractReviewState) -> ContractReviewState:
    """[N10] 沟通邮件草稿生成"""
    print("\n" + "=" * 60)
    print("[N10] 沟通邮件草稿生成")
    print("=" * 60)

    party = state.get("party_role", "甲方")
    risk_items = state.get("aggregated_risk_items", [])

    draft_lines = [f"主题：关于《{state.get('contract_type')}》条款修改建议", ""]
    draft_lines.append("尊敬的对方：")
    draft_lines.append("")
    draft_lines.append("经审核，合同存在以下需修改的条款：")
    draft_lines.append("")

    for item in risk_items:
        draft_lines.append(f"- 【{item['clause_title']}】(风险: {item['risk_level']})")
        draft_lines.append(f"  原文: {item['text']}")
        if item.get("ai_suggestion"):
            draft_lines.append(f"  建议: {item['ai_suggestion']}")
        if item.get("rule_suggestion"):
            draft_lines.append(f"  规则意见: {item['rule_suggestion']}")
        draft_lines.append("")

    draft_lines.append("望予以考虑并回复。")
    draft = "\n".join(draft_lines)

    state["email_draft"] = draft
    state["email_requested"] = True
    print(f"  → 邮件草稿已生成（{party}立场）")
    print(f"  → 草稿预览:\n{draft[:200]}...")
    return state


def node_N11_email_confirm(state: ContractReviewState) -> ContractReviewState:
    """[N11] 邮件确认节点"""
    print("\n" + "=" * 60)
    print("[N11] 邮件确认（human-in-the-loop）")
    print("=" * 60)

    print(f"  → 展示邮件草稿给用户确认...")
    print(f"  → 操作: 确认 / 编辑 / 拒绝")
    # TODO: 实际 interrupt
    state["email_confirmed"] = True
    print(f"  → 用户确认: ✓")
    return state


def node_N12_email_send(state: ContractReviewState) -> ContractReviewState:
    """[N12] 邮件自动发送"""
    print("\n" + "=" * 60)
    print("[N12] 邮件发送")
    print("=" * 60)

    if not state.get("email_confirmed"):
        print(f"  → 邮件未确认，跳过")
        return state

    print(f"  → 📧 邮件已发送")
    state["email_sent"] = True
    return state


def node_N13_email_log(state: ContractReviewState) -> ContractReviewState:
    """[N13] 发送记录"""
    print("\n" + "=" * 60)
    print("[N13] 发送记录")
    print("=" * 60)

    state["email_log"] = {
        "sent_at": "2026-08-06T15:30:00+08:00",
        "recipient": "counterparty@example.com",
        "status": "delivered",
        "case_id": "CASE-2026-0806-001",
    }
    print(f"  → 审计日志记录完成")
    return state


def node_N14_final_delivery(state: ContractReviewState) -> ContractReviewState:
    """[N14] 最终输出与交付"""
    print("\n" + "=" * 60)
    print("[N14] 最终输出与交付")
    print("=" * 60)

    state["final_report"] = {
        "summary": {
            "contract_type": state.get("contract_type"),
            "review_mode": state.get("review_mode"),
            "party_role": state.get("party_role"),
            "risk_score": state.get("risk_score"),
            "total_risk_items": len(state.get("aggregated_risk_items", [])),
        },
        "review_opinion": state.get("structured_report"),
        "revised_contract": state.get("revised_contract_path"),
        "highlighted_document": state.get("highlighted_document_path"),
        "email_record": state.get("email_log"),
        "decision_traceability": state.get("user_decisions", []),
    }

    state["lawyer_referral"] = (
        (state.get("risk_score", 100) < 60)
        or bool(state.get("conflict_items"))
    )

    print(f"  → ✅ 最终报告已生成")
    print(f"  → 律师转介: {'是' if state['lawyer_referral'] else '否'}")
    return state


# ============================================================
# 3. 条件分支函数
# ============================================================

def conditional_review_mode(state: ContractReviewState) -> str:
    """第一层：审核模式路由"""
    if state.get("review_mode") == ReviewMode.CUSTOM_RULES.value:
        return "custom_rules_path"
    return "ai_auto_path"


def conditional_after_clause_split(state: ContractReviewState) -> str:
    """
    N4 之后的条件路由：
    - AI_AUTO → N5_A
    - CUSTOM_RULES → N5_B
    """
    if state.get("review_mode") == ReviewMode.CUSTOM_RULES.value:
        return "go_rule_engine"
    return "go_ai_review"


def conditional_ai_second_review(state: ContractReviewState) -> str:
    """方案二：是否启用AI二次审核"""
    if state.get("enable_ai_second_review"):
        return "enable_ai_review"
    return "skip_ai_review"


def conditional_route_by_risk(state: ContractReviewState) -> str:
    """根据风险评分路由到 N9a/b/c"""
    risk_score = state.get("risk_score", 0)
    has_conflict = bool(state.get("conflict_items"))

    print(f"\n  [路由] risk={risk_score}, conflict={has_conflict}")

    if has_conflict or state.get("human_in_the_loop"):
        return "N9c_human_interrupt"

    if risk_score >= 81:
        return "N9a_auto_generate"
    elif risk_score >= 61:
        return "N9b_lawyer_review"
    else:
        return "N9c_human_interrupt"


def conditional_email_needed(state: ContractReviewState) -> str:
    """是否需要生成沟通邮件"""
    # 这里模拟：有高风险项时主动询问
    risk_items = state.get("aggregated_risk_items", [])
    if risk_items:
        return "ask_email"
    return "skip_email"


def conditional_after_email_ask(state: ContractReviewState) -> str:
    """用户是否确认需要邮件"""
    # TODO: 实际应 interrupt 等待用户选择
    # 模拟：有高风险时建议发邮件
    risk_score = state.get("risk_score", 100)
    if risk_score < 81:
        return "generate_email"
    return "skip_email"


# ============================================================
# 4. 辅助函数
# ============================================================

def _make_risk_item(clause: Dict, risk_level: str, source: str,
                    ai_suggestion: Optional[str], rule_suggestion: Optional[str]) -> RiskItem:
    """创建标准化风险项"""
    return RiskItem(
        clause_id=clause["clause_id"],
        clause_title=clause["title"],
        text=clause["text"],
        text_location=TextLocation(page=clause["page"], bbox=clause["bbox"]),
        risk_level=risk_level,
        source=source,
        ai_suggestion=ai_suggestion,
        rule_suggestion=rule_suggestion,
        final_decision=None,
        user_modified_text=None,
    )


def _extract_focus_clauses(rules_text: str) -> List[str]:
    keywords = ["违约金", "竞业限制", "知识产权", "保密", "赔偿", "解除"]
    return [kw for kw in keywords if kw in rules_text]

def _extract_thresholds(rules_text: str) -> Dict[str, str]:
    thresholds = {}
    if "违约金" in rules_text:
        if "20%" in rules_text: thresholds["违约金上限"] = "20%"
        elif "30%" in rules_text: thresholds["违约金上限"] = "30%"
    if "竞业" in rules_text:
        if "1年" in rules_text or "一年" in rules_text: thresholds["竞业期"] = "≤1年"
    return thresholds

def _extract_exclusions(rules_text: str) -> List[str]:
    return []

def _extract_risk_tolerance(rules_text: str) -> Dict[str, str]:
    return {"default": "Low"} if "低风险" in rules_text else {}

def _evaluate_rule_coverage(parsed_rules: Dict, clauses: List[Dict]) -> str:
    focus = parsed_rules.get("focus_clauses", [])
    if len(focus) >= 3:
        return "规则覆盖较为全面，建议补充：争议解决、不可抗力条款"
    elif len(focus) > 0:
        return "规则覆盖有限，建议补充更多条款类型"
    return "规则为空，建议启用AI辅助审核"

def _calculate_risk_score(risk_items: List[RiskItem], parsed_rules: Optional[Dict]) -> int:
    if not risk_items:
        return 95
    base = 100
    deductions = {"High": 25, "Medium": 12, "Low": 5}
    for item in risk_items:
        base -= deductions.get(item.get("risk_level", "Low"), 5)
        if item.get("source") == SourceType.CONFLICT.value:
            base -= 10
    return max(0, min(100, base))


# ============================================================
# 5. 构建 LangGraph
# ============================================================

def build_contract_review_graph():
    """构建完整的 LangGraph 状态图"""
    from langgraph.graph import StateGraph, END

    workflow = StateGraph(ContractReviewState)

    # ---- 注册节点 ----
    workflow.add_node("N1_intent_router", node_N1_intent_router)
    workflow.add_node("N1_5_rule_config", node_N1_5_rule_config)
    workflow.add_node("N2_document_extractor", node_N2_document_extractor)
    workflow.add_node("N3_contract_classifier", node_N3_contract_classifier)
    workflow.add_node("N4_clause_splitter", node_N4_clause_splitter)
    workflow.add_node("N5_A_ai_review", node_N5_A_ai_review)
    workflow.add_node("N5_B_rule_engine", node_N5_B_rule_engine)
    workflow.add_node("N5_C_ai_second_review", node_N5_C_ai_second_review)
    workflow.add_node("N6_legal_retrieval", node_N6_legal_retrieval)
    workflow.add_node("N7_risk_aggregation", node_N7_risk_aggregation)
    workflow.add_node("N7_5_highlight_renderer", node_N7_5_highlight_renderer)
    workflow.add_node("N8_human_interaction", node_N8_human_interaction)
    workflow.add_node("N8_party_identifier", node_N8_party_identifier)
    workflow.add_node("N9a_auto_generate", node_N9a_auto_generate)
    workflow.add_node("N9b_lawyer_review", node_N9b_lawyer_review)
    workflow.add_node("N9c_human_interrupt", node_N9c_human_interrupt)
    workflow.add_node("N10_email_draft", node_N10_email_draft)
    workflow.add_node("N11_email_confirm", node_N11_email_confirm)
    workflow.add_node("N12_email_send", node_N12_email_send)
    workflow.add_node("N13_email_log", node_N13_email_log)
    workflow.add_node("N14_final_delivery", node_N14_final_delivery)

    # ---- 入口 ----
    workflow.set_entry_point("N1_intent_router")

    # ---- 第一层：审核模式分支 ----
    workflow.add_conditional_edges(
        "N1_intent_router",
        conditional_review_mode,
        {
            "ai_auto_path": "N2_document_extractor",
            "custom_rules_path": "N1_5_rule_config",
        }
    )

    # 方案二：N1.5 → 预处理
    workflow.add_edge("N1_5_rule_config", "N2_document_extractor")

    # ---- 第二层：预处理链路 ----
    workflow.add_edge("N2_document_extractor", "N3_contract_classifier")
    workflow.add_edge("N3_contract_classifier", "N4_clause_splitter")

    # ---- 第三层：审核引擎分支 ----
    workflow.add_conditional_edges(
        "N4_clause_splitter",
        conditional_after_clause_split,
        {
            "go_ai_review": "N5_A_ai_review",
            "go_rule_engine": "N5_B_rule_engine",
        }
    )

    # AI_AUTO: N5-A → N6
    workflow.add_edge("N5_A_ai_review", "N6_legal_retrieval")

    # CUSTOM: N5-B → 条件判断(是否AI二次审核)
    workflow.add_conditional_edges(
        "N5_B_rule_engine",
        conditional_ai_second_review,
        {
            "enable_ai_review": "N5_C_ai_second_review",
            "skip_ai_review": "N6_legal_retrieval",
        }
    )
    workflow.add_edge("N5_C_ai_second_review", "N6_legal_retrieval")

    # ---- 第五层 ----
    workflow.add_edge("N6_legal_retrieval", "N7_risk_aggregation")
    workflow.add_edge("N7_risk_aggregation", "N7_5_highlight_renderer")

    # ---- 第六层：人机交互 ----
    workflow.add_edge("N7_5_highlight_renderer", "N8_human_interaction")
    workflow.add_edge("N8_human_interaction", "N8_party_identifier")

    # ---- 风险路由 ----
    workflow.add_conditional_edges(
        "N8_party_identifier",
        conditional_route_by_risk,
        {
            "N9a_auto_generate": "N9a_auto_generate",
            "N9b_lawyer_review": "N9b_lawyer_review",
            "N9c_human_interrupt": "N9c_human_interrupt",
        }
    )

    # ---- N9 → 邮件询问 ----
    workflow.add_conditional_edges(
        "N9a_auto_generate",
        conditional_email_needed,
        {"ask_email": "N10_email_draft", "skip_email": "N14_final_delivery"}
    )
    workflow.add_conditional_edges(
        "N9b_lawyer_review",
        conditional_email_needed,
        {"ask_email": "N10_email_draft", "skip_email": "N14_final_delivery"}
    )
    workflow.add_conditional_edges(
        "N9c_human_interrupt",
        conditional_email_needed,
        {"ask_email": "N10_email_draft", "skip_email": "N14_final_delivery"}
    )

    # ---- 邮件链路 ----
    workflow.add_edge("N10_email_draft", "N11_email_confirm")
    workflow.add_edge("N11_email_confirm", "N12_email_send")
    workflow.add_edge("N12_email_send", "N13_email_log")
    workflow.add_edge("N13_email_log", "N14_final_delivery")

    # ---- 终点 ----
    workflow.add_edge("N14_final_delivery", END)

    return workflow.compile()


# ============================================================
# 6. 初始状态工厂
# ============================================================

def make_initial_state(
    instruction: str,
    document: str = "/uploads/contract.pdf",
    rules: Optional[str] = None,
) -> ContractReviewState:
    """创建初始状态"""
    return ContractReviewState(
        raw_document=document,
        user_instruction=instruction,
        custom_rules_text=rules,
        intent="",
        review_mode="",
        enable_ai_second_review=False,
        extracted_text="",
        document_metadata={},
        contract_type="",
        clauses=[],
        parsed_rules=None,
        rule_coverage_evaluation=None,
        ai_review_result=None,
        rule_engine_result=None,
        conflict_items=None,
        legal_articles=[],
        similar_cases=[],
        aggregated_risk_items=[],
        risk_score=0,
        structured_report={},
        highlighted_document_path=None,
        user_decisions=[],
        conflict_resolutions=[],
        party_role="",
        route_decision="",
        revised_contract_path=None,
        review_opinion_path=None,
        email_requested=False,
        email_draft=None,
        email_confirmed=False,
        email_sent=False,
        email_log=None,
        final_report=None,
        lawyer_referral=False,
        interrupt_reason=None,
        human_in_the_loop=False,
    )


# ============================================================
# 7. Mermaid 图生成
# ============================================================

def generate_mermaid() -> str:
    """生成 Mermaid 状态图"""
    return """
```mermaid
stateDiagram-v2
    [*] --> N1_意图路由

    N1_意图路由 --> N2_文档提取 : AI_AUTO
    N1_意图路由 --> N1_5_规则配置 : CUSTOM_RULES

    N1_5_规则配置 --> N2_文档提取

    N2_文档提取 --> N3_合同分类
    N3_合同分类 --> N4_条款切分

    N4_条款切分 --> N5_A_AI审核 : AI_AUTO
    N4_条款切分 --> N5_B_规则引擎 : CUSTOM_RULES

    N5_A_AI审核 --> N6_法条检索

    N5_B_规则引擎 --> N5_C_AI二次审核 : 启用二次审核
    N5_B_规则引擎 --> N6_法条检索 : 跳过AI

    N5_C_AI二次审核 --> N6_法条检索

    N6_法条检索 --> N7_风险聚合
    N7_风险聚合 --> N7_5_标红渲染

    N7_5_标红渲染 --> N8_用户决策
    N8_用户决策 --> N8_甲乙方识别

    N8_甲乙方识别 --> N9a_自动生成 : risk≥81
    N8_甲乙方识别 --> N9b_律师复核 : 61≤risk≤80
    N8_甲乙方识别 --> N9c_人工中断 : risk<60或冲突

    N9a_自动生成 --> N10_邮件草稿 : 需要邮件
    N9b_律师复核 --> N10_邮件草稿 : 需要邮件
    N9c_人工中断 --> N10_邮件草稿 : 需要邮件

    N9a_自动生成 --> N14_最终交付 : 不需要邮件
    N9b_律师复核 --> N14_最终交付 : 不需要邮件
    N9c_人工中断 --> N14_最终交付 : 不需要邮件

    N10_邮件草稿 --> N11_邮件确认
    N11_邮件确认 --> N12_邮件发送
    N12_邮件发送 --> N13_发送记录
    N13_发送记录 --> N14_最终交付

    N14_最终交付 --> [*]
```
"""


# ============================================================
# 8. 演示入口
# ============================================================

def demo_scenario_1():
    """方案一：标准AI审核"""
    print("█" * 60)
    print("█  方案一演示：标准 AI 审核 + 用户决策")
    print("█" * 60)

    graph = build_contract_review_graph()
    state = make_initial_state(
        instruction="请帮我用AI审核这份劳动合同",
        rules=None,
    )
    final = graph.invoke(state)

    print("\n" + "█" * 60)
    print("█  最终摘要")
    print("█" * 60)
    print(f"  审核模式: {final.get('review_mode')}")
    print(f"  风险评分: {final.get('risk_score')}/100")
    print(f"  风险项数: {len(final.get('aggregated_risk_items', []))}")
    print(f"  律师转介: {final.get('lawyer_referral')}")
    return final


def demo_scenario_2():
    """方案二：自定义规则优先"""
    print("█" * 60)
    print("█  方案二演示：自定义规则优先 + AI辅助")
    print("█" * 60)

    graph = build_contract_review_graph()
    state = make_initial_state(
        instruction="按我的规则审核这份合同，启用AI辅助",
        rules="""
        审核规则：
        1. 违约金上限不得超过20%
        2. 竞业限制期必须≤1年
        3. 重点关注：知识产权归属、保密条款
        风险容忍度：低风险
        """,
    )
    final = graph.invoke(state)

    print("\n" + "█" * 60)
    print("█  最终摘要")
    print("█" * 60)
    print(f"  审核模式: {final.get('review_mode')}")
    print(f"  AI二次审核: {final.get('enable_ai_second_review')}")
    print(f"  风险评分: {final.get('risk_score')}/100")
    print(f"  风险项数: {len(final.get('aggregated_risk_items', []))}")
    print(f"  冲突项数: {len(final.get('conflict_items') or [])}")
    print(f"  规则评估: {final.get('rule_coverage_evaluation')}")

    print("\n" + "█" * 60)
    print("█  结构化风险报告")
    print("█" * 60)
    print(json.dumps(final.get("structured_report"), ensure_ascii=False, indent=2))
    return final


if __name__ == "__main__":
    print("""
╔══════════════════════════════════════════════════════════════╗
║         合同智能审核系统 - LangGraph 状态图                   ║
║         方案一: 标准AI审核 + 用户决策                        ║
║         方案二: 自定义规则优先 + AI辅助                       ║
╚══════════════════════════════════════════════════════════════╝
    """)

    demo_scenario_1()
    print("\n\n")
    demo_scenario_2()

    print("\n\n" + "█" * 60)
    print("█  Mermaid 状态图代码")
    print("█" * 60)
    print(generate_mermaid())
