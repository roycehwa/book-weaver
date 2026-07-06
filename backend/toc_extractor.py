"""
PDF Table of Contents (TOC) Extractor using PyMuPDF
使用 PyMuPDF 提取 PDF 内置大纲/目录结构
"""

import fitz  # PyMuPDF
import json
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class TOCItem:
    """目录项"""
    level: int
    title: str
    page: int
    children: List['TOCItem'] = None
    
    def __post_init__(self):
        if self.children is None:
            self.children = []


class PDFTOCFetcher:
    """
    PDF 内置大纲提取器
    
    PyMuPDF 的 get_toc() 可以提取 PDF 中嵌入的目录结构
    这是最准确的章节检测方法（如果 PDF 包含目录）
    """
    
    def __init__(self):
        self.stats = {
            "processed": 0,
            "with_toc": 0,
            "without_toc": 0,
            "errors": 0
        }
    
    def extract_toc(self, pdf_path: str) -> Optional[List[TOCItem]]:
        """
        提取 PDF 的目录结构
        
        Args:
            pdf_path: PDF 文件路径
            
        Returns:
            List[TOCItem]: 目录结构列表，如果没有目录则返回 None
        """
        try:
            doc = fitz.open(pdf_path)
            self.stats["processed"] += 1
            
            # 获取内置目录
            toc = doc.get_toc()
            
            if not toc:
                self.stats["without_toc"] += 1
                logger.info(f"PDF 没有内置目录: {pdf_path}")
                return None
            
            self.stats["with_toc"] += 1
            logger.info(f"成功提取目录，共 {len(toc)} 项: {pdf_path}")
            
            # 转换为 TOCItem 结构
            items = self._convert_to_items(toc)
            
            # 构建层级结构
            root_items = self._build_hierarchy(items)
            
            doc.close()
            return root_items
            
        except Exception as e:
            self.stats["errors"] += 1
            logger.error(f"提取目录失败 {pdf_path}: {e}")
            return None
    
    def _convert_to_items(self, toc: List[Tuple[int, str, int]]) -> List[TOCItem]:
        """将 PyMuPDF 的 toc 转换为 TOCItem"""
        items = []
        for level, title, page in toc:
            # 清理标题
            clean_title = self._clean_title(title)
            items.append(TOCItem(
                level=level,
                title=clean_title,
                page=page
            ))
        return items
    
    def _clean_title(self, title: str) -> str:
        """清理标题文本"""
        # 移除多余的空白
        title = ' '.join(title.split())
        # 移除常见的 PDF 生成伪影
        title = title.replace('\x00', '')
        return title.strip()
    
    def _build_hierarchy(self, items: List[TOCItem]) -> List[TOCItem]:
        """构建层级结构（将扁平列表转为树形）"""
        if not items:
            return []
        
        root = []
        stack = []
        
        for item in items:
            # 弹出层级更高的项
            while stack and stack[-1].level >= item.level:
                stack.pop()
            
            if stack:
                # 添加到父节点的 children
                stack[-1].children.append(item)
            else:
                # 顶层项
                root.append(item)
            
            stack.append(item)
        
        return root
    
    def toc_to_chapters(
        self, 
        toc_items: List[TOCItem], 
        total_pages: int
    ) -> List[Dict]:
        """
        将目录转换为 BookMate 的章节格式
        
        Args:
            toc_items: 目录项列表
            total_pages: 总页数
            
        Returns:
            List[Dict]: 符合 BookMate API 的章节列表
        """
        chapters = []
        flat_list = self._flatten_toc(toc_items)
        
        for i, item in enumerate(flat_list):
            # 计算章节结束页
            if i < len(flat_list) - 1:
                end_page = flat_list[i + 1].page - 1
            else:
                end_page = total_pages
            
            # 确保页码有效
            start_page = max(1, min(item.page, total_pages))
            end_page = max(start_page, min(end_page, total_pages))
            
            chapters.append({
                "index": i,
                "title": item.title,
                "start_page": start_page,
                "end_page": end_page,
                "level": item.level
            })
        
        return chapters
    
    def _flatten_toc(self, items: List[TOCItem]) -> List[TOCItem]:
        """将树形结构扁平化"""
        result = []
        for item in items:
            result.append(item)
            if item.children:
                result.extend(self._flatten_toc(item.children))
        return result
    
    def get_stats(self) -> Dict:
        """获取处理统计"""
        return self.stats.copy()


class HybridChapterDetector:
    """
    混合章节检测器
    
    策略：
    1. 先尝试 PyMuPDF 提取内置目录（最准确）
    2. 如果没有内置目录，回退到智能分章算法
    3. 可选：用户提供目录页范围，从 PDF 文本层解析目录
    """
    
    def __init__(self):
        self.toc_fetcher = PDFTOCFetcher()
        self.fallback_detector = None  # 延迟导入避免循环依赖
    
    def detect_chapters(
        self, 
        pdf_path: str, 
        book_id: str,
        total_pages: int
    ) -> Dict:
        """
        智能检测章节
        
        Returns:
            {
                "method": "toc" | "auto_detected" | "size_chunked",
                "chapters": [...],
                "confidence": float,
                "source": str  # 检测方法说明
            }
        """
        # Layer 1: 尝试 PyMuPDF 提取内置目录
        toc_items = self.toc_fetcher.extract_toc(pdf_path)
        
        if toc_items:
            chapters = self.toc_fetcher.toc_to_chapters(toc_items, total_pages)
            return {
                "method": "toc",
                "chapters": chapters,
                "confidence": 0.95,  # 内置目录置信度最高
                "source": "PDF embedded TOC via PyMuPDF",
                "toc_count": len(chapters)
            }
        
        # Layer 2: 回退到智能分章算法
        logger.info(f"无内置目录，使用智能分章算法: {book_id}")
        
        # 延迟导入避免循环依赖
        from chapter_detector import process_book
        
        # 提取文本进行智能分章
        try:
            doc = fitz.open(pdf_path)
            text = ""
            for page in doc:
                text += page.get_text()
            doc.close()
            
            result = process_book(book_id, text, total_pages)
            return {
                "method": result.get("processing_method", "auto_detected"),
                "chapters": result.get("chapters", []),
                "confidence": result.get("avg_confidence", 0.5),
                "source": "AI chapter detection"
            }
            
        except Exception as e:
            logger.error(f"智能分章失败: {e}")
            return {
                "method": "error",
                "chapters": [],
                "confidence": 0,
                "source": f"Error: {str(e)}"
            }


def test_toc_extraction():
    """测试目录提取"""
    import sys
    
    if len(sys.argv) < 2:
        print("用法: python toc_extractor.py <pdf_path>")
        print("\n示例:")
        print("  python toc_extractor.py /path/to/book.pdf")
        return
    
    pdf_path = sys.argv[1]
    fetcher = PDFTOCFetcher()
    
    print(f"🔍 正在分析: {pdf_path}\n")
    
    toc_items = fetcher.extract_toc(pdf_path)
    
    if toc_items:
        print(f"✅ 找到 {len(toc_items)} 个顶层目录项\n")
        
        def print_toc(items, indent=0):
            for item in items:
                prefix = "  " * indent
                print(f"{prefix}• {item.title} (第 {item.page} 页)")
                if item.children:
                    print_toc(item.children, indent + 1)
        
        print_toc(toc_items)
        
        # 转换为章节格式
        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        doc.close()
        
        chapters = fetcher.toc_to_chapters(toc_items, total_pages)
        print(f"\n📚 转换为 {len(chapters)} 个章节")
        
    else:
        print("❌ 未找到内置目录")
        print("\n统计:", fetcher.get_stats())


if __name__ == "__main__":
    test_toc_extraction()
