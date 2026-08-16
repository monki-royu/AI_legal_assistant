# -*- coding: utf-8 -*-
"""
法律文书生成 - 条款填充节点 (N_doc3)
====================================

【功能】
核心节点：基于案情分析（case_summary）+ 模板（template_id），通过 RAG 检索相关法条，
结合 LLM 填充完整的法律文书草稿。产出：
  - draft_content:  完整文书草稿（Markdown 格式）
  - cited_laws:     引用法条列表 [{law_name, article_no, content, source}]
  - rag_retrieved_laws: RAG 检索原始结果（供后续 law_validator 校验）

【流程位置】（文书生成链路 6 步中的第 3 步）
  [1/6] 案情分析 → [2/6] 模板匹配 → [3/6] 条款填充(RAG+LLM)（本节点）
  → [4/6] 法条校验 → [5/6] 风险提示 / [5b/6] 类案推荐 → [6/6] 最终交付

【设计】
1. RAG 双通道检索：通过 common.retrieval_engine 检索法规（laws）+
   司法解释（interpretations）两类知识库；
2. LLM 约束：System Prompt 要求法条引用必须来自 RAG 检索结果，禁止捏造
   （防幻觉 / Anti-Hallucination 的第一道防线）；
3. 防幻觉：每条引用都带 source 溯源标识，下个节点 law_validator 逐条回查
   （第二道防线：确定性校验兜底）。

【下游】
law_validator（法条校验节点）读取 cited_laws 逐条校验真实性，
若全部伪造（all_fail）则循环回本节点重填（need_refill 路由）。
"""

# 导入标准库 json：解析 LLM 返回的 JSON 文本
import json
# 导入 Optional 类型（类型标注用，提示参数可能为 None；本文件主要用作辅助）
from typing import Optional
# 导入项目统一的 LLM 实例：封装模型选择与调用细节
from common.llm import my_llm
# 导入 RAG 检索引擎实例（engine）：提供法规/案例检索能力（BM25 + FAISS + RRF 融合）
from common.retrieval_engine import engine as retrieval_engine
# 导入 LangChain 消息类型：SystemMessage 设定角色，HumanMessage 承载用户输入
from langchain_core.messages import SystemMessage, HumanMessage
# 导入 AgentState 类型：LangGraph 图中各节点共享的状态字典（TypedDict）
from __004__langgraph_more_nodes.agent_state import AgentState
# 导入微调数据收集工具：记录本节点的输入/输出为微调样本（可选旁路，失败静默）
from common.finetune_utils import collect_ft_sample


def doc_clause_fill_node(state: AgentState):
    """条款填充节点: RAG 检索 + LLM 生成文书草稿。"""
    # 打印日志：标记进入文书生成第 3 步"条款填充（RAG+LLM）"
    print("文书生成 [3/6] 条款填充(RAG+LLM)")
    # 读取案情分析结果（case_summary），缺失时兜底为空字典（保证后续 .get 可用）
    case_summary = state.get("case_summary", {}) or {}
    # 读取文书模板 ID（决定文书格式类型），默认 civil_complaint（民事起诉状）
    template_id = state.get("template_id", "civil_complaint")
    # 读取纠纷类型，缺失时为空字符串
    dispute_type = state.get("dispute_type", "")

    # ── 构造检索查询（Build Retrieval Query）──
    # 案由：优先取 case_summary 中 LLM 抽取的 case_type，否则用用户填的 dispute_type
    case_type = case_summary.get("case_type", dispute_type)
    # 事实文本：把 case_summary 的 facts 列表用"；"拼接；若为空则退回用户原始输入
    facts = "；".join(case_summary.get("facts", [])) or state.get("user_input", "") or state.get("input", "")
    # 诉求文本：把 claims 列表用"；"拼接（用于检索相关法条）
    claims_text = "；".join(case_summary.get("claims", [])) or ""
    # 组合检索查询串：案由 + 事实 + 诉求（信息越全，RAG 检索越精准）
    query_text = f"{case_type} {facts} {claims_text}"

    # RAG 检索：查法规（laws）+ 司法解释（interpretations）双通道
    rag_result = retrieval_engine.search(
        query=query_text,          # 检索查询文本
        task_type="legal_document_gen",  # 任务类型：法律文书生成（检索策略按任务差异化）
        top_k=8,                   # 返回 Top-8 条最相关结果
        sources=["laws", "interpretations"],  # 限定检索来源：法规 + 司法解释
        contract_type=dispute_type,  # 附带合同类型/纠纷类型作为过滤条件
    )
    # 取出检索到的法条引用列表（每条含 law_name/article_no/content/source）
    rag_laws = rag_result.get("citations", [])
    # 取出检索上下文文本（拼接后的法条摘要，供 LLM 引用）
    rag_context = rag_result.get("research_context", "")

    # 构造当事人信息（供文书抬头使用）
    parties = case_summary.get("parties", {})
    # 原告：优先取 case_summary 中抽取的原告，其次用户填写的 plaintiff，最后兜底"原告"
    plaintiff = parties.get("plaintiff", state.get("plaintiff", "原告"))
    # 被告：同理，兜底"被告"
    defendant = parties.get("defendant", state.get("defendant", "被告"))

    # 打印日志：展示检索到的法条数量与质量分（quality_score，RAG 引擎的置信度指标）
    print(f"  检索到 {len(rag_laws)} 条相关法条, 质量分 {rag_result.get('quality_score', 0):.2f}")

    # ── 构造 LLM Prompt（提示词）──
    # System 提示词：设定 LLM 为"专业法律文书起草专家"，并给出 4 条硬性约束
    # （重点是约束 1：法条引用必须来自检索结果，禁止编造 → 防幻觉设计）
    system = """你是专业法律文书起草专家。请根据用户提供的案情信息和检索到的法条,
按照指定的文书模板生成完整的法律文书草稿。

**硬性约束:**
1. 法条引用必须来自下面【检索到的法条】列表, 不得编造任何法条;
2. 每条引用须标注来源(法律全称+条号), 并确保与案情匹配;
3. 若某领域未检索到相关法条, 标注"（未检索到该领域相关法条）"而非捏造;
4. 文书格式为 Markdown, 按模板结构输出。"""

    # User 提示词：把模板 ID、案由、当事人、事实、诉求、检索法条全部嵌入，
    # 并要求 LLM 输出固定 JSON（draft_content + cited_laws 数组）
    user = f"""【文书模板】{template_id}
【案由】{case_type}
【原告】{plaintiff}
【被告】{defendant}
【案情事实】{facts[:2000]}
【诉讼请求】{claims_text[:1000]}
【事发时间】{state.get("incident_date", "")}

【检索到的法条(仅引用这些, 不得编造)】
{rag_context[:3000] if rag_context else "（未检索到相关法条, 请勿编造法条引用）"}

请输出 JSON 格式:
{{
    "draft_content": "完整文书草稿 (Markdown, 含诉讼请求/事实与理由/证据清单/引用法条)",
    "cited_laws": [
        {{"law_name": "中华人民共和国合同法", "article_no": "第一百零七条", "content": "条文原文", "source": "来源标签"}}
    ]
}}"""
    try:
        # 调用 LLM：SystemMessage(起草规则) + HumanMessage(用户材料与检索结果)
        resp = my_llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        # 取出返回文本并去除首尾空白
        text = resp.content.strip()
        # 处理 ```json ... ``` 代码块包裹（LLM 常见输出格式，需剥壳再解析）
        if "```json" in text:
            # 取第一个 ```json 之后、下一个 ``` 之前的内容
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            # 普通 ``` 包裹时：截取第一个 { 到最后一个 } 之间的 JSON 片段
            s = text.find("{"); e = text.rfind("}") + 1
            # 校验截取区间合法
            if s >= 0 and e > s:
                text = text[s:e]
        # 解析 JSON 为字典：含 draft_content（草稿）与 cited_laws（引用法条列表）
        result = json.loads(text)
        # 取出文书草稿文本，缺失时为空字符串
        draft = result.get("draft_content", "")
        # 取出引用法条列表，缺失时为空列表
        cited = result.get("cited_laws", [])
        # 若 LLM 返回空草稿或草稿过短（<50 字符，说明内容不完整），使用兜底模板
        if not draft or len(draft) < 50:
            draft = _fallback_draft(case_type, plaintiff, defendant, facts, claims_text)
        # 若 LLM 未返回任何引用，则退而使用 RAG 检索到的前 5 条法条（保证引用非空）
        if not cited:
            cited = rag_laws[:5] if rag_laws else []
        # 打印日志：草稿长度（字数）与引用法条数量（便于人工检查产出规模）
        print(f"  草稿长度: {len(draft)} 字, 引用法条: {len(cited)} 条")
        # 返回三个产物：draft_content（草稿）、cited_laws（引用）、rag_retrieved_laws（检索原始结果）
        return {"draft_content": draft, "cited_laws": cited, "rag_retrieved_laws": rag_laws}
    except Exception as e:
        # LLM 调用或解析失败：打印告警，改用兜底模板生成草稿（保证流程不断裂）
        print(f"  ⚠️ 条款填充 LLM 失败: {e}, 使用兜底模板")
        # 生成兜底草稿（不依赖 LLM，纯模板拼装）
        draft = _fallback_draft(case_type, plaintiff, defendant, facts, claims_text)
    # ==== 微调数据收集 ====
    # 微调样本收集块（可选旁路）：记录本节点的输入/输出用于后续模型微调
    try:
        # 构造微调输入：取 state["input"]，转字符串并截取前 2000 字符
        _ft_input = str(state.get("input", "") or "")[:2000]
        # 注意：此行是"裸字典表达式"（bare dict expression），单独成行无任何效果，
        # 属于遗留的无操作语句，此处按原样保留，不改动逻辑。
        {"filled_doc": state.get("filled_doc", "")
}
        # 调用微调样本收集器（记录节点名、输入、输出、任务类型）
        collect_ft_sample("doc_clause_fill", _ft_input, _ft_output,
                          task_type=state.get("task_type", ""))
    except Exception:
        # 微调收集失败（如 _ft_output 未定义）：进入此分支，静默处理后返回兜底产物
        pass
        # 返回兜底产物：草稿用刚生成的兜底模板，引用用 RAG 检索的前 5 条法条
        return {"draft_content": draft, "cited_laws": rag_laws[:5] if rag_laws else [], "rag_retrieved_laws": rag_laws}


def _fallback_draft(case_type, plaintiff, defendant, facts, claims_text) -> str:
    """兜底文书模板(LLM 失败时使用)。"""
    # 【功能】生成一份结构完整的民事起诉状兜底草稿（纯模板拼装，不依赖 LLM）。
    # 【参数】
    #     case_type (str): 案由/案件类型（如"合同纠纷"）
    #     plaintiff (str): 原告名称
    #     defendant (str): 被告名称
    #     facts (str): 事实描述文本
    #     claims_text (str): 诉讼请求文本
    # 【返回值】
    #     str: Markdown 格式的民事起诉状草稿（含当事人/诉讼请求/事实与理由/证据清单/引用法条）
    # 【逻辑】
    #     1. 用 f-string 三引号字符串按固定模板拼装；
    #     2. 诉讼请求缺失时给出默认话术"请求法院依法支持原告诉请"；
    #     3. 事实缺失时提示"（请补充事实描述）"；
    #     4. 文末附免责声明（须经执业律师审阅签章后正式使用）。
    return f"""# 民事起诉状

## 当事人信息
- **原告**: {plaintiff}
- **被告**: {defendant}

## 诉讼请求
{claims_text if claims_text else "1. 请求法院依法支持原告诉请。"}

## 事实与理由
{facts[:2000] if facts else "（请补充事实描述）"}

## 证据清单
（请补充证据材料）

## 引用法条
（引用法条待补充）

---
*本文书由法智引擎辅助生成, 须经执业律师审阅签章后正式使用。*
"""
