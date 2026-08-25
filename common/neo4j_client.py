# -*- coding: utf-8 -*-
"""
Neo4j 图数据库客户端 (common.neo4j_client)
==========================================

【定位】
项目与 Neo4j 图数据库交互的唯一「数据访问层」(DAL)。
原 common.neo4j_manager (底层连接/执行) 与 common.neo4j_client (写路径封装)
已合并为本文件, 消除「两个 Neo4jClient 类 / 两个 run_cypher 方法」的命名混淆。
所有需要读写 Neo4j 的代码统一 `from common.neo4j_client import neo4j_client`
使用本模块的单例。

【提供的接口】
  - run_cypher(query, parameters)        : 单条查询, 返回 dict 列表 (带重试/降级)
  - run_multiple_cypher(queries)         : 事务批量写
  - health_check()                       : 连通性闸门 (写库前调用)
  - run_in_tx(queries)                   : 批量事务写入 (写库主路径 generate_neo4j_cypher.py 使用)
  - explain(cypher)                      : EXPLAIN 语法预检
  - export_tcm_metadata_to_json(output)  : 导出图谱 schema 元数据

【降级行为】
  - neo4j Python 驱动或配置缺失(模块导入期异常)时, neo4j_available=False,
    run_cypher 返回空列表、run_in_tx 返回 0, 不抛异常。
  - 本文件不实现任何「JSON 检索引擎」兜底; Neo4j 不可用即视为无图数据,
    由上层 (如 neo4j_retriever) 决定返回空结果。
"""
import time
import logging
import json
from typing import List, Tuple, Dict

from tqdm import tqdm

logger = logging.getLogger(__name__)

# ============================================================
# 延迟导入: Neo4j 不是必选依赖, 缺失时降级
# ============================================================
neo4j_available = False
try:
    from neo4j import GraphDatabase
    from common.config import Config
    conf = Config()
    _driver = GraphDatabase.driver(
        conf.NEO4J_URI, auth=(conf.NEO4J_USER, conf.NEO4J_PASSWORD)
    )
    neo4j_available = True
except Exception as e:
    logger.warning(f"[Neo4j] 驱动/配置缺失, 降级为不可用: {e}")
    GraphDatabase = None
    conf = None
    _driver = None


class Neo4jClient:
    """Neo4j 客户端: 连接管理 + 单条/批量执行 + 健康检查 + 重试 + 降级。"""

    def __init__(self, max_retries: int = 3, retry_delay: float = 1.0):
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.driver = _driver
        self._available = neo4j_available and self.driver is not None

    def __del__(self):
        try:
            if self.driver is not None:
                self.driver.close()
        except Exception:
            pass

    @property
    def available(self) -> bool:
        """Neo4j 是否可用。"""
        return self._available

    def health_check(self) -> bool:
        """检查 Neo4j 是否可用。"""
        if not self._available:
            return False
        try:
            rows = self.run_cypher("RETURN 1 AS ok")
            return bool(rows)
        except Exception as e:
            logger.warning(f"[Neo4j] 健康检查失败: {e}")
            self._available = False
            return False

    def run_cypher(self, query, parameters=None) -> list:
        """执行单条 Cypher 查询(带重试), 返回 dict 列表; 不可用时返回 []。"""
        if not self._available:
            logger.warning("[Neo4j] 不可用(跳过查询)")
            return []
        last_error = None
        for attempt in range(self.max_retries):
            try:
                with self.driver.session() as session:
                    result = session.run(query, parameters or {})
                    return [record.data() for record in result]
            except Exception as e:
                last_error = e
                logger.warning(f"[Neo4j] 查询重试 {attempt+1}/{self.max_retries}: {e}")
                time.sleep(self.retry_delay)
        logger.error(f"[Neo4j] 查询失败({self.max_retries}次重试): {last_error}")
        return []

    def run_multiple_cypher(self, queries_with_params):
        """事务方式批量执行多条 Cypher(写), 全部成功才提交。"""
        if not self._available:
            return
        with self.driver.session() as session:
            def transaction_logic(tx):
                for query, params in tqdm(queries_with_params, desc="执行 Cypher 语句"):
                    tx.run(query, params or {})
            session.execute_write(transaction_logic)

    def run_in_tx(self, queries: List[Tuple[str, Dict]]) -> int:
        """在单个事务中批量执行多条 Cypher(写入场景), 返回成功执行的条数。"""
        if not self._available:
            return 0
        self.run_multiple_cypher(queries)
        return len(queries)

    def explain(self, cypher: str) -> dict:
        """EXPLAIN 分析 Cypher 执行计划(不实际执行)。"""
        from __003__create_neo4j_database.cypher_generator import split_cypher_statements
        stmts = split_cypher_statements(cypher)
        if not stmts:
            return {"ok": False, "error": "空语句"}
        for stmt in stmts:
            try:
                self.run_cypher(f"EXPLAIN {stmt}", {})
            except Exception as e:
                return {"plan": str(e), "ok": False, "error": f"{e}"}
        return {"plan": None, "ok": True, "count": len(stmts)}

    def export_tcm_metadata_to_json(self, output_path="tcm_metadata.json"):
        """导出当前 Neo4j 图的模式层元数据为 JSON 文件。"""
        if not self._available:
            logger.warning("[Neo4j] 不可用, 跳过元数据导出")
            return None
        with self.driver.session() as session:
            # 1. 所有节点标签
            label_query = """
               MATCH (n)
               UNWIND labels(n) AS label
               RETURN DISTINCT label
               """
            labels = [record["label"] for record in session.run(label_query)]

            # 2. 所有关系类型
            rel_query = """
               MATCH (n)-[r]-()
               RETURN DISTINCT type(r) AS rel_type
               """
            rel_types = [record["rel_type"] for record in session.run(rel_query)]

            # 3. 所有三元组结构
            triple_query = """
               MATCH (n)-[r]->(m)
               WITH head(labels(n)) AS from_label, type(r) AS rel_type, head(labels(m)) AS to_label
               RETURN DISTINCT from_label, rel_type, to_label
               """
            triples = [{
                "from": record["from_label"],
                "rel_type": record["rel_type"],
                "to": record["to_label"],
                "description": ""
            } for record in session.run(triple_query)]

            # 4. 节点属性（每个标签下的属性键）
            node_props_query = """
               MATCH (n)
               UNWIND labels(n) AS label
               UNWIND keys(n) AS prop
               RETURN DISTINCT label, prop
               ORDER BY label, prop
               """
            label_props = {}
            for record in session.run(node_props_query):
                label = record["label"]
                prop = record["prop"]
                if prop == "project":  # 忽略 project 字段 (项目内部隔离字段, 不暴露给 schema)
                    continue
                label_props.setdefault(label, []).append({
                    "name": prop,
                    "description": ""
                })

            # 5. 关系属性（每种关系下的属性键）
            rel_props_query = """
               MATCH (n)-[r]->(m)
               UNWIND keys(r) AS prop
               RETURN DISTINCT type(r) AS rel_type, prop
               ORDER BY rel_type, prop
               """
            rel_type_props = {}
            for record in session.run(rel_props_query):
                rel_type = record["rel_type"]
                prop = record["prop"]
                rel_type_props.setdefault(rel_type, []).append({
                    "name": prop,
                    "description": ""
                })

            # 构建 JSON
            json_obj = {
                "labels": [
                    {
                        "name": label,
                        "description": "",
                        "properties": label_props.get(label, [])
                    } for label in labels
                ],
                "relationships": [
                    {
                        "type": rel,
                        "description": "",
                        "properties": rel_type_props.get(rel, [])
                    } for rel in rel_types
                ],
                "triples": triples
            }

            # 保存到文件
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(json_obj, f, ensure_ascii=False, indent=2)

            return output_path


# ============================================================
# 全局单例
# ============================================================
neo4j_client = Neo4jClient()
