# -*- coding: utf-8 -*-
"""
法智引擎 FastAPI 后端统一入口 v5.1
====================================

【功能】
为法智引擎全部功能提供统一的 HTTP API 接口, 覆盖前端 8 大任务:

  序号  任务名称              API端点                                    对应 LangGraph 路径
  ─────────────────────────────────────────────────────────────────────────────────────
  1     首页智能问答            POST /api/v1/qa                           → legal_qa (QA子图)
  2     合同审核                POST /api/v1/contract/review             → contract_review (双审子图)
  3     合规审查                POST /api/v1/compliance/review           → compliance_review (合规单路)
  4     文书生成                POST /api/v1/docgen/generate             → legal_document_gen (文书生成子图)
  5     法规查询                GET  /api/v1/laws/search                 → legal_research (检索子图)
  6     案例检索                POST /api/v1/cases/search                → case_search (检索子图)
  7     历史记录                GET/POST/DELETE /api/v1/history/*        → 独立 CRUD 操作
  8     小红书发布              POST /api/v1/xhs/publish                 → xiaohongshu_publish (小红书子图)

【架构说明】
与 LangGraph v5.0 两级路由 + 6 子图组合架构完全对齐:
  - Level 1 (binary): 小红书发布意图 → 小红书/非小红书
  - Level 2 (4 paths): 合同合规 / 检索 / 法律问答 / 文书生成
  - 三层检索: L1 领域知识库 → L2 跨域图谱 → L3 外部API (逐级降级)

【延迟加载策略】
LangGraph 主图初始化涉及大量子图编译、LLM 初始化和知识库加载,
在服务启动时同步加载会导致启动时间过长(数十秒甚至数分钟)。
本模块采用**按需延迟加载**:
  - 服务启动只做基础 FastAPI 初始化(毫秒级)
  - 首次业务请求触发 LangGraph 初始化(懒加载), 后续请求复用
  - 健康检查接口不依赖 LangGraph, 可立即响应

【启动方式】
  python __005__fastapi/main.py
  或: uvicorn __005__fastapi.main:app --host 0.0.0.0 --port 8000
"""

import os
import sys
import json
import asyncio
import traceback
from datetime import datetime

# ============================================================
# 路径与编码配置
# ============================================================
# 把项目根目录加入 sys.path, 解决直接运行时找不到包的问题
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# UTF-8 stdout/stderr (Windows GBK 兼容)
if sys.stdout.encoding and sys.stdout.encoding.lower().replace("-", "") != "utf8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ============================================================
# FastAPI 与 Pydantic 导入
# ============================================================
from fastapi import FastAPI, APIRouter, Query, HTTPException, Request  # 【新增 Request】用于SSE流式端点
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse  # 【新增】SSE 流式输出支持
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, AsyncGenerator
import time  # 【新增】用于进度心跳时间戳

# ============================================================
# 【延迟加载】LangGraph 主图接口
# ============================================================
# LangGraph 初始化非常耗时(涉及子图编译/LLM连接/知识库加载),
# 因此不在模块导入时加载, 而是在首次业务请求时按需加载并缓存。
_langgraph_initialized = False
_legal_response_sync = None
_legal_response_resume = None
_get_graph = None
_default_config = None  # 【新增】LangGraph 默认配置 (线程ID等)
_init_lock = asyncio.Lock()  # 防止并发请求重复初始化


async def _ensure_langgraph():
    """【懒加载】确保 LangGraph 主图已初始化

    使用 asyncio.Lock 保证在高并发场景下只初始化一次,
    后续请求直接复用已加载的模块级全局变量。

    耗时: 首次调用可能需要 30-60 秒(取决于知识库大小和 LLM 连接速度)。
    """
    global _langgraph_initialized, _legal_response_sync, _legal_response_resume, _get_graph, _default_config

    if _langgraph_initialized:
        return  # 已初始化, 直接返回

    async with _init_lock:
        if _langgraph_initialized:
            return  # double-check, 防止并发重复初始化

        print("[LangGraph] 开始初始化主图 (首次加载可能较慢, 请耐心等待...)")
        t0 = datetime.now()
        try:
            # 延迟导入: 只有第一次业务请求才触发 LangGraph 模块加载
            from __004__langgraph_more_nodes.langgraph_main import (
                legal_response_sync as _lrs,
                legal_response_resume as _lrr,
                get_graph as _gg,
                _default_config as _dc,
            )
            _legal_response_sync = _lrs
            _legal_response_resume = _lrr
            # get_graph 是 async def(内部仅 return graph), 此处 await 拿到真正的图对象,
            # 避免下游同步线程里 _get_graph() 返回协程导致 '.stream' 报错 (LB008)
            _get_graph = await _gg()
            _default_config = _dc
            _langgraph_initialized = True
            elapsed = (datetime.now() - t0).total_seconds()
            print(f"[LangGraph] 主图初始化完成, 耗时 {elapsed:.1f}s")
        except Exception as e:
            print(f"[LangGraph] 初始化失败: {e}")
            traceback.print_exc()
            raise


# ============================================================
# 项目内部模块导入 (轻量级模块可提前加载)
# ============================================================
from common.history_store import store as history_store

# ============================================================
# FastAPI 应用实例化
# ============================================================
app = FastAPI(
    title="法智引擎 AI 法律助理 API",
    description="""
    基于 LangGraph v5.0 的法律智能体 API 服务。
    
    支持 8 大核心任务:
    - 首页智能问答
    - 合同审核 (双审模式: AI审核 + 合规审查)
    - 合规审查 (单审模式)
    - 文书生成 (案情分析 → 模板匹配 → 条款填充 → 风险提示)
    - 法规查询
    - 案例检索
    - 历史记录管理
    - 小红书内容生成与发布
    
    注: LangGraph 主图采用延迟加载, 首次业务请求会触发初始化(耗时较长),
    后续请求响应正常。
    """,
    version="5.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS 中间件 (开发环境全开放, 生产环境应限制域名)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 路由前缀
router = APIRouter(prefix="/api/v1")

# ============================================================
# 请求/响应数据模型 (Pydantic)
# ============================================================

class QaRequest(BaseModel):
    """首页智能问答请求"""
    input: str = Field(..., description="用户输入的问题文本")
    thread_id: Optional[str] = Field(None, description="会话线程ID (多轮对话时传入)")

class ContractReviewRequest(BaseModel):
    """合同审核请求 (支持文本或文件)"""
    input: Optional[str] = Field(None, description="合同文本 (与上传文件二选一)")
    user_side: str = Field(default="甲方", description="用户立场 (甲方/乙方)")
    contract_type: str = Field(default="", description="合同类型 (租赁/买卖/劳动等)")
    review_mode: str = Field(default="AI_AUTO", description="审核模式 (AI_AUTO/CUSTOM_RULES/CUSTOM_RULES_WITH_AI)")

class ComplianceReviewRequest(BaseModel):
    """合规审查请求"""
    input: str = Field(..., description="待审查的合规文本或描述")
    contract_type: str = Field(default="", description="合同类型 (可选, 影响合规侧重点)")

class DocgenRequest(BaseModel):
    """文书生成请求"""
    dispute_type: str = Field(..., description="纠纷类型 (劳动争议/合同纠纷等)")
    description: str = Field(..., description="事实描述")
    plaintiff: str = Field(..., description="原告/申请人")
    defendant: str = Field(..., description="被告/被申请人")
    incident_date: str = Field(default="", description="事发时间")
    claims: str = Field(default="", description="诉讼请求")
    document_type: str = Field(default="complaint", description="期望文书类型")

class CaseSearchRequest(BaseModel):
    """案例检索请求"""
    query: str = Field(..., description="检索关键词")
    case_type: str = Field(default="", description="案由过滤 (可选)")
    court_level: str = Field(default="", description="法院级别过滤 (可选)")
    page: int = Field(default=1, ge=1, description="页码")
    page_size: int = Field(default=10, ge=1, le=50, description="每页条数")

class XhsRequest(BaseModel):
    """小红书发布请求"""
    input: str = Field(..., description="小红书主题/文案要求")

class ResumeRequest(BaseModel):
    """中断恢复请求 (处理北大法宝付费确认等)"""
    thread_id: str = Field(..., description="中断会话线程ID")
    resume: Any = Field(default=True, description="用户决策: True=确认/False=拒绝/str=编辑后查询")

# ============================================================
# 通用响应模型
# ============================================================
class ApiResponse(BaseModel):
    """通用 API 响应"""
    code: int = Field(default=200, description="状态码")
    message: str = Field(default="success", description="状态消息")
    data: Optional[Dict[str, Any]] = Field(default=None, description="响应数据")

# ============================================================
# 辅助函数
# ============================================================

def _build_success_response(data: Any = None, message: str = "success") -> Dict[str, Any]:
    """构建成功响应"""
    return {"code": 200, "message": message, "data": data}

def _build_error_response(message: str, code: int = 500) -> Dict[str, Any]:
    """构建错误响应"""
    return {"code": code, "message": message, "data": None}

def _extract_output_from_result(result: Dict) -> str:
    """从 LangGraph 执行结果中提取 output 字段"""
    if isinstance(result, dict):
        return result.get("output", "") or ""
    return ""

def _extract_citations_from_result(result: Dict) -> List[Dict]:
    """从 LangGraph 执行结果中提取 citations 列表"""
    if isinstance(result, dict):
        return result.get("citations", []) or []
    return []

# ============================================================
# API 端点实现
# ============================================================

@router.get("/health")
async def health_check():
    """
    健康检查接口 (不依赖 LangGraph, 可立即响应)。
    
    LangGraph 状态采用 lazy init:
    - 若尚未初始化, 返回 "idle" 状态, 首次业务请求会自动触发初始化
    - 若已初始化, 返回 "initialized" 状态
    """
    try:
        status = "initialized" if _langgraph_initialized else "idle (lazy-loading, 首次请求触发)"
        return _build_success_response({
            "status": "healthy",
            "service": "法智引擎 AI 法律助理 API v5.1",
            "langgraph": status,
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        return _build_error_response(f"服务异常: {str(e)}", 503)

# ============================================================
# 1. 首页智能问答
# ============================================================
@router.post("/qa")
async def qa_endpoint(req: QaRequest):
    """
    首页智能问答接口。
    
    支持法律问答和通用问答, LangGraph 会自动识别意图:
    - 法律问题 → QA子图 (意图识别 → 法律相关判定 → 检索子图 → 生成回答)
    - 非法律问题 → 直接 LLM 回答
    
    支持多轮对话 (通过 thread_id 保持上下文)。
    """
    try:
        # 确保 LangGraph 已初始化 (首次调用会触发懒加载)
        await _ensure_langgraph()
        
        print(f"[QA] 收到请求: input='{req.input[:50]}...', thread_id={req.thread_id}")
        
        # 调用 LangGraph 主图 (task_type 为空时自动意图识别)
        result = await asyncio.to_thread(
            _legal_response_sync,
            req.input,
            task_type="legal_qa",
            thread_id=req.thread_id,
        )
        
        # 提取输出
        output = _extract_output_from_result(result)
        
        # 构建响应
        response_data = {
            "output": output,
            "citations": _extract_citations_from_result(result),
        }
        
        # 如果有中断 (如北大法宝付费确认), 返回中断信息
        if isinstance(result, dict) and result.get("pending_interrupt"):
            response_data["pending_interrupt"] = result["pending_interrupt"]
            response_data["thread_id"] = result.get("thread_id")
            print(f"[QA] 触发中断: thread_id={result.get('thread_id')}")
        
        # 如果有 thread_id 且不是因为中断返回, 也透传给前端以支持多轮
        if isinstance(result, dict) and result.get("thread_id"):
            response_data["thread_id"] = result["thread_id"]
        
        return _build_success_response(response_data)
    except Exception as e:
        error_msg = f"智能问答执行异常: {str(e)[:200]}"
        print(f"[QA] ERROR: {error_msg}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=error_msg)

# ============================================================
# 2. 合同审核
# ============================================================
@router.post("/contract/review")
async def contract_review(req: ContractReviewRequest):
    """
    合同审核接口 (双审模式)。
    
    执行流程:
    文本/文件输入 → 输入分流 → 文档提取/文本识别 → 文档预处理
    → 检索子图 (挂载法律知识源) → 双审子图 (合同AI审核 + 合规审查)
    → 风险聚合 → 最终交付
    
    返回结构化审核报告, 包含:
    - 合同风险点列表
    - 合规审查结果
    - 风险等级与评分
    - 建议与修改意见
    """
    try:
        await _ensure_langgraph()
        
        print(f"[ContractReview] 收到请求: input_len={len(req.input or '')}, contract_type={req.contract_type}")
        
        # 构建额外参数
        kwargs = {
            "user_side": req.user_side,
            "review_mode": req.review_mode,
        }
        if req.contract_type:
            kwargs["contract_type"] = req.contract_type
        
        # 如果有输入文本, 直接传给 LangGraph
        input_text = req.input or "请审核这份合同"
        
        # 调用 LangGraph 主图
        result = await asyncio.to_thread(
            _legal_response_sync,
            input_text,
            task_type="contract_review",
            **kwargs
        )
        
        # 提取结构化结果
        response_data = {
            "output": _extract_output_from_result(result),
            "citations": _extract_citations_from_result(result),
            "risks": (result or {}).get("risk_items", []) or [],
            "compliance_risks": (result or {}).get("compliance_risk_items", []) or [],
            "can_sign": (result or {}).get("can_sign", False),
        }
        
        # 透传更多有用的字段
        if isinstance(result, dict):
            for key in ["risk_score", "contract_ai_review", "compliance_review", 
                        "user_side", "contract_type", "thread_id"]:
                if key in result:
                    response_data[key] = result[key]
        
        return _build_success_response(response_data)
    except Exception as e:
        error_msg = f"合同审核异常: {str(e)[:200]}"
        print(f"[ContractReview] ERROR: {error_msg}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=error_msg)

# ============================================================
# 3. 合规审查
# ============================================================
@router.post("/compliance/review")
async def compliance_review(req: ComplianceReviewRequest):
    """
    合规审查接口 (单审模式)。
    
    执行流程:
    文本/文件输入 → 输入分流 → 文档预处理 → 检索子图 → 合规审查 (单路)
    
    返回合规审查结果, 包含:
    - 合规风险点列表
    - 强制性规定匹配结果
    - 合规建议
    """
    try:
        await _ensure_langgraph()
        
        print(f"[ComplianceReview] 收到请求: input_len={len(req.input)}, contract_type={req.contract_type}")
        
        kwargs = {}
        if req.contract_type:
            kwargs["contract_type"] = req.contract_type
        
        # 调用 LangGraph 主图
        result = await asyncio.to_thread(
            _legal_response_sync,
            req.input,
            task_type="compliance_review",
            **kwargs
        )
        
        # 提取结构化结果
        response_data = {
            "output": _extract_output_from_result(result),
            "citations": _extract_citations_from_result(result),
            "compliance_risks": (result or {}).get("compliance_risk_items", []) or [],
            "can_sign": (result or {}).get("can_sign", False),
        }
        
        if isinstance(result, dict):
            for key in ["compliance_review", "compliance_score", "thread_id"]:
                if key in result:
                    response_data[key] = result[key]
        
        return _build_success_response(response_data)
    except Exception as e:
        error_msg = f"合规审查异常: {str(e)[:200]}"
        print(f"[ComplianceReview] ERROR: {error_msg}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=error_msg)

# ============================================================
# 4. 文书生成
# ============================================================
@router.post("/docgen/generate")
async def docgen_generate(req: DocgenRequest):
    """
    文书生成接口。
    
    执行流程 (V3 线性架构):
    案情分析 (LLM 结构化抽取) → 模板匹配 (LLM 选择最佳模板)
    → 条款填充 (纯 LLM 生成文书草稿) → 风险提示 + 质量门控 (内部调 law_search + case_search)
    → 最终交付 (组装成品 + 持久化)
    
    返回:
    - final_document: 最终文书 (Markdown 格式)
    - document_id: 历史记录 ID
    - cited_laws: 引用法条
    - risks: 风险提示
    - similar_cases: 相似案例
    """
    try:
        await _ensure_langgraph()
        
        print(f"[DocGen] 收到请求: dispute_type={req.dispute_type}, doc_type={req.document_type}")
        
        # 构建用户输入文本 (事实描述作为主输入)
        input_text = req.description
        
        # 构建额外参数 (传递案情信息给 LangGraph 状态)
        kwargs = {
            "dispute_type": req.dispute_type,
            "plaintiff": req.plaintiff,
            "defendant": req.defendant,
            "incident_date": req.incident_date,
            "claims": req.claims,
            "document_type": req.document_type,
        }
        
        # 调用 LangGraph 主图
        result = await asyncio.to_thread(
            _legal_response_sync,
            input_text,
            task_type="legal_document_gen",
            **kwargs
        )
        
        # 提取结构化结果
        response_data = {
            "final_document": (result or {}).get("final_document", "") or "",
            "document_id": (result or {}).get("document_id", None),
            "cited_laws": (result or {}).get("cited_laws", []) or [],
            "risks": (result or {}).get("risks", []) or [],
            "similar_cases": (result or {}).get("similar_cases", []) or [],
            "quality_score": (result or {}).get("retrieval_quality_score", 0),
            "force_delivery": (result or {}).get("doc_force_delivery", False),
        }
        
        # 如果有中断 (如北大法宝付费确认)
        if isinstance(result, dict) and result.get("pending_interrupt"):
            response_data["pending_interrupt"] = result["pending_interrupt"]
            response_data["thread_id"] = result.get("thread_id")
        
        if isinstance(result, dict) and result.get("thread_id"):
            response_data["thread_id"] = result["thread_id"]
        
        return _build_success_response(response_data)
    except Exception as e:
        error_msg = f"文书生成异常: {str(e)[:200]}"
        print(f"[DocGen] ERROR: {error_msg}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=error_msg)

# ============================================================
# 5. 法规查询
# ============================================================
@router.get("/laws/search")
async def laws_search(
    query: str = Query(default="", description="检索关键词"),
    law_name: str = Query(default="", description="法律名称过滤"),
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=20, ge=1, le=100, description="每页条数"),
):
    """
    法规查询接口。
    
    走 LangGraph 检索子图 (task_type="legal_research"), 
    挂载法律法规、行政法规、司法解释等知识源,
    经过融合排序和质量门控后返回法规条目列表。
    """
    try:
        await _ensure_langgraph()
        
        print(f"[LawSearch] 收到请求: query='{query}', law_name='{law_name}'")
        
        # 构建查询文本
        query_text = query or ""
        if law_name:
            query_text = f"{query_text} (法规: {law_name})" if query_text else f"法规: {law_name}"
        
        if not query_text.strip():
            raise HTTPException(status_code=400, detail="查询关键词不能为空")
        
        # 调用 LangGraph 检索子图
        result = await asyncio.to_thread(
            _legal_response_sync,
            query_text,
            task_type="legal_research"
        )
        
        # 提取 citations 并过滤法规类条目
        citations = _extract_citations_from_result(result)
        
        # 筛选法规类引用
        law_citations = []
        for c in citations:
            if not isinstance(c, dict):
                continue
            src = str(c.get("source", "")).lower()
            title = str(c.get("title", "") or "")
            # 判断是否为法规 (laws/regulations)
            is_law = any(k in src for k in ["laws", "regulations", "law", "regulation"])
            is_law = is_law or any(k in title for k in ["法", "条例", "规定", "办法", "法典", "解释"])
            if is_law:
                # 法规名筛选
                if law_name and law_name not in title and law_name not in str(c.get("law_name", "")):
                    continue
                law_citations.append(c)
        
        # 分页
        total = len(law_citations)
        start = (page - 1) * page_size
        paged = law_citations[start:start + page_size]
        
        # 格式化输出
        items = []
        for c in paged:
            items.append({
                "id": c.get("doc_id", "") or c.get("id", ""),
                "lawName": c.get("law_name", "") or c.get("title", ""),
                "articleNo": c.get("article_no", ""),
                "chapter": c.get("chapter", ""),
                "content": c.get("content", ""),
                "effectiveDate": c.get("effective_date", "") or "",
                "status": c.get("status", "现行有效"),
                "source": c.get("source", ""),
                "score": c.get("score", 0),
            })
        
        response_data = {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "quality_score": (result or {}).get("quality_score"),
            "thread_id": (result or {}).get("thread_id"),
        }
        
        return _build_success_response(response_data)
    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"法规查询异常: {str(e)[:200]}"
        print(f"[LawSearch] ERROR: {error_msg}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=error_msg)

# ============================================================
# 6. 案例检索
# ============================================================
@router.post("/cases/search")
async def cases_search(req: CaseSearchRequest):
    """
    案例检索接口。
    
    走 LangGraph 检索子图 (task_type="case_search"),
    挂载案例库知识源 (cases + laws), 单源直查模式跳过跨源融合。
    支持关键词 + 案由 + 法院级别筛选, 分页返回。
    """
    try:
        await _ensure_langgraph()
        
        print(f"[CaseSearch] 收到请求: query='{req.query}', case_type='{req.case_type}'")
        
        # 构建查询文本 (案由过滤附加到查询中)
        query_text = req.query or ""
        if req.case_type:
            query_text = f"{query_text} (案由: {req.case_type})" if query_text else f"案由: {req.case_type}"
        
        if not query_text.strip():
            raise HTTPException(status_code=400, detail="查询关键词不能为空")
        
        # 调用 LangGraph 检索子图
        result = await asyncio.to_thread(
            _legal_response_sync,
            query_text,
            task_type="case_search"
        )
        
        # 提取 citations
        citations = _extract_citations_from_result(result)
        
        # 法院级别过滤
        if req.court_level:
            citations = [
                c for c in citations
                if req.court_level in str(c.get("court_name", "") or c.get("court", "") or "")
            ]
        
        # 分页
        total = len(citations)
        start = (req.page - 1) * req.page_size
        paged = citations[start:start + req.page_size]
        
        # 格式化输出
        items = []
        for c in paged:
            items.append({
                "id": c.get("doc_id", "") or c.get("id", ""),
                "title": c.get("case_title", "") or c.get("title", ""),
                "caseNo": c.get("case_no", "") or c.get("article_no", ""),
                "court": c.get("court_name", "") or c.get("court", ""),
                "caseType": c.get("case_type", "") or c.get("keyword", ""),
                "date": c.get("judge_date", "") or c.get("date", ""),
                "summary": (c.get("case_summary", "") or c.get("content", "") or "")[:200],
                "cited_laws": c.get("cited_laws", []) or c.get("tags", []),
                "source": c.get("source", ""),
                "score": c.get("score", 0),
            })
        
        response_data = {
            "items": items,
            "total": total,
            "page": req.page,
            "page_size": req.page_size,
            "quality_score": (result or {}).get("quality_score"),
            "thread_id": (result or {}).get("thread_id"),
        }
        
        return _build_success_response(response_data)
    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"案例检索异常: {str(e)[:200]}"
        print(f"[CaseSearch] ERROR: {error_msg}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=error_msg)

# ============================================================
# 7. 历史记录 CRUD
# ============================================================

@router.get("/history")
async def history_list(
    task_type: str = Query(default="", description="按任务类型筛选"),
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=10, ge=1, le=50, description="每页条数"),
    star_only: bool = Query(default=False, description="仅显示收藏"),
    sort: str = Query(default="time-desc", description="排序方式 (time-desc/time-asc/star)"),
):
    """获取历史记录列表"""
    try:
        records, total = history_store.list(task_type, page, page_size, star_only, sort)
        return _build_success_response({
            "items": records,
            "total": total,
            "page": page,
            "page_size": page_size,
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取历史记录异常: {str(e)[:200]}")

@router.get("/history/{record_id}")
async def history_detail(record_id: int):
    """获取单条历史记录详情"""
    try:
        record = history_store.get(record_id)
        if not record:
            raise HTTPException(status_code=404, detail="记录不存在")
        return _build_success_response(record)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取记录详情异常: {str(e)[:200]}")

@router.post("/history")
async def history_create(req: Dict[str, Any]):
    """创建历史记录"""
    try:
        record = history_store.store(
            task_type=req.get("task_type", ""),
            title=req.get("title", ""),
            user_input=req.get("user_input", {}),
            result=req.get("result", {}),
            summary=req.get("summary", ""),
        )
        return _build_success_response(record)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建历史记录异常: {str(e)[:200]}")

@router.delete("/history/{record_id}")
async def history_delete(record_id: int):
    """删除历史记录"""
    try:
        ok = history_store.delete(record_id)
        if not ok:
            raise HTTPException(status_code=404, detail="记录不存在")
        return _build_success_response({"deleted": True})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除历史记录异常: {str(e)[:200]}")

@router.patch("/history/{record_id}/star")
async def history_toggle_star(record_id: int):
    """切换收藏状态"""
    try:
        new_star = history_store.toggle_star(record_id)
        if new_star is None:
            raise HTTPException(status_code=404, detail="记录不存在")
        return _build_success_response({"is_starred": new_star})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"切换收藏异常: {str(e)[:200]}")

# ============================================================
# 8. 小红书发布
# ============================================================
@router.post("/xhs/publish")
async def xhs_publish(req: XhsRequest):
    """
    小红书发布接口。
    
    执行流程:
    文案生成 (LLM) → 图片生成 (AI绘图) → 图文合规检查 → 自动发布 → 生成 Markdown 交付物
    
    返回发布结果和生成的内容。
    """
    try:
        await _ensure_langgraph()
        
        print(f"[XhsPublish] 收到请求: input='{req.input[:50]}...'")
        
        # 调用 LangGraph 主图 (Level 1 会识别小红书意图)
        result = await asyncio.to_thread(
            _legal_response_sync,
            req.input,
            task_type="xiaohongshu_publish"
        )
        
        # 提取输出
        output = _extract_output_from_result(result)
        
        response_data = {
            "output": output,
            "published": (result or {}).get("xhs_published", False),
            "content": (result or {}).get("xhs_content", {}),
            "images": (result or {}).get("xhs_images", []),
        }
        
        if isinstance(result, dict) and result.get("thread_id"):
            response_data["thread_id"] = result["thread_id"]
        
        return _build_success_response(response_data)
    except Exception as e:
        error_msg = f"小红书发布异常: {str(e)[:200]}"
        print(f"[XhsPublish] ERROR: {error_msg}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=error_msg)

# ============================================================
# 中断恢复 (处理北大法宝付费确认)
# ============================================================
@router.post("/resume")
async def resume_endpoint(req: ResumeRequest):
    """
    恢复被 interrupt() 暂停的会话。
    
    当 LangGraph 执行到北大法宝付费确认节点时会暂停,
    前端展示确认弹窗, 用户决策后携带 thread_id 调用本端点继续执行。
    
    resume 参数:
    - True: 确认调用付费接口
    - False: 拒绝, 使用现有免费结果
    - str: 编辑后的查询内容 (视为确认, 用新查询调付费接口)
    """
    try:
        await _ensure_langgraph()
        
        print(f"[Resume] 收到请求: thread_id={req.thread_id}, resume={req.resume}")
        
        result = await asyncio.to_thread(
            _legal_response_resume,
            req.thread_id,
            req.resume
        )
        
        if isinstance(result, dict) and result.get("error"):
            raise HTTPException(status_code=400, detail=result["error"])
        
        # 提取输出
        response_data = {
            "output": _extract_output_from_result(result),
            "citations": _extract_citations_from_result(result),
        }
        
        # 如果还有下一个中断, 继续透传
        if isinstance(result, dict) and result.get("pending_interrupt"):
            response_data["pending_interrupt"] = result["pending_interrupt"]
            response_data["thread_id"] = result.get("thread_id", req.thread_id)
        
        return _build_success_response(response_data)
    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"会话恢复异常: {str(e)[:200]}"
        print(f"[Resume] ERROR: {error_msg}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=error_msg)

# ============================================================
# 辅助接口
# ============================================================

@router.get("/dispute-types")
async def dispute_types():
    """返回支持的纠纷类型列表 (供前端文书生成页下拉选择)"""
    types = [
        {"id": "labor", "name": "劳动争议", "icon": "⚙️", "color": "orange", "scenes": ["欠薪", "违法解除", "工伤"]},
        {"id": "contract", "name": "合同纠纷", "icon": "📝", "color": "blue", "scenes": ["买卖", "租赁", "借贷"]},
        {"id": "marriage", "name": "婚姻家庭", "icon": "💑", "color": "pink", "scenes": ["离婚", "抚养权", "财产分割"]},
        {"id": "traffic", "name": "交通事故", "icon": "🚗", "color": "yellow", "scenes": ["赔偿", "责任认定"]},
        {"id": "property", "name": "房产纠纷", "icon": "🏠", "color": "green", "scenes": ["买卖", "租赁", "拆迁"]},
        {"id": "company", "name": "公司纠纷", "icon": "🏢", "color": "purple", "scenes": ["股权", "分红", "清算"]},
        {"id": "administrative", "name": "行政纠纷", "icon": "⚖️", "color": "teal", "scenes": ["处罚", "许可", "复议"]},
        {"id": "criminal", "name": "刑事风险", "icon": "🛡️", "color": "red", "scenes": ["罪名", "量刑", "辩护"]},
        {"id": "other", "name": "其他纠纷", "icon": "📋", "color": "gray", "scenes": ["侵权", "行政", "刑事"]},
    ]
    return _build_success_response(types)

@router.get("/template-types")
async def template_types():
    """返回支持的文书模板类型列表"""
    templates = [
        {"id": "civil_complaint", "name": "民事起诉状", "category": "诉讼文书", "description": "适用于民事纠纷的一审起诉"},
        {"id": "civil_defense", "name": "民事答辩状", "category": "诉讼文书", "description": "适用于被告方答辩"},
        {"id": "arbitration_apply", "name": "仲裁申请书", "category": "仲裁文书", "description": "适用于合同纠纷/劳动争议仲裁"},
        {"id": "lawyer_letter", "name": "律师函", "category": "非诉文书", "description": "律师正式法律函件"},
        {"id": "contract_draft", "name": "合同草稿", "category": "合同文书", "description": "起草/修订合同文本"},
        {"id": "appeal_petition", "name": "上诉状", "category": "诉讼文书", "description": "不服一审判决提起上诉"},
        {"id": "execution_apply", "name": "强制执行申请书", "category": "执行文书", "description": "申请法院强制执行"},
        {"id": "property_preservation", "name": "财产保全申请书", "category": "保全文书", "description": "诉讼前/中申请财产保全"},
        {"id": "mediation_apply", "name": "调解申请书", "category": "调解文书", "description": "申请调解"},
    ]
    return _build_success_response(templates)

@router.get("/kb/stats")
async def kb_stats():
    """返回知识库统计信息"""
    try:
        stats = {
            "laws": {"count": 0, "name": "法律法规"},
            "regulations": {"count": 0, "name": "行政法规/规章"},
            "cases": {"count": 0, "name": "裁判案例"},
            "industry": {"count": 0, "name": "行业标准"},
            "interpretations": {"count": 0, "name": "司法解释"},
        }
        # 尝试从本地知识库读取统计
        kb_dir = os.path.join(_PROJECT_ROOT, "data", "knowledge_base")
        if os.path.isdir(kb_dir):
            for key in stats:
                json_path = os.path.join(kb_dir, f"{key}_docs.json")
                if os.path.exists(json_path):
                    try:
                        with open(json_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            stats[key]["count"] = len(data) if isinstance(data, list) else 0
                    except Exception:
                        pass
        
        return _build_success_response(stats)
    except Exception as e:
        return _build_error_response(f"获取知识库统计异常: {str(e)[:200]}")

# ============================================================
# 【优化 3+4+5】SSE 流式端点 + 超时配置 + 进度心跳
# ============================================================
# 超时配置 (秒) - 从 120s 提升到 300s, 避免长任务被误判为超时
DEFAULT_TIMEOUT = 300
# 进度心跳间隔 (秒) - 每 15s 发送一次心跳, 防止代理/浏览器断连
HEARTBEAT_INTERVAL = 15


@router.post("/stream")
async def stream_task(request: Request):
    """
    SSE 流式任务端点 — 支持所有 8 大任务的流式执行.

    【What】
        通过 Server-Sent Events (SSE) 将 LangGraph 各节点的执行进度
        实时推送给前端, 让用户看到"正在做什么"而不是傻等。

    【Why】
        1. 合同审核/文书生成等长任务耗时 60-180s, 无进度反馈体验差
        2. 网络代理/浏览器默认 60s 超时, 需要心跳保持连接
        3. 用户能看到每个节点的输入/输出摘要, 增强透明度

    【How】
        1. 接收统一请求体 (input + task_type + kwargs)
        2. 用 asyncio.to_thread 在后台运行 LangGraph stream
        3. 将每个节点的输出包装为 SSE 事件推送
        4. 每 15s 发送心跳事件保持连接
        5. 完成后发送最终结果事件

    SSE 事件格式:
        event: node_start  data: {"node": "compliance_review", "timestamp": ...}
        event: node_done   data: {"node": "compliance_review", "output_keys": [...], "elapsed": ...}
        event: heartbeat   data: {"alive": true, "elapsed": ...}
        event: done        data: {"final_result": {...}, "total_elapsed": ...}
        event: error       data: {"error": "错误信息"}
    """
    # 解析请求体
    try:
        body = await request.json()
    except Exception:
        body = {}

    input_text = body.get("input", "")
    task_type = body.get("task_type", "")
    kwargs = body.get("kwargs", {})

    if not input_text:
        return _build_error_response("缺少 input 参数")

    # 确保 LangGraph 已初始化
    await _ensure_langgraph()

    # SSE 响应头
    async def event_generator() -> AsyncGenerator:
        _start_time = time.time()
        _node_count = 0
        
        try:
            # 构造初始状态
            init_state = {"input": input_text}
            if task_type:
                init_state["task_type"] = task_type
            for k, v in (kwargs or {}).items():
                if v is not None:
                    init_state[k] = v

            # 发送开始事件
            start_data = json.dumps({"task_type": task_type, "message": "开始执行", "timeout": DEFAULT_TIMEOUT}, ensure_ascii=False)
            yield f"event: start\ndata: {start_data}\n\n"

            # 在后台线程中运行 LangGraph stream (graph.stream 是同步的)
            # 优化: 只执行一次 graph.stream, 获取所有节点输出 + 最终结果
            loop = asyncio.get_running_loop()
            
            def _run_stream_once():
                """执行一次 graph.stream (含子图), 返回 ((timestamp, chunk)列表, 父图最终状态).

                关键修复 (LB006 / BC001):
                    - subgraphs=True 让检索子图/问答子图等内部节点也能被流式暴露,
                      否则 stream 只会吐出顶层子图节点, 内部 10 节点被合并成一个事件,
                      无法定位"到底哪个节点出问题"。
                    - 开启后 chunk 结构变为 (namespace_tuple, {node: state}),
                      namespace==() 表示父图顶层节点, 否则为子图路径(如 ('retrieval_subgraph',))。
                    - 同时记录每个 chunk 的时间戳, 供节点级耗时 emit。
                """
                try:
                    graph = _get_graph
                    chunks = []
                    final_state = None
                    for chunk in graph.stream(init_state, config=_default_config(), subgraphs=True):
                        chunks.append((time.time(), chunk))
                        # chunk = (namespace, data); 父图顶层节点 namespace == ()
                        if isinstance(chunk, tuple) and len(chunk) == 2:
                            _ns, _data = chunk
                            if _ns == () and isinstance(_data, dict):
                                final_state = _data
                    return chunks, final_state
                except Exception as e:
                    return [(time.time(), ("__error__", {"__error__": str(e)}))], None

            # 执行流式调用
            _stream_chunks, _final_state = await loop.run_in_executor(None, _run_stream_once)
            
            # 处理每个 chunk, 发送节点级完成事件
            # 开启 subgraphs=True 后, chunk = (timestamp, (namespace_tuple, {node: output}))
            for _ts, chunk in _stream_chunks:
                # 错误 chunk: (ts, ("__error__", {"__error__": "..."}))
                if isinstance(chunk, tuple) and chunk and chunk[0] == "__error__":
                    raise Exception(chunk[1].get("__error__", "未知错误"))
                if not (isinstance(chunk, tuple) and len(chunk) == 2):
                    continue

                _ns, chunk_data = chunk
                if not isinstance(chunk_data, dict) or not chunk_data:
                    continue

                _node_count += 1
                node_name = list(chunk_data.keys())[0]
                node_output = chunk_data.get(node_name, {})

                # 命名空间: () 表示顶层父图; 否则为子图路径, 如 ('retrieval_subgraph',)
                # 嵌套子图会变成 ('legal_qa', 'qa_subgraph') 等多级元组
                subgraph_path = ".".join(_ns) if _ns else "root"
                node_level = "subgraph" if _ns else "top"

                # 节点级耗时 (相对任务起点, 单位秒)
                node_elapsed = round(_ts - _start_time, 2)

                # 发送节点完成事件 (含子图归属 + 层级 + 耗时, 实现节点级可见性)
                output_keys = list(node_output.keys()) if isinstance(node_output, dict) else []
                event_data = json.dumps({
                    "node": node_name,
                    "subgraph": subgraph_path,
                    "level": node_level,
                    "namespace": list(_ns),
                    "output_keys": output_keys,
                    "elapsed": node_elapsed,
                    "step": _node_count
                }, ensure_ascii=False)
                yield f"event: node_done\ndata: {event_data}\n\n"
            
            _total_elapsed = time.time() - _start_time
            
            # 发送完成事件 (包含最终状态摘要)
            _final_summary = {}
            if _final_state and isinstance(_final_state, dict):
                # 只提取关键字段, 避免传输过大 payload
                for key in ["task_type", "output", "risk_score", "can_sign", 
                           "retrieval_quality_score", "thread_id"]:
                    if key in _final_state:
                        val = _final_state[key]
                        if isinstance(val, str) and len(val) > 500:
                            val = val[:500] + "..."
                        _final_summary[key] = val
            
            done_data = json.dumps({
                "summary": _final_summary,
                "total_elapsed": round(_total_elapsed, 1),
                "nodes_executed": _node_count
            }, ensure_ascii=False)
            yield f"event: done\ndata: {done_data}\n\n"

        except asyncio.CancelledError:
            cancel_data = json.dumps({"error": "客户端断开连接"}, ensure_ascii=False)
            yield f"event: error\ndata: {cancel_data}\n\n"
        except Exception as e:
            _err_elapsed = time.time() - _start_time
            error_data = json.dumps({
                "error": str(e)[:500],
                "elapsed": round(_err_elapsed, 1)
            }, ensure_ascii=False)
            yield f"event: error\ndata: {error_data}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁止 Nginx 缓冲
        }
    )


@router.post("/contract/review/stream")
async def contract_review_stream(req: ContractReviewRequest):
    """合同审核 - 流式端点 (POST /api/v1/contract/review/stream)"""
    kwargs = {"user_side": req.user_side, "review_mode": req.review_mode}
    if req.contract_type:
        kwargs["contract_type"] = req.contract_type
    
    # 使用统一流式端点
    proxy_req = Request(
        {
            "type": "http",
            "method": "POST",
            "headers": [],
            "path": "/api/v1/stream",
            "query_string": b"",
        },
        receive=lambda: None,
    )
    # 简化: 直接调用 stream 逻辑
    await _ensure_langgraph()
    
    async def gen():
        _start = time.time()
        start_json = json.dumps({"task": "contract_review"}, ensure_ascii=False)
        yield f"event: start\ndata: {start_json}\n\n"
        
        try:
            result = await asyncio.to_thread(
                _legal_response_sync,
                req.input,
                task_type="contract_review",
                **kwargs
            )
            elapsed = time.time() - _start
            done_json = json.dumps({
                "result": result,
                "elapsed": round(elapsed, 1)
            }, ensure_ascii=False)
            yield f"event: done\ndata: {done_json}\n\n"
        except Exception as e:
            err_json = json.dumps({"error": str(e)[:300]}, ensure_ascii=False)
            yield f"event: error\ndata: {err_json}\n\n"
    
    return StreamingResponse(gen(), media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/docgen/generate/stream")
async def docgen_stream(req: DocgenRequest):
    """文书生成 - 流式端点 (POST /api/v1/docgen/generate/stream)"""
    kwargs = {
        "dispute_type": req.dispute_type,
        "description": req.description,
        "plaintiff": req.plaintiff,
        "defendant": req.defendant,
        "incident_date": req.incident_date,
        "claims": req.claims,
        "document_type": req.document_type,
    }
    
    await _ensure_langgraph()
    
    async def gen():
        _start = time.time()
        start_json2 = json.dumps({"task": "docgen"}, ensure_ascii=False)
        yield f"event: start\ndata: {start_json2}\n\n"
        
        try:
            result = await asyncio.to_thread(
                _legal_response_sync,
                req.description,
                task_type="legal_document_gen",
                **kwargs
            )
            elapsed = time.time() - _start
            done_json2 = json.dumps({
                "result": result,
                "elapsed": round(elapsed, 1)
            }, ensure_ascii=False)
            yield f"event: done\ndata: {done_json2}\n\n"
        except Exception as e:
            err_json2 = json.dumps({"error": str(e)[:300]}, ensure_ascii=False)
            yield f"event: error\ndata: {err_json2}\n\n"
    
    return StreamingResponse(gen(), media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ============================================================
# 注册路由
# ============================================================
app.include_router(router)

# ============================================================
# 直接运行入口
# ============================================================
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("API_PORT", "8000"))
    print(f"{'='*60}")
    print(f"⚖️ 法智引擎 AI 法律助理 API v5.1 启动")
    print(f"   本地访问: http://localhost:{port}")
    print(f"   API文档:  http://localhost:{port}/docs")
    print(f"   ReDoc:   http://localhost:{port}/redoc")
    print(f"{'='*60}")
    print(f"📋 8大核心任务已就绪:")
    print(f"   1. 首页智能问答    POST /api/v1/qa")
    print(f"   2. 合同审核        POST /api/v1/contract/review")
    print(f"   3. 合规审查        POST /api/v1/compliance/review")
    print(f"   4. 文书生成        POST /api/v1/docgen/generate")
    print(f"   5. 法规查询        GET  /api/v1/laws/search")
    print(f"   6. 案例检索        POST /api/v1/cases/search")
    print(f"   7. 历史记录        GET/POST/DELETE /api/v1/history/*")
    print(f"   8. 小红书发布      POST /api/v1/xhs/publish")
    print(f"{'='*60}")
    print(f"💡 注意: LangGraph 采用延迟加载, 首次业务请求会触发初始化 (可能耗时 30-60s)")
    print(f"   健康检查 /docs 等轻量接口可立即响应, 无需等待 LangGraph 初始化")
    print(f"{'='*60}")
    uvicorn.run(app, host="0.0.0.0", port=port, reload=False)
