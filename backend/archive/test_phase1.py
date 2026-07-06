"""
BookMate Phase 1 API Test Suite
测试所有 Phase 1 新增 API 功能
"""
import pytest
import asyncio
import aiohttp
import json
import os
from pathlib import Path
from datetime import datetime

# 测试配置
BASE_URL = "http://localhost:8000"
TEST_PDF_PATH = "./test_data/sample.pdf"

# ==================== Fixtures ====================

@pytest.fixture(scope="session")
def event_loop():
    """创建事件循环"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def http_client():
    """创建 HTTP 客户端"""
    async with aiohttp.ClientSession() as session:
        yield session


@pytest.fixture(scope="module")
async def uploaded_book(http_client):
    """上传测试书籍并返回 book_id"""
    # 如果没有测试 PDF，创建一个简单的
    if not os.path.exists(TEST_PDF_PATH):
        os.makedirs("./test_data", exist_ok=True)
        # 创建一个最小化的 PDF 用于测试
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import letter
        
        c = canvas.Canvas(TEST_PDF_PATH, pagesize=letter)
        c.setTitle("Test Book")
        
        # 添加目录页
        c.drawString(100, 700, "Table of Contents")
        c.drawString(100, 650, "Chapter 1: Introduction .................... 3")
        c.drawString(100, 630, "Chapter 2: Methods ......................... 10")
        c.showPage()
        
        # 添加章节内容
        for i in range(1, 4):
            c.drawString(100, 700, f"Chapter {i}")
            c.drawString(100, 650, f"This is the content of chapter {i}.")
            c.drawString(100, 630, "It contains important information about the topic.")
            for j in range(20):
                c.drawString(100, 600 - j*20, f"Paragraph {j+1}: Lorem ipsum dolor sit amet.")
            c.showPage()
        
        c.save()
    
    # 上传 PDF
    with open(TEST_PDF_PATH, 'rb') as f:
        data = aiohttp.FormData()
        data.add_field('file', f, filename='test_book.pdf', content_type='application/pdf')
        
        async with http_client.post(f"{BASE_URL}/upload", data=data) as resp:
            assert resp.status == 201
            result = await resp.json()
            book_id = result['book_id']
            print(f"Uploaded book: {book_id}")
            yield book_id
    
    # 清理：删除测试书籍
    async with http_client.delete(f"{BASE_URL}/books/{book_id}") as resp:
        assert resp.status == 200


# ==================== Basic API Tests ====================

@pytest.mark.asyncio
async def test_health_check(http_client):
    """测试健康检查端点"""
    async with http_client.get(f"{BASE_URL}/health") as resp:
        assert resp.status == 200
        data = await resp.json()
        
        assert data['status'] == 'healthy'
        assert data['version'] == '1.1.0'
        assert data['features']['ai_overview'] == True
        assert data['features']['chapter_summary'] == True
        assert data['features']['translation'] == True
        assert data['features']['reading_progress'] == True
        print("✓ Health check passed")


@pytest.mark.asyncio
async def test_upload_pdf(http_client):
    """测试 PDF 上传"""
    # 创建测试 PDF
    test_pdf = "./test_data/temp_test.pdf"
    os.makedirs("./test_data", exist_ok=True)
    
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    
    c = canvas.Canvas(test_pdf, pagesize=letter)
    c.setTitle("Temp Test Book")
    c.drawString(100, 700, "Chapter 1: Test")
    c.drawString(100, 650, "Test content here.")
    c.showPage()
    c.save()
    
    # 上传
    with open(test_pdf, 'rb') as f:
        data = aiohttp.FormData()
        data.add_field('file', f, filename='temp_test.pdf', content_type='application/pdf')
        
        async with http_client.post(f"{BASE_URL}/upload", data=data) as resp:
            assert resp.status == 201
            result = await resp.json()
            
            assert 'book_id' in result
            assert result['filename'] == 'temp_test.pdf'
            assert 'total_chapters' in result
            assert 'total_pages' in result
            assert result['message'] == "PDF uploaded and parsed successfully"
            
            book_id = result['book_id']
    
    # 清理
    async with http_client.delete(f"{BASE_URL}/books/{book_id}") as resp:
        assert resp.status == 200
    
    os.remove(test_pdf)
    print("✓ Upload PDF test passed")


@pytest.mark.asyncio
async def test_list_books(http_client):
    """测试获取书籍列表"""
    async with http_client.get(f"{BASE_URL}/books") as resp:
        assert resp.status == 200
        data = await resp.json()
        
        assert 'total_books' in data
        assert 'books' in data
        assert isinstance(data['books'], list)
        print("✓ List books test passed")


# ==================== Chapter Navigation Tests ====================

@pytest.mark.asyncio
async def test_get_chapters_with_page_numbers(http_client, uploaded_book):
    """测试获取章节列表（包含页码）"""
    book_id = uploaded_book
    
    async with http_client.get(f"{BASE_URL}/books/{book_id}/chapters") as resp:
        assert resp.status == 200
        data = await resp.json()
        
        assert data['book_id'] == book_id
        assert 'title' in data
        assert 'total_chapters' in data
        assert 'total_pages' in data
        assert 'chapters' in data
        
        # 验证每个章节都有页码
        for chapter in data['chapters']:
            assert 'index' in chapter
            assert 'title' in chapter
            assert 'content' in chapter
            assert 'page_number' in chapter, "Chapter should have page_number"
            assert 'end_page' in chapter, "Chapter should have end_page"
            assert isinstance(chapter['page_number'], int)
            assert isinstance(chapter['end_page'], int)
            assert chapter['page_number'] >= 1
            assert chapter['end_page'] >= chapter['page_number']
        
        print(f"✓ Get chapters test passed ({len(data['chapters'])} chapters)")


@pytest.mark.asyncio
async def test_get_single_chapter(http_client, uploaded_book):
    """测试获取单个章节"""
    book_id = uploaded_book
    
    async with http_client.get(f"{BASE_URL}/books/{book_id}/chapters/1") as resp:
        assert resp.status == 200
        data = await resp.json()
        
        assert data['index'] == 1
        assert 'title' in data
        assert 'content' in data
        assert 'page_number' in data
        assert 'end_page' in data
        print("✓ Get single chapter test passed")


@pytest.mark.asyncio
async def test_get_chapter_not_found(http_client, uploaded_book):
    """测试获取不存在的章节"""
    book_id = uploaded_book
    
    async with http_client.get(f"{BASE_URL}/books/{book_id}/chapters/999") as resp:
        assert resp.status == 404
        print("✓ Chapter not found test passed")


# ==================== AI Overview Tests ====================

@pytest.mark.asyncio
async def test_generate_overview(http_client, uploaded_book):
    """测试生成书籍概览"""
    book_id = uploaded_book
    
    async with http_client.post(
        f"{BASE_URL}/books/{book_id}/overview",
        json={"force_regenerate": False}
    ) as resp:
        assert resp.status == 200
        data = await resp.json()
        
        assert data['book_id'] == book_id
        assert 'introduction' in data
        assert 'key_arguments' in data
        assert isinstance(data['key_arguments'], list)
        assert 'reading_suggestions' in data
        assert 'generated_at' in data
        assert 'model' in data
        assert 'cached' in data
        
        print(f"✓ Generate overview test passed")
        print(f"  Introduction: {data['introduction'][:50]}...")
        print(f"  Key arguments: {len(data['key_arguments'])} items")


@pytest.mark.asyncio
async def test_get_overview_cached(http_client, uploaded_book):
    """测试获取已缓存的概览"""
    book_id = uploaded_book
    
    # 先生成概览
    async with http_client.post(f"{BASE_URL}/books/{book_id}/overview") as resp:
        assert resp.status == 200
    
    # 再获取概览
    async with http_client.get(f"{BASE_URL}/books/{book_id}/overview") as resp:
        assert resp.status == 200
        data = await resp.json()
        
        assert data['cached'] == True
        print("✓ Get cached overview test passed")


@pytest.mark.asyncio
async def test_get_overview_not_found(http_client):
    """测试获取不存在的概览"""
    fake_book_id = "00000000-0000-0000-0000-000000000000"
    
    async with http_client.get(f"{BASE_URL}/books/{fake_book_id}/overview") as resp:
        assert resp.status == 404
        print("✓ Overview not found test passed")


# ==================== Chapter Summary Tests ====================

@pytest.mark.asyncio
async def test_generate_chapter_summary(http_client, uploaded_book):
    """测试生成章节摘要"""
    book_id = uploaded_book
    
    async with http_client.post(
        f"{BASE_URL}/books/{book_id}/chapters/1/summary",
        json={"force_regenerate": False}
    ) as resp:
        assert resp.status == 200
        data = await resp.json()
        
        assert data['book_id'] == book_id
        assert data['chapter_index'] == 1
        assert 'chapter_title' in data
        assert 'summary' in data
        assert 'generated_at' in data
        assert 'model' in data
        assert 'cached' in data
        
        print(f"✓ Generate chapter summary test passed")
        print(f"  Summary: {data['summary'][:50]}...")


@pytest.mark.asyncio
async def test_get_chapter_summary_cached(http_client, uploaded_book):
    """测试获取已缓存的章节摘要"""
    book_id = uploaded_book
    
    # 先生成摘要
    async with http_client.post(f"{BASE_URL}/books/{book_id}/chapters/1/summary") as resp:
        assert resp.status == 200
    
    # 再获取摘要
    async with http_client.get(f"{BASE_URL}/books/{book_id}/chapters/1/summary") as resp:
        assert resp.status == 200
        data = await resp.json()
        
        assert data['cached'] == True
        print("✓ Get cached chapter summary test passed")


# ==================== Translation Tests ====================

@pytest.mark.asyncio
async def test_translate_to_chinese(http_client):
    """测试翻译为中文"""
    async with http_client.post(
        f"{BASE_URL}/translate",
        json={
            "text": "Artificial Intelligence is transforming the way we live and work.",
            "target_lang": "zh"
        }
    ) as resp:
        assert resp.status == 200
        data = await resp.json()
        
        assert 'original_text' in data
        assert 'translated_text' in data
        assert data['source_lang'] == 'en'
        assert data['target_lang'] == 'zh'
        assert 'generated_at' in data
        assert 'cached' in data
        
        # 验证翻译结果包含中文字符
        assert any('\u4e00' <= char <= '\u9fff' for char in data['translated_text'])
        
        print(f"✓ Translate to Chinese test passed")
        print(f"  Original: {data['original_text']}")
        print(f"  Translated: {data['translated_text']}")


@pytest.mark.asyncio
async def test_translate_to_english(http_client):
    """测试翻译为英文"""
    async with http_client.post(
        f"{BASE_URL}/translate",
        json={
            "text": "人工智能正在改变我们的生活和工作方式。",
            "target_lang": "en"
        }
    ) as resp:
        assert resp.status == 200
        data = await resp.json()
        
        assert data['source_lang'] == 'zh'
        assert data['target_lang'] == 'en'
        assert 'translated_text' in data
        
        print(f"✓ Translate to English test passed")
        print(f"  Original: {data['original_text']}")
        print(f"  Translated: {data['translated_text']}")


@pytest.mark.asyncio
async def test_translate_invalid_language(http_client):
    """测试无效的翻译语言"""
    async with http_client.post(
        f"{BASE_URL}/translate",
        json={
            "text": "Hello",
            "target_lang": "fr"  # 不支持法语
        }
    ) as resp:
        assert resp.status == 400
        print("✓ Invalid language test passed")


@pytest.mark.asyncio
async def test_translate_empty_text(http_client):
    """测试空文本翻译"""
    async with http_client.post(
        f"{BASE_URL}/translate",
        json={
            "text": "",
            "target_lang": "zh"
        }
    ) as resp:
        assert resp.status == 422  # Pydantic validation error
        print("✓ Empty text translation test passed")


# ==================== Reading Progress Tests ====================

@pytest.mark.asyncio
async def test_save_and_get_progress(http_client, uploaded_book):
    """测试保存和获取阅读进度"""
    book_id = uploaded_book
    
    # 保存进度
    async with http_client.post(
        f"{BASE_URL}/books/{book_id}/progress",
        json={
            "page_number": 10,
            "chapter_index": 2
        }
    ) as resp:
        assert resp.status == 200
        data = await resp.json()
        
        assert data['book_id'] == book_id
        assert data['page_number'] == 10
        assert data['chapter_index'] == 2
        assert 'last_read' in data
        assert 'reading_percentage' in data
    
    # 获取进度
    async with http_client.get(f"{BASE_URL}/books/{book_id}/progress") as resp:
        assert resp.status == 200
        data = await resp.json()
        
        assert data['page_number'] == 10
        assert data['chapter_index'] == 2
        
        print(f"✓ Save and get progress test passed")
        print(f"  Progress: {data['reading_percentage']}%")


@pytest.mark.asyncio
async def test_get_progress_not_exists(http_client, uploaded_book):
    """测试获取不存在的进度（应返回默认值）"""
    book_id = uploaded_book
    
    async with http_client.get(f"{BASE_URL}/books/{book_id}/progress") as resp:
        assert resp.status == 200
        data = await resp.json()
        
        assert data['book_id'] == book_id
        assert data['page_number'] == 1  # 默认第一页
        assert data['reading_percentage'] == 0.0
        print("✓ Get default progress test passed")


@pytest.mark.asyncio
async def test_save_progress_invalid_page(http_client, uploaded_book):
    """测试保存无效的页码"""
    book_id = uploaded_book
    
    async with http_client.post(
        f"{BASE_URL}/books/{book_id}/progress",
        json={
            "page_number": 99999  # 超出总页数
        }
    ) as resp:
        assert resp.status == 400
        print("✓ Invalid page number test passed")


# ==================== Utility Tests ====================

@pytest.mark.asyncio
async def test_storage_consistency_check(http_client):
    """测试存储一致性检查"""
    async with http_client.get(f"{BASE_URL}/storage/consistency-check") as resp:
        assert resp.status == 200
        data = await resp.json()
        
        assert 'status' in data
        assert 'issues_count' in data
        assert 'issues' in data
        
        print(f"✓ Consistency check test passed: {data['status']}")


@pytest.mark.asyncio
async def test_clear_cache(http_client):
    """测试清理缓存"""
    async with http_client.post(f"{BASE_URL}/cache/clear") as resp:
        assert resp.status == 200
        data = await resp.json()
        
        assert data['status'] == 'success'
        assert 'cleared_count' in data
        
        print(f"✓ Clear cache test passed (cleared {data['cleared_count']} files)")


# ==================== Error Handling Tests ====================

@pytest.mark.asyncio
async def test_book_not_found(http_client):
    """测试书籍不存在的情况"""
    fake_book_id = "00000000-0000-0000-0000-000000000000"
    
    async with http_client.get(f"{BASE_URL}/books/{fake_book_id}/chapters") as resp:
        assert resp.status == 404
    
    async with http_client.post(f"{BASE_URL}/books/{fake_book_id}/overview") as resp:
        assert resp.status == 404
    
    async with http_client.post(f"{BASE_URL}/books/{fake_book_id}/progress", json={"page_number": 1}) as resp:
        assert resp.status == 404
    
    print("✓ Book not found error handling test passed")


@pytest.mark.asyncio
async def test_upload_invalid_file(http_client):
    """测试上传无效文件"""
    # 创建临时文本文件
    test_txt = "./test_data/test.txt"
    with open(test_txt, 'w') as f:
        f.write("This is not a PDF")
    
    with open(test_txt, 'rb') as f:
        data = aiohttp.FormData()
        data.add_field('file', f, filename='test.txt', content_type='text/plain')
        
        async with http_client.post(f"{BASE_URL}/upload", data=data) as resp:
            assert resp.status == 400
    
    os.remove(test_txt)
    print("✓ Invalid file upload test passed")


# ==================== Integration Test ====================

@pytest.mark.asyncio
async def test_full_workflow(http_client):
    """
    完整工作流测试：
    1. 上传 PDF
    2. 获取章节列表
    3. 生成概览
    4. 生成章节摘要
    5. 翻译内容
    6. 保存阅读进度
    7. 获取进度
    8. 删除书籍
    """
    print("\n=== Running full workflow test ===")
    
    # 1. 上传 PDF
    test_pdf = "./test_data/workflow_test.pdf"
    os.makedirs("./test_data", exist_ok=True)
    
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    
    c = canvas.Canvas(test_pdf, pagesize=letter)
    c.setTitle("Workflow Test Book")
    
    # 添加 TOC
    c.drawString(100, 700, "Table of Contents")
    c.drawString(100, 650, "Chapter 1: Introduction .................. 3")
    c.drawString(100, 630, "Chapter 2: Methods ...................... 10")
    c.showPage()
    
    # 添加内容
    for i in range(1, 3):
        c.drawString(100, 700, f"Chapter {i}")
        c.drawString(100, 650, f"This chapter discusses important concepts.")
        c.drawString(100, 630, "Machine learning and artificial intelligence are key topics.")
        for j in range(10):
            c.drawString(100, 580 - j*20, f"Section {j+1}: Detailed explanation here.")
        c.showPage()
    
    c.save()
    
    with open(test_pdf, 'rb') as f:
        data = aiohttp.FormData()
        data.add_field('file', f, filename='workflow_test.pdf', content_type='application/pdf')
        
        async with http_client.post(f"{BASE_URL}/upload", data=data) as resp:
            assert resp.status == 201
            result = await resp.json()
            book_id = result['book_id']
            print(f"1. Uploaded book: {book_id}")
    
    # 2. 获取章节列表
    async with http_client.get(f"{BASE_URL}/books/{book_id}/chapters") as resp:
        chapters_data = await resp.json()
        assert 'chapters' in chapters_data
        chapter_count = len(chapters_data['chapters'])
        print(f"2. Got {chapter_count} chapters")
    
    # 3. 生成概览
    async with http_client.post(f"{BASE_URL}/books/{book_id}/overview") as resp:
        overview = await resp.json()
        assert 'introduction' in overview
        print(f"3. Generated overview: {overview['introduction'][:30]}...")
    
    # 4. 生成章节摘要
    async with http_client.post(f"{BASE_URL}/books/{book_id}/chapters/1/summary") as resp:
        summary = await resp.json()
        assert 'summary' in summary
        print(f"4. Generated summary: {summary['summary'][:30]}...")
    
    # 5. 翻译内容
    async with http_client.post(
        f"{BASE_URL}/translate",
        json={"text": "Machine learning is amazing.", "target_lang": "zh"}
    ) as resp:
        translation = await resp.json()
        assert 'translated_text' in translation
        print(f"5. Translated: {translation['translated_text']}")
    
    # 6. 保存阅读进度
    async with http_client.post(
        f"{BASE_URL}/books/{book_id}/progress",
        json={"page_number": 5, "chapter_index": 1}
    ) as resp:
        progress = await resp.json()
        assert progress['page_number'] == 5
        print(f"6. Saved progress: page {progress['page_number']}")
    
    # 7. 获取进度
    async with http_client.get(f"{BASE_URL}/books/{book_id}/progress") as resp:
        progress = await resp.json()
        assert progress['page_number'] == 5
        print(f"7. Got progress: {progress['reading_percentage']}%")
    
    # 8. 删除书籍
    async with http_client.delete(f"{BASE_URL}/books/{book_id}") as resp:
        assert resp.status == 200
        print(f"8. Deleted book: {book_id}")
    
    # 清理
    os.remove(test_pdf)
    
    print("\n✓ Full workflow test passed!")


# ==================== Main ====================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
