"""
法律知识图谱元数据导出脚本 (Step 3 的独立入口)
=============================================

# ============================================================
# 文件名称: __003__create_neo4j_database/__002__export_legal_metadata.py
# 文件作用: 把已有的 Neo4j 图谱结构导出为一份 JSON 元数据文件
# ============================================================
# 【这个文件是干什么的？】
#   这是一个"一键导出"小脚本。它不解析/抽取任何数据, 只是调用
#   common.neo4j_manager 里封装好的导出方法, 把当前 Neo4j 数据库中
#   的"标签 / 关系类型 / 三元组 / 属性"结构盘点成一份 legal_metadata.json。
#
# 【代码逻辑主线】
#   1. 从 common.neo4j_manager 引入全局单例 neo4j_client(已建好连接);
#   2. 用 common.path_utils 的 get_file_path 计算输出文件的绝对路径;
#   3. 调用 neo4j_client.export_legal_metadata_to_json(...) 执行导出;
#   4. 打印成功提示。
#
# 【新手建议】
#   - 它其实是 __000_main_build_graph.py 里 step3_export_metadata() 的"迷你版",
#     单独拎出来方便你只做"导出"这件事(比如图谱已在别处建好, 只想要元数据)。
#   - 想看完整流程, 去读同目录的 __000_main_build_graph.py。
#
# 📜 代码文字逻辑解析 (what / why / how)
#   WHAT : 把图数据库"长什么样"(有哪些节点类型、关系、字段)导出成机器可读的 JSON。
#   WHY  : 上层检索/问答模块、前端可视化都需要一份"图谱地图"来知道能查什么、怎么查;
#          用脚本自动盘点比人工维护更准确、永不落后。
#   HOW  : 复用现成的 neo4j_client.export_legal_metadata_to_json, 本文件只负责
#          "找对输出位置 + 触发一下 + 打印结果", 不做任何数据库细节。
"""

# 从 common.neo4j_manager 引入全局 Neo4j 客户端单例(已在模块加载时连好数据库)
from common.neo4j_manager import neo4j_client

# 从 common.path_utils 引入路径工具: get_file_path 把相对项目根的路径解析成绝对路径
from common.path_utils import get_file_path


# 用路径工具算出元数据 JSON 的落盘位置(项目根/__003__create_neo4j_database/legal_metadata.json)
output_path = get_file_path("__003__create_neo4j_database/legal_metadata.json")

# 调用客户端方法, 扫描全库并写出 JSON 元数据文件
neo4j_client.export_legal_metadata_to_json(output_path=output_path)

# 打印导出结果路径, 让用户确认文件已生成
print(f"✅ 元数据已导出: {output_path}")
