"""
Stage 1: 实体召回层 (Entity Recall)
=======================================

本文件实现的是法律智能检索系统中的 Stage 1：实体召回层（Entity Recall），其核心目标是通过两通道并行召回策略（图谱实体匹配 + FAISS 语义召回），最大化检索覆盖面。
整体工作流程
1.入口函数 retrieval_entity_recall_node 接收 Agent 状态中的查询词和候选关键词列表。
2.对每个知识源（如 laws/regulations/cases 等），并行执行三条召回通道：
◦通道 1a（图谱实体召回）：通过 Neo4j 图数据库，使用 4 种 UNION ALL 模式的 Cypher 查询，从法律条文图谱中匹配实体、条款号、法律名和全文内容。
◦通道 1b（FAISS 语义召回）：使用 bge-m3 模型将查询编码为向量，在本地 FAISS 向量索引中进行近似最近邻搜索，返回语义最相似的法律条文。
◦通道 1c（关键词扩展召回）：已于 2026-08-29 删除（其 marker 意图识别是 hack，兜底能力已被 1a 的 FULLTEXT + 1b 的 FAISS 覆盖）。
3.三条通道的结果合并后，基于 title + content 前50字符 进行去重。
4.最终输出 entity_recall_results（召回结果列表）和 recall_stats（各通道命中统计）。
设计哲学
•不追求精确，追求覆盖：召回层的首要任务是”把所有可能相关的条文都捞出来”，精度问题交给后续的排序层（Stage 2）处理。
•三通道互补：图谱匹配擅长精确实体和条款号匹配，FAISS 擅长语义理解，关键词扩展擅长兜底覆盖。三者结合可最大化召回率。
•分数体系：每条召回结果都带有 recall_score（0-1 范围），后续融合层可根据分数加权合并。

【输出】
  entity_recall_results: 召回结果列表，每项包含 title/article_no/content/source/recall_score 等
  recall_stats: 各通道命中统计 {graph, faiss}
"""
# TODO 2.1: 模块头部与全局常量
import os
import re
import pickle
from typing import List, Dict
# - AgentState: 从 LangGraph 状态管理器获取当前查询上下文
# - root_dir: 获取项目根目录路径，用于定位数据文件
# - normalize_candidates: 将原始查询标准化为候选查询词列表
# - neo4j_client: Neo4j 数据库客户端，用于执行 Cypher 查询
from __004__langgraph_more_nodes.agent_state import AgentState
from common.path_utils import root_dir
from common.alias_normalizer import normalize_candidates
from common.neo4j_manager import neo4j_client


# TODO 2.2:知识源 → FAISS索引映射
# 知识源 → FAISS 索引映射
# 定义 5 类知识源及其对应的 FAISS 索引文件和 id2text 映射文件
# 每类知识源（如 laws、regulations）都有独立的向量索引，支持按源隔离检索
_SOURCE_INDEX_MAP = {
    "laws":            ("laws_faiss.index",                    "laws_id2text.pkl"),
    "regulations":     ("regulations_faiss.index",             "regulations_id2text.pkl"),
    "cases":           ("cases_faiss.index",                   "cases_id2text.pkl"),
    "industry_sources":("industry_sources_faiss.index",      "industry_sources_id2text.pkl"),
    "interpretations": ("interpretations_faiss.index",         "interpretations_id2text.pkl"),
}
# 键名（如 "laws"）对应知识源标识，
# 值元组中第一个是 FAISS 二进制索引文件，第二个是 ID→文本映射的 pickle 文件。
# 这样设计的好处是：不同知识源可独立维护向量索引，检索时可精确限定范围。

# TODO 2.3: 关键词拆分函数
# 意图 → 偏好关系 (钉死在 __002__extract_information/__001__extract_legal_data.py
#   第 476 行的 Article 锚定 6 关系上)。用户问"定义"→ 偏置 DEFINES; 问"责任"→
#   偏置 HAS_LIABILITY(+HAS_PENALTY); 以此类推。general(无明确意图)→ 空集, 不偏置。
_INTENT_PREFERRED_REL = {
    "definition": {"DEFINES"},
    "liability":  {"HAS_LIABILITY", "HAS_PENALTY"},
    "penalty":    {"HAS_PENALTY"},
    "regulates":  {"REGULATES"},
    "condition":  {"HAS_CONDITION"},
    "parties":    {"INVOLVES"},
}
# 偏好关系召回分偏置系数 (P3: recall × 1.3)
_INTENT_REL_BOOST = 1


def _dedup_with_intent_bias(recalls: List[Dict], intents) -> List[Dict]:
    """意图驱动的智能去重 + 召回偏置 (P3)。

    【解决的真问题】同一条文可能同时挂多个关系 (DEFINES=100 / HAS_LIABILITY=90 / ...),
    朴素按 title+content 去重会「先到先得」保留首行 → 隐式 DEFINES 恒胜,
    与用户真实意图无关 (问"责任"却拿到"定义"行)。

    【做法】
      1. 按 (title + content[:100]) 分组, 同组优先保留「意图偏好关系」所在行
         (意图无偏好关系时保留 recall_score 最高者);
      2. 对最终保留的「偏好关系行」, recall_score × _INTENT_REL_BOOST (封顶 1.0)。

    参数:
        recalls (List[Dict]): 两通道合并后的召回结果 (含 relation_type 字段)
        intents (str | List[str]): 检索意图。支持单值(原 7 类闭集)或多分面意图列表
            (retrieval_intent_decompose_node 产出的 retrieval_intents)。多意图时取偏好关系
            并集 —— 任一召回的关系命中任一意图偏好即被偏置/优先, 修复「多子查询只偏置一种意图」。
    返回:
        List[Dict]: 去重 + 偏置后的召回结果
    """
    # 归一为意图列表 (单值 str → [str]); 计算偏好关系并集
    if isinstance(intents, str):
        intents = [intents]
    pref = set()
    for it in (intents or ["general"]):
        pref |= _INTENT_PREFERRED_REL.get((it or "general").lower(), set())
    best: Dict[str, Dict] = {}
    is_pref_flag: Dict[str, bool] = {}

    for r in recalls:
        key = f"{r.get('title', '')}|{str(r.get('content', ''))[:100]}"
        rel = r.get("relation_type", "") or ""
        is_pref = rel in pref
        if key not in best:
            best[key] = r
            is_pref_flag[key] = is_pref
            continue
        cur = best[key]
        cur_pref = is_pref_flag[key]
        # 规则: 偏好关系行优先; 同档(都偏好/都不偏好)时留 recall_score 高者
        if is_pref and not cur_pref:
            best[key] = r
            is_pref_flag[key] = True
        elif is_pref == cur_pref and (r.get("recall_score", 0.0) > cur.get("recall_score", 0.0)):
            best[key] = r
            is_pref_flag[key] = is_pref

    out = []
    for key, r in best.items():
        if is_pref_flag.get(key) and pref:
            r["recall_score"] = min(1.0, float(r.get("recall_score", 0.0)) * _INTENT_REL_BOOST)
        out.append(r)
    return out


def _split_keywords(query: str) -> list:
    """将查询文本按标点/空白拆分为关键词列表，过滤长度<=1的token"""
    # 此函数的作用是将自然语言查询拆分为可用于检索的关键词列表。
    # 细节说明：
    # 1. 先将中文标点（，。、）替换为空格，统一分隔符
    # 2. 再用正则表达式按空白/标点序列分割
    # 3. 过滤掉长度<=1的token（如单个标点或空格），避免无意义检索
    # 4. 如果拆分后结果为空（如查询全是单字），则取前4个字符作为唯一关键词兜底
    text = query.replace("，", " ").replace("。", " ").replace("、", " ")
    tokens = re.split(r'[\s,.;:;]+', text)
    keywords = [t.strip() for t in tokens if len(t.strip()) > 1]
    if not keywords and query:
        keywords = [query[:min(4, len(query))]]
    return keywords

# TODO 2.4: FAISS 索引路径获取函数
def _get_faiss_index_path(source_tag: str):
    """获取指定知识源的 FAISS 索引和 id2text 映射文件路径"""
    # 根据知识源标签（如 "laws"），从 _SOURCE_INDEX_MAP 中查找对应的
    # 索引文件和映射文件路径。路径拼接使用 root_dir + data/knowledge_base/index/。
    # 若知识源不存在于映射表中，返回 None。
    mapping = _SOURCE_INDEX_MAP.get(source_tag)
    if not mapping:
        return None
    index_name, id2text_name = mapping
    index_path = os.path.join(root_dir, "data", "knowledge_base", "index", index_name)
    id2text_path = os.path.join(root_dir, "data", "knowledge_base", "index", id2text_name)
    return index_path, id2text_path

# TODO 2.5: 通道 1a：图谱实体召回（核心 Cypher 查询）
def _graph_entity_recall(query: str, source_tag: str, top_k: int = 10,
                         ordered_keywords: list = None) -> List[Dict]:
    """通道 1a: 图谱实体召回 — Neo4j 直接 Cypher 查询

    4 种 UNION ALL 匹配模式 (按优先级排序):
      1. ENTITY_MATCH: 实体匹配 (概念/角色/责任/处罚) — 分数 100/90/60
      2. ARTICLE_EXACT: 条款号精确匹配 — 分数 95
      3. LAW_NAME: 法律名匹配 — 分数 70
      4. FULLTEXT: 全文兜底 — 分数 40

    参数 ordered_keywords: 上游 LLM 产出的、按重要性从高到低排列的关键词列表。
        若提供，则用 ordered_keywords[0] 作为主关键词 (main_kw) 赋予 100 分，
        其余关键词 90 分 —— 这样 main_kw 的选择是基于 LLM 判定的重要性，
        而非 _split_keywords 的文本切分顺序。
        若未提供或为空，则回退到 _split_keywords(query) 自行切分。
    """
    # 入口参数说明：
    # - query: 用户原始查询文本
    # - source_tag: 知识源标签（如 "laws"），用于过滤检索范围
    # - top_k: 返回结果数量上限，默认 10
    if not query:
        return []

    # 优先使用上游 LLM 产出的有序关键词；若未提供则回退到 _split_keywords
    if ordered_keywords:
        keywords = [str(k).strip() for k in ordered_keywords if str(k).strip()]
        if not keywords:
            keywords = _split_keywords(query)
    else:
        keywords = _split_keywords(query)
    if not keywords:
        return []

    # 构建源过滤条件。如果指定了 source_tag，
    # 则在 Cypher WHERE 子句末尾追加 "AND a.source_id = $source_tag"，
    # 确保只检索指定知识源下的条文节点。
    source_filter = ""
    source_params = {}
    if source_tag:
        source_filter = " AND a.source_id = $source_tag"
        source_params["source_tag"] = source_tag

    # ============================================================
    # 核心 Cypher 查询 — 4 种匹配模式通过 UNION ALL 合并
    # ============================================================
    #
    # UNION ALL 的设计意图：
    #   将 4 种不同粒度的匹配策略合并为一次数据库查询，
    #   每种模式返回不同的 match_type 标签和基础分数（base_score），
    #   最终按 base_score 降序排列并取 top_k。
    #
    # 模式优先级设计（分数越高越优先）：
    #   ENTITY_MATCH(100/90/60) > ARTICLE_EXACT(95) > LAW_NAME(70) > FULLTEXT(40)
    #
    # 为什么实体匹配分数最高？因为图谱中的实体节点（如"违约责任"、"担保"）
    #   是经过人工标注的法律概念，与查询词的语义关联最强。
    # 为什么全文兜底分数最低？因为全文匹配可能产生大量误匹配，
    #   但作为兜底策略确保不会遗漏任何可能相关的条文。
    #
    # 详细 Cypher 逻辑分解：
    #
    # --- 模式1: ENTITY_MATCH (实体匹配) ---
    # 匹配逻辑：查找与查询关键词相关的法律条文
    # 图谱路径: (实体节点)<-[关系]-(条文节点)
    # 关系类型: DEFINES（定义）、HAS_LIABILITY（责任）、HAS_PENALTY（处罚）、
    #   REGULATES（规范）、HAS_CONDITION（条件）、INVOLVES（主体）
    #   —— 与 extract_legal_data.py 第 476 行 Article 锚定 6 关系一致
    # 匹配条件: 实体节点的 name 属性包含任意查询关键词
    # 分数规则:
    #   - 实体名完全等于主关键词 -> 100分（最强匹配）
    #   - 实体名完全等于某个关键词 -> 90分（精确关键词匹配）
    #   - 实体名仅包含关键词（部分匹配）-> 60分（弱匹配）
    # 示例: 查询"违约金"，图谱中有实体节点 {name: "违约金"}，
    #   则与该实体关联的所有条文（如《民法典》第577条）会被召回，base_score=100
    #
    # --- 模式2: ARTICLE_EXACT (条款号精确匹配) ---
    # 匹配逻辑：通过条文节点的 name 属性（如"第577条"）精确匹配
    # 图谱路径: (条文节点)-[:BELONGS_TO]-(法律节点)
    # 匹配条件: 条文 name 包含任意查询关键词
    # 分数: 固定 95 分（条款号匹配非常精确，仅次于实体精确匹配）
    # 示例: 查询"第577条"，直接匹配 name 中包含"577"的条文节点
    #
    # --- 模式3: LAW_NAME (法律名匹配) ---
    # 匹配逻辑：通过法律名匹配定位条文
    # 图谱路径: (条文节点)-[:BELONGS_TO]-(法律节点)
    # 匹配条件: 法律名包含任意查询关键词
    # 分数: 固定 70 分（法律名匹配精度中等，因为目前数据处理不准确,且同一部法律有多版历史版本共存,或者效力层级不同,有不同政府制定）
    # 示例: 查询"民法典"，会召回所有归属于名为"民法典"的法律下的条文
    #
    # --- 模式4: FULLTEXT (全文兜底) ---
    # 匹配逻辑：在条文正文内容中搜索关键词
    # 图谱路径: (条文节点)-[:BELONGS_TO]-(法律节点)
    # 匹配条件: 条文 content 属性包含任意查询关键词
    # 分数: 固定 40 分（精度最低但覆盖率最高）
    # 示例: 查询"解除合同"，即使没有实体节点直接标注，
    #   只要条文正文中出现"解除合同"字样就会被召回
    #
    # OPTIONAL MATCH 的作用：
    #   在模式1中，条文可能不总是关联到法律节点，
    #   使用 OPTIONAL MATCH 确保即使没有 BELONGS_TO 关系也不会导致整条记录丢失。
    #
    # DISTINCT 的作用：
    #   防止 UNION ALL 合并后出现重复的条文记录。
    #   因为同一条文可能同时满足多种匹配模式。
    #
    # ORDER BY base_score DESC LIMIT $top_k：
    #   按匹配质量降序排列，只取前 top_k 条结果，
    #   确保高优先级的匹配结果排在最前面。

    recall_cypher = """
    // 模式1: 实体匹配 (概念/角色/责任/处罚)
    MATCH (e)<-[r:DEFINES|HAS_LIABILITY|HAS_PENALTY|REGULATES|HAS_CONDITION|INVOLVES]-(a:Article)
    WHERE any(kw IN $kws WHERE e.name CONTAINS kw)
    """ + source_filter + """
    OPTIONAL MATCH (a)-[:BELONGS_TO]->(law)
    RETURN DISTINCT
        a.name AS article_no,
        a.content AS content,
        a.source_id AS source_id,
        e.name AS concept,
        law.name AS law_name,
        'ENTITY_MATCH' AS match_type,
        type(r) AS rel_type,
        // base_score: 先按实体名匹配强度打分 (100/90/60), 再乘关系权重
        //   DEFINES×1.0 / HAS_LIABILITY×0.9 / HAS_PENALTY×0.9 / REGULATES×0.8 / HAS_CONDITION×0.7 / INVOLVES×0.7
        //   → 关系权重让"定义"略高于"责任/处罚", 但意图偏置(P3)会在此基础上再 ×1.3 反超
        (
            CASE
                WHEN e.name = $main_kw THEN 100
                WHEN any(kw IN $kws WHERE kw = e.name) THEN 90
                ELSE 60
            END
            *         CASE type(r)
            WHEN 'DEFINES' THEN 1.0
            WHEN 'HAS_LIABILITY' THEN 0.9
            WHEN 'HAS_PENALTY' THEN 0.9
            WHEN 'REGULATES' THEN 0.8
            WHEN 'HAS_CONDITION' THEN 0.7
            WHEN 'INVOLVES' THEN 0.7
            ELSE 0.7
          END
        ) AS base_score

    UNION ALL

    // 模式2: 条款号精确匹配
    MATCH (a:Article)-[:BELONGS_TO]->(law)
    WHERE any(kw IN $kws WHERE a.name CONTAINS kw)
    """ + source_filter + """
    RETURN DISTINCT
        a.name AS article_no,
        a.content AS content,
        a.source_id AS source_id,
        law.name AS concept,
        law.name AS law_name,
        'ARTICLE_EXACT' AS match_type,
        null AS rel_type,
        95 AS base_score

    UNION ALL

    // 模式3: 法律名匹配
    MATCH (a:Article)-[:BELONGS_TO]->(law)
    WHERE any(kw IN $kws WHERE law.name CONTAINS kw)
    """ + source_filter + """
    RETURN DISTINCT
        a.name AS article_no,
        a.content AS content,
        a.source_id AS source_id,
        law.name AS concept,
        law.name AS law_name,
        'LAW_NAME' AS match_type,
        null AS rel_type,
        70 AS base_score

    UNION ALL

    // 模式4: 全文兜底
    MATCH (a:Article)-[:BELONGS_TO]->(law)
    WHERE any(kw IN $kws WHERE a.content CONTAINS kw)
    """ + source_filter + """
    RETURN DISTINCT
        a.name AS article_no,
        a.content AS content,
        a.source_id AS source_id,
        law.name AS concept,
        law.name AS law_name,
        'FULLTEXT' AS match_type,
        null AS rel_type,
        40 AS base_score

    ORDER BY base_score DESC
    LIMIT $top_k
    """

    # 构建 Cypher 参数：
    # - kws: 所有关键词列表，供 any() 函数遍历
    # - main_kw: 第一个关键词（上游 LLM 按重要性排序后的首位），作为主关键词用于实体精确匹配打分
    #   → main_kw 匹配 = 100 分, 其他关键词匹配 = 90 分
    #   → 顺序语义: LLM prompt 已要求"按与查询的相关性从高到低排列"，所以 keywords[0] 是最重要的
    # - top_k: 返回结果数量上限
    # - source_tag: 如果指定了知识源，则传入用于过滤
    params = {
        "kws": keywords,
        "main_kw": keywords[0],
        "top_k": top_k,
    }
    params.update(source_params)

    # 执行 Cypher 查询，使用 try-except 捕获数据库异常，
    # 避免因单个知识源故障导致整个召回流程崩溃。
    try:
        results = neo4j_client.run_cypher(recall_cypher, params)
    except Exception as e:
        print(f"  [WARNING] 图谱召回[{source_tag}]失败: {e}")
        return []

    if not results:
        return []

    # 结果转换：将 Neo4j 返回的原始结果字典映射为统一格式。
    # 每条结果包含以下字段：
    # - title: 匹配到的实体名或法律名（供展示用）
    # - article_no: 条款号（如"第577条"）
    # - content: 条文正文内容
    # - source: 召回通道标识，格式为 "graph::laws"（供 citation_meta 解析）
    # - source_id: 直接的知识源id（供 citation_meta 直接映射 law_level）
    # - recall_score: 召回分数，归一化到 0-1（base_score / 100）
    # - recall_source: "graph"（供融合层判断来源通道）
    # - entity_name: 命中的实体名
    # - entity_type: 实体类型（图谱召回暂不填充）
    # - graph_path: 图路径（实体匹配时回填关系类型 [DEFINES/HAS_LIABILITY/HAS_PENALTY]，供 Stage 3 路径加成/跳转增益）
    recall_results = []
    for r in results:
        rel_type = r.get("rel_type") or ""
        recall_results.append({
            "title": r.get("concept", ""),
            "article_no": r.get("article_no", ""),
            "content": r.get("content", ""),
            "source": f"graph::{source_tag}",
            "source_id": source_tag,
            "recall_score": r.get("base_score", 40) / 100.0,
            "recall_source": "graph",
            "entity_name": r.get("concept", ""),
            "entity_type": "",
            # 关系类型 (DEFINES/HAS_LIABILITY/HAS_PENALTY) — 供 Stage 3 路径加成/跳转增益使用
            "relation_type": rel_type,
            # 图路径: 实体匹配带回关系类型, 让 Stage 3 的 path_bonus/jump_bonus 真正生效
            "graph_path": [rel_type] if rel_type else [],
        })

    return recall_results
# TODO 2.6: 通道 1b：FAISS 语义召回
def _faiss_semantic_recall(query: str, source_tag: str, top_k: int = 10) -> List[Dict]:
    """通道 1b: FAISS 语义召回 — bge-m3 编码 + 内积相似度"""
    # 此通道的作用：
    #   通过向量语义相似度搜索，召回与查询语义最接近的法律条文。
    #   与图谱召回不同，FAISS 不依赖关键词精确匹配，
    #   而是理解查询的语义意图，能召回表述不同但含义相近的条文。
    #
    # 工作流程：
    #   1. 根据 source_tag 加载对应的 FAISS 索引文件和 id2text 映射
    #   2. 使用 bge-m3 模型将查询文本编码为向量
    #   3. 在 FAISS 索引中进行近似最近邻搜索（内积相似度）
    #   4. 将搜索结果转换为统一格式返回
    #
    # 分数计算说明：
    #   FAISS 返回的是距离值（distance），距离越小表示越相似。
    #   转换为相似度分数: score = max(0, 1 - dist/100)
    #   再乘以 0.6 的衰减系数，确保 FAISS 召回的分数整体低于图谱召回。
    #   这是因为向量相似度是软匹配，精度不如图谱的精确匹配。
    if not query:
        return []

    try:
        paths = _get_faiss_index_path(source_tag)
        if not paths:
            return []

        index_path, id2text_path = paths
        # 检查索引文件是否存在，避免运行时 FileNotFoundError
        if not (os.path.exists(index_path) and os.path.exists(id2text_path)):
            return []

        # 动态导入 faiss 和 embedding_model，
        # 避免在模块加载时就依赖这些重型库，减少启动开销。
        import faiss
        import numpy as np
        from common.embedding_model import embedding_model

        # 读取 FAISS 二进制索引文件到内存
        index = faiss.read_index(index_path)
        # 读取 pickle 格式的 ID→文本映射表
        # id2text[idx] 返回该索引位置对应的法律条文文本内容
        with open(id2text_path, 'rb') as f:
            id2text = pickle.load(f)

        # 使用 bge-m3 模型将查询文本编码为向量
        # embedding_model.encode 返回的是一个 batch 的向量列表
        query_vector = embedding_model.encode([query])
        # 转换为 numpy float32 数组，FAISS 要求 float32 类型
        query_vector = np.array(query_vector).astype('float32')

        # 确定搜索数量 k，不超过索引中的总条目数
        k = min(top_k, index.ntotal)
        if k == 0:
            return []

        # 执行 FAISS 近似最近邻搜索
        # distances: 查询向量与各结果向量的距离（内积距离）
        # indices: 结果向量在 id2text 中的索引位置
        distances, indices = index.search(query_vector, k)

        # FAISS 索引存的是"实体名"(见 __003__faiss_embedding.build_faiss_index),
        # 因此 id2text[idx] 是实体名而非条文正文。原实现把 content 写成 "实体: {name}"、
        # article_no 留空, 导致三通道结构不一致 → Stage 2 rerank/去重/展示都拿到占位串。
        # 修复: 用 FAISS 找到的语义相似实体名做反向 Cypher 查询, 取回真实条文(article),
        #       使 FAISS 通道与其它通道结构一致 (带真实 content/article_no/relation_type)。
        # 1) 收集 top-k 实体名 → 各自最佳相似度
        entity_scores = {}
        for dist, idx in zip(distances[0], indices[0]):
            if idx < 0 or idx >= len(id2text):
                continue
            ent = id2text[int(idx)]
            score = float(max(0.0, 1.0 - dist / 100.0))   # 距离转相似度
            if ent not in entity_scores or score > entity_scores[ent]:
                entity_scores[ent] = score

        if not entity_scores:
            return []

        # 2) 反向查询: 这些实体名关联的真实条文 (沿 DEFINES/HAS_LIABILITY/HAS_PENALTY)
        src_filter = ""
        src_params = {}
        if source_tag:
            src_filter = " AND a.source_id = $source_tag"
            src_params["source_tag"] = source_tag
        article_cypher = """
        MATCH (e)<-[r:DEFINES|HAS_LIABILITY|HAS_PENALTY|REGULATES|HAS_CONDITION|INVOLVES]-(a:Article)
        WHERE e.name IN $entity_names
        """ + src_filter + """
        RETURN DISTINCT
            a.name AS article_no,
            a.content AS content,
            a.source_id AS source_id,
            e.name AS entity_name,
            type(r) AS rel_type,
            'FAISS_ENTITY' AS match_type
        """
        params = {"entity_names": list(entity_scores.keys())}
        params.update(src_params)
        try:
            article_results = neo4j_client.run_cypher(article_cypher, params)
        except Exception as e:
            print(f"  [WARNING] FAISS实体反查[{source_tag}]失败: {e}")
            article_results = []

        # 3) 组装召回结果: 用匹配实体的 FAISS 相似度作为 recall_score (乘 0.6 衰减)
        recall_results = []
        for r in article_results:
            ent = r.get("entity_name", "")
            ent_score = entity_scores.get(ent, 0.5)
            rel_type = r.get("rel_type") or ""
            recall_results.append({
                "title": ent,
                "article_no": r.get("article_no", ""),
                "content": r.get("content", ""),
                "source": f"faiss::{source_tag}",
                "source_id": source_tag,
                "recall_score": ent_score * 0.6,
                "recall_source": "faiss",
                "entity_name": ent,
                "entity_type": "",
                "relation_type": rel_type,
                "graph_path": [rel_type] if rel_type else [],
            })

        # 4) 按 recall_score 降序, 截 top_k (避免单实体多条文撑爆通道)
        recall_results.sort(key=lambda x: x.get("recall_score", 0), reverse=True)
        return recall_results[:top_k]

    except Exception as e:
        print(f"  [WARNING] FAISS召回[{source_tag}]失败: {e}")
        return []
# TODO 2.8: 主入口函数：三通道并行召回编排
def retrieval_entity_recall_node(state: AgentState) -> Dict:
    """Stage 1 实体召回层 — 两通道并行召回，最大化覆盖面

    对每个知识源 x 每个候选查询词，并行执行:
      1a. 图谱实体匹配 (4 种 Neo4j Cypher 模式, 关系已扩展到 6 类 Article 锚定关系)
      1b. FAISS 语义召回 (向量相似度搜索, 反向 Cypher 取真实条文)

    最后按「意图偏好关系优先 + recall×1.3 偏置」智能去重 (P3),
    替代原「先到先得」朴素去重 —— 修复同法条多关系时隐式 DEFINES 恒胜 bug。
    (原 1c 关键词扩展通道已于 2026-08-29 删除: 其 marker 意图识别是 hack,
     且同义词扩展的兜底能力已被 1a 的 FULLTEXT + 1b 的 FAISS 覆盖)
    """
    # 函数入口：从 AgentState 中提取检索所需的全部上下文信息。
    # AgentState 是 LangGraph 工作流中的全局状态对象，
    # 包含了上游节点处理后的所有中间结果。
    print("检索 [Stage 1] 实体召回层 (三通道并行召回)")

    # 获取查询文本：优先使用 retrieval_query，
    # 如果为空则回退到 input 字段。
    query = state.get("retrieval_query", "") or state.get("input", "") or ""
    # 获取上游节点提取的关键词列表（可能为空）
    keywords = state.get("retrieval_keywords", []) or []
    # 检索意图 + 锚点 (retrieval_intent_decompose_node 写入)
    #   retrieval_intents: 多分面意图列表(优先); retrieval_intent: 单值兼容字段。
    #   用于 Stage1 意图偏置(recall×1.3) + Stage2 意图关系加成(+0.1);
    #   缺失时退化为 ["general"] (不偏置)。
    intents = state.get("retrieval_intents") or []
    if not intents:
        intents = state.get("retrieval_intent", "") or "general"
    anchor = state.get("retrieval_anchor", "") or ""
    # 锚点兜底 main_kw: 关键词为空时用锚点实体, 否则回落文本切分
    if not keywords and anchor:
        keywords = [anchor]
    # 获取用户指定的知识源列表（如 ["laws", "cases"]）
    domain_sources = state.get("domain_sources", []) or []

    # 获取候选查询词列表。如果为空，则用 normalize_candidates 函数
    # 对原始查询进行标准化处理（如去除停用词、同义归一化等）。
    # 如果标准化后仍为空，则以原始查询本身作为唯一候选词。
    candidates = state.get("retrieval_candidates") or normalize_candidates(query)
    if not candidates:
        candidates = [query]

    # 如果用户未指定知识源，默认只检索 laws（法律）源。
    if not domain_sources:
        domain_sources = ["laws"]

    print(f"  候选查询词: {len(candidates)} 个 -> {candidates[:100]}{'...' if len(candidates) > 100 else ''}")

    # 初始化结果收集器和统计计数器 (两通道: 图谱 + FAISS; 1c 已删除)。
    all_recalls = []  # 收集所有通道的召回结果
    recall_stats = {"graph": 0, "faiss": 0}  # 各通道命中数统计

    # 外层循环：遍历每个知识源（如 laws, regulations, cases...）
    for source_tag in domain_sources:
        source_tag = str(source_tag).strip().lower()

        # 内层循环：对每个候选查询词，依次执行三条召回通道
        for cand in candidates:
            if not cand or not cand.strip():
                continue

            # 通道 1a: 图谱实体召回（4 种 Cypher 模式）
            # 返回 base_score 范围 40-100，归一化后为 0.4-1.0
            # 传入 state["retrieval_keywords"]（LLM 按重要性排序），使 main_kw = keywords[0] 有意义
            graph_results = _graph_entity_recall(cand, source_tag, top_k=10,
                                                 ordered_keywords=keywords)
            if graph_results:
                all_recalls.extend(graph_results)
                recall_stats["graph"] += len(graph_results)

            # 通道 1b: FAISS 语义召回（向量相似度搜索）
            # 返回分数经过 0.6 衰减，整体低于图谱召回
            faiss_results = _faiss_semantic_recall(cand, source_tag, top_k=10)
            if faiss_results:
                all_recalls.extend(faiss_results)
                recall_stats["faiss"] += len(faiss_results)

        print(f"    . {source_tag}: 图谱={recall_stats['graph']}, FAISS={recall_stats['faiss']}")

    # 去重 + 意图偏置 (P3): 同法条多关系时优先保留意图偏好关系行, 并 ×1.3 偏置。
    # 多分面意图 → 偏好关系并集, 同时照顾 condition/liability/penalty 等多种关系偏好。
    # 替代原「先到先得」朴素去重 —— 修复隐式 DEFINES 恒胜 bug (问"责任"却拿"定义"行)。
    unique_recalls = _dedup_with_intent_bias(all_recalls, intents)

    _intent_label = intents if isinstance(intents, list) else [intents]
    print(f"  召回汇总(两通道): 图谱={recall_stats['graph']}, FAISS={recall_stats['faiss']}, "
          f"意图偏置={_intent_label}, 去重后共 {len(unique_recalls)} 条")

    # 返回最终结果：
    # - entity_recall_results: 去重后的召回结果列表，供 Stage 2 排序层使用
    # - recall_stats: 各通道命中统计，供监控和分析使用
    return {
        "entity_recall_results": unique_recalls,
        "recall_stats": recall_stats,
    }