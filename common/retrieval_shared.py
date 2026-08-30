"""检索子图共享常量与工具 (单一真相源, 避免各节点重复/漂移定义)

集中存放:
  - _SOURCE_AUTHORITY: 5 知识源权威性基础权重 (fusion_ranking / precision_filter 共用)
  - _ask_user_interrupt: 付费接口中断问询的通用安全封装 (beida_fabao_gate / credit_check 共用)
  - _GENERIC_NAMES / _norm_name / _extract_party_names: 合同主体通用占位名与正则提取
    (credit_precheck / credit_check 共用, 统一口径)

本模块只依赖 os 与 langgraph, 不反向 import 任何 retrieval_nodes / agent_state, 避免循环依赖。
"""

import os
import re


# ---------------------------------------------------------------------------
# 5 知识源的权威性基础权重 (fusion_ranking / precision_filter 共用, 单一真相源)
#   权重依据: 法律效力层级 + 司法认可度 + 行业影响力
#     laws (法律)          = 1.0  — 全国人大制定, 效力最高
#     regulations (行政法规) = 0.9 — 国务院制定, 效力次之
#     interpretations (司法解释) = 0.85 — 两高制定, 司法实践中直接适用
#     cases (裁判案例)      = 0.8 — 法院判例, 具有参照性但非判例法
#     industry_sources (行业标准) = 0.7 — 部委/行业制定, 最低
# ---------------------------------------------------------------------------
_SOURCE_AUTHORITY = {
    "laws": 1.0,
    "regulations": 0.9,
    "interpretations": 0.85,
    "cases": 0.8,
    "industry_sources": 0.7,
}


# ---- interrupt 可用性探测 (模块导入期, 零运行时开销) ----
try:
    from langgraph.types import interrupt as _lg_interrupt  # langgraph >= 0.2.x
    _INTERRUPT_AVAILABLE = True
except Exception:  # ImportError 及其它导入期异常统一降级
    _lg_interrupt = None
    _INTERRUPT_AVAILABLE = False

try:
    from langgraph.errors import GraphInterrupt as _GraphInterrupt
except Exception:  # 旧版无此异常类时置 None, except 分支不生效
    _GraphInterrupt = None


def _ask_user_interrupt(payload: dict, label: str = "付费接口") -> object:
    """安全包装 interrupt(): 不可用/被禁用/异常时返回 None(视为用户拒绝), 绝不静默计费。

    label 仅用于日志前缀, 区分不同付费接口场景 (如 "北大法宝门禁" / "企查查资信")。

    环境变量 kill switch:
        LEGAL_DISABLE_INTERRUPT=1 -> 强制禁用 interrupt (子进程/非交互模式),
        降级为"拒绝付费调用", 流程正常跑完输出免费结果。

    【机制注意】interrupt() 的实现是抛出 GraphInterrupt 异常、由图运行时捕获。
    本函数【必须】把 GraphInterrupt 原样向上抛出(吞掉它 = 中断机制彻底失效)。
    """
    if not _INTERRUPT_AVAILABLE:
        print(f"{label}: 当前环境不支持 interrupt(), 降级为人工介入标志(不调用付费接口)")
        return None
    if os.environ.get("LEGAL_DISABLE_INTERRUPT", "").strip() in ("1", "true", "True", "yes"):
        print(f"{label}: LEGAL_DISABLE_INTERRUPT=1 (非交互/子进程模式), 跳过付费询问, 按拒绝处理")
        return None
    try:
        return _lg_interrupt(payload)
    except Exception as e:
        # GraphInterrupt 是正常中断信号, 必须原样上抛给图运行时(吞掉=中断失效)
        if _GraphInterrupt is not None and isinstance(e, _GraphInterrupt):
            raise
        # 其他异常(理论上不该发生)才降级为人工介入标志
        print(f"{label}: interrupt() 调用失败({e}), 降级为人工介入标志(不调用付费接口)")
        return None


# ---------------------------------------------------------------------------
# 合同主体通用占位名 (非真实主体): precheck / credit_check 共用, 单一真相源
# 注意: 这里用的是较完整的 14 名集合 (覆盖 发包方/承包方/采购方/...),
#       不再像 credit_check 旧版那样只认 {甲方, 乙方}, 避免漏判占位主体。
# ---------------------------------------------------------------------------
_GENERIC_NAMES = {"甲方", "乙方", "发包方", "承包方", "委托方", "受托方",
                  "采购方", "供货方", "买方", "卖方",
                  "出租方", "承租方", "出让方", "受让方"}


def _norm_name(n: str) -> str:
    """归一化主体名: 去除占位名/空值, 防止 '甲方'/'乙方' 污染企查查等外部查询。"""
    n = (n or "").strip()
    return "" if n in _GENERIC_NAMES else n


# ---------------------------------------------------------------------------
# 从合同/文档文本正则提取企业名称 (precheck / credit_check 共用, 统一口径)
# 覆盖常见写法: "甲方：XXX公司" / "乙方: XXX有限公司" / 独立的 "XXX有限公司"
# 说明: precheck 旧版称 _extract_party_names_simple、credit_check 旧版称
#       _extract_party_names_from_text, 两者正则不一致 —— 现统一为一份。
# ---------------------------------------------------------------------------
_PARTY_PATTERNS = [
    r'(?:甲方|发包方|委托方|采购方|买方|出租方|出让方|许可方|聘用方)[：:]\s*([^\n，,。；;]{2,30}(?:公司|集团|事务所|研究院|中心|厂|合作社))',
    r'(?:乙方|承包方|受托方|供货方|卖方|承租方|受让方|被许可方|受聘方)[：:]\s*([^\n，,。；;]{2,30}(?:公司|集团|事务所|研究院|中心|厂|合作社))',
    r'([\u4e00-\u9fa5A-Za-z（）()]{4,25}(?:有限公司|股份有限公司|有限责任公司|集团有限公司|科技有限公司))',
]


def _extract_party_names(text: str, limit: int = 4) -> list:
    """从合同/文档文本中提取企业名称 (正则, 不依赖 LLM)。

    参数:
        text (str): 合同/文档全文 (截取前 3000 字符即可覆盖大部分场景)
        limit (int): 最多返回的名称数量 (默认 4, 避免过多查询拖慢检索)
    返回:
        list[str]: 提取到的企业名称列表(去重); 无匹配时返回空列表。
    """
    if not text:
        return []
    sample = text[:3000]
    names = []
    seen = set()
    for pattern in _PARTY_PATTERNS:
        for m in re.findall(pattern, sample):
            name = m.strip() if isinstance(m, str) else str(m).strip()
            if name and name not in ("甲方", "乙方", "公司", "未知") and len(name) >= 4:
                if name not in seen:
                    seen.add(name)
                    names.append(name)
    return names[:limit]
