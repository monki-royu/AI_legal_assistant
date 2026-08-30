# -*- coding: utf-8 -*-
"""测试执行引擎

职责:
    1. 装载用例 → 执行 (图任务走 t_tracer, 历史记录走 HistoryStore 直测)
    2. 采集指标 (检索 P/R/MRR、成功率、人工修改率、延迟、成本)
    3. 失败归因
    4. 落盘 raw_results.json, 支持断点续跑

CLI:
    python -m test.t_runner                         # 全量
    python -m test.t_runner --task legal_qa          # 单任务
    python -m test.t_runner --case QA-01 --case LR-02
    python -m test.t_runner --category boundary      # 按类别
    python -m test.t_runner --limit 3                # 只跑前 3 条(冒烟)
    python -m test.t_runner --resume                 # 跳过已跑过的用例
"""
import os
import sys
import json
import time
import argparse

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.dirname(_TEST_DIR), _TEST_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from t_config import apply_env, RAW_RESULT_JSON, TASK_META, SAFE_MODE, CASE_TIMEOUT_SEC  # noqa: E402
from t_datasets import get_cases, all_tasks  # noqa: E402
from t_probe_data import load_manifest  # noqa: E402
from t_tracer import trace_run, run_history_op, apply_safe_mode, _COLLECTOR, get_graph  # noqa: E402
import t_metrics as M  # noqa: E402

apply_env()


# ============================================================================
# 分支识别: 从节点轨迹 + 最终状态推断实际走了哪条分支
# ============================================================================
def detect_branch(node_trace, final_state, task):
    names = {nd["name"] for nd in node_trace}
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
    if "xhs_auto_publish" in names:
        return "xhs:publish"
    if "xhs_check_text_image" in names:
        return "xhs:blocked_by_check"
    if final_state.get("fabao_skipped"):
        return "retrieval:free_only"
    return ""


def run_one_case(case, manifest, timeout_sec=None):
    """执行单条用例并计算全部指标"""
    task = case["task"]
    meta = TASK_META.get(task, {})
    rec = {
        "id": case["id"], "task": task, "task_name": meta.get("name", task),
        "category": case.get("category", "normal"), "desc": case.get("desc", ""),
        "input_preview": (case.get("input") or "")[:200],
        "executed": False, "exec_success": False, "biz_success": False,
        "latency_sec": None, "tokens_prompt": 0, "tokens_completion": 0,
        "llm_calls": 0, "cost": 0.0,
        "node_trace": [], "main_route": [], "branch": "",
        "expected_branch": (case.get("expected") or {}).get("branch", ""),
        "route_ok": True, "route_detail": "",
        "state_failures": [], "defects": [],
        "needs_manual_edit": False, "needs_manual_edit_strict": False,
        "causes": [], "error": None, "error_type": None,
        "citations": [], "citation_count": 0, "quality_score": None,
        "output_preview": "", "retrieval": {},
    }

    t_start = time.time()

    # ---------------- 历史记录任务: 直测存储层 ----------------
    if task == "history":
        extra_state = case.get("extra_state") or {}
        op, payload = extra_state.get("op"), extra_state.get("payload", {})
        try:
            passed, detail, extra = run_history_op(op, payload)
        except Exception as e:
            passed, detail, extra = False, f"{type(e).__name__}: {e}", {}
        rec["executed"] = True
        rec["exec_success"] = True
        rec["latency_sec"] = round(time.time() - t_start, 3)
        rec["final_state"] = {"__history_passed__": passed, "__history_detail__": detail}
        rec["history_detail"] = detail
        rec.update({k: v for k, v in extra.items() if k in
                    ("record_id", "summary_len", "graph_latency_sec",
                     "citation_count")})
        # 端到端用例内部跑了一次真实图, 把它的耗时/token 计入本用例
        if rec.get("graph_latency_sec"):
            rec["latency_sec"] = rec["graph_latency_sec"]
            rec["tokens_completion"] = int(extra.get("tokens", 0) or 0)
            rec["llm_calls"] = 1
            rec["cost"] = round(rec["tokens_completion"] / 1e6 * 6.0, 6)
        defects, ne, nes = M.check_quality(
            rec["final_state"], case.get("quality_checks") or [],
            None, manifest)
        rec["defects"] = defects
        rec["needs_manual_edit"] = ne
        rec["needs_manual_edit_strict"] = nes
        if not passed:
            rec["causes"] = [{
                "code": "HISTORY_ASSERT",
                "reason": detail,
                "suggestion": "检查 common/history_store.py 对应方法的实现契约",
            }]
        rec["biz_success"] = passed and not ne
        return rec

    # ---------------- 图任务 ----------------
    init_state = {"input": case.get("input", ""), "task_type": task}
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
    rec["timeout_sec"] = timeout_sec or CASE_TIMEOUT_SEC
    rec["interrupted"] = tr.get("interrupted", False)

    final_state = tr.get("final_state") or {}
    rec["main_route"] = M.normalize_route(rec["node_trace"])
    rec["branch"] = detect_branch(rec["node_trace"], final_state, task)
    rec["final_state_keys"] = sorted(final_state.keys())

    # 执行是否成功: 无异常 + 无超时 + 走到了 END(有节点轨迹且最后一个是主图层收口)
    rec["exec_success"] = (not rec["error"]) and bool(rec["node_trace"])

    # ---- 检索指标 ----
    citations = final_state.get("citations") or []
    rec["citations"] = [
        {"title": c.get("title", ""), "article_no": c.get("article_no", ""),
         "law_name": c.get("law_name", ""), "source": c.get("source", ""),
         "grade": c.get("grade", ""),
         "final_score": round(float(c.get("final_score", 0) or 0), 3),
         "content": str(c.get("content", ""))[:160]}
        for c in citations if isinstance(c, dict)
    ]
    rec["citation_count"] = len(citations)
    rec["quality_score"] = final_state.get("quality_score")
    rec["output_preview"] = str(final_state.get("output", "") or "")[:600]
    rec["retrieval"] = M.retrieval_metrics(citations, case.get("golden") or [])

    # ---- 路由 / 状态 / 质量 ----
    route_ok, route_detail = M.compare_route(
        rec["main_route"], case.get("expected") or {})
    rec["route_ok"] = route_ok
    rec["route_detail"] = route_detail

    exp_branch = rec["expected_branch"]
    if exp_branch and rec["branch"] != exp_branch:
        rec["route_ok"] = False
        rec["route_detail"] += f"; 分支不符: 期望 {exp_branch}, 实际 {rec['branch'] or '(未识别)'}"

    rec["state_failures"] = M.check_state(
        final_state, (case.get("expected") or {}).get("state_checks"))

    defects, ne, nes = M.check_quality(
        final_state, case.get("quality_checks") or [], rec["retrieval"], manifest)
    rec["defects"] = defects
    rec["needs_manual_edit"] = ne
    rec["needs_manual_edit_strict"] = nes

    # ---- 业务成功率: 执行成功 + 路由对 + 状态断言全过 + 无 block 级缺陷 ----
    rec["biz_success"] = bool(rec["exec_success"] and route_ok
                              and not rec["state_failures"] and not nes)

    # ---- 失败归因 ----
    if not rec["exec_success"] or not rec["biz_success"]:
        rec["causes"] = M.attribute_failure(
            case, rec, rec["route_ok"], rec["state_failures"],
            defects, rec["retrieval"], manifest)

    rec["cost"] = round(rec["tokens_prompt"] / 1e6 * 2.0
                        + rec["tokens_completion"] / 1e6 * 6.0, 6)
    return rec


def run_suite(tasks=None, case_ids=None, category=None, limit=None,
              resume=False, timeout_sec=None, verbose=True):
    """执行测试套件"""
    manifest = load_manifest()

    if verbose:
        print("=" * 74)
        print("【法智引擎 · 后端测试套件】")
        print(f"  SAFE MODE: {'开启 (外部调用已打桩)' if SAFE_MODE else '关闭'}"
              f" | 单用例超时 {timeout_sec or CASE_TIMEOUT_SEC}s")
        docs = manifest["neo4j"].get("documents", [])
        print(f"  知识库: {len(docs)} 份文档 / "
              f"{manifest['neo4j'].get('article_total', 0)} 条款 / "
              f"{len(manifest['neo4j'].get('case_nodes', []))} 案例节点")
        print("=" * 74)

    # 首次调用触发图加载(较慢), 提前做掉以便计时准确
    if verbose:
        print("\n⏳ 加载 LangGraph ...")
    t0 = time.time()
    apply_safe_mode(verbose=verbose)
    _COLLECTOR.install()
    get_graph()
    if verbose:
        print(f"   图加载完成 ({time.time() - t0:.1f}s)\n")

    # 组装用例
    cases = []
    for t in (tasks or all_tasks()):
        cases.extend(get_cases(task=t))
    if case_ids:
        cases = [c for c in cases if c["id"] in case_ids]
    if category:
        cases = [c for c in cases if c["category"] == category]
    if limit:
        cases = cases[:limit]

    # 断点续跑
    done = {}
    if resume and os.path.exists(RAW_RESULT_JSON):
        try:
            with open(RAW_RESULT_JSON, "r", encoding="utf-8") as f:
                prev = json.load(f)
            for r in prev.get("results", []):
                done[r["id"]] = r
            if verbose:
                print(f"♻️  断点续跑: 已有 {len(done)} 条结果, 将跳过\n")
        except Exception as e:
            if verbose:
                print(f"⚠️ 读取历史结果失败: {e}, 全量重跑\n")

    results = []
    for i, case in enumerate(cases, 1):
        if case["id"] in done:
            results.append(done[case["id"]])
            if verbose:
                print(f"[{i:>2}/{len(cases)}] {case['id']:<8} ⏭  已跳过(复用历史结果)")
            continue
        if verbose:
            print(f"[{i:>2}/{len(cases)}] {case['id']:<8} "
                  f"{TASK_META.get(case['task'], {}).get('name', case['task']):<8} "
                  f"{case.get('desc', '')[:40]} ... ", end="", flush=True)
        try:
            rec = run_one_case(case, manifest, timeout_sec=timeout_sec)
        except Exception as e:
            import traceback
            rec = {
                "id": case["id"], "task": case["task"],
                "task_name": TASK_META.get(case["task"], {}).get("name", case["task"]),
                "category": case.get("category", "normal"),
                "desc": case.get("desc", ""), "executed": True,
                "exec_success": False, "biz_success": False,
                "error": f"{type(e).__name__}: {e}",
                "error_type": "RUNNER_EXCEPTION",
                "traceback": traceback.format_exc()[-2000:],
                "causes": [{"code": "RUNNER_EXCEPTION",
                            "reason": f"{type(e).__name__}: {e}",
                            "suggestion": "测试引擎自身异常, 检查用例配置"}],
                "node_trace": [], "main_route": [], "defects": [],
                "state_failures": [], "retrieval": {},
                "needs_manual_edit": True, "needs_manual_edit_strict": True,
                "latency_sec": None, "tokens_prompt": 0, "tokens_completion": 0,
            }
        results.append(rec)
        if verbose:
            flag = "✅" if rec.get("biz_success") else ("⚠️ " if rec.get("exec_success") else "❌")
            lat = rec.get("latency_sec")
            print(f"{flag} {lat}s  "
                  f"P={_fmt((rec.get('retrieval') or {}).get('precision'))} "
                  f"R={_fmt((rec.get('retrieval') or {}).get('recall'))} "
                  f"MRR={_fmt((rec.get('retrieval') or {}).get('mrr'))}")

        # 每条用例后即时落盘, 长任务中断也不丢数据
        _dump({"results": results, "manifest": manifest}, RAW_RESULT_JSON)

    summary = M.aggregate(results, manifest)
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "summary": summary,
        "results": results,
        "manifest": manifest,
        "config": {
            "safe_mode": SAFE_MODE,
            "case_timeout_sec": timeout_sec or CASE_TIMEOUT_SEC,
            "top_k_precision": 5, "top_k_recall": 10, "mrr_cutoff": 10,
        },
    }
    _dump(payload, RAW_RESULT_JSON)

    if verbose:
        s = summary
        print("\n" + "=" * 74)
        print("【汇总】")
        print(f"  用例 {s['executed']}/{s['total_cases']}  "
              f"执行成功率 {s['exec_success_rate'] * 100:.1f}%  "
              f"业务成功率 {s['biz_success_rate'] * 100:.1f}%")
        print(f"  检索 P@5={_fmt(s['avg_precision'])}  R@10={_fmt(s['avg_recall'])}  "
              f"MRR={_fmt(s['avg_mrr'])}  (适用 {s['retrieval_cases']} 条)")
        print(f"  人工修改率(代理) {s['manual_edit_rate'] * 100:.1f}%  "
              f"(严格口径 {s['manual_edit_rate_strict'] * 100:.1f}%)")
        print(f"  延迟 P50={s['latency']['p50']}s  P95={s['latency']['p95']}s  "
              f"Max={s['latency']['max']}s")
        print(f"  Token {s['tokens']['total']} (in {s['tokens']['prompt']} / "
              f"out {s['tokens']['completion']})  估算成本 ¥{s['cost_total']}")
        print(f"\n  结果已写入: {RAW_RESULT_JSON}")
        print("=" * 74)
    return payload


def _fmt(v):
    return "-" if v is None else f"{v:.3f}"


def _dump(obj, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)


def main():
    ap = argparse.ArgumentParser(description="法智引擎后端测试套件")
    ap.add_argument("--task", action="append", help="只跑指定任务(可多次)")
    ap.add_argument("--case", action="append", help="只跑指定用例 id(可多次)")
    ap.add_argument("--category", help="只跑指定类别 normal/boundary/exception/negative")
    ap.add_argument("--limit", type=int, help="只跑前 N 条")
    ap.add_argument("--resume", action="store_true", help="跳过已有结果的用例")
    ap.add_argument("--timeout", type=int, help="单用例超时秒数")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    run_suite(tasks=args.task, case_ids=args.case, category=args.category,
              limit=args.limit, resume=args.resume, timeout_sec=args.timeout,
              verbose=not args.quiet)


if __name__ == "__main__":
    main()
