# -*- coding: utf-8 -*-
"""badcase 测试包

目录职责:
    bc_cases.py   —— 10 条代表性 badcase 定义 (golden set 严格锚定已入库数据)
    bc_probe.py   —— 环境/静态探针 (不跑图即可产出可验证结论)
    bc_runner.py  —— 执行器 (复用 test/t_tracer + test/t_metrics)
    bc_report.py  —— HTML 报告生成 (状态流转图 + 全量指标)
    run.py        —— 统一 CLI 入口
"""
