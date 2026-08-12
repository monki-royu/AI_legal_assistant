from common.neo4j_manager import neo4j_client
from common.path_utils import get_file_path


if __name__ == "__main__":
    output_path = get_file_path("__003__create_neo4j_database/legal_metadata.json")
    neo4j_client.export_tcm_metadata_to_json(output_path)
    print(f"✅ 法律元数据已导出到 {output_path}")
