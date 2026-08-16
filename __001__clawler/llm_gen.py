# -*- coding: utf-8 -*-
"""
LLM 生成助手 (条文正文补全 + 裁判案例生成)
==========================================

参考 legal-documents/backend/app/crawlers/llm_gen.py 实现.

用途:
1. 法条正文: flk 外网拿不到条文原文(只在 docx/OFD 内网), 只能拿到准确的
   "法律名 + 第X条 + 章节", 这里用 LLM 按真实条号补出条文正文(分批, 控制成本).
2. 案例: 中国裁判文书网强反爬, 无法直接爬取, 用 LLM 生成结构真实的裁判案例.

复用项目既有 common.llm.my_llm (ChatOpenAI 兼容客户端).
"""
# 📜 代码文字逻辑解析
# 本文件提供两个核心函数:
# - generate_article_contents(): 批量补全条文正文, 输入法律名+条款编号列表,
#   输出 {article_no: content} 字典. 使用低温度(0.2)尽量贴近真实条文.
# - generate_cases(): 生成指定案由的裁判案例列表, 输出案例对象列表.
#   使用略高温度(0.7)保证多样性.
# 两个函数都有降级策略: LLM 调用失败时返回占位内容, 保证非空.

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


# ============ 法条正文补全 ============

LAW_SYSTEM = """你是中国法律条文数据库整理专家，精通现行法律法规的条文原文。
任务：根据给定的【法律名称】和一批【条款编号】，输出每一条的条文正文。

严格要求：
1. 尽最大努力还原该法律该条款的真实正文内容，保持法言法语的严谨表述。
2. 只输出给定的条款编号，不要增删条款、不要合并或拆分。
3. 输出纯 JSON 对象，key 为条款编号（原样，如"第一条"），value 为该条正文文本（不含条号前缀）。
4. 不要输出任何解释、markdown 标记或多余文字。"""


def generate_article_contents(law_name: str, article_nos: list,
                              batch_size: int = 25) -> dict:
    """
    批量补全条文正文.

    Parameters
    ----------
    law_name : str
        法律全称, 如 "中华人民共和国民法典".
    article_nos : list[str]
        条款编号列表, 如 ["第一条", "第二条", ...].
    batch_size : int
        每批处理的条款数(控制 LLM 单次输入长度), 默认 25.

    Returns
    -------
    dict[str, str]
        {article_no: content} 映射. LLM 调用失败时返回占位内容, 保证非空.
    """
    result = {}
    if not article_nos:
        return result

    # 检查 LLM 是否可用(API key 是否配置)
    api_key = _conf.MODEL_API_KEY
    if not api_key:
        # 未配置 key, 返回占位内容
        return {no: f"（{law_name}{no}正文待补充）" for no in article_nos}

    # 分批调用 LLM, 避免单次输入过长
    for i in range(0, len(article_nos), batch_size):
        batch = article_nos[i:i + batch_size]
        user_msg = (
            f"法律名称：《{law_name}》\n"
            f"需要输出正文的条款编号（共{len(batch)}条）：\n"
            + "、".join(batch)
            + "\n\n请输出 JSON 对象。"
        )
        try:
            resp = my_llm.invoke([
                SystemMessage(content=LAW_SYSTEM),
                HumanMessage(content=user_msg),
            ])
            parsed = json.loads(_strip_fence(resp.content))
            for no in batch:
                text = parsed.get(no)
                # 非空字符串才采用, 否则占位
                result[no] = text.strip() if isinstance(text, str) and text.strip() \
                    else f"（{law_name}{no}正文待补充）"
        except Exception as e:
            print(f"[llm_gen] 条文补全失败({law_name} 批次{i//batch_size+1}): {e}")
            # 批次失败, 该批所有条款用占位内容
            for no in batch:
                result[no] = f"（{law_name}{no}正文待补充）"

    return result


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
        return parsed if isinstance(parsed, list) else []
    except Exception as e:
        print(f"[llm_gen] 案例生成失败({case_type}): {e}")
        return []
