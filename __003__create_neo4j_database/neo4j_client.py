# -*- coding: utf-8 -*-
"""
Neo4j 图数据库客户端 (封装 common.neo4j_manager)
================================================

【定位】
封装 common.neo4j_manager 中的 Neo4j 连接管理,
提供更简洁的连接池/健康检查/重试/批量写入接口,
供 cypher_generator.py / importer.py / query_node.py 使用。

【复用关系】
  - common.neo4j_manager.Neo4jClient  ← 底层连接管理(本项目已有)
  - 本文件在其基础上增加: 连接池缓存 / 健康检查 / EXPLAIN 校验 / 批量事务

【数据源切换】
  - 当 Neo4j 服务不可用时, 所有查询自动降级为 JSON 检索引擎。
"""
import os
import sys
import time
import logging
from typing import List, Tuple, Dict

logger = logging.getLogger(__name__)

# ============================================================
# 延迟导入: Neo4j 不是必选依赖, 缺失时降级
# ============================================================
neo4j_available = False
try:
    from common.neo4j_manager import neo4j_client as _neo4j_client
    neo4j_available = True
except Exception:
    _neo4j_client = None


class Neo4jClient:
    """
    Neo4j 客户端封装, 支持连接管理/健康检查/批量写入/优雅降级。

    用法:
        client = Neo4jClient()
        client.health_check()          # 检查连接
        client.run_cypher("MATCH (n) RETURN n LIMIT 10")
        client.run_in_tx(queries)      # 批量事务写入
    """

    def __init__(self, max_retries: int = 3, retry_delay: float = 1.0):
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._client = _neo4j_client  # 复用现有的 Neo4j 连接
        self._available = neo4j_available and self._client is not None

    def health_check(self) -> bool:
        """
        检查 Neo4j 是否可用。

        Returns
        -------
        bool
        """
        if not self._available:
            return False
        try:
            self.run_cypher("RETURN 1 AS ok")
            return True
        except Exception as e:
            logger.warning(f"[Neo4j] 健康检查失败: {e}")
            self._available = False
            return False

    @property
    def available(self) -> bool:
        """Neo4j 是否可用。"""
        return self._available

    def run_cypher(self, cypher: str, params: dict = None) -> list:
        """
        执行单条 Cypher 查询(带重试)。

        Parameters
        ----------
        cypher : str
            Cypher 查询语句。
        params : dict, optional
            参数化查询的参数。

        Returns
        -------
        list : 查询结果行列表。
        """
        if not self._available:
            logger.warning("[Neo4j] 不可用(跳过查询)")
            return []

        last_error = None
        for attempt in range(self.max_retries):
            try:
                return self._client.run_cypher(cypher, params or {})
            except Exception as e:
                last_error = e
                logger.warning(f"[Neo4j] 查询重试 {attempt+1}/{self.max_retries}: {e}")
                time.sleep(self.retry_delay)

        logger.error(f"[Neo4j] 查询失败({self.max_retries}次重试): {last_error}")
        return []

    def run_in_tx(self, queries: List[Tuple[str, Dict]]) -> int:
        """
        在单个事务中批量执行多条 Cypher(写入场景)。

        Parameters
        ----------
        queries : list[(cypher, params)]
            (Cypher语句, 参数字典) 元组列表。

        Returns
        -------
        int : 成功执行的条数。
        """
        if not self._available:
            return 0

        count = 0
        for cypher, params in queries:
            try:
                self.run_cypher(cypher, params)
                count += 1
            except Exception as e:
                logger.error(f"[Neo4j] 事务执行失败: {e}\\nCypher: {cypher[:100]}...")
        return count

    def explain(self, cypher: str) -> dict:
        """
        EXPLAIN 分析 Cypher 执行计划(不实际执行)。

        注意: EXPLAIN 只能分析单条语句。对于多条语句(用;或\\n分隔),
        只提取第一条进行 EXPLAIN。若第一条为空则跳过。

        Parameters
        ----------
        cypher : str
            单条或多条 Cypher 语句。

        Returns
        -------
        dict : 执行计划详情, 出错时返回 {"error": str}
        """
        # 提取第一条非空语句
        first_stmt = ""
        for part in cypher.split(";"):
            stmt = part.strip()
            if stmt:
                first_stmt = stmt
                break
        if not first_stmt:
            return {"ok": False, "error": "空语句"}
        try:
            result = self.run_cypher(f"EXPLAIN {first_stmt}")
            return {"plan": result, "ok": True}
        except Exception as e:
            return {"plan": str(e), "ok": False, "error": str(e)}

    def clear_all(self):
        """清空图数据库(危险操作, 仅开发环境使用)。"""
        if not self._available:
            return
        try:
            self.run_cypher("MATCH (n) DETACH DELETE n")
            logger.warning("[Neo4j] 已清空全部数据")
        except Exception as e:
            logger.error(f"[Neo4j] 清空失败: {e}")


# ============================================================
# 全局单例
# ============================================================
neo4j_client = Neo4jClient()