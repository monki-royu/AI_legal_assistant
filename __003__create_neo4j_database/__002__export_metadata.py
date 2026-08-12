# 📜 代码文字逻辑解析
# 本文件是 AI 法律助理项目中负责从 Neo4j 图数据库导出元数据的脚本模块。
# 其核心作用是在法律知识图谱构建完成（由 __001__graph_importer.py 完成实体与关系导入）后，
# 将 Neo4j 中的节点与关系数据导出为 JSON 格式的元数据文件 legal_metadata.json。
# 该元数据文件是连接"图数据库"与"向量检索"两个阶段的关键桥梁：
# 后续的 __003__vector_index.py 会读取该 JSON 文件，将每条关系三元组拼接为文本，
# 使用 bge-m3 模型编码为向量，并构建 FAISS 索引，从而实现基于语义的相似度检索。
# 本脚本逻辑简洁：通过 common.path_utils.get_file_path 获取项目内的标准输出路径，
# 调用 neo4j_client 单例的 export_tcm_metadata_to_json 方法完成实际导出工作，
# 最后打印导出路径以确认成功。该模块属于离线数据预处理流程的一环。

# 从公共模块导入 neo4j_client 单例，封装了 Neo4j 数据库连接及元数据导出能力
from common.neo4j_manager import neo4j_client
# 从公共模块导入 get_file_path 工具函数，用于将项目内相对路径转换为绝对路径
from common.path_utils import get_file_path


# 脚本主入口：当本文件被直接运行（而非被作为模块导入）时执行以下代码块
if __name__ == "__main__":
    # 调用 get_file_path 获取元数据输出文件的绝对路径
    # 输出路径为项目根目录下 __003__create_neo4j_database/legal_metadata.json
    output_path = get_file_path("__003__create_neo4j_database/legal_metadata.json")
    # 调用 neo4j_client 的 export_tcm_metadata_to_json 方法，
    # 将 Neo4j 中的法律节点与关系数据导出为 JSON 文件
    # 方法名中 tcm 沿用自中医项目（可迁移模板），实际导出的是法律领域数据
    neo4j_client.export_tcm_metadata_to_json(output_path)
    # 打印导出成功提示信息及输出文件路径，便于用户确认结果位置
    print(f"✅ 法律元数据已导出到 {output_path}")
