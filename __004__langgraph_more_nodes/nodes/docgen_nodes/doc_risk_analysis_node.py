# -*- coding: utf-8 -*-
"""
法律文书生成 - 风险分析节点 (V4 新增)
======================================

【架构位置】
  位于 clause_fill 之后、final_delivery 之前。

【职责】
  V4 链路里「两个检索各自走内部质量门控，外层不再做组合回边重试」，
  所以本节点不再管 need_refill / doc_retry_count，只做一件事：
    分析案情 + 已生成文书 → 识别风险点 → 输出 risks（高/中/低 + 建议）。

  风险分析逻辑直接复用 doc_risk_advisor_node 中的 _run_risk_analysis，
  保证输出字段完全兼容 doc_final_delivery_node 的渲染格式。

【写入】
  risks (List[Dict])：风险清单数组
  doc_risk_done (bool)
  retrieval_quality_score (float)：日志用，= 0.5*法法条质量 + 0.5*类案质量
"""

import json
from langchain_core.messages import SystemMessage, HumanMessage
from common.llm import my_llm
from __004__langgraph_more_nodes.agent_state import AgentState
from common.finetune_utils import collect_ft_sample


def _run_risk_analysis(draft: str, case_summary: dict, template_name: str, dispute_type: str) -> list:
    """LLM 风险分析（与 doc_risk_advisor_node._run_risk_analysis 一致，独立复制以避免循环 import）。"""
    print("  执行 LLM 风险分析...")
    prompt = f"""你是一位法律风险评估专家。请对以下法律文书草稿进行风险分析，
列出 2-5 条潜在风险（包含风险等级、标题、详细描述与应对建议）。

【文书类型】{template_name}
【纠纷类型】{dispute_type}
【当事人】原告/申请人={case_summary.get('parties', {}).get('plaintiff', '')}
          被告/被申请人={case_summary.get('parties', {}).get('defendant', '')}
【草稿摘要】{draft[:1500]}

请输出 JSON 数组（纯 JSON）：
[
  {{
    "level": "high/medium/low",
    "title": "风险标题",
    "description": "风险详细描述（含依据）",
    "suggestion": "应对建议"
  }}
]"""
    risks = []
    try:
        resp = my_llm.invoke([
            SystemMessage(content="你经验丰富的法律风险评估专家，输出严格 JSON 数组。"),
            HumanMessage(content=prompt),
        ])
        text = resp.content.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            s = text.find("{"); e = text.rfind("}") + 1
            if s >= 0 and e > s:
                text = text[s:e]
        # 兼容 LLM 用 {} 包裹的情况：尝试把顶层剥一层
        parsed = json.loads(text)
        if isinstance(parsed, list):
            risks = parsed
        elif isinstance(parsed, dict) and isinstance(parsed.get("risks"), list):
            risks = parsed["risks"]
        # 兜底：如果是对象但不是 list/dict，兜底到空
        if not isinstance(risks, list):
            risks = []
        print(f"    识别 {len(risks)} 项风险")
    except Exception as e:
        print(f"    ⚠️ 风险分析失败: {e}, 使用兜底提示")
        risks = [{
            "level": "medium", "title": "建议律师审阅",
            "description": "本文书由 AI 辅助生成，建议由执业律师审阅后正式使用。",
            "suggestion": "请执业律师复核全文，特别是事实陈述和法律依据部分。",
        }]
    return risks


def doc_risk_analysis_node(state: AgentState) -> dict:
    """风险分析节点：无回边、无质量门，单纯 LLM 识别风险。

    读取:
        - draft_content / cited_laws / similar_cases（已生成结果）
        - case_summary / template_name / dispute_type
        - law_quality_score / case_quality_score
    写入:
        - risks (List[Dict]): 风险清单
        - retrieval_quality_score (float): 组合质量分（仅日志）
        - doc_risk_done (bool): = True
    """
    print("文书生成 [6/8] 风险分析（无回边质量门，纯 LLM 风险识别）")

    draft = str(state.get("draft_content", "") or "")
    case_summary = state.get("case_summary", {}) or {}
    template_name = str(state.get("template_name", "") or state.get("template_id", ""))
    dispute_type = str(state.get("dispute_type", "") or "")
    law_q = float(state.get("law_quality_score", 0) or 0)
    case_q = float(state.get("case_quality_score", 0) or 0)

    # 组合质量分（仅日志/调试用，不再做门控；和 V3 算法一致便于对比）
    # 若两检索子图都失败（质量分 0），还是做风险分析（识别"法条检索为空白"类风险）
    if law_q and case_q:
        quality = round(0.5 * law_q + 0.5 * case_q, 1)
    elif law_q:
        quality = round(law_q, 1)
    elif case_q:
        quality = round(case_q, 1)
    else:
        quality = 0.0
    print(f"  组合质量分（仅供日志）: {quality}  (法条 {law_q:g} / 类案 {case_q:g})")
    if quality < 50:
        print("  ⚠️ 质量分偏低，但 V4 链路不做回边重试，直接进入风险分析（兜底风险项会提示补充证据/法条）")

    risks = _run_risk_analysis(draft, case_summary, template_name, dispute_type)

    # 兜底：当 cited_laws / similar_cases 为空时，硬塞一个"证据/法条不足"的通用提示
    cited_cnt = len(state.get("cited_laws", []) or [])
    case_cnt = len(state.get("similar_cases", []) or [])
    if cited_cnt == 0 and all(r["title"] != "法律依据检索不足" for r in risks):
        risks.append({
            "level": "high", "title": "法律依据检索不足",
            "description": "系统未能从知识库中检索到与本案诉求强相关的法条，当前文书正文中可能缺少可靠法律依据支撑。",
            "suggestion": "请补充更详细的案情要素重新生成；或人工核对《民法典》等相关法律原文、补充缺失条款后再提交打印/立案。",
        })
    if case_cnt == 0 and all(r["title"] != "类案参考缺失" for r in risks):
        risks.append({
            "level": "low", "title": "类案参考缺失",
            "description": "系统未检索到可参考的相似判决，败诉/胜诉预期无法通过判例数据佐证。",
            "suggestion": "可在裁判文书网（wenshu.court.gov.cn）/北大法宝/无讼自行补充类案查询。",
        })

    # 微调样本收集
    try:
        _ft_input = (
            f"template={template_name}, dispute={dispute_type}, "
            f"draft_len={len(draft)}, cited={cited_cnt}, cases={case_cnt}, quality={quality}"
        )
        collect_ft_sample("doc_risk_analysis_v4", _ft_input,
                          {"risks_len": len(risks), "quality": quality},
                          task_type=state.get("task_type", ""))
    except Exception as fe:
        print(f"  ⚠️ 微调样本收集失败(忽略): {fe}")

    return {
        "risks": risks,
        "retrieval_quality_score": quality,
        "doc_risk_done": True,
    }


if __name__ == "__main__":
    s = AgentState(
        template_name="民事起诉状",
        dispute_type="房屋租赁合同纠纷",
        case_summary={
            "parties": {"plaintiff": "张三", "defendant": "李四"},
            "case_type": "房屋租赁合同纠纷",
        },
        draft_content="# 民事起诉状\n## 诉讼请求\n1. 解除合同并支付违约金。\n",
        cited_laws=[],
        similar_cases=[],
        law_quality_score=30.0,
        case_quality_score=10.0,
    )
    r = doc_risk_analysis_node(s)
    print("组合质量分:", r.get("retrieval_quality_score"))
    for it in r.get("risks", []):
        print(f"  [{it['level']}] {it['title']}: {it['description'][:50]}")
