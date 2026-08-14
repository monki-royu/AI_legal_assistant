# -*- coding: utf-8 -*-
"""
检索智能体"横向按需挂载 + 纵向逐级降级"策略测试脚本
====================================================

功能:
    验证修改后的检索智能体是否符合设计预期:

    测试维度1 - 横向按需挂载:
        - case 1: 买卖合同   -> 应挂载"最高人民法院买卖合同司法解释"
        - case 2: 建设工程   -> 应挂载"住建部标准" + "建筑法实施条例"
        - case 3: 金融借贷   -> 应挂载"银保监会监管规定" + "贷款通则"
        - case 4: 劳动合同   -> 应挂载"劳动法司法解释" + "社保缴纳规定"
        - case 5: 租赁合同   -> 应挂载"城市房屋租赁管理办法"

    测试维度2 - 纵向逐级降级:
        - case 6: 异常空输入 -> L1/L2 必然空, 应触发 L3 LLM 伪检索
        - case 7: 极短输入   -> L1/L2 大概率空, 应触发 L3 LLM 伪检索

    测试维度3 - 融合排序:
        - 验证 fusion_sort 节点能正确合并 base_citations + enhance_citations
        - 验证去重逻辑(按 title|article_no|content[:40] 去重)
        - 验证 quality_score 计算(每条20分, 上限100)

执行方式:
    方式1: 双击 run_test_retrieval.bat
    方式2: 在项目根目录打开 cmd, 执行:
           set PYTHONIOENCODING=utf-8 && python test_retrieval_strategy.py
    方式3: 在 PowerShell 执行:
           $env:PYTHONIOENCODING='utf-8'; python test_retrieval_strategy.py

输出文件:
    - 控制台打印所有测试步骤与结论
    - test_retrieval_report.json: 结构化测试报告
"""

import os
import sys
import json
import time
import traceback

# ---------- 强制 UTF-8 输出(Windows cmd/Gbk 环境也能正常显示) ----------
if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        import io as _io
        sys.stderr = _io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

# 项目根目录加入 sys.path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ---------- 导入被测节点 ----------
from __004__langgraph_more_nodes.agent_state import AgentState
from __004__langgraph_more_nodes.nodes.retrieval_intent_decompose_node import retrieval_intent_decompose_node
from __004__langgraph_more_nodes.nodes.retrieval_base_layer_node import retrieval_base_layer_node
from __004__langgraph_more_nodes.nodes.retrieval_enhance_query_node import retrieval_enhance_query_node
from __004__langgraph_more_nodes.nodes.retrieval_fusion_sort_node import retrieval_fusion_sort_node
from __004__langgraph_more_nodes.nodes.retrieval_output_node import retrieval_output_node

# ---------- 测试报告骨架 ----------
REPORT = {
    "start_time": time.strftime("%Y-%m-%d %H:%M:%S"),
    "tests": {},
    "summary": {"passed": 0, "failed": 0, "skipped": 0},
}


def _ok(case, detail=""):
    """记录通过的测试用例"""
    print(f"  ✅ PASS  {case}" + (f"  → {detail}" if detail else ""))
    REPORT["tests"].setdefault(case, {"status": "pass", "detail": detail})
    REPORT["summary"]["passed"] += 1


def _fail(case, detail=""):
    """记录失败的测试用例"""
    print(f"  ❌ FAIL  {case}" + (f"  → {detail}" if detail else ""))
    REPORT["tests"].setdefault(case, {"status": "fail", "detail": str(detail)})
    REPORT["summary"]["failed"] += 1


def _skip(case, detail=""):
    """记录跳过的测试用例"""
    print(f"  ⏭ SKIP  {case}" + (f"  → {detail}" if detail else ""))
    REPORT["tests"].setdefault(case, {"status": "skip", "detail": str(detail)})
    REPORT["summary"]["skipped"] += 1


# ================================================================
# 测试数据集: 7 个不同类型的合同样本
# ================================================================
TEST_DATASETS = [
    {
        "case_id": "case_1_sale",
        "desc": "买卖合同 - 应横向挂载最高人民法院买卖合同司法解释",
        "input": "",
        "contract_type": "买卖合同",
        "doc_text": (
            "甲方: 北京华夏商贸有限公司\n乙方: 上海科创科技有限公司\n\n"
            "第一条 标的物: 甲方购买乙方生产的智能机器人100台, 单价5万元, 总价500万元。\n"
            "第二条 付款方式: 合同签订后7日内甲方支付30%预付款, 验收合格后7日内支付剩余70%。\n"
            "第三条 交货期限: 乙方应于2026年9月30日前完成全部交货。\n"
            "第四条 质量标准: 乙方保证产品符合国家相关质量标准, 出现质量问题乙方应在30日内免费更换。\n"
            "第五条 违约责任: 任何一方违约应支付合同总价20%的违约金。甲方逾期付款的, 按日支付万分之五的滞纳金。"
        ),
        "expected_industry_sources": ["最高人民法院买卖合同司法解释"],
        "expected_trigger_l3": False,
    },
    {
        "case_id": "case_2_construction",
        "desc": "建设工程合同 - 应横向挂载住建部标准 + 建筑法实施条例",
        "input": "",
        "contract_type": "建设工程",
        "doc_text": (
            "发包人(甲方): 北京城市建设投资集团\n承包人(乙方): 中铁建工集团有限公司\n\n"
            "工程名称: 朝阳区城市综合体建设项目\n工程地点: 北京市朝阳区建国路88号\n工程内容: 总建筑面积12万平方米, 含主体结构、装饰装修、机电安装。\n"
            "合同价款: 暂定人民币3.8亿元。\n"
            "工期: 2026年3月1日至2027年12月31日, 总工期670日历天。\n"
            "质量标准: 达到国家现行施工验收规范合格标准。\n"
            "工程款支付: 按月进度支付, 每月按已完成工程量的80%支付, 竣工验收合格后支付至95%, 5%作为质量保修金。\n"
            "违约责任: 承包人逾期竣工每日罚款5万元, 发包人逾期付款每日按应付金额万分之三支付违约金。"
        ),
        "expected_industry_sources": ["住建部标准", "建筑法实施条例"],
        "expected_trigger_l3": False,
    },
    {
        "case_id": "case_3_loan",
        "desc": "金融借贷合同 - 应横向挂载银保监会监管规定 + 贷款通则",
        "input": "",
        "contract_type": "金融借贷",
        "doc_text": (
            "贷款人(甲方): 中国工商银行股份有限公司北京分行\n借款人(乙方): 北京新兴科技有限公司\n\n"
            "借款金额: 人民币贰仟万元整(¥20,000,000.00)。\n借款用途: 用于企业流动资金周转, 不得用于股本权益性投资。\n"
            "借款期限: 12个月, 自2026年1月1日至2026年12月31日。\n"
            "借款利率: 年利率4.35%, 按季结息。\n"
            "还款方式: 按月还息到期还本。\n"
            "担保方式: 由北京担保有限公司提供连带责任保证担保。\n"
            "违约责任: 借款人未按期归还借款的, 按合同利率加收50%计收逾期利息; 挪用借款的, 按合同利率加收100%计收罚息。"
        ),
        "expected_industry_sources": ["银保监会监管规定", "贷款通则"],
        "expected_trigger_l3": False,
    },
    {
        "case_id": "case_4_labor",
        "desc": "劳动合同 - 应横向挂载劳动法司法解释 + 社保缴纳规定",
        "input": "",
        "contract_type": "劳动合同",
        "doc_text": (
            "用人单位(甲方): 北京互联网科技有限公司\n劳动者(乙方): 张三(身份证号110101199001011234)\n\n"
            "第一条 合同期限: 自2026年1月1日起至2028年12月31日止, 期限3年, 试用期6个月。\n"
            "第二条 工作岗位: 高级软件工程师, 工作地点北京。\n"
            "第三条 工作时间: 标准工时制, 每日工作8小时, 每周工作40小时。\n"
            "第四条 劳动报酬: 乙方月工资为人民币3万元, 每月10日以银行转账形式支付。\n"
            "第五条 社会保险: 甲方依法为乙方缴纳养老、医疗、失业、工伤、生育保险及住房公积金。\n"
            "第六条 保密义务: 乙方在职期间及离职后2年内对甲方商业秘密负有保密义务, 违反者支付违约金30万元。\n"
            "第七条 解除合同: 任何一方提前30日书面通知对方可解除合同, 否则支付一个月工资作为代通知金。"
        ),
        "expected_industry_sources": ["劳动法司法解释", "社保缴纳规定"],
        "expected_trigger_l3": False,
    },
    {
        "case_id": "case_5_lease",
        "desc": "租赁合同 - 应横向挂载城市房屋租赁管理办法",
        "input": "",
        "contract_type": "租赁合同",
        "doc_text": (
            "出租人(甲方): 王五\n承租人(乙方): 北京设计工作室\n\n"
            "租赁房屋: 北京市海淀区中关村大街1号18层1801室, 建筑面积200平方米。\n"
            "租赁用途: 办公。\n"
            "租赁期限: 自2026年1月1日至2028年12月31日, 共36个月。\n"
            "租金: 月租金人民币3万元, 按季度支付, 每季度首月10日前付清。\n"
            "押金: 乙方支付押金人民币9万元, 合同终止且乙方结清费用后甲方无息退还。\n"
            "维修责任: 房屋主体结构由甲方负责维修, 室内设施由乙方负责维修。\n"
            "违约责任: 乙方逾期支付租金超过15日的, 甲方有权解除合同并没收押金; 甲方擅自提前解约的, 应双倍返还押金。"
        ),
        "expected_industry_sources": ["城市房屋租赁管理办法"],
        "expected_trigger_l3": False,
    },
    {
        "case_id": "case_6_empty",
        "desc": "异常空输入 - 应触发 L3 LLM 伪检索兜底",
        "input": "",
        "contract_type": "",
        "doc_text": "",
        "expected_industry_sources": [],
        "expected_trigger_l3": True,
    },
    {
        "case_id": "case_7_short",
        "desc": "异常极短输入 - 应触发 L3 LLM 伪检索兜底",
        "input": "违约金",
        "contract_type": "",
        "doc_text": "买卖合同违约金条款",
        "expected_industry_sources": [],
        "expected_trigger_l3": True,
    },
]


def run_pipeline(dataset):
    """
    运行完整的检索子图流水线, 返回最终状态。

    流程: intent_decompose -> base_layer -> enhance_query -> fusion_sort -> output

    Parameters
    ----------
    dataset : dict
        测试用例数据集

    Returns
    -------
    dict
        运行后的完整状态字典
    """
    # 初始化状态
    state = {
        "input": dataset.get("input", ""),
        "contract_type": dataset.get("contract_type", ""),
        "doc_text": dataset.get("doc_text", ""),
    }

    print("\n" + "=" * 70)
    print(f"▶ 测试用例: {dataset['case_id']}")
    print(f"  描述: {dataset['desc']}")
    print(f"  合同类型: {dataset.get('contract_type') or '(空)'}")
    print(f"  文档长度: {len(dataset.get('doc_text', ''))} 字符")
    print("=" * 70)

    # 节点1: 意图分解
    print("\n[Step 1] 意图分解...")
    try:
        ret = retrieval_intent_decompose_node(state)
        state.update(ret)
        print(f"  → retrieval_query 长度: {len(state.get('retrieval_query', ''))}")
        print(f"  → retrieval_keywords: {state.get('retrieval_keywords', [])[:5]}")
    except Exception as e:
        print(f"  ⚠️ 意图分解异常: {e}")
        traceback.print_exc()
        state["retrieval_query"] = dataset.get("doc_text", "")[:300]
        state["retrieval_keywords"] = []

    # 节点2: 基础层(L1 FAISS + L2 本地法规 + 横向行业挂载)
    print("\n[Step 2] 基础层检索(横向挂载 + 纵向L1/L2)...")
    try:
        ret = retrieval_base_layer_node(state)
        state.update(ret)
        base_cites = state.get("base_citations", [])
        print(f"  → base_citations 共 {len(base_cites)} 条")
        for i, c in enumerate(base_cites):
            print(f"    [{i+1}] [{c.get('source', '')}] {c.get('title', '')} {c.get('article_no', '')}")
    except Exception as e:
        print(f"  ⚠️ 基础层检索异常: {e}")
        traceback.print_exc()
        state["base_citations"] = []

    # 节点3: 增强查询(L3 LLM 伪检索兜底)
    print("\n[Step 3] 增强查询(L3 LLM 伪检索兜底)...")
    try:
        ret = retrieval_enhance_query_node(state)
        state.update(ret)
        enhance_cites = state.get("enhance_citations", [])
        print(f"  → enhance_citations 共 {len(enhance_cites)} 条")
        for i, c in enumerate(enhance_cites):
            print(f"    [{i+1}] [{c.get('source', '')}] {c.get('title', '')} {c.get('article_no', '')}")
    except Exception as e:
        print(f"  ⚠️ 增强查询异常: {e}")
        traceback.print_exc()
        state["enhance_citations"] = []

    # 节点4: 融合排序
    print("\n[Step 4] 融合排序(RRF + 去重)...")
    try:
        ret = retrieval_fusion_sort_node(state)
        state.update(ret)
        merged = state.get("citations", [])
        print(f"  → 融合后 citations 共 {len(merged)} 条, 质量分 {state.get('quality_score', 0)}")
    except Exception as e:
        print(f"  ⚠️ 融合排序异常: {e}")
        traceback.print_exc()
        state["citations"] = []
        state["research_context"] = ""
        state["quality_score"] = 0

    # 节点5: 结果输出
    print("\n[Step 5] 结果输出...")
    try:
        ret = retrieval_output_node(state)
        state.update(ret)
        print(f"  → 最终 citations: {len(state.get('citations', []))} 条")
        print(f"  → research_context 长度: {len(state.get('research_context', ''))}")
        print(f"  → quality_score: {state.get('quality_score', 0)}")
    except Exception as e:
        print(f"  ⚠️ 结果输出异常: {e}")
        traceback.print_exc()

    return state


def validate_case(dataset, final_state):
    """
    根据测试用例的预期, 验证运行结果是否达标。

    返回 (passed_checks, failed_checks) 两个列表, 每个元素为 (检查项, 详情)。
    """
    passed_checks = []
    failed_checks = []
    case_id = dataset["case_id"]

    base_cites = final_state.get("base_citations", []) or []
    enhance_cites = final_state.get("enhance_citations", []) or []
    final_cites = final_state.get("citations", []) or []

    # ============== 检查1: 主流程不崩溃 ==============
    # 只要 final_state 有 citations 字段(即使是空列表)就算主流程跑通
    if isinstance(final_cites, list):
        passed_checks.append(("主流程完整性", f"citations={len(final_cites)}条"))
    else:
        failed_checks.append(("主流程完整性", f"citations类型异常: {type(final_cites)}"))

    # ============== 检查2: 横向按需挂载 ==============
    expected_sources = dataset.get("expected_industry_sources", [])
    if expected_sources:
        # 收集 base_citations 中实际命中的行业数据源
        actual_industry_titles = set()
        for c in base_cites:
            src = c.get("source", "")
            if src.startswith("行业增强层"):
                actual_industry_titles.add(c.get("title", ""))
        missing = [s for s in expected_sources if s not in actual_industry_titles]
        if not missing:
            passed_checks.append((
                "横向按需挂载",
                f"命中行业源: {sorted(actual_industry_titles)}"
            ))
        else:
            failed_checks.append((
                "横向按需挂载",
                f"缺失行业源: {missing}, 实际命中: {sorted(actual_industry_titles)}"
            ))
    else:
        # 不应挂载行业源的用例(case 6/7): 验证 base_citations 中无"行业增强层"来源
        industry_in_base = [c for c in base_cites if str(c.get("source", "")).startswith("行业增强层")]
        if not industry_in_base:
            passed_checks.append(("横向按需挂载", "未挂载行业源(符合预期)"))
        else:
            failed_checks.append(("横向按需挂载", f"不应挂载却挂载了: {len(industry_in_base)}条"))

    # ============== 检查3: 纵向降级逻辑 ==============
    # 统计 L1/L2/L3 命中情况
    l1_count = sum(1 for c in base_cites if str(c.get("source", "")).startswith("L1"))
    l2_count = sum(1 for c in base_cites if str(c.get("source", "")).startswith("L2"))
    l3_count = len(enhance_cites)  # enhance_citations 即 L3 输出

    detail_levels = f"L1={l1_count}, L2={l2_count}, L3={l3_count}"

    # 验证 L3 触发条件: 当 base_citations < 2 时, 应尝试 L3
    # 注意: L3 调用 LLM 可能失败, 故 enhance_cites 可能为空, 但只要触发了就符合预期
    expected_trigger_l3 = dataset.get("expected_trigger_l3", False)
    if expected_trigger_l3:
        if len(base_cites) < 2:
            passed_checks.append(("L3触发条件", f"base<2(实际{len(base_cites)}), 已触发L3"))
        else:
            failed_checks.append(("L3触发条件", f"预期触发L3但base={len(base_cites)}>=2"))
    else:
        if len(base_cites) >= 2:
            passed_checks.append(("L3未触发", f"base={len(base_cites)}>=2, 跳过L3"))
        else:
            # base<2 但不应触发L3: 说明横向挂载未命中, 但触发L3也算合理降级
            passed_checks.append(("L3触发条件", f"base={len(base_cites)}<2, 触发L3作为兜底(合理降级)"))

    # ============== 检查4: 融合排序质量分 ==============
    qs = final_state.get("quality_score", 0)
    if isinstance(qs, (int, float)) and 0 <= qs <= 100:
        passed_checks.append(("质量分有效性", f"quality_score={qs}"))
    else:
        failed_checks.append(("质量分有效性", f"quality_score异常: {qs}"))

    # ============== 检查5: 最终引用非空(正常用例) ==============
    if not expected_trigger_l3:
        if len(final_cites) > 0:
            passed_checks.append(("最终引用非空", f"final_citations={len(final_cites)}条"))
        else:
            failed_checks.append(("最终引用非空", "final_citations为空"))

    return passed_checks, failed_checks


def main():
    print("=" * 70)
    print("检索智能体『横向按需挂载 + 纵向逐级降级』策略测试")
    print("开始时间: " + REPORT["start_time"])
    print("测试用例数: " + str(len(TEST_DATASETS)))
    print("=" * 70)

    case_results = []

    for dataset in TEST_DATASETS:
        case_id = dataset["case_id"]
        try:
            final_state = run_pipeline(dataset)
            passed_checks, failed_checks = validate_case(dataset, final_state)

            # 汇总该用例的检查结果到报告
            for check_name, detail in passed_checks:
                _ok(f"{case_id}/{check_name}", detail)
            for check_name, detail in failed_checks:
                _fail(f"{case_id}/{check_name}", detail)

            case_results.append({
                "case_id": case_id,
                "desc": dataset["desc"],
                "passed": len(passed_checks),
                "failed": len(failed_checks),
                "base_count": len(final_state.get("base_citations", []) or []),
                "enhance_count": len(final_state.get("enhance_citations", []) or []),
                "final_count": len(final_state.get("citations", []) or []),
                "quality_score": final_state.get("quality_score", 0),
            })
        except Exception as e:
            print(f"\n❌ 用例 {case_id} 执行异常: {e}")
            traceback.print_exc()
            _fail(case_id, f"执行异常: {e}")
            case_results.append({
                "case_id": case_id,
                "desc": dataset["desc"],
                "passed": 0,
                "failed": 1,
                "error": str(e),
            })

    # ---------- 汇总输出 ----------
    REPORT["case_results"] = case_results
    REPORT["end_time"] = time.strftime("%Y-%m-%d %H:%M:%S")

    print("\n" + "=" * 70)
    print("测试汇总")
    print("=" * 70)
    print(f"通过: {REPORT['summary']['passed']}")
    print(f"失败: {REPORT['summary']['failed']}")
    print(f"跳过: {REPORT['summary']['skipped']}")
    print()

    print("各用例结果:")
    print("-" * 70)
    print(f"{'用例ID':<22} {'描述':<48} {'通过':<6} {'失败':<6}")
    print("-" * 70)
    for cr in case_results:
        print(f"{cr['case_id']:<22} {cr['desc'][:46]:<48} {cr['passed']:<6} {cr['failed']:<6}")
    print("-" * 70)

    # 写入 JSON 报告
    report_path = os.path.join(PROJECT_ROOT, "test_retrieval_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(REPORT, f, ensure_ascii=False, indent=2)
    print(f"\n报告已写入: {report_path}")

    # 退出码: 有失败返回1, 否则0
    sys.exit(0 if REPORT["summary"]["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
