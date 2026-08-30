"""
法律知识图谱 FAISS 向量索引构建
================================

# ============================================================
# 文件名称: __003__create_neo4j_database/__003__faiss_embedding.py
# 文件作用: 从 Neo4j 抽取实体名称 -> 用 bge-m3 编码成向量 -> 存入 FAISS 索引
# ============================================================
# 【这个文件是干什么的？】
#   本文件负责给"知识图谱里的实体"建一套"语义向量索引"。
#   通俗说: 把每个法律实体名(如"违约责任""农民工""解除合同")变成一串数字向量,
#   再放进 FAISS 这个高效的相似度搜索引擎。之后用户用自然语言提问时,
#   就能通过"向量相似度"快速找到最相关的实体, 实现"含义相近就召回"的检索。
#
#   它会建立三类索引:
#     (1) 全局实体索引: 所有实体类型汇总去重后的一个大索引;
#     (2) 按知识源分类索引: laws / regulations / cases / industry / interpretations 各一份;
#     (3) 按实体类型索引: 重点类型 LegalConcept / Action / Liability 各一份(便于语义跳转)。
#
# 【代码逻辑主线】
#   1. build_faiss_index()      : 把"文本列表"编码成向量并写 FAISS 索引 + id->文本映射;
#   2. get_entity_names_by_label(): 按节点标签从 Neo4j 取实体名;
#   3. get_entity_names_by_source(): 按知识源(source_id)从 Neo4j 取实体名;
#   4. build_all_indices()      : 串起以上三步, 分三类循环建索引并落盘。
#
# 【新手建议】
#   1) 先看 build_all_indices(), 它像"总指挥"告诉你建了哪些索引;
#   2) 再看 build_faiss_index(), 它是真正的"编码+存储"核心;
#   3) FAISS / Embedding 的概念若陌生, 记住一句话: 文本 -> 向量(编码) -> 存起来(索引) ->
#      查询时把问题也编码, 比谁向量更接近(内积/余弦)。
#
# 📜 代码文字逻辑解析 (what / why / how)
#   WHAT : 为图谱实体建可语义检索的向量索引文件(.index + .pkl 映射)。
#   WHY  : 图谱擅长"精确关系跳转", 但不擅长"意思差不多"的模糊匹配。向量索引补上这块短板:
#          让"用户问法"和"图谱里的实体名"即使措辞不同也能命中, 是 RAG 检索的必要一环。
#   HOW  : 从 Neo4j 用 Cypher 取出实体名 -> embedding_model.encode 转向量 -> faiss.IndexFlatIP
#          建内积索引(归一化后等价余弦) -> 写盘; 同时用 pickle 保存 id->原文 映射, 召回后能还原文本。
"""

# 导入 os: 用于拼接路径、创建目录
import os

# 导入 pickle: 把"索引 id -> 原始文本"的映射字典序列化保存到磁盘(.pkl)
import pickle

# 导入 faiss: Facebook 开源的高性能向量相似度检索库(本文用 IndexFlatIP 精确内积检索)
import faiss

# 导入 numpy: 向量以 numpy 数组形式参与计算与存储
import numpy as np

# 从 common.neo4j_manager 引入全局 Neo4j 客户端单例(用于取实体名)
from common.neo4j_manager import neo4j_client

# 从 common.embedding_model 引入 embedding_model: bge-m3  embedding 模型封装, 负责把文本编码成向量
from common.embedding_model import embedding_model

# 从 common.path_utils 引入 root_dir(项目根目录)、get_file_path(相对路径解析为绝对路径)
from common.path_utils import root_dir, get_file_path


# 知识源 -> 对应 Neo4j 节点标签 的映射表
# 作用: 按知识源分类建索引时, 通过 source_id 找到该源在图里的主节点标签
_SOURCE_LABEL_MAP = {
    "laws": "Law",                       # 法律法规源 -> Law 标签
    "regulations": "Regulation",         # 行政法规源 -> Regulation 标签
    "cases": "Case",                     # 裁判案例源 -> Case 标签
    "industry_sources": "IndustryStandard",  # 行业标准源 -> IndustryStandard 标签
    "interpretations": "Interpretation",  # 司法解释源 -> Interpretation 标签
}

# 需要建索引的实体标签清单(不含知识源/文档/条款等结构型节点, 只索引"语义实体")
_INDEX_ENTITY_LABELS = [
    "LegalConcept", "PartyRole", "Action", "Condition", "Penalty", "Liability",
    "Law", "Regulation", "Interpretation", "IndustryStandard", "Case",
]

# 索引文件统一保存目录: 项目根/data/knowledge_base/index
_INDEX_DIR = os.path.join(root_dir, "data", "knowledge_base", "index")


def build_faiss_index(sentences, index_path="faiss.index", mapping_path="id2text.pkl"):
    """
    基于字符串列表构建 FAISS 索引并保存。

    参数:
        sentences (list): 输入的文本列表(每行是一个实体名或短语)
        index_path (str): FAISS 索引(.index)的保存路径
        mapping_path (str): id->原始文本 映射(.pkl)的保存路径

    逻辑:
        1. 向量化: embedding_model.encode 把文本转成归一化向量;
        2. 建索引: 用 IndexFlatIP(内积)做精确最近邻检索, 归一化后等价于余弦相似度;
        3. 存索引: faiss.write_index 写盘;
        4. 存映射: pickle 把 idx->文本 字典写盘, 召回时反查原文。
    """
    # 防御: 空列表直接跳过, 避免建出空索引
    if not sentences:
        print(f"⚠️ 空文本列表, 跳过构建: {index_path}")
        return

    # 打印待编码条数, 让用户心里有数
    print(f"  📊 编码 {len(sentences)} 条文本...")

    # 1) 生成向量: 归一化后内积=余弦相似度, 便于后续直接按相似度排序
    embeddings = embedding_model.encode(
        sentences, convert_to_numpy=True, normalize_embeddings=True
    )

    # 2) 构建 FAISS 索引: IndexFlatIP 为"精确内积"索引(不压缩、最准, 数据量不大时适用)
    dim = embeddings.shape[1]                  # 向量维度(由 embedding 模型决定, 如 1024)
    index = faiss.IndexFlatIP(dim)             # 内积相似度(归一化后等价于余弦相似度)
    index.add(embeddings.astype('float32'))    # 把 numpy 向量(转 float32)加入索引

    # 3) 保存索引文件: 先确保父目录存在(不存在则创建)
    os.makedirs(os.path.dirname(index_path) if os.path.dirname(index_path) else '.', exist_ok=True)
    faiss.write_index(index, index_path)       # 把索引对象持久化到磁盘

    # 4) 保存 id -> 原始文本 映射: 检索返回的是整数 id, 需映射回实体名
    id2text = {i: s for i, s in enumerate(sentences)}   # 0->句0, 1->句1, ...
    with open(mapping_path, "wb") as f:
        pickle.dump(id2text, f)                        # 二进制写入 .pkl

    # 打印落盘信息, 便于核对
    print(f"  ✅ 索引已保存: {index_path}")
    print(f"  ✅ 映射已保存: {mapping_path}")
    print(f"     索引条数: {len(sentences)}, 向量维度: {dim}")


def get_entity_names_by_label(label: str) -> list:
    """
    从 Neo4j 获取指定标签的所有实体名称。

    参数:
        label (str): Neo4j 节点标签(如 "LegalConcept" / "Action")
    返回:
        list: 该标签下所有非空的实体名称(已按名字排序)
    逻辑:
        用 Cypher `MATCH (n:label) RETURN DISTINCT n.name` 取出去重后的 name 列表。
    """
    # 拼接 Cypher: 匹配某标签节点, 过滤掉 name 为空或空串的, 去重并按名字排序
    query = f"""
    MATCH (n:{label})
    WHERE n.name IS NOT NULL AND n.name <> ''
    RETURN DISTINCT n.name AS name
    ORDER BY name
    """
    try:
        # 执行查询
        results = neo4j_client.run_cypher(query)
        # 从每行取 name 字段, 再次过滤空值(防御性)
        return [r["name"] for r in results if r.get("name")]
    except Exception as e:
        # 查询失败(如标签不存在)时打印警告并返回空列表, 不中断整体流程
        print(f"  ⚠️ 获取 {label} 实体失败: {e}")
        return []


def get_entity_names_by_source(source_id: str) -> list:
    """
    从 Neo4j 获取指定知识源的所有实体名称(通过关系的 provenance 属性)。

    参数:
        source_id (str): 知识源ID(laws / regulations / cases / ...)
    返回:
        list: 该知识源涉及的所有实体名称(UNION 去重)
    逻辑:
        沿任意关系 r 查找 r.source_id = 某源 的两边节点(name),
        用 UNION 把"起点名"和"终点名"合并去重。
    """
    # 拼接 Cypher: 上半段取关系起点 name, 下半段取关系终点 name, UNION 自动去重
    query = f"""
    MATCH (n)-[r]->(m)
    WHERE r.source_id = $source_id
    RETURN DISTINCT n.name AS name
    UNION
    MATCH (n)-[r]->(m)
    WHERE r.source_id = $source_id
    RETURN DISTINCT m.name AS name
    """
    try:
        # 执行查询, 传入参数 source_id
        results = neo4j_client.run_cypher(query, {"source_id": source_id})
        # 取 name 并过滤空值
        return [r["name"] for r in results if r.get("name")]
    except Exception as e:
        # 失败则返回空列表, 保证主流程不崩
        print(f"  ⚠️ 获取 {source_id} 实体失败: {e}")
        return []


def build_all_indices():
    """
    构建所有 FAISS 索引(总指挥函数):
      1. 全局实体索引: 所有语义实体类型汇总去重后的一个大索引;
      2. 按知识源分类索引: 每个 source_id 一份;
      3. 按实体类型索引: 重点类型 LegalConcept / Action / Liability 各一份(便于语义跳转)。
    逻辑: 先确定要建哪些索引 -> 从 Neo4j 取对应实体名 -> 去重 -> 调 build_faiss_index 落盘。
    """
    # 打印分节标题
    print("=" * 60)
    print("构建法律知识图谱 FAISS 向量索引")
    print("=" * 60)

    # 确保索引目录存在(不存在则递归创建)
    os.makedirs(_INDEX_DIR, exist_ok=True)

    # ========== 1. 全局实体索引 ==========
    print("\n📚 Step 1: 构建全局实体索引")
    all_entities = []                              # 汇总所有类型的实体名
    for label in _INDEX_ENTITY_LABELS:
        entities = get_entity_names_by_label(label)  # 按标签取实体名
        print(f"  · {label}: {len(entities)} 个实体")
        all_entities.extend(entities)               # 并入总列表

    # 去重(同一实体名可能出现在多个标签下)
    all_entities = list(set(all_entities))
    print(f"  总计: {len(all_entities)} 个唯一实体")

    # 计算全局索引与映射的保存路径
    global_index_path = os.path.join(_INDEX_DIR, "legal_entities_faiss.index")
    global_mapping_path = os.path.join(_INDEX_DIR, "legal_entities_id2text.pkl")
    # 真正建索引并落盘
    build_faiss_index(all_entities, global_index_path, global_mapping_path)

    # ========== 2. 按知识源分类构建索引 ==========
    print("\n📚 Step 2: 按知识源分类构建索引")
    for source_id in ["laws", "regulations", "cases", "industry_sources", "interpretations"]:
        print(f"\n  处理知识源: {source_id}")
        entities = get_entity_names_by_source(source_id)   # 按源取实体名
        entities = list(set(entities))                      # 去重
        print(f"    · 实体数: {len(entities)}")

        # 该源有实体才建索引, 避免生成空文件
        if entities:
            index_path = os.path.join(_INDEX_DIR, f"{source_id}_faiss.index")
            mapping_path = os.path.join(_INDEX_DIR, f"{source_id}_id2text.pkl")
            build_faiss_index(entities, index_path, mapping_path)

    # ========== 3. 构建实体类型索引 (用于语义跳转) ==========
    print("\n📚 Step 3: 构建实体类型专属索引")
    for entity_type in ["LegalConcept", "Action", "Liability"]:
        entities = get_entity_names_by_label(entity_type)   # 按类型取实体名
        entities = list(set(entities))                       # 去重
        print(f"  · {entity_type}: {len(entities)} 个实体")

        # 有实体才建索引
        if entities:
            # 文件名用小写(entity_type.lower()), 如 legalconcept_faiss.index
            index_path = os.path.join(_INDEX_DIR, f"{entity_type.lower()}_faiss.index")
            mapping_path = os.path.join(_INDEX_DIR, f"{entity_type.lower()}_id2text.pkl")
            build_faiss_index(entities, index_path, mapping_path)

    # 打印完成汇总
    print("\n" + "=" * 60)
    print("✅ FAISS 索引构建完成!")
    print("=" * 60)
    print(f"\n索引文件保存在: {_INDEX_DIR}")
    print("包含:")
    print("  · legal_entities_faiss.index (全局实体索引)")
    print("  · {source_id}_faiss.index (按知识源分类)")
    print("  · {entity_type}_faiss.index (按实体类型分类)")


# 直接运行本文件(python __003__faiss_embedding.py)时, 执行全部索引构建
if __name__ == '__main__':
    build_all_indices()
