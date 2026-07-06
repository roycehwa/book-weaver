"""
PDF Parser Module using PyMuPDF (fitz)
Handles PDF parsing, TOC extraction, and chapter-based text extraction
"""
import fitz  # PyMuPDF
import uuid
import os
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, asdict, field
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class Chapter:
    """Represents a book chapter with page navigation"""
    index: int
    title: str
    content: str
    page_number: int = field(default=1)  # 章节起始页码 (1-based)
    end_page: int = field(default=1)     # 章节结束页码


@dataclass
class BookData:
    """Structured book data returned after parsing"""
    book_id: str
    title: str
    chapters: List[Chapter]
    total_chapters: int
    total_pages: int = field(default=0)  # PDF 总页数
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format"""
        return {
            "book_id": self.book_id,
            "title": self.title,
            "total_chapters": self.total_chapters,
            "total_pages": self.total_pages,
            "chapters": [
                {
                    "index": ch.index,
                    "title": ch.title,
                    "content": ch.content,
                    "page_number": ch.page_number,
                    "end_page": ch.end_page
                }
                for ch in self.chapters
            ]
        }


class PDFParser:
    """PDF parser using PyMuPDF for chapter extraction"""
    
    def __init__(self):
        self.supported_extensions = {'.pdf'}
    
    def parse_pdf(self, file_path: str, book_id: Optional[str] = None) -> BookData:
        """
        Parse a PDF file and extract chapters using TOC
        
        Args:
            file_path: Path to the PDF file
            book_id: Optional book ID (generated if not provided)
            
        Returns:
            BookData object containing structured book information
            
        Raises:
            FileNotFoundError: If PDF file doesn't exist
            ValueError: If file is not a valid PDF
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"PDF file not found: {file_path}")
        
        if not file_path.lower().endswith('.pdf'):
            raise ValueError(f"File must be a PDF: {file_path}")
        
        # Generate book ID if not provided
        if book_id is None:
            book_id = str(uuid.uuid4())
        
        logger.info(f"Parsing PDF: {file_path} (book_id: {book_id})")
        
        # Open PDF with PyMuPDF
        doc = fitz.open(file_path)
        total_pages = len(doc)
        
        try:
            # Extract book title from metadata or filename
            title = self._extract_title(doc, file_path)
            
            # Get Table of Contents
            toc = doc.get_toc()
            
            # Extract chapters
            chapters = self._extract_chapters(doc, toc)
            
            # If no TOC found, treat entire document as single chapter
            if not chapters:
                logger.warning(f"No TOC found in {file_path}, extracting as single chapter")
                chapters = self._extract_as_single_chapter(doc)
            
            logger.info(f"Successfully extracted {len(chapters)} chapters from {file_path}")
            
            return BookData(
                book_id=book_id,
                title=title,
                chapters=chapters,
                total_chapters=len(chapters),
                total_pages=total_pages
            )
            
        finally:
            doc.close()
    
    def _extract_title(self, doc: fitz.Document, file_path: str) -> str:
        """Extract book title from PDF metadata or filename"""
        # Try to get title from metadata
        metadata = doc.metadata
        if metadata and metadata.get('title'):
            return metadata['title'].strip()
        
        # Fallback to filename
        filename = os.path.basename(file_path)
        name_without_ext = os.path.splitext(filename)[0]
        return name_without_ext.replace('_', ' ').replace('-', ' ').title()
    
    def _extract_chapters(self, doc: fitz.Document, toc: List[Tuple[int, str, int]]) -> List[Chapter]:
        """
        Extract chapters based on TOC entries
        
        Args:
            doc: PyMuPDF Document object
            toc: Table of Contents from get_toc()
            
        Returns:
            List of Chapter objects with page numbers
        """
        chapters = []
        
        if not toc:
            return chapters
        
        # Filter for level 1 entries (main chapters)
        level1_entries = [(level, title, page) for level, title, page in toc if level == 1]
        
        # If no level 1 entries, use all entries
        if not level1_entries:
            level1_entries = toc
        
        total_pages = len(doc)
        
        for idx, (level, title, start_page) in enumerate(level1_entries, 1):
            # Determine end page (next chapter's start or document end)
            if idx < len(level1_entries):
                end_page = level1_entries[idx][2]
            else:
                end_page = total_pages + 1  # +1 because end_page is exclusive
            
            # Extract text from pages
            content = self._extract_text_from_pages(doc, start_page, end_page)
            
            chapter = Chapter(
                index=idx,
                title=title.strip(),
                content=content,
                page_number=start_page,  # 1-based page number from TOC
                end_page=min(end_page - 1, total_pages)  # inclusive end page
            )
            chapters.append(chapter)
        
        return chapters
    
    def _extract_text_from_pages(self, doc: fitz.Document, start_page: int, end_page: int) -> str:
        """
        Extract text from a range of pages
        
        Args:
            doc: PyMuPDF Document
            start_page: Starting page number (1-based from TOC)
            end_page: Ending page number (exclusive)
            
        Returns:
            Extracted text content
        """
        text_parts = []
        
        # Convert to 0-based indexing
        # end_page is exclusive (start of next chapter or total page count)
        start_idx = max(0, start_page - 1)
        end_idx = min(len(doc), end_page)
        
        for page_num in range(start_idx, end_idx):
            page = doc.load_page(page_num)
            text = page.get_text()
            if text.strip():
                text_parts.append(text)
        
        return '\n\n'.join(text_parts)
    
    def _extract_as_single_chapter(self, doc: fitz.Document) -> List[Chapter]:
        """Extract entire document as a single chapter when no TOC is available"""
        content_parts = []
        total_pages = len(doc)
        
        for page_num in range(total_pages):
            page = doc.load_page(page_num)
            text = page.get_text()
            if text.strip():
                content_parts.append(text)
        
        full_content = '\n\n'.join(content_parts)
        
        return [Chapter(
            index=0,
            title="Full Document",
            content=full_content,
            page_number=1,
            end_page=total_pages
        )]
    
    def get_toc_preview(self, file_path: str) -> List[Dict[str, Any]]:
        """
        Get a preview of the Table of Contents without full parsing
        
        Args:
            file_path: Path to PDF file
            
        Returns:
            List of TOC entries with level, title, and page
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"PDF file not found: {file_path}")
        
        doc = fitz.open(file_path)
        try:
            toc = doc.get_toc()
            return [
                {
                    "level": level,
                    "title": title,
                    "page": page
                }
                for level, title, page in toc
            ]
        finally:
            doc.close()


# Singleton instance
_parser_instance: Optional[PDFParser] = None


def get_parser() -> PDFParser:
    """Get or create PDF parser singleton"""
    global _parser_instance
    if _parser_instance is None:
        _parser_instance = PDFParser()
    return _parser_instance
