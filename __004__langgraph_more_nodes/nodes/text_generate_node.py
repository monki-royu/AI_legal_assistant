"""小红书文案生成节点(法律科普方向, 仿中医text_generate_node)"""
# 📜 代码文字逻辑解析
# 本文件是 AI 法律助理(LangGraph 多智能体协作)流程中的"小红书文案生成节点",
# 借鉴自中医项目的 text_generate_node 设计, 并适配到法律科普场景。它在小红书发布
# 意图被识别后执行, 负责根据用户输入的主题生成一条适合小红书发布的法律科普文案,
# 包含吸引人的标题和富有分享性的正文。核心逻辑:1) 定义 Pydantic 数据模型
# XiaohongshuLegalPostOutput, 用 title 与 content 两个字段约束 LLM 输出结构;
# 2) 使用 PydanticOutputParser 自动生成格式说明(format_instructions), 嵌入到
# SystemMessage 中, 让 LLM 明确返回 JSON 的字段结构;3) 构造 SystemMessage(角色
# 设定与写作要求)与 HumanMessage(用户主题), 调用 my_llm 获取原始文本;4) 通过
# parser.parse 将原始文本解析为结构化对象, 返回 (title, content) 元组;5) 节点函数
# text_generate_node 将结果写入 state 的 xiaohongshu_title 与 xiaohongshu_content,
# 供后续图片生成与自动发布节点使用。该节点展示了"LLM + Pydantic 结构化输出"的标准
# 范式, 可作为任何"结构化内容生成"场景的迁移模板。
# 导入 Pydantic 的 BaseModel, 用于定义结构化输出的数据模型
from pydantic import BaseModel
# 导入 LangChain 的消息类型: SystemMessage 用于设定角色, HumanMessage 用于承载用户输入
from langchain_core.messages import SystemMessage, HumanMessage
# 导入 PydanticOutputParser, 用于将 LLM 输出解析为 Pydantic 对象
from langchain_core.output_parsers import PydanticOutputParser

# 导入 AgentState 类型, 它是整个 LangGraph 图中各节点共享的状态字典(TypedDict)
from __004__langgraph_more_nodes.agent_state import AgentState
# 导入项目统一的 LLM 实例, 封装了模型选择与调用细节
from common.llm import my_llm


# 定义小红书法律科普文案的结构化输出模型, 约束 LLM 必须返回 title 与 content 两个字段
class XiaohongshuLegalPostOutput(BaseModel):
    # 文案标题字段
    title: str
    # 文案正文字段
    content: str


def generate_xiaohongshu_text(input: str):
    """
    调用 LLM 生成一条小红书法律科普文案, 返回标题与正文。

    作用:
        根据用户提供的主题或需求, 让 LLM 扮演小红书法律科普文案助手, 严格按照
        Pydantic 模型定义的 JSON 结构返回标题(title)与正文(content)。

    参数:
        input (str): 用户提供的主题或需求文本(如"劳动合同维权")。

    返回值:
        tuple[str, str]: (title, content) 二元组, 分别为生成的标题与正文。

    可迁移性说明:
        本函数展示了"PydanticOutputParser + LLM 结构化输出"的标准范式, 只需替换
        Pydantic 模型字段与系统提示词, 即可迁移到任何需要结构化内容生成的场景
        (如:商品文案、邮件正文、报告摘要等)。
    """
    # 创建 Pydantic 输出解析器, 绑定目标模型 XiaohongshuLegalPostOutput
    parser = PydanticOutputParser(pydantic_object=XiaohongshuLegalPostOutput)
    # 获取自动生成的格式说明文本, 用于在提示词中告知 LLM 应返回的 JSON 结构
    format_instructions = parser.get_format_instructions()

    # 构造发送给 LLM 的消息列表, 包含系统消息(角色与写作要求)与用户消息(主题)
    messages = [
        # SystemMessage: 设定 LLM 为小红书法律科普文案助手, 并明确写作要求与输出格式
        SystemMessage(content=(
            "你是一个专门为小红书平台撰写法律科普内容的文案助手。\n"
            "请根据用户提供的主题或需求，生成一条适合小红书发布的法律科普类内容，要求包含：\n"
            "1. 吸引人的标题（title）：不超过20个中文字符，简短有吸引力，可加emoji\n"
            "2. 内容正文（content）：具有分享性和实用性，语气自然亲切，适合社交媒体\n"
            "   - 适当使用emoji和换行\n"
            "   - 涵盖法律知识点、常见误区、实用建议\n"
            "   - 可加#话题标签\n"
            "请你严格按照以下格式返回结果：\n"
            f"{format_instructions}"
        )),
        # HumanMessage: 承载用户提供的主题, 作为 LLM 的创作依据
        HumanMessage(content=input)
    ]

    # 调用 LLM 获取原始回复文本, 并去除首尾空白
    raw_output = my_llm.invoke(messages).content.strip()
    # 使用解析器将原始文本解析为 XiaohongshuLegalPostOutput 对象(若格式不符会抛异常)
    parsed_output = parser.parse(raw_output)
    # 返回标题与正文组成的元组
    return parsed_output.title, parsed_output.content


def text_generate_node(state: AgentState):
    """根据用户输入生成法律科普类的小红书文案"""
    # 打印日志, 标记进入文案生成阶段, 便于在控制台追踪节点执行顺序
    print("开始生成小红书标题和内容")
    # 调用 generate_xiaohongshu_text, 传入用户输入(缺失时为空字符串), 获取标题与正文
    title, content = generate_xiaohongshu_text(state.get('input', ''))

    # 将生成的标题写入 state, 供后续图片生成与自动发布节点使用
    state['xiaohongshu_title'] = title
    # 将生成的正文写入 state
    state['xiaohongshu_content'] = content
    # 打印日志, 仅展示标题前 20 个字符, 避免日志过长
    print(f"完成生成小红书文案: 标题={title[:20]}...")
    # 返回更新后的 state, 供 LangGraph 继续流转
    return state


# 脚本直接运行时的自测入口
if __name__ == '__main__':
    # 构造一个包含法律科普主题的测试 state
    state = AgentState(input="我想在小红书发笔记, 关于劳动合同维权的")
    # 调用节点获取结果
    result = text_generate_node(state)
    # 打印生成的标题
    print(result.get('xiaohongshu_title'))
    # 打印生成的正文
    print(result.get('xiaohongshu_content'))
