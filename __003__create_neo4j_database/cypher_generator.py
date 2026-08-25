# -*- coding: utf-8 -*-
"""
Cypher 写入语句生成器 (规则模板)
===============================

【定位】
把 entity_extractor.py 输出的实体/关系数据, 经规则模板转换为 MERGE 写入语句。
本文件只负责「生成」, 不负责连接数据库或执行 (执行在 common.neo4j_client)。

【实际流程】
  entity_extractor 抽取实体/关系
        ↓ (LLM 仅用于此处的概念抽取, 不用于 Cypher 生成)
  generate_create_cypher(entities, relations, use_llm=False)
        ↓ 确定性规则模板 (_template_create_cypher)
  MERGE / MATCH-MERGE 写入语句 (分号分隔)

【关于 use_llm 参数】
  generate_create_cypher 默认 use_llm=True, 会先让 LLM 直接生成 Cypher 再降级到
  规则模板; 但写库主路径 generate_neo4j_cypher.py 固定以 use_llm=False 调用
  (逐实体截断不适合批量骨架), 因此线上实际走规则模板分支。use_llm=True 分支
  保留为可选能力, 非主路径。

【调用方】
  - __003__create_neo4j_database/generate_neo4j_cypher.py  ← 写库主路径
"""
import json, re
from typing import List

from common.llm import llm


# ============================================================
# LLM Cypher 生成 Prompt
# ============================================================
_CYPHER_CREATE_PROMPT = """你是一个 Neo4j Cypher 专家。请根据以下实体和关系数据生成 Cypher 写入语句。

【实体】
{entities_json}

【关系】
{relations_json}

要求:
1. 对每个实体生成 MERGE 语句(去重), 如:
   MERGE (n:Law {{name: '民法典', type: 'Law'}})
   ON CREATE SET n.description = '...'
2. 对每个关系生成 MATCH + MERGE 语句
3. 使用参数化查询(如有大量数据)
4. 只输出 Cypher 语句, 不要有解释
5. 每个语句用分号(;)结尾, 语句之间用换行分隔"""


# ============================================================
# 规则模板(LLM 降级兜底)
# ============================================================
def _escape_cypher_str(value) -> str:
    """转义 Cypher 单引号字符串字面量中的反斜杠与单引号。"""
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def split_cypher_statements(cypher: str) -> List[str]:
    """按分号拆分 Cypher 语句, 正确跳过单引号字符串内部的分号与转义引号。

    背景: 条文 content 常含 ASCII 分号(如"…有关的情况;"), 裸 split(";") 会把
    字符串内部的分号当语句边界, 切出引号不配对的残缺语句导致 SyntaxError
    (历史上 importer 半成品写库的隐形原因之一)。本函数逐字符扫描, 仅在
    "不在字符串内"的分号处切分。

    Parameters
    ----------
    cypher : str
        多条 Cypher 语句(分号分隔)。

    Returns
    -------
    List[str] : 不含结尾分号的语句列表(已去空白)。
    """
    stmts = []
    buf = []
    in_str = False
    i, n = 0, len(cypher)
    while i < n:
        ch = cypher[i]
        if in_str:
            if ch == "\\":          # 转义序列(含 \')整体保留
                buf.append(ch)
                if i + 1 < n:
                    buf.append(cypher[i + 1])
                    i += 1
            elif ch == "'":
                in_str = False
                buf.append(ch)
            else:
                buf.append(ch)
        else:
            if ch == "'":
                in_str = True
                buf.append(ch)
            elif ch == ";":
                s = "".join(buf).strip()
                if s:
                    stmts.append(s)
                buf = []
            else:
                buf.append(ch)
        i += 1
    s = "".join(buf).strip()
    if s:
        stmts.append(s)
    return stmts


def _entity_merge_pattern(name: str, etype: str, attrs: dict) -> str:
    """构造实体 MERGE 模式。

    Article 且 attributes 含 law_name 时, law_name 进 MERGE 键 —— 防跨法律
    同名条款坍缩: Article.name 只存条号("第一条"), 105 部法的"第一条"若仅按
    {name, type} MERGE 会合并成一个节点, content 只留第一部法的文本、
    BELONGS_TO 扇出到全部法律(实测 17264 条边挂 1290 个节点)。
    """
    if etype == "Article" and attrs.get("law_name"):
        law = _escape_cypher_str(attrs["law_name"])
        return f"(n:{etype} {{name: '{name}', type: '{etype}', law_name: '{law}'}})"
    return f"(n:{etype} {{name: '{name}', type: '{etype}'}})"


def _rel_endpoint_pattern(var: str, etype: str, name: str, law) -> str:
    """构造关系端点 MATCH 模式。Article 端点带 law(即 law_name) 时加进 MATCH,
    防止 MATCH (a:Article {name:'第一条'}) 命中所有法律的同号条款造成关系扇出。"""
    if etype == "Article" and law:
        return f"({var}:{etype} {{name: '{name}', law_name: '{_escape_cypher_str(law)}'}})"
    return f"({var}:{etype} {{name: '{name}'}})"


def _template_create_cypher(entities: list, relations: list) -> str:
    """基于规则的 Cypher 生成(不依赖 LLM)。"""
    statements = []

    for e in entities:
        name = _escape_cypher_str(e["name"])
        etype = e["type"]
        attrs = e.get("attributes", {}) or {}
        set_clause = ""
        # law_name 已进 MERGE 键, 不再重复出现在 ON CREATE SET
        set_attrs = {k: v for k, v in attrs.items() if k != "law_name"}
        if set_attrs:
            # attr 值过 _escape_cypher_str —— content 含单引号时安全转义
            pairs = ", ".join(
                f"n.{k} = '{_escape_cypher_str(v)}'"
                for k, v in set_attrs.items() if isinstance(v, str)
            )
            if pairs:
                set_clause = f" ON CREATE SET {pairs}"
        statements.append(
            f"MERGE {_entity_merge_pattern(name, etype, attrs)}{set_clause};"
        )

    for r in relations:
        sub = _escape_cypher_str(r["subject"])
        obj = _escape_cypher_str(r["object"])
        stype = r["subject_type"]
        obj_type = r["object_type"]
        rel_type = r["relation"]
        # Article 端点用 subject_law/object_law 精确定位到所属法律的条款节点
        a_pat = _rel_endpoint_pattern("a", stype, sub, r.get("subject_law"))
        b_pat = _rel_endpoint_pattern("b", obj_type, obj, r.get("object_law"))
        statements.append(
            f"MATCH {a_pat}, {b_pat}"
            f" MERGE (a)-[r:{rel_type}]->(b);"
        )

    return "".join(statements)


# ============================================================
# 主接口
# ============================================================
def generate_create_cypher(entities: list, relations: list,
                           use_llm: bool = True) -> str:
    """
    根据实体/关系数据生成 Cypher CREATE 语句。

    Parameters
    ----------
    entities : list[dict]
        entity_extractor.py 输出的实体列表。
    relations : list[dict]
        entity_extractor.py 输出的关系列表。
    use_llm : bool
        是否优先使用 LLM 生成。

    Returns
    -------
    str : Cypher 语句(多条用分号分隔)
    """
    if use_llm:
        try:
            prompt = _CYPHER_CREATE_PROMPT.format(
                entities_json=json.dumps(entities, ensure_ascii=False)[:3000],
                relations_json=json.dumps(relations, ensure_ascii=False)[:3000],
            )
            response = llm.invoke(prompt).content
            cypher = _extract_cypher(response)
            if cypher:
                # 校验 LLM 输出完整性: 语句数明显少于实体数说明输出被截断, 降级到规则模板
                stmt_count = len([s for s in cypher.split(";") if s.strip()])
                if stmt_count >= len(entities):
                    return cypher
                print(f"[CypherGenerator] LLM 输出仅 {stmt_count}/{len(entities)} 条, "
                      f"降级到规则模板")
        except Exception as e:
            print(f"[CypherGenerator] LLM 生成失败: {e}, 降级到规则模板")

    # LLM 失败 → 规则模板兜底
    return _template_create_cypher(entities, relations)


def _extract_cypher(text: str) -> str:
    """从 LLM 响应中提取 Cypher 语句块。"""
    # 找 ```cypher ... ``` 或 ```sql ... ```
    m = re.search(r"```(?:cypher|sql)?\\s*([\\s\\S]*?)\\s*```", text)
    if m:
        return m.group(1).strip()
    # 找分号结尾的语句
    stmts = [s.strip() for s in text.split(";") if s.strip() and "MERGE" in s or "CREATE" in s or "MATCH" in s]
    if stmts:
        return ";\\n".join(stmts) + ";"
    return text.strip()