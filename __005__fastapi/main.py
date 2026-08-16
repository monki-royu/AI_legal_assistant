# -*- coding: utf-8 -*-
"""
法智引擎 FastAPI 后端统一入口
==============================

【功能】
为法智引擎全部 9+ 功能提供统一的 HTTP API 接口, 覆盖:

  功能                     方法/路径                             说明
  ─────────────────────────────────────────────────────────────────────
  智能问答                  POST /api/v1/qa                       法律问答(LLM直答/图谱RAG)
  合同审核                  POST /api/v1/contract/review          合同审核全流程
  合规审查                  POST /api/v1/compliance/review        合规审查全流程
  法律检索(独立)            POST /api/v1/retrieval/search         独立检索引擎入口
  小红书发布                POST /api/v1/xhs/publish              小红书内容生成+发布
  法律文书生成(SSE)         POST /api/v1/docgen/generate          SSE 流式推送进度
  案例检索                  POST /api/v1/cases/search             案例知识库检索
  法规查询                  GET  /api/v1/laws/search              法规知识库检索
  历史记录                  GET/POST/DELETE /api/v1/history/*     历史记录 CRUD + 收藏/导出

【设计】
与 legal-documents 的 FastAPI 架构对齐, 但不依赖 MySQL/Redis/ES,
底层使用 common.retrieval_engine 和 common.history_store 等本地化组件,
实现零外部依赖的纯 Python API 服务, 开箱即用。

【启动】
  uvicorn __005__fastapi.main:app --reload --host 0.0.0.0 --port 8000

或:
  python -m __005__fastapi.main  (会直接启动 uvicorn)
"""

import os
import sys
import json
import asyncio
import traceback

# 把项目根目录加入 sys.path
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# UTF-8 stdout/stderr (Windows GBK 兼容)
if sys.stdout.encoding.lower().replace("-", "") != "utf8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from fastapi import FastAPI, APIRouter, Query, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional, List

# 项目内部模块
from common.retrieval_engine import engine as retrieval_engine
from common.history_store import store as history_store
from __004__langgraph_more_nodes.langgraph_main import (
    legal_response_sync, legal_response_full, legal_response
)

# ============================================================
# FastAPI 应用配置
# ============================================================
app = FastAPI(
    title="法智引擎 API",
    description="AI 法律助理全功能 API · 合同审核/合规审查/法律检索/文书生成/案例检索/法规查询/历史记录",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

router = APIRouter(prefix="/api/v1")

# ============================================================
# 请求/响应模型
# ============================================================

class QaRequest(BaseModel):
    input: str = Field(..., description="用户输入文本")
    task_type: str = Field(default="", description="强制指定任务类型(可选)")

class ContractReviewRequest(BaseModel):
    input: str = Field(..., description="合同全文")
    user_side: str = Field(default="甲方", description="用户立场")
    contract_type: str = Field(default="", description="合同类型(可选)")
    review_mode: str = Field(default="AI_AUTO", description="审核模式")
    custom_rules: list = Field(default=[], description="自定义规则(仅CUSTOM_RULES模式)")

class ComplianceReviewRequest(BaseModel):
    input: str = Field(..., description="待审查文档/描述")

class RetrievalSearchRequest(BaseModel):
    query: str = Field(..., description="检索查询")
    keywords: list = Field(default=[], description="关键词(可选)")
    task_type: str = Field(default="legal_research", description="任务类型(影响数据源挂载)")
    contract_type: str = Field(default="", description="合同类型(可选)")
    top_k: int = Field(default=8, ge=1, le=20, description="返回条数")
    sources: list = Field(default=None, description="指定数据源(可选)")

class XhsRequest(BaseModel):
    input: str = Field(..., description="小红书主题/文案要求")

class DocgenRequest(BaseModel):
    dispute_type: str = Field(..., description="纠纷类型")
    description: str = Field(..., description="事实描述")
    plaintiff: str = Field(..., description="原告/申请人")
    defendant: str = Field(..., description="被告/被申请人")
    incident_date: str = Field(default="", description="事发时间")
    claims: str = Field(default="", description="诉求")
    document_type: str = Field(default="complaint", description="期望文书类型")

class CaseSearchRequest(BaseModel):
    query: str = Field(..., description="检索关键词")
    case_type: str = Field(default="", description="案由过滤(可选)")
    court_level: str = Field(default="", description="法院级别过滤(可选)")
    page: int = Field(default=1, ge=1, description="页码")
    page_size: int = Field(default=10, ge=1, le=50, description="每页条数")

class LawSearchRequest(BaseModel):
    query: str = Field(..., description="检索关键词")
    law_name: str = Field(default="", description="法律名称过滤(可选)")
    page: int = Field(default=1, ge=1, description="页码")
    page_size: int = Field(default=20, ge=1, le=100, description="每页条数")

class StoreHistoryRequest(BaseModel):
    task_type: str = Field(..., description="任务类型")
    title: str = Field(default="", description="记录标题")
    user_input: dict = Field(default={}, description="用户输入")
    result: dict = Field(default={}, description="生成结果")
    summary: str = Field(default="", description="摘要")

class ExportHistoryRequest(BaseModel):
    fmt: str = Field(default="md", description="导出格式: md/txt")

# ============================================================
# API 端点
# ============================================================

@router.get("/health")
async def health():
    return {"status": "ok", "service": "法智引擎 API v2.0"}

# ---- 智能问答 ----
@router.post("/qa")
async def qa_endpoint(req: QaRequest):
    """法律问答/智能问答接口, 调用 LangGraph 完整链路。"""
    try:
        result = await legal_response(req.input, task_type=req.task_type or "legal_qa")
        return {"data": {"output": result or ""}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"QA 执行异常: {str(e)[:200]}")

# ---- 合同审核 ----
@router.post("/contract/review")
async def contract_review(req: ContractReviewRequest):
    """合同审核全流程, 返回结构化报告。"""
    try:
        result = legal_response_full(
            req.input,
            task_type="contract_review",
            user_side=req.user_side,
            contract_type=req.contract_type or None,
            review_mode=req.review_mode,
            custom_rules=req.custom_rules,
        )
        return {"data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"合同审核异常: {str(e)[:200]}")

# ---- 合规审查 ----
@router.post("/compliance/review")
async def compliance_review(req: ComplianceReviewRequest):
    """合规审查全流程, 返回结构化报告。"""
    try:
        result = legal_response_full(req.input, task_type="compliance_review")
        return {"data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"合规审查异常: {str(e)[:200]}")

# ---- 法律检索(独立) ----
@router.post("/retrieval/search")
async def retrieval_search(req: RetrievalSearchRequest):
    """独立检索接口: 不经过 LangGraph, 直接调用 RetrievalEngine 返回结构化结果。"""
    try:
        result = retrieval_engine.search(
            query=req.query,
            keywords=req.keywords or None,
            task_type=req.task_type,
            contract_type=req.contract_type,
            top_k=req.top_k,
            sources=req.sources,
        )
        return {"data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"检索异常: {str(e)[:200]}")

# ---- 小红书发布 ----
@router.post("/xhs/publish")
async def xhs_publish(req: XhsRequest):
    """小红书内容生成与发布。"""
    try:
        result = await legal_response(req.input, task_type="xiaohongshu")
        return {"data": {"output": result or ""}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"小红书异常: {str(e)[:200]}")

# ---- 法律文书生成(SSE) ----
@router.post("/docgen/generate")
async def docgen_generate(req: DocgenRequest):
    """法律文书生成: 通过 SSE 流式推送 Agent 工作进度, 最终返回 document_id。"""
    async def event_stream():
        # 流式推送进度(简化版: 直接调用 graph, 分阶段 yield)
        yield f"event: agent_start\ndata: {json.dumps({'agent':'系统','status':'文书生成已启动'}, ensure_ascii=False)}\n\n"
        yield f"event: log\ndata: {json.dumps({'level':'info','agent':'系统','message':'开始生成文书...'}, ensure_ascii=False)}\n\n"
        try:
            # 使用 legal_response_full 获取结构化结果
            result = legal_response_full(
                req.description or req.description,
                task_type="legal_document_gen",
                dispute_type=req.dispute_type,
                plaintiff=req.plaintiff,
                defendant=req.defendant,
                incident_date=req.incident_date,
                claims=req.claims,
                document_type=req.document_type,
            )
            document_id = result.get("document_id")
            if document_id:
                yield f"event: log\ndata: {json.dumps({'level':'info','agent':'系统','message':f'文书已完成, ID: {document_id}'}, ensure_ascii=False)}\n\n"
                yield f"event: complete\ndata: {json.dumps({'document_id': str(document_id)}, ensure_ascii=False)}\n\n"
            else:
                yield f"event: workflow_error\ndata: {json.dumps({'node_id':'system','node_name':'系统','error':'文书生成未返回 document_id'}, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"event: workflow_error\ndata: {json.dumps({'node_id':'system','node_name':'系统','error':str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )

# ---- 案例检索 ----
@router.post("/cases/search")
async def cases_search(req: CaseSearchRequest):
    """案例知识库检索: 支持关键词+案由+法院级别筛选, 分页返回。"""
    try:
        # 直接调用 RetrievalEngine 的案例搜索便捷方法
        results = retrieval_engine.search_cases(
            query=req.query, top_k=req.page * req.page_size,
            case_type=req.case_type,
        )
        if req.court_level:
            results = [r for r in results if req.court_level in (r.get("court_name", "") or "")]
        total = len(results)
        start = (req.page - 1) * req.page_size
        paged = results[start:start + req.page_size]
        items = []
        for r in paged:
            items.append({
                "id": r.get("doc_id", ""),
                "title": r.get("case_title", ""),
                "caseNo": r.get("case_no", ""),
                "court": r.get("court_name", ""),
                "caseType": r.get("case_type", ""),
                "date": r.get("judge_date", ""),
                "summary": (r.get("case_summary", "") or "")[:200],
                "tags": r.get("cited_laws", []),
            })
        return {"data": items, "total": total, "page": req.page, "page_size": req.page_size}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"案例检索异常: {str(e)[:200]}")

# ---- 法规查询 ----
@router.get("/laws/search")
async def laws_search(
    keyword: str = Query(default=""),
    law_name: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """法规知识库检索: 支持关键词+法规名筛选, 分页返回。"""
    try:
        results = retrieval_engine.search_laws(query=keyword, top_k=page * page_size)
        if law_name:
            results = [r for r in results if law_name in (r.get("law_name", "") or "")]
        total = len(results)
        start = (page - 1) * page_size
        paged = results[start:start + page_size]
        items = []
        for r in paged:
            items.append({
                "id": r.get("doc_id", ""),
                "lawName": r.get("law_name", ""),
                "articleNo": r.get("article_no", ""),
                "chapter": r.get("chapter", ""),
                "content": r.get("content", ""),
                "effectiveDate": r.get("effective_date", ""),
                "status": r.get("status", "现行有效"),
            })
        return {"data": items, "total": total, "page": page, "page_size": page_size}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"法规查询异常: {str(e)[:200]}")

# ---- 历史记录 ----
@router.get("/history")
async def history_list(
    task_type: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
    star_only: bool = Query(default=False),
    sort: str = Query(default="time-desc"),
):
    """获取历史记录列表, 支持任务类型筛选/收藏过滤/排序/分页。"""
    records, total = history_store.list(task_type, page, page_size, star_only, sort)
    return {"data": records, "total": total, "page": page, "page_size": page_size}

@router.get("/history/{record_id}")
async def history_detail(record_id: int):
    """获取单条历史记录详情。"""
    record = history_store.get(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"data": record}

@router.post("/history")
async def history_create(req: StoreHistoryRequest):
    """创建一条历史记录。"""
    record = history_store.store(
        task_type=req.task_type,
        title=req.title,
        user_input=req.user_input,
        result=req.result,
        summary=req.summary,
    )
    return {"data": record}

@router.delete("/history/{record_id}")
async def history_delete(record_id: int):
    """删除一条历史记录。"""
    ok = history_store.delete(record_id)
    if not ok:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"message": "删除成功"}

@router.patch("/history/{record_id}/star")
async def history_toggle_star(record_id: int):
    """切换收藏状态。"""
    new_star = history_store.toggle_star(record_id)
    if new_star is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"is_starred": new_star}

@router.post("/history/{record_id}/export")
async def history_export(record_id: int, req: ExportHistoryRequest):
    """导出历史记录为 md/txt 格式。"""
    record = history_store.get(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="记录不存在")
    result = record.get("result", {}) or {}
    draft = result.get("draft_content", "") or json.dumps(result, ensure_ascii=False, indent=2)
    fmt = req.fmt.lower()
    if fmt == "md":
        content = draft
        media = "text/markdown; charset=utf-8"
    else:
        content = draft.replace("# ", "").replace("## ", "")
        media = "text/plain; charset=utf-8"
    from fastapi.responses import Response
    filename = f"法律文书_{record.get('title', 'export')[:20]}.{fmt}"
    return Response(content=content, media_type=media, headers={"Content-Disposition": f"attachment; filename={filename}"})

# ---- 数据源统计 ----
@router.get("/kb/stats")
async def kb_stats():
    """返回知识库各数据源的文档统计。"""
    return {"data": retrieval_engine.kb_stats()}

# ---- 纠纷类型列表 ----
@router.get("/dispute-types")
async def dispute_types():
    """返回支持的纠纷类型列表(供前端文书生成页下拉选择)。"""
    types = [
        {"id": "labor", "name": "劳动争议", "icon": "⚙️", "color": "orange", "scenes": ["欠薪", "违法解除", "工伤"]},
        {"id": "contract", "name": "合同纠纷", "icon": "📝", "color": "blue", "scenes": ["买卖", "租赁", "借贷"]},
        {"id": "marriage", "name": "婚姻家庭", "icon": "💑", "color": "pink", "scenes": ["离婚", "抚养权", "财产分割"]},
        {"id": "traffic", "name": "交通事故", "icon": "🚗", "color": "yellow", "scenes": ["赔偿", "责任认定"]},
        {"id": "property", "name": "房产纠纷", "icon": "🏠", "color": "green", "scenes": ["买卖", "租赁", "拆迁"]},
        {"id": "other", "name": "其他纠纷", "icon": "⚖️", "color": "purple", "scenes": ["侵权", "行政", "刑事"]},
    ]
    return {"data": types}


# 注册路由
app.include_router(router)

# ============================================================
# 直接运行入口
# ============================================================
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("API_PORT", "8000"))
    print(f"⚖️ 法智引擎 API 启动: http://localhost:{port}/docs")
    uvicorn.run(app, host="0.0.0.0", port=port)