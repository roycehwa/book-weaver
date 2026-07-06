#!/usr/bin/env python3
"""
BookMate 章节修复测试脚本 - 从正确目录运行
测试场景：
1. 有原生目录的书籍添加用户标记
2. 用户标记与原生章节共存显示
3. 页码校准功能
每个场景测试10次
"""

import asyncio
import json
import sys
from typing import List, Dict, Any

from storage import init_storage, get_storage, UserMarkModel
from app.services.chapter_mark_service import init_chapter_mark_service, get_chapter_mark_service


class ChapterFixTester:
    """章节修复测试器"""

    def __init__(self):
        self.results = {
            "native_book_add_mark": [],
            "mixed_chapters_display": [],
            "page_calibration": []
        }

    async def setup(self):
        """初始化测试环境"""
        await init_storage()
        await init_chapter_mark_service()
        print("[TEST: 环境初始化 - 完成]")

    async def test_native_book_add_mark(self, test_round: int):
        """测试1: 有原生目录的书籍添加用户标记"""
        storage = await get_storage()
        mark_service = await get_chapter_mark_service()

        # 查找有原生章节的书籍
        books = await storage.list_books()
        book_with_chapters = None
        for book in books:
            book_data = await storage.get_book(book.book_id)
            if book_data and len(book_data.chapters) > 1:
                book_with_chapters = book_data
                break

        if not book_with_chapters:
            return {"round": test_round, "skipped": True, "reason": "No book with chapters found"}

        book_id = book_with_chapters.metadata.book_id

        # 在随机位置添加用户标记
        import random
        page = random.randint(1, min(5, book_with_chapters.metadata.total_pages))

        mark, new_chapters = await mark_service.create_mark(
            book_id=book_id,
            page_number=page,
            y_position=0.5,
            chapter_name=f"Test Mark Round {test_round}"
        )

        success = mark is not None and new_chapters is not None

        # 清理：删除测试标记
        if mark:
            await mark_service.delete_mark(book_id, mark.mark_id)

        return {
            "round": test_round,
            "success": success,
            "book_id": book_id,
            "page": page,
            "original_chapters": len(book_with_chapters.chapters),
            "new_chapters": len(new_chapters) if new_chapters else 0
        }

    async def test_mixed_chapters_display(self, test_round: int):
        """测试2: 用户标记与原生章节共存显示"""
        storage = await get_storage()
        mark_service = await get_chapter_mark_service()

        # 查找有原生章节的书籍
        books = await storage.list_books()
        book_with_chapters = None
        for book in books:
            book_data = await storage.get_book(book.book_id)
            if book_data and len(book_data.chapters) > 1:
                book_with_chapters = book_data
                break

        if not book_with_chapters:
            return {"round": test_round, "skipped": True, "reason": "No book with chapters found"}

        book_id = book_with_chapters.metadata.book_id

        # 添加多个用户标记
        import random
        marks_added = []
        for i in range(3):
            page = random.randint(1, min(10, book_with_chapters.metadata.total_pages))
            mark, _ = await mark_service.create_mark(
                book_id=book_id,
                page_number=page,
                y_position=0.3 + i * 0.2,
                chapter_name=f"Custom Mark {i+1}"
            )
            if mark:
                marks_added.append(mark)

        # 重新获取书籍数据
        updated_book = await storage.get_book(book_id)

        # 验证：应该同时包含原生章节和用户标记
        native_count = len([ch for ch in updated_book.chapters if not ch.is_user_mark])
        user_mark_count = len([ch for ch in updated_book.chapters if ch.is_user_mark])

        success = native_count > 0 and user_mark_count >= len(marks_added)

        # 清理
        for mark in marks_added:
            await mark_service.delete_mark(book_id, mark.mark_id)

        return {
            "round": test_round,
            "success": success,
            "book_id": book_id,
            "native_chapters": native_count,
            "user_mark_chapters": user_mark_count,
            "total_chapters": len(updated_book.chapters)
        }

    async def test_page_calibration(self, test_round: int):
        """测试3: 页码校准功能"""
        storage = await get_storage()
        mark_service = await get_chapter_mark_service()

        books = await storage.list_books()
        if not books:
            return {"round": test_round, "skipped": True, "reason": "No books found"}

        book_id = books[0].book_id
        book = await storage.get_book(book_id)

        # 测试页码校准
        pdf_page = min(10, book.metadata.total_pages)
        actual_page = 1  # 假设实际页码是1

        success, offset, updated_book = await mark_service.calibrate_page_offset(
            book_id=book_id,
            pdf_page=pdf_page,
            actual_page=actual_page
        )

        # 验证转换函数
        expected_offset = pdf_page - actual_page
        convert_test = mark_service.convert_to_actual_page(pdf_page, offset) == actual_page

        # 清理：重置偏移为0
        if success:
            await storage.update_page_offset(book_id, 0)

        return {
            "round": test_round,
            "success": success and convert_test,
            "book_id": book_id,
            "pdf_page": pdf_page,
            "actual_page": actual_page,
            "offset": offset,
            "expected_offset": expected_offset,
            "convert_test": convert_test
        }

    async def run_all_tests(self):
        """运行所有测试"""
        await self.setup()

        print("\n" + "="*60)
        print("开始章节修复测试 - 每个场景10次")
        print("="*60)

        # 测试场景1: 有原生目录的书籍添加用户标记
        print("\n[TEST: 场景1 - 有原生目录的书籍添加用户标记]")
        for i in range(1, 11):
            result = await self.test_native_book_add_mark(i)
            self.results["native_book_add_mark"].append(result)
            status = "✓ PASS" if result.get("success") else "✗ FAIL"
            if result.get("skipped"):
                status = "⊘ SKIP"
            print(f"  Round {i:2d}: {status} - {result}")

        # 测试场景2: 用户标记与原生章节共存
        print("\n[TEST: 场景2 - 用户标记与原生章节共存显示]")
        for i in range(1, 11):
            result = await self.test_mixed_chapters_display(i)
            self.results["mixed_chapters_display"].append(result)
            status = "✓ PASS" if result.get("success") else "✗ FAIL"
            if result.get("skipped"):
                status = "⊘ SKIP"
            print(f"  Round {i:2d}: {status} - Native: {result.get('native_chapters', 0)}, User: {result.get('user_mark_chapters', 0)}")

        # 测试场景3: 页码校准
        print("\n[TEST: 场景3 - 页码偏差调整]")
        for i in range(1, 11):
            result = await self.test_page_calibration(i)
            self.results["page_calibration"].append(result)
            status = "✓ PASS" if result.get("success") else "✗ FAIL"
            if result.get("skipped"):
                status = "⊘ SKIP"
            print(f"  Round {i:2d}: {status} - Offset: {result.get('offset', 'N/A')}")

        self.print_summary()

    def print_summary(self):
        """打印测试摘要"""
        print("\n" + "="*60)
        print("测试摘要")
        print("="*60)

        for scenario, results in self.results.items():
            total = len(results)
            passed = sum(1 for r in results if r.get("success"))
            skipped = sum(1 for r in results if r.get("skipped"))
            failed = total - passed - skipped

            print(f"\n{scenario}:")
            print(f"  总测试数: {total}")
            print(f"  通过: {passed} ({passed/total*100:.1f}%)")
            print(f"  跳过: {skipped}")
            print(f"  失败: {failed}")

        # 保存详细结果
        with open('/root/.openclaw/workspace/bookmate_chapter_fix_test_results.json', 'w') as f:
            json.dump(self.results, f, indent=2, default=str)

        print("\n[TEST: 详细结果已保存到 bookmate_chapter_fix_test_results.json]")


async def main():
    tester = ChapterFixTester()
    await tester.run_all_tests()


if __name__ == "__main__":
    asyncio.run(main())
