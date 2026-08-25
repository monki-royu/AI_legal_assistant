# -*- coding: utf-8 -*-
"""
小红书自动发布独立脚本（供 Streamlit 通过 subprocess 调用，与 Streamlit 运行时彻底隔离）。

解决的问题：
    Streamlit ScriptRunner 线程的 asyncio 事件循环不支持创建子进程（Windows 下会抛
    NotImplementedError），导致 Playwright 无法启动 Chromium。将发布流程隔离到独立
    Python 进程可完全绕过这个问题。

使用方式（命令行）：
    set PYTHONIOENCODING=utf-8
    python xhs_publish_runner.py --images "path1.png,path2.png" --title "标题" --content "正文"

退出码：
    0  成功
    1  失败（stdout 末尾包含错误信息）
"""
import argparse
import os
import sys

# ------------- 强制 stdout/stderr 为 UTF-8，防止 GBK 编码报错 -------------
if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        import io as _io
        sys.stderr = _io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

# 把项目根目录插入 sys.path，确保能 import 项目内模块
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 直接复用 __004__langgraph_more_nodes.nodes.auto_publish_xiaohongshu_node 中的
# XiaohongshuUploader / auto_publish_xiaohongshu，逻辑与 LangGraph 节点一致
# (参考 xhs_auto_publish_node.py 第 178 行 auto_publish_xiaohongshu 函数)


def main():
    parser = argparse.ArgumentParser(description="小红书自动发布独立 Runner")
    parser.add_argument("--images", required=True, help="图片路径列表，用英文逗号分隔")
    parser.add_argument("--title", required=True, help="笔记标题")
    parser.add_argument("--content", required=True, help="笔记正文")
    parser.add_argument("--timeout", type=int, default=300, help="单步超时秒数（默认300）")
    args = parser.parse_args()

    # Windows 下设置兼容的事件循环策略（必须在任何 asyncio 操作之前执行）
    if sys.platform.startswith("win"):
        import asyncio
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        except Exception as _e:
            print(f"[WARN] 设置 WindowsProactorEventLoopPolicy 失败: {_e}")

    # 解析图片路径
    image_paths = [p.strip() for p in args.images.split(",") if p.strip()]
    for fp in image_paths:
        if not os.path.exists(fp):
            print(f"[FAIL] 图片文件不存在: {fp}")
            return 1
    if not image_paths:
        print("[FAIL] 图片路径为空")
        return 1

    title = args.title
    content = args.content

    # 安全输出：截断过长的 title/content 避免刷屏
    _title_preview = title if len(title) <= 60 else title[:57] + "..."
    print(f"[INFO] 图片: {image_paths}")
    print(f"[INFO] 标题: {_title_preview}")
    print(f"[INFO] 正文长度: {len(content)} 字符")
    print("[INFO] 正在导入 Playwright 与 XiaohongshuUploader ...")

    try:
        import asyncio
        # 延迟导入 Playwright 模块，避免未安装时影响参数解析
        try:
            from playwright.async_api import async_playwright  # noqa: F401  确保 Playwright 可用
        except ImportError as _ie:
            print(f"[FAIL] 环境缺少 playwright 模块: {_ie}")
            print(f"[INFO] 当前 Python: {sys.executable}")
            print(f"[INFO] sys.path 前 3 项: {sys.path[:3]}")
            return 1

        # 直接复用项目内 auto_publish_xiaohongshu 函数（与 LangGraph 节点同一实现）
        try:
            from __004__langgraph_more_nodes.nodes.xhs_publish_nodes.xhs_auto_publish_node import (
                auto_publish_xiaohongshu,
            )
        except ImportError as ie2:
            print(f"[FAIL] 无法导入 auto_publish_xiaohongshu: {ie2}")
            # 兜底：再尝试从 __001__clawler 导入
            try:
                sys.path.insert(0, os.path.join(_PROJECT_ROOT, "__001__clawler"))
                from auto_publish_xiaohongshu_node import auto_publish_xiaohongshu  # type: ignore
                print("[INFO] 从 __001__clawler 目录加载 auto_publish_xiaohongshu")
            except Exception as ie3:
                print(f"[FAIL] __001__clawler 下也无法导入: {ie3}")
                return 1

        print("[INFO] 开始启动 Playwright + Chromium 并执行自动发布流程...")
        print("[INFO] ============================================================")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            success = loop.run_until_complete(auto_publish_xiaohongshu(image_paths, title, content))
        finally:
            loop.close()

        print("[INFO] ============================================================")
        if success:
            print("[DONE] ✅ 发布流程执行成功！")
            return 0
        else:
            print("[DONE] ❌ 发布流程执行失败！")
            return 1

    except Exception as e:
        print(f"[FAIL] 发布异常: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
