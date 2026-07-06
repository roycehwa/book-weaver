"""
Test script for PDF parser functionality
Creates a sample PDF with TOC and tests chapter extraction
"""
import fitz  # PyMuPDF
import os
import sys
from pdf_parser import get_parser

def create_test_pdf(output_path: str):
    """Create a sample PDF with Table of Contents for testing"""
    doc = fitz.open()
    
    # Create outline/toc entries
    toc = []
    
    # Chapter 1
    page1 = doc.new_page()
    page1.insert_text((72, 72), "Chapter 1: Introduction", fontsize=20)
    page1.insert_text((72, 120), "This is the introduction chapter. It covers the basics of the subject matter and provides an overview of what will be discussed in the following chapters.", fontsize=12)
    page1.insert_text((72, 180), "The introduction sets the stage for understanding the core concepts that will be explored throughout this book.", fontsize=12)
    toc.append((1, "Chapter 1: Introduction", 1))
    
    # Chapter 2
    page2 = doc.new_page()
    page2.insert_text((72, 72), "Chapter 2: Core Concepts", fontsize=20)
    page2.insert_text((72, 120), "This chapter dives into the core concepts. Here we explore the fundamental principles that underpin the entire framework.", fontsize=12)
    page2.insert_text((72, 160), "Section 2.1: Basic Principles", fontsize=14)
    page2.insert_text((72, 200), "The basic principles include consistency, clarity, and comprehensiveness. These three pillars support all subsequent discussions.", fontsize=12)
    toc.append((1, "Chapter 2: Core Concepts", 2))
    
    # Chapter 3
    page3 = doc.new_page()
    page3.insert_text((72, 72), "Chapter 3: Advanced Topics", fontsize=20)
    page3.insert_text((72, 120), "In this chapter, we explore advanced topics that build upon the foundation established in previous chapters.", fontsize=12)
    page3.insert_text((72, 160), "Advanced techniques require a solid understanding of the basics. Make sure you have mastered Chapter 2 before proceeding.", fontsize=12)
    toc.append((1, "Chapter 3: Advanced Topics", 3))
    
    # Chapter 4
    page4 = doc.new_page()
    page4.insert_text((72, 72), "Chapter 4: Conclusion", fontsize=20)
    page4.insert_text((72, 120), "This final chapter summarizes the key points covered in this book and provides recommendations for further study.", fontsize=12)
    page4.insert_text((72, 160), "We hope this book has provided valuable insights and will serve as a useful reference in your future endeavors.", fontsize=12)
    toc.append((1, "Chapter 4: Conclusion", 4))
    
    # Set TOC
    doc.set_toc(toc)
    
    # Set metadata
    doc.set_metadata({
        "title": "Sample Test Book",
        "author": "BookMate Test Suite",
        "subject": "Testing PDF Parsing",
        "keywords": "test, pdf, parsing, bookmate"
    })
    
    doc.save(output_path)
    doc.close()
    print(f"✓ Created test PDF: {output_path}")
    return output_path


def test_pdf_parser():
    """Test the PDF parser with the sample PDF"""
    print("\n" + "="*60)
    print("BOOKMATE PDF PARSER TEST")
    print("="*60)
    
    # Setup
    test_dir = "./test_output"
    os.makedirs(test_dir, exist_ok=True)
    test_pdf_path = os.path.join(test_dir, "test_book.pdf")
    
    # Create test PDF
    print("\n[1/4] Creating test PDF with TOC...")
    create_test_pdf(test_pdf_path)
    
    # Test TOC preview
    print("\n[2/4] Testing TOC preview...")
    parser = get_parser()
    try:
        toc_preview = parser.get_toc_preview(test_pdf_path)
        print(f"  ✓ TOC entries found: {len(toc_preview)}")
        for entry in toc_preview:
            print(f"    - Level {entry['level']}: {entry['title']} (Page {entry['page']})")
    except Exception as e:
        print(f"  ✗ TOC preview failed: {e}")
        return False
    
    # Test full parsing
    print("\n[3/4] Testing full PDF parsing...")
    try:
        book_data = parser.parse_pdf(test_pdf_path, book_id="test-book-001")
        print(f"  ✓ Book ID: {book_data.book_id}")
        print(f"  ✓ Title: {book_data.title}")
        print(f"  ✓ Total Chapters: {book_data.total_chapters}")
        
        print("\n  Chapters extracted:")
        for chapter in book_data.chapters:
            content_preview = chapter.content[:80].replace('\n', ' ') + "..." if len(chapter.content) > 80 else chapter.content
            print(f"    [{chapter.index}] {chapter.title}")
            print(f"        Content preview: {content_preview}")
        
    except Exception as e:
        print(f"  ✗ PDF parsing failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test dict conversion
    print("\n[4/4] Testing data serialization...")
    try:
        data_dict = book_data.to_dict()
        assert "book_id" in data_dict
        assert "title" in data_dict
        assert "chapters" in data_dict
        assert len(data_dict["chapters"]) == book_data.total_chapters
        print(f"  ✓ Serialization successful")
        print(f"  ✓ All {len(data_dict['chapters'])} chapters serialized")
    except Exception as e:
        print(f"  ✗ Serialization failed: {e}")
        return False
    
    print("\n" + "="*60)
    print("ALL TESTS PASSED ✓")
    print("="*60)
    return True


if __name__ == "__main__":
    success = test_pdf_parser()
    sys.exit(0 if success else 1)
