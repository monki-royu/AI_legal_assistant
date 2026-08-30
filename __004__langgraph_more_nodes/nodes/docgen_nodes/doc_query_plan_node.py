# -*- coding: utf-8 -*-
"""
法律文书生成 - 检索查询规划节点 (V4 新增, V5 简化)
====================================================

【架构位置】
  位于 doc_template_match 之后，法条/类案两个并发检索子图之前。

【职责】
  1. 规划法条检索：law_retrieval_query (字符串) — 面向「找法条」的精准查询
  2. 规划类案检索：case_retrieval_query (字符串) — 面向「找相似判决」的案情匹配

【V5 简化】
  - 不再产出关键词 (law/case_retrieval_keywords)。
    关键词提取交给检索子图的 intent_decompose_node Path A 做 LLM 规划。
  - 不再判定 industry_sources 触发条件。
    法条检索走 task_type=legal_research，由 KEYWORD_RULES 在 intent_decompose 内自行判定。

【下游衔接】
  - 法条检索子图 (task_type=legal_research)：读取 law_retrieval_query。
  - 类案检索子图 (task_type=case_search)：读取 case_retrieval_query。
"""

import json

from langchain_core.messages import SystemMessage, HumanMessage
from common.llm import my_llm
from __004__langgraph_more_nodes.agent_state import AgentState


def doc_query_plan_node(state: AgentState) -> dict:
    """查询规划节点：输出法条 + 类案两条独立检索语句。

    只产出查询语句 (retrieval_query)，不产出关键词。
    关键词提取交给检索子图的 intent_decompose_node Path A 做 LLM 规划。

    读取:
        - case_summary (Dict): doc_case_analyze 输出，含 case_type/parties/facts/claims
        - dispute_type (str):  纠纷类型（兜底）
        - template_id / template_name (str): doc_template_match 输出
        - input / user_input (str): 用户原始输入
        - contract_type (str): 合同类型（兜底）

    写入:
        - law_retrieval_query (str):  面向法条检索的精准查询语句
        - case_retrieval_query (str): 面向类案检索的案情匹配查询语句
    """
    print("文书生成 [3/8] 查询规划（法条 + 类案双检索语句）")

    case_summary = state.get("case_summary", {}) or {}
    dispute_type = str(state.get("dispute_type", "") or "")
    case_type = str(case_summary.get("case_type", "") or dispute_type)
    template_name = str(state.get("template_name", "") or state.get("template_id", "民事起诉状"))
    user_input = str(state.get("user_input", "") or state.get("input", "") or "")

    parties = case_summary.get("parties", {}) or {}
    facts_list = case_summary.get("facts", []) or []
    claims_list = case_summary.get("claims", []) or []
    facts_text = "；".join(str(f) for f in facts_list)
    claims_text = "；".join(str(c) for c in claims_list)
    contract_type = str(state.get("contract_type", "") or "")

    # --- LLM 规划两条检索语句（只产 query，不产 keywords）---
    law_query = ""
    case_query = ""

    prompt = f"""你是法律文书写作的检索查询规划助手。根据以下案情信息，
为两个独立的检索任务分别输出精准查询语句。

【输出要求】
1. LAW_QUERY: 面向「找法条」的检索查询。
   - 聚焦用户诉讼请求和争议焦点对应的法律依据；
   - 不要泛化描述整个案情，按请求逐条展开（如"违约金上限法律规定"、"房屋租赁合同解除条件"）。
2. CASE_QUERY: 面向「找相似判决」的检索查询。
   - 聚焦双方身份 + 核心事实 + 请求金额/违约金比例 + 违约行为；
   - 保留具体数字和合同要素，方便在判例库中找到同类判决。

输出严格 JSON：
{{
  "LAW_QUERY": "...",
  "CASE_QUERY": "..."
}}

【案情信息】
- 文书模板：{template_name}
- 案由/纠纷类型：{case_type or dispute_type}
- 原告/申请人：{parties.get("plaintiff", "")}
- 被告/被申请人：{parties.get("defendant", "")}
- 核心事实：{facts_text[:1500]}
- 诉讼请求：{claims_text[:1000]}
- 用户原始输入摘要：{user_input[:800]}
"""
    try:
        resp = my_llm.invoke([
            SystemMessage(content="你是严谨的法律检索查询规划专家，只输出 JSON，不做任何解释。"),
            HumanMessage(content=prompt),
        ])
        text = resp.content.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            s = text.find("{"); e = text.rfind("}") + 1
            if s >= 0 and e > s:
                text = text[s:e]
        parsed = json.loads(text)
        law_query = str(parsed.get("LAW_QUERY", "")).strip()
        case_query = str(parsed.get("CASE_QUERY", "")).strip()
    except Exception as e:
        print(f"  ⚠️ 查询规划 LLM 失败({e})，进入规则兜底")

    # --- LLM 失败或为空：规则兜底 ---
    if not law_query:
        law_query = f"{case_type} {claims_text}".strip() or f"{case_type} 法律规定"
    if not case_query:
        case_query = f"{case_type} {facts_text} {claims_text}".strip() or case_type

    print(f"  法条检索: query={law_query[:60]}…")
    print(f"  类案检索: query={case_query[:60]}…")

    return {
        "law_retrieval_query": law_query,
        "case_retrieval_query": case_query,
    }


# ========================================================================
# 模块自测入口
# ========================================================================
if __name__ == "__main__":
    s = AgentState(
        dispute_type="房屋租赁合同纠纷",
        case_summary={
            "case_type": "房屋租赁合同纠纷",
            "parties": {"plaintiff": "张三(出租人)", "defendant": "李四(承租人)"},
            "facts": [
                "2024年1月1日签订房屋租赁合同，租期1年，月租金5000元",
                "李四自2024年3月起未付租金，累计拖欠15000元",
                "合同约定逾期支付租金按日5%付违约金"
            ],
            "claims": [
                "判令被告支付拖欠租金15000元",
                "判令被告支付违约金（按合同约定计算）",
                "判令解除房屋租赁合同"
            ],
        },
        template_name="民事起诉状",
        input="我要写一份起诉状，告李四拖欠房租还不给违约金",
    )
    result = doc_query_plan_node(s)
    for k, v in result.items():
        print(f"{k}: {v}")
