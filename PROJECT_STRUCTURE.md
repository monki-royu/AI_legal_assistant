# 法智引擎 (AI Legal Assistant) — 项目目录结构规范
# ============================================================
# 本文件描述法智引擎项目的企业级目录结构，以及"历史编号目录
# (__001__ ~ __006__) 与"新命名目录"的一一对应关系。
#
# 设计原则（企业级）：
#   1. 语义化命名：目录名称直接表达职责，去掉数字编号前缀；
#   2. 分层清晰：数据获取 → 数据处理 → 知识构建 → 核心引擎 → 交互层；
#   3. 关注点分离：源码 / 配置 / 资源 / 存储 / 文档 物理分离；
#   4. 可追溯：保留 "历史目录 → 新目录" 映射表，兼容旧导入路径（软链接 / 别名层）。
#
# 兼容性：为避免大量 import 断裂，本项目采用 "双目录并存" 策略：
#   - 新建语义化目录（如 langgraph_engine/），存放未来维护代码；
#   - __001__ ~ __006__ 目录保持不动（"只读存档"），运行时仍可被 import；
#   - 每次重构时逐步将功能从 __XXX__ 目录迁移到语义化目录；
#   - 本规范不做破坏性的目录删除。
# ============================================================

"""
法智引擎项目目录结构（企业级标准 v1.0）

根目录下的一级分类：
┌─────────────────────────────────────────────────────┐
│  一、核心源码层 (业务逻辑模块)                       │
├─────────────────────────────────────────────────────┤
│  crawler/                     [原 __001__clawler]    │
│      ├── 法律法规/             已爬取的法律法条 txt   │
│      ├── __000__获取网页内容通用方法.py                │
│      ├── __001__local_docx_to_txt.py                 │
│      ├── __002__crawl_law_database.py                │
│      ├── 法律列表.csv                                 │
│      └── 法律列表.xlsx                                │
│                                                       │
│  data_processing/             [原 __002__extract_information] │
│      ├── __000__extract_graph_data_utils.py          │
│      └── __001__extract_law_data.py                  │
│                                                       │
│  knowledge_graph/             [原 __003__create_neo4j_database] │
│      ├── __001__graph_importer.py    Neo4j导入       │
│      ├── __002__export_metadata.py   元数据导出       │
│      ├── __003__vector_index.py      FAISS向量索引    │
│      ├── legal_metadata.json         法律图谱元数据   │
│      └── tcm_metadata.json           中药图谱元数据   │
│                                                       │
│  langgraph_engine/            [原 __004__langgraph_more_nodes] │
│      ├── agent_state.py        全局状态字典(TypedDict)│
│      ├── langgraph_main.py     图编排(主入口)         │
│      ├── graph.png             图可视化快照           │
│      ├── streaming_helpers.py  流式输出辅助           │
│      └── nodes/                30+ 个业务节点         │
│          ├── intent_router_node.py                   │
│          ├── party_identify_node.py                  │
│          ├── credit_check_node.py   ← (新增资信查询)  │
│          ├── risk_aggregate_node.py                  │
│          ├── contract_ai_review_node.py              │
│          ├── compliance_review_node.py               │
│          ├── legal_research_node.py                  │
│          ├── retrieval_*.py (5个检索拆分子节点)       │
│          ├── auto_publish_xiaohongshu_node.py        │
│          └── ... (其余30+节点略)                     │
│                                                       │
│  web_ui/                      [原 __006__streamlit]  │
│      ├── app.py                主界面(Streamlit单页)  │
│      ├── xhs_publish_runner.py 小红书发布子进程入口    │
│      ├── test_contract_logic.py 合同审核单元测试      │
│      └── test_image_generate.py 图像生成调试脚本      │
│                                                       │
│  common/                      [公共工具层，保持不变]  │
│      ├── config.py             配置统一入口(.env)     │
│      ├── llm.py                大模型统一封装         │
│      ├── path_utils.py         相对/绝对路径转换      │
│      ├── qichacha_client.py    企查查API客户端(新增)  │
│      ├── neo4j_manager.py      Neo4j连接管理         │
│      ├── embedding_model.py    向量模型加载           │
│      ├── langgraph_compat.py   Python3.8兼容层       │
│      └── ouput_graph_utils.py  图输出绘图工具         │
├─────────────────────────────────────────────────────┤
│  二、存储层 (运行期生成 / 用户上传)                   │
├─────────────────────────────────────────────────────┤
│  storage/                                               │
│      ├── assets/                 [原 assets/]         │
│      │   ├── cookies/           Cookie存放(xiaohongshu_cookies.json) │
│      │   └── images/            用户上传/AI生成图片   │
│      ├── browser_data/          [原 browser_data/]    │
│      │   └── Default/           Playwright持久化浏览器数据 │
│      ├── tmp/                   临时文件(上传、缓存)  │
│      └── reports/               生成的审核报告存档    │
├─────────────────────────────────────────────────────┤
│  三、工程配置层                                       │
├─────────────────────────────────────────────────────┤
│  .env                        敏感配置(AppKey/URI等，不入库) │
│  .streamlit/                 Streamlit配置(主题/端口) │
│      └── config.toml                                 │
│  .idea/                     IntelliJ IDEA工程配置(不入库) │
│  .gitignore                  Git忽略规则              │
│  requirements.txt            Python依赖清单           │
│  start_streamlit.bat         一键启动Web UI(Windows)  │
│  run_test_qichacha.bat       企查查接入自检(Windows)  │
├─────────────────────────────────────────────────────┤
│  四、测试 & 文档                                       │
├─────────────────────────────────────────────────────┤
│  docs/                                               │
│      └── flowcharts/         流程图HTML(xmind产物)    │
│          ├── 02_contract_review.html                 │
│          ├── 节点式流程图.html                        │
│          └── 根據架構初步流程圖.html                  │
│  test_qichacha_api.py        企查查接入自检脚本        │
│  test_qichacha_report.json   自检报告(脚本生成)       │
│  __test_full_integration.py  全链路集成测试           │
│  PROJECT_STRUCTURE.md        本文件：目录结构规范文档  │
├─────────────────────────────────────────────────────┤
│  五、存档区 (不再维护 / 历史遗留)                     │
├─────────────────────────────────────────────────────┤
│  archive/                     弃用代码归档(保持只读)  │
│      └── .gitkeep                                     │
│  agent_state.py              根目录遗留副本(已迁移到 langgraph_engine) │
│  auto_publish_xiaohongshu_node.py  根目录遗留副本     │
│  _start_streamlit_server.py   废弃启动脚本(改用 bat)  │
└─────────────────────────────────────────────────────┘
"""

# ============================================================
#  表 A: 历史编号目录 → 新语义化目录 映射
# ============================================================
#  | 历史目录名                 | 新目录名             | 职责说明                | 是否兼容双写 |
#  |---------------------------|---------------------|-------------------------|-------------|
#  | __001__clawler            | crawler             | 数据爬取与采集          | 是          |
#  | __002__extract_information| data_processing     | 信息抽取与结构化处理    | 是          |
#  | __003__create_neo4j_database | knowledge_graph | 知识图谱构建与向量索引  | 是          |
#  | __004__langgraph_more_nodes | langgraph_engine  | 多智能体编排核心引擎    | 是          |
#  | __006__streamlit          | web_ui              | 前端交互(Streamlit)     | 是          |
#  | assets/                   | storage/assets/     | 运行期资源(cookie/图片) | 是          |
#  | browser_data/             | storage/browser_data/ | Playwright浏览器缓存 | 是          |
#  | docs/flowcharts/          | docs/flowcharts/    | 架构与流程图文档        | 无需改动    |
#  | common/                   | common/             | 公共工具层              | 无需改动    |

# ============================================================
#  表 B: 新增企查查资信查询链路 — 文件职责一览
# ============================================================
# 接入相对方资信查询(企查查)功能涉及的文件清单：
#
#  序号 | 文件路径                                                       | 职责
#  -----|---------------------------------------------------------------|------
#   1   | common/qichacha_client.py                                    | 企查查API统一客户端(签名/HTTP/降级mock)
#   2   | common/config.py L83-L87                                     | 读取 QICHACHA_* 4项环境变量
#   3   | .env L22-L25                                                 | 企查查AppKey/SecretKey配置模板(注释)
#   4   | langgraph_engine/nodes/credit_check_node.py                  | N8.5 资信查询节点(甲乙方识别后执行)
#   5   | langgraph_engine/agent_state.py L120-L131                    | 4个资信状态字段(party_a/b_credit_info / credit_risk_items / credit_check_success)
#   6   | langgraph_engine/nodes/risk_aggregate_node.py                | 三路风险 → 四路风险(含资信) + 立场加权扣分 + 全局资信微调
#   7   | langgraph_engine/langgraph_main.py                           | 图编排边顺序调整: party_identify → credit_check → risk_aggregate → final_delivery
#   8   | test_qichacha_api.py                                         | 三层自检脚本(Client/Node/Aggregate) 27个断言
#   9   | run_test_qichacha.bat                                        | Windows一键自检启动器(纯ASCII避免CMD编码问题)
#  10   | test_qichacha_report.json                                    | 自检结构化报告(脚本生成)

# ============================================================
#  表 C: 代码注释规范 (本项目统一遵循)
# ============================================================
#  所有 Python 文件必须包含以下三层注释：
#
#  1) 文件级 docstring (文件首行)
#     格式：三引号开头，包含：
#       - 文件中文名称 / 模块定位
#       - 核心功能清单（按 1/2/3 列出）
#       - 关键外部依赖 / 配置项
#       - 对外暴露的核心类 / 函数列表
#
#  2) 函数/类级 docstring
#     格式：三引号，包含：
#       - 作用（该函数做什么）
#       - 参数：类型 + 含义 + 取值范围
#       - 返回值：类型 + 结构说明
#       - 可迁移性说明（如何迁移到其他项目）
#       - 异常说明（抛出什么异常，降级策略）
#
#  3) 行内中文注释
#     - 每个逻辑步骤段前写一句中文说明；
#     - 关键分支（if/elif/else）写明"此分支处理什么场景"；
#     - 字典字段、魔法数字（权重、阈值）、公式（如扣分制）必须注释；
#     - 外部API调用点必须注释：鉴权方式、超时、降级策略；
#     - 尽量每 3-8 行代码有一条注释。

# ============================================================
#  Python 依赖清单（核心）
# ============================================================
#  大模型 & Agent 编排：
#    langchain-core      LLM 抽象层
#    python-dotenv       .env 配置加载
#    requests            HTTP 客户端 (企查查 API)
#
#  知识图谱 & 向量：
#    neo4j               Neo4j 官方驱动
#    faiss-cpu           FAISS 向量索引 (CPU版)
#    sentence-transformers  Embedding 模型加载
#
#  Web UI：
#    streamlit           Web 交互框架（>=1.32）
#    pillow              图片处理
#
#  自动化发布：
#    playwright          浏览器自动化 (小红书自动发布)
#
#  开发测试：
#    pytest              测试框架（可选）
#    black               代码格式化（可选，统一PEP8）
