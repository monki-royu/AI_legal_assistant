"""小红书图片生成节点"""
# ============================================================
# 文件名称: nodes/image_generate_node.py
# 文件作用: 图片生成
# ============================================================
# 【这个文件是干什么的？】
# 图片生成
#
# 【代码逻辑主线】
# 参见各函数前的【功能】【参数】【返回值】【逻辑】说明。
#
# 【新手建议】
# 先看主函数 -> 再看辅助函数。
#

# 📜 代码文字逻辑解析
# 本文件是 AI 法律助理(LangGraph 多智能体协作)流程中的"小红书图片生成节点",
# 适配到法律科普场景。它在文案生成
# 节点之后执行, 负责为小红书笔记生成一张法律科普风格的配图。核心逻辑采用"主备降级"
# 策略:1) 优先调用即梦 AI(火山引擎 VisualService)进行文生图, 需配置 JIMENG_AK/SK;
# 2) 若即梦 AI 不可用或生成失败, 则降级使用 matplotlib 生成一张包含天平图标与标题的
# 占位图;3) 文件名通过时间戳 + 标题前缀生成, 避免重名覆盖;4) 生成成功后将图片路径
# 列表与提示信息写入 state, 供后续自动发布节点使用。文件包含多个工具函数:
# sanitize_title_for_filename(文件名清洗)、generate_legal_image_prompt(提示词构造)、
# download_image_from_url(URL 下载)、generate_image(即梦 AI 生图)、
# generate_placeholder_image(占位图生成)、image_generator_node(节点入口)。该节点展示了
# "外部 AI 服务 + 本地降级方案"的容错设计, 可作为任何"AI 生图 + 兜底"场景的迁移模板。
# 导入 os 模块, 用于路径拼接与目录创建
import os
# 导入 datetime 模块, 用于生成时间戳作为文件名前缀
import datetime
# 导入 requests 模块, 用于下载即梦 AI 返回的图片 URL
import requests

# 导入 AgentState 类型, 它是整个 LangGraph 图中各节点共享的状态字典(TypedDict)
from __004__langgraph_more_nodes.agent_state import AgentState
# 导入项目配置类 Config, 用于读取即梦 AI 的 AK/SK
from common.config import Config
# 导入路径工具函数 get_file_path, 用于获取项目内标准目录的绝对路径
from common.path_utils import get_file_path

# 实例化全局配置对象, 后续通过 conf.JIMENG_AK / conf.JIMENG_SK 读取密钥
conf = Config()


def sanitize_title_for_filename(title: str, max_length: int = 10) -> str:
    """
    将标题转换为安全的图片文件名(时间戳 + 标题前缀 + .png)。

    作用:
        根据当前时间戳与标题前 5 个字符, 拼接出唯一且文件系统安全的图片文件名,
        避免不同次生成覆盖同名文件。

    参数:
        title (str): 小红书笔记标题。
        max_length (int): 标题部分的最大保留长度(实际实现固定取前 5 字符, 此参数保留以兼容签名)。

    返回值:
        str: 形如 "20260811120000劳动合同.png" 的文件名字符串。

    可迁移性说明:
        该函数是通用的"标题 → 安全文件名"工具, 可迁移到任何需要为生成文件命名的场景,
        建议迁移时增加非法字符过滤(如替换 / \\ : * ? " < > |)。
    """
    # 获取当前时间
    now = datetime.datetime.now()
    # 格式化为年月日时分秒字符串, 作为文件名前缀, 保证唯一性
    time_str = now.strftime("%Y%m%d%H%M%S")
    # 拼接时间戳 + 标题前 5 个字符 + .png 后缀, 返回最终文件名
    return time_str + title[:5] + ".png"


def generate_legal_image_prompt(title: str, content: str) -> str:
    """
    根据标题与内容构造法律科普风格的图片生成提示词。

    作用:
        为即梦 AI 文生图服务构造一段描述性提示词, 强调法律元素(天平、法槌、法律书籍等)、
        专业权威但有亲和力的氛围, 并明确要求图片中不能出现任何文字。

    参数:
        title (str): 小红书笔记标题, 作为图片主题。
        content (str): 小红书笔记正文(当前未直接用于提示词, 保留参数以备扩展)。

    返回值:
        str: 用于即梦 AI 的完整提示词字符串。

    可迁移性说明:
        该函数是"主题 → 文生图提示词"的构造器, 修改法律元素描述即可迁移到其他主题
        (如:医疗、教育、金融等)的配图生成场景。
    """
    # 返回一段多段拼接的提示词, 描述画面元素、氛围、色调与画风要求
    return (
        f"一幅围绕法律科普主题创作的图像，画面展现与标题内容相关的场景，"
        f"构图中可包含天平、法槌、法律书籍、合同文书、法庭等法律元素，"
        f"整体氛围专业、权威但有亲和力，色调沉稳大方，"
        f"表达正义、公平与法治的精神。"
        f"图片内容主题为:{title}"
        f"图片中不能有任何文字。"
        f"整体画面和谐、美观，符合图片质量要求。"
        f"允许画风为扁平插画、水彩或现代设计风格。"
    )


def download_image_from_url(url: str, output_path: str):
    """
    从给定 URL 下载图片并保存到本地路径。

    作用:
        即梦 AI 生成图片后会返回一个临时 URL, 本函数以流式方式下载该图片并写入本地文件,
        避免大文件占用过多内存。

    参数:
        url (str): 图片的下载 URL。
        output_path (str): 本地保存路径(含文件名)。

    返回值:
        None: 无返回值, 成功时打印保存路径, 失败时打印错误信息。

    可迁移性说明:
        该函数是通用的流式下载工具, 可迁移到任何"URL → 本地文件"的下载场景,
        建议迁移时增加重试与超时控制。
    """
    # 使用 try/except 包裹下载逻辑, 防止网络异常导致节点崩溃
    try:
        # 以流式方式发起 GET 请求, 设置 30 秒超时
        response = requests.get(url, stream=True, timeout=30)
        # 若 HTTP 状态码非 2xx, 抛出异常
        response.raise_for_status()
        # 以二进制写模式打开本地文件
        with open(output_path, 'wb') as out_file:
            # 分块读取响应内容(每块 8192 字节), 逐块写入文件, 降低内存占用
            for chunk in response.iter_content(chunk_size=8192):
                out_file.write(chunk)
        # 打印保存成功日志
        print(f"图片已保存：{output_path}")
    except Exception as e:
        # 捕获并打印下载异常, 不抛出, 由调用方决定后续处理
        print(f"下载失败：{e}")


def generate_image(prompt: str, output_path: str):
    """调用即梦AI生成图片(需配置JIMENG_AK/SK)"""
    # 使用 try/except 包裹即梦 AI 调用, 失败时返回 None 触发降级
    try:
        # 延迟导入火山引擎 VisualService, 避免未安装该包时影响整个模块加载
        from volcengine.visual.VisualService import VisualService
        # 实例化视觉服务客户端
        visual_service = VisualService()
        # 设置 Access Key
        visual_service.set_ak(conf.JIMENG_AK)
        # 设置 Secret Key
        visual_service.set_sk(conf.JIMENG_SK)

        # 构造即梦 AI 文生图请求参数
        form = {
            # 请求键名, 指定使用即梦文生图 v3.1 模型
            "req_key": "jimeng_t2i_v31",
            # 提示词
            "prompt": prompt,
            # 要求返回图片 URL(而非直接返回二进制)
            "return_url": True
        }
        # 调用视觉服务的通用处理接口, 发起文生图请求
        resp = visual_service.cv_process(form)
        # 从响应中提取图片 URL 列表, 嵌套在 data.image_urls 下
        image_urls = resp.get('data', {}).get('image_urls', [])
        # 若存在图片 URL, 下载第一张到本地
        if image_urls:
            download_image_from_url(image_urls[0], output_path)
            # 返回本地图片路径, 表示生成成功
            return output_path
        else:
            # 无有效 URL 时抛出运行时异常, 触发降级
            raise RuntimeError("图像生成失败，无有效图片链接返回")
    except Exception as e:
        # 打印即梦 AI 调用失败日志
        print(f"⚠️ 即梦AI生成失败: {e}")
        # 返回 None, 供调用方触发降级逻辑
        return None


def generate_placeholder_image(title: str, output_path: str):
    """生成占位图片(当AI生图不可用时)"""
    # 使用 try/except 包裹占位图生成逻辑, 失败时返回 None
    try:
        # 延迟导入 matplotlib, 避免未安装时影响整个模块加载
        import matplotlib.pyplot as plt
        # 导入 mpatches 用于绘制圆角矩形背景
        import matplotlib.patches as mpatches

        # 创建 8x8 英寸、100dpi 的画布与坐标轴
        fig, ax = plt.subplots(figsize=(8, 8), dpi=100)
        # 设置 x 轴范围为 0-8
        ax.set_xlim(0, 8)
        # 设置 y 轴范围为 0-8
        ax.set_ylim(0, 8)
        # 关闭坐标轴显示
        ax.axis('off')

        # 墨绿色背景
        # 绘制一个圆角矩形作为背景, 墨绿色填充(#1a3c34), 绿色边框(#4CAF50)
        rect = mpatches.FancyBboxPatch((0.5, 0.5), 7, 7, boxstyle="round,pad=0.3",
                                        facecolor='#1a3c34', edgecolor='#4CAF50', linewidth=3)
        # 将矩形添加到坐标轴
        ax.add_patch(rect)

        # 天平图标
        # 在画布上方居中绘制天平 emoji, 字号 80, 白色
        ax.text(4, 5.5, "⚖️", fontsize=80, ha='center', va='center', color='white')
        # 标题
        # 在画布中部绘制标题前 15 个字符, 字号 16, 白色加粗
        ax.text(4, 3, title[:15], fontsize=16, ha='center', va='center',
                color='white', fontweight='bold', wrap=True)
        # 在画布下方绘制"法智引擎"水印, 字号 12, 绿色加粗
        ax.text(4, 1.5, "法智引擎", fontsize=12, ha='center', va='center',
                color='#4CAF50', fontweight='bold')

        # 保存图片到指定路径, 紧凑裁剪边距, 背景色为墨绿色
        plt.savefig(output_path, dpi=100, bbox_inches='tight', facecolor='#1a3c34')
        # 关闭画布, 释放内存
        plt.close()
        # 返回本地图片路径, 表示生成成功
        return output_path
    except Exception as e:
        # 打印占位图生成失败日志
        print(f"⚠️ 占位图生成失败: {e}")
        # 返回 None, 表示生成失败
        return None


def image_generator_node(state: AgentState):
    """根据标题和内容生成法律科普风格的小红书配图"""
    # 打印日志, 标记进入图片生成阶段, 便于在控制台追踪节点执行顺序
    print("开始生成小红书图片")
    # 从 state 读取标题, 缺失时默认"法律科普"
    title = state.get('xiaohongshu_title', '法律科普')
    # 从 state 读取正文内容(当前未直接使用, 保留以备扩展)
    content = state.get('xiaohongshu_content', '')

    # 确保 assets/images 目录存在, exist_ok=True 表示已存在时不报错
    os.makedirs(get_file_path("assets/images"), exist_ok=True)
    # 根据标题生成安全的文件名
    file_name = sanitize_title_for_filename(title)
    # 拼接图片完整输出路径
    output_path = os.path.join(get_file_path("assets/images"), file_name)

    # 先尝试即梦AI
    # 初始化图片路径为 None, 用于判断是否生成成功
    image_path = None
    # 仅当配置了即梦 AI 的 AK 与 SK 时, 才尝试调用
    if conf.JIMENG_AK and conf.JIMENG_SK:
        # 构造法律科普风格的提示词
        prompt = generate_legal_image_prompt(title, content)
        # 调用即梦 AI 生成图片
        image_path = generate_image(prompt, output_path)

    # 降级: 占位图
    # 若即梦 AI 未配置或生成失败, 则降级使用占位图
    if not image_path:
        # 打印降级日志
        print("  使用占位图")
        # 调用 matplotlib 生成占位图
        image_path = generate_placeholder_image(title, output_path)

    # 根据最终是否生成成功, 写入不同的 state 字段
    if image_path:
        # 成功: 将图片路径列表写入 state(列表形式以兼容多图场景)
        state['xiaohongshu_image_path_list'] = [image_path]
        # 写入成功提示
        state['xiaohongshu_tip'] = "图片生成成功"
        # 打印成功日志
        print(f"图片生成成功: {image_path}")
    else:
        # 失败: 写入空列表
        state['xiaohongshu_image_path_list'] = []
        # 写入失败提示
        state['xiaohongshu_tip'] = "图片生成失败"
        # 打印失败日志
        print("图片生成失败")

    # 打印日志, 标记图片生成阶段完成
    print("完成生成小红书图片")
    # 返回更新后的 state, 供 LangGraph 继续流转
    return state


# 脚本直接运行时的自测入口
if __name__ == '__main__':
    # 构造一个包含标题与正文的测试 state, 直接调用节点进行图片生成
    image_generator_node(state=AgentState(
        xiaohongshu_title="劳动合同维权指南",
        xiaohongshu_content="劳动者权益保护..."
    ))
