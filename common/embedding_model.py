# 📜 ============================================================
# 文件名称: common/embedding_model.py
# 文件作用: 项目的"向量化模型工厂" — 全局共享的 Embedding 模型
# ============================================================
#
# 【这个文件是干什么的？】
# 这个文件就像是一个"模型工厂"，它在整个项目启动的时候，只做一件事：
#   加载一个"能把文字变成向量"的 AI 模型，然后存起来，让全项目共享使用。
#
# 【为什么需要这个文件？】
# 法智引擎中有很多地方需要把"中文文本"变成"数字向量"：
#   - FAISS 索引检索：先把法规文本变成向量存进索引，检索时把用户问题也变成向量，然后去索引里找最相似的。
#   - 实体召回：从知识图谱里匹配实体时，需要计算文本的语义相似度。
#   - RAG 检索增强：把检索到的文档和用户问题做语义匹配。
# 所有这些地方都需要同一个 Embedding 模型。如果每个模块都自己加载一次模型，
# 那模型权重会在内存里重复加载很多次，浪费大量内存和启动时间。
# 所以这个文件把模型做成"模块级单例"——整个项目只加载一次，所有人都共用。
#
# 【代码逻辑】
# 1. import sentence_transformers.SentenceTransformer
#    — 引入"句向量转换器"，它是把句子变成向量的核心工具。
# 2. import Config
#    — 从 config.py 引入配置类，用来读取 .env 环境变量中的模型路径。
# 3. conf = Config()
#    — 实例化配置对象，拿到 EMBEDDING_MODEL_PATH（模型在磁盘上的位置）。
# 4. embedding_model = SentenceTransformer(conf.EMBEDDING_MODEL_PATH)
#    — 用模型路径加载模型，存到全局变量 embedding_model。
#    这个加载过程在 import 时就会执行（模块加载即初始化）。
#
# 【谁在用它？】
#   - retrieval_engine.py（检索引擎：做向量检索时 encode 用户问题）
#   - __003__create_neo4j_database/__003__vector_index.py（构建 FAISS 索引）
#   - 任何需要把中文文本转为向量的模块
#
# 【函数关系】
#   本模块依赖:
#     - common.config.Config（获取模型路径）
#   本模块被依赖:
#     - common.retrieval_engine（检索时用 embedding_model.encode）
#     - 所有向量化相关的模块

# 【import os + 线程环境变量钉死（必须在任何 torch/sentence_transformers/faiss import 之前）】：
# 规避 Windows 下 faiss 的 OpenMP 运行时与 torch 自带 OpenMP 运行时在同一进程内
# 冲突导致的 0xC0000005 访问违规（SIGSEGV）。实测：先 import faiss 再调用 torch.encode 必崩，
# 钉死 OMP/MKL/OPENBLAS/NUMEXPR 线程数为 1 后链路（read_index + encode + search）可正常跑通。
import os
for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

# 【import  sentence_transformers.SentenceTransformer】：
#   从 sentence_transformers 库导入 SentenceTransformer 类。
#   sentence_transformers 是一个专门做"句向量"的 Python 库。
#   SentenceTransformer 是它的核心类，封装了 BERT / BGE 等模型的加载和 encode 接口。
#   注意：首次 import 时会触发 torch（PyTorch）等重型深度学习框架的加载，
#   这个过程可能需要几秒钟，并且会占用几百 MB 到几个 GB 的内存。
#   模型选择：本项目使用 BAAI/bge-m3 模型（BGE-M3: 多语言/多粒度/多功能 Embedding），
#   它支持中文、英文等多语言，支持文本粒度到 token 级别，支持稠密/稀疏/多向量三种检索模式。
from sentence_transformers import SentenceTransformer

# 【from common.config import Config】：
#   从 common/config.py 导入项目的配置类 Config。
#   Config 类的职责是读取 .env 环境变量文件，把配置项变成 Python 对象的属性。
#   这里我们只需要 Config 中的一个属性：EMBEDDING_MODEL_PATH，
#   它指向了磁盘上 Embedding 模型文件的存放路径。
#   【为什么不用直接写死路径？】
#   因为不同开发者的电脑上模型存放位置可能不同（有人放 C:/models/，有人放 D:/data/），
#   而且生产环境和开发环境的路径也不同。通过 .env 配置文件统一管理，修改时只需要改 .env 文件，
#   不需要改代码。
from common.config import Config

# 【conf = Config()】：
#   实例化 Config 类，创建配置对象。
#   这个实例化过程会读取 .env 文件中的所有配置项。
#   之后可以通过 conf.EMBEDDING_MODEL_PATH 拿到 Embedding 模型的磁盘路径。
#   Config 类在整个项目中通常只实例化一次（单例模式），所有模块共用同一个配置对象，
#   避免多次读取 .env 文件造成 I/O 浪费。
conf = Config()

# 【embedding_model = SentenceTransformer(conf.EMBEDDING_MODEL_PATH)】：
#   【模块级全局变量】—— 这是整个文件的"输出产品"。
#   用配置中的模型路径加载 SentenceTransformer 模型，存入 embedding_model 变量。
#   这个变量是模块级的（在 .py 文件顶层定义），所以只要这个模块被 import 一次，
#   embedding_model 就会存在于 Python 进程的全局命名空间中，后续所有 import 它的模块
#   拿到的都是同一个模型实例（Python 的模块缓存机制保证这一点）。
#
#   加载过程发生的事情：
#   1. 读取模型配置文件（config.json），了解模型的结构和参数。
#   2. 加载模型权重文件（pytorch_model.bin 或 model.safetensors）。
#   3. 加载分词器（tokenizer.json），用于把中文文本切分成 token。
#   4. 把模型加载到内存中（如果有 GPU，会自动加载到 GPU 显存）。
#
#   加载完成后，其他模块只需要这样用：
#     from common.embedding_model import embedding_model
#     vector = embedding_model.encode("民法典第584条")
#   得到的 vector 是一个高维浮点数数组（维度由模型决定，bge-m3 是 1024 维）。
embedding_model = SentenceTransformer(conf.EMBEDDING_MODEL_PATH)

# 【import torch + torch.set_num_threads(1)（加固）】：
# 模型加载完成后，把 torch 内部算子使用的 BLAS/OpenMP 线程池钉死为 1。
# 这不影响 Python 层 ThreadPoolExecutor 的并发（双审 LLM 并行、文书法条+类案并行依赖的是 HTTP I/O 并发），
# 仅约束单次 encode/matmul 内部用几核，从而消除与 faiss 的 OpenMP 冲突导致的段错误。
import torch
torch.set_num_threads(1)