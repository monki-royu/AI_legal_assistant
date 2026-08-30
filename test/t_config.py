# -*- coding: utf-8 -*-
"""测试套件全局配置

【为什么单独抽一个配置文件】
    测试涉及三类"会变"的东西: 超时阈值 / 计费单价 / 指标口径参数。
    它们不应该硬编码在用例或指标代码里 —— 换模型、换机器、换验收标准时
    只改这一个文件即可, 用例本身保持稳定。
"""
import os

# ============================================================================
# 一、路径
# ============================================================================
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TEST_DIR)
OUTPUT_DIR = os.path.join(TEST_DIR, "outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)

RAW_RESULT_JSON = os.path.join(OUTPUT_DIR, "raw_results.json")
KB_MANIFEST_JSON = os.path.join(OUTPUT_DIR, "kb_manifest.json")
REPORT_HTML = os.path.join(OUTPUT_DIR, "test_report.html")

# ============================================================================
# 二、执行环境 (SAFE MODE)
#
# 测试必须"可重复、无副作用、不烧钱":
#   - LEGAL_DISABLE_PNG=1      跳过 import 期的 mermaid.ink 远程渲染 (每次省 20~40s)
#   - LEGAL_DISABLE_INTERRUPT=1 付费门禁(北大法宝/企查查)一律按"拒绝"处理, 图能跑到 END
#   - SAFE_MODE 桩: 小红书真实发布 / 企查查网络查询 全部打桩, 不产生真实外部行为
# ============================================================================
SAFE_MODE = os.environ.get("LEGAL_TEST_SAFE_MODE", "1").strip() in ("1", "true", "True")

# 单用例超时(秒)。合同审核链路实测 60~200s, 给 600s 余量; 超时即判定 TIMEOUT 失败。
CASE_TIMEOUT_SEC = int(os.environ.get("LEGAL_TEST_CASE_TIMEOUT", "600"))

# 单节点耗时超过该值, 在报告里标记为"慢节点"(黄色预警)
SLOW_NODE_SEC = float(os.environ.get("LEGAL_TEST_SLOW_NODE", "20"))
# 单节点耗时超过该值, 标记为"瓶颈节点"(红色)
BOTTLENECK_NODE_SEC = float(os.environ.get("LEGAL_TEST_BOTTLENECK_NODE", "60"))

# ============================================================================
# 三、计费单价 (用于"调用成本"指标)
#
# 口径: 元 / 百万 token。默认按 SiliconFlow Qwen 系列公开价目设置,
# 可通过环境变量覆盖。成本是估算值, 报告与简历中均标注"按配置单价估算"。
# ============================================================================
PRICE_PROMPT_PER_1M = float(os.environ.get("LEGAL_PRICE_PROMPT_1M", "2.0"))
PRICE_COMPLETION_PER_1M = float(os.environ.get("LEGAL_PRICE_COMPLETION_1M", "6.0"))
CURRENCY = "¥"

# ============================================================================
# 四、检索指标口径
# ============================================================================
TOP_K_PRECISION = 5      # P@5
TOP_K_RECALL = 10        # R@10 (召回分母口径: golden 全集)
MRR_CUTOFF = 10          # MRR@10

# 检索"完全落空"判定: citations 为空 或 最高质量分低于此值
EMPTY_RETRIEVAL_SCORE_THRESHOLD = 30.0

# 业务成功率判定: 质量分低于此值视为"依据薄弱"
MIN_ACCEPTABLE_QUALITY_SCORE = 50.0

# ============================================================================
# 五、任务类型 → 展示名 / 期望主链路
#
# 期望链路用于"状态是否正常流转"的断言, 值是**主图层节点序列**的关键路径。
# 比对策略见 t_metrics.compare_route —— 用子序列匹配而非全等,
# 允许质量门重试等合法回退分支。
# ============================================================================
TASK_META = {
    "legal_qa": {
        "name": "首页问答",
        "entry": "qa",
        "key_path": ["xiaohongshu_publish_intent", "intent_router", "qa"],
        "output_fields": ["output"],
    },
    "contract_review": {
        "name": "合同审核",
        "entry": "input_source_router",
        "key_path": ["xiaohongshu_publish_intent", "intent_router",
                     "input_source_router", "preprocess", "cc_retrieval", "dual_review"],
        "output_fields": ["output", "risk_items"],
    },
    "compliance_review": {
        "name": "合规审查",
        "entry": "input_source_router",
        "key_path": ["xiaohongshu_publish_intent", "intent_router",
                     "input_source_router", "preprocess", "cc_retrieval", "dual_review"],
        "output_fields": ["output", "risk_items"],
    },
    "legal_research": {
        "name": "法规查询",
        "entry": "r_retrieval",
        "key_path": ["xiaohongshu_publish_intent", "intent_router", "r_retrieval"],
        "output_fields": ["output"],
    },
    "case_search": {
        "name": "案例检索",
        "entry": "r_retrieval",
        "key_path": ["xiaohongshu_publish_intent", "intent_router", "r_retrieval"],
        "output_fields": ["output"],
    },
    "legal_document_gen": {
        "name": "文书生成",
        "entry": "docgen",
        "key_path": ["xiaohongshu_publish_intent", "intent_router", "docgen"],
        "output_fields": ["output", "generated_document"],
    },
    "xiaohongshu_publish": {
        "name": "小红书发布",
        "entry": "xhs",
        "key_path": ["xiaohongshu_publish_intent", "xhs"],
        "output_fields": ["output"],
    },
    "history": {
        "name": "历史记录",
        "entry": "history_store",
        "key_path": [],
        "output_fields": [],
    },
}

# 8 大任务执行顺序 (历史记录是纯存储层, 独立测试)
TASK_ORDER = [
    "legal_qa",
    "contract_review",
    "compliance_review",
    "legal_document_gen",
    "legal_research",
    "case_search",
    "history",
    "xiaohongshu_publish",
]


def apply_env():
    """在 import 后端模块之前注入测试环境变量 (必须早于任何后端 import)"""
    os.environ.setdefault("LEGAL_DISABLE_PNG", "1")
    os.environ.setdefault("LEGAL_DISABLE_INTERRUPT", "1")
    for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
               "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(_v, "1")
