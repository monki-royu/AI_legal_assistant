# -*- coding: utf-8 -*-
"""并行双审节点 (Parallel Dual Review Node)
==============================================

【优化目标】
    将原来串行执行的 compliance_review + contract_ai_review 改为并发执行,
    预计节省 40% 以上的双审耗时。

【为什么可行】
    contract_ai_review 对 compliance_risk_items 的依赖是「可选上下文」:
    - 读取方式: state.get("compliance_risk_items", []) or []
    - 当合规结果尚未产出时, 自动回退为空列表
    - LLM prompt 中的 compliance_block 为空时不会注入额外要求
    - contract_ai_review 的核心功能(抽取商业/法律/操作风险)不依赖合规结果
    - 后续 conflict_resolution 节点统一做双路结果的冲突消解

【并发策略】
    使用 ThreadPoolExecutor(max_workers=2) 同时提交两个 LLM 调用:
    - future_compliance: 执行 compliance_review_node
    - future_contract:  执行 contract_ai_review_node
    两个 future 独立完成后汇总结果写入 state。

【状态读写】
    读取 (两个节点共享):
        - doc_segments, doc_text, party_a, party_b, user_side, contract_type
        - review_context_bundle, citations, extracted_numerics
        - review_strategy, rule_risk_items, strategy_weights
        - retrieval_queries

    写入 (分别写入不冲突的字段):
        - compliance_review_node 写入: compliance_risk_items
        - contract_ai_review_node 写入: contract_risk_items

    由于写入字段不同, 不会发生竞态冲突。

【性能预估】
    - 串行: compliance(~8s) + contract(~12s) = ~20s
    - 并发: max(compliance, contract) = ~12s (取较慢的那个)
    - 节省: ~8s (40%)
"""

import os
import time
from concurrent.futures import ThreadPoolExecutor

from __004__langgraph_more_nodes.agent_state import AgentState
from __004__langgraph_more_nodes.nodes.contract_compliance_nodes.compliance_review_node import (
    compliance_review_node,
)
from __004__langgraph_more_nodes.nodes.contract_compliance_nodes.contract_ai_review_node import (
    contract_ai_review_node,
)
from common.logger import get_logger

logger = get_logger(__name__)

# 并行执行线程池: 双审任务固定为 2 个并发, 设为 2 worker
_PARALLEL_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dual_review_")

# 单路 future 等待超时(秒)。原实现钉死 120s, 合规审查单次 LLM 调用在冷启动/限流时
# 可能超过 120s, 触发 TimeoutError 把整个 graph 打死。改为可配(默认 300s), 且超时后
# 降级为「阻塞复用已提交线程」而非崩溃。
_REVIEW_FUTURE_TIMEOUT = float(os.environ.get("DUAL_REVIEW_TIMEOUT", "300"))


def _run_compliance(state: AgentState) -> dict:
    """在线程中运行合规审查节点 (包装函数, 便于提交到线程池)."""
    return compliance_review_node(state)


def _run_contract(state: AgentState) -> dict:
    """在线程中运行合同审核节点 (包装函数, 便于提交到线程池)."""
    return contract_ai_review_node(state)


def parallel_dual_review_node(state: AgentState) -> dict:
    """并行双审主节点 — 并发执行合规审查与合同审核.

    【What】
        将串行的 compliance_review → contract_ai_review 改为并发执行,
        两个 LLM 调用同时进行, 取两者中较慢的一个作为总耗时。

    【Why】
        1. contract_ai_review 对 compliance_risk_items 的依赖是可选的,
           空列表不会导致功能异常, 只是少了合规上下文参考
        2. 两个节点写入不同的 state 字段 (compliance_risk_items vs contract_risk_items),
           不存在竞态冲突
        3. 预估节省 40% 双审耗时 (从 ~20s 降到 ~12s)

    【How】
        1. 从主 state 复制一份子 state 给每个节点 (隔离读写)
        2. 用 ThreadPoolExecutor 并发提交两个节点函数
        3. 等待两个 future 完成
        4. 合并两个结果:
           - compliance_result → 写入 compliance_risk_items
           - contract_result → 写入 contract_risk_items
        5. 返回合并后的增量 state
    """
    task_type = state.get("task_type", "")
    print("=== 并行双审 (compliance_review ║ contract_ai_review) ===")
    print(f"  task_type: {task_type}")

    # 合规审查单路模式: 不需要并发, 直接执行 compliance_review 然后返回
    # 因为合规审查任务不需要 contract_ai_review 的输出
    if task_type == "compliance_review":
        print("  [合规单审模式] 只执行 compliance_review, 跳过并发")
        t0 = time.time()
        compliance_result = compliance_review_node(state)
        elapsed = time.time() - t0
        print(f"  compliance_review 完成, 耗时 {elapsed:.1f}s")
        return compliance_result

    # 合同审核双审模式: 并发执行
    t_total_start = time.time()

    # 【关键】两个线程共享同一份只读 state, 各自返回独立的增量 dict
    # 不存在写冲突, 因为写入字段完全不同
    print("  提交并发任务到线程池...")

    # 【计时口径修正】原实现把 t_compliance_start / t_contract_start 都放在
    # 两个 future 都提交**之后**才赋值, 两个起点几乎相同 →
    # 打印出的"各自耗时"实际是从同一基线起算的累计值, 不是各自耗时, 指标失真。
    # 现在改为: 每个 future 在 submit 前后各自打点。
    t_compliance_start = time.time()
    future_compliance = _PARALLEL_EXECUTOR.submit(_run_compliance, state)
    t_contract_start = time.time()
    future_contract = _PARALLEL_EXECUTOR.submit(_run_contract, state)

    # 等待两个任务完成 (超时保护, 默认 300s; 超时后降级为阻塞等待复用已提交线程, 不崩溃)
    print("  等待并发任务完成...")

    def _collect(future, name):
        """取 future 结果: 先按 _REVIEW_FUTURE_TIMEOUT 限时等待;
        超时则降级为无超时阻塞, 复用已提交线程(不重复执行), 保证 graph 不中断。"""
        try:
            return future.result(timeout=_REVIEW_FUTURE_TIMEOUT)
        except TimeoutError:
            logger.warning(
                "%s 超过 %.0fs 仍未返回, 降级为阻塞等待已提交线程(不中断 graph)",
                name, _REVIEW_FUTURE_TIMEOUT,
            )
            print(f"  ⚠️ {name} 超过 {_REVIEW_FUTURE_TIMEOUT:.0f}s, 继续等待其完成(不中断)")
            return future.result()

    compliance_result = _collect(future_compliance, "compliance_review")
    t_compliance_done = time.time()

    contract_result = _collect(future_contract, "contract_ai_review")
    t_contract_done = time.time()

    t_total_elapsed = time.time() - t_total_start
    t_compliance_elapsed = t_compliance_done - t_compliance_start
    t_contract_elapsed = t_contract_done - t_contract_start

    print(f"  ✅ 并行双审完成, 总耗时 {t_total_elapsed:.1f}s")
    print(f"     compliance_review: {t_compliance_elapsed:.1f}s")
    print(f"     contract_ai_review: {t_contract_elapsed:.1f}s")

    # 【性能计时走日志】耗时属于监控数据而非业务数据, 不应污染 state。
    #   parallel_review_elapsed 字段已在 AgentState 标注 TODO: 待迁移 logging,
    #   迁移完成后可删除 state 写入, 只保留这行 logger.info。
    logger.info(
        "并行双审耗时: total=%.1fs compliance=%.1fs contract=%.1fs (task_type=%s)",
        t_total_elapsed, t_compliance_elapsed, t_contract_elapsed, task_type,
    )

    # 合并结果: 两个节点写入不同字段, 直接合并 dict
    merged = {}
    if compliance_result:
        merged.update(compliance_result)
    if contract_result:
        merged.update(contract_result)

    # 记录性能指标到 state (便于监控)
    merged["parallel_review_elapsed"] = round(t_total_elapsed, 1)

    return merged
