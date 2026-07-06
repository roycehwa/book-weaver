"""
BookMate Reading Progress Module
阅读进度存储和管理
"""
import os
import json
import asyncio
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, asdict
from pydantic import BaseModel, Field

from config import get_settings

logger = logging.getLogger(__name__)


class ReadingProgressModel(BaseModel):
    """阅读进度数据模型"""
    book_id: str = Field(..., description="书籍 ID")
    page_number: int = Field(default=1, ge=1, description="当前阅读页码")
    chapter_index: Optional[int] = Field(default=None, description="当前章节索引")
    last_read: str = Field(default_factory=lambda: datetime.utcnow().isoformat(), 
                            description="最后阅读时间戳 (ISO 格式)")
    total_reading_time_minutes: int = Field(default=0, ge=0, 
                                             description="累计阅读时间（分钟）")
    reading_percentage: float = Field(default=0.0, ge=0.0, le=100.0,
                                       description="阅读进度百分比")


@dataclass
class ReadingProgress:
    """阅读进度数据类"""
    book_id: str
    page_number: int = 1
    chapter_index: Optional[int] = None
    last_read: str = ""
    total_reading_time_minutes: int = 0
    reading_percentage: float = 0.0
    
    def __post_init__(self):
        if not self.last_read:
            self.last_read = datetime.utcnow().isoformat()


class ProgressStorage:
    """阅读进度存储管理器"""
    
    def __init__(self, storage_path: str):
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._cache: Dict[str, ReadingProgress] = {}
        self._initialized = False
    
    async def initialize(self) -> None:
        """初始化存储"""
        if self._initialized:
            return
        
        async with self._lock:
            await self._load_all()
            self._initialized = True
        
        logger.info(f"ProgressStorage initialized with {len(self._cache)} records")
    
    async def _load_all(self) -> None:
        """加载所有进度记录"""
        if not self.storage_path.exists():
            return
        
        for json_file in self.storage_path.glob("*.json"):
            try:
                book_id = json_file.stem
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                progress = ReadingProgress(**data)
                self._cache[book_id] = progress
            except Exception as e:
                logger.error(f"Failed to load progress from {json_file}: {e}")
    
    def _get_file_path(self, book_id: str) -> Path:
        """获取进度文件路径"""
        return self.storage_path / f"{book_id}.json"
    
    async def save_progress(self, progress: ReadingProgress) -> ReadingProgress:
        """
        保存阅读进度
        
        Args:
            progress: 阅读进度对象
            
        Returns:
            保存后的阅读进度
        """
        progress.last_read = datetime.utcnow().isoformat()
        
        async with self._lock:
            self._cache[progress.book_id] = progress
            
            file_path = self._get_file_path(progress.book_id)
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(asdict(progress), f, ensure_ascii=False, indent=2)
                logger.info(f"Progress saved for book {progress.book_id}")
            except Exception as e:
                logger.error(f"Failed to save progress: {e}")
                raise
        
        return progress
    
    async def get_progress(self, book_id: str) -> Optional[ReadingProgress]:
        """
        获取阅读进度
        
        Args:
            book_id: 书籍 ID
            
        Returns:
            阅读进度对象，不存在则返回 None
        """
        async with self._lock:
            # 从缓存获取
            if book_id in self._cache:
                return self._cache[book_id]
            
            # 从磁盘加载
            file_path = self._get_file_path(book_id)
            if file_path.exists():
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    progress = ReadingProgress(**data)
                    self._cache[book_id] = progress
                    return progress
                except Exception as e:
                    logger.error(f"Failed to load progress for {book_id}: {e}")
            
            return None
    
    async def update_progress(self, book_id: str, page_number: int,
                              chapter_index: Optional[int] = None,
                              total_pages: Optional[int] = None) -> ReadingProgress:
        """
        更新阅读进度
        
        Args:
            book_id: 书籍 ID
            page_number: 当前页码
            chapter_index: 当前章节索引（可选）
            total_pages: 书籍总页数（用于计算百分比）
            
        Returns:
            更新后的阅读进度
        """
        progress = await self.get_progress(book_id)
        
        if progress is None:
            progress = ReadingProgress(book_id=book_id)
        
        progress.page_number = page_number
        if chapter_index is not None:
            progress.chapter_index = chapter_index
        
        if total_pages and total_pages > 0:
            progress.reading_percentage = round((page_number / total_pages) * 100, 2)
        
        return await self.save_progress(progress)
    
    async def list_all_progress(self) -> List[ReadingProgress]:
        """
        列出所有阅读进度
        
        Returns:
            阅读进度列表，按最后阅读时间倒序
        """
        async with self._lock:
            progress_list = list(self._cache.values())
        
        # 按最后阅读时间倒序
        progress_list.sort(key=lambda x: x.last_read, reverse=True)
        return progress_list
    
    async def delete_progress(self, book_id: str) -> bool:
        """
        删除阅读进度
        
        Args:
            book_id: 书籍 ID
            
        Returns:
            是否成功删除
        """
        async with self._lock:
            if book_id in self._cache:
                del self._cache[book_id]
            
            file_path = self._get_file_path(book_id)
            if file_path.exists():
                try:
                    file_path.unlink()
                    logger.info(f"Progress deleted for book {book_id}")
                    return True
                except Exception as e:
                    logger.error(f"Failed to delete progress: {e}")
            
            return False


# 全局存储实例
_progress_storage: Optional[ProgressStorage] = None


async def get_progress_storage() -> ProgressStorage:
    """获取阅读进度存储单例"""
    global _progress_storage
    if _progress_storage is None:
        settings = get_settings()
        _progress_storage = ProgressStorage(
            storage_path=os.path.join(settings.CACHE_DIR, "progress")
        )
    return _progress_storage


async def init_progress_storage() -> ProgressStorage:
    """初始化阅读进度存储"""
    storage = await get_progress_storage()
    await storage.initialize()
    return storage
