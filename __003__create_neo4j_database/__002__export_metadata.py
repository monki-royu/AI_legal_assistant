from common.neo4j_client import neo4j_client
from common.path_utils import get_file_path


def export_metadata(output_path: str = None) -> str:
    """
    导出图谱元数据(模式层)到 JSON 文件。

    Parameters
    ----------
    output_path : str | None
        输出路径; 为 None 时默认写到
        __003__create_neo4j_database/legal_metadata.json。

    Returns
    -------
    str : 实际写入的文件路径。
    """
    if not output_path:
        output_path = get_file_path("__003__create_neo4j_database/legal_metadata.json")
    neo4j_client.export_tcm_metadata_to_json(output_path)
    print(f"✅ 法律元数据已导出到 {output_path}")
    return output_path


if __name__ == "__main__":
    export_metadata()
