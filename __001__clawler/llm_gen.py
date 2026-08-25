# -*- coding: utf-8 -*-
"""
LLM 生成助手 (条文正文补全 + 裁判案例生成)
==========================================

参考 legal-documents/backend/app/crawlers/llm_gen.py 实现.

用途:
1. 案例: 中国裁判文书网强反爬, 无法直接爬取, 用 LLM 生成结构真实的裁判案例,
   并对每条案例打「AI 生成·未核验」标签, 避免被误认为真实裁判文书.

【防幻觉 (2026-08 决策)】
原「法条正文补全」(generate_article_contents) 会用 LLM 按条号凭空补出法条原文,
属于编造法条, 已按项目决策整体删除。法律条文一律只采用 flk 真实原文,
拿不到原文时保留「正文待补充」占位, 绝不编造。

复用项目既有 common.llm.my_llm (ChatOpenAI 兼容客户端).
"""
# 📜 代码文字逻辑解析
# 本文件提供一个核心函数:
# - generate_cases(): 生成指定案由的裁判案例列表, 输出案例对象列表,
#   每条案例自动附加「AI 生成·未核验」标签.
#   使用略高温度(0.7)保证多样性.

import json  # JSON 解析, 用于解析 LLM 返回的 JSON
from langchain_core.messages import SystemMessage, HumanMessage  # 消息类型

from common.llm import my_llm  # 项目统一 LLM 客户端
from common.config import Config  # 配置管理类, 实例化后读取 API key

_conf = Config()  # 模块级 Config 单例, 供下方读取 API key 判断


def _strip_fence(text: str) -> str:
    """去掉 LLM 输出里可能的 ```json ... ``` 代码块包裹."""
    content = (text or "").strip()
    if content.startswith("```"):
        # 去掉首行 ```json 标记
        content = content.split("\n", 1)[1] if "\n" in content else content
        # 去掉结尾 ```
        if content.rstrip().endswith("```"):
            content = content.rstrip()[:-3]
    return content.strip()


# ============ 裁判案例生成 ============

CASE_SYSTEM = """你是中国司法案例数据整理专家。请生成结构真实、符合裁判文书规范的公开裁判案例。

严格要求：
1. 输出纯 JSON 数组，每个元素是一个案例对象，字段如下：
   - case_title: 案件标题（如"张某与某公司劳动争议纠纷案"）
   - case_no: 案号（真实格式，如"(2024)京0105民初12345号"）
   - court_name: 审理法院全称
   - judge_date: 裁判日期，格式 YYYY-MM-DD（2022-2025 之间）
   - case_summary: 案情摘要，200-400字，包含当事人、争议焦点、基本事实
   - judgment: 判决结果，100-250字
   - cited_laws: 引用法条数组，如 ["中华人民共和国民法典第一千一百六十五条", "中华人民共和国劳动合同法第四十七条"]
2. 案件要贴近真实审判逻辑，法条引用要与案由匹配。
3. 不要输出任何解释、markdown 标记或多余文字，只输出 JSON 数组。"""


def generate_cases(case_type: str, count: int,
                   avoid_titles: list = None) -> list:
    """
    生成指定案由的裁判案例列表.

    Parameters
    ----------
    case_type : str
        案由, 如 "劳动争议", "合同纠纷", "婚姻家庭".
    count : int
        生成案例数量.
    avoid_titles : list[str], optional
        已有案例标题列表, 喂给 LLM 以避免重复生成.

    Returns
    -------
    list[dict]
        案例对象列表. 未配置 key 或调用失败时返回空列表(不塞假数据).
    """
    # 检查 LLM 是否可用
    api_key = _conf.MODEL_API_KEY
    if not api_key:
        print("[llm_gen] 未配置 LLM API key, 跳过案例生成")
        return []

    # 构造"避免重复"提示
    avoid = ""
    if avoid_titles:
        avoid = "\n\n请避免生成与以下已有案件标题重复或高度相似的案例：\n" + "、".join(avoid_titles[:30])

    user_msg = f"请生成 {count} 个「{case_type}」类型的裁判案例，JSON 数组。{avoid}"
    try:
        resp = my_llm.invoke([
            SystemMessage(content=CASE_SYSTEM),
            HumanMessage(content=user_msg),
        ])
        parsed = json.loads(_strip_fence(resp.content))
        # 兼容模型包一层 {"cases": [...]} 的情况
        if isinstance(parsed, dict):
            for v in parsed.values():
                if isinstance(v, list):
                    parsed = v
                    break
        if isinstance(parsed, list):
            # 打「AI 生成·未核验」标签: 这些案例由 LLM 生成, 非真实裁判文书,
            # 仅供检索/参考, 必须明确标注避免误用。
            for c in parsed:
                if isinstance(c, dict):
                    c["source_label"] = "AI 生成·未核验"
                    c["ai_generated"] = True
                    c.setdefault(
                        "disclaimer",
                        "本案例由 AI 生成, 未经人工核验, 仅供研究参考, 不构成法律意见。",
                    )
            return parsed
        return []
    except Exception as e:
        print(f"[llm_gen] 案例生成失败({case_type}): {e}")
        return []
