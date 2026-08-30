"""检索子图·出口统一节点: 按 task_type 分化输出

============================================================
文件名称: nodes/retrieval_nodes/retrieval_output_pack_node.py
文件作用: 检索子图出口 (统一收口节点)
============================================================

【这个文件是干什么的？】
    检索子图最后一个节点，按 task_type 分化输出：

    ① legal_research / case_search / legal_qa (纯检索路径, 主图直达):
       产出 state["output"] markdown + state["result_summary"] 结构化摘要。
       legal_qa 后续有 response_generation_node 做自然语言转化，但本节点仍需整理 citations。
       同时透传 citations / quality_score / research_context 到 state 顶层。

    ② contract_review / compliance_review (文档审查路径):
       产出 state["review_context_bundle"] Dict，含审查上下文打包 +
       _review_mode 标记 (dual / single_compliance)，供下游 dual_review 子图消费。
       覆盖度判定: 用 retrieval_keywords + citations 的 title/article_no/keyword
       做探针词，对 doc_segments 逐单元判定是否已被检索覆盖。

    ③ legal_document_gen (文书生成路径, 被 docgen_subgraph 内联调用):
       只透传 citations / quality_score / research_context 到 state 顶层。
       不产 output / result_summary / review_context_bundle ——
       文书生成的 clause_fill 直接读 state["citations"]，不需要额外打包。

【输出字段对照表】
    ┌─────────────────────┬──────────────────┬─────────────────┬────────────────────┐
    │ task_type            │ output           │ bundle          │ citations 透传     │
    ├─────────────────────┼──────────────────┼─────────────────┼────────────────────┤
    │ legal_research       │ ✅ markdown      │ ❌              │ ✅                 │
    │ case_search          │ ✅ markdown      │ ❌              │ ✅                 │
    │ legal_qa             │ ✅ markdown      │ ❌              │ ✅                 │
    │ contract_review      │ ❌               │ ✅ dual         │ ✅                 │
    │ compliance_review    │ ❌               │ ✅ single       │ ✅                 │
    │ legal_document_gen   │ ❌               │ ❌              │ ✅ (供 clause_fill)│
    └─────────────────────┴──────────────────┴─────────────────┴────────────────────┘
"""

from __004__langgraph_more_nodes.agent_state import AgentState


# ============================================================
# 打包参数
# ============================================================

BRIEF_CITATION_LIMIT = 12
BRIEF_CONTENT_CHARS = 220
UNRETRIEVED_TEXT_CHARS = 500
MIN_PROBE_LEN = 2

# 纯检索路径: 产 output markdown + result_summary
# legal_qa 后续有 response_generation_node 做自然语言转化，但本节点仍需整理 citations
_RETRIEVAL_OUTPUT_TASKS = {"legal_research", "case_search", "legal_qa"}

# 文档审查路径: 产 review_context_bundle
_REVIEW_BUNDLE_TASKS = {"contract_review", "compliance_review"}


def retrieval_output_pack_node(state: AgentState):
    """检索子图出口统一节点: 按 task_type 分化输出。

    读取字段:
        - retrieval_query / input (str)
        - retrieval_keywords (List[str])
        - citations (List[Dict])
        - research_context (str)
        - quality_score (float)
        - doc_segments (List[Dict])    仅文档审查路径用
        - task_type (str)

    写入字段 (按 task_type 分化):
        - citations / quality_score / research_context  → 所有任务都透传到 state 顶层
        - output / result_summary                       → 仅 legal_research / case_search / legal_qa
        - review_context_bundle                         → 仅 contract_review / compliance_review
    """
    print("--- 检索子图出口: 按 task_type 分化输出 ---")

    task_type = state.get("task_type", "")

    # ============================================================
    # 读取检索产物 (所有任务统一读取)
    # ============================================================
    retrieval_query = state.get("retrieval_query", "") or state.get("input", "") or ""
    raw_keywords = state.get("retrieval_keywords", []) or []
    retrieval_keywords = [k for k in raw_keywords if isinstance(k, str) and k.strip()]

    citations = state.get("citations", []) or []
    if not isinstance(citations, list):
        citations = []

    research_context = state.get("research_context", "") or ""
    if not isinstance(research_context, str):
        research_context = ""

    quality_score = state.get("quality_score", 0)
    if not isinstance(quality_score, (int, float)):
        quality_score = 0

    # ============================================================
    # 分化输出
    # ============================================================
    if task_type in _RETRIEVAL_OUTPUT_TASKS:
        # ---- 纯检索路径: 产 output markdown + result_summary ----
        output, result_summary = _build_retrieval_output(
            citations, research_context, quality_score, retrieval_query, task_type
        )
        state["output"] = output
        state["result_summary"] = result_summary
        state["fusion_mode"] = state.get("fusion_mode", "weighted")
        print(f"  [{task_type}] output 已生成 (质量分 {quality_score}, 命中 {len(citations)} 条)")

    elif task_type in _REVIEW_BUNDLE_TASKS:
        # ---- 文档审查路径: 产 review_context_bundle ----
        bundle = _build_review_bundle(
            retrieval_query, retrieval_keywords, citations,
            research_context, quality_score, state, task_type
        )
        state["review_context_bundle"] = bundle
        print(f"  [{task_type}] bundle 已打包 (引用 {len(citations)} 条, "
              f"覆盖率 {bundle['coverage_ratio']}, 模式 {bundle['_review_mode']})")

    else:
        # ---- 其他路径 (legal_document_gen / 未知): 只透传 ----
        print(f"  [{task_type}] 透传 citations ({len(citations)} 条), "
              f"质量分 {quality_score}")

    return state


# ============================================================
# 纯检索路径: output markdown 生成
# ============================================================

def _build_retrieval_output(citations, research_context, quality_score,
                            retrieval_query, task_type):
    """为纯检索路径生成面向用户的 markdown 总结 + 结构化摘要。

    返回 (output: str, result_summary: Dict)。
    """
    categories = {"法律法规": [], "裁判案例": [], "司法解释": [], "其他": []}
    for c in citations:
        if not isinstance(c, dict):
            continue
        title = c.get("title", "")
        if "法规" in title or "法律" in title or "法典" in title:
            categories["法律法规"].append(c)
        elif "案例" in title or "判决" in title or "判例" in title:
            categories["裁判案例"].append(c)
        elif "解释" in title:
            categories["司法解释"].append(c)
        else:
            categories["其他"].append(c)

    summary_parts = ["## 检索结果\n"]
    summary_parts.append(f"**查询**: {retrieval_query}\n")
    summary_parts.append(f"**质量分**: {quality_score}/100\n")
    summary_parts.append(f"**命中总数**: {len(citations)} 条\n")

    for cat_name, cat_items in categories.items():
        if cat_items:
            summary_parts.append(f"\n### {cat_name} ({len(cat_items)} 条)")
            for i, item in enumerate(cat_items[:5], 1):
                title = item.get("title", "")
                article = item.get("article_no", "")
                content = item.get("content", "")[:100]
                source = item.get("source", "")
                summary_parts.append(f"{i}. **{title}** {article} [{source}]\n   {content}")

    if not citations:
        summary_parts.append("\n⚠️ 未检索到相关结果, 建议调整关键词重试.")

    output = "\n".join(summary_parts)

    result_summary = {
        "query": retrieval_query,
        "total_results": len(citations),
        "quality_score": quality_score,
        "categories": {k: len(v) for k, v in categories.items()},
        "top_results": citations[:10],
    }
    return output, result_summary


# ============================================================
# 文档审查路径: review_context_bundle 打包
# ============================================================

def _build_review_bundle(retrieval_query, retrieval_keywords, citations,
                         research_context, quality_score, state, task_type):
    """为文档审查路径打包 review_context_bundle。

    含: 检索三件套 + 精简引用 + 覆盖度判定 + _review_mode 标记。
    """
    segments = state.get("doc_segments", []) or []
    if not isinstance(segments, list):
        segments = []

    # ---- 收集探针词 ----
    probes = set()
    for kw in retrieval_keywords:
        w = kw.strip()
        if len(w) >= MIN_PROBE_LEN:
            probes.add(w)
    for c in citations:
        if not isinstance(c, dict):
            continue
        for field in ("title", "article_no", "keyword"):
            v = c.get(field)
            if isinstance(v, str) and len(v.strip()) >= MIN_PROBE_LEN:
                probes.add(v.strip())

    # ---- 逐切分单元做覆盖度判定 ----
    retrieved_ids = []
    unretrieved = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        text = seg.get("text", "") or ""
        hit = any(p in text for p in probes) if probes else False
        if hit:
            retrieved_ids.append(seg.get("id"))
        else:
            unretrieved.append({
                "id": seg.get("id"),
                "type": seg.get("type", ""),
                "title": seg.get("title", ""),
                "text": text[:UNRETRIEVED_TEXT_CHARS],
            })

    segment_total = len(segments)
    coverage_ratio = round(len(retrieved_ids) / segment_total, 3) if segment_total else 0.0

    # ---- 精简引用 ----
    citations_brief = []
    for c in citations[:BRIEF_CITATION_LIMIT]:
        if not isinstance(c, dict):
            continue
        content = c.get("content", "") or ""
        citations_brief.append({
            "title": c.get("title", "") or "",
            "article_no": c.get("article_no", "") or "",
            "source": c.get("source", "") or "",
            "content": content[:BRIEF_CONTENT_CHARS],
        })

    # ---- _review_mode 标记 ----
    if task_type == "contract_review":
        review_mode = "dual"
    else:
        review_mode = "single_compliance"

    print(f"  [扇出模式] {task_type} → {review_mode}")

    return {
        "retrieval_query": retrieval_query,
        "retrieval_keywords": retrieval_keywords,
        "citations": citations,
        "citations_brief": citations_brief,
        "research_context": research_context,
        "quality_score": quality_score,
        "segment_total": segment_total,
        "retrieved_segment_ids": retrieved_ids,
        "unretrieved_segments": unretrieved,
        "coverage_ratio": coverage_ratio,
        "_review_mode": review_mode,
    }


# ============================================================
# 模块自测
# ============================================================
if __name__ == "__main__":
    # 测试 1: legal_research → output
    s1 = AgentState(
        task_type="legal_research",
        retrieval_query="买卖合同 违约金 上限 法律规定",
        retrieval_keywords=["违约金", "买卖合同"],
        citations=[{"title": "民法典", "article_no": "第五百八十五条",
                    "content": "当事人可以约定一方违约时应当支付违约金……", "source": "民法典"}],
        research_context="【民法典第五百八十五条】当事人可以约定……",
        quality_score=87,
    )
    retrieval_output_pack_node(s1)
    print("\n[legal_research] output 前 200 字:", str(s1.get("output", ""))[:200])
    print("[legal_research] bundle:", s1.get("review_context_bundle", "❌ 无 (正确)"))

    # 测试 2: contract_review → bundle
    s2 = AgentState(
        task_type="contract_review",
        retrieval_query="租赁合同违约金审查",
        retrieval_keywords=["违约金", "租赁合同"],
        citations=[{"title": "民法典", "article_no": "第五百八十五条",
                    "content": "违约金……", "source": "民法典"}],
        research_context="违约金条款",
        quality_score=75,
        doc_segments=[
            {"id": 1, "type": "preamble", "title": "前言", "text": "本合同由甲方与乙方签订。"},
            {"id": 2, "type": "clause", "title": "第八条", "text": "第八条 违约责任：逾期付款按千分之五支付违约金。"},
        ],
    )
    retrieval_output_pack_node(s2)
    b = s2["review_context_bundle"]
    print("\n[contract_review] 已覆盖:", b["retrieved_segment_ids"])
    print("[contract_review] 未覆盖:", [u["title"] for u in b["unretrieved_segments"]])
    print("[contract_review] 覆盖率:", b["coverage_ratio"])
    print("[contract_review] 模式:", b["_review_mode"])
    print("[contract_review] output:", s2.get("output", "❌ 无 (正确)"))

    # 测试 3: legal_qa → output (和 legal_research 一样)
    s3 = AgentState(
        task_type="legal_qa",
        retrieval_query="违约金过高怎么调整",
        retrieval_keywords=["违约金", "调整"],
        citations=[{"title": "民法典", "article_no": "第五百八十五条",
                    "content": "违约金……", "source": "民法典"}],
        research_context="民法典违约金",
        quality_score=80,
    )
    retrieval_output_pack_node(s3)
    print("\n[legal_qa] output 前 200 字:", str(s3.get("output", ""))[:200])
    print("[legal_qa] bundle:", s3.get("review_context_bundle", "❌ 无 (正确)"))

    # 测试 4: legal_document_gen → 透传, 无 output 无 bundle
    s4 = AgentState(
        task_type="legal_document_gen",
        retrieval_query="房屋租赁合同违约金",
        retrieval_keywords=["违约金", "租赁合同"],
        citations=[{"title": "民法典", "article_no": "第五百八十五条",
                    "content": "违约金……", "source": "laws", "source_ref": "laws#0"}],
        research_context="民法典违约金",
        quality_score=82,
    )
    retrieval_output_pack_node(s4)
    print("\n[legal_document_gen] output:", s4.get("output", "❌ 无 (正确)"))
    print("[legal_document_gen] bundle:", s4.get("review_context_bundle", "❌ 无 (正确)"))
    print("[legal_document_gen] citations 仍在 state:", len(s4.get("citations", [])))
