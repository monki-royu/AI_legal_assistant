# -*- coding: utf-8 -*-
"""badcase HTML 报告生成器 —— 状态流转图 + 技术指标 + 业务指标

==============================================================================
【设计目标】
    让"是哪个节点出问题了"一眼可见。报告分五层, 从粗到细:

    ① 环境体检      : Neo4j / FAISS / 数据一致性(探针结论, 不跑图也准)
    ② 指标总览      : 18 项技术 + 业务指标卡
    ③ 状态流转图    : 8 张子图 SVG, 节点按"执行次数/耗时/错误"着色,
                      未执行节点灰色虚线 = 本次没走到, 一眼看出分支
    ④ 用例矩阵      : 10 条用例 × 指标, 点击展开逐节点轨迹与归因
    ⑤ 证据层        : 关键词可达性热力表 + 探针结论 + 缺陷分布

【数据来源】
    badcase_results.json (跑图结果, 可选)  +  probe_report.json (探针, 必有)
    只跑探针也能出报告 —— 保证"环境不通"时报告依然可用。
==============================================================================
"""
import os
import sys
import json
import html
import time

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.dirname(os.path.dirname(_TEST_DIR)), _TEST_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

RESULTS_JSON = os.path.join(_TEST_DIR, "badcase_results.json")
PROBE_JSON = os.path.join(_TEST_DIR, "probe_report.json")
REPORT_HTML = os.path.join(_TEST_DIR, "badcase_flow.html")

SLOW_SEC = 20.0
BOTTLENECK_SEC = 60.0


# ============================================================================
# 一、图结构定义 (与 langgraph_main.py / subgraphs/*.py 一一对应)
#   节点: (id, 显示名, col, row, 是否守卫/分支节点)
#   边  : (from, to, 边标签)
# ============================================================================
PANELS = [
    {
        "id": "main", "title": "① 主图 · 两级路由", "h": 260,
        "nodes": [
            ("__start__", "START", 0, 0, 0),
            ("xiaohongshu_publish_intent", "L1 小红书意图", 1, 0, 1),
            ("intent_router", "L2 意图路由", 2, 0, 0),
            ("qa", "qa 子图", 3, -2, 0),
            ("r_retrieval", "r_retrieval 子图", 3, -1, 0),
            ("contract_compliance", "合同合规子图", 3, 0, 0),
            ("docgen", "文书生成子图", 3, 1, 0),
            ("xhs", "小红书子图", 3, 2, 0),
            ("__end__", "END", 4, 0, 0),
        ],
        "edges": [
            ("__start__", "xiaohongshu_publish_intent", ""),
            ("xiaohongshu_publish_intent", "intent_router", "非小红书"),
            ("xiaohongshu_publish_intent", "xhs", "小红书"),
            ("intent_router", "qa", "legal_qa"),
            ("intent_router", "r_retrieval", "检索类"),
            ("intent_router", "contract_compliance", "合同/合规"),
            ("intent_router", "docgen", "文书生成"),
            ("qa", "__end__", ""), ("r_retrieval", "__end__", ""),
            ("contract_compliance", "__end__", ""), ("docgen", "__end__", ""),
            ("xhs", "__end__", ""),
        ],
    },
    {
        "id": "qa", "title": "② QA 子图 (legal_qa)", "h": 170,
        "nodes": [
            ("qa_intent_classify", "qa_intent_classify\n(法律相关性)", 0, 0, 1),
            ("qa_retrieval", "检索子图(嵌套)", 1, -1, 0),
            ("llm_direct_out", "llm_direct_out\n(LLM 直答)", 1, 1, 0),
            ("legal_qa_final_answer", "legal_qa_final_answer", 2, -1, 0),
            ("__end_qa__", "END", 3, 0, 0),
        ],
        "edges": [
            ("qa_intent_classify", "qa_retrieval", "是"),
            ("qa_intent_classify", "llm_direct_out", "否"),
            ("qa_retrieval", "legal_qa_final_answer", ""),
            ("legal_qa_final_answer", "__end_qa__", ""),
            ("llm_direct_out", "__end_qa__", ""),
        ],
    },
    {
        "id": "retrieval",
        "title": "③ 检索子图 (被复用 3 次: r_retrieval / cc_retrieval / qa_retrieval)",
        "h": 240,
        "nodes": [
            ("retrieval_intent_decompose", "intent_decompose\n挂载源+LLM提词", 0, 0, 0),
            ("credit_precheck", "credit_precheck", 1, 0, 0),
            ("retrieval_entity_recall", "entity_recall\n图谱+FAISS", 2, 0, 0),
            ("retrieval_precision_filter", "precision_filter\n语义重排+闸门", 3, 0, 0),
            ("retrieval_fusion_ranking", "fusion_ranking\n7项融合+质量分", 4, 0, 0),
            ("quality_gate_retry", "quality_gate_retry", 5, 0, 1),
            ("beida_fabao_gate", "beida_fabao_gate", 6, -1, 1),
            ("credit_check", "credit_check", 7, -1, 0),
            ("context_pack", "context_pack\n(出口分流)", 8, -1, 0),
            ("__end_ret__", "END", 9, 0, 0),
        ],
        "edges": [
            ("retrieval_intent_decompose", "credit_precheck", ""),
            ("credit_precheck", "retrieval_entity_recall", ""),
            ("retrieval_entity_recall", "retrieval_precision_filter", ""),
            ("retrieval_precision_filter", "retrieval_fusion_ranking", ""),
            ("retrieval_fusion_ranking", "quality_gate_retry", ""),
            ("quality_gate_retry", "beida_fabao_gate", "pass"),
            ("quality_gate_retry", "retrieval_intent_decompose", "retry ×≤3"),
            ("beida_fabao_gate", "credit_check", ""),
            ("credit_check", "context_pack", ""),
            ("context_pack", "__end_ret__", ""),
        ],
    },
    {
        "id": "cc", "title": "④ 合同合规子图 (输入归一化 + 三子图编排)", "h": 220,
        "nodes": [
            ("input_source_router", "input_source_router", 0, 0, 1),
            ("doc_extract", "doc_extract", 1, -1, 0),
            ("doc_empty_guard", "doc_empty_guard", 2, -1, 1),
            ("text_recognize", "text_recognize", 1, 1, 1),
            ("preprocess", "preprocess 子图", 3, 0, 0),
            ("cc_retrieval", "cc_retrieval 子图", 4, 0, 0),
            ("dual_review", "dual_review 子图", 5, 0, 0),
            ("__end_cc__", "END", 6, 0, 0),
        ],
        "edges": [
            ("input_source_router", "doc_extract", "有文档"),
            ("input_source_router", "text_recognize", "纯文本"),
            ("doc_extract", "doc_empty_guard", ""),
            ("doc_empty_guard", "preprocess", "pass"),
            ("doc_empty_guard", "__end_cc__", "block"),
            ("text_recognize", "preprocess", "pass"),
            ("text_recognize", "__end_cc__", "block"),
            ("preprocess", "cc_retrieval", ""),
            ("cc_retrieval", "dual_review", ""),
            ("dual_review", "__end_cc__", ""),
        ],
    },
    {
        "id": "preprocess", "title": "⑤ 预处理子图 (5 节点)", "h": 130,
        "nodes": [
            ("party_identify", "party_identify", 0, 0, 0),
            ("contract_classify", "contract_classify", 1, 0, 0),
            ("full_text_segment", "full_text_segment", 2, 0, 0),
            ("numeric_extract", "numeric_extract", 3, 0, 0),
            ("llm_query_extract", "llm_query_extract", 4, 0, 0),
        ],
        "edges": [(a, b, "") for a, b in zip(
            ["party_identify", "contract_classify", "full_text_segment",
             "numeric_extract"],
            ["contract_classify", "full_text_segment", "numeric_extract",
             "llm_query_extract"])],
    },
    {
        "id": "dual", "title": "⑥ 双审子图 (合同双审 / 合规单审)", "h": 170,
        "nodes": [
            ("parallel_dual_review", "parallel_dual_review\n(线程内并发)", 0, 0, 0),
            ("conflict_resolution", "conflict_resolution\n(仅合同审核)", 1, -1, 1),
            ("numeric_validate", "numeric_validate", 2, 0, 0),
            ("risk_aggregate", "risk_aggregate", 3, 0, 0),
            ("final_delivery", "final_delivery", 4, 0, 0),
        ],
        "edges": [
            ("parallel_dual_review", "conflict_resolution", "contract_review"),
            ("parallel_dual_review", "numeric_validate", "compliance_review"),
            ("conflict_resolution", "numeric_validate", ""),
            ("numeric_validate", "risk_aggregate", ""),
            ("risk_aggregate", "final_delivery", ""),
        ],
    },
    {
        "id": "docgen", "title": "⑦ 文书生成子图 (澄清守卫 + 双路并发检索)", "h": 200,
        "nodes": [
            ("doc_case_analyze", "doc_case_analyze", 0, 0, 0),
            ("doc_template_match", "doc_template_match", 1, -1, 0),
            ("doc_query_plan", "doc_query_plan", 2, -1, 0),
            ("doc_parallel_retrieve", "doc_parallel_retrieve\n(并发法条+类案)", 3, -1, 0),
            ("doc_clause_fill", "doc_clause_fill", 4, -1, 0),
            ("doc_risk_analysis", "doc_risk_analysis", 5, -1, 0),
            ("doc_final_delivery", "doc_final_delivery", 6, -1, 0),
            ("__end_doc__", "END", 7, 0, 0),
        ],
        "edges": [
            ("doc_case_analyze", "doc_template_match", "continue"),
            ("doc_case_analyze", "__end_doc__", "clarify"),
            ("doc_template_match", "doc_query_plan", ""),
            ("doc_query_plan", "doc_parallel_retrieve", ""),
            ("doc_parallel_retrieve", "doc_clause_fill", ""),
            ("doc_clause_fill", "doc_risk_analysis", ""),
            ("doc_risk_analysis", "doc_final_delivery", ""),
            ("doc_final_delivery", "__end_doc__", ""),
        ],
    },
    {
        "id": "xhs", "title": "⑧ 小红书子图", "h": 130,
        "nodes": [
            ("text_generate", "text_generate", 0, 0, 0),
            ("image_generate", "image_generate", 1, 0, 0),
            ("xhs_check_text_image", "check_text_image", 2, 0, 1),
            ("xhs_auto_publish", "auto_publish", 3, 0, 0),
            ("generate_markdown", "generate_markdown", 4, 0, 0),
        ],
        "edges": [(a, b, "") for a, b in zip(
            ["text_generate", "image_generate", "xhs_check_text_image",
             "xhs_auto_publish"],
            ["image_generate", "xhs_check_text_image", "xhs_auto_publish",
             "generate_markdown"])],
    },
]

NODE_W, NODE_H, COL_W, ROW_H = 132, 40, 158, 52


# ============================================================================
# 二、运行时统计聚合
# ============================================================================
def build_node_stats(results):
    """按裸节点名聚合 (node_trace[i]['name'])"""
    st = {}
    for r in results or []:
        for nd in (r.get("node_trace") or []):
            nm = nd.get("name") or ""
            if not nm:
                continue
            d = st.setdefault(nm, {"runs": 0, "total": 0.0, "max": 0.0,
                                   "errors": 0, "cases": set(), "logs": []})
            d["runs"] += 1
            dur = nd.get("duration_sec") or 0
            d["total"] += dur
            d["max"] = max(d["max"], dur)
            d["cases"].add(r["id"])
            if nd.get("error"):
                d["errors"] += 1
                if len(d["logs"]) < 3:
                    d["logs"].append(str(nd.get("error"))[:200])
    for k, v in st.items():
        v["avg"] = round(v["total"] / max(1, v["runs"]), 3)
        v["total"] = round(v["total"], 3)
        v["max"] = round(v["max"], 3)
        v["cases"] = sorted(v["cases"])
    return st


def _node_class(st, name):
    d = st.get(name)
    if not d:
        return "idle", "未执行"
    if d["errors"]:
        return "err", f"出错 {d['errors']} 次"
    if d["max"] >= BOTTLENECK_SEC:
        return "bottleneck", f"瓶颈 max={d['max']}s"
    if d["max"] >= SLOW_SEC:
        return "slow", f"慢节点 max={d['max']}s"
    return "ok", f"执行 {d['runs']} 次 / avg {d['avg']}s"


# ============================================================================
# 三、SVG 生成
# ============================================================================
def _esc(s):
    return html.escape(str(s), quote=True)


def render_panel(panel, st):
    nodes = panel["nodes"]
    cols = [n[2] for n in nodes]
    rows = [n[3] for n in nodes]
    max_col = max(cols)
    min_row, max_row = min(rows), max(rows)
    width = 40 + (max_col + 1) * COL_W
    height = max(panel.get("h", 160),
                 40 + (max_row - min_row + 1) * ROW_H)
    y_center = height / 2

    pos = {}
    for nid, label, col, row, is_guard in nodes:
        pos[nid] = (24 + col * COL_W, y_center + row * ROW_H - NODE_H / 2,
                    is_guard)

    out = [f'<svg viewBox="0 0 {width} {height}" class="gsvg" '
           f'preserveAspectRatio="xMinYMin meet">',
           '<defs><marker id="ar" markerWidth="8" markerHeight="8" '
           'refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L7,3 z" '
           'fill="#9aa4b2"/></marker></defs>']

    # ---- 边 ----
    for src, dst, label in panel["edges"]:
        if src not in pos or dst not in pos:
            continue
        sx, sy, _ = pos[src]
        dx, dy, _ = pos[dst]
        x1, y1 = sx + NODE_W, sy + NODE_H / 2
        x2, y2 = dx, dy + NODE_H / 2
        if x2 >= x1:
            if y1 == y2:
                d = f"M{x1},{y1} L{x2 - 6},{y2}"
            else:
                mx = (x1 + x2) / 2
                d = (f"M{x1},{y1} C{mx},{y1} {mx},{y2} {x2 - 6},{y2}")
            lx = (x1 + x2) / 2
            ly = (y1 + y2) / 2 - 6
        else:
            # 回边 (retry): 从下方绕回
            dip = max(y1, y2) + NODE_H / 2 + 14
            d = (f"M{x1},{y1} C{x1 + 40},{dip} {x2 - 40},{dip} {x2 - 6},{y2}")
            lx = (x1 + x2) / 2
            ly = dip + 12
        out.append(f'<path d="{d}" fill="none" stroke="#9aa4b2" '
                   f'stroke-width="1.4" marker-end="url(#ar)"/>')
        if label:
            out.append(f'<text x="{lx:.0f}" y="{ly:.0f}" class="elab">{_esc(label)}</text>')

    # ---- 节点 ----
    for nid, label, col, row, is_guard in nodes:
        x, y, guard = pos[nid]
        cls, tip = _node_class(st, nid)
        d = st.get(nid)
        badge = f"{d['runs']}×" if d else "0×"
        sec = f"{d['avg']}s" if d else ""
        lines = label.split("\n")
        ly = y + NODE_H / 2 - (len(lines) - 1) * 6
        out.append(
            f'<g class="gnode {cls}{" guard" if (guard or is_guard) else ""}">'
            f'<title>{_esc(nid)} | {_esc(tip)}</title>'
            f'<rect x="{x}" y="{y}" width="{NODE_W}" height="{NODE_H}" rx="7"/>')
        for i, ln in enumerate(lines):
            out.append(f'<text x="{x + NODE_W/2}" y="{ly + i*13}" '
                       f'class="nlab" text-anchor="middle">{_esc(ln)}</text>')
        out.append(f'<text x="{x + 6}" y="{y + NODE_H - 5}" class="nsub">'
                   f'{_esc(badge)}</text>')
        if sec:
            out.append(f'<text x="{x + NODE_W - 6}" y="{y + NODE_H - 5}" '
                       f'class="nsub" text-anchor="end">{_esc(sec)}</text>')
        out.append('</g>')
    out.append('</svg>')
    return "".join(out), width, height


# ============================================================================
# 四、HTML
# ============================================================================
CSS = """
*{box-sizing:border-box}
body{margin:0;background:#f5f7fa;color:#1f2328;
 font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",
 "Microsoft YaHei",sans-serif;font-size:14px;line-height:1.6}
.wrap{max-width:1440px;margin:0 auto;padding:24px 28px 60px}
h1{font-size:24px;margin:0 0 4px}
h2{font-size:18px;margin:32px 0 12px;padding-left:10px;border-left:4px solid #2563eb}
h3{font-size:15px;margin:18px 0 8px;color:#374151}
.sub{color:#6b7280;font-size:13px;margin-bottom:18px}
.card{background:#fff;border:1px solid #e3e6eb;border-radius:10px;padding:16px 18px;
 margin-bottom:16px}
.badges{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 4px}
.badge{display:inline-flex;align-items:center;gap:5px;padding:4px 10px;border-radius:20px;
 font-size:12px;font-weight:600;border:1px solid}
.b-ok{background:#e8f5e9;color:#1b5e20;border-color:#a5d6a7}
.b-bad{background:#ffebee;color:#b71c1c;border-color:#ef9a9a}
.b-warn{background:#fff8e1;color:#e65100;border-color:#ffcc80}
.b-info{background:#e3f2fd;color:#0d47a1;border-color:#90caf9}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(148px,1fr));gap:10px}
.kpi{background:#fff;border:1px solid #e3e6eb;border-radius:9px;padding:10px 12px}
.kpi .k{font-size:11px;color:#6b7280;letter-spacing:.3px}
.kpi .v{font-size:21px;font-weight:700;margin-top:2px;line-height:1.2}
.kpi .u{font-size:11px;color:#9aa4b2;margin-left:2px}
.v-good{color:#1b5e20}.v-warn{color:#e65100}.v-bad{color:#b71c1c}.v-na{color:#9aa4b2}
.gsvg{width:100%;height:auto;display:block;overflow:visible}
.gnode rect{fill:#fafafa;stroke:#d0d5dd;stroke-width:1.2}
.gnode text{font-size:11px;fill:#6b7280;pointer-events:none}
.gnode.ok rect{fill:#e8f5e9;stroke:#43a047}
.gnode.ok text{fill:#1b5e20}
.gnode.slow rect{fill:#fff8e1;stroke:#f9a825;stroke-width:1.8}
.gnode.slow text{fill:#e65100}
.gnode.bottleneck rect{fill:#ffe0e0;stroke:#e53935;stroke-width:2}
.gnode.bottleneck text{fill:#b71c1c}
.gnode.err rect{fill:#ffcdd2;stroke:#b71c1c;stroke-width:2.4}
.gnode.err text{fill:#b71c1c}
.gnode.guard rect{stroke-dasharray:4 3}
.gnode .nlab{font-size:11px;font-weight:600}
.gnode .nsub{font-size:9.5px;fill:#9aa4b2}
.elab{font-size:10px;fill:#6b7280;text-anchor:middle}
.legend{display:flex;gap:14px;flex-wrap:wrap;margin:10px 0 14px;font-size:12px;
 color:#4b5563}
.legend i{display:inline-block;width:13px;height:13px;border-radius:3px;
 border:1.4px solid;vertical-align:-2px;margin-right:5px}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{border:1px solid #e8eaed;padding:6px 8px;text-align:left;vertical-align:top}
th{background:#f1f3f5;font-weight:600;white-space:nowrap}
td.c,th.c{text-align:center}
tr:hover td{background:#fafbfc}
.sev{display:inline-block;padding:1px 7px;border-radius:4px;font-size:11px;font-weight:700}
.s-P0{background:#ffebee;color:#b71c1c}.s-P1{background:#fff8e1;color:#e65100}
.s-P2{background:#e3f2fd;color:#0d47a1}
.st{display:inline-block;padding:1px 8px;border-radius:11px;font-size:11px;font-weight:700}
.st-pass{background:#e8f5e9;color:#1b5e20}.st-fail{background:#ffebee;color:#b71c1c}
.st-warn{background:#fff8e1;color:#e65100}
details{margin:6px 0}
details>summary{cursor:pointer;padding:6px 10px;background:#f8f9fb;border:1px solid #e3e6eb;
 border-radius:6px;font-weight:600;font-size:13px;list-style:none}
details>summary::-webkit-details-marker{display:none}
details>summary:before{content:"▸ ";color:#2563eb}
details[open]>summary:before{content:"▾ "}
details .body{padding:10px 12px;border:1px solid #e3e6eb;border-top:none;
 border-radius:0 0 6px 6px;background:#fff}
.track{display:flex;flex-wrap:wrap;gap:5px;align-items:center;margin:6px 0}
.tn{display:inline-flex;flex-direction:column;align-items:center;justify-content:center;
 min-width:88px;max-width:150px;padding:4px 8px;border-radius:6px;border:1.4px solid #d0d5dd;
 background:#fafafa;font-size:11px;line-height:1.3}
.tn b{font-size:10.5px;font-weight:700;word-break:break-all}
.tn span{font-size:10px;color:#6b7280}
.tn.ok{background:#e8f5e9;border-color:#43a047;color:#1b5e20}
.tn.slow{background:#fff8e1;border-color:#f9a825;color:#e65100}
.tn.bottleneck,.tn.err{background:#ffcdd2;border-color:#b71c1c;color:#b71c1c}
.arrow{color:#9aa4b2;font-size:13px}
.mut{color:#6b7280}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11.5px}
pre{background:#f6f8fa;border:1px solid #e3e6eb;border-radius:6px;padding:8px 10px;
 overflow:auto;max-height:280px;font-size:11.5px;white-space:pre-wrap;word-break:break-all}
.bar{height:9px;background:#eef0f3;border-radius:5px;overflow:hidden;min-width:70px}
.bar>i{display:block;height:100%;background:#2563eb}
.bar>i.warn{background:#f9a825}.bar>i.bad{background:#e53935}
.hit0{background:#ffebee;color:#b71c1c;font-weight:700;padding:1px 6px;border-radius:4px}
.hit1{background:#e8f5e9;color:#1b5e20;padding:1px 6px;border-radius:4px}
.orphan{background:#fff8e1;color:#e65100;padding:1px 6px;border-radius:4px}
.note{background:#f8f9fb;border-left:3px solid #2563eb;padding:8px 12px;margin:8px 0;
 font-size:12.5px;color:#374151}
.warnbox{background:#fff8e1;border-left:3px solid #f9a825;padding:8px 12px;margin:8px 0;
 font-size:12.5px;color:#6d4c00}
.flex{display:flex;gap:16px;flex-wrap:wrap}
.flex>div{flex:1;min-width:280px}
"""


def _kpi(k, v, unit="", cls="", sub=""):
    return (f'<div class="kpi"><div class="k">{_esc(k)}</div>'
            f'<div class="v {cls}">{v}'
            f'{f"<span class=u>{_esc(unit)}</span>" if unit else ""}</div>'
            f'{f"<div class=k style=margin-top:2px>{_esc(sub)}</div>" if sub else ""}'
            f'</div>')


def _pctf(v, digits=1):
    return "-" if v is None else f"{v * 100:.{digits}f}"


def _num(v, d=3):
    return "-" if v is None else f"{v:.{d}f}"


def build_report(results=None, probe=None, out_path=REPORT_HTML):
    if results is None and os.path.exists(RESULTS_JSON):
        with open(RESULTS_JSON, "r", encoding="utf-8") as f:
            payload = json.load(f)
        results = payload.get("results") or []
        probe = payload.get("probe") or probe
        summary = payload.get("summary")
    else:
        results = results or []
        summary = None
    if probe is None and os.path.exists(PROBE_JSON):
        with open(PROBE_JSON, "r", encoding="utf-8") as f:
            probe = json.load(f)
    probe = probe or {}

    if summary is None:
        import bc_runner
        summary = bc_runner.aggregate(results)

    st = build_node_stats(results)
    ran = bool(results)
    P = []
    A = P.append

    # ---------------- Header ----------------
    A('<div class="wrap">')
    A('<h1>法智引擎 · Badcase 状态流转与指标看板</h1>')
    A('<div class="sub">生成时间 %s ｜ 用例 %d 条 ｜ '
      ' golden set 严格锚定已入库的 8 部法规 + 1 份案例（详见数据一致性探针）</div>'
      % (time.strftime("%Y-%m-%d %H:%M:%S"), len(results)))

    # ---------------- ① 环境体检 ----------------
    pj = probe.get("neo4j") or {}
    fb = probe.get("faiss") or {}
    dc = probe.get("data_consistency") or {}
    A('<h2>① 环境体检（探针 · 不跑图也准）</h2>')
    A('<div class="card">')
    A('<div class="badges">')
    A(f'<span class="badge {"b-ok" if pj.get("available") else "b-bad"}">'
      f'Neo4j {"可用" if pj.get("available") else "不可用"} · {_esc(pj.get("uri",""))}</span>')
    A(f'<span class="badge {"b-ok" if fb.get("ok") else "b-bad"}">'
      f'FAISS 索引 {"完整" if fb.get("ok") else "缺失"}</span>')
    A(f'<span class="badge {"b-ok" if not dc.get("missing") else "b-bad"}">'
      f'数据一致性 {"一致" if not dc.get("missing") else "不一致"}</span>')
    findings = probe.get("findings") or []
    n_p0 = sum(1 for f in findings if f["severity"] == "P0")
    A(f'<span class="badge {"b-bad" if n_p0 else "b-ok"}">探针结论 P0 {n_p0} 条</span>')
    A('</div>')

    if not pj.get("available"):
        A('<div class="warnbox"><b>⚠️ Neo4j 未启动 —— 本轮所有检索指标不可用。</b><br>'
          '检索子图两个通道都依赖 Neo4j：通道 1a 直接跑 Cypher，通道 1b 用 FAISS 找到'
          '相似实体名后<b>反向查 Neo4j 取真实条文</b>。两处都在 <span class="mono">try/except</span>'
          ' 里 catch 后 <span class="mono">print WARNING; return []</span>，'
          '<b>state 中不留任何"通道故障"标记</b>。因此下游拿到的是"空结果"而不是"错误"，'
          '质量门会把它当成"检索质量不足"回边重试 3 次，最后强制放行输出"未检索到相关依据"。<br>'
          '<b>结论：本轮 P/R/MRR 全部为 0 属于环境故障，不代表检索算法能力。'
          '请在启动 Neo4j 后重跑。</b></div>')

    # FAISS 实体规模
    if fb.get("sources"):
        A('<h3>FAISS 索引实体规模</h3><table><tr><th>知识源</th><th class="c">实体数</th>'
          '<th class="c">平均长度</th><th class="c">最长</th><th>实体名样例</th></tr>')
        for s, v in fb["sources"].items():
            A(f'<tr><td class="mono">{_esc(s)}</td><td class="c">{v.get("entity_count")}</td>'
              f'<td class="c">{v.get("avg_len")} 字</td><td class="c">{v.get("max_len")} 字</td>'
              f'<td class="mut mono">{_esc(" / ".join(v.get("samples") or [])[:150])}</td></tr>')
        A('</table>')

    # 数据一致性
    if dc:
        A('<h3>数据一致性（声明入库 vs 实际抽取）</h3>')
        if dc.get("missing") or dc.get("extra"):
            A('<div class="warnbox"><b>声明文件与实际入库文件不一致：</b><br>')
            if dc.get("missing"):
                A(f'· 声明了但<b>未入库</b>：'
                  f'<span class="mono">{_esc(", ".join(dc["missing"]))}</span><br>')
            if dc.get("extra"):
                A(f'· <b>实际入库</b>但未声明：'
                  f'<span class="mono">{_esc(", ".join(dc["extra"]))}</span><br>')
            A('→ 以<b>未入库</b>文件构造 golden set 会得到 100% 假阴性；'
              'BC-05 已按此结论调整为<b>负样本</b>用例（检索就该为空）。</div>')
        A('<table><tr><th>声明文件</th><th class="c">在库</th>'
          '<th>声明文件</th><th class="c">在库</th></tr>')
        decl = dc.get("declared") or []
        act = set(dc.get("actual") or [])
        for i in range(0, len(decl), 2):
            A('<tr>')
            for j in range(2):
                if i + j < len(decl):
                    f_ = decl[i + j]
                    ok = f_ in act
                    A(f'<td class="mono">{_esc(f_)}</td><td class="c">'
                      f'{"✅" if ok else "❌"}</td>')
                else:
                    A('<td></td><td></td>')
            A('</tr>')
        A('</table>')
    A('</div>')

    # ---------------- ② 指标总览 ----------------
    A('<h2>② 指标总览</h2>')
    s = summary
    lat = s.get("latency") or {}
    tk = s.get("tokens") or {}
    A('<div class="card"><h3>业务指标</h3><div class="grid">')
    A(_kpi("任务成功率(执行)", f'{_pctf(s.get("exec_success_rate"))}', "%",
           "v-good" if (s.get("exec_success_rate") or 0) >= 0.9 else "v-bad",
           f'{s.get("exec_success")}/{s.get("total_cases")}'))
    A(_kpi("业务成功率", f'{_pctf(s.get("biz_success_rate"))}', "%",
           "v-good" if (s.get("biz_success_rate") or 0) >= 0.8 else
           ("v-warn" if (s.get("biz_success_rate") or 0) >= 0.5 else "v-bad"),
           f'{s.get("biz_success")}/{s.get("total_cases")}'))
    A(_kpi("状态流转正确率", f'{_pctf(s.get("route_ok_rate"))}', "%",
           "v-good" if (s.get("route_ok_rate") or 0) >= 0.9 else "v-bad"))
    A(_kpi("人工修改率(严格)", f'{_pctf(s.get("manual_edit_rate_strict"))}', "%",
           "v-good" if (s.get("manual_edit_rate_strict") or 1) <= 0.2 else "v-bad"))
    A(_kpi("人工修改率(代理)", f'{_pctf(s.get("manual_edit_rate"))}', "%",
           "v-warn" if (s.get("manual_edit_rate") or 0) > 0.3 else ""))
    A(_kpi("零引用用例", s.get("empty_retrieval_cases", 0), "条",
           "v-bad" if s.get("empty_retrieval_cases") else "v-good"))
    A(_kpi("触发重试用例", s.get("retry_cases", 0), "条",
           "v-bad" if s.get("retry_cases") else "v-good",
           f'平均 {_num(s.get("avg_retry_count"), 2)} 次'))
    A(_kpi("平均质量分", _num(s.get("avg_quality_score"), 1), "",
           "v-bad" if (s.get("avg_quality_score") or 100) < 50 else "v-good"))
    A('</div></div>')

    A('<div class="card"><h3>技术指标</h3><div class="grid">')
    A(_kpi("检索精确率 P@5", _num(s.get("avg_precision")), "",
           "v-good" if (s.get("avg_precision") or 0) >= 0.6 else "v-bad",
           f'{s.get("retrieval_cases")} 条适用'))
    A(_kpi("检索召回率 R@10", _num(s.get("avg_recall")), "",
           "v-good" if (s.get("avg_recall") or 0) >= 0.6 else "v-bad"))
    A(_kpi("MRR@10", _num(s.get("avg_mrr")), "",
           "v-good" if (s.get("avg_mrr") or 0) >= 0.5 else "v-bad"))
    A(_kpi("Hit@1", _num(s.get("hit1_rate")), ""))
    A(_kpi("Hit@5", _num(s.get("hit5_rate")), ""))
    A(_kpi("响应 P50", lat.get("p50") if lat.get("p50") is not None else "-", "s",
           "v-warn" if (lat.get("p50") or 0) > 30 else "v-good"))
    A(_kpi("响应 P95", lat.get("p95") if lat.get("p95") is not None else "-", "s",
           "v-bad" if (lat.get("p95") or 0) > 60 else ""))
    A(_kpi("响应 Max", lat.get("max") if lat.get("max") is not None else "-", "s"))
    A(_kpi("LLM 调用", tk.get("llm_calls", 0), "次"))
    A(_kpi("Token 总量", f'{tk.get("total", 0):,}', "",
           "", f'in {tk.get("prompt", 0):,} / out {tk.get("completion", 0):,}'))
    A(_kpi("估算成本", f'¥{s.get("cost_total", 0)}', "",
           "", "按 ¥2/M in · ¥6/M out"))
    A('</div></div>')

    if not ran:
        A('<div class="warnbox"><b>尚未执行图测试</b> —— 上方技术指标为空。'
          '运行 <span class="mono">python bc_runner.py</span> 后重新生成本报告即可填充。'
          '下方流转图与证据层使用探针数据，现已可用。</div>')

    # ---------------- ③ 状态流转图 ----------------
    A('<h2>③ 状态流转图（节点着色 = 执行健康度）</h2>')
    A('<div class="legend">'
      '<span><i style="background:#e8f5e9;border-color:#43a047"></i>正常执行</span>'
      '<span><i style="background:#fff8e1;border-color:#f9a825"></i>慢节点 ≥%ds</span>'
      '<span><i style="background:#ffe0e0;border-color:#e53935"></i>瓶颈 ≥%ds</span>'
      '<span><i style="background:#ffcdd2;border-color:#b71c1c"></i>出错</span>'
      '<span><i style="background:#fafafa;border-color:#d0d5dd"></i>本次未执行</span>'
      '<span><i style="background:#fff;border-color:#7c4dff;border-style:dashed"></i>'
      '守卫/条件分支节点</span>'
      '<span class="mut">节点左下角=执行次数，右下角=平均耗时</span>'
      '</div>' % (int(SLOW_SEC), int(BOTTLENECK_SEC)))

    for panel in PANELS:
        svg, w, h = render_panel(panel, st)
        A('<div class="card"><h3>%s</h3>%s</div>' % (_esc(panel["title"]), svg))

    # 节点耗时 Top
    top_nodes = [v for v in st.values() if v["runs"] > 0]
    top_nodes.sort(key=lambda x: -x["total"])
    if top_nodes:
        mx = max(x["total"] for x in top_nodes) or 1
        A('<div class="card"><h3>节点累计耗时 Top 12（定位链路瓶颈）</h3>'
          '<table><tr><th>节点</th><th>所属子图</th><th class="c">执行次数</th>'
          '<th class="c">累计(s)</th><th class="c">平均(s)</th><th class="c">最大(s)</th>'
          '<th style="width:190px">耗时占比</th><th class="c">出错</th></tr>')
        for v in top_nodes[:12]:
            cls = "bad" if v["max"] >= BOTTLENECK_SEC else (
                "warn" if v["max"] >= SLOW_SEC else "")
            A(f'<tr><td class="mono">{_esc(v.get("node",""))}</td>'
              f'<td class="mut">{_esc(v.get("subgraph",""))}</td>'
              f'<td class="c">{v["runs"]}</td><td class="c">{v["total"]}</td>'
              f'<td class="c">{v["avg"]}</td><td class="c">{v["max"]}</td>'
              f'<td><div class="bar"><i class="{cls}" style="width:'
              f'{100*v["total"]/mx:.1f}%"></i></div></td>'
              f'<td class="c">{"❌ "+str(v["errors"]) if v["errors"] else "—"}</td></tr>')
        A('</table></div>')

    # ---------------- ④ 用例矩阵 ----------------
    A('<h2>④ Badcase 用例矩阵</h2>')
    A('<div class="card"><table>'
      '<tr><th>ID</th><th>严重级</th><th>任务</th><th>场景</th>'
      '<th class="c">执行</th><th class="c">业务</th><th class="c">路由</th>'
      '<th class="c">延迟(s)</th><th class="c">引用</th><th class="c">重试</th>'
      '<th class="c">P@5</th><th class="c">R@10</th><th class="c">MRR</th>'
      '<th class="c">人工修改</th></tr>')
    for r in results:
        rm = r.get("retrieval") or {}
        A('<tr>'
          f'<td class="mono"><b>{_esc(r["id"])}</b></td>'
          f'<td><span class="sev s-{_esc(r.get("severity",""))}">'
          f'{_esc(r.get("severity",""))}</span></td>'
          f'<td class="mut">{_esc(r.get("task_name") or r.get("task",""))}</td>'
          f'<td>{_esc(r.get("title",""))}</td>'
          f'<td class="c">{"✅" if r.get("exec_success") else "❌"}</td>'
          f'<td class="c">{"✅" if r.get("biz_success") else "❌"}</td>'
          f'<td class="c">{"✅" if r.get("route_ok") else "❌"}</td>'
          f'<td class="c">{r.get("latency_sec")}</td>'
          f'<td class="c">{r.get("citation_count")}</td>'
          f'<td class="c">{r.get("quality_retry_count")}</td>'
          f'<td class="c">{_num(rm.get("precision")) if rm.get("applicable") else "—"}</td>'
          f'<td class="c">{_num(rm.get("recall")) if rm.get("applicable") else "—"}</td>'
          f'<td class="c">{_num(rm.get("mrr")) if rm.get("applicable") else "—"}</td>'
          f'<td class="c">{"<b>是</b>" if r.get("needs_manual_edit_strict") else ("建议" if r.get("needs_manual_edit") else "否")}</td>'
          '</tr>')
    A('</table></div>')

    # ---------------- ⑤ 逐用例详情 ----------------
    A('<h2>⑤ 逐用例详情（轨迹 · 归因 · 证据）</h2>')
    for r in results:
        bid = r["id"]
        ok = r.get("biz_success")
        A('<div class="card"><details%s>' % (" open" if not ok else ""))
        A('<summary>%s · %s · [%s] %s &nbsp; '
          '<span class="st %s">%s</span> &nbsp; '
          '<span class="mut">%.2fs · 引用 %s · 重试 %s · LLM %s · ¥%s</span></summary>'
          % (_esc(bid), _esc(r.get("task_name") or r.get("task", "")),
             _esc(r.get("severity", "")), _esc(r.get("title", "")),
             "st-pass" if ok else "st-fail", "业务通过" if ok else "业务失败",
             r.get("latency_sec") or 0, r.get("citation_count"),
             r.get("quality_retry_count"), r.get("llm_calls"),
             r.get("cost")))
        A('<div class="body">')

        # 目标与假设
        A(f'<h3>针对的节点 / 状态</h3><ul>')
        for f_ in (r.get("focus") or []):
            A(f'<li class="mono">{_esc(f_)}</li>')
        A('</ul>')
        if r.get("hypothesis"):
            A(f'<div class="note"><b>预判：</b>{_esc(r["hypothesis"])}</div>')

        # 实际轨迹
        A('<h3>实际状态流转轨迹</h3><div class="track">')
        nt = r.get("node_trace") or []
        for i, nd in enumerate(nt):
            dur = nd.get("duration_sec") or 0
            cls = ("err" if nd.get("error") else
                   "bottleneck" if dur >= BOTTLENECK_SEC else
                   "slow" if dur >= SLOW_SEC else "ok")
            if i:
                A('<span class="arrow">→</span>')
            A(f'<div class="tn {cls}" title="{_esc(nd.get("full_name",""))}">'
              f'<b>{_esc(nd.get("name",""))}</b>'
              f'<span>{dur}s</span></div>')
        A('</div>')
        if r.get("full_route"):
            A('<pre class="mono">' + _esc("\n".join(r["full_route"])) + '</pre>')

        # 路由/状态/缺陷
        if r.get("route_detail"):
            A(f'<p><b>路径断言：</b>'
              f'<span class="{"st st-pass" if r.get("route_ok") else "st st-fail"}">'
              f'{"通过" if r.get("route_ok") else "失败"}</span> '
              f'<span class="mut">{_esc(r["route_detail"])}</span></p>')
        if r.get("state_failures"):
            A('<h3>状态字段断言失败</h3><table><tr><th>字段</th><th>断言</th>'
              '<th>期望</th><th>实际</th></tr>')
            for f_ in r["state_failures"]:
                A(f'<tr><td class="mono">{_esc(f_.get("field"))}</td>'
                  f'<td>{_esc(f_.get("op"))}</td>'
                  f'<td>{_esc(f_.get("expected"))}</td>'
                  f'<td class="mono">{_esc(f_.get("actual"))}</td></tr>')
            A('</table>')
        if r.get("defects"):
            A('<h3>质量缺陷</h3><table><tr><th>规则</th><th>级别</th><th>详情</th></tr>')
            for d in r["defects"]:
                A(f'<tr><td class="mono">{_esc(d.get("rule"))}</td>'
                  f'<td>{_esc(d.get("severity"))}</td>'
                  f'<td>{_esc(d.get("detail"))}</td></tr>')
            A('</table>')
        if r.get("causes"):
            A('<h3>失败归因（定位到节点/函数）</h3><table><tr><th>节点</th><th>文件</th>'
              '<th>函数</th><th>原因</th><th>如何确认</th></tr>')
            for c in r["causes"]:
                A(f'<tr><td class="mono"><b>{_esc(c.get("node"))}</b></td>'
                  f'<td class="mono">{_esc(c.get("file"))}</td>'
                  f'<td class="mono">{_esc(c.get("func"))}</td>'
                  f'<td>{_esc(c.get("reason"))}</td>'
                  f'<td class="mut">{_esc(c.get("how_to_confirm"))}</td></tr>')
            A('</table>')

        # 挂载感知可达性
        kw = r.get("keyword_reach") or []
        if kw:
            A('<h3>关键词可达性（挂载感知）</h3>'
              '<table><tr><th>关键词</th><th class="c">全库实体命中</th>'
              '<th class="c">已挂载源内命中</th><th class="c">孤儿命中<br>'
              '<span class="mut">(落在未挂载源)</span></th><th>命中分布</th></tr>')
            for k in kw:
                hm = k.get("hit_mounted", 0)
                orp = k.get("orphan_hits", 0) or 0
                hit_cls = "hit0" if hm == 0 else "hit1"
                orp_cell = (f'<span class="orphan">{orp}</span>' if orp else "0")
                A(f'<tr><td class="mono">{_esc(k.get("kw",""))}</td>'
                  f'<td class="c">{k.get("hit_all")}</td>'
                  f'<td class="c"><span class="{hit_cls}">{hm}</span></td>'
                  f'<td class="c">{orp_cell}</td>'
                  f'<td class="mut mono">'
                  f'{_esc(json.dumps(k.get("per_source") or {}, ensure_ascii=False))}</td>'
                  f'</tr>')
            A('</table>')
            A('<p class="mut">挂载源：<span class="mono">%s</span> ｜ '
              '有效命中 <b>%s</b> ｜ 孤儿命中 <b>%s</b>'
              '<br>"孤儿命中"= 该关键词明明在库里有实体，但因为该知识源<b>未被挂载</b>，'
              '本次检索永远查不到 —— 这是"看起来库里有、实际查不到"的根因。</p>'
              % (_esc(json.dumps(r.get("mounted_sources_probe") or [], ensure_ascii=False)),
                 r.get("mount_effective_hits"), r.get("mount_orphan_hits")))

        # 引用与输出
        if r.get("citations"):
            A('<h3>Top 引用</h3><table><tr><th>#</th><th>title</th><th>条号</th>'
              '<th>law_name</th><th>来源</th><th class="c">分级</th>'
              '<th class="c">分</th><th>内容</th></tr>')
            for i, c in enumerate(r["citations"][:8], 1):
                ln = c.get("law_name") or ""
                A(f'<tr><td class="c">{i}</td><td>{_esc(c.get("title",""))}</td>'
                  f'<td class="mono">{_esc(c.get("article_no",""))}</td>'
                  f'<td>{"<span class=hit0>缺失</span>" if not ln else _esc(ln)}</td>'
                  f'<td class="mono">{_esc(c.get("source",""))}</td>'
                  f'<td class="c">{_esc(c.get("grade",""))}</td>'
                  f'<td class="c">{c.get("final_score")}</td>'
                  f'<td class="mut">{_esc((c.get("content") or "")[:80])}</td></tr>')
            A('</table>')
        if r.get("output_preview"):
            A('<h3>输出预览</h3><pre>' + _esc(r["output_preview"]) + '</pre>')
        if r.get("error"):
            A('<h3>异常</h3><pre class="mono">' + _esc(str(r["error"])[:1500]) + '</pre>')
        A('</div></details></div>')

    # ---------------- ⑥ 证据层 ----------------
    A('<h2>⑥ 证据层</h2>')

    kr = probe.get("keyword_reachability") or {}
    if kr.get("rows"):
        A('<div class="card"><h3>关键词 × 知识源 实体命中热力表</h3>'
          '<p class="mut">召回硬约束 <span class="mono">e.name CONTAINS kw</span>：'
          '关键词必须<b>逐字</b>是实体名的子串，ENTITY_MATCH(100/90/60 分) 才能生效。'
          '命中 0 表示该关键词在图谱通道上完全不可达，只能靠 FULLTEXT(固定 40 分) 兜底。</p>'
          '<table><tr><th>关键词</th><th>说明</th><th class="c">合计</th>')
        srcs = ["laws", "regulations", "interpretations", "industry_sources", "cases"]
        for s in srcs:
            A(f'<th class="c">{s}</th>')
        A('</tr>')
        for row in kr["rows"]:
            A(f'<tr><td class="mono"><b>{_esc(row["keyword"])}</b></td>'
              f'<td class="mut">{_esc(row.get("note",""))}</td>'
              f'<td class="c"><span class="{"hit0" if row["total_hit"]==0 else "hit1"}">'
              f'{row["total_hit"]}</span></td>')
            for s in srcs:
                n = (row.get("per_source") or {}).get(s, {}).get("hit", 0)
                bg = ("background:#e8f5e9" if n >= 5 else
                      "background:#fff8e1" if n >= 1 else "background:#fafafa")
                A(f'<td class="c" style="{bg}">{n if n else "·"}</td>')
            A('</tr>')
        A('</table>')
        A('<p class="mut">%s</p>' % _esc(kr.get("conclusion", "")))
        A('</div>')

    mar = probe.get("mount_aware_reachability") or []
    if mar:
        A('<div class="card"><h3>挂载感知可达性（最关键的诊断）</h3>'
          '<p class="mut">左表按用例汇总：有效命中 = 关键词在<b>已挂载源</b>上的实体命中数；'
          '孤儿命中 = 命中全部落在<b>未挂载源</b>上的部分。</p>'
          '<table><tr><th>用例</th><th>任务</th><th>已挂载源</th>'
          '<th class="c">有效命中</th><th class="c">孤儿命中</th><th>判定</th></tr>')
        for c in mar:
            eff, orp = c.get("effective_hits", 0), c.get("orphan_hits", 0)
            verdict = ("<span class='hit0'>图谱实体通道完全不可达</span>" if eff == 0
                       else "<span class='orphan'>部分命中丢失</span>" if orp > 0
                       else "<span class='hit1'>可达</span>")
            A(f'<tr><td class="mono"><b>{_esc(c["id"])}</b></td>'
              f'<td class="mut">{_esc(c["task"])}</td>'
              f'<td class="mono" style="font-size:11px">'
              f'{_esc(json.dumps(c.get("mounted_sources") or [], ensure_ascii=False))}</td>'
              f'<td class="c"><b>{eff}</b></td><td class="c">{orp}</td>'
              f'<td>{verdict}</td></tr>')
        A('</table>')
        A('<div class="warnbox"><b>核心结论：</b>BC-06（房屋租赁合同审核）的 4 个核心关键词'
          '「房屋租赁 / 租赁期限 / 违约金 / 押金」在库内<b>总共 10 个实体命中，'
          '全部落在 industry_sources</b>；而 '
          '<span class="mono">KEYWORD_RULES</span> 的 industry 触发词只有'
          '「建设工程 / 工程款 / 承包人 / 施工 / …」「商品房买卖 / 预售 / 房地产开发 / '
          '容积率 / 住建部 / 物业管理 / …」「金融借款 / 放款 / 催收 / …」三组，'
          '<b>不含"房屋租赁"</b> → industry_sources 未挂载 → 该合同审核中'
          '《城市房屋租赁管理办法》第四条（租期≤20 年）在结构上<b>永远不可能被召回</b>。'
          '<br>反观 BC-10 因为句中含有"建设工程"四个字而意外挂载了 industry_sources，'
          '"租赁期限"才拿到 1 个有效命中 —— 这就是典型的<b>借光式挂载</b>：'
          '能否召回取决于同一句话里有没有碰巧出现无关的行业关键词。</div>')
        A('</div>')

    if findings:
        A('<div class="card"><h3>探针结论（可直接转优化项）</h3><table>'
          '<tr><th>级别</th><th>ID</th><th>节点</th><th>问题</th><th>证据</th>'
          '<th>影响</th></tr>')
        for f in findings:
            A(f'<tr><td><span class="sev s-{_esc(f["severity"])}">'
              f'{_esc(f["severity"])}</span></td>'
              f'<td class="mono">{_esc(f["id"])}</td>'
              f'<td class="mono">{_esc(f.get("node",""))}</td>'
              f'<td><b>{_esc(f["title"])}</b></td>'
              f'<td class="mut mono" style="font-size:11px">'
              f'{_esc(str(f.get("evidence",""))[:300])}</td>'
              f'<td class="mut">{_esc(str(f.get("detail",""))[:400])}</td></tr>')
        A('</table></div>')

    # 常量冲突
    consts = probe.get("constants") or {}
    if consts.get("constants"):
        A('<div class="card"><h3>跨模块常量一致性</h3><table>'
          '<tr><th>常量</th><th class="c">值</th></tr>')
        for k, v in consts["constants"].items():
            A(f'<tr><td class="mono">{_esc(k)}</td><td class="c">{_esc(v)}</td></tr>')
        A('</table></div>')

    A('</div>')

    doc = (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>法智引擎 · Badcase 状态流转与指标看板</title>'
        '<style>%s</style></head><body>%s</body></html>'
        % (CSS, "\n".join(P))
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)
    return out_path


def main():
    print("生成报告 ...")
    p = build_report()
    print("已生成:", p)


if __name__ == "__main__":
    main()
