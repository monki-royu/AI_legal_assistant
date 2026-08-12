# 📜 代码文字逻辑解析
# 本文件是整个 AI 法律助理项目的"配置中枢"，统一管理所有外部依赖的连接信息和资源路径。
# 在项目中它扮演两个角色：第一，集中读取 .env 环境变量（大模型 API、Neo4j 图数据库、
# 极梦文生图密钥、Embedding 模型路径等敏感信息），避免把密钥硬编码进业务代码；
# 第二，借助 path_utils.get_file_path 把工程内的相对路径转成绝对路径，统一管理
# 知识图谱元数据 JSON、FAISS 向量索引、id2text 映射等本地资源。
# 核心逻辑：模块加载时先调用两次 load_dotenv（一次按默认规则扫描，一次显式定位工程根
# 目录下的 .env，确保不同启动目录都能正确加载配置），随后定义 Config 类，在 __init__
# 中通过 os.getenv 逐项把环境变量读到实例属性上，并对图谱元数据做"法律优先、中药兼容、
# 缺失降级为空 JSON"的三级兜底。函数关系：本模块依赖 path_utils.get_file_path，
# 自身被 llm.py、neo4j_manager.py、embedding_model.py 等公共模块 import，是项目所有
# 子模块共享的单一配置入口，调用方只需 Config().属性名 即可拿到所需配置。

import os                                                # 导入标准库 os，用于读取环境变量和检测文件是否存在（os.getenv / os.path.exists）
from dotenv import load_dotenv                           # 从 python-dotenv 包导入 load_dotenv，作用是把 .env 文件中的键值对加载到 os.environ 中

from common.path_utils import get_file_path              # 导入本项目的路径拼接工具函数，用于把工程相对路径转换为绝对路径

load_dotenv()                                            # 第一次加载：按 python-dotenv 默认规则从当前工作目录向上查找 .env 并加载，保证从任意目录运行也能读到部分配置
load_dotenv(get_file_path(".env"))                       # 第二次加载：显式定位到工程根目录下的 .env 文件再次加载，双保险策略，确保 .env 一定被加载（不会覆盖已有同名变量，但能补齐缺失项）
# get_file_path(".env") 只是一个找路标的动作，它只返回一个字符串（比如 /project/.env），它本身不会去读取文件。
#load_dotenv() 才是真正把文件里的配置       加载到系统中      的动作。

class Config:
    #这里负责存放 本项目相关的 所有配置信息，负责 对具体配置文件的读取，存放变量名，后期存放具体取值 只需要 调用.属性即可，
    #因为这里存放了所有对象相关的 配置信息，所以 创建不同的对象，但只需要 调用这一个类，然后.属性即可
    def __init__(self):
        """
        初始化配置对象，集中读取并保存项目所有运行期需要的配置项。

        作用:
            在实例化 Config 时一次性把所有外部配置读入内存，后续业务模块只需持有
            一个 Config 实例（或通过模块级单例）即可访问全部配置，避免重复读取。

        参数:
            无（self 之外不接受参数，所有配置来源均为环境变量与本地文件）。

        返回值:
            无返回值；构造完成后实例自身拥有 MODEL_API_KEY、MODEL_BASE_URL、
            MODEL_NAME、NEO4J_*、JIMENG_*、TCM_METADATA、EMBEDDING_MODEL_PATH、
            ENTITY_INDEX_PATH、ENTITY_ID2TEXT_PATH、history_num 等属性。

        可迁移性说明:
            该类只依赖标准库 os、第三方 python-dotenv 和项目内 path_utils，
            不耦合任何业务逻辑，可直接复制到其他需要 .env + 工程相对路径管理的
            Python 项目中；若新增配置项，只需在 __init__ 中追加一行 os.getenv
            或 get_file_path 即可，向后兼容性良好。
        """
        # 大模型相关
        self.MODEL_API_KEY = os.getenv("MODEL_API_KEY")  # 从环境变量读取大模型服务商的 API Key（如 DeepSeek/通义/智谱），用于 ChatOpenAI 鉴权
        #getenv找到文件并将数据---文件里的配置  读取到变量中
        self.MODEL_BASE_URL = os.getenv("MODEL_BASE_URL")  # 读取大模型服务的 API 基础地址，例如 https://api.deepseek.com/v1，决定请求打到哪个推理服务
        self.MODEL_NAME = os.getenv("MODEL_NAME")          # 读取具体调用的模型名（如 deepseek-chat），后续传给 ChatOpenAI(model=...)

        # neo4j相关
        self.NEO4J_URI = os.getenv("NEO4J_URI")            # 读取 Neo4j 图数据库连接 URI，形如 bolt://localhost:7687 或 neo4j+s://xxx.databases.neo4j.io
        self.NEO4J_USER = os.getenv("NEO4J_USER")          # 读取 Neo4j 登录用户名（默认常为 neo4j）
        self.NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")  # 读取 Neo4j 登录密码，与 URI、USER 一起传给 GraphDatabase.driver 完成连接

        # 读取极梦的密钥
        self.JIMENG_AK = os.getenv("JIMENG_AK")            # 读取火山引擎"即梦/极梦"文生图服务的 AccessKey，用于图像生成接口签名
        self.JIMENG_SK = os.getenv("JIMENG_SK")            # 读取对应 SecretKey，与 AK 配对用于 HMAC 签名鉴权
        #
        # 读取图谱模式层的数据(优先法律项目, 兼容中药项目)
        legal_meta_path = get_file_path("__003__create_neo4j_database/legal_metadata.json")  # 拼接法律项目图谱元数据 JSON 的绝对路径（首选方案）
        tcm_meta_path = get_file_path("__003__create_neo4j_database/tcm_metadata.json")      # 拼接中药项目图谱元数据 JSON 的绝对路径（兼容方案，复用同一套代码做其他项目）
        if os.path.exists(legal_meta_path):                # 优先判断法律项目元数据是否存在
            self.TCM_METADATA = open(legal_meta_path, "r", encoding="utf-8").read()  # 存在则读取全部文本（含 labels/relationships/triples 的模式层描述），供后续 Cypher 生成时作为 schema 参考
        elif os.path.exists(tcm_meta_path):                # 法律项目不存在则回退到中药项目元数据，保证代码可同时服务两个领域
            self.TCM_METADATA = open(tcm_meta_path, "r", encoding="utf-8").read()    # 读取中药元数据文本；注意这里读取的是字符串而非 dict，后续由调用方按需 json.loads
        else:
            self.TCM_METADATA = "{}"  # 文件不存在时降级为空JSON  # 两个文件都不存在时给一个合法的空 JSON 字符串，避免下游 json.loads 报错，保证运行不中断
        #
        # # embedding模型
        self.EMBEDDING_MODEL_PATH = os.getenv("EMBEDDING_MODEL_PATH")  # 读取本地 Embedding 模型路径（如 bge-small-zh），供 SentenceTransformer 加载做向量化
        #
        # # index的路径
        self.ENTITY_INDEX_PATH = get_file_path("__003__create_neo4j_database/nero4j_embedding_faiss.index")  # 实体向量 FAISS 索引文件绝对路径，用于实体召回时的近邻搜索
        self.ENTITY_ID2TEXT_PATH = get_file_path("__003__create_neo4j_database/nero4j_embedding_faiss_id2text.pkl")  # 实体 ID 到文本的映射 pickle 文件路径，配合 FAISS 索引把召回的 id 还原成可读实体名
        # # 记忆轮次
        self.history_num = 5                                # 多轮对话保留的历史轮数上限，超过该值的旧消息会被截断，控制 Context 长度防止超 token 限制


if __name__ == "__main__":
    conf = Config()                                       # 仅在直接运行本文件时实例化 Config，用于本地自测；被 import 时不会执行，避免重复构造
    # print(conf.TCM_METADATA)                            # 调试用：打印图谱元数据字符串以人工核对内容（已注释，默认不输出）
