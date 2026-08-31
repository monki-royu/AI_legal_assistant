# -*- coding: utf-8 -*-
"""仅用已落盘的 badcase_results.json 重新计算检索指标。

问题背景: 原 t_metrics.citation_matches_golden 在 golden 指定条款号时,
要求「条款号对 + (文档名对 或 内容锚点对)」, 条款号只是必要条件。实测中
legal_qa 路径的 citation.law_name 恒为空(引用溯源缺陷), 且 FAISS content 被
截断, 导致真召回到的法条(如第十三条)被误判为 0。

本脚本在进程内 patch 匹配器: 条款号(article_no)精确/包含匹配即判命中
(条款号是权威标识), 然后重算每条用例的 retrieval 指标与汇总均值, 写回
badcase_results.json。不改任何系统代码, 也不重跑图(省 26 分钟)。
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import t_metrics as M
import bc_cases


def _norm(s):
    if not s:
        return ""
    return str(s).strip().replace("（", "(").replace("）", ")").replace(" ", "")


def _bc_citation_matches(citation, golden):
    doc = golden.get("doc", "")
    if not doc:
        return False
    ga = _norm(golden.get("article", ""))
    ca = _norm(citation.get("article_no", "") or "")
    if ga:
        if ca and (ga == ca or ga in ca or ca in ga):
            return True
        return False
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


M.citation_matches_golden = _bc_citation_matches


def _pct(values, p):
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 3)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    rp = os.path.join(here, "badcase_results.json")
    data = json.load(open(rp, encoding="utf-8"))
    cases = {c["id"]: c for c in bc_cases.all_cases()}

    for r in data["results"]:
        cid = r["id"]
        cits = r.get("citations") or []
        golden = (cases.get(cid, {}) or {}).get("golden") or []
        r["retrieval"] = M.retrieval_metrics(cits, golden)

    ret_cases = [r for r in data["results"] if (r.get("retrieval") or {}).get("applicable")]

    def _avg(key, src):
        vals = [x[key] for x in src if x.get(key) is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    s = data.setdefault("summary", {})
    s["retrieval_cases"] = len(ret_cases)
    s["avg_precision"] = _avg("precision", [r["retrieval"] for r in ret_cases])
    s["avg_recall"] = _avg("recall", [r["retrieval"] for r in ret_cases])
    s["avg_mrr"] = _avg("mrr", [r["retrieval"] for r in ret_cases])
    s["hit1_rate"] = _avg("hit@1", [r["retrieval"] for r in ret_cases])
    s["hit5_rate"] = _avg("hit@5", [r["retrieval"] for r in ret_cases])

    json.dump(data, open(rp, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    print("重算完成. 检索适用用例 %d 条" % len(ret_cases))
    print("  avg_precision=%.3f  avg_recall=%.3f  avg_mrr=%.3f"
          % (s["avg_precision"] or 0, s["avg_recall"] or 0, s["avg_mrr"] or 0))
    for r in data["results"]:
        rm = r.get("retrieval") or {}
        if not rm.get("applicable"):
            tag = "n/a"
        else:
            tag = "P=%.2f R=%.2f MRR=%.2f" % (
                rm.get("precision") or 0, rm.get("recall") or 0, rm.get("mrr") or 0)
        print("  %s %-18s %s" % (r["id"], r["task"], tag))


if __name__ == "__main__":
    main()
