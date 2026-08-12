"""小红书发布结果生成markdown(仿中医generate_markdown_node)"""
# 📜 代码文字逻辑解析
# 本文件是 AI 法律助理(LangGraph 多智能体协作)流程中的"小红书发布结果 Markdown 生成节点",
# 借鉴自中医项目的 generate_markdown_node 设计。它在小红书发布流程结束后执行,
# 负责将标题、正文、图片路径列表与发布提示拼装成一段可在前端直接渲染的 HTML 代码,
# 用于在 Web 界面展示发布成果。核心逻辑:1) trans_image_path_list 工具函数将本地图片
# 绝对路径转换为基于项目根目录的相对路径, 并拼接上 localhost:8000 的前缀, 形成可被
# 本地静态服务器访问的 URL;2) generate_markdown_code 函数使用 f-string 拼装一段
# HTML, 包含标题、正文与一个 flex 布局的图片容器, 支持自定义图片宽高;3) 节点入口
# generate_markdown_node 从 state 读取标题、正文、图片列表与提示, 调用上述函数生成
# HTML, 同时写入 state["xiaohongshu_markdown_output"](纯 HTML)与 state["output"]
# (提示 + HTML), 供前端展示。该节点是纯字符串拼装逻辑, 不调用 LLM 或外部服务,
# 可作为任何"结构化数据 → HTML 卡片"场景的迁移模板。
# 导入 os 模块, 用于将绝对路径转换为相对路径
import os

# 导入 AgentState 类型, 它是整个 LangGraph 图中各节点共享的状态字典(TypedDict)
from __004__langgraph_more_nodes.agent_state import AgentState
# 导入路径工具: root_path 为项目根目录绝对路径, get_file_path 用于获取标准子目录路径
from common.path_utils import root_path, get_file_path


def trans_image_path_list(image_path_list: list):
    """
    将本地图片绝对路径列表转换为可被本地静态服务器访问的 URL 列表。

    作用:
        前端无法直接通过绝对路径访问本地图片, 需将绝对路径转换为相对于项目根目录的
        相对路径, 再拼接 localhost:8000 前缀, 形成 http://localhost:8000/xxx 形式的 URL。

    参数:
        image_path_list (list[str]): 本地图片绝对路径列表。

    返回值:
        list[str]: 转换后的 URL 列表。

    可迁移性说明:
        该函数是"本地路径 → 静态服务 URL"的通用转换器, 修改前缀(如改为正式域名)
        即可迁移到任何需要前端访问本地资源的场景。
    """
    # 定义单条路径的转换函数(内部函数, 闭包捕获 root_path)
    def trans_image_path(image_path):
        # 计算图片路径相对于项目根目录的相对路径
        relative_path = os.path.relpath(image_path, root_path)
        # 拼接本地静态服务器前缀, 返回完整 URL
        return f"http://localhost:8000/{relative_path}"
    # 对列表中每个路径应用转换函数, 返回 URL 列表
    return [trans_image_path(p) for p in image_path_list]


def generate_markdown_code(title, content, image_path_list, image_width="300px", image_height="300px"):
    """
    根据标题、正文与图片列表生成一段 HTML 卡片代码。

    作用:
        拼装一段包含标题、正文与图片容器的 HTML, 图片容器使用 flex 布局横向排列,
        支持自定义图片宽高, 用于在前端展示小红书发布成果。

    参数:
        title (str): 笔记标题。
        content (str): 笔记正文。
        image_path_list (list[str]): 本地图片路径列表(会被转换为 URL)。
        image_width (str): 图片宽度(CSS 值, 如 "300px")。
        image_height (str): 图片高度(CSS 值, 如 "300px")。

    返回值:
        str: 完整的 HTML 代码字符串。

    可迁移性说明:
        该函数是"数据 → HTML 卡片"的通用拼装器, 修改 HTML 模板即可迁移到任何
        需要在前端展示结构化成果的场景(如:报告卡片、商品卡片等)。
    """
    # 先将本地图片路径列表转换为可访问的 URL 列表
    image_path_list = trans_image_path_list(image_path_list)
    # 使用 f-string 拼接 HTML 头部与样式, 注意 CSS 中花括号需用 {{ }} 转义
    html_code = f"""
    <html>
        <head>
            <title>{title}</title>
            <style>
                .image-container {{
                    display: flex;
                    gap: 10px;
                    flex-wrap: wrap;
                    justify-content: flex-start;
                }}
                .image-container img {{
                    width: {image_width};
                    height: {image_height};
                }}
            </style>
        </head>
        <body>
            <p>小红书发布成功</p>
            <h3>标题：{title}</h3>
            <p>内容：{content}</p>
            <div class="image-container">
    """
    # 遍历图片 URL 列表, 为每张图片生成一个 <img> 标签
    for image_path in image_path_list:
        html_code += f'<img src="{image_path}" alt="image"/>\n'
    # 拼接 HTML 尾部, 关闭 div 与 body、html 标签
    html_code += """</div>
        </body>
    </html>
    """
    # 返回完整的 HTML 代码
    return html_code


def generate_markdown_node(state: AgentState):
    """根据标题和内容生成markdown"""
    # 从 state 读取小红书标题, 缺失时为空字符串
    title = state.get('xiaohongshu_title', '')
    # 从 state 读取正文, 缺失时为空字符串
    content = state.get('xiaohongshu_content', '')
    # 从 state 读取图片路径列表, 缺失时为空列表
    image_path_list = state.get('xiaohongshu_image_path_list', [])
    # 从 state 读取发布提示(如"发布成功"/"发布失败"), 缺失时为空字符串
    tip = state.get('xiaohongshu_tip', '')
    # 调用 generate_markdown_code 生成 HTML 卡片代码
    markdown = generate_markdown_code(title, content, image_path_list)
    # 将纯 HTML 代码写入 state, 供需要原始 HTML 的下游使用
    state['xiaohongshu_markdown_output'] = markdown
    # 将"提示 + HTML"拼接后写入 output, 作为节点的通用输出供前端展示
    state['output'] = f"<p>{tip}</p>\n" + markdown
    # 返回更新后的 state
    return state


# 脚本直接运行时的自测入口
if __name__ == '__main__':
    # 调用 generate_markdown_code 生成测试 HTML, 打印结果用于人工检查格式
    print(generate_markdown_code("标题", "内容", []))
