"""N5c-1 数值抽取节点: LLM抽取合同中的数值实体"""
# ============================================================
# 文件名称: nodes/numeric_extract_node.py
# 文件作用: 数值抽取
# ============================================================
# 【这个文件是干什么的？】
# 数值抽取
#
# 【代码逻辑主线】
# 参见各函数前的【功能】【参数】【返回值】【逻辑】说明。
#
# 【新手建议】
# 先看主函数 -> 再看辅助函数。
#

# 📜 代码文字逻辑解析
# 本文件是 AI 法律助理(LangGraph 多智能体系统)中的"数值抽取节点", 对应业务流程的 N5c-1 环节。
# 其核心职责是: 调用 LLM 从合同全文中抽取关键数值实体(单价/数量/总价/税率/违约金比例/保证金/
# 各类付款比例/天数等), 以 JSON 字典形式写入 state["extracted_numerics"]。
# 这些数值是后续 numeric_validate_node(确定性数值校验)的输入, 用于检测数值合理性
# (如违约金是否过高、付款比例之和是否为100%、质保期是否符合法规等)。
# 节点采用 "LLM 抽取 + JSON 解析 + 代码块剥离 + 异常降级" 策略:
# (1) 用结构化 prompt 指导 LLM 输出固定 schema 的 JSON;
# (2) 若 LLM 输出包含 markdown 代码块(```json ... ```), 自动剥离提取纯 JSON;
# (3) 解析失败或 LLM 异常时, 写入空字典, 由后续节点跳过校验。
# 为控制成本, 仅截取文档前 4000 字符作为抽取依据。


# 导入 json 模块, 用于解析 LLM 返回的 JSON 字符串
# json 是 Python 标准库, 提供 json.loads(反序列化) / json.dumps(序列化) 等函数
import json

# 从 langchain_core.messages 导入 HumanMessage, 用于构造 LLM 的用户消息
from langchain_core.messages import HumanMessage

# 从项目共享模块 common.llm 导入统一的 LLM 实例 my_llm
from common.llm import my_llm

# 从同包导入 AgentState 类型, 作为节点函数的类型注解
from __004__langgraph_more_nodes.agent_state import AgentState


def numeric_extract_node(state: AgentState):
    """
    数值抽取节点函数: 调用 LLM 从合同文本中抽取关键数值, 写入 state["extracted_numerics"]。

    作用:
        读取合同全文(前 4000 字), 调用 LLM 按预定义 schema 抽取所有数值实体,
        包括: 单价、数量、总价、税率、违约金比例/金额、保证金、定金、各类付款比例、
        付款周期天数、质保期天数、合同期限天数、其他金额列表。
        输出为 JSON 字典, 供 numeric_validate_node 进行规则化数值校验。

    参数:
        state (AgentState): LangGraph 共享状态字典。读取字段:
                            - doc_text (str, 可选): 合同全文(取前 4000 字)
                            写入字段:
                            - extracted_numerics (Dict): 抽取的数值字典,
                              形如 {"单价": 5000, "数量": 100, "总价": 500000, ...}

    返回值:
        AgentState: 更新后的状态字典, 必含 "extracted_numerics" 字段(可能为空字典)。

    可迁移性说明:
        本节点的"LLM 结构化抽取 + JSON 解析 + 代码块剥离"模式可迁移到任何信息抽取场景,
        例如: 简历抽取(姓名/学历/工作年限)、发票抽取(金额/税号/日期)、病历抽取(诊断/用药)等。
        只需修改 prompt 中的 JSON schema 定义即可适配新业务。
        代码块剥离逻辑(处理 ```json 包裹)具有通用性, 推荐保留。
    """
    # 打印节点开始日志
    print("开始数值抽取")

    # 从状态字典中取出合同全文, 并切片取前 4000 字符
    # [:4000] 控制传给 LLM 的文本长度, 避免超出 token 限制或产生过高费用
    # 数值信息通常分散在合同各条款中, 4000 字已能覆盖主要数值
    doc_text = state.get("doc_text", "")[:4000]

    # 若文档为空(去除空白后), 写入空字典并返回, 避免无意义调用 LLM
    if not doc_text.strip():
        state["extracted_numerics"] = {}
        return state

    # 构造抽取 prompt, 使用 f-string 嵌入合同文本
    # prompt 设计要点:
    #   (1) 明确任务"抽取所有数值信息";
    #   (2) 提供完整的 JSON schema 模板, 所有字段预填 null, LLM 只需填充;
    #   (3) "其他金额"字段为数组 [], 用于收纳未预定义的金额项;
    #   (4) 强约束输出格式"只输出JSON, 不要解释", 简化后续解析
    # 注意: JSON 模板中的双花括号 {{ }} 是 f-string 转义, 表示字面量的单花括号 { }
    prompt = f"""请从以下合同文本中抽取所有数值信息, 返回JSON格式。
需要抽取的字段(如果没有则填null):
{{
  "单价": null,
  "数量": null,
  "总价": null,
  "税率": null,
  "违约金比例": null,
  "违约金金额": null,
  "保证金": null,
  "定金": null,
  "预付款比例": null,
  "验收款比例": null,
  "质保金比例": null,
  "付款周期天数": null,
  "质保期天数": null,
  "合同期限天数": null,
  "其他金额": []
}}

合同文本:
{doc_text}

只输出JSON, 不要解释。"""

    # task_type 感知: contract_review 同时需要合规相关数值(资质/许可/社保等),
    # 在不改变既有字段契约的前提下, 提示 LLM 额外抽取合规数值键
    # (numeric_validate 只校验已知键, 未知键会被安全忽略)。
    task_type = state.get("task_type", "")
    if task_type == "contract_review":
        prompt += ("\n补充要求: 除以上字段外, 如合同涉及资质许可, 请额外提取 "
                   "\"资质有效期\"、\"许可期限\"、\"社保比例\"、\"公积金比例\"、"
                   "\"劳务派遣比例\" 等合规相关数值(无则忽略, 不要填 null 以外的占位)。")

    # 使用 try-except 包裹 LLM 调用与 JSON 解析, 防止任何环节失败导致流程中断
    try:
        # 调用 LLM 进行数值抽取, 传入 HumanMessage 列表
        # resp.content 是 LLM 返回的文本(理论上应是 JSON 字符串)
        resp = my_llm.invoke([HumanMessage(content=prompt)])

        # 取 LLM 输出文本并去除首尾空白
        content = resp.content.strip()

        # 代码块剥离逻辑: 部分 LLM 习惯用 markdown 代码块包裹 JSON 输出
        # 如 ```json\n{...}\n```, 此时直接 json.loads 会失败
        # 若检测到 "```" 标记, 则提取第一个 "{" 到最后一个 "}" 之间的内容
        if "```" in content:
            # find 返回第一个 "{" 的索引, 若不存在返回 -1
            start = content.find("{")
            # rfind 返回最后一个 "}" 的索引, +1 是因为切片是左闭右开
            end = content.rfind("}") + 1
            # 仅在 start 有效(>=0)时切片, 否则保持原 content(让后续 json.loads 报错)
            content = content[start:end] if start >= 0 else content

        # 将剥离后的 JSON 字符串解析为 Python 字典
        # 若解析失败会抛出 json.JSONDecodeError, 被 except 捕获
        numerics = json.loads(content)

        # 将抽取结果写入状态字典
        state["extracted_numerics"] = numerics
    # 捕获所有异常(LLM 调用失败、JSON 解析失败等)
    except Exception as e:
        # 打印警告日志
        print(f"⚠️ 数值抽取失败: {e}")
        # 异常时写入空字典, 后续 numeric_validate_node 会因无数值而跳过校验
        state["extracted_numerics"] = {}

    # 打印节点完成日志, 显示抽取结果(整个字典)
    print(f"完成数值抽取: {state.get('extracted_numerics')}")

    # 返回更新后的状态字典
    return state


# 模块自测入口: 直接运行本文件时执行, 验证数值抽取逻辑
if __name__ == "__main__":
    # 构造测试状态: 提供一段含多个数值的合同文本
    s = AgentState(doc_text="单价5000元, 数量100台, 总价50万元, 违约金每日千分之三")
    # 调用节点, 打印抽取的数值字典
    print(numeric_extract_node(s).get("extracted_numerics"))
