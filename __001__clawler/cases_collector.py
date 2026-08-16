# -*- coding: utf-8 -*-
"""
司法案例采集器 (LLM 生成真实结构案例)
=====================================

参考 legal-documents/backend/app/crawlers/cases_collector.py 实现.

数据源策略:
  中国裁判文书网有强反爬 + 验证码, 外网无法稳定爬取真实案例.
  改用 LLM 生成"结构真实、符合裁判文书规范"的案例, 落地为 txt 文件,
  供后续知识图谱抽取与 RAG 检索消费.

去重策略:
  - 文件级去重: 同一 case_title + case_no 只生成一次, 已存在则跳过
  - LLM 去重: 生成前把已有案件标题喂给 LLM, 要求避免重复

输出结构:
  __001__clawler/裁判案例/{案由}/{案件标题}.txt
  每个文件包含: 案件标题/案号/法院/日期/案情摘要/判决结果/引用法条
"""
# 📜 代码文字逻辑解析
# 本文件是 AI 法律助理项目的案例数据采集模块.
# 由于中国裁判文书网强反爬+验证码, 外网无法稳定爬取, 改用 LLM 生成结构真实的案例.
# 核心函数 generate_cases() 按 case_type 逐类生成案例:
# 1. 读取已有案例标题列表(avoid_titles), 避免重复生成
# 2. 调用 llm_gen.generate_cases() 让 LLM 生成案例列表
# 3. 逐个案例写入 txt 文件(按案由分目录), 文件级去重(已存在则跳过)
# 4. 统计新增数量并打印进度

import os  # 路径操作
import json  # JSON 序列化(用于 cited_laws 字段)
import hashlib  # 确定性文件名生成(避免特殊字符)

from common.path_utils import root_dir  # 项目根目录
from __001__clawler.llm_gen import generate_cases as llm_generate_cases  # LLM 案例生成


# 默认生成的案由(覆盖主要业务场景)
DEFAULT_CASE_TYPES = [
    "劳动争议",
    "合同纠纷",
    "婚姻家庭",
    "交通事故",
    "房产纠纷",
]


def _case_filename(case_title: str, case_no: str) -> str:
    """
    生成确定性的案例文件名.

    用 md5(case_title|case_no) 前 16 位作为文件名, 避免标题中的特殊字符导致文件名非法.
    同时保证同一案例永远对应同一文件名, 天然去重.
    """
    raw = f"{case_title}|{case_no}".encode("utf-8")
    return "case_" + hashlib.md5(raw).hexdigest()[:16] + ".txt"


def _existing_titles(case_type: str, case_dir: str) -> list:
    """
    取该案由下已有案例标题, 喂给 LLM 以避免重复生成.

    Parameters
    ----------
    case_type : str
        案由, 如 "劳动争议".
    case_dir : str
        该案由的输出目录路径.

    Returns
    -------
    list[str]
        已有案例标题列表(从已写入的 txt 文件第一行读取).
    """
    titles = []
    if not os.path.isdir(case_dir):
        return titles
    for fname in os.listdir(case_dir):
        if not fname.endswith(".txt"):
            continue
        fpath = os.path.join(case_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                first_line = f.readline().strip()
                # 文件第一行格式: "# 案件标题: xxx"
                if first_line.startswith("# 案件标题:"):
                    title = first_line.replace("# 案件标题:", "").strip()
                    if title:
                        titles.append(title)
        except Exception:
            continue
    return titles


def _write_case_file(case: dict, case_type: str, case_dir: str) -> bool:
    """
    将单个案例写入 txt 文件.

    Parameters
    ----------
    case : dict
        案例对象, 含 case_title/case_no/court_name/judge_date/case_summary/judgment/cited_laws.
    case_type : str
        案由.
    case_dir : str
        输出目录路径.

    Returns
    -------
    bool
        True=新写入, False=已存在跳过.
    """
    title = (case.get("case_title") or "").strip()
    case_no = (case.get("case_no") or "").strip()
    if not title:
        return False

    # 确定性文件名, 天然去重
    fname = _case_filename(title, case_no)
    fpath = os.path.join(case_dir, fname)
    if os.path.exists(fpath):
        return False  # 已存在, 跳过

    # 提取字段(空值兜底)
    court_name = (case.get("court_name") or "").strip()
    judge_date = (case.get("judge_date") or "").strip()
    summary = (case.get("case_summary") or "").strip()
    judgment = (case.get("judgment") or "").strip()
    cited_laws = case.get("cited_laws") or []
    if isinstance(cited_laws, str):
        cited_laws = [cited_laws]

    # 写入 txt 文件(带头部元信息, 格式与法律法规 txt 一致)
    content = (
        f"# 案件标题: {title}\n"
        f"# 案号: {case_no}\n"
        f"# 案由: {case_type}\n"
        f"# 审理法院: {court_name}\n"
        f"# 裁判日期: {judge_date}\n"
        f"# 数据来源: LLM生成(结构真实)\n\n"
        f"【案情摘要】\n{summary}\n\n"
        f"【判决结果】\n{judgment}\n\n"
        f"【引用法条】\n"
    )
    for law in cited_laws:
        content += f"- {law}\n"

    with open(fpath, "w", encoding="utf-8") as f:
        f.write(content)
    return True


def generate_cases(keywords: str = "", case_types: list = None,
                   count_per_type: int = 6) -> int:
    """
    生成裁判案例并落地为 txt 文件.

    Parameters
    ----------
    keywords : str
        非空时作为一个"案由"生成; 为空时按 case_types 或默认案由列表.
    case_types : list[str], optional
        案由列表; 为 None 时用 DEFAULT_CASE_TYPES.
    count_per_type : int
        每个案由生成案例数, 默认 6.

    Returns
    -------
    int
        本次新增的案例数(去重后).
    """
    # 确定案由列表
    if keywords.strip():
        types = [keywords.strip()]
    elif case_types:
        types = case_types
    else:
        types = DEFAULT_CASE_TYPES

    print(f"[CasesCollector] 开始生成案例, 案由 {len(types)} 类, 每类 {count_per_type} 个")

    # 输出根目录: __001__clawler/裁判案例/
    output_root = os.path.join(root_dir, "__001__clawler", "裁判案例")
    os.makedirs(output_root, exist_ok=True)

    total = 0
    for case_type in types:
        # 按案由分目录
        case_dir = os.path.join(output_root, case_type)
        os.makedirs(case_dir, exist_ok=True)

        # 读取已有标题, 避免重复
        avoid = _existing_titles(case_type, case_dir)
        # 调用 LLM 生成案例
        cases = llm_generate_cases(case_type, count_per_type, avoid_titles=avoid)
        if not cases:
            print(f"[CasesCollector] {case_type}: 未生成(可能未配置 LLM key)")
            continue

        # 逐个写入文件
        written = 0
        for c in cases:
            if _write_case_file(c, case_type, case_dir):
                written += 1
        total += written
        print(f"[CasesCollector] {case_type}: 新增 {written} 个案例")

    print(f"[CasesCollector] 生成完成, 共新增 {total} 个案例")
    print(f"[CasesCollector] 输出目录: {output_root}")
    return total


if __name__ == "__main__":
    # 直接运行时, 按默认案由生成案例
    generate_cases()
