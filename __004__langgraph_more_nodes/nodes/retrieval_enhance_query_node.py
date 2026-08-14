"""
检索子节点3: 增强查询节点 (纵向 L3 LLM 伪检索兜底)
=====================================================

【设计理念】作为"纵向逐级降级"策略的最后一级(L3):
    - 当 L1(FAISS向量) + L2(本地法规txt) 两级检索后, base_citations 仍不足 2 条时,
      说明结构化数据源和本地法规库均无法满足当前检索需求, 此时启动 L3 兜底.
    - L3 调用 LLM 直接生成 3-5 条最相关法律法规条款的概要, 作为"伪检索"结果.

【防死循环 / 防空指针】
    - LLM 调用失败时, 返回空 enhance_citations, 不抛出异常, 主流程继续往下走.
    - 输出始终是 dict, 保证下游节点可安全读取 enhance_citations 字段.
    - L3 仅作为兜底, 不期望其结果具有强权威性, 仅用于防止系统空转或空指针.
"""
# 📜 代码文字逻辑解析
# 本文件是法智引擎检索智能体子图的第3个节点: 增强查询节点.
# 它在 L1(FAISS) 和 L2(本地法规txt) 两级纵向降级之后作为 L3 级兜底,
# 调用 LLM 生成相关法条概要, 用于在极端情况下避免系统出现死循环或空指针.
import json
from langchain_core.messages import HumanMessage
from common.llm import my_llm
from __004__langgraph_more_nodes.agent_state import AgentState


def retrieval_enhance_query_node(state: AgentState):
    """
    增强查询节点主函数 (L3 LLM 伪检索兜底).

    触发条件: 当 state["base_citations"] 长度 < 2 时启动 L3.
    未触发时直接返回空列表, 不消耗 LLM 配额.

    Parameters
    ----------
    state : AgentState
        共享状态, 主要读取:
          - base_citations : 基础层(L1+L2+行业增强)累积引用列表
          - doc_text       : 合同全文(截取前1000字作为LLM上下文)
          - retrieval_query: 检索查询(作为LLM提示词补充)

    Returns
    -------
    dict
        写入 enhance_citations 字段: LLM 伪检索生成的引用列表(可能为空).
    """
    print("检索 [3/5] 增强查询(L3 LLM伪检索兜底)")
    base_citations = state.get("base_citations", []) or []
    doc_text = state.get("doc_text", "")[:1000]
    retrieval_query = state.get("retrieval_query", "")[:300]

    enhance_citations = []

    # ============== 触发条件判定 ==============
    # 仅当 L1+L2 合并后结果不足 2 条时, 才触发 L3 LLM 伪检索
    if len(base_citations) >= 2:
        print(f"  [3.1] L1+L2 已有 {len(base_citations)} 条结果, 跳过 L3 LLM伪检索")
        return {"enhance_citations": enhance_citations}

    print(f"  [3.1] L1+L2 结果不足2条(仅{len(base_citations)}条), 触发 L3 LLM伪检索...")

    # ============== 构造 LLM 提示词 ==============
    # 明确要求 LLM 输出 JSON 数组格式, 便于解析
    prompt = f"""请根据以下合同内容与检索诉求, 列出3-5条最相关的法律法规条款(包括法律名称和条文编号).

【合同内容】
{doc_text}

【检索诉求】
{retrieval_query}

【输出格式】严格返回JSON数组, 不要包含任何额外说明文字:
[{{"title":"法律名称", "article_no":"第X条", "content":"条文内容概要"}}]"""

    # ============== 调用 LLM 并解析结果 ==============
    try:
        resp = my_llm.invoke([HumanMessage(content=prompt)])
        content = resp.content.strip()
        # 兼容 LLM 将 JSON 包裹在 ```json ... ``` 代码块中的情况
        if "```" in content:
            s = content.find("[")
            e = content.rfind("]") + 1
            if s >= 0 and e > s:
                content = content[s:e]
        # 解析 JSON 数组
        llm_cites = json.loads(content)
        if isinstance(llm_cites, list):
            for c in llm_cites:
                if isinstance(c, dict):
                    # 统一标记来源为 L3·LLM伪检索, 便于下游识别其权威性较低
                    c["source"] = "L3·LLM伪检索"
                    c["score"] = 0  # LLM 生成结果不参与 score 排序
                    enhance_citations.append(c)
            print(f"  [3.2] L3 LLM伪检索生成 {len(enhance_citations)} 条")
    except json.JSONDecodeError as e:
        # JSON 解析失败, 返回空列表, 不影响主流程
        print(f"  ⚠️ L3 LLM返回内容JSON解析失败: {e}")
    except Exception as e:
        # LLM 调用本身失败(网络/配额/超时等), 返回空列表
        print(f"  ⚠️ L3 LLM伪检索调用失败: {e}")

    # 始终返回 dict, 保证下游 fusion_sort 节点可安全读取 enhance_citations
    return {"enhance_citations": enhance_citations}
