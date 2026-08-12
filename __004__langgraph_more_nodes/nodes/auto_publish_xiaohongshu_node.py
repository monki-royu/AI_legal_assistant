"""小红书自动发布节点(playwright, 仿中医auto_publish_xiaohongshu_node)"""
# 📜 代码文字逻辑解析
# 本文件是 AI 法律助理(LangGraph 多智能体协作)流程中的"小红书自动发布节点",
# 借鉴自中医项目的 auto_publish_xiaohongshu_node 设计。它在发布前检查节点通过后
# 执行, 负责通过 Playwright 浏览器自动化将生成好的图文笔记发布到小红书创作者平台。
# 核心逻辑由两部分组成:1) XiaohongshuUploader 类封装了完整的发布流程, 包括启动
# 浏览器、加载/保存登录状态(cookie 复用)、切换到"上传图文"Tab、上传图片、填写标题
# 与正文、点击发布按钮、关闭浏览器等步骤, 每步均带异常捕获与日志输出;2) 节点入口
# 函数 xiaohongshu_auto_publish_node 从 state 读取标题、正文、图片列表, 调用
# auto_publish_xiaohongshu 异步函数完成发布, 并将结果提示写入 state。
# 该节点使用了 Playwright 的 async API, 通过 asyncio.run 在同步节点中驱动异步流程;
# 登录状态通过 storage_state 持久化到本地 JSON 文件, 首次运行需手动扫码登录,
# 后续可直接复用 cookie 免登录。该节点展示了"RPA 自动化发布"的完整实现,
# 可作为任何"浏览器自动化操作第三方平台"场景的迁移模板。
# 导入 os 模块, 用于路径存在性检查与目录创建
import os
# 导入 asyncio 模块, 用于在同步节点中驱动异步发布流程
import asyncio

# 导入 AgentState 类型, 它是整个 LangGraph 图中各节点共享的状态字典(TypedDict)
from __004__langgraph_more_nodes.agent_state import AgentState
# 导入路径工具函数 get_file_path, 用于获取 cookie 文件的标准存储路径
from common.path_utils import get_file_path


class XiaohongshuUploader:
    """
    小红书图文笔记自动发布器, 基于 Playwright 异步 API 实现。

    作用:
        封装从启动浏览器到点击发布的完整自动化流程, 支持 cookie 复用免登录,
        适用于小红书创作者平台的图文笔记发布。

    可迁移性说明:
        该类是通用的"Playwright 浏览器自动化操作"模板, 修改选择器与目标 URL
        即可迁移到其他需要 RPA 操作的第三方平台(如:微博、知乎、B站等)。
    """
    # cookie 状态文件的存储路径, 用于持久化登录状态以实现免登录
    COOKIE_PATH = get_file_path("cookie/xiaohongshu_cookie_state.json")
    # 小红书创作者平台图文发布页 URL
    PUBLISH_URL = (
        "https://creator.xiaohongshu.com/publish/publish?from=homepage&target=image&source=official"
    )

    def __init__(self, image_path_list, title: str = "", content: str = ""):
        """
        初始化上传器实例。

        参数:
            image_path_list (list[str]): 待上传图片的本地路径列表。
            title (str): 笔记标题。
            content (str): 笔记正文。
        """
        # 保存图片路径列表
        self.image_path_list = image_path_list
        # 保存标题
        self.title = title
        # 保存正文
        self.content = content
        # Playwright 运行时实例, 初始为 None, 在 launch 中创建
        self.playwright = None
        # 浏览器实例, 初始为 None
        self.browser = None
        # 浏览器上下文(会话), 初始为 None
        self.context = None
        # 当前操作的页面, 初始为 None
        self.page = None

    async def launch(self):
        """
        启动 Playwright 与 Chromium 浏览器, 加载或创建登录上下文, 并打开发布页。

        作用:
            若本地存在已保存的 cookie 状态文件, 则复用登录态;否则创建新上下文,
            暂停等待用户手动扫码登录, 登录成功后保存 cookie 以备下次复用。
        """
        # 延迟导入 Playwright 异步 API, 避免未安装时影响整个模块加载
        from playwright.async_api import async_playwright
        # 打印启动日志
        print("开始启动")
        # 启动 Playwright 运行时
        self.playwright = await async_playwright().start()
        # 启动 Chromium 浏览器, headless=False 表示有界面模式(便于人工扫码与调试)
        self.browser = await self.playwright.chromium.launch(headless=False)

        # 检查本地是否已存在登录状态文件
        if os.path.exists(self.COOKIE_PATH):
            # 打印加载提示
            print("[√] 加载已保存的登录状态...")
            # 使用 storage_state 复用已保存的 cookie, 同时设置地理位置权限(上海坐标)
            self.context = await self.browser.new_context(
                storage_state=self.COOKIE_PATH,
                permissions=["geolocation"],
                geolocation={"latitude": 31.2304, "longitude": 121.4737},
            )
        else:
            # 打印首次登录提示
            print("[!] 未检测到登录状态，创建新上下文...")
            # 创建新上下文(无 cookie), 仅设置地理位置权限
            self.context = await self.browser.new_context(
                permissions=["geolocation"],
                geolocation={"latitude": 31.2304, "longitude": 121.4737},
            )

        # 在上下文中新建一个页面
        self.page = await self.context.new_page()
        # 导航到小红书图文发布页
        await self.page.goto(self.PUBLISH_URL)

        # 若不存在 cookie 文件, 需要用户手动登录
        if not os.path.exists(self.COOKIE_PATH):
            # 阻塞等待用户手动扫码登录后按回车继续(input 在异步环境中会阻塞事件循环, 但此处为简化实现)
            input("请手动登录后按回车继续...")
            # 确保 cookie 目录存在
            os.makedirs(os.path.dirname(self.COOKIE_PATH), exist_ok=True)
            # 将当前上下文的登录状态(cookie + localStorage)保存到文件, 供下次复用
            await self.context.storage_state(path=self.COOKIE_PATH)
            # 打印保存成功提示
            print("[√] 登录状态已保存")
        # 等待 1 秒, 让页面充分加载
        await self.wait_seconds(1)

    async def switch_to_image_post(self):
        """
        切换到发布页的"上传图文"Tab。

        作用:
            小红书发布页默认可能是视频上传 Tab, 需点击切换到图文上传 Tab,
            通过遍历所有 Tab 元素, 找到文本含"上传图文"且位置有效的 Tab 并点击。
        """
        # 打印切换日志
        print("🔀 正在切换到【上传图文】Tab...")
        # 使用 try/except 包裹切换逻辑, 防止选择器找不到元素导致崩溃
        try:
            # 等待 Tab 元素出现, 超时 10 秒
            await self.page.wait_for_selector(".creator-tab .title", timeout=10000)
            # 查询所有 Tab 元素
            tabs = await self.page.query_selector_all(".creator-tab .title")
            # 初始化目标 Tab 为 None
            target_tab = None
            # 遍历所有 Tab, 寻找"上传图文"
            for tab in tabs:
                # 获取 Tab 文本并去除空白
                text = (await tab.inner_text()).strip()
                # 检查文本是否包含"上传图文"
                if "上传图文" in text:
                    # 获取 Tab 的 bounding box, 用于判断是否可见且可点击
                    box = await tab.bounding_box()
                    # 仅当 Tab 在可视区域(x>0, y>0)时才选为目标
                    if box and box["x"] > 0 and box["y"] > 0:
                        target_tab = tab
                        break
            # 若找到目标 Tab, 则强制点击(force=True 忽略遮挡检查)
            if target_tab:
                await target_tab.click(force=True)
                # 打印切换成功日志
                print("[√] 已成功切换到【上传图文】Tab")
            else:
                # 打印未找到可点击 Tab 的提示
                print("[x] 未找到可点击的【上传图文】Tab")
        except Exception as e:
            # 打印切换失败日志
            print(f"[X] 切换失败: {e}")

    async def upload_images(self):
        """
        上传图片列表到小红书发布页。

        作用:
            定位页面中的文件上传 input 元素, 通过 set_input_files 一次性上传所有图片。
        """
        # 打印上传日志
        print("📤 正在上传图片...")
        # 使用 try/except 包裹上传逻辑
        try:
            # 等待文件上传 input 元素附加到 DOM(state="attached"), 超时 10 秒
            await self.page.wait_for_selector('input.upload-input[type="file"]', state="attached", timeout=10000)
            # 查询文件上传 input 元素
            file_input = await self.page.query_selector('input.upload-input[type="file"]')
            # 若找到 input, 则设置待上传的文件列表
            if file_input:
                await file_input.set_input_files(self.image_path_list)
                # 打印上传成功日志, 含图片数量
                print(f"[√] 已上传 {len(self.image_path_list)} 张图片")
            else:
                # 打印未找到 input 的提示
                print("[x] 未找到图片上传输入框")
        except Exception as e:
            # 打印上传失败日志
            print(f"[X] 图片上传失败: {e}")

    async def fill_title_and_content(self):
        """
        填写笔记标题与正文。

        作用:
            定位标题输入框与正文编辑器(tiptap 富文本), 分别填入 title 与 content。
            标题使用 fill 方法, 正文使用 click + type 模拟键盘输入。
        """
        # 打印填写日志
        print("📝 正在填写标题和正文...")
        # 标题填写: 使用 try/except 包裹
        try:
            # 等待标题输入框出现(通过 placeholder 含"填写标题"定位), 超时 10 秒
            title_input = await self.page.wait_for_selector(
                'input.d-text[placeholder*="填写标题"]', timeout=10000
            )
            # 填入标题
            await title_input.fill(self.title)
            # 打印填写成功日志
            print(f"[√] 标题已填写：{self.title}")
        except Exception:
            # 打印未找到标题输入框的提示
            print("[x] 未找到标题输入框")
        # 正文填写: 使用 try/except 包裹
        try:
            # 等待 tiptap 富文本编辑器出现(contenteditable="true"), 超时 10 秒
            editor = await self.page.wait_for_selector(
                '.tiptap[contenteditable="true"]', timeout=10000
            )
            # 先点击编辑器使其获得焦点
            await editor.click()
            # 模拟键盘逐字输入正文
            await editor.type(self.content)
            # 打印填写成功日志
            print(f"[√] 正文已填写")
        except Exception:
            # 打印未找到正文编辑器的提示
            print("[x] 未找到正文编辑器")

    async def submit_post(self):
        """
        点击发布按钮提交笔记。

        作用:
            由于小红书发布按钮是自定义 Web Component(<xhs-publish-btn>), 无法直接
            点击内部按钮, 故采用"获取容器 bounding box + 计算偏移坐标 + 鼠标点击"
            的方式触发发布。
        """
        # 等待 3 秒, 确保标题与正文已渲染完毕
        await self.wait_seconds(3)
        # 打印发布日志
        print("🚀 正在尝试点击发布按钮...")
        # 使用 try/except 包裹点击逻辑
        try:
            # 等待发布按钮容器出现, 超时 10 秒
            publish_container = await self.page.wait_for_selector('xhs-publish-btn', timeout=10000)
            # 获取容器的 bounding box(位置与尺寸)
            box = await publish_container.bounding_box()
            # 若获取到 box, 则通过坐标点击
            if box:
                # 计算点击 x 坐标: 容器左侧偏移 65% 宽度(发布按钮通常在右侧)
                btn_x = box["x"] + box["width"] * 0.65
                # 计算点击 y 坐标: 容器垂直居中
                btn_y = box["y"] + box["height"] / 2
                # 通过鼠标点击指定坐标
                await self.page.mouse.click(btn_x, btn_y)
                # 打印点击成功日志, 含坐标
                print(f"[√] 已通过坐标点击发布按钮 ({btn_x:.0f}, {btn_y:.0f})")
                # 直接返回, 结束发布
                return
        except Exception as e:
            # 打印坐标点击失败日志
            print(f"坐标点击失败: {e}")

    async def close(self):
        """
        关闭浏览器与 Playwright 运行时, 释放资源。

        作用:
            在发布完成后等待 4 秒(确保请求发出), 然后依次关闭浏览器与停止 Playwright。
        """
        # 等待 4 秒, 确保发布请求已发出
        await self.wait_seconds(4)
        # 关闭浏览器
        await self.browser.close()
        # 停止 Playwright 运行时
        await self.playwright.stop()

    async def wait_seconds(self, seconds):
        """
        异步等待指定秒数。

        参数:
            seconds (int/float): 等待时长(秒)。

        作用:
            封装 Playwright 的 wait_for_timeout, 便于在流程中插入固定等待。
        """
        # 打印等待日志
        print(f"⏳ 等待 {seconds} 秒...")
        # 调用 Playwright 的毫秒级等待
        await self.page.wait_for_timeout(seconds * 1000)


async def auto_publish_xiaohongshu(images, title, content):
    """
    执行完整的小红书自动发布流程(异步)。

    作用:
        作为 XiaohongshuUploader 的流程编排函数, 按顺序调用各步骤完成发布。

    参数:
        images (list[str]): 图片路径列表。
        title (str): 笔记标题。
        content (str): 笔记正文。

    返回值:
        None: 无返回值, 发布结果通过日志与异常体现。
    """
    # 实例化上传器
    xhs = XiaohongshuUploader(images, title, content)
    # 启动浏览器并加载登录态
    await xhs.launch()
    # 切换到图文上传 Tab
    await xhs.switch_to_image_post()
    # 上传图片
    await xhs.upload_images()
    # 填写标题与正文
    await xhs.fill_title_and_content()
    # 点击发布
    await xhs.submit_post()
    # 关闭浏览器
    await xhs.close()


async def xiaohongshu_auto_publish_node(state: AgentState):
    """自动发布小红书"""
    # 打印日志, 标记进入发布阶段
    print("开始发布小红书")
    # 从 state 读取标题, 缺失时为空字符串
    title = state.get("xiaohongshu_title", "")
    # 从 state 读取正文, 缺失时为空字符串
    content = state.get("xiaohongshu_content", "")
    # 从 state 读取图片路径列表, 缺失时为空列表
    images = state.get("xiaohongshu_image_path_list", [])

    # 使用 try/except 包裹发布流程, 失败时写入友好提示而非抛出异常
    try:
        # 调用异步发布函数
        await auto_publish_xiaohongshu(images, title, content)
        # 发布成功, 写入成功提示
        state["xiaohongshu_tip"] = "小红书发布成功！"
    except Exception as e:
        # 打印失败日志
        print(f"⚠️ 发布失败: {e}")
        # 写入失败提示, 含异常信息
        state["xiaohongshu_tip"] = f"小红书发布失败: {e}"
    # 打印日志, 标记发布阶段完成
    print("完成发布小红书")
    # 返回更新后的 state
    return state


# 脚本直接运行时的自测入口
if __name__ == "__main__":
    # 通过 asyncio.run 驱动异步节点, 使用测试图片与文案
    asyncio.run(xiaohongshu_auto_publish_node(
        state=AgentState(
            xiaohongshu_image_path_list=[get_file_path("picture/test.png")],
            xiaohongshu_title="法律科普",
            xiaohongshu_content="法律科普内容"
        )))
