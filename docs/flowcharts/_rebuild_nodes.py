# -*- coding: utf-8 -*-
"""
重建 docs/flowcharts/节点式流程图.html
- 保留原 CSS/弹窗系统
- 完全对照 docs/flowcharts_文字/ 最新架构重建 D 数据对象与 CHARTS 图表
- 单渲染引擎，移除文件尾部冲突的备用脚本
"""
import io, re, os

SRC = r'E:\to_github_project\AI_legal_assistant\docs\flowcharts\节点式流程图.html'
BAK = SRC + '.bak.' + __import__('datetime').datetime.now().strftime('%Y%m%d_%H%M%S')
OUT = SRC

# 读取原文件提取 CSS（到 </style> 为止）
with io.open(SRC, 'r', encoding='utf-8') as f:
    OLD = f.read()

m = re.search(r'<style>.*?</style>', OLD, flags=re.S)
if not m:
    raise SystemExit('未找到原文件 <style> 块')
CSS = m.group(0)

# 确保 light 主题下 SVG 文字可读（补一条兜底）
if '[data-theme="light"] svg text {' not in CSS:
    CSS = CSS.rstrip() + '\n    [data-theme="light"] svg text { fill: #1a2330 }\n  </style>'
    CSS = CSS.replace('  </style>\n  </style>', '  </style>', 1)


def escape_js(s):
    if s is None:
        return ''
    return (s.replace('\\', '\\\\')
            .replace("'", "\\'")
            .replace('\n', '\\n')
            .replace('\r', ''))


def qa_block(q, a):
    return {'q': q, 'a': a}


# ═══════════════════════════════════════════════════════
# D 数据对象：每个节点的弹窗内容
# ═══════════════════════════════════════════════════════
D = {}

# ── OVERVIEW / SHARED ──
D['ov_start'] = {
    't': 'START 入口', 'i': '🚀', 'y': 'start',
    'f': '用户通过 Streamlit / FastAPI 上传合同或输入文字指令，LangGraph 从 START 哨兵节点启动，初始化 AgentState。',
    'flow': '作为全图唯一入口，统一进入「小红书前置过滤」节点；只有非小红书意图才进入主意图路由。',
    'reuse': 'START 为所有智能体共享入口。',
    'why': '单一入口便于统一日志、trace_id、权限校验与审计。',
    'tc': 'LangGraph START 常量 + StateGraph 状态机。',
    'op': '可增加入口限流、文件病毒扫描、用户鉴权前置节点。',
    'iv': [qa_block('LangGraph 入口/出口怎么定义？', '使用 START、END 两个哨兵常量；add_edge(START, node) 定义入口，add_edge(node, END) 定义出口。')]
}

D['ov_xhs_intent'] = {
    't': '小红书意图·前置过滤', 'i': '📱', 'y': 'decision',
    'f': 'START 后第一个节点，LLM 二分类判断用户是否要发小红书。若是，直接进入独立发布链路；否则交给 intent_router。',
    'flow': 'START → 本节点；[小红书意图] → 文案生成；[非小红书意图] → intent_router。',
    'reuse': '独立链路，不被其他智能体复用，但所有请求必经。',
    'why': '小红书意图明确（关键词匹配即可高置信识别），前置过滤避免浪费主路由 LLM 调用；同时发布链路与法律业务完全解耦。',
    'tc': 'LLM JSON 二分类 + 关键词兜底。',
    'op': '可加入多平台发布意图识别（知乎、公众号），抽象为 content_publish_intent。',
    'iv': [qa_block('为什么小红书要前置过滤？', '意图明确、链路独立；避免非发布请求进入内容生产流程，节省成本并降低误触发。')]
}

D['ov_router'] = {
    't': 'intent_router 意图路由', 'i': '🧭', 'y': 'decision',
    'f': 'LLM 分析用户输入，识别 task_type：contract_review / compliance_review / legal_research / legal_qa / legal_document_gen / case_search / law_query / other。',
    'flow': '接收非小红书请求 → 输出 task_type → 经 add_conditional_edges 分发到 5+ 条业务链路。',
    'reuse': '全系统唯一主路由节点，被所有业务链路共享入口。',
    'why': '集中路由让新增智能体只需扩展 path_map，无需改动上游。',
    'tc': 'LLM with_structured_output + 条件边 path_map。',
    'op': '增加路由置信度阈值，低置信时返回 Top-3 候选让用户确认；增加路由错误回溯环。',
    'iv': [qa_block('intent_router 如何分发？', '路由函数读取 state["task_type"] 返回路径标识符，path_map 映射到具体节点名；新增路径只需扩展枚举与映射。')]
}

D['ov_credit_precheck'] = {
    't': 'credit_precheck 企查查预判定', 'i': '🏢', 'y': 'decision',
    'f': '非小红书路径的统一企查查预查：根据关键词强度决定查询深度（strong→查全部 / medium→查关键词 / weak→仅标记 / none→跳过），结果写入缓存避免重复查询。',
    'flow': 'intent_router 后 → 本节点 → after_credit_precheck_router 二次分发。',
    'reuse': '为合同/合规/检索/问答/文书等所有非小红书路径提供一次统一资信预查。',
    'why': '避免同一请求在后续多个节点重复查企查查，降低成本并保证一致性。',
    'tc': '企查查 MCP + 3-tier 降级（Bearer → AppKey+MD5 → Mock）+ Redis 缓存。',
    'op': '可按合同类型动态调整查询深度；增加资信结果 TTL 与缓存失效策略。',
    'iv': [qa_block('为什么资信要预判定而非深度查询？', '先轻量判定风险等级，高风险才全量查询；平衡成本与召回。')]
}

D['ov_second_router'] = {
    't': 'after_credit_precheck_router 二次路由', 'i': '🔀', 'y': 'decision',
    'f': '基于 task_type 将请求二次分发到各链路的真实起点：合同/合规走共享预处理；法律检索走检索子图；法律问答走 KG RAG；文书生成走 docgen 链路等。',
    'flow': '接收 credit_precheck 结果 → 按 task_type 分发 → 各链路入口。',
    'reuse': '与 intent_router 共同组成两级路由，解耦「意图识别」与「业务起点选择」。',
    'why': '企查查预判定结果可能影响下游挂载的数据源与处理策略（如高风险合同增强资信节点）。',
    'tc': 'LangGraph 条件边 + state.task_type。',
    'op': '可增加动态数据源挂载（mounted_sources）由本节点写入。',
    'iv': [qa_block('为什么需要二次路由？', '第一次路由识别意图，第二次路由在拿到企查查预判定后决定真实起点与增强策略。')]
}

# 小红书链路
for k, t, i, y, f, flow in [
    ('ov_xhs_text', 'text_generate 文案生成', '✍️', 'pink',
     '根据用户主题生成小红书风格标题与正文，写入 xiaohongshu_title / xiaohongshu_content。',
     '小红书意图确认 → 文案生成 → 图片生成。'),
    ('ov_xhs_img', 'image_generator 图片生成', '🎨', 'pink',
     '根据文案主题生成配图；Tier1 Stable Diffusion / Tier2 DALL-E 3 / Tier3 占位图降级。',
     '文案生成 → 图片生成 → 图文检查。'),
    ('ov_xhs_check', 'check_text_image 图文检查', '🛡️', 'decision',
     'LLM 三维度检查（敏感词/广告违规/图片合规），输出 is_can_publish_xiaohongshu。',
     '图片生成 → 本节点；[通过] → 自动发布；[不通过] → END。'),
    ('ov_xhs_pub', 'xiaohongshu_auto_publish 自动发布', '🚀', 'pink',
     'Playwright 模拟浏览器操作登录、填写标题正文、上传图片、点击发布。',
     '图文检查通过 → 自动发布 → Markdown 存档。'),
    ('ov_xhs_md', 'generate_markdown Markdown存档', '📝', 'pink',
     '将发布内容、时间、配图、状态整理为 Markdown 存档，便于追溯与审计。',
     '自动发布 → Markdown 存档 → END。'),
]:
    D[k] = {'t': t, 'i': i, 'y': y, 'f': f, 'flow': flow,
            'reuse': '小红书链路独立，不与其他智能体复用业务节点。',
            'why': '发布链路是 RPA 操作，与法律推理链路解耦，避免互相影响。',
            'tc': 'LLM 生成 + 图像 API + Playwright RPA。',
            'op': '可增加多平台分发（知乎/公众号）与发布效果追踪。',
            'iv': [qa_block('小红书发布失败怎么办？', '图片失败可降级为无图发布；发布失败不影响主流程，记录日志后提示用户。')]}

# 共享预处理 5 节点
D['ov_doc'] = {
    't': 'doc_extract 文档提取', 'i': '📄', 'y': 'green',
    'f': '检测 txt/md/docx 等文件类型并解析为纯文本，写入 state["doc_text"]；文件不存在或解析失败时用 input 兜底。',
    'flow': '合同/合规链路入口 → 文档提取 → 甲乙方识别。',
    'reuse': '被合同审核、合规审查两条链路共享。',
    'why': '律师拿到合同原件后首先要可读化，这是所有后续审查的前提。',
    'tc': 'python-docx / markdown / MinerU 等多解析器 + 降级兜底。',
    'op': '增加三级解析降级（MinerU → pdfplumber → OCR）与解析质量评分。',
    'iv': [qa_block('为什么合同和合规都走同一 doc_extract？', '两者都需要上传文档并解析文本；路由时 contract_review_path 与 compliance_review_path 都映射到同一节点，实现代码级复用。')]
}

D['ov_party'] = {
    't': 'party_identify 甲乙方识别', 'i': '👥', 'y': 'green',
    'f': '三层逻辑：①规则层 4 种正则匹配甲方/乙方；②LLM 层任一方未匹配时补全；③根据 input 推断 user_side（A/B/关键词）。',
    'flow': '文档提取 → 甲乙方识别 → 合同分类。',
    'reuse': '被合同审核、合规审查、法律检索共享；在合同/合规链路中属于预处理。',
    'why': '律师必须先搞清楚「我的客户是谁、对方是谁」，这决定后续审查立场与资信查询对象。',
    'tc': '正则 + LLM JSON + 立场推断规则。',
    'op': '增加多文件合同主体对齐、境外主体识别。',
    'iv': [qa_block('甲乙方识别为什么放在预处理阶段？', '新架构将 party_identify 前置到 doc_extract 之后，立场与主体信息越早确定，后续检索、审核、交付越能个性化。')]
}

D['ov_classify'] = {
    't': 'contract_classify 合同分类', 'i': '🏷️', 'y': 'green',
    'f': 'LLM 基于全文判断合同类型（买卖/租赁/借贷/建设工程/政府采购/劳动/服务/技术/其他），写入 contract_type。',
    'flow': '甲乙方识别 → 合同分类 → 条款切分。',
    'reuse': '被合同审核、合规审查共享。',
    'why': '不同类型合同适用法律不同、审查重点不同、挂载检索源不同。',
    'tc': 'LLM with_structured_output + 9 类分类体系。',
    'op': '支持混合类型（主类型+次要类型）同时挂载多类检索源。',
    'iv': [qa_block('分类错误会怎样？', '会导致检索源挂载错误，进而遗漏行业特定法规；可增加 confidence<0.8 时让用户确认。')]
}

D['ov_clause'] = {
    't': 'clause_split 条款切分', 'i': '✂️', 'y': 'green',
    'f': '优先按「第X条」正则切分，无编号时按换行分段，输出结构化条款列表 doc_clauses。',
    'flow': '合同分类 → 条款切分 → 数值抽取。',
    'reuse': '被合同审核、合规审查共享。',
    'why': '律师逐条阅读合同，先明确结构才能定位风险条款。',
    'tc': '正则 + LLM 辅助边界修正 + bbox 坐标保留。',
    'op': '增加切分质量校验（条款数量异常时重切）与嵌套子条款支持。',
    'iv': [qa_block('条款切分为什么不用简单换行？', '合同条款可能跨页、多段落、嵌套子条款；需语义+结构分析，保留 bbox 以支持原文标红。')]
}

D['ov_numeric_ext'] = {
    't': 'numeric_extract 数值抽取', 'i': '🔢', 'y': 'green',
    'f': 'LLM 从合同文本抽取单价/数量/总价/税率/违约金比例/利率/保证金/付款比例/期限等，写入 extracted_numerics。',
    'flow': '条款切分 → 数值抽取 → 「检索提前」进入检索子图。',
    'reuse': '被合同审核、合规审查共享；数值校验函数亦被合规基础筛查复用。',
    'why': '检索提前需要携带具体业务上下文（如违约金比例）才能召回最相关法条与类案。',
    'tc': 'LLM JSON 抽取 + 规则交叉验证 + 缺失检测。',
    'op': '增加数值抽取重试环：抽取后用公式校验，不一致时带反馈重抽。',
    'iv': [qa_block('为什么数值抽取在检索之前？', '新架构「检索提前」：先抽数值与条款，检索才能带具体上下文召回最相关法条，提升命中率。')]
}

# 检索子图（共享 5 节点）
ret_nodes = [
    ('ov_ret_intent', 'retrieval_intent_decompose 意图分解', '🧠', 'purple',
     '从 doc_text / contract_type / user_input 中提取检索查询与关键词，写入 retrieval_query / retrieval_keywords；LLM 失败时降级为标点分词。'),
    ('ov_ret_base', 'retrieval_base_layer 基础层检索', '📚', 'purple',
     '根据 mounted_sources 多路并行检索：L0 Neo4j 精确匹配 → L1 FAISS 向量检索 → L2 本地法规关键词扫描 → L3 行业标准 → L4 案例 → L5 司法解释 → 共享企查查资信。输出 base_citations。'),
    ('ov_ret_enhance', 'retrieval_enhance_query 增强查询', '🚀', 'purple',
     '当 base_citations < 2 时，LLM 根据合同正文补充生成 3-5 条相关法条，标记 source=LLM 生成，作为伪检索兜底。'),
    ('ov_ret_fusion', 'retrieval_fusion_sort 融合排序', '🔗', 'purple',
     '合并 base_citations + enhance_citations；去重（title+article_no+content 前 40 字符 hash）；RRF 融合排序；4 条冲突消解规则；4 维度质量分。输出 citations / quality_score / fusion_log。'),
    ('ov_ret_output', 'retrieval_output 结果输出', '📤', 'purple',
     '质量门禁：quality_score ≥ 0.85 直接输出，否则 quality_retry_count++；<3 次回退基础层重试，≥3 次提示人工并可选北大法宝 MCP 付费兜底。格式化输出 research_context。'),
]
for idx, (k, t, i, y, f) in enumerate(ret_nodes):
    prev = ret_nodes[idx-1][0] if idx > 0 else 'ov_numeric_ext'
    nxt = ret_nodes[idx+1][0] if idx < len(ret_nodes)-1 else '下游节点'
    flow = f'接收 {"检索词" if idx==0 else "上一步结果"} → 输出给 {nxt}'
    if k == 'ov_ret_output':
        flow = '接收融合结果 → 质量门禁判断 → 满足则输出 research_context 给合同审核AI/合规审查；不满足则重试或 MCP 兜底。'
    D[k] = {
        't': '🔄 ' + t + '（复用）', 'i': i, 'y': y, 'f': f, 'flow': flow,
        'reuse': '检索 5 节点子图被合同审核、合规审查、法律检索、文书生成 4 条链路复用；挂载层由 mounted_sources 控制。',
        'why': '把检索做成独立子图，保证所有需要法条/类案的链路使用同一召回、融合、质量门禁逻辑。',
        'tc': 'FAISS(bge-m3) + Neo4j + 本地法规 txt + RRF + MCP 北大法宝付费兜底。',
        'op': '增加检索结果缓存、检索源故障自动降级、多查询并行。',
        'iv': [qa_block('检索子图为什么被提前？', '旧架构检索在最后，审核无法引用法条；新架构检索提前，让合同审核AI和合规审查都有法条/类案支撑，结果可溯源。')]
    }

# 合同审核 / 合规审查 / 后处理
D['ov_contract_ai'] = {
    't': 'contract_ai_review 合同审核AI', 'i': '⚖️', 'y': 'green',
    'f': 'LLM 以商业律师/代理人立场，从 6 大维度（价格付款、交付验收、违约责任、保密IP、管辖争议、终止退出）审查，输出 contract_risk_items（可谈判，含修改建议）。',
    'flow': '检索结果输出 → 合同审核AI → 合规审查。',
    'reuse': 'contract_ai_review_node 被合同审核与合规审查链路复用。',
    'why': '合同审核关注条款是否对我方有利，是弹性商业判断。',
    'tc': 'LLM + Pydantic 结构化输出 + 立场化 Prompt。',
    'op': '增加 6 维度细拆、历史裁决库推荐、类案匹配增强。',
    'iv': [qa_block('合同审核AI和合规审查的区别？', '合同审核=商业视角（可谈判）；合规审查=法律监管视角（刚性一票否决）。冲突时合规优先。')]
}

D['ov_compliance'] = {
    't': 'compliance_review 合规审查', 'i': '🛡️', 'y': 'purple',
    'f': 'LLM 以合规律师/裁判者立场，从 7 大领域（强制规定、数据合规、反垄断、税务、劳动、行业准入、政府采购）审查，输出 compliance_risk_items 与 can_sign（pass/conditional/no）。',
    'flow': '合同审核AI → 合规审查 → 冲突消解。',
    'reuse': 'compliance_review_node 被合同审核链路作为必经子调用，也可被独立触发。',
    'why': '合规是法律底线，有一票否决权；商业条款再优也不能突破法律。',
    'tc': 'LLM + 7 领域 Prompt + can_sign 判定 + 刚性不降级约束。',
    'op': '增加合规规则库 YAML 配置、监管动态同步、历史处罚案例映射。',
    'iv': [qa_block('为什么合规结论是刚性不可降级的？', '《中央企业合规管理办法》§21 规定合规结论为事实判定；代码层 compliance_risk_items 的 severity 只读，合同审核只能追加 suggestion。')]
}

D['ov_conflict'] = {
    't': 'conflict_resolution 冲突消解', 'i': '⚔️', 'y': 'orange',
    'f': '5 条规则统一裁决合同审核与合规审查结论：①合规 critical 直接否决签约；②合规 high 强制整改；③同一问题双重发现以合规为准；④合规通过+合同有风险保留为商业风险；⑤结论冲突以合规结论优先。',
    'flow': '接收 contract_risk_items + compliance_risk_items → 统一裁决 → 输出 merged_risk_items + can_sign 给数值校验。',
    'reuse': 'conflict_resolution_node 被合同审核与合规审查两条链路共用。',
    'why': '商业立场（弹性）与法律底线（刚性）可能冲突，必须有统一裁决且合规优先。',
    'tc': 'StateGraph 节点 + 优先级合并 + 只读字段约束。',
    'op': '增加冲突模式沉淀为规则、按历史裁决库推荐处置方案。',
    'iv': [qa_block('冲突消解和直接聚合有什么区别？', '聚合只是堆叠风险算总分；冲突消解先按合规优先规则裁决，解决商业结论想覆盖法律结论的问题，是合规刚性的最后一道闸。')]
}

D['ov_numeric_val'] = {
    't': 'numeric_validate 数值校验', 'i': '✅', 'y': 'green',
    'f': '纯 Python 执行 7 条确定性数值校验（总价=单价×数量、付款比例之和=100%、违约金≤30%、定金≤20%、利率≤LPR×4 等），输出 numeric_risk_items。',
    'flow': '冲突消解 → 数值校验 → 资信查询。',
    'reuse': 'numeric_validate_node 被合同审核、合规审查、法律检索复用；校验函数亦被合规基础筛查直接调用。',
    'why': '法律金额要求零误差+可审计，不能用 LLM 做最终计算。',
    'tc': 'Decimal 精确计算 + ALL_NUMERIC_RULES 可配置列表 + Pydantic 输出。',
    'op': '增加数值缺失检测、LPR 动态获取、行业特定阈值。',
    'iv': [qa_block('数值校验为何不用 LLM？', 'LLM 只负责抽取，抽取结果再用规则交叉验证+重试；计算用确定性代码，避免浮点误差与幻觉导致法律金额误判。')]
}

D['ov_credit'] = {
    't': 'credit_check 企查查资信查询', 'i': '🏢', 'y': 'purple',
    'f': '查询甲乙方企业资信：工商/股东/失信/被执行/异常/处罚/司法协助/知识产权/年报等；3-tier 降级：MCP Bearer → AppKey+MD5 → Mock（永不阻塞）。',
    'flow': '甲乙方识别 → 资信查询 → 风险聚合。',
    'reuse': '被合同审核、合规审查、法律检索三条链路复用。',
    'why': '即使合同条款合法，对方若是失信被执行人，交易风险仍极高。',
    'tc': '企查查 MCP + 3-tier 降级 + 10 维度资信评分。',
    'op': '增加资信结果缓存（7 天）、风险阈值告警、黑名单联动。',
    'iv': [qa_block('资信查询对合同审核的意义？', '发现对方主体风险，作为风险聚合的增量输入，影响 overall_risk_score。')]
}

D['ov_aggregate'] = {
    't': 'risk_aggregate 四路风险聚合', 'i': '📊', 'y': 'purple',
    'f': '合并合同风险 + 合规风险 + 数值风险 + 资信风险；去重→加权→扣分制评分（0-100）→风险等级（Low/Medium/High）。合规 critical 不可降级。',
    'flow': '资信查询 → 风险聚合 → 最终交付。',
    'reuse': '被合同审核、合规审查、法律检索三条链路复用。',
    'why': '四路风险必须统一评分才能给出最终处置建议与签约结论。',
    'tc': '规则引擎 + 加权求和 + 不可降级约束。',
    'op': '增加律师反馈回溯环、风险趋势分析、评分敏感性测试。',
    'iv': [qa_block('风险聚合的核心规则？', '优先级：合规 > 数值 > 合同 > 资信；合规风险是刚性判断，不可被弹性商业判断降级。')]
}

D['ov_delivery'] = {
    't': 'final_delivery 最终交付', 'i': '📦', 'y': 'purple',
    'f': '纯字符串拼装 Markdown 报告：签约结论、合规风险清单（不可谈判）、商业风险清单（可谈判）、数值校验结果、相对方资信、综合评分与处置建议。输出 final_report_markdown + output。',
    'flow': '风险聚合 → 最终交付 → END。',
    'reuse': '被合同审核、合规审查、法律检索三条链路复用。',
    'why': '交付逻辑统一，保证不同链路输出格式一致、可审计。',
    'tc': 'Markdown 模板 + 字段动态填充 + 无 LLM 调用（确定性强）。',
    'op': '增加 PDF/Word 导出、原文标红、立场化修订版合同。',
    'iv': [qa_block('final_delivery 为什么要复用？', '不同链路差异仅在输入数据，通过 state 字段动态适配；复用减少代码重复并保证交付格式一致。')]
}

D['ov_party_post'] = {
    't': 'party_identify 甲乙方识别（后处理复用）', 'i': '👥', 'y': 'purple',
    'f': '在合同/合规链路中，预处理阶段已完成主体识别；此节点在检索/后处理链路中复用同一逻辑，识别立场以驱动立场化交付。',
    'flow': '数值校验 → 甲乙方识别 → 资信查询。',
    'reuse': '与预处理阶段共用 party_identify_node。',
    'why': '法律检索等无文档链路仍需知道 user_side 以生成立场化答案。',
    'tc': '与 ov_party 同一实现。',
    'op': '可合并到预处理阶段，法律检索链路独立处理。',
    'iv': [qa_block('法律检索中甲乙方识别有什么用？', '识别用户立场（甲方/乙方），让 final_delivery 从用户立场组织答案。')]
}

# 法律问答链路
qa_nodes = [
    ('ov_qa_extract', 'extract_entity 实体抽取', '🔬', 'cyan',
     'LLM 从用户问题中抽取法律实体、概念、法规名，写入 user_input_entities / concepts / statutes。'),
    ('ov_qa_match', 'match_entity Neo4j匹配', '🔎', 'cyan',
     '在 Neo4j 知识图谱中匹配抽取的实体节点；Neo4j 不可用时降级为 matched_entities=[]。'),
    ('ov_qa_cypher', 'generate_neo4j_cypher Cypher生成', '✍️', 'cyan',
     '基于匹配实体和图 schema，LLM 生成 Cypher 查询语句，写入 cypher_query。'),
    ('ov_qa_check', 'check_cypher Cypher校验', '🛡️', 'decision',
     'LLM 校验 Cypher 语法、标签/关系存在性、查询合理性；通过则执行，不通过且重试<3 次则回退重新生成。'),
    ('ov_qa_run', 'run_cypher Cypher执行', '⚡', 'cyan',
     '在 Neo4j 上执行查询，返回 cypher_results；执行异常则降级。'),
    ('ov_qa_answer', 'neo4j_answer_generate 答案生成', '💡', 'cyan',
     'LLM 将图查询结果翻译为自然语言答案，写入 neo4j_answer → output。'),
]
for idx, (k, t, i, y, f) in enumerate(qa_nodes):
    prev = qa_nodes[idx-1][0] if idx > 0 else 'ov_router'
    nxt = qa_nodes[idx+1][0] if idx < len(qa_nodes)-1 else 'END'
    flow = f'{prev} → 本节点 → {nxt}'
    if k == 'ov_qa_check':
        flow = 'Cypher生成 → 本节点；[通过] → Cypher执行；[不通过&retry<3] → 回退 Cypher生成；[retry≥3] → 答案生成（降级）。'
    if k == 'ov_qa_run':
        flow = 'Cypher校验通过 → Cypher执行 → 答案生成；执行异常 → 答案生成（降级）。'
    D[k] = {'t': t, 'i': i, 'y': y, 'f': f, 'flow': flow,
            'reuse': '法律问答是独立链路，不与其他智能体复用业务节点，但共享 intent_router。',
            'why': '问答是纯文本咨询场景，需要图谱推理而非文档审查。',
            'tc': 'Neo4j + Text-to-Cypher + LLM + 校验重试环。',
            'op': '增加 Cypher 模板缓存、查询计划成本评估、多轮对话上下文。',
            'iv': [qa_block('Cypher 校验重试环怎么实现？', 'check_cypher_router 条件路由：通过→run_cypher；不通过且 retry<3→generate_neo4j_cypher；retry≥3→neo4j_answer_generate 降级。')]}

# 文书生成链路
D['ov_docgen_start'] = {
    't': 'legal_document_gen 文书生成入口', 'i': '📝', 'y': 'start',
    'f': '用户输入案情描述/诉求/纠纷类型，intent_router 识别为 legal_document_gen 后进入文书生成链路。',
    'flow': 'intent_router → 文书生成入口 → 案情分析。',
    'reuse': '独立链路，但 N3 条款填充复用检索智能体的 search()。',
    'why': '将法律问题结构化并生成正式文书，与问答/审核形成互补。',
    'tc': 'LangGraph 子图 + 模板引擎 + RAG。',
    'op': '支持多文书批量生成、模板市场。',
    'iv': [qa_block('文书生成和问答有什么区别？', '问答侧重解释法律问题；文书生成侧重按模板产出正式法律文书。')]
}

D['ov_docgen_analyze'] = {
    't': 'doc_case_analyze 案情分析', 'i': '🔍', 'y': 'blue',
    'f': 'LLM 从用户输入中结构化抽取案由/当事人/事实/诉求/证据，输出 case_summary；信息不足时设置 need_clarify。',
    'flow': '文书入口 → 案情分析 → 模板匹配。',
    'reuse': '仅文书生成链路使用。',
    'why': '生成文书前必须先明确案情要素，否则模板填充缺乏依据。',
    'tc': 'LLM with_structured_output + JSON Schema。',
    'op': '增加追问生成与多轮澄清。',
    'iv': [qa_block('案情分析信息不足怎么办？', '设置 need_clarify=true，输出 clarify_question 追问用户，补齐后再继续。')]
}

D['ov_docgen_template'] = {
    't': 'doc_template_match 模板匹配', 'i': '📋', 'y': 'blue',
    'f': '根据 case_type / parties / facts 等特征匹配 10 种预设模板（民事起诉状、答辩状、上诉状、执行申请、保全申请、劳动仲裁、行政复议、合同审查意见、法律意见、律师函）。',
    'flow': '案情分析 → 模板匹配 → 条款填充。',
    'reuse': '仅文书生成链路使用。',
    'why': '不同类型文书格式差异大，模板保证结构合法。',
    'tc': '规则链 + 模板库 + 置信度评分。',
    'op': '支持自定义模板上传、模板版本管理。',
    'iv': [qa_block('为什么用规则而非 LLM 选模板？', '模板选择是确定性分类任务，规则更快更可控；LLM 用于复杂边界情况兜底。')]
}

D['ov_docgen_fill'] = {
    't': 'doc_clause_fill 条款填充(RAG)', 'i': '⚖️', 'y': 'blue',
    'f': '加载模板 → 复用检索智能体 search() 检索相关法条 → LLM 将案情与法条填入占位符，输出 filled_doc + cited_laws。',
    'flow': '模板匹配 → 条款填充 → 法条校验。',
    'reuse': '复用检索智能体的 search() 方法/子图。',
    'why': '文书需要准确的法律依据支撑，复用检索保证法条真实性与一致性。',
    'tc': '模板引擎 + 检索复用 + LLM 填充。',
    'op': '增加填充结果缓存、法条引用自动编号。',
    'iv': [qa_block('文书生成如何复用检索？', '调用 retrieval_agent.search()，传入 case_summary 中的 claims/keywords，复用检索 5 节点子图获取 citations。')]
}

D['ov_docgen_validate'] = {
    't': 'doc_law_validate 法条真实性校验', 'i': '✅', 'y': 'decision',
    'f': 'LLM 校验文书引用法条：pass / rewrite（存在但不准确）/ fabricated（不存在）。输出 need_refill 与 validation_issues。',
    'flow': '条款填充 → 法条校验；[pass] → 风险提示+类案推荐；[need_refill & retry<3] → 回退条款填充；[retry≥3] → 强制人工确认。',
    'reuse': '独立节点，但底层可复用检索验证逻辑。',
    'why': '防止 LLM 在文书中引用不存在的法条（幻觉），是法律文书可信的关键。',
    'tc': 'LLM 校验 + 3 级判定 + 重试环。',
    'op': '增加与检索数据库的精确比对、法条编号规则校验。',
    'iv': [qa_block('法条校验重试环怎么工作？', 'need_refill=true 且 doc_retry_count<3 时回退到条款填充重新检索+填充；超过 3 次则标记人工介入。')]
}

D['ov_docgen_risk'] = {
    't': 'doc_risk_advisor 风险提示', 'i': '⚠️', 'y': 'blue',
    'f': 'LLM 基于 filled_doc 标注 2-5 项法律风险点，输出 risks[]；与类案推荐并行执行。',
    'flow': '法条校验通过 → 风险提示（并行）→ 最终交付。',
    'reuse': '仅文书生成使用。',
    'why': '用户拿到文书的同时需要知道潜在风险。',
    'tc': 'LLM + 模板感知 Prompt。',
    'op': '增加风险等级量化、引用历史相似案件。',
    'iv': [qa_block('风险提示为什么和类案推荐并行？', '两者相互独立，并行可减少链路延迟。')]
}

D['ov_docgen_cases'] = {
    't': 'doc_case_recommend 类案推荐', 'i': '🔗', 'y': 'blue',
    'f': '纯检索：BM25 + FAISS 检索相似案例，输出 similar_cases[]；与风险提示并行执行。',
    'flow': '法条校验通过 → 类案推荐（并行）→ 最终交付。',
    'reuse': '可复用检索智能体的案例检索能力。',
    'why': '文书用户需要参考类似判例评估胜诉/败诉风险。',
    'tc': 'BM25 + FAISS 混合检索。',
    'op': '增加类案筛选（同案由、同法院层级）、裁判要旨摘要。',
    'iv': [qa_block('文书生成最终输出什么？', 'final_document + document_id + cited_laws + risks + similar_cases；通过 HistoryStore 持久化。')]
}

D['ov_docgen_delivery'] = {
    't': 'doc_final_delivery 文书最终交付', 'i': '📦', 'y': 'blue',
    'f': '组装完整法律文书 + 引用法条 + 风险提示 + 类案推荐 + HistoryStore 持久化，输出 final_document + document_id。',
    'flow': '风险提示/类案推荐 → 最终交付 → END。',
    'reuse': '与合同/合规/检索的 final_delivery 逻辑相似但字段不同；可抽象为统一交付框架。',
    'why': '文书生成需要持久化历史以支持续写与回溯。',
    'tc': 'Markdown 组装 + HistoryStore。',
    'op': '支持 Word/PDF 导出、版本管理。',
    'iv': [qa_block('HistoryStore 在文书生成中的作用？', '持久化生成历史，支持用户后续续写、回溯、对比不同版本。')]
}

# 案例检索 / 法规查询 / LLM兜底
D['ov_case_search'] = {
    't': 'case_search 案例检索', 'i': '⚖️', 'y': 'green',
    'f': '用户意图为 case_search 时，挂载 cases 数据源，通过检索子图单独检索裁判案例。',
    'flow': '二次路由 → 案例检索（复用检索子图，skip_fusion=true 单源直查）→ 风险聚合 → 最终交付。',
    'reuse': '复用检索 5 节点子图 + 风险聚合 + 最终交付。',
    'why': '专项案例查询与通用检索共享召回逻辑，但只挂载案例源。',
    'tc': '检索子图 + mounted_sources=[cases]。',
    'op': '增加案例筛选与裁判要旨生成。',
    'iv': [qa_block('案例检索和法律检索有什么区别？', '法律检索全源融合；案例检索只挂载 cases 源，skip_fusion 保留原始顺序。')]
}

D['ov_law_query'] = {
    't': 'law_query 法规查询', 'i': '📜', 'y': 'green',
    'f': '用户意图为 law_query 时，挂载 laws 数据源，通过检索子图单独检索法律法规。',
    'flow': '二次路由 → 法规查询（复用检索子图，skip_fusion=true 单源直查）→ 风险聚合 → 最终交付。',
    'reuse': '复用检索 5 节点子图 + 风险聚合 + 最终交付。',
    'why': '专项法条查询只挂载 laws 源，返回更精确。',
    'tc': '检索子图 + mounted_sources=[laws]。',
    'op': '增加法条时效性校验、新旧法关系提示。',
    'iv': [qa_block('法规查询为什么要 skip_fusion？', '单源直查场景不需要多源 RRF 融合，保留原始排序即可。')]
}

D['ov_llm_direct'] = {
    't': 'llm_direct_out LLM兜底', 'i': '🆘', 'y': 'danger',
    'f': '其他意图或未识别意图走 LLM 直接回答，输出写入 output。',
    'flow': 'intent_router → LLM兜底 → END。',
    'reuse': '独立兜底节点。',
    'why': '保证任何输入都能给出友好响应，不直接报错。',
    'tc': '通用 LLM + 安全 Prompt。',
    'op': '增加兜底置信度阈值，过低时引导用户重新描述需求。',
    'iv': [qa_block('LLM兜底如何保证安全？', '限制输出领域、过滤敏感内容、添加免责声明。')]
}

# 终点
D['ov_end_main'] = {
    't': 'END 主流程结束', 'i': '🏁', 'y': 'end',
    'f': '合同审核/合规审查/法律检索/案例检索/法规查询等链路的共享终点。',
    'flow': 'final_delivery 写入 output 后 → END。',
    'reuse': '多条链路共享。',
    'why': '统一终点便于日志归档与结果返回。',
    'tc': 'LangGraph END 常量。',
    'op': '可增加输出后处理（审计日志落库、消息通知）。',
    'iv': [qa_block('LangGraph 如何返回结果给前端？', 'graph.invoke(state) 返回完整状态，前端取 result.get("output") 或 final_report_markdown。')]
}

D['ov_end_xhs'] = {
    't': 'END 小红书终点', 'i': '🏁', 'y': 'end',
    'f': '小红书发布链路终点。',
    'flow': 'Markdown 存档后 → END。',
    'reuse': '小红书链路专用。',
    'why': '小红书链路独立，有独立终点。',
    'tc': 'LangGraph END。',
    'op': '可增加发布成功后回调通知。',
    'iv': []
}

D['ov_end_qa'] = {
    't': 'END 问答终点', 'i': '🏁', 'y': 'end',
    'f': '法律问答链路终点。',
    'flow': '答案生成 → END。',
    'reuse': '法律问答与 LLM 兜底共享。',
    'why': '问答链路独立结束。',
    'tc': 'LangGraph END。',
    'op': '增加问答日志记录。',
    'iv': []
}

D['ov_end_docgen'] = {
    't': 'END 文书生成终点', 'i': '🏁', 'y': 'end',
    'f': '文书生成链路终点。',
    'flow': '文书最终交付 → END。',
    'reuse': '文书生成专用。',
    'why': '文书生成链路独立结束。',
    'tc': 'LangGraph END。',
    'op': '增加版本归档。',
    'iv': []
}

D['ov_end_other'] = {
    't': 'END 其他/LLM兜底终点', 'i': '🏁', 'y': 'end',
    'f': '不属于合同/合规/检索/问答/文书/小红书等明确意图的请求，由 LLM 直接回答后结束。',
    'flow': 'intent_router/二次路由判定为「其他」→ LLM 直答 → END。',
    'reuse': '与法律问答共享 END 出口。',
    'why': '兜底路径保证任何请求都有响应，不丢请求。',
    'tc': 'LangGraph END。',
    'op': '可增加兜底日志与人工接管提示。',
    'iv': [qa_block('为什么需要 LLM 兜底？', '意图路由无法全覆盖所有请求，兜底路径保证系统始终有响应，避免请求丢失。')]
}

# ── CONTRACT REVIEW 子图专用节点 ──
D['ct_start'] = dict(D['ov_start'], **{
    't': 'START 合同审核入口', 'flow': '用户上传合同 → 进入合同审核子图。', 'reuse': '合同审核链路起点。'
})
D['ct_doc'] = dict(D['ov_doc'], **{'flow': '合同审核起点 → 文档提取 → 甲乙方识别。'})
D['ct_party'] = dict(D['ov_party'], **{'flow': '文档提取 → 甲乙方识别 → 合同分类。'})
D['ct_classify'] = dict(D['ov_classify'], **{'flow': '甲乙方识别 → 合同分类 → 条款切分。'})
D['ct_clause'] = dict(D['ov_clause'], **{'flow': '合同分类 → 条款切分 → 数值抽取。'})
D['ct_numeric_ext'] = dict(D['ov_numeric_ext'], **{'flow': '条款切分 → 数值抽取 → 检索子图。'})
D['ct_ret_intent'] = dict(D['ov_ret_intent']); D['ct_ret_base'] = dict(D['ov_ret_base']); D['ct_ret_enhance'] = dict(D['ov_ret_enhance']); D['ct_ret_fusion'] = dict(D['ov_ret_fusion']); D['ct_ret_output'] = dict(D['ov_ret_output'])
D['ct_contract_ai'] = dict(D['ov_contract_ai'], **{'flow': '检索结果 → 合同审核AI → 合规审查。'})
D['ct_compliance'] = dict(D['ov_compliance'], **{'flow': '合同审核AI → 合规审查 → 冲突消解。'})
D['ct_conflict'] = dict(D['ov_conflict'], **{'flow': '合同审核AI + 合规审查 → 冲突消解 → 数值校验。'})
D['ct_numeric_val'] = dict(D['ov_numeric_val'], **{'flow': '冲突消解 → 数值校验 → 甲乙方识别（后处理）→ 资信查询。'})
D['ct_party_post'] = dict(D['ov_party_post'], **{'flow': '数值校验 → 甲乙方识别 → 资信查询。'})
D['ct_credit'] = dict(D['ov_credit'], **{'flow': '甲乙方识别 → 资信查询 → 风险聚合。'})
D['ct_agg'] = dict(D['ov_aggregate'], **{'flow': '资信查询 → 风险聚合 → 最终交付。'})
D['ct_delivery'] = dict(D['ov_delivery'], **{'flow': '风险聚合 → 最终交付 → END。'})
D['ct_end'] = {'t': 'END 合同审核结束', 'i': '🏁', 'y': 'end', 'f': '合同审核链路终点。', 'flow': '最终交付 → END。', 'reuse': '', 'why': '', 'tc': '', 'op': '', 'iv': []}

# ── COMPLIANCE 子图专用节点 ──
D['cp_start'] = {'t': 'START 合规审查入口', 'i': '🚀', 'y': 'start', 'f': '用户选择合规审查或合同审核触发合规子调用。', 'flow': '进入合规审查子图 → 文档提取。', 'reuse': '', 'why': '', 'tc': '', 'op': '', 'iv': []}
D['cp_doc'] = dict(D['ov_doc'], **{'flow': '合规审查起点 → 文档提取 → 甲乙方识别。'})
D['cp_party'] = dict(D['ov_party'], **{'flow': '文档提取 → 甲乙方识别 → 合同分类。'})
D['cp_classify'] = dict(D['ov_classify'], **{'flow': '甲乙方识别 → 合同分类 → 条款切分。'})
D['cp_clause'] = dict(D['ov_clause'], **{'flow': '合同分类 → 条款切分 → 数值抽取。'})
D['cp_numeric_ext'] = dict(D['ov_numeric_ext'], **{'flow': '条款切分 → 数值抽取 → 检索子图。'})
D['cp_ret_intent'] = dict(D['ov_ret_intent']); D['cp_ret_base'] = dict(D['ov_ret_base']); D['cp_ret_enhance'] = dict(D['ov_ret_enhance']); D['cp_ret_fusion'] = dict(D['ov_ret_fusion']); D['cp_ret_output'] = dict(D['ov_ret_output'])
D['cp_compliance'] = dict(D['ov_compliance'], **{
    'flow': '检索输出 → 合规审查(7大领域) → 冲突消解（独立审查场景：仅保留 compliance_risk_items，pass-through）。',
    'reuse': 'compliance_review_node 本页为独立触发（task_type=compliance），不调用合同审核；也可被合同审核链路作为必经子调用。'})
D['cp_conflict'] = dict(D['ov_conflict'], **{'flow': '合规审查输出 → 冲突消解（独立场景：仅保留 compliance_risk_items，pass-through）→ 数值校验。'})
D['cp_numeric_val'] = dict(D['ov_numeric_val'], **{'flow': '冲突消解 → 数值校验 → 资信查询。'})
D['cp_credit'] = dict(D['ov_credit'], **{'flow': '数值校验 → 资信查询 → 风险聚合。'})
D['cp_agg'] = dict(D['ov_aggregate'], **{'flow': '资信查询 → 风险聚合 → 最终交付。'})
D['cp_delivery'] = dict(D['ov_delivery'], **{'flow': '风险聚合 → 最终交付 → END。'})
D['cp_end'] = {'t': 'END 合规审查结束', 'i': '🏁', 'y': 'end', 'f': '合规审查链路终点。', 'flow': '最终交付 → END。', 'reuse': '', 'why': '', 'tc': '', 'op': '', 'iv': []}

# ── RETRIEVAL 子图专用节点 ──
D['rt_start'] = {'t': 'intent_router → 法律检索', 'i': '🧭', 'y': 'decision', 'f': 'intent_router 识别 legal_research 后直接路由到检索子图入口。', 'flow': 'intent_router → 本节点 → 检索意图分解。', 'reuse': '复用 intent_router。', 'why': '', 'tc': '', 'op': '', 'iv': []}
D['rt_ret_intent'] = dict(D['ov_ret_intent']); D['rt_ret_base'] = dict(D['ov_ret_base']); D['rt_ret_enhance'] = dict(D['ov_ret_enhance']); D['rt_ret_fusion'] = dict(D['ov_ret_fusion']); D['rt_ret_output'] = dict(D['ov_ret_output'])
D['rt_party'] = dict(D['ov_party_post'], **{'flow': '检索结果输出 → 甲乙方识别 → 资信查询。'})
D['rt_credit'] = dict(D['ov_credit'], **{'flow': '甲乙方识别 → 资信查询 → 风险聚合。'})
D['rt_agg'] = dict(D['ov_aggregate'], **{'flow': '资信查询 → 风险聚合 → 最终交付。'})
D['rt_delivery'] = dict(D['ov_delivery'], **{'flow': '风险聚合 → 最终交付 → END。'})
D['rt_end'] = {'t': 'END 法律检索结束', 'i': '🏁', 'y': 'end', 'f': '法律检索链路终点。', 'flow': '最终交付 → END。', 'reuse': '', 'why': '', 'tc': '', 'op': '', 'iv': []}

# ── QA 子图专用节点（使用 ov 数据但改标题/流程） ──
for k in ['ov_qa_extract','ov_qa_match','ov_qa_cypher','ov_qa_check','ov_qa_run','ov_qa_answer']:
    qk = k.replace('ov_qa_', 'qa_')
    D[qk] = dict(D[k])  # copy
D['qa_start'] = {'t': 'START 法律问答入口', 'i': '🚀', 'y': 'start', 'f': '用户输入自然语言法律问题。', 'flow': 'intent_router → 法律问答入口 → 实体抽取。', 'reuse': '', 'why': '', 'tc': '', 'op': '', 'iv': []}
D['qa_end'] = {'t': 'END 法律问答结束', 'i': '🏁', 'y': 'end', 'f': '法律问答链路终点。', 'flow': '答案生成 → END。', 'reuse': '', 'why': '', 'tc': '', 'op': '', 'iv': []}

# ── XIAOHONGSHU 子图专用节点 ──
for k in ['ov_xhs_text','ov_xhs_img','ov_xhs_check','ov_xhs_pub','ov_xhs_md']:
    xk = k.replace('ov_xhs_','xhs_')
    if xk == 'xhs_text': xk = 'xhs_copy'
    if xk == 'xhs_md': xk = 'xhs_report'
    D[xk] = dict(D[k])
D['xhs_start'] = dict(D['ov_xhs_intent'], **{'t': 'xiaohongshu_publish_intent 前置过滤', 'flow': 'START → 本节点；[小红书意图] → 文案生成；[非小红书意图] → intent_router。', 'reuse': '所有请求必经。'})
D['xhs_end'] = {'t': 'END 小红书发布结束', 'i': '🏁', 'y': 'end', 'f': '小红书发布链路终点。', 'flow': 'Markdown存档/检查不通过 → END。', 'reuse': '', 'why': '', 'tc': '', 'op': '', 'iv': []}

# ── DOCGEN 子图专用节点 ──
D['docgen_start'] = dict(D['ov_docgen_start'])
D['docgen_analyze'] = dict(D['ov_docgen_analyze'])
D['docgen_template'] = dict(D['ov_docgen_template'])
D['docgen_fill'] = dict(D['ov_docgen_fill'])
D['docgen_validate'] = dict(D['ov_docgen_validate'])
D['docgen_risk'] = dict(D['ov_docgen_risk'])
D['docgen_cases'] = dict(D['ov_docgen_cases'])
D['docgen_delivery'] = dict(D['ov_docgen_delivery'])
D['docgen_end'] = {'t': 'END 文书生成结束', 'i': '🏁', 'y': 'end', 'f': '文书生成链路终点。', 'flow': '最终交付 → END。', 'reuse': '', 'why': '', 'tc': '', 'op': '', 'iv': []}

print('D entries:', len(D))

# ═══════════════════════════════════════════════════════
# CHARTS 图表定义：节点位置与连边
# ═══════════════════════════════════════════════════════
CHARTS = {}

# 节点尺寸常量
W_NODE = 180
H_NODE = 44
W_NODE_L = 200
H_NODE_L = 50
W_REUSE = 180


def N(x, y, w=W_NODE, h=H_NODE, **kw):
    return {'x': x, 'y': y, 'w': w, 'h': h, **kw}


# ── OVERVIEW 总架构图 ──
CHARTS['overview'] = {
    'svgId': 'svg-overview', 'viewBox': '0 0 1800 1320',
    'nodes': [
        # 入口层（中轴）
        N(810, 20, 180, 40, id='ov_start'),
        N(810, 75, 180, 44, id='ov_xhs_intent'),
        N(785, 135, 230, 50, id='ov_router'),
        N(785, 205, 230, 50, id='ov_credit_precheck'),
        N(785, 275, 230, 50, id='ov_second_router'),

        # 小红书分支（左侧）
        N(80, 135, 160, 40, id='ov_xhs_text'),
        N(80, 195, 160, 40, id='ov_xhs_img'),
        N(80, 255, 160, 40, id='ov_xhs_check'),
        N(80, 315, 160, 40, id='ov_xhs_pub'),
        N(80, 375, 160, 40, id='ov_xhs_md'),
        N(110, 435, 100, 35, id='ov_end_xhs'),

        # 共享预处理 5 节点（中轴）
        N(810, 360, 180, 40, id='ov_doc'),
        N(810, 415, 180, 40, id='ov_party'),
        N(810, 470, 180, 40, id='ov_classify'),
        N(810, 525, 180, 40, id='ov_clause'),
        N(810, 580, 180, 40, id='ov_numeric_ext'),

        # 检索子图（右侧 subgraph）
        N(1180, 360, 170, 40, id='ov_ret_intent'),
        N(1180, 415, 170, 40, id='ov_ret_base'),
        N(1180, 470, 170, 40, id='ov_ret_enhance'),
        N(1180, 525, 170, 40, id='ov_ret_fusion'),
        N(1180, 580, 170, 40, id='ov_ret_output'),

        # 合同审核/合规审查并行 + 后处理（中轴）
        N(795, 655, 210, 48, id='ov_contract_ai'),
        N(795, 720, 210, 48, id='ov_compliance'),
        N(795, 785, 210, 48, id='ov_conflict'),
        N(810, 850, 180, 40, id='ov_numeric_val'),
        N(810, 905, 180, 40, id='ov_party_post'),
        N(810, 960, 180, 40, id='ov_credit'),
        N(810, 1015, 180, 44, id='ov_aggregate'),
        N(810, 1075, 180, 44, id='ov_delivery'),
        N(785, 1140, 230, 44, id='ov_end_main'),

        # 右侧独立链路（从二次路由分出）
        # 法律问答
        N(1470, 360, 170, 40, id='ov_qa_extract'),
        N(1470, 415, 170, 40, id='ov_qa_match'),
        N(1470, 470, 170, 40, id='ov_qa_cypher'),
        N(1470, 525, 170, 40, id='ov_qa_check'),
        N(1470, 580, 170, 40, id='ov_qa_run'),
        N(1470, 635, 170, 40, id='ov_qa_answer'),
        N(1505, 695, 100, 35, id='ov_end_qa'),

        # 文书生成
        N(1470, 770, 170, 40, id='ov_docgen_analyze'),
        N(1470, 825, 170, 40, id='ov_docgen_template'),
        N(1470, 880, 170, 40, id='ov_docgen_fill'),
        N(1470, 935, 170, 40, id='ov_docgen_validate'),
        N(1470, 990, 170, 40, id='ov_docgen_risk'),
        N(1470, 1045, 170, 40, id='ov_docgen_cases'),
        N(1470, 1100, 170, 40, id='ov_docgen_delivery'),
        N(1505, 1160, 100, 35, id='ov_end_docgen'),

        # 案例检索 / 法规查询 / LLM兜底
        N(1180, 655, 170, 40, id='ov_case_search'),
        N(1180, 720, 170, 40, id='ov_law_query'),
        N(1180, 785, 170, 40, id='ov_llm_direct'),
        N(1215, 845, 100, 35, id='ov_end_other'),
    ],
    'edges': [
        # 入口
        {'from': 'ov_start', 'to': 'ov_xhs_intent', 'type': 'normal'},
        {'from': 'ov_xhs_intent', 'to': 'ov_router', 'type': 'normal', 'label': '非小红书'},
        {'from': 'ov_router', 'to': 'ov_credit_precheck', 'type': 'normal'},
        {'from': 'ov_credit_precheck', 'to': 'ov_second_router', 'type': 'normal'},

        # 小红书分支
        {'from': 'ov_xhs_intent', 'to': 'ov_xhs_text', 'type': 'branch', 'label': '小红书意图'},
        {'from': 'ov_xhs_text', 'to': 'ov_xhs_img', 'type': 'normal'},
        {'from': 'ov_xhs_img', 'to': 'ov_xhs_check', 'type': 'normal'},
        {'from': 'ov_xhs_check', 'to': 'ov_xhs_pub', 'type': 'success', 'label': '通过'},
        {'from': 'ov_xhs_check', 'to': 'ov_end_xhs', 'type': 'danger', 'label': '不通过'},
        {'from': 'ov_xhs_pub', 'to': 'ov_xhs_md', 'type': 'normal'},
        {'from': 'ov_xhs_md', 'to': 'ov_end_xhs', 'type': 'normal'},

        # 二次路由 → 合同/合规预处理
        {'from': 'ov_second_router', 'to': 'ov_doc', 'type': 'branch', 'label': '合同/合规'},
        {'from': 'ov_doc', 'to': 'ov_party', 'type': 'normal'},
        {'from': 'ov_party', 'to': 'ov_classify', 'type': 'normal'},
        {'from': 'ov_classify', 'to': 'ov_clause', 'type': 'normal'},
        {'from': 'ov_clause', 'to': 'ov_numeric_ext', 'type': 'normal'},

        # 检索提前：数值抽取 → 检索子图
        {'from': 'ov_numeric_ext', 'to': 'ov_ret_intent', 'type': 'normal', 'label': '检索提前'},
        {'from': 'ov_ret_intent', 'to': 'ov_ret_base', 'type': 'normal'},
        {'from': 'ov_ret_base', 'to': 'ov_ret_enhance', 'type': 'normal', 'label': '不足'},
        {'from': 'ov_ret_enhance', 'to': 'ov_ret_fusion', 'type': 'normal'},
        {'from': 'ov_ret_fusion', 'to': 'ov_ret_output', 'type': 'normal'},
        # 检索结果 → 合同审核AI
        {'from': 'ov_ret_output', 'to': 'ov_contract_ai', 'type': 'normal', 'label': '有法条'},

        # 合同/合规/冲突/数值校验/后处理
        {'from': 'ov_contract_ai', 'to': 'ov_compliance', 'type': 'normal', 'label': 'contract_risks'},
        {'from': 'ov_compliance', 'to': 'ov_conflict', 'type': 'normal', 'label': 'compliance_risks'},
        {'from': 'ov_conflict', 'to': 'ov_numeric_val', 'type': 'normal', 'label': '统一风险'},
        {'from': 'ov_numeric_val', 'to': 'ov_party_post', 'type': 'normal'},
        {'from': 'ov_party_post', 'to': 'ov_credit', 'type': 'normal'},
        {'from': 'ov_credit', 'to': 'ov_aggregate', 'type': 'normal'},
        {'from': 'ov_aggregate', 'to': 'ov_delivery', 'type': 'normal'},
        {'from': 'ov_delivery', 'to': 'ov_end_main', 'type': 'normal'},

        # 二次路由 → 法律问答
        {'from': 'ov_second_router', 'to': 'ov_qa_extract', 'type': 'branch', 'label': '法律问答'},
        {'from': 'ov_qa_extract', 'to': 'ov_qa_match', 'type': 'normal'},
        {'from': 'ov_qa_match', 'to': 'ov_qa_cypher', 'type': 'normal'},
        {'from': 'ov_qa_cypher', 'to': 'ov_qa_check', 'type': 'normal'},
        {'from': 'ov_qa_check', 'to': 'ov_qa_run', 'type': 'success', 'label': '通过'},
        {'from': 'ov_qa_check', 'to': 'ov_qa_cypher', 'type': 'loop', 'label': '重试≤3'},
        {'from': 'ov_qa_run', 'to': 'ov_qa_answer', 'type': 'normal'},
        {'from': 'ov_qa_answer', 'to': 'ov_end_qa', 'type': 'normal'},

        # 二次路由 → 文书生成
        {'from': 'ov_second_router', 'to': 'ov_docgen_analyze', 'type': 'branch', 'label': '文书生成'},
        {'from': 'ov_docgen_analyze', 'to': 'ov_docgen_template', 'type': 'normal'},
        {'from': 'ov_docgen_template', 'to': 'ov_docgen_fill', 'type': 'normal'},
        {'from': 'ov_docgen_fill', 'to': 'ov_docgen_validate', 'type': 'normal'},
        {'from': 'ov_docgen_validate', 'to': 'ov_docgen_risk', 'type': 'success', 'label': '通过'},
        {'from': 'ov_docgen_validate', 'to': 'ov_docgen_fill', 'type': 'loop', 'label': '重试≤3'},
        {'from': 'ov_docgen_risk', 'to': 'ov_docgen_cases', 'type': 'normal'},
        {'from': 'ov_docgen_cases', 'to': 'ov_docgen_delivery', 'type': 'normal'},
        {'from': 'ov_docgen_delivery', 'to': 'ov_end_docgen', 'type': 'normal'},

        # 二次路由 → 案例检索 / 法规查询 / LLM兜底
        {'from': 'ov_second_router', 'to': 'ov_case_search', 'type': 'branch', 'label': '案例检索'},
        {'from': 'ov_second_router', 'to': 'ov_law_query', 'type': 'branch', 'label': '法规查询'},
        {'from': 'ov_second_router', 'to': 'ov_llm_direct', 'type': 'branch', 'label': '其他'},
        {'from': 'ov_case_search', 'to': 'ov_end_main', 'type': 'normal', 'label': '复用后处理'},
        {'from': 'ov_law_query', 'to': 'ov_end_main', 'type': 'normal', 'label': '复用后处理'},
        {'from': 'ov_llm_direct', 'to': 'ov_end_other', 'type': 'normal'},
    ]
}

# 注意：overview 中案例检索/法规查询实际应经过检索子图+后处理；此处为总架构概览做了简化，
#       详细流程见 retrieval / case_search / law_query 子图。

# ── CONTRACT REVIEW 合同审核 ──
CHARTS['contract'] = {
    'svgId': 'svg-contract', 'viewBox': '0 0 1200 1200',
    'nodes': [
        N(500, 30, 200, 45, id='ct_start'),
        # 预处理 5 节点（中轴）
        N(500, 95, 200, 42, id='ct_doc'),
        N(500, 150, 200, 42, id='ct_party'),
        N(500, 205, 200, 42, id='ct_classify'),
        N(500, 260, 200, 42, id='ct_clause'),
        N(500, 315, 200, 42, id='ct_numeric_ext'),
        # 检索子图（右侧）
        N(800, 260, 170, 40, id='ct_ret_intent'),
        N(800, 315, 170, 40, id='ct_ret_base'),
        N(800, 370, 170, 40, id='ct_ret_enhance'),
        N(800, 425, 170, 40, id='ct_ret_fusion'),
        N(800, 480, 170, 40, id='ct_ret_output'),
        # 审核/合规/冲突/数值
        N(485, 390, 230, 48, id='ct_contract_ai'),
        N(485, 455, 230, 48, id='ct_compliance'),
        N(485, 520, 230, 48, id='ct_conflict'),
        N(500, 585, 200, 42, id='ct_numeric_val'),
        # 后处理
        N(500, 645, 200, 42, id='ct_party_post'),
        N(500, 705, 200, 42, id='ct_credit'),
        N(500, 770, 200, 46, id='ct_agg'),
        N(500, 835, 200, 46, id='ct_delivery'),
        N(500, 910, 200, 45, id='ct_end'),
    ],
    'edges': [
        {'from': 'ct_start', 'to': 'ct_doc', 'type': 'normal'},
        {'from': 'ct_doc', 'to': 'ct_party', 'type': 'normal'},
        {'from': 'ct_party', 'to': 'ct_classify', 'type': 'normal'},
        {'from': 'ct_classify', 'to': 'ct_clause', 'type': 'normal'},
        {'from': 'ct_clause', 'to': 'ct_numeric_ext', 'type': 'normal'},
        # 检索提前
        {'from': 'ct_numeric_ext', 'to': 'ct_ret_intent', 'type': 'normal', 'label': '检索提前'},
        {'from': 'ct_ret_intent', 'to': 'ct_ret_base', 'type': 'normal'},
        {'from': 'ct_ret_base', 'to': 'ct_ret_enhance', 'type': 'normal', 'label': '不足'},
        {'from': 'ct_ret_enhance', 'to': 'ct_ret_fusion', 'type': 'normal'},
        {'from': 'ct_ret_fusion', 'to': 'ct_ret_output', 'type': 'normal'},
        {'from': 'ct_ret_output', 'to': 'ct_contract_ai', 'type': 'normal', 'label': '有法条'},
        # 审核链路
        {'from': 'ct_contract_ai', 'to': 'ct_compliance', 'type': 'normal', 'label': 'contract_risks'},
        {'from': 'ct_compliance', 'to': 'ct_conflict', 'type': 'normal', 'label': 'compliance_risks'},
        {'from': 'ct_conflict', 'to': 'ct_numeric_val', 'type': 'normal', 'label': '统一风险'},
        {'from': 'ct_numeric_val', 'to': 'ct_party_post', 'type': 'normal'},
        {'from': 'ct_party_post', 'to': 'ct_credit', 'type': 'normal'},
        {'from': 'ct_credit', 'to': 'ct_agg', 'type': 'normal'},
        {'from': 'ct_agg', 'to': 'ct_delivery', 'type': 'normal'},
        {'from': 'ct_delivery', 'to': 'ct_end', 'type': 'success'},
    ]
}

# ── COMPLIANCE 合规审查 ──
CHARTS['compliance'] = {
    'svgId': 'svg-compliance', 'viewBox': '0 0 1200 1200',
    'nodes': [
        N(500, 30, 200, 45, id='cp_start'),
        N(500, 95, 200, 42, id='cp_doc'),
        N(500, 150, 200, 42, id='cp_party'),
        N(500, 205, 200, 42, id='cp_classify'),
        N(500, 260, 200, 42, id='cp_clause'),
        N(500, 315, 200, 42, id='cp_numeric_ext'),
        N(800, 260, 170, 40, id='cp_ret_intent'),
        N(800, 315, 170, 40, id='cp_ret_base'),
        N(800, 370, 170, 40, id='cp_ret_enhance'),
        N(800, 425, 170, 40, id='cp_ret_fusion'),
        N(800, 480, 170, 40, id='cp_ret_output'),
        N(485, 390, 230, 48, id='cp_compliance'),
        N(485, 455, 230, 48, id='cp_conflict'),
        N(500, 520, 200, 42, id='cp_numeric_val'),
        N(500, 580, 200, 42, id='cp_credit'),
        N(500, 645, 200, 46, id='cp_agg'),
        N(500, 710, 200, 46, id='cp_delivery'),
        N(500, 780, 200, 45, id='cp_end'),
    ],
    'edges': [
        {'from': 'cp_start', 'to': 'cp_doc', 'type': 'normal'},
        {'from': 'cp_doc', 'to': 'cp_party', 'type': 'normal'},
        {'from': 'cp_party', 'to': 'cp_classify', 'type': 'normal'},
        {'from': 'cp_classify', 'to': 'cp_clause', 'type': 'normal'},
        {'from': 'cp_clause', 'to': 'cp_numeric_ext', 'type': 'normal'},
        {'from': 'cp_numeric_ext', 'to': 'cp_ret_intent', 'type': 'normal', 'label': '检索提前'},
        {'from': 'cp_ret_intent', 'to': 'cp_ret_base', 'type': 'normal'},
        {'from': 'cp_ret_base', 'to': 'cp_ret_enhance', 'type': 'normal', 'label': '不足'},
        {'from': 'cp_ret_enhance', 'to': 'cp_ret_fusion', 'type': 'normal'},
        {'from': 'cp_ret_fusion', 'to': 'cp_ret_output', 'type': 'normal'},
        {'from': 'cp_ret_output', 'to': 'cp_compliance', 'type': 'normal', 'label': '有法条'},
        {'from': 'cp_compliance', 'to': 'cp_conflict', 'type': 'normal', 'label': 'compliance_risks'},
        {'from': 'cp_conflict', 'to': 'cp_numeric_val', 'type': 'normal', 'label': '独立pass-through'},
        {'from': 'cp_numeric_val', 'to': 'cp_credit', 'type': 'normal'},
        {'from': 'cp_credit', 'to': 'cp_agg', 'type': 'normal'},
        {'from': 'cp_agg', 'to': 'cp_delivery', 'type': 'normal'},
        {'from': 'cp_delivery', 'to': 'cp_end', 'type': 'success'},
    ]
}

# ── RETRIEVAL 法律检索 ──
CHARTS['retrieval'] = {
    'svgId': 'svg-retrieval', 'viewBox': '0 0 1100 750',
    'nodes': [
        N(450, 30, 200, 45, id='rt_start'),
        N(450, 100, 200, 42, id='rt_ret_intent'),
        N(450, 160, 200, 42, id='rt_ret_base'),
        N(450, 220, 200, 42, id='rt_ret_enhance'),
        N(450, 280, 200, 42, id='rt_ret_fusion'),
        N(450, 340, 200, 42, id='rt_ret_output'),
        N(450, 430, 200, 42, id='rt_party'),
        N(450, 490, 200, 42, id='rt_credit'),
        N(450, 555, 200, 46, id='rt_agg'),
        N(450, 620, 200, 46, id='rt_delivery'),
        N(450, 690, 200, 45, id='rt_end'),
    ],
    'edges': [
        {'from': 'rt_start', 'to': 'rt_ret_intent', 'type': 'normal', 'label': '法律检索'},
        {'from': 'rt_ret_intent', 'to': 'rt_ret_base', 'type': 'normal'},
        {'from': 'rt_ret_base', 'to': 'rt_ret_enhance', 'type': 'normal', 'label': '不足'},
        {'from': 'rt_ret_enhance', 'to': 'rt_ret_fusion', 'type': 'normal'},
        {'from': 'rt_ret_fusion', 'to': 'rt_ret_output', 'type': 'normal'},
        {'from': 'rt_ret_output', 'to': 'rt_party', 'type': 'normal', 'label': '复用'},
        {'from': 'rt_party', 'to': 'rt_credit', 'type': 'normal', 'label': '复用'},
        {'from': 'rt_credit', 'to': 'rt_agg', 'type': 'normal', 'label': '复用'},
        {'from': 'rt_agg', 'to': 'rt_delivery', 'type': 'normal', 'label': '复用'},
        {'from': 'rt_delivery', 'to': 'rt_end', 'type': 'success'},
    ]
}

# ── QA 法律问答 ──
CHARTS['qa'] = {
    'svgId': 'svg-qa', 'viewBox': '0 0 1000 700',
    'nodes': [
        N(400, 30, 200, 45, id='qa_start'),
        N(400, 100, 200, 42, id='qa_extract'),
        N(400, 165, 200, 42, id='qa_match'),
        N(400, 230, 200, 42, id='qa_cypher'),
        N(400, 300, 200, 48, id='qa_check'),
        N(400, 375, 200, 42, id='qa_run'),
        N(400, 445, 200, 42, id='qa_answer'),
        N(400, 525, 200, 45, id='qa_end'),
    ],
    'edges': [
        {'from': 'qa_start', 'to': 'qa_extract', 'type': 'normal'},
        {'from': 'qa_extract', 'to': 'qa_match', 'type': 'normal'},
        {'from': 'qa_match', 'to': 'qa_cypher', 'type': 'normal'},
        {'from': 'qa_cypher', 'to': 'qa_check', 'type': 'normal'},
        {'from': 'qa_check', 'to': 'qa_run', 'type': 'success', 'label': '通过'},
        {'from': 'qa_check', 'to': 'qa_cypher', 'type': 'loop', 'label': '重试≤3'},
        {'from': 'qa_run', 'to': 'qa_answer', 'type': 'normal'},
        {'from': 'qa_answer', 'to': 'qa_end', 'type': 'success'},
    ]
}

# ── XIAOHONGSHU 小红书 ──
CHARTS['xiaohongshu'] = {
    'svgId': 'svg-xiaohongshu', 'viewBox': '0 0 1000 650',
    'nodes': [
        N(400, 30, 200, 45, id='xhs_start'),
        N(400, 100, 200, 42, id='xhs_copy'),
        N(400, 165, 200, 42, id='xhs_img'),
        N(400, 235, 200, 48, id='xhs_check'),
        N(400, 310, 200, 42, id='xhs_pub'),
        N(400, 380, 200, 42, id='xhs_report'),
        N(400, 455, 200, 45, id='xhs_end'),
    ],
    'edges': [
        {'from': 'xhs_start', 'to': 'xhs_copy', 'type': 'normal', 'label': '小红书意图'},
        {'from': 'xhs_copy', 'to': 'xhs_img', 'type': 'normal'},
        {'from': 'xhs_img', 'to': 'xhs_check', 'type': 'normal'},
        {'from': 'xhs_check', 'to': 'xhs_pub', 'type': 'success', 'label': '通过'},
        {'from': 'xhs_check', 'to': 'xhs_end', 'type': 'danger', 'label': '不通过'},
        {'from': 'xhs_pub', 'to': 'xhs_report', 'type': 'normal'},
        {'from': 'xhs_report', 'to': 'xhs_end', 'type': 'normal'},
    ]
}

# ── DOCGEN 文书生成 ──
CHARTS['docgen'] = {
    'svgId': 'svg-docgen', 'viewBox': '0 0 1000 850',
    'nodes': [
        N(400, 30, 200, 45, id='docgen_start'),
        N(400, 95, 200, 42, id='docgen_analyze'),
        N(400, 155, 200, 42, id='docgen_template'),
        N(400, 220, 200, 48, id='docgen_fill'),
        N(400, 295, 200, 48, id='docgen_validate'),
        N(400, 375, 200, 42, id='docgen_risk'),
        N(400, 440, 200, 42, id='docgen_cases'),
        N(400, 510, 200, 46, id='docgen_delivery'),
        N(400, 585, 200, 45, id='docgen_end'),
    ],
    'edges': [
        {'from': 'docgen_start', 'to': 'docgen_analyze', 'type': 'normal'},
        {'from': 'docgen_analyze', 'to': 'docgen_template', 'type': 'normal'},
        {'from': 'docgen_template', 'to': 'docgen_fill', 'type': 'normal'},
        {'from': 'docgen_fill', 'to': 'docgen_validate', 'type': 'normal'},
        {'from': 'docgen_validate', 'to': 'docgen_risk', 'type': 'success', 'label': '通过'},
        {'from': 'docgen_validate', 'to': 'docgen_fill', 'type': 'loop', 'label': '重试≤3'},
        {'from': 'docgen_risk', 'to': 'docgen_cases', 'type': 'normal'},
        {'from': 'docgen_cases', 'to': 'docgen_delivery', 'type': 'normal'},
        {'from': 'docgen_delivery', 'to': 'docgen_end', 'type': 'success'},
    ]
}

print('CHARTS:', list(CHARTS.keys()))

# ═══════════════════════════════════════════════════════
# 生成 JS 代码段
# ═══════════════════════════════════════════════════════

def to_js_obj(o, indent=0):
    """把 Python 简单对象序列化为 JS 对象/数组字面量（不换行）"""
    sp = '  ' * indent
    if o is None:
        return 'null'
    if isinstance(o, bool):
        return 'true' if o else 'false'
    if isinstance(o, (int, float)):
        return str(o)
    if isinstance(o, str):
        return "'" + escape_js(o) + "'"
    if isinstance(o, (list, tuple)):
        return '[' + ', '.join(to_js_obj(v, indent) for v in o) + ']'
    if isinstance(o, dict):
        items = []
        for k, v in o.items():
            items.append(sp + "'" + k + "': " + to_js_obj(v, indent + 1))
        return '{' + ', '.join(items) + '}'
    return str(o)


def build_d_js():
    parts = ['    const D = {']
    for k in sorted(D.keys()):
        parts.append("      '" + k + "': " + to_js_obj(D[k]) + ',')
    parts.append('    };')
    return '\n'.join(parts)


def build_charts_js():
    parts = ['    const CHARTS = {']
    for key in CHARTS:
        chart = CHARTS[key]
        parts.append("      " + key + ": {")
        parts.append("        svgId: '" + chart['svgId'] + "', viewBox: '" + chart['viewBox'] + "',")
        # nodes
        parts.append("        nodes: [")
        for n in chart['nodes']:
            s = "          { id: '" + n['id'] + "', x: " + str(n['x']) + ", y: " + str(n['y']) + ", w: " + str(n['w']) + ", h: " + str(n['h']) + " },"
            parts.append(s)
        parts.append("        ],")
        # edges
        parts.append("        edges: [")
        for e in chart['edges']:
            s = "          { from: '" + e['from'] + "', to: '" + e['to'] + "', type: '" + e['type'] + "'"
            if 'label' in e:
                s += ", label: '" + escape_js(e['label']) + "'"
            s += " },"
            parts.append(s)
        parts.append("        ]")
        parts.append("      },")
    parts.append('    };')
    return '\n'.join(parts)


# ═══════════════════════════════════════════════════════
# HTML body 片段
# ═══════════════════════════════════════════════════════
NAV = '''
  <nav class="topnav">
    <div class="brand">⚖️ 法智引擎</div>
    <a href="#sec-index" class="nav-index" style="color:#60c5fa">🏠 首页</a>
    <a href="#sec-qa" style="color:#22d3ee">💬 法律问答</a>
    <a href="#sec-retrieval" style="color:#6ee7b7">🔍 法律检索</a>
    <a href="#sec-xiaohongshu" style="color:#f472b6">📱 小红书发布</a>
    <a href="#sec-contract" style="color:#fdba74">📋 合同审核</a>
    <a href="#sec-compliance" style="color:#c4b5fd">🛡️ 合规审查</a>
    <a href="#sec-docgen" style="color:#a5b4fc">📝 文书生成</a>
    <a href="#sec-arch" style="color:#93c5fd">🏛️ 总架构</a>
    <a href="#sec-reuse" style="color:#7dd3fc">🧩 节点复用</a>
    <a href="#sec-optimization" style="color:#f9a8d4">⚡ 优化分析</a>
    <span class="spacer"></span>
    <span class="stat">LangGraph Multi-Agent · 对齐文字架构 v3.0</span>
    <button id="theme-toggle" onclick="toggleTheme()" title="切换明暗主题"
      style="background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);color:inherit;border-radius:8px;padding:6px 10px;cursor:pointer;font-size:14px;transition:all .2s">🌙</button>
  </nav>
'''

COVER = '''
  <section id="sec-index" class="cover">
    <div>
      <h1>⚖️ 法智引擎</h1>
      <p class="subtitle">LangGraph 多智能体架构 · 节点式流程图（对照文字架构 v3.0）</p>
      <div class="cover-grid">
        <a href="#sec-qa" class="cover-card">
          <div class="cc-tag" style="color:#22d3ee">AGENT 01</div>
          <div class="cc-title" style="color:#67e8f9">💬 法律问答智能体</div>
          <div class="cc-desc">Text-to-Cypher 知识图谱 RAG · 实体抽取 → Neo4j 匹配 → Cypher 生成/校验/执行 → 答案生成 · 校验失败重试≤3次</div>
        </a>
        <a href="#sec-retrieval" class="cover-card">
          <div class="cc-tag" style="color:#34d399">AGENT 02</div>
          <div class="cc-title" style="color:#6ee7b7">🔍 法律检索智能体</div>
          <div class="cc-desc">5节点检索子图（意图分解→基础层→增强→融合→输出）· 被合同审核·合规审查·法律检索·文书生成 4 链路复用 · 质量门禁+北大法宝 MCP 兜底</div>
        </a>
        <a href="#sec-xiaohongshu" class="cover-card">
          <div class="cc-tag" style="color:#f472b6">AGENT 03</div>
          <div class="cc-title" style="color:#f9a8d4">📱 小红书发布智能体</div>
          <div class="cc-desc">START 后前置过滤 · 文案生成 → AI 配图 → 图文检查 → Playwright 自动发布 → Markdown 存档 · 独立链路</div>
        </a>
        <a href="#sec-contract" class="cover-card">
          <div class="cc-tag" style="color:#fb923c">AGENT 04</div>
          <div class="cc-title" style="color:#fdba74">📋 合同审核智能体</div>
          <div class="cc-desc">共享预处理5节点 → 检索提前 → 合同审核AI → 合规审查 → ⚔️冲突消解（合规优先）→ 数值校验 → 资信 → 四路风险聚合 → 交付</div>
        </a>
        <a href="#sec-compliance" class="cover-card">
          <div class="cc-tag" style="color:#a78bfa">AGENT 05</div>
          <div class="cc-title" style="color:#c4b5fd">🛡️ 合规审查智能体</div>
          <div class="cc-desc">独立执行（不调合同审核） · 7 大合规领域刚性审查 · 一票否决 · 冲突消解合规优先 · 与合同审核共享预处理/检索/数值校验/交付链路</div>
        </a>
        <a href="#sec-docgen" class="cover-card">
          <div class="cc-tag" style="color:#818cf8">AGENT 06</div>
          <div class="cc-title" style="color:#a5b4fc">📝 法律文书生成智能体</div>
          <div class="cc-desc">案情分析 → 模板匹配(10种) → RAG 条款填充（复用检索）→ 法条真实性校验（重试≤3次）→ 风险提示 ∥ 类案推荐 → 交付+HistoryStore</div>
        </a>
        <a href="#sec-arch" class="cover-card">
          <div class="cc-tag" style="color:#60c5fa">OVERVIEW</div>
          <div class="cc-title" style="color:#93c5fd">🏛️ 架构总流程图</div>
          <div class="cc-desc">完整节点串联 · 小红书前置过滤 · 企查查预判定 · 二次路由 · 检索提前 · 节点复用关系总览 · 点击节点查看深度解析</div>
        </a>
      </div>
      <div style="margin-top:40px;font-size:13px;color:#5a6a7a;line-height:2">
        <div>💡 <strong style="color:#fcd34d">交互说明：</strong>点击流程图中任意节点 → 弹出深度解析面板（功能·流转·复用·设计理由·技术选型·优化建议·面试Q&A）</div>
        <div>🔄 <strong style="color:#fb923c">循环标识：</strong>橙色虚线箭头 = 重试/回溯循环 · 红色虚线 = 异常/不通过路径 · 绿色 = 成功路径</div>
      </div>
    </div>
  </section>
'''

SECTIONS = [
    ('sec-qa', 'AGENT 01', '💬 法律问答智能体流程图（含循环）', 'cyan',
     '<strong>Text-to-Cypher 知识图谱 RAG 链路</strong> · 实体抽取 → Neo4j 匹配 → Cypher 生成 → Cypher 校验 → Cypher 执行 → 答案生成<br>⚠️ 核心循环：Cypher 语法校验失败 → 重试生成（最多 3 次）；匹配/执行失败 → LLM 直答降级<br>📌 <strong>复用关系：</strong>独立链路，只共享 intent_router；由 intent_router 的 legal_qa_path 进入。',
     'svg-qa', 'qa'),
    ('sec-retrieval', 'AGENT 02', '🔍 法律检索智能体流程图（含循环）', 'green',
     '<strong>5 节点检索子图 + 复用后处理链路</strong> · 意图分解 → 基础层检索 → 增强查询 → 融合排序 → 结果输出 → 甲乙方识别 → 资信查询 → 风险聚合 → 最终交付<br>⚠️ 由 intent_router 识别 legal_research 后直接进入检索子图；检索完成后复用合同/合规的后处理链路<br>📌 <strong>复用关系：</strong>检索 5 节点被合同审核·合规审查·法律检索·文书生成 4 条链路复用；后处理节点 risk_aggregate / party_identify / credit_check / final_delivery 被 3 条链路复用。',
     'svg-retrieval', 'retrieval'),
    ('sec-xiaohongshu', 'AGENT 03', '📱 小红书发布智能体流程图（含循环）', 'pink',
     '<strong>入口前置意图识别 + 独立发布链路</strong> · START → 小红书意图识别 → 文案生成 → 图片生成 → 图文检查 → 自动发布 → Markdown 存档 → END<br>⚠️ 核心循环：图片生成失败 → 无图降级；图文检查不通过 → 直接 END（不发布）<br>📌 <strong>复用关系：</strong>独立链路，入口在 intent_router 之前（前置过滤），不与其他智能体复用业务节点。',
     'svg-xiaohongshu', 'xiaohongshu'),
    ('sec-docgen', 'AGENT 06', '📝 法律文书生成智能体流程图（含循环）', 'indigo',
     '<strong>7 节点串联链路</strong> · 案情分析 → 模板匹配(10 种) → 条款填充（🔄复用检索 search()）→ 法条真实性校验（3 级）→ 风险提示 ∥ 类案推荐 → 最终交付+HistoryStore<br>⚠️ 核心循环：法条校验发现 fabricated/rewrite → 回退到条款填充重试（≤3 次）<br>📌 <strong>复用关系：</strong>条款填充调用检索智能体 search()；最终交付与合同/合规/检索共用交付框架。',
     'svg-docgen', 'docgen'),
    ('sec-contract', 'AGENT 04', '📋 合同审核智能体流程图（含循环）', 'orange',
     '<strong>检索提前 · 双重审查 · 合规优先</strong> · 文档提取 → 甲乙方识别 → 合同分类 → 条款切分 → 数值抽取 → 🔄检索子图（5 节点，提前） → 合同审核AI（有法条） → 合规审查（有法规） → ⚔️冲突消解（合规优先） → 数值校验 → 甲乙方识别（后处理） → 资信查询 → 风险聚合 → 最终交付<br>⚠️ 🛡️ 合规审查为合同审核<strong>必经子调用</strong>；合规结论刚性不可降级<br>📌 <strong>复用关系：</strong>检索 5 节点 / doc_extract / party_identify / contract_classify / clause_split / numeric_extract / contract_ai_review / compliance_review / conflict_resolution / numeric_validate / credit_check / risk_aggregate / final_delivery 均被多条链路复用。',
     'svg-contract', 'contract'),
    ('sec-compliance', 'AGENT 05', '🛡️ 合规审查智能体流程图（独立链路）', 'purple',
     '<strong>独立执行 · 检索提前 · 合规刚性不降级</strong> · 文档提取 → 甲乙方识别(共享预处理) → 合同分类 → 条款切分 → 数值抽取 → 🔄检索子图（5 节点，提前） → 合规审查（7 大领域：强制规定/数据合规/反垄断/税务/劳动/行业准入/政府采购） → ⚔️冲突消解（独立场景：仅保留 compliance_risk_items，pass-through；合规优先一票否决） → 数值校验 → 资信查询 → 风险聚合 → 最终交付<br>⚠️ 本页为独立合规审查链路：合规律师只回答"是否违法"，<strong>不调用合同审核</strong>；若用户既要合规又要商业分析，应走合同审核链路（合同审核会作为子调用调用本节点）<br>📌 <strong>复用关系：</strong>与合同审核共享完整预处理、检索、冲突消解、数值校验、资信、风险聚合、交付链路；compliance_review_node 本页独立触发，也可被合同审核链路作为必经子调用。',
     'svg-compliance', 'compliance'),
    ('sec-arch', 'OVERVIEW', '🏛️ 架构总流程图（含循环）', 'blue',
     'START → 小红书前置识别 → intent_router → 企查查预判定 → 二次路由 → 共享 5 节点预处理 → 🔄检索提前（5 节点子图，被 4 链路复用） → 合同审核AI ∥ 合规审查 → ⚔️冲突消解（合规优先） → 数值校验 → 资信查询 → 风险聚合（四路） → 最终交付；另有 法律问答（KG RAG） / 文书生成 / 案例检索 / 法规查询 / LLM兜底 并行独立链路。<br>⚠️ 本图展示完整节点串联与复用关系，含 Cypher 校验重试环 / 法条校验重试环 / 检索质量门禁重试环。<br>📌 <strong>节点复用总览：</strong>检索 5 节点（合同+合规+检索+文书）；doc_extract / party_identify / classify / clause_split / numeric_extract（合同+合规）；contract_ai_review / compliance_review / conflict_resolution / numeric_validate（合同+合规）；credit_check / risk_aggregate / final_delivery（合同+合规+检索）。',
     'svg-overview', 'overview'),
]


def build_section(sec_id, tag, title, color, desc, svg_id, chart_key):
    ph = 'ph-' + color
    return f'''
  <section id="{sec_id}">
    <div class="sec-head">
      <span class="sec-num {ph}">{tag}</span>
      <h2 style="color:{ {'cyan':'#67e8f9','green':'#6ee7b7','pink':'#f9a8d4','indigo':'#a5b4fc','orange':'#fdba74','purple':'#c4b5fd','blue':'#93c5fd'}.get(color,'#93c5fd') }">{title}</h2>
    </div>
    <p class="sec-desc">{desc}</p>
    <div class="hint-bar">💡 点击任意节点查看深度解析（功能说明·流转关系·节点复用·设计理由·技术选型·优化建议·面试Q&A）</div>
    <div class="svg-wrap"><svg id="{svg_id}"></svg></div>
  </section>
'''


REUSE_SECTION = '''
  <section id="sec-reuse">
    <div class="sec-head">
      <span class="sec-num ph-blue">SUMMARY</span>
      <h2 style="color:#93c5fd">🧩 总架构协作与节点复用汇总</h2>
    </div>
    <p class="sec-desc">「法智引擎」由 7 大智能体组成，底层通过 <strong>共享预处理 + 检索子图复用 + 冲突消解 + 风险聚合</strong> 实现降本增效。<br>下方汇总各智能体如何协作，以及哪些节点被多条链路复用（这是本系统架构设计的核心）。</p>
    <div class="hint-bar">🔄 <strong>复用节点（被多条链路共享）</strong>：检索 5 节点 · doc_extract / party_identify / contract_classify / clause_split / numeric_extract · contract_ai_review / compliance_review / conflict_resolution / numeric_validate · credit_check · risk_aggregate · final_delivery</div>
    <table style="width:100%;border-collapse:collapse;margin-top:14px;font-size:13px;line-height:1.6">
      <thead>
        <tr style="background:rgba(148,163,184,0.15)">
          <th style="border:1px solid #94a3b8;padding:8px 10px;text-align:left;font-weight:700">复用节点 / 子图</th>
          <th style="border:1px solid #94a3b8;padding:8px 10px;text-align:left;font-weight:700">被哪些链路复用</th>
          <th style="border:1px solid #94a3b8;padding:8px 10px;text-align:left;font-weight:700">作用</th>
        </tr>
      </thead>
      <tbody>
        <tr><td style="border:1px solid #94a3b8;padding:8px 10px"><strong>检索 5 节点子图</strong><br>intent_decompose → base_layer → enhance → fusion → output</td><td style="border:1px solid #94a3b8;padding:8px 10px">合同审核 · 合规审查 · 法律检索 · 文书生成</td><td style="border:1px solid #94a3b8;padding:8px 10px">统一召回法条/类案/行业标准/司法解释；挂载层由 mounted_sources 控制</td></tr>
        <tr><td style="border:1px solid #94a3b8;padding:8px 10px"><strong>共享预处理 5 节点</strong><br>doc_extract / party_identify / contract_classify / clause_split / numeric_extract</td><td style="border:1px solid #94a3b8;padding:8px 10px">合同审核 · 合规审查</td><td style="border:1px solid #94a3b8;padding:8px 10px">解析文档、识别主体、分类、切条款、抽数值</td></tr>
        <tr><td style="border:1px solid #94a3b8;padding:8px 10px"><strong>双重审查 + 冲突消解</strong><br>contract_ai_review / compliance_review / conflict_resolution / numeric_validate</td><td style="border:1px solid #94a3b8;padding:8px 10px">合同审核 · 合规审查</td><td style="border:1px solid #94a3b8;padding:8px 10px">商业立场审查 + 法律底线审查 + 合规优先统一裁决 + 数值校验</td></tr>
        <tr><td style="border:1px solid #94a3b8;padding:8px 10px"><strong>企查查资信查询</strong><br>credit_check</td><td style="border:1px solid #94a3b8;padding:8px 10px">合同审核 · 合规审查 · 法律检索</td><td style="border:1px solid #94a3b8;padding:8px 10px">3-tier 降级（MCP Bearer → AppKey+MD5 → Mock）资信查询</td></tr>
        <tr><td style="border:1px solid #94a3b8;padding:8px 10px"><strong>四路风险聚合</strong><br>risk_aggregate</td><td style="border:1px solid #94a3b8;padding:8px 10px">合同审核 · 合规审查 · 法律检索</td><td style="border:1px solid #94a3b8;padding:8px 10px">合并合同+合规+数值+资信风险；合规 critical 不可降级</td></tr>
        <tr><td style="border:1px solid #94a3b8;padding:8px 10px"><strong>最终交付</strong><br>final_delivery</td><td style="border:1px solid #94a3b8;padding:8px 10px">合同审核 · 合规审查 · 法律检索</td><td style="border:1px solid #94a3b8;padding:8px 10px">统一组装 Markdown 报告 / 标红 PDF / 修订版合同 / 审计日志</td></tr>
        <tr><td style="border:1px solid #94a3b8;padding:8px 10px"><strong>Neo4j KG</strong><br>法律问答 6 节点</td><td style="border:1px solid #94a3b8;padding:8px 10px">仅法律问答</td><td style="border:1px solid #94a3b8;padding:8px 10px">知识图谱 RAG，Text-to-Cypher 推理</td></tr>
        <tr><td style="border:1px solid #94a3b8;padding:8px 10px"><strong>HistoryStore</strong><br>文书生成</td><td style="border:1px solid #94a3b8;padding:8px 10px">仅文书生成</td><td style="border:1px solid #94a3b8;padding:8px 10px">持久化生成历史，支持续写与回溯</td></tr>
      </tbody>
    </table>
    <div class="hint-bar" style="margin-top:16px">🧭 <strong>协作总链路</strong>：START → 小红书前置识别 → intent_router → 企查查预判定 → 二次路由 → 共享预处理 5 节点 → 检索提前（5 节点，被 4 链路复用） → 合同审核AI ∥ 合规审查 → 冲突消解（合规优先） → 数值校验 → 资信查询 → 风险聚合（四路） → 最终交付；法律问答（KG RAG） / 文书生成 / 案例检索 / 法规查询 / LLM兜底 并行独立。</div>
  </section>
'''

OPTIMIZATION_SECTION = '''
  <section id="sec-optimization">
    <div class="sec-head">
      <span class="sec-num ph-pink">OPTIMIZATION</span>
      <h2 style="color:#f9a8d4">⚡ 架构优化分析与面试要点</h2>
    </div>
    <p class="sec-desc">对照 <code>docs/flowcharts_文字/</code> 最新架构，对设计中的关键点进行优化建议 · 每条含问题分析+解决方案+成本收益 · 面试高频问题与回答思路</p>
    <div id="opt-container"></div>
  </section>
'''

FOOTER = '''
  <footer>
    <div style="margin-bottom:8px">
      <a href="#sec-index">🏠 返回顶部</a> | <a href="#sec-qa">法律问答</a> | <a href="#sec-retrieval">法律检索</a> | <a href="#sec-xiaohongshu">小红书发布</a> | <a href="#sec-contract">合同审核</a> | <a href="#sec-compliance">合规审查</a> | <a href="#sec-docgen">文书生成</a> | <a href="#sec-arch">总架构</a> | <a href="#sec-reuse">节点复用汇总</a> | <a href="#sec-optimization">优化分析</a>
    </div>
    <div>⚖️ 法智引擎 · LangGraph 多智能体架构流程图（对照文字架构 v3.0）</div>
    <div style="margin-top:4px">设计铁律：AI 做前置审查 · 律师做最终决策 | LangGraph + RAG + Neo4j + FAISS + bge-m3 + MinerU + Pydantic</div>
  </footer>
'''

MODAL = '''
  <div class="modal-overlay" id="modal" onclick="if(event.target===this)closePopup()">
    <div class="modal-content">
      <div class="modal-header">
        <span class="m-icon" id="m-icon">📋</span>
        <h2 id="m-title">节点详情</h2>
        <span class="m-type" id="m-type"></span>
        <button class="modal-close" onclick="closePopup()">✕</button>
      </div>
      <div class="modal-body" id="m-body"></div>
    </div>
  </div>
'''


# ═══════════════════════════════════════════════════════
# 校验：每个 chart 的 node id 必须在 D 中有弹窗数据；edge 的 from/to 必须存在；无重复 id；无矩形重叠
# ═══════════════════════════════════════════════════════
errors = []
for key, chart in CHARTS.items():
    ids = []
    for n in chart['nodes']:
        ids.append(n['id'])
        if n['id'] not in D:
            errors.append('[%s] 节点 %s 缺少 D 弹窗数据' % (key, n['id']))
    dup = [x for x in set(ids) if ids.count(x) > 1]
    if dup:
        errors.append('[%s] 重复节点 id: %s' % (key, dup))
    idset = set(ids)
    for e in chart['edges']:
        if e['from'] not in idset:
            errors.append('[%s] 边 from=%s 不存在' % (key, e['from']))
        if e['to'] not in idset:
            errors.append('[%s] 边 to=%s 不存在' % (key, e['to']))
    # 矩形重叠检查（避免文字被遮挡）
    ns = chart['nodes']
    for a in range(len(ns)):
        for b in range(a + 1, len(ns)):
            na, nb = ns[a], ns[b]
            if (na['x'] < nb['x'] + nb['w'] and na['x'] + na['w'] > nb['x'] and
                    na['y'] < nb['y'] + nb['h'] and na['y'] + na['h'] > nb['y']):
                errors.append('[%s] 节点 %s 与 %s 矩形重叠（文字可能被遮挡）' % (key, na['id'], nb['id']))

if errors:
    print('❌ 校验失败:')
    for e in errors:
        print('  -', e)
    raise SystemExit(1)
else:
    total_nodes = sum(len(c['nodes']) for c in CHARTS.values())
    print('✅ 校验通过: %d 个图表 / %d 个节点 均有弹窗数据，边引用有效，无重复 id' % (len(CHARTS), total_nodes))

# ═══════════════════════════════════════════════════════
# 提取原始渲染引擎（第一个 <script> 块），替换其中的 D / CHARTS
# ═══════════════════════════════════════════════════════
m_script = re.search(r'<script>(.*?)</script>', OLD, flags=re.S)
if not m_script:
    raise SystemExit('未找到原始 <script> 引擎块')
ENGINE = m_script.group(1)


def replace_block(src, prefix, new_block):
    """找到 src 中以 prefix 开头的 JS 对象声明，用 new_block 整体替换（含花括号配对，跳过字符串/注释）"""
    i = src.index(prefix)
    j = src.index('{', i)
    depth = 0
    k = j
    n = len(src)
    while k < n:
        c = src[k]
        if c in ("'", '"', '`'):
            q = c
            k += 1
            while k < n:
                if src[k] == '\\':
                    k += 2
                    continue
                if src[k] == q:
                    k += 1
                    break
                k += 1
            continue
        if c == '/' and k + 1 < n and src[k + 1] == '/':
            while k < n and src[k] != '\n':
                k += 1
            continue
        if c == '/' and k + 1 < n and src[k + 1] == '*':
            k += 2
            while k < n and not (src[k] == '*' and k + 1 < n and src[k + 1] == '/'):
                k += 1
            k += 2
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                end = k + 1
                if end < n and src[end] == ';':
                    end += 1
                return src[:i] + new_block + '\n' + src[end:]
        k += 1
    raise ValueError('括号不匹配: ' + prefix)


d_js = build_d_js()
charts_js = build_charts_js()
ENGINE = replace_block(ENGINE, 'const D =', d_js)
ENGINE = replace_block(ENGINE, 'const CHARTS =', charts_js)

# ═══════════════════════════════════════════════════════
# 组装完整 HTML（保留原 CSS + 引擎，重写 D/CHARTS/body）
# ═══════════════════════════════════════════════════════
HEAD = (
    '<!DOCTYPE html>\n'
    '<html lang="zh-CN">\n'
    '<head>\n'
    '<meta charset="UTF-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
    '<title>法智引擎 · LangGraph 多智能体节点式流程图</title>\n'
)
sections_html = ''.join(build_section(*s) for s in SECTIONS)
BODY = (
    NAV + '\n' + COVER + '\n' + sections_html + '\n'
    + REUSE_SECTION + '\n' + OPTIMIZATION_SECTION + '\n' + FOOTER + '\n' + MODAL + '\n'
)
HTML = (
    HEAD + CSS + '\n</head>\n<body>\n'
    + BODY
    + '<script>\n' + ENGINE + '\n</script>\n'
    + '</body>\n</html>\n'
)

import shutil as _shutil
_shutil.copy2(SRC, BAK)
print('已备份原文件:', BAK)

with io.open(OUT, 'w', encoding='utf-8') as f:
    f.write(HTML)
print('✅ 已重写:', OUT, '(', len(HTML), '字符 )')

# ═══════════════════════════════════════════════════════
# JS 语法校验
# ═══════════════════════════════════════════════════════
import subprocess as _sp
node_path = r'C:\Users\Monki\.workbuddy\binaries\node\versions\22.22.2\node.exe'
script_check = OUT + '.check.js'
with io.open(script_check, 'w', encoding='utf-8') as f:
    f.write(ENGINE)
try:
    r = _sp.run([node_path, '--check', script_check], capture_output=True, text=True, timeout=60000)
    if r.returncode == 0:
        print('✅ JS 语法校验通过 (node --check)')
    else:
        print('❌ JS 语法错误:')
        print(r.stderr)
finally:
    try:
        import os as _os
        _os.remove(script_check)
    except Exception:
        pass



