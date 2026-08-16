"""N8.5 相对方资信查询节点: 调用企查查API, 生成资信风险项"""
# ============================================================
# 文件名称: nodes/credit_check_node.py
# 文件作用: 企查查资信查询
# ============================================================
# 【这个文件是干什么的？】
# 企查查资信查询
#
# 【代码逻辑主线】
# 参见各函数前的【功能】【参数】【返回值】【逻辑】说明。
#
# 【新手建议】
# 先看主函数 -> 再看辅助函数。
#

# 📜 代码文字逻辑解析
# 本文件是法智引擎(LangGraph 多智能体系统)中新增的"相对方资信查询节点",
# 位于业务流程的 N8 甲乙方识别节点之后、N9 最终交付节点之前。
# 其核心职责是: 基于 party_identify_node 识别出的甲方(party_a)与乙方(party_b)名称,
# 通过企查查开放平台 API 检索两家企业的工商/司法/经营全维度资信信息,
# 并将负面记录(失信被执行人、被执行人、经营异常、行政处罚、吊销/注销等)
# 转化为结构化的 credit_risk_items, 供 risk_aggregate_node 融入综合风险评分。
# 节点采用 "真实API优先 + 模拟数据兜底" 两级容错:
# (1) 若 .env 中配置了 QICHACHA_APP_KEY/SECRET_KEY 且 requests 库可用 -> 真实 API;
# (2) 否则 -> 调用 QiChaChaClient 内建的 _build_mock_data 生成演示数据,
#     保证合同审核主流程不会因第三方服务中断而卡死。
# 资信风险项生成规则: 每类负面记录映射到对应严重程度, 并自动判断是否为
# "用户对立方", 对立方的风险会被加权并在 description 中明确标注 "对方XX"。

# 导入 AgentState: 节点函数的输入/输出数据契约(TypedDict)
from __004__langgraph_more_nodes.agent_state import AgentState
# 导入企查查客户端: 封装了签名、HTTP 请求、模拟数据降级
from common.qichacha_client import QiChaChaClient


def credit_check_node(state: AgentState):
    """
    相对方资信查询节点函数: 查询甲乙双方资信, 写入 4 个状态字段。

    作用:
        (1) 从 state 读取 party_a/party_b 名称 (跳过空名/通用名如"甲方");
        (2) 实例化 QiChaChaClient, 分别查询两家企业的资信信息;
        (3) 将原始查询结果写入 party_a_credit_info / party_b_credit_info;
        (4) 遍历两家企业的负面记录(失信/被执行人/经营异常/行政处罚/经营状态异常),
            生成 credit_risk_items 风险项, 并根据 user_side 判断是否为"对方风险",
            对方风险会加重扣分(在 risk_aggregate_node 中体现);
        (5) 写入 credit_check_success 标志(真实 API 至少一家成功才为 True)。

    参数:
        state (AgentState): LangGraph 共享状态字典。读取字段:
                            - party_a (str, 可选): 甲方名称
                            - party_b (str, 可选): 乙方名称
                            - user_side (str, 可选): 用户立场 A/B/Unknown
                            写入字段:
                            - party_a_credit_info (Dict): 甲方资信详情
                            - party_b_credit_info (Dict): 乙方资信详情
                            - credit_risk_items (List[Dict]): 资信风险项列表
                            - credit_check_success (bool): 真实 API 是否成功

    返回值:
        AgentState: 更新后的状态字典, 必含上述 4 个字段。

    可迁移性说明:
        本节点的"独立第三方数据接入 + 负面记录转风险项 + 用户立场加权"模式
        可迁移到任何需要"对手方画像"的业务场景, 例如:
        - 招投标系统: 投标方资质/业绩核验;
        - 供应链金融: 核心企业上下游信用评估;
        - 信贷审批: 借款人企业资信核查。
        替换 QiChaChaClient 为其它数据源(天眼查/启信宝/工商总局开放接口),
        并保持 _build_credit_risk_items 的字段输出契约不变即可迁移。
    """
    # 打印节点开始日志
    print("开始相对方资信查询")

    # 从 state 读取甲乙方名称与用户立场
    party_a = state.get("party_a", "") or ""
    party_b = state.get("party_b", "") or ""
    user_side = state.get("user_side", "Unknown") or "Unknown"

    # 实例化企查查客户端(构造函数内部会读取配置判断是否启用真实 API)
    client = QiChaChaClient()

    # 查询甲方资信 (若为空字符串, client 内部会直接返回 mock 数据 + note)
    info_a = client.query_company_credit(party_a) if party_a else {}
    # 查询乙方资信
    info_b = client.query_company_credit(party_b) if party_b else {}

    # 判断是否使用了真实 API: 任意一方 mock=False 即视为至少部分成功
    real_success = False
    if info_a and not info_a.get("mock", True):
        real_success = True
    if info_b and not info_b.get("mock", True):
        real_success = True

    # 生成资信风险项: 分别解析甲方与乙方
    credit_risks = []
    credit_risks.extend(_build_credit_risk_items(info_a, "甲方", party_a, user_side))
    credit_risks.extend(_build_credit_risk_items(info_b, "乙方", party_b, user_side))

    # 将 4 个资信字段写入 state
    state["party_a_credit_info"] = info_a
    state["party_b_credit_info"] = info_b
    state["credit_risk_items"] = credit_risks
    state["credit_check_success"] = real_success

    # 打印完成日志, 便于调试时快速核对
    print(
        f"完成相对方资信查询: 甲方信用分={info_a.get('credit_score', 'N/A') if info_a else 'N/A'} "
        f"(mock={info_a.get('mock', True) if info_a else True}), "
        f"乙方信用分={info_b.get('credit_score', 'N/A') if info_b else 'N/A'} "
        f"(mock={info_b.get('mock', True) if info_b else True}), "
        f"生成资信风险项={len(credit_risks)}条, 真实API={'成功' if real_success else '未启用/失败'}"
    )

    # 返回更新后的状态字典 (LangGraph 会自动合并到全局 state)
    return state


# ======================================================================
# 内部工具函数: 把单家企业的资信详情转换为结构化风险项
# ======================================================================
def _build_credit_risk_items(
    info: dict,
    party_label: str,
    party_name: str,
    user_side: str,
) -> list:
    """
    将单家企业的资信查询结果转换为 credit_risk_items 风险项列表。

    映射规则 (严重程度 ↔ 负面记录类型):
        - critical: 失信被执行人(老赖)、经营状态为吊销/注销
        - high:     多条被执行人、经营异常未移除、重大行政处罚
        - medium:   单条被执行人、已移除经营异常、一般行政处罚
        - low:      注册资本过低提示、股东结构过度集中等软提示

    立场加权:
        若 user_side=A 且当前 party_label=乙方 -> 该方是用户对立方, 风险标记
        is_counterparty=True (risk_aggregate_node 中会额外扣分)。反之 user_side=B
        且为甲方亦然。Unknown 时双方都不额外加权。

    参数:
        info (dict):        QiChaChaClient 返回的单家企业资信结构
        party_label (str):  "甲方" or "乙方"
        party_name (str):   企业全称(用于风险描述中展示)
        user_side (str):    用户立场 A / B / Unknown

    返回值:
        List[Dict]: 标准化风险项, 字段结构与 contract_risk_items 一致:
                    {source, clause, severity, description, suggestion, legal_basis,
                     credit_category, is_counterparty, party_label, party_name}
    """
    # 入参校验: info 为空直接返回空列表
    if not info or not isinstance(info, dict):
        return []

    risks = []

    # ------------------------------------------------------------------
    # 判定是否为"用户对立方": 对立方的风险会在 risk_aggregate 中额外扣分
    # ------------------------------------------------------------------
    is_counterparty = False
    if user_side == "A" and party_label == "乙方":
        # 用户是甲方, 当前分析乙方 -> 是对立方
        is_counterparty = True
    elif user_side == "B" and party_label == "甲方":
        # 用户是乙方, 当前分析甲方 -> 是对立方
        is_counterparty = True

    # 方便复用: 统一前缀 (在每条风险描述中标明是哪一方、什么公司)
    name_display = party_name if party_name else party_label
    prefix = f"{party_label}【{name_display}】"

    # ------------------------------------------------------------------
    # 维度 1: 经营状态 (吊销/注销 = critical, 停业 = medium)
    # ------------------------------------------------------------------
    basic = info.get("basic_info", {}) or {}
    status = str(basic.get("status", "")) if isinstance(basic, dict) else ""
    if status and ("吊销" in status or "注销" in status):
        risks.append({
            "source": "资信审查",
            "clause": f"{party_label}经营资质",
            "severity": "critical",
            "description": f"{prefix}经营状态为【{status}】，已丧失合法经营主体资格，合同存在主体无效风险。",
            "suggestion": f"立即终止与该主体的合作，或更换具备合法经营资质的签约主体。如已签署合同，建议咨询律师评估合同效力与救济途径。",
            "legal_basis": "《民法典》第一百四十三条、第六十八条；《市场主体登记管理条例》第三十一条。",
            "credit_category": "经营状态异常",
            "is_counterparty": is_counterparty,
            "party_label": party_label,
            "party_name": party_name,
        })
    elif status and ("停业" in status or "清算" in status):
        risks.append({
            "source": "资信审查",
            "clause": f"{party_label}经营资质",
            "severity": "medium",
            "description": f"{prefix}经营状态为【{status}】，履约能力存在重大不确定性。",
            "suggestion": f"暂缓签约或要求对方提供履约担保（如保证金、连带保证人）；核实清算/停业进展后再决策。",
            "legal_basis": "《民法典》第五百二十七条（不安抗辩权）。",
            "credit_category": "经营状态异常",
            "is_counterparty": is_counterparty,
            "party_label": party_label,
            "party_name": party_name,
        })

    # ------------------------------------------------------------------
    # 维度 2: 失信被执行人 (每条 = critical)
    # ------------------------------------------------------------------
    dishonest = info.get("dishonest", []) or []
    for idx, d in enumerate(dishonest, 1):
        situation = d.get("situation", "有履行能力而拒不履行")
        case_no = d.get("case_no", "")
        court = d.get("court", "")
        risks.append({
            "source": "资信审查",
            "clause": f"{party_label}失信记录",
            "severity": "critical",
            "description": f"{prefix}存在失信被执行人记录（俗称「老赖」）第{idx}条：案号【{case_no}】，执行法院【{court}】，失信情形【{situation}】。表明该主体有能力履行却拒不履行债务，诚信度极差。",
            "suggestion": f"强烈建议拒绝合作；如确需合作，必须要求其提供足额财产抵押或有实力的第三方连带担保，并约定严格的违约条款。",
            "legal_basis": "《最高人民法院关于公布失信被执行人名单信息的若干规定》第一条；《民法典》第五百二十七条。",
            "credit_category": "失信被执行人",
            "is_counterparty": is_counterparty,
            "party_label": party_label,
            "party_name": party_name,
        })

    # ------------------------------------------------------------------
    # 维度 3: 被执行人 (单条 = medium, ≥3 条或仍在执行中 = high)
    # ------------------------------------------------------------------
    executed = info.get("executed", []) or []
    still_running = sum(1 for e in executed if "执行中" in str(e.get("status", "")))
    exe_severity = "low"
    if len(executed) >= 3 or still_running >= 2:
        exe_severity = "high"
    elif len(executed) >= 1 or still_running >= 1:
        exe_severity = "medium"
    if executed:
        # 合并成 1 条总览风险（避免列表过长），并列出关键指标
        total_amount_mention = ""
        amounts = [e.get("exec_target", "") for e in executed if e.get("exec_target")]
        if amounts:
            total_amount_mention = f"，涉及执行标的约 {sum(_parse_amount(a) for a in amounts)} 万元" if False \
                else f"，典型执行标的如 {amounts[0]}"
        risks.append({
            "source": "资信审查",
            "clause": f"{party_label}被执行人记录",
            "severity": exe_severity,
            "description": f"{prefix}存在 {len(executed)} 条被执行人记录（仍在执行中 {still_running} 条）{total_amount_mention}。表明该公司存在较多未了结的债务纠纷，偿债能力存疑。",
            "suggestion": f"建议对其进行尽职调查，了解涉诉原因与实际清偿情况；签约前要求提供近期财务报表，并可适当提高保证金比例或增设担保条款。",
            "legal_basis": "《民事诉讼法》第二百三十一条；《民法典》第五百二十七条。",
            "credit_category": "被执行人",
            "is_counterparty": is_counterparty,
            "party_label": party_label,
            "party_name": party_name,
        })

    # ------------------------------------------------------------------
    # 维度 4: 经营异常 (未移除 = high, 已移除 = low)
    # ------------------------------------------------------------------
    abnormal = info.get("abnormal", []) or []
    not_removed = [a for a in abnormal if not a.get("remove_date")]
    removed = [a for a in abnormal if a.get("remove_date")]
    # 未移除经营异常
    if not_removed:
        for a in not_removed:
            reason = a.get("reason", "经营异常")
            authority = a.get("authority", "")
            put_date = a.get("put_date", "")
            risks.append({
                "source": "资信审查",
                "clause": f"{party_label}经营异常",
                "severity": "high",
                "description": f"{prefix}目前被列入经营异常名录（尚未移除）：列入原因【{reason}】，列入机关【{authority}】，列入日期【{put_date}】。该异常可能影响企业开票、资质办理、招投标等经营活动。",
                "suggestion": f"要求对方立即办理经营异常移除手续，并在合同中约定因经营异常导致履约受阻的违约责任；移除完成前暂缓付款。",
                "legal_basis": "《企业信息公示暂行条例》第八条、第十七条；《市场主体登记管理条例实施细则》第六十三条。",
                "credit_category": "经营异常(未移除)",
                "is_counterparty": is_counterparty,
                "party_label": party_label,
                "party_name": party_name,
            })
    # 已移除经营异常 (历史记录, 软提示)
    if removed:
        risks.append({
            "source": "资信审查",
            "clause": f"{party_label}经营异常历史",
            "severity": "low",
            "description": f"{prefix}历史上存在 {len(removed)} 条经营异常记录（已移除）。虽已整改，但提示其内部合规管理曾有疏漏。",
            "suggestion": f"可在合同中适当增加合规性陈述与保证条款，要求对方承诺持续合法合规经营。",
            "legal_basis": "《企业信息公示暂行条例》第八条。",
            "credit_category": "经营异常(已移除)",
            "is_counterparty": is_counterparty,
            "party_label": party_label,
            "party_name": party_name,
        })

    # ------------------------------------------------------------------
    # 维度 5: 行政处罚 (≥3 条 = high, 1~2 条 = medium)
    # ------------------------------------------------------------------
    penalties = info.get("penalties", []) or []
    if penalties:
        pen_severity = "high" if len(penalties) >= 3 else "medium"
        # 合并展示
        types = list({p.get("reason", "违规") for p in penalties})
        risks.append({
            "source": "资信审查",
            "clause": f"{party_label}行政处罚",
            "severity": pen_severity,
            "description": f"{prefix}近年内存在 {len(penalties)} 条行政处罚记录，涉及【{'、'.join(types[:3])}{'等' if len(types) > 3 else ''}】，表明其在合规经营方面存在薄弱环节。",
            "suggestion": f"重点核查处罚类型与合同履行是否相关（如环保处罚影响供货、税务处罚影响开票）；可在合同中设置合规违约条款。",
            "legal_basis": "《民法典》第五百零九条（全面履行义务）、第五百二十七条。",
            "credit_category": "行政处罚",
            "is_counterparty": is_counterparty,
            "party_label": party_label,
            "party_name": party_name,
        })

    # ------------------------------------------------------------------
    # 维度 6: 软风险 (资信评分 < 60 = high, 60~80 = medium, 股东过度集中 = low)
    # ------------------------------------------------------------------
    credit_score = info.get("credit_score")
    if isinstance(credit_score, (int, float)) and credit_score < 60:
        risks.append({
            "source": "资信审查",
            "clause": f"{party_label}综合资信评分",
            "severity": "high",
            "description": f"{prefix}综合资信评分为 {credit_score} 分（满分 100），处于较低水平，整体履约能力与商业信誉偏弱。",
            "suggestion": f"谨慎合作，建议提高预付款比例下限、增设履约保函或第三方担保，并分段控制付款节奏。",
            "legal_basis": "《民法典》第五百二十七条（不安抗辩权）。",
            "credit_category": "综合资信偏弱",
            "is_counterparty": is_counterparty,
            "party_label": party_label,
            "party_name": party_name,
        })
    elif isinstance(credit_score, (int, float)) and credit_score < 80:
        risks.append({
            "source": "资信审查",
            "clause": f"{party_label}综合资信评分",
            "severity": "low",
            "description": f"{prefix}综合资信评分为 {credit_score} 分（满分 100），属于中等水平，存在小幅资信瑕疵。",
            "suggestion": f"合作中注意常规风控，如分阶段付款、保留适当质保金等。",
            "legal_basis": "《民法典》第五百零九条。",
            "credit_category": "综合资信一般",
            "is_counterparty": is_counterparty,
            "party_label": party_label,
            "party_name": party_name,
        })

    # 股东过度集中（单人持股 ≥ 90% = low，提示一人有限责任公司风险）
    holders = info.get("shareholders", []) or []
    if holders:
        # 尝试从 share_ratio 字符串中解析数值比例, 找到最大股东持股
        max_ratio = 0.0
        for h in holders:
            ratio_str = str(h.get("share_ratio", "0%")).replace("%", "").strip()
            try:
                ratio = float(ratio_str)
            except ValueError:
                ratio = 0.0
            if ratio > max_ratio:
                max_ratio = ratio
        if max_ratio >= 90.0:
            risks.append({
                "source": "资信审查",
                "clause": f"{party_label}股权集中度",
                "severity": "low",
                "description": f"{prefix}股权高度集中（单一大股东持股 {max_ratio}%），若为自然人独资的一人有限责任公司，存在股东个人财产与公司财产混同的法律风险。",
                "suggestion": f"可要求实际控制人/大股东承担个人连带保证责任；重大合同可查询其工商档案确认是否为一人公司。",
                "legal_basis": "《公司法》第六十三条（一人有限责任公司股东连带责任推定）。",
                "credit_category": "股权过度集中",
                "is_counterparty": is_counterparty,
                "party_label": party_label,
                "party_name": party_name,
            })

    return risks


# ======================================================================
# 小型辅助: 从 "XXX万元" 字符串中解析出数字万元数 (供被执行人金额汇总用, 失败返回 0)
# ======================================================================
def _parse_amount(text: str) -> float:
    """从"100万元"/"5000万"/"200万人民币"等简单格式中提取数值(万元)"""
    try:
        # 提取第一段连续数字(含小数点)
        import re
        m = re.search(r"([\d\.]+)", str(text))
        if not m:
            return 0.0
        val = float(m.group(1))
        # 若原文有 "亿" 则 ×10000 转万元
        if "亿" in str(text):
            val *= 10000
        return val
    except Exception:
        return 0.0


# ======================================================================
# 模块自测入口: 直接运行本文件时验证资信风险项生成逻辑
# ======================================================================
if __name__ == "__main__":
    # 构造测试状态: 甲方信用良好, 乙方存在大量负面记录
    s = AgentState(
        party_a="华为技术有限公司",
        party_b="某经营异常失信商贸有限公司",
        user_side="A",   # 用户是甲方, 乙方为对立方 -> 乙方风险 is_counterparty=True
    )
    # 调用节点
    result = credit_check_node(s)
    # 打印结果概览
    print(f"\n[自测] 资信风险项共 {len(result.get('credit_risk_items', []))} 条:")
    for i, r in enumerate(result.get("credit_risk_items", []), 1):
        print(f"  {i}. [{r['severity']}] {r['source']} | "
              f"对方={r.get('is_counterparty')} | 分类={r.get('credit_category')} | "
              f"{r.get('description', '')[:60]}...")
