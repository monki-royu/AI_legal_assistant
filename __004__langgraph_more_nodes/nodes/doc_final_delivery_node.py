# -*- coding: utf-8 -*-
"""
法律文书生成 - 最终交付节点 (N_doc7)
====================================

【功能】
汇总前面所有节点的产物（draft_content, cited_laws, risks, similar_cases），
组装为统一格式的最终交付物，并持久化到历史记录（通过 common.history_store）。

输出：
  - final_document: 最终排版文书（Markdown，含封面/目录/正文/引用/风险提示）
  - document_id: 持久化后的记录 ID（供前端跳转详情页）

【流程位置】（文书生成链路 6 步中的最后一步 [6/6]，即终点 END）
  [5/6] 风险提示 / [5b/6] 类案推荐 → [6/6] 最终交付（本节点）→ END

【设计】
与合同审核的 final_delivery_node 共享"纯确定性逻辑 + 格式拼装"的设计理念，
不调用 LLM，保证最终交付 100% 由结构化数据驱动（Deterministic & Data-driven）。
同时将记录写入 HistoryStore（历史存储），支持前端历史记录页展示和后续导出
（通过 __005__fastapi 的导出接口）。

【下游】
END — 本节点是文书生成链路的终点（Terminal Node）。
"""
# 导入标准库 datetime：生成文书封面上的"生成日期"时间戳
from datetime import datetime
# 导入 AgentState 类型：LangGraph 图中各节点共享的状态字典（TypedDict）
from __004__langgraph_more_nodes.agent_state import AgentState
# 导入历史记录存储实例（store）：把最终交付物持久化，供历史页/导出接口使用
from common.history_store import store as history_store
# 导入微调数据收集工具：记录本节点的输入/输出为微调样本（可选旁路，失败静默）
from common.finetune_utils import collect_ft_sample


def doc_final_delivery_node(state: AgentState):
    """最终交付节点: 组装最终文书 + 持久化历史记录。"""
    # 【功能】汇总所有前置节点产物，组装为一份完整的 Markdown 法律文书，
    #         并写入历史记录存储（HistoryStore）持久化。
    # 【参数】
    #     state (AgentState): LangGraph 共享状态字典，本节点读取：
    #         - draft_content (str): 条款填充节点生成的文书草稿正文
    #         - cited_laws (list[dict]): 法条校验节点输出的已校正引用列表
    #         - risks (list[dict]): 风险提示节点输出的风险列表
    #         - similar_cases (list[dict]): 类案推荐节点输出的相似案例列表
    #         - case_summary (dict): 案情分析结果（含 parties 当事人、claims、facts）
    #         - template_name (str): 文书类型名称（如"民事起诉状"）
    #         - dispute_type (str): 纠纷类型
    #         - law_validation (dict): 法条校验汇总（含 summary 人读文案）
    #         - document_type (str, 可选): 文书类型编码（如"complaint"）
    # 【返回值】
    #     dict（合并进 state），包含：
    #         - final_document: str，最终排版文书（Markdown 全文）
    #         - document_id: str|None，持久化记录 ID（失败时为 None）
    #         - output: str，与 final_document 相同（通用输出字段，供前端读取）
    # 【逻辑】
    #     1. 读取各前置节点产物（全部带默认值兜底）；
    #     2. 按"封面 → 正文 → 引用法条 → 法条校验日志 → 风险提示 → 相似案例
    #        → 免责声明"的顺序逐行拼装 Markdown（lines 列表 + join）；
    #     3. 调用 history_store.store 持久化（task_type=docgen），拿 document_id；
    #     4. 持久化失败时打印告警并将 document_id 置为 None（不中断流程）；
    #     5. 返回 final_document / document_id / output 三个字段。
    # 打印日志：标记进入文书生成第 6 步"最终交付"
    print("文书生成 [6/6] 最终交付")

    # ---- 读取各节点的产物（Read Node Outputs）----
    # 读取文书草稿正文，缺失时兜底为空字符串
    draft = state.get("draft_content", "") or ""
    # 读取已校正的引用法条列表，缺失时兜底为空列表
    cited_laws = state.get("cited_laws", []) or []
    # 读取风险列表，缺失时兜底为空列表
    risks = state.get("risks", []) or []
    # 读取相似案例列表，缺失时兜底为空列表
    similar_cases = state.get("similar_cases", []) or []
    # 读取案情分析结果，缺失时兜底为空字典
    case_summary = state.get("case_summary", {}) or {}
    # 读取文书类型名称，缺失时兜底为"法律文书"
    template_name = state.get("template_name", "法律文书")
    # 读取纠纷类型，缺失时为空字符串
    dispute_type = state.get("dispute_type", "")
    # 读取法条校验汇总结果，缺失时兜底为空字典
    law_validation = state.get("law_validation", {}) or {}

    # ---- 组装最终文书(封面 + 正文 + 引用 + 风险提示 + 免责声明) ----
    # 创建行列表：逐行追加 Markdown 内容，最后用 "\n".join 拼接为完整文书
    lines = []
    # ── 封面（Cover）──
    # 一级标题：文书类型名称（如"# 民事起诉状"），\n 保证标题后换行
    lines.append(f"# {template_name}\n")
    # 封面信息行：纠纷类型
    lines.append(f"**纠纷类型**: {dispute_type}")
    # 封面信息行：生成日期（datetime.now() 取当前时间，strftime 格式化为 年-月-日 时:分）
    lines.append(f"**生成日期**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    # 封面信息行：生成引擎署名（"法智引擎 · 文书生成智能体"），\n 分隔与正文
    lines.append(f"**生成引擎**: 法智引擎 · 文书生成智能体\n")
    # 水平线（---）：封面与正文之间的视觉分隔
    lines.append("---\n")

    # ── 正文（Body）──
    # 直接追加条款填充节点生成的草稿正文（draft 本身是 Markdown 多行文本）
    lines.append(draft)
    # 水平线：正文与后面"引用法条"小节之间的分隔
    lines.append("\n---\n")

    # ── 引用法条（Cited Laws）──
    # 仅当存在引用法条（cited_laws 非空）时才输出该小节，避免空标题
    if cited_laws:
        # 小节标题：二级标题 + 📚 图标
        lines.append("## 📚 引用法条\n")
        # 遍历每条已校正的法条引用
        for c in cited_laws:
            # 读取校正标记 note（如"已根据知识库校正"），缺失时为空字符串
            note = c.get("note", "")
            # 构造标记字符串：有 note 则显示为" (note内容)"，否则为空（不显示括号）
            note_str = f" ({note})" if note else ""
            # 输出列表项：加粗法律全称 + 条号 + 可选校正标记
            lines.append(f"- **{c.get('law_name', '')}** {c.get('article_no', '')}{note_str}")
            # 若该条存在条文内容，则以引用块（>）展示，截取前 200 字符控制长度
            if c.get("content"):
                lines.append(f"  > {c.get('content', '')[:200]}")
        # 小节末尾空行，保持段落间距
        lines.append("")

    # ── 法条校验日志（Law Validation Log）──
    # 仅当存在校验结果（law_validation 非空）时输出，向用户透明展示校验情况
    if law_validation:
        # 小节标题：二级标题 + ✅ 图标
        lines.append(f"## ✅ 法条校验\n")
        # 输出人读的校验汇总文案（如"✅ 3条通过 · ⚠️ 1条已校正 · ❌ 1条虚假引用(已移除)"）
        lines.append(f"{law_validation.get('summary', '')}\n")

    # ── 风险提示（Risk Warnings）──
    # 仅当存在风险（risks 非空）时输出该小节
    if risks:
        # 小节标题：二级标题 + ⚠️ 图标
        lines.append("## ⚠️ 风险提示\n")
        # 遍历每条风险
        for r in risks:
            # 风险等级映射表：英文 level → emoji + 中文（高/中/低）
            level_map = {"high": "🔴 高", "medium": "🟡 中", "low": "🟢 低"}
            # 输出风险标题：三级标题（###），含等级标识与风险标题；
            # level 缺失或不在表中时兜底为"🟡 中"
            lines.append(f"### {level_map.get(r.get('level', 'medium'), '🟡 中')} {r.get('title', '')}\n")
            # 输出风险详细描述（含依据）
            lines.append(f"{r.get('description', '')}\n")
            # 若存在应对建议（suggestion），以引用块（> 加粗建议）输出
            if r.get("suggestion"):
                lines.append(f"> **建议**: {r.get('suggestion', '')}\n")
        # 小节末尾空行
        lines.append("")

    # ── 相似案例（Similar Cases）──
    # 仅当存在相似案例（similar_cases 非空）时输出该小节
    if similar_cases:
        # 小节标题：二级标题 + 🔗 图标
        lines.append("## 🔗 相似案例参考\n")
        # 最多展示前 3 个案例（similar_cases[:3] 切片），避免文书过长
        for c in similar_cases[:3]:
            # 输出案例标题（加粗）与案号（括号包裹）
            lines.append(f"- **{c.get('title', '')}** ({c.get('caseNo', '')})")
            # 输出法院与裁判日期（→ 箭头连接，格式为"法院 · 日期"）
            lines.append(f"  → {c.get('court', '')} · {c.get('date', '')}")
            # 输出案情摘要（引用块格式，截取前 200 字符）
            lines.append(f"  > {c.get('summary', '')[:200]}")
        # 小节末尾空行
        lines.append("")

    # ── 免责声明（Disclaimer）──
    # 水平线：正文与页脚声明的分隔
    lines.append("---\n")
    # 免责声明第一句：AI 辅助生成，仅供参考（引用块格式）
    lines.append("> **免责声明**: 本文书由法智引擎 AI 辅助生成, 仅供参考。\n")
    # 免责声明第二句：援引《律师法》第 13/28 条，强调最终法律文件须经执业律师审阅签章
    lines.append("> 依据《律师法》第 13/28 条, 最终法律文件须经执业律师审阅签章后正式使用。\n")

    # 把所有行用换行符拼接为完整的最终文书（Markdown 全文）
    final_document = "\n".join(lines)

    # ---- 持久化到历史记录（Persist to History）----
    try:
        # 调用历史存储的 store 方法，把本次文书生成的完整信息持久化
        record = history_store.store(
            task_type="docgen",     # 任务类型：文书生成（docgen，区别于合同审核）
            title=f"{template_name} - {dispute_type}",  # 记录标题：文书类型 - 纠纷类型
            user_input={            # 记录用户输入侧信息（供历史页回看）
                "dispute_type": dispute_type,           # 纠纷类型
                "plaintiff": case_summary.get("parties", {}).get("plaintiff", ""),  # 原告
                "defendant": case_summary.get("parties", {}).get("defendant", ""),  # 被告
                "claims": case_summary.get("claims", []),   # 诉讼请求列表
                "facts": case_summary.get("facts", []),     # 事实列表
                "document_type": state.get("document_type", "complaint"),  # 文书类型编码
            },
            result={                # 记录结果侧信息（各节点的最终产物）
                "draft_content": draft,          # 文书草稿正文
                "cited_laws": cited_laws,        # 已校正引用法条
                "risks": risks,                  # 风险列表
                "similar_cases": similar_cases,  # 相似案例
                "law_validation": law_validation,  # 法条校验结果
            },
            summary=f"{template_name} - {dispute_type}"[:200],  # 摘要（截取前 200 字符）
        )
        # 从存储返回的记录中取出记录 ID（供前端跳转详情页）
        document_id = record["id"]
        # 打印日志：提示已持久化成功及记录 ID
        print(f"  已持久化到历史记录, id={document_id}")
    except Exception as e:
        # 持久化失败（如存储服务不可用）：打印告警，不中断主流程
        print(f"  ⚠️ 历史记录持久化失败: {e}")
        # 记录 ID 置为 None（前端可据此判断"未持久化"，隐藏详情跳转入口）
        document_id = None

    # 打印日志：展示最终文书长度（字符数），便于监控产出规模
    print(f"  最终文书长度: {len(final_document)} 字")
    # ==== 微调数据收集 ====
    # 微调样本收集块（可选旁路）：记录本节点的输入/输出用于后续模型微调
    try:
        # 构造微调输入：取 state["input"]，转字符串并截取前 2000 字符
        _ft_input = str(state.get("input", "") or "")[:2000]
        # 注意：此行是"裸字典表达式"（bare dict expression），单独成行无任何效果，
        # 属于遗留的无操作语句，此处按原样保留，不改动逻辑。
        {"output": state.get("output", ""), "final_report_markdown": state.get("final_report_markdown", "")
}
        # 调用微调样本收集器（记录节点名、输入、输出、任务类型）
        collect_ft_sample("doc_final_delivery", _ft_input, _ft_output,
                          task_type=state.get("task_type", ""))
    except Exception:
        # 微调收集失败（如 _ft_output 未定义）：静默忽略，不影响主流程
        pass
    # 返回三个产物：
    # final_document（最终排版文书）、document_id（持久化记录 ID）、
    # output（与 final_document 相同的通用输出字段，供前端统一读取）
    return {"final_document": final_document, "document_id": document_id, "output": final_document}
