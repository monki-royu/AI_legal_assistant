# -*- coding: utf-8 -*-
"""生成 flowcharts/ 下的两种流程图之一：langgraph 形式（01~07 + index）。
严格依据 flowcharts_文字/ 中的最新架构逻辑（检索提前、冲突消解、四路聚合等）。
保持 style.css 原有风格，新增：节点点击弹窗 + 旁批 + 总架构/节点复用汇总。
"""
import os, json

OUT = r"E:\to_github_project\AI_legal_assistant\docs\flowcharts"

# ---------- 公共：弹窗样式（叠加在 style.css 之上，不改动原风格） ----------
POPUP_CSS = """
/* ===== 节点点击弹窗（新增，不影响原风格） ===== */
.modal-mask{position:fixed;inset:0;background:rgba(5,10,18,.72);z-index:9999;display:none;
  align-items:flex-start;justify-content:center;padding:48px 18px;overflow-y:auto;}
.modal-mask.on{display:flex;animation:mfade .2s ease;}
@keyframes mfade{from{opacity:0}to{opacity:1}}
.modal-box{width:100%;max-width:820px;background:#16213e;border:1px solid rgba(255,255,255,.12);
  border-radius:16px;box-shadow:0 24px 70px rgba(0,0,0,.55);animation:mslide .28s ease;margin-bottom:40px;}
@keyframes mslide{from{transform:translateY(26px);opacity:0}to{transform:translateY(0);opacity:1}}
.m-head{display:flex;align-items:center;gap:12px;padding:18px 22px;border-bottom:1px solid rgba(255,255,255,.08);
  position:sticky;top:0;background:#16213e;border-radius:16px 16px 0 0;z-index:5;}
.m-head .m-ico{font-size:24px;}
.m-head h2{font-size:18px;font-weight:800;flex:1;color:#e2e8f0;}
.m-head .m-tag{font-size:10px;font-weight:700;letter-spacing:1px;padding:3px 10px;border-radius:6px;
  background:rgba(96,197,250,.16);color:#7dd3fc;}
.m-close{width:32px;height:32px;border:none;border-radius:8px;background:rgba(255,255,255,.06);
  color:#8a9aab;font-size:18px;cursor:pointer;}
.m-close:hover{background:rgba(255,255,255,.12);color:#e2e8f0;}
.m-body{padding:8px 22px 22px;max-height:68vh;overflow-y:auto;}
.m-sec{margin:16px 0;padding:14px 16px;border-left:3px solid rgba(96,197,250,.5);
  background:rgba(255,255,255,.025);border-radius:0 10px 10px 0;}
.m-sec h3{font-size:13.5px;font-weight:800;margin-bottom:6px;color:#93c5fd;display:flex;align-items:center;gap:6px;}
.m-sec p,.m-sec li{font-size:13px;line-height:1.85;color:#cbd5e1;}
.m-sec.reuse{border-left-color:#34d399;}.m-sec.reuse h3{color:#6ee7b7;}
.m-sec.why{border-left-color:#fbbf24;}.m-sec.why h3{color:#fcd34d;}
.m-sec.tech{border-left-color:#a78bfa;}.m-sec.tech h3{color:#c4b5fd;}
.m-sec.opt{border-left-color:#fb923c;}.m-sec.opt h3{color:#fdba74;}
.m-sec.iv{border-left-color:#f472b6;}.m-sec.iv h3{color:#f9a8d4;}
.m-sec ol{margin-left:18px;}
/* 旁批（初学者友好） */
.side-note{margin:14px 0;padding:12px 16px;border:1px dashed rgba(251,191,36,.45);
  background:rgba(251,191,36,.05);border-radius:12px;font-size:12.5px;color:#fcd34d;line-height:1.8;}
.side-note b{color:#fde68a;}
/* 复用汇总 */
.reuse-summary{margin-top:56px;padding:28px;border:1px solid rgba(52,211,153,.3);border-radius:16px;
  background:rgba(52,211,153,.04);}
.reuse-summary h2{font-size:20px;color:#6ee7b7;margin-bottom:8px;}
.reuse-summary p{font-size:13px;color:#cbd5e1;line-height:1.9;margin-bottom:10px;}
.reuse-table{width:100%;border-collapse:collapse;margin-top:14px;font-size:12.5px;}
.reuse-table th,.reuse-table td{border:1px solid rgba(255,255,255,.12);padding:9px 12px;text-align:left;vertical-align:top;}
.reuse-table th{background:rgba(255,255,255,.05);color:#e2e8f0;font-weight:700;}
.reuse-table td{color:#cbd5e1;}
.reuse-table .rk{color:#6ee7b7;font-weight:700;white-space:nowrap;}
.click-hint{font-size:11px;color:#fbbf24;margin:6px 0 0;}
"""

# ---------- 公共：弹窗脚本 ----------
POPUP_JS = """
function openNode(id){
  var d=NODE_DATA[id]; if(!d) return;
  document.getElementById('m-ico').textContent=d.ico||'📋';
  document.getElementById('m-title').textContent=d.title||id;
  var tag=document.getElementById('m-tag'); tag.textContent=d.tag||'节点详情';
  var b=document.getElementById('m-body'); b.innerHTML='';
  function add(cls,h,html){var s=document.createElement('div');s.className='m-sec '+cls;
    s.innerHTML='<h3>'+h+'</h3>'+html;b.appendChild(s);}
  if(d.role) add('','📌 节点作用（做什么 / 输入输出）','<p>'+d.role+'</p>');
  if(d.flow) add('','🔄 如何流转到下一个节点','<p>'+d.flow+'</p>');
  if(d.reuse) add('reuse','🔗 节点复用情况','<p>'+d.reuse+'</p>');
  if(d.why) add('why','❓ 为什么设计这个节点','<p>'+d.why+'</p>');
  if(d.tech) add('tech','⚡ 技术选型与理由','<p>'+d.tech+'</p>');
  if(d.optimize) add('opt','🚀 可优化方向','<p>'+d.optimize+'</p>');
  if(d.interview) add('iv','💼 面试可能会问','<p>'+d.interview+'</p>');
  document.getElementById('modal-mask').classList.add('on');
  document.body.style.overflow='hidden';
}
function closeNode(){document.getElementById('modal-mask').classList.remove('on');document.body.style.overflow='';}
document.addEventListener('keydown',function(e){if(e.key==='Escape')closeNode();});
"""

NAV = """  <nav class="topnav">
    <div class="brand">⚖️ 法智引擎</div>
    <a href="00_index.html" class="nav-index">🏠 首页</a>
    <a href="01_architecture.html" class="nav-arch">🏛️ 总架构</a>
    <a href="02_contract_review.html" class="nav-contract">📋 合同审核</a>
    <a href="03_retrieval.html" class="nav-retrieval">🔍 检索智能体</a>
    <a href="04_compliance.html" class="nav-compliance">🛡️ 合规审查</a>
    <a href="05_legal_qa.html" class="nav-qa">💬 法律问答</a>
    <a href="06_xiaohongshu.html" class="nav-xhs">📱 小红书</a>
    <a href="07_docgen.html" class="nav-docgen">📝 文书生成</a>
    <span class="spacer"></span>
    <span class="stat">LangGraph · Neo4j · FAISS · 企查查MCP · bge-m3</span>
  </nav>
"""

FOOT = """  <footer>
    <div class="foot-nav">
      <a href="00_index.html">🏠 首页</a>·
      <a href="01_architecture.html">🏛️ 总架构</a>·
      <a href="02_contract_review.html">📋 合同审核</a>·
      <a href="03_retrieval.html">🔍 检索</a>·
      <a href="04_compliance.html">🛡️ 合规</a>·
      <a href="05_legal_qa.html">💬 法律问答</a>·
      <a href="06_xiaohongshu.html">📱 小红书</a>·
      <a href="07_docgen.html">📝 文书生成</a>·
      <a href="节点式流程图.html">🧩 节点式流程</a>
    </div>
    <p>法智引擎 · LangGraph 多智能体架构 · 设计铁律：AI 辅助 · 人工兜底</p>
    <p style="margin-top:6px;">依据《律师法》第 13/28 条 · LangGraph + FAISS + Neo4j + 企查查 MCP + bge-m3</p>
  </footer>
"""

def node(nid, idx, ico, title, cls, role, side=None):
    s = f'''    <div class="node {cls}" onclick="openNode('{nid}')" style="cursor:pointer;">
      <div class="node-head"><span class="ico">{ico}</span><span class="idx">{idx}</span><h3>{title}</h3></div>
      <p>{role}</p>
      <div class="click-hint">👆 点击节点查看：作用 / 流转 / 复用 / 设计理由 / 技术选型 / 优化 / 面试题</div>
    </div>
    <div class="arrow"></div>
'''
    if side:
        s += f'    <div class="side-note"><b>💡 旁批（小白版）：</b>{side}</div>\n'
    return s

def stage(label):
    return f'    <div class="stage-label">{label}</div>\n'

def branch(text):
    return f'    <div class="branch">{text}</div>\n    <div class="arrow"></div>\n'

def parallel(title, items):
    its = "".join(f'<div class="pl-item"><strong>{k}</strong>{v}</div>' for k,v in items)
    return f'''    <div class="parallel">
      <div class="pl-title">{title}</div>
      <div class="pl-grid">{its}</div>
    </div>
    <div class="arrow"></div>
'''

def page(fname, active, crumb, h1, desc, body, node_data, design_items, extra_reuse=None,
         nav_extra="", foot_extra=""):
    di = "".join(f'<div class="design-item"><strong>{k}</strong>{v}</div>' for k,v in design_items)
    reuse_html = ""
    if extra_reuse:
        reuse_html = f'''
  <div class="reuse-summary">
    <h2>🧩 {h1.replace("🏛️","").replace("📋","").replace("🔍","").replace("🛡️","").replace("💬","").replace("📱","").replace("📝","")} · 节点复用与总架构协作</h2>
    {extra_reuse}
  </div>
'''
    nd = json.dumps(node_data, ensure_ascii=False)
    html = f'''<!DOCTYPE html>
<html lang="zh-CN" data-theme="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>{h1} · 法智引擎</title>
  <link rel="stylesheet" href="style.css">
  <style>{POPUP_CSS}</style>
</head>
<body>
{NAV}
  <div class="page-wrap">
    <div class="page-head">
      <div class="crumb">{crumb}</div>
      <h1>{h1}</h1>
      <p class="desc">{desc}</p>
    </div>
    <div class="flow">
{body}    </div>
    <div class="design-bar">
{di}
    </div>
{reuse_html}  </div>
{FOOT}
  <div class="modal-mask" id="modal-mask" onclick="if(event.target===this)closeNode()">
    <div class="modal-box">
      <div class="m-head">
        <span class="m-ico" id="m-ico">📋</span>
        <h2 id="m-title">节点详情</h2>
        <span class="m-tag" id="m-tag">节点</span>
        <button class="m-close" onclick="closeNode()">✕</button>
      </div>
      <div class="m-body" id="m-body"></div>
    </div>
  </div>
  <script>
  var NODE_DATA = {nd};
{POPUP_JS}  </script>
</body>
</html>
'''
    path = os.path.join(OUT, fname)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print("written:", path, len(html), "bytes")

# =====================================================================
# 数据：每个智能体的节点与弹窗内容（依据 flowcharts_文字/ 最新架构）
# =====================================================================

# ---- 通用：共享数据预处理 5 节点（被合同审核/合规审查共用） ----
def shared_preprocess_nodes():
    return [
      node("doc_extract","N2","📄","文档提取 (doc_extract_node)","t-contract",
        "读取 <code>uploaded_doc_path</code> 指向的合同文件（txt/md/docx），检测文件类型并调用对应解析器，提取纯文本写入 <code>doc_text</code>。无文件时用 <code>input</code> 兜底。",
        side="律师拿到合同原件的第一件事：把各种格式的纸面/电子版合同变成可阅读的纯文本，后面所有分析都基于这段文本。"),
      node("party_identify","N8","👥","甲乙方识别 (party_identify_node)","t-contract",
        "从 <code>doc_text[:3000]</code> + <code>input</code> 识别甲方/乙方名称（规则正则 → LLM 兜底），并推断用户立场 <code>user_side</code>（A甲方/B乙方/关键词）。输出 <code>party_a/party_b/user_side</code>。",
        side="律师得先搞清“我的客户是谁、对方是谁”，这决定了后面审查时该帮哪一方挑风险（立场化）。"),
      node("contract_classify","N3","🏷️","合同分类 (contract_classify_node)","t-contract",
        "LLM 基于全文判断合同类型（买卖/租赁/借贷/建设工程/政府采购/劳动/服务/技术/其他），写入 <code>contract_type</code>。",
        side="不同类型的合同，适用的法律、审查重点、模板都不同。比如建设工程合同要看建筑法，劳动合同要看劳动法。"),
      node("clause_split","N4","✂️","条款切分 (clause_split_node)","t-contract",
        "按“第X条”编号优先切分；无编号则按换行分段。输出结构化条款列表 <code>doc_clauses=[{id,title,text}]</code>。",
        side="律师逐条读合同，先搞清楚结构：哪些条款说付款、哪些说违约、哪些说保密……"),
      node("numeric_extract","N5c-1","🔢","数值抽取 (numeric_extract_node)","t-contract",
        "LLM 抽取合同全部关键数值：单价/数量/总价/税率/违约金比例/利率/保证金/付款比例/期限等，写入 <code>extracted_numerics</code>。",
        side="律师做“数字标注”——把合同里所有数字圈出来，后面要检查这些数字是否合理、是否超标。"),
    ]

SHARED_PRE_ND = {
 "doc_extract":None,
}
# 共享预处理节点弹窗数据
SHARED_ND = {
 "doc_extract":{
   "ico":"📄","title":"文档提取 doc_extract_node","tag":"共享预处理",
   "role":"检测文件类型(txt/md/docx)→调用对应解析器→提取纯文本写入 <code>doc_text</code>。文件不存在/解析失败时用 <code>input</code> 兜底。",
   "flow":"输出 <code>doc_text</code> 给后续 甲乙方识别 / 合同分类 / 条款切分 使用，是整条链路的起点。",
   "reuse":"合同审核、合规审查两条链路共用此节点（同一份合同只解析一次）。",
   "why":"没有统一的文本入口，后续所有 LLM 分析都无从谈起；单独抽离便于替换解析器（MinerU/pdfplumber/OCR）。",
   "tech":"按扩展名分派解析器；docx 用 python-docx，纯文本直接读。规则简单稳定，不依赖 LLM 以节省成本。",
   "optimize":"增加三级降级：MinerU→pdfplumber→OCR，文字覆盖率<70% 触发 OCR 补充，目标解析成功率>98%。",
   "interview":"“解析失败怎么办？”答：多解析器降级 + 质量评分 + 失败通知用户重传，体现工程容错。"},
 "party_identify":{
   "ico":"👥","title":"甲乙方识别 party_identify_node","tag":"共享预处理",
   "role":"三层逻辑：①正则匹配甲方/乙方名称；②任一方未匹配→LLM 从全文识别(JSON)；③立场推断：input 含甲方名→user_side=A，含乙方→B。",
   "flow":"输出 <code>party_a/party_b/user_side</code>，供合同审核AI做立场化审查，也供企查查资信查询确定查谁。",
   "reuse":"合同审核与合规审查共用；合规审查也需要知道义务主体是谁（部分合规只针对特定主体）。",
   "why":"立场决定了“帮谁挑风险”。不识别立场，合同审核只能泛泛而谈，无法给出站队的具体谈判策略。",
   "tech":"正则优先（快、零成本）+ LLM 兜底（应对复杂表述）。先规则后 LLM，兼顾速度与覆盖。",
   "optimize":"增加公司名归一化（别名/曾用名映射），避免同一主体被识别成两个名字。",
   "interview":"“如何确定用户立场？”答：文本规则+LLM 双路，规则覆盖大部分简单合同，LLM 处理歧义。"},
 "contract_classify":{
   "ico":"🏷️","title":"合同分类 contract_classify_node","tag":"共享预处理",
   "role":"LLM 基于全文判断 9 类合同类型并 JSON 输出，写入 <code>contract_type</code>。",
   "flow":"决定后续行业增强检索挂载哪些数据源（如建设工程→GB 标准），以及适用的规则集与模板。",
   "reuse":"合同审核、合规审查共用；合规审查中不同合同适用不同强制规范（建筑法/劳动法/民法典）。",
   "why":"审查重点因合同类型而异，分类后才能精准挂载行业库与法规。",
   "tech":"LLM 分类比关键词更鲁棒；输出受控 JSON 便于下游消费。",
   "optimize":"增加置信度阈值，低置信时让用户确认类型，避免错挂数据源。",
   "interview":"“为什么不在入口就让用户选类型？”答：减少用户操作，且 LLM 可从正文自动判定，体验更好。"},
 "clause_split":{
   "ico":"✂️","title":"条款切分 clause_split_node","tag":"共享预处理",
   "role":"优先用正则 r\"第[零一二三…]+条\" 切分带编号条款；无编号则按换行分段。输出 <code>doc_clauses</code>。",
   "flow":"为后续条款级审查（合同审核AI 逐条审、合规审查逐条审）提供粒度可控的输入。",
   "reuse":"合同审核、合规审查共用同一份切分结果。",
   "why":"风险藏在具体条款里，必须切到条款级才能逐条对照法条，而不是整篇笼统看。",
   "tech":"正则优先（结构合同准确）+ 换行兜底（无编号合同），两级回退保证覆盖率。",
   "optimize":"对“鉴于条款/附件”做特殊处理，避免误切；支持跨页条款合并。",
   "interview":"“条款切分不准会怎样？”答：导致审查粒度错位，可能漏审或重复审，所以需两级回退。"},
 "numeric_extract":{
   "ico":"🔢","title":"数值抽取 numeric_extract_node","tag":"共享预处理",
   "role":"LLM 抽取所有关键数值字段（单价/数量/总价/税率/违约金比例/利率/保证金/付款比例/期限等），写入 <code>extracted_numerics</code>。",
   "flow":"供后续 数值校验节点 做确定性规则比对（如违约金≤30%、定金≤20%），也供报告展示。",
   "reuse":"合同审核、合规审查共用；数值校验节点拿到抽取结果后才能比对法定阈值。",
   "why":"数字是合同最容易出法律问题的地方（金额、比例、期限），需单独结构化以便机器校验。",
   "tech":"LLM 抽取（理解语境）优于正则；结果用于后续纯 Python 规则校验，LLM 只做“读”，规则做“判”。",
   "optimize":"增加交叉验证：总价≠单价×数量 时带反馈重抽，避免 LLM 抽错数字（数值要求 100% 准）。",
   "interview":"“数值校验如何保证准确？”答：LLM 抽取 + 规则交叉验证 + 重试环 + 人工兜底。"},
}

# ---- 检索 5 节点子图（被合同审核/合规审查/法律检索/文书生成 复用） ----
def retrieval_nodes():
    return [
      node("ret_intent","①","🔍","检索·意图分解 (retrieval_intent_decompose_node)","t-retrieval",
        "将原始检索意图拆解为 3-5 个检索子角度/关键词（如“违约金是否过高”→[违约金/违约赔偿/损害赔偿/定金罚则]）。初始化检索上下文。"),
      node("ret_base","②","📚","检索·基础层 (retrieval_base_layer_node)","t-retrieval",
        "多路并行检索：L0 Neo4j 实体精确匹配 → L1 FAISS 向量检索 → L2 本地法规关键词(4级回退) → L3 行业标准 → L4 案例 → L5 司法解释；共享层企查查资信。输出 <code>base_citations</code>。"),
      node("ret_enhance","③","🔧","检索·增强查询 (retrieval_enhance_query_node)","t-retrieval",
        "当 <code>len(base_citations)&lt;2</code> 时触发：LLM 基于合同正文补充生成相关法条（伪检索兜底），输出 <code>enhance_citations</code>。",
        side="基础层没找到够多法条时，让 LLM 凭合同内容“补几条相关法条”，宁可带‘AI生成’标注也别空着。"),
      node("ret_fusion","④","🔀","检索·融合排序 (retrieval_fusion_sort_node)","t-retrieval",
        "RRF 倒数排名融合多路结果 → 去重 → 冲突消解(新法优于旧法/上位法优于下位法/特别法优于一般法) → 质量分(权威+时效+相关+完整)。输出 <code>citations</code>+<code>quality_score</code>。"),
      node("ret_output","⑤","📤","检索·输出 (retrieval_output_node)","t-retrieval",
        "格式化为 <code>research_context</code> 字符串；质量门禁 <code>quality_score≥0.85</code> 直接输出，否则 ≤3 次重试→北大法宝 MCP 付费兜底/用户确认。",
        side="质量够高就直接用（省 MCP 钱）；不够就重试，实在不行再花真钱去北大法宝查，或让用户决定。"),
    ]

RETRIEVAL_ND = {
 "ret_intent":{
   "ico":"🔍","title":"检索·意图分解","tag":"检索子图①",
   "role":"LLM 将用户查询/合同类型拆成 3-5 个检索角度，初始化 mounted_sources 等上下文。",
   "flow":"输出扩展后的关键词列表，交给 基础层节点 做多路并行检索。",
   "reuse":"被合同审核/合规审查/法律检索/文书生成 调用（作为检索子图入口）。",
   "why":"单次查询太窄，拆解成多角度才能覆盖“法律事实+法条+案例+行业”多维度。",
   "tech":"LLM 生成子查询，比人工 keyword 更贴近法律语义。",
   "optimize":"缓存相同合同类型的拆解结果，避免重复 LLM 调用。",
   "interview":"“为什么检索前要拆解意图？”答：提升召回，避免单一表述漏掉相关法条。"},
 "ret_base":{
   "ico":"📚","title":"检索·基础层","tag":"检索子图②",
   "role":"按挂载数据源多路并行：Neo4j 精确匹配→FAISS 向量→本地关键词(4级回退)→行业标准→案例→司法解释；不足3条自动下沉下一层。",
   "flow":"汇成 <code>base_citations</code> 交给 增强查询 节点（不足2条时触发 LLM 补充）。",
   "reuse":"同一子图被 4 条链路复用；挂载哪些层由 task_type+contract_type 控制。",
   "why":"单一检索源召回有限，平行多源+逐级下沉兼顾“准”与“全”。",
   "tech":"Neo4j(精确/图谱)+FAISS(bge-m3语义)+本地关键词(零成本兜底)+行业/案例/解释分层。",
   "optimize":"增加质量退化检测：连续低分自动降阈值并告警，避免单源故障导致全瘫痪。",
   "interview":"“为什么混合向量+关键词？”答：向量召回语义，关键词保证精确条款编号不漏，互补。"},
 "ret_enhance":{
   "ico":"🔧","title":"检索·增强查询","tag":"检索子图③",
   "role":"基础层结果&lt;2 条时，LLM 基于合同正文生成 3-5 条相关法条（标注‘基于AI生成’），作伪检索兜底。",
   "flow":"输出 <code>enhance_citations</code> 与 base 一起进入 融合排序。",
   "reuse":"复用检索子图，随合同审核/合规审查等链路触发。",
   "why":"检索真空白比检索不准更糟；伪检索保证下游至少有法条可引用（带标注即可识别）。",
   "tech":"纯 LLM 生成，仅在基础层真正缺失时兜底，控制幻觉范围（明确标注来源=LLM）。",
   "optimize":"增强结果参与质量分计算时降低其权重（source=LLM 权威分+0）。",
   "interview":"“伪检索会不会引入错误法条？”答：会，但显式标注来源=LLM，下游校验环节可识别。"},
 "ret_fusion":{
   "ico":"🔀","title":"检索·融合排序","tag":"检索子图④",
   "role":"RRF(Σ1/(rank+60)) 融合各路→hash 去重→4条冲突消解规则→4维加权质量分。输出 <code>citations</code>+<code>quality_score</code>。",
   "flow":"交给 输出节点 做质量门禁；质量分决定是否重试或付费兜底。",
   "reuse":"检索子图核心，被多链路复用。",
   "why":"多路结果需统一排序；RRF 不需各路分数可比，鲁棒；冲突消解保证法条时效/层级正确。",
   "tech":"RRF 融合（经典、无需归一化分数）；冲突规则编码法律位阶（宪法>法律>法规>规章）。",
   "optimize":"引入用户反馈信号微调质量分权重。",
   "interview":"“RRF 和加权平均排序区别？”答：RRF 只用排名、对分数尺度不敏感，更适合多异构源。"},
 "ret_output":{
   "ico":"📤","title":"检索·输出","tag":"检索子图⑤",
   "role":"格式化 <code>research_context</code>；质量门禁≥0.85 直接输出，否则 ≤3 次重试→北大法宝 MCP 付费/用户确认。",
   "flow":"输出 citations 供合同审核AI/合规审查/数值校验 引用；是整条链路“检索提前”的关键产物。",
   "reuse":"被合同审核/合规审查/法律检索/文书生成 复用。",
   "why":"质量门禁避免低质法条进入审查；重试+MCP 兜底平衡成本与可用性。",
   "tech":"阈值门禁 + 重试环 + 付费 MCP 兜底（北大法宝），形成“免费优先、付费保底”。",
   "optimize":"质量退化检测自动降阈值；对高频失败源做熔断告警。",
   "interview":"“质量门禁阈值怎么定？”答：0.85 兼顾成本与准确，配合重试与人工兜底。"},
}

# =====================================================================
# 01 总架构
# =====================================================================
def build_architecture():
    body = (
      stage("L1 · 用户入口") +
      node("u_entry","L1","👤","用户入口 (Streamlit / FastAPI)","t-arch",
        "合同文件 / 法律问题 / 检索关键词 / 文书要求 / 小红书内容。输出初始 <code>AgentState{input, uploaded_doc_path,...}</code>。") +
      stage("L2 · 小红书意图前置过滤（所有请求必经）") +
      node("xhs_intent","L2","📱","小红书意图识别 (xiaohongshu_publish_intent_node)","t-xhs",
        "LLM 判断用户是否想发小红书：是→走小红书独立链路；否→进入主意图路由。前置过滤(Filter-Before-Route)模式。") +
      stage("L3 · 意图路由（二次分发）") +
      node("router","L3","🧭","意图路由 (intent_router_node)","t-arch",
        "LLM 分析输入确定任务类型写入 <code>task_type</code>：contract_review / compliance_review / legal_research / legal_qa / legal_document_gen / case_search / law_query / other。") +
      stage("L4 · 企查查预判定（统一缓存，8 条非小红书路径共享一次）") +
      node("credit_pre","L4","🏢","企查查预判定 (credit_precheck)","t-compliance",
        "所有非小红书路径共享一次企查查预查，避免重复查询。strong→查全部 / medium→查关键词 / weak→仅标记 / none→跳过。",
        side="不管走哪条法律链路，先统一查一次对方企业资信（带缓存），后面不用重复查，省钱省时。") +
      stage("L5 · after_credit_precheck_router（二次路由到真实起点）") +
      branch("↓ 按 task_type 分发至：合同审核 / 合规审查 / 法律检索 / 法律问答 / 文书生成 / 案例检索 / 法规查询 / 其他兜底") +
      stage("⭐ 共享数据预处理（5 节点，合同/合规共用）") +
      "".join(shared_preprocess_nodes()) +
      stage("⭐ 检索已提前到此处！（5 节点子图，被合同/合规/法律检索/文书生成 复用）") +
      "".join(retrieval_nodes()) +
      stage("⭐ 分支独立执行：合同审核 ≠ 合规审查") +
      parallel("两条链路从检索输出后并行展开",[
        ("📗 合同审核AI（商业律师·立场化）","6 大维度：价格付款/交付验收/违约责任/保密IP/管辖争议/终止退出；user_side=A→站甲方，B→站乙方。输出可谈判的 contract_risk_items。"),
        ("🔴 合规审查（合规律师·客观中立）","7 大领域：强制规定/数据合规/反垄断/税务/劳动/行业准入/政府采购。输出刚性 compliance_risk_items + can_sign。"),
      ]) +
      stage("⚡ 冲突消解节点（新增，合规优先）") +
      node("conflict","⚡","🔀","冲突消解 (conflict_resolution)","t-compliance",
        "合规优先原则：①合规 critical→no；②合规 high→conditional（即使合同说可接受也要改）；③同问题双重发现以合规为准；④合规过+合同有风险→保留商业风险；⑤结论冲突以合规为准。输出 merged_risk_items+can_sign。",
        side="这是“裁判”环节：商业律师（合同审核）说能签，合规律师（合规）说违法——以合规为准，因为合规有一票否决权。") +
      node("numeric_validate","🔢","✔️","数值校验 (numeric_validate_node)","t-contract",
        "确定性规则引擎（threshold/range/sum_equals），无 LLM 调用，比对法定阈值（已有 citations）。输出 numeric_risk_items。") +
      node("credit_check","🏢","🏢","资信查询 (credit_check_node)","t-compliance",
        "3-tier 降级 MCP Bearer→AppKey+MD5→Mock（永不阻塞）。10 维度工商/股东/失信/被执行/异常/处罚/司法协助/知产/年报。输出 credit_risk_items+credit_score。") +
      node("risk_aggregate","⚖️","⚠️","风险聚合 (risk_aggregate_node)","t-compliance",
        "合并合同+合规+数值+资信四路；去重→加权→扣分制(0-100)→等级(Low/Medium/High)。合规 critical 不可降级。") +
      node("final_delivery","OUT","📦","最终交付 (final_delivery_node)","t-arch",
        "纯字符串拼装 Markdown 报告（无 LLM）：合规结论→合规风险清单→商业风险清单→数值校验→资信→综合评分处置。输出 final_report_markdown+output。",
        side="最后把四路结果拼成一份人能看懂的报告：先说“能不能签”，再列风险清单，最后给评分和处置建议。")
    )
    nd = {}
    nd.update(SHARED_ND); nd.update(RETRIEVAL_ND)
    nd.update({
     "u_entry":{"ico":"👤","title":"用户入口","tag":"L1",
       "role":"Streamlit/API 接入，输入文本+可选上传文档，构造初始 AgentState。",
       "flow":"进入 小红书意图识别（所有请求第一站）。","reuse":"所有链路共用入口。",
       "why":"统一收口用户输入，下游节点都从同一 State 读取字段。",
       "tech":"Streamlit 做交互界面，FastAPI 暴露服务；State 用 TypedDict 集中管理。",
       "optimize":"增加输入预校验与字段缺失兜底，减少下游 KeyError。",
       "interview":"“为什么用 StateGraph？”答：显式状态+条件边，流程可观测、可回溯。"},
     "xhs_intent":{"ico":"📱","title":"小红书意图识别","tag":"L2",
       "role":"LLM 二分类：是否想发小红书。","flow":"是→小红书链路；否→意图路由。",
       "reuse":"前置过滤，所有请求必经，独立于法律业务。",
       "why":"把“发笔记”这种非法律意图在路由前拦截，避免污染法律链路。",
       "tech":"轻量 LLM 二分类，JSON 输出 is_xiaohongshu。",
       "optimize":"热点意图正则快筛，命中再调 LLM。",
       "interview":"“为什么前置过滤？”答：解耦特殊意图与主线，互不干扰。"},
     "router":{"ico":"🧭","title":"意图路由","tag":"L3",
       "role":"LLM 判定 task_type，条件边分流到 8 条业务链路。","flow":"写入 task_type，进入企查查预判定→各链路。",
       "reuse":"所有法律链路共用路由。","why":"单一入口按意图分发，避免写 8 套独立服务。",
       "tech":"LLM 分类+path_map 确定性路由，比 if-else 易扩展。",
       "optimize":"增加路由校验回溯环：下游不匹配时回到路由重判（≤2次）。",
       "interview":"“LLM 路由错了怎么办？”答：下游前置校验+回溯环+用户兜底。"},
     "credit_pre":{"ico":"🏢","title":"企查查预判定","tag":"L4",
       "role":"统一一次企查查预查并缓存，供后续链路复用。","flow":"输出预查结果，进入二次路由分发。",
       "reuse":"8 条非小红书路径共享一次，避免重复查询。",
       "why":"资信是多数法律链路的公共需求，提前查一次可大幅降本。",
       "tech":"MCP Bearer→AppKey+MD5→Mock 三级降级。",
       "optimize":"结果缓存 TTL，同企业短时重复请求直接命中。",
       "interview":"“如何避免重复查资信？”答：统一预判定+缓存，一次查询多链路复用。"},
     "conflict":{"ico":"🔀","title":"冲突消解 conflict_resolution","tag":"核心新增",
       "role":"合规优先五规则，合并合同与合规风险并定 can_sign。","flow":"输出 merged_risk_items+can_sign 给 数值校验→资信→聚合→交付。",
       "reuse":"合同审核链路内嵌调用；合规审查独立链路也用它做 pass-through。",
       "why":"合同审核（商业立场）与合规审查（法律立场）结论可能冲突，需明确以谁为准。",
       "tech":"纯 Python 规则引擎，确定性、可审计，不依赖 LLM。",
       "optimize":"将规则表外置为配置，便于按行业调整阈值。",
       "interview":"“合规和合同审核冲突听谁的？”答：合规一票否决，critical→no，high→conditional。"},
     "numeric_validate":{"ico":"✔️","title":"数值校验","tag":"确定性",
       "role":"Python 规则比对法定阈值（违约金≤30%/定金≤20%/利率≤LPR4倍等）。","flow":"输出 numeric_risk_items 给 聚合。",
       "reuse":"合同审核、合规审查共用（共享 extracted_numerics + citations）。",
       "why":"金额比例类风险必须 100% 准确，规则比 LLM 可靠。",
       "tech":"确定性规则引擎，零 LLM 调用，结果一致可复现。",
       "optimize":"增加交叉验证重试环，LLM 抽错数字时带反馈重抽。",
       "interview":"“为什么数值校验不用 LLM？”答：要确定性、可复现、零成本。"},
     "credit_check":{"ico":"🏢","title":"资信查询 credit_check_node","tag":"MCP",
       "role":"3-tier 降级查企业 10 维度风险，算 credit_score。","flow":"输出 credit_risk_items 给 聚合。",
       "reuse":"合同审核、合规审查共用；预判定已提前查过一次。",
       "why":"对方是失信被执行人时签约风险极高，必须查。",
       "tech":"企查查 MCP：Bearer→AppKey+MD5→Mock 永不阻塞。",
       "optimize":"增加熔断器，连续失败转 Mock 并告警。",
       "interview":"“MCP 调用失败怎么办？”答：三级降级，最终 Mock 保证链路不中断。"},
     "risk_aggregate":{"ico":"⚠️","title":"风险聚合","tag":"四路合并",
       "role":"合并四路风险、去重、加权扣分、定等级；合规 critical 不可降级。","flow":"输出 merged_risk_items/score/level 给 最终交付。",
       "reuse":"合同审核、合规审查共用四路聚合逻辑。",
       "why":"单维风险无法决策，需综合打分并体现合规刚性。",
       "tech":"加权扣分模型（0-100），合规 critical 强制 High。",
       "optimize":"评分权重可按案件类型配置。",
       "interview":"“合规 critical 为什么不能降级？”答：法律红线，_agent 不能替用户违法签约。"},
     "final_delivery":{"ico":"📦","title":"最终交付","tag":"OUT",
       "role":"拼装 Markdown 报告：合规结论→合规风险→商业风险→数值→资信→评分处置。","flow":"END，输出 final_report_markdown+output。",
       "reuse":"合同审核、合规审查共用交付模板（合规风险前置）。",
       "why":"报告结构体现“合规优先”，让用户第一时间看到能否签约。",
       "tech":"纯字符串拼装，无 LLM，稳定低成本。",
       "optimize":"报告可导出 PDF/带目录锚点。",
       "interview":"“为什么报告把合规放最前？”答：合规有一票否决权，结论最重要。"},
    })
    reuse = """
    <p>整个系统由 <b>8 大智能体</b> 协作，核心是 <b>“检索提前 + 节点复用 + 冲突消解”</b> 三件事：</p>
    <table class="reuse-table">
      <tr><th class="rk">被复用节点</th><th>被哪些链路复用</th><th>复用价值</th></tr>
      <tr><td class="rk">共享数据预处理 5 节点</td><td>合同审核、合规审查</td><td>同一份合同只解析/识别/切分/抽数一次</td></tr>
      <tr><td class="rk">检索 5 节点子图</td><td>合同审核、合规审查、法律检索、文书生成</td><td>检索提前到审核之前，三审都能引用法条；被 4 链路复用</td></tr>
      <tr><td class="rk">冲突消解节点</td><td>合同审核（内嵌）、合规审查（独立 pass-through）</td><td>统一“合规优先”裁决，避免重复实现</td></tr>
      <tr><td class="rk">数值校验 / 资信查询 / 风险聚合 / 最终交付</td><td>合同审核、合规审查</td><td>四路风险合并逻辑共用，报告模板共用</td></tr>
      <tr><td class="rk">企查查 MCP 资信</td><td>总架构预判定 + 各链路资信查询</td><td>统一缓存一次查询，三级降级永不阻塞</td></tr>
      <tr><td class="rk">HistoryStore 持久化</td><td>文书生成</td><td>案情/文书可追溯、可重新生成</td></tr>
    </table>
    <p style="margin-top:12px;"><b>协作总架构一句话：</b>用户入口 → 小红书前置过滤 → 意图路由 → 企查查统一预查 → 二次路由 →
    共享 5 节点预处理 → <b>检索提前</b> → 合同审核(立场化)与合规审查(客观)并行 → <b>冲突消解(合规优先)</b> →
    数值校验 → 资信查询 → 四路风险聚合 → 最终交付。</p>
    """
    page("01_architecture.html","nav-arch","OVERVIEW · 全局视角","🏛️ LangGraph 多智能体总架构",
      "8 智能体协作全景：用户入口 → 小红书前置过滤 → 意图路由 → 企查查统一预查 → 二次路由 → 共享 5 节点预处理 → <b>检索提前</b> → 合同审核/合规审查并行 → <b>冲突消解(合规优先)</b> → 四路风险聚合 → 最终交付。一图掌握全局数据流与复用关系。",
      body, nd,
      [("🧱 模块解耦","检索 5 节点子图被合同审核/合规审查/法律检索/文书生成 4 条链路复用"),
       ("🔒 确定性门禁","数值校验用 Python 规则(阈值/范围/求和)，不依赖 LLM"),
       ("🔄 弹性重试","Cypher 校验失败重试≤3次；企查查 MCP 三级兜底(Bearer→AppKey→Mock)"),
       ("⚡ 冲突消解","合规 critical→no；high→conditional；同问题以合规为准"),
       ("🏢 企查查MCP","统一预判定+缓存，10 维度资信，永不阻塞"),
       ("📊 四路聚合","条款+合规+数值+资信，合规 critical/high 不降级")],
      extra_reuse=reuse)

# =====================================================================
# 02 合同审核
# =====================================================================
def build_contract():
    body = (
      stage("前置过滤 · START 入口") +
      node("xhs_intent","N0","📱","小红书意图识别 (xiaohongshu_publish_intent_node)","t-xhs",
        "所有请求第一站。LLM 判断是否发小红书：是→小红书链路；否→主意图路由。") +
      node("router","N1","🧭","意图路由 (intent_router_node)","t-arch",
        "LLM 识别意图输出 <code>task_type</code>，contract_review 走完整链路。") +
      branch("↓ contract_review 路径：以下为合同审核完整链路（与合规审查共用预处理与检索）") +
      stage("共享数据预处理 · N2-N6（合同审核 / 合规审查 共用）") +
      "".join(shared_preprocess_nodes()) +
      stage("⭐ 检索智能体提前 · N7a-N7e（5 节点，被 4 链路复用）") +
      "".join(retrieval_nodes()) +
      stage("三审并联 · 合同审核AI + 合规审查（均可用 citations）") +
      parallel("从检索输出后并行展开（二者结论都进入冲突消解）",[
        ("📗 合同审核AI（商业律师·立场化）","6 大维度：价格付款/交付验收/违约责任/保密IP/管辖争议/终止退出；user_side 决定站哪方。输出可谈判 contract_risk_items。"),
        ("🔴 合规审查（合规律师·客观中立）","7 大领域刚性审查，不站立场。输出 compliance_risk_items(不可谈判)+can_sign(签约结论)。"),
      ]) +
      stage("⚡ 冲突消解（合规优先）") +
      node("conflict","⚡","🔀","冲突消解 (conflict_resolution)","t-compliance",
        "五规则合并合同与合规风险：合规 critical→no；high→conditional(必须改)；同问题以合规为准；合规过+合同有风险→保留商业风险。输出 merged_risk_items+can_sign+conflict_log。",
        side="合同审核说“这条款能接受”，合规说“这条款违法”——听合规的。这是法律 AI 的红线。") +
      node("numeric_validate","N9","✔️","数值校验 (numeric_validate_node)","t-contract",
        "确定性规则引擎比对法定阈值（违约金≤30%/定金≤20%/利率≤LPR4倍/质保金≤3%）。输出 numeric_risk_items。",
        side="用 Python 硬规则算：比如违约金写 50% 超过法定 30%，直接标红，不靠 LLM 猜。") +
      node("credit_check","N10","🏢","资信查询 (credit_check_node)","t-compliance",
        "3-tier 降级查企业 10 维度，算 credit_score。输出 credit_risk_items。") +
      node("risk_aggregate","N11","⚠️","风险聚合 (risk_aggregate_node)","t-compliance",
        "四路合并（合同+合规+数值+资信），合规 critical 不可降级，输出 score/level。") +
      node("final_delivery","N12","📦","最终交付 (final_delivery_node)","t-contract",
        "拼装报告：合规结论→合规风险清单→商业风险清单(含修改建议)→数值校验→资信→评分处置。")
    )
    nd = {}
    nd.update(SHARED_ND); nd.update(RETRIEVAL_ND)
    nd.update({
     "xhs_intent":{"ico":"📱","title":"小红书意图识别","tag":"前置过滤",
       "role":"LLM 二分类是否发小红书。","flow":"否→进入意图路由→合同审核链路。",
       "reuse":"全局前置过滤，所有请求共用。","why":"解耦特殊意图与法律链路。",
       "tech":"轻量 LLM 二分类。","optimize":"正则快筛热点意图。",
       "interview":"“为什么前置过滤？”答：特殊意图不污染主线。"},
     "router":{"ico":"🧭","title":"意图路由","tag":"N1",
       "role":"LLM 判定 task_type。","flow":"contract_review→走完整 12 节点链路。",
       "reuse":"复用全局路由。","why":"按意图分发避免重复服务。",
       "tech":"LLM+path_map。","optimize":"路由校验回溯。",
       "interview":"“路由失败怎么办？”答：下游校验+回溯+兜底。"},
     "conflict":{"ico":"🔀","title":"冲突消解","tag":"核心新增",
       "role":"合规优先五规则，合并双审风险并定 can_sign。","flow":"→数值校验→资信→聚合→交付。",
       "reuse":"合同审核内嵌；合规审查独立链路也调用。",
       "why":"商业与法律立场可能冲突，需明确裁决。",
       "tech":"纯 Python 规则，可审计。","optimize":"规则配置外置。",
       "interview":"“冲突听谁的？”答：合规一票否决。"},
     "numeric_validate":{"ico":"✔️","title":"数值校验","tag":"确定性",
       "role":"规则比对法定阈值。","flow":"→聚合。","reuse":"与合规审查共用 extracted_numerics+citations。",
       "why":"金额比例须 100% 准。","tech":"Python 规则引擎。","optimize":"交叉验证重试。",
       "interview":"“为何不用 LLM？”答：确定性/可复现。"},
     "credit_check":{"ico":"🏢","title":"资信查询","tag":"MCP",
       "role":"3-tier 查 10 维度资信。","flow":"→聚合。","reuse":"与合规审查共用。",
       "why":"对方失信则风险极高。","tech":"企查查 MCP 三级降级。","optimize":"熔断告警。",
       "interview":"“MCP 失败？”答：三级降级永不阻塞。"},
     "risk_aggregate":{"ico":"⚠️","title":"风险聚合","tag":"四路",
       "role":"四路合并定级。","flow":"→交付。","reuse":"与合规审查共用。",
       "why":"综合决策需合并。","tech":"加权扣分，合规不可降级。","optimize":"权重可配。",
       "interview":"“critical 为何不可降？”答：法律红线。"},
     "final_delivery":{"ico":"📦","title":"最终交付","tag":"OUT",
       "role":"拼装报告。","flow":"END。","reuse":"与合规审查共用模板(合规前置)。",
       "why":"合规优先展示。","tech":"纯字符串拼装。","optimize":"导出 PDF。",
       "interview":"“报告为何合规在前？”答：一票否决最重要。"},
    })
    reuse = """
    <p>合同审核 = <b>商业律师角色（代理人，立场化）</b>：站在客户一方挑商业风险，给修改建议；但最终能否签约须经合规审查把关——<b>合规有一票否决权</b>。</p>
    <table class="reuse-table">
      <tr><th class="rk">复用节点</th><th>说明</th></tr>
      <tr><td class="rk">共享预处理 5 节点</td><td>与合规审查共用同一份 doc_text / 甲乙方 / 类型 / 条款 / 数值</td></tr>
      <tr><td class="rk">检索 5 节点子图</td><td>📌 已提前！合同审核AI/合规审查/数值校验都能引用 citations（检索增强审核）</td></tr>
      <tr><td class="rk">冲突消解节点</td><td>内嵌调用，统一“合规优先”裁决</td></tr>
      <tr><td class="rk">数值校验/资信/聚合/交付</td><td>与合规审查共用四路合并与报告模板</td></tr>
    </table>
    """
    page("02_contract_review.html","nav-contract","AGENT 01 · 合同审核","📋 合同审核智能体",
      "<strong>完整链路（🔁 检索智能体已提前到数值抽取之后！）</strong>：小红书前置过滤 → 意图路由 → 共享 5 节点预处理 → <strong>5 阶段检索(提前)</strong> → 合同审核AI(有法条) 与 合规审查(有法规) 并行 → <strong>冲突消解(合规优先)</strong> → 数值校验(有阈值) → 资信查询 → 四路风险聚合 → 最终交付。",
      body, nd,
      [("📱 前置过滤","小红书意图在路由前拦截"),
       ("🛡️ 合规刚性","合规结论不被商业审核降级"),
       ("⭐ 检索提前","检索从数值校验后移到数值抽取后，三审都能引法条"),
       ("✔️ 确定性校验","数值校验用 Python 规则"),
       ("🏢 企查查资信","MCP 三级兜底"),
       ("⚠️ 四路聚合","条款+合规+数值+资信，立场化评分")],
      extra_reuse=reuse)

# =====================================================================
# 03 检索智能体（独立入口）
# =====================================================================
def build_retrieval():
    body = (
      stage("检索入口") +
      node("ret_entry","IN","🔍","检索入口 (legal_research / case_search / law_query)","t-retrieval",
        "合同审核/合规审查经过预处理后进入；法律检索/案例检索/法规查询直接从检索入口开始（跳过 doc_extract 段）。") +
      "".join(retrieval_nodes()) +
      node("ret_done","OUT","📤","检索结果输出","t-retrieval",
        "输出 <code>citations</code> + <code>research_context</code>，供上游合同审核/合规审查/文书生成引用，或独立返回给用户。",
        side="检索本身也是一条独立服务：用户问“违约金过高怎么算”，直接走检索把法条找出来返回。")
    )
    nd = {}
    nd.update(RETRIEVAL_ND)
    nd.update({
     "ret_entry":{"ico":"🔍","title":"检索入口","tag":"IN",
       "role":"作为子图被 4 链路调用；也可独立承接法律检索/案例/法规查询。","flow":"→意图分解→…→输出。",
       "reuse":"被合同审核/合规审查/法律检索/文书生成复用（核心复用节点）。",
       "why":"把检索抽成独立子图，避免每条链路各写一套检索。",
       "tech":"LangGraph 子图（subgraph），统一挂载数据源。",
       "optimize":"结果缓存，相同 query 直接命中。",
       "interview":"“检索为什么做成子图？”答：复用+可独立测试+统一质量门禁。"},
     "ret_done":{"ico":"📤","title":"检索结果输出","tag":"OUT",
       "role":"输出 citations+research_context。","flow":"返回上游或用户。","reuse":"4 链路共用输出格式。",
       "why":"统一格式便于上游引用。","tech":"字符串+结构化列表。","optimize":"附带来源标签便于溯源。",
       "interview":"“检索结果如何保证质量？”答：质量门禁+重试+MCP 兜底。"},
    })
    reuse = """
    <p>检索 5 节点子图是整个系统的 <b>复用枢纽</b>：</p>
    <table class="reuse-table">
      <tr><th class="rk">复用方</th><th>挂载数据源(mounted_sources)</th></tr>
      <tr><td class="rk">合同审核</td><td>laws + industry + cases + interpretations</td></tr>
      <tr><td class="rk">合规审查</td><td>laws + industry（侧重强规）</td></tr>
      <tr><td class="rk">法律检索</td><td>全部数据源</td></tr>
      <tr><td class="rk">案例检索</td><td>仅 cases</td></tr>
      <tr><td class="rk">法规查询</td><td>仅 laws</td></tr>
      <tr><td class="rk">文书生成</td><td>复用 search() 方法填条款</td></tr>
    </table>
    <p style="margin-top:10px;"><b>检索提前的优势：</b>旧架构检索在最后，审核时没法条；新架构检索提前到数值抽取之后，使合同审核AI/合规审查/数值校验<b>都能引用法条与法定阈值</b>，实现“检索增强审核”。</p>
    """
    page("03_retrieval.html","nav-retrieval","AGENT 02 · 检索核心","🔍 检索智能体",
      "<strong>5 子节点检索链路（被 4 条链路复用）</strong>：意图分解 → 基础层(FAISS+Neo4j+local 多路并行) → 增强查询(LLM 伪检索兜底) → 融合排序(RRF+去重+冲突消解+质量分) → 输出(质量门禁≥0.85 / ≤3次重试 → 北大法宝 MCP 兜底)。📌 检索已提前到数值抽取之后！",
      body, nd,
      [("🧩 子图复用","被合同审核/合规审查/法律检索/文书生成 4 链路复用"),
       ("🔀 RRF 融合","多路排名融合，对分数尺度不敏感"),
       ("⚖️ 冲突消解","新法>旧法、上位法>下位法、特别法>一般法"),
       ("🚪 质量门禁","≥0.85 直接出；否则≤3次重试→MCP 付费兜底"),
       ("🔧 增强兜底","基础层不足2条时 LLM 补法条(标注AI生成)"),
       ("🏷️ 挂载可控","task_type+contract_type 决定挂哪些数据源")],
      extra_reuse=reuse)

# =====================================================================
# 04 合规审查
# =====================================================================
def build_compliance():
    body = (
      stage("共享数据预处理（与合同审核共用）") +
      "".join(shared_preprocess_nodes()) +
      stage("⭐ 检索提前（5 节点，合规侧重强规）") +
      "".join(retrieval_nodes()) +
      stage("🔴 合规审查核心（客观中立·刚性）") +
      node("compliance_review","🔴","🛡️","合规审查 (compliance_review_node)","t-compliance",
        "7 大领域刚性审查：强制规定/数据合规/反垄断/税务/劳动/行业准入/政府采购。输出 compliance_risk_items(不可谈判)+can_sign(pass/conditional/no)。任一 critical→no，任一 high→conditional。",
        side="合规律师不站任何一方，只站法律。发现违法就标红，结论不能因为“商业上能接受”就被降级。") +
      stage("⚡ 冲突消解（合规视角）") +
      node("conflict","⚡","🔀","冲突消解 (conflict_resolution)","t-compliance",
        "独立合规审查(task_type=compliance)时仅 pass-through 保留 compliance_risk_items；作为合同审核子调用时执行完整五规则。输出 merged_risk_items+can_sign。") +
      node("numeric_validate","🔢","✔️","数值校验 (numeric_validate_node)","t-contract",
        "合规侧重：违约金>实际损失30%→民法典585条 critical；定金>20%→586条 high；利率>LPR4倍→high；质保金>3%→建设工程办法。每项直连法条与合规等级。") +
      node("credit_check","🏢","🏢","资信查询 (credit_check_node)","t-compliance",
        "3-tier 降级查 10 维度；对方失信则签约风险极高。") +
      node("risk_aggregate","⚖️","⚠️","风险聚合 (risk_aggregate_node)","t-compliance",
        "四路合并；合规 critical 不降级→整体 High→can_sign=no（否决权体现）。") +
      node("final_delivery","OUT","📦","最终交付 (final_delivery_node)","t-compliance",
        "合规审查报告：签约结论 → 合规风险清单(按领域分组, critical/high/medium) → 合同审核补充意见(如有)。")
    )
    nd = {}
    nd.update(SHARED_ND); nd.update(RETRIEVAL_ND)
    nd.update({
     "compliance_review":{"ico":"🛡️","title":"合规审查 compliance_review_node","tag":"核心",
       "role":"7 大领域刚性审查，输出 compliance_risk_items+can_sign；critical→no，high→conditional。",
       "flow":"→冲突消解(独立时 pass-through)→数值校验→资信→聚合→交付。",
       "reuse":"被合同审核作为子调用；也可独立承接 compliance_review 任务。",
       "why":"合规是法律裁判者，必须客观中立、刚性不降级。",
       "tech":"LLM 领域审查 + 确定性 can_sign 判定（规则：critical/no, high/conditional）。",
       "optimize":"领域规则表外置，按行业开启/关闭领域。",
       "interview":"“合规和合同审核区别？”答：前者裁判者(刚性)，后者代理人(可谈判)。”"},
     "conflict":{"ico":"🔀","title":"冲突消解","tag":"核心",
       "role":"独立合规时 pass-through；合同子调用时执行完整五规则。","flow":"→数值校验→…→交付。",
       "reuse":"与合同审核共用同一节点实现。","why":"保证“合规优先”裁决唯一来源。",
       "tech":"纯 Python 规则。","optimize":"规则配置外置。",
       "interview":"“为什么独立合规时跳过消解？”答：无合同审核结论可冲突，直接透传。"},
     "numeric_validate":{"ico":"✔️","title":"数值校验(合规侧重)","tag":"确定性",
       "role":"比对法定阈值并直连法条与合规等级。","flow":"→聚合。","reuse":"与合同审核共用。",
       "why":"数值超法定上限即违法，须标合规等级。","tech":"Python 规则+法条映射。","optimize":"阈值表外置。",
       "interview":"“违约金超标为何 critical？”答：违反民法典585条，属刚性违法。"},
     "credit_check":{"ico":"🏢","title":"资信查询","tag":"MCP",
       "role":"3-tier 查 10 维度。","flow":"→聚合。","reuse":"与合同审核共用。","why":"对方失信风险极高。",
       "tech":"企查查 MCP 三级降级。","optimize":"熔断告警。","interview":"“MCP 失败？”答：三级降级。"},
     "risk_aggregate":{"ico":"⚠️","title":"风险聚合","tag":"四路",
       "role":"四路合并，合规 critical 不降级→整体 High→no。","flow":"→交付。","reuse":"与合同审核共用。",
       "why":"体现否决权。","tech":"加权扣分，合规强制 High。","optimize":"权重可配。",
       "interview":"“否决权如何体现？”答：critical→整体High→can_sign=no。"},
     "final_delivery":{"ico":"📦","title":"最终交付(合规报告)","tag":"OUT",
       "role":"合规结论→合规风险清单(按领域)→合同补充意见。","flow":"END。","reuse":"与合同审核共用模板(合规前置)。",
       "why":"合规优先展示。","tech":"纯字符串拼装。","optimize":"导出 PDF。",
       "interview":"“合规报告结构？”答：结论→按领域风险→补充意见。"},
    })
    reuse = """
    <p>合规审查 = <b>合规律师角色（裁判者，客观中立）</b>：只站法律，逐条检查是否违反强制性规定，给出能否签约结论。⚠️ <b>合规有一票否决权</b>。</p>
    <table class="reuse-table">
      <tr><th class="rk">复用节点</th><th>说明</th></tr>
      <tr><td class="rk">共享预处理 5 节点</td><td>与合同审核共用</td></tr>
      <tr><td class="rk">检索 5 节点子图</td><td>合规侧重强规(L0/L1)+行业准入(L3)+司法解释(L5)</td></tr>
      <tr><td class="rk">冲突消解节点</td><td>独立时 pass-through，子调用时完整五规则</td></tr>
      <tr><td class="rk">数值校验/资信/聚合/交付</td><td>与合同审核共用</td></tr>
    </table>
    """
    page("04_compliance.html","nav-compliance","AGENT 03 · 合规审查","🛡️ 合规审查智能体",
      "<strong>精简链路（检索已提前）</strong>：共享 5 节点预处理 → <strong>检索(提前,侧重强规)</strong> → 🛡️ 合规审查(7 大领域,刚性不降级) → 冲突消解 → 数值校验(有阈值) → 资信查询 → 四路风险聚合 → 最终交付。⚠️ 合规结论刚性——不可被商业条款审核降级。",
      body, nd,
      [("🛡️ 合规刚性","critical→no，high→conditional，不被商业降级"),
       ("⭐ 检索提前","合规审查也有法条支撑"),
       ("🔴 7 大领域","强制规定/数据合规/反垄断/税务/劳动/行业准入/政府采购"),
       ("⚡ 冲突消解","合规优先唯一裁决来源"),
       ("🏢 企查查资信","三级降级"),
       ("📊 四路聚合","合规 critical 不降级")],
      extra_reuse=reuse)

# =====================================================================
# 05 法律问答（Neo4j KG RAG + Cypher 重试环）
# =====================================================================
def build_qa():
    body = (
      stage("入口") +
      node("qa_entry","IN","💬","法律问答入口 (legal_qa)","t-qa",
        "用户法律问题输入；由意图路由分流到本链路（不经过 doc_extract 预处理）。") +
      node("qa_extract","1/6","🧠","实体抽取 (extract_entity_from_user_input_node)","t-qa",
        "LLM 从问题抽取三类：user_input_entities / concepts / statutes（JSON）。使后续 Neo4j 匹配更精准，降低幻觉。") +
      node("qa_match","2/6","🔗","Neo4j 实体匹配 (match_entity_from_neo4j_node)","t-qa",
        "对每个实体在知识图谱 MATCH 模糊匹配；Neo4j 不可用时降级 matched_entities=[]（走纯 LLM 直答）。") +
      node("qa_cypher","3/6","📝","Cypher 生成 (generate_neo4j_cypher_node)","t-qa",
        "基于匹配实体 + TCM_METADATA(图 schema) 生成 Cypher 查询。参考图 schema 提高准确率、减少重试。") +
      node("qa_check","4/6","✅","Cypher 校验 (check_cypher_node)","t-qa",
        "LLM 校验语法/标签/关系/合理性，累加 cypher_retry_count。不通过→≤3次重试环回到生成。") +
      branch("↓ 校验通过 → 执行；校验失败≥3次 → 基于实体直答(降级)") +
      parallel("两条出口",[
        ("⚡ run_cypher（执行）","在 Neo4j 执行查询，异常则 cypher_results=[]"),
        ("💬 neo4j_answer_generate（降级）","Cypher 反复失败≥3次→放弃图查询，基于 matched_entities 直接 LLM 回答")]) +
      node("qa_answer","OUT","💬","答案生成 (neo4j_answer_generate_node)","t-qa",
        "正常路径：cypher_results→自然语言答案；降级路径：基于实体直答。输出 neo4j_answer。")
    )
    nd = {
     "qa_entry":{"ico":"💬","title":"法律问答入口","tag":"IN",
       "role":"承接 legal_qa 任务。","flow":"→实体抽取。","reuse":"复用全局意图路由。",
       "why":"知识图谱问答独立成链。","tech":"路由分流。","optimize":"缓存高频问法。",
       "interview":"“为什么用知识图谱？”答：实体关系结构化，适合法律概念/案例关联。"},
     "qa_extract":{"ico":"🧠","title":"实体抽取","tag":"1/6",
       "role":"LLM 抽 entities/concepts/statutes。","flow":"→Neo4j 匹配。","reuse":"独立链路节点。",
       "why":"结构化抽取提升图谱匹配精度、降幻觉。","tech":"LLM+受控 JSON。","optimize":"字典回退。",
       "interview":"“为什么先抽实体？”答：把自然语言映射成图谱节点，检索更准。"},
     "qa_match":{"ico":"🔗","title":"Neo4j 实体匹配","tag":"2/6",
       "role":"图谱模糊 MATCH；失败降级 []。","flow":"→Cypher 生成。","reuse":"独立链路节点。",
       "why":"把文本实体对齐到图谱节点。","tech":"Cypher MATCH + 降级。","optimize":"向量辅助匹配。",
       "interview":"“Neo4j 挂了怎么办？”答：降级 matched_entities=[]，走 LLM 直答。"},
     "qa_cypher":{"ico":"📝","title":"Cypher 生成","tag":"3/6",
       "role":"基于实体+图 schema 生成 Cypher。","flow":"→校验。","reuse":"独立链路节点。",
       "why":"Text-to-Cypher 让 LLM 能查图谱。","tech":"LLM+图元数据约束。","optimize":"Few-shot 示例。",
       "interview":"“为什么给图 schema？”答：减少非法标签/关系，降重试率。"},
     "qa_check":{"ico":"✅","title":"Cypher 校验","tag":"4/6",
       "role":"LLM 校验 + 重试计数；≤3次重试环。","flow":"通过→执行；失败≥3→降级直答。","reuse":"独立链路节点。",
       "why":"Cypher 易语法错，校验+重试提升成功率。","tech":"LLM 校验+条件边回退。","optimize":"本地语法预检。",
       "interview":"“Cypher 重试几次？”答：≤3次，超限降级直答保证可用。"},
     "qa_answer":{"ico":"💬","title":"答案生成","tag":"OUT",
       "role":"图结果→自然语言；降级→实体直答。","flow":"END。","reuse":"独立链路节点。",
       "why":"把结构化结果翻译成人话。","tech":"LLM 翻译+降级。","optimize":"引用来源标注。",
       "interview":"“降级答案质量？”答：基于实体常识+RAG，永不报错。"},
    }
    reuse = """
    <p>法律问答 = <b>知识图谱 RAG（Neo4j + Cypher）</b>：实体抽取 → 图谱匹配 → Cypher 生成/校验(≤3次重试环) → 执行 → 答案生成。完整降级策略保证可用性：</p>
    <table class="reuse-table">
      <tr><th class="rk">降级点</th><th>行为</th></tr>
      <tr><td class="rk">Neo4j 不可用</td><td>matched_entities=[] → 跳过 Cypher → LLM 直答(RAG 补充)</td></tr>
      <tr><td class="rk">Cypher 生成失败</td><td>重试≤3次</td></tr>
      <tr><td class="rk">Cypher 校验≥3次失败</td><td>基于实体直接回答(降级)</td></tr>
      <tr><td class="rk">执行异常</td><td>LLM 直接回答</td></tr>
    </table>
    <p style="margin-top:10px;">本链路<b>不复用检索子图</b>（走 Neo4j 图谱路线），但共享全局意图路由与最终“可用优先”的降级设计哲学。</p>
    """
    page("05_legal_qa.html","nav-qa","AGENT 04 · 法律问答","💬 法律问答智能体",
      "Text-to-Cypher 知识图谱问答：用户提问 → 实体抽取 → Neo4j 匹配 → Cypher 生成/校验(≤3次重试环) → 执行 → 答案生成。Neo4j/Cypher 超限失败降级至 LLM 直答，保证可用性。",
      body, nd,
      [("🕸️ 知识图谱","Neo4j 实体/关系结构化，适合法律概念关联"),
       ("🔄 Cypher 重试","校验失败自动重试≤3次"),
       ("🛟 多级降级","Neo4j 挂→匹配空→直答；校验≥3→实体直答；执行异常→直答"),
       ("📝 图 schema 约束","TCM_METADATA 降低 Cypher 非法率"),
       ("🤖 LLM 翻译","图结果→自然语言答案"),
       ("🧠 实体抽取","结构化降低幻觉")],
      extra_reuse=reuse)

# =====================================================================
# 06 小红书发布
# =====================================================================
def build_xhs():
    body = (
      stage("入口（前置过滤命中）") +
      node("xhs_intent","L2","📱","小红书意图识别 (xiaohongshu_publish_intent_node)","t-xhs",
        "前置过滤命中：LLM 判断用户要发小红书。") +
      node("xhs_text","L5a","✍️","文案生成 (text_generate_node)","t-xhs",
        "LLM 生成小红书风格标题+正文（含 emoji，禁违禁词/绝对化用语），内容基于法律科普/合同避坑/合规提醒。输出 xiaohongshu_title/content。") +
      node("xhs_img","L5b","🎨","图片生成 (image_generator_node)","t-xhs",
        "提取视觉关键词→多方案降级生成配图：Stable Diffusion(本地)→DALL·E3(付费)→占位图。失败→无图发布（小红书支持纯文字）。") +
      node("xhs_check","L5c","✅","图文检查 (check_text_image_node)","t-xhs",
        "LLM 三维度检查：敏感词/广告违规/图片合规。不通过→直接结束不发布。输出 is_can_publish_xiaohongshu。") +
      branch("↓ 检查通过 → 自动发布；不通过 → END(不发布)") +
      node("xhs_pub","L5d","📱","自动发布 (xiaohongshu_auto_publish)","t-xhs",
        "Playwright 持久化浏览器→加载 cookies→检查登录(未登录弹二维码≤180s)→填内容→传图→发布→存 cookies。失败不影响主流程。") +
      node("xhs_md","L5e","📝","Markdown 存档 (generate_markdown_node)","t-xhs",
        "将发布内容整理为 Markdown 存档（时间/标题/正文/配图/状态），便于追溯与重新发布。")
    )
    nd = {
     "xhs_intent":{"ico":"📱","title":"小红书意图识别","tag":"前置过滤",
       "role":"LLM 判断是否发小红书。","flow":"命中→文案生成。","reuse":"全局前置过滤。",
       "why":"隔离内容生产链路与法律业务。","tech":"LLM 二分类。","optimize":"正则快筛。",
       "interview":"“为什么前置？”答：解耦。"},
     "xhs_text":{"ico":"✍️","title":"文案生成","tag":"L5a",
       "role":"LLM 生成标题+正文，法律科普向。","flow":"→图片生成。","reuse":"独立链路。",
       "why":"从法律角度做科普，平衡专业与可读。","tech":"LLM+风格约束 JSON。","optimize":"模板化开头。",
       "interview":"“如何避免违规？”答：禁绝对化用语+发布前检查闸门。"},
     "xhs_img":{"ico":"🎨","title":"图片生成","tag":"L5b",
       "role":"多方案降级生成配图。","flow":"→图文检查。","reuse":"独立链路。",
       "why":"图文更吸睛。","tech":"SD本地→DALL·E付费→占位图三级降级。","optimize":"缓存关键词→图。",
       "interview":"“图片生成失败？”答：无图发布降级。"},
     "xhs_check":{"ico":"✅","title":"图文检查","tag":"L5c",
       "role":"敏感词/广告/图片三维度合规检查。","flow":"通过→发布；不通过→END。","reuse":"独立链路。",
       "why":"发布前最后闸门，违规内容绝不发。","tech":"LLM 三维度+规则。","optimize":"敏感词库热更新。",
       "interview":"“不通过为何直接结束？”答：宁可不发也不违规。"},
     "xhs_pub":{"ico":"📱","title":"自动发布","tag":"L5d",
       "role":"Playwright 自动化发布+登录态复用。","flow":"→Markdown 存档。","reuse":"独立链路。",
       "why":"减少人工操作。","tech":"Playwright 持久化浏览器+cookies。","optimize":"登录态缓存降扫码。",
       "interview":"“登录失效？”答：弹二维码≤180s 让用户扫码。"},
     "xhs_md":{"ico":"📝","title":"Markdown 存档","tag":"L5e",
       "role":"存档发布内容。","flow":"END。","reuse":"独立链路。","why":"可追溯/可重发。",
       "tech":"字符串拼装。","optimize":"入库检索。","interview":"“为何存档？”答：合规留痕。"},
    }
    reuse = """
    <p>小红书发布 = <b>前置过滤(Filter-Before-Route)</b> 命中的独立内容生产链路，与法律业务完全解耦：</p>
    <table class="reuse-table">
      <tr><th class="rk">设计要点</th><th>说明</th></tr>
      <tr><td class="rk">独立隔离</td><td>命中意图即走 L5a~L5e，不经过主意图路由/法律链路</td></tr>
      <tr><td class="rk">多方案降级</td><td>图片 SD→DALL·E→占位图；发布失败不影响主流程</td></tr>
      <tr><td class="rk">最后闸门</td><td>图文检查不通过→直接结束，违规内容绝不发送</td></tr>
    </table>
    <p style="margin-top:10px;">本链路<b>不调用检索子图/合规审查</b>，但同样遵循“发布前合规闸门”的设计哲学（与全局合规优先一脉相承）。</p>
    """
    page("06_xiaohongshu.html","nav-xhs","AGENT 05 · 小红书发布","📱 小红书发布智能体",
      "入口前置(Intent Filter)命中：小红书意图识别 → 文案生成 → 图片生成(多方案降级) → 图文检查(不通过→直接结束) → 自动发布(Playwright) → Markdown 存档。独立链路，与法律业务解耦。",
      body, nd,
      [("🚪 前置过滤","命中意图才进入，隔离法律业务"),
       ("🎨 多方案降级","图片 SD→DALL·E→占位图；发布失败不阻塞"),
       ("🛡️ 最后闸门","图文检查不通过直接结束，绝不发违规内容"),
       ("🔐 登录复用","Playwright 持久化 cookies，失效弹二维码≤180s"),
       ("📝 可追溯","Markdown 存档便于重发"),
       ("📱 内容合规","禁违禁词/绝对化用语")],
      extra_reuse=reuse)

# =====================================================================
# 07 文书生成（7 节点 + 法条校验重试环 + 并行分支）
# =====================================================================
def build_docgen():
    body = (
      stage("入口") +
      node("dg_entry","IN","📝","文书生成入口 (legal_document_gen)","t-docgen",
        "用户案情描述/诉求/原告被告等；由意图路由分流，不经过 doc_extract 预处理。") +
      node("dg_case","1/7","📂","案情分析 (doc_case_analyze_node)","t-docgen",
        "LLM 结构化抽取 case_type/parties/facts/claims/evidence；已有 case_summary 则复用跳过 LLM。输出 case_summary+need_clarify。") +
      node("dg_template","2/7","🏷️","模板匹配 (doc_template_match_node)","t-docgen",
        "基于案情匹配 10 种预设模板（起诉状/答辩状/上诉状/执行申请/保全申请/劳动仲裁/行政复议/合同审查意见/法律意见/律师函）。输出 template_id/name。") +
      node("dg_fill","3/7","✍️","条款填充 (doc_clause_fill_node)","t-docgen",
        "🔄 复用检索智能体 search()：加载模板→填充当事人信息→用 RAG 检索结果填法条推理部分。输出 filled_doc。",
        side="这里直接调用检索智能体的 search() 方法去找相关法条，把模板里的空位填上——又一次复用检索子图。") +
      node("dg_validate","4/7","🔍","法条校验 (doc_law_validate_node)","t-docgen",
        "LLM 校验文书引用法条是否真实：pass/rewrite/fabricated；累加 doc_retry_count；fabricated→≤3次重试环回到条款填充重新检索。") +
      branch("↓ 引用真实 → 并行风险提示+类案推荐；需修改/虚假 → ≤3次重试回到[3/7]") +
      parallel("并行执行 [5/7]+[6/7]",[
        ("⚠️ 风险提示 doc_risk_advisor","LLM 标注文书法律风险点→risk_advice"),
        ("⚖️ 类案推荐 doc_case_recommend","LLM 推荐相关案例→case_recommendations")]) +
      node("dg_deliver","7/7","📦","最终交付 (doc_final_delivery_node)","t-docgen",
        "组合 filled_doc+risk_advice+case_recommendations，保存到 HistoryStore。输出 final_document+output。")
    )
    nd = {
     "dg_entry":{"ico":"📝","title":"文书生成入口","tag":"IN",
       "role":"承接 legal_document_gen。","flow":"→案情分析。","reuse":"复用全局路由。",
       "why":"文书生成独立成链。","tech":"路由分流。","optimize":"草稿续写。",
       "interview":"“为什么独立？”答：模板+校验自成体系。"},
     "dg_case":{"ico":"📂","title":"案情分析","tag":"1/7",
       "role":"LLM 抽案情要素；有 case_summary 跳过。","flow":"→模板匹配。","reuse":"可复用已有分析结果。",
       "why":"结构化案情是模板匹配前提。","tech":"LLM+JSON Schema+缺失兜底。","optimize":"追问澄清 need_clarify。",
       "interview":"“信息不足？”答：need_clarify 追问用户。"},
     "dg_template":{"ico":"🏷️","title":"模板匹配","tag":"2/7",
       "role":"规则匹配 10 种模板。","flow":"→条款填充。","reuse":"模板库独立维护。",
       "why":"法律文书格式法定，模板保证规范。","tech":"规则匹配+置信度。","optimize":"模板可扩展。",
       "interview":"“为何用模板？”答：格式合规+高效。"},
     "dg_fill":{"ico":"✍️","title":"条款填充","tag":"3/7 🔄复用检索",
       "role":"调用检索智能体 search() 填法条。","flow":"→法条校验。","reuse":"🔄 复用检索智能体 search() 方法（检索子图再次复用）。",
       "why":"法条必须真实，靠检索而非凭空写。","tech":"模板占位符+检索填充+LLM 推理。","optimize":"缓存检索。",
       "interview":"“文书如何保证法条真实？”答：复用检索子图+RAG 填充。"},
     "dg_validate":{"ico":"🔍","title":"法条校验","tag":"4/7",
       "role":"LLM 三级校验 pass/rewrite/fabricated；≤3次重试。","flow":"通过→并行；虚假→回退重填。","reuse":"独立链路节点。",
       "why":"防幻觉引用虚假法条（法律风险极高）。","tech":"LLM 校验+重试环。","optimize":"法条库比对。",
       "interview":"“法条造假怎么办？”答：校验环+重试，≥3次接受不完整并标注。"},
     "dg_deliver":{"ico":"📦","title":"最终交付","tag":"7/7",
       "role":"组合文书+风险+类案，存 HistoryStore。","flow":"END。","reuse":"🔄 复用总架构 HistoryStore 持久化。",
       "why":"可追溯、可重新生成。","tech":"字符串拼装+持久化。","optimize":"版本管理。",
       "interview":"“为何持久化？”答：案件可追溯，符合律师执业留痕。"},
    }
    reuse = """
    <p>文书生成 = <b>7 节点串行（含法条校验重试环 + 并行分支）</b>，关键复用点：</p>
    <table class="reuse-table">
      <tr><th class="rk">复用节点</th><th>说明</th></tr>
      <tr><td class="rk">检索智能体 search()</td><td>[3/7] 条款填充直接调用检索子图找法条（第 4 次复用检索）</td></tr>
      <tr><td class="rk">HistoryStore 持久化</td><td>[7/7] 交付时保存案情+文书，可追溯/重生成</td></tr>
      <tr><td class="rk">法条校验重试环</td><td>[4/7] fabricated→≤3次回到[3/7]重新检索填充</td></tr>
    </table>
    <p style="margin-top:10px;"><b>全系统复用总览：</b>检索 5 节点子图被 <b>合同审核 / 合规审查 / 法律检索 / 文书生成</b> 4 链路复用；HistoryStore 被文书生成复用；共享预处理 5 节点被合同/合规复用；冲突消解节点被合同/合规复用。</p>
    """
    page("07_docgen.html","nav-docgen","AGENT 06 · 文书生成","📝 法律文书生成智能体",
      "<strong>7 节点串联（含法条校验重试环 + 并行分支）</strong>：案情分析 → 模板匹配(10 模板) → 条款填充(🔄复用检索 search()) → 法条真实性校验(3级:通过/改写/虚假,≤3次重试环) → 风险提示 + 类案推荐(并行) → 最终交付 + 持久化(HistoryStore)。",
      body, nd,
      [("📂 案情结构化","有 case_summary 则跳过 LLM 复用"),
       ("🏷️ 10 模板","起诉状/答辩状/上诉状/执行/保全/仲裁/复议/审查意见/法律意见/律师函"),
       ("🔄 复用检索","条款填充调用检索 search() 填法条"),
       ("🔍 法条校验环","pass/rewrite/fabricated，≤3次重试回到填充"),
       ("⚖️ 并行分支","风险提示 + 类案推荐同时跑"),
       ("🗂️ HistoryStore","持久化可追溯")],
      extra_reuse=reuse)

# =====================================================================
# 00 index
# =====================================================================
def build_index():
    cards = """
      <a class="card card-arch" href="01_architecture.html">
        <div class="num">OVERVIEW · 全局视角</div>
        <h2>🏛️ 架构总流程图</h2>
        <p><strong>8 智能体 + 节点复用 + 检索提前 + 冲突消解</strong><br>L1入口→L2小红书前置过滤→L3意图路由→L4企查查统一预查→L5二次路由→共享5节点预处理→<strong style="color:#34d399;">⭐检索提前</strong>→合同审核(立场化)与合规审查(客观)并行→<strong style="color:#f87171;">⚡冲突消解(合规优先)</strong>→四路风险聚合→交付。节点式交互图见 <a href="节点式流程图.html" style="color:inherit;text-decoration:underline;">节点式流程图.html</a>。</p>
        <span class="tag">8智能体 · 检索提前 · 冲突消解 · 节点复用</span>
      </a>
      <a class="card card-contract" href="02_contract_review.html">
        <div class="num">AGENT 01 · 合同审核</div>
        <h2>📋 合同审核智能体</h2>
        <p><strong>完整链路（⭐检索提前到数值抽取之后）</strong>：共享5节点预处理→<strong style="color:#34d399;">5阶段检索(提前)</strong>→合同审核AI(有法条)与合规审查(有法规)并行→<strong style="color:#f87171;">⚡冲突消解(合规优先)</strong>→数值校验(有阈值)→资信→四路聚合→交付。<br><span style="color:#fb923c;font-size:12px;">💡 检索被合同审核/合规审查/法律检索/文书生成 4 链路复用</span></p>
        <span class="tag">检索提前 · 冲突消解 · 4链路复用 · MCP资信</span>
      </a>
      <a class="card card-retrieval" href="03_retrieval.html">
        <div class="num">AGENT 02 · 检索核心</div>
        <h2>🔍 检索智能体</h2>
        <p><strong>5 子节点检索链路（被 4 条链路复用）</strong><br>意图分解→基础层(FAISS+Neo4j+local多路)→增强(LLM伪检索)→融合排序(RRF+去重+冲突消解)→输出(质量门禁≥0.85/≤3次重试→MCP兜底)。<br><strong style="color:#34d399;">⭐检索已提前！</strong>从数值校验之后→提前到数值抽取之后。</p>
        <span class="tag">5子节点 · 4链路复用 · 检索提前 · RRF融合</span>
      </a>
      <a class="card card-compliance" href="04_compliance.html">
        <div class="num">AGENT 03 · 合规审查</div>
        <h2>🛡️ 合规审查智能体</h2>
        <p><strong>精简链路（检索已提前）</strong><br>共享5节点预处理→<strong style="color:#34d399;">检索(提前,侧重强规)</strong>→🛡️合规审查(7大领域,刚性)→冲突消解→数值校验(有阈值)→资信→四路聚合→交付。<br><span style="color:#f87171;">⚠️合规结论刚性——不可被商业条款审核降级（一票否决）</span></p>
        <span class="tag">检索提前 · 合规刚性 · 节点复用</span>
      </a>
      <a class="card card-qa" href="05_legal_qa.html">
        <div class="num">AGENT 04 · 法律问答</div>
        <h2>💬 法律问答智能体</h2>
        <p>Text-to-Cypher 知识图谱问答：实体抽取→Neo4j匹配→Cypher生成/校验(≤3次重试环)/执行→答案生成。Neo4j/Cypher 超限失败降级至 LLM 直答，保证可用性。</p>
        <span class="tag">知识图谱 · Cypher重试 · 降级兜底</span>
      </a>
      <a class="card card-xhs" href="06_xiaohongshu.html">
        <div class="num">AGENT 05 · 小红书发布</div>
        <h2>📱 小红书发布智能体</h2>
        <p>前置(Intent Filter)命中：意图识别→文案生成→图片生成(多方案降级)→图文检查(不通过→直接结束)→自动发布(Playwright)→Markdown存档。独立链路，与法律业务解耦。</p>
        <span class="tag">前置过滤 · 文案图片 · 自动发布</span>
      </a>
      <a class="card card-docgen" href="07_docgen.html">
        <div class="num">AGENT 06 · 文书生成</div>
        <h2>📝 法律文书生成智能体</h2>
        <p><strong>7 节点串联</strong>：案情分析→模板匹配(10模板)→条款填充(🔄复用检索 search())→法条真实性校验(3级+≤3次重试环)→风险提示+类案推荐(并行)→交付+持久化(HistoryStore)。</p>
        <span class="tag">7节点 · RAG填充 · 法条校验环 · 历史持久化</span>
      </a>
    """
    design = """
      <div class="design-item"><strong>🧱 模块解耦</strong>检索 5 节点子图被合同审核/合规审查/法律检索/文书生成 4 条链路复用</div>
      <div class="design-item"><strong>⭐ 检索提前</strong>检索从数值校验后移到数值抽取后，使三审都能引用法条（检索增强审核）</div>
      <div class="design-item"><strong>⚡ 冲突消解</strong>合规 critical→no；high→conditional；同问题以合规为准（一票否决）</div>
      <div class="design-item"><strong>🔒 确定性门禁</strong>数值校验用 Python 规则，不依赖 LLM</div>
      <div class="design-item"><strong>🔄 弹性重试</strong>Cypher 校验失败重试≤3次；企查查 MCP 三级兜底(Bearer→AppKey→Mock)</div>
      <div class="design-item"><strong>🏢 企查查MCP</strong>统一预判定+缓存，10 维度资信，永不阻塞</div>
    """
    note = """
      <div style="background:#1e293b;border:1px solid #f87171;padding:14px;border-radius:10px;margin-bottom:22px;">
        <p style="color:#f87171;font-weight:bold;margin:0 0 8px 0;">⚡ 重要设计说明：合同审核 与 合规审查 的关系</p>
        <p style="color:#94a3b8;font-size:13px;margin:0;">
          <strong style="color:#e2e8f0;">合同审核</strong> = 商业律师角色（立场化，站在客户一方挑商业风险，可谈判）<br>
          <strong style="color:#f87171;">合规审查</strong> = 合规律师角色（客观中立，检查法律强制性规定，刚性不可谈判）<br><br>
          <strong style="color:#f87171;">合规有一票否决权：</strong>合规结果与合同审核冲突时，以合规为准。<br>
          二者从检索输出后<strong style="color:#34d399;">分支并行执行</strong>，通过 <strong style="color:#f59e0b;">冲突消解节点</strong> 合并，确保合规优先级。
        </p>
      </div>
    """
    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>法智引擎 — LangGraph 多智能体架构</title>
  <link rel="stylesheet" href="style.css">
  <style>{POPUP_CSS}</style>
</head>
<body>
{NAV}
  <section class="hero">
    <h1>⚖️ 法智引擎</h1>
    <p class="sub">LangGraph 多智能体 · 法律垂直领域 AI 助理</p>
    <div class="motto">
      设计铁律：AI 做前置审查 / 辅助生成 / 风险提示<br>
      律师做最终决策 / 签章交付<br>
      依据《律师法》第 13、28 条 · 合规边界清晰
    </div>
    <div class="grid">{cards}</div>
    <div class="design-bar">{design}</div>
    <div class="scroll-hint">↓ 点击上方卡片进入对应流程图（每个节点可点击查看深度解析）↓</div>
    <div class="page-wrap" style="max-width:1000px;">
      {note}
      <div class="stage-label" style="text-align:center;margin:10px 0;">📐 两种流程图形式 · 任选查看</div>
      <div class="grid" style="grid-template-columns:1fr 1fr;">
        <a class="card card-arch" href="01_architecture.html" style="cursor:pointer;">
          <div class="num">FORM A · 大文本框流程</div>
          <h2>🏛️ langgraph 形式流程图</h2>
          <p>本页 01~07 与下方卡片：每个节点用<strong>直观大文本框</strong>写清作用与到下一节点的流转，<strong>点击节点弹出深度解析</strong>（作用/流转/复用/设计理由/技术选型/优化/面试题）。</p>
          <span class="tag">大文本框 · 点击弹窗 · 旁批小白版</span>
        </a>
        <a class="card card-retrieval" href="节点式流程图.html" style="cursor:pointer;">
          <div class="num">FORM B · 节点式流程图</div>
          <h2>🧩 SVG 节点式交互图</h2>
          <p>节点式流程图.html：SVG 绘制的<strong>节点关系图</strong>，能一眼看清有哪些节点、谁连谁；<strong>点击任意节点</strong>同样弹出深度解析。</p>
          <span class="tag">SVG 节点图 · 点击弹窗 · 3 组边</span>
        </a>
      </div>
    </div>
  </section>
{FOOT}
</body>
</html>
'''
    path = os.path.join(OUT, "00_index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print("written:", path, len(html), "bytes")

if __name__ == "__main__":
    build_architecture()
    build_contract()
    build_retrieval()
    build_compliance()
    build_qa()
    build_xhs()
    build_docgen()
    build_index()
    print("ALL DONE")
