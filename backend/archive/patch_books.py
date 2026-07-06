import re

with open('main.py', 'r') as f:
    lines = f.readlines()

# 找到list_books函数的位置并替换
new_lines = []
i = 0
while i < len(lines):
    if '@app.get("/books")' in lines[i]:
        # 找到函数开始，跳过旧函数体
        new_lines.append(lines[i])  # @app.get("/books")
        i += 1
        # 跳过函数定义和文档字符串
        while i < len(lines) and 'async def list_books' not in lines[i]:
            new_lines.append(lines[i])
            i += 1
        if i < len(lines):
            new_lines.append(lines[i])  # async def list_books():
            i += 1
        # 跳过旧函数体直到遇到下一个装饰器或函数
        brace_count = 0
        started = False
        while i < len(lines):
            if '{' in lines[i]:
                brace_count += lines[i].count('{')
                started = True
            if '}' in lines[i]:
                brace_count -= lines[i].count('}')
            if started and brace_count == 0:
                i += 1
                break
            i += 1
        
        # 插入新函数体
        new_func = '''    """
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
    }

'''
        new_lines.append(new_func)
    else:
        new_lines.append(lines[i])
        i += 1

with open('main.py', 'w') as f:
    f.writelines(new_lines)

print("✅ /books端点已修复")
