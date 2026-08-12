# 📜 代码文字逻辑解析
# 本文件是 AI 法律助理项目中负责构建 FAISS 向量索引的核心模块，对应中医项目的
# faiss_embedding.py 模板。其作用是将法律知识图谱中的关系三元组转化为可被语义
# 检索的向量索引。整体流程分为两个阶段：第一阶段为构建索引（build_faiss_index），
# 读取 __002__export_metadata.py 导出的 legal_metadata.json，提取其中的 relationships
# 关系列表，将每条三元组拼接为"主体 关系 客体"形式的文本，并保留结构化元数据
# （包括起止节点名称、类型、关系类型等）。随后调用 bge-m3 嵌入模型对文本进行批量
# 编码并归一化，使用 FAISS 的 IndexFlatIP（内积索引，因向量已归一化故等价于余弦相似度）
# 构建索引，最终将索引文件与元数据映射文件分别保存为 .index 与 .pkl。
# 第二阶段为检索接口（search），输入用户查询文本，编码为向量后在 FAISS 索引中
# 检索 top_k 最相似的三元组，返回带相似度分数的结果列表。该模块为后续法律检索
# 智能体提供了底层的向量召回能力，是混合检索（向量+图谱）的关键组件。

"""
法律知识图谱 FAISS 向量索引
对应中医项目 __003__create_neo4j_database/__003__faiss_embedding.py

从 legal_metadata.json 读取三元组, 用 bge-m3 编码, 构建 FAISS 索引
输出:
  - legal_embedding_faiss.index
  - legal_embedding_faiss_id2text.pkl
"""
# 导入标准库 os，用于检查文件/路径是否存在
import os
# 导入标准库 json，用于读取元数据 JSON 文件
import json
# 导入标准库 pickle，用于序列化保存三元组元数据映射（id2text）
import pickle

# 导入 faiss 库，Facebook 开源的高效向量相似度检索库，用于构建与查询向量索引
import faiss
# 导入 numpy，用于将嵌入向量转换为 float32 数组以供 FAISS 使用
import numpy as np
# 导入 tqdm，用于在批量编码时显示进度条
from tqdm import tqdm

# 从公共模块导入 embedding_model 单例，封装了 bge-m3 嵌入模型的编码能力
from common.embedding_model import embedding_model
# 从公共模块导入 get_file_path 与 root_dir 工具函数，用于路径转换
from common.path_utils import get_file_path, root_dir


def build_faiss_index(metadata_path: str, index_path: str, id2text_path: str):
    """
    从 legal_metadata.json 读取三元组, 构建FAISS索引

    作用：
        读取法律元数据 JSON 文件，提取所有关系三元组，拼接为文本后使用 bge-m3 模型
        编码为归一化向量，构建基于内积（等价余弦相似度）的 FAISS 索引，
        并将索引文件与三元组元数据映射分别持久化保存。

    参数：
        metadata_path: str，输入的元数据 JSON 文件路径（由 __002__export_metadata.py 产出）。
        index_path: str，输出的 FAISS 索引文件路径（.index）。
        id2text_path: str，输出的三元组元数据映射文件路径（.pkl）。

    返回值：
        bool：构建成功返回 True，失败（文件缺失或无关系数据）返回 False。

    可迁移性说明：
        该函数与具体业务解耦，只要元数据 JSON 遵循 {"relationships": [...]} 结构即可复用。
        迁移到其他领域时，仅需调整三元组文本拼接方式与元数据字段命名即可。
    """
    # 检查元数据文件是否存在，不存在则提示用户先运行导出脚本并返回 False
    if not os.path.exists(metadata_path):
        # 打印警告信息，提示元数据文件路径
        print(f"⚠️  元数据文件不存在: {metadata_path}")
        # 提示用户应先运行的导出脚本路径
        print("    请先运行 __003__create_neo4j_database/__002__export_metadata.py")
        # 返回 False 表示构建失败
        return False

    # 以 UTF-8 编码只读打开元数据 JSON 文件
    with open(metadata_path, "r", encoding="utf-8") as f:
        # 使用 json.load 解析 JSON 文件内容为 Python 字典
        metadata = json.load(f)

    # 元数据结构: {"nodes": [...], "relationships": [...]}
    # 参考 neo4j_manager.export_tcm_metadata_to_json 的输出格式
    # 从元数据中获取 relationships 关系列表，若字段不存在则使用空列表
    relationships = metadata.get("relationships", [])
    # 若关系列表为空，则无法构建有意义的索引，提示并返回 False
    if not relationships:
        # 打印警告，说明元数据中无关系数据
        print("⚠️  元数据中无关系数据, 无法构建索引")
        # 返回 False 表示构建失败
        return False

    # 打印待编码的三元组总条数，便于用户了解数据规模
    print(f"📊 共 {len(relationships)} 条三元组")

    # 拼接三元组文本: "主体 关系 客体"
    # 初始化三元组文本列表，用于存放拼接后的字符串
    triple_texts = []
    # 初始化三元组元数据列表，用于存放每条三元组的结构化信息
    triple_meta = []
    # 遍历所有关系，逐条构造文本与元数据
    for rel in relationships:
        # 获取起始节点名称，缺失则使用空字符串
        subject = rel.get("subject", "")
        # 获取起始节点类型，缺失则使用空字符串
        subject_type = rel.get("subject_type", "")
        # 获取关系类型名称，缺失则使用空字符串
        relation = rel.get("relation", "")
        # 获取目标节点名称，缺失则使用空字符串
        object = rel.get("object", "")
        # 获取目标节点类型，缺失则使用空字符串
        object_type = rel.get("object_type", "")
        # 将主体、关系、客体拼接为"主体 关系 客体"形式的文本，作为编码输入
        triple_text = f"{subject} {relation} {object}"
        # 将拼接后的文本追加到文本列表
        triple_texts.append(triple_text)
        # 构造该三元组的结构化元数据字典，包含文本与各字段信息，用于检索结果展示
        triple_meta.append({
            "triple_text": triple_text,   # 拼接后的三元组文本
            "from_label": subject_type,   # 起始节点类型
            "rel_type": relation,         # 关系类型
            "to_label": object_type,      # 目标节点类型
            "from_name": subject,         # 起始节点名称
            "to_name": object,            # 目标节点名称
        })

    # 编码
    # 打印编码开始提示，说明使用的是 bge-m3 模型
    print("🔢 正在编码向量(bge-m3)...")
    # 调用 embedding_model 的 encode 方法对三元组文本列表进行批量编码
    # batch_size=32 设置批大小为 32，平衡显存占用与编码速度
    # normalize_embeddings=True 对输出向量进行 L2 归一化，使内积等价于余弦相似度
    # show_progress_bar=True 显示编码进度条
    embeddings = embedding_model.encode(
        triple_texts,
        batch_size=32,
        normalize_embeddings=True,
        show_progress_bar=True,
    )
    # 将编码结果转换为 numpy 数组，数据类型为 float32（FAISS 要求的精度）
    embeddings = np.array(embeddings, dtype=np.float32)

    # 构建FAISS索引
    # 获取向量维度（即嵌入模型的输出维度），用于创建 FAISS 索引
    dim = embeddings.shape[1]
    # 打印向量维度，便于确认模型输出是否符合预期
    print(f"📐 向量维度: {dim}")
    # 创建 FAISS IndexFlatIP 索引，基于精确内积检索
    # 因向量已归一化，内积结果等价于余弦相似度，适用于语义相似度检索
    index = faiss.IndexFlatIP(dim)  # 内积(已归一化=余弦相似度)
    # 将所有向量添加到 FAISS 索引中
    index.add(embeddings)

    # 保存
    # 将 FAISS 索引写入指定路径的 .index 文件
    faiss.write_index(index, index_path)
    # 以二进制写模式打开 id2text 文件
    with open(id2text_path, "wb") as f:
        # 使用 pickle 将三元组元数据列表序列化保存，供检索时反查使用
        pickle.dump(triple_meta, f)

    # 打印 FAISS 索引保存成功提示及路径
    print(f"✅ FAISS索引已保存: {index_path}")
    # 打印 id2text 保存成功提示及路径
    print(f"✅ id2text已保存: {id2text_path}")
    # 打印索引中包含的向量总数，确认所有三元组均已入库
    print(f"   索引包含 {index.ntotal} 条向量")
    # 返回 True 表示构建成功
    return True


def search(query: str, top_k: int = 5, index_path: str = None, id2text_path: str = None):
    """
    检索接口: 输入查询文本, 返回top_k相关三元组

    作用：
        加载已构建的 FAISS 索引与三元组元数据映射，将用户查询文本编码为向量，
        在索引中检索 top_k 个最相似的三元组，返回包含相似度分数与元数据的结果列表。

    参数：
        query: str，用户查询文本。
        top_k: int，返回的最相似结果数量，默认为 5。
        index_path: str，FAISS 索引文件路径，为 None 时使用默认路径。
        id2text_path: str，三元组元数据映射文件路径，为 None 时使用默认路径。

    返回值：
        list[dict]：检索结果列表，每个元素为包含 triple_text、from_label、rel_type、
                   to_label、from_name、to_name、score 字段的字典。
                   若索引文件不存在则返回空列表。

    可迁移性说明：
        该函数为通用向量检索接口，迁移时需确保索引文件与元数据映射文件配套使用，
        并保持 embedding_model 的编码方式与构建索引时一致。
    """
    # 若未显式指定索引路径，则使用项目默认的索引文件路径
    if index_path is None:
        index_path = get_file_path("__003__create_neo4j_database/legal_embedding_faiss.index")
    # 若未显式指定 id2text 路径，则使用项目默认的元数据映射文件路径
    if id2text_path is None:
        id2text_path = get_file_path("__003__create_neo4j_database/legal_embedding_faiss_id2text.pkl")

    # 检查索引文件或元数据映射文件是否存在，任一缺失则返回空列表
    if not os.path.exists(index_path) or not os.path.exists(id2text_path):
        return []

    # 从磁盘读取 FAISS 索引到内存
    index = faiss.read_index(index_path)
    # 以二进制读模式打开 id2text 文件
    with open(id2text_path, "rb") as f:
        # 使用 pickle 反序列化加载三元组元数据列表
        triple_meta = pickle.load(f)

    # 调用 embedding_model 对查询文本进行编码，并归一化（与构建索引时保持一致）
    query_vec = embedding_model.encode([query], normalize_embeddings=True)
    # 将查询向量转换为 float32 类型的 numpy 数组，符合 FAISS 输入要求
    query_vec = np.array(query_vec, dtype=np.float32)

    # 在 FAISS 索引中检索 top_k 个最相似向量
    # 返回 scores（相似度分数数组）与 indices（对应向量索引数组）
    scores, indices = index.search(query_vec, top_k)

    # 初始化检索结果列表
    results = []
    # 遍历检索返回的索引（indices[0] 因为输入是单条查询）
    for i, idx in enumerate(indices[0]):
        # 若索引为负数，表示 FAISS 未找到足够结果（填充项），跳过
        if idx < 0:
            continue
        # 根据索引从元数据列表中取出对应的三元组元数据
        meta = triple_meta[idx]
        # 将元数据与相似度分数组合为结果字典，追加到结果列表
        results.append({
            **meta,                          # 展开三元组元数据的所有字段
            "score": float(scores[0][i]),    # 添加相似度分数，转为 Python float
        })
    # 返回最终的检索结果列表
    return results


# 脚本主入口：当本文件被直接运行时执行
if __name__ == "__main__":
    # 获取元数据 JSON 文件的绝对路径
    metadata_path = get_file_path("__003__create_neo4j_database/legal_metadata.json")
    # 获取 FAISS 索引输出文件的绝对路径
    index_path = get_file_path("__003__create_neo4j_database/legal_embedding_faiss.index")
    # 获取 id2text 映射输出文件的绝对路径
    id2text_path = get_file_path("__003__create_neo4j_database/legal_embedding_faiss_id2text.pkl")
    # 调用 build_faiss_index 构建索引
    build_faiss_index(metadata_path, index_path, id2text_path)

    # 测试检索
    # 打印测试检索的查询词
    print("\n🔍 测试检索: '违约金'")
    # 调用 search 函数检索"违约金"相关的 top 3 三元组
    results = search("违约金", top_k=3, index_path=index_path, id2text_path=id2text_path)
    # 遍历检索结果并打印相似度分数与三元组文本
    for r in results:
        # 格式化打印：[分数] 三元组文本
        print(f"  [{r['score']:.4f}] {r['triple_text']}")
