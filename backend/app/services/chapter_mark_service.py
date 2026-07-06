"""
Chapter Mark Service - Phase 2 P0
处理用户章节标记的创建、删除和自动重新分段
"""
import uuid
import logging
from typing import List, Optional, Tuple
from datetime import datetime

import fitz  # PyMuPDF

from storage import (
    BookStorage, UserMarkModel, ChapterModel, StoredBook,
    get_storage
)
from pdf_parser import PDFParser

logger = logging.getLogger(__name__)


class ChapterMarkService:
    """
    章节标记服务

    主要职责：
    1. 创建用户标记
    2. 删除用户标记
    3. 根据用户标记自动重新分段
    """

    def __init__(self, storage: BookStorage):
        self.storage = storage

    async def create_mark(
        self,
        book_id: str,
        page_number: int,
        y_position: float,
        chapter_name: Optional[str] = None
    ) -> Tuple[Optional[UserMarkModel], Optional[List[ChapterModel]]]:
        """
        创建用户章节标记并触发重新分段

        Args:
            book_id: 书籍唯一标识
            page_number: 页码（1-based）
            y_position: 页面上的垂直位置（0-1 归一化）
            chapter_name: 可选的章节名称

        Returns:
            (新创建的标记, 重新分段后的章节列表)
        """
        # 获取书籍
        book = await self.storage.get_book(book_id)
        if not book:
            logger.error(f"Book not found: {book_id}")
            return None, None

        # 验证页码范围
        if page_number < 1 or page_number > book.metadata.total_pages:
            logger.error(f"Invalid page number: {page_number}, total: {book.metadata.total_pages}")
            return None, None

        # 验证 y_position 范围
        if not (0 <= y_position <= 1):
            raise ValueError("y_position must be between 0 and 1")

        # 检查重复标记（同页同位置，y_position 差值 < 0.05 视为重复）
        DUPLICATE_THRESHOLD = 0.05
        for existing_mark in book.user_marks:
            if (existing_mark.page_number == page_number and
                abs(existing_mark.y_position - y_position) < DUPLICATE_THRESHOLD):
                logger.warning(f"Duplicate mark detected for book {book_id}: "
                               f"page {page_number}, y={y_position}. "
                               f"Existing mark at y={existing_mark.y_position}")
                # 返回已存在的标记和当前章节列表，不创建新标记
                return existing_mark, book.chapters

        # 创建标记
        mark = UserMarkModel(
            mark_id=str(uuid.uuid4()),
            page_number=page_number,
            y_position=y_position,
            chapter_name=chapter_name,
            created_at=datetime.utcnow()
        )

        # 保存标记到存储
        updated_book = await self.storage.add_user_mark(book_id, mark)
        if not updated_book:
            logger.error(f"Failed to add mark to book {book_id}")
            return None, None

        # 触发重新分段
        new_chapters = await self._recalculate_chapters(book_id)

        logger.info(f"Mark created for book {book_id}: {mark.mark_id} at page {page_number}")
        return mark, new_chapters

    async def delete_mark(
        self,
        book_id: str,
        mark_id: str
    ) -> Tuple[bool, Optional[List[ChapterModel]]]:
        """
        删除用户章节标记并触发重新分段

        Args:
            book_id: 书籍唯一标识
            mark_id: 要删除的标记 ID

        Returns:
            (是否成功删除, 重新分段后的章节列表)
        """
        # 删除标记
        updated_book = await self.storage.remove_user_mark(book_id, mark_id)
        if not updated_book:
            logger.error(f"Failed to remove mark {mark_id} from book {book_id}")
            return False, None

        # 触发重新分段
        new_chapters = await self._recalculate_chapters(book_id)

        logger.info(f"Mark deleted from book {book_id}: {mark_id}")
        return True, new_chapters

    async def _recalculate_chapters(self, book_id: str) -> Optional[List[ChapterModel]]:
        """
        根据所有用户标记重新计算章节边界

        规则：
        1. 用户标记位置作为硬边界
        2. 重新计算该标记之后的所有章节边界
        3. 保留原始 TOC 提取的章节，但会根据用户标记进行分割

        Args:
            book_id: 书籍唯一标识

        Returns:
            新的章节列表
        """
        book = await self.storage.get_book(book_id)
        if not book:
            logger.error(f"Book not found for recalculation: {book_id}")
            return None

        # 如果没有用户标记，返回原始章节
        if not book.user_marks:
            return book.chapters

        # 获取 PDF 路径
        from config import get_settings
        settings = get_settings()
        pdf_path = f"{settings.UPLOAD_DIR}/{book_id}.pdf"

        try:
            # [FIX] 使用 original_chapters 作为原始章节，避免重复处理已分段的章节
            # 如果 original_chapters 为空（旧数据），则使用当前 chapters 并过滤掉用户标记
            if book.original_chapters:
                original_chapters = book.original_chapters
            else:
                # 向后兼容：过滤掉用户标记创建的章节
                original_chapters = [ch for ch in book.chapters if not getattr(ch, 'is_user_mark', False)]
            
            new_chapters = await self._extract_chapters_with_marks(
                pdf_path,
                book.metadata.total_pages,
                book.user_marks,
                original_chapters
            )

            # 更新存储
            await self.storage.update_book_chapters(book_id, new_chapters)

            logger.info(f"Chapters recalculated for book {book_id}: {len(new_chapters)} chapters")
            return new_chapters

        except Exception as e:
            logger.error(f"Failed to recalculate chapters for {book_id}: {e}")
            return book.chapters

    async def _extract_chapters_with_marks(
        self,
        pdf_path: str,
        total_pages: int,
        user_marks: List[UserMarkModel],
        original_chapters: List[ChapterModel]
    ) -> List[ChapterModel]:
        """
        根据用户标记和原生章节合并提取章节内容

        Args:
            pdf_path: PDF 文件路径
            total_pages: 总页数
            user_marks: 用户标记列表（已按位置排序）
            original_chapters: 原始章节列表

        Returns:
            合并后的章节列表（用户标记与原生章节共存）
        """
        doc = fitz.open(pdf_path)
        new_chapters = []
        chapter_index = 0

        try:
            # 修复 P0：改进边界合并策略
            # 策略：
            # 1. 用户标记作为硬边界，优先级最高
            # 2. 保留不冲突的原生章节
            # 3. 当用户标记接近原生章节时，使用用户标记位置但保留原生章节标题

            boundaries = []

            # 添加用户标记作为硬边界（最高优先级）
            for mark in user_marks:
                boundaries.append({
                    "page": mark.page_number,
                    "y": mark.y_position,
                    "type": "user_mark",
                    "mark_id": mark.mark_id,
                    "title": mark.chapter_name or f"章节 {mark.page_number}",
                    "priority": 2  # 用户标记优先级更高
                })

            # 添加原生章节边界
            for ch in original_chapters:
                boundaries.append({
                    "page": ch.page_number,
                    "y": 0.0,  # 章节通常从页面顶部开始
                    "type": "original",
                    "title": ch.title,
                    "priority": 1  # 原生章节优先级较低
                })

            # 按页码和位置排序
            boundaries.sort(key=lambda b: (b["page"], b["y"]))

            # [FIX] 改进的边界合并逻辑
            # 合并条件：
            # 1. 同一页且 y 差 < 0.5（放宽容差）
            # 2. 或者标题相同且在同一页（强制合并相同标题）
            filtered_boundaries = []
            for b in boundaries:
                if not filtered_boundaries:
                    filtered_boundaries.append(b)
                else:
                    last = filtered_boundaries[-1]
                    # 检查是否在同一页
                    is_same_page = b["page"] == last["page"]
                    # 检查位置是否接近（放宽到 0.5）
                    is_close_y = abs(b["y"] - last["y"]) < 0.5
                    # 检查标题是否相同（或高度相似）
                    is_same_title = b["title"].strip() == last["title"].strip()

                    if is_same_page and (is_close_y or is_same_title):
                        # 需要合并
                        # 策略：高优先级保留，但如果高优先级没有自定义标题，使用低优先级的标题
                        if b["priority"] > last["priority"]:
                            # 当前边界优先级更高，替换上一个
                            # 如果当前是用户标记但没有自定义章节名，继承原生章节标题
                            if b["type"] == "user_mark":
                                if (b["title"].startswith("章节") or b["title"].startswith("Chapter") or 
                                    b["title"].startswith("第 ") or len(b["title"]) < 5):
                                    b["title"] = last["title"]  # 继承原生章节标题
                            filtered_boundaries[-1] = b
                        else:
                            # 上一个优先级更高或相等，保留上一个
                            # 如果上一个没有自定义标题，使用当前标题
                            if last["type"] == "user_mark":
                                if (last["title"].startswith("章节") or last["title"].startswith("Chapter") or
                                    last["title"].startswith("第 ") or len(last["title"]) < 5):
                                    last["title"] = b["title"]
                        # [FIX] 重要：合并后不再添加新边界
                        continue
                    else:
                        # 位置不接近且标题不同，添加为新边界
                        filtered_boundaries.append(b)

            # 根据边界提取章节内容
            for i, boundary in enumerate(filtered_boundaries):
                start_page = boundary["page"]
                start_y = boundary["y"]

                # 确定结束位置
                if i < len(filtered_boundaries) - 1:
                    end_page = filtered_boundaries[i + 1]["page"]
                    end_y = filtered_boundaries[i + 1]["y"]
                else:
                    end_page = total_pages
                    end_y = 1.0

                # 提取文本内容
                content = self._extract_text_between(
                    doc, start_page, start_y, end_page, end_y
                )

                # 创建新章节
                chapter = ChapterModel(
                    index=chapter_index,
                    title=boundary["title"],
                    content=content,
                    page_number=start_page,
                    end_page=end_page,
                    is_user_mark=(boundary["type"] == "user_mark"),
                    mark_id=boundary.get("mark_id")
                )

                new_chapters.append(chapter)
                chapter_index += 1

            logger.info(f"Chapter merge complete: {len(original_chapters)} original + {len(user_marks)} user marks -> {len(new_chapters)} chapters")
            return new_chapters

        finally:
            doc.close()

    def _extract_text_between(
        self,
        doc: fitz.Document,
        start_page: int,
        start_y: float,
        end_page: int,
        end_y: float
    ) -> str:
        """
        提取指定范围内的文本

        Args:
            doc: PDF 文档对象
            start_page: 起始页码（1-based）
            start_y: 起始垂直位置（0-1 归一化）
            end_page: 结束页码（1-based）
            end_y: 结束垂直位置（0-1 归一化）

        Returns:
            提取的文本内容
        """
        content_parts = []

        for page_num in range(start_page, end_page + 1):
            if page_num > len(doc):
                break

            page = doc[page_num - 1]  # PyMuPDF 使用 0-based 索引
            page_rect = page.rect

            # 确定提取区域
            if page_num == start_page and page_num == end_page:
                # 起止在同一页
                clip_rect = fitz.Rect(
                    page_rect.x0,
                    page_rect.y0 + page_rect.height * start_y,
                    page_rect.x1,
                    page_rect.y0 + page_rect.height * end_y
                )
            elif page_num == start_page:
                # 起始页：从 start_y 到页面底部
                clip_rect = fitz.Rect(
                    page_rect.x0,
                    page_rect.y0 + page_rect.height * start_y,
                    page_rect.x1,
                    page_rect.y1
                )
            elif page_num == end_page:
                # 结束页：从页面顶部到 end_y
                clip_rect = fitz.Rect(
                    page_rect.x0,
                    page_rect.y0,
                    page_rect.x1,
                    page_rect.y0 + page_rect.height * end_y
                )
            else:
                # 中间页：整页
                clip_rect = page_rect

            # 提取文本
            text = page.get_text("text", clip=clip_rect)
            if text.strip():
                content_parts.append(text.strip())

        return "\n\n".join(content_parts)

    async def calibrate_page_offset(
        self,
        book_id: str,
        pdf_page: int,
        actual_page: int
    ) -> Tuple[bool, int, Optional[StoredBook]]:
        """
        校准页码偏移

        允许用户标记实际页码位置，计算并存储页码偏移量。
        偏移量 = PDF页码 - 实际页码

        Args:
            book_id: 书籍唯一标识
            pdf_page: 当前PDF显示的页码
            actual_page: 用户指定的实际页码（书籍印刷页码）

        Returns:
            (是否成功, 计算出的偏移量, 更新后的书籍对象)
        """
        book = await self.storage.get_book(book_id)
        if not book:
            logger.error(f"Book not found: {book_id}")
            return False, 0, None

        # 验证页码范围
        if pdf_page < 1 or pdf_page > book.metadata.total_pages:
            logger.error(f"Invalid PDF page number: {pdf_page}")
            return False, 0, None

        if actual_page < 1:
            logger.error(f"Invalid actual page number: {actual_page}")
            return False, 0, None

        # 计算偏移量：offset = pdf_page - actual_page
        # 例如：PDF显示第10页，但实际是第1页，则 offset = 9
        offset = pdf_page - actual_page

        # 更新存储
        updated_book = await self.storage.update_page_offset(book_id, offset)
        if not updated_book:
            logger.error(f"Failed to update page offset for book {book_id}")
            return False, 0, None

        logger.info(f"Page offset calibrated for book {book_id}: PDF page {pdf_page} = Actual page {actual_page}, offset={offset}")
        return True, offset, updated_book

    async def set_page_offset_direct(
        self,
        book_id: str,
        offset: int
    ) -> Tuple[bool, int, Optional[StoredBook]]:
        """
        直接设置页码偏移量

        Args:
            book_id: 书籍唯一标识
            offset: 页码偏移量（直接设置）

        Returns:
            (是否成功, 设置的偏移量, 更新后的书籍对象)
        """
        book = await self.storage.get_book(book_id)
        if not book:
            logger.error(f"Book not found: {book_id}")
            return False, 0, None

        # 更新存储
        updated_book = await self.storage.update_page_offset(book_id, offset)
        if not updated_book:
            logger.error(f"Failed to update page offset for book {book_id}")
            return False, 0, None

        logger.info(f"Page offset set directly for book {book_id}: offset={offset}")
        return True, offset, updated_book

    def convert_to_actual_page(self, pdf_page: int, offset: int) -> int:
        """
        将PDF页码转换为实际页码

        Args:
            pdf_page: PDF页码（1-based）
            offset: 页码偏移量

        Returns:
            实际页码
        """
        return pdf_page - offset

    def convert_to_pdf_page(self, actual_page: int, offset: int) -> int:
        """
        将实际页码转换为PDF页码

        Args:
            actual_page: 实际页码（1-based）
            offset: 页码偏移量

        Returns:
            PDF页码
        """
        return actual_page + offset


# 全局服务实例
_chapter_mark_service: Optional[ChapterMarkService] = None


async def get_chapter_mark_service() -> ChapterMarkService:
    """获取全局章节标记服务实例"""
    global _chapter_mark_service
    if _chapter_mark_service is None:
        storage = await get_storage()
        _chapter_mark_service = ChapterMarkService(storage)
    return _chapter_mark_service


async def init_chapter_mark_service() -> ChapterMarkService:
    """初始化章节标记服务"""
    return await get_chapter_mark_service()
