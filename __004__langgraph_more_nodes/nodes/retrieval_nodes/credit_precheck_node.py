"""企查查资信前置预检节点
    解决的问题: 旧架构下企查查判定逻辑分散在 credit_check 内部, 每次重试
    都要重新判断. 新架构前置一次性决定, 避免重复判定.

    本节点为纯逻辑判定, 不调用任何外部 API, 零耗时.

    判断逻辑:
    1. 仅 contract_review / compliance_review 任务需要资信查询
    2. 必须有至少一个合同主体 (party_a 或 party_b)
    3. 主体不能是占位名 (如 "甲方"、"乙方")

    写入字段:
        - credit_check_needed (bool): 是否需要企查查查询
        - credit_parties (list): 待查询的主体名称列表
"""

from __004__langgraph_more_nodes.agent_state import AgentState
# 主体提取 / 归一化共享工具统一收口到 common.retrieval_shared, 与 credit_check 共用同一份
from common.retrieval_shared import _GENERIC_NAMES, _norm_name, _extract_party_names

# 触发资信查询的任务类型
_CREDIT_ENABLED_TASKS = {"contract_review", "compliance_review"}

def credit_precheck_node(state: AgentState):
    """企查查资信前置预检节点.

    【职责】
    轻量级判定是否需要企查查资信查询, 在检索循环之前一次性完成.
    不调用 API, 仅基于 state 中的 task_type / party_a / party_b 做逻辑判断.

    读取字段:
        - task_type (str): 任务类型
        - party_a (str): 甲方名称
        - party_b (str): 乙方名称
        - doc_text / input (str): 文档全文/用户输入 (兜底提取)

    写入字段:
        - credit_check_needed (bool): 是否需要企查查查询
        - credit_parties (list): 待查询主体列表 (已过滤占位名)
    """
    print("检索 [预检] 企查查资信前置判定")

    task_type = state.get("task_type", "")

    # 1) 任务类型过滤: 仅合同/合规任务需要资信
    if task_type not in _CREDIT_ENABLED_TASKS:
        print(f"  任务类型 [{task_type}] 非合同/合规, 无需资信查询")
    return {
        "credit_check_needed": False,
        "credit_parties": [],
    }

    # 2) 主体提取: 从 state 中读取 + 兜底从文档提取
    party_a = _norm_name(state.get("party_a", ""))
    party_b = _norm_name(state.get("party_b", ""))

    # 主体缺失时的兜底提取 (与 credit_check_node 一致, 统一用 common.retrieval_shared._extract_party_names)
    if not party_a and not party_b:
        doc_text = state.get("doc_text", "") or state.get("input", "") or ""
        party_names = _extract_party_names(doc_text)
        if party_names:
            party_a = _norm_name(party_names[0]) if len(party_names) > 0 else ""
            party_b = _norm_name(party_names[1]) if len(party_names) > 1 else ""
            print(f"  主体缺失, 从文档文本兜底提取: {[party_a, party_b]}")

    # 3) 过滤有效主体
    parties = [p for p in (party_a, party_b) if p]

    if not parties:
        print("  无有效合同主体, 无需资信查询")
    return {
        "credit_check_needed": False,
        "credit_parties": [],
    }

    # 4) 需要资信查询
    print(f"  ✅ 需要企查查资信查询, 主体: {parties}")
    return {
        "credit_check_needed": True,
        "credit_parties": parties,
    }

# 脚本直接运行时的自测入口
if __name__ == "__main__":
    # 测试1: 合同审核 + 有主体
    s1 = AgentState(task_type="contract_review", party_a="华为技术有限公司", party_b="某商贸公司")
    r1 = credit_precheck_node(s1)
    print(f"测试1 (合同+主体): needed={r1.get('credit_check_needed')}, parties={r1.get('credit_parties')}")

    # 测试2: 法律问答 (无需资信)
    s2 = AgentState(task_type="legal_qa")
    r2 = credit_precheck_node(s2)
    print(f"测试2 (QA): needed={r2.get('credit_check_needed')}")

    # 测试3: 合规审查 + 无主体
    s3 = AgentState(task_type="compliance_review")
    r3 = credit_precheck_node(s3)
    print(f"测试3 (合规+无主体): needed={r3.get('credit_check_needed')}")