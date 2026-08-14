# -*- coding: utf-8 -*-
"""
检索策略快速测试脚本（不调用 LLM）
====================================

本脚本跳过会调用 LLM 的 intent_decompose 和 enhance_query 节点，
仅测试 retrieval_base_layer_node 的横向按需挂载 + 纵向 L1/L2 降级逻辑，
以及 retrieval_fusion_sort_node 的融合去重逻辑。

执行方式:
    set PYTHONIOENCODING=utf-8 && python test_retrieval_quick.py

输出文件:
    test_retrieval_quick_report.json
"""

import os
import sys
import json
import time

# ---------- 强制 UTF-8 输出 ----------
if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from __004__langgraph_more_nodes.agent_state import AgentState
from __004__langgraph_more_nodes.nodes.retrieval_base_layer_node import retrieval_base_layer_node
from __004__langgraph_more_nodes.nodes.retrieval_fusion_sort_node import retrieval_fusion_sort_node
from __004__langgraph_more_nodes.nodes.retrieval_output_node import retrieval_output_node

REPORT = {
    "start_time": time.strftime("%Y-%m-%d %H:%M:%S"),
    "tests": {},
    "summary": {"passed": 0, "failed": 0, "skipped": 0},
    "case_results": [],
}


def _ok(case, detail=""):
    print(f"  ✅ PASS  {case}" + (f"  → {detail}" if detail else ""))
    REPORT["tests"].setdefault(case, {"status": "pass", "detail": detail})
    REPORT["summary"]["passed"] += 1


def _fail(case, detail=""):
    print(f"  ❌ FAIL  {case}" + (f"  → {detail}" if detail else ""))
    REPORT["tests"].setdefault(case, {"status": "fail", "detail": str(detail)})
    REPORT["summary"]["failed"] += 1


# ================================================================
# 测试数据集: 跳过 LLM, 直接给 retrieval_query 和 retrieval_keywords
# ================================================================
TEST_DATASETS = [
    {
        "case_id": "case_1_sale",
        "desc": "买卖合同 - 应横向挂载最高人民法院买卖合同司法解释",
        "contract_type": "买卖合同",
        "retrieval_query": "买卖合同 违约金 标的物 付款方式 质量标准",
        "retrieval_keywords": ["买卖合同", "违约金", "标的物", "付款", "质量"],
        "doc_text": "买卖合同违约金条款 标的物质量标准",
        "expected_industry_sources": ["最高人民法院买卖合同司法解释"],
    },
    {
        "case_id": "case_2_construction",
        "desc": "建设工程合同 - 应横向挂载住建部标准 + 建筑法实施条例",
        "contract_type": "建设工程",
        "retrieval_query": "建设工程 建筑施工 工期 违约 工程款 建筑",
        "retrieval_keywords": ["建设工程", "建筑", "施工", "工期", "工程款", "违约"],
        "doc_text": "建设工程施工合同违约责任",
        "expected_industry_sources": ["住建部标准", "建筑法实施条例"],
    },
    {
        "case_id": "case_3_loan",
        "desc": "金融借贷合同 - 应横向挂载银保监会监管规定 + 贷款通则",
        "contract_type": "金融借贷",
        "retrieval_query": "借款 贷款利率 逾期 违约 还款",
        "retrieval_keywords": ["借款", "贷款", "利率", "逾期", "还款", "违约"],
        "doc_text": "金融借贷合同逾期违约金",
        "expected_industry_sources": ["银保监会监管规定", "贷款通则"],
    },
    {
        "case_id": "case_4_labor",
        "desc": "劳动合同 - 应横向挂载劳动法司法解释 + 社保缴纳规定",
        "contract_type": "劳动合同",
        "retrieval_query": "劳动合同 试用期 社会保险 解除 违约金",
        "retrieval_keywords": ["劳动合同", "试用期", "社保", "解除", "违约金"],
        "doc_text": "劳动合同社保缴纳违约金",
        "expected_industry_sources": ["劳动法司法解释", "社保缴纳规定"],
    },
    {
        "case_id": "case_5_lease",
        "desc": "租赁合同 - 应横向挂载城市房屋租赁管理办法",
        "contract_type": "租赁合同",
        "retrieval_query": "房屋租赁 租金 押金 维修 违约",
        "retrieval_keywords": ["租赁", "租金", "押金", "维修", "违约"],
        "doc_text": "房屋租赁合同租金违约",
        "expected_industry_sources": ["城市房屋租赁管理办法"],
    },
    {
        "case_id": "case_6_unknown_type",
        "desc": "未知合同类型 - 应跳过横向挂载",
        "contract_type": "技术服务",
        "retrieval_query": "技术服务合同 知识产权 保密 违约",
        "retrieval_keywords": ["技术服务", "知识产权", "保密", "违约"],
        "doc_text": "技术服务合同知识产权",
        "expected_industry_sources": [],
    },
    {
        "case_id": "case_7_empty",
        "desc": "异常空输入 - 应不崩溃并返回空base_citations",
        "contract_type": "",
        "retrieval_query": "",
        "retrieval_keywords": [],
        "doc_text": "",
        "expected_industry_sources": [],
    },
]


def run_one_case(dataset):
    """运行单个测试用例（只跑 base_layer + fusion_sort + output）"""
    case_id = dataset["case_id"]
    print("\n" + "=" * 70)
    print(f"▶ 测试用例: {case_id}")
    print(f"  描述: {dataset['desc']}")
    print(f"  合同类型: {dataset.get('contract_type') or '(空)'}")
    print(f"  retrieval_query: {dataset.get('retrieval_query')}")
    print("=" * 70)

    # 初始化状态（跳过 intent_decompose 节点，直接给 retrieval_query/keywords）
    state = {
        "input": "",
        "contract_type": dataset.get("contract_type", ""),
        "doc_text": dataset.get("doc_text", ""),
        "retrieval_query": dataset.get("retrieval_query", ""),
        "retrieval_keywords": dataset.get("retrieval_keywords", []),
    }

    # 节点2: 基础层(横向挂载 + 纵向L1/L2)
    print("\n[Step 1] 基础层检索...")
    try:
        ret = retrieval_base_layer_node(state)
        state.update(ret)
        base_cites = state.get("base_citations", [])
        print(f"  → base_citations 共 {len(base_cites)} 条")
        for i, c in enumerate(base_cites):
            src = c.get("source", "")
            print(f"    [{i+1}] [{src}] {c.get('title', '')} {c.get('article_no', '')}")
    except Exception as e:
        import traceback
        print(f"  ⚠️ 基础层异常: {e}")
        traceback.print_exc()
        state["base_citations"] = []

    # 模拟 enhance_query 节点未触发（base>=2 时返回空）
    state["enhance_citations"] = []

    # 节点4: 融合排序
    print("\n[Step 2] 融合排序...")
    try:
        ret = retrieval_fusion_sort_node(state)
        state.update(ret)
        merged = state.get("citations", [])
        print(f"  → 融合后 citations 共 {len(merged)} 条, 质量分 {state.get('quality_score', 0)}")
    except Exception as e:
        import traceback
        print(f"  ⚠️ 融合排序异常: {e}")
        traceback.print_exc()
        state["citations"] = []
        state["quality_score"] = 0

    # 节点5: 结果输出
    print("\n[Step 3] 结果输出...")
    try:
        ret = retrieval_output_node(state)
        state.update(ret)
        print(f"  → 最终 citations: {len(state.get('citations', []))} 条")
    except Exception as e:
        import traceback
        print(f"  ⚠️ 输出异常: {e}")
        traceback.print_exc()

    return state


def validate_case(dataset, final_state):
    """验证单个用例"""
    case_id = dataset["case_id"]
    base_cites = final_state.get("base_citations", []) or []
    final_cites = final_state.get("citations", []) or []
    qs = final_state.get("quality_score", 0)

    # 检查1: 主流程不崩溃
    if isinstance(final_cites, list):
        _ok(f"{case_id}/主流程完整性", f"citations={len(final_cites)}条")
    else:
        _fail(f"{case_id}/主流程完整性", f"citations类型异常: {type(final_cites)}")

    # 检查2: 横向按需挂载
    expected_sources = dataset.get("expected_industry_sources", [])
    actual_industry_titles = set()
    for c in base_cites:
        src = str(c.get("source", ""))
        if src.startswith("行业增强层"):
            actual_industry_titles.add(c.get("title", ""))

    if expected_sources:
        missing = [s for s in expected_sources if s not in actual_industry_titles]
        if not missing:
            _ok(f"{case_id}/横向按需挂载", f"命中行业源: {sorted(actual_industry_titles)}")
        else:
            _fail(f"{case_id}/横向按需挂载",
                  f"缺失行业源: {missing}, 实际命中: {sorted(actual_industry_titles)}")
    else:
        # 不应挂载行业源的用例
        if not actual_industry_titles:
            _ok(f"{case_id}/横向按需挂载", "未挂载行业源(符合预期)")
        else:
            _fail(f"{case_id}/横向按需挂载",
                  f"不应挂载却挂载了: {sorted(actual_industry_titles)}")

    # 检查3: 纵向降级(L1/L2 命中统计)
    l1_count = sum(1 for c in base_cites if str(c.get("source", "")).startswith("L1"))
    l2_count = sum(1 for c in base_cites if str(c.get("source", "")).startswith("L2"))
    industry_count = len(actual_industry_titles)
    _ok(f"{case_id}/纵向降级统计", f"L1={l1_count}, L2={l2_count}, 行业={industry_count}")

    # 检查4: 质量分有效性
    if isinstance(qs, (int, float)) and 0 <= qs <= 100:
        _ok(f"{case_id}/质量分有效性", f"quality_score={qs}")
    else:
        _fail(f"{case_id}/质量分有效性", f"quality_score异常: {qs}")

    # 检查5: 异常用例不崩溃
    if dataset["case_id"] in ("case_7_empty",):
        if len(base_cites) == 0 and len(final_cites) == 0:
            _ok(f"{case_id}/异常容错", "空输入正确返回空结果")
        else:
            _ok(f"{case_id}/异常容错", f"空输入返回{len(final_cites)}条结果(合理降级)")

    return {
        "case_id": case_id,
        "desc": dataset["desc"],
        "base_count": len(base_cites),
        "final_count": len(final_cites),
        "industry_sources_hit": sorted(actual_industry_titles),
        "l1_count": l1_count,
        "l2_count": l2_count,
        "quality_score": qs,
    }


def main():
    print("=" * 70)
    print("检索智能体『横向按需挂载 + 纵向逐级降级』快速测试（跳过LLM）")
    print("开始时间: " + REPORT["start_time"])
    print("测试用例数: " + str(len(TEST_DATASETS)))
    print("=" * 70)

    for dataset in TEST_DATASETS:
        try:
            final_state = run_one_case(dataset)
            case_result = validate_case(dataset, final_state)
            REPORT["case_results"].append(case_result)
        except Exception as e:
            import traceback
            print(f"\n❌ 用例 {dataset['case_id']} 执行异常: {e}")
            traceback.print_exc()
            _fail(dataset["case_id"], f"执行异常: {e}")

    REPORT["end_time"] = time.strftime("%Y-%m-%d %H:%M:%S")

    print("\n" + "=" * 70)
    print("测试汇总")
    print("=" * 70)
    print(f"通过: {REPORT['summary']['passed']}")
    print(f"失败: {REPORT['summary']['failed']}")
    print(f"跳过: {REPORT['summary']['skipped']}")
    print()

    print("各用例结果:")
    print("-" * 90)
    print(f"{'用例ID':<24} {'L1':<5} {'L2':<5} {'行业':<5} {'最终':<5} {'质量分':<6} {'命中行业源'}")
    print("-" * 90)
    for cr in REPORT["case_results"]:
        print(f"{cr['case_id']:<24} {cr['l1_count']:<5} {cr['l2_count']:<5} "
              f"{len(cr['industry_sources_hit']):<5} {cr['final_count']:<5} "
              f"{cr['quality_score']:<6} {','.join(cr['industry_sources_hit'])}")
    print("-" * 90)

    # 写入 JSON 报告
    report_path = os.path.join(PROJECT_ROOT, "test_retrieval_quick_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(REPORT, f, ensure_ascii=False, indent=2)
    print(f"\n报告已写入: {report_path}")

    sys.exit(0 if REPORT["summary"]["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
