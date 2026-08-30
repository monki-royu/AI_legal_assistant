# -*- coding: utf-8 -*-
"""并行检索节点 (Parallel DocGen Retrieve Node)
=================================================

【优化目标】
    将原来串行/内联的 doc_law_retrieve + doc_case_retrieve 改为并发执行,
    预计节省约 40%~50% 的检索耗时(两个子图调用取较慢者为准)。

【类比对象】
    contract_compliance_nodes/parallel_dual_review_node.py:
    合同审核与合规审查大模型并发 —— 用 ThreadPoolExecutor 在单节点内并发跑两个子节点函数,
    各自返回不冲突的独立字段, 再合并写入 state。

    本文件与之完全同构:
        parallel_dual_review_node   ←→  parallel_docgen_retrieve_node
        compliance_review_node     ←→  doc_law_retrieve_node
        contract_ai_review_node     ←→  doc_case_retrieve_node

【并发策略】
    使用 ThreadPoolExecutor(max_workers=2) 同时提交两个检索调用:
    - future_law:  执行 doc_law_retrieve_node  (legal_research 子图)
    - future_case: 执行 doc_case_retrieve_node (case_search 子图)
    两个 future 独立完成后汇总结果写入 state。

【状态读写】
    读取 (两个子节点共享, 只读):
        - law_retrieval_query / case_retrieval_query (doc_query_plan 输出)
        - input (兜底)

    写入 (分别写入不冲突的字段):
        - doc_law_retrieve_node  → law_citations / law_quality_score / law_research_context
        - doc_case_retrieve_node → similar_cases / case_quality_score

    由于写入字段完全不同, 不会发生竞态冲突。

【线程安全】
    legal_response_sync 在 thread_id 为 None 时自动生成独立 uuid4,
    两个并发调用各自拿到不同 thread_id, checkpointer 不互相覆盖;
    底层 LLM 客户端 invoke 本身是线程安全的(与 parallel_dual_review 的并发 LLM 调用同理)。

【性能预估】
    - 串行: law(~5s) + case(~5s) = ~10s
    - 并发: max(law, case) = ~5s
    - 节省: ~5s (50%)

【与 parallel_dual_review 的差异】
    parallel_dual_review 有 "compliance_review 单审模式" 分支(只跑合规),
    因为合规任务不需要合同审核输出。而文书生成中法条检索与类案检索是
    clause_fill 的互补输入, 永远需要一起跑, 故本节点无单审分支, 始终并发双路。
"""

import time
from concurrent.futures import ThreadPoolExecutor

from __004__langgraph_more_nodes.agent_state import AgentState
from __004__langgraph_more_nodes.nodes.docgen_nodes.doc_law_retrieve_node import (
    doc_law_retrieve_node,
)
from __004__langgraph_more_nodes.nodes.docgen_nodes.doc_case_retrieve_node import (
    doc_case_retrieve_node,
)
from common.logger import get_logger

logger = get_logger(__name__)

# 并行执行线程池: 双检索任务固定为 2 个并发, 设为 2 worker
_PARALLEL_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="docgen_retrieve_")


def _run_law(state: AgentState) -> dict:
    """在线程中运行法条检索节点 (包装函数, 便于提交到线程池)."""
    return doc_law_retrieve_node(state)


def _run_case(state: AgentState) -> dict:
    """在线程中运行类案检索节点 (包装函数, 便于提交到线程池)."""
    return doc_case_retrieve_node(state)


def parallel_docgen_retrieve_node(state: AgentState) -> dict:
    """并行检索主节点 — 并发执行法条检索与类案检索。

    【What】
        将串行的 law_retrieve + case_retrieve 改为并发执行,
        两个检索子图同时进行, 取两者中较慢的一个作为总耗时。

    【Why】
        1. 两个检索子图之间无数据依赖: 法条检索只吃 law_retrieval_query,
           类案检索只吃 case_retrieval_query, 二者互不依赖。
        2. 两个子节点写入不同的 state 字段
           (law_citations* vs similar_cases/case_quality_score), 不存在竞态冲突。
        3. 预估节省约 50% 检索耗时 (从 ~10s 降到 ~5s)。

    【How】
        1. 共享同一份只读 state, 各自返回独立的增量 dict。
        2. 用 ThreadPoolExecutor 并发提交两个节点函数。
        3. 等待两个 future 完成 (timeout 120s)。
        4. 合并两个结果写入 state。
        5. 返回合并后的增量 state。
    """
    print("=== 并行检索 (law_retrieve ║ case_retrieve) ===")

    t_total_start = time.time()

    # 【关键】两个线程共享同一份只读 state, 各自返回独立的增量 dict
    # 不存在写冲突, 因为写入字段完全不同
    print("  提交并发检索任务到线程池...")

    # 【计时口径修正】原实现把 t_law_start / t_case_start 都放在两个 future
    # 都提交**之后**才赋值, 两个起点几乎相同 → 打印出的"各自耗时"实际是从
    # 同一基线起算的累计值, 不是各自耗时, 指标失真。改为各自 submit 前后打点。
    t_law_start = time.time()
    future_law = _PARALLEL_EXECUTOR.submit(_run_law, state)
    t_case_start = time.time()
    future_case = _PARALLEL_EXECUTOR.submit(_run_case, state)

    # 等待两个任务完成 (timeout 120s)
    print("  等待并发检索任务完成...")

    law_result = future_law.result(timeout=120)
    t_law_done = time.time()

    case_result = future_case.result(timeout=120)
    t_case_done = time.time()

    t_total_elapsed = time.time() - t_total_start
    t_law_elapsed = t_law_done - t_law_start
    t_case_elapsed = t_case_done - t_case_start

    print(f"  ✅ 并行检索完成, 总耗时 {t_total_elapsed:.1f}s")
    print(f"     law_retrieve:  {t_law_elapsed:.1f}s")
    print(f"     case_retrieve: {t_case_elapsed:.1f}s")

    # 【性能计时走日志】耗时是监控数据而非业务数据, 不应污染 state。
    #   parallel_retrieve_elapsed 字段已在 AgentState 标注 TODO: 待迁移 logging,
    #   迁移完成后可删除 state 写入, 只保留这行 logger.info。
    logger.info(
        "文书并行检索耗时: total=%.1fs law=%.1fs case=%.1fs",
        t_total_elapsed, t_law_elapsed, t_case_elapsed,
    )

    # 合并结果: 两个子节点写入不同字段, 直接合并 dict
    merged = {}
    if law_result:
        merged.update(law_result)
    if case_result:
        merged.update(case_result)

    # 记录性能指标到 state (便于监控)
    merged["parallel_retrieve_elapsed"] = round(t_total_elapsed, 1)

    return merged
