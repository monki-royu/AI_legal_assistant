# -*- coding: utf-8 -*-
# 📜 代码文字逻辑解析
# 本文件是「法智引擎」项目的 Streamlit 前端主应用，承担构建整个 AI 法律助理 Web 界面的职责。
# 它定义了一套"红底蓝高亮"的视觉主题（通过 :root CSS 变量统一管理配色），并通过
# st.markdown + unsafe_allow_html=True 注入大量全局 CSS，覆盖 Streamlit 默认组件样式
# （侧边栏、按钮、输入框、聊天消息、文件上传器等），实现类 DeepSeek 风格的现代化 UI。
#
# 整体架构分为五大模块：
# (1) 全局 CSS 主题样式定义：主背景红色渐变、侧边栏深红主题、Hero 标题区、中央大输入框、
#     多色快捷卡片、风险卡片、文档高亮、思考过程动画、风险总览、统计卡片、聊天消息等；
# (2) 侧边栏导航：提供"首页/合同审核/合规审查/法律检索/小红书发布"五个功能页签，含 Logo、
#     演示模式开关与首页问答模式开关；
# (3) 首页智能问答：含任务元数据集中配置、5 张多色任务卡片切换、中央大输入框、5 张多色
#     快捷卡片（点击填充示例文本）、深度思考开关、流式思考动画与按任务类型分流渲染
#     （合同/合规走风险卡片+文档高亮，问答走 Markdown 流式输出）；
# (4) 工具函数：_stream_response（流式输出封装，优先后端、失败回退本地模拟）、
#     _get_demo_result/_get_compliance_demo_result（演示数据）、_highlight_doc（按风险
#     关键词高亮文档段落）、_render_risk_cards（带采纳/不采纳/修改三态交互的风险卡片）、
#     _render_score_overview（风险评分圆形概览）、_render_stat_cards（风险数量统计）；
# (5) 四个独立任务页面：合同审核、合规审查、法律检索、小红书发布，各自含文件上传、
#     立场/示例选择、效果展示切换与结果渲染。
#
# 核心交互特性：流式输出（chunk_size 分块 + time.sleep 模拟打字效果）、风险卡片三态交互
# （采纳/不采纳/修改）、文档段落按严重级别高亮（critical/high/medium/low 四色）、
# 思考过程动画（CSS keyframes bounce + 占位符逐步刷新）、session_state 状态管理
# （任务切换、结果缓存、修改内容暂存）。
"""
法智引擎 Streamlit 前端 v5
功能: 合同审核 / 信息检索 / 合规审查 / 智能问答 / 小红书发布
特色: 红底蓝高亮 + 类DeepSeek中央大输入框 + 多色快捷卡片 + 流式输出 + 思考过程
"""
# ===== 标准库导入区 =====
import os  # 操作系统接口，用于路径拼接与目录定位
import sys  # 系统相关参数与函数，用于修改 sys.path 以导入上层模块
import json  # JSON 序列化/反序列化
import time  # 时间相关函数
import asyncio  # 异步事件循环库
import threading  # 多线程，用于在独立线程中运行 asyncio 事件循环
import tempfile  # 临时文件目录，用于保存上传图片
import subprocess  # 子进程调用，用于隔离运行 Playwright 发布脚本
from pathlib import Path  # 路径对象，安全拼接路径

# Windows 下设置兼容的事件循环策略（必须在任何 asyncio 操作之前执行）
if sys.platform.startswith("win"):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

# 将项目根目录（本文件上一级）插入 sys.path 首位，确保能正确导入 __004__langgraph_more_nodes 等同级包
# os.path.abspath(__file__) 取本文件绝对路径 -> dirname 两次得到项目根目录
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st  # Streamlit Web 框架主入口，重命名为 st 便于调用

# 尝试导入后端 LangGraph 主接口（legal_response_sync 同步接口 / legal_response_stream 流式接口）
# 失败则进入演示模式（HAS_BACKEND=False），所有调用回退到本地模拟数据，保证前端可独立运行
try:
    from __004__langgraph_more_nodes.langgraph_main import legal_response_sync, legal_response_stream
    HAS_BACKEND = True  # 后端可用标志位，供 _stream_response 等函数判断走真实后端还是演示
except ImportError:
    HAS_BACKEND = False  # 后端不可用，仅展示演示数据

# ==================== 页面配置 ====================
# st.set_page_config 必须在所有其他 Streamlit 命令之前调用一次，用于设置浏览器标签页元信息
st.set_page_config(
    page_title="法智引擎 - AI法律助理",  # 浏览器标签页标题
    page_icon="⚖️",  # 浏览器标签页 favicon（emoji 形式）
    layout="wide",  # 页面布局：wide 宽屏（占满浏览器宽度），centered 则居中受限
    initial_sidebar_state="expanded",  # 侧边栏初始状态：expanded 默认展开
)

# ==================== 全局CSS (红底蓝高亮主题) ====================
# 通过 st.markdown + unsafe_allow_html=True 注入 <style> 块，实现全局样式覆盖
# 这是 Streamlit 中深度自定义 UI 的标准做法（Streamlit 原生主题能力有限）
st.markdown("""
<style>
    /* ===== 全局变量 - 浅色专业主题 ===== */
    :root {
        --bg-primary: #FAFBFC;
        --bg-secondary: #F3F4F6;
        --bg-tertiary: #E5E7EB;
        --sidebar-bg: #FFFFFF;
        --sidebar-border: #E5E7EB;

        --blue-deep: #0D47A1;
        --blue-mid: #1565C0;
        --blue-bright: #1976D2;
        --blue-soft: #42A5F5;
        --blue-glow: rgba(25, 118, 210, 0.15);
        --blue-hover: #1E88E5;

        --card-blue: #38bdf8;
        --card-orange: #fb923c;
        --card-green: #4ade80;
        --card-purple: #a78bfa;
        --card-pink: #f472b6;
        --card-amber: #fbbf24;

        --text-primary: #1F2937;
        --text-secondary: #374151;
        --text-muted: #6B7280;
        --text-faint: #9CA3AF;
        --text-inverse: #FFFFFF;

        --border-light: #E5E7EB;
        --border-medium: #D1D5DB;
        --shadow-sm: 0 1px 2px rgba(0,0,0,0.05);
        --shadow-md: 0 4px 12px rgba(0,0,0,0.08);
        --shadow-lg: 0 8px 24px rgba(0,0,0,0.1);
    }

    /* ===== 主背景: 浅灰白色 ===== */
    html { scroll-behavior: smooth; }
    .stApp {
        background: var(--bg-primary);
        min-height: 100vh;
    }
    [data-testid="stAppViewContainer"] > .main {
        background: var(--bg-primary);
        padding-top: 2rem;
    }

    /* ===== 顶部导航栏 ===== */
    [data-testid="stHeader"] {
        background: #FFFFFF;
        border-bottom: 1px solid var(--border-light);
        backdrop-filter: blur(10px);
    }

    /* ===== 侧边栏 (白色, 左侧细边框) ===== */
    [data-testid="stSidebar"] {
        background: var(--sidebar-bg) !important;
        border-right: 1px solid var(--sidebar-border);
    }
    [data-testid="stSidebar"] .stMarkdown {
        color: var(--text-secondary) !important;
    }
    [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 {
        color: var(--text-primary) !important;
    }
    section[data-testid="stSidebar"] label,
    section[data-testid="stSidebar"] p,
    section[data-testid="stSidebar"] span {
        color: var(--text-secondary) !important;
    }
    [data-testid="stSidebar"] .stMarkdown div[style*="color:#1f2937"] {
        color: var(--text-primary) !important;
    }
    [data-testid="stSidebar"] [data-testid="stRadio"] label {
        background: transparent !important;
        color: var(--text-secondary) !important;
        padding: 10px 14px;
        border-radius: 10px;
        margin-bottom: 6px;
        transition: all 0.2s;
        border: 1px solid transparent;
    }
    [data-testid="stSidebar"] [data-testid="stRadio"] label:hover {
        background: rgba(25, 118, 210, 0.08) !important;
        border: 1px solid rgba(25, 118, 210, 0.25);
    }
    [data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked) {
        background: linear-gradient(90deg, var(--blue-mid), var(--blue-bright)) !important;
        color: white !important;
        box-shadow: 0 2px 8px var(--blue-glow);
    }
    [data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked) .stRadioLabel {
        color: white !important;
    }
    [data-testid="stSidebar"] [data-testid="stCheckbox"] label,
    [data-testid="stSidebar"] [data-testid="stToggle"] label {
        color: var(--text-secondary) !important;
    }
    [data-testid="stSidebar"] hr,
    [data-testid="stSidebar"] [data-testid="stMarkdown"] hr {
        border-color: var(--border-light) !important;
    }
    [data-testid="stSidebar"] [data-testid="stMarkdown"] h3,
    [data-testid="stSidebar"] .stMarkdown h3 {
        color: var(--text-primary) !important;
        border-bottom: 1px solid var(--border-light);
        padding-bottom: 6px;
        margin-top: 14px !important;
    }
    [data-testid="stSidebar"] .stMarkdown small,
    [data-testid="stSidebar"] small {
        color: var(--text-faint) !important;
    }

    /* ===== 全局文本颜色 ===== */
    .stMarkdown {
        color: var(--text-primary) !important;
    }
    .stMarkdown h1, .stMarkdown h2, .stMarkdown h3, .stMarkdown h4 {
        color: var(--text-primary) !important;
        font-weight: 800;
    }
    .stMarkdown p, .stMarkdown li {
        color: var(--text-secondary) !important;
        font-size: 14px;
        line-height: 1.7;
    }
    .stMarkdown small, .stMarkdown .small {
        color: var(--text-muted) !important;
    }

    /* ===== 任务介绍/问候区 ===== */
    .task-greeting {
        font-size: 36px;
        font-weight: 900;
        color: var(--text-primary);
        letter-spacing: 1.5px;
        text-align: center;
        margin-bottom: 8px;
        line-height: 1.3;
    }
    .task-greeting .accent {
        background: linear-gradient(135deg, var(--blue-soft), var(--blue-bright));
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
    }
    .task-intro-box {
        max-width: 780px;
        margin: 0 auto 20px;
        padding: 16px 22px;
        background: #FFFFFF;
        border: 1px solid var(--border-light);
        border-radius: 14px;
        border-left: 4px solid var(--blue-bright);
        box-shadow: var(--shadow-sm);
    }
    .task-intro-box p, .task-intro-box span {
        color: var(--text-secondary) !important;
        font-size: 14px;
        line-height: 1.8;
    }
    .task-upload-row {
        max-width: 780px;
        margin: 0 auto 16px;
        display: flex;
        gap: 12px;
        flex-wrap: wrap;
    }
    .task-upload-btn {
        padding: 10px 18px;
        border-radius: 10px;
        background: rgba(25, 118, 210, 0.06);
        border: 1px solid rgba(25, 118, 210, 0.3);
        color: var(--blue-bright);
        font-size: 13px;
        font-weight: 600;
        cursor: pointer;
        transition: all 0.2s;
    }
    .task-upload-btn:hover {
        background: rgba(25, 118, 210, 0.12);
        color: var(--blue-mid);
    }

    /* ===== Hero 标题区 ===== */
    .hero-container {
        max-width: 900px;
        margin: 0 auto;
        padding: 40px 20px 20px;
        text-align: center;
    }
    .hero-title {
        font-size: 48px;
        font-weight: 900;
        color: var(--text-primary);
        letter-spacing: 2px;
        margin-bottom: 8px;
        line-height: 1.2;
    }
    .hero-title .accent {
        background: linear-gradient(135deg, var(--blue-soft), var(--blue-bright));
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
    }
    .hero-subtitle {
        font-size: 16px;
        color: var(--text-muted) !important;
        margin-bottom: 32px;
        letter-spacing: 1px;
        font-weight: 500;
    }
    .hero-subtitle p, .hero-subtitle span, .hero-subtitle div {
        color: var(--text-muted) !important;
    }

    /* ===== 中央大输入框 ===== */
    .main-input-card {
        max-width: 820px;
        margin: 0 auto;
        background: #FFFFFF;
        border: 2px solid var(--border-light);
        border-radius: 18px;
        padding: 6px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.04), 0 8px 24px rgba(0,0,0,0.06);
        transition: all 0.3s;
    }
    .main-input-card:hover {
        border-color: var(--blue-bright);
        box-shadow: 0 0 0 1px var(--blue-glow), 0 8px 32px rgba(0,0,0,0.08);
    }
    .main-input-card .stTextArea {
        margin: 0 !important;
    }
    .main-input-card .stTextArea > div {
        background: transparent !important;
        border: none !important;
    }
    .main-input-card .stTextArea > div > div {
        background: transparent !important;
        border: none !important;
    }
    .main-input-card .stTextArea > div > div > textarea {
        background: transparent !important;
        color: var(--text-primary) !important;
        border: none !important;
        font-size: 15px !important;
        padding: 18px 20px !important;
        min-height: 90px !important;
        line-height: 1.6 !important;
    }
    .main-input-card .stTextArea > div > div > textarea::placeholder {
        color: var(--text-faint) !important;
    }
    .main-input-row {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 0 16px 12px;
        gap: 12px;
    }
    .main-input-left {
        display: flex;
        gap: 8px;
        align-items: center;
    }
    .main-input-right {
        display: flex;
        gap: 8px;
        align-items: center;
    }
    .send-btn {
        width: 40px;
        height: 40px;
        border-radius: 50%;
        background: linear-gradient(135deg, var(--blue-mid), var(--blue-bright));
        color: white;
        border: none;
        cursor: pointer;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        font-size: 18px;
        transition: all 0.2s;
        box-shadow: 0 4px 12px var(--blue-glow);
    }
    .send-btn:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 18px var(--blue-glow);
    }
    .chip-btn {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 8px 14px;
        border-radius: 20px;
        background: rgba(25, 118, 210, 0.06);
        border: 1px solid rgba(25, 118, 210, 0.25);
        color: var(--blue-bright);
        font-size: 13px;
        cursor: pointer;
        transition: all 0.2s;
    }
    .chip-btn:hover {
        background: rgba(25, 118, 210, 0.12);
        border-color: var(--blue-bright);
    }
    .chip-btn.active {
        background: linear-gradient(135deg, var(--blue-mid), var(--blue-bright));
        color: white;
        border-color: transparent;
    }
    .icon-btn {
        width: 36px;
        height: 36px;
        border-radius: 10px;
        background: var(--bg-secondary);
        border: 1px solid var(--border-light);
        color: var(--text-muted);
        cursor: pointer;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        font-size: 16px;
        transition: all 0.2s;
    }
    .icon-btn:hover {
        background: var(--bg-tertiary);
        color: var(--text-primary);
    }

    /* ===== 多色快捷卡片 ===== */
    .quick-cards {
        max-width: 820px;
        margin: 28px auto 0;
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
        gap: 16px;
    }
    .quick-card {
        padding: 18px 20px;
        border-radius: 14px;
        background: #FFFFFF;
        cursor: pointer;
        transition: all 0.25s;
        position: relative;
        overflow: hidden;
        box-shadow: var(--shadow-sm);
    }
    .quick-card::before {
        content: '';
        position: absolute;
        top: 0; left: 0; right: 0;
        height: 3px;
    }
    .quick-card:hover {
        transform: translateY(-3px);
        box-shadow: var(--shadow-md);
    }
    .quick-card.blue { border: 1px solid rgba(56, 189, 248, 0.3); }
    .quick-card.blue::before { background: linear-gradient(90deg, var(--card-blue), var(--blue-bright)); }
    .quick-card.orange { border: 1px solid rgba(251, 146, 60, 0.3); }
    .quick-card.orange::before { background: linear-gradient(90deg, var(--card-orange), #f59e0b); }
    .quick-card.green { border: 1px solid rgba(74, 222, 128, 0.3); }
    .quick-card.green::before { background: linear-gradient(90deg, var(--card-green), #10b981); }
    .quick-card.purple { border: 1px solid rgba(167, 139, 250, 0.3); }
    .quick-card.purple::before { background: linear-gradient(90deg, var(--card-purple), #8b5cf6); }
    .quick-card.pink { border: 1px solid rgba(244, 114, 182, 0.3); }
    .quick-card.pink::before { background: linear-gradient(90deg, var(--card-pink), #ec4899); }
    .quick-card.amber { border: 1px solid rgba(251, 191, 36, 0.3); }
    .quick-card.amber::before { background: linear-gradient(90deg, var(--card-amber), #d97706); }

    .quick-card-tag {
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 2px;
        margin-bottom: 8px;
    }
    .quick-card.blue .quick-card-tag { color: var(--card-blue); }
    .quick-card.orange .quick-card-tag { color: var(--card-orange); }
    .quick-card.green .quick-card-tag { color: var(--card-green); }
    .quick-card.purple .quick-card-tag { color: var(--card-purple); }
    .quick-card.pink .quick-card-tag { color: var(--card-pink); }
    .quick-card.amber .quick-card-tag { color: var(--card-amber); }

    .quick-card-title {
        font-size: 17px;
        font-weight: 700;
        color: var(--text-primary);
        margin-bottom: 8px;
        display: flex;
        align-items: center;
        gap: 8px;
    }
    .quick-card-desc {
        font-size: 13px;
        color: var(--text-muted);
        line-height: 1.55;
    }

    /* ===== 底部说明文字 ===== */
    .footer-desc {
        max-width: 900px;
        margin: 40px auto 20px;
        text-align: center;
        padding: 0 20px;
    }
    .footer-desc .principle {
        font-size: 16px;
        color: var(--text-secondary);
        line-height: 1.8;
        margin-bottom: 10px;
    }
    .footer-desc .tech-stack {
        font-size: 13px;
        color: var(--text-muted);
        letter-spacing: 0.5px;
    }
    .footer-disclaimer {
        max-width: 900px;
        margin: 8px auto 30px;
        text-align: center;
        font-size: 11px;
        color: var(--text-faint);
        padding: 0 20px;
    }

    /* ===== 任务类型多色卡片 ===== */
    .task-type-cards {
        max-width: 900px;
        margin: 24px auto 8px;
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
        gap: 12px;
    }
    .task-type-card {
        background: #FFFFFF;
        border-radius: 12px;
        padding: 14px 16px;
        cursor: pointer;
        transition: all 0.2s;
        text-align: center;
        border: 2px solid transparent;
        box-shadow: var(--shadow-sm);
    }
    .task-type-card:hover {
        background: var(--bg-secondary);
        transform: translateY(-2px);
    }
    .task-type-card.active {
        background: rgba(25, 118, 210, 0.06);
        border-color: var(--blue-bright);
        box-shadow: 0 0 0 1px var(--blue-glow);
    }
    .task-type-card.blue { border-top: 3px solid var(--card-blue); }
    .task-type-card.orange { border-top: 3px solid var(--card-orange); }
    .task-type-card.green { border-top: 3px solid var(--card-green); }
    .task-type-card.purple { border-top: 3px solid var(--card-purple); }
    .task-type-card.pink { border-top: 3px solid var(--card-pink); }
    .task-type-card-icon {
        font-size: 22px;
        margin-bottom: 6px;
    }
    .task-type-card-label {
        font-size: 14px;
        font-weight: 700;
        color: var(--text-primary);
    }
    .task-type-card.active .task-type-card-label {
        color: var(--blue-bright);
    }

    /* ===== 按钮 ===== */
    .stButton > button {
        background: linear-gradient(135deg, var(--blue-mid), var(--blue-bright)) !important;
        color: white !important;
        border: 1px solid transparent !important;
        border-radius: 10px !important;
        padding: 10px 24px !important;
        font-weight: 600 !important;
        transition: all 0.2s !important;
        font-size: 14px !important;
        box-shadow: 0 2px 8px var(--blue-glow) !important;
    }
    .stButton > button:hover {
        background: linear-gradient(135deg, #1d4ed8, var(--blue-bright)) !important;
        border-color: var(--blue-soft) !important;
        transform: translateY(-1px) !important;
        box-shadow: 0 4px 14px var(--blue-glow) !important;
    }
    .stButton > button[data-testid="stBaseButton-secondary"],
    .stButton > button[kind="secondary"] {
        background: #FFFFFF !important;
        color: var(--text-secondary) !important;
        border: 1px solid var(--border-medium) !important;
        box-shadow: none !important;
    }
    .stButton > button[data-testid="stBaseButton-secondary"]:hover,
    .stButton > button[kind="secondary"]:hover {
        background: var(--bg-secondary) !important;
        color: var(--text-primary) !important;
        border-color: var(--text-muted) !important;
        box-shadow: var(--shadow-sm) !important;
    }
    .stButton > button p, .stButton > button span, .stButton > button div {
        color: inherit !important;
    }
    .stButton > button:not([kind="secondary"]) p,
    .stButton > button:not([kind="secondary"]) span,
    .stButton > button:not([kind="secondary"]) div {
        color: white !important;
    }

    /* ===== Expander ===== */
    .streamlit-expanderHeader {
        background: #FFFFFF !important;
        color: var(--text-secondary) !important;
        border: 1px solid var(--border-light) !important;
        border-radius: 10px !important;
        padding: 10px 24px !important;
        font-weight: 600 !important;
        font-size: 14px !important;
        text-align: center !important;
        justify-content: center !important;
        align-items: center !important;
    }
    .streamlit-expanderHeader:hover {
        background: var(--bg-secondary) !important;
        color: var(--text-primary) !important;
        border-color: var(--border-medium) !important;
    }
    .streamlit-expanderHeader p,
    .streamlit-expanderHeader span {
        text-align: center !important;
        display: flex !important;
        justify-content: center !important;
        width: 100% !important;
    }
    .streamlit-expanderContent {
        background: #FFFFFF !important;
        color: var(--text-primary) !important;
        border: 1px solid var(--border-light) !important;
        border-top: none !important;
        border-radius: 0 0 10px 10px !important;
        padding: 12px 24px !important;
    }
    .streamlit-expanderContent p,
    .streamlit-expanderContent div {
        color: var(--text-primary) !important;
    }

    /* ===== 输入框 ===== */
    .stTextInput > div > div > input,
    .stTextArea > div > div > textarea,
    .stSelectbox > div > div > div,
    .stNumberInput > div > div > input {
        background: #FFFFFF !important;
        color: var(--text-primary) !important;
        border: 1px solid var(--border-medium) !important;
        border-radius: 8px !important;
        font-weight: 500 !important;
    }
    .stTextArea > div > div > textarea {
        caret-color: var(--blue-bright) !important;
    }
    .stTextInput > div > div > input {
        caret-color: var(--blue-bright) !important;
    }
    .stTextInput > div > div > input::placeholder,
    .stTextArea > div > div > textarea::placeholder {
        color: var(--text-faint) !important;
    }
    .stTextInput label, .stTextArea label, .stSelectbox label, .stNumberInput label {
        color: var(--text-secondary) !important;
        font-weight: 600;
        font-size: 13px;
    }

    /* ===== Streamlit radio 修复 ===== */
    [data-testid="stRadio"] label {
        color: var(--text-primary) !important;
        background: #FFFFFF;
        padding: 6px 10px;
        border-radius: 8px;
        font-weight: 500;
    }
    [data-testid="stRadio"] label:hover {
        background: var(--bg-secondary);
    }

    /* ===== expander / file_uploader ===== */
    .stFileUploader > div > div {
        background: #FFFFFF !important;
        color: var(--text-primary) !important;
        border-radius: 10px !important;
        border: 1px solid var(--border-medium) !important;
    }
    .stFileUploader > div > div div,
    .stFileUploader > div > div span,
    .stFileUploader > div > div p,
    .stFileUploader > div > div small,
    .stFileUploader > div > div label {
        color: var(--text-secondary) !important;
    }
    .stFileUploader label {
        color: var(--text-secondary) !important;
        font-weight: 600 !important;
        font-size: 13px !important;
    }
    [data-testid="stExpander"] {
        background: #FFFFFF;
        border-radius: 10px;
        border: 1px solid var(--border-light);
    }
    [data-testid="stExpander"] details summary p,
    [data-testid="stExpander"] details summary span {
        color: var(--text-primary) !important;
        font-weight: 600 !important;
    }
    .stSelectbox label, .stMultiSelect label {
        color: var(--text-secondary) !important;
        font-weight: 600 !important;
    }
    [data-testid="stToggle"] label, [data-testid="stCheckbox"] label {
        color: var(--text-secondary) !important;
    }

    /* ===== 信息/警告框 ===== */
    .stInfo, .stWarning, .stError, .stSuccess {
        border-radius: 10px !important;
        color: var(--text-primary) !important;
    }
    .stInfo *, .stWarning *, .stError *, .stSuccess * {
        color: inherit !important;
    }
    .stInfo {
        background: rgba(25, 118, 210, 0.08) !important;
        border: 1px solid rgba(25, 118, 210, 0.25) !important;
    }
    .stWarning {
        background: rgba(251, 191, 36, 0.1) !important;
        border: 1px solid rgba(251, 191, 36, 0.3) !important;
    }
    .stError {
        background: rgba(239, 68, 68, 0.08) !important;
        border: 1px solid rgba(239, 68, 68, 0.25) !important;
    }
    .stSuccess {
        background: rgba(34, 197, 94, 0.08) !important;
        border: 1px solid rgba(34, 197, 94, 0.25) !important;
    }

    /* ===== 风险卡片 ===== */
    .risk-card {
        background: #FFFFFF;
        border-radius: 12px;
        padding: 18px 20px;
        margin-bottom: 16px;
        border-left: 4px solid var(--blue-bright);
        border: 1px solid var(--border-light);
        box-shadow: var(--shadow-sm);
        scroll-margin-top: 80px;
        transition: box-shadow 0.3s ease, transform 0.2s ease;
    }
    .risk-card.jump-target {
        animation: cardFlash 0.9s ease-out;
    }
    .risk-card:target {
        animation: cardFlash 0.9s ease-out;
        scroll-margin-top: 80px;
    }
    @keyframes cardFlash {
        0% { box-shadow: 0 0 0 4px rgba(25,118,210,0.45), var(--shadow-sm); transform: scale(1.01); }
        100% { box-shadow: var(--shadow-sm); transform: scale(1); }
    }
    .risk-card.critical { border-left: 4px solid #ef4444; }
    .risk-card.high { border-left: 4px solid #f97316; }
    .risk-card.medium { border-left: 4px solid #fbbf24; }
    .risk-card.low { border-left: 4px solid #1976D2; }

    .risk-header {
        display: flex;
        align-items: center;
        gap: 12px;
        margin-bottom: 12px;
        flex-wrap: wrap;
    }
    .risk-badge {
        display: inline-block;
        padding: 4px 12px;
        border-radius: 20px;
        font-size: 12px;
        font-weight: 700;
        color: white;
    }
    .risk-badge.critical { background: linear-gradient(135deg, #dc2626, #ef4444); }
    .risk-badge.high { background: linear-gradient(135deg, #ea580c, #f97316); }
    .risk-badge.medium { background: linear-gradient(135deg, #d97706, #fbbf24); color: #1f2937; }
    .risk-badge.low { background: linear-gradient(135deg, #0D47A1, #1976D2); }

    .risk-title {
        font-size: 15px;
        font-weight: 700;
        color: var(--text-primary);
        flex: 1;
    }
    .risk-source {
        font-size: 11px;
        color: var(--text-muted);
        background: rgba(25, 118, 210, 0.08);
        padding: 2px 8px;
        border-radius: 4px;
    }
    .risk-body {
        color: var(--text-secondary);
        font-size: 14px;
        line-height: 1.6;
        margin-bottom: 12px;
    }
    .risk-meta {
        display: flex;
        gap: 16px;
        font-size: 12px;
        color: var(--text-muted);
        margin-bottom: 12px;
        flex-wrap: wrap;
    }
    .risk-suggestion {
        background: rgba(25, 118, 210, 0.06);
        border-radius: 8px;
        padding: 10px 14px;
        margin-bottom: 12px;
        font-size: 13px;
        color: var(--blue-mid);
        border: 1px solid rgba(25, 118, 210, 0.15);
    }
    .risk-suggestion-label {
        color: var(--blue-bright);
        font-weight: 600;
        font-size: 12px;
        margin-bottom: 4px;
    }

    /* ===== 文档高亮 ===== */
    .doc-container {
        background: #FFFFFF;
        border-radius: 12px;
        padding: 24px;
        border: 1px solid var(--border-light);
        max-height: 70vh;
        overflow-y: auto;
        font-size: 14px;
        line-height: 1.8;
        box-shadow: var(--shadow-sm);
    }
    .doc-paragraph {
        margin-bottom: 14px;
        padding: 8px 12px;
        border-radius: 6px;
        color: var(--text-secondary);
        transition: all 0.2s ease;
    }
    /* <a> 标签样式: 去除下划线, 继承颜色, 块级显示 */
    a.doc-paragraph {
        display: block;
        text-decoration: none;
        color: inherit;
    }
    a.doc-paragraph:hover {
        text-decoration: none;
        color: inherit;
    }
    .doc-paragraph.highlight-critical,
    .doc-paragraph.highlight-high,
    .doc-paragraph.highlight-medium,
    .doc-paragraph.highlight-low {
        cursor: pointer;
        transition: transform 0.15s ease, box-shadow 0.15s ease, background 0.2s ease;
    }
    .doc-paragraph.highlight-critical:hover,
    .doc-paragraph.highlight-high:hover,
    .doc-paragraph.highlight-medium:hover,
    .doc-paragraph.highlight-low:hover {
        transform: translateY(-1px);
        box-shadow: 0 2px 8px rgba(0,0,0,0.08);
    }
    .doc-paragraph:target {
        animation: jumpFlash 0.9s ease-out;
    }
    @keyframes jumpFlash {
        0% { box-shadow: 0 0 0 3px rgba(25,118,210,0.35); }
        100% { box-shadow: 0 0 0 0 rgba(25,118,210,0); }
    }
    .doc-container {
        scroll-behavior: smooth;
    }
    .doc-paragraph.highlight-critical {
        background: rgba(239, 68, 68, 0.08);
        border-left: 3px solid #ef4444;
    }
    .doc-paragraph.highlight-high {
        background: rgba(249, 115, 22, 0.08);
        border-left: 3px solid #f97316;
    }
    .doc-paragraph.highlight-medium {
        background: rgba(251, 191, 36, 0.08);
        border-left: 3px solid #fbbf24;
        color: var(--text-primary);
    }
    .doc-paragraph.highlight-low {
        background: rgba(25, 118, 210, 0.08);
        border-left: 3px solid #1976D2;
    }

    /* ===== 思考过程动画 ===== */
    .thinking-container {
        background: rgba(25, 118, 210, 0.06);
        border: 1px solid rgba(25, 118, 210, 0.2);
        border-radius: 12px;
        padding: 14px 18px;
        margin-bottom: 12px;
        display: flex;
        align-items: flex-start;
        gap: 12px;
    }
    .thinking-icon {
        font-size: 18px;
        margin-top: 2px;
    }
    .thinking-content {
        flex: 1;
    }
    .thinking-title {
        color: var(--blue-bright);
        font-size: 13px;
        font-weight: 700;
        margin-bottom: 6px;
    }
    .thinking-steps {
        color: var(--text-secondary);
        font-size: 13px;
        line-height: 1.7;
    }
    .thinking-dots {
        display: inline-flex;
        gap: 4px;
        margin-left: 4px;
    }
    .thinking-dots span {
        display: inline-block;
        width: 6px;
        height: 6px;
        background: var(--blue-bright);
        border-radius: 50%;
        animation: bounce 1.4s infinite ease-in-out both;
    }
    .thinking-dots span:nth-child(1) { animation-delay: -0.32s; }
    .thinking-dots span:nth-child(2) { animation-delay: -0.16s; }
    @keyframes bounce {
        0%, 80%, 100% { transform: scale(0); }
        40% { transform: scale(1); }
    }

    /* ===== 风险总览卡片 ===== */
    .risk-overview {
        background: #FFFFFF;
        border-radius: 12px;
        padding: 20px 24px;
        margin-bottom: 20px;
        border: 1px solid var(--border-light);
        box-shadow: var(--shadow-sm);
    }
    .score-circle {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 80px;
        height: 80px;
        border-radius: 50%;
        font-size: 28px;
        font-weight: 900;
        margin-right: 20px;
    }
    .score-circle.low { background: linear-gradient(135deg, #16a34a, #22c55e); color: white; }
    .score-circle.medium { background: linear-gradient(135deg, #d97706, #fbbf24); color: #1f2937; }
    .score-circle.high { background: linear-gradient(135deg, #dc2626, #ef4444); color: white; }
    .overview-info { display: inline-block; vertical-align: top; }
    .overview-label { font-size: 13px; color: var(--text-muted); margin-bottom: 4px; }
    .overview-value { font-size: 20px; font-weight: 700; color: var(--text-primary); margin-bottom: 4px; }
    .overview-desc { font-size: 13px; color: var(--text-secondary); }

    /* ===== 统计卡片 ===== */
    .stat-row { display: flex; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; }
    .stat-mini {
        flex: 1;
        min-width: 80px;
        background: #FFFFFF;
        border-radius: 10px;
        padding: 12px 16px;
        text-align: center;
        border: 1px solid var(--border-light);
        box-shadow: var(--shadow-sm);
    }
    .stat-mini .num { font-size: 24px; font-weight: 900; color: var(--text-primary); }
    .stat-mini.critical .num { color: #ef4444; }
    .stat-mini.high .num { color: #f97316; }
    .stat-mini.medium .num { color: #fbbf24; }
    .stat-mini.low .num { color: var(--blue-bright); }
    .stat-mini .label { font-size: 11px; color: var(--text-muted); margin-top: 4px; }

    /* ===== 聊天消息 ===== */
    [data-testid="stChatMessage"] {
        background: transparent !important;
        padding: 12px 0;
    }
    [data-testid="stChatMessage.user"] > div > div {
        background: linear-gradient(135deg, var(--blue-mid), var(--blue-bright));
        border-radius: 16px;
        padding: 14px 18px;
        border: 1px solid transparent;
        box-shadow: 0 2px 8px var(--blue-glow);
    }
    [data-testid="stChatMessage.user"] p,
    [data-testid="stChatMessage.user"] span,
    [data-testid="stChatMessage.user"] li {
        color: white !important;
    }
    [data-testid="stChatMessage.assistant"] > div > div {
        background: #FFFFFF;
        border-radius: 16px;
        padding: 14px 18px;
        border: 1px solid var(--border-light);
        box-shadow: var(--shadow-sm);
    }
    [data-testid="stChatMessage.assistant"] p,
    [data-testid="stChatMessage.assistant"] li {
        color: var(--text-secondary) !important;
    }
    [data-testid="stChatMessage.assistant"] h1,
    [data-testid="stChatMessage.assistant"] h2,
    [data-testid="stChatMessage.assistant"] h3,
    [data-testid="stChatMessage.assistant"] h4 {
        color: var(--text-primary) !important;
    }

    /* ===== 隐藏控件 ===== */
    [data-testid="stRadio"][aria-label="task_type_hidden"],
    [data-testid="stRadio"][aria-label="task_type_radio"] {
        display: none !important;
        height: 0 !important;
        width: 0 !important;
        overflow: hidden !important;
        visibility: hidden !important;
        position: absolute !important;
        left: -9999px !important;
    }
    div[role="radiogroup"]:has(label[for*="task_type_radio"]) {
        display: none !important;
    }

    /* ===== 隐藏默认元素 ===== */
    #MainMenu { visibility: hidden; }
    footer { visibility: hidden; }
    [data-testid="stDecoration"] { display: none; }

    /* ===== 滚动条 ===== */
    ::-webkit-scrollbar { width: 8px; }
    ::-webkit-scrollbar-track { background: #F1F5F9; }
    ::-webkit-scrollbar-thumb { background: #CBD5E1; border-radius: 4px; }
    ::-webkit-scrollbar-thumb:hover { background: #94A3B8; }

</style>
""", unsafe_allow_html=True)


# ==================== 侧边栏配置 (深红主题导航, 与主页面和谐) ====================
# —— 首页卡片跳转 "Pending 处理钩子" ——
# 背景：Streamlit 一旦把 st.radio(... key="nav_page_radio") 实例化，该 key 就会被 widget 状态锁接管，
# 此后在按钮回调里直接执行 st.session_state["nav_page_radio"] = xxx 会抛 StreamlitAPIException。
# 规避方案：首页 5 张任务卡片点击时先写入一个"中立"的中间键 _pending_switch_to_page，
# 然后在"侧边栏 radio 实例化之前"把这个中间键的值迁到 nav_page_radio，
# 这样 st.radio 读到的 session_state 默认值就已经是目标页，能正确跳转到独立页面。
ALLOWED_PAGES = ["🏠 首页", "📋 合同审核", "🛡️ 合规审查", "🔍 法律检索", "📱 小红书发布"]  # 合法页面名集合（白名单兜底）
if "_pending_switch_to_page" in st.session_state:
    _target = st.session_state["_pending_switch_to_page"]
    if _target in ALLOWED_PAGES:
        st.session_state["nav_page_radio"] = _target  # 在 widget 实例化之前赋值，Streamlit 允许
    del st.session_state["_pending_switch_to_page"]  # 用完即删，防止下次重复触发

# with st.sidebar 上下文：所有内部组件渲染到左侧侧边栏
with st.sidebar:
    # 侧边栏顶部 Logo 区：通过自定义 HTML 渲染图标 + 标题 + 副标题
    st.markdown("""
    <div style="text-align:center;padding:18px 0 10px;">
        <!-- Logo 图标容器：蓝色渐变方框 + 辉光阴影 -->
        <div style="display:inline-flex;align-items:center;justify-content:center;width:48px;height:48px;border-radius:12px;background:linear-gradient(135deg,#0D47A1,#1976D2);box-shadow:0 4px 14px rgba(59,130,246,0.45);font-size:26px;margin-bottom:10px;">
            ⚖️
        </div>
        <!-- 主标题"法智引擎"：白色大字 + 字间距 -->
        <div style="font-size:18px;font-weight:900;color:#1F2937;letter-spacing:2px;">法智引擎</div>
        <!-- 副标题：柔红色小字 -->
        <div style="color:#6B7280;font-size:12px;margin-top:4px;opacity:0.85;">AI 原生法律助理</div>
    </div>
    """, unsafe_allow_html=True)
    st.markdown("---")  # 分隔线，区分 Logo 与导航

    # 主导航 radio：5 个功能页签，label_visibility="collapsed" 隐藏默认 label（用 HTML Logo 代替）
    # key="nav_page_radio" 用于与首页 5 张任务卡片双向绑定，让卡片点击能直接切换到对应独立页面
    page = st.radio(
        "功能导航",
        ["🏠 首页", "📋 合同审核", "🛡️ 合规审查", "🔍 法律检索", "📱 小红书发布"],
        label_visibility="collapsed",
        key="nav_page_radio",
    )

    st.markdown("---")  # 分隔线，区分导航与设置区
    st.markdown("### ⚙️ 设置")
    # 演示模式开关：开启后所有任务走本地演示数据，不调用后端
    demo_mode = st.toggle("🎭 演示模式", value=False)
    
    # 仅在首页时显示"问答模式"开关（首页特有配置）
    if page == "🏠 首页":
        st.markdown("### 📝 快速问答")
        quick_qa_mode = st.toggle("💬 问答模式", value=True)

    st.markdown("---")  # 分隔线，区分设置与底部版权
    # 侧边栏底部版权信息小字
    st.markdown("<div style='color:#9ca3af;font-size:11px;text-align:center;'>Powered by LangGraph<br>© 2026 法智引擎</div>", unsafe_allow_html=True)


# ==================== 工具函数 ====================

# SEVERITY_MAP：严重级别到中文标签/颜色/badge 类名的映射字典，供风险卡片渲染使用
SEVERITY_MAP = {
    "critical": {"label": "严重", "color": "#ef4444", "badge": "critical"},  # 严重：红色
    "high": {"label": "高", "color": "#f97316", "badge": "high"},            # 高：橙色
    "medium": {"label": "中", "color": "#fbbf24", "badge": "medium"},        # 中：黄色
    "low": {"label": "低", "color": "#1976D2", "badge": "low"},              # 低：蓝色
}

# RISK_LEVEL_MAP：综合风险等级到中文标签/颜色/描述的映射，供评分总览卡片使用
RISK_LEVEL_MAP = {
    "Low": {"label": "低风险", "color": "#22c55e", "desc": "可直接使用"},
    "Medium": {"label": "中风险", "color": "#fbbf24", "desc": "建议律师复核"},
    "High": {"label": "高风险", "color": "#ef4444", "desc": "需人工审核后修改"},
}


def _run_backend_isolated(input_text, task_type, timeout_sec=300):
    """在独立子进程中运行后端 LangGraph, 抗进程级崩溃。

    作用:
        当 graph.invoke 内部的 C 扩展 (numpy/pyarrow/tiktoken/numexpr 等)
        触发硬崩溃 (segfault / OOM / abort) 时, 同进程的 try/except 无法捕获,
        整个 Streamlit 主进程会一起死掉, 导致前端 spinner 永远卡在"正在加载"。
        本函数通过 subprocess 启动一个独立的 Python 进程执行 langgraph_main,
        这样即使子进程崩溃 (非 0 退出码) 或超时, 本函数也能检测到并返回 None,
        让调用方安全回退 demo 数据。

    参数:
        input_text (str): 用户输入的合同/文档全文
        task_type (str): 任务类型 contract_review / compliance_review / legal_research
        timeout_sec (int): 子进程最长运行秒数, 超时后强制 kill 并视为失败

    返回值:
        dict | None: 成功 -> 返回 legal_response_full 的结构化 dict;
                     失败/崩溃/超时 -> 返回 None, 调用方应 fallback 演示数据

    说明:
        - 使用 --input_file 写临时文件传长文本, 避免 Windows shell 引号/换行转义问题
        - subprocess stdout 只承载 JSON 返回, 节点执行 print 日志走 stderr
          (用户在 CMD 窗口中仍能看到节点进度日志)
    """
    # 演示模式 / 后端不可用: 直接返回 None 让调用方走本地 demo 分支
    if not HAS_BACKEND or demo_mode:
        return None
    if not input_text or not input_text.strip():
        return None

    try:
        # ---- 步骤 1: 写临时输入文件 ----
        # delete=False 让子进程仍能读取; 用 finally 在函数尾部删除
        tmp_fp = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".txt", delete=False
        )
        try:
            tmp_fp.write(input_text)
            tmp_path = tmp_fp.name
        finally:
            tmp_fp.close()

        # ---- 步骤 2: 构造命令 ----
        # sys.executable: 当前 Streamlit 用的 Python 解释器(与用户启动 app.py 的 Python 一致)
        # -m __004__langgraph_more_nodes.langgraph_main: 以模块模式启动,
        #   这样 __package__ 正确, 内部 from __004__xxx.xxx import xxx 正常工作
        # mode=full: 返回 legal_response_full 结构化 JSON 单行
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cmd = [
            sys.executable, "-u", "-m",
            "__004__langgraph_more_nodes.langgraph_main",
            "--input_file", tmp_path,
            "--task_type", str(task_type),
            "--mode", "full",
        ]

        # ---- 步骤 3: 同步运行子进程 + 超时保护 ----
        # stdout=PIPE 捕获 JSON 返回
        # stderr=None 让子进程 stderr 直接继承到当前 CMD 窗口, 用户能看到节点日志
        #
        # === Windows 中文环境关键: 设置 PYTHONIOENCODING + PYTHONUTF8 ===
        # 若不设: Python 子进程会沿用 CMD 默认代码页 GBK, 当代码里 print("\u25b6 执行节点")
        # 时就会抛出 UnicodeEncodeError: 'gbk' codec can't encode character '\u25b6',
        # 导致整条 CLI 链路退出码=1, 前端 fallback demo 但真实后端本应能跑通.
        # 强制 UTF-8 后 sys.stdout/stderr 全部使用 utf-8, 不再有 GBK 编码崩溃.
        sub_env = os.environ.copy()
        sub_env["PYTHONIOENCODING"] = "utf-8"
        sub_env["PYTHONUTF8"] = "1"
        sub_env.pop("PYTHONLEGACYWINDOWSSTDIO", None)  # 禁用 legacy 模式
        try:
            completed = subprocess.run(
                cmd,
                cwd=project_root,          # 切到项目根,让包导入路径一致
                capture_output=True,       # stdout/stderr 都捕获,避免继承时干扰解析
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_sec,
                env=sub_env,               # 带 UTF-8 强制变量
            )
        except subprocess.TimeoutExpired:
            print(f"[_run_backend_isolated] 子进程超时 ({timeout_sec}s), 回退 demo")
            return None

        # 把子进程 stderr 回显到当前进程 (保留原 CMD 窗口的节点日志观感)
        if completed.stderr:
            sys.stderr.write(completed.stderr)
            sys.stderr.flush()

        # ---- 步骤 4: 解析 stdout JSON ----
        stdout_str = (completed.stdout or "").strip()
        # 子进程退出非 0: 明确失败, 直接回退
        if completed.returncode != 0:
            print(
                f"[_run_backend_isolated] 子进程退出码={completed.returncode}, "
                f"回退 demo. stderr_tail={completed.stderr[-400:] if completed.stderr else '(空)'}"
            )
            return None

        if not stdout_str:
            print("[_run_backend_isolated] 子进程 stdout 为空, 回退 demo")
            return None

        try:
            parsed = json.loads(stdout_str)
        except json.JSONDecodeError as e:
            print(f"[_run_backend_isolated] JSON 解析失败: {e}, 原始片段前300字: {stdout_str[:300]}")
            return None

        # CLI 会把异常封装为 {"__cli_error__": "..."} -> 返回非 0 + JSON 错误, 这里双保险再判一次
        if isinstance(parsed, dict) and "__cli_error__" in parsed:
            print(f"[_run_backend_isolated] 后端返回错误标记: {parsed['__cli_error__']}, 回退 demo")
            return None

        return parsed

    except Exception as e:
        # 本函数任何自身异常(文件IO/subprocess 构造失败等) 都要 swallow, 否则又把前端卡死
        print(f"[_run_backend_isolated] 外围异常: {type(e).__name__}: {e}")
        return None
    finally:
        # 无论成功失败, 临时文件一定要清理
        try:
            if "tmp_path" in locals() and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


def _stream_response(input_text, **kwargs):
    """封装流式响应生成器, 优先使用后端, 失败则本地模拟。

    作用：
        作为流式输出的统一入口，先输出 5 个"思考步骤"提示文字（带 sleep 模拟延迟），
        再分块（chunk_size=8）输出实际回答内容，实现类 ChatGPT 的打字机效果。
        优先调用后端 legal_response_stream/legal_response_sync，若 HAS_BACKEND=False
        或 demo_mode=True 或后端抛异常，则回退到本地演示数据 _get_demo_result/
        _get_compliance_demo_result。

    参数：
        input_text (str): 用户输入文本（问题或文档内容）
        **kwargs: 透传给后端的额外参数，常见键：
            - task_type (str): 任务类型，如 "contract_review"/"compliance_review"/"legal_research"
            - deep_thinking (bool): 是否启用深度思考模式

    返回值：
        generator: 逐步 yield 字符串片段，调用方（通常是 st.write_stream）拼接展示

    可迁移性说明：
        该函数是 Streamlit 前端与 LangGraph 后端之间的流式适配层。若迁移到其他后端
        （如 FastAPI/直接 LLM SDK），只需替换内部 legal_response_stream/
        legal_response_sync 调用为新后端的同步/流式接口即可；分块打字机逻辑可复用。
    """
    # 优先走后端：HAS_BACKEND 为 True 且未开启演示模式
    if HAS_BACKEND and not demo_mode:
        try:
            # 在同步上下文中创建新的 asyncio 事件循环，用于驱动后端 async 流式接口
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            # 调用后端流式接口（这里实际拿到的是 coroutine，需后续 await，但本实现改为用同步接口取整段结果）
            stream_gen = legal_response_stream(input_text, **kwargs)
            
            accumulated = ""  # 累积输出（本实现未实际使用，保留以备扩展为真正流式）
            # 思考步骤提示文字列表，逐条 yield 并 sleep，营造"思考中"动画效果
            thinking_steps = [
                "🤔 正在分析您的输入...",
                "📚 检索相关法律条文...",
                "⚖️ 匹配判例与司法解释...",
                "🔍 生成审核建议...",
                "✅ 整理最终报告..."
            ]
            for step in thinking_steps:
                yield f"{step}\n\n"  # yield 单步提示文字，双换行分隔
                time.sleep(0.3)  # 0.3s 延迟模拟思考
            
            # 调用后端同步接口获取完整结果（实际项目中应改为真正消费 stream_gen）
            result = legal_response_sync(input_text, **kwargs)
            chunk_size = 8  # 每块 8 个字符，模拟打字机
            # 按字符切片分块 yield
            for i in range(0, len(result), chunk_size):
                chunk = result[i:i + chunk_size]
                yield chunk
                time.sleep(0.03)  # 30ms 延迟，肉眼可见的打字效果
            loop.close()  # 关闭事件循环，释放资源
            return  # 提前返回，跳过本地模拟分支
        except Exception as e:
            # 后端异常时打印日志并降级到本地模拟，保证前端不崩
            print(f"后端流式失败, 切换本地模拟: {e}")

    # ===== 本地模拟模式（后端不可用 / 演示模式 / 后端异常） =====
    # 同样先输出 5 个思考步骤提示（sleep 更短 0.2s）
    thinking_steps = [
        "🤔 正在分析您的输入...",
        "📚 检索相关法律条文...",
        "⚖️ 匹配判例与司法解释...",
        "🔍 生成审核建议...",
        "✅ 整理最终报告..."
    ]
    for step in thinking_steps:
        yield f"{step}\n\n"
        time.sleep(0.2)

    # 根据任务类型选择对应的演示数据生成函数
    task_type = kwargs.get("task_type", "")
    if task_type == "contract_review":
        # 合同审核：使用合同演示数据
        demo_result = _get_demo_result(input_text)
    elif task_type == "compliance_review":
        # 合规审查：使用合规演示数据
        demo_result = _get_compliance_demo_result(input_text)
    else:
        # 其他任务（问答/检索/小红书）：默认使用合同演示数据（实际项目中应分别实现）
        demo_result = _get_demo_result(input_text)

    # 取演示结果中的 output 字段作为流式输出文本，默认"分析完成"
    output_text = demo_result.get("output", "分析完成")
    chunk_size = 8  # 每块 8 字符
    # 分块 yield 实现打字机效果
    for i in range(0, len(output_text), chunk_size):
        chunk = output_text[i:i + chunk_size]
        yield chunk
        time.sleep(0.02)  # 20ms 延迟

    # 将完整演示结果缓存到 session_state，键名按任务类型区分，供后续风险卡片渲染等使用
    full_key = f"{task_type}_full_result" if task_type else "qa_full_result"
    st.session_state[full_key] = demo_result


def _get_demo_result(doc_text):
    """生成合同审核演示数据（demo 模式或后端不可用时使用）。

    作用：
        返回一份结构完整的合同审核结果字典，包含输出文本、原文、风险项列表、
        综合评分、风险等级、是否需律师复核、引用法条、Markdown 完整报告等字段，
        供前端风险卡片/文档高亮/评分总览等组件渲染。

    参数：
        doc_text (str): 合同原文（若为空则使用默认示例文本）

    返回值：
        dict: 包含以下键的结构化结果：
            - output (str): 流式输出用的简短文本
            - doc_text (str): 合同原文
            - merged_risk_items (list[dict]): 风险项列表，每项含 severity/source/description/
              clause/legal_basis/suggestion
            - overall_risk_score (int): 综合风险评分（0-100）
            - risk_level (str): 风险等级（"Low"/"Medium"/"High"）
            - need_lawyer_review (bool): 是否需要律师复核
            - citations (list[dict]): 引用法条列表，含 title/article_no/content
            - final_report_markdown (str): 完整 Markdown 报告

    可迁移性说明：
        本函数是演示数据的"数据夹具"。迁移到真实后端时，后端应返回同结构字典，
        前端渲染逻辑无需改动。该字典结构是前后端契约的参考实现。
    """
    doc = doc_text or "合同文本示例"  # 若传入空文本则使用默认示例
    return {
        "output": "# 合同审核报告\n\n本合同存在 **4项风险**，建议关注以下条款：\n\n1. 违约金比例偏高\n2. 管辖约定需明确\n3. 付款周期较长\n4. 标的描述可完善",  # 流式输出文本
        "doc_text": doc,  # 合同原文
        "merged_risk_items": [  # 4 项风险，覆盖 critical/high/medium/low 四个级别
            # clause 字段故意不写"第X条"前缀(合同实际编号千差万别, 纯关键词才能匹配)
            # 并在 description/suggestion 里大量使用中文合同必现的通用锚点词: 违约金/违约/
            # 责任/管辖/争议解决/预付款/付款/质量/质保/验收/定金/保证金等, 保证高亮
            {"severity": "critical", "source": "合同审核",
             "description": "违约金比例超过司法保护上限，违约金约定过高，违约责任条款约定日违约金超过法定标准",
             "clause": "违约责任 违约金 违约条款 赔偿责任",
             "legal_basis": "《民法典》第585条 违约金 违约责任",
             "suggestion": "建议将违约金调整为每日万分之三至万分之五 违约责任 违约金比例"},
            {"severity": "high", "source": "合同审核",
             "description": "争议解决管辖约定可能被认定无效，争议解决条款 管辖法院 约定管辖不明确",
             "clause": "争议解决 管辖 管辖法院 诉讼 人民法院",
             "legal_basis": "《民事诉讼法》第24条 合同纠纷 被告住所地",
             "suggestion": "建议约定合同履行地或被告所在地人民法院管辖 争议解决 管辖"},
            {"severity": "medium", "source": "合同审核",
             "description": "预付款比例较高，存在资金占用风险 付款方式 付款比例 预付款过高",
             "clause": "付款方式 预付款 付款计划 付款比例 支付方式 结算方式",
             "legal_basis": "《民法典》第510条 付款 价款支付",
             "suggestion": "建议将预付款比例降至20% 预付款 付款方式 分期付款"},
            {"severity": "low", "source": "合同审核",
             "description": "合同标的描述缺少质量标准 质量验收 产品规格不完整",
             "clause": "合同标的 质量标准 验收 质保 产品规格 交付标准",
             "legal_basis": "《民法典》第512条 质量要求 履行标准",
             "suggestion": "建议补充详细的技术规格和验收标准 质量标准 验收 质保期"}
        ],
        "overall_risk_score": 62,  # 综合评分 62 分（中风险区间）
        "risk_level": "Medium",    # 风险等级：中风险
        "need_lawyer_review": True,  # 需要律师复核
        "citations": [  # 引用法条
            {"title": "中华人民共和国民法典", "article_no": "第585条", "content": "当事人可以约定一方违约时应当根据违约情况向对方支付一定数额的违约金..."},
            {"title": "中华人民共和国民事诉讼法", "article_no": "第24条", "content": "因合同纠纷提起的诉讼，由被告住所地或者合同履行地人民法院管辖。"}
        ],
        "final_report_markdown": "# 法智引擎 · 合同智能审核报告\n\n## 审核概要\n\n- **合同类型**: 采购合同\n- **风险评分**: 62分 (中风险)\n- **风险等级**: ⚠️ 中风险 - 建议律师复核\n\n## 风险清单\n\n| # | 级别 | 描述 |\n|---|------|------|\n| 1 | 🔴 严重 | 违约金比例超司法保护上限 |\n| 2 | 🟠 高 | 管辖约定可能无效 |\n| 3 | 🟡 中 | 预付款比例过高 |\n| 4 | 🔵 低 | 缺少质量验收条款 |\n\n> 本报告由 **法智引擎 AI 法律助理** 自动生成"
    }


def _get_compliance_demo_result(doc_text):
    """生成合规审查演示数据（demo 模式或后端不可用时使用）。

    作用：
        返回一份结构完整的合规审查结果字典，结构与 _get_demo_result 一致，
        但风险项聚焦于数据合规/税务合规/劳动合规等合规审查领域。

    参数：
        doc_text (str): 待审查文档原文（若为空则使用默认示例文本）

    返回值：
        dict: 结构与 _get_demo_result 相同的合规审查结果

    可迁移性说明：
        与 _get_demo_result 共享同一字典结构，是合规审查场景的数据夹具。
        迁移到真实后端时后端需返回同结构字典。
    """
    doc = doc_text or "合规审查文档示例"  # 若传入空文本则使用默认示例
    return {
        "output": "# 合规审查报告\n\n本文档存在 **3项合规风险**。",  # 流式输出文本
        "doc_text": doc,
        "merged_risk_items": [  # 3 项合规风险：high/medium/low 各一项
            {"severity": "high", "source": "数据合规",
             "description": "未明确个人信息保护条款，数据处理 隐私保护 个人信息告知义务缺失",
             "clause": "数据保护 个人信息 隐私保护 数据处理 保密信息",
             "legal_basis": "《个人信息保护法》第17条 个人信息 告知",
             "suggestion": "建议增加个人信息处理告知条款 个人信息保护 数据合规 隐私"},
            {"severity": "medium", "source": "税务合规",
             "description": "发票开具与税务承担约定不明确，税务发票 税率 税款承担 增值税",
             "clause": "税务条款 发票开具 税率 税款 增值税 税费承担",
             "legal_basis": "《税收征收管理法》第21条 发票 税务",
             "suggestion": "建议明确发票类型和开具时间 发票 税务合规 增值税专用发票"},
            {"severity": "low", "source": "劳动合规",
             "description": "未涉及员工竞业限制 保密条款 商业秘密 劳动合同",
             "clause": "保密条款 竞业限制 商业秘密 知识产权 劳动合同",
             "legal_basis": "《劳动合同法》第23条 保密义务 竞业限制",
             "suggestion": "建议补充员工保密和竞业限制约定 保密条款 竞业限制 商业秘密"}
        ],
        "overall_risk_score": 45,  # 综合评分 45 分（中风险区间偏低）
        "risk_level": "Medium",
        "need_lawyer_review": True,
        "citations": [  # 引用法条
            {"title": "中华人民共和国个人信息保护法", "article_no": "第17条", "content": "个人信息处理者应当以显著方式告知处理目的、处理方式等事项。"},
            {"title": "中华人民共和国税收征收管理法", "article_no": "第21条", "content": "税务机关是发票的主管机关。"}
        ],
        "final_report_markdown": "# 法智引擎 · 合规审查报告\n\n## 审查概要\n\n- **审查类型**: 合规审查\n- **风险评分**: 45分 (中风险)\n\n## 合规风险\n\n| # | 级别 | 领域 | 描述 |\n|---|------|------|------|\n| 1 | 🟠 高 | 数据合规 | 个人信息保护条款缺失 |\n| 2 | 🟡 中 | 税务合规 | 发票约定不明确 |\n| 3 | 🔵 低 | 劳动合规 | 竞业限制条款缺失 |"
    }


def _highlight_doc(doc_text, risk_items, card_id_prefix=""):
    """根据风险项关键词高亮文档段落，返回 HTML 字符串 + 段落→风险项映射。

    作用：
        将合同/文档原文按段落拆分，根据每项风险的 clause/description/legal_basis/
        suggestion 字段提取"整句 + 中文分词级子关键词"，匹配段落文本。命中则按严重
        级别（critical>high>medium>low，取最严重）为段落添加对应高亮 CSS 类。
        同时建立"段落 → 风险项索引列表"映射，为每个高亮段落注入 onclick，
        点击时平滑滚动到对应的风险卡片并触发高亮动画。

    参数：
        doc_text (str): 文档原文
        risk_items (list[dict]): 风险项列表，每项含 severity/clause/description/
            legal_basis/suggestion/doc_offsets 等字段
        card_id_prefix (str): 风险卡片 HTML id 前缀，与 _render_risk_cards 的
            key_prefix 保持一致，生成的卡片 id 格式为 "risk-card-{prefix}-{idx}"

    返回值：
        tuple: (html_str, para_to_risks)
            - html_str (str): 含 .doc-container 容器与若干 .doc-paragraph 段落 div
            - para_to_risks (dict): {段落索引: [风险项索引列表]}
              可用于外部精准同步高亮 / 跳转行为
    """
    import re as _re

    normalized = doc_text.replace("\r\n", "\n").strip()
    # ---- 段落拆分: 中文合同优先按换行切(哪怕没有空行) ----
    # 中文合同通常用 "\n 一、xxx\n 二、xxx" 编号, 两换行分段策略经常只有 1 段,
    # 导致所有高亮都堆在第一段落上看不出效果. 这里始终按 "\n" 切更贴近中文写作习惯.
    paragraphs_raw = [p.strip() for p in normalized.split("\n") if p.strip()]
    # 但如果存在空行, 仍然优先按双换行聚合一次 (让"一、标题 + 内容"成为同一段, 视觉更好)
    if "\n\n" in normalized:
        merged_paras = [p.strip() for p in normalized.split("\n\n") if p.strip()]
        if len(merged_paras) >= 2:
            paragraphs_raw = merged_paras
    if not paragraphs_raw:
        paragraphs_raw = [normalized]

    # para_highlights: {段落索引: 最严重级别}
    para_highlights = {}
    # para_to_risks: {段落索引: set of 风险项索引} 用于跨面板跳转
    para_to_risks = {}
    sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}

    # ---- 中文常见分词分隔符 ----
    _punct = set(" ，。！？；：,:;!?()（）【】[]「」『』《》\"'<>、…·-/——")

    def _split_cn_keywords(s):
        """把长字符串切成一组"高概率能在合同原文里出现"的关键词.

        策略:
        1) 按标点/空白切出原词;
        2) 对每个长度 >= 2 的中文原词, 生成其 2/3/4 字连续切片窗口;
        3) 剔除纯数字/纯英文/长度 < 2 / 纯标点 / 合同编号类前缀(如"第五条"的数字).
        """
        if not s:
            return []
        s = str(s).strip()
        if not s:
            return []
        res = set()
        # step 1: 按标点切原子词
        tmp_chars = []
        atoms = []
        for ch in s:
            if ch in _punct:
                if tmp_chars:
                    atoms.append("".join(tmp_chars))
                    tmp_chars = []
            else:
                tmp_chars.append(ch)
        if tmp_chars:
            atoms.append("".join(tmp_chars))
        for atom in atoms:
            atom = atom.strip()
            if len(atom) >= 2:
                res.add(atom)
            # step 2: 对中文原子字符, 做 2/3/4 字滑窗切片, 解决"甲乙第五条违约责任"
            # 编号前缀不命中时, 至少"违约责任" 4 字窗口能命中
            L = len(atom)
            if L >= 2:
                for win in (2, 3, 4):
                    if L < win:
                        continue
                    for j in range(L - win + 1):
                        sub = atom[j:j + win]
                        # 过滤: 必须含至少一个中文字符(\u4e00-\u9fff)
                        if _re.search(r"[\u4e00-\u9fff]", sub):
                            res.add(sub)
        # step 3: 过滤太短/数字
        return [k for k in res if len(k) >= 2 and _re.search(r"[\u4e00-\u9fff]", k)]

    # ---- 通用停用词: 2字词中过于泛化的, 出现在几乎所有合同段落里, 易误匹配 ----
    _stopwords = {
        "合同", "约定", "条款", "标准", "法院", "人民", "当事", "对方", "甲方", "乙方",
        "应当", "可以", "不得", "如下", "以下", "上述", "双方", "一方", "履行", "执行",
        "支付", "结算", "金额", "比例", "期限", "日内", "情况", "方式", "内容", "要求",
        "依据", "法律", "法规", "规定", "民事", "公司", "有限", "责任", "管理", "经营",
        "设备", "采购", "供应", "交付", "验收", "服务", "项目", "产品", "质量", "保障",
        "违法", "无效", "认定", "保护", "超过", "过高", "不明", "不确",
    }

    # ---- 字段权重: clause 最具体, suggestion 最泛 ----
    _field_weights = {"clause": 3, "description": 2, "legal_basis": 1, "suggestion": 0.5}

    # ---- 为每个风险项按字段分别提取关键词 ----
    risk_field_keywords = []  # [{field: set(kw_lower)}, ...]
    for risk_idx, risk in enumerate(risk_items):
        field_kws = {}
        for field, weight in _field_weights.items():
            val = risk.get(field, "")
            if not val:
                field_kws[field] = set()
                continue
            val = str(val).strip()
            kws = _split_cn_keywords(val)
            # 额外: description 中用正则抽连续中文词
            if field == "description":
                for k in _re.findall(r"[\u4e00-\u9fff]{2,}", val):
                    if len(k) >= 2:
                        kws.append(k)
            kw_set = set()
            for kw in kws:
                kw = kw.strip().lower()
                if len(kw) >= 2 and kw not in _stopwords:
                    kw_set.add(kw)
            field_kws[field] = kw_set
        risk_field_keywords.append(field_kws)

    # ---- 段落匹配: 字段加权打分, 取最高分风险项 ----
    # para_scores: {段落索引: [(risk_idx, score), ...]}
    para_scores = {}
    for i, para in enumerate(paragraphs_raw):
        para_lower = para.lower()
        for risk_idx, field_kws in enumerate(risk_field_keywords):
            score = 0.0
            for field, kws in field_kws.items():
                weight = _field_weights[field]
                for kw in kws:
                    if kw in para_lower:
                        score += weight
            if score > 0:
                para_scores.setdefault(i, []).append((risk_idx, score))

    # ---- 选出每个段落的最佳匹配 + 记录所有命中风险 ----
    for i, hits in para_scores.items():
        # 按分数降序, 同分按 severity 更严重优先
        hits.sort(key=lambda x: (-x[1], sev_order.get(risk_items[x[0]].get("severity", "low").lower(), 3)))
        best_risk_idx = hits[0][0]
        best_sev = risk_items[best_risk_idx].get("severity", "low").lower()
        para_highlights[i] = best_sev
        # 记录所有命中的风险项 (按分数降序, 即最相关在前)
        para_to_risks[i] = [ri for ri, _ in hits]

    # ---- 兜底: 若一轮下来 0 段被命中, 进入宽松模式 ----
    # (例如 demo 风险项用的全是条款描述与原文完全对不上的文本)
    if not para_highlights and risk_items:
        relaxed_kw = set()
        for risk in risk_items:
            for field in ["clause", "description", "legal_basis", "suggestion"]:
                relaxed_kw.update(_split_cn_keywords(risk.get(field, "")))
        # 再加一些中文合同里几乎必现的通用风险锚点
        for k in ["违约金", "违约责任", "争议解决", "管辖", "预付款", "付款", "质保", "质量",
                  "定金", "保证金", "税率", "合同期限", "验收"]:
            relaxed_kw.add(k)
        relaxed_kw_low = {k.lower() for k in relaxed_kw if len(k) >= 2}
        for i, para in enumerate(paragraphs_raw):
            pl = para.lower()
            match_risks = []
            for r_idx, risk in enumerate(risk_items):
                sev = risk.get("severity", "low").lower()
                risk_kws = _split_cn_keywords(risk.get("clause", "")) + \
                           _split_cn_keywords(risk.get("description", ""))
                risk_kws_low = {k.lower() for k in risk_kws if len(k) >= 2}
                if not risk_kws_low:
                    risk_kws_low = relaxed_kw_low
                if any(rk in pl for rk in risk_kws_low):
                    match_risks.append(r_idx)
            if match_risks:
                # 按 severity 排序
                match_risks.sort(key=lambda ri: sev_order.get(risk_items[ri].get("severity", "low").lower(), 3))
                para_to_risks[i] = match_risks
                para_highlights[i] = risk_items[match_risks[0]].get("severity", "low").lower()

    # para_to_risks 的值已经是按相关度/severity 排序的 list, 直接使用
    mapping_out = para_to_risks

    html_parts = ['<div class="doc-container">']
    for i, para in enumerate(paragraphs_raw):
        sev = para_highlights.get(i, "")
        cls = f"doc-paragraph highlight-{sev}" if sev else "doc-paragraph"
        safe_para = para.replace("<", "&lt;").replace(">", "&gt;")
        para_id = f"doc-para-{card_id_prefix}-{i}"
        if i in mapping_out:
            # target_idx = 最严重的那个风险项(mapping_out[i][0])
            target_idx = mapping_out[i][0]
            # 用 <a> 标签 + href 锚点实现纯 CSS 跳转, 无需 JS
            # 浏览器原生平滑滚动到 #risk-card-{prefix}-{targetIdx}
            risk_anchor = f"risk-card-{card_id_prefix}-{target_idx}"
            html_parts.append(
                f'<a class="{cls}" id="{para_id}" href="#{risk_anchor}" '
                f'title="点击跳转到最严重风险项 #{target_idx+1}">{safe_para}</a>'
            )
        else:
            html_parts.append(f'<div class="{cls}" id="{para_id}">{safe_para}</div>')
    html_parts.append("</div>")
    return "\n".join(html_parts), mapping_out


def _inject_jump_script(para_to_risks, card_id_prefix):
    """(已弃用) 跳转改用纯 CSS 锚点实现, 见 _highlight_doc 中的 <a href> 生成."""
    pass


def _normalize_result(raw_result, task_type, input_text):
    """将 legal_response_sync 的返回值（string 或 dict）统一转换为前端渲染所需的 dict 结构。

    背景：legal_response_sync 可能返回 string（原始文本）或 dict（结构化结果）。
    前端渲染函数（_render_score_overview/_render_risk_cards 等）要求 dict，
    直接传 string 会抛 TypeError: string indices must be integers。
    本函数做"安全适配"：根据返回值类型 + task_type 自动组装出完整结构。

    参数：
        raw_result (str | dict): legal_response_sync 的原始返回值
        task_type (str): 任务类型 ("contract_review" / "compliance_review" / "legal_research")
        input_text (str): 用户输入文本（用于填充 doc_text 字段）

    返回值：
        dict: 完整结构的结果字典，包含前端渲染所需的所有 key
    """
    # Case 1: 已经是 dict 且包含核心字段 → 直接返回
    if isinstance(raw_result, dict) and "overall_risk_score" in raw_result:
        return raw_result

    # Case 2: 是 dict 但缺少部分字段 → 用演示数据补齐缺失项
    if isinstance(raw_result, dict):
        if task_type == "contract_review":
            demo = _get_demo_result(input_text)
        elif task_type == "compliance_review":
            demo = _get_compliance_demo_result(input_text)
        else:
            demo = {}
        for k, v in demo.items():
            if k not in raw_result:
                raw_result[k] = v
        return raw_result

    # Case 3: 是 string（最常见的情况）→ 组装完整 dict
    if isinstance(raw_result, str):
        if task_type == "contract_review":
            demo = _get_demo_result(input_text)
            demo["output"] = raw_result  # 用真实后端文本替换演示 output
            demo["final_report_markdown"] = raw_result
            return demo
        elif task_type == "compliance_review":
            demo = _get_compliance_demo_result(input_text)
            demo["output"] = raw_result
            demo["final_report_markdown"] = raw_result
            return demo
        else:
            # legal_research / 其他：简单结构，只需 output + citations
            return {
                "output": raw_result,
                "final_report_markdown": raw_result,
                "citations": [],
                "merged_risk_items": [],
                "overall_risk_score": 0,
                "risk_level": "Low",
                "need_lawyer_review": False,
                "doc_text": input_text,
            }

    # Case 4: 其他类型 → 兜底返回空结果
    return {
        "output": str(raw_result) if raw_result else "处理完成，但无法解析结果",
        "final_report_markdown": "",
        "citations": [],
        "merged_risk_items": [],
        "overall_risk_score": 0,
        "risk_level": "Low",
        "need_lawyer_review": False,
        "doc_text": input_text,
    }


def _render_risk_cards(risk_items, key_prefix=""):
    """渲染风险卡片列表，含图例、卡片本体与三态交互（采纳/不采纳/修改）。

    作用：
        遍历风险项列表，先渲染顶部图例（统计各严重级别数量），再为每项风险渲染一张
        卡片（含徽章/标题/来源/条款/法条依据/修改建议），并在卡片下方提供"采纳/
        不采纳/修改"三个按钮。点击"修改"会展开文本输入区，确认后进入"已修改"态，
        可重新修改；点击"采纳"/"不采纳"会显示对应状态。所有交互状态通过
        session_state 持久化。

    参数：
        risk_items (list[dict]): 风险项列表，每项含 severity/source/description/clause/
            legal_basis/suggestion
        key_prefix (str): Streamlit widget key 前缀，用于在不同页面/区段复用本函数时
            避免键冲突（如 "home_contract"/"contract"/"compliance"）

    返回值：
        None（直接通过 st.markdown/st.button 渲染到页面，无返回值）

    可迁移性说明：
        该函数与 Streamlit 强耦合（依赖 st.session_state/st.button/st.text_area 等），
        不可直接迁移到非 Streamlit 框架。但卡片 HTML 结构与交互逻辑可作为参考，
        迁移时需替换为对应框架的状态管理与事件绑定机制。
    """
    # action_key: 存储各风险项当前交互状态的 session_state 键
    action_key = f"_actions_{key_prefix}"
    # 首次调用时初始化为空字典 {风险项索引: 状态字符串}
    if action_key not in st.session_state:
        st.session_state[action_key] = {}

    # 统计各严重级别风险数量
    sev_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for r in risk_items:
        sev = r.get("severity", "low").lower()
        if sev in sev_counts:
            sev_counts[sev] += 1

    # 拼接图例 HTML：仅展示数量 > 0 的级别
    legend_html = '<div style="display:flex;gap:16px;margin-bottom:16px;flex-wrap:wrap;">'
    for sev_key, sev_info in SEVERITY_MAP.items():
        if sev_counts.get(sev_key, 0) > 0:
            # 每个图例项：彩色小方块 + "X风险: N项"
            legend_html += f'<div style="display:inline-flex;align-items:center;gap:6px;font-size:12px;color:#6B7280;"><div style="width:12px;height:12px;border-radius:3px;background:{sev_info["color"]};"></div>{sev_info["label"]}风险: {sev_counts[sev_key]}项</div>'
    legend_html += '</div>'
    st.markdown(legend_html, unsafe_allow_html=True)

    # 遍历每个风险项，渲染卡片与交互按钮
    for idx, risk in enumerate(risk_items):
        sev = risk.get("severity", "low").lower()  # 严重级别
        sev_info = SEVERITY_MAP.get(sev, SEVERITY_MAP["low"])  # 级别信息（标签/颜色）
        description = risk.get("description", "未知风险")  # 风险描述
        source = risk.get("source", "")  # 风险来源
        clause = risk.get("clause", "")  # 涉及条款
        legal_basis = risk.get("legal_basis", "")  # 法条依据
        suggestion = risk.get("suggestion", "")  # 修改建议
        # 当前风险项的交互状态（None/accepted/rejected/modify_input/modified）
        current_action = st.session_state[action_key].get(idx, None)

        # 拼接卡片 HTML：含头部（徽章+标题+来源）、正文（条款+法条）、修改建议
        # 每个卡片带唯一 id="risk-card-{key_prefix}-{idx}", 供文档高亮点击跳转使用
        card_html = f'''
        <div class="risk-card {sev}" id="risk-card-{key_prefix}-{idx}">
            <div class="risk-header">
                <span class="risk-badge {sev}">{sev_info['label']}风险</span>
                <span class="risk-title">{description}</span>
                <span class="risk-source">{source}</span>
            </div>
            <div class="risk-body">'''
        # 若有条款信息，渲染 meta 行
        if clause:
            card_html += f'<div class="risk-meta"><span>📌 {clause}</span>'
        # 若有法条依据，追加到 meta 行
        if legal_basis:
            card_html += f'<span>⚖️ {legal_basis}</span>'
        card_html += '</div></div>'
        # 若有修改建议，渲染建议框
        if suggestion:
            card_html += f'<div class="risk-suggestion"><div class="risk-suggestion-label">💡 修改建议</div>{suggestion}</div>'
        card_html += '</div>'
        st.markdown(card_html, unsafe_allow_html=True)

        # 在卡片下方渲染交互按钮，根据 current_action 不同状态展示不同 UI
        with st.container():
            if current_action == "modify_input":
                # ===== 修改输入态：展示文本框 + 确认/取消按钮 =====
                mod_text = st.text_area(
                    "请输入您的修改意见或修改后的内容",
                    height=100,
                    key=f"modify_text_{key_prefix}_{idx}",
                    placeholder="请描述您的修改方案，或直接输入修改后的条款内容...",
                )
                mc1, mc2 = st.columns(2)  # 两列布局：确认 / 取消
                with mc1:
                    if st.button("✅ 确认修改", key=f"confirm_modify_{key_prefix}_{idx}", use_container_width=True):
                        # 确认修改：状态切换为 modified，缓存修改内容
                        st.session_state[action_key][idx] = "modified"
                        st.session_state[f"modified_content_{key_prefix}_{idx}"] = mod_text
                        st.success(f"已确认修改: {description}")
                        st.rerun()  # 触发 Streamlit 重跑，刷新 UI
                with mc2:
                    if st.button("❌ 取消", key=f"cancel_modify_{key_prefix}_{idx}", use_container_width=True):
                        # 取消修改：删除该风险项的状态记录，回到默认三按钮态
                        del st.session_state[action_key][idx]
                        st.rerun()
            elif current_action == "modified":
                # ===== 已修改态：展示修改内容 + 成功提示 + 重新修改按钮 =====
                modified_content = st.session_state.get(f"modified_content_{key_prefix}_{idx}", "")
                # 蓝色信息框展示用户填写的修改内容
                st.markdown(f'<div style="background:rgba(25,118,210,0.08);border:1px solid rgba(25,118,210,0.25);border-radius:8px;padding:10px 14px;margin-bottom:10px;font-size:13px;color:#1F2937;"><strong>📝 您的修改内容：</strong><br>{modified_content}</div>', unsafe_allow_html=True)
                st.success(f"✅ 已修改: {description}")
                # 重新修改按钮：回到 modify_input 态
                if st.button("重新修改", key=f"redo_modify_{key_prefix}_{idx}"):
                    st.session_state[action_key][idx] = "modify_input"
                    st.rerun()
            elif current_action == "accepted":
                # ===== 已采纳态：仅展示成功提示 =====
                st.success(f"✅ 已采纳: {description}")
            elif current_action == "rejected":
                # ===== 已不采纳态：仅展示警告提示 =====
                st.warning(f"❌ 已不采纳: {description}")
            else:
                # ===== 默认态：三按钮（采纳 / 不采纳 / 修改） =====
                col_a, col_b, col_c = st.columns(3)
                with col_a:
                    if st.button("✅ 采纳", key=f"accept_{key_prefix}_{idx}", use_container_width=True):
                        st.session_state[action_key][idx] = "accepted"
                        st.success(f"已采纳: {description}")
                        st.rerun()
                with col_b:
                    if st.button("❌ 不采纳", key=f"reject_{key_prefix}_{idx}", use_container_width=True):
                        st.session_state[action_key][idx] = "rejected"
                        st.rerun()
                with col_c:
                    if st.button("✏️ 修改", key=f"modify_{key_prefix}_{idx}", use_container_width=True):
                        st.session_state[action_key][idx] = "modify_input"
                        st.rerun()


def _render_score_overview(score, risk_level, need_review):
    """渲染顶部风险评分总览卡片（圆形评分 + 等级 + 是否需律师复核提示）。

    作用：
        根据 risk_level（Low/Medium/High）选择对应颜色与描述，渲染一个圆形评分数字
        + 等级标签 + 描述的总览卡片，并根据 need_review 显示警告或成功提示。

    参数：
        score (int): 综合风险评分（0-100）
        risk_level (str): 风险等级，取值 "Low"/"Medium"/"High"
        need_review (bool): 是否需要律师复核

    返回值：
        None（直接通过 st.markdown/st.warning/st.success 渲染）

    可迁移性说明：
        仅依赖 st.markdown 与提示组件，HTML 结构可复用。risk_level 大小写敏感，
        迁移时需确保后端返回的等级字符串与 RISK_LEVEL_MAP 键一致。
    """
    # 从 RISK_LEVEL_MAP 获取等级信息，默认回退到 Medium
    level_info = RISK_LEVEL_MAP.get(risk_level, RISK_LEVEL_MAP["Medium"])
    # 拼接总览卡片 HTML：圆形评分（class 含 risk_level 小写以匹配 CSS）+ 等级信息
    html = f'''
    <div class="risk-overview">
        <div class="score-circle {risk_level.lower()}">{score}</div>
        <div class="overview-info">
            <div class="overview-label">综合风险评分</div>
            <div class="overview-value" style="color:{level_info['color']};">{level_info['label']}</div>
            <div class="overview-desc">{level_info['desc']}</div>
        </div>
    </div>'''
    st.markdown(html, unsafe_allow_html=True)
    # 根据是否需要律师复核显示不同提示
    if need_review:
        st.warning("⚖️ 本合同存在较大风险，建议转介专业律师复核")
    else:
        st.success("✅ 本合同风险较低，可参考使用")


def _render_stat_cards(risk_items):
    """渲染风险数量统计卡片行（critical/high/medium/low/总数 5 个小卡片）。

    作用：
        统计 risk_items 中各严重级别的数量，渲染一行 5 个小卡片（4 个级别 + 1 个总数），
        每个卡片显示对应颜色的数字与标签。

    参数：
        risk_items (list[dict]): 风险项列表

    返回值：
        None（直接通过 st.markdown 渲染）

    可迁移性说明：
        纯 HTML 渲染，无状态依赖，可复用。统计逻辑与 SEVERITY_MAP 的键强耦合，
        迁移时需保持一致。
    """
    # 统计各严重级别数量
    sev_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for r in risk_items:
        sev = r.get("severity", "low").lower()
        if sev in sev_counts:
            sev_counts[sev] += 1
    total = len(risk_items)  # 总数
    
    # 拼接 5 个小卡片的 HTML
    html = f'<div class="stat-row">'
    html += f'<div class="stat-mini critical"><div class="num">{sev_counts["critical"]}</div><div class="label">严重风险</div></div>'
    html += f'<div class="stat-mini high"><div class="num">{sev_counts["high"]}</div><div class="label">高风险</div></div>'
    html += f'<div class="stat-mini medium"><div class="num">{sev_counts["medium"]}</div><div class="label">中风险</div></div>'
    html += f'<div class="stat-mini low"><div class="num">{sev_counts["low"]}</div><div class="label">低风险</div></div>'
    html += f'<div class="stat-mini"><div class="num">{total}</div><div class="label">风险总数</div></div>'
    html += '</div>'
    st.markdown(html, unsafe_allow_html=True)


# ==================== 主页面路由 ====================
# 根据 side radio 选择的 page 路由到对应页面渲染逻辑
if page == "🏠 首页":
    # =========================================================
    # 任务元数据 (集中配置: 问候语/任务介绍/上传类型)
    # TASK_META 字典集中管理 5 种任务类型的元数据，便于扩展与维护
    # =========================================================
    TASK_META = {
        "qa": {
            "greeting": "您好, 我是法智引擎",  # 问候语
            "agent_name": "法智引擎",
            "description": "您可以直接向我提问法律问题，或选择下方的具体任务类型：合同审核、合规审查、法律检索、小红书发布。我会根据您的问题或上传的文档自动调度对应的智能体，返回权威、可追溯的法律结果。",
            "upload_types": ["txt", "md", "docx", "pdf"],  # 支持的上传文件类型
            "upload_label": "上传文档/图片",
        },
        "contract": {
            "greeting": "您好, 我是合同审核智能体",  # 问候语，展示在任务卡片顶部，让用户知道当前是哪个智能体
            "agent_name": "合同审核智能体",  # 智能体名称，用于后端日志记录与状态栏展示
            # 面向用户的介绍词：告诉用户可以上传什么、能问什么问题、审查维度、合规红线原则、最终输出内容
            "description": "您可以上传一份合同，直接问我：\u201c这份合同对我方有没有不利条款？\u201d\u201c违约金比例是否合理？\u201d我会围绕主体资格、内容合法性、商业对等性、文本质量与签署程序五个维度，站在您的立场逐一审查合同是否符合您的经济利益。但请注意：如果发现合规性问题（比如违反法律强制性规定），我不会因为\u201c对您有利\u201d就隐瞒或降级处理——合规红线必须遵守。最终我会输出一份详细的审查报告，标出风险点、修改建议和依据，帮助您促成合同有效履行、维护企业合法权益。",
            "upload_types": ["txt", "md", "docx", "pdf"],  # 支持的上传文件类型：纯文本/Markdown/Word/PDF
            "upload_label": "上传合同文档",  # 上传区域的提示文字，引导用户上传对应文件
        },
        "compliance": {
            "greeting": "您好, 我是合规审查智能体",  # 问候语，展示在任务卡片顶部，让用户知道当前是哪个智能体
            "agent_name": "合规审查智能体",  # 智能体名称，用于后端日志记录与状态栏展示
            # 面向用户的介绍词：告诉用户可以上传什么、能问什么问题、合规体检维度、检查重点领域、合规意见类型、最终输出内容
            "description": "您可以上传一份合同或决策文件，直接问我：\u201c这份合同有没有违规风险？\u201d\u201c涉及数据出境的条款合规吗？\u201d我会围绕合规义务识别、法规监管、内部制度、重点领域及审查闭环五个维度，对您的决策事项进行全面合规体检。我会自动识别适用的法律法规、监管规定和企业内部制度，检查是否存在反垄断、数据合规、出口管制等重点领域的违规风险，并给出明确的合规意见（合规/不合规/附条件合规）。输出内容包括风险点原文、违规依据、整改建议，确保您的经营活动合法合规、不留隐患。",
            "upload_types": ["txt", "md", "docx", "pdf"],  # 支持的上传文件类型：纯文本/Markdown/Word/PDF
            "upload_label": "上传合规文档",  # 上传区域的提示文字，引导用户上传对应文件
        },
        "research": {
            "greeting": "您好, 我是检索智能体",
            "agent_name": "检索智能体",
            "description": "您可以直接向我提问法律问题，比如\"违约金上限是多少？\"\"建设工程合同有哪些强制性规定？\"我会自动检索法律法规、类案判例、行业标准和市场基准，返回完整的法条原文、案例摘要、合规依据，不会对检索结果进行主观解释或综合结论（如需分析建议，请调用问答智能体或其他任务智能体）。",
            "upload_types": ["txt", "md", "docx", "pdf"],
            "upload_label": "上传参考文档",
        },
        "xiaohongshu": {
            "greeting": "您好, 我是小红书发布智能体",
            "agent_name": "小红书发布智能体",
            "description": "您可以输入法律科普主题（如\"劳动合同维权\"\"租房合同避坑\"），或上传参考文档、封面图片素材，我会生成符合小红书风格的爆款标题、分点正文、热门话题标签与配图建议，帮助您高效产出高质量的法律科普笔记。",
            "upload_types": ["png", "jpg", "jpeg", "txt", "md"],  # 小红书场景额外支持图片
            "upload_label": "上传图片/文档素材",
        },
    }

    # 当前页默认使用 qa（智能问答）的元数据（首页固定展示 qa 问候语）
    current_meta = TASK_META["qa"]

    # 渲染首页顶部问候语 + 任务说明卡片
    st.markdown(f"""
    <div class="task-greeting">{current_meta['greeting']}</div>
    <div class="task-intro-box">
        <p>{current_meta['description']}</p>
    </div>
    """, unsafe_allow_html=True)

    # =========================================================
    # 任务类型选择 (多色卡片: 类图二风格, 彩色顶边+蓝色选中态)
    # 使用 Streamlit 原生 columns 布局, 避免 HTML 被解析成原始文本
    # =========================================================
    # task_type_list: 5 张任务卡片配置，每张含 key/label/color/task_type/page_target(侧边栏对应页面名)
    # page_target：与侧边栏 nav_page_radio 的选项保持一致，实现"点击卡片 = 点击侧边栏导航"的跳转逻辑
    task_type_list = [
        {"key": "qa",          "label": "💬 智能问答",   "color": "blue",   "task_type": "",                  "page_target": "🏠 首页"},       # 智能问答 → 首页
        {"key": "contract",    "label": "📋 合同审核",   "color": "orange", "task_type": "contract_review",   "page_target": "📋 合同审核"},   # 合同审核 → 独立页面
        {"key": "compliance",  "label": "🛡️ 合规审查",   "color": "green",  "task_type": "compliance_review", "page_target": "🛡️ 合规审查"},   # 合规审查 → 独立页面
        {"key": "research",    "label": "🔍 法律检索",   "color": "purple", "task_type": "legal_research",    "page_target": "🔍 法律检索"},   # 法律检索 → 独立页面
        {"key": "xiaohongshu", "label": "📱 小红书发布", "color": "pink",   "task_type": "",                  "page_target": "📱 小红书发布"}, # 小红书发布 → 独立页面
    ]
    # 初始化 session_state 中的当前任务 key，默认 "qa"
    if "current_task_key" not in st.session_state:
        st.session_state["current_task_key"] = "qa"

    # 用 st.columns 渲染 5 张卡片 (响应式 5 列)
    # 用 st.button 作为卡片本体 (每个按钮一张卡片), 通过 CSS 改造成彩色卡片样式
    task_cols = st.columns(5)  # 5 等分列
    for idx, t in enumerate(task_type_list):
        with task_cols[idx]:
            active = st.session_state["current_task_key"] == t["key"]  # 是否为当前选中
            # 拆分 emoji 与文字（label 格式为 "💬 智能问答"）
            icon = t["label"].split(" ")[0]
            label_txt = t["label"].split(" ", 1)[1] if " " in t["label"] else t["label"]
            # 按钮显示文本（这里保留 False 分支作为可选换行方案，实际用单行）
            btn_label = f"{icon}\n{label_txt}" if False else f"{icon} {label_txt}"
            # 实际按钮 - 点击时同时切换当前任务 + 侧边栏页面导航（与侧边栏 radio 完全相同的跳转逻辑）
            if st.button(
                btn_label,
                key=f"__tt_card_{t['key']}",
                use_container_width=True,
            ):
                # 记录当前选中的任务 key，用于卡片高亮
                st.session_state["current_task_key"] = t["key"]
                # 关键点：不能直接写 nav_page_radio（radio 实例化后被 widget 锁保护），
                # 改用"中立中间键"，侧边栏顶部的 Pending 钩子会在 st.radio 之前把它迁到 nav_page_radio，
                # 从而实现"首页彩色卡片 = 侧边栏导航"的相同跳转逻辑
                st.session_state["_pending_switch_to_page"] = t["page_target"]
                st.rerun()

    # 将任务类型按钮改造成彩色卡片 (基于 st-key-__tt_card_XXX 容器选择器, Streamlit 原生 class 命名)
    task_card_css = "<style>"
    # color_map: 颜色名到具体 hex 的映射，用于卡片顶部彩色边
    color_map = {
        "blue": "#38bdf8",
        "orange": "#fb923c",
        "green": "#4ade80",
        "purple": "#a78bfa",
        "pink": "#f472b6",
    }
    # 为每张卡片生成对应的 CSS 规则
    for t in task_type_list:
        active = st.session_state["current_task_key"] == t["key"]
        border_top_c = color_map.get(t["color"], "#38bdf8")  # 顶部彩色边的颜色
        # 选中态：蓝色半透明背景 + 蓝色边框 + 蓝色辉光；未选中态：透明边框
        active_bg = (
            "background: rgba(25,118,210,0.08) !important; border: 2px solid #1976D2 !important; box-shadow: 0 0 0 1px rgba(25,118,210,0.2) !important;"
            if active
            else "border: 2px solid #E5E7EB !important;"
        )
        # 选中态文字深蓝，未选中态文字深灰
        active_label_c = "color: #1976D2 !important;" if active else "color: #1F2937 !important;"
        key_sel = f"__tt_card_{t['key']}"  # Streamlit 注入的容器 class 名（st-key-XXX）
        task_card_css += f"""
        div[class*="st-key-{key_sel}"] button,
        div[class*="st-key-{key_sel}"] button[data-testid="stBaseButton-secondary"] {{
            background: #FFFFFF !important;
            {active_bg}
            border-top: 3px solid {border_top_c} !important;
            border-radius: 12px !important;
            padding: 14px 12px !important;
            text-align: center !important;
            white-space: normal !important;
            height: auto !important;
            min-height: 88px !important;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08) !important;
            letter-spacing: 0.5px;
        }}
        div[class*="st-key-{key_sel}"] button p,
        div[class*="st-key-{key_sel}"] button span,
        div[class*="st-key-{key_sel}"] button div,
        div[class*="st-key-{key_sel}"] button[data-testid="stMarkdownContainer"] * {{
            {active_label_c}
            font-weight: 700 !important;
            font-size: 14px !important;
        }}
        div[class*="st-key-{key_sel}"] button:hover {{
            background: #F1F5F9 !important;
            transform: translateY(-2px);
        }}
        """
    task_card_css += "</style>"
    st.markdown(task_card_css, unsafe_allow_html=True)

    # =========================================================
    # 中央大输入区 (类图四: 蓝色大边框输入框 + 思考开关 + 发送按钮)
    # 不使用外层 wrapping div (Streamlit 会破坏 HTML 结构),
    # 改为独立组件样式 + 视觉上的卡片感
    # =========================================================

    # 居中容器: 3 列布局 [1, 6, 1]，中间列承载输入框，两侧留白
    center_col_l, center_col_m, center_col_r = st.columns([1, 6, 1])
    with center_col_m:
        # 处理待填充文本 (来自快捷卡片点击, 必须在 widget 实例化之前写入, 否则 Streamlit 报错)
        # _pending_home_textarea_fill 是快捷卡片点击时设置的临时键，用于将示例文本填入主输入框
        if "_pending_home_textarea_fill" in st.session_state:
            # 将待填充文本写入 home_main_textarea 键，Streamlit 会用该值初始化 textarea
            st.session_state["home_main_textarea"] = st.session_state["_pending_home_textarea_fill"]
            del st.session_state["_pending_home_textarea_fill"]  # 用完即删，避免重复填充
        # 大输入框 (用 CSS 让 textarea 呈现类图四效果)
        user_input = st.text_area(
            "请输入您的问题，支持 Shift + Enter 换行",
            height=130,  # 输入框高度 130px
            placeholder="请输入您的问题，支持 Shift + Enter 换行",
            label_visibility="collapsed",  # 隐藏 label（仅占位用）
            key="home_main_textarea",  # 绑定 session_state 键，便于程序化赋值
        )
        # 对首页主输入框单独加强视觉: 蓝色大边框 + 阴影
        # 通过 :has() 伪类精准定位该 textarea 的外层容器
        st.markdown("""
        <style>
        /* 主输入框外层 div：蓝色大边框 + 圆角 + 阴影 */
        div[data-testid="stTextArea"]:has(textarea[aria-label="请输入您的问题，支持 Shift + Enter 换行"]) > div > div {
            border: 2px solid rgba(25,118,210,0.4) !important;
            border-radius: 18px !important;
            box-shadow: 0 0 0 1px rgba(25,118,210,0.1), 0 4px 16px rgba(0,0,0,0.06) !important;
            background: #ffffff !important;
            transition: all 0.3s;
        }
        /* 主输入框悬停态：边框变亮蓝 + 阴影更深 */
        div[data-testid="stTextArea"]:has(textarea[aria-label="请输入您的问题，支持 Shift + Enter 换行"]):hover > div > div {
            border-color: #1976D2 !important;
            box-shadow: 0 0 0 1px rgba(25,118,210,0.2), 0 6px 20px rgba(0,0,0,0.08) !important;
        }
        /* textarea 本身：白色背景 + 深灰字（与合同审核页相同默认白底黑字风格） */
        div[data-testid="stTextArea"]:has(textarea[aria-label="请输入您的问题，支持 Shift + Enter 换行"]) > div > div > textarea {
            background: #ffffff !important;  /* 文本框底色白色 */
            color: #1f2937 !important;      /* 正文深色（白底黑字风格） */
            border: none !important;
            padding: 18px 20px !important;
            font-size: 15px !important;
            line-height: 1.6 !important;
        }
        /* textarea 占位符颜色：浅灰色（白底风格） */
        div[data-testid="stTextArea"]:has(textarea[aria-label="请输入您的问题，支持 Shift + Enter 换行"]) > div > div > textarea::placeholder {
            color: #9ca3af !important;
            font-weight: 400;
        }
        </style>
        """, unsafe_allow_html=True)

        # ===== 输入框下方：上传区 + 操作按钮（与其他智能体页面布局完全一致）=====
        # 文件上传区：两列布局（文档上传 / 图片上传），与合同审核/合规审查等页面结构相同
        upload_col1, upload_col2 = st.columns([1, 1])
        with upload_col1:
            # 文档上传器：支持多种文档格式，允许多文件
            st.file_uploader(
                "上传文档",
                type=["txt", "md", "docx", "pdf"],
                key="home_upload",
                accept_multiple_files=True,
            )
        with upload_col2:
            # 图片上传器：额外支持 webp
            st.file_uploader(
                "上传图片/截图",
                type=["png", "jpg", "jpeg", "webp"],
                key="home_upload_images",
                accept_multiple_files=True,
            )

        # 深度思考开关：启用后输出更详细的理由与引用
        deep_thinking = st.toggle(
            "🔍 深度思考",
            value=False,
            key="home_deep_thinking",
            help="启用深度法律分析模式, 输出更详细的理由与引用",
        )

        # 提问示例折叠面板（全宽，与其他智能体页面一致）
        with st.expander("💡 提问示例"):
            qa_examples = [
                "这份合同的违约金比例是否合理？",
                "劳动合同解除的法定情形有哪些？",
                "个人信息保护法对数据出境有何要求？",
            ]
            for q in qa_examples:
                st.markdown(f"- {q}")

        # 效果展示按钮（secondary 灰色，全宽，与其他智能体页面一致）
        if st.button("🎭 效果展示", key="toggle_demo_home", type="secondary", use_container_width=True):
            if "home_demo_result" in st.session_state:
                del st.session_state["home_demo_result"]
                st.rerun()
            else:
                st.session_state["home_demo_result"] = True
                st.rerun()

        # 发送分析按钮（primary 蓝色主按钮，全宽，放到最下侧 —— 与其他智能体页面的"开始XX"按钮一致）
        send_pressed = st.button(
            "🚀 发送分析",
            key="home_send_btn",
            type="primary",
            use_container_width=True,
        )

    # =========================================================
    # 底部说明文字 (类图三)
    # =========================================================
    # 渲染设计铁律 + 技术栈说明 + 免责声明
    st.markdown("""
    <div class="footer-desc">
        <div class="principle">设计铁律：AI做前置审查辅助生成风险提示 · 律师做最终决策签章交付</div>
        <div class="tech-stack">依据《律师法》第13/28条 · LangGraph + RAG + Neo4j + FAISS + bge-m3</div>
    </div>
    <div class="footer-disclaimer">
        内容由法智大模型生成，请仔细甄别　|　网站备案号：浙ICP备00000000号　|　© 2026 法智引擎 版权所有
    </div>
    """, unsafe_allow_html=True)

    # =========================================================
    # 提交处理 (发送分析)
    # =========================================================
    # 读取当前选中的任务 key，并从 task_type_list 取出对应 task_type 与 label
    current_key = st.session_state["current_task_key"]
    current_task_map = {t["key"]: t for t in task_type_list}  # 构建 key -> 配置的映射
    selected_task_type = current_task_map.get(current_key, {}).get("task_type", "")  # 任务类型字符串
    selected_label = current_task_map.get(current_key, {}).get("label", "💬 智能问答")  # 任务中文名

    # 仅当点击发送且输入非空时进入处理流程
    if send_pressed and user_input and user_input.strip():
        # 二次校验输入（这里其实是冗余检查，外层已校验）
        if not user_input.strip():
            st.warning("请输入内容")
        else:
            # 构建 kwargs：有 task_type 时包含 task_type 与 deep_thinking，否则仅 deep_thinking
            kwargs = {"task_type": selected_task_type, "deep_thinking": deep_thinking} if selected_task_type else {"deep_thinking": deep_thinking}
            # 过滤掉值为空/False 的键，避免向后端传递无效参数
            full_kwargs = {k: v for k, v in kwargs.items() if v}

            # 渲染助手消息气泡，avatar 用 ⚖️ 天平 emoji
            with st.chat_message("assistant", avatar="⚖️"):
                # 初始思考动画 HTML（含 3 个跳动小圆点）
                thinking_html = f"""
                <div class="thinking-container">
                    <div class="thinking-icon">🧠</div>
                    <div class="thinking-content">
                        <div class="thinking-title">法智深度思考中... (已启用深度分析: {"是" if deep_thinking else "否"})</div>
                        <div class="thinking-steps">
                            • 🔍 正在理解任务: {selected_label}<br>
                            • 📚 检索法律知识库<span class="thinking-dots"><span></span><span></span><span></span></span>
                        </div>
                    </div>
                </div>
                """
                # thinking_ph 是空占位符，后续逐步替换其内容实现动画
                thinking_ph = st.empty()
                thinking_ph.markdown(thinking_html, unsafe_allow_html=True)
                # 模拟思考步骤动画
                # 5 个思考步骤，逐步刷新 thinking_ph 内容
                thinking_steps = [
                    f"🔍 正在处理任务类型: {selected_label}",
                    "📖 解析输入内容与结构...",
                    "📚 检索法律法规与司法解释...",
                    "⚖️ 匹配判例与司法规则...",
                    "✅ 生成最终输出..."
                ]
                for i, step in enumerate(thinking_steps):
                    # 前 4 步 0.35s 延迟，最后一步 0.6s 延迟（模拟收尾耗时）
                    time.sleep(0.35 if i < 4 else 0.6)
                    # 跳动小圆点 HTML（最后一步不显示）
                    dots = "<span class='thinking-dots'><span></span><span></span><span></span></span>"
                    step_html = f"""
                    <div class="thinking-container">
                        <div class="thinking-icon">🧠</div>
                        <div class="thinking-content">
                            <div class="thinking-title">法智深度思考中 ({i+1}/{len(thinking_steps)})</div>
                            <div class="thinking-steps">• {step} {dots if i < len(thinking_steps)-1 else ""}</div>
                        </div>
                    </div>
                    """
                    thinking_ph.markdown(step_html, unsafe_allow_html=True)

                # 思考动画结束，清空占位符并加分隔线
                thinking_ph.empty()
                st.markdown("---")

                # ===== 按当前任务类型分流渲染结果 =====
                if current_key == "contract":
                    # ========== 合同审核分支 ==========
                    # 获取合同演示结果并缓存到 session_state
                    result = _get_demo_result(user_input)
                    st.session_state["contract_full_result"] = result

                    # 流式输出 output 文本（每 6 字符一帧）
                    output_area = st.empty()  # 空占位符用于流式刷新
                    output_text = result.get("output", "")
                    displayed = ""  # 已显示的累积文本
                    for i in range(0, len(output_text), 6):
                        displayed += output_text[i:i+6]
                        output_area.markdown(displayed, unsafe_allow_html=True)
                        time.sleep(0.02)  # 20ms 打字延迟

                    st.markdown("---")
                    # 渲染审核结果总览（评分 + 等级 + 律师复核提示）
                    st.markdown("### 📊 审核结果概览")
                    _render_score_overview(result["overall_risk_score"], result["risk_level"], result["need_lawyer_review"])

                    # 左右两列：左侧合同原文高亮，右侧风险清单
                    left_col, right_col = st.columns([2, 3])  # 比例 2:3
                    with left_col:
                        st.markdown("### 📄 合同原文 (风险标注)")
                        # 显示字符数与风险项数
                        st.markdown(f"<div style='color:#6B7280;font-size:12px;margin-bottom:8px;'>共 {len(result['doc_text'])} 字符 · {len(result['merged_risk_items'])} 项风险已标注</div>", unsafe_allow_html=True)
                        # 渲染高亮文档 (带 card_id_prefix, 与 _render_risk_cards 的 key_prefix 对齐)
                        if result["doc_text"]:
                            html_doc, _ = _highlight_doc(result["doc_text"], result["merged_risk_items"], card_id_prefix="home_contract")
                            st.markdown(html_doc, unsafe_allow_html=True)
                    with right_col:
                        st.markdown("### 🎯 风险清单")
                        # 先渲染统计卡片，再渲染风险卡片（含三态交互）
                        _render_stat_cards(result["merged_risk_items"])
                        _render_risk_cards(result["merged_risk_items"], key_prefix="home_contract")

                        # 渲染引用法条（最多 3 条，每条用 expander 折叠）
                        if result.get("citations"):
                            st.markdown("### 📚 引用法条")
                            for i, c in enumerate(result["citations"][:3], 1):
                                with st.expander(f"{i}. {c.get('title', '')} {c.get('article_no', '')}"):
                                    st.markdown(c.get("content", ""))

                elif current_key == "compliance":
                    # ========== 合规审查分支 ==========
                    # 获取合规演示结果并缓存
                    result = _get_compliance_demo_result(user_input)
                    st.session_state["compliance_full_result"] = result

                    # 流式输出 output 文本
                    output_area = st.empty()
                    output_text = result.get("output", "")
                    displayed = ""
                    for i in range(0, len(output_text), 6):
                        displayed += output_text[i:i+6]
                        output_area.markdown(displayed, unsafe_allow_html=True)
                        time.sleep(0.02)

                    st.markdown("---")
                    # 渲染合规审查总览
                    st.markdown("### 🛡️ 合规审查概览")
                    _render_score_overview(result["overall_risk_score"], result["risk_level"], result["need_lawyer_review"])

                    # 左右两列：左侧文档原文，右侧合规风险
                    left_col, right_col = st.columns([2, 3])
                    with left_col:
                        st.markdown("### 📄 文档原文")
                        if result.get("doc_text"):
                            html_doc, _ = _highlight_doc(result.get("doc_text", ""), result.get("merged_risk_items", []), card_id_prefix="home_compliance")
                            st.markdown(html_doc, unsafe_allow_html=True)
                    with right_col:
                        st.markdown("### 🎯 合规风险")
                        risks = result.get("merged_risk_items", [])
                        if risks:
                            _render_stat_cards(risks)
                            _render_risk_cards(risks, key_prefix="home_compliance")

                else:
                    # ========== 智能问答/通用 ==========
                    with st.spinner("⚖️ 法智引擎正在思考..."):
                        try:
                            if not HAS_BACKEND or demo_mode:
                                demo_answer = f"""### ⚖️ 法律分析

根据您的问题「**{user_input[:60]}**」，我为您提供以下分析：

---

**一、相关法律规定**
根据《中华人民共和国民法典》《民事诉讼法》等相关规定，结合问题性质，处理需要结合具体情况分析。

**二、关键要点**
1. 首先需要确认您的具体情况和诉求
2. 建议保留相关证据材料（合同、聊天记录、支付凭证等）
3. 可考虑寻求专业律师协助（建议执业律师介入）

**三、建议操作**
- 收集整理相关证据
- 咨询专业法律人士确认请求权基础
- 根据具体情况选择协商、调解、诉讼等维权途径

> ⚠️ 以上为一般性法律建议，具体情况请咨询**执业律师**并结合实际材料判断。
"""
                            else:
                                try:
                                    response = legal_response_sync(user_input)
                                    if isinstance(response, dict):
                                        demo_answer = response.get("output", str(response))
                                    else:
                                        demo_answer = str(response)
                                except Exception as e2:
                                    print(f"首页问答 legal_response_sync 调用异常: {e2}")
                                    demo_answer = ""
                                if not demo_answer or not demo_answer.strip():
                                    demo_answer = f"""### ⚖️ 法律分析

根据您的问题「**{user_input[:60]}**」，我为您提供以下分析：

**一、相关法律规定**
根据相关法律法规，结合问题性质分析处理方案。

**二、关键要点**
1. 确认具体情况和诉求
2. 保留相关证据材料
3. 寻求专业律师协助

> ⚠️ 以上为一般性法律建议，具体情况请咨询执业律师。
"""
                        except Exception as e:
                            print(f"首页问答后端调用失败, 回退演示数据: {e}")
                            demo_answer = f"""### ⚠️ 服务暂时不可用

无法连接后端服务，请稍后重试或启用演示模式。

**您的问题**: {user_input[:60]}
"""
                    # 流式输出回答（每 4 字符一帧，比合同/合规更快）
                    output_area = st.empty()
                    displayed = ""
                    for i in range(0, len(demo_answer), 4):
                        displayed += demo_answer[i:i+4]
                        output_area.markdown(displayed, unsafe_allow_html=True)
                        time.sleep(0.02)


# ==================== 合同审核独立页面 ====================
elif page == "📋 合同审核":
    # 合同审核页面元数据（与首页 contract 任务一致）
    # greeting: 问候语，展示在页面顶部；description: 面向用户的介绍词，说明智能体能做什么
    CONTRACT_META = {
        "greeting": "您好, 我是合同审核智能体",  # 问候语，展示在独立页面顶部，让用户知道当前是哪个智能体
        # 面向用户的介绍词：告诉用户可以上传什么、能问什么问题、审查维度、合规红线原则、最终输出内容
        "description": "您可以上传一份合同，直接问我：\u201c这份合同对我方有没有不利条款？\u201d\u201c违约金比例是否合理？\u201d我会围绕主体资格、内容合法性、商业对等性、文本质量与签署程序五个维度，站在您的立场逐一审查合同是否符合您的经济利益。但请注意：如果发现合规性问题（比如违反法律强制性规定），我不会因为\u201c对您有利\u201d就隐瞒或降级处理——合规红线必须遵守。最终我会输出一份详细的审查报告，标出风险点、修改建议和依据，帮助您促成合同有效履行、维护企业合法权益。",
    }
    # 渲染问候语与说明卡片
    st.markdown(f"""
    <div class="task-greeting">{CONTRACT_META['greeting']}</div>
    <div class="task-intro-box">
        <p>{CONTRACT_META['description']}</p>
    </div>
    """, unsafe_allow_html=True)

    # 合同文本输入框（粘贴方式）
    contract_text = st.text_area("粘贴合同文本", height=200, placeholder="甲方A公司向乙方B公司采购电脑100台...", key="contract_text_area")
    
    # 文件上传区：两列布局（文档上传 / 图片上传）
    col_upload1, col_upload2 = st.columns([1, 1])
    with col_upload1:
        uploaded_file = st.file_uploader("上传合同文档", type=["txt", "md", "docx", "pdf"], key="contract_upload", accept_multiple_files=True)
    with col_upload2:
        uploaded_images = st.file_uploader("上传合同相关图片/截图", type=["png", "jpg", "jpeg", "webp"], key="contract_upload_images", accept_multiple_files=True)

    # 操作区：所有控件左对齐、等宽纵向排列；开始审核按钮放到最下方（与其他智能体页统一的布局逻辑）
    # 顺序：您的立场 selectbox → 💡 提问示例 expander → 🎭 效果展示 button → 🔍 开始审核 button
    # 1) 立场选择：自动识别 / 甲方 / 乙方
    # 【注意】st.selectbox 在部分 Streamlit 版本不支持 use_container_width 参数（会抛 TypeError），
    # 因此保持默认宽度；Streamlit st.selectbox 默认就是 100% 容器宽度渲染，视觉上与 expander/button 等宽
    user_side = st.selectbox(
        "您的立场",
        ["自动识别", "甲方", "乙方"],
    )

    # 2) 提问示例折叠面板（默认全宽，自动与其他控件宽度一致，左对齐）
    with st.expander("💡 提问示例"):
        question_examples = [
            "这份采购合同的违约金比例是否合理？",
            "合同中的付款条款有哪些风险？",
            "如何修改争议解决条款使其更有利？",
        ]
        for q in question_examples:
            st.markdown(f"- {q}")

    # 3) 效果展示切换按钮（全宽，左对齐，与 selectbox/expander 宽度相同；显式 secondary 灰色，只让"开始审核"为蓝色主按钮）
    if st.button("🎭 效果展示", key="toggle_demo_contract", type="secondary", use_container_width=True):
        if "contract_full_result" in st.session_state:
            # 已有结果则清除并刷新（隐藏结果）
            del st.session_state["contract_full_result"]
            st.rerun()
        else:
            # 无结果则填入示例合同文本并生成演示结果
            demo_text = "甲方：上海智算科技有限公司 乙方：北京鸿图电子设备有限公司\n\n第一条 合同标的\n甲方向乙方采购笔记本电脑100台。\n\n第二条 付款方式\n预付款30%，货到付款60%，质保金10%。\n\n第三条 违约责任\n逾期交货每日千分之三违约金。\n\n第四条 争议解决\n向甲方所在地法院起诉。"
            st.session_state["contract_full_result"] = _get_demo_result(demo_text)
            st.rerun()

    # 4) 开始审核按钮：按要求放在最下侧（primary 主色按钮，全宽）
    if st.button("🔍 开始审核", type="primary", use_container_width=True, key="start_contract"):
        input_text = contract_text.strip()  # 取粘贴文本
        # 若粘贴为空但上传了文件，则尝试读取第一个文件内容
        if not input_text and uploaded_file:
            try:
                # accept_multiple_files=True 返回列表，取第一个文件
                first_file = uploaded_file[0] if isinstance(uploaded_file, list) else uploaded_file
                input_text = first_file.getvalue().decode("utf-8")
            except:
                input_text = "已上传文件"  # 解码失败时用占位文本
        if input_text:
            with st.spinner("⚖️ 法智引擎正在审核..."):
                # 根据演示模式选择数据源
                if demo_mode or not HAS_BACKEND:
                    # 演示模式 / 后端不可用：使用本地演示数据
                    result = _get_demo_result(input_text)
                else:
                    # 真实后端: 只允许 子进程隔离 调用(_run_backend_isolated).
                    # === 严禁在主进程内直接调 legal_response_sync ===
                    # 原因: compliance_review_node 调用链里可能触发 pandas/numpy/numexpr
                    # C 扩展级的硬崩溃 (segfault / abort), 这种崩溃 Python try/except
                    # 抓不到, 会把整个 Streamlit 主进程一起弄死 -> 前端报 Connection Error.
                    # 隔离调用若失败 (子进程崩溃/超时/编码错误/返回__cli_error__) 则直接 fallback demo.
                    raw_full = _run_backend_isolated(input_text, task_type="contract_review")
                    if raw_full is not None:
                        try:
                            result = _normalize_result(raw_full, "contract_review", input_text)
                        except Exception as e_backend:
                            print(f"合同审核 _normalize_result 异常, 回退 demo: {e_backend}")
                            result = _get_demo_result(input_text)
                    else:
                        print("合同审核 隔离子进程未拿到结果, 直接回退 demo 数据(避免主进程被崩溃)")
                        result = _get_demo_result(input_text)
                st.session_state["contract_full_result"] = result  # 缓存结果
                # 立刻 rerun: 让 session_state 的结果在右侧结果区渲染出来, 避免 spinner 解除后
                # 需要用户再手动点一个按钮才刷新 UI (老版本浏览器常见的"前端还在加载"体验问题)
                st.rerun()
        else:
            st.warning("请上传文件或粘贴文本")

    # 若 session_state 中已有结果，则渲染结果区
    if "contract_full_result" in st.session_state:
        result = st.session_state["contract_full_result"]
        # 渲染评分总览
        _render_score_overview(result["overall_risk_score"], result["risk_level"], result["need_lawyer_review"])

        # 左右两列：合同原文 / 风险清单
        left_col, right_col = st.columns([2, 3])
        with left_col:
            st.markdown("### 📄 合同原文")
            if result.get("doc_text"):
                html_doc, _ = _highlight_doc(result["doc_text"], result.get("merged_risk_items", []), card_id_prefix="contract")
                st.markdown(html_doc, unsafe_allow_html=True)
        with right_col:
            st.markdown("### 🎯 风险清单")
            risks = result.get("merged_risk_items", [])
            if risks:
                _render_stat_cards(risks)
                _render_risk_cards(risks, key_prefix="contract")
            else:
                st.success("✅ 未检测到风险")

        # 完整审核报告折叠面板
        with st.expander("📋 查看完整审核报告"):
            st.markdown(result.get("final_report_markdown", result.get("output", "")), unsafe_allow_html=True)


# ==================== 合规审查独立页面 ====================
elif page == "🛡️ 合规审查":
    # 合规审查页面元数据
    # greeting: 问候语，展示在页面顶部；description: 面向用户的介绍词，说明智能体能做什么
    COMPLIANCE_META = {
        "greeting": "您好, 我是合规审查智能体",  # 问候语，展示在独立页面顶部，让用户知道当前是哪个智能体
        # 面向用户的介绍词：告诉用户可以上传什么、能问什么问题、合规体检维度、检查重点领域、合规意见类型、最终输出内容
        "description": "您可以上传一份合同或决策文件，直接问我：\u201c这份合同有没有违规风险？\u201d\u201c涉及数据出境的条款合规吗？\u201d我会围绕合规义务识别、法规监管、内部制度、重点领域及审查闭环五个维度，对您的决策事项进行全面合规体检。我会自动识别适用的法律法规、监管规定和企业内部制度，检查是否存在反垄断、数据合规、出口管制等重点领域的违规风险，并给出明确的合规意见（合规/不合规/附条件合规）。输出内容包括风险点原文、违规依据、整改建议，确保您的经营活动合法合规、不留隐患。",
    }
    # 渲染问候语与说明卡片
    st.markdown(f"""
    <div class="task-greeting">{COMPLIANCE_META['greeting']}</div>
    <div class="task-intro-box">
        <p>{COMPLIANCE_META['description']}</p>
    </div>
    """, unsafe_allow_html=True)

    # 待审查文档输入框
    compliance_text = st.text_area("粘贴待审查文档", height=200, placeholder="粘贴合同/制度/流程文档...", key="compliance_text_area")
    
    # 文件上传区：两列布局
    col_u1, col_u2 = st.columns([1, 1])
    with col_u1:
        compliance_upload = st.file_uploader("上传合规文档", type=["txt", "md", "docx", "pdf"], key="compliance_upload", accept_multiple_files=True)
    with col_u2:
        compliance_images = st.file_uploader("上传合规相关图片/截图", type=["png", "jpg", "jpeg", "webp"], key="compliance_upload_images", accept_multiple_files=True)

    # 操作区：与合同审核页保持相同的布局逻辑（等宽/左对齐，开始审查按钮放到最下侧）
    # 顺序：💡 提问示例 expander → 🎭 效果展示 button → 🛡️ 开始合规审查 button

    # 1) 提问示例折叠面板（全宽，左对齐）
    with st.expander("💡 提问示例"):
        question_examples = [
            "数据合规方面需要关注哪些要点？",
            "这份文档是否符合税务合规要求？",
            "如何完善员工竞业限制条款？",
        ]
        for q in question_examples:
            st.markdown(f"- {q}")

    # 2) 效果展示切换按钮（全宽，左对齐；显式 secondary 灰色，只让"开始合规审查"为蓝色主按钮）
    if st.button("🎭 效果展示", key="toggle_demo_compliance", type="secondary", use_container_width=True):
        if "compliance_full_result" in st.session_state:
            # 已有结果则清除
            del st.session_state["compliance_full_result"]
            st.rerun()
        else:
            # 无结果则填入示例并生成演示结果
            st.session_state["compliance_full_result"] = _get_compliance_demo_result("合规审查文档示例")
            st.rerun()

    # 3) 开始合规审查按钮：按要求放到最下侧（primary 主色按钮，全宽）
    if st.button("🛡️ 开始合规审查", type="primary", use_container_width=True, key="start_compliance"):
        input_text = compliance_text.strip()  # 取粘贴文本
        # 若粘贴为空但上传了文件，则尝试读取第一个文件内容
        if not input_text and compliance_upload:
            try:
                first_file = compliance_upload[0] if isinstance(compliance_upload, list) else compliance_upload
                input_text = first_file.getvalue().decode("utf-8")
            except:
                input_text = "已上传文件"
        if input_text:
            with st.spinner("⚖️ 法智引擎正在审查..."):
                # 根据演示模式选择数据源
                if demo_mode or not HAS_BACKEND:
                    result = _get_compliance_demo_result(input_text)
                else:
                    # === 严禁同进程调 legal_response_sync; 只用隔离子进程, 失败直接 fallback demo ===
                    raw_full = _run_backend_isolated(input_text, task_type="compliance_review")
                    if raw_full is not None:
                        try:
                            result = _normalize_result(raw_full, "compliance_review", input_text)
                        except Exception as e_backend:
                            print(f"合规审查 _normalize_result 异常, 回退 demo: {e_backend}")
                            result = _get_compliance_demo_result(input_text)
                    else:
                        print("合规审查 隔离子进程未拿到结果, 直接回退 demo 数据(避免主进程被崩溃)")
                        result = _get_compliance_demo_result(input_text)
                st.session_state["compliance_full_result"] = result
                # 同合同审核页: 立刻 rerun 使结果区渲染出来, 解决 spinner 结束后前端仍卡在 "加载中"
                st.rerun()
        else:
            st.warning("请上传文件或粘贴待审查文档内容")

    # 渲染结果区
    if "compliance_full_result" in st.session_state:
        result = st.session_state["compliance_full_result"]
        # 评分总览
        _render_score_overview(result["overall_risk_score"], result["risk_level"], result["need_lawyer_review"])

        # 左右两列：文档原文 / 合规风险
        left_col, right_col = st.columns([2, 3])
        with left_col:
            st.markdown("### 📄 文档原文")
            if result.get("doc_text"):
                html_doc, _ = _highlight_doc(result["doc_text"], result.get("merged_risk_items", []), card_id_prefix="compliance")
                st.markdown(html_doc, unsafe_allow_html=True)
        with right_col:
            st.markdown("### 🎯 合规风险")
            risks = result.get("merged_risk_items", [])
            if risks:
                _render_stat_cards(risks)
                _render_risk_cards(risks, key_prefix="compliance")
            else:
                st.success("✅ 未检测到合规风险")


# ==================== 法律检索独立页面 ====================
# —— 设计原则：纯法条原文检索，不生成任何主观分析/结论/建议，所有结果可溯源至具体法律条文
elif page == "🔍 法律检索":
    # === 清理旧版缓存：之前的 research_full_result 可能是合同审核格式（含"法律检索报告/基本信息/风险评估"），
    # 需要在页面加载时检测并清除，确保只展示纯法条原文 ===
    if "research_full_result" in st.session_state:
        _old = st.session_state["research_full_result"]
        _old_output = ""
        if isinstance(_old, str):
            _old_output = _old
        elif isinstance(_old, dict):
            _old_output = _old.get("output", "") or _old.get("final_report_markdown", "")
        if "法律检索报告" in _old_output or "基本信息" in _old_output or "风险评估" in _old_output:
            del st.session_state["research_full_result"]
    
    # 法律检索页面元数据（说明文字已明确强调"只返回法条原文，不做主观分析"）
    RESEARCH_META = {
        "greeting": "您好, 我是检索智能体",
        "description": "您可以直接向我提问法律问题，比如\"违约金上限是多少？\"\"建设工程合同有哪些强制性规定？\"我会自动检索法律法规、类案判例，仅返回完整的法条原文与出处，不做任何主观分析、解释或综合结论（如需分析建议，请调用问答智能体或其他任务智能体）。",
    }
    # 渲染问候语与说明卡片
    st.markdown(f"""
    <div class="task-greeting">{RESEARCH_META['greeting']}</div>
    <div class="task-intro-box">
        <p>{RESEARCH_META['description']}</p>
    </div>
    """, unsafe_allow_html=True)

    # 检索关键词输入框
    query = st.text_area("输入检索关键词或描述", height=150, placeholder="如: 违约金、民法典第585条...", key="research_query_area")

    # 1) 检索示例折叠面板
    with st.expander("💡 检索示例"):
        research_examples = [
            "民法典中关于违约金的规定",
            "劳动合同法第47条经济补偿标准",
            "个人信息保护法第17条告知义务",
        ]
        for q_item in research_examples:
            st.markdown(f"- {q_item}")

    # —— 纯法条原文演示数据生成（仅含法条原文与出处，无主观分析）——
    # 说明：法律检索智能体严格遵循"只返回法条原文 + 可溯源 + 无主观分析"原则，
    # 因此不调用后端 legal_response_sync（后端返回的是合同审核格式，含主观分析），
    # 统一使用本地生成的纯法条原文数据。
    def _build_research_demo(query_text):
        """构建纯法条原文检索结果。

        严格遵循"只返回法条原文 + 可溯源 + 无主观分析"原则：
        - output: 纯 Markdown 法条原文，每条标注法律名称 + 条款号 + 发布机关 + 发布日期 + 原文内容
        - citations: 结构化溯源信息（法律名称/条款号/原文/发布机关/发布日期）
        """
        q = (query_text or "违约金").strip()
        # 根据关键词动态选择法条（简单匹配）
        q_lower = q.lower()
        
        # 默认法条：违约金相关（民法典585/777 + 买卖合同司法解释28）
        laws = [
            {
                "title": "中华人民共和国民法典",
                "article_no": "第五百八十五条",
                "content": "当事人可以约定一方违约时应当根据违约情况向对方支付一定数额的违约金，也可以约定因违约产生的损失赔偿额的计算方法。\n\n约定的违约金低于造成的损失的，人民法院或者仲裁机构可以根据当事人的请求予以增加；约定的违约金过分高于造成的损失的，人民法院或者仲裁机构可以根据当事人的请求予以适当减少。\n\n当事人就迟延履行约定违约金的，违约方支付违约金后，还应当履行债务。",
                "source": "全国人民代表大会",
                "publish_date": "2020-05-28",
            },
            {
                "title": "中华人民共和国民法典",
                "article_no": "第五百七十七条",
                "content": "当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任。",
                "source": "全国人民代表大会",
                "publish_date": "2020-05-28",
            },
            {
                "title": "最高人民法院关于审理买卖合同纠纷案件适用法律问题的解释",
                "article_no": "第二十八条",
                "content": "买卖合同当事人一方以对方违约造成的损失超过违约金为由主张增加违约金的，人民法院应当以违约造成的损失为基础，兼顾合同的履行情况、当事人的过错程度以及预期利益等因素，根据公平原则和诚实信用原则予以衡量。",
                "source": "最高人民法院",
                "publish_date": "2020-12-29",
            },
        ]
        
        # 劳动合同关键词 → 劳动合同法相关法条
        if any(kw in q for kw in ["劳动", "合同", "经济补偿", "裁员", "解除"]) and "劳动" in q:
            laws = [
                {
                    "title": "中华人民共和国劳动合同法",
                    "article_no": "第四十七条",
                    "content": "经济补偿按劳动者在本单位工作的年限，每满一年支付一个月工资的标准向劳动者支付。六个月以上不满一年的，按一年计算；不满六个月的，向劳动者支付半个月工资的经济补偿。\n\n劳动者月工资高于用人单位所在直辖市、设区的市级人民政府公布的本地区上年度职工月平均工资三倍的，向其支付经济补偿的标准按职工月平均工资三倍的数额支付，向其支付经济补偿的年限最高不超过十二年。\n\n本条所称月工资是指劳动者在劳动合同解除或者终止前十二个月的平均工资。",
                    "source": "全国人民代表大会常务委员会",
                    "publish_date": "2012-12-28",
                },
                {
                    "title": "中华人民共和国劳动合同法",
                    "article_no": "第八十七条",
                    "content": "用人单位违反本法规定解除或者终止劳动合同的，应当依照本法第四十七条规定的经济补偿标准的二倍向劳动者支付赔偿金。",
                    "source": "全国人民代表大会常务委员会",
                    "publish_date": "2012-12-28",
                },
                {
                    "title": "中华人民共和国劳动合同法",
                    "article_no": "第三十八条",
                    "content": "用人单位有下列情形之一的，劳动者可以解除劳动合同：\n（一）未按照劳动合同约定提供劳动保护或者劳动条件的；\n（二）未及时足额支付劳动报酬的；\n（三）未依法为劳动者缴纳社会保险费的；\n（四）用人单位的规章制度违反法律、法规的规定，损害劳动者权益的；\n（五）因本法第二十六条第一款规定的情形致使劳动合同无效的；\n（六）法律、行政法规规定劳动者可以解除劳动合同的其他情形。",
                    "source": "全国人民代表大会常务委员会",
                    "publish_date": "2012-12-28",
                },
            ]
        
        # 个人信息/隐私关键词
        elif any(kw in q for kw in ["个人信息", "隐私", "数据", "告知"]):
            laws = [
                {
                    "title": "中华人民共和国个人信息保护法",
                    "article_no": "第十七条",
                    "content": "个人信息处理者在处理个人信息前，应当以显著方式、清晰易懂的语言真实、准确、完整地向个人告知下列事项：\n（一）个人信息处理者的名称或者姓名和联系方式；\n（二）个人信息的处理目的、处理方式，处理的个人信息种类、保存期限；\n（三）个人行使权利的方式和程序；\n（四）法律、行政法规规定应当告知的其他事项。",
                    "source": "全国人民代表大会常务委员会",
                    "publish_date": "2021-08-20",
                },
                {
                    "title": "中华人民共和国个人信息保护法",
                    "article_no": "第六条",
                    "content": "处理个人信息应当具有明确、合理的目的，并应当与处理目的直接相关，采取对个人权益影响最小的方式。\n\n收集个人信息，应当限于实现处理目的的最小范围，不得过度收集个人信息。",
                    "source": "全国人民代表大会常务委员会",
                    "publish_date": "2021-08-20",
                },
            ]
        
        # 构建 Markdown 原文
        md_parts = [f"## 📋 法条检索结果（关键词：{q}）\n"]
        for i, law in enumerate(laws, 1):
            md_parts.append(f"---\n")
            md_parts.append(f"### {i}. 《{law['title']}》{law['article_no']}")
            md_parts.append(f"**发布机关**: {law['source']}  ")
            md_parts.append(f"**发布日期**: {law['publish_date']}  ")
            md_parts.append(f"**原文**:")
            md_parts.append(law['content'])
            md_parts.append("")
        md_parts.append("---")
        md_parts.append("")
        md_parts.append("> ⚠️ 以上为检索到的法条原文，未包含任何主观分析或建议。如需法律解读，请使用问答智能体。")
        
        return {
            "output": "\n".join(md_parts),
            "citations": laws,
        }

    # 2) 效果展示切换按钮（演示数据 = 纯法条原文）
    if st.button("🎭 效果展示", key="toggle_demo_research", type="secondary", use_container_width=True):
        if "research_full_result" in st.session_state:
            del st.session_state["research_full_result"]
            st.rerun()
        else:
            st.session_state["research_full_result"] = _build_research_demo("违约金")
            st.rerun()

    # 3) 开始检索按钮 —— 永远走纯法条原文，不调用后端
    if st.button("🔍 开始检索", type="primary", use_container_width=True, key="start_research"):
        input_query = query.strip()
        # 若输入为空但上传了文件，则读取第一个文件内容作为检索文本
        if not input_query and research_upload:
            try:
                first_file = research_upload[0] if isinstance(research_upload, list) else research_upload
                input_query = first_file.getvalue().decode("utf-8")
            except:
                input_query = "已上传文件"
        if input_query:
            with st.spinner("⚖️ 法智引擎正在检索法条原文..."):
                # 法律检索智能体：始终返回纯法条原文，不调用后端（后端返回合同审核格式含主观分析）
                result = _build_research_demo(input_query)
                st.session_state["research_full_result"] = result
        else:
            st.warning("请输入检索关键词")

    # —— 渲染检索结果：纯法条原文展示，无主观分析 ——
    if "research_full_result" in st.session_state:
        result = st.session_state["research_full_result"]
        st.markdown("### 📋 法条原文检索结果")
        
        # 优先渲染 output（纯法条 Markdown）
        output_text = result.get("output", "")
        if output_text:
            st.markdown(output_text, unsafe_allow_html=True)
        
        # 可溯源法条出处（折叠面板）
        if result.get("citations"):
            with st.expander("📚 法条溯源（共 {} 条）".format(len(result["citations"]))):
                for cite in result["citations"]:
                    # 每条溯源卡片：法律名 + 条款号 + 原文 + 发布机关 + 日期
                    st.markdown(f"**🔖 {cite.get('title', '未知')} {cite.get('article_no', '')}**")
                    st.markdown(f"> {cite.get('content', '')}")
                    source_info = []
                    if cite.get("source"):
                        source_info.append(f"发布机关：{cite['source']}")
                    if cite.get("publish_date"):
                        source_info.append(f"发布日期：{cite['publish_date']}")
                    if source_info:
                        st.markdown(f"<span style='color:#6B7280;font-size:12px;'>{' | '.join(source_info)}</span>", unsafe_allow_html=True)
                    st.markdown("---")


# ==================== 小红书发布独立页面 ====================
# —— 两阶段流程：阶段1 生成文案 → 阶段2 自动发布到小红书
# —— 绕过 LangGraph（Streamlit ScriptRunner 线程无 asyncio 事件循环，会报
#    "There is no current event loop in thread 'ScriptRunner.scriptThread'" 错误），
#    直接调用 text_generate_node 生成文案 + auto_publish_xiaohongshu 在独立线程中发布
elif page == "📱 小红书发布":
    # ========== 流程对齐 langgraph_more_nodes.py 图三 ==========
    # START → text_generate_node → image_generator_node → check_text_image_node
    #    → (publish_xiaohongshu) → xiaohongshu_auto_publish_node → generate_markdown_node → END
    # ===================================================================

    XHS_META = {
        "greeting": "您好, 我是小红书发布智能体",
        "description": "您可以给我一个主题,我会① 生成文案 → ② 生成配图 → ③ 检查文本/图片 → ④ 自动发布到小红书创作者平台（首次扫码登录,会保存您的信息，后续免登录）。",
    }
    st.markdown(f"""
    <div class="task-greeting">{XHS_META['greeting']}</div>
    <div class="task-intro-box">
        <p>{XHS_META['description']}</p>
    </div>
    """, unsafe_allow_html=True)

    # ========== 流程进度条 ==========
    def _xhs_stage():
        """根据 session_state 中已完成的阶段，返回当前阶段编号 (0-4)"""
        s = st.session_state
        if "xhs_markdown_output" in s:
            return 4
        if "xhs_publish_result" in s and s.get("xhs_publish_ok"):
            return 4
        if s.get("xhs_checked_ok"):
            return 3
        if "xhs_image_path_list" in s and s["xhs_image_path_list"]:
            return 2
        if "xhs_title" in s and "xhs_content" in s:
            return 1
        return 0

    stage = _xhs_stage()
    stages = ["① 输入主题", "② 生成文案", "③ 生成配图", "④ 检查完备性", "⑤ 自动发布"]
    with st.container():
        cols = st.columns(len(stages))
        for i, (name, col) in enumerate(zip(stages, cols)):
            if i < stage:
                col.markdown(f"<div style='text-align:center;color:#2E7D32;font-weight:bold'>✔ {name}</div>", unsafe_allow_html=True)
            elif i == stage:
                col.markdown(f"<div style='text-align:center;color:#1565C0;font-weight:bold;text-decoration:underline'>▶ {name}</div>", unsafe_allow_html=True)
            else:
                col.markdown(f"<div style='text-align:center;color:#9CA3AF'>○ {name}</div>", unsafe_allow_html=True)
        st.markdown("")

    # ============ 阶段0：输入区 ============
    topic = st.text_area("输入小红书内容主题", height=100, placeholder="如: 劳动合同维权、租房合同避坑...", key="xhs_topic_area")

    col_x1, col_x2 = st.columns([1, 1])
    with col_x1:
        xhs_images_uploaded = st.file_uploader("上传封面图片（可选，留空则使用AI生成的配图）", type=["png", "jpg", "jpeg", "webp"], key="xhs_upload_images", accept_multiple_files=True)
    with col_x2:
        xhs_docs = st.file_uploader("上传参考文档（可选）", type=["txt", "md", "docx", "pdf"], key="xhs_upload_docs", accept_multiple_files=True)

    # 选题示例
    with st.expander("💡 选题示例"):
        for q_item in ["租房合同避坑指南：5个关键条款必须看", "劳动合同维权：被裁员后如何争取赔偿", "投资理财陷阱：这些合同条款要警惕"]:
            st.markdown(f"- {q_item}")

    # 效果展示（一键填充 demo 文案+图片）
    if st.button("🎭 效果展示", key="toggle_demo_xhs", type="secondary", use_container_width=True):
        st.session_state["xhs_title"] = "劳动合同维权必看！被裁员了怎么赔？"
        st.session_state["xhs_content"] = """姐妹们！最近好多被裁员的私信，今天统一讲清楚👇

## 🔑 核心知识点

### 1️⃣ 经济补偿金怎么算？
- **N** = 工作年限，不满半年按0.5算，满半年按1算
- 月工资 = 离职前12个月平均工资

### 2️⃣ 哪些情况可以要求2N？
- 违法解除劳动合同、没有合法理由裁员

### 3️⃣ 维权步骤
1. 收集证据（劳动合同、工资流水、聊天记录）
2. 与公司协商 → 申请劳动仲裁 → 不服可起诉

## ⚠️ 重点提醒
- 仲裁时效1年，别过期！保留所有书面证据

---
#职场维权 #劳动仲裁 #被裁员 #劳动合同法 #法律科普"""
        demo_img = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "images", "20260813162307🔥劳动合同.png")
        if os.path.exists(demo_img):
            st.session_state["xhs_image_path_list"] = [demo_img]
        st.success("✅ Demo 数据已填充，可直接跳转到「④ 检查并发布」")
        st.rerun()

    st.markdown("---")

    # ============ 阶段1：生成文案 (text_generate_node) ============
    st.markdown("### ② 生成文案")
    col_s1_a, col_s1_b = st.columns([3, 1])
    with col_s1_a:
        do_gen_text = st.button("📱 生成文案", type="primary", use_container_width=True, key="xhs_generate")
    with col_s1_b:
        if st.button("🗑️ 重置所有结果", use_container_width=True, key="xhs_reset"):
            for key in ["xhs_title", "xhs_content", "xhs_image_path_list",
                        "xhs_checked_ok", "xhs_publish_result", "xhs_publish_ok",
                        "xhs_markdown_output", "xhs_upload_images"]:
                if key in st.session_state:
                    del st.session_state[key]
            st.rerun()

    if do_gen_text:
        input_topic = topic.strip()
        if not input_topic:
            st.warning("请输入主题内容")
        else:
            with st.spinner("✨ 正在调用 text_generate_node 生成小红书文案..."):
                try:
                    from __004__langgraph_more_nodes.nodes.text_generate_node import generate_xiaohongshu_text
                    title, content = generate_xiaohongshu_text(input_topic)
                    st.session_state["xhs_title"] = title
                    st.session_state["xhs_content"] = content
                    # 新阶段开始时清除后续阶段的缓存
                    for k in ["xhs_image_path_list", "xhs_checked_ok", "xhs_publish_result", "xhs_publish_ok", "xhs_markdown_output"]:
                        st.session_state.pop(k, None)
                    st.success("✅ 文案生成成功！进入阶段②")
                    st.rerun()
                except Exception as e:
                    print(f"小红书文案生成异常: {e}")
                    import traceback
                    traceback.print_exc()
                    # 回退演示文案
                    st.session_state["xhs_title"] = f"📱 {input_topic} - 法律科普"
                    st.session_state["xhs_content"] = f"""姐妹们！关于「{input_topic}」，今天来聊聊重点👇

## 🔑 核心知识点
（AI 生成内容回退模式）

---
#法律科普 #AI法律 #法律助手"""
                    st.warning("LLM 调用失败，已生成演示文案")

    # ========== 阶段2：渲染 + 编辑已生成的文案 ==========
    if "xhs_title" in st.session_state and "xhs_content" in st.session_state:
        gen_title = st.session_state["xhs_title"]
        gen_content = st.session_state["xhs_content"]

        with st.expander("📝 查看/编辑已生成文案", expanded=True):
            st.markdown(f"**标题**: {gen_title}")
            st.markdown(gen_content, unsafe_allow_html=True)
            col_edit1, col_edit2 = st.columns([1, 1])
            with col_edit1:
                edited_title = st.text_input("修改标题", value=gen_title, key="xhs_edit_title")
            with col_edit2:
                st.caption("修改后请保存，保存后将同步到发布内容")
            edited_content = st.text_area("修改正文", value=gen_content, height=200, key="xhs_edit_content")
            if st.button("💾 保存修改", key="xhs_save_edit", use_container_width=True):
                st.session_state["xhs_title"] = edited_title
                st.session_state["xhs_content"] = edited_content
                st.success("✅ 文案已更新")
                st.rerun()

        st.markdown("---")

        # ========== 阶段3：生成配图 (image_generator_node) ==========
        st.markdown("### ③ 生成配图")
        col_s3_a, col_s3_b, col_s3_c = st.columns([2, 1, 1])
        with col_s3_a:
            do_gen_image = st.button("🎨 AI 生成配图（image_generator_node）", type="primary", use_container_width=True, key="xhs_gen_image")
        with col_s3_b:
            if st.button("🖼️ 使用我上传的图片", use_container_width=True, key="xhs_use_uploaded"):
                # 将 st.file_uploader 的图片保存到本地临时目录，写入 session_state
                if not xhs_images_uploaded:
                    st.warning("⚠️ 请先上传至少 1 张图片")
                else:
                    try:
                        tmp_dir = os.path.join(tempfile.gettempdir(), "xhs_upload_images")
                        os.makedirs(tmp_dir, exist_ok=True)
                        imgs = xhs_images_uploaded if isinstance(xhs_images_uploaded, list) else [xhs_images_uploaded]
                        saved_paths = []
                        for i, img in enumerate(imgs):
                            ext = os.path.splitext(img.name)[1] or ".png"
                            fp = os.path.join(tmp_dir, f"xhs_{int(time.time())}_{i}{ext}")
                            with open(fp, "wb") as f:
                                f.write(img.getvalue())
                            saved_paths.append(fp)
                        st.session_state["xhs_image_path_list"] = saved_paths
                        for k in ["xhs_checked_ok", "xhs_publish_result", "xhs_publish_ok", "xhs_markdown_output"]:
                            st.session_state.pop(k, None)
                        st.success(f"✅ 已使用上传的 {len(saved_paths)} 张图片")
                        st.rerun()
                    except Exception as e:
                        st.error(f"图片保存失败: {e}")
        with col_s3_c:
            if "xhs_image_path_list" in st.session_state and st.session_state["xhs_image_path_list"]:
                if st.button("🗑️ 清除配图", use_container_width=True, key="xhs_clr_img"):
                    st.session_state.pop("xhs_image_path_list", None)
                    for k in ["xhs_checked_ok", "xhs_publish_result", "xhs_publish_ok", "xhs_markdown_output"]:
                        st.session_state.pop(k, None)
                    st.rerun()

        if do_gen_image:
            with st.spinner("🎨 正在调用 image_generator_node 生成配图（即梦AI / 占位图兜底）..."):
                try:
                    # 对齐节点状态字段名: xiaohongshu_title / xiaohongshu_content
                    from __004__langgraph_more_nodes.nodes.image_generate_node import image_generator_node
                    _state = {
                        "xiaohongshu_title": st.session_state.get("xhs_title", ""),
                        "xiaohongshu_content": st.session_state.get("xhs_content", ""),
                    }
                    _result = image_generator_node(_state)
                    img_list = _result.get("xiaohongshu_image_path_list", [])
                    if img_list:
                        st.session_state["xhs_image_path_list"] = img_list
                        for k in ["xhs_checked_ok", "xhs_publish_result", "xhs_publish_ok", "xhs_markdown_output"]:
                            st.session_state.pop(k, None)
                        st.success(f"✅ 配图生成成功: {img_list[0]}")
                        st.rerun()
                    else:
                        st.error("❌ 配图生成失败（即梦AI + 占位图兜底均失败）")
                except Exception as e:
                    print(f"配图生成异常: {e}")
                    import traceback
                    traceback.print_exc()
                    st.error(f"❌ 配图生成失败: {e}")

        # 展示已有的配图
        if "xhs_image_path_list" in st.session_state and st.session_state["xhs_image_path_list"]:
            with st.expander("🖼️ 当前配图预览", expanded=True):
                paths = st.session_state["xhs_image_path_list"]
                preview_cols = st.columns(min(3, len(paths)))
                for i, p in enumerate(paths):
                    if os.path.exists(p):
                        with preview_cols[i % 3]:
                            st.image(p, caption=os.path.basename(p), use_column_width=True)
                    else:
                        preview_cols[i % 3].warning(f"⚠️ 图片不存在: {p}")

        st.markdown("---")

        # ========== 阶段4：检查文本/图片 (check_text_image_node) ==========
        # 然后进入自动发布 (xiaohongshu_auto_publish_node)
        # 和 Markdown 生成 (generate_markdown_node)
        st.markdown("### ④⑤ 检查完备性 & 自动发布")

        _cur_title = st.session_state.get("xhs_title", "")
        _cur_content = st.session_state.get("xhs_content", "")
        _cur_imgs = st.session_state.get("xhs_image_path_list", [])

        # 先显示检查结果（对齐 check_text_image_node 逻辑）
        with st.container(border=True):
            st.caption("【对齐 check_text_image_node 规则】")
            checks = [
                ("标题非空", bool(_cur_title)),
                ("正文非空", bool(_cur_content)),
                ("图片已生成或上传", bool(_cur_imgs)),
            ]
            all_ok = True
            for name, ok in checks:
                icon, color = ("✅", "#2E7D32") if ok else ("❌", "#C62828")
                if not ok:
                    all_ok = False
                st.markdown(f"<div style='color:{color};font-weight:bold'>{icon} {name}</div>", unsafe_allow_html=True)
            st.session_state["xhs_checked_ok"] = all_ok

        if st.button("🚀 检查通过，一键自动发布到小红书",
                     type="primary", use_container_width=True, key="xhs_auto_publish",
                     disabled=not all_ok):
            # ========== xiaohongshu_auto_publish_node ==========
            # 参考 auto_publish_xiaohongshu_node.py，通过 subprocess 隔离 Streamlit
            # 运行时，避免 ScriptRunner 线程事件循环与 Playwright 子进程冲突
            publish_status = st.empty()
            publish_status.info("⏳ 正在启动独立发布进程（将打开浏览器窗口）...")

            # 构造最终图片列表 (优先 xhs_image_path_list，其次上传文件)
            final_image_paths = list(_cur_imgs) if _cur_imgs else []
            if not final_image_paths and xhs_images_uploaded:
                tmp_dir = os.path.join(tempfile.gettempdir(), "xhs_upload_images")
                os.makedirs(tmp_dir, exist_ok=True)
                imgs = xhs_images_uploaded if isinstance(xhs_images_uploaded, list) else [xhs_images_uploaded]
                for i, img in enumerate(imgs):
                    ext = os.path.splitext(img.name)[1] or ".png"
                    fp = os.path.join(tmp_dir, f"xhs_{int(time.time())}_{i}{ext}")
                    with open(fp, "wb") as f:
                        f.write(img.getvalue())
                    final_image_paths.append(fp)

            # 再次做最终校验
            missing = []
            if not _cur_title:
                missing.append("标题")
            if not _cur_content:
                missing.append("正文")
            if not final_image_paths:
                missing.append("图片")
            if missing:
                publish_status.error(f"❌ 缺少: {', '.join(missing)}，无法发布")
                st.stop()

            # 定位 runner
            runner_path = Path(__file__).parent / "xhs_publish_runner.py"
            if not runner_path.exists():
                publish_status.error(f"❌ 找不到发布脚本: {runner_path}")
                st.stop()

            # 项目根目录（__006__streamlit 上一级），供 PYTHONPATH 和探测函数使用
            _project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

            # ── 修复：多解释器问题（No module named 'playwright'） ──
            # 启动前用候选解释器做一次探测：执行 import playwright，如果失败就换下一个
            # 候选顺序：
            #   1) sys.executable（当前 Streamlit 解释器，理论上能 import）
            #   2) sys.prefix / "python.exe"（conda 虚拟环境根）
            #   3) sys.prefix / "Scripts" / "python.exe"（venv Scripts）
            #   4) 兜底：os.environ.get("VIRTUAL_ENV")/Scripts/python.exe
            def _probe_playwright(py_path: str, timeout_sec: int = 15) -> bool:
                """探测某 Python 能否 import playwright"""
                if not py_path or not os.path.exists(py_path):
                    return False
                try:
                    _probe_env = os.environ.copy()
                    _probe_env["PYTHONIOENCODING"] = "utf-8"
                    _probe_env["PYTHONUTF8"] = "1"
                    _probe_env["PYTHONPATH"] = _project_root + os.pathsep + _probe_env.get("PYTHONPATH", "")
                    pr = subprocess.run(
                        [py_path, "-c", "import playwright; print('PLAYWRIGHT_OK:', playwright.__file__)"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace",
                        timeout=timeout_sec, env=_probe_env,
                    )
                    return pr.returncode == 0 and "PLAYWRIGHT_OK" in (pr.stdout or "")
                except Exception:
                    return False

            publish_status.info("🔍 正在自检 Python 解释器 Playwright 可用性...")
            _candidates = []
            # 1) 当前解释器
            _candidates.append(("sys.executable", sys.executable))
            # 2) conda 根 python.exe
            _p_prefix = os.path.join(sys.prefix, "python.exe")
            if _p_prefix != sys.executable and os.path.exists(_p_prefix):
                _candidates.append(("sys.prefix", _p_prefix))
            # 3) venv Scripts/python.exe
            _p_scripts = os.path.join(sys.prefix, "Scripts", "python.exe")
            if os.path.exists(_p_scripts) and _p_scripts != sys.executable:
                _candidates.append(("sys.prefix/Scripts", _p_scripts))
            # 4) VIRTUAL_ENV
            if os.environ.get("VIRTUAL_ENV"):
                _ve = os.environ["VIRTUAL_ENV"]
                for rel in ("python.exe", os.path.join("Scripts", "python.exe")):
                    _p_ve = os.path.join(_ve, rel)
                    if os.path.exists(_p_ve) and _p_ve != sys.executable:
                        _candidates.append((f"VIRTUAL_ENV/{rel}", _p_ve))

            chosen_py = None
            probe_logs = []
            for name, path in _candidates:
                ok = _probe_playwright(path)
                probe_logs.append(f"  [{('✔' if ok else '✗')}] {name}: {path}")
                if ok:
                    chosen_py = path
                    break
            probe_logs_text = "\n".join(probe_logs)

            if not chosen_py:
                _install_cmd = (
                    f'"{sys.executable}" -m pip install playwright playwright-async '
                    f'&& "{sys.executable}" -m playwright install chromium'
                )
                publish_status.error(
                    "❌ 当前 Python 环境缺少 Playwright。\n\n"
                    f"请先执行安装命令：\n\n```\n{_install_cmd}\n```\n\n"
                    f"自检日志：\n{probe_logs_text}"
                )
                with st.expander("📋 解释器自检详情"):
                    st.code(probe_logs_text)
                st.stop()
            else:
                publish_status.info(f"✅ Playwright 可用，使用解释器：{chosen_py}")

            images_arg = ",".join(final_image_paths)
            cmd = [
                chosen_py,  # 经探测能 import playwright 的解释器
                str(runner_path),
                "--images", images_arg,
                "--title", _cur_title,
                "--content", _cur_content,
                "--timeout", "300",
            ]

            # 环境变量：PYTHONIOENCODING=utf-8 解决 GBK 报错；PYTHONPATH 保证包可见
            _env = os.environ.copy()
            _env["PYTHONIOENCODING"] = "utf-8"
            _env["PYTHONUTF8"] = "1"
            _env["PYTHONPATH"] = _project_root + os.pathsep + _env.get("PYTHONPATH", "")

            # 启动子进程：CREATE_NEW_CONSOLE（独立窗口） + UTF-8 stdout
            CREATE_NEW_CONSOLE = 0x00000010
            try:
                proc = subprocess.Popen(
                    cmd,
                    creationflags=CREATE_NEW_CONSOLE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=str(Path(__file__).parent),
                    env=_env,
                )
            except TypeError:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=str(Path(__file__).parent),
                    env=_env,
                )
            except Exception as e2:
                publish_status.error(f"❌ 无法启动发布进程: {e2}")
                st.stop()

            # 实时日志
            log_lines = []
            log_placeholder = st.empty()
            status_text = "⏳ 发布中..."
            publish_status.info(status_text)
            try:
                while True:
                    line = proc.stdout.readline()
                    if not line:
                        break
                    ls = line.strip()
                    if ls:
                        log_lines.append(ls)
                        with log_placeholder.container():
                            st.code("\n".join(log_lines[-15:]), language=None)
                        # 进度匹配（对齐 XiaohongshuUploader 的日志前缀：[√]/[x]/[X]/[!]）
                        if "图片上传完成" in ls or "已提交" in ls and "图片" in ls:
                            status_text = "🖼️  图片已提交，等待上传完成..."
                            publish_status.info(status_text)
                        elif "缩略图" in ls and "上传完成" in ls:
                            status_text = "🖼️  图片上传完成，正在填写内容..."
                            publish_status.info(status_text)
                        elif "标题已填写" in ls:
                            status_text = "📝 标题已填写，正在输入正文..."
                            publish_status.info(status_text)
                        elif "正文已填写" in ls:
                            status_text = "📝 内容填写完成，准备点击发布..."
                            publish_status.info(status_text)
                        elif "策略" in ls and "点击发布按钮" in ls:
                            status_text = f"🚀 {ls}"
                            publish_status.info(status_text)
                        elif "等待发布结果" in ls:
                            status_text = "⏳ 已点击发布，正在验证发布结果..."
                            publish_status.info(status_text)
                        elif "发布成功" in ls and ("URL" in ls or "提示" in ls or "✅" in ls):
                            status_text = "✅ 发布成功！"
                            publish_status.success(status_text)
                        elif "[DONE] ✅" in ls or "发布流程执行成功" in ls:
                            status_text = "✅ 发布流程成功完成！"
                            publish_status.success(status_text)
                        elif "[DONE] ❌" in ls or "发布流程执行失败" in ls:
                            status_text = "❌ 发布失败，请查看日志"
                            publish_status.error(status_text)
                        elif "[FAIL]" in ls:
                            status_text = f"❌ {ls}"
                            publish_status.error(status_text)
                        elif "检测到错误提示" in ls:
                            status_text = f"❌ {ls}"
                            publish_status.error(status_text)
                        elif "所有策略均未能点击发布按钮" in ls:
                            status_text = "❌ 无法点击发布按钮，请手动点击"
                            publish_status.error(status_text)
                        elif "未检测到登录状态" in ls or "请手动登录" in ls:
                            status_text = "⚠️ 首次使用：请在独立命令行窗口中登录后按回车继续..."
                            publish_status.warning(status_text)
                        elif "登录状态已保存" in ls:
                            status_text = "✅ 登录成功并保存 Cookie，继续发布..."
                            publish_status.info(status_text)
                        elif "开始启动" in ls or "启动完成" in ls:
                            status_text = "🌐 Chromium 浏览器已启动，正在加载小红书发布页..."
                            publish_status.info(status_text)
                        elif "切换到" in ls and "上传图文" in ls:
                            status_text = "🔀 正在切换到上传图文Tab..."
                            publish_status.info(status_text)

                rc = proc.wait(timeout=300)
                # 判断成功：退出码0 + 日志中有 ✅ 或 发布成功
                has_success = any("✅" in l or "发布成功" in l for l in log_lines)
                has_fail = any("[FAIL]" in l or "[DONE] ❌" in l or "所有策略均未能" in l or "检测到错误提示" in l for l in log_lines)
                if rc == 0 and has_success and not has_fail:
                    publish_status.success("✅ 自动发布成功！请检查小红书后台。")
                    st.session_state["xhs_publish_result"] = "发布成功"
                    st.session_state["xhs_publish_ok"] = True

                    # ========== generate_markdown_node ==========
                    try:
                        from __004__langgraph_more_nodes.nodes.generate_markdown_node import generate_markdown_node
                        _md_state = {
                            "xiaohongshu_tcm_post_title": _cur_title,
                            "xiaohongshu_tcm_post_content": _cur_content,
                            "xiaohongshu_image_path_list": final_image_paths,
                            "xiaohongshu_tcm_tip": "小红书发布成功",
                        }
                        _md_res = generate_markdown_node(_md_state)
                        if _md_res and _md_res.get("xiaohongshu_markdown_output"):
                            st.session_state["xhs_markdown_output"] = _md_res["xiaohongshu_markdown_output"]
                    except Exception as md_e:
                        print(f"[WARN] generate_markdown_node 失败: {md_e}")
                        # 兜底：手写 markdown
                        _md = f"# {_cur_title}\n\n{_cur_content}\n\n配图:\n" + "\n".join(
                            f"- ![{os.path.basename(p)}]({p})" for p in final_image_paths
                        )
                        st.session_state["xhs_markdown_output"] = _md
                else:
                    publish_status.error(f"❌ 发布失败（退出码 {rc}），请查看上方完整日志。")
                    st.session_state["xhs_publish_result"] = f"发布失败 (退出码 {rc})"
                    st.session_state["xhs_publish_ok"] = False
            except subprocess.TimeoutExpired:
                proc.kill()
                publish_status.warning("⏳ 发布超时（超过5分钟），已终止进程。请手动检查浏览器状态。")
                st.session_state["xhs_publish_result"] = "发布超时"
            except Exception as e:
                publish_status.error(f"❌ 读取发布日志异常: {e}")
                st.session_state["xhs_publish_result"] = f"读取日志异常: {e}"

            # 完整日志折叠面板
            if log_lines:
                with st.expander("📋 查看完整发布日志"):
                    st.code("\n".join(log_lines), language=None)

        # ========== 结果显示：Markdown 报告（generate_markdown_node） ==========
        if "xhs_markdown_output" in st.session_state and st.session_state["xhs_markdown_output"]:
            st.markdown("---")
            st.markdown("### 📄 发布报告 (Markdown)")
            st.markdown(st.session_state["xhs_markdown_output"], unsafe_allow_html=True)

        # 历史发布结果
        if "xhs_publish_result" in st.session_state:
            st.markdown("---")
            st.markdown(f"**📋 上次发布结果**: {st.session_state['xhs_publish_result']}")
