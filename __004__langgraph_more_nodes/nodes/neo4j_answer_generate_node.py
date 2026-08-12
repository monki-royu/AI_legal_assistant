"""基于知识图谱检索结果生成答案(仿中医neo4j_answer_generate_node)"""

# 📜 代码文字逻辑解析
# 本文件是法智引擎(AI法律助理)Text-to-Cypher多智能体流程的第六站(终点站),负责基于知识图谱检索结果
# 生成自然语言答案。在LangGraph编排链路中,该节点的上游是run_cypher_node(已执行Cypher查询并获得结果),
# 下游无后续节点(本节点是legal_qa链路的终点,生成的答案会写入state["output"]返回给前端展示)。
# 核心职责:作为知识图谱问答的"答案合成"环节,本节点根据检索结果的有无采取双路策略:
#   路径A(有结果):当cypher_results非空时,将检索结果(JSON序列化,截断至3000字符防止Prompt过长)、
#     匹配实体、用户问题组织成结构化Prompt,让LLM基于检索结果" grounded"地生成答案,要求引用法律条文、
#     不编造、专业但易懂。这种"检索增强生成"(RAG)模式能保证答案有据可查,提高可信度。
#   路径B(无结果降级):当cypher_results为空时(可能是Cypher执行失败或图谱中无相关数据),降级为
#     LLM直接回答用户问题,不依赖知识图谱。此时LLM基于自身参数化知识生成答案,虽无图谱背书,但能保证
#     用户始终获得响应,避免"无法回答"的尴尬体验。
# 两种路径均写入state["neo4j_answer"]和state["output"]两个字段:neo4j_answer是知识图谱链路的专属答案字段,
# output是面向前端展示的通用输出字段(legal_response系列函数读取该字段返回给调用方)。本节点具备容错降级:
# 若LLM调用失败,写入错误提示信息而非抛出异常,保证流程不中断。该节点借鉴中医问答系统的同名节点,
# 将医疗问答的RAG逻辑迁移为法律问答,核心是Prompt工程的领域适配。

# 导入json模块:用于将cypher_results(字典列表)序列化为JSON字符串,嵌入Prompt供LLM参考
# ensure_ascii=False保留中文可读性,indent=2格式化便于LLM理解结构
import json
# 从langchain_core.messages导入SystemMessage和HumanMessage:
# - SystemMessage:系统角色消息,设定LLM的角色身份(如"你是法智引擎AI法律助理")
# - HumanMessage:用户角色消息,承载具体的问答任务与上下文(检索结果、用户问题等)
from langchain_core.messages import SystemMessage, HumanMessage
# 从common.llm导入my_llm:项目共享的LLM客户端实例(全局单例)
from common.llm import my_llm
# 从同包的agent_state模块导入AgentState:LangGraph中各节点共享的状态字典类型定义
# 本节点读取input/cypher_results/matched_entities字段,写入neo4j_answer/output字段
from __004__langgraph_more_nodes.agent_state import AgentState


def neo4j_answer_generate_node(state: AgentState):
    """
    基于知识图谱检索结果生成自然语言答案,具备降级兜底能力(Text-to-Cypher流程的第6步/终点)。

    【作用】
        本节点是知识图谱问答(legal_qa)链路的终点。它根据run_cypher_node返回的检索结果采取双路策略:
          - 路径A(有结果,RAG模式):将检索结果、匹配实体、用户问题组织成Prompt,让LLM基于检索结果
            生成有据可查的答案,要求引用法律条文、不编造、专业易懂。
          - 路径B(无结果,降级模式):当cypher_results为空时,降级为LLM直接回答,基于自身参数化知识生成答案,
            保证用户始终获得响应。
        生成的答案写入state["neo4j_answer"](知识图谱链路专属)和state["output"](面向前端的通用输出)。

    【参数】
        state (AgentState): LangGraph共享状态字典,本节点读取以下字段:
            - input (str): 用户原始问题
            - cypher_results (List[dict]): Neo4j查询返回的记录列表(可能为空)
            - matched_entities (List[str]): Neo4j匹配到的实体列表(用于补充上下文)

    【返回值】
        AgentState: 更新后的状态字典,新增/写入以下字段:
            - neo4j_answer (str): 知识图谱链路生成的自然语言答案
            - output (str): 面向前端展示的最终输出(与neo4j_answer内容相同,legal_response读取此字段)
        若LLM调用失败,上述字段写入错误提示信息。

    【可迁移性说明】
        本节点的双路策略(有结果RAG + 无结果降级)是通用的问答兜底设计,可迁移到任何RAG场景。迁移时需修改
        Prompt中的角色身份(如将"法智引擎AI法律助理"改为目标领域助手)和领域约束(如将"引用法律条文"改为
        "引用相关规范/标准")。检索结果序列化(json.dumps + 截断)和容错降级机制是通用的,建议保留。
        注意:截断长度3000字符需根据LLM的上下文窗口调整,过短会丢失信息,过长会增加token成本。
        SystemMessage与HumanMessage的分工模式(系统设定角色 + 用户承载任务)是LangChain的最佳实践,
        迁移时建议保持。本节点是legal_qa链路终点,生成的output字段会被legal_response系列函数返回给前端。
    """
    # 打印流程开始日志:标识当前进入"答案生成"阶段
    print("开始基于知识图谱生成答案")
    # 从state中读取用户原始问题,作为答案生成的核心语义输入
    user_input = state.get("input", "")
    # 从state中读取Neo4j查询返回的记录列表,这是RAG模式的核心上下文
    # 若为空列表,则触发降级模式(LLM直接回答)
    cypher_results = state.get("cypher_results", [])
    # 从state中读取Neo4j匹配到的实体列表,作为答案生成的辅助上下文
    # 帮助LLM理解问题涉及的核心实体
    matched_entities = state.get("matched_entities", [])

    # 判断是否需要降级:若cypher_results为空(查询无结果或执行失败),走降级路径B
    # 如果Neo4j无结果, 降级用LLM直接回答
    if not cypher_results:
        # 打印降级日志,标识当前走降级路径
        print("  Neo4j无结果, 降级LLM回答")
        # 构造降级Prompt:不依赖检索结果,直接让LLM基于自身知识回答用户问题
        # Prompt设计要点:
        #   1. 角色设定:你是法智引擎AI法律助理(明确身份)
        #   2. 兜底声明:如果无法确定,请说明并建议咨询专业律师(避免误导,符合法律咨询伦理)
        #   3. 注入用户问题
        #   4. 要求专业、准确、引用法律条文(即使在降级模式下也保持专业标准)
        prompt = f"""你是法智引擎AI法律助理。请回答以下法律问题。
如果无法确定, 请说明并建议咨询专业律师。

用户问题: {user_input}

请给出专业、准确的法律解答, 并引用相关法律条文。"""

        # 使用try-except包裹LLM调用,实现容错
        try:
            # 调用LLM:传入HumanMessage(降级模式无需SystemMessage,角色已在Prompt中设定)
            # 返回resp对象,content属性即LLM生成的答案
            resp = my_llm.invoke([HumanMessage(content=prompt)])
            # 将LLM答案去除首尾空白后写入state的neo4j_answer字段
            state["neo4j_answer"] = resp.content.strip()
        # 捕获LLM调用异常:如网络错误、限流、API密钥失效等
        except Exception as e:
            # 降级策略:写入错误提示信息,保证用户获得响应而非报错
            state["neo4j_answer"] = f"抱歉, 处理您的问题时出现错误: {e}"
        # 将neo4j_answer同步到output字段:output是面向前端展示的通用输出字段
        # legal_response系列函数读取output字段返回给调用方
        state["output"] = state["neo4j_answer"]
        # 打印完成日志,标识降级路径执行完毕
        print("完成答案生成(降级)")
        # 返回state,本节点是legal_qa链路终点,流程结束
        return state

    # 路径A(有结果,RAG模式):将检索结果序列化为JSON字符串,供LLM参考
    # json.dumps参数说明:
    #   - cypher_results: 待序列化的字典列表(如[{"name": "违约金", "content": "..."}])
    #   - ensure_ascii=False:保留中文字符,避免\uXXXX转义,提高可读性
    #   - indent=2:缩进2空格格式化,便于LLM理解JSON结构
    # [:3000]截断:限制字符串长度为3000字符,防止Prompt过长导致token超限或增加成本
    # 有知识图谱结果, 让LLM基于检索结果回答
    results_str = json.dumps(cypher_results, ensure_ascii=False, indent=2)[:3000]

    # 构造RAG模式Prompt:将检索结果、匹配实体、用户问题组织成结构化Prompt
    # Prompt设计要点:
    #   1. 角色设定:你是法智引擎AI法律助理(明确身份)
    #   2. 注入检索结果:作为答案的事实依据(grounded)
    #   3. 注入匹配实体:帮助LLM理解问题核心
    #   4. 注入用户问题:明确回答目标
    #   5. 生成要求:
    #      - 基于检索结果回答,不要编造(防止幻觉,保证有据可查)
    #      - 引用相关法律条文(提高可信度,符合法律咨询规范)
    #      - 检索结果不足时说明并补充通用法律知识(兜底策略,保证回答完整性)
    #      - 语气专业但易懂(平衡专业性与可读性)
    prompt = f"""你是法智引擎AI法律助理。请基于以下知识图谱检索结果回答用户问题。

知识图谱检索结果:
{results_str}

匹配到的实体: {matched_entities}

用户问题: {user_input}

要求:
1. 基于检索结果回答, 不要编造
2. 引用相关法律条文
3. 如果检索结果不足以回答, 请说明并补充通用法律知识
4. 语气专业但易懂"""

    # 构造消息列表:采用[SystemMessage, HumanMessage]的标准格式
    # SystemMessage:设定LLM的角色身份"法智引擎AI法律助理, 提供专业法律解答"
    #   这是全局角色设定,与HumanMessage中的任务指令配合,使LLM始终以法律助理身份回答
    # HumanMessage:承载具体的RAG任务(检索结果 + 用户问题 + 生成要求)
    messages = [
        SystemMessage(content="你是法智引擎AI法律助理, 提供专业法律解答。"),
        HumanMessage(content=prompt),
    ]

    # 使用try-except包裹LLM调用,实现容错
    try:
        # 调用LLM:传入消息列表[SystemMessage, HumanMessage]
        # LLM会基于检索结果生成有据可查的答案
        resp = my_llm.invoke(messages)
        # 提取LLM答案并去除首尾空白
        answer = resp.content.strip()
        # 将答案写入state的neo4j_answer字段(知识图谱链路专属答案)
        state["neo4j_answer"] = answer
        # 将答案同步到output字段(面向前端展示的通用输出)
        # legal_response系列函数读取output字段返回给调用方
        state["output"] = answer
    # 捕获LLM调用异常:如网络错误、限流、API密钥失效等
    except Exception as e:
        # 降级策略:写入错误提示信息到neo4j_answer
        state["neo4j_answer"] = f"抱歉, 处理您的问题时出现错误: {e}"
        # 同步错误信息到output字段,保证前端能展示响应
        state["output"] = state["neo4j_answer"]

    # 打印完成日志:标识RAG模式答案生成完毕
    print("完成答案生成")
    # 返回更新后的state:本节点是legal_qa链路终点,生成的output会被legal_response返回给前端
    return state


# 模块自测入口:当直接运行本文件时执行,用于独立验证节点功能
if __name__ == "__main__":
    # 构造测试用AgentState实例,模拟用户问题"违约金怎么规定？"及Cypher查询结果
    # cypher_results非空,预期走RAG模式(路径A),基于检索结果生成答案
    # 检索结果包含name="违约金"和content="当事人可以约定违约金..."的记录
    s = AgentState(input="违约金怎么规定？",
                   cypher_results=[{"name": "违约金", "content": "当事人可以约定违约金..."}])
    # 调用节点函数并打印生成的答案前200字符,验证答案生成效果
    # [:200]截断避免日志过长
    print(neo4j_answer_generate_node(s).get("output", "")[:200])
