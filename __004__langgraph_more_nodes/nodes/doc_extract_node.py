"""N2 文档提取节点: 读取上传文档(txt/md/docx), 提取纯文本"""
# 📜 代码文字逻辑解析
# 本文件是 AI 法律助理(LangGraph 多智能体系统)中的"文档提取节点", 对应业务流程的 N2 环节。
# 其核心职责是: 将用户上传的合同/法规文档(.txt/.md/.docx)解析为纯文本, 写入 state["doc_text"],
# 供后续节点(合同分类、条款切分、AI 审核、合规审查等)消费。
# 实现亮点在于: 完全不依赖第三方库 python-docx, 而是直接将 .docx 视为 zip 包,
# 解析内部的 word/document.xml 提取段落文本, 这样减少了依赖、提升了部署兼容性。
# 节点设计了多级回退策略: (1) 优先读取 uploaded_doc_path 指定的文件;
# (2) 若文件不存在或解析后为空, 则将 state["input"](用户输入文本)直接作为文档内容;
# (3) 这保证了即使用户未上传文件而是直接粘贴合同文本, 流程也能继续运转。
# 文件顶部定义的 W_NS 和 W 是 WordprocessingML 命名空间的常量, 用于 XML 元素标签的拼接与查找。


# 导入 os 模块, 用于路径操作(os.path.exists / os.path.splitext 等)
# os 模块是 Python 标准库, 提供与操作系统交互的跨平台接口
import os

# 导入 zipfile 模块, 用于读取 .docx 文件(本质是 ZIP 压缩包)
# .docx 格式是 OOXML 标准, 内部是若干 XML 文件打包成的 zip, 这里用 zipfile 直接读取
import zipfile

# 导入 xml.etree.ElementTree, 用于解析 docx 内部的 XML 文档
# 别名为 ET 是社区惯例, 缩短调用路径
from xml.etree import ElementTree as ET

# 从同包导入 AgentState 类型, 作为节点函数的类型注解
# AgentState 是 LangGraph 共享状态字典, 本节点会读取 uploaded_doc_path/input, 写入 doc_text
from __004__langgraph_more_nodes.agent_state import AgentState

# 定义 WordprocessingML 的官方命名空间字符串
# OOXML 规范规定 Word 文档 XML 元素均位于该命名空间下, 如 <w:p>(段落)/<w:t>(文本片段)
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

# 构造 ElementTree 使用的命名空间前缀形式 "{namespace}tag"
# ET 模块查找带命名空间的标签时需要这种特殊格式, 例如 "{http://...}p" 表示 <w:p>
# 后续代码通过 f"{W}p" 即可生成完整的标签字符串
W = f"{{{W_NS}}}"


def _docx_to_text(path: str) -> str:
    """
    内部辅助函数: 从 .docx 文件中提取纯文本(不依赖 python-docx 第三方库)。

    作用:
        将 .docx 视为 ZIP 压缩包, 读取其中的 word/document.xml 文件,
        解析 XML 树, 遍历所有段落(<w:p>)元素, 拼接其内部的文本片段(<w:t>)内容,
        最终返回以换行符连接的多行纯文本。这是项目自行实现的轻量级 docx 解析器。

    参数:
        path (str): .docx 文件的绝对或相对路径。

    返回值:
        str: 提取出的纯文本(各段落以 "\n" 分隔); 若读取或解析失败则返回空字符串 ""。

    可迁移性说明:
        本函数可在任何需要轻量级解析 .docx 的场景复用, 无需安装 python-docx。
        若需扩展支持表格(<w:tbl>)、页眉页脚、批注等, 可在 body.iter() 循环中增加对应标签处理。
        注意: 本实现不保留任何格式信息(加粗/字体/字号), 仅提取文本流。
    """
    # 第一阶段: 用 zipfile 打开 docx 并读取内部 word/document.xml 的字节流
    try:
        # 以只读模式打开 docx 文件(zipfile.ZipFile 是上下文管理器, 用 with 自动关闭)
        with zipfile.ZipFile(path, "r") as z:
            # 检查 zip 包内是否存在 word/document.xml(这是 docx 的主文档部分)
            # 若不存在(可能是损坏的 docx 或非 docx 文件), 返回空字符串
            if "word/document.xml" not in z.namelist():
                return ""
            # 读取 word/document.xml 的原始字节数据
            # z.read 返回 bytes, 后续由 ET.fromstring 解析
            xml_bytes = z.read("word/document.xml")
    # 捕获 zipfile 相关异常(文件不存在、损坏、权限不足等)
    except Exception as e:
        # 打印错误日志, 包含异常信息, 便于排查
        print(f"❌ 读取docx失败: {e}")
        # 返回空字符串, 调用方会进入回退逻辑(用 input 作为文档内容)
        return ""

    # 第二阶段: 解析 XML 字节流, 提取段落文本
    # lines 列表用于收集每个段落的文本行, 最后用 "\n" 拼接
    lines = []
    try:
        # ET.fromstring 将 XML 字节流解析为 ElementTree 元素树, 返回根元素 <w:document>
        root = ET.fromstring(xml_bytes)

        # 在根元素下查找 <w:body> 子元素, 即文档正文主体
        # 若找不到 body(异常文档), 直接返回空字符串
        body = root.find(f"{W}body")
        if body is None:
            return ""

        # 遍历 body 子树中的所有元素(深度优先), 逐个检查标签名
        # body.iter() 返回一个迭代器, 包含 body 自身及其所有后代元素
        for elem in body.iter():
            # 若当前元素是段落标签 <w:p>(paragraph), 则提取其内部所有文本片段
            if elem.tag == f"{W}p":
                # 遍历段落内的所有 <w:t>(text) 元素, 收集其 .text 属性
                # 过滤掉 text 为 None 或空字符串的 <w:t>(如空 run、分隔符等)
                parts = [t.text for t in elem.iter(f"{W}t") if t.text]
                # 将段落内的所有文本片段拼接成一行, 并去除首尾空白
                line = "".join(parts).strip()
                # 仅保留非空行(避免空段落污染输出)
                if line:
                    lines.append(line)
    # 捕获 XML 解析过程中的异常(格式错误、编码问题等)
    except Exception as e:
        # 打印错误日志(不抛出异常, 保证主流程继续)
        print(f"❌ 解析XML失败: {e}")

    # 将所有段落文本以换行符连接, 返回最终纯文本
    # 若 lines 为空(无内容或解析失败), 则返回空字符串
    return "\n".join(lines)


def doc_extract_node(state: AgentState):
    """
    文档提取节点函数: 从上传文档或用户输入中提取合同/法规文本, 写入 state["doc_text"]。

    作用:
        作为合同审核/合规审查链路的第二步(在意图路由之后), 将用户提供的文档材料标准化为纯文本。
        优先解析 uploaded_doc_path 指向的本地文件(.txt/.md/.docx), 若文件不可用或解析为空,
        则回退使用 state["input"](用户直接粘贴的文本)作为文档内容, 保证流程健壮性。

    参数:
        state (AgentState): LangGraph 共享状态字典。读取字段:
                            - uploaded_doc_path (str, 可选): 上传文档路径
                            - input (str, 可选): 用户原始输入文本(回退方案)
                            写入字段:
                            - doc_text (str): 提取的文档纯文本

    返回值:
        AgentState: 更新后的状态字典, 必含 "doc_text" 字段。

    可迁移性说明:
        本节点的"多源回退"策略(文件优先 → 输入兜底)适用于任何"文档材料采集"场景。
        若需支持更多格式(如 .pdf/.html/.eml), 只需在文件类型分支中新增对应解析逻辑即可。
        _docx_to_text 辅助函数可直接复用, 无外部依赖是其优势。
    """
    # 打印节点开始日志
    print("开始文档提取")

    # 从状态字典中取出上传文档路径, 若不存在则为空字符串
    doc_path = state.get("uploaded_doc_path", "")

    # 第一优先级: 若提供了文档路径且文件确实存在, 则按扩展名解析
    if doc_path and os.path.exists(doc_path):
        # 使用 os.path.splitext 分离文件名与扩展名, 取扩展名并转小写
        # 例: "合同.docx" -> (".docx", ""), 取 [1] 即 ".docx", lower() 保证大小写不敏感
        ext = os.path.splitext(doc_path)[1].lower()

        # 根据扩展名分发到不同的解析逻辑
        if ext == ".docx":
            # .docx 调用自定义解析器, 不依赖 python-docx
            text = _docx_to_text(doc_path)
        elif ext in (".txt", ".md"):
            # .txt / .md 是纯文本格式, 直接读取文件内容
            # encoding="utf-8" 显式指定编码, 避免在 Windows 下默认 GBK 编码导致乱码
            with open(doc_path, "r", encoding="utf-8") as f:
                text = f.read()
        else:
            # 不支持的扩展名, 文本置空, 后续会进入回退逻辑
            text = ""

        # 若文件解析得到了非空文本(去除空白后), 写入状态并返回
        # strip() 后非空才视为有效, 避免只有空格/换行的文档污染下游节点
        if text.strip():
            state["doc_text"] = text
            # 打印提取成功的日志, 显示字符数(便于核对)
            print(f"完成文档提取: {len(text)} 字符")
            return state

    # 第二优先级(回退方案): 文件不存在或解析为空, 将 state["input"] 作为文档内容
    # 这处理了用户直接在前端粘贴合同文本(未上传文件)的场景
    user_input = state.get("input", "")

    # 根据输入长度给出不同的提示日志(仅展示差异, 实际逻辑一致)
    if len(user_input) > 50:
        # 长文本输入, 视为完整的文档内容
        state["doc_text"] = user_input
        print(f"使用input作为文档: {len(user_input)} 字符")
    else:
        # 短文本输入(可能只是问题而非合同), 也照常写入, 由后续节点自行判断
        state["doc_text"] = user_input
        print("文档为空, 使用input")

    # 返回更新后的状态字典
    return state


# 模块自测入口: 直接运行本文件时执行, 验证 doc_extract_node 的回退逻辑
if __name__ == "__main__":
    # 构造测试状态: 不提供 uploaded_doc_path, 仅提供 input(模拟用户粘贴文本)
    s = AgentState(input="甲方A公司向乙方B公司采购电脑100台")
    # 调用节点, 打印提取的 doc_text 前 50 个字符(切片预览, 避免输出过长)
    print(doc_extract_node(s).get("doc_text", "")[:50])
