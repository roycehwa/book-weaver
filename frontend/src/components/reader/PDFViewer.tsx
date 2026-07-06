import { useState, useCallback, useEffect, useRef } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
import 'react-pdf/dist/esm/Page/AnnotationLayer.css'
import 'react-pdf/dist/esm/Page/TextLayer.css'

// Set worker for PDF.js
pdfjs.GlobalWorkerOptions.workerSrc = `//cdnjs.cloudflare.com/ajax/libs/pdf.js/${pdfjs.version}/pdf.worker.min.js`

interface PDFViewerProps {
  bookId: string
  pageNumber: number
  scale: number
  onDocumentLoadSuccess: (numPages: number) => void
  onPageChange: (page: number) => void
  onTextSelection?: (text: string) => void
}

const PDFViewer: React.FC<PDFViewerProps> = ({
  bookId,
  pageNumber,
  scale,
  onDocumentLoadSuccess,
  onPageChange,
  onTextSelection
}) => {
  const [loading, setLoading] = useState(true)
  const [numPages, setNumPages] = useState(0)
  const [pageWidth, setPageWidth] = useState<number>(800)
  const containerRef = useRef<HTMLDivElement>(null)

  // 响应式宽度计算
  useEffect(() => {
    const updateWidth = () => {
      if (containerRef.current) {
        const containerWidth = containerRef.current.clientWidth
        const padding = 32 // 16px * 2 (左右 padding)
        const maxPageWidth = 1200 // 最大页面宽度限制
        const calculatedWidth = Math.min(containerWidth - padding, maxPageWidth)
        // 移动端最小宽度调整为屏幕宽度减去padding
        const minWidth = window.innerWidth < 640 ? containerWidth - padding : 400
        setPageWidth(Math.max(calculatedWidth, minWidth))
      }
    }

    updateWidth()
    window.addEventListener('resize', updateWidth)
    
    // 使用 ResizeObserver 更精确地监听容器变化
    const resizeObserver = new ResizeObserver(() => {
      updateWidth()
    })
    
    if (containerRef.current) {
      resizeObserver.observe(containerRef.current)
    }

    return () => {
      window.removeEventListener('resize', updateWidth)
      resizeObserver.disconnect()
    }
  }, [])

  const handleDocumentLoadSuccess = useCallback(({ numPages }: { numPages: number }) => {
    setNumPages(numPages)
    setLoading(false)
    onDocumentLoadSuccess(numPages)
  }, [onDocumentLoadSuccess])

  const pdfUrl = `/api/books/${bookId}/pdf`

  // Handle internal PDF links (TOC, footnotes, etc.)
  const handleItemClick = useCallback(({ pageNumber: targetPage }: { pageNumber: number }) => {
    if (targetPage >= 1 && targetPage <= numPages) {
      onPageChange(targetPage)
    }
  }, [numPages, onPageChange])

  // 根据页面宽度计算最佳缩放比例
  const getOptimalScale = () => {
    // 如果用户手动设置了缩放，使用用户设置
    if (scale !== 1.2) return scale
    
    // 默认根据页面宽度自动调整
    const baseWidth = 612 // 标准 Letter 页面宽度 (72dpi * 8.5inch)
    return Math.min(pageWidth / baseWidth, 1.5) // 最大放大到 1.5 倍
  }

  // Handle text selection - 使用 mouseup 事件捕获 PDF 文字选择
  useEffect(() => {
    if (!onTextSelection) return

    const handleMouseUp = () => {
      // 延迟一点让 selection 完成后处理
      setTimeout(() => {
        const selection = window.getSelection()
        if (!selection) return
        const selectedText = selection.toString().trim()
        if (selectedText.length > 0) {
          onTextSelection(selectedText)
        }
      }, 10)
    }

    const container = containerRef.current
    if (container) {
      container.addEventListener('mouseup', handleMouseUp)
    }

    return () => {
      if (container) {
        container.removeEventListener('mouseup', handleMouseUp)
      }
    }
  }, [onTextSelection])

  return (
    <div 
      ref={containerRef} 
      className="flex-1 overflow-auto bg-slate-100 p-4 md:p-6 lg:p-8 select-text"
      style={{ userSelect: 'text', WebkitUserSelect: 'text' }}
    >
      {loading && (
        <div className="flex justify-center items-center h-full">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
        </div>
      )}
      
      <Document
        file={pdfUrl}
        onLoadSuccess={handleDocumentLoadSuccess}
        onItemClick={handleItemClick}
        loading={null}
        error={
          <div className="text-center py-16">
            <svg className="w-16 h-16 mx-auto mb-4 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
            </svg>
            <p className="text-slate-500 mb-2">无法加载 PDF 文件</p>
            <p className="text-sm text-slate-400">请检查网络连接或刷新页面重试</p>
          </div>
        }
      >
        <div className="flex justify-center">
          <Page
            pageNumber={pageNumber}
            width={pageWidth}
            scale={getOptimalScale()}
            renderTextLayer={true}
            renderAnnotationLayer={false}
            className="shadow-lg select-text"
          />
        </div>
      </Document>
      
      {/* Bottom Page Navigation */}
      {!loading && numPages > 0 && (
        <div className="flex items-center justify-center space-x-4 mt-8 pb-8">
          <button
            onClick={() => numPages > 0 && pageNumber > 1 && onPageChange(pageNumber - 1)}
            disabled={numPages > 0 && pageNumber <= 1}
            className="flex items-center space-x-2 px-6 py-3 bg-white hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg shadow-md border border-slate-200 transition-all"
          >
            <svg className="w-5 h-5 text-slate-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
            <span className="text-sm font-medium text-slate-700">上一页</span>
          </button>
          
          <div className="px-6 py-3 bg-white rounded-lg shadow-sm border border-slate-200 min-w-[120px] text-center">
            <span className="text-sm text-slate-600">
              <span className="font-semibold text-slate-800 text-lg">{pageNumber}</span>
              <span className="mx-2 text-slate-400">/</span>
              <span className="text-slate-500">{numPages}</span>
            </span>
          </div>
          
          <button
            onClick={() => numPages > 0 && pageNumber < numPages && onPageChange(pageNumber + 1)}
            disabled={numPages > 0 && pageNumber >= numPages}
            className="flex items-center space-x-2 px-6 py-3 bg-white hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg shadow-md border border-slate-200 transition-all"
          >
            <span className="text-sm font-medium text-slate-700">下一页</span>
            <svg className="w-5 h-5 text-slate-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
          </button>
        </div>
      )}
    </div>
  )
}

export default PDFViewer
