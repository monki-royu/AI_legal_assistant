"""从用户输入抽取法律实体(仿中医extract_entity_from_user_input_node)"""

# 📜 代码文字逻辑解析
# 本文件是法智引擎(AI法律助理)Text-to-Cypher多智能体流程的第一站,负责从用户自然语言问题中抽取法律实体。
# 在整个LangGraph编排链路中,该节点位于"知识图谱问答(legal_qa)"分支的入口位置,其上游是意图路由节点
# (intent_router_node,将task_type分流到legal_qa),其下游是Neo4j实体匹配节点(match_entity_from_neo4j_node)。
# 核心思路:利用大语言模型(LLM)的语义理解能力,从用户的口语化问题中识别出结构化的法律实体,包括四类:
#   1. concepts(法律概念):如"违约金"、"定金"、"合同效力"等抽象法律术语
#   2. statutes(法律法规名):如"民法典"、"合同法"等法规全称或简称
#   3. articles(条文编号):如"第585条"、"第六百条"等具体条款标识
#   4. case_types(案件类型):如"买卖合同纠纷"、"租赁合同纠纷"等案件分类
# 抽取结果以JSON格式返回,然后解析为Python列表,统一写入AgentState的对应字段(user_input_concepts、
# user_input_statutes、user_input_entities)供后续节点使用。该节点采用"宽松失败"策略:一旦LLM调用或
# JSON解析失败,会降级为空列表而非抛出异常,保证整个图流程不会因单个节点失败而中断。这种设计借鉴了
# 中医问答系统的同名节点,通过Prompt工程约束LLM只输出严格JSON,并兼容模型可能返回的```json```代码块标记。
# 整个节点是"非侵入式"的——它不依赖Neo4j连接,纯靠LLM完成,因此可独立测试与迁移到其他领域。

# 导入json模块:用于将LLM返回的JSON字符串解析为Python字典,是数据格式转换的基础工具
import json
# 从langchain_core.messages导入HumanMessage:LangChain消息体系中代表"用户消息"的类
# 在调用LLM时,需将提示词包装为HumanMessage对象,以符合ChatModel的输入规范(角色:human)
from langchain_core.messages import HumanMessage
# 从common.llm导入my_llm:项目共享的LLM客户端实例(封装了具体的模型配置,如GLM/GPT等)
# 使用全局单例避免每个节点重复初始化,统一管理模型调用
from common.llm import my_llm
# 从同包的agent_state模块导入AgentState:LangGraph中各节点共享的状态字典类型定义
# 该TypedDict声明了所有可能流转的字段,本节点会写入user_input_concepts/user_input_statutes/user_input_entities
from __004__langgraph_more_nodes.agent_state import AgentState


def extract_entity_from_user_input_node(state: AgentState):
    """
    从用户输入的自然语言问题中抽取法律相关实体(Text-to-Cypher流程的第1步)。

    【作用】
        本节点是知识图谱问答(legal_qa)链路的起点。它接收用户的原始问题(如"民法典第585条规定的违约金
        和定金有什么区别？"),通过LLM语义理解,识别出四类法律实体:
          - concepts: 法律概念(违约金、定金)
          - statutes: 法规名称(民法典)
          - articles: 条文编号(第585条)
          - case_types: 案件类型(可选,本例无)
        抽取结果写入AgentState,供下游match_entity_from_neo4j_node在Neo4j知识图谱中进行实体匹配与消歧。

    【参数】
        state (AgentState): LangGraph共享状态字典,本节点读取其中的"input"(用户原始问题)字段。

    【返回值】
        AgentState: 更新后的状态字典,新增/写入以下字段:
            - user_input_concepts (List[str]): 抽取出的法律概念列表
            - user_input_statutes (List[str]): 抽取出的法规名称列表
            - user_input_entities (List[str]): 上述四类实体的合并总列表(供下游匹配使用)
        若发生异常,上述字段均降级为空列表,保证流程不中断。

    【可迁移性说明】
        本节点逻辑与领域无关,核心是"LLM + JSON解析 + 容错"。迁移到其他领域(如医疗、电商、金融)时,
        只需修改Prompt中的实体类别定义(如将"法律概念/法规/条文/案件类型"改为"症状/药品/科室/疾病"),
        以及对应的state字段名,即可复用。容错降级机制(try/except回退空列表)是通用的健壮性设计模式,
        可直接保留。该节点不依赖任何外部数据库连接,因此对运行环境要求极低,迁移成本主要在Prompt工程上。
    """
    # 打印流程开始日志:用于调试与监控,标识当前进入"实体抽取"阶段
    print("开始抽取用户输入的法律实体")
    # 从state中读取用户输入文本,若"input"键不存在则返回空字符串作为兜底
    # 这是整个抽取流程的数据源,LLM将基于该文本识别实体
    user_input = state.get("input", "")

    # 构造抽取提示词(Prompt):使用f-string将用户输入动态嵌入
    # Prompt设计要点:
    #   1. 明确指定输出为JSON格式,并给出schema示例(四类实体字段)
    #   2. 强调"只抽取明确出现的实体,不要臆造",防止LLM幻觉导致下游匹配失败
    #   3. 要求"如果没有某类实体,返回空数组",保证JSON结构完整便于解析
    #   4. 最后一句"只输出JSON"进一步约束LLM不要输出解释性文字
    # 注意:JSON示例中的花括号需要用{{ }}转义,因为外层是f-string
    prompt = f"""请从以下用户输入中抽取法律相关实体, 返回JSON格式:
{{
  "concepts": ["法律概念1", "概念2"],
  "statutes": ["法律名称1", "法律名称2"],
  "articles": ["条文编号1", "条文编号2"],
  "case_types": ["案件类型1"]
}}

只抽取明确出现的实体, 不要臆造。如果没有某类实体, 返回空数组。

用户输入: {user_input}

只输出JSON。"""

    # 使用try-except包裹整个LLM调用与解析过程,实现容错降级
    try:
        # 调用LLM:将prompt包装为HumanMessage(用户角色消息)传入invoke方法
        # my_llm.invoke接受消息列表,返回包含content属性的响应对象
        resp = my_llm.invoke([HumanMessage(content=prompt)])
        # 提取响应内容并去除首尾空白:resp.content是LLM生成的文本(应为JSON字符串)
        content = resp.content.strip()
        # 兼容性处理:某些LLM(如GPT-4)会在JSON外包裹```json ... ```代码块标记
        # 若检测到```存在,则截取第一个{到最后一个}之间的内容,剥离markdown标记
        if "```" in content:
            # 找到第一个左花括号的位置(JSON起始)
            start = content.find("{")
            # 找到最后一个右花括号的位置并+1(因为切片是左闭右开,需包含该位置)
            end = content.rfind("}") + 1
            # 截取纯JSON部分,去除代码块标记和多余说明文字
            content = content[start:end]
        # 将清理后的JSON字符串解析为Python字典
        # 若LLM返回的JSON格式不合法,此处会抛出json.JSONDecodeError,被外层except捕获
        data = json.loads(content)

        # 从解析结果中分别提取四类实体,使用dict.get(key, [])确保键缺失时返回空列表而非None
        # concepts: 法律概念列表,如["违约金", "定金"]
        concepts = data.get("concepts", [])
        # statutes: 法规名称列表,如["民法典"]
        statutes = data.get("statutes", [])
        # articles: 条文编号列表,如["第585条"]
        articles = data.get("articles", [])
        # case_types: 案件类型列表,如["买卖合同纠纷"](本例可能为空)
        case_types = data.get("case_types", [])

        # 将四类实体合并为一个总列表:使用+运算符拼接列表
        # 该总列表作为user_input_entities写入state,是下游match_entity_from_neo4j_node的输入
        # 注意:此处未做去重,若同一实体在多类中出现会重复,但下游匹配节点会处理去重
        all_entities = concepts + statutes + articles + case_types

        # 将抽取结果写入AgentState的对应字段,供后续节点读取
        # user_input_concepts: 法律概念,后续用于扩展Cypher检索范围
        state["user_input_concepts"] = concepts
        # user_input_statutes: 法规名称,后续用于精确检索特定法规
        state["user_input_statutes"] = statutes
        # user_input_entities: 合并后的所有实体,作为Neo4j实体匹配的输入
        state["user_input_entities"] = all_entities
    # 捕获所有异常:包括LLM调用失败(网络/限流)、JSON解析失败(格式错误)、字段缺失等
    except Exception as e:
        # 打印警告日志,标识抽取失败及具体异常信息,便于排查问题
        print(f"⚠️ 实体抽取失败: {e}")
        # 降级策略:将三个字段全部置为空列表,使下游节点能感知"无实体"状态
        # match_entity_from_neo4j_node会检测空列表并跳过匹配,保证流程继续
        state["user_input_concepts"] = []
        state["user_input_statutes"] = []
        state["user_input_entities"] = []

    # 打印抽取完成日志:输出最终抽取到的实体列表,便于调试与监控
    print(f"完成实体抽取: {state.get('user_input_entities')}")
    # 返回更新后的state:LangGraph框架会将其合并到全局状态,供下一个节点使用
    return state


# 模块自测入口:当直接运行本文件(python extract_entity_from_user_input_node.py)时执行
# 用于独立验证节点功能,不依赖整个LangGraph图流程
if __name__ == "__main__":
    # 构造测试用AgentState实例,模拟用户输入"民法典第585条规定的违约金和定金有什么区别？"
    # 预期抽取结果应包含: statutes=["民法典"], articles=["第585条"], concepts=["违约金", "定金"]
    s = AgentState(input="民法典第585条规定的违约金和定金有什么区别？")
    # 调用节点函数并打印抽取到的实体列表,验证抽取效果
    print(extract_entity_from_user_input_node(s).get("user_input_entities"))
