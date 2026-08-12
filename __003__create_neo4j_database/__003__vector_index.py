"""
法律知识图谱 FAISS 向量索引
对应中医项目 __003__create_neo4j_database/__003__faiss_embedding.py

从 legal_metadata.json 读取三元组, 用 bge-m3 编码, 构建 FAISS 索引
输出:
  - legal_embedding_faiss.index
  - legal_embedding_faiss_id2text.pkl
"""
import os
import json
import pickle

import faiss
import numpy as np
from tqdm import tqdm

from common.embedding_model import embedding_model
from common.path_utils import get_file_path, root_dir


def build_faiss_index(metadata_path: str, index_path: str, id2text_path: str):
    """
    从 legal_metadata.json 读取三元组, 构建FAISS索引
    """
    if not os.path.exists(metadata_path):
        print(f"⚠️  元数据文件不存在: {metadata_path}")
        print("    请先运行 __003__create_neo4j_database/__002__export_metadata.py")
        return False

    with open(metadata_path, "r", encoding="utf-8") as f:
        metadata = json.load(f)

    # 元数据结构: {"nodes": [...], "relationships": [...]}
    # 参考 neo4j_manager.export_tcm_metadata_to_json 的输出格式
    relationships = metadata.get("relationships", [])
    if not relationships:
        print("⚠️  元数据中无关系数据, 无法构建索引")
        return False

    print(f"📊 共 {len(relationships)} 条三元组")

    # 拼接三元组文本: "主体 关系 客体"
    triple_texts = []
    triple_meta = []
    for rel in relationships:
        subject = rel.get("subject", "")
        subject_type = rel.get("subject_type", "")
        relation = rel.get("relation", "")
        object = rel.get("object", "")
        object_type = rel.get("object_type", "")
        triple_text = f"{subject} {relation} {object}"
        triple_texts.append(triple_text)
        triple_meta.append({
            "triple_text": triple_text,
            "from_label": subject_type,
            "rel_type": relation,
            "to_label": object_type,
            "from_name": subject,
            "to_name": object,
        })

    # 编码
    print("🔢 正在编码向量(bge-m3)...")
    embeddings = embedding_model.encode(
        triple_texts,
        batch_size=32,
        normalize_embeddings=True,
        show_progress_bar=True,
    )
    embeddings = np.array(embeddings, dtype=np.float32)

    # 构建FAISS索引
    dim = embeddings.shape[1]
    print(f"📐 向量维度: {dim}")
    index = faiss.IndexFlatIP(dim)  # 内积(已归一化=余弦相似度)
    index.add(embeddings)

    # 保存
    faiss.write_index(index, index_path)
    with open(id2text_path, "wb") as f:
        pickle.dump(triple_meta, f)

    print(f"✅ FAISS索引已保存: {index_path}")
    print(f"✅ id2text已保存: {id2text_path}")
    print(f"   索引包含 {index.ntotal} 条向量")
    return True


def search(query: str, top_k: int = 5, index_path: str = None, id2text_path: str = None):
    """
    检索接口: 输入查询文本, 返回top_k相关三元组
    """
    if index_path is None:
        index_path = get_file_path("__003__create_neo4j_database/legal_embedding_faiss.index")
    if id2text_path is None:
        id2text_path = get_file_path("__003__create_neo4j_database/legal_embedding_faiss_id2text.pkl")

    if not os.path.exists(index_path) or not os.path.exists(id2text_path):
        return []

    index = faiss.read_index(index_path)
    with open(id2text_path, "rb") as f:
        triple_meta = pickle.load(f)

    query_vec = embedding_model.encode([query], normalize_embeddings=True)
    query_vec = np.array(query_vec, dtype=np.float32)

    scores, indices = index.search(query_vec, top_k)

    results = []
    for i, idx in enumerate(indices[0]):
        if idx < 0:
            continue
        meta = triple_meta[idx]
        results.append({
            **meta,
            "score": float(scores[0][i]),
        })
    return results


if __name__ == "__main__":
    metadata_path = get_file_path("__003__create_neo4j_database/legal_metadata.json")
    index_path = get_file_path("__003__create_neo4j_database/legal_embedding_faiss.index")
    id2text_path = get_file_path("__003__create_neo4j_database/legal_embedding_faiss_id2text.pkl")
    build_faiss_index(metadata_path, index_path, id2text_path)

    # 测试检索
    print("\n🔍 测试检索: '违约金'")
    results = search("违约金", top_k=3, index_path=index_path, id2text_path=id2text_path)
    for r in results:
        print(f"  [{r['score']:.4f}] {r['triple_text']}")
