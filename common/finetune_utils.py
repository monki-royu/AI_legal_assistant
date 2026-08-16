# -*- coding: utf-8 -*-
"""
微调数据收集工具 (Fine-tune Data Collector)
============================================

【定位】
提供一个全局统一的微调数据保存函数，供所有 LLM 驱动的智能体节点调用。
每个节点在 return 之前调用一次 collect_ft_sample()，自动将输入/输出对
追加到对应的 jsonl 文件中，后续可用于 LoRA / QLoRA 等指令微调。

【数据格式】
每行一条 JSON:
  {"agent": "节点名", "input": "...", "output": {...},
   "task_type": "...", "timestamp": "2025-...", "source": "..."}

【存储路径】
  data/ft_data/{agent_name}.jsonl  — 按智能体分文件存储
  data/ft_data/all_ft_data.jsonl   — 全量汇总（用于全链路微调）

【用法】
  from common.finetune_utils import collect_ft_sample
  collect_ft_sample("contract_ai_review", input_text, output_data, task_type=...)
"""
import os
import json
import time
from datetime import datetime
from common.path_utils import root_dir

# 微调数据根目录
_FT_ROOT = os.path.join(root_dir, "data", "ft_data")


def collect_ft_sample(
    agent_name: str,
    input_text: str,
    output_data: any,
    task_type: str = "",
    source: str = "",
    max_input_len: int = 2000,
    max_output_len: int = 2000,
) -> None:
    """
    收集一条微调样本，追加到 jsonl 文件。

    Parameters
    ----------
    agent_name : str
        智能体/节点名称，如 "contract_ai_review"、"case_search"。
        会作为文件名的一部分: data/ft_data/{agent_name}.jsonl
    input_text : str
        该节点的输入文本(用户问题/文档片段/检索结果等)。
    output_data : any
        该节点的输出数据(风险项列表/答案字符串/检索结果列表等)。
        会被 JSON 序列化(ensure_ascii=False)。
    task_type : str, optional
        当前任务类型，如 "contract_review"、"legal_qa"。
    source : str, optional
        数据来源，如 "生产环境"、"测试环境"、"手动标注"。
    max_input_len : int
        输入文本截取长度，防止单条数据过大。默认 2000 字符。
    max_output_len : int
        输出数据截取长度。默认 2000 字符。
    """
    try:
        os.makedirs(_FT_ROOT, exist_ok=True)

        # 截取输入(防止单条数据过大)
        if isinstance(input_text, str) and len(input_text) > max_input_len:
            input_text = input_text[:max_input_len]

        # 构造样本记录
        sample = {
            "agent": agent_name,
            "input": input_text,
            "output": output_data,
            "task_type": task_type,
            "timestamp": datetime.now().isoformat(),
            "source": source or "production",
        }

        # 序列化输出
        sample_json = json.dumps(sample, ensure_ascii=False, default=str)

        # ---- 写入按智能体分文件 ----
        agent_path = os.path.join(_FT_ROOT, f"{agent_name}.jsonl")
        with open(agent_path, "a", encoding="utf-8") as f:
            f.write(sample_json + "\n")

        # ---- 写入全量汇总文件 ----
        all_path = os.path.join(_FT_ROOT, "all_ft_data.jsonl")
        with open(all_path, "a", encoding="utf-8") as f:
            f.write(sample_json + "\n")

    except Exception as e:
        # 微调数据收集不允许影响主流程——失败时只打印警告
        print(f"[FT] ⚠️ 收集微调数据失败({agent_name}): {e}")