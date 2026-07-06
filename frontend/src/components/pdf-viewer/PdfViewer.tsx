import React, { useState, useCallback, useEffect } from 'react';
import { Document, Page } from 'react-pdf';
import { usePdfLoader } from './usePdfLoader';
import './PdfViewer.css';

/**
 * PdfViewer 组件 Props
 */
export interface PdfViewerProps {
  /** PDF文件的URL地址 */
  url: string;
  /** 页面变化回调函数 */
  onPageChange?: (page: number) => void;
  /** PDF 页数变化回调函数 */
  onDocumentLoad?: (numPages: number) => void;
  /** 初始页码 (默认第1页) */
  initialPage?: number;
  /** 初始缩放比例 (默认1.0) */
  initialScale?: number;
  /** 自定义CSS类名 */
  className?: string;
}

/**
 * PDF查看器组件
 * 
 * 功能特性：
 * - 从URL加载PDF文档
 * - 单页显示模式
 * - 上一页/下一页导航
 * - 缩放控制 (放大/缩小/重置)
 * - 页码跳转
 * - 加载状态显示
 * 
 * @example
 * ```tsx
 * <PdfViewer 
 *   url="https://example.com/book.pdf" 
 *   onPageChange={(page) => console.log('Current page:', page)}
 *   initialPage={1}
 *   initialScale={1.5}
 * />
 * ```
 */
export const PdfViewer: React.FC<PdfViewerProps> = ({
  url,
  onPageChange,
  onDocumentLoad,
  initialPage = 1,
  initialScale = 1.0,
  className = '',
}) => {
  // 使用自定义Hook加载PDF
  const { loading, error, numPages } = usePdfLoader(url);
  
  // 组件状态
  const [pageNumber, setPageNumber] = useState<number>(initialPage);
  const [scale, setScale] = useState<number>(initialScale);
  const [inputPage, setInputPage] = useState<string>(String(initialPage));

  // 配置常量
  const MIN_SCALE = 0.25;
  const MAX_SCALE = 3.0;
  const SCALE_STEP = 0.25;

  /**
   * 外部选择章节或页码时，同步 PDF 当前页。
   */
  useEffect(() => {
    const desiredPage = Math.max(1, initialPage);
    const boundedPage = numPages > 0 ? Math.min(desiredPage, numPages) : desiredPage;
    setPageNumber((currentPage) => {
      if (currentPage === boundedPage) return currentPage;
      setInputPage(String(boundedPage));
      return boundedPage;
    });
  }, [initialPage, numPages]);

  /**
   * 当PDF加载完成后，确保当前页码在有效范围内
   */
  useEffect(() => {
    if (numPages > 0 && pageNumber > numPages) {
      const validPage = Math.min(pageNumber, numPages);
      setPageNumber(validPage);
      setInputPage(String(validPage));
    }
  }, [numPages, pageNumber]);

  /**
   * 页码变化时触发回调
   */
  useEffect(() => {
    onPageChange?.(pageNumber);
  }, [pageNumber, onPageChange]);

  useEffect(() => {
    if (numPages > 0) onDocumentLoad?.(numPages);
  }, [numPages, onDocumentLoad]);

  /**
   * 切换到上一页
   */
  const goToPrevPage = useCallback(() => {
    setPageNumber((prev) => {
      const newPage = Math.max(1, prev - 1);
      setInputPage(String(newPage));
      return newPage;
    });
  }, []);

  /**
   * 切换到下一页
   */
  const goToNextPage = useCallback(() => {
    setPageNumber((prev) => {
      const newPage = Math.min(numPages, prev + 1);
      setInputPage(String(newPage));
      return newPage;
    });
  }, [numPages]);

  /**
   * 跳转到指定页
   */
  const goToPage = useCallback((page: number) => {
    const validPage = Math.max(1, Math.min(numPages, page));
    setPageNumber(validPage);
    setInputPage(String(validPage));
  }, [numPages]);

  /**
   * 处理页码输入
   */
  const handlePageInputChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    // 只允许输入数字
    if (/^\d*$/.test(value)) {
      setInputPage(value);
    }
  }, []);

  /**
   * 处理页码输入确认
   */
  const handlePageInputSubmit = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      const page = parseInt(inputPage, 10);
      if (!isNaN(page)) {
        goToPage(page);
      } else {
        setInputPage(String(pageNumber));
      }
    }
  }, [inputPage, pageNumber, goToPage]);

  /**
   * 处理页码输入失去焦点
   */
  const handlePageInputBlur = useCallback(() => {
    const page = parseInt(inputPage, 10);
    if (!isNaN(page)) {
      goToPage(page);
    } else {
      setInputPage(String(pageNumber));
    }
  }, [inputPage, pageNumber, goToPage]);

  /**
   * 放大
   */
  const zoomIn = useCallback(() => {
    setScale((prev) => Math.min(MAX_SCALE, prev + SCALE_STEP));
  }, []);

  /**
   * 缩小
   */
  const zoomOut = useCallback(() => {
    setScale((prev) => Math.max(MIN_SCALE, prev - SCALE_STEP));
  }, []);

  /**
   * 重置缩放
   */
  const resetZoom = useCallback(() => {
    setScale(1.0);
  }, []);

  /**
   * 页面渲染成功回调
   */
  const onPageRenderSuccess = useCallback(() => {
    // 页面渲染完成，可以在这里添加额外的处理
    // 例如：发送分析事件、更新阅读进度等
  }, []);

  // 加载状态
  if (loading) {
    return (
      <div className={`pdf-viewer pdf-viewer--loading ${className}`}>
        <div className="pdf-viewer__spinner">
          <div className="pdf-viewer__spinner-icon"></div>
          <p className="pdf-viewer__loading-text">正在加载PDF...</p>
        </div>
      </div>
    );
  }

  // 错误状态
  if (error) {
    const workerSrc =
      typeof window !== 'undefined'
        ? (window as unknown as { pdfjsWorker?: string }).pdfjsWorker
        : undefined;
    return (
      <div className={`pdf-viewer pdf-viewer--error ${className}`}>
        <div className="pdf-viewer__error">
          <svg className="pdf-viewer__error-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor">
            <circle cx="12" cy="12" r="10" strokeWidth="2"/>
            <line x1="12" y1="8" x2="12" y2="12" strokeWidth="2"/>
            <line x1="12" y1="16" x2="12.01" y2="16" strokeWidth="2"/>
          </svg>
          <p className="pdf-viewer__error-title">加载失败</p>
          <p className="pdf-viewer__error-message">{error.message}</p>
          <p className="pdf-viewer__error-message" style={{ wordBreak: 'break-all' }}>
            <span className="text-slate-500">URL：</span>{url}
          </p>
          {workerSrc && (
            <p className="pdf-viewer__error-message" style={{ wordBreak: 'break-all' }}>
              <span className="text-slate-500">Worker：</span>{workerSrc}
            </p>
          )}
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="mt-2 rounded border border-slate-300 bg-white px-2 py-1 text-xs text-slate-700"
          >
            重新加载页面
          </button>
        </div>
      </div>
    );
  }

  // 主渲染
  return (
    <div className={`pdf-viewer ${className}`}>
      {/* 工具栏 */}
      <div className="pdf-viewer__toolbar">
        {/* 页码导航 */}
        <div className="pdf-viewer__page-nav">
          <button
            className="pdf-viewer__btn pdf-viewer__btn--nav"
            onClick={goToPrevPage}
            disabled={pageNumber <= 1}
            aria-label="上一页"
            title="上一页"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polyline points="15 18 9 12 15 6"></polyline>
            </svg>
          </button>
          
          <div className="pdf-viewer__page-info">
            <input
              type="text"
              className="pdf-viewer__page-input"
              value={inputPage}
              onChange={handlePageInputChange}
              onKeyDown={handlePageInputSubmit}
              onBlur={handlePageInputBlur}
              aria-label="页码"
            />
            <span className="pdf-viewer__page-separator">/</span>
            <span className="pdf-viewer__page-total">{numPages}</span>
          </div>
          
          <button
            className="pdf-viewer__btn pdf-viewer__btn--nav"
            onClick={goToNextPage}
            disabled={pageNumber >= numPages}
            aria-label="下一页"
            title="下一页"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polyline points="9 18 15 12 9 6"></polyline>
            </svg>
          </button>
        </div>

        {/* 缩放控制 */}
        <div className="pdf-viewer__zoom-controls">
          <button
            className="pdf-viewer__btn pdf-viewer__btn--zoom"
            onClick={zoomOut}
            disabled={scale <= MIN_SCALE}
            aria-label="缩小"
            title="缩小"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="11" cy="11" r="8"></circle>
              <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
              <line x1="8" y1="11" x2="14" y2="11"></line>
            </svg>
          </button>
          
          <button
            className="pdf-viewer__btn pdf-viewer__btn--reset"
            onClick={resetZoom}
            title="重置缩放"
          >
            {Math.round(scale * 100)}%
          </button>
          
          <button
            className="pdf-viewer__btn pdf-viewer__btn--zoom"
            onClick={zoomIn}
            disabled={scale >= MAX_SCALE}
            aria-label="放大"
            title="放大"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="11" cy="11" r="8"></circle>
              <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
              <line x1="11" y1="8" x2="11" y2="14"></line>
              <line x1="8" y1="11" x2="14" y2="11"></line>
            </svg>
          </button>
        </div>
      </div>

      {/* PDF显示区域 */}
      <div className="pdf-viewer__container">
        <Document
          file={url}
          className="pdf-viewer__document"
          loading={
            <div className="pdf-viewer__page-loading">
              <div className="pdf-viewer__spinner-icon pdf-viewer__spinner-icon--small"></div>
            </div>
          }
          error={
            <div className="pdf-viewer__page-error">
              无法渲染此页面
            </div>
          }
        >
          <Page
            pageNumber={pageNumber}
            scale={scale}
            className="pdf-viewer__page"
            renderTextLayer={true}
            renderAnnotationLayer={true}
            onRenderSuccess={onPageRenderSuccess}
            loading={
              <div className="pdf-viewer__page-loading">
                <div className="pdf-viewer__spinner-icon pdf-viewer__spinner-icon--small"></div>
              </div>
            }
          />
        </Document>
      </div>

      {/* 底部页码指示器 */}
      <div className="pdf-viewer__footer">
        <span className="pdf-viewer__footer-page">
          第 {pageNumber} 页，共 {numPages} 页
        </span>
      </div>
    </div>
  );
};

export default PdfViewer;
