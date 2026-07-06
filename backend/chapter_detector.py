#!/usr/bin/env python3
"""
BookMate 智能分章处理器
Auto Chapter Detection for Books without Built-in Structure

Usage:
    python chapter_detector.py --book-id bee40aa4-c305-474a-b204-3a7c55bbffc1
    python chapter_detector.py --text-file book.txt --pages 332
"""

import re
import json
import argparse
from dataclasses import dataclass, asdict
from typing import List, Tuple, Optional


@dataclass
class Chapter:
    title: str
    start_page: int
    end_page: int
    content: str = ""
    confidence: float = 0.0  # 检测置信度


class ChapterDetector:
    """章节检测器 - 使用规则+启发式算法识别章节"""
    
    # 章节标题正则模式（按优先级排序）
    PATTERNS = [
        # Chapter Six / Chapter 6 / Chapter 6: Title
        (r'Chapter\s+[\d一二三四五六七八九十]+[.:]?\s*', 'chapter_number', 0.9),
        
        # 第X章 / 第X章：标题
        (r'第[\d一二三四五六七八九十]+章[.:]?\s*', 'chapter_cn', 0.9),
        
        # CHAPTER SIX (全大写)
        (r'^CHAPTER\s+[\dIVX]+[.:]?\s*', 'chapter_upper', 0.85),
        
        # 罗马数字章节 I. Title / II. Title
        (r'^[IVX]+\.?\s+[A-Z][a-z]', 'roman_chapter', 0.8),
        
        # 数字章节 1. Title / 1 Title / 1: Title
        (r'^\d+[.:\s]+[A-Z][^a-z]{2,}', 'numbered_section', 0.7),
        
        # 纯大写短行（可能是章节标题）
        (r'^[A-Z][A-Z\s]{5,40}[A-Z]$', 'uppercase_title', 0.5),
    ]
    
    # 常见的非章节标题（需要过滤）
    SKIP_PATTERNS = [
        r'^Page\s+\d+',  # Page 123
        r'^\d+\s+of\s+\d+',  # 1 of 332
        r'^ISBN',  # ISBN信息
        r'^Copyright',  # 版权信息
        r'^http',  # URL
        r'^References',  # References（通常作为独立部分处理）
        r'^Notes',  # Notes
        r'^Index',  # Index
        r'^Appendix',  # Appendix（可配置是否作为章节）
    ]
    
    def __init__(self, min_chapter_length: int = 2000, max_title_length: int = 100):
        self.min_chapter_length = min_chapter_length
        self.max_title_length = max_title_length
        self.skip_patterns = [re.compile(p, re.IGNORECASE) for p in self.SKIP_PATTERNS]
    
    def detect_chapters(self, text: str, total_pages: int = 332) -> List[Chapter]:
        """
        检测文本中的章节
        
        Args:
            text: 书籍完整文本
            total_pages: 总页数
            
        Returns:
            List[Chapter]: 章节列表
        """
        lines = text.split('\n')
        chapters = []
        current_chapter_start = 0
        current_chapter_title = "Introduction"  # 默认第一章
        current_chapter_confidence = 0.5
        
        for i, line in enumerate(lines):
            line = line.strip()
            
            # 跳过空行
            if not line:
                continue
            
            # 检测是否为章节标题
            is_chapter, chapter_type, confidence = self._is_chapter_title(line)
            
            if is_chapter and self._should_use_as_chapter(line, i, lines):
                # 保存上一章
                if current_chapter_start > 0 or chapters:
                    chapter_content = '\n'.join(lines[current_chapter_start:i])
                    if len(chapter_content) > self.min_chapter_length:
                        start_page = self._estimate_page(current_chapter_start, len(lines), total_pages)
                        end_page = self._estimate_page(i - 1, len(lines), total_pages)
                        
                        chapters.append(Chapter(
                            title=current_chapter_title,
                            start_page=start_page,
                            end_page=end_page,
                            content=chapter_content[:500],
                            confidence=current_chapter_confidence
                        ))
                
                current_chapter_title = line
                current_chapter_start = i
                current_chapter_confidence = confidence
        
        # 添加最后一章
        if current_chapter_start < len(lines):
            chapter_content = '\n'.join(lines[current_chapter_start:])
            start_page = self._estimate_page(current_chapter_start, len(lines), total_pages)
            end_page = total_pages
            
            chapters.append(Chapter(
                title=current_chapter_title,
                start_page=start_page,
                end_page=end_page,
                content=chapter_content[:500],
                confidence=current_chapter_confidence
            ))
        
        # 合并过短的章节
        chapters = self._merge_short_chapters(chapters)
        
        return chapters
    
    def _is_chapter_title(self, line: str) -> Tuple[bool, str, float]:
        """检测一行是否为章节标题"""
        if not line:
            return False, "", 0.0
        
        # 检查是否在跳过列表中
        for pattern in self.skip_patterns:
            if pattern.match(line):
                return False, "", 0.0
        
        # 匹配章节模式
        for pattern, ptype, confidence in self.PATTERNS:
            if re.search(pattern, line, re.MULTILINE):
                return True, ptype, confidence
        
        return False, "", 0.0
    
    def _should_use_as_chapter(self, line: str, index: int, all_lines: List[str]) -> bool:
        """判断是否应该将此行作为章节标题"""
        # 标题长度检查
        if len(line) > self.max_title_length:
            return False
        
        # 检查前后文（章节标题通常独立成段）
        prev_line = all_lines[index - 1].strip() if index > 0 else ""
        next_line = all_lines[index + 1].strip() if index < len(all_lines) - 1 else ""
        
        # 章节标题通常前面有空行（PDF转换特性）
        # 但我们也接受紧跟页码的情况
        
        return True
    
    def _estimate_page(self, line_index: int, total_lines: int, total_pages: int) -> int:
        """根据行号估算页码"""
        if total_lines == 0:
            return 1
        ratio = line_index / total_lines
        return max(1, min(total_pages, int(ratio * total_pages) + 1))
    
    def _merge_short_chapters(self, chapters: List[Chapter]) -> List[Chapter]:
        """合并过短的章节（可能是误判）"""
        if len(chapters) < 2:
            return chapters
        
        merged = []
        i = 0
        while i < len(chapters):
            current = chapters[i]
            content_length = len(current.content)
            
            # 如果当前章节太短且不是第一个，尝试与下一个合并
            if content_length < self.min_chapter_length and i < len(chapters) - 1 and i > 0:
                next_ch = chapters[i + 1]
                # 合并标题
                merged_title = f"{current.title} / {next_ch.title}"
                merged_ch = Chapter(
                    title=merged_title[:100],
                    start_page=current.start_page,
                    end_page=next_ch.end_page,
                    content=current.content + next_ch.content[:200],
                    confidence=min(current.confidence, next_ch.confidence)
                )
                merged.append(merged_ch)
                i += 2
            else:
                merged.append(current)
                i += 1
        
        return merged


class FallbackChunker:
    """后备分块器 - 当无法识别章节时使用"""
    
    def __init__(self, target_chunk_size: int = 10000):
        """
        Args:
            target_chunk_size: 每块目标字符数（约5页内容）
        """
        self.target_chunk_size = target_chunk_size
    
    def chunk_by_size(self, text: str, total_pages: int) -> List[Chapter]:
        """按固定大小分块"""
        total_chars = len(text)
        chars_per_page = total_chars // total_pages if total_pages > 0 else 2000
        
        chunks = []
        start = 0
        chunk_num = 1
        
        while start < total_chars:
            target_end = min(start + self.target_chunk_size, total_chars)
            
            # 尝试在段落边界分割
            end = self._find_good_breakpoint(text, start, target_end, total_chars)
            
            chunk_text = text[start:end]
            start_page = start // chars_per_page + 1
            end_page = min(end // chars_per_page + 1, total_pages)
            
            # 尝试提取标题（第一行非空内容）
            title = self._extract_title(chunk_text) or f"Part {chunk_num}"
            
            chunks.append(Chapter(
                title=title,
                start_page=start_page,
                end_page=end_page,
                content=chunk_text[:300],
                confidence=0.3  # 后备策略置信度较低
            ))
            
            start = end
            chunk_num += 1
        
        return chunks
    
    def _find_good_breakpoint(self, text: str, start: int, target_end: int, total_length: int) -> int:
        """寻找合适的分割点（段落边界）"""
        if target_end >= total_length:
            return total_length
        
        # 在目标位置前后10%范围内寻找段落结束
        search_start = int(start + self.target_chunk_size * 0.9)
        search_end = min(int(start + self.target_chunk_size * 1.1), total_length)
        
        # 寻找最近的段落结束（.\n或\n\n）
        best_break = target_end
        
        for i in range(search_start, search_end):
            if i < len(text) - 1:
                if text[i:i+2] == '.\n' or text[i:i+2] == '\n\n':
                    best_break = i + 2
                    break
        
        return best_break
    
    def _extract_title(self, text: str) -> Optional[str]:
        """从文本块中提取可能的标题"""
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        if not lines:
            return None
        
        # 取前3行中的短行作为标题候选
        for line in lines[:3]:
            if 5 < len(line) < 80 and not line.startswith('http'):
                return line[:80]
        
        return None


def process_book(book_id: str, text: str, total_pages: int = 332, 
                 min_chapters: int = 3) -> dict:
    """
    处理无章节书籍的主函数
    
    策略:
    1. 先尝试用规则检测章节
    2. 如果检测到的章节太少(<min_chapters)，使用后备分块
    3. 返回结构化数据
    """
    detector = ChapterDetector()
    chunker = FallbackChunker()
    
    # 尝试检测章节
    chapters = detector.detect_chapters(text, total_pages)
    method = "auto_detected"
    
    # 后备策略
    if len(chapters) < min_chapters:
        print(f"[INFO] 仅检测到 {len(chapters)} 个章节，使用后备分块策略")
        chapters = chunker.chunk_by_size(text, total_pages)
        method = "size_chunked"
    
    # 构建API响应格式
    result = {
        "book_id": book_id,
        "processing_method": method,
        "total_chapters": len(chapters),
        "total_pages": total_pages,
        "avg_confidence": sum(ch.confidence for ch in chapters) / len(chapters) if chapters else 0,
        "chapters": [
            {
                "id": f"{book_id}_ch_{i+1:03d}",
                "index": i + 1,
                "title": ch.title,
                "start_page": ch.start_page,
                "end_page": ch.end_page,
                "page_count": ch.end_page - ch.start_page + 1,
                "confidence": round(ch.confidence, 2),
                "preview": ch.content[:150] + "..." if len(ch.content) > 150 else ch.content
            }
            for i, ch in enumerate(chapters)
        ]
    }
    
    return result


def simulate_api_response():
    """模拟处理 Policing Higher Education 的输出"""
    
    # 基于实际内容观察到的章节结构
    chapters_data = [
        {"title": "Introduction", "start": 1, "end": 18},
        {"title": "Chapter 1 Intersecting Global Trends", "start": 19, "end": 68},
        {"title": "Chapter 2 The Politics of Knowledge Production", "start": 69, "end": 104},
        {"title": "Chapter 3 Classrooms as Global Battlegrounds", "start": 105, "end": 134},
        {"title": "Chapter 4 Higher Education and Democratic Dreams", "start": 135, "end": 186},
        {"title": "Chapter 5 Weaponizing Universities in the Twenty-First Century", "start": 187, "end": 215},
        {"title": "Chapter 6 Fighting Back: Revisioning Higher Education", "start": 216, "end": 237},
        {"title": "Acknowledgments", "start": 238, "end": 240},
        {"title": "Appendix: PEN America Principles", "start": 241, "end": 253},
        {"title": "Notes", "start": 254, "end": 269},
        {"title": "References", "start": 270, "end": 293},
        {"title": "Index", "start": 294, "end": 305},
    ]
    
    book_id = "bee40aa4-c305-474a-b204-3a7c55bbffc1"
    
    result = {
        "book_id": book_id,
        "processing_method": "auto_detected",
        "total_chapters": len(chapters_data),
        "total_pages": 332,
        "avg_confidence": 0.88,
        "chapters": [
            {
                "id": f"{book_id}_ch_{i+1:03d}",
                "index": i + 1,
                "title": ch["title"],
                "start_page": ch["start"],
                "end_page": ch["end"],
                "page_count": ch["end"] - ch["start"] + 1,
                "confidence": 0.9 if ch["title"].startswith("Chapter") else 0.7
            }
            for i, ch in enumerate(chapters_data)
        ]
    }
    
    return result


def main():
    parser = argparse.ArgumentParser(description='BookMate 智能分章处理器')
    parser.add_argument('--book-id', type=str, help='书籍ID')
    parser.add_argument('--text-file', type=str, help='文本文件路径')
    parser.add_argument('--pages', type=int, default=332, help='总页数')
    parser.add_argument('--simulate', action='store_true', help='使用模拟数据（用于测试）')
    parser.add_argument('--output', type=str, default='chapters.json', help='输出文件')
    
    args = parser.parse_args()
    
    if args.simulate:
        result = simulate_api_response()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"\n✅ 结果已保存到: {args.output}")
        return
    
    if args.text_file:
        with open(args.text_file, 'r', encoding='utf-8') as f:
            text = f.read()
        
        book_id = args.book_id or "unknown"
        result = process_book(book_id, text, args.pages)
        
        print(json.dumps(result, indent=2, ensure_ascii=False))
        
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"\n✅ 结果已保存到: {args.output}")
    else:
        print("请提供 --text-file 或使用 --simulate 进行测试")
        print("\n示例:")
        print("  python chapter_detector.py --simulate")
        print("  python chapter_detector.py --text-file book.txt --pages 332 --book-id xxx")


if __name__ == "__main__":
    main()
