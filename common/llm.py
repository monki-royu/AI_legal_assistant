from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from common.config import Config

conf = Config()

# ============ 配置llm区域 ============
my_llm = ChatOpenAI(
    api_key=conf.MODEL_API_KEY,
    base_url=conf.MODEL_BASE_URL,
    model=conf.MODEL_NAME
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
        HumanMessage(content="用一句话介绍一下你自己")
    ]

    # 调用模型
    response = my_llm.invoke(messages)
    print(response.content)
