#!/usr/bin/env python3
"""
BookMate Phase 1 API Quick Test Script
快速测试 Phase 1 API，无需 pytest
"""
import asyncio
import aiohttp
import json
import sys
from pathlib import Path

BASE_URL = "http://localhost:8000"

# ANSI color codes
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"


def print_success(msg):
    print(f"{GREEN}✓{RESET} {msg}")


def print_error(msg):
    print(f"{RED}✗{RESET} {msg}")


def print_info(msg):
    print(f"{YELLOW}ℹ{RESET} {msg}")


async def test_health(session):
    """测试健康检查"""
    async with session.get(f"{BASE_URL}/health") as resp:
        if resp.status == 200:
            data = await resp.json()
            print_success(f"Health check: {data['status']} (v{data['version']})")
            features = data['features']
            print_info(f"  Features: AI Overview={features['ai_overview']}, "
                      f"Summary={features['chapter_summary']}, "
                      f"Translation={features['translation']}, "
                      f"Progress={features['reading_progress']}")
            return True
        else:
            print_error(f"Health check failed: {resp.status}")
            return False


async def test_list_books(session):
    """测试获取书籍列表"""
    async with session.get(f"{BASE_URL}/books") as resp:
        if resp.status == 200:
            data = await resp.json()
            print_success(f"List books: {data['total_books']} books found")
            return data.get('books', [])
        else:
            print_error(f"List books failed: {resp.status}")
            return []


async def test_chapters_with_pages(session, book_id):
    """测试章节页码功能"""
    async with session.get(f"{BASE_URL}/books/{book_id}/chapters") as resp:
        if resp.status == 200:
            data = await resp.json()
            chapters = data.get('chapters', [])
            print_success(f"Get chapters: {len(chapters)} chapters, {data.get('total_pages')} pages")
            
            # 验证页码
            for ch in chapters[:3]:  # 只显示前3个
                print_info(f"  Ch{ch['index']}: {ch['title'][:30]}... "
                          f"(pages {ch['page_number']}-{ch['end_page']})")
            return True
        else:
            print_error(f"Get chapters failed: {resp.status}")
            return False


async def test_overview(session, book_id):
    """测试 AI 概览"""
    print_info("Generating AI overview (this may take a few seconds)...")
    
    async with session.post(f"{BASE_URL}/books/{book_id}/overview") as resp:
        if resp.status == 200:
            data = await resp.json()
            print_success(f"AI Overview generated")
            print_info(f"  Model: {data['model']}")
            print_info(f"  Introduction: {data['introduction'][:60]}...")
            print_info(f"  Key arguments: {len(data['key_arguments'])} items")
            for i, arg in enumerate(data['key_arguments'][:3], 1):
                print_info(f"    {i}. {arg[:50]}...")
            return True
        else:
            text = await resp.text()
            print_error(f"AI Overview failed: {resp.status} - {text[:100]}")
            return False


async def test_summary(session, book_id):
    """测试章节摘要"""
    print_info("Generating chapter summary (this may take a few seconds)...")
    
    async with session.post(f"{BASE_URL}/books/{book_id}/chapters/1/summary") as resp:
        if resp.status == 200:
            data = await resp.json()
            print_success(f"Chapter summary generated")
            print_info(f"  Chapter: {data['chapter_title']}")
            print_info(f"  Summary: {data['summary'][:80]}...")
            return True
        else:
            text = await resp.text()
            print_error(f"Chapter summary failed: {resp.status} - {text[:100]}")
            return False


async def test_translation(session):
    """测试翻译功能"""
    test_cases = [
        ("Artificial Intelligence is transforming our world.", "zh", "EN->ZH"),
        ("人工智能正在改变我们的世界。", "en", "ZH->EN")
    ]
    
    for text, target_lang, label in test_cases:
        async with session.post(
            f"{BASE_URL}/translate",
            json={"text": text, "target_lang": target_lang}
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                print_success(f"Translation {label}")
                print_info(f"  Original: {data['original_text'][:50]}...")
                print_info(f"  Result: {data['translated_text'][:50]}...")
            else:
                text = await resp.text()
                print_error(f"Translation {label} failed: {resp.status}")


async def test_progress(session, book_id):
    """测试阅读进度"""
    # 保存进度
    async with session.post(
        f"{BASE_URL}/books/{book_id}/progress",
        json={"page_number": 10, "chapter_index": 2}
    ) as resp:
        if resp.status == 200:
            data = await resp.json()
            print_success(f"Progress saved: page {data['page_number']}, "
                         f"{data['reading_percentage']}%")
        else:
            print_error(f"Save progress failed: {resp.status}")
            return
    
    # 获取进度
    async with session.get(f"{BASE_URL}/books/{book_id}/progress") as resp:
        if resp.status == 200:
            data = await resp.json()
            print_success(f"Progress retrieved: page {data['page_number']}, "
                         f"chapter {data['chapter_index']}")
        else:
            print_error(f"Get progress failed: {resp.status}")


async def test_error_handling(session):
    """测试错误处理"""
    fake_id = "00000000-0000-0000-0000-000000000000"
    
    # 测试不存在的书籍
    async with session.get(f"{BASE_URL}/books/{fake_id}/chapters") as resp:
        if resp.status == 404:
            print_success("Error handling: 404 for non-existent book")
        else:
            print_error(f"Expected 404, got {resp.status}")
    
    # 测试无效的语言
    async with session.post(
        f"{BASE_URL}/translate",
        json={"text": "Hello", "target_lang": "fr"}
    ) as resp:
        if resp.status == 400:
            print_success("Error handling: 400 for invalid language")
        else:
            print_error(f"Expected 400, got {resp.status}")


async def find_test_book(session):
    """查找或创建测试书籍"""
    books = await test_list_books(session)
    
    if books:
        book_id = books[0]['book_id']
        print_info(f"Using existing book: {book_id}")
        return book_id
    
    print_error("No books found. Please upload a PDF first.")
    print_info("Example: curl -X POST -F 'file=@book.pdf' http://localhost:8000/upload")
    return None


async def main():
    """主测试函数"""
    print("=" * 60)
    print("BookMate Phase 1 API Test Suite")
    print("=" * 60)
    print()
    
    async with aiohttp.ClientSession() as session:
        # 1. 健康检查
        print("[1/7] Testing health check...")
        if not await test_health(session):
            print_error("Server is not healthy. Exiting.")
            return 1
        print()
        
        # 2. 查找测试书籍
        print("[2/7] Finding test book...")
        book_id = await find_test_book(session)
        if not book_id:
            return 1
        print()
        
        # 3. 测试章节导航
        print("[3/7] Testing chapter navigation with page numbers...")
        await test_chapters_with_pages(session, book_id)
        print()
        
        # 4. 测试 AI 概览
        print("[4/7] Testing AI overview generation...")
        await test_overview(session, book_id)
        print()
        
        # 5. 测试章节摘要
        print("[5/7] Testing chapter summary generation...")
        await test_summary(session, book_id)
        print()
        
        # 6. 测试翻译
        print("[6/7] Testing translation...")
        await test_translation(session)
        print()
        
        # 7. 测试阅读进度
        print("[7/7] Testing reading progress...")
        await test_progress(session, book_id)
        await test_error_handling(session)
        print()
        
        print("=" * 60)
        print_success("All tests completed!")
        print("=" * 60)
    
    return 0


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\nTest interrupted.")
        sys.exit(1)
    except Exception as e:
        print_error(f"Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
