"""
Neo4j 图数据库统一访问客户端 (单例)
====================================

# ============================================================
# 文件名称: common/neo4j_manager.py
# 文件作用: 封装 Neo4j 数据库的连接、Cypher 执行与元数据导出
# ============================================================
# 【这个文件是干什么的？】
#   本文件是一个"数据库工具箱"。它把对 Neo4j 图数据库的所有常用操作
#   (建连接、执行单条/批量 Cypher、导出图谱结构元数据) 封装进一个
#   Neo4jClient 类, 并在模块底部创建一个全局单例 neo4j_client,
#   供项目中其他脚本(如图谱导入、元数据导出、主流程)直接复用。
#
# 【代码逻辑主线】
#   1. Neo4jClient.__init__   -> 用连接串/账号/密码建立 driver 连接
#   2. Neo4jClient.__del__    -> 对象销毁时关闭连接, 防止连接泄露
#   3. run_cypher             -> 执行"查询类"Cypher, 返回结果列表(dict)
#   4. run_multiple_cypher    -> 在单个事务里批量执行多条写 Cypher(带进度条)
#   5. export_legal_metadata_to_json -> 扫描整库, 把"标签/关系/三元组/属性"
#                                      结构导出成一份 JSON 元数据文件
#   6. get_all_node_names     -> 按标签(或全库)取出所有节点 name, 供下拉/检索用
#   7. 模块末尾: 用配置实例化一个全局 neo4j_client 单例
#
# 【新手建议】
#   1) 先看模块底部的 neo4j_client 单例, 理解"别人怎么用它";
#   2) 再看 run_cypher / run_multiple_cypher, 这是最常用的两个方法;
#   3) export_legal_metadata_to_json 里的 Cypher 较多, 可结合 Neo4j Browser
#      逐条理解"它在问数据库什么问题"。
#
# 📜 代码文字逻辑解析 (what / why / how)
#   WHAT : 我们要让 Python 程序能连上 Neo4j、发 Cypher 语句、并能把整库结构
#          自动盘点成一份 JSON 元数据(供前端/检索模块使用)。
#   WHY  : 项目里至少有 3 个脚本都要操作 Neo4j(导入、导出、主流程)。如果各写
#          各的连接代码, 会出现"账号散落、连接不关闭、Cypher 重复"等问题。
#          因此集中成一个客户端类 + 单例, 统一入口、统一生命周期管理。
#   HOW  : 用官方 neo4j 驱动建立 driver -> session 执行语句; 用 tqdm 为批量写入
#          提供进度可视; 用一组"只查询结构、不改数据"的 Cypher 扫描全库标签、
#          关系类型、节点属性, 汇总成 JSON。所有对外方法都返回 Python 原生结构
#          (list / dict), 上层无需关心 Neo4j 驱动对象。
"""

# 导入 Neo4j 官方 Python 驱动: GraphDatabase 用于创建驱动(driver)并管理连接
from neo4j import GraphDatabase

# 导入项目统一配置类 Config: 从 config/*.yaml 读取 NEO4J_URI / USER / PASSWORD
from common.config import Config

# 导入 tqdm: 在批量执行 Cypher 时打印进度条, 让长时间任务可见可控
from tqdm import tqdm

# 导入 json: 用于将盘点出的图谱元数据写入 .json 文件
import json


# 加载全局配置对象(在导入本模块时即初始化, 后续直接读取连接信息)
conf = Config()


class Neo4jClient:
    """
    Neo4j 客户端类: 负责"连接管理 + 语句执行 + 元数据导出"。

    设计要点:
      - 一个实例持有一个 driver(到数据库的长连接池);
      - 所有读写都通过 session 上下文管理器, 用后自动归还, 避免连接泄露;
      - 对外只暴露高层方法(run_cypher / run_multiple_cypher / 导出),
        上层不需要直接接触 neo4j 驱动的 session/transaction 细节。
    """

    def __init__(self, uri, user, password):
        # 用连接地址与认证信息创建 driver; 后续所有数据库操作都走它
        self.driver = GraphDatabase.driver(uri, auth=(user, password))

    def __del__(self):
        # 对象被垃圾回收时, 若 driver 还存在则关闭连接, 释放网络资源
        if self.driver is not None:
            self.driver.close()

    def run_cypher(self, query, parameters=None):
        # 执行一条 Cypher 查询, 并以"字典列表"形式返回所有结果行
        # 适用: 只读查询(MATCH/RETURN)或单条写操作
        with self.driver.session() as session:
            # 在 session 内运行查询; parameters 为空时传 {} 占位, 防止 None 报错
            result = session.run(query, parameters or {})
            # 把每一条记录转成 dict(键=列名, 值=字段值), 汇总成列表返回
            return [record.data() for record in result]

    def run_multiple_cypher(self, queries_with_params):
        # 在一个写入事务里, 顺序执行"多条"Cypher(每条都带参数)
        # 适用: 批量导入节点/关系, 用事务保证要么全成功、要么整体失败回滚
        with self.driver.session() as session:
            # 定义事务函数: 事务内逐条执行, 并用 tqdm 显示进度
            def transaction_logic(tx):
                # 遍历 [(cypher, params), ...], 进度条标签为"执行 Cypher 语句"
                for query, params in tqdm(queries_with_params, desc="执行 Cypher 语句"):
                    # 在事务 tx 上执行单条语句; params 为空时传 {}
                    tx.run(query, params or {})

            # execute_write 会启动一个写事务并调用上面的逻辑
            session.execute_write(transaction_logic)

    def export_legal_metadata_to_json(self, output_path="legal_metadata.json"):
        # 盘点整库结构(标签、关系类型、三元组、节点/关系属性), 导出为 JSON 元数据
        with self.driver.session() as session:
            # ---- 1) 收集数据库中出现的全部"节点标签" ----
            label_query = """
            MATCH (n)
            UNWIND labels(n) AS label
            RETURN DISTINCT label
            """
            # 执行查询并提取每行的 label 字段, 组成标签列表
            labels = [record["label"] for record in session.run(label_query)]

            # ---- 2) 收集数据库中出现的全部"关系类型" ----
            rel_query = """
            MATCH (n)-[r]-()
            RETURN DISTINCT type(r) AS rel_type
            """
            # 执行查询并提取每行的 rel_type 字段, 组成关系类型列表
            rel_types = [record["rel_type"] for record in session.run(rel_query)]

            # ---- 3) 收集全部"有向三元组" (起点标签 - 关系类型 - 终点标签) ----
            triple_query = """
            MATCH (n)-[r]->(m)
            WITH head(labels(n)) AS from_label, type(r) AS rel_type, head(labels(m)) AS to_label
            RETURN DISTINCT from_label, rel_type, to_label
            """
            # 对每条三元组记录, 组装为 {from, rel_type, to, description} 字典
            # description 暂留空, 供后续人工/自动补充中文释义
            triples = [{
                "from": record["from_label"],
                "rel_type": record["rel_type"],
                "to": record["to_label"],
                "description": ""
            } for record in session.run(triple_query)]

            # ---- 4) 收集"每个标签下有哪些属性名" ----
            label_props = {}
            node_props_query = """
            MATCH (n)
            UNWIND labels(n) AS label
            UNWIND keys(n) AS prop
            RETURN DISTINCT label, prop
            ORDER BY label, prop
            """
            # 遍历结果, 把 (label -> [属性名...]) 组织成嵌套字典, 每个属性带空描述
            for record in session.run(node_props_query):
                label = record["label"]
                prop = record["prop"]
                label_props.setdefault(label, []).append({
                    "name": prop,
                    "description": ""
                })

            # ---- 5) 收集"每个关系类型下有哪些属性名" ----
            rel_type_props = {}
            rel_props_query = """
            MATCH (n)-[r]->(m)
            UNWIND keys(r) AS prop
            RETURN DISTINCT type(r) AS rel_type, prop
            ORDER BY rel_type, prop
            """
            # 遍历结果, 把 (关系类型 -> [属性名...]) 组织成嵌套字典
            for record in session.run(rel_props_query):
                rel_type = record["rel_type"]
                prop = record["prop"]
                rel_type_props.setdefault(rel_type, []).append({
                    "name": prop,
                    "description": ""
                })

            # ---- 6) 组装最终 JSON 对象 ----
            json_obj = {
                # labels: 每个节点标签的元数据(名字 + 属性列表)
                "labels": [
                    {
                        "name": label,
                        "description": "",
                        "properties": label_props.get(label, [])
                    } for label in labels
                ],
                # relationships: 每个关系类型的元数据(名字 + 属性列表)
                "relationships": [
                    {
                        "type": rel,
                        "description": "",
                        "properties": rel_type_props.get(rel, [])
                    } for rel in rel_types
                ],
                # triples: 全部有向三元组(用于前端图谱概览/结构说明)
                "triples": triples
            }

            # ---- 7) 写入 JSON 文件 ----
            # 用 utf-8 编码 + 中文不转义(ensure_ascii=False) + 缩进 2 空格, 方便人读
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(json_obj, f, ensure_ascii=False, indent=2)

            # 返回输出路径, 供调用方打印/进一步使用
            return output_path

    def get_all_node_names(self, label: str = None):
        # 取出某类(或全部)节点的 name 属性, 按字母排序返回
        # 用途: 给前端下拉框、检索联想、调试时快速看有哪些实体
        if label is None:
            # 未指定标签 -> 全库节点, 取 name(过滤掉无 name 的节点)
            query = """
            MATCH (n)
            RETURN DISTINCT n.name AS name
            ORDER BY name
            """
        else:
            # 指定了标签 -> 只取该标签下的节点 name
            query = f"""
            MATCH (n:{label})
            RETURN DISTINCT n.name AS name
            ORDER BY name
            """
        with self.driver.session() as session:
            # 执行查询并过滤掉空 name, 返回排序后的名称列表
            result = session.run(query)
            return [record["name"] for record in result if record["name"]]


# 模块级全局单例: 用配置里的连接信息创建一个 neo4j_client,
# 项目中任何地方 `from common.neo4j_manager import neo4j_client` 即可复用同一连接
neo4j_client = Neo4jClient(conf.NEO4J_URI, conf.NEO4J_USER, conf.NEO4J_PASSWORD)

# 当直接运行本文件(python neo4j_manager.py)时的自测入口:
# 立即导出一份 legal_metadata.json, 验证连接与导出功能是否正常
if __name__ == '__main__':
    neo4j_client.export_legal_metadata_to_json(output_path="legal_metadata.json")
