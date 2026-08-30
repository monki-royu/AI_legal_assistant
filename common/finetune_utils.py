"""微调数据收集工具 (finetune_utils)

【功能】
在各业务节点的关键路径上旁路收集 LLM 的输入/输出样本,
为后续模型微调 (Fine-Tuning) 积累训练数据.

【设计原则】
- 非侵入式: 采集逻辑独立 try/except, 失败时仅打印告警, 绝不影响主流程.
- 可追溯: 每条样本记录节点名、输入、输出、任务类型和时间戳.
- 易解析: 使用 JSONL 格式 (一行一个 JSON 对象), 方便后续批量读取.

【使用方式】
    from common.finetune_utils import collect_ft_sample
    collect_ft_sample("node_name", input_text, output_data, task_type="xxx")

【输出位置】
    data/finetune_samples.jsonl (项目根目录下)
"""

import json
import os
import time
from datetime import datetime

# 样本存储路径: 项目根目录下的 data/finetune_samples.jsonl
_SAMPLE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "finetune_samples.jsonl",
)


def collect_ft_sample(node_name: str, input_text: str, output_data, task_type: str = ""):
    """收集单个微调样本 (输入/输出对).

    【参数】
        node_name (str): 节点名称, 如 "doc_case_analyze"
        input_text (str): 输入文本 (截断到 2000 字符为宜)
        output_data (Any): 输出数据 (dict/str/list 等, 会被 json.dumps 序列化)
        task_type (str): 任务类型, 如 "legal_document_gen"

    【返回】
        None (纯副作用: 追加一行到 JSONL 文件)

    【异常处理】
        IO 错误 / 序列化错误均向上抛出, 调用方负责 try/except 兜底.
    """
    sample = {
        "node": node_name,
        "task_type": task_type,
        "timestamp": datetime.now().isoformat(),
        "ts": time.time(),
        "input": input_text,
        "output": output_data if not isinstance(output_data, (dict, list)) else output_data,
    }

    # 确保输出目录存在
    os.makedirs(os.path.dirname(_SAMPLE_PATH), exist_ok=True)

    # 追加写入 (UTF-8 编码, 兼容中文)
    with open(_SAMPLE_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(sample, ensure_ascii=False) + "\n")
