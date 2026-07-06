import re

with open('main.py', 'r') as f:
    content = f.read()

# 替换 list_books 函数，使其基于文件系统
old_pattern = r'@app\.get\("/books"\).*?return \{.*?"total_books": len\(books_storage\),.*?"books": \[.*?for book in books_storage\.values\(\).*?\].*?\}'

new_code = '''@app.get("/books")
async def list_books():
    """
    List all uploaded books from filesystem
    """
    import os
    from glob import glob
    
    settings = get_settings()
    upload_dir = settings.UPLOAD_DIR
    
    books = []
    # 扫描 uploads 目录中的所有 PDF 文件
    for pdf_path in glob(os.path.join(upload_dir, "*.pdf")):
        book_id = os.path.basename(pdf_path).replace(".pdf", "")
        try:
            # 尝试解析获取标题
            book_info = pdf_parser.parse(pdf_path, book_id)
            books.append({
                "book_id": book_id,
                "title": book_info.get("title", "untitled"),
                "total_chapters": len(book_info.get("chapters", []))
            })
        except Exception as e:
            # 如果解析失败，至少显示文件名
            books.append({
                "book_id": book_id,
                "title": f"Book {book_id[:8]}...",
                "total_chapters": 0
            })
    
    return {
        "total_books": len(books),
        "books": books
    }'''

# 使用更简单的方法：直接替换整个函数
content = re.sub(
    r'(@app\.get\("/books"\)\s*async def list_books\([^)]*\):.*?return \{[^}]+"total_books"[^}]+"books"[^}]+\})',
    new_code,
    content,
    flags=re.DOTALL
)

with open('main.py', 'w') as f:
    f.write(content)

print("✅ /books 端点已修复，现在基于文件系统扫描")
