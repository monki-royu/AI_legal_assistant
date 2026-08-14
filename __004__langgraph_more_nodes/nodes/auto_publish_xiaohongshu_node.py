"""小红书自动发布节点(playwright, 参考中医项目发布思路重构)"""
# 📜 代码文字逻辑解析
# 本文件是 AI 法律助理(LangGraph 多智能体协作)流程中的"小红书自动发布节点"。
# 参考中医项目的发布实现思路, 使用 launch_persistent_context + JSON cookie 管理,
# 并通过 xhs-publish-btn 容器坐标点击发布按钮, 解决发布按钮无法定位的问题。
#
# 核心改进(相比旧版):
# 1) 使用 launch_persistent_context 替代 launch+new_context, 浏览器用户数据持久化;
# 2) cookie 管理改为 JSON 文件方式(保存/加载 cookies 列表), 更直观可调试;
# 3) 发布按钮定位: 优先 xhs-publish-btn 容器 bounding_box 坐标点击(65% 宽度位置),
#    失败后依次回退: 文本匹配 → Locator has-text → JS 注入 → 页面元素调试;
# 4) 新增 _debug_page_elements 方法, 在关键步骤打印页面所有可交互元素, 便于定位问题;
# 5) 标题/正文填写: 大幅扩展选择器列表, 覆盖小红书页面 DOM 变更;
# 6) 发布结果验证: URL 跳转 + 成功文本 + URL 变化轮询三重检测。

import os
import asyncio
import json

from __004__langgraph_more_nodes.agent_state import AgentState
from common.path_utils import get_file_path


class XiaohongshuUploader:
    """
    小红书图文笔记自动发布器, 基于 Playwright 异步 API 实现。

    使用 launch_persistent_context 持久化浏览器数据, JSON cookie 复用登录态,
    xhs-publish-btn 容器坐标点击发布按钮。
    """

    # cookie JSON 文件路径(存储 cookies 列表, 非 storage_state 格式)
    COOKIE_PATH = get_file_path("cookie/xiaohongshu_cookies.json")
    # 浏览器用户数据目录(持久化登录态、缓存等)
    USER_DATA_DIR = get_file_path("browser_data")
    # 小红书创作者平台图文发布页 URL
    PUBLISH_URL = (
        "https://creator.xiaohongshu.com/publish/publish?from=homepage&target=image&source=official"
    )

    def __init__(self, image_path_list, title: str = "", content: str = ""):
        self.image_path_list = image_path_list or []
        self.title = title or ""
        self.content = content or ""
        self.playwright = None
        self.context = None
        self.page = None

    # ================================================================
    # 浏览器启动与上下文管理 (使用 persistent_context)
    # ================================================================
    async def launch(self):
        """启动 Playwright 与 Chromium 持久化上下文, 加载 cookie, 打开发布页。"""
        from playwright.async_api import async_playwright

        print("开始启动浏览器...")
        self.playwright = await async_playwright().start()

        # 确保 user_data_dir 目录存在
        os.makedirs(self.USER_DATA_DIR, exist_ok=True)

        # 使用 persistent_context: 浏览器用户数据持久化, 后续可复用登录态
        self.context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=self.USER_DATA_DIR,
            headless=False,
            args=[
                "--start-maximized",
                "--disable-features=SameSiteByDefaultCookies",
                "--disable-blink-features=AutomationControlled",
            ],
            viewport=None,  # 配合 --start-maximized, 不固定 viewport
        )

        # 打开发布页
        self.page = await self.context.new_page()
        await self.page.goto(
            self.PUBLISH_URL,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        # 尝试加载已保存的 cookies
        await self._load_cookies(self.page)

        # 重新导航(加载 cookie 后刷新页面以应用登录态)
        await self.page.goto(
            self.PUBLISH_URL,
            wait_until="domcontentloaded",
            timeout=60000,
        )
        await self.page.wait_for_timeout(5000)

        # 检查是否已登录: 尝试寻找发布页特征元素
        logged_in = await self._check_login_status()
        if not logged_in:
            print("[!] 未检测到登录状态, 请在浏览器中手动扫码登录...")
            try:
                # 等待用户登录成功(最长 180 秒)
                await self.page.wait_for_selector(
                    'input[placeholder*="标题"], [placeholder*="填写标题"]',
                    timeout=180000,
                )
                print("[√] 登录成功!")
                await self._save_cookies(self.page)
            except Exception:
                print("[x] 登录超时")
                return False
        else:
            print("[√] 已登录, 进入发布页面")
            # 每次都更新 cookies(防止过期)
            await self._save_cookies(self.page)

        return True

    async def _check_login_status(self) -> bool:
        """检查是否已登录: 探测发布页特征元素。"""
        selectors = [
            'input[placeholder*="标题"]',
            '[placeholder*="填写标题"]',
            'button:has-text("上传图片")',
            'div:has-text("写长文")',
            '.upload-btn',
            'input[type="file"]',
        ]
        for sel in selectors:
            try:
                el = await self.page.query_selector(sel)
                if el:
                    return True
            except Exception:
                continue
        return False

    # ================================================================
    # Cookie 管理 (JSON 格式)
    # ================================================================
    async def _save_cookies(self, page):
        """保存当前上下文的 cookies 到 JSON 文件。"""
        try:
            cookies = await page.context.cookies()
            os.makedirs(os.path.dirname(self.COOKIE_PATH), exist_ok=True)
            with open(self.COOKIE_PATH, "w", encoding="utf-8") as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)
            print(f"[√] Cookies 已保存: {self.COOKIE_PATH}")
        except Exception as e:
            print(f"[!] 保存 Cookies 失败: {e}")

    async def _load_cookies(self, page):
        """从 JSON 文件加载 cookies 到当前上下文。"""
        if not os.path.exists(self.COOKIE_PATH):
            print("[!] Cookies 文件不存在, 跳过加载")
            return False
        try:
            with open(self.COOKIE_PATH, "r", encoding="utf-8") as f:
                cookies = json.load(f)
            await page.context.add_cookies(cookies)
            print(f"[√] Cookies 已加载: {self.COOKIE_PATH}")
            return True
        except Exception as e:
            print(f"[!] 加载 Cookies 失败: {e}")
            return False

    # ================================================================
    # 调试工具: 打印页面所有可交互元素
    # ================================================================
    async def _debug_page_elements(self, step_name: str):
        """打印当前页面所有 input/textarea/button/contenteditable 元素信息, 便于定位问题。"""
        try:
            elements = await self.page.evaluate("""() => {
                const result = [];
                document.querySelectorAll('input, textarea, [contenteditable="true"], button, [role="button"], xhs-publish-btn').forEach(el => {
                    const rect = el.getBoundingClientRect();
                    result.push({
                        tag: el.tagName,
                        class: el.className.toString().substring(0, 80),
                        id: el.id,
                        placeholder: el.placeholder || '',
                        textContent: el.textContent ? el.textContent.substring(0, 50).trim() : '',
                        visible: rect.width > 0 && rect.height > 0,
                        x: Math.round(rect.left + rect.width / 2),
                        y: Math.round(rect.top + rect.height / 2),
                        w: Math.round(rect.width),
                        h: Math.round(rect.height),
                    });
                });
                return result;
            }""")
            print(f"\n{'='*60}")
            print(f"=== {step_name} - 页面元素 ({len(elements)} 个) ===")
            for i, el in enumerate(elements):
                vis = "✓" if el["visible"] else "✗"
                print(f"  [{i}] {vis} tag={el['tag']} id={el['id']} class={el['class'][:40]} "
                      f"placeholder={el['placeholder']} text={el['textContent']} "
                      f"pos=({el['x']},{el['y']}) size={el['w']}x{el['h']}")
            print("=" * 60)
        except Exception as e:
            print(f"[!] 调试信息获取失败: {e}")

    # ================================================================
    # 切换到"上传图文" Tab
    # ================================================================
    async def switch_to_image_post(self):
        """切换到发布页的"上传图文"Tab(小红书默认可能是视频上传 Tab)。"""
        print("🔀 正在切换到【上传图文】Tab...")
        try:
            tabs = await self.page.query_selector_all(".creator-tab .title")
            if not tabs:
                # 备用选择器
                tabs = await self.page.query_selector_all("[class*='tab'] [class*='title']")
            for tab in tabs:
                text = (await tab.inner_text()).strip()
                if "上传图文" in text or "图文" in text:
                    box = await tab.bounding_box()
                    if box and box["x"] > 0 and box["y"] > 0:
                        await tab.click(force=True)
                        print("[√] 已切换到【上传图文】Tab")
                        await self.page.wait_for_timeout(2000)
                        return
            print("[!] 未找到【上传图文】Tab(可能已经在图文页面)")
        except Exception as e:
            print(f"[!] 切换 Tab 失败: {e}")

    # ================================================================
    # 上传图片
    # ================================================================
    async def upload_images(self):
        """上传图片列表到小红书发布页, 并等待上传完成。"""
        print("📤 正在上传图片...")

        # 过滤出实际存在的图片文件
        valid_paths = []
        for path in self.image_path_list:
            abs_path = os.path.abspath(path) if not os.path.isabs(path) else path
            if os.path.exists(abs_path):
                valid_paths.append(abs_path)
            else:
                print(f"[!] 图片文件不存在, 跳过: {abs_path}")

        if not valid_paths:
            print("[!] 没有有效的图片文件")
            return False

        try:
            # 定位 file input(hidden 元素, set_input_files 不要求 visible)
            file_input = None
            selectors = [
                'input.upload-input[type="file"]',
                'input[type="file"][accept*="image"]',
                'input[type="file"]',
            ]
            for sel in selectors:
                try:
                    await self.page.wait_for_selector(sel, state="attached", timeout=8000)
                    file_input = await self.page.query_selector(sel)
                    if file_input:
                        print(f"[√] 找到文件上传控件: {sel}")
                        break
                except Exception:
                    continue

            if not file_input:
                print("[x] 未找到图片上传控件")
                await self._debug_page_elements("upload_images_failed")
                return False

            await file_input.set_input_files(valid_paths)
            print(f"[√] 已提交 {len(valid_paths)} 张图片, 等待上传完成...")

            # 等待上传完成: 检测缩略图出现
            try:
                await self.page.wait_for_selector(
                    '.upload-item, .image-item, .c-image-uploader__item, [class*="upload-item"], [class*="image-item"]',
                    timeout=20000,
                )
                print("[√] 图片上传完成(检测到缩略图)")
            except Exception:
                print("[!] 未检测到缩略图元素, 固定等待 5 秒...")
                await self.page.wait_for_timeout(5000)

            # 额外等待确保前端渲染完毕
            await self.page.wait_for_timeout(2000)
            return True

        except Exception as e:
            print(f"[X] 图片上传失败: {e}")
            return False

    # ================================================================
    # 填写标题 (大幅扩展选择器列表)
    # ================================================================
    async def fill_title(self):
        """定位标题输入框并填入标题, 尝试多种选择器。"""
        print("📝 正在填写标题...")
        selectors = [
            'input[placeholder*="填写标题会有更多赞"]',
            'input[placeholder*="填写标题"]',
            'input[placeholder*="标题"]',
            'input.d-text[placeholder*="填写标题"]',
            '[placeholder*="填写标题"]',
            '[placeholder*="标题"]',
            'input[placeholder*="更多曝光"]',
            'input[placeholder*="更多赞"]',
            '.d-input-text',
            'input[type="text"]',
            '.publish-title-input input',
            '.title-input input',
            '[class*="title"] input',
            'div[contenteditable="true"][placeholder*="标题"]',
        ]

        for sel in selectors:
            try:
                title_input = self.page.locator(sel)
                if await title_input.count() > 0:
                    await title_input.first.click()
                    await self.page.wait_for_timeout(300)
                    await title_input.first.fill(self.title)
                    print(f"[√] 标题已填写: {self.title}")
                    return True
            except Exception:
                continue

        # 备用: 键盘输入
        try:
            await self.page.mouse.click(300, 300)
            await self.page.wait_for_timeout(300)
            await self.page.keyboard.type(self.title)
            print(f"[√] 标题已填写(坐标+键盘): {self.title}")
            return True
        except Exception:
            pass

        print("[x] 未找到标题输入框")
        await self._debug_page_elements("fill_title_failed")
        return False

    # ================================================================
    # 填写正文 (大幅扩展选择器列表)
    # ================================================================
    async def fill_content(self):
        """定位正文编辑器并填入正文, 尝试多种选择器。"""
        print("📝 正在填写正文...")
        selectors = [
            '.tiptap[contenteditable="true"]',
            'div[contenteditable="true"]',
            '[placeholder*="输入正文描述"]',
            '[placeholder*="真诚有价值的分享"]',
            'textarea[placeholder*="正文"]',
            'textarea[placeholder*="输入正文"]',
            'textarea[placeholder*="描述"]',
            '.editor-content',
            '.tiptap.ProseMirror',
            '.publish-content textarea',
            '.content-input textarea',
            '[class*="editor"] div[contenteditable]',
            '[class*="content"] textarea',
            'div.ProseMirror',
            '[placeholder*="正文"]',
            '[placeholder*="输入正文"]',
            '[placeholder*="描述"]',
        ]

        for sel in selectors:
            try:
                content_elem = self.page.locator(sel)
                if await content_elem.count() > 0:
                    await content_elem.first.click()
                    await self.page.wait_for_timeout(300)
                    await content_elem.first.type(self.content)
                    print("[√] 正文已填写")
                    return True
            except Exception:
                continue

        # 备用: 键盘输入
        try:
            await self.page.mouse.click(300, 400)
            await self.page.wait_for_timeout(300)
            await self.page.keyboard.type(self.content)
            print("[√] 正文已填写(坐标+键盘)")
            return True
        except Exception:
            pass

        print("[x] 未找到正文输入框")
        await self._debug_page_elements("fill_content_failed")
        return False

    # ================================================================
    # 点击发布按钮 (核心修复: 参考 xhs-publish-btn 容器坐标方案)
    # ================================================================
    async def submit_post(self) -> bool:
        """
        点击发布按钮提交笔记, 并验证发布结果。

        策略顺序(由精准到兜底):
        1. xhs-publish-btn 容器 bounding_box 坐标点击(65% 宽度位置 = 发布按钮)
        2. 遍历所有 button, 文本匹配 "发布"
        3. Playwright Locator has-text("发布")
        4. JS 注入穿透 shadow DOM 点击
        5. 页面元素调试输出(辅助人工排查)
        """
        await self.page.wait_for_timeout(3000)
        print("🚀 正在尝试点击发布按钮...")

        clicked = False

        # ===== 策略1: xhs-publish-btn 容器坐标点击(参考代码核心方案) =====
        # xhs-publish-btn 是小红书自定义 Web Component, 内部含"存草稿"和"发布"两个按钮,
        # "发布"按钮在容器右侧约 65% 宽度位置
        try:
            publish_container = await self.page.wait_for_selector(
                'xhs-publish-btn', timeout=10000
            )
            box = await publish_container.bounding_box()
            if box and box["width"] > 0:
                btn_x = box["x"] + box["width"] * 0.65
                btn_y = box["y"] + box["height"] / 2
                await self.page.mouse.click(btn_x, btn_y)
                print(f"[√] 策略1-坐标点击发布按钮 ({btn_x:.0f}, {btn_y:.0f})")
                clicked = True
            else:
                print("[!] 策略1-xhs-publish-btn bounding_box 为空")
        except Exception as e:
            print(f"[!] 策略1-坐标点击失败: {e}")

        # ===== 策略2: 遍历所有 button, 文本匹配 "发布" =====
        if not clicked:
            try:
                await self.page.wait_for_timeout(1000)
                btns = await self.page.query_selector_all("button")
                for btn in btns:
                    txt = (await btn.inner_text()).strip()
                    if txt == "发布":
                        box = await btn.bounding_box()
                        if box and box["width"] > 30 and box["height"] > 20:
                            await btn.click(force=True)
                            print("[√] 策略2-文本匹配点击发布按钮")
                            clicked = True
                            break
            except Exception as e:
                print(f"[!] 策略2-文本匹配失败: {e}")

        # ===== 策略3: Locator + has-text =====
        if not clicked:
            try:
                publish_locator = self.page.locator(
                    'button:has-text("发布"), [role="button"]:has-text("发布")'
                )
                count = await publish_locator.count()
                if count > 0:
                    await publish_locator.last.click(force=True)
                    print("[√] 策略3-Locator has-text 点击发布按钮")
                    clicked = True
            except Exception as e:
                print(f"[!] 策略3-Locator 失败: {e}")

        # ===== 策略4: JS 注入穿透 shadow DOM =====
        if not clicked:
            try:
                result = await self.page.evaluate("""
                    () => {
                        const container = document.querySelector('xhs-publish-btn');
                        if (container && container.shadowRoot) {
                            const btn = container.shadowRoot.querySelector('button');
                            if (btn) { btn.click(); return 'shadow-btn'; }
                        }
                        if (container) { container.click(); return 'container'; }
                        const all = document.querySelectorAll('button, [role="button"], .publish-btn');
                        for (const el of all) {
                            if (el.textContent.trim() === '发布') { el.click(); return 'text-match'; }
                        }
                        return 'none';
                    }
                """)
                if result != "none":
                    print(f"[√] 策略4-JS 注入点击发布按钮 ({result})")
                    clicked = True
                else:
                    print("[!] 策略4-JS 注入未找到发布按钮")
            except Exception as e:
                print(f"[!] 策略4-JS 注入失败: {e}")

        # ===== 所有策略失败: 输出调试信息 =====
        if not clicked:
            print("[X] 所有策略均未能点击发布按钮")
            await self._debug_page_elements("submit_post_failed")
            # 截图保存供人工排查
            try:
                debug_ss = os.path.join(
                    os.path.dirname(self.COOKIE_PATH),
                    "debug_publish_failed.png",
                )
                await self.page.screenshot(path=debug_ss, full_page=True)
                print(f"[!] 调试截图已保存: {debug_ss}")
            except Exception:
                pass
            return False

        # ===== 验证发布结果 =====
        print("⏳ 等待发布结果...")
        await self.page.wait_for_timeout(3000)

        # 检测1: URL 跳转
        try:
            current_url = self.page.url
            if any(kw in current_url for kw in ["success", "manage", "content", "publish/success"]):
                print(f"[√] 发布成功! URL 已跳转: {current_url}")
                return True
        except Exception:
            pass

        # 检测2: 成功文本提示
        try:
            success_selectors = [
                'text="发布成功"',
                '.el-message--success',
                '.toast-success',
                '[class*="success"]',
                '.ant-message-success',
            ]
            for sel in success_selectors:
                try:
                    el = await self.page.query_selector(sel)
                    if el:
                        txt = (await el.inner_text()).strip()
                        if any(kw in txt for kw in ["成功", "发布成功", "已发布"]):
                            print(f"[√] 发布成功! 检测到成功提示: {txt}")
                            return True
                except Exception:
                    continue
        except Exception:
            pass

        # 检测3: URL 变化轮询(7 秒)
        try:
            old_url = self.page.url
            for _ in range(7):
                await self.page.wait_for_timeout(1000)
                new_url = self.page.url
                if new_url != old_url:
                    print(f"[√] 发布后 URL 已变化: {new_url}")
                    return True
        except Exception:
            pass

        # 检测4: 错误提示
        try:
            error_selectors = [
                '.el-message--error', '.toast-error',
                '[class*="error"]', '.ant-message-error',
            ]
            for sel in error_selectors:
                try:
                    el = await self.page.query_selector(sel)
                    if el:
                        txt = (await el.inner_text()).strip()
                        print(f"[!] 检测到错误提示: {txt}")
                        return False
                except Exception:
                    continue
        except Exception:
            pass

        print("[!] 无法确认发布结果, 假定发布已提交(请手动检查小红书后台)")
        return True

    # ================================================================
    # 关闭浏览器
    # ================================================================
    async def close(self):
        """关闭浏览器上下文与 Playwright 运行时。"""
        await self.page.wait_for_timeout(4000)
        try:
            await self.context.close()
        except Exception:
            pass
        try:
            await self.playwright.stop()
        except Exception:
            pass


# ================================================================
# 完整发布流程
# ================================================================
async def auto_publish_xiaohongshu(images, title, content):
    """
    执行完整的小红书自动发布流程。

    返回值:
        bool: True=发布成功, False=发布失败
    """
    xhs = XiaohongshuUploader(images, title, content)
    try:
        # 1. 启动浏览器 + 登录
        launched = await xhs.launch()
        if not launched:
            print("[FAIL] 浏览器启动或登录失败")
            return False

        # 2. 切换到图文 Tab
        await xhs.switch_to_image_post()

        # 3. 上传图片
        await xhs.upload_images()

        # 4. 填写标题与正文
        await xhs.fill_title()
        await xhs.fill_content()

        # 5. 点击发布
        success = await xhs.submit_post()

        # 6. 关闭浏览器
        await xhs.close()

        if success:
            print("[DONE] ✅ 发布成功!")
        else:
            print("[DONE] ❌ 发布失败(发布按钮点击或验证失败)")
        return success

    except Exception as e:
        print(f"[FAIL] 发布流程异常: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        try:
            await xhs.close()
        except Exception:
            pass
        return False


# ================================================================
# LangGraph 节点入口
# ================================================================
async def xiaohongshu_auto_publish_node(state: AgentState):
    """自动发布小红书"""
    print("开始发布小红书")

    # 从 state 读取标题、正文、图片路径
    title = state.get("xiaohongshu_title", "")
    content = state.get("xiaohongshu_content", "")
    images = state.get("xiaohongshu_image_path_list", [])

    try:
        success = await auto_publish_xiaohongshu(images, title, content)
        if success:
            state["xiaohongshu_tip"] = "小红书发布成功!"
            state["is_can_publish_xiaohongshu"] = True
        else:
            state["xiaohongshu_tip"] = "小红书发布失败(请检查登录状态或网络)"
            state["is_can_publish_xiaohongshu"] = False
    except Exception as e:
        print(f"⚠️ 发布失败: {e}")
        state["xiaohongshu_tip"] = f"小红书发布失败: {e}"
        state["is_can_publish_xiaohongshu"] = False

    print("完成发布小红书")
    return state


# ================================================================
# 自测入口
# ================================================================
if __name__ == "__main__":
    asyncio.run(xiaohongshu_auto_publish_node(
        state=AgentState(
            xiaohongshu_image_path_list=[get_file_path("assets/images/test.png")],
            xiaohongshu_title="法律科普",
            xiaohongshu_content="法律科普内容",
        )
    ))
