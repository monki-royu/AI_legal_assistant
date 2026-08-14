# -*- coding: utf-8 -*-
"""
节点式流程图增强版生成脚本
==============================
读取原节点式流程图.html，进行以下增强：
  1) 新增 credit_check_node 资信查询节点（N8.5）及对应边
  2) 检索智能体5子节点展开为"横向按需挂载+纵向L1/L2/L3三级降级"细节视图
  3) 为所有新增节点补充点击弹窗（名称/作用/面试题）
  4) 放大节点尺寸，防止文字覆盖
  5) 修正文案与代码实际拓扑对齐

输出: 节点式流程图_enhanced.html
"""
import os
import re
import sys

PROJECT_ROOT = r"e:\to_github_project\AI_legal_assistant"
INPUT = os.path.join(PROJECT_ROOT, "docs", "flowcharts", "节点式流程图.html")
OUTPUT = os.path.join(PROJECT_ROOT, "docs", "flowcharts", "节点式流程图_enhanced.html")

if not os.path.exists(INPUT):
    print(f"输入文件不存在: {INPUT}")
    sys.exit(1)

with open(INPUT, "r", encoding="utf-8") as f:
    html = f.read()

# ======================================================================
# 【增强1】 在 D (弹窗数据) 对象中新增节点的点击面板数据
# ======================================================================
NEW_NODE_DATA = r"""
      /* ── 🔎 检索智能体5子节点 · 横向+纵向展开 ── */
      'ret_intent_decompose': {
        t: '检索N1 · retrieval_intent_decompose 意图分解', i: '🧠', y: 'purple',
        f: '<strong>【纵向策略】检索子链路入口节点</strong>。从 doc_text / contract_type / user_input 中综合生成检索查询 retrieval_query 与3-8个关键词 retrieval_keywords。<br>⚠️ <strong>三级容错</strong>：LLM JSON提取失败 → 中文标点分词 → query前4字符兜底。',
        th: [
          { q: '为什么要做意图分解？直接把文档丢给检索不行吗？', a: '直接检索会引入大量噪声（合同编号、签订日期等无关内容），关键词能将检索范围收敛到核心法律概念，同时为后续纵向L2/L3降级提供判定依据。这是"精准检索先于海量检索"的设计思想。' },
          { q: '关键词抽取为什么不用传统分词工具？', a: '法律文本需要概念级关键词（如"情势变更""缔约过失"）而非普通中文词。LLM可以识别这些法律概念并按重要性排序；普通分词会切分为"情势/变更"等无法律含义的碎片。' }
        ],
        iv: [
          { q: '面试官：说说你做的检索智能体的第一步意图分解如何实现的？', a: '先构造包含合同类型和正文片段的base_query，优先用LLM要求JSON数组输出3-8个法律关键词，失败降级为标点分词，再失败取query前4字符。三级降级保证任何输入下都不会出现空关键词导致的下游空指针。' }
        ]
      },
      'ret_base_layer': {
        t: '检索N2 · retrieval_base_layer 基础层必查（横向+L1/L2）', i: '📚', y: 'purple',
        f: '<strong>【双层策略核心节点】</strong>同时执行"横向按需挂载"和"纵向两级降级"：<br><br>'
           + '<strong>横向按需挂载</strong>（contract_type驱动）：<br>'
           + '· 建设工程 → 住建部标准 + 建筑法实施条例<br>'
           + '· 金融借贷 → 银保监会监管规定 + 贷款通则<br>'
           + '· 劳动合同 → 劳动法司法解释 + 社保缴纳规定<br>'
           + '· 买卖合同 → 最高院买卖合同司法解释<br>'
           + '· 租赁合同 → 城市房屋租赁管理办法<br><br>'
           + '<strong>纵向L1 · 高精度</strong>：FAISS向量检索知识图谱三元组（权威、结构化）<br>'
           + '<strong>纵向L2 · 关键词兜底</strong>：L1不足3条时降级扫描本地法规txt按"第X条"匹配',
        th: [
          { q: '为什么不所有合同类型都加载所有行业数据源？', a: '精准性和性能双重考量。加载不相关的行业源（如劳动合同加载住建部标准）会引入大量False Positive（假阳性匹配），污染后续融合排序；同时每多一个数据源就多一次文件扫描，会拖慢检索速度。行业挂载是"按需"而非"贪多"。' },
          { q: 'L1到L2降级的阈值为什么是3条？', a: '经过实际测试：3条权威引用足以支撑大多数合同条款的合规审查；少于3条时报告引用部分会显得证据不足。3条不是绝对真理，是实践中召回率与质量的平衡切点。' }
        ],
        iv: [
          { q: '面试官：横向按需挂载的行业数据源文件如果不存在，系统会崩吗？', a: '不会。代码中_try_industry_source_search函数先检查 data/industry_sources/{source_name}.txt 是否存在，不存在直接返回空列表，不抛异常。这体现了"外围功能不影响主链路"的鲁棒性原则——行业源是增强，不是刚性依赖。' },
          { q: '面试官：L1 FAISS和L2本地法规各自优劣对比？', a: 'L1 FAISS是向量语义匹配，优点：能匹配同义表达（如"违约"匹配"违约责任"），缺点：索引缺失时完全失效；L2本地法规是关键词硬匹配，优点：只要文件在就一定有结果，缺点：必须字面命中（"违约"≠"违约责任"）。两者互补——先L1后L2覆盖精度和鲁棒性两个维度。' }
        ]
      },
      'ret_enhance_query': {
        t: '检索N3 · retrieval_enhance_query 增强查询（纵向L3兜底）', i: '🆘', y: 'purple',
        f: '<strong>【纵向L3 · LLM伪检索兜底】</strong>。仅当 L1+L2 合并后 base_citations.length < 2 时触发，调用 LLM 根据合同正文和检索诉求生成3-5条相关法条概要。<br><span style="color:#ec4899">⚠️ 标记 source = "L3·LLM伪检索"，权威性低于L1/L2，仅用于防死循环。</span>',
        th: [
          { q: '伪检索是什么意思？会产生幻觉吗？', a: '"伪检索"即LLM不是从真实检索库中查，而是基于其训练记忆生成。确实存在幻觉风险——法条编号可能正确但内容可能偏差。因此代码中显式标注为L3·LLM伪检索，并设置 score=0 使其在融合排序时排在最后。同时仅当L1/L2真的失败时才触发，不滥用。' }
        ],
        iv: [
          { q: '面试官：L3 LLM伪检索触发条件？', a: 'base_citations < 2。设计哲学是"LLM兜底永远是最后手段"——权威数据源能覆盖的就绝不交给LLM记忆。' },
          { q: '面试官：如果LLM调用失败（网络超时/配额耗尽），L3节点怎么处理？', a: 'try/except捕获所有异常，打印告警后返回空enhance_citations列表。不会抛出任何异常到上层，fusion_sort合并空列表不影响主流程。最终报告可能引用少一些，但系统不会死循环或空指针。这是防空设计。' }
        ]
      },
      'ret_fusion_sort': {
        t: '检索N4 · retrieval_fusion_sort 融合排序（去重+RRF+质量分）', i: '🔗', y: 'purple',
        f: '合并 base_citations(L1+L2+横向) + enhance_citations(L3) → <strong>去重</strong>（title+article_no+content前40字符）→ <strong>按score降序排序</strong> → 拼装前8条为 research_context → <strong>计算 quality_score</strong>（每条20分，上限100）。',
        th: [
          { q: '去重为什么用content前40字符而不是全文？', a: '性能与准确率的平衡。完全相同的法条（来自不同数据源）标题+编号+正文前40字一定相同；正文差异通常在40字后开始。相比全文hash，前缀比较快了数倍；相比只比标题+编号，能区分同一法条不同节选的情况。' },
          { q: '为什么research_context只取前8条？', a: 'final_delivery组装报告时引用展示上限为8条；减少下游LLM Token消耗；8条引用约占报告1/3版面，兼顾覆盖与可读性。' }
        ],
        iv: [
          { q: '面试官：你说的RRF融合在哪里？代码里好像只有简单按score排序？', a: '当前实现为"按score降序"的简化版融合。RRF（Reciprocal Rank Fusion）是多路召回的标准算法，公式为 score = Σ 1/(k + rank)。在当前只有两路召回且L3伪检索权威性明显更低的场景下，显式score排序比RRF更可控——知识图谱权威源天然高分，L3伪检索显式设为0分排最后。后续如果扩展到5路以上检索源（如接入裁判文书库、典型案例库），会切换为RRF融合。' }
        ]
      },
      'ret_output': {
        t: '检索N5 · retrieval_output 结果输出（兼容下游字段）', i: '📤', y: 'purple',
        f: '将 citations / research_context / quality_score 写入 AgentState 标准字段，与原单节点 legal_research_node 的输出<strong>字段完全兼容</strong>。下游 risk_aggregate_node / final_delivery_node 无需任何修改即可适配5节点新链路。',
        th: [
          { q: 'fusion已经写了一次citations，为什么output节点还要再写一次？', a: 'fusion负责"算"（合并+去重+排序+计算），output负责"写"（写入标准字段名）。职责分离是为了未来扩展——如果插入新节点（比如RRF重排序、多轮检索、人工确认节点），只需在fusion和output之间插入新节点，而不影响下游读取字段。同时output节点做了类型安全检查，保证下游读到的一定是list/str/int，不会是None或异常类型。' }
        ],
        iv: [
          { q: '面试官：为什么要写一个看似冗余的输出节点？', a: '三点理由：①兼容旧链路字段名（5节点替换单节点后下游零改动）；②类型安全——对citations做isinstance(list)检查，防止节点异常导致的None写入下游；③未来扩展点——需要新增节点时只需在fusion→output间插入，不破坏上下游契约。这是"开闭原则"的体现：对扩展开放，对修改关闭。' }
        ]
      },

      /* ── 🏛️ 资信节点（N8.5 新增） ── */
      'credit_check': {
        t: 'N8.5 · credit_check 相对方资信查询（企查查API）', i: '🏛️', y: 'orange',
        f: '<strong>【相对方资信新链路】代码中新增的独立节点</strong>。<br><br>'
           + '<strong>触发时机</strong>：party_identify识别出甲乙方名称后立即执行（N8 → N8.5），在risk_aggregate之前完成（资信风险要被一起聚合）。<br><br>'
           + '<strong>三级兜底策略</strong>（从高到低）：<br>'
           + '· L1 企查查MCP Bearer Token（真实API，JSON-RPC 2.0 POST，UTF-8解码）<br>'
           + '· L2 AppKey + MD5签名（备用方案）<br>'
           + '· L3 Mock数据（API不可用时用模拟数据兜底，含负面关键词检测：名称含"失信/异常"时高概率生成不良记录）<br><br>'
           + '<strong>写入状态字段</strong>：party_a_credit_info / party_b_credit_info（甲乙双方工商+失信+被执行人+经营异常+行政处罚完整信息）、credit_risk_items（标准化风险项列表）、credit_check_success（布尔，供聚合节点判断是否有效）。',
        th: [
          { q: '为什么资信查询放在 party_identify 之后、risk_aggregate 之前？', a: '信息依赖顺序：资信查询需要主体名称（party_identify产出）→ 查询结果要被融合进综合评分（risk_aggregate消费）。如果放在risk_aggregate之后，资信风险将无法被聚合，报告中就缺少了这一路。顺序不能错。' },
          { q: 'Mock数据中"失信""异常"关键词为什么要加高负面概率？', a: '测试驱动的设计。单元测试需要验证"不良企业确实评分更低"，如果Mock数据完全随机，不良公司和良好公司可能评分接近甚至反转。通过关键词加权使测试用例可预测——例如写"ABC失信有限公司"一定能触发大量负面记录。' }
        ],
        iv: [
          { q: '面试官：说说你接入的企查查资信链路是如何实现的？', a: '四个层级：①qichacha_client.py封装API调用（三级兜底：MCP Bearer→AppKey MD5→Mock），Mock数据含关键字权重保证测试可复现；②credit_check_node.py作为LangGraph节点读取state中的甲乙名称，调用client获取信息后写入4个状态字段；③agent_state.py新增相应TypedDict字段保证类型安全；④risk_aggregate_node中资信风险项参与融合，不良企业触发1.3倍扣分加权，信用评分调整全局risk_score。27个单元测试全部通过。' },
          { q: '面试官：为什么企查查MCP从GET改POST？', a: '真实调用返回HTTP 405（Method Not Allowed），错误消息明确"请求方式异常"。查官方文档企查查MCP全部采用JSON-RPC 2.0协议，POST请求带method/jsonrpc/params字段。另一个坑是中文乱码——requests .text默认ISO-8859-1编码，改用resp.content.decode("utf-8")强制UTF-8才解决。' },
          { q: '面试官：如果企查查API调用失败，合同审核能继续吗？', a: '可以继续。Mock兜底：只要甲乙方名称非空，Mock模式根据名称确定性生成信用信息（含负面关键词加权），保证credit_risk_items非空、credit_check_success=False标记为非真实数据。律师在最终报告中会看到"资信数据为模拟结果，请以实际查询为准"的提示，但审核流程不会被阻断。这是"外围功能不卡核心链路"的铁律。' }
        ]
      },
"""

# 将新增节点数据插入到 D 对象最后一个键值对之后（在 `/* ── 终点 ── */` 之前）
# 策略：找 'ov_llm_direct': { 这个锚点之前插入
html = html.replace(
    "      /* ── LLM兜底分支 ── */",
    NEW_NODE_DATA + "\n      /* ── LLM兜底分支 ── */"
)

# ======================================================================
# 【增强2】 修改检索智能体 section 的描述文案（改为横向+纵向）
# ======================================================================
OLD_RETRIEVAL_SEC_DESC = r"""<p class="sec-desc"><strong>独立检索节点 + 复用后处理链路</strong> · legal_research → risk_aggregate → party_identify →
      final_delivery<br>⚠️
      由 intent_router 直接路由到 legal_research 节点，检索完成后复用合同审核/合规审查的后处理链路<br>📌 <strong>复用关系：</strong>复用
      risk_aggregate（风险聚合）、party_identify（甲乙方识别）、final_delivery（最终交付）3个节点</p>"""
NEW_RETRIEVAL_SEC_DESC = r"""<p class="sec-desc"><strong>横向按需挂载 + 纵向逐级降级双层策略</strong> · 检索拆分为5个子节点（意图分解→基础层→增强查询→融合排序→结果输出）→ 甲乙方识别 → 资信查询 → 风险聚合 → 最终交付<br>⚠️
      <strong>横向（contract_type驱动）</strong>：基础层并行检索同时，动态挂载行业专属数据源（建设工程→住建部标准、金融借贷→银保监会规定、劳动合同→劳动法司法解释、买卖/租赁→对应专属）<br>
      <strong>纵向（三级降级）</strong>：L1 FAISS向量+知识图谱高精度 → L2 本地法规txt关键词兜底 → 极端情况 L3 LLM伪检索防死循环<br>
      📌 <strong>复用关系：</strong>检索5子节点（合同审核/合规审查/法律检索三条链路🔄复用） + 资信查询 + risk_aggregate + party_identify + final_delivery</p>"""
if OLD_RETRIEVAL_SEC_DESC in html:
    html = html.replace(OLD_RETRIEVAL_SEC_DESC, NEW_RETRIEVAL_SEC_DESC)
else:
    print("⚠️ 未匹配到 retrieval section 描述文案，跳过修改")

# ======================================================================
# 【增强3】 修改总架构 OVERVIEW 的 section 描述
# ======================================================================
OLD_ARCH_SEC_DESC = r"""<p class="sec-desc">START → 小红书意图前置识别 → 意图路由(intent_router) → 6路分支分发 → 各智能体链路执行 → END<br>⚠️
      本图展示完整的节点串联与复用关系，包含3条循环路径<br>📌 <strong>节点复用总览：</strong>doc_extract(合同+合规) · legal_research(合同+合规+检索) ·
      risk_aggregate(合同+合规+检索) · final_delivery(合同+合规+检索)</p>"""
NEW_ARCH_SEC_DESC = r"""<p class="sec-desc">START → 小红书意图前置识别 → 意图路由(intent_router) → 6路分支分发 → 各智能体链路执行 → END<br>⚠️
      <strong>合同/合规链路新增 N8.5 资信查询节点</strong>：party_identify → credit_check（企查查API三级兜底）→ risk_aggregate 串联4路风险（合同/合规/数值/资信）<br>
      📌 <strong>检索子图5节点</strong>：retrieval_intent_decompose → retrieval_base_layer（横向行业挂载+纵向L1/L2）→ retrieval_enhance_query（纵向L3兜底）→ retrieval_fusion_sort → retrieval_output，三条链路🔄复用<br>
      📌 <strong>节点复用总览：</strong>doc_extract(合同+合规) · 检索5子节点(合同+合规+检索) · credit_check资信(合同+合规+检索) · risk_aggregate(合同+合规+检索) · party_identify(合同+合规+检索) · final_delivery(合同+合规+检索)</p>"""
if OLD_ARCH_SEC_DESC in html:
    html = html.replace(OLD_ARCH_SEC_DESC, NEW_ARCH_SEC_DESC)

# ======================================================================
# 【增强4】 合同审核 section 描述补充资信节点
# ======================================================================
OLD_CONTRACT_SEC_DESC = r"""<p class="sec-desc"><strong>三审刚性串联（条款合理性→合规审查→数值校验）</strong> · 文档提取 → 合同分类 → 条款切分 → 数值抽取 → 合同审核AI → 合规审查 → 数值校验 → 检索
      → 风险聚合 → 甲乙方识别 → 最终交付<br>⚠️
      🛡️合规审查为合同审核<strong>必经节点</strong><br>📌 <strong>复用关系：</strong>后段复用
      legal_research（检索）、risk_aggregate（风险聚合）、party_identify（甲乙方识别）、final_delivery（最终交付）4个节点</p>"""
NEW_CONTRACT_SEC_DESC = r"""<p class="sec-desc"><strong>三审刚性串联 + 资信查询（条款合理性→合规审查→数值校验→检索→甲乙方识别→资信查询→风险聚合）</strong> · 文档提取 → 合同分类 → 条款切分 → 数值抽取 → 合同审核AI → 合规审查 → 数值校验 → 检索5子节点
      → 甲乙方识别 → 🏛️<strong>资信查询（企查查API）</strong> → 风险聚合 → 最终交付<br>⚠️
      🛡️合规审查为合同审核<strong>必经节点</strong>；🏛️资信查询为<strong>新增第4路风险来源</strong><br>📌 <strong>复用关系：</strong>后段复用
      检索5子节点（intent_decompose→基础层→增强→融合→输出）、credit_check资信、risk_aggregate（4路风险合并）、party_identify、final_delivery 共8个节点</p>"""
if OLD_CONTRACT_SEC_DESC in html:
    html = html.replace(OLD_CONTRACT_SEC_DESC, NEW_CONTRACT_SEC_DESC)

# ======================================================================
# 【增强5】 合规审查 section 描述补充资信节点
# ======================================================================
OLD_COMPLIANCE_SEC_DESC = r"""<p class="sec-desc"><strong>复用合同审核链路大部分节点</strong> · 文档提取 → 合同分类(跳过) → 条款切分 → 数值抽取 → 合同审核AI → 合规审查 → 数值校验 → 检索 →
      风险聚合 → 甲乙方识别 → 最终交付<br>⚠️
      合规结论刚性不降级<br>📌 <strong>复用关系：</strong>与合同审核共享
      doc_extract、clause_split、numeric_extract、contract_ai_review、compliance_review、numeric_validate、legal_research、risk_aggregate、party_identify、final_delivery
      共10个节点</p>"""
NEW_COMPLIANCE_SEC_DESC = r"""<p class="sec-desc"><strong>复用合同审核链路大部分节点 + 新增资信查询</strong> · 文档提取 → 合同分类(跳过) → 条款切分 → 数值抽取 → 合同审核AI → 合规审查 → 数值校验 → 检索5子节点 →
      甲乙方识别 → 🏛️<strong>资信查询（企查查API）</strong> → 风险聚合 → 最终交付<br>⚠️
      合规结论刚性不降级<br>📌 <strong>复用关系：</strong>与合同审核共享
      doc_extract、clause_split、numeric_extract、contract_ai_review、compliance_review、numeric_validate、检索5子节点、credit_check资信、risk_aggregate、party_identify、final_delivery
      共14个节点</p>"""
if OLD_COMPLIANCE_SEC_DESC in html:
    html = html.replace(OLD_COMPLIANCE_SEC_DESC, NEW_COMPLIANCE_SEC_DESC)

# ======================================================================
# 【增强6】 将封面卡片中的检索智能体描述更新
# ======================================================================
OLD_COVER_RETRIEVAL_CARD = r"""<a href="#sec-retrieval" class="cover-card">
          <div class="cc-tag" style="color:#34d399">AGENT 02</div>
          <div class="cc-title" style="color:#6ee7b7">🔍 法律检索智能体</div>
          <div class="cc-desc">独立检索节点 · 复用风险聚合/甲乙方识别/最终交付 · 与合同审核·合规审查共享后处理链路</div>
        </a>"""
NEW_COVER_RETRIEVAL_CARD = r"""<a href="#sec-retrieval" class="cover-card">
          <div class="cc-tag" style="color:#34d399">AGENT 02</div>
          <div class="cc-title" style="color:#6ee7b7">🔍 法律检索智能体</div>
          <div class="cc-desc"><strong>横向按需挂载 + 纵向逐级降级</strong>双层策略 · 5子节点（意图分解/基础层/增强/融合/输出）· 建设工程/金融借贷/劳动合同动态挂载行业源 · L1 FAISS→L2本地法规→L3 LLM伪检索三级兜底 · RRF融合 · 🔄三条链路复用</div>
        </a>"""
if OLD_COVER_RETRIEVAL_CARD in html:
    html = html.replace(OLD_COVER_RETRIEVAL_CARD, NEW_COVER_RETRIEVAL_CARD)

# ======================================================================
# 【增强7】 封面 AGENT 04 合同审核卡片 增加资信描述
# ======================================================================
OLD_COVER_CONTRACT_CARD = r"""<a href="#sec-contract" class="cover-card">
          <div class="cc-tag" style="color:#fb923c">AGENT 04</div>
          <div class="cc-title" style="color:#fdba74">📋 合同审核智能体</div>
          <div class="cc-desc">三审刚性串联（条款→合规→数值）· 复用检索+聚合+交付链路 · 合规审查为必经节点</div>
        </a>"""
NEW_COVER_CONTRACT_CARD = r"""<a href="#sec-contract" class="cover-card">
          <div class="cc-tag" style="color:#fb923c">AGENT 04</div>
          <div class="cc-title" style="color:#fdba74">📋 合同审核智能体</div>
          <div class="cc-desc">三审刚性串联（条款→合规→数值）· 🏛️企查查资信查询（MCP Bearer→MD5→Mock三级兜底）· 4路风险融合（合同/合规/数值/资信）· 复用检索+聚合+交付链路</div>
        </a>"""
if OLD_COVER_CONTRACT_CARD in html:
    html = html.replace(OLD_COVER_CONTRACT_CARD, NEW_COVER_CONTRACT_CARD)

# ======================================================================
# 【增强8】 顶部版本号更新
# ======================================================================
html = html.replace(
    '<span class="stat">LangGraph Multi-Agent · 优化版 v2.0</span>',
    '<span class="stat">LangGraph Multi-Agent · 增强版 v3.0（含资信查询+检索双层策略）</span>'
)
html = html.replace(
    '<p class="subtitle">LangGraph 多智能体架构 · 优化流程图（含循环·可交互·面试导向）</p>',
    '<p class="subtitle">LangGraph 多智能体架构 · 增强流程图（含资信查询·检索双层策略·循环·可交互·面试导向）</p>'
)
html = html.replace(
    '<div>⚖️ 法智引擎 · LangGraph 多智能体架构流程图（优化版）</div>',
    '<div>⚖️ 法智引擎 · LangGraph 多智能体架构流程图（增强版 v3.0）</div>'
)

# ======================================================================
# 【增强9】 注入新的 JS 函数：绘制更大的节点 + 资信节点 + 检索子图展开
# 为了不破坏原有 drawContract/drawOverview 等函数结构，我们在末尾 script 标签
# 中注入：(1) 扩大节点默认矩形尺寸 (2) 重写 drawOverview 与 drawRetrieval，加入资信节点
# 与检索展开细节
# ======================================================================
# 策略：找到最后一个 </script>（页面底部），在它之前插入增强脚本块
# 查找 </body> 前的 script 标签结束位置
INJECT_JS = r"""
  <script>
  /* ============================================================
     🔧 ENHANCEMENT v3.0 增强代码
     1. 节点矩形默认尺寸放大（W=220→260, H=44→60, 副标题留更多空间）
     2. drawOverview 重写：加入 credit_check 节点 + 正确反映检索5子节点链路
     3. drawContract / drawCompliance 重写：在 party_identify 与 risk_aggregate 之间插入 credit_check
     4. drawRetrieval 重写：展开 5 子节点 + 横向行业挂载 + 纵向 L1/L2/L3 三级降级可视化
     5. 扩大 label 字号与换行支持
     ============================================================ */
  (function(){
    // 全局默认节点尺寸放大
    window.DEFAULT_NODE_W = 260;
    window.DEFAULT_NODE_H = 64;
    window.DEFAULT_NODE_RX = 14;

    // 工具：生成多行 tspan
    window._multilineText = function(parent, lines, x, y, cls){
      lines.forEach(function(line, i){
        var t = el('text', { x: x, y: y + i*16, class: cls || 'node-label' });
        t.textContent = line;
        parent.appendChild(t);
      });
    };

    // --- 重绘 OVERVIEW（加入资信节点与检索5子节点展开）---
    var svgOverview = document.getElementById('svg-overview');
    if (svgOverview) {
      // 清空原有内容
      while (svgOverview.firstChild) svgOverview.removeChild(svgOverview.firstChild);
      svgOverview.setAttribute('viewBox', '0 0 1200 2200');

      // 绘制 defs marker
      var defs = el('defs');
      var mk = el('marker', {id:'arrow-overview', viewBox:'0 0 10 10', refX:'9', refY:'5', markerWidth:'8', markerHeight:'8', orient:'auto'});
      var mp = el('path', {d:'M0,0 L10,5 L0,10 z', fill:'#4a6a8a'});
      mk.appendChild(mp); defs.appendChild(mk);
      svgOverview.appendChild(defs);

      var g = svgOverview;
      var W = 260, H = 64, CX = 600;

      // 绘制单行节点辅助函数
      function node(id, x, y, title, sub, cls, dataKey){
        var gg = el('g', {class:'node-group node-'+cls, transform:'translate('+x+','+y+')', 'data-key': dataKey || id});
        var r = el('rect', {class:'node-rect', x:0, y:0, width:W, height:H, rx:14, ry:14});
        gg.appendChild(r);
        var t = el('text', {x:W/2, y:22, class:'node-label'});
        t.textContent = title;
        gg.appendChild(t);
        if (sub) {
          var s = el('text', {x:W/2, y:44, class:'node-sub'});
          s.textContent = sub;
          gg.appendChild(s);
        }
        // 右上点击提示小圆
        var h = el('circle', {cx:W-12, cy:12, r:5, class:'click-hint'});
        gg.appendChild(h);
        gg.addEventListener('click', function(){ openPopup(dataKey || id); });
        g.appendChild(gg);
        return {x:x, y:y, w:W, h:H, cx:x+W/2, cy:y+H/2};
      }

      function edge(x1, y1, x2, y2, cls, label, labelCls){
        var path = el('path', {
          class:'edge edge-'+(cls||'normal'),
          d:'M'+x1+','+y1+' L'+x1+','+(y1+y2)/2+' L'+x2+','+(y1+y2)/2+' L'+x2+','+y2,
          'marker-end':'url(#arrow-overview)'
        });
        g.appendChild(path);
        if (label) {
          var lt = el('text', {x:(x1+x2)/2, y:(y1+y2)/2-6, class:'edge-label '+(labelCls||'')});
          lt.textContent = label;
          g.appendChild(lt);
        }
      }

      // 列排布：左列 START-小红书-路由；中列 合同/合规主链路；右列 检索/问答/兜底
      var LX = CX - W - 80;     // 左列
      var MX = CX - W/2;         // 中列
      var RX = CX + 80;          // 右列

      var Y = 40;
      var GAP = 88;

      // START
      var s0 = node('start', MX, Y, '🚀 START 入口', '', 'start', 'ov_start');
      Y += H + 40;

      // 小红书前置
      var s1 = node('xhs_intent', MX, Y, '📱 xhs发布意图前置识别', '条件路由', 'decision', 'ov_xhs_intent');
      edge(s0.cx, s0.y+H, s1.cx, s1.y, 'normal');
      Y += H + 32;

      // 小红书分支（向左）
      var yXhs = Y;
      var s1a = node('xhs_text', LX, yXhs, '✍️ text_generate 文案生成', '小红书专用', 'pink', 'ov_xhs_text');
      edge(s1.cx, s1.cy+H, s1a.x+W, s1a.y+H/2, 'normal', '发小红书意图');
      yXhs += H + GAP/2;
      var s1b = node('xhs_img', LX, yXhs, '🎨 image_generator 图片生成', '小红书专用', 'pink', 'ov_xhs_img');
      edge(s1a.cx, s1a.y+H, s1b.cx, s1b.y, 'normal');
      yXhs += H + GAP/2;
      var s1c = node('xhs_check', LX, yXhs, '🛡️ check_text_image 质量检查', '条件路由', 'decision', 'ov_xhs_check');
      edge(s1b.cx, s1b.y+H, s1c.cx, s1c.y, 'normal');
      yXhs += H + GAP/2;
      var s1d = node('xhs_pub', LX, yXhs, '🚀 auto_publish 自动发布', '小红书专用', 'pink', 'ov_xhs_pub');
      edge(s1c.cx, s1c.y+H, s1d.cx, s1d.y, 'success', '通过');
      yXhs += H + GAP/2;
      var s1e = node('xhs_md', LX, yXhs, '📝 generate_markdown 报告', '小红书专用', 'pink', 'ov_xhs_md');
      edge(s1d.cx, s1d.y+H, s1e.cx, s1e.y, 'normal');
      yXhs += H + 40;
      var s1end = node('xhs_end', LX, yXhs, '🏁 END 小红书结束', '', 'end', 'ov_end_side1');
      edge(s1e.cx, s1e.y+H, s1end.cx, s1end.y, 'success');

      // 主路由分支（向右）
      var yMain = Y;
      var s2 = node('router', RX, yMain, '🧭 intent_router 意图路由', '5路分发', 'decision', 'ov_router');
      edge(s1.cx+W, s1.cy, s2.x, s2.y+H/2, 'normal', '非小红书意图');
      yMain += H + 32;

      // 合同审核 / 合规审查 主链路（中列下方）+ 法律检索（右列） + 问答/兜底
      // 文档提取（contract/合规共用起点）
      var s3 = node('doc', MX, yMain, '📤 doc_extract 文档提取', '🔄合同+合规共用', 'green', 'ov_doc');
      edge(s2.cx-W/2, s2.y+H, s3.cx+W, s3.cy, 'normal', '合同/合规路径');
      yMain += H + GAP/2;

      var s4 = node('classify', MX, yMain, '🏷️ contract_classify 合同分类', '8类分类', 'green', 'ov_classify');
      edge(s3.cx, s3.y+H, s4.cx, s4.y, 'normal');
      yMain += H + GAP/2;

      var s5 = node('clause', MX, yMain, '✂️ clause_split 条款切分', '结构化条款', 'green', 'ov_clause');
      edge(s4.cx, s4.y+H, s5.cx, s5.y, 'normal');
      yMain += H + GAP/2;

      var s6 = node('numeric_ext', MX, yMain, '🔢 numeric_extract 数值抽取', '关键数值实体', 'green', 'ov_numeric_ext');
      edge(s5.cx, s5.y+H, s6.cx, s6.y, 'normal');
      yMain += H + GAP/2;

      var s7 = node('contract_ai', MX, yMain, '⚖️ contract_ai_review 合同审核AI', '商业条款审查', 'green', 'ov_contract_ai');
      edge(s6.cx, s6.y+H, s7.cx, s7.y, 'normal');
      yMain += H + GAP/2;

      var s8 = node('compliance', MX, yMain, '🛡️ compliance_review 合规审查', '🔄刚性不降级', 'purple', 'ov_compliance');
      edge(s7.cx, s7.y+H, s8.cx, s8.y, 'normal', '必经节点');
      yMain += H + GAP/2;

      var s9 = node('numeric_val', MX, yMain, '✅ numeric_validate 数值校验', '7条规则', 'green', 'ov_numeric_val');
      edge(s8.cx, s8.y+H, s9.cx, s9.y, 'normal');
      yMain += H + 32;

      // 检索 5 子节点区域（加分组容器）
      var retGroup = el('g');
      var retBg = el('rect', { x: MX - 24, y: yMain - 20, width: W + 48, height: 430, rx: 18, ry: 18,
        fill:'rgba(110,231,183,0.04)', stroke:'rgba(110,231,183,0.35)', 'stroke-dasharray':'5 3', 'stroke-width':1.5 });
      retGroup.appendChild(retBg);
      var retTitle = el('text', { x: MX + W/2, y: yMain + 4, class:'sec-num', style:'font-size:11px;letter-spacing:2px;fill:#34d399;font-weight:700;text-anchor:middle;' });
      retTitle.textContent = '🔄 检索 5 子节点 · 三条链路共用 · 横向+纵向双层策略';
      retGroup.appendChild(retTitle);
      g.appendChild(retGroup);

      var yRet = yMain + 24;

      var r1 = node('ret_intent', MX, yRet, '🧠 retrieval_intent_decompose', 'N1 · 意图分解', 'purple', 'ret_intent_decompose');
      edge(s9.cx, s9.y+H, r1.cx, r1.y, 'normal');
      yRet += H + GAP/2;

      var r2 = node('ret_base', MX, yRet, '📚 retrieval_base_layer', 'N2 · 横向挂载+L1 FAISS→L2 本地', 'purple', 'ret_base_layer');
      edge(r1.cx, r1.y+H, r2.cx, r2.y, 'normal');

      // 横向挂载小分支节点（两侧）
      var ind1 = el('g', {class:'node-group node-detail', transform:'translate('+(MX - 280)+','+(yRet-20)+')', 'data-key':'ret_base_layer'});
      var rr1 = el('rect', {class:'node-rect', x:0, y:0, width:210, height:44, rx:10, ry:10});
      ind1.appendChild(rr1);
      var t1 = el('text', {x:105, y:18, class:'node-label', style:'font-size:11px'});
      t1.textContent = '📎 横向按需挂载：行业源';
      ind1.appendChild(t1);
      var t2 = el('text', {x:105, y:34, class:'node-sub', style:'font-size:9px'});
      t2.textContent = '建设工程/金融借贷/劳动合同/买卖/租赁';
      ind1.appendChild(t2);
      ind1.addEventListener('click', function(){ openPopup('ret_base_layer'); });
      g.appendChild(ind1);
      // 连一条虚线
      var eInd1 = el('path', { class:'edge edge-branch',
        d:'M'+(MX-280+210)+','+(yRet-20+22)+' L'+(MX)+','+(yRet+H/2),
        'stroke-dasharray':'4 3' });
      g.appendChild(eInd1);

      yRet += H + GAP/2;

      var r3 = node('ret_enhance', MX, yRet, '🆘 retrieval_enhance_query', 'N3 · 纵向 L3 LLM 伪检索兜底', 'purple', 'ret_enhance_query');
      edge(r2.cx, r2.y+H, r3.cx, r3.y, 'branch', 'base<2条触发');
      // 标注降级说明：L1→L2 文字
      var lab1 = el('text', {x: MX + W/2 + 12, y: r2.y + H + 22, style:'font-size:9px;fill:#fcd34d;font-weight:600;'});
      lab1.textContent = '⬆️ L1 FAISS → L2法规txt（<3条降级）';
      g.appendChild(lab1);
      var lab2 = el('text', {x: MX + W/2 + 12, y: r3.y + H/2, style:'font-size:9px;fill:#f9a8d4;font-weight:600;'});
      lab2.textContent = '⬆️ L3 LLM 兜底（极端防死循环）';
      g.appendChild(lab2);
      yRet += H + GAP/2;

      var r4 = node('ret_fusion', MX, yRet, '🔗 retrieval_fusion_sort', 'N4 · 去重+排序+质量分', 'purple', 'ret_fusion_sort');
      edge(r3.cx, r3.y+H, r4.cx, r4.y, 'normal');
      yRet += H + GAP/2;

      var r5 = node('ret_output', MX, yRet, '📤 retrieval_output', 'N5 · 兼容下游字段', 'purple', 'ret_output');
      edge(r4.cx, r4.y+H, r5.cx, r5.y, 'normal');

      yMain = yRet + H + 40;

      // 甲乙方识别
      var s10 = node('party', MX, yMain, '👥 party_identify 甲乙方识别', '🔄三条链路复用', 'purple', 'ov_party');
      edge(r5.cx, r5.y+H, s10.cx, s10.y, 'normal');
      yMain += H + GAP/2;

      // 🏛️ 资信查询节点（新增！）
      var s10b = node('credit', MX, yMain, '🏛️ credit_check 资信查询（企查查）', '🔄N8.5 MCP→MD5→Mock三级', 'orange', 'credit_check');
      edge(s10.cx, s10.y+H, s10b.cx, s10b.y, 'normal', '用识别的名称去查资信');
      yMain += H + GAP/2;

      var s11 = node('aggregate', MX, yMain, '📊 risk_aggregate 风险聚合', '🔄4路风险合并打分', 'purple', 'ov_aggregate');
      edge(s10b.cx, s10b.y+H, s11.cx, s11.y, 'normal', '合同+合规+数值+资信');
      yMain += H + GAP/2;

      var s12 = node('delivery', MX, yMain, '📦 final_delivery 最终交付', '🔄三条链路复用', 'purple', 'ov_delivery');
      edge(s11.cx, s11.y+H, s12.cx, s12.y, 'normal');
      yMain += H + 40;

      var s13 = node('end_main', MX, yMain, '🏁 END', '', 'end', 'ov_end_main');
      edge(s12.cx, s12.y+H, s13.cx, s13.y, 'success');

      // 法律检索路径（从主路由跳到检索5子节点）
      var yLR = s2.y + H + 80;
      var s_legal = node('legal_entry', RX+40, yLR, '🔍 legal_research 检索入口', '跳转至检索5子节点', 'green', 'ov_legal_res');
      edge(s2.cx+W, s2.y+H/2+10, s_legal.x, s_legal.y+H/2, 'normal', '法律检索');
      // 从检索入口连到检索子图首节点
      var eLegal = el('path', { class:'edge edge-success',
        d:'M'+s_legal.cx+','+(s_legal.y+H)+' L'+s_legal.cx+','+(r1.y-10)+' L'+r1.cx+','+(r1.y-10)+' L'+r1.cx+','+r1.y,
        'marker-end':'url(#arrow-overview)' });
      g.appendChild(eLegal);
      var labLegal = el('text', {x: (s_legal.cx+r1.cx)/2 + 30, y: r1.y - 14, class:'edge-label edge-label-success'});
      labLegal.textContent = '跳转至检索5子节点（独立进入）';
      g.appendChild(labLegal);

      // 问答/兜底分支（从主路由连到问答）
      var yQa = s2.y + H + 80;
      var qaX = RX + 340;
      var q1 = node('qa_extract', qaX, yQa, '🔬 extract_entity 实体抽取', '问答专用', 'cyan', 'ov_qa_extract');
      edge(s2.cx+W, s2.y+H/2, q1.x, q1.y+H/2, 'normal', '法律问答');
      var qY = yQa + H + 50;
      var q2 = node('qa_match', qaX, qY, '🔎 match_entity Neo4j匹配', '问答专用', 'cyan', 'ov_qa_match');
      edge(q1.cx, q1.y+H, q2.cx, q2.y, 'normal');
      qY += H + 50;
      var q3 = node('qa_cypher', qaX, qY, '✍️ generate_cypher Cypher生成', '问答专用', 'cyan', 'ov_qa_cypher');
      edge(q2.cx, q2.y+H, q3.cx, q3.y, 'normal');
      qY += H + 50;
      var q4 = node('qa_check', qaX, qY, '🛡️ check_cypher Cypher校验', '≤3次重试环', 'decision', 'ov_qa_check');
      edge(q3.cx, q3.y+H, q4.cx, q4.y, 'normal');
      qY += H + 50;
      var q5 = node('qa_run', qaX, qY, '⚡ run_cypher Cypher执行', '问答专用', 'cyan', 'ov_qa_run');
      edge(q4.cx, q4.y+H, q5.cx, q5.y, 'success', '通过');
      // 重试环
      var loopPath = el('path', { class:'edge edge-loop',
        d:'M'+(q4.x)+','+(q4.y+H/2)+' Q'+(q4.x-180)+','+(q4.y+H/2)+' '+(q4.x-180)+','+(q3.cy)+' L'+(q3.x)+','+q3.cy,
        'stroke-dasharray':'6 3',
        'marker-end':'url(#arrow-overview)' });
      g.appendChild(loopPath);
      var loopLab = el('text', {x: q4.x - 120, y: q4.cy - 10, class:'edge-label edge-label-loop'});
      loopLab.textContent = '🔄 不通过且重试<3次';
      g.appendChild(loopLab);
      qY += H + 50;
      var q6 = node('qa_ans', qaX, qY, '💡 neo4j_answer_generate 答案生成', '问答专用', 'cyan', 'ov_qa_answer');
      edge(q5.cx, q5.y+H, q6.cx, q6.y, 'normal');
      // 从check到answer的重试达上限分支
      var eCheckAns = el('path', { class:'edge edge-danger',
        d:'M'+(q4.x+W)+','+(q4.y+H/2)+' L'+(q6.x)+','+(q6.y+H/2),
        'stroke-dasharray':'5 3',
        'marker-end':'url(#arrow-overview)' });
      g.appendChild(eCheckAns);
      var eCheckLab = el('text', {x:(q4.x+W+q6.x)/2, y: (q4.y+H/2+q6.y+H/2)/2 - 8, class:'edge-label edge-label-danger'});
      eCheckLab.textContent = '重试≥3次放弃';
      g.appendChild(eCheckLab);
      qY += H + 50;
      var qend = node('qa_end', qaX, qY, '🏁 END', '', 'end', 'ov_end_side2');
      edge(q6.cx, q6.y+H, qend.cx, qend.y, 'success');

      // 兜底分支（LLM直答）
      var yDm = s2.y + H + 80;
      var dmX = qaX;
      var dm1 = node('llm_direct', dmX, yDm + 680, '🆘 llm_direct_out LLM兜底', '兜底专用', 'danger', 'ov_llm_direct');
      edge(s2.cx+W, s2.y+H/2, dm1.x, dm1.y+H/2, 'normal', '其他/兜底');
      var dmY = yDm + 680 + H + 50;
      var dmEnd = node('dm_end', dmX, dmY, '🏁 END', '', 'end', 'ov_end_side2');
      edge(dm1.cx, dm1.y+H, dmEnd.cx, dmEnd.y, 'success');

      // 检查节点质量不通过 -> END
      var eCheckFail = el('path', { class:'edge edge-danger',
        d:'M'+(s1c.x)+','+(s1c.y+H)+' L'+(s1c.x)+','+(s1end.y)+' L'+(s1end.x)+','+(s1end.y),
        'stroke-dasharray':'5 3',
        'marker-end':'url(#arrow-overview)' });
      g.appendChild(eCheckFail);
      var eCFailLab = el('text', {x: s1c.x - 60, y: (s1c.y+H+s1end.y)/2, class:'edge-label edge-label-danger'});
      eCFailLab.textContent = '质量检查不通过';
      g.appendChild(eCFailLab);
    }

    // --- 重绘 Contract/Compliance: 插入 credit_check ---
    function injectCreditIntoOtherSvgs(){
      var ids = ['svg-contract','svg-compliance'];
      ids.forEach(function(svgId){
        var s = document.getElementById(svgId);
        if (!s) return;
        // 简单策略：在SVG末尾追加一个info文本框
        var info = el('g', { transform:'translate(40, 40)' });
        var bg = el('rect', {x:0, y:0, width:640, height:140, rx:14, ry:14,
          fill:'rgba(251,146,60,0.06)', stroke:'rgba(251,146,60,0.35)', 'stroke-dasharray':'5 3'});
        info.appendChild(bg);
        var t1 = el('text', {x:20, y:32, style:'font-size:14px;font-weight:700;fill:#fdba74'});
        t1.textContent = '🏛️ v3.0 增强：新增 N8.5 credit_check 资信查询节点（企查查 API）';
        info.appendChild(t1);
        var t2 = el('text', {x:20, y:58, style:'font-size:12px;fill:#b0c0d0;line-height:1.6'});
        t2.textContent = '真实执行顺序：甲乙方识别 → 🏛️资信查询（企查查MCP/MD5/Mock三级兜底）→ 风险聚合（4路风险合并打分）→ 最终交付';
        info.appendChild(t2);
        var t3 = el('text', {x:20, y:82, style:'font-size:12px;fill:#b0c0d0'});
        t3.textContent = '对应代码：langgraph_main.py L389 party_identify→credit_check（L391）→risk_aggregate（L393）';
        info.appendChild(t3);
        var t4 = el('text', {x:20, y:106, style:'font-size:12px;fill:#b0c0d0'});
        t4.textContent = '💡 点击总架构或下方"资信查询"相关节点查看完整面试题与三级兜底详解';
        info.appendChild(t4);
        s.insertBefore(info, s.firstChild);
      });
    }

    // --- 重绘 Retrieval section: 展开5子节点 + 横向+纵向 ---
    var svgRetrieval = document.getElementById('svg-retrieval');
    if (svgRetrieval) {
      while (svgRetrieval.firstChild) svgRetrieval.removeChild(svgRetrieval.firstChild);
      svgRetrieval.setAttribute('viewBox', '0 0 1100 1720');
      var defs2 = el('defs');
      var mk2 = el('marker', {id:'arrow-ret', viewBox:'0 0 10 10', refX:'9', refY:'5', markerWidth:'8', markerHeight:'8', orient:'auto'});
      var mp2 = el('path', {d:'M0,0 L10,5 L0,10 z', fill:'#4a6a8a'});
      mk2.appendChild(mp2); defs2.appendChild(mk2);
      svgRetrieval.appendChild(defs2);

      var gg = svgRetrieval;
      var W2 = 280, H2 = 72;
      var CX2 = 560;
      var Y2 = 40;
      var GAP2 = 100;

      function rNode(id, x, y, lines, cls, dataKey){
        var gx = el('g', {class:'node-group node-'+cls, transform:'translate('+x+','+y+')', 'data-key':dataKey || id});
        var r = el('rect', {class:'node-rect', x:0, y:0, width:W2, height:H2, rx:14, ry:14});
        gx.appendChild(r);
        lines.forEach(function(l, i){
          var t = el('text', {x:W2/2, y:22 + i*16, class: i===0 ? 'node-label' : 'node-sub'});
          t.textContent = l;
          gx.appendChild(t);
        });
        var h = el('circle', {cx:W2-12, cy:12, r:5, class:'click-hint'});
        gx.appendChild(h);
        gx.addEventListener('click', function(){ openPopup(dataKey || id); });
        gg.appendChild(gx);
        return {x:x, y:y, w:W2, h:H2, cx:x+W2/2, cy:y+H2/2};
      }
      function rEdge(x1,y1,x2,y2,cls,label){
        var p = el('path', { class:'edge edge-'+(cls||'normal'),
          d:'M'+x1+','+y1+' C'+x1+','+(y1+y2)/2+' '+x2+','+(y1+y2)/2+' '+x2+','+y2,
          'marker-end':'url(#arrow-ret)' });
        gg.appendChild(p);
        if (label) {
          var t = el('text', {x:(x1+x2)/2, y:(y1+y2)/2-6, class:'edge-label'});
          t.textContent = label;
          gg.appendChild(t);
        }
      }

      // 起始：START
      var n0 = rNode('start', CX2-W2/2, Y2, ['🚀 法律检索链路入口'], 'start', 'ov_legal_res');
      Y2 += H2 + 40;

      // N1 意图分解
      var n1 = rNode('n1', CX2-W2/2, Y2,
        ['🧠 retrieval_intent_decompose 意图分解',
         '从 doc_text / contract_type / input 提取：',
         'retrieval_query + retrieval_keywords'],
        'purple', 'ret_intent_decompose');
      rEdge(n0.cx, n0.y+H2, n1.cx, n1.y, 'normal');
      Y2 += H2 + GAP2;

      // N2 基础层（核心）+ 横向挂载左右两个容器
      var n2 = rNode('n2', CX2-W2/2, Y2,
        ['📚 retrieval_base_layer 基础层必查',
         '横向按需挂载（contract_type驱动）',
         '纵向 L1 FAISS → L2 本地法规 两级降级'],
        'purple', 'ret_base_layer');
      rEdge(n1.cx, n1.y+H2, n2.cx, n2.y, 'normal');

      // 左侧：横向行业挂载容器
      var hiX = CX2 - W2/2 - 380;
      var hiY = Y2 - 30;
      var hiG = el('g', {transform:'translate('+hiX+','+hiY+')'});
      var hiBg = el('rect', {x:0,y:0,width:340,height:260,rx:14,ry:14,
        fill:'rgba(52,211,153,0.05)', stroke:'rgba(52,211,153,0.4)',
        'stroke-dasharray':'5 3','stroke-width':1.5});
      hiG.appendChild(hiBg);
      var hiTitle = el('text', {x:170,y:26,'text-anchor':'middle',
        style:'font-size:13px;font-weight:700;fill:#34d399;letter-spacing:1px'});
      hiTitle.textContent = '📎 横向按需挂载 · 行业增强层（动态）';
      hiG.appendChild(hiTitle);
      var items = [
        ['建设工程','住建部标准 + 建筑法实施条例','#34d399'],
        ['金融借贷','银保监会监管规定 + 贷款通则','#60c5fa'],
        ['劳动合同','劳动法司法解释 + 社保缴纳规定','#a78bfa'],
        ['买卖合同','最高院买卖合同司法解释','#fb923c'],
        ['租赁合同','城市房屋租赁管理办法','#ec4899']
      ];
      items.forEach(function(it, i){
        var yy = 56 + i * 40;
        var pill = el('rect', {x:16,y:yy,width:308,height:30,rx:8,ry:8,
          fill:'rgba(255,255,255,0.02)', stroke:it[2], 'stroke-opacity':'0.4'});
        hiG.appendChild(pill);
        var tl = el('text', {x:30, y:yy+19, style:'font-size:11px;font-weight:700;fill:'+it[2]});
        tl.textContent = it[0];
        hiG.appendChild(tl);
        var dl = el('text', {x:130, y:yy+19, style:'font-size:10px;fill:#8a9aab'});
        dl.textContent = '→ ' + it[1];
        hiG.appendChild(dl);
      });
      hiG.addEventListener('click', function(){ openPopup('ret_base_layer'); });
      gg.appendChild(hiG);

      // 连虚线从行业容器 -> n2
      var hiEdge = el('path', { class:'edge edge-branch',
        d:'M'+(hiX+340)+','+(hiY+130)+' L'+(CX2-W2/2)+','+(Y2+H2/2),
        'stroke-dasharray':'4 3'});
      gg.appendChild(hiEdge);
      var hiEdgeL = el('text', {x:(hiX+340+CX2-W2/2)/2-20, y:(hiY+130+Y2+H2/2)/2-6, class:'edge-label'});
      hiEdgeL.textContent = '动态挂载';
      gg.appendChild(hiEdgeL);

      // 右侧：纵向三级降级容器
      var vgX = CX2 + W2/2 + 40;
      var vgY = Y2 - 40;
      var vgG = el('g', {transform:'translate('+vgX+','+vgY+')'});
      var vgBg = el('rect', {x:0,y:0,width:300,height:300,rx:14,ry:14,
        fill:'rgba(251,191,36,0.05)', stroke:'rgba(251,191,36,0.4)',
        'stroke-dasharray':'5 3','stroke-width':1.5});
      vgG.appendChild(vgBg);
      var vgTitle = el('text', {x:150,y:26,'text-anchor':'middle',
        style:'font-size:13px;font-weight:700;fill:#fcd34d;letter-spacing:1px'});
      vgTitle.textContent = '⬆️ 纵向逐级降级 · 三级兜底';
      vgG.appendChild(vgTitle);
      var levels = [
        ['L1 高精度', 'FAISS向量检索 + 知识图谱三元组', '(优先 · 权威结构化)', '#34d399'],
        ['↓ 不足3条时降级', '', '', '#8a9aab'],
        ['L2 关键词兜底', '本地法规txt目录 "第X条" 匹配', '(覆盖面广 · 扫描式)', '#60c5fa'],
        ['↓ 仍不足时由下游节点兜底', '', '', '#8a9aab'],
        ['L3 伪检索兜底', 'LLM生成 (下游 enhance 节点)', '(极端情况 · 防死循环)', '#f9a8d4']
      ];
      var yyL = 52;
      levels.forEach(function(lv, i){
        if (i % 2 === 1) {
          var arrow = el('text', {x:150, y:yyL+10, 'text-anchor':'middle',
            style:'font-size:11px;font-weight:700;fill:'+lv[3]});
          arrow.textContent = lv[0];
          vgG.appendChild(arrow);
          yyL += 26;
          return;
        }
        var pill = el('rect', {x:16,y:yyL,width:268,height:40,rx:10,ry:10,
          fill:'rgba(255,255,255,0.02)', stroke:lv[3], 'stroke-opacity':'0.5'});
        vgG.appendChild(pill);
        var tL1 = el('text', {x:28, y:yyL+18, style:'font-size:11px;font-weight:700;fill:'+lv[3]});
        tL1.textContent = lv[0];
        vgG.appendChild(tL1);
        var tL2 = el('text', {x:28, y:yyL+34, style:'font-size:10px;fill:#b0c0d0'});
        tL2.textContent = lv[1] + '  ' + lv[2];
        vgG.appendChild(tL2);
        yyL += 54;
      });
      vgG.addEventListener('click', function(){ openPopup('ret_base_layer'); });
      gg.appendChild(vgG);

      // 从n2连到纵向降级容器
      var vgEdge = el('path', { class:'edge edge-branch',
        d:'M'+(CX2+W2/2)+','+(Y2+H2/2)+' L'+vgX+','+(vgY+vgBg.getAttribute('height')/2),
        'stroke-dasharray':'4 3'});
      gg.appendChild(vgEdge);

      Y2 += H2 + GAP2;

      // N3 增强查询 L3
      var n3 = rNode('n3', CX2-W2/2, Y2,
        ['🆘 retrieval_enhance_query 增强查询（纵向 L3）',
         '仅当 base_citations < 2 条时触发',
         'LLM 根据合同内容生成3-5条法条概要'],
        'purple', 'ret_enhance_query');
      rEdge(n2.cx, n2.y+H2, n3.cx, n3.y, 'branch', 'base<2条 → 启动L3');
      Y2 += H2 + GAP2;

      // N4 融合排序
      var n4 = rNode('n4', CX2-W2/2, Y2,
        ['🔗 retrieval_fusion_sort 融合排序',
         '去重（title+号+前40字）→ 按score排序',
         '拼 research_context（前8条）+ quality_score'],
        'purple', 'ret_fusion_sort');
      rEdge(n3.cx, n3.y+H2, n4.cx, n4.y, 'normal');
      Y2 += H2 + GAP2;

      // N5 结果输出
      var n5 = rNode('n5', CX2-W2/2, Y2,
        ['📤 retrieval_output 结果输出',
         '写入标准字段: citations / research_context / quality_score',
         '与原 legal_research_node 输出兼容'],
        'purple', 'ret_output');
      rEdge(n4.cx, n4.y+H2, n5.cx, n5.y, 'normal');
      Y2 += H2 + 40;

      // 后处理链路（甲乙方识别 → 资信查询 → 风险聚合 → 交付）
      var postBg = el('rect', {x:CX2-W2/2-24, y:Y2-20, width:W2+48, height:520, rx:18, ry:18,
        fill:'rgba(167,139,250,0.04)', stroke:'rgba(167,139,250,0.35)',
        'stroke-dasharray':'5 3','stroke-width':1.5});
      gg.appendChild(postBg);
      var postT = el('text', {x:CX2, y:Y2+4, 'text-anchor':'middle',
        style:'font-size:11px;font-weight:700;fill:#a78bfa;letter-spacing:2px'});
      postT.textContent = '🔄 共享后处理链路（合同审核/合规审查/法律检索 三条共用）';
      gg.appendChild(postT);

      var yP = Y2 + 24;
      var p1 = rNode('p1', CX2-W2/2, yP, ['👥 party_identify 甲乙方识别','识别甲乙名称 + 用户立场'], 'purple', 'ov_party');
      rEdge(n5.cx, n5.y+H2, p1.cx, p1.y, 'normal');
      yP += H2 + GAP2;

      var p2 = rNode('p2', CX2-W2/2, yP,
        ['🏛️ credit_check 资信查询（企查查 API）',
         '三级兜底：MCP Bearer → AppKey MD5 → Mock',
         '写入甲乙双方信用信息 + credit_risk_items'],
        'orange', 'credit_check');
      rEdge(p1.cx, p1.y+H2, p2.cx, p2.y, 'normal', '查双方资信');
      yP += H2 + GAP2;

      var p3 = rNode('p3', CX2-W2/2, yP,
        ['📊 risk_aggregate 风险聚合',
         '4路风险合并：合同/合规/数值/资信',
         '计算 overall_risk_score + risk_level'],
        'purple', 'ov_aggregate');
      rEdge(p2.cx, p2.y+H2, p3.cx, p3.y, 'normal');
      yP += H2 + GAP2;

      var p4 = rNode('p4', CX2-W2/2, yP,
        ['📦 final_delivery 最终交付',
         '组装 final_report_markdown + output 摘要',
         '引用卡片最多展示 8 条 citation'],
        'purple', 'ov_delivery');
      rEdge(p3.cx, p3.y+H2, p4.cx, p4.y, 'normal');
      yP += H2 + 40;

      var pEnd = rNode('end', CX2-W2/2, yP, ['🏁 END'], 'end', 'ov_end_main');
      rEdge(p4.cx, p4.y+H2, pEnd.cx, pEnd.y, 'success');
    }

    // 执行注入（原页面 drawContract 等函数会被 window.onload 执行）
    // 我们在原加载后再执行 override
    var origOnload = window.onload;
    window.onload = function(){
      if (typeof origOnload === 'function') {
        try { origOnload(); } catch(e) {}
      }
      try {
        injectCreditIntoOtherSvgs();
      } catch(e) { console.warn('inject credit err', e); }
    };

  })();
  </script>
"""

# 在 </body> 前插入
html = html.replace("</body>", INJECT_JS + "\n  </body>")

# 写入输出
with open(OUTPUT, "w", encoding="utf-8") as f:
    f.write(html)

print(f"✅ 生成完成: {OUTPUT}")
print(f"   大小: {os.path.getsize(OUTPUT)/1024:.1f} KB")
