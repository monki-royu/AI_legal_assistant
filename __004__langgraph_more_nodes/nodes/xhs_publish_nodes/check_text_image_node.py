"""检查小红书标题/内容/图片是否完整"""
# ============================================================
# 文件名称: nodes/check_text_image_node.py
# 文件作用: 图文检查
# ============================================================
# 【这个文件是干什么的？】
# 图文检查
#
# 【代码逻辑主线】
# 参见各函数前的【功能】【参数】【返回值】【逻辑】说明。
#
# 【新手建议】
# 先看主函数 -> 再看辅助函数。
#

# 📜 代码文字逻辑解析
# 本文件是 AI 法律助理(LangGraph 多智能体协作)流程中的"小红书发布前检查节点",
# 借鉴自中医项目的 check_text_image_node 设计。它在文案生成与图片生成节点之后、
# 自动发布节点之前执行, 扮演"质量门禁"的角色, 确保发布所需的三要素(标题、正文、
# 图片)均已就绪。核心逻辑:1) 从 AgentState 中读取标题、正文、图片路径列表三个字段;
# 2) 依次检查三者是否为空, 若任一缺失, 则将 is_can_publish_xiaohongshu 置为 False,
# 并在 output 中写入对应的失败原因, 提前返回终止后续发布流程;3) 若三者均存在,
# 则将 is_can_publish_xiaohongshu 置为 True, 允许进入自动发布节点。该节点是典型的
# "前置条件校验节点"实现, 不调用任何 LLM 或外部服务, 纯粹基于 state 字段做判定,
# 可作为任何"发布前校验/门控"场景的迁移模板。
# 导入 AgentState 类型, 它是整个 LangGraph 图中各节点共享的状态字典(TypedDict)
from __004__langgraph_more_nodes.agent_state import AgentState


def check_text_image_node(state: AgentState):
    """检查是否可以发布小红书"""
    # 从 state 中读取小红书标题, 缺失时为空字符串
    title = state.get("xiaohongshu_title", "")
    # 从 state 中读取小红书正文内容, 缺失时为空字符串
    content = state.get("xiaohongshu_content", "")
    # 从 state 中读取图片路径列表, 缺失时为空列表
    image_path_list = state.get("xiaohongshu_image_path_list", [])

    # 检查 1: 标题是否缺失
    if not title:
        # 标题缺失, 标记为不可发布
        state["is_can_publish_xiaohongshu"] = False
        # 在 output 中写入失败原因, 供前端或日志展示
        state["output"] = "发布小红书失败，标题缺失！"
        # 提前返回, 跳过后续检查
        return state
    # 检查 2: 正文是否缺失
    if not content:
        # 正文缺失, 标记为不可发布
        state["is_can_publish_xiaohongshu"] = False
        # 在 output 中写入失败原因
        state["output"] = "发布小红书失败，内容缺失！"
        # 提前返回
        return state
    # 检查 3: 图片列表是否为空
    if not image_path_list:
        # 图片缺失, 标记为不可发布
        state["is_can_publish_xiaohongshu"] = False
        # 在 output 中写入失败原因
        state["output"] = "发布小红书失败，图片缺失！"
        # 提前返回
        return state

    # 三项检查均通过, 标记为可发布
    state["is_can_publish_xiaohongshu"] = True
    # 返回更新后的 state, 供 LangGraph 继续流转(后续条件边据此决定是否进入发布节点)
    return state


# 脚本直接运行时的自测入口
if __name__ == '__main__':
    # 构造一个包含完整标题、正文与图片列表的测试 state(此处用普通 dict 模拟)
    state = {
        "xiaohongshu_title": "测试标题",
        "xiaohongshu_content": "测试内容",
        "xiaohongshu_image_path_list": ["image1.png"],
    }
    # 调用节点并打印返回的 state, 用于人工验证检查逻辑
    print(check_text_image_node(state))
