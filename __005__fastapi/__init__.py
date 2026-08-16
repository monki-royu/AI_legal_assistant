# -*- coding: utf-8 -*-
"""
法智引擎 FastAPI 后端服务
========================

提供 RESTful API 和 SSE 流式接口, 覆盖法智引擎全部 9+ 功能:
  - 智能问答 / 合同审核 / 合规审查 / 法律检索 / 小红书
  - 法律文书生成(SSE) / 案例检索 / 法规查询 / 历史记录 CRUD/导出

启动: uvicorn __005__fastapi.main:app --reload --host 0.0.0.0 --port 8000
"""
from .main import app