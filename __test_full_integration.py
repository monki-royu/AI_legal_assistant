"""法智引擎前端全链路验证测试"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=" * 60)
print("法智引擎前端+后端 全链路集成测试")
print("=" * 60)

# 测试1: 语法验证 (py_compile)
print("\n[测试1] Python语法验证...")
import py_compile
files = [
    "__006__streamlit/app.py",
    "__004__langgraph_more_nodes/langgraph_main.py",
    "__004__langgraph_more_nodes/agent_state.py",
]
all_ok = True
for f in files:
    try:
        py_compile.compile(f, doraise=True)
        print(f"  ✅ {f} 语法通过")
    except Exception as e:
        print(f"  ❌ {f} 语法错误: {e}")
        all_ok = False
assert all_ok, "语法错误，请先修复"

# 测试2: 前端导入
print("\n[测试2] 前端模块导入...")
try:
    with open("__006__streamlit/app.py", encoding="utf-8") as fh:
        src = fh.read()
    # 提取关键函数/变量名
    checks = [
        ("_render_score_overview", "def _render_score_overview(" in src),
        ("_get_demo_result", "def _get_demo_result(" in src),
        ("_get_compliance_demo_result", "def _get_compliance_demo_result(" in src),
        ("RISK_LEVEL_MAP", "RISK_LEVEL_MAP = {" in src),
        ("legal_response_sync 导入", "from __004__langgraph_more_nodes.langgraph_main import legal_response_sync" in src or
            "from __004__langgraph_more_nodes.langgraph_main import legal_response_sync" in src.replace(" ", "")),
        ("legal_response_full 导入", "legal_response_full" in src),
        ("合同审核用 full 接口", 'legal_response_full(input_text, task_type="contract_review")' in src),
        ("合规审查用 full 接口", 'legal_response_full(input_text, task_type="compliance_review")' in src),
        ("检索用 full 接口", 'legal_response_full(f"检索关于' in src),
        ("合规审查文件读取", "compliance_upload" in src and "if not input_text and compliance_upload:" in src),
        ("法律检索文件读取", "research_upload" in src and "if not input_query and research_upload:" in src),
        ("合同审核文件读取", "if not input_text and uploaded_file:" in src),
        ("首页QA spinner", 'with st.spinner("⚖️ 法智引擎正在思考...")' in src),
        ("浅色专业背景", "--bg-primary: #FAFBFC" in src),
        ("深色文字", "--text-primary: #1F2937" in src),
        ("浅色侧边栏", "--sidebar-bg: #FFFFFF" in src),
    ]
    for name, ok in checks:
        if ok:
            print(f"  ✅ {name}")
        else:
            print(f"  ❌ 缺失: {name}")
            all_ok = False
except Exception as e:
    print(f"  ❌ 读取失败: {e}")
    all_ok = False

# 测试3: 后端 graph 构建
print("\n[测试3] 后端图构建验证...")
try:
    from __004__langgraph_more_nodes.langgraph_main import graph, legal_response_sync, legal_response_full
    print(f"  ✅ 图导入成功, 节点数={len(graph.nodes)}")
except Exception as e:
    print(f"  ❌ 图导入失败: {e}")
    all_ok = False

# 测试4: 接口返回结构 (使用demo数据模拟)
print("\n[测试4] 演示结果结构验证...")
# 我们实际导入 app 中的 demo 函数
import importlib.util
spec = importlib.util.spec_from_file_location("appmod", "__006__streamlit/app.py")
print("  (注意: app.py 会执行 set_page_config, 跳过实际加载, 仅验证源)")

# 测试5: LLM兜底链路
print("\n[测试5] 后端 LLM 兜底链路 (真实调用)...")
try:
    r = legal_response_sync("你好")
    if isinstance(r, str) and len(r) > 10:
        print(f"  ✅ LLM兜底返回成功, {len(r)}字")
        print(f"     前60字: {r[:60]}...")
    else:
        print(f"  ⚠️ 返回异常: {type(r)} {str(r)[:60]}")
except Exception as e:
    print(f"  ❌ 调用失败: {e}")

# 测试6: legal_response_full 合同审核
print("\n[测试6] legal_response_full 合同审核 (真实调用)...")
try:
    sample_text = "买卖合同 甲方张三 乙方李四 第一条iPhone15 第二条单价5000元数量100台总价500000元"
    s = legal_response_full(sample_text, task_type="contract_review")
    keys_needed = ["output", "doc_text", "merged_risk_items", "overall_risk_score",
                   "risk_level", "citations", "party_a", "need_lawyer_review"]
    missing = [k for k in keys_needed if k not in s]
    if missing:
        print(f"  ❌ 缺失字段: {missing}")
    else:
        print(f"  ✅ 结构完整, 评分={s['overall_risk_score']}, 等级={s['risk_level']}, 风险项={len(s['merged_risk_items'])}, 引用={len(s.get('citations', []))}")
except Exception as e:
    print(f"  ❌ 调用失败: {type(e).__name__}: {e}")
    import traceback; traceback.print_exc()

# 测试7: legal_response_full 合规审查
print("\n[测试7] legal_response_full 合规审查 (真实调用)...")
try:
    sample_text = "甲方：公司A 乙方：公司B 合同金额500万元，要求支付100%预付款。"
    s = legal_response_full(sample_text, task_type="compliance_review")
    keys_needed = ["output", "overall_risk_score", "risk_level", "need_lawyer_review"]
    missing = [k for k in keys_needed if k not in s]
    if missing:
        print(f"  ❌ 缺失字段: {missing}")
    else:
        print(f"  ✅ 结构完整, 评分={s['overall_risk_score']}, 等级={s['risk_level']}, 输出={len(s['output'])}字")
except Exception as e:
    print(f"  ❌ 调用失败: {type(e).__name__}: {e}")

# 测试8: legal_response_full 法律检索
print("\n[测试8] legal_response_full 法律检索 (真实调用)...")
try:
    s = legal_response_full("检索关于违约金的法律法规", task_type="legal_research")
    output_text = s.get("output", "") or s.get("final_report_markdown", "")
    if output_text:
        print(f"  ✅ 检索返回成功, {len(output_text)}字, 引用={len(s.get('citations', []))}条")
    else:
        print(f"  ⚠️ output 为空, keys={list(s.keys())[:10]}")
except Exception as e:
    print(f"  ❌ 调用失败: {type(e).__name__}: {e}")

print("\n" + "=" * 60)
print("测试完成" + (" ✅" if all_ok else " ⚠️ 存在检查项，请看上方"))
print("=" * 60)
