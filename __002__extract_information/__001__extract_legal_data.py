"""
法律知识图谱数据抽取脚本 (双层关系架构)
========================================

# ============================================================
# 文件名称: __002__extract_information/__001__extract_legal_data.py
# 文件作用: 从 5 类法律 TXT 知识源中, 按"条款粒度"调用 LLM 抽取实体与关系, 落盘为 JSON
# ============================================================
# 【这个文件是干什么的？】
#   本文件是"造图谱原料"的脚本。它负责把 humans 写的、散落在 .txt 里的法律文本,
#   切成一个个"条款", 再调用大语言模型(LLM)从每条条款中抽出:
#     - 实体(法律概念、主体角色、行为、条件、处罚、责任 ...)
#     - 关系(条款定义了什么概念、规范了什么行为、行为导致什么后果 ...)
#   并且每条关系都带上"溯源信息"(来自哪个文件、哪条、原文是什么), 保证可回溯。
#   最终所有抽取结果汇总成一个 JSON 文件, 供后续 __003__create_neo4j_database 导入图数据库。
#
# 【代码逻辑主线】
#   1. 常量定义: 实体类型清单、关系类型清单、Few-Shot 示例、主 Prompt 模板;
#   2. extract_legal_knowledge()  : 单条条款 -> 调用 LLM -> 返回结构化实体/关系;
#   3. load_existing_results()    : 断点续抽——已处理过的文件跳过, 不重复花钱;
#   4. save_results()             : 把结果 JSON 落盘(原子覆盖);
#   5. parse_legal_file()/parse_case_file(): 把不同格式的 .txt 解析成"条款列表";
#   6. extract_from_folder()      : 串联以上, 5 线程并行处理一个知识源文件夹, 汇总结果;
#   7. __main__                   : 依次处理 5 个知识源(laws/regulations/.../cases)。
#
# 【新手建议】
#   1) 先读文件顶部的"双层关系架构"说明, 理解为什么要分 Article 锚定层 与 Entity 关联层;
#   2) 再看 extract_legal_knowledge(): 它就是"给 LLM 一段文本, 还我结构化 JSON";
#   3) 再看 parse_legal_file(): 理解"条款是怎么从 txt 里切出来的";
#   4) 最后看 extract_from_folder(): 它是真正跑全流程的发动机(含并行与断点续抽)。
#
# 📜 代码文字逻辑解析 (what / why / how)
#   WHAT : 把法律 txt 变成"实体-关系"结构化数据(JSON), 且关系带溯源。
#   WHY  : 图数据库(Neo4j)需要结构化三元组才能建图; 纯文本进不去。LLM 是最省力的
#          抽取器, 但必须约束输出格式(JSON Schema)、给出示例(Few-Shot)、并要求"带溯源",
#          否则结果无法审计、无法定位原文。双层关系(条款锚定 + 实体关联)让图谱既"可溯源"
#          又能"做推理"。
#   HOW  : 用 LangChain 的 PromptTemplate + JsonOutputParser + LLM 组成 chain; 用正则把
#          txt 按"第X条"切条; 用 ThreadPoolExecutor 并行抽多个文件; 用线程锁 + 即时落盘
#          保证并行下结果不丢、可续抽。
#
# ------------------------------------------------------------------
# 双层关系架构(核心设计, 务必理解)
# ------------------------------------------------------------------
# 第一层: Article 锚定层 — 以条款为中心, 确保溯源
# 第二层: Entity 关联层  — 实体间语义连接, 支持推理
#
# 从5类知识源的 TXT 文件中, 按条款粒度调用 LLM 抽取实体与关系.
#
# 实体类型 (6类, 仅从正文抽取):
#  LegalConcept  法律概念/术语
#  PartyRole     当事人/主体角色
#  Action        行为/行为模式
#  Condition     条件/情形
#  Penalty       处罚/法律后果
#  Liability     责任类型
#
# 关系类型:
#  【Article 锚定层 — 必须带溯源属性】
#  DEFINES       Article → LegalConcept   条款定义了某概念
#  REGULATES     Article → Action         条款规范了某行为
#  HAS_CONDITION Article → Condition      条款设定了适用条件
#  HAS_PENALTY   Article → Penalty        条款规定了某处罚
#  HAS_LIABILITY Article → Liability      条款明确了某责任
#  INVOLVES      (Article/Case) → PartyRole  条款/案例涉及的当事人
#  CITES         Case → Law              案例引用某法律
#
#  【Entity 关联层 — 实体间语义关系, 也需带溯源属性】
#  CAUSES        Action → Penalty         行为导致某处罚
#  LEADS_TO      Action → Liability       行为导致某责任
#  INCLUDES      Liability → LegalConcept/Penalty 责任包含某概念/处罚
#  ESTABLISHES   Action → LegalConcept    行为确立某法律权利/概念
#  RELATED_TO    LegalConcept ↔ LegalConcept 概念间关联
#  PERFORMED_BY  Action → PartyRole       行为由谁做出
#
# 核心设计原则:
#  - 所有关系 (Article锚定层 + Entity关联层) 都必须携带 provenance
#  - Entity关联层的 provenance 指向"发现该关系的条款", 确保可追溯
# ------------------------------------------------------------------
"""

# 导入 json: 用于读取/写入抽取结果 JSON、序列化微调数据
import json

# 导入 os: 用于路径拼接、判断文件是否存在、创建目录
import os

# 导入 threading: 并行抽取时, 用 Lock 保证多线程写同一个 JSON 文件不冲突
import threading

# 导入 re: 用于正则解析 txt 中的"第X条"条款边界
import re

# 从 typing 导入类型注解(Optional 可空, List 列表), 提升代码可读性与健壮性
from typing import Optional, List

# 从 langchain_core.output_parsers 导入 JsonOutputParser: 把 LLM 输出的 JSON 字符串安全解析成对象
from langchain_core.output_parsers import JsonOutputParser

# 从 langchain_core.prompts 导入 PromptTemplate: 构造带占位符的可复用提示词模板
from langchain_core.prompts import PromptTemplate

# 从 pydantic 导入 BaseModel: 定义数据模型, 作为 JSON 解析的目标结构(校验字段)
from pydantic import BaseModel

# 从 concurrent.futures 导入线程池与完成回调: 多文件并行抽取, 提升吞吐
from concurrent.futures import ThreadPoolExecutor, as_completed

# 从 tqdm 导入 tqdm: 显示抽取进度条
from tqdm import tqdm

# 从 common.llm 引入统一的 LLM 实例 my_llm(项目所有地方共用同一个模型客户端)
from common.llm import my_llm

# 从 common.path_utils 引入 get_file_path: 把相对项目根的路径解析为绝对路径
from common.path_utils import get_file_path


# 仅由 LLM 从正文中抽取的 6 类实体(图谱的"语义实体")
LLM_EXTRACTED_ENTITY_TYPES = [
    "LegalConcept", "PartyRole", "Action", "Condition", "Penalty", "Liability"
]

# 由系统(非 LLM)生成的实体类型: 文件/法律/条款/知识源等"结构型"节点
SYSTEM_GENERATED_ENTITY_TYPES = [
    "Law", "Regulation", "Interpretation", "IndustryStandard", "Case",
    "Article", "KnowledgeSource"
]

# 全部实体类型的合集(LLM 抽取 + 系统生成)
LEGAL_ENTITY_TYPES = LLM_EXTRACTED_ENTITY_TYPES + SYSTEM_GENERATED_ENTITY_TYPES

# 全部合法的关系类型清单(用于校验/文档, 双层架构共 15 种)
# 注意区分两类来源, 避免误以为都要让 LLM 去抽:
#   1) LLM 抽取的关系(13 种): DEFINES/REGULATES/HAS_CONDITION/HAS_PENALTY/HAS_LIABILITY/
#      INVOLVES/CITES + Entity关联层 CAUSES/LEADS_TO/INCLUDES/ESTABLISHES/RELATED_TO/PERFORMED_BY
#   2) 系统生成的结构边(2 种, 由 __001__legal_graph_importer.py 自动建立, LLM 不输出):
#      BELONGS_TO(Article → 所属文件) / BELONGS_TO_SOURCE(文档 → 知识源)
#   → BELONGS_TO / BELONGS_TO_SOURCE 是图谱骨架, 不要教 LLM 抽取它们
LEGAL_RELATION_TYPES = [
    "DEFINES", "REGULATES", "HAS_CONDITION", "HAS_PENALTY",
    "HAS_LIABILITY", "INVOLVES", "CITES",
    "CAUSES", "LEADS_TO", "INCLUDES", "ESTABLISHES", "RELATED_TO", "PERFORMED_BY",
    "BELONGS_TO", "BELONGS_TO_SOURCE"
]


# =================================================================
# 数据模型定义 (用 pydantic 描述"一次抽取结果"的结构)
# =================================================================

class Provenance(BaseModel):
    """溯源信息模型: 记录"这条关系/实体是从哪来"的, 保证图谱可审计。

    字段:
        source_id (Optional[str]): 知识源 id(laws/regulations/cases ...)
        file_name (Optional[str]): 来源文件名(如 中华人民共和国民法典)
        article_no (Optional[str]): 条款号(如 第577条)
        content   (Optional[str]): 命中该关系的原文片段
    """
    source_id: Optional[str] = None
    file_name: Optional[str] = None
    article_no: Optional[str] = None
    content: Optional[str] = None


class LegalEntity(BaseModel):
    """单个实体模型。

    字段:
        name      (str)          : 实体名称(如 "违约责任")
        type      (str, 默认 LegalConcept): 实体类型
        attributes(Optional[dict]): 额外属性(如 file_name/content 等)
    """
    name: str
    type: str = "LegalConcept"
    attributes: Optional[dict] = None


class LegalRelation(BaseModel):
    """单条关系(边)模型。

    字段:
        subject      (str)          : 起点名称
        subject_type (str, 默认 Article): 起点类型
        relation     (str, 默认 DEFINES): 关系类型
        object       (str)          : 终点名称
        object_type  (str, 默认 LegalConcept): 终点类型
        provenance   (Optional[Provenance]): 溯源信息(核心: 保证可回溯)
    """
    subject: str
    subject_type: str = "Article"
    relation: str = "DEFINES"
    object: str
    object_type: str = "LegalConcept"
    provenance: Optional[Provenance] = None


class LegalKnowledgeGraph(BaseModel):
    """一次抽取的整体结果: 实体列表 + 关系列表。"""
    entities: List[LegalEntity]
    relations: List[LegalRelation]


# =================================================================
# Few-Shot 示例: 展示双层关系抽取(喂给 LLM 学习的标杆样本)
# =================================================================

FEW_SHOT_EXAMPLES = """
--- 示例 1: 民法典第577条 (合同编, 典型的违约条款) ---

输入文本:
"当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任。"
溯源信息: source_id=laws, file_name=中华人民共和国民法典, article_no=第577条

期望输出:
{
  "entities": [
    {"name": "当事人一方", "type": "PartyRole"},
    {"name": "违约责任", "type": "Liability"},
    {"name": "违约责任", "type": "LegalConcept"},
    {"name": "合同义务", "type": "LegalConcept"},
    {"name": "赔偿损失", "type": "Penalty"},
    {"name": "赔偿损失", "type": "LegalConcept"},
    {"name": "继续履行", "type": "Action"},
    {"name": "采取补救措施", "type": "Action"},
    {"name": "不履行合同义务", "type": "Action"},
    {"name": "履行合同义务不符合约定", "type": "Action"},
    {"name": "履行合同义务不符合约定", "type": "Condition"}
  ],
  "relations": [
    {"subject": "第577条", "subject_type": "Article", "relation": "INVOLVES", "object": "当事人一方", "object_type": "PartyRole", "provenance": {"source_id": "laws", "file_name": "中华人民共和国民法典", "article_no": "第577条", "content": "当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任。"}},
    {"subject": "第577条", "subject_type": "Article", "relation": "DEFINES", "object": "合同义务", "object_type": "LegalConcept", "provenance": {"source_id": "laws", "file_name": "中华人民共和国民法典", "article_no": "第577条", "content": "当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任。"}},
    {"subject": "第577条", "subject_type": "Article", "relation": "DEFINES", "object": "赔偿损失", "object_type": "LegalConcept", "provenance": {"source_id": "laws", "file_name": "中华人民共和国民法典", "article_no": "第577条", "content": "当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任。"}},
    {"subject": "第577条", "subject_type": "Article", "relation": "DEFINES", "object": "违约责任", "object_type": "LegalConcept", "provenance": {"source_id": "laws", "file_name": "中华人民共和国民法典", "article_no": "第577条", "content": "当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任。"}},
    {"subject": "第577条", "subject_type": "Article", "relation": "REGULATES", "object": "不履行合同义务", "object_type": "Action", "provenance": {"source_id": "laws", "file_name": "中华人民共和国民法典", "article_no": "第577条", "content": "当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任。"}},
    {"subject": "第577条", "subject_type": "Article", "relation": "REGULATES", "object": "履行合同义务不符合约定", "object_type": "Action", "provenance": {"source_id": "laws", "file_name": "中华人民共和国民法典", "article_no": "第577条", "content": "当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任。"}},
    {"subject": "第577条", "subject_type": "Article", "relation": "REGULATES", "object": "继续履行", "object_type": "Action", "provenance": {"source_id": "laws", "file_name": "中华人民共和国民法典", "article_no": "第577条", "content": "当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任。"}},
    {"subject": "第577条", "subject_type": "Article", "relation": "REGULATES", "object": "采取补救措施", "object_type": "Action", "provenance": {"source_id": "laws", "file_name": "中华人民共和国民法典", "article_no": "第577条", "content": "当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任。"}},
    {"subject": "第577条", "subject_type": "Article", "relation": "HAS_CONDITION", "object": "履行合同义务不符合约定", "object_type": "Condition", "provenance": {"source_id": "laws", "file_name": "中华人民共和国民法典", "article_no": "第577条", "content": "当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任。"}},
    {"subject": "第577条", "subject_type": "Article", "relation": "HAS_PENALTY", "object": "赔偿损失", "object_type": "Penalty", "provenance": {"source_id": "laws", "file_name": "中华人民共和国民法典", "article_no": "第577条", "content": "当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任。"}},
    {"subject": "第577条", "subject_type": "Article", "relation": "HAS_LIABILITY", "object": "违约责任", "object_type": "Liability", "provenance": {"source_id": "laws", "file_name": "中华人民共和国民法典", "article_no": "第577条", "content": "当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任。"}},

    {"subject": "不履行合同义务", "subject_type": "Action", "relation": "CAUSES", "object": "赔偿损失", "object_type": "Penalty", "provenance": {"source_id": "laws", "file_name": "中华人民共和国民法典", "article_no": "第577条", "content": "当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任。"}},
    {"subject": "不履行合同义务", "subject_type": "Action", "relation": "LEADS_TO", "object": "违约责任", "object_type": "Liability", "provenance": {"source_id": "laws", "file_name": "中华人民共和国民法典", "article_no": "第577条", "content": "当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任。"}},
    {"subject": "不履行合同义务", "subject_type": "Action", "relation": "PERFORMED_BY", "object": "当事人一方", "object_type": "PartyRole", "provenance": {"source_id": "laws", "file_name": "中华人民共和国民法典", "article_no": "第577条", "content": "当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任。"}},
    {"subject": "继续履行", "subject_type": "Action", "relation": "PERFORMED_BY", "object": "当事人一方", "object_type": "PartyRole", "provenance": {"source_id": "laws", "file_name": "中华人民共和国民法典", "article_no": "第577条", "content": "当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任。"}},
    {"subject": "采取补救措施", "subject_type": "Action", "relation": "PERFORMED_BY", "object": "当事人一方", "object_type": "PartyRole", "provenance": {"source_id": "laws", "file_name": "中华人民共和国民法典", "article_no": "第577条", "content": "当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任。"}},
    {"subject": "违约责任", "subject_type": "Liability", "relation": "INCLUDES", "object": "赔偿损失", "object_type": "Penalty", "provenance": {"source_id": "laws", "file_name": "中华人民共和国民法典", "article_no": "第577条", "content": "当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任。"}},
    {"subject": "合同义务", "subject_type": "LegalConcept", "relation": "RELATED_TO", "object": "违约责任", "object_type": "LegalConcept", "provenance": {"source_id": "laws", "file_name": "中华人民共和国民法典", "article_no": "第577条", "content": "当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任。"}}
  ]
}

--- 示例 2: 贷款通则第9条 (行业标准, 典型的违约处罚条款) ---

输入文本:
"借款人违反借款合同约定，未按期归还贷款的，贷款人可以按合同约定收取违约金。"
溯源信息: source_id=industry_sources, file_name=贷款通则, article_no=第九条

期望输出:
{
  "entities": [
    {"name": "借款人", "type": "PartyRole"},
    {"name": "贷款人", "type": "PartyRole"},
    {"name": "借款合同", "type": "LegalConcept"},
    {"name": "违约金", "type": "Penalty"},
    {"name": "违约金", "type": "LegalConcept"},
    {"name": "违约金", "type": "Liability"},
    {"name": "收取违约金的权利", "type": "LegalConcept"},
    {"name": "未按期归还贷款", "type": "Action"},
    {"name": "收取违约金", "type": "Action"},
    {"name": "违反借款合同约定", "type": "Condition"},
    {"name": "按合同约定", "type": "Condition"}
  ],
  "relations": [
    {"subject": "第九条", "subject_type": "Article", "relation": "INVOLVES", "object": "借款人", "object_type": "PartyRole", "provenance": {"source_id": "industry_sources", "file_name": "贷款通则", "article_no": "第九条", "content": "借款人违反借款合同约定，未按期归还贷款的，贷款人可以按合同约定收取违约金。"}},
    {"subject": "第九条", "subject_type": "Article", "relation": "INVOLVES", "object": "贷款人", "object_type": "PartyRole", "provenance": {"source_id": "industry_sources", "file_name": "贷款通则", "article_no": "第九条", "content": "借款人违反借款合同约定，未按期归还贷款的，贷款人可以按合同约定收取违约金。"}},
    {"subject": "第九条", "subject_type": "Article", "relation": "DEFINES", "object": "借款合同", "object_type": "LegalConcept", "provenance": {"source_id": "industry_sources", "file_name": "贷款通则", "article_no": "第九条", "content": "借款人违反借款合同约定，未按期归还贷款的，贷款人可以按合同约定收取违约金。"}},
    {"subject": "第九条", "subject_type": "Article", "relation": "DEFINES", "object": "违约金", "object_type": "LegalConcept", "provenance": {"source_id": "industry_sources", "file_name": "贷款通则", "article_no": "第九条", "content": "借款人违反借款合同约定，未按期归还贷款的，贷款人可以按合同约定收取违约金。"}},
    {"subject": "第九条", "subject_type": "Article", "relation": "REGULATES", "object": "未按期归还贷款", "object_type": "Action", "provenance": {"source_id": "industry_sources", "file_name": "贷款通则", "article_no": "第九条", "content": "借款人违反借款合同约定，未按期归还贷款的，贷款人可以按合同约定收取违约金。"}},
    {"subject": "第九条", "subject_type": "Article", "relation": "REGULATES", "object": "收取违约金", "object_type": "Action", "provenance": {"source_id": "industry_sources", "file_name": "贷款通则", "article_no": "第九条", "content": "借款人违反借款合同约定，未按期归还贷款的，贷款人可以按合同约定收取违约金。"}},
    {"subject": "第九条", "subject_type": "Article", "relation": "HAS_CONDITION", "object": "违反借款合同约定", "object_type": "Condition", "provenance": {"source_id": "industry_sources", "file_name": "贷款通则", "article_no": "第九条", "content": "借款人违反借款合同约定，未按期归还贷款的，贷款人可以按合同约定收取违约金。"}},
    {"subject": "第九条", "subject_type": "Article", "relation": "HAS_CONDITION", "object": "按合同约定", "object_type": "Condition", "provenance": {"source_id": "industry_sources", "file_name": "贷款通则", "article_no": "第九条", "content": "借款人违反借款合同约定，未按期归还贷款的，贷款人可以按合同约定收取违约金。"}},
    {"subject": "第九条", "subject_type": "Article", "relation": "HAS_PENALTY", "object": "违约金", "object_type": "Penalty", "provenance": {"source_id": "industry_sources", "file_name": "贷款通则", "article_no": "第九条", "content": "借款人违反借款合同约定，未按期归还贷款的，贷款人可以按合同约定收取违约金。"}},
    {"subject": "第九条", "subject_type": "Article", "relation": "HAS_LIABILITY", "object": "违约金", "object_type": "Liability", "provenance": {"source_id": "industry_sources", "file_name": "贷款通则", "article_no": "第九条", "content": "借款人违反借款合同约定，未按期归还贷款的，贷款人可以按合同约定收取违约金。"}},

    {"subject": "未按期归还贷款", "subject_type": "Action", "relation": "CAUSES", "object": "违约金", "object_type": "Penalty", "provenance": {"source_id": "industry_sources", "file_name": "贷款通则", "article_no": "第九条", "content": "借款人违反借款合同约定，未按期归还贷款的，贷款人可以按合同约定收取违约金。"}},
    {"subject": "未按期归还贷款", "subject_type": "Action", "relation": "LEADS_TO", "object": "违约金", "object_type": "Liability", "provenance": {"source_id": "industry_sources", "file_name": "贷款通则", "article_no": "第九条", "content": "借款人违反借款合同约定，未按期归还贷款的，贷款人可以按合同约定收取违约金。"}},
    {"subject": "未按期归还贷款", "subject_type": "Action", "relation": "PERFORMED_BY", "object": "借款人", "object_type": "PartyRole", "provenance": {"source_id": "industry_sources", "file_name": "贷款通则", "article_no": "第九条", "content": "借款人违反借款合同约定，未按期归还贷款的，贷款人可以按合同约定收取违约金。"}},
    {"subject": "收取违约金", "subject_type": "Action", "relation": "PERFORMED_BY", "object": "贷款人", "object_type": "PartyRole", "provenance": {"source_id": "industry_sources", "file_name": "贷款通则", "article_no": "第九条", "content": "借款人违反借款合同约定，未按期归还贷款的，贷款人可以按合同约定收取违约金。"}},
    {"subject": "收取违约金", "subject_type": "Action", "relation": "ESTABLISHES", "object": "收取违约金的权利", "object_type": "LegalConcept", "provenance": {"source_id": "industry_sources", "file_name": "贷款通则", "article_no": "第九条", "content": "借款人违反借款合同约定，未按期归还贷款的，贷款人可以按合同约定收取违约金。"}},
    {"subject": "违约金", "subject_type": "Liability", "relation": "INCLUDES", "object": "违约金", "object_type": "Penalty", "provenance": {"source_id": "industry_sources", "file_name": "贷款通则", "article_no": "第九条", "content": "借款人违反借款合同约定，未按期归还贷款的，贷款人可以按合同约定收取违约金。"}},
    {"subject": "借款合同", "subject_type": "LegalConcept", "relation": "RELATED_TO", "object": "违约金", "object_type": "LegalConcept", "provenance": {"source_id": "industry_sources", "file_name": "贷款通则", "article_no": "第九条", "content": "借款人违反借款合同约定，未按期归还贷款的，贷款人可以按合同约定收取违约金。"}}
  ]
}

--- 示例 3: 保障农民工工资支付条例 (行政法规, 典型的劳动保护条款) ---

输入文本:
"农民工有按时足额获得工资的权利。任何单位和个人不得拖欠农民工工资。"
溯源信息: source_id=regulations, file_name=保障农民工工资支付条例, article_no=第三条

期望输出:
{
  "entities": [
    {"name": "农民工", "type": "PartyRole"},
    {"name": "单位和个人", "type": "PartyRole"},
    {"name": "工资", "type": "LegalConcept"},
    {"name": "权利", "type": "LegalConcept"},
    {"name": "按时足额获得工资", "type": "Action"},
    {"name": "按时足额获得工资", "type": "Condition"},
    {"name": "拖欠农民工工资", "type": "Action"},
    {"name": "不得拖欠", "type": "Action"},
    {"name": "不得拖欠", "type": "Condition"}
  ],
  "relations": [
    {"subject": "第三条", "subject_type": "Article", "relation": "INVOLVES", "object": "农民工", "object_type": "PartyRole", "provenance": {"source_id": "regulations", "file_name": "保障农民工工资支付条例", "article_no": "第三条", "content": "农民工有按时足额获得工资的权利。任何单位和个人不得拖欠农民工工资。"}},
    {"subject": "第三条", "subject_type": "Article", "relation": "INVOLVES", "object": "单位和个人", "object_type": "PartyRole", "provenance": {"source_id": "regulations", "file_name": "保障农民工工资支付条例", "article_no": "第三条", "content": "农民工有按时足额获得工资的权利。任何单位和个人不得拖欠农民工工资。"}},
    {"subject": "第三条", "subject_type": "Article", "relation": "DEFINES", "object": "工资", "object_type": "LegalConcept", "provenance": {"source_id": "regulations", "file_name": "保障农民工工资支付条例", "article_no": "第三条", "content": "农民工有按时足额获得工资的权利。任何单位和个人不得拖欠农民工工资。"}},
    {"subject": "第三条", "subject_type": "Article", "relation": "DEFINES", "object": "权利", "object_type": "LegalConcept", "provenance": {"source_id": "regulations", "file_name": "保障农民工工资支付条例", "article_no": "第三条", "content": "农民工有按时足额获得工资的权利。任何单位和个人不得拖欠农民工工资。"}},
    {"subject": "第三条", "subject_type": "Article", "relation": "REGULATES", "object": "按时足额获得工资", "object_type": "Action", "provenance": {"source_id": "regulations", "file_name": "保障农民工工资支付条例", "article_no": "第三条", "content": "农民工有按时足额获得工资的权利。任何单位和个人不得拖欠农民工工资。"}},
    {"subject": "第三条", "subject_type": "Article", "relation": "REGULATES", "object": "拖欠农民工工资", "object_type": "Action", "provenance": {"source_id": "regulations", "file_name": "保障农民工工资支付条例", "article_no": "第三条", "content": "农民工有按时足额获得工资的权利。任何单位和个人不得拖欠农民工工资。"}},
    {"subject": "第三条", "subject_type": "Article", "relation": "REGULATES", "object": "不得拖欠", "object_type": "Action", "provenance": {"source_id": "regulations", "file_name": "保障农民工工资支付条例", "article_no": "第三条", "content": "农民工有按时足额获得工资的权利。任何单位和个人不得拖欠农民工工资。"}},
    {"subject": "第三条", "subject_type": "Article", "relation": "HAS_CONDITION", "object": "按时足额获得工资", "object_type": "Condition", "provenance": {"source_id": "regulations", "file_name": "保障农民工工资支付条例", "article_no": "第三条", "content": "农民工有按时足额获得工资的权利。任何单位和个人不得拖欠农民工工资。"}},
    {"subject": "第三条", "subject_type": "Article", "relation": "HAS_CONDITION", "object": "不得拖欠", "object_type": "Condition", "provenance": {"source_id": "regulations", "file_name": "保障农民工工资支付条例", "article_no": "第三条", "content": "农民工有按时足额获得工资的权利。任何单位和个人不得拖欠农民工工资。"}},

    {"subject": "按时足额获得工资", "subject_type": "Action", "relation": "ESTABLISHES", "object": "权利", "object_type": "LegalConcept", "provenance": {"source_id": "regulations", "file_name": "保障农民工工资支付条例", "article_no": "第三条", "content": "农民工有按时足额获得工资的权利。任何单位和个人不得拖欠农民工工资。"}},
    {"subject": "按时足额获得工资", "subject_type": "Action", "relation": "PERFORMED_BY", "object": "农民工", "object_type": "PartyRole", "provenance": {"source_id": "regulations", "file_name": "保障农民工工资支付条例", "article_no": "第三条", "content": "农民工有按时足额获得工资的权利。任何单位和个人不得拖欠农民工工资。"}},
    {"subject": "拖欠农民工工资", "subject_type": "Action", "relation": "PERFORMED_BY", "object": "单位和个人", "object_type": "PartyRole", "provenance": {"source_id": "regulations", "file_name": "保障农民工工资支付条例", "article_no": "第三条", "content": "农民工有按时足额获得工资的权利。任何单位和个人不得拖欠农民工工资。"}},
    {"subject": "不得拖欠", "subject_type": "Action", "relation": "PERFORMED_BY", "object": "单位和个人", "object_type": "PartyRole", "provenance": {"source_id": "regulations", "file_name": "保障农民工工资支付条例", "article_no": "第三条", "content": "农民工有按时足额获得工资的权利。任何单位和个人不得拖欠农民工工资。"}},
    {"subject": "工资", "subject_type": "LegalConcept", "relation": "RELATED_TO", "object": "权利", "object_type": "LegalConcept", "provenance": {"source_id": "regulations", "file_name": "保障农民工工资支付条例", "article_no": "第三条", "content": "农民工有按时足额获得工资的权利。任何单位和个人不得拖欠农民工工资。"}}
  ]
}

--- 示例 4: 案例 (赡养纠纷, 法院判决, 含引用法条) ---

输入文本:
【案情摘要】
原告杨某（84岁）与被告朱某系母子关系。杨某年老多病，无收入来源，朱某作为独子长期不履行赡养义务。杨某起诉要求朱某履行赡养义务。
【判决结果】
法院判决朱某自2024年6月起每月支付杨某赡养费1200元。
【引用法条】
- 中华人民共和国老年人权益保障法
- 中华人民共和国民法典第一千零六十七条
溯源信息: source_id=cases, file_name=case_9b9a89cc41e40733, article_no=杨某诉朱某赡养纠纷案

期望输出:
{
  "entities": [
    {"name": "杨某", "type": "PartyRole"},
    {"name": "朱某", "type": "PartyRole"},
    {"name": "法院", "type": "PartyRole"},
    {"name": "赡养义务", "type": "LegalConcept"},
    {"name": "赡养义务", "type": "Liability"},
    {"name": "赡养费", "type": "LegalConcept"},
    {"name": "赡养费", "type": "Penalty"},
    {"name": "不履行赡养义务", "type": "Action"},
    {"name": "支付赡养费", "type": "Action"},
    {"name": "法院判决", "type": "Action"},
    {"name": "每月支付1200元", "type": "Penalty"},
    {"name": "中华人民共和国老年人权益保障法", "type": "LegalConcept"},
    {"name": "中华人民共和国民法典第一千零六十七条", "type": "LegalConcept"}
  ],
  "relations": [
    {"subject": "杨某诉朱某赡养纠纷案", "subject_type": "Case", "relation": "INVOLVES", "object": "杨某", "object_type": "PartyRole", "provenance": {"source_id": "cases", "file_name": "case_9b9a89cc41e40733", "article_no": "杨某诉朱某赡养纠纷案", "content": "【案情摘要】\n原告杨某（84岁）与被告朱某系母子关系。杨某年老多病，无收入来源，朱某作为独子长期不履行赡养义务。杨某起诉要求朱某履行赡养义务。\n【判决结果】\n法院判决朱某自2024年6月起每月支付杨某赡养费1200元。\n【引用法条】\n- 中华人民共和国老年人权益保障法\n- 中华人民共和国民法典第一千零六十七条"}},
    {"subject": "杨某诉朱某赡养纠纷案", "subject_type": "Case", "relation": "INVOLVES", "object": "朱某", "object_type": "PartyRole", "provenance": {"source_id": "cases", "file_name": "case_9b9a89cc41e40733", "article_no": "杨某诉朱某赡养纠纷案", "content": "【案情摘要】\n原告杨某（84岁）与被告朱某系母子关系。杨某年老多病，无收入来源，朱某作为独子长期不履行赡养义务。杨某起诉要求朱某履行赡养义务。\n【判决结果】\n法院判决朱某自2024年6月起每月支付杨某赡养费1200元。\n【引用法条】\n- 中华人民共和国老年人权益保障法\n- 中华人民共和国民法典第一千零六十七条"}},
    {"subject": "杨某诉朱某赡养纠纷案", "subject_type": "Case", "relation": "INVOLVES", "object": "法院", "object_type": "PartyRole", "provenance": {"source_id": "cases", "file_name": "case_9b9a89cc41e40733", "article_no": "杨某诉朱某赡养纠纷案", "content": "【案情摘要】\n原告杨某（84岁）与被告朱某系母子关系。杨某年老多病，无收入来源，朱某作为独子长期不履行赡养义务。杨某起诉要求朱某履行赡养义务。\n【判决结果】\n法院判决朱某自2024年6月起每月支付杨某赡养费1200元。\n【引用法条】\n- 中华人民共和国老年人权益保障法\n- 中华人民共和国民法典第一千零六十七条"}},
    {"subject": "杨某诉朱某赡养纠纷案", "subject_type": "Case", "relation": "CITES", "object": "中华人民共和国老年人权益保障法", "object_type": "LegalConcept", "provenance": {"source_id": "cases", "file_name": "case_9b9a89cc41e40733", "article_no": "杨某诉朱某赡养纠纷案", "content": "【案情摘要】\n原告杨某（84岁）与被告朱某系母子关系。杨某年老多病，无收入来源，朱某作为独子长期不履行赡养义务。杨某起诉要求朱某履行赡养义务。\n【判决结果】\n法院判决朱某自2024年6月起每月支付杨某赡养费1200元。\n【引用法条】\n- 中华人民共和国老年人权益保障法\n- 中华人民共和国民法典第一千零六十七条"}},
    {"subject": "杨某诉朱某赡养纠纷案", "subject_type": "Case", "relation": "CITES", "object": "中华人民共和国民法典第一千零六十七条", "object_type": "LegalConcept", "provenance": {"source_id": "cases", "file_name": "case_9b9a89cc41e40733", "article_no": "杨某诉朱某赡养纠纷案", "content": "【案情摘要】\n原告杨某（84岁）与被告朱某系母子关系。杨某年老多病，无收入来源，朱某作为独子长期不履行赡养义务。杨某起诉要求朱某履行赡养义务。\n【判决结果】\n法院判决朱某自2024年6月起每月支付杨某赡养费1200元。\n【引用法条】\n- 中华人民共和国老年人权益保障法\n- 中华人民共和国民法典第一千零六十七条"}},
    {"subject": "杨某诉朱某赡养纠纷案", "subject_type": "Case", "relation": "HAS_PENALTY", "object": "赡养费", "object_type": "Penalty", "provenance": {"source_id": "cases", "file_name": "case_9b9a89cc41e40733", "article_no": "杨某诉朱某赡养纠纷案", "content": "【案情摘要】\n原告杨某（84岁）与被告朱某系母子关系。杨某年老多病，无收入来源，朱某作为独子长期不履行赡养义务。杨某起诉要求朱某履行赡养义务。\n【判决结果】\n法院判决朱某自2024年6月起每月支付杨某赡养费1200元。\n【引用法条】\n- 中华人民共和国老年人权益保障法\n- 中华人民共和国民法典第一千零六十七条"}},
    {"subject": "杨某诉朱某赡养纠纷案", "subject_type": "Case", "relation": "HAS_LIABILITY", "object": "赡养义务", "object_type": "Liability", "provenance": {"source_id": "cases", "file_name": "case_9b9a89cc41e40733", "article_no": "杨某诉朱某赡养纠纷案", "content": "【案情摘要】\n原告杨某（84岁）与被告朱某系母子关系。杨某年老多病，无收入来源，朱某作为独子长期不履行赡养义务。杨某起诉要求朱某履行赡养义务。\n【判决结果】\n法院判决朱某自2024年6月起每月支付杨某赡养费1200元。\n【引用法条】\n- 中华人民共和国老年人权益保障法\n- 中华人民共和国民法典第一千零六十七条"}},
    {"subject": "不履行赡养义务", "subject_type": "Action", "relation": "CAUSES", "object": "每月支付1200元", "object_type": "Penalty", "provenance": {"source_id": "cases", "file_name": "case_9b9a89cc41e40733", "article_no": "杨某诉朱某赡养纠纷案", "content": "【案情摘要】\n原告杨某（84岁）与被告朱某系母子关系。杨某年老多病，无收入来源，朱某作为独子长期不履行赡养义务。杨某起诉要求朱某履行赡养义务。\n【判决结果】\n法院判决朱某自2024年6月起每月支付杨某赡养费1200元。\n【引用法条】\n- 中华人民共和国老年人权益保障法\n- 中华人民共和国民法典第一千零六十七条"}},
    {"subject": "不履行赡养义务", "subject_type": "Action", "relation": "LEADS_TO", "object": "赡养费", "object_type": "Penalty", "provenance": {"source_id": "cases", "file_name": "case_9b9a89cc41e40733", "article_no": "杨某诉朱某赡养纠纷案", "content": "【案情摘要】\n原告杨某（84岁）与被告朱某系母子关系。杨某年老多病，无收入来源，朱某作为独子长期不履行赡养义务。杨某起诉要求朱某履行赡养义务。\n【判决结果】\n法院判决朱某自2024年6月起每月支付杨某赡养费1200元。\n【引用法条】\n- 中华人民共和国老年人权益保障法\n- 中华人民共和国民法典第一千零六十七条"}},
    {"subject": "不履行赡养义务", "subject_type": "Action", "relation": "PERFORMED_BY", "object": "朱某", "object_type": "PartyRole", "provenance": {"source_id": "cases", "file_name": "case_9b9a89cc41e40733", "article_no": "杨某诉朱某赡养纠纷案", "content": "【案情摘要】\n原告杨某（84岁）与被告朱某系母子关系。杨某年老多病，无收入来源，朱某作为独子长期不履行赡养义务。杨某起诉要求朱某履行赡养义务。\n【判决结果】\n法院判决朱某自2024年6月起每月支付杨某赡养费1200元。\n【引用法条】\n- 中华人民共和国老年人权益保障法\n- 中华人民共和国民法典第一千零六十七条"}},
    {"subject": "支付赡养费", "subject_type": "Action", "relation": "PERFORMED_BY", "object": "朱某", "object_type": "PartyRole", "provenance": {"source_id": "cases", "file_name": "case_9b9a89cc41e40733", "article_no": "杨某诉朱某赡养纠纷案", "content": "【案情摘要】\n原告杨某（84岁）与被告朱某系母子关系。杨某年老多病，无收入来源，朱某作为独子长期不履行赡养义务。杨某起诉要求朱某履行赡养义务。\n【判决结果】\n法院判决朱某自2024年6月起每月支付杨某赡养费1200元。\n【引用法条】\n- 中华人民共和国老年人权益保障法\n- 中华人民共和国民法典第一千零六十七条"}},
    {"subject": "法院判决", "subject_type": "Action", "relation": "ESTABLISHES", "object": "赡养义务", "object_type": "LegalConcept", "provenance": {"source_id": "cases", "file_name": "case_9b9a89cc41e40733", "article_no": "杨某诉朱某赡养纠纷案", "content": "【案情摘要】\n原告杨某（84岁）与被告朱某系母子关系。杨某年老多病，无收入来源，朱某作为独子长期不履行赡养义务。杨某起诉要求朱某履行赡养义务。\n【判决结果】\n法院判决朱某自2024年6月起每月支付杨某赡养费1200元。\n【引用法条】\n- 中华人民共和国老年人权益保障法\n- 中华人民共和国民法典第一千零六十七条"}},
    {"subject": "法院判决", "subject_type": "Action", "relation": "PERFORMED_BY", "object": "法院", "object_type": "PartyRole", "provenance": {"source_id": "cases", "file_name": "case_9b9a89cc41e40733", "article_no": "杨某诉朱某赡养纠纷案", "content": "【案情摘要】\n原告杨某（84岁）与被告朱某系母子关系。杨某年老多病，无收入来源，朱某作为独子长期不履行赡养义务。杨某起诉要求朱某履行赡养义务。\n【判决结果】\n法院判决朱某自2024年6月起每月支付杨某赡养费1200元。\n【引用法条】\n- 中华人民共和国老年人权益保障法\n- 中华人民共和国民法典第一千零六十七条"}},
    {"subject": "赡养义务", "subject_type": "LegalConcept", "relation": "RELATED_TO", "object": "赡养费", "object_type": "LegalConcept", "provenance": {"source_id": "cases", "file_name": "case_9b9a89cc41e40733", "article_no": "杨某诉朱某赡养纠纷案", "content": "【案情摘要】\n原告杨某（84岁）与被告朱某系母子关系。杨某年老多病，无收入来源，朱某作为独子长期不履行赡养义务。杨某起诉要求朱某履行赡养义务。\n【判决结果】\n法院判决朱某自2024年6月起每月支付杨某赡养费1200元。\n【引用法条】\n- 中华人民共和国老年人权益保障法\n- 中华人民共和国民法典第一千零六十七条"}}
  ]
}
"""


# =================================================================
# 主 Prompt 模板(喂给 LLM 的"任务说明书")
# =================================================================
# 创建 JSON 解析器: 把 LLM 输出按 LegalKnowledgeGraph 模型结构解析/校验
parser = JsonOutputParser(pydantic_object=LegalKnowledgeGraph)

# 构造提示词模板: 内含实体/关系定义、抽取规则、Few-Shot 示例、输出格式约束
# 使用 python 格式(str.format 风格), 避免 f-string 把 JSON 花括号误解析为变量
prompt_template = PromptTemplate(
    # template 为多段拼接字符串, 描述"你是谁、抽什么、怎么抽"
    # template_format="python" 表示用 str.format() 风格(而非 f-string),
    # 这样模板内的 JSON 花括号 { } 不会被误判为变量占位符
    template=(
        "你是一位资深法律信息抽取专家，擅长从法律文本中识别实体与关系并构建知识图谱。\n\n"
        "## 任务\n"
        "从下方输入文本中抽取实体与关系，输出符合指定格式的 JSON。\n\n"
        "## 实体类型定义（仅从正文抽取以下 6 类实体）\n\n"
        "1. LegalConcept — 法律概念/术语\n"
        "   定义: 法律文本中出现的具有独立法律意义的名词性概念\n"
        "   示例: 违约金、合同、违约责任、物权、债权、劳动争议、赡养义务、抵押权、工资、劳动合同、劳动关系\n\n"
        "2. PartyRole — 当事人/主体角色\n"
        "   定义: 法律关系中的参与方角色\n"
        "   示例: 用人单位、劳动者、农民工、原告、被告、贷款人、借款人\n\n"
        "3. Action — 行为/行为模式\n"
        "   定义: 主体可以做出或被要求做出的动作\n"
        "   示例: 不履行合同义务、解除合同、支付违约金、赔偿损失、拖欠工资\n\n"
        "4. Condition — 条件/情形/前提\n"
        "   定义: 触发某项法律后果或权利义务的前提条件\n"
        "   示例: 不可抗力、违反借款合同约定、履行合同义务不符合约定\n\n"
        "5. Penalty — 处罚/法律后果\n"
        "   定义: 违反义务后产生的具体不利后果\n"
        "   特征: 通常带有金额、期限等具体内容\n"
        "   示例: 违约金(带金额)、赔偿金、每月支付1200元\n\n"
        "6. Liability — 责任类型\n"
        "   定义: 违反义务后应承担的责任种类\n"
        "   特征: 通常以'...责任'结尾\n"
        "   示例: 违约责任、侵权责任、连带责任、赡养责任\n\n"
        "## 关系类型定义（双层架构）\n\n"
        "### 第一层: Article 锚定层 — 以条款为中心, 确保溯源\n\n"
        "1. DEFINES: Article → LegalConcept\n"
        "   条款定义/使用了某法律概念\n"
        "   示例: 第577条 DEFINES 合同义务\n\n"
        "2. REGULATES: Article → Action\n"
        "   条款规范(允许/禁止/要求)某行为\n"
        "   示例: 第577条 REGULATES 不履行合同义务\n\n"
        "3. HAS_CONDITION: Article → Condition\n"
        "   条款设定了法律规则的适用条件\n"
        "   示例: 第577条 HAS_CONDITION 履行合同义务不符合约定\n\n"
        "4. HAS_PENALTY: Article → Penalty\n"
        "   条款规定了违反义务后的处罚\n"
        "   示例: 第577条 HAS_PENALTY 赔偿损失\n\n"
        "5. HAS_LIABILITY: Article → Liability\n"
        "   条款明确了违反义务后的责任类型\n"
        "   示例: 第577条 HAS_LIABILITY 违约责任\n\n"
        "6. INVOLVES: (Article/Case) → PartyRole\n"
        "   条款或案例涉及的当事人\n"
        "   示例: 第三条 INVOLVES 农民工 / 杨某诉朱某案 INVOLVES 朱某\n\n"
        "7. CITES: Case → Law/Regulation\n"
        "   案例引用/适用了某法律或法规(案例层最核心的关系, 用于打通案例与法条)\n"
        "   示例: 杨某诉朱某赡养纠纷案 CITES 中华人民共和国民法典\n\n"
        "### 第二层: Entity 关联层 — 实体间语义连接, 支持推理\n\n"
        "8. CAUSES: Action → Penalty\n"
        "   行为直接导致了某处罚\n"
        "   示例: 不履行合同义务 CAUSES 赔偿损失\n\n"
        "9. LEADS_TO: Action → Liability\n"
        "   行为导致了某责任\n"
        "   示例: 不履行合同义务 LEADS_TO 违约责任\n\n"
        "10. INCLUDES: Liability → LegalConcept/Penalty\n"
        "    责任包含的具体内容\n"
        "    示例: 违约责任 INCLUDES 赔偿损失\n\n"
        "11. ESTABLISHES: Action → LegalConcept\n"
        "    行为确立/赋予了某法律权利或概念\n"
        "    示例: 按时足额获得工资 ESTABLISHES 权利\n\n"
        "12. RELATED_TO: LegalConcept → LegalConcept\n"
        "    概念间的关联关系\n"
        "    示例: 合同义务 RELATED_TO 违约责任\n\n"
        "13. PERFORMED_BY: Action → PartyRole\n"
        "    行为由谁做出\n"
        "    示例: 拖欠农民工工资 PERFORMED_BY 单位和个人\n\n"
        "## 抽取规则（必须严格遵守）\n\n"
        "1. **每条关系必须携带 provenance**, 指向发现该关系的条款或案例\n"
        "2. **优先抽取 LegalConcept**: 这是检索的核心\n"
        "3. **Entity关联层的关系只在以下情况抽取**:\n"
        "   - CAUSES: 文本明确表明'行为X导致了处罚Y'\n"
        "   - LEADS_TO: 文本明确表明'行为X导致了责任Y'\n"
        "   - INCLUDES: 文本明确表明'责任X包含Y内容'(可以是Penalty或LegalConcept)\n"
        "   - ESTABLISHES: 文本明确表明'行为X确立/赋予了权利或概念Y'\n"
        "   - PERFORMED_BY: 文本明确表明'行为X由角色Y做出'\n"
        "   - RELATED_TO: 两个概念在同一语境下明确相关\n"
        "4. **在原文可定位的前提下尽量多抽**: 只要关系能在原文中找到依据就抽取, 避免凭空捏造不存在的关系\n"
        "5. **同名实体可多次输出(不同类型)**: 同一名称的实体如果同时属于多种类型, 可以在 entities 数组中出现多次, 每次带不同的 type. 如'违约金'既是 LegalConcept 又是 Penalty, 输出两条: {name:'违约金', type:'LegalConcept'} 和 {name:'违约金', type:'Penalty'}. 导入图数据库时会自动合并为一个节点, 打上所有类型标签\n"
        "6. **锚点规则(关键, 决定关系挂在哪里)**:\n"
        "   - 法律/法规/解释/标准类文本: 关系起点用 Article 节点(subject_type=Article), 仅使用 DEFINES/REGULATES/HAS_CONDITION/HAS_PENALTY/HAS_LIABILITY/INVOLVES\n"
        "   - 案例类文本: 关系起点用 Case 节点(subject_type=Case); 禁止 Article 锚定层关系(DEFINES/REGULATES/HAS_CONDITION/HAS_PENALTY/HAS_LIABILITY), 因为案例是'适用'法律而非'定义'法律; 实体关联层(CAUSES/PERFORMED_BY/RELATED_TO 等)仍可按规则3抽取, 且 INVOLVES/CITES 照常使用\n"
        "   - DEFINES 只用于 Article → LegalConcept, 不要把它用在 Liability/Penalty 对象上(后者用 HAS_LIABILITY / HAS_PENALTY)\n\n"
        "## 负面示例\n\n"
        "- 不要抽取数字、日期、标点作为实体\n"
        "- 不要将整个段落作为实体\n"
        "- CAUSES/LEADS_TO 仅在文本明确表达因果时抽取, 不要臆造因果\n"
        "- Entity关联层关系仅在规则3所列情形抽取, 不要编造其他类型\n\n"
        "## Few-Shot 示例 (请学习示例的抽取粒度和关系类型)\n\n"
        + FEW_SHOT_EXAMPLES + "\n\n"          # jinja2 中 { } 是字面量, FEW_SHOT_EXAMPLES 不需要转义
        "## 输出格式\n"
        "所有输出必须严格符合以下 JSON 格式：\n"
        "{{ format_instructions }}\n\n"          # 由 JsonOutputParser 注入的 JSON Schema 约束
        "## 输入\n"
        "溯源信息:\n"
        "  source_id: {{ source_id }}\n"
        "  file_name: {{ file_name }}\n"
        "  article_no: {{ article_no }}\n\n"
        "输入文本:\n{{ text }}"
    ),
    # 需要运行时填充的变量(每条条款的溯源信息 + 正文)
    input_variables=["text", "source_id", "file_name", "article_no"],
    # 预设变量: 把 JSON 格式说明注入模板(LLM 据此输出可被解析的 JSON)
    partial_variables={"format_instructions": parser.get_format_instructions()},
    # 使用 jinja2 格式: { } 是字面量, {{ }} 才是变量占位符
    # 这样模板中的 JSON 花括号不需要转义
    template_format="jinja2"
)


# =================================================================
# 主函数
# =================================================================

def extract_legal_knowledge(text: str, source_id: str, file_name: str, article_no: str):
    """
    单条条款抽取: 把一段法律文本交给 LLM, 返回结构化的实体/关系。

    参数:
        text      (str): 单条条款的原文
        source_id (str): 知识源 id(laws/regulations/cases ...)
        file_name (str): 来源文件名
        article_no(str): 条款号
    返回:
        dict: 形如 {"entities": [...], "relations": [...]} 的解析结果
              解析失败则返回空结构 {}
    逻辑:
        用 prompt_template | my_llm | parser 组成 chain,
        一次性完成"填模板 -> 调 LLM -> 解析 JSON"。
    """
    # 组装 LangChain 链: 模板填入变量 -> LLM 生成 -> 解析为对象
    chain = prompt_template | my_llm | parser
    # 调用链, 传入该条款的溯源信息与正文
    return chain.invoke({
        "text": text,
        "source_id": source_id,
        "file_name": file_name,
        "article_no": article_no
    })


def load_existing_results(save_path: str):
    """
    断点续抽: 读取上一次已保存的结果 JSON。

    参数:
        save_path (str): 结果 JSON 路径
    返回:
        dict: 若文件存在且是 {"results": [...]} 结构则返回它, 否则返回 {"results": []}
    逻辑:
        解析失败(文件损坏)时安全回退为空结果, 不中断流程。
    """
    # 文件存在才尝试读取
    if os.path.exists(save_path):
        with open(save_path, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                # 只认 {"results": [...]} 这种结构, 防止读了别的 json
                if isinstance(data, dict) and "results" in data:
                    return data
            except json.JSONDecodeError:
                # JSON 损坏时直接忽略, 走下方兜底
                pass
    # 兜底: 返回一个空结果容器
    return {"results": []}


def save_results(data: dict, save_path: str):
    """
    把抽取结果 JSON 落盘。

    参数:
        data      (dict): 要保存的结果字典
        save_path (str) : 目标文件路径
    逻辑:
        先确保父目录存在, 再以 utf-8 + 中文不转义 + 缩进 2 写出。
    """
    # 确保保存目录存在(取文件路径的目录; 若为空字符串则用当前目录 '.')
    os.makedirs(os.path.dirname(save_path) if os.path.dirname(save_path) else '.', exist_ok=True)
    # 写 JSON, ensure_ascii=False 保留中文, indent=2 便于阅读
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def parse_legal_file(file_path: str, source_id: str):
    """
    解析"法律/法规/解释/标准"类 txt: 按"第X条"切成一条条条款。

    参数:
        file_path (str): txt 文件路径
        source_id (str): 知识源 id(仅用于回填到每条条款里)
    返回:
        list: [(条款号, 条款正文, 文件名, 知识源), ...]
    约定(数据格式):
        第1行  : # 文件名
        第2、3行: 空行/元信息(被跳过)
        之后    : 每行一条, 形如 "第577条 当事人一方不..." 或 "第九条 ..."
                  遇到空行表示上一条结束
    逻辑:
        用正则 ^(第[一二三...条]) 匹配条款起始行, 后续非空行累加到当前条款正文。
    """
    # 读取整个文件文本
    with open(file_path, "r", encoding="utf-8") as f:
        text = f.read()

    # 按行拆分(去掉首尾空白)
    lines = text.strip().split('\n')
    # 取第 1 行作为文件名(去 "# " 前缀, 再去掉扩展名), 解析失败则回退用文件名
    file_name = lines[0].replace('# ', '').strip() if lines else os.path.basename(file_path)
    file_name = os.path.splitext(file_name)[0]

    # 存放解析出的条款列表
    articles = []
    # 正则: 匹配"第X条"开头(支持中文数字与阿拉伯数字), 捕获条款号与剩余内容
    pattern = re.compile(r'^(第[一二三四五六七八九十百千万零\d]+条)\s*(.*)')

    # 当前正在累积的条款号与正文(初始为空)
    current_no = None
    current_content = ""

    # 从第 4 行开始解析(前 3 行是标题/空行, 非条款)
    for line in lines[3:]:
        line = line.strip()
        # 空行: 表示上一条条款结束, 收尾并重置
        if not line:
            if current_no and current_content:
                # 把累积好的条款加入列表(含文件名与知识源)
                articles.append((current_no, current_content.strip(), file_name, source_id))
                current_no = None
                current_content = ""
            continue

        # 尝试用正则匹配"第X条"起始行
        match = pattern.match(line)
        if match:
            # 若已有未保存的条款, 先收尾
            if current_no and current_content:
                articles.append((current_no, current_content.strip(), file_name, source_id))
            # 更新为新的条款号与(行内剩余)正文
            current_no = match.group(1)
            current_content = match.group(2)
        elif current_no:
            # 非起始行但属于当前条款: 把该行续接到正文
            current_content += line + "\n"

    # 文件末尾若还有未收尾的条款, 补收一次
    if current_no and current_content:
        articles.append((current_no, current_content.strip(), file_name, source_id))

    # 返回条款列表
    return articles


def parse_case_file(file_path: str, source_id: str):
    """
    解析"案例"类 txt: 抽取元信息 + 案情/判决正文, 作为一条整体记录。

    参数:
        file_path (str): 案例 txt 路径
        source_id (str): 知识源 id(固定为 "cases")
    返回:
        list: [(案例标题, 正文, 文件名(去扩展名), "cases")]
    约定(数据格式):
        顶部若干 "# 案件标题: xxx / # 案由: xxx" 形式的元信息行
        正文含 【案情摘要】 / 【判决结果】 等分段标记
    逻辑:
        1) 扫一遍取 # 元信息(标题/案由/法院/日期);
        2) 截取 【案情摘要】+【判决结果】 两段作为抽取正文(取不到则用全文)。
    """
    # 读取整个文件
    with open(file_path, "r", encoding="utf-8") as f:
        text = f.read()

    # 按行拆分
    lines = text.strip().split('\n')
    # 案例文件名(去扩展名)作为默认名
    file_name = os.path.splitext(os.path.basename(file_path))[0]

    # 存放元信息, 如 {"案件标题": ..., "案由": ...}
    meta = {}
    # 遍历每一行, 抓取以 "# " 开头且含 "：" 或 ":" 的"键: 值"元信息
    for line in lines:
        if line.startswith('# '):
            key_val = line[2:].strip()              # 去掉 "# " 前缀
            if '：' in key_val:
                k, v = key_val.split('：', 1)        # 按中文冒号拆分
                meta[k.strip()] = v.strip()
            elif ':' in key_val:
                k, v = key_val.split(':', 1)         # 按英文冒号拆分
                meta[k.strip()] = v.strip()

    # 拼接需要抽取的正文: 取 【案情摘要】 + 【判决结果】 + 【引用法条】 三段
    # 注意: 【引用法条】是 CITES 关系的来源, 不能丢弃
    content_parts = []
    for section in ['【案情摘要】', '【判决结果】', '【引用法条】']:
        if section in text:
            idx = text.index(section)                                  # 段起始位置
            # 找下一段的 "【" 作为结束; 没有则用全文结尾
            end_idx = text.index('【', idx + 1) if '【' in text[idx + 1:] else len(text)
            content_parts.append(text[idx:end_idx].strip())            # 截取该段

    # 有内容则合并, 否则退用全文
    content = '\n'.join(content_parts) if content_parts else text
    # 案例标题取元信息的"案件标题", 没有则用文件名
    case_title = meta.get('案件标题', file_name)
    # 案例作为"一条整体"返回(不像法律文件那样拆多条条款)
    return [(case_title, content, file_name, source_id)]


def extract_from_folder(folder_path: str, source_id: str, save_path: str, max_files: int = None):
    """
    核心发动机: 处理一个知识源文件夹, 并行抽取全部 txt, 汇总并落盘。

    参数:
        folder_path (str) : 该知识源的 txt 所在目录
        source_id   (str) : 知识源 id(laws/regulations/cases ...)
        save_path   (str) : 结果 JSON 输出路径
        max_files   (int) : 最多处理几个文件(测试用, None 表示全部)
    返回:
        dict: 全部抽取结果 {"results": [{filename, source_id, extract_dict}, ...]}
    逻辑:
        1) 加载历史结果 -> 算出"待处理文件"(已处理过的跳过, 实现断点续抽);
        2) 同时准备微调数据(若已存在则读取);
        3) 5 线程并行: 每文件 -> 解析条款 -> 逐条调 LLM -> 汇总 -> 加锁写盘;
        4) 打印成功/失败统计。
    """
    # 读取历史结果(用于断点续抽)
    all_results = load_existing_results(save_path)
    # 已处理过的文件名集合
    processed_files = {r["filename"] for r in all_results["results"]}

    # 准备微调数据集(与抽取结果并行产出, 供后续微调 LLM 用)
    finetune_data = []
    ft_path = save_path.replace('.json', '_finetune.json')   # 微调文件同名换后缀
    if os.path.exists(ft_path):
        try:
            with open(ft_path, "r", encoding="utf-8") as f:
                finetune_data = json.load(f)
        except json.JSONDecodeError:
            pass

    # 列出目录下所有 .txt, 按文件名排序(保证可复现)
    txt_files = sorted([f for f in os.listdir(folder_path) if f.endswith(".txt")])
    # 若指定了 max_files, 只取前 N 个(调试用)
    if max_files:
        txt_files = txt_files[:max_files]

    # 打印发现文件数
    print(f"🔍 共发现 {len(txt_files)} 个文本文件。")

    # 过滤掉已处理的文件, 得到待处理列表
    to_process = [f for f in txt_files if f not in processed_files]
    skipped = len(txt_files) - len(to_process)
    if skipped > 0:
        print(f"⏭ 跳过已处理文件：{skipped} 个")
    print(f"📝 待处理文件：{len(to_process)} 个")

    # 线程锁: 多个线程完成时需要同时写同一个 JSON, 用锁防竞争
    lock = threading.Lock()

    def process_one_file(filename):
        """
        处理单个文件(在线程内调用):
          1) 按 source_id 选择解析器(案例 vs 其他);
          2) 逐条条款调 LLM 抽取实体/关系;
          3) 汇总成 extract_dict 与一条微调样本;
          4) 返回 (文件名, 抽取结果, 微调样本, 错误信息)。
        """
        # 拼出文件完整路径
        file_path = os.path.join(folder_path, filename)
        try:
            # 案例源用案例解析器, 其余用条款解析器
            if source_id == "cases":
                articles = parse_case_file(file_path, source_id)
            else:
                articles = parse_legal_file(file_path, source_id)

            # 没解析出任何条款/内容, 记为"未解析到条款"
            if not articles:
                return filename, None, None, "未解析到条款"

            # 累积本文件所有实体、关系，以及用于微调的真实正文
            all_entities = []
            all_relations = []
            all_text = []
            # 记录成功/失败的条款数
            art_ok = 0
            art_fail = 0
            # 取文件名前缀用于日志
            short_name = os.path.splitext(filename)[0][:20]

            # 逐条条款调 LLM 抽取
            for idx, (article_no, content, file_name, src_id) in enumerate(articles, 1):
                if not content:
                    continue

                # 收集正文, 供后续微调样本作为输入
                all_text.append(content)

                # 调用抽取链
                try:
                    result_dict = extract_legal_knowledge(content, src_id, file_name, article_no)
                    # 把本条款的实体/关系并入总列表
                    if result_dict and "entities" in result_dict:
                        all_entities.extend(result_dict["entities"])
                    if result_dict and "relations" in result_dict:
                        all_relations.extend(result_dict["relations"])
                    art_ok += 1
                    # 每 5 条打印一次进度, 或最后一条
                    if idx % 5 == 0 or idx == len(articles):
                        tqdm.write(f"  [{short_name}] {idx}/{len(articles)} 条款已抽取 (成功 {art_ok}, 失败 {art_fail})")
                except Exception as e:
                    art_fail += 1
                    tqdm.write(f"  [{short_name}] 条款 {article_no} 抽取失败: {str(e)[:80]}")

            # 组装本文件的抽取结果(空则给空列表, 不存 None)
            extract_dict = {
                "entities": all_entities if all_entities else [],
                "relations": all_relations if all_relations else []
            }

            # 【修复判定】: 如果所有条款都失败了(art_ok==0), 整个文件应记为失败
            # 防止超时后返回空结果却被误计为"成功"
            if art_ok == 0 and len(articles) > 0:
                return filename, None, None, f"全部 {len(articles)} 条款抽取失败", file_content

            # 组装一条微调样本(供后续训练/蒸馏用)
            # 注意: input 必须包含真实正文, 否则无法用于训练
            input_text = "\n".join(all_text)
            finetune_item = {
                "instruction": "请从以下法律文本中抽取知识图谱结构，包括实体与关系。",
                "input": json.dumps({"text": input_text, "source_id": source_id}, ensure_ascii=False),
                "output": json.dumps(extract_dict, ensure_ascii=False, indent=2)
            }

            # 返回成功: 文件名 + 抽取结果 + 微调样本 + 无错误 + 文件正文(案例源需要)
            # 第 5 个返回值 file_content: 案例源的完整正文, 用于写入 Case 节点
            file_content = input_text if source_id == "cases" else ""
            return filename, extract_dict, finetune_item, None, file_content
        except Exception as e:
            # 单文件失败不影响整体, 返回错误字符串
            return filename, None, None, str(e), ""

    # 打印并行开始信息(5 线程)
    print(f"\n开始并行抽取（5 线程并发）...")
    success_count = 0   # 成功计数
    fail_count = 0      # 失败计数

    # 启动线程池, 最多 5 个worker并行
    with ThreadPoolExecutor(max_workers=5) as executor:
        # 把每个待处理文件提交给线程池, 记录 未来对象->文件名 映射
        futures = {
            executor.submit(process_one_file, filename): filename
            for filename in to_process
        }

        # 进度条: 总数为待处理文件数
        with tqdm(total=len(to_process), desc="处理中...") as pbar:
            # 按"完成顺序"遍历已完成的 future
            for future in as_completed(futures):
                # 取该文件的返回结果(5 元组: 文件名, 抽取结果, 微调样本, 错误, 文件正文)
                filename, result_dict, finetune_item, error, file_content = future.result()
                pbar.update(1)   # 进度 +1

                # 有错误: 打印并计数, 跳过写盘
                if error:
                    tqdm.write(f"❌ 处理失败：{filename}, 错误：{error}")
                    fail_count += 1
                    continue

                # 无错误: 加锁后写盘(多个线程可能同时完成, 锁保证不互相覆盖)
                with lock:
                    # 组装一条结果记录(案例源携带正文 content, 用于写入 Case 节点)
                    record = {
                        "filename": filename,
                        "source_id": source_id,
                        "extract_dict": result_dict,
                    }
                    # 案例源: 把正文存入 record, 供导入器写入 Case 节点
                    if file_content:
                        record["content"] = file_content
                    # 追加到总结果并立即落盘(断点续抽的关键: 即时保存)
                    all_results["results"].append(record)
                    save_results(all_results, save_path)
                    tqdm.write(f"✅ 已保存结果：{filename}")

                    # 同步追加并保存微调数据
                    if finetune_item:
                        finetune_data.append(finetune_item)
                        with open(ft_path, "w", encoding="utf-8") as f:
                            json.dump(finetune_data, f, ensure_ascii=False, indent=2)

                success_count += 1

    # 打印最终统计
    print(f"\n🎯 处理完成，共抽取 {len(all_results['results'])} 个文件结果。成功: {success_count}, 失败: {fail_count}")
    # 返回完整结果
    return all_results


# 直接运行本文件时的入口: 依次处理 5 个知识源
if __name__ == '__main__':
    # 用路径工具解析数据目录与保存目录(相对于项目根)
    base_data = get_file_path("data")
    save_dir = get_file_path("__002__extract_information")

    # 定义 5 个知识源: (文件夹名, source_id, 结果JSON路径, 最大文件数[None=全部])
    sources = [
        ("laws", "laws", f"{save_dir}/extract_law_data.json", None),
        ("regulations", "regulations", f"{save_dir}/extract_regulation_data.json", None),
        ("interpretations", "interpretations", f"{save_dir}/extract_interpretation_data.json", None),
        ("industry_sources", "industry_sources", f"{save_dir}/extract_industry_data.json", None),
        ("cases", "cases", f"{save_dir}/extract_case_data.json", None),
    ]

    # 逐个知识源处理
    for folder, source_id, save_path, max_files in sources:
        # 打印分隔与当前源信息
        print(f"\n{'='*60}")
        print(f"📚 正在处理知识源: {source_id}")
        print(f"   文件夹: {folder}")
        print(f"   保存到: {save_path}")
        print(f"{'='*60}")

        # 拼出该源的数据目录
        folder_path = f"{base_data}/{folder}"
        # 目录不存在则跳过(避免报错)
        if not os.path.exists(folder_path):
            print(f"⚠️ 文件夹不存在: {folder_path}, 跳过")
            continue

        # 调用抽取(目录存在才抽)
        extract_from_folder(folder_path, source_id, save_path, max_files)

    # 全部知识源处理完
    print("\n🎉 全部知识源抽取完成！")
