# -*- coding: utf-8 -*-
"""
法律文书生成 - 案情分析节点 (N_doc1)
====================================

【功能】
读取用户输入的纠纷事实描述（dispute_type/description/plaintiff/defendant/claims），
调用 LLM 将其结构化抽取为 {case_type, parties, facts, claims, evidence, court}，
输出到 state["case_summary"]。若信息不足（缺少当事人/诉求等），设置 need_clarify=True。

【流程位置】（文书生成链路共 6 步，本节点是第 1 步）
  [1/6] 案情分析（本节点）→ [2/6] 模板匹配 → [3/6] 条款填充(RAG+LLM)
  → [4/6] 法条校验 → [5/6] 风险提示 / [5b/6] 类案推荐(并行) → [6/6] 最终交付

【下游衔接】
下游 doc_template_match_node（模板匹配节点）读取 case_summary 做模板匹配。

【面试视角】
Q: 为什么案情分析不直接用规则匹配?
A: 法律纠纷事实的描述方式千差万别（口语化/结构化/残缺），规则匹配难以覆盖。
   LLM 能理解自然语言，按固定 JSON schema 输出，兼顾灵活性与结构化。
"""
# 导入标准库 json：用于把 LLM 返回的文本解析为 Python 字典（JSON 反序列化）
import json
# 导入 LangChain 消息类型：SystemMessage 设定角色，HumanMessage 承载用户输入
from langchain_core.messages import SystemMessage, HumanMessage
# 导入项目统一的 LLM 实例（my_llm）：封装模型选择与调用细节
from common.llm import my_llm
# 导入 AgentState 类型：LangGraph 图中各节点共享的状态字典（TypedDict）
from __004__langgraph_more_nodes.agent_state import AgentState
# 导入微调数据收集工具：记录本节点的输入/输出为微调样本（可选旁路，失败静默）
from common.finetune_utils import collect_ft_sample


def doc_case_analyze_node(state: AgentState):
    """
    【功能】案情分析节点：用 LLM 从用户输入中抽取结构化案情（Case Analysis）。

    读取用户在 state 中填写的纠纷信息（纠纷类型、事实描述、当事人、诉求等），
    构造一个要求"输出严格 JSON"的 Prompt 发给 LLM，让 LLM 按固定 schema
    抽取为结构化字段（case_type / parties / facts / claims / evidence / court），
    并判断是否需要向用户追问补充信息（need_clarify / clarify_question）。
    若 state 中已有完整 case_summary（例如前端直接传入），则直接复用、跳过 LLM。

    【参数】
        state : AgentState（LangGraph 共享状态字典），本节点读取以下键：
            - dispute_type (str): 纠纷类型（如"劳动争议"/"合同纠纷"）
            - user_input / input (str): 用户的事实描述文本（user_input 优先）
            - plaintiff (str): 原告/申请人名称
            - defendant (str): 被告/被申请人名称
            - incident_date (str): 事发时间
            - claims (str): 诉讼请求
            - case_summary (dict, 可选): 若已存在完整案情摘要则直接复用

    【返回值】
        dict（将被 LangGraph 合并进 state），包含：
            - case_summary: dict，结构化案情
              {case_type, parties{plaintiff, defendant}, facts[], claims[],
               evidence[], court}
            - need_clarify: bool，是否需要向用户追问补充信息
            - clarify_question: str，需要追问的问题（不需要时为空字符串）
        注意：LLM 失败时走兜底结构（用原始输入直接填字段，不抛异常）。

    【逻辑】
        1. 从 state 读取 5 类原始字段（全部带默认值兜底）；
        2. 若已有完整 case_summary 则直接返回空 dict {}（复用，跳过 LLM 省成本）；
        3. 构造 JSON-schema Prompt（把字段说明写死在提示词里，约束 LLM 输出格式）；
        4. 调用 my_llm.invoke，清理可能出现的 ```json 代码块包裹后 json.loads 解析；
        5. 弹出 need_clarify / clarify_question 两个控制字段，其余作为 case_summary 返回；
        6. 异常时打印告警并返回基于原始输入的兜底结构（保证流程不断裂）。
    """

    # 打印日志：标记进入文书生成第 1 步"案情分析"（[1/6] 进度提示）
    print("文书生成 [1/6] 案情分析")
    # 读取纠纷类型（如"劳动争议"），缺失时为空字符串
    dispute_type = state.get("dispute_type", "")
    # 读取事实描述：优先取 user_input，其次取 input（双键兼容不同上游写法），缺失为空串
    description = state.get("user_input", "") or state.get("input", "")
    # 读取原告名称，缺失时为空字符串
    plaintiff = state.get("plaintiff", "")
    # 读取被告名称，缺失时为空字符串
    defendant = state.get("defendant", "")
    # 读取事发时间，缺失时为空字符串
    incident_date = state.get("incident_date", "")
    # 读取诉讼请求，缺失时为空字符串
    claims = state.get("claims", "")

    # 若已有完整 case_summary（如从前端传入），跳过 LLM（避免重复调用、节省成本）
    existing = state.get("case_summary")
    # 判断"完整"的标准：非空、是 dict、且包含 case_type 字段（说明已做过结构化）
    if existing and isinstance(existing, dict) and existing.get("case_type"):
        # 打印日志：提示复用已有案情摘要（不调用 LLM）
        print("  复用已有 case_summary")
        # 返回空 dict：LangGraph 会把空 dict 合并进 state，不改变任何字段
        return {}

    # 构造 Prompt（提示词）：把用户输入嵌入模板，要求 LLM 输出结构化的案情 JSON。
    # 这里用 f-string 三引号字符串，把所有可用信息都塞进提示词，信息越全抽取越准。
    prompt = f"""你是一位法律案情分析专家。请根据以下信息, 输出结构化的案情 JSON。

【纠纷类型】{dispute_type}
【原告】{plaintiff}
【被告】{defendant}
【事发时间】{incident_date}
【事实描述】{description[:2000]}
【诉讼请求】{claims}

请按以下 JSON schema 输出(不要多余文字):
{{
    "case_type": "案件类型(如'劳动争议'/'合同纠纷'/'民间借贷')",
    "parties": {{"plaintiff": "原告/申请人全称", "defendant": "被告/被申请人全称"}},
    "facts": ["事实1", "事实2", ...],
    "claims": ["诉求1", "诉求2", ...],
    "evidence": ["证据1", "证据2", ...],
    "court": "管辖法院全称(根据案件性质推定)",
    "need_clarify": false,
    "clarify_question": "若信息不足, 填需要追问的问题; 否则填空字符串"
}}"""
    try:
        # 调用 LLM：消息列表 = SystemMessage(角色设定: 输出严格 JSON) + HumanMessage(上述 Prompt)
        resp = my_llm.invoke([SystemMessage(content="你经验丰富的法律助手, 输出严格 JSON。"),
                              HumanMessage(content=prompt)])
        # 取出 LLM 返回的文本并去除首尾空白
        text = resp.content.strip()
        # 处理 ```json ... ``` 包裹（LLM 常用 Markdown 代码块包裹 JSON，需要剥壳）
        if "```json" in text:
            # 若文本以 ```json 开头：取第一个 ```json 之后、下一个 ``` 之前的内容
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            # 若只有普通 ``` 包裹：直接用"找第一个 { 到最后一个 }"的方式截取 JSON 片段
            s = text.find("{"); e = text.rfind("}") + 1
            # 校验截取区间合法（找到了 { 且 } 在 { 之后）
            if s >= 0 and e > s:
                text = text[s:e]
        # 用 json.loads 把清理后的文本解析为 Python 字典（结构化案情）
        case_summary = json.loads(text)
        # 弹出 need_clarify 控制字段（pop 会从字典中移除该键并返回其值），默认 False
        need_clarify = case_summary.pop("need_clarify", False)
        # 弹出 clarify_question 追问问题字段，默认空字符串
        clarify_q = case_summary.pop("clarify_question", "")
        # 打印日志：展示 LLM 抽取出的案由（便于人工核对抽取质量）
        print(f"  案由: {case_summary.get('case_type', '')}")
        # 暂存输出（成功路径）：稍后统一做微调采集并返回，避免提前 return 跳过采集
        _ft_output = {
            "case_summary": case_summary,
            "need_clarify": need_clarify,
            "clarify_question": clarify_q,
        }
    except Exception as e:
        # LLM 调用或 JSON 解析失败：打印告警（含异常信息），提示将使用兜底结构
        print(f"  ⚠️ 案情分析 LLM 失败: {e}, 使用兜底结构")
    # 兜底结构（仅当上方 LLM try 抛异常、_ft_output 尚未赋值时使用）
    if "_ft_output" not in locals():
        _ft_output = {
            "case_summary": {
                # 案由：优先用用户填的纠纷类型，缺失则兜底为"其他纠纷"
                "case_type": dispute_type or "其他纠纷",
                # 当事人：优先用用户填的名称，缺失则兜底为"原告"/"被告"
                "parties": {"plaintiff": plaintiff or "原告", "defendant": defendant or "被告"},
                # 事实列表：若有描述则作为单元素列表（截取前 500 字符），否则空列表
                "facts": [description[:500]] if description else [],
                # 诉求列表：若有诉求则作为单元素列表，否则空列表
                "claims": [claims] if claims else [],
                # 证据列表：兜底结构无证据信息，置空列表
                "evidence": [],
                # 管辖法院：兜底结构无法推定，置空字符串
                "court": "",
            },
            # 是否需要追问：原告或被告缺失时为 True（信息不全，需要用户补充）
            "need_clarify": bool(not plaintiff or not defendant),
            # 追问问题：原告或被告缺失时提示"请补充原告、被告信息"，否则空字符串
            "clarify_question": "请补充原告、被告信息" if not plaintiff or not defendant else "",
        }

    # ==== 微调数据收集 ====
    # 微调样本收集块（可选旁路）：记录本节点的输入/输出用于后续模型微调。
    # 独立 try/except，失败仅告警，绝不吞掉主流程的 return。
    try:
        _ft_input = str(state.get("input", "") or "")[:2000]
        collect_ft_sample("doc_case_analyze", _ft_input, _ft_output,
                          task_type=state.get("task_type", ""))
    except Exception as fe:
        print(f"  ⚠️ 微调样本收集失败(忽略): {fe}")

    # ==== 信息不足时的追问出口 ====
    # need_clarify 为 True 时, docgen_subgraph._clarify_router 会把流程路由到 END,
    # 后续 template_match / clause_fill 等节点不再执行。此时 output 是调用方
    # (前端 / FastAPI) 唯一能拿到的内容, 必须把追问文案写进去, 否则用户只会看到
    # 一个空响应 —— 不知道系统为什么没生成文书、也不知道该补什么。
    # 【接线背景】need_clarify / clarify_question 两个字段此前一直被写入但零消费方
    #   (子图是 7 条 add_edge 纯线性, 没有条件边), 本轮补上路由与出口文案。
    if _ft_output.get("need_clarify"):
        _ft_output["output"] = _ft_output.get("clarify_question") or (
            "生成文书前需要补充信息：请提供完整的当事人（原告/被告）名称与诉讼请求。"
        )
        print(f"  ⚠️ 信息不足, 终止生成并追问用户: {_ft_output['output']}")

    # 统一返回（成功/兜底路径都走到这里，保证微调数据被采集）
    return _ft_output
