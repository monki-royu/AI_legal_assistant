"""citation 结构化元数据回填工具.

在检索层 (retrieval_entity_recall_node / retrieval_precision_filter_node) 产出 citation 时,
基于 citation 中的 source_id (知识源标识) 和标题/正文, 回填结构化元数据:

    - law_level:            法律层级 (优先用 source_id 直接映射, 标题启发式兜底)
                            枚举: law(法律) / administrative_regulation(行政法规)
                                  / department_rule(部门规章) / judicial_interpretation(司法解释)
                                  / other(其他/未知)
    - case_level:           案例层级 (仅 cases 知识源才推断)
                            枚举: guiding(指导性案例) / gazette(公报案例)
                                  / typical(典型案例) / ordinary(普通案例)
    - effective_date:       生效/发布日期 (str "YYYY-MM-DD" 或 "YYYY", 提取不到为 None)
    - data_source_authority: 数据源权威
                            枚举: official_external(权威外部/北大法宝) / internal_private(本地私有)
                                  / graph(图谱) / credit(资信) / unknown

枚举与 nodes/retrieval_nodes/retrieval_precision_filter_node.py 中
_authority_filter 的 data_source_authority 取值保持一致, 避免双重标准.

核心设计原则: source_id (laws/regulations/interpretations/industry_sources/cases)
是知识源的权威标识, 应作为 law_level/case_level 的第一信息源, 而非靠标题文本瞎猜.
标题启发式仅在 source_id 缺失时作为兜底.
"""

import re

# ---------------------------------------------------------------------------
# 知识源 → 法律层级的直接映射 (核心: 用 source_id 直接定位 law_level)
# ---------------------------------------------------------------------------
# 5 个知识源与 law_level 的对应关系基于 data/ 目录的实际结构:
#   laws/               → law (法律, 全国人大及常委会制定, 效力最高)
#   regulations/        → administrative_regulation (行政法规, 国务院制定)
#   interpretations/     → judicial_interpretation (司法解释, 两高制定)
#   industry_sources/   → department_rule (部门规章/行业标准, 部委制定)
#   cases/              → None (案例无法律层级, 改用 case_level)
_SOURCE_TO_LAW_LEVEL = {
    "laws": "law",
    "regulations": "administrative_regulation",
    "interpretations": "judicial_interpretation",
    "industry_sources": "department_rule",
    # cases 不在此映射, 案例用 case_level 而非 law_level
}

# 知识源 → 案例层级的直接映射 (仅 cases 源使用)
_SOURCE_TO_CASE_LEVEL = {
    "cases": "ordinary",  # 默认普通案例, 标题中有指导性/公报/典型时再升级
}

# ---------------------------------------------------------------------------
# 法律效力层级 (用于上下位法冲突判定: 法律 > 行政法规 > 部门规章 > 司法解释)
#    rank 越大效力越高, 冲突时高 rank 优先 (低位被标记 superseded)
# ---------------------------------------------------------------------------
LAW_LEVEL_RANK = {
    "law": 4,                      # 法律 (全国人大及常委会)
    "administrative_regulation": 3,  # 行政法规 (国务院)
    "department_rule": 2,          # 部门规章 (部委)
    "judicial_interpretation": 1,  # 司法解释 (两高)
    "other": 0,                    # 其他/未识别
}

# 案例层级 (用于案例层级冲突: 指导性 > 公报 > 典型 > 普通)
CASE_LEVEL_RANK = {
    "guiding": 4,   # 指导性案例
    "gazette": 3,   # 公报案例
    "typical": 2,   # 典型案例
    "ordinary": 1,  # 普通案例
}

# ---------------------------------------------------------------------------
# 从 citation 的 source 字段提取 source_id
# source 字段格式: "graph::laws" / "faiss::regulations" / "keyword::cases" 等
# ---------------------------------------------------------------------------
def _extract_source_id(source: str) -> str:
    """从 source 字段中提取知识源 id.

    格式: "召回通道::知识源id", 如 "graph::laws" → "laws".
    若不含 "::", 则直接用整串 (兼容只有 source_id 的情况).
    """
    s = (source or "").strip()
    if "::" in s:
        return s.split("::", 1)[1].strip().lower()
    return s.lower()


def _infer_law_level(title: str, article_no: str, content: str) -> str:
    """从标题/文号/正文推断法律层级. 返回 LAW_LEVEL_RANK 的 key 或 'other'."""
    t = (title or "").strip()
    a = (article_no or "").strip()
    c = (content or "").strip()
    low = (t + " " + a + " " + c).lower()

    # 司法解释 (两高): 最高优先级识别, 避免被"规定/办法"误判为部门规章
    if ("司法解释" in t or "法释" in a or "法发" in a
            or "最高人民法院" in t or "最高人民检察院" in t
            or "最高法" in t or "最高检" in t):
        return "judicial_interpretation"

    # 行政法规 (国务院): 条例 / 实施细则 / 国务院令
    if ("条例" in t or "实施细则" in t or "国务院令" in t
            or "暂行条例" in t or re.search(r"国务院.*规定", t)):
        return "administrative_regulation"

    # 部门规章 (部委): 规定 / 办法 / 部委令
    if ("部门规章" in t or re.search(r"部(?:令|规定|办法)", t)
            or "暂行办法" in t or "管理规定" in t or "实施办法" in t
            or (a and "〔" in a and "〕" in a and "号" in a and "法释" not in a)):
        return "department_rule"

    # 法律 (全国人大): 通常以"法"结尾且无"办法/条例/规定"等
    if t.endswith("法") or "法典" in t or "基本法" in t or "全国人大" in t:
        return "law"

    # 兜底: 含"法"字且不像部门规章/行政法规
    if "法" in t and "办法" not in t and "条例" not in t:
        return "law"

    return "other"


def _infer_case_level(title: str, content: str, source_id: str = "") -> str:
    """从标题/正文推断案例层级. 返回 CASE_LEVEL_RANK 的 key 或 'ordinary'.

    若 source_id 已知且不是 cases, 直接返回 None (非案例源不应有 case_level).
    """
    # 非 cases 源不推断 case_level (节省计算, 避免误标)
    if source_id and source_id not in _SOURCE_TO_CASE_LEVEL:
        return None

    t = (title or "").strip()
    c = (content or "").strip()
    low = (t + " " + c).lower()

    # 指导性案例: 最高优先级识别
    if "指导性案例" in t or "指导案例" in t or "最高人民法院指导" in t:
        return "guiding"
    # 公报案例
    if "公报案例" in t or "最高人民法院公报" in t or "公报" in t:
        return "gazette"
    # 典型案例
    if "典型案例" in t or "典型案件" in t:
        return "typical"
    # 裁判文书/普通案例默认
    return "ordinary"


_DATE_PATTERNS = [
    re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"),  # 2021年1月1日
    re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})"),                    # 2021-01-01
    re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月"),                   # 2021年1月
    re.compile(r"(\d{4})\s*年"),                                    # 2021年
]


def _extract_effective_date(title: str, article_no: str, content: str):
    """提取生效/发布日期. 优先从文号年份(法释〔2023〕)推断, 其次正文'自XXXX年X月X日起施行'."""
    a = (article_no or "").strip()
    c = (content or "").strip()
    t = (title or "").strip()

    # 文号年份: 法释〔2023〕X号 / 〔2021〕X号
    m = re.search(r"[〔\[](\d{4})[〕\]]", a)
    if m:
        return m.group(1)  # 仅年份, 作为发布年

    # 正文"自 XXXX 年 X 月 X 日起施行" / "XXXX年X月X日公布"
    for pat in _DATE_PATTERNS:
        m = pat.search(c)
        if not m:
            m = pat.search(t)
        if m:
            try:
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                return f"{y:04d}-{mo:02d}-{d:02d}"
            except (IndexError, ValueError):
                return m.group(1)  # 仅年份
    return None


def _infer_data_source_authority(source: str) -> str:
    """推断数据源权威. 枚举与冲突节点 _classify_citation 保持一致."""
    s = (source or "").strip()
    low = s.lower()
    if "北大法宝" in s or "beida" in low or "法宝" in s:
        return "official_external"
    if "企查查" in low or "资信" in low:
        return "credit"
    if "图谱" in s or "neo4j" in low:
        return "graph"
    if "私有" in s or "企业内部" in s or "行业" in s or "industry" in low:
        return "internal_private"
    # L1/L2 本地库 (laws/cases/regulations/interpretations/txt/知识图谱兜底)
    return "internal_private"


# 法律领域 → 关键词 (与 data/process_law_library.py 的 DOMAIN_RULES 保持一致,
# 供未入库标签的 citation 兜底推断; 顺序敏感, 先匹配更具体领域)
_DOMAIN_RULES = [
    ("合同",   ["合同", "买卖", "租赁", "借贷", "承揽", "保证", "担保法", "定金"]),
    ("劳动社保", ["劳动", "劳动合同", "社会保险", "社保", "工资", "女职工"]),
    ("公司企业", ["公司", "企业", "合伙", "独资", "个体户", "国有企业"]),
    ("证券金融", ["证券", "金融", "银行", "保险法", "期货", "基金", "票据"]),
    ("破产",   ["破产"]),
    ("婚姻家庭", ["婚姻", "继承", "收养", "家庭"]),
    ("物权担保", ["物权", "担保", "抵押", "质押", "房地产"]),
    ("侵权责任", ["侵权", "损害赔偿", "人身损害", "医疗损害"]),
    ("知识产权", ["专利", "商标", "著作权", "知识产权"]),
    ("行政诉讼", ["行政", "复议", "行政处罚", "行政许可"]),
    ("民事诉讼", ["民事诉讼法", "民诉"]),
    ("刑事诉讼", ["刑事诉讼法", "刑诉"]),
    ("交通运输", ["交通", "道路", "车辆", "驾驶证", "铁路", "航空", "机动车"]),
    ("食品安全", ["食品", "农产品"]),
    ("药品医疗", ["药品", "医疗", "卫生", "疫苗"]),
    ("安全生产", ["安全生产", "消防", "矿山", "建筑法"]),
    ("税收财政", ["税收", "税务", "发票", "财政", "会计", "审计"]),
    ("未成年人", ["未成年人", "妇女", "老年人", "残疾人", "母婴"]),
    ("执行程序", ["执行", "查封", "冻结", "拍卖"]),
    ("环境保护", ["环境", "污染", "生态"]),
    ("刑事治安", ["刑法", "刑事", "犯罪", "治安", "公安", "警察", "监狱"]),
    ("宪法",   ["宪法"]),
]


def _infer_legal_domain(title: str) -> str:
    """从标题推断法律领域 (默认 '其他法律'). 与入库脚本枚举一致."""
    t = (title or "").strip()
    for domain, kws in _DOMAIN_RULES:
        if any(k in t for k in kws):
            return domain
    if "法" in t:
        return "民事综合" if "民法" in t else "其他法律"
    return "其他"


def enrich_citation_meta(c: dict) -> dict:
    """给单条 citation 回填结构化元数据 (原地修改并返回). 不抛异常.

    回填字段:
        law_level, case_level, effective_date, data_source_authority, legal_domain

    核心逻辑 (优先级从高到低):
        1. 若 citation 已带 law_level / case_level (入库时已标注), 直接保留, 不覆盖
        2. 若 citation 带 source_id (或可从 source 字段提取), 用 source_id 直接映射:
           - laws → law_level="law" (法律)
           - regulations → law_level="administrative_regulation" (行政法规)
           - interpretations → law_level="judicial_interpretation" (司法解释)
           - industry_sources → law_level="department_rule" (部门规章)
           - cases → case_level="ordinary" (案例默认普通, 标题中有指导性/公报/典型时升级)
        3. source_id 缺失时, 用标题启发式推断 _infer_law_level / _infer_case_level
        4. 其他字段 (effective_date / data_source_authority / legal_domain) 同理

    这样做的好处: 避免把"行政法规"误判为"部门规章"(title 启发式的常见错误),
    也避免案例被打上 law_level (案例无法律层级概念).
    """
    if not isinstance(c, dict):
        return c

    # 提取 citation 的核心字段
    title = str(c.get("title", ""))
    article_no = str(c.get("article_no", ""))
    content = str(c.get("content", ""))
    source = str(c.get("source", ""))

    # ---- 第一步: 从 citation 中提取 source_id ----
    # citation 可能直接带 source_id 字段, 也可能需要从 source 字段解析
    source_id = c.get("source_id", "") or _extract_source_id(source)

    # ---- 第二步: 回填 law_level (用 source_id 直接映射, 而非标题启发式) ----
    if c.get("law_level"):
        pass  # 入库时已标注, 保留
    elif source_id and source_id in _SOURCE_TO_LAW_LEVEL:
        # 核心路径: 用 source_id 直接定位 law_level, 不依赖标题文本
        c["law_level"] = _SOURCE_TO_LAW_LEVEL[source_id]
    else:
        # 兜底路径: source_id 缺失时, 才用标题启发式推断
        c["law_level"] = _infer_law_level(title, article_no, content)

    # ---- 第三步: 回填 case_level (仅 cases 源才推断) ----
    if c.get("case_level"):
        pass  # 入库时已标注, 保留
    elif source_id and source_id in _SOURCE_TO_CASE_LEVEL:
        # 核心路径: cases 源默认 ordinary, 标题中有关键词时升级
        base_level = _SOURCE_TO_CASE_LEVEL[source_id]
        # 标题中有指导性/公报/典型关键词时, 升级 case_level
        title_level = _infer_case_level(title, content, source_id)
        c["case_level"] = title_level if title_level else base_level
    elif source_id and source_id not in _SOURCE_TO_CASE_LEVEL:
        # 非 cases 源, 不设 case_level (避免误标)
        c["case_level"] = None
    else:
        # 兜底路径: source_id 缺失时, 按标题推断
        c["case_level"] = _infer_case_level(title, content, source_id)

    # ---- 第四步: 生效日期 (保留入库标签, 不做启发式推断避免误判) ----
    if "effective_date" not in c:
        c["effective_date"] = ""

    # ---- 第五步: 数据源权威 (保留入库标签, 否则启发式) ----
    if not c.get("data_source_authority"):
        c["data_source_authority"] = _infer_data_source_authority(source)

    # ---- 第六步: 法律领域/事项 (保留入库标签, 否则从标题推断) ----
    if not c.get("legal_domain"):
        c["legal_domain"] = _infer_legal_domain(title)

    return c


def enrich_citations(citations: list) -> list:
    """批量回填; 原地修改并返回同一列表. 容忍非 dict 元素."""
    for c in citations:
        if isinstance(c, dict):
            enrich_citation_meta(c)
    return citations
