# 📜 代码文字逻辑解析
# 本文件是 AI 法律助理(LangGraph 多智能体协作)流程中的"最终交付节点",
# 扮演整个合同审核流水线的"汇总输出"角色。它在前置节点(风险识别、合规审查、
# 法律检索等)完成分析后,将所有结构化结果汇聚为一份面向用户的 Markdown 报告。
# 核心逻辑包括:1) 从 AgentState 中读取综合评分、风险等级、风险清单、法条引用、
# 当事人信息等关键字段;2) 将英文风险等级映射为中文文案与 emoji 标识;3) 按
# 严重程度(critical>high>medium>low)对风险项排序,确保最严重的风险排在报告顶部;
# 4) 分段构建报告:基本信息、风险评估、风险清单、引用法条、律师转介提示;5) 根据
# 评分区间给出差异化的处置建议(自动输出/律师复核/强制人工);6) 将最终 Markdown
# 同时写入 state["final_report_markdown"] 与 state["output"],供下游节点或前端使用。
# 该节点是纯展示型节点,不调用 LLM,完全基于已有 state 数据进行格式化拼装,
# 可作为任何"结构化数据 → Markdown 报告"场景的迁移模板。
# 导入 AgentState 类型,它是整个 LangGraph 图中各节点共享的状态字典(TypedDict)
from __004__langgraph_more_nodes.agent_state import AgentState


def final_delivery_node(state: AgentState):
    """
    最终交付节点: 汇总合同审核全流程结果, 生成结构化 Markdown 报告。

    作用:
        作为 LangGraph 图的终点节点之一, 将前面各节点(风险识别、法律检索、
        合规审查等)写入 state 的结构化结果, 按照固定模板拼装成一份可读性强、
        含风险清单与法条引用的 Markdown 报告, 并同时写入 final_report_markdown
        与 output 两个字段, 供前端展示或下游节点消费。

    参数:
        state (AgentState): LangGraph 共享状态字典, 需包含以下可选键:
            - overall_risk_score (float): 综合风险评分(0-100)
            - risk_level (str): 风险等级("Low"/"Medium"/"High")
            - merged_risk_items (list[dict]): 合并去重后的风险项列表
            - citations (list[dict]): 引用的法条列表
            - party_a / party_b (str): 甲乙方名称
            - user_side (str): 用户立场(甲方/乙方)
            - need_lawyer_review (bool): 是否需要律师复核
            - contract_type (str): 合同类型
            - can_sign (str): 签约结论 "pass"/"conditional"/"no" (合规一票否决权的载体)
            - review_strategy (str): 审核策略, 当前仅 AI_AUTO 一档有实现
            - conflict_log (list[str]): 冲突消解记录 (报告可解释性)
            - presentation_order (str): 风险展示顺序, 当前仅 "compliance_first"

    返回值:
        AgentState: 更新后的状态字典, 新增 final_report_markdown 与 output 字段。

    可迁移性说明:
        本节点是纯字符串拼装逻辑, 不依赖 LLM 或外部服务, 可直接迁移到任何
        "结构化数据 → Markdown 报告" 的场景, 只需调整字段映射与报告模板即可。
    """
    # 打印日志, 标记进入最终报告生成阶段, 便于在控制台追踪节点执行顺序
    print("开始生成最终报告")
    # 从 state 读取综合风险评分, 默认 0(缺失时不报错, 保证节点健壮性)
    score = state.get("overall_risk_score", 0)
    # 读取风险等级, 默认 "Unknown"
    level = state.get("risk_level", "Unknown")
    # 读取已合并去重的风险项列表, 默认空列表
    merged = state.get("merged_risk_items", [])
    # 读取引用法条列表, 默认空列表
    citations = state.get("citations", [])
    # 读取甲方名称, 默认"甲方"
    party_a = state.get("party_a") or "未识别"
    # 读取乙方名称, 默认"乙方"
    party_b = state.get("party_b") or "未识别"
    # 读取用户立场(代表哪一方), 默认"Unknown"
    user_side = state.get("user_side", "Unknown")
    # 读取是否需要律师复核的布尔标志, 默认 False
    need_review = state.get("need_lawyer_review", False)
    # 读取合同类型(如"买卖"/"租赁"), 默认"未分类"
    contract_type = state.get("contract_type") or "未识别"
    # 读取任务类型, 用于区分报告标题(合同审核/合规审查/法律检索)
    task_type = state.get("task_type", "contract_review")

    # 风险等级映射
    # 将英文风险等级映射为中文展示文案, 若未匹配则原样返回 level
    level_text = {"Low": "低风险", "Medium": "中风险", "High": "高风险"}.get(level, level)
    # 将英文风险等级映射为 emoji 图标, 未匹配则用"❓"
    level_emoji = {"Low": "✅", "Medium": "⚠️", "High": "🔴"}.get(level, "❓")

    # 按严重程度分组
    # 定义严重程度的排序权重: critical 最前(0), low 最后(3), 用于后续 sorted
    sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    # 定义严重程度的中文文案映射
    sev_text = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}

    # 【展示顺序】消费 conflict_resolution_node 写入的 presentation_order。
    #   该字段此前一直被写入但零消费方, 报告只是按 merged 原序输出,
    #   "合规风险始终优先展示"的设计意图从未生效。
    #   合规风险的法律后果最刚性(违反强制性规定可能直接导致条款无效),
    #   因此同一严重程度下把合规源风险排在前面。
    #   风险项 source 取值: "compliance+contract" / "compliance_only" /
    #   "contract_only" / "冲突消解" / "数值校验" / "资信审查"。
    #   Python 的 sorted 是稳定排序, 同组内保持原有相对顺序。
    presentation_order = state.get("presentation_order") or "severity_first"
    if presentation_order == "compliance_first":
        sorted_risks = sorted(
            merged,
            key=lambda x: (
                0 if "compliance" in str(x.get("source", "")).lower() else 1,
                sev_order.get(x.get("severity", "medium"), 2),
            ),
        )
    else:
        # 兜底: 仅按严重程度排序, 缺失 severity 字段时默认权重为 2(中等)
        sorted_risks = sorted(merged, key=lambda x: sev_order.get(x.get("severity", "medium"), 2))

    # 构建报告
    # 创建行列表, 逐行追加报告内容, 最后用 "\n".join 拼接为完整 Markdown
    lines = []
    # 报告主标题（根据任务类型动态切换）
    _title_map = {
        "contract_review": "合同审核报告",
        "compliance_review": "合规审查报告",
        "legal_research": "法律检索报告",
    }
    _report_title = _title_map.get(task_type, "法律分析报告")
    lines.append(f"# 法智引擎 - {_report_title}\n")
    # "基本信息"小节标题
    lines.append(f"## 📋 基本信息\n")
    # 输出合同类型
    lines.append(f"- **合同类型**: {contract_type}")
    # 输出甲方
    lines.append(f"- **甲方**: {party_a}")
    # 输出乙方
    lines.append(f"- **乙方**: {party_b}")
    # 输出用户立场
    lines.append(f"- **用户立场**: {user_side}方")
    # review_strategy 已于 2026-08-29 删除: 规则引擎(rule_engine_node)未落地,
    #   仅 AI_AUTO 一档子图链路存在, 无策略维度可展示, 故不再输出"审核模式"行。
    # 追加空行, 保证 Markdown 段落间距
    lines.append("")

    # "风险评估"小节标题(含 emoji)
    lines.append(f"## {level_emoji} 风险评估\n")
    # 输出综合评分(满分 100)
    lines.append(f"- **综合评分**: {score}/100")
    # 输出风险等级中文文案
    lines.append(f"- **风险等级**: {level_text}")
    # 输出签约结论（来自 conflict_resolution_node 的合规审查决定）
    # 【法律实务意义】合规审查有一票否决权：
    #   can_sign="no" → 即使评分≥81，也**不得签约**
    #   can_sign="conditional" → 即使评分≥81，也**须整改后方可签约**
    can_sign = state.get("can_sign", "pass")
    can_sign_map = {"pass": "✅ 可签约", "conditional": "⚠️ 条件通过（须整改）", "no": "❌ 不得签约"}
    can_sign_text = can_sign_map.get(can_sign, "未知")
    lines.append(f"- **签约结论**: {can_sign_text}")
    # 合规否决权提示：若 can_sign=no，在报告中突出显示
    if can_sign == "no":
        lines.append(f"  > ⚡ **合规审查否决**: 存在违反强制性法律规定的风险, 建议**不得签约**")
    elif can_sign == "conditional":
        lines.append(f"  > ⚡ **合规审查预警**: 存在高风险合规项, **须整改后方可签约**")
    # 根据评分区间给出差异化的处置建议: 81+ 可自动输出; 61-80 需律师复核; 60 及以下强制人工
    if score >= 81:
        # 高分: 风险低, 可直接输出
        lines.append(f"- **处置建议**: 可自动输出, 建议关注标注风险项")
    elif score >= 61:
        # 中分: 需律师复核后交付
        lines.append(f"- **处置建议**: 需律师复核后交付")
    else:
        # 低分: 风险高, 强制人工签章
        lines.append(f"- **处置建议**: 强制人工签章, 建议重大修改")
    # 空行分隔
    lines.append("")

    # 仅当存在风险项时才输出"风险清单"小节
    if sorted_risks:
        # 风险清单标题, 含风险项总数
        lines.append(f"## 📝 风险清单(共{len(sorted_risks)}项)\n")
        # 遍历风险项, 从 1 开始编号
        for i, r in enumerate(sorted_risks, 1):
            # 读取当前风险项的严重程度, 默认 medium
            sev = r.get("severity", "medium")
            # 转换为中文文案
            sev_t = sev_text.get(sev, sev)
            # 读取风险来源(如"合同审核"/"合规审查")
            source = r.get("source", "")
            # 输出风险标题, 含编号、严重程度标签与风险描述
            lines.append(f"### {i}. [{sev_t}] {r.get('description', '未知风险')}")
            # 输出来源
            lines.append(f"- **来源**: {source}")
            # 若存在相关条款, 则输出
            if r.get("clause"):
                lines.append(f"- **相关条款**: {r['clause']}")
            # 若存在法律依据, 则输出
            if r.get("legal_basis"):
                lines.append(f"- **法律依据**: {r['legal_basis']}")
            # 若存在修改建议, 则输出
            if r.get("suggestion"):
                lines.append(f"- **修改建议**: {r['suggestion']}")
            # 每个风险项之间空行分隔
            lines.append("")

    # 【冲突消解说明】消费 conflict_resolution_node 写入的 conflict_log。
    #   该字段记录了"两条审查路线如何合并、哪条规则触发了否决"等决策依据,
    #   此前一直被写入但零消费方(只在 conflict_resolution 自己内部 print)。
    #   把它放进报告, 用户才能理解"为什么得出这个签约结论", 属于可解释性输出。
    conflict_log = state.get("conflict_log") or []
    if conflict_log:
        lines.append(f"## 🔀 冲突消解说明\n")
        for log in conflict_log:
            lines.append(f"- {log}")
        lines.append("")

    # 仅当存在引用法条时才输出"引用法条"小节
    if citations:
        # 引用法条标题, 含法条总数
        lines.append(f"## 📚 引用法条(共{len(citations)}条)\n")
        # 最多展示前 10 条, 避免报告过长
        for c in citations[:10]:
            # 法条所属法律名称
            title = c.get("title", "")
            # 条文编号(如"第585条")
            art = c.get("article_no", "")
            # 条文内容, 截取前 150 字符
            content = c.get("content", "")[:150]
            # 以列表项形式输出
            lines.append(f"- **{title} {art}**: {content}")
        # 空行分隔
        lines.append("")

    # 转介提示
    # "律师转介"小节标题
    lines.append(f"## ⚖️ 律师转介\n")
    # 根据是否需要律师复核, 给出不同提示
    if need_review:
        # 需要复核: 强调风险数量与评分, 建议转介律师
        lines.append(f"> 本合同存在{len(merged)}个风险点, 评分{score}分, **建议转介专业律师复核**。")
        # 免责声明: AI 仅辅助, 决策权归律师
        lines.append(f"> 法智引擎仅提供AI辅助审查, 最终决策权归律师。")
    else:
        # 无需复核: 提示风险较低, 可直接使用, 但仍提供转接选项
        lines.append(f"> 本合同风险较低, 可直接使用。如需进一步法律意见, 可一键转接专业律师。")
    # 空行分隔
    lines.append("")

    # 报告分隔线
    lines.append("---")
    # 报告末尾免责声明
    lines.append(f"*本报告由法智引擎AI生成, 仅供参考, 不构成法律意见。*")

    # 将所有行拼接为完整 Markdown 字符串
    report = "\n".join(lines)
    # 写入 state 的 final_report_markdown 字段, 供需要 Markdown 格式的下游使用
    state["final_report_markdown"] = report
    # 同时写入 output 字段, 作为节点的通用输出(前端/日志/下游节点默认读取此字段)
    state["output"] = report
    # 打印报告字符数, 便于调试与监控
    print(f"完成最终报告: {len(report)} 字符")
    # 返回更新后的 state, 供 LangGraph 继续流转
    return state


# 脚本直接运行时的自测入口
if __name__ == "__main__":
    # 构造一个包含示例风险项、法条引用、当事人信息与签约结论的测试 state
    s = AgentState(
        overall_risk_score=72.5, risk_level="Medium",
        can_sign="conditional",  # 合规审查结论：条件通过
        merged_risk_items=[
            # 同为 high 严重程度, 但来源不同 —— 用于验证 presentation_order 生效:
            # "compliance_only" 应排在 "contract_only" 之前
            {"severity": "high", "description": "免除人身损害赔偿责任", "source": "compliance_only",
             "clause": "第8条", "legal_basis": "民法典506条", "suggestion": "删除该条"},
            {"severity": "high", "description": "违约金过高", "source": "contract_only",
             "clause": "第3条", "legal_basis": "民法典585条", "suggestion": "降低比例"},
        ],
        citations=[{"title": "民法典", "article_no": "第585条", "content": "违约金..."}],
        party_a="A公司", party_b="B公司", contract_type="买卖", need_lawyer_review=True,
        # 以下两个字段此前零消费方, 本轮接线后应出现在报告中
        presentation_order="compliance_first",
        conflict_log=[
            "[规则2] 合规high降级为条件通过: 条款'第8条' - 免除人身损害赔偿责任",
            "[规则3] 双路均命中: 条款'第3条' 已合并为一条风险项",
        ],
    )
    # 调用节点并打印完整报告, 用于人工检查报告格式与新增小节
    print(final_delivery_node(s)["output"])
