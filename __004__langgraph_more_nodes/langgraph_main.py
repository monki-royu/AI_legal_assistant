"""LangGraph 主图构建与编译 ── 两级路由 + 6 子图组合架构

【当前版本 v5.0 架构演进 ── 详见下方【架构总览】与各路径详解】

本文件是 AI 法律助理(LangGraph 多智能体系统)的核心组装器，将 6 个独立编译的
子图按"四大任务类别"组合为主图。

职责：
  1. 导入全部子图 (preprocess / retrieval / dual_review / qa / docgen / xhs)
  2. 创建 StateGraph 构建器
  3. 注册两级路由节点 (Level 1 小红书 / Level 2 四类任务)
  4. 定义边与条件路由 (四大路径 → 子图组合复用)
  5. 编译生成最终可调用的 compiled graph 对象

【架构总览】

  ┌───────────────────────────────────────────────────────────────────┐
  │                        START                                      │
  │                          │                                        │
  │                          ▼                                        │
  │            【Level 1 一级路由】                                    │
  │        xiaohongshu_publish_intent                                │
  │          (小红书发布意图? is_xiaohongshu_publish_intent)           │
  │           ╱                    ╲                                  │
  │         True                  False                               │
  │          ╱                      ╲                                 │
  │  ┌─ xhs_subgraph        intent_router_node                        │
  │  │  (小红书发布子图)      (Level 2: 写入 task_type)                 │
  │  │  text→image→check     │                                        │
  │  │  →publish→markdown    ▼                                        │
  │  │                   【Level 2 二级路由】                          │
  │  │                   level2_router (second_intent_router)         │
  │  │                    ╱      │      ╲        ╲                    │
  │  └── END            ▼       ▼       ▼        ▼                    │
  │               contract_  检索路径  QA路径    文书生成路径                     │
  │               compliance                                          │
  │                  │        │       │        │                      │
  │                  ▼        ▼       ▼        ▼                      │
  │         input_source  r_retrieval  qa_subgraph   docgen_subgraph  │
  │         _router     _subgraph   (嵌套         (7节点+法条               │
  │         (输入分流)    (10节点)   retrieval)    校验回边)                    │
  │          ╱      ╲       │       │        │                        │
  │   有上传文档  纯文本       ▼       ▼        ▼                             │
  │    ╱          ╲     retrieval_  END       END                     │
  │ doc_extract  text_recognize _summarize                            │
  │    │          │(合规pass/合同判是否合同)                                   │
  │ doc_empty_    │(非合同+合同审核→END)                                     │
  │  guard        ▼                                                   │
  │   ╱   ╲    preprocess_subgraph(5节点文档预处理)                          │
  │ 空   非空      │                                                     │
  │  │     │      ▼                                                   │
  │ END    ▼      cc_retrieval(检索复用)                                  │
  │       _subgraph   │                                               │
  │          ▼        ▼                                               │
  │   dual_review_    END                                             │
  │   subgraph(双审fan-out/合规单链)                                        │
  │      │                                                            │
  │      ▼                                                            │
  │     END                                                           │

  【四大路径详解】 (各路径节点/边定义见下方详解与源码)

  ① 合同合规路径 (contract_review / compliance_review)
     intent_router → input_source_router (分流: 文档/文本)
                   → 文档路径: doc_extract → doc_empty_guard(空/损坏守卫)
                   → 文本路径: text_recognize(是否合同相关 + 归一化为 doc_text)
                   → preprocess_subgraph (5 节点文档预处理)
                   → retrieval_subgraph (检索复用)
                   → dual_review_subgraph (合同双审 fan-out / 合规单链)
                   → END

  ② 独立检索路径 (legal_research / case_search)
     intent_router → retrieval_subgraph
         └─ 内部: retrieval_intent_decompose 按 task_type 挂载知识源
            · case_search 挂 cases+laws → 单源直查跳过跨源融合
            · legal_research 多源     → 权威加权线性融合排序
            · 出口节点 (retrieval_output_pack) 打包 bundle + 产出 output markdown
                   → END

  ③ 法律问答路径 (legal_qa)
     intent_router → qa_subgraph
         └─ 内部三级路由: qa_intent_classify (先判是否法律问题)
                         → 法律相关 → (嵌套 retrieval_subgraph) → final_answer
                         → 非法律   → llm_direct_out
     → END

  ④ 文书生成路径 (legal_document_gen)
     intent_router → docgen_subgraph (V3: 5节点线性链路)
                    case_analyze → template_match → clause_fill(纯LLM)
                    → risk_advisor(内部调 law_search+case_search 子图 + 质量门控)
                    → final_delivery(含强制交付模式)
     → END

  ⑤ 小红书发布路径 (xiaohongshu)
     Level 1 判定为小红书意图后:
     xhs_subgraph (文案→图片→合规检查→自动发布→markdown)
     → END

  【子图复用 (subgraph composition)】
    - retrieval_subgraph 被复用 3 次:
      ① 合同合规路径 (cc_retrieval)
      ② 独立检索路径 (r_retrieval)
      ③ QA 子图内部嵌套 (qa_retrieval)
    - 全部 6 个子图均在 subgraphs/ 目录独立编译, 主图仅做组合.
"""

import sys
import os
import uuid

# 将项目根目录插入 sys.path，解决直接运行时找不到包的问题
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ============================================================
# 【CLI 子进程模式 stdout 保护 —— 必须位于所有子图 import 之前】
#
# 子图模块 (subgraphs/*.py) 在 import 期就会执行 output_pic_graph
# 向 stdout 打印"正在生成流程图..."。CLI 模式下 stdout 是调用方
# (_run_backend_isolated) 的 JSON 专用通道, 这些 import 期打印会
# 污染 JSON 导致解析失败 —— 因此在首个子图 import 之前就把 stdout
# 重定向到 stderr, 真实 stdout 保存到 _CLI_REAL_STDOUT 供最终
# JSON 输出使用 (见文件尾部 __main__ 的 _cli_main)。
# ============================================================
_CLI_REAL_STDOUT = sys.stdout
if __name__ == "__main__" and "--input_file" in sys.argv:
    sys.stdout = sys.stderr

from langgraph.graph import StateGraph, END

# 状态定义
from __004__langgraph_more_nodes.agent_state import AgentState

# 可视化与路径工具
import subprocess
from common.path_utils import get_file_path
from common.ouput_graph_utils import output_pic_graph

# ── 两级路由节点 ──
from __004__langgraph_more_nodes.nodes.xhs_publish_nodes.xiaohongshu_publish_intent_node import (
    xiaohongshu_publish_intent_node,
)
from __004__langgraph_more_nodes.nodes.intent_router_node import (
    intent_router_node,
    LEVEL2_PATH_MAP,
)

# ── 合同合规链路·主图层单节点 (输入分流 + 文档提取 + 双守卫) ──
from __004__langgraph_more_nodes.nodes.preprocess_nodes.input_source_router_node import (
    input_source_router,
)
from __004__langgraph_more_nodes.nodes.preprocess_nodes.doc_extract_node import (
    doc_extract_node,
)
from __004__langgraph_more_nodes.nodes.preprocess_nodes.doc_empty_guard_node import (
    doc_empty_guard_node,
)
from __004__langgraph_more_nodes.nodes.preprocess_nodes.text_recognize_node import (
    text_recognize_node,
)

# ── 主图层收尾节点 (未封装进子图的单节点) ──
# 注: 原 retrieval_result_summarize_node 已于 2026-08-23 并入检索子图出口节点
# (retrieval_output_pack_node), 独立检索路径的 output 由子图出口直接产出。

# ── 6 个独立编译子图 (subgraphs/) ──
from __004__langgraph_more_nodes.subgraphs.preprocess_subgraph import (
    preprocess_subgraph,
)
from __004__langgraph_more_nodes.subgraphs.retrieval_subgraph import (
    build_retrieval_subgraph,
)
from __004__langgraph_more_nodes.subgraphs.dual_review_subgraph import (
    dual_review_subgraph,
)
from __004__langgraph_more_nodes.subgraphs.qa_subgraph import (
    build_qa_subgraph,
)
from __004__langgraph_more_nodes.subgraphs.docgen_subgraph import (
    docgen_subgraph,
)
from __004__langgraph_more_nodes.subgraphs.xhs_subgraph import (
    xhs_subgraph,
)

# ── Checkpointer (状态持久化, 支持断点续跑 / 多轮会话) ──
# 【2026-08-23】优先使用 SqliteSaver(磁盘持久化): 子进程模式(_run_backend_isolated)
# 的请求触发 interrupt 后, 主进程 resume 需要从磁盘读到子进程写入的 checkpoint;
# MemorySaver 是进程内内存, 子进程退出即销毁, 无法跨进程恢复。
# 降级链: SqliteSaver → MemorySaver → 无状态。
try:
    from langgraph.checkpoint.sqlite import SqliteSaver
    import sqlite3
    from common.path_utils import get_file_path

    # 直接构造 SqliteSaver(sqlite3.Connection): 生命周期可控, 模块级全局可用
    # 存储路径固定在项目根下, 供主进程/子进程共享同一份 checkpoint 数据
    _conn = sqlite3.connect(get_file_path("checkpoints.sqlite"), check_same_thread=False)
    _checkpointer = SqliteSaver(_conn)
    _HAS_CHECKPOINTER = True
except Exception:  # 环境不支持时降级为无状态
    _checkpointer = None
    _HAS_CHECKPOINTER = False
    # 兜底: 若 sqlite 不可用但内存可用, 降级 MemorySaver
    try:
        from langgraph.checkpoint.memory import MemorySaver

        _checkpointer = MemorySaver()
        _HAS_CHECKPOINTER = True
    except Exception:
        _checkpointer = None
        _HAS_CHECKPOINTER = False


# ==============================================================================
# 【条件路由函数】
# ==============================================================================


def after_xiaohongshu_intent_router(state: AgentState):
    """【Level 1 路由】小红书发布意图判定

    读取:
        - is_xiaohongshu_publish_intent (bool): xiaohongshu_publish_intent 写入
        - task_type (str): 兼容前端直接指定 task_type="xiaohongshu_publish" 的场景

    返回:
        "xhs":          进入小红书发布子图
        "intent_router": 进入二级路由 (四大任务分类)
    """
    if state.get("is_xiaohongshu_publish_intent", False):
        return "xhs"
    if state.get("task_type") == "xiaohongshu_publish":
        return "xhs"
    return "intent_router"


def after_doc_empty_guard(state: AgentState):
    """【文档路径守卫出口路由】doc_empty_guard 判定空/损坏文档 → 直接 END

    读取:
        - doc_empty_flag (str): doc_empty_guard_node 写入 "pass"/"block"

    返回:
        "preprocess": 文档非空, 进预处理子图 → cc_retrieval → dual_review
        "end":        文档为空/损坏/解析失败, 直接结束(提示用户重传)
    """
    if state.get("doc_empty_flag") == "block":
        print("⛔ [主图] 文档为空/损坏 → 跳过预处理/检索/双审, 直接结束")
        return "end"
    return "preprocess"


def after_text_recognize(state: AgentState):
    """【文本路径识别出口路由】text_recognize 判定是否合同相关 → 分流

    读取:
        - text_recognize_flag (str): text_recognize_node 写入 "pass"/"block"

    返回:
        "preprocess": 文本已归一化为 doc_text, 进预处理子图 → cc_retrieval → dual_review
        "end":        非合同 + 合同审核, 直接结束(提示用户粘贴/上传合同)
    """
    if state.get("text_recognize_flag") == "block":
        print("⛔ [主图] 文本非合同 + 合同审核 → 跳过预处理/检索/双审, 直接结束")
        return "end"
    return "preprocess"


def level2_router(state: AgentState):
    """【Level 2 路由】根据 task_type 路由到 4 大路径 (架构图中的 second_intent_router)

    路径分组 (LEVEL2_PATH_MAP):
    - contract_compliance: contract_review / compliance_review → 合同合规路径 (input_source_router 输入分流 + 双守卫 + 预处理)
    - retrieval:           legal_research / case_search → 检索子图
      (检索子图内部: retrieval_intent_decompose 按 task_type 挂载知识源;
       case_search 挂 cases+laws → 单源直查跳过跨源融合)
    - legal_qa:            legal_qa → QA 子图 (内部先判法律相关性)
    - legal_document_gen:  legal_document_gen → 文书生成子图 (首节点案情分析)
    """
    task_type = state.get("task_type", "legal_qa")
    path = LEVEL2_PATH_MAP.get(task_type, "legal_qa")

    if path == "contract_compliance":
        return "input_source_router"       # → 输入分流(文档/文本) + 双守卫 + 预处理
    elif path == "retrieval":
        return "r_retrieval"                # → 检索子图 (内部按条件挂载源, 单源不融合)
    elif path == "legal_document_gen":
        return "docgen"                     # → 文书生成子图 (首节点 doc_case_analyze)
    else:
        return "qa"                         # legal_qa (含兜底) → QA 子图 (先判法律相关性)


# ==============================================================================
# 【图构建】
# ==============================================================================

# 1. 创建构建器
builder = StateGraph(AgentState)

# 2. 注册节点
# ── 两级路由节点 ──
builder.add_node("xiaohongshu_publish_intent", xiaohongshu_publish_intent_node)
builder.add_node("intent_router", intent_router_node)

# ── 子图节点 (subgraph composition) ──
# 小红书发布子图 (Level 1 命中后进入)
builder.add_node("xhs", xhs_subgraph)
# 合同合规路径: 输入分流 → (文档提取/文本识别 + 双守卫) → 预处理 → 检索复用 → 双审
builder.add_node("input_source_router", input_source_router)
builder.add_node("doc_extract", doc_extract_node)
builder.add_node("doc_empty_guard", doc_empty_guard_node)
builder.add_node("text_recognize", text_recognize_node)
builder.add_node("preprocess", preprocess_subgraph)
# retrieval_subgraph 复用两次: 使用独立编译实例避免状态串扰
# 检索子图内部有北大法宝/企查查 interrupt，必须传递 checkpointer
if _HAS_CHECKPOINTER:
    builder.add_node("cc_retrieval", build_retrieval_subgraph(_checkpointer))
    builder.add_node("r_retrieval", build_retrieval_subgraph(_checkpointer))
else:
    builder.add_node("cc_retrieval", build_retrieval_subgraph())
    builder.add_node("r_retrieval", build_retrieval_subgraph())
builder.add_node("dual_review", dual_review_subgraph)
# 法律问答子图 (内部嵌套 retrieval_subgraph)，传递 checkpointer 支持 interrupt
if _HAS_CHECKPOINTER:
    builder.add_node("qa", build_qa_subgraph(_checkpointer))
else:
    builder.add_node("qa", build_qa_subgraph())
# 文书生成子图
builder.add_node("docgen", docgen_subgraph)

# 3. 定义边与条件路由

# ── 入口: START → 一级路由 (小红书意图) ──
builder.set_entry_point("xiaohongshu_publish_intent")

# ── Level 1: 小红书 vs 非小红书 ──
builder.add_conditional_edges(
    "xiaohongshu_publish_intent",
    after_xiaohongshu_intent_router,
    {
        "xhs": "xhs",
        "intent_router": "intent_router",
    },
)
builder.add_edge("xhs", END)

# ── Level 2: 二级路由 → 四大路径子图 ──
builder.add_conditional_edges(
    "intent_router",
    level2_router,
    {
        "input_source_router": "input_source_router",  # 合同合规路径(输入分流)
        "r_retrieval": "r_retrieval",   # 独立检索路径
        "qa": "qa",                     # 法律问答路径
        "docgen": "docgen",             # 文书生成路径
    },
)

# ── ① 合同合规路径: 输入分流 → 文档提取/文本识别 + 双守卫 → 预处理 → 检索 → 双审 ──
# 入口分流: 有上传文档 → doc_extract; 纯文本 → text_recognize
builder.add_conditional_edges(
    "input_source_router",
    lambda s: "doc" if (s.get("uploaded_doc_path") and str(s.get("uploaded_doc_path")).strip()) else "text",
    {
        "doc": "doc_extract",
        "text": "text_recognize",
    },
)
# 文档路径: doc_extract → 空/损坏守卫 → (pass→预处理 | block→END)
builder.add_edge("doc_extract", "doc_empty_guard")
builder.add_conditional_edges(
    "doc_empty_guard",
    after_doc_empty_guard,
    {
        "preprocess": "preprocess",   # 文档非空: 进预处理子图
        "end": END,                   # 空/损坏: 直接返回提示文案
    },
)
# 文本路径: text_recognize → (pass→预处理 | block→END)
builder.add_conditional_edges(
    "text_recognize",
    after_text_recognize,
    {
        "preprocess": "preprocess",   # 文本已归一化为 doc_text: 进预处理子图
        "end": END,                   # 非合同+合同审核: 直接返回提示文案
    },
)
# 预处理子图 → 检索复用 → 双审 (固定边: 守卫已在主图层前置拦截)
builder.add_edge("preprocess", "cc_retrieval")
builder.add_edge("cc_retrieval", "dual_review")
builder.add_edge("dual_review", END)

# ── ② 独立检索路径: retrieval 子图出口直接产出 output, 出子图即 END ──
#   (原 retrieval_summarize 节点已并入检索子图出口 retrieval_output_pack_node)
builder.add_edge("r_retrieval", END)

# ── ③ 法律问答路径: QA 子图内部三级路由, 出口直接 END ──
builder.add_edge("qa", END)

# ── ④ 文书生成路径: risk_advisor 质量门控回边, 出口直接 END ──
builder.add_edge("docgen", END)


# 4. 编译 graph
# 注意: compile() 必须在所有节点添加和边定义完成之后调用
if _HAS_CHECKPOINTER:
    graph = builder.compile(checkpointer=_checkpointer)
else:
    graph = builder.compile()

# 主图流程图: 每次 import 自动重绘 (除非 LEGAL_DISABLE_PNG=1),
# 保证 graph.png 始终与当前架构一致, 不会像旧版那样陈旧(只在该脚本直接运行时才画)。
output_pic_graph(graph, get_file_path("__004__langgraph_more_nodes/graph.png"))


async def get_graph():
    """获取已编译的 graph 对象 (供外部 FastAPI / 脚本调用)"""
    return graph


def _default_config(config: dict = None) -> dict:
    """为 checkpointer 模式补齐默认 config (thread_id), 兼容无 config 调用"""
    if config:
        return config
    if _HAS_CHECKPOINTER:
        return {"configurable": {"thread_id": str(uuid.uuid4())}}
    return None


def _extract_pending_interrupt(result: dict, thread_id: str = None) -> dict:
    """把 invoke 结果中的 __interrupt__ 转为可序列化的 pending_interrupt 结构。

    图在节点 interrupt() 处暂停时(如 beida_fabao_gate_node 的付费确认),
    invoke 返回值含 "__interrupt__" 键 —— 值是 Interrupt 对象列表,
    不可 JSON 序列化, 直接回传 FastAPI/Streamlit 会报错。
    本函数提取每个 Interrupt 的 .value(payload), 拼成:
        {"pending_interrupt": {"thread_id": ..., "payloads": [...]}}
    前端据此识别"图在等用户确认"并展示确认弹窗, 再调 legal_response_resume 恢复。
    无中断时返回空 dict(不污染结果)。
    """
    if not isinstance(result, dict):
        return {}
    interrupts = result.get("__interrupt__")
    if not interrupts:
        return {}
    payloads = []
    for it in interrupts:
        try:
            payloads.append(it.value)          # Interrupt.value = interrupt(payload) 的入参
        except Exception:
            payloads.append(str(it))           # 兜底: 非 Interrupt 对象转字符串
    return {"pending_interrupt": {"thread_id": thread_id, "payloads": payloads}}


def legal_response_sync(input_text: str, task_type: str = None,
                        thread_id: str = None, **kwargs):
    """同步调用接口 - 供 Streamlit 前端使用

    参数:
        input_text: 用户输入文本
        task_type: 可选，直接指定任务类型(跳过意图识别)
        thread_id: 可选，会话线程 id —— 续跑/多轮场景传入同一 id 复用 checkpointer
                   状态; 缺省时新生成并在返回值中回传(供中断 resume 使用)
        **kwargs: 其他 AgentState 字段 (如 doc_text, contract_type, credit_confirmed 等)

    返回:
        dict: 包含 output, task_type, citations, risk_items 等字段的结果;
              图在 interrupt() 暂停时额外含:
              - thread_id (str): 本次会话线程 id(调用方需保存, resume 时传入)
              - pending_interrupt (dict): {"thread_id":..., "payloads":[...]}
                (如北大法宝付费确认 payload: type/quality_score/query/message)
    """
    # 构建初始状态
    init_state = {"input": input_text}
    if task_type:
        init_state["task_type"] = task_type
    # 合并额外参数
    for k, v in kwargs.items():
        if v is not None:
            init_state[k] = v

    # thread_id: 外部传入(续跑)或新生成 —— 必须回传给调用方以便 resume 中断的图
    tid = thread_id or (str(uuid.uuid4()) if _HAS_CHECKPOINTER else None)
    if tid and _HAS_CHECKPOINTER:
        config = {"configurable": {"thread_id": tid}}
    else:
        config = _default_config()

    # 调用 graph
    result = graph.invoke(init_state, config=config)

    # 中断检测: 图在 interrupt() 处暂停(如北大法宝付费确认)时提取可序列化结构
    result.update(_extract_pending_interrupt(result, tid))
    if tid:
        result.setdefault("thread_id", tid)
    return result


def legal_response_resume(thread_id: str, resume_value=None):
    """恢复因 interrupt() 暂停的图执行(北大法宝付费确认的用户决策回填)。

    参数:
        thread_id: 首次 legal_response_sync 返回的会话线程 id
                   (checkpointer 已保存暂停点状态, 同一 thread_id 才能恢复)
        resume_value: 用户决策, 由 beida_fabao_gate_node 解析:
            - True:  确认调用付费北大法宝 MCP
            - False / None: 拒绝, 返回现有免费检索结果
            - str:   编辑后的检索查询(视为确认, 用新查询调付费接口)

    返回:
        dict: resume 后继续执行到 END 的完整结果(含 output/citations 等);
              可能再次包含 pending_interrupt(链式中断场景);
              环境不支持/恢复失败时返回 {"error": ...}。
    """
    if not _HAS_CHECKPOINTER:
        return {"error": "当前环境未启用 checkpointer, 不支持 interrupt/resume"}
    try:
        from langgraph.types import Command
    except Exception:
        return {"error": "当前 langgraph 版本不支持 Command(resume=), 请升级 langgraph"}
    if not thread_id:
        return {"error": "缺少 thread_id, 无法定位待恢复的会话线程"}

    config = {"configurable": {"thread_id": thread_id}}
    try:
        result = graph.invoke(Command(resume=resume_value), config=config)
        result.update(_extract_pending_interrupt(result, thread_id))
        return result
    except Exception as e:
        print(f"⚠️ [legal_response_resume] resume 失败: {e}")
        return {"error": f"resume 失败: {e}"}


def legal_response_stream(input_text: str, task_type: str = None, config: dict = None, **kwargs):
    """流式调用接口 - 供 Streamlit 前端使用

    参数:
        input_text: 用户输入文本
        task_type: 可选，直接指定任务类型
        config: 可选，LangGraph config (如 thread_id)
        **kwargs: 其他 AgentState 字段

    生成:
        逐节点状态字典，yield 给前端流式展示
    """
    init_state = {"input": input_text}
    if task_type:
        init_state["task_type"] = task_type
    for k, v in kwargs.items():
        if v is not None:
            init_state[k] = v

    for chunk in graph.stream(init_state, config=_default_config(config)):
        yield chunk


def stream_graph(input_text: str, task_type: str = None, config: dict = None):
    """流式执行 graph, 返回逐节点输出 (供前端流式展示思考过程)"""
    initial_state = {"input": input_text}
    if task_type:
        initial_state["task_type"] = task_type
    return graph.stream(initial_state, config=_default_config(config))



# 脚本直接运行时的入口
if __name__ == "__main__":

    # ============================================================
    # 【CLI 子进程模式】供 __006__streamlit/app.py 的 _run_backend_isolated 调用
    #
    # 调用契约:
    #   python -u -m __004__langgraph_more_nodes.langgraph_main \
    #       --input_file <临时文件路径> --task_type <任务类型> --mode full
    #
    # 输出契约:
    #   - stdout 只承载**单行 JSON** (legal_response_sync 完整结构化结果);
    #     执行期间节点/本模块的所有 print 一律重定向到 stderr。
    #   - 执行异常 → stdout 输出 {"__cli_error__": "..."} 且退出码 1
    #     (调用方对 __cli_error__ 双保险再判一次并回退 demo)。
    #   - 中断说明: 调用方已注入 LEGAL_DISABLE_INTERRUPT=1 (子进程退出后
    #     MemorySaver 状态销毁、无法 resume), 付费门禁按"拒绝"处理,
    #     保证子进程总能跑到 END 输出完整报告。
    # ============================================================
    if "--input_file" in sys.argv:
        import json as _json

        def _cli_main():
            import argparse
            parser = argparse.ArgumentParser(
                description="AI 法律助理子进程 CLI 入口 (供 Streamlit 隔离调用)"
            )
            parser.add_argument(
                "--input_file", required=True,
                help="UTF-8 文本文件路径, 内容为用户输入全文",
            )
            parser.add_argument(
                "--task_type", default=None,
                help="任务类型 (contract_review/compliance_review/case_search/"
                     "legal_document_gen/...); 缺省 = 自动意图识别",
            )
            parser.add_argument(
                "--mode", default="full", choices=["full"],
                help="输出模式; 目前仅 full (完整结构化 JSON)",
            )
            args = parser.parse_args()

            # stdout 专用于最终 JSON: 模块顶部已把 stdout 重定向到 stderr
            # (拦住子图 import 期的流程图打印), 这里沿用该状态执行
            # 文件读取 + 图调用, 结束后切回真实 stdout 输出 JSON
            sys.stdout = sys.stderr
            try:
                # 读取用户输入 (长合同文本经临时文件传递, 避免 shell 转义问题)
                # —— 读取也在 try 内: 文件缺失/编码错误同样走 __cli_error__ 封装
                with open(args.input_file, "r", encoding="utf-8") as f:
                    input_text = f.read()
                result = legal_response_sync(input_text, task_type=args.task_type)
                err = None
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                import traceback
                traceback.print_exc()
            finally:
                sys.stdout = _CLI_REAL_STDOUT

            if err is not None:
                print(_json.dumps({"__cli_error__": err}, ensure_ascii=False))
                sys.exit(1)

            # __interrupt__ 键持有 Interrupt 对象(不可 JSON 序列化);
            # 可序列化 payload 已由 legal_response_sync 提取进 pending_interrupt,
            # 这里剥离原始对象作为双保险 (default=str 兜底之外的第二道防线)
            result.pop("__interrupt__", None)

            # default=str: 兜底序列化意外类型 (如 numpy 标量/自定义对象)
            print(_json.dumps(result, ensure_ascii=False, default=str))
            sys.exit(0)

        _cli_main()

    # ============================================================
    # 【交互测试模式】无 CLI 参数时运行 5 个固定测试用例
    # ============================================================
    print("=" * 60)
    print("【LangGraph 法律AI助理 v5.2 - 两级路由 + 6子图组合架构】")
    print(f"  checkpointer: {'启用 (' + type(_checkpointer).__name__ + ')' if _HAS_CHECKPOINTER else '未启用 (降级无状态)'}")
    print("=" * 60)

    def main():
        """同步主流程: 统一使用 graph.invoke 执行

        【为什么同步】2026-08-23 起 checkpointer 改为 SqliteSaver(同步版),
        graph.ainvoke 会触发其 aget_tuple -> NotImplementedError(仅支持同步方法)。
        本检查点先前的异步路径(graph.ainvoke)正是为此报错。同步路径对节点无要求:
        - 小红书发布链路本用 async 节点(xiaohongshu_auto_publish_node), 但其同步包装器
          xiaohongshu_auto_publish_node_sync 已在 xhs_subgraph.py 注册, 同步 invoke 兼容。
        - 其余节点全部同步。
        服务主路径(legal_response_sync/legal_response_resume)本就同步 invoke, 不受影响。
        """

        # 测试: 合同审核 (合同合规路径 → 文本路径 → preprocess → retrieval → dual_review)
        print("\n📋 测试1: 合同审核 (双审模式)")
        s1_input = (
            "房屋租赁合同\n甲方：张三\n乙方：李四\n"
            "第一条 甲方将位于北京市朝阳区某小区301室出租给乙方，建筑面积90平方米。\n"
            "第二条 租赁期限12个月，自2024年1月1日起至2024年12月31日止。\n"
            "第三条 月租金5000元，押金10000元。\n"
            "第四条 违约责任：乙方逾期支付租金的，每日按月租金5%支付违约金。"
        )
        s1 = AgentState(input=s1_input, task_type="contract_review")
        out1 = graph.invoke(s1, config=_default_config())
        print(f"  输出: {str(out1.get('output', ''))[:200]}...")

        # 测试: 合规审查 (合同合规路径 → 单审)
        print("\n📋 测试2: 合规审查 (单审模式)")
        s2 = AgentState(input="检查我的数据处理行为是否合规", task_type="compliance_review")
        out2 = graph.invoke(s2, config=_default_config())
        print(f"  输出: {str(out2.get('output', ''))[:200]}...")

        # 测试: 法律问答 (法律相关 → 检索路径)
        print("\n📋 测试3: 法律问答 (法律相关 → 检索路径)")
        s3 = AgentState(input="民法典第585条违约金是怎么规定的？", task_type="legal_qa")
        out3 = graph.invoke(s3, config=_default_config())
        print(f"  输出: {str(out3.get('output', ''))[:200]}...")

        # 测试: 法律问答 (非法律相关 → LLM直接回答)
        print("\n📋 测试4: 法律问答 (非法律相关 → LLM直接回答)")
        s4 = AgentState(input="你好，今天天气怎么样？", task_type="legal_qa")
        out4 = graph.invoke(s4, config=_default_config())
        print(f"  输出: {str(out4.get('output', ''))[:200]}...")

        # 测试: 小红书发布
        print("\n📋 测试5: 小红书发布")
        s5 = AgentState(input="帮我生成一篇关于租房攻略的小红书内容", task_type="xiaohongshu_publish")
        out5 = graph.invoke(s5, config=_default_config())
        print(f"  输出: {str(out5.get('output', ''))[:200]}...")

        print("\n✅ 所有测试完成!")

    main()

    # 可视化 LangGraph 流程图 (graph.png 已在模块 import 期绘制, 此处无需重复)
