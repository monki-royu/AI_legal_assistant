"""【文件作用】意图路由节点 ── 识别用户输入的任务类型，作为 LangGraph 工作流的"分流器"
【逻辑】本文件是 AI 法律助理(LangGraph 多智能体系统)的入口业务节点，紧随小红书前置过滤之后执行。
    核心流程：
    1. 从【state】中读取用户原始输入文本（state["input"]）
    2. 优先检查前端是否已传入【task_type】，若有则直接使用，跳过 LLM 调用（避免误判覆盖）
    3. 若无前端传入，调用【LLM】对用户输入进行意图分类，输出四大链路之一的具体任务类型
    4. 采用"【LLM 主判】+【模糊匹配兜底】+【异常降级】"三层容错策略：
       (a) LLM 直接输出任务类型英文标识
       (b) 若 LLM 输出不在白名单内，用【子串匹配】尝试纠正
       (c) 若仍无法识别或 LLM 异常，降级为 "legal_qa" 避免流程中断
    5. 将识别结果写入 state["task_type"]，供【level2_router】二级条件路由判断流程走向
    6. 函数以【AgentState】(dict)为输入输出，符合 LangGraph 状态合并式编程范式

【新架构说明 · 两级路由重构】
    - Level 1 (binary): xiaohongshu_publish_intent_node 已在前置完成 (小红书/非小红书)
    - Level 2 (本节点): 在非小红书前提下，将任务归类为四大路径：
        ① 合同合规 (contract_review / compliance_review)
        ② 检索 (legal_research / case_search)
        ③ 法律问答 (legal_qa)
        ④ 文书生成 (legal_document_gen)
      同时保留具体 task_type 值供下游节点精细化判断
    - Level 3 (内部): QA 子图内部由 qa_intent_classify 判断法律相关/非法律相关
"""

from langchain_core.messages import HumanMessage
from common.llm import my_llm
from __004__langgraph_more_nodes.agent_state import AgentState


# 二级路由的 4 大路径分组映射表
# key: 具体任务类型; value: 所属路径分组(4选1)
LEVEL2_PATH_MAP = {
    # ① 合同合规路径
    "contract_review": "contract_compliance",
    "compliance_review": "contract_compliance",
    # ② 检索路径
    "legal_research": "retrieval",
    "case_search": "retrieval",
    # ③ 法律问答路径
    "legal_qa": "legal_qa",
    # ④ 文书生成路径
    "legal_document_gen": "legal_document_gen",
}


def intent_router_node(state: AgentState):
    """
    【功能】意图路由节点函数：根据用户输入文本识别任务类型，并写入 state["task_type"]
    【参数】state (AgentState)：LangGraph 共享状态字典，必须包含 "input" 字段
    【返回值】AgentState：更新后的状态字典，新增/覆盖 "task_type" 字段
                "task_type" 取值枚举（按 4 大路径分组）：
                ┌─ 合同合规路径 ──────────────┐
                - "contract_review"      → 【合同审核】用户要求审核合同
                - "compliance_review"    → 【合规审查】用户要求做合规检查
                ├─ 检索路径 ────────────────┤
                - "legal_research"       → 【法律检索】用户要求查找法规/案例
                - "case_search"          → 【案例检索】用户要求检索司法案例
                ├─ 法律问答路径 ────────────┤
                - "legal_qa"             → 【法律问答】用户提出法律问题要求解答
                ├─ 文书生成路径 ────────────┤
                - "legal_document_gen"   → 【文书生成】用户要求生成法律文书
                └─ 兜底 ────────────────────┘
                - "other"                → 【其他】明确非法律相关
    【逻辑】三层容错策略：
            1.【LLM 主判】调用 LLM 按 prompt 输出任务类型标识
            2.【模糊匹配兜底】若输出不在白名单内，遍历白名单用子串匹配纠正
            3.【异常降级】若 LLM 调用异常或完全无法识别，降级为 "legal_qa"
    """
    print("--- 开始意图路由 (Level 2: 4路径分类) ---")

    # 【步骤1】检查前端是否已传入 task_type
    existing_task_type = state.get("task_type", "")
    valid_task_types = set(LEVEL2_PATH_MAP.keys()) | {"other"}
    if existing_task_type and existing_task_type in valid_task_types:
        print(f"--- 完成意图路由: {existing_task_type} (使用前端传入) ---")
        return {"task_type": existing_task_type}

    # 【步骤2】提取用户输入
    user_input = state.get("input", "")

    # 【步骤3】构造 LLM Prompt —— 按 4 大路径分类，保留具体任务类型
    prompt = f"""你是一个法律AI助手的意图分类器。请判断用户输入属于以下哪类任务:

【合同合规路径】
- contract_review: 合同审核(用户上传或粘贴了合同, 要求审核风险)
- compliance_review: 合规审查(用户要求做法律合规检查, 判断是否违反法律法规)

【检索路径】
- legal_research: 法律检索(用户要求系统查找法规/案例/司法解释)
- case_search: 案例检索(用户要求检索司法案例/判例)

【法律问答路径】
- legal_qa: 法律问答(用户提出法律问题要求解答; 若不属于以上任何一类法律任务, 也归为此类作为问答兜底)

【文书生成路径】
- legal_document_gen: 文书生成(用户要求生成起诉状/律师函/合同草稿等法律文书)

【兜底】
- other: 其他(明确非法律相关的闲聊/问候/无关请求)

用户输入: {user_input}

只输出任务类型英文标识, 不要解释。若无法明确判断, 输出 legal_qa。"""

    # 【步骤4】调用 LLM 进行意图识别
    try:
        resp = my_llm.invoke([HumanMessage(content=prompt)])
        task_type = resp.content.strip().lower()

        valid = set(LEVEL2_PATH_MAP.keys()) | {"other"}

        if task_type not in valid:
            # 模糊匹配纠错
            for v in valid:
                if v in task_type:
                    task_type = v
                    break
            else:
                task_type = "legal_qa"

    except Exception as e:
        print(f"⚠️ 意图识别失败, 默认 legal_qa: {e}")
        task_type = "legal_qa"

    # 【步骤5】写入状态并打印所属路径分组
    state["task_type"] = task_type
    path_group = LEVEL2_PATH_MAP.get(task_type, "legal_qa")
    print(f"--- 完成意图路由: {task_type} (路径分组: {path_group}) ---")

    return state


# 脚本直接运行时的自测入口
if __name__ == "__main__":
    s = AgentState(input="帮我审核这份买卖合同")
    result = intent_router_node(s)
    print(f"任务类型: {result.get('task_type')}")