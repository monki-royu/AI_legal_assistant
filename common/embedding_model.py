# 📜 代码文字逻辑解析
# 本文件是项目的"向量化模型工厂"，负责在模块加载时实例化一个全局共享的
# SentenceTransformer 嵌入模型对象 embedding_model，供项目所有需要把文本转向量
# 的模块（如 FAISS 实体召回、RAG 检索、相似度匹配等）统一使用。核心逻辑非常简洁：
# 先 import sentence_transformers.SentenceTransformer 和项目配置类 Config，然后
# 实例化 Config 拿到本地 Embedding 模型路径 EMBEDDING_MODEL_PATH，最后用该路径
# 构造 SentenceTransformer 实例存到模块级变量 embedding_model。模块加载时模型权重
# 即被读入内存，后续调用方只需 `from common.embedding_model import embedding_model`
# 即可获得一个可直接 encode 的向量化客户端，避免重复加载浪费内存。函数关系：本模块
# 依赖 common.config.Config，自身被实体召回、RAG 向量化等流程 import，是项目向量化
# 能力的单一入口。

from sentence_transformers import SentenceTransformer      # 从 sentence_transformers 库导入 SentenceTransformer 类，它封装了 BERT/BGE 等句向量模型的加载与 encode 接口，是本项目做文本向量化的核心依赖；首次 import 时会触发 torch 等重型依赖加载
from common.config import Config                           # 导入项目配置类 Config，用于读取 EMBEDDING_MODEL_PATH（本地 Embedding 模型路径，指向磁盘上的模型目录）

conf = Config()                                            # 实例化 Config，把 .env 中的 EMBEDDING_MODEL_PATH 加载到 conf.EMBEDDING_MODEL_PATH 属性上；此处复用 Config 单例模式，所有模块读到同一份配置

embedding_model = SentenceTransformer(conf.EMBEDDING_MODEL_PATH)  # 全局单例：用配置中的模型路径加载 SentenceTransformer 模型；模块导入时即把模型权重读入内存，后续调用 embedding_model.encode(text) 即可得到高维浮点向量（维度由模型决定，如 512/768/1024）；做成模块级单例是为了避免每个调用方重复加载造成内存浪费和启动延迟
