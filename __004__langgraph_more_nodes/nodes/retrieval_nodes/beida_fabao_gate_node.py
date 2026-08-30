"""检索子节点: 北大法宝 付费外挂门禁 (质量门 3 次重试失败独占触发)

【架构位置】
    本节点是"检索智能体"(retrieval_subgraph) 在 quality_gate_retry_node 之后、
    context_pack / 退出子图之前的【最后唯一付费询问环节】。

    注意：case_search 等单源任务只跳过「多源融合 (RRF + 7 项权威分)」，
    质量评分仍然会计算 (fusion 节点里仍跑 _calculate_quality_score)，
    因此本门禁对单源/多源任务一视同仁。

【触发条件 V5 (用户最终定稿 — 只有 1 条触发通路)】
    ╔════════════════════════════════════════════════════════════╗
    ║  唯一触发条件: state["fabao_retry_eligible"] is True      ║
    ║  - 仅在 quality_gate_retry_node 达到 MAX_QUALITY_RETRIES  ║
    ║    (默认 3 次) 仍低于 QUALITY_GATE_THRESHOLD 时，          ║
    ║    由它写 fabao_retry_eligible=True 到此 state 字段；     ║
    ║  - 免费链路（关键词扩展、源切换、fallback）全用尽才允许     ║
    ║    给用户一次付费机会；                                     ║
    ║  - 其它任何情况都直接跳过：                                 ║
    ║      ✓ 即便 quality_score < 50，只要还在免费重试轮次内；     ║
    ║      ✓ 即便 query 命中「涉外/跨境/银行保险」等关键词         ║
    ║        （retrieval_intent_decompose_node 内已彻底删除所有    ║
    ║         beida_fabao 关键词规则，不再进 mounted/api_sources）║
    ║      ✓ 即便上游手动传了 api_sources=[] 含 beida_fabao       ║
    ║         （前端直查付费源走 FastAPI，不经 LangGraph）。       ║
    ╚════════════════════════════════════════════════════════════╝

【询问流程】
    fabao_retry_eligible=True → interrupt() 真中断图，
    向调用方抛出付费确认 payload（当前免费质量分 / 查询 / 已重试次数 / 付费提醒），
    前端弹窗后通过 graph.invoke(Command(resume=value)) 恢复：
        - resume=False / None: 用户拒绝 -> 返回现有免费结果 (fabao_skipped=True)
        - resume=True: 用户确认 -> 调 common.mcp_beidafabao 付费 MCP(最多 3 轮技术性重试)
        - resume="<编辑后的查询>": 用户改写检索词 -> 用新查询调用付费 MCP

【幂等性】
    ① fabao_retry_eligible 基于 state 判定，resume 重跑仍一致；
    ② mcp_invoked=True 只在成功后写入，二次进入直接跳过，避免重复付费。

【interrupt 不可用时的降级】
    旧版 langgraph (无 langgraph.types.interrupt) 时降级为
    human_intervention_needed=True + 付费提醒文案，绝不静默调用付费接口。
"""

from __future__ import annotations

import os

from __004__langgraph_more_nodes.agent_state import AgentState

# 高分兜底值：即便 fabao_retry_eligible 被异常写 True，
# 只要免费质量分已经达到此值也不问用户（理论路径：质量门 3 次重试后仍<QUALITY_GATE_THRESHOLD，
# 才会被置为 eligible，这里只是对"误设置 eligible + 异常高分数"的防御）。
FABAO_SANE_SKIP_SCORE = 85

# 付费接口最大内部重试次数(用户确认调用后, 单次授权内的技术性重试)
FABAO_MAX_RETRIES = 3

# 付费接口中断问询的安全封装统一收口到 common/retrieval_shared._ask_user_interrupt
# (与 credit_check_node 共用同一实现, 避免逐字复制; 日志前缀用 label 区分场景)
from common.retrieval_shared import _ask_user_interrupt


def beida_fabao_gate_node(state: AgentState):
    """北大法宝付费外挂门禁节点: 仅对质量门 3 次重试失败独占触发 (fabao_retry_eligible=True)。

    读取字段:
        - fabao_retry_eligible (bool): **唯一触发标记**，由 quality_gate_retry_node 在
          retry_count >= MAX_QUALITY_RETRIES 且质量分仍低于阈值时置 True。
        - quality_score (float): 免费检索质量分 (0-100)。用于 payload 展示 +  sanity 兜底。
        - quality_retry_count (int): 已重试次数 (用于日志/payload)。
        - retrieval_query (str) / research_query (str): 检索查询。
        - mcp_invoked (bool): 北大法宝是否已成功调用过 (幂等去重, 避免重复付费)。
        - citations (List[Dict]) / research_context (str): 已有免费检索结果。
        - task_type (str): 任务类型, 仅用于日志区分 case_search / legal_research ...
        注意: api_sources / 关键词是否命中 —— **不再影响门禁触发判断**。

    写入字段:
        - fabao_skipped (bool): 是否跳过付费接口
        - fabao_retry_count (int): 实际尝试次数 (0~3)
        - citations (List[Dict]) / research_context (str): 用户确认且成功时追加
        - mcp_invoked (bool): 成功调用后置 True
        - human_intervention_needed (bool): interrupt 不可用 / 3 轮重试失败后置 True
        - human_intervention_prompt (str): 付费提醒文案
    """
    quality_score = state.get("quality_score", 0) or 0
    task_type = str(state.get("task_type", "") or "")
    retry_count = state.get("quality_retry_count", 0) or 0
    max_retries = state.get("quality_max_retries", 3) or 3
    fabao_eligible = bool(state.get("fabao_retry_eligible", False))

    # ① 唯一触发通道: fabao_retry_eligible=True (质量门 3 次重试失败独占)
    #    其余任何场景都直接跳过 —— 即便是 quality_score=10 或用户写了"涉外/反垄断"关键词。
    if not fabao_eligible:
        print(
            f"北大法宝门禁: [{task_type or '未知任务'}] fabao_retry_eligible=False "
            f"(质量分={quality_score}, 已重试 {retry_count}/{max_retries})"
            f" — 未进入 3 次重试失败独占通道, 跳过付费询问"
        )
        return {"fabao_skipped": True, "fabao_retry_count": 0}

    # ② sanity 兜底: 即便 eligible 被误置为 True，高分 (>85) 也不打扰
    if quality_score >= FABAO_SANE_SKIP_SCORE:
        print(
            f"北大法宝门禁: [{task_type or '未知任务'}] 质量分 {quality_score} "
            f"≥ sanity 兜底阈值 {FABAO_SANE_SKIP_SCORE}, 自动跳过付费询问"
        )
        return {"fabao_skipped": True, "fabao_retry_count": 0}

    # ③ 幂等: 本流程已成功调用过付费接口 -> 直接复用, 不重复付费
    if state.get("mcp_invoked", False):
        print("北大法宝门禁: 已成功调用过付费接口, 跳过重复调用")
        return {"fabao_skipped": True, "fabao_retry_count": 0}

    query = (
        state.get("retrieval_query", "")
        or state.get("research_query", "")
        or state.get("input", "")
    )
    top_k = 5

    # ④ 进入付费询问: 免费重试 3 次仍不达标
    print(
        f"北大法宝门禁: [{task_type or '未知任务'}] 免费重试 {retry_count}/{max_retries} 次仍不达标 "
        f"(质量分 {round(quality_score,1)} < 质量门阈值) → "
        f"interrupt() 暂停等待用户确认是否调用北大法宝付费接口"
    )
    decision = _ask_user_interrupt({
        "type": "beida_fabao_confirm",
        "quality_score": quality_score,
        "quality_retry_count": retry_count,
        "quality_max_retries": max_retries,
        "query": query,
        "trigger": {
            "reason": (
                f"免费检索 + 融合/单源排序 + 关键词扩展 + 源切换 连续重试 {retry_count} 次 "
                f"(上限 {max_retries}) 仍低于质量门阈值"
            ),
            "only_after_retries_exhausted": True,
            "sane_skip_score": FABAO_SANE_SKIP_SCORE,
        },
        "message": (
            f"免费检索已连续重试 {retry_count}/{max_retries} 次仍不达标 (质量分 "
            f"{round(quality_score,1)})，是否调用北大法宝付费接口补充权威依据？"
        ),
        "reminder": "北大法宝 MCP 为按调用付费接口, 确认后将使用付费额度。",
    }, label="北大法宝门禁")

    # ⑤ 解析用户决策: True=确认调用 / False·None=拒绝 / str=改写查询 / dict=带 confirm 键
    confirm = False
    if isinstance(decision, dict):
        confirm = bool(decision.get("confirm"))
        if decision.get("query"):
            query = str(decision["query"])
    elif isinstance(decision, str):
        # 字符串视为"编辑后的查询" —— 非空即确认调用(用户主动改写说明想查)
        confirm = bool(decision.strip())
        if confirm:
            query = decision.strip()
    else:
        confirm = bool(decision)

    if not confirm:
        print("北大法宝门禁: 用户拒绝调用付费接口, 返回现有免费结果")
        return {
            "fabao_skipped": True,
            "fabao_retry_count": 0,
            # 不置 human_intervention_needed —— 用户已明确选择, 非异常场景
        }

    # ⑥ 用户确认 -> 调用付费北大法宝 MCP, 最多 3 轮技术性重试
    print(f"北大法宝门禁: 用户确认调用, 开始付费检索 (query={query[:30]}...)")
    try:
        from common.mcp_beidafabao import get_beida_mcp_client
    except ImportError:
        # 客户端模块缺失 -> 视为调用失败 -> 人工介入 + 付费提醒
        return _fail_human_intervention(0)

    client = get_beida_mcp_client()
    fabao_results = []
    retry = 0
    while retry < FABAO_MAX_RETRIES:
        try:
            results = client.search_all(query, top_k=top_k) or []
        except Exception:
            results = []
        if results:
            fabao_results = results
            break
        retry += 1
        print(f"  北大法宝第 {retry}/{FABAO_MAX_RETRIES} 次重试...")

    # ⑦ 成功 -> 追加到 citations / research_context, 标记已调用
    if fabao_results:
        citations = list(state.get("citations", []) or [])
        citations.extend(fabao_results)

        # 拼接 research_context: 只追加可溯源原文, 不主观加工
        existing_ctx = state.get("research_context", "") or ""
        fabao_text = "\n\n".join(
            f"【{r.get('source', '北大法宝')}】{r.get('title', '')}"
            f"{(' ' + r.get('article_no', '')) if r.get('article_no') else ''}"
            f"\n{r.get('content', '')}"
            for r in fabao_results
        )
        merged_ctx = existing_ctx
        if merged_ctx and not merged_ctx.endswith("\n"):
            merged_ctx += "\n\n"
        merged_ctx += "【北大法宝·付费补充依据】\n" + fabao_text

        print(f"  北大法宝成功召回 {len(fabao_results)} 条 (重试 {retry} 次)")
        return {
            "citations": citations,
            "research_context": merged_ctx,
            "fabao_skipped": False,
            "fabao_retry_count": retry,
            "mcp_invoked": True,
        }

    # ⑧ 失败 (3 轮仍无有效结果) -> 人工介入 + 付费提醒
    return _fail_human_intervention(retry)


def _fail_human_intervention(retry: int) -> dict:
    """重试后仍失败 / interrupt 不可用: 置人工介入 + 付费提醒, 不阻断主流程"""
    print(f"北大法宝门禁: 重试 {retry} 次仍无有效结果, 转人工介入 + 付费提醒")
    return {
        "fabao_skipped": False,
        "fabao_retry_count": retry,
        "human_intervention_needed": True,
        "human_intervention_prompt": (
            "北大法宝付费检索接口连续多次调用未返回有效结果，"
            "可能触发额度限制或网络异常。请确认付费额度/Token 配置后重试，"
            "或人工补充权威法律依据。"
        ),
    }


# 脚本直接运行时的自测入口(脱离图运行 -> interrupt 降级路径)
if __name__ == "__main__":
    # V5 门禁: 只有 fabao_retry_eligible=True 才进入询问分支
    #        因此下面构造「质量门 3 次重试失败」状态；无图运行时 interrupt 降级为人工介入，
    #        最终 fabao_skipped=True，不会静默调用付费接口。
    s = AgentState(
        task_type="legal_research",
        quality_score=30,
        quality_retry_count=3,
        quality_max_retries=3,
        fabao_retry_eligible=True,               # 唯一触发条件 (质量门 3 次重试失败后置位)
        api_sources=[],                           # 无论这个字段是什么，都不影响门禁触发
        retrieval_query="民法典 违约金调整规则",
        citations=[{"title": "民法典", "article_no": "第585条",
                    "content": "违约金规定", "source": "L1·领域库"}],
        research_context="【民法典第585条】违约金规定",
    )
    result = beida_fabao_gate_node(s)
    print(f"门禁结果(无图运行, interrupt 降级): {result}")
    # 预期: fabao_retry_eligible=True 才进入询问; 无图运行 interrupt 降级为用户拒绝,
    # 最终 fabao_skipped=True (不计费), 不会静默调用付费接口。
