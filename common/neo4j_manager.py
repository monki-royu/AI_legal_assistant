# 📜 代码文字逻辑解析
# 本文件是项目与 Neo4j 图数据库交互的"数据访问层"，封装了连接管理、Cypher 执行、
# 元数据导出、节点名查询和语法校验等能力，供知识图谱构建流程和多智能体图谱问答
# 流程统一调用。核心逻辑：模块加载时实例化 Config 拿到 Neo4j 的 URI/USER/PASSWORD，
# 然后用这三个值创建一个全局共享的 Neo4jClient 单例 neo4j_client，业务代码 import
# 即用。Neo4jClient 内部通过 GraphDatabase.driver 建立驱动，每个方法用 `with
# self.driver.session() as session` 打开短会话执行 Cypher，避免长连接资源占用。
# 关键方法包括：run_cypher 单条查询返回 dict 列表；run_multiple_cypher 用事务
# execute_write 批量执行并显示 tqdm 进度条；export_tcm_metadata_to_json 通过 5 条
# Cypher 抽取图模式层（标签/关系类型/三元组结构/节点属性/关系属性）导出为 JSON，
# 供后续 LLM 生成 Cypher 时作为 schema 参考；get_all_node_names 返回指定标签的
# name 属性列表用于实体召回校验；validate_cypher 用 EXPLAIN 做语法预检。函数关系：
# 本模块依赖 common.config.Config，自身被图谱构建脚本和 LangGraph 问答节点 import。

from neo4j import GraphDatabase                          # 导入官方 neo4j Python 驱动的 GraphDatabase 类，用于创建 driver 连接图数据库
from common.config import Config                         # 导入项目配置类 Config，用于读取 NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD
from tqdm import tqdm                                    # 导入 tqdm 进度条库，用于在批量执行 Cypher 时显示进度
import json                                              # 导入标准库 json，用于把元数据对象序列化为 JSON 字符串写文件

conf = Config()                                          # 实例化 Config，把 Neo4j 连接信息加载到 conf 属性，供下方创建全局单例使用


class Neo4jClient:
    def __init__(self, uri, user, password):
        """
        初始化 Neo4j 客户端，建立驱动连接。

        作用:
            根据传入的 uri/user/password 创建 GraphDatabase.driver 实例并保存到
            self.driver，后续所有方法通过该 driver 打开 session 执行 Cypher。

        参数:
            uri (str): Neo4j 连接 URI，例如 'bolt://localhost:7687' 或
                'neo4j+s://xxx.databases.neo4j.io'（加密云实例）。
            user (str): 登录用户名，通常为 'neo4j'。
            password (str): 登录密码。

        返回值:
            无返回值；构造完成后 self.driver 即可用于打开 session。

        可迁移性说明:
            该类基于官方 neo4j 驱动，可原样用于任何 Neo4j 项目；如需连接池调优
            可在 driver 创建时传入 max_connection_lifetime 等参数。
        """
        """初始化连接"""
        self.driver = GraphDatabase.driver(uri, auth=(user, password))  # 创建 driver：auth 参数为 (user, password) 元组，driver 内部维护连接池，后续 session 复用连接

    def __del__(self):
        """
        析构时关闭 driver 释放连接池资源。

        作用:
            在对象被垃圾回收时自动调用 driver.close()，关闭底层连接池，避免
            连接泄漏。

        参数:
            无。

        返回值:
            无返回值。

        可迁移性说明:
            依赖 __del__ 时机不可控，建议在长期运行的服务中显式调用 close；
            该析构仅作为兜底保险。
        """
        """关闭连接"""
        if self.driver is not None:                       # 防御性判断：若 driver 因初始化失败而为 None 则跳过
            self.driver.close()                           # 关闭 driver 释放所有连接，避免连接泄漏

    def run_cypher(self, query, parameters=None):
        """
        执行一条 Cypher 语句并返回结果。

        作用:
            打开一个短 session 执行 query，把结果集中每条 record 转成 dict
            返回，方便上层直接 JSON 序列化或迭代处理。

        参数:
            query (str): Cypher 查询语句，可包含 $param 占位符。
            parameters (dict, optional): 参数字典，与 query 中的 $param 一一
                对应；为 None 时传空 dict。

        返回值:
            list[dict]: 结果列表，每个元素是一条记录的 dict 视图（record.data()）；
                无结果时返回空列表。

        可迁移性说明:
            该方法适用于任何只读或单条写 Cypher；批量写推荐用 run_multiple_cypher
            以事务方式执行保证原子性。
        """
        """
        执行一条 Cypher 语句并返回结果
        :param query: Cypher 查询语句
        :param parameters: 可选参数字典
        :return: 查询结果列表（每一行是一个 dict）
        """
        with self.driver.session() as session:            # 打开一个短会话，with 块结束自动关闭，归还连接到池
            result = session.run(query, parameters or {})  # 执行 Cypher；parameters 为 None 时用空 dict，避免传 None 报错
            return [record.data() for record in result]   # 遍历结果游标，把每条 record 转成 dict 收集到列表返回；record.data() 把点/关系的属性展平为 dict

    def run_multiple_cypher(self, queries_with_params):
        """
        以事务方式批量执行多条 Cypher 语句，并显示进度条。

        作用:
            把多条写操作放进同一个托管事务（execute_write）中执行，要么全部成功
            要么全部回滚，保证原子性；tqdm 让用户直观看到批量进度。

        参数:
            queries_with_params (List[Tuple[str, Dict]]): 形如
                [("CREATE (n:Test {name: $name})", {"name": "Alice"}), ...]
                的列表，每个元组是 (cypher, params)。

        返回值:
            无返回值；事务成功提交后所有写操作生效，失败则整体回滚并抛异常。

        可迁移性说明:
            适用于批量建图、批量插入节点/关系；若单条失败需要部分提交，可改为
            逐条 run_cypher 但失去原子性。
        """
        """
        执行多条 Cypher 语句，使用事务，并显示 tqdm 进度条。

        参数:
            queries_with_params: List[Tuple[str, Dict]]
                形式如: [("CREATE (n:Test {name: $name})", {"name": "Alice"}), ...]
        """
        with self.driver.session() as session:            # 打开会话

            def transaction_logic(tx):                    # 定义事务体函数，由 execute_write 在托管事务中调用，tx 为 Transaction 对象
                for query, params in tqdm(queries_with_params, desc="执行 Cypher 语句"):  # 用 tqdm 包裹迭代器，显示"执行 Cypher 语句"进度条
                    tx.run(query, params or {})           # 在当前事务内执行每条 Cypher；params 为 None 时用空 dict

            session.execute_write(transaction_logic)      # 以托管写事务方式执行 transaction_logic，全部成功才提交，任一失败回滚

    def export_tcm_metadata_to_json(self, output_path="tcm_metadata.json"):
        """
        导出当前 Neo4j 图的模式层元数据为 JSON 文件。

        作用:
            通过 5 条 Cypher 依次抽取：所有节点标签、所有关系类型、所有三元组
            结构（from_label, rel_type, to_label）、每种标签的节点属性键、每种
            关系类型的属性键，组装成 {labels, relationships, triples} 三段式 JSON
            写入文件。该 JSON 作为"图谱 schema"喂给 LLM，用于辅助生成合法 Cypher。

        参数:
            output_path (str): 输出 JSON 文件路径，默认 'tcm_metadata.json'。

        返回值:
            str: 实际写入的 output_path，便于调用方知道文件位置。

        可迁移性说明:
            该方法与具体业务无关，可对任何 Neo4j 图导出模式层；导出格式与
            legal_metadata.json / tcm_metadata.json 一致，便于跨项目复用。
        """
        with self.driver.session() as session:            # 打开会话，本次导出全部在该会话内完成

            # 1. 所有节点标签
            label_query = """
               MATCH (n)
               UNWIND labels(n) AS label
               RETURN DISTINCT label
               """                                         # Cypher：匹配所有节点 n，用 UNWIND 把节点的标签数组展开成多行，再 DISTINCT 去重，得到全图所有标签
            labels = [record["label"] for record in session.run(label_query)]  # 执行查询并把每条记录的 "label" 字段收集到列表

            # 2. 所有关系类型
            rel_query = """
               MATCH (n)-[r]-()
               RETURN DISTINCT type(r) AS rel_type
               """                                         # Cypher：匹配任意有关系的节点对，type(r) 取关系类型，DISTINCT 去重
            rel_types = [record["rel_type"] for record in session.run(rel_query)]  # 收集所有关系类型到列表

            # 3. 所有三元组结构
            triple_query = """
               MATCH (n)-[r]->(m)
               WITH head(labels(n)) AS from_label, type(r) AS rel_type, head(labels(m)) AS to_label
               RETURN DISTINCT from_label, rel_type, to_label
               """                                         # Cypher：匹配有向关系 n-r->m，head(labels(n)) 取起点第一个标签，type(r) 关系类型，head(labels(m)) 终点第一个标签，DISTINCT 得到所有 (起点标签, 关系, 终点标签) 三元组结构
            triples = [{
                "from": record["from_label"],             # 起点标签
                "rel_type": record["rel_type"],           # 关系类型
                "to": record["to_label"],                 # 终点标签
                "description": ""                         # 预留描述字段，留给人工或 LLM 后续填写语义说明
            } for record in session.run(triple_query)]    # 把每条记录构造成 dict 加入列表

            # 4. 节点属性（每个标签下的属性键）
            node_props_query = """
               MATCH (n)
               UNWIND labels(n) AS label
               UNWIND keys(n) AS prop
               RETURN DISTINCT label, prop
               ORDER BY label, prop
               """                                         # Cypher：对每个节点展开其所有标签和所有属性键，DISTINCT 得到 (标签, 属性键) 对，按标签和属性键排序
            label_props = {}                               # 临时存储 {标签: [属性 dict 列表]}
            for record in session.run(node_props_query):  # 遍历每条 (label, prop) 记录
                label = record["label"]                   # 取标签
                prop = record["prop"]                     # 取属性键
                if prop == "project":  # 忽略 project 字段  # 跳过 project 字段（项目内部用的隔离字段，不暴露给 LLM schema）
                    continue                              # 不加入属性列表
                label_props.setdefault(label, []).append({  # 把属性键加到该标签的列表里，setdefault 自动初始化空列表
                    "name": prop,                         # 属性键名
                    "description": ""                     # 预留描述字段
                })

            # 5. 关系属性（每种关系下的属性键）
            rel_props_query = """
               MATCH (n)-[r]->(m)
               UNWIND keys(r) AS prop
               RETURN DISTINCT type(r) AS rel_type, prop
               ORDER BY rel_type, prop
               """                                         # Cypher：对每条有向关系展开其所有属性键，DISTINCT 得到 (关系类型, 属性键) 对，排序
            rel_type_props = {}                            # 临时存储 {关系类型: [属性 dict 列表]}
            for record in session.run(rel_props_query):   # 遍历每条 (rel_type, prop) 记录
                rel_type = record["rel_type"]             # 取关系类型
                prop = record["prop"]                     # 取属性键
                rel_type_props.setdefault(rel_type, []).append({  # 把属性键加到该关系类型的列表里
                    "name": prop,                         # 属性键名
                    "description": ""                     # 预留描述字段
                })

            # 构建 JSON
            json_obj = {
                "labels": [
                    {
                        "name": label,                    # 标签名
                        "description": "",                 # 标签描述，留给人工填写
                        "properties": label_props.get(label, [])  # 该标签下的所有属性 dict 列表，若无则为空列表
                    } for label in labels                 # 遍历第1步收集的所有标签
                ],
                "relationships": [
                    {
                        "type": rel,                      # 关系类型名
                        "description": "",                 # 关系描述，留给人工填写
                        "properties": rel_type_props.get(rel, [])  # 该关系类型下的所有属性 dict 列表
                    } for rel in rel_types                # 遍历第2步收集的所有关系类型
                ],
                "triples": triples                        # 第3步收集的三元组结构列表
            }

            # 保存到文件
            with open(output_path, "w", encoding="utf-8") as f:  # 以 UTF-8 编码写文件，避免中文乱码
                json.dump(json_obj, f, ensure_ascii=False, indent=2)  # 序列化为 JSON 写入文件；ensure_ascii=False 保留中文不转义，indent=2 缩进2空格便于人读

            return output_path                            # 返回输出路径，调用方可据此定位文件

    def get_all_node_names(self, label: str = None):
        """
        获取指定标签下所有节点的 name 属性。

        作用:
            返回图中（或某标签下）所有节点的 name 属性列表，常用于实体召回后
            校验 LLM 输出的实体名是否真实存在于图谱中。

        参数:
            label (str, optional): 节点标签，如 'Effect'、'Symptom'、'Disease'；
                为 None 时查询全图所有节点。

        返回值:
            List[str]: name 属性列表，按字母序升序；过滤掉 name 为 None 的节点。

        可迁移性说明:
            假设节点都有 name 属性；若其他图谱用 title 或 id 作为主键，需调整
            Cypher 中的属性名。
        """
        """
        获取指定标签下所有节点的 name 属性
        :param label: 节点标签（如 'Effect', 'Symptom', 'Disease'）
        :return: List[str]
        """
        if label is None:                                 # 未指定标签，查全图
            query = """
            MATCH (n)
            RETURN DISTINCT n.name AS name
            ORDER BY name
            """                                            # Cypher：匹配所有节点，返回去重的 name，按 name 升序
        else:                                              # 指定了标签
            query = f"""
            MATCH (n:{label})
            RETURN DISTINCT n.name AS name
            ORDER BY name
            """                                            # Cypher：用 f-string 把标签插入 MATCH (n:标签)，仅查该标签节点；注意 label 来自代码内部不受用户输入控制，不存在注入风险
        with self.driver.session() as session:            # 打开会话
            result = session.run(query)                   # 执行查询
            return [record["name"] for record in result if record["name"]]  # 收集 name 字段，过滤掉 None/空值

    def validate_cypher(self, query: str) -> bool:
        """
        检测 Cypher 语句语法是否合法（不实际执行）。

        作用:
            在 Cypher 前加 EXPLAIN 关键字让 Neo4j 仅做语法解析和执行计划生成，
            不真正执行写操作，用于在执行 LLM 生成的 Cypher 前预检语法。

        参数:
            query (str): 待检测的 Cypher 语句。

        返回值:
            bool: True 表示语法合法，False 表示语法错误（同时打印错误信息）。

        可迁移性说明:
            EXPLAIN 不执行任何写操作，安全可重入；可对任意 Cypher 做语法预检。
        """
        """
        检测 Cypher 查询语句是否合法（语法层面）
        :param query: 待检测的 Cypher 语句
        :return: True 表示合法，False 表示不合法
        """
        try:
            with self.driver.session() as session:        # 打开会话
                # 使用 EXPLAIN 只做解析，不执行
                session.run(f"EXPLAIN {query}")           # 在 query 前拼接 EXPLAIN，让 Neo4j 只解析不执行；语法错误会抛 CypherSyntaxError
            return True                                   # 未抛异常即语法合法
        except Exception as e:                            # 捕获语法错误等异常
            print(f"Cypher 语法错误: {e}")                # 打印错误信息便于调试 LLM 生成的 Cypher
            return False                                  # 返回 False 表示语法不合法

neo4j_client=Neo4jClient(conf.NEO4J_URI, conf.NEO4J_USER, conf.NEO4J_PASSWORD)  # 模块级单例：用 conf 中的连接信息创建全局共享的 Neo4jClient，业务代码 `from common.neo4j_manager import neo4j_client` 即可使用
