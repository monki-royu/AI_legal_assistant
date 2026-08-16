# 📜 ============================================================
# 文件名称: nodes/credit_precheck_node.py
# 文件作用: 资信预判定节点
# ============================================================
#
# 【这个文件是干什么的？】
# 在意图路由之后提前触发企查查查询并缓存，供下游链路复用。三层触发判断。
#
# 【代码逻辑主线】
# 三层触发判定 → 调用企查查 → 写入 credit_precheck_cache
#
# 【谁在调用它？】
# langgraph_main.py 中的 build_graph() 通过 add_node 注册本节点，
# 并通过 add_edge / add_conditional_edges 定义其前后依赖。



import re

from __004__langgraph_more_nodes.agent_state import AgentState


# ======================================================================
# 资信关键词集合 (层级3弱触发用)
# ======================================================================
_CREDIT_KEYWORDS = {
    # 资信类
    "资信", "信用", "信用评分", "信用等级", "征信", "企业信用",
    # 司法负面类
    "失信", "老赖", "被执行", "被执行人", "限制高消费", "限高",
    # 经营状态类
    "经营异常", "吊销", "注销", "停业", "清算", "破产",
    # 工商类
    "工商", "注册资本", "法定代表人", "法人", "股东", "股权", "实缴",
    # 处罚类
    "行政处罚", "监管处罚", "罚款", "警告", "列入严重违法",
}

# 层级1强触发任务 (必查)
_STRONG_TRIGGER_TASKS = {"contract_review", "compliance_review"}
# 层级2中触发任务 (有企业名则查)
_MEDIUM_TRIGGER_TASKS = {"legal_research", "legal_qa"}
# 层级3弱触发任务 (关键词触发)
_WEAK_TRIGGER_TASKS = {"legal_research", "legal_qa", "other"}

# 查询方法说明 (层级3弱触发无企业名时写入 guidance)
_CREDIT_GUIDANCE = (
    "📌 企业资信查询方法说明:\n"
    "  1. 登录「企查查」官网 https://www.qcc.com\n"
    "  2. 在搜索框输入完整企业名称(建议包含「有限公司/股份公司」后缀)\n"
    "  3. 重点查看以下栏目:\n"
    "     - 「基本信息」: 经营状态、注册资本、法定代表人\n"
    "     - 「司法风险」: 失信被执行人、被执行人、限制高消费\n"
    "     - 「经营风险」: 经营异常、行政处罚、严重违法\n"
    "     - 「股东信息」: 股权结构、实际控制人\n"
    "  4. 如您能提供具体企业名称, 可直接在对话中告知, 我将为您查询并分析"
)


# ======================================================================
# 企业名正则提取 (复用 retrieval_base_layer_node 同名函数, 但可同时搜 input)
# ======================================================================
_COMPANY_SUFFIX = (
    "有限公司", "股份有限公司", "有限责任公司", "集团有限公司",
    "科技有限公司", "实业有限公司", "贸易有限公司", "投资有限公司",
    "公司", "集团", "事务所", "研究院", "中心", "厂", "合作社",
    "银行", "保险公司", "证券", "基金", "资产管理",
)


def _extract_company_names(text: str) -> list:
    if not text:
        return []
    sample = text[:3000]
    names = []
    seen = set()

    suffix_alt = "|".join(re.escape(s) for s in _COMPANY_SUFFIX)

    patterns = [
        # 角色前缀: 甲方/乙方/出租方/承租方 ... XXX公司
        r'(?:甲方|发包方|委托方|采购方|买方|出租方|出让方|许可方|聘用方|'
        r'乙方|承包方|受托方|供货方|卖方|承租方|受让方|被许可方|受聘方)'
        r'[：:]\s*([^\n，,。；;]{2,30}(?:' + suffix_alt + r'))',
        # 独立出现: 含公司/集团/银行/基金等后缀
        r'([\u4e00-\u9fa5A-Za-z0-9（）()]{2,25}(?:' + suffix_alt + r'))',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, sample)
        for m in matches:
            name = m.strip() if isinstance(m, str) else str(m).strip()
            if (name and name not in ("甲方", "乙方", "公司", "未知", "失信", "被执行人")
                    and len(name) >= 4):
                if name not in seen:
                    seen.add(name)
                    names.append(name)
    return names[:4]


def _keyword_hit(text: str) -> bool:
    try:
        info = client.query_company_credit(company_name)
    except Exception as e:
        print(f"  ⚠️ [credit_precheck] 企查查查询[{company_name}]异常: {e}")
        return None, None
    if not info or not isinstance(info, dict):
        return None, None

    basic = info.get("basic_info", {}) or {}
    credit_score = info.get("credit_score", 0)
    risk_level = info.get("risk_level", "Unknown")
    mock = info.get("mock", True)
    mode = info.get("mode", "Mock")

    dishonest_count = len(info.get("dishonest", []) or [])
    executed_count = len(info.get("executed", []) or [])
    abnormal_count = len(info.get("abnormal", []) or [])
    penalties_count = len(info.get("penalties", []) or [])

    status = basic.get("status", "未知") if isinstance(basic, dict) else "未知"
    legal_person = basic.get("legal_person", "未知") if isinstance(basic, dict) else "未知"
    registered_capital = basic.get("registered_capital", "未知") if isinstance(basic, dict) else "未知"

    summary_parts = [
        f"企业名称: {company_name}",
        f"法定代表人: {legal_person}",
        f"注册资本: {registered_capital}",
        f"经营状态: {status}",
        f"信用评分: {credit_score}/100 ({risk_level})",
        f"失信被执行人: {dishonest_count} 条",
        f"被执行人: {executed_count} 条",
        f"经营异常: {abnormal_count} 条",
        f"行政处罚: {penalties_count} 条",
        f"数据来源: {'真实API' if not mock else '模拟数据'} ({mode})",
    ]
    content = "; ".join(summary_parts)

    citation = {
        "title": f"企查查·{company_name}资信报告",
        "article_no": f"信用评分:{credit_score} 风险等级:{risk_level}",
        "content": content,
        "source": "L1·企查查资信",
        "score": float(credit_score) if isinstance(credit_score, (int, float)) else 0,
    }
    return info, citation


# ======================================================================
# 主节点函数
# ======================================================================
def credit_precheck_node(state: AgentState):
    """
    credit_precheck_node 函数: 实现节点具体逻辑。
    """
    print("开始企查查预判定")

    task_type = state.get("task_type", "other") or "other"
    user_input = state.get("input", "") or ""
    doc_text = state.get("doc_text", "") or ""
    party_a = state.get("party_a", "") or ""
    party_b = state.get("party_b", "") or ""

    trigger_level = "none"
    company_names = []
    citations = []
    guidance = ""
    credit_info_cache = {}  # company_name -> info dict

    # ============== 三层触发判定 ==============
    if task_type in _STRONG_TRIGGER_TASKS:
        # 层级1 强触发: 必查, 优先用 state 中的 party_a/party_b, 回退 doc_text 正则
        trigger_level = "strong"
        if party_a:
            company_names.append(party_a)
        if party_b and party_b not in company_names:
            company_names.append(party_b)
        # 若 party_a/b 为空, 从 doc_text 兜底提取
        if not company_names:
            company_names.extend(_extract_company_names(doc_text))

    elif task_type in _MEDIUM_TRIGGER_TASKS:
        # 层级2 中触发: 能提取到企业名则查
        # 提取顺序: state.party_a/b → doc_text → user_input
        if party_a:
            company_names.append(party_a)
        if party_b and party_b not in company_names:
            company_names.append(party_b)
        doc_names = [n for n in _extract_company_names(doc_text) if n not in company_names]
        company_names.extend(doc_names)
        input_names = [n for n in _extract_company_names(user_input) if n not in company_names]
        company_names.extend(input_names)
        if company_names:
            trigger_level = "medium"
        # 若未提取到企业名, 继续判层级3关键词弱触发

    # 层级3 弱触发: 关键词命中 (含层级2未命中的 legal_research/legal_qa 也走这里)
    if trigger_level == "none" and task_type in _WEAK_TRIGGER_TASKS and _keyword_hit(user_input):
        trigger_level = "weak"
        # 先从 input + doc_text 尝试提取企业名
        names_from_input = _extract_company_names(user_input)
        names_from_doc = _extract_company_names(doc_text)
        seen = set()
        for n in names_from_input + names_from_doc:
            if n not in seen:
                seen.add(n)
                company_names.append(n)
        if not company_names:
            # 关键词命中但无企业名 → 写「查询方法说明」, 不实际调用API
            guidance = _CREDIT_GUIDANCE

    # ============== 去重 company_names ==============
    seen = set()
    unique_names = []
    for n in company_names:
        if n not in seen:
            seen.add(n)
            unique_names.append(n)
    company_names = unique_names

    # ============== 实际调用企查查 (若有 company_names) ==============
    if company_names and trigger_level in ("strong", "medium", "weak"):
        try:
            from common.qichacha_client import QiChaChaClient
        except Exception as e:
            print(f"  ⚠️ [credit_precheck] 企查查客户端导入失败: {e}")
            client = None
        try:
            client = QiChaChaClient() if client is not None else None
        except Exception as e:
            print(f"  ⚠️ [credit_precheck] 企查查客户端初始化失败: {e}")
            client = None

        if client is not None:
            for name in company_names:
                info, cite = _query_one(client, name)
                if info is not None:
                    credit_info_cache[name] = info
                if cite is not None:
                    citations.append(cite)

    # ============== 将查询结果匹配到 party_a / party_b 供 credit_check_node 复用 ==============
    # 若 party_a 在 company_names 中 (或近似匹配), 将对应 info 写入 state
    info_a_out = state.get("party_a_credit_info") or {}
    info_b_out = state.get("party_b_credit_info") or {}

    if party_a and not info_a_out:
        for n in company_names:
            if n == party_a or party_a in n or n in party_a:
                cached = credit_info_cache.get(n)
                if cached:
                    info_a_out = cached
                    break
    if party_b and not info_b_out:
        for n in company_names:
            if n == party_b or party_b in n or n in party_b:
                cached = credit_info_cache.get(n)
                if cached:
                    info_b_out = cached
                    break

    print(f"完成企查查预判定: 触发层级={trigger_level}, 企业数={len(company_names)}, "
          f"citations={len(citations)}, 甲方缓存={'有' if info_a_out else '无'}, "
          f"乙方缓存={'有' if info_b_out else '无'}")

    return {
        "credit_precheck_done": True,
        "credit_precheck_trigger_level": trigger_level,
        "credit_precheck_company_names": company_names,
        "credit_precheck_citations": citations,
        "credit_precheck_guidance": guidance,
        "party_a_credit_info": info_a_out,
        "party_b_credit_info": info_b_out,
    }



# 自测入口
if __name__ == "__main__":
    import json
    # 测试: 强触发 + 有 party_a/party_b
    s = AgentState(
        task_type="contract_review",
        input="审核合同",
        doc_text="甲方：北京科技有限公司   乙方：上海贸易有限公司",
        party_a="北京科技有限公司",
        party_b="上海贸易有限公司",
    )
    out = credit_precheck_node(s)
    print(json.dumps({k: (type(v).__name__ if isinstance(v, dict) else (len(v) if isinstance(v, list) else v))
                      for k, v in out.items()}, ensure_ascii=False, indent=2))

    # 测试: 弱触发 + 无企业名
    s2 = AgentState(task_type="other", input="怎么查公司的失信信息？")
    out2 = credit_precheck_node(s2)
    print("\n---弱触发无企业名---")
    print("trigger_level:", out2["credit_precheck_trigger_level"])
    print("guidance:", bool(out2["credit_precheck_guidance"]))
