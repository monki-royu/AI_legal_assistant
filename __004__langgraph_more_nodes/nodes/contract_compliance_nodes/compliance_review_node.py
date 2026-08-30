"""【文件作用】合规审查节点 ── 从法律法规合规角度审查文档，生成合规风险项
【逻辑】本文件是 AI 法律助理(LangGraph 多智能体系统)中的合规审查节点
    核心流程：
    1. 从 【state】 中读取切分单元(doc_segments)、检索上下文包(review_context_bundle)、
       文档全文(doc_text)和合同类型(contract_type)
    2. 【硬隔离】进入本节点前先将 user_side 快照并清空，使合规审查期间无法读到用户立场，
       出节点时再恢复原始值（不破坏下游 credit_check / final_delivery 对 user_side 的消费）
    3. 【规则 + LLM 双层提取】先用本地正则对切分单元做"合规信号预筛"
       (个人信息处理/数据出境/垄断协议/发票税务/劳动用工/资质许可……)，
       把命中结果作为"重点关注清单"注入 prompt，再由 LLM 做语义判断
    4. 以"企业合规审查专家"角色调用 LLM，对文档进行 7 大合规领域、逐单元审查：
       ① 法律强制性规定违反 ② 数据合规（个保法/数安法）③ 反垄断/反不正当竞争
       ④ 税务合规（金税四期）⑤ 劳动合规 ⑥ 行业准入与资质 ⑦ 政府采购合规
    5. 要求 LLM 返回 JSON 数组格式的合规风险项，并【按切分单元编号定位风险】
    6. 对 LLM 输出进行【代码块剥离】+【JSON 解析】+【类型校验】+【segment_id 归一化】四重清洗
    7. 若任一环节异常，降级为空列表写入 state["compliance_risk_items"]
    8. 输出包含 segment_id / clause / compliance_area / severity / description /
       legal_basis / remediation 七个字段（segment_id 用于把风险精确锚定到切分单元）

【本节点的两处关键修正(相对旧实现)】
    ①【消费全文本切分】旧实现读 doc_clauses（只含"第X条"结构化条款）+ doc_text[:5000]，
      前言、附件、非结构化段落都无法被单元级定位。现改为消费 doc_segments
      （全文本统一切分，含 preamble/clause/paragraph），风险项回填 segment_id。
    ②【消费检索结果】旧实现不读 citations/research_context，"检索提前"的成果没进入
      合规推理。现改为消费 review_context_bundle，把检索查询、召回法规、
      以及【未被检索覆盖的单元】一并注入 prompt，并显式提示哪些部分缺少法规支撑。

【与合同审核的架构差异(两条链路并非同一套)】
    - 【立场】本节点对 user_side 做【硬隔离】(入口清空、出口还原)，保持客观中立；
      合同审核则是【立场化审核】，注入 user_side 站在用户一方挑对己不利条款。
    - 【规则集】本节点用 COMPLIANCE_RULE_SIGNALS（法规合规信号）；
      合同审核用 CONTRACT_RULE_SIGNALS（商业条款风险信号）。
    - 【输出字段】本节点产出 remediation(整改建议) + compliance_area(合规领域)；
      合同审核产出 suggestion(修改建议) + risk_type(风险类型)。
"""

# ============================================================
# 📦 导入模块
# ============================================================

# 导入 json 模块，用于将 LLM 返回的 JSON 数组字符串解析为 Python 列表
import json

# 导入 re 模块：用于"合规信号预筛"层的正则匹配（纯本地计算，零 LLM 成本）
import re

# 从 langchain_core.messages 导入 HumanMessage（【人类消息】类）
# 用于构造对 LLM 的用户消息输入
from langchain_core.messages import HumanMessage

# 从项目共享模块 common.llm 导入统一的 LLM 实例 my_llm
from common.llm import my_llm

# 从 common.review_context_utils 导入"审核机制四件套"（与 N5a 合同审核共享机制层）
# 【注意】共享的只是【机制】，规则集与立场处理各自私有 —— 详见文件头架构差异说明
from common.review_context_utils import (
    prescreen_segments,      # ① 规则层：正则扫描切分单元，产出合规信号命中清单
    render_prescreen_hint,   # ① 规则层：把命中清单渲染成 prompt 片段
    build_review_text,       # ② 材料层：把 doc_segments 组装成带编号的待审查材料
    build_law_block,         # ③ 依据层：把 review_context_bundle 渲染成法规依据 + 未覆盖提示
    normalize_segment_ids,   # ④ 回填层：把 LLM 输出的 segment_id 归一化锚定回原文单元
)

# 从同包导入 AgentState（【代理状态】类型），作为节点函数的类型注解
from __004__langgraph_more_nodes.agent_state import AgentState


# ============================================================
# 📐 规则层：法规合规信号正则表
# ============================================================
# 【与 N5a 的关键区别】N5a 的规则表问的是"这条对我方是否不利"（商业立场）；
#   本表问的是"这条是否可能触碰法规红线"（客观合规），与立场无关 ——
#   这正是 user_side 硬隔离在规则层的体现：规则本身就不含任何立场词。
# 【规则只做提示不下结论】命中仅作为 LLM 的注意力锚点，是否真的违规由 LLM 判定。
COMPLIANCE_RULE_SIGNALS = [
    # 【注意语序】中文里"收集个人信息"与"个人信息的收集"两种语序都常见，
    #   故正反两个方向都要匹配，否则会漏掉"收集使用其终端用户个人信息"这类写法。
    ("个人信息处理", re.compile(
        r'(个人信息|个人数据|用户信息|身份证号|人脸|生物识别).{0,20}?(收集|使用|处理|提供|共享|存储|授权)'
        r'|(收集|使用|处理|提供|共享|存储).{0,20}?(个人信息|个人数据|用户信息|身份证号|人脸|生物识别)'
    )),
    # 【注意插入语】"无需另行告知""视为已经同意"中间常有副词，故用 .{0,6} 放宽
    ("个信告知同意缺失", re.compile(r'(无需|不再|不必|无须|视为)[^。；;]{0,6}?(通知|告知|同意|授权)|(默认|自动)[^。；;]{0,4}?(同意|授权)')),
    ("数据出境/跨境", re.compile(r'(数据|信息).{0,10}(出境|跨境|境外(传输|存储|服务器))')),
    ("敏感/重要数据", re.compile(r'(敏感个人信息|重要数据|核心数据|国家秘密|商业秘密)')),
    ("垄断/限制竞争", re.compile(r'(独家|排他|唯一).{0,15}(供应|销售|代理|合作)|(限定|固定).{0,10}(转售|最低)?价格|划分.{0,6}(市场|区域)')),
    ("不正当竞争", re.compile(r'(商业贿赂|回扣|好处费|返点|虚假宣传|搭售|捆绑销售)')),
    ("发票/税务合规", re.compile(r'(不(开|提供)发票|无票|白条|开(具)?(增值税)?专用发票|税(点|费).{0,10}承担|阴阳合同)')),
    ("劳动用工合规", re.compile(r'(劳务派遣|试用期|加班|工时|社会保险|公积金|竞业限制补偿|不缴纳社保)')),
    ("资质/许可缺失", re.compile(r'(资质|许可证|备案|准入|执业(资格|许可)).{0,15}(无需|不需|未|欠缺|由.{0,6}方(负责|提供))')),
    ("政府采购合规", re.compile(r'(政府采购|招标|投标|中标|评标).{0,20}(规避|指定|串通|围标|陪标)')),
    ("格式条款/消费者权益", re.compile(r'(概不(退换|负责)|不予退还|最终解释权|视为.{0,6}放弃)')),
    ("强制性规定冲突", re.compile(r'(不受.{0,10}法律|排除.{0,10}(法律|法规)(适用|管辖)|以本合同为准.{0,10}法律)')),
]


def compliance_review_node(state: AgentState):
    """
    【功能】合规审查节点函数：从法律合规角度、逐单元审查文档，生成合规风险项列表
    【参数】state (AgentState)：LangGraph 共享状态字典，读取以下字段：
                - doc_segments (List[Dict], 可选)【全文本切分单元】：优先输入，每项含
                  id/type/title/text，按 [编号|类型] 组装成待审查材料（含前言/条款/段落）
                - review_context_bundle (Dict, 可选)【检索上下文包】：含检索原始查询、
                  已召回法规(citations_brief/research_context)、未被检索覆盖的单元
                - doc_text (str, 可选)【文档全文】：doc_segments 为空时的回退输入
                - contract_type (str, 可选)【合同类型】：默认 "其他"
                - user_side (str, 可选)【用户立场】：本节点【硬隔离】——进入即清空，
                  保证合规审查看不到立场，出节点时还原，不影响下游
            写入字段：
                - compliance_risk_items (List[Dict])【合规风险项列表】：七个字段的结构化合规风险项
                  （新增 segment_id，用于把风险锚定回具体切分单元）
    【返回值】AgentState：更新后的状态字典，必含 "compliance_risk_items" 字段（可能为空列表）
    【逻辑】① 快照并清空 user_side（硬隔离）② 读取 doc_segments / review_context_bundle / doc_text
            ③ 规则层预筛合规信号 ④ 组装切分材料 + 法规依据
            ⑤ 构造合规审查 prompt（7 大重点 + 单元级定位）⑥ 调用 LLM 审查
            ⑦ 剥离代码块标记 ⑧ 解析 JSON 数组 ⑨ 类型校验 ⑩ segment_id 归一化回填
            ⑪ 写入 state ⑫ 还原 user_side ⑬ 异常时写入空列表降级
    【与 N5a 合同审核的区别】
            - 【立场】合同审核是立场化（从 user_side 出发），合规审查是客观法律合规（已硬隔离 user_side）
            - 【规则集】合同审核用 CONTRACT_RULE_SIGNALS（商业风险信号），
              合规审查用 COMPLIANCE_RULE_SIGNALS（法规合规信号，不含任何立场词）
            - 【字段】合同审核用 suggestion（修改建议），合规审查用 remediation（整改建议）
            - 【领域】合同审核关注商业条款，合规审查关注法律法规合规
            - 【粒度】两者现在都按 doc_segments 的单元编号定位（共享机制层，规则集各自私有）
    【可迁移性】本节点的"合规视角审查 + 多领域覆盖 + 规则预筛"模式可迁移到任何合规审查场景，
            如上市公司信息披露合规、医疗广告合规、跨境数据传输合规等。
    """
    # 【步骤1】打印节点开始日志，标记进入此节点
    print("--- 开始合规审查 ---")

    # ============================================================
    # 【步骤2】硬隔离用户立场（prompt 层实现）
    # ============================================================
    # 【为什么硬隔离？】合规审查必须客观中立，绝不能被"用户站在哪一边"
    # 的立场污染。本节点在【prompt 文本】层面实现硬隔离——prompt 明确声明
    # "本次审查不提供签约方立场信息"且不注入 user_side（见步骤6）。
    # 【为什么不再原地清空 state["user_side"]？】LangGraph 并行 fan-out 下
    # 多个节点共享同一 state 字典，原地修改会污染并行分支（contract_ai_review
    # 需要读 user_side 做立场化审查），且 `return state` 会与并行节点产生
    # 同键写入冲突 (InvalidUpdateError)。故本节点不读、不改 user_side，
    # 只返回 partial update {"compliance_risk_items": ...}。

    # ============================================================
    # 【步骤3】从 state 中读取输入字段
    # ============================================================

    # 读取全文本统一切分单元（本节点的首选输入）
    # 【上游】full_text_segment_node 产出，含 preamble(前言)/clause(第X条)/paragraph(段落)
    # 【为什么不再用 doc_clauses？】doc_clauses 只含"第X条"结构化条款，
    #   前言、附件、非结构化段落全都定位不到 —— 而合规风险恰恰常藏在这些地方
    #   （例如前言里的"甲方授权乙方使用其用户数据"）。
    doc_segments = state.get("doc_segments", []) or []

    # 读取检索上下文包（本节点的第二个关键输入）
    # 【上游】context_pack_node 产出，把"检索查询 + 召回法规 + 未覆盖单元"打成一个包
    review_bundle = state.get("review_context_bundle", {}) or {}

    # 读取文档全文（doc_segments 为空时的回退输入）
    doc_text = state.get("doc_text", "") or ""

    # 读取合同类型，默认 "其他"
    # 注入 prompt 让 LLM 知晓文档类型，便于针对性审查
    contract_type = state.get("contract_type", "其他")  # 合同/文档类型标识

    # 检索聚焦(本视角查询): 来自 llm_query_extract 的"合规审查视角"查询集合,
    # 让本合规审查大模型明确应重点对照的法条检索方向 (与合同审核视角区分)。
    retrieval_queries = state.get("retrieval_queries", {}) or {}
    _compliance_focus = retrieval_queries.get("compliance_review", []) or []
    compliance_focus_block = ""
    if _compliance_focus:
        _cq_text = "\n".join(f"  - {q}" for q in _compliance_focus[:12])
        compliance_focus_block = f"""
【检索聚焦 · 合规审查视角查询(供你重点对照的法条检索方向)】
{_cq_text}
"""

    # ============================================================
    # 【步骤4】规则层预筛：本地正则先标出"可能触碰法规红线的单元"
    # ============================================================
    # 【规则 + LLM 双层提取的第一层】零 LLM 成本；命中项只作为 LLM 的注意力锚点。
    # 【注意】这里用的是 COMPLIANCE_RULE_SIGNALS（合规信号），
    #   与 N5a 的 CONTRACT_RULE_SIGNALS（商业信号）是两张完全不同的表 —— 架构差异所在。
    prescreen_hits = prescreen_segments(doc_segments, COMPLIANCE_RULE_SIGNALS)
    prescreen_hint = render_prescreen_hint(prescreen_hits, "规则预筛命中的合规重点单元")

    # ============================================================
    # 【步骤5】组装待审查材料 + 法规依据
    # ============================================================
    # ① 材料文本：优先用切分单元（带 [编号|类型] 前缀），无切分单元才回退全文
    review_text, source_desc = build_review_text(doc_segments, doc_text, max_chars=12000)

    # ② 法规依据文本：来自 review_context_bundle（检索查询 + 召回法规 + 未覆盖单元）
    law_block = build_law_block(
        review_bundle,
        "【检索依据】本次未获得检索上下文，legal_basis 请标注\"无检索依据·凭经验判断\"，禁止编造法条。",
    )

    # ============================================================
    # 【步骤6】构造合规审查 prompt（核心设计 · 单元级）
    # ============================================================
    # 【Prompt 设计要点】
    #   ①【角色定位】"企业合规审查专家" → 侧重法规合规而非商业合理性
    #   ②【单元级定位】要求 LLM 回填 segment_id（材料中 [编号|类型] 的编号）
    #   ③【审查重点】列出 7 大合规领域 → 覆盖主要合规风险维度
    #   ④【规则预筛注入】{prescreen_hint} → 规则层告诉 LLM"先看哪几个单元"，降低漏检
    #   ⑤【法规依据注入】{law_block} → 让"检索提前"真正服务于合规推理，并标出未覆盖部分
    #   ⑥【compliance_area 字段】标准化为数据/税务/劳动/反垄断/行业准入 → 便于分类统计
    #   ⑦【remediation 字段】区别于合同审核的 suggestion，强调"如何整改以达到合规"
    #   ⑧【severity 标准化】critical/high/medium/low → 与合同审核保持一致，便于后续聚合
    # 【注意】本 prompt 不注入 user_side（已在步骤2清空），合规审查保持中立；
    #   也不含"对我方有利/不利"之类的立场措辞 —— 只问"是否合法合规"。
    prompt = f"""你是一个企业合规审查专家。请从法律法规合规角度客观审查以下{contract_type}合同/文档。
请只判断"是否合法合规"，不要考虑任何一方的商业利益得失（本次审查不提供签约方立场信息）。

合规审查重点:
1. 是否违反法律法规强制性规定
2. 数据合规(个人信息保护法/数据安全法)
3. 反垄断/反不正当竞争
4. 税务合规(金税四期)
5. 劳动合规(如涉及)
6. 行业准入与资质
7. 政府采购合规(如涉及)
{prescreen_hint}{law_block}{compliance_focus_block}
请返回JSON数组, 每个合规风险项包含:
{{
  "segment_id": "该风险对应的材料单元编号(材料中 [编号|类型] 的编号, 整数; 无法定位填 null)",
  "clause": "风险所在的条款标题或原文摘录，无法对应具体单元时填'全文/总体'",
  "compliance_area": "合规领域(数据/税务/劳动/反垄断/行业准入)",
  "severity": "critical/high/medium/low",
  "description": "合规风险描述(结合具体单元内容)",
  "legal_basis": "法律依据(优先引用上方【已检索到的法条/案例依据】; 无依据写\\"无检索依据·凭经验判断\\", 禁止编造法条)",
  "remediation": "整改建议"
}}

{source_desc}:
{review_text}

只输出JSON数组, 不要解释。如无风险返回 []"""

    # ============================================================
    # 【步骤7】调用 LLM + 解析输出（try-except 包裹，全程容错）
    # ============================================================
    try:
        # 调用 LLM 的 invoke 方法，传入包含 HumanMessage 的消息列表
        # resp 是 AIMessage 类型，resp.content 为生成的文本
        resp = my_llm.invoke([HumanMessage(content=prompt)])

        # 取 LLM 输出文本并去除首尾空白字符
        content = resp.content.strip()  # 清洗后的 LLM 输出字符串

        # ============================================================
        # 【步骤8】代码块剥离：处理 LLM 用 ``` 包裹输出的情况
        # ============================================================
        # 逻辑与 contract_ai_review_node 完全一致，处理 JSON 数组格式
        if "```" in content:
            # find("["): 查找第一个 "[" 索引，即 JSON 数组起始位置
            start = content.find("[")
            # rfind("]"): 查找最后一个 "]" 索引，+1 为切片右边界
            end = content.rfind("]") + 1
            # 仅当 start >= 0（找到了 "["）时才切片，否则保留原内容
            content = content[start:end] if start >= 0 else content

        # ============================================================
        # 【步骤9】JSON 解析：将字符串解析为 Python 列表
        # ============================================================
        # json.loads() 将 JSON 数组字符串转为 Python list 对象
        # 若 LLM 输出不是合法 JSON，会抛出 json.JSONDecodeError 异常
        risks = json.loads(content)  # 解析后的合规风险项列表

        # ============================================================
        # 【步骤10】类型校验：确保解析结果为 list 类型
        # ============================================================
        # 防御 LLM 返回单个字典对象而非数组的情况
        if not isinstance(risks, list):
            risks = []  # 类型不符时降级为空列表

        # ============================================================
        # 【步骤11】segment_id 归一化：把合规风险锚定回原文切分单元
        # ============================================================
        # 【为什么要归一化？】LLM 可能把 segment_id 写成 "3"、"[3]"、"单元3" 或漏掉；
        #   下游 risk_aggregate_node 与前端原文高亮需要稳定的 int/None。
        # 【绝不伪造编号】抠出的整数必须真实存在于 doc_segments 中才保留，否则置 None。
        risks = normalize_segment_ids(risks, doc_segments)

        # 将合规风险项列表作为 partial update 返回 (不原地写 state,
        # 避免并行 fan-out 下与 contract_ai_review 的同键写入冲突)

    # ============================================================
    # 【步骤12】异常处理：任何异常都降级为空列表
    # ============================================================
    except Exception as e:
        # 打印警告日志，包含异常信息
        print(f"⚠️ 合规审查失败: {e}")
        # 异常时置空列表，保证下游聚合节点不会因字段缺失而报错
        risks = []

    # 打印节点完成日志，显示识别出的合规风险项数量
    print(f"--- 完成合规审查: {len(risks)} 个风险项 (prompt 层立场硬隔离) ---")

    # 返回 partial update (只写本节点产物, 不返回整个 state 对象)
    # LangGraph 会将此字典合并到全局状态中
    return {"compliance_risk_items": risks}


# ============================================================
# 🧪 模块自测入口（仅在直接运行本文件时执行）
# ============================================================
if __name__ == "__main__":
    # 构造测试状态：提供一段涉及数据合规问题的文档
    # doc_text = "甲方收集用户个人信息用于营销, 不告知用户"
    s = AgentState(doc_text="甲方收集用户个人信息用于营销, 不告知用户")
    # 调用合规审查节点，获取 compliance_risk_items 并格式化打印
    # json.dumps(..., ensure_ascii=False 保留中文, indent=2 缩进美化)
    print(json.dumps(compliance_review_node(s).get("compliance_risk_items"), ensure_ascii=False, indent=2))