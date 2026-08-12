# 📜 代码文字逻辑解析
# 本文件是项目的"大模型客户端工厂"，负责把 Config 中读取到的 API Key、Base URL 和
# 模型名实例化为一个 LangChain 标准的 ChatOpenAI 对象 my_llm，并暴露给项目所有需要
# 调用大模型的模块（如多智能体节点、RAG 检索后答案生成、Cypher 生成等）统一使用。
# 核心逻辑：模块加载时先 import LangChain 的消息类型 HumanMessage 和 ChatOpenAI 类，
# 然后构造 Config 实例 conf 拿到配置，再用 conf 的三个属性实例化 my_llm。这样后续
# 业务代码只需 `from common.llm import my_llm` 即可拿到一个配置好的 LLM 客户端，
# 不必在每个文件里重复读取配置或重复实例化。函数关系：本模块依赖 common.config.Config，
# 自身作为下游被 LangGraph 各节点、RAG 流程、知识图谱问答流程等 import；模块底部
# __main__ 块为本地自测代码，演示了"构造消息列表 → 调用 invoke → 打印响应"的最小
# 调用闭环，并通过大段注释解释了 Message 与 Context 在内存与显存上的差异。

from langchain_core.messages import HumanMessage           # 从 langchain_core 导入 HumanMessage，表示"用户发言"这一类消息，用于构造对话上下文；与 SystemMessage/ AIMessage 一起组成 messages 列表
from langchain_openai import ChatOpenAI                    # 从 langchain_openai 导入 ChatOpenAI，这是 LangChain 封装的 OpenAI 兼容聊天客户端，支持任何 OpenAI API 协议的服务（DeepSeek、通义、智谱等）
from common.config import Config                           # 导入项目配置类 Config，用于读取 MODEL_API_KEY / MODEL_BASE_URL / MODEL_NAME

conf = Config()                                            # 实例化 Config，把 .env 中的大模型相关配置加载到 conf 属性上，供下方 ChatOpenAI 使用

# ============ 配置llm区域 ============
my_llm = ChatOpenAI(                                       # 创建全局共享的 ChatOpenAI 客户端实例 my_llm；模块级单例，避免每个调用方都重复构造造成连接浪费
    api_key=conf.MODEL_API_KEY,                            # 鉴权密钥：从 conf 读取 API Key，ChatOpenAI 会把它放进 HTTP 请求头 Authorization: Bearer <key>
    base_url=conf.MODEL_BASE_URL,                          # 服务地址：指定 OpenAI 兼容 API 的基础 URL，决定请求打到 DeepSeek/通义/本地 vllm 等哪个推理后端
    model=conf.MODEL_NAME                                  # 模型名：指定具体调用的模型标识（如 deepseek-chat），服务端据此路由到对应权重
)

if __name__ == '__main__':
    # 构造对话消息
    # Message是Context的“砖块”。Context通常是由一系列Messages拼接而成的。例如，当你向大模型提问时，
    # 后台实际发送给模型的Context可能是：[System Message] + [User Message 1] + [Assistant Message 1] + [User Message 2(当前)]。
    #
    # 1.Message = 一句话（一个发言片段）
    # 在API中，一个Message 就是JSON列表里的一个元素
    # 2.Context = 整个“记忆背包”
    # Context是你发送给大模型的完整数据包。它通常由以下几部分组成：
    # Context = 系统指令 + 多轮对话历史 + 外部知识
    # 系统指令(SystemMessage)：比如“你是一个专业的翻译助手”。（1个 Message）
    # 多轮对话历史：你们之前聊的10轮内容。（20个Messages）
    # 外部知识(RAG)：系统偷偷塞给模型的一篇参考文章。（可能又算作 1~2 个 Messages）
    # 当你问出第11个问题时，大模型看到的Context，就是前面这20多个Messages加上参考文章的整体组合

    # 1.Message（消息）：占用的是“存储内存”本质：Message本质上是字符串（Text）。
    # 内存表现：在内存中，它仅仅是一串字符编码（如UTF - 8）。它的内存占用非常小，通常只与文本的长度成正比（比如几千个汉字可能只占几十KB 的内存）。
    # 生命周期：只要你不主动删除它，它就一直静静地躺在内存里，不会自动膨胀。
    # 2. Context（上下文）：占用的是巨大的“计算内存”（显存） 当大模型开始处理这些 Message 时，它们会被转化为Context。在这个过程中，内存占用会呈指数级爆炸。因为
    # Context 在内存中包含了以下极其消耗资源的结构：
    # Token嵌入（Embeddings）：文本被切分成Token后，每个Token会被映射成一个高维向量（比如4096维的浮点数数组）。这比纯文本字符串占用的空间大得多。
    # KVCache（键值缓存）：这是大模型最吃内存的地方！为了实现“记住上下文”，模型在计算注意力机制（Attention）时，必须把之前所有Message计算出来的Key 和Value矩阵缓存在显存中。
    # Context越长，这个缓存占用的显存就越庞大。
    # 激活值（Activations）：在模型进行前向推理时，神经网络每一层产生的中间计算结果都需要占用内存。
    messages = [
        HumanMessage(content="用一句话介绍一下你自己")       # 构造一个仅含单条用户消息的列表，content 是发送给模型的实际问题；列表形式是为了与多轮对话接口保持一致
    ]

    # 调用模型
    response = my_llm.invoke(messages)                     # 同步调用 ChatOpenAI.invoke，把 messages 列表整体作为 Context 发给模型，返回一个 AIMessage 对象（含 content/usage 等字段）
    print(response.content)                                # 打印响应正文字段 content，即模型生成的回答文本；调试时用于肉眼确认 LLM 通路是否打通
