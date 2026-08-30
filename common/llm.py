# 📜 ============================================================
# 文件名称: common/llm.py
# 文件作用: 项目的"大模型客户端工厂"
# ============================================================
#
# 【这个文件是干什么的？】
# 这个文件是整个法智引擎中"最被频繁调用的文件"之一。
# 它只做一件事：从配置文件读取 API Key / Base URL / 模型名，
# 然后实例化一个 LangChain 标准的 ChatOpenAI 客户端对象，
# 存在模块级变量 my_llm 中，让全项目所有需要调用大模型的地方
# 都能通过 from common.llm import my_llm 直接使用。
#
# 【为什么需要这个文件？】
# 法智引擎中有几十个地方需要调用大模型（LLM）：
#   - 合同审核AI节点：让LLM分析条款是否有风险
#   - 合规审查节点：让LLM检查合同是否合规
#   - 意图路由节点：让LLM判断用户想做什么
#   - 检索意图分解节点：让LLM生成子查询和关键词
#   - 文书生成节点：让LLM撰写法律文书
#   - ...等等
# 如果每个文件都自己读配置、自己 new ChatOpenAI，就会造成：
#   1. 代码重复 —— 每个文件都要写一样的配置读取逻辑
#   2. 资源浪费 —— 每个文件都创建一个新的 HTTP 连接池
#   3. 配置分散 —— 修改模型名要改几十个文件
# 所以这个文件把"创建 LLM 客户端"这件事集中到一处，
# 做成"模块级单例"（module-level singleton）。
#
# 【代码逻辑】
# 1. import HumanMessage（消息类型）和 ChatOpenAI（LLM 客户端类）
# 2. import Config（项目配置类，从 .env 读 API Key/Base URL/模型名）
# 3. conf = Config() —— 实例化配置对象
# 4. my_llm = ChatOpenAI(api_key=..., base_url=..., model=...) —— 创建客户端
# 5. 其他模块只要 from common.llm import my_llm 就能拿到这个客户端
# 6. my_llm.invoke(messages) 发送消息列表给大模型，得到回答
#
# 【谁在用它？】
#   几乎所有业务节点！具体包括但不限于：
#   - contract_ai_review_node    — 合同审核AI
#   - compliance_review_node     — 合规审查
#   - intent_router_node          — 意图路由
#   - contract_classify_node      — 合同分类
#   - numeric_extract_node        — 数值抽取
#   - party_identify_node         — 甲乙方识别
#   - final_delivery_node         — 最终交付
#   - llm_direct_out_node         — LLM直答
#   - check_cypher_node           — Cypher校验
#   - generate_neo4j_cypher_node  — Cypher生成
#   - retrieval_intent_decompose_node — 检索意图分解
#   - doc_case_analyze_node       — 案情分析
#   - doc_clause_fill_node        — 条款填充
#   - doc_risk_advisor_node       — 风险提示
#   - ... 以及所有需要 LLM 能力的节点
#
# 【哪些节点【不】用它？】
#   纯本地计算的节点不依赖 LLM，零成本零延迟，例如：
#   - full_text_segment_node      — 全文本统一切分（正则锚点 + 段落聚合 + 句界兜底）
#   - context_pack_node           — 检索上下文打包（字符串匹配算覆盖率）
#   - numeric_validate_node       — 数值校验（规则引擎）
#   - risk_aggregate_node         — 风险聚合（加权计分）
#   审核/审查节点内部的"规则预筛层"(common/review_context_utils.py)同样是纯正则，
#   先用规则标出重点单元，再把 LLM 的算力集中花在语义判断上。
#
# 【支持的模型】
#   本项目使用 OpenAI 兼容 API 协议，所以只要是支持这个协议的模型都能用：
#   - DeepSeek (deepseek-chat / deepseek-reasoner)
#   - 通义千问 (qwen-max / qwen-plus)
#   - 智谱 GLM (glm-4 / glm-3-turbo)
#   - 本地部署的 vLLM / Ollama 等
#   只需在 .env 文件中修改 MODEL_NAME / MODEL_BASE_URL / MODEL_API_KEY 即可切换。

# 【from langchain_core.messages import HumanMessage】：
#   导入 LangChain 的消息类型 HumanMessage（人类消息）。
#   LangChain 把与大模型的交互抽象为"消息列表"（messages list）：
#     - HumanMessage：用户的发言
#     - SystemMessage：系统指令（设置 AI 的角色和行为）
#     - AIMessage：AI 的回复
#   这个文件虽然只 import 了 HumanMessage（用于自测），
#   但业务代码中会用到所有三种消息类型来构造对话上下文。
from langchain_core.messages import HumanMessage

# 【from langchain_openai import ChatOpenAI】：
#   导入 LangChain 封装的 OpenAI 兼容聊天客户端。
#   ChatOpenAI 是 LangChain 中最常用的 LLM 封装之一，
#   它把"发送 HTTP 请求到 OpenAI API"这件事封装成了一个简单的 Python 对象。
#   调用 my_llm.invoke(messages) 就会自动：
#     1. 把 messages 列表序列化成 JSON
#     2. 发送 POST 请求到 base_url/chat/completions
#     3. 解析响应，取出 AI 的回答
#     4. 返回一个 AIMessage 对象
#   本项目使用的是"OpenAI 兼容协议"——也就是说，只要模型服务提供商的 API
#   跟 OpenAI 的格式一样（DeepSeek、通义千问、智谱GLM等都是），就能用这个类。
from langchain_openai import ChatOpenAI

# 【from common.config import Config】：
#   导入项目配置类。Config 从 .env 文件中读取三个关键配置：
#     - MODEL_API_KEY：API 鉴权密钥（相当于密码）
#     - MODEL_BASE_URL：API 服务地址（决定了请求发到哪个服务器）
#     - MODEL_NAME：模型名称（决定了用哪个 AI 模型）
#   这三个配置分别对应 ChatOpenAI 的三个参数。
from common.config import Config

# 【conf = Config()】：
#   实例化 Config，触发 .env 文件读取。
#   现在可以通过 conf.MODEL_API_KEY、conf.MODEL_BASE_URL、conf.MODEL_NAME
#   来访问三个关键配置了。
#   Config 类内部会缓存读取结果（模块级单例），多次实例化不会重复读 .env 文件。
conf = Config()

# ============ 配置llm区域 ============
# 【my_llm = ChatOpenAI(...)】：
#   创建全局共享的 LLM 客户端实例。
#   这是"模块级单例"（module-level singleton）——在 Python 中，
#   模块级别的变量在第一次 import 时创建，之后所有引用该模块的代码
#   拿到的都是同一个对象。这避免了在每个节点中重复创建 LLM 客户端。
my_llm = ChatOpenAI(
    # 【api_key】：API 鉴权密钥。
    # ChatOpenAI 在每次请求时会把 api_key 放到 HTTP 请求头中：
    #   Authorization: Bearer <api_key>
    # 服务器根据这个密钥来识别用户身份和计费。
    api_key=conf.MODEL_API_KEY,

    # 【base_url】：API 服务地址。
    # 决定了 HTTP 请求发到哪个服务器。例如：
    #   - https://api.deepseek.com      （DeepSeek 官方）
    #   - https://dashscope.aliyuncs.com （通义千问）
    #   - http://localhost:8000/v1       （本地 vLLM 服务）
    base_url=conf.MODEL_BASE_URL,

    # 【model】：模型名称。
    # 指定具体调用哪个 AI 模型。服务器根据这个参数选择合适的模型权重。
    # 例如：deepseek-chat、qwen-max、glm-4 等。
    model=conf.MODEL_NAME,

    # 【timeout】：单次 HTTP 请求超时(秒), 从配置 MODEL_TIMEOUT 读取(默认 300)。
    # 闲聊/短问答 120s 足够; 但重负载任务(法条概念抽取: 8000 字输入 + 大 JSON 输出)
    # 实测 120s 会 "Request timed out" 而降级规则抽取, 故调大并做成可配置。
    timeout=conf.MODEL_TIMEOUT,

    # 【max_retries】：请求失败重试次数。
    # 默认 2 次; 降为 1 次以缩短最坏等待, 重试仍失败时由调用方降级(规则抽取)。
    max_retries=1,
)

# 【llm 别名】：兼容旧代码中的 from common.llm import llm。
# 项目中有些模块（如 entity_extractor、cypher_generator）仍引用 llm,
# 提供别名避免 ImportError。推荐新代码直接使用 my_llm。
llm = my_llm

# ============================================================
# 模块自测入口
# ============================================================
if __name__ == '__main__':
    # 【自测逻辑】：
    #   直接运行 python -m common.llm 时执行下面的代码，
    #   向大模型发一条消息，打印回复，验证 LLM 通路是否通畅。

    # 【构造消息列表】：
    # messages = [HumanMessage(content="...")]
    # 这是一个"单轮对话"——只有一条用户消息，没有历史。
    # 如果是多轮对话，messages 可以包含多个 HumanMessage 和 AIMessage：
    #   messages = [
    #       SystemMessage("你是法律助手"),
    #       HumanMessage("民法典第584条是什么？"),
    #       AIMessage("民法典第584条规定..."),
    #       HumanMessage("能举个例子吗？"),
    #   ]
    # ⚠️ 注意：消息列表越长，消耗的 Token 越多（花钱越多！）。
    messages = [
        HumanMessage(content="用一句话介绍一下你自己")
    ]

    # 【调用模型】：
    # response = my_llm.invoke(messages)
    # invoke 方法做了以下事情：
    #   1. 把 messages 序列化为 JSON
    #   2. 组装 HTTP 请求（POST /chat/completions）
    #   3. 发送请求并等待响应
    #   4. 解析响应 JSON 为 AIMessage 对象
    #   5. 返回 AIMessage 对象（包含回答文本、Token 用量等）
    #
    # 📌 Message vs Context（重要概念）：
    #   - Message（消息）是"一句话"——占用的是普通内存（RAM），很小。
    #   - Context（上下文）是整个消息列表 + 模型内部状态——占用的是显存（VRAM），很大！
    #     因为模型在处理 Context 时，需要为每个 Token 计算并缓存 Key-Value 矩阵
    #     （这就是 KVCache，占显存的大头）。
    #   Context 越长，显存占用就越大，而且是指数级增长！
    #   所以大模型 API 按 Token 数量收费，Token 越多越贵。
    response = my_llm.invoke(messages)

    # 【打印响应】：
    # response.content 是 AI 生成的文本内容。
    # 如果一切正常，应该打印出一句自我介绍。
    # 如果报错，说明 API Key、Base URL 或网络可能有问题。
    print(response.content)