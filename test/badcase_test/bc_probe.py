# -*- coding: utf-8 -*-
"""环境与静态探针 —— 不跑图即可产出的可验证结论

==============================================================================
【为什么要有探针】
    badcase 的第一性问题往往是"环境/数据/规则"层面的, 而不是"模型"层面的。
    如果 Neo4j 没起来, 检索两通道会静默返回 [], 图上跑一万次也只会得到同一个
    "未检索到相关依据" —— 你不跑图就能先把这类问题钉死, 省下几十分钟和几块钱。

    探针全部是**只读**操作 (连不上就报"不可用", 绝不写库)。

【检查项】
    P1 Neo4j 连通性 + 库内节点/关系统计
    P2 FAISS 索引文件与 id2text 完整性 + 实体名形态统计
    P3 声明入库文件 vs 实际抽取结果比对 (数据一致性)
    P4 KEYWORD_RULES 对每条用例输入的知识源挂载判定 (纯函数, 确定性)
    P5 text_recognize 合同语义评分 (纯规则, 确定性)
    P6 源码常量一致性 (单源阈值 50 vs 质量门阈值 60)
    P7 citation 关键字段可用性 (law_name 是否会被写入)
==============================================================================
"""
import os
import re
import sys
import json
import pickle
import time

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_PKG_DIR = os.path.dirname(_TEST_DIR)          # test/
_ROOT = os.path.dirname(_PKG_DIR)              # 项目根
for _p in (_ROOT, _PKG_DIR, _TEST_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

PROBE_JSON = os.path.join(_TEST_DIR, "probe_report.json")

# 用户声明的"已成功处理"的 9 个数据文件
DECLARED_FILES = [
    ("industry_sources", "住建部标准.txt"),
    ("industry_sources", "城市房屋租赁管理办法.txt"),
    ("interpretations", "劳动法司法解释.txt"),
    ("interpretations", "最高人民法院、最高人民检察院关于办理妨害信用卡管理刑事案件具体应用法律若干问题的解释.txt"),
    ("laws", "个人独资企业法.txt"),
    ("laws", "中华人民共和国个人信息保护法.txt"),
    ("regulations", "不动产登记暂行条例.txt"),
    ("regulations", "个人所得税法实施条例.txt"),
    ("cases", "case_698be5cb1791f068.txt"),
]


# ============================================================================
# P1 · Neo4j
# ============================================================================
def probe_neo4j():
    out = {"available": False, "uri": "", "error": "", "labels": [], "rels": [],
           "documents": [], "article_total": 0}
    try:
        from common.config import Config
        conf = Config()
        out["uri"] = str(getattr(conf, "NEO4J_URI", "") or "")
    except Exception as e:
        out["error"] += f"[config] {type(e).__name__}: {e}; "
    try:
        from common.neo4j_manager import neo4j_client
        rows = neo4j_client.run_cypher(
            "MATCH (n) RETURN labels(n)[0] AS l, count(*) AS c ORDER BY c DESC")
        out["labels"] = [{"label": r.get("l"), "count": r.get("c")} for r in rows]
        rows2 = neo4j_client.run_cypher(
            "MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c ORDER BY c DESC")
        out["rels"] = [{"type": r.get("t"), "count": r.get("c")} for r in rows2]
        out["available"] = True
        for r in out["labels"]:
            if r["label"] in ("Law", "Document", "Source"):
                out["documents"].append(r["label"])
            if r["label"] == "Article":
                out["article_total"] = r["count"]
    except Exception as e:
        out["error"] += f"[connect] {type(e).__name__}: {str(e)[:300]}"
    return out


# ============================================================================
# P2 · FAISS 索引 + id2text 实体形态
# ============================================================================
_SOURCES = ["laws", "regulations", "interpretations", "industry_sources", "cases"]


def probe_faiss():
    base = os.path.join(_ROOT, "data", "knowledge_base", "index")
    out = {"base": base, "sources": {}, "ok": True}
    for s in _SOURCES:
        idx_p = os.path.join(base, f"{s}_faiss.index")
        id_p = os.path.join(base, f"{s}_id2text.pkl")
        item = {"index_exists": os.path.exists(idx_p),
                "id2text_exists": os.path.exists(id_p),
                "index_bytes": os.path.getsize(idx_p) if os.path.exists(idx_p) else 0,
                "entity_count": 0, "avg_len": 0.0, "max_len": 0,
                "long_entity_ratio": 0.0, "samples": [], "error": ""}
        if item["index_exists"] and item["id2text_exists"]:
            try:
                with open(id_p, "rb") as f:
                    d = pickle.load(f)
                vals = [str(d[i]) for i in range(len(d))]
                item["entity_count"] = len(vals)
                lens = [len(v) for v in vals]
                item["avg_len"] = round(sum(lens) / max(1, len(lens)), 1)
                item["max_len"] = max(lens) if lens else 0
                # "长实体" = 超过 12 字的法条片段 (这类实体几乎不可能被关键词 CONTAINS 命中)
                long_n = sum(1 for v in vals if len(v) >= 12)
                item["long_entity_ratio"] = round(long_n / max(1, len(vals)), 3)
                item["samples"] = vals[:8]
            except Exception as e:
                item["error"] = f"{type(e).__name__}: {e}"
                out["ok"] = False
        else:
            out["ok"] = False
        out["sources"][s] = item
    return out


# ============================================================================
# P3 · 声明入库文件 vs 实际抽取结果
# ============================================================================
_EXTRACT_MAP = {
    "laws": "extract_law_data.json",
    "regulations": "extract_regulation_data.json",
    "interpretations": "extract_interpretation_data.json",
    "industry_sources": "extract_industry_data.json",
    "cases": "extract_case_data.json",
}


def probe_data_consistency():
    out = {"declared": [], "actual": [], "missing": [], "extra": [], "mtime": {}}
    save_dir = os.path.join(_ROOT, "__002__extract_information")
    actual = {}
    for src, fn in _EXTRACT_MAP.items():
        p = os.path.join(save_dir, fn)
        if not os.path.exists(p):
            continue
        out["mtime"][fn] = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(p)))
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            for r in (d.get("results") or []):
                actual.setdefault(src, []).append(str(r.get("filename", "")))
        except Exception as e:
            out.setdefault("errors", []).append(f"{fn}: {type(e).__name__}: {e}")

    for src, fn in DECLARED_FILES:
        out["declared"].append(f"{src}/{fn}")
    for src, lst in actual.items():
        for fn in lst:
            out["actual"].append(f"{src}/{fn}")

    ds, as_ = set(out["declared"]), set(out["actual"])
    out["missing"] = sorted(ds - as_)      # 声明了但库里没有
    out["extra"] = sorted(as_ - ds)        # 库里有但没声明
    return out


# ============================================================================
# P4 · KEYWORD_RULES 挂载判定 (确定性, 直接调用被测函数)
# ============================================================================
def probe_source_mounts(cases):
    """对每条用例调 _build_source_mounts, 看 industry_sources 是否被挂载"""
    out = {"imported": False, "per_case": [], "error": ""}
    fn = None
    try:
        from __004__langgraph_more_nodes.nodes.retrieval_nodes import (
            retrieval_intent_decompose_node as _m,
        )
        fn = _m._build_source_mounts
        out["imported"] = True
        out["keyword_rules_groups"] = len(getattr(_m, "KEYWORD_RULES", []))
        out["keyword_rules"] = [
            {"keywords": kws[:8], "sources": srcs}
            for kws, srcs in getattr(_m, "KEYWORD_RULES", [])
        ]
        out["task_defaults"] = getattr(_m, "TASK_SOURCE_DEFAULTS", {})
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        return out

    for c in cases:
        task = c["task"]
        text = c["input"][:4000]
        try:
            m = fn(task, text, "")
            mounted = m.get("mounted_sources", [])
        except Exception as e:
            mounted = []
            out.setdefault("errors", []).append(f"{c['id']}: {e}")
        doc_text = c["input"] if c["task"] in ("contract_review", "compliance_review") else ""
        try:
            m2 = fn(task, text, doc_text)
            mounted2 = m2.get("mounted_sources", [])
        except Exception:
            mounted2 = mounted
        out["per_case"].append({
            "id": c["id"], "task": task,
            "mounted_user_only": mounted,
            "mounted_with_doc": mounted2,
            "industry_mounted": "industry_sources" in mounted2,
        })
    return out


# ============================================================================
# P4b · 关键词可达性测试 —— 直接量化 ENTITY_MATCH 的命中上限
#
# 召回的硬约束: `e.name CONTAINS kw` (实体名包含关键词)。
# 因此"关键词是否是实体名的子串"决定了 ENTITY_MATCH(100/90/60) 能否生效。
# 本测试对每条用例的代表性关键词, 统计各源有多少实体名能命中 ——
# 命中 0 = 该关键词在图谱通道上完全不可达, 只能靠 FULLTEXT(a.content CONTAINS kw)。
# ============================================================================
KEYWORD_PROBES = [
    # (关键词, 说明)
    ("个人信息", "BC-01/BC-07 核心概念"),
    ("同意", "BC-01/BC-07 核心概念"),
    ("单独同意", "BC-07 个保法第29条关键词"),
    ("人脸识别", "BC-07 业务词(法条中无此字面)"),
    ("设立", "BC-02 个独法第8条"),
    ("条件", "BC-02 意图词"),
    ("恶意透支", "BC-03 信用卡解释第8条核心概念"),
    ("数额较大", "BC-03 数值标准词"),
    ("信用卡", "BC-03 主题词"),
    ("违法解除", "BC-04 案由词"),
    ("赔偿金", "BC-04 诉求词"),
    ("房屋租赁", "BC-05/BC-06 核心场景"),
    ("租赁期限", "BC-06/BC-10 城市房屋租赁办法第4条"),
    ("违约金", "BC-06 住建部标准第5条"),
    ("建设工程", "BC-10 行业源触发词"),
    ("质量保修", "BC-10 住建部标准第3条"),
]


_ENT_CACHE = {}


def _load_entities():
    """懒加载全部源的实体名(进程内缓存, 只读)"""
    if _ENT_CACHE:
        return _ENT_CACHE
    base = os.path.join(_ROOT, "data", "knowledge_base", "index")
    for s in _SOURCES:
        p = os.path.join(base, f"{s}_id2text.pkl")
        if not os.path.exists(p):
            continue
        try:
            with open(p, "rb") as f:
                d = pickle.load(f)
            _ENT_CACHE[s] = [str(d[i]) for i in range(len(d))]
        except Exception:
            pass
    return _ENT_CACHE


def probe_mount_aware_reachability(cases, mount_rows):
    """【核心诊断】关键词可达性 × 实际挂载源 = 图谱实体通道的真实有效命中

    召回硬约束: `e.name CONTAINS kw`。
    即使某关键词在 industry_sources 有命中, 只要 industry_sources 没被挂载,
    该命中在本次检索中就是 0 —— 这正是"看起来库里有、实际永远查不到"的成因。
    """
    ents = _load_entities()
    by_id = {r["id"]: r for r in (mount_rows or [])}
    out = []
    for c in cases:
        row = by_id.get(c["id"]) or {}
        mounted = row.get("mounted_with_doc") or []
        kws = c.get("keywords") or []
        items = []
        hit_mounted_total = 0
        for kw in kws:
            per = {}
            hit_all = hit_mounted = 0
            for s, lst in ents.items():
                n = sum(1 for e in lst if kw in e)
                if n:
                    per[s] = n
                hit_all += n
                if s in mounted:
                    hit_mounted += n
            hit_mounted_total += hit_mounted
            items.append({"kw": kw, "hit_all": hit_all, "hit_mounted": hit_mounted,
                          "per_source": per,
                          "orphan_hits": hit_all - hit_mounted})
        out.append({
            "id": c["id"], "task": c["task"],
            "mounted_sources": mounted,
            "keywords": items,
            "effective_hits": hit_mounted_total,
            "orphan_hits": sum(i["orphan_hits"] for i in items),
        })
    return out


def probe_keyword_reachability():
    ents = _load_entities()
    rows = []
    for kw, note in KEYWORD_PROBES:
        per_src = {}
        total = 0
        for s, lst in ents.items():
            hit = [e for e in lst if kw in e]
            per_src[s] = {"hit": len(hit), "total": len(lst),
                          "samples": hit[:3]}
            total += len(hit)
        rows.append({
            "keyword": kw, "note": note, "total_hit": total,
            "entity_match_reachable": total > 0,
            "per_source": per_src,
        })

    unreachable = [r["keyword"] for r in rows if r["total_hit"] == 0]
    return {
        "rows": rows,
        "unreachable_keywords": unreachable,
        "unreachable_ratio": round(len(unreachable) / max(1, len(rows)), 3),
        "conclusion": (
            f"{len(unreachable)}/{len(rows)} 个代表性关键词在全部 5 个源的实体名上"
            f"命中数为 0 → ENTITY_MATCH(e.name CONTAINS kw) 对这些关键词完全失效, "
            f"只能靠 FULLTEXT(a.content CONTAINS kw, 固定 40 分)兜底。"
            if unreachable else "全部关键词均可达"
        ),
    }


# ============================================================================
# P5 · text_recognize 合同语义评分
# ============================================================================
def _local_score(text):
    """text_recognize_node._score_contract_likeness 的本地镜像(导入失败时用)"""
    score = 0
    n = len(text)
    if re.findall(r"第\s*[一二三四五六七八九十百零〇\d]+\s*[条款章节]", text):
        score += 2
    has_a, has_b = "甲方" in text, "乙方" in text
    score += 2 if (has_a and has_b) else (1 if (has_a or has_b) else 0)
    if any(w in text for w in ("合同", "协议", "契约", "承诺书", "授权书", "备忘录")):
        score += 1
    elems = ("违约责任", "违约金", "争议解决", "权利义务", "生效", "签字", "盖章", "标的",
             "租金", "价款", "付款方式", "质量保证", "保密", "租赁期限", "交付", "验收",
             "仲裁", "管辖", "解除", "赔偿", "本合同", "押金")
    hit = [w for w in elems if w in text]
    if hit:
        score += min(len(hit), 3)
    if n >= 300:
        score += 1
    head = text[:15]
    if n < 100 and any(p in head for p in ("请帮我", "帮我", "请问", "我想", "能否",
                                           "可以帮", "麻烦", "请你", "帮忙", "请给我",
                                           "我需要", "看看")):
        score -= 3
    if n < 100 and text.rstrip().endswith(("？", "?")):
        score -= 2
    if any(w in text for w in ("天气", "你好", "吃饭", "谢谢", "在吗", "自我介绍")):
        score -= 2
    if n < 50:
        score -= 2
    return score


def probe_text_score(cases):
    out = {"imported": False, "per_case": [], "error": ""}
    fn = None
    try:
        from __004__langgraph_more_nodes.nodes.preprocess_nodes import (
            text_recognize_node as _m,
        )
        fn = _m._score_contract_likeness
        out["imported"] = True
        out["pass_threshold"] = getattr(_m, "_PASS_THRESHOLD", 3)
        out["block_threshold"] = getattr(_m, "_BLOCK_THRESHOLD", 0)
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e} (使用本地镜像规则)"
        fn = _local_score
        out["pass_threshold"], out["block_threshold"] = 3, 0

    for c in cases:
        if c["task"] not in ("contract_review", "compliance_review"):
            continue
        s = fn(c["input"])
        if c["task"] == "compliance_review":
            # 【与源码一致】text_recognize_node 对 compliance_review 是**首分支短路**:
            # 不评分、不调 LLM, 直接 pass + is_contract_input=False。
            # 这里仍把评分算出来, 用于说明"若被误判为 contract_review 会怎样"。
            out["per_case"].append({
                "id": c["id"], "task": c["task"], "score": s,
                "verdict": "pass(合规分支短路, 不评分)",
                "if_contract_review": (
                    "block(直接拦截)" if s <= out["block_threshold"]
                    else "灰区(调 LLM)" if s < out["pass_threshold"]
                    else "pass(直接放行)"),
                "note": ("合规审查输入的形态是'问题描述'而非'合同正文', "
                         "若意图路由误判为 contract_review, 该输入会被守卫拦截 → "
                         "意图路由与守卫的耦合风险点"),
            })
            continue
        verdict = ("pass(直接放行)" if s >= out["pass_threshold"]
                   else "block(直接拦截)" if s <= out["block_threshold"]
                   else "灰区(调 LLM)")
        out["per_case"].append({"id": c["id"], "task": c["task"],
                                "score": s, "verdict": verdict})
    return out


# ============================================================================
# P6 · 源码常量一致性
# ============================================================================
def probe_constants():
    """检测跨模块阈值/常量不一致"""
    out = {"constants": {}, "conflicts": []}
    single_src_th = multi_th = max_retry = None
    try:
        from __004__langgraph_more_nodes.nodes.retrieval_nodes import (
            retrieval_fusion_ranking_node as _f,
        )
        single_src_th = getattr(_f, "SINGLE_SOURCE_THRESHOLD", None)
        multi_th = getattr(_f, "QUALITY_GATE_THRESHOLD", None)
    except Exception:
        pass
    try:
        from __004__langgraph_more_nodes.nodes.retrieval_nodes import (
            quality_gate_retry_node as _q,
        )
        q_th = getattr(_q, "QUALITY_GATE_THRESHOLD", None)
        max_retry = getattr(_q, "MAX_QUALITY_RETRIES", None)
        exp_map = getattr(_q, "_KEYWORD_EXPANSION_MAP", {}) or {}
        out["constants"]["expansion_map_tasks"] = sorted(exp_map.keys())
    except Exception:
        q_th = None

    out["constants"]["fusion_single_source_threshold"] = single_src_th
    out["constants"]["fusion_multi_threshold"] = multi_th
    out["constants"]["quality_gate_threshold"] = q_th
    out["constants"]["max_quality_retries"] = max_retry

    if single_src_th is not None and q_th is not None and single_src_th != q_th:
        out["conflicts"].append({
            "id": "C1",
            "title": "单源阈值与质量门阈值不一致",
            "detail": (
                f"retrieval_fusion_ranking_node.SINGLE_SOURCE_THRESHOLD={single_src_th} "
                f"用于判定 quality_gate_passed; "
                f"quality_gate_retry_node.QUALITY_GATE_THRESHOLD={q_th} 用于决定是否回边重试。"
                f"单源(case_search)质量分落在 [{single_src_th}, {q_th}) 时, "
                f"fusion 判通过、quality_gate 判不通过 → 必然回边重试。"
            ),
            "impact": "case_search 每次命中该分数区间都会空转重试 3 次",
            "severity": "P0",
        })

    # 关键词扩展词表覆盖度
    try:
        tasks_needing_expansion = ["case_search", "legal_research", "legal_document_gen"]
        covered = set(out["constants"].get("expansion_map_tasks") or [])
        gap = [t for t in tasks_needing_expansion if t not in covered]
        if gap:
            out["conflicts"].append({
                "id": "C2",
                "title": "关键词扩展词表未覆盖全部 task_type",
                "detail": (
                    f"_KEYWORD_EXPANSION_MAP 仅覆盖 {sorted(covered)}, "
                    f"缺 {gap}。这些任务重试时 _expand_keywords 返回与原关键词等长的列表, "
                    f"quality_gate_retry_node 判定 len(expanded) > len(current) 为 False → "
                    f"expanded_kw=None → 连关键词都不更新。"
                ),
                "impact": "重试完全等价, 3 轮 LLM+FAISS+Neo4j 开销换 0 增益",
                "severity": "P0",
            })
    except Exception:
        pass
    return out


# ============================================================================
# P7 · citation 字段可用性
# ============================================================================
def probe_citation_fields():
    """检查图谱召回返回的 citation 是否带 law_name (决定引用能否溯源)"""
    p = os.path.join(
        _ROOT, "__004__langgraph_more_nodes", "nodes", "retrieval_nodes",
        "retrieval_entity_recall_node.py")
    out = {"file": p, "has_law_name_in_return": False, "return_fields": [],
           "cypher_selects_law_name": False, "note": ""}
    if not os.path.exists(p):
        out["note"] = "源码文件不存在"
        return out
    src = open(p, encoding="utf-8").read()
    out["cypher_selects_law_name"] = "law.name AS law_name" in src
    m = re.search(r"recall_results\.append\(\{(.*?)\}\)", src, re.S)
    if m:
        fields = re.findall(r'"(\w+)":', m.group(1))
        out["return_fields"] = sorted(set(fields))
        out["has_law_name_in_return"] = "law_name" in fields
    out["note"] = (
        "Cypher 已 SELECT law.name AS law_name, 但组装 recall_results 时未写入 → "
        "citation 丢失法律名, 用户看到的引用形如【违约金 第七条】无法溯源到具体法律; "
        "同时 retrieval_output_pack_node 按 title 猜分类(含'法规/法律/法典'), "
        "而 title 实际是实体名/概念名 → 分类几乎 100% 落'其他'。"
        if out["cypher_selects_law_name"] and not out["has_law_name_in_return"]
        else ""
    )
    return out


# ============================================================================
# 汇总
# ============================================================================
def run_probe(cases=None, verbose=True):
    if cases is None:
        from bc_cases import all_cases
        cases = all_cases()

    mounts = probe_source_mounts(cases)
    rep = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "project_root": _ROOT,
        "neo4j": probe_neo4j(),
        "faiss": probe_faiss(),
        "data_consistency": probe_data_consistency(),
        "source_mounts": mounts,
        "keyword_reachability": probe_keyword_reachability(),
        "mount_aware_reachability": probe_mount_aware_reachability(
            cases, mounts.get("per_case") or []),
        "text_score": probe_text_score(cases),
        "constants": probe_constants(),
        "citation_fields": probe_citation_fields(),
    }

    # ---- 由探针结论直接推出的 P0/P1 判定 ----
    findings = []
    if not rep["neo4j"]["available"]:
        findings.append({
            "id": "F1", "severity": "P0", "node": "retrieval_entity_recall (通道1a/1b)",
            "title": "Neo4j 不可用 → 两通道召回静默归零",
            "evidence": rep["neo4j"]["error"][:300],
            "detail": (
                "_graph_entity_recall 与 _faiss_semantic_recall(反向 Cypher) 都把数据库异常"
                "catch 后 print WARNING 并 return [], state 中不会留下任何'通道故障'标记。"
                "下游 precision/fusion 拿到空池 → quality_score=20 → quality_gate 判不通过 → "
                "回边重试 3 次 → 3 轮重试全部等价空转 → 最终 legal_qa 走'无依据'兜底。"
                "用户侧看到的'未检索到相关依据'与'库里真的没有'完全无法区分。"
            ),
        })
    fa = rep["faiss"]["sources"]
    for s in ("laws", "industry_sources"):
        item = fa.get(s) or {}
        if item.get("long_entity_ratio", 0) >= 0.5:
            findings.append({
                "id": f"F2-{s}", "severity": "P1",
                "node": "retrieval_entity_recall._graph_entity_recall",
                "title": f"{s} 实体名以长片段为主, ENTITY_MATCH 命中率结构性偏低",
                "evidence": (f"{s}: {item.get('entity_count')} 实体, 平均长度 "
                             f"{item.get('avg_len')} 字, ≥12 字占比 "
                             f"{item.get('long_entity_ratio')}"),
                "detail": (
                    "召回用 `e.name CONTAINS kw`, 要求关键词是实体名的子串。"
                    "而实体名本身是法条整句片段(如'经发卡银行两次有效催收后超过三个月仍不归还'), "
                    "LLM 抽出的关键词('恶意透支')是片段的**上位概念**而非子串 → "
                    "ENTITY_MATCH(100/90/60) 通道基本失效, 只剩 FULLTEXT(40 分)兜底。"
                ),
            })
    miss = rep["data_consistency"].get("missing") or []
    if miss:
        findings.append({
            "id": "F3", "severity": "P0", "node": "数据层 (图谱构建)",
            "title": "声明入库文件与实际抽取结果不一致",
            "evidence": f"声明但未入库: {miss}; 已入库但未声明: "
                        f"{rep['data_consistency'].get('extra') or []}",
            "detail": (
                "用该文件的条款构造 golden set 会得到 100% 假阴性 —— "
                "测试集的失败不来自代码, 而来自数据未入库。必须以探针结论为准修正 golden。"
            ),
        })
    for cf in rep["constants"].get("conflicts") or []:
        findings.append({
            "id": f"F4-{cf['id']}", "severity": cf["severity"],
            "node": "quality_gate_retry / retrieval_fusion_ranking",
            "title": cf["title"], "evidence": cf["detail"], "detail": cf["impact"],
        })
    cfi = rep["citation_fields"]
    if cfi.get("cypher_selects_law_name") and not cfi.get("has_law_name_in_return"):
        findings.append({
            "id": "F5", "severity": "P0",
            "node": "retrieval_entity_recall / retrieval_output_pack",
            "title": "citation 缺失 law_name + title 语义错位 → 引用不可溯源、分类失效",
            "evidence": (f"Cypher 已 SELECT law_name={cfi['cypher_selects_law_name']}, "
                         f"返回字段={cfi['return_fields']}"),
            "detail": cfi.get("note", ""),
        })

    rep["findings"] = findings
    rep["summary"] = {
        "neo4j_ok": rep["neo4j"]["available"],
        "faiss_ok": rep["faiss"]["ok"],
        "data_consistent": not miss,
        "findings_by_severity": {
            sev: sum(1 for f in findings if f["severity"] == sev)
            for sev in ("P0", "P1", "P2")
        },
    }

    with open(PROBE_JSON, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2, default=str)

    if verbose:
        print("=" * 74)
        print("【环境与静态探针】")
        print("=" * 74)
        n4 = rep["neo4j"]
        print(f"  Neo4j        : {'✅ 可用' if n4['available'] else '❌ 不可用'}  "
              f"{n4['uri']}")
        if n4["available"]:
            print(f"                 节点 {n4['labels']} 关系 {n4['rels']}")
        else:
            print(f"                 {n4['error'][:160]}")
        print(f"  FAISS        : {'✅' if rep['faiss']['ok'] else '⚠️ '}  "
              + "  ".join(f"{s}={fa[s]['entity_count']}" for s in _SOURCES))
        dc = rep["data_consistency"]
        print(f"  数据一致性   : 声明 {len(dc['declared'])} 份 / 实际 {len(dc['actual'])} 份")
        if dc["missing"]:
            print(f"                 ❌ 声明但未入库: {dc['missing']}")
        if dc["extra"]:
            print(f"                 ⚠️  入库但未声明: {dc['extra']}")
        print(f"  源码常量冲突 : {len(rep['constants'].get('conflicts') or [])} 项")
        for cf in rep["constants"].get("conflicts") or []:
            print(f"                 [{cf['severity']}] {cf['title']}")
        print(f"\n  探针结论 {len(findings)} 条:")
        for f in findings:
            print(f"    [{f['severity']}] {f['id']} {f['title']}")
        print(f"\n  报告: {PROBE_JSON}")
        print("=" * 74)
    return rep


if __name__ == "__main__":
    run_probe()
