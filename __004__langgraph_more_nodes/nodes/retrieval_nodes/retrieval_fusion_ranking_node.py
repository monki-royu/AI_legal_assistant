"""
Stage 3: 融合排序层 (Fusion & Ranking)
=========================================

【功能】
接收精准过滤层(或实体召回层)的结果, 进行多信号融合打分 → MD5 去重 → S/A/B/C 分级 → 质量门评估

【数据流】
  输入: state 中的 precision_results (优先) 或 entity_recall_results
        每条 citation 已被 citation_meta.enrich_citations 回填了:
        - source_id (从 source 字段解析, 如 "graph::laws" → "laws")
        - law_level (从 source_id 直接映射: laws→law, regulations→administrative_regulation, ...)
        - case_level (仅 cases 源: 默认 ordinary, 标题有关键词时升级)
        - recall_score / rerank_score / precision_score 等前置分数
  输出: 融合+去重+分级后的 citations 列表, 以及质量分/分级统计

【7 项融合打分】(最终加权公式: Σ score_i × weight_i)
  graph_score × 0.30  — 图谱相关性 (召回分×0.6 + 重排分×0.2 + 路径加成 + 0.2)
  faiss_score × 0.20  — FAISS 语义分数
  authority_score × 0.20 — 权威性 (5 知识源权重 + law_level/case_level 精炼)
  precision_score × 0.15 — 精准过滤层传入的分数
  jump_bonus × 0.05   — 语义跳转增益 (路径含 CAUSES/LEADS_TO 等关系时 +0.1)
  law_level_bonus × 0.05 — 法律层级加成 (law_level 非 None 时按 rank/4.0 给加成)
  case_level_bonus × 0.05 — 案例层级加成 (case_level 非 None 时按 rank/4.0 给加成)

【权威性评分逻辑】(_compute_authority_score, 本文件的核心 + 用户疑问点)
  1. 从 citation 提取 source_id (直接取 source_id 字段, 或从 source 字段解析)
  2. 用 source_id 在 _SOURCE_AUTHORITY 中查基础权重 (5 知识源各有固定权重)
  3. 若 law_level 非 None (法律/法规/解释/行业源), 按 LAW_LEVEL_RANK 给加成
  4. 若 case_level 非 None (案例源), 按 CASE_LEVEL_RANK 给加成
  5. 最终 = 基础权重 + 加成, 截断在 [0.0, 1.0]

【分级】
  S ≥ 0.9 / A ≥ 0.8 / B ≥ 0.7 / C < 0.7

【质量门】
  基于覆盖度(数量) + 相关性(final_score 均值) + 权威性(authority_score 均值)计算质量分 (0-100), 阈值 60 分
"""

# 导入 hashlib: 用于生成 citation 的 MD5 哈希, 实现内容去重
import hashlib

# 导入 Dict/List 类型注解, 提升代码可读性
from typing import List, Dict

# 从 AgentState 引入状态定义: 本节点读取 precision_results/entity_recall_results, 写回 citations/quality_score 等
from __004__langgraph_more_nodes.agent_state import AgentState

# 从 citation_meta 引入:
#   - LAW_LEVEL_RANK: 法律层级权重 (law=4, administrative_regulation=3, department_rule=2, judicial_interpretation=1, other=0)
#   - CASE_LEVEL_RANK: 案例层级权重 (guiding=4, gazette=3, typical=2, ordinary=1)
#   - enrich_citations: 批量回填 citation 元数据 (law_level/case_level 等)
#   - _extract_source_id: 从 source 字段解析 source_id (如 "graph::laws" → "laws")
from common.citation_meta import LAW_LEVEL_RANK, CASE_LEVEL_RANK, enrich_citations, _extract_source_id
# 5 知识源权威性基础权重统一收口到 common/retrieval_shared (单一真相源, fusion/precision 共用)
from common.retrieval_shared import _SOURCE_AUTHORITY
# 统一日志 (阶段 6 全量替换 print 的落点, 本文件先行接入)
from common.logger import get_logger


# ---------------------------------------------------------------------------
# 图谱关系权重表: 用于计算"路径加成"
#   当图谱召回的路径包含某关系时, 该关系权重越高, 路径加成越大
# ---------------------------------------------------------------------------
_RELATION_WEIGHT = {
    "DEFINES": 1.0,       # 定义关系 (最核心, 条款定义概念)
    "HAS_PENALTY": 0.9,   # 处罚关系
    "HAS_LIABILITY": 0.9, # 责任关系
    "REGULATES": 0.8,     # 规范行为
    "INVOLVES": 0.7,      # 涉及主体
    "HAS_CONDITION": 0.6, # 条件关系
    "CAUSES": 0.85,       # 因果关系 (行为→处罚)
    "LEADS_TO": 0.85,     # 推导关系 (行为→责任)
    "INCLUDES": 0.75,     # 包含关系
    "ESTABLISHES": 0.8,   # 确立关系 (行为→权利)
    "PERFORMED_BY": 0.7,  # 行为主体
    "RELATED_TO": 0.5,    # 概念关联 (最弱)
}

# 质量门阈值: 质量分 >= 60 才算通过 (多源融合场景)
QUALITY_GATE_THRESHOLD = 60

# 单源直查场景的质量门阈值: 放宽到 50
# 【为什么放宽】纯判例库与法规库的相关性分数标准差不同, 单源池的 final_score
# 由 precision_score/recall_score 线性合成, 分布区间与多源融合池不一致,
# 沿用 60 会把大量有效单源结果误判为不达标。
# 【历史 bug】该常量原本定义在函数体内, 且打印时误引用了从未定义的 THRESHOLD
# → NameError。因单源分支长期不可达(字段被 LangGraph 丢弃)而未被发现。
SINGLE_SOURCE_THRESHOLD = 50

# Top-K 截断: 融合后最多保留 12 条结果
TOP_K = 12


def _content_hash(c: Dict) -> str:
    """基于 title + article_no + content 前缀生成 MD5 哈希, 用于 citation 去重.

    参数:
        c (Dict): 单条 citation, 包含 title/article_no/content 等字段
    返回:
        str: MD5 十六进制哈希值 (32 字符)
    逻辑:
        把 title|article_no|content 前 80 字符拼成原始字符串, MD5 后返回.
        用 content 前 80 字符而非全文, 是为了避免同一条款因尾部空白/换行差异被误判为不同条目.
    """
    # 拼接唯一键: 标题 + 条款号 + 内容前 80 字符
    raw = f"{c.get('title', '')}|{c.get('article_no', '')}|{str(c.get('content', ''))[:80]}"
    # MD5 哈希: 32 字符十六进制串, 用于快速去重
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _compute_graph_score(r: Dict) -> float:
    """图谱相关性分数 (0-1).

    参数:
        r (Dict): 单条 citation
    返回:
        float: 图谱分数, 范围 [0.0, 1.0]
    数据流:
        1. 若 recall_source != "graph" (非图谱召回), 直接返回 0.5 (中性分)
        2. 取 recall_score (召回分) 和 rerank_score (重排分)
        3. 取 graph_path (图路径), 计算路径加成:
           - 路径长度 × 0.05, 上限 0.2
           - 路径中所有关系权重的最大值 × 0.2
           - 两者取较大值作为 path_bonus
        4. 最终 = recall_score × 0.6 + rerank_score × 0.2 + path_bonus + 0.2
    """
    # 非图谱召回源: 给中性分 0.5, 不参与图谱加成计算
    if r.get("recall_source") != "graph":
        return 0.5

    # 从 citation 取召回分和重排分 (默认 0.5 中性分)
    base_score = r.get("recall_score", 0.5)
    rerank_score = r.get("rerank_score", 0.5)

    # 取图路径 (可能为空列表)
    graph_path = r.get("graph_path", [])
    path_bonus = 0.0  # 路径加成初始值

    if graph_path:
        # 路径长度加成: 每多一跳 +0.05, 上限 0.2 (鼓励多跳推理, 但不过度)
        path_len = len(graph_path)
        path_bonus = min(0.2, path_len * 0.05)
        # 关系权重加成: 取路径中最大关系权重 × 0.2
        for rel in graph_path:
            rel_weight = _RELATION_WEIGHT.get(rel, 0.5)  # 未定义的关系默认 0.5
            path_bonus = max(path_bonus, rel_weight * 0.2)

    # 加权求和, 截断在 [0.0, 1.0]
    return min(1.0, base_score * 0.6 + rerank_score * 0.2 + path_bonus + 0.2)


def _compute_faiss_score(r: Dict) -> float:
    """FAISS 语义分数 (0-1).

    参数:
        r (Dict): 单条 citation
    返回:
        float: FAISS 分数, 范围 [0.0, 1.0]
    数据流:
        1. 若 recall_source == "faiss" (FAISS 召回), 直接用 recall_score
        2. 否则若有 rerank_score, 用 rerank × 0.8 折算 (FAISS 重排分数不是真正的 FAISS 召回分, 打折)
        3. 都没有则返回 0.5 (中性分)
    """
    # FAISS 源: recall_score 就是 FAISS 相似度分数
    if r.get("recall_source") == "faiss":
        return r.get("recall_score", 0.5)
    # 其他源但有 FAISS 重排分: 打折折算
    elif r.get("rerank_score"):
        return r.get("rerank_score", 0.5) * 0.8
    # 兜底: 中性分
    return 0.5


def _compute_authority_score(r: Dict) -> float:
    """权威性分数 (0-1) — 本函数的核心 + 用户疑问点.

    【之前的问题】
      旧代码只用 law_level 和 case_level 两个字段评分, 5 个知识源的权重(_SOURCE_AUTHORITY)
      只是兜底. 当 citation 有 law_level 时, case_level 被忽略; 反之亦然. 导致:
      - cases 源的 citation 拿到 0.5 兜底分, 而不是 0.8 (cases 的基础权重)
      - law_level 和 case_level 无法共存 (法律+案例混合场景得分偏低)

    【修复后的逻辑】
      1. 从 citation 提取 source_id (直接取 source_id 字段, 或从 source 字段解析 "graph::laws" → "laws")
      2. 用 source_id 在 _SOURCE_AUTHORITY 中查 5 知识源的基础权重
      3. 若 law_level 非 None (法律/法规/解释/行业源), 按 LAW_LEVEL_RANK 给加成
         - 公式: LAW_LEVEL_RANK[law_level] / 4.0 × 0.2
         - law=0.2, administrative_regulation=0.15, department_rule=0.1, judicial_interpretation=0.05
      4. 若 case_level 非 None (案例源), 按 CASE_LEVEL_RANK 给加成
         - 公式: CASE_LEVEL_RANK[case_level] / 4.0 × 0.2
         - guiding=0.2, gazette=0.15, typical=0.1, ordinary=0.05
      5. 最终 = 基础权重 + law_level 加成 + case_level 加成, 截断在 [0.0, 1.0]

    参数:
        r (Dict): 单条 citation, 需包含 source_id 或 source 字段
    返回:
        float: 权威性分数, 范围 [0.0, 1.0]
    """
    # ---- 第一步: 提取 source_id ----
    # 优先用 citation 直接携带的 source_id 字段 (entity_recall_node 已添加)
    # 兜底: 从 source 字段解析 (如 "graph::laws" → "laws")
    source_id = r.get("source_id", "") or _extract_source_id(r.get("source", ""))

    # ---- 第二步: 查 5 知识源的基础权重 ----
    # _SOURCE_AUTHORITY 覆盖全部 5 个源: laws/regulations/interpretations/cases/industry_sources
    base_score = _SOURCE_AUTHORITY.get(source_id, 0.5)  # 未知源给 0.5 中性分

    # ---- 第三步: law_level 加成 (法律层级精炼) ----
    law_level = r.get("law_level", "")
    law_level_bonus = 0.0
    if law_level and law_level in LAW_LEVEL_RANK:
        # LAW_LEVEL_RANK: law=4, administrative_regulation=3, department_rule=2, judicial_interpretation=1, other=0
        # 除以 4.0 归一化到 [0.0, 1.0], 再乘以 0.2 限制加成范围
        law_level_bonus = LAW_LEVEL_RANK[law_level] / 4.0 * 0.2

    # ---- 第四步: case_level 加成 (案例层级精炼) ----
    case_level = r.get("case_level", "")
    case_level_bonus = 0.0
    if case_level and case_level in CASE_LEVEL_RANK:
        # CASE_LEVEL_RANK: guiding=4, gazette=3, typical=2, ordinary=1
        # 同理归一化 × 0.2 限制加成范围
        case_level_bonus = CASE_LEVEL_RANK[case_level] / 4.0 * 0.2

    # ---- 第五步: 最终分数 = 基础 + 两个加成 ----
    score = base_score + law_level_bonus + case_level_bonus

    # 截断到 [0.0, 1.0] 范围 (正常不会超过 1.0, 但防御性截断)
    return min(1.0, score)


def _compute_jump_bonus(r: Dict) -> float:
    """语义跳转增益 (0 或 0.1).

    当图谱召回路径包含"语义跳转"关系时, 给额外加分.
    这些关系表示图谱中存在"行为→后果"或"责任→内容"的语义连接,
    意味着检索到的法条可以直接回答"为什么/怎么办"类问题.

    跳转关系类型: CAUSES (行为→处罚) / LEADS_TO (行为→责任) / INCLUDES (责任→内容)
                  ESTABLISHES (行为→权利) / PERFORMED_BY (行为→主体)

    参数:
        r (Dict): 单条 citation
    返回:
        float: 0.1 (有跳转关系) 或 0.0 (无跳转关系)
    """
    # 取图路径
    graph_path = r.get("graph_path", [])
    # 无路径: 无跳转增益
    if not graph_path:
        return 0.0
    # 定义跳转关系集合
    jump_relations = {"CAUSES", "LEADS_TO", "INCLUDES", "ESTABLISHES", "PERFORMED_BY"}
    # 路径中有任一跳转关系 → +0.1
    has_jump = any(rel in jump_relations for rel in graph_path)
    return 0.1 if has_jump else 0.0


def _compute_final_score(r: Dict) -> float:
    """7 项加权融合分数 (0-1).

    本函数把 7 个子分数按权重加权求和, 得到最终排序依据.
    权重总和 = 0.30 + 0.20 + 0.20 + 0.15 + 0.05 + 0.05 + 0.05 = 1.0

    参数:
        r (Dict): 单条 citation, 需包含所有子分数所需字段
    返回:
        float: 最终融合分数, 范围 [0.0, 1.0]
    数据流:
        1. 调用 _compute_graph_score → graph_score
        2. 调用 _compute_faiss_score → faiss_score
        3. 调用 _compute_authority_score → authority_score (5 知识源权重 + law/case 加成)
        4. 取 precision_score (由精准过滤层写入)
        5. 调用 _compute_jump_bonus → jump_bonus
        6. 计算 law_level_bonus (若 law_level 非 None)
        7. 计算 case_level_bonus (若 case_level 非 None)
        8. 加权求和, 截断到 [0.0, 1.0]
    """
    # ---- 1. 图谱分数 (权重 0.30, 最大权重, 图谱召回是核心) ----
    graph_score = _compute_graph_score(r)

    # ---- 2. FAISS 语义分数 (权重 0.20) ----
    faiss_score = _compute_faiss_score(r)

    # ---- 3. 权威性分数 (权重 0.20, 5 知识源权重 + law/case 加成) ----
    authority_score = _compute_authority_score(r)

    # ---- 4. 精准分数 (权重 0.15, 由精准过滤层 _compute_precision_score 写入) ----
    precision_score = r.get("precision_score", 0.5)

    # ---- 5. 跳转增益 (权重 0.05, 有语义跳转关系时 +0.1) ----
    jump_bonus = _compute_jump_bonus(r)

    # ---- 6. 法律层级加成 (权重 0.05, law_level 非 None 时按 rank/4.0 计算) ----
    law_level = r.get("law_level", "")
    law_level_bonus = LAW_LEVEL_RANK[law_level] / 4.0 if law_level in LAW_LEVEL_RANK else 0.0

    # ---- 7. 案例层级加成 (权重 0.05, case_level 非 None 时按 rank/4.0 计算) ----
    case_level = r.get("case_level", "")
    case_level_bonus = CASE_LEVEL_RANK[case_level] / 4.0 if case_level in CASE_LEVEL_RANK else 0.0

    # ---- 加权求和 ----
    final_score = (
        graph_score * 0.30 +
        faiss_score * 0.20 +
        authority_score * 0.20 +
        precision_score * 0.15 +
        jump_bonus * 0.05 +
        law_level_bonus * 0.05 +
        case_level_bonus * 0.05
    )
    # 截断到 [0.0, 1.0]
    return min(1.0, final_score)


def _assign_grade(score: float) -> str:
    """根据融合分数分配等级 (S/A/B/C).

    参数:
        score (float): 融合分数, 范围 [0.0, 1.0]
    返回:
        str: "S" (≥0.9) / "A" (≥0.8) / "B" (≥0.7) / "C" (<0.7)
    """
    if score >= 0.9:
        return "S"
    elif score >= 0.8:
        return "A"
    elif score >= 0.7:
        return "B"
    else:
        return "C"


def _calculate_quality_score(citations: List[Dict], query: str = "") -> float:
    """质量分 (0~100): 覆盖度×0.4 + 相关性×0.4 + 权威性×0.2.

    覆盖度: 根据 citation 数量打分 (越多越好, 但边际递减)
      n≥10 → 100, n≥5 → 80, n≥3 → 60, n≥1 → 40, n=0 → 20
    相关性: 所有 citation 的 final_score 均值 × 100
    权威性: 所有 citation 的 authority_score 均值 × 100

    参数:
        citations (List[Dict]): 融合排序后的 citation 列表
        query (str): 原始检索查询。**预留参数, 当前不参与计算** ——
            相关性已由 final_score 体现, 而 final_score 是召回/重排阶段
            基于 query 算出的, 此处再引入 query 会重复计入。
            保留形参是为了让调用方可以透传查询上下文, 便于未来扩展
            (如查询词命中率、覆盖率按 query term 拆分等)。
    返回:
        float: 质量分, 范围 [0.0, 100.0], 保留 1 位小数

    【历史 bug】本函数原只声明 1 个形参, 但多源分支调用处传了 2 个实参
    (`_calculate_quality_score(top_results, query)`) → TypeError。
    因多源分支长期不可达(字段被 LangGraph 丢弃)而未被发现。
    """
    # 空列表: 最低分 20
    if not citations:
        return 20.0

    # ---- 覆盖度分 (40% 权重) ----
    n = len(citations)
    if n >= 10:
        coverage_score = 100.0
    elif n >= 5:
        coverage_score = 80.0
    elif n >= 3:
        coverage_score = 60.0
    elif n >= 1:
        coverage_score = 40.0
    else:
        coverage_score = 20.0

    # ---- 相关性分 (40% 权重) ----
    # 取所有 citation 的 final_score 均值, 乘以 100 转百分制
    final_scores = [c.get("final_score", 0) for c in citations]
    relevance_score = min(100.0, sum(final_scores) / max(1, len(final_scores)) * 100)

    # ---- 权威性分 (20% 权重) ----
    # 重新调用 _compute_authority_score 计算每条 citation 的权威性
    authority_scores = [_compute_authority_score(c) for c in citations]
    authority_score = min(100.0, sum(authority_scores) / max(1, len(authority_scores)) * 100)

    # ---- 加权求和: 覆盖度 40% + 相关性 40% + 权威性 20% ----
    quality = coverage_score * 0.4 + relevance_score * 0.4 + authority_score * 0.2
    # 截断到 [0.0, 100.0], 保留 1 位小数
    return round(max(0.0, min(100.0, quality)), 1)


def _build_retrieval_eval(recall_stats: Dict, filter_stats: Dict,
                          top_results: List[Dict], quality_score: float,
                          quality_passed: bool, fusion_mode: str) -> Dict:
    """汇总本轮检索的四维评估, 写入 state["retrieval_eval"]

    【接线背景】
      - retrieval_eval 在 AgentState 中早已声明, 但**从未被任何节点写入** (僵尸字段),
        agent_state.py 的注释还写着"由 retrieval_fusion_ranking_node 写入", 实际没实现。
      - recall_stats (Stage1) / filter_stats (Stage2) 一直被上游写入但**零消费方**。
      本函数同时解决这两件事: 把两阶段统计汇总进 retrieval_eval, 供前端诊断面板消费。

    参数:
        recall_stats: Stage1 各召回通道命中数 {graph, faiss, keyword}
        filter_stats: Stage2 各级过滤计数 {input, keyword_pass, rerank, authority_pass, final}
        top_results: 融合排序后的 Top-K citation 列表
        quality_score: 本轮质量分 (0-100)
        quality_passed: 是否通过质量门
        fusion_mode: "single_source" 单源直查 / "weighted" 多源加权融合

    返回:
        Dict: 四维评估 + 两阶段明细, 全部为可 JSON 序列化的基础类型
    """
    # 分级计数: 统计 Top-K 中 S/A/B/C 各多少条, 反映结果整体质量分布
    grade_counts = {"S": 0, "A": 0, "B": 0, "C": 0}
    for r in top_results:
        grade = r.get("grade", "C")
        grade_counts[grade] = grade_counts.get(grade, 0) + 1

    # 过滤率: (输入条数 - 最终条数) / 输入条数, 反映 Stage2 的过滤强度。
    # 过滤率过高说明召回噪声大, 过低说明精准过滤层几乎没起作用, 都值得关注。
    f_in = int(filter_stats.get("input") or 0)
    f_out = int(filter_stats.get("final") or 0)
    filter_rate = round((f_in - f_out) / f_in, 4) if f_in > 0 else 0.0

    return {
        "fusion_mode": fusion_mode,
        "quality_score": quality_score,
        "quality_gate_passed": quality_passed,
        "top_k": len(top_results),
        "grade_counts": grade_counts,
        "recall_stats": dict(recall_stats),    # Stage1 明细
        "filter_stats": dict(filter_stats),    # Stage2 明细
        "filter_rate": filter_rate,
    }


def retrieval_fusion_ranking_node(state: AgentState) -> Dict:
    """Stage 3 融合排序层主入口.

    【在 LangGraph 中的位置】
    接收 retrieval_precision_filter_node (精准过滤) 或 retrieval_entity_recall_node (实体召回) 的输出,
    进行多信号融合打分 → MD5 去重 → S/A/B/C 分级 → 质量门评估, 最终输出给下游问答生成节点.

    【数据流转】
    state["precision_results"] (Stage 2 产出) ──┐
                                                ├──→ pool (待融合池)
    state["entity_recall_results"] (Stage 1) ──┘

    pool → enrich_citations (回填 law_level/case_level/source_id)
        → _compute_final_score (7 项融合打分, 每条 citation 写入 final_score 等字段)
        → _content_hash (MD5 去重)
        → 按 final_score 降序排序
        → _assign_grade (S/A/B/C 分级)
        → Top-K 截断 (12 条)
        → 拼装 research_context (给 LLM 的上下文)
        → _calculate_quality_score (质量分, 0-100)

    【返回】
    Dict: {
        "citations": 融合排序后的 citation 列表 (最多 12 条),
        "research_context": 拼装好的研究上下文 (给 LLM 用),
        "quality_score": 质量分 (0-100),
        "quality_gate_passed": 质量分是否 ≥ 60 (bool),
        "retrieval_eval": 四维评估汇总 (含 Stage1/Stage2 统计, 供前端诊断面板)
    }
    """
    print("检索 [Stage 3] 融合排序层 (多信号融合 + citation_meta)")

    # 引入统一日志 (阶段 6 会把本文件的 print 全量替换掉, 这里先行接入)
    logger = get_logger(__name__)

    # 跨阶段统计: Stage1 召回 / Stage2 过滤的详细计数。
    # 【接线背景】recall_stats / filter_stats 此前一直被上游写入但零消费方;
    #   而 AgentState 里已声明的 retrieval_eval 又从未被任何节点写入(僵尸字段)。
    #   本节点位于 Stage1/2/3 的交汇点, 正好把它们汇总成 retrieval_eval 一并输出。
    recall_stats = state.get("recall_stats") or {}
    filter_stats = state.get("filter_stats") or {}
    logger.debug("Stage1 召回统计=%s, Stage2 过滤统计=%s", recall_stats, filter_stats)

    # ---- 1. 获取输入数据 ----
    # 优先使用精准过滤层的结果, 若为空则降级到实体召回层
    precision_results = state.get("precision_results", []) or []
    recall_results = state.get("entity_recall_results", []) or []
    query = state.get("retrieval_query", "") or state.get("input", "") or ""
    domain_sources = state.get("domain_sources", []) or []

    # 选择待融合池: 精准过滤结果优先, 否则用召回结果
    pool = precision_results if precision_results else recall_results

    # 空池: 直接返回空结果
    if not pool:
        print("  无检索结果, 融合跳过")
        return {
            "citations": [],
            "quality_score": 20.0,
            "quality_gate_passed": False,
            "research_context": "",
            "fusion_mode": "empty",
            "retrieval_eval": _build_retrieval_eval(
                recall_stats, filter_stats, [], 20.0, False, "empty"),
        }

    # ------------------------------------------------------------------
    # V4 skip_fusion 分支: 单源任务 (case_search) 不做多源融合,
    # 直接走 enrich + 按已有的排序分数(recall_score/precision_score 加权混合)排序+去重+Top-K.
    # 这样 cases 不会因 authority_score 低于 laws 被错压, 12 条名额全给类案.
    # 判定条件: len(domain_sources) <= 1
    # ------------------------------------------------------------------
    if len(domain_sources) <= 1:
        print(f"  [skip_fusion] 单源 (domain_sources={domain_sources}) → 跳过 7 项融合/RRF, 按检索分直接排序")
        pool = enrich_citations(pool)

        # 每条 citation 回填一个 proxy final_score = 0.7*precision+0.3*recall（都没有则取 content_hash 的稳定性）
        for r in pool:
            p = float(r.get("precision_score", r.get("rerank_score", 0)) or 0)
            rec = float(r.get("recall_score", r.get("faiss_score", 0)) or 0)
            score = 0.7 * p + 0.3 * rec
            if score <= 0:
                # 完全没有结构化分数时, 用召回层内置的 final_score / graph_score 兜底
                score = max(float(r.get("final_score", 0) or 0), float(r.get("graph_score", 0) or 0))
            r["final_score"] = round(score, 4)
            r["authority_score"] = _compute_authority_score(r)
            r["graph_score"] = _compute_graph_score(r)
            r["faiss_score"] = _compute_faiss_score(r)
            r["jump_bonus"] = _compute_jump_bonus(r)

        # MD5 去重 + 按 proxy 分降序
        seen = set(); deduped = []
        for r in pool:
            h = _content_hash(r)
            if h not in seen:
                seen.add(h); deduped.append(r)
        deduped.sort(key=lambda x: x.get("final_score", 0), reverse=True)

        # 分级 + Top-K
        for r in deduped:
            r["grade"] = _assign_grade(r.get("final_score", 0))
        top_results = deduped[:TOP_K]

        grade_counts = {"S": 0, "A": 0, "B": 0, "C": 0}
        for r in top_results:
            grade_counts[r.get("grade", "C")] += 1
        print(f"  单源排序结果: S={grade_counts['S']}, A={grade_counts['A']}, B={grade_counts['B']}, C={grade_counts['C']} (取 Top{len(top_results)})")

        research_context = ""
        if top_results:
            research_context = "\n\n".join([
                f"【{r.get('title', '')}】{r.get('article_no', '')} "
                f"[{r.get('grade', '')}级, 分={r.get('final_score', 0):.2f}]\n"
                f"{r.get('content', '')[:200]}"
                for r in top_results
            ])

        quality_score = _calculate_quality_score(top_results, query)
        quality_gate_passed = quality_score >= SINGLE_SOURCE_THRESHOLD
        print(f"  单源质量分: {quality_score} 阈值={SINGLE_SOURCE_THRESHOLD} "
              f"{'✅ 通过' if quality_gate_passed else '⚠️ 未达阈值'}")

        return {
            "citations": top_results,
            "research_context": research_context,
            "quality_score": quality_score,
            "quality_gate_passed": quality_gate_passed,
            "fusion_mode": "single_source",
            "retrieval_eval": _build_retrieval_eval(
                recall_stats, filter_stats, top_results,
                quality_score, quality_gate_passed, "single_source"),
        }

    # ---- 2. 回填 citation 元数据 ----
    # 用 citation_meta.enrich_citations 给每条 citation 回填:
    #   - law_level (从 source_id 直接映射, 而非标题启发式)
    #   - case_level (仅 cases 源, 默认 ordinary + 标题升级)
    #   - data_source_authority (数据源权威)
    #   - legal_domain (法律领域)
    pool = enrich_citations(pool)

    # ---- 3. 多信号融合计算 ----
    # 遍历每条 citation, 计算 7 项分数并写入 citation 本身
    for r in pool:
        r["final_score"] = _compute_final_score(r)      # 7 项加权融合分数 (主排序依据)
        r["graph_score"] = _compute_graph_score(r)       # 图谱分数 (用于调试/可视化)
        r["faiss_score"] = _compute_faiss_score(r)       # FAISS 分数 (用于调试/可视化)
        r["authority_score"] = _compute_authority_score(r)  # 权威性分数 (5 源权重 + law/case 加成)
        r["jump_bonus"] = _compute_jump_bonus(r)         # 跳转增益 (用于调试/可视化)

    print(f"  融合分数计算完成: {len(pool)} 条")

    # ---- 4. MD5 去重 ----
    # 基于 title + article_no + content 前缀的 MD5 哈希, 去除重复 citation
    seen = set()
    deduped = []
    for r in pool:
        h = _content_hash(r)      # 计算哈希
        if h not in seen:          # 未见过则保留
            seen.add(h)
            deduped.append(r)

    print(f"  去重: {len(pool)} → {len(deduped)} 条")

    # ---- 5. 按 final_score 降序排序 ----
    deduped.sort(key=lambda x: x.get("final_score", 0), reverse=True)

    # ---- 6. S/A/B/C 分级 ----
    # 给每条 citation 打等级标签
    for r in deduped:
        r["grade"] = _assign_grade(r.get("final_score", 0))

    # ---- 7. Top-K 截断 (最多 12 条) ----
    top_results = deduped[:TOP_K]

    # ---- 8. 分级统计 (用于日志/可视化) ----
    grade_counts = {"S": 0, "A": 0, "B": 0, "C": 0}
    for r in top_results:
        grade_counts[r.get("grade", "C")] += 1

    print(f"  结果分级: S={grade_counts['S']}, A={grade_counts['A']}, "
          f"B={grade_counts['B']}, C={grade_counts['C']}")

    # ---- 9. 拼装 research_context ----
    # 把 Top-K citation 格式化成 LLM 可直接消费的文本上下文
    research_context = ""
    if top_results:
        research_context = "\n\n".join([
            f"【{r.get('title', '')}】{r.get('article_no', '')} "
            f"[{r.get('grade', '')}级, 分={r.get('final_score', 0):.2f}]\n"
            f"{r.get('content', '')[:200]}"
            for r in top_results
        ])

    # ---- 10. 质量门评估 ----
    # 基于覆盖度 + 相关性 + 权威性计算质量分 (0-100)
    quality_score = _calculate_quality_score(top_results, query)
    quality_gate_passed = quality_score >= QUALITY_GATE_THRESHOLD

    gate_status = "✅ 通过" if quality_gate_passed else "⚠️ 未达阈值"
    print(f"  质量分: {quality_score} {gate_status}")

    # 多源场景固定走权威加权线性融合 (非 RRF)
    # 【注】fusion_mode 在 AgentState 中已声明, 但此前从未被本节点写入(僵尸字段),
    #   前端/诊断面板拿不到"本次检索是单源直查还是多源融合"的信息。本轮补上。
    fusion_mode = "weighted"

    # ---- 11. 返回结果 ----
    return {
        "citations": top_results,                     # 融合排序后的 citation 列表
        "research_context": research_context,         # 给 LLM 的研究上下文
        "quality_score": quality_score,               # 质量分 (0-100)
        "quality_gate_passed": quality_gate_passed,   # 是否通过质量门
        "fusion_mode": fusion_mode,                   # 本次融合模式
        "retrieval_eval": _build_retrieval_eval(      # 四维评估 + Stage1/2 明细
            recall_stats, filter_stats, top_results,
            quality_score, quality_gate_passed, fusion_mode),
    }
