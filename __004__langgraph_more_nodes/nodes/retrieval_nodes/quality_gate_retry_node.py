"""质量门禁重试节点: 策略调整 + 质量门控 (2026-08 重构)"""
# ============================================================
# 文件名称: nodes/retrieval_nodes/quality_gate_retry_node.py
# 文件作用: 检索质量门禁判定、策略调整与重试控制
# ============================================================
# 【这个文件是干什么的？】
# 质量门禁重试节点 —— 检索子图的"质量守门员 + 策略调整器"。
#
# 【代码逻辑主线】
# 1. 读取融合节点计算的 quality_score / quality_gate_passed / quality_retry_count 等字段
# 2. 判定质量门是否通过:
#    - 优先读取 quality_gate_passed (由上游节点写入)
#    - 兜底用质量分阈值 QUALITY_GATE_THRESHOLD (60分) 判定
# 3. 若未通过且重试次数未达上限 (MAX_QUALITY_RETRIES=3):
#    a. 递增重试计数器 (防死循环)
#    b. 执行策略调整: 关键词扩展
#    c. 返回 retry 路由 (条件路由函数 _quality_gate_router 将其路由回 retrieval_intent_decompose)
# 4. 若通过或达上限: 返回 pass 退出重试循环
#
# 【2026-08-23 重构要点】
#    - 质量门判定逻辑从 retrieval_output_pack_node 收敛到本节点 + fusion_ranking
#    - 新增策略调整逻辑: 关键词扩展 + 兜底降级
#    - 重试回边目标从 entity_recall 改为 retrieval_intent_decompose (让意图分解节点真正重新规划)
#
# 【新手建议】
#    先看主函数 quality_gate_retry_node -> 再看辅助函数 _expand_keywords。
#
# 📜 代码文字逻辑解析
# 本文件是 AI 法律助理(LangGraph 多智能体系统)中的"质量门禁重试节点"。
# 其核心职责是: 对检索结果进行质量判定, 不达标时自动调整检索策略(扩展关键词),
# 并将结果回传给意图分解节点重新规划查询。这是一个典型的"闭环反馈控制"模式:
# 检测(质量打分) → 判定(是否达标) → 调整(策略优化) → 重试(重新检索)。
# 节点采用"最大重试次数"防止死循环, 达上限后强制放行, 保证系统鲁棒性。


# 从项目共享模块导入 AgentState 类型, 作为节点函数的类型注解
from __004__langgraph_more_nodes.agent_state import AgentState

# 质量门阈值: 质量分 >= 此值才算通过 (与 fusion_ranking / output_pack 保持一致, 单位: 分)
QUALITY_GATE_THRESHOLD = 60

# 最大重试次数: 防止死循环, 达上限后强制放行 (即便是不及格的结果)
MAX_QUALITY_RETRIES = 3

# 关键词扩展词库 (按任务类型分组):
# 当检索质量不达标时, 从当前关键词出发, 添加同义词/上下位词/相关法律术语,
# 扩大检索范围, 提升召回率。结构: {任务类型: {触发词: [同义词列表]}}
_KEYWORD_EXPANSION_MAP = {
    # 合同审查任务: 覆盖合同常见条款的法律术语扩展
    "contract_review": {
        # 违约金相关: 涵盖违约责任、损失赔偿等多种表述
        "违约金": ["违约责任", "损失赔偿", "损害赔偿", "滞纳金", "罚息"],
        # 合同解除相关: 涵盖终止、解除权等多种表述
        "解除": ["终止", "解除权", "单方解除", "协商解除", "法定解除"],
        # 付款相关: 涵盖支付、给付、结算等多种表述
        "付款": ["支付", "给付", "结算", "对价", "报酬"],
        # 担保相关: 涵盖保证、抵押、质押、定金等多种担保形式
        "担保": ["保证", "抵押", "质押", "定金", "保证金"],
        # 管辖相关: 涵盖管辖法院、争议解决等多种表述
        "管辖": ["管辖法院", "争议解决", "诉讼管辖", "仲裁条款"],
        # 保密相关: 涵盖秘密、机密、非公开等多种表述
        "保密": ["秘密", "机密", "非公开", "披露", "泄露"],
        # 竞业限制相关: 涵盖竞业禁止、竞业约束等多种表述
        "竞业限制": ["竞业禁止", "竞业约束", "禁止竞争"],
        # 知识产权相关: 涵盖专利、商标、著作权等多种知识产权类型
        "知识产权": ["专利", "商标", "著作权", "版权", "所有权"],
    },
    # 合规审查任务: 覆盖合规性判断相关的法律术语
    "compliance_review": {
        # 合规相关: 涵盖合法、合法性、合规性等多种表述
        "合规": ["合法", "合法性", "合规性", "法律风险", "违法"],
        # 强制性规定相关: 涵盖强制规定、禁止性规定等多种表述
        "强制性": ["强制规定", "强制性规定", "禁止性规定", "法定"],
        # 公序良俗相关: 涵盖公共秩序、善良风俗等多种表述
        "公序良俗": ["公共秩序", "善良风俗", "社会公德"],
        # 效力相关: 涵盖合同效力、无效、可撤销等多种表述
        "效力": ["合同效力", "无效", "可撤销", "效力待定"],
    },
    # 法律问答任务: 覆盖常见法律领域的术语扩展
    "legal_qa": {
        # 劳动领域: 涵盖劳动法、劳动合同法等
        "劳动": ["劳动法", "劳动合同法", "劳动争议", "劳动仲裁"],
        # 合同领域: 涵盖合同法、民法典合同编等
        "合同": ["合同法", "民法典合同编", "合同纠纷"],
        # 物权领域: 涵盖物权法、民法典物权编等
        "物权": ["物权法", "民法典物权编", "所有权", "用益物权", "担保物权"],
        # 婚姻领域: 涵盖婚姻法、民法典婚姻编等
        "婚姻": ["婚姻法", "民法典婚姻编", "离婚", "财产分割", "子女抚养"],
        # 继承领域: 涵盖继承法、民法典继承编等
        "继承": ["继承法", "民法典继承编", "遗嘱", "遗产"],
        # 侵权领域: 涵盖侵权责任法、民法典侵权编等
        "侵权": ["侵权责任法", "民法典侵权编", "损害赔偿"],
    },
}


def _expand_keywords(keywords: list, task_type: str) -> list:
    """
    关键词扩展: 基于预定义词表, 为每个关键词添加同义词/相关词.

    作用:
        在检索质量不达标时, 扩展用户输入的关键词, 引入同义词、上下位词和相关法律术语,
        扩大检索召回范围, 提升检索结果的覆盖面。支持精确匹配和模糊匹配两种策略。

    参数:
        keywords (list): 原始关键词列表, 由意图分解节点生成
        task_type (str): 任务类型 (如 "contract_review"/"compliance_review"/"legal_qa"),
                         决定使用哪组词表进行扩展

    返回:
        list: 扩展后的关键词列表 (已去重, 保留原词在前, 新增词追加在后)

    可迁移性说明:
        本函数的"词表映射 + 精确/模糊匹配"架构可迁移到任何关键词扩展场景,
        例如: 搜索引擎查询扩展、电商搜索推荐、FAQ 问答匹配等。
        通过替换 _KEYWORD_EXPANSION_MAP 即可适配新业务领域。
    """
    # 复制原始关键词列表, 作为扩展结果的基础 (保留原词顺序)
    expanded = list(keywords)

    # 根据任务类型获取对应的扩展词表, 若无匹配则使用空字典
    expansion_map = _KEYWORD_EXPANSION_MAP.get(task_type, {})

    # 遍历每个原始关键词, 查找同义词
    for kw in keywords:
        # 清理关键词: 转为字符串并去除首尾空白
        kw_clean = str(kw).strip()

        # 策略1: 精确匹配 —— 关键词完全等于某个触发词 (如 "违约金" 完全匹配 "违约金")
        if kw_clean in expansion_map:
            # 遍历该触发词对应的所有同义词
            for synonym in expansion_map[kw_clean]:
                # 仅当同义词尚未在扩展列表中时才添加 (去重)
                if synonym not in expanded:
                    expanded.append(synonym)

        # 策略2: 模糊匹配 —— 关键词包含某个触发词 (如 "违约金条款" 包含 "违约金")
        # 遍历所有触发词, 检查当前关键词是否包含该触发词
        for trigger, syns in expansion_map.items():
            if trigger in kw_clean:
                # 遍历该触发词对应的所有同义词
                for synonym in syns:
                    # 仅当同义词尚未在扩展列表中时才添加 (去重)
                    if synonym not in expanded:
                        expanded.append(synonym)

    # 返回去重后的扩展关键词列表
    return expanded


def quality_gate_retry_node(state: AgentState):
    """
    质量门禁判定 + 策略调整节点 (2026-08 重构).

    作用:
        (1) 读取上游节点(融合检索节点 retrieval_fusion_ranking_node)写入的质量分 quality_score、
            质量门标志 quality_gate_passed 和重试计数 quality_retry_count;
        (2) 判定质量门是否通过:
            - 若质量门标志为 True 且质量分 >= 阈值 QUALITY_GATE_THRESHOLD(60): 通过;
            - 若重试次数 >= MAX_QUALITY_RETRIES(3): 强制放行(防死循环);
            - 否则: 未通过, 执行策略调整;
        (3) 策略调整: 关键词扩展(添加同义词);
        (4) 构建返回字段: 更新重试计数、质量门标志、扩展后的关键词/源等;
        (5) 由 _quality_gate_router 路由函数读取 quality_gate_passed 字段,
            决定下一个节点: 通过 → beida_fabao_gate; 未通过 → retrieval_intent_decompose(重试)。

    参数:
        state (AgentState): LangGraph 共享状态字典。读取字段:
                            - quality_score (float): 检索质量分 (由 retrieval_fusion_ranking_node 计算, 0-100)
                            - quality_gate_passed (bool): 质量门是否通过 (由 fusion_ranking 节点计算)
                            - quality_retry_count (int): 已重试次数 (初始为 0)
                            - retrieval_keywords (list): 当前检索关键词列表
                            - task_type (str): 任务类型 (决定关键词扩展使用哪组词表)
        写入字段 (通过返回值):
                            - quality_retry_count (int): 递增后的重试次数
                            - quality_gate_passed (bool): 质量门是否通过
                            - quality_max_retries (int): 最大重试次数 (供下游日志/调试)
            - retrieval_keywords (list): 扩展后的关键词 (仅重试且扩展有效时写入)

    返回值:
        dict: 需要更新到 state 的字段字典, 由 LangGraph 自动合并回全局状态。

    可迁移性说明:
        本节点的"质量判定 → 策略调整 → 闭环反馈"架构可迁移到任何质量控制场景,
        例如: 搜索引擎的结果质量反馈、AI 生成内容的质量迭代、数据清洗的质量闭环等。
        "最大重试次数 + 策略调整"是工程上平衡质量与成本的经典模式, 推荐保留。
    """
    # 打印节点开始日志 (带 QA 子图标识)
    print("QA子图 [质量门禁 + 策略调整]")

    # 从状态字典中读取质量分, 若无则默认为 0 (兜底)
    quality_score = state.get("quality_score", 0) or 0

    # 从状态字典中读取质量门通过标志, 若无则默认为 True (假设通过, 保守策略)
    quality_gate_passed = state.get("quality_gate_passed", True)

    # 从状态字典中读取已重试次数, 若无则默认为 0
    retry_count = state.get("quality_retry_count", 0) or 0

    # 策略调整产物: 扩展后的关键词 (仅"未通过质量门且扩展有效"时非空)。
    # 【为什么用局部变量而不是 state】
    #   本变量只在**本函数内**产生、也只在本函数末尾被消费, 属于纯函数内数据流。
    #   【历史 bug】原实现写成 state["_temp_expanded_keywords"] = expanded, 再在
    #   函数末尾 state.get("_temp_expanded_keywords") 读回 —— 绕了一圈, 且该键
    #   未在 AgentState 声明 → 被 LangGraph 静默丢弃, 跨节点传递本就不可能生效。
    #   函数内传递数据用局部变量即可, 不要污染 state。
    expanded_kw = None

    # ========== 质量门判定逻辑 ==========
    # 判定规则: 优先读取上游的 gate_passed 标志, 兜底用分数阈值判断
    if quality_gate_passed and quality_score >= QUALITY_GATE_THRESHOLD:
        # 条件1: 上游判定通过 且 质量分 >= 阈值 → 正式通过
        passed = True
        print(f"  ✅ 质量门通过: 质量分 {quality_score} >= 阈值 {QUALITY_GATE_THRESHOLD}")
        fabao_eligible = False
    elif retry_count >= MAX_QUALITY_RETRIES:
        # 条件2: 重试次数已达上限 → 强制放行 (防死循环, 即便质量不达标也继续流程)
        passed = True
        # 只有这里才能触发后续的北大法宝付费询问：
        #   免费的「三通道召回 + 融合/skip_fusion + 关键词扩展 + fallback」
        #   连续 MAX_QUALITY_RETRIES 次仍低于阈值时，才把唯一的付费机会交给 beida_fabao_gate_node。
        #   beida_fabao_gate_node 仅对 fabao_retry_eligible=True 生效，其余任何场景都不问用户。
        fabao_eligible = True
        print(
            f"  ⚠️ 重试次数已达上限 ({MAX_QUALITY_RETRIES}), 强制放行 "
            f"(质量分 {quality_score}) → 置 fabao_retry_eligible=True, 进入北大法宝付费询问"
        )
    else:
        # 条件3: 未通过且重试次数未达上限 → 执行策略调整（仍在免费重试链路上）
        passed = False
        fabao_eligible = False
        # 重试次数 +1 (防死循环计数器)
        retry_count += 1
        print(f"  ⚠️ 质量门未通过: 质量分 {quality_score} < 阈值 {QUALITY_GATE_THRESHOLD}, "
              f"执行策略调整 (重试 {retry_count}/{MAX_QUALITY_RETRIES})")

        # ========== 策略调整模块 ==========
        # 从状态字典读取策略调整所需的上下文信息
        task_type = state.get("task_type", "")                 # 任务类型 (决定扩展词表)
        current_keywords = state.get("retrieval_keywords", []) or []  # 当前关键词列表

        # 策略调整步骤1: 关键词扩展
        # 若当前有关键词, 则尝试扩展 (添加同义词/相关词)
        if current_keywords:
            # 调用关键词扩展函数, 根据任务类型扩展同义词
            expanded = _expand_keywords(current_keywords, task_type)
            # 仅当扩展后关键词数量增加时才生效 (避免无效扩展)
            if len(expanded) > len(current_keywords):
                print(f"    · 关键词扩展: {len(current_keywords)} → {len(expanded)} 个")
                # 打印前5个新增关键词 (日志截断, 避免过长)
                print(f"      新增: {[k for k in expanded if k not in current_keywords][:5]}")
                # 暂存扩展结果, 供下方构建返回字段时使用 (函数内局部变量, 不写 state)
                expanded_kw = expanded

    # ========== 构建返回字段 ==========
    # 基础字段: 无论通过与否都返回
    result = {
        "quality_retry_count": retry_count,          # 更新后的重试计数
        "quality_gate_passed": passed,                # 质量门通过标志
        "quality_max_retries": MAX_QUALITY_RETRIES,   # 最大重试次数 (供下游参考)
        # fabao_retry_eligible: **唯一**允许 beida_fabao_gate_node 中断问用户的触发标记；
        # 其余任何场景（分数低 / 关键词命中 / 手动指定 api_sources）都不应触发付费询问。
        "fabao_retry_eligible": fabao_eligible,
    }

    # 若未通过质量门, 把扩展后的关键词写回 state,
    # 供回边后的 retrieval_intent_decompose / entity_recall 重新检索时使用
    if not passed and expanded_kw:
        result["retrieval_keywords"] = expanded_kw  # 用扩展后的关键词覆盖原有关键词
        # 注: 原实现还会写 retrieval_strategy_adjusted=True, 该标记全项目零消费方,
        #     且"是否调整过策略"已可由 quality_retry_count > 0 推出, 故删除。
        print("    · 策略调整完成 ✓")

    # 返回需要更新到 state 的字段字典
    return result


# 模块自测入口: 直接运行本文件时执行, 验证质量门判定与策略调整逻辑
if __name__ == "__main__":
    # 测试1: 质量达标场景 —— 质量分 85 (>= 60), 应判定通过
    s1 = AgentState(quality_score=85, quality_retry_count=0)
    r1 = quality_gate_retry_node(s1)
    print(f"测试1 (达标): passed={r1.get('quality_gate_passed')}")

    # 测试2: 质量不达标, 可重试 —— 质量分 45 (< 60), 触发策略调整 (关键词扩展)
    s2 = AgentState(
        quality_score=45,                                      # 质量分: 不及格
        quality_retry_count=0,                                 # 重试次数: 0 (可重试)
        retrieval_keywords=["违约金", "解除"],                  # 当前关键词 (合同审查场景)
        domain_sources=["laws"],                                # 当前激活源: 仅法律
        mounted_sources=["laws", "regulations", "interpretations"],  # 挂载源: 3种可用
        task_type="contract_review",                           # 任务类型: 合同审查
    )
    r2 = quality_gate_retry_node(s2)
    print(f"测试2 (不达标): passed={r2.get('quality_gate_passed')}, "
          f"retry={r2.get('quality_retry_count')}, "
          f"keywords={r2.get('retrieval_keywords')}")

    # 测试3: 重试达上限场景 —— 重试次数 3 (>= MAX_QUALITY_RETRIES), 强制放行
    s3 = AgentState(quality_score=45, quality_retry_count=3)
    r3 = quality_gate_retry_node(s3)
    print(f"测试3 (达上限): passed={r3.get('quality_gate_passed')}")