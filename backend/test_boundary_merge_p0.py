#!/usr/bin/env python3
"""
P0 边界合并逻辑单元测试
测试场景：同一页有原始章节和用户标记时的标题保留逻辑
"""
import sys
import os
import unittest
import asyncio
import types
import importlib

# ============= 前置：mock fitz 模块（必须在导入服务之前）=============
from unittest.mock import MagicMock

mock_fitz = types.ModuleType('fitz')
mock_doc = MagicMock()
mock_doc.__len__ = MagicMock(return_value=10)
mock_doc.close = MagicMock()
mock_fitz.open = MagicMock(return_value=mock_doc)
mock_fitz.Rect = MagicMock(return_value=MagicMock())
mock_fitz.Document = MagicMock
_ORIGINAL_FITZ_MODULE = importlib.import_module("fitz")
_ORIGINAL_FITZ_SUBMODULE = sys.modules.get("fitz.fitz")

# 添加项目路径（延迟导入服务，避免在 pytest 收集阶段污染全局 fitz）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dataclasses import dataclass
from typing import Optional, List


def tearDownModule():
    """恢复全局 fitz 模块，避免污染后续 pytest 用例。"""
    sys.modules["fitz"] = _ORIGINAL_FITZ_MODULE
    if _ORIGINAL_FITZ_SUBMODULE is not None:
        sys.modules["fitz.fitz"] = _ORIGINAL_FITZ_SUBMODULE


def _install_mock_fitz() -> None:
    sys.modules["fitz"] = mock_fitz


def _restore_fitz() -> None:
    sys.modules["fitz"] = _ORIGINAL_FITZ_MODULE
    if _ORIGINAL_FITZ_SUBMODULE is not None:
        sys.modules["fitz.fitz"] = _ORIGINAL_FITZ_SUBMODULE


@dataclass
class MockUserMark:
    """模拟用户标记"""
    mark_id: str
    page_number: int
    y_position: float
    chapter_name: Optional[str] = None


@dataclass
class MockChapter:
    """模拟原始章节"""
    index: int
    title: str
    page_number: int
    content: str = ""


class TestBoundaryMergeLogic(unittest.TestCase):
    """测试边界合并逻辑"""

    @classmethod
    def setUpClass(cls):
        _install_mock_fitz()
        module = importlib.import_module("app.services.chapter_mark_service")
        cls._chapter_mark_service_module = importlib.reload(module)
        cls.ChapterMarkService = cls._chapter_mark_service_module.ChapterMarkService

    @classmethod
    def tearDownClass(cls):
        _restore_fitz()
    
    def setUp(self):
        """每个测试前创建新的服务实例"""
        mock_storage = MagicMock()
        self.service = self.ChapterMarkService(mock_storage)
        # Mock 文本提取方法
        self.service._extract_text_between = MagicMock(return_value="Mocked content")
        # 重置 mock
        mock_doc.__len__ = MagicMock(return_value=10)
    
    def run_async(self, coro):
        """运行异步函数"""
        return asyncio.run(coro)
    
    def test_user_mark_without_name_should_inherit_original_title(self):
        """
        测试场景1：用户标记未提供章节名称时，应继承原始章节标题
        
        条件：
        - 原始章节在第5页，标题为"原始章节标题"
        - 用户标记也在第5页（相近位置），chapter_name=None
        
        期望：
        - 合并后使用用户标记的边界
        - 但标题应使用"原始章节标题"（而非默认的"Chapter at Page X"）
        """
        # 准备数据
        original_chapters = [
            MockChapter(index=0, title="原始章节标题", page_number=5),
        ]
        
        user_marks = [
            MockUserMark(
                mark_id="mark_001",
                page_number=5,
                y_position=0.02,  # 与原始章节(y=0)距离 < 0.05，会被合并
                chapter_name=None  # 用户未提供名称
            ),
        ]
        
        # 调用被测试的方法
        chapters = self.run_async(self.service._extract_chapters_with_marks(
            pdf_path="dummy.pdf",
            total_pages=10,
            user_marks=user_marks,
            original_chapters=original_chapters
        ))
        
        # 验证：应该只有一个章节
        self.assertEqual(len(chapters), 1, f"Expected 1 chapter, got {len(chapters)}")
        
        # 验证：标题应该使用原始章节标题（关键修复点）
        self.assertEqual(chapters[0].title, "原始章节标题",
            f"Expected title '原始章节标题', got '{chapters[0].title}'")
        
        # 验证：标记信息保留
        self.assertTrue(chapters[0].is_user_mark)
        self.assertEqual(chapters[0].mark_id, "mark_001")
    
    def test_user_mark_with_name_should_override_original_title(self):
        """
        测试场景2：用户标记提供了章节名称时，应使用用户提供的名称
        
        条件：
        - 原始章节在第5页，标题为"原始章节标题"
        - 用户标记也在第5页（相近位置），chapter_name="用户自定义标题"
        
        期望：
        - 使用用户提供的标题
        """
        original_chapters = [
            MockChapter(index=0, title="原始章节标题", page_number=5),
        ]
        
        user_marks = [
            MockUserMark(
                mark_id="mark_002",
                page_number=5,
                y_position=0.02,
                chapter_name="用户自定义标题"
            ),
        ]
        
        chapters = self.run_async(self.service._extract_chapters_with_marks(
            pdf_path="dummy.pdf",
            total_pages=10,
            user_marks=user_marks,
            original_chapters=original_chapters
        ))
        
        # 验证：标题应该使用用户提供的标题
        self.assertEqual(chapters[0].title, "用户自定义标题",
            f"Expected title '用户自定义标题', got '{chapters[0].title}'")
    
    def test_far_boundaries_should_not_merge(self):
        """
        测试场景3：距离较远的边界不应被合并
        
        条件：
        - 原始章节在第5页 y=0
        - 用户标记在第5页 y=0.5（距离 > 0.05）
        
        期望：
        - 生成两个独立的章节
        """
        original_chapters = [
            MockChapter(index=0, title="原始章节", page_number=5),
        ]
        
        user_marks = [
            MockUserMark(
                mark_id="mark_003",
                page_number=5,
                y_position=0.5,  # 距离足够远
                chapter_name="用户章节"
            ),
        ]
        
        chapters = self.run_async(self.service._extract_chapters_with_marks(
            pdf_path="dummy.pdf",
            total_pages=10,
            user_marks=user_marks,
            original_chapters=original_chapters
        ))
        
        # 验证：应该有两个章节
        self.assertEqual(len(chapters), 2, f"Expected 2 chapters, got {len(chapters)}")
    
    def test_original_title_backup_inheritance(self):
        """
        测试场景4：验证原始标题备份机制
        
        这是核心修复点：当用户标记替换原始边界时，
        应该保留原始标题作为备选
        """
        mock_doc.__len__ = MagicMock(return_value=20)
        
        # 使用更复杂的数据结构测试边界合并逻辑
        original_chapters = [
            MockChapter(index=0, title="第一章：引言", page_number=3),
            MockChapter(index=1, title="第二章：方法", page_number=10),
        ]
        
        user_marks = [
            MockUserMark(
                mark_id="mark_004",
                page_number=3,
                y_position=0.01,  # 非常接近第一章
                chapter_name=None  # 无自定义名称
            ),
            MockUserMark(
                mark_id="mark_005",
                page_number=10,
                y_position=0.02,  # 非常接近第二章
                chapter_name="自定义第二章"  # 有自定义名称
            ),
        ]
        
        chapters = self.run_async(self.service._extract_chapters_with_marks(
            pdf_path="dummy.pdf",
            total_pages=20,
            user_marks=user_marks,
            original_chapters=original_chapters
        ))
        
        # 找到对应章节并验证标题
        chapters_at_page3 = [c for c in chapters if c.page_number == 3]
        chapters_at_page10 = [c for c in chapters if c.page_number == 10]
        
        self.assertTrue(len(chapters_at_page3) > 0, "Should have chapter at page 3")
        self.assertTrue(len(chapters_at_page10) > 0, "Should have chapter at page 10")
        
        # 验证：无自定义名称的使用原始标题
        self.assertEqual(chapters_at_page3[0].title, "第一章：引言",
            f"Expected '第一章：引言', got '{chapters_at_page3[0].title}'")
        
        # 验证：有自定义名称的使用自定义标题
        self.assertEqual(chapters_at_page10[0].title, "自定义第二章",
            f"Expected '自定义第二章', got '{chapters_at_page10[0].title}'")


class TestBoundaryMergeCoreLogic(unittest.TestCase):
    """直接测试边界合并核心逻辑（不依赖服务实例）"""
    
    def test_old_logic_loses_original_title(self):
        """
        验证当前有问题的逻辑确实会丢失原始标题
        """
        # 模拟边界列表
        boundaries = [
            {"page": 5, "y": 0, "type": "original", "title": "原始标题"},
            {"page": 5, "y": 0.02, "type": "user_mark", "mark_id": "m1", "title": "Chapter at Page 5"},
        ]
        
        # 模拟当前的过滤逻辑（这是有问题的版本）
        filtered_boundaries = []
        for b in boundaries:
            if not filtered_boundaries:
                filtered_boundaries.append(b)
            else:
                last = filtered_boundaries[-1]
                if b["page"] == last["page"] and abs(b["y"] - last["y"]) < 0.05:
                    # 用户标记优先 - 但会丢失原始标题
                    if b["type"] == "user_mark":
                        filtered_boundaries[-1] = b
                else:
                    filtered_boundaries.append(b)
        
        # 验证：当前逻辑确实会丢失原始标题
        self.assertEqual(filtered_boundaries[0]["title"], "Chapter at Page 5",
            "当前逻辑确实会丢失原始标题（这是预期的失败）")
    
    def test_new_logic_preserves_original_title(self):
        """
        验证修复后的新逻辑会保留原始标题
        """
        boundaries = [
            {"page": 5, "y": 0, "type": "original", "title": "原始标题"},
            {"page": 5, "y": 0.02, "type": "user_mark", "mark_id": "m1", "title": "Chapter at Page 5"},
        ]
        
        # 期望的新逻辑（修复后）
        filtered_boundaries = []
        for b in boundaries:
            if not filtered_boundaries:
                filtered_boundaries.append(b)
            else:
                last = filtered_boundaries[-1]
                if b["page"] == last["page"] and abs(b["y"] - last["y"]) < 0.05:
                    if b["type"] == "user_mark":
                        # 修复：保留原始标题作为备选
                        original_title = last["title"] if last["type"] == "original" else None
                        
                        # 构建合并后的边界
                        merged = b.copy()
                        # 如果用户没有提供章节名（使用的是默认标题），则使用原始标题
                        if b["title"].startswith("Chapter at Page") and original_title:
                            merged["title"] = original_title
                        merged["original_title_backup"] = original_title  # 保留备份
                        filtered_boundaries[-1] = merged
                else:
                    filtered_boundaries.append(b)
        
        # 验证：修复后的逻辑应保留原始标题
        self.assertEqual(filtered_boundaries[0]["title"], "原始标题",
            f"修复后应使用原始标题，但得到 '{filtered_boundaries[0]['title']}'")
        self.assertEqual(filtered_boundaries[0]["original_title_backup"], "原始标题")


if __name__ == "__main__":
    unittest.main(verbosity=2)
