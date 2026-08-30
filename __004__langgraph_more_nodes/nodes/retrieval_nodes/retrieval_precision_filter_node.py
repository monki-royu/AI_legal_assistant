"""
Stage 2: 精准过滤层 (Precision Filtering)
=============================================

【功能】
从大量召回结果中筛选精准结果, 提高检索精度.

【三级过滤流程】
  2a. 关键词信号 (方案A: 不淘汰): 计算每条召回的关键词命中数, 仅作 precision 轻量 boost 信号
  2b. FAISS 语义重排序: Embedding 编码 + 余弦相似度, 对全量召回取 Top-K (语义精排, 主导排序)
  2c. 来源分级 + 相关性闸门: 法条类永不硬删, 补充类需过 precision_score 门槛 (替代原无效权威过滤)

【数据流】
  输入: entity_recall_results (Stage 1 召回结果, 每条含 source_id/source/recall_score 等)
  处理:
    1. enrich_citations 回填 law_level/case_level (用 source_id 直接映射)
    2. _keyword_hit_count 关键词信号 (计算命中数, 不淘汰, 仅作 boost)
    3. _faiss_rerank 语义重排序 (全量召回, 取 Top 30)
    4. _attach_authority_score 回填权威分 + _precision_gate 来源分级相关性闸门
  输出: precision_results (精准排序后的结果, 每条带 precision_score)

【集成 citation_meta】
  enrich_citations 回填 law_level/case_level 等元数据, 辅助权威性判断.
  law_level 由 source_id 直接映射 (laws→law, regulations→administrative_regulation, ...)
  case_level 仅 cases 源有值 (默认 ordinary, 标题有关键词时升级)
"""

# 导入 re: 用于关键词分割 (按标点/空白切分)
import re

# 导入 List/Dict 类型注解
from typing import List, Dict

# 从 AgentState 引入状态定义
from __004__langgraph_more_nodes.agent_state import AgentState

# 从 citation_meta 引入:
#   - enrich_citations: 批量回填 law_level/case_level (用 source_id 直接映射)
#   - CASE_LEVEL_RANK: 案例层级权重 (用于 authority_score 的唯一真实区分度加成)
#   - _extract_source_id: 从 source 字段解析 source_id
#   注: LAW_LEVEL_RANK 不再引入 —— law_level 是 source_id 的 1:1 派生, 在 precision/authority
#       里冗余且无区分度, 已由本文件删除其加成 (见 _attach_authority_score / _compute_precision_score)。
from common.citation_meta import enrich_citations, CASE_LEVEL_RANK, _extract_source_id
# 5 知识源权威性基础权重统一收口到 common/retrieval_shared (单一真相源, fusion/precision 共用)
from common.retrieval_shared import _SOURCE_AUTHORITY


# 来源分级 (替代原无效权威过滤):
#   法条类 (强制引用, 永不硬删) — 法律/法规/司法解释是答案的硬依据, 宁可相关性略低也保留
#   补充类 (参考性, 需过相关性门槛) — 案例/行业标准仅作补充, 相关性不足则丢弃
_STATUTE_SOURCES = {"laws", "regulations", "interpretations"}
_SUPPLEMENTARY_SOURCES = {"cases", "industry_sources"}

# 补充类进入答案所需的 precision_score 下限 (真实旋钮;
#   原 0.3 权威阈值低于分数下界 0.5, 永远 inert, 故改为相关性门槛)
_PRECISION_FLOOR = 0.4

# 关键词 AND 匹配的最小命中数: 默认 2 个关键词命中才算相关
#   (单关键词查询时由 _keyword_and_match 动态降为 1, 避免整层空转)
_MIN_KEYWORD_HITS = 2


def _extract_keywords(text: str) -> List[str]:
    """从查询文本中提取关键词 (按标点分割, 过滤长度<2 的短词).

    参数:
        text (str): 用户查询文本
    返回:
        List[str]: 关键词列表 (去除标点/空白, 每个词长度 >= 2)
    示例:
        "违约金怎么计算" → ["违约金", "计算"]
        "劳动合同解除赔偿" → ["劳动合同", "解除", "赔偿"]
    """
    if not text:
        return []
    # 按中英文标点 + 空白切分
    keywords = re.split(r'''[，。！？、；：""''（）\s]+''', text)
    # 过滤长度 < 2 的短词 (如 "的" "了" 等无意义词)
    return [k for k in keywords if len(k) >= 2]


def _keyword_hit_count(content: str, keywords: List[str]) -> int:
    """返回关键词命中加权计数 (长词≥4字权重×2); 0 表示无命中.

    Stage 2 方案A: 该计数不再用于"硬淘汰", 仅作为 precision 轻量 boost 信号,
    避免 LLM 抽出的抽象意图词(商业风险/履约能力/交付标准...)因不在法条正文逐字
    出现, 而把 Stage 1 已语义验证过的召回全量否决。
    """
    if not keywords or not content:
        return 0
    hits = 0
    for kw in keywords:
        weight = 2 if len(kw) >= 4 else 1
        if kw in content:
            hits += weight
    return hits


def _keyword_and_match(content: str, keywords: List[str], min_hits: int = 2) -> bool:
    """多关键词 AND 匹配: 至少 min_hits 个关键词命中内容.

    长关键词 (≥4字) 权重加倍, 因为长关键词更具体, 命中价值更高.

    参数:
        content (str): citation 的 title + content 拼接文本
        keywords (List[str]): 查询关键词列表
        min_hits (int): 最小命中数 (默认 2)
    返回:
        bool: 是否通过 AND 匹配
    逻辑:
        遍历每个关键词, 命中则累加权重 (长词 × 2, 短词 × 1).
        累计权重 >= effective_min 时通过.
    """
    if not keywords:
        return True  # 无关键词时直接通过 (不做关键词过滤)

    hit_count = _keyword_hit_count(content, keywords)

    # 动态最小命中数: 单关键词查询时退化为至少命中 1, 避免整层空转
    # (原固定 min_hits=2 在单关键词查询下恒为 False → Stage 2 整体跳过 → 回退 Stage 1)
    effective_min = min(min_hits, max(1, len(keywords)))
    return hit_count >= effective_min


def _keyword_boost(r: Dict) -> float:
    """关键词命中轻量 boost: 命中越多加成越大, 封顶 0.1.

    语义回填的法条即便不含用户字面词也不应被否决; 关键词仅作为排序信号之一,
    与意图偏好关系加成(+0.1)同量级。以 _MIN_KEYWORD_HITS 为满载基准。
    """
    hits = r.get("keyword_hits", 0)
    if hits <= 0:
        return 0.0
    return min(0.1, 0.1 * min(hits, _MIN_KEYWORD_HITS) / _MIN_KEYWORD_HITS)


def _faiss_rerank(query: str, results: List[Dict], top_k: int = 20) -> List[Dict]:
    """FAISS 语义重排序: Embedding 编码 + 余弦相似度, 取 Top-K.

    【为什么需要重排序】
    召回层 (Stage 1) 可能有上千条结果, 其中很多是"关键词匹配但语义不相关"的.
    FAISS 重排序用 bge-m3 模型编码 query 和每条 citation 的 content,
    计算余弦相似度后按分数降序排列, 取 Top-K 最相关的.

    参数:
        query (str): 用户查询文本
        results (List[Dict]): 待重排序的 citation 列表
        top_k (int): 保留前 K 条 (默认 20)
    返回:
        List[Dict]: 重排序后的 citation 列表 (每条带 rerank_score 字段)
    """
    if not results or not query:
        return results  # 空输入或空查询直接返回

    try:
        # 延迟导入 (FAISS/embedding 较重, 避免启动时加载)
        from common.embedding_model import embedding_model
        import numpy as np

        # 编码 query 为向量
        query_vector = np.array(embedding_model.encode([query])).astype('float32')

        # 提取每条 citation 的 content (取 title + content 前 512 字符, 避免超长)
        contents = []
        for r in results:
            content = r.get("content", "") or r.get("title", "")
            contents.append(content[:512])

        # 批量编码所有 citation content
        result_vectors = np.array(embedding_model.encode(contents)).astype('float32')

        # 计算余弦相似度 (已归一化, 内积 = 余弦相似度)
        similarities = np.dot(result_vectors, query_vector.T).flatten()

        # 把相似度写入每条 citation (作为 rerank_score)
        for i, r in enumerate(results):
            r["rerank_score"] = float(similarities[i])

        # 按 rerank_score 降序排列, 取 Top-K
        results.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
        return results[:top_k]

    except Exception as e:
        # FAISS 重排序失败不阻塞流程, 退回原排序
        print(f"  ⚠️ FAISS重排序失败, 使用原排序: {e}")
        return results[:top_k]


def _attach_authority_score(r: Dict) -> Dict:
    """回填权威分 (0-1): 基于 5 知识源权重 + law_level/case_level.

    仅做"特征回填", 不做阈值过滤 —— 原 _authority_filter 的 0.3 阈值低于分数下界 0.5,
    是永远通过的死代码。权威性在此仅作为 precision_score / final_score 的 0.20 特征位,
    是否"进答案"由下游 _precision_gate (相关性闸门) 决定, 而非声望闸门。

    参数:
        r (Dict): 单条 citation (已被 enrich_citations 回填 law_level/case_level)
    返回:
        Dict: 同一对象 (就地写入 authority_score 字段)
    """
    # ---- 提取 source_id ----
    source_id = r.get("source_id", "") or _extract_source_id(r.get("source", ""))
    # ---- 5 知识源基础权重 ----
    base_score = _SOURCE_AUTHORITY.get(source_id, 0.5)
    # ---- law_level 加成: 已删除 ----
    #   law_level 是 source_id 的 1:1 派生 (laws→law, regulations→administrative_regulation...),
    #   冗余且与 source 基础权重同源; 在 authority_score 里既无区分度又被 min(1.0) 抹平,
    #   仅 case_level 真正携带「案例层级」区分信息。故 authority_score = source_base + case_level_bonus。
    # ---- case_level 加成 (唯一真实区分度来源) ----
    case_level = r.get("case_level", "")
    case_bonus = 0.0
    if case_level and case_level in CASE_LEVEL_RANK:
        case_bonus = CASE_LEVEL_RANK[case_level] / 4.0 * 0.2
    # ---- 最终权威分 ----
    r["authority_score"] = min(1.0, base_score + case_bonus)
    return r


def _precision_gate(results: List[Dict], precision_floor: float = 0.4) -> List[Dict]:
    """来源分级 + 相关性闸门 (替代原无效权威过滤).

    【设计】过滤应作用于"综合相关性", 权威只作其中一个特征, 不应把"来源声望"
    当成"查询相关性"硬删。
      - 法条类 (laws/regulations/interpretations): 强制引用, 永不硬删
        (哪怕 precision_score 略低, 法律文本本身就是答案的硬依据)
      - 补充类 (cases/industry_sources): 必须 precision_score >= precision_floor 才进答案
        (案例/行业标准是参考性材料, 相关性不足会伤准确率, 直接丢弃)
    图谱豁免被"来源分级"吸收: 图谱的法条类本就不过滤, 图谱的 cases 仍走门槛。

    参数:
        results (List[Dict]): 已计算 precision_score 的 citation 列表
        precision_floor (float): 补充类进答案的 precision_score 下限 (默认 0.4)
    返回:
        List[Dict]: 通过闸门的 citation 列表
    """
    kept = []
    for r in results:
        src = r.get("source_id", "") or _extract_source_id(r.get("source", ""))
        if src in _STATUTE_SOURCES:
            kept.append(r)                       # 法条类: 强制引用, 永不硬删
        elif r.get("precision_score", 0.0) >= precision_floor:
            kept.append(r)                       # 补充类: 过相关性门槛才进答案
        # 其余 (补充类且相关性不足): 丢弃
    return kept


# 意图 → 偏好关系 (与 entity_recall_node._INTENT_PREFERRED_REL 同源, 钉死在
#   __002__extract_information/__001__extract_legal_data.py 第 476 行 Article 锚定 6 关系上)。
# Stage2 仅给「偏好关系行」一点 precision 加成 (+0.1), 帮助其通过补充类闸门 / 提升排序位次。
_INTENT_PREFERRED_REL = {
    "definition": {"DEFINES"},
    "liability":  {"HAS_LIABILITY", "HAS_PENALTY"},
    "penalty":    {"HAS_PENALTY"},
    "regulates":  {"REGULATES"},
    "condition":  {"HAS_CONDITION"},
    "parties":    {"INVOLVES"},
}


def _intent_relation_bonus(r: Dict, intents) -> float:
    """意图偏好关系加成 (0.1 / 0.0): 命中偏好关系则 +0.1, 否则 0。

    参数:
        r (Dict): 单条 citation
        intents (str | List[str]): 检索意图。支持单值(原 7 类闭集)或多分面意图列表
            (retrieval_intent_decompose_node 产出的 retrieval_intents)。多意图时取偏好关系
            并集 —— 任一召回的关系命中任一意图偏好即加成, 与 Stage1 _dedup_with_intent_bias 对齐。
    """
    if isinstance(intents, str):
        intents = [intents]
    pref = set()
    for it in (intents or ["general"]):
        pref |= _INTENT_PREFERRED_REL.get((it or "general").lower(), set())
    if not pref:
        return 0.0
    return 0.1 if (r.get("relation_type", "") or "") in pref else 0.0


def _compute_precision_score(r: Dict) -> float:
    """计算精准分数 (0-1): recall×0.55 + rerank×0.25 + authority×0.20。

    参数:
        r (Dict): 单条 citation (已被 _attach_authority_score 写入 authority_score)
    返回:
        float: 精准分数, 范围 [0.0, 1.0]
    数据流:
        1. recall_score × 0.55 (召回分, 来自 Stage 1; 原 law_level 0.20 因 source 派生冗余已删除, 权重并入此处)
        2. rerank_score × 0.25 (重排分, 来自 _faiss_rerank)
        3. authority_score × 0.20 (权威分, 来自 _attach_authority_score, 仅 source_base + case_level)
    """
    score = 0.0
    # recall_score 权重 0.55 (最大, 召回是基础; 含原 law_level 的 0.20 冗余权重)
    score += r.get("recall_score", 0.5) * 0.55
    # rerank_score 权重 0.25 (语义精排)
    score += r.get("rerank_score", 0.5) * 0.25
    # authority_score 权重 0.20 (权威性, 仅 source_base + case_level, 不含冗余 law_level)
    score += r.get("authority_score", 0.5) * 0.2

    return min(1.0, score)


def retrieval_precision_filter_node(state: AgentState) -> Dict:
    """Stage 2 精准过滤层主入口.

    【在 LangGraph 中的位置】
    接收 retrieval_entity_recall_node (Stage 1) 的召回结果,
    进行三级过滤 (关键词 AND → FAISS 重排 → 权威过滤),
    输出精准排序后的 citation 列表给融合排序层 (Stage 3).

    【数据流转】
    state["entity_recall_results"] (Stage 1 产出)
        → enrich_citations (回填 law_level/case_level)
        → _keyword_hit_count (关键词信号, 不淘汰, 仅作 boost)
        → _faiss_rerank (语义重排序, 全量召回 Top 30)
        → _attach_authority_score (回填权威分) + _precision_gate (来源分级闸门)
        → _compute_precision_score (精准分计算)
        → 排序 + 截断 Top 20

    【返回】
    Dict: {
        "precision_results": 精准排序后的 citation 列表 (最多 20 条, 每条带 precision_score),
        "filter_stats": 各级过滤统计 (输入/关键词通过/重排/权威通过/最终)
    }
    """
    print("检索 [Stage 2] 精准过滤层")

    # ---- 1. 获取输入数据 ----
    recall_results = state.get("entity_recall_results", []) or []
    query = state.get("retrieval_query", "") or state.get("input", "") or ""
    # 关键词: 优先用上游解析好的, 否则从 query 中提取
    keywords = state.get("retrieval_keywords", []) or _extract_keywords(query)
    # 检索意图 (多分面列表优先; 单值兼容字段兜底) — 用于偏好关系 precision 加成 (+0.1)
    #   retrieval_intents: 多分面意图列表(优先); retrieval_intent: 单值兼容字段。
    #   _intent_relation_bonus 内部取偏好关系并集, 与 Stage1 _dedup_with_intent_bias 对齐。
    intents = state.get("retrieval_intents") or []
    if not intents:
        intents = state.get("retrieval_intent", "") or "general"

    # 空输入: 直接返回空结果
    if not recall_results:
        print("  无召回结果, 跳过精准过滤")
        return {
            "precision_results": [],
            "filter_stats": {"input": 0, "keyword_pass": 0, "rerank": 0, "authority_pass": 0, "final": 0},
        }

    # ---- 2. 回填 citation 元数据 ----
    # 用 citation_meta.enrich_citations 给每条 citation 回填 law_level/case_level
    # law_level 从 source_id 直接映射 (而非标题启发式)
    recall_results = enrich_citations(recall_results)

    input_count = len(recall_results)
    print(f"  输入召回结果: {input_count} 条 (已回填 citation_meta)")

    # ---- 3. 2a. 关键词信号 (方案A: 不再做硬淘汰) ----
    # 原设计: 关键词 AND 匹配 ≥2 命中才保留 → 但 LLM 抽的抽象意图词(商业风险/履约能力/交付标准...)
    #   并不在法条正文逐字出现, 导致 Stage 1 已语义验证的召回被字面门槛全量否决 (163→0)。
    # 修复: 取消硬淘汰, 全部召回进 2b 语义重排; 关键词命中数仅作为 precision 轻量 boost 信号,
    #   排序仍由 FAISS 语义相似度主导。法条类永不删、补充类过 0.4 门槛不变。
    effective_min = min(_MIN_KEYWORD_HITS, max(1, len(keywords)))
    keyword_passed = 0
    for r in recall_results:
        # 拼接 title + content 作为匹配文本
        content = f"{r.get('title', '')} {r.get('content', '')}"
        hits = _keyword_hit_count(content, keywords)
        r["keyword_hits"] = hits
        r["keyword_pass"] = hits >= effective_min
        if r["keyword_pass"]:
            keyword_passed += 1

    print(f"  [2a] 关键词信号(不淘汰): {keyword_passed}/{input_count} 命中, 全量进重排")

    # ---- 4. 2b. FAISS 语义重排序 (全量召回, 不再受关键词门槛限制) ----
    # 对全部召回(含关键词未命中的)做 FAISS 语义精排, 取 Top 30 —— 排序由语义相似度主导
    reranked = _faiss_rerank(query, recall_results, top_k=30)
    print(f"  [2b] FAISS重排序: {len(reranked)} 条")

    # ---- 5. 2c. 回填权威分 (仅特征, 不做阈值过滤) ----
    # 原 _authority_filter 的 0.3 阈值低于分数下界 0.5, 是永远通过的死代码, 已删除。
    # 此处只把 authority_score 写回, 供下一步 precision_score 与 Stage 3 final_score 使用。
    for r in reranked:
        _attach_authority_score(r)

    # ---- 6. 计算精准分数 ----
    for r in reranked:
        r["precision_score"] = _compute_precision_score(r)

    # ---- 6.5 P3: 意图偏好关系 precision 加成 (+0.1) + 关键词轻量 boost (+≤0.1) ----
    #   命中意图偏好关系的行在 precision_score 上加 0.1 (封顶 1.0),
    #   帮助其通过补充类闸门 / 在排序中位次前移 (与 Stage1 的 recall×1.3 偏置呼应)。
    #   关键词命中数转为轻量 boost (封顶 0.1): 语义召回的法条即便不含用户字面词也不否决,
    #   关键词仅作排序信号之一 (方案A 修复 Stage2 全空转)。
    for r in reranked:
        boost = _intent_relation_bonus(r, intents) + _keyword_boost(r)
        r["precision_score"] = min(1.0, r["precision_score"] + boost)

    # ---- 7. 来源分级 + 相关性闸门 ----
    # 法条类永不硬删; 补充类需 precision_score >= 0.4 才进答案
    gated = _precision_gate(reranked, _PRECISION_FLOOR)
    print(f"  [2c] 来源分级闸门: {len(gated)}/{len(reranked)} 通过")

    # ---- 8. 按 precision_score 降序排列, 取 Top 20 ----
    gated.sort(key=lambda x: x.get("precision_score", 0), reverse=True)
    final_results = gated[:20]

    # ---- 7. 组装过滤统计 ----
    filter_stats = {
        "input": input_count,
        "keyword_pass": keyword_passed,
        "rerank": len(reranked),
        "authority_pass": len(gated),
        "final": len(final_results),
    }

    # 打印收缩率 (从输入到最终的压缩比)
    print(f"  精准过滤汇总: {input_count} → {len(final_results)} 条 (收缩率 {100*len(final_results)/max(1,input_count):.1f}%)")

    # ---- 8. 返回结果 ----
    return {
        "precision_results": final_results,
        "filter_stats": filter_stats,
    }
