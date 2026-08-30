# -*- coding: utf-8 -*-
"""可观测 HTML 测试报告生成器

读取 test/outputs/raw_results.json + kb_manifest.json, 生成自包含(无外部依赖)
HTML 报告, 让 Agent / 人都能直观看到:

  · 总览 KPI 卡: 检索精确率 / 召回率 / MRR / 任务成功率(执行+业务) /
    人工修改率 / 响应时间 P50·P95 / 调用成本 / Token 总量
  · 8 分支意图路由主图 (SVG), 每个分支按真实结果染色 (绿/黄/红)
  · 每个任务的节点状态流转管线 (CSS 芯片), 失败/瓶颈节点红色高亮
  · 失败与告警用例的根因分析 (error_type + causes[].reason/suggestion)
  · 节点健康榜 (执行次数 / 失败 / 慢 / 平均·峰值耗时), 定位瓶颈节点
  · 成本与延迟分布

用法:
    python -m test.t_report                 # 读 outputs/raw_results.json 出 HTML
    python -m test.t_report --src <其他json> # 指定源
"""
import os
import sys
import json
import html
import statistics
import argparse

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.dirname(_TEST_DIR), _TEST_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from t_config import (REPORT_HTML, RAW_RESULT_JSON, KB_MANIFEST_JSON,
                      TASK_META, TASK_ORDER, SAFE_MODE,
                      PRICE_PROMPT_PER_1M, PRICE_COMPLETION_PER_1M, CURRENCY,
                      SLOW_NODE_SEC, BOTTLENECK_NODE_SEC,
                      TOP_K_PRECISION, TOP_K_RECALL, MRR_CUTOFF)  # noqa: E402

_e = lambda s: html.escape(str(s if s is not None else ""), quote=True)


# ============================================================================
# 聚合指标
# ============================================================================
def _avg(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _pct(v):
    return "-" if v is None else f"{v * 100:.1f}%"


def _f3(v):
    return "-" if v is None else f"{v:.3f}"


def _pctl(xs, p):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def aggregate(results, manifest):
    total = len(results)
    executed = sum(1 for r in results if r.get("executed"))
    exec_ok = sum(1 for r in results if r.get("exec_success"))
    biz_ok = sum(1 for r in results if r.get("biz_success"))

    retr = [r.get("retrieval") or {} for r in results]
    retr_cases = [x for x in retr if x.get("precision") is not None
                  or x.get("recall") is not None or x.get("mrr") is not None]
    prec = _avg([x.get("precision") for x in retr_cases])
    rec = _avg([x.get("recall") for x in retr_cases])
    mrr = _avg([x.get("mrr") for x in retr_cases])

    manual = sum(1 for r in results if r.get("needs_manual_edit"))
    manual_strict = sum(1 for r in results if r.get("needs_manual_edit_strict"))

    lat = [r.get("latency_sec") for r in results if r.get("latency_sec") is not None]
    toks_p = sum(r.get("tokens_prompt", 0) or 0 for r in results)
    toks_c = sum(r.get("tokens_completion", 0) or 0 for r in results)
    cost = sum(r.get("cost", 0.0) or 0.0 for r in results)
    llm_calls = sum(r.get("llm_calls", 0) or 0 for r in results)

    # 节点健康
    node_stats = {}
    for r in results:
        for nd in (r.get("node_trace") or []):
            n = nd.get("name")
            if not n:
                continue
            st = node_stats.setdefault(n, {"runs": 0, "fails": 0, "slow": 0,
                                           "durs": [], "subgraph": nd.get("subgraph", "")})
            st["runs"] += 1
            d = nd.get("duration_sec") or 0
            st["durs"].append(d)
            if nd.get("error"):
                st["fails"] += 1
            elif d >= BOTTLENECK_NODE_SEC:
                st["fails"] += 1
            elif d >= SLOW_NODE_SEC:
                st["slow"] += 1

    # 按任务的聚合
    per_task = {}
    for t in TASK_ORDER:
        rs = [r for r in results if r.get("task") == t]
        if not rs:
            continue
        rt = [r.get("retrieval") or {} for r in rs]
        rtc = [x for x in rt if x.get("precision") is not None
               or x.get("recall") is not None or x.get("mrr") is not None]
        per_task[t] = {
            "name": TASK_META.get(t, {}).get("name", t),
            "total": len(rs),
            "exec_ok": sum(1 for r in rs if r.get("exec_success")),
            "biz_ok": sum(1 for r in rs if r.get("biz_success")),
            "prec": _avg([x.get("precision") for x in rtc]),
            "rec": _avg([x.get("recall") for x in rtc]),
            "mrr": _avg([x.get("mrr") for x in rtc]),
            "manual": sum(1 for r in rs if r.get("needs_manual_edit")),
            "lat_avg": _avg([r.get("latency_sec") for r in rs if r.get("latency_sec") is not None]),
            "lat_p95": _pctl([r.get("latency_sec") for r in rs if r.get("latency_sec") is not None], 0.95),
            "cost": sum(r.get("cost", 0) or 0 for r in rs),
            "fails": [r for r in rs if not r.get("biz_success")],
        }

    return {
        "total": total, "executed": executed, "exec_ok": exec_ok, "biz_ok": biz_ok,
        "exec_success_rate": (exec_ok / executed) if executed else None,
        "biz_success_rate": (biz_ok / total) if total else None,
        "prec": prec, "rec": rec, "mrr": mrr, "retr_cases": len(retr_cases),
        "manual_rate": (manual / total) if total else None,
        "manual_rate_strict": (manual_strict / total) if total else None,
        "latency": {"p50": _pctl(lat, 0.5), "p95": _pctl(lat, 0.95),
                    "max": max(lat) if lat else None, "avg": _avg(lat)},
        "tokens": {"prompt": toks_p, "completion": toks_c, "total": toks_p + toks_c},
        "cost_total": cost, "llm_calls": llm_calls,
        "node_stats": node_stats, "per_task": per_task,
    }


# ============================================================================
# 渲染辅助
# ============================================================================
def node_status(nd):
    if nd.get("error"):
        return "fail"
    d = nd.get("duration_sec") or 0
    if d >= BOTTLENECK_NODE_SEC:
        return "bottleneck"
    if d >= SLOW_NODE_SEC:
        return "slow"
    return "ok"


STATUS_COLOR = {"ok": "#2e9e5b", "slow": "#e0a800", "bottleneck": "#d9480f", "fail": "#c92a2a"}


def pipeline_html(node_trace, highlight_name=None):
    """把节点轨迹渲染成可观测的状态流转芯片"""
    if not node_trace:
        return '<div class="muted">（无节点轨迹——用例未真正进入图执行）</div>'
    chips = []
    for nd in node_trace:
        st = node_status(nd)
        color = STATUS_COLOR[st]
        dur = nd.get("duration_sec") or 0
        name = nd.get("name", "?")
        flag = " ⚠" if (highlight_name and name == highlight_name) else ""
        title = (f"{name} | {st} | {dur:.1f}s"
                 + (f" | {nd.get('subgraph','')}" if nd.get('subgraph') else ""))
        chips.append(
            f'<span class="chip" style="border-color:{color};color:{color}" '
            f'title="{_e(title)}">{_e(name)}{flag}'
            f'<em>{dur:.1f}s</em></span>')
    # 用箭头连接
    body = '<span class="arrow">→</span>'.join(chips)
    return f'<div class="pipeline">{body}</div>'


def kpi_card(label, value, sub=None, tone="normal"):
    cls = "" if tone == "normal" else f" {tone}"
    sub_html = f'<div class="kpi-sub">{_e(sub)}</div>' if sub else ""
    return (f'<div class="kpi{cls}"><div class="kpi-label">{_e(label)}</div>'
            f'<div class="kpi-value">{_e(value)}</div>{sub_html}</div>')


def causes_html(causes):
    if not causes:
        return '<div class="muted">无</div>'
    items = []
    for c in causes:
        items.append(
            f'<li><b>[{_e(c.get("code",""))}]</b> {_e(c.get("reason",""))}'
            f'<br><span class="sug">建议: {_e(c.get("suggestion",""))}</span></li>')
    return f'<ul class="causes">{"".join(items)}</ul>'


def master_svg(per_task):
    """8 分支意图路由主图, 按任务结果染色"""
    # 中央 intent_router
    W, H = 680, 470
    parts = [f'<svg viewBox="0 0 {W} {H}" class="arch" xmlns="http://www.w3.org/2000/svg">']
    # 中心节点
    cx, cy = 340, 235
    parts.append(f'<rect x="{cx-70}" y="{cy-26}" width="140" height="52" rx="10" '
                 f'fill="#1f3a5f" stroke="#0b2545" stroke-width="2"/>')
    parts.append(f'<text x="{cx}" y="{cy-4}" text-anchor="middle" fill="#fff" '
                 f'font-size="14" font-weight="700">intent_router</text>')
    parts.append(f'<text x="{cx}" y="{cy+14}" text-anchor="middle" fill="#cfe3ff" '
                 f'font-size="10">7 分支 + 全能兜底</text>')
    # 8 个分支围绕
    branches = []
    for t in TASK_ORDER:
        if t == "history":
            continue
        pt = per_task.get(t, {})
        if pt:
            if pt["biz_ok"] == pt["total"]:
                col = "#2e9e5b"
            elif pt["biz_ok"] > 0:
                col = "#e0a800"
            else:
                col = "#c92a2a"
        else:
            col = "#adb5bd"
        branches.append((TASK_META.get(t, {}).get("name", t), col, pt))
    n = len(branches)
    import math
    for i, (name, col, pt) in enumerate(branches):
        ang = math.pi * (0.5 + 2 * i / n)  # 右侧半圆分布
        bx = cx + 250 * math.cos(ang)
        by = cy - 170 * math.sin(ang)
        bw, bh = 118, 46
        x = bx - bw / 2
        y = by - bh / 2
        parts.append(f'<line x1="{cx+60 if math.cos(ang)>0 else cx-60}" '
                     f'y1="{cy}" x2="{bx - (bw/2 if math.cos(ang)>0 else -bw/2)}" '
                     f'y2="{by}" stroke="#9aa7b4" stroke-width="1.5"/>')
        parts.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{bw}" height="{bh}" '
                     f'rx="8" fill="{col}" opacity="0.92"/>')
        parts.append(f'<text x="{bx:.0f}" y="{by-2:.0f}" text-anchor="middle" '
                     f'fill="#fff" font-size="12" font-weight="700">{_e(name)}</text>')
        if pt:
            parts.append(f'<text x="{bx:.0f}" y="{by+14:.0f}" text-anchor="middle" '
                         f'fill="#fff" font-size="10">{pt["biz_ok"]}/{pt["total"]} 通过</text>')
    # 历史记录独立存储层
    hpt = per_task.get("history", {})
    hcol = "#2e9e5b" if (hpt and hpt["biz_ok"] == hpt["total"]) else ("#c92a2a" if hpt else "#adb5bd")
    parts.append(f'<rect x="20" y="{H-58}" width="180" height="44" rx="8" fill="{hcol}" opacity="0.92"/>')
    parts.append(f'<text x="110" y="{H-40}" text-anchor="middle" fill="#fff" font-size="12" '
                 f'font-weight="700">历史记录 (存储层)</text>')
    parts.append(f'<text x="110" y="{H-24}" text-anchor="middle" fill="#fff" font-size="10">'
                 f'{hpt["biz_ok"]}/{hpt["total"]} 通过</text>' if hpt else
                 f'<text x="110" y="{H-24}" text-anchor="middle" fill="#fff" font-size="10">未测</text>')
    parts.append('</svg>')
    return "".join(parts)


def node_health_html(node_stats):
    rows = []
    items = sorted(node_stats.items(), key=lambda kv: (-kv[1]["fails"],
                  -(max(kv[1]["durs"]) if kv[1]["durs"] else 0)))
    for name, st in items:
        avg = statistics.mean(st["durs"]) if st["durs"] else 0
        mx = max(st["durs"]) if st["durs"] else 0
        if st["fails"] > 0:
            tone = "fail"
        elif st["slow"] > 0:
            tone = "slow"
        else:
            tone = "ok"
        col = STATUS_COLOR[tone]
        rows.append(
            f'<tr style="color:{col}">'
            f'<td>{_e(name)}</td>'
            f'<td>{_e(st["subgraph"])}</td>'
            f'<td>{st["runs"]}</td>'
            f'<td>{st["fails"]}</td>'
            f'<td>{st["slow"]}</td>'
            f'<td>{avg:.1f}s</td>'
            f'<td>{mx:.1f}s</td></tr>')
    return (f'<table class="grid"><thead><tr><th>节点</th><th>子图</th><th>执行</th>'
            f'<th>失败/瓶颈</th><th>慢</th><th>平均耗时</th><th>峰值耗时</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>')


# ============================================================================
# 主渲染
# ============================================================================
def build_report(src_json=RAW_RESULT_JSON, manifest_json=KB_MANIFEST_JSON):
    results = json.load(open(src_json, "r", encoding="utf-8")).get("results", [])
    manifest = json.load(open(manifest_json, "r", encoding="utf-8"))
    agg = aggregate(results, manifest)
    per_task = agg["per_task"]

    # ---- KPI 卡 ----
    kpis = []
    kpis.append(kpi_card("检索精确率 P@%d" % TOP_K_PRECISION, _f3(agg["prec"]),
                         f"适用 {agg['retr_cases']} 条检索型用例",
                         "good" if (agg["prec"] or 0) >= 0.5 else "warn"))
    kpis.append(kpi_card("召回率 R@%d" % TOP_K_RECALL, _f3(agg["rec"]),
                         "golden 全集召回口径",
                         "good" if (agg["rec"] or 0) >= 0.5 else "warn"))
    kpis.append(kpi_card("MRR@%d" % MRR_CUTOFF, _f3(agg["mrr"]),
                         "首条相关结果排名质量",
                         "good" if (agg["mrr"] or 0) >= 0.5 else "warn"))
    kpis.append(kpi_card("任务执行成功率", _pct(agg["exec_success_rate"]),
                         f"{agg['exec_ok']}/{agg['executed']} 进入图并跑到 END",
                         "good" if (agg["exec_success_rate"] or 0) >= 0.9 else "warn"))
    kpis.append(kpi_card("业务成功率", _pct(agg["biz_success_rate"]),
                         f"{agg['biz_ok']}/{agg['total']} 含路由+状态+质量",
                         "good" if (agg["biz_success_rate"] or 0) >= 0.9 else "warn"))
    kpis.append(kpi_card("人工修改率", _pct(agg["manual_rate"]),
                         f"严格口径 {_pct(agg['manual_rate_strict'])}",
                         "warn" if (agg["manual_rate"] or 0) > 0.3 else "normal"))
    kpis.append(kpi_card("响应时间", f"P50 {agg['latency']['p50']}s",
                         f"P95 {agg['latency']['p95']}s · Max {agg['latency']['max']}s",
                         "warn" if (agg['latency']['p95'] or 0) > 120 else "normal"))
    kpis.append(kpi_card("调用成本", f"{CURRENCY}{agg['cost_total']:.2f}",
                         f"{agg['tokens']['total']} tokens · {agg['llm_calls']} 次 LLM",
                         "normal"))
    kpi_row = "".join(kpis)

    # ---- 任务分卡 ----
    task_cards = []
    for t in TASK_ORDER:
        pt = per_task.get(t)
        if not pt:
            continue
        tone = ("good" if pt["biz_ok"] == pt["total"]
                else "warn" if pt["biz_ok"] > 0 else "bad")
        # 代表性管线 (首个有轨迹的用例)
        rep = next((r for r in results if r.get("task") == t
                    and r.get("node_trace")), None)
        pipe = pipeline_html(rep.get("node_trace", []) if rep else [])
        # 失败用例列表
        fail_rows = []
        for r in pt["fails"]:
            fail_rows.append(
                f'<tr><td>{_e(r.get("id"))}</td><td>{_e(r.get("desc"))}</td>'
                f'<td>{"❌" if not r.get("exec_success") else "⚠️"}</td>'
                f'<td>{_e(r.get("error_type") or "-")}</td>'
                f'<td>{_f3((r.get("retrieval") or {}).get("precision"))}</td>'
                f'<td>{_e(r.get("latency_sec"))}s</td></tr>')
        fail_tbl = ('<table class="grid sm"><thead><tr><th>用例</th><th>说明</th>'
                    '<th>状态</th><th>错误类型</th><th>P</th><th>延迟</th></tr></thead>'
                    f'<tbody>{"".join(fail_rows)}</tbody></table>') if fail_rows else \
                   '<div class="muted">本任务全部用例业务通过 ✅</div>'
        task_cards.append(f'''
        <div class="task-card {tone}">
          <div class="task-head"><span class="dot"></span>
            <b>{_e(pt["name"])}</b>
            <span class="task-meta">业务 {pt["biz_ok"]}/{pt["total"]} ·
            执行 {pt["exec_ok"]}/{pt["total"]} ·
            P@{TOP_K_PRECISION} {_f3(pt["prec"])} · R@{TOP_K_RECALL} {_f3(pt["rec"])} ·
            MRR {_f3(pt["mrr"])} · 修改率 {_pct(pt["manual"]/pt["total"] if pt["total"] else 0)} ·
            P95 {pt["lat_p95"]}s · {CURRENCY}{pt["cost"]:.2f}</span>
          </div>
          <div class="task-pipe">{pipe}</div>
          <details><summary>失败 / 告警用例 ({len(pt["fails"])})</summary>
            {fail_tbl}</details>
        </div>''')

    # ---- 失败根因分析 ----
    fail_analysis = []
    for r in results:
        if r.get("biz_success"):
            continue
        flag = "❌ 执行失败" if not r.get("exec_success") else "⚠️ 业务未达标"
        # 定位失败节点
        fail_node = None
        for nd in (r.get("node_trace") or []):
            if nd.get("error"):
                fail_node = nd.get("name")
        fail_analysis.append(f'''
        <div class="fail-block">
          <div class="fail-title">{flag} · <b>{_e(r.get("id"))}</b>
            （{_e(r.get("task_name"))} · {_e(r.get("desc"))}）</div>
          <div class="fail-meta">error_type: <code>{_e(r.get("error_type") or "-")}</code>
            · 分支: {_e(r.get("branch") or "(未识别)")} / 期望 {_e(r.get("expected_branch") or "-")}
            · 路由 {'✔' if r.get("route_ok") else '✘'}</div>
          <div class="fail-pipe">{pipeline_html(r.get("node_trace", []), fail_node)}</div>
          <div class="fail-cause"><b>根因分析:</b>{causes_html(r.get("causes"))}</div>
          <div class="muted">输出预览: {_e((r.get("output_preview") or "")[:200])}</div>
        </div>''')
    fail_html = "".join(fail_analysis) if fail_analysis else \
        '<div class="ok-banner">🎉 全部用例业务通过, 未触发失败根因分析。</div>'

    # ---- 文档覆盖 (grounding) ----
    docs = manifest.get("neo4j", {}).get("documents", [])
    doc_items = "".join(
        f'<li>{_e(d.get("name",""))} <span class="muted">'
        f'({d.get("article_count",0)} 条 · {d.get("source_type","")})</span></li>'
        for d in docs)
    case_nodes = manifest.get("neo4j", {}).get("case_nodes", [])
    case_note = ("<b class='warn'>⚠ 案例库结构性缺口:</b> 已处理 case_698be5cb1791f068.txt, "
                 "但图谱未生成 Case 节点、cases 向量库无 Article 切片, 案例检索任务预期召回偏低。"
                 if not case_nodes else f"案例节点 {len(case_nodes)} 个已入库")

    html_doc = f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>法智引擎 · 后端可观测测试报告</title>
<style>
  :root {{ --bg:#f5f7fa; --card:#fff; --line:#e3e8ef; --ink:#1f2933; --mut:#7b8794; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
    font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
    font-size:14px; line-height:1.5; }}
  .wrap {{ max-width:1180px; margin:0 auto; padding:24px 20px 60px; }}
  h1 {{ font-size:24px; margin:0 0 4px; }}
  .sub {{ color:var(--mut); margin:0 0 18px; font-size:13px; }}
  .kpis {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-bottom:22px; }}
  .kpi {{ background:var(--card); border:1px solid var(--line); border-radius:12px;
    padding:14px 16px; }}
  .kpi-label {{ color:var(--mut); font-size:12px; }}
  .kpi-value {{ font-size:22px; font-weight:800; margin:4px 0 2px; }}
  .kpi-sub {{ color:var(--mut); font-size:11px; }}
  .kpi.good .kpi-value {{ color:#2e9e5b; }}
  .kpi.warn .kpi-value {{ color:#e0a800; }}
  .kpi.bad .kpi-value {{ color:#c92a2a; }}
  .section {{ background:var(--card); border:1px solid var(--line); border-radius:12px;
    padding:18px 20px; margin-bottom:20px; }}
  .section h2 {{ font-size:17px; margin:0 0 12px; border-left:4px solid #1f3a5f; padding-left:10px; }}
  .arch {{ width:100%; height:auto; background:#fafbfc; border-radius:8px; }}
  .task-card {{ border:1px solid var(--line); border-left:5px solid #adb5bd; border-radius:10px;
    padding:14px 16px; margin-bottom:14px; background:#fff; }}
  .task-card.good {{ border-left-color:#2e9e5b; }}
  .task-card.warn {{ border-left-color:#e0a800; }}
  .task-card.bad {{ border-left-color:#c92a2a; }}
  .task-head {{ display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:8px; }}
  .task-head .dot {{ width:10px; height:10px; border-radius:50%; background:#1f3a5f; }}
  .task-meta {{ color:var(--mut); font-size:12px; }}
  .pipeline {{ display:flex; flex-wrap:wrap; align-items:center; gap:4px; margin:6px 0; }}
  .chip {{ border:1.5px solid #999; border-radius:14px; padding:3px 9px; font-size:12px;
    background:#fff; white-space:nowrap; }}
  .chip em {{ font-style:normal; color:var(--mut); margin-left:5px; font-size:11px; }}
  .arrow {{ color:#9aa7b4; font-size:12px; }}
  .grid {{ width:100%; border-collapse:collapse; font-size:12.5px; }}
  .grid th, .grid td {{ border:1px solid var(--line); padding:6px 8px; text-align:left; }}
  .grid th {{ background:#f0f3f7; }}
  .grid.sm th, .grid.sm td {{ padding:4px 6px; font-size:12px; }}
  details {{ margin-top:8px; }}
  summary {{ cursor:pointer; color:#1f3a5f; font-size:13px; }}
  .fail-block {{ border:1px solid #f1c0c0; background:#fff7f7; border-radius:10px;
    padding:12px 14px; margin-bottom:12px; }}
  .fail-title {{ font-weight:700; margin-bottom:4px; }}
  .fail-meta {{ color:var(--mut); font-size:12px; margin-bottom:6px; }}
  .fail-meta code {{ background:#f0f0f0; padding:1px 5px; border-radius:4px; }}
  .fail-cause {{ margin:6px 0; }}
  .causes {{ margin:4px 0 4px 18px; padding:0; font-size:13px; }}
  .causes li {{ margin-bottom:4px; }}
  .sug {{ color:#2b6cb0; font-size:12px; }}
  .ok-banner {{ background:#eafaf0; border:1px solid #b2e2c6; color:#2e9e5b;
    border-radius:8px; padding:12px; }}
  .muted {{ color:var(--mut); font-size:12px; }}
  .warn {{ color:#c92a2a; }}
  .note {{ font-size:12.5px; color:#52606d; }}
  ul.coverage {{ margin:6px 0 0 18px; padding:0; }}
  footer {{ color:var(--mut); font-size:12px; text-align:center; margin-top:24px; }}
</style></head>
<body><div class="wrap">
  <h1>法智引擎 · 后端可观测测试报告</h1>
  <p class="sub">生成时间: {_e(json.load(open(src_json,encoding="utf-8")).get("generated_at","-"))}
     · SAFE MODE: {"开启(外部发布/付费查询已打桩)" if SAFE_MODE else "关闭"}
     · 指标单价: 输入 {CURRENCY}{PRICE_PROMPT_PER_1M}/M · 输出 {CURRENCY}{PRICE_COMPLETION_PER_1M}/M
     · 检索口径 P@{TOP_K_PRECISION}/R@{TOP_K_RECALL}/MRR@{MRR_CUTOFF}</p>

  <div class="kpis">{kpi_row}</div>

  <div class="section">
    <h2>① 8 分支意图路由主图（按真实结果染色）</h2>
    {master_svg(per_task)}
    <p class="note">中心 <b>intent_router</b> 分发至 7 类法律任务 + 全能兜底；
       <span style="color:#2e9e5b">绿=全部业务通过</span> /
       <span style="color:#e0a800">黄=部分通过</span> /
       <span style="color:#c92a2a">红=全部失败</span>。历史记录为独立存储层直测。</p>
  </div>

  <div class="section">
    <h2>② 各任务节点状态流转（failure/bottleneck 红色高亮）</h2>
    {''.join(task_cards)}
  </div>

  <div class="section">
    <h2>③ 失败 / 告警根因分析</h2>
    {fail_html}
  </div>

  <div class="section">
    <h2>④ 节点健康榜（定位瓶颈 / 失败节点）</h2>
    {node_health_html(agg["node_stats"])}
  </div>

  <div class="section">
    <h2>⑤ 测试数据 Grounding（已入库知识清单）</h2>
    <p class="note">知识库: {len(docs)} 份文档 / {manifest.get("neo4j",{}).get("article_total",0)} 条款 /
       {len(case_nodes)} 案例节点。本测试集全部用例依据以下真实入库数据构造:</p>
    <ul class="coverage">{doc_items}</ul>
    <p class="note" style="margin-top:8px">{case_note}</p>
  </div>

  <footer>法智引擎测试套件 · 自包含报告 · 由 test/t_report.py 生成</footer>
</div></body></html>'''

    with open(REPORT_HTML, "w", encoding="utf-8") as f:
        f.write(html_doc)
    return REPORT_HTML, agg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=RAW_RESULT_JSON)
    ap.add_argument("--manifest", default=KB_MANIFEST_JSON)
    args = ap.parse_args()
    path, agg = build_report(args.src, args.manifest)
    print(f"报告已生成: {path}")
    print(f"  用例 {agg['total']} | 业务成功率 {_pct(agg['biz_success_rate'])} "
          f"| P@{TOP_K_PRECISION} {_f3(agg['prec'])} R@{TOP_K_RECALL} {_f3(agg['rec'])} "
          f"MRR {_f3(agg['mrr'])} | 成本 {CURRENCY}{agg['cost_total']:.2f}")


if __name__ == "__main__":
    main()
