# -*- coding: utf-8 -*-
"""N2 文档提取节点: 读取上传文档(pdf/docx/txt/md 等), 提取纯文本 + MinerU 多模态结构化 JSON
=========================================================================================

# ============================================================
# 文件名称: nodes/doc_extract_node.py
# 文件作用: 文档提取 (MinerU 增强 · 多格式)
# ============================================================

【2026-08 改造说明】
在保持子图接线不变(本 doc_extract_node 现由 contract_compliance 子图在入口编排调用)的前提下,
把原 doc_extract_mineru_node 的 MinerU 多模态解析能力并入本节点:

  - .pdf      → MinerU 原生解析(版面/表格/图片/坐标标签), 失败降级
  - .docx     → 优先 MinerU(先转 PDF 再解析), 转换/解析失败降级本地解析(段落 + 表格)
  - .txt/.md  → 直接读取
  - 其他扩展名 → 尝试交给 MinerU, 失败兜底用 input
  统一输出:
  - doc_text (str):            纯文本(向后兼容, 下游全部节点照常消费)
  - doc_structured_json (dict): 结构化 JSON(含表格/图片/bbox, 供未来多模态消费)
  MinerU 不可用(未安装 magic_pdf)时自动降级为纯文本解析, 不影响主流程。

【为什么保留本地 docx 解析】
MinerU 解析 docx 需先转 PDF(docx2pdf 依赖 MS Word / libreoffice 命令行), 环境可能没有;
本地 _docx_to_text 已增强支持 <w:tbl> 表格(合同单价表/付款节点/违约金比例常见),
保证表格内容不丢 —— 这正好补上"用户上传合同含表格"的真实场景。

【MinerU 本地模型注意】
UNIPipe 走本地模型管线, 首次使用会下载模型权重(需联网, 约数百MB~GB);
下载失败或离线时抛异常 → 本节点捕获后静默降级为本地解析, 不会中断流程。
"""

# 导入 os 模块, 用于路径操作(os.path.exists / os.path.splitext 等)
import os

# 导入 zipfile 模块, 用于读取 .docx 文件(本质是 ZIP 压缩包)
import zipfile

# 导入 xml.etree.ElementTree, 用于解析 docx 内部的 XML 文档
from xml.etree import ElementTree as ET

# 导入 datetime, 用于 MinerU 结构化 JSON 的 parse_time 时间戳
from datetime import datetime

# 从同包导入 AgentState 类型, 作为节点函数的类型注解
from __004__langgraph_more_nodes.agent_state import AgentState

# 定义 WordprocessingML 的官方命名空间字符串
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"


# ============================================================
# 本地 docx 解析 (无第三方依赖, 已增强表格支持)
# ============================================================
def _docx_to_text(path: str) -> str:
    """
    从 .docx 文件中提取纯文本(不依赖 python-docx 第三方库)。

    增强(2026-08):
      - 除段落(<w:p>/<w:t>)外, 新增表格(<w:tbl>)解析: 每个 <w:tr> 行内各单元格
        文本用制表符 \t 拼接成一行输出, 保证"单价/数量/金额/违约金比例"等表格数据
        进入 doc_text, 供 numeric_extract 等下游正则抽取;
      - 表格内段落不重复输出(收集 table 内段落 id 跳过), 避免同一文本出现两次。
    """
    try:
        with zipfile.ZipFile(path, "r") as z:
            if "word/document.xml" not in z.namelist():
                return ""
            xml_bytes = z.read("word/document.xml")
    except Exception as e:
        print(f"❌ 读取docx失败: {e}")
        return ""

    lines = []
    try:
        root = ET.fromstring(xml_bytes)
        body = root.find(f"{W}body")
        if body is None:
            return ""

        # 收集表格内所有段落的 id, 段落遍历时跳过(避免与表格输出重复)
        table_p_ids = set()
        for tbl in body.iter(f"{W}tbl"):
            for p in tbl.iter(f"{W}p"):
                table_p_ids.add(id(p))

        # 表格: 每行 <w:tr> → 单元格 <w:tc> 文本用 \t 拼接
        for tbl in body.iter(f"{W}tbl"):
            for tr in tbl.iter(f"{W}tr"):
                cells = []
                for tc in tr.iter(f"{W}tc"):
                    cell = "".join(t.text for t in tc.iter(f"{W}t") if t.text).strip()
                    if cell:
                        cells.append(cell)
                if cells:
                    lines.append("\t".join(cells))
            lines.append("")  # 表格块后留空行, 与正文分隔

        # 段落: 跳过表格内的段落(已由表格块输出)
        for elem in body.iter():
            if elem.tag == f"{W}p" and id(elem) not in table_p_ids:
                parts = [t.text for t in elem.iter(f"{W}t") if t.text]
                line = "".join(parts).strip()
                if line:
                    lines.append(line)
    except Exception as e:
        print(f"❌ 解析XML失败: {e}")

    return "\n".join(lines)


# ============================================================
# MinerU 配置注入 (必须在 import magic_pdf 之前执行)
# ============================================================
def _inject_mineru_env() -> bool:
    """
    把 Config 中读取到的 MinerU AK/SK 注入 os.environ, 保证 magic_pdf
    在首次 import/初始化模型时能立即拿到鉴权信息.

    返回值:
        bool: True 表示已成功注入 AK/SK, False 表示缺少有效配置.
    """
    try:
        from common.config import Config
        _cfg = Config()
        _ak = _cfg.MINERU_AK
        _sk = _cfg.MINERU_SK
    except Exception:
        _ak, _sk = None, None

    if not _ak or not _sk:
        return False

    # 同时注入四个变量名 (magic-pdf 历史版本兼容)
    _envs = {
        "MINERU_ACCESS_KEY": _ak,
        "MINERU_SECRET_KEY": _sk,
        "MINERU_AK": _ak,
        "MINERU_SK": _sk,
    }
    for _k, _v in _envs.items():
        os.environ.setdefault(_k, _v)

    def _mask(s: str) -> str:
        if len(s) <= 6:
            return "***"
        return f"{s[:3]}...{s[-3:]} (len={len(s)})"
    print(f"  [MinerU] 注入鉴权: AK={_mask(_ak)}, SK={_mask(_sk)}")
    return True


_INJECTED_MINERU_ENV = False  # 只注入一次的标记


def _ensure_mineru_env_injected():
    """懒加载注入: 首次检测 MinerU 前调用一次, 之后跳过."""
    global _INJECTED_MINERU_ENV
    if not _INJECTED_MINERU_ENV:
        _inject_mineru_env()
        _INJECTED_MINERU_ENV = True


def _check_mineru_available():
    """
    检测 MinerU (magic_pdf) 是否已安装且可用.

    返回值:
        bool: True 表示 MinerU 可用, False 表示需降级
    """
    _ensure_mineru_env_injected()  # import magic_pdf 之前必须先注入 env
    try:
        from magic_pdf.pipe.UNIPipe import UNIPipe
        from magic_pdf.rw.DiskReaderWriter import DiskReaderWriter
        return True
    except ImportError:
        return False
    except Exception:
        return False


def _parse_with_mineru(file_path: str) -> dict:
    """
    使用 MinerU 解析 PDF 文件, 返回标准结构化 JSON.

    返回值:
        dict: 标准 MinerU 输出 JSON (含 metadata + pages + blocks)
              解析失败时返回空 dict
    """
    try:
        _ensure_mineru_env_injected()

        from magic_pdf.pipe.UNIPipe import UNIPipe
        from magic_pdf.rw.DiskReaderWriter import DiskReaderWriter

        with open(file_path, "rb") as f:
            file_bytes = f.read()

        image_writer = DiskReaderWriter(os.path.dirname(file_path))
        pipe = UNIPipe(file_bytes, {"_pdf_type": "", "model_list": []}, image_writer)

        pipe.pipe_classify()
        pipe.pipe_analyze()
        pipe.pipe_parse()

        result = pipe.pipe_mk_uni_format(image_writer, drop_mode="none")
        return _normalize_mineru_output(result, file_path)

    except Exception as e:
        print(f"  ⚠️ MinerU解析失败: {e}")
        return {}


def _normalize_mineru_output(raw_result: dict, file_path: str) -> dict:
    """
    将 MinerU 原始输出转换为项目标准 JSON 格式.

    ⚠️ 注意: MinerU 1.3.x 的 uni 格式中页面字段可能是 "pdf_info" 而非 "blocks",
    若真实解析结果为空, 需按实际字段适配(见验证清单)。
    """
    structured = {
        "metadata": {
            "file_name": os.path.basename(file_path),
            "file_type": os.path.splitext(file_path)[1].lower(),
            "page_count": 0,
            "parse_engine": "MinerU",
            "parse_time": datetime.now().isoformat()
        },
        "pages": []
    }

    if raw_result and isinstance(raw_result, list):
        for page_idx, page_data in enumerate(raw_result):
            page_blocks = []
            blocks = page_data.get("blocks", []) if isinstance(page_data, dict) else []
            # 兼容 MinerU 用 "pdf_info" 字段的版本
            if not blocks and isinstance(page_data, dict):
                blocks = page_data.get("pdf_info", []) or []
            for block in blocks:
                block_type = block.get("type", "text")
                block_content = block.get("content", "") or block.get("text", "") or ""
                block_bbox = block.get("bbox", [0, 0, 0, 0])
                page_blocks.append({
                    "type": block_type,
                    "content": block_content,
                    "bbox": block_bbox,
                })
            structured["pages"].append({
                "page_idx": page_idx,
                "blocks": page_blocks
            })
        structured["metadata"]["page_count"] = len(raw_result)

    return structured


def _structured_json_to_text(structured_json: dict) -> str:
    """
    将结构化 JSON 转换为纯文本(向后兼容 doc_text 字段).
    """
    if not structured_json or not structured_json.get("pages"):
        return ""

    lines = []
    for page in structured_json["pages"]:
        for block in page.get("blocks", []):
            content = block.get("content", "")
            block_type = block.get("type", "text")

            if block_type == "table":
                # 表格内容: 将二维数组拼接为文本
                if isinstance(content, list):
                    for row in content:
                        if isinstance(row, list):
                            lines.append("\t".join(str(cell) for cell in row))
            elif block_type == "image":
                # 图片内容: 标注为 [图片]
                lines.append(f"[图片: {content}]")
            elif content:
                lines.append(content)

    return "\n".join(lines)


def _build_fallback_structured_json(text: str, file_path: str) -> dict:
    """
    降级方案: 当 MinerU 不可用时, 将纯文本包装为标准 JSON 格式,
    让下游节点可以统一使用 doc_structured_json 字段.
    """
    blocks = []
    for line in text.split("\n"):
        line = line.strip()
        if line:
            blocks.append({
                "type": "text",
                "content": line,
                "bbox": [0, 0, 0, 0],  # 降级模式无坐标
            })

    return {
        "metadata": {
            "file_name": os.path.basename(file_path) if file_path else "input_text",
            "file_type": os.path.splitext(file_path)[1].lower() if file_path else ".txt",
            "page_count": 1,
            "parse_engine": "fallback",
            "parse_time": datetime.now().isoformat()
        },
        "pages": [
            {
                "page_idx": 0,
                "blocks": blocks
            }
        ]
    }


def _try_convert_docx_to_pdf(docx_path: str) -> str:
    """
    尝试将 DOCX 转换为 PDF (使用 docx2pdf 或 libreoffice).

    返回值:
        str: 转换后的 PDF 文件路径; 失败返回空字符串
    """
    # 方案 1: 尝试使用 docx2pdf (Windows 下依赖 Word)
    try:
        from docx2pdf import convert
        pdf_path = docx_path.replace(".docx", ".pdf")
        convert(docx_path, pdf_path)
        if os.path.exists(pdf_path):
            return pdf_path
    except Exception:
        pass

    # 方案 2: 尝试使用 libreoffice 命令行
    try:
        import subprocess
        result = subprocess.run(
            ["libreoffice", "--headless", "--convert-to", "pdf", docx_path],
            capture_output=True, timeout=30
        )
        pdf_path = docx_path.replace(".docx", ".pdf")
        if os.path.exists(pdf_path):
            return pdf_path
    except Exception:
        pass

    return ""


# ============================================================
# 主节点: 多格式文档提取 (MinerU 优先, 本地解析兜底)
# ============================================================
def doc_extract_node(state: AgentState):
    """
    文档提取节点函数: 从上传文档或用户输入中提取合同/法规文本, 写入
    state["doc_text"], 并附带 state["doc_structured_json"](MinerU 结构化或 fallback 包装).

    优先级:
        1. .pdf          → MinerU 原生解析
        2. .docx         → MinerU(先转 PDF) → 失败降级本地 _docx_to_text(含表格)
        3. .txt/.md      → 直接读取
        4. 其他扩展名    → 尝试 MinerU, 失败兜底

    【注意】本节点只解析上传文件, 不把 input 当作文档兜底(旧逻辑已移除)。
    纯文本输入的归一化由 text_recognize_node(文本路径) 负责; 解析失败保留
    doc_text 为空, 交由 doc_empty_guard 拦截。

    参数:
        state (AgentState): 读取 uploaded_doc_path; 写入 doc_text / doc_structured_json.
    返回值:
        AgentState: 更新后的状态字典, 必含 doc_text 与 doc_structured_json.
    """
    print("开始文档提取 (MinerU 多格式)")

    doc_path = state.get("uploaded_doc_path", "")
    mineru_available = _check_mineru_available()
    if mineru_available:
        print("  MinerU 可用, 多模态解析优先")
    else:
        print("  MinerU 不可用(未安装 magic_pdf), 使用本地纯文本解析")

    if doc_path and os.path.exists(doc_path):
        ext = os.path.splitext(doc_path)[1].lower()

        # ---- 1) PDF: MinerU 原生解析 ----
        if ext == ".pdf" and mineru_available:
            structured_json = _parse_with_mineru(doc_path)
            if structured_json and structured_json.get("pages"):
                doc_text = _structured_json_to_text(structured_json)
                if doc_text.strip():
                    state["doc_text"] = doc_text
                    state["doc_structured_json"] = structured_json
                    print(f"  PDF MinerU 解析成功: {len(structured_json['pages'])} 页, {len(doc_text)} 字符")
                    return state

        # ---- 2) DOCX: 优先 MinerU(转 PDF), 失败降级本地解析 ----
        if ext == ".docx":
            if mineru_available:
                pdf_path = _try_convert_docx_to_pdf(doc_path)
                if pdf_path and os.path.exists(pdf_path):
                    structured_json = _parse_with_mineru(pdf_path)
                    if structured_json and structured_json.get("pages"):
                        doc_text = _structured_json_to_text(structured_json)
                        if doc_text.strip():
                            state["doc_text"] = doc_text
                            state["doc_structured_json"] = structured_json
                            print(f"  DOCX MinerU 解析成功: {len(structured_json['pages'])} 页, {len(doc_text)} 字符")
                            return state
                else:
                    print("  DOCX→PDF 转换失败, 降级本地解析(段落+表格)")

            # 本地降级: _docx_to_text 已支持表格
            text = _docx_to_text(doc_path)
            if text.strip():
                state["doc_text"] = text
                state["doc_structured_json"] = _build_fallback_structured_json(text, doc_path)
                print(f"  本地 DOCX 解析成功: {len(text)} 字符 (含表格)")
                return state

        # ---- 3) TXT / MD: 直接读取 ----
        if ext in (".txt", ".md"):
            with open(doc_path, "r", encoding="utf-8") as f:
                text = f.read()
            if text.strip():
                state["doc_text"] = text
                state["doc_structured_json"] = _build_fallback_structured_json(text, doc_path)
                print(f"  纯文本读取成功: {len(text)} 字符")
                return state

        # ---- 4) 其他扩展名: 尝试交给 MinerU ----
        if mineru_available:
            structured_json = _parse_with_mineru(doc_path)
            if structured_json and structured_json.get("pages"):
                doc_text = _structured_json_to_text(structured_json)
                if doc_text.strip():
                    state["doc_text"] = doc_text
                    state["doc_structured_json"] = structured_json
                    print(f"  其他格式({ext}) MinerU 解析成功: {len(doc_text)} 字符")
                    return state
            print(f"  不支持扩展名 {ext}, MinerU 亦失败 → 兜底 input")

    # ---- 5) 兜底: 无文件 / 文件不存在 / 全部解析失败 → 不回填 input ----
    # 【设计变更】文档路径不再把 input 当作文档兜底。文本输入的归一化职责
    # 已移交 text_recognize_node(文本路径)。本节点只负责"解析上传文件",
    # 解析不出文本就保留 doc_text 为空, 交由下游 doc_empty_guard 拦截
    # (提示用户重新上传 / 粘贴), 避免对空 doc_text 跑完整流水线。
    # 仅当 state 中已有 doc_text(极少数上游预置场景)时原样保留。
    if not str(state.get("doc_text", "")).strip():
        print("  文档路径未解析出非空文本(文件空/损坏/不支持); 交由 doc_empty_guard 拦截")
    return state


# 模块自测入口: 直接运行本文件时执行
if __name__ == "__main__":
    # 构造测试状态: 不提供 uploaded_doc_path, 仅提供 input(模拟用户粘贴文本)
    s = AgentState(input="甲方A公司向乙方B公司采购电脑100台，单价5000元，总价50万元")
    result = doc_extract_node(s)
    print(f"\ndoc_text 前 100 字: {result.get('doc_text', '')[:100]}")
    structured = result.get("doc_structured_json", {})
    print(f"parse_engine: {structured.get('metadata', {}).get('parse_engine', 'N/A')}")
