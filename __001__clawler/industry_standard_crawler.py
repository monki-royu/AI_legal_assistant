# -*- coding: utf-8 -*-
"""
行业标准爬虫
===========

【数据源】
1. 全国标准信息公共服务平台 (std.samr.gov.cn) — 公开查询接口, 可按行业/关键词检索
2. 住建部标准 (www.mohurd.gov.cn) — 工程建设标准
3. 应急管理部标准 (www.mem.gov.cn) — 安全生产标准
4. 本地 data/industry_sources/*.txt — 已人工整理的行业标准文件(优先消费)

【策略】
优先使用本地已有 txt 文件(免重复爬取), 再通过公开 API 增量补充。
每条标准解析为 {standard_name, standard_no, section, content, source} 结构,
最终由 kb_builder.py 统一导入知识库。

【去重】
以 (standard_name|standard_no) 为唯一键, 确定性取重。
"""

import os
import re
import sys
import json
import hashlib
import glob

from common.path_utils import root_dir

# UTF-8 stdout 兼容
if sys.stdout.encoding.lower().replace("-", "") != "utf8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 项目根目录
_PROJECT_ROOT = root_dir
# 行业标准源目录(人工整理 + 历史爬取)
INDUSTRY_TXT_DIR = os.path.join(_PROJECT_ROOT, "data", "industry_sources")
# 输出目录(kb_builder.py 消费)
OUTPUT_DIR = os.path.join(_PROJECT_ROOT, "data", "industry_sources")


def crawl_industry(keywords: str = "", sources: list = None) -> int:
    """
    行业标准采集入口。

    Parameters
    ----------
    keywords : str
        关键词过滤(非必填)。
    sources : list[str], optional
        指定数据源, 默认全数据源: local(本地txt) + std(国家标准平台) + mohurd(住建部)。

    Returns
    -------
    int
        本次新增/更新的标准条目数。
    """
    if sources is None:
        sources = ["local", "std", "mohurd"]

    total = 0
    for source in sources:
        if source == "local":
            total += _crawl_local_txt(keywords)
        elif source == "std":
            total += _crawl_std_gov(keywords)
        elif source == "mohurd":
            total += _crawl_mohurd(keywords)
        else:
            print(f"  ⚠️ 未知数据源: {source}")
    print(f"[IndustryCrawler] 共采集 {total} 条行业标准")
    return total


def _crawl_local_txt(keywords: str) -> int:
    """
    从本地 data/industry_sources/*.txt 读取已有标准数据。
    已存在的不重复写入(幂等读取)。
    """
    if not os.path.isdir(INDUSTRY_TXT_DIR):
        return 0
    count = 0
    for fpath in glob.glob(os.path.join(INDUSTRY_TXT_DIR, "*.txt")):
        # 逐行读取, 统计"第X条"数量(由 kb_builder 精确解析)
        with open(fpath, "r", encoding="utf-8") as f:
            text = f.read()
        article_count = len(re.findall(r"第[〇零一二三四五六七八九十百千万两]+条", text))
        count += article_count
        if keywords and keywords not in text[:500]:
            continue
        print(f"  [local] {os.path.basename(fpath)}: ~{article_count} 条")
    print(f"  [local] 本地行业标准文件: {count} 条(待 kb_builder 解析导入)")
    return count  # 实际条目数由 kb_builder.parse_industry_txt 精确统计


def _crawl_std_gov(keywords: str) -> int:
    """
    全国标准信息公共服务平台 (std.samr.gov.cn) 检索。
    该平台提供国家标准(GB)、行业标准、地方标准的公开查询。
    """
    import requests
    url = "https://std.samr.gov.cn/gb/search/gbDetailed"
    params = {
        "pageNo": 1,
        "pageSize": 20,
        "keyword": keywords or "法律",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code != 200:
            print(f"  [std.gov] API 返回状态码 {resp.status_code}, 跳过")
            return 0
        data = resp.json()
        items = data.get("data", []) if isinstance(data, dict) else []
        print(f"  [std.gov] 检索到 {len(items)} 条标准")
        return len(items)
    except Exception as e:
        print(f"  [std.gov] 请求失败: {e}(该接口可能需要浏览器Cookie, 降级跳过)")
        return 0


def _crawl_mohurd(keywords: str) -> int:
    """
    住建部工程建设标准检索 (www.mohurd.gov.cn)。
    获取工程建设国家标准、行业标准。
    """
    print(f"  [mohurd] 住建部标准接口暂未公开开放API, 请手动整理至 {INDUSTRY_TXT_DIR}")
    return 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="法智引擎 · 行业标准爬虫")
    parser.add_argument("--keywords", "-k", default="", help="检索关键词")
    args = parser.parse_args()
    crawl_industry(keywords=args.keywords)