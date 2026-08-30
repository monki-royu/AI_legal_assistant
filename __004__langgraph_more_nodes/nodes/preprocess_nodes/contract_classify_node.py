"""N3 合同分类节点: LLM判断合同类型"""
# ============================================================
# 文件名称: nodes/contract_classify_node.py
# 文件作用: 合同分类
# ============================================================
# 【这个文件是干什么的？】
# 合同分类
#
# 【代码逻辑主线】
# 参见各函数前的【功能】【参数】【返回值】【逻辑】说明。
#
# 【新手建议】
# 先看主函数 -> 再看辅助函数。
#

# 📜 代码文字逻辑解析
# 本文件是 AI 法律助理(LangGraph 多智能体系统)中的"合同分类节点", 对应业务流程的 N3 环节。
# 在文档提取(N2)之后, 本节点根据合同全文(state["doc_text"])判断合同的具体类型(买卖/租赁/借贷等),
# 并将结果写入 state["contract_type"]。该类型字段在后续节点中起到关键作用:
# (1) contract_ai_review_node 会基于合同类型选择不同的审核 prompt 与侧重点;
# (2) compliance_review_node 会依据类型选择对应的合规法规;
# (3) 最终报告会展示合同类型作为基础信息。
# 节点采用 LLM 分类 + 白名单校验 + 子串匹配纠正 + 异常降级("其他")的策略,
# 确保输出始终在预定义的 9 种合同类型范围内。为控制 LLM 成本与延迟,
# 仅截取文档前 3000 字作为分类依据(绝大多数合同类型在开头即可识别)。


# 从 langchain_core.messages 导入 HumanMessage, 用于构造 LLM 的用户消息
from langchain_core.messages import HumanMessage

# 从项目共享模块 common.llm 导入统一的 LLM 实例 my_llm
# 所有节点共享同一 LLM 实例, 便于统一配置与模型切换
from common.llm import my_llm

# 从同包导入 AgentState 类型, 作为节点函数的类型注解
from __004__langgraph_more_nodes.agent_state import AgentState

# 定义合同类型候选列表(常量), 作为 LLM 分类的白名单
# 包含 9 种常见合同类型: 买卖/租赁/借贷/建设工程/政府采购/劳动/服务/技术/其他
# 最后一项 "其他" 作为兜底类型, 当无法归入前 8 种时使用
CONTRACT_TYPES = ["买卖", "租赁", "借贷", "建设工程", "政府采购", "劳动", "服务", "技术", "其他"]


def contract_classify_node(state: AgentState):
    """
    合同分类节点函数: 调用 LLM 判断合同类型, 写入 state["contract_type"]。

    作用:
        读取合同全文(仅前 3000 字), 调用 LLM 将其归类到 CONTRACT_TYPES 中的某一类型,
        输出中文类型名称(如"买卖"/"租赁")。该类型会影响后续 AI 审核、合规审查的 prompt 与法规选择。
        若文档为空或非合同文本(由 is_contract_input 标记), 则写空字符串
        (不编造); 真实但无法归类的合同仍降级为 "其他"。

    参数:
        state (AgentState): LangGraph 共享状态字典。读取字段:
                            - doc_text (str, 可选): 合同全文(取前 3000 字)
                            写入字段:
                            - contract_type (str): 合同类型中文名(如 "买卖"/"租赁"/"其他")

    返回值:
        AgentState: 更新后的状态字典, 必含 "contract_type" 字段。

    可迁移性说明:
        本节点的"LLM 分类 + 白名单校验 + 子串匹配纠正"模式可迁移到任何文本分类场景,
        例如: 工单分类(咨询/投诉/建议)、邮件分类(垃圾/正常/重要)、商品类目预测等。
        只需修改 CONTRACT_TYPES 常量和 prompt 描述即可适配新业务。
        建议保留 "截取前 N 字" 的做法以控制 LLM 成本。
    """
    # 打印节点开始日志
    print("开始合同分类")

    # 从状态字典中取出合同全文, 并切片取前 3000 字符
    # [:3000] 是为了控制传给 LLM 的文本长度, 避免超出 token 限制或产生过高费用
    # 合同类型通常在标题或开头部分即可识别, 3000 字已足够覆盖
    doc_text = state.get("doc_text", "")[:3000]

    # 若文档为空(去除空白后), 直接判定为空并返回, 避免无意义调用 LLM
    if not doc_text.strip():
        state["contract_type"] = ""
        return state

    # 非合同文本(如合规业务描述 / 闲聊): 不允许瞎编合同类型, 直接写空
    # —— 由 text_recognize 在文本路径判定并写入 is_contract_input;
    #    文档路径未判定时默认 True(信任用户上传的文档)。
    if not state.get("is_contract_input", True):
        print("  [合同分类] 非合同文本 → 写空(不瞎编)")
        state["contract_type"] = ""
        return state

    # 构造分类 prompt, 使用 f-string 嵌入合同类型列表与文档内容
    # prompt 设计要点:
    #   (1) 强约束"只能从以下选项中选择一个", 避免输出歧义;
    #   (2) 提供完整候选列表(用 ", ".join 拼接成逗号分隔字符串);
    #   (3) 明示输出格式("只输出类型名称, 不要解释"), 简化后续解析;
    #   (4) 提供示例("如: 买卖"), 利用 in-context learning 提升准确率
    prompt = f"""请判断以下合同文本的合同类型, 只能从以下选项中选择一个:
{", ".join(CONTRACT_TYPES)}

合同文本(前3000字):
{doc_text}

只输出类型名称, 不要解释。如: 买卖"""

    # 使用 try-except 包裹 LLM 调用, 防止服务异常导致流程中断
    try:
        # 调用 LLM 进行分类, 传入 HumanMessage 列表
        # resp.content 是 LLM 返回的文本(理论上应是某个合同类型名称)
        resp = my_llm.invoke([HumanMessage(content=prompt)])

        # 取 LLM 输出文本并去除首尾空白
        ct = resp.content.strip()

        # 校验阶段: 检查 LLM 输出是否命中 CONTRACT_TYPES 中的某个类型
        # matched 用于记录匹配到的类型, 初始为 None
        matched = None

        # 遍历所有候选类型, 用 "in" 检查类型名是否作为子串出现在 LLM 输出中
        # 子串匹配比精确匹配更宽容, 能处理 "买卖合同" / "这是买卖类型" 等带上下文的输出
        for t in CONTRACT_TYPES:
            if t in ct:
                matched = t  # 命中则记录并跳出循环
                break

        # 将匹配结果写入状态; 若未匹配到任何类型, 则使用 "其他" 兜底
        # "matched or '其他'" 是 Python 短路求值: matched 为 None/空 时取后者
        state["contract_type"] = matched or "其他"
    # 捕获所有异常(网络/限流/模型错误等), 保证流程继续
    except Exception as e:
        # 打印警告日志
        print(f"⚠️ 合同分类失败: {e}")
        # 异常时降级为 "其他"
        state["contract_type"] = "其他"

    # 打印节点完成日志, 显示分类结果
    print(f"完成合同分类: {state.get('contract_type')}")

    # 返回更新后的状态字典
    return state


# 模块自测入口: 直接运行本文件时执行, 验证合同分类逻辑
if __name__ == "__main__":
    # 构造测试状态: 提供一段租赁合同文本作为 doc_text
    s = AgentState(doc_text="甲方将房屋出租给乙方使用, 月租金5000元")
    # 调用节点, 打印分类结果(应输出 "租赁")
    print(contract_classify_node(s).get("contract_type"))
