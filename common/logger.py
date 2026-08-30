# -*- coding: utf-8 -*-
"""统一日志配置 (common.logger)

【为什么需要它】
    2026-08-29 全量审计发现: 三个目录共 **448 处 `print` / 仅 3 处 `logging`**,
    且那 3 处只集中在 common/mcp_beidafabao.py —— 所有 LangGraph 节点
    (44 个文件) 零 logging。

    print 的问题:
      1. 无级别 (info/warning/error 混在一起, 无法按严重度过滤)
      2. 无时间戳、无模块名, 线上排障时无法定位
      3. 无法关停 —— 想静默只能删代码
      4. Windows GBK 控制台下打印 emoji 会抛 UnicodeEncodeError
         (xhs_auto_publish_node.py 单文件就有 62 处 print 含 emoji)

【设计原则】
    - **只提供配置, 不强制替换**: 阶段 6 会按链路分批把 print 换成 logger,
      在此之前本模块可以被单独使用 (任何模块 `from common.logger import get_logger`)。
    - **幂等初始化**: setup_logging() 可重复调用, 不会重复挂 handler。
    - **控制台 UTF-8 兜底**: Windows 下把 StreamHandler 的编码强制为 utf-8,
      并设 errors="replace", 避免 emoji 打崩进程。
    - **默认不写文件**: 需要落盘时显式传 log_file。

【用法】
    from common.logger import get_logger
    logger = get_logger(__name__)
    logger.info("检索完成: %s 条", len(citations))

    # 应用入口 (FastAPI / Streamlit / CLI) 调用一次:
    from common.logger import setup_logging
    setup_logging(level="INFO", log_file="logs/app.log")
"""

import logging
import os
import sys
from typing import Optional

# 环境变量可覆盖的默认级别, 便于不同环境(开发/生产)切换
DEFAULT_LEVEL = os.getenv("LEGAL_LOG_LEVEL", "INFO").upper()

# 日志格式: 时间 | 级别 | 模块名:行号 | 消息
LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s:%(lineno)d | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 标记是否已初始化, 保证幂等
_CONFIGURED = False


def _force_utf8_stream(stream):
    """把标准流的编码强制为 UTF-8 (Windows GBK 控制台兼容)

    errors="replace" 保证遇到无法编码的字符(如某些 emoji)时替换成 '?'
    而不是抛 UnicodeEncodeError 打断业务流程 —— 日志不该让主流程崩溃。
    """
    try:
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    return stream


def setup_logging(level: Optional[str] = None,
                  log_file: Optional[str] = None,
                  force: bool = False) -> logging.Logger:
    """配置根 logger (幂等)

    参数:
        level: 日志级别字符串 (DEBUG/INFO/WARNING/ERROR), 缺省读 LEGAL_LOG_LEVEL, 再缺省 INFO
        log_file: 可选的日志文件路径, 传入则额外挂一个 FileHandler (UTF-8)
        force: True 时强制重新配置 (默认 False, 已配置过则跳过)

    返回:
        根 logger
    """
    global _CONFIGURED
    root = logging.getLogger()

    if _CONFIGURED and not force:
        return root

    # Windows 控制台编码兜底, 必须在挂 StreamHandler 之前做
    _force_utf8_stream(sys.stdout)
    _force_utf8_stream(sys.stderr)

    resolved_level = getattr(logging, (level or DEFAULT_LEVEL), logging.INFO)
    root.setLevel(resolved_level)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    if force:
        for h in list(root.handlers):
            root.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass

    if not root.handlers:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(formatter)
        try:
            console.stream = _force_utf8_stream(console.stream)
        except Exception:
            pass
        root.addHandler(console)

    if log_file:
        try:
            os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except OSError as e:
            # 日志落盘失败绝不能阻断应用启动
            root.warning("日志文件无法创建, 仅输出到控制台: %s", e)

    # 第三方库太吵, 统一压到 WARNING
    for noisy in ("httpx", "httpcore", "urllib3", "openai", "neo4j",
                  "sentence_transformers", "faiss", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True
    return root


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """获取 logger。若全局尚未配置过, 先按默认配置初始化一次。"""
    if not _CONFIGURED:
        setup_logging()
    return logging.getLogger(name)


# 提供与 print 语义接近的便捷入口, 便于阶段 6 批量替换时保持调用点改动最小
def log_node_enter(logger: logging.Logger, node_label: str, **fields) -> None:
    """节点进入日志: 统一格式, 便于按节点名检索执行轨迹"""
    extra = " ".join(f"{k}={v}" for k, v in fields.items())
    logger.info("[进入] %s %s", node_label, extra)


def log_node_exit(logger: logging.Logger, node_label: str, **fields) -> None:
    """节点退出日志"""
    extra = " ".join(f"{k}={v}" for k, v in fields.items())
    logger.info("[完成] %s %s", node_label, extra)
