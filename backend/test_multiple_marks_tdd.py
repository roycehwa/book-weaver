#!/usr/bin/env python3
"""
TDD Test: 多章节标记功能测试
验证一本书可以添加多个章节标记，标记之间不互相覆盖
"""
import asyncio
import sys
import os
from datetime import datetime
from typing import List

# 添加 backend 目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from storage import BookStorage, UserMarkModel, StoredBook, BookMetadata, ChapterModel


async def test_multiple_marks():
    """测试添加多个章节标记"""
    print("=" * 60)
    print("Test: 多章节标记功能")
    print("=" * 60)
    
    # 创建临时存储
    storage = BookStorage(
        storage_path="./test_storage",
        upload_dir="./test_uploads"
    )
    await storage.initialize()
    
    # 创建测试书籍
    test_book = StoredBook(
        metadata=BookMetadata(
            book_id="test-book-001",
            title="Test Book",
            filename="test.pdf",
            total_chapters=3,
            total_pages=100
        ),
        chapters=[
            ChapterModel(index=0, title="Chapter 1", content="Content 1", page_number=1, end_page=10),
            ChapterModel(index=1, title="Chapter 2", content="Content 2", page_number=11, end_page=20),
            ChapterModel(index=2, title="Chapter 3", content="Content 3", page_number=21, end_page=30),
        ],
        user_marks=[]
    )
    
    # 保存测试书籍
    async with storage._lock:
        storage._cache[test_book.metadata.book_id] = test_book
        await storage._write_to_disk(test_book)
    
    print(f"\n[初始化] 创建测试书籍: {test_book.metadata.book_id}")
    print(f"[初始化] 初始标记数: {len(test_book.user_marks)}")
    
    # 测试添加 3 个不同的标记
    marks_to_add = [
        UserMarkModel(
            mark_id="mark-001",
            page_number=5,
            y_position=0.2,
            chapter_name="第一章：引言"
        ),
        UserMarkModel(
            mark_id="mark-002",
            page_number=15,
            y_position=0.5,
            chapter_name="第二章：核心概念"
        ),
        UserMarkModel(
            mark_id="mark-003",
            page_number=25,
            y_position=0.8,
            chapter_name="第三章：总结"
        ),
    ]
    
    print(f"\n[测试步骤] 准备添加 {len(marks_to_add)} 个标记")
    
    # 添加第一个标记
    result1 = await storage.add_user_mark("test-book-001", marks_to_add[0])
    print(f"\n[标记 1] 页码={marks_to_add[0].page_number}, y={marks_to_add[0].y_position}")
    print(f"[标记 1] 当前标记总数: {len(result1.user_marks) if result1 else 0}")
    
    # 添加第二个标记
    result2 = await storage.add_user_mark("test-book-001", marks_to_add[1])
    print(f"\n[标记 2] 页码={marks_to_add[1].page_number}, y={marks_to_add[1].y_position}")
    print(f"[标记 2] 当前标记总数: {len(result2.user_marks) if result2 else 0}")
    
    # 添加第三个标记
    result3 = await storage.add_user_mark("test-book-001", marks_to_add[2])
    print(f"\n[标记 3] 页码={marks_to_add[2].page_number}, y={marks_to_add[2].y_position}")
    print(f"[标记 3] 当前标记总数: {len(result3.user_marks) if result3 else 0}")
    
    # 验证结果
    final_book = await storage.get_book("test-book-001")
    print(f"\n[验证] 最终标记数量: {len(final_book.user_marks)}")
    print(f"[验证] 期望标记数量: 3")
    
    # 验证每个标记都存在
    success = True
    expected_marks = [(5, 0.2, "第一章：引言"), (15, 0.5, "第二章：核心概念"), (25, 0.8, "第三章：总结")]
    
    for i, (expected_page, expected_y, expected_name) in enumerate(expected_marks, 1):
        found = any(
            m.page_number == expected_page and 
            abs(m.y_position - expected_y) < 0.001 and
            m.chapter_name == expected_name
            for m in final_book.user_marks
        )
        status = "✅" if found else "❌"
        print(f"{status} 标记 {i}: 页码={expected_page}, y={expected_y}, 名称='{expected_name}' - {'找到' if found else '未找到'}")
        if not found:
            success = False
    
    # 验证没有重复
    mark_keys = [(m.page_number, round(m.y_position, 3)) for m in final_book.user_marks]
    has_duplicates = len(mark_keys) != len(set(mark_keys))
    if has_duplicates:
        print("\n❌ 发现重复标记!")
        success = False
    else:
        print("\n✅ 无重复标记")
    
    # 清理测试数据
    import shutil
    if os.path.exists("./test_storage"):
        shutil.rmtree("./test_storage")
    if os.path.exists("./test_uploads"):
        shutil.rmtree("./test_uploads")
    
    print("\n" + "=" * 60)
    if success and len(final_book.user_marks) == 3:
        print("✅ 测试通过: 多章节标记功能正常")
        return True
    else:
        print("❌ 测试失败: 多章节标记功能异常")
        return False


async def test_duplicate_prevention():
    """测试重复标记防护"""
    print("\n" + "=" * 60)
    print("Test: 重复标记防护")
    print("=" * 60)
    
    # 创建临时存储
    storage = BookStorage(
        storage_path="./test_storage2",
        upload_dir="./test_uploads2"
    )
    await storage.initialize()
    
    # 创建测试书籍
    test_book = StoredBook(
        metadata=BookMetadata(
            book_id="test-book-002",
            title="Test Book 2",
            filename="test2.pdf",
            total_chapters=3,
            total_pages=100
        ),
        chapters=[
            ChapterModel(index=0, title="Chapter 1", content="Content 1", page_number=1, end_page=10),
        ],
        user_marks=[]
    )
    
    async with storage._lock:
        storage._cache[test_book.metadata.book_id] = test_book
        await storage._write_to_disk(test_book)
    
    # 添加第一个标记
    mark1 = UserMarkModel(
        mark_id="mark-001",
        page_number=5,
        y_position=0.2,
        chapter_name="第一章"
    )
    await storage.add_user_mark("test-book-002", mark1)
    
    # 尝试添加重复标记（同页同位置）
    mark_duplicate = UserMarkModel(
        mark_id="mark-002",
        page_number=5,
        y_position=0.2001,  # 非常接近的位置（差值 < 0.05）
        chapter_name="重复章节"
    )
    
    # 检查是否被阻止
    result = await storage.add_user_mark("test-book-002", mark_duplicate)
    
    print(f"\n[测试] 添加第一个标记: 页码=5, y=0.2")
    print(f"[测试] 尝试添加重复标记: 页码=5, y=0.2001 (差值 < 0.05)")
    
    # 验证 - 重复标记应该被阻止
    final_book = await storage.get_book("test-book-002")
    print(f"\n[验证] 最终标记数量: {len(final_book.user_marks)}")
    print(f"[验证] 期望标记数量: 1 (重复标记应被阻止)")
    
    # 清理
    import shutil
    if os.path.exists("./test_storage2"):
        shutil.rmtree("./test_storage2")
    if os.path.exists("./test_uploads2"):
        shutil.rmtree("./test_uploads2")
    
    print("\n" + "=" * 60)
    success = len(final_book.user_marks) == 1
    print(f"{'✅ 通过' if success else '❌ 失败'}: 重复标记防护 {'已阻止' if success else '未阻止'}重复标记")
    return success


async def run_all_tests():
    """运行所有测试"""
    print("\n" + "🧪 " * 30)
    print("BookMate 多章节标记功能 TDD 测试")
    print("🧪 " * 30 + "\n")
    
    test1_passed = await test_multiple_marks()
    test2_passed = await test_duplicate_prevention()
    
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)
    print(f"测试 1 - 多章节标记: {'✅ 通过' if test1_passed else '❌ 失败'}")
    print(f"测试 2 - 重复标记防护: {'✅ 通过' if test2_passed else '❌ 失败'}")
    
    return test1_passed and test2_passed


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
