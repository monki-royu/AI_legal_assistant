"""【文件作用】审核/审查节点共享工具 ── "规则预筛 + 切分材料组装 + 检索依据注入 + 风险回填"四件套
【逻辑】本文件为 合同审核(contract_ai_review_node) 与 合规审查(compliance_review_node)
    提供共享的**机制层**函数。

【为什么要抽出这个文件？】
    两条链路的架构【并不相同】：
      - 合同审核：立场化审核，注入 user_side，规则集 = "商业条款风险信号"
      -合规审查：客观中立审查，user_side 硬隔离，规则集 = "法规合规信号"
    但它们共用同一套**机制**：
      ① 用正则规则表对切分单元做预筛（规则层）
      ② 把 doc_segments 组装成带编号的待审查材料
      ③ 把 review_context_bundle 渲染成"法条依据 + 未覆盖提示"
      ④ 把 LLM 返回的 segment_id 归一化回填到原文单元
    把【机制】放在这里共享、把【规则集与立场】留在各节点内，
    正好让"两条链路的差异"在代码里一目了然 —— 差异即各节点独有的部分。

【设计原则：规则只做提示，不下结论】
    正则规则擅长"确定性命中"但不懂语义；LLM 擅长"理解与表达"但存在漏检与随机性。
    因此规则层只产出"请重点看第几条、可能是什么问题"的注意力锚点，
    最终是否构成风险仍由 LLM 判定 —— 规则误命中不会变成假风险。

【本文件被谁使用】
    - __004__langgraph_more_nodes/nodes/contract_ai_review_node.py
    - __004__langgraph_more_nodes/nodes/compliance_review_node.py
"""

# ============================================================
# 📦 导入模块
# ============================================================

# 导入 re 模块：规则预筛层做正则匹配、segment_id 归一化时抠数字
import re


# ============================================================
# ① 规则层：预筛切分单元
# ============================================================

def prescreen_segments(segments, rule_signals, max_hits=40):
    """
    【功能】规则预筛：用正则规则表扫描切分单元，产出"风险信号命中清单"。
    【参数】segments (List[Dict])：doc_segments 切分单元列表，每项含 id/type/title/text；
            rule_signals (List[Tuple[str, Pattern]])：(信号名, 已编译正则) 列表，
                由调用方节点自行提供 —— 合同审核传商业信号表，合规审查传合规信号表；
            max_hits (int)：命中上限，防止超长文档把 prompt 挤爆，默认 40。
    【返回值】List[Dict]：[{"segment_id":int, "title":str, "signals":List[str]}, ...]
    【逻辑】逐单元 × 逐规则做 search，命中即记录信号名；同一单元的多个信号合并成一条。
    【设计说明】只回传"单元编号 + 命中的信号名"，不回传正文 —— 正文已在审查材料里，
            这里只需给 LLM 一个指针，省 token。
    """
    hits = []
    for seg in segments:
        # 防御：上游若产出非 dict 元素，跳过而不报错
        if not isinstance(seg, dict):
            continue
        text = seg.get("text", "") or ""
        # 列表推导：把所有命中的信号名收集起来（一个单元可能同时命中多个信号）
        matched = [name for name, pat in rule_signals if pat.search(text)]
        if matched:
            hits.append({
                "segment_id": seg.get("id"),
                "title": seg.get("title", ""),
                "signals": matched,
            })
        # 达到上限即停止，保护 prompt 长度
        if len(hits) >= max_hits:
            break
    return hits


def render_prescreen_hint(hits, header):
    """
    【功能】把规则预筛命中清单渲染成可直接嵌入 prompt 的文本片段。
    【参数】hits (List[Dict])：prescreen_segments 的返回值；
            header (str)：提示标题，由调用方定制（合同/合规措辞不同）。
    【返回值】str：命中为空时返回空串（不影响原有 prompt 行为）。
    【逻辑】每条渲染成 "- [编号] 标题：疑似 信号1、信号2"，并在标题中声明"仅为线索"。
    """
    if not hits:
        return ""
    hit_lines = "\n".join(
        f"- [{h['segment_id']}] {h['title']}：疑似 {'、'.join(h['signals'])}"
        for h in hits
    )
    return f"\n【{header}（仅为规则线索，需你自行确认是否真的构成风险）】\n{hit_lines}\n"


# ============================================================
# ② 材料层：把切分单元组装成待审查材料
# ============================================================

def build_review_text(segments, fallback_text, max_chars=12000):
    """
    【功能】把切分单元组装成带编号的"待审查材料"文本；无切分单元时回退全文。
    【参数】segments (List[Dict])：doc_segments 切分单元；
            fallback_text (str)：回退用的 doc_text 全文；
            max_chars (int)：材料文本上限（token 预算控制），默认 12000。
    【返回值】Tuple[str, str]：(材料文本, 材料来源说明)
    【逻辑】每个单元渲染成 "[编号|类型] 标题：正文"，用空行拼接；
            超上限则截断并显式提示 LLM"未展示部分请在结论中说明未覆盖"。
    【为什么必须带编号？】只有把 segment_id 显式写进材料，LLM 才能在输出里准确回填
            segment_id，实现"风险 → 原文单元"的可追溯锚定。
            旧实现直接丢 doc_text[:5000]，所以"按条款号定位"只是 LLM 随手写的文字。
    """
    if segments:
        lines = []
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            sid = seg.get("id", "?")
            stype = seg.get("type", "")
            title = seg.get("title", "") or f"单元{sid}"
            body = seg.get("text", "") or ""
            lines.append(f"[{sid}|{stype}] {title}：{body}")
        text = "\n\n".join(lines)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n...(材料过长已截断，未展示部分请在结论中说明未覆盖)"
        return text, "以下为【全文本统一切分】后的文档材料（每个单元带 [编号|类型] 前缀，含前言/条款/段落，无遗漏）"
    # 回退分支：上游切分未产出结果（极端情况），退回全文，保证节点仍可工作
    return (fallback_text or "")[:max_chars], "以下为文档全文（上游未产出切分单元，无法按单元编号定位）"


# ============================================================
# ③ 依据层：把检索上下文包渲染成法条依据提示
# ============================================================

def build_law_hint(bundle, max_uncovered=12, max_context_chars=1500):
    """
    【功能】把检索上下文包渲染成 prompt 可注入的"法条依据 + 未覆盖提示"文本。
    【参数】bundle (Dict)：review_context_bundle（由 context_pack_node 产出）；
            max_uncovered (int)：未覆盖单元展示上限，默认 12；
            max_context_chars (int)：检索上下文原文摘要上限，默认 1500。
    【返回值】str：可直接嵌入 prompt 的文本片段（bundle 为空时返回空串）。
    【逻辑】① 渲染检索原始查询与关键词 ② 渲染精简引用(法条清单)
            ③ 渲染检索上下文原文摘要 ④ 渲染"未被检索覆盖的单元编号" + 覆盖率。
    【为什么要主动告知"没有依据"？】避免 LLM 把"检索到的法条"误当成"全部依据"。
            对未覆盖部分，要么按常识判断并明确标注、要么说明依据不足 —— 防止幻觉引用。
            这也是"检索提前"真正落地的关键：不仅把召回结果喂下来，
            还把【没检索到的部分】一并喂下来，让下游知道自己的知识边界在哪。
    """
    if not bundle or not isinstance(bundle, dict):
        return ""

    parts = []

    # ① 检索原始查询文本 + 关键词：让 LLM 知道"检索是围绕什么问题做的"
    query = bundle.get("retrieval_query", "") or ""
    keywords = bundle.get("retrieval_keywords", []) or []
    if query or keywords:
        parts.append(f"【检索原始查询】{query}\n【检索关键词】{', '.join(keywords)}")

    # ② 精简引用清单：法条号 + 正文摘要，供 LLM 在 legal_basis 字段中准确引用
    brief = bundle.get("citations_brief", []) or []
    if brief:
        cite_lines = []
        for i, c in enumerate(brief, start=1):
            title = c.get("title", "")
            art = c.get("article_no", "")
            content = c.get("content", "")
            cite_lines.append(f"({i}) 《{title}》{art}：{content}")
        parts.append("【已检索到的法条/案例依据（请优先引用这些，不要编造法条）】\n" + "\n".join(cite_lines))

    # ③ 检索上下文原文摘要：补充引用清单之外的连续上下文
    ctx = bundle.get("research_context", "") or ""
    if ctx:
        parts.append("【检索上下文原文（摘要）】\n" + ctx[:max_context_chars])

    # ④ 未被检索覆盖的单元：显式列出编号与标题，提醒"此处无法条支撑"
    uncovered = bundle.get("unretrieved_segments", []) or []
    if uncovered:
        tags = "、".join(
            f"[{u.get('id')}]{u.get('title', '')}" for u in uncovered[:max_uncovered]
        )
        more = f"（另有 {len(uncovered) - max_uncovered} 个未列出）" if len(uncovered) > max_uncovered else ""
        parts.append(
            "【未被检索覆盖的单元（这些内容没有对应法条依据，需你自行判断；"
            "若无法给出依据，法律依据字段请写\"无检索依据·凭经验判断\"）】\n"
            f"{tags}{more}"
        )
        cov = bundle.get("coverage_ratio", 0)
        parts.append(f"【检索覆盖率】{cov}（覆盖率偏低时请谨慎引用法条，避免张冠李戴）")

    return "\n\n".join(parts)


def build_law_block(bundle, fallback_note):
    """
    【功能】build_law_hint 的外壳：有检索上下文时返回依据文本，没有时返回兜底提示。
    【参数】bundle (Dict)：review_context_bundle；
            fallback_note (str)：无检索上下文时的兜底提示语（各节点措辞不同）。
    【返回值】str：始终非空，保证 prompt 里"法条依据"这一段永远存在（要么给依据，要么明说没有）。
    【为什么要兜底？】若检索链路降级(异常/无结果)，prompt 里若完全不提依据，
            LLM 会自由编造法条。显式写明"本次无检索依据"能显著抑制幻觉引用。
    """
    law_hint = build_law_hint(bundle)
    return f"\n{law_hint}\n" if law_hint else f"\n{fallback_note}\n"


# ============================================================
# ④ 回填层：把 LLM 输出的 segment_id 归一化锚定回原文单元
# ============================================================

def normalize_segment_ids(risks, segments, title_key="segment_title"):
    """
    【功能】把 LLM 返回的 segment_id 归一化为合法 int 或 None，并补齐单元标题。
    【参数】risks (List[Dict])：LLM 解析出的风险项列表（就地修改）；
            segments (List[Dict])：doc_segments，用于建立 id → 单元 的索引表；
            title_key (str)：回填标题所用的字段名，默认 "segment_title"。
    【返回值】List[Dict]：同一个 risks 对象（就地修改后返回，便于链式调用）。
    【为什么要归一化？】LLM 可能把 segment_id 写成字符串 "3"、"[3]"、"单元3"、"null"，
            甚至漏掉该字段。下游 risk_aggregate_node 与前端原文高亮需要稳定的 int/None。
    【逻辑】① 建 id 索引表 ② 用正则从任意写法里抠出第一个整数
            ③ 该整数必须真实存在于索引表中才保留，否则置 None（绝不伪造编号）
            ④ 命中时补一个标题字段，前端可直接显示"风险出自第几条"
    """
    seg_index = {
        seg.get("id"): seg
        for seg in (segments or [])
        if isinstance(seg, dict) and seg.get("id") is not None
    }
    for item in risks:
        if not isinstance(item, dict):
            continue
        raw_sid = item.get("segment_id")
        norm_sid = None
        if raw_sid is not None:
            # 从 "3" / "[3]" / "单元3" 之类的写法里抠出第一个整数
            m = re.search(r'\d+', str(raw_sid))
            if m:
                candidate = int(m.group())
                # 关键校验：编号必须真实存在，防止 LLM 凭空编造单元号
                if candidate in seg_index:
                    norm_sid = candidate
        item["segment_id"] = norm_sid
        if norm_sid is not None:
            item[title_key] = seg_index[norm_sid].get("title", "")
    return risks


# ============================================================
# 🧪 模块自测入口（仅在直接运行本文件时执行）
# ============================================================
if __name__ == "__main__":
    demo_segments = [
        {"id": 1, "type": "preamble", "title": "前言", "text": "本合同由甲方与乙方签订。"},
        {"id": 2, "type": "clause", "title": "第三条", "text": "逾期付款按每日千分之五支付违约金。"},
    ]
    demo_rules = [("违约金比例", re.compile(r'违约金.{0,20}(千分之|百分之|%)|(?:千分之|百分之)\S{0,4}违约金'))]
    print("预筛:", prescreen_segments(demo_segments, demo_rules))
    print("材料:", build_review_text(demo_segments, "回退全文")[1])
    print("依据:", build_law_block({}, "【检索依据】本次未获得检索上下文。"))
    print("回填:", normalize_segment_ids([{"segment_id": "单元2"}, {"segment_id": "99"}], demo_segments))
