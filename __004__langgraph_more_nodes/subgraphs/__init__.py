"""子图包 (Subgraphs) —— 法智引擎 LangGraph 子图组合单元

本包按"技术架构图 / docs/技术架构图.html" 实现 7 个独立编译的子图,
供 langgraph_main.py 通过 add_node(name, subgraph) 组合复用.

子图清单:
  ┌─────────────────┬──────────────────────────────────────────────────────┐
  │ 子图名            │ 用途                                                │
  ├─────────────────┼──────────────────────────────────────────────────────┤
  │ preprocess       │ 合同/合规 文档预处理 (6 节点完整链)                    │
  │ retrieval        │ 检索能力中枢 (6 节点 + 质量门禁回边)                    │
  │ dual_review      │ 双审 fan-out/fan-in (8 节点, 双任务模式)              │
  │ contract_compliance │ 合同合规编排外壳 (输入管道+preprocess+retrieval+dual_review 嵌套) │
  │ qa               │ 法律问答三级路由 (4 节点, 嵌套 retrieval)               │
  │ docgen           │ 文书生成 + 法条校验回边 (7 节点)                        │
  │ xhs              │ 小红书发布 (5 节点 + 条件终止)                          │
  └─────────────────┴──────────────────────────────────────────────────────┘

复用关系 (subgraph composition):
  ① contract_compliance 路径: contract_compliance 子图(内部嵌套 preprocess → retrieval → dual_review)
  ② 独立检索路径:            second_intent_router → retrieval → summary
  ③ legal_qa 路径:           qa_subgraph 内部嵌套 retrieval_subgraph
  ④ legal_document_gen 路径: docgen_subgraph
  ⑤ xiaohongshu 路径:        xhs_subgraph
"""
from __004__langgraph_more_nodes.subgraphs.preprocess_subgraph import (
    preprocess_subgraph,
    build_preprocess_subgraph,
)
from __004__langgraph_more_nodes.subgraphs.retrieval_subgraph import (
    build_retrieval_subgraph,
)
from __004__langgraph_more_nodes.subgraphs.dual_review_subgraph import (
    dual_review_subgraph,
    build_dual_review_subgraph,
)
from __004__langgraph_more_nodes.subgraphs.qa_subgraph import (
    qa_subgraph,
    build_qa_subgraph,
)
from __004__langgraph_more_nodes.subgraphs.docgen_subgraph import (
    docgen_subgraph,
    build_docgen_subgraph,
)
from __004__langgraph_more_nodes.subgraphs.xhs_subgraph import (
    xhs_subgraph,
    build_xhs_subgraph,
)
from __004__langgraph_more_nodes.subgraphs.contract_compliance_subgraph import (
    build_contract_compliance_subgraph,
)


__all__ = [
    # 默认编译实例 (主图直接 import 使用)
    "preprocess_subgraph",
    "dual_review_subgraph",
    "qa_subgraph",
    "docgen_subgraph",
    "xhs_subgraph",
    # 工厂函数 (供需要重建实例的场景; retrieval 需传 checkpointer 支持 interrupt)
    "build_preprocess_subgraph",
    "build_retrieval_subgraph",
    "build_dual_review_subgraph",
    "build_qa_subgraph",
    "build_docgen_subgraph",
    "build_xhs_subgraph",
    "build_contract_compliance_subgraph",
]

