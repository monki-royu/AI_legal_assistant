"""生成Neo4j Cypher查询语句(仿中医generate_neo4j_cypher_node)"""

# 📜 代码文字逻辑解析
# 本文件是法智引擎(AI法律助理)Text-to-Cypher多智能体流程的第三站,负责基于上下文生成Neo4j Cypher查询语句。
# 在LangGraph编排链路中,该节点的上游是match_entity_from_neo4j_node(已在图谱中匹配到规范化实体),
# 下游是check_cypher_node(对生成的Cypher进行语法与安全性校验)。
# 核心思路:这是Text-to-Cypher(自然语言转图查询语言)的关键环节。将用户问题、匹配到的实体、抽取的概念
# 与法规名等信息组织成结构化Prompt,交给LLM生成对应的Cypher查询语句。Prompt中明确告知LLM知识图谱的
# schema(节点标签和关系类型),使生成的Cypher能正确引用图谱结构。节点标签包括:Statute(法律法规)、
# Article(法条)、Concept(法律概念)、CaseType(案件类型)、Penalty(处罚)、Right(权利)、Obligation(义务)等;
# 关系类型包括:CONTAINS_ARTICLE、REFERS_TO_STATUTE、DEFINED_AS等。
# 为约束LLM输出,本节点使用SystemMessage(系统角色消息)强制定义"只输出Cypher语句,不加markdown标记",
# 并在HumanMessage(用户角色消息)中提供详细的生成要求(只输出一条、用MATCH和RETURN、LIMIT 10、返回name和关系)。
# 生成后还会做markdown代码块标记的清理(去除```包裹),保证写入state的cypher_query是纯Cypher文本。
# 该节点具备容错降级:若LLM调用失败,写入空字符串,下游check_cypher_node会检测到空Cypher并标记校验失败,
# 触发重试或降级到LLM直接回答。该节点借鉴中医问答系统的同名节点,将医疗图谱schema替换为法律图谱schema。

# 从langchain_core.messages导入HumanMessage和SystemMessage:
# - SystemMessage:系统角色消息,用于设定LLM的全局行为准则(如"你只输出Cypher语句")
# - HumanMessage:用户角色消息,用于承载具体的任务指令与上下文信息
# 在LangChain的ChatModel中,消息顺序通常为[SystemMessage, HumanMessage],系统消息优先级最高
from langchain_core.messages import HumanMessage, SystemMessage
# 从common.llm导入my_llm:项目共享的LLM客户端实例(全局单例)
from common.llm import my_llm
# 从同包的agent_state模块导入AgentState:LangGraph中各节点共享的状态字典类型定义
# 本节点读取input/matched_entities/user_input_concepts/user_input_statutes字段,写入cypher_query字段
from __004__langgraph_more_nodes.agent_state import AgentState


def generate_neo4j_cypher_node(state: AgentState):
    """
    基于用户问题与匹配实体,通过LLM生成Neo4j Cypher查询语句(Text-to-Cypher流程的第3步)。

    【作用】
        本节点是Text-to-Cypher的核心环节。它将以下上下文信息组织成结构化Prompt,调用LLM生成Cypher查询:
          - 用户原始问题(input):如"违约金是怎么规定的？"
          - Neo4j匹配到的实体(matched_entities):如["违约金", "民法典"]
          - 用户提到的概念(user_input_concepts):如["违约金"]
          - 用户提到的法规(user_input_statutes):如["民法典"]
        Prompt中明确告知LLM知识图谱的schema(7种节点标签 + 8种关系类型),使生成的Cypher能正确引用图谱结构。
        生成的Cypher语句写入state["cypher_query"],供下游check_cypher_node校验合法性。

    【参数】
        state (AgentState): LangGraph共享状态字典,本节点读取以下字段:
            - input (str): 用户原始问题
            - matched_entities (List[str]): Neo4j匹配到的规范化实体列表
            - user_input_concepts (List[str]): 抽取的法律概念列表
            - user_input_statutes (List[str]): 抽取的法规名称列表

    【返回值】
        AgentState: 更新后的状态字典,新增/写入以下字段:
            - cypher_query (str): 生成的Cypher查询语句(纯文本,无markdown标记)
        若LLM调用失败,cypher_query为空字符串,下游check_cypher_node会处理此情况。

    【可迁移性说明】
        本节点的Text-to-Cypher逻辑可迁移到任何基于Neo4j的知识图谱问答场景。迁移时需修改Prompt中的
        schema描述(节点标签和关系类型),以匹配目标图谱的实际结构。SystemMessage的"只输出Cypher"约束
        和markdown清理逻辑是通用的,建议保留。若目标图谱使用GQL或其他查询语言,需同步调整Prompt要求。
        注意:LLM生成的Cypher可能存在语法错误或安全风险(如包含写操作),因此下游必须有check_cypher_node
        进行校验,不可直接执行。这种"生成-校验-执行"的三段式设计是Text-to-Cypher的最佳实践。
    """
    # 打印流程开始日志:标识当前进入"Cypher生成"阶段
    print("开始生成Cypher语句")
    # 从state中读取用户原始问题,作为Cypher生成的核心语义输入
    user_input = state.get("input", "")
    # 从state中读取Neo4j匹配到的规范化实体列表,这些实体将作为Cypher查询的锚点
    matched_entities = state.get("matched_entities", [])
    # 从state中读取用户抽取的法律概念列表,用于辅助LLM理解查询意图
    user_concepts = state.get("user_input_concepts", [])
    # 从state中读取用户抽取的法规名称列表,用于辅助LLM定位查询范围
    user_statutes = state.get("user_input_statutes", [])

    # 构造Cypher生成提示词(Prompt):使用f-string动态嵌入上下文信息
    # Prompt结构设计:
    #   1. 角色设定:你是Neo4j Cypher查询语句生成专家(激发LLM的专业能力)
    #   2. Schema告知:明确列出节点标签和关系类型,使LLM生成的Cypher符合图谱结构
    #   3. 上下文注入:用户问题、匹配实体、概念、法规(提供生成依据)
    #   4. 生成要求:只输出一条、用MATCH和RETURN、LIMIT 10、返回name和关系(约束输出格式)
    # Schema说明:
    #   节点标签:Statute(法律法规), Article(法条), Concept(法律概念), CaseType(案件类型),
    #           Penalty(处罚), Right(权利), Obligation(义务)
    #   关系类型:CONTAINS_ARTICLE(包含法条), REFERS_TO_STATUTE(引用法规), DEFINED_AS(定义为),
    #           APPLIES_TO_CASE(适用于案件), IMPOSES_PENALTY(施加处罚), GRANTS_RIGHT(授予权利),
    #           IMPOSES_OBLIGATION(施加义务), RELATES_TO_CONCEPT(关联概念)
    prompt = f"""你是一个Neo4j Cypher查询语句生成专家。

法律知识图谱的节点标签和关系类型:
节点标签: Statute(法律法规), Article(法条), Concept(法律概念), CaseType(案件类型), Penalty(处罚), Right(权利), Obligation(义务)
关系类型: CONTAINS_ARTICLE, REFERS_TO_STATUTE, DEFINED_AS, APPLIES_TO_CASE, IMPOSES_PENALTY, GRANTS_RIGHT, IMPOSES_OBLIGATION, RELATES_TO_CONCEPT

用户问题: {user_input}
匹配到的实体: {matched_entities}
用户提到的概念: {user_concepts}
用户提到的法规: {user_statutes}

请生成一条Cypher查询语句, 从知识图谱中检索与用户问题相关的信息。
要求:
1. 只输出一条Cypher语句, 不要解释
2. 使用MATCH和RETURN
3. 限制返回数量LIMIT 10
4. 返回节点的name和关系信息

Cypher语句:"""

    # 使用try-except包裹LLM调用过程,实现容错降级
    try:
        # 调用LLM:传入消息列表[SystemMessage, HumanMessage]
        # SystemMessage:设定LLM全局行为"你只输出Cypher语句, 不加任何markdown标记或解释"
        #   这是一条强约束,防止LLM输出```cypher```代码块或解释性文字,简化后续清理逻辑
        # HumanMessage:承载具体的Cypher生成任务(包含schema和上下文)
        # LLM会返回包含content属性的响应对象,content即生成的Cypher语句
        resp = my_llm.invoke([SystemMessage(content="你只输出Cypher语句, 不加任何markdown标记或解释。"),
                              HumanMessage(content=prompt)])
        # 提取响应内容并去除首尾空白:resp.content应为纯Cypher语句
        cypher = resp.content.strip()
        # 清理markdown标记:尽管SystemMessage已约束,但部分LLM仍可能返回```cypher ... ```格式
        # 若检测到以```开头,则逐行过滤掉所有以```开头的行,保留中间的Cypher代码
        # 清理markdown标记
        if cypher.startswith("```"):
            # 按换行符分割为行列表
            lines = cypher.split("\n")
            # 使用列表推导式过滤:保留不以```开头的行,然后用\n重新拼接,最后去除首尾空白
            # 例如"```cypher\nMATCH (n) RETURN n\n```" → "MATCH (n) RETURN n"
            cypher = "\n".join(l for l in lines if not l.startswith("```")).strip()
        # 将清理后的Cypher语句写入state的cypher_query字段
        # 该字段是下游check_cypher_node校验和run_cypher_node执行的输入
        state["cypher_query"] = cypher
    # 捕获LLM调用过程中的异常:如网络错误、限流、API密钥失效等
    except Exception as e:
        # 打印警告日志,标识Cypher生成失败及具体异常
        print(f"⚠️ Cypher生成失败: {e}")
        # 降级策略:写入空字符串,下游check_cypher_node会检测到空Cypher并标记校验失败
        # check_cypher_router会根据校验结果决定重试(回到本节点)或降级到LLM直接回答
        state["cypher_query"] = ""

    # 打印完成日志:输出Cypher语句的前80个字符(避免日志过长),加省略号标识截断
    # state.get('cypher_query', '')[:80]确保即使cypher_query字段不存在也不会报错
    print(f"完成Cypher生成: {state.get('cypher_query', '')[:80]}...")
    # 返回更新后的state:LangGraph框架会将其合并到全局状态
    return state


# 模块自测入口:当直接运行本文件时执行,用于独立验证节点功能
if __name__ == "__main__":
    # 构造测试用AgentState实例,模拟用户问题"违约金是怎么规定的？"及匹配实体["违约金", "民法典"]
    # 预期生成类似"MATCH (n:Concept)-[:DEFINED_AS]-(m) WHERE n.name CONTAINS '违约金' RETURN n, m LIMIT 10"的Cypher
    s = AgentState(input="违约金是怎么规定的？", matched_entities=["违约金", "民法典"])
    # 调用节点函数并打印生成的Cypher语句,验证生成效果
    print(generate_neo4j_cypher_node(s).get("cypher_query"))
