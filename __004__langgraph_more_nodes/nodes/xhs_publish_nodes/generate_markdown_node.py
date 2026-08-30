"""【文件作用】小红书发布结果 Markdown 生成节点 ── 将标题/正文/图片拼装为 HTML 代码供前端展示
【逻辑】本文件是 AI 法律助理(LangGraph 多智能体系统)中小红书发布流程的收尾节点，
    借鉴自中医项目的 generate_markdown_node 设计思路。核心流程：
    1. 从 【state】 中读取小红书标题（xiaohongshu_title）、正文（xiaohongshu_content）、
       图片路径列表（xiaohongshu_image_path_list）、发布提示（xiaohongshu_tip）
    2. 调用 trans_image_path_list() 将本地图片绝对路径转换为可被本地静态服务器访问的 URL
       （转换规则：绝对路径 → 相对于项目根目录的相对路径 → 拼接 localhost:8000 前缀）
    3. 调用 generate_markdown_code() 将标题/正文/图片URL拼装为一段 HTML 卡片代码
       （HTML 包含标题显示、正文展示、flex 布局的图片容器）
    4. 将纯 HTML 写入 state["xiaohongshu_markdown_output"]
    5. 将"提示 + HTML"拼接后写入 state["output"]，作为前端展示内容
    6. 本节点是纯字符串拼装逻辑，不调用 LLM 或外部服务

【可迁移性】本节点可作为任何"结构化数据 → HTML 卡片"场景的迁移模板，
    例如：报告生成、商品卡片、作品展示等。
"""

# ============================================================
# 📦 导入模块
# ============================================================

# 导入 os 模块，用于将绝对路径转换为相对路径（os.path.relpath）
import os

# 从同包导入 AgentState（【代理状态】类型），它是整个 LangGraph 图中各节点共享的状态字典(TypedDict)
from __004__langgraph_more_nodes.agent_state import AgentState

# 从 common.path_utils 导入路径工具函数
# root_path：项目根目录的绝对路径（字符串）
# get_file_path：用于获取标准子目录路径的函数
from common.path_utils import root_path, get_file_path


def trans_image_path_list(image_path_list: list):
    """
    【功能】将本地图片绝对路径列表转换为可被本地静态服务器访问的 URL 列表
    【参数】image_path_list (list[str])【本地图片绝对路径列表】：如 ["C:\\project\\images\\pic1.png", ...]
    【返回值】list[str]【URL 列表】：如 ["http://localhost:8000/images/pic1.png", ...]
    【逻辑】对列表中的每个图片路径执行以下转换：
            ① 用 os.path.relpath() 计算相对于项目根目录的相对路径
            ② 拼接 localhost:8000 前缀形成可访问的 URL
            ③ 返回转换后的 URL 列表
    【为什么需要转换？】前端无法通过本地绝对路径访问图片（浏览器安全策略），
            需要将绝对路径转换为可通过 HTTP 请求访问的 URL。
    【可迁移性】本函数是"本地路径 → 静态服务 URL"的通用转换器，
            修改前缀（如改为正式域名 https://cdn.example.com）即可迁移到生产环境。
    """
    # 定义单条路径的转换函数（内部函数，闭包捕获 root_path 变量）
    def trans_image_path(image_path):
        """
        【功能】内部辅助函数：将单条本地图片路径转换为静态服务器 URL
        【参数】image_path (str)【单条图片绝对路径】
        【返回值】str【转换后的 URL 字符串】
        【逻辑】os.path.relpath(绝对路径, 起始路径) 计算从起始路径到绝对路径的相对路径
        """
        # 计算图片路径相对于项目根目录的相对路径
        # os.path.relpath() 的机制：以 root_path() 为基准，计算 image_path 的相对位置
        # 示例：image_path="C:\\project\\images\\pic.png", root_path="C:\\project"
        # → relative_path = "images\\pic.png"（Windows）或 "images/pic.png"（Unix）
        # 注意: path_utils.root_path 是函数(动态计算项目根), 必须调用而非当字符串用
        relative_path = os.path.relpath(image_path, root_path())  # 相对路径

        # 拼接本地静态服务器前缀，返回完整 URL
        # 【前缀说明】localhost:8000 是本地开发静态服务器的默认地址
        # 生产环境应替换为实际服务器域名
        return f"http://localhost:8000/{relative_path}"  # 完整 URL

    # 对列表中的每个图片路径应用 trans_image_path 转换函数
    # 列表推导式 [func(p) for p in list] 逐元素转换，返回新列表
    return [trans_image_path(p) for p in image_path_list]


def generate_markdown_code(title, content, image_path_list, image_width="300px", image_height="300px"):
    """
    【功能】根据标题、正文与图片列表生成一段 HTML 卡片代码
    【参数】
        title (str)【笔记标题】：小红书笔记的标题文本
        content (str)【笔记正文】：小红书笔记的正文内容
        image_path_list (list[str])【图片路径列表】：本地图片路径（会被转换为 URL）
        image_width (str)【图片宽度】：CSS 宽度值，默认 "300px"
        image_height (str)【图片高度】：CSS 高度值，默认 "300px"
    【返回值】str【完整的 HTML 代码字符串】：包含标题、正文、flex 布局图片容器的 HTML 页面
    【逻辑】① 调用 trans_image_path_list 将图片路径转为 URL
            ② 使用 f-string 拼接 HTML 头部（含 CSS 样式）
            ③ 遍历图片 URL 列表，为每张图片生成 <img> 标签
            ④ 拼接 HTML 尾部，返回完整 HTML 字符串
    【可迁移性】本函数是"数据 → HTML 卡片"的通用拼装器，
            修改 HTML 模板即可迁移到任何需要在前端展示结构化成果的场景。
    """
    # 第一步：将本地图片路径列表转换为可访问的 URL 列表
    # 覆盖入参的 image_path_list，后续使用 URL 列表而非原始路径列表
    image_path_list = trans_image_path_list(image_path_list)  # 转换为 URL 列表

    # 第二步：使用 f-string 拼接 HTML 代码
    # 【注意】CSS 样式中的花括号 {} 在 f-string 中需要用 {{ 和 }} 转义
    # 例如：.image-container {{ display: flex; }} → 实际渲染为 .image-container { display: flex; }
    html_code = f"""
    <html>
        <head>
            <title>{title}</title>
            <style>
                .image-container {{
                    display: flex;           /* flex 布局实现图片横向排列 */
                    gap: 10px;               /* 图片间距 10px */
                    flex-wrap: wrap;         /* 超出换行，适应不同屏幕宽度 */
                    justify-content: flex-start;  /* 左对齐排列 */
                }}
                .image-container img {{
                    width: {image_width};    /* 图片宽度（可自定义） */
                    height: {image_height};  /* 图片高度（可自定义） */
                }}
            </style>
        </head>
        <body>
            <p>小红书发布成功</p>
            <h3>标题：{title}</h3>
            <p>内容：{content}</p>
            <div class="image-container">
    """

    # 第三步：遍历图片 URL 列表，为每张图片生成 <img> 标签
    for image_path in image_path_list:
        # 拼接 <img> 标签，src 指向图片 URL，alt 为图片描述（空字符串）
        # 每张图片占一行，HTML 自动忽略换行符
        html_code += f'<img src="{image_path}" alt="image"/>\\n'

    # 第四步：拼接 HTML 尾部，关闭所有标签
    # 关闭 div → 关闭 body → 关闭 html，保证 HTML 结构完整
    html_code += """</div>
        </body>
    </html>
    """

    # 返回完整的 HTML 代码字符串
    return html_code


def generate_markdown_node(state: AgentState):
    """
    【功能】节点入口函数：根据 state 中的小红书数据生成 HTML，写入 state["xiaohongshu_markdown_output"] 和 state["output"]
    【参数】state (AgentState)：LangGraph 共享状态字典，读取以下字段：
                - xiaohongshu_title (str)【小红书标题】：笔记标题
                - xiaohongshu_content (str)【小红书正文】：笔记正文内容
                - xiaohongshu_image_path_list (list[str])【图片路径列表】：本地图片绝对路径
                - xiaohongshu_tip (str)【发布提示】：如"发布成功"/"发布失败"等
            写入字段：
                - xiaohongshu_markdown_output (str)【纯 HTML 代码】：供需要原始 HTML 的下游使用
                - output (str)【拼接后的输出】："提示 + HTML"，供前端直接展示
    【返回值】AgentState：更新后的状态字典
    【逻辑】① 从 state 读取四个小红书相关字段
            ② 调用 generate_markdown_code 生成 HTML 卡片代码
            ③ 将纯 HTML 写入 state["xiaohongshu_markdown_output"]
            ④ 将"提示 + HTML"拼接后写入 state["output"]
            ⑤ 返回更新后的 state
    【注意】本节点不调用 LLM，纯字符串拼装，性能高且无额外成本。
    """
    # 从 state 读取小红书标题，缺失时默认为空字符串
    title = state.get('xiaohongshu_title', '')  # 笔记标题

    # 从 state 读取小红书正文，缺失时默认为空字符串
    content = state.get('xiaohongshu_content', '')  # 笔记正文

    # 从 state 读取图片路径列表，缺失时默认为空列表
    image_path_list = state.get('xiaohongshu_image_path_list', [])  # 图片路径列表

    # 从 state 读取发布提示（如"发布成功" / "发布失败"），缺失时默认为空字符串
    tip = state.get('xiaohongshu_tip', '')  # 发布提示

    # 调用 generate_markdown_code 生成 HTML 卡片代码
    # 使用默认图片尺寸 300px × 300px
    markdown = generate_markdown_code(title, content, image_path_list)  # 生成的 HTML 代码

    # 将纯 HTML 代码写入 state["xiaohongshu_markdown_output"]
    # 供需要原始 HTML 的下游组件使用（如邮件发送、PDF 生成等）
    state['xiaohongshu_markdown_output'] = markdown  # 纯 HTML 输出

    # 将"提示 + HTML"拼接后写入 state["output"]
    # 这是节点的通用输出字段，前端直接读取并渲染此字段
    # f-string 将 tip 放入 <p> 标签，再拼接 markdown HTML
    state['output'] = f"<p>{tip}</p>\\n" + markdown  # 提示 + HTML 拼接输出

    # 返回更新后的 state
    return state


# ============================================================
# 🧪 模块自测入口（仅在直接运行本文件时执行）
# ============================================================
if __name__ == '__main__':
    # 调用 generate_markdown_code 生成测试 HTML
    # 传入空标题、空内容、空图片列表，检查基础 HTML 结构是否完整
    # 打印结果用于人工检查格式（应包含完整的 html/head/body 结构）
    print(generate_markdown_code("测试标题", "测试内容", []))