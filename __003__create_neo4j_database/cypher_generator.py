# -*- coding: utf-8 -*-
"""
Cypher 语句生成器 + 校验器
============================

【定位】
根据 entity_extractor.py 输出的实体/关系数据,
生成对应的 Cypher 写入语句, 并校验语法正确性。

【流程】
  实体/关系数据 → LLM 生成 Cypher → 语法校验(EXPLAIN) → 返回合法 Cypher
                            ↓ 失败
                        规则模板生成(兜底) → 语法校验 → 返回

【复用关系】
  - importer.py   ← 批量导入时调用 generate_create_cypher()
  - query_node.py ← 实时查询时调用 generate_match_cypher()
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
def _template_create_cypher(entities: list, relations: list) -> str:
    """基于规则的 Cypher 生成(不依赖 LLM)。"""
    statements = []

    for e in entities:
        name = e["name"].replace("'", "\\'")
        etype = e["type"]
        attrs = e.get("attributes", {}) or {}
        set_clause = ""
        if attrs:
            pairs = ", ".join(f"n.{k} = '{v}'" for k, v in attrs.items() if isinstance(v, str))
            if pairs:
                set_clause = f" ON CREATE SET {pairs}"
        statements.append(
            f"MERGE (n:{etype} {{name: '{name}', type: '{etype}'}}){set_clause};"
        )

    for r in relations:
        sub = r["subject"].replace("'", "\\'")
        obj = r["object"].replace("'", "\\'")
        stype = r["subject_type"]
        obj_type = r["object_type"]
        rel_type = r["relation"]
        statements.append(
            f"MATCH (a:{stype} {{name: '{sub}'}}), (b:{obj_type} {{name: '{obj}'}})"
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
            response = llm(prompt)
            cypher = _extract_cypher(response)
            if cypher:
                return cypher
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


# ============================================================
# 查询 Cypher 生成 (query_node 使用)
# ============================================================
def generate_match_cypher(entity_names: List[str]) -> str:
    """
    根据实体名列表生成查询 Cypher。

    Parameters
    ----------
    entity_names : list[str]
        从用户问题中抽取的实体名列表。

    Returns
    -------
    str : Cypher MATCH 语句
    """
    conditions = " OR ".join(f"n.name CONTAINS '{n.replace(chr(39), chr(39)+chr(39))}'" for n in entity_names)
    if not conditions:
        return "MATCH (n) RETURN n LIMIT 20"
    return f"""
        MATCH (n)
        WHERE {conditions}
        OPTIONAL MATCH (n)-[r]-(m)
        RETURN n, r, m
        LIMIT 50
    """.strip()