"""
BookMate Storage Module - Phase 1 Updated
提供 JSON 持久化存储，支持 page_number 字段
"""
import os
import json
import logging
import asyncio
import time
from typing import List, Optional, Dict, Any
from datetime import datetime
from pathlib import Path
from pydantic import BaseModel, Field, field_validator

from pdf_parser import BookData, Chapter

logger = logging.getLogger(__name__)


class ChapterModel(BaseModel):
    """Pydantic model for chapter data with page navigation"""
    index: int = Field(..., description="Chapter index/number")
    title: str = Field(..., description="Chapter title")
    content: str = Field(..., description="Chapter text content")
    page_number: int = Field(default=1, description="Chapter start page number (1-based)")
    end_page: int = Field(default=1, description="Chapter end page number (inclusive)")
    is_user_mark: bool = Field(default=False, description="Whether this chapter is from a user mark")
    mark_id: Optional[str] = Field(default=None, description="Associated user mark ID if is_user_mark is True")

    @field_validator('title')
    @classmethod
    def title_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            return "Untitled Chapter"
        return v.strip()


class UserMarkModel(BaseModel):
    """Pydantic model for user chapter marks"""
    mark_id: str = Field(..., description="Unique mark identifier")
    page_number: int = Field(..., ge=1, description="Page number where mark is placed (1-based)")
    y_position: float = Field(..., ge=0, description="Vertical position on the page (0-1 normalized)")
    chapter_name: Optional[str] = Field(default=None, description="Optional chapter name extracted by AI or user input")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Creation timestamp")


class BookMetadata(BaseModel):
    """Pydantic model for book metadata"""
    book_id: str = Field(..., description="Unique book identifier")
    title: str = Field(..., description="Book title")
    filename: str = Field(..., description="Original filename")
    total_chapters: int = Field(..., ge=0, description="Total number of chapters")
    total_pages: int = Field(default=0, description="Total PDF pages")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Creation timestamp")
    updated_at: datetime = Field(default_factory=datetime.utcnow, description="Last update timestamp")
    version: str = Field(default="1.1", description="Storage format version")
    page_offset: int = Field(default=0, description="用户定义的页码偏移量（用于页码校准）")

    @field_validator('title')
    @classmethod
    def title_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            return "Untitled Book"
        return v.strip()


class StoredBook(BaseModel):
    """Complete book data model for storage"""
    metadata: BookMetadata
    chapters: List[ChapterModel]
    user_marks: List[UserMarkModel] = Field(default_factory=list, description="User-defined chapter marks")
    original_chapters: List[ChapterModel] = Field(default_factory=list, description="Original chapters from PDF TOC (for recalculation)")

    def to_book_data(self) -> BookData:
        """Convert StoredBook to BookData (for backward compatibility)"""
        return BookData(
            book_id=self.metadata.book_id,
            title=self.metadata.title,
            filename=self.metadata.filename,
            total_chapters=self.metadata.total_chapters,
            total_pages=self.metadata.total_pages,
            chapters=[
                Chapter(
                    index=ch.index,
                    title=ch.title,
                    content=ch.content,
                    page_number=ch.page_number,
                    end_page=ch.end_page
                )
                for ch in self.chapters
            ]
        )

    @classmethod
    def from_book_data(cls, book_data: BookData, user_marks: Optional[List[UserMarkModel]] = None) -> "StoredBook":
        """Create StoredBook from BookData"""
        chapters = [
            ChapterModel(
                index=ch.index,
                title=ch.title,
                content=ch.content,
                page_number=ch.page_number,
                end_page=ch.end_page
            )
            for ch in book_data.chapters
        ]
        return cls(
            metadata=BookMetadata(
                book_id=book_data.book_id,
                title=book_data.title,
                filename=getattr(book_data, 'filename', ''),
                total_chapters=book_data.total_chapters,
                total_pages=getattr(book_data, 'total_pages', 0)
            ),
            chapters=chapters,
            user_marks=user_marks or [],
            original_chapters=chapters.copy()  # 保存原始章节备份
        )


class BookListItem(BaseModel):
    """Book list item for API responses"""
    book_id: str
    title: str
    total_chapters: int
    total_pages: int = 0
    created_at: Optional[datetime] = None


class BookStorage:
    """
    JSON 持久化存储管理类
    
    Features:
    - 异步安全（使用 asyncio.Lock）
    - Pydantic 模型验证
    - 自动 JSON 序列化/反序列化
    - 向后兼容（处理旧数据格式）
    - 完整的错误处理和日志
    """
    
    def __init__(self, storage_path: str, upload_dir: str):
        """
        初始化存储管理器
        
        Args:
            storage_path: JSON 元数据存储目录路径
            upload_dir: PDF 文件上传目录路径
        """
        self.storage_path = Path(storage_path)
        self.upload_dir = Path(upload_dir)
        self._lock = asyncio.Lock()
        self._cache: Dict[str, StoredBook] = {}
        self._initialized = False
        
        logger.info(f"BookStorage initialized: storage_path={storage_path}, upload_dir={upload_dir}")
    
    async def initialize(self) -> None:
        """
        异步初始化存储目录并加载现有数据
        必须在应用启动时调用
        """
        if self._initialized:
            return
            
        async with self._lock:
            # 确保目录存在
            self.storage_path.mkdir(parents=True, exist_ok=True)
            self.upload_dir.mkdir(parents=True, exist_ok=True)
            
            # 加载所有已存在的书籍
            await self._load_all_books()
            self._initialized = True
            
        logger.info(f"BookStorage initialized with {len(self._cache)} books")
    
    async def _load_all_books(self) -> None:
        """从磁盘加载所有书籍（内部方法，需在锁保护下调用）"""
        if not self.storage_path.exists():
            return
            
        json_files = list(self.storage_path.glob("*.json"))
        logger.info(f"Found {len(json_files)} book metadata files")
        
        for json_file in json_files:
            try:
                book = await self._load_book_from_file(json_file)
                if book:
                    self._cache[book.metadata.book_id] = book
            except Exception as e:
                logger.error(f"Failed to load book from {json_file}: {e}")
    
    async def _load_book_from_file(self, file_path: Path) -> Optional[StoredBook]:
        """从单个 JSON 文件加载书籍（内部方法）"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 向后兼容：处理旧格式数据
            data = self._migrate_old_format(data)
            
            # Pydantic 验证
            return StoredBook.model_validate(data)
            
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in {file_path}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error loading {file_path}: {e}")
            return None
    
    def _migrate_old_format(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        迁移旧格式数据到新格式

        支持的旧格式:
        - 直接包含 book_id, title 等字段的扁平结构
        - 不含 metadata 包装器的结构
        - 不含 page_number 的章节结构
        - 不含 user_marks 的结构
        """
        # 如果已经是新格式，添加缺失的字段
        if "metadata" in data and "chapters" in data:
            # 检查章节是否有 page_number 和 is_user_mark
            for ch in data.get("chapters", []):
                if "page_number" not in ch:
                    ch["page_number"] = 1
                if "end_page" not in ch:
                    ch["end_page"] = 1
                if "is_user_mark" not in ch:
                    ch["is_user_mark"] = False
                if "mark_id" not in ch:
                    ch["mark_id"] = None
            # 添加 user_marks 字段（如果不存在）
            if "user_marks" not in data:
                data["user_marks"] = []
            return data
        
        # 处理旧格式：扁平结构
        logger.debug(f"Migrating old format data for book: {data.get('book_id', 'unknown')}")
        
        metadata = {
            "book_id": data.get("book_id", ""),
            "title": data.get("title", "Untitled"),
            "filename": data.get("filename", ""),
            "total_chapters": data.get("total_chapters", 0),
            "total_pages": data.get("total_pages", 0),
            "created_at": data.get("created_at", datetime.utcnow().isoformat()),
            "updated_at": data.get("updated_at", datetime.utcnow().isoformat()),
            "version": "1.1-migrated"
        }
        
        chapters = data.get("chapters", [])
        # 为旧章节添加 page_number
        for ch in chapters:
            if "page_number" not in ch:
                ch["page_number"] = ch.get("index", 1)
            if "end_page" not in ch:
                ch["end_page"] = ch.get("index", 1)
            if "is_user_mark" not in ch:
                ch["is_user_mark"] = False
            if "mark_id" not in ch:
                ch["mark_id"] = None

        return {
            "metadata": metadata,
            "chapters": chapters,
            "user_marks": []
        }
    
    async def save_book(self, book_data: BookData) -> StoredBook:
        """
        保存书籍到存储
        
        Args:
            book_data: 书籍数据对象
            
        Returns:
            StoredBook: 保存后的书籍模型
            
        Raises:
            ValueError: 数据验证失败
            IOError: 磁盘写入失败
        """
        # 转换为存储模型
        stored_book = StoredBook.from_book_data(book_data)
        
        # 更新时间戳
        stored_book.metadata.updated_at = datetime.utcnow()
        
        async with self._lock:
            # 更新缓存
            self._cache[stored_book.metadata.book_id] = stored_book
            
            # 写入磁盘
            await self._write_to_disk(stored_book)
        
        logger.info(f"Book saved: {stored_book.metadata.book_id} - {stored_book.metadata.title}")
        return stored_book
    
    async def _write_to_disk(self, book: StoredBook) -> None:
        """将书籍写入磁盘 JSON 文件（内部方法，需在锁保护下调用）"""
        file_path = self.storage_path / f"{book.metadata.book_id}.json"
        
        try:
            # 使用 model_dump 进行序列化，处理 datetime 等特殊类型
            data = book.model_dump(mode='json')
            
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                
        except Exception as e:
            logger.error(f"Failed to write book {book.metadata.book_id} to disk: {e}")
            raise IOError(f"Failed to save book to disk: {e}")
    
    async def get_book(self, book_id: str) -> Optional[StoredBook]:
        """
        获取单本书籍
        
        Args:
            book_id: 书籍唯一标识
            
        Returns:
            StoredBook 或 None（如果不存在）
        """
        async with self._lock:
            # 先从缓存获取
            if book_id in self._cache:
                return self._cache[book_id]
            
            # 缓存未命中，从磁盘加载
            file_path = self.storage_path / f"{book_id}.json"
            if file_path.exists():
                book = await self._load_book_from_file(file_path)
                if book:
                    self._cache[book_id] = book
                    return book
            
            return None
    
    async def get_book_data(self, book_id: str) -> Optional[BookData]:
        """
        获取书籍数据（返回 BookData 格式，向后兼容）
        
        Args:
            book_id: 书籍唯一标识
            
        Returns:
            BookData 或 None（如果不存在）
        """
        stored = await self.get_book(book_id)
        return stored.to_book_data() if stored else None
    
    async def list_books(self) -> List[BookListItem]:
        """
        列出所有书籍
        
        Returns:
            书籍列表（不含章节内容）
        """
        async with self._lock:
            books = []
            for book in self._cache.values():
                books.append(BookListItem(
                    book_id=book.metadata.book_id,
                    title=book.metadata.title,
                    total_chapters=book.metadata.total_chapters,
                    total_pages=book.metadata.total_pages,
                    created_at=book.metadata.created_at
                ))
            return books
    
    async def delete_book(self, book_id: str) -> Dict[str, Any]:
        """
        删除书籍及其关联文件
        
        Args:
            book_id: 书籍唯一标识
            
        Returns:
            Dict: 删除结果，包含:
                - success: bool 是否成功删除
                - error: Optional[str] 错误信息
                - deleted_files: List[str] 已删除的文件列表
        """
        async with self._lock:
            # 从缓存移除
            if book_id in self._cache:
                del self._cache[book_id]
            
            result = {
                "success": True,
                "error": None,
                "deleted_files": []
            }
            
            # 删除 JSON 元数据文件
            json_file = self.storage_path / f"{book_id}.json"
            if json_file.exists():
                try:
                    json_file.unlink()
                    result["deleted_files"].append(str(json_file))
                    logger.info(f"Deleted metadata file: {json_file}")
                except Exception as e:
                    error_msg = f"Failed to delete metadata file: {e}"
                    logger.error(error_msg)
                    result["success"] = False
                    result["error"] = error_msg
                    return result
            
            # 删除 PDF 文件（带重试机制，处理文件被占用的情况）
            pdf_file = self.upload_dir / f"{book_id}.pdf"
            if pdf_file.exists():
                max_retries = 5
                retry_delay = 0.5  # 秒
                
                for attempt in range(max_retries):
                    try:
                        pdf_file.unlink()
                        result["deleted_files"].append(str(pdf_file))
                        logger.info(f"Deleted PDF file: {pdf_file}")
                        break
                    except PermissionError as e:
                        # 文件被占用，等待后重试
                        if attempt < max_retries - 1:
                            logger.warning(f"PDF file is locked (attempt {attempt + 1}/{max_retries}), retrying in {retry_delay}s...")
                            await asyncio.sleep(retry_delay)
                            retry_delay *= 1.5  # 指数退避
                        else:
                            error_msg = f"PDF file is currently in use and cannot be deleted. Please wait a moment and try again."
                            logger.error(f"Failed to delete PDF file after {max_retries} attempts: {e}")
                            result["success"] = False
                            result["error"] = error_msg
                            return result
                    except Exception as e:
                        error_msg = f"Failed to delete PDF file: {e}"
                        logger.error(error_msg)
                        result["success"] = False
                        result["error"] = error_msg
                        return result
            
            return result
    
    async def book_exists(self, book_id: str) -> bool:
        """
        检查书籍是否存在
        
        Args:
            book_id: 书籍唯一标识
            
        Returns:
            bool: 是否存在
        """
        async with self._lock:
            if book_id in self._cache:
                return True
            json_file = self.storage_path / f"{book_id}.json"
            return json_file.exists()
    
    async def get_stats(self) -> Dict[str, Any]:
        """
        获取存储统计信息
        
        Returns:
            统计信息字典
        """
        async with self._lock:
            total_books = len(self._cache)
            total_chapters = sum(b.metadata.total_chapters for b in self._cache.values())
            total_pages = sum(b.metadata.total_pages for b in self._cache.values())
            
            # 计算存储大小
            storage_size = 0
            if self.storage_path.exists():
                for f in self.storage_path.glob("*.json"):
                    storage_size += f.stat().st_size
            
            pdf_size = 0
            if self.upload_dir.exists():
                for f in self.upload_dir.glob("*.pdf"):
                    pdf_size += f.stat().st_size
            
            return {
                "total_books": total_books,
                "total_chapters": total_chapters,
                "total_pages": total_pages,
                "metadata_storage_bytes": storage_size,
                "pdf_storage_bytes": pdf_size,
                "total_storage_bytes": storage_size + pdf_size
            }
    
    async def verify_consistency(self) -> List[Dict[str, Any]]:
        """
        验证 PDF 文件和 JSON 元数据的一致性
        
        Returns:
            问题列表，每个问题包含类型和描述
        """
        issues = []
        
        async with self._lock:
            # 检查有 JSON 但没有 PDF 的情况
            for book_id in self._cache.keys():
                pdf_file = self.upload_dir / f"{book_id}.pdf"
                if not pdf_file.exists():
                    issues.append({
                        "type": "missing_pdf",
                        "book_id": book_id,
                        "description": f"Metadata exists but PDF file is missing"
                    })
            
            # 检查有 PDF 但没有 JSON 的情况
            if self.upload_dir.exists():
                for pdf_file in self.upload_dir.glob("*.pdf"):
                    book_id = pdf_file.stem
                    json_file = self.storage_path / f"{book_id}.json"
                    if not json_file.exists():
                        issues.append({
                            "type": "orphan_pdf",
                            "book_id": book_id,
                            "description": f"PDF file exists but metadata is missing"
                        })
        
        if issues:
            logger.warning(f"Found {len(issues)} consistency issues: {issues}")
        else:
            logger.info("Storage consistency check passed")

        return issues

    # ==================== Chapter Mark Operations ====================

    async def add_user_mark(self, book_id: str, mark: UserMarkModel) -> Optional[StoredBook]:
        """
        添加用户章节标记

        Args:
            book_id: 书籍唯一标识
            mark: 用户标记数据

        Returns:
            更新后的书籍对象，如果书籍不存在则返回 None
            如果标记重复（同页同位置），返回已存在的书籍对象而不添加
        """
        async with self._lock:
            book = await self._get_book_from_cache_or_disk(book_id)
            if not book:
                return None

            # 检查重复标记（同页同位置，y_position 差值 < 0.05 视为重复）
            DUPLICATE_THRESHOLD = 0.05
            for existing_mark in book.user_marks:
                if (existing_mark.page_number == mark.page_number and
                    abs(existing_mark.y_position - mark.y_position) < DUPLICATE_THRESHOLD):
                    logger.warning(f"Duplicate mark detected for book {book_id}: "
                                   f"page {mark.page_number}, y={mark.y_position}. "
                                   f"Existing mark at y={existing_mark.y_position}")
                    return book  # 返回现有书籍，不添加重复标记

            # 添加标记
            book.user_marks.append(mark)
            book.user_marks.sort(key=lambda m: (m.page_number, m.y_position))

            # 更新时间戳
            book.metadata.updated_at = datetime.utcnow()

            # 保存到磁盘
            await self._write_to_disk(book)

            logger.info(f"User mark added to book {book_id}: page {mark.page_number}, y={mark.y_position}")
            return book

    async def remove_user_mark(self, book_id: str, mark_id: str) -> Optional[StoredBook]:
        """
        删除用户章节标记

        Args:
            book_id: 书籍唯一标识
            mark_id: 要删除的标记 ID

        Returns:
            更新后的书籍对象，如果书籍或标记不存在则返回 None
        """
        async with self._lock:
            book = await self._get_book_from_cache_or_disk(book_id)
            if not book:
                return None

            # 查找并删除标记
            original_count = len(book.user_marks)
            book.user_marks = [m for m in book.user_marks if m.mark_id != mark_id]

            if len(book.user_marks) == original_count:
                logger.warning(f"Mark {mark_id} not found in book {book_id}")
                return None

            # 更新时间戳
            book.metadata.updated_at = datetime.utcnow()

            # 保存到磁盘
            await self._write_to_disk(book)

            logger.info(f"User mark {mark_id} removed from book {book_id}")
            return book

    async def update_book_chapters(self, book_id: str, chapters: List[ChapterModel]) -> Optional[StoredBook]:
        """
        更新书籍的章节列表（用于重新分段后）

        Args:
            book_id: 书籍唯一标识
            chapters: 新的章节列表

        Returns:
            更新后的书籍对象，如果书籍不存在则返回 None
        """
        async with self._lock:
            book = await self._get_book_from_cache_or_disk(book_id)
            if not book:
                return None

            # 更新章节
            book.chapters = chapters
            book.metadata.total_chapters = len(chapters)
            book.metadata.updated_at = datetime.utcnow()

            # 保存到磁盘
            await self._write_to_disk(book)

            logger.info(f"Chapters updated for book {book_id}: {len(chapters)} chapters")
            return book

    async def update_page_offset(self, book_id: str, offset: int) -> Optional[StoredBook]:
        """
        更新书籍的页码偏移量（用于页码校准）

        Args:
            book_id: 书籍唯一标识
            offset: 页码偏移量（正数表示 PDF 页码比实际页码大，负数表示 PDF 页码比实际页码小）

        Returns:
            更新后的书籍对象，如果书籍不存在则返回 None
        """
        async with self._lock:
            book = await self._get_book_from_cache_or_disk(book_id)
            if not book:
                return None

            # 更新页码偏移
            book.metadata.page_offset = offset
            book.metadata.updated_at = datetime.utcnow()

            # 保存到磁盘
            await self._write_to_disk(book)

            logger.info(f"Page offset updated for book {book_id}: offset={offset}")
            return book

    async def _get_book_from_cache_or_disk(self, book_id: str) -> Optional[StoredBook]:
        """从缓存或磁盘获取书籍（内部方法，需在锁保护下调用）"""
        if book_id in self._cache:
            return self._cache[book_id]

        file_path = self.storage_path / f"{book_id}.json"
        if file_path.exists():
            book = await self._load_book_from_file(file_path)
            if book:
                self._cache[book_id] = book
                return book

        return None


# 全局存储实例（单例模式）
_storage_instance: Optional[BookStorage] = None


async def get_storage() -> BookStorage:
    """
    获取全局存储实例（单例）
    
    注意：首次使用前必须先调用 initialize()
    """
    global _storage_instance
    if _storage_instance is None:
        from config import get_settings
        settings = get_settings()
        _storage_instance = BookStorage(
            storage_path=settings.BOOKS_STORAGE_PATH,
            upload_dir=settings.UPLOAD_DIR
        )
    return _storage_instance


async def init_storage() -> BookStorage:
    """
    初始化并返回存储实例
    
    在应用启动时调用
    """
    storage = await get_storage()
    await storage.initialize()
    return storage
