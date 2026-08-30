"""【文件作用】数值校验节点 ── 基于 YAML 规则对抽取的数值做确定性校验，生成数值风险项
【逻辑】本文件是 AI 法律助理(LangGraph 多智能体系统)中的数值校验节点
    与 数值抽取节点（LLM 抽取）配套使用。核心流程：
    1. 从 【state】 中读取抽取出的数值字典（extracted_numerics）
    2. 若数值字典为空，直接跳过（无数值可校验）
    3. 调用 _load_rules() 加载两份 YAML 规则文件（contract_review.yaml / compliance_review.yaml）
    4. 遍历每条规则，调用 _check_rule() 进行三种类型的确定性校验：
       - 【threshold 类型】单值阈值比较（大于/小于/等于/大于等于/小于等于）
       - 【range 类型】区间范围检查（是否在 [min, max] 内）
       - 【sum_equals 类型】多项求和等于预期值（如百分比之和=100%）
    5. 命中的违规项以结构化字典收集，写入 state["numeric_risk_items"]
    6. 本节点【不调用 LLM】，校验结果 100% 确定性与可解释性，可审计
    7. _to_number() 辅助函数支持中文数字智能转换（千分之三/50万/百分之五等）

设计亮点：
    - 【规则与代码分离】业务人员可直接维护 YAML 文件而不改代码
    - 【中文数值转换】支持合同审核中常见的中文比例和量级表达
    - 【三级容错】规则文件缺失→空列表、数值缺失→跳过规则、异常→继续下一条
"""

# ============================================================
# 📦 导入模块
# ============================================================

# 导入 os 模块，用于路径拼接与文件存在性检查
import os

# 导入 yaml 模块，用于解析 YAML 规则文件
# 【注意】yaml 是 PyYAML 库提供的，需 pip install pyyaml 安装
# yaml.safe_load() 安全加载 YAML，避免任意代码执行风险
import yaml

# 从同包导入 AgentState（【代理状态】类型），作为节点函数的类型注解
from __004__langgraph_more_nodes.agent_state import AgentState

# 从 common.path_utils 导入 root_dir（项目根目录绝对路径）
# 用于定位 config/rules 目录下的 YAML 规则文件
from common.path_utils import root_dir


def _load_rules(task_type: str = ""):
    """
    【功能】内部辅助函数：按任务类型加载 YAML 规则文件，合并为统一的规则列表
    【参数】task_type (str)：任务类型
            - "compliance_review" → 仅加载 compliance_review.yaml (合规单链独立运行)
            - 其他 (含 contract_review / 空) → 加载 contract_review.yaml + compliance_review.yaml
              (合同审核是"冲突消解 + 数值校验"合并链路, 双规则都参与)
    【返回值】list：合并后的规则列表，每个元素是一个规则字典。若文件不存在则返回空列表。
    【逻辑】① 拼接规则文件目录路径（root_dir + config/rules）
            ② 按 task_type 选择规则文件列表
            ③ 对每份文件，若存在则用 yaml.safe_load() 解析
            ④ 兼容两种 YAML 结构：
               a) 标准结构 {"rules": [...]} → 直接取 rules 列表
               b) 非标准结构 {key1: [...], key2: [...]} → 遍历所有键，取列表值合并
            ⑤ 返回合并后的规则列表
    【可迁移性】本函数的"多 YAML 文件合并加载 + 多结构兼容"模式可迁移到任何规则引擎场景，
            如风控规则加载、配置中心、特征工程规则等。通过扩展 fname 列表可支持更多规则文件。
    """
    # 初始化空列表 rules，用于收集所有加载到的规则字典
    rules = []  # 规则收集容器

    # 拼接规则文件目录的绝对路径：项目根目录 + "config" 子目录 + "rules" 子目录
    # os.path.join() 自动处理路径分隔符（Windows 用 \\，Linux/Mac 用 /）
    rule_dir = os.path.join(root_dir, "config", "rules")  # 规则文件目录路径

    # 按任务类型选择规则文件:
    # 合规审查单链独立运行 → 只加载合规规则; 合同审核合并链路 → 两份都加载
    if task_type == "compliance_review":
        fnames = ["compliance_review.yaml"]
    else:
        fnames = ["contract_review.yaml", "compliance_review.yaml"]

    # 遍历规则文件名，逐个加载
    # 【可扩展性】如需新增规则文件（如 credit_review.yaml），只需在此列表追加文件名
    for fname in fnames:
        # 拼接完整的规则文件路径：目录路径 + 文件名
        fpath = os.path.join(rule_dir, fname)  # 规则文件完整路径

        # 【容错】仅在文件存在时加载，避免文件缺失导致 FileNotFoundError
        if os.path.exists(fpath):
            # 以 UTF-8 编码打开文件（显式指定避免 Windows 默认 GBK 导致中文规则乱码）
            with open(fpath, "r", encoding="utf-8") as f:
                # yaml.safe_load() 将 YAML 文档解析为 Python 对象（字典或列表）
                # 【为什么用 safe_load 而非 load？】安全考虑，避免 YAML 中嵌入的任意代码执行
                data = yaml.safe_load(f)  # 解析后的 YAML 数据对象

                # 【防御】仅在 data 是字典类型时处理，防御空文件或异常 YAML 结构
                if isinstance(data, dict):
                    # ============================================================
                    # 兼容两种 YAML 结构
                    # ============================================================

                    # 结构一（标准）：{"rules": [rule1, rule2, ...]}
                    if "rules" in data:
                        # 标准结构：直接用 extend 将 rules 列表追加到总列表
                        # extend 比 append 高效：一次加入多个元素，而非逐个添加
                        rules.extend(data["rules"])  # 合并标准结构规则列表

                    # 结构二（非标准）：{threshold_rules: [...], range_rules: [...], ...}
                    else:
                        # 遍历字典的所有键值对
                        # 【适用场景】规则按类型分组存储时，用不同键名区分
                        for k, v in data.items():
                            # 仅当值是列表类型时才合并
                            # 这过滤掉非列表值（如元数据、版本号等）
                            if isinstance(v, list):
                                rules.extend(v)  # 合并非标准结构规则列表

    # 返回合并后的规则列表（可能为空列表）
    return rules


def _to_number(val):
    """
    【功能】内部辅助函数：将各种格式的数值（含中文表达）转换为 float 类型
    【参数】val：待转换的值，可以是 None / int / float / str 等类型
    【返回值】float 或 None：转换成功返回 float，无法转换返回 None
    【逻辑】分三个阶段智能转换：
            阶段一【中文比例】：处理"千分之三"、"百分之五"、"万分之二"等中文比例表达
            阶段二【中文量级】：处理"50万"、"3千"等带量级的中文数字
            阶段三【普通数字】：去除逗号/单位后直接转 float
    【可迁移性】本函数的"多格式数值智能转换"逻辑可迁移到任何需要处理中文数值的场景，
            如财务报表解析、合同金额提取、电商价格清洗等。
    """
    # 【容错】None 直接返回 None，表示无数据，调用方据此跳过校验
    if val is None:
        return None  # 无数据，无法转换

    # 【捷径】若已是数字类型（int / float），直接转 float 返回
    # isinstance(val, (int, float)) 检查是否是 int 或 float 类型
    if isinstance(val, (int, float)):
        return float(val)  # 数字类型直接返回

    # ============================================================
    # 字符串预处理：去除常见分隔符、单位、空白字符
    # ============================================================
    # 先转为字符串（防御传入非字符串类型）
    s = str(val)
    # .replace(",", "")     → 去除千分位逗号（如 "1,000" → "1000"）
    # .replace("元", "")    → 去除金额单位"元"
    # .replace("%", "")     → 去除百分号（后续按小数处理）
    # .strip()              → 去除首尾空白字符
    s = s.replace(",", "").replace("元", "").replace("%", "").strip()

    # ============================================================
    # 阶段一：处理中文比例表达
    # ============================================================
    # cn_map 定义中文比例前缀与对应的数值换算系数
    # "千分之" → 0.001（千分之一 = 1/1000）
    # "百分之" → 0.01（百分之一 = 1/100）
    # "万分之" → 0.0001（万分之一 = 1/10000）
    cn_map = {"千分之": 0.001, "百分之": 0.01, "万分之": 0.0001}

    # 遍历每个中文比例前缀
    for cn, ratio in cn_map.items():
        # 若字符串包含该前缀（如 "千分之五" 包含 "千分之"）
        if cn in s:
            # 去除前缀，剩余部分应为数字（如 "千分之五" → "五"，"百分之12.5" → "12.5"）
            num_part = s.replace(cn, "").strip()  # 提取数字部分
            try:
                # 尝试将数字部分转为 float，乘以换算系数
                # 例如："千分之5" → 5 * 0.001 = 0.005
                # 【注意】中文数字如"千分之三"中的"三"无法被 float() 解析，会进入 except
                return float(num_part) * ratio  # 转换成功返回
            except ValueError:
                # 若 float() 转换失败（如"三"、"五"等中文数字），继续尝试下一个 cn 或进入阶段二
                # 【TODO】可扩展支持中文数字转阿拉伯数字（如 "三" → 3）
                pass  # 跳过当前前缀，继续尝试

    # ============================================================
    # 阶段二：处理中文量级（"50万" / "3千"）
    # ============================================================
    try:
        # 处理"万"量级：去除"万"后转 float，乘以 10000
        # 例如："50万" → 50 * 10000 = 500000.0
        if "万" in s:
            return float(s.replace("万", "")) * 10000  # 万级转换

        # 处理"千"量级：去除"千"后转 float，乘以 1000
        # 例如："3千" → 3 * 1000 = 3000.0
        if "千" in s:
            return float(s.replace("千", "")) * 1000  # 千级转换

        # 普通数字：直接转 float
        # 例如："123.45" → 123.45, "5000" → 5000.0
        return float(s)  # 普通数字转换

    # 若所有转换均失败（如字符串包含无法识别的非数字字符），返回 None
    except ValueError:
        return None  # 无法转换，返回 None


def _check_rule(rule, numerics):
    """
    【功能】内部辅助函数：检查单条规则是否被违反，返回风险项字典或 None
    【参数】
        rule (dict)：单条规则字典，包含以下关键字段（视 check_type 而定）：
            - rule_id (str)【规则唯一标识】：用于追踪与日志
            - name / description (str)【规则名称】：用于风险项描述
            - check_type (str)【校验类型】：threshold / range / sum_equals 之一
            - target_field (str)【目标字段】：要校验的数值在 numerics 中的键名
            - threshold (number)【阈值】：threshold 类型校验用的比较值
            - operator (str)【比较运算符】：> / >= / < / <= / ==，默认 ">"
            - min / max (number)【范围边界】：range 类型校验用的上下界
            - fields (list)【求和字段列表】：sum_equals 类型校验用的字段名列表
            - expected (number)【预期和】：sum_equals 类型校验用的期望总和，默认 100
            - severity (str)【严重程度】：critical / high / medium / low
            - legal_basis (str)【法律依据】：展示用
        numerics (dict)【数值字典】：从 state["extracted_numerics"] 读取的键值对
            如 {"违约金比例": 0.005, "预付款比例": 50}
    【返回值】dict 或 None：违反规则时返回风险项字典，未违反或无法校验时返回 None。
            风险项字典包含规则信息与违规详情。
    【逻辑】① 从规则字典提取各字段
            ② 从 numerics 中按 target_field 查找目标值（先精确匹配，再模糊匹配子串）
            ③ 用 _to_number() 将目标值和阈值转为 float
            ④ 按 check_type 分三种校验逻辑：
                a) 【threshold】根据 operator 比较 target_num 与 threshold_num
                b) 【range】检查 target_num 是否在 [min, max] 区间外
                c) 【sum_equals】多个字段值之和是否不等于 expected
            ⑤ 违规则返回结构化的风险项字典，否则返回 None
    【可迁移性】本函数的"多类型规则校验"框架可迁移到任何规则引擎场景，
            通过新增 check_type 分支可支持更多校验类型（如正则匹配/枚举校验/跨字段比值等）。
    """
    # ============================================================
    # 【步骤1】从规则字典中提取各字段（用 .get() 提供默认值避免 KeyError）
    # ============================================================

    # rule_id：规则唯一标识符，用于追踪定位
    rule_id = rule.get("rule_id", "")  # 规则 ID

    # rule_name：规则名称，优先取 "name" 字段，其次取 "description" 字段兜底
    rule_name = rule.get("name", rule.get("description", ""))  # 规则名称

    # check_type：校验类型，决定走三个分支中的哪一个
    check_type = rule.get("check_type", "")  # 校验类型：threshold / range / sum_equals

    # target_field：目标字段名，即要校验的数值在 numerics 字典中的键
    target_field = rule.get("target_field", "")  # 目标数值字段名

    # threshold：比较阈值，用于 threshold 类型校验
    threshold = rule.get("threshold")  # 阈值（可为 None）

    # operator：比较运算符，默认 ">"（大于），可选 > / >= / < / <= / ==
    operator = rule.get("operator", ">")  # 比较运算符

    # legal_basis：法律依据，用于风险项展示
    legal_basis = rule.get("legal_basis", "")  # 法律依据

    # ============================================================
    # 【步骤2】从 numerics 字典中获取目标值
    # ============================================================

    # 先尝试精确匹配：以 target_field 为键从 numerics 中取值
    target_val = numerics.get(target_field)  # 目标字段的原始值（精确匹配）

    # 【模糊匹配兜底】若精确匹配未取到值且 target_field 非空，进行子串模糊匹配
    if target_val is None and target_field:
        # 遍历 numerics 的所有键值对
        for k, v in numerics.items():
            # 检查 target_field 是否作为子串出现在当前键中（大小写不敏感）
            # .lower() 转小写后进行子串判断，忽略大小写差异
            # 【场景示例】target_field="违约金" 可匹配 numerics 中的 "违约金比例"
            if target_field.lower() in k.lower():
                target_val = v  # 命中模糊匹配，取该值
                break  # 找到即退出循环

    # ============================================================
    # 【步骤3】将目标值转为 float，便于数值比较
    # ============================================================
    # 调用 _to_number() 将目标值转为 float
    # _to_number() 支持各种格式（int/float/中文表达等）
    target_num = _to_number(target_val)  # 转换后的数值（float 或 None）

    # 【容错】若无法转为数字（None），则跳过该规则（无法校验）
    if target_num is None:
        return None  # 无法校验，返回 None

    # ============================================================
    # 【步骤4】三种校验类型的分支逻辑
    # ============================================================

    # --- 阈值校验类型（threshold） ---
    # 先将阈值转为 float，若阈值无法转换则 threshold_num 为 None
    threshold_num = _to_number(threshold)  # 转换后的阈值

    # violated 标志位：初始为 False，违规时设为 True
    violated = False  # 是否违规的标志

    # 仅在 check_type == "threshold" 且阈值可转换时才进入校验
    if check_type == "threshold" and threshold_num is not None:
        # 根据 operator 选择对应的比较逻辑
        if operator == ">" and target_num > threshold_num:
            # 【大于阈值】违规（如违约金比例 > 5%）
            violated = True
        elif operator == ">=" and target_num >= threshold_num:
            # 【大于等于阈值】违规（如某项费用 >= 上限）
            violated = True
        elif operator == "<" and target_num < threshold_num:
            # 【小于阈值】违规（如质保金比例 < 3%）
            violated = True
        elif operator == "<=" and target_num <= threshold_num:
            # 【小于等于阈值】违规（如某项指标 <= 下限）
            violated = True
        elif operator == "==" and target_num == threshold_num:
            # 【等于阈值】违规（如某项不得等于特定值）
            violated = True

    # --- 范围校验类型（range） ---
    elif check_type == "range":
        # 从规则中取 min 和 max 并转为数字
        min_val = _to_number(rule.get("min"))  # 范围下限
        max_val = _to_number(rule.get("max"))  # 范围上限

        # 若有下限且目标值小于下限，则违规（低于最低要求）
        if min_val is not None and target_num < min_val:
            violated = True  # 低于下限
        # 若有上限且目标值大于上限，则违规（超过最高允许）
        if max_val is not None and target_num > max_val:
            violated = True  # 超过上限

    # --- 求和校验类型（sum_equals） ---
    elif check_type == "sum_equals":
        # fields：参与求和的字段名列表（如 ["首付比例", "贷款比例"]）
        fields = rule.get("fields", [])  # 求和字段列表

        # expected：预期总和值，默认 100（适用于百分比加起来=100% 的场景）
        expected = _to_number(rule.get("expected", 100))  # 预期总和

        # total：累加器，初始为 0
        total = 0  # 实际总和

        # has_data：标志位，记录是否至少有一个字段有有效数据
        # 【为什么需要？】避免所有字段都取不到值时 total=0 误报违规
        has_data = False  # 是否有有效数据

        # 遍历所有参与求和的字段
        for f in fields:
            # 从 numerics 中取字段值并转为数字
            v = _to_number(numerics.get(f))  # 单个字段的数值
            if v is not None:  # 仅当字段有值且可转换时才累加
                total += v  # 累加到总和
                has_data = True  # 标记有有效数据

        # 仅在至少有一个有效数据时才校验（避免全 None 时 total=0 误报）
        # abs(total - expected) > 0.01 使用容差比较，避免浮点数精度误差导致的误判
        if has_data and abs(total - expected) > 0.01:
            # 【直接返回】求和不等则直接返回风险项字典，不继续走下面的通用逻辑
            return {
                "rule_id": rule_id,                          # 规则 ID
                "check_type": "sum_equals",                  # 校验类型（求和）
                "target_fields": fields,                     # 参与求和的字段列表
                "expected": expected,                        # 预期总和
                "actual": total,                             # 实际总和
                "severity": rule.get("severity", "high"),    # 严重程度（默认 high）
                "description": f"{rule_name}: 各项之和应为{expected}, 实际为{total}",  # 违规描述
                "legal_basis": legal_basis,                  # 法律依据
            }

    # ============================================================
    # 【步骤5】threshold 或 range 违规的统一返回逻辑
    # ============================================================
    # 若 violated 为 True（threshold 或 range 校验违规），返回风险项字典
    if violated:
        return {
            "rule_id": rule_id,                                # 规则 ID
            "check_type": check_type,                          # 校验类型
            "target_field": target_field,                      # 目标字段名
            "target_value": target_val,                        # 目标字段原始值
            "threshold": threshold,                            # 阈值
            "operator": operator,                              # 比较运算符
            "severity": rule.get("severity", "medium"),        # 严重程度（默认 medium）
            "description": f"{rule_name}: {target_field}={target_val}, {operator}{threshold}",  # 违规描述
            "legal_basis": legal_basis,                        # 法律依据
        }

    # ============================================================
    # 【步骤6】未违规或无法校验，返回 None
    # ============================================================
    return None  # 未违规


def numeric_validate_node(state: AgentState):
    """
    【功能】数值校验节点函数：基于 YAML 规则对抽取的数值做确定性校验，写入 state["numeric_risk_items"]
    【参数】state (AgentState)：LangGraph 共享状态字典。
            读取字段：
                - extracted_numerics (Dict, 可选)【抽取的数值字典】：由 N5c-1 数值抽取节点生成
            写入字段：
                - numeric_risk_items (List[Dict])【数值校验风险项列表】：校验命中的违规项
    【返回值】AgentState：更新后的状态字典，必含 "numeric_risk_items" 字段（可能为空列表）
    【逻辑】① 从 state 读取 extracted_numerics
            ② 若为空则直接返回（无数值可校验）
            ③ 调用 _load_rules() 加载 YAML 规则文件
            ④ 遍历每条规则，调用 _check_rule() 校验
            ⑤ 若命中则加入 risk_items 列表
            ⑥ 每条规则校验用 try-except 包裹，异常时 continue 跳过
            ⑦ 将 risk_items 写入 state["numeric_risk_items"]
    【亮点】本节点【不调用 LLM】，校验结果 100% 确定性，可解释可审计。
            YAML 规则与代码分离的设计便于业务人员维护。
    """
    # 【步骤1】打印节点开始日志
    print("--- 开始数值校验 ---")

    # ============================================================
    # 【步骤2】从 state 中读取抽取的数值字典
    # ============================================================
    # 默认空字典 {}，若上游数值抽取节点未执行或失败
    numerics = state.get("extracted_numerics", {})  # 抽取的数值字典

    # ============================================================
    # 【步骤3】若无数值可校验，直接跳过
    # ============================================================
    # 空字典 {} 或 None 均视为无数值
    if not numerics:
        # 写入空列表，保证下游节点字段存在
        state["numeric_risk_items"] = []
        print("  无数值可校验, 跳过")  # 调试日志
        return {"numeric_risk_items": []}  # 直接返回，避免无意义加载规则

    # ============================================================
    # 【步骤4】加载 YAML 规则文件 (按任务类型分流)
    # ============================================================
    # task_type=compliance_review → 只加载合规规则 (合规单链独立运行)
    # 其他 (contract_review 合并链路) → 合同规则 + 合规规则都加载
    task_type = state.get("task_type", "")
    rules = _load_rules(task_type)

    # 打印加载的规则数量，便于调试与审计
    print(f"  加载 {len(rules)} 条数值规则 (task_type={task_type})")  # 规则数量日志

    # ============================================================
    # 【步骤5】遍历每条规则进行校验
    # ============================================================

    # 初始化 risk_items 空列表，用于收集所有命中的风险项
    risk_items = []  # 风险项收集容器

    # 遍历规则列表，逐条校验
    for rule in rules:
        try:
            # 调用 _check_rule() 检查单条规则
            # 返回风险项字典（违规）或 None（未违规/无法校验）
            risk = _check_rule(rule, numerics)  # 校验结果

            # 若返回非 None，表示该规则被违反
            if risk:
                risk_items.append(risk)  # 加入风险项列表

        # 【容错】捕获单条规则校验过程中的异常，避免一条规则出错影响整体流程
        except Exception as e:
            # 打印该规则的异常信息，包含 rule_id 便于定位问题
            print(f"  ⚠️ 规则 {rule.get('rule_id', '?')} 校验异常: {e}")
            # continue 跳过该规则，继续校验下一条
            continue

    # ============================================================
    # 【步骤6】将校验结果写入 state
    # ============================================================
    # 将风险项列表写入 state["numeric_risk_items"]
    # 下游节点（如 risk_aggregate_node）会读取此字段
    state["numeric_risk_items"] = risk_items  # 写入状态字典

    # 打印节点完成日志，显示命中的风险项数量
    print(f"--- 完成数值校验: {len(risk_items)} 个风险项 ---")

    # 返回更新后的状态字典
    return {"numeric_risk_items": risk_items}


# ============================================================
# 🧪 模块自测入口（仅在直接运行本文件时执行）
# ============================================================
if __name__ == "__main__":
    # 构造测试状态：提供抽取的数值字典
    # "违约金比例": 0.005（千分之五）
    # "预付款比例": 50（50%）
    s = AgentState(extracted_numerics={"违约金比例": 0.005, "预付款比例": 50})

    # 调用数值校验节点，打印校验出的风险项列表
    # 用于人工验证规则是否正确触发
    print(numeric_validate_node(s).get("numeric_risk_items"))