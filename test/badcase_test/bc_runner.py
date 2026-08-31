# -*- coding: utf-8 -*-
"""badcase 执行器

==============================================================================
【与 test/t_runner.py 的关系】
    复用 test/t_tracer.py (节点级轨迹 + 耗时 + token) 与 test/t_metrics.py
    (P@5 / R@10 / MRR / 状态断言), 在此基础上补三件事:

    1. **完整节点路径断言** —— t_metrics.normalize_route 只保留主图层节点名,
       无法区分"contract_compliance 内部走到了 text_recognize 还是 preprocess"。
       badcase 定位必须精确到子图内节点, 因此本执行器额外做 full_route 断言
       (形如 "contract_compliance::preprocess::party_identify")。

    2. **badcase 专属质量规则** —— citation_has_law_name / retry_not_wasted /
       output_contains / no_placeholder_in_doc / risk_items_nonempty,
       这些是 badcase 库特有的"业务可交付性"判据。

    3. **节点级失败归因** —— 把失败定位到具体节点而不是笼统的"任务失败"。

【CLI】
    python bc_runner.py --probe                 # 只跑探针(不烧 LLM)
    python bc_runner.py                         # 跑全部 10 条
    python bc_runner.py --case BC-06 --case BC-10
    python bc_runner.py --limit 2
    python bc_runner.py --skip-probe
==============================================================================
"""
import os
import sys
import json
import time
import argparse

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_PKG_DIR = os.path.dirname(_TEST_DIR)          # test/
_ROOT = os.path.dirname(_PKG_DIR)              # 项目根
for _p in (_ROOT, _PKG_DIR, _TEST_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from bc_cases import all_cases, get_case                     # noqa: E402
from t_config import apply_env, SAFE_MODE, CASE_TIMEOUT_SEC, TASK_META  # noqa: E402
import t_metrics as M                                        # noqa: E402

apply_env()   # 必须在任何后端 import 之前


# ============================================================================
# 修正版 golden 匹配器 (仅作用于 badcase 套件进程内, 不改共享 t_metrics)
# ----------------------------------------------------------------------------
# 原 t_metrics.citation_matches_golden 在 golden 指定了条款号时, 要求
# 「条款号对 + (文档名对 或 内容锚点对)」, 即条款号只是必要条件而非充分条件。
# 实测发现两条会导致误判为 0 的真实情况:
#   (1) legal_qa 路径回填的 citation.law_name 恒为空 (引用溯源缺陷 P2);
#   (2) FAISS 召回的 content 被截断 (如第十三条只取到导语,
#       "取得个人的同意"等 must_any 锚点不在截断后的 content 中)。
# 条款号(article_no)是法条的权威标识 —— 一旦 golden.article 与
# citation.article_no 精确/包含匹配, 即应判为命中, 不应被 content 截断或
# 空 law_name 拖累。无 article 期望时仍用文档名匹配(保持原口径)。
# ============================================================================
def _bc_norm_article(s):
    if not s:
        return ""
    s = str(s).strip()
    return s.replace("（", "(").replace("）", ")").replace(" ", "")


def _bc_citation_matches(citation, golden):
    doc = golden.get("doc", "")
    if not doc:
        return False
    article_no = str(citation.get("article_no", "") or "")
    ga = _bc_norm_article(golden.get("article", ""))
    ca = _bc_norm_article(article_no)
    if ga:
        # 条款号命中即视为命中 (权威标识, 不受 content 截断 / 空 law_name 影响)
        if ca and (ga == ca or ga in ca or ca in ga):
            return True
        return False
    # 无条款号期望: 文档名匹配 (与原口径一致)
    title = str(citation.get("title", "") or "")
    law_name = str(citation.get("law_name", "") or "")
    content = str(citation.get("content", "") or "")
    doc_main = doc.replace("中华人民共和国", "")
    for suf in ("法", "条例", "办法", "规定", "解释", "实施细则", "决定",
                "通知", "批复", "答复", "指引", "若干问题"):
        if doc_main.endswith(suf):
            doc_main = doc_main[: -len(suf)]
    doc_main = doc_main.strip()
    hay = f"{title} {law_name} {content}"
    return bool((doc in hay) or (doc_main and doc_main in hay))


# 仅在 badcase 进程内替换匹配器 (不影响 test/t_metrics 的其它调用方)
M.citation_matches_golden = _bc_citation_matches

RESULTS_JSON = os.path.join(_TEST_DIR, "badcase_results.json")
PROBE_JSON = os.path.join(_TEST_DIR, "probe_report.json")


# ============================================================================
# 一、完整节点路径 (带子图命名空间)
# ============================================================================
def full_route(node_trace):
    """返回带子图命名空间的完整执行路径, 形如:
        ['xiaohongshu_publish_intent',
         'intent_router',
         'contract_compliance::text_recognize',
         'contract_compliance::preprocess::party_identify', ...]
    """
    out = []
    for nd in node_trace or []:
        ns = nd.get("namespace") or ()
        nm = nd.get("name") or ""
        full = "::".join(list(ns) + [nm]) if ns else nm
        if full not in out:
            out.append(full)
    return out


def check_route(actual_full, expected):
    """完整路径断言: contains 为子序列, excludes 为禁止出现"""
    problems = []
    for node in (expected.get("full_route_contains") or []):
        if not any(a == node or a.endswith("::" + node) or node in a
                   for a in actual_full):
            problems.append(f"未执行节点 `{node}`")
    for node in (expected.get("full_route_excludes") or []):
        if any(a == node or a.endswith("::" + node) for a in actual_full):
            problems.append(f"不应执行的节点 `{node}` 被执行")
    return (not problems), ("; ".join(problems) if problems else "完整路径符合预期")


def detect_branch(node_trace, final_state, task):
    """分支识别(与 t_runner 一致, 补充 badcase 关心的分支)"""
    names = {nd["name"] for nd in (node_trace or [])}
    if "llm_direct_out" in names:
        return "qa:llm_direct_out"
    if "qa_retrieval" in names or "legal_qa_final_answer" in names:
        return "qa:retrieval"
    if final_state.get("text_recognize_flag") == "block":
        return "text_recognize:block"
    if final_state.get("doc_empty_flag") == "block":
        return "doc_empty_guard:block"
    if final_state.get("need_clarify"):
        return "docgen:clarify"
    if "doc_final_delivery" in names:
        return "docgen:full"
    rc = final_state.get("quality_retry_count", 0) or 0
    if rc >= 1:
        return f"retrieval:retried_{rc}"
    if final_state.get("fusion_mode") == "single_source":
        return "retrieval:single_source"
    return ""


# ============================================================================
# 二、badcase 专属质量规则
# ============================================================================
def _doc_text(final_state):
    for f in ("output", "generated_document", "final_document", "document",
              "legal_qa_answer", "final_report_markdown"):
        v = final_state.get(f)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def check_badcase_quality(final_state, case, retrieval):
    """返回 defects(list), needs_edit(bool), needs_edit_strict(bool)"""
    defects = []
    citations = final_state.get("citations") or []
    out = _doc_text(final_state)
    strict_bad = False      # blocker 级: 必须人工修改
    major_bad = False       # major 级: 建议人工修改

    for qc in (case.get("quality_checks") or []):
        rule = qc.get("rule")
        sev = qc.get("severity", "major")

        # ---- 引用不可溯源: citation 缺 law_name, 且 title 不像法律名 ----
        if rule == "citation_has_law_name":
            if citations:
                no_law = [c for c in citations
                          if not str(c.get("law_name", "") or "").strip()]
                if no_law:
                    defects.append({
                        "rule": rule, "severity": sev,
                        "detail": (f"{len(no_law)}/{len(citations)} 条引用缺 law_name, "
                                   f"示例 title={str((no_law[0].get('title') or ''))[:30]!r} "
                                   f"article={no_law[0].get('article_no')!r} → 用户无法溯源"),
                    })
                    major_bad = True

        # ---- 重试空转: 重试了但关键词没变 ----
        elif rule == "retry_not_wasted":
            rc = final_state.get("quality_retry_count", 0) or 0
            if rc >= 1:
                defects.append({
                    "rule": rule, "severity": sev,
                    "detail": (f"质量门重试 {rc} 次才放行 "
                               f"(quality_score={final_state.get('quality_score')})。"
                               f"每次重试会重跑 intent_decompose(LLM) + entity_recall"
                               f"(Neo4j+FAISS) + precision_filter(embedding) → "
                               f"成本/延迟近似 ×{rc + 1}"),
                })
                major_bad = True

        # ---- 输出必须包含指定文案 (守卫提示/追问) ----
        elif rule == "output_contains":
            if qc.get("value") not in out:
                defects.append({
                    "rule": rule, "severity": sev,
                    "detail": f"output 未包含预期文案 {qc.get('value')!r}",
                })
                strict_bad = True

        # ---- 占位符/残缺文书 ----
        elif rule == "no_placeholder":
            for ph in ("XXX", "xxx", "TODO", "【待填写】", "【请填写】", "None", "null"):
                if ph in out:
                    defects.append({
                        "rule": rule, "severity": sev,
                        "detail": f"输出含占位符 {ph!r}",
                    })
                    strict_bad = True
                    break

        elif rule == "no_placeholder_in_doc":
            # 文书生成: 当事人仍是"原告"/"被告"泛称 = 残缺文书
            for ph in ("原告", "被告"):
                if f"{ph}：" in out or f"{ph}:" in out:
                    seg = out[out.find(ph): out.find(ph) + 12]
                    if "某某" in seg:
                        defects.append({
                            "rule": rule, "severity": sev,
                            "detail": f"文书当事人仍为泛称 {ph!r} → 残缺文书",
                        })
                        strict_bad = True
                        break

        elif rule == "no_hallucinated_citation":
            # 负样本用例: 检索为空却仍给出了带条号的引用 → 高度疑似编造
            if not citations and out:
                import re as _re
                arts = _re.findall(r"第[一二三四五六七八九十百千零〇\d]+条", out)
                cases_no = _re.findall(r"[（(]\d{4}[）)][^\s，,。]{2,10}号", out)
                if arts or cases_no:
                    defects.append({
                        "rule": rule, "severity": "blocker",
                        "detail": (f"检索结果为空, 但输出中出现了 {len(arts)} 个条号 / "
                                   f"{len(cases_no)} 个案号 → 疑似编造依据"),
                    })
                    strict_bad = True

        elif rule == "retrieval_nonempty":
            # 正样本用例: 检索为空 = 答案无依据 = 该次交付对用户无价值, 必须人工介入
            if not citations:
                defects.append({
                    "rule": rule, "severity": sev,
                    "detail": ("检索结果为空 (citation_count=0), 输出只能是'无依据'兜底文案 → "
                               "本次交付对用户无价值, 必须人工介入或补充语料"),
                })
                strict_bad = True

        elif rule == "risk_items_nonempty":
            ri = (final_state.get("merged_risk_items")
                  or final_state.get("risk_items") or [])
            if not ri:
                defects.append({
                    "rule": rule, "severity": sev,
                    "detail": "合同/合规审查未产出任何风险项 → 报告对用户无价值",
                })
                strict_bad = True

    return defects, (major_bad or strict_bad), strict_bad


# ============================================================================
# 三、节点级失败归因
# ============================================================================
_NODE_OWNER = [
    ("intent_router", "intent_router_node", "意图路由(LLM 分类 + 模糊匹配兜底)"),
    ("qa_intent_classify", "legal_qa_intent_node", "QA 三级路由(是否法律相关)"),
    ("retrieval_intent_decompose", "retrieval_intent_decompose_node",
     "知识源挂载 + LLM 关键词提取 + 意图判定"),
    ("retrieval_entity_recall", "retrieval_entity_recall_node",
     "图谱 Cypher 召回 + FAISS 语义召回"),
    ("retrieval_precision_filter", "retrieval_precision_filter_node",
     "语义重排 + 来源分级闸门"),
    ("retrieval_fusion_ranking", "retrieval_fusion_ranking_node",
     "7 项融合打分 + 去重 + 分级 + 质量分"),
    ("quality_gate_retry", "quality_gate_retry_node", "质量门判定 + 关键词扩展重试"),
    ("context_pack", "retrieval_output_pack_node", "按 task_type 分化输出"),
    ("text_recognize", "text_recognize_node", "文本路径守卫 + 输入归一化"),
    ("doc_extract", "doc_extract_node", "文档解析"),
    ("doc_empty_guard", "doc_empty_guard_node", "空/损坏文档守卫"),
    ("party_identify", "party_identify_node", "甲乙方识别"),
    ("contract_classify", "contract_classify_node", "合同类型分类"),
    ("full_text_segment", "full_text_segment_node", "全文切分"),
    ("numeric_extract", "numeric_extract_node", "数值抽取"),
    ("llm_query_extract", "llm_query_extract_node", "检索查询构造"),
    ("parallel_dual_review", "parallel_dual_review_node", "并行双审"),
    ("conflict_resolution", "conflict_resolution_node", "双审冲突消解"),
    ("numeric_validate", "numeric_validate_node", "数值一致性校验"),
    ("risk_aggregate", "risk_aggregate_node", "三路风险聚合"),
    ("final_delivery", "final_delivery_node", "审查报告交付"),
    ("doc_case_analyze", "doc_case_analyze_node", "案情结构化 + 澄清判定"),
    ("doc_template_match", "doc_template_match_node", "文书模板匹配"),
    ("doc_query_plan", "doc_query_plan_node", "法条/类案双查询规划"),
    ("doc_parallel_retrieve", "doc_parallel_retrieve_node", "双路并发检索"),
    ("doc_clause_fill", "doc_clause_fill_node", "条款填充"),
    ("doc_risk_analysis", "doc_risk_analysis_node", "文书风险分析"),
    ("doc_final_delivery", "doc_final_delivery_node", "文书交付"),
    ("legal_qa_final_answer", "legal_qa_final_answer_node", "问答出答"),
]


def attribute_node_failure(rec):
    """把失败归因到最可能的节点"""
    causes = []
    fs = rec.get("final_state") or {}
    fr = rec.get("full_route") or []
    citations = fs.get("citations") or []
    qs = fs.get("quality_score")
    rc = fs.get("quality_retry_count", 0) or 0
    mounted = fs.get("mounted_sources") or []

    def _has(name):
        return any(name in x for x in fr)

    if not citations:
        causes.append({
            "node": "retrieval_entity_recall",
            "file": "nodes/retrieval_nodes/retrieval_entity_recall_node.py",
            "func": "_graph_entity_recall / _faiss_semantic_recall",
            "reason": "召回结果为空 → 下游 precision/fusion 全链路空转",
            "how_to_confirm": "看该节点日志里 '召回汇总(两通道): 图谱=0, FAISS=0' "
                              "或 '[WARNING] 图谱召回[...]失败'",
        })
    if rc >= 1:
        causes.append({
            "node": "quality_gate_retry",
            "file": "nodes/retrieval_nodes/quality_gate_retry_node.py",
            "func": "quality_gate_retry_node / _expand_keywords",
            "reason": f"质量门重试 {rc} 次, 每次重跑 intent_decompose→entity_recall→"
                      f"precision_filter→fusion, 成本与延迟近似 ×{rc + 1}",
            "how_to_confirm": "state['quality_retry_count'] 与节点轨迹中 "
                              "retrieval_intent_decompose 出现次数",
        })
    if citations and not any(str(c.get("law_name", "") or "").strip()
                             for c in citations):
        causes.append({
            "node": "retrieval_entity_recall",
            "file": "nodes/retrieval_nodes/retrieval_entity_recall_node.py",
            "func": "recall_results.append({...})",
            "reason": "citation 缺 law_name 字段(Cypher 已 SELECT 但未写入返回字典) "
                      "→ 引用不可溯源, 且 output_pack 分类退化",
            "how_to_confirm": "state['citations'][0].keys() 中无 'law_name'",
        })
    if _has("text_recognize") and fs.get("text_recognize_flag") == "block":
        causes.append({
            "node": "text_recognize",
            "file": "nodes/preprocess_nodes/text_recognize_node.py",
            "func": "_score_contract_likeness",
            "reason": "输入被守卫拦截(这是预期行为, 需确认是否误拦)",
            "how_to_confirm": "节点日志 '合同语义评分=X' 与 _PASS_THRESHOLD=3 比较",
        })
    if fs.get("need_clarify"):
        causes.append({
            "node": "doc_case_analyze",
            "file": "nodes/docgen_nodes/doc_case_analyze_node.py",
            "func": "doc_case_analyze_node",
            "reason": "案情信息不足 → 澄清守卫终止生成(预期行为)",
            "how_to_confirm": "state['need_clarify']=True 且 "
                              "docgen 后续节点未出现在轨迹中",
        })
    if rec.get("mount_effective_hits") == 0 and mounted:
        causes.append({
            "node": "retrieval_intent_decompose",
            "file": "nodes/retrieval_nodes/retrieval_intent_decompose_node.py",
            "func": "_build_source_mounts / KEYWORD_RULES",
            "reason": f"关键词在已挂载源 {mounted} 上的实体命中数为 0 "
                      f"(存在未挂载源的孤儿命中, 见探针 mount_aware_reachability)",
            "how_to_confirm": "对比 probe_report.json 中该用例的 "
                              "effective_hits 与 orphan_hits",
        })
    return causes


# ============================================================================
# 四、单条用例执行
# ============================================================================
def run_one_case(case, probe, timeout_sec=None):
    from t_tracer import trace_run, apply_safe_mode, _COLLECTOR, get_graph
    apply_safe_mode(verbose=False)
    _COLLECTOR.install()
    get_graph()

    meta = TASK_META.get(case["task"], {})
    rec = {
        "id": case["id"], "task": case["task"],
        "task_name": meta.get("name", case["task"]),
        "title": case.get("title", ""),
        "severity": case.get("severity", ""),
        "focus": case.get("focus", []),
        "hypothesis": case.get("hypothesis", ""),
        "expect_recall_zero": bool(case.get("expect_recall_zero")),
        "input_preview": (case.get("input") or "")[:200],
        "keywords": case.get("keywords", []),
        "executed": False, "exec_success": False, "biz_success": False,
        "latency_sec": None, "tokens_prompt": 0, "tokens_completion": 0,
        "llm_calls": 0, "cost": 0.0,
        "node_trace": [], "main_route": [], "full_route": [],
        "branch": "", "expected_branch": (case.get("expected") or {}).get("branch", ""),
        "route_ok": True, "route_detail": "",
        "state_failures": [], "defects": [], "causes": [],
        "needs_manual_edit": False, "needs_manual_edit_strict": False,
        "citations": [], "citation_count": 0, "quality_score": None,
        "quality_retry_count": 0, "mounted_sources": [],
        "output_preview": "", "retrieval": {},
        "error": None, "error_type": None, "timed_out": False,
    }

    # 挂载感知可达性(来自探针, 无需跑图)
    for r in (probe.get("mount_aware_reachability") or []):
        if r["id"] == case["id"]:
            rec["mount_effective_hits"] = r.get("effective_hits")
            rec["mount_orphan_hits"] = r.get("orphan_hits")
            rec["mounted_sources_probe"] = r.get("mounted_sources")
            rec["keyword_reach"] = r.get("keywords")
            break

    init_state = {"input": case.get("input", ""), "task_type": case["task"]}
    for k, v in (case.get("extra_state") or {}).items():
        if v is not None:
            init_state[k] = v

    tr = trace_run(init_state, timeout_sec=timeout_sec)

    rec["executed"] = True
    rec["latency_sec"] = tr.get("latency_sec")
    rec["tokens_prompt"] = tr.get("tokens_prompt", 0)
    rec["tokens_completion"] = tr.get("tokens_completion", 0)
    rec["llm_calls"] = tr.get("llm_calls", 0)
    rec["node_trace"] = tr.get("node_trace", [])
    rec["error"] = tr.get("error")
    rec["error_type"] = tr.get("error_type")
    rec["timed_out"] = tr.get("timed_out", False)
    rec["interrupted"] = tr.get("interrupted", False)

    fs = tr.get("final_state") or {}
    rec["main_route"] = M.normalize_route(rec["node_trace"])
    rec["full_route"] = full_route(rec["node_trace"])
    rec["branch"] = detect_branch(rec["node_trace"], fs, case["task"])
    rec["exec_success"] = (not rec["error"]) and bool(rec["node_trace"])

    citations = fs.get("citations") or []
    rec["citations"] = [
        {"title": c.get("title", ""), "article_no": c.get("article_no", ""),
         "law_name": c.get("law_name", ""), "source": c.get("source", ""),
         "grade": c.get("grade", ""),
         "final_score": round(float(c.get("final_score", 0) or 0), 3),
         "content": str(c.get("content", ""))[:160]}
        for c in citations if isinstance(c, dict)
    ]
    rec["citation_count"] = len(citations)
    rec["quality_score"] = fs.get("quality_score")
    rec["quality_retry_count"] = fs.get("quality_retry_count", 0) or 0
    rec["mounted_sources"] = fs.get("mounted_sources") or []
    rec["domain_sources"] = fs.get("domain_sources") or []
    rec["fusion_mode"] = fs.get("fusion_mode", "")
    rec["retrieval_intents"] = fs.get("retrieval_intents") or []
    rec["retrieval_keywords"] = fs.get("retrieval_keywords") or []
    rec["output_preview"] = _doc_text(fs)[:800]
    rec["final_state"] = {
        k: fs.get(k) for k in (
            "task_type", "is_legal_related", "text_recognize_flag", "doc_empty_flag",
            "is_contract_input", "contract_type", "need_clarify", "clarify_question",
            "need_user_confirm", "need_lawyer_review", "human_intervention_needed",
            "quality_score", "quality_retry_count", "quality_gate_passed",
            "fusion_mode", "coverage_ratio", "fabao_retry_eligible",
            "risk_level", "overall_risk_score", "template_id", "template_confidence",
        )
    }

    # ---- 检索指标 ----
    rec["retrieval"] = M.retrieval_metrics(citations, case.get("golden") or [])

    # ---- 路径断言 ----
    exp = case.get("expected") or {}
    r_ok, r_detail = M.compare_route(rec["main_route"], exp)
    f_ok, f_detail = check_route(rec["full_route"], exp)
    rec["route_ok"] = r_ok and f_ok
    rec["route_detail"] = " | ".join(x for x in (r_detail, f_detail) if x)

    eb = rec["expected_branch"]
    if eb and rec["branch"] != eb:
        rec["route_ok"] = False
        rec["route_detail"] += f" | 分支不符: 期望 {eb}, 实际 {rec['branch'] or '(未识别)'}"

    rec["state_failures"] = M.check_state(fs, exp.get("state_checks"))

    # ---- 质量 / 人工修改率 ----
    defects, ne, nes = check_badcase_quality(fs, case, rec["retrieval"])
    rec["defects"] = defects
    rec["needs_manual_edit"] = ne
    rec["needs_manual_edit_strict"] = nes

    # ---- 业务成功率 ----
    if rec["expect_recall_zero"]:
        # 负样本: 不因检索为空判失败; 只看"是否编造 / 是否明确告知无依据"
        rec["biz_success"] = bool(rec["exec_success"] and rec["route_ok"]
                                  and not rec["state_failures"] and not nes)
    else:
        rec["biz_success"] = bool(rec["exec_success"] and rec["route_ok"]
                                  and not rec["state_failures"] and not nes)

    if not rec["exec_success"] or not rec["biz_success"]:
        rec["causes"] = attribute_node_failure(rec)

    rec["cost"] = round(rec["tokens_prompt"] / 1e6 * 2.0
                        + rec["tokens_completion"] / 1e6 * 6.0, 6)
    return rec


# ============================================================================
# 五、套件执行 + 汇总
# ============================================================================
def _pct(values, p):
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 3)


def aggregate(results):
    n = len(results)
    ret_cases = [r for r in results if (r.get("retrieval") or {}).get("applicable")]
    lats = [r["latency_sec"] for r in results if r.get("latency_sec") is not None]

    def _avg(key, src):
        vals = [r[key] for r in src if r.get(key) is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    node_stats = {}
    for r in results:
        for nd in (r.get("node_trace") or []):
            key = nd.get("full_name") or nd.get("name")
            st = node_stats.setdefault(
                key, {"node": key, "subgraph": nd.get("subgraph", ""),
                      "runs": 0, "total_sec": 0.0, "max_sec": 0.0, "errors": 0})
            st["runs"] += 1
            d = nd.get("duration_sec") or 0
            st["total_sec"] += d
            st["max_sec"] = max(st["max_sec"], d)
            if nd.get("error"):
                st["errors"] += 1
    for st in node_stats.values():
        st["avg_sec"] = round(st["total_sec"] / max(1, st["runs"]), 3)
        st["total_sec"] = round(st["total_sec"], 3)
        st["max_sec"] = round(st["max_sec"], 3)

    defect_by_rule = {}
    for r in results:
        for d in (r.get("defects") or []):
            defect_by_rule[d["rule"]] = defect_by_rule.get(d["rule"], 0) + 1

    cause_by_node = {}
    for r in results:
        for c in (r.get("causes") or []):
            cause_by_node[c["node"]] = cause_by_node.get(c["node"], 0) + 1

    return {
        "total_cases": n,
        "executed": sum(1 for r in results if r.get("executed")),
        "exec_success": sum(1 for r in results if r.get("exec_success")),
        "exec_success_rate": round(
            sum(1 for r in results if r.get("exec_success")) / max(1, n), 4),
        "biz_success": sum(1 for r in results if r.get("biz_success")),
        "biz_success_rate": round(
            sum(1 for r in results if r.get("biz_success")) / max(1, n), 4),
        "route_ok_rate": round(
            sum(1 for r in results if r.get("route_ok")) / max(1, n), 4),
        "retrieval_cases": len(ret_cases),
        "avg_precision": _avg("precision", [r["retrieval"] for r in ret_cases]),
        "avg_recall": _avg("recall", [r["retrieval"] for r in ret_cases]),
        "avg_mrr": _avg("mrr", [r["retrieval"] for r in ret_cases]),
        "hit1_rate": _avg("hit@1", [r["retrieval"] for r in ret_cases]),
        "hit5_rate": _avg("hit@5", [r["retrieval"] for r in ret_cases]),
        "avg_quality_score": _avg("quality_score", results),
        "empty_retrieval_cases": sum(1 for r in results if not r.get("citation_count")),
        "retry_cases": sum(1 for r in results if (r.get("quality_retry_count") or 0) >= 1),
        "avg_retry_count": _avg("quality_retry_count", results),
        "manual_edit_rate": round(
            sum(1 for r in results if r.get("needs_manual_edit")) / max(1, n), 4),
        "manual_edit_rate_strict": round(
            sum(1 for r in results if r.get("needs_manual_edit_strict")) / max(1, n), 4),
        "latency": {
            "p50": _pct(lats, 50), "p95": _pct(lats, 95),
            "max": max(lats) if lats else None,
            "avg": round(sum(lats) / len(lats), 3) if lats else None,
        },
        "tokens": {
            "prompt": sum(r.get("tokens_prompt", 0) for r in results),
            "completion": sum(r.get("tokens_completion", 0) for r in results),
            "total": sum(r.get("tokens_prompt", 0) + r.get("tokens_completion", 0)
                         for r in results),
            "llm_calls": sum(r.get("llm_calls", 0) for r in results),
        },
        "cost_total": round(sum(r.get("cost", 0) for r in results), 6),
        "node_stats": sorted(node_stats.values(),
                             key=lambda x: -x["total_sec"]),
        "defect_by_rule": defect_by_rule,
        "cause_by_node": cause_by_node,
        "severity_stats": {
            sev: {
                "total": sum(1 for r in results if r.get("severity") == sev),
                "biz_success": sum(1 for r in results
                                   if r.get("severity") == sev and r.get("biz_success")),
            } for sev in ("P0", "P1", "P2")
        },
    }


def run_suite(case_ids=None, limit=None, timeout_sec=None, with_probe=True,
              verbose=True):
    probe = {}
    if with_probe:
        import bc_probe
        probe = bc_probe.run_probe(verbose=verbose)
    elif os.path.exists(PROBE_JSON):
        with open(PROBE_JSON, "r", encoding="utf-8") as f:
            probe = json.load(f)

    cases = all_cases()
    if case_ids:
        cases = [c for c in cases if c["id"] in case_ids]
    if limit:
        cases = cases[:limit]

    if verbose:
        print("\n" + "=" * 74)
        print(f"【badcase 套件】{len(cases)} 条 | SAFE_MODE={SAFE_MODE} "
              f"| 超时 {timeout_sec or CASE_TIMEOUT_SEC}s")
        print("=" * 74)

    results = []
    for i, c in enumerate(cases, 1):
        if verbose:
            print(f"[{i:>2}/{len(cases)}] {c['id']} [{c['severity']}] "
                  f"{c['title'][:36]:<36} ... ", end="", flush=True)
        t0 = time.time()
        try:
            rec = run_one_case(c, probe, timeout_sec=timeout_sec)
        except Exception as e:
            import traceback
            rec = {
                "id": c["id"], "task": c["task"], "title": c.get("title", ""),
                "severity": c.get("severity", ""), "executed": True,
                "exec_success": False, "biz_success": False,
                "needs_manual_edit": True, "needs_manual_edit_strict": True,
                "error": f"{type(e).__name__}: {e}",
                "error_type": "RUNNER_EXCEPTION",
                "traceback": traceback.format_exc()[-2000:],
                "node_trace": [], "main_route": [], "full_route": [],
                "defects": [], "state_failures": [], "causes": [],
                "retrieval": {}, "latency_sec": round(time.time() - t0, 2),
                "tokens_prompt": 0, "tokens_completion": 0, "cost": 0.0,
                "focus": c.get("focus", []), "hypothesis": c.get("hypothesis", ""),
            }
        results.append(rec)
        if verbose:
            flag = ("✅" if rec.get("biz_success")
                    else "⚠️ " if rec.get("exec_success") else "❌")
            rm = rec.get("retrieval") or {}
            print(f"{flag} {rec.get('latency_sec')}s "
                  f"cit={rec.get('citation_count')} "
                  f"retry={rec.get('quality_retry_count')} "
                  f"P={_f(rm.get('precision'))} R={_f(rm.get('recall'))} "
                  f"MRR={_f(rm.get('mrr'))}")

        with open(RESULTS_JSON, "w", encoding="utf-8") as f:
            json.dump({"results": results, "probe": probe}, f,
                      ensure_ascii=False, indent=2, default=str)

    summary = aggregate(results)
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "summary": summary, "results": results, "probe": probe,
    }
    with open(RESULTS_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)

    if verbose:
        s = summary
        print("\n" + "=" * 74)
        print("【汇总】")
        print(f"  用例 {s['executed']}/{s['total_cases']}  "
              f"执行成功率 {s['exec_success_rate'] * 100:.1f}%  "
              f"业务成功率 {s['biz_success_rate'] * 100:.1f}%  "
              f"路由正确率 {s['route_ok_rate'] * 100:.1f}%")
        print(f"  检索 P@5={_f(s['avg_precision'])} R@10={_f(s['avg_recall'])} "
              f"MRR={_f(s['avg_mrr'])} Hit@1={_f(s['hit1_rate'])} "
              f"(适用 {s['retrieval_cases']} 条)")
        print(f"  空结果 {s['empty_retrieval_cases']} 条 | "
              f"触发重试 {s['retry_cases']} 条 | 平均重试 {_f(s['avg_retry_count'])} 次")
        print(f"  人工修改率(代理) {s['manual_edit_rate'] * 100:.1f}% "
              f"(严格 {s['manual_edit_rate_strict'] * 100:.1f}%)")
        print(f"  延迟 P50={s['latency']['p50']}s P95={s['latency']['p95']}s "
              f"Max={s['latency']['max']}s")
        print(f"  Token {s['tokens']['total']} / LLM {s['tokens']['llm_calls']} 次 "
              f"/ 估算成本 ¥{s['cost_total']}")
        print(f"\n  结果: {RESULTS_JSON}")
        print("=" * 74)
    return payload


def _f(v):
    return "-" if v is None else f"{v:.3f}"


def main():
    ap = argparse.ArgumentParser(description="badcase 测试执行器")
    ap.add_argument("--case", action="append", help="只跑指定用例(可多次)")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--timeout", type=int)
    ap.add_argument("--skip-probe", action="store_true")
    ap.add_argument("--probe-only", action="store_true", help="只跑探针, 不跑图")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    if a.probe_only:
        import bc_probe
        bc_probe.run_probe(verbose=True)
        return
    run_suite(case_ids=a.case, limit=a.limit, timeout_sec=a.timeout,
              with_probe=not a.skip_probe, verbose=not a.quiet)


if __name__ == "__main__":
    main()
