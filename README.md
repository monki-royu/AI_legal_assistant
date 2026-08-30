# 法智引擎 — AI 法律助理（多智能体系统）

> 当前后端为 **官方 LangGraph 1.x 编排 + 6 子图组合 + SqliteSaver 状态持久化 + interrupt 人工确认**，详见文末「架构演进与文档对齐说明」。

## 1 项目背景

国内法律服务存在结构性供需错配：83 万执业律师集中于头部机构，中小微企业与普通民众的长尾法律咨询长期供给不足；金税四期、新《公司法》等新规落地持续抬高企业合规压力。传统法律工具仅支持字面关键词检索，无法甄别法规效力层级、新旧法条冲突；通用大模型存在事实幻觉、推理链路黑盒，难以满足法律业务「可溯源、可复核、可审计」的硬约束。

本人自主研发「法智引擎」人机协同法律 AI 平台，基于 **RAG + LangGraph 多智能体** 完成全流程研发，遵循《律师法》确立「AI 前置辅助、律师终审签章」的合规运行模式。平台一站式覆盖合同审核、合规审查、法律问答、法规/案例检索、文书生成、小红书普法发布等场景。

**设计铁律**：AI 做前置审查 / 辅助生成 / 风险提示；律师做最终决策 / 签章交付。依据《律师法》第 13/28 条，AI 辅助不替代律师专业判断，最终法律文件须经执业律师审核签章。

---

## 2 系统架构总览

```
┌──────────────────────────────────────────────────────────────────────────┐
│                      前端  Streamlit (__006__streamlit/app.py)               │
│  首页智能问答 · 合同审核 · 合规审查 · 文书生成 · 案例检索 · 法规查询 ·         │
│  历史记录 · 小红书发布         (8 类任务卡片 / 流式思考 / 风险高亮)          │
├──────────────────────────────────┬───────────────────────────────────────┤
│  后端 API  FastAPI (__005__fastapi/main.py) │  SSE 流式 + interrupt/resume │
│  /qa /contract/review /compliance/review /docgen/generate                   │
│  /laws/search /cases/search /history /xhs/publish /resume                  │
│  /stream /contract/review/stream /docgen/generate/stream /kb/stats ...     │
├──────────────────────────────────┴───────────────────────────────────────┤
│             LangGraph 编排层 (__004__langgraph_more_nodes)                  │
│  langgraph_main.py: StateGraph + SqliteSaver(checkpointer)                  │
│  两级路由 → 5 入口路径 → 6 子图组合（详见第 3 节）                           │
├──────────────┬───────────────────┬───────────────────┬────────────────────┤
│  LLM 服务层   │   检索服务层         │  知识层            │  外部数据服务层      │
│  ChatOpenAI  │   检索子图(3阶段)    │  5 域 FAISS 索引   │  企查查 MCP(资信)    │
│  (OpenAI兼容) │   Neo4j 图谱召回     │  Neo4j 知识图谱     │  北大法宝 MCP(法条)  │
│              │   BM25/关键词        │  data/* TXT 语料    │                    │
├──────────────┴───────────────────┴───────────────────┴────────────────────┤
│              公共基础设施层 (common/)                                        │
│  config · llm · embedding_model · neo4j_manager · qichacha_client ·         │
│  mcp_beidafabao · history_store · retrieval_shared · alias_normalizer ·     │
│  path_utils · logger · citation_meta · review_context_utils · ouput_graph   │
└──────────────────────────────────────────────────────────────────────────┘
```

**技术选型要点**
- 编排核心：**LangGraph 1.x**（`StateGraph` / `add_conditional_edges` / `compile(checkpointer=...)`），支持子图组合（subgraph composition）、并行 fan-out、Checkpointer 状态持久化、`interrupt()` 人工断点。
- 为何不用「自研兼容层」：项目运行环境为 Python 3.10+（conda `ctm_kg`）/ 3.12，可直接安装 `langgraph>=1.0.0`；官方实现提供工业级 checkpoint / 流式 / 中断能力，自研兼容层仅为早期 Python 3.8 受限环境的过渡方案，当前代码已不再使用（见文末对齐说明）。

---

## 3 LangGraph 多智能体编排

### 3.1 主图拓扑（两级路由 + 6 子图组合）

`__004__langgraph_more_nodes/langgraph_main.py` 是组装器：导入并编译 6 个独立子图，通过 `StateGraph(AgentState)` 注册主图节点与条件边。

```
START
  │
  ▼
xiaohongshu_publish_intent            (Level 1：小红书发布意图?)
  ├─ 是 ─► xhs ─────────────────────────────────────────────► END
  └─ 否 ─► intent_router
                 │
                 ▼  level2_router（按 LEVEL2_PATH_MAP 分 4 组）
        ┌────────────┼──────────────┬──────────────┐
        ▼            ▼              ▼              ▼
  input_source_  r_retrieval      qa            docgen
  router         (独立检索)      (法律问答)     (文书生成)
        │              │              │              │
        │              ▼              ▼              ▼
        │            END            END            END
        ▼
  [doc] doc_extract → doc_empty_guard ──block──► END
  [text] text_recognize ───────────block──► END
        │ (pass)
        ▼
  preprocess (子图) → cc_retrieval (子图·复用) → dual_review (子图) → END
```

**主图注册的节点（13 个）**：`xiaohongshu_publish_intent`、`intent_router`、`input_source_router`、`doc_extract`、`doc_empty_guard`、`text_recognize`、`preprocess`(子图)、`cc_retrieval`(子图)、`dual_review`(子图)、`r_retrieval`(子图)、`qa`(子图)、`docgen`(子图)、`xhs`(子图)。

**条件路由函数（真实签名）**

| 路由函数 | 判断字段 | 分支 |
|---------|---------|------|
| `after_xiaohongshu_intent_router` | `is_xiaohongshu_publish_intent` / `task_type=="xiaohongshu_publish"` | `xhs` / `intent_router` |
| `level2_router` | `LEVEL2_PATH_MAP[task_type]` | `input_source_router`(合同合规) / `r_retrieval`(检索) / `qa`(问答) / `docgen`(文书) |
| `after_doc_empty_guard` | `doc_empty_flag` | `preprocess` / `end` |
| `after_text_recognize` | `text_recognize_flag` | `preprocess` / `end` |
| `input_source_router` 内联 lambda | `uploaded_doc_path` 是否为空 | `doc`(文档提取) / `text`(文本识别) |

### 3.2 六子图清单（真实节点与边）

#### ① 预处理子图 `preprocess_subgraph`（5 节点）
`party_identify → contract_classify → full_text_segment → numeric_extract → llm_query_extract → END`
- 甲乙方识别（不编造空主体）、合同分类、`doc_text` 全文本统一切分（preamble/clause/paragraph，原文零丢失且带编号）、数值抽取、检索查询构造。

#### ② 检索子图 `retrieval_subgraph`（9 节点 + 质量门重试环）
`retrieval_intent_decompose → credit_precheck → retrieval_entity_recall → retrieval_precision_filter → retrieval_fusion_ranking → quality_gate_retry → beida_fabao_gate → credit_check → context_pack(=retrieval_output_pack) → END`
- 质量门 `quality_gate_retry` 不达标回边 `retrieval_intent_decompose`（最多重试 3 次，`quality_max_retries`）。
- 收尾 `context_pack` 打包 `review_context_bundle`（含检索覆盖率、未覆盖段落，显式告诉下游「哪些条款无依据」以防幻觉）。

#### ③ 双审子图 `dual_review_subgraph`（5 节点）
`parallel_dual_review → [contract_review: conflict_resolution → numeric_validate] / [compliance_review: numeric_validate] → risk_aggregate → final_delivery → END`
- `parallel_dual_review` 按 `task_type` 分两路：`task_type == "compliance_review"` 走**单路**——直接执行 `compliance_review_node` 即返回，不进线程池（纯合规任务无需合同审查输出）；合同审核路径才用 `ThreadPoolExecutor(max_workers=2)` **并发**执行 `compliance_review_node` 与 `contract_ai_review_node`，约省 40% 耗时（实测 ~20s→~12s，取较慢者）。并发安全性双重保障：两线程共享同一只读 state 快照、各自返回独立增量 dict 且写入字段不同（`compliance_risk_items` vs `contract_risk_items`）故无竞态；`contract_ai_review` 仅把 `compliance_risk_items` 当可选上下文（`state.get(..., []) or []`，并发读到空也只少参考、不报错）；最终由 `conflict_resolution` 统一收敛双路。
- `conflict_resolution` 实施「合规优先 5 规则」；`can_sign` 三态贯穿全链路；`risk_aggregate` 三路（合同/合规/资信）聚合；`final_delivery` 产出 Markdown 报告。

#### ④ 法律问答子图 `qa_subgraph`（4 节点 + 嵌套检索子图）
`qa_intent_classify → [is_legal_related: qa_retrieval(嵌套 retrieval_subgraph) → legal_qa_final_answer] / [非法律: llm_direct_out] → END`
- 三级路由：先判是否法律问题，法律相关复用检索子图，非法律走 LLM 直答兜底。

#### ⑤ 文书生成子图 `docgen_subgraph`（7 节点 + 澄清守卫）
`doc_case_analyze → [need_clarify? END 追问] → doc_template_match → doc_query_plan → doc_parallel_retrieve(并发法条+类案) → doc_clause_fill → doc_risk_analysis → doc_final_delivery → END`
- `doc_parallel_retrieve` 单节点内 `ThreadPoolExecutor` 并发跑法条检索与类案检索。
- 澄清守卫：信息不足时直接 END 返回追问文案，避免产出残缺文书。

#### ⑥ 小红书子图 `xhs_subgraph`（5 节点）
`xhs_text_generate → xhs_image_generate → xhs_check_text_image → [pass: xhs_auto_publish → xhs_generate_markdown → END] / [fail: END]`
- 图文合规检查不通过直接终止子图（不污染主图状态）；Playwright 自动发布 + 火山引擎文生图。

### 3.3 Checkpointer 与 interrupt 人工确认

- `langgraph_main.py` 优先 `SqliteSaver(checkpoints.sqlite)`（磁盘持久化，支持子进程跨进程 resume），降级 `MemorySaver`，再降级无状态。
- 付费接口（北大法宝、企查查）通过 `langgraph.types.interrupt()` 真中断图执行，前端弹窗询问用户，用户决策经 `graph.invoke(Command(resume=value))` 续跑（`legal_response_resume`）。免费检索连续重试 3 次仍不达标才触发北大法宝付费询问门禁（`fabao_retry_eligible`）。

### 3.4 AgentState（TypedDict, total=False，100+ 字段 / 9 大类）

所有节点以 `def node(state: AgentState) -> dict` 签名运行，仅返回自身修改的字段，框架自动合并。核心字段分组：

| 分组 | 代表字段 |
|------|---------|
| Input | `input`, `uploaded_doc_path`, `contract_type`, `dispute_type`, `plaintiff`, `defendant`, `claims` |
| Routing | `task_type`, `mounted_sources`, `is_xiaohongshu_publish_intent` |
| Document | `doc_text`, `doc_structured_json`(MinerU), `doc_segments`, `doc_clauses`, `review_context_bundle` |
| Numeric | `extracted_numerics` |
| Retrieval | `retrieval_query`, `retrieval_keywords`, `citations`, `quality_score`, `quality_retry_count`, `research_context`, `fabao_retry_eligible` |
| Review | `contract_risk_items`, `compliance_risk_items`, `numeric_risk_items`, `credit_risk_items` |
| Conflict | `post_conflict_risk_items`, `can_sign`, `conflict_log`, `overall_risk_score`, `risk_level` |
| QA/DocGen/XHS | `is_legal_related`, `legal_qa_answer`, `final_document`, `template_id`, `xiaohongshu_*` |
| Infra | `thread_id`, `pending_interrupt`, `need_lawyer_review`, `final_report_markdown`, `output` |

### 3.5 节点复用（subgraph composition）

`retrieval_subgraph` 被复用 3 次：`cc_retrieval`（合同合规）、`r_retrieval`（独立检索）、`qa_retrieval`（问答子图内部嵌套）。所有子图共享 `AgentState` 单一状态总线，避免重复实现。

---

## 4 检索智能体（全系统共享底座）

`retrieval_intent_decompose_node` 按 `task_type` + 关键词规则动态挂载知识源（横向按需挂载），纵向三级检索：

**知识源挂载矩阵（TASK_SOURCE_DEFAULTS + KEYWORD_RULES）**

| 任务 | 默认域 | 关键词追加 |
|------|--------|-----------|
| contract_review / compliance_review / legal_qa | laws+regulations+interpretations+cases | 命中「建设工程/金融贷款/房地产开发」等追加 industry_sources |
| legal_research | laws+regulations+interpretations（纯法规） | 不追加 |
| case_search | cases（单源，跳过融合） | 不追加 |

**三阶段检索链路**
1. **实体召回层** `retrieval_entity_recall`：Neo4j Cypher 4 种 UNION ALL 匹配模式（实体匹配/条款号精确/法律名/全文兜底）+ 5 域 FAISS 向量召回 + 关键词扩展；按 7 类检索意图（`definition/penalty/liability/regulates/condition/parties/general`，确定性标记词，不调 LLM）对关系做偏置召回（×1.3）。
2. **精准过滤层** `retrieval_precision_filter`：关键词 AND + FAISS 重排 + 权威过滤，每条带 `precision_score`。
3. **融合排序层** `retrieval_fusion_ranking`：RRF 倒数排名融合 + 权威加权线性融合（`fusion_mode`：单源直查 / 多源加权），7 维权威分（`retrieval_eval`）。
4. **质量门** `quality_gate_retry`：质量分 < 阈值回边重试（≤3 次）。
5. **付费门禁** `beida_fabao_gate`：仅免费重试 3 次仍不达标才 `interrupt()` 询问是否调北大法宝 MCP。
6. **资信查询** `credit_check`：企查查 MCP，`interrupt()` 确认后调用，产出 `credit_risk_items` 流入双审聚合。

---

## 5 双审与冲突消解（法律实务核心）

`conflict_resolution_node` 实施「合规优先 5 规则」：

| 规则 | 条件 | 效果 | 法律依据 |
|------|------|------|---------|
| 1 否决权 | 合规 `critical` | `can_sign="no"` | 民法典第153条（违反强制性规定无效） |
| 2 强制整改 | 合规 `high` | `can_sign="conditional"` | 高风险但未构成无效 |
| 3 双重发现 | 同 clause 双路命中 | 以合规 severity 为准，`source="compliance+contract"` | 合规（客观）> 合同（商业） |
| 4 保留商业风险 | 仅合同命中 | 保留为 `contract_only` 可谈判风险 | 合规通过≠商业合理 |
| 5 结论冲突 | 合同说可接受+合规说违法 | 以合规为准，强制不可谈判 | 商业利益不凌驾法律 |

**签约结论三态**：`pass`（可签约）/ `conditional`（须整改）/ `no`（不得签约，无论评分多高）。`risk_level`：Low(≥81)/Medium(61-80)/High(<60)；High 触发 `need_lawyer_review`。

---

## 6 数值校验规则引擎（确定性 · 零幻觉）

`numeric_validate_node` 加载 `config/rules/*.yaml`，**不调用 LLM**，做三类确定性校验：
- `threshold`（单值阈值比较）、`range`（区间）、`sum_equals`（多项求和=预期，如付款比例和=100%）。
- 支持中文数字智能转换（千分之三 / 50万 / 百分之五）。
- 规则与代码分离：法务直接维护 YAML 即可，无需改代码。

| 规则文件 | 维度 | 代表规则 |
|---------|------|---------|
| `contract_review.yaml` | 8 维（金额/税务/付款/违约金/质保金/量价/期限/行业基准） | 单价×数量=小计、违约金≤30%、预付款≤30%、时序颠倒 |
| `compliance_review.yaml` | 8 维（招投标/工程比例/政府采购/违约金红线/数据合规/财税/行业监管/内部制度） | 施工≥400万须公开招标(critical)、履约保证金≤10%、数据出境安全评估、利率≤LPR4倍 |

`contract_review.yaml` 与 `compliance_review.yaml` 双向引用（违约金 30% 上限：合规做「违规判定」，合同做「商业保护力度」分析），合规结论不可被合同结论降级或覆盖。

---

## 7 法律问答 / 文书生成 / 小红书

- **法律问答子图**：见 3.2④。复用检索子图，非法律问题 LLM 直答兜底，保证「不编造法条」。
- **文书生成子图**：见 3.2⑤。10 类纠纷模板匹配（`template_id`），RAG 填充条款，法条/类案并发检索，风险提示。
- **小红书子图**：见 3.2⑥。文案→配图（火山引擎）→合规质检→Playwright 自动发布→Markdown 存档。

---

## 8 数据流水线（知识底座构建）

```
__001__clawler (爬取/解析)  →  __002__extract_information (实体关系抽取)
   →  __003__create_neo4j_database (建图谱+导Neo4j+导元数据+建FAISS)
   →  data/knowledge_base/index/*.index (5 域向量索引) + Neo4j 图谱
```

- **采集** `__001__clawler`：`flk_client`（法律法规库）、`cases_collector`（案例）、`interpretation_crawler`（司法解释）、`__002__crawl_law_database.py`、`__000__获取网页内容通用方法.py`、`data_prep.py`。
- **抽取** `__002__extract_information/__001__extract_legal_data.py`：按 5 类知识源（law/regulation/case/interpretation/industry）抽取实体-关系-属性三元组，产出 `extract_*_data.json`（含 finetune 版本）。
- **构建** `__003__create_neo4j_database/__000_main_build_graph.py` 编排：`step0` 清空 → `step1` 抽取 → `step2` 导入 Neo4j（`__001__legal_graph_importer.py`）→ `step3` 导出元数据（`__002__export_legal_metadata.py`）→ `step4` 验证 → `step5` 建 FAISS（`__003__faiss_embedding.py`）。
- **语料规模**：`data/laws`(89)、`data/regulations`(72)、`data/interpretations`(55)、`data/cases`(30)、`data/industry_sources`(6) 份 TXT。

**知识源存储选型**

| 知识域 | 主存储 | 辅助存储 |
|--------|--------|---------|
| laws / regulations / interpretations / cases / industry_sources | FAISS 向量索引（`bge-m3` 嵌入） | Neo4j 图谱（实体-关系跨域查询）|
| 跨域关联 | Neo4j 图谱 Cypher（非独立源，是 5 域的跨域查询手段） | — |
| 外部 API | 企查查 / 北大法宝 MCP | 关键词触发挂载 |

---

## 9 公共基础设施（common/）

| 模块 | 职责 |
|------|------|
| `config.py` | 读取 `.env`，集中管理 API Key / Neo4j / 企查查 / 北大法宝 / 嵌入模型路径等配置 |
| `llm.py` | `my_llm` 模块级单例，LangChain `ChatOpenAI`（OpenAI 兼容，DeepSeek/通义/智谱等） |
| `embedding_model.py` | `embedding_model` 单例，`sentence-transformers` 加载本地 `bge-m3` |
| `neo4j_manager.py` | `neo4j_client` 单例，Cypher 执行 / 健康检查 / 元数据导出 / EXPLAIN 预检 |
| `qichacha_client.py` | 企查查 MCP 客户端，三级降级（Bearer Token → AppKey+MD5 → Mock），10 维资信 |
| `mcp_beidafabao.py` | 北大法宝付费 MCP 客户端（search_all） |
| `retrieval_shared.py` | 检索共享工具：`_ask_user_interrupt`（interrupt 封装）、主体归一化 |
| `history_store.py` | 历史记录持久化（CRUD / 收藏 / 导出） |
| `alias_normalizer.py` | 查询/关键词同义词扩展与归一化 |
| `path_utils.py` | 相对路径→绝对路径（基于工程根定位） |
| `logger.py` / `citation_meta.py` / `review_context_utils.py` / `ouput_graph_utils.py` / `finetune_utils.py` | 日志 / 引用元数据 / 审查上下文 / 流程图绘制 / 微调数据 |

**关键设计**：Embedding 模型、LLM 客户端、Neo4j 客户端均采用模块级单例，规避重复加载，减少内存与算力消耗。

---

## 10 前端设计（Streamlit）

`__006__streamlit/app.py`（4200+ 行）：
- **10 类任务类型** 集中在 `TASK_META` 字典，首页以多色卡片呈现，按任务类型分流渲染。
- **后端可用探测**：启动尝试 `import` 后端接口，`HAS_BACKEND=True` 走真实 LangGraph；否则 `HAS_BACKEND=False` 自动切换演示模式（内置演示数据）。
- **流式输出**：`legal_response_stream` 逐块 yield 节点状态，前端渲染「思考过程」动画。
- **交互特性**：风险卡片三态（采纳/不采纳/修改）、原文四色高亮（critical/high/medium/low）、点击高亮跳转风险卡片（字段加权匹配 + 停用词过滤）、合规否决横幅（`can_sign="no"/"conditional"`）。
- **子进程隔离**：合同/合规等重任务经 `_run_backend_isolated` 子进程调用，stdout 专用于单行 JSON，保护 JSON 不被流程图 print 污染。

---

## 11 后端 API（FastAPI）

`__005__fastapi/main.py`（1450 行），RESTful + SSE 流式 + interrupt/resume：

| 路由 | 说明 |
|------|------|
| `GET /health` | 健康检查 |
| `POST /qa` | 法律问答 |
| `POST /contract/review` · `/contract/review/stream` | 合同审核（结构化 / SSE 流式） |
| `POST /compliance/review` | 合规审查 |
| `POST /docgen/generate` · `/docgen/generate/stream` | 文书生成（结构化 / SSE 流式） |
| `GET /laws/search` | 法规查询（按法规名/条款号/关键词，返回效力状态） |
| `GET /cases/search` | 案例检索（按案由/法院层级筛选） |
| `GET/POST /history` · `GET /history/{id}` | 历史记录 CRUD |
| `POST /xhs/publish` | 小红书发布 |
| `POST /resume` | interrupt 续跑（付费确认回填） |
| `GET /dispute-types` · `/template-types` · `/kb/stats` | 元数据 / 模板 / 知识库统计 |
| `POST /stream` | 通用 SSE 流式（逐节点输出） |

响应统一经 `STATE_TO_API_MAP` 字段投影，并透传 `thread_id` / `pending_interrupt`（前端据此弹确认窗）。

---

## 12 测试与评估体系（test/）

`test/` 是一套**真实后端评估 harness**，可量化系统质量并支撑面试复盘：

| 文件 | 职责 |
|------|------|
| `t_runner.py` | 测试执行引擎：装载用例→执行→采集指标→失败归因→落盘 `raw_results.json`，支持 `--task/--case/--category/--limit/--resume` 断点续跑 |
| `t_config.py` | 环境注入、`TASK_META` 任务元数据、`SAFE_MODE`（外部调用打桩） |
| `t_datasets.py` | 测试用例集（normal/boundary/exception/negative 类别） |
| `t_probe_data.py` | 加载知识库 manifest（文档数/条款数/案例节点数） |
| `t_tracer.py` | 节点轨迹追踪（`trace_run`）+ LLM 调用采集器（`_COLLECTOR`）+ 历史记录直测 |
| `t_metrics.py` | 指标：检索 P@5 / R@10 / MRR、执行成功率、业务成功率、人工修改率（代理/严格）、延迟 P50/P95、Token 与成本 |
| `t_report.py` | 汇总报告生成 |

**可演示的评估能力**：分支识别（`detect_branch` 从节点轨迹推断实际走了哪条路径）、路由/状态断言、质量缺陷检测、`attribute_failure` 失败归因、`SAFE_MODE` 安全评测（企查查/北大法宝打桩）。这套 harness 是面试讲解「全链路可观测、可量化」的硬证据。

---

## 13 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| 前端 | Streamlit + 自定义 CSS | Web 界面，10 任务卡片 / 流式思考 / 风险高亮 |
| 后端编排 | **LangGraph 1.x**（`StateGraph` / `compile(checkpointer=SqliteSaver)`） | 6 子图组合 + 条件路由 + interrupt 人工确认 |
| 后端服务 | FastAPI + uvicorn | RESTful + SSE 流式 + resume |
| 大模型 | OpenAI 兼容接口（ChatOpenAI） | DeepSeek/通义/智谱等，模块级单例 |
| 向量检索 | FAISS（faiss-cpu） | 5 域实体向量近邻 + `bge-m3` 嵌入 |
| 知识图谱 | Neo4j（neo4j Python Driver） | 法律实体-关系图谱，Cypher 精确/跨域召回 |
| 关键词检索 | BM25 + 正则 + 同义词归一化 | 本地 TXT 关键词扫描兜底 |
| 融合排序 | 自研 RRF 倒数排名 + 权威加权 | 多路结果归一重排 |
| Embedding | sentence-transformers（bge-m3） | 文本向量化，单例 |
| 文档解析 | MinerU（magic-pdf，可选） | 多模态解析（文本/表格/印章 + bbox），降级本地纯文本 |
| 图像生成 | 火山引擎文生图 | 小红书配图 |
| 自动发布 | Playwright | 小红书平台自动发布 |
| 资信查询 | 企查查 MCP API | 工商/失信/被执行/经营异常/行政处罚 |
| 法条兜底 | 北大法宝 MCP | 付费 API 真实法条检索（质量门 3 次重试后触发） |
| 规则引擎 | PyYAML | 数值校验规则与代码分离 |
| 数据处理 | pandas / numpy / openpyxl | 法规数据处理 |
| 配置 | python-dotenv | `.env` 环境变量 |
| 测试 | argparse + json | 后端评估 harness（P/R/MRR/成本） |

---

## 14 部署

### 14.1 环境要求
- Python 3.10+（官方 LangGraph 1.x 需要；conda `ctm_kg` 已验证）
- Neo4j 5.x（知识图谱运行时依赖；检索子图亦依赖 Neo4j Cypher 召回）
- 依赖见 `requirements.txt`（`langgraph>=1.0.0` 等）

### 14.2 配置
项目根目录 `.env`：`MODEL_API_KEY` / `MODEL_BASE_URL` / `MODEL_NAME` / `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` / `QICHACHA_AUTHORIZATION`(或 `QICHACHA_APP_KEY`+`QICHACHA_SECRET_KEY`) / `EMBEDDING_MODEL_PATH` 等。

### 14.3 启动
```bash
pip install -r requirements.txt

# 启动 Streamlit 前端
python -m streamlit run __006__streamlit/app.py --server.port 8501

# 或启动 FastAPI 后端（供前端/外部调用）
uvicorn __005__fastapi.main:app --host 0.0.0.0 --port 8000
```

### 14.4 运行测试 harness
```bash
python -m test.t_runner                 # 全量
python -m test.t_runner --task legal_qa # 单任务
python -m test.t_runner --limit 3       # 冒烟（前 3 条）
python -m test.t_runner --resume        # 断点续跑
```

---

## 15 架构演进与文档对齐说明

旧版 README 与当前代码的主要差异（已在本版修正）：

| 旧版 README 描述 | 当前代码事实 | 证据 |
|-----------------|-------------|------|
| 自研 `common/langgraph_compat.py` 兼容层 | 该文件不存在、无任何引用；使用官方 `langgraph.graph.StateGraph` | `langgraph_main.py:109` |
| 扁平 21 节点单图 | 6 子图组合（`subgraphs/*.py`），主图 13 节点 | `langgraph_main.py` 各 `add_node` |
| 未启用 Checkpointer | `SqliteSaver`（降级 `MemorySaver`）持久化 + `interrupt()` 续跑 | `langgraph_main.py:171-192` |
| 旧节点名（context_pack/retrieval_result_summarize 等） | 实际 `context_pack` 已并入 `retrieval_output_pack_node`；双审合并且并发 | `retrieval_subgraph.py` / `dual_review_subgraph.py` |
| 检索 7 节点链 | 实际 9 节点（含 `credit_precheck` / `beida_fabao_gate` / `credit_check`） | `retrieval_subgraph.py` |
| 文档预处理 6 节点（含 doc_extract） | 预处理子图 5 节点；`doc_extract`/`text_recognize`/`doc_empty_guard` 上移到主图 | `preprocess_subgraph.py` |
| 双审串行 | `parallel_dual_review` 用 `ThreadPoolExecutor` 并发 | `parallel_dual_review_node.py` |

