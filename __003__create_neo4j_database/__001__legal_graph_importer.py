"""
法律知识图谱导入器 (Neo4j)
=======================

# ============================================================
# 文件名称: __003__create_neo4j_database/__001__legal_graph_importer.py
# 文件作用: 把抽取结果 JSON 翻译成 Neo4j 的 Cypher, 批量导入成"节点 + 关系"图
# ============================================================
# 【这个文件是干什么的？】
#   本文件是一个"翻译官 + 装卸工"。它读取 __002__extract_information 产出的抽取 JSON,
#   把里面的实体/关系翻译成一条条 Neo4j 的 Cypher 写语句(MERGE 节点、MERGE 关系),
#   再分批提交到数据库, 最终在 Neo4j 里长出一张法律知识图谱。
#
#   它按"层级"依次创建:
#     (0) 知识源节点 KnowledgeSource(laws/regulations/cases ...)
#     (1) 法律文件/案例节点(Law / Regulation / Case ...)
#     (2) 条款节点 Article(挂在文件下)
#     (3) 语义实体节点(LegalConcept / Action / PartyRole ...)
#     (4) 关系边(DEFINES / REGULATES / CAUSES ...), 并带上溯源属性
#
# 【代码逻辑主线】
#   1. LegalGraphImporter.__init__: 持有一个 Neo4j 客户端与批量大小;
#   2. load_json(): 读抽取结果 JSON;
#   3. _build_source_nodes(): 生成"知识源"节点 Cypher;
#   4. _build_document_nodes(): 生成"法律文件/案例"节点 + 归属知识源的边;
#   5. _build_article_nodes(): 生成"条款"节点 + 归属文件的边;
#   6. _build_entity_nodes(): 生成"语义实体"节点;
#   7. _build_relation_queries(): 生成"关系边"(含溯源属性);
#   8. execute_batch(): 把 Cypher 列表按 batch 分批提交;
#   9. import_from_json(): 串联 3~7, 完成一个 JSON 的导入;
#  10. verify_import(): 跑几条统计 Cypher 验证导入结果。
#
# 【新手建议】
#   1) 先读 import_from_json(): 它是"总调度", 告诉你先建什么后建什么;
#   2) 再看 _build_relation_queries(): 关系边最复杂(要拼起点/终点/溯源属性);
#   3) 所有建节点都优先用 MERGE(存在就更新、不存在才创建), 保证可重复导入不重复;
#   4) 每条 Cypher 都以 (query字符串, params字典) 的元组形式存放, 最后统一提交。
#
# 📜 代码文字逻辑解析 (what / why / how)
#   WHAT : 把"抽取 JSON"变成"图数据库里的节点和边"。
#   WHY  : 抽取阶段产出的是纯文本 JSON, 图数据库看不懂; 必须有人把它"翻译"成 Cypher。
#          用 MERGE 而非 CREATE, 是为了幂等(同一数据导两次不会 duplicated); 用溯源属性,
#          是为了回答"这条关系从哪条、哪个文件来"这种审计问题。
#   HOW  : 每个 _build_* 方法扫描 JSON 的 data['results'], 把需要的节点/关系翻译成
#          (cypher, params) 列表; 用 seen 集合做"同一节点只建一次"的去重; 最后 execute_batch
#          按 batch_size 分批发给 neo4j_client.run_multiple_cypher 提交。
#
# ------------------------------------------------------------------
"""

# 导入 json: 读取抽取结果 JSON
import json

# 导入 os: 路径拼接、判断文件是否存在
import os

# 从 common.neo4j_manager 引入全局 Neo4j 客户端单例
from common.neo4j_manager import neo4j_client

# 从 common.path_utils 引入 get_file_path: 相对路径 -> 绝对路径
from common.path_utils import get_file_path

# 从 tqdm 引入进度条, 分批导入时显示进度
from tqdm import tqdm


# 知识源 -> (文件夹名, 节点标签) 的映射: 决定每个源在图里用哪个标签表示"一篇文件"
SOURCE_CONFIG = {
    "laws": {"folder": "laws", "label": "Law"},
    "regulations": {"folder": "regulations", "label": "Regulation"},
    "interpretations": {"folder": "interpretations", "label": "Interpretation"},
    "industry_sources": {"folder": "industry_sources", "label": "IndustryStandard"},
    "cases": {"folder": "cases", "label": "Case"},
}


# 需要"带溯源属性"的关系类型集合(见上方双层关系架构): 这些关系的边要写入 source_id/file_name/article_no/content
PROVENANCE_RELATIONS = {
    "DEFINES", "REGULATES", "HAS_CONDITION", "HAS_PENALTY",
    "HAS_LIABILITY", "INVOLVES", "CITES",
    "CAUSES", "LEADS_TO", "INCLUDES", "ESTABLISHES", "RELATED_TO", "PERFORMED_BY"
}


class LegalGraphImporter:
    """
    法律知识图谱导入器: 把抽取结果 JSON 翻译成 Neo4j Cypher 并批量导入。

    关键约定:
      - 所有节点创建统一用 MERGE(幂等: 存在则更新属性、不存在才创建);
      - 每个 _build_* 方法返回 [(cypher, params), ...] 列表, 由 execute_batch 统一提交;
      - 用 seen 集合做"同一节点只生成一次 MERGE"的去重, 避免重复语句。
    """

    def __init__(self, client=neo4j_client, batch_size=500):
        # 绑定 Neo4j 客户端(默认用全局单例)
        self.client = client
        # 每次事务提交的 Cypher 条数(batch, 控制内存与事务大小)
        self.batch_size = batch_size

    def load_json(self, file_path):
        # 读取抽取结果 JSON 文件, 返回 dict
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _build_source_nodes(self):
        # 为每个知识源生成一个 KnowledgeSource 节点(保证后续"BELONGS_TO_SOURCE"边有目标)
        queries = []
        # 遍历 SOURCE_CONFIG 中的每个知识源 id
        for source_id in SOURCE_CONFIG:
            # MERGE: 若 (KnowledgeSource {source_id}) 不存在则创建
            query = "MERGE (ks:KnowledgeSource {source_id: $source_id})"
            queries.append((query, {"source_id": source_id}))
        return queries

    def _build_document_nodes(self, data):
        # 为每个"文件/案例"生成一个文档型节点(Law/Regulation/Case...), 并建立它归属知识源的边
        queries = []
        # seen: 记录已生成的 (标签, 文档名), 避免同一文件重复建节点
        seen = set()
        # 遍历每个抽取结果项
        for item in data['results']:
            source_id = item.get('source_id', '')          # 该文件所属知识源
            filename = item.get('filename', '')            # 文件名
            # 查 SOURCE_CONFIG 得到该源对应的"文档节点标签"(默认 Law)
            doc_label = SOURCE_CONFIG.get(source_id, {}).get('label', 'Law')

            # 案例源: 文档名取"案件标题"(从文件头解析); 否则直接取文件名(去扩展名)
            if source_id == 'cases':
                doc_name = self._extract_case_id(filename)
                extra_attrs = self._extract_case_metadata(filename)
                # 案例源: 从抽取记录中读取完整正文, 用于写入 Case 节点的 content 属性
                case_content = item.get('content', '')
            else:
                doc_name = os.path.splitext(filename)[0]
                extra_attrs = {}
                case_content = ''

            # 去重键; 跳过空名与重复
            key = (doc_label, doc_name)
            if key not in seen and doc_name:
                seen.add(key)
                # 基础参数: 节点 name
                params = {"name": doc_name}
                # 用 SET 子句收集要写入的额外属性
                set_parts = []
                if source_id:
                    set_parts.append("n.source_id = $source_id")   # 写入所属知识源
                    params["source_id"] = source_id
                # 写入案例的额外属性(标题/案由/法院/日期), 跳过空值
                for k, v in extra_attrs.items():
                    if v is not None and v != '':
                        set_parts.append(f"n.{k} = ${k}")
                        params[k] = v
                # 案例源: 把正文 content 写入 Case 节点(供前端展示/检索用)
                if case_content:
                    set_parts.append("n.content = $content")
                    params["content"] = case_content

                # 拼出最终的 MERGE 语句(有属性则追加 SET)
                set_clause = " SET " + ", ".join(set_parts) if set_parts else ""
                query = f"MERGE (n:{doc_label} {{name: $name}}){set_clause}"
                queries.append((query, params))

                # 建立 (文档节点)-[:BELONGS_TO_SOURCE]->(知识源) 的归属边
                ks_query = (
                    f"MATCH (n:{doc_label} {{name: $name}}) "
                    f"MATCH (ks:KnowledgeSource {{source_id: $source_id}}) "
                    f"MERGE (n)-[:BELONGS_TO_SOURCE]->(ks)"
                )
                ks_params = {"name": doc_name, "source_id": source_id}
                queries.append((ks_query, ks_params))

        return queries

    def _extract_case_id(self, filename):
        # 从案例 txt 文件头读 "# 案件标题: xxx" 作为案例名(读不到则退用文件名)
        file_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "data", "cases", filename
        )
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('# 案件标题:'):
                        return line.replace('# 案件标题:', '').strip()
        except Exception:
            # 文件不存在 / 读取异常 -> 忽略, 走下方兜底
            pass
        # 兜底: 用文件名(去扩展名)作为案例名
        return os.path.splitext(filename)[0]

    def _extract_case_metadata(self, filename):
        # 从案例 txt 文件头解析结构化元信息(标题/案由/法院/日期), 用于丰富 Case 节点属性
        file_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "data", "cases", filename
        )
        meta = {}
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('# 案件标题:'):
                        meta['title'] = line.replace('# 案件标题:', '').strip()
                    elif line.startswith('# 案由:'):
                        meta['cause'] = line.replace('# 案由:', '').strip()
                    elif line.startswith('# 审理法院:'):
                        meta['court'] = line.replace('# 审理法院:', '').strip()
                    elif line.startswith('# 裁判日期:'):
                        meta['judge_date'] = line.replace('# 裁判日期:', '').strip()
        except Exception:
            pass
        return meta

    def _build_article_nodes(self, data):
        # 为"条款"生成 Article 节点(从实体里的 Article 类型, 以及关系里锚定的 Article), 并挂到所属文件下
        queries = []
        # seen: 去重键 (条款号, 文件名, 知识源)
        seen = set()
        # 遍历每个抽取结果项
        for item in data['results']:
            source_id = item.get('source_id', '')
            extract_dict = item.get('extract_dict', {})
            entities = extract_dict.get('entities', [])
            relations = extract_dict.get('relations', [])
            filename = item.get('filename', '')

            # 案例源文档名用案件标题, 其它用文件名(去扩展名)
            if source_id == 'cases':
                doc_name = self._extract_case_id(filename)
            else:
                doc_name = os.path.splitext(filename)[0]

            # ---- 第一遍: 从实体列表里找 Article 类型的实体, 建条款节点 ----
            for entity in entities:
                ent_type = entity.get('type', '')
                ent_name = entity.get('name', '')
                # 只处理 Article 实体
                if ent_type == 'Article':
                    attrs = entity.get('attributes', {}) or {}
                    file_name = attrs.get('file_name', '') or doc_name
                    content = attrs.get('content', '')
                    article_no = ent_name

                    # 去重: 同一 (条款号, 文件, 源) 只建一次
                    key = (article_no, file_name, source_id)
                    if key not in seen:
                        seen.add(key)
                        # 条款节点基础属性
                        params = {
                            "name": article_no,
                            "file_name": file_name,
                            "source_id": source_id,
                        }
                        # 默认 SET 文件来源信息
                        set_clause = " SET n.file_name = $file_name, n.source_id = $source_id"
                        if content:
                            set_clause += ", n.content = $content"
                            params["content"] = content
                        # MERGE 条款节点
                        query = (
                            f"MERGE (n:Article {{name: $name, file_name: $file_name, "
                            f"source_id: $source_id}}){set_clause}"
                        )
                        queries.append((query, params))

                        # 建立 (条款)-[:BELONGS_TO]->(所属文件) 边
                        doc_label = SOURCE_CONFIG.get(source_id, {}).get('label', 'Law')
                        belongs_query = (
                            f"MATCH (a:Article {{name: $name, file_name: $file_name}}) "
                            f"MATCH (d:{doc_label} {{name: $doc_name}}) "
                            f"MERGE (a)-[:BELONGS_TO]->(d)"
                        )
                        belongs_params = {"name": article_no, "file_name": file_name, "doc_name": doc_name}
                        queries.append((belongs_query, belongs_params))

            # ---- 第二遍: 从关系列表里找"以 Article 为起点"的锚定关系, 补建可能缺失的条款节点 ----
            # 将 ARTICLE_ANCHORED_RELATIONS 定义移入 for item 循环体内, 确保每个 item 的关系都被遍历
            ARTICLE_ANCHORED_RELATIONS = {
                'DEFINES', 'REGULATES', 'HAS_CONDITION', 'HAS_PENALTY',
                'HAS_LIABILITY', 'INVOLVES', 'CITES'
            }

            for rel in relations:
                rel_type = rel.get('relation', '')
                # 只处理"条款锚定层"关系
                if rel_type in ARTICLE_ANCHORED_RELATIONS:
                    subject = rel.get('subject', '')
                    subj_type = rel.get('subject_type', '')
                    provenance = rel.get('provenance', {}) or {}
                    file_name = provenance.get('file_name', '') or doc_name
                    # 仅当起点是 Article 且有文件/名称时才建节点
                    if subj_type == 'Article' and file_name and subject:
                        key = (subject, file_name, source_id)
                        if key not in seen:
                            seen.add(key)
                            params = {
                                "name": subject,
                                "file_name": file_name,
                                "source_id": source_id,
                            }
                            set_clause = " SET n.file_name = $file_name, n.source_id = $source_id"
                            content = provenance.get('content', '')
                            if content:
                                set_clause += ", n.content = $content"
                                params["content"] = content
                            # MERGE 条款节点(同上结构)
                            query = (
                                f"MERGE (n:Article {{name: $name, file_name: $file_name, "
                                f"source_id: $source_id}}){set_clause}"
                            )
                            queries.append((query, params))

                            # 建立 (条款)-[:BELONGS_TO]->(所属文件) 边
                            doc_label = SOURCE_CONFIG.get(source_id, {}).get('label', 'Law')
                            belongs_query = (
                                f"MATCH (a:Article {{name: $name, file_name: $file_name}}) "
                                f"MATCH (d:{doc_label} {{name: $doc_name}}) "
                                f"MERGE (a)-[:BELONGS_TO]->(d)"
                            )
                            belongs_params = {"name": subject, "file_name": file_name, "doc_name": doc_name}
                            queries.append((belongs_query, belongs_params))

        return queries

    def _build_entity_nodes(self, data):
        # 为"语义实体"生成节点(LegalConcept/Action/PartyRole ...), 跳过结构型节点与 Article
        queries = []
        # seen: 以实体名为 key, 已收集的类型集合为 value (同名异构实体合并策略)
        seen = {}
        # 需要建节点的语义实体类型(排除 Law/Regulation/Case 等"文档型"与 Article)
        semantic_types = {'LegalConcept', 'PartyRole', 'Action', 'Condition', 'Penalty', 'Liability'}

        # 第一遍: 收集所有实体, 按名称分组, 累积类型标签
        for item in data['results']:
            extract_dict = item.get('extract_dict', {})
            entities = extract_dict.get('entities', [])

            for entity in entities:
                ent_type = entity.get('type', '')
                ent_name = entity.get('name', '')
                # 跳过非目标类型、跳过 Article(条款节点由 _build_article_nodes 负责)
                if ent_type not in semantic_types or not ent_name:
                    continue
                # 累积类型到 name -> types 映射
                if ent_name not in seen:
                    seen[ent_name] = {'types': set(), 'attrs': entity.get('attributes', {}) or {}}
                seen[ent_name]['types'].add(ent_type)

        # 第二遍: 为每个唯一名称创建一个节点, 带上所有累积的标签
        for ent_name, info in seen.items():
            types = info['types']
            attrs = info['attrs']
            params = {"name": ent_name}
            set_parts = []
            # 把属性写进节点, 但排除 file_name/source_id/content(这些用于溯源, 不挂在实体上)
            for k, v in attrs.items():
                if v is not None and v != '' and k not in ('file_name', 'source_id', 'content'):
                    set_parts.append(f"n.{k} = ${k}")
                    params[k] = v

            # 组装 SET 子句(属性 + 所有类型标签)
            label_clause = "".join(f":{t}" for t in sorted(types))
            # SET 标签用 n:Type, 多个类型用冒号连接
            if len(types) > 1:
                set_parts.insert(0, f"n:{':'.join(sorted(types))}")
            elif types:
                set_parts.insert(0, f"n:{next(iter(types))}")

            if set_parts:
                set_clause = " SET " + ", ".join(set_parts)
            else:
                set_clause = ""

            # MERGE 实体节点: 按 name 唯一匹配, 再 SET 标签(支持多标签合并)
            query = f"MERGE (n {{name: $name}}){set_clause}"
            queries.append((query, params))

        return queries

    def _build_relation_queries(self, data):
        # 生成所有"关系边"的 Cypher(含溯源属性), 这是最复杂的一步
        queries = []
        # 遍历每个抽取结果项
        for item in data['results']:
            source_id = item.get('source_id', '')
            extract_dict = item.get('extract_dict', {})
            relations = extract_dict.get('relations', [])
            filename = item.get('filename', '')

            # 遍历每条关系
            for rel in relations:
                subject = rel.get('subject', '')            # 起点名
                subject_type = rel.get('subject_type', '')  # 起点类型
                relation = rel.get('relation', '')          # 关系类型
                object_name = rel.get('object', '')         # 终点名
                object_type = rel.get('object_type', '')    # 终点类型
                provenance = rel.get('provenance', {}) or {}  # 溯源信息

                # 起点或终点缺失则跳过(无法建边)
                if not subject or not object_name:
                    continue

                # 公共参数: 起点名、终点名
                params = {"subject": subject, "object": object_name}

                # 根据起点类型拼"起点匹配模式"; Article 需同时用 file_name 唯一定位
                if subject_type == 'Article':
                    file_name = provenance.get('file_name', '') or filename.replace('.txt', '')
                    params["file_name"] = file_name
                    subject_match = f"(a:Article {{name: $subject, file_name: $file_name}})"
                else:
                    # 非 Article 实体: 按 name 匹配(不再按 type+name, 支持同名异构实体合并)
                    subject_match = f"(a {{name: $subject}})"

                # 根据终点类型拼"终点匹配模式"; Article 同理
                if object_type == 'Article':
                    obj_file_name = provenance.get('file_name', '') or filename.replace('.txt', '')
                    params["obj_file_name"] = obj_file_name
                    object_match = f"(b:Article {{name: $object, file_name: $obj_file_name}})"
                else:
                    # 非 Article 实体: 按 name 匹配(支持同名异构实体合并)
                    object_match = f"(b {{name: $object}})"

                # 若该关系属于"需溯源"类型, 把 provenance 里的字段写进边属性
                if relation in PROVENANCE_RELATIONS:
                    prov_params = {}
                    set_props = []
                    # 依次尝试写入 source_id / file_name / article_no / content
                    if source_id:
                        prov_params["source_id"] = source_id
                        set_props.append("r.source_id = $source_id")
                    if provenance.get('file_name'):
                        prov_params["file_name"] = provenance['file_name']
                        set_props.append("r.file_name = $file_name")
                    if provenance.get('article_no'):
                        prov_params["article_no"] = provenance['article_no']
                        set_props.append("r.article_no = $article_no")
                    # content 仅保留在 Article 节点上 (每个条款只存一份, 不膨胀)
                    # 关系边不再复制 content, 避免每条边都携带全文导致 15x 存储膨胀
                    # 若需查询原文, 通过 r.file_name + r.article_no → Article 节点获取

                    # 把溯源参数并入总参数
                    if prov_params:
                        params.update(prov_params)

                    # 组装带溯源属性的 MERGE 边
                    # 注意: 必须给关系变量名 r, 否则 SET r.xxx 会报 "Variable r not defined"
                    set_clause = " SET " + ", ".join(set_props) if set_props else ""
                    query = (
                        f"MATCH {subject_match} "
                        f"MATCH {object_match} "
                        f"MERGE (a)-[r:{relation}]->(b){set_clause}"
                    )
                else:
                    # 非溯源关系: 仅建立边, 不写溯源属性
                    query = (
                        f"MATCH {subject_match} "
                        f"MATCH {object_match} "
                        f"MERGE (a)-[:{relation}]->(b)"
                    )

                queries.append((query, params))

        return queries

    def execute_batch(self, queries, desc=""):
        # 把 Cypher 列表按 batch_size 分批, 逐批提交(带进度条)
        if not queries:
            return
        # 以 batch_size 为步长切片, 每批交给客户端在一个事务里执行
        for i in tqdm(range(0, len(queries), self.batch_size), desc=desc):
            batch = queries[i:i + self.batch_size]
            self.client.run_multiple_cypher(batch)

    def import_from_json(self, file_path, label=""):
        # 导入总调度: 读取一个抽取 JSON, 依次建各层节点与关系边
        print(f"\n{'=' * 50}")
        print(f"开始导入: {label}")
        print(f"文件: {file_path}")

        # 文件不存在则跳过
        if not os.path.exists(file_path):
            print(f"⚠️ 文件不存在, 跳过: {file_path}")
            return

        # 读取 JSON
        data = self.load_json(file_path)
        results = data.get('results', [])
        if not results:
            print(f"⚠️ 无数据, 跳过: {file_path}")
            return

        print(f"  数据条数: {len(results)}")

        # 第 0 层: 知识源节点
        print("  创建知识源节点...")
        source_queries = self._build_source_nodes()
        self.execute_batch(source_queries, desc="创建知识源")

        # 第 1 层: 法律文件/案例节点
        print("  创建法律文件节点...")
        doc_queries = self._build_document_nodes(data)
        self.execute_batch(doc_queries, desc="创建法律文件")

        # 第 2 层: 条款节点
        print("  创建条款节点...")
        article_queries = self._build_article_nodes(data)
        self.execute_batch(article_queries, desc="创建条款")

        # 第 3 层: 语义实体节点
        print("  创建实体节点...")
        entity_queries = self._build_entity_nodes(data)
        self.execute_batch(entity_queries, desc="创建实体")

        # 第 4 层: 关系边
        print("  创建关系边...")
        relation_queries = self._build_relation_queries(data)
        self.execute_batch(relation_queries, desc="创建关系")

        print(f"  ✅ 导入完成: {label}")

    def verify_import(self):
        # 导入后用几条统计/示例 Cypher 验证成果
        print(f"\n{'=' * 50}")
        print("导入结果验证:")

        # 统计各类型节点数量
        result = self.client.run_cypher(
            "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS count ORDER BY count DESC"
        )
        print("各类型节点数量:")
        for r in result:
            print(f"  {r['label']}: {r['count']}")

        # 统计各类型关系数量
        result = self.client.run_cypher(
            "MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS count ORDER BY count DESC"
        )
        print("各类型关系数量:")
        for r in result:
            print(f"  {r['type']}: {r['count']}")

        # 示例: DEFINES 关系的溯源(概念 <- 文件/源)
        result = self.client.run_cypher(
            "MATCH (c:LegalConcept)<-[r:DEFINES]-(a:Article) "
            "RETURN c.name AS concept, r.source_id AS source, r.file_name AS file, r.article_no AS article "
            "LIMIT 10"
        )
        print("溯源查询示例 (DEFINES 关系):")
        for r in result:
            print(f"  [{r['concept']}] ← {r['source']}/{r['file']}/{r['article']}")

        # 示例: 民法典条款 -> 概念 映射
        result = self.client.run_cypher(
            "MATCH (a:Article {file_name: $fname})-[:DEFINES]->(c:LegalConcept) "
            "RETURN a.name AS article, c.name AS concept LIMIT 10",
            {"fname": "中华人民共和国民法典"}
        )
        print("民法典条款→概念映射示例:")
        for r in result:
            print(f"  {r['article']} → {r['concept']}")

        # 示例: 责任类型溯源(HAS_LIABILITY)
        result = self.client.run_cypher(
            "MATCH (a:Article)-[:HAS_LIABILITY]->(l:Liability) "
            "RETURN a.file_name AS file, a.name AS article, l.name AS liability "
            "LIMIT 10"
        )
        print("责任类型溯源示例 (HAS_LIABILITY):")
        for r in result:
            print(f"  {r['file']} {r['article']} → {r['liability']}")

        # 示例: 高频角色实体 TOP10
        result = self.client.run_cypher(
            "MATCH (a:Article)-[:INVOLVES]->(p:PartyRole) "
            "RETURN p.name AS party, count(*) AS cnt ORDER BY cnt DESC LIMIT 10"
        )
        print("高频角色实体 TOP10:")
        for r in result:
            print(f"  {r['party']}: {r['cnt']}次")


# 直接运行本文件时的自测入口: 清空库 -> 导入 5 个源 -> 验证
if __name__ == '__main__':
    # 先清空数据库, 保证自测从干净状态开始
    print("清空数据库...")
    neo4j_client.run_cypher("MATCH (n) DETACH DELETE n")
    print("数据库已清空")

    # 创建导入器(每批 500 条)
    importer = LegalGraphImporter(batch_size=500)

    # 解析基目录(项目根/__002__extract_information)
    base_dir = get_file_path('__002__extract_information')
    # 5 个知识源对应的抽取 JSON 与中文标签
    sources = [
        ('extract_law_data.json', '法律法规'),
        ('extract_regulation_data.json', '行政法规'),
        ('extract_interpretation_data.json', '司法解释'),
        ('extract_industry_data.json', '行业标准'),
        ('extract_case_data.json', '裁判案例'),
    ]

    # 逐个文件导入
    for filename, label in sources:
        file_path = os.path.join(base_dir, filename)
        importer.import_from_json(file_path, label)

    # 导入完成后做验证
    importer.verify_import()

    print("\n🎉 全部导入完成!")
