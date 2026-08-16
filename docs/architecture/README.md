# 法智引擎 — AI 法律助理

## 1 项目背景

当前国内法律服务市场存在严重结构性供需错配：全国 83 万执业律师资源高度集中于头部机构，中小微企业、普通民众的长尾法律咨询长期存在供给真空；叠加金税四期、新《公司法》等新规落地，企业合规审查需求激增。传统法律工具仅支持基础法条检索，通用大模型缺少法律专业深度，无法落地真实风控场景。

伴随国家 "人工智能 +" 行动落地、垂直大模型技术日趋成熟，政策与技术双重窗口叠加，本人自主研发人机协同法律 AI 平台「法智引擎」。项目依托 RAG 检索增强、LangGraph 多智能体协同架构搭建整体体系，全程严格遵循《律师法》划分人机权责，确立 "AI 前置辅助、律师终审签章" 的合规运行模式。平台一站式覆盖合同审核、合规风险筛查、法律检索、智能问答、普法内容自动化发布五大场景，有效降低法律服务使用成本、统一风险审查标准，弥补传统法律服务效率低、覆盖范围有限、定价门槛高的行业短板。

> **设计铁律**：AI 做前置审查 / 辅助生成 / 风险提示 · 律师做最终决策 / 签章交付
> 依据《律师法》第 13/28 条，AI 辅助不替代律师专业判断，最终法律文件须经执业律师审核签章。

---

## 2 系统架构

### 2.1 整体架构

系统采用前后端分离架构，前端基于 Streamlit 构建 Web 界面，后端基于 LangGraph 编排多智能体工作流，知识层由 Neo4j 图数据库与 FAISS 向量索引 + BM25 纯 Python 全文检索构成。

```
┌─────────────────────────────────────────────────────────────┐
│                前端 (Streamlit · 8 页面)                      │
│  首页问答 · 合同审核 · 合规审查 ·  · 案例检索         │
│  法规查询 · 文书生成 · 小红书发布 · 历史记录 ·     │
├─────────────────────────────────────────────────────────────┤
│              后端 API 接口层 + FastAPI 服务                    │
│  legal_response        (异步, 返回纯文本)                      │
│  legal_response_sync   (同步, 返回纯文本)                      │
│  legal_response_full   (同步, 返回完整结构化数据)               │
│  legal_response_stream (异步生成器, 流式输出)                   │
│  FastAPI /api/v1/···   (RESTful · SSE · 10+ 接口)             │
├─────────────────────────────────────────────────────────────┤
│                 LangGraph 多智能体编排层                       │
│  StateGraph(AgentState) · ~45 节点 · 条件路由 · 循环重试      │
│  8 条业务链路: 合同/合规/问答/文书/案例/法规/小红书/直接大模型回答     │
├──────────────┬──────────────┬───────────────────────────────┤
│  LLM 服务层   │  检索服务层    │  知识层                        │
│  ChatOpenAI  │  FAISS 向量  │  Neo4j 图数据库                │
│  (OpenAI兼容) │  BM25 全文   │  FAISS 实体索引 + BM25 索引    │
│              │  Embedding  │  图谱元数据 JSON                │
├──────────────┴──────────────┴───────────────────────────────┤
│              外部数据服务层                                    │
│  企查查 MCP API (工商/失信/被执行/经营异常/行政处罚)           │
├──────────────────────────────────────────────────────────────┤
│                    公共基础设施层                              │
│  Config(.env) · path_utils · langgraph_compat · qichacha   │
│  retrieval_engine · history_store                           │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 目录结构

```
AI_legal_assistant/
├── __006__streamlit/                    # 前端应用
│   └── app.py                           # Streamlit 主入口 (法智引擎 v5)
├── __004__langgraph_more_nodes/         # 后端 LangGraph 多智能体
│   ├── langgraph_main.py                # 主编排: 节点注册 + 边定义 + 条件路由 + API接口
│   ├── agent_state.py                   # AgentState 共享状态定义 (TypedDict, 50+ 字段)
│   └── nodes/                           # 31+ 个节点函数 (见 2.3 节)
├── __003__create_neo4j_database/        # 知识图谱构建
│   ├── __001__graph_importer.py         # 图谱数据导入 Neo4j
│   ├── __002__export_metadata.py        # 导出图谱元数据 JSON (供 Cypher 生成参考)
│   └── __003__vector_index.py           # 构建 FAISS 实体向量索引
├── __002__extract_information/          # 信息抽取
│   ├── __000__extract_graph_data_utils.py  # 图谱数据抽取工具
│   └── __001__extract_law_data.py       # 从法规文本抽取实体关系三元组
├── __001__clawler/                      # 数据采集
│   ├── __000__获取网页内容通用方法.py     # 通用网页抓取
│   ├── __001__local_docx_to_txt.py      # DOCX 转 TXT
│   ├── __002__crawl_law_database.py     # 法律法规数据库爬虫
│   └── 法律法规/                        # 12 部法律 TXT 文本 (民法典/公司法/刑法等)
├── common/                              # 公共模块
│   ├── config.py                        # 配置管理 (读取 .env, 单例模式)
│   ├── llm.py                           # LLM 客户端 (ChatOpenAI, 模块级单例)
│   ├── embedding_model.py               # Embedding 模型 (SentenceTransformer, 单例)
│   ├── neo4j_manager.py                 # Neo4j 数据访问层 (Neo4jClient)
│   ├── qichacha_client.py               # 企查查 MCP API 客户端 (资信查询, 三级降级)
│   ├── langgraph_compat.py              # LangGraph 兼容层 (Python 3.8 轻量 StateGraph)
│   ├── ouput_pic_graph.py               # 图可视化工具 (导出 PNG)
│   └── path_utils.py                    # 路径工具 (相对路径→绝对路径)
├── config/
│   └── rules/                           # 数值校验规则
│       ├── contract_review.yaml         # 合同审核规则 (金额/比例/期限等)
│       └── compliance_review.yaml       # 合规审查规则 (法规符合性)
├── data/
│   ├── raw/                             # 原始法规 DOCX 文件
│   └── sample/                          # 测试合同样本
├── docs/
│   ├── architecture/README.md           # 本文件
│   ├── flowcharts/                      # 交互式流程图 (HTML)
│   └── record/                          # 开发记录与商业计划书
├── .streamlit/config.toml              # Streamlit 部署配置
├── requirements.txt                     # Python 依赖
└── .env                                 # 环境变量 (API Key, Neo4j 连接等, 不入库)
```

### 2.3 节点清单

后端共注册 ~45 个节点，按功能分组如下（新增节点以 ★ 标记）：

| 分组 | 节点文件 | 节点函数 | 功能 |
|------|---------|---------|------|
| **意图路由** | intent_router_node.py | intent_router_node | 基于 LLM 对用户输入分类，输出 task_type (支持 8 种类型) |
| | xiaohongshu_publish_intent_node.py | xiaohongshu_publish_intent_node | START 后首个节点，前置过滤小红书发布意图 |
| **文档处理** | doc_extract_node.py | doc_extract_node | 解析上传文档 (txt/md/docx) 为纯文本 |
| | contract_classify_node.py | contract_classify_node | 基于 LLM 判定合同类型 (买卖/租赁/借贷等) |
| | clause_split_node.py | clause_split_node | 将合同文本切分为结构化条款列表 |
| | numeric_extract_node.py | numeric_extract_node | 正则 + LLM 抽取合同关键数值 (单价/数量/总价等) |
| **风险审查** | contract_ai_review_node.py | contract_ai_review_node | 基于 LLM 审查合同条款商业风险，输出 contract_risk_items |
| | compliance_review_node.py | compliance_review_node | 对照法规检测合规性，输出 compliance_risk_items |
| | numeric_validate_node.py | numeric_validate_node | 基于 YAML 规则校验数值一致性与合理性，输出 numeric_risk_items |
| ★ **冲突消解** | conflict_resolution_node.py | conflict_resolution_node | 合规优先5规则合并合同+合规风险，输出 can_sign (pass/conditional/no) |
| **检索 (5节点链)** | retrieval_intent_decompose_node.py | retrieval_intent_decompose_node | LLM 提取检索词与关键词 |
| | retrieval_base_layer_node.py | retrieval_base_layer_node | FAISS 向量检索 + 本地法规 TXT 关键词匹配 |
| | retrieval_enhance_query_node.py | retrieval_enhance_query_node | 基础层结果不足时 LLM 补充检索 |
| | retrieval_fusion_sort_node.py | retrieval_fusion_sort_node | 去重、排序、拼装上下文、计算质量分 |
| | retrieval_output_node.py | retrieval_output_node | 写入标准字段，确保类型安全 |
| | legal_research_node.py | legal_research_node | 原单节点检索 (已弃用，保留注册兼容) |
| **风险聚合** | risk_aggregate_node.py | risk_aggregate_node | 合并三路风险（冲突消解后合同+合规 / 数值 / 资信），计算综合评分与等级 |
| | party_identify_node.py | party_identify_node | 识别合同甲乙方主体，判定用户立场 |
| | credit_check_node.py | credit_check_node | 调用企查查 MCP API 查询甲乙双方资信 |
| **交付** | final_delivery_node.py | final_delivery_node | 组装 Markdown 报告（含 can_sign 签约结论），写入 output |
| | llm_direct_out_node.py | llm_direct_out_node | 非法律意图的 LLM 兜底回答 |
| **法律问答** | extract_entity_from_user_input_node.py | extract_entity_from_user_input_node | 从用户问题抽取法律实体/概念/法规名 |
| | match_entity_from_neo4j_node.py | match_entity_from_neo4j_node | 在知识图谱中匹配相关实体 |
| | generate_neo4j_cypher_node.py | generate_neo4j_cypher_node | 基于匹配实体生成 Cypher 查询语句 |
| | check_cypher_node.py | check_cypher_node | 校验 Cypher 语法合法性 |
| | run_cypher_node.py | run_cypher_node | 在 Neo4j 上执行 Cypher 查询 |
| | neo4j_answer_generate_node.py | neo4j_answer_generate_node | 基于查询结果生成自然语言答案 |
| | legal_qa_intent_node.py | legal_qa_intent_node | 法律问答意图判断 (由 intent_router 分流) |
| **小红书** | text_generate_node.py | text_generate_node | 生成小红书标题与正文文案 |
| | image_generate_node.py | image_generator_node | 调用极梦文生图 API 生成配图 |
| | check_text_image_node.py | check_text_image_node | 校验文案与图片是否可发布 |
| | auto_publish_xiaohongshu_node.py | xiaohongshu_auto_publish_node | 调用 Playwright 自动发布到小红书 |
| | generate_markdown_node.py | generate_markdown_node | 整理发布结果为 Markdown 存档 |
| ★ **文书生成 (7节点)** | doc_case_analyze_node.py | doc_case_analyze_node | LLM 抽取案情结构化(案由/当事人/诉求等) |
| | doc_template_match_node.py | doc_template_match_node | LLM 匹配文书模板 (10 预设) |
| | doc_clause_fill_node.py | doc_clause_fill_node | RAG 检索法规+司法解释, LLM 生成条文填充 |
| | doc_law_validate_node.py | doc_law_validate_node | 3级校验引用法条真实性(通过/改写/虚假) |
| | doc_risk_advisor_node.py | doc_risk_advisor_node | LLM 分析 2-5 项法律风险 (模板感知) |
| | doc_case_recommend_node.py | doc_case_recommend_node | 纯检索类案推荐 (BM25+FAISS) |
| | doc_final_delivery_node.py | doc_final_delivery_node | 组装最终文书 + 持久化 HistoryStore |
| ★ **独立检索** | case_search_node.py | case_search_node | 案由/关键词/法院检索案例 → case_search_results |
| | law_query_node.py | law_query_node | 关键词/法规名检索法条 → law_query_results |

---

## 3 LangGraph 多智能体编排

### 3.1 图拓扑

图从 START 出发，首先经过小红书意图前置过滤，再进入主意图路由，按 task_type 分流到 8 条业务链路或兜底链路。

```
START → xiaohongshu_publish_intent_node
         │
         ├─ is_xiaohongshu_publish_intent=True
         │   → text_generate → image_generate → check_text_image
         │     ├─ is_can_publish=True → auto_publish → generate_markdown → END
         │     └─ is_can_publish=False → END
         │
         └─ is_xiaohongshu_publish_intent=False
             → intent_router_node → credit_precheck(企查查预判) → 条件路由
               │
               ├─ contract_review
               │   → doc_extract → party_identify → contract_classify → clause_split
               │   → numeric_extract → 检索5节点链
               │   → **after_retrieval_router** (条件分支)
               │     ├─ contract_ai_review (商业风险·6大维度·立场化)
               │     │   → compliance_review (合规审查·7大领域·客观中立)
               │     │   → **conflict_resolution** (合规优先5规则)
               │     └─ → numeric_validate → credit_check → risk_aggregate
               │       → final_delivery (含 can_sign 签约结论) → END
               │
               ├─ compliance_review (独立合规审查)
               │   → doc_extract → party_identify → contract_classify → clause_split
               │   → numeric_extract → 检索5节点链
               │   → **after_retrieval_router** (条件分支)
               │     └─ compliance_review (跳过合同审核AI)
               │       → **conflict_resolution** (仅合规结果, pass-through)
               │       → numeric_validate → credit_check → risk_aggregate
               │       → final_delivery (含 can_sign) → END
               │
               ├─ legal_research
               │   → 检索5节点链 (无文档处理)
               │   → **after_retrieval_router** → skip_review
               │   → conflict_resolution (空风险, pass-through)
               │   → numeric_validate → credit_check → risk_aggregate
               │   → final_delivery → END
               │
               ├─ legal_qa
               │   → extract_entity → match_entity_from_neo4j → generate_cypher
               │   → check_cypher
               │     ├─ 通过 → run_cypher → answer_generate → END
               │     ├─ 不通过(≤3次) → 回 generate_cypher (重试环)
               │     └─ 不通过(>3次) → 降级 answer_generate → END
               │
               ├─ legal_document_gen (★)
               │   → doc_case_analyze → doc_template_match → doc_clause_fill
               │   → doc_law_validate
               │     ├─ need_refill=True(≤3次) → doc_clause_fill (重试环)
               │     └─ need_refill=False → doc_risk_advisor → doc_case_recommend
               │         → doc_final_delivery → END
               │
               ├─ case_search (★) → case_search_node → END
               ├─ law_query (★) → law_query_node → END
               └─ other → llm_direct_out → END
```

**关键架构变更（v2.0）：合同审核与合规审查从共享串行链改为分支独立 + 冲突消解**

| 变更项 | 旧架构 (v1.x) | 新架构 (v2.0) |
|--------|--------------|--------------|
| 合同审核与合规审查的关系 | 共享完全相同的串行节点链（输出必然同质化） | 检索输出后按 task_type 条件分支，各走独立线路 |
| 合同审核AI的调用 | 所有合同类任务都经过 | 仅 contract_review 任务经过；compliance_review 任务跳过 |
| 冲突处理 | 无——contract_risk_items 和 compliance_risk_items 在 risk_aggregate 中简单合并 | 新增 **conflict_resolution_node**，执行"合规优先"5条规则 |
| 合规否决权 | 未实现——合规风险仅作为一路输入参与评分 | 通过 can_sign 字段实现：critical → no（否决签约），high → conditional（强制整改） |
| 报告输出 | 混合风险清单，未区分强制性 vs 可谈判 | 合规风险（不可谈判）展示在商业风险（可谈判）之前，签约结论独立行 |

### 3.2 条件路由

| 路由节点 | 判断字段 | 路由分支 |
|---------|---------|---------|
| xiaohongshu_publish_intent_node | is_xiaohongshu_publish_intent | publish_xiaohongshu_intent / intent_router |
| intent_router_node | task_type | contract_review / compliance_review / legal_research / legal_qa / doc_gen / case_search / law_query / llm_direct |
| after_credit_precheck_router | task_type | doc_extract(合同/合规) / retrieval_intent_decompose(检索) / extract_entity(问答) / doc_case_analyze(文书) / case_search / law_query / llm_direct |
| **after_retrieval_router** ★ | task_type | **contract_review**(→合同审核AI) / **compliance_review**(→直接合规审查) / **skip_review**(→直接冲突消解) |
| check_text_image_node | is_can_publish | publish_xiaohongshu / END |
| check_cypher_node | is_all_validate_cypher + cypher_retry_count | run_cypher(通过) / generate_neo4j_cypher(重试≤3) / answer_generate(降级>3) |
| doc_law_validate_node | need_refill + doc_retry_count | doc_clause_fill(重试≤3) / doc_risk_advisor(通过) |

### 3.3 节点复用

以下节点被多条业务链路复用，通过 AgentState 共享状态，避免重复计算：

| 复用节点 | 调用方链路 | 读取字段 | 写入字段 |
|---------|-----------|---------|---------|
| 检索5节点链 | 合同审核、合规审查、法律检索 | doc_text / contract_type / input | citations / research_context / quality_score |
| party_identify | 合同审核、合规审查 | doc_text | party_a / party_b / user_side |
| credit_check | 合同审核、合规审查、法律检索 | party_a / party_b / user_side | party_a_credit_info / party_b_credit_info / credit_risk_items |
| conflict_resolution ★ | 合同审核、合规审查、法律检索 | contract_risk_items / compliance_risk_items | **post_conflict_risk_items** / **can_sign** / conflict_log |
| risk_aggregate | 合同审核、合规审查、法律检索 | **post_conflict_risk_items** / numeric_risk_items / credit_risk_items | overall_risk_score / risk_level / merged_risk_items |
| final_delivery | 合同审核、合规审查、法律检索 | merged_risk_items / overall_risk_score / citations / **can_sign** 等 | output / final_report_markdown |

> **注**：contract_risk_items 和 compliance_risk_items 不再被 risk_aggregate 直接读取——它们先经 conflict_resolution_node 合并为 post_conflict_risk_items（已应用"合规优先"规则），再由 risk_aggregate 与 numeric_risk_items、credit_risk_items 聚合评分。

### 3.4 状态管理

AgentState（TypedDict, total=False）是所有节点间的数据总线，所有字段可选，不同链路只写入自身路径相关字段。主要字段分组：

| 分组 | 字段 | 说明 |
|------|------|------|
| 输入 | input, uploaded_doc_path, task_type, review_mode, custom_rules | 用户输入与任务控制 |
| 文档解析 | doc_text, doc_clauses, contract_type | 文档提取与条款切分结果 |
| 数值 | extracted_numerics | 合同关键数值字典 |
| 风险项 | contract_risk_items, compliance_risk_items, numeric_risk_items, credit_risk_items | 四路独立风险检测结果（合同/合规/数值/资信） |
| ★ 冲突消解 | **post_conflict_risk_items**, **can_sign**, **conflict_log**, **presentation_order** | 冲突消解结果：合并风险列表、签约结论、消解日志、展示顺序 |
| 检索中间态 | retrieval_query, retrieval_keywords, base_citations, enhance_citations | 5节点链路内部传递 |
| 检索结果 | research_context, citations, quality_score | 最终检索输出 |
| 风险聚合 | overall_risk_score, risk_level, merged_risk_items | 综合评分与合并清单 |
| 甲乙方 | party_a, party_b, user_side | 合同主体识别 |
| 资信查询 | party_a_credit_info, party_b_credit_info, credit_check_success | 甲乙双方企查查资信详情与查询状态 |
| 交付 | output, final_report_markdown, need_lawyer_review | 最终产物 |
| 小红书 | is_xiaohongshu_publish_intent, xiaohongshu_title, xiaohongshu_content, xiaohongshu_image_path_list, is_can_publish_xiaohongshu | 小红书链路状态 |
| 法律问答 | user_input_entities, matched_entities, cypher_query, is_all_validate_cypher, cypher_retry_count, cypher_results, neo4j_answer | 知识图谱RAG状态 |

### 3.5 API 接口

langgraph_main.py 对外暴露四个接口，适配不同调用场景：

| 接口 | 签名 | 返回值 | 适用场景 |
|------|------|--------|---------|
| legal_response | async def legal_response(input, **kwargs) | str | Web 服务异步调用，返回纯文本 |
| legal_response_sync | def legal_response_sync(input, **kwargs) | str | 脚本/CLI 同步调用，返回纯文本 |
| legal_response_full | def legal_response_full(input, **kwargs) | dict | 前端卡片渲染，返回完整结构化数据 (风险项/引用/评分/can_sign等) |
| legal_response_stream | async def legal_response_stream(input, **kwargs) | AsyncGenerator | Streamlit 流式输出，逐块 yield 文本 + 末尾 JSON 数据包 |

---

## 4 数据流水线

项目包含完整的数据采集→抽取→入库→检索流水线：

```
__001__clawler               __002__extract_information     __003__create_neo4j_database
法规网站爬取 ──→ TXT/DOCX ──→ 实体关系三元组抽取 ──→ Neo4j 图谱导入 + FAISS 索引构建
     │                              │                           │
     │                              │                           ├─ legal_metadata.json (图谱元数据)
     │                              │                           ├─ nero4j_embedding_faiss.index (向量索引)
     │                              │                           └─ nero4j_embedding_faiss_id2text.pkl (ID映射)
     │                              │
     └─ 法律法规/*.txt ──────────────└→ 检索基础层 (本地法规关键词匹配)
```

### 4.1 数据采集 (__001__clawler)

- 爬取法律法规数据库，输出 12 部法律的 TXT 文本（民法典、公司法、刑法、劳动合同法等）
- 存放于 `__001__clawler/法律法规/`，作为检索基础层的本地法规数据源

### 4.2 信息抽取 (__002__extract_information)

- 从法规文本中抽取法律实体（法规名、条款号、法律概念）及实体间关系
- 输出三元组数据，供图谱导入使用

### 4.3 知识图谱构建 (__003__create_neo4j_database)

- **图谱导入**：将三元组数据导入 Neo4j，建立法律实体关系图谱
- **元数据导出**：导出图谱模式（标签层/关系类型/属性）为 JSON，供 Cypher 生成节点作为 schema 参考
- **向量索引构建**：基于 SentenceTransformer (bge-m3) 对实体名向量化，构建 FAISS 索引，用于实体召回

### 4.4 检索策略

检索5节点链路采用三层降级策略：

| 层级 | 数据源 | 触发条件 | 说明 |
|------|--------|---------|------|
| 第一层：FAISS 向量检索 | FAISS 实体索引 | 默认 | 基于 embedding_model 对查询向量化，近邻搜索召回相关实体 |
| 第二层：本地法规匹配 | 法律法规/*.txt | 第一层结果不足 | 关键词匹配本地法规文本，补充检索结果 |
| 第三层：LLM 伪检索 | LLM | 前两层均不足 | LLM 基于知识生成引用，作为兜底降级 |

---

## 5 公共基础设施

### 5.1 配置管理 (common/config.py)

Config 类集中管理所有外部依赖配置，通过 python-dotenv 读取 `.env` 文件：

| 配置项 | 环境变量 | 用途 |
|--------|---------|------|
| MODEL_API_KEY | MODEL_API_KEY | 大模型 API 密钥 |
| MODEL_BASE_URL | MODEL_BASE_URL | 大模型服务地址 (OpenAI 兼容) |
| MODEL_NAME | MODEL_NAME | 模型名称 (如 deepseek-chat) |
| NEO4J_URI | NEO4J_URI | Neo4j 连接 URI |
| NEO4J_USER | NEO4J_USER | Neo4j 用户名 |
| NEO4J_PASSWORD | NEO4J_PASSWORD | Neo4j 密码 |
| JIMENG_AK / JIMENG_SK | JIMENG_AK / JIMENG_SK | 极梦文生图 API 密钥 |
| QICHACHA_AUTHORIZATION | QICHACHA_AUTHORIZATION | 企查查 MCP Bearer Token (优先模式) |
| QICHACHA_APP_KEY / QICHACHA_SECRET_KEY | QICHACHA_APP_KEY / QICHACHA_SECRET_KEY | 企查查开放平台 AppKey/SecretKey (兼容模式, MD5 签名) |
| EMBEDDING_MODEL_PATH | EMBEDDING_MODEL_PATH | 本地 Embedding 模型路径 |

### 5.2 LLM 客户端 (common/llm.py)

模块级单例 `my_llm`，基于 LangChain ChatOpenAI 封装，兼容任何 OpenAI 协议的推理服务（DeepSeek、通义、智谱等）。所有节点通过 `from common.llm import my_llm` 获取共享实例。

### 5.3 Embedding 模型 (common/embedding_model.py)

模块级单例 `embedding_model`，基于 SentenceTransformer 加载本地 bge-m3 模型。供 FAISS 向量检索与实体召回使用。

### 5.4 Neo4j 数据访问层 (common/neo4j_manager.py)

Neo4jClient 类封装了连接管理、Cypher 执行、元数据导出、语法校验等能力。核心方法：

- `run_cypher(query)` — 单条查询，返回 dict 列表
- `run_multiple_cypher(queries)` — 批量事务执行，带 tqdm 进度条
- `export_tcm_metadata_to_json()` — 导出图谱模式层 JSON
- `validate_cypher(query)` — EXPLAIN 语法预检（仅接受单条语句）

### 5.5 LangGraph 兼容层 (common/langgraph_compat.py)

为兼容 Python 3.8 环境（无法安装 langgraph 0.2+）自研的轻量 StateGraph 实现，API 与官方完全兼容：

- StateGraph / START / END 常量
- add_node / add_edge / add_conditional_edges / compile
- CompiledGraph.invoke (同步) / ainvoke (异步)
- 固定边取第一条出边执行（不支持真并行），条件边通过 router 函数动态路由
- 内置 _GraphVisualizer 支持 matplotlib 简易绘图

> 未来升级 Python 3.9+ 后，只需将 `from common.langgraph_compat import StateGraph, START, END` 切换为 `from langgraph.graph import StateGraph, START, END` 即可无缝迁移。

### 5.6 路径工具 (common/path_utils.py)

`get_file_path(relative_path)` 函数将工程内相对路径转换为绝对路径，基于 `__file__` 向上回溯定位工程根目录，确保不同工作目录下均可正确访问资源文件。

### 5.7 企查查客户端 (common/qichacha_client.py)

QiChaChaClient 类封装企查查 MCP API，为 credit_check_node 提供企业资信查询能力，覆盖 10 个资信维度（工商基本/股东/失信/被执行/经营异常/行政处罚/知识产权/招投标/司法判例/历史变更）。采用三级降级策略确保服务可用性：

| 模式 | 鉴权方式 | 触发条件 |
|------|---------|---------|
| MCP Bearer Token (优先) | `QICHACHA_AUTHORIZATION` 请求头 | 默认优先，响应为 SSE 流 |
| AppKey + MD5 签名 (兼容) | `QICHACHA_APP_KEY` / `QICHACHA_SECRET_KEY` 字典序拼接 MD5 | Bearer Token 缺失或 401/403 时降级 |
| Mock 模拟数据 (兜底) | 无 | 两种鉴权均缺失或请求失败时，基于公司名 hash 生成定制化数据 |

核心方法 `query_company_credit(company_name)` 返回包含 basic_info / shareholders / dishonest / executed / abnormal / penalties / credit_score / risk_level / mock 标志的字典。资信查询节点绝不会因第三方服务中断而阻塞合同审核主流程。

---

## 6 前端设计

### 6.1 技术方案

前端基于 Streamlit 构建，通过 `st.markdown(unsafe_allow_html=True)` 注入全局 CSS 覆盖默认样式，实现红底蓝高亮的现代化 UI 风格。

### 6.2 页面结构（8 页面）

| 页面 | 智能体名称 | 核心功能 |
|------|-----------|---------|
| 首页 (智能问答) | 法智引擎 | 中央大输入框 + 深度思考开关 + 10 功能任务选择卡 |
| 合同审核 | 合同审核智能体 | 文档上传 + 文本粘贴 → 风险评分 + 原文高亮 + 风险卡片（含合规否决提示） |
| 合规审查 | 合规审查智能体 | 同合同审核布局，适配合规审查场景，签约结论突出显示 |
| ★ 文书生成 | 文书生成智能体 | 纠纷类型选择 + 案情填写 → SSE 流式进度 → 文书预览 + 法条溯源 + 风险提示 |
| ★ 案例检索 | 案例检索智能体 | 关键词/案由/法院筛选 → 案例列表 + 详情 + 引用法条追溯 |
| ★ 法规查询 | 法规查询智能体 | 关键词/法规名筛选 → 法条列表 + 全文 + 效力状态 |
| ★ 历史记录 | 历史记录智能体 | 全部历史 CRUD + 收藏/取消收藏 + 详情 + 全文导出(md/txt) |
| 小红书发布 | 小红书发布智能体 | 生成法律科普文案 + 配图 + 自动发布 |

### 6.3 后端集成

前端启动时尝试导入后端接口（legal_response_sync / legal_response_full / legal_response_stream）：

- **后端可用** (HAS_BACKEND=True)：调用 LangGraph 多智能体处理真实请求
- **后端不可用** (HAS_BACKEND=False)：自动切换演示模式，使用内置演示数据展示界面
- 侧边栏提供演示模式开关，可手动强制启用

### 6.4 交互特性

- **流式输出**：legal_response_stream 逐块 yield 文本 (chunk_size=4, 间隔 20ms)，末尾附带 JSON 数据包
- **风险卡片三态交互**：采纳 / 不采纳 / 修改，状态持久化于 st.session_state
- **文档高亮**：按风险严重程度 (critical/high/medium/low) 四色标注原文段落
- **点击高亮跳转风险卡片**：左侧合同高亮段落可点击，通过纯 CSS 锚点 (`<a href="#risk-card-...">`) 平滑滚动至右侧对应风险卡片，并触发蓝色闪烁动画。采用字段加权匹配（clause=3 > description=2 > legal_basis=1 > suggestion=0.5）+ 停用词过滤精确定位段落，若一段命中多个风险项则跳转至最严重项
- **效果展示切换**：首次点击加载演示数据，再次点击清除
- **合规否决权展示**：当 can_sign="no" 或 "conditional" 时，报告中突出显示合规否决/预警横幅

---

## 7 数值校验规则

数值校验规则存放于 `config/rules/`，采用 YAML 格式，由 numeric_validate_node 加载执行：

### 7.1 合同审核规则 (contract_review.yaml)

依据《企业内部控制应用指引第16号——合同管理》，从商业视角校验：

- **金额准确性**：单价×数量=小计、大小写金额一致、附件金额一致
- **比例合理性**：违约金比例、保证金比例、预付款比例上限校验
- **期限合规性**：付款期限、交货期限、保修期限合理性

### 7.2 合规审查规则 (compliance_review.yaml)

从法规符合性视角校验，全局约束：不得降级或覆盖合规审查的违规结论。

### 7.3 设计原则

- 数值校验使用确定性 Python 代码执行，不依赖 LLM
- 合规风险不可降级，合同风险可降级
- 风险项带 severity 字段 (critical/high/medium/low)，在报告中按严重程度排序
- 合规 critical → 直接否决签约（can_sign="no"），不受评分影响

---

## 8 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| 前端 | Streamlit + 自定义 CSS | Web 界面，红底蓝高亮主题 |
| 后端编排 | LangGraph (兼容层) | 多智能体状态图编排，45+ 节点 |
| 大模型 | OpenAI 兼容接口 (ChatOpenAI) | 支持 DeepSeek/通义/智谱等 |
| 向量检索 | FAISS (faiss-cpu) | 实体向量近邻搜索 |
| 知识图谱 | Neo4j (neo4j Python Driver) | 法律实体关系图谱 |
| Embedding | sentence-transformers (bge-m3) | 文本向量化 |
| 图像生成 | 火山引擎极梦文生图 | 小红书配图生成 |
| 自动发布 | Playwright | 小红书平台自动发布 |
| 资信查询 | 企查查 MCP API | 甲乙双方工商/司法/经营资信核验 |
| 数据处理 | pandas, numpy, openpyxl | 法规数据处理 |
| 配置管理 | python-dotenv | .env 环境变量 |

---

## 9 部署

### 9.1 环境要求

- Python 3.8+（兼容层支持 3.8，官方 LangGraph 需 3.9+）
- Neo4j 5.17+（法律问答链路依赖，不可用时降级为 LLM 直答）
- 依赖见 [requirements.txt](../../requirements.txt)

### 9.2 环境配置

在项目根目录创建 `.env` 文件：

```env
MODEL_API_KEY=your_api_key
MODEL_BASE_URL=https://api.deepseek.com/v1
MODEL_NAME=deepseek-chat
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password
JIMENG_AK=your_ak
JIMENG_SK=your_sk
QICHACHA_AUTHORIZATION=Bearer your_mcp_token
QICHACHA_APP_KEY=your_app_key
QICHACHA_SECRET_KEY=your_secret_key
EMBEDDING_MODEL_PATH=path/to/bge-m3
```

### 9.3 启动命令

```bash
# 安装依赖
pip install -r requirements.txt

# 启动 Streamlit 服务
python -m streamlit run __006__streamlit/app.py --server.port 8501
```

### 9.4 部署配置

`.streamlit/config.toml` 配置：

- 服务端口：8501
- 主题：dark 模式，主色 #1976D2 (科技蓝)
- 背景色：#450a0a (深红)
- 最大上传：200MB
- 安全：启用 XSRF 保护，关闭 CORS

---

## 10 冲突消解规则（法律实务核心）

**conflict_resolution_node** 实现以下 5 条规则，确保合规审查的否决权在最终输出中得到贯彻：

| 规则 | 条件 | 效果 | 法律依据 |
|------|------|------|---------|
| **规则1（否决权）** | compliance_risk_items 中有 severity="critical" | can_sign="no"，该风险标 is_overridden=True | 民法典第153条——违反强制性规定的法律行为无效 |
| **规则2（强制整改）** | compliance_risk_items 中有 severity="high" | can_sign="conditional"，即使合同审核说"可接受"也必须改 | 法律风险达到 high 但不构成无效 |
| **规则3（双重发现）** | 同一 clause 同时出现在两路结果中 | 以合规 severity 为准，source 标注 "compliance+contract" | 合规审查（客观法律标准）> 合同审核（商业判断） |
| **规则4（保留商业风险）** | 合同审核发现某风险但合规未发现 | 保留为商业风险，source="contract_only"，标注可谈判 | 合规通过不代表商业上合理 |
| **规则5（结论冲突）** | 合同说"可接受"同时合规说"违法" | 以合规结论为准，强制归入不可谈判分类 | 商业利益不能凌驾于法律之上 |

### 签约结论三态

| can_sign 值 | 含义 | 最终报告展示 | 后续动作 |
|-------------|------|------------|---------|
| "pass" | ✅ 可签约 | 正常展示风险清单 | 评分≥81可自动输出，<61需律师复核 |
| "conditional" | ⚠️ 条件通过（须整改） | 红色预警横幅 + 强制整改项列表 | 必须整改后方可签约，即使评分≥81 |
| "no" | ❌ 不得签约 | 红色否决横幅 + 不得签约理由 | 无论评分多高都**不得签约**，需律师介入 |

---

## 11 流程图文档

项目包含两套交互式流程图，位于 `docs/flowcharts/`，均支持深色/浅色主题切换（右上角按钮，通过 `data-theme` 属性 + CSS 变量实现，localStorage 持久化用户偏好）：

| 文件 | 说明 |
|------|------|
| 00_index.html | 首页导航，链接到各智能体流程图 + 法律实务架构图 |
| 总架构_详细架构图.html | 用户输入到输出的完整流程图（含分支独立+冲突消解） |
| 合同审核智能体_法律实务架构.html | 商业律师角色·6大商业维度·合规子调用·冲突消解 |
| 合规审查智能体_法律实务架构.html | 合规律师角色·7大合规领域·否决权·签约结论 |
| 检索智能体_详细架构图.html | 5子节点·多路并行·RRF融合·质量门禁·≤3次重试 |
| 法律问答智能体_详细架构图.html | 实体抽取→Neo4j→Cypher↺答案·降级兜底 |
| 小红书发布智能体_详细架构图.html | 前置过滤·5节点流水线·Playwright发布 |
| 文书生成智能体_详细架构图.html | 7节点·10模板·RAG填充·法条校验环≤3次·持久化 |
| 节点式流程图.html | 节点式交互流程图，展示节点复用关系与调用结构 |
| style.css | 共用样式表，定义深色/浅色双主题、节点卡片、导航栏等基础样式 |