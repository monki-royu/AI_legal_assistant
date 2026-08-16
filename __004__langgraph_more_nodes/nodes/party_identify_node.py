"""N8 甲乙方识别节点: 识别甲乙方名称, 判断用户立场"""
# ============================================================
# 文件名称: nodes/party_identify_node.py
# 文件作用: 甲乙方识别
# ============================================================
# 【这个文件是干什么的？】
# 甲乙方识别
#
# 【代码逻辑主线】
# 参见各函数前的【功能】【参数】【返回值】【逻辑】说明。
#
# 【新手建议】
# 先看主函数 -> 再看辅助函数。
#

# 📜 代码文字逻辑解析
# 本文件是 AI 法律助理(LangGraph 多智能体系统)中的"甲乙方识别节点", 对应业务流程的 N8 环节。
# 其核心职责是: 从合同文本中识别甲方与乙方的名称, 并判断当前用户代表哪一方(用户立场),
# 将结果写入 state["party_a"]/state["party_b"]/state["user_side"]。
# 用户立场(user_side)是后续 contract_ai_review_node 的重要输入, 决定 LLM 从甲方还是乙方
# 视角评估条款利弊(如对甲方不利的条款, 若用户是甲方则风险加权)。
# 节点采用 "规则优先 + LLM 兜底" 双层策略:
# (1) 规则层: 用 4 种正则模式匹配"甲方(XXX)"/"甲方:XXX"/"甲方为XXX"/"甲方 XXX"等常见格式;
# (2) LLM 层: 若规则未匹配到任何一方, 调用 LLM 从文本中识别;
# (3) 立场判断: 通过用户输入(input)中是否包含甲方/乙方名称, 或显式提及"甲方"/"乙方"关键词推断。
# 该设计兼顾性能(规则快)与覆盖度(LLM 兜底), 是工程上的经典权衡。


# 导入 re 模块, 用于正则表达式匹配甲乙方名称
import re

# 从 langchain_core.messages 导入 HumanMessage, 用于构造 LLM 的用户消息
from langchain_core.messages import HumanMessage

# 从项目共享模块 common.llm 导入统一的 LLM 实例 my_llm
from common.llm import my_llm

# 从同包导入 AgentState 类型, 作为节点函数的类型注解
from __004__langgraph_more_nodes.agent_state import AgentState


def party_identify_node(state: AgentState):
    """
    甲乙方识别节点函数: 识别合同中的甲乙方名称, 判断用户立场, 写入多个状态字段。

    作用:
        (1) 读取合同全文(前 3000 字)与用户输入;
        (2) 优先用 4 种正则模式匹配甲方/乙方名称(规则层);
        (3) 若任一方未匹配到, 调用 LLM 从文本识别(LLM 兜底层);
        (4) 通过用户输入推断用户立场(A/B/Unknown): 若 input 含甲方名称则用户为甲方,
            含乙方名称则为乙方, 或含"甲方"/"乙方"关键词时按关键词判断;
        (5) 输出 party_a/party_b/user_side 三个字段。

    参数:
        state (AgentState): LangGraph 共享状态字典。读取字段:
                            - doc_text (str, 可选): 合同全文(取前 3000 字)
                            - input (str, 可选): 用户原始输入(用于立场判断)
                            写入字段:
                            - party_a (str): 甲方名称
                            - party_b (str): 乙方名称
                            - user_side (str): 用户立场(A/B/Unknown)

    返回值:
        AgentState: 更新后的状态字典, 必含 party_a/party_b/user_side 三个字段。

    可迁移性说明:
        本节点的"规则优先 + LLM 兜底 + 立场推断"架构可迁移到任何主体识别场景,
        例如: 招投标中的招标方/投标方识别、诉讼中的原告/被告识别、合同中的买方/卖方识别等。
        通过修改正则模式与 prompt 即可适配新业务。
        "规则优先 LLM 兜底"是控制成本与提升覆盖度的经典模式, 推荐保留。
    """
    # 打印节点开始日志
    print("开始甲乙方识别")

    # 从状态字典中取出合同全文, 并切片取前 3000 字符
    # 3000 字足以覆盖合同开头的双方信息部分
    doc_text = state.get("doc_text", "")[:3000]

    # 取用户原始输入, 用于后续立场判断(检查 input 中是否含甲方/乙方名称)
    user_input = state.get("input", "")

    # 初始化甲乙方名称为空字符串
    # party_a / party_b 分别存储识别到的甲方 / 乙方名称
    party_a = ""
    party_b = ""

    # 规则层: 定义甲方名称的 4 种正则匹配模式(按优先级排序)
    # 每个模式用捕获组 (...) 提取甲方名称
    patterns_a = [
        # 模式1: "甲方(XXX)" 或 "甲方（XXX）" - 全角/半角括号
        r'甲方[（(]([^)）]+)[)）]',
        # 模式2: "甲方：XXX" 或 "甲方:XXX" - 全角/半角冒号, 后接非换行/标点字符
        r'甲方[：:]\s*([^\n，,。.]+)',
        # 模式3: "甲方为XXX" - "为"字连接
        r'甲方为\s*([^\n，,。.]+)',
        # 模式4: "甲方 XXX" - 空格分隔(最宽松, 兜底)
        r'甲方\s+([^\n，,。.(（]+)',
    ]
    # 遍历模式, 按优先级尝试匹配, 命中即跳出
    for p in patterns_a:
        # re.search 在文本中搜索第一个匹配
        m = re.search(p, doc_text)
        if m:
            # m.group(1) 取第一个捕获组内容(即甲方名称), strip 去空白
            party_a = m.group(1).strip()
            break  # 命中则跳出循环, 不再尝试后续模式

    # 规则层: 定义乙方名称的 4 种正则匹配模式(与甲方对称)
    patterns_b = [
        # 模式1: "乙方(XXX)" 或 "乙方（XXX）"
        r'乙方[（(]([^)）]+)[)）]',
        # 模式2: "乙方：XXX" 或 "乙方:XXX"
        r'乙方[：:]\s*([^\n，,。.]+)',
        # 模式3: "乙方为XXX"
        r'乙方为\s*([^\n，,。.]+)',
        # 模式4: "乙方 XXX"
        r'乙方\s+([^\n，,。.(（]+)',
    ]
    # 遍历模式, 匹配乙方名称
    for p in patterns_b:
        m = re.search(p, doc_text)
        if m:
            party_b = m.group(1).strip()
            break

    # LLM 兜底层: 若规则未匹配到甲方或乙方, 调用 LLM 识别
    # not party_a or not party_b: 任一为空则触发 LLM
    if not party_a or not party_b:
        # 构造 LLM 识别 prompt, 使用 f-string 嵌入合同文本(进一步截取前 1500 字以控制成本)
        # prompt 设计要点:
        #   (1) 明确任务"识别甲方和乙方的名称";
        #   (2) 提供 JSON schema 强约束输出;
        #   (3) 处理边界情况"如果没有明确名称, 填'甲方'/'乙方'";
        #   (4) 截取前 1500 字(双方信息通常在开头)
        prompt = f"""请从以下合同文本中识别甲方和乙方的名称。
返回JSON: {{"party_a": "甲方名称", "party_b": "乙方名称"}}
如果没有明确名称, 填"甲方"/"乙方"。

合同文本:
{doc_text[:1500]}

只输出JSON。"""
        try:
            # 调用 LLM 进行甲乙方识别
            resp = my_llm.invoke([HumanMessage(content=prompt)])
            # 延迟导入 json(仅在需要时加载, 微优化)
            import json
            # 取 LLM 输出文本
            content = resp.content.strip()
            # 代码块剥离: 处理 ```json 包裹的输出
            if "```" in content:
                # 提取第一个 "{" 到最后一个 "}" 之间的 JSON
                start = content.find("{")
                end = content.rfind("}") + 1
                content = content[start:end]
            # 解析 JSON 字符串为字典
            data = json.loads(content)
            # 仅在规则未匹配到时, 用 LLM 结果填充(避免覆盖规则已识别的名称)
            if not party_a:
                # 默认值 "甲方"(LLM 也未识别时的兜底)
                party_a = data.get("party_a", "甲方")
            if not party_b:
                party_b = data.get("party_b", "乙方")
        # 捕获所有异常(LLM 调用失败、JSON 解析失败等)
        except Exception as e:
            # 打印警告日志
            print(f"  ⚠️ LLM识别失败: {e}")
            # 兜底: 若 party_a 仍为空(规则与 LLM 均失败), 使用通用名"甲方"
            # "party_a or '甲方'" 是短路求值: party_a 为空时取 '甲方'
            party_a = party_a or "甲方"
            # 同理, party_b 兜底为 "乙方"
            party_b = party_b or "乙方"

    # 立场判断阶段: 根据用户输入推断用户代表哪一方
    # 默认 "Unknown"(无法判断)
    user_side = "Unknown"

    # 判断逻辑按优先级:
    # (1) 若用户输入包含甲方名称(且甲方名称非通用"甲方"), 则用户为甲方
    if party_a and party_a != "甲方" and party_a in user_input:
        user_side = "A"
    # (2) 若用户输入包含乙方名称(且乙方名称非通用"乙方"), 则用户为乙方
    elif party_b and party_b != "乙方" and party_b in user_input:
        user_side = "B"
    # (3) 若用户输入含"甲方"但不含"乙方", 则用户为甲方
    elif "甲方" in user_input and "乙方" not in user_input:
        user_side = "A"
    # (4) 若用户输入含"乙方"但不含"甲方", 则用户为乙方
    elif "乙方" in user_input and "甲方" not in user_input:
        user_side = "B"
    # 其余情况保持 "Unknown"(用户未明确立场)

    # 将识别结果写入状态字典
    state["party_a"] = party_a
    state["party_b"] = party_b
    state["user_side"] = user_side

    # 打印节点完成日志, 显示甲方、乙方、用户立场
    print(f"完成甲乙方识别: 甲方={party_a}, 乙方={party_b}, 用户立场={user_side}")

    # 返回更新后的状态字典
    return state


# 模块自测入口: 直接运行本文件时执行, 验证甲乙方识别逻辑
if __name__ == "__main__":
    # 构造测试状态: 用户输入含甲方名称"A公司", 合同文本含"甲方(A公司)"格式
    s = AgentState(input="我是A公司", doc_text="甲方(A公司)向乙方(B公司)采购电脑")
    # 调用节点, 打印完整状态字典(应包含 party_a="A公司", party_b="B公司", user_side="A")
    print(party_identify_node(s))
