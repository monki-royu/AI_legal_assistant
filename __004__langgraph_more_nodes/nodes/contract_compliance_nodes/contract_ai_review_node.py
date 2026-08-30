"""合同审核节点 —— 接收文档预处理 + 检索结果 + 合规审查结果,
利用 LLM 抽取本合同条款的商业/法律/操作风险项。

【架构定位】
    在去掉 credit_precheck_node / credit_check_node 中转层后,
    contract_review 链路为:
        ... → context_pack → compliance_review → contract_ai_review (本节点)
              → conflict_resolution → numeric_validate → risk_aggregate → final_delivery
    合规审查已先于本节点执行, compliance_risk_items 已写入 State。

【本节点职责(单一职责)】
    ★ 只做一件事: 从合同文本中抽取「商业/法律/操作风险项」contract_risk_items,
      作为 conflict_resolution 节点的输入之一。
    ★ 不做冲突消解 (由 conflict_resolution 负责, 遵守「合规优先」)
    ★ 不做数值校验 (由 numeric_validate 负责, 基于 YAML 规则确定性校验)
    ★ 不算综合评分/风险等级 (由 risk_aggregate 负责)
    ★ 不下签约结论 (由 conflict_resolution + risk_aggregate 负责)
    ★ 不生成最终报告 (由 final_delivery 负责)

【重要】历史上本节点曾以「一站式 LLM」自居, 一次性产出 post_conflict_risk_items /
final_report_markdown / overall_risk_score / risk_level / can_sign / numeric_risk_items /
output 共 7 个字段, 但经代码核查, 这 7 个字段全部被下游节点无条件覆盖:
    - post_conflict_risk_items  → conflict_resolution_node 覆盖 (:331)
    - numeric_risk_items        → numeric_validate_node 覆盖 (:461)
    - overall_risk_score/level  → risk_aggregate_node 覆盖 (:235/:237)
    - can_sign                  → conflict_resolution_node 覆盖 (:332)
    - final_report_markdown     → final_delivery_node 覆盖 (:224)
    - output                    → final_delivery_node 覆盖 (:226)
故本节点已精简为「只回写 contract_risk_items」, 其余字段一律不产出,
避免 LLM 无效算力浪费与误导性的「死输出」。

【逻辑主线】
    1. 读取 State 中三路数据 + extracted_numerics
    2. 构造 Prompt, 引导 LLM 仅抽取 contract_risk_items (含 severity, 供下游聚合)
    3. 解析 LLM 输出的 JSON, 写入 contract_risk_items
    4. 异常降级: 失败则回写空列表, 保证流程不崩溃
"""

import json
import re

from langchain_core.messages import HumanMessage

from common.llm import my_llm
from common.review_context_utils import (
    build_review_text,       # 把 doc_segments 组装成带编号的待审查材料
    build_law_block,         # 把 review_context_bundle 渲染成法条依据 + 未覆盖提示
    normalize_segment_ids,   # 把 LLM 输出的 segment_id 归一化
)
from __004__langgraph_more_nodes.agent_state import AgentState


# ======================================================================
# 正则风险信号预筛 (商业条款风险信号)
# ======================================================================
_CONTRACT_RISK_SIGNALS = {
    "违约金过高":           r"违约金.*(?:每日|每.*日|日.*)[百千万]分[之][五一二三四五六七八九]",
    "单方解除权":           r"(?:甲方|乙方|出租方|承租方).*(?:有权|可).*(?:单方|随时).*(?:解除|终止|取消)",
    "连带责任":             r"(?:连带|无限).*责任",
    "管辖地偏向":           r"(?:管辖|诉讼|仲裁).*(?:甲方|乙方|出租方).*所在地",
    "自动续约":             r"自动.*(?:续约|续期|延长)",
    "付款条件不利":         r"(?:预付|全额|一次性).*(?:付款|支付).*(?:[百千万]分[之][五一二三四五六七八九]|[百千万]%])",
    "知识产权归属":         r"知识产权.*(?:归|属于|所有)",
    "保密义务不对等":       r"保密.*(?:义务|责任).*(?:甲方|乙方).*(?:单方|仅)",
}

_CONTRACT_SEVERITY_MAP = {
    "违约金过高": "high",
    "单方解除权": "high",
    "连带责任":   "critical",
    "管辖地偏向": "medium",
    "自动续约":   "low",
    "付款条件不利": "medium",
    "知识产权归属": "medium",
    "保密义务不对等": "low",
}


def _prescreen_contract(raw_text: str, doc_segments: list) -> str:
    """对合同文本做正则预筛,返回重点关注清单 prompt 片段"""
    if not raw_text and not doc_segments:
        return ""
    if not raw_text:
        raw_text = "\n".join(s.get("content", "") for s in (doc_segments or []))

    hits = []
    for signal_name, pattern in _CONTRACT_RISK_SIGNALS.items():
        matches = re.findall(pattern, raw_text)
        if matches:
            severity = _CONTRACT_SEVERITY_MAP.get(signal_name, "medium")
            hits.append(f"  - 【{severity.upper()}】{signal_name} (命中 {len(matches)} 处)")

    if hits:
        return (
            "\n【规则预筛 · 重点关注清单】\n"
            "以下为本地正则规则扫描到的潜在风险信号,请重点核实:\n"
            + "\n".join(hits)
            + "\n--- 注意:规则层只做提示不下结论,最终以你(LLM)的语义判断为准 ---\n"
        )
    return ""


def contract_ai_review_node(state: AgentState):
    """
    综合合同审核节点 —— 单一职责: 抽取合同商业/法律/操作风险项 (contract_risk_items)。

    【架构说明】V3 串行架构下, 本节点仅在 task_type == "contract_review" 时被调用
    (由 dual_review_subgraph 的条件路由保证), 不再需要内部守卫逻辑.

    读取字段:
        - doc_segments / doc_text / party_a / party_b / user_side / contract_type
        - review_context_bundle (含 citations, research_context)
        - compliance_risk_items (合规审查已先于本节点执行, 仅作上下文参考)
        - extracted_numerics (预提取的数值项, 仅作上下文参考, 数值校验由 numeric_validate 负责)
        - review_strategy / custom_rules / rule_risk_items (可选)
        - retrieval_queries["contract_review"] (检索聚焦, 指明应重点对照的法条方向)
    写入字段:
        - contract_risk_items: 商业/法律/操作风险项列表 (下游 conflict_resolution 的输入)
    """

    print("=== 综合合同审核(抽取合同风险项, 单一职责) ===")

    # ---- Step 1: 读取三路数据 ----
    # 文档预处理路
    doc_segments = state.get("doc_segments", []) or []
    doc_text = state.get("doc_text", "") or ""
    party_a = state.get('party_a', "") or ""
    party_b = state.get("party_b", "") or ""
    user_side = state.get("user_side", "Unknown") or "Unknown"
    contract_type = state.get("contract_type", "未知类型") or "未知类型"

    # 检索智能体路
    review_bundle = state.get("review_context_bundle", {}) or {}
    citations = state.get("citations", []) or []

    # 合规审查路 (已先执行, 仅作上下文)
    compliance_items = state.get("compliance_risk_items", []) or []

    # 数值抽取路 (仅作上下文, 校验由 numeric_validate 负责)
    extracted_numerics = state.get("extracted_numerics", {}) or {}

    # 检索聚焦(本视角查询): 来自 llm_query_extract 的"合同审核视角"查询集合,
    # 让本合同审核大模型明确应重点对照的法条检索方向。
    retrieval_queries = state.get("retrieval_queries", {}) or {}
    _contract_focus = retrieval_queries.get("contract_review", []) or []
    contract_focus_block = ""
    if _contract_focus:
        _cq_text = "\n".join(f"  - {q}" for q in _contract_focus[:12])
        contract_focus_block = f"""
【检索聚焦 · 合同审核视角查询(供你重点对照的法条检索方向)】
{_cq_text}
"""

    # ---- Step 2: 组装审查材料 ----
    review_text = build_review_text(doc_segments, doc_text)
    law_block = build_law_block(review_bundle, citations)
    prescreen_hint = _prescreen_contract(doc_text, doc_segments)

    # ---- Step 3: 规则命中注入 ----
    # 规则引擎(rule_engine_node)尚未落地, 自定义规则审核路径不存在,
    # 合同审核恒走 AI_AUTO 子图链路, rule_injection 恒为空。
    rule_injection = ""

    # ---- Step 4: 合规审查结果注入 (仅作上下文, 冲突消解由下游负责) ----
    compliance_block = ""
    if compliance_items:
        comp_text = "\n".join(
            f"  - 【{r.get('severity', 'medium').upper()}】"
            f"单元#{r.get('segment_id', '?')} | {r.get('clause', '')[:60]} | "
            f"{r.get('description', '')[:100]}"
            for r in compliance_items
        )
        compliance_block = f"""
【前置合规审查结果(仅供你理解背景, 不要在本节点做冲突消解)】
以下为合规审查智能体独立产出的合规风险项, 仅供你理解合同背景:
{comp_text}
--- 注意 ---
本节点只抽取合同自身的商业/法律/操作风险, 不做合规与商业的冲突消解
(那一步由下游 conflict_resolution 节点按「合规优先」原则统一处理)。
"""

    # ---- Step 5: 数值项注入 (仅作上下文, 数值校验由 numeric_validate 负责) ----
    numeric_block = ""
    if extracted_numerics:
        num_items = "\n".join(
            f"  - {k}: {v}" for k, v in extracted_numerics.items()
        )
        numeric_block = f"""
【预提取数值项(仅供你理解背景, 不要单独输出数值校验结果)】
{num_items}
--- 说明 ---
以上数值的法定合规性由下游 numeric_validate 节点基于 YAML 规则确定性校验。
你只需在 contract_risk_items 中, 就明显异常的数值(如违约金过高、利率超上限)
标注对应的风险项即可。
"""

    # ---- Step 6: 构造 Prompt (仅要求 contract_risk_items) ----
    prompt = f"""你是一位资深合同审核律师。请从 {user_side} 方立场, 审核以下 {contract_type} 合同,
逐条抽取合同的商业 / 法律 / 操作风险项。

【待审查材料】
{review_text}
{prescreen_hint}
{law_block}
{compliance_block}
{numeric_block}
{contract_focus_block}
{rule_injection}

【输出规范】
你必须返回一个严格的 JSON 对象(外层是 {{}},不是数组), 仅包含以下一个字段:

1. "contract_risk_items": [
       {{"segment_id": <int|null>, "clause": "...", "risk_type": "商业/法律/操作",
         "severity": "critical/high/medium/low", "description": "...",
         "suggestion": "...", "legal_basis": "..."}}
   ]
   说明: 只抽取本合同条款自身的风险, 不要做合规与商业的冲突消解,
   也不要生成报告或下签约结论 (那些由下游节点负责)。

只输出 JSON,不要额外解释。如果无任何风险,contract_risk_items 返回空数组 []。
"""

    # ---- Step 7: 调用 LLM + 解析 ----
    try:
        resp = my_llm.invoke([HumanMessage(content=prompt)])
        content = resp.content.strip()

        # 剥离 ```json ... ``` 包裹
        if "```" in content:
            start = content.find("{")
            end = content.rfind("}") + 1
            content = content[start:end] if start >= 0 and end > start else content

        result = json.loads(content)

        # 仅提取本节点职责字段; 其余字段(评分/报告/消解/签约结论)由下游节点产出。
        # 局部变量, 不原地写 state, 避免并行 fan-out 下与 compliance_review 同键写入冲突。
        contract_risk_items = result.get("contract_risk_items", [])

        # segment_id 归一化
        if contract_risk_items:
            contract_risk_items = normalize_segment_ids(
                contract_risk_items, doc_segments
            )

        print(f"  合同风险项抽取完成: {len(contract_risk_items)}项")

    except Exception as e:
        print(f"⚠️ 综合合同审核异常: {e}")
        # 降级:安全默认值
        contract_risk_items = []

    # 返回 partial update (仅本节点产物 contract_risk_items, 不返回整个 state 对象,
    # 兼容 LangGraph 并行 fan-out: 多个并行节点各自返回独立字段集)
    return {
        "contract_risk_items": contract_risk_items,
    }


if __name__ == "__main__":
    # 快速自测
    s = AgentState(
        doc_text="甲方:北京科技有限公司\n乙方:上海贸易有限公司\n"
                 "第1条 甲方应于合同签订后3日内支付全部货款。\n"
                 "第2条 若乙方逾期交货,每日按货款总额千分之五支付违约金。\n"
                 "第3条 任何一方可随时单方解除本合同。",
        user_side="A",
        contract_type="买卖合同",
    )
    out = contract_ai_review_node(s)
    print(json.dumps({
        "contract_count": len(out.get("contract_risk_items", [])),
    }, ensure_ascii=False, indent=2))
