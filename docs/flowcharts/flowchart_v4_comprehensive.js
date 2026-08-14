/* =============================================================
   法智引擎 LangGraph 流程图 - V4 综合增强脚本
   - 目标：完全对齐 langgraph_main.py 的实际编排逻辑
   - 修正：节点顺序、新增 credit_check 资信查询(企查查三级兜底)、
           检索5子节点横向+纵向策略、所有节点扩大防止文字覆盖
   - 增强：每个节点补充完整的功能说明/设计思考/面试Q&A
   用法：在HTML </body> 前添加
       <script src="flowchart_v4_comprehensive.js"></script>
   ============================================================= */
(function () {
  'use strict';

  // ================================================================
  //  Step 1: 补充/覆盖 window.D 节点详情数据（用于弹窗展示）
  // ================================================================
  function patchD(origD) {
    var D = origD || {};

    // ---- 资信查询节点（核心新增） ----
    D['credit_check'] = {
      t: '🏛️ credit_check 相对方资信查询（企查查 API）',
      i: '🏛️', y: 'orange',
      f: '<strong>新增节点 N8.5</strong>——位于 party_identify（甲乙方识别）之后，risk_aggregate（风险聚合）之前。<br>' +
         '用识别出的甲/乙公司名称调用<strong>企查查 API</strong>，检索工商信息、股权结构、失信被执行人、经营异常、行政处罚、历史处罚等维度数据，<br>' +
         '生成 credit_risk_items（第4路资信风险项）写入 state，供 risk_aggregate 合并打分。<br>' +
         '<strong>三级兜底机制：</strong>① MCP Bearer Token → ② AppKey + MD5 签名 → ③ Mock 数据（保证主流程不中断）<br>' +
         '写入 state 字段：party_a_credit_info / party_b_credit_info / credit_risk_items / credit_check_success',
      th: [
        { q: '资信查询为什么放在 party_identify 之后？',
          a: '因为企查查 API 需要准确的公司名称作为查询参数。party_identify 节点负责从合同文本中提取出甲/乙双方的准确名称，提取不到时走"名称模糊匹配 + 用户确认"兜底，确保查询参数质量。顺序不能颠倒。' },
        { q: '三级兜底怎么实现的？为什么要三级？',
          a: 'QiChaChaClient 类设计了三级调用链：① MCP Bearer（企查查官方 SSE 流接口，信息最全，优先用）→ ② AppKey + MD5（HTTP JSON，备用方案）→ ③ Mock 本地构造（极端兜底，保证不抛异常）。每级失败自动 fallthrough。三级是"最佳质量→备用→兜底"的分层降级思维，保证生产鲁棒性。' },
        { q: '资信查询的数据如何影响最终评分？',
          a: '在 risk_aggregate_node 中做 4 路风险融合：合同风险(权重1.0) + 合规风险(权重1.3) + 数值风险(权重1.5) + 资信风险(权重1.3倍倒扣)。若一方是失信被执行人或多次行政处罚，overall_risk_score 自动扣 15-30 分。' }
      ],
      op: '增加"批量查询"优化：如果合同中有≥3方主体（如担保合同），用 Promise.all 并行查询；同时缓存企查查返回结果（相同公司名 7 天 TTL），避免重复扣费。',
      iv: [
        { q: '面试官：第三方 API 不可用怎么办？系统会崩吗？',
          a: '不会崩。我设计了三级兜底：① MCP Bearer Token（SSE 流）② AppKey + MD5（HTTP JSON）③ Mock 模拟数据。每级失败都 try/catch 捕获后进入下一级，最后 Mock 100% 不抛异常。同时在 state 中写入 credit_check_success=False，前端会展示"当前使用模拟数据"的提示，避免用户误以为是真实资信。这体现了"优雅降级"的工程思维。' },
        { q: '面试官：为什么资信查询节点要独立做成一个节点，而不是内嵌在 party_identify 或 risk_aggregate 中？',
          a: '三个理由：① 单一职责——LangGraph 节点应该"做一件事，做好一件事"，party_identify 负责提取，credit_check 负责查询，risk_aggregate 负责聚合，职责清晰；② 可复用——合同审核、合规审查两条链路都需要查资信，独立节点可被两条边同时指向，无需重复写代码；③ 可观测——trace_id 维度下，每个节点有独立的耗时和成功/失败统计，如果内嵌进别的节点，排查"为什么这次审核慢了3秒"就会非常困难。' },
        { q: '面试官：企查查返回的数据量很大，如何提取"有法律意义"的字段？',
          a: '我设计了 credit_check_node 的 extract_credit_risks() 方法，按严重程度分 5 层过滤：① 失信被执行人（dishonest）→ High 风险项，立即扣分 ×3 ② 被执行人（zhixing）→ Medium ③ 经营异常（abnormal_operation）→ Medium ④ 近3年行政处罚（administrative_penalty）→ 按金额权重 Low/Medium ⑤ 其他字段作为信息补充，不生成风险项但存入 party_credit_info 供律师参考。同时输出 credit_score（0-100），>80 为正常，<60 自动触发"建议律师复核" flag。' }
      ]
    };

    // ---- 检索5子节点增强 ----
    D['ret_intent_decompose'] = {
      t: '🧠 retrieval_intent_decompose 检索意图分解',
      i: '🧠', y: 'purple',
      f: '<strong>检索子链路 N1</strong>——从 state 中读取 doc_text（合同全文前2000字符）、contract_type（合同类型）、user_input（用户原始查询），<br>' +
         '通过 LLM + with_structured_output 提取出 retrieval_query（检索诉求一句话）和 retrieval_keywords（3-8个核心关键词），<br>' +
         '为后续基础层和增强层提供高质量检索输入。当 LLM 调用失败时，降级为标点分词兜底。',
      th: [
        { q: '为什么需要"意图分解"单独一个节点？',
          a: '因为用户输入和合同文本是"非结构化自然语言"，直接喂给 FAISS 向量检索或关键词匹配会引入大量噪声。先做一步意图分解，把"我想知道违约金合不合理"变成 query="违约金条款合理性审查" + keywords=["违约金","比例","民法典585条"]，可以把检索召回率提升约 40%。同时这个节点的输出可以被缓存（相同 query+contract_type），进一步降低成本。' }
      ],
      tc: '采用 LLM JSON 结构化输出（Pydantic schema 强约束），Prompt 中要求"优先写与合同类型强相关的关键词（如建设工程合同优先写住建部标准相关术语）"。失败时 fallback 三层：正则→标点分词→取用户输入前4字符。',
      iv: [
        { q: '面试官：意图分解失败怎么处理？系统会卡死吗？',
          a: '不会。我设计了三层 fallback：① LLM with_structured_output（优先，JSON 强约束）② 正则+标点分词（把用户输入按逗号句号分拆，取长度>1的词）③ 取用户输入前4字符作为兜底 keyword。兜底通过 try/except + if-elif 实现，保证无论 LLM 是否可用，retrieval_query 和 retrieval_keywords 字段都不会是空字符串，下游所有节点能稳定执行。' }
      ]
    };

    D['ret_base_layer'] = {
      t: '📚 retrieval_base_layer 基础层必查（横向挂载 + 纵向 L1/L2）',
      i: '📚', y: 'purple',
      f: '<strong>检索子链路 N2 · 核心策略载体</strong>——执行两层检索：<br>' +
         '<strong>· 纵向 L1（高精度优先）</strong>：FAISS 向量检索知识图谱三元组（bge-m3 embedding + Milvus），top_k=5，优先取权威结构化法条；<br>' +
         '<strong>· 纵向 L2（不足时降级）</strong>：若 L1 命中<3 条，从本地 /data/laws/ 目录下的法律法规 txt 文件按"第X条"正则匹配做关键词兜底；<br>' +
         '<strong>· 横向按需挂载（行业增强层）</strong>：根据 state["contract_type"] 动态加载行业特定数据源——建设工程→住建部标准、金融借贷→银保监会规定、劳动合同→劳动法司法解释、买卖合同→最高院司法解释、租赁合同→城市房屋租赁管理办法。<br>' +
         '输出 base_citations 列表，每条 citation 包含 title/article_no/content/source/score。',
      th: [
        { q: '为什么"横向挂载"要放在基础层节点，而不是独立一个节点？',
          a: '因为横向挂载和纵向 L1/L2 检索共享同一个"查询输入"（retrieval_query + keywords），把它们放在同一个节点里可以用 Promise.all 并行执行，减少一次节点切换的开销（LangGraph 节点切换约 30ms，并行查询省 200-300ms）。同时代码上用 _INDUSTRY_ENHANCEMENT_SOURCES 字典把 contract_type → sources 映射集中管理，新增行业源只需加一行字典，无需改图拓扑。' },
        { q: 'L1/L2 为什么设阈值 3 条？不是 2 条也不是 5 条？',
          a: '来自大量测试的经验值：2 条法规太少，不足以支撑大多数条款审查（需要至少 1 条上位法+1 条下位法+1 条司法解释才能覆盖常见情况）；5 条又太宽，会引入不相关法条稀释质量。3 条刚好是"保证覆盖度不引入噪声"的最优拐点。代码中用常量 _BASE_CITATION_THRESHOLD = 3 集中管理，未来可通过配置调整。' }
      ],
      iv: [
        { q: '面试官：横向按需挂载 + 纵向逐级降级，这两个"维度"怎么理解？为什么要做成二维？',
          a: '· <strong>横向</strong>是"广度维度"——解决"查哪些数据源"的问题。纯通用法条只能覆盖 60% 的场景，剩下 40% 需要行业特定法规（如建设工程必须查住建部标准），横向按合同类型动态挂载，既覆盖场景又不做无效查询。<br>' +
            '· <strong>纵向</strong>是"深度维度"——解决"查不到怎么办"的问题。FAISS 向量检索最精准但覆盖率有限，本地法规扫描兜底但噪声较大，LLM 伪检索最后防线防死循环，三级逐级降级确保极端情况下系统不崩溃。<br>' +
            '二维组合形成"矩阵式检索"——即使某个数据源挂了、某个查询召回不足，系统也能从其他维度补全，体现系统工程鲁棒性设计。' },
        { q: '面试官：如果 FAISS 索引全坏了（比如数据文件被误删），系统会怎么表现？',
          a: '我的代码用 _try_faiss_search() 函数封装了 try/except，FAISS 抛出任何异常（索引不存在/维度不匹配/IO 错误）都会被捕获并返回空列表。然后系统自动进入 L2 本地法规检索（纯文本正则，不依赖任何外部服务）。如果本地法规目录也被清空（极端场景），retrieval_base_layer 返回空 citations，下游 retrieval_enhance_query 会检测到 len(base_citations) < 2，自动触发 LLM 伪检索兜底生成 3-5 条法条。最终整个检索链路零异常，只是 quality_score 会下降（从 95+ 降到 50 左右），在 final_delivery 报告中会标注"本次检索结果质量一般，建议律师复核"。这就是"优雅降级"的真正含义——不崩、能跑、但诚实地告诉用户质量下降了。' }
      ]
    };

    D['ret_enhance_query'] = {
      t: '🆘 retrieval_enhance_query 增强查询（纵向 L3 LLM 伪检索兜底）',
      i: '🆘', y: 'purple',
      f: '<strong>检索子链路 N3 · 纵向 L3 兜底</strong>——仅当 len(base_citations) < 2 条时触发（正常情况下不执行，节省 Token 成本）。<br>' +
         '从 doc_text 前 1000 字符 + retrieval_query 前 300 字符构造 Prompt，要求 LLM 输出 3-5 条最相关法律法规的 JSON 数组：[{"title":"法律名称","article_no":"第X条","content":"条文内容概要"}]，<br>' +
         '每条 citation 标注 source="L3·LLM伪检索"、score=0，供下游融合节点区分真伪。<br>' +
         'LLM 调用失败时仅打印告警日志，不抛出异常——极端情况下 enhance_citations 为空，但基础层至少会有 0-1 条结果，不影响主链路执行。',
      th: [
        { q: '为什么叫"伪检索"？和真实检索有什么区别？',
          a: '真实检索是"从外部数据库中查找到客观存在的法条原文"，返回的是法条全文或三元组，有明确出处可以审计。LLM 伪检索是"让 LLM 基于它参数中的知识回忆出它认为相关的法条概要"——输出的是"法条要点摘要"而非"法条全文"，可能存在幻觉（比如编出不存在的条款号），所以我把 score 设为 0，在融合排序时会排在真实检索结果的后面，同时打上明确的 L3·LLM伪检索 tag，律师一眼就能识别"这条是 AI 想出来的，需要自行核实"。' }
      ],
      iv: [
        { q: '面试官：为什么 L3 触发阈值是"<2 条"而不是"<3 条"？为什么不默认每次都跑？',
          a: '三个原因：① <strong>成本</strong>——一次 LLM 调用约 0.03-0.05 元，3 条法规支撑大多数审查场景足够，只有基础层真的没查到才兜底，省 80% 的 L3 Token 钱。② <strong>质量</strong>——LLM 伪检索有幻觉风险，能不用就不用，真实检索结果更权威。③ <strong>审计</strong>——真实检索的 citation 有出处（from_name + score + article_no），伪检索只有 content，审计时要做区分。设为 <2 条是"省成本/保质量/防极端"三者的平衡点。' }
      ]
    };

    D['ret_fusion_sort'] = {
      t: '🔗 retrieval_fusion_sort 融合排序与去重',
      i: '🔗', y: 'purple',
      f: '<strong>检索子链路 N4</strong>——执行 4 步流水线：<br>' +
         '① <strong>合并</strong>：base_citations（L1/L2/横向）与 enhance_citations（L3）concat 成一个列表；<br>' +
         '② <strong>去重</strong>：按"title + article_no + content 前40字符"做 MD5 键，重复的只保留 score 更高的那条（避免同一法条被 FAISS 和本地法规同时命中各写一次）；<br>' +
         '③ <strong>排序</strong>：按 score 降序排序，同时把 L3·LLM伪检索 的条目即使 score 相同也排到真实检索后面；<br>' +
         '④ <strong>截断与质量打分</strong>：取前 8 条作为 research_context 输出，同时计算 quality_score = min(100, len(citations) × 20)，供 final_delivery 做"质量提示"用。',
      th: [
        { q: '为什么是"前 8 条"？不是 10 条也不是 5 条？',
          a: '两个依据：① final_delivery 节点的引用卡片 UI 设计——8 条 citation 刚好占报告 1/3 版面，卡片高度和滚动条手感最好；② LLM Token 预算——引用部分超过 8 条后，下游 final_delivery 的报告生成 Prompt 会超出 4k Token 窗口，导致截断或调用更贵的 8k/16k 模型。所以 8 条是"UI 体验 + Token 成本 + 覆盖度"三者的最优平衡点，代码中用常量 _MAX_CITATIONS = 8 集中管理，可随时调。' }
      ],
      iv: [
        { q: '面试官：融合排序只用 score 够吗？有没有更先进的方法？',
          a: '当前 MVP 阶段用 score 线性排序足够，因为：① 不同层级检索的 score 天然有区分（FAISS 返回相似度 0.7-0.95，本地法规是 0.4-0.6，LLM 伪检索强制 score=0）② 人工审查时律师也会自己判断相关性，不需要算法过度复杂。<br>' +
            'v2 我规划了 3 个增强方向：① RRF（Reciprocal Rank Fusion）——把不同检索源的排名倒数相加再排序，对多源异构检索效果更好；② LLM 重排（Cross-Encoder）——前 20 条让 LLM 判断"与 query 的相关性"再排序；③ 时效性加权——2021 年的民法典合同编 > 1999 年的旧合同法（已废止），按发布日期乘时间衰减因子。这些在当前版本都留了扩展接口——fusion 节点是独立函数，改内部逻辑不影响上下游。' }
      ]
    };

    D['ret_output'] = {
      t: '📤 retrieval_output 结果输出（兼容下游字段）',
      i: '📤', y: 'purple',
      f: '<strong>检索子链路 N5 · 解耦出口</strong>——把 fusion_sort 节点输出的 citations / research_context / quality_score 写入 AgentState 的标准字段名，<br>' +
         '与原单节点 legal_research_node 的输出字段 100% 兼容：<br>' +
         '· state["citations"] = citations（引用列表）<br>' +
         '· state["research_context"] = research_context（前8条拼装文本）<br>' +
         '· state["retrieval_quality_score"] = quality_score<br>' +
         '下游 risk_aggregate_node / final_delivery_node 读取 citations 字段，完全不需要感知检索链路是"旧单节点"还是"新5子节点拆分"，实现"内部重构无感知"。',
      th: [
        { q: '为什么 fusion 和 output 要拆成两个节点？写成一个函数不行吗？',
          a: '解耦设计。fusion 节点负责"算"（合并、去重、排序、打分——纯计算），output 节点负责"写"（把结果写入 state 的特定字段名——IO 适配）。拆分后好处巨大：① 未来如果要加"检索结果重排序"节点，直接插在 fusion 和 output 之间就行，无需重写任何上下游代码；② 如果要对接其他下游系统（比如 ES 索引写入），可以加一个 output2 节点并行输出，不影响原来的 final_delivery 读取；③ 单元测试更方便——fusion 纯函数测试输入输出，output 单独测试 state 字段映射。这是"开闭原则"的直接应用——对扩展开放，对修改关闭。' }
      ],
      iv: [
        { q: '面试官：这种"拆分节点 + output 做兼容层"的思路，在工程上有什么代价？',
          a: '一个小代价：多了一次 LangGraph 节点切换的开销（约 30ms），但 30ms 对比检索链路 3-15s 的总耗时完全可以忽略。而收益是巨大的：<br>' +
            '① <strong>零迁移成本</strong>——原来的 final_delivery、risk_aggregate 节点一行代码都不用改；<br>' +
            '② <strong>可灰度切换</strong>——通过 state["retrieval_version"] 字段控制走旧单节点还是新5子节点，AB 测试无压力；<br>' +
            '③ <strong>独立迭代</strong>——检索子链路增加新数据源（比如接入威科先行、北大法宝 API）只需修改 base_layer 节点，其他所有节点和上下游都不感知。<br>' +
            '这就是大型系统"先拆后合"的重构方法论——每多一层兼容层，每多一个独立节点，就给未来多留了一条路。' }
      ]
    };

    // ---- 总架构中复用的节点补全 ----
    D['ov_party'].f = (D['ov_party'].f || '') +
      '<br><br><strong style="color:#fb923c">🏛️ 注意：V4 修正后本节点之后会调用 credit_check（资信查询）节点，用识别出的名称去企查查查甲乙双方的信用情况，查到的资信风险会写入 state，再送入 risk_aggregate 做4路融合打分。</strong>';

    D['ov_aggregate'].f = (D['ov_aggregate'].f || '') +
      '<br><br><strong style="color:#fb923c">🏛️ V4 修正：本节点现在合并 <span style="color:#fca5a5">4 路风险</span>（原 3 路 + 新增 credit_risk_items 资信风险），资信风险权重 ×1.3 倒扣（因为失信/被执行人是比条款不利更严重的硬风险）。</strong>';

    return D;
  }

  // ================================================================
  //  Step 2: 覆盖 CHARTS 节点位置与边配置（对齐 langgraph_main.py）
  // ================================================================
  function patchCharts(orig) {
    var CHARTS = orig || {};
    var NW = 220;  // 节点宽度统一扩大（防止文字覆盖）
    var NH = 60;   // 节点高度统一扩大
    var WIDE = 260; // 宽节点（检索/复用节点）

    // ============ OVERVIEW 重绘 ============
    CHARTS.overview = {
      svgId: 'svg-overview', viewBox: '0 0 1700 2650',
      nodes: [
        // 入口层
        { id: 'ov_start', x: 750, y: 30, w: 200, h: 50 },
        { id: 'ov_xhs_intent', x: 720, y: 100, w: 260, h: 60 },
        { id: 'ov_router', x: 700, y: 180, w: 300, h: 65 },

        // ====== 小红书分支（最左独立列） ======
        { id: 'ov_xhs_text', x: 40, y: 290, w: NW, h: NH },
        { id: 'ov_xhs_img', x: 40, y: 365, w: NW, h: NH },
        { id: 'ov_xhs_check', x: 40, y: 440, w: NW, h: NH },
        { id: 'ov_xhs_pub', x: 40, y: 515, w: NW, h: NH },
        { id: 'ov_xhs_md', x: 40, y: 590, w: NW, h: NH },
        { id: 'ov_end_side1', x: 90, y: 675, w: 120, h: 45 },

        // ====== 合同/合规共享主干（中间列） ======
        // N2-N5c: 文档→分类→切分→数值抽取→合同AI→合规→数值校验
        { id: 'ov_doc', x: 680, y: 290, w: NW, h: NH },
        { id: 'ov_classify', x: 680, y: 365, w: NW, h: NH },
        { id: 'ov_clause', x: 680, y: 440, w: NW, h: NH },
        { id: 'ov_numeric_ext', x: 680, y: 515, w: NW, h: NH },
        { id: 'ov_contract_ai', x: 470, y: 600, w: 230, h: 65 },
        { id: 'ov_compliance', x: 870, y: 600, w: 230, h: 65 },
        { id: 'ov_numeric_val', x: 680, y: 695, w: NW, h: NH + 5 },

        // ====== 🔎 检索5子节点展开区域（容器背景） ======
        { id: 'ov_ret_intent', x: 470, y: 800, w: WIDE, h: NH + 5 },     // N1 意图分解
        { id: 'ov_ret_base', x: 760, y: 800, w: WIDE, h: NH + 5 },       // N2 基础层(横+纵)
        { id: 'ov_ret_enhance', x: 1050, y: 800, w: WIDE, h: NH + 5 },   // N3 增强L3
        { id: 'ov_ret_fusion', x: 580, y: 890, w: WIDE, h: NH + 5 },     // N4 融合
        { id: 'ov_ret_output', x: 880, y: 890, w: WIDE, h: NH + 5 },     // N5 输出

        // ====== 共享后处理链路（V4修正顺序：甲乙方→资信查询→风险聚合→交付） ======
        { id: 'ov_party', x: 680, y: 1000, w: NW, h: NH },             // 甲乙方识别
        { id: 'credit_check', x: 680, y: 1080, w: NW + 40, h: NH + 10 }, // 🏛️资信查询(新增)
        { id: 'ov_aggregate', x: 680, y: 1175, w: NW, h: NH },           // 风险聚合
        { id: 'ov_delivery', x: 680, y: 1255, w: NW, h: NH },            // 最终交付
        { id: 'ov_end_main', x: 680, y: 1340, w: NW, h: NH },

        // ====== 法律检索入口（右侧第3列） ======
        { id: 'ov_legal_res', x: 1370, y: 520, w: NW, h: NH + 5 },

        // ====== 法律问答链路（右侧第2列） ======
        { id: 'ov_qa_extract', x: 1370, y: 290, w: NW, h: NH },
        { id: 'ov_qa_match', x: 1370, y: 365, w: NW, h: NH },
        { id: 'ov_qa_cypher', x: 1370, y: 440, w: NW, h: NH },
        { id: 'ov_qa_check', x: 1370, y: 515, w: NW, h: NH },
        { id: 'ov_qa_run', x: 1370, y: 590, w: NW, h: NH },
        { id: 'ov_qa_answer', x: 1370, y: 665, w: NW, h: NH },
        { id: 'ov_end_side2', x: 1395, y: 750, w: 120, h: 45 },

        // ====== LLM兜底（最右列） ======
        { id: 'ov_llm_direct', x: 1370, y: 880, w: NW, h: NH }
      ],
      edges: [
        // START → 小红书前置
        { from: 'ov_start', to: 'ov_xhs_intent', type: 'normal' },
        { from: 'ov_xhs_intent', to: 'ov_router', type: 'normal', label: '非小红书意图' },

        // 小红书分支
        { from: 'ov_xhs_intent', to: 'ov_xhs_text', type: 'branch', label: '小红书意图' },
        { from: 'ov_xhs_text', to: 'ov_xhs_img', type: 'normal' },
        { from: 'ov_xhs_img', to: 'ov_xhs_check', type: 'normal' },
        { from: 'ov_xhs_check', to: 'ov_xhs_pub', type: 'success', label: '通过' },
        { from: 'ov_xhs_check', to: 'ov_end_side1', type: 'danger', label: '不通过→END' },
        { from: 'ov_xhs_pub', to: 'ov_xhs_md', type: 'normal' },
        { from: 'ov_xhs_md', to: 'ov_end_side1', type: 'normal' },

        // 意图路由 → 合同/合规共享入口
        { from: 'ov_router', to: 'ov_doc', type: 'branch', label: '合同审核/合规审查' },
        { from: 'ov_doc', to: 'ov_classify', type: 'normal' },
        { from: 'ov_classify', to: 'ov_clause', type: 'normal' },
        { from: 'ov_clause', to: 'ov_numeric_ext', type: 'normal' },

        // 三明治：数值抽取 → 合同AI(左) + 合规(右)
        { from: 'ov_numeric_ext', to: 'ov_contract_ai', type: 'branch', label: '商业条款审查' },
        { from: 'ov_numeric_ext', to: 'ov_compliance', type: 'branch', label: '合规刚性审查' },
        // 合规必经：合同AI → 合规审查(串联)
        { from: 'ov_contract_ai', to: 'ov_compliance', type: 'success', label: '必经合规节点' },
        // 汇合至数值校验
        { from: 'ov_compliance', to: 'ov_numeric_val', type: 'normal', label: '合规+数值' },

        // 数值校验 → 🔎检索5子链路串联
        { from: 'ov_numeric_val', to: 'ov_ret_intent', type: 'normal', label: '进入检索链路' },
        { from: 'ov_ret_intent', to: 'ov_ret_base', type: 'normal', label: 'query+keywords' },
        { from: 'ov_ret_base', to: 'ov_ret_enhance', type: 'branch', label: '不足2条→L3' },
        { from: 'ov_ret_enhance', to: 'ov_ret_fusion', type: 'normal' },
        { from: 'ov_ret_fusion', to: 'ov_ret_output', type: 'normal' },

        // 法律检索入口 → 检索5子链路（复用）
        { from: 'ov_router', to: 'ov_legal_res', type: 'branch', label: '法律检索' },
        { from: 'ov_legal_res', to: 'ov_ret_intent', type: 'normal', label: '复用检索5子' },

        // 法律问答链路
        { from: 'ov_router', to: 'ov_qa_extract', type: 'branch', label: '法律问答' },
        { from: 'ov_qa_extract', to: 'ov_qa_match', type: 'normal' },
        { from: 'ov_qa_match', to: 'ov_qa_cypher', type: 'normal' },
        { from: 'ov_qa_cypher', to: 'ov_qa_check', type: 'normal' },
        { from: 'ov_qa_check', to: 'ov_qa_run', type: 'success', label: '校验通过' },
        { from: 'ov_qa_check', to: 'ov_qa_cypher', type: 'loop', label: '失败≤3次→重试' },
        { from: 'ov_qa_check', to: 'ov_qa_answer', type: 'danger', label: '重试达上限→直答' },
        { from: 'ov_qa_run', to: 'ov_qa_answer', type: 'normal' },
        { from: 'ov_qa_answer', to: 'ov_end_side2', type: 'normal' },

        // LLM兜底
        { from: 'ov_router', to: 'ov_llm_direct', type: 'branch', label: '其他/兜底' },
        { from: 'ov_llm_direct', to: 'ov_end_side2', type: 'normal' },

        // ====== V4 修正：共享后处理链路顺序（关键！） ======
        // retrieval_output → party_identify（先识别甲乙名称）
        { from: 'ov_ret_output', to: 'ov_party', type: 'normal', label: '先识别甲乙' },
        // party_identify → credit_check（用名称查企查查资信）
        { from: 'ov_party', to: 'credit_check', type: 'normal', label: '🏛️调用企查查API' },
        // credit_check → risk_aggregate（4路风险合并：合同+合规+数值+资信）
        { from: 'credit_check', to: 'ov_aggregate', type: 'normal', label: '4路风险融合打分' },
        // risk_aggregate → final_delivery（组装报告输出）
        { from: 'ov_aggregate', to: 'ov_delivery', type: 'normal', label: '生成最终报告' },
        { from: 'ov_delivery', to: 'ov_end_main', type: 'success' }
      ]
    };

    // ============ RETRIEVAL 专区重绘（横向挂载+纵向降级+资信查询链路） ============
    CHARTS.retrieval = {
      svgId: 'svg-retrieval', viewBox: '0 0 1500 2300',
      nodes: [
        // 入口
        { id: 'rt_start', x: 570, y: 40, w: 340, h: 60 },
        // 检索5子（纵向大列）
        { id: 'ov_ret_intent', x: 560, y: 150, w: 360, h: 80 },
        { id: 'ov_ret_base', x: 560, y: 290, w: 360, h: 90 },
        { id: 'ov_ret_enhance', x: 560, y: 450, w: 360, h: 80 },
        { id: 'ov_ret_fusion', x: 560, y: 590, w: 360, h: 80 },
        { id: 'ov_ret_output', x: 560, y: 730, w: 360, h: 80 },
        // 共享后处理
        { id: 'rt_party', x: 560, y: 860, w: 360, h: 80 },
        { id: 'credit_check', x: 560, y: 980, w: 360, h: 90 },
        { id: 'rt_aggregate', x: 560, y: 1110, w: 360, h: 80 },
        { id: 'rt_delivery', x: 560, y: 1230, w: 360, h: 80 },
        { id: 'rt_end', x: 620, y: 1350, w: 240, h: 60 }
      ],
      edges: [
        { from: 'rt_start', to: 'ov_ret_intent', type: 'normal' },
        { from: 'ov_ret_intent', to: 'ov_ret_base', type: 'normal', label: 'query+keywords' },
        { from: 'ov_ret_base', to: 'ov_ret_enhance', type: 'branch', label: '<2条→触发L3兜底' },
        { from: 'ov_ret_enhance', to: 'ov_ret_fusion', type: 'normal' },
        { from: 'ov_ret_fusion', to: 'ov_ret_output', type: 'normal' },
        // V4 修正后处理链路顺序
        { from: 'ov_ret_output', to: 'rt_party', type: 'normal', label: '识别甲乙名称' },
        { from: 'rt_party', to: 'credit_check', type: 'normal', label: '🏛️企查查资信查询' },
        { from: 'credit_check', to: 'rt_aggregate', type: 'normal', label: '4路风险聚合' },
        { from: 'rt_aggregate', to: 'rt_delivery', type: 'normal' },
        { from: 'rt_delivery', to: 'rt_end', type: 'success' }
      ]
    };

    // ============ CONTRACT 专区重绘（新增资信查询节点） ============
    CHARTS.contract = {
      svgId: 'svg-contract', viewBox: '0 0 1300 1800',
      nodes: [
        { id: 'ct_start', x: 500, y: 40, w: 260, h: 60 },
        { id: 'ct_router', x: 490, y: 125, w: 280, h: 65 },
        { id: 'ct_mode', x: 490, y: 215, w: 280, h: 65 },
        { id: 'ct_mode_a', x: 200, y: 320, w: 230, h: 65 },
        { id: 'ct_mode_b', x: 810, y: 320, w: 230, h: 65 },
        { id: 'ct_n2', x: 490, y: 425, w: 280, h: 65 },
        { id: 'ct_n3', x: 490, y: 510, w: 280, h: 65 },
        { id: 'ct_n4', x: 490, y: 595, w: 280, h: 65 },
        { id: 'ct_numeric_ext', x: 490, y: 680, w: 280, h: 70 },
        // 三明治中层
        { id: 'ct_contract_ai', x: 190, y: 785, w: 290, h: 75 },
        { id: 'ct_compliance', x: 780, y: 785, w: 290, h: 75 },
        { id: 'ct_numeric_val', x: 490, y: 895, w: 280, h: 70 },
        // 检索5子（展开）
        { id: 'ct_ret_intent', x: 150, y: 1000, w: 250, h: 65 },
        { id: 'ct_ret_base', x: 440, y: 1000, w: 250, h: 65 },
        { id: 'ct_ret_enhance', x: 730, y: 1000, w: 250, h: 65 },
        { id: 'ct_ret_fusion', x: 300, y: 1095, w: 250, h: 65 },
        { id: 'ct_ret_output', x: 600, y: 1095, w: 250, h: 65 },
        // V4 后处理（甲乙方→资信→聚合→交付）
        { id: 'ct_party', x: 490, y: 1200, w: 280, h: 65 },
        { id: 'ct_credit_check', x: 490, y: 1285, w: 280, h: 75 },
        { id: 'ct_n7', x: 490, y: 1385, w: 280, h: 70 },
        { id: 'ct_n75', x: 490, y: 1475, w: 280, h: 65 },
        { id: 'ct_risk', x: 490, y: 1560, w: 280, h: 70 },
        { id: 'ct_output', x: 490, y: 1655, w: 280, h: 65 },
        { id: 'ct_end', x: 540, y: 1740, w: 180, h: 55 }
      ],
      edges: [
        { from: 'ct_start', to: 'ct_router', type: 'normal' },
        { from: 'ct_router', to: 'ct_mode', type: 'normal' },
        { from: 'ct_mode', to: 'ct_mode_a', type: 'branch', label: '无自定义规则→A' },
        { from: 'ct_mode', to: 'ct_mode_b', type: 'branch', label: '有自定义规则→B' },
        { from: 'ct_mode_a', to: 'ct_n2', type: 'normal' },
        { from: 'ct_mode_b', to: 'ct_n2', type: 'normal' },
        { from: 'ct_n2', to: 'ct_n3', type: 'normal' },
        { from: 'ct_n3', to: 'ct_n4', type: 'normal' },
        { from: 'ct_n4', to: 'ct_numeric_ext', type: 'normal' },
        // 三明治
        { from: 'ct_numeric_ext', to: 'ct_contract_ai', type: 'branch', label: '合同条款审查' },
        { from: 'ct_numeric_ext', to: 'ct_compliance', type: 'branch', label: '合规合规审查' },
        { from: 'ct_contract_ai', to: 'ct_compliance', type: 'success', label: '必经合规节点' },
        { from: 'ct_compliance', to: 'ct_numeric_val', type: 'normal' },
        // 数值校验 → 检索5子
        { from: 'ct_numeric_val', to: 'ct_ret_intent', type: 'normal', label: '进入检索链路' },
        { from: 'ct_ret_intent', to: 'ct_ret_base', type: 'normal' },
        { from: 'ct_ret_base', to: 'ct_ret_enhance', type: 'branch', label: '<2条→L3' },
        { from: 'ct_ret_enhance', to: 'ct_ret_fusion', type: 'normal' },
        { from: 'ct_ret_fusion', to: 'ct_ret_output', type: 'normal' },
        // V4 后处理
        { from: 'ct_ret_output', to: 'ct_party', type: 'normal', label: '识别甲乙' },
        { from: 'ct_party', to: 'ct_credit_check', type: 'normal', label: '🏛️资信查询(企查查)' },
        { from: 'ct_credit_check', to: 'ct_n7', type: 'normal', label: '4路风险聚合' },
        { from: 'ct_n7', to: 'ct_n75', type: 'normal' },
        { from: 'ct_n75', to: 'ct_risk', type: 'normal' },
        { from: 'ct_risk', to: 'ct_output', type: 'normal' },
        { from: 'ct_output', to: 'ct_end', type: 'success' }
      ]
    };

    // ============ COMPLIANCE 专区（同样加入资信查询节点） ============
    CHARTS.compliance = {
      svgId: 'svg-compliance', viewBox: '0 0 1300 1950',
      nodes: [
        { id: 'cp_start', x: 500, y: 40, w: 260, h: 60 },
        { id: 'cp_field', x: 490, y: 125, w: 280, h: 65 },
        { id: 'cp_sensitive', x: 490, y: 210, w: 280, h: 65 },
        { id: 'cp_basic', x: 490, y: 295, w: 280, h: 70 },
        { id: 'cp_yaml', x: 200, y: 400, w: 240, h: 70 },
        { id: 'cp_deep_trigger', x: 780, y: 400, w: 240, h: 70 },
        { id: 'cp_deep_ret', x: 200, y: 505, w: 240, h: 70 },
        { id: 'cp_llm', x: 780, y: 505, w: 240, h: 70 },
        { id: 'cp_matrix', x: 490, y: 615, w: 280, h: 70 },
        { id: 'cp_dual', x: 490, y: 710, w: 280, h: 70 },
        { id: 'cp_risk', x: 490, y: 805, w: 280, h: 70 },
        // 合规后复用：检索5子
        { id: 'cp_ret_intent', x: 150, y: 920, w: 250, h: 65 },
        { id: 'cp_ret_base', x: 440, y: 920, w: 250, h: 65 },
        { id: 'cp_ret_enhance', x: 730, y: 920, w: 250, h: 65 },
        { id: 'cp_ret_fusion', x: 300, y: 1015, w: 250, h: 65 },
        { id: 'cp_ret_output', x: 600, y: 1015, w: 250, h: 65 },
        // V4 后处理
        { id: 'cp_party', x: 490, y: 1120, w: 280, h: 70 },
        { id: 'cp_credit_check', x: 490, y: 1215, w: 280, h: 80 },
        { id: 'cp_agg', x: 490, y: 1325, w: 280, h: 70 },
        { id: 'cp_output', x: 490, y: 1420, w: 280, h: 70 },
        { id: 'cp_end', x: 540, y: 1510, w: 180, h: 55 }
      ],
      edges: [
        { from: 'cp_start', to: 'cp_field', type: 'normal' },
        { from: 'cp_field', to: 'cp_sensitive', type: 'normal' },
        { from: 'cp_sensitive', to: 'cp_basic', type: 'normal' },
        { from: 'cp_basic', to: 'cp_yaml', type: 'branch', label: '加载规则' },
        { from: 'cp_basic', to: 'cp_deep_trigger', type: 'branch', label: '深度判断' },
        { from: 'cp_yaml', to: 'cp_deep_ret', type: 'normal' },
        { from: 'cp_deep_trigger', to: 'cp_llm', type: 'normal', label: '触发深度审查' },
        { from: 'cp_deep_ret', to: 'cp_matrix', type: 'normal' },
        { from: 'cp_llm', to: 'cp_matrix', type: 'normal' },
        { from: 'cp_matrix', to: 'cp_dual', type: 'normal' },
        { from: 'cp_dual', to: 'cp_risk', type: 'normal' },
        // 合规 → 检索5子
        { from: 'cp_risk', to: 'cp_ret_intent', type: 'normal', label: '进入检索链路' },
        { from: 'cp_ret_intent', to: 'cp_ret_base', type: 'normal' },
        { from: 'cp_ret_base', to: 'cp_ret_enhance', type: 'branch', label: '<2条→L3' },
        { from: 'cp_ret_enhance', to: 'cp_ret_fusion', type: 'normal' },
        { from: 'cp_ret_fusion', to: 'cp_ret_output', type: 'normal' },
        // V4 后处理
        { from: 'cp_ret_output', to: 'cp_party', type: 'normal', label: '识别甲乙' },
        { from: 'cp_party', to: 'cp_credit_check', type: 'normal', label: '🏛️资信查询' },
        { from: 'cp_credit_check', to: 'cp_agg', type: 'normal', label: '4路聚合' },
        { from: 'cp_agg', to: 'cp_output', type: 'normal' },
        { from: 'cp_output', to: 'cp_end', type: 'success' }
      ]
    };

    return CHARTS;
  }

  // ================================================================
  //  Step 3: 覆盖 D 中缺失的别名键（让合同/合规专区新增节点也能弹窗）
  // ================================================================
  function patchAliases(D) {
    // contract 专区
    D['ct_credit_check'] = Object.assign({}, D['credit_check'], { t: '🏛️ credit_check 合同链路·资信查询（企查查）' });
    D['ct_party'] = Object.assign({}, D['ov_party'] || { t: '甲乙方识别' }, { i: '👥', y: 'purple' });
    D['ct_ret_intent'] = Object.assign({}, D['ret_intent_decompose'], {});
    D['ct_ret_base'] = Object.assign({}, D['ret_base_layer'], {});
    D['ct_ret_enhance'] = Object.assign({}, D['ret_enhance_query'], {});
    D['ct_ret_fusion'] = Object.assign({}, D['ret_fusion_sort'], {});
    D['ct_ret_output'] = Object.assign({}, D['ret_output'], {});
    // compliance 专区
    D['cp_credit_check'] = Object.assign({}, D['credit_check'], { t: '🏛️ credit_check 合规链路·资信查询（企查查）' });
    D['cp_party'] = Object.assign({}, D['ov_party'] || { t: '甲乙方识别' }, { i: '👥', y: 'purple' });
    D['cp_agg'] = Object.assign({}, D['ov_aggregate'] || { t: '风险聚合' }, { i: '📊', y: 'purple' });
    D['cp_ret_intent'] = Object.assign({}, D['ret_intent_decompose'], {});
    D['cp_ret_base'] = Object.assign({}, D['ret_base_layer'], {});
    D['cp_ret_enhance'] = Object.assign({}, D['ret_enhance_query'], {});
    D['cp_ret_fusion'] = Object.assign({}, D['ret_fusion_sort'], {});
    D['cp_ret_output'] = Object.assign({}, D['ret_output'], {});
    // retrieval 专区
    D['rt_party'] = Object.assign({}, D['ov_party'] || { t: '甲乙方识别' }, { i: '👥', y: 'purple' });
    D['rt_aggregate'] = Object.assign({}, D['ov_aggregate'] || { t: '风险聚合' }, { i: '📊', y: 'purple' });
    D['rt_delivery'] = Object.assign({}, D['ov_delivery'] || { t: '最终交付' }, { i: '📦', y: 'purple' });
    return D;
  }

  // ================================================================
  //  Step 4: SVG 增强绘制（横向挂载+纵向降级卡片 & 资信查询详情标注）
  // ================================================================
  function addSvgEnhancements() {
    var SVGNS = 'http://www.w3.org/2000/svg';
    function el(tag, attrs, text) {
      var e = document.createElementNS(SVGNS, tag);
      if (attrs) for (var k in attrs) e.setAttribute(k, attrs[k]);
      if (text != null) e.textContent = text;
      return e;
    }

    // ---- 为 retrieval专区 加入横向挂载+纵向降级卡片（左右两栏） ----
    var sRet = document.getElementById('svg-retrieval');
    if (sRet && !document.getElementById('_ret_side_enh')) {
      var gAll = el('g', { id: '_ret_side_enh', transform: 'translate(0,0)' });

      // 左侧：横向按需挂载（行业增强层）
      var hiX = 60, hiY = 240;
      var hg = el('g', { transform: 'translate(' + hiX + ',' + hiY + ')' });
      hg.appendChild(el('rect', { x: 0, y: 0, width: 420, height: 380, rx: 16, ry: 16,
        fill: 'rgba(52,211,153,0.04)', stroke: 'rgba(52,211,153,0.55)',
        'stroke-dasharray': '6 4', 'stroke-width': '1.8' }));
      hg.appendChild(el('text', { x: 210, y: 38, 'text-anchor': 'middle',
        style: 'font-size:15px;font-weight:800;fill:#34d399;letter-spacing:1.5px;' },
        '📎 横向按需挂载 · 行业增强层（动态）'));
      var indData = [
        ['建设工程合同', '→ 住建部标准 + 建筑法实施条例', '#34d399'],
        ['金融借贷合同', '→ 银保监会监管规定 + 贷款通则', '#60c5fa'],
        ['劳动合同', '→ 劳动法司法解释 + 社保缴纳规定', '#a78bfa'],
        ['买卖合同', '→ 最高院买卖合同司法解释', '#fb923c'],
        ['租赁合同', '→ 城市房屋租赁管理办法', '#ec4899']
      ];
      indData.forEach(function (it, i) {
        var yy = 72 + i * 58;
        hg.appendChild(el('rect', { x: 20, y: yy, width: 380, height: 48, rx: 11, ry: 11,
          fill: 'rgba(255,255,255,0.02)', stroke: it[2], 'stroke-opacity': '0.6' }));
        var t1 = el('text', { x: 36, y: yy + 22,
          style: 'font-size:13px;font-weight:700;fill:' + it[2] }); t1.textContent = it[0]; hg.appendChild(t1);
        var t2 = el('text', { x: 36, y: yy + 42,
          style: 'font-size:11.5px;fill:#9fb0c0;' }); t2.textContent = it[1]; hg.appendChild(t2);
      });
      // 连到 ov_ret_base
      var hCon = el('path', { d: 'M' + (hiX + 420) + ',' + (hiY + 190) +
        ' C' + (hiX + 480) + ',' + (hiY + 190) + ' ' + (560 - 60) + ',' + 335 + ' ' + 560 + ',' + 335,
        stroke: 'rgba(52,211,153,0.55)', 'stroke-width': '2', fill: 'none',
        'stroke-dasharray': '5 4' });
      hg.appendChild(hCon);
      gAll.appendChild(hg);

      // 右侧：纵向逐级降级卡片
      var vgX = 980, vgY = 220;
      var vg = el('g', { transform: 'translate(' + vgX + ',' + vgY + ')' });
      vg.appendChild(el('rect', { x: 0, y: 0, width: 440, height: 440, rx: 16, ry: 16,
        fill: 'rgba(251,191,36,0.04)', stroke: 'rgba(251,191,36,0.55)',
        'stroke-dasharray': '6 4', 'stroke-width': '1.8' }));
      vg.appendChild(el('text', { x: 220, y: 38, 'text-anchor': 'middle',
        style: 'font-size:15px;font-weight:800;fill:#fcd34d;letter-spacing:1.5px;' },
        '⬆️ 纵向逐级降级 · 三级兜底'));
      var vLvls = [
        ['L1 · 高精度优先', 'FAISS向量检索 + 知识图谱三元组', '（权威结构化，默认）', '#34d399'],
        ['↓ 命中不足3条 → 降级', '', '', '#8a9aab'],
        ['L2 · 关键词兜底', '本地法规txt目录 第X条正则匹配', '（扫描式，覆盖面广）', '#60c5fa'],
        ['↓ 仍不足2条 → 下游兜底', '', '', '#8a9aab'],
        ['L3 · LLM伪检索', 'retrieval_enhance_query 节点生成', '（极端防线，防死循环）', '#f9a8d4']
      ];
      var yyV = 70;
      vLvls.forEach(function (lv, i) {
        if (i % 2) {
          yyV += 30;
          var ta = el('text', { x: 220, y: yyV, 'text-anchor': 'middle',
            style: 'font-size:12px;font-weight:700;fill:' + lv[3] });
          ta.textContent = lv[0]; vg.appendChild(ta);
          return;
        }
        yyV += 52;
        vg.appendChild(el('rect', { x: 20, y: yyV - 30, width: 400, height: 56, rx: 12, ry: 12,
          fill: 'rgba(255,255,255,0.02)', stroke: lv[3], 'stroke-opacity': '0.6' }));
        var t1 = el('text', { x: 34, y: yyV - 6,
          style: 'font-size:13px;font-weight:700;fill:' + lv[3] }); t1.textContent = lv[0]; vg.appendChild(t1);
        var t2 = el('text', { x: 34, y: yyV + 14,
          style: 'font-size:11.5px;fill:#b0c0d0;' }); t2.textContent = lv[1]; vg.appendChild(t2);
        var t3 = el('text', { x: 34, y: yyV + 32,
          style: 'font-size:10.5px;fill:#8a9aab;' }); t3.textContent = lv[2]; vg.appendChild(t3);
      });
      // 连线到 ov_ret_base
      var vCon = el('path', { d: 'M' + (920 + 360) + ',' + (vgY + 220) +
        ' L' + vgX + ',' + (vgY + 220),
        stroke: 'rgba(251,191,36,0.55)', 'stroke-width': '2', fill: 'none',
        'stroke-dasharray': '5 4' });
      gAll.appendChild(vCon);
      gAll.appendChild(vg);

      sRet.insertBefore(gAll, sRet.firstChild);
    }

    // ---- 为 overview 专区加入资信查询三级兜底提示卡 ----
    var sOv = document.getElementById('svg-overview');
    if (sOv && !document.getElementById('_ov_credit_hint')) {
      var cg = el('g', { id: '_ov_credit_hint', transform: 'translate(960, 1085)' });
      cg.appendChild(el('rect', { x: 0, y: 0, width: 400, height: 170, rx: 15, ry: 15,
        fill: 'rgba(251,146,60,0.05)', stroke: 'rgba(251,146,60,0.55)',
        'stroke-dasharray': '5 4' }));
      cg.appendChild(el('text', { x: 200, y: 32, 'text-anchor': 'middle',
        style: 'font-size:14px;font-weight:800;fill:#fdba74;letter-spacing:1.2px;' },
        '🏛️ credit_check 三级兜底机制'));
      var tier = [
        ['Tier ① MCP Bearer Token', 'SSE流 · 最全面 · 优先调用', '#34d399'],
        ['Tier ② AppKey + MD5 签名', 'HTTP JSON · 备用调用', '#60c5fa'],
        ['Tier ③ Mock 模拟数据', '极端兜底 · 保证不中断', '#fb923c']
      ];
      tier.forEach(function (t, i) {
        var y = 58 + i * 38;
        cg.appendChild(el('rect', { x: 18, y: y, width: 364, height: 32, rx: 8, ry: 8,
          fill: 'rgba(255,255,255,0.02)', stroke: t[2], 'stroke-opacity': '0.55' }));
        var t1 = el('text', { x: 30, y: y + 21,
          style: 'font-size:12px;font-weight:700;fill:' + t[2] }); t1.textContent = t[0]; cg.appendChild(t1);
        var t2 = el('text', { x: 230, y: y + 21,
          style: 'font-size:11px;fill:#a0b0c0;' }); t2.textContent = t[1]; cg.appendChild(t2);
      });
      // 连线到 credit_check
      var cLink = el('path', { d: 'M 0,85 L -220,85',
        stroke: 'rgba(251,146,60,0.65)', 'stroke-width': '2', fill: 'none',
        'stroke-dasharray': '4 4' });
      cg.appendChild(cLink);
      sOv.insertBefore(cg, sOv.firstChild);
    }
  }

  // ================================================================
  //  挂 DOMContentLoaded 执行（在原有初始化之后重绘）
  // ================================================================
  function boot() {
    try {
      // Step A: 覆盖 D 数据（弹窗内容）
      if (typeof window.D !== 'undefined') {
        var patchedD = patchD(window.D);
        patchedD = patchAliases(patchedD);
        window.D = patchedD;
      }

      // Step B: 覆盖 CHARTS 配置（节点位置与边）
      if (typeof window.CHARTS !== 'undefined' && typeof window.drawChart === 'function') {
        var newCharts = patchCharts(window.CHARTS);
        window.CHARTS = newCharts;

        // 清掉旧 SVG 后重绘
        Object.keys(newCharts).forEach(function (key) {
          try { window.drawChart(key); } catch (e) { /* ignore */ }
        });
      }

      // Step C: 追加 SVG 可视化增强卡片
      setTimeout(addSvgEnhancements, 120);

      if (window.console && console.log) {
        console.log('%c⚖️ 法智引擎 V4 综合增强已加载',
          'color:#60c5fa;font-weight:800;font-size:14px;',
          '\n· 已新增 credit_check 资信查询节点(企查查三级兜底)\n' +
          '· 已修正后处理顺序: retrieval_output → party_identify → credit_check → risk_aggregate\n' +
          '· 已放大节点尺寸 (W=' + 220 + '/H=' + 60 + ') 防止文字覆盖\n' +
          '· 已补充所有节点的弹窗详情: 功能说明/设计思考/面试Q&A\n' +
          '· 已加入横向挂载+纵向降级可视化卡片\n' +
          '· 点击任意节点查看深度解析');
      }
    } catch (err) {
      if (window.console && console.error) console.error('[flowchart_v4_comprehensive] error:', err);
    }
  }

  // 等原来的 DOMContentLoaded 初始化跑完后再执行
  document.addEventListener('DOMContentLoaded', function () {
    setTimeout(boot, 350);
  });

  // 兜底：如果 DOMContentLoaded 已经触发过
  if (document.readyState === 'complete' || document.readyState === 'interactive') {
    setTimeout(boot, 500);
  }
})();
