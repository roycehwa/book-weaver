#!/usr/bin/env python3
"""
Comprehensive PDF Parser Test Suite for BookMate
Tests chapter extraction with different PDF structures
"""
import fitz  # PyMuPDF
import os
import sys
import time
import json
from typing import List, Dict, Any, Tuple
from dataclasses import dataclass, asdict

# Add backend to path
sys.path.insert(0, '/root/.openclaw/workspace/bookmate/backend')
from pdf_parser import PDFParser, get_parser


@dataclass
class TestResult:
    """Test result record"""
    test_name: str
    pdf_type: str
    success: bool
    chapter_count: int
    expected_chapters: int
    processing_time: float
    errors: List[str]
    toc_found: bool
    details: Dict[str, Any]


class PDFTestSuite:
    """Test suite for PDF parser with different structures"""
    
    def __init__(self, output_dir: str = "/root/.openclaw/workspace/bookmate/backend/test_chapters"):
        self.output_dir = output_dir
        self.parser = PDFParser()
        self.results: List[TestResult] = []
        os.makedirs(output_dir, exist_ok=True)
    
    def create_pdf_with_toc(self, path: str) -> str:
        """Create PDF with proper Table of Contents"""
        doc = fitz.open()
        toc = []
        
        # Chapter 1 - Introduction
        page1 = doc.new_page()
        page1.insert_text((72, 72), "Chapter 1: Introduction", fontsize=20)
        page1.insert_text((72, 120), "This chapter introduces the fundamental concepts.", fontsize=12)
        page1.insert_text((72, 160), "Learning objectives: Understand basics, prepare for advanced topics.", fontsize=12)
        toc.append((1, "Chapter 1: Introduction", 1))
        
        # Chapter 2 - Core Concepts
        page2 = doc.new_page()
        page2.insert_text((72, 72), "Chapter 2: Core Concepts", fontsize=20)
        page2.insert_text((72, 120), "Core concepts form the foundation of understanding.", fontsize=12)
        page2.insert_text((72, 160), "Key principles: consistency, clarity, and comprehensiveness.", fontsize=12)
        toc.append((1, "Chapter 2: Core Concepts", 2))
        
        # Chapter 3 - Implementation
        page3 = doc.new_page()
        page3.insert_text((72, 72), "Chapter 3: Implementation", fontsize=20)
        page3.insert_text((72, 120), "Practical implementation strategies discussed here.", fontsize=12)
        page3.insert_text((72, 160), "Step-by-step guide to applying the concepts.", fontsize=12)
        toc.append((1, "Chapter 3: Implementation", 3))
        
        # Chapter 4 - Conclusion
        page4 = doc.new_page()
        page4.insert_text((72, 72), "Chapter 4: Conclusion", fontsize=20)
        page4.insert_text((72, 120), "Summary and future directions.", fontsize=12)
        page4.insert_text((72, 160), "Key takeaways and recommended next steps.", fontsize=12)
        toc.append((1, "Chapter 4: Conclusion", 4))
        
        doc.set_toc(toc)
        doc.set_metadata({"title": "PDF with TOC", "author": "Test Suite"})
        doc.save(path)
        doc.close()
        return path
    
    def create_pdf_without_toc(self, path: str) -> str:
        """Create PDF without Table of Contents (fallback test)"""
        doc = fitz.open()
        
        # Chapter 1 content
        page1 = doc.new_page()
        page1.insert_text((72, 72), "Chapter 1: Getting Started", fontsize=20)
        page1.insert_text((72, 120), "Welcome to the getting started guide.", fontsize=12)
        
        # Chapter 2 content
        page2 = doc.new_page()
        page2.insert_text((72, 72), "Chapter 2: Basics", fontsize=20)
        page2.insert_text((72, 120), "Basic concepts everyone should know.", fontsize=12)
        
        # Chapter 3 content
        page3 = doc.new_page()
        page3.insert_text((72, 72), "Chapter 3: Advanced", fontsize=20)
        page3.insert_text((72, 120), "Advanced techniques for experts.", fontsize=12)
        
        # No TOC set - this tests fallback extraction
        doc.set_metadata({"title": "PDF without TOC", "author": "Test Suite"})
        doc.save(path)
        doc.close()
        return path
    
    def create_multilevel_chapter_pdf(self, path: str) -> str:
        """Create PDF with multi-level chapter hierarchy"""
        doc = fitz.open()
        toc = []
        
        # Part I
        page1 = doc.new_page()
        page1.insert_text((72, 72), "Part I: Foundations", fontsize=24)
        page1.insert_text((72, 120), "This part covers the foundational material.", fontsize=12)
        toc.append((1, "Part I: Foundations", 1))
        
        # Chapter 1 under Part I
        page2 = doc.new_page()
        page2.insert_text((72, 72), "Chapter 1: History", fontsize=20)
        page2.insert_text((72, 120), "Historical background and context.", fontsize=12)
        toc.append((2, "Chapter 1: History", 2))
        
        # Chapter 2 under Part I
        page3 = doc.new_page()
        page3.insert_text((72, 72), "Chapter 2: Theory", fontsize=20)
        page3.insert_text((72, 120), "Theoretical framework and models.", fontsize=12)
        toc.append((2, "Chapter 2: Theory", 3))
        
        # Part II
        page4 = doc.new_page()
        page4.insert_text((72, 72), "Part II: Applications", fontsize=24)
        page4.insert_text((72, 120), "This part covers practical applications.", fontsize=12)
        toc.append((1, "Part II: Applications", 4))
        
        # Chapter 3 under Part II
        page5 = doc.new_page()
        page5.insert_text((72, 72), "Chapter 3: Use Cases", fontsize=20)
        page5.insert_text((72, 120), "Real-world use cases and examples.", fontsize=12)
        toc.append((2, "Chapter 3: Use Cases", 5))
        
        # Chapter 4 under Part II
        page6 = doc.new_page()
        page6.insert_text((72, 72), "Chapter 4: Best Practices", fontsize=20)
        page6.insert_text((72, 120), "Industry best practices and guidelines.", fontsize=12)
        toc.append((2, "Chapter 4: Best Practices", 6))
        
        doc.set_toc(toc)
        doc.set_metadata({"title": "Multi-level Chapter PDF", "author": "Test Suite"})
        doc.save(path)
        doc.close()
        return path
    
    def create_chinese_pdf(self, path: str) -> str:
        """Create PDF with Chinese (CJK) text"""
        doc = fitz.open()
        toc = []
        
        # Use a font that supports CJK characters
        # Chapter 1: 简介
        page1 = doc.new_page()
        page1.insert_text((72, 72), "第一章：简介", fontsize=20)
        page1.insert_text((72, 120), "本章介绍基本概念和背景知识。", fontsize=12)
        page1.insert_text((72, 160), "学习目标：理解基础理论，为后续章节做准备。", fontsize=12)
        toc.append((1, "第一章：简介", 1))
        
        # Chapter 2: 核心概念
        page2 = doc.new_page()
        page2.insert_text((72, 72), "第二章：核心概念", fontsize=20)
        page2.insert_text((72, 120), "核心概念是理解整个体系的基础。", fontsize=12)
        page2.insert_text((72, 160), "三大支柱：一致性、清晰性和完整性。", fontsize=12)
        toc.append((1, "第二章：核心概念", 2))
        
        # Chapter 3: 实践应用
        page3 = doc.new_page()
        page3.insert_text((72, 72), "第三章：实践应用", fontsize=20)
        page3.insert_text((72, 120), "实践应用部分展示如何将理论转化为行动。", fontsize=12)
        page3.insert_text((72, 160), "通过案例分析，深入理解应用场景。", fontsize=12)
        toc.append((1, "第三章：实践应用", 3))
        
        doc.set_toc(toc)
        doc.set_metadata({"title": "中文测试文档", "author": "测试套件"})
        doc.save(path)
        doc.close()
        return path
    
    def create_english_pdf(self, path: str) -> str:
        """Create standard English PDF"""
        doc = fitz.open()
        toc = []
        
        # Chapter 1
        page1 = doc.new_page()
        page1.insert_text((72, 72), "Chapter 1: Overview", fontsize=20)
        page1.insert_text((72, 120), "This chapter provides an overview of the subject matter.", fontsize=12)
        page1.insert_text((72, 160), "The overview helps readers understand the scope and structure.", fontsize=12)
        toc.append((1, "Chapter 1: Overview", 1))
        
        # Chapter 2
        page2 = doc.new_page()
        page2.insert_text((72, 72), "Chapter 2: Methodology", fontsize=20)
        page2.insert_text((72, 120), "The methodology chapter describes the approach used.", fontsize=12)
        page2.insert_text((72, 160), "Research methods and data collection procedures are detailed here.", fontsize=12)
        toc.append((1, "Chapter 2: Methodology", 2))
        
        # Chapter 3
        page3 = doc.new_page()
        page3.insert_text((72, 72), "Chapter 3: Results", fontsize=20)
        page3.insert_text((72, 120), "Results are presented and analyzed in this chapter.", fontsize=12)
        page3.insert_text((72, 160), "Findings demonstrate significant patterns and trends.", fontsize=12)
        toc.append((1, "Chapter 3: Results", 3))
        
        # Chapter 4
        page4 = doc.new_page()
        page4.insert_text((72, 72), "Chapter 4: Discussion", fontsize=20)
        page4.insert_text((72, 120), "The discussion interprets the results in context.", fontsize=12)
        page4.insert_text((72, 160), "Implications for theory and practice are explored.", fontsize=12)
        toc.append((1, "Chapter 4: Discussion", 4))
        
        doc.set_toc(toc)
        doc.set_metadata({"title": "English Test Document", "author": "Test Suite"})
        doc.save(path)
        doc.close()
        return path
    
    def create_mixed_language_pdf(self, path: str) -> str:
        """Create PDF with mixed English and Chinese content"""
        doc = fitz.open()
        toc = []
        
        # Chapter 1: Mixed
        page1 = doc.new_page()
        page1.insert_text((72, 72), "Chapter 1: Introduction / 第一章：简介", fontsize=20)
        page1.insert_text((72, 120), "This chapter covers the basics / 本章涵盖基础知识", fontsize=12)
        page1.insert_text((72, 160), "Key concepts: 关键概念包括理论基础和实践应用", fontsize=12)
        toc.append((1, "Chapter 1: Introduction / 第一章：简介", 1))
        
        # Chapter 2: Mixed
        page2 = doc.new_page()
        page2.insert_text((72, 72), "Chapter 2: Analysis / 第二章：分析", fontsize=20)
        page2.insert_text((72, 120), "Analysis methods include qualitative and quantitative approaches.", fontsize=12)
        page2.insert_text((72, 160), "分析方法包括定性和定量两种途径 / Mixed methodology review", fontsize=12)
        toc.append((1, "Chapter 2: Analysis / 第二章：分析", 2))
        
        # Chapter 3: Mixed
        page3 = doc.new_page()
        page3.insert_text((72, 72), "Chapter 3: Conclusion / 第三章：结论", fontsize=20)
        page3.insert_text((72, 120), "In conclusion / 总之, the findings support the hypothesis.", fontsize=12)
        page3.insert_text((72, 160), "Future work: 未来研究方向包括扩展数据集和改进算法", fontsize=12)
        toc.append((1, "Chapter 3: Conclusion / 第三章：结论", 3))
        
        doc.set_toc(toc)
        doc.set_metadata({"title": "Mixed Language Document", "author": "Test Suite"})
        doc.save(path)
        doc.close()
        return path
    
    def run_test(self, test_name: str, pdf_type: str, pdf_path: str, expected_chapters: int) -> TestResult:
        """Run a single test and record results"""
        print(f"\n{'='*60}")
        print(f"TEST: {test_name}")
        print(f"{'='*60}")
        
        errors = []
        details = {
            "pdf_path": pdf_path,
            "toc_entries": [],
            "chapter_titles": [],
            "chapter_pages": [],
            "content_lengths": []
        }
        
        start_time = time.time()
        
        try:
            # Check if PDF exists
            if not os.path.exists(pdf_path):
                raise FileNotFoundError(f"PDF not found: {pdf_path}")
            
            # Get TOC preview
            print(f"\n[1] Testing get_toc_preview()...")
            try:
                toc_preview = self.parser.get_toc_preview(pdf_path)
                details["toc_entries"] = toc_preview
                toc_found = len(toc_preview) > 0
                print(f"    ✓ TOC entries found: {len(toc_preview)}")
                for entry in toc_preview:
                    print(f"      Level {entry['level']}: {entry['title']} (Page {entry['page']})")
            except Exception as e:
                toc_found = False
                errors.append(f"TOC preview error: {str(e)}")
                print(f"    ✗ TOC preview failed: {e}")
            
            # Parse PDF
            print(f"\n[2] Testing parse_pdf()...")
            try:
                book_data = self.parser.parse_pdf(pdf_path, book_id=f"test-{test_name.lower().replace(' ', '-')}")
                chapter_count = book_data.total_chapters
                
                print(f"    ✓ Book ID: {book_data.book_id}")
                print(f"    ✓ Title: {book_data.title}")
                print(f"    ✓ Total Chapters: {chapter_count}")
                
                # Verify chapters
                print(f"\n[3] Verifying chapter extraction...")
                for i, chapter in enumerate(book_data.chapters, 1):
                    details["chapter_titles"].append(chapter.title)
                    details["content_lengths"].append(len(chapter.content))
                    
                    content_preview = chapter.content[:60].replace('\n', ' ') + "..." if len(chapter.content) > 60 else chapter.content
                    print(f"    [{i}] {chapter.title}")
                    print(f"        Content length: {len(chapter.content)} chars")
                    print(f"        Preview: {content_preview}")
                    
                    # Verify content is not empty
                    if not chapter.content.strip():
                        errors.append(f"Chapter {i} has empty content")
                
                # Verify chapter count
                if chapter_count != expected_chapters:
                    errors.append(f"Chapter count mismatch: expected {expected_chapters}, got {chapter_count}")
                
                # Test serialization
                print(f"\n[4] Testing to_dict() serialization...")
                data_dict = book_data.to_dict()
                assert "book_id" in data_dict
                assert "title" in data_dict
                assert "chapters" in data_dict
                print(f"    ✓ Serialization successful")
                
                success = len(errors) == 0
                
            except Exception as e:
                chapter_count = 0
                success = False
                errors.append(f"Parse error: {str(e)}")
                print(f"    ✗ Parsing failed: {e}")
                import traceback
                traceback.print_exc()
        
        except Exception as e:
            success = False
            toc_found = False
            chapter_count = 0
            errors.append(f"Test setup error: {str(e)}")
            print(f"    ✗ Test failed: {e}")
        
        processing_time = time.time() - start_time
        
        result = TestResult(
            test_name=test_name,
            pdf_type=pdf_type,
            success=success,
            chapter_count=chapter_count,
            expected_chapters=expected_chapters,
            processing_time=processing_time,
            errors=errors,
            toc_found=toc_found,
            details=details
        )
        
        self.results.append(result)
        
        # Print summary
        print(f"\n[RESULT] {'✓ PASSED' if success else '✗ FAILED'}")
        print(f"         Processing time: {processing_time:.3f}s")
        print(f"         Chapters: {chapter_count}/{expected_chapters}")
        if errors:
            print(f"         Errors: {len(errors)}")
            for err in errors:
                print(f"           - {err}")
        
        return result
    
    def run_all_tests(self):
        """Run all test scenarios"""
        print("\n" + "="*60)
        print("BOOKMATE PDF PARSER - COMPREHENSIVE TEST SUITE")
        print("="*60)
        
        # Test 1: PDF with TOC
        pdf_path = os.path.join(self.output_dir, "test_with_toc.pdf")
        self.create_pdf_with_toc(pdf_path)
        self.run_test(
            test_name="PDF with TOC",
            pdf_type="toc",
            pdf_path=pdf_path,
            expected_chapters=4
        )
        
        # Test 2: PDF without TOC (fallback)
        pdf_path = os.path.join(self.output_dir, "test_no_toc.pdf")
        self.create_pdf_without_toc(pdf_path)
        self.run_test(
            test_name="PDF without TOC",
            pdf_type="no_toc",
            pdf_path=pdf_path,
            expected_chapters=1  # Fallback: single chapter
        )
        
        # Test 3: Multi-level chapter PDF
        pdf_path = os.path.join(self.output_dir, "test_multilevel.pdf")
        self.create_multilevel_chapter_pdf(pdf_path)
        self.run_test(
            test_name="Multi-level Chapter PDF",
            pdf_type="multilevel",
            pdf_path=pdf_path,
            expected_chapters=2  # Level 1 entries only (Part I, Part II)
        )
        
        # Test 4: Chinese PDF (CJK)
        pdf_path = os.path.join(self.output_dir, "test_chinese.pdf")
        self.create_chinese_pdf(pdf_path)
        self.run_test(
            test_name="Chinese PDF (CJK)",
            pdf_type="chinese",
            pdf_path=pdf_path,
            expected_chapters=3
        )
        
        # Test 5: English PDF
        pdf_path = os.path.join(self.output_dir, "test_english.pdf")
        self.create_english_pdf(pdf_path)
        self.run_test(
            test_name="English PDF",
            pdf_type="english",
            pdf_path=pdf_path,
            expected_chapters=4
        )
        
        # Test 6: Mixed Language PDF
        pdf_path = os.path.join(self.output_dir, "test_mixed.pdf")
        self.create_mixed_language_pdf(pdf_path)
        self.run_test(
            test_name="Mixed Language PDF",
            pdf_type="mixed",
            pdf_path=pdf_path,
            expected_chapters=3
        )
        
        # Additional: Test with real-world PDFs if available
        real_pdfs = [
            ("/root/.openclaw/workspace/A股市场分析报告_2026年3月中旬.pdf", "Real Chinese Report"),
            ("/root/.openclaw/workspace/黄金四大皆空_深度分析报告.pdf", "Real Chinese Analysis"),
        ]
        
        for pdf_path, name in real_pdfs:
            if os.path.exists(pdf_path):
                print(f"\n{'='*60}")
                print(f"BONUS TEST: {name}")
                print(f"{'='*60}")
                print(f"Testing with real-world PDF: {pdf_path}")
                
                start_time = time.time()
                try:
                    toc_preview = self.parser.get_toc_preview(pdf_path)
                    toc_found = len(toc_preview) > 0
                    print(f"  TOC found: {toc_found} ({len(toc_preview)} entries)")
                    
                    book_data = self.parser.parse_pdf(pdf_path, book_id=f"real-{name.lower().replace(' ', '-')}")
                    processing_time = time.time() - start_time
                    
                    result = TestResult(
                        test_name=name,
                        pdf_type="real_world",
                        success=True,
                        chapter_count=book_data.total_chapters,
                        expected_chapters=-1,  # Unknown for real PDFs
                        processing_time=processing_time,
                        errors=[],
                        toc_found=toc_found,
                        details={"pdf_path": pdf_path, "toc_entries": toc_preview}
                    )
                    self.results.append(result)
                    
                    print(f"  ✓ Extracted {book_data.total_chapters} chapters")
                    print(f"  ✓ Processing time: {processing_time:.3f}s")
                    if book_data.chapters:
                        print(f"  First chapter: {book_data.chapters[0].title}")
                        print(f"  Content sample: {book_data.chapters[0].content[:100]}...")
                        
                except Exception as e:
                    processing_time = time.time() - start_time
                    print(f"  ✗ Error: {e}")
                    result = TestResult(
                        test_name=name,
                        pdf_type="real_world",
                        success=False,
                        chapter_count=0,
                        expected_chapters=-1,
                        processing_time=processing_time,
                        errors=[str(e)],
                        toc_found=False,
                        details={"pdf_path": pdf_path}
                    )
                    self.results.append(result)
    
    def generate_report(self) -> str:
        """Generate comprehensive test report"""
        report_lines = []
        report_lines.append("\n" + "="*80)
        report_lines.append("BOOKMATE PDF PARSER - TEST REPORT")
        report_lines.append("="*80)
        report_lines.append(f"Total Tests: {len(self.results)}")
        report_lines.append(f"Passed: {sum(1 for r in self.results if r.success)}")
        report_lines.append(f"Failed: {sum(1 for r in self.results if not r.success)}")
        report_lines.append("")
        
        # Summary table
        report_lines.append("-"*80)
        report_lines.append(f"{'Test Name':<30} {'Type':<12} {'Status':<8} {'Chapters':<10} {'Time (s)':<10} {'TOC':<6}")
        report_lines.append("-"*80)
        
        for result in self.results:
            status = "PASS" if result.success else "FAIL"
            chapters = f"{result.chapter_count}/{result.expected_chapters}" if result.expected_chapters > 0 else str(result.chapter_count)
            toc = "Yes" if result.toc_found else "No"
            report_lines.append(f"{result.test_name:<30} {result.pdf_type:<12} {status:<8} {chapters:<10} {result.processing_time:<10.3f} {toc:<6}")
        
        report_lines.append("-"*80)
        report_lines.append("")
        
        # Detailed results
        report_lines.append("\nDETAILED RESULTS:")
        report_lines.append("-"*80)
        
        for result in self.results:
            report_lines.append(f"\n【{result.test_name}】")
            report_lines.append(f"  PDF Type: {result.pdf_type}")
            report_lines.append(f"  Status: {'✓ PASSED' if result.success else '✗ FAILED'}")
            report_lines.append(f"  Chapters: {result.chapter_count} (expected: {result.expected_chapters if result.expected_chapters > 0 else 'N/A'})")
            report_lines.append(f"  TOC Found: {'Yes' if result.toc_found else 'No'}")
            report_lines.append(f"  Processing Time: {result.processing_time:.3f}s")
            
            if result.details.get("chapter_titles"):
                report_lines.append(f"  Chapter Titles:")
                for i, title in enumerate(result.details["chapter_titles"], 1):
                    content_len = result.details["content_lengths"][i-1] if i <= len(result.details["content_lengths"]) else 0
                    report_lines.append(f"    {i}. {title} ({content_len} chars)")
            
            if result.errors:
                report_lines.append(f"  Errors ({len(result.errors)}):")
                for error in result.errors:
                    report_lines.append(f"    - {error}")
        
        report_lines.append("\n" + "="*80)
        report_lines.append("END OF REPORT")
        report_lines.append("="*80)
        
        return "\n".join(report_lines)
    
    def save_report(self, path: str = None):
        """Save report to file"""
        if path is None:
            path = os.path.join(self.output_dir, "test_report.txt")
        
        report = self.generate_report()
        with open(path, 'w', encoding='utf-8') as f:
            f.write(report)
        
        print(f"\n✓ Report saved to: {path}")
        return path


def main():
    """Main entry point"""
    suite = PDFTestSuite()
    suite.run_all_tests()
    print(suite.generate_report())
    suite.save_report()


if __name__ == "__main__":
    main()
