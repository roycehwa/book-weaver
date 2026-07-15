"""
BookMate FastAPI Backend - Phase 1 Extended
Main application with all Phase 1 APIs:
- PDF upload and chapter extraction
- AI Overview generation
- Chapter summaries
- Translation
- Reading progress tracking

改进后的版本：
- 使用 BookStorage 类进行 JSON 持久化存储
- 异步安全操作
- Pydantic 模型验证
- 完整的错误处理和日志
- 结果缓存避免重复生成
"""
import asyncio
import os
import re
import uuid
import shutil
import logging
import json
import subprocess
import hashlib
import sys
import contextlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Literal, Optional
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, APIRouter, File, Form, UploadFile, HTTPException, status, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field

from config import get_settings
from pdf_parser import PDFParser, get_parser, BookData
from storage import init_storage, get_storage, StoredBook, BookListItem
from ai_service import (
    AIBackendUnavailable,
    AIOutputError,
    BookOverview,
    ChapterSummary,
    get_ai_service,
)
from progress_storage import (
    init_progress_storage, get_progress_storage,
    ReadingProgress, ReadingProgressModel
)
from app.services.chapter_mark_service import (
    init_chapter_mark_service, get_chapter_mark_service
)
from job_service import BookJobService, JobNotFound, JobServiceError, get_job_service

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ==================== Pydantic Models ====================

class ChapterResponse(BaseModel):
    """Chapter data response model with page navigation"""
    index: int = Field(..., description="Chapter index/number")
    title: str = Field(..., description="Chapter title")
    content: str = Field(..., description="Chapter text content")
    page_number: int = Field(..., description="Chapter start page number (1-based)")
    end_page: int = Field(..., description="Chapter end page number (inclusive)")
    is_user_mark: bool = Field(default=False, description="Whether this chapter is from a user mark")
    mark_id: Optional[str] = Field(default=None, description="Associated user mark ID if is_user_mark is True")
    actual_start_page: Optional[int] = Field(default=None, description="Chapter start page with page_offset applied")


class BookChaptersResponse(BaseModel):
    """Book chapters response model"""
    book_id: str = Field(..., description="Unique book identifier")
    title: str = Field(..., description="Book title")
    total_chapters: int = Field(..., description="Total number of chapters")
    total_pages: int = Field(..., description="Total PDF pages")
    chapters: List[ChapterResponse] = Field(..., description="List of chapters")


class HealthResponse(BaseModel):
    """Health check response model"""
    status: str = Field(..., description="Service status")
    version: str = Field(..., description="API version")
    features: dict = Field(..., description="Available features")
    storage: dict = Field(default_factory=dict, description="Storage statistics")


class UploadResponse(BaseModel):
    """File upload response model"""
    book_id: str = Field(..., description="Unique book identifier")
    filename: str = Field(..., description="Original filename")
    title: str = Field(..., description="Extracted book title")
    total_chapters: int = Field(..., description="Number of chapters extracted")
    total_pages: int = Field(..., description="Total PDF pages")
    message: str = Field(..., description="Status message")


class BookListResponse(BaseModel):
    """Book list response model"""
    total_books: int = Field(..., description="Total number of books")
    books: List[BookListItem]


class DeleteResponse(BaseModel):
    """Delete response model"""
    message: str = Field(..., description="Status message")
    book_id: str = Field(..., description="Deleted book ID")


# === Phase 1: AI Overview Models ===

class BookOverviewResponse(BaseModel):
    """AI 书籍概览响应"""
    book_id: str
    introduction: str
    key_arguments: List[str]
    reading_suggestions: str
    generated_at: str
    model: str
    cached: bool = False


class GenerateOverviewRequest(BaseModel):
    """生成概览请求"""
    force_regenerate: bool = Field(default=False, description="是否强制重新生成")


class OverviewStatusResponse(BaseModel):
    """概览生成状态响应"""
    book_id: str
    has_overview: bool
    generated_at: Optional[str] = None


# === Phase 1: Chapter Summary Models ===

class ChapterSummaryResponse(BaseModel):
    """章节摘要响应"""
    book_id: str
    chapter_index: int
    chapter_title: str
    summary: str
    generated_at: str
    model: str
    cached: bool = False


class GenerateSummaryRequest(BaseModel):
    """生成摘要请求"""
    force_regenerate: bool = Field(default=False, description="是否强制重新生成")


# === Phase 1: Reading Progress Models ===

class SaveProgressRequest(BaseModel):
    """保存阅读进度请求"""
    page_number: int = Field(..., ge=1, description="当前页码")
    chapter_index: Optional[int] = Field(default=None, description="当前章节索引")


class ProgressResponse(BaseModel):
    """阅读进度响应"""
    book_id: str
    page_number: int
    chapter_index: Optional[int]
    last_read: str
    reading_percentage: float


# === Phase 2: Chapter Mark Models ===

class ChapterMarkRequest(BaseModel):
    """创建章节标记请求"""
    page_number: int = Field(..., ge=1, description="页码 (1-based)")
    y_position: float = Field(..., ge=0.0, le=1.0, description="页面垂直位置 (0-1 归一化)")
    chapter_name: Optional[str] = Field(default=None, description="可选的章节名称 (AI提取或用户输入)")


class ChapterMarkResponse(BaseModel):
    """章节标记响应"""
    mark_id: str = Field(..., description="标记唯一标识")
    page_number: int = Field(..., description="页码")
    y_position: float = Field(..., description="垂直位置")
    chapter_name: Optional[str] = Field(default=None, description="章节名称")
    created_at: str = Field(..., description="创建时间")


class CreateMarkResponse(BaseModel):
    """创建标记完整响应"""
    book_id: str = Field(..., description="书籍ID")
    mark: ChapterMarkResponse = Field(..., description="新创建的标记")
    chapters: List[ChapterResponse] = Field(..., description="重新分段后的章节列表")
    message: str = Field(..., description="状态消息")


class DeleteMarkResponse(BaseModel):
    """删除标记响应"""
    book_id: str = Field(..., description="书籍ID")
    deleted_mark_id: str = Field(..., description="已删除的标记ID")
    chapters: List[ChapterResponse] = Field(..., description="重新分段后的章节列表")
    message: str = Field(..., description="状态消息")


class PageCalibrationRequest(BaseModel):
    """页码校准请求"""
    pdf_page: Optional[int] = Field(None, ge=1, description="PDF显示的页码（1-based）")
    actual_page: Optional[int] = Field(None, ge=1, description="用户指定的实际页码（书籍印刷页码）")
    page_offset: Optional[int] = Field(None, description="直接设置页码偏移量（优先级高于 pdf_page/actual_page）")


class PageCalibrationResponse(BaseModel):
    """页码校准响应"""
    book_id: str = Field(..., description="书籍ID")
    pdf_page: int = Field(..., description="PDF页码")
    actual_page: int = Field(..., description="实际页码")
    offset: int = Field(..., description="计算出的页码偏移量 (PDF页码 - 实际页码)")
    message: str = Field(..., description="状态消息")


class BookInfoResponse(BaseModel):
    """书籍信息响应（包含页码偏移）"""
    book_id: str = Field(..., description="书籍ID")
    title: str = Field(..., description="书籍标题")
    total_chapters: int = Field(..., description="总章节数")
    total_pages: int = Field(..., description="总页数")
    page_offset: int = Field(..., description="页码偏移量")
    message: str = Field(..., description="状态消息")


class ReviewDecisionRequest(BaseModel):
    """翻译审阅决定"""
    status: Literal["approved", "resolved", "open"] = Field(default="approved", description="approved/resolved/open")
    action: Literal["manual_edit", "model_rewrite"] = Field(default="manual_edit", description="用户处理方式")
    reviewer_comment: Optional[str] = Field(default=None, description="用户修改意见")
    approved_text: Optional[str] = Field(default=None, description="用户确认后的译文")


class ReviewRewriteRequest(BaseModel):
    """触发 pdf-translator review-rewrite"""
    target_lang: str = Field(default="zh-CN", description="目标语言")
    source_lang: Optional[str] = Field(default=None, description="源语言")
    segment_id: Optional[str] = Field(default=None, description="仅重译指定段落")
    translator: Literal["openai", "mock", "minimax", "compatible", "openai-compatible"] = Field(
        default="minimax",
        description="重译使用的翻译后端",
    )


class ReviewExportRequest(BaseModel):
    """触发 pdf-translator review-export"""
    version: str = Field(..., description="版本名，例如 v2 或 final")
    parent_version: Optional[str] = Field(default=None, description="父版本标签")
    target_lang: str = Field(default="zh-CN", description="目标语言")
    output_format: Literal["pdf", "epub", "both"] = Field(default="both", description="导出格式")


class ReviewWorkflowRequest(BaseModel):
    """切换人工审阅模式"""
    human_review_mode: Literal["issues_only", "full"] = Field(
        default="issues_only",
        description="issues_only=仅审可疑段，full=全书逐段",
    )


class ReviewChapterMarkRequest(BaseModel):
    """在审阅 run 中手动定义章节起点"""
    segment_id: str = Field(..., description="新章节起始 segment_id")
    chapter_title: str = Field(..., description="章节标题")


class ReviewProjectListItem(BaseModel):
    run_dir: str
    title: str
    source_path: Optional[str] = None
    workspace_job_id: Optional[str] = None
    review_status: Literal["unreviewed", "in_review", "reviewed", "exported"]
    review_completed: bool = False
    export_completed: bool = False
    review_scope_segments: int = 0
    reviewed_scope_segments: int = 0
    total_segments: int = 0
    reviewed_segments: int = 0
    progress_percent: int = 0
    qa_items_total: int = 0
    qa_items_open: int = 0
    pending_rewrites: int = 0
    rewrites_needing_instruction: int = 0
    exported_versions: List[str] = Field(default_factory=list)
    latest_version: Optional[str] = None
    updated_at: str


class ReviewProjectsResponse(BaseModel):
    total_projects: int
    projects: List[ReviewProjectListItem]


class ReviewSyncResponse(BaseModel):
    imported: int
    skipped: int
    failed: int
    imported_runs: List[str] = Field(default_factory=list)
    skipped_sources: List[str] = Field(default_factory=list)
    failed_sources: List[str] = Field(default_factory=list)


class JobListResponse(BaseModel):
    total_jobs: int
    jobs: List[dict[str, Any]]


class DuplicateBookMatch(BaseModel):
    kind: Literal["workspace_job", "review_project"]
    id: str
    title: str
    status: str
    href: str
    updated_at: Optional[str] = None
    reason: Literal["same_file", "same_filename", "same_title"]


class DuplicateBookCheckResponse(BaseModel):
    source_filename: str
    source_sha256: str
    has_matches: bool
    matches: List[DuplicateBookMatch] = Field(default_factory=list)


class JobChapterDraftResponse(BaseModel):
    job_id: str
    chapters: List[dict[str, Any]]
    draft_source: str = "book_structure"
    draft_source_detail: Optional[str] = None
    suggested_page_offset: int = 0
    toc_page_start: Optional[int] = None
    toc_page_end: Optional[int] = None
    page_offset: Optional[int] = None
    toc_depth: Optional[int] = 1


class JobChapterDraftPrefsRequest(BaseModel):
    toc_page_start: Optional[int] = Field(default=None, ge=1, description="目录起始页（PDF 页码）")
    toc_page_end: Optional[int] = Field(default=None, ge=1, description="目录结束页（PDF 页码）")
    page_offset: Optional[int] = Field(
        default=None,
        description="页码偏移：印刷页码 = PDF 页码 + 偏移",
    )
    toc_depth: Optional[int] = Field(default=None, ge=0, le=4, description="目录层级：1=仅章，2=章+节，0=全部")


class JobChapterConfirmationRequest(BaseModel):
    chapters: Optional[List[dict[str, Any]]] = None


class JobReprocessRequest(BaseModel):
    processing_mode: Literal["auto", "translate", "preserve", "convert"]
    source_language: Optional[str] = None
    target_language: str = "zh-CN"
    translator: Literal["openai", "mock", "minimax", "compatible", "openai-compatible"] = "minimax"
    output_format: Literal["pdf", "epub", "both"] = "epub"


class GlossaryApplyRequest(BaseModel):
    source: str = Field(..., description="英文源术语")
    target: Optional[str] = Field(default=None, description="中文译法；拒绝时可留空")
    term_type: str = Field(default="concept", description="术语类型")
    status: Literal["active", "rejected", "candidate"] = Field(default="active")


class GlossaryProfileRequest(BaseModel):
    profile: Literal[
        "humanities_history",
        "social_econ_philosophy",
        "science_tech_engineering",
        "formal_logic_philosophy",
    ]


class GlossarySuggestRequest(BaseModel):
    target_lang: str = Field(default="zh-CN")
    translator: Literal["openai", "mock", "minimax", "compatible", "openai-compatible"] = "minimax"


class WorkspaceBooksResponse(BaseModel):
    total_books: int
    books: List[dict[str, Any]]
    total_source_books: int = 0
    source_books: List[dict[str, Any]] = Field(default_factory=list)
    jobs_dir: str = ""


# ==================== API Router Setup ====================
# 创建带 /api 前缀的路由器
api_router = APIRouter(prefix="/api")

# ==================== Application Lifespan ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler - 启动时初始化存储"""
    supervisor_task = None
    # Startup
    logger.info("Starting BookMate API Phase 2...")
    
    try:
        # 初始化书籍存储
        storage = await init_storage()
        stats = await storage.get_stats()
        logger.info(f"Book storage initialized: {stats['total_books']} books loaded")
        
        # 初始化阅读进度存储
        progress_storage = await init_progress_storage()
        logger.info("Progress storage initialized")

        # 初始化章节标记服务
        await init_chapter_mark_service()
        logger.info("Chapter mark service initialized")

        job_service = get_job_service()
        bootstrap_env = os.environ.copy()
        job_service._augment_subprocess_env(bootstrap_env)
        for key, value in bootstrap_env.items():
            if key in os.environ or not value:
                continue
            os.environ[key] = value

        from translation_supervisor import translation_supervisor_loop

        supervisor_task = asyncio.create_task(
            translation_supervisor_loop(get_job_service())
        )
        logger.info("Translation supervisor started")

    except Exception as e:
        logger.error(f"Failed to initialize storage: {e}")
        raise
    
    yield
    
    # Shutdown
    if supervisor_task is not None:
        supervisor_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await supervisor_task
    logger.info("Shutting down BookMate API...")


# ==================== FastAPI App Initialization ====================

settings = get_settings()
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="BookMate API - book reading, AI overview, summaries, review, progress tracking, and chapter marks",
    lifespan=lifespan
)

# ==================== Mount API Router ====================
# 将所有 /api 前缀的路由挂载到主应用
# [FIX] Moved to end of file to ensure all routes are registered first
# app.include_router(api_router)  # See end of file

# CORS middleware - allow all origins for testing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== Helper Functions ====================

def chapter_to_response(ch, page_offset: int = 0) -> ChapterResponse:
    """Convert Chapter to ChapterResponse"""
    start_page = getattr(ch, 'page_number', 1)
    return ChapterResponse(
        index=ch.index,
        title=ch.title,
        content=ch.content,
        page_number=start_page,
        end_page=getattr(ch, 'end_page', 1),
        is_user_mark=getattr(ch, 'is_user_mark', False),
        mark_id=getattr(ch, 'mark_id', None),
        actual_start_page=start_page + page_offset if page_offset else None
    )


# ==================== Root Level Endpoints (for compatibility) ====================

@app.get("/books", response_model=BookListResponse)
async def root_list_books():
    """Root level books endpoint for nginx proxy compatibility"""
    storage = await get_storage()
    books = await storage.list_books()
    return BookListResponse(
        total_books=len(books),
        books=books
    )

@app.get("/books/{book_id}/chapters", response_model=BookChaptersResponse)
async def root_get_book_chapters(book_id: str):
    """Root level chapters endpoint for nginx proxy compatibility"""
    storage = await get_storage()
    book = await storage.get_book(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    page_offset = getattr(book.metadata, 'page_offset', 0)
    return BookChaptersResponse(
        book_id=book_id,
        title=book.metadata.title,
        total_chapters=book.metadata.total_chapters,
        total_pages=book.metadata.total_pages,
        chapters=[chapter_to_response(ch, page_offset) for ch in book.chapters]
    )

@app.get("/health", response_model=HealthResponse)
async def root_health_check():
    """Root level health check for backward compatibility"""
    try:
        storage = await get_storage()
        stats = await storage.get_stats()
        
        return HealthResponse(
            status="healthy",
            version=settings.APP_VERSION,
            features={
                "pdf_parsing": True,
                "toc_extraction": True,
                "chapter_extraction": True,
                "persistent_storage": True,
                "ai_overview": True,
                "chapter_summary": True,
                "translation_review": True,
                "reading_progress": True,
                "chapter_marks": True
            },
            storage=stats
        )
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return HealthResponse(
            status="degraded",
            version=settings.APP_VERSION,
            features={
                "pdf_parsing": True,
                "toc_extraction": True,
                "chapter_extraction": True,
                "persistent_storage": True,
                "ai_overview": True,
                "chapter_summary": True,
                "translation_review": True,
                "reading_progress": True,
                "chapter_marks": True
            },
            storage={"error": str(e)}
        )

# ==================== API Endpoints ====================


def _run_job_in_background(service: BookJobService, job_id: str, *, resume: bool = False) -> None:
    try:
        if resume:
            service.get(job_id)
            service.resume(job_id)
        else:
            service.execute(job_id)
    except Exception:
        logger.exception("Background job execution failed: %s", job_id)


def _run_translate_in_background(service: BookJobService, job_id: str) -> None:
    try:
        service.run_translation(job_id)
    except Exception:
        logger.exception("Background translation failed: %s", job_id)


def _run_export_in_background(service: BookJobService, job_id: str) -> None:
    try:
        service.run_export(job_id)
    except Exception:
        logger.exception("Background EPUB export failed: %s", job_id)


def _run_glossary_suggest_in_background(
    service: BookJobService,
    job_id: str,
    *,
    target_lang: str,
    translator: str,
) -> None:
    try:
        service.glossary_suggest(
            job_id,
            target_lang=target_lang,
            translator=translator,
            from_background=True,
        )
    except Exception:
        logger.exception("Background glossary suggest failed: %s", job_id)


def _step(status_value: str, label: str, description: str) -> dict[str, str]:
    return {
        "status": status_value,
        "label": label,
        "description": description,
    }


_REVIEW_DONE_STATUSES = {"approved", "resolved"}


def _review_completion_state(
    *,
    segments: list,
    review_items: list,
    review_state: dict,
) -> dict[str, Any]:
    decisions = review_state.get("decisions", {}) if isinstance(review_state, dict) else {}
    if not isinstance(decisions, dict):
        decisions = {}
    workflow = review_state.get("workflow", {}) if isinstance(review_state, dict) else {}
    if not isinstance(workflow, dict):
        workflow = {}
    mode = workflow.get("human_review_mode")
    segment_ids = [
        str(segment.get("segment_id"))
        for segment in segments
        if isinstance(segment, dict) and str(segment.get("segment_id") or "").strip()
    ]
    if mode == "issues_only" or (not mode and review_items):
        scope_ids = [
            str(item.get("segment_id"))
            for item in review_items
            if isinstance(item, dict) and str(item.get("segment_id") or "").strip()
        ]
        if not scope_ids:
            scope_ids = segment_ids
    else:
        scope_ids = segment_ids
    scope_ids = list(dict.fromkeys(scope_ids))
    reviewed_scope_segments = sum(
        1
        for segment_id in scope_ids
        if isinstance(decisions.get(segment_id), dict)
        and decisions[segment_id].get("status") in _REVIEW_DONE_STATUSES
    )
    pending_rewrites = sum(
        1
        for decision in decisions.values()
        if isinstance(decision, dict)
        and decision.get("action") == "model_rewrite"
        and decision.get("status") == "open"
    )
    return {
        "review_scope_segments": len(scope_ids),
        "reviewed_scope_segments": reviewed_scope_segments,
        "review_completed": bool(scope_ids) and reviewed_scope_segments == len(scope_ids) and pending_rewrites == 0,
        "pending_rewrites": pending_rewrites,
        "progress_percent": int(round((reviewed_scope_segments / len(scope_ids)) * 100)) if scope_ids else 0,
    }


def _review_completion_for_job(job: dict[str, Any]) -> dict[str, Any]:
    default = {
        "review_scope_segments": 0,
        "reviewed_scope_segments": 0,
        "review_completed": False,
        "pending_rewrites": 0,
        "progress_percent": 0,
    }
    job_id = str(job.get("job_id") or "")
    if not job_id:
        return default
    artifacts = job.get("artifacts") if isinstance(job.get("artifacts"), dict) else {}
    review_items_artifact = artifacts.get("review_items")
    review_items_href = review_items_artifact.get("href") if isinstance(review_items_artifact, dict) else None
    if not isinstance(review_items_href, str):
        return default
    try:
        run_dir = (get_job_service().jobs_dir / job_id / review_items_href).resolve().parent
        segments_payload = _read_review_json(run_dir, "segments.json")
        review_items_payload = _read_review_json(run_dir, "review_items.json")
        review_state = _read_review_json(run_dir, "review_state.json")
    except Exception:
        return default
    segments = segments_payload.get("segments", []) if isinstance(segments_payload, dict) else []
    review_items = review_items_payload.get("items", []) if isinstance(review_items_payload, dict) else []
    return _review_completion_state(
        segments=segments if isinstance(segments, list) else [],
        review_items=review_items if isinstance(review_items, list) else [],
        review_state=review_state if isinstance(review_state, dict) else {},
    )


def _polish_outcome(job: dict[str, Any]) -> str | None:
    progress = job.get("progress") if isinstance(job.get("progress"), dict) else {}
    resolved = job.get("resolved") if isinstance(job.get("resolved"), dict) else {}
    request = job.get("request") if isinstance(job.get("request"), dict) else {}
    candidates = [
        job.get("polish_outcome"),
        progress.get("polish_outcome"),
        resolved.get("polish_outcome"),
        request.get("polish_outcome"),
    ]
    valid = {"applied", "no_candidates", "waived", "failed"}
    for candidate in candidates:
        if isinstance(candidate, str) and candidate in valid:
            return candidate
    return None


def _canonical_lifecycle_stage(job: dict[str, Any]) -> str:
    state = str(job.get("state") or "created")
    failed_stage = str(job.get("failed_stage") or "").strip()
    if state == "failed" and failed_stage:
        return failed_stage
    return state


def _chapters_confirmed_by_user(job: dict[str, Any], artifacts: dict[str, Any]) -> bool:
    canonical = artifacts.get("canonical_chapters")
    if isinstance(canonical, dict):
        if canonical.get("source_artifact") == "user_confirmation":
            return True
        if "source_artifact" in canonical:
            return False
    job_id = job.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        return False
    try:
        path = get_job_service().artifact_path(job_id, "canonical_chapters")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    return payload.get("source_artifact") == "user_confirmation"


def _workspace_book_from_job(job: dict[str, Any]) -> dict[str, Any]:
    state = str(job.get("state") or "created")
    failed_stage = job.get("failed_stage")
    resolved = job.get("resolved") if isinstance(job.get("resolved"), dict) else {}
    request = job.get("request") if isinstance(job.get("request"), dict) else {}
    source = job.get("source") if isinstance(job.get("source"), dict) else {}
    artifacts = job.get("artifacts") if isinstance(job.get("artifacts"), dict) else {}
    progress = job.get("progress") if isinstance(job.get("progress"), dict) else {}
    text_operation = resolved.get("text_operation")
    if not text_operation and request.get("processing_mode") in {"translate", "preserve", "convert"}:
        text_operation = request.get("processing_mode")
    is_translation_path = text_operation == "translate"
    is_preserve_path = text_operation == "preserve"
    is_convert_path = str(request.get("processing_mode") or "") == "convert"

    has_structure = "book" in artifacts or state in {
        "awaiting_glossary",
        "validating",
        "pre_review",
        "awaiting_human_review",
        "exporting",
        "completed",
    } or (state == "failed" and failed_stage in {"translating", "preserving", "validating", "pre_review"})
    glossary_candidates_ready = "glossary_candidates" in artifacts
    glossary_workflow = None
    if is_translation_path and glossary_candidates_ready:
        job_id = job.get("job_id")
        if isinstance(job_id, str) and job_id:
            try:
                glossary_workflow = get_job_service().glossary_workflow(job_id)
            except Exception:
                glossary_workflow = None
    glossary_workflow_stage = (
        glossary_workflow.get("stage") if isinstance(glossary_workflow, dict) else None
    )
    glossary_ready_pending_translation = (
        state == "awaiting_glossary" and glossary_workflow_stage == "glossary_ready"
    )
    glossary_finalized = bool(
        isinstance(glossary_workflow, dict)
        and glossary_workflow.get("glossary_finalized_by_user")
    )
    text_processing_done = state in {
        "validating",
        "pre_review",
        "awaiting_human_review",
        "exporting",
        "completed",
    } or (state == "failed" and failed_stage in {"validating", "pre_review"})
    text_processing_failed = state == "failed" and failed_stage in {"translating", "preserving"}
    review_ready = state in {"awaiting_human_review", "exporting", "completed"} and "review_items" in artifacts
    review_completion = _review_completion_for_job(job) if is_translation_path and review_ready else {}
    review_done = bool(review_completion.get("review_completed"))
    chapters_confirmed = _chapters_confirmed_by_user(job, artifacts)
    polish_outcome = _polish_outcome(job)
    polish_finished = polish_outcome in {"applied", "no_candidates", "waived"}
    polish_failed = state == "failed" and failed_stage == "polishing" or polish_outcome == "failed"
    lifecycle_stage = _canonical_lifecycle_stage(job)
    lifecycle_state = "failed" if state == "failed" else "active"

    if is_convert_path:
        workflow_path = "convert_edition"
        workflow_summary = (
            "解析并导出 EPUB：确认源书章节目录后直接生成原文 EPUB，不进入术语或翻译审阅。"
        )
        workflow_step_order = [
            "import",
            "structure",
            "chapter_confirmation",
            "text_processing",
            "knowledge_handoff",
        ]
        text_processing_label = "导出 EPUB"
        text_processing_desc = "按已确认章节目录渲染原文 EPUB。"
        chapter_confirmation_desc = (
            "确认源书章节目录（标题与起止页）；导出将严格按此结构分章。"
        )
        text_processing_done = state in {"exporting", "completed"} or (
            state == "failed" and failed_stage == "exporting"
        )
        text_processing_failed = state == "failed" and failed_stage == "exporting"
    elif is_translation_path:
        workflow_path = "translation_edition"
        workflow_summary = (
            "译本路径：先定稿术语并机器翻译，再进入翻译审阅；源书章节目录可在审阅前后确认，"
            "仅用于知识拆分与 PDF 对照，不编辑译文，也不会自动重译。"
        )
        workflow_step_order = [
            "import",
            "structure",
            "glossary_finalization",
            "text_processing",
            "polish",
            "translation_review",
            "chapter_confirmation",
            "knowledge_handoff",
        ]
        text_processing_label = "机器翻译"
        text_processing_desc = "术语定稿后调用翻译模型，并生成机器预审与审阅工件。"
        chapter_confirmation_desc = (
            "确认源书章节目录（标题与起止页），作为知识拆分的权威边界。"
            "可与翻译审阅并行或在其后完成。"
        )
    else:
        workflow_path = "source_edition"
        workflow_summary = (
            "原文路径：保留源书文本，跳过翻译审阅，直接确认源书章节目录后进入知识解析。"
        )
        workflow_step_order = [
            "import",
            "structure",
            "text_processing",
            "chapter_confirmation",
            "knowledge_handoff",
        ]
        text_processing_label = "保留原文"
        text_processing_desc = "按处理模式保留源书正文，不调用翻译模型。"
        chapter_confirmation_desc = (
            "确认源书章节目录（标题与起止页），作为知识拆分的权威边界。"
        )

    steps: dict[str, dict[str, str]] = {
        "import": _step(
            "running" if state == "created" else "done",
            "导入",
            "文件已上传，正在启动后台解析。" if state == "created" else "文件已进入书籍处理任务。",
        ),
        "structure": _step(
            "done" if has_structure else ("failed" if state == "failed" else "running"),
            "结构解析",
            "生成章节、正文结构和后续处理所需的书籍模型。",
        ),
    }

    if is_translation_path:
        steps["glossary_finalization"] = _step(
            "done" if glossary_finalized else (
                "action_required" if state == "awaiting_glossary" else (
                    "running" if state in {"ingesting", "reconstructing"} else "blocked"
                )
            ),
            "术语定稿",
            "翻译前确定全书关键术语的中文译法，避免译后再改术语。",
        )
    steps["text_processing"] = _step(
        "done" if text_processing_done else (
            "failed" if text_processing_failed else (
                "running" if state in {"translating", "preserving", "exporting"} else (
                    "action_required"
                    if is_convert_path and chapters_confirmed and state == "awaiting_glossary"
                    else "blocked"
                )
            )
        ),
        text_processing_label,
        text_processing_desc,
    )
    if is_translation_path:
        if polish_finished:
            polish_desc = {
                "applied": "润色建议已应用，译文进入预审与人工审阅。",
                "no_candidates": "未发现需要润色的段落，直接进入预审与人工审阅。",
                "waived": "已明确跳过润色，直接进入预审与人工审阅。",
            }.get(polish_outcome or "", "润色阶段已完成。")
            polish_status = "done"
        elif polish_failed:
            polish_status = "failed"
            polish_desc = "润色阶段失败；可从失败检查点重试。"
        elif state == "polishing":
            polish_status = "running"
            polish_desc = "正在执行润色质量收敛。"
        elif text_processing_done:
            polish_status = "running"
            polish_desc = "机器翻译完成，正在准备润色与预审。"
        else:
            polish_status = "blocked"
            polish_desc = "等待机器翻译完成后执行润色。"
        steps["polish"] = _step(polish_status, "润色", polish_desc)

    if is_preserve_path or is_convert_path:
        steps["translation_review"] = _step(
            "skipped",
            "翻译审阅",
            "原文路径不需要翻译审阅。" if is_preserve_path else "解析导出路径不需要翻译审阅。",
        )
    elif is_translation_path and review_done:
        steps["translation_review"] = _step(
            "done",
            "翻译审阅",
            "人工审阅已完成；批准版译文可在审阅控制台导出。",
        )
    elif is_translation_path and review_ready:
        steps["translation_review"] = _step(
            "action_required",
            "翻译审阅",
            "在审阅控制台逐段检查或修改译文；与源书章节目录确认无关。",
        )
    else:
        steps["translation_review"] = _step(
            "blocked",
            "翻译审阅",
            "等待机器翻译与预审完成。",
        )

    if chapters_confirmed:
        chapter_status = "done"
    elif has_structure:
        chapter_status = "action_required"
    else:
        chapter_status = "blocked"
    steps["chapter_confirmation"] = _step(
        chapter_status,
        "源书章节目录",
        chapter_confirmation_desc,
    )

    knowledge_ready = chapters_confirmed and (
        is_preserve_path or review_done or (is_convert_path and text_processing_done)
    )
    steps["knowledge_handoff"] = _step(
        "ready" if knowledge_ready else "blocked",
        "知识解析入口",
        "文本版本与章节目录都确认后，交给 BookWeaver 知识拆分。",
    )

    if state == "failed":
        pipeline_status = "failed"
        resume_state = job.get("translation_resume") if isinstance(job.get("translation_resume"), dict) else None
        if resume_state is None and is_translation_path and failed_stage in {"translating", "preserving"}:
            job_id = job.get("job_id")
            if isinstance(job_id, str) and job_id:
                try:
                    enriched = get_job_service().get(job_id)
                    candidate = enriched.get("translation_resume")
                    if isinstance(candidate, dict):
                        resume_state = candidate
                except Exception:
                    resume_state = None
        if (
            is_translation_path
            and failed_stage in {"translating", "preserving"}
            and isinstance(resume_state, dict)
            and resume_state.get("available") is False
        ):
            reason = str(resume_state.get("reason") or "")
            detail = str(resume_state.get("detail") or "请先完成前置确认，再继续翻译。")
            if reason == "human_gate_required" and "章节" in detail:
                next_action = {"kind": "confirm_chapters", "label": detail, "href": f"/jobs/{job.get('job_id')}"}
            elif reason == "human_gate_required":
                next_action = {"kind": "finalize_glossary", "label": detail, "href": f"/jobs/{job.get('job_id')}"}
            else:
                next_action = {"kind": "view_progress", "label": detail, "href": f"/jobs/{job.get('job_id')}"}
        else:
            label = "重试润色" if failed_stage == "polishing" else "从检查点恢复"
            next_action = {"kind": "resume_job", "label": label, "href": f"/jobs/{job.get('job_id')}"}
    elif is_translation_path and glossary_ready_pending_translation and not chapters_confirmed:
        pipeline_status = "processing"
        next_action = {
            "kind": "confirm_chapters",
            "label": "确认章节后开始翻译",
            "href": f"/jobs/{job.get('job_id')}",
        }
    elif is_translation_path and glossary_ready_pending_translation:
        pipeline_status = "processing"
        next_action = {
            "kind": "start_translation",
            "label": "开始全文翻译",
            "href": f"/jobs/{job.get('job_id')}",
        }
    elif is_translation_path and state == "awaiting_glossary":
        pipeline_status = "processing"
        next_action = {"kind": "finalize_glossary", "label": "审定并定稿术语", "href": f"/jobs/{job.get('job_id')}"}
    elif state in {"translating", "preserving"}:
        pipeline_status = "processing"
        label = "正在翻译全文" if state == "translating" else "正在保留原文"
        next_action = {"kind": "view_progress", "label": label, "href": f"/jobs/{job.get('job_id')}"}
    elif state in {"validating", "pre_review"}:
        pipeline_status = "processing"
        next_action = {
            "kind": "view_progress",
            "label": "机器预审中",
            "href": f"/jobs/{job.get('job_id')}",
        }
    elif is_translation_path and review_ready and not review_done:
        pipeline_status = "needs_translation_review"
        next_action = {"kind": "review_translation", "label": "开始翻译审阅", "href": f"/jobs/{job.get('job_id')}"}
    elif steps["chapter_confirmation"]["status"] == "action_required":
        pipeline_status = "needs_chapter_confirmation"
        next_action = {"kind": "confirm_chapters", "label": "确认源书章节目录", "href": f"/jobs/{job.get('job_id')}"}
    elif knowledge_ready:
        pipeline_status = "ready_for_knowledge"
        next_action = {"kind": "start_knowledge", "label": "进入知识解析", "href": f"/jobs/{job.get('job_id')}"}
    else:
        pipeline_status = "processing"
        next_action = {"kind": "view_progress", "label": "查看处理进度", "href": f"/jobs/{job.get('job_id')}"}

    return {
        "book_id": job.get("job_id"),
        "title": source.get("filename") or job.get("job_id"),
        "source_filename": source.get("filename"),
        "job": job,
        "processing_mode": request.get("processing_mode"),
        "text_operation": text_operation,
        "workflow_path": workflow_path,
        "workflow_summary": workflow_summary,
        "workflow_step_order": workflow_step_order,
        "pipeline_status": pipeline_status,
        "pipeline_locked": _pipeline_locked(job),
        "job_state": state,
        "lifecycle_stage": lifecycle_stage,
        "lifecycle_state": lifecycle_state,
        "polish_outcome": polish_outcome,
        "steps": steps,
        "next_action": next_action,
        "knowledge_ready": knowledge_ready,
        "updated_at": job.get("updated_at"),
        "progress_percent": progress.get("overall_percent", 0),
    }


_WORKSPACE_STATUS_PRIORITY = {
    "ready_for_knowledge": 6,
    "needs_chapter_confirmation": 5,
    "needs_translation_review": 4,
    "processing": 2,
    "failed": 1,
}

_PIPELINE_IN_FLIGHT_STATES = frozenset(
    {
        "created",
        "ingesting",
        "reconstructing",
        "translating",
        "preserving",
        "validating",
        "pre_review",
    }
)


def _job_is_in_flight(job: dict[str, Any]) -> bool:
    state = str(job.get("state") or "")
    if state in _PIPELINE_IN_FLIGHT_STATES:
        return True
    if state == "failed":
        return job.get("failed_stage") in {"translating", "preserving", "validating", "pre_review"}
    return False


def _pipeline_locked(job: dict[str, Any]) -> bool:
    return _job_is_in_flight(job)


def _workspace_timestamp(book: dict[str, Any]) -> str:
    job = book.get("job") if isinstance(book.get("job"), dict) else {}
    return str(book.get("updated_at") or job.get("updated_at") or job.get("created_at") or "")


def _workspace_source_key(book: dict[str, Any]) -> str:
    job = book.get("job") if isinstance(book.get("job"), dict) else {}
    source = job.get("source") if isinstance(job.get("source"), dict) else {}
    return str(source.get("sha256") or book.get("source_filename") or book.get("title") or book.get("book_id"))


def _workspace_text_version_kind(book: dict[str, Any]) -> str:
    operation = book.get("text_operation") or book.get("processing_mode")
    if operation == "translate":
        return "translated"
    if operation == "preserve":
        return "source"
    return "pending"


def _prefer_workspace_book(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_job = left.get("job") if isinstance(left.get("job"), dict) else {}
    right_job = right.get("job") if isinstance(right.get("job"), dict) else {}
    left_in_flight = _job_is_in_flight(left_job)
    right_in_flight = _job_is_in_flight(right_job)
    if left_in_flight != right_in_flight:
        return left if left_in_flight else right
    if left_in_flight and right_in_flight:
        return left if _workspace_timestamp(left) >= _workspace_timestamp(right) else right
    left_priority = _WORKSPACE_STATUS_PRIORITY.get(str(left.get("pipeline_status")), 0)
    right_priority = _WORKSPACE_STATUS_PRIORITY.get(str(right.get("pipeline_status")), 0)
    if left_priority != right_priority:
        return left if left_priority > right_priority else right
    return left if _workspace_timestamp(left) >= _workspace_timestamp(right) else right


def _workspace_chapter_state(versions: list[dict[str, Any]]) -> dict[str, Any]:
    in_flight = next(
        (
            version
            for version in versions
            if _job_is_in_flight(version.get("job") if isinstance(version.get("job"), dict) else {})
        ),
        None,
    )
    if in_flight is not None:
        job = in_flight.get("job") if isinstance(in_flight.get("job"), dict) else {}
        state = str(job.get("state") or "")
        if state == "translating":
            return {
                "status": "processing",
                "label": "翻译进行中",
                "job_id": in_flight.get("book_id"),
                "updated_at": in_flight.get("updated_at"),
                "description": "当前译本仍在生成，源书章节目录可稍后确认。",
            }
        if state in {"created", "ingesting", "reconstructing"}:
            labels = {
                "ingesting": "解析中",
                "reconstructing": "结构重建中",
            }
            return {
                "status": "blocked",
                "label": labels.get(state, "处理进行中"),
                "job_id": in_flight.get("book_id"),
                "updated_at": in_flight.get("updated_at"),
                "description": "结构解析完成前暂不能确认源书章节目录。",
            }
    confirmed = next(
        (
            version
            for version in versions
            if version.get("steps", {}).get("chapter_confirmation", {}).get("status") == "done"
        ),
        None,
    )
    if confirmed is not None:
        return {
            "status": "confirmed",
            "label": "源书章节目录已确认",
            "job_id": confirmed.get("book_id"),
            "updated_at": confirmed.get("updated_at"),
            "description": "源书章节目录（标题与页码）已确认，作为知识拆分的权威边界。",
        }
    action_required = next(
        (
            version
            for version in versions
            if version.get("steps", {}).get("chapter_confirmation", {}).get("status") == "action_required"
        ),
        None,
    )
    if action_required is not None:
        return {
            "status": "needs_confirmation",
            "label": "需要确认源书章节目录",
            "job_id": action_required.get("book_id"),
            "updated_at": action_required.get("updated_at"),
            "description": "确认源书标题与页码边界；不编辑译文，确认后不会自动重译。",
        }
    return {
        "status": "blocked",
        "label": "等待文本版本就绪",
        "job_id": None,
        "updated_at": None,
        "description": "需要先完成解析、原文保留或翻译审阅。",
    }


def _source_workspace_books_from_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    workspace_books = [_workspace_book_from_job(job) for job in jobs]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for book in workspace_books:
        grouped.setdefault(_workspace_source_key(book), []).append(book)

    source_books: list[dict[str, Any]] = []
    for source_id, versions in grouped.items():
        visible_by_kind: dict[str, dict[str, Any]] = {}
        for version in versions:
            kind = _workspace_text_version_kind(version)
            current = visible_by_kind.get(kind)
            visible_by_kind[kind] = version if current is None else _prefer_workspace_book(current, version)

        visible_versions = []
        for kind, version in visible_by_kind.items():
            visible_versions.append(
                {
                    "kind": kind,
                    "job_id": version.get("book_id"),
                    "title": version.get("title"),
                    "source_filename": version.get("source_filename"),
                    "pipeline_status": version.get("pipeline_status"),
                    "job_state": (version.get("job") or {}).get("state") if isinstance(version.get("job"), dict) else None,
                    "pipeline_locked": bool(version.get("pipeline_locked")),
                    "status_label": version.get("next_action", {}).get("label"),
                    "text_operation": version.get("text_operation"),
                    "processing_mode": version.get("processing_mode"),
                    "knowledge_ready": version.get("knowledge_ready"),
                    "progress_percent": version.get("progress_percent"),
                    "updated_at": version.get("updated_at"),
                    "next_action": version.get("next_action"),
                    "steps": version.get("steps"),
                }
            )
        visible_versions.sort(
            key=lambda item: (
                0 if item["kind"] == "source" else 1 if item["kind"] == "translated" else 2,
                item.get("updated_at") or "",
            )
        )
        primary = _prefer_workspace_book(versions[0], versions[0])
        for version in versions[1:]:
            primary = _prefer_workspace_book(primary, version)
        source_job = primary.get("job") if isinstance(primary.get("job"), dict) else {}
        source = source_job.get("source") if isinstance(source_job.get("source"), dict) else {}
        task_history = sorted(
            [
                {
                    "job_id": version.get("book_id"),
                    "state": version.get("job", {}).get("state"),
                    "pipeline_status": version.get("pipeline_status"),
                    "text_operation": version.get("text_operation"),
                    "processing_mode": version.get("processing_mode"),
                    "updated_at": version.get("updated_at"),
                }
                for version in versions
            ],
            key=lambda item: str(item.get("updated_at") or ""),
            reverse=True,
        )
        source_books.append(
            {
                "source_id": source_id,
                "title": primary.get("title"),
                "source_filename": primary.get("source_filename"),
                "source_sha256": source.get("sha256"),
                "updated_at": primary.get("updated_at"),
                "chapter_structure": _workspace_chapter_state(versions),
                "text_versions": visible_versions,
                "task_history_count": len(task_history),
                "hidden_task_count": max(len(task_history) - len(visible_versions), 0),
                "task_history": task_history,
            }
        )

    source_books.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return source_books


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_book_name(value: str | None) -> str:
    if not value:
        return ""
    name = Path(value).name
    stem = name.rsplit(".", 1)[0]
    return re.sub(r"\s+", " ", stem).strip().casefold()


def _duplicate_matches_for_source(
    *,
    source_filename: str,
    source_sha256: str,
) -> list[DuplicateBookMatch]:
    matches: list[DuplicateBookMatch] = []
    filename_key = Path(source_filename).name.casefold()
    title_key = _normalize_book_name(source_filename)

    for job in get_job_service().list():
        source = job.get("source") if isinstance(job.get("source"), dict) else {}
        job_sha = str(source.get("sha256") or "")
        job_filename = str(source.get("filename") or "")
        reason: Literal["same_file", "same_filename", "same_title"] | None = None
        if job_sha and job_sha == source_sha256:
            reason = "same_file"
        elif job_filename and Path(job_filename).name.casefold() == filename_key:
            reason = "same_filename"
        elif _normalize_book_name(job_filename) and _normalize_book_name(job_filename) == title_key:
            reason = "same_title"
        if reason is None:
            continue
        workspace_book = _workspace_book_from_job(job)
        matches.append(
            DuplicateBookMatch(
                kind="workspace_job",
                id=str(job.get("job_id") or ""),
                title=str(workspace_book.get("title") or job_filename or job.get("job_id") or ""),
                status=str(workspace_book.get("pipeline_status") or job.get("state") or "unknown"),
                href=f"/jobs/{job.get('job_id')}",
                updated_at=job.get("updated_at") if isinstance(job.get("updated_at"), str) else None,
                reason=reason,
            )
        )

    review_projects = list_review_projects_sync()
    for project in review_projects:
        source_name = Path(project.source_path).name if project.source_path else ""
        reason = None
        if source_name and source_name.casefold() == filename_key:
            reason = "same_filename"
        elif _normalize_book_name(source_name or project.title) == title_key:
            reason = "same_title"
        if reason is None:
            continue
        matches.append(
            DuplicateBookMatch(
                kind="review_project",
                id=project.run_dir,
                title=project.title,
                status=project.review_status,
                href=f"/review?runDir={quote(project.run_dir, safe='')}",
                updated_at=project.updated_at,
                reason=reason,
            )
        )

    reason_priority = {"same_file": 0, "same_filename": 1, "same_title": 2}
    matches.sort(key=lambda item: (reason_priority[item.reason], item.updated_at or ""), reverse=False)
    return matches


@api_router.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    processing_mode: Literal["auto", "translate", "preserve", "convert"] = Form(default="preserve"),
    source_language: Optional[str] = Form(default=None),
    target_language: str = Form(default="zh-CN"),
    translator: Literal["openai", "mock", "minimax", "compatible", "openai-compatible"] = Form(
        default="minimax"
    ),
    output_format: Literal["pdf", "epub", "both"] = Form(default="epub"),
    allow_duplicate: bool = Form(default=False),
):
    filename = Path(file.filename or "").name
    suffix = Path(filename).suffix.lower()
    if suffix not in {".pdf", ".epub"}:
        raise HTTPException(status_code=400, detail="Only PDF and EPUB files are supported.")

    service = get_job_service()
    incoming_dir = service.jobs_dir / ".incoming" / uuid.uuid4().hex
    incoming_dir.mkdir(parents=True, exist_ok=True)
    incoming_path = incoming_dir / filename
    try:
        with incoming_path.open("wb") as handle:
            shutil.copyfileobj(file.file, handle)
        if incoming_path.stat().st_size > settings.MAX_UPLOAD_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File too large. Maximum size is {settings.MAX_UPLOAD_SIZE // (1024 * 1024)}MB",
            )
        source_sha256 = _sha256_file(incoming_path)
        duplicate_matches = _duplicate_matches_for_source(
            source_filename=filename,
            source_sha256=source_sha256,
        )
        if duplicate_matches and not allow_duplicate:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "这本书看起来已经进入过处理流程。请先继续现有项目，或明确选择创建新版。",
                    "source_filename": filename,
                    "source_sha256": source_sha256,
                    "matches": [match.dict() for match in duplicate_matches],
                },
            )
        effective_translator = "mock" if processing_mode in {"preserve", "convert"} else translator
        snapshot = service.create(
            source_path=incoming_path,
            processing_mode=processing_mode,
            source_language=source_language,
            target_language=target_language,
            translator=effective_translator,
            output_format=output_format,
            ingest_timeout_seconds=settings.BOOKMATE_INGEST_TIMEOUT_SECONDS,
        )
        background_tasks.add_task(_run_job_in_background, service, snapshot["job_id"])
        return snapshot
    except HTTPException:
        raise
    except JobServiceError as exc:
        logger.error("Failed to create job: %s", exc)
        raise HTTPException(status_code=503, detail="Book processing service is unavailable.") from exc
    finally:
        shutil.rmtree(incoming_dir, ignore_errors=True)
        file.file.close()


@api_router.post("/jobs/duplicates", response_model=DuplicateBookCheckResponse)
async def check_job_duplicates(file: UploadFile = File(...)):
    filename = Path(file.filename or "").name
    suffix = Path(filename).suffix.lower()
    if suffix not in {".pdf", ".epub"}:
        raise HTTPException(status_code=400, detail="Only PDF and EPUB files are supported.")

    service = get_job_service()
    incoming_dir = service.jobs_dir / ".incoming" / uuid.uuid4().hex
    incoming_dir.mkdir(parents=True, exist_ok=True)
    incoming_path = incoming_dir / filename
    try:
        with incoming_path.open("wb") as handle:
            shutil.copyfileobj(file.file, handle)
        if incoming_path.stat().st_size > settings.MAX_UPLOAD_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File too large. Maximum size is {settings.MAX_UPLOAD_SIZE // (1024 * 1024)}MB",
            )
        source_sha256 = _sha256_file(incoming_path)
        matches = _duplicate_matches_for_source(
            source_filename=filename,
            source_sha256=source_sha256,
        )
        return DuplicateBookCheckResponse(
            source_filename=filename,
            source_sha256=source_sha256,
            has_matches=bool(matches),
            matches=matches,
        )
    finally:
        shutil.rmtree(incoming_dir, ignore_errors=True)
        file.file.close()


@api_router.get("/jobs", response_model=JobListResponse)
async def list_jobs():
    service = get_job_service()
    jobs = service.list()
    return JobListResponse(total_jobs=len(jobs), jobs=jobs)


@api_router.get("/workspace/books", response_model=WorkspaceBooksResponse)
async def list_workspace_books():
    service = get_job_service()
    jobs = service.list()
    books = [_workspace_book_from_job(job) for job in jobs]
    source_books = _source_workspace_books_from_jobs(jobs)
    jobs_dir = getattr(service, "jobs_dir", Path(settings.BOOKMATE_JOBS_DIR))
    return WorkspaceBooksResponse(
        total_books=len(books),
        books=books,
        total_source_books=len(source_books),
        source_books=source_books,
        jobs_dir=str(jobs_dir.expanduser().resolve()),
    )


@api_router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    try:
        return get_job_service().get(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobServiceError as exc:
        raise HTTPException(status_code=500, detail="Invalid job data.") from exc


@api_router.delete("/jobs/{job_id}", status_code=status.HTTP_200_OK)
async def delete_job(job_id: str):
    try:
        get_job_service().delete(job_id)
        return {"status": "deleted", "job_id": job_id}
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api_router.post("/jobs/{job_id}/resume", status_code=status.HTTP_202_ACCEPTED)
async def resume_job(job_id: str, background_tasks: BackgroundTasks):
    service = get_job_service()
    try:
        snapshot = service.get(job_id)
        state = str(snapshot.get("state") or "")
        resume = service.translation_resume(
            snapshot,
            snapshot.get("translation_activity")
            if isinstance(snapshot.get("translation_activity"), dict)
            else None,
        )
        if isinstance(resume, dict) and resume.get("available") is False:
            detail = str(resume.get("detail") or "当前无需恢复翻译。")
            raise HTTPException(status_code=409, detail=detail)
        if state not in {"failed", "translating"}:
            state_labels = {
                "awaiting_human_review": "已进入人工审阅阶段",
                "completed": "已完成",
                "ingesting": "正在解析",
            }
            hint = state_labels.get(state, f"当前状态：{state}")
            raise HTTPException(
                status_code=409,
                detail=f"当前状态无法恢复。{hint}，请刷新页面查看最新进度。",
            )
        service.record_resume_request(job_id)
        background_tasks.add_task(_run_job_in_background, service, job_id, resume=True)
        return service.get(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api_router.post("/jobs/{job_id}/reprocess", status_code=status.HTTP_202_ACCEPTED)
async def reprocess_job(
    job_id: str,
    request: JobReprocessRequest,
    background_tasks: BackgroundTasks,
):
    service = get_job_service()
    try:
        snapshot = service.create_from_existing(
            job_id,
            processing_mode=request.processing_mode,
            source_language=request.source_language,
            target_language=request.target_language,
            translator=request.translator,
            output_format=request.output_format,
            ingest_timeout_seconds=settings.BOOKMATE_INGEST_TIMEOUT_SECONDS,
        )
        background_tasks.add_task(_run_job_in_background, service, snapshot["job_id"])
        return snapshot
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobServiceError as exc:
        logger.error("Failed to reprocess job %s: %s", job_id, exc)
        raise HTTPException(status_code=503, detail="Book processing service is unavailable.") from exc


@api_router.get("/jobs/{job_id}/chapters/draft", response_model=JobChapterDraftResponse)
async def get_job_chapter_draft(
    job_id: str,
    toc_page_start: Optional[int] = Query(default=None, ge=1),
    toc_page_end: Optional[int] = Query(default=None, ge=1),
    page_offset: Optional[int] = Query(default=None),
    toc_depth: Optional[int] = Query(default=None, ge=0, le=4),
    persist_prefs: bool = Query(default=False),
):
    try:
        service = get_job_service()
        if hasattr(service, "chapter_draft"):
            payload = service.chapter_draft(
                job_id,
                toc_page_start=toc_page_start,
                toc_page_end=toc_page_end,
                page_offset=page_offset,
                toc_depth=toc_depth,
                persist_prefs=persist_prefs,
            )
        else:
            payload = {"job_id": job_id, "chapters": service.draft_chapters(job_id)}
        return JobChapterDraftResponse(
            **payload
        )
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api_router.put("/jobs/{job_id}/chapters/draft-prefs", response_model=JobChapterDraftResponse)
async def update_job_chapter_draft_prefs(job_id: str, request: JobChapterDraftPrefsRequest):
    try:
        return JobChapterDraftResponse(
            **get_job_service().update_chapter_draft_prefs(
                job_id,
                toc_page_start=request.toc_page_start,
                toc_page_end=request.toc_page_end,
                page_offset=request.page_offset,
                toc_depth=request.toc_depth,
            )
        )
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api_router.post("/jobs/{job_id}/chapters/confirm")
async def confirm_job_chapters(
    job_id: str,
    request: JobChapterConfirmationRequest = JobChapterConfirmationRequest(),
):
    try:
        snapshot = get_job_service().confirm_chapters(job_id, chapters=request.chapters)
        return {
            "job": snapshot,
            "workspace_book": _workspace_book_from_job(snapshot),
        }
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api_router.get("/jobs/{job_id}/events")
async def get_job_events(job_id: str):
    try:
        events = get_job_service().events(job_id)
        return {"job_id": job_id, "events": events}
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobServiceError as exc:
        raise HTTPException(status_code=500, detail="Invalid job event history.") from exc


@api_router.get("/jobs/{job_id}/translation-events")
async def get_translation_events(
    job_id: str,
    since_offset: int = 0,
    limit: int = 500,
):
    """Tail the per-chunk translation events written by the translator.

    The frontend should poll this endpoint with ``since_offset`` instead
    of reading ``state`` (which may be stale when ``progress.json`` is
    throttled). Lines are JSON objects that match the schema
    ``translation_event_v1``; each line is one chunk attempt.
    """
    try:
        service = get_job_service()
        job_dir = service._job_dir(job_id)  # noqa: SLF001 (internal but stable)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobServiceError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    events_paths = sorted(
        job_dir.glob("artifacts/*/jobs/translation-events.jsonl"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    events_path = events_paths[0] if events_paths else job_dir / "jobs" / "translation-events.jsonl"
    if not events_path.exists():
        return {
            "job_id": job_id,
            "events": [],
            "offset": 0,
            "size": 0,
            "completed": False,
        }
    try:
        current_size = events_path.stat().st_size
    except OSError:
        raise HTTPException(status_code=503, detail="events file unreadable")
    if since_offset < 0 or since_offset > current_size:
        since_offset = 0
    consumed_offset = since_offset
    try:
        with events_path.open("rb") as fp:
            fp.seek(since_offset)
            events: list[dict] = []
            while since_offset <= current_size:
                raw_line = fp.readline()
                if not raw_line:
                    break
                consumed_offset += len(raw_line)
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
                if 0 < limit <= len(events):
                    break
    except OSError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    progress_path = events_path.parent / "progress.json"
    completed = False
    if progress_path.exists():
        try:
            payload = json.loads(progress_path.read_text(encoding="utf-8"))
            completed = str(payload.get("status") or "") in {"completed", "failed"}
        except (OSError, json.JSONDecodeError):
            pass
    return {
        "job_id": job_id,
        "events": events,
        "offset": consumed_offset,
        "size": current_size,
        "completed": completed,
    }


@api_router.get("/jobs/{job_id}/events/stream")
async def stream_job_events(job_id: str):
    """Server-Sent Events tail of the job's events.jsonl.

    Streams new events as ``data: <json>\n\n`` lines. Closes the
    connection when the job is no longer ``translating`` (the client
    can decide whether to re-open). The tail is computed by reading
    the last byte offset of the file on first read, so historical
    events are not replayed.
    """

    async def _event_source():
        try:
            service = get_job_service()
            job_dir = service._job_dir(job_id)  # internal but stable
        except JobNotFound as exc:
            yield f"event: error\ndata: {json.dumps({'detail': str(exc)})}\n\n"
            return
        except JobServiceError as exc:
            yield f"event: error\ndata: {json.dumps({'detail': str(exc)})}\n\n"
            return
        events_path = job_dir / "events.jsonl"
        if not events_path.exists():
            yield f"event: error\ndata: {json.dumps({'detail': 'no events file'})}\n\n"
            return
        offset = events_path.stat().st_size
        # initial hello so the client knows the stream is live
        yield f"event: hello\ndata: {json.dumps({'job_id': job_id, 'offset': offset})}\n\n"
        idle_ticks = 0
        while idle_ticks < 6:  # ~30s of no-activity then close
            await asyncio.sleep(5)
            try:
                cur_size = events_path.stat().st_size
            except FileNotFoundError:
                break
            if cur_size < offset:
                # file rotated/truncated; restart from the new size
                offset = 0
            if cur_size == offset:
                idle_ticks += 1
                # check job state: if not translating, close
                try:
                    snap = service.get(job_id)
                except Exception:
                    break
                if snap.get("state") not in {"translating", "failed"}:
                    yield f"event: closed\ndata: {json.dumps({'reason': 'state=' + str(snap.get('state'))})}\n\n"
                    return
                yield f"event: heartbeat\ndata: {json.dumps({'job_id': job_id, 'idle_ticks': idle_ticks})}\n\n"
                continue
            idle_ticks = 0
            with events_path.open("rb") as fp:
                fp.seek(offset)
                new_blob = fp.read(cur_size - offset).decode("utf-8", errors="replace")
            offset = cur_size
            for line in new_blob.splitlines():
                if not line.strip():
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    evt = {"raw": line}
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        yield f"event: closed\ndata: {json.dumps({'reason': 'idle_timeout'})}\n\n"

    from fastapi.responses import StreamingResponse

    return StreamingResponse(_event_source(), media_type="text/event-stream")


@api_router.get("/jobs/{job_id}/source")
async def get_job_source(job_id: str):
    try:
        path = get_job_service().source_path(job_id)
        return FileResponse(path=path, filename=path.name)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api_router.get("/jobs/{job_id}/source/info")
async def get_job_source_info(job_id: str):
    try:
        path = get_job_service().source_path(job_id)
        suffix = path.suffix.lower()
        size = path.stat().st_size
        kind = "pdf" if suffix == ".pdf" else ("epub" if suffix == ".epub" else "other")
        return {
            "job_id": job_id,
            "filename": path.name,
            "size": size,
            "kind": kind,
            "download_url": jobs_api_source_url(job_id),
        }
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def jobs_api_source_url(job_id: str) -> str:
    return f"/api/jobs/{job_id}/source"


@api_router.get("/jobs/{job_id}/epub/pages")
async def get_job_epub_pages(job_id: str):
    """按 <a id="page_N"/> 把 EPUB 拆成印刷页级索引。

    返回 [{index, page_number, chapter_title, chapter_href, page_anchor, page_url}]。
    """
    from epub_spine import resolve_epub_pages
    try:
        path = get_job_service().source_path(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if path.suffix.lower() != ".epub":
        raise HTTPException(status_code=400, detail="源文件不是 EPUB")
    try:
        pages = resolve_epub_pages(path)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"EPUB 解析失败：{exc}") from exc
    return {
        "job_id": job_id,
        "total": len(pages),
        "pages": [
            {
                "index": page.index,
                "page_number": page.page_number,
                "page_label": page.page_label,
                "chapter_title": page.chapter_title,
                "chapter_href": page.chapter_href,
                "page_anchor": page.page_anchor,
                "page_url": f"/api/jobs/{job_id}/epub/page-render?chapter={encode_uri(page.chapter_href)}&anchor={encode_uri(page.page_anchor)}",
            }
            for page in pages
        ],
    }


def encode_uri(value: str) -> str:
    from urllib.parse import quote
    return quote(value, safe="")


@api_router.get("/jobs/{job_id}/epub/spine")
async def get_job_epub_spine(job_id: str):
    """Resolve EPUB spine without streaming the whole file to the client.

    Returns a JSON list of {index, href, title, page_url}. Page content is
    fetched lazily via /epub/page/{n}.
    """
    from epub_spine import resolve_epub_spine  # local import keeps startup fast
    try:
        path = get_job_service().source_path(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if path.suffix.lower() != ".epub":
        raise HTTPException(status_code=400, detail="源文件不是 EPUB")
    try:
        spine = resolve_epub_spine(path)
    except Exception as exc:  # noqa: BLE001 - surface to client
        raise HTTPException(status_code=500, detail=f"EPUB 解析失败：{exc}") from exc
    return {
        "job_id": job_id,
        "spine": [
            {
                "index": entry.index,
                "href": entry.href,
                "title": entry.title,
                "page_url": f"/api/jobs/{job_id}/epub/page/{entry.index}",
            }
            for entry in spine
        ],
    }


@api_router.get("/jobs/{job_id}/epub/page-render")
async def get_job_epub_page_render(job_id: str, chapter: str, anchor: str):
    """按 (chapter_href, page_anchor) 渲染一页 xhtml 片段。"""
    from epub_spine import render_epub_page_by_anchor
    from urllib.parse import unquote
    chapter_decoded = unquote(chapter)
    anchor_decoded = unquote(anchor)
    try:
        path = get_job_service().source_path(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if path.suffix.lower() != ".epub":
        raise HTTPException(status_code=400, detail="源文件不是 EPUB")
    try:
        html = render_epub_page_by_anchor(path, chapter_decoded, anchor_decoded, job_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"EPUB 页面渲染失败：{exc}") from exc
    return HTMLResponse(content=html, media_type="text/html; charset=utf-8")


@api_router.get("/jobs/{job_id}/epub/asset")
async def get_job_epub_asset(job_id: str, path: str):
    """返回 EPUB 内部相对资源（图片、字体等）。用于内联 HTML 内的 <img> 重写目标。"""
    import mimetypes
    import zipfile
    from urllib.parse import unquote
    asset_path = unquote(path)
    try:
        source = get_job_service().source_path(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if source.suffix.lower() != ".epub":
        raise HTTPException(status_code=400, detail="源文件不是 EPUB")
    # 路径安全：必须落在 EPUB 内
    if asset_path.startswith("/") or ".." in asset_path.split("/"):
        raise HTTPException(status_code=400, detail="非法资源路径")
    try:
        with zipfile.ZipFile(source) as zf:
            data = zf.read(asset_path)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"资源不存在：{asset_path}") from None
    media_type, _ = mimetypes.guess_type(asset_path)
    if not media_type:
        media_type = "application/octet-stream"
    return Response(content=data, media_type=media_type)


@api_router.get("/jobs/{job_id}/epub/page/{page_index}")
async def get_job_epub_page(job_id: str, page_index: int):
    """Return a single spine page (1-based) as text/html."""
    from epub_spine import resolve_epub_spine, render_epub_page
    try:
        path = get_job_service().source_path(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if path.suffix.lower() != ".epub":
        raise HTTPException(status_code=400, detail="源文件不是 EPUB")
    if page_index < 1:
        raise HTTPException(status_code=400, detail="page_index 必须 ≥ 1")
    try:
        spine = resolve_epub_spine(path)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"EPUB 解析失败：{exc}") from exc
    if page_index > len(spine):
        raise HTTPException(status_code=404, detail="超出 spine 长度")
    try:
        html = render_epub_page(path, spine[page_index - 1])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"EPUB 页面渲染失败：{exc}") from exc
    return HTMLResponse(content=html, media_type="text/html; charset=utf-8")


@api_router.get("/jobs/{job_id}/glossary")
async def get_job_glossary(job_id: str):
    try:
        return get_job_service().glossary(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class GlossaryExcludeRequest(BaseModel):
    source: str = Field(..., min_length=1, description="英文源术语")
    action: Literal["exclude", "restore"] = "exclude"


@api_router.post("/jobs/{job_id}/glossary/exclude")
async def exclude_glossary_term(job_id: str, request: GlossaryExcludeRequest):
    try:
        service = get_job_service()
        return await asyncio.to_thread(
            service.glossary_exclude,
            job_id,
            source=request.source,
            action=request.action,
        )
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api_router.post("/jobs/{job_id}/glossary/apply")
async def apply_job_glossary(job_id: str, request: GlossaryApplyRequest):
    try:
        service = get_job_service()
        return await asyncio.to_thread(
            service.glossary_apply,
            job_id,
            source=request.source,
            target=request.target,
            term_type=request.term_type,
            status=request.status,
        )
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api_router.post("/jobs/{job_id}/glossary/reset-review")
async def reset_job_glossary_review(job_id: str):
    try:
        service = get_job_service()
        return await asyncio.to_thread(service.glossary_reset_review, job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api_router.post("/jobs/{job_id}/glossary/clear-suggestions")
async def clear_job_glossary_suggestions(job_id: str):
    try:
        service = get_job_service()
        return await asyncio.to_thread(service.glossary_clear_suggestions, job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api_router.post("/jobs/{job_id}/glossary/ready")
async def mark_job_glossary_ready(job_id: str):
    try:
        service = get_job_service()
        return await asyncio.to_thread(service.glossary_ready, job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api_router.get("/jobs/{job_id}/glossary/profile")
async def get_job_glossary_profile(job_id: str):
    try:
        return get_job_service().glossary_profile(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api_router.put("/jobs/{job_id}/glossary/profile")
async def set_job_glossary_profile(job_id: str, request: GlossaryProfileRequest):
    try:
        return get_job_service().glossary_set_profile(job_id, request.profile)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api_router.post("/jobs/{job_id}/glossary/reextract")
async def reextract_job_glossary(job_id: str):
    try:
        service = get_job_service()
        return await asyncio.to_thread(service.glossary_reextract, job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api_router.post("/jobs/{job_id}/glossary/suggest", status_code=status.HTTP_202_ACCEPTED)
async def suggest_job_glossary(
    job_id: str,
    background_tasks: BackgroundTasks,
    request: GlossarySuggestRequest | None = None,
):
    try:
        body = request or GlossarySuggestRequest()
        service = get_job_service()
        started = await asyncio.to_thread(
            service.glossary_suggest_async,
            job_id,
            target_lang=body.target_lang,
            translator=body.translator,
        )
        background_tasks.add_task(
            _run_glossary_suggest_in_background,
            service,
            job_id,
            target_lang=body.target_lang,
            translator=body.translator,
        )
        glossary = await asyncio.to_thread(service.glossary, job_id)
        return {
            "status": "started",
            "suggest_status": started,
            "glossary": glossary,
        }
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api_router.post("/jobs/{job_id}/translate", status_code=status.HTTP_202_ACCEPTED)
async def start_job_translation(job_id: str, background_tasks: BackgroundTasks):
    try:
        service = get_job_service()
        snapshot = await asyncio.to_thread(service.start_translation, job_id)
        background_tasks.add_task(_run_translate_in_background, service, job_id)
        return {"job": snapshot, "workspace_book": _workspace_book_from_job(snapshot)}
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api_router.post("/jobs/{job_id}/export", status_code=status.HTTP_202_ACCEPTED)
async def start_job_export(job_id: str, background_tasks: BackgroundTasks):
    try:
        service = get_job_service()
        snapshot = await asyncio.to_thread(service.start_export, job_id)
        background_tasks.add_task(_run_export_in_background, service, job_id)
        return {"job": snapshot, "workspace_book": _workspace_book_from_job(snapshot)}
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api_router.get("/jobs/{job_id}/artifacts/{artifact_name}")
async def get_job_artifact(job_id: str, artifact_name: str):
    try:
        path = get_job_service().artifact_path(job_id, artifact_name)
        return FileResponse(path=path, filename=path.name)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api_router.get("/jobs/{job_id}/review-link")
async def get_job_review_link(job_id: str):
    try:
        run_dir = get_job_service().review_run_dir(job_id)
        return {"job_id": job_id, "run_dir": str(run_dir)}
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api_router.get("/health", response_model=HealthResponse)
async def health_check():
    """
    Health check endpoint
    
    Returns service status, version, available features, and storage statistics
    """
    try:
        storage = await get_storage()
        stats = await storage.get_stats()
        
        return HealthResponse(
            status="healthy",
            version=settings.APP_VERSION,
            features={
                "pdf_parsing": True,
                "toc_extraction": True,
                "chapter_extraction": True,
                "persistent_storage": True,
                "ai_overview": True,
                "chapter_summary": True,
                "translation_review": True,
                "reading_progress": True,
                "chapter_marks": True
            },
            storage=stats
        )
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return HealthResponse(
            status="degraded",
            version=settings.APP_VERSION,
            features={
                "pdf_parsing": True,
                "toc_extraction": True,
                "chapter_extraction": True,
                "persistent_storage": True,
                "ai_overview": True,
                "chapter_summary": True,
                "translation_review": True,
                "reading_progress": True,
                "chapter_marks": True
            },
            storage={"error": str(e)}
        )


@api_router.post("/upload", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_pdf(file: UploadFile = File(..., description="PDF file to upload and parse")):
    """
    Upload and parse a PDF file
    
    - Extracts the Table of Contents using PyMuPDF
    - Parses text by chapters with page numbers
    - Saves metadata to persistent JSON storage
    - Returns book metadata and chapter count
    
    Supported file type: PDF only
    Max file size: 50MB
    """
    # Validate file type
    if not file.filename or not file.filename.lower().endswith('.pdf'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF files are supported"
        )
    
    # Generate unique book ID
    book_id = str(uuid.uuid4())
    
    # Create safe filename
    safe_filename = f"{book_id}.pdf"
    file_path = os.path.join(settings.UPLOAD_DIR, safe_filename)
    
    logger.info(f"Uploading file: {file.filename} (book_id: {book_id})")
    
    try:
        # Save uploaded file
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Check file size
        file_size = os.path.getsize(file_path)
        if file_size > settings.MAX_UPLOAD_SIZE:
            os.remove(file_path)
            logger.warning(f"File too large: {file.filename} ({file_size} bytes)")
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File too large. Maximum size is {settings.MAX_UPLOAD_SIZE // (1024*1024)}MB"
            )
        
        # Parse PDF
        parser = get_parser()
        book_data = parser.parse_pdf(file_path, book_id=book_id)
        book_data.filename = file.filename  # Store original filename
        
        # Save to persistent storage
        storage = await get_storage()
        await storage.save_book(book_data)
        
        logger.info(f"Upload successful: {book_id} - {book_data.title} ({book_data.total_chapters} chapters, {book_data.total_pages} pages)")
        
        return UploadResponse(
            book_id=book_id,
            filename=file.filename,
            title=book_data.title,
            total_chapters=book_data.total_chapters,
            total_pages=book_data.total_pages,
            message="PDF uploaded and parsed successfully"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        # Cleanup on error
        if os.path.exists(file_path):
            os.remove(file_path)
        logger.error(f"Upload failed for {file.filename}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to parse PDF: {str(e)}"
        )
    finally:
        file.file.close()


@api_router.get("/books", response_model=BookListResponse)
async def list_books():
    """
    List all uploaded books
    
    Returns book metadata without chapter content (use /books/{id}/chapters for full data)
    """
    try:
        storage = await get_storage()
        books = await storage.list_books()
        
        return BookListResponse(
            total_books=len(books),
            books=books
        )
    except Exception as e:
        logger.error(f"Failed to list books: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve book list: {str(e)}"
        )


# ==================== Book Detail Endpoint ====================

class BookDetailResponse(BaseModel):
    """Book detail response model"""
    book_id: str
    title: str
    filename: str
    total_chapters: int
    total_pages: int
    created_at: str
    updated_at: str


@api_router.get("/books/{book_id}", response_model=BookDetailResponse)
async def get_book_detail(book_id: str):
    """
    Get book details by ID
    
    Returns basic book metadata without chapter content
    For full chapter data, use /books/{id}/chapters
    """
    try:
        storage = await get_storage()
        book = await storage.get_book(book_id)
        
        if not book:
            logger.warning(f"Book not found: {book_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Book with ID '{book_id}' not found"
            )
        
        return BookDetailResponse(
            book_id=book.metadata.book_id,
            title=book.metadata.title,
            filename=book.metadata.filename,
            total_chapters=book.metadata.total_chapters,
            total_pages=book.metadata.total_pages,
            created_at=book.metadata.created_at.isoformat() if book.metadata.created_at else "",
            updated_at=book.metadata.updated_at.isoformat() if book.metadata.updated_at else ""
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get book detail for {book_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve book detail: {str(e)}"
        )


@api_router.get("/books/{book_id}/chapters", response_model=BookChaptersResponse)
async def get_book_chapters(book_id: str):
    """
    Get all chapters for a specific book with page navigation
    
    - Returns complete book data including all chapters
    - Each chapter contains: index, title, content, page_number, end_page
    
    Path parameter:
    - book_id: The unique identifier returned from /upload
    """
    try:
        storage = await get_storage()
        book = await storage.get_book(book_id)
        
        if not book:
            logger.warning(f"Book not found: {book_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Book with ID '{book_id}' not found"
            )
        
        page_offset = getattr(book.metadata, 'page_offset', 0)
        return BookChaptersResponse(
            book_id=book.metadata.book_id,
            title=book.metadata.title,
            total_chapters=book.metadata.total_chapters,
            total_pages=book.metadata.total_pages,
            chapters=[chapter_to_response(ch, page_offset) for ch in book.chapters]
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get chapters for {book_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve book chapters: {str(e)}"
        )


@api_router.get("/books/{book_id}/chapters/{chapter_index}", response_model=ChapterResponse)
async def get_single_chapter(book_id: str, chapter_index: int):
    """
    Get a specific chapter by index with page navigation

    Path parameters:
    - book_id: The unique book identifier
    - chapter_index: The chapter index (0-based, matching the index in chapters list)
    """
    logger.info(f"Getting single chapter: book_id={book_id}, chapter_index={chapter_index}")

    try:
        storage = await get_storage()
        book = await storage.get_book(book_id)

        if not book:
            logger.warning(f"Book not found: {book_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Book with ID '{book_id}' not found"
            )

        logger.debug(f"Book found: {book_id}, total_chapters={len(book.chapters)}, available_indices={[ch.index for ch in book.chapters]}")

        # Find chapter by index (0-based)
        chapter = next(
            (ch for ch in book.chapters if ch.index == chapter_index),
            None
        )

        if not chapter:
            logger.warning(f"Chapter {chapter_index} not found in book {book_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Chapter {chapter_index} not found in book '{book_id}'"
            )

        page_offset = getattr(book.metadata, 'page_offset', 0)
        logger.info(f"Returning chapter: index={chapter.index}, title={chapter.title}")
        return chapter_to_response(chapter, page_offset)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get chapter {chapter_index} for {book_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve chapter: {str(e)}"
        )


@api_router.get("/books/{book_id}/pdf")
async def get_book_pdf(book_id: str):
    """
    Get the original PDF file for a book
    
    Path parameter:
    - book_id: The unique book identifier
    """
    try:
        file_path = os.path.join(settings.UPLOAD_DIR, f"{book_id}.pdf")
        
        if not os.path.exists(file_path):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"PDF file for book '{book_id}' not found"
            )
        
        return FileResponse(
            path=file_path,
            media_type="application/pdf",
            filename=f"{book_id}.pdf"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get PDF for {book_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve PDF: {str(e)}"
        )


@api_router.delete("/books/{book_id}", status_code=status.HTTP_200_OK)
async def delete_book(book_id: str):
    """
    Delete a book and its associated data
    
    Deletes both:
    - JSON metadata file
    - PDF file
    - Reading progress
    
    Path parameter:
    - book_id: The unique book identifier to delete
    """
    try:
        storage = await get_storage()
        
        # Check if book exists
        exists = await storage.book_exists(book_id)
        if not exists:
            logger.warning(f"Attempted to delete non-existent book: {book_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Book with ID '{book_id}' not found"
            )
        
        # Delete from storage (removes both JSON and PDF)
        delete_result = await storage.delete_book(book_id)
        
        if not delete_result["success"]:
            # 删除失败，返回具体的错误信息
            error_msg = delete_result.get("error", "Unknown error during deletion")
            logger.error(f"Failed to delete book {book_id}: {error_msg}")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=error_msg
            )
        
        # Also delete reading progress
        try:
            progress_storage = await get_progress_storage()
            await progress_storage.delete_progress(book_id)
        except Exception as e:
            # 阅读进度删除失败不影响主流程
            logger.warning(f"Failed to delete progress for {book_id}: {e}")
        
        logger.info(f"Book deleted: {book_id}")
        
        return DeleteResponse(
            message="Book deleted successfully",
            book_id=book_id
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete book {book_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete book: {str(e)}"
        )


# ==================== Phase 1: AI Overview Endpoints ====================

@api_router.post("/books/{book_id}/overview", response_model=BookOverviewResponse)
async def generate_book_overview(book_id: str, request: GenerateOverviewRequest):
    """
    生成或获取书籍的 AI 概览
    
    - 返回书籍简介、关键论点列表、阅读建议
    - 结果会被缓存，避免重复生成
    - 设置 force_regenerate=true 可强制重新生成
    
    Path parameter:
    - book_id: 书籍唯一标识
    """
    try:
        # 获取书籍数据
        storage = await get_storage()
        book = await storage.get_book(book_id)
        
        if not book:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Book with ID '{book_id}' not found"
            )
        
        # 调用 AI 服务生成概览
        ai_service = await get_ai_service()
        
        chapters_data = [
            {"title": ch.title, "content": ch.content}
            for ch in book.chapters
        ]
        
        overview = await ai_service.generate_book_overview(
            book_id=book_id,
            title=book.metadata.title,
            chapters=chapters_data
        )
        
        return BookOverviewResponse(
            book_id=overview.book_id,
            introduction=overview.introduction,
            key_arguments=overview.key_arguments,
            reading_suggestions=overview.reading_suggestions,
            generated_at=overview.generated_at,
            model=overview.model,
            cached=False
        )
        
    except HTTPException:
        raise
    except AIBackendUnavailable as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
    except AIOutputError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to generate overview for {book_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate overview: {str(e)}"
        )


@api_router.get("/books/{book_id}/overview", response_model=BookOverviewResponse)
async def get_book_overview(book_id: str):
    """
    获取书籍的 AI 概览（如果已生成）
    
    如果概览未生成，返回 404
    """
    try:
        # 获取书籍数据
        storage = await get_storage()
        book = await storage.get_book(book_id)
        
        if not book:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Book with ID '{book_id}' not found"
            )
        
        # 检查缓存
        ai_service = await get_ai_service()
        chapters_data = [
            {"title": ch.title, "content": ch.content}
            for ch in book.chapters
        ]
        
        cache_content = f"{book_id}|{book.metadata.title}|{len(chapters_data)}"
        cached = await ai_service.cache.get("overview", cache_content)
        
        if not cached:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Overview not found for book '{book_id}'. Use POST to generate."
            )
        
        overview = BookOverview(**cached)
        
        return BookOverviewResponse(
            book_id=overview.book_id,
            introduction=overview.introduction,
            key_arguments=overview.key_arguments,
            reading_suggestions=overview.reading_suggestions,
            generated_at=overview.generated_at,
            model=overview.model,
            cached=True
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get overview for {book_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get overview: {str(e)}"
        )


# ==================== Phase 1: Chapter Summary Endpoints ====================

@api_router.post("/books/{book_id}/chapters/{chapter_index}/summary", response_model=ChapterSummaryResponse)
async def generate_chapter_summary(book_id: str, chapter_index: int, request: GenerateSummaryRequest):
    """
    生成或获取章节摘要

    - 返回章节的中文摘要
    - 结果会被缓存，避免重复生成

    Path parameters:
    - book_id: 书籍唯一标识
    - chapter_index: 章节索引 (0-based，与 chapters 列表中的 index 一致)
    """
    logger.info(f"Generating chapter summary: book_id={book_id}, chapter_index={chapter_index}")

    try:
        # 获取书籍数据
        storage = await get_storage()
        book = await storage.get_book(book_id)

        if not book:
            logger.warning(f"Book not found: {book_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Book with ID '{book_id}' not found"
            )

        logger.info(f"Book found: {book_id}, total_chapters={len(book.chapters)}")

        # 查找章节 - 支持 0-based 索引
        chapter = next(
            (ch for ch in book.chapters if ch.index == chapter_index),
            None
        )

        if not chapter:
            logger.warning(f"Chapter {chapter_index} not found in book {book_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Chapter {chapter_index} not found"
            )

        logger.info(f"Chapter found: index={chapter.index}, title={chapter.title}, content_length={len(chapter.content)}")

        # 调用 AI 服务生成摘要
        ai_service = await get_ai_service()

        summary = await ai_service.generate_chapter_summary(
            book_id=book_id,
            chapter_index=chapter_index,
            chapter_title=chapter.title,
            chapter_content=chapter.content
        )

        logger.info(f"Summary generated successfully for chapter {chapter_index}")

        return ChapterSummaryResponse(
            book_id=summary.book_id,
            chapter_index=summary.chapter_index,
            chapter_title=chapter.title,
            summary=summary.summary,
            generated_at=summary.generated_at,
            model=summary.model,
            cached=False
        )

    except HTTPException:
        raise
    except AIBackendUnavailable as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
    except AIOutputError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to generate summary for chapter {chapter_index}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate summary: {str(e)}"
        )


@api_router.get("/books/{book_id}/chapters/{chapter_index}/summary", response_model=ChapterSummaryResponse)
async def get_chapter_summary(book_id: str, chapter_index: int):
    """
    获取章节摘要（如果已生成）

    如果摘要未生成，返回 404

    Path parameters:
    - book_id: 书籍唯一标识
    - chapter_index: 章节索引 (0-based，与 chapters 列表中的 index 一致)
    """
    logger.info(f"Getting chapter summary: book_id={book_id}, chapter_index={chapter_index}")

    try:
        # 获取书籍数据
        storage = await get_storage()
        book = await storage.get_book(book_id)

        if not book:
            logger.warning(f"Book not found: {book_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Book with ID '{book_id}' not found"
            )

        logger.info(f"Book found: {book_id}, total_chapters={len(book.chapters)}")
        logger.debug(f"Available chapter indices: {[ch.index for ch in book.chapters]}")

        # 查找章节 - 支持 0-based 索引
        chapter = next(
            (ch for ch in book.chapters if ch.index == chapter_index),
            None
        )

        if not chapter:
            logger.warning(f"Chapter {chapter_index} not found in book {book_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Chapter {chapter_index} not found"
            )

        logger.info(f"Chapter found: index={chapter.index}, title={chapter.title}")

        # 检查缓存 - 使用与生成时相同的缓存键逻辑
        ai_service = await get_ai_service()
        cache_content = f"{book_id}|{chapter_index}|{chapter.title}|{chapter.content[:200]}"
        cached = await ai_service.cache.get("summary", cache_content)

        if not cached:
            logger.info(f"Summary cache miss for chapter {chapter_index}, book {book_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Summary not found for chapter {chapter_index}. Use POST to generate."
            )

        logger.info(f"Summary cache hit for chapter {chapter_index}")
        summary = ChapterSummary(**cached)

        return ChapterSummaryResponse(
            book_id=summary.book_id,
            chapter_index=summary.chapter_index,
            chapter_title=chapter.title,
            summary=summary.summary,
            generated_at=summary.generated_at,
            model=summary.model,
            cached=True
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get summary for chapter {chapter_index}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get summary: {str(e)}"
        )


# ==================== Phase 1: Reading Progress Endpoints ====================

@api_router.post("/books/{book_id}/progress", response_model=ProgressResponse)
async def save_reading_progress(book_id: str, request: SaveProgressRequest):
    """
    保存阅读进度
    
    Path parameter:
    - book_id: 书籍唯一标识
    
    Request body:
    - page_number: 当前页码 (>=1)
    - chapter_index: 当前章节索引（可选）
    """
    try:
        # 验证书籍存在
        storage = await get_storage()
        book = await storage.get_book(book_id)
        
        if not book:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Book with ID '{book_id}' not found"
            )
        
        # 验证页码范围
        if request.page_number > book.metadata.total_pages:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Page number exceeds total pages ({book.metadata.total_pages})"
            )
        
        # 保存进度
        progress_storage = await get_progress_storage()
        progress = await progress_storage.update_progress(
            book_id=book_id,
            page_number=request.page_number,
            chapter_index=request.chapter_index,
            total_pages=book.metadata.total_pages
        )
        
        return ProgressResponse(
            book_id=progress.book_id,
            page_number=progress.page_number,
            chapter_index=progress.chapter_index,
            last_read=progress.last_read,
            reading_percentage=progress.reading_percentage
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to save progress for {book_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save progress: {str(e)}"
        )


@api_router.get("/books/{book_id}/progress", response_model=ProgressResponse)
async def get_reading_progress(book_id: str):
    """
    获取阅读进度
    
    如果从未保存过进度，返回 page_number=1
    
    Path parameter:
    - book_id: 书籍唯一标识
    """
    try:
        # 验证书籍存在
        storage = await get_storage()
        book = await storage.get_book(book_id)
        
        if not book:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Book with ID '{book_id}' not found"
            )
        
        # 获取进度
        progress_storage = await get_progress_storage()
        progress = await progress_storage.get_progress(book_id)
        
        if not progress:
            # 返回默认进度
            return ProgressResponse(
                book_id=book_id,
                page_number=1,
                chapter_index=None,
                last_read=datetime.utcnow().isoformat(),
                reading_percentage=0.0
            )
        
        return ProgressResponse(
            book_id=progress.book_id,
            page_number=progress.page_number,
            chapter_index=progress.chapter_index,
            last_read=progress.last_read,
            reading_percentage=progress.reading_percentage
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get progress for {book_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get progress: {str(e)}"
        )


# ==================== Phase 2: Chapter Mark Endpoints ====================

@api_router.post("/books/{book_id}/chapters/mark", response_model=CreateMarkResponse)
async def create_chapter_mark(book_id: str, request: ChapterMarkRequest):
    """
    创建用户章节标记

    - 在指定页面和位置创建章节标记
    - 自动触发重新分段，用户标记作为硬边界
    - 返回新创建的标记和重新分段后的章节列表

    Path parameter:
    - book_id: 书籍唯一标识

    Request body:
    - page_number: 页码 (1-based, >=1)
    - y_position: 页面垂直位置 (0.0-1.0 归一化)
    - chapter_name: 可选的章节名称
    """
    try:
        # 验证书籍存在
        storage = await get_storage()
        book = await storage.get_book(book_id)
        if not book:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Book with ID '{book_id}' not found"
            )

        # 验证页码范围
        if request.page_number > book.metadata.total_pages:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Page number exceeds total pages ({book.metadata.total_pages})"
            )

        # 创建标记并触发重新分段
        mark_service = await get_chapter_mark_service()
        mark, new_chapters = await mark_service.create_mark(
            book_id=book_id,
            page_number=request.page_number,
            y_position=request.y_position,
            chapter_name=request.chapter_name
        )

        if not mark or not new_chapters:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create chapter mark"
            )

        page_offset = getattr(book.metadata, 'page_offset', 0)
        return CreateMarkResponse(
            book_id=book_id,
            mark=ChapterMarkResponse(
                mark_id=mark.mark_id,
                page_number=mark.page_number,
                y_position=mark.y_position,
                chapter_name=mark.chapter_name,
                created_at=mark.created_at.isoformat()
            ),
            chapters=[chapter_to_response(ch, page_offset) for ch in new_chapters],
            message="Chapter mark created successfully, chapters recalculated"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create chapter mark for {book_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create chapter mark: {str(e)}"
        )


@api_router.delete("/books/{book_id}/marks/{mark_id}", response_model=DeleteMarkResponse)
async def delete_chapter_mark(book_id: str, mark_id: str):
    """
    删除用户章节标记

    - 删除指定的章节标记（如果是用户标记）
    - 自动触发重新分段
    - 返回删除后的章节列表

    Path parameters:
    - book_id: 书籍唯一标识
    - mark_id: 标记ID（对应用户标记的 mark_id）
    """
    try:
        # 验证书籍存在
        storage = await get_storage()
        book = await storage.get_book(book_id)
        if not book:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Book with ID '{book_id}' not found"
            )

        # 验证标记是否存在
        mark = next((m for m in book.user_marks if m.mark_id == mark_id), None)
        if not mark:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Mark with ID '{mark_id}' not found"
            )

        # 删除标记并触发重新分段
        mark_service = await get_chapter_mark_service()
        success, new_chapters = await mark_service.delete_mark(book_id, mark_id)

        if not success or not new_chapters:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete chapter mark"
            )

        page_offset = getattr(book.metadata, 'page_offset', 0)
        return DeleteMarkResponse(
            book_id=book_id,
            deleted_mark_id=mark_id,
            chapters=[chapter_to_response(ch, page_offset) for ch in new_chapters],
            message="Chapter mark deleted successfully, chapters recalculated"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete chapter mark {mark_id} from {book_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete chapter mark: {str(e)}"
        )


@api_router.get("/books/{book_id}/marks")
async def get_book_marks(book_id: str):
    """
    获取书籍的所有用户标记

    Path parameter:
    - book_id: 书籍唯一标识
    """
    try:
        storage = await get_storage()
        book = await storage.get_book(book_id)

        if not book:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Book with ID '{book_id}' not found"
            )

        marks = [
            {
                "mark_id": mark.mark_id,
                "page_number": mark.page_number,
                "y_position": mark.y_position,
                "chapter_name": mark.chapter_name,
                "created_at": mark.created_at.isoformat()
            }
            for mark in book.user_marks
        ]

        return {
            "book_id": book_id,
            "total_marks": len(marks),
            "marks": marks
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get marks for {book_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get marks: {str(e)}"
        )


@api_router.post("/books/{book_id}/calibrate", response_model=PageCalibrationResponse)
async def calibrate_page_numbers(book_id: str, request: PageCalibrationRequest):
    """
    校准页码偏移

    支持两种方式：
    1. 直接设置偏移量：提供 page_offset 参数
    2. 通过页码计算：提供 pdf_page 和 actual_page 参数

    偏移量 = PDF页码 - 实际页码
    例如：PDF显示第10页，但书籍实际页码是第1页，则偏移量为 9。

    Path parameter:
    - book_id: 书籍唯一标识

    Request body (方式1 - 直接设置):
    - page_offset: 直接设置页码偏移量（优先级最高）

    Request body (方式2 - 通过页码计算):
    - pdf_page: PDF显示的页码 (>=1)
    - actual_page: 用户指定的实际页码 (>=1)
    """
    try:
        # 验证书籍存在
        storage = await get_storage()
        book = await storage.get_book(book_id)
        if not book:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Book with ID '{book_id}' not found"
            )

        mark_service = await get_chapter_mark_service()

        # 方式1：直接设置偏移量
        if request.page_offset is not None:
            success, offset, updated_book = await mark_service.set_page_offset_direct(
                book_id=book_id,
                offset=request.page_offset
            )

            if not success or not updated_book:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to set page offset"
                )

            return PageCalibrationResponse(
                book_id=book_id,
                pdf_page=0,  # 直接设置时不适用
                actual_page=0,  # 直接设置时不适用
                offset=offset,
                message=f"Page offset set to {offset}."
            )

        # 方式2：通过页码计算偏移量
        if request.pdf_page is None or request.actual_page is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Either provide 'page_offset' directly, or both 'pdf_page' and 'actual_page'"
            )

        # 验证页码范围
        if request.pdf_page > book.metadata.total_pages:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"PDF page number exceeds total pages ({book.metadata.total_pages})"
            )

        success, offset, updated_book = await mark_service.calibrate_page_offset(
            book_id=book_id,
            pdf_page=request.pdf_page,
            actual_page=request.actual_page
        )

        if not success or not updated_book:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to calibrate page numbers"
            )

        return PageCalibrationResponse(
            book_id=book_id,
            pdf_page=request.pdf_page,
            actual_page=request.actual_page,
            offset=offset,
            message=f"Page calibration successful. Offset set to {offset}. "
                   f"PDF page {request.pdf_page} corresponds to actual page {request.actual_page}."
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to calibrate page numbers for {book_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to calibrate page numbers: {str(e)}"
        )


@api_router.get("/books/{book_id}/info", response_model=BookInfoResponse)
async def get_book_info(book_id: str):
    """
    获取书籍信息（包含页码偏移）

    Path parameter:
    - book_id: 书籍唯一标识
    """
    try:
        storage = await get_storage()
        book = await storage.get_book(book_id)

        if not book:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Book with ID '{book_id}' not found"
            )

        return BookInfoResponse(
            book_id=book_id,
            title=book.metadata.title,
            total_chapters=book.metadata.total_chapters,
            total_pages=book.metadata.total_pages,
            page_offset=getattr(book.metadata, 'page_offset', 0),
            message="Book info retrieved successfully"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get book info for {book_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get book info: {str(e)}"
        )


# ==================== Translation Review Adapter ====================

def _review_roots() -> List[Path]:
    jobs_root = get_job_service().jobs_dir.resolve()
    configured_roots = os.getenv("PDF_TRANSLATOR_REVIEW_ROOTS")
    if configured_roots:
        roots = [
            Path(raw).expanduser().resolve()
            for raw in configured_roots.split(os.pathsep)
            if raw.strip()
        ]
    else:
        roots = [jobs_root]
    if jobs_root not in roots:
        roots.append(jobs_root)
    return roots


def _extract_review_title(run_dir: Path, manifest: dict, segments: List[dict]) -> str:
    source_path = manifest.get("source_pdf") or manifest.get("source_epub")
    if isinstance(source_path, str) and source_path.strip():
        return Path(source_path).name.rsplit(".", 1)[0]
    if segments:
        chapter_title = str(segments[0].get("chapter_title") or "").strip()
        if chapter_title:
            return chapter_title
    return run_dir.name


def _review_import_roots() -> List[Path]:
    configured = os.getenv("PDF_TRANSLATOR_IMPORT_ROOTS")
    default_roots = ["/Users/huachunmu/Desktop/文档/OK"]
    return [
        Path(raw).expanduser().resolve()
        for raw in (configured.split(os.pathsep) if configured else default_roots)
        if raw.strip()
    ]


def _review_delivery_root() -> Path:
    configured = os.getenv("BOOKMATE_EXPORT_DIR")
    return Path(configured or "~/Desktop/文档/Translated").expanduser().resolve()


def _hidden_review_projects_path() -> Path:
    return get_job_service().jobs_dir.resolve() / ".hidden-review-projects.json"


def _load_hidden_review_projects() -> set[str]:
    path = _hidden_review_projects_path()
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    values = payload.get("run_dirs", []) if isinstance(payload, dict) else []
    return {str(value) for value in values if isinstance(value, str)}


def _save_hidden_review_projects(run_dirs: set[str]) -> None:
    path = _hidden_review_projects_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"run_dirs": sorted(run_dirs)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _collect_exported_versions(run_dir: Path) -> List[str]:
    versions_dir = run_dir / "versions"
    if not versions_dir.exists():
        return []
    versions: List[str] = []
    for child in versions_dir.iterdir():
        if child.is_dir() and (child / "version-manifest.json").exists():
            versions.append(child.name)
    return sorted(versions)


def _discover_review_run_dirs(root: Path) -> List[Path]:
    """Find review projects both at the root and inside BookMate job directories."""
    if not root.exists() or not root.is_dir():
        return []
    required = {"segments.json", "translated_segments.json", "review_items.json", "review_state.json"}
    candidates = [root]
    candidates.extend(child / "review" for child in root.iterdir() if child.is_dir())
    candidates.extend(path.parent for path in root.rglob("review_state.json"))
    discovered: dict[Path, Path] = {}
    for candidate in candidates:
        if not all((candidate / name).is_file() for name in required):
            continue
        resolved = candidate.resolve()
        discovered.setdefault(resolved, candidate)
    return list(discovered.values())


def _build_review_project_item(run_dir: Path) -> Optional[ReviewProjectListItem]:
    required = ["segments.json", "translated_segments.json", "review_items.json", "review_state.json"]
    if any(not (run_dir / name).exists() for name in required):
        return None
    try:
        segments_payload = _read_review_json(run_dir, "segments.json")
        review_items_payload = _read_review_json(run_dir, "review_items.json")
        review_state = _read_review_json(run_dir, "review_state.json")
        manifest_path = run_dir / "manifest.json"
        manifest = _read_review_json(run_dir, "manifest.json") if manifest_path.exists() else {}
    except Exception as exc:
        logger.warning(f"Skip invalid review run {run_dir}: {exc}")
        return None

    segments = segments_payload.get("segments", [])
    total_segments = len(segments)
    decisions = review_state.get("decisions", {}) if isinstance(review_state, dict) else {}
    review_items = review_items_payload.get("items", []) if isinstance(review_items_payload, dict) else []
    completion = _review_completion_state(
        segments=segments if isinstance(segments, list) else [],
        review_items=review_items if isinstance(review_items, list) else [],
        review_state=review_state if isinstance(review_state, dict) else {},
    )
    reviewed_segments = int(completion["reviewed_scope_segments"])
    progress_percent = int(completion["progress_percent"])

    review_summary = review_state.get("summary", {}) if isinstance(review_state, dict) else {}
    qa_items_total = int(
        review_summary["total_items"]
        if "total_items" in review_summary
        else len(review_items)
    )
    qa_items_open = int(
        review_summary["open_items"]
        if "open_items" in review_summary
        else max(qa_items_total - reviewed_segments, 0)
    )
    pending_rewrites = int(completion["pending_rewrites"])
    rewrites_needing_instruction = sum(
        1
        for decision in decisions.values()
        if isinstance(decision, dict)
        and decision.get("action") == "model_rewrite"
        and decision.get("status") == "open"
        and not str(decision.get("reviewer_comment") or "").strip()
    )

    exported_versions = _collect_exported_versions(run_dir)
    export_completed = bool(exported_versions)
    review_completed = bool(completion["review_completed"])
    if review_completed and export_completed:
        review_status = "exported"
    elif review_completed:
        review_status = "reviewed"
    elif export_completed or reviewed_segments > 0:
        review_status = "in_review"
    else:
        review_status = "unreviewed"

    state_updated_at = str(review_state.get("updated_at") or "").strip() if isinstance(review_state, dict) else ""
    if not state_updated_at:
        state_updated_at = datetime.fromtimestamp(run_dir.stat().st_mtime, tz=timezone.utc).isoformat()
    source_path = manifest.get("source_pdf") or manifest.get("source_epub")

    return ReviewProjectListItem(
        run_dir=str(run_dir),
        title=_extract_review_title(run_dir, manifest, segments),
        source_path=source_path if isinstance(source_path, str) else None,
        workspace_job_id=_review_workspace_job_id(run_dir),
        review_status=review_status,
        review_completed=review_completed,
        export_completed=export_completed,
        review_scope_segments=int(completion["review_scope_segments"]),
        reviewed_scope_segments=reviewed_segments,
        total_segments=total_segments,
        reviewed_segments=reviewed_segments,
        progress_percent=progress_percent,
        qa_items_total=qa_items_total,
        qa_items_open=max(qa_items_open, 0),
        pending_rewrites=pending_rewrites,
        rewrites_needing_instruction=rewrites_needing_instruction,
        exported_versions=exported_versions,
        latest_version=exported_versions[-1] if exported_versions else None,
        updated_at=state_updated_at,
    )


def _review_project_identity(item: ReviewProjectListItem) -> tuple[str, int]:
    source_name = ""
    if item.source_path:
        source_name = Path(item.source_path).name.strip().casefold()
    return (source_name or item.title.strip().casefold(), item.total_segments)


def _review_workspace_job_id(run_dir: Path) -> Optional[str]:
    jobs_root = get_job_service().jobs_dir.resolve()
    candidates = [run_dir]
    try:
        candidates.append(run_dir.resolve())
    except OSError:
        pass
    for candidate in candidates:
        for path in [candidate, *candidate.parents]:
            resolved = path.resolve()
            if resolved == jobs_root:
                break
            if jobs_root not in resolved.parents:
                continue
            if (resolved / "job.json").is_file():
                return resolved.name
    return None


def _is_preferred_review_project(
    candidate: ReviewProjectListItem,
    current: ReviewProjectListItem,
    *,
    jobs_root: Path,
) -> bool:
    candidate_path = Path(candidate.run_dir).resolve()
    current_path = Path(current.run_dir).resolve()
    candidate_is_job = candidate_path == jobs_root or jobs_root in candidate_path.parents
    current_is_job = current_path == jobs_root or jobs_root in current_path.parents
    if candidate_is_job != current_is_job:
        return candidate_is_job
    return candidate.updated_at > current.updated_at


def list_review_projects_sync() -> list[ReviewProjectListItem]:
    projects_by_identity: dict[tuple[str, int], ReviewProjectListItem] = {}
    seen: set[Path] = set()
    jobs_root = get_job_service().jobs_dir.resolve()
    hidden = _load_hidden_review_projects()
    for root in _review_roots():
        for run_dir in _discover_review_run_dirs(root):
            resolved = run_dir.resolve()
            if resolved in seen or str(resolved) in hidden:
                continue
            seen.add(resolved)
            item = _build_review_project_item(run_dir)
            if item is None:
                continue
            identity = _review_project_identity(item)
            current = projects_by_identity.get(identity)
            if current is None or _is_preferred_review_project(
                item,
                current,
                jobs_root=jobs_root,
            ):
                projects_by_identity[identity] = item
    projects = list(projects_by_identity.values())
    projects.sort(key=lambda item: item.updated_at, reverse=True)
    return projects


def _resolve_review_run_dir(run_dir: str) -> Path:
    path = Path(run_dir).expanduser().resolve()
    roots = _review_roots()
    if roots and not any(path == root or root in path.parents for root in roots):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Review run directory is outside allowed PDF translator runs roots.",
        )
    if not path.exists() or not path.is_dir():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Review run directory not found: {path}",
        )
    required = ["segments.json", "translated_segments.json", "review_items.json", "review_state.json"]
    missing = [name for name in required if not (path / name).exists()]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Review run is missing required files: {', '.join(missing)}",
        )
    return path


def _read_review_json(run_dir: Path, name: str):
    return json.loads((run_dir / name).read_text(encoding="utf-8"))


def _write_review_json(run_dir: Path, name: str, payload) -> None:
    target = run_dir / name
    temporary = run_dir / f".{name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _has_translated_output_files(source_dir: Path) -> bool:
    required = ["book.json", "translated-chapters.json", "manifest.json"]
    return all((source_dir / name).exists() for name in required) and (source_dir / "translation-cache").exists()


def _run_name_from_source_dir(source_dir: Path) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", source_dir.name).strip("-").lower() or "review"
    short_hash = hashlib.sha1(str(source_dir).encode("utf-8")).hexdigest()[:8]
    return f"{base}-{short_hash}-review"


def _prepare_review_run_from_output(source_dir: Path, run_dir: Path) -> None:
    home = _pdf_translator_home()
    script = (
        "import json\n"
        "from pathlib import Path\n"
        "from pdf_translator.review import build_review_artifacts, write_review_artifacts\n"
        "source_dir = Path(__import__('sys').argv[1])\n"
        "run_dir = Path(__import__('sys').argv[2])\n"
        "run_dir.mkdir(parents=True, exist_ok=True)\n"
        "book = json.loads((source_dir / 'book.json').read_text(encoding='utf-8'))\n"
        "translated = json.loads((source_dir / 'translated-chapters.json').read_text(encoding='utf-8'))\n"
        "chapters = translated.get('chapters') if isinstance(translated, dict) else translated\n"
        "manifest = json.loads((source_dir / 'manifest.json').read_text(encoding='utf-8'))\n"
        "max_chunk = int((manifest.get('translation', {}) or {}).get('max_chunk_chars', 9000))\n"
        "target_lang = str(manifest.get('target_language') or 'zh-CN')\n"
        "source_path = None\n"
        "for key in ('source_pdf', 'source_epub'):\n"
        "    value = manifest.get(key)\n"
        "    if isinstance(value, str) and value.strip() and Path(value).exists():\n"
        "        source_path = Path(value)\n"
        "        break\n"
        "if source_path is None:\n"
        "    candidates = sorted(source_dir.glob('*.pdf')) + sorted(source_dir.glob('*.epub'))\n"
        "    for c in candidates:\n"
        "        name = c.name.lower()\n"
        "        if '(zh' not in name and 'polished' not in name:\n"
        "            source_path = c\n"
        "            break\n"
        "    if source_path is None and candidates:\n"
        "        source_path = candidates[0]\n"
        "if source_path is None:\n"
        "    raise RuntimeError(f'Cannot determine source file for {source_dir}')\n"
        "cache_dir = source_dir / 'translation-cache'\n"
        "artifacts = build_review_artifacts(source_path=source_path, target_language=target_lang, book=book, translated_chapters=chapters, cache_dir=cache_dir if cache_dir.exists() else None, max_chunk_chars=max_chunk, run_dir=source_dir)\n"
        "write_review_artifacts(run_dir, artifacts)\n"
        "run_manifest = dict(manifest)\n"
        "run_manifest['review_alignment'] = 'reading_units'\n"
        "run_manifest['review_source_dir'] = str(source_dir)\n"
        "(run_dir / 'manifest.json').write_text(json.dumps(run_manifest, ensure_ascii=False, indent=2), encoding='utf-8')\n"
        "(run_dir / 'book.json').write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding='utf-8')\n"
    )
    result = subprocess.run(
        ["uv", "run", "python", "-c", script, str(source_dir), str(run_dir)],
        cwd=home,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Failed to prepare review run").strip()
        raise RuntimeError(detail[:2000])


def _pdf_translator_home() -> Path:
    from engine_home import resolve_book_weaver_home

    return resolve_book_weaver_home()


def _pdf_translator_python() -> Path:
    home = _pdf_translator_home()
    candidates = [
        home / ".venv" / "bin" / "python",
        home / ".venv" / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return Path(sys.executable)


def _run_pdf_translator_cli(args: list[str], *, timeout_seconds: int = 900) -> subprocess.CompletedProcess[str]:
    service = get_job_service()
    home = service.project_home
    if not (home / "pyproject.toml").is_file():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"pdf-translator project not found at {home}",
        )
    environment = os.environ.copy()
    environment.pop("VIRTUAL_ENV", None)
    service._augment_subprocess_env(environment)
    service._normalize_provider_env(environment)
    try:
        cmd = service._resolve_runner_cmd(environment)
    except JobServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    try:
        result = subprocess.run(
            [*cmd, "pdf-translator", *args],
            cwd=home,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"pdf-translator command timed out after {timeout_seconds}s: {' '.join(args)}",
        ) from exc
    if result.returncode != 0:
        raw_detail = (result.stderr or result.stdout or "pdf-translator command failed").strip()
        value_error_matches = re.findall(r"ValueError:\s*(.+)", raw_detail)
        detail = value_error_matches[-1].strip() if value_error_matches else raw_detail.splitlines()[-1].strip()
        unresolved_match = re.search(
            r"unresolved review items remain \((\d+)\)",
            detail,
            flags=re.IGNORECASE,
        )
        if unresolved_match:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"导出已拦截：仍有 {unresolved_match.group(1)} 个审阅项未完成。",
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=detail[:1000],
        )
    return result


def _parse_rewrite_count(stdout: str) -> Optional[int]:
    match = re.search(r"Rewrite candidates generated:\s*(\d+)", stdout)
    if not match:
        return None
    return int(match.group(1))


def _run_review_python(*, run_dir: Path, snippet: str) -> Any:
    """Execute a short pdf_translator.review helper against a run directory."""
    import sys as _sys

    home = _pdf_translator_home()
    src_path = home / "src"
    if not src_path.exists():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"pdf-translator source not found at {src_path}",
        )
    script = (
        "import json\n"
        "import sys\n"
        f"sys.path.insert(0, {json.dumps(str(src_path))})\n"
        "from pathlib import Path\n"
        f"run_dir = Path({json.dumps(str(run_dir))})\n"
        f"{snippet}\n"
        "print(json.dumps(result, ensure_ascii=False))\n"
    )
    try:
        proc = subprocess.run(
            [str(_pdf_translator_python()), "-c", script],
            cwd=home,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Review helper timed out",
        ) from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "Review helper failed").strip()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail[:4000])
    try:
        return json.loads(proc.stdout.strip() or "null")
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Invalid review helper response: {proc.stdout[:500]}",
        ) from exc


def _load_pre_review_payload(run_dir: Path, segments: list, review_items: list) -> dict:
    path = run_dir / "pre_review.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    issue_counts: dict[str, int] = {}
    flagged_segment_ids: list[str] = []
    for item in review_items:
        issue_type = str(item.get("issue_type") or "unknown")
        issue_counts[issue_type] = issue_counts.get(issue_type, 0) + 1
        segment_id = str(item.get("segment_id") or "").strip()
        if segment_id:
            flagged_segment_ids.append(segment_id)
    total_segments = len(segments)
    flagged_segments = len(flagged_segment_ids)
    return {
        "schema": "translation_pre_review_v1",
        "status": "completed",
        "total_segments": total_segments,
        "flagged_segments": flagged_segments,
        "clean_segments": max(total_segments - flagged_segments, 0),
        "issue_counts": issue_counts,
        "flagged_segment_ids": flagged_segment_ids,
    }


def _load_chapter_marks_payload(run_dir: Path) -> dict:
    path = run_dir / "review_chapter_marks.json"
    if not path.exists():
        return {"schema": "translation_review_chapter_marks_v1", "marks": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("marks"), list):
            return payload
    except Exception:
        pass
    return {"schema": "translation_review_chapter_marks_v1", "marks": []}


def _build_review_project_payload(path: Path) -> dict:
    segments_payload = _read_review_json(path, "segments.json")
    translated_segments = _read_review_json(path, "translated_segments.json")
    review_items_payload = _read_review_json(path, "review_items.json")
    review_state = _read_review_json(path, "review_state.json")
    manifest_path = path / "manifest.json"
    manifest = _read_review_json(path, "manifest.json") if manifest_path.exists() else {}
    segments = segments_payload.get("segments", [])
    review_items = review_items_payload.get("items", [])
    pre_review = _load_pre_review_payload(path, segments, review_items)
    chapter_marks = _load_chapter_marks_payload(path)
    workflow = review_state.get("workflow", {}) if isinstance(review_state, dict) else {}
    if not isinstance(workflow, dict):
        workflow = {}
    if not workflow.get("human_review_mode"):
        flagged = int(pre_review.get("flagged_segments") or 0)
        workflow = {
            "pre_review_completed": True,
            "human_review_mode": "issues_only" if flagged else "full",
        }
    chapter_groups: list = []
    if chapter_marks.get("marks"):
        groups = _run_review_python(
            run_dir=path,
            snippet=(
                "from pdf_translator.review import build_chapter_groups_from_marks\n"
                "segments = json.loads((run_dir / 'segments.json').read_text(encoding='utf-8')).get('segments', [])\n"
                "marks_payload = json.loads((run_dir / 'review_chapter_marks.json').read_text(encoding='utf-8'))\n"
                "result = build_chapter_groups_from_marks(segments, marks_payload.get('marks', []))\n"
            ),
        )
        if isinstance(groups, list):
            chapter_groups = groups
    return {
        "run_dir": str(path),
        "manifest": manifest,
        "segments": segments,
        "translated_segments": translated_segments.get("segments", []),
        "review_items": review_items,
        "review_state": review_state,
        "pre_review": pre_review,
        "chapter_marks": chapter_marks,
        "chapter_groups": chapter_groups if isinstance(chapter_groups, list) else [],
        "workflow": workflow,
    }


@api_router.get("/review/project")
async def get_review_project(run_dir: str):
    """Load a pdf-translator review project from a completed run directory."""
    path = _resolve_review_run_dir(run_dir)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: _build_review_project_payload(path))


@api_router.get("/review/projects", response_model=ReviewProjectsResponse)
async def list_review_projects():
    """List all review projects under configured review roots."""
    projects = list_review_projects_sync()
    return ReviewProjectsResponse(total_projects=len(projects), projects=projects)


@api_router.delete("/review/projects")
async def remove_review_project(
    run_dir: str,
    mode: Literal["hide", "delete"] = "hide",
):
    """Hide one project from the console or delete its BookMate working copy."""
    raw_path = Path(run_dir).expanduser()
    path = _resolve_review_run_dir(run_dir)
    hidden = _load_hidden_review_projects()
    hidden.add(str(path))

    if mode == "delete":
        jobs_root = get_job_service().jobs_dir.resolve()
        if path == jobs_root or jobs_root not in path.parents:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only review working copies under Desktop/文档/Bookmate/Jobs can be deleted.",
            )
        if raw_path.is_symlink():
            raw_path.unlink()
        shutil.rmtree(path)
        hidden.discard(str(path))

    _save_hidden_review_projects(hidden)
    return {
        "status": "deleted" if mode == "delete" else "hidden",
        "run_dir": str(path),
        "source_preserved": True,
        "exports_preserved": True,
    }


@api_router.post("/review/projects/sync", response_model=ReviewSyncResponse)
async def sync_review_projects():
    """Import translated book outputs into review runs."""
    roots = _review_import_roots()
    run_root = get_job_service().jobs_dir.resolve()
    run_root.mkdir(parents=True, exist_ok=True)

    imported_runs: List[str] = []
    skipped_sources: List[str] = []
    failed_sources: List[str] = []

    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        for source_dir in root.iterdir():
            if not source_dir.is_dir():
                continue
            if not _has_translated_output_files(source_dir):
                continue
            run_name = _run_name_from_source_dir(source_dir)
            target_run_dir = run_root / run_name
            if _build_review_project_item(target_run_dir) is not None:
                skipped_sources.append(str(source_dir))
                continue
            try:
                _prepare_review_run_from_output(source_dir, target_run_dir)
                imported_runs.append(str(target_run_dir))
            except Exception as exc:
                logger.warning(f"Failed to import review source {source_dir}: {exc}")
                failed_sources.append(f"{source_dir} :: {exc}")

    return ReviewSyncResponse(
        imported=len(imported_runs),
        skipped=len(skipped_sources),
        failed=len(failed_sources),
        imported_runs=imported_runs,
        skipped_sources=skipped_sources,
        failed_sources=failed_sources,
    )


@api_router.post("/review/segments/{segment_id:path}/decision")
async def save_review_decision(segment_id: str, request: ReviewDecisionRequest, run_dir: str):
    """Save a reviewer decision for one source/translation segment."""
    path = _resolve_review_run_dir(run_dir)
    segments = _read_review_json(path, "segments.json").get("segments", [])
    segment_ids = {segment.get("segment_id") for segment in segments}
    if segment_id not in segment_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Review segment not found: {segment_id}",
        )
    state = _read_review_json(path, "review_state.json")
    decisions = state.setdefault("decisions", {})
    previous = decisions.get(segment_id, {})
    if not isinstance(previous, dict):
        previous = {}
    updated = dict(previous)
    updated["status"] = request.status
    updated["action"] = request.action
    if request.reviewer_comment is not None:
        updated["reviewer_comment"] = request.reviewer_comment
    if request.approved_text is not None:
        updated["approved_text"] = request.approved_text
    updated["updated_at"] = datetime.now(timezone.utc).isoformat()
    decisions[segment_id] = updated

    items = _read_review_json(path, "review_items.json").get("items", [])
    for item in items:
        if item.get("segment_id") == segment_id:
            item["status"] = request.status
    total_items = len(items)
    item_segment_ids = {item.get("segment_id") for item in items}
    approved_items = sum(
        1
        for item_id in item_segment_ids
        if decisions.get(item_id, {}).get("status") == "approved"
    )
    resolved_items = sum(
        1
        for item_id in item_segment_ids
        if decisions.get(item_id, {}).get("status") == "resolved"
    )
    state["summary"] = {
        "total_items": total_items,
        "open_items": max(total_items - approved_items - resolved_items, 0),
        "approved_items": approved_items,
        "resolved_items": resolved_items,
    }
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_review_json(path, "review_state.json", state)
    review_items_payload = _read_review_json(path, "review_items.json")
    review_items_payload["items"] = items
    _write_review_json(path, "review_items.json", review_items_payload)
    return {"status": "saved", "segment_id": segment_id, "review_state": state}


@api_router.post("/review/rewrite")
async def run_review_rewrite(run_dir: str, request: ReviewRewriteRequest):
    """Generate model rewrite candidates for segments marked with model_rewrite."""
    path = _resolve_review_run_dir(run_dir)
    review_state = _read_review_json(path, "review_state.json")
    decisions = review_state.get("decisions", {}) if isinstance(review_state, dict) else {}
    selected_decisions = [
        (segment_id, decision)
        for segment_id, decision in decisions.items()
        if isinstance(decision, dict)
        and decision.get("action") == "model_rewrite"
        and decision.get("status") in {"open", "requested"}
        and (request.segment_id is None or segment_id == request.segment_id)
    ]
    missing_instruction = [
        segment_id
        for segment_id, decision in selected_decisions
        if not str(decision.get("reviewer_comment") or "").strip()
    ]
    if missing_instruction:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"{len(missing_instruction)} 段尚未填写给模型的重译要求。"
                "请进入该段，填写具体修改要求后再执行重译。"
            ),
        )
    if not selected_decisions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="没有可执行的待重译段落。",
        )
    args = [
        "review",
        "rewrite",
        str(path),
        "--target-lang",
        request.target_lang,
        "--translator",
        request.translator,
    ]
    if request.source_lang:
        args.extend(["--source-lang", request.source_lang])
    if request.segment_id:
        args.extend(["--segment-id", request.segment_id])
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, lambda: _run_pdf_translator_cli(args))
    rewritten_count = _parse_rewrite_count(result.stdout) or 0
    review_state = _read_review_json(path, "review_state.json")
    return {
        "status": "completed",
        "rewritten_count": rewritten_count,
        "stdout": result.stdout.strip(),
        "review_state": review_state,
    }


@api_router.post("/review/export")
async def run_review_export(run_dir: str, request: ReviewExportRequest):
    """Apply review decisions and export a versioned reviewed output."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", request.version):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsafe review version name. Use letters, numbers, dots, underscores, or dashes.",
        )
    path = _resolve_review_run_dir(run_dir)
    args = [
        "review",
        "export",
        str(path),
        "--version",
        request.version,
        "--target-lang",
        request.target_lang,
        "--format",
        request.output_format,
    ]
    if request.parent_version:
        args.extend(["--parent-version", request.parent_version])
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, lambda: _run_pdf_translator_cli(args, timeout_seconds=1800))
    version_manifest_path = path / "versions" / request.version / "version-manifest.json"
    if not version_manifest_path.exists():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="review-export completed but version manifest was not created.",
        )
    version_manifest = json.loads(version_manifest_path.read_text(encoding="utf-8"))
    delivery_root = _review_delivery_root()
    delivery_root.mkdir(parents=True, exist_ok=True)
    files = version_manifest.get("files", {})
    delivered_files: dict[str, str] = {}
    for key in ("translated_epub", "translated_pdf", "translated_markdown"):
        source = files.get(key) if isinstance(files, dict) else None
        if not isinstance(source, str):
            continue
        source_path = Path(source)
        if not source_path.is_absolute():
            source_path = path / source_path
        if not source_path.is_file():
            continue
        if key == "translated_markdown":
            epub_source = files.get("translated_epub") if isinstance(files, dict) else None
            delivery_stem = Path(epub_source).stem if isinstance(epub_source, str) else path.name
            destination = delivery_root / f"{delivery_stem} ({request.version}).md"
        else:
            destination = delivery_root / source_path.name
        shutil.copy2(source_path, destination)
        delivered_files[key] = str(destination)
    version_manifest["delivery_dir"] = str(delivery_root)
    version_manifest["delivered_files"] = delivered_files
    _write_review_json(version_manifest_path.parent, version_manifest_path.name, version_manifest)
    return {
        "status": "completed",
        "version": request.version,
        "version_dir": str(version_manifest_path.parent),
        "delivery_dir": str(delivery_root),
        "delivered_files": delivered_files,
        "manifest": version_manifest,
        "stdout": result.stdout.strip(),
    }


@api_router.post("/review/workflow")
async def update_review_workflow(run_dir: str, request: ReviewWorkflowRequest):
    """Switch human review mode between issues_only and full."""
    path = _resolve_review_run_dir(run_dir)
    loop = asyncio.get_running_loop()
    review_state = await loop.run_in_executor(
        None,
        lambda: _run_review_python(
            run_dir=path,
            snippet=(
                "from pdf_translator.review import update_review_workflow\n"
                f"result = update_review_workflow(run_dir, human_review_mode={json.dumps(request.human_review_mode)})\n"
            ),
        ),
    )
    return {"status": "saved", "workflow": review_state.get("workflow", {}), "review_state": review_state}


@api_router.post("/review/chapter-marks")
async def create_review_chapter_mark(run_dir: str, request: ReviewChapterMarkRequest):
    """Mark the start of a new chapter at a review segment boundary."""
    path = _resolve_review_run_dir(run_dir)
    segments = _read_review_json(path, "segments.json").get("segments", [])
    segment_ids = {segment.get("segment_id") for segment in segments}
    if request.segment_id not in segment_ids:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Review segment not found: {request.segment_id}")
    title = request.chapter_title.strip()
    if not title:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="chapter_title is required")
    loop = asyncio.get_running_loop()
    chapter_marks = await loop.run_in_executor(
        None,
        lambda: _run_review_python(
            run_dir=path,
            snippet=(
                "from pdf_translator.review import add_review_chapter_mark\n"
                f"segments = json.loads((run_dir / 'segments.json').read_text(encoding='utf-8')).get('segments', [])\n"
                f"result = add_review_chapter_mark(run_dir=run_dir, segments=segments, "
                f"segment_id={json.dumps(request.segment_id)}, chapter_title={json.dumps(title)})\n"
            ),
        ),
    )
    project = await loop.run_in_executor(None, lambda: _build_review_project_payload(path))
    return {"status": "saved", "chapter_marks": chapter_marks, "chapter_groups": project.get("chapter_groups", [])}


@api_router.delete("/review/chapter-marks/{mark_id}")
async def delete_review_chapter_mark(run_dir: str, mark_id: str):
    """Remove a user-defined review chapter mark."""
    path = _resolve_review_run_dir(run_dir)
    loop = asyncio.get_running_loop()
    try:
        chapter_marks = await loop.run_in_executor(
            None,
            lambda: _run_review_python(
                run_dir=path,
                snippet=(
                    "from pdf_translator.review import remove_review_chapter_mark\n"
                    f"result = remove_review_chapter_mark(run_dir=run_dir, mark_id={json.dumps(mark_id)})\n"
                ),
            ),
        )
    except HTTPException as exc:
        if "Chapter mark not found" in str(exc.detail):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc.detail)) from exc
        raise
    project = await loop.run_in_executor(None, lambda: _build_review_project_payload(path))
    return {"status": "deleted", "chapter_marks": chapter_marks, "chapter_groups": project.get("chapter_groups", [])}


# ==================== Utility Endpoints ====================

@api_router.get("/storage/consistency-check")
async def check_storage_consistency():
    """
    Check storage consistency between PDF files and JSON metadata
    
    Returns a list of any inconsistencies found
    """
    try:
        storage = await get_storage()
        issues = await storage.verify_consistency()
        
        return {
            "status": "consistent" if not issues else "issues_found",
            "issues_count": len(issues),
            "issues": issues
        }
    except Exception as e:
        logger.error(f"Consistency check failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to run consistency check: {str(e)}"
        )


@api_router.post("/cache/clear")
async def clear_ai_cache():
    """
    清理过期的 AI 生成缓存
    
    Returns the number of cleared cache files
    """
    try:
        ai_service = await get_ai_service()
        cleared = await ai_service.clear_cache()
        
        return {
            "status": "success",
            "cleared_count": cleared
        }
    except Exception as e:
        logger.error(f"Failed to clear cache: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to clear cache: {str(e)}"
        )


# ==================== Error Handlers ====================

@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    """HTTP exception handler"""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail}
    )


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Global exception handler"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An unexpected error occurred", "message": str(exc)}
    )


# ==================== Main Entry Point ====================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG
    )

# ==================== Mount API Router (FIXED) ====================
# [FIX] Router must be mounted AFTER all routes are defined
app.include_router(api_router)
