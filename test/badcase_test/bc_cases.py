# -*- coding: utf-8 -*-
"""badcase 用例集 —— 10 条代表性用例

==============================================================================
【设计约束】
    1. golden set 严格锚定"当前已成功入图/入索引"的数据, 不测未处理数据。
       已入库清单 (由 __003__/__000_main_build_graph.py 落盘, 见 bc_probe.py 复核):
         industry_sources : 住建部标准.txt / 城市房屋租赁管理办法.txt
         interpretations  : 劳动法司法解释.txt / 信用卡管理刑事案件解释.txt
         laws             : 个人独资企业法.txt / 中华人民共和国个人信息保护法.txt
         regulations      : 不动产登记暂行条例.txt / 个人所得税法实施条例.txt
         cases            : 见 bc_probe 复核结果(存在"声明文件 vs 实际入库文件"不一致)
    2. 每条用例覆盖一个**不同的节点/状态/分支**, 10 条合起来覆盖:
         - 6 个 task_type (legal_qa / legal_research / case_search /
                          contract_review / compliance_review / legal_document_gen)
         - 4 条主图路径 (qa / r_retrieval / contract_compliance / docgen)
         - 3 类守卫分支 (text_recognize:block / docgen:clarify / quality_gate:retry)
         - 检索子图 11 节点中的 9 个 (intent_decompose / entity_recall /
           precision_filter / fusion_ranking / quality_gate / context_pack ...)
    3. 不因"数据量少导致 P/R 低"而判失败 —— 用例的失败判据是
       **流程/状态/函数行为**, 检索 P/R 只作观测项。

【字段说明】
    expected.route_contains      : 主图层节点名序列的子序列断言
    expected.full_route_contains : 带子图命名空间的完整节点名断言 (形如
                                   "contract_compliance::preprocess::party_identify")
    expected.expect_recall_zero  : True 表示为**负样本**, 检索就该为空,
                                   此时失败判据是"是否编造/是否明确说无依据"
==============================================================================
"""


# ============================================================================
# BC-06 用的房屋租赁合同全文 (故意埋 4 处风险点, 均可在已入库规章中找到对照)
#   风险点1: 租赁期限 25 年  → 《城市房屋租赁管理办法》第四条 (不得超过二十年)
#   风险点2: 逾期违约金日 5% → 《住建部标准》第五条 (违约金一般不超合同总价 20%)
#   风险点3: 维修义务全转嫁  → 《城市房屋租赁管理办法》第六条 (出租人负维修义务)
#   风险点4: 押金不退+另付2月违约金 (重复处罚, 类案裁判倾向于调减)
# ============================================================================
_LEASE_CONTRACT = """房屋租赁合同

出租方（甲方）：张明
承租方（乙方）：李华

第一条 租赁房屋
甲方将位于上海市浦东新区XX路88号3栋501室的房屋出租给乙方，建筑面积90平方米，用途为居住。

第二条 租赁期限
租赁期限为25年，自2025年1月1日起至2049年12月31日止。

第三条 租金及押金
月租金8000元，乙方应于每月5日前支付。乙方签约时一次性支付押金24000元。

第四条 房屋修缮
租赁期间房屋主体结构损坏由甲方负责维修，其余全部维修义务均由乙方承担，
包括但不限于水电管线、门窗、家电及墙面地面。

第五条 转租
乙方不得擅自将房屋转租给第三人，如需转租应事先征得甲方书面同意。

第六条 违约责任
乙方逾期支付租金的，每逾期一日按月租金的5%向甲方支付违约金。
乙方提前退租的，押金不予退还，并另付两个月租金作为违约金。

第七条 合同解除
租赁期间，甲方可提前30日通知乙方解除本合同，无需承担违约责任。

第八条 争议解决
因本合同发生争议，双方协商不成的，提交甲方所在地人民法院诉讼解决。

第九条 其他
本合同自双方签字盖章之日起生效。

甲方（签字）：张明
乙方（签字）：李华
2024年12月20日
"""


CASES = [
    # ======================================================================
    # BC-01  legal_qa · 个人信息保护法 · 合法性基础
    # ======================================================================
    {
        "id": "BC-01",
        "keywords": ['个人信息', '同意', '个人信息处理者'],
        "task": "legal_qa",
        "title": "个保法·处理个人信息的合法性基础",
        "input": "公司在没有取得用户同意的情况下，能不能处理用户的个人信息？法律上有哪些不需要取得同意的例外情形？",
        "extra_state": {},
        "focus": [
            "intent_router_node (LLM 分类 → legal_qa)",
            "qa_intent_classify (Level3 二分类: 是否法律相关)",
            "retrieval_intent_decompose_node._detect_retrieval_intents (多分面意图)",
            "retrieval_entity_recall_node._graph_entity_recall (ENTITY_MATCH)",
        ],
        "hypothesis": (
            "意图应判为 condition(条件/情形)。主风险不在路由, 而在 ENTITY_MATCH: "
            "图谱召回用 `e.name CONTAINS kw`, 而库内实体名是法条文本片段 "
            "(如'取得个人的同意''处理目的、处理方式和处理的个人信息种类发生变更'), "
            "LLM 抽出的关键词('个人信息''单独同意''合法性基础')未必是片段的超串, "
            "ENTITY_MATCH(100/90/60 分)可能整体落空, 只剩 FULLTEXT(40 分)兜底。"
        ),
        "severity": "P1",
        "golden": [
            {"doc": "中华人民共和国个人信息保护法", "article": "第十三条",
             "must_any": ["取得个人的同意", "为订立、履行个人作为一方当事人的合同所必需"]},
            {"doc": "中华人民共和国个人信息保护法", "article": "第十四条",
             "must_any": ["充分知情", "自愿、明确"]},
        ],
        "expected": {
            "route_contains": ["xiaohongshu_publish_intent", "intent_router", "qa"],
            "route_excludes": ["r_retrieval", "contract_compliance", "docgen"],
            "full_route_contains": [
                "qa::qa_intent_classify",
                "qa::qa_retrieval::retrieval_intent_decompose",
                "qa::qa_retrieval::retrieval_entity_recall",
                "qa::legal_qa_final_answer",
            ],
            "branch": "qa:retrieval",
            "state_checks": [
                {"field": "task_type", "op": "eq", "value": "legal_qa"},
                {"field": "is_legal_related", "op": "eq", "value": True},
                {"field": "mounted_sources", "op": "nonempty"},
                {"field": "retrieval_keywords", "op": "min_len", "value": 1},
                {"field": "output", "op": "nonempty"},
            ],
        },
        "quality_checks": [
            {"rule": "no_placeholder", "severity": "blocker"},
            {"rule": "citation_has_law_name", "severity": "major"},
            {"rule": "retry_not_wasted", "severity": "major"},
            {"rule": "retrieval_nonempty", "severity": "blocker"},
        ],
        "manual_takeover": "低分/空引用 → 建议挂人工复核队列 (当前无此链路, 见文档 5.3)",
    },

    # ======================================================================
    # BC-02  legal_research · 个人独资企业法 · 设立条件
    # ======================================================================
    {
        "id": "BC-02",
        "keywords": ['设立', '条件', '营业执照'],
        "task": "legal_research",
        "title": "个人独资企业法·设立条件(纯法规三源)",
        "input": "设立个人独资企业应当具备哪些法定条件？登记机关多久要做出是否登记的决定？",
        "extra_state": {},
        "focus": [
            "retrieval_intent_decompose_node._build_source_mounts (legal_research 固定 3 源, 不做关键词追加)",
            "retrieval_entity_recall_node (laws/regulations/interpretations 三源轮询)",
            "retrieval_fusion_ranking_node (多源 weighted 融合, 阈值 60)",
        ],
        "hypothesis": (
            "legal_research 的 domain 固定为 ['laws','regulations','interpretations'], "
            "且代码显式跳过 KEYWORD_RULES 追加 —— 因此 industry_sources 永不挂载, 属预期行为。"
            "真正的风险是**多源融合下的源间挤压**: laws 基础权威 1.0 > regulations 0.9 > "
            "interpretations 0.85, authority 权重占 0.20, 会让'个人独资企业法第八条'之外的"
            "法规/解释条目挤进 Top-12, 压低 P@5。"
        ),
        "severity": "P1",
        "golden": [
            {"doc": "个人独资企业法", "article": "第八条",
             "must_any": ["投资人为一个自然人", "有合法的企业名称", "有固定的生产经营场所"]},
            {"doc": "个人独资企业法", "article": "第十二条",
             "must_any": ["十五日", "发给营业执照"]},
        ],
        "expected": {
            "route_contains": ["xiaohongshu_publish_intent", "intent_router", "r_retrieval"],
            "route_excludes": ["qa", "contract_compliance", "docgen"],
            "full_route_contains": [
                "r_retrieval::retrieval_intent_decompose",
                "r_retrieval::retrieval_fusion_ranking",
                "r_retrieval::context_pack",
            ],
            "branch": "",
            "state_checks": [
                {"field": "task_type", "op": "eq", "value": "legal_research"},
                {"field": "domain_sources", "op": "eq",
                 "value": ["laws", "regulations", "interpretations"]},
                {"field": "output", "op": "nonempty"},
                {"field": "result_summary", "op": "nonempty"},
            ],
        },
        "quality_checks": [
            {"rule": "no_placeholder", "severity": "blocker"},
            {"rule": "citation_has_law_name", "severity": "major"},
            {"rule": "retry_not_wasted", "severity": "major"},
            {"rule": "retrieval_nonempty", "severity": "blocker"},
        ],
        "manual_takeover": "源间挤压 → 人工调整 _SOURCE_AUTHORITY 或按 doc 做 Top-K 配额",
    },

    # ======================================================================
    # BC-03  legal_research · 信用卡司法解释 · 数值型查询
    # ======================================================================
    {
        "id": "BC-03",
        "keywords": ['恶意透支', '数额较大', '信用卡'],
        "task": "legal_research",
        "title": "信用卡解释·恶意透支数额标准(数值型)",
        "input": "恶意透支信用卡，透支多少钱会被认定为数额较大、数额巨大、数额特别巨大？",
        "extra_state": {},
        "focus": [
            "retrieval_entity_recall_node._graph_entity_recall (interpretations 源)",
            "_INTENT_PREFERRED_REL (penalty/liability 偏置)",
            "retrieval_precision_filter_node._precision_gate (法条类永不硬删)",
        ],
        "hypothesis": (
            "数值型查询是 ENTITY_MATCH 失效最典型的场景: 库内实体名是"
            "'数额在五十万元以上不满五百万元''伪造的信用卡内存款余额、透支额度单独或者"
            "合计数额在一百万元以上的'这类**整句片段**, 而查询关键词是'恶意透支''数额较大'。"
            "`e.name CONTAINS '恶意透支'` 对以上片段全部为 False → ENTITY_MATCH 归零, "
            "只能靠 FULLTEXT(a.content CONTAINS kw) 命中, base_score 被钉在 40 分, "
            "precision_score 起步即 0.4×0.55, 排序被语义重排主导。"
        ),
        "severity": "P0",
        "golden": [
            {"doc": "最高人民法院、最高人民检察院关于办理妨害信用卡管理刑事案件具体应用法律若干问题的解释",
             "article": "第八条",
             "must_any": ["五万元以上不满五十万元", "五十万元以上不满五百万元", "五百万元以上"]},
        ],
        "expected": {
            "route_contains": ["xiaohongshu_publish_intent", "intent_router", "r_retrieval"],
            "full_route_contains": [
                "r_retrieval::retrieval_entity_recall",
                "r_retrieval::retrieval_precision_filter",
            ],
            "state_checks": [
                {"field": "task_type", "op": "eq", "value": "legal_research"},
                {"field": "output", "op": "nonempty"},
            ],
        },
        "quality_checks": [
            {"rule": "no_placeholder", "severity": "blocker"},
            {"rule": "citation_has_law_name", "severity": "major"},
            {"rule": "retry_not_wasted", "severity": "major"},
            {"rule": "retrieval_nonempty", "severity": "blocker"},
        ],
        "manual_takeover": "数值型查询建议走'条款号直查'通道 (需新增, 见文档 5.4)",
    },

    # ======================================================================
    # BC-04  case_search · 劳动纠纷案例 (库内唯一案例 / 单源 skip_fusion)
    # ======================================================================
    {
        "id": "BC-04",
        "keywords": ['违法解除', '赔偿金', '劳动合同'],
        "task": "case_search",
        "title": "类案检索·违法解除赔偿金(单源直查)",
        "input": "公司违法解除劳动合同，员工可以要求支付多少赔偿金？法院一般怎么判？",
        "extra_state": {},
        "focus": [
            "retrieval_fusion_ranking_node.skip_fusion 分支 (len(domain_sources)<=1)",
            "SINGLE_SOURCE_THRESHOLD=50 vs quality_gate_retry.QUALITY_GATE_THRESHOLD=60",
            "quality_gate_retry_node._expand_keywords (无 case_search 词表)",
        ],
        "hypothesis": (
            "**阈值冲突(确定性 bug)**: fusion 用 SINGLE_SOURCE_THRESHOLD=50 判定 "
            "quality_gate_passed, 而 quality_gate_retry 用 QUALITY_GATE_THRESHOLD=60 再判一次。"
            "当单源质量分落在 [50, 60) 区间时, fusion 说通过、quality_gate 说不通过 → 回边重试。"
            "而 _KEYWORD_EXPANSION_MAP 只覆盖 contract_review/compliance_review/legal_qa, "
            "case_search 无词表 → expanded 与 current_keywords 等长 → expanded_kw=None → "
            "**连关键词都不变, 3 次重试完全等价, 纯烧钱**。预期 quality_retry_count=3。"
        ),
        "severity": "P0",
        "golden": [
            {"doc": "张某与上海某贸易公司违法解除劳动合同赔偿金纠纷案",
             "must_any": ["违法解除", "赔偿金"]},
        ],
        "expected": {
            "route_contains": ["xiaohongshu_publish_intent", "intent_router", "r_retrieval"],
            "full_route_contains": [
                "r_retrieval::retrieval_intent_decompose",
                "r_retrieval::quality_gate_retry",
            ],
            "state_checks": [
                {"field": "task_type", "op": "eq", "value": "case_search"},
                {"field": "domain_sources", "op": "eq", "value": ["cases"]},
                {"field": "fusion_mode", "op": "in", "value": ["single_source", "empty"]},
                {"field": "quality_retry_count", "op": "lte", "value": 3},
                {"field": "output", "op": "nonempty"},
            ],
        },
        "quality_checks": [
            {"rule": "no_placeholder", "severity": "blocker"},
            {"rule": "retry_not_wasted", "severity": "major"},
            {"rule": "retrieval_nonempty", "severity": "blocker"},
        ],
        "manual_takeover": "重试空转 → 应短路; 案例库只有 1 条 → 需人工标注补充语料",
    },

    # ======================================================================
    # BC-05  case_search · 房屋租赁合同纠纷 (负样本: 该案未入库)
    # ======================================================================
    {
        "id": "BC-05",
        "keywords": ['房屋租赁', '违约金', '提前退租'],
        "task": "case_search",
        "title": "类案检索·房屋租赁违约金(负样本/库外)",
        "input": "房屋租赁合同纠纷，承租人提前退租，合同约定的违约金过高，法院会怎么调整？",
        "extra_state": {},
        "focus": [
            "检索子图空结果链路 (entity_recall=[] → precision=[] → citations=[] → quality=20)",
            "legal_qa_final_answer_node / retrieval_output_pack_node 的'无依据'兜底",
            "幻觉风险: 模型是否编造不存在的案号/判决",
        ],
        "expect_recall_zero": True,
        "hypothesis": (
            "**负样本, 检索就该为空**。该用例专测'系统会不会硬编'。"
            "代码路径上 retrieval_output_pack_node 会输出"
            "'⚠️ 未检索到相关结果, 建议调整关键词重试', 属正确兜底。"
            "真正的失败判据是: ① 是否编造案号/法院/判决结果; "
            "② 空结果时是否仍重试 3 次(空转); "
            "③ quality_retry_count 是否把基础设施故障与'库里真没有'区分开(当前无法区分)。"
        ),
        "severity": "P0",
        "golden": [
            {"doc": "case_698be5cb1791f068",
             "must_any": ["房屋租赁合同", "违约金"]},
        ],
        "expected": {
            "route_contains": ["xiaohongshu_publish_intent", "intent_router", "r_retrieval"],
            "state_checks": [
                {"field": "task_type", "op": "eq", "value": "case_search"},
                {"field": "output", "op": "nonempty"},
                {"field": "quality_retry_count", "op": "lte", "value": 3},
            ],
        },
        "quality_checks": [
            {"rule": "no_hallucinated_citation", "severity": "blocker"},
            {"rule": "no_placeholder", "severity": "blocker"},
        ],
        "manual_takeover": "空结果必须落到'人工复核/补充语料'队列, 而不是静默返回",
    },

    # ======================================================================
    # BC-06  contract_review · 房屋租赁合同全文 (合同合规主链路)
    # ======================================================================
    {
        "id": "BC-06",
        "keywords": ['房屋租赁', '租赁期限', '违约金', '押金'],
        "task": "contract_review",
        "title": "合同审核·房屋租赁合同(全链路+行业标准挂载)",
        "input": _LEASE_CONTRACT,
        "extra_state": {},
        "focus": [
            "text_recognize_node._score_contract_likeness (规则评分, 预期 ≥3 直接放行)",
            "preprocess 子图 5 节点 (party_identify/contract_classify/full_text_segment/"
            "numeric_extract/llm_query_extract)",
            "retrieval_intent_decompose_node.KEYWORD_RULES (industry_sources 触发)",
            "dual_review 子图 (conflict_resolution → numeric_validate → risk_aggregate → final_delivery)",
        ],
        "hypothesis": (
            "**规则-数据不对称**: 合同正文大量出现'房屋租赁''租赁期限''租金''押金', "
            "但 KEYWORD_RULES 的 industry_sources 触发词只有三组 —— "
            "建设工程类(建设工程/工程款/承包人/施工/...)、金融类(金融借款/放款/催收/...)、"
            "房产开发类(商品房买卖/预售/房地产开发/容积率/住建部/物业管理/...)。"
            "'房屋租赁'不在任何一组 → industry_sources **不会被挂载** → "
            "已入库的《城市房屋租赁管理办法》第四条(租期≤20年)在本次审核中完全不可达, "
            "25 年租期这一高风险点必然漏检。"
        ),
        "severity": "P0",
        "golden": [
            {"doc": "城市房屋租赁管理办法", "article": "第四条",
             "must_any": ["二十年", "超过部分无效"]},
            {"doc": "住建部标准", "article": "第五条",
             "must_any": ["违约金", "百分之二十"]},
        ],
        "expected": {
            "route_contains": ["xiaohongshu_publish_intent", "intent_router",
                               "contract_compliance"],
            "full_route_contains": [
                "contract_compliance::text_recognize",
                "contract_compliance::preprocess::party_identify",
                "contract_compliance::preprocess::contract_classify",
                "contract_compliance::cc_retrieval::retrieval_intent_decompose",
                "contract_compliance::dual_review::parallel_dual_review",
                "contract_compliance::dual_review::conflict_resolution",
                "contract_compliance::dual_review::final_delivery",
            ],
            "state_checks": [
                {"field": "task_type", "op": "eq", "value": "contract_review"},
                {"field": "text_recognize_flag", "op": "eq", "value": "pass"},
                {"field": "is_contract_input", "op": "eq", "value": True},
                {"field": "doc_text", "op": "nonempty"},
                {"field": "doc_segments", "op": "min_len", "value": 3},
                {"field": "contract_type", "op": "nonempty"},
                {"field": "output", "op": "nonempty"},
            ],
        },
        "quality_checks": [
            {"rule": "no_placeholder", "severity": "blocker"},
            {"rule": "risk_items_nonempty", "severity": "major"},
            {"rule": "retry_not_wasted", "severity": "major"},
            {"rule": "retrieval_nonempty", "severity": "blocker"},
        ],
        "manual_takeover": "漏检补录: 人工在 industry KEYWORD_RULES 增加'房屋租赁'族触发词后重跑",
    },

    # ======================================================================
    # BC-07  compliance_review · 人脸识别合规 (合规单审直通)
    # ======================================================================
    {
        "id": "BC-07",
        "keywords": ['个人信息', '单独同意', '人脸识别'],
        "task": "compliance_review",
        "title": "合规审查·人脸信息单独同意",
        "input": "公司在办公场所安装人脸识别门禁，采集员工人脸信息用于考勤，没有单独告知员工、也没有取得单独同意，这样做是否合规？有哪些法律后果？",
        "extra_state": {},
        "focus": [
            "text_recognize_node (compliance_review 直接 pass, 0 次 LLM, is_contract_input=False)",
            "contract_classify_node (非合同输入应写 '', 不瞎编合同类型)",
            "dual_review 子图 (_after_parallel_review → 跳过 conflict_resolution)",
            "敏感个人信息单独同意 (个保法第二十八/二十九条)",
        ],
        "hypothesis": (
            "compliance_review 直通分支本身是**省成本的正确设计**。"
            "风险点在下游: contract_classify 若未严格按 is_contract_input=False 写空串, "
            "会给出一个'劳动合同'之类的臆测类型, 污染 llm_query_extract 的检索查询;"
            "另外合规审查走单审(跳过 conflict_resolution), 风险项只来自 compliance 一路, "
            "覆盖面天然比合同审核窄。"
        ),
        "severity": "P1",
        "golden": [
            {"doc": "中华人民共和国个人信息保护法", "article": "第二十六条",
             "must_any": ["图像采集", "个人身份识别设备", "公共安全所必需"]},
            {"doc": "中华人民共和国个人信息保护法", "article": "第二十九条",
             "must_any": ["单独同意"]},
            {"doc": "中华人民共和国个人信息保护法", "article": "第二十八条",
             "must_any": ["生物识别", "敏感个人信息"]},
        ],
        "expected": {
            "route_contains": ["xiaohongshu_publish_intent", "intent_router",
                               "contract_compliance"],
            "full_route_contains": [
                "contract_compliance::text_recognize",
                "contract_compliance::cc_retrieval::retrieval_intent_decompose",
                "contract_compliance::dual_review::parallel_dual_review",
                "contract_compliance::dual_review::final_delivery",
            ],
            "full_route_excludes": [
                "contract_compliance::dual_review::conflict_resolution",
            ],
            "state_checks": [
                {"field": "task_type", "op": "eq", "value": "compliance_review"},
                {"field": "text_recognize_flag", "op": "eq", "value": "pass"},
                {"field": "is_contract_input", "op": "eq", "value": False},
                {"field": "output", "op": "nonempty"},
            ],
        },
        "quality_checks": [
            {"rule": "no_placeholder", "severity": "blocker"},
            {"rule": "citation_has_law_name", "severity": "major"},
            {"rule": "retry_not_wasted", "severity": "major"},
            {"rule": "retrieval_nonempty", "severity": "blocker"},
        ],
        "manual_takeover": "合规审查结论仅作参考, 高风险项应推 need_lawyer_review",
    },

    # ======================================================================
    # BC-08  contract_review · 非合同短输入 (守卫 block 分支)
    # ======================================================================
    {
        "id": "BC-08",
        "keywords": ['合同'],
        "task": "contract_review",
        "title": "合同审核·非合同短输入(守卫拦截)",
        "input": "帮我看看这个合不合法",
        "extra_state": {},
        "focus": [
            "text_recognize_node._score_contract_likeness (短请求负分 → block)",
            "contract_compliance_subgraph.after_text_recognize (block → END)",
            "need_user_confirm=True + output 提示文案",
        ],
        "hypothesis": (
            "输入 16 字、以'帮我'开头、无条款/无甲乙/无要素 → 规则评分: "
            "n<100 且命中 _REQUEST_PREFIXES → -3; n<50 → -2; 无加分项 → 得分 -5 ≤ 0 → "
            "**直接 block, 不调 LLM**。预期: 不进 preprocess/cc_retrieval/dual_review, "
            "output 为 _NOT_CONTRACT_MESSAGE, need_user_confirm=True。"
            "这条用例是**守卫正确性**的正向验证, 期望全绿; 若它失败说明守卫被改坏了。"
        ),
        "severity": "P2",
        "golden": [],
        "expected": {
            "route_contains": ["xiaohongshu_publish_intent", "intent_router",
                               "contract_compliance"],
            "full_route_contains": ["contract_compliance::text_recognize"],
            "full_route_excludes": [
                "contract_compliance::preprocess::party_identify",
                "contract_compliance::cc_retrieval::retrieval_intent_decompose",
                "contract_compliance::dual_review::parallel_dual_review",
            ],
            "branch": "text_recognize:block",
            "state_checks": [
                {"field": "text_recognize_flag", "op": "eq", "value": "block"},
                {"field": "need_user_confirm", "op": "eq", "value": True},
                {"field": "output", "op": "nonempty"},
            ],
        },
        "quality_checks": [
            {"rule": "output_contains", "severity": "blocker",
             "value": "合同审核"},
        ],
        "manual_takeover": "守卫式接管: 前端展示提示, 用户补全合同后重新提交(新 thread_id)",
    },

    # ======================================================================
    # BC-09  legal_document_gen · 缺当事人 (澄清守卫)
    # ======================================================================
    {
        "id": "BC-09",
        "keywords": ['违法解除', '赔偿金'],
        "task": "legal_document_gen",
        "title": "文书生成·缺当事人(澄清守卫)",
        "input": "公司违法解除劳动合同，还不给赔偿金，我要起诉公司，帮我写一份起诉状",
        "extra_state": {},   # 故意不给 plaintiff/defendant
        "focus": [
            "doc_case_analyze_node (LLM 抽 parties + need_clarify)",
            "docgen_subgraph._clarify_router (need_clarify → END)",
            "output 是否为追问文案而非残缺文书",
        ],
        "hypothesis": (
            "原告/被告确实缺失(只有'公司', 无具体名称), LLM 大概率判 need_clarify=True → "
            "_clarify_router 走 clarify 分支直接 END, 后续 template_match/query_plan/"
            "clause_fill/risk_analysis/final_delivery 全部不执行, output 为追问文案。"
            "**注意**: 该断言依赖 LLM 判定, 存在不稳定性; 若 LLM 把'公司'当成被告名, "
            "会一路生成到 final_delivery, 产出当事人为'原告/公司'的残缺文书 —— "
            "这本身就是要捕捉的 badcase, 两种结果都有价值。"
        ),
        "severity": "P1",
        "golden": [],
        "expected": {
            "route_contains": ["xiaohongshu_publish_intent", "intent_router", "docgen"],
            "full_route_contains": ["docgen::doc_case_analyze"],
            "branch": "docgen:clarify",
            "state_checks": [
                {"field": "output", "op": "nonempty"},
            ],
        },
        "quality_checks": [
            {"rule": "no_placeholder_in_doc", "severity": "blocker"},
        ],
        "manual_takeover": "追问式接管: 前端展示 clarify_question, 用户补全后重跑(复用同 thread_id)",
    },

    # ======================================================================
    # BC-10  legal_qa · 跨库复合查询 (industry 挂载不对称 + 质量门放大)
    # ======================================================================
    {
        "id": "BC-10",
        "keywords": ['建设工程', '质量保修', '租赁期限'],
        "task": "legal_qa",
        "title": "跨库复合查询·建设工程质保期 + 房屋租赁期限",
        "input": "建设工程的最低保修期限是多久？另外房屋租赁合同的租赁期限最长能签多少年？",
        "extra_state": {},
        "focus": [
            "retrieval_intent_decompose_node.KEYWORD_RULES (半挂载: '建设工程'命中, '租赁期限'不命中)",
            "_detect_retrieval_intents ('多久'不是 marker → general, 无偏好关系偏置)",
            "quality_gate_retry_node (复合查询质量分易低 → 重试放大)",
        ],
        "hypothesis": (
            "**挂载不对称的确证用例**: "
            "'建设工程' 命中 KEYWORD_RULES 第 1 组 → industry_sources 被挂载; "
            "但 '房屋租赁期限' 未命中任何组。也就是说《城市房屋租赁管理办法》能被召回, "
            "**完全是因为同一句话里碰巧出现了'建设工程'四个字** —— 语义上毫无关系。"
            "把'建设工程的最低保修期限是多久？'删掉, 第二问就 100% 召回不到。"
            "这个'借光式挂载'是最容易被忽略、也最难排查的一类 badcase。"
            "同时复合查询意图会判为 general(无 marker 命中), Stage1 的 recall×1.3 偏置与 "
            "Stage2 的 +0.1 加成双双失效。"
        ),
        "severity": "P0",
        "golden": [
            {"doc": "住建部标准", "article": "第三条",
             "must_any": ["质量保修", "合理使用年限", "地基基础工程"]},
            {"doc": "城市房屋租赁管理办法", "article": "第四条",
             "must_any": ["二十年", "超过部分无效"]},
        ],
        "expected": {
            "route_contains": ["xiaohongshu_publish_intent", "intent_router", "qa"],
            "full_route_contains": [
                "qa::qa_retrieval::retrieval_intent_decompose",
                "qa::qa_retrieval::retrieval_entity_recall",
            ],
            "state_checks": [
                {"field": "task_type", "op": "eq", "value": "legal_qa"},
                {"field": "output", "op": "nonempty"},
            ],
        },
        "quality_checks": [
            {"rule": "no_placeholder", "severity": "blocker"},
            {"rule": "citation_has_law_name", "severity": "major"},
            {"rule": "retry_not_wasted", "severity": "major"},
            {"rule": "retrieval_nonempty", "severity": "blocker"},
        ],
        "manual_takeover": "复合查询建议拆分为多子查询并行检索 (multi-facet, 见文档 5.2)",
    },
]


def all_cases():
    return list(CASES)


def get_case(cid):
    for c in CASES:
        if c["id"] == cid:
            return c
    return None


def by_severity(sev):
    return [c for c in CASES if c.get("severity") == sev]


if __name__ == "__main__":
    print(f"共 {len(CASES)} 条 badcase:")
    for c in CASES:
        print(f"  {c['id']}  [{c['severity']}]  {c['task']:<20} {c['title']}")
