# -*- coding: utf-8 -*-
"""8 大任务代表性测试集

【Grounding 原则】
    每一条 golden 答案都来自 outputs/kb_manifest.json 中**真实存在**的
    (文档名, 条款号) 组合。已通过 t_probe_data 逐条核验, 避免使用
    "看起来应该有、其实没入库"的条款, 否则召回率会被数据缺失污染,
    无法归因到检索算法本身。

【用例四分类】
    normal    正常业务输入 —— 测主链路是否跑通、召回是否命中
    boundary  边界输入     —— 测守卫/澄清分支是否按设计拦截
    exception 异常输入     —— 测空输入/损坏输入是否优雅降级
    negative  对抗输入     —— 测幻觉防御 (问库外法条, 不应编造)

【字段说明】
    id            用例编号 (<任务>-<序号>)
    task          任务类型, 对应 TASK_META
    category      normal / boundary / exception / negative
    desc          用例意图一句话
    input         用户输入 (或合同/案情正文)
    extra_state   额外注入的 AgentState 字段 (绕过澄清守卫等)
    golden        检索标准答案 [{"doc","article","must_any"}]
    expected      结构断言:
        route_contains  主图执行路径必须包含的节点(子序列匹配)
        route_excludes  不应出现的节点
        branch          期望命中的分支标记 (如 qa:llm_direct_out)
        state_checks    AgentState 字段断言 [{field, op, value}]
    quality_checks 人工修改率代理规则 [{rule, ...}]
"""

DATASETS = []


def _case(**kw):
    DATASETS.append(kw)
    return kw


# ============================================================================
# T1 · 首页问答 (legal_qa) —— QA 子图三级路由: 法律相关→检索→组织答案 / 非法律→LLM直答
# ============================================================================
_case(
    id="QA-01", task="legal_qa", category="normal",
    desc="库内法律概念定义类提问 (个人独资企业定义与责任承担)",
    input="个人独资企业法里是怎么界定个人独资企业的？投资人对企业债务要承担什么责任？",
    golden=[
        {"doc": "个人独资企业法", "article": "第二条", "must_any": ["自然人投资", "无限责任"]},
        {"doc": "个人独资企业法", "article": "第十八条", "must_any": ["家庭共有财产"]},
    ],
    expected={
        "route_contains": ["intent_router", "qa"],
        "state_checks": [
            {"field": "is_legal_related", "op": "eq", "value": True},
            {"field": "output", "op": "nonempty"},
            {"field": "citations", "op": "min_len", "value": 1},
        ],
    },
    quality_checks=[
        {"rule": "no_hallucinated_citation", "severity": "block"},
        {"rule": "output_nonempty", "severity": "block"},
        {"rule": "golden_recall_min", "value": 1, "severity": "major"},
        {"rule": "must_contain_any", "field": "output",
         "value": ["第二条", "无限责任", "个人独资企业"], "severity": "major"},
    ],
)

_case(
    id="QA-02", task="legal_qa", category="normal",
    desc="个人信息保护场景 (工作场所人脸识别合规性)",
    input="公司在办公区域安装人脸识别考勤设备，员工不同意，这样做合法吗？",
    golden=[
        {"doc": "中华人民共和国个人信息保护法", "article": "第二十六条",
         "must_any": ["公共场所", "图像采集", "维护公共安全"]},
    ],
    expected={
        "route_contains": ["intent_router", "qa"],
        "state_checks": [
            {"field": "is_legal_related", "op": "eq", "value": True},
            {"field": "output", "op": "nonempty"},
        ],
    },
    quality_checks=[
        {"rule": "no_hallucinated_citation", "severity": "block"},
        {"rule": "output_nonempty", "severity": "block"},
        {"rule": "golden_recall_min", "value": 1, "severity": "major"},
    ],
)

_case(
    id="QA-03", task="legal_qa", category="normal",
    desc="部门规章类提问 (房屋租赁期限上限)",
    input="房屋租赁合同最长可以签多少年？签了超过期限的部分有效吗？",
    golden=[
        {"doc": "城市房屋租赁管理办法", "article": "第四条",
         "must_any": ["不得超过二十年", "超过部分无效"]},
    ],
    expected={
        "route_contains": ["intent_router", "qa"],
        "state_checks": [{"field": "output", "op": "nonempty"}],
    },
    quality_checks=[
        {"rule": "no_hallucinated_citation", "severity": "block"},
        {"rule": "output_nonempty", "severity": "block"},
        {"rule": "golden_recall_min", "value": 1, "severity": "major"},
    ],
)

_case(
    id="QA-04", task="legal_qa", category="normal",
    desc="司法解释类提问 (劳动争议仲裁时效)",
    input="劳动争议申请仲裁的时效期间是多久？从什么时候开始计算？",
    golden=[
        {"doc": "劳动法司法解释", "article": "第三条", "must_any": ["一年", "仲裁时效"]},
    ],
    expected={
        "route_contains": ["intent_router", "qa"],
        "state_checks": [{"field": "output", "op": "nonempty"}],
    },
    quality_checks=[
        {"rule": "no_hallucinated_citation", "severity": "block"},
        {"rule": "output_nonempty", "severity": "block"},
        {"rule": "golden_recall_min", "value": 1, "severity": "major"},
    ],
)

_case(
    id="QA-05", task="legal_qa", category="boundary",
    desc="非法律闲聊输入 —— 应走 LLM 直答分支, 不进检索子图",
    input="你好呀，帮我写一首描写春天的短诗吧。",
    golden=[],
    expected={
        "route_contains": ["intent_router", "qa"],
        "branch": "qa:llm_direct_out",
        "state_checks": [
            {"field": "is_legal_related", "op": "eq", "value": False},
            {"field": "output", "op": "nonempty"},
        ],
    },
    quality_checks=[
        {"rule": "output_nonempty", "severity": "block"},
        {"rule": "must_not_contain", "field": "output",
         "value": ["根据《", "本法规定", "第", "条"], "severity": "minor"},
    ],
)

_case(
    id="QA-06", task="legal_qa", category="negative",
    desc="幻觉防御 —— 询问未入库的《民法典》条款, 不应编造具体条文",
    input="民法典第五百八十五条关于违约金是怎么具体规定的？原文是什么？",
    golden=[],
    expected={
        "route_contains": ["intent_router", "qa"],
        "state_checks": [{"field": "output", "op": "nonempty"}],
    },
    quality_checks=[
        {"rule": "no_hallucinated_citation", "severity": "block"},
        {"rule": "output_nonempty", "severity": "block"},
        {"rule": "must_not_contain", "field": "output",
         "value": ["当事人可以约定一方违约时应当根据违约情况向对方支付一定数额的违约金"],
         "severity": "block"},
    ],
)


# ============================================================================
# T2 · 合同审核 (contract_review) —— 输入分流 → 文本识别 → 预处理5节点 → 检索 → 双审
# ============================================================================
_CONTRACT_RENT = """房屋租赁合同

出租方（甲方）：王建国
承租方（乙方）：李小雨

第一条 甲方将位于上海市浦东新区世纪大道 100 号 3 单元 1802 室的房屋出租给乙方，
建筑面积 89 平方米，用途为居住。

第二条 租赁期限共 25 年，自 2024 年 1 月 1 日起至 2048 年 12 月 31 日止。

第三条 月租金人民币 12000 元，乙方应于每月 5 日前支付。押金 24000 元。

第四条 乙方逾期支付租金的，每逾期一日按月租金的 5% 向甲方支付违约金；
逾期超过 15 日的，甲方有权解除合同并要求乙方支付相当于三个月租金的违约金。

第五条 租赁期间，甲方不承担任何房屋维修义务，所有维修费用由乙方承担。

第六条 甲方如需出售该房屋，无需事先通知乙方。

第七条 乙方不得对房屋进行任何装修或改变房屋用途。

第八条 本合同自双方签字之日起生效。
"""

_case(
    id="CR-01", task="contract_review", category="normal",
    desc="房屋租赁合同 —— 租赁期限超 20 年 + 日 5% 高额违约金 + 免除出租人维修义务",
    input=_CONTRACT_RENT,
    golden=[
        {"doc": "城市房屋租赁管理办法", "article": "第四条", "must_any": ["二十年"]},
        {"doc": "城市房屋租赁管理办法", "article": "第七条", "must_any": ["支付租金", "违约责任"]},
        {"doc": "城市房屋租赁管理办法", "article": "第六条", "must_any": ["维修义务"]},
        {"doc": "城市房屋租赁管理办法", "article": "第九条", "must_any": ["优先购买"]},
    ],
    expected={
        "route_contains": ["intent_router", "input_source_router", "text_recognize",
                           "preprocess", "cc_retrieval", "dual_review"],
        "state_checks": [
            {"field": "text_recognize_flag", "op": "eq", "value": "pass"},
            {"field": "doc_text", "op": "nonempty"},
            {"field": "output", "op": "nonempty"},
            {"field": "contract_type", "op": "nonempty"},
        ],
    },
    quality_checks=[
        {"rule": "no_hallucinated_citation", "severity": "block"},
        {"rule": "output_nonempty", "severity": "block"},
        {"rule": "risk_item_nonempty", "severity": "major"},
        {"rule": "golden_recall_min", "value": 1, "severity": "major"},
        {"rule": "must_contain_any", "field": "output",
         "value": ["二十年", "20年", "违约金", "风险"], "severity": "major"},
    ],
)

_CONTRACT_CONSTRUCTION = """建设工程施工合同

发包人（甲方）：星辰置业有限公司
承包人（乙方）：宏远建设集团有限公司

第一条 工程名称：星辰中心 A 座写字楼；地点：杭州市滨江区。

第二条 承包范围：土建、安装、装饰装修及室外配套工程。

第三条 合同总价：人民币 8600 万元（固定总价包干）。

第四条 工期：总日历天数 420 天，自 2024 年 3 月 1 日开工。

第五条 工程质量标准：达到国家施工验收合格标准。

第六条 违约责任：乙方逾期竣工的，每日按合同总价的 1% 支付违约金；
甲方逾期付款的，每日按应付金额的 0.1% 支付违约金。
任何一方违约，违约金总额不超过合同总价的 30%。

第七条 工程价款支付：预付款 10%，进度款按月完成量的 70% 支付，竣工结算后付至 97%，
余款 3% 作为质量保证金。

第八条 质量保修：本工程不设质量保修期，竣工验收后乙方不再承担保修责任。

第九条 乙方可将本工程的部分专业工程分包给具备相应资质的单位。
"""

_case(
    id="CR-02", task="contract_review", category="normal",
    desc="建设工程施工合同 —— 违约金 30% 超住建部标准上限 + 排除质量保修责任",
    input=_CONTRACT_CONSTRUCTION,
    golden=[
        {"doc": "住建部标准", "article": "第五条", "must_any": ["违约金", "百分之二十"]},
        {"doc": "住建部标准", "article": "第三条", "must_any": ["质量保修"]},
    ],
    expected={
        "route_contains": ["intent_router", "input_source_router", "preprocess",
                           "cc_retrieval", "dual_review"],
        "state_checks": [
            {"field": "output", "op": "nonempty"},
            {"field": "doc_segments", "op": "min_len", "value": 3},
        ],
    },
    quality_checks=[
        {"rule": "no_hallucinated_citation", "severity": "block"},
        {"rule": "output_nonempty", "severity": "block"},
        {"rule": "risk_item_nonempty", "severity": "major"},
        {"rule": "golden_recall_min", "value": 1, "severity": "major"},
    ],
)

_CONTRACT_PI = """个人信息委托处理协议

委托方（甲方）：云图数据科技有限公司
受托方（乙方）：智擎信息技术有限公司

第一条 甲方委托乙方处理其收集的用户个人信息，包括姓名、手机号、
身份证号、人脸图像及行踪轨迹数据。

第二条 处理目的：用户画像分析与精准营销。

第三条 乙方可自行决定将上述个人信息提供给其合作的第三方数据服务商，
无需另行通知用户或取得用户同意。

第四条 本协议未约定处理期限，乙方可无限期保存上述个人信息。

第五条 甲方不对乙方的处理活动进行任何监督，亦不要求进行安全评估。

第六条 用户要求删除其个人信息时，乙方有权拒绝。
"""

_case(
    id="CR-03", task="contract_review", category="normal",
    desc="个人信息委托处理协议 —— 缺失单独同意 / 无限期保存 / 无监督义务",
    input=_CONTRACT_PI,
    golden=[
        {"doc": "中华人民共和国个人信息保护法", "article": "第二十三条",
         "must_any": ["单独同意", "提供"]},
        {"doc": "中华人民共和国个人信息保护法", "article": "第二十一条",
         "must_any": ["委托处理", "监督"]},
        {"doc": "中华人民共和国个人信息保护法", "article": "第十九条",
         "must_any": ["保存期限", "最短时间"]},
    ],
    expected={
        "route_contains": ["intent_router", "input_source_router", "preprocess",
                           "cc_retrieval", "dual_review"],
        "state_checks": [
            {"field": "output", "op": "nonempty"},
            {"field": "citations", "op": "min_len", "value": 1},
        ],
    },
    quality_checks=[
        {"rule": "no_hallucinated_citation", "severity": "block"},
        {"rule": "output_nonempty", "severity": "block"},
        {"rule": "risk_item_nonempty", "severity": "major"},
        {"rule": "golden_recall_min", "value": 1, "severity": "major"},
    ],
)

_case(
    id="CR-04", task="contract_review", category="exception",
    desc="空输入 —— 应被 text_recognize 守卫拦截, 不进预处理/检索/双审",
    input="   ",
    golden=[],
    expected={
        "route_contains": ["input_source_router", "text_recognize"],
        "route_excludes": ["preprocess", "dual_review"],
        "branch": "text_recognize:block",
        "state_checks": [
            {"field": "text_recognize_flag", "op": "eq", "value": "block"},
        ],
    },
    quality_checks=[
        {"rule": "output_nonempty", "severity": "block"},
        {"rule": "must_not_contain", "field": "output",
         "value": ["风险等级", "## 合同审核报告"], "severity": "minor"},
    ],
)

_case(
    id="CR-05", task="contract_review", category="boundary",
    desc="非合同文本 (一段散文) —— text_recognize 应判定非合同并拦截",
    input=("那是一个深秋的午后，梧桐叶铺满了整条小巷。我踩着落叶往前走，"
           "听见远处传来悠扬的二胡声。老人坐在门前的藤椅上，闭着眼睛，"
           "弓子在弦上缓缓地推拉。阳光斜斜地照在他的肩上，像是给他披了一件金色的外衣。"),
    golden=[],
    expected={
        "route_contains": ["input_source_router", "text_recognize"],
        "route_excludes": ["preprocess", "dual_review"],
        "branch": "text_recognize:block",
        "state_checks": [
            {"field": "text_recognize_flag", "op": "eq", "value": "block"},
        ],
    },
    quality_checks=[
        {"rule": "output_nonempty", "severity": "block"},
    ],
)


# ============================================================================
# T3 · 合规审查 (compliance_review) —— 与合同审核同路径, 但双审子图走单审分支
# ============================================================================
_case(
    id="CP-01", task="compliance_review", category="normal",
    desc="办公场所人脸识别考勤未履行告知义务 —— 合规风险筛查",
    input=("我司拟在办公区域及卫生间门口安装人脸识别考勤设备，用于员工考勤管理，"
           "未单独向员工告知并取得同意，也未设置提示标识。请审查该做法的合规风险。"),
    golden=[
        {"doc": "中华人民共和国个人信息保护法", "article": "第二十六条",
         "must_any": ["公共场所", "提示标识"]},
        {"doc": "中华人民共和国个人信息保护法", "article": "第十七条", "must_any": ["告知"]},
        {"doc": "中华人民共和国个人信息保护法", "article": "第二十九条",
         "must_any": ["单独同意"]},
    ],
    expected={
        "route_contains": ["intent_router", "input_source_router", "preprocess",
                           "cc_retrieval", "dual_review"],
        "state_checks": [
            {"field": "output", "op": "nonempty"},
        ],
    },
    quality_checks=[
        {"rule": "no_hallucinated_citation", "severity": "block"},
        {"rule": "output_nonempty", "severity": "block"},
        {"rule": "golden_recall_min", "value": 1, "severity": "major"},
    ],
)

_case(
    id="CP-02", task="compliance_review", category="normal",
    desc="App 向第三方共享用户手机号做营销, 未取得单独同意",
    input=("我司运营的电商 App 在用户注册时通过一揽子隐私政策取得同意，"
           "现将用户的手机号与购物偏好数据共享给第三方广告公司用于精准营销，"
           "未就该共享行为单独取得用户同意，也未告知接收方名称。请做合规审查。"),
    golden=[
        {"doc": "中华人民共和国个人信息保护法", "article": "第二十三条",
         "must_any": ["单独同意", "接收方"]},
        {"doc": "中华人民共和国个人信息保护法", "article": "第二十四条",
         "must_any": ["自动化决策"]},
    ],
    expected={
        "route_contains": ["intent_router", "input_source_router", "preprocess",
                           "cc_retrieval", "dual_review"],
        "state_checks": [{"field": "output", "op": "nonempty"}],
    },
    quality_checks=[
        {"rule": "no_hallucinated_citation", "severity": "block"},
        {"rule": "output_nonempty", "severity": "block"},
        {"rule": "golden_recall_min", "value": 1, "severity": "major"},
    ],
)

_case(
    id="CP-03", task="compliance_review", category="normal",
    desc="个人独资企业未为职工缴纳社保、未签劳动合同 —— 用工合规审查",
    input=("我经营一家个人独资企业，雇了 8 名员工，一直未与职工签订书面劳动合同，"
           "也未依法为职工缴纳社会保险费，职工工资按现金发放。请审查该用工模式的合规风险。"),
    golden=[
        {"doc": "个人独资企业法", "article": "第二十三条", "must_any": ["社会保险"]},
        {"doc": "个人独资企业法", "article": "第二十二条", "must_any": ["劳动合同", "工资"]},
        {"doc": "个人独资企业法", "article": "第三十九条", "must_any": ["处罚", "责任"]},
    ],
    expected={
        "route_contains": ["intent_router", "input_source_router", "preprocess",
                           "cc_retrieval", "dual_review"],
        "state_checks": [{"field": "output", "op": "nonempty"}],
    },
    quality_checks=[
        {"rule": "no_hallucinated_citation", "severity": "block"},
        {"rule": "output_nonempty", "severity": "block"},
        {"rule": "golden_recall_min", "value": 1, "severity": "major"},
    ],
)


# ============================================================================
# T4 · 文书生成 (legal_document_gen) —— docgen 子图 7 节点 + 澄清守卫
# ============================================================================
_case(
    id="DG-01", task="legal_document_gen", category="normal",
    desc="要素完整的劳动争议起诉状 —— 应走完整链路并引用二倍赔偿金规则",
    input=("2023 年 3 月，被告上海某贸易公司以原告张某严重违反规章制度为由单方解除劳动合同，"
           "但该公司从未向张某告知过该规章制度，且报销单据均经主管审批。请求判令被告支付"
           "违法解除劳动合同赔偿金。"),
    extra_state={
        "dispute_type": "劳动争议",
        "plaintiff": "张某",
        "defendant": "上海某贸易公司",
        "claims": "请求判令被告支付违法解除劳动合同赔偿金 200000 元",
    },
    golden=[
        {"doc": "劳动法司法解释", "article": "第七条", "must_any": ["二倍", "赔偿金"]},
        {"doc": "劳动法司法解释", "article": "第四条", "must_any": ["举证责任"]},
    ],
    expected={
        "route_contains": ["intent_router", "docgen"],
        "state_checks": [
            {"field": "need_clarify", "op": "eq", "value": False},
            {"field": "output", "op": "nonempty"},
        ],
    },
    quality_checks=[
        {"rule": "no_hallucinated_citation", "severity": "block"},
        {"rule": "output_nonempty", "severity": "block"},
        {"rule": "document_field_filled", "severity": "block"},
        {"rule": "must_contain_any", "field": "output",
         "value": ["张某", "上海某贸易公司", "起诉状", "诉请", "请求"], "severity": "major"},
        {"rule": "golden_recall_min", "value": 1, "severity": "major"},
    ],
)

_case(
    id="DG-02", task="legal_document_gen", category="normal",
    desc="房屋租赁违约金纠纷起诉状 —— 租期超 20 年 + 违约金过高",
    input=("原告李小雨与被告王建国签订 25 年房屋租赁合同，现因租赁期限超过法定上限、"
           "逾期付款违约金按日 5% 计算明显过高产生争议，请生成民事起诉状。"),
    extra_state={
        "dispute_type": "房屋租赁合同纠纷",
        "plaintiff": "李小雨",
        "defendant": "王建国",
        "claims": "请求确认超过二十年的租赁期限部分无效，并调减违约金",
    },
    golden=[
        {"doc": "城市房屋租赁管理办法", "article": "第四条", "must_any": ["二十年"]},
    ],
    expected={
        "route_contains": ["intent_router", "docgen"],
        "state_checks": [
            {"field": "need_clarify", "op": "eq", "value": False},
            {"field": "output", "op": "nonempty"},
        ],
    },
    quality_checks=[
        {"rule": "no_hallucinated_citation", "severity": "block"},
        {"rule": "output_nonempty", "severity": "block"},
        {"rule": "document_field_filled", "severity": "block"},
        {"rule": "must_contain_any", "field": "output",
         "value": ["李小雨", "王建国", "起诉状", "请求"], "severity": "major"},
    ],
)

_case(
    id="DG-03", task="legal_document_gen", category="boundary",
    desc="案情要素严重缺失 —— 应触发澄清守卫, 返回追问而非残缺文书",
    input="帮我写一份起诉状。",
    golden=[],
    expected={
        "route_contains": ["intent_router", "docgen"],
        "branch": "docgen:clarify",
        "state_checks": [
            {"field": "need_clarify", "op": "eq", "value": True},
            {"field": "clarify_question", "op": "nonempty"},
        ],
    },
    quality_checks=[
        {"rule": "output_nonempty", "severity": "block"},
        {"rule": "must_not_contain", "field": "output",
         "value": ["此致", "具状人", "起诉请求"], "severity": "major"},
    ],
)


# ============================================================================
# T5 · 法规查询 (legal_research) —— 独立检索路径, 挂 laws+regulations+interpretations
# ============================================================================
_case(
    id="LR-01", task="legal_research", category="normal",
    desc="设立个人独资企业的法定条件",
    input="设立个人独资企业应当具备哪些条件？",
    golden=[
        {"doc": "个人独资企业法", "article": "第八条", "must_any": ["自然人", "企业名称", "出资"]},
        {"doc": "个人独资企业法", "article": "第九条", "must_any": ["设立申请书", "登记机关"]},
    ],
    expected={
        "route_contains": ["intent_router", "r_retrieval"],
        "state_checks": [
            {"field": "output", "op": "nonempty"},
            {"field": "citations", "op": "min_len", "value": 1},
        ],
    },
    quality_checks=[
        {"rule": "no_hallucinated_citation", "severity": "block"},
        {"rule": "output_nonempty", "severity": "block"},
        {"rule": "golden_recall_min", "value": 1, "severity": "major"},
        {"rule": "quality_score_min", "value": 50, "severity": "major"},
    ],
)

_case(
    id="LR-02", task="legal_research", category="normal",
    desc="不动产登记机构办结登记手续的法定时限",
    input="不动产登记机构应当自受理登记申请之日起多少时间内办结不动产登记手续？",
    golden=[
        {"doc": "不动产登记暂行条例", "article": "第二十条", "must_any": ["30个工作日"]},
    ],
    expected={
        "route_contains": ["intent_router", "r_retrieval"],
        "state_checks": [{"field": "output", "op": "nonempty"}],
    },
    quality_checks=[
        {"rule": "no_hallucinated_citation", "severity": "block"},
        {"rule": "output_nonempty", "severity": "block"},
        {"rule": "golden_recall_min", "value": 1, "severity": "major"},
    ],
)

_case(
    id="LR-03", task="legal_research", category="normal",
    desc="个人所得税法实施条例中劳务报酬所得的范围界定",
    input="个人所得税法中的劳务报酬所得具体包括哪些？",
    golden=[
        {"doc": "个人所得税法实施条例", "article": "第六条", "must_any": ["劳务报酬所得", "设计", "咨询"]},
    ],
    expected={
        "route_contains": ["intent_router", "r_retrieval"],
        "state_checks": [{"field": "output", "op": "nonempty"}],
    },
    quality_checks=[
        {"rule": "no_hallucinated_citation", "severity": "block"},
        {"rule": "output_nonempty", "severity": "block"},
        {"rule": "golden_recall_min", "value": 1, "severity": "major"},
    ],
)

_case(
    id="LR-04", task="legal_research", category="normal",
    desc="信用卡恶意透支的认定标准 (刑事司法解释)",
    input="持卡人恶意透支，在什么情形下应当认定为刑法第一百九十六条规定的恶意透支？",
    golden=[
        {"doc": "最高人民法院、最高人民检察院关于办理妨害信用卡管理刑事案件具体应用法律若干问题的解释",
         "article": "第六条", "must_any": ["非法占有为目的", "两次有效催收", "三个月"]},
        {"doc": "最高人民法院、最高人民检察院关于办理妨害信用卡管理刑事案件具体应用法律若干问题的解释",
         "article": "第七条", "must_any": ["有效催收", "三十日"]},
    ],
    expected={
        "route_contains": ["intent_router", "r_retrieval"],
        "state_checks": [{"field": "output", "op": "nonempty"}],
    },
    quality_checks=[
        {"rule": "no_hallucinated_citation", "severity": "block"},
        {"rule": "output_nonempty", "severity": "block"},
        {"rule": "golden_recall_min", "value": 1, "severity": "major"},
    ],
)


# ============================================================================
# T6 · 案例检索 (case_search) —— 单源直查 cases
#
# ⚠️ 已知结构性缺陷 (由 t_probe_data 检出): cases 源在 Neo4j 中无任何 Article 节点,
#    而检索链只查 :Article → 本组用例预期大面积 0 命中。这正是本测试集要暴露的问题,
#    失败归因会指向 KNOWLEDGE_GAP / STRUCTURAL_DEFECT 而非检索算法。
# ============================================================================
_case(
    id="CS-01", task="case_search", category="normal",
    desc="违法解除劳动合同赔偿金类案检索 (库中确有该 :Case 节点)",
    input="公司违法解除劳动合同，需要支付赔偿金的案例",
    golden=[
        {"doc": "张某与上海某贸易公司违法解除劳动合同赔偿金纠纷案", "article": "",
         "must_any": ["违法解除", "赔偿金", "规章制度"]},
    ],
    expected={
        "route_contains": ["intent_router", "r_retrieval"],
        "state_checks": [{"field": "output", "op": "nonempty"}],
    },
    quality_checks=[
        {"rule": "output_nonempty", "severity": "block"},
        {"rule": "golden_recall_min", "value": 1, "severity": "major"},
    ],
)

_case(
    id="CS-02", task="case_search", category="normal",
    desc="离婚纠纷类案检索 (库中确有该 :Case 节点)",
    input="因一方沉迷赌博导致夫妻感情破裂的离婚纠纷案例",
    golden=[
        {"doc": "李某与王某离婚纠纷案", "article": "", "must_any": ["离婚", "赌博", "感情破裂"]},
    ],
    expected={
        "route_contains": ["intent_router", "r_retrieval"],
        "state_checks": [{"field": "output", "op": "nonempty"}],
    },
    quality_checks=[
        {"rule": "output_nonempty", "severity": "block"},
        {"rule": "golden_recall_min", "value": 1, "severity": "major"},
    ],
)

_case(
    id="CS-03", task="case_search", category="negative",
    desc="房屋租赁违约金纠纷 —— 该案例文件未入库, 应优雅返回空结果而非编造案号",
    input="承租人提前退租，中介公司要求支付两个月租金作为违约金，法院予以调减的案例",
    golden=[],
    expected={
        "route_contains": ["intent_router", "r_retrieval"],
        "state_checks": [{"field": "output", "op": "nonempty"}],
    },
    quality_checks=[
        {"rule": "output_nonempty", "severity": "block"},
        {"rule": "must_not_contain", "field": "output",
         "value": ["(2023)沪0115民初98765号", "某房产中介公司"], "severity": "block"},
    ],
)


# ============================================================================
# T7 · 历史记录 (history) —— 纯存储层, 不走 LangGraph
#     每个用例是 HistoryStore 的一个方法级行为契约
# ============================================================================
_case(
    id="HS-01", task="history", category="normal",
    desc="写入后立即回读, 字段应完全一致 (user_input/result 需 JSON 往返无损)",
    input="history.store_get_roundtrip",
    extra_state={"op": "store_get_roundtrip",
                 "payload": {"task_type": "qa", "title": "违约金上限咨询",
                             "user_input": {"query": "违约金上限是多少"},
                             "result": {"output": "依据第二十条...", "citations": 3},
                             "summary": "违约金上限咨询"}},
    golden=[], expected={"state_checks": []},
    quality_checks=[{"rule": "history_roundtrip", "severity": "block"}],
)

_case(
    id="HS-02", task="history", category="normal",
    desc="分页查询: 写入 12 条, 取第 2 页 (page_size=5) 应返回 5 条且总数正确",
    input="history.list_pagination",
    extra_state={"op": "list_pagination", "payload": {"n": 12, "page": 2, "page_size": 5,
                                                      "task_type": "hs_pagination"}},
    golden=[], expected={"state_checks": []},
    quality_checks=[{"rule": "history_pagination", "severity": "block"}],
)

_case(
    id="HS-03", task="history", category="normal",
    desc="按 task_type 筛选: 混入两种类型, 筛选结果不应串类型",
    input="history.filter_by_task_type",
    extra_state={"op": "filter_by_task_type",
                 "payload": {"types": [("contract", 3), ("docgen", 2)],
                             "task_type": "hs_filter"}},
    golden=[], expected={"state_checks": []},
    quality_checks=[{"rule": "history_filter", "severity": "block"}],
)

_case(
    id="HS-04", task="history", category="normal",
    desc="收藏状态切换: 0→1→0 幂等; 不存在的 id 返回 None 而非抛异常",
    input="history.toggle_star",
    extra_state={"op": "toggle_star", "payload": {"task_type": "hs_star"}},
    golden=[], expected={"state_checks": []},
    quality_checks=[{"rule": "history_star", "severity": "block"}],
)

_case(
    id="HS-05", task="history", category="normal",
    desc="删除: 已存在的记录删除成功, 重复删除返回 False 而非报错",
    input="history.delete",
    extra_state={"op": "delete", "payload": {"task_type": "hs_delete"}},
    golden=[], expected={"state_checks": []},
    quality_checks=[{"rule": "history_delete", "severity": "block"}],
)

_case(
    id="HS-06", task="history", category="boundary",
    desc="边界: 空 user_input/超长 summary(>200字) 应被裁断且不抛异常",
    input="history.boundary_payload",
    extra_state={"op": "boundary_payload",
                 "payload": {"task_type": "hs_boundary",
                             "summary": "超长摘要" + "啊" * 300}},
    golden=[], expected={"state_checks": []},
    quality_checks=[{"rule": "history_boundary", "severity": "block"}],
)

_case(
    id="HS-07", task="history", category="normal",
    desc="端到端: 真实 graph 结果落库后能完整回读 (含 citations 结构化字段)",
    input="history.e2e_graph_persist",
    extra_state={"op": "e2e_graph_persist",
                 "payload": {"query": "个人独资企业的设立条件有哪些？",
                             "task_type": "legal_research"}},
    golden=[], expected={"state_checks": []},
    quality_checks=[{"rule": "history_e2e", "severity": "block"}],
)


# ============================================================================
# T8 · 小红书发布 (xiaohongshu_publish) —— Level 1 直连, 独立子图
#     注意: SAFE MODE 下自动发布节点被桩替换, 不会真实打开浏览器发帖
# ============================================================================
_case(
    id="XH-01", task="xiaohongshu_publish", category="normal",
    desc="租房避坑普法内容 —— 应走 文案→配图→合规检查→(桩)发布→markdown 完整链路",
    input="帮我生成一篇关于租客签合同前必看的 5 个法律避坑点的小红书笔记并发布",
    golden=[],
    expected={
        "route_contains": ["xiaohongshu_publish_intent", "xhs"],
        "branch": "xhs:publish",
        "state_checks": [
            {"field": "is_xiaohongshu_publish_intent", "op": "eq", "value": True},
            {"field": "output", "op": "nonempty"},
        ],
    },
    quality_checks=[
        {"rule": "output_nonempty", "severity": "block"},
        {"rule": "xhs_content_fields", "severity": "major"},
    ],
)

_case(
    id="XH-02", task="xiaohongshu_publish", category="boundary",
    desc="含违规承诺的营销文案 (包赢官司/100%胜诉) —— 合规检查应拦截, 不进入发布",
    input=("帮我写一篇小红书推广：我们是全国最专业的律所，包赢官司，"
           "100% 胜诉，不上诉不收费，加微信立即办理。"),
    golden=[],
    expected={
        "route_contains": ["xiaohongshu_publish_intent", "xhs"],
        "state_checks": [{"field": "output", "op": "nonempty"}],
    },
    quality_checks=[
        {"rule": "output_nonempty", "severity": "block"},
        {"rule": "must_not_contain", "field": "output",
         "value": ["包赢官司", "100% 胜诉", "100%胜诉"], "severity": "block"},
    ],
)


# ============================================================================
# 工具函数
# ============================================================================
def get_cases(task=None, case_id=None, category=None, limit=None):
    """按条件筛选用例"""
    out = DATASETS
    if task:
        out = [c for c in out if c["task"] == task]
    if case_id:
        out = [c for c in out if c["id"] == case_id]
    if category:
        out = [c for c in out if c["category"] == category]
    if limit:
        out = out[:limit]
    return out


def all_tasks():
    seen, out = set(), []
    for c in DATASETS:
        if c["task"] not in seen:
            seen.add(c["task"])
            out.append(c["task"])
    return out


if __name__ == "__main__":
    print(f"用例总数: {len(DATASETS)}")
    for t in sorted(all_tasks()):
        cs = get_cases(task=t)
        cats = {}
        for c in cs:
            cats[c["category"]] = cats.get(c["category"], 0) + 1
        print(f"  {t:<24} {len(cs):>2} 条  {cats}")
