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


# -*- coding: utf-8 -*-

import json
from langchain_core.messages import SystemMessage, HumanMessage
from common.llm import my_llm
from __004__langgraph_more_nodes.agent_state import AgentState


# 预定义模板元数据(后续可从文件/数据库加载)
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
    """
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
        confidence = max(0.0, min(1.0, float(result.get("template_confidence", 0.5))))
        print(f"  匹配: {TEMPLATES.get(tid, {}).get('name', tid)} (置信度 {confidence:.0%})")
        return {"template_id": tid, "template_confidence": confidence, "template_name": TEMPLATES.get(tid, {}).get("name", tid)}
    except Exception as e:
        # LLM 失败时基于 dispute_type 做规则兜底
        fallback_map = {
            "劳动争议": "civil_complaint",
            "合同纠纷": "civil_complaint",
            "婚姻家庭": "civil_complaint",
            "交通事故": "civil_complaint",
            "房产纠纷": "civil_complaint",
            "民间借贷": "civil_complaint",
        }
        tid = fallback_map.get(dispute_type, "civil_complaint")
        print(f"  ⚠️ LLM 匹配失败({e}), 规则兜底: {TEMPLATES.get(tid, {}).get('name', tid)}")
        return {"template_id": tid, "template_confidence": 0.6, "template_name": TEMPLATES.get(tid, {}).get("name", tid)}