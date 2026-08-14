"""检索子节点1: 意图分解 - 分析输入拆解为检索词与关键词"""
import os
from common.llm import my_llm
from common.path_utils import root_dir
from __004__langgraph_more_nodes.agent_state import AgentState
from langchain_core.messages import HumanMessage

def retrieval_intent_decompose_node(state: AgentState):
    """意图分解：根据合同类型/合同正文/用户输入生成检索查询与关键词"""
    print("检索 [1/5] 意图分解")
    doc_text = state.get("doc_text", "")[:2000]
    contract_type = state.get("contract_type", "")
    user_query = state.get("input", "")[:500]

    # 构造基础检索查询
    if contract_type:
        base_query = f"{contract_type}合同 {doc_text[:200]}"
    elif doc_text:
        base_query = doc_text[:300]
    else:
        base_query = user_query[:300]

    # LLM 辅助扩展关键词（当关键词不足时）
    retrieval_keywords = []
    try:
        prompt = f"""请从以下文本中提取3-8个法律检索关键词（法规名、法律概念、关键词），以JSON数组返回：["词1","词2"]
文本: {base_query[:800]}
只输出JSON数组。"""
        resp = my_llm.invoke([HumanMessage(content=prompt)])
        content = resp.content.strip()
        if "```" in content:
            s = content.find("["); e = content.rfind("]") + 1
            if s >= 0 and e > s: content = content[s:e]
        import json
        kws = json.loads(content)
        if isinstance(kws, list):
            retrieval_keywords = [str(k) for k in kws if str(k).strip()]
    except Exception:
        pass

    if not retrieval_keywords:
        # 降级：基于标点符号分词
        q = base_query.replace("，", " ").replace("。", " ")
        retrieval_keywords = [w for w in q.split() if len(w) > 1][:8]

    return {
        "retrieval_query": base_query,
        "retrieval_keywords": retrieval_keywords,
    }
