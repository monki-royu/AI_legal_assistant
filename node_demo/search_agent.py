from typing import TypedDict, List, Optional, Dict

class LegalResearchState(TypedDict):
    # 输入
    contract_text: str
    user_query: str
    
    # N1
    sub_queries: List[str]
    contract_type: str          # 初步类型
    clauses: List[Dict]         # 条款列表 [{id, text}]
    
    # N2
    confirmed_contract_type: str
    
    # N3 调度
    active_nodes: List[str]     # 本次需要运行的检索节点名称
    completed_nodes: List[str]  # 已完成节点（用于去重）
    
    # 检索结果（每个节点输出后存入）
    raw_results: Dict[str, List[Dict]]  # key: node_name, value: results
    
    # N5
    fused_results: List[Dict]
    
    # N6
    conflicts: List[Dict]
    resolved_results: List[Dict]
    
    # N7
    formatted_citations: List[Dict]
    
    # N8
    quality_score: float
    retry_count: int
    needs_human: bool
    
    # N9
    final_report: Dict