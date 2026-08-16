# -*- coding: utf-8 -*-
"""
知识图谱查询节点 (LangGraph Node)
==================================

【定位】
作为一个 LangGraph 节点, 接收用户问题 → 抽取实体 → 生成 Cypher MATCH →
执行查询 → 格式化返回。
可作为"智能问答"流程中的知识图谱增强节点, 也可以独立调用。

【复用关系】
  - 被 __004__langgraph_more_nodes/langgraph_main.py 注册为 neo4j_query_node
  - 智能问答流程中作为可选的"图查询增强"分支
  - 检索引擎的 Neo4j 数据源挂载点

【流程】
  用户问题 → LLM 实体抽取 → generate_match_cypher() → Neo4j 查询 → 格式化结果
                                            ↓ 失败
                    降级: 关键词全文检索(复用 common.retrieval_engine)
"""

import sys, re, json
from typing import List, Dict

if sys.stdout.encoding.lower().replace("-", "") != "utf8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from __004__langgraph_more_nodes.agent_state import AgentState


# ============================================================
# LLM 实体抽取 Prompt (简化版)
# ============================================================
_ENTITY_EXTRACT_PROMPT = """从用户的法律问题中抽取关键实体名称(法律概念/条款/主体等)。

用户问题: {question}

只输出 JSON 数组, 如 ["违约金", "合同解除", "民法典"], 不要包含其他文字。"""


def neo4j_query_node(state: AgentState) -> dict:
    """
    知识图谱查询节点: 抽取实体 → Cypher MATCH → Neo4j → 格式化结果。

    如果 Neo4j 不可用或查询为空, 自动降级到关键词全文检索。

    Parameters (from state)
    -----------------------
    user_input : str
        用户的原始问题。
    neo4j_query_result : list, optional
        已有图查询结果(外部注入)。

    Returns (into state)
    --------------------
    neo4j_query_result : list
        查询结果列表。
    neo4j_query_fallback : bool
        是否降级到全文检索。
    neo4j_query_cypher : str
        实际执行的 Cypher 语句。
    """

    from __003__create_neo4j_database.neo4j_client import neo4j_client
    from __003__create_neo4j_database.cypher_generator import generate_match_cypher
    from common.llm import llm

    question = state.get("user_input", "")
    if not question:
        return {"neo4j_query_result": [], "neo4j_query_fallback": True}

    # 1. 用 LLM 抽取实体
    entities = _extract_entities_from_question(question)
    if not entities:
        print("[Neo4jQuery] 未抽取到实体, 降级到全文检索")
        return {"neo4j_query_result": [], "neo4j_query_fallback": True}

    # 2. 生成 MATCH Cypher
    cypher = generate_match_cypher(entities)

    # 3. 检查 Neo4j 是否可用
    if not neo4j_client.available:
        print("[Neo4jQuery] Neo4j 不可用, 降级到全文检索")
        return {"neo4j_query_result": [], "neo4j_query_fallback": True}

    # 4. 执行查询
    try:
        rows = neo4j_client.run_cypher(cypher)
        result = _format_results(rows)
        if not result:
            print("[Neo4jQuery] 查询结果为空, 降级到全文检索")
            return {
                "neo4j_query_result": [],
                "neo4j_query_fallback": True,
                "neo4j_query_cypher": cypher,
            }

        return {
            "neo4j_query_result": result,
            "neo4j_query_fallback": False,
            "neo4j_query_cypher": cypher,
        }

    except Exception as e:
        print(f"[Neo4jQuery] 查询失败: {e}, 降级到全文检索")
        return {
            "neo4j_query_result": [],
            "neo4j_query_fallback": True,
            "neo4j_query_cypher": cypher,
        }


def _extract_entities_from_question(question: str) -> List[str]:
    """用 LLM 从用户问题中抽取实体列表。"""
    try:
        prompt = _ENTITY_EXTRACT_PROMPT.format(question=question[:500])
        response = llm(prompt)
        # 提取 JSON 数组
        m = re.search(r"\[.*?\]", response, re.DOTALL)
        if m:
            entities = json.loads(m.group())
            if isinstance(entities, list) and entities:
                return entities
    except Exception:
        pass

    # LLM 失败 → 简单规则: 常见的法律概念关键词
    keywords = ["民法典", "劳动合同法", "公司法", "违约金", "解除合同",
                "赔偿", "争议", "仲裁", "诉讼", "侵权", "合同"]
    found = [kw for kw in keywords if kw in question]
    return found[:5]  # 最多 5 个


def _format_results(rows: list) -> List[dict]:
    """
    将 Neo4j 返回的行格式化为可读的 dict 列表。

    Neo4j row 通常包含节点对象, 这里提取其属性。
    """
    formatted = []
    for row in rows:
        # row 可能是 neo4j 记录, 简化处理
        if isinstance(row, dict):
            formatted.append(row)
        elif hasattr(row, "items"):
            formatted.append(dict(row))
        else:
            formatted.append({"value": str(row)[:200]})
    return formatted