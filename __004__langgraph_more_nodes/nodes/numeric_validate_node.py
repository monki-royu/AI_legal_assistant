"""N5c 数值校验节点: 基于YAML规则做确定性数值校验"""
# 📜 代码文字逻辑解析
# 本文件是 AI 法律助理(LangGraph 多智能体系统)中的"数值校验节点", 对应业务流程的 N5c 环节。
# 与 N5c-1 数值抽取节点(LLM 抽取)配套使用, 本节点接收抽取出的数值字典(extracted_numerics),
# 基于 YAML 规则文件做"确定性"规则校验(无 LLM 调用, 100% 可解释可审计)。
# 核心职责是: 加载两份 YAML 规则文件(contract_review.yaml / compliance_review.yaml),
# 逐条规则对数值进行校验(threshold 阈值/range 范围/sum_equals 求和三种校验类型),
# 将命中的违规项写入 state["numeric_risk_items"]。
# 该字段是三路风险检测(合同审核/合规审查/数值校验)之一, 最终会被 risk_aggregate_node 聚合。
# 设计亮点: (1) 规则与代码分离, 业务人员可直接维护 YAML 而不改代码;
# (2) _to_number 辅助函数支持中文数字("千分之三"/"50万")的智能转换;
# (3) 三种校验类型覆盖常见的数值合规场景(单值阈值/区间范围/多项求和)。


# 导入 os 模块, 用于路径拼接与文件存在性检查
import os

# 导入 yaml 模块, 用于解析 YAML 规则文件
# yaml 是 PyYAML 库提供的, 需 pip install pyyaml
import yaml

# 从同包导入 AgentState 类型, 作为节点函数的类型注解
from __004__langgraph_more_nodes.agent_state import AgentState

# 从 common.path_utils 导入 root_dir, 即项目根目录的绝对路径
# 用于定位 config/rules 目录下的 YAML 规则文件
from common.path_utils import root_dir


def _load_rules():
    """
    内部辅助函数: 加载两份 YAML 规则文件, 合并为统一的规则列表。

    作用:
        从项目根目录的 config/rules 子目录下读取
        contract_review.yaml 与 compliance_review.yaml 两份规则文件,
        解析其中的规则列表并合并返回。支持两种 YAML 结构:
        (1) {"rules": [...]} 标准结构;
        (2) {key1: [...], key2: [...]} 任意键的列表结构(取所有列表值合并)。

    参数:
        无

    返回值:
        list: 合并后的规则列表, 每个元素是一个规则字典。若文件不存在则返回空列表。

    可迁移性说明:
        本函数的"多 YAML 文件合并加载 + 多结构兼容"模式可迁移到任何规则引擎场景,
        例如: 风控规则加载、配置中心、特征工程规则等。
        通过扩展 fname 列表可支持更多规则文件, 实现规则的模块化管理。
    """
    # rules 列表用于收集所有加载到的规则
    rules = []

    # 拼接规则文件目录的绝对路径: 项目根目录 + "config/rules"
    rule_dir = os.path.join(root_dir, "config", "rules")

    # 遍历两份规则文件名, 逐个加载
    for fname in ["contract_review.yaml", "compliance_review.yaml"]:
        # 拼接完整的规则文件路径
        fpath = os.path.join(rule_dir, fname)

        # 仅在文件存在时加载, 避免文件缺失导致异常
        if os.path.exists(fpath):
            # 以 UTF-8 编码打开文件(避免 Windows 默认 GBK 导致中文规则乱码)
            with open(fpath, "r", encoding="utf-8") as f:
                # yaml.safe_load 解析 YAML 文件为 Python 对象(字典/列表等)
                # 使用 safe_load 而非 load 是安全考虑, 避免任意代码执行风险
                data = yaml.safe_load(f)

                # 仅在 data 是字典时处理(防御空文件或异常 YAML)
                if isinstance(data, dict):
                    # YAML 结构兼容: 优先识别 {"rules": [...]} 标准结构
                    if "rules" in data:
                        # 标准结构: 直接 extend rules 列表
                        rules.extend(data["rules"])
                    else:
                        # 非标准结构: 遍历所有键, 若值是列表则 extend
                        # 这支持 {threshold_rules: [...], range_rules: [...]} 等自定义分组结构
                        for k, v in data.items():
                            if isinstance(v, list):
                                rules.extend(v)
    # 返回合并后的规则列表
    return rules


def _to_number(val):
    """
    内部辅助函数: 将各种格式的数值(含中文表达)转换为 float。

    作用:
        支持 int/float 直接返回、字符串清洗(去逗号/单位)、中文比例("千分之三"/"百分之五")、
        中文量级("50万"/"3千")等多种格式的智能转换。是数值校验的关键预处理函数。

    参数:
        val: 待转换的值, 可以是 None/int/float/str 等类型。

    返回值:
        float 或 None: 转换成功返回 float, 无法转换返回 None。

    可迁移性说明:
        本函数的"多格式数值智能转换"逻辑可迁移到任何需要处理中文数值的场景,
        例如: 财务报表解析、合同金额提取、电商价格清洗等。
        若需扩展支持"亿"/"百万"等量级, 只需在量级处理分支新增对应规则即可。
    """
    # None 直接返回 None, 表示无数据
    if val is None:
        return None

    # 若已是数字类型(int/float), 直接转 float 返回
    if isinstance(val, (int, float)):
        return float(val)

    # 字符串预处理: 去除常见分隔符与单位
    # replace(",", "") 去千分位逗号(如 "1,000" -> "1000")
    # replace("元", "") 去金额单位
    # replace("%", "") 去百分号(后续按小数处理)
    # strip() 去首尾空白
    s = str(val).replace(",", "").replace("元", "").replace("%", "").strip()

    # 第一阶段: 处理中文比例表达("千分之三"/"百分之五"/"万分之二")
    # cn_map 定义中文比例前缀与对应的小数换算系数
    cn_map = {"千分之": 0.001, "百分之": 0.01, "万分之": 0.0001}
    for cn, ratio in cn_map.items():
        # 若字符串包含中文比例前缀(如"千分之")
        if cn in s:
            # 去除前缀, 剩余部分应为数字(如"三"/"5")
            num_part = s.replace(cn, "").strip()
            try:
                # 尝试将剩余部分转为 float, 乘以换算系数
                # 注意: 此处仅处理阿拉伯数字, "千分之三"中的"三"无法转换, 会进入 except
                return float(num_part) * ratio
            except ValueError:
                # 转换失败则继续尝试下一个 cn 或进入下阶段
                pass

    # 第二阶段: 处理中文量级("50万"/"3千")
    try:
        # 处理"万"量级: 去除"万"后转 float, 乘以 10000
        if "万" in s:
            return float(s.replace("万", "")) * 10000
        # 处理"千"量级: 去除"千"后转 float, 乘以 1000
        if "千" in s:
            return float(s.replace("千", "")) * 1000
        # 普通数字: 直接转 float
        return float(s)
    # 若所有转换均失败, 返回 None 表示无法识别
    except ValueError:
        return None


def _check_rule(rule, numerics):
    """
    内部辅助函数: 检查单条规则是否被违反, 返回风险项字典或 None。

    作用:
        根据规则类型(threshold/range/sum_equals)对抽取的数值进行校验,
        若违反规则则返回结构化的风险项字典, 否则返回 None。

    参数:
        rule (dict): 单条规则字典, 包含 rule_id/name/check_type/target_field/threshold/operator/
                     min/max/fields/expected/severity/legal_basis 等字段(视 check_type 而定)。
        numerics (dict): 抽取的数值字典, 如 {"违约金比例": 0.005, "预付款比例": 50}。

    返回值:
        dict 或 None: 违反规则时返回风险项字典(包含 rule_id/check_type/description/severity 等),
                     未违反或无法校验时返回 None。

    可迁移性说明:
        本函数的"多类型规则校验"框架可迁移到任何规则引擎场景,
        通过新增 check_type 分支可支持更多校验类型(如正则匹配/枚举校验/跨字段比值等)。
        建议保持"返回结构化风险项"的约定, 便于聚合节点统一处理。
    """
    # 从规则字典中提取各字段, 使用 get 提供默认值避免 KeyError
    # rule_id: 规则唯一标识, 用于追踪与日志
    rule_id = rule.get("rule_id", "")
    # rule_name: 规则名称, 优先取 "name", 其次取 "description"
    rule_name = rule.get("name", rule.get("description", ""))
    # check_type: 校验类型, 决定走哪个校验分支(threshold/range/sum_equals)
    check_type = rule.get("check_type", "")
    # target_field: 目标字段名, 即要校验的数值在 numerics 中的键
    target_field = rule.get("target_field", "")
    # threshold: 阈值, 用于 threshold 类型校验
    threshold = rule.get("threshold")
    # operator: 比较运算符, 默认 ">"(大于)
    operator = rule.get("operator", ">")
    # legal_basis: 法律依据, 用于风险项展示
    legal_basis = rule.get("legal_basis", "")

    # 获取目标值: 从 numerics 字典中按 target_field 取值
    target_val = numerics.get(target_field)

    # 若精确匹配未取到值且 target_field 非空, 尝试模糊匹配
    if target_val is None and target_field:
        # 遍历 numerics 所有键, 检查 target_field 是否作为子串出现在某个键中(大小写不敏感)
        # 这处理了字段名轻微差异的情况, 如 target_field="违约金" 可匹配 numerics 中的 "违约金比例"
        for k, v in numerics.items():
            if target_field.lower() in k.lower():
                target_val = v
                break

    # 将目标值转为 float, 便于数值比较
    target_num = _to_number(target_val)
    # 若无法转为数字, 则无法校验, 返回 None(跳过该规则)
    if target_num is None:
        return None  # 无法校验

    # 阈值校验类型: 比较 target_num 与 threshold
    threshold_num = _to_number(threshold)
    # violated 标志位, 初始为 False
    violated = False
    if check_type == "threshold" and threshold_num is not None:
        # 根据 operator 选择对应的比较逻辑
        if operator == ">" and target_num > threshold_num:
            # 大于阈值则违规(如违约金比例 > 0.05)
            violated = True
        elif operator == ">=" and target_num >= threshold_num:
            # 大于等于阈值则违规
            violated = True
        elif operator == "<" and target_num < threshold_num:
            # 小于阈值则违规(如质保金比例 < 0.03)
            violated = True
        elif operator == "<=" and target_num <= threshold_num:
            # 小于等于阈值则违规
            violated = True
        elif operator == "==" and target_num == threshold_num:
            # 等于阈值则违规(如某项必须不等于某值)
            violated = True

    # 范围校验类型: 检查 target_num 是否在 [min, max] 范围内
    elif check_type == "range":
        # 从规则取 min/max 并转为数字
        min_val = _to_number(rule.get("min"))
        max_val = _to_number(rule.get("max"))
        # 若有 min 且 target 小于 min, 则违规(低于下限)
        if min_val is not None and target_num < min_val:
            violated = True
        # 若有 max 且 target 大于 max, 则违规(超过上限)
        if max_val is not None and target_num > max_val:
            violated = True

    # 求和校验类型: 多个字段之和应等于预期值(如付款比例之和=100)
    elif check_type == "sum_equals":
        # fields: 参与求和的字段列表
        fields = rule.get("fields", [])
        # expected: 预期和值, 默认 100(适用于百分比场景)
        expected = _to_number(rule.get("expected", 100))
        # total: 累加器
        total = 0
        # has_data: 标志位, 记录是否至少有一个字段有数据(避免全 None 误判)
        has_data = False
        # 遍历所有参与求和的字段, 累加可转数字的值
        for f in fields:
            v = _to_number(numerics.get(f))
            if v is not None:
                total += v
                has_data = True
        # 仅在有数据时校验, 避免全 None 时 total=0 误报
        # abs(total - expected) > 0.01 用容差比较, 避免浮点误差
        if has_data and abs(total - expected) > 0.01:
            # 求和不等则直接返回风险项(此处不复用 violated, 因结构略有不同)
            return {
                "rule_id": rule_id,
                "check_type": "sum_equals",
                "target_fields": fields,
                "expected": expected,
                "actual": total,
                "severity": rule.get("severity", "high"),
                "description": f"{rule_name}: 各项之和应为{expected}, 实际为{total}",
                "legal_basis": legal_basis,
            }

    # 若 violated 为 True(threshold 或 range 校验违规), 返回风险项
    if violated:
        return {
            "rule_id": rule_id,
            "check_type": check_type,
            "target_field": target_field,
            "target_value": target_val,
            "threshold": threshold,
            "operator": operator,
            "severity": rule.get("severity", "medium"),
            "description": f"{rule_name}: {target_field}={target_val}, {operator}{threshold}",
            "legal_basis": legal_basis,
        }

    # 未违规或无法校验, 返回 None
    return None


def numeric_validate_node(state: AgentState):
    """
    数值校验节点函数: 基于 YAML 规则对抽取的数值做确定性校验, 写入 state["numeric_risk_items"]。

    作用:
        加载 YAML 规则文件, 逐条规则对 state["extracted_numerics"] 中的数值进行校验,
        支持三种校验类型: threshold(阈值比较)、range(范围检查)、sum_equals(求和等于)。
        命中的违规项以结构化字典形式收集, 写入 state["numeric_risk_items"]。
        本节点不调用 LLM, 校验结果 100% 确定性与可解释性。

    参数:
        state (AgentState): LangGraph 共享状态字典。读取字段:
                            - extracted_numerics (Dict, 可选): 抽取的数值字典
                            写入字段:
                            - numeric_risk_items (List[Dict]): 数值校验风险项列表

    返回值:
        AgentState: 更新后的状态字典, 必含 "numeric_risk_items" 字段(可能为空列表)。

    可迁移性说明:
        本节点的"规则引擎 + 多类型校验"架构可迁移到任何需要确定性规则校验的场景,
        例如: 表单校验、配置合规检查、数据质量监控等。
        YAML 规则与代码分离的设计便于业务人员维护, 推荐保留。
        _to_number 的中文数值转换能力是法律行业的特色, 其他场景可简化。
    """
    # 打印节点开始日志
    print("开始数值校验")

    # 从状态字典中取出抽取的数值字典, 默认空字典
    numerics = state.get("extracted_numerics", {})

    # 若无数值可校验, 写入空列表并直接返回, 避免无意义加载规则
    if not numerics:
        state["numeric_risk_items"] = []
        print("无数值可校验, 跳过")
        return state

    # 加载 YAML 规则文件, 获取所有规则列表
    rules = _load_rules()
    # 打印加载的规则数量, 便于调试
    print(f"  加载 {len(rules)} 条数值规则")

    # risk_items 列表用于收集所有命中的风险项
    risk_items = []

    # 遍历每条规则, 调用 _check_rule 进行校验
    for rule in rules:
        try:
            # 检查单条规则, 返回风险项字典或 None
            risk = _check_rule(rule, numerics)
            # 若返回非 None, 表示该规则被违反, 加入风险项列表
            if risk:
                risk_items.append(risk)
        # 捕获单条规则校验过程中的异常, 避免一条规则出错影响整体流程
        except Exception as e:
            # 打印该规则的异常信息, 包含 rule_id 便于定位
            print(f"  ⚠️ 规则 {rule.get('rule_id', '?')} 校验异常: {e}")
            # continue 跳过该规则, 继续校验下一条
            continue

    # 将风险项列表写入状态字典
    state["numeric_risk_items"] = risk_items

    # 打印节点完成日志, 显示命中的风险项数量
    print(f"完成数值校验: {len(risk_items)} 个风险项")

    # 返回更新后的状态字典
    return state


# 模块自测入口: 直接运行本文件时执行, 验证数值校验逻辑
if __name__ == "__main__":
    # 构造测试状态: 提供抽取的数值(违约金比例 0.005 = 千分之五, 预付款比例 50%)
    s = AgentState(extracted_numerics={"违约金比例": 0.005, "预付款比例": 50})
    # 调用节点, 打印校验出的风险项
    print(numeric_validate_node(s).get("numeric_risk_items"))
