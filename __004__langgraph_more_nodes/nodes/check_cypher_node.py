"""检查Cypher语句(仿中医check_cypher_node)"""

# 📜 代码文字逻辑解析
# 本文件是法智引擎(AI法律助理)Text-to-Cypher多智能体流程的第四站,负责对LLM生成的Cypher语句进行
# 语法与安全性校验。在LangGraph编排链路中,该节点的上游是generate_neo4j_cypher_node(已生成Cypher语句),
# 下游由check_cypher_router条件路由根据校验结果分流:校验通过则进入run_cypher_node执行查询,
# 校验失败且重试次数未超阈值则回到generate_neo4j_cypher_node重新生成,重试次数超阈值则降级到答案生成节点。
# 核心问题:LLM生成的Cypher语句存在两类风险——一是安全性风险(可能包含DELETE/CREATE等写操作,会破坏图谱数据),
# 二是语法风险(可能缺少MATCH或RETURN关键字,导致查询无法执行)。本节点通过"关键词黑名单 + 必需关键词白名单"
# 的双重校验机制来拦截这两类风险。
# 安全性校验:维护一个危险关键词列表DANGEROUS_KEYWORDS(DELETE/REMOVE/DROP/CREATE/MERGE/SET /DETACH),
# 这些都是Cypher的写操作或破坏性操作,在只读查询场景下必须禁止。注意"SET "后带空格,是为了避免误伤
# 包含"SET"子串的合法字符串(如节点名"ASSET")。语法校验:要求Cypher必须包含MATCH(查询起点)和RETURN
# (返回结果)关键字,这是合法查询语句的基本构成。
# 校验结果写入state["is_all_validate_cypher"](布尔值),失败时递增cypher_retry_count计数器。
# 该计数器是check_cypher_router决定重试或降级的关键依据(通常重试>=3次则放弃)。本节点为纯静态校验,
# 不依赖Neo4j连接,执行速度快且无副作用,是"生成-校验-执行"三段式设计中保护图谱数据安全的关键防线。
# 该节点借鉴中医问答系统的同名节点,校验逻辑完全通用,无需修改即可复用。

# 从同包的agent_state模块导入AgentState:LangGraph中各节点共享的状态字典类型定义
# 本节点读取cypher_query/cypher_retry_count字段,写入is_all_validate_cypher/cypher_retry_count字段
from __004__langgraph_more_nodes.agent_state import AgentState


# 定义危险关键词列表(全局常量):这些Cypher关键字对应写操作或破坏性操作,在只读查询场景下必须禁止
# 危险关键词(禁止写操作)
# 列表内容说明:
#   - "DELETE":删除节点或关系(如MATCH (n) DELETE n会清空数据)
#   - "REMOVE":移除节点标签或属性(如REMOVE n.name会破坏数据完整性)
#   - "DROP":删除数据库、索引、约束等(如DROP DATABASE会销毁整个数据库)
#   - "CREATE":创建节点、关系、索引、约束等(虽然不是破坏性操作,但在只读场景下不应出现)
#   - "MERGE":类似于"查找或创建",可能新增数据,在只读场景下禁止
#   - "SET ":更新节点或关系属性(注意"SET "后带空格,避免误匹配"ASSET"等含SET子串的合法词)
#   - "DETACH":通常与DELETE连用(DETACH DELETE),删除节点及其所有关系,极具破坏性
# 使用大写形式,后续校验时会把Cypher语句转为大写进行匹配,保证大小写不敏感
DANGEROUS_KEYWORDS = ["DELETE", "REMOVE", "DROP", "CREATE", "MERGE", "SET ", "DETACH"]


def check_cypher_node(state: AgentState):
    """
    对LLM生成的Cypher语句进行语法与安全性校验(Text-to-Cypher流程的第4步)。

    【作用】
        本节点是Text-to-Cypher流程的安全防线。它对generate_neo4j_cypher_node生成的Cypher语句执行两项校验:
          1. 安全性校验:检查是否包含危险关键词(DELETE/REMOVE/DROP/CREATE/MERGE/SET /DETACH),
             这些写操作会破坏图谱数据,在只读查询场景下必须禁止。
          2. 语法校验:检查是否包含必需的MATCH(查询起点)和RETURN(返回结果)关键字。
        校验结果写入state["is_all_validate_cypher"](True/False),失败时递增cypher_retry_count。
        下游check_cypher_router根据这两个字段决定:通过→执行查询;失败且重试<3→重新生成;失败且重试>=3→降级。

    【参数】
        state (AgentState): LangGraph共享状态字典,本节点读取以下字段:
            - cypher_query (str): 待校验的Cypher语句
            - cypher_retry_count (int): 当前的重试次数(用于失败时递增)

    【返回值】
        AgentState: 更新后的状态字典,新增/写入以下字段:
            - is_all_validate_cypher (bool): 校验是否通过(True表示Cypher安全且语法基本合法)
            - cypher_retry_count (int): 校验失败时递增1,通过时保持不变

    【可迁移性说明】
        本节点的校验逻辑(关键词黑名单 + 必需关键词白名单)与领域完全无关,可无缝迁移到任何Text-to-Cypher场景。
        迁移时可根据实际需求调整DANGEROUS_KEYWORDS列表(如允许某些写操作)或增加必需关键词(如要求LIMIT)。
        注意:本节点是纯静态字符串校验,无法检测所有语法错误(如括号不匹配、属性名拼写错误等),这些错误会在
        run_cypher_node执行时由Neo4j引擎报错。若需更严格的校验,可考虑接入Neo4j的EXPLAIN指令做语法预检,
        但会增加数据库负担,需权衡。该节点不依赖任何外部连接,执行快速无副作用,是理想的"前置守门员"。
    """
    # 打印流程开始日志:标识当前进入"Cypher校验"阶段
    print("开始检查Cypher语句")
    # 从state中读取待校验的Cypher语句,若字段不存在则返回空字符串
    cypher = state.get("cypher_query", "")
    # 从state中读取当前的重试次数,若字段不存在则返回0(首次校验)
    # 该计数器用于在check_cypher_router中判断是否已达重试上限(通常为3次)
    retry_count = state.get("cypher_retry_count", 0)

    # 前置检查:若Cypher语句为空(去除首尾空白后),直接标记校验失败
    # 空Cypher可能源于generate_neo4j_cypher_node的LLM调用失败降级,无法执行查询
    if not cypher.strip():
        # 写入校验失败标志:is_all_validate_cypher = False
        state["is_all_validate_cypher"] = False
        # 递增重试计数器:cypher_retry_count + 1,供check_cypher_router判断是否继续重试
        state["cypher_retry_count"] = retry_count + 1
        # 打印失败日志,标识Cypher为空导致校验失败
        print("Cypher为空, 校验失败")
        # 提前返回state,跳过后续校验逻辑
        return state

    # 检查危险操作:将Cypher语句转为大写,实现大小写不敏感匹配
    # cypher_upper用于后续所有关键词匹配,避免遗漏大小写变体(如"delete"或"Delete")
    # 检查危险操作
    cypher_upper = cypher.upper()
    # 初始化校验结果标志为True(乐观策略:默认通过,发现问题时置为False)
    is_valid = True
    # 遍历危险关键词列表,逐个检查是否出现在Cypher语句中(大写形式)
    for kw in DANGEROUS_KEYWORDS:
        # 若Cypher大写形式中包含当前危险关键词,则判定为不合法
        if kw in cypher_upper:
            # 标记校验失败
            is_valid = False
            # 打印警告日志,标识检测到的具体危险操作,便于排查
            print(f"  ⚠️ 检测到危险操作: {kw}")
            # break跳出循环:一旦发现危险操作即可判定失败,无需继续检查其他关键词
            break

    # 检查必须包含MATCH:MATCH是Cypher查询语句的起点关键字,缺失则无法执行查询
    # 检查必须包含MATCH
    if "MATCH" not in cypher_upper:
        # 标记校验失败
        is_valid = False
        # 打印警告日志,标识缺少MATCH关键字
        print("  ⚠️ 缺少MATCH")

    # 检查必须包含RETURN:RETURN是Cypher查询语句的返回关键字,缺失则查询无结果输出
    # 检查必须包含RETURN
    if "RETURN" not in cypher_upper:
        # 标记校验失败
        is_valid = False
        # 打印警告日志,标识缺少RETURN关键字
        print("  ⚠️ 缺少RETURN")

    # 将最终校验结果写入state的is_all_validate_cypher字段
    # 该字段是check_cypher_router条件路由的判断依据:True→run_cypher_node, False→重试或降级
    state["is_all_validate_cypher"] = is_valid
    # 若校验失败,递增重试计数器,供check_cypher_router判断是否已达重试上限
    if not is_valid:
        state["cypher_retry_count"] = retry_count + 1

    # 打印完成日志:输出校验结果(通过/失败)及当前重试次数,便于监控流程状态
    # '通过' if is_valid else '失败' 是三元表达式,根据is_valid选择展示文本
    print(f"完成Cypher检查: {'通过' if is_valid else '失败'} (重试{state.get('cypher_retry_count', 0)})")
    # 返回更新后的state:LangGraph框架会将其合并到全局状态,供条件路由判断下一步走向
    return state


# 模块自测入口:当直接运行本文件时执行,用于独立验证节点功能
if __name__ == "__main__":
    # 构造测试用AgentState实例,传入一条合法的Cypher查询语句
    # 该Cypher包含MATCH和RETURN,且不含危险关键词,预期校验通过(is_all_validate_cypher=True)
    s = AgentState(cypher_query="MATCH (n:Concept) WHERE n.name CONTAINS '违约金' RETURN n LIMIT 5")
    # 调用节点函数并打印校验结果,验证校验逻辑
    print(check_cypher_node(s).get("is_all_validate_cypher"))
