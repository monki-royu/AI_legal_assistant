"""
法智引擎 AgentState
基于 XMind 主架构定义, 覆盖:
  - 合同审核(N1-N15)
  - 合规审查
  - 法律检索(N1-N9 子图)
  - 智能问答(知识图谱 RAG)
  - 小红书自动发布
  - 通用兜底
"""
# 📜 代码文字逻辑解析
# 本文件是法智引擎(AI法律助理)多智能体编排系统中"共享状态"的唯一数据契约定义。
# 在 LangGraph 框架中,多个节点之间需要传递上下文信息(如用户输入、抽取的文档、风险项、检索引用、
# 最终报告等),这些信息统一封装在 AgentState 中流转。本文件采用 Python 的 TypedDict 机制,
# 而非 Pydantic BaseModel,是为了与 LangGraph 的 StateGraph 兼容(状态在节点间以 dict 形式合并更新)。
# 该状态覆盖六大业务链路:合同审核(N1-N15)、合规审查、法律检索、知识图谱问答、小红书自动发布、
# 通用兜底(LLM 直接回答)。每个字段都对应业务流程中的某一环节产物,如 doc_text 为文档提取结果、
# contract_risk_items 为合同审核AI风险项、cypher_query 为知识图谱查询语句等。
# 通过 total=False 设计,所有字段均为可选,允许不同分支只写入自己关心的字段,实现灵活的状态合并。
# 整个 AgentState 是各节点之间数据交换的"总线",其字段设计直接决定了节点函数的输入输出契约。
# typing 模块提供类型注解支持:TypedDict 用于定义"具有固定键值类型的字典",
# List/Dict/Optional 用于精确描述字段的数据结构,便于 IDE 智能提示与类型检查
from typing import TypedDict, List, Dict, Optional


# 📜 定义 AgentState 类,继承自 TypedDict
# TypedDict 是 typing 模块提供的特殊类型,允许像定义类一样声明"字典的键及其对应值类型"
# 这样既保持了字典的灵活性(可作为 LangGraph 状态在节点间合并),又获得了静态类型检查的好处
# total=False 参数的含义:所有字段都是"可选的"(optional),即创建实例时不必提供所有键
# 这一点非常关键,因为不同业务分支(如合同审核 vs 小红书发布)只会写入自己路径上的相关字段,
# 若 total=True(默认),则要求所有键必须存在,会导致大量空字段,违背灵活性原则
class AgentState(TypedDict, total=False):
    # ==================== 输入 ====================
    # 用户输入文本字段:存储用户在前端输入的原始问题或合同文本
    # 这是整个流程的起点数据,会被 intent_router_node 用于意图识别,也被后续节点用作上下文
    input: str                          # 用户输入文本
    # 上传文档路径:用户上传的合同/法规文档(txt/md/docx格式)的本地或远程路径
    # doc_extract_node 会读取该路径并通过相应解析库(LangChain loader)提取全文文本到 doc_text
    uploaded_doc_path: str              # 上传文档路径(txt/md/docx)
    # 合同类型:用于在 contract_classify_node 中分类后存储,影响后续审核规则的选取
    # 取值范围对应不同合同模板(买卖/租赁/借贷等),不同类型有不同的审核侧重点与法规依据
    contract_type: str                  # 合同类型(买卖/租赁/借贷/建设工程/政府采购/劳动/服务/技术/其他)
    # 审核方案:决定合同审核走哪条路径
    # AI_AUTO 表示完全由 LLM 自动审核;CUSTOM_RULES 表示基于用户提供的自定义规则审核
    review_mode: str                    # 审核方案: AI_AUTO | CUSTOM_RULES
    # 自定义规则列表:仅当 review_mode == CUSTOM_RULES 时使用
    # 用户可上传或输入特定的审核规则(如"违约金不得超过合同总价的20%"),供 contract_ai_review_node 参考
    custom_rules: List[str]             # 自定义规则(CUSTOM_RULES 模式下使用)

    # ==================== 意图路由 ====================
    # 任务类型:意图路由节点(intent_router_node)输出的核心字段
    # 该字段直接决定后续走哪条业务链路,是条件路由(intent_router_router)的判断依据
    # 取值范围:contract_review(合同审核)/compliance_review(合规审查)/legal_research(法律检索)/
    # legal_qa(法律问答)/xiaohongshu(小红书)/other(其他)
    task_type: str                      # 任务类型: contract_review/compliance_review/legal_research/legal_qa/xiaohongshu/other

    # ==================== 文档解析 ====================
    # 文档全文:doc_extract_node 解析 uploaded_doc_path 后得到的纯文本内容
    # 后续 clause_split_node 会基于此切分条款, contract_ai_review_node 会基于此做风险审查
    doc_text: str                       # 文档全文
    # MinerU 结构化 JSON: doc_extract_mineru_node 输出的多模态解析结果
    # 含 pages → blocks → {type, content, bbox} 结构, 支持文字/表格/图片/坐标
    # MinerU 不可用时自动降级为纯文本包装的 fallback JSON
    doc_structured_json: Dict           # MinerU结构化JSON{metadata, pages:[{page_idx, blocks:[{type,content,bbox}]}]}
    # 切分后的条款列表:clause_split_node 输出
    # 每个元素是一个字典,包含条款id(序号)、title(标题)、text(正文)、bbox(可选,文本坐标框,用于PDF定位)
    # 该结构便于逐条审核,并在最终报告中按条款定位风险点
    doc_clauses: List[Dict]             # 切分后的条款[{id, title, text, bbox?}]

    # ==================== 数值抽取 ====================
    # 抽取的数值实体字典:numeric_extract_node 输出
    # 键值对形式存储从合同文本中通过正则/LLM抽取出关键数值,如 {"单价": 5000, "数量": 100, "总价": 500000, ...}
    # 这些数值会被 numeric_validate_node 用于校验逻辑一致性(如单价×数量是否等于总价)
    extracted_numerics: Dict            # 抽取的数值实体{单价/数量/总价/税率/违约金/保证金/付款比例...}

    # ==================== 风险项(三路并行) ====================
    # 以下三个字段对应三种独立的风险检测路径,理论上可并行执行(项目中以串行模拟)
    # 每个风险项通常包含: 条款id/标题/风险描述/严重程度/建议修改 等字段,具体结构由各节点定义
    # 合同审核风险项:contract_ai_review_node 输出,主要检测条款措辞、权责、违约金等合同本身风险
    contract_risk_items: List[Dict]     # 合同审核风险项
    # 合规审查风险项:compliance_review_node 输出,检测是否符合相关法律法规(如《民法典》要求)
    compliance_risk_items: List[Dict]   # 合规审查风险项
    # 数值校验风险项:numeric_validate_node 输出,检测数值一致性、比例合理性、是否超标等
    numeric_risk_items: List[Dict]      # 数值校验风险项

    # ==================== 检索 ====================
    # ===== 检索链路中间字段（5节点拆分后用于节点间传递） =====
    retrieval_query: str                # 意图分解后的查询字符串
    retrieval_keywords: List[str]       # 意图分解后的关键词列表
    base_citations: List[Dict]          # 基础层检索结果（FAISS+本地法规）
    enhance_citations: List[Dict]       # 增强层检索结果（LLM补充检索）
    # 检索结果上下文:legal_research_node 输出
    # 通过 FAISS/Neo4j 等检索系统召回的相关法规、案例文本,会作为最终报告的依据
    research_context: str               # 检索结果上下文
    # 引用列表:检索到的具体法规条款,每个元素包含 title(法规名)、article_no(条款号)、
    # content(条款内容)、source(来源,如"民法典"/"公司法")。用于在报告中标注引用来源,提高可信度
    citations: List[Dict]               # 引用[{title, article_no, content, source}]
    # 检索质量分:0-100分,反映检索结果的相关性高低
    # 可用于在风险聚合时调整评分权重,或在质量过低时触发降级策略(走LLM伪检索)
    quality_score: float                # 检索质量分(0-100)

    # ==================== 风险聚合 ====================
    # 综合风险评分:risk_aggregate_node 综合三路风险项后给出的总体风险分(0-100)
    # 分数越高表示风险越高,前端会据此展示风险等级标签
    overall_risk_score: float           # 综合风险评分(0-100)
    # 风险等级:基于 overall_risk_score 转换的等级标签
    # Low: 81分及以上(低风险);Medium: 61-80分(中风险);High: 60分及以下(高风险)
    # 该阈值的设定直接影响用户的决策提示(是否需要律师复核)
    risk_level: str                     # 风险等级: Low(>=81) / Medium(61-80) / High(<60)
    # 合并去重后的风险项:risk_aggregate_node 输出
    # 由于三路风险检测可能命中同一条款的不同维度,聚合节点会去重并合并,形成最终风险清单
    merged_risk_items: List[Dict]       # 合并去重后的风险项

    # ==================== 甲乙方识别 ====================
    # 甲方名称:party_identify_node 通过LLM/规则从合同中识别出的甲方主体名称
    # 用于在报告中明确"用户立场",并据此评估条款对用户是否有利
    party_a: str                        # 甲方名称
    # 乙方名称:同上,识别出的乙方主体名称
    party_b: str                        # 乙方名称
    # 用户立场:标识当前审核请求代表甲方还是乙方
    # 取值 A/B/Unknown,会影响风险评分的偏向性(如对甲方不利的条款若用户是甲方则风险加权)
    user_side: str                      # 用户立场: A / B / Unknown

    # ==================== 相对方资信(企查查API) ====================
    # 甲方资信查询结果:credit_check_node 通过企查查API检索甲方的工商/司法/经营等资信信息
    # 包含基本信息、股权结构、失信记录、被执行人、经营异常、行政处罚等字段
    party_a_credit_info: Dict           # 甲方资信信息{basic_info, shareholders, dishonest, executed, abnormal, penalties, credit_score, ...}
    # 乙方资信查询结果:同上,检索乙方的资信信息
    party_b_credit_info: Dict           # 乙方资信信息{basic_info, shareholders, dishonest, executed, abnormal, penalties, credit_score, ...}
    # 资信风险项列表:credit_check_node 根据甲乙双方资信情况生成的独立风险项
    # 每个风险项含 source(资信审查)/party(甲方/乙方)/severity/description/suggestion 等字段
    credit_risk_items: List[Dict]       # 资信风险项
    # 资信查询状态标志:标识是否成功调用了企查查API(用于降级判断和报告中显示)
    # True=API查询成功, False=API不可用(使用模拟数据或跳过)
    credit_check_success: bool          # 资信查询是否成功(True/False)

    # ==================== 交付产物 ====================
    # 最终报告(Markdown格式):final_delivery_node 输出,可直接在前端以Markdown渲染展示
    # 报告通常包含: 基本信息汇总/风险项清单/法规引用/修改建议等
    final_report_markdown: str          # 最终报告(markdown)
    # 修正后的合同文本:基于风险项给出的修改建议,自动生成的修正版本(可选)
    # 供用户参考,可进一步人工编辑
    revised_contract_text: str          # 修正后的合同文本
    # 是否需要律师复核:布尔标志,基于风险等级判定
    # 通常 risk_level == High 时为 True,提示用户风险较高建议寻求专业律师意见
    need_lawyer_review: bool            # 是否需要律师复核
    # 最终输出文本:面向前端展示的纯文本结果,可能是报告摘要、问答答案或直接回答
    # legal_response 系列函数即读取该字段返回给调用方
    output: str                         # 最终输出(给前端展示)

    # ==================== 重试 ====================
    # 重试计数:用于在节点失败时记录重试次数,防止无限循环
    # 例如法律检索失败时可重试N次后降级为LLM伪检索
    retry_count: int                    # 重试计数

    # ==================== 小红书发布 ====================
    # 是否有发小红书意图:xiaohongshu_publish_intent_node 输出的布尔标志
    # 该字段是 START 后第一个条件路由(is_xiaohongshu_publish_intent)的判断依据
    is_xiaohongshu_publish_intent: bool       # 是否有发小红书意图
    # 小红书标题:text_generate_node 生成的标题文案,需符合小红书平台风格(吸引眼球、含emoji等)
    xiaohongshu_title: str                    # 小红书标题
    # 小红书正文:text_generate_node 生成的正文文案,通常包含分点、emoji、话题标签
    xiaohongshu_content: str                  # 小红书正文
    # 图片路径列表:image_generator_node 生成的配图本地路径列表
    # 小红书发布需要至少一张图片,该列表驱动 auto_publish_node 上传图片
    xiaohongshu_image_path_list: List[str]    # 图片路径列表
    # 提示信息:check_text_image_node 输出的检查反馈,如"标题过长需修改"等
    xiaohongshu_tip: str                      # 提示信息
    # 是否可以发布:check_text_image_node 综合校验标题/正文/图片后给出的可发布标志
    # 是 check_text_image_router 条件路由的判断依据,为True才走auto_publish_node,否则直接END
    is_can_publish_xiaohongshu: bool          # 是否可以发布
    # markdown输出:generate_markdown_node 输出,将发布结果整理为Markdown格式供存档/展示
    xiaohongshu_markdown_output: str          # markdown输出

    # ==================== 知识图谱问答 ====================
    # 是否法律问答意图:legal_qa_intent_node 输出(注:实际流程中由intent_router根据task_type分流)
    is_legal_qa_intent: bool            # 是否法律问答意图
    # 用户输入抽取的实体:extract_entity_from_user_input_node 输出
    # 通过LLM从用户问题中抽取的法律实体(如"租赁合同"、"违约金"、"民法典第六百条"等)
    # 作为Neo4j实体匹配的输入
    user_input_entities: List[str]      # 用户输入抽取的实体
    # 抽取的法律概念:LLM识别出的法律概念(如"合同效力"、"违约责任"),用于扩展检索范围
    user_input_concepts: List[str]      # 抽取的法律概念
    # 抽取的法规名:LLM识别出的法规名称(如"民法典"、"合同法"),用于精确检索
    user_input_statutes: List[str]      # 抽取的法规名
    # Neo4j匹配到的实体:match_entity_from_neo4j_node 输出
    # 在知识图谱中匹配到的相关实体列表,作为后续Cypher生成的依据
    matched_entities: List[str]         # Neo4j匹配到的实体
    # 生成的cypher语句:generate_neo4j_cypher_node 输出
    # 基于匹配实体生成的Neo4j图查询语句,会被 check_cypher_node 校验合法性
    cypher_query: str                   # 生成的cypher语句
    # cypher是否通过校验:check_cypher_node 输出的布尔标志
    # 是 check_cypher_router 条件路由的判断依据之一;为True则执行查询,否则重试生成
    is_all_validate_cypher: bool        # cypher是否通过校验
    # cypher重试次数:记录cypher生成->校验失败->重新生成的累计次数
    # 当重试次数>=3次仍未通过校验,直接走答案生成节点(放弃图查询),避免死循环
    cypher_retry_count: int             # cypher重试次数
    # cypher查询结果:run_cypher_node 执行Cypher后返回的结果列表
    # 每个元素是Neo4j返回的记录字典,供答案生成节点组装最终回答
    cypher_results: List[dict]          # cypher查询结果
    # 知识图谱答案:neo4j_answer_generate_node 基于cypher_results生成的自然语言答案
    # 该字段会被填充到 output 字段,作为legal_qa链路的最终输出
    neo4j_answer: str                   # 知识图谱答案

    # ==================== 思考过程(流式) ====================
    # 思考过程文本:用于在前端展示AI的推理步骤(类似Chain-of-Thought),提升用户体验与可解释性
    # 各节点可向该字段追加内容,前端通过流式渲染逐步展示思考过程
    think_process: str                  # 思考过程文本(供前端展示)
