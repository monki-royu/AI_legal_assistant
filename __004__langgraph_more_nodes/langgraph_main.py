"""
法智引擎 LangGraph 主编排
对应中医项目 langgraph_more_nodes.py

架构(XMind主图):
  START -> N1意图路由
    ├─ contract_review(合同审核):
    │   N2文档提取 -> N3合同分类 -> N4条款切分 -> N5a合同审核AI
    │   -> N5b合规审查 -> N5c数值校验 -> N6法律检索 -> N7风险聚合
    │   -> N8甲乙方识别 -> N9最终交付 -> END
    ├─ compliance_review(合规审查):
    │   N2文档提取 -> N5b合规审查 -> N5c数值校验 -> N6法律检索
    │   -> N7风险聚合 -> N9最终交付 -> END
    ├─ legal_research(法律检索):
    │   N6法律检索 -> N9最终交付 -> END
    ├─ legal_qa(法律问答):
    │   实体抽取 -> Neo4j匹配 -> Cypher生成/检查/执行 -> 答案生成 -> END
    ├─ xiaohongshu(小红书发布):
    │   文案生成 -> 图片生成 -> 检查 -> 发布 -> Markdown -> END
    └─ other(其他):
        LLM直接回答 -> END

说明:
  - 并行N5a/N5b在Python3.8轻量StateGraph中用串行模拟
  - Neo4j/FAISS不可用时优雅降级为LLM伪检索
  - 小红书发布需要playwright+登录cookie
"""
# 📜 代码文字逻辑解析
# 本文件是法智引擎(AI法律助理)基于 LangGraph 框架的多智能体编排核心入口。
# 其核心职责是:1)注册所有业务节点函数;2)定义节点之间的边(普通边和条件边);3)编译生成可调用的图;
# 4)对外暴露多个调用接口(同步/异步/流式/完整状态)供前端或上游服务调用。
# 整个图从 START 出发,首先进入小红书意图识别节点做"前置过滤":若用户想发小红书则走小红书发布链路
# (文案生成->图片生成->检查->发布->Markdown),否则进入主意图路由(intent_router_node)。
# 主路由根据 task_type 将流程分流到六条业务链路:合同审核(N2-N9全流程)、合规审查(精简流程)、
# 法律检索、法律问答(知识图谱RAG:实体抽取->Neo4j匹配->Cypher生成/校验/执行->答案生成)、
# 小红书发布、通用兜底(LLM直接回答)。条件路由函数(如 intent_router_router、check_cypher_router 等)
# 根据状态字段返回不同的下一跳,实现动态分支与重试控制(如 Cypher 校验失败重试最多3次)。
# 本文件还提供 legal_response/ legal_response_sync/ legal_response_full/ legal_response_stream 四个
# 对外接口,分别支持异步调用、同步纯文本、完整结构化数据、流式输出四种使用场景,适配不同前端需求。
# 在模块加载时即构建并编译图(graph = build_graph()),并尝试生成可视化流程图(graph.png)便于调试。
# asyncio 标准库:提供异步IO支持,本文件中 legal_response/ legal_response_stream 使用 async/await 语法,
# 配合 graph.ainvoke 异步执行图,避免阻塞主线程(尤其重要于 Web 服务场景下高并发请求)
import asyncio

# 使用兼容层(Python 3.8环境)
# 从 common.langgraph_compat 兼容层导入 StateGraph 类与 START/END 常量
# 兼容层的作用:在 Python 3.8 环境(无法安装官方 langgraph 最新版)下,提供与官方 API 一致的轻量实现,
# 使上层代码无需关心运行环境差异。StateGraph 是构建状态图的核心类,START/END 是图入口与出口的哨兵常量
from common.langgraph_compat import StateGraph, START, END

# 法智引擎核心节点
# 以下导入各业务节点函数,每个函数签名为 (state: AgentState) -> AgentState(部分返回字典增量)
# 节点函数通过 __name__ 属性作为图中的唯一标识注册,确保名称与函数绑定的可追溯性
# 意图路由节点:基于用户输入做意图分类,输出 task_type 字段,是主路由的判定依据
from __004__langgraph_more_nodes.nodes.intent_router_node import intent_router_node
# 文档提取节点:读取 uploaded_doc_path 并解析为纯文本写入 doc_text
from __004__langgraph_more_nodes.nodes.doc_extract_node import doc_extract_node
# 合同分类节点:基于 doc_text 判定合同类型(买卖/租赁/借贷等),写入 contract_type
from __004__langgraph_more_nodes.nodes.contract_classify_node import contract_classify_node
# 条款切分节点:将 doc_text 切分为结构化条款列表 doc_clauses
from __004__langgraph_more_nodes.nodes.clause_split_node import clause_split_node
# 数值抽取节点:从合同文本中抽取关键数值(单价/数量/总价等)写入 extracted_numerics
from __004__langgraph_more_nodes.nodes.numeric_extract_node import numeric_extract_node
# 合同审核AI节点:基于 LLM 对条款做风险审查,输出 contract_risk_items
from __004__langgraph_more_nodes.nodes.contract_ai_review_node import contract_ai_review_node
# 合规审查节点:对照法律法规检测合规性风险,输出 compliance_risk_items
from __004__langgraph_more_nodes.nodes.compliance_review_node import compliance_review_node
# 数值校验节点:校验数值一致性与合理性,输出 numeric_risk_items
from __004__langgraph_more_nodes.nodes.numeric_validate_node import numeric_validate_node
# 法律检索节点:通过 FAISS/Neo4j 检索相关法规,输出 research_context 与 citations
from __004__langgraph_more_nodes.nodes.legal_research_node import legal_research_node
# 风险聚合节点:合并三路风险项并计算 overall_risk_score 与 risk_level
from __004__langgraph_more_nodes.nodes.risk_aggregate_node import risk_aggregate_node
# 甲乙方识别节点:识别合同主体,写入 party_a/party_b/user_side
from __004__langgraph_more_nodes.nodes.party_identify_node import party_identify_node
# 最终交付节点:组装最终报告 markdown 与 output 文本
from __004__langgraph_more_nodes.nodes.final_delivery_node import final_delivery_node
# LLM直接输出节点:other 类型任务的兜底回答节点
from __004__langgraph_more_nodes.nodes.llm_direct_out_node import llm_direct_out_node

# 小红书发布节点
# 小红书意图识别节点:前置判断用户是否要发小红书,输出 is_xiaohongshu_publish_intent
from __004__langgraph_more_nodes.nodes.xiaohongshu_publish_intent_node import xiaohongshu_publish_intent_node
# 文案生成节点:生成小红书标题与正文,写入 xiaohongshu_title/xiaohongshu_content
from __004__langgraph_more_nodes.nodes.text_generate_node import text_generate_node
# 图片生成节点:生成配图,写入 xiaohongshu_image_path_list
from __004__langgraph_more_nodes.nodes.image_generate_node import image_generator_node
# 文案图片检查节点:校验生成内容是否可发布,输出 is_can_publish_xiaohongshu
from __004__langgraph_more_nodes.nodes.check_text_image_node import check_text_image_node
# 自动发布节点:调用 playwright 模拟登录发布到小红书平台
from __004__langgraph_more_nodes.nodes.auto_publish_xiaohongshu_node import xiaohongshu_auto_publish_node
# Markdown生成节点:将发布结果整理为 markdown 存档
from __004__langgraph_more_nodes.nodes.generate_markdown_node import generate_markdown_node

# 知识图谱问答节点
# 法律问答意图节点:判断是否为法律问答意图(注:实际流程由 intent_router 分流)
from __004__langgraph_more_nodes.nodes.legal_qa_intent_node import legal_qa_intent_node
# 实体抽取节点:从用户问题中抽取法律实体/概念/法规名
from __004__langgraph_more_nodes.nodes.extract_entity_from_user_input_node import extract_entity_from_user_input_node
# Neo4j实体匹配节点:在知识图谱中匹配相关实体
from __004__langgraph_more_nodes.nodes.match_entity_from_neo4j_node import match_entity_from_neo4j_node
# Cypher生成节点:基于匹配实体生成 Neo4j 查询语句
from __004__langgraph_more_nodes.nodes.generate_neo4j_cypher_node import generate_neo4j_cypher_node
# Cypher校验节点:校验 Cypher 语法合法性,输出 is_all_validate_cypher
from __004__langgraph_more_nodes.nodes.check_cypher_node import check_cypher_node
# Cypher执行节点:在 Neo4j 上执行查询,返回 cypher_results
from __004__langgraph_more_nodes.nodes.run_cypher_node import run_cypher_node
# 答案生成节点:基于 cypher_results 生成自然语言答案 neo4j_answer
from __004__langgraph_more_nodes.nodes.neo4j_answer_generate_node import neo4j_answer_generate_node

# 状态与工具
# 从 agent_state.py 导入共享状态类型 AgentState,作为 StateGraph 的状态模式
from __004__langgraph_more_nodes.agent_state import AgentState
# 路径工具函数:跨平台获取项目内文件绝对路径(避免硬编码相对路径在不同工作目录下失效)
from common.path_utils import get_file_path
# 图可视化工具:将编译后的 LangGraph 导出为 PNG 流程图,便于调试与文档展示
from common.ouput_graph_utils import output_pic_graph


def build_graph():
    """
    构建法智引擎 LangGraph 状态图并编译为可执行图对象。

    作用:
        本函数是整个多智能体系统的"装配车间"。它依次完成三件事:
        1) 实例化 StateGraph,绑定状态模式 AgentState;
        2) 注册所有业务节点(add_node),将节点函数以 __name__ 为键加入图;
        3) 定义边(add_edge)与条件边(add_conditional_edges),构建节点间的数据流;
        4) 调用 compile() 将图编译为可执行对象,供后续 invoke/ainvoke 调用。

    参数:
        无参数。所有节点函数均为模块级导入,图结构在函数内静态构建。

    返回值:
        CompiledGraph:编译后的可调用图对象,支持 .invoke(state) 同步调用
        与 .ainvoke(state) 异步调用,返回完整的最终状态字典。

    可迁移性说明:
        本函数体现的"装配模式"(注册节点 -> 加普通边 -> 加条件边 -> 编译)是 LangGraph
        项目的通用骨架,可迁移到任何基于状态图的多智能体编排场景(如客服机器人、
        研究助手等)。条件路由函数均以 (state) -> str 形式定义,与具体业务解耦,
        迁移时只需替换节点函数与路由判断逻辑。path_map 机制是 LangGraph 的标准用法,
        将路由函数返回的字符串映射到具体下一跳节点名,使路由逻辑与图拓扑分离,便于维护。
    """
    # 实例化 StateGraph,传入 AgentState 作为状态模式(Schema)
    # StateGraph 会基于 AgentState 的字段定义自动管理状态合并(节点返回的 dict 增量会被合并到全局 state)
    graph_builder = StateGraph(AgentState)

    # ==================== 注册所有节点 ====================
    # 核心节点
    # 使用 graph_builder.add_node 注册节点:第一个参数是节点名(此处用函数 __name__ 属性保证一致),
    # 第二个参数是节点函数本身。注册后该节点名可被 add_edge/add_conditional_edges 引用作为下一跳
    # 注册意图路由节点:N1 主路由节点,负责将用户输入分流到具体业务链路
    graph_builder.add_node(intent_router_node.__name__, intent_router_node)
    # 注册文档提取节点:N2 解析上传文档为纯文本
    graph_builder.add_node(doc_extract_node.__name__, doc_extract_node)
    # 注册合同分类节点:N3 判定合同类型
    graph_builder.add_node(contract_classify_node.__name__, contract_classify_node)
    # 注册条款切分节点:N4 切分合同条款
    graph_builder.add_node(clause_split_node.__name__, clause_split_node)
    # 注册数值抽取节点:抽取出合同中的关键数值
    graph_builder.add_node(numeric_extract_node.__name__, numeric_extract_node)
    # 注册合同审核AI节点:N5a 基于 LLM 做条款风险审查
    graph_builder.add_node(contract_ai_review_node.__name__, contract_ai_review_node)
    # 注册合规审查节点:N5b 检测合规性风险
    graph_builder.add_node(compliance_review_node.__name__, compliance_review_node)
    # 注册数值校验节点:N5c 校验数值合理性与一致性
    graph_builder.add_node(numeric_validate_node.__name__, numeric_validate_node)
    # 注册法律检索节点:N6 检索相关法规与案例
    graph_builder.add_node(legal_research_node.__name__, legal_research_node)
    # 注册风险聚合节点:N7 合并三路风险并计算综合评分
    graph_builder.add_node(risk_aggregate_node.__name__, risk_aggregate_node)
    # 注册甲乙方识别节点:N8 识别合同主体
    graph_builder.add_node(party_identify_node.__name__, party_identify_node)
    # 注册最终交付节点:N9 组装最终报告
    graph_builder.add_node(final_delivery_node.__name__, final_delivery_node)
    # 注册LLM直接输出节点:other 任务的兜底回答节点
    graph_builder.add_node(llm_direct_out_node.__name__, llm_direct_out_node)

    # 小红书节点
    # 注册小红书意图识别节点:START 后第一个节点,做前置意图过滤
    graph_builder.add_node(xiaohongshu_publish_intent_node.__name__, xiaohongshu_publish_intent_node)
    # 注册文案生成节点:生成小红书标题与正文
    graph_builder.add_node(text_generate_node.__name__, text_generate_node)
    # 注册图片生成节点:生成小红书配图
    graph_builder.add_node(image_generator_node.__name__, image_generator_node)
    # 注册文案图片检查节点:校验内容是否可发布
    graph_builder.add_node(check_text_image_node.__name__, check_text_image_node)
    # 注册自动发布节点:调用 playwright 自动发布到小红书
    graph_builder.add_node(xiaohongshu_auto_publish_node.__name__, xiaohongshu_auto_publish_node)
    # 注册Markdown生成节点:整理发布结果为 markdown
    graph_builder.add_node(generate_markdown_node.__name__, generate_markdown_node)

    # 知识图谱问答节点
    # 注册法律问答意图节点
    graph_builder.add_node(legal_qa_intent_node.__name__, legal_qa_intent_node)
    # 注册实体抽取节点:从用户问题抽取法律实体
    graph_builder.add_node(extract_entity_from_user_input_node.__name__, extract_entity_from_user_input_node)
    # 注册Neo4j实体匹配节点:在知识图谱中匹配实体
    graph_builder.add_node(match_entity_from_neo4j_node.__name__, match_entity_from_neo4j_node)
    # 注册Cypher生成节点:生成 Neo4j 查询语句
    graph_builder.add_node(generate_neo4j_cypher_node.__name__, generate_neo4j_cypher_node)
    # 注册Cypher校验节点:校验 Cypher 合法性
    graph_builder.add_node(check_cypher_node.__name__, check_cypher_node)
    # 注册Cypher执行节点:执行 Neo4j 查询
    graph_builder.add_node(run_cypher_node.__name__, run_cypher_node)
    # 注册答案生成节点:基于查询结果生成自然语言答案
    graph_builder.add_node(neo4j_answer_generate_node.__name__, neo4j_answer_generate_node)

    # ==================== 边: START -> 小红书意图识别 ====================
    # 添加图的入口边:从 START 哨兵节点指向小红书意图识别节点
    # 这样设计意味着所有请求都会先经过小红书意图过滤,再做主路由分流,属于"前置过滤"架构模式
    graph_builder.add_edge(START, xiaohongshu_publish_intent_node.__name__)

    # 小红书意图 -> 条件路由
    # 定义条件路由函数:根据小红书意图识别节点的输出决定下一跳
    # 该函数签名固定为 (state: AgentState) -> str,返回值会被 path_map 映射到具体节点名
    def is_xiaohongshu_publish_intent(state: AgentState):
        # 从 state 读取 is_xiaohongshu_publish_intent 布尔字段(由 xiaohongshu_publish_intent_node 写入)
        # state.get 在字段不存在时返回 None(假值),保证容错性
        if state.get("is_xiaohongshu_publish_intent"):
            # 若识别为小红书发布意图,返回 "publish_xiaohongshu_intent" 标识,后续会路由到文案生成节点
            return "publish_xiaohongshu_intent"
        else:
            # 否则返回 "intent_router" 标识,进入主意图路由节点处理常规法律任务
            return "intent_router"

    # 添加条件边:从小红书意图识别节点出发,根据 is_xiaohongshu_publish_intent 函数的返回值路由
    # path_map 参数将路由函数返回的字符串映射到具体节点名,使路由逻辑与图拓扑解耦
    graph_builder.add_conditional_edges(
        xiaohongshu_publish_intent_node.__name__,  # 条件边的起点节点
        is_xiaohongshu_publish_intent,             # 路由判定函数
        path_map={                                 # 路由返回值 -> 下一跳节点名的映射
            "publish_xiaohongshu_intent": text_generate_node.__name__,  # 走小红书文案生成
            "intent_router": intent_router_node.__name__,               # 走主意图路由
        }
    )

    # ==================== 小红书发布链路 ====================
    # 添加普通边:文案生成 -> 图片生成(顺序执行)
    # 这两个节点之间存在数据依赖:图片生成可能需要参考文案内容
    graph_builder.add_edge(text_generate_node.__name__, image_generator_node.__name__)
    # 添加普通边:图片生成 -> 文案图片检查(顺序执行)
    # 检查节点会综合校验标题/正文/图片是否符合发布要求
    graph_builder.add_edge(image_generator_node.__name__, check_text_image_node.__name__)

    # 定义检查节点的条件路由函数:决定是否进入自动发布环节
    def check_text_image_router(state: AgentState):
        # 读取 is_can_publish_xiaohongshu 字段(check_text_image_node 写入)
        # 为 True 表示文案图片检查通过,可以发布
        if state.get("is_can_publish_xiaohongshu"):
            # 返回 "publish_xiaohongshu" 标识,后续映射到自动发布节点
            return "publish_xiaohongshu"
        else:
            # 检查不通过直接结束流程(返回 END 哨兵)
            return END

    # 添加条件边:从检查节点出发,根据 check_text_image_router 路由
    graph_builder.add_conditional_edges(
        check_text_image_node.__name__,     # 条件边起点
        check_text_image_router,            # 路由判定函数
        path_map={                          # 路由映射
            "publish_xiaohongshu": xiaohongshu_auto_publish_node.__name__,  # 通过则自动发布
            END: END,                                                       # 不通过则直接结束
        }
    )
    # 添加普通边:自动发布 -> Markdown生成
    # 发布完成后生成 markdown 存档,便于后续追溯与展示
    graph_builder.add_edge(xiaohongshu_auto_publish_node.__name__, generate_markdown_node.__name__)
    # 添加普通边:Markdown生成 -> END(小红书链路结束)
    graph_builder.add_edge(generate_markdown_node.__name__, END)

    # ==================== 法智引擎核心链路 ====================
    # N1意图路由 -> 条件路由
    # 定义主路由的条件判定函数:根据 task_type 将流程分流到五大业务链路或兜底链路
    def intent_router_router(state: AgentState):
        # 从 state 读取 task_type 字段,默认值 "other" 防止字段缺失导致的 KeyError
        # task_type 由 intent_router_node 通过 LLM 分类后写入
        task_type = state.get("task_type", "other")
        # 以下 if-elif 链对应六大业务分支,每条返回一个路由标识符,由 path_map 映射到下一跳节点
        if task_type == "contract_review":
            # 合同审核:走完整流程(文档提取->合同分类->条款切分->...->最终交付)
            return "contract_review_path"
        elif task_type == "compliance_review":
            # 合规审查:走精简流程(文档提取->合规审查->数值校验->检索->风险聚合->交付)
            return "compliance_review_path"
        elif task_type == "legal_research":
            # 法律检索:直接进入法律检索节点(无需文档预处理)
            return "legal_research_path"
        elif task_type == "legal_qa":
            # 法律问答:进入知识图谱RAG链路(实体抽取->Neo4j匹配->Cypher->答案生成)
            return "legal_qa_path"
        else:
            # 其他意图(包括 "other" 与未识别):走LLM直接回答兜底链路
            return "llm_direct"

    # 添加条件边:从意图路由节点出发,根据 intent_router_router 函数路由到具体业务链路起点
    graph_builder.add_conditional_edges(
        intent_router_node.__name__,  # 条件边起点
        intent_router_router,         # 路由判定函数
        path_map={                    # 路由返回值 -> 下一跳节点名的映射
            # 合同审核与合规审查都从文档提取开始(后续在 doc_extract 后再二次分流)
            "contract_review_path": doc_extract_node.__name__,
            "compliance_review_path": doc_extract_node.__name__,
            # 法律检索直接进入检索节点
            "legal_research_path": legal_research_node.__name__,
            # 法律问答进入实体抽取节点
            "legal_qa_path": extract_entity_from_user_input_node.__name__,
            # 兜底链路进入LLM直接输出节点
            "llm_direct": llm_direct_out_node.__name__,
        }
    )
    # 添加普通边:LLM直接输出 -> END(兜底链路直接结束)
    graph_builder.add_edge(llm_direct_out_node.__name__, END)

    # ==================== 合同审核链路(N2->N3->N4->N5a->N5b->N5c->N6->N7->N8->N9) ====================
    # 以下依次连接合同审核主链路的各节点,形成完整的顺序执行流
    # 文档提取 -> 合同分类(N2 -> N3)
    graph_builder.add_edge(doc_extract_node.__name__, contract_classify_node.__name__)
    # 合同分类 -> 条款切分(N3 -> N4)
    graph_builder.add_edge(contract_classify_node.__name__, clause_split_node.__name__)
    # 条款切分 -> 数值抽取(N4 -> 数值抽取)
    graph_builder.add_edge(clause_split_node.__name__, numeric_extract_node.__name__)
    # 数值抽取 -> 合同审核AI(-> N5a)
    graph_builder.add_edge(numeric_extract_node.__name__, contract_ai_review_node.__name__)
    # 合同审核AI -> 合规审查(N5a -> N5b,串行模拟原本并行的两路检测)
    graph_builder.add_edge(contract_ai_review_node.__name__, compliance_review_node.__name__)
    # 合规审查 -> 数值校验(N5b -> N5c)
    graph_builder.add_edge(compliance_review_node.__name__, numeric_validate_node.__name__)

    # N5c数值校验后: 合同审核走检索+聚合, 合规审查走检索+聚合
    # 定义数值校验后的路由函数(注:当前实现中两条路径都走 legal_research,该函数保留为未来扩展)
    def after_numeric_validate_router(state: AgentState):
        # 读取 task_type,默认空字符串避免 None 比较异常
        task_type = state.get("task_type", "")
        # 若为合规审查,理论上可走精简路径(跳过合同审核AI,但实际链路已串行执行了审核AI)
        if task_type == "compliance_review":
            # 返回 "legal_research" 标识,表示下一步进入法律检索节点
            return "legal_research"  # 合规审查跳过合同审核AI, 直接到检索
        # 默认(合同审核)也进入法律检索节点
        return "legal_research"

    # 添加普通边:数值校验 -> 法律检索(注:此处使用普通边而非条件边,因两条路径下一跳相同)
    # 该边会覆盖 after_numeric_validate_router 的潜在分流(简化实现)
    graph_builder.add_edge(numeric_validate_node.__name__, legal_research_node.__name__)

    # 合规审查路径: doc_extract -> compliance_review -> numeric_validate
    # (通过intent_router路由到doc_extract, 然后在contract_classify后判断)
    # 定义文档提取后的路由函数(预留:用于区分合规审查与合同审核路径)
    def after_doc_extract_router(state: AgentState):
        # 读取 task_type
        task_type = state.get("task_type", "")
        # 若为合规审查,理论上应跳过合同分类直接进入合规审查
        if task_type == "compliance_review":
            # 返回 "compliance_review_direct" 标识(注:该标识在 path_map 中未启用)
            return "compliance_review_direct"
        # 默认(合同审核)进入合同分类节点
        return "contract_classify"

    # 重新调整: doc_extract后根据task_type分流
    # 合规审查: doc_extract -> compliance_review -> numeric_validate -> legal_research -> risk_aggregate -> final_delivery
    # 合同审核: doc_extract -> contract_classify -> ... -> final_delivery
    # 上面的边已经加了 doc_extract -> contract_classify, 这里改为条件边
    # 注意: langgraph_compat支持覆盖边, 但为安全起见我们保留contract_review走contract_classify
    # compliance_review也走doc_extract但会跳过合同分类(在contract_classify_node中task_type不是contract_review时简单跳过)

    # 添加普通边:法律检索 -> 风险聚合(N6 -> N7)
    # 风险聚合节点会合并三路风险项并计算综合评分
    graph_builder.add_edge(legal_research_node.__name__, risk_aggregate_node.__name__)
    # 添加普通边:风险聚合 -> 甲乙方识别(N7 -> N8)
    graph_builder.add_edge(risk_aggregate_node.__name__, party_identify_node.__name__)
    # 添加普通边:甲乙方识别 -> 最终交付(N8 -> N9)
    graph_builder.add_edge(party_identify_node.__name__, final_delivery_node.__name__)
    # 添加普通边:最终交付 -> END(N9 -> 结束)
    graph_builder.add_edge(final_delivery_node.__name__, END)

    # 合规审查专用路径: legal_research直接到final_delivery(跳过risk_aggregate)
    # 实际上合规审查也走risk_aggregate(合规风险也需评分), 所以共用同一路径
    # 上面的边已覆盖

    # 法律检索路径: legal_research -> final_delivery
    # 但legal_research_node后已经有边到risk_aggregate, 这里需要条件判断
    # 为简化: legal_research路径也走final_delivery(在risk_aggregate中无风险时评分95)
    # 上面的边已覆盖

    # ==================== 知识图谱问答链路 ====================
    # 以下连接法律问答(知识图谱RAG)链路的各节点
    # 实体抽取 -> Neo4j实体匹配
    graph_builder.add_edge(extract_entity_from_user_input_node.__name__, match_entity_from_neo4j_node.__name__)
    # Neo4j实体匹配 -> Cypher生成
    graph_builder.add_edge(match_entity_from_neo4j_node.__name__, generate_neo4j_cypher_node.__name__)
    # Cypher生成 -> Cypher校验
    graph_builder.add_edge(generate_neo4j_cypher_node.__name__, check_cypher_node.__name__)

    # 定义Cypher校验后的路由函数:实现"校验失败重试最多3次"的循环控制
    def check_cypher_router(state: AgentState):
        # 优先判断 Cypher 是否通过校验(由 check_cypher_node 写入 is_all_validate_cypher)
        if state.get("is_all_validate_cypher"):
            # 通过校验,返回 "run_cypher" 标识,进入执行节点
            return "run_cypher"
        # 若未通过校验,检查重试次数是否已达上限(默认上限3次,防止死循环)
        # state.get 第二个参数 0 提供默认值,cypher_retry_count 由 check_cypher_node 在重试时累加
        elif state.get("cypher_retry_count", 0) >= 3:
            # 重试达上限仍失败,放弃图查询,直接走答案生成(可能基于已有实体匹配结果回答)
            return "generate_answer"
        else:
            # 未达上限,返回 "generate_neo4j_cypher" 标识,回到Cypher生成节点重新生成(形成循环)
            return "generate_neo4j_cypher"

    # 添加条件边:从Cypher校验节点出发,根据 check_cypher_router 路由(支持循环与终止)
    graph_builder.add_conditional_edges(
        check_cypher_node.__name__,       # 条件边起点
        check_cypher_router,              # 路由判定函数
        path_map={                        # 路由映射
            "run_cypher": run_cypher_node.__name__,                       # 通过校验->执行查询
            "generate_neo4j_cypher": generate_neo4j_cypher_node.__name__, # 未通过->重新生成(循环)
            "generate_answer": neo4j_answer_generate_node.__name__,       # 重试上限->直接生成答案
        }
    )
    # 添加普通边:Cypher执行 -> 答案生成(查询完成后生成自然语言答案)
    graph_builder.add_edge(run_cypher_node.__name__, neo4j_answer_generate_node.__name__)
    # 添加普通边:答案生成 -> END(法律问答链路结束)
    graph_builder.add_edge(neo4j_answer_generate_node.__name__, END)

    # ==================== 编译 ====================
    # 调用 compile() 将图构建器编译为可执行图对象
    # 编译过程会校验图拓扑(如未注册节点、孤立节点、缺失入口等),返回 CompiledGraph
    # 编译后的对象支持 .invoke(state) 同步与 .ainvoke(state) 异步调用
    return graph_builder.compile()


# 构建图
# 模块级执行:在导入本模块时立即构建并编译图对象 graph
# 这样设计使图只构建一次(单例),后续所有 legal_response* 调用复用同一编译图,避免重复编译开销
graph = build_graph()

# 生成流程图
# 使用 try-except 包裹可视化生成,因为 graphviz 等依赖可能未安装或环境不支持
# 失败时仅打印警告而不中断模块加载(可视化是辅助功能,不影响业务逻辑)
try:
    # 调用 output_pic_graph 将编译图导出为 PNG 图片,保存到指定路径
    # get_file_path 跨平台解析项目内文件路径,确保不同工作目录下都能正确写入
    output_pic_graph(graph, get_file_path("__004__langgraph_more_nodes/graph.png"))
except Exception as e:
    # 捕获所有异常(如缺依赖、写入权限等),打印警告信息
    # 使用 emoji ⚠️ 在控制台中突出显示,便于开发者注意到可视化跳过
    print(f"⚠️ 流程图生成跳过: {e}")


async def legal_response(input: str, **kwargs):
    """
    异步调用法智引擎图并返回纯文本输出。

    作用:
        异步入口接口,适用于 Web 服务(async handler)或需要并发的场景。
        将用户输入与可选参数打包为状态字典,调用 graph.ainvoke 异步执行图,
        执行完毕后从最终状态中提取 output 字段返回。

    参数:
        input (str): 用户输入文本(法律问题、合同文本、发小红书指令等)。
        **kwargs: 任意可选的状态字段,如 task_type(强制指定任务类型)、
                  uploaded_doc_path(上传文档路径)、user_side(用户立场)等。
                  这些参数会合并到初始 state 中,可覆盖默认行为。

    返回值:
        str: 最终状态中的 output 字段(纯文本回答/报告摘要),若不存在返回空字符串。

    可迁移性说明:
        这是 LangGraph 异步调用的标准模式:state_input -> ainvoke -> get(output)。
        可迁移到任何基于图的异步任务,只需替换 graph 对象与输出字段名。
        注意 ainvoke 返回的是完整最终状态,而非节点返回的增量,因此可读取任意字段。
    """
    # 构造初始状态字典:将 input 文本与 kwargs 中的可选字段合并
    # 例如 legal_response("...", task_type="contract_review") 会生成 {"input": "...", "task_type": "contract_review"}
    state_input = {"input": input, **kwargs}
    # 异步调用图:graph.ainvoke 会从 START 开始按拓扑执行各节点,直到抵达 END
    # await 等待整个图执行完毕,返回合并后的完整状态字典
    result = await graph.ainvoke(state_input)
    # 从最终状态提取 output 字段(由 final_delivery_node 或对应链路的终态节点写入)
    # 使用 .get 容错:若 output 未被设置则返回空字符串,避免 KeyError
    return result.get("output", "")


def legal_response_sync(input: str, **kwargs):
    """
    同步调用法智引擎图,返回纯文本输出(兼容旧接口)。

    作用:
        同步入口接口,适用于脚本、CLI 工具或不支持异步的旧代码。
        与 legal_response 功能等价,但使用同步 invoke 避免事件循环开销。
        保留该接口是为了向后兼容项目中已有的同步调用代码。

    参数:
        input (str): 用户输入文本。
        **kwargs: 任意可选状态字段(同 legal_response)。

    返回值:
        str: 最终状态的 output 字段(纯文本)。

    可迁移性说明:
        该接口是 LangGraph 同步调用的最小封装,适合简单脚本与测试场景。
        在生产 Web 服务中推荐使用 legal_response(异步)以提升并发性能。
        迁移到其他图时,只需替换 graph 对象即可。
    """
    # 构造初始状态字典(与 legal_response 相同)
    state_input = {"input": input, **kwargs}
    # 同步调用图:graph.invoke 阻塞当前线程直到图执行完毕
    # 适合脚本与简单场景;在异步环境(如 FastAPI handler)中应改用 ainvoke 避免阻塞事件循环
    result = graph.invoke(state_input)
    # 返回 output 字段(默认空字符串)
    return result.get("output", "")


def legal_response_full(input: str, **kwargs):
    """
    同步调用法智引擎图,返回完整 state dict(含结构化数据,供前端卡片展示)。

    作用:
        同步入口接口,但返回比 legal_response_sync 更丰富的结构化数据。
        前端可基于该数据渲染风险卡片、引用列表、合同信息等多种可视化组件,
        而不仅仅是纯文本回答。本函数负责从最终状态中提取并整理前端需要的所有字段。

    参数:
        input (str): 用户输入文本。
        **kwargs: 任意可选状态字段。

    返回值:
        dict: 包含前端展示所需的完整数据结构,键包括:
            - output (str): 纯文本输出
            - doc_text (str): 文档全文
            - merged_risk_items (List): 合并去重后的风险项列表
            - contract_risk_items / compliance_risk_items / numeric_risk_items (List): 三路风险项
            - overall_risk_score (float): 综合风险评分(0-100)
            - risk_level (str): 风险等级(Low/Medium/High)
            - citations (List): 法规引用列表
            - party_a / party_b (str): 甲乙方名称
            - contract_type (str): 合同类型
            - need_lawyer_review (bool): 是否需要律师复核
            - final_report_markdown (str): Markdown 格式最终报告
        所有字段均提供默认值,确保字段缺失时不会抛出异常。

    可迁移性说明:
        该接口体现的"字段映射+默认值"模式可迁移到任何需要向前端暴露结构化结果的场景。
        迁移时需根据新业务调整返回字段集合。注意 .get(key, default) 的使用确保了
        即使图中某些节点未执行(如非合同审核链路下 contract_risk_items 为空)也能稳定返回。
    """
    # 构造初始状态字典
    state_input = {"input": input, **kwargs}
    # 同步调用图,获取完整最终状态
    result = graph.invoke(state_input)
    # 构造前端需要的完整数据
    # 逐字段从最终状态提取,使用 .get 提供默认值,确保前端不会因字段缺失而崩溃
    return {
        # 纯文本输出(报告摘要或问答答案)
        "output": result.get("output", ""),
        # 文档全文(供前端展示原文对照)
        "doc_text": result.get("doc_text", ""),
        # 合并去重后的风险项(供风险卡片渲染)
        "merged_risk_items": result.get("merged_risk_items", []),
        # 三路独立风险项(供细分展示)
        "contract_risk_items": result.get("contract_risk_items", []),
        "compliance_risk_items": result.get("compliance_risk_items", []),
        "numeric_risk_items": result.get("numeric_risk_items", []),
        # 综合风险评分(0-100,用于风险等级条形图)
        "overall_risk_score": result.get("overall_risk_score", 0),
        # 风险等级标签(用于颜色标识:Low绿/Medium黄/High红)
        "risk_level": result.get("risk_level", "Unknown"),
        # 法规引用列表(用于引用卡片)
        "citations": result.get("citations", []),
        # 甲乙方名称(用于合同信息卡片)
        "party_a": result.get("party_a", ""),
        "party_b": result.get("party_b", ""),
        # 合同类型(用于合同信息卡片)
        "contract_type": result.get("contract_type", ""),
        # 是否需要律师复核(用于提示横幅)
        "need_lawyer_review": result.get("need_lawyer_review", False),
        # Markdown 格式最终报告(用于详细报告折叠面板)
        "final_report_markdown": result.get("final_report_markdown", ""),
    }


async def legal_response_stream(input: str, **kwargs):
    """
    流式输出接口,逐块返回生成器,供前端 st.write_stream 使用。

    作用:
        异步生成器接口,模拟流式输出体验。先按固定字符块逐块 yield 文本(模拟打字机效果),
        最后再 yield 一个 JSON 数据包(以特殊标记包裹),内含完整结构化数据供前端卡片渲染。
        适用于 Streamlit(st.write_stream)等支持流式渲染的前端框架。

    参数:
        input (str): 用户输入文本。
        **kwargs: 任意可选状态字段。

    返回值:
        AsyncGenerator: 异步生成器,逐块产出:
            - 多个 str: 文本片段(每块 chunk_size=4 个字符),供前端流式拼接展示;
            - 末尾 str: 形如 "__DATA_END__{json}__DATA_END__" 的特殊标记字符串,
              内含完整结构化数据(done=True 标识结束)。

    可迁移性说明:
        该接口的特殊设计在于"文本流 + 末尾数据包"的双段式输出:
        1) 文本段用于前端实时打字机效果,提升用户体验;
        2) 末尾数据段携带完整结构化结果,供前端在文本流结束后渲染卡片。
        双下划线标记 __DATA_END__ 是约定的分隔符,前端需据此识别并解析 JSON。
        此模式可迁移到任何需要"流式文本+结构化结果"的 LLM 应用。
        注意当前实现是"伪流式"(先完整执行图再分块输出),实际生产可替换为 LLM 流式调用
        (如 await llm.astream)实现真流式。

    实现细节:
        - chunk_size=4:每个文本块4个字符,平衡流畅度与开销
        - asyncio.sleep(0.02):每块间停顿20ms,模拟真实打字速度
        - 末尾 JSON 通过 ensure_ascii=False 保留中文可读性
    """
    # 构造初始状态字典
    state_input = {"input": input, **kwargs}
    # 异步调用图,等待整个图执行完毕(注:此处非真流式,先完成再分块)
    result = await graph.ainvoke(state_input)
    # 提取纯文本输出
    output_text = result.get("output", "")
    
    # 模拟流式效果: 按字符分块输出 (实际项目中可替换为 LLM 流式调用)
    # 定义每个文本块的字符数:4个字符一块,既保证流畅感又控制 yield 次数
    chunk_size = 4
    # 使用 range 生成起始索引,步长为 chunk_size,遍历整个 output_text
    for i in range(0, len(output_text), chunk_size):
        # 切片取出当前块(最后一块可能不足 chunk_size 字符,切片会自动处理)
        chunk = output_text[i:i + chunk_size]
        # yield 当前块给前端,前端 st.write_stream 会实时拼接显示
        yield chunk
        # 异步等待 20ms,制造打字机停顿效果(避免输出过快失去流式感)
        # 也可理解为给前端留出渲染时间
        await asyncio.sleep(0.02)

    # 最终推送完整的结构化数据 (供前端卡片渲染)
    # 在函数内导入 json,避免在模块顶部引入未广泛使用的库(局部导入习惯)
    import json
    # 构造末尾数据包:done=True 标识流式结束,full_result 携带前端需要的结构化字段
    final_data = {
        "done": True,  # 流式结束标志,前端据此停止接收
        "full_result": {  # 完整结构化结果
            "output": output_text,                                # 完整文本(与流式拼接结果一致)
            "doc_text": result.get("doc_text", ""),               # 文档全文
            "merged_risk_items": result.get("merged_risk_items", []),  # 合并风险项
            "overall_risk_score": result.get("overall_risk_score", 0),  # 综合风险评分
            "risk_level": result.get("risk_level", "Unknown"),    # 风险等级
            "need_lawyer_review": result.get("need_lawyer_review", False),  # 是否需律师复核
            "citations": result.get("citations", []),             # 法规引用
            "final_report_markdown": result.get("final_report_markdown", ""),  # Markdown报告
        }
    }
    # 将数据包序列化为 JSON 字符串(ensure_ascii=False 保留中文,避免 \uXXXX 转义)
    # 用 __DATA_END__ 标记包裹,前端通过正则或字符串匹配识别并解析
    yield f"__DATA_END__{json.dumps(final_data, ensure_ascii=False)}__DATA_END__"


if __name__ == "__main__":
    # 测试: 合同审核
    # 构造一份测试合同文本,包含典型合同要素(标的、数量、单价、总价、付款比例、违约金、管辖等)
    # 用于在命令行直接运行本文件时验证合同审核链路是否正常工作
    test_contract = (
        "甲方A公司向乙方B公司采购电脑100台，单价5000元，总价50万元；"
        "付款比例：预付款50%，货到验收后付款40%，质保10%一年后付；"
        "违约金每日千分之三；争议解决由甲方所在地法院管辖。"
    )

    # 打印分隔线与测试标题(便于在控制台输出中定位测试结果)
    print("=" * 60)
    print("测试: 合同审核")
    print("=" * 60)
    # 同步调用图,强制指定 task_type 为 contract_review 走合同审核链路
    # 这样可绕过意图路由节点的 LLM 分类,直接验证合同审核链路本身
    result = legal_response_sync(test_contract, task_type="contract_review")
    # 打印结果前1000字符(避免输出过长刷屏);若无输出则打印"无输出"提示
    print(result[:1000] if result else "无输出")
