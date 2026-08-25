# -*- coding: utf-8 -*-
"""
法律文本实体/关系抽取器 (Entity Extractor)
=========================================

【文件定位】
本文件是项目中「唯一」的实体/关系抽取实现，被知识图谱导入流水线
（__003__create_neo4j_database/importer.py）直接复用。
提供统一的 extract_entities(text, doc_type=...) 接口，不再依赖任何独立的抽取脚本。

【数据/状态流转】
  输入: 法律/司法解释/行业标准/案例文本 (TXT 或纯文本段落)
    ↓
  第1级: LLM 抽取 (高精度，使用 common.llm 的 LLM 调用，文档类型感知)
    ↓ 失败时降级
  第2级: 规则抽取 (中精度，正则 + 词典匹配，零依赖，文档类型感知)
    ↓
  第3级: Schema 校验 + 方向过滤 (validate_and_filter)
    ↓
  输出: {entities: [...], relations: [...]}

【文档类型感知 (doc_type)】
  - "law"           法律法规   → 顶层节点标签 Law
  - "regulation"    行政法规/部门规章 → 顶层节点标签 Regulation
  - "interpretation" 司法解释 → 顶层节点标签 Interpretation
  - "industry"      行业标准   → 顶层节点标签 IndustryStandard
  - "case"          裁判案例   → 顶层节点标签 Case

  顶层节点与 Article 均带 category 属性（取值: 法律法规/行政法规·部门规章/司法解释/行业标准/裁判案例），
  便于后续检索智能体按类别过滤。BELONGS_TO 关系指向正确的顶层节点标签。

【微调数据保存】
  每次 LLM 抽取成功后，自动保存 {input_text, output_entities, output_relations}
  三元组到 data/extract_finetune_data.jsonl，供后续模型微调使用。

【依赖关系】
  上游依赖:
    - common/path_utils.py — 提供 root_dir 路径常量
    - common/llm.py — 提供 llm 全局 LLM 客户端
    - 标准库: os, sys, json, re, typing

  下游被依赖:
    - __003__create_neo4j_database/importer.py — 批量导入时调用
    - __003__create_neo4j_database/generate_neo4j_cypher.py — 概念抽取增量
"""

import os                                                    # 操作系统接口，用于路径拼接和目录创建
import sys                                                   # 系统模块，用于 stdout 编码修复
import json                                                  # JSON 解析，用于 LLM 返回解析和微调数据序列化
import re                                                    # 正则表达式，用于条款匹配和 JSON 容错
from typing import List, Dict                                # 类型标注，用于函数签名

# 修复 Windows 下 stdout 编码问题 (某些终端默认 GBK 导致中文乱码)
if sys.stdout.encoding.lower().replace("-", "") != "utf8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from common.path_utils import root_dir                       # 项目根目录路径常量
from common.llm import llm                                   # 项目统一 LLM 客户端 (支持 invoke 调用)

# ============================================================
# 微调数据输出路径
# ============================================================
_FINETUNE_PATH = os.path.join(root_dir, "data", "extract_finetune_data.jsonl")  # LLM 抽取结果的微调数据


# ============================================================
# LLM 抽取 Prompt 模板
# ============================================================
# 包含: 反幻觉约束 + 方向约束 + 实体/关系类型定义 + few-shot 示例占位符
_EXTRACT_PROMPT = """你是一个专业的法律知识图谱构建助手。请从以下{doc_type_name}文本中抽取实体和关系。

【重要提示 — 反幻觉约束】
- 仅从原文中抽取确实存在的概念和关系, 无原文依据禁止编造任何实体或关系
- 对每个 Article(条款), 检查原文是否确实定义了概念(DEFINES)、规范了行为(REGULATES)、包含条件(HAS_CONDITION)、规定了处罚(HAS_PENALTY), 仅在有明确依据时建立关系
- 对每个 Case(案例), 检查原文引用了哪些法条(CITES)、涉及哪些主体(INVOLVES)
- 宁可少抽也不要抽错: 一个不存在的幻觉关系会污染下游检索

【方向约束】
- DEFINES / REGULATES / HAS_CONDITION / HAS_PENALTY 的 subject 必须是 Article, object 必须是 LegalConcept/Action/Condition/Penalty, 方向不可反转
- BELONGS_TO 的 subject 必须是 Article, object 必须是顶层节点(Law/Regulation/Interpretation/IndustryStandard)
- CITES 的 subject 必须是 Case, object 必须是 Law 或 Article

【顶层节点类型】
本文本属于「{doc_type_name}」, 其顶层节点(整部法 / 整部行政法规/部门规章 / 整部司法解释 / 整部行业标准)的标签(label)必须使用: {top_label}
- 法律法规 → 标签 Law
- 行政法规/部门规章 → 标签 Regulation
- 司法解释 → 标签 Interpretation
- 行业标准 → 标签 IndustryStandard
- 案例 → 标签 Case
顶层节点必须带 category 属性, 取值为 "{doc_type_name}", 便于后续检索智能体按类别过滤。

【实体类型定义】(与知识图谱架构对齐)
- Law: 法律, 属性 {{name(名称), article_no(条款编号), content(内容), level(效力层级), status(时效状态), date(生效日期)}}
- Article: 法条, 属性 {{name(条款名), content(内容)}}
- Case: 案例, 属性 {{title(案例标题), court(审理法院), summary(裁判摘要)}}
- LegalConcept: 法律概念/术语, 如"违约金"、"违约责任"
- Subject: 主体, 如"用人单位"、"劳动者"、"原告"、"被告"
- Action: 行为/动作, 如"解除合同"、"赔偿损失"
- Condition: 条件/情形, 如"不可抗力"、"情势变更"
- Penalty: 处罚/后果, 如"赔偿损失"、"支付违约金"
- Party: 当事人/诉讼参与人, 如"张三"、"某公司"(仅案例类型使用)

【关系类型定义】
- BELONGS_TO: (Article) → (Law/Regulation/Interpretation/IndustryStandard)  条款属于哪部法律
- CITES: (Case) → (Article|Law)  案例引用的法条
- INVOLVES: (Case) → (Party)  案例涉及的当事人(原告/被告/第三人等)
- DEFINES: (Article) → (LegalConcept)  该条款定义了哪个法律概念
- REGULATES: (Article) → (Action)  该条款规范了哪种行为
- HAS_CONDITION: (Article) → (Condition)  适用的条件/情形
- HAS_PENALTY: (Article) → (Penalty)  违反的后果/处罚

【输出格式】
请严格按以下 JSON 格式输出, 不要包含其他文字。只抽取原文中确实存在的实体和关系:
{{
  "entities": [
    {{"name": "实体名称", "type": "实体类型", "attributes": {{"description": "简短描述", "category": "{doc_type_name}"}}}}
  ],
  "relations": [
    {{"subject": "实体名称", "subject_type": "实体类型", "relation": "关系类型", "object": "目标名称", "object_type": "目标类型"}}
  ]
}}

【示例】(few-shot, 请严格参照下述结构)
{few_shot_example}

【{doc_type_name}文本】
{text}
"""

# ============================================================
# 文档类型 → 顶层节点标签 / 类别名 (供抽取与写入使用)
# ============================================================
_TOP_LABEL = {
    "law": "Law",
    "regulation": "Regulation",            # 行政法规/部门规章/地方性法规
    "interpretation": "Interpretation",
    "industry": "IndustryStandard",
    "case": "Case",                        # 裁判案例顶层节点
}

_DOC_TYPE_HINT = {
    "law": "法律法规",
    "regulation": "行政法规/部门规章",
    "interpretation": "司法解释",
    "industry": "行业标准",
    "case": "裁判案例",
}

# ============================================================
# Few-shot 示例 (按文档类型自适应)
# 每个示例给出「输入片段 → 期望 JSON 输出」，示范正确的实体/关系抽取格式
# ============================================================
_FEW_SHOT_BLOCKS = {
    "law": """【示例 · 法律法规】
输入片段:
# 中华人民共和国民法典
第577条 当事人一方不履行合同义务或者履行合同义务不符合约定的, 应当承担继续履行、采取补救措施或者赔偿损失等违约责任。
第584条 当事人一方不履行合同义务或者履行合同义务不符合约定, 造成对方损失的, 损失赔偿额应当相当于因违约所造成的损失, 包括合同履行后可以获得的利益; 但是, 不得超过违约一方订立合同时预见到或者应当预见到的因违约可能造成的损失。

期望输出:
{
  "entities": [
    {"name": "中华人民共和国民法典", "type": "Law", "attributes": {"description": "中华人民共和国民法典", "category": "法律法规"}},
    {"name": "第577条", "type": "Article", "attributes": {"description": "第577条", "category": "法律法规"}},
    {"name": "第584条", "type": "Article", "attributes": {"description": "第584条", "category": "法律法规"}},
    {"name": "违约责任", "type": "LegalConcept", "attributes": {"description": "违约方应承担的责任形式", "category": "法律法规"}},
    {"name": "赔偿损失", "type": "Penalty", "attributes": {"description": "违约造成的损害赔偿", "category": "法律法规"}},
    {"name": "不可抗力", "type": "Condition", "attributes": {"description": "不能预见、不能避免且不能克服的客观情况", "category": "法律法规"}}
  ],
  "relations": [
    {"subject": "第577条", "subject_type": "Article", "relation": "BELONGS_TO", "object": "中华人民共和国民法典", "object_type": "Law"},
    {"subject": "第584条", "subject_type": "Article", "relation": "BELONGS_TO", "object": "中华人民共和国民法典", "object_type": "Law"},
    {"subject": "第577条", "subject_type": "Article", "relation": "DEFINES", "object": "违约责任", "object_type": "LegalConcept"},
    {"subject": "第584条", "subject_type": "Article", "relation": "HAS_PENALTY", "object": "赔偿损失", "object_type": "Penalty"},
    {"subject": "第577条", "subject_type": "Article", "relation": "HAS_CONDITION", "object": "不可抗力", "object_type": "Condition"}
  ]
}""",

    "interpretation": """【示例 · 司法解释】
输入片段:
# 最高人民法院关于民事诉讼证据的若干规定
第1条 原告向人民法院起诉或者被告提出反诉, 应当提供符合起诉条件的相应的证据。
第2条 人民法院应当向当事人说明举证的要求及法律后果, 促使当事人在合理期限内积极、全面、正确、诚实地完成举证。

期望输出:
{
  "entities": [
    {"name": "最高人民法院关于民事诉讼证据的若干规定", "type": "Interpretation", "attributes": {"description": "最高人民法院关于民事诉讼证据的若干规定", "category": "司法解释"}},
    {"name": "第1条", "type": "Article", "attributes": {"description": "第1条", "category": "司法解释"}},
    {"name": "第2条", "type": "Article", "attributes": {"description": "第2条", "category": "司法解释"}},
    {"name": "举证", "type": "Action", "attributes": {"description": "当事人提供证据的行为", "category": "司法解释"}},
    {"name": "举证责任", "type": "LegalConcept", "attributes": {"description": "当事人提供证据承担法律责任的义务", "category": "司法解释"}}
  ],
  "relations": [
    {"subject": "第1条", "subject_type": "Article", "relation": "BELONGS_TO", "object": "最高人民法院关于民事诉讼证据的若干规定", "object_type": "Interpretation"},
    {"subject": "第2条", "subject_type": "Article", "relation": "BELONGS_TO", "object": "最高人民法院关于民事诉讼证据的若干规定", "object_type": "Interpretation"},
    {"subject": "第2条", "subject_type": "Article", "relation": "REGULATES", "object": "举证", "object_type": "Action"}
  ]
}""",

    "regulation": """【示例 · 行政法规/部门规章】
输入片段:
# 中华人民共和国发票管理办法
第一条 为了加强发票管理和财务监督，保障国家税收收入，维护经济秩序，根据《中华人民共和国税收征收管理法》，制定本办法。
第七条 增值税专用发票由国务院税务主管部门确定的企业印制；其他发票，按照国务院税务主管部门的规定，由省、自治区、直辖市税务机关确定的企业印制。禁止私自印制、伪造、变造发票。

期望输出:
{
  "entities": [
    {"name": "中华人民共和国发票管理办法", "type": "Regulation", "attributes": {"description": "中华人民共和国发票管理办法", "category": "行政法规/部门规章"}},
    {"name": "第一条", "type": "Article", "attributes": {"description": "第一条", "category": "行政法规/部门规章"}},
    {"name": "第七条", "type": "Article", "attributes": {"description": "第七条", "category": "行政法规/部门规章"}},
    {"name": "发票", "type": "LegalConcept", "attributes": {"description": "购销商品、提供或接受服务及从事其他经营活动开具、收取的收付款凭证", "category": "行政法规/部门规章"}},
    {"name": "增值税专用发票", "type": "LegalConcept", "attributes": {"description": "增值税专用的发票种类", "category": "行政法规/部门规章"}},
    {"name": "私自印制发票", "type": "Action", "attributes": {"description": "未经许可印制发票的行为", "category": "行政法规/部门规章"}}
  ],
  "relations": [
    {"subject": "第一条", "subject_type": "Article", "relation": "BELONGS_TO", "object": "中华人民共和国发票管理办法", "object_type": "Regulation"},
    {"subject": "第七条", "subject_type": "Article", "relation": "BELONGS_TO", "object": "中华人民共和国发票管理办法", "object_type": "Regulation"},
    {"subject": "第一条", "subject_type": "Article", "relation": "DEFINES", "object": "发票", "object_type": "LegalConcept"},
    {"subject": "第七条", "subject_type": "Article", "relation": "REGULATES", "object": "私自印制发票", "object_type": "Action"}
  ]
}""",

    "industry": """【示例 · 行业标准】
输入片段:
# 城市房屋租赁管理办法
第1条 为加强城市房屋租赁管理, 维护房地产市场秩序, 保障房屋租赁当事人的合法权益, 制定本办法。
第2条 本办法适用于直辖市、市、建制镇规划区内的房屋租赁。

期望输出:
{
  "entities": [
    {"name": "城市房屋租赁管理办法", "type": "IndustryStandard", "attributes": {"description": "城市房屋租赁管理办法", "category": "行业标准"}},
    {"name": "第1条", "type": "Article", "attributes": {"description": "第1条", "category": "行业标准"}},
    {"name": "第2条", "type": "Article", "attributes": {"description": "第2条", "category": "行业标准"}},
    {"name": "房屋租赁当事人", "type": "Subject", "attributes": {"description": "出租人与承租人", "category": "行业标准"}},
    {"name": "房屋租赁", "type": "Action", "attributes": {"description": "房屋出租与承租行为", "category": "行业标准"}}
  ],
  "relations": [
    {"subject": "第1条", "subject_type": "Article", "relation": "BELONGS_TO", "object": "城市房屋租赁管理办法", "object_type": "IndustryStandard"},
    {"subject": "第2条", "subject_type": "Article", "relation": "BELONGS_TO", "object": "城市房屋租赁管理办法", "object_type": "IndustryStandard"},
    {"subject": "第2条", "subject_type": "Article", "relation": "REGULATES", "object": "房屋租赁", "object_type": "Action"}
  ]
}""",

    "case": """【示例 · 裁判案例】
输入片段:
# 案件标题: 张三与李四房屋租赁合同纠纷案
# 案号: (2023)京0102民初1234号
# 案由: 房屋租赁合同纠纷
【案情摘要】原告张三将房屋出租给被告李四, 被告未按约定支付租金。
【判决结果】法院判决被告支付拖欠租金及违约金。
【引用法条】《中华人民共和国民法典》第577条、第584条

期望输出:
{
  "entities": [
    {"name": "张三与李四房屋租赁合同纠纷案", "type": "Case", "attributes": {"description": "房屋租赁合同纠纷案", "category": "裁判案例", "case_no": "(2023)京0102民初1234号"}},
    {"name": "张三", "type": "Party", "attributes": {"description": "原告/出租人", "category": "裁判案例"}},
    {"name": "李四", "type": "Party", "attributes": {"description": "被告/承租人", "category": "裁判案例"}},
    {"name": "租金", "type": "LegalConcept", "attributes": {"description": "房屋租金", "category": "裁判案例"}},
    {"name": "违约金", "type": "LegalConcept", "attributes": {"description": "违约金", "category": "裁判案例"}}
  ],
  "relations": [
    {"subject": "张三与李四房屋租赁合同纠纷案", "subject_type": "Case", "relation": "INVOLVES", "object": "张三", "object_type": "Party"},
    {"subject": "张三与李四房屋租赁合同纠纷案", "subject_type": "Case", "relation": "INVOLVES", "object": "李四", "object_type": "Party"},
    {"subject": "张三与李四房屋租赁合同纠纷案", "subject_type": "Case", "relation": "CITES", "object": "中华人民共和国民法典", "object_type": "Law"},
    {"subject": "张三与李四房屋租赁合同纠纷案", "subject_type": "Case", "relation": "CITES", "object": "第577条", "object_type": "Article"}
  ]
}""",
}


def _parse_doc_title(text: str) -> str:
    """从文本前几行提取文档标题.

    【What】
        扫描文本前 8 行，找到以 '# ' 开头且不是元信息行的行，提取标题文本。

    【Why】
        标题约定为文件首行以 '# ' 开头的行（爬虫/数据准备脚本均把文档名放在第一行），
        但需过滤掉后续以 '# ' 开头的元信息行（来源/生成时间/时效性等）。

    【How】
        1. 定义元信息前缀集合（来源、生成时间等）
        2. 遍历前 8 行，跳过以 '# ' 开头的元信息行
        3. 返回第一个非元信息的标题文本
    """
    _META_PREFIXES = ("来源", "生成时间", "时效性", "制定机关", "公布日期",
                      "施行日期", "条款数", "官方名称")
    for line in text.strip().split("\n")[:8]:
        s = line.strip()
        if s.startswith("#"):
            title = s.lstrip("#").strip()
            if title and not title.startswith(_META_PREFIXES):
                return title
    return ""


# ============================================================
# 规则抽取 (第2级降级, 文档类型感知)
# ============================================================
# 根据文本模式匹配，零外部依赖
# 注: 法律法规多用中文数字(第五百八十四条)，司法解释/行业标准多用阿拉伯数字(第1条/第12条)，
#     故字符集同时覆盖 0-9 与中文数字，避免漏抽绝大多数司法解释/行业标准条文。
# B5: 负向前瞻防"依照/根据/按照(本法)第X条"的引用被误抽为本文档的 Article 实体。
_ARTICLE_PATTERN = re.compile(
    r"(?<!依照)(?<!根据)(?<!按照)(?<!依照本法)(?<!根据本法)"
    r"第[0-9〇零一二三四五六七八九十百千万两]+条(之[0-9〇零一二三四五六七八九十]+)?"
)


# ============================================================
# Schema 校验 + 方向约束 (写入 Neo4j 前最后一道防线)
# ============================================================
# 实体类型白名单 —— 与 generate_neo4j_cypher 下游消费对齐
_VALID_ENTITY_TYPES = {
    "Law", "Regulation", "Interpretation", "IndustryStandard",
    "Article", "Case", "Party",
    "LegalConcept", "Action", "Condition", "Penalty", "Subject",
}
# 关系类型白名单
_VALID_RELATIONS = {
    "BELONGS_TO", "CITES", "INVOLVES",
    "DEFINES", "REGULATES", "HAS_CONDITION", "HAS_PENALTY",
}
# 概念类关系: subject 必须是 Article, object 必须是概念类型
_CONCEPT_REL_OBJECT_TYPES = {
    "DEFINES": {"LegalConcept"},
    "REGULATES": {"Action"},
    "HAS_CONDITION": {"Condition"},
    "HAS_PENALTY": {"Penalty"},
}


def validate_and_filter(result: dict, doc_type: str = "law") -> dict:
    """校验并过滤抽取结果，剔除不符合 schema 的实体/关系，并对方向错误的概念关系做翻转.

    【What】
        对 LLM 或规则抽取的原始结果进行严格的 Schema 校验和方向检查，
        确保输出符合知识图谱的结构要求。

    【Why】
        LLM 输出可能包含非法类型、方向错误的关系或空值，直接写入 Neo4j 会污染图谱。
        本函数作为写入前的「最后一道防线」，确保数据质量。

    【How】
        1. 实体过滤: 检查 type 是否在白名单，name 是否为空
        2. 关系过滤: 检查 relation 是否在白名单
        3. 概念关系方向校验: DEFINES/REGULATES/HAS_CONDITION/HAS_PENALTY 的 subject 必须是 Article
           - 若方向反了（subject 是概念类型，object 是 Article），自动翻转
           - 翻转后仍不满足则丢弃
        4. BELONGS_TO/CITES/INVOLVES 的 subject 类型校验
    """
    if not result:
        return {"entities": [], "relations": []}

    valid_entities = []
    for e in result.get("entities", []):
        etype = e.get("type", "")
        name = (e.get("name") or "").strip()
        if not name:
            continue
        if etype not in _VALID_ENTITY_TYPES:
            print(f"[validate_and_filter] 丢弃非法实体类型: type={etype}, name={name!r}")
            continue
        e["name"] = name                                       # 规范化: 写回清洗后的 name
        valid_entities.append(e)

    valid_relations = []
    for r in result.get("relations", []):
        rel = r.get("relation", "")
        subj = (r.get("subject") or "").strip()
        obj = (r.get("object") or "").strip()
        subj_type = r.get("subject_type", "")
        obj_type = r.get("object_type", "")
        if not subj or not obj:
            continue
        if rel not in _VALID_RELATIONS:
            print(f"[validate_and_filter] 丢弃非法关系: {subj}-[{rel}]->{obj}")
            continue

        # 方向校验: 概念类关系 (DEFINES/REGULATES/HAS_CONDITION/HAS_PENALTY)
        if rel in _CONCEPT_REL_OBJECT_TYPES:
            if subj_type != "Article" and obj_type == "Article":
                # 方向反了 → 翻转 subject/object 及类型
                r["subject"], r["object"] = obj, subj
                r["subject_type"], r["object_type"] = obj_type, subj_type
                subj, obj = obj, subj
                subj_type, obj_type = obj_type, subj_type
                print(f"[validate_and_filter] 翻转关系: Article({obj})-[{rel}]->{subj}")
            if subj_type != "Article":
                print(f"[validate_and_filter] 丢弃: {rel} subject 非 Article: "
                      f"{subj}(type={subj_type})-[{rel}]->{obj}")
                continue
            if obj_type and obj_type not in _CONCEPT_REL_OBJECT_TYPES[rel]:
                print(f"[validate_and_filter] 丢弃: {rel} object 类型不符: "
                      f"{subj}-[{rel}]->{obj}(type={obj_type})")
                continue

        # BELONGS_TO: subject 必须是 Article
        elif rel == "BELONGS_TO":
            if subj_type != "Article":
                print(f"[validate_and_filter] 丢弃: BELONGS_TO subject 非 Article: {subj}")
                continue

        # CITES: subject 必须是 Case
        elif rel == "CITES":
            if subj_type != "Case":
                print(f"[validate_and_filter] 丢弃: CITES subject 非 Case: {subj}")
                continue

        # INVOLVES: subject 必须是 Case
        elif rel == "INVOLVES":
            if subj_type != "Case":
                print(f"[validate_and_filter] 丢弃: INVOLVES subject 非 Case: {subj}")
                continue

        # 规范化: 写回清洗后的字段
        r["subject"] = subj
        r["object"] = obj
        valid_relations.append(r)

    return {"entities": valid_entities, "relations": valid_relations}


def _extract_by_rules(text: str, doc_type: str = "law") -> dict:
    """基于正则 + 词典的规则抽取 (不依赖 LLM), 按 doc_type 决定顶层节点标签.

    【What】
        使用正则匹配条款号和预定义词典匹配概念/行为/条件/处罚，
        构建确定性的实体和关系结构。

    【Why】
        作为 LLM 抽取失败后的降级方案，提供零依赖、可预测的基础抽取能力。
        虽然精度较低（受限于固定词典），但保证输出格式正确、不会完全空白。

    【How】
        1. 对 case 类型直接返回空（案例无"第X条"结构，规则抽取无意义）
        2. 提取文档标题作为顶层节点
        3. 用 _ARTICLE_PATTERN 匹配所有 "第X条" 作为 Article 实体
        4. 对每个 Article，用词典匹配概念/行为/条件/处罚，建立关系
           - common_concepts → DEFINES (Article → LegalConcept)
           - common_actions → REGULATES (Article → Action)
           - common_conditions → HAS_CONDITION (Article → Condition)
           - common_penalties → HAS_PENALTY (Article → Penalty)
    """
    # case 场景规则抽取无意义（案例无"第X条"结构，硬编码词典也不适配裁判文书）
    # LLM 失败降级时直接返回空结构，避免产出无价值噪声
    if doc_type == "case":
        print("[_extract_by_rules] 规则抽取不支持 case 类型(案例无条款结构 + 词典不适配), "
              "返回空结构(由调用方决定是否走确定性骨架)")
        return {"entities": [], "relations": []}

    entities = []
    relations = []
    seen = set()                                              # 去重集合: "{name}|{type}"

    def _add_entity(name, etype, attrs=None):
        """添加实体到列表，自动按 (name, type) 去重."""
        key = f"{name}|{etype}"
        if key not in seen:
            seen.add(key)
            entities.append({"name": name, "type": etype, "attributes": attrs or {}})

    top_label = _TOP_LABEL.get(doc_type, "Law")
    category = _DOC_TYPE_HINT.get(doc_type, "法律法规")

    # 1. 抽取文档标题作为顶层节点（标签由 doc_type 决定）
    title = _parse_doc_title(text)
    top_name = None
    if title:
        _add_entity(title, top_label, {"description": title, "category": category})
        top_name = title

    # 2. 抽取条款编号: 匹配所有 "第X条"
    for match in _ARTICLE_PATTERN.finditer(text):
        art = match.group()
        _add_entity(art, "Article", {"description": art, "category": category})
        if top_name:
            relations.append({
                "subject": art, "subject_type": "Article",
                "relation": "BELONGS_TO",
                "object": top_name, "object_type": top_label,
            })

    # 3. 概念/行为/条件/处罚词典 (预定义的常用法律术语)
    common_concepts = ["违约金", "赔偿", "违约责任", "侵权责任", "不可抗力",
                       "解除合同", "损害赔偿", "过错", "故意", "过失",
                       "合同", "权利", "义务", "责任"]
    common_actions = ["解除", "赔偿", "支付", "履行", "违约", "担保",
                      "通知", "协商", "起诉", "仲裁", "上诉"]
    common_conditions = ["不可抗力", "情势变更", "合同约定", "法律规定",
                         "当事人约定", "协商一致", "提前通知"]
    common_penalties = ["赔偿损失", "支付违约金", "继续履行", "采取补救措施",
                        "解除合同", "承担责任", "返还财产"]

    # 按条款切分文本 (用 "第X条" 作为分隔符)
    # B5: 负向前瞻防"依照/根据/按照本法第X条"被误匹配为新条款起点
    _clause_pattern = re.compile(
        r"(?<!依照)(?<!根据)(?<!按照)(?<!依照本法)(?<!根据本法)"
        r"(第[0-9〇零一二三四五六七八九十百千万两]+条"
        r"(?:之[0-9〇零一二三四五六七八九十]+)?"
        r"(?:第[0-9〇零一二三四五六七八九十]+款)?"
        r"(?:第[0-9〇零一二三四五六七八九十]+项)?)"
        r"\s*(.*?)"
        r"(?=(?:(?<!依照)(?<!根据)(?<!按照)(?<!依照本法)(?<!根据本法)"
        r"第[0-9〇零一二三四五六七八九十百千万两]+条)|$)",
        re.DOTALL
    )
    clause_segments = _clause_pattern.findall(text)

    # 对每个条款，用词典匹配概念并建立关系
    for art_name, art_body in clause_segments:
        art_name = art_name.strip()
        art_body = art_body.strip()
        if not art_body:
            continue

        # 3a. 匹配 LegalConcept (DEFINES 关系)
        for concept in common_concepts:
            if concept in art_body:
                _add_entity(concept, "LegalConcept", {"category": category})
                # 建立 Article → DEFINES → LegalConcept (去重检查)
                if not any(r["subject"] == art_name and r["object"] == concept for r in relations):
                    relations.append({
                        "subject": art_name, "subject_type": "Article",
                        "relation": "DEFINES",
                        "object": concept, "object_type": "LegalConcept",
                    })

        # 3b. 匹配 Action (REGULATES 关系)
        for action in common_actions:
            if action in art_body and len(action) >= 2:
                _add_entity(action, "Action", {"category": category})
                if not any(r["subject"] == art_name and r["object"] == action for r in relations):
                    relations.append({
                        "subject": art_name, "subject_type": "Article",
                        "relation": "REGULATES",
                        "object": action, "object_type": "Action",
                    })

        # 3c. 匹配 Condition (HAS_CONDITION 关系)
        for cond in common_conditions:
            if cond in art_body:
                _add_entity(cond, "Condition", {"category": category})
                if not any(r["subject"] == art_name and r["object"] == cond for r in relations):
                    relations.append({
                        "subject": art_name, "subject_type": "Article",
                        "relation": "HAS_CONDITION",
                        "object": cond, "object_type": "Condition",
                    })

        # 3d. 匹配 Penalty (HAS_PENALTY 关系)
        for penalty in common_penalties:
            if penalty in art_body:
                _add_entity(penalty, "Penalty", {"category": category})
                if not any(r["subject"] == art_name and r["object"] == penalty for r in relations):
                    relations.append({
                        "subject": art_name, "subject_type": "Article",
                        "relation": "HAS_PENALTY",
                        "object": penalty, "object_type": "Penalty",
                    })

    return {"entities": entities, "relations": relations}


def extract_entities(text: str, use_llm: bool = True, doc_type: str = "law") -> dict:
    """从法律/司法解释/行业标准/案例文本中抽取实体和关系 (主接口).

    【What】
        项目唯一的实体/关系抽取入口。支持 LLM 高精度抽取和规则降级两种模式，
        自动进行 Schema 校验和方向过滤。

    【Why】
        统一抽取接口，供 importer.py（知识图谱导入）和 generate_neo4j_cypher.py
        （Cypher 生成）调用，保证所有下游消费方拿到结构一致的输出。

    【How】
        1. 根据 doc_type 确定顶层节点标签和类别名
        2. use_llm=True 时:
           - 文本截断至 12000 字（LLM 上下文窗口限制）
           - 构造 prompt → 调用 llm.invoke() → 解析 JSON → 保存微调数据
           - 失败时自动降级到规则抽取
        3. use_llm=False 时或 LLM 失败:
           - 调用 _extract_by_rules() 正则+词典抽取
        4. 最后统一走 validate_and_filter() 校验

    【数据/状态流转】
        text → [截断] → [LLM/规则抽取] → [解析/校验] → {entities, relations}
    """
    doc_type = doc_type or "law"
    top_label = _TOP_LABEL.get(doc_type, "Law")
    doc_type_name = _DOC_TYPE_HINT.get(doc_type, "法律法规")

    if use_llm:
        try:
            # 文本太长时截断（LLM 上下文窗口有限）
            input_text = text[:12000] if len(text) > 12000 else text

            # 构造 prompt 并调用 LLM
            prompt = _EXTRACT_PROMPT.format(
                text=input_text,
                doc_type_name=doc_type_name,
                top_label=top_label,
                few_shot_example=_FEW_SHOT_BLOCKS.get(doc_type, _FEW_SHOT_BLOCKS["law"]),
            )
            response = llm.invoke(prompt).content              # 调用项目 LLM 客户端

            # 解析 LLM 返回的 JSON
            result = _parse_llm_response(response)
            if result and len(result.get("entities", [])) > 0:
                _save_finetune_data(input_text, result)        # 保存微调数据
                return validate_and_filter(result, doc_type=doc_type)
        except Exception as e:
            print(f"[EntityExtractor] LLM 抽取失败: {e}, 降级到规则抽取")

    # LLM 失败或显式降级 → 走规则抽取
    result = _extract_by_rules(text, doc_type=doc_type)
    return validate_and_filter(result, doc_type=doc_type)


def _parse_llm_response(response: str) -> dict:
    """从 LLM 响应中提取 JSON 部分并解析，带多层容错处理.

    【What】
        从 LLM 输出文本中提取 JSON 实体/关系数据，处理常见的 LLM 输出格式问题。

    【Why】
        LLM 输出不稳定，可能包含 markdown 代码块标记、控制字符、尾逗号等，
        直接 json.loads() 大概率失败。多层容错确保尽可能成功解析。

    【How】
        容错策略（按顺序尝试）:
        1. 提取 ```json ... ``` 代码块或裸 {...} 片段
        2. 清除控制字符（\\x00-\\x1f，除 \\n \\t 外）
        3. 修复尾逗号（]}前多余的,）
        4. 上述均失败则打印原因并返回 None
    """
    # 尝试找 ```json ... ``` 代码块
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response, re.DOTALL)
    if m:
        response = m.group(1)
    # 尝试直接找含 "entities" 的 JSON 片段
    m = re.search(r"\{.*\"entities\".*\}", response, re.DOTALL)
    if not m:
        print("[_parse_llm_response] 未找到含 entities 的 JSON 片段")
        return None

    raw = m.group()
    # 容错1: 清除控制字符（保留 \\n \\r \\t，其余移除）
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e1:
        # 容错2: 修复尾逗号 }, ] 前的多余逗号
        fixed = re.sub(r",\s*([}\]])", r"\1", cleaned)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError as e2:
            print(f"[_parse_llm_response] JSON 解析失败(已尝试清控制字符+修尾逗号): "
                  f"{e2}; 原始片段前 200 字: {raw[:200]!r}")
            return None


def _save_finetune_data(input_text: str, result: dict):
    """保存 LLM 抽取的输入/输出对到微调数据集.

    【What】
        将 {input_text, output_entities, output_relations} 三元组追加写入 JSONL 文件。

    【Why】
        积累高质量的 LLM 抽取结果，供后续模型微调（fine-tuning）使用，
        提升实体/关系抽取的准确率。

    【How】
        1. 确保 data/ 目录存在
        2. 追加写入一行 JSON（JSONL 格式：一行一个 JSON 对象）
        3. 异常时仅打印错误，不中断主流程
    """
    try:
        os.makedirs(os.path.dirname(_FINETUNE_PATH), exist_ok=True)
        with open(_FINETUNE_PATH, "a", encoding="utf-8") as f:
            record = json.dumps({
                "input": input_text,                               # 传给 LLM 的文本（已截断到 12000）
                "output_entities": result["entities"],
                "output_relations": result["relations"],
            }, ensure_ascii=False)
            f.write(record + "\n")
    except Exception as e:
        print(f"[EntityExtractor] 保存微调数据失败: {e}")


def merge_results(results: List[dict]) -> Dict:
    """合并多次抽取结果，按 (name|type) 和完整关系键去重.

    【What】
        将多个 extract_entities() 的返回值合并为一个去重后的结果。

    【Why】
        当一部法被分多次抽取时（如 LLM 分块处理），需要合并结果并去重，
        避免同一实体/关系重复写入图谱。

    【How】
        1. 实体去重: 按 (name, type) 元组
        2. 关系去重: 按 (subject, subject_type, relation, object, object_type) 五元组
           （补充 subject_type/object_type 防止同名不同类型被误去重）
        3. 遍历所有结果，跳过已存在的实体和关系
    """
    seen_entities = set()
    seen_relations = set()
    merged = {"entities": [], "relations": []}

    for r in results:
        for e in r.get("entities", []):
            key = (e.get("name"), e.get("type"))
            if key not in seen_entities:
                seen_entities.add(key)
                merged["entities"].append(e)
        for r_rel in r.get("relations", []):
            key = (
                r_rel.get("subject"), r_rel.get("subject_type"),
                r_rel.get("relation"),
                r_rel.get("object"), r_rel.get("object_type"),
            )
            if key not in seen_relations:
                seen_relations.add(key)
                merged["relations"].append(r_rel)

    return merged


if __name__ == "__main__":
    # 命令行测试: 先用规则抽取验证基本功能
    sample = """# 民法典
    第584条 当事人一方不履行合同义务或者履行合同义务不符合约定, 造成对方损失的,
    损失赔偿额应当相当于因违约所造成的损失, 包括合同履行后可以获得的利益;
    但是, 不得超过违约一方订立合同时预见到或者应当预见到的因违约可能造成的损失。
    """
    result = extract_entities(sample, use_llm=False)           # 先走规则测试，避免 LLM 调用
    print(json.dumps(result, ensure_ascii=False, indent=2))