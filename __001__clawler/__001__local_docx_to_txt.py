"""
本地 data/raw 目录下法律 docx 批量转 txt
不依赖 python-docx，直接解析 docx (zip) 内的 word/document.xml
输出: __001__clawler/法律法规/<同名>.txt
支持断点续跑: 已存在的 txt 跳过
"""
# 📜 代码文字逻辑解析
# 本文件实现本地 .docx 法律文档到纯文本 .txt 的批量转换，是爬虫模块的离线数据预处理入口。
# 由于 .docx 本质是一个 ZIP 压缩包，其正文存储在内部的 word/document.xml 中，因此本文件
# 不依赖 python-docx 第三方库，而是直接用标准库 zipfile 读取压缩包，再用 xml.etree.ElementTree
# 解析 XML。docx_to_text 函数遍历 <w:body> 下所有元素，对 <w:p> 段落拼接其内所有 <w:t> 文本
# 片段，对 <w:tbl> 表格插入 "[表格]" 标记，最终以换行符拼接为完整文本。batch_convert 函数负责
# 批量遍历输入目录，对每个 docx 调用 docx_to_text 转换并写出 txt，已存在且非空的 txt 会被跳过，
# 实现断点续跑。主程序入口固定读取项目根目录下的 data/raw，输出到 __001__clawler/法律法规/，
# 供后续的知识图谱抽取脚本消费。
import os  # 导入 os 模块，用于路径拼接、目录创建与文件存在性判断
import re  # 导入 re 模块，正则表达式支持（本文件中实际未直接使用，保留以备扩展）
import zipfile  # 导入 zipfile 模块，用于读取 docx 这一 ZIP 压缩包格式
from xml.etree import ElementTree as ET  # 导入 ElementTree，用于解析 docx 内部的 XML 文档

from common.path_utils import get_file_path, root_dir  # 从公共模块导入路径工具：get_file_path 用于按相对路径定位文件，root_dir 为项目根目录

# Word 命名空间
# 定义 WordprocessingML 的 XML 命名空间 URI，docx 内部 XML 大量使用该命名空间
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
# 构造 ElementTree 使用的命名空间前缀格式：{URI}，用于匹配带命名空间的标签名
W = f"{{{W_NS}}}"


def docx_to_text(docx_path: str) -> str:
    """
    从 .docx 中提取纯文本

    【作用】
        读取 .docx 压缩包内的 word/document.xml，解析其中的段落 <w:p> 与表格 <w:tbl>，
        将每个段落内所有 <w:t> 文本片段拼接为一行，表格则以 "[表格]" 标记占位，
        最终以换行符拼接所有行返回。任何读取或解析错误均打印日志并返回空字符串。

    【参数】
        docx_path (str): docx 文件的绝对或相对路径

    【返回值】
        str: 提取的纯文本，段落以换行分隔；失败时返回空字符串 ""

    【可迁移性说明】
        该函数仅依赖 Python 标准库（zipfile、xml.etree），可在任何无 python-docx 的环境运行，
        适合迁移到任何需要轻量解析 docx 文本的场景。需注意：该实现不保留表格内文本细节、
        不处理页眉页脚与脚注，若需更完整提取，建议改用 python-docx 或 docx2txt。
    """
    try:
        # 以只读模式打开 docx 压缩包
        with zipfile.ZipFile(docx_path, "r") as z:
            # docx 主文档
            # docx 包内正文固定存放在 word/document.xml
            xml_name = "word/document.xml"
            # 检查压缩包内是否包含该文件，部分损坏或非标准 docx 可能缺失
            if xml_name not in z.namelist():
                # 缺失则打印警告并返回空字符串
                print(f"⚠️  {docx_path} 内未找到 {xml_name}")
                return ""
            # 读取 document.xml 的原始字节流
            xml_bytes = z.read(xml_name)
    except Exception as e:
        # 捕获 zipfile 打开/读取过程中的任何异常（如文件不存在、压缩包损坏）
        print(f"❌ 读取 docx 失败 {docx_path}: {e}")
        return ""

    # 解析 XML，按段落提取文本
    # 初始化行列表，用于收集每个段落/表格的文本
    lines = []
    try:
        # 从字节流解析 XML，得到根元素 <w:document>
        root = ET.fromstring(xml_bytes)
        # 查找 <w:body> 正文节点
        body = root.find(f"{W}body")
        # 若无 body 节点，说明文档结构异常，返回空字符串
        if body is None:
            return ""
        # 遍历 body 下所有后代元素（递归），逐一判断标签类型
        for elem in body.iter():
            # 获取当前元素的标签名（含命名空间前缀）
            tag = elem.tag
            # 若为段落标签 <w:p>
            if tag == f"{W}p":
                # 段落：拼接所有 w:t
                # 初始化文本片段列表
                text_parts = []
                # 遍历段落内所有 <w:t> 文本节点（一个段落可能被拆成多个 run）
                for t in elem.iter(f"{W}t"):
                    # 仅当 t.text 非空时才收集
                    if t.text:
                        text_parts.append(t.text)
                # 将所有片段拼接为一行，并去除首尾空白
                line = "".join(text_parts).strip()
                # 非空行才加入结果列表
                if line:
                    lines.append(line)
            # 若为表格标签 <w:tbl>
            elif tag == f"{W}tbl":
                # 表格单独标记
                # 在文本中以 "[表格]" 占位，便于后续识别（本实现不提取表格内文字细节）
                lines.append("\n[表格]")
    except Exception as e:
        # 捕获 XML 解析过程中的任何异常（如 XML 格式错误）
        print(f"❌ 解析 XML 失败 {docx_path}: {e}")
        return ""

    # 以换行符拼接所有行，返回完整文本
    return "\n".join(lines)


def batch_convert(input_dir: str, output_dir: str):
    """
    批量把 input_dir 下的 .docx 转成 .txt 写到 output_dir

    【作用】
        遍历 input_dir 下所有 .docx 文件，逐个调用 docx_to_text 转换为纯文本，
        写入 output_dir 下同名 .txt 文件。已存在且非空的目标 txt 会被跳过，
        实现断点续跑。转换过程实时打印进度，最终输出成功数量统计。

    【参数】
        input_dir (str): 输入目录，存放 .docx 文件
        output_dir (str): 输出目录，存放转换后的 .txt 文件；不存在时自动创建

    【返回值】
        int: 成功转换的文件数量（含跳过的已存在文件）

    【可迁移性说明】
        该函数是通用的批量转换调度器，与法律业务无关。迁移到其他 docx 转 txt 场景时，
        只需调整输入输出目录即可。断点续跑逻辑基于"目标文件存在且非空即跳过"，简单可靠，
        适合长时间批量任务中断后恢复。
    """
    # 创建输出目录，exist_ok=True 表示目录已存在时不报错
    os.makedirs(output_dir, exist_ok=True)
    # 列出输入目录下所有文件，过滤出 .docx 后缀（大小写不敏感）
    files = [f for f in os.listdir(input_dir) if f.lower().endswith(".docx")]
    # 打印发现的 docx 文件数量与输入目录
    print(f"📂 发现 {len(files)} 个 docx 文件：{input_dir}")
    # 初始化成功计数器
    success = 0
    # 遍历每个 docx 文件
    for fname in files:
        # 去除扩展名，得到文件主名
        base = os.path.splitext(fname)[0]
        # 拼接输出 txt 的完整路径
        out_path = os.path.join(output_dir, base + ".txt")
        # 断点续跑
        # 若目标 txt 已存在且大小大于 0，视为已转换完成，跳过
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            # 打印跳过提示
            print(f"⏭️  跳过(已存在): {base}.txt")
            # 计入成功数（已存在也算成功）
            success += 1
            # 继续下一个文件
            continue
        # 拼接源 docx 的完整路径
        src_path = os.path.join(input_dir, fname)
        # 打印当前转换进度
        print(f"🔄 转换: {fname} -> {base}.txt")
        # 调用 docx_to_text 提取文本
        text = docx_to_text(src_path)
        # 若提取到非空文本
        if text:
            # 以 UTF-8 编码写入 txt 文件
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(text)
            # 打印写入的字符数
            print(f"   ✅ 写入 {len(text)} 字符")
            # 成功计数加 1
            success += 1
        else:
            # 文本为空时跳过写入，避免产生空文件
            print(f"   ⚠️  内容为空，跳过")
    # 打印批量转换完成的统计信息
    print(f"\n🎉 完成：{success}/{len(files)} 个文件成功转换")
    # 返回成功转换的文件数
    return success


if __name__ == "__main__":
    # 输入：优先 data/sample，若该目录无 .docx 则回退到旧目录 data/raw（兼容过渡期）
    in_dir = os.path.join(root_dir, "data", "sample")
    fallback_dir = os.path.join(root_dir, "data", "raw")
    if not any(f.lower().endswith(".docx") for f in (os.listdir(in_dir) if os.path.isdir(in_dir) else [])):
        if os.path.isdir(fallback_dir) and any(f.lower().endswith(".docx") for f in os.listdir(fallback_dir)):
            in_dir = fallback_dir
    # 输出：__001__clawler/法律法规/
    # 拼接输出目录：项目根目录/__001__clawler/法律法规
    out_dir = os.path.join(root_dir, "__001__clawler", "法律法规")
    # 调用批量转换函数执行转换
    batch_convert(in_dir, out_dir)
