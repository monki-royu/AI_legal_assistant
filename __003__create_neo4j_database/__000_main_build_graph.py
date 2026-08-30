"""
法律知识图谱构建主流程
=====================

# ============================================================
# 文件名称: __003__create_neo4j_database/__000_main_build_graph.py
# 文件作用: 把"抽取 -> 导入 -> 导出 -> 验证 -> 建向量索引"串成一条可一键运行的主流程
# ============================================================
# 【这个文件是干什么的？】
#   本文件是"建图谱的总指挥"。它把前面几个脚本(抽取、导入、导出元数据、建 FAISS 索引)
#   编排成一个有序流程, 并支持用命令行参数灵活跳过某些步骤(如复用已有抽取结果、只重建索引)。
#   类比:
#     中医: txt文件 → LLM抽取 → JSON → Neo4j导入 → 元数据导出
#     法律: 5种txt文件夹 → 解析+LLM抽取 → JSON → Neo4j导入 → 元数据导出 → FAISS索引
#
# 【代码逻辑主线】
#   Step 0: step0_clear_database()   清空 Neo4j(可选)
#   Step 1: step1_extract()          从 5 个知识源抽取实体/关系/属性(LLM)
#   Step 2: step2_import()           把抽取 JSON 导入 Neo4j 图谱
#   Step 3: step3_export_metadata()  导出图谱结构元数据 JSON
#   Step 4: step4_verify()           统计节点/关系数量, 抽查溯源与实体间关系
#   Step 5: step5_build_faiss()      构建 FAISS 向量索引
#   main(): 解析命令行参数, 按开关依次调用上述步骤
#
# 【新手建议】
#   1) 直接看 main() 的 argparse 部分, 理解有哪些开关(--skip-* / --max-files);
#   2) 再看每个 step* 函数, 它们都很短, 各自只做一件事;
#   3) 各 step 实际是"调用别的文件里的函数", 真正的重活在 __001__/__002__/__003__ 里。
#
# 📜 代码文字逻辑解析 (what / why / how)
#   WHAT : 一键把法律 txt 知识源变成"Neo4j 图谱 + 元数据 JSON + 向量索引"三件套。
#   WHY  : 抽取/导入/导出/索引是独立脚本, 手动依次跑容易漏步骤、顺序错。主流程把它们编排好,
#          并用命令行开关实现"可断点续跑"(比如只改了索引逻辑, 就不必重抽重导)。
#   HOW  : 每个 step 函数用延迟导入(from ... import)只在用到时才载入对应模块(避免未安装依赖时报错);
#          通过 argparse 接收 --skip-* 开关决定哪些步骤执行; 最后顺序串起 Step0~5。
# ------------------------------------------------------------------
"""

# 导入 os: 路径拼接、判断目录是否存在
import os

# 导入 sys: 将项目根目录加入 sys.path, 确保 common 等自定义包可被导入
import sys

# 将项目根目录(本文件向上两级)加入 sys.path
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


# 从 common.neo4j_manager 引入全局 Neo4j 客户端单例(供清空/导出/验证用)
from common.neo4j_manager import neo4j_client

# 从 common.path_utils 引入 get_file_path: 相对项目根的路径 -> 绝对路径
from common.path_utils import get_file_path


def step0_clear_database():
    # Step 0: 清空整个 Neo4j 数据库(删除所有节点与关系, 含其关系)
    print("=" * 60)
    print("Step 0: 清空 Neo4j 数据库")
    print("=" * 60)
    # DETACH DELETE n: 先删除每个节点的关系再删除节点, 避免"有关系的节点不能直接删"的错误
    neo4j_client.run_cypher("MATCH (n) DETACH DELETE n")
    print("✅ 数据库已清空\n")


def step1_extract(max_files_per_source=None):
    # Step 1: 从 5 个知识源抽取实体/关系/属性(调用 __002__ 的抽取脚本)
    print("=" * 60)
    print("Step 1: 从5个知识源提取实体/关系/属性")
    print("=" * 60)

    # 延迟导入: 只有真正执行抽取时才载入抽取模块(该模块依赖 LLM, 可能较重)
    from __002__extract_information.__001__extract_legal_data import extract_from_folder

    # 用路径工具得到数据根目录与抽取结果保存目录(均相对于项目根)
    base_data = get_file_path("data")
    save_dir = get_file_path("__002__extract_information")

    # 定义 5 个知识源: (文件夹名, source_id, 结果JSON路径)
    sources = [
        ("laws", "laws", os.path.join(save_dir, "extract_law_data.json")),
        ("regulations", "regulations", os.path.join(save_dir, "extract_regulation_data.json")),
        ("interpretations", "interpretations", os.path.join(save_dir, "extract_interpretation_data.json")),
        ("industry_sources", "industry_sources", os.path.join(save_dir, "extract_industry_data.json")),
        ("cases", "cases", os.path.join(save_dir, "extract_case_data.json")),
    ]

    # 逐个知识源抽取
    for folder, source_id, save_path in sources:
        print(f"\n📚 处理知识源: {source_id}")
        # 拼出该源的数据目录
        folder_path = os.path.join(base_data, folder)
        # 目录不存在则打印警告并跳过
        if not os.path.exists(folder_path):
            print(f"⚠️ 文件夹不存在: {folder_path}, 跳过")
            continue
        # 执行抽取(传 max_files 可限制每个源处理的文件数, 用于测试)
        extract_from_folder(folder_path, source_id, save_path, max_files=max_files_per_source)

    print("\n✅ Step 1 完成: 所有知识源抽取完毕\n")


def step2_import():
    # Step 2: 把抽取结果 JSON 导入 Neo4j 图谱(调用 __001__ 导入器)
    print("=" * 60)
    print("Step 2: 将抽取结果导入 Neo4j 图谱")
    print("=" * 60)

    # 延迟导入导入器类
    from __003__create_neo4j_database.__001__legal_graph_importer import LegalGraphImporter

    # 创建导入器, 每批 500 条 Cypher
    importer = LegalGraphImporter(batch_size=500)
    # 抽取结果所在目录
    base_dir = get_file_path("__002__extract_information")

    # 5 个抽取 JSON 与对应中文标签
    sources = [
        ("extract_law_data.json", "法律法规"),
        ("extract_regulation_data.json", "行政法规"),
        ("extract_interpretation_data.json", "司法解释"),
        ("extract_industry_data.json", "行业标准"),
        ("extract_case_data.json", "裁判案例"),
    ]

    # 逐个文件导入
    for filename, label in sources:
        file_path = os.path.join(base_dir, filename)
        importer.import_from_json(file_path, label)

    print("\n✅ Step 2 完成: 图谱导入完毕\n")


def step3_export_metadata():
    # Step 3: 导出图谱结构元数据 JSON(调用 neo4j_client 的导出方法)
    print("=" * 60)
    print("Step 3: 导出图谱元数据")
    print("=" * 60)

    # 计算元数据输出路径
    output_path = get_file_path("__003__create_neo4j_database/legal_metadata.json")
    # 执行导出
    neo4j_client.export_legal_metadata_to_json(output_path=output_path)
    print(f"✅ 元数据已导出: {output_path}\n")


def step4_verify():
    # Step 4: 验证图谱完整性: 统计节点/关系数量, 抽查溯源与实体间关系
    print("=" * 60)
    print("Step 4: 验证图谱完整性")
    print("=" * 60)

    # 统计各类型节点数量
    result = neo4j_client.run_cypher(
        "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS count ORDER BY count DESC"
    )
    print("\n📊 各类型节点数量:")
    total_nodes = 0
    for r in result:
        print(f"  {r['label']}: {r['count']}")
        total_nodes += r['count']
    print(f"  ─────────────")
    print(f"  总计: {total_nodes} 个节点")

    # 统计各类型关系数量
    result = neo4j_client.run_cypher(
        "MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS count ORDER BY count DESC"
    )
    print("\n📊 各类型关系数量:")
    total_rels = 0
    for r in result:
        print(f"  {r['type']}: {r['count']}")
        total_rels += r['count']
    print(f"  ─────────────")
    print(f"  总计: {total_rels} 条关系")

    # 验证实体间关系(双层架构): 抽查 Action -> 后果/权利 示例
    result = neo4j_client.run_cypher(
        "MATCH (a:Action)-[r:CAUSES|LEADS_TO|ESTABLISHES]->(b) "
        "RETURN a.name AS action, type(r) AS rel, b.name AS consequence "
        "LIMIT 15"
    )
    print("\n🔗 实体间关系示例 (Action→Consequence/Right):")
    if result:
        for r in result:
            print(f"  [{r['action']}] -{r['rel']}-> [{r['consequence']}]")
    else:
        print("  ⚠️ 暂无实体间关系数据")

    # 溯源查询: 概念 <- 条款 <- 知识源/文件
    result = neo4j_client.run_cypher(
        "MATCH (c:LegalConcept)<-[r:DEFINES]-(a:Article) "
        "RETURN c.name AS concept, r.source_id AS source, r.file_name AS file "
        "LIMIT 15"
    )
    print("\n🔍 溯源查询示例 (概念→知识源→文件):")
    if result:
        for r in result:
            print(f"  [{r['concept']}] ← {r['source']}/{r['file']}")
    else:
        print("  ⚠️ 暂无 DEFINES 关系数据")

    print("\n✅ Step 4 完成: 验证完毕\n")


def step5_build_faiss():
    # Step 5: 构建 FAISS 向量索引(调用 __003__ 的索引脚本)
    print("=" * 60)
    print("Step 5: 构建 FAISS 向量索引")
    print("=" * 60)

    # 延迟导入索引构建函数
    from __003__create_neo4j_database.__003__faiss_embedding import build_all_indices
    # 执行全部索引构建
    build_all_indices()

    print("\n✅ Step 5 完成: FAISS 索引构建完毕\n")


def main():
    # 主入口: 打印横幅 + 解析命令行参数 + 按开关串联各步骤
    print("╔" + "═" * 58 + "╗")
    print("║" + "  法律知识图谱构建主流程".center(48) + "║")
    print("╚" + "═" * 58 + "╝")

    # 使用标准库 argparse 解析命令行参数
    import argparse
    parser = argparse.ArgumentParser(description="构建法律知识图谱")
    # 各开关: 命中则跳过对应步骤
    parser.add_argument("--skip-clear", action="store_true", help="跳过清空数据库步骤")
    parser.add_argument("--skip-extract", action="store_true", help="跳过抽取步骤 (复用已有JSON)")
    parser.add_argument("--skip-import", action="store_true", help="跳过导入步骤")
    parser.add_argument("--skip-faiss", action="store_true", help="跳过FAISS索引构建")
    # 每个知识源最多处理的文件数(调试用, 默认 None=全部)
    parser.add_argument("--max-files", type=int, default=None, help="每个知识源最多处理的文件数 (测试用)")
    args = parser.parse_args()

    # 未跳过清空则执行 Step 0
    if not args.skip_clear:
        step0_clear_database()

    # 未跳过抽取则执行 Step 1(可限制文件数)
    if not args.skip_extract:
        step1_extract(max_files_per_source=args.max_files)
    else:
        print("⏭ 跳过 Step 1 (抽取), 使用已有JSON数据\n")

    # 未跳过导入则执行 Step 2
    if not args.skip_import:
        step2_import()
    else:
        print("⏭ 跳过 Step 2 (导入)\n")

    # Step 3 / Step 4 默认始终执行(轻量且有用)
    step3_export_metadata()
    step4_verify()

    # 未跳过 FAISS 则执行 Step 5
    if not args.skip_faiss:
        step5_build_faiss()
    else:
        print("⏭ 跳过 Step 5 (FAISS索引构建)\n")

    # 完成横幅
    print("╔" + "═" * 58 + "╗")
    print("║" + "  🎉 法律知识图谱构建完成！".center(48) + "║")
    print("╚" + "═" * 58 + "╝")


# 直接运行本文件时启动主流程
if __name__ == '__main__':
    main()
