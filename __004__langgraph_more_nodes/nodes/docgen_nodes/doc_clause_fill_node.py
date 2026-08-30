# -*- coding: utf-8 -*-
"""
法律文书生成 - 条款填充节点 (V4: 接收检索结果, 写稿 + 标注真实法条)
===================================================================

【V4 架构变化（本文件按用户方案重写）】
  V3：先写文书 + [法条待填充] 占位符 → risk_advisor 后置检索再回填
  V4（本次）：前置的法条检索子图已经把 citations 交给本节点，
             直接在写作时把真实法条插入合适位置（「先检索后写稿」）

  对应 SYSTEM_PROMPT 要求：
    ① 正文（诉讼请求/事实与理由/证据清单）引法条时使用「法条名 + 条款号 + source_ref 锚点」，不贴原文；
    ② cited_laws 数组单独存每条原文（source_ref = {source_id}#{序号} 替代不存在的 rag_doc_id）；
    ③ 未检索到对应法条的主张标注「（未检索到相关法条）」，不得自行编造。

【流程位置】
  query_plan → 并发 law_retrieve + case_retrieve → clause_fill (本节点)
  → risk_analysis → final_delivery

【写入字段】
  draft_content (Markdown)：正文
  cited_laws (List[Dict])：文末要贴的原文 + source_ref（供 final_delivery 文末引用板块渲染）
"""

import json
from typing import List, Dict, Any

from common.llm import my_llm
from langchain_core.messages import SystemMessage, HumanMessage
from __004__langgraph_more_nodes.agent_state import AgentState
from common.finetune_utils import collect_ft_sample


SYSTEM_PROMPT = """你是法律文书撰写专家，精通各类法律文书的格式规范和条款表述。

## 任务
根据案情摘要、选定模板，以及下面【RAG 检索法条】块中给出的**真实法条**，
直接生成已正确插入法条引用的完整文书草稿。

## 工作流程
1. 阅读【案情信息】+【文书模板】明确写作目标；
2. 遍历【RAG 检索法条】，从已给出的法条里挑出最合适的条款；
3. 逐段填充文书：诉讼请求 → 事实与理由 → 证据清单 → 法律依据。

## 🚫 严格约束（防幻觉）
- 只能引用【RAG 检索法条】里出现的法条编号与内容，**严禁自行编造任何法条编号或内容**；
- 若某个主张在【RAG 检索法条】中没有对应的条款，在主张末尾写「（未检索到相关法条）」，不得自行杜撰；
- 每条法条引用须附带「source_ref」（形如 laws#0、regulations#3），它就是每条 RAG 返回法条的唯一标识，不能改、不能编；
- 不得对 RAG 返回的法条原文做任何改写或扩展。

## ✍ 引用分档规则（关键！正文不可法条堆砌）
- **正文段落（诉讼请求、事实与理由、证据清单）** 引法条时：
    只写「依据《XX 法》第 X 条〔source_ref=laws#0〕」或「依据《XX 法》相关规定〔source_ref=laws#0〕」。
    **正文段落不贴法条原文**，否则文书成了法条堆砌，不像正规起诉状/答辩状。
- **文末「引用法条」板块**：统一贴每条被引用法条的原文，标注 source_ref。
"""


def _format_law_block(law_citations: List[Dict[str, Any]]) -> str:
    """把 citations 列表格式化成 SYSTEM_PROMPT 要的 RAG 法条块字符串。

    每条：【N】{title}  {article_no or ''} [source_ref=xxx#i]
         原文：{content 前 300 字}（避免 prompt 爆炸；如果要完整原文，RAG 已经把 full content 给了 cited_laws）
    """
    if not law_citations:
        return "（无）"
    lines = []
    for idx, c in enumerate(law_citations):
        if not isinstance(c, dict):
            continue
        title = str(c.get("title") or c.get("name") or "(无标题)")
        article_no = str(c.get("article_no") or c.get("article") or "")
        source_ref = str(c.get("source_ref") or f"unknown#{idx}")
        content = str(c.get("content") or "")
        if len(content) > 600:
            content = content[:600] + "…（已截断，完整原文在 cited_laws[].content 中）"
        label = f"【{idx}】{title}"
        if article_no:
            label += f"  条款号：{article_no}"
        label += f"  [source_ref={source_ref}]"
        lines.append(label)
        if content:
            lines.append(f"  原文：{content}")
        lines.append("")
    return "\n".join(lines)


def _fallback_llm_result(case_summary: dict, parties: dict, case_type: str,
                         template_id: str, law_citations: list) -> Dict[str, Any]:
    """LLM 失败兜底：用纯规则生成一份可交付的草稿 + cited_laws（从已检索到的法条全量塞进来）。

    兜底策略是「宁可法条全贴（检索到的都作为附件引用），也不编造」。
    """
    plaintiff = parties.get("plaintiff", "原告")
    defendant = parties.get("defendant", "被告")
    facts = "；".join(case_summary.get("facts", []) or []) or "（案情事实待补充）"
    claims = "；".join(case_summary.get("claims", []) or []) or "1. 请求法院依法支持原告诉请。"

    # cited_laws 兜底：从传入 citations 直接映射，不改写
    cited_laws_fallback = []
    for idx, c in enumerate(law_citations or []):
        if not isinstance(c, dict):
            continue
        cited_laws_fallback.append({
            "law_name": str(c.get("title") or c.get("name") or "(无标题)"),
            "article_no": str(c.get("article_no") or c.get("article") or ""),
            "content": str(c.get("content") or ""),
            "source_ref": str(c.get("source_ref") or f"unknown#{idx}"),
        })

    # 正文中在「法律依据」区按 source_ref 批量引用
    body_ref_lines = []
    for item in cited_laws_fallback:
        ref = item["source_ref"]
        name = item["law_name"]
        art = item["article_no"]
        label = f"《{name}》第{art}条" if art else f"《{name}》"
        body_ref_lines.append(f"- 依据 {label}〔source_ref={ref}〕")

    draft = f"""# 民事起诉状

## 当事人信息
- **原告**：{plaintiff}
- **被告**：{defendant}

## 诉讼请求
{claims}

## 事实与理由
{facts[:2000]}

## 证据清单
（请补充证据材料，如合同、转账记录、催款函等）

## 法律依据
{chr(10).join(body_ref_lines) if body_ref_lines else '（未检索到相关法条）'}

---
*本文书由法智引擎辅助生成，须经执业律师审阅签章后正式使用。*
"""
    return {"draft_content": draft, "cited_laws": cited_laws_fallback}


def doc_clause_fill_node(state: AgentState) -> Dict[str, Any]:
    """V4 版条款填充：先拿检索结果，再一次 LLM 写作 + 真实法条引用。

    读取:
        - case_summary / template_id / dispute_type / user_input
        - law_citations (List[Dict]): 法条检索子图返回的法条列表（已含 source_ref 字段）
        - template_name (str): 文书类型中文名（让 LLM 更明确）
    写入:
        - draft_content (str): Markdown 正文（引用只写法条名+条款号+source_ref 锚点，不贴原文）
        - cited_laws (List[Dict]): 被引用法条的原文清单（供 final_delivery 文末引用板块渲染）
        - doc_law_done (bool): =True（V4 写法已完成，下游风险分析只读不改）
    """
    print("文书生成 [5/8] 条款填充（先检索后写稿，真实法条引用）")

    case_summary = state.get("case_summary", {}) or {}
    template_id = state.get("template_id", "civil_complaint")
    template_name = state.get("template_name", template_id)
    dispute_type = state.get("dispute_type", "") or ""
    law_citations = state.get("law_citations", []) or []
    # 法条全文上下文: doc_law_retrieve_node 把检索到的法条正文拼成的连续文本。
    # 【接线背景】该字段此前一直被写入但零消费方 —— 本节点只用 law_citations
    #   (条目列表, 每条已被截断), LLM 看不到法条完整表述, 容易出现"引了条号、
    #   但说理与法条原文对不上"的问题。现在把它一并注入 prompt 供 LLM 理解,
    #   但引用仍受 law_citations 的 source_ref 白名单约束, 不会放宽幻觉防御。
    law_context = str(state.get("law_research_context") or "").strip()

    parties = case_summary.get("parties", {}) or {}
    plaintiff = parties.get("plaintiff", state.get("plaintiff", "原告"))
    defendant = parties.get("defendant", state.get("defendant", "被告"))
    case_type = case_summary.get("case_type", dispute_type)
    facts = "；".join(case_summary.get("facts", []) or []) \
        or state.get("user_input", "") or state.get("input", "")
    claims_text = "；".join(case_summary.get("claims", []) or []) or ""

    print(f"  案由: {case_type}, 模板: {template_name} ({template_id})")
    print(f"  可引用法条池: {len(law_citations)} 条"
          + (f", 法条上下文 {len(law_context)} 字" if law_context else " (无法条上下文)"))

    rag_block = _format_law_block(law_citations)

    # 法条原文的注入块: 截断到 3000 字, 避免 prompt 过长挤占生成空间
    law_context_block = ""
    if law_context:
        law_context_block = f"""

【法条原文上下文（帮助你准确理解法条表述，仅供说理参考）】
{law_context[:3000]}
【注意】说理可参考上面的原文，但引用清单仍必须来自『RAG 检索法条』的 source_ref，不得自行新增。"""

    user_msg = f"""【文书模板】{template_name}（ID: {template_id}）
【案由】{case_type}
【原告】{plaintiff}
【被告】{defendant}
【案情事实】{facts[:2000]}
【诉讼请求】{claims_text[:1200]}
【事发时间】{state.get('incident_date', '')}

【RAG 检索法条（只能从这里选，不可自行编造）】
{rag_block}
{law_context_block}

请输出严格 JSON：
{{
  "draft_content": "完整文书 Markdown（正文段落内引用只写『《XX法》第X条〔source_ref=laws#i〕』样式，不贴法条原文；文末附一个『## 引用法条』二级标题，列出被引用法条的 source_ref + 法条名 + 条款号）",
  "cited_laws": [
    {{
      "law_name": "《XX法》",
      "article_no": "第X条或条款号（原文保留）",
      "content": "RAG 检索到的原文（原封不动，不可改写/扩展）",
      "source_ref": "RAG 块中对应的 source_ref，如 laws#0"
    }}
  ]
}}
仅输出 JSON，不要输出任何其他文字或解释。"""

    try:
        resp = my_llm.invoke([
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=user_msg),
        ])
        text = resp.content.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            s = text.find("{"); e = text.rfind("}") + 1
            if s >= 0 and e > s:
                text = text[s:e]
        parsed = json.loads(text)
        draft = str(parsed.get("draft_content", "") or "")
        cited = parsed.get("cited_laws") or []
        if not isinstance(cited, list):
            cited = []

        # 格式规整：source_ref / content / law_name 全部保留，做一次非空兜底
        normalized_cited = []
        for idx, item in enumerate(cited):
            if not isinstance(item, dict):
                continue
            normalized_cited.append({
                "law_name": str(item.get("law_name") or "").strip() or "(无标题)",
                "article_no": str(item.get("article_no") or "").strip(),
                "content": str(item.get("content") or "").strip(),
                "source_ref": str(item.get("source_ref") or f"unknown#{idx}"),
            })

        # 幻觉防御：LLM 若编造了「不在 law_citations 池里的 source_ref」，剔除该行
        valid_refs = set()
        for idx, c in enumerate(law_citations):
            if isinstance(c, dict):
                valid_refs.add(str(c.get("source_ref") or f"unknown#{idx}"))
        filtered_cited = [it for it in normalized_cited if it["source_ref"] in valid_refs]
        if len(filtered_cited) < len(normalized_cited):
            print(f"  幻觉防御: 剔除 {len(normalized_cited)-len(filtered_cited)} 条 source_ref 不在检索池的引用")

        if len(draft) < 50 or (not law_citations and len(draft) < 100):
            raise ValueError("LLM 输出草稿过短，走规则兜底")

        print(f"  草稿长度: {len(draft)} 字, 引用法条数: {len(filtered_cited)}")

    except Exception as e:
        print(f"  ⚠️ 条款填充 LLM 失败 ({e})，进入规则兜底")
        fallback = _fallback_llm_result(case_summary, {"plaintiff": plaintiff, "defendant": defendant},
                                        case_type, template_id, law_citations)
        draft = fallback["draft_content"]
        filtered_cited = fallback["cited_laws"]
        print(f"  兜底草稿长度: {len(draft)} 字, 引用法条数: {len(filtered_cited)}")

    # 微调样本收集
    try:
        _ft_input = (
            f"template={template_id}, case_type={case_type}, "
            f"facts={str(facts)[:1000]}, claims={str(claims_text)[:600]}, "
            f"law_citations_cnt={len(law_citations)}"
        )
        collect_ft_sample("doc_clause_fill_v4", _ft_input,
                          {"draft_content": draft, "cited_laws": filtered_cited},
                          task_type=state.get("task_type", ""))
    except Exception as fe:
        print(f"  ⚠️ 微调样本收集失败(忽略): {fe}")

    return {
        "draft_content": draft,
        "cited_laws": filtered_cited,
        "doc_law_done": True,       # V4 填充完成（法条已写入 cited_laws 数组 + 正文锚点）
        "doc_risk_done": False,     # 风险分析后置
        "doc_case_done": False,     # 类案子图单独跑
        "doc_retry_count": 0,       # V4 无回边重试，保持 0
    }


# 模块自测：构造最小 AgentState 跑 LLM（若无 LLM 密钥会静默失败/走兜底）
if __name__ == "__main__":
    from __004__langgraph_more_nodes.agent_state import AgentState

    demo_law_citations = [
        {
            "title": "中华人民共和国民法典",
            "article_no": "第五百八十五条",
            "content": "当事人可以约定一方违约时应当根据违约情况向对方支付一定数额的违约金，也可以约定因违约产生的损失赔偿额的计算方法。",
            "source_ref": "laws#0",
            "source_id": "laws",
        },
        {
            "title": "中华人民共和国民法典",
            "article_no": "第七百二十二条",
            "content": "承租人无正当理由未支付或者迟延支付租金的，出租人可以请求承租人在合理期限内支付；承租人逾期不支付的，出租人可以解除合同。",
            "source_ref": "laws#1",
            "source_id": "laws",
        },
    ]
    s = AgentState(
        dispute_type="房屋租赁合同纠纷",
        case_summary={
            "case_type": "房屋租赁合同纠纷",
            "parties": {"plaintiff": "张三", "defendant": "李四"},
            "facts": [
                "2024年1月1日双方签订《房屋租赁合同》，租期1年，月租金5000元。",
                "李四自2024年3月起未付租金，至起诉日累计拖欠3个月共15000元。",
                "合同约定逾期支付租金按日5%承担违约金。",
            ],
            "claims": [
                "1. 判令被告支付拖欠租金15000元；",
                "2. 判令被告按合同约定支付逾期违约金；",
                "3. 判令解除房屋租赁合同，被告腾退房屋。",
            ],
        },
        template_id="civil_complaint",
        template_name="民事起诉状",
        law_citations=demo_law_citations,
        input="告李四拖欠房租",
    )
    r = doc_clause_fill_node(s)
    print("=" * 60)
    print("[DRAFT]")
    print(r["draft_content"])
    print("=" * 60)
    print(f"[CITED_LAWS] {len(r['cited_laws'])} 条")
    for it in r["cited_laws"]:
        print(f"  - {it['source_ref']} {it['law_name']} {it['article_no']}: {it['content'][:60]}…")
