"""
LangGraph 流程图可视化工具

多级降级渲染策略：
1. 优先使用 mermaid.ink API (速度快、质量高)
2. 降级使用 Playwright 本地渲染 (无需外部 API, 需 playwright + chromium)
3. 最终降级：保存 .mmd (Mermaid 源码) + .html (浏览器可直接打开)
   + .txt (ASCII 文本图)

支持环境变量：
- LEGAL_DISABLE_PNG=1: 跳过所有渲染（测试提速用）
"""

import os
import sys
import io
import tempfile
from langgraph.graph.state import CompiledStateGraph


def output_pic_graph(app: CompiledStateGraph, filename: str = "graph.png"):
    """
    生成 LangGraph 流程图图片，支持多级降级。

    Args:
        app: LangGraph CompiledStateGraph 实例
        filename: 输出文件路径（支持 .png/.jpg/.mmd/.html 等）

    环境变量:
        LEGAL_DISABLE_PNG=1: 跳过渲染（测试时提速）
    """
    # 支持跳过渲染（测试提速）
    if os.environ.get("LEGAL_DISABLE_PNG", "").strip() in ("1", "true", "True"):
        return

    # 获取 mermaid 源码（始终可用，不依赖外部 API）
    graph = app.get_graph()
    mermaid_code = graph.draw_mermaid()

    # 尝试策略 1: mermaid.ink API (原方案)
    png_data = _try_mermaid_api(graph)
    if png_data:
        _write_file(filename, png_data)
        return

    # 尝试策略 2: Playwright 本地渲染
    png_data = _try_playwright(mermaid_code)
    if png_data:
        _write_file(filename, png_data)
        return

    # 最终降级: 保存 .mmd + .html + .txt
    _fallback_save(mermaid_code, graph, filename)


def _try_mermaid_api(graph) -> bytes | None:
    """策略 1: 使用 mermaid.ink API 渲染（原方案），静默捕获错误"""
    try:
        # 重定向 stderr 以抑制 LangGraph 库的噪声错误信息
        stderr_orig = sys.stderr
        sys.stderr = io.StringIO()

        try:
            png = graph.draw_mermaid_png(max_retries=1, retry_delay=0.5)
        finally:
            captured = sys.stderr.getvalue()
            sys.stderr = stderr_orig

        if png and len(png) > 100:
            print(f"  [渲染] mermaid.ink API 成功 ({len(png)} bytes)")
            return png
    except Exception:
        pass  # 静默处理，直接走降级方案
    return None


def _try_playwright(mermaid_code: str) -> bytes | None:
    """策略 2: 使用 Playwright 本地浏览器渲染（无需外部图像 API）"""
    try:
        from playwright.sync_api import sync_playwright

        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
    <style>
        body {{ margin: 0; padding: 20px; background: white; }}
        .mermaid {{ display: inline-block; }}
    </style>
    <script>
        mermaid.configure({{ startOnLoad: true, theme: 'default' }});
    </script>
</head>
<body>
    <div class="mermaid">
{mermaid_code}
    </div>
</body>
</html>"""

        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as tmp:
            tmp.write(html_content)
            html_path = tmp.name

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1200, "height": 800})
            page.goto(f"file:///{html_path}")
            page.wait_for_timeout(2500)
            png_bytes = page.screenshot(full_page=True)
            browser.close()

        try:
            os.unlink(html_path)
        except OSError:
            pass

        if png_bytes and len(png_bytes) > 100:
            print(f"  [渲染] Playwright 本地渲染成功 ({len(png_bytes)} bytes)")
            return png_bytes
    except ImportError:
        pass  # Playwright 未安装，静默跳过
    except Exception as e:
        print(f"  [渲染] Playwright 渲染失败: {e}")
    return None


def _fallback_save(mermaid_code: str, graph, filename: str):
    """策略 3: 最终降级 — 保存 .mmd + .html + .txt"""
    base = os.path.splitext(filename)[0]

    # 3a. 保存 .mmd (Mermaid 源码)
    mmd_path = base + ".mmd"
    _write_file(mmd_path, mermaid_code)
    print(f"  [降级] 已保存 Mermaid 源码: {mmd_path}")

    # 3b. 保存 .html (浏览器可直接打开)
    html_path = base + ".html"
    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Graph: {os.path.basename(base)}</title>
    <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
    <style>
        body {{ margin: 0; padding: 20px; background: white; font-family: sans-serif; }}
        .mermaid {{ display: inline-block; }}
        .info {{ color: #666; margin-bottom: 10px; }}
    </style>
    <script>
        mermaid.configure({{ startOnLoad: true, theme: 'default' }});
    </script>
</head>
<body>
    <div class="info">📊 LangGraph 流程图 · Mermaid 可视化</div>
    <div class="mermaid">
{mermaid_code}
    </div>
</body>
</html>"""
    _write_file(html_path, html_content)
    print(f"  [降级] 已保存 HTML 可视化: {html_path} (在浏览器中打开即可查看)")

    # 3c. 保存 .txt (ASCII 文本图)
    txt_path = base + ".txt"
    try:
        ascii_art = graph.draw_ascii()
        _write_file(txt_path, ascii_art)
        print(f"  [降级] 已保存 ASCII 文本图: {txt_path}")
    except Exception:
        pass


def _write_file(filepath: str, content: str | bytes):
    """写入文件，自动处理文本/二进制"""
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    mode = "wb" if isinstance(content, bytes) else "w"
    encoding = None if isinstance(content, bytes) else "utf-8"
    with open(filepath, mode, encoding=encoding) as f:
        f.write(content)
