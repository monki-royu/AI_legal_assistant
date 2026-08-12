# 📜 代码文字逻辑解析
# 本文件定义了 AI 法律助理项目中法律检索智能体的核心状态数据结构 LegalResearchState。
# 该状态结构基于 Python 的 TypedDict 实现，用于在多节点智能体工作流（LangGraph 风格）
# 中传递与持久化各阶段的中间数据。整个法律检索流程被划分为 N1～N9 共 9 个节点：
# N1 负责合同文本解析与子查询生成；N2 负责合同类型确认；N3 负责检索节点调度
# （决定本次运行哪些检索节点）；N4 为各检索节点并行执行（结果存入 raw_results）；
# N5 负责多路检索结果融合；N6 负责冲突检测与消解；N7 负责引文格式化；
# N8 负责质量评分与重试/人工介入判定；N9 负责生成最终报告。
# LegalResearchState 将所有节点共享的输入、中间产物与最终输出集中定义，
# 确保工作流各节点间数据传递的类型安全与字段明确，是整个智能体编排的基础契约。

# 从 typing 模块导入 TypedDict（用于定义带类型提示的字典类型）、
# List（列表类型提示）、Optional（可选类型提示）、Dict（字典类型提示）
from typing import TypedDict, List, Optional, Dict


# 定义法律检索智能体的状态数据结构，继承自 TypedDict
class LegalResearchState(TypedDict):
    """
    法律检索智能体工作流的状态数据结构。

    作用：
        定义法律检索智能体在多节点工作流（N1～N9）中各阶段共享的状态字段，
        作为节点间数据传递的统一契约，保证工作流各环节能够读写一致的中间数据。

    参数：
        无（TypedDict 类型定义，非可调用类）。

    返回值：
        无（类型定义）。

    可迁移性说明：
        该状态结构面向法律检索场景设计，但其分阶段（解析→调度→检索→融合→
        冲突消解→格式化→质检→报告）的编排思路可迁移至其他需要多步推理与
        多源检索的智能体场景（如医疗诊断、金融风控），迁移时调整字段即可。
    """
    # 输入
    # 用户输入的原始合同文本，作为整个检索流程的输入基础
    contract_text: str
    # 用户提出的法律查询问题，用于驱动子查询生成与检索方向
    user_query: str

    # N1
    # 由用户查询拆分得到的子查询列表，用于多角度并行检索
    sub_queries: List[str]
    contract_type: str          # 初步类型  —— N1 阶段对合同类型的初步判定结果
    clauses: List[Dict]         # 条款列表 [{id, text}]  —— 从合同文本中解析出的条款列表，每项含 id 与 text

    # N2
    # N2 阶段确认后的合同类型（经校验/修正后的最终类型），供后续节点使用
    confirmed_contract_type: str

    # N3 调度
    active_nodes: List[str]     # 本次需要运行的检索节点名称  —— N3 调度阶段确定的本次需激活的检索节点列表
    completed_nodes: List[str]  # 已完成节点（用于去重）  —— 已完成的检索节点名称列表，用于避免重复执行

    # 检索结果（每个节点输出后存入）
    raw_results: Dict[str, List[Dict]]  # key: node_name, value: results  —— 以节点名为键、检索结果列表为值的原始结果映射

    # N5
    # N5 阶段多路检索结果融合后的统一结果列表
    fused_results: List[Dict]

    # N6
    # N6 阶段检测到的结果冲突列表，每项为描述冲突的字典
    conflicts: List[Dict]
    # N6 阶段经过冲突消解后的最终结果列表
    resolved_results: List[Dict]

    # N7
    # N7 阶段格式化后的法律引文列表，每项为格式化引文字典
    formatted_citations: List[Dict]

    # N8
    # N8 阶段对检索结果的质量评分（浮点数，通常 0～1 或 0～100）
    quality_score: float
    # 重试次数计数器，记录因质量不达标而重试的次数
    retry_count: int
    # 是否需要人工介入的标志位，True 表示质量过低需人工审核
    needs_human: bool

    # N9
    # N9 阶段生成的最终报告字典，包含面向用户的综合检索结论
    final_report: Dict
