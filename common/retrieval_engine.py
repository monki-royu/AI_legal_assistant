# -*- coding: utf-8 -*-
"""
【文件总览 · 检索引擎 · RetrievalEngine】
=========================================

一、这个文件是干什么的？(给完全新手的一句话解释)
------------------------------------------------
这个文件是整个"法智引擎"(AI 法律助手)项目里负责"查资料"的核心代码。
当用户问"违约金的法定上限是多少？"、审核合同需要查"建设工程相关法规"、
或者需要找"类似案例"时, 都是靠本文件把本地知识库(法律原文、裁判案例、
行业标准、司法解释)翻出来, 整理成一份"带来源、能溯源、可打分"的检索报告。

二、完整执行流程(一条检索请求从进到出, 一共走 4 大步 + 2 小步)
--------------------------------------------------------------
【第0步 · 数据源挂载(横向)】:
  根据 task_type(任务类型)和 contract_type(合同类型), 决定本次要查哪些"数据源"。
  数据源一共有 4 个: laws(法律法规) / cases(裁判案例) / industry(行业标准) /
  interpretations(司法解释)。法律法规 + 裁判案例是"任何任务必查"的基础;
  合同审核类任务额外追加行业标准; 问答/检索/文书类任务额外追加司法解释。

【第1步 · 单源纵向降级检索】:
  对每个被挂载的数据源, 依次尝试 3 种检索方式(从"最聪明"到"最笨"):
    第1级 FAISS 向量语义检索(靠神经网络理解"意思相近");
    第2级 BM25 稀疏检索(靠关键词 + 词频统计打分);
    第3级 关键词子串扫描(纯字符串包含判断, 保证永远有结果)。
  任何一级失败或结果不够, 就自动降到下一级 —— 这叫【纵向降级】(graceful degradation),
  目的是"检索永不抛异常、永不返回空", 符合法律场景"可用性优先"的要求。

【第2步 · RRF 多源融合】:
  把 4 个数据源查到的结果合并成一个大列表。因为各路打分尺度完全不同
  (FAISS 是余弦相似度 -1~1, BM25 是无上界的正数), 直接比分数不公平,
  所以用【RRF(Reciprocal Rank Fusion)倒数排名融合】: 只看"排名"不看"分数",
  排名越靠前贡献越大, 从而公平地合并多路结果。

【第3步 · 法律效力冲突消解】:
  对法规/司法解释类的引用, 套用 4 套硬编码规则处理"打架"的法条:
    规则1 上位法优于下位法(宪法 > 法律 > 行政法规 > ...);
    规则2 新法优于旧法(施行日期新的优先);
    规则3 特别法优于一般法(名称含"特别/专项/专门"者优先);
    规则4 已废止/已修改法条降权(把分数乘 0.3, 并打上"⚠️已废止"标注)。
  注意: 消解只做"排序 + 标注", 不删除任何条目 —— 保留【可追溯性】。

【第4步 · 质量门禁 + MCP 兜底 + 人工介入】:
  计算一个 0~1 的【质量分 quality_score】(覆盖率 40% + 相关性 40% + 时效性 20%)。
  若 >= 0.85(QUALITY_GATE)视为达标, 直接返回结果 —— 这就是"达标跳过付费接口"的降本设计;
  若不达标, 尝试调用【北大法宝 MCP 付费接口】补充权威结果;
  若 MCP 也救不回来, 就置起【人工介入标志 need_human_intervention】,
  由上游(LangGraph 重试环)最多自动重试 MAX_RETRY=3 轮, 再交给人工处理。

三、谁在依赖这个文件？(调用方一览)
-----------------------------------
1. 前端"法律检索"页面 / FastAPI 后端 / 命令行脚本: 直接调用 engine.search();
2. 各智能体(合同审核、合规审查、法律问答、文书生成): 通过
   retrieval_base_layer_node 等"薄封装节点"间接调用本引擎;
3. 法规查询页 / 案例检索页: 调用 search_laws() / search_cases() 等单源便捷方法;
4. 前端"数据源状态"展示: 调用 kb_stats() 获取各源文档数量。

四、数据从哪来？(数据目录约定)
------------------------------
- data/knowledge_base/{laws,cases,industry,interpretations}_docs.json   : 结构化文档集(JSON 数组)
- data/knowledge_base/index/{corpus}_faiss.index + {corpus}_id2text.pkl  : FAISS 向量索引 + 文本映射
- data/knowledge_base/index/{corpus}_bm25.pkl                            : BM25 稀疏索引缓存
- 上述文件由 __001__clawler/kb_builder.py 构建; 文件缺失时引擎自动降级为关键词扫描, 不会崩溃。

五、外部依赖(全部可选, 缺失即自动降级)
--------------------------------------
- faiss-cpu / sentence-transformers(经 common.embedding_model) —— 缺失则跳过向量检索
- common.qichacha_client(QiChaChaClient) —— 缺失则跳过资信数据源
- common.neo4j_manager(Neo4jClient) —— 缺失则跳过图谱数据源

六、设计哲学(为什么这么写)
--------------------------
把"检索"从 LangGraph 的"节点"中彻底抽离: 节点只负责读写状态(编排),
本引擎只负责检索计算(算法)。这样前端独立调用与智能体内部调用走同一条代码路径,
保证行为一致、便于维护、可单测。本文件本身也是可执行的:
    python -m common.retrieval_engine "违约金上限"
即可做 CLI 自测。
"""
# ==========================================================================
# 新手阅读路线图(按顺序读, 10 分钟看懂全文件)
# ==========================================================================
#   第1段 import(第 100 行附近): 引入 Python 标准库与项目内部模块;
#   第2段 常量区: 数据源名字 / 法律效力层级表 / 冲突消解规则 / 质量门禁阈值;
#   第3段 class Bm25Index: 纯 Python 实现的"关键词打分"检索器(最底层算法);
#   第4段 class KnowledgeBase: 知识库"搬运工", 负责读文件、建索引、做单源检索;
#   第5段 模块级函数: resolve_law_conflicts(冲突消解) 与 rrf_fusion(多源融合);
#   第6段 class RetrievalEngine: 总指挥, 对外只有一个主方法 search();
#   第7段 engine = RetrievalEngine(): 全局单例, 所有调用方共享同一个实例;
#   第8段 __main__: CLI 自测入口。
# ==========================================================================
import os          # 标准库 os: 用于路径拼接(os.path.join)与文件存在性判断(os.path.exists)
import re          # 标准库 re: 正则表达式模块, 用于关键词切分、标准编号提取、条款/层级匹配
import math        # 标准库 math: 数学函数, BM25 打分公式里的 log 对数运算要用
import json        # 标准库 json: JSON 序列化/反序列化, 用来读取知识库文档集与生成兜底 key
import pickle      # 标准库 pickle: 把 Python 对象(如 Bm25Index)存成二进制文件, 用于索引缓存
import hashlib     # 标准库 hashlib: 提供 md5 摘要, 用于生成"确定性去重键"(同一文档永远得到同一 key)
from collections import Counter, defaultdict  # 标准库 collections: Counter 做词频统计, defaultdict 构建倒排索引

# 项目内部模块: path_utils 提供 root_dir(工程根目录的绝对路径), 检索引擎据此定位知识库文件
from common.path_utils import root_dir

# ======================================================================
# 常量区: 数据源名称 / 法律效力层级 / 冲突消解规则 / 质量门禁阈值
# ======================================================================
# 下面的常量把"魔法字符串"变成有名字的符号, 全文件统一引用,
# 避免到处写死 "laws" / "cases" 等字符串导致拼写错误难以排查。
# 【数据源命名约定】: 常量值 = 知识库目录下的文件前缀, 二者一一对应。
CORPUS_LAWS = "laws"               # 【数据源: 法律法规】基础必查项, 对应文件 laws_docs.json
CORPUS_CASES = "cases"             # 【数据源: 裁判案例】基础必查项, 对应文件 cases_docs.json
CORPUS_INDUSTRY = "industry"       # 【数据源: 行业标准】按合同类型横向挂载, 对应 industry_docs.json
CORPUS_INTERPRETATIONS = "interpretations"  # 【数据源: 司法解释】增强层, 对应 interpretations_docs.json

# 【法律效力层级表】(冲突消解规则1: 上位法优于下位法)
# 原理: 不同层级法律的"权力大小"不同, 冲突时应当以效力更高的法条为准。
# 实现: 用数字表示层级, 数字越小效力越高; 冲突排序时保留层级数字更小者。
_LAW_LEVEL = {
    "宪法": 0,                          # 0: 宪法 —— 国家根本大法, 效力最高
    "法律": 1,                          # 1: 法律 —— 全国人大及其常委会制定的法律, 如《民法典》
    "行政法规": 2,                      # 2: 行政法规 —— 国务院制定的, 如《劳动合同法实施条例》
    "监察法规": 2,                      # 2: 监察法规 —— 国家监委制定的, 与行政法规同级
    "司法解释": 2,                      # 2: 司法解释 —— 最高法/最高检的司法解释, 效力相当于行政法规层级
    "地方性法规": 3,                    # 3: 地方性法规 —— 省级人大制定的, 如《北京市xx条例》
    "部门规章": 4,                      # 4: 部门规章 —— 国务院各部门制定的, 如住建部规章
    "地方政府规章": 5,                  # 5: 地方政府规章 —— 省级政府制定的
    "行业标准": 6,                      # 6: 行业标准/技术规范 —— 效力最低, 仅作参考
    "其他": 9,                          # 9: 未识别层级的兜底 —— 优先级最低
}
# 【层级识别正则表】(规则1的配套工具)
# 每一项是 (正则模式, 识别出的层级名) 二元组; 按顺序逐条匹配, 先命中先得。
# 匹配顺序很重要: "宪法"这种最特殊的模式必须放在最前面, 否则可能被后面的宽泛模式抢走。
_LAW_LEVEL_PATTERNS = [
    (r"宪法", "宪法"),                                  # 名称里含"宪法"二字 -> 宪法(如《宪法修正案》)
    (r"^中华人民共和国.*法$", "法律"),                  # 全称形如"中华人民共和国XX法" -> 法律(如《民法典》)
    (r"条例|规定|办法|细则|规则|决定", "行政法规"),      # 行政法规常用后缀词, 命中即归为行政法规层级
    (r"解释|批复|纪要", "司法解释"),                    # 司法解释常用后缀词(如《xx解释》《xx纪要》)
    (r"标准|规范|规程|导则", "行业标准"),               # 行业标准常用后缀词(如 GB50300 验收规范)
]

# 【4 套法律效力冲突消解规则】(面试/设计文档中的明确设计依据, 实现见 resolve_law_conflicts)
#   规则1: 上位法优于下位法 —— 依据 _LAW_LEVEL 层级表, 层级数字小者优先;
#   规则2: 新法优于旧法   —— 同一效力层级下, 施行日期(effective_date)更新的法条优先;
#   规则3: 特别法优于一般法 —— 名称含"特别/专项/专门/实施办法"等标识的特别法优先;
#   规则4: 已废止/已修改法条降权 —— 命中已废止特征或 status 非"现行有效"时, 分数乘 0.3 并标注。

# 【质量门禁阈值】: 检索质量分达到 0.85 即视为达标, 达标后跳过付费的北大法宝 MCP 接口(降本核心设计)
QUALITY_GATE = 0.85
# 【最大自动重试轮数】: 质量分不达标时, 上游 LangGraph 重试环最多自动重试 3 轮, 仍不达标触发人工介入
MAX_RETRY = 3

# 【知识库根目录】: 由 kb_builder.py 构建脚本生成, root_dir 是工程根目录的绝对路径
_KB_DIR = os.path.join(root_dir, "data", "knowledge_base")        # 例: <工程根>/data/knowledge_base
_KB_INDEX_DIR = os.path.join(_KB_DIR, "index")                    # 索引子目录, 存放 FAISS / BM25 缓存文件


# ======================================================================
# Bm25Index: 纯 Python 稀疏检索(倒排索引 + BM25 打分)
# ======================================================================
class Bm25Index:
    """
    【功能】
    纯 Python 实现的 BM25 稀疏检索索引(不依赖 rank_bm25 第三方包)。
    BM25 是信息检索领域最经典的"关键词打分"算法之一, 擅长处理"用户输入的词
    与文档中的词精确匹配"的场景。本类负责: ① 建立倒排索引(词 -> 文档列表);
    ② 对查询命中的文档按 BM25 公式打分; ③ 返回按分数降序的文档列表。

    【原理讲解(给新手)】
    - 倒排索引(inverted index): 类似于书末尾的"索引页"。普通索引是"文档 -> 词",
      倒排索引反过来存"词 -> 哪些文档包含这个词"。查询时只需要查词, 不用遍历全部文档。
    - BM25 打分公式(核心, 建议背下来):
        score(d, q) = Σ IDF(qi) * ( tf(qi,d) * (k1+1) ) / ( tf(qi,d) + k1*(1-b+b*|d|/avgdl) )
      其中:
        qi      : 查询里的第 i 个词;
        IDF(qi) : 逆文档频率, 越"稀有"的词权重越高(如"违约金"比"的"重要得多);
        tf      : 词在文档里出现的次数(词频), 出现越多分越高(但不是线性, 会"饱和");
        k1=1.5  : 词频饱和参数, 控制 tf 增长的"坡度", 防止一个词重复 100 次就顶 100 分;
        b=0.75  : 文档长度归一化参数, 越长文档的每个词被"稀释"得越厉害;
        |d|     : 当前文档的词项总数;
        avgdl   : 所有文档的平均词项数(归一化的基准)。

    【为什么不用 rank_bm25 第三方包】(本项目实测决策)
    ① Python 3.8 环境未安装该包;
    ② 法律文本以中文为主, rank_bm25 默认按空格分词, 对中文完全无效, 仍需自定义分词器;
    ③ 自研实现完全可控, 可以把"词项权重/停用词"与项目检索策略深度耦合。
    """

    def __init__(self, docs: list, id_key: str = "doc_id"):
        """
        【功能】
        初始化 Bm25Index 对象: 保存超参数、把文档列表存起来, 并立即调用 _build()
        构建倒排索引与 IDF 表 —— 也就是说"new 一个 Bm25Index 就会完成建索引"。

        【参数】
        - docs : list[dict]  —— 语料文档列表, 每项是一个字典, 至少含 id_key 指定的
          唯一标识字段与 "search_text" 检索文本字段(或其它可拼接文本的字段);
        - id_key : str       —— 文档唯一标识字段名, 默认 "doc_id"。

        【返回值】
        无(构造函数不返回值, 索引构建结果保存在 self 的各个字段里)。

        【逻辑】
        ① 记录 id_key / k1 / b 等参数; ② 遍历 docs 统计词频、文档长度、文档频率;
        ③ 计算平均文档长度 avgdl 与每个词的 IDF; ④ 至此索引可被 search() 使用。
        """
        self.id_key = id_key                    # 文档唯一标识字段名(默认 "doc_id"), 用于把打分结果映射回原文档
        self.k1 = 1.5                           # BM25 词频饱和参数 k1, 标准默认值 1.5, 控制词频贡献的"坡度"
        self.b = 0.75                           # BM25 文档长度归一化参数 b, 标准默认值 0.75, 惩罚超长文档
        self.docs = docs                        # 原始文档列表(存一份引用), 最终要把 doc_id 映射回完整文档返回
        self.avgdl = 0.0                        # 平均文档长度(词项数), 初始为 0, 由 _build() 计算后用于 BM25 归一化
        self.doc_len = {}                       # 字典: doc_id -> 该文档的词项总数, 打分时查文档长度用
        self.df = Counter()                     # Counter: 词项 -> 包含该词的文档数(文档频率 document frequency)
        self.idf = {}                           # 字典: 词项 -> 逆文档频率, 在 _build() 里预计算并缓存, 避免每次查询重复算
        self.inverted = defaultdict(list)       # 倒排索引: 词项 -> [(doc_id, tf), ...] 倒排列表, 值是"出现该词的文档们"
        self._build(docs)                       # 立即构建索引: 这一步做完, 本对象才可以被 search() 查询

    # ---------- 分词器 ----------
    @staticmethod
    def tokenize(text: str) -> list:
        """
        【功能】
        中文轻量分词器: 把一段文本切成"词"的列表, 供建索引与查询共同使用。

        【参数】
        - text : str —— 待切分的文本(文档内容或查询语句)。

        【返回值】
        - list —— 切分出的词列表; 输入为空时返回空列表。

        【逻辑】
        ① 先转小写(兼容英文标准编号, 如 GB50300 与 gb50300 视为同一词);
        ② 按标点/空白切分成"粗粒度词块"(如"违约金、留置权" -> ["违约金", "留置权"]);
        ③ 对每个词块: 长度 >= 2 保留原词; 长度 >= 3 时再做 2-gram 滑窗切分,
           把长词拆成多个双字子串(如"建设工程" -> ["建设", "设工", "工程"])。

        【为什么用 2-gram(双字滑窗)】
        中文法律术语(违约金/留置权/不可抗力)以双字词为主, 2-gram 能覆盖绝大多数
        关键概念, 且无需加载任何分词模型, 工程性价比最高; 长词通过滑窗拆成子串,
        即使查询只包含长词的一部分, 也能命中, 从而提高【召回率 recall】。
        """
        if not text:
            return []                           # 空文本直接返回空列表, 避免后续处理报错(防御式编程)
        # 统一小写: 中文不受影响, 但英文编号 GB50300 与 gb50300 会被视为同一个词(提高命中)
        text = text.lower()
        # 按标点与空白切分为"粗粒度词": re.split 的第二个参数是"分隔符集合",
        # 包含中文逗号句号、英文分号冒号、括号、空白、引号、破折号、斜杠、竖线等;
        # 列表推导式 + if t.strip() 过滤掉空串(连续标点会产生空串)。
        raw_tokens = [t for t in re.split(r'[，。；;：:、（）()【】\[\]\s"\'“”‘’\-—/\\|]+', text) if t.strip()]
        tokens = []                             # 最终词列表(注意: 它会同时包含"完整词"和"2-gram 子串")
        for tok in raw_tokens:                  # 遍历每个粗粒度词块
            if len(tok) >= 2:                   # 只保留长度 >= 2 的词块(单字噪音大, 无区分度)
                tokens.append(tok)              # 先把完整词块本身加入列表(如"建设工程施工合同")
                # 2-gram 滑窗切分: 仅对长度 >= 3 的词块执行, 生成所有相邻双字子串
                if len(tok) >= 3:               # 长度 2 的词无需再切(本身就是一个双字词)
                    for i in range(len(tok) - 1):       # 滑窗起点从 0 到 len-2
                        sub = tok[i:i + 2]              # 取第 i 和第 i+1 个字符组成双字子串
                        if sub not in tokens:           # 去重: 同一个子串只保留一份, 避免重复计分
                            tokens.append(sub)          # 加入子串(如"建设"、"设工"、"工程")
        return tokens                           # 返回最终词列表, 供倒排索引 / 查询使用

    # ---------- 构建 ----------
    def _build(self, docs: list):
        """
        【功能】
        遍历全部文档, 统计词频并构建倒排索引与 IDF 表 —— 这是建索引的核心步骤。

        【参数】
        - docs : list[dict] —— 语料文档列表(即 __init__ 里传入的 docs)。

        【返回值】
        无(所有结果直接写进 self 的字段: doc_len / df / inverted / avgdl / idf)。

        【逻辑】
        ① 对每篇文档: 取 doc_id(缺失时用文档 JSON 的 md5 前 16 位兜底生成);
        ② 拼接检索文本(优先用 search_text 字段, 否则把文档所有字符串/列表字段值拼起来);
        ③ 分词后记录文档长度, 累加总长度; ④ 统计词频, 写入倒排索引并更新文档频率;
        ⑤ 全部文档处理完后计算平均长度 avgdl; ⑥ 预计算每个词的 IDF 并缓存。
        """
        total_len = 0                                     # 累加器: 所有文档的词项总数, 最后用来算平均长度 avgdl
        for doc in docs:                                  # 遍历每篇文档(字典)
            # 取文档唯一标识: 优先用 id_key 字段(如 "doc_id");
            # 若字段缺失, 用整个文档 JSON 的 md5 哈希前 16 位兜底 —— 同一份文档永远得到同一个 key(确定性)
            doc_id = str(doc.get(self.id_key, hashlib.md5(json.dumps(doc, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]))
            # 拼接检索文本: 优先用专用字段 "search_text"(建库时已把最关键的文本拼好);
            # 否则把文档里所有 str / list 类型的字段值用空格拼起来(保证一定有文本可检索)
            text = doc.get("search_text") or " ".join(str(v) for v in doc.values() if isinstance(v, (str, list)))
            tokens = self.tokenize(text)                  # 调用分词器把文本切成词列表
            self.doc_len[doc_id] = len(tokens)            # 记录该文档的词项总数(文档长度), BM25 长度归一化要用
            total_len += len(tokens)                      # 累加到总长度, 供计算 avgdl
            # 用 Counter 统计该文档内各词的出现次数(Counter 天然去重计数)
            tf_counter = Counter(tokens)                  # 例: {"违约金": 2, "上限": 1, ...}
            for term, tf in tf_counter.items():           # 遍历 (词, 词频) 对
                self.inverted[term].append((doc_id, tf))  # 写入倒排索引: 词 -> 追加一条 (doc_id, tf) 记录
                self.df[term] += 1                        # 文档频率 +1: 该词又"多被一篇文档包含"
        self.avgdl = total_len / max(1, len(docs))        # 平均文档长度 = 总词数 / 文档数; max(1, ...) 防除零
        # 预计算每个词项的 IDF(逆文档频率), 公式: idf = ln(1 + (N - df + 0.5) / (df + 0.5))
        n_docs = max(1, len(docs))                        # 文档总数 N, 同样防除零
        for term, df in self.df.items():                  # 遍历词表(每个出现过的词)
            # 词越稀有(df 越小), 分子越大, IDF 越大 —— "违约金"的 IDF 远大于"的"
            self.idf[term] = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))

    # ---------- 查询 ----------
    def search(self, query: str, top_k: int = 10) -> list:
        """
        【功能】
        BM25 打分检索: 给定查询文本, 返回最相关的前 top_k 篇文档。

        【参数】
        - query : str —— 查询文本(内部会再次调用 tokenize 分词);
        - top_k : int —— 返回结果数上限, 默认 10。

        【返回值】
        - list[dict] —— 形如 [{doc_id, bm25_score, ...原文档字段}] 按分数降序;
          每项会附带原始文档的全部字段, 便于调用方直接拼装 citation(引用证据)。

        【逻辑】
        ① 空查询或空语料直接返回空列表; ② 查询分词, 无词也返回空;
        ③ 遍历查询词(去重), 对每个词查倒排索引, 累加每篇命中文档的 BM25 得分;
        ④ 按得分降序取前 top_k; ⑤ 把 doc_id 映射回原始文档对象, 附上 bm25_score 返回。
        """
        if not query or not self.docs:                    # 防御: 查询为空 或 语料为空, 无检索意义
            return []                                     # 直接返回空列表
        terms = self.tokenize(query)                      # 对查询文本做同样的分词处理(与索引分词保持一致)
        if not terms:                                     # 查询分词后一个词都没有(如纯标点)
            return []                                     # 返回空列表
        scores = defaultdict(float)                       # 打分表: doc_id -> 累计得分(默认 0.0)
        # 遍历查询词项, 累加每项对命中文档的贡献(核心打分循环)
        for term in set(terms):                           # set 去重: 同一个词在查询里出现多次只算一次
            if term not in self.inverted:                 # 该词在语料里从未出现过, 无文档可命中
                continue                                  # 跳过这个词(它的贡献为 0)
            idf = self.idf.get(term, 0.0)                 # 取该词的逆文档频率(建索引时已缓存, 取不到给 0)
            for doc_id, tf in self.inverted[term]:        # 遍历倒排列表中所有包含该词的 (doc_id, 词频)
                dl = self.doc_len.get(doc_id, 0)          # 取出该文档的词项总数(文档长度)
                # 【BM25 词频饱和公式】: tf*(k1+1) / (tf + k1*(1 - b + b*dl/avgdl))
                # 分母里 dl/avgdl 表示"该文档比平均文档长多少", 越长惩罚越大(归一化);
                # max(1.0, avgdl) 防止平均长度为 0 时除零崩溃。
                denom = tf + self.k1 * (1 - self.b + self.b * dl / max(1.0, self.avgdl))
                scores[doc_id] += idf * (tf * (self.k1 + 1)) / denom   # 累加: IDF * 饱和词频 = 该词对这篇文档的贡献
        # 按得分降序排序, 并截取前 top_k 个 (doc_id, score) 对
        ranked = sorted(scores.items(), key=lambda x: -x[1])[:top_k]    # key=lambda x: -x[1] 表示按分数从大到小
        # 把 doc_id 映射回原始文档并附带得分(调用方需要完整的文档字段来拼 citation)
        results = []                                     # 最终结果列表
        doc_map = {str(d.get(self.id_key, "")): d for d in self.docs}   # 预构建 doc_id -> 文档 的查找表, 加速映射
        for doc_id, score in ranked:                     # 遍历排名靠前的 (doc_id, score)
            doc = doc_map.get(doc_id)                    # 用 doc_id 查回原始文档字典
            if doc is not None:                          # 防御: 理论上都能查到, 查不到就跳过
                # 展开原文档字段, 并追加 doc_id 与 bm25_score(保留 4 位小数, 便于展示与排序)
                results.append({**doc, "doc_id": doc_id, "bm25_score": round(score, 4)})
        return results                                   # 返回按 BM25 分数降序的文档列表


# ======================================================================
# KnowledgeBase: 本地知识库加载器(JSON 文档集 + FAISS + BM25 索引)
# ======================================================================
class KnowledgeBase:
    """
    【功能】
    本地知识库加载器(也可以理解为"知识库的仓库管理员"): 负责按数据源名称
    (laws/cases/industry/interpretations)加载结构化文档集, 并构建/读取
    BM25 稀疏索引与 FAISS 向量索引, 对外暴露统一的单源检索接口 search_source()。

    【懒加载策略 lazy loading】(为什么这么设计)
    本类"初始化时不读任何文件", 只有第一次真正访问某个数据源时才读文件、建索引,
    结果缓存到实例字段 _cache 里。好处: ① 模块导入即加载全部知识库会导致启动变慢;
    ② 内存浪费(4 个源的文档 + 索引都很大); ③ 用不到的源永远不加载, 省时省内存。
    """

    def __init__(self):
        """
        【功能】
        初始化 KnowledgeBase 对象。

        【参数】
        无。

        【返回值】
        无。

        【逻辑】
        只创建一个缓存字典 _cache, 不预加载任何数据 —— 这就是"懒加载"的起点。
        """
        self._cache = {}                  # 缓存: corpus(数据源名) -> {"docs": 文档列表, "bm25": Bm25Index 对象, "faiss": (index, id2text) 元组}

    # ---------- 路径辅助 ----------
    @staticmethod
    def _docs_path(corpus: str) -> str:
        """
        【功能】
        计算某个数据源"文档集 JSON 文件"的完整路径。

        【参数】
        - corpus : str —— 数据源名称(如 "laws")。

        【返回值】
        - str —— 如 <工程根>/data/knowledge_base/laws_docs.json。

        【逻辑】
        用 os.path.join 把知识库根目录 _KB_DIR 与 "{corpus}_docs.json" 拼起来。
        """
        return os.path.join(_KB_DIR, f"{corpus}_docs.json")   # f-string 拼文件名: laws -> laws_docs.json

    @staticmethod
    def _bm25_path(corpus: str) -> str:
        """
        【功能】
        计算某数据源 BM25 索引缓存文件的完整路径。

        【参数】
        - corpus : str —— 数据源名称。

        【返回值】
        - str —— 如 <工程根>/data/knowledge_base/index/laws_bm25.pkl。

        【逻辑】
        路径拼在索引子目录 _KB_INDEX_DIR 下, 扩展名 .pkl(pickle 二进制格式)。
        """
        return os.path.join(_KB_INDEX_DIR, f"{corpus}_bm25.pkl")   # 例如 laws_bm25.pkl

    @staticmethod
    def _faiss_path(corpus: str) -> str:
        """
        【功能】
        计算某数据源 FAISS 向量索引文件的完整路径。

        【参数】
        - corpus : str —— 数据源名称。

        【返回值】
        - str —— 如 <工程根>/data/knowledge_base/index/laws_faiss.index。

        【逻辑】
        FAISS 官方索引文件扩展名约定为 .index, 由 faiss.write_index 生成。
        """
        return os.path.join(_KB_INDEX_DIR, f"{corpus}_faiss.index")   # 例如 laws_faiss.index

    @staticmethod
    def _faiss_id2text_path(corpus: str) -> str:
        """
        【功能】
        计算某数据源 FAISS "id2text 映射文件"的完整路径。

        【参数】
        - corpus : str —— 数据源名称。

        【返回值】
        - str —— 如 <工程根>/data/knowledge_base/index/laws_id2text.pkl。

        【逻辑】
        FAISS 索引里只存向量(数字), 不存原文; 这份 pkl 存"向量序号 -> 原始文档",
        检索命中第 i 个向量时, 用它在 id2text 里查回原文。二者必须成对存在。
        """
        return os.path.join(_KB_INDEX_DIR, f"{corpus}_id2text.pkl")   # 例如 laws_id2text.pkl

    # ---------- 文档加载 ----------
    def load_docs(self, corpus: str) -> list:
        """
        【功能】
        加载某数据源的文档集(JSON 数组), 文件缺失或损坏时返回空列表, 不抛异常。

        【参数】
        - corpus : str —— 数据源名称(laws/cases/industry/interpretations)。

        【返回值】
        - list[dict] —— 文档字典列表; 文件不存在或读取失败时返回 []。

        【文档标准结构(各字段含义)】
        - laws:            {law_name 法律名, article_no 条号, chapter 章节, content 条文内容,
                            effective_date 施行日期, status 效力状态, source 来源}
        - cases:           {case_title 案件标题, case_no 案号, case_type 案由, court_name 法院,
                            judge_date 裁判日期, case_summary 案情摘要, judgment 裁判结果, cited_laws 引用法条}
        - industry:        {standard_name 标准名, standard_no 标准编号, section 章节, content 内容, source 来源}
        - interpretations: {interpretation_name 解释名, article_no 条号, content 内容, source 来源}

        【逻辑】
        ① 拼路径; ② 文件不存在直接返回空(引擎上层会自动降级); ③ 以 utf-8 读 JSON;
        ④ 解析失败(文件损坏)时打印告警并返回空 —— 保证检索流程永不中断。
        """
        path = self._docs_path(corpus)                    # 先算出该数据源的文档 JSON 路径
        if not os.path.exists(path):                      # 文件不存在(知识库还没构建或该源未生成)
            return []                                     # 返回空列表, 不报错 —— 上层会走"降级"逻辑
        try:                                              # 尝试读取, 用 try 包住是为了捕获文件损坏等异常
            with open(path, "r", encoding="utf-8") as f:  # 以只读模式 + utf-8 编码打开文件(中文必须 utf-8)
                return json.load(f)                       # json.load 把整个 JSON 数组反序列化成 Python 列表返回
        except Exception as e:                            # 捕获任何读取/解析异常(如 JSON 语法错误、编码错误)
            # 文件损坏时打印告警并返回空, 保证检索流程不中断(法律场景可用性优先)
            print(f"  ⚠️ [KB] 知识库[{corpus}]加载失败: {e}")   # 输出告警日志, 方便排查数据问题
            return []                                     # 返回空列表, 由上层降级处理

    # ---------- BM25 构建/读取 ----------
    def _get_bm25(self, corpus: str, docs: list):
        """
        【功能】
        获取或构建某数据源的 BM25 索引, 带 pickle 磁盘缓存(避免每次重启都重建)。

        【参数】
        - corpus : str —— 数据源名称;
        - docs : list —— 该数据源的文档列表(已由 load_docs 加载)。

        【返回值】
        - Bm25Index 对象; 文档为空时返回 None。

        【逻辑】
        ① 文档为空 -> 返回 None(没有东西可索引);
        ② 缓存文件存在 -> 尝试 pickle.load 读回; 若缓存有效(有 doc_len 且文档数与当前一致)直接复用;
        ③ 缓存损坏或数量不一致 -> 重新 new Bm25Index(docs) 构建;
        ④ 构建成功后尝试写回磁盘(写失败只告警, 不影响本次使用)。
        """
        if not docs:                                      # 防御: 没有文档就没有索引可言
            return None                                   # 返回 None, 上层调用方会跳过 BM25 这一级
        bm25_path = self._bm25_path(corpus)               # 算出缓存文件路径(如 laws_bm25.pkl)
        # 尝试读取缓存: 缓存存在且文档数一致时直接复用, 省去重建索引的时间
        if os.path.exists(bm25_path):                     # 缓存文件存在才尝试读取
            try:                                          # 读缓存也可能失败(文件被截断/版本不兼容)
                with open(bm25_path, "rb") as f:          # "rb" = 二进制只读模式(pickle 是二进制格式)
                    cached = pickle.load(f)               # pickle.load 把磁盘上的对象还原成 Python 对象
                # 校验缓存有效性: 对象存在 && 有 doc_len 字段(说明是完整的 Bm25Index) &&
                # 缓存的文档数量与当前文档数量一致(数据没变过, 索引才不会过期)
                if cached and getattr(cached, "doc_len", None) and len(cached.docs) == len(docs):
                    return cached                         # 缓存有效, 直接返回复用
            except Exception:                             # 任何读取/反序列化异常(如旧版本 pickle 不兼容)
                pass  # 缓存损坏则重新构建(静默吞掉异常, 走下面的重建分支)
        # 走到这里说明: 没有缓存 / 缓存损坏 / 缓存数量不一致 —— 需要现场构建
        bm25 = Bm25Index(docs)                            # 调用 Bm25Index 构造函数, 内部完成分词 + 倒排 + IDF
        try:                                              # 构建成功后尝试写缓存(写失败不影响本次使用)
            os.makedirs(_KB_INDEX_DIR, exist_ok=True)     # 确保索引目录存在(exist_ok=True: 已存在也不报错)
            with open(bm25_path, "wb") as f:              # "wb" = 二进制写模式
                pickle.dump(bm25, f)                      # pickle.dump 把对象序列化写入磁盘, 下次启动直接读
        except Exception as e:                            # 写缓存失败(如磁盘只读、权限不足)
            print(f"  ⚠️ [KB] BM25 索引缓存写入失败({corpus}): {e}")   # 只告警不中断
        return bm25                                       # 返回新构建的 BM25 索引

    # ---------- FAISS 构建/读取 ----------
    def _get_faiss(self, corpus: str, docs: list):
        """
        【功能】
        获取某数据源的 FAISS 向量索引(惰性构建): 索引文件已存在则直接读取,
        否则尝试用共享 embedding_model(句向量模型)对文档编码并现场构建。

        【参数】
        - corpus : str —— 数据源名称;
        - docs : list —— 该数据源的文档列表。

        【返回值】
        - (index, id2text) 元组: index 是 faiss.IndexFlatIP, id2text 是"向量序号 -> 文档"列表;
          嵌入模型不可用 / 索引构建失败时返回 None, 由调用方降级到 BM25 / 关键词检索。

        【逻辑】
        ① 索引与 id2text 文件都存在 -> 读取并返回(最快路径);
        ② 不存在且无文档 -> 返回 None(巧妇难为无米之炊);
        ③ 否则尝试 import embedding_model + faiss + numpy, 按数据源类型拼接索引文本,
           编码成向量, 构建 IndexFlatIP(内积索引, 向量已归一化, 内积等价余弦相似度),
           写盘缓存, 返回 (index, docs)。

        【什么是 FAISS / 向量检索(给新手)】
        FAISS 是 Facebook 开源的向量相似度检索库。核心思路: 把每篇文档用神经网络
        (sentence-transformer)编码成一个"语义向量"(几百维的浮点数数组), 语义相近的
        文档向量在空间中距离也近。查询时同样把问题编码成向量, 然后找"最接近"的文档向量。
        这叫【语义检索 semantic search】, 能处理"意思相同但用词不同"的查询 —— 这是
        关键词检索(BM25)做不到的。
        """
        faiss_path = self._faiss_path(corpus)             # FAISS 索引文件路径(如 laws_faiss.index)
        id2text_path = self._faiss_id2text_path(corpus)   # id2text 映射文件路径(如 laws_id2text.pkl)
        # 索引已存在: 直接读取(两份文件必须同时存在, 缺一不可)
        if os.path.exists(faiss_path) and os.path.exists(id2text_path):   # 两份文件都在才算"索引就绪"
            try:                                          # 读取也可能失败(版本不兼容等)
                import faiss                              # 延迟导入: 只有真正用时才 import, 加速模块加载
                index = faiss.read_index(faiss_path)      # faiss.read_index 从磁盘读入向量索引
                with open(id2text_path, "rb") as f:       # 二进制读 id2text 映射文件
                    id2text = pickle.load(f)              # 还原成"向量序号 -> 文档"列表
                return (index, id2text)                   # 返回 (索引, 映射) 元组, 供 _faiss_search 使用
            except Exception as e:                        # 读取失败(如 faiss 未安装、文件损坏)
                print(f"  ⚠️ [KB] FAISS 索引读取失败({corpus}): {e}")   # 告警后继续走构建分支
                return None                               # 返回 None, 上层降级
        # 索引不存在且无文档: 无法构建(没有可编码的文本)
        if not docs:                                      # 连文档都没有, 自然无法建索引
            return None                                   # 返回 None
        # 尝试用项目共享 embedding_model 构建(模型未配置 / 未安装时自动跳过)
        try:                                              # 整个构建过程可能因缺依赖而失败, 全部兜住
            from common.embedding_model import embedding_model   # 项目共享的句向量编码器(封装了 sentence-transformers)
            import faiss                                  # FAISS 向量索引库
            import numpy as np                            # numpy: 把向量列表转成 float32 矩阵(FAISS 要求)
            # 构造索引文本: 不同数据源拼接不同的关键字段(把最"有信息量"的字段拼成一句话)
            texts = []                                    # 待编码的文本列表, 与 docs 一一对应
            for d in docs:                                # 遍历每篇文档
                if corpus == CORPUS_LAWS:                 # 法规源: 法律名 + 条号 + 条文内容
                    texts.append(f"{d.get('law_name','')} {d.get('article_no','')} {d.get('content','')}")
                elif corpus == CORPUS_CASES:              # 案例源: 案件标题 + 案情摘要(裁判文书太长, 摘要足够)
                    texts.append(f"{d.get('case_title','')} {d.get('case_summary','')}")
                else:                                     # 其它源(行业标准/司法解释): 所有文本字段拼接兜底
                    texts.append(" ".join(str(v) for v in d.values() if isinstance(v, (str, list))))
            # 批量编码: batch_size=32 控制一次喂给模型的文本数(省显存/内存);
            # normalize_embeddings=True 归一化向量长度, 使"内积"等价于"余弦相似度"(余弦在 0~1 之间更好比较)
            vectors = embedding_model.encode(texts, batch_size=32, normalize_embeddings=True)
            vectors = np.asarray(vectors, dtype=np.float32)   # 转成 float32 的二维矩阵(FAISS 只认这个类型)
            index = faiss.IndexFlatIP(vectors.shape[1])   # IndexFlatIP = 暴力内积索引, 维度 = 向量维数
            index.add(vectors)                            # 把所有文档向量加入索引(建好索引)
            os.makedirs(_KB_INDEX_DIR, exist_ok=True)     # 确保索引目录存在
            faiss.write_index(index, faiss_path)          # 把索引写盘, 下次启动直接读, 不用重新编码
            with open(id2text_path, "wb") as f:           # 同时把"向量序号 -> 文档"映射写盘
                pickle.dump(docs, f)                      # 注意: 这里存的是 docs 本身(序号 i 对应 docs[i])
            print(f"  ✅ [KB] 已构建 {corpus} FAISS 索引, {len(docs)} 条")   # 构建成功日志
            return (index, docs)                          # 返回 (索引, 文档列表) —— id2text 就是 docs 本身
        except Exception as e:                            # 任一环节失败(模型未装/内存不足/faiss 缺失)
            print(f"  ⚠️ [KB] FAISS 索引构建跳过({corpus}): {e}")   # 告警并跳过 —— 上层自动降级到 BM25/关键词
            return None                                   # 返回 None

    # ---------- 向量检索 ----------
    def _faiss_search(self, corpus: str, query: str, top_k: int) -> list:
        """
        【功能】
        在某数据源的 FAISS 索引中做向量语义检索, 返回按相似度降序的文档列表。

        【参数】
        - corpus : str —— 数据源名称;
        - query : str —— 查询文本;
        - top_k : int —— 返回条数上限。

        【返回值】
        - list[dict] —— 命中文档列表(每项带 vec_score 字段); 索引不可用或无命中时返回
          空列表(调用方据此降级到 BM25 / 关键词检索)。

        【逻辑】
        ① 从 _cache 取该源的 faiss 数据, 没有则返回空;
        ② 把查询编码成向量(normalize_embeddings=True, 与建索引时保持一致!);
        ③ index.search(query_vec, top_k) 返回 (相似度数组, 下标数组);
        ④ 遍历下标, 过滤掉 -1(FAISS 用 -1 表示"没有这么多结果"),
           用下标在 id2text 里查回原文档, 附上 vec_score 返回。
        """
        cache = self._cache.get(corpus, {})               # 从缓存取该数据源的条目(懒加载已保证存在)
        faiss_data = cache.get("faiss")                   # 取 faiss 数据: (index, id2text) 或 None
        if not faiss_data:                                # 该源没有 FAISS 索引(构建失败或未启用)
            return []                                     # 返回空列表, 上层自动降级
        index, id2text = faiss_data                       # 解包: index = 向量索引, id2text = 序号->文档 列表
        try:                                              # 检索过程可能因缺依赖失败, 兜住异常
            from common.embedding_model import embedding_model   # 延迟导入共享编码器
            import numpy as np                            # 向量类型转换用
            # 把查询编码成向量; normalize_embeddings=True 必须与建索引时一致, 否则相似度失真!
            query_vec = embedding_model.encode([query], normalize_embeddings=True)
            query_vec = np.asarray(query_vec, dtype=np.float32)   # 转 float32 矩阵(FAISS 要求)
            # index.search 返回两个数组: scores[0] 是 top_k 个相似度, indices[0] 是对应的向量下标
            scores, indices = index.search(query_vec, top_k)
            results = []                                  # 结果列表
            for i, idx in enumerate(indices[0]):          # 遍历返回的每个命中下标
                if idx < 0 or idx >= len(id2text):        # 下标 -1 表示结果不足; 越界防御
                    continue                              # 跳过无效下标
                doc = id2text[idx]                        # 用下标查回原始文档(建索引时 docs[i] 就是第 i 个向量)
                # 展开原文档字段, 附上 vec_score(向量相似度, 保留 4 位小数; 归一化后近似余弦相似度)
                results.append({**doc, "vec_score": round(float(scores[0][i]), 4)})
            return results                                # 返回按相似度排序的文档列表
        except Exception as e:                            # 编码失败 / faiss 缺失 / 类型错误等
            print(f"  ⚠️ [KB] FAISS 检索失败({corpus}): {e}")   # 告警
            return []                                     # 返回空, 上层降级

    # ---------- 单源检索(纵向降级) ----------
    def search_source(self, corpus: str, query: str, keywords: list = None,
                      top_k: int = 5, use_vector: bool = True) -> list:
        """
        【功能】
        在某一个数据源内做"纵向逐级降级"检索, 这是单源检索的统一入口:
          高阶语义(FAISS 向量) → BM25 稀疏 → 关键词子串扫描。
        任何一级结果不足 top_k 时, 自动用下一级补充, 保证结果尽量多、永不抛异常。

        【参数】
        - corpus : str        —— 数据源名称(laws/cases/industry/interpretations);
        - query : str         —— 检索查询文本;
        - keywords : list[str], optional —— 预抽取的关键词列表; 为空时从 query 退化提取;
        - top_k : int         —— 返回条数上限, 默认 5;
        - use_vector : bool   —— 是否启用 FAISS 向量检索, 默认 True(可关闭以省内存)。

        【返回值】
        - list[dict] —— 命中文档列表, 每项带 score 字段(0~1 归一化), 已按分数降序截断到 top_k。

        【逻辑】
        ① 首次访问该数据源 -> 懒加载文档 + 构建/读取 BM25、FAISS 索引, 存入 _cache;
        ② 知识库为空 -> 返回空列表; ③ 关键词为空 -> 从 query 退化提取(按标点切分, 取长度>=2);
        ④ 第1级 FAISS: 向量检索结果直接转 score(余弦截断到 [0,1]); ⑤ 第2级 BM25:
        结果不足时补充, BM25 原始分用 sigmoid 风格公式归一化到 (0,1); ⑥ 第3级关键词扫描:
        还不足时按"关键词是否出现在文档文本里"扫全量文档(命中给基准分 0.4);
        ⑦ 最终按 score 降序, 截断到 top_k 返回。
        """
        # ---- 首次访问该数据源时懒加载文档 + 构建索引 ----
        if corpus not in self._cache:                     # 缓存里没有这个源 -> 首次访问
            docs = self.load_docs(corpus)                 # 从磁盘加载文档集(JSON)
            self._cache[corpus] = {                       # 把该源的一切存进缓存(此后不再重复加载)
                "docs": docs,                             # 文档列表
                "bm25": self._get_bm25(corpus, docs),     # BM25 索引(带磁盘缓存)
                "faiss": self._get_faiss(corpus, docs) if use_vector else None,   # FAISS 索引(use_vector=False 时跳过)
            }
        cache = self._cache[corpus]                       # 取出该源的缓存条目
        docs = cache.get("docs", [])                      # 取出文档列表
        if not docs:                                      # 知识库为空(文件缺失/加载失败)
            return []                                     # 返回空列表, 无内容可检索

        # ---- 关键词退化提取 ----
        if not keywords:                                  # 调用方没给关键词时, 自己从查询里提取
            # 按中文/英文标点与空白切分查询, 只保留长度 >= 2 的词块(单字无区分度)
            keywords = [w for w in re.split(r'[，。；;：:\s]+', query) if len(w) >= 2]
        if not keywords:                                  # 切完还是空(查询本身全是标点/单字)
            keywords = [query[:4]]                        # 兜底: 硬取查询前 4 个字符当关键词, 保证能扫到东西

        # ---- 第1级: FAISS 向量语义检索(最高阶, 结果最"聪明") ----
        results = []                                      # 最终结果列表(前两级结果都会汇入这里)
        seen_ids = set()                                  # 去重集合: 记录已经收进结果的 doc_id, 防止跨级重复
        if use_vector:                                    # 只有启用向量检索时才执行这一级
            vec_results = self._faiss_search(corpus, query, top_k)   # 调用 FAISS 向量检索
            for r in vec_results:                         # 遍历每条向量命中结果
                # 取文档唯一标识: 有 doc_id 用 doc_id, 否则用 JSON 前 40 字符兜底(去重键)
                doc_id = r.get("doc_id", json.dumps(r, ensure_ascii=False)[:40])
                if doc_id in seen_ids:                    # 该文档已被收过(理论上不会, 防御)
                    continue                              # 跳过
                seen_ids.add(doc_id)                      # 标记为已收录
                # 向量相似度归一化到 0~1: 余弦相似度理论范围是 -1~1, 用 max(0,...) 把负数截断为 0
                results.append({**r, "score": max(0.0, min(1.0, float(r.get("vec_score", 0))))})

        # ---- 第2级: BM25 稀疏检索(结果不足 top_k 时补充) ----
        if len(results) < top_k and cache.get("bm25"):    # 向量结果不够 且 该源有 BM25 索引
            # 多取一倍(top_k*2)再筛, 因为可能有很多结果已被向量级收录而需丢弃
            bm25_results = cache["bm25"].search(query, top_k=top_k * 2)
            for r in bm25_results:                        # 遍历 BM25 命中
                doc_id = r.get("doc_id", "")              # BM25 结果必带 doc_id(见 Bm25Index.search)
                if doc_id in seen_ids:                    # 向量级已经收录过 -> 跳过(去重)
                    continue
                seen_ids.add(doc_id)                      # 标记已收录
                # BM25 分数是"无上界的正数", 量纲与余弦完全不同;
                # 用 sigmoid 风格公式 1 - 1/(1+raw) 归一化到 (0,1), 便于跨源比较
                raw = float(r.get("bm25_score", 0))       # 取出 BM25 原始分
                norm = 1.0 - 1.0 / (1.0 + raw) if raw > 0 else 0.0   # raw=0 -> 0; raw 越大越接近 1(单调)
                results.append({**r, "score": round(norm, 4)})   # 附上归一化分数收录
                if len(results) >= top_k:                 # 已凑够 top_k 条
                    break                                 # 提前结束循环(后面的结果不再需要)

        # ---- 第3级: 关键词子串扫描(前两级仍不足时兜底, 保证非空) ----
        if len(results) < top_k:                          # 前两级加起来还不够 top_k
            # 构造检索文本字段: 优先用 search_text, 否则把文档所有字符串字段值拼接成一段文本
            scan_results = []                             # 关键词命中的文档暂存区
            for d in docs:                                # 全量遍历该源文档(最笨但最可靠的兜底)
                text = d.get("search_text") or " ".join(str(v) for v in d.values() if isinstance(v, str))
                if any(k in text for k in keywords):      # 用 Python 的 in 做子串判断: 任一关键词出现在文本里即命中
                    scan_results.append({**d, "score": 0.4})       # 关键词命中的基准分 0.4(刻意低于向量/BM25 的高分)
            # 用未出现过的文档补充(同样去重)
            for r in scan_results:                        # 遍历扫描命中的文档
                doc_id = r.get("doc_id", json.dumps(r, ensure_ascii=False)[:40])   # 取去重键(缺失时 JSON 前 40 字符兜底)
                if doc_id in seen_ids:                    # 前面级别已收录 -> 跳过
                    continue
                seen_ids.add(doc_id)                      # 标记已收录
                results.append(r)                         # 收录这条(score 已是 0.4)
                if len(results) >= top_k:                 # 凑够就停
                    break

        # 按分数降序并截断(把所有级的结果统一按 score 从大到小排, 取前 top_k)
        results.sort(key=lambda x: -x.get("score", 0))    # key=lambda x: -x.get("score", 0) 表示按 score 降序(缺失按 0)
        return results[:top_k]                            # 截断到 top_k 条返回


# ======================================================================
# 法律效力冲突消解(4 套硬编码规则)
# ======================================================================
def _detect_law_level(law_name: str) -> str:
    """
    【功能】
    从法律名称中识别其效力层级(供冲突消解规则1使用), 返回层级名。

    【参数】
    - law_name : str —— 法律/法规名称(如 "中华人民共和国劳动法"、"建设工程质量管理条例")。

    【返回值】
    - str —— 层级名("宪法"/"法律"/"行政法规"/"司法解释"/"行业标准"/"其他")。

    【逻辑】
    依次拿 _LAW_LEVEL_PATTERNS 里的每个 (正则, 层级) 对去匹配名称,
    先命中先得(顺序很重要, 特殊模式放前面); 全部不命中返回兜底"其他"。
    """
    for pattern, level in _LAW_LEVEL_PATTERNS:            # 遍历 (正则模式, 层级名) 列表
        if re.search(pattern, law_name or ""):            # re.search: 在名称中查找模式, 找到返回 Match 对象
            return level                                  # 命中即返回对应层级(先命中先得)
    return "其他"                                         # 都没命中 -> 未识别层级, 返回最低优先级的"其他"


def resolve_law_conflicts(citations: list) -> list:
    """
    【功能】
    对法规类引用执行 4 套硬编码冲突消解规则, 返回消解(排序+标注)后的引用列表。
    这是法律专业性的核心体现: 多条法条"打架"时, 告诉使用者谁更该被采信。

    【4 套规则详解】
    规则1(上位法优于下位法): 同一主题命中多条法条时, 按 _LAW_LEVEL 层级表
                             保留效力更高者(层级数字小者排前面);
    规则2(新法优于旧法):   同一层级且均标注施行日期时, 保留施行日期更新的法条
                             (effective_date 字符串转成数字比较, 如 "2021-01-01" -> 20210101);
    规则3(特别法优于一般法): 名称含"特别/专项/专门/实施办法"的特别法优先于一般法
                              (如《特别规定》优于《规定》);
    规则4(已废止/已修改降权): status 非"现行有效"或名称命中已废止特征时,
                             在 source 中追加"⚠️已废止/已修改(参考)", 并把 score 乘 0.3 降权。

    【参数】
    - citations : list[dict] —— 融合后的引用列表, 每项含 title/law_name/article_no/
      content/score/source 等字段(注意: 函数会就地修改这些字典并排序列表)。

    【返回值】
    - list[dict] —— 消解后的引用列表(只排序 + 标注, 不删除任何条目, 保留可追溯性)。

    【逻辑】
    ① 空列表直接返回; ② 对每条引用: 执行规则4(降权+标注) 与规则1(附加 _law_level 字段);
    ③ 按 (层级, 日期新者, 特别法优先, 原分数) 四元组排序, 完成规则1/2/3。
    """
    if not citations:                                     # 防御: 没有引用就没什么可消解的
        return citations                                  # 原样返回
    # 已废止/已修改法规名称特征(规则4用): 这些都是历史上知名、后来被新法取代的法律;
    # 注意 (?!典) 是"负向先行断言": "婚姻法(?!典)" 能匹配"婚姻法"但不会匹配"婚姻法典",
    # 同理 "合同法(?!典)" 不会误伤《民法典合同编》里的"合同法"字样。
    repealed_patterns = ["经济合同法", "民法通则", "婚姻法(?!典)", "继承法", "物权法", "侵权责任法", "合同法(?!典)"]
    for c in citations:                                   # 遍历每条引用(注意: 列表里的字典会被就地修改)
        name = c.get("law_name") or c.get("title") or ""  # 取名称: 优先 law_name 字段, 否则 title, 都空则 ""
        status = c.get("status", "")                      # 取效力状态字段(如 "现行有效" / "已废止")
        # 规则4: 非现行有效 或 名称命中已废止特征 -> 降权+标注
        # 条件1: status 非空 且 不是 "现行有效"(即已废止/已修改/失效等);
        # 条件2: 名称命中 repealed_patterns 里的任一正则(any(...) 只要一个命中即可)。
        if status and status not in ("现行有效", "") or any(re.search(p, name) for p in repealed_patterns):
            old_score = float(c.get("score", 0))          # 取出原分数(缺失按 0)
            c["score"] = round(old_score * 0.3, 4)        # 降权到 30%: 已废止的法条不应被优先采信
            c["source"] = f"{c.get('source','')}·⚠️已废止/已修改(参考)"   # 在来源里追加标注, 保留可追溯性
        # 规则1: 附加效力层级信息(存进 _law_level 字段, 供下面的排序使用与前端展示)
        c["_law_level"] = _detect_law_level(name)         # 用名称识别层级, 如 "中华人民共和国劳动法" -> "法律"
    # 规则1/2/3 的排序: 层级高者在前 -> 施行日期新者在前 -> 特别法在前 -> 原分数兜底
    # 先把层级名映射成数字(level_order), 数字小 = 层级高 = 排前面
    level_order = {name: idx for idx, name in enumerate(_LAW_LEVEL)}   # {"宪法":0, "法律":1, ..., "其他":9}
    citations.sort(key=lambda x: (                       # 用"四元组"作为排序键, Python 依次比较每个元素
        level_order.get(x.get("_law_level", "其他"), 9),   # 规则1: 层级数字(缺失按 9, 即最低)
        -int(str(x.get("effective_date", "")).replace("-", "") or 0),  # 规则2: 日期转成数字后取负 -> 日期新者排前
        0 if re.search(r"特别|专项|专门|实施办法", x.get("law_name", "") or x.get("title", "")) else 1,  # 规则3: 特别法=0 排前
        -float(x.get("score", 0)),                          # 兜底: 原分数高的排前(取负实现降序)
    ))
    return citations                                    # 返回排序+标注后的引用列表


# ======================================================================
# RRF 融合(Reciprocal Rank Fusion)
# ======================================================================
def rrf_fusion(source_lists: list, k: int = 60) -> list:
    """
    【功能】
    RRF 倒数排名融合: 把多路检索结果合并成一路, 消除各路打分尺度差异。

    【公式】
    score(d) = Σ 1 / (k + rank_i(d))
    其中 rank_i(d) 是文档 d 在第 i 路结果中的排名(从 1 开始), k 是平滑常数(默认 60)。
    排名越靠前, 1/(k+rank) 越大, 贡献越高; 排名靠后的贡献迅速衰减。

    【为什么选 RRF 而不是加权平均】(关键设计决策)
    各路数据源(FAISS 余弦相似度 / BM25 分数 / 关键词命中)的分数量纲完全不同:
    余弦在 -1~1, BM25 无上界, 关键词只有 0.4。直接加权平均会淹没弱信号源
    (BM25 的高分把关键词命中的低分全盖掉)。RRF 只看"排名"不看"分数",
    天然免疫量纲差异 —— 即使某路分数整体偏大, 只要排序是对的, 融合结果就合理。
    实现简单、效果稳健, 是学术界广泛验证的经典融合方法。

    【参数】
    - source_lists : list[list[dict]] —— 多路检索结果列表, 每路按相关度降序排列,
      文档须含唯一标识 doc_id, 或 title+article_no 等可组合成唯一键的字段;
    - k : int —— RRF 平滑常数, 默认 60(与论文原值一致, 经验上 30~100 都稳定)。

    【返回值】
    - list[dict] —— 融合后按 RRF 分数降序的结果列表, 每项附带 rrf_score 字段。

    【逻辑】
    ① 遍历每一路结果, 按排名累加 1/(k+rank+1) 到该文档的累计分;
    ② 用 doc_id(或组合键)把同一篇文档的多路贡献合并;
    ③ 按累计 RRF 分降序输出, 附上 rrf_score。
    """
    rrf_scores = defaultdict(float)          # 累计表: 文档唯一键 -> 累计 RRF 分数(默认 0.0)
    doc_map = {}                             # 映射表: 文档唯一键 -> 文档对象(最后要把 key 换回完整文档)
    for ranked in source_lists:              # 遍历每一路结果列表(如 laws 路、cases 路)
        for rank, doc in enumerate(ranked):  # enumerate 同时拿到排名 rank(0 起)与文档 doc
            # 构造唯一键: 优先 doc_id; 否则拼 "名称|编号|内容前20字" 三件套(名称+编号基本能唯一定位一条法规/案例)
            key = doc.get("doc_id") or f"{doc.get('law_name') or doc.get('title') or doc.get('standard_name') or ''}|{doc.get('article_no') or doc.get('case_no') or doc.get('standard_no') or ''}|{str(doc.get('content') or doc.get('case_summary') or '')[:20]}"
            if not key.strip():              # 极端情况: key 全是空(文档所有字段都缺)
                key = json.dumps(doc, ensure_ascii=False)[:40]   # 兜底: 用文档 JSON 前 40 字符当 key
            rrf_scores[key] += 1.0 / (k + rank + 1)   # 【RRF 核心累加】rank 从 0 起所以要 +1 对齐"从第 1 名算起"
            doc_map.setdefault(key, doc)     # 记录这个 key 对应的文档(setdefault: 已有则不动, 保留第一次见到的)
    # 按 RRF 分数降序输出(分数高的排前面)
    merged = sorted(rrf_scores.items(), key=lambda x: -x[1])   # 对 (key, score) 按 score 降序排序
    results = []                             # 最终结果列表
    for key, score in merged:                # 遍历排序后的 (key, score)
        doc = doc_map[key]                   # 用 key 查回完整文档对象
        results.append({**doc, "rrf_score": round(score, 6)})   # 展开文档字段, 附上 rrf_score(保留 6 位小数)
    return results                           # 返回融合后的结果列表


# ======================================================================
# RetrievalEngine: 统一检索入口
# ======================================================================
class RetrievalEngine:
    """
    【功能】
    可复用检索智能体核心引擎(全局单例模式): 对外只暴露一个主方法 search(),
    内部串起完整的检索流水线: 数据源挂载 → 每源纵向降级检索 → RRF 融合 →
    法律效力冲突消解 → 质量门禁(0.85) → 北大法宝 MCP 兜底 → 结构化输出。

    同时提供 search_laws / search_cases / search_industry / search_interpretations
    四个单源便捷方法, 供法规查询页、案例检索页等单源场景直接调用。

    【与外部的关系】
    - 上游: LangGraph 检索节点(retrieval_base_layer_node 等)调用 search();
    - 下游: 依赖 KnowledgeBase(知识库加载)、rrf_fusion(融合)、resolve_law_conflicts(消解)、
      common.mcp_beidafabao(付费兜底)、common.embedding_model(向量编码)。
    """

    def __init__(self):
        """
        【功能】
        初始化 RetrievalEngine 对象。

        【参数】
        无。

        【返回值】
        无。

        【逻辑】
        ① 创建 KnowledgeBase 实例(懒加载, 此刻不读任何数据);
        ② 定义数据源权重映射(预留字段: 目前 RRF 天然等权, 权重先全为 1.0, 未来可做加权融合)。
        """
        self.kb = KnowledgeBase()                        # 知识库加载器(懒加载: 首次 search 时才真正读文件)
        # 数据源挂载映射: 数据源名 -> 该源在 RRF 融合中的权重(预留字段, 当前 RRF 天然等权, 全为 1.0)
        self.source_weights = {                          # 未来若要"法规权重更高"等策略, 直接改这里的数字即可
            CORPUS_LAWS: 1.0,                            # 法律法规权重
            CORPUS_CASES: 1.0,                           # 裁判案例权重
            CORPUS_INDUSTRY: 1.0,                        # 行业标准权重
            CORPUS_INTERPRETATIONS: 1.0,                 # 司法解释权重
        }

    # ==================================================================
    # 主检索接口
    # ==================================================================
    def search(self, query: str, keywords: list = None, contract_type: str = "",
               task_type: str = "", top_k: int = 8, sources: list = None,
               use_vector: bool = True, doc_text: str = "") -> dict:
        """
        【功能】
        统一检索入口: 一次调用完成"多源挂载 + 纵向降级 + RRF 融合 + 冲突消解 +
        质量门禁 + MCP 兜底 + 人工介入判断", 返回结构化检索结果。
        这是整个引擎对外最重要的方法, 也是唯一需要掌握的方法。

        【参数】
        - query : str         —— 检索查询文本(必填), 如 "违约金的上限是多少" 或合同中的一段话;
        - keywords : list[str], optional —— 预抽取的关键词列表; 为空时引擎内部从 query 退化提取;
        - contract_type : str —— 合同类型(如 "建设工程"、"劳动合同"), 决定是否横向挂载行业标准源;
        - task_type : str     —— 任务类型(contract_review 合同审核 / legal_qa 法律问答 /
          legal_research 法律检索 / legal_document_gen 文书生成 ...), 决定数据源组合;
        - top_k : int         —— 最终返回引用条数上限, 默认 8;
        - sources : list[str], optional —— 显式指定要检索的数据源列表(默认按任务类型自动挂载);
        - use_vector : bool   —— 是否启用 FAISS 向量检索, 默认 True;
        - doc_text : str      —— 文档全文(合同审核等场景传入, 供企查查资信源提取企业名称用)。

        【返回值】
        - dict, 结构如下:
            {
              citations: list[dict],        # 融合消解后的引用列表(每条带 source 溯源标签)
              research_context: str,        # 拼装好的检索上下文文本(可直接喂给 LLM 引用)
              quality_score: float,         # 0~1 质量分(>= 0.85 达标)
              quality_passed: bool,         # 是否达标(达标则跳过北大法宝 MCP 付费调用)
              mcp_used: bool,               # 是否调用了付费北大法宝 MCP 兜底
              conflict_warnings: list[str], # 冲突消解产生的可读提示(如"已被替代/修改")
              need_human_intervention: bool # 是否触发人工介入(重试 3 轮仍不达标时为 True)
            }

        【逻辑(6 步流水线)】
        第0步 数据源挂载: sources 为空时按 task_type/contract_type 自动决定查哪些源;
        第1步 逐源检索: 每个源调 search_source 做纵向降级检索, 结果打上数据源标签;
        第2步 RRF 融合: 把多路结果合并成一路;
        第3步 冲突消解: 法规/解释类引用走 resolve_law_conflicts, 收集冲突提示;
        第4步 截断 + 拼装 research_context(可溯源原文格式);
        第5步 质量门禁: 算质量分, 达标直接返回; 不达标尝试 MCP 兜底;
              MCP 仍不达标则置 need_human_intervention=True。
        """
        # ---- 第0步: 数据源挂载(横向按需) ----
        if sources is None:                               # 调用方没有显式指定数据源
            sources = self._mount_sources(task_type, contract_type)   # 按任务类型/合同类型自动决定挂载哪些源

        # ---- 第1步: 每源纵向降级检索 ----
        source_lists = []                                  # 各源结果列表的集合(形状: [[源1结果], [源2结果], ...]), 供 RRF 融合
        for corpus in sources:                             # 遍历每个被挂载的数据源
            # 行业标准源只在合同类型挂载命中时启用: 行业标准与具体合同类型强相关
            if corpus == CORPUS_INDUSTRY and contract_type:   # 查行业标准 且 给了合同类型
                # 按合同类型扩展查询词, 提高行业标准命中率(如 "建设工程 违约金上限")
                source_query = f"{contract_type} {query}"
            else:                                          # 其它源直接用原始查询
                source_query = query
            # 调用单源检索(内部自动纵向降级); top_k 取 max(5, top_k) 给每源多留点余量, 供 RRF 挑
            results = self.kb.search_source(corpus, source_query, keywords,
                                            top_k=max(5, top_k), use_vector=use_vector)
            # 给每条结果打上"数据源标签"(如 "L1·法规·民法典"), 这是可溯源证据的起点
            for r in results:                              # 遍历该源每条结果
                r["source"] = self._source_tag(corpus, r)  # 生成并写入 source 字段(见 _source_tag)
            source_lists.append(results)                   # 把这一路结果收进集合, 参与后面的 RRF 融合

        # ---- 第2步: RRF 融合(把多路结果公平地合并成一路) ----
        merged = rrf_fusion(source_lists)                  # 调用 RRF, 得到按 rrf_score 降序的融合结果

        # ---- 第3步: 法律效力冲突消解(仅法规/解释类) ----
        conflict_warnings = []                             # 冲突提示列表(供前端展示/审计)
        # 挑出法规类引用: source 标签以 "L1·法规" 或 "L4·司法解释" 开头(只有这类才需要法律效力消解)
        law_cites = [c for c in merged if c.get("source", "").startswith("L1·法规") or c.get("source", "").startswith("L4·司法解释")]
        other_cites = [c for c in merged if c not in law_cites]   # 其余引用(案例/行业标准)不参与消解, 原样保留
        if law_cites:                                      # 存在法规类引用才需要消解
            resolved = resolve_law_conflicts(law_cites)    # 执行 4 套硬编码消解规则(排序 + 标注)
            # 统计冲突提示: 若消解后出现"已废止"标注, 生成一条可读提示, 告知使用者谨慎引用
            for c in resolved:                             # 遍历消解后的法规引用
                if "已废止" in c.get("source", ""):        # source 里含"已废止"(规则4打上的标注)
                    # 拼一条提示: ⚠️ 法规名+条号 已被替代/修改, 仅供参考
                    conflict_warnings.append(f"⚠️ {c.get('law_name', c.get('title',''))}{c.get('article_no','')} 已被替代/修改, 仅供参考")
            merged = resolved + other_cites                 # 消解后的法规引用排前面, 其它引用跟后面
        else:                                              # 没有法规类引用
            merged = other_cites                           # 直接使用其它引用(无需消解)

        # ---- 第4步: 截断与上下文拼装 ----
        citations = merged[:top_k]                          # 截断: 只保留前 top_k 条引用
        # 构造"可溯源原始法律原文证据"格式的检索上下文(research_context):
        # 每条引用带 法规名+条号+原文+来源, 并明确声明"以下为检索原文, 不做主观分析",
        # 这样 LLM 引用时不会把检索结果误当成自己的推断, 也方便审计。
        context_lines = ["【检索结果原文(可溯源, 不含主观分析)】"]   # 上下文首行: 声明性质
        for i, c in enumerate(citations, 1):               # 遍历引用, i 从 1 开始编号
            # 取名称: 不同数据源字段名不同, 用 or 链逐个尝试(law_name -> title -> case_title -> standard_name)
            name = c.get("law_name") or c.get("title") or c.get("case_title") or c.get("standard_name") or ""
            no = c.get("article_no") or c.get("case_no") or c.get("standard_no") or ""   # 取编号(条号/案号/标准号)
            content = c.get("content") or c.get("case_summary") or c.get("judgment") or ""   # 取原文内容
            source = c.get("source", "")                   # 取来源标签(如 "L1·法规·民法典")
            # 拼成一条带编号的引用文本; 内容截断到前 300 字符, 防止上下文爆炸
            context_lines.append(f"{i}. [{name} {no}] ({source})\n{str(content)[:300]}")
        research_context = "\n\n".join(context_lines)      # 用两个换行把每条引用连成一大段文本

        # ---- 第5步: 质量门禁(0.85) + 北大法宝 MCP 兜底 + 人工介入标志 ----
        quality_score = self._compute_quality(citations, query, keywords)   # 计算 0~1 质量分(覆盖率/相关性/时效性)
        quality_passed = quality_score >= QUALITY_GATE      # 是否达标: 分数 >= 0.85(QUALITY_GATE 常量)
        mcp_used = False                                    # 标记: 本次调用是否用过付费 MCP(默认没用)
        need_human = False                                  # 标记: 是否需要人工介入(默认不需要)
        if not quality_passed:                              # 质量不达标才需要"救火"
            # 未达标: 尝试北大法宝 MCP 兜底(付费接口, 仅在达标跳过逻辑之外调用 —— 这就是降本设计)
            mcp_citations, mcp_ok = self._try_pku_law_mcp(query, top_k)   # 调用付费兜底, 返回 (结果, 是否成功)
            if mcp_ok and mcp_citations:                    # MCP 调用成功 且 有返回结果
                # 用 MCP 结果补充/替换低质量结果, 并标记来源(权威兜底数据优先展示)
                citations = mcp_citations[:top_k]           # 直接用 MCP 结果作为最终引用(截断到 top_k)
                mcp_used = True                             # 记录: 本次用了付费 MCP(便于计费审计)
                quality_score = min(0.95, quality_score + 0.15)   # MCP 兜底成功后小幅加分(封顶 0.95, 不让它虚高)
                quality_passed = quality_score >= QUALITY_GATE    # 重新判断是否达标(加分后可能达标)
            # MCP 兜底仍不达标: 触发人工介入标志(由上游重试环 / 前端展示提示)
            if not quality_passed:                          # 兜底后还是不达标
                need_human = True                           # 置起人工介入标志 —— 上游最多重试 MAX_RETRY=3 轮后交给人

        return {                                            # 组装最终返回字典(结构化输出)
            "citations": citations,                         # 最终引用列表(带 source 溯源标签)
            "research_context": research_context,           # 拼装好的检索上下文(供 LLM 引用)
            "quality_score": round(quality_score, 4),       # 质量分(保留 4 位小数)
            "quality_passed": quality_passed,               # 是否达标(达标 = 跳过付费 MCP 的依据)
            "mcp_used": mcp_used,                           # 是否用了付费 MCP(计费审计用)
            "conflict_warnings": conflict_warnings,         # 冲突消解提示列表
            "need_human_intervention": need_human,          # 是否需要人工介入
        }

    # ==================================================================
    # 数据源挂载(横向按需)
    # ==================================================================
    def _mount_sources(self, task_type: str, contract_type: str) -> list:
        """
        【功能】
        根据任务类型与合同类型决定挂载哪些数据源(横向按需挂载, 决定"查哪几个源")。

        【参数】
        - task_type : str     —— 任务类型(contract_review / legal_qa / legal_research ...);
        - contract_type : str —— 合同类型(如 "建设工程"), 空串表示非合同场景。

        【返回值】
        - list[str] —— 数据源名称列表(如 ["laws", "cases", "industry", "interpretations"])。

        【挂载矩阵】
          - 通用基础(任何任务必查): laws(法律法规) + cases(裁判案例);
          - 行业增强: contract_type 非空时追加 industry(行业标准);
          - 司法解释增强: legal_qa / legal_research / legal_document_gen 任务追加 interpretations。

        【逻辑】
        ① 先放基础两源(laws + cases); ② 有合同类型 -> 追加 industry; ③ 命中问答/检索/文书
        任务 -> 追加 interpretations; ④ 返回最终列表。
        """
        sources = [CORPUS_LAWS, CORPUS_CASES]            # 基础必查: 法规 + 案例(任何任务都要查)
        if contract_type:                                 # 合同审核/合规审查等场景会带合同类型
            sources.append(CORPUS_INDUSTRY)               # 追加行业标准源(与具体合同类型强相关)
        if task_type in ("legal_qa", "legal_research", "legal_document_gen"):   # 问答/检索/文书生成场景
            sources.append(CORPUS_INTERPRETATIONS)        # 追加司法解释源(增强法理解释能力)
        return sources                                    # 返回最终挂载的数据源列表

    # ==================================================================
    # 数据源标签(可溯源证据)
    # ==================================================================
    def _source_tag(self, corpus: str, doc: dict) -> str:
        """
        【功能】
        生成数据源标签, 用于 citation 溯源(前端展示 / 审计追踪): 让使用者一眼看出
        "这条引用来自哪个数据源、叫什么名字"。

        【参数】
        - corpus : str —— 数据源名称(laws/cases/industry/interpretations);
        - doc : dict  —— 文档字典(用于取名称字段)。

        【返回值】
        - str —— 标签字符串。

        【标签规则(与历史版本兼容, 便于旧节点解析)】
          - laws            -> "L1·法规·{law_name}"
          - cases           -> "L2·案例·{case_type}"
          - industry        -> "L3·行业标准·{standard_name}"
          - interpretations -> "L4·司法解释·{interpretation_name}"
          - 其它            -> "L5·{corpus}"

        【逻辑】
        按 corpus 分支拼接; L 前缀 + 序号用于前端排序/过滤, 名称用于展示。
        """
        if corpus == CORPUS_LAWS:                         # 法规源
            return f"L1·法规·{doc.get('law_name', '')}"   # 例: "L1·法规·中华人民共和国劳动法"
        if corpus == CORPUS_CASES:                        # 案例源
            return f"L2·案例·{doc.get('case_type', '')}"  # 例: "L2·案例·劳动争议"
        if corpus == CORPUS_INDUSTRY:                     # 行业标准源
            return f"L3·行业标准·{doc.get('standard_name', '')}"   # 例: "L3·行业标准·建设工程施工质量验收统一标准"
        if corpus == CORPUS_INTERPRETATIONS:              # 司法解释源
            return f"L4·司法解释·{doc.get('interpretation_name', '')}"   # 例: "L4·司法解释·最高人民法院关于适用民法典合同编通则若干问题的解释"
        return f"L5·{corpus}"                             # 未知源兜底标签

    # ==================================================================
    # 质量分计算
    # ==================================================================
    def _compute_quality(self, citations: list, query: str, keywords: list) -> float:
        """
        【功能】
        计算检索质量分(0~1), 用于质量门禁判断: 是否达标(>= 0.85)以跳过付费 MCP。

        【三个维度(加权合成)】
          1. 覆盖率 coverage(权重 0.4): 命中条数是否达到期望条数(期望按 6 条计);
             缺条意味着"召回不足", 该维度扣分;
          2. 相关性 relevance(权重 0.4): 引用内容与查询关键词的命中程度 ——
             统计"至少命中一个关键词"的引用占比;
          3. 时效性 timeliness(权重 0.2): 引用中非"已废止"标注的比例(法律场景时效极重要,
             引用已废止法条的法律意见毫无价值)。

        【参数】
        - citations : list —— 当前引用列表;
        - query : str —— 原始查询文本(关键词为空时退化提取用);
        - keywords : list —— 关键词列表(可为空)。

        【返回值】
        - float —— 0~1 的质量分。

        【逻辑】
        ① 无引用直接返回 0.0(没查到东西, 质量必然为 0);
        ② 覆盖率 = min(1, 命中数/6); ③ 关键词为空时从 query 提取;
        ④ 相关性 = 命中关键词的引用数 / 引用总数; ⑤ 时效性 = 非"已废止"引用数 / 引用总数;
        ⑥ 最终分 = 0.4*覆盖率 + 0.4*相关性 + 0.2*时效性。
        """
        if not citations:                                 # 一条引用都没有
            return 0.0                                    # 质量分直接为 0(召回完全失败)
        # 维度1: 覆盖率 —— 期望条数按 6 条计(与 top_k 解耦, 阈值稳定, 不会因调用方改 top_k 而波动)
        expect = 6                                        # 期望命中条数(固定值)
        coverage = min(1.0, len(citations) / expect)      # 命中数/期望数, 超过 6 条按 1.0 封顶

        # 维度2: 相关性 —— 统计引用中命中关键词的占比
        if not keywords:                                  # 调用方没给关键词
            # 从查询退化提取: 按标点/空白切分, 保留长度 >= 2 的词块
            keywords = [w for w in re.split(r'[，。；;：:\s]+', query or "") if len(w) >= 2]
        hit_count = 0                                     # 计数器: 至少命中一个关键词的引用条数
        for c in citations:                               # 遍历每条引用
            # 拼接该引用的检索文本: 条文内容 + 案例摘要 + 法律名 + 标题(覆盖各数据源字段)
            text = f"{c.get('content','')}{c.get('case_summary','')}{c.get('law_name','')}{c.get('title','')}"
            if any(k in text for k in keywords):          # 任一关键词出现在该引用文本里
                hit_count += 1                            # 计为"命中关键词的引用"
        relevance = hit_count / len(citations) if citations else 0.0   # 命中占比(防御除零)

        # 维度3: 时效性 —— 有效引用占比("已废止"降权项视为无效)
        valid_count = sum(1 for c in citations if "已废止" not in c.get("source", ""))   # 数一下 source 里不含"已废止"的条数
        timeliness = valid_count / len(citations)         # 有效引用占比(分母一定有值, 因为前面已判非空)

        # 加权合成(权重: 覆盖率 0.4 + 相关性 0.4 + 时效性 0.2, 合计 1.0)
        score = 0.4 * coverage + 0.4 * relevance + 0.2 * timeliness   # 加权平均得到最终质量分
        return score                                      # 返回 0~1 的质量分

    # ==================================================================
    # 北大法宝 MCP 兜底(付费接口, 仅在质量不达标时触发)
    # ==================================================================
    def _try_pku_law_mcp(self, query: str, top_k: int) -> tuple:
        """
        【功能】
        尝试调用北大法宝 MCP 付费接口补充权威检索结果(仅在质量门禁未达标时触发)。

        【设计动机(降本核心设计)】
        本地知识库(自建, 免费) + 北大法宝 MCP(按调用付费)。为了控制成本,
        只有本地检索质量分 < 0.85 时才调用 MCP; 达标则完全跳过 —— 这就是
        "质量门禁"存在的经济意义: 用免费本地检索挡住 90% 的查询, 付费兜底只接住少数难查的。

        【参数】
        - query : str —— 查询文本;
        - top_k : int —— 期望返回条数。

        【返回值】
        - tuple (list, bool): (citations 引用列表, 是否成功)。
          成功且非空时, citations 每项含 law_name/article_no/content/source/score 字段。

        【逻辑】
        ① 打印提示日志; ② import common.mcp_beidafabao 的 get_beida_mcp_client;
        ③ client.available 为 False(Token 未配置) -> 返回 ([], False), 进入人工介入分支;
        ④ 调用 client.search_all(query, top_k) 查权威数据库;
        ⑤ 有结果 -> 映射成统一字段结构, 返回 (citations, True);
        ⑥ 无结果/抛异常 -> 打印提示, 返回 ([], False), 保证流程不中断。
        """
        print("  [MCP] 质量分未达标, 尝试调用北大法宝 MCP 兜底...")   # 日志: 标记进入付费兜底路径(便于审计)
        try:                                              # 整个调用过程可能因网络/配置失败, 全部兜住
            from common.mcp_beidafabao import get_beida_mcp_client   # 延迟导入: 只有真要用 MCP 才 import
            client = get_beida_mcp_client()               # 获取 MCP 客户端单例
            if not client.available:                      # Token 未配置/服务不可用(available 是客户端属性)
                print("  [MCP] 北大法宝 Token 未配置, 跳过(进入人工介入提示)")   # 提示原因
                return [], False                          # 返回空 + 失败, 上层进入人工介入分支
            results = client.search_all(query, top_k=top_k)   # 调用付费接口, 全库检索
            if results:                                   # 有返回结果
                citations = [{                            # 把 MCP 返回的原始字段映射成统一结构
                    "law_name": r.get("title", ""),       # MCP 的 title 字段 -> 我们的 law_name(法律名)
                    "article_no": r.get("article_no", ""),   # 条号原样传递
                    "content": r.get("content", ""),      # 条文内容原样传递
                    "source": r.get("source", "MCP·北大法宝(付费)"),   # 来源: 有就用, 没有则标注付费来源
                    "score": r.get("score", 1.0),         # 分数: 有就用, 没有默认 1.0(权威数据默认高可信)
                } for r in results]                       # 列表推导式: 把每条 MCP 结果转成统一字典
                print(f"  [MCP] 北大法宝 MCP 兜底返回 {len(citations)} 条")   # 成功日志
                return citations, True                    # 返回 (引用列表, 成功=True)
            else:                                         # 接口可用但没查到东西
                print("  [MCP] 北大法宝 MCP 返回为空")     # 提示空结果
                return [], False                          # 返回 (空, 失败)
        except Exception as e:                            # 网络异常 / 导入失败 / 超时等
            print(f"  [MCP] 北大法宝 MCP 调用失败: {e}")   # 打印异常信息
            return [], False                              # 返回 (空, 失败) —— 流程绝不中断

    # ==================================================================
    # 便捷方法: 单源检索(供法规查询/案例检索页面直接调用)
    # ==================================================================
    def search_laws(self, query: str, keywords: list = None, top_k: int = 10) -> list:
        """
        【功能】
        法规查询便捷接口: 仅检索法律法规数据源(不经过 RRF 融合/冲突消解, 直接返回单源结果)。

        【参数】
        - query : str —— 查询文本;
        - keywords : list[str], optional —— 关键词(可空);
        - top_k : int —— 返回条数上限, 默认 10。

        【返回值】
        - list[dict] —— 法规文档列表(每项带 score 字段)。

        【逻辑】
        一行转发: 直接调用 kb.search_source(CORPUS_LAWS, ...) 即"法规源单源检索"。
        """
        return self.kb.search_source(CORPUS_LAWS, query, keywords, top_k=top_k)   # 转发给 KnowledgeBase 的单源检索

    def search_cases(self, query: str, keywords: list = None, top_k: int = 10,
                     case_type: str = "") -> list:
        """
        【功能】
        案例检索便捷接口: 仅检索裁判案例数据源, 并支持按案由(case_type)过滤。

        【参数】
        - query : str —— 查询文本;
        - keywords : list[str], optional —— 关键词(可空);
        - top_k : int —— 返回条数上限, 默认 10;
        - case_type : str —— 案由过滤条件(如 "劳动争议"), 非空时只保留案由完全相等的案例。

        【返回值】
        - list[dict] —— 案例文档列表(每项带 score 字段)。

        【逻辑】
        ① 先做单源检索; ② 若给了 case_type, 用列表推导式过滤出案由相等的案例; ③ 返回。
        """
        results = self.kb.search_source(CORPUS_CASES, query, keywords, top_k=top_k)   # 先查案例源
        if case_type:                                     # 调用方给了案由过滤条件
            results = [r for r in results if r.get("case_type") == case_type]   # 只保留案由完全相等的案例
        return results                                    # 返回过滤后的结果

    def search_industry(self, query: str, keywords: list = None, top_k: int = 10) -> list:
        """
        【功能】
        行业标准检索便捷接口: 仅检索行业标准数据源。

        【参数】
        - query : str —— 查询文本;
        - keywords : list[str], optional —— 关键词(可空);
        - top_k : int —— 返回条数上限, 默认 10。

        【返回值】
        - list[dict] —— 行业标准文档列表(每项带 score 字段)。

        【逻辑】
        一行转发: kb.search_source(CORPUS_INDUSTRY, ...)。
        """
        return self.kb.search_source(CORPUS_INDUSTRY, query, keywords, top_k=top_k)   # 行业标准源单源检索

    def search_interpretations(self, query: str, keywords: list = None, top_k: int = 10) -> list:
        """
        【功能】
        司法解释检索便捷接口: 仅检索司法解释数据源。

        【参数】
        - query : str —— 查询文本;
        - keywords : list[str], optional —— 关键词(可空);
        - top_k : int —— 返回条数上限, 默认 10。

        【返回值】
        - list[dict] —— 司法解释文档列表(每项带 score 字段)。

        【逻辑】
        一行转发: kb.search_source(CORPUS_INTERPRETATIONS, ...)。
        """
        return self.kb.search_source(CORPUS_INTERPRETATIONS, query, keywords, top_k=top_k)   # 司法解释源单源检索

    # ==================================================================
    # 知识库统计(前端展示数据源状态)
    # ==================================================================
    def kb_stats(self) -> dict:
        """
        【功能】
        统计各数据源文档数量, 供前端"数据源状态"面板展示(如 "法律法规: 1234 条")。

        【参数】
        无。

        【返回值】
        - dict —— {数据源名: 文档条数}, 如 {"laws": 1234, "cases": 567, "industry": 89, "interpretations": 45};
          文件缺失的源返回 0(load_docs 内部已处理)。

        【逻辑】
        ① 遍历 4 个数据源常量; ② 对每个源调用 load_docs 加载并取长度; ③ 汇总成字典返回。
        """
        stats = {}                                        # 统计结果字典
        for corpus in (CORPUS_LAWS, CORPUS_CASES, CORPUS_INDUSTRY, CORPUS_INTERPRETATIONS):   # 遍历 4 个源
            docs = self.kb.load_docs(corpus)              # 加载该源文档(文件缺失时返回 [])
            stats[corpus] = len(docs)                     # 记录文档条数(缺失则为 0)
        return stats                                      # 返回统计字典


# ======================================================================
# 全局单例: 所有调用方共享同一个引擎实例(避免重复加载索引/模型)
# ======================================================================
# 模块级单例模式, 与 common/llm.py 的 my_llm 写法一致:
# 首次 import 本模块时执行下面这一行, 创建唯一实例;
# 之后所有 from common.retrieval_engine import engine 拿到的都是同一个对象,
# 从而 KnowledgeBase 的 _cache 与各索引只构建一次, 省内存、省启动时间。
engine = RetrievalEngine()      # 创建全局唯一引擎实例(此时不加载任何数据, 懒加载到首次 search 才发生)


# ======================================================================
# CLI 自测入口: python -m common.retrieval_engine "违约金上限"
# ======================================================================
if __name__ == "__main__":                                # 只有"直接运行本文件"时(__name__ == "__main__")才执行;
    import sys                                            # 标准库 sys: 读取命令行参数 sys.argv
    # 取命令行参数作为查询词: sys.argv[1] 是第一个参数; 没传参数时默认测试"违约金"
    _q = sys.argv[1] if len(sys.argv) > 1 else "违约金"    # 例: python -m common.retrieval_engine "违约金上限" -> _q = "违约金上限"
    # 打印知识库统计: 验证各数据源加载状态(哪几个源有数据、各多少条)
    print(f"📊 知识库统计: {engine.kb_stats()}")           # 调用 kb_stats() 并打印
    # 执行一次完整检索(独立可用性自测: 验证整条流水线能跑通)
    print(f"\n🔍 检索测试: {_q}")                          # 打印本次测试查询词
    result = engine.search(_q, task_type="legal_research", top_k=5)   # 用"法律检索"任务类型跑一次完整检索, 取 5 条
    print(f"   质量分: {result['quality_score']} (达标: {result['quality_passed']}, 人工介入: {result['need_human_intervention']})")   # 打印质量门禁三要素
    print(f"   命中 {len(result['citations'])} 条:")       # 打印命中条数
    for c in result["citations"]:                         # 遍历每条引用
        name = c.get("law_name") or c.get("title") or c.get("case_title") or ""   # 取名称(多字段兜底)
        print(f"   - [{name}] {c.get('article_no', c.get('case_no', ''))} 来源:{c.get('source','')} score:{c.get('score', c.get('rrf_score', ''))}")   # 打印 名称/编号/来源/分数
