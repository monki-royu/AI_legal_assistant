# -*- coding: utf-8 -*-
"""
国家法律法规数据库 (flk.npc.gov.cn) API 客户端
==============================================

参考 legal-documents/backend/app/crawlers/flk_client.py 实现,
使用官方前端 SPA 的真实接口(逆向自 flk.npc.gov.cn):

1. POST /law-search/search/list  —— 按标题搜索法律, 返回列表
   (含 bbbs/title/gbrq/sxrq/sxx/zdjgName/flxz 等真实元数据)
2. GET  /law-search/search/flfgDetails —— 按 bbbs 拿详情
   data.content 是完整条文目录树(编/章/节/条结构)

说明: 条文正文在 docx/OFD 里, 下载链接是内网签名地址, 外网拿不到.
      本模块只负责"抓真实元数据 + 解析条文结构(第X条 + 所属编/章)",
      条文正文由 llm_gen.py 用 LLM 按真实条号补齐.

可迁移性: 该模块封装了 flk.npc.gov.cn 的两步式抓取(搜索→详情→解析条文树),
         可迁移到任何需要从国家法律法规数据库获取法律结构的场景.
"""
# 📜 代码文字逻辑解析
# 本文件封装了国家法律法规数据库(flk.npc.gov.cn)的真实API调用:
# - search_laws(): 按标题搜索法律, 返回搜索结果列表(含bbbs唯一标识)
# - get_detail(): 按bbbs获取法律详情, 返回条文目录树
# - parse_articles(): 深度遍历目录树, 抽取每一条"第X条"及其所属编/章/节
# - pick_best_row(): 从搜索结果中挑最匹配目标法律名的一条
# 接口返回的元数据(公布日期/施行日期/时效性/制定机关)都是真实准确的,
# 仅条文正文需要LLM补齐(因外网无法获取docx/OFD内网签名链接).

import re  # 正则表达式, 用于条文/章节编号匹配与HTML标签清洗
import time  # 时间戳, 新版 API 请求参数
from urllib.parse import quote  # URL编码, 构造搜索/GitHub raw链接
import requests  # HTTP 请求库, 用于调用各 API

# flk.npc.gov.cn 新版 API 基础 URL
BASE = "https://flk.npc.gov.cn"

# 请求头: 模拟浏览器访问, 部分接口校验 Referer
HEADERS = {
    "Content-Type": "application/json;charset=utf-8",  # POST 请求体为 JSON
    "Accept": "application/json, text/plain, */*",  # 接受 JSON 响应
    "Referer": "https://flk.npc.gov.cn/",  # Referer 校验
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
}

# 条款编号正则: 第一条 / 第一千二百六十条 / 第一条之一
# 支持: 〇零一二三四五六七八九十百千万两 + 可选"之X"
ARTICLE_RE = re.compile(r"^第[〇零一二三四五六七八九十百千万两]+条(之[〇零一二三四五六七八九十]+)?$")
# 章节编号正则: 第一编 / 第二章 / 第三节
CHAPTER_RE = re.compile(r"^第[〇零一二三四五六七八九十百千万两]+[编章节]")

# 时效性映射 (sxx 字段值 → 中文状态)
SXX_STATUS = {3: "现行有效", 2: "已修改", 1: "已废止", 5: "尚未生效"}


def strip_tags(text: str) -> str:
    """
    去掉搜索结果标题里的 <em class='highlight'> 高亮标签.

    flk 搜索接口返回的 title 字段会带高亮标签, 需清洗后才能用于精确匹配.
    """
    return re.sub(r"<[^>]+>", "", text or "").strip()


def sxx_to_status(sxx) -> str:
    """将 sxx 字段值转换为中文时效性状态(现行有效/已修改/已废止/尚未生效)."""
    try:
        return SXX_STATUS.get(int(sxx), "现行有效")
    except (TypeError, ValueError):
        return "现行有效"


def search_laws(keyword: str, page: int = 1, size: int = 20,
                search_type: int = 2, timeout: int = 15) -> tuple:
    """
    按标题搜索法律法规.

    Parameters
    ----------
    keyword : str
        法律名称关键词, 如 "中华人民共和国民法典".
    page : int
        页码, 从 1 开始.
    size : int
        每页条数, 默认 20.
    search_type : int
        搜索类型: 2=模糊匹配, 1=精确匹配.
    timeout : int
        请求超时秒数.

    Returns
    -------
    tuple[int, list[dict]]
        (总数, 行列表). 每行含 bbbs / title / gbrq / sxrq / sxx / zdjgName / flxz.
        请求失败返回 (0, []).
    """
    # 构造 POST 请求体(与官方前端 SPA 的请求结构一致)
    body = {
        "searchRange": 1,          # 1 = 标题范围
        "sxrq": [], "gbrq": [], "sxx": [],  # 施行日期/公布日期/时效性 过滤条件(空=不过滤)
        "searchType": search_type,  # 搜索类型
        "xgzlSearch": False,       # 是否搜索相关资料
        "searchContent": keyword,  # 搜索关键词
        "orderByParam": {"order": "-1", "sort": ""},  # 排序: -1=默认排序
        "flfgCodeId": [], "zdjgCodeId": [], "gbrqYear": [],  # 分类/制定机关/年份过滤(空=不过滤)
        "pageNum": page,           # 页码
        "pageSize": size,          # 每页条数
    }
    try:
        resp = requests.post(f"{BASE}/law-search/search/list", json=body,
                             headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        return data.get("total", 0), data.get("rows", []) or []
    except Exception as e:
        print(f"[flk_client] 搜索失败: {e}")
        return 0, []


def get_detail(bbbs: str, timeout: int = 15) -> dict:
    """
    按 bbbs 拿法律详情.

    Parameters
    ----------
    bbbs : str
        法律唯一标识(由 search_laws 返回).
    timeout : int
        请求超时秒数.

    Returns
    -------
    dict
        data 对象, 含 title/gbrq/sxrq/sxx/flxz/zdjgName/content(目录树).
        请求失败返回空字典.
    """
    try:
        resp = requests.get(
            f"{BASE}/law-search/search/flfgDetails",
            params={"bbbs": bbbs},
            headers=HEADERS,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json().get("data", {}) or {}
    except Exception as e:
        print(f"[flk_client] 获取详情失败: {e}")
        return {}


def parse_articles(content_node: dict) -> list:
    """
    深度遍历条文目录树, 抽取每一条"第X条"及其所属章节.

    Parameters
    ----------
    content_node : dict | None
        flk 详情接口返回的 data.content 对象(目录树根节点).
        允许 None（API 未返回正文结构时返回空列表）.

    Returns
    -------
    list[dict]
        条文列表, 每项含:
        - article_no: 条款编号, 如 "第一条"
        - chapter: 所属章节路径, 如 "第一编 总则 · 第一章 基本规定"
        正文文本不在树里(官方只放 docx/OFD), 此处只拿结构.
    """
    if not content_node:
        # API 未返回正文结构(如司法解释没有目录树), 返回空列表
        return []

    results = []

    def norm(title: str) -> str:
        """全角空格/多空格归一, 方便正则匹配."""
        return re.sub(r"\s+", " ", strip_tags(title)).strip()

    def dfs(node: dict, chapters: list):
        """深度遍历目录树节点."""
        title = norm(node.get("title", ""))
        children = node.get("children") or []

        # 若是"第X条"节点, 记录条款编号和所属章节路径
        if ARTICLE_RE.match(title):
            results.append({
                "article_no": title,
                "chapter": " · ".join(chapters),
            })
            return

        # 若是章节节点(第X编/章/节), 加入章节路径继续遍历
        # 含"附则/附　则"这种无编号的收尾章节
        next_chapters = chapters
        if CHAPTER_RE.match(title) or title in ("附则", "附　则"):
            next_chapters = chapters + [title]

        for child in children:
            dfs(child, next_chapters)

    # 从根节点的 children 开始遍历
    for child in (content_node.get("children") or []):
        dfs(child, [])

    return results


def pick_best_row(rows: list, target_name: str) -> dict:
    """
    从搜索结果里挑最匹配目标法律名的一条.

    优先级:
      1. 清洗后标题完全相等
      2. 现行有效(sxx=3)且包含目标名
      3. 包含目标名
      4. 第一条结果

    Parameters
    ----------
    rows : list[dict]
        search_laws 返回的行列表.
    target_name : str
        目标法律全称.

    Returns
    -------
    dict | None
        最佳匹配行; 无结果返回 None.
    """
    if not rows:
        return None
    # 清洗所有标题
    cleaned = [(r, strip_tags(r.get("title", ""))) for r in rows]

    # 优先级 1: 完全相等
    for r, title in cleaned:
        if title == target_name:
            return r
    # 优先级 2: 现行有效且包含
    for r, title in cleaned:
        if target_name in title and r.get("sxx") == 3:
            return r
    # 优先级 3: 包含即可
    for r, title in cleaned:
        if target_name in title:
            return r
    # 兜底: 第一条
    return rows[0]


# ============================================================
# 旧版 API (获取真实条文正文 docx)
# ============================================================
# 旧版 API 流程:
#   1. GET  /api/?type=flfg&searchType=title;vague  → result.data[].id
#   2. POST /api/detail  (form: id=xxx)             → result.body[0].path
#   3. GET  https://wb.flk.npc.gov.cn + path         → docx 二进制文件
# 与新版 API(/law-search/)的区别: 旧版能拿到 docx 下载链接, 正文是真实原文.

# 条文起始正则: 匹配 "第一条　..." 或 "第一千二百六十条 ..." 开头的行
ARTICLE_START_RE = re.compile(
    r'^(第[〇零一二三四五六七八九十百千万两]+条(?:之[〇零一二三四五六七八九十]+)?)\s*'
)


def fetch_real_articles(law_name: str) -> dict:
    """
    一站式获取法律真实条文正文: 多数据源尝试(最高法院公报/司法部官网/GitHub lawtext),
    全部失败时返回空字典(让上层走LLM降级).

    数据源优先级:
      1. gongbao.court.gov.cn  (最高法公报, 条文完整且权威)
      2. moj.gov.cn           (司法部官网, 民法典宣传月等专题页含完整条文)
      3. github.com/lawtext/laws (社区整理, 作为补漏)

    Parameters
    ----------
    law_name : str
        法律名称.

    Returns
    -------
    dict[str, str]
        {article_no: article_text}. 任一步骤失败返回空字典.
    """
    simple_headers = {"User-Agent": HEADERS["User-Agent"]}
    # 最佳部分版本暂存 (所有源都拿不到完整版本时, 用条数最多的那个)
    best_partial = {}
    best_partial_src = ""
    best_partial_count = 0

    # ---- 通用判定: 不同法律期望的"完整条数阈值" ----
    def _is_full(n, name):
        # 对已知大法典设置严格阈值
        if "民法典" in name:        return n >= 1200
        if "刑法" in name:          return n >= 450
        if "民事诉讼法" in name:    return n >= 280
        if "刑事诉讼法" in name:    return n >= 300
        if "行政诉讼法" in name:    return n >= 100
        if "合同法" in name or "劳动合同" in name: return n >= 100
        if "公司法" in name:       return n >= 200
        if "证券法" in name:       return n >= 220
        if "个人信息保护法" in name or "数据安全法" in name: return n >= 70
        # 默认阈值: 80 条以上认为较完整
        return n >= 80

    def _register_partial(articles, src):
        nonlocal best_partial, best_partial_src, best_partial_count
        if len(articles) > best_partial_count:
            best_partial = articles
            best_partial_src = src
            best_partial_count = len(articles)

    # ============== 数据源 1: gongbao.court.gov.cn 搜索 + 详情页 ==============
    # 先用搜索接口找到详情链接, 再解析详情页
    try:
        print(f"  [真实源1] gongbao.court.gov.cn...")
        # gongbao 搜索接口
        search_url = (
            "http://gongbao.court.gov.cn/ArticleList.html"
            "?serial_no=flxd&sw={}"
        ).format(quote(law_name))
        try:
            resp = requests.get(search_url, headers=simple_headers, timeout=20)
            resp.encoding = resp.apparent_encoding or 'utf-8'
            html = resp.text
        except Exception as e:
            print(f"    搜索跳过: {type(e).__name__}")
            html = ""

        detail_url = None
        if html:
            # 在搜索结果里找详情链接 (href="/Details/xxx")
            # 匹配包含 law_name 关键子串的标题链接
            key = law_name.replace("中华人民共和国", "")  # 如"民法典"
            m = re.search(
                r'<a[^>]+href=[\'"](?P<href>/Details/[^\'"]+)[\'"][^>]*>(?P<title>.*?)</a>',
                html, re.S,
            )
            visited_urls = set()
            for m in re.finditer(
                r'<a[^>]+href=[\'"](?P<href>/Details/[^\'"]+)[\'"][^>]*>(?P<title>.*?)</a>',
                html, re.S,
            ):
                title = strip_tags(m.group('title')).strip()
                href = m.group('href')
                if (key in title or law_name in title) and href not in visited_urls:
                    detail_url = "http://gongbao.court.gov.cn" + href
                    visited_urls.add(href)
                    print(f"    匹配详情: {title[:50]} -> {detail_url[:80]}...")
                    break

        # ---- 12部默认法律的最高法公报硬编码链接 (搜索接口ReadTimeout时兜底) ----
        _GONGBAO_KNOWN_URLS = {
            # 民法典: 2020年官报, 完整1260条 (moj源补充更全)
            "民法典": (
                "http://gongbao.court.gov.cn/Details/"
                "51eb6750b8361f79be8f90d09bc202.html?sw=2020年9月2日"
            ),
            # 刑法 (1997年修订版)
            "刑法": (
                "http://gongbao.court.gov.cn/Details/"
                "f8e30d0689b23f57bfc782d21035c3.html"
            ),
            # 刑事诉讼法 (2018修正版, 分两页: 第1页总则, 第2页续=审判/执行)
            "刑事诉讼法": [
                "http://gongbao.court.gov.cn/Details/"
                "11aaa996a59f5824912be62d3dfa0d.html",
                "http://gongbao.court.gov.cn/Details/"
                "b772fbc86b46cdf2eddda464ef3325.html",
            ],
            # 民事诉讼法 (2023/2024最新修正版)
            "民事诉讼法": (
                "http://gongbao.court.gov.cn/Details/"
                "886331ece0f6611a370642e89f08c6.html"
            ),
            # 公司法 (2023年第二次修订版)
            "公司法": (
                "http://gongbao.court.gov.cn/Details/"
                "c979401282004999d124f53b17ec32.html"
            ),
            # 劳动合同法
            "劳动合同法": (
                "http://gongbao.court.gov.cn/Details/"
                "51fd7f43c533cce8345b59f426fd43.html"
            ),
            # 反垄断法 (2007版, 后续如有2022修正版可替换)
            "反垄断法": (
                "http://gongbao.court.gov.cn/Details/"
                "62a3b1d678b02a466a51889827f9ce.html"
            ),
            # 招标投标法
            "招标投标法": (
                "http://gongbao.court.gov.cn/Details/"
                "861b5d140c03b839727ce4d531e307.html"
            ),
        }
        for _k, _u in _GONGBAO_KNOWN_URLS.items():
            if _k in law_name:
                detail_url = _u  # 可能是 str 或 list[str] (刑诉法两页合并)
                print(f"    命中已知{_k}硬编码链接")
                break

        if detail_url:
            try:
                # 支持单URL 或 URL列表 (如刑诉法分两页发布)
                _urls = detail_url if isinstance(detail_url, list) else [detail_url]
                articles = {}
                for _u in _urls:
                    resp = requests.get(_u, headers=simple_headers, timeout=20)
                    resp.encoding = resp.apparent_encoding or 'utf-8'
                    _part = _parse_html_to_articles(resp.text)
                    # 合并: 同条号冲突时保留先出现的 (总则优先)
                    for _k, _v in _part.items():
                        articles.setdefault(_k, _v)
                    print(f"    子页 {len(_part)} 条, 累计 {len(articles)} 条")
                # 阈值判定: 达到完整阈值即返回, 否则暂存为最佳部分版本
                if _is_full(len(articles), law_name):
                    print(f"  [真实源1] gongbao.court.gov.cn 成功(完整): {len(articles)} 条")
                    return articles
                elif len(articles) >= 10:
                    print(f"    抓取到 {len(articles)} 条 (疑似节选, 继续试更完整的源)...")
                    _register_partial(articles, "gongbao")
                else:
                    print(f"    解析不足: {len(articles)} 条")
            except Exception as e:
                print(f"    详情抓取失败: {type(e).__name__}: {e}")
    except Exception as e:
        print(f"  [真实源1] 异常: {type(e).__name__}: {e}")

    # ============== 数据源 2: moj.gov.cn (司法部官网) ==============
    try:
        print(f"  [真实源2] moj.gov.cn...")
        if "民法典" in law_name:
            # moj 民法典宣传月着陆页含完整1260条正文
            moj_urls = [
                "https://www.moj.gov.cn/pub/sfbgw/zwgkztzl/2025nianzhuanti/2025mfdxcy/",
            ]
            for url in moj_urls:
                try:
                    resp = requests.get(url, headers=simple_headers, timeout=25)
                    resp.encoding = resp.apparent_encoding or 'utf-8'
                    articles = _parse_html_to_articles(resp.text)
                    if _is_full(len(articles), law_name):
                        print(f"  [真实源2] moj.gov.cn 成功(完整): {len(articles)} 条")
                        return articles
                    elif len(articles) >= 10:
                        print(f"    抓取到 {len(articles)} 条 (疑似节选, 继续试更完整的源)...")
                        _register_partial(articles, "moj")
                    else:
                        print(f"    解析不足: {len(articles)} 条")
                except Exception as e:
                    print(f"    抓取失败: {type(e).__name__}: {e}")
        else:
            print(f"    暂不支持 {law_name} 的moj硬编码, 跳过")
    except Exception as e:
        print(f"  [真实源2] 异常: {type(e).__name__}: {e}")

    # ============== 数据源 3: GitHub lawtext/laws raw markdown ==============
    try:
        print(f"  [真实源3] github.com/lawtext/laws...")
        # 构建可能的 raw URL, 直接请求而不是列目录
        key2 = law_name.replace("中华人民共和国", "")
        candidates = [
            f"https://raw.githubusercontent.com/lawtext/laws/main/content/"
            f"法律/{quote(law_name)}.md",
            f"https://raw.githubusercontent.com/lawtext/laws/main/content/"
            f"法律/{quote(key2)}.md",
        ]
        for url in candidates:
            try:
                resp = requests.get(url, headers=simple_headers, timeout=15)
                if resp.status_code == 200 and len(resp.text) > 2000:
                    print(f"    命中: {url.split('/')[-1]} ({len(resp.text)} 字符)")
                    articles = _parse_html_to_articles(resp.text)
                    if _is_full(len(articles), law_name):
                        print(f"  [真实源3] GitHub lawtext 成功(完整): {len(articles)} 条")
                        return articles
                    elif len(articles) >= 10:
                        print(f"    抓取到 {len(articles)} 条 (疑似节选)...")
                        _register_partial(articles, "github-lawtext")
            except Exception:
                pass
        print(f"    未匹配到对应 markdown 文件或条数不足")
    except Exception as e:
        print(f"  [真实源3] 异常: {type(e).__name__}: {e}")

    # ============== 所有源都未命中"完整版本", 返回条数最多的部分版本 ==============
    if best_partial_count >= 10:
        print(f"  [真实源] 无完整版本, 选用最佳部分版本: {best_partial_src} {best_partial_count} 条")
        return best_partial

    # 所有源都失败 (连 ≥10 条的部分版本都没有)
    print(f"  [真实源] 全部失败, 将使用 LLM 降级补全")
    return {}


# ------------------------------------------------------------
# HTML 解析工具: 从任意含 "第一条　xxx" 文本的 HTML/Markdown 中切出条文
# ------------------------------------------------------------
_STRIP_TAG_RE = re.compile(r'<[^>]+>')  # HTML标签清洗
# 块级标签: 这些标签的结束意味着换行, 需要在去标签前先替换为\n
_BLOCK_TAG_RE = re.compile(
    r'</?(?:p|div|br|li|tr|h[1-6]|section|article|td|th|hr|ul|ol|dl|dd|dt)[^>]*>',
    re.I,
)


def _strip_html(s: str) -> str:
    """清除 HTML/Markdown 标签和格式符, 保留段落换行"""
    # Step 1: 块级标签替换为换行 (关键! 否则所有文字挤在一行)
    s = _BLOCK_TAG_RE.sub('\n', s)
    # Step 2: 清除剩余 HTML 标签
    s = _STRIP_TAG_RE.sub('', s)
    # Step 3: 清除 markdown **加粗** / 标题 #
    s = re.sub(r'\*{1,3}', '', s)
    s = re.sub(r'^#{1,6}\s*', '', s, flags=re.M)
    # Step 4: 替换 HTML 实体
    s = s.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    # Step 5: 合并多余空行
    s = re.sub(r'\n{3,}', '\n\n', s)
    return s


def _parse_html_to_articles(full_text: str) -> dict:
    """
    从 HTML/Markdown 或纯文本中切分 "第X条" 条文.

    流程: 去HTML标签 -> 按行拆 -> ARTICLE_START_RE 切分 -> 组装 {条号: 正文}
    """
    if not full_text:
        return {}
    full_text = _strip_html(full_text)
    articles = {}
    current_no = None
    current_text = []

    for line in full_text.split("\n"):
        line = line.strip()
        if not line:
            continue
        m = ARTICLE_START_RE.match(line)
        if m:
            if current_no and current_text:
                articles[current_no] = "".join(current_text).strip()
            current_no = m.group(1)
            rest = line[m.end():].strip()
            current_text = [rest] if rest else []
        elif current_no:
            current_text.append(line)

    if current_no and current_text:
        articles[current_no] = "".join(current_text).strip()

    if articles:
        for i, (no, txt) in enumerate(list(articles.items())[:3], 1):
            preview = txt[:50] + ("..." if len(txt) > 50 else "")
            print(f"    {no}: {preview}")
    return articles


# ------------------------------------------------------------
# 旧版 API (已下线, 保留函数签名但直接跳过, 防止旧代码崩溃)
# ------------------------------------------------------------
def search_laws_old(keyword: str, page: int = 1, size: int = 10,
                    timeout: int = 15) -> list:
    """旧版API已下线(flk.npc.gov.cn已升级SPA), 直接返回空."""
    print(f"  [flk旧版] API已下线, 跳过")
    return []


def get_download_path(old_id: str, timeout: int = 15) -> str:
    """旧版API已下线, 直接返回空."""
    return ""


def download_law_docx(url: str, timeout: int = 30) -> bytes:
    """旧版API已下线, 直接抛错."""
    raise RuntimeError("flk旧版API已下线, 不支持下载")


def parse_docx_articles(content: bytes) -> dict:
    """解析 docx/doc 文件二进制 (保留给 law-flk-vol1 未来使用)."""
    import io
    if len(content) >= 4 and content[:4] == b'\xd0\xcf\x11\xe0':
        print(f"  [docx] .doc旧格式, 尝试兼容解析...")
        try:
            import olefile
            ole = olefile.OleFileIO(io.BytesIO(content))
            if ole.exists('WordDocument'):
                stream = ole.openstream('WordDocument')
                raw = stream.read()
                try:
                    text = raw.decode('utf-16-le', errors='ignore')
                    lines = []
                    buf = []
                    for ch in text:
                        if '\u4e00' <= ch <= '\u9fff' or ch in '，。；：、（）《》""''0123456789零一二三四五六七八九十百千万两条之编章节　 ':
                            buf.append(ch)
                        else:
                            if len(buf) > 3:
                                lines.append(''.join(buf))
                            buf = []
                    if len(buf) > 3:
                        lines.append(''.join(buf))
                    full_text = '\n'.join(lines)
                except Exception:
                    full_text = ''
            else:
                full_text = ''
            ole.close()
            if not full_text:
                return {}
        except ImportError:
            print(f"  [docx] 未安装olefile, 无法解析.doc (pip install olefile)")
            return {}
        except Exception as e:
            print(f"  [docx] .doc解析失败: {type(e).__name__}: {e}")
            return {}
    else:
        try:
            from docx import Document
        except ImportError:
            print("  [docx] python-docx 未安装, 无法解析")
            return {}
        try:
            doc = Document(io.BytesIO(content))
        except Exception as e:
            print(f"  [docx] 解析失败: {type(e).__name__}: {e}")
            return {}
        full_text = "\n".join([p.text.strip() for p in doc.paragraphs if p.text.strip()])
    return _parse_html_to_articles(full_text)
