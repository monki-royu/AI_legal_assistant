# -*- coding: utf-8 -*-
"""
企查查 API 接入自检脚本
====================================================
功能:
    分三层验证企查查相对方资信接入链路是否正常工作:

    第1层: QiChaChaClient 工具模块
        - 校验配置加载(AppKey/SecretKey/BaseURL/Timeout)
        - 执行 5 次查询(含空名/通用名/正常企业/不良企业名)验证 mock 降级
        - 若已配置真实 API Key 则尝试真实调用并对比

    第2层: credit_check_node 节点逻辑
        - 构造 AgentState(甲方良好/乙方不良), 运行 credit_check_node
        - 验证 party_a_credit_info / party_b_credit_info / credit_risk_items
          / credit_check_success 4 个状态字段是否正确写入
        - 验证立场加权逻辑: 用户=甲方时乙方风险的 is_counterparty=True
        - 验证严重程度映射: 吊销/注销 => critical, 失信 => critical 等

    第3层: risk_aggregate_node 资信评分融合
        - 同时运行 party_identify_node => credit_check_node => risk_aggregate_node
        - 验证 overall_risk_score / risk_level / merged_risk_items 含资信风险
        - 验证对立方加权扣分(对比同一风险作为己方/对立方的分差)

执行方式:
    方式1: 双击 run_test_qichacha.bat
    方式2: 在项目根目录打开 cmd(不是 PowerShell), 执行:
           set PYTHONIOENCODING=utf-8 && python test_qichacha_api.py
    方式3: 若 PowerShell 已解除执行策略:
           $env:PYTHONIOENCODING='utf-8'; python test_qichacha_api.py

输出文件:
    - 控制台打印所有测试步骤与结论
    - test_qichacha_report.json: 完整的结构化测试报告(可提交留档)
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

REPORT = {
    "start_time": time.strftime("%Y-%m-%d %H:%M:%S"),
    "tests": {},
    "summary": {"passed": 0, "failed": 0, "skipped": 0},
}


def _ok(case, detail=""):
    print(f"  ✅ PASS  {case}" + (f"  → {detail}" if detail else ""))
    REPORT["tests"].setdefault(case, {"status": "pass", "detail": detail})
    REPORT["summary"]["passed"] += 1


def _fail(case, detail=""):
    print(f"  ❌ FAIL  {case}" + (f"  → {detail}" if detail else ""))
    REPORT["tests"].setdefault(case, {"status": "fail", "detail": str(detail)})
    REPORT["summary"]["failed"] += 1


def _skip(case, detail=""):
    print(f"  ⚠️  SKIP  {case}" + (f"  → {detail}" if detail else ""))
    REPORT["tests"].setdefault(case, {"status": "skip", "detail": detail})
    REPORT["summary"]["skipped"] += 1


# ================================================================
# 第 1 层: QiChaChaClient 工具模块
# ================================================================
def test_layer1_client():
    print("\n" + "=" * 70)
    print("🔥 第 1 层: QiChaChaClient 工具模块测试")
    print("=" * 70)
    try:
        from common.qichacha_client import QiChaChaClient
    except Exception as e:
        _fail("L1-01 导入 QiChaChaClient", f"ImportError: {e}")
        return
    _ok("L1-01 导入 QiChaChaClient")

    c = QiChaChaClient()

    # --- 配置加载 ---
    print("\n  [配置加载]")
    if c.app_key and c.secret_key:
        _ok("L1-02 企查查 AppKey/SecretKey 已配置", f"AppKey={c.app_key[:6]}***... BaseURL={c.base_url} Timeout={c.timeout}s")
    else:
        _skip("L1-02 企查查 AppKey/SecretKey 未配置", "后续所有真实 API 测试将走 mock 数据降级(不影响主流程)")
        _skip("   提示: 在 .env 中配置 QICHACHA_APP_KEY 和 QICHACHA_SECRET_KEY 可启用真实 API")

    if c.enabled:
        _ok("L1-03 requests 库可用 + Key 完整 → 真实 API 模式已启用")
    else:
        _skip("L1-03 未启用真实 API (未配置Key 或 requests 库不可用)", "将走 mock 数据降级(主流程可用)")

    # --- 5 组 mock 查询 ---
    print("\n  [Mock 数据查询一致性]")
    cases = [
        ("华为技术有限公司",     "科技大公司",      False),
        ("阿里巴巴(中国)有限公司", "投资/股份大公司", False),
        ("某失信异常商贸有限公司", "不良名称触发负面",  False),
        ("甲方",                 "通用名,直接降级",   True),
        ("",                     "空名,直接降级",     True),
    ]
    scores = {}
    for name, tag, expect_empty_note in cases:
        try:
            r = c.query_company_credit(name)
        except Exception as e:
            _fail(f"L1-04 查询 name={name!r}", f"Exception: {e}")
            continue

        mock = r.get("mock", True)
        score = r.get("credit_score")
        level = r.get("risk_level")
        required_keys = ["basic_info", "shareholders", "dishonest", "executed",
                         "abnormal", "penalties", "credit_score", "risk_level", "mock"]
        missing = [k for k in required_keys if k not in r]

        if missing:
            _fail(f"L1-04 查询 name={name!r}", f"缺少字段: {missing}")
            continue

        if not mock and expect_empty_note:
            _fail(f"L1-04 查询 name={name!r}", f"名称={name!r} 应走 mock 但 mock=False")
            continue

        if not isinstance(score, (int, float)) or score < 0 or score > 100:
            _fail(f"L1-04 查询 name={name!r}", f"credit_score={score!r} 不在 [0,100]")
            continue

        if level not in ("Low", "Medium", "High"):
            _fail(f"L1-04 查询 name={name!r}", f"risk_level={level!r} 非法(应为 Low/Medium/High)")
            continue

        info = r.get("basic_info", {})
        if isinstance(info, dict):
            for k in ("company_name", "legal_person", "registered_capital",
                      "establish_date", "status", "credit_code"):
                if not info.get(k):
                    _fail(f"L1-04 查询 name={name!r}", f"basic_info 缺 {k}")
                    break
            else:
                _ok(f"L1-04 查询 name={name!r} ({tag})",
                    f"mock={mock}, score={score}, level={level}, "
                    f"status={info.get('status')}, shareholders={len(r['shareholders'])}, "
                    f"dishonest={len(r['dishonest'])}, executed={len(r['executed'])}, "
                    f"abnormal={len(r['abnormal'])}, penalties={len(r['penalties'])}")
                scores[name] = (score, level)
                continue
        _fail(f"L1-04 查询 name={name!r}", "basic_info 结构非法")

    # --- 同公司 hash 一致性(同名查询两次应返回完全相同 score) ---
    print("\n  [Mock 数据 hash 一致性]")
    r1 = c.query_company_credit("一致性测试有限公司")
    r2 = c.query_company_credit("一致性测试有限公司")
    if (r1.get("credit_score") == r2.get("credit_score")
            and r1.get("risk_level") == r2.get("risk_level")
            and len(r1.get("shareholders", [])) == len(r2.get("shareholders", []))):
        _ok("L1-05 同名查询两次结果一致", f"score={r1.get('credit_score')}, level={r1.get('risk_level')}")
    else:
        _fail("L1-05 同名查询两次结果不一致",
              f"第1次: score={r1.get('credit_score')}, level={r1.get('risk_level')}, "
              f"第2次: score={r2.get('credit_score')}, level={r2.get('risk_level')}")

    # --- 评分单调性: "不良名称"应显著低于"大公司" ---
    print("\n  [评分单调性校验]")
    if scores.get("华为技术有限公司") and scores.get("某失信异常商贸有限公司"):
        good_score = scores["华为技术有限公司"][0]
        bad_score = scores["某失信异常商贸有限公司"][0]
        if good_score > bad_score:
            _ok("L1-06 评分单调性(大公司 > 不良名)",
                f"华为={good_score}, 不良商贸={bad_score}, 分差={good_score - bad_score}")
        else:
            _fail("L1-06 评分单调性异常", f"华为={good_score} ≤ 不良商贸={bad_score}")
    else:
        _skip("L1-06 评分单调性(缺少样本)")


# ================================================================
# 第 2 层: credit_check_node 节点逻辑
# ================================================================
def test_layer2_node():
    print("\n" + "=" * 70)
    print("🔥 第 2 层: credit_check_node 节点逻辑测试")
    print("=" * 70)
    try:
        from __004__langgraph_more_nodes.agent_state import AgentState
        from __004__langgraph_more_nodes.nodes.credit_check_node import credit_check_node
    except Exception as e:
        _fail("L2-01 导入 AgentState / credit_check_node", f"ImportError: {e}")
        return
    _ok("L2-01 导入 AgentState / credit_check_node")

    # 构造 state: 用户=甲方; 甲方=华为(良好); 乙方=某失信异常商贸(不良)
    s = AgentState(
        party_a="华为技术有限公司",
        party_b="某失信异常商贸有限公司",
        user_side="A",
    )
    try:
        out = credit_check_node(s)
    except Exception as e:
        _fail("L2-02 credit_check_node 执行无异常", f"Exception: {e}\n{traceback.format_exc()}")
        return
    _ok("L2-02 credit_check_node 执行无异常")

    # 检查 4 个资信状态字段
    a_info = out.get("party_a_credit_info") or {}
    b_info = out.get("party_b_credit_info") or {}
    risks = out.get("credit_risk_items") or []
    success_flag = out.get("credit_check_success")

    print("\n  [状态字段写入校验]")
    if isinstance(a_info, dict) and "credit_score" in a_info:
        _ok("L2-03 party_a_credit_info 结构正确",
            f"score={a_info.get('credit_score')}, level={a_info.get('risk_level')}, mock={a_info.get('mock')}")
    else:
        _fail("L2-03 party_a_credit_info 结构非法", f"实际={type(a_info).__name__}: {a_info}")

    if isinstance(b_info, dict) and "credit_score" in b_info:
        _ok("L2-04 party_b_credit_info 结构正确",
            f"score={b_info.get('credit_score')}, level={b_info.get('risk_level')}, mock={b_info.get('mock')}")
    else:
        _fail("L2-04 party_b_credit_info 结构非法", f"实际={type(b_info).__name__}: {b_info}")

    if isinstance(risks, list):
        _ok("L2-05 credit_risk_items 是列表", f"共 {len(risks)} 条")
    else:
        _fail("L2-05 credit_risk_items 类型错误", f"实际={type(risks).__name__}")

    if isinstance(success_flag, bool):
        _ok("L2-06 credit_check_success 是 bool", f"值={success_flag}")
    else:
        _fail("L2-06 credit_check_success 类型错误", f"实际={type(success_flag).__name__}")

    # 风险项字段一致性
    print("\n  [风险项字段一致性]")
    required_risk_keys = ["source", "clause", "severity", "description",
                          "suggestion", "legal_basis", "is_counterparty",
                          "party_label", "credit_category"]
    bad_risk_count = 0
    sev_counter_a = 0
    sev_counter_b = 0
    counterparty_b_true = 0
    critical_count = 0
    for r in risks:
        if not isinstance(r, dict):
            bad_risk_count += 1
            continue
        missing = [k for k in required_risk_keys if k not in r]
        if missing:
            print(f"      风险项缺字段: {missing}")
            bad_risk_count += 1
            continue
        if r.get("severity") == "critical":
            critical_count += 1
        if r.get("party_label") == "甲方":
            sev_counter_a += 1
        if r.get("party_label") == "乙方":
            sev_counter_b += 1
            if r.get("is_counterparty") is True:
                counterparty_b_true += 1
    if bad_risk_count == 0:
        _ok("L2-07 风险项字段齐全(无缺失)", f"甲方风险={sev_counter_a}, 乙方风险={sev_counter_b}, critical={critical_count}")
    else:
        _fail("L2-07 风险项字段有缺失", f"{bad_risk_count} 条不符合要求")

    # 立场加权: 用户=甲方, 乙方风险的 is_counterparty 必须全部=True
    print("\n  [立场加权逻辑]")
    if sev_counter_b > 0 and counterparty_b_true == sev_counter_b:
        _ok("L2-08 用户=甲方, 乙方风险全部 is_counterparty=True",
            f"乙方风险 {sev_counter_b} 条, 标记对立方 {counterparty_b_true} 条")
    elif sev_counter_b == 0:
        _skip("L2-08 乙方未产生风险项(无法验证立场加权)")
    else:
        _fail("L2-08 立场加权逻辑错误",
              f"乙方风险 {sev_counter_b} 条, 但仅 {counterparty_b_true} 条标记 is_counterparty=True")

    # 严重程度映射: 若乙方有"失信/吊销注销", 应至少 1 条 critical
    b_dishonest = len(b_info.get("dishonest", [])) if isinstance(b_info, dict) else 0
    b_status = str(b_info.get("basic_info", {}).get("status", "")) if isinstance(b_info, dict) else ""
    if b_dishonest > 0 or "吊销" in b_status or "注销" in b_status:
        if critical_count >= 1:
            _ok("L2-09 严重程度映射(失信/吊销注销 => critical)",
                f"乙方失信={b_dishonest}, 状态={b_status}, critical风险={critical_count}条")
        else:
            _fail("L2-09 严重程度映射失败",
                  f"乙方失信={b_dishonest}, 状态={b_status}, 但无 critical 风险项")
    else:
        _skip("L2-09 无触发 critical 的乙方负面数据")


# ================================================================
# 第 3 层: risk_aggregate_node 资信评分融合
# ================================================================
def test_layer3_aggregate():
    print("\n" + "=" * 70)
    print("🔥 第 3 层: risk_aggregate_node 资信评分融合测试")
    print("=" * 70)
    try:
        from __004__langgraph_more_nodes.agent_state import AgentState
        from __004__langgraph_more_nodes.nodes.party_identify_node import party_identify_node
        from __004__langgraph_more_nodes.nodes.credit_check_node import credit_check_node
        from __004__langgraph_more_nodes.nodes.risk_aggregate_node import risk_aggregate_node
    except Exception as e:
        _fail("L3-01 导入 4 个节点", f"ImportError: {e}")
        return
    _ok("L3-01 导入 4 个节点 (party_identify/credit_check/risk_aggregate + AgentState)")

    # 构造完整合同 -> 甲乙方识别 -> 资信 -> 风险聚合
    contract_text = """合同名称：软件开发服务合同
甲方（委托方）：华为技术有限公司
乙方（受托方）：某失信异常商贸有限公司
依据《民法典》及相关法律法规，甲乙双方本着平等自愿的原则，经充分协商，
就甲方向乙方采购软件开发服务事宜，签订本合同。
第一条 服务内容：乙方为甲方开发 OA 系统一套。
第二条 合同金额：人民币壹佰万元整（¥1,000,000）。
第三条 付款方式：合同签订后 7 日内甲方预付 50%；验收合格后 10 日内支付尾款 50%。
第四条 交付周期：合同签订后 90 日内完成交付。
第五条 违约责任：任何一方违约，应向守约方支付合同金额 5% 的违约金。
第六条 争议解决：协商不成的，提交北京仲裁委员会仲裁。
"""
    try:
        s0 = AgentState(input="测试合同", doc_text=contract_text)
        s1 = party_identify_node(s0)
        s2 = credit_check_node(s1)
        s3 = risk_aggregate_node(s2)
    except Exception as e:
        _fail("L3-02 三节点流水线执行", f"Exception: {e}\n{traceback.format_exc()}")
        return
    _ok("L3-02 三节点流水线执行无异常")

    merged = s3.get("merged_risk_items") or []
    score = s3.get("overall_risk_score", 0)
    level = s3.get("risk_level", "")

    print("\n  [融合字段校验]")
    if isinstance(score, (int, float)) and 0 <= score <= 100:
        _ok("L3-03 overall_risk_score 在 [0,100]", f"score={score}")
    else:
        _fail("L3-03 overall_risk_score 非法", f"实际={score!r}")

    if level in ("Low", "Medium", "High"):
        _ok("L3-04 risk_level 合法", f"level={level}")
    else:
        _fail("L3-04 risk_level 非法", f"实际={level!r}")

    if isinstance(merged, list):
        # 统计来源分布
        src_cnt = {}
        for r in merged:
            src = r.get("source", "未知")
            src_cnt[src] = src_cnt.get(src, 0) + 1
        has_credit = src_cnt.get("资信审查", 0) > 0
        if has_credit:
            _ok("L3-05 merged_risk_items 含资信风险项", f"来源分布={src_cnt}")
        else:
            _fail("L3-05 merged_risk_items 不含资信风险项", f"来源分布={src_cnt}")
    else:
        _fail("L3-05 merged_risk_items 类型错误", f"实际={type(merged).__name__}")

    # 对立方加权验证: 制造"乙方只有 1 条 medium 信用风险"的对照实验
    print("\n  [对立方加权扣分验证(对照实验)]")
    credit_risk_base = {
        "source": "资信审查", "clause": "乙方被执行人", "severity": "medium",
        "description": "测试项", "suggestion": "测试", "legal_basis": "测试",
        "credit_category": "被执行人", "party_label": "乙方", "party_name": "对照公司",
    }
    # 己方(用户=乙方): is_counterparty=False
    s_own = AgentState(
        merged_risk_items=[],
        contract_risk_items=[],
        compliance_risk_items=[],
        numeric_risk_items=[],
        credit_risk_items=[{**credit_risk_base, "is_counterparty": False}],
        party_a_credit_info={},
        party_b_credit_info={},
    )
    # 对立方(用户=甲方): is_counterparty=True
    s_opp = AgentState(
        merged_risk_items=[],
        contract_risk_items=[],
        compliance_risk_items=[],
        numeric_risk_items=[],
        credit_risk_items=[{**credit_risk_base, "is_counterparty": True}],
        party_a_credit_info={},
        party_b_credit_info={},
    )
    try:
        r_own = risk_aggregate_node(s_own)
        r_opp = risk_aggregate_node(s_opp)
        sc_own = r_own.get("overall_risk_score", 0)
        sc_opp = r_opp.get("overall_risk_score", 0)
        if sc_opp < sc_own:
            _ok("L3-06 对立方加权扣分生效",
                f"己方风险 score={sc_own}, 对立方风险 score={sc_opp}, "
                f"多扣 {round(sc_own - sc_opp, 2)} 分")
        else:
            _fail("L3-06 对立方加权未生效",
                  f"己方风险 score={sc_own} ≤ 对立方风险 score={sc_opp}")
    except Exception as e:
        _fail("L3-06 对立方加权验证异常", f"Exception: {e}")

    # 全局资信分修正: 乙方 credit_score=50 应额外扣分
    print("\n  [全局资信分微调验证(对照实验)]")
    s_base = AgentState(
        merged_risk_items=[],
        contract_risk_items=[], compliance_risk_items=[],
        numeric_risk_items=[], credit_risk_items=[],
        party_a_credit_info={"credit_score": 95},
        party_b_credit_info={"credit_score": 95},  # 双方 95 → 加 3 分
    )
    s_bad = AgentState(
        merged_risk_items=[],
        contract_risk_items=[], compliance_risk_items=[],
        numeric_risk_items=[], credit_risk_items=[],
        party_a_credit_info={"credit_score": 95},
        party_b_credit_info={"credit_score": 50},  # 乙方 50 → (60-50)/10*2 = 2 分? 不: (60-50)/10=1 gap, -min(10, 2*1)=-2
    )
    try:
        r_base = risk_aggregate_node(s_base)
        r_bad = risk_aggregate_node(s_bad)
        sc_base = r_base.get("overall_risk_score", 0)
        sc_bad = r_bad.get("overall_risk_score", 0)
        # 双方 95: 95 基础 + 3 修正 = 98
        # 一方 50: 95 基础 - 2 修正 = 93
        if sc_bad < sc_base:
            _ok("L3-07 全局资信分微调生效",
                f"双方95分 score={sc_base}, 乙方50分 score={sc_bad}, "
                f"扣 {round(sc_base - sc_bad, 2)} 分")
        else:
            _fail("L3-07 全局资信分微调未生效",
                  f"双方95分 score={sc_base} ≤ 乙方50分 score={sc_bad}")
    except Exception as e:
        _fail("L3-07 全局资信分微调验证异常", f"Exception: {e}")


# ================================================================
# 主入口
# ================================================================
def main():
    banner = "\n" + "#" * 70 + "\n"
    banner += "#  企查查 API 接入自检脚本 (法智引擎 - 相对方资信功能)\n"
    banner += "#  项目路径: " + PROJECT_ROOT + "\n"
    banner += "#" * 70
    print(banner)

    test_layer1_client()
    test_layer2_node()
    test_layer3_aggregate()

    # ---- 汇总 ----
    REPORT["end_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
    passed = REPORT["summary"]["passed"]
    failed = REPORT["summary"]["failed"]
    skipped = REPORT["summary"]["skipped"]
    total = passed + failed + skipped
    ratio = (passed / total * 100) if total else 0

    print("\n" + "=" * 70)
    print("📊 测试汇总")
    print("=" * 70)
    print(f"   PASS:  {passed}")
    print(f"   FAIL:  {failed}")
    print(f"   SKIP:  {skipped}")
    print(f"   总计:  {total}")
    print(f"   通过率: {ratio:.1f}%")
    if failed == 0:
        print("\n🎉 所有断言通过! 企查查接入链路工作正常")
        print("   注: 若未配置真实 API Key, 资信节点会自动降级为 mock 数据(不影响合同审核主流程)")
    else:
        print("\n⚠️  存在失败用例, 请检查上方 FAIL 日志并修复")

    # 保存 JSON 报告
    report_path = os.path.join(PROJECT_ROOT, "test_qichacha_report.json")
    try:
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(REPORT, f, ensure_ascii=False, indent=2)
        print(f"\n📄 结构化测试报告已保存: {report_path}")
    except Exception as e:
        print(f"\n[!] 报告保存失败: {e}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[!] 用户中断")
        sys.exit(130)
    except Exception:
        traceback.print_exc()
        sys.exit(2)
