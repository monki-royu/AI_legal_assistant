# -*- coding: utf-8 -*-
"""
法律文书生成 - 最终交付节点 (V3: 简化版, 无 fan-in)
==================================================

【V3 架构变化】
    不再需要 fan-in 完成标志检查。V3 链路是线性的:
    risk_advisor → (pass) → final_delivery → END
    所有产物 (cited_laws, similar_cases, risks) 都由 risk_advisor 写入 state.

    新增: 支持 doc_force_delivery=True 时显示「质量不达标, 仅供参考」免责声明.

【功能】
    汇总前面所有节点的产物, 组装为统一格式的最终交付物, 并持久化到历史记录.

【下游】
    END — 本节点是文书生成链路的终点.
"""
from datetime import datetime
from __004__langgraph_more_nodes.agent_state import AgentState
from common.history_store import store as history_store
from common.finetune_utils import collect_ft_sample


def doc_final_delivery_node(state: AgentState):
    """最终交付节点 V3: 直接交付, 无 fan-in 等待.

    读取:
        - draft_content (str): 文书草稿
        - cited_laws (list): 法条引用 (由 risk_advisor 子图获取)
        - risks (list): 风险提示 (由 risk_advisor LLM 分析)
        - similar_cases (list): 相似案例 (由 risk_advisor 子图获取)
        - retrieval_quality_score (float): 组合质量分
        - doc_force_delivery (bool): 是否强制交付 (显示免责声明)
        - case_summary (dict): 案情分析
        - template_name (str): 文书类型名称
        - dispute_type (str): 纠纷类型

    写入:
        - final_document (str): 最终排版文书
        - document_id (str): 持久化记录 ID
    """
    print("文书生成 [5/5] 最终交付")

    # 读取各节点产物
    draft = state.get("draft_content", "") or ""
    cited_laws = state.get("cited_laws", []) or []
    risks = state.get("risks", []) or []
    similar_cases = state.get("similar_cases", []) or []
    case_summary = state.get("case_summary", {}) or {}
    template_name = state.get("template_name", "法律文书")
    dispute_type = state.get("dispute_type", "")
    force_delivery = state.get("doc_force_delivery", False)
    quality_score = state.get("retrieval_quality_score", 0)

    # ---- 组装最终文书 ----
    lines = []

    # 封面
    lines.append(f"# {template_name}\n")
    lines.append(f"**纠纷类型**: {dispute_type}")
    lines.append(f"**生成日期**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**生成引擎**: 法智引擎 · 文书生成智能体")
    lines.append(f"**检索质量分**: {quality_score}\n")
    lines.append("---\n")

    # 正文
    lines.append(draft)
    lines.append("\n---\n")

    # 引用法条 (来自 law_search 子图)
    if cited_laws:
        lines.append("## 📚 引用法条\n")
        for c in cited_laws:
            note = c.get("note", "")
            note_str = f" ({note})" if note else ""
            lines.append(f"- **{c.get('law_name', c.get('title', ''))}** {c.get('article_no', '')}{note_str}")
            if c.get("content"):
                lines.append(f"  > {c.get('content', '')[:200]}")
        lines.append("")

    # 风险提示 (来自 risk_advisor LLM 分析)
    if risks:
        lines.append("## ⚠️ 风险提示\n")
        for r in risks:
            level_map = {"high": "🔴 高", "medium": "🟡 中", "low": "🟢 低"}
            lines.append(f"### {level_map.get(r.get('level', 'medium'), '🟡 中')} {r.get('title', '')}\n")
            lines.append(f"{r.get('description', '')}\n")
            if r.get("suggestion"):
                lines.append(f"> **建议**: {r.get('suggestion', '')}\n")
        lines.append("")

    # 相似案例 (来自 case_search 子图)
    if similar_cases:
        lines.append("## 🔗 相似案例参考\n")
        for c in similar_cases[:3]:
            lines.append(f"- **{c.get('title', c.get('case_title', ''))}** ({c.get('caseNo', '')})")
            lines.append(f"  → {c.get('court', '')} · {c.get('date', '')}")
            lines.append(f"  > {c.get('summary', c.get('content', ''))[:200]}")
        lines.append("")

    # 免责声明
    lines.append("---\n")
    if force_delivery:
        lines.append("> **⚠️ 质量不达标**: 本次文书检索质量分 {:.0f}, 未达 70 分阈值, ".format(quality_score))
        lines.append("系统已自动放行, 但内容可能不完整, 仅供参考, 建议人工复核.\n")
    lines.append("> **免责声明**: 本文书由法智引擎 AI 辅助生成, 仅供参考。")
    lines.append("> 依据《律师法》第 13/28 条, 最终法律文件须经执业律师审阅签章后正式使用。\n")

    final_document = "\n".join(lines)

    # ---- 持久化到历史记录 ----
    document_id = None
    try:
        record = history_store.store(
            task_type="docgen",
            title=f"{template_name} - {dispute_type}",
            user_input={
                "dispute_type": dispute_type,
                "plaintiff": case_summary.get("parties", {}).get("plaintiff", ""),
                "defendant": case_summary.get("parties", {}).get("defendant", ""),
                "claims": case_summary.get("claims", []),
                "facts": case_summary.get("facts", []),
                "document_type": state.get("document_type", "complaint"),
            },
            result={
                "draft_content": draft,
                "cited_laws": cited_laws,
                "risks": risks,
                "similar_cases": similar_cases,
                "retrieval_quality_score": quality_score,
                "force_delivery": force_delivery,
            },
            summary=f"{template_name} - {dispute_type}"[:200],
        )
        document_id = record["id"]
        print(f"  已持久化, id={document_id}")
    except Exception as e:
        print(f"  ⚠️ 持久化失败: {e}")

    print(f"  最终文书长度: {len(final_document)} 字")

    _ft_output = {"final_document": final_document, "document_id": document_id, "output": final_document}
    try:
        _ft_input = str(state.get("input", "") or "")[:2000]
        collect_ft_sample("doc_final_delivery", _ft_input, _ft_output,
                          task_type=state.get("task_type", ""))
    except Exception as fe:
        print(f"  ⚠️ 微调样本收集失败(忽略): {fe}")

    return {"final_document": final_document, "document_id": document_id, "output": final_document}
