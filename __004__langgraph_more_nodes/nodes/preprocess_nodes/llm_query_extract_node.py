"""查询文本抽取节点 (llm_query_extract) — preprocess 子图末节点

【架构定位】
    本节点是文档预处理子图 (preprocess_subgraph) 的【末节点】(N5),
    位于 numeric_extract 之后, 输出传给检索子图 (retrieval_subgraph).

    仅服务于 contract_review / compliance_review 两种任务类型.
    其他任务类型 (legal_qa / case_search / legal_document_gen 等)
    有独立的主图路径, 不经过 preprocess 子图.

【核心逻辑】
    1. 从 doc_segments 中提取 clause 类型切分单元 (MAX_CLAUSES 条);
    2. 按 task_type 为每条条款构造检索查询:
       - contract_review: 双视角 (合同审核 + 合规审查) 各生成一条查询;
       - compliance_review: 仅合规审查视角生成查询;
    3. 产出两种格式供下游消费:
       - retrieval_queries (Dict): 按视角分组的查询集, 供 dual_review 子图的
         compliance_review_node / contract_ai_review_node 作为"检索聚焦"提示;
       - retrieval_query (str): 扁平化查询 (用 | 连接), 供检索子图
         (retrieval_intent_decompose_node → retrieval_base_layer_node) 使用.

    关键词提取不再由本节点负责 —— 统一交给检索子图的
    retrieval_intent_decompose_node 做 LLM 关键词提取 + 按重要性排序。

【数据流】
    full_text_segment → doc_segments (含 type=clause 的切分单元)
                              ↓
                    llm_query_extract (逐条款构造查询)
                              ↓
                    ┌─────────────────────────────┐
                    │ retrieval_queries (Dict)     │ → dual_review 子图的审查节点
                    │ retrieval_query (str)       │ → retrieval 子图的检索基座
                    └─────────────────────────────┘
"""

import json

from langchain_core.messages import HumanMessage

from common.llm import my_llm
from __004__langgraph_more_nodes.agent_state import AgentState


MAX_CLAUSES = 8  # 单合同最多分析条款数 (控制 LLM 调用次数)

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _extract_clauses(doc_segments: list) -> list:
    """从 doc_segments 中提取 clause 类型的切分单元文本"""
    return [
        seg.get("text", "")
        for seg in (doc_segments or [])
        if seg.get("type") == "clause" and seg.get("text", "").strip()
    ][:MAX_CLAUSES]


def _dedup_queries(queries) -> list:
    """去重查询列表 (忽略大小写/首尾空白, 保序)"""
    seen = set()
    out = []
    for q in queries or []:
        if not q:
            continue
        key = str(q).strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(str(q).strip())
    return out


# ---------------------------------------------------------------------------
# LLM 调用封装
# ---------------------------------------------------------------------------

def _llm_json(prompt: str) -> dict:
    """调用 LLM 并解析为 JSON dict, 失败返回空 dict"""
    try:
        resp = my_llm.invoke([HumanMessage(content=prompt)])
        content = (resp.content or "").strip()
        # 去除 markdown 代码块包裹
        if "```" in content:
            s = content.find("{")
            e = content.rfind("}") + 1
            if s >= 0 and e > s:
                content = content[s:e]
        return json.loads(content)
    except Exception as e:
        print(f"  ⚠️ llm_query_extract LLM JSON 调用失败: {e}")
        return {}


def _llm_text(prompt: str) -> str:
    """调用 LLM 生成文本, 失败返回空串"""
    try:
        resp = my_llm.invoke([HumanMessage(content=prompt)])
        return (resp.content or "").strip()
    except Exception as e:
        print(f"  ⚠️ llm_query_extract LLM 调用失败: {e}")
        return ""


# ---------------------------------------------------------------------------
# 查询构造 (按 task_type 分流)
# ---------------------------------------------------------------------------

def _gen_dual_perspective_queries(clause_text: str, contract_type: str) -> tuple:
    """为单条条款构造双视角查询 (合同审核 + 合规审查), 一次 LLM 调用

    返回: (contract_query: str, compliance_query: str)
    """
    prompt = f"""你是法律检索查询构造专家。请基于以下【单条合同条款】，分别构造两条法律检索查询：
(1) 合同审核视角：聚焦商业风险、履约能力、价款与支付、违约责任、权利义务对等；
(2) 合规审查视角：聚焦是否违反法律法规强制性规定、监管红线、资质许可、税务/劳动合规。
只输出 JSON, 严格格式：{{"contract_query":"...","compliance_query":"..."}}
【合同类型】{contract_type}
【待分析条款】{clause_text[:300]}
只输出 JSON, 不要解释。"""

    data = _llm_json(prompt)
    cq = (data.get("contract_query") if isinstance(data, dict) else "") or ""
    cpq = (data.get("compliance_query") if isinstance(data, dict) else "") or ""

    # 兜底: LLM 调用失败时用模板生成
    if not cq:
        cq = f"{contract_type}合同 商业条款风险审查：{clause_text[:80]}"
    if not cpq:
        cpq = f"{contract_type}合同 合规性审查(强制性规定/资质许可)：{clause_text[:80]}"

    return cq.strip(), cpq.strip()


def _gen_compliance_query(clause_text: str, contract_type: str) -> str:
    """为单条条款构造合规审查单视角查询"""
    prompt = f"""你是法律检索查询构造专家。请基于以下【单条合同条款】，构造 1 条合规审查视角的法律检索查询
(聚焦: 是否违反法律法规强制性规定、监管红线、资质许可、税务/劳动合规)。
只输出查询文本本身, 不要解释。
【合同类型】{contract_type}
【待分析条款】{clause_text[:300]}"""

    q = _llm_text(prompt)
    if not q:
        q = f"{contract_type}合同 合规性审查(强制性规定/资质许可)：{clause_text[:80]}"
    return q.strip()


# ---------------------------------------------------------------------------
# 主节点函数
# ---------------------------------------------------------------------------

def llm_query_extract_node(state: AgentState):
    """查询文本抽取节点 — 按 task_type 从 doc_segments 构造检索查询

    读取字段:
        - task_type (str):            任务类型 (contract_review / compliance_review)
        - doc_segments (List[Dict]):  全文本切分结果
        - contract_type (str):        合同类型 (辅助构造查询)
        - input (str):                用户原始输入 (兜底)

    写入字段:
        - retrieval_queries (Dict):   按视角分组的查询集 (供审查节点做检索聚焦)
        - retrieval_query (str):       扁平化查询 (供检索子图使用)
    """
    task_type = state.get("task_type", "")
    doc_segments = state.get("doc_segments", []) or []
    contract_type = state.get("contract_type", "") or ""
    user_input = state.get("input", "") or ""

    clauses = _extract_clauses(doc_segments)
    print(f"查询文本抽取 (task_type={task_type}, 条款数={len(clauses)})")

    # ---- 按 task_type 分流 ----
    if task_type == "contract_review":
        # 双视角模式: 为每条条款生成 (合同审核, 合规审查) 两条查询
        contract_q, compliance_q = [], []
        for c in clauses:
            # _gen_dual_perspective_queries 返回 (合同审核视角, 合规审查视角) 两条查询,
            # 分别进入 contract_q / compliance_q, 两个视角各自独立去重。
            # 【历史 bug】原代码两处都 append(cq), 解包出的 cpq 从未使用 →
            # 合规视角查询与合同视角完全相同, 跨视角去重后双视角塌陷为单视角,
            # 白花一倍 LLM 调用却只得到一套查询。
            cq, cpq = _gen_dual_perspective_queries(c, contract_type)
            contract_q.append(cq)
            compliance_q.append(cpq)

        contract_q = _dedup_queries(contract_q)
        compliance_q = _dedup_queries(compliance_q)
        # 跨视角去重: 两视角完全相同的查询只保留一次
        flat = _dedup_queries(contract_q + compliance_q)

        retrieval_queries = {
            "contract_review": contract_q,
            "compliance_review": compliance_q,
        }

    elif task_type == "compliance_review":
        # 单视角模式: 仅合规审查
        compliance_q = [_gen_compliance_query(c, contract_type) for c in clauses]
        compliance_q = _dedup_queries(compliance_q)
        flat = compliance_q

        retrieval_queries = {"compliance_review": compliance_q}

    else:
        # 兜底 (理论上不应触发: preprocess 子图只服务于 contract/compliance)
        base = user_input.strip() or (f"{contract_type}合同 审查" if contract_type else "合同审查")
        flat = [base]
        retrieval_queries = {"default": flat}

    # ---- 兜底: 无任何 clause 时, 用合同类型构造单一查询 ----
    if not flat:
        base = f"{contract_type}合同 风险与合规审查" if contract_type else (user_input or "合同审查")
        flat = [base]
        retrieval_queries = (
            {"contract_review": [base], "compliance_review": [base]}
            if task_type == "contract_review"
            else {"compliance_review": [base]}
        )

    # ---- 扁平化查询 ----
    retrieval_query = " | ".join(flat)[:800]
    if not retrieval_query:
        retrieval_query = (user_input or "")[:500]

    print(f"  视角={list(retrieval_queries.keys())}, "
          f"扁平查询={retrieval_query[:60]}")

    return {
        "retrieval_queries": retrieval_queries,
        "retrieval_query": retrieval_query,
        "research_query": retrieval_query,  # 兼容字段: beida_fabao_gate_node 读取
    }
