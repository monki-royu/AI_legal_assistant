# -*- coding: utf-8 -*-
"""
法律数据爬虫 (v4 · 对齐 legal-documents 新接口)
================================================

数据源策略(参考 legal-documents/backend/app/crawlers/law_crawler.py):
  1. flk.npc.gov.cn 新接口搜索法律 → 拿到 bbbs(真实元数据)
  2. 按 bbbs 拉详情 → 解析条文目录树, 得到"第X条"+ 所属编/章(全部真实准确)
  3. 条文正文外网拿不到(官方只放 docx/OFD 内网), 用 LLM 按真实条号补正文
  4. 去重后写入 txt 文件(已存在且>3000字符直接跳过, 支持断点续跑)

与 v3 的区别:
  - v3 用旧的 /flk/search + flkofd.npc.gov.cn/reader/text 接口(已失效)
  - v4 用新的 /law-search/search/list + /law-search/search/flfgDetails 接口
  - v4 用 flk_client.parse_articles() DFS 解析条文目录树(结构准确)
  - v4 用 llm_gen.generate_article_contents() 按真实条号补正文(分批控制成本)
"""
# 📜 代码文字逻辑解析
# 本文件是 AI 法律助理项目的数据采集核心模块(v4 版本).
# 核心改进: 对齐 legal-documents 项目的新 flk 接口调用方式, 替换已失效的旧接口.
# 流程: flk搜索 → flk详情 → 解析条文树 → LLM补正文 → 写txt文件
# 兼容性: 保留 numpy 2.x 补丁, UTF-8 编码, CSV/XLSX 索引导出, 断点续跑

import os  # 路径操作
import sys  # sys.path 操作与 stdout 编码重配
import time  # 时间戳与爬取间隔休眠
import random  # 随机休眠, 避免请求过于密集

# ============================================================
# [CRITICAL] numpy 2.x 兼容补丁必须放在最开头, 在任何 import openpyxl 之前
# 原因: 旧 openpyxl 在 import 阶段就会访问 np.float, 若补丁在函数内部则已经太晚
# ============================================================
try:
    import numpy as np
    # numpy 2.x 兼容补丁: 直接赋值覆盖, 避免 hasattr 访问属性触发 FutureWarning
    # 在 numpy 1.x 中冗余但无害, 在 2.x 中保证 openpyxl 等旧库访问 np.float/bool/int 时不出错
    np.float = float
    np.int = int
    np.bool = bool
except ImportError:
    pass

# 统一 stdout 为 UTF-8, 避免 Windows GBK 中文/符号输出错误
if sys.stdout.encoding.lower().replace("-", "") != "utf8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 把项目根目录加入 sys.path (脚本在子目录运行时找不到 common 包)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from common.path_utils import root_dir  # 项目根目录
# 导入新 flk 客户端(新接口)
from __001__clawler.flk_client import search_laws, get_detail, parse_articles, pick_best_row, strip_tags, sxx_to_status, fetch_real_articles
# 注: 已删除 LLM 条文补全 (generate_article_contents) —— 防幻觉, 不编造法条


# 目标法律清单(常用基础法律)
# code 字段保留用于断点续跑检测(不参与新接口调用, 新接口用 bbbs 标识)
TARGET_LAWS = [
    {"name": "中华人民共和国民法典", "code": "20200528", "keywords": "民法典 合同编 物权编"},
    {"name": "中华人民共和国刑法", "code": "20201226", "keywords": "刑法 罪名 刑罚"},
    {"name": "中华人民共和国劳动法", "code": "20181229", "keywords": "劳动法 劳动合同 工时 工资"},
    {"name": "中华人民共和国劳动合同法", "code": "20120928", "keywords": "劳动合同法 解除 经济补偿"},
    {"name": "中华人民共和国民事诉讼法", "code": "20230901", "keywords": "民事诉讼法 管辖 证据"},
    {"name": "中华人民共和国公司法", "code": "20231229", "keywords": "公司法 注册资本 股东"},
    {"name": "中华人民共和国政府采购法", "code": "20140831", "keywords": "政府采购 公开招标 中标"},
    {"name": "中华人民共和国招标投标法", "code": "20170823", "keywords": "招标投标 开标 评标"},
    {"name": "中华人民共和国个人信息保护法", "code": "20210820", "keywords": "个人信息 告知同意 敏感信息"},
    {"name": "中华人民共和国数据安全法", "code": "20210610", "keywords": "数据安全 数据分类 数据处理"},
    {"name": "中华人民共和国反垄断法", "code": "20220624", "keywords": "反垄断 经营者集中 垄断协议"},
    {"name": "中华人民共和国网络安全法", "code": "20161107", "keywords": "网络安全 关键信息基础设施"},
]


def crawl_single_law(law_info: dict, output_dir: str, max_articles: int = 60) -> tuple:
    """
    爬取单部法律(优先真实docx正文, 降级LLM补全).

    流程:
      1. flk 新接口搜索 → 拿到 bbbs + 真实元数据(标题/公布日期/施行日期/时效性/制定机关)
      2. flk 新接口详情 → 解析条文目录树, 得到"第X条" + 所属编/章
      3. 优先: flk 旧版API下载真实 docx 原文 → 解析条文正文
         降级: docx 获取失败时用 LLM 按真实条号补正文
      4. 写入 txt 文件(头部元信息 + 条文正文)

    Parameters
    ----------
    law_info : dict
        法律信息, 含 name/code/keywords.
    output_dir : str
        输出目录路径.
    max_articles : int
        单部法律最大爬取条款数(控制 LLM 成本), 默认 60.

    Returns
    -------
    tuple[str, str]
        (text, source). text 为法律文本(含头部元信息+条文), source 为数据来源描述.
        失败时 text 为占位文本.
    """
    name = law_info["name"]
    keywords = law_info.get("keywords", "")
    out_path = os.path.join(output_dir, name + ".txt")

    # 断点续跑: 已有文件且超过 3000 字符则跳过
    if os.path.exists(out_path) and os.path.getsize(out_path) > 3000:
        with open(out_path, "r", encoding="utf-8") as f:
            text = f.read()
        print(f"[跳过] {name}: 已存在, {len(text)} 字符")
        return (text, "本地已存在")

    print(f"[抓取] {name} ...")

    # ============ Step 1: flk 搜索 ============
    try:
        total, rows = search_laws(name, size=10)
    except Exception as e:
        print(f"  [搜索失败] {name}: {e}")
        rows = []

    if not rows:
        # flk 搜索失败: 不编造法条, 写占位文本
        print(f"  [flk未搜到] {name}, 写入占位文本(不编造法条)")
        text = _fallback_placeholder(name, keywords)
        src = "flk未获取原文(占位)"
        _write_law_file(out_path, name, src, text)
        return (text, src)

    # 从搜索结果中挑选最佳匹配
    row = pick_best_row(rows, name)
    if not row:
        print(f"  [未匹配] {name}, 写入占位文本(不编造法条)")
        text = _fallback_placeholder(name, keywords)
        src = "flk未匹配原文(占位)"
        _write_law_file(out_path, name, src, text)
        return (text, src)

    bbbs = row.get("bbbs", "")
    if not bbbs:
        print(f"  [无bbbs] {name}, 写入占位文本(不编造法条)")
        text = _fallback_placeholder(name, keywords)
        src = "flk无bbbs(占位)"
        _write_law_file(out_path, name, src, text)
        return (text, src)

    # ============ Step 2: flk 详情 + 解析条文树 ============
    try:
        detail = get_detail(bbbs)
    except Exception as e:
        print(f"  [详情失败] {name}: {e}")
        detail = {}

    # 提取真实元数据
    real_name = strip_tags(detail.get("title") or row.get("title") or name)
    effective_date = detail.get("sxrq") or row.get("sxrq") or ""
    publish_date = detail.get("gbrq") or row.get("gbrq") or ""
    status = sxx_to_status(detail.get("sxx", row.get("sxx")))
    issuing_authority = detail.get("zdjgName") or row.get("zdjgName") or ""

    # 解析条文目录树
    articles = parse_articles(detail.get("content") or {})
    if max_articles > 0:
        articles = articles[:max_articles]

    if not articles:
        print(f"  [无条文] {real_name}, 写入占位文本(不编造法条)")
        text = _fallback_placeholder(name, keywords)
        src = "flk无条文树(占位)"
        _write_law_file(out_path, name, src, text)
        return (text, src)

    print(f"  [flk] {real_name}: 解析到 {len(articles)} 个条款, 时效={status}")

    # ============ Step 3: 优先下载真实 docx 正文, 降级 LLM 补全 ============
    article_nos = [a["article_no"] for a in articles]

    # 3a: 尝试旧版 API 下载 docx 获取真实条文正文
    print(f"  [docx] 尝试下载真实正文...")
    real_contents = fetch_real_articles(real_name)

    # 将 docx 解析出的正文匹配到条文结构
    contents = {}
    matched = 0
    if real_contents:
        for no in article_nos:
            if no in real_contents and len(real_contents[no]) > 10:
                contents[no] = real_contents[no]
                matched += 1

    # 3b: 未匹配到的条款不编造正文, 保留「正文待补充」占位
    missing_nos = [no for no in article_nos if no not in contents]
    if missing_nos:
        if matched > 0:
            print(f"  [docx] 真实正文匹配 {matched}/{len(article_nos)} 条, "
                  f"剩余 {len(missing_nos)} 条正文待补充(不编造)")
        else:
            print(f"  [docx] 未能获取真实正文, 全部 {len(article_nos)} 条正文待补充(不编造)")
    else:
        print(f"  [docx] 全部 {len(article_nos)} 条正文来自真实原文")

    # ============ Step 4: 拼装文本 ============
    text_parts = []
    for a in articles:
        no = a["article_no"]
        chapter = a.get("chapter", "")
        content = contents.get(no, f"（{real_name}{no}正文待补充）")
        # 条文格式: 章节路径(若有) + 条号 + 正文
        if chapter:
            text_parts.append(f"[{chapter}]")
        text_parts.append(f"{no} {content}")
        text_parts.append("")  # 空行分隔

    text = "\n".join(text_parts)
    if matched == len(article_nos):
        src = f"flk真实原文(时效:{status})"
    elif matched > 0:
        src = f"flk真实原文{matched}条+{len(missing_nos)}条待补充(时效:{status})"
    else:
        src = f"flk原文未获取(时效:{status})"

    # ============ 写文件 ============
    _write_law_file(out_path, name, src, text,
                    real_name=real_name, effective_date=effective_date,
                    publish_date=publish_date, status=status,
                    issuing_authority=issuing_authority,
                    article_count=len(articles))

    print(f"  [OK] {src}, {len(text)} 字符 -> {os.path.basename(out_path)}")
    return (text, src)


def _fallback_placeholder(law_name: str, keywords: str) -> str:
    """
    兜底占位文本(flk 搜索/详情失败时使用) —— 不编造法条。

    【防幻觉 (2026-08 决策)】
    此前的 _llm_fallback_skeleton 会让 LLM 凭空生成「第X条...」条文骨架,
    属于编造法条, 已删除。本函数只返回明确的占位说明, 正文留待人工/联网后补充。
    """
    return (
        f"{law_name}\n"
        f"核心关键词: {keywords}\n"
        f"说明: 未能从 flk 获取真实条文原文, 本文件为占位文本(未编造法条)。"
        f"建议联网后重新运行爬虫获取原文。"
    )


def _write_law_file(out_path: str, name: str, src: str, text: str,
                    real_name: str = "", effective_date: str = "",
                    publish_date: str = "", status: str = "",
                    issuing_authority: str = "", article_count: int = 0):
    """
    写入法律 txt 文件(带头部元信息).

    头部格式(以 # 开头的元信息行)被下游 importer.py 的实体抽取器按行过滤依赖,
    修改时需同步.
    """
    # 构造头部元信息
    header_lines = [
        f"# {name}",
        f"# 来源: {src}",
        f"# 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    if real_name and real_name != name:
        header_lines.append(f"# 官方名称: {real_name}")
    if publish_date:
        header_lines.append(f"# 公布日期: {publish_date}")
    if effective_date:
        header_lines.append(f"# 施行日期: {effective_date}")
    if status:
        header_lines.append(f"# 时效性: {status}")
    if issuing_authority:
        header_lines.append(f"# 制定机关: {issuing_authority}")
    if article_count:
        header_lines.append(f"# 条款数: {article_count}")

    header = "\n".join(header_lines) + "\n\n"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(header + text)


def export_law_index(output_path: str, success_list: list):
    """
    导出法律索引 (csv 为主, xlsx 为辅).

    与 v3 保持一致, 同时输出 CSV(零依赖)和 XLSX(openpyxl).
    """
    # 1. CSV 输出 (100% 可靠, 零额外依赖)
    csv_path = output_path.replace(".xlsx", ".csv")
    with open(csv_path, "w", encoding="utf-8-sig") as f:
        f.write("序号,法律名称,代码/版本,是否已下载,来源,文件路径\n")
        for i, item in enumerate(success_list, 1):
            f.write(f"{i},{item['name']},{item.get('code', '')},"
                    f"{'是' if item.get('ok') else '否'},"
                    f"{item.get('source', '')},"
                    f"{item.get('path', '')}\n")
    print(f"[索引] CSV 已保存: {csv_path}")

    # 2. XLSX 输出
    try:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "法律列表"
        ws.append(["序号", "法律名称", "代码/版本", "是否已下载", "来源", "文件路径"])
        for i, item in enumerate(success_list, 1):
            ws.append([
                i, item["name"], item.get("code", ""),
                "是" if item.get("ok") else "否",
                item.get("source", ""), item.get("path", "")
            ])
        wb.save(output_path)
        print(f"[索引] XLSX 已保存: {output_path}")
    except Exception as e:
        print(f"[索引] XLSX 生成跳过 ({type(e).__name__}: {e}), 已保存 CSV")


def main():
    """
    主函数: 遍历 TARGET_LAWS 逐部爬取法律并导出索引.

    每部法律间随机休眠 0.3-0.8 秒, 避免请求过于密集被反爬.
    """
    output_dir = os.path.join(root_dir, "data", "laws")
    os.makedirs(output_dir, exist_ok=True)

    results = []
    success = 0
    for law in TARGET_LAWS:
        text, source = crawl_single_law(law, output_dir)
        ok = bool(text) and len(text) > 100
        results.append({
            **law,
            "ok": ok,
            "source": source,
            "path": os.path.join(output_dir, law["name"] + ".txt") if ok else ""
        })
        if ok:
            success += 1
        time.sleep(random.uniform(0.3, 0.8))  # 随机休眠

    # 导出索引
    index_path = os.path.join(root_dir, "__001__clawler", "法律列表.xlsx")
    export_law_index(index_path, results)

    print(f"\n=== 完成: {success}/{len(TARGET_LAWS)} 部法律获取成功 ===")
    for r in results:
        mark = "OK" if r["ok"] else "FAIL"
        print(f"  [{mark}] {r['name']} - {r.get('source', '')}")
    print(f"输出目录: {output_dir}")


if __name__ == "__main__":
    main()
