# -*- coding: utf-8 -*-
"""
从 __001__clawler/法律法规/*.txt 批量抽取法律实体和关系
输出:
  - extract_law_data.json        (供 graph_importer 导入 Neo4j)
  - extract_law_finetune_data.json (微调数据, 保留输入输出对)

支持断点续跑: 已处理的文件记录在 extract_law_progress.json, 跳过已处理项

适配新数据格式:
  - 文件名无日期后缀 (如 "中华人民共和国民法典.txt" 而非 "xxx_20200528.txt")
  - 文件头部有注释行 (# 法律名 / # 来源 / # 生成时间), 需过滤
"""
# 📜 代码文字逻辑解析
# 本文件是法律知识图谱抽取的批量执行入口，负责将爬虫模块产出的法律法规 txt 文件
# 批量送入 LLM 抽取实体与关系三元组。整体流程为：读取 __001__clawler/法律法规/ 目录下
# 所有 txt → 过滤头部注释行(# 法律名/来源/时间) → 按"编/章/节/段落/条文"层级切分为
# 6000字符以内的分块 → 对每个分块调用 extract_graph_data(LLM 抽取) → 收集实体与关系 →
# 为 Article 实体补 statute 属性并建立 CONTAINS_ARTICLE 关系 → 实体按 name+type 去重 →
# 写入 extract_law_data.json(图谱数据) 和 extract_law_finetune_data.json(微调样本)。
# 支持断点续跑：每完成一个文件即将结果、微调数据、进度记录三份 JSON 落盘，中断后重跑
# 会跳过已处理文件。适配新旧文件名格式(有/无日期后缀)，从文件名提取法规名和版本日期。
import os  # 导入 os 模块，用于路径拼接、目录判断、文件存在性检查
import sys  # 导入 sys 模块，用于 stdout 编码重配
import json  # 导入 json 模块，用于读写 JSON 格式的结果文件与进度文件
import re  # 导入 re 模块，正则表达式支持，用于法律文本切分与文件名解析

# Windows 控制台 UTF-8
# 检测当前 stdout 编码是否非 UTF-8，若否则重配为 UTF-8，避免 Windows GBK 环境下中文输出乱码
if sys.stdout.encoding.lower().replace("-", "") != "utf8":  # 将编码名转小写并去掉连字符，统一比较
    try:  # 尝试重配 stdout 编码
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # 重配为 UTF-8，无法编码的字符用 ? 替换
    except Exception:  # 某些环境不支持 reconfigure，静默忽略
        pass  # 无操作，继续执行

from tqdm import tqdm  # 导入 tqdm 进度条库，用于显示批量抽取进度

from common.path_utils import get_file_path, root_dir  # 导入公共路径工具：get_file_path 按相对路径定位文件，root_dir 为项目根目录
from __002__extract_information.__000__extract_graph_data_utils import extract_graph_data  # 导入知识图谱抽取核心函数，负责调用 LLM 抽取实体与关系


def _strip_header_lines(text: str) -> str:
    """过滤掉爬虫生成的头部注释行(# 法律名 / # 来源 / # 生成时间)

    【作用】
        爬虫模块(__002__crawl_law_database.py)在写 txt 文件时会添加以 # 开头的头部
        元信息行(法律名、来源、生成时间)，这些行不应送入 LLM 抽取。本函数逐行过滤
        所有以 # 开头的行，返回剩余正文。

    【参数】
        text (str): 原始法律文本，可能包含头部注释行

    【返回值】
        str: 过滤注释行后的正文，去除首尾空白

    【可迁移性说明】
        该函数为通用文本预处理工具，可迁移到任何需要过滤 # 注释行的场景。
        过滤逻辑基于行首 # 检测，简单可靠。若爬虫头部格式变更(如改用其他注释符)，
        需同步修改此函数。
    """
    lines = text.split("\n")  # 按换行符分割文本为行列表
    cleaned = []  # 初始化过滤后的行列表
    for line in lines:  # 遍历每一行
        stripped = line.strip()  # 去除当前行首尾空白
        # 过滤所有以 # 开头的注释行(法律名/来源/生成时间等元信息)
        if stripped.startswith("#"):  # 若该行以 # 开头，视为注释行
            continue  # 跳过，不加入 cleaned
        cleaned.append(line)  # 非注释行加入结果列表（保留原始行，含缩进）
    return "\n".join(cleaned).strip()  # 以换行符拼接并去除首尾空白


def split_law_text(text: str, max_chunk: int = 6000) -> list:
    """
    按章节/编/分编切分法律文本, 控制每块不超过 max_chars
    优先按"第X编""第X章""第X节"切, 切不动则按段落切, 最后兜底按字数切

    【作用】
        将完整法律文本按法律层级结构(编→章→节→段落→条文)逐级尝试切分，控制每个
        分块不超过 max_chunk 字符，避免超出 LLM 上下文窗口。先尝试按"第X编"切分，
        若不可行则降级到"第X章""第X节""双换行段落"；对仍超长的块再按"第X条"条文
        切分并累加拼接至接近 max_chunk。

    【参数】
        text (str): 完整法律文本
        max_chunk (int): 单个分块最大字符数，默认 6000

    【返回值】
        list: 分块文本列表，每项为不超过 max_chunk 字符的文本块

    【可迁移性说明】
        该函数专为中文法律文本设计，正则匹配"第X编/章/节/条"使用中文数字字符集
        (一二三四五六七八九十百千零)。可迁移到任何层级结构相似的中文法规/规章切分
        场景。max_chunk 阈值需根据下游 LLM 上下文窗口大小调整。
    """
    # 先按"编"切
    # 尝试按"第X编"切分（编是法律最高层级，如民法典的"第一编 总则"）
    parts = re.split(r"\n第[一二三四五六七八九十百千]+编\s", text)  # 正则匹配换行后的"第X编 "作为切分点
    if len(parts) <= 1:  # 若未切分成功（只有1块说明文本中无"第X编"）
        # 降级到按"第X章"切分
        parts = re.split(r"\n第[一二三四五六七八九十百千]+章\s", text)  # 正则匹配换行后的"第X章 "作为切分点
    if len(parts) <= 1:  # 若仍未切分成功
        # 降级到按"第X节"切分
        parts = re.split(r"\n第[一二三四五六七八九十百千]+节\s", text)  # 正则匹配换行后的"第X节 "作为切分点
    if len(parts) <= 1:  # 若仍未切分成功
        # 兜底按双换行段落切分
        parts = text.split("\n\n")  # 按空行(双换行)分割为段落

    # 对超长块进一步切分
    final = []  # 初始化最终分块列表
    for p in parts:  # 遍历初步切分的每个块
        p = p.strip()  # 去除块首尾空白
        if not p:  # 空块跳过
            continue  # 继续下一个块
        if len(p) <= max_chunk:  # 块长度未超阈值
            final.append(p)  # 直接加入最终列表
        else:  # 块超长，需进一步按条文切分
            # 按条文切
            # 按"第X条"切分，正则中用捕获组保留分隔符（条文编号行）
            articles = re.split(r"\n(第[一二三四五六七八九十百千零\d]+条\s)", p)  # 切分后列表交替为[正文, 条号, 正文, 条号, ...]
            buf = ""  # 初始化缓冲区，用于累加条文至接近 max_chunk
            for i in range(0, len(articles), 2):  # 步长2遍历，i 指向正文部分
                # 拼接正文与其后的条号（若存在）
                seg = (articles[i] + (articles[i + 1] if i + 1 < len(articles) else "")).strip()  # 当前段 = 正文 + 条号
                if not seg:  # 空段跳过
                    continue  # 继续下一段
                if len(buf) + len(seg) > max_chunk and buf:  # 若加入当前段会超阈值且缓冲区已有内容
                    final.append(buf)  # 将当前缓冲区内容作为一个分块加入最终列表
                    buf = seg  # 缓冲区重置为当前段
                else:  # 加入当前段不会超阈值，或缓冲区为空
                    buf += "\n" + seg if buf else seg  # 拼接到缓冲区（首段不加换行符）
            if buf:  # 遍历结束后若缓冲区仍有内容
                final.append(buf)  # 将剩余内容作为最后一个分块加入
    return final  # 返回最终分块列表


def main():
    """
    主函数: 批量抽取法律文本的实体和关系

    【作用】
        读取 __001__clawler/法律法规/ 下所有 txt 文件，逐个过滤头部注释、切分分块、
        调用 LLM 抽取实体与关系。为 Article 实体补充 statute 属性并建立
        CONTAINS_ARTICLE 关系，实体按 name+type 去重。每完成一个文件即落盘三份
        JSON(图谱数据/微调数据/进度记录)，支持断点续跑。最终打印抽取统计。

    【参数】
        无参数

    【返回值】
        None: 无返回值，副作用为写入 JSON 文件

    【可迁移性说明】
        该函数是批量抽取的入口，整体流程(读取→过滤→切分→抽取→去重→落盘)可迁移
        到任何批量文本信息抽取场景。断点续跑机制基于进度文件，简单可靠。微调数据
        格式(input/output/source)适配 LoRA 等指令微调需求。从文件名提取法规名的
        正则兼容新旧格式(有/无日期后缀)。
    """
    input_dir = os.path.join(root_dir, "__001__clawler", "法律法规")  # 拼接输入目录：项目根/__001__clawler/法律法规
    if not os.path.isdir(input_dir):  # 若输入目录不存在
        print(f"[错误] 输入目录不存在: {input_dir}")  # 打印错误信息
        print("请先运行 __001__clawler/__002__crawl_law_database.py")  # 提示先运行爬虫
        return  # 直接返回，不继续执行

    txt_files = sorted([f for f in os.listdir(input_dir) if f.endswith(".txt")])  # 列出并排序所有 txt 文件
    print(f"[信息] 发现 {len(txt_files)} 个法律文本文件")  # 打印发现的文件数量

    # 断点续跑
    # 进度文件路径：记录已处理的文件名列表，中断后重跑时跳过
    progress_path = get_file_path("__002__extract_information/extract_law_progress.json")  # 获取进度文件路径
    processed = set()  # 初始化已处理文件名集合
    if os.path.exists(progress_path):  # 若进度文件存在
        with open(progress_path, "r", encoding="utf-8") as f:  # 以 UTF-8 打开进度文件
            processed = set(json.load(f).get("processed", []))  # 读取已处理文件名列表并转为集合
        print(f"[续跑] 已处理 {len(processed)} 个文件, 将跳过")  # 打印续跑信息

    # 加载已有结果(若有)
    # 图谱数据输出路径：含所有法律的实体与关系，供 graph_importer 导入 Neo4j
    out_path = get_file_path("__002__extract_information/extract_law_data.json")  # 获取图谱数据输出路径
    # 微调数据输出路径：保留 input/output 对，供模型微调使用
    finetune_path = get_file_path("__002__extract_information/extract_law_finetune_data.json")  # 获取微调数据输出路径
    results = []  # 初始化图谱结果列表
    finetune_data = []  # 初始化微调数据列表
    if os.path.exists(out_path):  # 若图谱数据文件已存在（续跑场景）
        with open(out_path, "r", encoding="utf-8") as f:  # 以 UTF-8 打开
            try:  # 捕获 JSON 解析异常
                results = json.load(f).get("results", [])  # 读取已有结果列表
            except Exception:  # JSON 解析失败
                results = []  # 重置为空列表
    if os.path.exists(finetune_path):  # 若微调数据文件已存在（续跑场景）
        with open(finetune_path, "r", encoding="utf-8") as f:  # 以 UTF-8 打开
            try:  # 捕获 JSON 解析异常
                finetune_data = json.load(f)  # 读取已有微调数据
            except Exception:  # JSON 解析失败
                finetune_data = []  # 重置为空列表

    for fname in tqdm(txt_files, desc="抽取法律图谱"):  # 遍历所有 txt 文件，tqdm 显示进度条
        if fname in processed:  # 若该文件已处理过（断点续跑跳过）
            continue  # 跳过该文件
        fpath = os.path.join(input_dir, fname)  # 拼接文件完整路径
        with open(fpath, "r", encoding="utf-8") as f:  # 以 UTF-8 打开文件
            raw_text = f.read()  # 读取全部内容

        if not raw_text.strip():  # 若文件内容为空
            print(f"[警告] 空文件: {fname}")  # 打印警告
            processed.add(fname)  # 标记为已处理（避免重复处理空文件）
            continue  # 跳过该文件

        # [适配新格式] 过滤头部注释行 (# 法律名 / # 来源 / # 生成时间)
        text = _strip_header_lines(raw_text)  # 调用过滤函数去除头部注释行

        if not text.strip():  # 若过滤后为空（整文件都是注释行）
            print(f"[警告] 过滤后为空: {fname}")  # 打印警告
            processed.add(fname)  # 标记为已处理
            continue  # 跳过该文件

        # 切分
        chunks = split_law_text(text)  # 调用切分函数将文本分为多个分块
        print(f"\n[处理] {fname}: {len(chunks)} 个分块, 总 {len(text)} 字符")  # 打印分块信息

        # [适配新格式] 从文件名提取法规名和版本
        # 新格式: "中华人民共和国民法典.txt" (无日期后缀)
        # 旧格式: "中华人民共和国民法典_20200528.txt" (有日期后缀)
        base = os.path.splitext(fname)[0]  # 去除 .txt 扩展名，得到文件主名
        m = re.match(r"(.+)_((\d{8})?)$", base)  # 正则匹配：主名_日期后缀，日期为8位数字
        if m and m.group(3):  # 若匹配成功且有日期后缀（旧格式）
            statute_name = m.group(1)  # 法规名为日期前部分
            version_date = m.group(3)  # 版本日期为8位数字
        else:  # 无日期后缀（新格式）或正则不匹配
            statute_name = base  # 法规名为完整文件主名
            version_date = ""  # 版本日期为空字符串

        # 先把法规本身作为顶层实体
        # 构造法规(Statute)实体，作为该法律的顶层节点
        statute_entity = {
            "name": statute_name,  # 法规名称
            "type": "Statute",  # 实体类型为法规
            "attributes": {  # 法规属性字典
                "version_date": version_date,  # 版本日期
                "authority": "",  # 颁布机关（暂空，待补充）
                "effective_status": "现行有效",  # 默认标记为现行有效
                "scope": "全国",  # 默认适用范围为全国
            }
        }

        all_entities = [statute_entity]  # 初始化实体列表，首项为法规本身
        all_relations = []  # 初始化关系列表

        for i, chunk in enumerate(chunks):  # 遍历每个分块
            print(f"   [分块] {i + 1}/{len(chunks)} ({len(chunk)} 字符)")  # 打印当前分块进度
            extract_dict = extract_graph_data(chunk, fname)  # 调用 LLM 抽取该分块的实体与关系
            ents = extract_dict.get("entities", [])  # 提取实体列表
            rels = extract_dict.get("relations", [])  # 提取关系列表

            # 给 Article 实体补上 statute_name 属性, 并建立 CONTAINS_ARTICLE 关系
            for ent in ents:  # 遍历该分块抽取的所有实体
                if ent.get("type") == "Article":  # 若实体类型为法条
                    ent.setdefault("attributes", {})  # 确保 attributes 字段存在
                    ent["attributes"].setdefault("statute", statute_name)  # 补充 statute 属性（若未设置）
                    # 建立法规 -> 法条 关系
                    all_relations.append({  # 追加 CONTAINS_ARTICLE 关系
                        "subject": statute_name,  # 主体为法规名
                        "subject_type": "Statute",  # 主体类型为法规
                        "relation": "CONTAINS_ARTICLE",  # 关系类型为包含法条
                        "object": ent["name"],  # 客体为法条名
                        "object_type": "Article",  # 客体类型为法条
                    })

            all_entities.extend(ents)  # 将该分块的实体追加到总列表
            all_relations.extend(rels)  # 将该分块的关系追加到总列表

            # 微调数据(输入输出对)
            finetune_data.append({  # 追加微调样本
                "input": chunk[:2000],  # 输入文本（截取前2000字符，控制微调样本长度）
                "output": {"entities": ents, "relations": rels},  # 输出为该分块的实体与关系
                "source": fname,  # 来源文件名
            })

        # 实体去重(按 name + type)
        seen = set()  # 初始化已见实体键集合
        dedup_entities = []  # 初始化去重后的实体列表
        for ent in all_entities:  # 遍历所有实体
            key = (ent.get("name", ""), ent.get("type", ""))  # 以 (name, type) 元组作为去重键
            if key in seen:  # 若该键已存在
                continue  # 跳过重复实体
            seen.add(key)  # 将键加入已见集合
            dedup_entities.append(ent)  # 将实体加入去重列表

        results.append({  # 将该法律的抽取结果追加到总结果列表
            "filename": fname,  # 文件名
            "statute_name": statute_name,  # 法规名
            "extract_dict": {  # 抽取结果字典
                "entities": dedup_entities,  # 去重后的实体列表
                "relations": all_relations,  # 关系列表（含自动建立的 CONTAINS_ARTICLE）
            }
        })
        processed.add(fname)  # 将文件名标记为已处理

        # 每完成一个文件就保存一次(断点续跑)
        with open(out_path, "w", encoding="utf-8") as f:  # 以 UTF-8 写模式打开图谱数据文件
            json.dump({"results": results}, f, ensure_ascii=False, indent=2)  # 写入图谱数据，ensure_ascii=False 保留中文，indent=2 缩进
        with open(finetune_path, "w", encoding="utf-8") as f:  # 以 UTF-8 写模式打开微调数据文件
            json.dump(finetune_data, f, ensure_ascii=False, indent=2)  # 写入微调数据
        with open(progress_path, "w", encoding="utf-8") as f:  # 以 UTF-8 写模式打开进度文件
            json.dump({"processed": list(processed)}, f, ensure_ascii=False)  # 写入已处理文件名列表（紧凑格式，无缩进）

    # 统计
    total_ents = sum(len(r["extract_dict"]["entities"]) for r in results)  # 统计所有法律的实体总数
    total_rels = sum(len(r["extract_dict"]["relations"]) for r in results)  # 统计所有法律的关系总数
    print(f"\n[完成] 抽取完成:")  # 打印完成标题
    print(f"   - 法律文件: {len(results)}")  # 打印处理的法律文件数
    print(f"   - 实体总数: {total_ents}")  # 打印实体总数
    print(f"   - 关系总数: {total_rels}")  # 打印关系总数
    print(f"   - 微调样本: {len(finetune_data)}")  # 打印微调样本数
    print(f"[输出] {out_path}")  # 打印输出文件路径


if __name__ == "__main__":
    main()  # 脚本直接运行时调用主函数
