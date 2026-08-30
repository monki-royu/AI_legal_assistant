"""小红书发布子图 (XHS Publish Subgraph)

【架构定位】
    本子图是小红书发布链路 (xiaohongshu_publish) 的【独立处理单元】,
    一级路由判定为小红书意图后进入. 完全独立于法律推理栈, 封装
    Playwright 自动化 + 图文生成 + 合规检查.

【节点组成】(5 节点 + 1 条件终止)
    text_generate → image_generate → check_text_image
        → [pass] → xiaohongshu_auto_publish → generate_markdown → END
        → [fail] → END  (不发布直接优雅终止)

【关键设计】
    check_text_image 节点判定图文是否可发布, 不通过直接路由 END
    (path_map 的 value 可为 END 常量, 框架直接终止子图)
    这样失败不会污染主图状态, 也无需额外终止节点.
"""
from langgraph.graph import StateGraph, END

from __004__langgraph_more_nodes.agent_state import AgentState
from __004__langgraph_more_nodes.nodes.xhs_publish_nodes.text_generate_node import text_generate_node
from __004__langgraph_more_nodes.nodes.xhs_publish_nodes.image_generate_node import image_generator_node
from __004__langgraph_more_nodes.nodes.xhs_publish_nodes.check_text_image_node import check_text_image_node
from __004__langgraph_more_nodes.nodes.xhs_publish_nodes.xhs_auto_publish_node import (
    xiaohongshu_auto_publish_node_sync as xiaohongshu_auto_publish_node,
)
from __004__langgraph_more_nodes.nodes.xhs_publish_nodes.generate_markdown_node import generate_markdown_node
from common.ouput_graph_utils import output_pic_graph
from common.path_utils import get_file_path


def _check_text_image_router(state: AgentState) -> str:
    """【小红书子图内部路由】图文合规检查

    读取:
        - is_can_publish_xiaohongshu (bool): check_text_image 写入的可发布标志

    返回:
        "pass": 进入自动发布
        "fail": 直接退出子图 (END), 不污染主图
    """
    if state.get("is_can_publish_xiaohongshu", False):
        return "pass"
    return "fail"


def build_xhs_subgraph():
    """构建并编译小红书发布子图

    内部 5 节点:
        text → image → check → [pass: publish → markdown → END
                              | fail: END]

    返回:
        CompiledStateGraph
    """
    builder = StateGraph(AgentState)

    # 5 节点注册
    builder.add_node("xhs_text_generate", text_generate_node)
    builder.add_node("xhs_image_generate", image_generator_node)
    builder.add_node("xhs_check_text_image", check_text_image_node)
    builder.add_node("xhs_auto_publish", xiaohongshu_auto_publish_node)
    builder.add_node("xhs_generate_markdown", generate_markdown_node)

    # 入口
    builder.set_entry_point("xhs_text_generate")

    # 主链
    builder.add_edge("xhs_text_generate", "xhs_image_generate")
    builder.add_edge("xhs_image_generate", "xhs_check_text_image")

    # 图文检查条件边
    builder.add_conditional_edges(
        "xhs_check_text_image",
        _check_text_image_router,
        {
            "pass": "xhs_auto_publish",
            "fail": END,  # 不通过直接终止, 不污染主图
        },
    )

    # 通过后主链
    builder.add_edge("xhs_auto_publish", "xhs_generate_markdown")
    builder.add_edge("xhs_generate_markdown", END)

    return builder.compile()


# 默认实例
xhs_subgraph = build_xhs_subgraph()
output_pic_graph(xhs_subgraph, get_file_path("__004__langgraph_more_nodes/xhs_subgraph.png"))