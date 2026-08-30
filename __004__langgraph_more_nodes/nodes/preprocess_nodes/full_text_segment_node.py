"""N4 全文本统一切分节点: 对文档【全部文本】做统一切分, 替代原"仅按第X条"的条款切分

============================================================
文件名称: nodes/full_text_segment_node.py
文件作用: 全文本统一切分(Full-Text Unified Segmentation)
============================================================

【这个文件是干什么的？】
    把 doc_extract 提取出的整篇 doc_text, 切成一串"可定位、可引用、可逐条审查"的
    结构化切分单元(segment), 写入 state["doc_segments"];
    同时为兼容既有下游, 把其中"第X条"类单元投影成旧字段 state["doc_clauses"]。

【为什么要新增这个节点(它替代了谁、修了什么 Bug)】
    旧节点 clause_split_node 只以"第X条"为锚点做 re.split, 存在三个真实缺陷:
      ① 【前言被丢弃】re.split 结果的 index 0 是"第一个第X条之前的全部文本"
         (合同标题、甲乙方信息、鉴于条款、前言声明), 旧代码从 i=1 开始遍历,
         直接把这一整块扔掉 —— 而"鉴于条款/免责声明"里恰恰常藏风险。
      ② 【非结构化大段没被真正切分】若文档没有"第X条"标记, 旧代码回退为按 "\n" 分段;
         若正文是一整块没有换行的长文本(很多 PDF 抽取结果就是这样),
         就会退化成"1 个条款 = 整篇全文", 等于没切。
      ③ 【超长条款不再细分】某些"第X条"下辖十几个子款、长达数千字,
         整条丢给 LLM 会挤爆上下文, 且无法精确定位到具体子款。
    本节点用"锚点定位 + 前言保留 + 段落聚合 + 句界二次切分"四层策略修掉上述三点,
    做到【全文本无遗漏地进入切分结果】。

【切分策略(四层, 纯本地规则, 零 LLM 成本)】
    第1层 结构锚点: 用正则 finditer 定位所有 "第X条" 出现位置(而非 re.split),
                    这样能拿到每个锚点的精确字符偏移, 便于计算区间与定位。
    第2层 前言保留: 第一个锚点之前的文本不再丢弃, 标记 type="preamble" 单独成段;
                    若前言过长, 继续按第3层策略切成多个单元。
    第3层 段落聚合: 对"非结构化文本"(前言 / 无锚点文档 / 超长条款正文),
                    按换行分段后【累加聚合】到目标长度(TARGET_CHARS)再切,
                    避免"一行一段"造成碎片过多。
    第4层 句界兜底: 若单个段落本身就超过硬上限(MAX_CHARS, 例如一整块无换行长文),
                    按中文句末标点(。；！？)聚合切分, 保证任何输入都能被切开。

【输出字段】
    state["doc_segments"]: List[Dict], 每个元素:
        {
          "id":         int,   切分单元序号(从 1 连续递增, 全局唯一, 供风险项引用定位)
          "type":       str,   单元类型: preamble(前言) / clause(第X条) / paragraph(普通段落)
          "title":      str,   单元标题: "前言"/"前言-2"/"第一条"/"第一条(2/3)"/"段落5"
          "text":       str,   单元正文(clause 类型含标题前缀, 避免上下文断裂)
          "char_start": int,   在 doc_text 中的起始字符偏移(可用于原文高亮定位)
          "char_end":   int,   在 doc_text 中的结束字符偏移
        }
    state["doc_clauses"]: List[Dict], 向后兼容字段(旧下游仍在读它),
        取 doc_segments 中 type=="clause" 的单元投影成 {"id","title","text"};
        若文档完全没有"第X条"(纯非结构化), 则退化为全部单元的投影, 保证非空。

【谁读取本节点的产物】
    - contract_ai_review_node(N5a): 按 segment 定位商业风险(立场化审核)
    - compliance_review_node(N5b):  按 segment 定位合规风险(中立审查)
    - context_pack_node:            判定哪些 segment 已被检索覆盖、哪些未被覆盖
    - final_delivery_node:          报告中按单元编号引用原文

【可迁移性】
    "锚点定位 + 前言保留 + 段落聚合 + 句界兜底"是通用的长文档切分范式,
    换掉第1层的锚点正则(如法规"第X条"、论文"第X章"、标准"X.Y.Z"),
    即可迁移到任意结构化/半结构化长文档的 RAG 切分场景。
"""

# 导入 re 模块: 提供正则表达式的 finditer / split 等能力(纯标准库, 零依赖)
import re

# 从同包导入 AgentState 类型, 作为节点函数的类型注解(TypedDict, 运行时即普通 dict)
from __004__langgraph_more_nodes.agent_state import AgentState


# ============================================================
# 📐 切分参数与正则(集中定义, 便于统一调参)
# ============================================================

# 【结构锚点正则】匹配 "第一条" / "第1条" / "第一百零八条" 等条款起始标记
# 说明: 与旧 clause_split_node 保持同一套锚点规则, 保证对标准合同的兼容性;
#       区别在于这里用 finditer(拿偏移), 而不是 re.split(丢偏移且丢前言)。
CLAUSE_ANCHOR = re.compile(r'第[一二三四五六七八九十百千零\d]+条[、.\s]*')

# 【句末标点切分正则】用于第4层"句界兜底"切分
# (?<=...) 是零宽后视断言: 在标点【之后】切开, 从而把标点保留在前一句末尾
SENTENCE_BOUNDARY = re.compile(r'(?<=[。；;!?！？])')

# 【目标聚合长度】非结构化文本按段落累加到该长度就切一刀(单位: 字符)
# 600 字约等于 LLM 一个"可独立判断"的语义块, 既不过碎也不过粗
TARGET_CHARS = 600

# 【单元硬上限】任何切分单元不得超过该长度, 超过则继续二次切分
# 1200 字是"单条款级别"的经验上限, 超过通常意味着该条下辖多个子款
MAX_CHARS = 1200


def _split_by_sentence(text: str, base_offset: int = 0, max_chars: int = MAX_CHARS):
    """
    【功能】第4层兜底切分: 对"单段就超长"的文本, 按中文句末标点聚合切分。
    【参数】text (str): 待切分文本; base_offset (int): 该文本在 doc_text 中的绝对起始偏移;
            max_chars (int): 单块硬上限。
    【返回值】List[Tuple[str, int, int]]: [(块文本, 绝对起始偏移, 绝对结束偏移), ...]
    【逻辑】按句末标点拆成句子 -> 逐句累加进缓冲区 -> 缓冲区将超上限时先落盘再继续。
    """
    # 按句末标点拆句(标点保留在句尾), 过滤空串
    pieces = [p for p in SENTENCE_BOUNDARY.split(text) if p]

    out = []          # 结果列表
    buf = ""          # 当前累加缓冲区
    buf_start = 0     # 缓冲区在 text 内的相对起始偏移
    cursor = 0        # 扫描游标(相对偏移)

    for piece in pieces:
        # 若缓冲区已有内容, 且再加这一句就会超上限 -> 先把缓冲区落盘
        if buf and len(buf) + len(piece) > max_chars:
            out.append((buf, base_offset + buf_start, base_offset + cursor))
            buf = ""
        # 缓冲区为空时, 记录本块的起始偏移
        if not buf:
            buf_start = cursor
        # 累加当前句到缓冲区, 并推进游标
        buf += piece
        cursor += len(piece)

    # 循环结束后把残留缓冲区落盘(注意判空: 全空白则丢弃)
    if buf.strip():
        out.append((buf, base_offset + buf_start, base_offset + cursor))

    return out


def _pack_paragraphs(text: str, base_offset: int = 0,
                     target: int = TARGET_CHARS, max_chars: int = MAX_CHARS):
    """
    【功能】第3层段落聚合: 把非结构化文本按换行分段, 再累加聚合到 target 长度成块。
    【参数】text (str): 待切分文本; base_offset (int): 该文本在 doc_text 中的绝对起始偏移;
            target (int): 目标聚合长度; max_chars (int): 单块硬上限。
    【返回值】List[Tuple[str, int, int]]: [(块文本, 绝对起始偏移, 绝对结束偏移), ...]
    【逻辑】逐行扫描并维护绝对偏移 -> 空行跳过 -> 单行超上限则交给句界兜底 ->
            其余按 target 聚合成块。
    """
    out = []          # 结果列表
    buf = ""          # 当前累加缓冲区
    buf_start = 0     # 缓冲区在 text 内的相对起始偏移
    buf_end = 0       # 缓冲区在 text 内的相对结束偏移
    cursor = 0        # 逐行扫描游标(相对偏移)

    # text.split("\n") 会去掉换行符本身, 因此游标推进要 +1 补回换行符占位,
    # 这样 char_start / char_end 才能与原始 doc_text 对齐
    for raw_line in text.split("\n"):
        line_start = cursor                 # 本行在 text 内的起始偏移
        line_end = cursor + len(raw_line)   # 本行在 text 内的结束偏移
        cursor = line_end + 1               # 推进游标(+1 补回被 split 吃掉的 "\n")

        line = raw_line.strip()             # 去掉首尾空白后的行内容
        if not line:                        # 空行: 不参与切分, 直接跳过
            continue

        # 情况A: 单行本身就超过硬上限(典型: PDF 抽取出的一整块无换行长文)
        if len(line) > max_chars:
            # 先把已有缓冲区落盘, 保证顺序不乱
            if buf:
                out.append((buf, base_offset + buf_start, base_offset + buf_end))
                buf = ""
            # 该超长行交给第4层"句界兜底"继续切
            for sub, s, e in _split_by_sentence(raw_line, base_offset + line_start, max_chars):
                out.append((sub, s, e))
            continue

        # 情况B: 缓冲区已有内容, 且再加这一行就超过目标长度 -> 先落盘
        if buf and len(buf) + len(line) + 1 > target:
            out.append((buf, base_offset + buf_start, base_offset + buf_end))
            buf = ""

        # 把当前行并入缓冲区(缓冲区为空则同时记录起始偏移)
        if not buf:
            buf = line
            buf_start = line_start
        else:
            buf = buf + "\n" + line
        buf_end = line_end

    # 落盘残留缓冲区
    if buf.strip():
        out.append((buf, base_offset + buf_start, base_offset + buf_end))

    return out


def full_text_segment_node(state: AgentState):
    """
    【功能】全文本统一切分节点: 把 doc_text 全量切成 doc_segments(并投影出兼容的 doc_clauses)。
    【参数】state (AgentState): LangGraph 共享状态字典。
            读取字段: doc_text (str, 可选) —— 文档全文
            写入字段: doc_segments (List[Dict]) —— 统一切分单元(含 preamble/clause/paragraph)
                      doc_clauses  (List[Dict]) —— 向后兼容投影 {"id","title","text"}
    【返回值】AgentState: 更新后的状态字典, 必含上述两个字段(可能为空列表)。
    【逻辑】① 空文档直接返回空结果
            ② finditer 定位所有"第X条"锚点
            ③ 有锚点: 前言保留(段落聚合) + 逐条款切分(超长再聚合)
            ④ 无锚点: 全文走段落聚合 + 句界兜底
            ⑤ 统一编号并写入 doc_segments
            ⑥ 投影出向后兼容的 doc_clauses
    """
    # 【步骤1】打印节点开始日志
    print("开始全文本统一切分")

    # 从状态字典取出文档全文, 缺失则为空字符串
    doc_text = state.get("doc_text", "") or ""

    # 空文档(仅空白)直接写空结果返回, 避免下游拿到 None
    if not doc_text.strip():
        state["doc_segments"] = []
        state["doc_clauses"] = []
        print("完成全文本统一切分: 0 个单元(文档为空)")
        return state

    # segments 收集最终切分单元
    segments = []

    def _add(seg_type: str, title: str, text: str, start: int, end: int):
        """内部辅助: 追加一个切分单元(自动编号, 自动过滤空白单元)"""
        cleaned = text.strip()
        if not cleaned:
            return
        segments.append({
            "id": len(segments) + 1,   # 全局连续编号, 供风险项引用定位
            "type": seg_type,          # preamble / clause / paragraph
            "title": title,            # 人类可读标题
            "text": cleaned,           # 单元正文
            "char_start": start,       # 原文起始偏移(供 PDF/前端高亮)
            "char_end": end,           # 原文结束偏移
        })

    # 【步骤2】定位所有"第X条"锚点(finditer 保留每个锚点的字符偏移)
    anchors = list(CLAUSE_ANCHOR.finditer(doc_text))

    if anchors:
        # ========================================================
        # 【步骤3-A】有结构锚点: 前言保留 + 逐条款切分
        # ========================================================

        # ---- 3-A-1 前言(第一个锚点之前的全部文本) ----
        # 【关键修复】旧节点在这里把整块前言丢掉了; 现在保留并按段落聚合切分。
        preamble_raw = doc_text[:anchors[0].start()]
        if preamble_raw.strip():
            pre_blocks = _pack_paragraphs(preamble_raw, 0)
            for pi, (chunk, s, e) in enumerate(pre_blocks):
                # 前言只有一块时标题就叫"前言", 多块时加序号便于引用
                title = "前言" if len(pre_blocks) == 1 else f"前言-{pi + 1}"
                _add("preamble", title, chunk, s, e)

        # ---- 3-A-2 逐个"第X条"区间切分 ----
        for idx, m in enumerate(anchors):
            # 本条款起点 = 当前锚点起始位置(含"第X条"标题, 保留上下文)
            start = m.start()
            # 本条款终点 = 下一个锚点起始位置; 最后一条则到全文末尾
            end = anchors[idx + 1].start() if idx + 1 < len(anchors) else len(doc_text)
            raw = doc_text[start:end]
            # 标题取锚点原文并去掉尾随分隔符(如"第一条、" -> "第一条")
            title = m.group().strip().rstrip('、.').strip()

            if len(raw) <= MAX_CHARS:
                # 常规长度条款: 整条作为一个单元
                _add("clause", title, raw, start, end)
            else:
                # 【关键修复】超长条款(下辖多个子款)继续二次切分, 标题带 (i/n) 便于定位
                blocks = _pack_paragraphs(raw, start)
                total = len(blocks)
                for bi, (chunk, s, e) in enumerate(blocks):
                    sub_title = title if total == 1 else f"{title}({bi + 1}/{total})"
                    _add("clause", sub_title, chunk, s, e)
    else:
        # ========================================================
        # 【步骤3-B】无结构锚点(非标准合同 / 纯非结构化长文本)
        # 【关键修复】旧节点在此退化为"按换行分段", 无换行时等于不切;
        #            现在走"段落聚合 + 句界兜底", 任何输入都能被有效切开。
        # ========================================================
        blocks = _pack_paragraphs(doc_text, 0)
        for bi, (chunk, s, e) in enumerate(blocks):
            _add("paragraph", f"段落{bi + 1}", chunk, s, e)

    # 【步骤4】写入统一切分结果
    state["doc_segments"] = segments

    # ============================================================
    # 【步骤5】投影出向后兼容的 doc_clauses
    # ============================================================
    # 【为什么保留 doc_clauses？】既有下游(compliance_review / final_delivery /
    # 报告模板)仍在读这个字段。为"最小改动"起见, 这里做一次投影而非强迫下游全改:
    #   - 优先取 type=="clause" 的单元(语义等价于旧的"第X条"条款);
    #   - 若整篇没有"第X条"(纯非结构化), 则退化为全部单元投影, 保证字段非空。
    clause_units = [s for s in segments if s["type"] == "clause"]
    if not clause_units:
        clause_units = segments
    state["doc_clauses"] = [
        {"id": i + 1, "title": u["title"], "text": u["text"]}
        for i, u in enumerate(clause_units)
    ]

    # 【步骤6】打印完成日志(分类型统计, 便于排查切分质量)
    n_pre = sum(1 for s in segments if s["type"] == "preamble")
    n_clause = sum(1 for s in segments if s["type"] == "clause")
    n_para = sum(1 for s in segments if s["type"] == "paragraph")
    print(f"完成全文本统一切分: {len(segments)} 个单元 "
          f"(前言 {n_pre} / 条款 {n_clause} / 段落 {n_para}), "
          f"兼容 doc_clauses {len(state['doc_clauses'])} 条")

    # 返回更新后的状态字典
    return state


# ============================================================
# 🧪 模块自测入口(直接运行本文件时执行)
# ============================================================
if __name__ == "__main__":
    # 测试1: 含前言 + 第X条 的标准合同 —— 验证"前言不再丢失"
    demo = (
        "本合同由甲方（A科技有限公司）与乙方（B贸易有限公司）于2026年3月签订。\n"
        "鉴于：乙方具备相关供货资质，甲方有采购需求，双方达成如下协议。\n"
        "第一条 标的及数量：甲方向乙方采购服务器100台。\n"
        "第二条 价款及支付：单价50000元，总价5000000元，预付款30%。\n"
        "第八条 违约责任：任何一方逾期付款，每日按未付金额的千分之五支付违约金。\n"
    )
    s1 = AgentState(doc_text=demo)
    full_text_segment_node(s1)
    for seg in s1["doc_segments"]:
        print(f"  [{seg['id']}] {seg['type']:9s} {seg['title']:12s} {seg['text'][:30]}...")

    # 测试2: 一整块无换行的非结构化长文 —— 验证"不再退化为1条"
    s2 = AgentState(doc_text=("甲方应当按期交付货物。" * 200))
    full_text_segment_node(s2)
    print(f"  非结构化长文切分单元数: {len(s2['doc_segments'])}")
