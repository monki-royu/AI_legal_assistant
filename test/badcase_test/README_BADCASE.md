# 法智引擎 · Badcase 测试库与分析说明

> 测试目标：基于 `__003__create_neo4j_database/__000_main_build_graph.py` **已成功处理**的 9 个法律数据文件，构造与现有数据强相关的代表性 badcase 测试集，验证各流程 / 各节点 / 各状态 / 各函数是否**正常运行、状态正常流转、效果是否合格**。
>
> **范围边界（重要）**：本次测试**不评价**因「数据规模/质量」导致的准确率、召回率偏低（那需要更长时间的数据处理，属于另一阶段）。本库聚焦**代码与架构层面的流程正确性、状态流转、节点健壮性**——即"库里有、但流程跑不通 / 查不到 / 状态不闭环"这类 badcase。

---

## 0. 测试资产一览

| 文件 | 作用 |
|---|---|
| `bc_cases.py` | 10 条代表性 badcase 用例 + golden 判据（严格锚定 9 个已处理文件） |
| `bc_probe.py` | 静态/规则探针：不跑图即可验证环境、挂载、阈值、引用字段等（生成 `probe_report.json`） |
| `bc_runner.py` | 执行器：复用 `t_tracer`/`t_metrics` 跑图，产出 `badcase_results.json`（节点轨迹 + 指标） |
| `bc_report.py` | 生成 `badcase_flow.html`：**状态流转图 + 全量指标看板**（技术 + 业务指标） |
| `probe_report.json` / `badcase_results.json` | 上述两份产物 |
| 本文件 | 分析方法论、三优先问题、最小改动方案、评测指标、人工接管方式、优化方向 |

**复现命令**（在项目根目录的 conda `ctm_kg` 下）：

```bash
cd test/badcase_test
LEGAL_DISABLE_PNG=1 LEGAL_DISABLE_INTERRUPT=1 python bc_probe.py      # 静态探针
LEGAL_DISABLE_PNG=1 LEGAL_DISABLE_INTERRUPT=1 python bc_runner.py     # 跑全量 10 条
LEGAL_DISABLE_PNG=1 python bc_report.py                               # 生成 HTML 看板
```

---

## 1. Agent 结构（代码级现状）

### 1.1 主线编排
`langgraph_main.py` 构建主图，入口为一个**意图分发器** `intent_router_node`（`nodes/intent_router_node.py`）。

**实际 6 个任务类型 → 4 个子图**（`LEVEL2_PATH_MAP`）：

| task_type | 中文 | 路由子图 | 关键节点 |
|---|---|---|---|
| `contract_review` | 合同审核 | `contract_compliance` | text_recognize → risk_aggregate → final_delivery |
| `compliance_review` | 合规审查 | `contract_compliance` | 同上（合规口径） |
| `legal_research` | 法律检索 | `retrieval` | intent_decompose → entity_recall → precision_filter → fusion_ranking → quality_gate_retry → output_pack |
| `case_search` | 案例检索 | `retrieval` | 同上 |
| `legal_qa` | 法律问答（兜底） | `legal_qa` | qa_intent →（双路 RAG：cypher + 共享检索子图）→ final_answer |
| `legal_document_gen` | 文书生成 | `legal_document_gen` | case_analyze → template_match → query_plan → 生成 |

> 注：项目记忆中提到的"法规查询"在代码中并入 `legal_research`（检索子图统一处理 laws/regulations/interpretations/cases/industry_sources 五源）。异常时统一降级为 `legal_qa`。

### 1.2 检索子图节点链（badcase 高发区）
`subgraphs/retrieval_subgraph.py` 顺序：
1. `retrieval_intent_decompose_node` — 拆解检索意图 / 关键词
2. `retrieval_entity_recall_node` — **实体召回（含 Neo4j Cypher + FAISS 双路）**
3. `retrieval_precision_filter_node` — 精排过滤
4. `retrieval_fusion_ranking_node` — 融合排序（阈值：`fusion_single_source_threshold=50`，`fusion_multi_threshold=60`）
5. `quality_gate_retry_node` — 质量门 + 重试（`quality_gate_threshold=60`，`max_quality_retries=3`）
6. `retrieval_output_pack_node` — 组装引用包（`citations`）

### 1.3 知识源挂载机制（关键）
`retrieval_intent_decompose_node` 按 `KEYWORD_RULES` 的触发词决定挂载哪些知识源。
- **laws / regulations / interpretations / cases** 为"默认挂载源"，任何检索都挂。
- **industry_sources** 为"条件挂载源"，仅在用户输入命中其触发词组（建设工程 / 商品房买卖 / 金融借款等）时才挂载。

### 1.4 状态字段（AgentState）
贯穿全链路的关键字段：`task_type`、`mounted_sources`、`recall_results`/`retrieval`（实体召回）、`citations`（引用）、`quality_score`/`quality_retry_count`、`need_lawyer_review`（合同/合规）、`human_intervention_needed`（付费接口北大法宝门禁）、`final_state`。

---

## 2. 测试方法

- **Golden 锚定**：每条用例的 `keywords`/`focus`/`hypothesis` 均来自已处理的 9 文件（个人信息保护法、个人独资企业法、不动产登记暂行条例、个人所得税法实施条例、劳动法司法解释、妨害信用卡管理司法解释、住建部标准、城市房屋租赁管理办法、case_698be5cb…）。
- **探针（bc_probe）**：在**不跑图**的前提下，用真实 FAISS 实体名（`*_id2text.pkl`）与 `KEYWORD_RULES` 计算"挂载感知可达性"——即"该关键词在**已挂载源**上有多少实体命中（有效命中），多少命中全落在未挂载源（孤儿命中）"。这是定位"库里有、查不到"的根因利器。
- **执行器（bc_runner）**：跑真实图，记录 `node_trace`（逐节点耗时/状态增量）、`full_route`、`defects`、`state_failures`、`causes`，并按 `quality_checks` 规则判定。
- **指标**：见第 4 节。

---

## 3. 现状快照（探针 + 动态跑通）

> 你已在 2026-08-31 20:09 重启 Neo4j。下方保留**宕机基线**作对照，并给出**恢复后重测**结论。两套结果文件均已留存：`badcase_results_neo4j_down.json`（基线）/ `badcase_results.json`（当前在线），看板对应 `badcase_flow_neo4j_down.html` / `badcase_flow.html`。

### 3.1 两条基线对比（核心）

| 维度 | Neo4j 宕机（历史基线） | **Neo4j 恢复后（本次重测）** |
|---|---|---|
| 图谱连通 | ✗ `ServiceUnavailable` | ✓ 653 概念 / 224 条文 / 全图就绪 |
| 空召回条数（适用检索）| 10/10 | 3 条（BC-04 / BC-05 / BC-06 适用检索却 0 引用；汇总计 5 含 BC-08/09 无检索需求）|
| 平均检索 P / R / MRR | 全 0（架构塌缩）| **P=0.325 / R=0.500 / MRR=0.525**（8 条适用）|
| 业务成功率 | 30% | **80%** |
| 延迟 P50 / P95 / Max | 155 / 440 / 600s | **141 / 448 / 600s** |
| 触发重试条数 | 7 条（全链路重跑）| 2 条（仅 case_search）|
| 总成本 / Token / LLM 次 | ¥0.043 / 11,928 / 35 | ¥0.098 / 23,003 / 30 |

> **结论**：Neo4j 恢复后，挂载域（laws / regulations / interpretations / cases + 已挂载 industry）的 KG/FAISS 检索**全面复活**，P/R 从 0 回到 0.2~1.0。但**三处结构性缺陷与 Neo4j 可用性无关、依然独立存在**（详见 §4）：① P0 — 房屋租赁因 industry 未挂载，BC-06 合同路径**直接挂死 600s**；② P2 — legal_qa 引用 `law_name` 恒空 + 内容截断；③ BC-01 的 condition 意图 **KG 通道=0**（图谱在线也失效）。

### 3.2 探针（环境 / 挂载）

Neo4j `bolt://localhost:7687` **当前可用**（683 节点 / 1003+ `DEFINES` 关系 / 224 条文）。

**挂载感知可达性（最关键诊断）**——有效命中=0 即"库里有、检索通道够不到"：

| 用例 | 任务 | 有效命中 | 孤儿命中 | 判定 |
|---|---|---|---|---|
| BC-05 | 房屋租赁·案例(库外负样本) | **0** | 9 | ❌ 完全不可达 |
| BC-06 | 房屋租赁·合同审核 | **0** | 10 | ❌ 完全不可达（且合同路径挂死 600s）|
| BC-07 | 个人信息·合规 | 127 | 0 | ✅ 可达 |
| BC-10 | 建设工程·合同审核 | 13 | 0 | ✅ 借光挂载 |
| BC-04 | 违法解除·案例 | 27 | 16 | ⚠️ 部分丢失（"劳动合同"14 命中孤儿）|
| BC-08 | 不含行业词·合同审核 | 29 | 11 | ⚠️ 部分丢失 |

### 3.3 动态逐条结果（Neo4j 恢复后重测）

| ID | 任务 | 业务 | 延迟(s) | 引用 | P | R | MRR | 图谱通道 | 备注 |
|---|---|---|---|---|---|---|---|---|---|
| BC-01 | legal_qa | ✓ | 152 | 12 | 0.20 | 0.50 | 0.20 | **图谱=0** | KG 对 condition 意图失效；第十三条内容被截断 |
| BC-02 | legal_research | ✓ | 172 | 12 | 0.20 | 0.50 | 1.00 | 图谱=159 | ✓ 正常 |
| BC-03 | legal_research | ✓ | 130 | 12 | 0.80 | 1.00 | 1.00 | 图谱=129 | ✓ 优 |
| BC-04 | case_search | ✗ | 59 | 0 | 0 | 0 | 0 | — | 案例库仅 1 条，违法解除无命中（数据覆盖）|
| BC-05 | case_search | ✓ | 262 | 0 | 0 | 0 | 0 | — | 库外负样本，重试 3 次仍 0（符合预期）|
| BC-06 | contract_review | ✗ | **600** | 0 | 0 | 0 | 0 | — | ❌ **挂死撞超时**（industry 未挂载→检索空→合同路径无降级）|
| BC-07 | compliance_review | ✓ | 157 | 12 | 0.80 | 1.00 | 1.00 | 图谱=96 | ✓ 优 |
| BC-08 | contract_review | ✓ | 0.02 | 0 | n/a | n/a | n/a | — | 守卫拦截（SAFE_MODE）|
| BC-09 | legal_document_gen | ✓ | 3.24 | 0 | n/a | n/a | n/a | — | 文书生成，无需检索 |
| BC-10 | legal_qa | ✓ | 45 | 12 | 0.60 | 1.00 | 1.00 | 图谱=24 | ✓ 借光挂载 |

> 注：BC-01 的 P/R 已用**修正版匹配器**重算（条款号 `article_no` 命中即计分，避免 content 截断 / 空 `law_name` 误判为 0）。其 12 条引用中确实召回了"第十三条"，但 content 仅取到导语"符合下列情形之一的，个人信息处理者方可处理个人信息："，且漏召回"第十四条"（同意的自愿/明确要件）——故 R=0.5 反映"找到主条款但未取全"。

### 3.4 关键新发现（Neo4j 在线才暴露）

1. **FAISS 软依赖 Neo4j 的实体名映射**：宕机时 FAISS 反查也报 `Couldn't connect to localhost:7687`；恢复后 FAISS 正常返回 30/40。即 FAISS **并非完全独立的本地路**——它依赖图做实体名映射，**Neo4j 宕机时连带失效**（这正是 P1 架构耦合的实证），但**恢复后两者都正常**。
2. **KG cypher 通道对 BC-01 的 condition 意图失效**：图谱在线、且 BC-02/03/07/10 的「图谱」分别为 159/129/96/24，唯独 BC-01（意图偏置 `condition`、锚点"无需同意处理个人信息"）「图谱=0」——KG cypher 构造对条件类意图存在盲区，与可用性无关。
3. **引用 `law_name` 在 legal_qa 路径恒为空**（BC-01、BC-07 实测），且 BC-01 的第十三条 content 被截断——引用溯源与内容完整性双重缺陷（P2）。
4. **BC-06 合同审核挂死 600s**：比"查不到"更严重——未挂载域的合同路径无任何超时/降级，直接撞 `CASE_TIMEOUT_SEC=600`（P0 最坏形态）。

### 3.5 FAISS 长实体占比（嵌入噪声源，静态）

interpretations **28.2%**、cases **24.2%**、laws 17.4%、regulations 15.2%、industry 5.5%（最长 61 字符，多为整条"第X条…"标题，作为实体名会稀释向量语义）。

---

## 4. 三个最高优先问题（含最小改动方案）

> 排序依据：**影响面 × 严重度 × 修复成本**。均不改业务逻辑，只做最小代码修补。

### 🔴 P0 — `industry_sources` 挂载缺失：房屋租赁类业务"库里有、永远查不到"

- **现象**：BC-05、BC-06 的 4 个核心关键词（房屋租赁 / 租赁期限 / 违约金 / 押金）在库内共 10~18 个实体命中，**100% 落在 industry_sources 且挂载内命中 = 0**。即《城市房屋租赁管理办法》第四条（租期≤20 年）、违约金规则在结构上**不可能被召回**。
- **根因**：`KEYWORD_RULES` 的 industry 触发词组为「建设工程 / 商品房买卖 / 金融借款」三组，**不含"房屋租赁"**。挂载判定纯靠关键词命中，导致租赁合同类业务永远不挂 industry_sources。BC-10 则因句中有"建设工程"四字**借光挂载**才拿到 1 个有效命中——典型「能不能查到取决于同一句里有没有碰巧出现无关行业词」。
- **影响**：所有"房屋租赁 / 租赁合同"类咨询的法规依据彻底缺失，且用户/运营无感知。
- **最小改动方案（不重构）**：
  1. 在 `KEYWORD_RULES` 的 industry 组追加触发词：`房屋租赁`、`租赁合同`、`出租`、`承租人`、`押金`、`租期`，并补 `租赁` 模糊触发；
  2. 或在 `retrieval_intent_decompose_node` 增加"**语义/实体兜底挂载**"：当默认源召回为空但 FAISS 全库命中 >0 时，自动挂载其命中所在源（消除"孤儿命中"）。
  3. **验收**：重跑 BC-05/BC-06，挂载感知有效命中 >0，且 BC-10 不再依赖"借光"。

### 🔴 P1 — KG 韧性缺口：FAISS 软耦合 Neo4j + condition 意图 KG 盲区 + 空结果无接管

> 宕机基线结论（仍有效，作为可用性风险留存）：Neo4j 宕机时本批 **10/10 空召回**、业务成功率 30%、平均延迟 183s、最大 600s 撞超时——`retrieval_entity_recall` 的「FAISS 实体反查」也报 `Couldn't connect to localhost:7687`，证实 **FAISS 依赖图做实体名映射，并非独立本地路**。

- **现象 A（在线仍存的耦合风险）**：FAISS 索引与实体名映射未本地化，Neo4j 一旦再宕，图谱与 FAISS 双路同崩（见 3.1 基线）。
- **现象 B（条件意图 KG 盲区，新发现）**：图谱在线、且 BC-02/03/07/10 的「图谱」召回分别为 159/129/96/24，唯独 **BC-01（legal_qa，意图偏置 `condition`、锚点"无需同意处理个人信息"）「图谱=0」**——KG Cypher 构造对**条件类意图**存在盲区，与可用性无关，纯属查询编排缺陷。
- **现象 C（空结果无接管）**：`quality_gate_retry_node` 只重试不设连接级快速失败；空召回时**不置 `human_intervention_needed`**，前端只收到"无依据"，用户/运营无感知。
- **最小改动方案**：
  1. Cypher 调用包**连接超时熔断**（`connection_acquisition_timeout=5s`），达阈值即标记 `kg_unavailable=True` 并跳过后续重试，将单次检索从 ~183s 压到秒级；
  2. **本地化 FAISS 实体名映射**（不依赖图连接），使 `kg_unavailable` 时仍能走纯本地 FAISS 召回；
  3. 修复 `retrieval_entity_recall_node` 的 **condition 意图 Cypher 分支**，使 KG 路径对"无需同意的例外情形"类查询能命中（BC-01 实证漏召回第十三条的 KG 来源）；
  4. 空召回 / `kg_unavailable` 时置 `human_intervention_needed=True`，答案加降级横幅。
  5. **验收**：断 Neo4j 重跑 BC-01/BC-02 → 延迟 <10s 且有本地 FAISS 命中；Neo4j 在线重跑 BC-01 → 「图谱」>0。

### 🟠 P2 — 引用溯源断裂（law_name 空）+ 内容截断 + 分类失真

- **现象 A（引用丢失）**：探针确认 `retrieval_entity_recall_node.py` 的 Cypher 已 `SELECT law.name AS law_name`，但组装 `recall_results` 时**未写入 `law_name`**；legal_qa 路径下 12 条引用 `law_name` **恒为空**（BC-01、BC-07 实测），用户看到的引用形如「违约金 第七条」无法溯源到具体法律。
- **现象 B（内容截断，新发现）**：BC-01 召回的"第十三条"引用 `content` 仅取到导语"符合下列情形之一的，个人信息处理者方可处理个人信息："，**枚举项（取得同意 / 订立合同所必需等）被截断**——chunk 切分过碎导致法条正文不完整，直接拉低答案可用性。
- **现象 C（分类失真）**：`retrieval_output_pack_node` 用 `title` 是否含"法规/法律/法典"猜分类，而 `title` 实为**实体名/概念名** → 分类**几乎 100% 落"其他"**，下游无法按源过滤。
- **最小改动方案**：
  1. `retrieval_entity_recall_node` 组装时补 `law_name`（来自 Cypher 已选字段）；
  2. 调整 FAISS chunk 切分粒度（按法条/条款边界切，避免截断条文主体）；
  3. `retrieval_output_pack_node` 改按 `source`（laws/regulations/…）硬分类，弃用 title 关键词猜测；
  4. **验收**：任意有 law_name 的引用字段完整；BC-01 的第十三条 `content` 含完整枚举项。

---

## 5. 评测指标（看板与后续回归统一口径）

| 指标 | 含义 | 类型 | 本库观测方式 |
|---|---|---|---|
| **检索精确率 Precision@5** | 前 5 召回中与意图相关比例 | 技术 | golden 关键词 vs 实际召回 `source`/`entity` |
| **召回率 Recall@10** |  golden 应召回实体被覆盖比例 | 技术 | 挂载感知有效命中 / golden 期望命中 |
| **MRR** | 首个相关结果排名倒数均值 | 技术 | 由 `recall_results` 排名计算 |
| **任务成功率** | 路由正确 + 业务判定通过 | 业务 | `route_ok` ∧ `biz_success` |
| **人工修改率** | 需人工接管/修订的占比（代理+严格两档） | 业务 | `needs_manual_edit` / `_strict` |
| **响应时间** | P50/P95/Max 延迟（秒） | 技术/体验 | `node_trace` 累计；当前 BC-02=183s 为反例 |
| **调用成本** | Token（prompt/completion）、LLM 次数、¥估算 | 成本 | `tokens_*` / `cost` |

> **代理说明**：Neo4j 恢复后，"检索精确率/召回率/MRR"已脱离空转、反映真实召回能力（均值 P=0.325 / R=0.500 / MRR=0.525）。宕机时的全 0 是**架构性检索塌缩**本身（P1 的 badcase 证据），不代表数据层面准确率问题（用户已明确不评数据层面）。探针的"挂载感知有效命中"是隔离了基础设施后的**纯净召回能力**指标，仍用于定位"库里有查不到"。

---

## 6. 人工接管方式（现状与缺口）

**现状（代码真实行为）**：
- ✅ **合同/合规审查**：`risk_aggregate_node.py:246` 按风险分写 `need_lawyer_review`，`final_delivery_node.py:66` 读取并决定是否律师复核——这是**已落地**的人工接管出口。
- ⚠️ **北大法宝付费门禁**：`beida_fabao_gate_node.py` 在不可用时置 `human_intervention_needed=True`——但仅限付费接口，与主检索无关。
- ❌ **检索/legal_qa 空结果**：Cypher 失败、0 引用时**无任何接管信号**，用户静默得到"无依据"。

**建议的统一接管出口（最小改动，待决策）**：
1. 定义单一字段 `human_intervention_needed` 的置位规则：空召回 / KG 不可用 / 质量门连续失败 / 低置信答案 → 置位。
2. `final_state` 透传该字段 + 原因码（如 `KG_DOWN`、`EMPTY_RECALL`、`LOW_CONF`），前端据此展示"⚠ 建议人工复核"并进入人工队列。
3. 与现有 `need_lawyer_review` 区分：**律师复核**（内容专业性） vs **人工接管**（流程/服务可用性异常），二者可叠加。

---

## 7. 优化方向（结构级，待你决策后实施）

1. **挂载策略升级**：从"关键词触发"→"实体/语义驱动 + 孤儿自动挂载"，根治 BC-05/06 类不可达（对应 P0）。
2. **KG 韧性**：连接超时熔断 + FAISS-only 降级 + 不可用横幅（对应 P1）。
3. **引用与可观测性**：补全 `law_name`、按 `source` 硬分类、节点级耗时与状态写入 `final_state`（对应 P2）。
4. **长实体治理**：对 interpretations/cases 中 >30 字符的实体做切分/归一，降低嵌入噪声（长实体占比 28%/24%）。
5. **评测闭环**：将本 `bc_runner` + `bc_report` 接成 CI 回归，每次代码改动后自动跑 10 条并比对指标漂移。

> 以上均**未改动任何业务代码**，仅在本测试库内新增文件。具体落地方案待你对所有测试跑完、确认优先级后决策。

---

## 8. 代表性测试集与测试语句（10 条）

| ID | 任务 | 锚定数据 | 核心测试语句（示例） | 关注点 |
|---|---|---|---|---|
| BC-01 | 法律问答 | 个人信息保护法 | "处理个人信息需要满足哪些同意条件？" | KG 双路融合；industry 不相关时默认源召回 |
| BC-02 | 法律检索 | 个人独资企业法 | "设立个人独资企业需要满足哪些条件？" | ✅ Neo4j 恢复后正常（P=0.20/R=0.50，图谱=159）|
| BC-03 | 法律检索 | 妨害信用卡管理司法解释 | "信用卡恶意透支'数额较大'怎么认定？" | 刑法解释实体召回 |
| BC-04 | 案例检索 | case_698be5cb | "违法解除劳动合同该怎么赔偿？" | 案例源召回；"劳动合同"孤儿命中 |
| BC-05 | 案例检索(库外) | 城市房屋租赁管理办法 | "检索一下房屋租赁违约金相关的司法案例" | ❌ industry 未挂载→完全不可达（库外负样本） |
| BC-06 | 合同审核 | 城市房屋租赁管理办法 | "帮我审核这份房屋租赁合同，重点看租期和违约金" | ❌ industry 未挂载→检索空→**合同路径挂死 600s**（P0 最坏形态）|
| BC-07 | 合规审查 | 个人信息保护法 | "用人脸识别做身份验证需要单独同意吗？" | 合规口径；"人脸识别"库内本就 0 实体（真缺失） |
| BC-08 | 合同审核（守卫） | 通用 | "帮我审一下这份合作合同"（不含行业词） | 守卫拦截；"合同"孤儿命中 11 |
| BC-09 | 文书生成 | case_698be5cb | "根据违法解除案例，帮我写一份劳动仲裁申请书" | 文书生成链路 + 引用回填 |
| BC-10 | 法律问答 | 住建部标准 + 城市房屋租赁 | "建设工程质量保修期和租赁期限冲突怎么办？" | ✅ 借光挂载；验证挂载副作用 |

> 完整 `input`、golden `quality_checks`、`manual_takeover` 说明见 `bc_cases.py`。

---

## 9. 状态流转 HTML 看板

`bc_report.py` 生成的 HTML 包含：
- **每个用例的状态流转图**（节点序列 + 耗时 + 状态增量 + 成功/失败标记），可直观看到"断在哪个节点"；
- **全量指标卡**：精确率 / 召回率 / MRR / 任务成功率 / 人工修改率 / 响应时间（P50/P95/Max）/ 调用成本 / Token；
- **挂载感知可达性表**：有效命中 vs 孤儿命中，定位"库里有查不到"；
- **探针结论卡片**：可直接转优化项（P0/P1/P2 的代码级证据）。

**两份对照看板**（均在本目录）：
- `badcase_flow.html` — **Neo4j 恢复后重测**（当前，P/R 已复活，3 处结构缺陷可见）；
- `badcase_flow_neo4j_down.html` — 宕机基线（10/10 空召回、业务成功率 30% 的反例，用于对照优化前后）。

打开方式：浏览器直接打开上述文件，或在 IDE 内置预览面板查看。
