import json

from common.neo4j_manager import neo4j_client
from common.path_utils import get_file_path
from tqdm import tqdm


class LegalGraphImporter:
    def __init__(self, neo4j_client):
        self.neo4j_client = neo4j_client

    def create_entity(self, entity):
        label = entity["type"]
        name = entity["name"]
        attributes = entity.get("attributes", {}) or {}

        set_clause = ", ".join([f"n.{k} = ${k}" for k in attributes.keys()])
        parameters = {"name": name, "type": label, **attributes}

        if set_clause:
            cypher = f"""
                MERGE (n:{label} {{name: $name, type: $type}})
                SET {set_clause}
            """
        else:
            cypher = f"MERGE (n:{label} {{name: $name, type: $type}})"
        self.neo4j_client.run_cypher(cypher, parameters)

    def create_relation(self, relation):
        cypher = f"""
        MATCH (a:{relation['subject_type']} {{name: $subject, type: $subject_type}}),
              (b:{relation['object_type']} {{name: $object, type: $object_type}})
        MERGE (a)-[r:{relation['relation']}]->(b)
        """
        params = {
            "subject": relation["subject"],
            "object": relation["object"],
            "subject_type": relation["subject_type"],
            "object_type": relation["object_type"]
        }
        self.neo4j_client.run_cypher(cypher, params)

    def import_from_json(self, json_path):
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        for item in tqdm(data["results"], desc="总进度"):
            try:
                extract_dict = item["extract_dict"]
                entities = extract_dict["entities"]
                relations = extract_dict["relations"]

                for ent in entities:
                    self.create_entity(ent)

                for rel in relations:
                    self.create_relation(rel)
            except Exception as e:
                print(f"❌ 错误：{item['filename']}")
                print(f"❌ 错误：{e}")
                continue

        print("✅ 法律数据已成功导入 Neo4j 数据库！")


if __name__ == "__main__":
    legal_graph_importer = LegalGraphImporter(neo4j_client)
    legal_graph_importer.import_from_json(get_file_path("__002__extract_information/extract_law_data.json"))
