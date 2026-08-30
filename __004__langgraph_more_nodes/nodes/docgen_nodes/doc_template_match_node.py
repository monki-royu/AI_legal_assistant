# 📜 ============================================================
# 文件名称: nodes/doc_template_match_node.py
# 文件作用: 模板匹配节点
# ============================================================
#
# 【这个文件是干什么的？】
# 基于案情分析匹配最合适的法律文书模板。
#
# 【代码逻辑主线】
# 读取 case_analysis → 匹配预设模板 → 写入 template_match
#
# 【谁在调用它？】
# langgraph_main.py 中的 build_graph() 通过 add_node 注册本节点，
# 并通过 add_edge / add_conditional_edges 定义其前后依赖。

'''
这段代码实现了一个基于 LangGraph 的法律文书模板智能匹配节点。它的核心作用是充当“路由器”：根据用户的案情分析结果，精准挑选出最适合的法律文书模板。

下面我为你详细拆解它的代码逻辑，并回答你关于“大模型是否能自动生成”的疑问。

📜 一、 代码逻辑详细拆解

整个节点的运行逻辑可以分为四个主要步骤：

状态读取与兜底机制
   代码首先从 AgentState 中提取关键信息。为了保证程序的健壮性，它设计了优先级读取：优先使用结构化提取的 case_summary.case_type，如果没有，则退而求其次使用 dispute_type，最后才使用用户的原始输入 input。

构造 Prompt（提示词）
   代码将预定义的 TEMPLATES 字典转化为一段格式化的文本清单（包含模板ID、名称、分类和描述）。然后，将“用户需求”、“已识别案由”和“可用模板清单”拼装成一个 Prompt，要求大模型（LLM）扮演“经验丰富的法律文书专家”，并严格以 JSON 格式返回最匹配的模板ID和匹配置信度。

大模型推理与结果解析
   调用 my_llm 发送消息后，代码对返回结果进行了防御性处理。因为大模型有时会自作聪明地用  json  代码块包裹输出，代码通过字符串分割和 find 方法精准提取出纯 JSON 字符串，并使用 json.loads 解析。同时，它还做了边界校验（如置信度限制在 0.0~1.0 之间，模板ID必须在预定义列表中）。

异常处理与规则兜底（Fallback）
   这是工程化非常关键的一步。如果大模型调用失败（如网络超时、JSON解析错误等），代码不会让流程崩溃，而是进入 except 块，通过一个硬编码的 fallback_map 字典，基于 dispute_type 进行传统的规则匹配，并赋予一个默认的 0.6 置信度，确保系统始终有结果输出。

💡 二、 核心疑问解答：大模型知道范围后，可以自动生成吗？

结论是：不能。大模型在这里只负责“做选择题”，不负责“做填空题”。

针对你的疑问，我们需要明确区分“模板匹配（Template Matching）”和“文书生成（Document Generation）”这两个概念：

大模型知道的范围是什么？
   是的，通过 TEMPLATES 元数据，大模型清楚地知道系统里有哪些文书可用（比如民事起诉状、律师函等），以及每种文书的适用场景（description）。这使得大模型能够根据案情，从这 10 个选项中挑出最准确的一个。

大模型能自动生成文书吗？
   不能。 这段代码的返回值仅仅是 template_id（例如 "civil_complaint"）、template_confidence 和 template_name。它没有包含任何具体的文书内容（如具体的诉讼请求怎么写、事实理由的段落结构等）。

真正的生成逻辑在哪里？
   在标准的 LangGraph 架构中，这个节点只是流水线的一环。它的下游节点（例如 doc_generation_node）会拿到这个 template_id`，然后去数据库或本地文件中加载该模板的具体骨架（Jinja2/占位符模板），最后再调用大模型，把案情要素填入骨架中，这才完成了真正的“自动生成”。

总结来说： 这段代码利用大模型的语义理解能力，解决的是“选对工具”的问题；而“用工具干活（生成文书）”，是由后续的节点结合具体的模板文件来完成的。这种设计将“路由”和“生成”解耦，既保证了匹配的准确性，又方便后续单独维护文书模板。
'''

# -*- coding: utf-8 -*-

import json
from langchain_core.messages import SystemMessage, HumanMessage
from common.llm import my_llm
from __004__langgraph_more_nodes.agent_state import AgentState


# 预定义模板元数据(优化点:后续可从文件/数据库加载)
TEMPLATES = {
    "civil_complaint": {
        "name": "民事起诉状",
        "category": "诉讼文书",
        "description": "适用于民事纠纷的一审起诉, 含诉讼请求、事实与理由、证据清单",
    },
    "civil_defense": {
        "name": "民事答辩状",
        "category": "诉讼文书",
        "description": "适用于民事案件被告方的答辩, 针对原告诉求逐条反驳",
    },
    "arbitration_apply": {
        "name": "仲裁申请书",
        "category": "仲裁文书",
        "description": "适用于合同纠纷/劳动争议等提交仲裁委员会",
    },
    "lawyer_letter": {
        "name": "律师函",
        "category": "非诉文书",
        "description": "适用于律师受委托向对方发出正式法律函件, 含事实陈述、法律依据、要求",
    },
    "contract_draft": {
        "name": "合同草稿",
        "category": "合同文书",
        "description": "适用于起草/修订合同文本",
    },
    "appeal_petition": {
        "name": "上诉状",
        "category": "诉讼文书",
        "description": "适用于对一审判决不服提起上诉",
    },
    "execution_apply": {
        "name": "强制执行申请书",
        "category": "执行文书",
        "description": "适用于申请法院强制执行生效判决",
    },
    "property_preservation": {
        "name": "财产保全申请书",
        "category": "保全文书",
        "description": "适用于诉讼前/诉讼中申请财产保全",
    },
    "evidence_preservation": {
        "name": "证据保全申请书",
        "category": "保全文书",
        "description": "适用于证据可能灭失时申请保全",
    },
    "mediation_apply": {
        "name": "调解申请书",
        "category": "调解文书",
        "description": "适用于申请人民调解委员会/法院调解",
    },
}


def doc_template_match_node(state: AgentState):
    """
    doc_template_match_node 函数: 实现节点具体逻辑。

    读取:
        - case_summary (Dict): doc_case_analyze 输出的案情结构化结果 (含 case_type)
        - dispute_type (str):  纠纷类型 (兜底)
        - input (str):         用户原始输入 (兜底)
    写入:
        - template_id (str): 匹配的模板 ID
        - template_confidence (float): 匹配置信度
        - template_name (str): 模板名称
    """
    # 从 state 读取案情要素: 优先 case_summary.case_type, 其次 dispute_type, 再次 input
    case_summary = state.get("case_summary", {}) or {}
    dispute_type = str(state.get("dispute_type", "") or "") or str(case_summary.get("case_type", "") or "")
    user_input = str(state.get("input", "") or "")

    # 构造模板匹配 prompt: 把用户诉求 + 已抽取案由 + 模板清单交给 LLM
    template_list = "\n".join(
        f"- {tid}: {meta['name']} ({meta['category']}) - {meta['description']}"
        for tid, meta in TEMPLATES.items()
    )
    prompt = f"""请根据用户的法律文书需求, 从以下模板清单中选择最合适的模板。

【用户需求】{user_input[:2000]}
【已识别案由】{dispute_type}
【可用模板】
{template_list}

输出严格 JSON: {{"template_id": "<模板ID>", "template_confidence": 0.0~1.0}}
只需输出 JSON, 不要输出其他文字。"""

    try:
        resp = my_llm.invoke([
            SystemMessage(content="你经验丰富的法律文书专家, 输出严格 JSON。"),
            HumanMessage(content=prompt),
        ])
        text = resp.content.strip()
        # 处理 ```json ... ``` 包裹
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            s = text.find("{"); e = text.rfind("}") + 1
            if s >= 0 and e > s:
                text = text[s:e]
        result = json.loads(text)
        tid = result.get("template_id", "civil_complaint")
        if tid not in TEMPLATES:
            tid = "civil_complaint"
        confidence = max(0.0, min(1.0, float(result.get("template_confidence", 0.5))))
        print(f"  匹配: {TEMPLATES.get(tid, {}).get('name', tid)} (置信度 {confidence:.0%})")
        return {"template_id": tid, "template_confidence": confidence, "template_name": TEMPLATES.get(tid, {}).get("name", tid)}
    except Exception as e:
        # LLM 失败时基于 dispute_type 做规则兜底
        fallback_map = {
            "劳动争议": "arbitration_apply",
            "合同纠纷": "civil_complaint",
            "婚姻家庭": "civil_complaint",
            "交通事故": "civil_complaint",
            "房产纠纷": "civil_complaint",
            "民间借贷": "civil_complaint",
            "执行": "execution_apply",
            "保全": "property_preservation",
            "上诉": "appeal_petition",
            "调解": "mediation_apply",
            "律师函": "lawyer_letter",
            "合同": "contract_draft",
        }
        tid = fallback_map.get(dispute_type, "civil_complaint")
        print(f"  ⚠️ LLM 匹配失败({e}), 规则兜底: {TEMPLATES.get(tid, {}).get('name', tid)}")
        return {"template_id": tid, "template_confidence": 0.6, "template_name": TEMPLATES.get(tid, {}).get("name", tid)}