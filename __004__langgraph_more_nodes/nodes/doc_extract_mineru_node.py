"""
N2-MinerU 文档解析节点 (Phase 1)
================================

【设计理念】利用 MinerU 将一份合同拆解为文字、表格、图片、印章、批注等
所有可识别元素, 每个元素都贴上精确的坐标标签, 然后打包成一个标准 JSON.

后续各智能体可根据不同的任务类型, 从标准 JSON 中按规则 + LLM 混合方式
提取所需字段:
  - 规则层: 遍历 JSON 中的文字元素, 用正则提取金额/日期/百分比/身份证号等
  - LLM 层: 将规则未能提取的字段连同上下文送入 LLM, 输出结构化结果
  - 融合层: 合并规则结果和 LLM 结果, 校验冲突(规则为准)

【Phase 1 范围】
  - 创建 MinerU 解析节点, 支持 PDF/DOCX 输入
  - 输出标准 JSON (doc_structured_json) + 纯文本 (doc_text, 向后兼容)
  - MinerU 不可用时自动降级为原有 doc_extract_node 逻辑
  - 构造含表格等多模态测试数据

【MinerU 输出 JSON 标准格式】
  {
    "metadata": {
      "file_name": "xxx.pdf",
      "file_type": "pdf",
      "page_count": 3,
      "parse_engine": "MinerU" | "fallback",
      "parse_time": "2026-08-14T10:00:00"
    },
    "pages": [
      {
        "page_idx": 0,
        "blocks": [
          {"type": "text", "content": "甲方：XXX公司", "bbox": [x1,y1,x2,y2]},
          {"type": "table", "content": [["列1","列2"],...], "bbox": [...]},
          {"type": "image", "content": "path/to/img", "bbox": [...]},
          {"type": "title", "content": "采购合同", "bbox": [...], "level": 1}
        ]
      }
    ]
  }
"""
# 📜 代码文字逻辑解析
# 本文件是法智引擎文档解析链路的 MinerU 增强版本(Phase 1).
# 它在原有 doc_extract_node 的基础上, 引入 MinerU 多模态解析能力:
# 1) 优先尝试调用 MinerU(magic_pdf) 解析 PDF/DOCX, 获取结构化 JSON
# 2) MinerU 不可用时, 降级为原有 doc_extract_node 逻辑(纯文本提取)
# 3) 无论哪种方式, 都同时输出 doc_text(纯文本, 向后兼容) 和
#    doc_structured_json(结构化 JSON, 供后续规则层+LLM层提取字段)

import os
import json
from datetime import datetime
from __004__langgraph_more_nodes.agent_state import AgentState


# ============================================================
# MinerU 可用性检测 (延迟导入, 不影响模块加载)
# ============================================================
def _check_mineru_available():
    """
    检测 MinerU (magic_pdf) 是否已安装且可用.

    返回值:
        bool: True 表示 MinerU 可用, False 表示需降级
    """
    try:
        # 尝试导入 MinerU 核心模块
        from magic_pdf.pipe.UNIPipe import UNIPipe
        from magic_pdf.rw.DiskReaderWriter import DiskReaderWriter
        return True
    except ImportError:
        return False
    except Exception:
        return False


def _parse_with_mineru(file_path: str) -> dict:
    """
    使用 MinerU 解析 PDF/DOCX 文件, 返回标准结构化 JSON.

    参数:
        file_path (str): PDF 或 DOCX 文件路径

    返回值:
        dict: 标准 MinerU 输出 JSON (含 metadata + pages + blocks)
              解析失败时返回空 dict
    """
    try:
        from magic_pdf.pipe.UNIPipe import UNIPipe
        from magic_pdf.rw.DiskReaderWriter import DiskReaderWriter
        import magic_pdf.model as model_config

        # 读取文件字节
        with open(file_path, "rb") as f:
            file_bytes = f.read()

        # 创建 MinerU 读取器
        image_writer = DiskReaderWriter(os.path.dirname(file_path))
        pipe = UNIPipe(file_bytes, {"_pdf_type": "", "model_list": []}, image_writer)

        # 执行解析
        pipe.pipe_classify()
        pipe.pipe_analyze()
        pipe.pipe_parse()

        # 获取解析结果
        result = pipe.pipe_mk_uni_format(image_writer, drop_mode="none")

        # 转换为标准 JSON 格式
        structured_json = _normalize_mineru_output(result, file_path)
        return structured_json

    except Exception as e:
        print(f"  ⚠️ MinerU解析失败: {e}")
        return {}


def _normalize_mineru_output(raw_result: dict, file_path: str) -> dict:
    """
    将 MinerU 原始输出转换为项目标准 JSON 格式.

    参数:
        raw_result (dict): MinerU 原始解析结果
        file_path (str): 原始文件路径(用于 metadata)

    返回值:
        dict: 标准格式 JSON, 含 metadata/pages/blocks
    """
    # 构造标准 JSON 结构
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

    # 遍历 MinerU 返回的页面数据, 转换为标准 block 格式
    if raw_result and isinstance(raw_result, list):
        for page_idx, page_data in enumerate(raw_result):
            page_blocks = []
            # 提取页面中的 block
            blocks = page_data.get("blocks", []) if isinstance(page_data, dict) else []
            for block in blocks:
                block_type = block.get("type", "text")
                block_content = block.get("content", "")
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

    参数:
        structured_json (dict): 标准 MinerU JSON

    返回值:
        str: 拼接后的纯文本
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
                # 文本/标题内容: 直接拼接
                lines.append(content)

    return "\n".join(lines)


def _build_fallback_structured_json(text: str, file_path: str) -> dict:
    """
    降级方案: 当 MinerU 不可用时, 将纯文本包装为标准 JSON 格式.

    作用:
        将 doc_extract_node 提取的纯文本"模拟"为 MinerU 输出格式,
        让下游节点可以统一使用 doc_structured_json 字段, 无需关心
        实际是 MinerU 解析还是降级纯文本.

    参数:
        text (str): 纯文本内容
        file_path (str): 文件路径(用于 metadata)

    返回值:
        dict: 包装后的标准 JSON (parse_engine="fallback")
    """
    # 将纯文本按行分割, 每行作为一个 text block
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


def doc_extract_mineru_node(state: AgentState):
    """
    MinerU 文档解析节点函数 (Phase 1).

    作用:
        作为文档提取链路的增强版本, 优先使用 MinerU 进行多模态解析,
        输出结构化 JSON (含文字/表格/图片/坐标标签). MinerU 不可用时
        自动降级为原有纯文本提取逻辑, 并将纯文本包装为标准 JSON 格式.

    参数:
        state (AgentState): LangGraph 共享状态字典。读取字段:
                            - uploaded_doc_path (str, 可选): 上传文档路径
                            - input (str, 可选): 用户原始输入文本(回退方案)
                            写入字段:
                            - doc_text (str): 纯文本(向后兼容)
                            - doc_structured_json (dict): 结构化 JSON

    返回值:
        AgentState: 更新后的状态字典, 必含 doc_text 和 doc_structured_json 字段.
    """
    print("开始文档解析 (MinerU Phase 1)")

    # 从状态字典中取出上传文档路径
    doc_path = state.get("uploaded_doc_path", "")

    # 检测 MinerU 是否可用
    mineru_available = _check_mineru_available()
    if mineru_available:
        print("  MinerU 可用, 使用多模态解析")
    else:
        print("  MinerU 不可用, 降级为纯文本解析")

    # ============== 优先级 1: 有文件路径 + MinerU 可用 ==============
    if doc_path and os.path.exists(doc_path) and mineru_available:
        ext = os.path.splitext(doc_path)[1].lower()

        # MinerU 原生支持 PDF; DOCX 需先转换为 PDF
        if ext == ".pdf":
            structured_json = _parse_with_mineru(doc_path)
        elif ext == ".docx":
            # DOCX → PDF 转换 (需要 libreoffice 或 docx2pdf)
            print("  DOCX 文件, 尝试转换为 PDF 后解析...")
            pdf_path = _try_convert_docx_to_pdf(doc_path)
            if pdf_path and os.path.exists(pdf_path):
                structured_json = _parse_with_mineru(pdf_path)
            else:
                # 转换失败, 降级为原有逻辑
                print("  DOCX→PDF 转换失败, 降级为纯文本解析")
                structured_json = {}
        else:
            structured_json = {}

        # 若 MinerU 解析成功, 提取纯文本并返回
        if structured_json and structured_json.get("pages"):
            doc_text = _structured_json_to_text(structured_json)
            if doc_text.strip():
                state["doc_text"] = doc_text
                state["doc_structured_json"] = structured_json
                print(f"  MinerU 解析成功: {len(structured_json['pages'])} 页, {len(doc_text)} 字符")
                return state

    # ============== 优先级 2: 有文件路径, 降级为原有逻辑 ==============
    if doc_path and os.path.exists(doc_path):
        # 复用原有 doc_extract_node 的文件解析逻辑
        from __004__langgraph_more_nodes.nodes.doc_extract_node import _docx_to_text
        ext = os.path.splitext(doc_path)[1].lower()

        if ext == ".docx":
            text = _docx_to_text(doc_path)
        elif ext in (".txt", ".md"):
            with open(doc_path, "r", encoding="utf-8") as f:
                text = f.read()
        else:
            text = ""

        if text.strip():
            state["doc_text"] = text
            # 将纯文本包装为标准 JSON 格式
            state["doc_structured_json"] = _build_fallback_structured_json(text, doc_path)
            print(f"  降级解析成功(纯文本): {len(text)} 字符")
            return state

    # ============== 优先级 3: 无文件, 使用 input 作为文档内容 ==============
    user_input = state.get("input", "")
    state["doc_text"] = user_input
    state["doc_structured_json"] = _build_fallback_structured_json(user_input, "")
    print(f"  使用 input 作为文档: {len(user_input)} 字符")

    return state


def _try_convert_docx_to_pdf(docx_path: str) -> str:
    """
    尝试将 DOCX 转换为 PDF (使用 docx2pdf 或 libreoffice).

    参数:
        docx_path (str): DOCX 文件路径

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


# 模块自测入口
if __name__ == "__main__":
    # 测试: 使用 input 文本模拟文档
    s = AgentState(input="甲方A公司向乙方B公司采购电脑100台，单价5000元，总价50万元")
    result = doc_extract_mineru_node(s)

    print(f"\ndoc_text: {result.get('doc_text', '')[:100]}")
    structured = result.get("doc_structured_json", {})
    print(f"parse_engine: {structured.get('metadata', {}).get('parse_engine', 'N/A')}")
    print(f"page_count: {structured.get('metadata', {}).get('page_count', 0)}")
    blocks = structured.get("pages", [{}])[0].get("blocks", [])
    print(f"blocks count: {len(blocks)}")
    for b in blocks[:3]:
        print(f"  [{b.get('type')}] {b.get('content', '')[:50]}")
