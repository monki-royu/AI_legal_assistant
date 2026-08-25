# -*- coding: utf-8 -*-
"""
知识库构建器 (Knowledge Base Builder)
====================================

【模块定位】
把"原始文本数据"统一转换为"结构化 JSON 文档集",
供 FAISS 索引构建(rebuild_faiss_single.py) 和知识图谱构建(generate_neo4j_cypher.py)消费。

【数据流向】
  原始数据                                                    知识库产物
  ─────────────────────────────────────────────────────────────────────
  data/laws_txt/*.txt                      ──►  data/knowledge_base/laws_docs.json
  data/regulations/*.txt                   ──►  data/knowledge_base/regulations_docs.json
  data/cases_txt/*/*.txt                   ──►  data/knowledge_base/cases_docs.json
  data/industry_sources/*.txt              ──►  data/knowledge_base/industry_docs.json
  data/interpretations/*.txt               ──►  data/knowledge_base/interpretations_docs.json
  data/law/*.docx (官方docx原文, 可选, 当前未提供) ──► 并入 laws_docs.json

【核心设计】
  1. 统一文档结构: 每条文档都含文本字段 + 元数据字段(法规名/条号/来源等),
     供下游 FAISS 向量编码与 Neo4j 实体抽取直接使用;
  2. 幂等构建: 按 (title|article_no) 确定性去重, 重复执行不产生重复文档;
  3. 全量重建: 数据量小(千级条文), 每次全量重建秒级完成, 无需增量状态。

【对外函数】
  - parse_law_txt(path)           : 解析单部法律 txt → 条文列表
  - parse_law_docx(path)          : 解析官方 docx 原文 → 条文列表
  - parse_case_txt(path)          : 解析单个案例 txt → 案例文档
  - parse_industry_txt(path)      : 解析行业标准 txt → 条款列表
  - parse_interpretation_txt(path): 解析司法解释 txt → 条款列表
  - build_laws_docs()             : 构建法律法规文档集
  - build_regulations_docs()      : 构建行政法规/部门规章文档集
  - build_cases_docs()            : 构建裁判案例文档集
  - build_industry_docs()         : 构建行业标准文档集
  - build_interpretations_docs()  : 构建司法解释文档集
  - build_all()                   : 一键构建全部 5 类文档集

【索引构建】
  本文件不再负责 FAISS/BM25 索引构建。索引构建统一由
  __003__create_neo4j_database/rebuild_faiss_single.py 完成
  (支持分块编码、断点续跑、OOM 保护)。

【外部依赖】
  - python-docx(可选): 仅解析 data/law/*.docx 官方原文时使用, 缺失时自动跳过
"""
import os          # 标准库: 路径拼接与目录扫描
import sys         # 标准库: stdout 编码重配(Windows GBK 环境兼容)
import re          # 标准库: 正则表达式, 条文/章节/段落边界匹配
import json        # 标准库: JSON 序列化, 写 *_docs.json
import glob        # 标准库: 通配符匹配文件路径(扫描 txt/docx)
import hashlib     # 标准库: md5, 生成确定性文档唯一键(去重用)

# 统一 stdout 为 UTF-8, 避免 Windows GBK 控制台打印 emoji/中文报错
if sys.stdout.encoding.lower().replace("-", "") != "utf8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 项目路径工具: 定位工程根目录
from common.path_utils import root_dir

# ======================================================================
# 常量: 目录与正则
# ======================================================================
# 知识库根目录 (输出 *_docs.json 的目录)
KB_DIR = os.path.join(root_dir, "data", "knowledge_base")

# 原始数据目录
LAW_TXT_DIR = os.path.join(root_dir, "data", "laws_txt")              # 法律 txt 文本(已从 __001__clawler/法律法规 迁移)
LAW_DOCX_DIR = os.path.join(root_dir, "data", "law")                 # 官方 docx 原文
CASE_TXT_DIR = os.path.join(root_dir, "data", "cases_txt")             # 裁判案例 txt(按案由分子目录, 已从 __001__clawler/裁判案例 迁移)
INDUSTRY_TXT_DIR = os.path.join(root_dir, "data", "industry_sources")  # 行业标准 txt
INTERPRETATION_TXT_DIR = os.path.join(root_dir, "data", "interpretations")  # 司法解释 txt
REGULATIONS_TXT_DIR = os.path.join(root_dir, "data", "regulations")  # 行政法规/部门规章 txt (新增)

# 条文起始正则: "第X条" 或 "第X条之X"(支持中文数字和阿拉伯数字)
ARTICLE_RE = re.compile(r"^(第(\d+|[〇零一二三四五六七八九十百千万两]+)条(?:之(\d+|[〇零一二三四五六七八九十]+))?)")
# 章节正则: "第一编/第二章/第三节" 或无编号的"附则"(支持中文数字和阿拉伯数字)
CHAPTER_RE = re.compile(r"^(第(\d+|[〇零一二三四五六七八九十百千万两]+)[编章节]|附\s*则)")


# ======================================================================
# 通用工具
# ======================================================================
def _norm(text: str) -> str:
    """清洗文本: 去空白/全角空格, 统一为紧凑字符串。"""
    if not text:
        return ""
    return re.sub(r"\s+", "", str(text)).strip()


def _doc_id(*parts) -> str:
    """生成确定性文档唯一键: md5(parts 拼接)前 16 位, 天然去重。"""
    raw = "|".join(str(p) for p in parts).encode("utf-8")
    return hashlib.md5(raw).hexdigest()[:16]


# ======================================================================
# 法律 txt 解析
# ======================================================================
def parse_law_txt(path: str) -> dict:
    """
    解析单部法律 txt 文件, 返回 {law_name, articles: [...]}。

    txt 格式(由 __002__crawl_law_database.py 写入):
      # 中华人民共和国民法典
      # 来源: xxx
      # 公布日期: 2020-05-28
      # 时效性: 现行有效
      [第一编 总则]
      第一条 为了保护民事主体的合法权益...
      第二条 民法调整平等主体的...

    Parameters
    ----------
    path : str
        法律 txt 文件绝对路径。

    Returns
    -------
    dict
        {law_name, effective_date, status, source, articles: [{article_no, chapter, content}]}
        文件不存在或解析失败返回空结构。
    """
    if not os.path.exists(path):
        return {"law_name": os.path.splitext(os.path.basename(path))[0], "articles": []}
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    law_name = os.path.splitext(os.path.basename(path))[0]   # 默认: 文件名即法律名
    effective_date = ""
    status = "现行有效"
    source = "本地txt"
    # 解析头部元信息(# 字段: 值)
    header_meta = {"公布日期": "", "施行日期": "", "时效性": "", "来源": "", "官方名称": ""}
    idx = 0
    while idx < len(lines) and lines[idx].strip().startswith("#"):
        line = lines[idx].strip().lstrip("#").strip()
        idx += 1
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            if key in header_meta:
                header_meta[key] = val.strip()
    # 用官方名称优先(如文件名为简称时)
    if header_meta["官方名称"]:
        law_name = header_meta["官方名称"]
    effective_date = header_meta["施行日期"]
    status = header_meta["时效性"] or "现行有效"
    source = header_meta["来源"] or f"本地txt:{os.path.basename(path)}"

    # 逐行切分条文
    articles = []
    current_article = None      # 当前条文 {article_no, chapter, content}
    current_chapter = ""        # 当前所属章节路径
    for line in lines[idx:]:
        line = line.strip()
        if not line:
            continue
        # 章节行: 更新章节路径
        if CHAPTER_RE.match(line):
            current_chapter = line
            continue
        # 条文起始行: 前一个条文入列, 开启新条文
        m = ARTICLE_RE.match(line)
        if m:
            if current_article and _norm(current_article["content"]):
                articles.append(current_article)
            current_article = {
                "article_no": m.group(1),
                "chapter": current_chapter,
                "content": line[m.end():].strip(),
            }
        elif current_article:
            # 续行: 追加到当前条文
            current_article["content"] += line
    if current_article and _norm(current_article["content"]):
        articles.append(current_article)

    return {
        "law_name": law_name,
        "effective_date": effective_date,
        "status": status,
        "source": source,
        "articles": articles,
    }


# ======================================================================
# 官方 docx 解析(可选, 依赖 python-docx)
# ======================================================================
def parse_law_docx(path: str) -> dict:
    """
    解析官方 docx 原文(如 data/law/中华人民共和国民法典_20200528.docx)。

    与 txt 解析输出结构完全一致, 便于下游统一处理。docx 缺失/依赖缺失时返回空。
    """
    try:
        from docx import Document
    except ImportError:
        print(f"  ⚠️ [KB] python-docx 未安装, 跳过 docx 解析: {path}")
        return {"law_name": os.path.splitext(os.path.basename(path))[0], "articles": []}
    try:
        doc = Document(path)
        lines = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    except Exception as e:
        print(f"  ⚠️ [KB] docx 解析失败({path}): {e}")
        return {"law_name": os.path.splitext(os.path.basename(path))[0], "articles": []}

    # 从文件名提取法律名: "中华人民共和国民法典_20200528.docx" -> "中华人民共和国民法典"
    law_name = os.path.basename(path).split("_")[0]
    articles = []
    current_article = None
    current_chapter = ""
    for line in lines:
        if CHAPTER_RE.match(line):
            current_chapter = line
            continue
        m = ARTICLE_RE.match(line)
        if m:
            if current_article and _norm(current_article["content"]):
                articles.append(current_article)
            current_article = {
                "article_no": m.group(1),
                "chapter": current_chapter,
                "content": line[m.end():].strip(),
            }
        elif current_article:
            current_article["content"] += line
    if current_article and _norm(current_article["content"]):
        articles.append(current_article)

    return {
        "law_name": law_name,
        "effective_date": "",
        "status": "现行有效",
        "source": f"官方docx:{os.path.basename(path)}",
        "articles": articles,
    }


# ======================================================================
# 案例 txt 解析
# ======================================================================
def parse_case_txt(path: str, case_type: str = "") -> dict:
    """
    解析单个案例 txt 文件, 返回案例文档。

    txt 格式(由 __001__clawler/cases_collector.py 写入):
      # 案件标题: 张某与某公司劳动争议纠纷案
      # 案号: (2024)京0105民初12345号
      # 案由: 劳动争议
      # 审理法院: 北京市朝阳区人民法院
      # 裁判日期: 2024-06-01
      # 数据来源: LLM生成(结构真实)

      【案情摘要】
      ...
      【判决结果】
      ...
      【引用法条】
      - 中华人民共和国劳动合同法第四十七条
    """
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    # 解析头部元信息
    meta = {}
    for m in re.finditer(r"^#\s*([^:：]+)[:：]\s*(.*)$", text, re.M):
        meta[m.group(1).strip()] = m.group(2).strip()
    # 提取正文三段(【案情摘要】/【判决结果】/【引用法条】)
    summary = ""
    judgment = ""
    cited_laws = []
    m_sum = re.search(r"【案情摘要】\s*(.*?)(?=【判决结果】|$)", text, re.S)
    m_jud = re.search(r"【判决结果】\s*(.*?)(?=【引用法条】|$)", text, re.S)
    m_law = re.search(r"【引用法条】\s*(.*)$", text, re.S)
    if m_sum:
        summary = m_sum.group(1).strip()
    if m_jud:
        judgment = m_jud.group(1).strip()
    if m_law:
        cited_laws = [l.strip().lstrip("- ").strip() for l in m_law.group(1).splitlines() if l.strip()]

    return {
        "case_title": meta.get("案件标题", os.path.splitext(os.path.basename(path))[0]),
        "case_no": meta.get("案号", ""),
        "case_type": meta.get("案由", case_type),
        "court_name": meta.get("审理法院", ""),
        "judge_date": meta.get("裁判日期", ""),
        "case_summary": summary,
        "judgment": judgment,
        "cited_laws": cited_laws,
        "source": meta.get("数据来源", "本地txt"),
    }


# ======================================================================
# 行业标准 txt 解析(与法律 txt 格式一致: 章节 + 第X条)
# ======================================================================
def parse_industry_txt(path: str) -> dict:
    """
    解析行业标准 txt 文件(如 data/industry_sources/住建部标准.txt), 返回条款列表。

    行业标准 txt 的格式与法律 txt 兼容(头部 # 注释 + [章节] + 第X条), 因此直接复用
    parse_law_txt 的解析逻辑, 仅把字段名映射为行业标准命名(standard_name/standard_no)。
    """
    parsed = parse_law_txt(path)
    if not parsed.get("articles"):
        return parsed
    # 字段重命名: law_name -> standard_name, article_no -> standard_no
    return {
        "standard_name": parsed["law_name"],
        "source": parsed["source"],
        "articles": [
            {
                "standard_no": a["article_no"],
                "section": a["chapter"],
                "content": a["content"],
            }
            for a in parsed["articles"]
        ],
    }


# ======================================================================
# 司法解释 txt 解析(与法律 txt 格式一致)
# ======================================================================
def parse_interpretation_txt(path: str) -> dict:
    """解析司法解释 txt(格式与法律 txt 一致), 字段命名为 interpretation_name/article_no。"""
    parsed = parse_law_txt(path)
    if not parsed.get("articles"):
        return parsed
    return {
        "interpretation_name": parsed["law_name"],
        "effective_date": parsed["effective_date"],
        "status": parsed["status"],
        "source": parsed["source"],
        "articles": [
            {"article_no": a["article_no"], "chapter": a["chapter"], "content": a["content"]}
            for a in parsed["articles"]
        ],
    }


# ======================================================================
# 各数据源构建函数
# ======================================================================
def build_laws_docs() -> int:
    """
    构建法律法规知识库: 合并 txt 与 docx 两路数据, 输出 laws_docs.json。

    去重策略: 以 (law_name|article_no) 为唯一键, docx(官方原文)优先于 txt(可能 LLM 补全),
    同一法条只保留一份。
    """
    docs = {}
    # 1) 扫描法律 txt 目录
    if os.path.isdir(LAW_TXT_DIR):
        for fpath in glob.glob(os.path.join(LAW_TXT_DIR, "*.txt")):
            parsed = parse_law_txt(fpath)
            for a in parsed["articles"]:
                key = _doc_id(parsed["law_name"], a["article_no"])
                docs[key] = {
                    "doc_id": key,
                    "law_name": parsed["law_name"],
                    "article_no": a["article_no"],
                    "chapter": a.get("chapter", ""),
                    "content": a.get("content", ""),
                    "effective_date": parsed.get("effective_date", ""),
                    "status": parsed.get("status", "现行有效"),
                    "source": parsed.get("source", "本地txt"),
                    "search_text": f"{parsed['law_name']} {a.get('article_no','')} {a.get('chapter','')} {a.get('content','')}",
                }
    # 2) 扫描官方 docx 目录(可选, 覆盖同名法条为官方原文)
    if os.path.isdir(LAW_DOCX_DIR):
        for fpath in glob.glob(os.path.join(LAW_DOCX_DIR, "*.docx")):
            parsed = parse_law_docx(fpath)
            if not parsed.get("articles"):
                continue
            for a in parsed["articles"]:
                key = _doc_id(parsed["law_name"], a["article_no"])
                docs[key] = {   # docx 覆盖 txt(官方原文优先)
                    "doc_id": key,
                    "law_name": parsed["law_name"],
                    "article_no": a["article_no"],
                    "chapter": a.get("chapter", ""),
                    "content": a.get("content", ""),
                    "effective_date": parsed.get("effective_date", ""),
                    "status": parsed.get("status", "现行有效"),
                    "source": parsed.get("source", "官方docx"),
                    "search_text": f"{parsed['law_name']} {a.get('article_no','')} {a.get('chapter','')} {a.get('content','')}",
                }
    # 3) 写入 JSON
    _write_docs_json("laws", list(docs.values()))
    return len(docs)


def build_cases_docs() -> int:
    """构建裁判案例知识库: 扫描 裁判案例/{案由}/*.txt, 输出 cases_docs.json。"""
    docs = {}
    if os.path.isdir(CASE_TXT_DIR):
        for case_type_dir in os.listdir(CASE_TXT_DIR):
            sub_dir = os.path.join(CASE_TXT_DIR, case_type_dir)
            if not os.path.isdir(sub_dir):
                continue
            for fpath in glob.glob(os.path.join(sub_dir, "*.txt")):
                case = parse_case_txt(fpath, case_type=case_type_dir)
                if not case.get("case_title"):
                    continue
                key = _doc_id(case["case_title"], case["case_no"])
                docs[key] = {
                    "doc_id": key,
                    **case,
                    "search_text": f"{case['case_title']} {case['case_type']} {case['case_summary']} {case['judgment']}",
                }
    _write_docs_json("cases", list(docs.values()))
    return len(docs)


def build_industry_docs() -> int:
    """构建行业标准知识库: 扫描 data/industry_sources/*.txt, 输出 industry_docs.json。"""
    docs = {}
    if os.path.isdir(INDUSTRY_TXT_DIR):
        for fpath in glob.glob(os.path.join(INDUSTRY_TXT_DIR, "*.txt")):
            parsed = parse_industry_txt(fpath)
            for a in parsed.get("articles", []):
                key = _doc_id(parsed["standard_name"], a["standard_no"])
                docs[key] = {
                    "doc_id": key,
                    "standard_name": parsed["standard_name"],
                    "standard_no": a["standard_no"],
                    "section": a.get("section", ""),
                    "content": a.get("content", ""),
                    "source": parsed.get("source", "行业txt"),
                    "search_text": f"{parsed['standard_name']} {a.get('standard_no','')} {a.get('section','')} {a.get('content','')}",
                }
    _write_docs_json("industry", list(docs.values()))
    return len(docs)


def build_regulations_docs() -> int:
    """构建行政法规/部门规章/地方性法规知识库: 扫描 data/regulations/*.txt, 输出 regulations_docs.json。

    文档结构与 laws 一致 (law_name/article_no/content), 另带 law_level 标签
    (administrative_regulation / department_rule) 供冲突消解确定性规则消费。
    """
    docs = {}
    if os.path.isdir(REGULATIONS_TXT_DIR):
        for fpath in glob.glob(os.path.join(REGULATIONS_TXT_DIR, "*.txt")):
            parsed = parse_law_txt(fpath)
            law_name = parsed.get("law_name", "")
            # law_level 推断: 条例/地方性法规 → 行政法规, 其余 → 部门规章
            if any(k in law_name for k in ("条例", "地方", "经济特区", "自治区", "实施条例", "自治条例")):
                level = "administrative_regulation"
            else:
                level = "department_rule"
            for a in parsed.get("articles", []):
                key = _doc_id(law_name, a["article_no"])
                docs[key] = {
                    "doc_id": key,
                    "law_name": law_name,
                    "article_no": a["article_no"],
                    "chapter": a.get("chapter", ""),
                    "content": a.get("content", ""),
                    "effective_date": parsed.get("effective_date", ""),
                    "status": parsed.get("status", "现行有效"),
                    "source": parsed.get("source", "本地txt"),
                    "law_level": level,
                    "data_source_authority": "internal_private",
                    "search_text": f"{law_name} {a.get('article_no','')} {a.get('chapter','')} {a.get('content','')}",
                }
    _write_docs_json("regulations", list(docs.values()))
    return len(docs)


def build_interpretations_docs() -> int:
    """构建司法解释知识库: 扫描 data/interpretations/*.txt, 输出 interpretations_docs.json。"""
    docs = {}
    if os.path.isdir(INTERPRETATION_TXT_DIR):
        for fpath in glob.glob(os.path.join(INTERPRETATION_TXT_DIR, "*.txt")):
            parsed = parse_interpretation_txt(fpath)
            for a in parsed.get("articles", []):
                key = _doc_id(parsed["interpretation_name"], a["article_no"])
                docs[key] = {
                    "doc_id": key,
                    "interpretation_name": parsed["interpretation_name"],
                    "article_no": a["article_no"],
                    "chapter": a.get("chapter", ""),
                    "content": a.get("content", ""),
                    "effective_date": parsed.get("effective_date", ""),
                    "status": parsed.get("status", "现行有效"),
                    "source": parsed.get("source", "司法解释txt"),
                    "search_text": f"{parsed['interpretation_name']} {a.get('article_no','')} {a.get('chapter','')} {a.get('content','')}",
                }
    _write_docs_json("interpretations", list(docs.values()))
    return len(docs)


# ======================================================================
# 输出与索引
# ======================================================================
def _write_docs_json(corpus: str, docs: list):
    """
    把文档列表写入 data/knowledge_base/{corpus}_docs.json, 并更新元数据统计。
    """
    os.makedirs(KB_DIR, exist_ok=True)
    out_path = os.path.join(KB_DIR, f"{corpus}_docs.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(docs, f, ensure_ascii=False, indent=1)
    print(f"  ✅ [KB] {corpus}: {len(docs)} 条 -> {out_path}")


# ======================================================================
# 一键构建入口
# ======================================================================
def build_all():
    """
    一键构建全部知识库(法规+行政法规+案例+行业+司法解释), 输出 *_docs.json。

    用法: python -m __001__clawler.kb_builder

    注意: 本函数只生成结构化文档 JSON, 不构建索引。
    FAISS/BM25 索引构建请使用:
      python -m __003__create_neo4j_database.rebuild_faiss_single
    """
    print("=" * 60)
    print("📚 知识库构建器启动 (仅生成结构化文档, 不构建索引)")
    print("=" * 60)
    n_laws = build_laws_docs()
    n_regs = build_regulations_docs()
    n_cases = build_cases_docs()
    n_industry = build_industry_docs()
    n_ints = build_interpretations_docs()
    print("-" * 60)
    print(f"  法规条文: {n_laws} 条")
    print(f"  行政法规/部门规章: {n_regs} 条")
    print(f"  裁判案例: {n_cases} 个")
    print(f"  行业标准: {n_industry} 条")
    print(f"  司法解释: {n_ints} 条")
    print("-" * 60)
    print(f"✅ 文档 JSON 构建完成, 输出目录: {KB_DIR}")
    print("    如需构建 FAISS 索引, 请运行:")
    print("    python -m __003__create_neo4j_database.rebuild_faiss_single")
    print("=" * 60)


if __name__ == "__main__":
    # 直接运行时执行一键构建
    build_all()
