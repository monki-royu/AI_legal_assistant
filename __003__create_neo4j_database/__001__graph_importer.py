# 📜 代码文字逻辑解析
# 本文件是 AI 法律助理项目中负责构建 Neo4j 知识图谱的核心导入模块。
# 其主要功能是将经过信息抽取后的 JSON 数据（包含实体与关系三元组）
# 批量导入到 Neo4j 图数据库中，从而形成可被图查询的法律知识网络。
# 整体流程为：先从 extract_law_data.json 文件中读取抽取结果，
# 遍历每一条法律文件的抽取字典（extract_dict），其中包含 entities 实体列表
# 和 relations 关系列表。对每个实体调用 create_entity 方法，通过 MERGE 语句
# 在图中创建或更新节点（保证幂等性，避免重复插入）；对每个关系调用 create_relation
# 方法，先 MATCH 匹配首尾两个节点，再通过 MERGE 创建有向边。
# 在属性设置上，动态根据 attributes 字典构造 SET 子句，将额外属性写入节点。
# 整个导入过程使用 tqdm 显示进度，并通过 try-except 容错，
# 单条失败不影响整体流程，保证大规模导入的健壮性。
# 该模块是后续向量检索与智能体检索的基础数据准备步骤。

# 导入标准库 json，用于解析抽取结果 JSON 文件
import json

# 从公共模块导入 neo4j_client 单例，封装了 Neo4j 数据库的连接与 Cypher 执行能力
from common.neo4j_manager import neo4j_client
# 从公共模块导入 get_file_path 工具函数，用于将相对路径转换为项目根目录下的绝对路径
from common.path_utils import get_file_path
# 导入 tqdm 进度条库，用于在批量导入时可视化处理进度
from tqdm import tqdm


# 定义法律图谱导入器类，封装实体与关系的导入逻辑
class LegalGraphImporter:
    """
    法律知识图谱导入器。

    作用：
        将信息抽取阶段产出的 JSON 数据（实体 + 关系三元组）批量导入 Neo4j 图数据库，
        构建法律领域的结构化知识图谱，为后续的图查询与混合检索提供数据基础。

    参数：
        neo4j_client: Neo4j 数据库客户端实例，需提供 run_cypher(cypher, parameters) 方法
                      用于执行 Cypher 语句并传入参数化查询变量。

    返回值：
        无（类定义）。

    可迁移性说明：
        该类与具体业务（法律）耦合度较低，核心逻辑（实体 MERGE、关系 MERGE、JSON 遍历）
        可直接迁移至其他领域（如医疗、金融知识图谱构建）。
        迁移时只需调整 JSON 数据结构中的字段名映射，以及 Neo4j 节点的 label 命名规则即可。
    """

    # 构造函数，初始化导入器并接收 Neo4j 客户端
    def __init__(self, neo4j_client):
        """
        初始化 LegalGraphImporter 实例。

        作用：
            保存 Neo4j 客户端引用，后续所有 Cypher 操作均通过该客户端执行。

        参数：
            neo4j_client: Neo4j 数据库客户端对象，需支持 run_cypher 方法。

        返回值：
            无。

        可迁移性说明：
            采用依赖注入方式，便于在测试时替换为 mock 客户端，提升可测试性。
        """
        # 将传入的 neo4j_client 保存为实例属性，供类内其他方法调用
        self.neo4j_client = neo4j_client

    # 创建实体节点方法，将单个实体写入 Neo4j 图数据库
    def create_entity(self, entity):
        """
        在 Neo4j 中创建或更新一个实体节点。

        作用：
            根据实体字典中的 type（作为节点 label）、name（作为唯一标识）以及
            可选的 attributes（额外属性），使用 MERGE 语句实现"存在则更新、不存在则创建"
            的幂等写入，保证重复导入不会产生重复节点。

        参数：
            entity: dict，实体字典，至少包含 "type" 和 "name" 字段，
                    可选 "attributes" 字段（dict）存放额外属性键值对。

        返回值：
            无（直接通过 neo4j_client 执行 Cypher，无返回值）。

        可迁移性说明：
            通过动态构造 SET 子句适配任意属性集合，具备良好的通用性。
            迁移时注意 Neo4j label 命名需符合规范（不含特殊字符）。
        """
        # 从实体字典中取出 type 字段，作为 Neo4j 节点的标签（label）
        label = entity["type"]
        # 从实体字典中取出 name 字段，作为节点的核心标识属性
        name = entity["name"]
        # 从实体字典中获取 attributes 字段，若不存在则使用空字典；or {} 用于处理 None 值
        attributes = entity.get("attributes", {}) or {}

        # 动态构造 SET 子句字符串：对每个属性键生成 "n.键 = $键" 形式，逗号分隔
        # 这样可以通过参数化方式安全地设置属性值，避免 Cypher 注入风险
        set_clause = ", ".join([f"n.{k} = ${k}" for k in attributes.keys()])
        # 构造参数字典：包含 name、type 以及展开的所有额外属性，供 Cypher 参数化查询使用
        parameters = {"name": name, "type": label, **attributes}

        # 若存在额外属性（set_clause 非空），则构造带 SET 子句的 MERGE 语句
        if set_clause:
            # 构造 Cypher 语句：先 MERGE 匹配/创建节点（以 name+type 为唯一约束），再 SET 更新属性
            cypher = f"""
                MERGE (n:{label} {{name: $name, type: $type}})
                SET {set_clause}
            """
        else:
            # 若无额外属性，则仅构造简单的 MERGE 语句，确保节点存在即可
            cypher = f"MERGE (n:{label} {{name: $name, type: $type}})"
        # 通过 Neo4j 客户端执行构造好的 Cypher 语句，传入参数字典
        self.neo4j_client.run_cypher(cypher, parameters)

    # 创建关系方法，在两个已存在节点之间建立有向边
    def create_relation(self, relation):
        """
        在 Neo4j 中两个已存在节点之间创建有向关系（边）。

        作用：
            根据 subject_type、subject 定位起始节点，根据 object_type、object 定位
            目标节点，使用 MERGE 创建一条类型为 relation 的有向边。
            MERGE 保证同一条关系不会重复创建。

        参数：
            relation: dict，关系字典，需包含以下字段：
                - subject: 起始节点名称
                - subject_type: 起始节点类型（label）
                - object: 目标节点名称
                - object_type: 目标节点类型（label）
                - relation: 关系类型（边的类型名）

        返回值：
            无（直接通过 neo4j_client 执行 Cypher）。

        可迁移性说明：
            通用三元组建边逻辑，适用于任何领域的知识图谱构建。
            迁移时需确保两端节点已提前创建（依赖 create_entity 的调用顺序）。
        """
        # 构造 Cypher 语句：先 MATCH 匹配首尾两个节点，再 MERGE 创建有向关系
        # 使用参数化查询（$subject、$object 等）避免注入并提升执行效率
        cypher = f"""
        MATCH (a:{relation['subject_type']} {{name: $subject, type: $subject_type}}),
              (b:{relation['object_type']} {{name: $object, type: $object_type}})
        MERGE (a)-[r:{relation['relation']}]->(b)
        """
        # 构造参数字典，包含起始节点与目标节点的名称和类型
        params = {
            "subject": relation["subject"],          # 起始节点名称
            "object": relation["object"],            # 目标节点名称
            "subject_type": relation["subject_type"], # 起始节点类型
            "object_type": relation["object_type"]    # 目标节点类型
        }
        # 通过 Neo4j 客户端执行关系创建 Cypher 语句
        self.neo4j_client.run_cypher(cypher, params)

    # 从 JSON 文件批量导入数据的主入口方法
    def import_from_json(self, json_path):
        """
        从 JSON 文件批量导入法律实体与关系到 Neo4j。

        作用：
            读取信息抽取阶段产出的 JSON 文件，遍历其中每一条法律文件的抽取结果，
            先创建所有实体节点，再创建所有关系边，完成整个知识图谱的构建。
            导入过程带进度条显示，并对单条异常进行容错处理。

        参数：
            json_path: str，输入 JSON 文件的路径。
                       文件结构应为 {"results": [{"filename":..., "extract_dict": {"entities":[...], "relations":[...]}}]}。

        返回值：
            无（数据直接写入 Neo4j 数据库）。

        可迁移性说明：
            只要输入 JSON 遵循指定结构，该函数可迁移至任意领域的图谱导入任务。
            迁移时可调整进度条描述文案与错误提示以适配业务场景。
        """
        # 以 UTF-8 编码只读打开 JSON 文件，确保中文内容正确解析
        with open(json_path, "r", encoding="utf-8") as f:
            # 使用 json.load 将文件内容解析为 Python 字典对象
            data = json.load(f)

        # 使用 tqdm 遍历 data["results"] 列表，显示"总进度"进度条
        for item in tqdm(data["results"], desc="总进度"):
            # 使用 try-except 包裹单条处理逻辑，保证单条失败不影响整体导入
            try:
                # 取出当前法律文件的抽取字典 extract_dict
                extract_dict = item["extract_dict"]
                # 从抽取字典中获取实体列表 entities
                entities = extract_dict["entities"]
                # 从抽取字典中获取关系列表 relations
                relations = extract_dict["relations"]

                # 遍历当前文件的所有实体，逐个调用 create_entity 写入 Neo4j
                for ent in entities:
                    self.create_entity(ent)

                # 遍历当前文件的所有关系，逐个调用 create_relation 写入 Neo4j
                # 注意：必须先完成所有实体创建，再创建关系，否则 MATCH 可能失败
                for rel in relations:
                    self.create_relation(rel)
            # 捕获处理过程中出现的任意异常，防止单条错误中断整个导入流程
            except Exception as e:
                # 打印出错的法律文件名，便于定位问题数据
                print(f"❌ 错误：{item['filename']}")
                # 打印具体的异常信息，辅助调试
                print(f"❌ 错误：{e}")
                # 使用 continue 跳过当前条目，继续处理下一条
                continue

        # 全部导入完成后，打印成功提示信息
        print("✅ 法律数据已成功导入 Neo4j 数据库！")


# 脚本主入口：当本文件被直接运行（而非被导入）时执行
if __name__ == "__main__":
    # 实例化 LegalGraphImporter，传入全局 neo4j_client 单例作为数据库客户端
    legal_graph_importer = LegalGraphImporter(neo4j_client)
    # 调用 import_from_json 方法，传入抽取结果 JSON 文件的绝对路径，启动导入流程
    legal_graph_importer.import_from_json(get_file_path("__002__extract_information/extract_law_data.json"))
