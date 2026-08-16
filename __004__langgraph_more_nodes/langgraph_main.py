"""
================================================================================
法智引擎 LangGraph 主编排 —— 全项目最核心的"神经中枢"文件
================================================================================

【0. 写给完全零基础读者的开篇说明:这个文件到底是什么?】
--------------------------------------------------------------------------------
如果你以前从没接触过 LangGraph,请先花 3 分钟读这一段,后面的每一行代码注释
都是站在"你完全不懂"的角度写的,读完这一段再看代码会事半功倍。

(1) 什么是【Agent / 智能体】?
    在 AI 应用中,"智能体(Agent)"可以理解为"一个会干某一件具体小事的 AI 功能单元"。
    比如本项目里:
      - 文档提取节点:负责把 PDF/Word 合同"读"成纯文本;
      - 合同审核节点:负责调用大模型(LLM)逐条挑出合同里的风险;
      - 法律检索节点:负责去 FAISS/Neo4j 数据库里找相关法条。
    单独看,每个节点就像一个"只会一招的小工人"。

(2) 什么是【LangGraph】?
    光有"小工人"还不够,我们需要一个"车间主任"来安排:
      先让谁干活?干完活把结果递给谁?遇到不同情况该走哪条流水线?
    LangGraph 就是这样一个"车间主任"框架 —— 它把每个智能体当作图里的一个
    【节点(Node)】,用【边(Edge)】把节点串起来,最终形成一张"有向图(Directed Graph)"。
    图从入口【START】开始,一路沿着边执行节点,直到出口【END】结束。
    一句话总结:**LangGraph = 用"图"来编排多个 AI 智能体协作的框架**。

(3) 什么是【State / 状态】?
    流水线上所有工人要共享一块"公用的工作台/黑板",上面写着大家都能读写的共享数据,
    这块黑板在 LangGraph 里就叫【State(状态)】。
    本项目里 State 的类型是 AgentState(从 agent_state.py 导入),它上面有
    input(用户输入)、task_type(任务类型)、doc_text(合同文本)等几十个字段。
    每个节点执行时:读黑板 -> 干活 -> 把新结果(一个字典)写回黑板。
    LangGraph 会自动把节点返回的字典"合并"进黑板,所以后面的节点永远能读到
    前面节点写下的内容。**节点之间不直接传参,全靠这块黑板(State)传递数据**。

(4) 什么是【节点函数】与【add_node】?
    每个节点本质是一个 Python 函数,签名固定为:
        def 某节点(state: AgentState) -> dict: ...
    即"吃进整个状态,吐出一个增量字典"。
    用 graph.add_node("节点名", 函数) 把它注册进图里,节点名就像工牌号,
    后面连边(add_edge / add_conditional_edges)时就用工牌号指路。

(5) 什么是【普通边 add_edge】与【条件边 add_conditional_edges】?
    - 普通边:固定顺序。A -> B 表示 A 干完一定轮到 B,没有其他可能。
    - 条件边:有分支。从 A 出发挂一个"路由函数(router)",
      路由函数看黑板上的某个字段,返回一个字符串(路线代号),
      LangGraph 再用 path_map(路线代号 -> 下一节点名 的查表)决定真正去哪个节点。
      这正是"多智能体分流/循环重试"能力的来源。

(6) 什么是【编译 compile】?
    把"节点 + 边"组装成一张图之后,调用 graph.compile() 做一次"体检":
    检查有没有注册了边却没注册的节点、有没有死路等,通过后返回一个可执行对象
    (CompiledGraph),之后就能用 graph.invoke(state) 同步执行或
    graph.ainvoke(state) 异步执行整张图了。

【1. 本文件在整个项目中的位置】
--------------------------------------------------------------------------------
本项目是一个"法智引擎"(AI 法律助理)。前端(如 Streamlit 的 app.py)把用户输入
丢给本文件暴露的四个对外接口,本文件负责:
  1) 构建并编译整张 LangGraph 状态图(模块加载时完成,只构建一次);
  2) 把用户请求塞进 State,驱动整张图跑完;
  3) 把最终结果(纯文本 / 完整结构化数据 / 流式文本)返回给前端。
本文件【只负责"编排",不负责"具体干活"】—— 所有具体业务逻辑都在
__004__langgraph_more_nodes/nodes/ 目录下的各个节点文件里。

【2. 完整流程图(架构总览,务必对照着看下面的代码)】
--------------------------------------------------------------------------------
入口:
  START
   └─▶ N0 小红书意图识别(xiaohongshu_publish_intent_node)
        ├─ 是小红书意图 ──▶ 小红书发布链路:
        │      文案生成(text_generate_node)
        │      └▶ 图片生成(image_generator_node)
        │          └▶ 文案图片检查(check_text_image_node)
        │              ├─ 检查通过 ─▶ 自动发布(xiaohongshu_auto_publish_node)
        │              │              └▶ Markdown生成(generate_markdown_node) ─▶ END
        │              └─ 检查不通过 ─▶ END(直接结束,不发布)
        └─ 非小红书意图 ──▶ N1 主意图路由(intent_router_node)
             └▶(按 task_type 分流,但统一先进 N0.5 企查查预判定)
                 └─▶ N0.5 企查查预判定(credit_precheck_node)
                      └▶(按 task_type 二次路由到各链路真实起点)
                          ├─ contract_review(合同审核)──▶ N2文档提取
                          │    └▶ N8甲乙方识别 ─▶ N3合同分类 ─▶ N4条款切分
                          │       ─▶ 数值抽取 ─▶ 检索5节点(意图分解→基础层→
                          │          增强查询→融合排序→输出)
                          │          ─▶ N5a合同审核AI ─▶ N5b合规审查 ─▶ N5c数值校验
                          │             ─▶ N8.5相对方资信查询 ─▶ N7风险聚合
                          │                ─▶ N9最终交付 ─▶ END
                          ├─ compliance_review(合规审查)──▶ 同合同审核前半段(文档提取)
                          │    ─▶ N5b合规审查 ─▶ N5c数值校验 ─▶ 检索 ─▶ N7风险聚合
                          │    ─▶ N9最终交付 ─▶ END
                          ├─ legal_research(法律检索)──▶ 检索5节点 ─▶ N9最终交付 ─▶ END
                          ├─ legal_qa(法律问答/知识图谱RAG)──▶ 实体抽取
                          │    ─▶ Neo4j实体匹配 ─▶ Cypher生成
                          │       ─▶ Cypher校验 ◀──(失败且未到3次:回 Cypher生成,循环)
                          │          ├─ 通过 ─▶ Cypher执行 ─▶ 答案生成 ─▶ END
                          │          └─ 超3次仍失败 ─▶ 答案生成 ─▶ END
                          ├─ legal_document_gen(法律文书生成)──▶ N_doc1案情分析
                          │    ─▶ N_doc2模板匹配 ─▶ N_doc3条款填充
                          │       ─▶ N_doc4法条校验 ◀──(need_refill 且未到3次:回条款填充,循环)
                          │          └▶ N_doc5风险提示 ─▶ N_doc6类案推荐
                          │             ─▶ N_doc7最终交付 ─▶ END
                          ├─ case_search(案例检索)──▶ case_search_node ─▶ END(单节点)
                          ├─ law_query(法规查询)──▶ law_query_node ─▶ END(单节点)
                          └─ other(其他)──▶ LLM直接回答(llm_direct_out_node) ─▶ END

【3. 关键设计思想(读代码前先建立心智模型)】
--------------------------------------------------------------------------------
a) 【前置过滤架构】:所有请求先过"小红书意图识别",是小红书就走小红书链路,
   否则才进主意图路由 —— 把特殊业务(发小红书)和常规法律业务解耦。
b) 【统一预判定(Phase 2 企查查)】:主路由之后所有链路统一先经过
   credit_precheck_node 做"三层触发判定 + 资信预查",再把查到的资信缓存进 State,
   供下游所有链路复用 —— 避免每条链路各自重复查企查查。
c) 【串行模拟并行】:Python 3.8 的轻量兼容层(common/langgraph_compat)不支持真并行,
   所以本来可以并行的 N5a/N5b/N5c 在这里用串行边依次执行。
d) 【优雅降级】:Neo4j/FAISS 不可用时,相关节点内部会降级为 LLM 伪检索,保证不崩。
e) 【重试环(循环边)】:Cypher 校验失败最多重试 3 次;文书法条校验失败最多重试 3 次。
   循环是条件边实现的:路由函数返回"回到上一个节点"的路线代号即可。
f) 【对外四个接口】:legal_response(异步纯文本)/ legal_response_sync(同步纯文本)/
   legal_response_full(同步完整结构化 dict)/ legal_response_stream(流式文本+末尾数据包),
   适配不同前端调用场景。
g) 【子进程隔离 CLI】:_run_cli() 让前端用 subprocess 启动独立 Python 进程跑图,
   防止 numpy/pyarrow 等 C 扩展的硬崩溃把 Streamlit 主进程一起带死。

【4. 阅读顺序建议】
--------------------------------------------------------------------------------
第 1 遍:先看第【2】节的总览图,再通读 build_graph() 里的 add_node 注册区,
        知道"有哪些工人"。
第 2 遍:顺着边(edge)走一遍合同审核链路,理解数据流。
第 3 遍:重点看三个条件路由函数(intent_router_router / check_cypher_router /
        doc_law_validate_router),理解"分支"与"重试循环"。
第 4 遍:看文件末尾的四个对外接口与 CLI,理解"怎么被前端调用"。

本文件对应中医项目中的 langgraph_more_nodes.py,是整套多智能体编排的骨架。
================================================================================
"""

# ==============================================================================
# 【一、模块级注释:代码文字逻辑总解析】
# ------------------------------------------------------------------------------
# 本文件是法智引擎(AI法律助理)基于 LangGraph 框架的多智能体编排核心入口。
# 其核心职责是:
#   1) 注册所有业务节点函数(add_node,把"小工人"登记进图);
#   2) 定义节点之间的边 —— 普通边(add_edge)和条件边(add_conditional_edges),
#      从而确定"小工人之间的先后顺序与分支路线";
#   3) 编译生成可调用的图(compile,相当于给整条流水线做最终验收);
#   4) 对外暴露多个调用接口(同步/异步/流式/完整状态)供前端或上游服务调用。
# 整个图从 START 出发,首先进入小红书意图识别节点做【前置过滤】:若用户想发小红书
# 则走小红书发布链路(文案生成->图片生成->检查->发布->Markdown),否则进入主意图
# 路由(intent_router_node)。主路由根据 task_type 将流程分流到多条业务链路:
# 合同审核(全流程)、合规审查(精简流程)、法律检索、法律问答(知识图谱RAG:
# 实体抽取->Neo4j匹配->Cypher生成/校验/执行->答案生成)、法律文书生成、
# 案例检索、法规查询、通用兜底(LLM直接回答)。
# 条件路由函数(如 intent_router_router、check_cypher_router、doc_law_validate_router)
# 根据状态字段返回不同的"路线代号",实现动态分支与重试控制(如 Cypher 校验失败
# 重试最多 3 次)。本文件还提供 legal_response / legal_response_sync /
# legal_response_full / legal_response_stream 四个对外接口,分别支持异步调用、
# 同步纯文本、完整结构化数据、流式输出四种使用场景,适配不同前端需求。
# 在模块加载时即构建并编译图(graph = build_graph()),并尝试生成可视化流程图
# (graph.png)便于调试。
# ==============================================================================

# 【二、标准库导入区】
# 下面 4 个 import 是 Python 标准库(不需要 pip 安装),是后面所有代码的地基。

# asyncio 标准库:提供异步 IO 支持(即"边等边干别的活"的能力)。
# 本文件中 legal_response / legal_response_stream 使用 async/await 语法,
# 配合 graph.ainvoke 异步执行图,避免阻塞主线程 ——
# 尤其重要于 Web 服务场景下高并发请求:一个请求在等 LLM 回答时,主线程还能
# 继续接待其他请求,而不是干等(阻塞)。
import asyncio
# sys 标准库:提供与 Python 解释器交互的能力(如命令行参数 sys.argv、
# 标准输出 sys.stdout / 标准错误 sys.stderr)。本文件在 CLI 入口(_run_cli)中
# 用它读写控制台输出,并 reconfigure 标准流的编码以适配 Windows GBK 环境。
import sys
# os 标准库:提供操作系统相关功能(环境变量、路径拼接等)。
# 本文件里主要用于配合 common.path_utils 做跨平台路径解析。
import os
# json 标准库:提供 JSON 序列化(json.dumps,把 dict 变成字符串)与反序列化
# (json.loads,把字符串变回 dict)。本文件在 CLI 输出与流式数据包中用
# json.dumps(..., ensure_ascii=False) 把结果打包成前端能解析的 JSON 字符串。
import json

# 使用兼容层(Python 3.8 环境)
# 从 common.langgraph_compat 兼容层导入 StateGraph 类与 START/END 常量。
# 兼容层的作用:在 Python 3.8 环境(无法安装官方 langgraph 最新版)下,
# 提供与官方 API 一致的轻量实现,使上层代码无需关心运行环境差异。
# 三个名字的含义:
#   StateGraph —— 【状态图类】,构建图的核心类。用法:StateGraph(状态类型),
#                 然后 add_node / add_edge / add_conditional_edges / compile。
#   START      —— 【入口哨兵常量】,图的起点。add_edge(START, 节点名) 表示
#                 "从入口直接连到该节点",整张图从这里开始执行。
#   END        —— 【出口哨兵常量】,图的终点。add_edge(节点名, END) 表示
#                 "该节点执行完,整张图就结束"。
from common.langgraph_compat import StateGraph, START, END

# 法智引擎核心节点
# ------------------------------------------------------------------------------
# 下面这一大片 import 就是把"所有小工人(节点函数)"请进本文件的"工人名册"。
# 每个节点函数的签名统一为 (state: AgentState) -> dict 或 -> AgentState,
# 即"吃进整个状态黑板,吐出一个增量字典(要写回黑板的新内容)"。
# 节点函数通过 __name__ 属性(即函数定义时的名字)作为图中的唯一标识注册,
# 确保"名字和函数"一一对应、可追溯:后面 add_node(函数.__name__, 函数)
# 用同一个名字登记,add_edge 也用这个名字指路,三者完全一致才不会迷路。
# ------------------------------------------------------------------------------

# 意图路由节点(N1):基于用户输入做意图分类,输出 task_type 字段
# (如 "contract_review" / "legal_qa" / "other" 等),是主路由的判定依据。
# 可以理解为"前台接待员":先问清用户想办什么事,再引导到对应窗口。
from __004__langgraph_more_nodes.nodes.intent_router_node import intent_router_node
# 文档提取节点(N2):读取 uploaded_doc_path 指向的上传文档(如 PDF/Word 合同),
# 解析为纯文本并写入 doc_text 字段。相当于"把纸质合同扫描成电子文字"。
from __004__langgraph_more_nodes.nodes.doc_extract_node import doc_extract_node
# 合同分类节点(N3):基于 doc_text 判定合同类型(买卖/租赁/借贷等),
# 写入 contract_type 字段。不同类型合同的风险点不同,后续审核要按类型看。
from __004__langgraph_more_nodes.nodes.contract_classify_node import contract_classify_node
# 条款切分节点(N4):将 doc_text 按条款结构切分为结构化条款列表,
# 写入 doc_clauses 字段。把一整篇合同切成一条一条,方便逐条审查。
from __004__langgraph_more_nodes.nodes.clause_split_node import clause_split_node
# 数值抽取节点:从合同文本中抽取关键数值(单价/数量/总价/期限等),
# 写入 extracted_numerics 字段。为后面的数值校验(N5c)提供"原料"。
from __004__langgraph_more_nodes.nodes.numeric_extract_node import numeric_extract_node
# 合同审核AI节点(N5a):基于 LLM 对每一条条款做风险审查(结合检索到的法条),
# 输出 contract_risk_items(合同风险项列表)。这是"AI 律师"的第一只眼睛。
from __004__langgraph_more_nodes.nodes.contract_ai_review_node import contract_ai_review_node
# 合规审查节点(N5b):对照法律法规检测合规性风险(如霸王条款、违法约定),
# 输出 compliance_risk_items(合规风险项列表)。这是"AI 律师"的第二只眼睛。
from __004__langgraph_more_nodes.nodes.compliance_review_node import compliance_review_node
# 数值校验节点(N5c):校验数值的一致性与合理性(如总价是否等于单价×数量、
# 利率是否超过法定上限),输出 numeric_risk_items。这是"AI 律师"的第三只眼睛。
from __004__langgraph_more_nodes.nodes.numeric_validate_node import numeric_validate_node
# 法律检索节点(N6):通过 FAISS/Neo4j 检索相关法规与案例(不可用时降级为
# LLM 伪检索),输出 research_context 与 citations(法条引用列表)。
# 检索结果要提前就位,后面的审核AI才能"引经据典"。
from __004__langgraph_more_nodes.nodes.legal_research_node import legal_research_node
# 风险聚合节点(N7):合并多路风险项(合同/合规/数值/资信四路)并计算
# overall_risk_score(0-100 综合评分)与 risk_level(Low/Medium/High)。
# 相当于"总审法官":把三只眼睛 + 资信调查的结果汇总打分。
from __004__langgraph_more_nodes.nodes.risk_aggregate_node import risk_aggregate_node
# 冲突消解节点(N5d):当合同审核与合规审查结果冲突时，以合规为准
# 应用5条冲突消解规则（合规有一票否决权），输出 merged_risk_items + can_sign
from __004__langgraph_more_nodes.nodes.conflict_resolution_node import conflict_resolution_node
# 甲乙方识别节点(N8):识别合同主体,写入 party_a(甲方)/ party_b(乙方)/
# user_side(用户站在哪一边)。用户立场决定审核视角(如站在买家还是卖家看风险)。
from __004__langgraph_more_nodes.nodes.party_identify_node import party_identify_node
# 相对方资信查询节点(N8.5):在甲乙方识别之后,调用企查查 API 检索对方的
# 工商/司法/经营资信信息,生成 credit_risk_items(资信风险项)。
# 相当于"背景调查员":查对方公司有没有被执行、失信、经营异常等。
from __004__langgraph_more_nodes.nodes.credit_check_node import credit_check_node
# Phase 2: 企查查预判定节点(N0.5)(intent_router 之后全局触发):
# 做"三层触发判定 + 资信预查",把资信缓存进 State,供下游所有链路复用。
# 这样合同审核/合规审查/法律检索/法律问答等链路都能共享同一套企查查逻辑,
# 避免每条链路重复查、重复写代码。
from __004__langgraph_more_nodes.nodes.credit_precheck_node import credit_precheck_node
# 最终交付节点(N9):组装最终报告 markdown(final_report_markdown)与
# output 文本(纯文本答案)。这是"文书秘书":把所有结果整理成给用户看的报告。
from __004__langgraph_more_nodes.nodes.final_delivery_node import final_delivery_node
# LLM 直接输出节点:other 类型任务的兜底回答节点,直接调用 LLM 回答用户问题,
# 不经过任何合同/检索流程。这是"全科门诊":什么杂活都接。
from __004__langgraph_more_nodes.nodes.llm_direct_out_node import llm_direct_out_node

# ---- 法律文书生成链路节点 (N_doc1 ~ N_doc7) ----
# ------------------------------------------------------------------------------
# 这是"法律文书生成"任务(如写起诉状、合同草稿)专用的七人流水线:
# N_doc1 案情分析 -> N_doc2 模板匹配 -> N_doc3 条款填充(RAG) ->
# N_doc4 法条校验(≤3次重试环) -> N_doc5 风险提示 -> N_doc6 类案推荐 ->
# N_doc7 最终交付 + 历史持久化。
# ------------------------------------------------------------------------------
# N_doc1 案情分析节点:从用户输入中结构化抽取案情要素(当事人/诉求/事实等)。
from __004__langgraph_more_nodes.nodes.doc_case_analyze_node import doc_case_analyze_node
# N_doc2 模板匹配节点:根据案情选择最合适的文书模板(起诉状/上诉状/合同等)。
from __004__langgraph_more_nodes.nodes.doc_template_match_node import doc_template_match_node
# N_doc3 条款填充节点:用 RAG(检索增强生成,先从知识库检索相关法条/范例,
# 再让 LLM 生成)填充模板中的各个条款。
from __004__langgraph_more_nodes.nodes.doc_clause_fill_node import doc_clause_fill_node
# N_doc4 法条校验节点:校验生成的条款引用的法条是否真实、准确;
# 若 need_refill=True 则要求回到 N_doc3 重新填充(最多重试 3 次)。
from __004__langgraph_more_nodes.nodes.doc_law_validate_node import doc_law_validate_node
# N_doc5 风险提示节点:提示文书中可能对用户不利或存在法律风险的内容。
from __004__langgraph_more_nodes.nodes.doc_risk_advisor_node import doc_risk_advisor_node
# N_doc6 类案推荐节点:检索并推荐与本案相似的既往案例,供用户参考。
from __004__langgraph_more_nodes.nodes.doc_case_recommend_node import doc_case_recommend_node
# N_doc7 最终交付节点:组装完整文书并持久化历史记录,输出最终结果。
from __004__langgraph_more_nodes.nodes.doc_final_delivery_node import doc_final_delivery_node

# ---- 独立检索节点 (案例检索/法规查询) ----
# ------------------------------------------------------------------------------
# 两个"单节点链路"节点:任务简单到不需要流水线,一个节点干完全部活直接 END。
# ------------------------------------------------------------------------------
# 案例检索节点:根据用户输入检索相似判例,结果写入 case_search_results。
from __004__langgraph_more_nodes.nodes.case_search_node import case_search_node
# 法规查询节点:根据用户输入查询法律法规条文,结果写入 law_query_results。
from __004__langgraph_more_nodes.nodes.law_query_node import law_query_node

# 小红书发布节点
# ------------------------------------------------------------------------------
# 小红书发布是一套独立的"社交媒体自动运营"流水线(需要 playwright + 登录 cookie):
# 意图识别 -> 文案生成 -> 图片生成 -> 检查 -> 自动发布 -> Markdown 存档。
# ------------------------------------------------------------------------------
# 小红书意图识别节点(N0):START 之后第一个节点,前置判断用户是否要发小红书,
# 输出 is_xiaohongshu_publish_intent 布尔字段。相当于"门卫"先问一句:
# "您是来发小红书的吗?"是 -> 走发布链路;不是 -> 进主意图路由。
from __004__langgraph_more_nodes.nodes.xiaohongshu_publish_intent_node import xiaohongshu_publish_intent_node
# 文案生成节点:生成小红书标题(xiaohongshu_title)与正文(xiaohongshu_content),
# 文案风格会贴合小红书爆款语气(emoji、话题标签等)。
from __004__langgraph_more_nodes.nodes.text_generate_node import text_generate_node
# 图片生成节点:生成配图,路径列表写入 xiaohongshu_image_path_list。
from __004__langgraph_more_nodes.nodes.image_generate_node import image_generator_node
# 文案图片检查节点:校验生成内容是否可发布(敏感词/违规图/长度限制等),
# 输出 is_can_publish_xiaohongshu 布尔字段。相当于"发稿前的三审三校"。
from __004__langgraph_more_nodes.nodes.check_text_image_node import check_text_image_node
# 自动发布节点:调用 playwright 模拟登录浏览器,把文案+图片发布到小红书平台。
from __004__langgraph_more_nodes.nodes.auto_publish_xiaohongshu_node import xiaohongshu_auto_publish_node
# Markdown 生成节点:将发布结果整理为 markdown 存档,便于后续追溯与展示。
from __004__langgraph_more_nodes.nodes.generate_markdown_node import generate_markdown_node

# 知识图谱问答节点
# ------------------------------------------------------------------------------
# 法律问答(legal_qa)是一条【知识图谱 RAG】流水线:
# 实体抽取 -> Neo4j 匹配 -> Cypher 生成/检查/执行 -> 答案生成。
# 知识图谱(Neo4j)里存着"法条-概念-案例"之间的关联关系(图结构数据),
# Cypher 是 Neo4j 的查询语言(类似 SQL 之于关系数据库)。
# ------------------------------------------------------------------------------
# 法律问答意图节点:判断是否为法律问答意图(注:实际流程由 intent_router 分流,
# 本节点在此注册,用于意图相关逻辑的完整性)。
from __004__langgraph_more_nodes.nodes.legal_qa_intent_node import legal_qa_intent_node
# 实体抽取节点:从用户问题中抽取法律实体/概念/法规名
# (如从"公司欠钱不还怎么办"抽出"欠款""公司"等实体)。
from __004__langgraph_more_nodes.nodes.extract_entity_from_user_input_node import extract_entity_from_user_input_node
# Neo4j 实体匹配节点:用抽出的实体在知识图谱中模糊匹配到图中的真实节点,
# 把"用户说的词"对齐到"图谱里的节点"。
from __004__langgraph_more_nodes.nodes.match_entity_from_neo4j_node import match_entity_from_neo4j_node
# Cypher 生成节点:基于匹配到的实体,让 LLM 生成一条 Neo4j 查询语句(Cypher)。
from __004__langgraph_more_nodes.nodes.generate_neo4j_cypher_node import generate_neo4j_cypher_node
# Cypher 校验节点:校验 Cypher 语法合法性,输出 is_all_validate_cypher 布尔字段,
# 不合法时累加 cypher_retry_count(重试计数),让流程回去重新生成。
from __004__langgraph_more_nodes.nodes.check_cypher_node import check_cypher_node
# Cypher 执行节点:在 Neo4j 上真正执行查询,返回 cypher_results(查询结果)。
from __004__langgraph_more_nodes.nodes.run_cypher_node import run_cypher_node
# 答案生成节点:基于 cypher_results 生成自然语言答案 neo4j_answer,
# 把图查询结果"翻译"成用户能看懂的话。
from __004__langgraph_more_nodes.nodes.neo4j_answer_generate_node import neo4j_answer_generate_node

# 检索链路拆分子节点（意图分解→基础层→增强查询→融合→输出）
# ------------------------------------------------------------------------------
# 检索任务被拆成了 5 个更细的节点,形成一条"检索小流水线":
# ① retrieval_intent_decompose(把检索意图拆解成若干子查询)
# ② retrieval_base_layer(基础层检索:走 FAISS 向量库等)
# ③ retrieval_enhance_query(增强查询:改写/扩展查询词提升召回)
# ④ retrieval_fusion_sort(融合排序:把多路结果合并去重排序)
# ⑤ retrieval_output(输出:整理成 citations 等最终格式)
# 这样拆分的好处:每步可单独调优、单独替换,比如换检索后端只动②。
# ------------------------------------------------------------------------------
# ① 检索意图分解节点:把用户(或上游节点)的检索意图拆成多个子查询角度。
from __004__langgraph_more_nodes.nodes.retrieval_intent_decompose_node import retrieval_intent_decompose_node
# ② 检索基础层节点:执行基础检索(向量相似度/关键词),得到粗召回结果。
from __004__langgraph_more_nodes.nodes.retrieval_base_layer_node import retrieval_base_layer_node
# ③ 检索增强查询节点:对查询做改写增强(同义词扩展/子查询合并),提高召回率。
from __004__langgraph_more_nodes.nodes.retrieval_enhance_query_node import retrieval_enhance_query_node
# ④ 检索融合排序节点:把多路粗召回结果融合、去重、按相关度排序。
from __004__langgraph_more_nodes.nodes.retrieval_fusion_sort_node import retrieval_fusion_sort_node
# ⑤ 检索输出节点:把排序后的结果整理成下游可直接使用的 citations 等字段。
from __004__langgraph_more_nodes.nodes.retrieval_output_node import retrieval_output_node

# 状态与工具
# ------------------------------------------------------------------------------
# 下面是"黑板(State)的定义"和"两个通用小工具"。
# ------------------------------------------------------------------------------
# 从 agent_state.py 导入共享状态类型 AgentState,作为 StateGraph 的【状态模式
# (State Schema)】。StateGraph(AgentState) 会基于它的字段定义自动管理状态合并:
# 节点返回的 dict 增量会被合并进全局 state,节点间就这样共享数据。
from __004__langgraph_more_nodes.agent_state import AgentState
# 路径工具函数:跨平台获取项目内文件绝对路径(避免硬编码相对路径在不同工作目录
# 下失效)。比如生成 graph.png 时要定位到项目内的输出路径,就用它。
from common.path_utils import get_file_path
# 图可视化工具:将编译后的 LangGraph 导出为 PNG 流程图,便于调试与文档展示。
# (注意:依赖 graphviz,没装时只是跳过,不影响业务)
from common.ouput_graph_utils import output_pic_graph


def build_graph():
    """
    构建法智引擎 LangGraph 状态图并编译为可执行图对象。

    作用:
        本函数是整个多智能体系统的"装配车间"。它依次完成四件事:
        1) 实例化 StateGraph,绑定状态模式 AgentState(确定"黑板"长什么样);
        2) 注册所有业务节点(add_node),将节点函数以 __name__ 为键加入图
           ("把每个小工人的工牌挂上墙");
        3) 定义边(add_edge)与条件边(add_conditional_edges),构建节点间的
           数据流("规定谁干完活把结果递给谁,以及遇到分支怎么走");
        4) 调用 compile() 将图编译为可执行对象("整条流水线最终验收"),
           供后续 invoke/ainvoke 调用。

    参数:
        无参数。所有节点函数均为模块级导入,图结构在函数内静态构建。

    返回值:
        CompiledGraph:编译后的可调用图对象,支持 .invoke(state) 同步调用
        与 .ainvoke(state) 异步调用,返回完整的最终状态字典(整块黑板)。

    可迁移性说明(对初学者理解框架精髓很有帮助):
        本函数体现的"装配模式"(注册节点 -> 加普通边 -> 加条件边 -> 编译)
        是 LangGraph 项目的通用骨架,可迁移到任何基于状态图的多智能体编排
        场景(如客服机器人、研究助手等)。条件路由函数均以 (state) -> str
        形式定义,与具体业务解耦,迁移时只需替换节点函数与路由判断逻辑。
        path_map 机制是 LangGraph 的标准用法:把路由函数返回的字符串映射到
        具体下一跳节点名,使"路由逻辑(返回什么代号)"与"图拓扑(代号去哪)"
        分离,便于维护 —— 想改路线只需改 path_map,不用动路由函数。
    """
    # 实例化 StateGraph,传入 AgentState 作为状态模式(Schema)。
    # StateGraph 会基于 AgentState 的字段定义自动管理状态合并:
    # 节点返回的 dict 增量会被合并到全局 state(黑板),后面的节点都能读到。
    # graph_builder 是"未完工的图"(构建器),compile 之后才变成可执行图。
    graph_builder = StateGraph(AgentState)

    # ==================== 注册所有节点 ====================
    # --------------------------------------------------------------------------
    # 【本节干什么】:把项目里所有"小工人"(节点函数)一一登记进图。
    # 使用的 API:graph_builder.add_node(节点名, 节点函数)。
    #   第一个参数是节点名 —— 这里统一用 函数.__name__ 属性(即函数定义时的
    #   名字),保证"注册名"和"函数名"永远一致、不会拼错;
    #   第二个参数是节点函数本身(注意是函数对象,不是调用它,所以不带括号)。
    # 注册后,这个名字就可以被 add_edge / add_conditional_edges 引用为下一跳
    # ("工牌号"被流水线图纸引用)。
    # 注意:add_node 的调用顺序【不影响】执行顺序 —— 真正决定顺序的是后面
    # 的边(edge),所以这里先全部注册,后面再统一连边。
    # --------------------------------------------------------------------------

    # ---- 核心节点注册区 ----
    # 注册意图路由节点:N1 主路由节点,负责将用户输入分流到具体业务链路。
    # 它读用户的 input,让 LLM 判断任务类型并写入 task_type 字段。
    graph_builder.add_node(intent_router_node.__name__, intent_router_node)
    # 注册文档提取节点:N2 解析上传文档为纯文本(读 uploaded_doc_path 写 doc_text)。
    graph_builder.add_node(doc_extract_node.__name__, doc_extract_node)
    # 注册合同分类节点:N3 判定合同类型(写 contract_type)。
    graph_builder.add_node(contract_classify_node.__name__, contract_classify_node)
    # 注册条款切分节点:N4 把整篇合同切分为结构化条款列表(写 doc_clauses)。
    graph_builder.add_node(clause_split_node.__name__, clause_split_node)
    # 注册数值抽取节点:从合同文本中抽取出关键数值(写 extracted_numerics)。
    graph_builder.add_node(numeric_extract_node.__name__, numeric_extract_node)
    # 注册合同审核AI节点:N5a 基于 LLM 做条款风险审查(写 contract_risk_items)。
    graph_builder.add_node(contract_ai_review_node.__name__, contract_ai_review_node)
    # 注册合规审查节点:N5b 检测合规性风险(写 compliance_risk_items)。
    graph_builder.add_node(compliance_review_node.__name__, compliance_review_node)
    # 注册冲突消解节点:N5d 当合同审核与合规审查冲突时以合规为准
    # (写 merged_risk_items / can_sign / conflict_log / presentation_order)。
    graph_builder.add_node(conflict_resolution_node.__name__, conflict_resolution_node)
    # 注册数值校验节点:N5c 校验数值合理性与一致性(写 numeric_risk_items)。
    graph_builder.add_node(numeric_validate_node.__name__, numeric_validate_node)
    # 注册法律检索节点:N6 检索相关法规与案例(写 research_context / citations)。
    graph_builder.add_node(legal_research_node.__name__, legal_research_node)
    # 注册风险聚合节点:N7 合并四路(合同/合规/数值/资信)风险并计算综合评分
    # (写 merged_risk_items / overall_risk_score / risk_level)。
    graph_builder.add_node(risk_aggregate_node.__name__, risk_aggregate_node)
    # 注册甲乙方识别节点:N8 识别合同主体(写 party_a / party_b / user_side),
    # 为后面的资信查询提供对方名称。
    graph_builder.add_node(party_identify_node.__name__, party_identify_node)
    # 注册相对方资信查询节点:N8.5 (在 party_identify 之后、risk_aggregate 之前,
    # 因为资信风险要被聚合进总评分)。调用企查查 API 查对方资信(写 credit_risk_items)。
    graph_builder.add_node(credit_check_node.__name__, credit_check_node)
    # Phase 2: 注册企查查预判定节点:N0.5 (intent_router 之后、所有业务链路之前,
    # 全局三层触发+预查复用)。所有任务类型统一先过它,把资信结果缓存进 State。
    graph_builder.add_node(credit_precheck_node.__name__, credit_precheck_node)
    # 注册最终交付节点:N9 组装最终报告(写 final_report_markdown / output)。
    graph_builder.add_node(final_delivery_node.__name__, final_delivery_node)
    # 注册 LLM 直接输出节点:other 任务的兜底回答节点(直接 LLM 回答)。
    graph_builder.add_node(llm_direct_out_node.__name__, llm_direct_out_node)

    # ---- 小红书节点注册区 ----
    # 注册小红书意图识别节点:START 后第一个节点,做前置意图过滤
    # (写 is_xiaohongshu_publish_intent)。
    graph_builder.add_node(xiaohongshu_publish_intent_node.__name__, xiaohongshu_publish_intent_node)
    # 注册文案生成节点:生成小红书标题与正文(写 xiaohongshu_title / xiaohongshu_content)。
    graph_builder.add_node(text_generate_node.__name__, text_generate_node)
    # 注册图片生成节点:生成小红书配图(写 xiaohongshu_image_path_list)。
    graph_builder.add_node(image_generator_node.__name__, image_generator_node)
    # 注册文案图片检查节点:校验内容是否可发布(写 is_can_publish_xiaohongshu)。
    graph_builder.add_node(check_text_image_node.__name__, check_text_image_node)
    # 注册自动发布节点:调用 playwright 自动发布到小红书平台。
    graph_builder.add_node(xiaohongshu_auto_publish_node.__name__, xiaohongshu_auto_publish_node)
    # 注册 Markdown 生成节点:整理发布结果为 markdown 存档。
    graph_builder.add_node(generate_markdown_node.__name__, generate_markdown_node)

    # ---- 知识图谱问答节点注册区 ----
    # 注册法律问答意图节点(意图判定相关,实际分流由 intent_router 完成)。
    graph_builder.add_node(legal_qa_intent_node.__name__, legal_qa_intent_node)
    # 注册实体抽取节点:从用户问题抽取法律实体(写 extracted_entities)。
    graph_builder.add_node(extract_entity_from_user_input_node.__name__, extract_entity_from_user_input_node)
    # 注册 Neo4j 实体匹配节点:在知识图谱中匹配实体(写 matched_entities)。
    graph_builder.add_node(match_entity_from_neo4j_node.__name__, match_entity_from_neo4j_node)
    # 注册 Cypher 生成节点:生成 Neo4j 查询语句(写 generated_cypher)。
    graph_builder.add_node(generate_neo4j_cypher_node.__name__, generate_neo4j_cypher_node)
    # 注册 Cypher 校验节点:校验 Cypher 合法性(写 is_all_validate_cypher /
    # cypher_retry_count)。
    graph_builder.add_node(check_cypher_node.__name__, check_cypher_node)
    # 注册 Cypher 执行节点:执行 Neo4j 查询(写 cypher_results)。
    graph_builder.add_node(run_cypher_node.__name__, run_cypher_node)
    # 注册答案生成节点:基于查询结果生成自然语言答案(写 neo4j_answer)。
    graph_builder.add_node(neo4j_answer_generate_node.__name__, neo4j_answer_generate_node)

    # ---- 检索链路拆分子节点注册区 ----
    # 注册 ① 检索意图分解节点。
    graph_builder.add_node(retrieval_intent_decompose_node.__name__, retrieval_intent_decompose_node)
    # 注册 ② 检索基础层节点。
    graph_builder.add_node(retrieval_base_layer_node.__name__, retrieval_base_layer_node)
    # 注册 ③ 检索增强查询节点。
    graph_builder.add_node(retrieval_enhance_query_node.__name__, retrieval_enhance_query_node)
    # 注册 ④ 检索融合排序节点。
    graph_builder.add_node(retrieval_fusion_sort_node.__name__, retrieval_fusion_sort_node)
    # 注册 ⑤ 检索输出节点。
    graph_builder.add_node(retrieval_output_node.__name__, retrieval_output_node)

    # ---- 法律文书生成链路节点注册区 (N_doc1 ~ N_doc7) ----
    # 注册 N_doc1 案情分析节点。
    graph_builder.add_node(doc_case_analyze_node.__name__, doc_case_analyze_node)
    # 注册 N_doc2 模板匹配节点。
    graph_builder.add_node(doc_template_match_node.__name__, doc_template_match_node)
    # 注册 N_doc3 条款填充节点。
    graph_builder.add_node(doc_clause_fill_node.__name__, doc_clause_fill_node)
    # 注册 N_doc4 法条校验节点。
    graph_builder.add_node(doc_law_validate_node.__name__, doc_law_validate_node)
    # 注册 N_doc5 风险提示节点。
    graph_builder.add_node(doc_risk_advisor_node.__name__, doc_risk_advisor_node)
    # 注册 N_doc6 类案推荐节点。
    graph_builder.add_node(doc_case_recommend_node.__name__, doc_case_recommend_node)
    # 注册 N_doc7 最终交付节点。
    graph_builder.add_node(doc_final_delivery_node.__name__, doc_final_delivery_node)

    # ---- 独立检索节点注册区 (案例检索 / 法规查询) ----
    # 注册案例检索节点:单节点链路。
    graph_builder.add_node(case_search_node.__name__, case_search_node)
    # 注册法规查询节点:单节点链路。
    graph_builder.add_node(law_query_node.__name__, law_query_node)

    # ==================== 边: START -> 小红书意图识别 ====================
    # --------------------------------------------------------------------------
    # 【本节干什么】:确定整张图的【入口】。
    # 添加图的入口边:从 START 哨兵节点指向小红书意图识别节点。
    # add_edge(起点, 终点) 的参数是节点名;START 是入口哨兵常量。
    # 这样设计意味着:所有请求都会【先】经过小红书意图过滤,再做主路由分流。
    # 这属于"前置过滤"架构模式:把特殊业务(发小红书)在最前面拦下来,
    # 不让它进入复杂的法律任务路由,降低主路由的负担。
    # --------------------------------------------------------------------------
    graph_builder.add_edge(START, xiaohongshu_publish_intent_node.__name__)

    # 小红书意图 -> 条件路由
    # --------------------------------------------------------------------------
    # 【本节干什么】:定义小红书意图识别节点之后的【第一个条件分支】。
    # 定义条件路由函数:根据小红书意图识别节点的输出决定下一跳。
    # 该函数签名固定为 (state: AgentState) -> str:
    #   - 入参 state:整块黑板(所有节点共享的状态);
    #   - 返回值 str:一个"路线代号",由下方 path_map 查表映射成具体节点名。
    # 【它检查什么】:检查 state 里的 is_xiaohongshu_publish_intent 布尔字段
    # (由 xiaohongshu_publish_intent_node 写入)。
    # 【可能路径】:
    #   True  -> 返回 "publish_xiaohongshu_intent" -> 去文案生成节点(小红书链路);
    #   False -> 返回 "intent_router"              -> 去主意图路由节点(常规法律任务)。
    # 【为什么这样设计】:把"发小红书"这种非法律任务在入口处就分流,
    # 既保证小红书链路独立完整,又不污染主法律流程。
    # --------------------------------------------------------------------------
    def is_xiaohongshu_publish_intent(state: AgentState):
        # 从 state 读取 is_xiaohongshu_publish_intent 布尔字段
        # (由 xiaohongshu_publish_intent_node 写入)。
        # state.get 在字段不存在时返回 None(假值),保证容错性 ——
        # 即使某次运行该节点没写入字段,也不会抛 KeyError,而是按"非小红书"处理。
        if state.get("is_xiaohongshu_publish_intent"):
            # 若识别为小红书发布意图(字段为真值),
            # 返回 "publish_xiaohongshu_intent" 路线代号,
            # 后续由 path_map 映射到文案生成节点。
            return "publish_xiaohongshu_intent"
        else:
            # 否则(字段为假或缺失)返回 "intent_router" 路线代号,
            # 进入主意图路由节点处理常规法律任务。
            return "intent_router"

    # 添加条件边:从小红书意图识别节点出发,根据 is_xiaohongshu_publish_intent
    # 函数(注意:这里传的是函数对象,不带括号,由 LangGraph 在运行时调用它)
    # 的返回值路由。path_map 参数将路由函数返回的字符串映射到具体节点名,
    # 使"路由逻辑(返回什么代号)"与"图拓扑(代号去哪)"解耦,便于维护。
    graph_builder.add_conditional_edges(
        xiaohongshu_publish_intent_node.__name__,  # 条件边的起点节点(工牌号)
        is_xiaohongshu_publish_intent,             # 路由判定函数(运行时自动调用)
        path_map={                                 # 路由返回值 -> 下一跳节点名的映射
            "publish_xiaohongshu_intent": text_generate_node.__name__,  # 走小红书文案生成
            "intent_router": intent_router_node.__name__,               # 走主意图路由
        }
    )

    # ==================== 小红书发布链路 ====================
    # --------------------------------------------------------------------------
    # 【本节干什么】:连接小红书发布链路的后续节点:
    # 文案生成 -> 图片生成 -> 检查 -> (条件) 自动发布 -> Markdown -> END。
    # 这条链是"内容生产流水线":先写文案,再配图,再质检,合格才发布。
    # --------------------------------------------------------------------------
    # 添加普通边:文案生成 -> 图片生成(顺序执行)。
    # 这两个节点之间存在数据依赖:图片生成可能需要参考文案内容
    # (如根据文案主题生成配图),所以必须严格先后执行。
    graph_builder.add_edge(text_generate_node.__name__, image_generator_node.__name__)
    # 添加普通边:图片生成 -> 文案图片检查(顺序执行)。
    # 检查节点会综合校验标题/正文/图片是否符合发布要求(敏感词/违规图等)。
    graph_builder.add_edge(image_generator_node.__name__, check_text_image_node.__name__)

    # 定义检查节点的条件路由函数:决定是否进入自动发布环节。
    # --------------------------------------------------------------------------
    # 【它检查什么】:检查 state 里的 is_can_publish_xiaohongshu 布尔字段
    # (由 check_text_image_node 写入)。
    # 【可能路径】:
    #   True  -> 返回 "publish_xiaohongshu" -> 去自动发布节点(内容合格,发出去);
    #   False -> 返回 END 哨兵常量          -> 直接结束整张图(内容不合格,不发布)。
    # 【为什么这样设计】:质检是发布前的最后一道闸门。不合格的内容绝不能发出去
    # (可能违规封号),所以直接走 END 终止,不给发布机会。
    # --------------------------------------------------------------------------
    def check_text_image_router(state: AgentState):
        # 读取 is_can_publish_xiaohongshu 字段(check_text_image_node 写入)。
        # 为 True 表示文案图片检查通过,可以发布。
        if state.get("is_can_publish_xiaohongshu"):
            # 返回 "publish_xiaohongshu" 路线代号,后续映射到自动发布节点。
            return "publish_xiaohongshu"
        else:
            # 检查不通过直接结束流程(返回 END 哨兵常量)。
            # 注意:END 同时作为 route 返回值和 path_map 的 key,这是 LangGraph
            # 支持的特殊用法 —— 路由函数可以直接返回 END 表示"到此为止"。
            return END

    # 添加条件边:从检查节点出发,根据 check_text_image_router 路由。
    graph_builder.add_conditional_edges(
        check_text_image_node.__name__,     # 条件边起点(检查节点)
        check_text_image_router,            # 路由判定函数
        path_map={                          # 路由映射
            "publish_xiaohongshu": xiaohongshu_auto_publish_node.__name__,  # 通过则自动发布
            END: END,                                                       # 不通过则直接结束
        }
    )
    # 添加普通边:自动发布 -> Markdown 生成。
    # 发布完成后生成 markdown 存档,便于后续追溯与展示
    # (把"发出去的帖子"留一份文字档案)。
    graph_builder.add_edge(xiaohongshu_auto_publish_node.__name__, generate_markdown_node.__name__)
    # 添加普通边:Markdown 生成 -> END(小红书链路结束)。
    # 存档完成后整条小红书链路走完,到达图的出口。
    graph_builder.add_edge(generate_markdown_node.__name__, END)

    # ==================== 法智引擎核心链路 ====================
    # --------------------------------------------------------------------------
    # 【本节干什么】:搭建主意图路由(N1)以及 Phase 2 的企查查预判定(N0.5)
    # 两段条件路由,把常规法律任务正确送到各自链路的真实起点。
    # --------------------------------------------------------------------------

    # N1 意图路由 -> 条件路由
    # 定义主路由的条件判定函数:根据 task_type 将流程分流到各业务链路或兜底链路。
    # --------------------------------------------------------------------------
    # 【它检查什么】:检查 state 里的 task_type 字段(intent_router_node 通过
    # LLM 分类用户输入后写入)。
    # 【可能路径】(每种 task_type 对应一个路线代号):
    #   "contract_review"      -> "contract_review_path"      (合同审核:完整流程)
    #   "compliance_review"    -> "compliance_review_path"    (合规审查:精简流程)
    #   "legal_research"       -> "legal_research_path"       (法律检索:直接检索)
    #   "legal_qa"             -> "legal_qa_path"             (法律问答:知识图谱RAG)
    #   "legal_document_gen"   -> "doc_gen_path"              (文书生成:七节点链路)
    #   "case_search"          -> "case_search_path"          (案例检索:单节点)
    #   "law_query"            -> "law_query_path"            (法规查询:单节点)
    #   其他任何值(含默认 "other")-> "llm_direct"            (LLM 直接回答兜底)
    # 【为什么这样设计】:任务类型决定了用户要办什么"业务",不同业务需要完全
    # 不同的流水线,所以这里必须先分流。注意:Phase 2 设计下,这里所有分支的
    # path_map 都指向同一个 credit_precheck_node(统一预判定),真正的二次分流
    # 在 after_credit_precheck_router 里完成 —— 这样所有链路都能共享企查查预查。
    # --------------------------------------------------------------------------
    def intent_router_router(state: AgentState):
        # 从 state 读取 task_type 字段,默认值 "other" 防止字段缺失导致的
        # KeyError(即 state.get("task_type", "other") 的第二参数就是默认值)。
        # task_type 由 intent_router_node 通过 LLM 分类后写入。
        task_type = state.get("task_type", "other")
        # 以下 if-elif 链对应各业务分支,每条返回一个路由标识符(路线代号),
        # 由 path_map 映射到下一跳节点。
        if task_type == "contract_review":
            # 合同审核:走完整流程
            # (文档提取->甲乙方识别->合同分类->条款切分->数值抽取->检索5节点
            #  ->合同审核AI->合规审查->数值校验->资信查询->风险聚合->最终交付)。
            return "contract_review_path"
        elif task_type == "compliance_review":
            # 合规审查:走精简流程
            # (文档提取->合规审查->数值校验->检索->风险聚合->交付)。
            return "compliance_review_path"
        elif task_type == "legal_research":
            # 法律检索:直接进入法律检索链路(无需文档预处理,
            # 用户可能只是问"某法条怎么说",没有合同要审)。
            return "legal_research_path"
        elif task_type == "legal_qa":
            # 法律问答:进入知识图谱 RAG 链路
            # (实体抽取->Neo4j匹配->Cypher生成/校验/执行->答案生成)。
            return "legal_qa_path"
        elif task_type == "legal_document_gen":
            # 法律文书生成:进入文书生成链路
            # (案情分析->模板匹配->条款填充->法条校验->风险提示->类案推荐->交付)。
            return "doc_gen_path"
        elif task_type == "case_search":
            # 案例检索:直接进入案例检索节点(单节点链路)。
            return "case_search_path"
        elif task_type == "law_query":
            # 法规查询:直接进入法规查询节点(单节点链路)。
            return "law_query_path"
        else:
            # 其他意图(包括 "other" 与未能识别的任务类型):走 LLM 直接回答
            # 兜底链路 —— 用户问什么,LLM 就直接答什么,不套任何流程。
            return "llm_direct"

    # Phase 2: intent_router 之后先统一进入 credit_precheck_node (企查查预判定),
    # 再由 credit_precheck_node 按 task_type 二次路由到各链路真实起点。
    # 这样 contract_review / compliance_review / legal_research / legal_qa / other
    # 五条链路都能共享同一套「三层触发」企查查判定+预查逻辑
    # (把对方公司资信提前查好并缓存进 State,下游要用时直接读,不用重复查)。
    graph_builder.add_conditional_edges(
        intent_router_node.__name__,  # 条件边起点(N1 主意图路由)
        intent_router_router,         # 路由判定函数(读 task_type 返回路线代号)
        path_map={                    # 路由返回值 -> 统一进入 credit_precheck
            "contract_review_path": credit_precheck_node.__name__,
            "compliance_review_path": credit_precheck_node.__name__,
            "legal_research_path": credit_precheck_node.__name__,
            "legal_qa_path": credit_precheck_node.__name__,
            "doc_gen_path": credit_precheck_node.__name__,
            "case_search_path": credit_precheck_node.__name__,
            "law_query_path": credit_precheck_node.__name__,
            "llm_direct": credit_precheck_node.__name__,
        }
    )

    # Phase 2: credit_precheck_node 二次路由 -> 各链路真实起点
    # --------------------------------------------------------------------------
    # 【它检查什么】:直接读取 state["task_type"],与 intent_router_router 的
    # 判定结果保持一致(即用同一个字段做两次判定:第一次决定"先统一去预判定",
    # 第二次决定"预判定之后各自去哪个真实起点")。
    # 【可能路径】(task_type -> 真实起点节点):
    #   "contract_review" / "compliance_review" -> "doc_extract"(都要先读文档)
    #   "legal_research"  -> "retrieval_intent_decompose"(直接进检索5节点链)
    #   "legal_qa"        -> "extract_entity"(直接进知识图谱RAG链)
    #   "legal_document_gen" -> "doc_case_analyze"(文书生成不需要读上传文档,
    #        直接从用户输入结构化案情,所以先走案情分析)
    #   "case_search"     -> "case_search"(案例检索单节点)
    #   "law_query"       -> "law_query"(法规查询单节点)
    #   其他(含 "other") -> "llm_direct_out"(LLM 直接回答)
    # 【为什么这样设计】:把"统一预判定"和"业务分流"拆成两段条件路由,
    # 是【中间节点 + 二次路由】的经典模式:credit_precheck_node 像一座
    # "中转站",所有任务都经过它(共享资信预查),再由它分发到不同出口。
    # --------------------------------------------------------------------------
    def after_credit_precheck_router(state: AgentState):
        # 读取 task_type(默认 "other"),与前面 intent_router_router 一致。
        task_type = state.get("task_type", "other")
        # 合同审核与合规审查都需要先读取上传文档,所以共用一个起点 doc_extract。
        if task_type in ("contract_review", "compliance_review"):
            return "doc_extract"
        # 法律检索不需要读文档,直接进入检索5节点链的入口
        # (检索意图分解节点)。
        elif task_type == "legal_research":
            return "retrieval_intent_decompose"
        # 法律问答直接进入知识图谱链的入口(实体抽取节点)。
        elif task_type == "legal_qa":
            return "extract_entity"
        # 文书生成:不需要文档提取(直接从用户输入结构化案情),先走案情分析。
        elif task_type == "legal_document_gen":
            return "doc_case_analyze"
        # 案例检索:直接进入案例检索节点。
        elif task_type == "case_search":
            return "case_search"
        # 法规查询:直接进入法规查询节点。
        elif task_type == "law_query":
            return "law_query"
        else:  # 其他(含 "other")
            # LLM 直接回答兜底。
            return "llm_direct_out"

    # 添加条件边:从 credit_precheck_node 出发,按 after_credit_precheck_router
    # 的返回值路由到各链路真实起点。
    graph_builder.add_conditional_edges(
        credit_precheck_node.__name__,          # 条件边起点(企查查预判定节点)
        after_credit_precheck_router,           # 路由判定函数
        path_map={                              # 路线代号 -> 下一跳节点
            "doc_extract": doc_extract_node.__name__,                            # 读文档(合同/合规审查)
            "retrieval_intent_decompose": retrieval_intent_decompose_node.__name__,  # 检索5节点链入口(法律检索)
            "extract_entity": extract_entity_from_user_input_node.__name__,      # 知识图谱链入口(法律问答)
            "doc_case_analyze": doc_case_analyze_node.__name__,                  # 文书生成链入口
            "case_search": case_search_node.__name__,                            # 案例检索
            "law_query": law_query_node.__name__,                                # 法规查询
            "llm_direct_out": llm_direct_out_node.__name__,                      # LLM 直接回答兜底
        }
    )

    # 添加普通边:LLM 直接输出 -> END(兜底链路直接结束)。
    # 其他类型任务由 LLM 答完就直接到达图的出口,不再经过任何业务节点。
    graph_builder.add_edge(llm_direct_out_node.__name__, END)

    # ==================== 合同审核链路(N2->N8->N3->N4->数值抽取->检索5节点->N5a(有法条)->N5b(有法规)->N5c(有阈值)->N8.5->N7->N9) ====================
    # --------------------------------------------------------------------------
    # 【本节干什么】:连接合同审核(以及合规审查复用前半段)的主链路,
    # 这是全项目最长、最完整的一条流水线。下面每条 add_edge 都是"上一步的
    # 输出是下一步的输入"这样的数据依赖关系。
    # 完整顺序:
    #   N2文档提取 -> N8甲乙方识别 -> N3合同分类 -> N4条款切分 -> 数值抽取
    #   -> 检索5节点(意图分解→基础层→增强查询→融合排序→输出)
    #   -> N5a合同审核AI(此时已有法条 citations,可"引经据典")
    #   -> N5b合规审查(有法规支撑)
    #   -> N5c数值校验(可比对法定阈值)
    #   -> N8.5资信查询 -> N7风险聚合 -> N9最终交付 -> END
    # --------------------------------------------------------------------------
    # 文档提取 -> 甲乙方识别(N2 -> N8 前置):
    # party_identify 前移到 doc_extract 之后,使 contract_ai_review_node 能拿到
    # 真实 user_side 做"立场化审核"(站在用户这一方挑风险),而不是默认 "Unknown"。
    graph_builder.add_edge(doc_extract_node.__name__, party_identify_node.__name__)
    # 甲乙方识别 -> 合同分类(N8 -> N3):
    # 识别完主体后再做合同类型分类 —— 分类时可能参考当事人信息。
    graph_builder.add_edge(party_identify_node.__name__, contract_classify_node.__name__)
    # 合同分类 -> 条款切分(N3 -> N4):
    # 先知道是什么合同(买卖/租赁…),再按该类型合同的常见结构切分条款。
    graph_builder.add_edge(contract_classify_node.__name__, clause_split_node.__name__)
    # 条款切分 -> 数值抽取(N4 -> 数值抽取):
    # 条款切好后才能从各条款中抽取数值(单价/数量/总价/期限等)。
    graph_builder.add_edge(clause_split_node.__name__, numeric_extract_node.__name__)
    # ========== [优化] 检索智能体提前到 AI 审核之前 ==========
    # 新顺序(经过调优):数值抽取 → 检索智能体(5节点) → 合同审核AI(有法条依据)
    #        → 合规审查(有法规支撑) → 数值校验(可比对法定阈值)
    #        → 资信查询 → 风险聚合 → 最终交付。
    # 核心思路:【先检索、后审核】 —— 让审核 AI 手里先有法条(citations),
    # 再逐条审查,这样 AI 挑出的风险才有法律依据,而不是凭空说"有风险"。
    #
    # 数值抽取 -> 检索意图分解(N4 -> 检索①,提前获取 citations)。
    graph_builder.add_edge(numeric_extract_node.__name__, retrieval_intent_decompose_node.__name__)
    # 检索 5 节点链(保持原样):① 意图分解 -> ② 基础层(向量检索/关键词)。
    graph_builder.add_edge(retrieval_intent_decompose_node.__name__, retrieval_base_layer_node.__name__)
    # ② 基础层 -> ③ 增强查询(改写/扩展查询词提升召回)。
    graph_builder.add_edge(retrieval_base_layer_node.__name__, retrieval_enhance_query_node.__name__)
    # ③ 增强查询 -> ④ 融合排序(多路结果合并去重排序)。
    graph_builder.add_edge(retrieval_enhance_query_node.__name__, retrieval_fusion_sort_node.__name__)
    # ④ 融合排序 -> ⑤ 检索输出(整理成 citations 等下游可用字段)。
    graph_builder.add_edge(retrieval_fusion_sort_node.__name__, retrieval_output_node.__name__)

    # ============================================================
    # 检索输出 -> 条件分支（按 task_type 路由到不同审查线路）
    # ============================================================
    # 【法律实务】
    # - contract_review: 需要合同审核AI(商业风险) + 合规审查(法律合规)
    # - compliance_review: 只需要合规审查(独立法律合规评估)
    # - legal_research/other: 跳过所有审查,直接进入冲突消解(空结果通行)
    #
    # 分支架构取代了旧架构中"所有任务都走同一串行链"的设计,
    # 确保合规审查独立执行,不受合同审核立场的影响。

    def after_retrieval_router(state: AgentState):
        """检索5节点后的条件路由:按 task_type 分流到不同审查线路"""
        task_type = state.get("task_type", "")
        if task_type == "contract_review":
            return "contract_review"          # 合同审核:进合同审核AI
        elif task_type == "compliance_review":
            return "compliance_review"        # 合规审查:直接进合规审查(跳过合同审核AI)
        else:
            return "skip_review"             # 其他(legal_research等):跳过所有审查

    graph_builder.add_conditional_edges(
        retrieval_output_node.__name__,       # 条件边起点(检索输出节点)
        after_retrieval_router,               # 路由判定函数
        path_map={
            "contract_review": contract_ai_review_node.__name__,    # -> 合同审核AI
            "compliance_review": compliance_review_node.__name__,   # -> 合规审查(独立)
            "skip_review": conflict_resolution_node.__name__,       # -> 冲突消解(跳过审查)
        }
    )

    # 合同审核AI -> 合规审查(N5a -> N5b,串行):
    #   只有 contract_review 任务会走到这里。
    #   合同审核AI先做商业风险评估,然后合规审查复核其合规性。
    #   这就是"合同审核需要调用合规审查"的实现——并非跳过,而是先审商业再审合规。
    graph_builder.add_edge(contract_ai_review_node.__name__, compliance_review_node.__name__)

    # 合规审查 -> 冲突消解(N5b -> N5d【新增】):
    #   无论来自 contract_review 还是 compliance_review 线路,
    #   合规审查结果都要经过冲突消解节点。
    #   冲突消解应用"合规优先原则"合并合同与合规两路风险。
    graph_builder.add_edge(compliance_review_node.__name__, conflict_resolution_node.__name__)

    # 冲突消解 -> 数值校验(N5d -> N5c):
    #   合并后的风险项进入数值校验环节。
    #   此时merged_risk_items已有合规vs合同的冲突消解结论。
    graph_builder.add_edge(conflict_resolution_node.__name__, numeric_validate_node.__name__)
    # 数值校验 -> 相对方资信查询(party_identify 已前置到 doc_extract 之后,
    # 此处 credit_check 可直接读取 state 中的 party_a / party_b / user_side,
    # 无需再经过 party_identify —— 名字早已在黑板上了)。
    graph_builder.add_edge(numeric_validate_node.__name__, credit_check_node.__name__)
    # 资信查询 -> 风险聚合(N8.5 -> N7:把第 4 路资信风险项 credit_risk_items
    # 交给聚合节点,与合同/合规/数值三路风险统一评分)。
    graph_builder.add_edge(credit_check_node.__name__, risk_aggregate_node.__name__)
    # 风险聚合 -> 最终交付(N7 -> N9:含 4 路风险的综合评分驱动报告生成)。
    graph_builder.add_edge(risk_aggregate_node.__name__, final_delivery_node.__name__)
    # 最终交付 -> END(N9 -> 结束:报告生成完毕,整张图到达出口)。
    graph_builder.add_edge(final_delivery_node.__name__, END)

    # 法律检索/合规审查等任务在冲突消解节点中无风险项时直接通行,
    # 但仍经过数值校验/资信查询/风险聚合/最终交付等尾部节点。
    # 这条路径上的节点会检查 state 中的相关字段是否为空,为空则跳过处理。

    # 法律检索路径: legal_research -> final_delivery
    # 但 legal_research_node 后已经有边到 risk_aggregate,这里需要条件判断
    # 为简化: legal_research 路径也走 final_delivery(在 risk_aggregate 中
    # 无风险时评分 95)—— 上面的边已覆盖,这里仅作设计说明,不重复连边。

    # ==================== 知识图谱问答链路 ====================
    # --------------------------------------------------------------------------
    # 【本节干什么】:连接法律问答(知识图谱 RAG)链路的节点,并实现
    # "Cypher 校验失败最多重试 3 次"的重试环。
    # 链路:实体抽取 -> Neo4j 实体匹配 -> Cypher 生成 -> Cypher 校验
    #       ->(通过)Cypher 执行 -> 答案生成 -> END
    #       ->(未通过且未达上限)回 Cypher 生成(循环)
    #       ->(未通过且达上限)直接答案生成(降级回答)
    # --------------------------------------------------------------------------
    # 实体抽取 -> Neo4j 实体匹配:
    # 先把用户问题里的实体抽出来,再到知识图谱里匹配对应节点。
    graph_builder.add_edge(extract_entity_from_user_input_node.__name__, match_entity_from_neo4j_node.__name__)
    # Neo4j 实体匹配 -> Cypher 生成:
    # 匹配到图谱实体后,让 LLM 基于这些实体编写 Neo4j 查询语句(Cypher)。
    graph_builder.add_edge(match_entity_from_neo4j_node.__name__, generate_neo4j_cypher_node.__name__)
    # Cypher 生成 -> Cypher 校验:
    # LLM 生成的 Cypher 不一定合法,必须经过语法校验才能执行。
    graph_builder.add_edge(generate_neo4j_cypher_node.__name__, check_cypher_node.__name__)

    # 定义 Cypher 校验后的路由函数:实现"校验失败重试最多 3 次"的循环控制。
    # --------------------------------------------------------------------------
    # 【它检查什么】:检查两个字段:
    #   1) is_all_validate_cypher(布尔):Cypher 是否通过语法校验(check_cypher_node 写入);
    #   2) cypher_retry_count(整数):已经重试过几次(check_cypher_node 在每次
    #      校验失败时累加)。
    # 【可能路径】(优先级从高到低):
    #   ① 校验通过          -> "run_cypher"          -> 执行节点(正常查询);
    #   ② 未通过且重试≥3次  -> "generate_answer"     -> 直接生成答案(放弃图查询);
    #   ③ 未通过且重试<3次  -> "generate_neo4j_cypher" -> 回到生成节点(重试循环)。
    # 【为什么这样设计】:LLM 写代码(这里是 Cypher)可能写错,但"一次写错就
    # 放弃"太可惜,所以给 3 次重试机会(有限重试,防死循环);若 3 次都不行,
    # 说明问题本身不适合图查询,就降级为基于已有实体匹配结果直接回答,
    # 保证用户【总能拿到一个答案】而不是报错。
    # --------------------------------------------------------------------------
    def check_cypher_router(state: AgentState):
        # 优先判断 Cypher 是否通过校验(由 check_cypher_node 写入
        # is_all_validate_cypher 字段;state.get 读不到时返回 None 即假值)。
        if state.get("is_all_validate_cypher"):
            # 通过校验,返回 "run_cypher" 路线代号,进入执行节点真正查 Neo4j。
            return "run_cypher"
        # 若未通过校验,检查重试次数是否已达上限。
        # state.get 第二个参数 0 提供默认值:cypher_retry_count 字段不存在时
        # 按 0 次处理(还没重试过);该计数由 check_cypher_node 在每次重试时累加。
        elif state.get("cypher_retry_count", 0) >= 3:
            # 重试达上限(≥3 次)仍失败:放弃图查询,直接走答案生成
            # (可能基于已有实体匹配结果降级回答)。
            return "generate_answer"
        else:
            # 未达上限:返回 "generate_neo4j_cypher" 路线代号,
            # 回到 Cypher 生成节点重新生成 —— 这条"回去的边"就构成了循环。
            return "generate_neo4j_cypher"

    # 添加条件边:从 Cypher 校验节点出发,根据 check_cypher_router 路由
    # (支持循环与终止两条出路)。
    graph_builder.add_conditional_edges(
        check_cypher_node.__name__,       # 条件边起点(Cypher 校验节点)
        check_cypher_router,              # 路由判定函数
        path_map={                        # 路由映射
            "run_cypher": run_cypher_node.__name__,                       # 通过校验 -> 执行查询
            "generate_neo4j_cypher": generate_neo4j_cypher_node.__name__, # 未通过 -> 重新生成(循环)
            "generate_answer": neo4j_answer_generate_node.__name__,       # 重试上限 -> 直接生成答案
        }
    )
    # 添加普通边:Cypher 执行 -> 答案生成(查询完成后,把 cypher_results 加工成
    # 自然语言答案 neo4j_answer)。
    graph_builder.add_edge(run_cypher_node.__name__, neo4j_answer_generate_node.__name__)
    # 添加普通边:答案生成 -> END(法律问答链路结束)。
    graph_builder.add_edge(neo4j_answer_generate_node.__name__, END)

    # ==================== 法律文书生成链路 ====================
    # --------------------------------------------------------------------------
    # 【本节干什么】:连接法律文书生成的 7 个串联节点(N_doc1 ~ N_doc7),
    # 并实现"法条校验失败最多重试 3 次"的第二个重试环。
    # 链路:案情分析 -> 模板匹配 -> 条款填充(RAG) -> 法条校验(含重试环)
    #       -> 风险提示 -> 类案推荐 -> 最终交付 -> END
    # --------------------------------------------------------------------------
    # 案情分析 -> 模板匹配(N_doc1 → N_doc2):
    # 先结构化抽取案情要素,再据此选择最合适的文书模板。
    graph_builder.add_edge(doc_case_analyze_node.__name__, doc_template_match_node.__name__)
    # 模板匹配 -> 条款填充(N_doc2 → N_doc3):
    # 选定模板后,用 RAG(检索增强生成)逐条填充模板条款。
    graph_builder.add_edge(doc_template_match_node.__name__, doc_clause_fill_node.__name__)
    # 条款填充 -> 法条校验(N_doc3 → N_doc4):
    # 填充完的条款要校验引用的法条是否真实、准确。
    graph_builder.add_edge(doc_clause_fill_node.__name__, doc_law_validate_node.__name__)

    # 法条校验后的条件路由:若 need_refill=True 且重试未达上限,回退到条款填充
    # 重新生成;否则进入风险提示节点(最多重试 3 次,防止死循环)。
    # --------------------------------------------------------------------------
    # 【它检查什么】:检查两个字段:
    #   1) need_refill(布尔):是否需要重新填充(doc_law_validate_node 写入);
    #   2) doc_retry_count(整数):文书链路已经重试过几次(默认 0)。
    # 【可能路径】:
    #   ① need_refill 为真 且 重试次数 < 3 -> "refill"  -> 回条款填充(重试环);
    #   ② 其余情况                        -> "proceed" -> 进风险提示(继续前进)。
    # 【为什么这样设计】:与 Cypher 重试环同理 —— LLM 填条款可能引用错法条,
    # 给有限次重试(≤3 次)让生成环节自我修正;超过 3 次说明反复修正无果,
    # 直接带着现有结果继续往下走(宁可提示风险,也不无限循环卡死)。
    # --------------------------------------------------------------------------
    def doc_law_validate_router(state):
        # 条件:需要重新填充 且 重试次数未达上限(< 3)。
        # state.get("need_refill") 读不到时返回 None(假值,不会误触发重试);
        # state.get("doc_retry_count", 0) 读不到时按 0 次计(还没重试过)。
        if state.get("need_refill") and state.get("doc_retry_count", 0) < 3:
            return "refill"          # 重试:回到条款填充节点(形成重试环)
        return "proceed"             # 继续:进入风险提示节点

    # 添加条件边:从法条校验节点出发,按 doc_law_validate_router 路由。
    graph_builder.add_conditional_edges(
        doc_law_validate_node.__name__,   # 条件边起点(法条校验节点)
        doc_law_validate_router,          # 路由判定函数
        path_map={                        # 路由映射
            "refill": doc_clause_fill_node.__name__,        # 需要重填 -> 回条款填充
            "proceed": doc_risk_advisor_node.__name__,      # 校验 OK -> 进风险提示
        }
    )
    # 风险提示 -> 类案推荐(N_doc5 → N_doc6,串行模拟并行 —— 因为 compat
    # 兼容层不支持真并行,所以两个本可并行的节点按顺序执行)。
    graph_builder.add_edge(doc_risk_advisor_node.__name__, doc_case_recommend_node.__name__)
    # 类案推荐 -> 最终交付(N_doc6 → N_doc7):
    # 推荐完类案后,组装完整文书并输出。
    graph_builder.add_edge(doc_case_recommend_node.__name__, doc_final_delivery_node.__name__)
    # 最终交付 -> END(文书生成链路结束)。
    graph_builder.add_edge(doc_final_delivery_node.__name__, END)

    # ==================== 独立检索链路(案例检索/法规查询) ====================
    # --------------------------------------------------------------------------
    # 【本节干什么】:给两个"单节点链路"收尾。
    # 案例检索是单节点链路:由 credit_precheck 条件路由直接进入 case_search_node,
    # 执行完后直接 END(不需要后续处理),结果写在 case_search_results 字段。
    # 法规查询同理:单节点,直接结果写入 law_query_results 字段。
    # --------------------------------------------------------------------------
    # 案例检索节点执行完 -> END(单节点链路,一步到位)。
    graph_builder.add_edge(case_search_node.__name__, END)
    # 法规查询节点执行完 -> END(单节点链路,一步到位)。
    graph_builder.add_edge(law_query_node.__name__, END)

    # ==================== 编译 ====================
    # --------------------------------------------------------------------------
    # 【本节干什么】:把"节点 + 边"组装好的图构建器编译成可执行对象。
    # 调用 compile() 将图构建器编译为可执行图对象。
    # 编译过程会校验图拓扑(如:边引用了未注册的节点、出现孤立节点、
    # 缺少入口等都会在这里报错),通过后返回 CompiledGraph。
    # 编译后的对象支持 .invoke(state) 同步调用与 .ainvoke(state) 异步调用,
    # 返回的是合并后的完整最终状态字典(整块黑板的最终内容)。
    # --------------------------------------------------------------------------
    return graph_builder.compile()


# 构建图
# 模块级执行:在导入本模块时【立即】构建并编译图对象 graph。
# 这样设计使图只构建一次(单例),后续所有 legal_response* 调用复用同一个
# 编译图,避免每次请求都重新 add_node / compile 的重复开销
# (编译是很重的操作,一定要只做一次)。
graph = build_graph()

# 生成流程图
# 使用 try-except 包裹可视化生成,因为 graphviz 等依赖可能未安装或环境不支持。
# 失败时仅打印警告而不中断模块加载(可视化是辅助功能,不影响业务逻辑)。
try:
    # 调用 output_pic_graph 将编译图导出为 PNG 图片,保存到指定路径。
    # get_file_path 跨平台解析项目内文件路径,确保不同工作目录下都能正确写入
    # (避免相对路径在换工作目录后失效)。
    output_pic_graph(graph, get_file_path("__004__langgraph_more_nodes/graph.png"))
except Exception as e:
    # 捕获所有异常(如缺依赖、写入权限等),打印警告信息。
    # 使用 emoji ⚠️ 在控制台中突出显示,便于开发者注意到可视化被跳过。
    print(f"⚠️ 流程图生成跳过: {e}")


async def legal_response(input: str, **kwargs):
    """
    异步调用法智引擎图并返回纯文本输出。

    作用:
        异步入口接口,适用于 Web 服务(async handler)或需要并发的场景。
        将用户输入与可选参数打包为状态字典(塞进黑板),调用 graph.ainvoke
        异步执行整张图,执行完毕后从最终状态中提取 output 字段返回。

    参数:
        input (str): 用户输入文本(法律问题、合同文本、发小红书指令等)。
        **kwargs: 任意可选的状态字段,如 task_type(强制指定任务类型)、
                  uploaded_doc_path(上传文档路径)、user_side(用户立场)等。
                  这些参数会合并到初始 state 中,可覆盖默认行为
                  (比如前端已经知道是合同审核,就直接传 task_type="contract_review",
                  省去 LLM 分类这一步)。

    返回值:
        str: 最终状态中的 output 字段(纯文本回答/报告摘要),若不存在返回空字符串。

    可迁移性说明:
        这是 LangGraph 异步调用的标准模式:state_input -> ainvoke -> get(output)。
        可迁移到任何基于图的异步任务,只需替换 graph 对象与输出字段名。
        注意 ainvoke 返回的是【完整最终状态】(合并了所有节点增量后的整块黑板),
        而非节点返回的增量,因此可以读取任意字段。
    """
    # 构造初始状态字典:将 input 文本与 kwargs 中的可选字段合并。
    # 例如 legal_response("...", task_type="contract_review") 会生成
    # {"input": "...", "task_type": "contract_review"} —— 这就是黑板的初始内容。
    # **kwargs 语法:把调用者传入的所有关键字参数展开成字典项。
    state_input = {"input": input, **kwargs}
    # 异步调用图:graph.ainvoke 会从 START 开始按拓扑(边定义)执行各节点,
    # 直到抵达 END。await 挂起当前协程、把控制权交还事件循环,等整张图跑完
    # 后恢复,返回合并后的完整状态字典(即"最终黑板")。
    # 因为是异步的,等待期间主线程/事件循环还能处理其他请求(高并发友好)。
    result = await graph.ainvoke(state_input)
    # 从最终状态提取 output 字段(由 final_delivery_node 或对应链路的终态节点
    # 写入)。使用 .get 容错:若 output 未被设置则返回空字符串,避免 KeyError。
    return result.get("output", "")


def legal_response_sync(input: str, **kwargs):
    """
    同步调用法智引擎图,返回纯文本输出(兼容旧接口)。

    作用:
        同步入口接口,适用于脚本、CLI 工具或不支持异步的旧代码。
        与 legal_response 功能等价,但使用同步 invoke(阻塞式)避免事件循环开销。
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
    # 构造初始状态字典(与 legal_response 相同:input + 可选 kwargs 合并)。
    state_input = {"input": input, **kwargs}
    # 同步调用图:graph.invoke 会【阻塞当前线程】直到整张图执行完毕(从 START
    # 一路跑到 END),然后返回完整最终状态。适合脚本与简单场景;
    # 在异步环境(如 FastAPI handler)中应改用 ainvoke,避免阻塞事件循环
    # (否则一个慢请求会卡住所有其他请求的处理)。
    result = graph.invoke(state_input)
    # 返回 output 字段(默认空字符串,容错处理)。
    return result.get("output", "")


def legal_response_full(input: str, **kwargs):
    """
    同步调用法智引擎图,返回完整 state dict(含结构化数据,供前端卡片展示)。

    作用:
        同步入口接口,但返回比 legal_response_sync 更丰富的结构化数据。
        前端可基于该数据渲染风险卡片、引用列表、合同信息等多种可视化组件,
        而不仅仅是纯文本回答。本函数负责从最终状态中提取并整理前端需要的
        所有字段,统一打包成一个 dict 返回。

    参数:
        input (str): 用户输入文本。
        **kwargs: 任意可选状态字段。

    返回值:
        dict: 包含前端展示所需的完整数据结构,键包括:
            - output (str): 纯文本输出
            - doc_text (str): 文档全文
            - merged_risk_items (List): 合并去重后的风险项列表
            - contract_risk_items / compliance_risk_items / numeric_risk_items
              / credit_risk_items (List): 四路独立风险项
            - overall_risk_score (float): 综合风险评分(0-100)
            - risk_level (str): 风险等级(Low/Medium/High)
            - citations (List): 法规引用列表
            - party_a / party_b (str): 甲乙方名称
            - party_a_credit_info / party_b_credit_info (dict): 双方资信详情
            - credit_check_success (bool): 资信查询是否真实 API 成功
            - contract_type (str): 合同类型
            - need_lawyer_review (bool): 是否需要律师复核
            - final_report_markdown (str): Markdown 格式最终报告
        所有字段均提供默认值,确保字段缺失时不会抛出异常。

    可迁移性说明:
        该接口体现的"字段映射 + 默认值"模式可迁移到任何需要向前端暴露
        结构化结果的场景。迁移时需根据新业务调整返回字段集合。
        注意 .get(key, default) 的使用确保了即使图中某些节点未执行
        (如非合同审核链路下 contract_risk_items 为空)也能稳定返回。
    """
    # 构造初始状态字典(input + 可选 kwargs 合并成黑板初始内容)。
    state_input = {"input": input, **kwargs}
    # 同步调用图,获取完整最终状态(阻塞直到整张图执行完毕)。
    result = graph.invoke(state_input)
    # 构造前端需要的完整数据:
    # 逐字段从最终状态提取,使用 .get 提供默认值,确保前端不会因字段缺失而崩溃。
    return {
        # 纯文本输出(报告摘要或问答答案),前端主展示区使用。
        "output": result.get("output", ""),
        # 文档全文(供前端展示原文对照,让用户边看原文边看风险)。
        "doc_text": result.get("doc_text", ""),
        # 合并去重后的风险项(供风险卡片渲染,含第 4 路资信风险)。
        "merged_risk_items": result.get("merged_risk_items", []),
        # 四路独立风险项(供细分展示:合同/合规/数值/资信各一张卡片)。
        "contract_risk_items": result.get("contract_risk_items", []),
        "compliance_risk_items": result.get("compliance_risk_items", []),
        "numeric_risk_items": result.get("numeric_risk_items", []),
        "credit_risk_items": result.get("credit_risk_items", []),
        # 综合风险评分(0-100,用于风险等级条形图,含资信分修正)。
        "overall_risk_score": result.get("overall_risk_score", 0),
        # 风险等级标签(用于颜色标识:Low 绿 / Medium 黄 / High 红)。
        "risk_level": result.get("risk_level", "Unknown"),
        # 法规引用列表(用于引用卡片,展示审核依据了哪些法条)。
        "citations": result.get("citations", []),
        # 甲乙方名称(用于合同信息卡片)。
        "party_a": result.get("party_a", ""),
        "party_b": result.get("party_b", ""),
        # 甲乙双方资信详情(企查查返回的完整结构:
        # 基本信息/股权/失信/被执行人/异常/处罚/评分/等级/mock)。
        "party_a_credit_info": result.get("party_a_credit_info", {}),
        "party_b_credit_info": result.get("party_b_credit_info", {}),
        # 资信查询状态(True = 至少一方真实 API 成功,False = 使用模拟数据,
        # 前端可据此决定是否提示"数据可能不完整")。
        "credit_check_success": result.get("credit_check_success", False),
        # 合同类型(用于合同信息卡片)。
        "contract_type": result.get("contract_type", ""),
        # 是否需要律师复核(用于提示横幅:AI 判断风险过高时建议人工介入)。
        "need_lawyer_review": result.get("need_lawyer_review", False),
        # Markdown 格式最终报告(用于详细报告折叠面板)。
        "final_report_markdown": result.get("final_report_markdown", ""),
    }


async def legal_response_stream(input: str, **kwargs):
    """
    流式输出接口,逐块返回生成器,供前端 st.write_stream 使用。

    作用:
        异步生成器接口,模拟流式输出体验。先按固定字符块逐块 yield 文本
        (模拟打字机效果),最后再 yield 一个 JSON 数据包(以特殊标记包裹),
        内含完整结构化数据供前端卡片渲染。
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
        此模式可迁移到任何需要"流式文本 + 结构化结果"的 LLM 应用。
        注意当前实现是"伪流式"(先完整执行图再分块输出),实际生产可替换为
        LLM 流式调用(如 await llm.astream)实现真流式。

    实现细节:
        - chunk_size=4:每个文本块 4 个字符,平衡流畅度与开销
        - asyncio.sleep(0.02):每块间停顿 20ms,模拟真实打字速度
        - 末尾 JSON 通过 ensure_ascii=False 保留中文可读性
    """
    # 构造初始状态字典(input + 可选 kwargs 合并成黑板初始内容)。
    state_input = {"input": input, **kwargs}
    # 异步调用图,等待整个图执行完毕(注:此处非真流式,先完整执行完再分块
    # 输出;真流式应改为逐 token 调用 LLM,此处保持实现简单)。
    result = await graph.ainvoke(state_input)
    # 提取纯文本输出(整段最终答案)。
    output_text = result.get("output", "")
    
    # 模拟流式效果:按字符分块输出(实际项目中可替换为 LLM 流式调用)。
    # 定义每个文本块的字符数:4 个字符一块,既保证流畅感又控制 yield 次数
    # (块太小则 yield 太频繁有性能开销,块太大则失去"打字机"效果)。
    chunk_size = 4
    # 使用 range 生成起始索引:0, 4, 8, ... 步长为 chunk_size,遍历整个
    # output_text,保证每个字符恰好被切进一个块。
    for i in range(0, len(output_text), chunk_size):
        # 切片取出当前块:output_text[i : i+chunk_size](最后一块可能不足
        # chunk_size 个字符,Python 切片会自动截到末尾,不会越界报错)。
        chunk = output_text[i:i + chunk_size]
        # yield 当前块给前端:yield 是生成器的关键语法,每次执行到这里就把
        # 这块文本"吐"给调用方,前端 st.write_stream 会实时拼接显示。
        yield chunk
        # 异步等待 20ms,制造打字机停顿效果(避免输出过快失去流式感;
        # 也可理解为给前端留出渲染时间)。await asyncio.sleep 不阻塞事件循环,
        # 等待期间其他协程仍可运行。
        await asyncio.sleep(0.02)

    # 最终推送完整的结构化数据(供前端卡片渲染)。
    # 在函数内导入 json,避免在模块顶部引入未广泛使用的库(局部导入习惯,
    # 只有走到这一行才真正加载 json 模块)。
    import json
    # 构造末尾数据包:done=True 标识流式结束,full_result 携带前端需要的
    # 结构化字段(与 legal_response_full 保持字段对齐,便于前端复用同一套
    # 解析逻辑渲染卡片)。
    final_data = {
        "done": True,  # 流式结束标志,前端据此停止接收后续内容
        "full_result": {  # 完整结构化结果
            "output": output_text,                                    # 完整文本(与流式拼接结果一致)
            "doc_text": result.get("doc_text", ""),                   # 文档全文
            "merged_risk_items": result.get("merged_risk_items", []),  # 合并风险项(含资信)
            "credit_risk_items": result.get("credit_risk_items", []),  # 第 4 路资信风险项
            "overall_risk_score": result.get("overall_risk_score", 0),  # 综合风险评分
            "risk_level": result.get("risk_level", "Unknown"),        # 风险等级
            "need_lawyer_review": result.get("need_lawyer_review", False),  # 是否需律师复核
            "citations": result.get("citations", []),                 # 法规引用
            "party_a": result.get("party_a", ""),                      # 甲方名称
            "party_b": result.get("party_b", ""),                      # 乙方名称
            "party_a_credit_info": result.get("party_a_credit_info", {}),  # 甲方资信详情
            "party_b_credit_info": result.get("party_b_credit_info", {}),  # 乙方资信详情
            "credit_check_success": result.get("credit_check_success", False),  # 真实 API 是否成功
            "final_report_markdown": result.get("final_report_markdown", ""),  # Markdown 报告
        }
    }
    # 将数据包序列化为 JSON 字符串:json.dumps 把 dict 变成字符串,
    # ensure_ascii=False 保留中文原样(否则中文会被转成 \uXXXX 转义序列,
    # 前端虽然也能解析但可读性差)。
    # 用 __DATA_END__ 标记包裹(前后各一个),前端通过正则或字符串匹配
    # 识别这个标记,取出中间部分解析 JSON —— 标记的作用是把"数据包"和
    # 前面的"文本流"清晰分隔开。
    yield f"__DATA_END__{json.dumps(final_data, ensure_ascii=False)}__DATA_END__"


def _run_cli():
    """
    Subprocess 隔离 CLI 入口。

    设计目标:
        让前端 app.py 可以通过 subprocess 启动独立 Python 进程调用后端
        LangGraph,防止 langchain / pandas / C 扩展(如 numpy/pyarrow)引起的
        进程级硬崩溃(segfault / OOM)把 Streamlit 主进程一起带死。
        崩溃时前端可以 catch CalledProcessError/TimeoutExpired 并安全回退
        demo 数据 —— 即"后端崩了,前端也不崩,还能给用户看演示数据"。

    命令行参数:
        --input_file  包含用户输入文本的临时文件路径(推荐,避免长文本 shell 转义问题)
        --input       用户输入文本(若未提供 input_file 则使用本字段)
        --task_type   任务类型: contract_review / compliance_review / legal_research / ...
        --mode        返回模式:
                        full  → 输出 legal_response_full 的结构化 JSON(默认,前端推荐)
                        sync  → 输出 legal_response_sync 的纯文本字符串

    输出:
        stdout: 单行 JSON (mode=full) 或 纯文本 (mode=sync)
        stderr: 原有的节点执行 print 日志(保留原有终端输出体验,让用户可追踪进度)
        exit_code: 0 = 成功; 非 0 = 失败 (前端据此判定 fallback)
    """
    # 局部导入 argparse(命令行参数解析库)与 traceback(异常堆栈打印库):
    # 只在 CLI 入口用到,所以放在函数内部导入,避免模块加载时引入。
    import argparse
    import traceback

    # ========== Windows 中文 GBK 环境保护 ==========
    # 即使前端已经设置了 PYTHONIOENCODING=utf-8,这里仍做一次 belt-and-suspenders
    # (双重保险)兜底:用 sys.stdout.reconfigure 强制 encoding=utf-8,
    # errors=replace,这样任何节点的 print(含 emoji / 箭头符号 \u25b6 /
    # 中文字符)都不会再抛 UnicodeEncodeError(Windows 默认 GBK 编码
    # 无法表示某些字符,直接 print 会崩溃)。
    # stderr 同样处理(traceback 打印的报错含中英文混合,也可能超出 GBK 范围)。
    try:
        # 若标准输出对象支持 reconfigure 方法(通常支持),就重设编码为
        # utf-8,errors=replace 表示遇到无法编码的字符用 "?" 替代而非报错。
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        # 对标准错误流做同样的编码兜底(错误日志也要能正常打印中文)。
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        # 极少数嵌入运行环境没有 reconfigure,降级不改(保持默认编码,
        # 此时若出编码错误由外层捕获处理)。
        pass

    # 创建命令行参数解析器:description 是 --help 时显示的说明文字。
    # argparse 会把命令行参数解析成 args 对象,args.xxx 即对应参数值。
    parser = argparse.ArgumentParser(description="法智引擎 LangGraph 后端 CLI(隔离进程入口)")
    # 定义 --input_file 参数:用户输入文本所在的临时文件路径(UTF-8 编码)。
    # type=str 表示按字符串接收,default="" 表示未传时为空字符串。
    parser.add_argument("--input_file", type=str, default="", help="用户输入文本所在的临时文件路径(UTF-8)")
    # 定义 --input 参数:直接传用户输入文本(若未用 --input_file 则使用本字段;
    # 用文件传参可避免长文本在 shell 里被特殊字符转义搞坏)。
    parser.add_argument("--input", type=str, default="", help="用户输入文本(若未用--input_file则使用本字段)")
    # 定义 --task_type 参数:任务类型,默认 contract_review(合同审核),
    # 前端一般会显式传入具体类型。
    parser.add_argument("--task_type", type=str, default="contract_review", help="任务类型,默认 contract_review")
    # 定义 --mode 参数:返回模式,choices 限定只能选 "full" 或 "sync",
    # 默认 "full"(结构化 JSON,前端推荐)。
    parser.add_argument("--mode", type=str, default="full", choices=["full", "sync"], help="返回模式: full=结构化JSON, sync=纯文本")
    # 真正解析命令行参数(sys.argv 由 Python 解释器自动填充,
    # parse_args 把结果放进 args 命名空间对象)。
    args = parser.parse_args()

    # ---- 读取用户输入: 文件优先 ----
    # 若传了 --input_file,优先从文件读输入(避免 shell 转义问题)。
    if args.input_file:
        try:
            # 以 UTF-8 编码打开文件并读取全部内容,存为 user_input。
            # with 语句保证文件用完自动关闭(即使中途异常也会关闭)。
            with open(args.input_file, "r", encoding="utf-8") as f:
                user_input = f.read()
        except Exception as e:
            # 读文件失败(路径不存在/权限不足/编码错误等)时,立刻写 JSON 错误
            # 到 stdout 并退出,防止被当作空输入继续跑下去。
            # 错误对象以 {"__cli_error__": ...} 结构输出,前端可识别这个键。
            sys.stdout.write(json.dumps({"__cli_error__": f"读取 input_file 失败: {e}"}, ensure_ascii=False))
            # 补一个换行符,保证 stdout 输出是完整的"一行",前端按行读取时
            # 能读到完整 JSON(否则 JSON 可能与后续输出粘连)。
            sys.stdout.write("\n")
            # 以退出码 2 结束进程(非 0 即失败,前端据此判定 fallback)。
            sys.exit(2)
    else:
        # 未传 input_file,则直接使用 --input 参数作为用户输入。
        user_input = args.input

    # 空输入检查:strip() 去掉首尾空白后为空,说明用户没给有效输入。
    if not user_input.strip():
        # 输出 JSON 格式的错误信息到 stdout(前端可识别 __cli_error__ 键)。
        sys.stdout.write(json.dumps({"__cli_error__": "用户输入为空"}, ensure_ascii=False))
        sys.stdout.write("\n")
        # 以退出码 3 结束(与读文件失败的 2 区分,便于前端排查)。
        sys.exit(3)

    # 组装传给图的可选状态字段 kwargs:目前只传 task_type(任务类型),
    # 它会被合并进初始 state,让图跳过 LLM 意图分类、直接走指定链路。
    kwargs = {"task_type": args.task_type}

    try:
        if args.mode == "sync":
            # sync 模式:直接把字符串打印到 stdout(不用 JSON 包,与原有
            # 接口行为一致,方便简单脚本直接消费纯文本)。
            result_text = legal_response_sync(user_input, **kwargs)
            # 确保始终为字符串类型:即使后端返回空串 "" 也是合法的
            # (前端 _normalize_result 会兜底处理),但若返回了非字符串
            # (如 None 或数字),这里统一转成字符串,保证 stdout 输出稳定。
            if not isinstance(result_text, str):
                result_text = str(result_text) if result_text else ""
            # 把结果文本写入 stdout。
            sys.stdout.write(result_text)
            # 补换行,保证输出是一整行(前端按行读取时不会截断)。
            sys.stdout.write("\n")
        else:
            # full 模式(默认):调用 legal_response_full 拿到结构化 dict。
            result_dict = legal_response_full(user_input, **kwargs)
            # 若 output 为空字符串,按 project_memory 要求,保证前端拿到的
            # home_qa_answer 非空 —— legal_response_full 中 output 字段来自
            # graph output,若为 "" 则在 demo 模式下由前端再兜底一次,
            # 这里只负责把 graph 返回的结果如实序列化。
            # ensure_ascii=False 保证中文不被转义为 \uXXXX(可读且省空间);
            # default=str 兜底处理 dict 中无法 JSON 序列化的对象
            # (如 datetime 或自定义类,一律转成字符串)。
            line = json.dumps(result_dict, ensure_ascii=False, default=str)
            # 把 JSON 单行写入 stdout。
            sys.stdout.write(line)
            # 补换行,保证 stdout 输出是完整的一行 JSON。
            sys.stdout.write("\n")
        # 正常结束:以退出码 0 退出进程,前端据此判定本次调用成功。
        sys.exit(0)
    except Exception as e:
        # Python 级异常(图执行报错、LLM 调用失败等):
        # 把完整堆栈打印到 stderr(便于开发者调试,stderr 与 stdout 分离,
        # 不会污染前端解析的数据流)。
        traceback.print_exc(file=sys.stderr)
        # 写错误 JSON 到 stdout(前端可识别 __cli_error__ 键并安全回退
        # demo 数据),包含异常类型名与异常信息。
        sys.stdout.write(json.dumps({"__cli_error__": f"后端执行异常: {type(e).__name__}: {e}"}, ensure_ascii=False))
        sys.stdout.write("\n")
        # 以非 0 退出码(1)结束,前端据此判定 fallback。
        sys.exit(1)


# Python 入口守卫:只有"直接运行本文件"时(python langgraph_main.py)才执行
# _run_cli();若本文件是被其他模块 import(如前端 from ... import legal_response),
# 则 __name__ 等于模块名而非 "__main__",不会启动 CLI ——
# 这是 Python 的标准写法,保证"作为库被导入"和"作为脚本运行"两种场景互不干扰。
if __name__ == "__main__":
    # 以命令行模式启动:调用子进程隔离的 CLI 入口函数。
    _run_cli()
