# -*- coding: utf-8 -*-
"""指标计算引擎

【指标体系设计 —— 技术 + 业务双视角】

┌─ 技术指标 (检索质量) ────────────────────────────────────────────┐
│ P@K       Top-K 中相关文档占比 —— 衡量"推给用户的前 K 条有多准"   │
│ R@K       Top-K 覆盖标准答案的比例 —— 衡量"该找到的找到没有"     │
│ MRR@K     首个正确答案的排名倒数 —— 衡量"好结果是否排在前面"     │
│ Hit@1/5   首条命中率 / 前五命中率                                 │
└──────────────────────────────────────────────────────────────────┘
┌─ 技术指标 (系统质量) ────────────────────────────────────────────┐
│ 执行成功率  图无异常跑到 END —— 系统健壮性                        │
│ 业务成功率  执行成功 + 路由正确 + 关键字段非空 + 依据充分          │
│ 节点健康度  各节点执行次数/失败次数/耗时分布                      │
└──────────────────────────────────────────────────────────────────┘
┌─ 业务指标 ───────────────────────────────────────────────────────┐
│ 人工修改率  AI 产出需律师实质性修改才能交付的比例 (代理口径)      │
│ 响应时延    P50/P95/Max 端到端 + 节点级瓶颈定位                   │
│ 调用成本    token 消耗 × 单价, 折算"单次任务成本"                 │
└──────────────────────────────────────────────────────────────────┘

【关于"人工修改率"的口径声明 —— 必须诚实】
    真值需要执业律师逐份标注, 无法自动化。本测试采用**缺陷规则代理口径**:
    用一组可机检的缺陷规则 (幻觉引用 / 输出残缺 / 占位符未替换 / 关键依据缺失)
    自动判定"存在必须人工干预的缺陷"。
    - manual_edit_rate_proxy: 命中任一 block/major 级缺陷
    - manual_edit_rate_strict: 仅命中 block 级 (幻觉 + 残缺, 法律上必须改)
    报告中两个数字同时呈现, 并在口径说明中标注这是 proxy 而非人工标注。
"""
import re
import math
from t_config import (TOP_K_PRECISION, TOP_K_RECALL, MRR_CUTOFF,
                      MIN_ACCEPTABLE_QUALITY_SCORE, PRICE_PROMPT_PER_1M,
                      PRICE_COMPLETION_PER_1M)


# ============================================================================
# 一、检索指标
# ============================================================================

def _norm_article(s):
    """条款号归一化: 全角/空格/括号统一, 便于 '第二十条' 与 '第 二十 条' 比对"""
    if not s:
        return ""
    s = str(s).strip()
    s = s.replace("（", "(").replace("）", ")").replace(" ", "")
    return s


_SUFFIX_RE = re.compile(r"(中华人民共和国|最高人民法院、最高人民检察院关于办理|关于|法|条例|办法|规定|若干问题|解释|实施细则|决定|通知|批复|答复|指引)$")


def _doc_main(doc):
    """文档主名: 去掉'中华人民共和国'前缀与法律后缀, 用于跨字段模糊对齐

    例: '个人独资企业法' -> '个人独资企业'
        '中华人民共和国个人信息保护法' -> '个人信息保护'
    """
    if not doc:
        return ""
    d = str(doc).replace("中华人民共和国", "")
    d = _SUFFIX_RE.sub("", d)
    return d.strip()


def _resolve_doc_key(query, candidates):
    """把 citation 里可能出现的文档名 (可能缺'法'字/带尾空格) 对齐到 manifest 的标准键

    返回匹配到的标准键; 找不到返回 None。
    """
    q = (query or "").strip()
    if not q:
        return None
    qm = _doc_main(q)
    for c in candidates:
        if not c:
            continue
        if c == q or _doc_main(c) == qm:
            return c
        cm = _doc_main(c)
        if cm and (cm in q or q in cm):
            return c
    return None


def citation_matches_golden(citation, golden):
    """判断一条 citation 是否命中一个 golden 标准答案

    匹配逻辑 (三重, 逐层收紧):
      1. 文档名匹配: citation 的 title / law_name / content 中出现 golden.doc 的关键片段
      2. 条款号匹配: 若 golden 给了 article, 则 citation.article_no 必须等于它
      3. 内容锚点: 若 golden 给了 must_any, 则 content 需至少包含其中一个锚点词
         (锚点仅用于"命中确认", 不作为唯一判据, 避免切分导致的假阴性)
    """
    if not isinstance(citation, dict):
        return False
    doc = golden.get("doc", "")
    if not doc:
        return False

    title = str(citation.get("title", "") or "")
    law_name = str(citation.get("law_name", "") or "")
    content = str(citation.get("content", "") or "")
    hay_doc = f"{title} {law_name} {content}"

    # 文档名匹配: 用主名(去前缀/后缀)在 title/content 中查找, 容忍缺'法'字
    doc_main = _doc_main(doc)
    doc_hit = (doc in hay_doc) or (doc_main and doc_main in hay_doc)

    # 条款号匹配 (主信号)
    ga = _norm_article(golden.get("article", ""))
    ca = _norm_article(citation.get("article_no", ""))
    if ga:
        if not ca:
            return False
        if ga != ca and ga not in ca and ca not in ga:
            return False

    # 命中判定: 条款号对 + (文档名对 或 内容锚点对)
    # 允许"文档名在 citation 里写得不全"时, 用 must_any 内容锚点兜底确认
    must_any = golden.get("must_any") or []
    if not ga:
        # 没有条款号期望: 必须文档名命中
        return bool(doc_hit)
    if doc_hit:
        return True
    if must_any and any(str(m) in content for m in must_any):
        return True
    return False


def retrieval_metrics(citations, goldens):
    """计算单条用例的检索指标

    Returns:
        dict: precision@5, recall@10, mrr@10, hit@1, hit@5, golden_hit_count,
              golden_total, first_hit_rank
    """
    citations = citations or []
    goldens = goldens or []
    res = {
        "precision": None, "recall": None, "mrr": None,
        "hit@1": None, "hit@5": None,
        "golden_hit_count": 0, "golden_total": len(goldens),
        "first_hit_rank": None, "applicable": False,
    }
    if not goldens:
        return res
    res["applicable"] = True

    # 每个 golden 首次被命中的排名
    golden_first_rank = {}
    for rank, c in enumerate(citations, start=1):
        for gi, g in enumerate(goldens):
            if gi in golden_first_rank:
                continue
            if citation_matches_golden(c, g):
                golden_first_rank[gi] = rank
                break

    hit_ranks = sorted(golden_first_rank.values())
    res["golden_hit_count"] = len(golden_first_rank)
    res["first_hit_rank"] = hit_ranks[0] if hit_ranks else None

    k_p = min(TOP_K_PRECISION, len(citations)) or TOP_K_PRECISION
    top_p = citations[:TOP_K_PRECISION]
    hits_in_p = sum(1 for c in top_p
                    if any(citation_matches_golden(c, g) for g in goldens))
    res["precision"] = round(hits_in_p / k_p, 4) if k_p else 0.0

    # 召回率: 在 top-K 窗口内, 有多少个 distinct golden 被首次命中 (≤1.0)
    hit_in_r = sum(1 for gi, rank in golden_first_rank.items()
                   if rank <= TOP_K_RECALL)
    res["recall"] = round(hit_in_r / len(goldens), 4) if goldens else None

    res["mrr"] = round(1.0 / hit_ranks[0], 4) if hit_ranks else 0.0
    res["hit@1"] = 1.0 if (hit_ranks and hit_ranks[0] == 1) else 0.0
    res["hit@5"] = 1.0 if any(r <= 5 for r in hit_ranks) else 0.0
    return res


# ============================================================================
# 二、状态流转比对
# ============================================================================

def normalize_route(node_trace):
    """把带子图命名空间的节点轨迹压平为"主图层节点名"序列

    node_trace 元素的 name 形如:
        ('r_retrieval:uuid',) + retrieval_intent_decompose
    主图层节点就是 namespace 的第一段 (无 ':' 分隔时即节点自身)。
    """
    seen, out = set(), []
    for nd in node_trace:
        raw_ns = nd.get("namespace") or ()
        ns0 = raw_ns[0] if raw_ns else ""
        main = ns0.split(":")[0] if ns0 else nd.get("name", "")
        if main and main not in seen:
            seen.add(main)
            out.append(main)
    return out


def compare_route(actual_main_route, expected):
    """比对实际主图路径与期望路径

    策略: 子序列匹配 (允许中间插入合法的重试/守卫分支), 而非全等。
    返回 (ok: bool, detail: str)
    """
    contains = expected.get("route_contains") or []
    excludes = expected.get("route_excludes") or []
    problems = []

    if contains:
        idx = 0
        for node in contains:
            try:
                pos = actual_main_route.index(node, idx)
                idx = pos + 1
            except ValueError:
                problems.append(f"缺失节点 `{node}`")
    for node in excludes:
        if node in actual_main_route:
            problems.append(f"不应执行的节点 `{node}` 被执行")

    return (not problems), ("; ".join(problems) if problems else "路径符合预期")


# ============================================================================
# 三、状态字段断言
# ============================================================================

def check_state(final_state, checks):
    """执行 AgentState 字段断言

    支持的 op: nonempty / eq / min_len / gte / lte / in
    """
    failures = []
    for ck in checks or []:
        field, op = ck.get("field"), ck.get("op")
        val = (final_state or {}).get(field)
        ok = False
        if op == "nonempty":
            ok = bool(val) and (not isinstance(val, str) or val.strip() != "")
        elif op == "eq":
            ok = (val == ck.get("value"))
        elif op == "min_len":
            ok = isinstance(val, (list, str)) and len(val) >= ck.get("value", 1)
        elif op == "gte":
            ok = isinstance(val, (int, float)) and val >= ck.get("value", 0)
        elif op == "lte":
            ok = isinstance(val, (int, float)) and val <= ck.get("value", 0)
        elif op == "in":
            ok = val in (ck.get("value") or [])
        if not ok:
            failures.append({
                "field": field, "op": op,
                "expected": ck.get("value"), "actual": _short(val),
            })
    return failures


def _short(v, n=120):
    s = str(v)
    return s[:n] + ("..." if len(s) > n else "")


# ============================================================================
# 四、人工修改率 —— 缺陷规则代理口径
# ============================================================================

_ARTICLE_RE = re.compile(r"第[一二三四五六七八九十百千零〇\d]+条")
_PLACEHOLDERS = ["原告", "被告", "XXX", "xxx", "TODO", "None", "null", "【待填写】",
                 "【请填写】", "某某"]


def _get_doc_field(final_state, field):
    """output 字段缺失时, 尝试从常见别名取 (文书生成有时落在 generated_document)"""
    if not final_state:
        return ""
    v = final_state.get(field)
    if v:
        return v if isinstance(v, str) else str(v)
    for alt in ("generated_document", "final_document", "document", "result_summary"):
        v = final_state.get(alt)
        if v:
            return v if isinstance(v, str) else str(v)
    return ""


def check_quality(final_state, quality_checks, retrieval_res, manifest):
    """执行人工修改率代理规则

    Returns:
        (defects: list, needs_edit: bool, needs_edit_strict: bool)
        defects 每项: {rule, severity, detail}
    """
    defects = []
    citations = (final_state or {}).get("citations") or []
    whitelist = set((manifest or {}).get("doc_name_whitelist") or [])
    doc_articles = (manifest or {}).get("doc_articles") or {}

    for qc in quality_checks or []:
        rule = qc.get("rule")
        sev = qc.get("severity", "major")
        bad, detail = False, ""

        if rule == "no_hallucinated_citation":
            # 幻觉判定: citation 来自检索结果, 本应都在库内。
            # 仅当"该文档确实无此条款号"或"全库都找不到此条款号"时才判为库外幻觉,
            # 避免 citation 的文档名写得不全(如缺'法'字)造成的误报。
            ghosts = []
            doc_keys = list(doc_articles.keys())
            all_arts = {_norm_article(a.get("article_no", ""))
                        for arts in doc_articles.values() for a in arts}
            for c in citations:
                if not isinstance(c, dict):
                    continue
                t = str(c.get("title", "") or "")
                ln = str(c.get("law_name", "") or "")
                art = _norm_article(c.get("article_no", ""))
                name = (ln or t).strip()
                if not name and not art:
                    continue
                resolved = _resolve_doc_key(name, doc_keys) if name else None
                is_ghost = False
                reason = ""
                if art:
                    if resolved:
                        arts = {_norm_article(a.get("article_no", ""))
                                for a in doc_articles.get(resolved, [])}
                        if arts and art not in arts:
                            is_ghost = True
                            reason = f"{name}{art} (该文档无此条款号)"
                    elif art not in all_arts:
                        is_ghost = True
                        reason = f"{name or '(未署名)'}{art} (知识库无此条款)"
                if is_ghost:
                    ghosts.append(reason)
            if ghosts:
                bad = True
                detail = "引用了知识库中不存在的法条: " + "; ".join(ghosts[:3])

        elif rule == "output_nonempty":
            if not _get_doc_field(final_state, "output").strip():
                bad, detail = True, "output 为空, 未产出任何可交付内容"

        elif rule == "citation_min":
            if len(citations) < qc.get("value", 1):
                bad = True
                detail = f"引用条数 {len(citations)} < 要求 {qc.get('value', 1)}"

        elif rule == "quality_score_min":
            qs = (final_state or {}).get("quality_score") or 0
            if float(qs) < float(qc.get("value", 0)):
                bad = True
                detail = f"检索质量分 {qs} < 阈值 {qc.get('value')}, 依据薄弱需人工补检索"

        elif rule == "golden_recall_min":
            need = qc.get("value", 1)
            got = (retrieval_res or {}).get("golden_hit_count", 0)
            if got < need:
                bad = True
                detail = (f"标准答案召回 {got}/{need} —— 关键法律依据缺失, "
                          f"律师需自行补检索")

        elif rule == "must_contain_any":
            text = _get_doc_field(final_state, qc.get("field", "output"))
            vals = qc.get("value") or []
            if not any(v in text for v in vals):
                bad = True
                detail = f"输出未包含任一关键要素 {vals[:3]}"

        elif rule == "must_not_contain":
            text = _get_doc_field(final_state, qc.get("field", "output"))
            hits = [v for v in (qc.get("value") or []) if v in text]
            if hits:
                bad = True
                detail = f"输出包含不应出现的内容: {hits[:3]}"

        elif rule == "risk_item_nonempty":
            ri = (final_state or {}).get("risk_items") or []
            post = (final_state or {}).get("post_conflict_risk_items") or []
            comp = (final_state or {}).get("compliance_risk_items") or []
            cont = (final_state or {}).get("contract_risk_items") or []
            if not (ri or post or comp or cont):
                bad, detail = True, "未产出任何风险项, 审核结论不可交付"

        elif rule == "document_field_filled":
            doc = _get_doc_field(final_state, "output")
            # 文书里仍保留未替换的占位符 = 残缺文书
            left = [p for p in _PLACEHOLDERS[:2] if p in doc and
                    f"{p}：" in doc.replace(" ", "") or
                    (p in doc and len(doc) < 200)]
            if not doc.strip():
                bad, detail = True, "生成的文书正文为空"
            elif _ARTICLE_RE.search(doc) and len(doc) < 150:
                bad, detail = True, "文书过短, 疑似未完整生成"

        elif rule == "xhs_content_fields":
            has = any((final_state or {}).get(k) for k in
                      ("xhs_title", "xhs_content", "xiaohongshu_title",
                       "xiaohongshu_content", "title"))
            if not has:
                bad, detail = True, "小红书标题/正文字段缺失"

        # ---- 历史记录任务的规则由 t_runner 直接执行并写回 passed 字段 ----
        elif rule.startswith("history_"):
            if not (final_state or {}).get("__history_passed__", False):
                bad = True
                detail = (final_state or {}).get("__history_detail__", "历史记录断言未通过")

        if bad:
            defects.append({"rule": rule, "severity": sev, "detail": detail})

    needs_edit = any(d["severity"] in ("block", "major") for d in defects)
    needs_edit_strict = any(d["severity"] == "block" for d in defects)
    return defects, needs_edit, needs_edit_strict


# ============================================================================
# 五、失败归因
# ============================================================================

def attribute_failure(case, exec_result, route_ok, state_failures, defects,
                      retrieval_res, manifest):
    """自动归因: 把一次失败归类到具体根因并给出修复建议

    归因优先级: 异常 > 超时 > 路由错 > 结构性数据缺陷 > 空召回 > 幻觉 > 依据不足
    """
    causes = []

    if exec_result.get("error_type") == "TIMEOUT":
        causes.append({
            "code": "TIMEOUT",
            "reason": f"执行超时 (>{exec_result.get('timeout_sec')}s), 链路未跑到 END",
            "suggestion": "定位最慢节点 (见节点耗时榜); 优先优化 LLM 调用并发或降低 top_k",
        })
    if exec_result.get("error"):
        causes.append({
            "code": "EXCEPTION",
            "reason": f"节点抛出异常: {_short(exec_result.get('error'), 200)}",
            "suggestion": "查看该用例的节点日志, 检查 LLM 返回解析与外部依赖连通性",
        })
    if not route_ok:
        causes.append({
            "code": "ROUTE_MISMATCH",
            "reason": f"状态流转异常: {exec_result.get('route_detail', '')}",
            "suggestion": "检查意图路由节点的 task_type 判定与条件边映射表 LEVEL2_PATH_MAP",
        })
    if state_failures:
        fs = ", ".join(f"{f['field']}({f['op']})" for f in state_failures[:3])
        causes.append({
            "code": "MISSING_FIELD",
            "reason": f"关键状态字段未满足断言: {fs}",
            "suggestion": "检查上游节点是否正确写入该字段, 或子图出口是否按 task_type 分流",
        })
    for d in defects:
        if d["rule"] == "no_hallucinated_citation":
            causes.append({
                "code": "HALLUCINATION",
                "reason": d["detail"],
                "suggestion": "在最终答案生成节点增加'引用必须在检索结果内'的硬校验; "
                              "或在 prompt 中禁止补充库外法条",
            })
        elif d["rule"] == "golden_recall_min":
            causes.append({
                "code": "LOW_RECALL",
                "reason": d["detail"],
                "suggestion": "检查关键词抽取是否被归一化掉; "
                              "检查该条款是否真的入库 (见 kb_manifest 结构健康度)",
            })
        elif d["rule"] == "quality_score_min":
            causes.append({
                "code": "LOW_QUALITY",
                "reason": d["detail"],
                "suggestion": "调整质量门阈值或融合权重; 检查意图偏置是否把正确法条压低",
            })

    # 结构性数据缺陷: 用例依赖的知识源在库中根本没有可检索单元
    golden_docs = {g.get("doc") for g in (case.get("golden") or []) if g.get("doc")}
    if golden_docs and manifest:
        case_nodes = {c["name"] for c in manifest["neo4j"].get("case_nodes", [])}
        article_docs = {d["doc"] for d in manifest["neo4j"].get("documents", [])}
        missing = golden_docs - article_docs - case_nodes
        if missing:
            causes.append({
                "code": "KNOWLEDGE_GAP",
                "reason": f"标准答案依赖的文档未入库: {sorted(missing)}",
                "suggestion": "先跑数据管线入库该文档, 再评估检索算法 —— 否则召回率被数据缺失污染",
            })
        # 案例源结构性失效
        if case.get("task") == "case_search":
            for h in manifest.get("health", []):
                if h["source"] == "cases" and not h["ok"]:
                    causes.append({
                        "code": "STRUCTURAL_DEFECT",
                        "reason": "案例检索结构性失效: " + " / ".join(h["issues"]),
                        "suggestion": "让检索通道兼容 :Case 节点 (扩 UNION ALL 分支), "
                                      "或在入库时把案例正文切成 :Article 节点",
                    })
                    break

    if (case.get("golden") and retrieval_res
            and retrieval_res.get("golden_hit_count", 0) == 0
            and not any(c["code"] in ("KNOWLEDGE_GAP", "STRUCTURAL_DEFECT") for c in causes)):
        causes.append({
            "code": "EMPTY_RETRIEVAL",
            "reason": f"检索 0 命中 (citations={exec_result.get('citation_count', 0)})",
            "suggestion": "检查知识源挂载 (TASK_SOURCE_DEFAULTS) 与关键词抽取结果",
        })

    return causes


# ============================================================================
# 六、聚合统计
# ============================================================================

def percentile(values, p):
    """线性插值分位数"""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return round(vals[0], 2)
    k = (len(vals) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return round(vals[int(k)], 2)
    return round(vals[f] * (c - k) + vals[c] * (k - f), 2)


def aggregate(results, manifest):
    """把全部用例结果聚合成报告所需的汇总指标"""
    total = len(results)
    executed = [r for r in results if r.get("executed")]
    ok_exec = [r for r in executed if r.get("exec_success")]
    ok_biz = [r for r in executed if r.get("biz_success")]

    ret = [r for r in executed if (r.get("retrieval") or {}).get("applicable")]
    lat = [r["latency_sec"] for r in executed if r.get("latency_sec") is not None]

    def _avg(items, key):
        vals = [r[key] for r in items
                if r.get(key) is not None and isinstance(r[key], (int, float))]
        return round(sum(vals) / len(vals), 4) if vals else None

    tokens_in = sum(r.get("tokens_prompt", 0) or 0 for r in executed)
    tokens_out = sum(r.get("tokens_completion", 0) or 0 for r in executed)
    cost = (tokens_in / 1e6 * PRICE_PROMPT_PER_1M
            + tokens_out / 1e6 * PRICE_COMPLETION_PER_1M)

    # ---- 节点级聚合 ----
    node_stat = {}
    for r in executed:
        for nd in r.get("node_trace", []):
            k = nd["full_name"]
            s = node_stat.setdefault(k, {
                "name": nd["name"], "subgraph": nd.get("subgraph", ""),
                "count": 0, "fail": 0, "durations": [], "cases": [],
            })
            s["count"] += 1
            s["durations"].append(nd.get("duration_sec") or 0)
            if nd.get("error"):
                s["fail"] += 1
            if r["id"] not in s["cases"]:
                s["cases"].append(r["id"])
    for k, s in node_stat.items():
        d = s.pop("durations")
        s["avg_sec"] = round(sum(d) / len(d), 2) if d else 0
        s["max_sec"] = round(max(d), 2) if d else 0
        s["p95_sec"] = percentile(d, 95)
        s["total_sec"] = round(sum(d), 2)

    # ---- 按任务聚合 ----
    by_task = {}
    for r in results:
        t = r["task"]
        b = by_task.setdefault(t, {
            "task": t, "name": r.get("task_name", t), "total": 0, "executed": 0,
            "exec_ok": 0, "biz_ok": 0, "needs_edit": 0, "needs_edit_strict": 0,
            "latencies": [], "tokens": 0, "cost": 0.0,
            "precisions": [], "recalls": [], "mrrs": [], "routes": {},
        })
        b["total"] += 1
        if not r.get("executed"):
            continue
        b["executed"] += 1
        b["exec_ok"] += 1 if r.get("exec_success") else 0
        b["biz_ok"] += 1 if r.get("biz_success") else 0
        b["needs_edit"] += 1 if r.get("needs_manual_edit") else 0
        b["needs_edit_strict"] += 1 if r.get("needs_manual_edit_strict") else 0
        if r.get("latency_sec") is not None:
            b["latencies"].append(r["latency_sec"])
        b["tokens"] += (r.get("tokens_prompt", 0) or 0) + (r.get("tokens_completion", 0) or 0)
        b["cost"] += _case_cost(r)
        rr = r.get("retrieval") or {}
        if rr.get("applicable"):
            b["precisions"].append(rr.get("precision") or 0)
            b["recalls"].append(rr.get("recall") or 0)
            b["mrrs"].append(rr.get("mrr") or 0)
        key = " → ".join(r.get("main_route", []))
        b["routes"][key] = b["routes"].get(key, 0) + 1

    for t, b in by_task.items():
        ls = b.pop("latencies")
        b["p50_sec"] = percentile(ls, 50)
        b["p95_sec"] = percentile(ls, 95)
        b["max_sec"] = round(max(ls), 2) if ls else None
        b["avg_sec"] = round(sum(ls) / len(ls), 2) if ls else None
        b["cost"] = round(b["cost"], 4)
        b["avg_cost"] = round(b["cost"] / b["executed"], 4) if b["executed"] else 0
        b["exec_success_rate"] = round(b["exec_ok"] / b["executed"], 4) if b["executed"] else 0
        b["biz_success_rate"] = round(b["biz_ok"] / b["executed"], 4) if b["executed"] else 0
        b["manual_edit_rate"] = round(b["needs_edit"] / b["executed"], 4) if b["executed"] else 0
        b["manual_edit_rate_strict"] = (round(b["needs_edit_strict"] / b["executed"], 4)
                                        if b["executed"] else 0)
        b["avg_precision"] = _avg_num(b["precisions"])
        b["avg_recall"] = _avg_num(b["recalls"])
        b["avg_mrr"] = _avg_num(b["mrrs"])
        b.pop("precisions"); b.pop("recalls"); b.pop("mrrs")

    # ---- 失败归因分布 ----
    cause_dist = {}
    for r in executed:
        for c in r.get("causes", []):
            cause_dist[c["code"]] = cause_dist.get(c["code"], 0) + 1

    return {
        "total_cases": total,
        "executed": len(executed),
        "exec_success": len(ok_exec),
        "biz_success": len(ok_biz),
        "exec_success_rate": round(len(ok_exec) / len(executed), 4) if executed else 0,
        "biz_success_rate": round(len(ok_biz) / len(executed), 4) if executed else 0,
        "retrieval_cases": len(ret),
        "avg_precision": _avg(ret, "precision"),
        "avg_recall": _avg(ret, "recall"),
        "avg_mrr": _avg(ret, "mrr"),
        "hit1_rate": round(sum(1 for r in ret if (r.get("retrieval") or {}).get("hit@1"))
                           / len(ret), 4) if ret else None,
        "hit5_rate": round(sum(1 for r in ret if (r.get("retrieval") or {}).get("hit@5"))
                           / len(ret), 4) if ret else None,
        "needs_manual_edit": sum(1 for r in executed if r.get("needs_manual_edit")),
        "needs_manual_edit_strict": sum(1 for r in executed
                                        if r.get("needs_manual_edit_strict")),
        "manual_edit_rate": (round(sum(1 for r in executed if r.get("needs_manual_edit"))
                                   / len(executed), 4) if executed else 0),
        "manual_edit_rate_strict": (round(sum(1 for r in executed
                                              if r.get("needs_manual_edit_strict"))
                                          / len(executed), 4) if executed else 0),
        "latency": {
            "p50": percentile(lat, 50), "p95": percentile(lat, 95),
            "max": round(max(lat), 2) if lat else None,
            "avg": round(sum(lat) / len(lat), 2) if lat else None,
            "min": round(min(lat), 2) if lat else None,
        },
        "tokens": {"prompt": tokens_in, "completion": tokens_out,
                   "total": tokens_in + tokens_out},
        "cost_total": round(cost, 4),
        "cost_avg_per_case": round(cost / len(executed), 4) if executed else 0,
        "by_task": by_task,
        "node_stat": node_stat,
        "cause_dist": cause_dist,
    }


def _case_cost(r):
    return round((r.get("tokens_prompt", 0) or 0) / 1e6 * PRICE_PROMPT_PER_1M
                 + (r.get("tokens_completion", 0) or 0) / 1e6 * PRICE_COMPLETION_PER_1M, 6)


def _avg_num(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None
