"""相对方资信查询节点: 调用企查查API, 生成资信风险项"""
# ============================================================
# 文件名称: nodes/retrieval_nodes/credit_check_node.py
# 文件作用: 企查查相对方资信查询 (retrieval_subgraph 的 L3 收尾环节)
# ============================================================
# 【这个文件是干什么的？】
# 企查查相对方资信查询
#
# 【代码逻辑主线】
# 参见各函数前的【功能】【参数】【返回值】【逻辑】说明。
#
# 【新手建议】
# 先看主函数 -> 再看辅助函数。
#

# 📜 代码文字逻辑解析
# 本文件是法智引擎(LangGraph 多智能体系统)中的"相对方资信查询节点",
# 现作为 retrieval_subgraph (检索智能体) 的 L3 收尾环节之一运行,
# 在质量门重试结束、退出检索子图前执行。
# 其核心职责是: 基于 party_identify_node 识别出的甲方(party_a)与乙方(party_b)名称,
# 通过企查查开放平台 API 检索两家企业的工商/司法/经营全维度资信信息,
# 并将负面记录(失信被执行人、被执行人、经营异常、行政处罚、吊销/注销等)
# 转化为结构化的 credit_risk_items, 供 dual_review 子图的 risk_aggregate_node 融入综合风险评分。
# 节点采用 "真实API优先 + 模拟数据兜底" 两级容错:
# (1) 若 .env 中配置了 QICHACHA_APP_KEY/SECRET_KEY 且 requests 库可用 -> 真实 API;
# (2) 否则 -> 调用 QiChaChaClient 内建的 _build_mock_data 生成演示数据,
#     保证合同审核主流程不会因第三方服务中断而卡死。
# 【付费门控 2026-08-23 更新】真实 API 按次计费 -> 已改为 LangGraph interrupt()
# 真中断续跑: 首次进入且未确认时 interrupt(payload) 暂停图, 前端渲染确认 UI,
# 用户决策后 resume(True/False) 从暂停点续跑。不再使用 credit_confirm_needed
# 标志重调全流程的旧机制。Mock 模式免费, 直接查询。
# 主体提取与占位名过滤由上游 credit_precheck_node 一次性完成, 结果经
# state["credit_parties"] 传入本节点, 本节点不再自行正则提取。
# 资信风险项生成规则: 每类负面记录映射到对应严重程度, 并自动判断是否为
# "用户对立方", 对立方的风险会被加权并在 description 中明确标注 "对方XX"。

# 导入 AgentState: 节点函数的输入/输出数据契约(TypedDict)
from __004__langgraph_more_nodes.agent_state import AgentState
# 导入企查查客户端: 封装了签名、HTTP 请求、模拟数据降级
from common.qichacha_client import QiChaChaClient
# 主体提取 / 中断问询等共享工具统一收口到 common.retrieval_shared, 避免与 credit_precheck 重复定义
from common.retrieval_shared import (
    _ask_user_interrupt,
    _norm_name,
)

# 是否真正查询企查查, 完全由上游 credit_precheck_node 写入的 credit_check_needed 决定;
# 本节点不再做任务类型过滤 / 重复主体提取 (precheck 已是单一事实来源)。


def credit_check_node(state: AgentState):
    """相对方资信查询节点: 查询甲乙双方资信, 输出 6 个状态字段。

    【架构定位】
        本节点是 retrieval_subgraph 的 L3 收尾环节之一, 在质量门重试结束后、
        退出检索子图前执行。只负责"相对方资信"这一路 (企查查), 与合规/合同审查无关。
        产出的 credit_risk_items 由 dual_review 子图的 risk_aggregate_node 聚合。

    【任务类型判定 (信任上游预检)】
        contract_review / compliance_review 之外的任务由 credit_precheck_node 判定为
        credit_check_needed=False, 本节点据此早退空返回, 不调 API、不 interrupt、不阻塞主流程。
        本节点不在内部重复做任务类型过滤。

    【付费接口门控 (2026-08-23 改为 interrupt 真中断)】
        真实 API 模式(MCP-Bearer / AppKey-MD5)下按调用付费 —— 通过 LangGraph
        interrupt() 暂停图执行, 由前端弹窗询问用户; 用户经 graph.invoke(
        Command(resume=value)) 恢复后才真正调用企查查:
        - resume=False / None: 用户拒绝 -> 跳过查询(不计费)
        - resume=True: 用户确认 -> 调用付费 API
        Mock 模式(未配置密钥, 免费)或 interrupt 不可用/被禁用时, 直接查询或跳过,
        不再走"标志重跑"旧逻辑。

    读取字段:
        - credit_check_needed (bool): 上游预检判定结果 (False -> 直接空返回)
        - credit_parties (list): 上游预检提取并过滤后的待查主体名
        - user_side (str): 用户立场 A/B/Unknown (用于标记对立方)
    写入字段:
        - party_a_credit_info (Dict): 甲方资信详情
        - party_b_credit_info (Dict): 乙方资信详情
        - credit_risk_items (List[Dict]): 资信风险项列表
        - credit_check_success (bool): 真实 API 是否成功
        - human_intervention_needed (bool): interrupt 不可用 / 调用失败后置 True
        - human_intervention_prompt (str): 付费提醒文案

    触发条件:
        由上游 credit_precheck_node 写入的 credit_check_needed 标志驱动:
        precheck 判定为 False 时本节点直接空返回; 为 True 时基于 precheck 已提取的
        credit_parties 主体查询, 不调 API、不阻塞主流程。
    """
    print("检索 [L3·资信] 相对方资信查询 (企查查)")

    # 【2026-08 新增】前置预检结果: 若 credit_precheck 已判定无需资信, 直接跳过
    precheck_needed = state.get("credit_check_needed", None)
    if precheck_needed is False:
        print("  前置预检判定无需资信查询 (credit_check_needed=False), 跳过")
        return {
            "party_a_credit_info": {},
            "party_b_credit_info": {},
            "credit_risk_items": [],
            "credit_check_success": False,
            "human_intervention_needed": False,
            "human_intervention_prompt": "",
        }

    # 【信任预检】credit_precheck_node 已在检索循环前完成主体提取与占位名过滤,
    # 结果存于 state["credit_parties"]。本节点不再重复提取主体 / 跑正则兜底, 直接复用,
    # 与 precheck 保持单一事实来源, 避免"预判定说有主体、真查询却说没有"的矛盾。
    precheck_parties = [p for p in (state.get("credit_parties") or []) if p]
    party_a = _norm_name(precheck_parties[0]) if len(precheck_parties) >= 1 else ""
    party_b = _norm_name(precheck_parties[1]) if len(precheck_parties) >= 2 else ""
    user_side = state.get("user_side", "Unknown") or "Unknown"

    # 预检未识别到有效主体 -> 不调企查查, 直接空返回
    if not party_a and not party_b:
        print("  资信预检未识别到有效合同主体, 跳过资信查询")
        return {
            "party_a_credit_info": {},
            "party_b_credit_info": {},
            "credit_risk_items": [],
            "credit_check_success": False,
            "human_intervention_needed": False,
            "human_intervention_prompt": "",
        }

    # 实例化企查查客户端(构造函数内部会读取配置判断是否启用真实 API)
    client = QiChaChaClient()

    # ============== 付费接口门控: 真实 API -> interrupt() 暂停问用户 ==============
    # interrupt 不可用/被禁用(Mock 模式/子进程)时 _ask_user_interrupt 返回 None(拒绝),
    # 直接跳过查询; 真实 API 且未被禁用时暂停图, resume 后继续本段逻辑。
    if client.mode != "Mock":
        names = "、".join([n for n in (party_a, party_b) if n])
        decision = _ask_user_interrupt({
            "type": "qichacha_confirm",
            "parties": names,
            "message": (
                f"检测到合同相对方【{names}】。查询企查查资信信息需要调用付费接口"
                f"(企查查开放平台 API, 按次计费), 将获取对方工商/失信/被执行/经营异常/"
                f"行政处罚等资信记录。是否确认调用?"
            ),
        "reminder": "企查查 API 为按次计费接口, 确认后将使用付费额度。",
    }, label="企查查资信")
        # decision=False/None -> 用户拒绝; 仅 True 才继续查询
        if not decision:
            print("  企查查资信: 用户拒绝或未确认(降级), 跳过付费查询")
            return {
                "party_a_credit_info": {},
                "party_b_credit_info": {},
                "credit_risk_items": [],
                "credit_check_success": False,
                "human_intervention_needed": False,
                "human_intervention_prompt": "",
            }
        print(f"  企查查资信: 用户确认调用, 开始付费查询 (主体 {names})")

    # Mock 模式(免费)或用户已确认 -> 正常查询
    # 查询与风险构建整体包在 try/except 中: 任一异常均不击穿检索子图,
    # 返回空结果与 human_intervention_needed=True 提示人工介入。
    try:
        # 分别查询甲乙双方资信 (空名 -> 客户端返回 mock/空, 主流程不卡死)
        info_a = client.query_company_credit(party_a) if party_a else {}
        info_b = client.query_company_credit(party_b) if party_b else {}

        # 判断是否使用了真实 API: 任意一方 mock=False 即视为至少部分成功
        real_success = False
        if info_a and not info_a.get("mock", True):
            real_success = True
        if info_b and not info_b.get("mock", True):
            real_success = True

        # 生成资信风险项: 直接复用 client 内收口的风险判定 (severity / credit_category)
        credit_risks = []
        if party_a:
            credit_risks.extend(client.build_credit_risk_items(info_a, "甲方", party_a, user_side))
        if party_b:
            credit_risks.extend(client.build_credit_risk_items(info_b, "乙方", party_b, user_side))
    except Exception as e:
        print(f"  [ERROR] 企查查资信查询异常, 降级为空结果: {type(e).__name__}: {e}")
        return {
            "party_a_credit_info": {},
            "party_b_credit_info": {},
            "credit_risk_items": [],
            "credit_check_success": False,
            "human_intervention_needed": True,
            "human_intervention_prompt": f"企查查资信查询失败（{type(e).__name__}），请稍后重试或联系管理员。",
        }

    # 打印完成日志, 便于调试时快速核对
    print(
        f"  资信查询完成: 甲方信用分={info_a.get('credit_score', 'N/A') if info_a else 'N/A'} "
        f"(mock={info_a.get('mock', True) if info_a else True}), "
        f"乙方信用分={info_b.get('credit_score', 'N/A') if info_b else 'N/A'} "
        f"(mock={info_b.get('mock', True) if info_b else True}), "
        f"生成资信风险项={len(credit_risks)}条, 真实API={'成功' if real_success else '未启用/失败'}"
    )

    # 返回 partial dict (LangGraph 自动合并到全局 state, 避免原地改共享 state)
    return {
        "party_a_credit_info": info_a,
        "party_b_credit_info": info_b,
        "credit_risk_items": credit_risks,
        "credit_check_success": real_success,
        "human_intervention_needed": False,
        "human_intervention_prompt": "",
    }

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
