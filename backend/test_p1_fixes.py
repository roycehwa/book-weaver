#!/usr/bin/env python3
"""
TDD Tests for P1 Fixes:
1. Route path conflict: DELETE /books/{book_id}/marks/{mark_id} 
2. y_position parameter validation (0 <= y_position <= 1)
"""
import pytest
import sys
import os

# 确保能导入 backend 模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.services.chapter_mark_service import ChapterMarkService
from unittest.mock import AsyncMock, MagicMock


# ============ Test 1: y_position parameter validation ============

class TestYPositionValidation:
    """测试 y_position 参数校验"""

    @pytest.fixture
    def mock_service(self):
        """创建带有 mock storage 的 service"""
        mock_storage = AsyncMock()
        service = ChapterMarkService(mock_storage)
        return service, mock_storage

    @pytest.mark.asyncio
    async def test_y_position_negative_should_raise(self, mock_service):
        """测试 y_position < 0 应该抛出 ValueError"""
        service, mock_storage = mock_service
        
        # Mock book data
        mock_book = MagicMock()
        mock_book.metadata.total_pages = 100
        mock_storage.get_book.return_value = mock_book
        
        # 测试 y_position = -0.1 应该抛出 ValueError
        with pytest.raises(ValueError, match="y_position must be between 0 and 1"):
            await service.create_mark(
                book_id="test-book",
                page_number=1,
                y_position=-0.1,
                chapter_name="Test"
            )

    @pytest.mark.asyncio
    async def test_y_position_greater_than_1_should_raise(self, mock_service):
        """测试 y_position > 1 应该抛出 ValueError"""
        service, mock_storage = mock_service
        
        # Mock book data
        mock_book = MagicMock()
        mock_book.metadata.total_pages = 100
        mock_storage.get_book.return_value = mock_book
        
        # 测试 y_position = 1.5 应该抛出 ValueError
        with pytest.raises(ValueError, match="y_position must be between 0 and 1"):
            await service.create_mark(
                book_id="test-book",
                page_number=1,
                y_position=1.5,
                chapter_name="Test"
            )

    @pytest.mark.asyncio
    async def test_y_position_zero_should_be_valid(self, mock_service):
        """测试 y_position = 0 是有效的"""
        service, mock_storage = mock_service
        
        # Mock book data
        mock_book = MagicMock()
        mock_book.metadata.total_pages = 100
        mock_book.user_marks = []
        mock_storage.get_book.return_value = mock_book
        mock_storage.add_user_mark.return_value = mock_book
        
        # 测试 y_position = 0 应该成功
        mark, _ = await service.create_mark(
            book_id="test-book",
            page_number=1,
            y_position=0.0,
            chapter_name="Test"
        )
        
        assert mark is not None
        assert mark.y_position == 0.0

    @pytest.mark.asyncio
    async def test_y_position_one_should_be_valid(self, mock_service):
        """测试 y_position = 1 是有效的"""
        service, mock_storage = mock_service
        
        # Mock book data
        mock_book = MagicMock()
        mock_book.metadata.total_pages = 100
        mock_book.user_marks = []
        mock_storage.get_book.return_value = mock_book
        mock_storage.add_user_mark.return_value = mock_book
        
        # 测试 y_position = 1.0 应该成功
        mark, _ = await service.create_mark(
            book_id="test-book",
            page_number=1,
            y_position=1.0,
            chapter_name="Test"
        )
        
        assert mark is not None
        assert mark.y_position == 1.0

    @pytest.mark.asyncio
    async def test_y_position_half_should_be_valid(self, mock_service):
        """测试 y_position = 0.5 是有效的"""
        service, mock_storage = mock_service
        
        # Mock book data
        mock_book = MagicMock()
        mock_book.metadata.total_pages = 100
        mock_book.user_marks = []
        mock_storage.get_book.return_value = mock_book
        mock_storage.add_user_mark.return_value = mock_book
        
        # 测试 y_position = 0.5 应该成功
        mark, _ = await service.create_mark(
            book_id="test-book",
            page_number=1,
            y_position=0.5,
            chapter_name="Test"
        )
        
        assert mark is not None
        assert mark.y_position == 0.5


# ============ Test 2: Route path conflict ============

class TestRoutePathConflict:
    """测试路由路径冲突修复"""
    
    def test_delete_mark_endpoint_path(self):
        """验证删除标记端点应该使用 /marks/{mark_id} 路径"""
        # 读取 main.py 文件
        with open(os.path.join(os.path.dirname(__file__), 'main.py'), 'r') as f:
            content = f.read()
        
        # 检查应该存在新的路径模式 /marks/{mark_id}
        assert '@api_router.delete("/books/{book_id}/marks/{mark_id}"' in content, \
            "DELETE endpoint should use /books/{book_id}/marks/{mark_id} path"
        
        # 检查不应该再存在旧的路径模式 /chapters/{mark_id} 用于 DELETE
        # 注意：GET /books/{book_id}/chapters/{chapter_index} 仍然是有效的
        # 所以只检查 DELETE 操作符下面的路径
        lines = content.split('\n')
        in_delete_handler = False
        
        for i, line in enumerate(lines):
            if '@api_router.delete("/books/{book_id}/chapters/{mark_id}"' in line:
                pytest.fail(f"Found old conflicting DELETE route at line {i+1}: {line}")

    def test_get_chapter_endpoint_still_exists(self):
        """验证获取单章端点仍然存在（不应该被破坏）"""
        with open(os.path.join(os.path.dirname(__file__), 'main.py'), 'r') as f:
            content = f.read()
        
        # 验证 GET /books/{book_id}/chapters/{chapter_index} 仍然存在
        assert '@api_router.get("/books/{book_id}/chapters/{chapter_index}"' in content, \
            "GET chapter endpoint should still exist"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
