#!/usr/bin/env python3
"""
重新解析所有 PDF 文件，提取章节内容并更新存储
"""
import os
import sys
import json
import asyncio
from pathlib import Path

# 添加 backend 目录到路径
sys.path.insert(0, '/root/.openclaw/workspace/bookmate/backend')

from pdf_parser import get_parser
from storage import init_storage, get_storage, StoredBook, ChapterModel, BookMetadata
from datetime import datetime

async def reparse_all_books():
    """重新解析所有 PDF 文件并更新存储"""
    
    storage = await init_storage()
    parser = get_parser()
    
    upload_dir = Path('/root/.openclaw/workspace/bookmate/backend/uploads')
    storage_dir = Path('/root/.openclaw/workspace/bookmate/backend/storage/books')
    
    # 获取所有 PDF 文件
    pdf_files = list(upload_dir.glob('*.pdf'))
    print(f"Found {len(pdf_files)} PDF files to reparse")
    
    for pdf_file in pdf_files:
        book_id = pdf_file.stem
        json_file = storage_dir / f"{book_id}.json"
        
        print(f"\nProcessing: {book_id}")
        print(f"  PDF: {pdf_file}")
        
        try:
            # 解析 PDF
            book_data = parser.parse_pdf(str(pdf_file), book_id=book_id)
            book_data.filename = pdf_file.name
            
            print(f"  Title: {book_data.title}")
            print(f"  Chapters: {len(book_data.chapters)}")
            
            # 检查章节内容
            total_content = sum(len(ch.content) for ch in book_data.chapters)
            print(f"  Total content length: {total_content} chars")
            
            # 保存到存储
            stored_book = await storage.save_book(book_data)
            print(f"  Saved successfully!")
            
            # 验证保存的内容
            if stored_book.chapters:
                sample_ch = stored_book.chapters[0]
                print(f"  Chapter 0 content length: {len(sample_ch.content)} chars")
            
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback
            traceback.print_exc()
    
    print("\n\nReparse complete!")
    
    # 验证结果
    print("\n=== Verification ===")
    books = await storage.list_books()
    for book in books:
        full_book = await storage.get_book(book.book_id)
        if full_book and full_book.chapters:
            content_lengths = [len(ch.content) for ch in full_book.chapters]
            avg_length = sum(content_lengths) / len(content_lengths)
            print(f"{book.title}: {len(full_book.chapters)} chapters, avg content: {avg_length:.0f} chars")

if __name__ == "__main__":
    asyncio.run(reparse_all_books())
