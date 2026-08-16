# -*- coding: utf-8 -*-
"""
历史记录持久化存储 (SQLite)
===========================

【模块定位】
为法智引擎所有需要"保存对话/任务/文书记录"的场景提供统一的历史记录存储服务。
基于 SQLite 实现, 零外部依赖(不依赖 MySQL/Redis), 与项目"轻量、可独立运行"
的定位一致。当前覆盖:
  1. 文书生成记录: 保存用户输入、生成结果、引用法条、风险提示、关联案例等;
  2. 通用历史记录: 可按任务类型(query/contract/compliance/docgen/case/law)扩展。

【核心类 / 函数】
  - HistoryStore    : 单例类, 提供 CRUD + 分页查询
  - init_db()       : 建表(自动执行, 幂等)
  - store()         : 插入一条记录
  - get()           : 按 id 获取详情
  - list()          : 分页列表(支持任务类型筛选 + 排序)
  - delete()        : 删除
  - toggle_star()   : 切换收藏

【与 legal-documents 的区别】
  legal-documents 使用 MySQL + JSON 字段, 依赖外部数据库服务;
  本模块使用 SQLite + JSON 序列化, 零外部依赖, 便于独立运行和测试。

【用法】
  from common.history_store import store
  record = store.store("docgen", {"user_input": {...}, "result": ...})
  records, total = store.list("docgen", page=1, page_size=10)
"""
# ============================================================
# 文件名称: common/history_store.py
# 文件作用: 历史记录数据库 (SQLite)
# ============================================================
# 【这个文件是干什么的？】
# 用 SQLite 存储用户会话历史、审核报告、文书结果。提供 save/load/list/delete 接口。
#
# 【代码逻辑主线】
# 参见各函数前的【功能】【参数】【返回值】【逻辑】说明。
#
# 【新手建议】
# 先看主函数 -> 再看辅助函数。
#

import os
import sys
import json
import sqlite3
import threading
from datetime import datetime
from typing import Optional, List, Tuple

# 项目路径工具
from common.path_utils import root_dir

# 📜 数据库存储路径: data/history.db (与 knowledge_base 同目录, 便于一起管理)
_DB_PATH = os.path.join(root_dir, "data", "history.db")


class HistoryStore:
    """
    历史记录持久化存储(单例模式, 线程安全)。

    因为 SQLite 不适合高并发写入, 本模块通过 threading.Lock 确保串行写,
    线程池/协程环境下调用方应排队访问。
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        """单例: 整个进程共享同一个 HistoryStore 实例。"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """惰性初始化: 首次访问时建表(非模块加载时), 避免启动时创建无用连接。"""
        if getattr(self, "_initialized", False):
            return
        with self._lock:
            if self._initialized:
                return
            # 确保 data/ 目录存在
            os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
            self._conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")   # WAL 模式: 读不阻塞写
            self._conn.execute("PRAGMA busy_timeout=5000")   # 忙时等待 5 秒
            self._init_tables()
            self._initialized = True

    def _init_tables(self):
        """
        建表(幂等: CREATE TABLE IF NOT EXISTS)。

        表结构:
          - id              INTEGER PRIMARY KEY AUTOINCREMENT
          - task_type       TEXT NOT NULL      -- 任务类型: docgen/contract/compliance/qa/retrieval/xhs
          - title           TEXT DEFAULT ''    -- 记录标题(如文书名/合同名/查询问题)
          - user_input      TEXT DEFAULT ''    -- 用户输入的 JSON 字符串
          - result          TEXT DEFAULT ''    -- 生成结果的 JSON 字符串
          - summary         TEXT DEFAULT ''    -- 摘要(列表展示用, 前 200 字符)
          - is_starred      INTEGER DEFAULT 0 -- 是否收藏 0/1
          - created_at      TEXT DEFAULT (datetime('now','localtime'))
          - updated_at      TEXT DEFAULT (datetime('now','localtime'))

        JSON 字段(user_input/result) 在 Python 端序列化/反序列化, 保持灵活性。
        """
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS history_records (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type   TEXT NOT NULL,
                title       TEXT DEFAULT '',
                user_input  TEXT DEFAULT '',
                result      TEXT DEFAULT '',
                summary     TEXT DEFAULT '',
                is_starred  INTEGER DEFAULT 0,
                created_at  TEXT DEFAULT (datetime('now','localtime')),
                updated_at  TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        # 索引: 按任务类型 + 创建时间快速筛选和排序
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_history_task_time
            ON history_records(task_type, created_at DESC)
        """)
        # 收藏索引: 快速查询收藏记录
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_history_starred
            ON history_records(is_starred)
        """)
        self._conn.commit()

    # ================================================================
    # 写操作(带锁保护)
    # ================================================================

    def store(self, task_type: str, title: str = "",
              user_input: dict = None, result: dict = None,
              summary: str = "") -> dict:
        """
        插入一条历史记录。

        Parameters
        ----------
        task_type : str
            任务类型: docgen(文书生成)/contract(合同审核)/compliance(合规审查)/
                     qa(法律问答)/retrieval(法律检索)/xhs(小红书发布)/case(案例检索)/law(法规查询)。
        title : str
            记录标题。
        user_input : dict, optional
            用户输入的结构化数据(会被 JSON 序列化)。
        result : dict, optional
            生成结果的结构化数据(会被 JSON 序列化)。
        summary : str
            列表展示摘要(建议前 200 字符)。

        Returns
        -------
        dict
            插入后的完整记录(含自增 id)。
        """
        user_input_json = json.dumps(user_input, ensure_ascii=False) if user_input else "{}"
        result_json = json.dumps(result, ensure_ascii=False) if result else "{}"
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO history_records (task_type, title, user_input, result, summary)
                   VALUES (?, ?, ?, ?, ?)""",
                (task_type, title, user_input_json, result_json, summary[:200])
            )
            self._conn.commit()
            # 返回刚插入的完整记录
            return self.get(cursor.lastrowid)

    def delete(self, record_id: int) -> bool:
        """删除指定 id 记录, 返回是否找到并删除。"""
        with self._lock:
            cursor = self._conn.execute("DELETE FROM history_records WHERE id=?", (record_id,))
            self._conn.commit()
            return cursor.rowcount > 0

    def toggle_star(self, record_id: int) -> Optional[bool]:
        """切换收藏状态, 返回新的 is_starred 值; 记录不存在返回 None。"""
        with self._lock:
            # 先查当前状态
            cursor = self._conn.execute(
                "SELECT is_starred FROM history_records WHERE id=?", (record_id,)
            )
            row = cursor.fetchone()
            if row is None:
                return None
            new_val = 0 if row["is_starred"] else 1
            self._conn.execute(
                "UPDATE history_records SET is_starred=?, updated_at=datetime('now','localtime') WHERE id=?",
                (new_val, record_id)
            )
            self._conn.commit()
            return bool(new_val)

    # ================================================================
    # 读操作(无锁, SQLite WAL 模式读不互斥)
    # ================================================================

    def get(self, record_id: int) -> Optional[dict]:
        """按 id 获取完整记录(JSON 字段自动反序列化)。"""
        cursor = self._conn.execute(
            "SELECT * FROM history_records WHERE id=?", (record_id,)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def list(self, task_type: str = "", page: int = 1, page_size: int = 10,
             star_only: bool = False, sort: str = "time-desc") -> Tuple[List[dict], int]:
        """
        分页查询历史记录。

        Parameters
        ----------
        task_type : str
            筛选任务类型, 空字符串不筛选。
        page : int, 从 1 开始。
        page_size : int, 每页条数。
        star_only : bool, 仅查收藏记录。
        sort : str, "time-desc"(默认)/"time-asc"。

        Returns
        -------
        Tuple[List[dict], int]
            (记录列表, 总条数)。
        """
        # 构造 WHERE 条件
        where_clauses = []
        params = []
        if task_type:
            where_clauses.append("task_type=?")
            params.append(task_type)
        if star_only:
            where_clauses.append("is_starred=1")
        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

        # 统计总数
        cursor = self._conn.execute(
            f"SELECT COUNT(*) FROM history_records WHERE {where_sql}", params
        )
        total = cursor.fetchone()[0]

        # 排序
        order = "DESC" if sort == "time-desc" else "ASC"

        # 分页
        offset = (page - 1) * page_size
        cursor = self._conn.execute(
            f"SELECT * FROM history_records WHERE {where_sql} ORDER BY created_at {order} LIMIT ? OFFSET ?",
            params + [page_size, offset]
        )
        records = [self._row_to_dict(row) for row in cursor.fetchall()]
        return records, total

    # ================================================================
    # 工具
    # ================================================================

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        """把 sqlite3.Row 转为标准 dict, 并反序列化 JSON 字段。"""
        d = dict(row)
        # 反序列化 JSON 字段
        for key in ("user_input", "result"):
            try:
                d[key] = json.loads(d[key]) if d[key] else {}
            except (json.JSONDecodeError, TypeError):
                d[key] = {}
        d["is_starred"] = bool(d.get("is_starred", 0))
        return d

    def close(self):
        """显式关闭数据库连接(进程退出时自动释放, 通常无需手动调用)。"""
        if getattr(self, "_conn", None):
            self._conn.close()


# ======================================================================
# 模块级单例(与 common/llm.py 的 my_llm 同一模式)
# ======================================================================
# 调用方只需: from common.history_store import store
# 即可获得全局唯一的 HistoryStore 实例
store = HistoryStore()


# ======================================================================
# CLI 自测
# ======================================================================
if __name__ == "__main__":
    # 插入一条测试记录
    record = store.store(
        task_type="docgen",
        title="民事起诉状-劳动争议",
        user_input={"plaintiff": "张三", "defendant": "某公司", "claims": "支付工资 50000 元"},
        result={"document": "民事起诉状\n...", "cited_laws": []},
        summary="张三诉某公司劳动争议一案"
    )
    print(f"插入记录 id={record['id']}")
    # 查询列表
    records, total = store.list(task_type="docgen")
    print(f"列表: {len(records)}/{total} 条")
    # 切换收藏
    new_star = store.toggle_star(record["id"])
    print(f"收藏状态: {new_star}")
    # 详情
    detail = store.get(record["id"])
    print(f"详情 title: {detail['title']}")
    # 删除
    deleted = store.delete(record["id"])
    print(f"删除: {deleted}")