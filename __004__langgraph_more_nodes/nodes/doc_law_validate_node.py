# -*- coding: utf-8 -*-
"""
法律文书生成 - 法条校验节点 (N_doc4)
====================================

【功能】
对 doc_clause_fill（条款填充节点）输出的 cited_laws 做逐条真实性校验
（Law Validation / Anti-Hallucination 防幻觉第二道防线）。
校验方式：
  1. 取每条引用中的 law_name + article_no，在知识库（laws_docs.json）中精确回查；
  2. 若知识库中存在且内容相似度 >= 0.9 → passed（通过校验，保留原文）；
  3. 若知识库中存在但内容相似度 < 0.9 → altered（已校正，用知识库原文覆盖
     该条的 content，并打上 note="已根据知识库校正"标记）；
  4. 若知识库中不存在 → fake（标记为虚假引用，移除或标注，不放入 corrected_cited）。

【流程位置】（文书生成链路 6 步中的第 4 步）
  [3/6] 条款填充 → [4/6] 法条校验（本节点，纯确定性逻辑 / Deterministic）
  → [5/6] 风险提示 / [5b/6] 类案推荐 → [6/6] 最终交付

【下游】
若全部引用均为 fake → need_refill=True，由 langgraph_main 的路由循环回
doc_clause_fill 重填（重生成草稿）；
否则进入 doc_risk_advisor 继续。

【设计亮点】
本节点是"纯确定性校验"（Deterministic Validation），不调用 LLM：
用查表（law_map） + 字符串相似度（编辑距离/Jaccard）完成判断，
保证校验结果可复现、零成本、100% 可控。
"""
# 导入标准库 json（本文件主要使用类型标注与数据结构，json 为惯例保留导入）
import json
# 导入 Optional 类型（类型标注用，提示参数可能为 None）
from typing import Optional
# 导入 AgentState 类型：LangGraph 图中各节点共享的状态字典（TypedDict）
from __004__langgraph_more_nodes.agent_state import AgentState
# 导入 RAG 检索引擎实例（engine）：本节点用其 kb.load_docs 加载法规知识库做查表
from common.retrieval_engine import engine as retrieval_engine
# 导入微调数据收集工具：记录本节点的输入/输出为微调样本（可选旁路，失败静默）
from common.finetune_utils import collect_ft_sample


def _lev_similarity(a: str, b: str) -> float:
    """简单编辑距离相似度(0~1)。"""
    # 【功能】计算两个字符串的相似度（0~1），用于判断"LLM 引用的法条内容"
    #         与"知识库原文"是否足够接近（接近 → 引用真实；偏离 → 需要校正）。
    # 【参数】
    #     a (str): 第一个字符串（通常是 LLM 引用的法条内容）
    #     b (str): 第二个字符串（通常是知识库中的法条原文）
    # 【返回值】
    #     float: 相似度，范围 [0.0, 1.0]；完全相同返回 1.0，完全无关趋近 0.0
    # 【逻辑】
    #     1. 任一字符串为空 → 返回 0.0（无法比较）；
    #     2. 各截取前 200 字符（控制计算规模，避免超长文本拖慢速度）；
    #     3. 完全相同 → 返回 1.0（短路快速返回）；
    #     4. 否则用"字符集合的 Jaccard 相似度"近似：交集大小 / 并集大小。
    # 说明：这里名为"编辑距离"但实际实现是 Jaccard 字符相似度，
    #       属于简单近似（Simplified Approximation），够用且速度快。
    if not a or not b:
        # 任一输入为空：无法计算相似度，直接返回 0.0（视为完全不同）
        return 0.0
    # 用最长公共子串近似（注释原意）：实际做法是截取前 200 字符控制计算量
    a, b = a[:200], b[:200]
    if a == b:
        # 截取后完全相同：直接返回 1.0（短路优化，省去集合计算）
        return 1.0
    # 简单 Jaccard 字符相似：把字符串转成字符集合（set），
    # 集合自动去重，只关心"出现了哪些字符"，不关心顺序与频次
    set_a, set_b = set(a), set(b)
    # 交集大小：两个字符串共有的字符种类数
    inter = len(set_a & set_b)
    # 并集大小：两个字符串合起来出现的字符种类总数
    union = len(set_a | set_b)
    # 返回 Jaccard 相似度 = 交集 / 并集；并集为 0（不可能，除非两者都空）时兜底 0.0
    return inter / union if union else 0.0


def doc_law_validate_node(state: AgentState):
    """法条校验节点: 逐条比对知识库, 返回校验结果。"""
    # 【功能】对条款填充节点产出的 cited_laws 逐条做真实性校验（纯确定性逻辑）。
    #         校验结果写入 state["law_validation"]，并输出 need_refill 路由标志。
    # 【参数】
    #     state (AgentState): LangGraph 共享状态字典，本节点读取：
    #         - cited_laws (list[dict]): 待校验的引用法条列表，
    #           每条含 law_name / article_no / content 等字段
    # 【返回值】
    #     dict（合并进 state），包含：
    #         - law_validation: dict
    #           {status: "passed"/"partial_fail"/"all_fail",
    #            details: list[dict]（每条引用的校验明细）,
    #            summary: str（人读的汇总文案）}
    #         - need_refill: bool（True=全部虚假，需路由回条款填充节点重填）
    #         - cited_laws: list[dict]（校正后的引用列表；fake 已被剔除）
    # 【逻辑】
    #     1. 无引用法条 → 直接返回 passed + need_refill=False（跳过校验）；
    #     2. 加载法规知识库文档，构建 (law_name, article_no) → content 快速查找表；
    #     3. 逐条校验：命中知识库且相似度>=0.9 → passed；
    #        命中但相似度<0.9 → altered（用知识库原文覆盖并打 note）；
    #        未命中 → fake（丢弃，不计入 corrected_cited）；
    #     4. 统计 fake/altered/passed 数量，判定整体状态：
    #        - 全部 fake → all_fail + need_refill=True；
    #        - 部分问题 → partial_fail + need_refill=False（已校正）；
    #        - 全部通过 → passed + need_refill=False。
    # 打印日志：标记进入文书生成第 4 步"法条校验（确定性）"
    print("文书生成 [4/6] 法条校验(确定性)")
    # 读取待校验的引用法条列表，缺失时兜底为空列表
    cited_laws = state.get("cited_laws", []) or []
    if not cited_laws:
        # 无引用法条：无需校验，直接返回通过状态（避免空循环）
        print("  无引用法条, 跳过校验")
        # 返回：status=passed（无引用视为通过）、details 空、need_refill=False
        return {"law_validation": {"status": "passed", "details": [], "summary": "无引用法条"}, "need_refill": False}

    # 加载知识库（法规索引）用于快速查表：从检索引擎的知识库组件加载"laws"类文档
    laws_docs = retrieval_engine.kb.load_docs("laws")
    # 构建 (law_name, article_no) -> content 快速查找表（哈希表，O(1) 查找）
    law_map = {}
    # 遍历知识库中的所有法规文档
    for d in laws_docs:
        # 用 (法律名, 条号) 二元组作为哈希键（精确匹配标识一条法条）
        key = (d.get("law_name", ""), d.get("article_no", ""))
        # 值为该法条的原文内容（用于比对与校正覆盖）
        law_map[key] = d.get("content", "")

    # 初始化统计变量与结果容器
    details = []        # 校验明细列表（每条引用的结果记录，供前端展示）
    fake_count = 0      # 虚假引用计数（知识库中不存在）
    altered_count = 0   # 内容不符计数（已用知识库原文校正）
    passed_count = 0    # 通过校验计数
    corrected_cited = []  # 校正后的引用列表（passed 原样保留 + altered 覆盖原文；fake 丢弃）

    # 逐条校验引用法条（核心循环）
    for c in cited_laws:
        # 读取该引用的法律名称，缺失时为空字符串
        name = c.get("law_name", "")
        # 读取该引用的条号（如"第一百零七条"），缺失时为空字符串
        no = c.get("article_no", "")
        # 读取 LLM 引用的条文内容
        content = c.get("content", "")
        # 构造查找键（与 law_map 相同的二元组结构）
        key = (name, no)

        if key in law_map:
            # ── 分支 1：知识库中存在该 (法律名, 条号) ──
            # 取出知识库中的真实条文原文
            real_content = law_map[key]
            # 计算 LLM 引用内容与知识库原文的相似度（0~1）
            sim = _lev_similarity(content, real_content)
            if sim >= 0.9 and no == c.get("article_no", ""):
                # ── 子分支 1a：相似度 >= 0.9 → 通过校验（passed）──
                # 记录明细：法律名、条号、结果=passed、相似度（保留 4 位小数）
                details.append({"law_name": name, "article_no": no, "result": "passed", "similarity": round(sim, 4)})
                # 原样保留该引用到校正后列表（内容可信，无需修改）
                corrected_cited.append(c)
                # 通过计数 +1
                passed_count += 1
            else:
                # ── 子分支 1b：存在但内容不匹配 → 校正（altered）──
                # 记录明细：法律名、条号、结果=altered、原内容摘要、校正后内容摘要、相似度
                details.append({
                    "law_name": name, "article_no": no, "result": "altered",
                    "original": content[:100], "corrected": real_content[:100],
                    "similarity": round(sim, 4),
                })
                # 用知识库真实原文覆盖该引用的 content（防幻觉核心动作：以权威源为准）
                c["content"] = real_content  # 用真实原文覆盖
                # 打上校正标记 note，便于下游/前端展示"此条已校正"
                c["note"] = "已根据知识库校正"
                # 将校正后的引用放入校正后列表
                corrected_cited.append(c)
                # 校正计数 +1
                altered_count += 1
        else:
            # ── 分支 2：知识库中不存在该 (法律名, 条号) → 虚假引用（fake）──
            # 记录明细：法律名、条号、结果=fake、原因=知识库中未找到
            details.append({"law_name": name, "article_no": no, "result": "fake", "reason": "知识库中未找到"})
            # 虚假引用计数 +1
            fake_count += 1
            # 虚假引用不放入 corrected_cited，直接丢弃（从最终文书中移除）

    # ── 判定整体结果（Aggregate Status）──
    # 引用总条数（用于判断"是否全部虚假"）
    total = len(cited_laws)
    if fake_count == total:
        # 全部都是虚假引用：整体状态 all_fail（全失败）
        status = "all_fail"
        need_refill = True   # 全部虚假: 需要回退重新生成（路由回条款填充节点）
    elif fake_count > 0 or altered_count > 0:
        # 部分有问题（有虚假或已校正）：整体状态 partial_fail（部分失败）
        status = "partial_fail"
        need_refill = False  # 部分问题: 已校正, 无需回退（保留校正结果继续走）
    else:
        # 全部通过：整体状态 passed（通过）
        status = "passed"
        need_refill = False

    # 构造人读的汇总文案：✅ N 条通过 · ⚠️ N 条已校正 · ❌ N 条虚假引用(已移除)
    summary = f"✅ {passed_count}条通过 · ⚠️ {altered_count}条已校正 · ❌ {fake_count}条虚假引用(已移除)"
    # 打印汇总文案（控制台可见校验结果）
    print(f"  {summary}")

    # ==== 微调数据收集 ====
    # 微调样本收集块（可选旁路）：记录本节点的输入/输出用于后续模型微调
    try:
        # 构造微调输入：取 state["input"]，转字符串并截取前 2000 字符
        _ft_input = str(state.get("input", "") or "")[:2000]
        # 注意：此行是"裸字典表达式"（bare dict expression），单独成行无任何效果，
        # 属于遗留的无操作语句，此处按原样保留，不改动逻辑。
        {"validation_result": state.get("validation_result", ""), "need_refill": state.get("need_refill", False)
}
        # 调用微调样本收集器（记录节点名、输入、输出、任务类型）
        collect_ft_sample("doc_law_validate", _ft_input, _ft_output,
                          task_type=state.get("task_type", ""))
    except Exception:
        # 微调收集失败（如 _ft_output 未定义）：静默忽略，不影响主流程
        pass
    # 返回校验结果：law_validation（状态+明细+汇总）、need_refill（路由标志）、
    # cited_laws（校正后的引用列表，供下游最终交付使用）
    return {
        "law_validation": {"status": status, "details": details, "summary": summary},
        "need_refill": need_refill,
        "cited_laws": corrected_cited,  # 已校正的引用列表
    }
