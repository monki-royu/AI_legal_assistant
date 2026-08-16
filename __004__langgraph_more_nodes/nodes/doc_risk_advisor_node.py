# -*- coding: utf-8 -*-
"""
法律文书生成 - 风险提示节点 (N_doc5)
====================================

【功能】
基于生成的法律文书草稿（draft_content）和案情（case_summary），
LLM 分析潜在风险（诉讼时效、证据充分性、管辖法院、对方抗辩可能性、
执行难度等），输出风险提示列表（risks）。

【流程位置】（文书生成链路 6 步中的第 5 步）
  [4/6] 法条校验 → [5/6] 风险提示（本节点）→ [6/6] 最终交付
  （注：[5b/6] 类案推荐与 [5/6] 风险提示是并行关系，见 doc_case_recommend_node）

【设计】
与合同审核的 risk_aggregate（风险聚合）不同，文书生成的风险提示面向
"文书使用方"（Document User），按文书类型针对性输出：
  - 如果是起诉状: 风险提示包括败诉风险、举证困难、诉讼时效等;
  - 如果是答辩状: 风险提示包括逾期应诉、管辖权异议等;
  - 如果是律师函: 风险提示包括函件效果、对方回应可能性等。
LLM 按文书类型针对性输出风险，提高实用价值。

【下游】
doc_final_delivery（最终交付节点）读取 risks 与 draft_content 组装最终交付物。
"""
# 导入标准库 json：解析 LLM 返回的 JSON 数组
import json
# 导入 LangChain 消息类型：SystemMessage 设定角色，HumanMessage 承载用户输入
from langchain_core.messages import SystemMessage, HumanMessage
# 导入项目统一的 LLM 实例：封装模型选择与调用细节
from common.llm import my_llm
# 导入 AgentState 类型：LangGraph 图中各节点共享的状态字典（TypedDict）
from __004__langgraph_more_nodes.agent_state import AgentState
# 导入微调数据收集工具：记录本节点的输入/输出为微调样本（可选旁路，失败静默）
from common.finetune_utils import collect_ft_sample


def doc_risk_advisor_node(state: AgentState):
    """风险提示节点: LLM 分析文书潜在风险。"""
    # 【功能】基于文书草稿与案情，调用 LLM 分析 2-5 条潜在风险，输出风险列表。
    # 【参数】
    #     state (AgentState): LangGraph 共享状态字典，本节点读取：
    #         - case_summary (dict): 案情分析结果（含 parties 当事人信息）
    #         - draft_content (str): 条款填充节点生成的文书草稿（取前 2000 字符）
    #         - template_name (str): 文书类型名称（如"民事起诉状"/"答辩状"/"律师函"）
    #         - dispute_type (str): 纠纷类型
    # 【返回值】
    #     dict（合并进 state），包含：
    #         - risks: list[dict]，每条风险为
    #           {level: "high"/"medium"/"low", title: 风险标题,
    #            description: 风险详细描述(含依据), suggestion: 应对建议}
    #         注：LLM 失败时返回一条兜底风险（建议律师审阅），保证流程不断裂。
    # 【逻辑】
    #     1. 读取案情、草稿（截取前 2000 字符控成本）、文书类型、纠纷类型；
    #     2. 构造 Prompt：把文书类型/纠纷类型/当事人/草稿摘要嵌入，
    #        要求 LLM 输出 2-5 条风险的 JSON 数组（每条含 level/title/description/suggestion）；
    #     3. 调用 LLM，清理 ```json 代码块后 json.loads 解析为列表；
    #     4. 校验解析结果确实是 list（防止 LLM 返回字典/字符串等异常结构）；
    #     5. 异常时打印告警并返回兜底风险（单条 medium 风险：建议律师审阅）。
    # 打印日志：标记进入文书生成第 5 步"风险提示"
    print("文书生成 [5/6] 风险提示")
    # 读取案情分析结果，缺失时兜底为空字典
    case_summary = state.get("case_summary", {}) or {}
    # 读取文书草稿并截取前 2000 字符（控制 Prompt 长度与 LLM 成本）
    draft = state.get("draft_content", "")[:2000]
    # 读取文书类型名称（如"民事起诉状"），缺失时兜底为"法律文书"
    template_name = state.get("template_name", "法律文书")
    # 读取纠纷类型，缺失时为空字符串
    dispute_type = state.get("dispute_type", "")

    # 构造 Prompt：把文书类型、纠纷类型、当事人、草稿摘要嵌入提示词，
    # 要求 LLM 输出纯 JSON 数组（每项：level/title/description/suggestion）。
    prompt = f"""你是一位法律风险评估专家。请对以下法律文书草稿进行风险分析,
列出 2-5 条潜在风险(包含风险等级、标题、详细描述与应对建议)。

【文书类型】{template_name}
【纠纷类型】{dispute_type}
【当事人】原告/申请人={case_summary.get("parties", {}).get("plaintiff", "")}
          被告/被申请人={case_summary.get("parties", {}).get("defendant", "")}
【草稿摘要】{draft[:1500]}

请输出 JSON 数组(纯 JSON):
[
    {{
        "level": "high/medium/low",
        "title": "风险标题",
        "description": "风险详细描述(含依据)",
        "suggestion": "应对建议"
    }}
]"""
    try:
        # 调用 LLM：SystemMessage(角色设定: 输出严格 JSON 数组) + HumanMessage(上述 Prompt)
        resp = my_llm.invoke([
            SystemMessage(content="你经验丰富的法律风险评估专家, 输出严格 JSON 数组。"),
            HumanMessage(content=prompt),
        ])
        # 取出返回文本并去除首尾空白
        text = resp.content.strip()
        # 处理 ```json ... ``` 代码块包裹（LLM 常见输出格式，需剥壳再解析）
        if "```json" in text:
            # 取第一个 ```json 之后、下一个 ``` 之前的内容
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            # 普通 ``` 包裹时：截取第一个 [ 到最后一个 ] 之间的 JSON 数组片段
            s = text.find("["); e = text.rfind("]") + 1
            # 校验截取区间合法
            if s >= 0 and e > s:
                text = text[s:e]
        # 解析 JSON 并用 isinstance 校验必须是 list（防止 LLM 返回 dict/其他结构）；
        # 若解析结果不是列表，则取空列表 []（走下方空风险逻辑）
        risks = json.loads(text) if isinstance(json.loads(text), list) else []
        # 打印日志：展示识别出的风险数量
        print(f"  识别 {len(risks)} 项风险")
        # 返回风险列表，供下游最终交付节点组装进文书
        return {"risks": risks}
    except Exception as e:
        # LLM 调用或解析失败：打印告警，提示将使用兜底风险提示
        print(f"  ⚠️ 风险分析失败: {e}, 使用兜底提示")
    # ==== 微调数据收集 ====
    # 微调样本收集块（可选旁路）：记录本节点的输入/输出用于后续模型微调
    try:
        # 构造微调输入：取 state["input"]，转字符串并截取前 2000 字符
        _ft_input = str(state.get("input", "") or "")[:2000]
        # 注意：此行是"裸字典表达式"（bare dict expression），单独成行无任何效果，
        # 属于遗留的无操作语句，此处按原样保留，不改动逻辑。
        {"risk_advice": state.get("risk_advice", "")
}
        # 调用微调样本收集器（记录节点名、输入、输出、任务类型）
        collect_ft_sample("doc_risk_advisor", _ft_input, _ft_output,
                          task_type=state.get("task_type", ""))
    except Exception:
        # 微调收集失败（如 _ft_output 未定义）：进入此分支，静默处理后返回兜底风险
        pass
        # 返回兜底风险：单条 medium 级风险"建议律师审阅"，
        # 保证最终交付节点始终能拿到非空 risks（流程健壮性）
        return {"risks": [
            {"level": "medium", "title": "建议律师审阅", "description": "本文书由AI辅助生成, 建议由执业律师审阅后正式使用。",
             "suggestion": "请执业律师复核全文, 特别是事实陈述和法律依据部分。"},
        ]}
