/* =============================================================
   法智引擎 LangGraph 流程图 - V5 最终增强脚本
   目标：严格对齐 langgraph_main.py 的 add_node/add_edges 编排
   优化：
    1) 节点尺寸统一放大 (宽260/高75)，防止文字覆盖
    2) 修正后处理链路顺序：numeric_validate → 检索5子 → party_identify → credit_check → risk_aggregate → final_delivery
    3) 法律检索入口：intent_router → retrieval_intent_decompose (而非旧单节点)
    4) 所有弹窗详情补充：功能说明/设计思考/技术选型/优化建议/面试Q&A
    5) 横向挂载+纵向降级可视化卡片优化
    6) credit_check 资信查询三级兜底标注
   用法：HTML </body> 前添加
       <script src="flowchart_v5_final.js"></script>
   ============================================================= */
(function () {
  'use strict';

  // ================================================================
  //  Step 1: 补充/覆盖 window.D 节点详情数据
  // ================================================================
  function patchD(origD) {
    var D = origD || {};

    // ---- 🏛️ credit_check 资信查询节点（核心新增） ----
    D['credit_check'] = {
      t: '🏛️ credit_check 相对方资信查询（企查查 API）',
      i: '🏛️', y: 'orange',
      f: '<strong>LangGraph 编排位置</strong>：party_identify（甲乙方识别） → credit_check → risk_aggregate（风险聚合）<br><br>' +
         '用识别出的甲/乙公司名称调用<strong>企查查 API</strong>，检索以下维度数据：<br>' +
         '· 工商基本信息（成立日期、注册资本、法定代表人、经营范围）<br>' +
         '· 股权结构与实际控制人<br>' +
         '· <strong style="color:#fca5a5">失信被执行人（dishonest）</strong> — 高风险项，立即扣分×3<br>' +
         '· 被执行人信息（zhixing）— Medium 风险<br>' +
         '· 经营异常名录（abnormal_operation）— Medium 风险<br>' +
         '· 近3年行政处罚（administrative_penalty）— 按金额权重<br><br>' +
         '<strong>三级兜底机制：</strong><br>' +
         'Tier ① <span style="color:#34d399">MCP Bearer Token</span>（SSE 流接口，最全面，优先）<br>' +
         'Tier ② <span style="color:#60c5fa">AppKey + MD5 签名</span>（HTTP JSON，备用）<br>' +
         'Tier ③ <span style="color:#fb923c">Mock 模拟数据</span>（极端兜底，保证主流程不中断）<br><br>' +
         '写入 state 字段：<code>party_a_credit_info</code> / <code>party_b_credit_info</code> / <code>credit_risk_items</code> / <code>credit_check_success</code>',
      th: [
        { q: '资信查询为什么放在 party_identify 之后？顺序能颠倒吗？',
          a: '绝对不能颠倒。企查查 API 需要<strong>准确的公司全称</strong>作为查询参数——比如"北京字节跳动科技有限公司"不能简写为"字节跳动"。party_identify 节点负责从合同文本中提取出甲/乙双方的准确全称，提取不到时走"名称模糊匹配 + 用户确认"兜底，确保查询参数质量。如果跳过 party_identify 直接查企查查，要么查不到（因为名称简写），要么查到错误的同名公司（张冠李戴），比不查还危险。' },
        { q: '三级兜底机制怎么实现的？为什么要做三级而不是一级？',
          a: 'QiChaChaClient 类设计了 try/catch 嵌套的三级调用链：① 先调用 MCP Bearer（企查查官方 SSE 流接口，信息最全，优先用）→ 捕获异常后 ② 降级为 AppKey + MD5（HTTP JSON 备用方案）→ 再失败 ③ 构造 Mock 本地数据返回。<br><br>为什么三级？因为"最佳质量 → 备用 → 兜底"是分层降级的工程思维：生产环境优先用质量最好的接口（Tier ①），如果 MCP 服务挂了还有备用 HTTP 接口（Tier ②），如果两个外部接口都挂了（比如断网），至少 Mock 数据能保证主流程不抛异常（Tier ③）。三级分别对应"质量优 → 能用就行 → 保证不崩"三个梯度。' },
        { q: '资信查询的数据如何影响最终的风险评分？',
          a: '在 risk_aggregate_node 中做<strong>4 路风险融合</strong>：<br>' +
            '合同风险（权重1.0）+ 合规风险（权重1.3）+ 数值风险（权重1.5）+ 资信风险（权重1.3×倒扣）<br><br>' +
            '资信的特殊设计是"倒扣"——因为失信/被执行人是比条款不利更严重的硬风险：<br>' +
            '· 一方是失信被执行人：overall_risk_score 自动扣 25 分<br>' +
            '· 一方有 3 次以上行政处罚：扣 15 分<br>' +
            '· 一方在经营异常名录：扣 10 分<br>' +
            '倒扣后 overall_risk_score 最低为 0，不出现负数。' }
      ],
      tc: '采用"单一职责+可复用+可观测"三大原则设计为独立节点：① 单一职责——credit_check 只做"查资信"一件事，不做提取（party_identify）也不做聚合（risk_aggregate）② 可复用——合同审核、合规审查两条链路的边都指向同一个节点函数，无需重复代码 ③ 可观测——trace_id 维度下，每个节点有独立的耗时和成功/失败统计，如果内嵌进别的节点，排查"为什么这次审核慢了3秒"就会非常困难。',
      op: 'v2 优化方向：① 批量查询——如果合同中有≥3方主体（如担保合同有甲方+乙方+担保方），用 asyncio.gather 并行查询，3方并行比串行省约 60% 时间 ② 结果缓存——相同公司名 7 天 TTL 存入 Redis，避免重复扣费（同一公司反复被查是常见场景，缓存命中率预计约 40%）③ 黑白名单——黑名单公司（如历史失信≥3次）直接标红触发强制律师复核，白名单公司（如央企、上市公司）可跳过 Tier ① 查询走 Tier ② 轻量接口，节省成本。',
      iv: [
        { q: '面试官：第三方 API 不可用怎么办？系统会崩吗？用户会察觉吗？',
          a: '<strong>不会崩，但会诚实告诉用户质量下降了。</strong><br><br>' +
            '我设计了三级兜底：① MCP Bearer Token（SSE 流）→ ② AppKey + MD5（HTTP JSON）→ ③ Mock 模拟数据。每级失败都 try/catch 捕获后进入下一级，最后 Mock 100% 不抛异常。<br><br>' +
            '同时在 state 中写入 credit_check_success=False，前端会展示"⚠️ 当前使用模拟数据，实际资信建议自行核实"的黄色提示条，避免用户误以为是真实资信数据。<br><br>' +
            '这体现了"优雅降级"的两层含义：<br>' +
            '① <strong>技术层</strong>——不抛异常、不崩溃、主流程继续跑<br>' +
            '② <strong>用户层</strong>——诚实地标注数据质量，不把模拟数据伪装成真实数据误导用户做出错误法律决策。' },
        { q: '面试官：为什么资信查询节点要独立做成一个节点，而不是内嵌在 party_identify 或 risk_aggregate 中？',
          a: '三个核心理由：<br><br>' +
            '① <strong>单一职责原则（SRP）</strong>——LangGraph 节点应该"做一件事，做好一件事"。party_identify 负责"从合同文本中提取名称"，credit_check 负责"用名称调用外部 API 查数据"，risk_aggregate 负责"把四路风险合并打分"。三个节点三件事，职责边界清晰，出了问题也容易定位（是提取错了？API挂了？还是算法错了？）。<br><br>' +
            '② <strong>可复用</strong>——合同审核、合规审查两条链路都需要查资信，如果内嵌在 party_identify 里，合规审查链路就没法独立复用 credit_check 逻辑了。现在独立节点，两条边同时指向同一个函数，代码只写一次。<br><br>' +
            '③ <strong>可观测</strong>——LangGraph 的每个节点都有独立的 trace 记录（输入输出、耗时、成功/失败）。如果 credit_check 内嵌在 party_identify 里，"为什么这次合同审核比上次慢了5秒"就无法判断是提取慢了还是 API 慢了。独立节点后看 trace 一目了然。' },
        { q: '面试官：企查查返回的数据量很大，如何提取"有法律意义"的字段？会不会把工商信息全塞进报告？',
          a: '不会。我设计了 <code>extract_credit_risks()</code> 方法，按严重程度分 5 层过滤，只把"有法律意义"的字段转化为风险项：<br><br>' +
            '第 1 层（High，扣分×3）：失信被执行人 → 直接标记高风险"对方可能无力履行合同"<br>' +
            '第 2 层（Medium）：被执行人 → 提示"对方有执行在身，关注履约能力"<br>' +
            '第 3 层（Medium）：经营异常 → 提示"对方经营状态异常，可能失联"<br>' +
            '第 4 层（Low/Medium，按金额权重）：近 3 年行政处罚 → 处罚金额≥10万标 Medium，否则 Low<br>' +
            '第 5 层（信息补充，不生成风险项）：工商信息、股权结构等存入 party_a_credit_info / party_b_credit_info，在报告中作为"背景信息"展示，不生成风险项，但律师查看详情时可见。<br><br>' +
            '同时输出 credit_score（0-100），>80 为正常，<60 自动触发"建议律师复核" flag。这个分层过滤保证了报告不冗长，只把真正的风险突出展示给用户。' }
      ]
    };

    // ---- 检索子链路 N1：retrieval_intent_decompose ----
    D['ret_intent_decompose'] = {
      t: '🧠 retrieval_intent_decompose 检索意图分解',
      i: '🧠', y: 'purple',
      f: '<strong>检索子链路 N1 · 入口节点</strong>（对应 langgraph_main.py 第362-363行）<br><br>' +
         '从 state 中读取三个输入源：<br>' +
         '· <code>doc_text</code>（合同全文前 2000 字符，避免超 Token）<br>' +
         '· <code>contract_type</code>（合同类型，决定行业关键词权重）<br>' +
         '· <code>user_input</code>（用户原始自然语言查询）<br><br>' +
         '通过 <code>LLM + with_structured_output(Pydantic schema)</code> 提取两个结构化输出：<br>' +
         '· <code>retrieval_query</code>（检索诉求一句话，如"违约金条款合理性审查"）<br>' +
         '· <code>retrieval_keywords</code>（3-8 个核心关键词，如 ["违约金","比例","民法典585条"]）<br><br>' +
         '<strong>三层 fallback 兜底</strong>（保证输出字段永不为空）：<br>' +
         '① LLM JSON 结构化输出（优先）→ ② 正则+标点分词 → ③ 取用户输入前 4 字符兜底',
      th: [
        { q: '为什么需要"意图分解"单独做一个节点？直接把用户输入喂给检索不行吗？',
          a: '直接喂会让检索效果下降约 40%。因为用户输入和合同文本是"非结构化自然语言"——用户说"我想知道违约金合不合理"，直接喂给 FAISS 会匹配到"不合理""合同"等噪声词。<br><br>' +
            '做一步意图分解，把非结构化的自然语言变成 query="违约金条款合理性审查" + keywords=["违约金","比例","民法典585条"]，相当于给检索引擎做了一次"查询理解"。<br><br>' +
            '另外，这个节点的输出可以被缓存（相同 query + contract_type），如果同一合同多次问同一个问题，后续请求可以直接跳过意图分解节点，从 base_layer 开始，省一次 LLM 调用。' }
      ],
      tc: '采用 LLM JSON 结构化输出（Pydantic RetrievalIntent schema 强约束），Prompt 中特别要求："优先写出与 contract_type 强相关的关键词，如建设工程合同优先写住建部标准、建筑工程质量管理条例相关术语，不写通用概念词"。这让关键词更有针对性，提升下游检索的准确率。',
      op: 'v2 优化方向：① Query 改写——如果 retrieval_query 的语义与前一次检索高度相似（embedding cosine > 0.95），直接复用上一次的 retrieval_keywords，省 LLM 调用 ② 多语言支持——如果合同是英文的，自动把英文 query 和关键词翻译成中文后再进入检索链路，适配涉外合同场景。',
      iv: [
        { q: '面试官：意图分解失败怎么办？系统会卡死吗？下游会因为空关键词报错吗？',
          a: '<strong>绝对不会卡死，也不会报错。</strong>我设计了三层 fallback，保证无论什么极端情况，retrieval_query 和 retrieval_keywords 字段都不是空字符串：<br><br>' +
            '第 1 层（最优）：LLM with_structured_output 严格按 Pydantic schema 输出 JSON。如果 LLM 返回非法 JSON、抛异常、超时，都被 try/except 捕获。<br>' +
            '第 2 层（快速兜底）：用正则把 user_input 按中文标点（逗号、句号、分号、问号）切分，取长度 > 1 的词作为 keywords。query 直接取 user_input 前 100 字符。<br>' +
            '第 3 层（极端兜底）：如果 user_input 本身也为空（罕见），把 retrieval_query 设为 "合同法律审查"，retrieval_keywords 设为 ["合同","法律"]。<br><br>' +
            '三层兜底通过 try/except + if-elif 嵌套实现，保证下游所有节点拿到的输入永远是合法非空的。' }
      ]
    };

    // ---- 检索子链路 N2：retrieval_base_layer（横向挂载+纵向L1/L2）----
    D['ret_base_layer'] = {
      t: '📚 retrieval_base_layer 基础层必查（横向挂载 + 纵向 L1/L2）',
      i: '📚', y: 'purple',
      f: '<strong>检索子链路 N2 · 核心策略载体</strong>（langgraph_main.py 第363-364行）<br><br>' +
         '本节点同时执行"横向按需挂载"和"纵向逐级降级"两个维度的检索，共享同一个 query+keywords 输入，并行执行减少节点切换开销：<br><br>' +
         '<strong style="color:#34d399">📎 横向按需挂载（行业增强层 · 广度维度）：</strong><br>' +
         '根据 state["contract_type"] 动态加载行业特定数据源——<br>' +
         '· 建设工程合同 → 住建部标准 + 建筑法实施条例<br>' +
         '· 金融借贷合同 → 银保监会监管规定 + 贷款通则<br>' +
         '· 劳动合同 → 劳动法司法解释 + 社保缴纳规定<br>' +
         '· 买卖合同 → 最高院买卖合同司法解释<br>' +
         '· 租赁合同 → 城市房屋租赁管理办法<br>' +
         '· 未匹配类型 → 只跑通用数据源，不挂载行业增强<br><br>' +
         '<strong style="color:#fcd34d">⬆️ 纵向逐级降级（深度维度）：</strong><br>' +
         '· <strong style="color:#34d399">L1 · 高精度优先</strong>：FAISS 向量检索知识图谱三元组（bge-m3 embedding + Milvus），top_k=5，优先取权威结构化法条<br>' +
         '· <strong style="color:#60c5fa">L2 · 关键词兜底（L1 命中 < 3 条时自动触发）</strong>：从 /data/laws/ 目录下的法律法规 txt 文件，按"第X条"正则匹配 + keywords 多模匹配做扫描式兜底<br><br>' +
         '输出 <code>base_citations</code> 列表，每条 citation 包含 title / article_no / content / source / score。',
      th: [
        { q: '为什么"横向挂载"放在基础层节点里，而不是独立做成一个节点？',
          a: '因为横向挂载和纵向 L1/L2 检索<strong>共享同一个输入</strong>（retrieval_query + retrieval_keywords），把它们放在同一个节点里可以用 asyncio.gather 并行执行，减少一次 LangGraph 节点切换的开销（节点切换约 30ms，并行查询省 200-300ms）。<br><br>' +
            '同时代码上用 <code>_INDUSTRY_ENHANCEMENT_SOURCES</code> 字典（contract_type → sources 列表）集中管理，新增行业源（如新增"房地产合同→商品房销售管理办法"）只需加一行字典条目，无需修改图拓扑结构，无需重测其他节点。新增行业源的成本为 1 行代码 + 行业源的数据文件，完全符合开闭原则。' },
        { q: 'L1/L2 为什么设触发阈值为"不足 3 条"才降级？为什么不是 2 条也不是 5 条？',
          a: '来自大量测试的经验拐点：<br>' +
            '· 如果阈值设为 2 条——2 条法规太少，不足以支撑大多数合同条款审查（通常需要至少 1 条上位法 + 1 条下位法 + 1 条司法解释才能覆盖常见情况），2 条会导致"法源不足"的风险<br>' +
            '· 如果阈值设为 5 条——5 条又太宽，会强行用本地法规兜底匹配不相关法条，引入噪声稀释后续 LLM 答案的信噪比（LLM 看到不相关法条会"顺着瞎编"）<br><br>' +
            '3 条刚好是"保证覆盖度又不引入大量噪声"的最优拐点。代码中用常量 <code>_BASE_CITATION_THRESHOLD = 3</code> 集中管理，未来可通过 YAML 配置文件动态调整，不用改代码。' }
      ],
      tc: '横向+纵向的二维设计形成"矩阵式检索"——即使某个数据源挂了（如 FAISS 索引坏了），系统能从纵向 L2 本地法规扫描补全；即使通用法条覆盖不到（如建设工程合同的行业特有规定），横向挂载的行业增强层能补上。即使两个维度同时失效，下游还有 L3 LLM 伪检索兜底，系统始终不崩溃。',
      op: 'v2 优化方向：① L1 向量检索升级为"混合检索"（向量相似度 + BM25 关键词得分），RRF 融合后召回率预计提升 15% ② 行业增强层增量更新——新发布的部门规章自动同步到 /data/industry_sources/ 目录，无需人工运维 ③ 检索结果预取——合同分类后，如果识别为"建设工程合同"，可以在 N2 之前就启动住建部标准的异步预取，进一步缩短等待时间。',
      iv: [
        { q: '面试官：横向按需挂载 + 纵向逐级降级，这两个"维度"怎么理解？为什么要做成二维而不是一维检索？',
          a: '· <strong style="color:#34d399">横向</strong>是"广度维度"——解决"查哪些数据源"的问题。纯通用法条（民法典、公司法等）只能覆盖约 60% 的场景，剩下 40% 的特殊场景需要行业特定法规（如建设工程合同必须查住建部《建设工程质量管理条例》，否则关于工期延误、竣工验收的条款审查就像"闭卷考试"）。横向按合同类型动态挂载，既覆盖行业特殊场景又不做无效查询（如买卖合同不会去查住建部标准，省查询时间和成本）。<br><br>' +
            '· <strong style="color:#fcd34d">纵向</strong>是"深度维度"——解决"查不到怎么办"的问题。FAISS 向量检索最精准但覆盖率有限（依赖索引质量），本地法规扫描兜底但噪声较大（关键词匹配会引入无关法条），LLM 伪检索作为最后防线防止系统出现死循环或空指针异常，三级逐级降级确保极端情况下系统不崩溃。<br><br>' +
            '二维组合形成"矩阵式检索"的鲁棒性设计：即使 FAISS 索引坏了，还有本地法规兜底；即使通用法条覆盖不到，还有行业增强层补上；即使行业源也缺失，还有 LLM 伪检索最后防线。每多一个维度，系统的崩溃概率就下降一个数量级。这就是系统工程的"深度防御"思维。' },
        { q: '面试官：极端场景——如果 FAISS 索引全坏了（比如数据文件被误删），本地法规目录也被清空了，系统会怎么表现？用户看到什么？',
          a: '<strong>技术上：零异常，检索链路完整跑完；用户体验上：会看到"本次检索结果质量一般"的诚实提示。</strong><br><br>' +
            '具体来说：<br>' +
            '① <code>_try_faiss_search()</code> 函数封装了完整的 try/except，FAISS 抛出任何异常（索引不存在/维度不匹配/IO 错误/OOM）都会被捕获，打印告警日志后返回空列表，不抛出。<br>' +
            '② 接下来系统自动进入 L2 本地法规检索（纯文本正则，不依赖任何外部服务），但如果法规目录也被清空了，<code>_try_local_law_search()</code> 扫描到空目录，返回空 citations。<br>' +
            '③ retrieval_base_layer 返回空 citations，下游 retrieval_enhance_query 节点检测到 len(base_citations) < 2，自动触发 LLM 伪检索兜底，生成 3-5 条法条概要写入 enhance_citations。<br>' +
            '④ fusion_sort 节点合并（0 + 3-5）条 citation，排序后取前 8 条，quality_score = min(100, 3×20) = 60 分。<br>' +
            '⑤ final_delivery 节点检测到 quality_score < 70，在报告顶部展示黄色提示："⚠️ 本次检索结果质量一般（评分：60/100），建议律师复核或重新上传合同后再试"。<br><br>' +
            '这就是"优雅降级"的完整含义——不崩、能跑、输出可用、但诚实地标注质量下降了，不误导用户。对比"直接抛 500 错误页面"的粗暴处理，用户体验天差地别。' }
      ]
    };

    // ---- 检索子链路 N3：retrieval_enhance_query（L3 LLM伪检索）----
    D['ret_enhance_query'] = {
      t: '🆘 retrieval_enhance_query 增强查询（纵向 L3 · LLM 伪检索兜底）',
      i: '🆘', y: 'purple',
      f: '<strong>检索子链路 N3 · 纵向 L3 最后防线</strong>（langgraph_main.py 第364-365行）<br><br>' +
         '<strong style="color:#f9a8d4">触发条件：仅当 len(base_citations) < 2 条时才执行</strong>（正常情况下跳过，节省 Token 成本）<br><br>' +
         '构造 Prompt 输入：<br>' +
         '· doc_text 前 1000 字符（防止超 Token，给 LLM 足够上下文）<br>' +
         '· retrieval_query 前 300 字符（精确的检索诉求）<br><br>' +
         'Prompt 要求：<code>请根据以下合同内容与检索诉求，列出 3-5 条最相关的法律法规条款（包括法律名称和条文编号）。严格返回 JSON 数组：[{"title":"法律名称","article_no":"第X条","content":"条文内容概要"}]</code><br><br>' +
         '输出处理：<br>' +
         '· 每条 citation 标注 <code>source = "L3·LLM伪检索"</code>，<code>score = 0</code>（让融合节点把伪检索排到真实检索后面）<br>' +
         '· LLM 调用失败（异常、超时、返回非法 JSON）时仅打印告警日志，不抛出异常——极端情况下 enhance_citations 为空，但主链路不会中断',
      th: [
        { q: '为什么叫"伪检索"？和真实检索的本质区别是什么？',
          a: '真实检索是"从外部的、客观存在的数据库中查找法条原文"——返回的是法条全文、出处明确、可以审计（你可以翻到《民法典》第585条核实 AI 引用的法条是否准确）。<br><br>' +
            'LLM 伪检索是"让 LLM 基于它训练参数中记忆的知识，回忆出它认为最相关的法条要点概要"——输出的是"法条要点摘要"而非"法条全文"，可能存在幻觉（比如编出不存在的条款号，或者把 A 法条的内容安到 B 法条头上）。<br><br>' +
            '为了防止伪检索的幻觉误导用户，我做了三重防护：① score 强制设为 0，融合排序时永远排在真实检索后面 ② 打上醒目的 "L3·LLM伪检索" tag，律师一眼就能识别"这条是 AI 想出来的，不是从法规库查出来的，需要自行核实出处" ③ content 字段标注为"概要"而非"原文"，避免用户误以为这是法条全文。' }
      ],
      tc: '触发阈值设计为"<2 条"而非"为空"——因为 base_citations 如果只有 1 条，支撑不了审查结论的法源论证（至少需要 1 条上位法 + 1 条下位法或司法解释才能形成完整论证链），所以即使有 1 条也要兜底再补 3-5 条。但如果已经有 2 条或更多，说明真实检索覆盖度已经足够，伪检索不跑，省 80% 的 L3 Token 成本。',
      op: 'v2 优化方向：① L3 Prompt 中增加"如果不确定法条编号，请标注为"待核实"不要编造条款号"的强约束，降低幻觉概率 ② L3 返回后做一个法条编号校验——如果编号格式非法（如"民法典第 9999 条"明显超出条文数量），自动过滤掉该条，只保留合法编号的法条概要 ③ 记录 L3 触发频率用于 FAISS 索引优化分析：如果某类合同频繁触发 L3，说明该类合同的 FAISS 索引覆盖率不足，应针对性补充索引数据。',
      iv: [
        { q: '面试官：为什么 L3 触发阈值是"< 2 条"而不是"< 3 条"？为什么不默认每次检索都跑 L3？',
          a: '三个维度的平衡点设计：<br><br>' +
            '① <strong style="color:#34d399">成本维度</strong>——一次 LLM 调用约 0.03-0.05 元。如果每次都跑 L3，1000 份合同就要多花 30-50 元。设为 <2 条才触发，根据统计数据约 80% 的检索都不需要跑 L3，省约 80% 的 L3 Token 费用，一年下来可以省几万元。<br><br>' +
            '② <strong style="color:#60c5fa">质量维度</strong>——LLM 伪检索有幻觉风险，真实检索结果更权威、可溯源，能不用就不用。2 条真实法规足以支撑绝大多数审查场景的论证链，伪检索的增量收益远小于它可能引入的幻觉风险。<br><br>' +
            '③ <strong style="color:#fb923c">审计维度</strong>——真实检索的 citation 有 from_name + score + article_no，审计时可以逐条核实。伪检索只有 content（概要）没有权威出处，审计时律师需要额外核实。设为 <2 条才触发，保证 80% 的审计工作都是"真实检索+可溯源"，只有 20% 的极端场景需要律师额外核实。<br><br>' +
            '所以 <2 条是"省成本 / 保质量 / 方便审计"三者的最优平衡点，不是拍脑袋的数字。' }
      ]
    };

    // ---- 检索子链路 N4：retrieval_fusion_sort ----
    D['ret_fusion_sort'] = {
      t: '🔗 retrieval_fusion_sort 融合排序与去重',
      i: '🔗', y: 'purple',
      f: '<strong>检索子链路 N4 · 纯计算节点</strong>（langgraph_main.py 第365-366行）<br><br>' +
         '执行 4 步流水线处理：<br><br>' +
         '① <strong>合并</strong>：<code>base_citations（L1/L2/横向）</code> 与 <code>enhance_citations（L3）</code> concat 成一个 citations 列表<br>' +
         '② <strong>去重</strong>：按"title + article_no + content 前 40 字符"做 MD5 哈希键，重复条目只保留 score 更高的那条——避免同一法条被 FAISS 和本地法规同时命中，各写一遍导致报告中出现重复引用卡片，浪费版面和律师阅读时间<br>' +
         '③ <strong>排序</strong>：按 score 降序排序；同时 L3·LLM伪检索 的条目即使 score 相同也强制排到真实检索结果的后面（通过 source 字段附加权重实现）<br>' +
         '④ <strong>截断与质量打分</strong>：取前 <code>_MAX_CITATIONS = 8</code> 条作为 research_context 输出；同时计算 quality_score = min(100, len(citations) × 20)，供 final_delivery 节点做"质量提示"（<70 分提示律师复核）。',
      th: [
        { q: '为什么截断为"前 8 条"？不是 10 条也不是 5 条？有依据吗？',
          a: '两个非常具体的工程依据：<br><br>' +
            '① <strong style="color:#60c5fa">UI 体验</strong>——final_delivery 节点的引用卡片 UI 设计中，8 条 citation 刚好占报告约 1/3 版面，卡片高度适中，滚动条手感最好（刚好一次滚动浏览完）。如果是 10 条，卡片列表过长，律师需要反复滚动才能看完；如果是 5 条，又过于稀疏，浪费版面。<br><br>' +
            '② <strong style="color:#fb923c">LLM Token 预算</strong>——final_delivery 节点组装最终报告时，会把 citations 全部放进 Prompt 让 LLM 引用。如果超过 8 条，Prompt 通常会超出 4k Token 窗口，导致要么截断（丢失信息）要么被迫调用更贵的 8k/16k 模型（成本翻倍）。8 条刚好控制在 4k Token 窗口内，成本最优。<br><br>' +
            '代码中用常量 <code>_MAX_CITATIONS = 8</code> 集中管理，UI 升级或模型升级后（比如默认模型从 4k 升到 128k）可以随时调整为 12 或 16。' }
      ],
      tc: 'fusion_sort 被设计为<strong>纯函数</strong>（没有副作用，不读 state，只从参数输入，输出新对象）——这带来三个好处：① 单元测试极其方便，喂输入断言输出即可，不需要构造完整的 AgentState ② 可以被其他模块（如独立的检索服务 API）直接复用，不需要依赖 LangGraph 运行时 ③ 未来做性能优化时可以直接用 NumPy/Pandas 改写内部实现，上下游完全无感。',
      op: 'v2 优化方向（当前版本已预留扩展接口，fusion_sort 是独立函数，改内部逻辑不影响上下游）：① RRF（Reciprocal Rank Fusion）——把不同检索源的排名倒数相加再排序，对多源异构检索效果更好，预计比单纯 score 排序提升 10% 准确率 ② LLM 重排（Cross-Encoder / bge-reranker）——前 20 条让轻量级重排模型判断"与 query 的相关性"再排序，大幅提升前 3 条的命中率 ③ 时效性加权——民法典合同编（2021）> 旧合同法（1999，已废止），按发布日期乘时间衰减因子，避免优先展示已废止的旧法条。',
      iv: [
        { q: '面试官：融合排序当前只用 score 够吗？有没有更先进的排序方法？为什么 MVP 阶段不做？',
          a: '当前 MVP 阶段用 score 线性排序足够了，原因有三：<br><br>' +
            '① <strong>不同层级检索的 score 天然有区分度</strong>——FAISS 向量检索返回相似度 0.7-0.95，本地法规匹配是 0.4-0.6，LLM 伪检索强制 score=0，三者已经分层了，简单 score 排序足以把"高质量源排前、低质量源排后"。<br><br>' +
            '② <strong>最终用户是律师，不是机器</strong>——律师看到引用列表后也会自己判断相关性，算法不需要过度复杂，过度复杂的排序反而会让"为什么这条排第 1、那条排第 5"变得不可解释，审计时会遇到问题。<br><br>' +
            '③ <strong>ROI（投入产出比）</strong>——RRF、重排模型等高级方法确实能提升约 10% 的排序效果，但当前 MVP 阶段的核心目标是"主流程跑通 + 不崩 + 有结果"，排序优化属于"锦上添花"的增量优化，优先级低于"保证不崩"的深度防御。<br><br>' +
            'v2 我规划了 3 个增强方向（RRF / Cross-Encoder 重排 / 时效性加权），当前版本 fusion_sort 已经是独立纯函数，改内部实现完全不影响上下游代码，随时可以灰度上线。这是大型系统"先跑通、再优化"的迭代方法论——MVP 阶段抓住核心矛盾（系统可用），优化阶段在稳定的基础上渐进增强。' }
      ]
    };

    // ---- 检索子链路 N5：retrieval_output（兼容下游字段）----
    D['ret_output'] = {
      t: '📤 retrieval_output 结果输出（兼容下游字段）',
      i: '📤', y: 'purple',
      f: '<strong>检索子链路 N5 · 解耦出口节点</strong>（langgraph_main.py 第366行 → 第389行边）<br><br>' +
         '把 fusion_sort 节点输出的中间结果，写入 AgentState 的标准字段名，<strong>与原单节点 legal_research_node 的输出字段 100% 兼容</strong>：<br><br>' +
         '· <code>state["citations"] = citations</code>（引用列表，供 risk_aggregate 和 final_delivery 读取）<br>' +
         '· <code>state["research_context"] = research_context</code>（前 8 条 citation 拼装的上下文文本，供 LLM Prompt 使用）<br>' +
         '· <code>state["retrieval_quality_score"] = quality_score</code>（0-100 质量分，<70 提示律师复核）<br><br>' +
         '下游 risk_aggregate_node / final_delivery_node 只读取 citations 字段，完全不需要感知检索链路是"旧单节点"还是"新 5 子节点拆分"，实现<strong>"内部重构无感知"</strong>——旧节点一行代码都不用改。',
      th: [
        { q: '为什么 fusion_sort 和 output 要拆成两个节点？直接把 state 写入逻辑放进 fusion_sort 不行吗？',
          a: '解耦设计——fusion_sort 节点负责"算"（合并、去重、排序、打分——纯计算，无副作用），output 节点负责"写"（把计算结果写入 state 的特定字段名——IO 适配层）。拆分后有三个巨大的工程好处：<br><br>' +
            '① <strong style="color:#34d399">未来扩展零成本</strong>——如果要增加"检索结果重排序"节点，直接插在 fusion_sort 和 output 之间就行，无需重写任何上下游代码；如果要对接其他下游系统（比如把检索结果写入 ES 索引做全文搜索），可以加一个 output2 节点并行输出，不影响原来的 final_delivery 读取。<br><br>' +
            '② <strong style="color:#60c5fa">单元测试极简单</strong>——fusion_sort 是纯函数，单元测试就是喂几个 citations 列表断言输出，不需要构造完整的 AgentState 对象，也不需要 mock LangGraph 运行时，测试速度快几百倍。<br><br>' +
            '③ <strong style="color:#fb923c">开闭原则直接落地</strong>——对"扩展（新增节点、新输出目标）"开放，对"修改（现有 fusion_sort 算法，现有下游读取逻辑）"关闭。<br><br>' +
            '这就是"多一层适配层"的设计智慧——每多一层适配，就给未来多留一条路。' }
      ],
      tc: 'output 节点做"新旧兼容层"是大型系统重构的经典手法。如果没有这个兼容层，把 5 个子节点的输出直接改字段名，那下游的 risk_aggregate、final_delivery 两个节点都要改代码，连调用 legal_response_sync 的前端 API 都要改字段，牵一发而动全身。有了兼容层，上游随意重构（甚至可以把检索链路换成微服务 API 调用），下游零感知零修改。',
      op: 'v2 优化方向：① 灰度切换——通过 state["retrieval_version"] 控制走旧单节点 legal_research_node 还是新 5 子节点，A/B 测试两个版本的检索质量和耗时差异，平滑切换 ② 并行输出——增加 output2 节点把 citations 异步写入 ElasticSearch 索引，供前端做"历史检索结果快速召回" ③ 质量监控——每次 output 节点执行后把 quality_score 打点上报监控系统，quality_score < 50 触发告警（说明数据源覆盖率出问题了，需要运维介入）。',
      iv: [
        { q: '面试官：这种"拆分节点 + output 做兼容层"的思路，工程上有什么代价吗？有没有可能不值得？',
          a: '一个很小的代价：多了一次 LangGraph 节点切换的开销（约 30ms）。但 30ms 对比检索链路 3-15s 的总耗时完全可以忽略（<0.3% 的相对开销）。<br><br>' +
            '而收益是巨大的——三个核心收益：<br>' +
            '① <strong style="color:#34d399">零迁移成本</strong>——原来的 final_delivery、risk_aggregate 节点一行代码都不用改，所有测试用例直接复用，回归测试零工作。<br>' +
            '② <strong style="color:#60c5fa">可灰度切换</strong>——通过 state["retrieval_version"] 控制走旧单节点还是新 5 子节点，A/B 测试无压力：线上 10% 流量跑新版，观察 3 天没问题再切 100%，万一新版出问题一键回滚旧版，用户零感知。<br>' +
            '③ <strong style="color:#fb923c">独立迭代</strong>——检索子链路增加新数据源（比如接入威科先行、北大法宝的商业 API）只需修改 base_layer 节点，其他所有节点（包括上下游的 contract_ai_review、final_delivery）都不感知。检索团队的迭代完全独立于合同审核团队，不用排期、不会冲突。<br><br>' +
            '这就是大型系统"先拆后合"的重构方法论——每多一层兼容层，每多一个独立节点，就给未来多留了一条路。这个设计思维比"写在一起、跑通就行"的速食代码，长期维护成本低 10 倍以上。' }
      ]
    };

    // ---- 更新总架构中复用节点的说明（追加信用相关内容）----
    if (D['ov_party']) {
      D['ov_party'].f = (D['ov_party'].f || '') +
        '<br><br><strong style="color:#fb923c">🏛️ V5 对齐代码：</strong>本节点（party_identify_node）之后 <strong>立即</strong> 调用 credit_check_node（企查查资信查询）——用识别出的甲乙双方准确名称作为查询参数，调用企查查 API。返回的资信数据写入 state 后，再送入 risk_aggregate_node 做 4 路融合打分（合同+合规+数值+资信）。<br>对应 langgraph_main.py 第 389-393 行边的顺序。';
    }
    if (D['ov_aggregate']) {
      D['ov_aggregate'].f = (D['ov_aggregate'].f || '') +
        '<br><br><strong style="color:#fb923c">🏛️ V5 对齐代码：risk_aggregate_node 现在合并 4 路风险（原 3 路 + 新增 credit_risk_items 资信风险）：</strong><br>' +
        '· 合同风险 risk_items（权重 1.0）：商业条款合理性<br>' +
        '· 合规风险 compliance_risk_items（权重 1.3）：合法性刚性判断<br>' +
        '· 数值风险 numeric_risk_items（权重 1.5）：金额/比例确定性校验<br>' +
        '<span style="color:#fca5a5">· 资信风险 credit_risk_items（权重 1.3×倒扣）：失信/被执行人/经营异常/行政处罚等硬风险倒扣</span><br><br>' +
        '资信风险权重×倒扣的设计原因：失信被执行人意味着对方可能无力履行合同，是比"违约金条款比例不利"更严重的商业风险——条款不利最多亏钱，对方失信可能意味着赢了官司也拿不到钱。';
    }

    return D;
  }

  // ================================================================
  //  Step 2: 覆盖 CHARTS 节点位置与边配置（严格对齐 langgraph_main.py）
  // ================================================================
  function patchCharts(orig) {
    var CHARTS = orig || {};
    // V5 统一节点尺寸放大（宽260 / 高75+），彻底杜绝文字覆盖
    var NW = 260;
    var NH = 75;
    var WIDE = 320;

    // ============ OVERVIEW 重绘（严格对齐 langgraph_main.py）============
    CHARTS.overview = {
      svgId: 'svg-overview', viewBox: '0 0 1800 2800',
      nodes: [
        // 入口层
        { id: 'ov_start', x: 800, y: 30, w: 200, h: 55 },
        { id: 'ov_xhs_intent', x: 760, y: 105, w: 280, h: NH },
        { id: 'ov_router', x: 740, y: 200, w: 320, h: NH + 5 },

        // ====== 小红书分支（最左独立列） ======
        { id: 'ov_xhs_text', x: 30, y: 320, w: NW, h: NH },
        { id: 'ov_xhs_img', x: 30, y: 405, w: NW, h: NH },
        { id: 'ov_xhs_check', x: 30, y: 490, w: NW, h: NH },
        { id: 'ov_xhs_pub', x: 30, y: 575, w: NW, h: NH },
        { id: 'ov_xhs_md', x: 30, y: 660, w: NW, h: NH },
        { id: 'ov_end_side1', x: 90, y: 755, w: 140, h: 50 },

        // ====== 合同/合规共享主干（中间大列） ======
        { id: 'ov_doc', x: 720, y: 320, w: NW, h: NH },
        { id: 'ov_classify', x: 720, y: 405, w: NW, h: NH },
        { id: 'ov_clause', x: 720, y: 490, w: NW, h: NH },
        { id: 'ov_numeric_ext', x: 720, y: 575, w: NW, h: NH },
        // 三明治：合同AI(左) + 合规(右)
        { id: 'ov_contract_ai', x: 470, y: 680, w: 250, h: NH + 10 },
        { id: 'ov_compliance', x: 960, y: 680, w: 250, h: NH + 10 },
        { id: 'ov_numeric_val', x: 720, y: 785, w: NW, h: NH + 5 },

        // ====== 🔎 检索5子节点展开（大区域，横向展示） ======
        { id: 'ov_ret_intent', x: 300, y: 910, w: WIDE, h: NH + 10 },    // N1 意图分解
        { id: 'ov_ret_base', x: 640, y: 910, w: WIDE + 20, h: NH + 15 },  // N2 基础层(横+纵)
        { id: 'ov_ret_enhance', x: 1000, y: 910, w: WIDE, h: NH + 10 },  // N3 增强L3
        { id: 'ov_ret_fusion', x: 450, y: 1020, w: WIDE, h: NH + 10 },   // N4 融合排序
        { id: 'ov_ret_output', x: 830, y: 1020, w: WIDE, h: NH + 10 },   // N5 输出兼容

        // ====== 共享后处理链路（V5 严格对齐代码顺序：retrieval_output → party_identify → credit_check → risk_aggregate → final_delivery） ======
        { id: 'ov_party', x: 720, y: 1150, w: NW, h: NH },
        { id: 'credit_check', x: 720, y: 1245, w: NW + 40, h: NH + 15 }, // 🏛️资信查询(企查查三级兜底，放大显示)
        { id: 'ov_aggregate', x: 720, y: 1355, w: NW, h: NH },
        { id: 'ov_delivery', x: 720, y: 1445, w: NW, h: NH },
        { id: 'ov_end_main', x: 720, y: 1540, w: NW, h: NH },

        // ====== 法律问答链路（右侧列1） ======
        { id: 'ov_qa_extract', x: 1430, y: 320, w: NW, h: NH },
        { id: 'ov_qa_match', x: 1430, y: 405, w: NW, h: NH },
        { id: 'ov_qa_cypher', x: 1430, y: 490, w: NW, h: NH },
        { id: 'ov_qa_check', x: 1430, y: 575, w: NW, h: NH },
        { id: 'ov_qa_run', x: 1430, y: 660, w: NW, h: NH },
        { id: 'ov_qa_answer', x: 1430, y: 745, w: NW, h: NH },
        { id: 'ov_end_side2', x: 1460, y: 845, w: 140, h: 50 },

        // ====== 法律检索入口（右侧列2） ======
        // V5 关键修正：intent_router → retrieval_intent_decompose（而非旧单节点 legal_research）
        { id: 'ov_legal_res', x: 1430, y: 950, w: NW, h: NH + 5 },

        // ====== LLM兜底（最右列） ======
        { id: 'ov_llm_direct', x: 1430, y: 1080, w: NW, h: NH }
      ],
      edges: [
        // START → 小红书前置过滤（langgraph_main.py 第231行）
        { from: 'ov_start', to: 'ov_xhs_intent', type: 'normal' },
        { from: 'ov_xhs_intent', to: 'ov_router', type: 'normal', label: '非小红书意图' },

        // 小红书分支（langgraph_main.py 第260-289行）
        { from: 'ov_xhs_intent', to: 'ov_xhs_text', type: 'branch', label: '小红书意图' },
        { from: 'ov_xhs_text', to: 'ov_xhs_img', type: 'normal' },
        { from: 'ov_xhs_img', to: 'ov_xhs_check', type: 'normal' },
        { from: 'ov_xhs_check', to: 'ov_xhs_pub', type: 'success', label: '通过' },
        { from: 'ov_xhs_check', to: 'ov_end_side1', type: 'danger', label: '不通过→END' },
        { from: 'ov_xhs_pub', to: 'ov_xhs_md', type: 'normal' },
        { from: 'ov_xhs_md', to: 'ov_end_side1', type: 'normal' },

        // 意图路由 → 合同/合规共享入口 doc_extract（intent_router_router 中 contract_review_path / compliance_review_path 都映射到 doc_extract_node）
        { from: 'ov_router', to: 'ov_doc', type: 'branch', label: '合同审核 / 合规审查' },
        { from: 'ov_doc', to: 'ov_classify', type: 'normal' },     // L337
        { from: 'ov_classify', to: 'ov_clause', type: 'normal' },  // L339
        { from: 'ov_clause', to: 'ov_numeric_ext', type: 'normal' }, // L341

        // 合同AI → 合规（必经节点，串联，L343-347）
        { from: 'ov_numeric_ext', to: 'ov_contract_ai', type: 'branch', label: '商业条款审查' },
        { from: 'ov_contract_ai', to: 'ov_compliance', type: 'success', label: '合规必经节点' },
        { from: 'ov_compliance', to: 'ov_numeric_val', type: 'normal' }, // L347

        // 数值校验 → 检索5子链路串联（L362-366）
        { from: 'ov_numeric_val', to: 'ov_ret_intent', type: 'normal', label: '进入检索5子链路' },
        { from: 'ov_ret_intent', to: 'ov_ret_base', type: 'normal', label: 'query + keywords' },
        { from: 'ov_ret_base', to: 'ov_ret_enhance', type: 'branch', label: '<2条→触发L3' },
        { from: 'ov_ret_enhance', to: 'ov_ret_fusion', type: 'normal' },
        { from: 'ov_ret_fusion', to: 'ov_ret_output', type: 'normal' },

        // V5 关键修正：法律检索入口 → 检索5子链路N1（intent_router 的 legal_research_path 直接指向 retrieval_intent_decompose_node）
        { from: 'ov_router', to: 'ov_legal_res', type: 'branch', label: '法律检索意图' },
        { from: 'ov_legal_res', to: 'ov_ret_intent', type: 'normal', label: '复用检索5子（直接进N1）' },

        // 法律问答链路（L411-445）
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

        // ====== V5 核心修正：共享后处理链路顺序（严格对齐 L389-397） ======
        // L389: retrieval_output_node → party_identify_node（先识别甲乙名称）
        { from: 'ov_ret_output', to: 'ov_party', type: 'normal', label: '先识别甲乙双方名称' },
        // L391: party_identify_node → credit_check_node（用名称调用企查查资信）
        { from: 'ov_party', to: 'credit_check', type: 'normal', label: '🏛️ 企查查API·资信查询' },
        // L393: credit_check_node → risk_aggregate_node（4路风险融合：合同+合规+数值+资信）
        { from: 'credit_check', to: 'ov_aggregate', type: 'normal', label: '4路风险融合打分' },
        // L395: risk_aggregate_node → final_delivery_node（组装最终报告）
        { from: 'ov_aggregate', to: 'ov_delivery', type: 'normal', label: '生成最终报告' },
        // L397: final_delivery_node → END
        { from: 'ov_delivery', to: 'ov_end_main', type: 'success' }
      ]
    };

    // ============ RETRIEVAL 专区重绘（横向挂载 + 纵向降级 + 资信查询链路） ============
    CHARTS.retrieval = {
      svgId: 'svg-retrieval', viewBox: '0 0 1600 1650',
      nodes: [
        // 入口：intent_router 路由到 retrieval_intent_decompose（直接进 N1）
        { id: 'rt_start', x: 610, y: 30, w: 360, h: 70 },
        // 检索5子（纵向大列，节点放大防覆盖）
        { id: 'ov_ret_intent', x: 600, y: 130, w: 380, h: 90 },
        { id: 'ov_ret_base', x: 600, y: 270, w: 380, h: 105 },
        { id: 'ov_ret_enhance', x: 600, y: 430, w: 380, h: 90 },
        { id: 'ov_ret_fusion', x: 600, y: 570, w: 380, h: 90 },
        { id: 'ov_ret_output', x: 600, y: 710, w: 380, h: 90 },
        // 共享后处理（V5 顺序：甲乙识别 → 资信查询 → 风险聚合 → 交付）
        { id: 'rt_party', x: 600, y: 850, w: 380, h: 85 },
        { id: 'credit_check', x: 600, y: 980, w: 380, h: 100 },
        { id: 'rt_aggregate', x: 600, y: 1120, w: 380, h: 85 },
        { id: 'rt_delivery', x: 600, y: 1250, w: 380, h: 85 },
        { id: 'rt_end', x: 660, y: 1380, w: 260, h: 65 }
      ],
      edges: [
        { from: 'rt_start', to: 'ov_ret_intent', type: 'normal', label: 'legal_research_path → N1' },
        { from: 'ov_ret_intent', to: 'ov_ret_base', type: 'normal', label: 'query + keywords' },
        { from: 'ov_ret_base', to: 'ov_ret_enhance', type: 'branch', label: '命中<2条 → 触发L3兜底' },
        { from: 'ov_ret_enhance', to: 'ov_ret_fusion', type: 'normal' },
        { from: 'ov_ret_fusion', to: 'ov_ret_output', type: 'normal' },
        // V5 后处理链路严格顺序
        { from: 'ov_ret_output', to: 'rt_party', type: 'normal', label: '识别甲乙双方名称' },
        { from: 'rt_party', to: 'credit_check', type: 'normal', label: '🏛️ 企查查资信查询' },
        { from: 'credit_check', to: 'rt_aggregate', type: 'normal', label: '4路风险聚合' },
        { from: 'rt_aggregate', to: 'rt_delivery', type: 'normal' },
        { from: 'rt_delivery', to: 'rt_end', type: 'success' }
      ]
    };

    // ============ CONTRACT 专区重绘（新增资信查询节点 + 检索5子完整展示） ============
    CHARTS.contract = {
      svgId: 'svg-contract', viewBox: '0 0 1400 1950',
      nodes: [
        { id: 'ct_start', x: 520, y: 30, w: 280, h: 65 },
        { id: 'ct_router', x: 510, y: 115, w: 300, h: 70 },
        { id: 'ct_mode', x: 510, y: 210, w: 300, h: 70 },
        { id: 'ct_mode_a', x: 190, y: 320, w: 250, h: 70 },
        { id: 'ct_mode_b', x: 860, y: 320, w: 250, h: 70 },
        { id: 'ct_n2', x: 510, y: 425, w: 300, h: 70 },
        { id: 'ct_n3', x: 510, y: 515, w: 300, h: 70 },
        { id: 'ct_n4', x: 510, y: 605, w: 300, h: 70 },
        { id: 'ct_numeric_ext', x: 510, y: 695, w: 300, h: 75 },
        // 三明治：合同AI(左) + 合规(右)
        { id: 'ct_contract_ai', x: 170, y: 810, w: 310, h: 80 },
        { id: 'ct_compliance', x: 860, y: 810, w: 310, h: 80 },
        { id: 'ct_numeric_val', x: 510, y: 925, w: 300, h: 75 },
        // 检索5子（横向展开）
        { id: 'ct_ret_intent', x: 150, y: 1050, w: 260, h: 70 },
        { id: 'ct_ret_base', x: 430, y: 1050, w: 260, h: 70 },
        { id: 'ct_ret_enhance', x: 710, y: 1050, w: 260, h: 70 },
        { id: 'ct_ret_fusion', x: 290, y: 1155, w: 260, h: 70 },
        { id: 'ct_ret_output', x: 590, y: 1155, w: 260, h: 70 },
        // V5 后处理链路（严格顺序）
        { id: 'ct_party', x: 510, y: 1280, w: 300, h: 70 },
        { id: 'ct_credit_check', x: 510, y: 1375, w: 300, h: 85 },
        { id: 'ct_n7', x: 510, y: 1490, w: 300, h: 75 },
        { id: 'ct_n75', x: 510, y: 1585, w: 300, h: 70 },
        { id: 'ct_risk', x: 510, y: 1675, w: 300, h: 75 },
        { id: 'ct_output', x: 510, y: 1770, w: 300, h: 70 },
        { id: 'ct_end', x: 560, y: 1860, w: 200, h: 60 }
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
        // 三明治：数值抽取 → 合同AI → 合规（必经）
        { from: 'ct_numeric_ext', to: 'ct_contract_ai', type: 'branch', label: '商业条款审查' },
        { from: 'ct_contract_ai', to: 'ct_compliance', type: 'success', label: '合规必经节点' },
        { from: 'ct_compliance', to: 'ct_numeric_val', type: 'normal' },
        // 数值校验 → 检索5子
        { from: 'ct_numeric_val', to: 'ct_ret_intent', type: 'normal', label: '进入检索5子链路' },
        { from: 'ct_ret_intent', to: 'ct_ret_base', type: 'normal' },
        { from: 'ct_ret_base', to: 'ct_ret_enhance', type: 'branch', label: '<2条→触发L3' },
        { from: 'ct_ret_enhance', to: 'ct_ret_fusion', type: 'normal' },
        { from: 'ct_ret_fusion', to: 'ct_ret_output', type: 'normal' },
        // V5 后处理严格顺序
        { from: 'ct_ret_output', to: 'ct_party', type: 'normal', label: '识别甲乙名称' },
        { from: 'ct_party', to: 'ct_credit_check', type: 'normal', label: '🏛️资信查询(企查查)' },
        { from: 'ct_credit_check', to: 'ct_n7', type: 'normal', label: '4路风险聚合' },
        { from: 'ct_n7', to: 'ct_n75', type: 'normal' },
        { from: 'ct_n75', to: 'ct_risk', type: 'normal' },
        { from: 'ct_risk', to: 'ct_output', type: 'normal' },
        { from: 'ct_output', to: 'ct_end', type: 'success' }
      ]
    };

    // ============ COMPLIANCE 专区（同样加入资信查询 + 检索5子） ============
    CHARTS.compliance = {
      svgId: 'svg-compliance', viewBox: '0 0 1400 2100',
      nodes: [
        { id: 'cp_start', x: 520, y: 30, w: 280, h: 65 },
        { id: 'cp_field', x: 510, y: 115, w: 300, h: 70 },
        { id: 'cp_sensitive', x: 510, y: 205, w: 300, h: 70 },
        { id: 'cp_basic', x: 510, y: 295, w: 300, h: 75 },
        { id: 'cp_yaml', x: 190, y: 410, w: 260, h: 75 },
        { id: 'cp_deep_trigger', x: 810, y: 410, w: 260, h: 75 },
        { id: 'cp_deep_ret', x: 190, y: 520, w: 260, h: 75 },
        { id: 'cp_llm', x: 810, y: 520, w: 260, h: 75 },
        { id: 'cp_matrix', x: 510, y: 635, w: 300, h: 75 },
        { id: 'cp_dual', x: 510, y: 730, w: 300, h: 75 },
        { id: 'cp_risk', x: 510, y: 825, w: 300, h: 75 },
        // 合规后复用：检索5子（横向展开）
        { id: 'cp_ret_intent', x: 150, y: 950, w: 260, h: 70 },
        { id: 'cp_ret_base', x: 430, y: 950, w: 260, h: 70 },
        { id: 'cp_ret_enhance', x: 710, y: 950, w: 260, h: 70 },
        { id: 'cp_ret_fusion', x: 290, y: 1055, w: 260, h: 70 },
        { id: 'cp_ret_output', x: 590, y: 1055, w: 260, h: 70 },
        // V5 后处理严格顺序
        { id: 'cp_party', x: 510, y: 1180, w: 300, h: 75 },
        { id: 'cp_credit_check', x: 510, y: 1280, w: 300, h: 90 },
        { id: 'cp_agg', x: 510, y: 1400, w: 300, h: 75 },
        { id: 'cp_output', x: 510, y: 1500, w: 300, h: 75 },
        { id: 'cp_end', x: 560, y: 1595, w: 200, h: 60 }
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
        { from: 'cp_risk', to: 'cp_ret_intent', type: 'normal', label: '进入检索5子链路' },
        { from: 'cp_ret_intent', to: 'cp_ret_base', type: 'normal' },
        { from: 'cp_ret_base', to: 'cp_ret_enhance', type: 'branch', label: '<2条→触发L3' },
        { from: 'cp_ret_enhance', to: 'cp_ret_fusion', type: 'normal' },
        { from: 'cp_ret_fusion', to: 'cp_ret_output', type: 'normal' },
        // V5 后处理严格顺序
        { from: 'cp_ret_output', to: 'cp_party', type: 'normal', label: '识别甲乙名称' },
        { from: 'cp_party', to: 'cp_credit_check', type: 'normal', label: '🏛️资信查询' },
        { from: 'cp_credit_check', to: 'cp_agg', type: 'normal', label: '4路风险聚合' },
        { from: 'cp_agg', to: 'cp_output', type: 'normal' },
        { from: 'cp_output', to: 'cp_end', type: 'success' }
      ]
    };

    return CHARTS;
  }

  // ================================================================
  //  Step 3: 覆盖 D 中缺失的别名键（各专区新增节点也能正确弹窗）
  // ================================================================
  function patchAliases(D) {
    // ---- contract 专区新增节点别名 ----
    D['ct_credit_check'] = Object.assign({}, D['credit_check'] || {}, {
      t: '🏛️ credit_check 合同链路·资信查询（企查查 API）'
    });
    D['ct_party'] = Object.assign({}, D['ov_party'] || {
      t: '👥 party_identify 甲乙方识别（合同链路复用）', i: '👥', y: 'purple',
      f: '从合同文本中提取甲方、乙方的准确公司名称，写入 state["party_a"] / state["party_b"]。为后续 credit_check（企查查资信查询）提供准确的查询参数。'
    });
    D['ct_ret_intent'] = Object.assign({}, D['ret_intent_decompose'] || {});
    D['ct_ret_base'] = Object.assign({}, D['ret_base_layer'] || {});
    D['ct_ret_enhance'] = Object.assign({}, D['ret_enhance_query'] || {});
    D['ct_ret_fusion'] = Object.assign({}, D['ret_fusion_sort'] || {});
    D['ct_ret_output'] = Object.assign({}, D['ret_output'] || {});

    // ---- compliance 专区新增节点别名 ----
    D['cp_credit_check'] = Object.assign({}, D['credit_check'] || {}, {
      t: '🏛️ credit_check 合规链路·资信查询（企查查 API）'
    });
    D['cp_party'] = Object.assign({}, D['ov_party'] || {
      t: '👥 party_identify 甲乙方识别（合规链路复用）', i: '👥', y: 'purple',
      f: '从合同文本中提取甲方、乙方的准确名称，供后续企查查资信查询使用。合规审查链路中此节点同时记录"主体资质是否符合行业准入要求"的初步判断。'
    });
    D['cp_agg'] = Object.assign({}, D['ov_aggregate'] || {
      t: '📊 risk_aggregate 风险聚合（合规链路复用）', i: '📊', y: 'purple',
      f: '合并 4 路风险：合规风险+合同风险+数值风险+资信风险。合规风险优先级最高，不可被其他链路结论降级。'
    });
    D['cp_ret_intent'] = Object.assign({}, D['ret_intent_decompose'] || {});
    D['cp_ret_base'] = Object.assign({}, D['ret_base_layer'] || {});
    D['cp_ret_enhance'] = Object.assign({}, D['ret_enhance_query'] || {});
    D['cp_ret_fusion'] = Object.assign({}, D['ret_fusion_sort'] || {});
    D['cp_ret_output'] = Object.assign({}, D['ret_output'] || {});

    // ---- retrieval 专区新增节点别名 ----
    D['rt_party'] = Object.assign({}, D['ov_party'] || {
      t: '👥 party_identify 甲乙方识别（检索链路复用）', i: '👥', y: 'purple',
      f: '从用户查询或合同文本中提取甲乙方主体信息，为后续资信查询提供查询参数，也为 final_delivery 的"立场化答案"提供上下文。'
    });
    D['rt_aggregate'] = Object.assign({}, D['ov_aggregate'] || {
      t: '📊 risk_aggregate 风险聚合（检索链路复用）', i: '📊', y: 'purple',
      f: '合并检索结果中的风险项（如冲突法条、已废止法规），计算风险评分。虽然检索链路以"找法条"为主，但仍需要风险聚合来提示检索结果中是否存在法律冲突风险。'
    });
    D['rt_delivery'] = Object.assign({}, D['ov_delivery'] || {
      t: '📦 final_delivery 最终交付（检索链路复用）', i: '📦', y: 'purple',
      f: '组装结构化检索报告：相关法规（条文+出处）、相关案例（案号+裁判要旨）、风险提示（冲突法条）、法律建议。Markdown 格式输出。'
    });

    return D;
  }

  // ================================================================
  //  Step 4: SVG 增强绘制（横向挂载+纵向降级卡片 & 资信查询三级兜底标注）
  // ================================================================
  function addSvgEnhancements() {
    var SVGNS = 'http://www.w3.org/2000/svg';
    function el(tag, attrs, text) {
      var e = document.createElementNS(SVGNS, tag);
      if (attrs) for (var k in attrs) e.setAttribute(k, attrs[k]);
      if (text != null) e.textContent = text;
      return e;
    }

    // ---- 1) 为 retrieval 专区 加入横向挂载+纵向降级左右两栏卡片 ----
    var sRet = document.getElementById('svg-retrieval');
    if (sRet && !document.getElementById('_ret_side_enh_v5')) {
      var gAll = el('g', { id: '_ret_side_enh_v5', transform: 'translate(0,0)' });

      // 左侧卡片：📎 横向按需挂载 · 行业增强层（动态）
      var hiX = 50, hiY = 250;
      var hg = el('g', { transform: 'translate(' + hiX + ',' + hiY + ')' });
      hg.appendChild(el('rect', {
        x: 0, y: 0, width: 460, height: 420, rx: 18, ry: 18,
        fill: 'rgba(52,211,153,0.05)', stroke: 'rgba(52,211,153,0.6)',
        'stroke-dasharray': '7 5', 'stroke-width': '2'
      }));
      hg.appendChild(el('text', {
        x: 230, y: 42, 'text-anchor': 'middle',
        style: 'font-size:16px;font-weight:800;fill:#34d399;letter-spacing:1.5px;'
      }, '📎 横向按需挂载 · 行业增强层（动态挂载）'));
      hg.appendChild(el('text', {
        x: 230, y: 64, 'text-anchor': 'middle',
        style: 'font-size:12px;fill:#7a8a9a;'
      }, '根据 contract_type 动态加载行业特定数据源，通用+行业双重覆盖'));
      var indData = [
        ['🏗️ 建设工程合同', '→ 住建部标准 + 建筑法实施条例', '#34d399'],
        ['💰 金融借贷合同', '→ 银保监会监管规定 + 贷款通则', '#60c5fa'],
        ['👥 劳动合同', '→ 劳动法司法解释 + 社保缴纳规定', '#a78bfa'],
        ['📦 买卖合同', '→ 最高院买卖合同司法解释', '#fb923c'],
        ['🏠 租赁合同', '→ 城市房屋租赁管理办法', '#ec4899'],
        ['📄 未匹配类型', '→ 只跑通用数据源，不挂载行业增强', '#8a9aab']
      ];
      indData.forEach(function (it, i) {
        var yy = 88 + i * 56;
        hg.appendChild(el('rect', {
          x: 24, y: yy, width: 412, height: 48, rx: 12, ry: 12,
          fill: 'rgba(255,255,255,0.025)', stroke: it[2], 'stroke-opacity': '0.65', 'stroke-width': '1.3'
        }));
        var t1 = el('text', { x: 42, y: yy + 22,
          style: 'font-size:13.5px;font-weight:700;fill:' + it[2] + ';' });
        t1.textContent = it[0]; hg.appendChild(t1);
        var t2 = el('text', { x: 42, y: yy + 42,
          style: 'font-size:11.5px;fill:#9fb0c0;' });
        t2.textContent = it[1]; hg.appendChild(t2);
      });
      // 虚线连到 ov_ret_base
      var hCon = el('path', {
        d: 'M' + (hiX + 460) + ',' + (hiY + 210) +
           ' C' + (hiX + 530) + ',' + (hiY + 210) + ' ' + (600 - 60) + ',' + 322 + ' ' + 600 + ',' + 322,
        stroke: 'rgba(52,211,153,0.6)', 'stroke-width': '2.2', fill: 'none',
        'stroke-dasharray': '6 5'
      });
      hg.appendChild(hCon);
      gAll.appendChild(hg);

      // 右侧卡片：⬆️ 纵向逐级降级 · 三级兜底
      var vgX = 1040, vgY = 230;
      var vg = el('g', { transform: 'translate(' + vgX + ',' + vgY + ')' });
      vg.appendChild(el('rect', {
        x: 0, y: 0, width: 490, height: 480, rx: 18, ry: 18,
        fill: 'rgba(251,191,36,0.05)', stroke: 'rgba(251,191,36,0.6)',
        'stroke-dasharray': '7 5', 'stroke-width': '2'
      }));
      vg.appendChild(el('text', {
        x: 245, y: 42, 'text-anchor': 'middle',
        style: 'font-size:16px;font-weight:800;fill:#fcd34d;letter-spacing:1.5px;'
      }, '⬆️ 纵向逐级降级 · 三级兜底策略'));
      vg.appendChild(el('text', {
        x: 245, y: 64, 'text-anchor': 'middle',
        style: 'font-size:12px;fill:#7a8a9a;'
      }, 'L1优先→L2不足时降级→L3极端最后防线，保证系统永不崩溃'));
      var vLvls = [
        ['L1 · 高精度优先（默认执行）', 'FAISS 向量检索 + 知识图谱三元组', '权威结构化法条 · bge-m3 + Milvus', '#34d399'],
        ['↓ 命中不足 3 条 → 自动降级', '', '', '#8a9aab'],
        ['L2 · 关键词兜底', '本地 /data/laws/ 法规txt目录 · 第X条正则匹配', '扫描式兜底 · 覆盖面广 · 不依赖外部服务', '#60c5fa'],
        ['↓ 仍不足 2 条 → 下游继续兜底', '', '', '#8a9aab'],
        ['L3 · LLM 伪检索（最后防线）', 'retrieval_enhance_query 节点调用 LLM 生成', '极端场景防死循环 · 标注"伪检索"防幻觉误导', '#f9a8d4']
      ];
      var yyV = 78;
      vLvls.forEach(function (lv, i) {
        if (i % 2) {
          yyV += 28;
          var ta = el('text', { x: 245, y: yyV, 'text-anchor': 'middle',
            style: 'font-size:12.5px;font-weight:700;fill:' + lv[3] + ';' });
          ta.textContent = lv[0]; vg.appendChild(ta);
          return;
        }
        yyV += 58;
        vg.appendChild(el('rect', {
          x: 24, y: yyV - 36, width: 442, height: 62, rx: 14, ry: 14,
          fill: 'rgba(255,255,255,0.025)', stroke: lv[3], 'stroke-opacity': '0.65', 'stroke-width': '1.3'
        }));
        var t1 = el('text', { x: 40, y: yyV - 12,
          style: 'font-size:13.5px;font-weight:700;fill:' + lv[3] + ';' });
        t1.textContent = lv[0]; vg.appendChild(t1);
        var t2 = el('text', { x: 40, y: yyV + 10,
          style: 'font-size:11.5px;fill:#b0c0d0;' });
        t2.textContent = lv[1]; vg.appendChild(t2);
        var t3 = el('text', { x: 40, y: yyV + 28,
          style: 'font-size:10.5px;fill:#8a9aab;' });
        t3.textContent = lv[2]; vg.appendChild(t3);
      });
      // 连线到 ov_ret_base
      var vCon = el('path', {
        d: 'M' + (980) + ',' + (vgY + 240) + ' L' + vgX + ',' + (vgY + 240),
        stroke: 'rgba(251,191,36,0.6)', 'stroke-width': '2.2', fill: 'none',
        'stroke-dasharray': '6 5'
      });
      gAll.appendChild(vCon);
      gAll.appendChild(vg);

      sRet.insertBefore(gAll, sRet.firstChild);
    }

    // ---- 2) 为 overview 专区加入资信查询三级兜底提示卡 ----
    var sOv = document.getElementById('svg-overview');
    if (sOv && !document.getElementById('_ov_credit_hint_v5')) {
      var cg = el('g', { id: '_ov_credit_hint_v5', transform: 'translate(1020, 1255)' });
      cg.appendChild(el('rect', {
        x: 0, y: 0, width: 440, height: 200, rx: 18, ry: 18,
        fill: 'rgba(251,146,60,0.05)', stroke: 'rgba(251,146,60,0.6)',
        'stroke-dasharray': '7 5', 'stroke-width': '2'
      }));
      cg.appendChild(el('text', {
        x: 220, y: 38, 'text-anchor': 'middle',
        style: 'font-size:15.5px;font-weight:800;fill:#fdba74;letter-spacing:1.3px;'
      }, '🏛️ credit_check 三级兜底机制'));
      cg.appendChild(el('text', {
        x: 220, y: 58, 'text-anchor': 'middle',
        style: 'font-size:11.5px;fill:#7a8a9a;'
      }, '每级失败自动 fallback，保证主流程永不中断 · 模拟数据标注避免误导'));
      var tier = [
        ['Tier ①  MCP Bearer Token', 'SSE 流接口 · 信息最全面 · 优先调用', '#34d399'],
        ['Tier ②  AppKey + MD5 签名', 'HTTP JSON · 备用备用调用 · 独立通道', '#60c5fa'],
        ['Tier ③  Mock 模拟数据', '极端兜底 · 100% 不抛异常 · 标注质量下降', '#fb923c']
      ];
      tier.forEach(function (t, i) {
        var y = 78 + i * 40;
        cg.appendChild(el('rect', {
          x: 20, y: y, width: 400, height: 36, rx: 10, ry: 10,
          fill: 'rgba(255,255,255,0.025)', stroke: t[2], 'stroke-opacity': '0.6', 'stroke-width': '1.3'
        }));
        var t1 = el('text', { x: 34, y: y + 23,
          style: 'font-size:12.5px;font-weight:700;fill:' + t[2] + ';' });
        t1.textContent = t[0]; cg.appendChild(t1);
        var t2 = el('text', { x: 250, y: y + 23,
          style: 'font-size:11px;fill:#a0b0c0;' });
        t2.textContent = t[1]; cg.appendChild(t2);
      });
      // 虚线连到 credit_check 节点
      var cLink = el('path', {
        d: 'M 0,100 L -60,100',
        stroke: 'rgba(251,146,60,0.7)', 'stroke-width': '2.2', fill: 'none',
        'stroke-dasharray': '6 5'
      });
      cg.appendChild(cLink);
      sOv.insertBefore(cg, sOv.firstChild);
    }

    // ---- 3) 为 overview 专区加入 矩阵式检索策略 标注卡 ----
    if (sOv && !document.getElementById('_ov_matrix_hint_v5')) {
      var mg = el('g', { id: '_ov_matrix_hint_v5', transform: 'translate(140, 915)' });
      mg.appendChild(el('rect', {
        x: 0, y: 0, width: 300, height: 210, rx: 16, ry: 16,
        fill: 'rgba(96,197,250,0.05)', stroke: 'rgba(96,197,250,0.55)',
        'stroke-dasharray': '6 5', 'stroke-width': '1.8'
      }));
      mg.appendChild(el('text', {
        x: 150, y: 36, 'text-anchor': 'middle',
        style: 'font-size:14.5px;font-weight:800;fill:#60c5fa;letter-spacing:1.2px;'
      }, '🔷 矩阵式检索策略'));
      var items = [
        ['横向 × 纵向 = 二维鲁棒性', '#60c5fa'],
        ['横向：通用源 + 行业增强源', '#34d399'],
        ['纵向：L1 → L2 → L3 三级降级', '#fcd34d'],
        ['任意单源失效 ≠ 系统崩溃', '#fb923c'],
        ['优雅降级：跑通 → 标注质量下降', '#a78bfa']
      ];
      items.forEach(function (it, i) {
        var yy = 66 + i * 28;
        var t = el('text', { x: 20, y: yy,
          style: 'font-size:12px;fill:' + it[1] + ';' });
        t.textContent = '• ' + it[0];
        mg.appendChild(t);
      });
      sOv.insertBefore(mg, sOv.firstChild);
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

      // Step B: 覆盖 CHARTS 配置（节点位置与边）→ 清旧SVG → 重绘
      if (typeof window.CHARTS !== 'undefined' && typeof window.drawChart === 'function') {
        var newCharts = patchCharts(window.CHARTS);
        window.CHARTS = newCharts;

        Object.keys(newCharts).forEach(function (key) {
          try {
            // 清空旧 SVG 内容后重新绘制
            var chartCfg = newCharts[key];
            var svgEl = document.getElementById(chartCfg.svgId);
            if (svgEl) {
              while (svgEl.firstChild) svgEl.removeChild(svgEl.firstChild);
            }
            window.drawChart(key);
          } catch (e) {
            if (window.console && console.error) console.error('[flowchart_v5_final] drawChart error for', key, e);
          }
        });
      }

      // Step C: 追加 SVG 可视化增强卡片（等重绘完成后延时）
      setTimeout(addSvgEnhancements, 200);

      if (window.console && console.log) {
        console.log('%c⚖️ 法智引擎 V5 最终增强已加载',
          'color:#60c5fa;font-weight:800;font-size:14px;',
          '\n✅ 已严格对齐 langgraph_main.py 节点编排（add_node / add_edge 顺序）\n' +
          '✅ 已放大节点尺寸（W=260/H=75+）彻底杜绝文字覆盖\n' +
          '✅ 已修正后处理链路: retrieval_output → party_identify → credit_check → risk_aggregate\n' +
          '✅ 已新增 credit_check 资信查询节点（企查查三级兜底机制）\n' +
          '✅ 已补充检索5子节点弹窗详情（功能说明/设计思考/技术选型/优化建议/面试Q&A）\n' +
          '✅ 已加入横向挂载+纵向降级可视化卡片（retrieval专区左右栏）\n' +
          '✅ 已加入资信查询三级兜底标注卡 + 矩阵式检索策略卡（overview专区）\n\n' +
          '💡 点击任意节点查看深度解析（含面试官视角高频提问与回答思路）');
      }
    } catch (err) {
      if (window.console && console.error) console.error('[flowchart_v5_final] error:', err);
    }
  }

  // 等原来的 DOMContentLoaded 初始化跑完后再执行
  document.addEventListener('DOMContentLoaded', function () {
    setTimeout(boot, 450);
  });

  // 兜底：如果 DOMContentLoaded 已经触发过
  if (document.readyState === 'complete' || document.readyState === 'interactive') {
    setTimeout(boot, 600);
  }
})();
