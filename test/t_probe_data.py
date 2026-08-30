# -*- coding: utf-8 -*-
"""数据基建探针 (Knowledge Base Manifest)

【这个文件是干什么的】
    在跑任何测试之前, 先回答一个致命问题: **库里到底有什么?**

    法律 RAG 的测试用例如果脱离真实语料, 测出来的"低召回率"根本无法归因 ——
    你分不清是「检索算法不行」还是「这条数据压根没入库」。本探针把 Neo4j 图库
    与 FAISS 向量索引的真实内容dump 成一份清单 (kb_manifest.json), 用于:

      1. 测试集 grounding 校验 —— 用例引用的文档/条款必须真实存在于清单中,
         否则该用例在报告中被标为 UNGROUNDED (不计入检索指标分母);
      2. 空库/缺源检测 —— 直接暴露"某个知识源根本没有 Article 节点"这类
         结构性缺陷 (这正是 case_search 链路当前的问题);
      3. 幻觉检测白名单 —— 模型引用了清单外的法条名, 即判定为幻觉。

【输出】
    outputs/kb_manifest.json
"""
import os
import sys
import json
import pickle
import time

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.dirname(_TEST_DIR), _TEST_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from t_config import apply_env, KB_MANIFEST_JSON, PROJECT_ROOT  # noqa: E402

apply_env()

INDEX_DIR = os.path.join(PROJECT_ROOT, "data", "knowledge_base", "index")

# 5 类知识源 → FAISS 索引文件 (与检索节点 _SOURCE_INDEX_MAP 保持一致)
SOURCE_INDEX_MAP = {
    "laws": ("laws_faiss.index", "laws_id2text.pkl"),
    "regulations": ("regulations_faiss.index", "regulations_id2text.pkl"),
    "cases": ("cases_faiss.index", "cases_id2text.pkl"),
    "industry_sources": ("industry_sources_faiss.index", "industry_sources_id2text.pkl"),
    "interpretations": ("interpretations_faiss.index", "interpretations_id2text.pkl"),
}


def probe_neo4j():
    """探测 Neo4j: 每个文档有多少 Article 节点 (Article 是检索链唯一的检索单元)"""
    from common.neo4j_manager import neo4j_client

    out = {"available": False, "documents": [], "article_total": 0,
           "case_nodes": [], "node_total": 0, "error": ""}
    try:
        out["node_total"] = neo4j_client.run_cypher(
            "MATCH (n) RETURN count(n) AS n", {})[0]["n"]
        rows = neo4j_client.run_cypher(
            "MATCH (a:Article)-[:BELONGS_TO]->(law) "
            "RETURN law.name AS doc, a.source_id AS source, count(*) AS n "
            "ORDER BY source, n DESC", {})
        for r in rows:
            out["documents"].append({
                "doc": r["doc"], "source": r["source"], "article_count": r["n"],
            })
        out["article_total"] = sum(d["article_count"] for d in out["documents"])

        # 案例节点单独统计: Case 是独立 label, 不是 Article
        try:
            cases = neo4j_client.run_cypher(
                "MATCH (c:Case) RETURN c.name AS name, c.source_id AS source, "
                "c.cause AS cause, c.court AS court, c.judge_date AS judge_date, "
                "substring(coalesce(c.content,''),0,300) AS content", {})
            out["case_nodes"] = [dict(c) for c in cases]
        except Exception as e:
            out["case_nodes"] = []
            out["error"] += f"case query: {e}; "

        # 抽取实体总量 (反映图谱稠密度)
        try:
            out["concept_total"] = neo4j_client.run_cypher(
                "MATCH (n:LegalConcept) RETURN count(n) AS n", {})[0]["n"]
        except Exception:
            out["concept_total"] = 0

        out["available"] = True
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def probe_faiss():
    """探测 FAISS: 每个知识源索引了多少条实体"""
    out = {"sources": {}, "error": ""}
    for tag, (idx_name, id2text_name) in SOURCE_INDEX_MAP.items():
        idx_path = os.path.join(INDEX_DIR, idx_name)
        pkl_path = os.path.join(INDEX_DIR, id2text_name)
        rec = {"tag": tag, "index_exists": False, "id2text_exists": False,
               "vectors": 0, "entities": 0, "sample": []}
        if os.path.exists(pkl_path):
            rec["id2text_exists"] = True
            try:
                with open(pkl_path, "rb") as f:
                    d = pickle.load(f)
                if isinstance(d, dict):
                    vals = list(d.values())
                else:
                    vals = list(d)
                rec["entities"] = len(vals)
                rec["sample"] = [str(v)[:60] for v in vals[:8]]
            except Exception as e:
                rec["error"] = str(e)
        if os.path.exists(idx_path):
            rec["index_exists"] = True
            try:
                import faiss
                rec["vectors"] = int(faiss.read_index(idx_path).ntotal)
            except Exception as e:
                rec["error"] = str(e)
        out["sources"][tag] = rec
    return out


def probe_doc_articles(docs):
    """逐个文档列出条款号与正文前 80 字, 供测试集 golden 答案精确引用"""
    from common.neo4j_manager import neo4j_client
    detail = {}
    for d in docs:
        try:
            rows = neo4j_client.run_cypher(
                "MATCH (a:Article)-[:BELONGS_TO]->(law) WHERE law.name = $doc "
                "RETURN a.name AS article_no, substring(a.content,0,80) AS content "
                "ORDER BY a.name", {"doc": d["doc"]})
            detail[d["doc"]] = [{"article_no": r["article_no"],
                                 "content": r["content"]} for r in rows]
        except Exception as e:
            detail[d["doc"]] = [{"error": str(e)}]
    return detail


def build_manifest(verbose=True):
    t0 = time.time()
    if verbose:
        print("=" * 70)
        print("【数据基建探针】探测 Neo4j + FAISS 真实可用知识")
        print("=" * 70)

    manifest = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "neo4j": probe_neo4j(),
        "faiss": probe_faiss(),
    }
    manifest["doc_articles"] = probe_doc_articles(manifest["neo4j"]["documents"])

    # ---- 结构性健康度分析 ----
    docs_by_source = {}
    for d in manifest["neo4j"]["documents"]:
        docs_by_source.setdefault(d["source"], []).append(d["doc"])
    manifest["docs_by_source"] = docs_by_source

    health = []
    for tag in SOURCE_INDEX_MAP:
        has_article = tag in docs_by_source
        fa = manifest["faiss"]["sources"].get(tag, {})
        issues = []
        if not has_article:
            issues.append("Neo4j 中该源无任何 Article 节点 → 检索 Cypher 必然 0 命中")
        if not fa.get("index_exists"):
            issues.append("FAISS 索引文件缺失")
        if fa.get("vectors", 0) == 0:
            issues.append("FAISS 索引向量数为 0")
        if tag == "cases":
            n_case = len(manifest["neo4j"].get("case_nodes", []))
            if n_case and not has_article:
                issues.append(
                    f"存在 {n_case} 个 :Case 节点, 但检索链只查 :Article 节点 → "
                    f"案例检索结构性失效 (Case 内容无法通过检索通道召回)")
            if not n_case and not has_article:
                issues.append("案例库完全为空")
        health.append({
            "source": tag,
            "doc_count": len(docs_by_source.get(tag, [])),
            "article_count": sum(d["article_count"] for d in manifest["neo4j"]["documents"]
                                 if d["source"] == tag),
            "faiss_vectors": fa.get("vectors", 0),
            "ok": not issues,
            "issues": issues,
        })
    manifest["health"] = health
    manifest["doc_name_whitelist"] = sorted(
        [d["doc"] for d in manifest["neo4j"]["documents"]]
        + [c["name"] for c in manifest["neo4j"].get("case_nodes", [])]
    )
    manifest["probe_cost_sec"] = round(time.time() - t0, 2)

    with open(KB_MANIFEST_JSON, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    if verbose:
        print(f"\nNeo4j 节点总数: {manifest['neo4j']['node_total']}  "
              f"Article: {manifest['neo4j']['article_total']}  "
              f"概念实体: {manifest['neo4j'].get('concept_total', 0)}")
        print("\n[文档清单]")
        for d in manifest["neo4j"]["documents"]:
            print(f"  {d['source']:<18} {d['doc']:<50} {d['article_count']:>4} 条")
        if manifest["neo4j"]["case_nodes"]:
            print("\n[案例节点] (注意: Case 不是 Article, 不参与通用检索通道)")
            for c in manifest["neo4j"]["case_nodes"]:
                print(f"  - {c['name']} ({c.get('cause', '')})")
        print("\n[FAISS 索引]")
        for tag, r in manifest["faiss"]["sources"].items():
            print(f"  {tag:<18} 向量={r['vectors']:<6} 实体={r['entities']:<6} "
                  f"{'OK' if r.get('vectors') else 'EMPTY'}")
        print("\n[结构健康度]")
        for h in manifest["health"]:
            flag = "✅" if h["ok"] else "❌"
            print(f"  {flag} {h['source']:<18} 文档={h['doc_count']} "
                  f"条款={h['article_count']} 向量={h['faiss_vectors']}")
            for i in h["issues"]:
                print(f"       ⚠️ {i}")
        print(f"\n清单已写入: {KB_MANIFEST_JSON}  (耗时 {manifest['probe_cost_sec']}s)")
    return manifest


def load_manifest():
    """读取已有清单; 不存在则现算"""
    if os.path.exists(KB_MANIFEST_JSON):
        with open(KB_MANIFEST_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    return build_manifest(verbose=False)


if __name__ == "__main__":
    build_manifest(verbose=True)
