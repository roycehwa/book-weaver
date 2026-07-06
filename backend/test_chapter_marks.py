#!/usr/bin/env python3
"""
测试章节标记 API
"""
import requests
import json
import sys

BASE_URL = "http://localhost:5000"

def test_health():
    """测试健康检查端点"""
    print("Testing health endpoint...")
    response = requests.get(f"{BASE_URL}/health")
    print(f"Status: {response.status_code}")
    data = response.json()
    print(f"Features: {data.get('features', {})}")
    assert "chapter_marks" in data.get("features", {}), "chapter_marks feature not found"
    print("✅ Health check passed\n")

def test_create_mark(book_id: str):
    """测试创建章节标记"""
    print(f"Testing create mark for book {book_id}...")

    # 首先列出书籍
    response = requests.get(f"{BASE_URL}/books")
    books = response.json()
    print(f"Available books: {books.get('total_books', 0)}")

    if books.get('total_books', 0) == 0:
        print("⚠️ No books available, skipping mark creation test")
        return None

    book_id = books['books'][0]['book_id']
    total_pages = books['books'][0]['total_pages']
    print(f"Using book: {book_id}, total pages: {total_pages}")

    # 创建标记
    mark_data = {
        "page_number": min(2, total_pages),
        "y_position": 0.3,
        "chapter_name": "Test User Chapter"
    }

    response = requests.post(
        f"{BASE_URL}/books/{book_id}/chapters/mark",
        json=mark_data
    )
    print(f"Create mark status: {response.status_code}")

    if response.status_code == 200:
        data = response.json()
        print(f"Mark created: {data.get('mark', {})}")
        print(f"Total chapters after mark: {len(data.get('chapters', []))}")
        print("✅ Create mark passed\n")
        return book_id, data['mark']['mark_id']
    else:
        print(f"❌ Create mark failed: {response.text}\n")
        return None

def test_get_marks(book_id: str):
    """测试获取标记列表"""
    print(f"Testing get marks for book {book_id}...")

    response = requests.get(f"{BASE_URL}/books/{book_id}/marks")
    print(f"Status: {response.status_code}")

    if response.status_code == 200:
        data = response.json()
        print(f"Total marks: {data.get('total_marks', 0)}")
        print(f"Marks: {data.get('marks', [])}")
        print("✅ Get marks passed\n")
        return True
    else:
        print(f"❌ Get marks failed: {response.text}\n")
        return False

def test_delete_mark(book_id: str, mark_id: str):
    """测试删除标记"""
    print(f"Testing delete mark {mark_id} for book {book_id}...")

    response = requests.delete(f"{BASE_URL}/books/{book_id}/marks/{mark_id}")
    print(f"Status: {response.status_code}")

    if response.status_code == 200:
        data = response.json()
        print(f"Deleted mark: {data.get('deleted_mark_id')}")
        print(f"Total chapters after delete: {len(data.get('chapters', []))}")
        print("✅ Delete mark passed\n")
        return True
    else:
        print(f"❌ Delete mark failed: {response.text}\n")
        return False

def main():
    print("=" * 50)
    print("BookMate Phase 2 API Test - Chapter Marks")
    print("=" * 50 + "\n")

    try:
        # 测试健康检查
        test_health()

        # 需要现有书籍进行测试
        response = requests.get(f"{BASE_URL}/books")
        books = response.json()

        if books.get('total_books', 0) == 0:
            print("⚠️ No books available. Please upload a PDF first.")
            print("Skipping mark-related tests.")
            return 0

        book_id = books['books'][0]['book_id']

        # 测试创建标记
        result = test_create_mark(book_id)
        if not result:
            return 1

        book_id, mark_id = result

        # 测试获取标记
        test_get_marks(book_id)

        # 测试删除标记
        test_delete_mark(book_id, mark_id)

        print("=" * 50)
        print("All tests completed!")
        print("=" * 50)
        return 0

    except requests.exceptions.ConnectionError:
        print(f"❌ Cannot connect to server at {BASE_URL}")
        print("Please start the server with: python main.py")
        return 1
    except Exception as e:
        print(f"❌ Test failed: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
