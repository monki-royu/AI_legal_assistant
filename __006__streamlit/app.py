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
import json  # JSON 序列化/反序列化（本文件中虽未直接使用，但保留以备扩展）
import time  # 时间相关函数，用于流式输出的 sleep 模拟打字延迟
import asyncio  # 异步事件循环库，用于在同步上下文中驱动后端 async 流式接口

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
    /* ===== 全局变量 - 红底蓝配 ===== */
    /* :root 选择器定义全局 CSS 变量，所有后续样式可通过 var(--xxx) 引用，便于统一调色 */
    :root {
        /* 大面积红色背景系 —— 主色调，营造庄重法律氛围 */
        --red-bg-1: #450a0a;      /* 深红 1 —— 顶部最深处背景 */
        --red-bg-2: #7f1d1d;      /* 深红 2 —— 中段过渡色 */
        --red-bg-3: #991b1b;      /* 中红 3 —— 下段背景 */
        --red-bg-4: #b91c1c;      /* 亮红 4 —— 高亮强调色 */
        --red-accent: #dc2626;    /* 鲜红 —— 危险/重点强调用 */
        
        /* 科技感深蓝色系 —— 用于高亮、按钮、链接、选中态，与红色形成强对比 */
        --blue-deep: #0a1929;     /* 极深蓝 —— 深色卡片底色 */
        --blue-mid: #0D47A1;      /* 科技深蓝 —— 按钮渐变起始色 */
        --blue-bright: #1976D2;   /* 科技蓝 —— 主交互色，按钮/选中态主色 */
        --blue-soft: #42A5F5;     /* 科技浅蓝 —— 文字高亮、链接悬停色 */
        --blue-glow: rgba(25, 118, 210, 0.45);  /* 蓝色辉光半透明色，用于 box-shadow 光晕 */
        
        /* 多色卡片边框 (参考图二) —— 5 张快捷卡片各用一种主题色 */
        --card-blue: #38bdf8;     /* 天蓝色卡片 */
        --card-orange: #fb923c;   /* 橙色卡片 */
        --card-green: #4ade80;    /* 绿色卡片 */
        --card-purple: #a78bfa;   /* 紫色卡片 */
        --card-pink: #f472b6;     /* 粉色卡片 */
        --card-amber: #fbbf24;    /* 琥珀色卡片 */
        
        /* 文本 (高对比度, 确保清晰可读) —— 在深红背景上需使用浅色文字 */
        --text-white: #ffffff;    /* 主标题/强调文本纯白 */
        --text-light: #fef2f2;    /* 近白 —— 副标题 */
        --text-soft: #fecaca;     /* 柔红 —— 正文文字（浅红色，与红底和谐） */
        --text-muted: #fca5a5;    /* 浅红 —— 次要/灰色文字 */
        --text-dark: #1f2937;     /* 深灰 (用于白底组件内部文字，如输入框) */
    }

    /* ===== 主背景: 大面积红色 (从上到下渐变) ===== */
    /* .stApp 是 Streamlit 应用最外层容器 */
    .stApp {
        background: linear-gradient(180deg, #450a0a 0%, #7f1d1d 40%, #991b1b 100%);  /* 180deg 自上而下三色渐变红 */
        min-height: 100vh;  /* 最小高度占满视口，确保短内容时背景也铺满 */
    }
    /* 主内容容器：与 stApp 配合再叠一层渐变，并加上顶部内边距 */
    [data-testid="stAppViewContainer"] > .main {
        background: linear-gradient(180deg, #450a0a 0%, #7f1d1d 100%);  /* 主内容区双色渐变 */
        padding-top: 2rem;  /* 顶部留白，避免内容贴顶 */
    }

    /* ===== 顶部导航栏 ===== */
    /* stHeader 是 Streamlit 顶部固定栏 */
    [data-testid="stHeader"] {
        background: linear-gradient(90deg, #450a0a 0%, #7f1d1d 50%, #450a0a 100%);  /* 90deg 横向三段渐变，中间亮两端深 */
        border-bottom: 2px solid rgba(37, 99, 235, 0.3);  /* 底部蓝色分隔线，与主色调呼应 */
        backdrop-filter: blur(10px);  /* 毛玻璃效果，滚动时背景模糊 */
    }

    /* ===== 侧边栏 (左侧导航: 深红渐变与主页面和谐) ===== */
    /* !important 强制覆盖 Streamlit 默认浅色背景 */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #3b0808 0%, #5b1212 50%, #7f1d1d 100%) !important;  /* 比主页面更深的红渐变 */
        border-right: 2px solid rgba(37, 99, 235, 0.35);  /* 右侧蓝色分隔线 */
    }
    /* 侧边栏内 Markdown 文字颜色统一为柔红色 */
    [data-testid="stSidebar"] .stMarkdown {
        color: #fecaca !important;
    }
    /* 侧边栏各级标题：白色 + 文字阴影，确保在深红背景上清晰可读 */
    [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 {
        color: #ffffff !important;
        text-shadow: 0 1px 4px rgba(0,0,0,0.5);  /* 半透明黑色阴影增强可读性 */
    }
    /* 侧边栏内的 label / p / span 等通用文字颜色 */
    section[data-testid="stSidebar"] label,
    section[data-testid="stSidebar"] p,
    section[data-testid="stSidebar"] span {
        color: #fecaca !important;
    }
    /* 侧边栏 logo 文字 —— 通过属性选择器精准定位内联 style 设置深色的元素并强制改白 */
    [data-testid="stSidebar"] .stMarkdown div[style*="color:#1f2937"] {
        color: #ffffff !important;
    }
    /* 侧边栏 radio: 选中态蓝色背景, 未选中浅色文字 */
    /* radio 是侧边栏主导航组件，这里将其选项 label 改造成卡片式按钮 */
    [data-testid="stSidebar"] [data-testid="stRadio"] label {
        background: transparent !important;  /* 默认透明背景 */
        color: #fecaca !important;  /* 未选中文字柔红色 */
        padding: 10px 14px;  /* 内边距，让点击区域更大更易点 */
        border-radius: 10px;  /* 圆角，符合现代 UI 风格 */
        margin-bottom: 6px;  /* 选项之间间距 */
        transition: all 0.2s;  /* 0.2s 过渡动画，让 hover/选中切换更顺滑 */
        border: 1px solid transparent;  /* 默认无可见边框，hover 时显示 */
    }
    /* radio 选项悬停态：浅蓝背景 + 浅蓝边框 */
    [data-testid="stSidebar"] [data-testid="stRadio"] label:hover {
        background: rgba(25, 118, 210, 0.18) !important;
        border: 1px solid rgba(25, 118, 210, 0.3);
    }
    /* radio 选中态：使用 :has(input:checked) CSS4 伪类精准定位被选中的 label */
    [data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked) {
        background: linear-gradient(90deg, var(--blue-mid), var(--blue-bright)) !important;  /* 蓝色渐变背景 */
        color: white !important;  /* 选中时文字变白 */
        box-shadow: 0 4px 14px rgba(59,130,246,0.35);  /* 蓝色辉光阴影 */
    }
    /* 选中态下内部子标签文字也强制白色 */
    [data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked) .stRadioLabel {
        color: white !important;
    }
    /* 侧边栏 toggle / checkbox 文字颜色 */
    [data-testid="stSidebar"] [data-testid="stCheckbox"] label,
    [data-testid="stSidebar"] [data-testid="stToggle"] label {
        color: #fecaca !important;
    }
    /* 侧边栏 divider 分隔线颜色 */
    [data-testid="stSidebar"] hr,
    [data-testid="stSidebar"] [data-testid="stMarkdown"] hr {
        border-color: rgba(254, 202, 202, 0.2) !important;
    }
    /* 侧边栏 section headings —— h3 标题样式增强 */
    [data-testid="stSidebar"] [data-testid="stMarkdown"] h3,
    [data-testid="stSidebar"] .stMarkdown h3 {
        color: #ffffff !important;
        text-shadow: 0 1px 3px rgba(0,0,0,0.5);
        border-bottom: 1px solid rgba(59,130,246,0.25);  /* 标题底部蓝色细线 */
        padding-bottom: 6px;
        margin-top: 14px !important;
    }
    /* 侧边栏 footer 小字颜色更淡 */
    [data-testid="stSidebar"] .stMarkdown small,
    [data-testid="stSidebar"] small {
        color: rgba(254, 202, 202, 0.7) !important;
    }

    /* ===== 全局文本颜色 (高对比度, 清晰可读) ===== */
    /* 所有 Markdown 文字默认白色 */
    .stMarkdown {
        color: var(--text-white) !important;
    }
    /* Markdown 各级标题：白色 + 粗体 + 文字阴影 */
    .stMarkdown h1, .stMarkdown h2, .stMarkdown h3, .stMarkdown h4 {
        color: #ffffff !important;
        font-weight: 800;
        text-shadow: 0 1px 4px rgba(0,0,0,0.4);
    }
    /* Markdown 段落与列表项：柔红色 + 14px + 1.7 行高，保证可读性 */
    .stMarkdown p, .stMarkdown li {
        color: #fecaca !important;
        font-size: 14px;
        line-height: 1.7;
    }
    /* small / .small 类：浅红色更淡的辅助文字 */
    .stMarkdown small, .stMarkdown .small {
        color: #fca5a5 !important;
    }

    /* ===== 任务介绍/问候区 (带背景框, 确保文字清晰) ===== */
    /* .task-greeting 是任务页顶部大字问候语，通过 st.markdown 自定义 HTML 渲染 */
    .task-greeting {
        font-size: 36px;  /* 大字号醒目 */
        font-weight: 900;  /* 最粗字重 */
        color: #ffffff;
        letter-spacing: 1.5px;  /* 字间距加宽，增强气势 */
        text-align: center;  /* 居中对齐 */
        margin-bottom: 8px;
        line-height: 1.3;
        text-shadow: 0 2px 8px rgba(0,0,0,0.5);  /* 强阴影增强可读性 */
    }
    /* .accent 是问候语中"法智"等关键词的渐变文字效果 */
    .task-greeting .accent {
        background: linear-gradient(135deg, #42A5F5, #1976D2);  /* 135deg 蓝色渐变 */
        -webkit-background-clip: text;  /* 背景裁剪到文字（Webkit 内核） */
        -webkit-text-fill-color: transparent;  /* 文字填充透明，露出渐变背景 */
        background-clip: text;  /* 标准属性，兼容现代浏览器 */
    }
    /* .task-intro-box 是问候语下方的任务说明卡片 */
    .task-intro-box {
        max-width: 780px;  /* 限制最大宽度，避免长行难读 */
        margin: 0 auto 20px;  /* 水平居中 + 底部间距 */
        padding: 16px 22px;
        background: rgba(0, 0, 0, 0.35);  /* 半透明黑底，与红背景叠加产生深色卡片效果 */
        border: 1px solid rgba(25, 118, 210, 0.25);  /* 蓝色细边框 */
        border-radius: 14px;  /* 圆角 */
        border-left: 4px solid #1976D2;  /* 左侧蓝色粗边，作为视觉强调条 */
        backdrop-filter: blur(6px);  /* 毛玻璃效果 */
    }
    /* 任务说明卡片内的段落与 span 文字样式 */
    .task-intro-box p, .task-intro-box span {
        color: #fecaca !important;
        font-size: 14px;
        line-height: 1.8;
        text-shadow: 0 1px 2px rgba(0,0,0,0.3);
    }
    /* .task-upload-row 是任务页文件上传按钮行的容器 */
    .task-upload-row {
        max-width: 780px;
        margin: 0 auto 16px;
        display: flex;  /* 弹性布局，水平排列按钮 */
        gap: 12px;  /* 按钮之间间距 */
        flex-wrap: wrap;  /* 自动换行，适配窄屏 */
    }
    /* .task-upload-btn 是上传按钮的视觉样式（实际点击由 Streamlit file_uploader 承担） */
    .task-upload-btn {
        padding: 10px 18px;
        border-radius: 10px;
        background: rgba(25, 118, 210, 0.15);  /* 半透明蓝底 */
        border: 1px solid rgba(25, 118, 210, 0.4);
        color: #93c5fd;  /* 浅蓝文字 */
        font-size: 13px;
        font-weight: 600;
        cursor: pointer;  /* 鼠标变手型，提示可点击 */
        transition: all 0.2s;
    }
    /* 上传按钮悬停态：背景加深 + 文字变白 */
    .task-upload-btn:hover {
        background: rgba(25, 118, 210, 0.28);
        color: #ffffff;
    }

    /* ===== Hero 标题区 (参考图四: "你好, 我是法智") ===== */
    /* .hero-container 是首页 Hero 区最外层容器，居中并限制宽度 */
    .hero-container {
        max-width: 900px;
        margin: 0 auto;
        padding: 40px 20px 20px;
        text-align: center;
    }
    /* .hero-title 是首页最大标题文字 */
    .hero-title {
        font-size: 48px;
        font-weight: 900;
        color: var(--text-white);
        letter-spacing: 2px;  /* 字间距更宽，气势更强 */
        margin-bottom: 8px;
        line-height: 1.2;
    }
    /* Hero 标题中关键词的渐变文字效果（与 .task-greeting .accent 同款） */
    .hero-title .accent {
        background: linear-gradient(135deg, var(--blue-soft), var(--blue-bright));
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
    }
    /* .hero-subtitle 是 Hero 标题下方的副标题/描述文字 */
    .hero-subtitle {
        font-size: 16px;
        color: #fecaca !important;
        margin-bottom: 32px;
        letter-spacing: 1px;
        text-shadow: 0 1px 4px rgba(0,0,0,0.4);
        font-weight: 500;
    }
    /* 副标题内部所有子元素统一柔红色 */
    .hero-subtitle p, .hero-subtitle span, .hero-subtitle div {
        color: #fecaca !important;
    }

    /* ===== 中央大输入框 (参考图四) ===== */
    /* .main-input-card 是输入框外层卡片，呈现类 DeepSeek 大边框效果 */
    .main-input-card {
        max-width: 820px;
        margin: 0 auto;
        background: rgba(255, 255, 255, 0.04);  /* 极淡白底，几乎透明 */
        border: 2px solid rgba(25, 118, 210, 0.5);  /* 蓝色 2px 边框 */
        border-radius: 18px;  /* 大圆角 */
        padding: 6px;
        box-shadow: 0 0 0 1px rgba(25, 118, 210, 0.2), 0 20px 60px rgba(0, 0, 0, 0.4);  /* 双层阴影：内层蓝色细圈 + 外层深色投影 */
        transition: all 0.3s;  /* 0.3s 过渡，hover 时平滑变化 */
    }
    /* 卡片悬停态：边框变亮蓝 + 阴影更深 */
    .main-input-card:hover {
        border-color: var(--blue-bright);
        box-shadow: 0 0 0 1px var(--blue-glow), 0 24px 72px rgba(0, 0, 0, 0.5);
    }
    /* 输入框区域去除默认外边距，紧贴卡片内边距 */
    .main-input-card .stTextArea {
        margin: 0 !important;
    }
    /* 移除 Streamlit textarea 默认背景与边框，让其融入卡片 */
    .main-input-card .stTextArea > div {
        background: transparent !important;
        border: none !important;
    }
    .main-input-card .stTextArea > div > div {
        background: transparent !important;
        border: none !important;
    }
    /* 实际 textarea 元素：透明背景 + 白字 + 大内边距 + 最小高度 */
    .main-input-card .stTextArea > div > div > textarea {
        background: transparent !important;
        color: var(--text-white) !important;
        border: none !important;
        font-size: 15px !important;
        padding: 18px 20px !important;
        min-height: 90px !important;
        line-height: 1.6 !important;
    }
    /* textarea 的占位符文字颜色（半透明柔红） */
    .main-input-card .stTextArea > div > div > textarea::placeholder {
        color: rgba(254, 202, 202, 0.5) !important;
    }
    /* .main-input-row 是输入框下方的操作行（左侧 chip 按钮 + 右侧发送按钮） */
    .main-input-row {
        display: flex;
        align-items: center;
        justify-content: space-between;  /* 两端对齐 */
        padding: 0 16px 12px;
        gap: 12px;
    }
    /* 左侧按钮组容器 */
    .main-input-left {
        display: flex;
        gap: 8px;
        align-items: center;
    }
    /* 右侧按钮组容器 */
    .main-input-right {
        display: flex;
        gap: 8px;
        align-items: center;
    }
    /* .send-btn 是圆形发送按钮，蓝色渐变 + 辉光阴影 */
    .send-btn {
        width: 40px;
        height: 40px;
        border-radius: 50%;  /* 圆形 */
        background: linear-gradient(135deg, var(--blue-mid), var(--blue-bright));
        color: white;
        border: none;
        cursor: pointer;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        font-size: 18px;
        transition: all 0.2s;
        box-shadow: 0 4px 14px var(--blue-glow);  /* 蓝色辉光 */
    }
    /* 发送按钮悬停态：上移 2px + 阴影扩大 */
    .send-btn:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 20px var(--blue-glow);
    }
    /* .chip-btn 是输入框下方的圆角胶囊按钮（如附件、思考开关等） */
    .chip-btn {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 8px 14px;
        border-radius: 20px;  /* 胶囊形 */
        background: rgba(25, 118, 210, 0.12);
        border: 1px solid rgba(25, 118, 210, 0.35);
        color: var(--blue-soft);
        font-size: 13px;
        cursor: pointer;
        transition: all 0.2s;
    }
    /* chip 按钮悬停态 */
    .chip-btn:hover {
        background: rgba(25, 118, 210, 0.22);
        border-color: var(--blue-bright);
    }
    /* chip 按钮激活态：蓝色渐变填充 + 白字 */
    .chip-btn.active {
        background: linear-gradient(135deg, var(--blue-mid), var(--blue-bright));
        color: white;
        border-color: transparent;
    }
    /* .icon-btn 是方形图标按钮（如附件、麦克风等） */
    .icon-btn {
        width: 36px;
        height: 36px;
        border-radius: 10px;
        background: rgba(254, 242, 242, 0.08);
        border: 1px solid rgba(254, 242, 242, 0.15);
        color: var(--text-soft);
        cursor: pointer;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        font-size: 16px;
        transition: all 0.2s;
    }
    /* 图标按钮悬停态 */
    .icon-btn:hover {
        background: rgba(254, 242, 242, 0.15);
        color: var(--text-white);
    }

    /* ===== 多色快捷卡片 (参考图二) ===== */
    /* .quick-cards 是快捷卡片网格容器，自适应列数 */
    .quick-cards {
        max-width: 820px;
        margin: 28px auto 0;
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));  /* CSS Grid 自适应布局，每列最小 240px */
        gap: 16px;
    }
    /* .quick-card 是单张快捷卡片本体 */
    .quick-card {
        padding: 18px 20px;
        border-radius: 14px;
        background: rgba(0, 0, 0, 0.3);  /* 半透明黑底 */
        backdrop-filter: blur(8px);  /* 毛玻璃 */
        cursor: pointer;
        transition: all 0.25s;
        position: relative;  /* 相对定位，配合 ::before 伪元素绘制顶部彩条 */
        overflow: hidden;
    }
    /* ::before 伪元素：卡片顶部 3px 彩色装饰条，颜色由具体颜色类决定 */
    .quick-card::before {
        content: '';
        position: absolute;
        top: 0; left: 0; right: 0;
        height: 3px;
    }
    /* 卡片悬停态：上移 3px + 背景加深 */
    .quick-card:hover {
        transform: translateY(-3px);
        background: rgba(0, 0, 0, 0.4);
    }
    /* 6 种颜色变体：分别设置边框颜色与顶部彩条渐变 */
    .quick-card.blue { border: 1px solid rgba(56, 189, 248, 0.35); }
    .quick-card.blue::before { background: linear-gradient(90deg, var(--card-blue), var(--blue-bright)); }
    .quick-card.orange { border: 1px solid rgba(251, 146, 60, 0.35); }
    .quick-card.orange::before { background: linear-gradient(90deg, var(--card-orange), #f59e0b); }
    .quick-card.green { border: 1px solid rgba(74, 222, 128, 0.35); }
    .quick-card.green::before { background: linear-gradient(90deg, var(--card-green), #10b981); }
    .quick-card.purple { border: 1px solid rgba(167, 139, 250, 0.35); }
    .quick-card.purple::before { background: linear-gradient(90deg, var(--card-purple), #8b5cf6); }
    .quick-card.pink { border: 1px solid rgba(244, 114, 182, 0.35); }
    .quick-card.pink::before { background: linear-gradient(90deg, var(--card-pink), #ec4899); }
    .quick-card.amber { border: 1px solid rgba(251, 191, 36, 0.35); }
    .quick-card.amber::before { background: linear-gradient(90deg, var(--card-amber), #d97706); }

    /* .quick-card-tag 是卡片顶部小标签（如 OVERVIEW / AGENT 01） */
    .quick-card-tag {
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 2px;  /* 字母间距大，营造科技感 */
        margin-bottom: 8px;
    }
    /* 各颜色变体的小标签文字颜色 */
    .quick-card.blue .quick-card-tag { color: var(--card-blue); }
    .quick-card.orange .quick-card-tag { color: var(--card-orange); }
    .quick-card.green .quick-card-tag { color: var(--card-green); }
    .quick-card.purple .quick-card-tag { color: var(--card-purple); }
    .quick-card.pink .quick-card-tag { color: var(--card-pink); }
    .quick-card.amber .quick-card-tag { color: var(--card-amber); }

    /* .quick-card-title 是卡片主标题 */
    .quick-card-title {
        font-size: 17px;
        font-weight: 700;
        color: var(--text-white);
        margin-bottom: 8px;
        display: flex;
        align-items: center;
        gap: 8px;
    }
    /* .quick-card-desc 是卡片描述文字 */
    .quick-card-desc {
        font-size: 13px;
        color: var(--text-muted);
        line-height: 1.55;
    }

    /* ===== 底部说明文字 (参考图三) ===== */
    /* .footer-desc 是页面底部说明区，居中限宽 */
    .footer-desc {
        max-width: 900px;
        margin: 40px auto 20px;
        text-align: center;
        padding: 0 20px;
    }
    /* .principle 是设计铁律主说明文字 */
    .footer-desc .principle {
        font-size: 16px;
        color: var(--text-soft);
        line-height: 1.8;
        margin-bottom: 10px;
    }
    /* .tech-stack 是技术栈说明文字 */
    .footer-desc .tech-stack {
        font-size: 13px;
        color: var(--text-muted);
        letter-spacing: 0.5px;
    }
    /* .footer-disclaimer 是底部免责声明小字 */
    .footer-disclaimer {
        max-width: 900px;
        margin: 8px auto 30px;
        text-align: center;
        font-size: 11px;
        color: rgba(252, 165, 165, 0.5);  /* 更淡的浅红，弱化处理 */
        padding: 0 20px;
    }

    /* ===== 任务类型多色卡片 (首页: 智能问答/合同审核...) ===== */
    /* .task-type-cards 是首页 5 张任务类型选择卡片的网格容器 */
    .task-type-cards {
        max-width: 900px;
        margin: 24px auto 8px;
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));  /* 每列最小 160px */
        gap: 12px;
    }
    /* .task-type-card 是单张任务类型卡片 */
    .task-type-card {
        background: rgba(0, 0, 0, 0.25);
        border-radius: 12px;
        padding: 14px 16px;
        cursor: pointer;
        transition: all 0.2s;
        text-align: center;
        border: 2px solid transparent;  /* 默认透明边框，选中时变蓝 */
    }
    /* 卡片悬停态 */
    .task-type-card:hover {
        background: rgba(0, 0, 0, 0.4);
        transform: translateY(-2px);
    }
    /* 卡片选中态：蓝色半透明背景 + 蓝色边框 + 蓝色辉光 */
    .task-type-card.active {
        background: rgba(37, 99, 235, 0.18);
        border-color: var(--blue-bright);
        box-shadow: 0 0 0 1px var(--blue-glow);
    }
    /* 5 种颜色变体的顶部 3px 彩色边 */
    .task-type-card.blue { border-top: 3px solid var(--card-blue); }
    .task-type-card.orange { border-top: 3px solid var(--card-orange); }
    .task-type-card.green { border-top: 3px solid var(--card-green); }
    .task-type-card.purple { border-top: 3px solid var(--card-purple); }
    .task-type-card.pink { border-top: 3px solid var(--card-pink); }
    /* 卡片图标 emoji 样式 */
    .task-type-card-icon {
        font-size: 22px;
        margin-bottom: 6px;
    }
    /* 卡片标签文字样式 */
    .task-type-card-label {
        font-size: 14px;
        font-weight: 700;
        color: var(--text-white);
    }
    /* 选中态下标签文字变浅蓝 */
    .task-type-card.active .task-type-card-label {
        color: var(--blue-soft);
    }

    /* ===== 按钮 ===== */
    /* 全局 stButton 按钮样式：蓝色渐变 + 白字 + 圆角 */
    .stButton > button {
        background: linear-gradient(135deg, var(--blue-mid), var(--blue-bright));
        color: white !important;
        border: 1px solid var(--blue-glow);
        border-radius: 10px;
        padding: 10px 24px;
        font-weight: 600;
        transition: all 0.2s;
        font-size: 14px;
    }
    /* 按钮悬停态：更深蓝 + 上移 1px + 蓝色辉光 */
    .stButton > button:hover {
        background: linear-gradient(135deg, #1d4ed8, var(--blue-bright));
        border-color: var(--blue-soft);
        transform: translateY(-1px);
        box-shadow: 0 4px 14px var(--blue-glow);
    }
    /* 按钮内部所有子元素文字强制白色 */
    .stButton > button p, .stButton > button span, .stButton > button div {
        color: white !important;
    }

    /* ===== 输入框 (黑色字 + 白底/清晰色) ===== */
    /* 各类输入控件统一白底黑字，确保在深色页面上清晰可读 */
    .stTextInput > div > div > input,
    .stTextArea > div > div > textarea,
    .stSelectbox > div > div > div,
    .stNumberInput > div > div > input {
        background: rgba(255, 255, 255, 0.95) !important;  /* 接近纯白底 */
        color: #111827 !important;         /* 黑色字, 确保清楚 */
        border: 1px solid rgba(37, 99, 235, 0.25) !important;  /* 蓝色细边框 */
        border-radius: 8px !important;
        font-weight: 500 !important;
    }
    /* 输入框占位符文字颜色 */
    .stTextInput > div > div > input::placeholder,
    .stTextArea > div > div > textarea::placeholder {
        color: #6b7280 !important;
    }
    /* 各类输入控件的 label 文字颜色与字重 */
    .stTextInput label, .stTextArea label, .stSelectbox label, .stNumberInput label {
        color: var(--text-soft) !important;
        font-weight: 600;
        font-size: 13px;
    }

    /* ===== Streamlit radio 修复: 黑色字可见 ===== */
    /* 主内容区 radio 选项：白底黑字（侧边栏 radio 由前面规则单独处理为深色风格） */
    [data-testid="stRadio"] label {
        color: #111827 !important;
        background: rgba(255, 255, 255, 0.9);
        padding: 6px 10px;
        border-radius: 8px;
        font-weight: 500;
    }
    /* 主内容区 radio 悬停态：纯白背景 */
    [data-testid="stRadio"] label:hover {
        background: rgba(255, 255, 255, 1);
    }

    /* ===== expander / file_uploader ===== */
    /* 文件上传器内部容器：白底黑字 + 蓝色边框 */
    .stFileUploader > div > div {
        background: rgba(255, 255, 255, 0.95) !important;
        color: #111827 !important;
        border-radius: 10px !important;
        border: 1px solid rgba(25, 118, 210, 0.3) !important;
    }
    /* 文件上传器内部所有子元素文字颜色统一深灰 */
    .stFileUploader > div > div div,
    .stFileUploader > div > div span,
    .stFileUploader > div > div p,
    .stFileUploader > div > div small,
    .stFileUploader > div > div label {
        color: #374151 !important;
    }
    /* 文件上传器外层 label 文字颜色（柔红色） */
    .stFileUploader label {
        color: #fecaca !important;
        font-weight: 600 !important;
        font-size: 13px !important;
    }
    /* expander 折叠面板：半透明黑底 + 圆角 + 细边框 */
    [data-testid="stExpander"] {
        background: rgba(0, 0, 0, 0.3);
        border-radius: 10px;
        border: 1px solid rgba(254, 242, 242, 0.1);
    }
    /* expander 折叠面板的标题（summary）文字样式 */
    [data-testid="stExpander"] details summary p,
    [data-testid="stExpander"] details summary span {
        color: #ffffff !important;
        font-weight: 600 !important;
    }
    /* selectbox / multiselect 的 label 颜色 */
    .stSelectbox label, .stMultiSelect label {
        color: #fecaca !important;
        font-weight: 600 !important;
    }
    /* toggle / checkbox 的 label 颜色 */
    [data-testid="stToggle"] label, [data-testid="stCheckbox"] label {
        color: #fecaca !important;
    }

    /* ===== 信息/警告框 ===== */
    /* stInfo/stWarning/stError/stSuccess 四种提示框统一圆角与深色文字 */
    .stInfo, .stWarning, .stError, .stSuccess {
        border-radius: 10px !important;
        color: var(--text-dark) !important;
    }
    /* 提示框内部所有子元素继承父级颜色 */
    .stInfo *, .stWarning *, .stError *, .stSuccess * {
        color: inherit !important;
    }
    /* 信息框：蓝色半透明背景 + 蓝色边框 */
    .stInfo {
        background: rgba(25, 118, 210, 0.18) !important;
        border: 1px solid rgba(25, 118, 210, 0.4) !important;
    }
    /* 警告框：琥珀色半透明背景 + 琥珀色边框 */
    .stWarning {
        background: rgba(251, 191, 36, 0.18) !important;
        border: 1px solid rgba(251, 191, 36, 0.4) !important;
    }
    /* 错误框：红色半透明背景 + 红色边框 */
    .stError {
        background: rgba(239, 68, 68, 0.18) !important;
        border: 1px solid rgba(239, 68, 68, 0.4) !important;
    }
    /* 成功框：绿色半透明背景 + 绿色边框 */
    .stSuccess {
        background: rgba(34, 197, 94, 0.18) !important;
        border: 1px solid rgba(34, 197, 94, 0.4) !important;
    }

    /* ===== 风险卡片 ===== */
    /* .risk-card 是单张风险项卡片，左侧彩色粗边作为严重级别标识 */
    .risk-card {
        background: rgba(0, 0, 0, 0.3);
        border-radius: 12px;
        padding: 18px 20px;
        margin-bottom: 16px;
        border-left: 4px solid var(--blue-bright);  /* 默认左侧蓝色粗边 */
        backdrop-filter: blur(6px);
        border: 1px solid rgba(254, 242, 242, 0.08);
    }
    /* 4 种严重级别的左侧粗边颜色：红/橙/黄/蓝 */
    .risk-card.critical { border-left: 4px solid #ef4444; }
    .risk-card.high { border-left: 4px solid #f97316; }
    .risk-card.medium { border-left: 4px solid #fbbf24; }
    .risk-card.low { border-left: 4px solid #1976D2; }

    /* .risk-header 是风险卡片头部（徽章 + 标题 + 来源） */
    .risk-header {
        display: flex;
        align-items: center;
        gap: 12px;
        margin-bottom: 12px;
        flex-wrap: wrap;
    }
    /* .risk-badge 是严重级别徽章（胶囊形） */
    .risk-badge {
        display: inline-block;
        padding: 4px 12px;
        border-radius: 20px;
        font-size: 12px;
        font-weight: 700;
        color: white;
    }
    /* 4 种严重级别徽章的渐变背景色 */
    .risk-badge.critical { background: linear-gradient(135deg, #dc2626, #ef4444); }
    .risk-badge.high { background: linear-gradient(135deg, #ea580c, #f97316); }
    .risk-badge.medium { background: linear-gradient(135deg, #d97706, #fbbf24); color: #1f2937; }  /* 黄底配深色字 */
    .risk-badge.low { background: linear-gradient(135deg, #0D47A1, #1976D2); }

    /* .risk-title 是风险描述标题，flex:1 占满中间空间 */
    .risk-title {
        font-size: 15px;
        font-weight: 700;
        color: var(--text-white);
        flex: 1;
    }
    /* .risk-source 是风险来源标签（小胶囊） */
    .risk-source {
        font-size: 11px;
        color: var(--text-muted);
        background: rgba(25, 118, 210, 0.15);
        padding: 2px 8px;
        border-radius: 4px;
    }
    /* .risk-body 是风险正文描述 */
    .risk-body {
        color: var(--text-soft);
        font-size: 14px;
        line-height: 1.6;
        margin-bottom: 12px;
    }
    /* .risk-meta 是元信息行（条款 + 法条依据） */
    .risk-meta {
        display: flex;
        gap: 16px;
        font-size: 12px;
        color: var(--text-muted);
        margin-bottom: 12px;
        flex-wrap: wrap;
    }
    /* .risk-suggestion 是修改建议框 */
    .risk-suggestion {
        background: rgba(25, 118, 210, 0.1);
        border-radius: 8px;
        padding: 10px 14px;
        margin-bottom: 12px;
        font-size: 13px;
        color: var(--blue-soft);
        border: 1px solid rgba(25, 118, 210, 0.2);
    }
    /* .risk-suggestion-label 是"修改建议"小标签 */
    .risk-suggestion-label {
        color: var(--blue-soft);
        font-weight: 600;
        font-size: 12px;
        margin-bottom: 4px;
    }

    /* ===== 文档高亮 ===== */
    /* .doc-container 是合同/文档原文展示容器，带滚动条 */
    .doc-container {
        background: rgba(0, 0, 0, 0.25);
        border-radius: 12px;
        padding: 24px;
        border: 1px solid rgba(254, 242, 242, 0.08);
        max-height: 70vh;  /* 最大高度 70% 视口，超出滚动 */
        overflow-y: auto;  /* 垂直方向自动滚动条 */
        font-size: 14px;
        line-height: 1.8;
    }
    /* .doc-paragraph 是单个文档段落 */
    .doc-paragraph {
        margin-bottom: 14px;
        padding: 8px 12px;
        border-radius: 6px;
        color: var(--text-soft);
    }
    /* 4 种严重级别的高亮段落背景色与左侧粗边 */
    .doc-paragraph.highlight-critical {
        background: rgba(239, 68, 68, 0.22);
        border-left: 3px solid #ef4444;
    }
    .doc-paragraph.highlight-high {
        background: rgba(249, 115, 22, 0.2);
        border-left: 3px solid #f97316;
    }
    .doc-paragraph.highlight-medium {
        background: rgba(251, 191, 36, 0.18);
        border-left: 3px solid #fbbf24;
        color: var(--text-white);  /* 黄底配白字更清晰 */
    }
    .doc-paragraph.highlight-low {
        background: rgba(25, 118, 210, 0.16);
        border-left: 3px solid #1976D2;
    }

    /* ===== 思考过程动画 ===== */
    /* .thinking-container 是思考过程提示卡片 */
    .thinking-container {
        background: rgba(37, 99, 235, 0.12);
        border: 1px solid rgba(25, 118, 210, 0.3);
        border-radius: 12px;
        padding: 14px 18px;
        margin-bottom: 12px;
        display: flex;
        align-items: flex-start;
        gap: 12px;
    }
    /* .thinking-icon 是左侧思考图标 */
    .thinking-icon {
        font-size: 18px;
        margin-top: 2px;
    }
    /* .thinking-content 是右侧思考文字内容区 */
    .thinking-content {
        flex: 1;
    }
    /* .thinking-title 是思考标题 */
    .thinking-title {
        color: var(--blue-soft);
        font-size: 13px;
        font-weight: 700;
        margin-bottom: 6px;
    }
    /* .thinking-steps 是思考步骤文字 */
    .thinking-steps {
        color: var(--text-soft);
        font-size: 13px;
        line-height: 1.7;
    }
    /* .thinking-dots 是三个跳动小圆点动画容器 */
    .thinking-dots {
        display: inline-flex;
        gap: 4px;
        margin-left: 4px;
    }
    /* 单个小圆点样式 */
    .thinking-dots span {
        display: inline-block;
        width: 6px;
        height: 6px;
        background: var(--blue-soft);
        border-radius: 50%;
        animation: bounce 1.4s infinite ease-in-out both;  /* 应用 bounce 动画 */
    }
    /* 三个圆点错开动画延迟，形成"波浪"效果 */
    .thinking-dots span:nth-child(1) { animation-delay: -0.32s; }
    .thinking-dots span:nth-child(2) { animation-delay: -0.16s; }
    /* @keyframes bounce 定义缩放动画：0%与100%缩小为0，40%放大为1 */
    @keyframes bounce {
        0%, 80%, 100% { transform: scale(0); }
        40% { transform: scale(1); }
    }

    /* ===== 风险总览卡片 ===== */
    /* .risk-overview 是顶部风险评分总览卡片 */
    .risk-overview {
        background: rgba(0, 0, 0, 0.28);
        border-radius: 12px;
        padding: 20px 24px;
        margin-bottom: 20px;
        border: 1px solid rgba(254, 242, 242, 0.08);
    }
    /* .score-circle 是风险评分圆形数字展示 */
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
    /* 3 种风险等级的圆形背景渐变 */
    .score-circle.low { background: linear-gradient(135deg, #16a34a, #22c55e); color: white; }
    .score-circle.medium { background: linear-gradient(135deg, #d97706, #fbbf24); color: #1f2937; }
    .score-circle.high { background: linear-gradient(135deg, #dc2626, #ef4444); color: white; }
    /* .overview-info 是评分圆右侧的信息块 */
    .overview-info { display: inline-block; vertical-align: top; }
    /* .overview-label 是信息块标签 */
    .overview-label { font-size: 13px; color: var(--text-muted); margin-bottom: 4px; }
    /* .overview-value 是信息块主值（风险等级） */
    .overview-value { font-size: 20px; font-weight: 700; color: var(--text-white); margin-bottom: 4px; }
    /* .overview-desc 是信息块描述 */
    .overview-desc { font-size: 13px; color: var(--text-soft); }

    /* ===== 统计卡片 ===== */
    /* .stat-row 是统计卡片行，flex 布局水平排列 */
    .stat-row { display: flex; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; }
    /* .stat-mini 是单个小统计卡片 */
    .stat-mini {
        flex: 1;  /* 等分剩余空间 */
        min-width: 80px;
        background: rgba(0, 0, 0, 0.25);
        border-radius: 10px;
        padding: 12px 16px;
        text-align: center;
        border: 1px solid rgba(254, 242, 242, 0.08);
    }
    /* .stat-mini .num 是统计数字，大号粗体白字 */
    .stat-mini .num { font-size: 24px; font-weight: 900; color: var(--text-white); }
    /* 各严重级别统计数字的颜色 */
    .stat-mini.critical .num { color: #ef4444; }
    .stat-mini.high .num { color: #f97316; }
    .stat-mini.medium .num { color: #fbbf24; }
    .stat-mini.low .num { color: var(--blue-bright); }
    /* .stat-mini .label 是统计标签小字 */
    .stat-mini .label { font-size: 11px; color: var(--text-muted); margin-top: 4px; }

    /* ===== 聊天消息 ===== */
    /* stChatMessage 容器：透明背景 + 上下内边距 */
    [data-testid="stChatMessage"] {
        background: transparent !important;
        padding: 12px 0;
    }
    /* 用户消息气泡：蓝色渐变背景 + 圆角 + 蓝色边框 */
    [data-testid="stChatMessage.user"] > div > div {
        background: linear-gradient(135deg, var(--blue-mid), var(--blue-bright));
        border-radius: 16px;
        padding: 14px 18px;
        border: 1px solid var(--blue-glow);
    }
    /* 用户消息内部文字颜色统一白色 */
    [data-testid="stChatMessage.user"] p,
    [data-testid="stChatMessage.user"] span,
    [data-testid="stChatMessage.user"] li {
        color: white !important;
    }
    /* 助手消息气泡：半透明黑底 + 圆角 + 细边框 */
    [data-testid="stChatMessage.assistant"] > div > div {
        background: rgba(0, 0, 0, 0.25);
        border-radius: 16px;
        padding: 14px 18px;
        border: 1px solid rgba(254, 242, 242, 0.08);
    }
    /* 助手消息正文与列表项颜色 */
    [data-testid="stChatMessage.assistant"] p,
    [data-testid="stChatMessage.assistant"] li {
        color: var(--text-soft) !important;
    }
    /* 助手消息各级标题颜色 */
    [data-testid="stChatMessage.assistant"] h1,
    [data-testid="stChatMessage.assistant"] h2,
    [data-testid="stChatMessage.assistant"] h3,
    [data-testid="stChatMessage.assistant"] h4 {
        color: var(--text-white) !important;
    }

    /* ===== 彻底隐藏首页内部的隐藏 radio 与 selectbox 控件 ===== */
    /* 通过 aria-label 精准定位隐藏的 radio 控件，使用多种方式彻底隐藏（display/尺寸/visibility/位移） */
    [data-testid="stRadio"][aria-label="task_type_hidden"],
    [data-testid="stRadio"][aria-label="task_type_radio"] {
        display: none !important;
        height: 0 !important;
        width: 0 !important;
        overflow: hidden !important;
        visibility: hidden !important;
        position: absolute !important;
        left: -9999px !important;  /* 移出视口外，确保不影响布局 */
    }
    /* 隐藏 qa/contract 等文字外露的 radio —— 通过 :has 伪类定位包含特定 label 的 radiogroup */
    div[role="radiogroup"]:has(label[for*="task_type_radio"]) {
        display: none !important;
    }

    /* ===== 隐藏默认元素 ===== */
    /* 隐藏 Streamlit 右上角主菜单 */
    #MainMenu { visibility: hidden; }
    /* 隐藏底部 footer */
    footer { visibility: hidden; }
    /* 隐藏 Streamlit 装饰性元素 */
    [data-testid="stDecoration"] { display: none; }

    /* ===== 滚动条 ===== */
    /* 自定义 Webkit 滚动条宽度与轨道、滑块颜色 */
    ::-webkit-scrollbar { width: 8px; }
    ::-webkit-scrollbar-track { background: rgba(0, 0, 0, 0.2); }
    ::-webkit-scrollbar-thumb { background: rgba(25, 118, 210, 0.5); border-radius: 4px; }
    ::-webkit-scrollbar-thumb:hover { background: rgba(25, 118, 210, 0.7); }
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
        <div style="font-size:18px;font-weight:900;color:#ffffff;letter-spacing:2px;text-shadow:0 1px 4px rgba(0,0,0,0.5);">法智引擎</div>
        <!-- 副标题：柔红色小字 -->
        <div style="color:#fecaca;font-size:12px;margin-top:4px;opacity:0.85;">AI 原生法律助理</div>
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
        "merged_risk_items": [  # 4 项风险，覆盖 critical/high/medium/low 四个级别，便于演示
            {"severity": "critical", "source": "合同审核", "description": "违约金比例超过司法保护上限", "clause": "第五条 违约责任", "legal_basis": "《民法典》第585条", "suggestion": "建议将违约金调整为每日万分之三至万分之五"},
            {"severity": "high", "source": "合同审核", "description": "争议解决管辖约定可能被认定无效", "clause": "第六条 争议解决", "legal_basis": "《民事诉讼法》第24条", "suggestion": "建议约定合同履行地或被告所在地人民法院管辖"},
            {"severity": "medium", "source": "合同审核", "description": "预付款比例较高，存在资金占用风险", "clause": "第三条 付款方式", "legal_basis": "《民法典》第510条", "suggestion": "建议将预付款比例降至20%"},
            {"severity": "low", "source": "合同审核", "description": "合同标的描述缺少质量标准", "clause": "第一条 合同标的", "legal_basis": "《民法典》第512条", "suggestion": "建议补充详细的技术规格和验收标准"}
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
            {"severity": "high", "source": "数据合规", "description": "未明确个人信息保护条款", "clause": "数据与隐私保护", "legal_basis": "《个人信息保护法》第17条", "suggestion": "建议增加个人信息处理告知条款"},
            {"severity": "medium", "source": "税务合规", "description": "发票开具与税务承担约定不明确", "clause": "税务与发票", "legal_basis": "《税收征收管理法》第21条", "suggestion": "建议明确发票类型和开具时间"},
            {"severity": "low", "source": "劳动合规", "description": "未涉及员工竞业限制", "clause": "保密条款", "legal_basis": "《劳动合同法》第23条", "suggestion": "建议补充员工保密和竞业限制约定"}
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


def _highlight_doc(doc_text, risk_items):
    """根据风险项关键词高亮文档段落，返回 HTML 字符串。

    作用：
        将合同/文档原文按段落拆分，根据每项风险的 clause/description/legal_basis/
        suggestion 字段提取关键词，匹配段落文本。命中则按严重级别（critical>high>
        >medium>low，取最严重）为段落添加对应高亮 CSS 类，最终生成带高亮的 HTML。

    参数：
        doc_text (str): 文档原文
        risk_items (list[dict]): 风险项列表，每项含 severity/clause/description/
            legal_basis/suggestion 等字段

    返回值：
        str: HTML 字符串，包含 .doc-container 容器与若干 .doc-paragraph 段落 div

    可迁移性说明：
        纯文本处理 + HTML 生成，无 Streamlit 依赖，可迁移到任何需要文档高亮的场景。
        匹配策略为"关键词子串包含"，简单但可能误匹配；若需更精准可改为正则或语义匹配。
    """
    # 统一换行符：将 Windows 风格 \r\n 转为 \n，并去除首尾空白
    normalized = doc_text.replace("\r\n", "\n").strip()
    # 优先按双换行（空行）分段
    paragraphs = [p.strip() for p in normalized.split("\n\n") if p.strip()]
    # 若双换行分段只有 0 或 1 段，但文本中存在单换行，则降级按单换行分段
    if len(paragraphs) <= 1 and "\n" in normalized:
        paragraphs = [p.strip() for p in normalized.split("\n") if p.strip()]
    # 若仍无段落（极端空文本），则将整段原文作为唯一段落
    if not paragraphs:
        paragraphs = [normalized]

    # para_highlights: {段落索引: 严重级别}，记录每个段落应高亮的级别
    para_highlights = {}
    # sev_order: 严重级别排序字典，数字越小越严重（用于取最严重级别）
    sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    # 遍历每个风险项，提取关键词并匹配段落
    for risk in risk_items:
        sev = risk.get("severity", "low").lower()  # 当前风险级别
        keywords = []  # 关键词列表
        # 从 clause/description/legal_basis/suggestion 4 个字段提取关键词
        for field in ["clause", "description", "legal_basis", "suggestion"]:
            val = risk.get(field, "")
            # 仅保留长度 > 2 的非空字符串作为关键词（避免过短误匹配）
            if val and len(val.strip()) > 2:
                keywords.append(val.strip())
        # 遍历所有段落，检查是否包含任一关键词
        for i, para in enumerate(paragraphs):
            para_lower = para.lower()  # 段落小写，做大小写不敏感匹配
            for kw in keywords:
                kw_clean = kw.strip().lower()  # 关键词小写
                # 关键词长度 > 2 且存在于段落中
                if len(kw_clean) > 2 and kw_clean in para_lower:
                    # 取当前段落已记录级别与当前级别中更严重者
                    cur = para_highlights.get(i, "low")
                    if sev_order.get(sev, 3) < sev_order.get(cur, 3):
                        para_highlights[i] = sev
                    break  # 命中一个关键词即可，跳出关键词循环

    # 拼接 HTML：外层 .doc-container 容器
    html_parts = ['<div class="doc-container">']
    # 遍历段落，按高亮级别添加对应 CSS 类
    for i, para in enumerate(paragraphs):
        sev = para_highlights.get(i, "")  # 该段落的高亮级别（无则空字符串）
        cls = f"doc-paragraph highlight-{sev}" if sev else "doc-paragraph"  # 拼接 class
        # HTML 转义：< > 替换为实体，防止原文中 HTML 标签被解析
        safe_para = para.replace("<", "&lt;").replace(">", "&gt;")
        html_parts.append(f'<div class="{cls}">{safe_para}</div>')
    html_parts.append("</div>")
    return "\n".join(html_parts)  # 用换行连接各部分，返回完整 HTML


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
            legend_html += f'<div style="display:inline-flex;align-items:center;gap:6px;font-size:12px;color:#fca5a5;"><div style="width:12px;height:12px;border-radius:3px;background:{sev_info["color"]};"></div>{sev_info["label"]}风险: {sev_counts[sev_key]}项</div>'
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
        card_html = f'''
        <div class="risk-card {sev}">
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
                st.markdown(f'<div style="background:rgba(25,118,210,0.15);border:1px solid rgba(25,118,210,0.4);border-radius:8px;padding:10px 14px;margin-bottom:10px;font-size:13px;color:#90caf9;"><strong>📝 您的修改内容：</strong><br>{modified_content}</div>', unsafe_allow_html=True)
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
            "background: rgba(37,99,235,0.22) !important; border: 2px solid #1976D2 !important; box-shadow: 0 0 0 1px rgba(59,130,246,0.35) !important;"
            if active
            else "border: 2px solid transparent !important;"
        )
        # 选中态文字浅蓝，未选中态文字白色
        active_label_c = "color: #93c5fd !important;" if active else "color: #ffffff !important;"
        key_sel = f"__tt_card_{t['key']}"  # Streamlit 注入的容器 class 名（st-key-XXX）
        task_card_css += f"""
        div[class*="st-key-{key_sel}"] button,
        div[class*="st-key-{key_sel}"] button[data-testid="stBaseButton-secondary"] {{
            background: rgba(0,0,0,0.3) !important;
            {active_bg}
            border-top: 3px solid {border_top_c} !important;
            border-radius: 12px !important;
            padding: 14px 12px !important;
            text-align: center !important;
            white-space: normal !important;  /* 允许换行 */
            height: auto !important;
            min-height: 88px !important;
            box-shadow: none !important;
            letter-spacing: 0.5px;
        }}
        div[class*="st-key-{key_sel}"] button p,
        div[class*="st-key-{key_sel}"] button span,
        div[class*="st-key-{key_sel}"] button div,
        div[class*="st-key-{key_sel}"] button[data-testid="stMarkdownContainer"] * {{
            {active_label_c}
            font-weight: 700 !important;
            font-size: 14px !important;
            text-shadow: 0 1px 2px rgba(0,0,0,0.4);
        }}
        div[class*="st-key-{key_sel}"] button:hover {{
            background: rgba(0,0,0,0.45) !important;
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
        /* 主输入框外层 div：蓝色大边框 + 圆角 + 阴影（与合同审核页保持一致的视觉风格） */
        div[data-testid="stTextArea"]:has(textarea[aria-label="请输入您的问题，支持 Shift + Enter 换行"]) > div > div {
            border: 2px solid rgba(59,130,246,0.55) !important;
            border-radius: 18px !important;
            box-shadow: 0 0 0 1px rgba(59,130,246,0.18), 0 20px 60px rgba(0,0,0,0.45) !important;
            background: #ffffff !important;  /* 文本框底色改为白色，与合同审核页面样式逻辑相同 */
            transition: all 0.3s;
        }
        /* 主输入框悬停态：边框变亮蓝 + 阴影更深 */
        div[data-testid="stTextArea"]:has(textarea[aria-label="请输入您的问题，支持 Shift + Enter 换行"]):hover > div > div {
            border-color: #1976D2 !important;
            box-shadow: 0 0 0 1px rgba(59,130,246,0.35), 0 24px 72px rgba(0,0,0,0.55) !important;
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

        # 输入框下方一行四列：【深度思考（左） | 上传文档 | 上传图片 | 发送按钮（右）】
        # —— 上传按钮放在输入文本框下侧，且夹在深度思考与发送按钮之间（与合同审核页布局顺序保持一致）
        # —— 注意：Streamlit 禁止在 st.columns 内部再嵌套 st.columns（会抛 _check_nested_element_violation），
        #    因此把"上传文档 + 上传图片"直接展开为两个独立列，而不是把列套列
        act_col_l, act_col_u1, act_col_u2, act_col_r = st.columns([1.6, 1.7, 1.7, 1])  # 四列比例：思考开关 | 上传文档 | 上传图片 | 发送
        with act_col_l:
            # 深度思考开关：启用后输出更详细的理由与引用
            deep_thinking = st.toggle(
                "🔍 深度思考",
                value=False,
                key="home_deep_thinking",
                help="启用深度法律分析模式, 输出更详细的理由与引用",
            )
        with act_col_u1:
            # 文档上传器：支持多种文档格式，允许多文件
            st.file_uploader(
                current_meta["upload_label"],
                type=current_meta["upload_types"],
                key="home_upload",
                accept_multiple_files=True,
            )
        with act_col_u2:
            # 图片上传器：额外支持 webp
            st.file_uploader(
                "上传图片/截图",
                type=["png", "jpg", "jpeg", "webp"],
                key="home_upload_images",
                accept_multiple_files=True,
            )
        with act_col_r:
            # 发送分析按钮：primary 类型（主色调按钮），与深度思考、上传按钮处于同一行右侧
            send_pressed = st.button(
                "🚀 发送分析",
                key="home_send_btn",
                type="primary",
                use_container_width=True,
            )
        attach_pressed = False  # 保留占位（附件按钮预留位，当前未实现）

    # =========================================================
    # 多色快捷卡片 (类图二: 蓝/橙/绿/紫/琥珀 5张不同色边框卡片)
    # 使用 st.columns + st.markdown 输出 (无 onclick 避免 Streamlit 转义成原始文本)
    # 卡片下方放置按钮承载点击, CSS 让按钮浮在卡片上 (透明可见点击区)
    # =========================================================
    # shortcut_defs: 5 张快捷卡片配置，每张含 tag(标签)/tag_c(颜色)/icon/title/fill(填充示例文本)
    shortcut_defs = [
        {"k": "1", "tag": "OVERVIEW", "tag_c": "blue",   "icon": "🔍", "title": "法律研究",   "fill": "请检索最新的劳动合同解除赔偿相关的法律研究"},
        {"k": "2", "tag": "AGENT 01", "tag_c": "orange", "icon": "📋", "title": "深度报告",   "fill": "请为我生成一份买卖合同的深度审核报告"},
        {"k": "3", "tag": "AGENT 02", "tag_c": "green",  "icon": "📊", "title": "类案分析",   "fill": "请对劳动合同解除赔偿争议做类案分析"},
        {"k": "4", "tag": "AGENT 03", "tag_c": "purple", "icon": "📚", "title": "法条检索",   "fill": "请检索《民法典》合同编违约相关的全部法条"},
        {"k": "5", "tag": "AGENT 04", "tag_c": "amber",  "icon": "✍️", "title": "文书起草",   "fill": "请帮我起草一份电脑采购合同"},
    ]

    # 居中容器 [1, 6, 1]，与上方输入框对齐
    center2_l, center2_m, center2_r = st.columns([1, 6, 1])
    with center2_m:
        # 用两行网格展示 (3 + 2)：上行 3 列，下行 2 列
        shortcut_upper = st.columns(3)
        shortcut_lower = st.columns(2)
        # 将两行合并为 5 个 cell 的列表，按索引访问
        shortcut_cells = list(shortcut_upper) + list(shortcut_lower)

        # quick_css_colors: 颜色名到 (主色, 辅色) 的映射，用于卡片顶部渐变边
        quick_css_colors = {
            "blue":   ("#38bdf8", "#1976D2"),
            "orange": ("#fb923c", "#f59e0b"),
            "green":  ("#4ade80", "#10b981"),
            "purple": ("#a78bfa", "#8b5cf6"),
            "amber":  ("#fbbf24", "#d97706"),
        }

        # 简单可靠: 先渲染 VISUAL 卡片 (markdown HTML) + 后透明按钮覆盖 (st.button)
        # 生成 5 张卡片的视觉样式 CSS（.qcard-1 ~ .qcard-5）
        sc_visual_css = "<style>"
        for s in shortcut_defs:
            c1, c2 = quick_css_colors.get(s["tag_c"], ("#38bdf8", "#1976D2"))  # 主色与辅色
            sc_visual_css += f"""
            .qcard-{s['k']} {{
                padding: 18px 20px;
                border-radius: 14px;
                background: rgba(0,0,0,0.32);
                backdrop-filter: blur(8px);
                transition: all 0.25s;
                border: 1px solid {c1}55;  /* 主色 + 55 透明度边框 */
                border-top: 3px solid;
                border-image: linear-gradient(90deg, {c1}, {c2}) 1;  /* 顶部彩色渐变边 */
                min-height: 150px;
                cursor: default;
            }}
            .qcard-{s['k']}:hover {{
                transform: translateY(-3px);
                background: rgba(0,0,0,0.45);
            }}
            .qcard-{s['k']} .tag {{
                font-size: 11px; font-weight: 700; letter-spacing: 2px;
                color: {c1}; margin-bottom: 10px;
                text-shadow: 0 1px 2px rgba(0,0,0,0.4);
            }}
            .qcard-{s['k']} .title {{
                font-size: 17px; font-weight: 700; color: #ffffff;
                margin-bottom: 10px; display: flex; align-items: center; gap: 8px;
                text-shadow: 0 1px 3px rgba(0,0,0,0.5);
            }}
            .qcard-{s['k']} .desc {{
                font-size: 13px; color: #fecaca; line-height: 1.6;
                text-shadow: 0 1px 2px rgba(0,0,0,0.3);
            }}
            """
        # 透明按钮 (overlay): 基于 Streamlit 注入的 st-key-__scov_X 容器 class
        # 将按钮设为完全透明，并通过负 margin 上移覆盖到视觉卡片上方，承载点击事件
        for s in shortcut_defs:
            key_sel = f"__scov_{s['k']}"
            sc_visual_css += f"""
            div[class*="st-key-{key_sel}"] button {{
                opacity: 0 !important;  /* 完全透明 */
                background: transparent !important;
                border: none !important;
                box-shadow: none !important;
                color: transparent !important;
                padding: 0 !important;
                min-height: 150px !important;  /* 与视觉卡片同高 */
                height: 150px !important;
                width: 100% !important;
                margin-top: -155px !important;  /* 负 margin 上移，覆盖到视觉卡片上 */
                pointer-events: auto !important;  /* 仍可接收点击 */
                cursor: pointer !important;
                position: relative !important;
                z-index: 5 !important;  /* 层级高于视觉卡片 */
            }}
            div[class*="st-key-{key_sel}"] button * {{
                display: none !important;  /* 隐藏按钮内部所有文字 */
                color: transparent !important;
                font-size: 0 !important;
            }}
            """
        sc_visual_css += "</style>"
        st.markdown(sc_visual_css, unsafe_allow_html=True)

        # 渲染 5 张快捷卡片：每张由"视觉卡片 markdown + 透明覆盖按钮"两部分组成
        for idx, s in enumerate(shortcut_defs):
            with shortcut_cells[idx]:
                # 1. 视觉卡片 (markdown) —— 展示标签/标题/描述
                # 描述截断：超过 38 字符则取前 36 + "..."
                short_desc = (s["fill"][:36] + "...") if len(s["fill"]) > 38 else s["fill"]
                card_html = f"""
                <div class="qcard-{s['k']}">
                    <div class="tag">{s['tag']}</div>
                    <div class="title">{s['icon']} {s['title']}</div>
                    <div class="desc">{short_desc}</div>
                </div>
                """
                st.markdown(card_html, unsafe_allow_html=True)
                # 2. 透明 overlay 按钮 (接收点击) — 必须用 pending state 写入 textarea, 否则 Streamlit 禁止改 key 值
                # 按钮文本使用 __SC__ 前缀避免与可见文本冲突
                btn_text = f"__SC__{s['k']}__"
                if st.button(btn_text, key=f"__scov_{s['k']}", use_container_width=True):
                    # 点击后将示例文本写入 _pending_home_textarea_fill，触发 rerun，
                    # 下一帧主输入框逻辑会读取该值填充 textarea
                    st.session_state["_pending_home_textarea_fill"] = s["fill"]
                    st.rerun()

    # =========================================================
    # 底部说明文字 (类图三)
    # =========================================================
    # 渲染设计铁律 + 技术栈说明 + 免责声明
    st.markdown("""
    <div class="footer-desc">
        <div class="principle">设计铁律：AI做前置审查辅助生成风险提示 · 律师做最终决策签章交付</div>
        <div class="tech-stack">依据《律师法》第13/28条 · LangGraph + RAG + Neo4j + Milvus + bge-m3</div>
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
                        st.markdown(f"<div style='color:#fca5a5;font-size:12px;margin-bottom:8px;'>共 {len(result['doc_text'])} 字符 · {len(result['merged_risk_items'])} 项风险已标注</div>", unsafe_allow_html=True)
                        # 渲染高亮文档
                        if result["doc_text"]:
                            highlighted = _highlight_doc(result["doc_text"], result["merged_risk_items"])
                            st.markdown(highlighted, unsafe_allow_html=True)
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
                            st.markdown(_highlight_doc(result["doc_text"], result.get("merged_risk_items", [])), unsafe_allow_html=True)
                    with right_col:
                        st.markdown("### 🎯 合规风险")
                        risks = result.get("merged_risk_items", [])
                        if risks:
                            _render_stat_cards(risks)
                            _render_risk_cards(risks, key_prefix="home_compliance")

                else:
                    # ========== 智能问答/通用 ==========
                    # 拼接演示回答 Markdown（含法律分析/关键要点/建议操作）
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
    # 1) 立场选择：自动识别 / 甲方 / 乙方（全宽，左对齐）
    user_side = st.selectbox(
        "您的立场",
        ["自动识别", "甲方", "乙方"],
        use_container_width=True,  # 与 expander/button 保持一致的全宽
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
                input_text = uploaded_file.getvalue().decode("utf-8")
            except:
                input_text = "已上传文件"  # 解码失败时用占位文本
        if input_text:
            with st.spinner("⚖️ 法智引擎正在审核..."):
                # 根据演示模式选择数据源
                result = _get_demo_result(input_text) if demo_mode else legal_response_sync(input_text, task_type="contract_review")
                st.session_state["contract_full_result"] = result  # 缓存结果
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
                st.markdown(_highlight_doc(result["doc_text"], result.get("merged_risk_items", [])), unsafe_allow_html=True)
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
        if compliance_text.strip():
            with st.spinner("审查中..."):
                # 根据演示模式选择数据源
                result = _get_compliance_demo_result(compliance_text.strip()) if demo_mode else legal_response_sync(compliance_text.strip(), task_type="compliance_review")
                st.session_state["compliance_full_result"] = result
        else:
            st.warning("请粘贴待审查文档内容")

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
                st.markdown(_highlight_doc(result["doc_text"], result.get("merged_risk_items", [])), unsafe_allow_html=True)
        with right_col:
            st.markdown("### 🎯 合规风险")
            risks = result.get("merged_risk_items", [])
            if risks:
                _render_stat_cards(risks)
                _render_risk_cards(risks, key_prefix="compliance")
            else:
                st.success("✅ 未检测到合规风险")


# ==================== 法律检索独立页面 ====================
elif page == "🔍 法律检索":
    # 法律检索页面元数据
    RESEARCH_META = {
        "greeting": "您好, 我是检索智能体",
        "description": "您可以直接向我提问法律问题，比如\"违约金上限是多少？\"\"建设工程合同有哪些强制性规定？\"我会自动检索法律法规、类案判例、行业标准和市场基准，返回完整的法条原文、案例摘要、合规依据，不会对检索结果进行主观解释或综合结论（如需分析建议，请调用问答智能体或其他任务智能体）。",
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
    
    # 文件上传区：两列布局
    col_r1, col_r2 = st.columns([1, 1])
    with col_r1:
        research_upload = st.file_uploader("上传参考文档", type=["txt", "md", "docx", "pdf"], key="research_upload", accept_multiple_files=True)
    with col_r2:
        research_images = st.file_uploader("上传参考图片/截图", type=["png", "jpg", "jpeg", "webp"], key="research_upload_images", accept_multiple_files=True)

    # 操作区：与合同审核页保持相同的布局逻辑（等宽/左对齐，开始检索按钮放到最下侧）
    # 顺序：💡 检索示例 expander → 🎭 效果展示 button → 🔍 开始检索 button

    # 1) 检索示例折叠面板（全宽，左对齐）
    with st.expander("💡 检索示例"):
        research_examples = [
            "民法典中关于违约金的规定",
            "劳动合同法第47条经济补偿标准",
            "个人信息保护法第17条告知义务",
        ]
        for q_item in research_examples:
            st.markdown(f"- {q_item}")

    # 2) 效果展示切换按钮（全宽，左对齐；显式 secondary 灰色，只让"开始检索"为蓝色主按钮）
    if st.button("🎭 效果展示", key="toggle_demo_research", type="secondary", use_container_width=True):
        if "research_full_result" in st.session_state:
            # 已有结果则清除
            del st.session_state["research_full_result"]
            st.rerun()
        else:
            # 无结果则填入演示数据（含法条与案例）
            demo_result = {
                "output": "# 法律检索结果\n\n## 一、相关法条\n\n### 《中华人民共和国民法典》\n**第585条** 当事人可以约定一方违约时应当根据违约情况向对方支付一定数额的违约金，也可以约定因违约产生的损失赔偿额的计算方法。\n\n约定的违约金低于造成的损失的，人民法院或者仲裁机构可以根据当事人的请求予以增加；约定的违约金过分高于造成的损失的，人民法院或者仲裁机构可以根据当事人的请求予以适当减少。\n\n## 二、相关案例\n\n1. **最高人民法院关于审理买卖合同纠纷案件适用法律问题的解释** - 第28条\n   买卖合同当事人一方以对方违约造成的损失超过违约金为由主张增加违约金的，人民法院应当以违约造成的损失为基础，兼顾合同的履行情况、当事人的过错程度以及预期利益等因素，根据公平原则和诚实信用原则予以衡量。",
                "citations": [
                    {"title": "中华人民共和国民法典", "article_no": "第585条", "content": "当事人可以约定一方违约时应当根据违约情况向对方支付一定数额的违约金..."},
                    {"title": "最高人民法院关于审理买卖合同纠纷案件适用法律问题的解释", "article_no": "第28条", "content": "违约金调整的相关规定"}
                ]
            }
            st.session_state["research_full_result"] = demo_result
            st.rerun()

    # 3) 开始检索按钮：按要求放到最下侧（primary 主色按钮，全宽）
    if st.button("🔍 开始检索", type="primary", use_container_width=True, key="start_research"):
        input_query = query.strip()
        if input_query:
            with st.spinner("⚖️ 法智引擎正在检索..."):
                # 法律检索直接调用后端（task_type=legal_research）
                result = legal_response_sync(f"检索关于{input_query}的法律法规", task_type="legal_research")
                st.session_state["research_full_result"] = result
        else:
            st.warning("请输入检索关键词")

    # 渲染检索结果
    if "research_full_result" in st.session_state:
        result = st.session_state["research_full_result"]
        st.markdown("### 📋 检索结果")
        # 渲染输出文本（含 Markdown）
        st.markdown(result.get("output", "无结果"), unsafe_allow_html=True)
        
        # 引用法规折叠面板
        if result.get("citations"):
            with st.expander("📚 法规引用"):
                for cite in result["citations"]:
                    st.markdown(f"**{cite['title']} {cite['article_no']}**")
                    st.markdown(f"> {cite['content']}")  # 引用块形式展示法条内容
                    st.markdown("---")


# ==================== 小红书发布独立页面 ====================
elif page == "📱 小红书发布":
    # 小红书发布页面元数据
    XHS_META = {
        "greeting": "您好, 我是小红书发布智能体",  # 问候语
        "description": "您可以输入法律科普主题（如\"劳动合同维权\"\"租房合同避坑\"），或上传参考文档、封面图片素材，我会生成符合小红书风格的爆款标题、分点正文、热门话题标签与配图建议，帮助您高效产出高质量的法律科普笔记。",
    }
    # 渲染问候语与说明卡片
    st.markdown(f"""
    <div class="task-greeting">{XHS_META['greeting']}</div>
    <div class="task-intro-box">
        <p>{XHS_META['description']}</p>
    </div>
    """, unsafe_allow_html=True)

    # 小红书内容主题输入框
    topic = st.text_area("输入小红书内容主题", height=150, placeholder="如: 劳动合同维权、租房合同避坑...", key="xhs_topic_area")
    
    # 文件上传区：两列布局（封面图片 / 参考文档）
    col_x1, col_x2 = st.columns([1, 1])
    with col_x1:
        # 封面图片上传器
        xhs_images = st.file_uploader("上传封面图片", type=["png", "jpg", "jpeg", "webp"], key="xhs_upload_images", accept_multiple_files=True)
    with col_x2:
        # 参考文档上传器
        xhs_docs = st.file_uploader("上传参考文档", type=["txt", "md", "docx", "pdf"], key="xhs_upload_docs", accept_multiple_files=True)

    # 操作区：与合同审核页保持相同的布局逻辑（等宽/左对齐，生成文案按钮放到最下侧）
    # 顺序：💡 选题示例 expander → 🎭 效果展示 button → 📱 生成文案 button

    # 1) 选题示例折叠面板（全宽，左对齐）
    with st.expander("💡 选题示例"):
        xhs_examples = [
            "租房合同避坑指南：5个关键条款必须看",
            "劳动合同维权：被裁员后如何争取赔偿",
            "投资理财陷阱：这些合同条款要警惕",
        ]
        for q_item in xhs_examples:
            st.markdown(f"- {q_item}")

    # 2) 效果展示切换按钮（全宽，左对齐；显式 secondary 灰色，只让"生成文案"为蓝色主按钮）
    if st.button("🎭 效果展示", key="toggle_demo_xhs", type="secondary", use_container_width=True):
        if "xhs_full_result" in st.session_state:
            # 已有结果则清除
            del st.session_state["xhs_full_result"]
            st.rerun()
        else:
            # 无结果则填入演示小红书文案（含标题/正文/话题标签）
            demo_result = """# 📱 劳动合同维权必看！被裁员了怎么赔？

姐妹们！最近后台收到好多被裁员的私信，今天统一给大家讲清楚👇

## 🔑 核心知识点

### 1️⃣ 经济补偿金怎么算？
- **N** = 工作年限
- 不满半年按0.5算，满半年按1算
- 月工资 = 离职前12个月平均工资

### 2️⃣ 哪些情况可以要求2N？
- 违法解除劳动合同
- 没有合法理由裁员

### 3️⃣ 维权步骤
1. 收集证据（劳动合同、工资流水、聊天记录）
2. 与公司协商
3. 申请劳动仲裁
4. 不服可起诉

## ⚠️ 重点提醒
- 仲裁时效1年，别过期！
- 保留所有书面证据
- 可以寻求法律援助

---
#职场维权 #劳动仲裁 #被裁员 #劳动合同法 #法律科普"""
            st.session_state["xhs_full_result"] = demo_result
            st.rerun()

    # 3) 生成文案按钮：按要求放到最下侧（primary 主色按钮，全宽）
    if st.button("📱 生成文案", type="primary", use_container_width=True, key="start_xhs"):
        input_topic = topic.strip()
        if input_topic:
            with st.spinner("✨ 生成中..."):
                # 调用后端接口（不带 task_type，按通用问答处理）
                result = legal_response_sync(f"小红书发布: {input_topic}")
                st.session_state["xhs_full_result"] = result
        else:
            st.warning("请输入主题内容")

    # 渲染生成的小红书文案结果
    if "xhs_full_result" in st.session_state:
        result = st.session_state["xhs_full_result"]
        st.markdown("### 📝 生成内容")
        # 直接以 Markdown 渲染文案（含标题、列表、话题标签等）
        st.markdown(result, unsafe_allow_html=True)
