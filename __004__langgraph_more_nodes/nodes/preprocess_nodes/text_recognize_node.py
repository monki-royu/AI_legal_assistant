# -*- coding: utf-8 -*-
"""
文本识别节点 (Text Recognize)
=============================

【架构定位】
    位于主图层「文本路径」(input_source_router 判定为 text 后进入)。
    职责: 把纯文本输入归一化为 doc_text, 并对 contract_review 做"是否合同相关"
    的交叉校验 —— 这是「意图 vs 输入」一致性的第二层(语义层)校验。

【两条规则】
    ① compliance_review: 输入可以是业务描述/数据处理说明/内部制度, 不要求是合同。
       → 直接 pass(不调 LLM, 省一次调用), 但标记 is_contract_input=False
         (下游 contract_classify 据此把 contract_type 写为 "", 不瞎编)。
    ② contract_review:   输入必须是一份合同正文。用"规则优先 + LLM 兜底"判定:
       - 规则评分 >= 3        → 直接放行(0 次 LLM)
       - 规则评分 <= 0        → 直接拦截(0 次 LLM)
       - 规则评分 1~2 (灰区)  → 调 1 次轻量 LLM 判"是/否"
       相关 → pass(is_contract_input=True); 不相关 → block(返回提示, 不进预处理)。

【归一化】
    文本路径不经过 doc_extract(它只解析文件), 因此本节点在 pass 时把
    input 写入 doc_text(及 doc_structured_json), 供后续 preprocess 5 节点消费。
    这与文档路径 doc_extract 写入 doc_text 的语义一致 —— doc_text 始终是
    "待审查的文档正文"这一单一真相源。

【不引入 interrupt】
    "非合同 + 合同审核" 用【返回提示】终止本轮(block), 不引入新的 interrupt
    确认循环。用户看到提示、改完输入重新提交, 会再走一次 intent_router。
"""

import re

from langchain_core.messages import HumanMessage

from common.llm import my_llm
from __004__langgraph_more_nodes.agent_state import AgentState
from __004__langgraph_more_nodes.nodes.preprocess_nodes.doc_extract_node import (
    _build_fallback_structured_json,
)


# ==============================================================================
# 【用户提示文案】block 时回给前端的内容 (不调 LLM, 纯静态文案)
# ==============================================================================
_NOT_CONTRACT_MESSAGE = (
    "⚠️ 您选择了「合同审核」，但当前输入看起来不是合同正文。\n\n"
    "合同审核需要拿到**合同全文**才能逐条分析风险，否则只能凭空推测，"
    "反而会给出不可靠的结论。\n\n"
    "请任选一种方式重新提交：\n"
    "1. 将合同全文直接粘贴到输入框；\n"
    "2. 上传合同文件（支持 PDF / DOCX / TXT）。\n\n"
    "如果您只是想咨询某个法律问题（而非审核某份具体合同），"
    "可以直接在首页或智能问答页面提问，我会按法律问答为您解答。"
)


# ==============================================================================
# 【合同语义评分规则】纯正则 + 关键词, 不调 LLM (复用 preprocess_guard 的判定思路)
# ==============================================================================
_CLAUSE_PATTERN = re.compile(r"第\s*[一二三四五六七八九十百零〇\d]+\s*[条款章节]")
_CONTRACT_TITLE_WORDS = ("合同", "协议", "契约", "承诺书", "授权书", "备忘录")
_CONTRACT_ELEMENT_WORDS = (
    "违约责任", "违约金", "争议解决", "权利义务", "生效", "签字", "盖章",
    "标的", "租金", "价款", "付款方式", "质量保证", "保密", "租赁期限",
    "交付", "验收", "仲裁", "管辖", "解除", "赔偿", "本合同", "押金",
)
_REQUEST_PREFIXES = (
    "请帮我", "帮我", "请问", "我想", "能否", "可以帮", "麻烦", "请你",
    "帮忙", "请给我", "我需要", "看看",
)
_CHITCHAT_WORDS = ("天气", "你好", "吃饭", "谢谢", "在吗", "自我介绍")

_PASS_THRESHOLD = 3      # >= 3 直接放行
_BLOCK_THRESHOLD = 0     # <= 0 直接拦截; 中间 1~2 为灰区走 LLM


def _score_contract_likeness(text: str):
    """给文本打"像不像合同"的分数 (纯规则, 不调 LLM)。"""
    score = 0
    n = len(text)

    if _CLAUSE_PATTERN.findall(text):
        score += 2
    has_a = "甲方" in text
    has_b = "乙方" in text
    if has_a and has_b:
        score += 2
    elif has_a or has_b:
        score += 1
    if any(w in text for w in _CONTRACT_TITLE_WORDS):
        score += 1
    hit_elements = [w for w in _CONTRACT_ELEMENT_WORDS if w in text]
    if hit_elements:
        score += min(len(hit_elements), 3)
    if n >= 300:
        score += 1

    head = text[:15]
    if n < 100 and any(p in head for p in _REQUEST_PREFIXES):
        score -= 3
    if n < 100 and text.rstrip().endswith(("？", "?")):
        score -= 2
    if any(w in text for w in _CHITCHAT_WORDS):
        score -= 2
    if n < 50:
        score -= 2

    return score


def _llm_is_contract(text: str) -> bool:
    """灰区兜底: 调 1 次轻量 LLM 判断文本是否为合同/协议正文。

    LLM 调用失败时返回 True(放行) —— 宁可放行也不误拦, 因为下游
    dual_review 入口还有第二道守卫, 而误拦会直接阻断正常请求。
    """
    prompt = f"""判断以下文本是否是一份合同/协议正文（包括合同条款片段）。

判断标准：
- 是：合同、协议全文，或摘录的合同条款（含权利义务、违约责任等约定内容）
- 否：用户的请求/提问（如"帮我看看这个合同"）、闲聊、新闻、法条原文、纯业务描述

文本：
{text[:800]}

只回答一个字：是 或 否。"""
    try:
        resp = my_llm.invoke([HumanMessage(content=prompt)])
        answer = str(resp.content).strip()
        if "否" in answer or "不是" in answer:
            return False
        return True
    except Exception as e:
        print(f"  ⚠️ [文本识别] 灰区 LLM 判定失败, 保守放行: {e}")
        return True


def _normalize_text_input(state: AgentState) -> dict:
    """文本路径归一化: 把 input 写入 doc_text / doc_structured_json。

    与文档路径 doc_extract 写入 doc_text 的语义对齐 —— doc_text 始终是
    "待审查的文档正文"单一真相源。
    """
    user_input = state.get("input", "") or ""
    return {
        "doc_text": user_input,
        "doc_structured_json": _build_fallback_structured_json(user_input, ""),
    }


def text_recognize_node(state: AgentState):
    """文本路径识别节点: 归一化 input→doc_text, 并(仅合同审核)校验是否合同相关。

    读取字段:
        - input       (str): 用户纯文本输入
        - task_type   (str): contract_review / compliance_review

    写入字段:
        - doc_text / doc_structured_json: pass 时由 _normalize_text_input 写入
        - is_contract_input (bool): 是否为合同正文(供 contract_classify 决定空值)
        - text_recognize_flag ("pass" / "block"): 供主图分流
        - block 时: output / final_report_markdown / need_user_confirm

    说明: 本节点【不写 task_type】(那是 intent_router 的职责)。
    """
    print("--- 文本路径识别: 归一化输入 + (合同审核)校验是否合同相关 ---")

    task_type = state.get("task_type", "")
    input_text = str(state.get("input", "") or "").strip()

    # ============ compliance_review: 输入可非合同, 直接放行 ============
    if task_type == "compliance_review":
        print("  [文本识别] compliance_review → 直接 pass(不调 LLM), 标记非合同")
        return {
            **_normalize_text_input(state),
            "is_contract_input": False,   # 非合同文本 → contract_classify 写 ""
            "text_recognize_flag": "pass",
        }

    # ============ contract_review: 必须校验是否合同相关 ============
    # 空文本 → 拦截(理论上 input_source_router 后不应为空, 这里双保险)
    if not input_text:
        return {
            "is_contract_input": False,
            "text_recognize_flag": "block",
            "need_user_confirm": True,
            "output": _NOT_CONTRACT_MESSAGE,
            "final_report_markdown": _NOT_CONTRACT_MESSAGE,
        }

    score = _score_contract_likeness(input_text)
    print(f"  [文本识别] 合同语义评分={score}")

    if score >= _PASS_THRESHOLD:
        print("  [文本识别] 规则判定为合同 → 放行 (未调用 LLM)")
        is_contract = True
    elif score <= _BLOCK_THRESHOLD:
        print("  [文本识别] 规则判定非合同 → 拦截 (未调用 LLM)")
        is_contract = False
    else:
        print(f"  [文本识别] 落入灰区({score}分) → 调用轻量 LLM 兜底判定")
        is_contract = _llm_is_contract(input_text)
        print(f"  [文本识别] LLM 判定: {'是合同' if is_contract else '非合同'}")

    if is_contract:
        return {
            **_normalize_text_input(state),
            "is_contract_input": True,
            "text_recognize_flag": "pass",
        }

    # 非合同 + 合同审核 → block, 返回提示, 不进预处理
    print("  [文本识别] 非合同 + 合同审核 → 拦截, 返回提示(未进预处理/检索/双审)")
    return {
        "is_contract_input": False,
        "text_recognize_flag": "block",
        "need_user_confirm": True,
        "output": _NOT_CONTRACT_MESSAGE,
        "final_report_markdown": _NOT_CONTRACT_MESSAGE,
    }
