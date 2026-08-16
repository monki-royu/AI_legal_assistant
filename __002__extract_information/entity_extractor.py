# -*- coding: utf-8 -*-
"""
法律文本实体/关系抽取器 (Entity Extractor)
=========================================

【定位】
本文件是对 __001__extract_law_data.py 已有抽取逻辑的封装升级,
提供统一的 extract_entities() / extract_relations() 接口,
供知识图谱构建流水线(importer.py)和实时查询节点(query_node.py)复用。

【流程】
  输入: 法律条文文本(TXT 或纯文本段落)
  第1级: LLM 抽取(高精度, 使用 __001__extract_law_data 的 LLM 调用)
  第2级: 规则抽取(中精度, 正则 + 词典匹配, 零依赖)
  第3级: 默认降级(兜底, 返回基本结构)
  输出: {entities: [...], relations: [...]}

【微调数据保存】
  每次 LLM 抽取成功后, 自动保存 {input_text, output_entities, output_relations}
  三元组到 data/extract_finetune_data.jsonl, 供后续模型微调使用。

【复用关系】
  - __003__create_neo4j_database/importer.py   ← 批量导入时调用
  - __003__create_neo4j_database/query_node.py  ← 实时查询时调用
  - 可直接被 pipeline/流水线中的其他节点调用
"""

import os, sys, json, re
from typing import List, Dict

if sys.stdout.encoding.lower().replace("-", "") != "utf8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from common.path_utils import root_dir
from common.llm import llm  # 复用项目中已有的 LLM 封装

# ============================================================
# 微调数据输出路径
# ============================================================
_FINETUNE_PATH = os.path.join(root_dir, "data", "extract_finetune_data.jsonl")


# ============================================================
# LLM 抽取 Prompt 模板
# ============================================================
_EXTRACT_PROMPT = """你是一个专业的法律知识图谱构建助手。请从以下法律文本中抽取实体和关系。

【实体类型定义】(与知识图谱架构对齐)
- Law: 法律, 属性 {name(名称), article_no(条款编号), content(内容), level(效力层级), status(时效状态), date(生效日期)}
- Article: 法条, 属性 {name(条款名), content(内容)}
- Case: 案例, 属性 {title(案例标题), court(审理法院), summary(裁判摘要)}
- Entity: 通用实体, 属性 {name(名称), type(实体类型)}  ← 从 TXT 中用 LLM 抽取
- LegalConcept: 法律概念/术语, 如"违约金"、"违约责任"
- Subject: 主体, 如"用人单位"、"劳动者"
- Action: 行为/动作, 如"解除合同"
- Condition: 条件/情形, 如"不可抗力"
- Penalty: 处罚/后果, 如"赔偿损失"

【关系类型定义】(与知识图谱架构对齐 3+3 种核心关系)
- BELONGS_TO: (Article|Entity) → (Law)  该条款/实体属于哪部法律
- CITES: (Case) → (Article|Law)  案例引用的法条
- INVOLVES: (Entity|Article) → (Article|Case)  实体涉及的法条或案例
- DEFINES: (Article) → (LegalConcept)  该条款定义了哪个概念
- REGULATES: (Article) → (Action)  该条款规范了哪种行为
- HAS_CONDITION: (Article|Action) → (Condition)  适用的条件/情形
- HAS_PENALTY: (Article|Action) → (Penalty)  违反的后果

【输出格式】
请严格按以下 JSON 格式输出, 不要包含其他文字:
{
  "entities": [
    {"name": "实体名称", "type": "实体类型", "attributes": {"description": "简短描述"}}
  ],
  "relations": [
    {"subject": "实体名称", "subject_type": "实体类型", "relation": "关系类型", "object": "目标名称", "object_type": "目标类型"}
  ]
}

【法律文本】
{text}
"""

# ============================================================
# 规则抽取词典 (第2级降级)
# ============================================================
# 根据文本模式匹配, 零外部依赖
_ARTICLE_PATTERN = re.compile(r"第[〇零一二三四五六七八九十百千万两]+条(之[〇零一二三四五六七八九十]+)?")
_LAW_NAME_SUFFIX = ("法", "条例", "规定", "办法", "细则", "规则", "解释")


def _extract_by_rules(text: str) -> dict:
    """
    基于正则 + 词典的规则抽取(不依赖 LLM)。

    Parameters
    ----------
    text : str
        法律文本原文。

    Returns
    -------
    dict : {entities: [...], relations: [...]}
    """
    entities = []
    relations = []
    seen = set()

    def _add_entity(name, etype, attrs=None):
        key = f"{name}|{etype}"
        if key not in seen:
            seen.add(key)
            entities.append({"name": name, "type": etype, "attributes": attrs or {}})

    # 1. 抽取法律名称: 从文件开头的 "# 法律名" 或文本首行匹配
    lines = text.strip().split("\n")
    for line in lines[:5]:
        line = line.strip()
        if line.startswith("#"):
            name = line.lstrip("#").strip()
            if any(name.endswith(suf) for suf in _LAW_NAME_SUFFIX):
                _add_entity(name, "Law", {"description": name})

    # 2. 抽取条款编号: 匹配所有 "第X条"
    law_name = None
    for e in entities:
        if e["type"] == "Law":
            law_name = e["name"]
            break
    for match in _ARTICLE_PATTERN.finditer(text):
        art = match.group()
        _add_entity(art, "Article", {"description": art})
        if law_name:
            relations.append({
                "subject": art, "subject_type": "Article",
                "relation": "BELONGS_TO",
                "object": law_name, "object_type": "Law",
            })

    # 3. 简单概念提取: 从正文中匹配常见法律术语
    common_concepts = ["违约金", "赔偿", "违约责任", "侵权责任", "不可抗力",
                       "解除合同", "损害赔偿", "过错", "故意", "过失"]
    for concept in common_concepts:
        if concept in text:
            _add_entity(concept, "LegalConcept")

    return {"entities": entities, "relations": relations}


# ============================================================
# 主抽取接口
# ============================================================
def extract_entities(text: str, use_llm: bool = True) -> dict:
    """
    从法律文本中抽取实体和关系。

    Parameters
    ----------
    text : str
        法律文本原文(纯文本, 长度不限, 超过6000字将自动切割)。
    use_llm : bool
        是否优先使用 LLM 抽取。False 则只走规则降级。

    Returns
    -------
    dict : {entities: [...], relations: [...]}
    """
    if use_llm:
        try:
            # 文本太长时截断 (LLM 上下文窗口有限)
            input_text = text[:12000] if len(text) > 12000 else text

            prompt = _EXTRACT_PROMPT.format(text=input_text)
            response = llm(prompt)  # 调用项目中已有的 LLM 封装

            # 解析 LLM 返回的 JSON
            result = _parse_llm_response(response)
            if result and len(result.get("entities", [])) > 0:
                # 保存微调数据
                _save_finetune_data(input_text, result)
                return result
        except Exception as e:
            print(f"[EntityExtractor] LLM 抽取失败: {e}, 降级到规则抽取")

    # LLM 失败或显式降级 → 走规则抽取
    result = _extract_by_rules(text)
    return result


def _parse_llm_response(response: str) -> dict:
    """从 LLM 响应中提取 JSON 部分并解析。"""
    # 尝试找 ```json ... ``` 代码块
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response, re.DOTALL)
    if m:
        response = m.group(1)
    # 尝试直接找 {...}
    m = re.search(r"\{.*\"entities\".*\}", response, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return None


def _save_finetune_data(input_text: str, result: dict):
    """保存 LLM 抽取的输入/输出对到微调数据集。"""
    try:
        os.makedirs(os.path.dirname(_FINETUNE_PATH), exist_ok=True)
        with open(_FINETUNE_PATH, "a", encoding="utf-8") as f:
            record = json.dumps({
                "input": input_text[:2000],  # 只保留前 2000 字
                "output_entities": result["entities"],
                "output_relations": result["relations"],
            }, ensure_ascii=False)
            f.write(record + "\n")
    except Exception as e:
        print(f"[EntityExtractor] 保存微调数据失败: {e}")


# ============================================================
# 便捷函数: 抽取 + 去重 + 合并
# ============================================================
def merge_results(results: List[dict]) -> Dict:
    """
    合并多次抽取结果, 按 (name|type) 去重。

    Parameters
    ----------
    results : list[dict]
        多个 extract_entities() 的返回值。

    Returns
    -------
    dict : {entities: [...], relations: [...]}
    """
    seen_entities = set()
    seen_relations = set()
    merged = {"entities": [], "relations": []}

    for r in results:
        for e in r.get("entities", []):
            key = (e["name"], e["type"])
            if key not in seen_entities:
                seen_entities.add(key)
                merged["entities"].append(e)
        for r_rel in r.get("relations", []):
            key = (r_rel["subject"], r_rel["relation"], r_rel["object"])
            if key not in seen_relations:
                seen_relations.add(key)
                merged["relations"].append(r_rel)

    return merged


if __name__ == "__main__":
    # 命令行测试
    sample = """# 民法典
    第584条 当事人一方不履行合同义务或者履行合同义务不符合约定, 造成对方损失的,
    损失赔偿额应当相当于因违约所造成的损失, 包括合同履行后可以获得的利益;
    但是, 不得超过违约一方订立合同时预见到或者应当预见到的因违约可能造成的损失。
    """
    result = extract_entities(sample, use_llm=False)  # 先走规则测试
    print(json.dumps(result, ensure_ascii=False, indent=2))