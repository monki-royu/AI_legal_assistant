/* =============================================================
   法智引擎 LangGraph 流程图 - v3.0 增强脚本 (独立外部JS)
   - 保持原有暗色科技感风格
   - 重绘 svg-overview / svg-retrieval 两个SVG为 LangGraph 编排
   - 新增资信查询节点 & 检索5子节点的横向+纵向双层策略展示
   用法：在HTML </body> 前添加
       <script src="flowchart_enhancement_v3.js"></script>
   ============================================================= */
(function () {
  'use strict';

  function el(tag, attrs) {
    var e = document.createElementNS('http://www.w3.org/2000/svg', tag);
    if (!attrs) return e;
    for (var k in attrs) { if (attrs.hasOwnProperty(k)) e.setAttribute(k, attrs[k]); }
    return e;
  }

  // -------- 独立的 popup 层（不破坏原有 window.D / openPopup）---------
  function ensurePopup() {
    if (document.getElementById('_ovEnh')) return;
    // overlay
    var ov = document.createElement('div');
    ov.id = '_ovEnh';
    ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:99998;display:none;backdrop-filter:blur(4px);';
    ov.onclick = shutPopup;
    document.body.appendChild(ov);
    // panel
    var p = document.createElement('div');
    p.id = '_poEnh';
    p.style.cssText = 'position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);width:min(92vw,840px);max-height:90vh;overflow:auto;background:#121a27;border:1px solid rgba(110,231,183,.3);border-radius:18px;padding:28px 32px;z-index:99999;display:none;color:#e8f0f8;box-shadow:0 24px 90px rgba(0,0,0,.7);line-height:1.6;';
    p.innerHTML =
      '<button onclick="window._shutPopup()" style="float:right;background:rgba(236,72,153,.15);border:1px solid rgba(236,72,153,.4);color:#ec4899;border-radius:10px;padding:6px 18px;cursor:pointer;font-weight:700;font-size:14px;">✕ 关闭</button>' +
      '<h2 id="_phEnh" style="margin:0 0 18px 0;font-size:22px;font-weight:800;letter-spacing:1px;"></h2>' +
      '<div style="font-size:12px;color:#8a9aab;margin-bottom:12px;">点击流程图任意节点可查看详情 · 按 ESC 关闭</div>' +
      '<h3 style="color:#60c5fa;margin:16px 0 8px;font-size:14px;letter-spacing:1px;font-weight:700;">📌 节点作用 / 功能说明</h3>' +
      '<div id="_pfEnh" style="line-height:1.85;font-size:14px;color:#c0ccdd;margin-bottom:10px;"></div>' +
      '<h3 id="_thtEnh" style="color:#a78bfa;margin:22px 0 8px;font-size:14px;letter-spacing:1px;font-weight:700;">🤔 技术原理 / 设计思考</h3>' +
      '<ul id="_thlEnh" style="padding-left:20px;line-height:1.85;font-size:13.5px;color:#cbd5e1;list-style:disc;"></ul>' +
      '<h3 id="_ivtEnh" style="color:#fb923c;margin:22px 0 8px;font-size:14px;letter-spacing:1px;font-weight:700;">🎤 面试高频问题 & 标准作答</h3>' +
      '<ul id="_ivlEnh" style="padding-left:20px;line-height:1.85;font-size:13.5px;color:#cbd5e1;list-style:disc;"></ul>';
    document.body.appendChild(p);
  }
  window._shutPopup = shutPopup;
  function shutPopup() {
    var ov = document.getElementById('_ovEnh');
    var po = document.getElementById('_poEnh');
    if (ov) ov.style.display = 'none';
    if (po) po.style.display = 'none';
  }
  function paintTitle(el, y) {
    var m = {
      blue: ['#60c5fa', '#3b82f6'],
      purple: ['#c084fc', '#a78bfa'],
      green: ['#34d399', '#10b981'],
      orange: ['#fb923c', '#f59e0b'],
      cyan: ['#22d3ee', '#06b6d4'],
      pink: ['#f472b6', '#ec4899'],
      danger: ['#ef4444', '#dc2626'],
      start: ['#34d399', '#60c5fa'],
      end: ['#94a3b8', '#64748b'],
      decision: ['#fcd34d', '#f59e0b']
    };
    var c = m[y] || m.blue;
    el.style.background = 'linear-gradient(135deg,' + c[0] + ',' + c[1] + ')';
    el.style.webkitBackgroundClip = 'text';
    el.style.webkitTextFillColor = 'transparent';
  }
  function showDetail(key) {
    ensurePopup();
    var D = (typeof window !== 'undefined' && window.D) ? window.D : {};
    var d = D[key];
    var ph = document.getElementById('_phEnh');
    var pfn = document.getElementById('_pfEnh');
    var pthl = document.getElementById('_thlEnh');
    var pivl = document.getElementById('_ivlEnh');
    var tht = document.getElementById('_thtEnh');
    var ivt = document.getElementById('_ivtEnh');
    if (!d) {
      ph.textContent = '🔔 节点：' + key;
      paintTitle(ph, 'blue');
      pfn.innerHTML = '<em style="color:#f97316">该节点暂未注册详情。可点击总架构/检索区域的紫色/橙色节点查看已注册的6个增强节点（检索5子+资信查询N8.5）。</em>';
      pthl.innerHTML = ''; pivl.innerHTML = ''; tht.style.display = 'none'; ivt.style.display = 'none';
    } else {
      ph.textContent = (d.i || '') + '  ' + (d.t || key);
      paintTitle(ph, d.y || 'blue');
      pfn.innerHTML = d.f || '';
      pthl.innerHTML = '';
      (d.th || []).forEach(function (it) {
        var li = document.createElement('li');
        li.style.marginBottom = '14px';
        li.innerHTML = '<div style="color:#c4b5fd;font-weight:700;margin-bottom:5px;">❓ ' + it.q + '</div><div style="color:#cbd5e1;padding-left:4px;">💡 ' + it.a + '</div>';
        pthl.appendChild(li);
      });
      tht.style.display = (d.th && d.th.length) ? 'block' : 'none';
      pivl.innerHTML = '';
      (d.iv || []).forEach(function (it) {
        var li = document.createElement('li');
        li.style.marginBottom = '14px';
        li.innerHTML = '<div style="color:#fdba74;font-weight:700;margin-bottom:5px;">🎤 ' + it.q + '</div><div style="color:#cbd5e1;padding-left:4px;">✅ ' + it.a + '</div>';
        pivl.appendChild(li);
      });
      ivt.style.display = (d.iv && d.iv.length) ? 'block' : 'none';
    }
    document.getElementById('_ovEnh').style.display = 'block';
    document.getElementById('_poEnh').style.display = 'block';
  }
  window._showDetailEnh = showDetail;

  // -------- SVG marker --------
  function mkArrow(id, fill) {
    var m = el('marker', { id: id, viewBox: '0 0 10 10', refX: '9', refY: '5', markerWidth: '8', markerHeight: '8', orient: 'auto' });
    m.appendChild(el('path', { d: 'M0,0 L10,5 L0,10 z', fill: fill || '#5a7a9a' }));
    return m;
  }

  // ================================================================
  //  drawOverview - 重绘总架构为 LangGraph 完整多智能体编排
  // ================================================================
  function drawOverview() {
    var so = document.getElementById('svg-overview');
    if (!so) return;
    while (so.firstChild) so.removeChild(so.firstChild);
    so.setAttribute('viewBox', '0 0 1340 2540');
    var d = el('defs'); d.appendChild(mkArrow('arro')); so.appendChild(d);
    var g = so;
    var W = 270, H = 70, CX = 670;

    function nd(x, y, title, sub, cls, key) {
      var gg = el('g', { 'class': 'node-group node-' + cls, transform: 'translate(' + x + ',' + y + ')' });
      gg.appendChild(el('rect', { 'class': 'node-rect', x: 0, y: 0, width: W, height: H, rx: 14, ry: 14 }));
      var t1 = el('text', { x: W / 2, y: 26, 'class': 'node-label', style: 'font-size:14px;font-weight:700;' });
      t1.textContent = title; gg.appendChild(t1);
      if (sub) {
        var t2 = el('text', { x: W / 2, y: 48, 'class': 'node-sub', style: 'font-size:11.5px;' });
        t2.textContent = sub; gg.appendChild(t2);
      }
      gg.appendChild(el('circle', { cx: W - 14, cy: 16, r: 5, 'class': 'click-hint' }));
      gg.addEventListener('click', function () { showDetail(key); });
      g.appendChild(gg);
      return { x: x, y: y, w: W, h: H, cx: x + W / 2, cy: y + H / 2 };
    }

    function edge(x1, y1, x2, y2, cls, label, labelClass) {
      var p = el('path', {
        'class': 'edge edge-' + (cls || 'normal'),
        d: 'M' + x1 + ',' + y1 + ' C' + x1 + ',' + (y1 + y2) / 2 + ' ' + x2 + ',' + (y1 + y2) / 2 + ' ' + x2 + ',' + y2,
        'marker-end': 'url(#arro)'
      });
      g.appendChild(p);
      if (label) {
        var t = el('text', { x: (x1 + x2) / 2, y: (y1 + y2) / 2 - 8, 'class': 'edge-label edge-label-' + (labelClass || ''), style: 'font-size:11.5px;' });
        t.textContent = label; g.appendChild(t);
      }
    }

    var LX = CX - W - 110, MX = CX - W / 2, RX = CX + 150;
    var Y = 50;
    var s0 = nd(MX, Y, '🚀 START 入口', '', 'start', 'ov_start'); Y += H + 48;

    var s1 = nd(MX, Y, '📱 xhs发布意图前置识别', '条件路由', 'decision', 'ov_xhs_intent');
    edge(s0.cx, s0.y + H, s1.cx, s1.y); Y += H + 36;

    // ====== 小红书分支（左列） ======
    var yXhs = Y;
    var x1a = nd(LX, yXhs, '✍️ text_generate 文案生成', '小红书专用', 'pink', 'ov_xhs_text');
    edge(s1.cx, s1.cy + H, x1a.x + W, x1a.y + H / 2, 'normal', '发小红书意图'); yXhs += H + 56;
    var x1b = nd(LX, yXhs, '🎨 image_generator 图片生成', '小红书专用', 'pink', 'ov_xhs_img');
    edge(x1a.cx, x1a.y + H, x1b.cx, x1b.y); yXhs += H + 56;
    var x1c = nd(LX, yXhs, '🛡️ check_text_image 质量检查', '条件路由', 'decision', 'ov_xhs_check');
    edge(x1b.cx, x1b.y + H, x1c.cx, x1c.y); yXhs += H + 56;
    var x1d = nd(LX, yXhs, '🚀 auto_publish 自动发布', '小红书专用', 'pink', 'ov_xhs_pub');
    edge(x1c.cx, x1c.y + H, x1d.cx, x1d.y, 'success', '通过'); yXhs += H + 56;
    var x1e = nd(LX, yXhs, '📝 generate_markdown 报告', '小红书专用', 'pink', 'ov_xhs_md');
    edge(x1d.cx, x1d.y + H, x1e.cx, x1e.y); yXhs += H + 46;
    var x1f = nd(LX, yXhs, '🏁 END', '', 'end', 'ov_end_side1');
    edge(x1e.cx, x1e.y + H, x1f.cx, x1f.y, 'success');
    // 不通过 → END（虚线）
    var ecf = el('path', { 'class': 'edge edge-danger', d: 'M' + x1c.x + ',' + (x1c.y + H) + ' L' + x1c.x + ',' + x1f.y + ' L' + x1f.x + ',' + x1f.y, 'stroke-dasharray': '5 3', 'marker-end': 'url(#arro)' });
    g.appendChild(ecf);

    // ====== 主路由（右列） ======
    var yMain = Y;
    var s2 = nd(RX, yMain, '🧭 intent_router 意图路由', '5路分发', 'decision', 'ov_router');
    edge(s1.cx + W, s1.cy, s2.x, s2.y + H / 2, 'normal', '非小红书意图'); yMain += H + 46;

    // ====== 合同/合规主链路 ======
    var s3 = nd(MX, yMain, '📤 doc_extract 文档提取', '合同+合规共用', 'green', 'ov_doc');
    edge(s2.cx - W / 2, s2.y + H, s3.cx + W, s3.cy, 'normal', '合同/合规'); yMain += H + 56;
    var s4 = nd(MX, yMain, '🏷️ contract_classify 合同分类', '8类分类', 'green', 'ov_classify');
    edge(s3.cx, s3.y + H, s4.cx, s4.y); yMain += H + 56;
    var s5 = nd(MX, yMain, '✂️ clause_split 条款切分', '结构化条款', 'green', 'ov_clause');
    edge(s4.cx, s4.y + H, s5.cx, s5.y); yMain += H + 56;
    var s6 = nd(MX, yMain, '🔢 numeric_extract 数值抽取', '关键数值实体', 'green', 'ov_numeric_ext');
    edge(s5.cx, s5.y + H, s6.cx, s6.y); yMain += H + 56;
    var s7 = nd(MX, yMain, '⚖️ contract_ai_review 合同审核AI', '商业条款审查', 'green', 'ov_contract_ai');
    edge(s6.cx, s6.y + H, s7.cx, s7.y); yMain += H + 56;
    var s8 = nd(MX, yMain, '🛡️ compliance_review 合规审查', '刚性不降级', 'purple', 'ov_compliance');
    edge(s7.cx, s7.y + H, s8.cx, s8.y, 'success', '必经节点'); yMain += H + 56;
    var s9 = nd(MX, yMain, '✅ numeric_validate 数值校验', '7条规则', 'green', 'ov_numeric_val');
    edge(s8.cx, s8.y + H, s9.cx, s9.y); yMain += H + 40;

    // ========== 🔎 检索 5 子节点容器 ==========
    var contH = 640;
    g.appendChild(el('rect', { x: MX - 32, y: yMain - 24, width: W + 64, height: contH, rx: 18, ry: 18, fill: 'rgba(110,231,183,0.04)', stroke: 'rgba(110,231,183,0.36)', 'stroke-dasharray': '5 4', 'stroke-width': '1.5' }));
    var cgt = el('text', { x: MX + W / 2, y: yMain, 'text-anchor': 'middle', style: 'font-size:12px;letter-spacing:2px;fill:#34d399;font-weight:700;' });
    cgt.textContent = '🔄 检索 5 子节点 · 三条链路共用 · 横向+纵向双层策略'; g.appendChild(cgt);
    var yR = yMain + 28;

    var r1 = nd(MX, yR, '🧠 retrieval_intent_decompose', 'N1 · 意图分解', 'purple', 'ret_intent_decompose');
    edge(s9.cx, s9.y + H, r1.cx, r1.y); yR += H + 66;

    var r2 = nd(MX, yR, '📚 retrieval_base_layer', 'N2 · 横向挂载 + L1/L2 两级', 'purple', 'ret_base_layer');
    edge(r1.cx, r1.y + H, r2.cx, r2.y);

    // 左侧：横向按需挂载（行业增强层）
    var hiX = MX - 380, hiY = yR - 50;
    var hg = el('g', { transform: 'translate(' + hiX + ',' + hiY + ')' });
    hg.appendChild(el('rect', { x: 0, y: 0, width: 330, height: H + 170, rx: 14, ry: 14, fill: 'rgba(52,211,153,0.05)', stroke: 'rgba(52,211,153,0.42)', 'stroke-dasharray': '5 3' }));
    var ht = el('text', { x: 165, y: 28, 'text-anchor': 'middle', style: 'font-size:13px;font-weight:700;fill:#34d399;letter-spacing:1px;' });
    ht.textContent = '📎 横向按需挂载 · 行业增强层（动态）'; hg.appendChild(ht);
    [['建设工程', '住建部标准 + 建筑法实施条例', '#34d399'],
     ['金融借贷', '银保监会监管规定 + 贷款通则', '#60c5fa'],
     ['劳动合同', '劳动法司法解释 + 社保缴纳规定', '#a78bfa'],
     ['买卖合同', '最高院买卖合同司法解释', '#fb923c'],
     ['租赁合同', '城市房屋租赁管理办法', '#ec4899']].forEach(function (it, i) {
      var yy = 56 + i * 40;
      hg.appendChild(el('rect', { x: 16, y: yy, width: 298, height: 32, rx: 8, ry: 8, fill: 'rgba(255,255,255,0.02)', stroke: it[2], 'stroke-opacity': '0.55' }));
      var t1 = el('text', { x: 28, y: yy + 21, style: 'font-size:11.5px;font-weight:700;fill:' + it[2] }); t1.textContent = it[0]; hg.appendChild(t1);
      var t2 = el('text', { x: 124, y: yy + 21, style: 'font-size:10.5px;fill:#9fb0c0;' }); t2.textContent = '→ ' + it[1]; hg.appendChild(t2);
    });
    hg.addEventListener('click', function () { showDetail('ret_base_layer'); });
    g.appendChild(hg);
    var he = el('path', { 'class': 'edge edge-branch', d: 'M' + (hiX + 330) + ',' + (hiY + (H + 170) / 2) + ' L' + MX + ',' + (yR + H / 2), 'stroke-dasharray': '4 3' });
    g.appendChild(he);
    var hel = el('text', { x: (hiX + 330 + MX) / 2 - 30, y: (hiY + (H + 170) / 2 + yR + H / 2) / 2 - 6, 'class': 'edge-label' });
    hel.textContent = '动态挂载'; g.appendChild(hel);

    // 右侧：纵向逐级降级卡片
    var vgX = MX + W + 70, vgY = yR - 72;
    var vg = el('g', { transform: 'translate(' + vgX + ',' + vgY + ')' });
    vg.appendChild(el('rect', { x: 0, y: 0, width: 290, height: H + 230, rx: 14, ry: 14, fill: 'rgba(251,191,36,0.05)', stroke: 'rgba(251,191,36,0.42)', 'stroke-dasharray': '5 3' }));
    var vt = el('text', { x: 145, y: 26, 'text-anchor': 'middle', style: 'font-size:13px;font-weight:700;fill:#fcd34d;letter-spacing:1px;' });
    vt.textContent = '⬆️ 纵向逐级降级 · 三级兜底'; vg.appendChild(vt);
    var lvls = [
      ['L1 高精度', 'FAISS向量检索 + 知识图谱三元组', '优先·权威结构化', '#34d399'],
      ['↓ 不足3条时降级', '', '', '#8a9aab'],
      ['L2 关键词兜底', '本地法规txt目录匹配', '覆盖面广·扫描式', '#60c5fa'],
      ['↓ 仍不足时下游兜底', '', '', '#8a9aab'],
      ['L3 伪检索兜底', 'LLM生成（下游N3节点）', '极端情况·防死循环', '#f9a8d4']
    ];
    var yyL = 50;
    lvls.forEach(function (lv, i) {
      yyL += (i % 2) ? 26 : 50;
      if (i % 2) {
        var ta = el('text', { x: 145, y: yyL, 'text-anchor': 'middle', style: 'font-size:11px;font-weight:700;fill:' + lv[3] });
        ta.textContent = lv[0]; vg.appendChild(ta); return;
      }
      vg.appendChild(el('rect', { x: 14, y: yyL - 28, width: 262, height: 44, rx: 10, ry: 10, fill: 'rgba(255,255,255,0.02)', stroke: lv[3], 'stroke-opacity': '0.55' }));
      var t1 = el('text', { x: 28, y: yyL - 9, style: 'font-size:11.5px;font-weight:700;fill:' + lv[3] }); t1.textContent = lv[0]; vg.appendChild(t1);
      var t2 = el('text', { x: 28, y: yyL + 11, style: 'font-size:10.5px;fill:#b0c0d0;' }); t2.textContent = lv[1] + '（' + lv[2] + '）'; vg.appendChild(t2);
    });
    vg.addEventListener('click', function () { showDetail('ret_base_layer'); });
    g.appendChild(vg);
    var ve = el('path', { 'class': 'edge edge-branch', d: 'M' + (MX + W) + ',' + (yR + H / 2) + ' L' + vgX + ',' + (vgY + (H + 230) / 2), 'stroke-dasharray': '4 3' });
    g.appendChild(ve);

    yR += H + 66;
    var r3 = nd(MX, yR, '🆘 retrieval_enhance_query', 'N3 · 纵向 L3 LLM 伪检索兜底', 'purple', 'ret_enhance_query');
    edge(r2.cx, r2.y + H, r3.cx, r3.y, 'branch', '<2条触发'); yR += H + 66;
    var r4 = nd(MX, yR, '🔗 retrieval_fusion_sort', 'N4 · 去重+排序+质量分', 'purple', 'ret_fusion_sort');
    edge(r3.cx, r3.y + H, r4.cx, r4.y); yR += H + 66;
    var r5 = nd(MX, yR, '📤 retrieval_output', 'N5 · 兼容下游字段', 'purple', 'ret_output');
    edge(r4.cx, r4.y + H, r5.cx, r5.y); yMain = yR + H + 46;

    // ========== 后处理 + 🏛️资信查询 ==========
    var s10 = nd(MX, yMain, '👥 party_identify 甲乙方识别', '三条链路复用', 'purple', 'ov_party');
    edge(r5.cx, r5.y + H, s10.cx, s10.y); yMain += H + 56;
    var s10b = nd(MX, yMain, '🏛️ credit_check 资信查询（企查查）', 'N8.5 MCP→MD5→Mock三级', 'orange', 'credit_check');
    edge(s10.cx, s10.y + H, s10b.cx, s10b.y, 'normal', '用识别的名称查资信'); yMain += H + 56;
    var s11 = nd(MX, yMain, '📊 risk_aggregate 风险聚合', '4路风险合并打分', 'purple', 'ov_aggregate');
    edge(s10b.cx, s10b.y + H, s11.cx, s11.y, 'success', '合同+合规+数值+资信'); yMain += H + 56;
    var s12 = nd(MX, yMain, '📦 final_delivery 最终交付', '三条链路复用', 'purple', 'ov_delivery');
    edge(s11.cx, s11.y + H, s12.cx, s12.y); yMain += H + 46;
    var s13 = nd(MX, yMain, '🏁 END', '', 'end', 'ov_end_main');
    edge(s12.cx, s12.y + H, s13.cx, s13.y, 'success');

    // 法律检索路径（从主路由进入检索5子节点）
    var yLR = s2.y + H + 100;
    var sl = nd(RX + 80, yLR, '🔍 legal_research 检索入口', '跳转至检索5子节点', 'green', 'ov_legal_res');
    edge(s2.cx + W, s2.y + H / 2, sl.x, sl.y + H / 2, 'normal', '法律检索');
    var p2 = 'M' + sl.cx + ',' + (sl.y + H) + ' L' + sl.cx + ',' + (r1.y - 12) + ' L' + r1.cx + ',' + (r1.y - 12) + ' L' + r1.cx + ',' + r1.y;
    g.appendChild(el('path', { 'class': 'edge edge-success', d: p2, 'marker-end': 'url(#arro)' }));

    // 问答分支（最右列）
    var yQa = s2.y + H + 114, qaX = RX + 430;
    var q1 = nd(qaX, yQa, '🔬 extract_entity 实体抽取', '问答专用', 'cyan', 'ov_qa_extract');
    edge(s2.cx + W, s2.y + H / 2, q1.x, q1.y + H / 2, 'normal', '法律问答'); var qY = yQa + H + 66;
    var q2 = nd(qaX, qY, '🔎 match_entity Neo4j匹配', '问答专用', 'cyan', 'ov_qa_match');
    edge(q1.cx, q1.y + H, q2.cx, q2.y); qY += H + 66;
    var q3 = nd(qaX, qY, '✍️ generate_cypher Cypher生成', '问答专用', 'cyan', 'ov_qa_cypher');
    edge(q2.cx, q2.y + H, q3.cx, q3.y); qY += H + 66;
    var q4 = nd(qaX, qY, '🛡️ check_cypher Cypher校验', '≤3次重试环', 'decision', 'ov_qa_check');
    edge(q3.cx, q3.y + H, q4.cx, q4.y); qY += H + 66;
    var q5 = nd(qaX, qY, '⚡ run_cypher Cypher执行', '问答专用', 'cyan', 'ov_qa_run');
    edge(q4.cx, q4.y + H, q5.cx, q5.y, 'success', '通过');
    // Cypher重试环
    var lp = 'M' + q4.x + ',' + (q4.y + H / 2) + ' Q' + (q4.x - 220) + ',' + (q4.y + H / 2) + ' ' + (q4.x - 220) + ',' + q3.cy + ' L' + q3.x + ',' + q3.cy;
    g.appendChild(el('path', { 'class': 'edge edge-loop', d: lp, 'stroke-dasharray': '6 3', 'marker-end': 'url(#arro)' }));
    qY += H + 66; var q6 = nd(qaX, qY, '💡 neo4j_answer_generate 答案生成', '问答专用', 'cyan', 'ov_qa_answer');
    edge(q5.cx, q5.y + H, q6.cx, q6.y);
    var eca = 'M' + (q4.x + W) + ',' + (q4.y + H / 2) + ' L' + q6.x + ',' + (q6.y + H / 2);
    g.appendChild(el('path', { 'class': 'edge edge-danger', d: eca, 'stroke-dasharray': '5 3', 'marker-end': 'url(#arro)' }));
    qY += H + 66; var qend = nd(qaX, qY, '🏁 END', '', 'end', 'ov_end_side2');
    edge(q6.cx, q6.y + H, qend.cx, qend.y, 'success');

    // 兜底分支（LLM直答）
    var yDm = s2.y + H + 114, dmX = qaX;
    var dm1 = nd(dmX, yDm + 870, '🆘 llm_direct_out LLM兜底', '兜底专用', 'danger', 'ov_llm_direct');
    edge(s2.cx + W, s2.y + H / 2, dm1.x, dm1.y + H / 2, 'normal', '其他/兜底');
    var dmY = yDm + 870 + H + 66;
    var dmEnd = nd(dmX, dmY, '🏁 END', '', 'end', 'ov_end_side2');
    edge(dm1.cx, dm1.y + H, dmEnd.cx, dmEnd.y, 'success');
  }

  // -------- 合同/合规SVG顶部注入info提示条 --------
  function injectTopInfo() {
    ['svg-contract', 'svg-compliance'].forEach(function (id) {
      var s = document.getElementById(id); if (!s) return;
      var o = el('g', { transform: 'translate(40,40)' });
      o.appendChild(el('rect', { x: 0, y: 0, width: 780, height: 150, rx: 14, ry: 14, fill: 'rgba(251,146,60,0.06)', stroke: 'rgba(251,146,60,0.4)', 'stroke-dasharray': '5 3' }));
      [
        ['🏛️ v3.0 增强：新增 N8.5 credit_check 资信查询节点（企查查 API）', 14, '#fdba74', 34],
        ['真实执行顺序：甲乙方识别 → 🏛️资信查询（企查查MCP/MD5/Mock三级兜底）→ 风险聚合（4路风险合并打分）→ 最终交付', 12, '#b0c0d0', 62],
        ['对应代码：langgraph_main.py L389 party_identify → credit_check（L391）→ risk_aggregate（L393）', 12, '#b0c0d0', 88],
        ['💡 点击总架构或检索区域任意橙色/紫色节点 → 弹出详情（作用/设计/面试题）', 12, '#b0c0d0', 114]
      ].forEach(function (it) {
        var t = el('text', { x: 22, y: it[3], style: 'font-size:' + it[1] + 'px;font-weight:700;fill:' + it[2] });
        t.textContent = it[0]; o.appendChild(t);
      });
      s.insertBefore(o, s.firstChild);
    });
  }

  // ================================================================
  //  drawRetrieval - 检索专区重绘
  // ================================================================
  function drawRetrieval() {
    var sr = document.getElementById('svg-retrieval'); if (!sr) return;
    while (sr.firstChild) sr.removeChild(sr.firstChild);
    sr.setAttribute('viewBox', '0 0 1260 2080');
    var d2 = el('defs'); d2.appendChild(mkArrow('arr2')); sr.appendChild(d2);
    var W2 = 320, H2 = 90, CX2 = 630, Y2 = 50;

    function rn(x, y, lines, cls, key) {
      var gg = el('g', { 'class': 'node-group node-' + cls, transform: 'translate(' + x + ',' + y + ')' });
      gg.appendChild(el('rect', { 'class': 'node-rect', x: 0, y: 0, width: W2, height: H2, rx: 16, ry: 16 }));
      lines.forEach(function (l, i) {
        var t = el('text', { x: W2 / 2, y: 30 + i * 18, 'class': i === 0 ? 'node-label' : 'node-sub', style: i === 0 ? 'font-size:15px;font-weight:700;' : 'font-size:12.5px;' });
        t.textContent = l; gg.appendChild(t);
      });
      gg.appendChild(el('circle', { cx: W2 - 16, cy: 20, r: 6, 'class': 'click-hint' }));
      gg.addEventListener('click', function () { showDetail(key); });
      sr.appendChild(gg);
      return { x: x, y: y, w: W2, h: H2, cx: x + W2 / 2, cy: y + H2 / 2 };
    }
    function re(x1, y1, x2, y2, cls, label, lc) {
      var p = el('path', { 'class': 'edge edge-' + (cls || 'normal'), d: 'M' + x1 + ',' + y1 + ' C' + x1 + ',' + (y1 + y2) / 2 + ' ' + x2 + ',' + (y1 + y2) / 2 + ' ' + x2 + ',' + y2, 'marker-end': 'url(#arr2)' });
      sr.appendChild(p);
      if (label) {
        var t = el('text', { x: (x1 + x2) / 2, y: (y1 + y2) / 2 - 8, 'class': 'edge-label edge-label-' + (lc || ''), style: 'font-size:12px;' });
        t.textContent = label; sr.appendChild(t);
      }
    }

    var n0 = rn(CX2 - W2 / 2, Y2, ['🚀 法律检索链路入口'], 'start', 'ov_legal_res'); Y2 += H2 + 60;
    var n1 = rn(CX2 - W2 / 2, Y2, ['🧠 retrieval_intent_decompose 意图分解', '从 doc_text / contract_type / input 提取：', 'retrieval_query + retrieval_keywords'], 'purple', 'ret_intent_decompose');
    re(n0.cx, n0.y + H2, n1.cx, n1.y); Y2 += H2 + 120;

    var n2 = rn(CX2 - W2 / 2, Y2, ['📚 retrieval_base_layer 基础层必查', '横向按需挂载（contract_type驱动）', '纵向 L1 FAISS → L2 本地法规 两级降级'], 'purple', 'ret_base_layer');
    re(n1.cx, n1.y + H2, n2.cx, n2.y);

    // 左：横向行业挂载
    var hiX = CX2 - W2 / 2 - 470, hiY = Y2 - 90;
    var hg = el('g', { transform: 'translate(' + hiX + ',' + hiY + ')' });
    hg.appendChild(el('rect', { x: 0, y: 0, width: 430, height: 360, rx: 16, ry: 16, fill: 'rgba(52,211,153,0.05)', stroke: 'rgba(52,211,153,0.45)', 'stroke-dasharray': '5 3', 'stroke-width': '1.6' }));
    var ht = el('text', { x: 215, y: 34, 'text-anchor': 'middle', style: 'font-size:15px;font-weight:700;fill:#34d399;letter-spacing:1px;' });
    ht.textContent = '📎 横向按需挂载 · 行业增强层（动态）'; hg.appendChild(ht);
    [['建设工程', '住建部标准 + 建筑法实施条例', '#34d399'],
     ['金融借贷', '银保监会监管规定 + 贷款通则', '#60c5fa'],
     ['劳动合同', '劳动法司法解释 + 社保缴纳规定', '#a78bfa'],
     ['买卖合同', '最高院买卖合同司法解释', '#fb923c'],
     ['租赁合同', '城市房屋租赁管理办法', '#ec4899']].forEach(function (it, i) {
      var yy = 66 + i * 54;
      hg.appendChild(el('rect', { x: 20, y: yy, width: 390, height: 44, rx: 10, ry: 10, fill: 'rgba(255,255,255,0.02)', stroke: it[2], 'stroke-opacity': '0.55' }));
      var t1 = el('text', { x: 34, y: yy + 28, style: 'font-size:12px;font-weight:700;fill:' + it[2] }); t1.textContent = it[0]; hg.appendChild(t1);
      var t2 = el('text', { x: 142, y: yy + 28, style: 'font-size:11.5px;fill:#b0c0d0;' }); t2.textContent = '→ ' + it[1]; hg.appendChild(t2);
    });
    hg.addEventListener('click', function () { showDetail('ret_base_layer'); }); sr.appendChild(hg);
    var he = el('path', { 'class': 'edge edge-branch', d: 'M' + (hiX + 430) + ',' + (hiY + 180) + ' L' + (CX2 - W2 / 2) + ',' + (Y2 + H2 / 2), 'stroke-dasharray': '4 3' });
    sr.appendChild(he);
    var hel = el('text', { x: (hiX + 430 + CX2 - W2 / 2) / 2 - 30, y: (hiY + 180 + Y2 + H2 / 2) / 2 - 8, 'class': 'edge-label' });
    hel.textContent = '动态挂载'; sr.appendChild(hel);

    // 右：纵向三级降级
    var vgX = CX2 + W2 / 2 + 70, vgY = Y2 - 110;
    var vg = el('g', { transform: 'translate(' + vgX + ',' + vgY + ')' });
    vg.appendChild(el('rect', { x: 0, y: 0, width: 350, height: 400, rx: 16, ry: 16, fill: 'rgba(251,191,36,0.05)', stroke: 'rgba(251,191,36,0.45)', 'stroke-dasharray': '5 3', 'stroke-width': '1.6' }));
    var vt = el('text', { x: 175, y: 34, 'text-anchor': 'middle', style: 'font-size:15px;font-weight:700;fill:#fcd34d;letter-spacing:1px;' });
    vt.textContent = '⬆️ 纵向逐级降级 · 三级兜底'; vg.appendChild(vt);
    [['L1 高精度', 'FAISS向量检索 + 知识图谱三元组', '优先·权威结构化', '#34d399'],
     ['↓ 不足3条时降级', '', '', '#8a9aab'],
     ['L2 关键词兜底', '本地法规txt目录 第X条匹配', '覆盖面广·扫描式', '#60c5fa'],
     ['↓ 仍不足时下游节点兜底', '', '', '#8a9aab'],
     ['L3 伪检索兜底', 'LLM生成（下游 enhance 节点）', '极端情况·防死循环', '#f9a8d4']].forEach(function (lv, i) {
      var yy = 68 + i * 62;
      if (i % 2) {
        var ta = el('text', { x: 175, y: yy + 8, 'text-anchor': 'middle', style: 'font-size:12px;font-weight:700;fill:' + lv[3] });
        ta.textContent = lv[0]; vg.appendChild(ta); return;
      }
      vg.appendChild(el('rect', { x: 18, y: yy, width: 314, height: 52, rx: 12, ry: 12, fill: 'rgba(255,255,255,0.02)', stroke: lv[3], 'stroke-opacity': '0.6' }));
      var t1 = el('text', { x: 32, y: yy + 22, style: 'font-size:12px;font-weight:700;fill:' + lv[3] }); t1.textContent = lv[0]; vg.appendChild(t1);
      var t2 = el('text', { x: 32, y: yy + 42, style: 'font-size:11px;fill:#b0c0d0;' }); t2.textContent = lv[1] + '（' + lv[2] + '）'; vg.appendChild(t2);
    });
    vg.addEventListener('click', function () { showDetail('ret_base_layer'); }); sr.appendChild(vg);
    var ve = el('path', { 'class': 'edge edge-branch', d: 'M' + (CX2 + W2 / 2) + ',' + (Y2 + H2 / 2) + ' L' + vgX + ',' + (vgY + 200), 'stroke-dasharray': '4 3' });
    sr.appendChild(ve);

    Y2 += H2 + 120;
    var n3 = rn(CX2 - W2 / 2, Y2, ['🆘 retrieval_enhance_query 增强查询（纵向 L3）', '仅当 base_citations < 2 条时触发', 'LLM 根据合同内容生成3-5条法条概要'], 'purple', 'ret_enhance_query');
    re(n2.cx, n2.y + H2, n3.cx, n3.y, 'branch', 'base<2条 → 启动L3'); Y2 += H2 + 120;
    var n4 = rn(CX2 - W2 / 2, Y2, ['🔗 retrieval_fusion_sort 融合排序', '去重（title+号+前40字）→ 按score排序', '拼 research_context（前8条）+ quality_score'], 'purple', 'ret_fusion_sort');
    re(n3.cx, n3.y + H2, n4.cx, n4.y); Y2 += H2 + 120;
    var n5 = rn(CX2 - W2 / 2, Y2, ['📤 retrieval_output 结果输出', '写入标准字段: citations/context/quality_score', '与原 legal_research_node 输出完全兼容'], 'purple', 'ret_output');
    re(n4.cx, n4.y + H2, n5.cx, n5.y); Y2 += H2 + 60;

    // 共享后处理链路容器
    sr.appendChild(el('rect', { x: CX2 - W2 / 2 - 32, y: Y2 - 26, width: W2 + 64, height: 680, rx: 18, ry: 18, fill: 'rgba(167,139,250,0.04)', stroke: 'rgba(167,139,250,0.35)', 'stroke-dasharray': '5 3', 'stroke-width': '1.5' }));
    var pt = el('text', { x: CX2, y: Y2, 'text-anchor': 'middle', style: 'font-size:13px;font-weight:700;fill:#a78bfa;letter-spacing:2px;' });
    pt.textContent = '🔄 共享后处理链路（合同审核/合规审查/法律检索 三条共用）'; sr.appendChild(pt);
    var yP = Y2 + 34;
    var p1 = rn(CX2 - W2 / 2, yP, ['👥 party_identify 甲乙方识别', '识别甲乙名称 + 用户立场'], 'purple', 'ov_party');
    re(n5.cx, n5.y + H2, p1.cx, p1.y); yP += H2 + 120;
    var p2 = rn(CX2 - W2 / 2, yP, ['🏛️ credit_check 资信查询（企查查 API）', '三级兜底：MCP Bearer → AppKey MD5 → Mock', '写入甲乙双方信用信息 + credit_risk_items'], 'orange', 'credit_check');
    re(p1.cx, p1.y + H2, p2.cx, p2.y, 'normal', '查双方资信'); yP += H2 + 120;
    var p3 = rn(CX2 - W2 / 2, yP, ['📊 risk_aggregate 风险聚合', '4路风险合并：合同/合规/数值/资信', '计算 overall_risk_score + risk_level'], 'purple', 'ov_aggregate');
    re(p2.cx, p2.y + H2, p3.cx, p3.y); yP += H2 + 120;
    var p4 = rn(CX2 - W2 / 2, yP, ['📦 final_delivery 最终交付', '组装 final_report_markdown + output 摘要', '引用卡片最多展示 8 条 citation'], 'purple', 'ov_delivery');
    re(p3.cx, p3.y + H2, p4.cx, p4.y); yP += H2 + 60;
    var pE = rn(CX2 - W2 / 2, yP, ['🏁 END'], 'end', 'ov_end_main');
    re(p4.cx, p4.y + H2, pE.cx, pE.y, 'success');
  }

  // ================================================================
  //  挂 window.onload
  // ================================================================
  var oldOnLoad = window.onload;
  window.onload = function () {
    if (typeof oldOnLoad === 'function') { try { oldOnLoad.call(window); } catch (e) { /* ignore */ } }
    try {
      ensurePopup();
      drawOverview();
      drawRetrieval();
      injectTopInfo();
    } catch (e) {
      if (window.console && console.error) console.error('[flowchart_enhancement_v3] error:', e);
    }
  };
  // ESC 关闭弹窗
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') shutPopup(); });
})();
