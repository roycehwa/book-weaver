import { useState, useCallback, useEffect, useMemo } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import './reader/Reader.css'

// Sub-components
import PDFViewer from './reader/PDFViewer'
import AIToolbar from './reader/AIToolbar'
import ChapterSidebar from './reader/ChapterSidebar'
import ChapterMarker, { type ChapterMark } from './reader/ChapterMarker'

// Types
interface Chapter {
  index: number
  title: string
  content: string
  page_number: number
  end_page: number
  is_user_mark?: boolean
  mark_id?: string
  actual_start_page?: number
}

interface BookData {
  book_id: string
  title: string
  total_chapters: number
  total_pages: number
  chapters: Chapter[]
}

interface BookOverview {
  book_id: string
  introduction: string
  key_arguments: string[]
  reading_suggestions: string
  generated_at: string
  model: string
  cached: boolean
}

interface ChapterSummary {
  book_id: string
  chapter_index: number
  chapter_title: string
  summary: string
  generated_at: string
  model: string
  cached: boolean
}

const Reader = () => {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  
  const [numPages, setNumPages] = useState<number>(0)
  const [pageNumber, setPageNumber] = useState<number>(1)
  const [scale, setScale] = useState<number>(1.2)
  const [, setLoading] = useState<boolean>(true)
  const [bookData, setBookData] = useState<BookData | null>(null)
  
  const [showOverview, setShowOverview] = useState(false)
  const [overview, setOverview] = useState<BookOverview | null>(null)
  const [overviewLoading, setOverviewLoading] = useState(false)
  
  const [showSummary, setShowSummary] = useState(false)
  const [summary, setSummary] = useState<ChapterSummary | null>(null)
  const [summaryLoading, setSummaryLoading] = useState(false)
  
  const [showSidebar, setShowSidebar] = useState(() => {
    // 移动端默认关闭侧边栏
    if (typeof window !== 'undefined') {
      return window.innerWidth >= 1024
    }
    return true
  })
  const [error, setError] = useState<string | null>(null)
  
  const [isMarkingMode, setIsMarkingMode] = useState(false)
  const [customMarks, setCustomMarks] = useState<ChapterMark[]>([])
  const [showDeleteMenu, setShowDeleteMenu] = useState(false)
  const [isMobile, setIsMobile] = useState(() => {
    if (typeof window !== 'undefined') {
      return window.innerWidth < 1024
    }
    return false
  })
  
  // Toast notification state
  const [toast, setToast] = useState<{message: string, type: 'success' | 'error'} | null>(null)
  
  // Auto-hide toast after 3 seconds
  useEffect(() => {
    if (toast) {
      const timer = setTimeout(() => setToast(null), 3000)
      return () => clearTimeout(timer)
    }
  }, [toast])

  // 监听窗口大小变化，更新移动端状态
  useEffect(() => {
    const handleResize = () => {
      const mobile = window.innerWidth < 1024
      setIsMobile(mobile)
      // 如果切换到移动端且侧边栏打开，自动关闭
      if (mobile && showSidebar) {
        setShowSidebar(false)
      }
      // 如果切换到桌面端且侧边栏关闭，自动打开
      if (!mobile && !showSidebar) {
        setShowSidebar(true)
      }
    }

    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [showSidebar])

  // [FIXED] 统一的错误处理函数
  const handleApiError = useCallback((err: unknown, defaultMessage: string): string => {
    if (err instanceof Error) return err.message
    if (typeof err === 'string') return err
    return defaultMessage
  }, [])

  const fetchBookMarks = useCallback(async () => {
    if (!id) return
    try {
      const response = await fetch(`/api/books/${id}/marks`)
      if (response.ok) {
        const data = await response.json()
        const loadedMarks: ChapterMark[] = data.marks.map((m: {
          mark_id: string
          page_number: number
          y_position: number
          chapter_name?: string
          created_at: string
        }) => ({
          id: m.mark_id,
          page: m.page_number,
          title: m.chapter_name || `第 ${m.page_number} 页标记`,
          yPosition: m.y_position * 100,
          createdAt: m.created_at
        }))
        setCustomMarks(loadedMarks)
      }
    } catch (err) {
      console.error('Failed to fetch book marks:', err)
    }
  }, [id])

  // [ADDED] 重新获取书籍详情和标记，用于章节标记变更后刷新
  const refreshBookData = useCallback(async () => {
    if (!id) return
    try {
      setLoading(true)
      // 并行获取书籍详情和标记
      const [bookResponse, marksResponse] = await Promise.all([
        fetch(`/api/books/${id}/chapters`),
        fetch(`/api/books/${id}/marks`)
      ])

      if (bookResponse.ok) {
        const data = await bookResponse.json()
        setBookData(data)
        setNumPages(data.total_pages)
      }

      if (marksResponse.ok) {
        const data = await marksResponse.json()
        const loadedMarks: ChapterMark[] = data.marks.map((m: {
          mark_id: string
          page_number: number
          y_position: number
          chapter_name?: string
          created_at: string
        }) => ({
          id: m.mark_id,
          page: m.page_number,
          title: m.chapter_name || `第 ${m.page_number} 页标记`,
          yPosition: m.y_position * 100,
          createdAt: m.created_at
        }))
        setCustomMarks(loadedMarks)
      }
    } catch (err) {
      console.error('Failed to refresh book data:', err)
    } finally {
      setLoading(false)
    }
  }, [id])

  const handleChaptersUpdated = useCallback((chapters: Chapter[]) => {
    if (bookData) {
      setBookData({ ...bookData, chapters, total_chapters: chapters.length })
      
      // [FIX] 从 chapters 中提取用户标记，同步更新 customMarks
      const marksFromChapters: ChapterMark[] = chapters
        .filter(ch => ch.is_user_mark && ch.mark_id)
        .map(ch => ({
          id: ch.mark_id!,
          page: ch.page_number,
          title: ch.title,
          yPosition: 0, // yPosition 不在 chapter 中，保持现有值或设为0
          createdAt: new Date().toISOString()
        }))
      
      setCustomMarks(marksFromChapters)
    }
  }, [bookData])

  useEffect(() => {
    const fetchBookData = async () => {
      if (!id) {
        setError('无效的书籍ID')
        setLoading(false)
        return
      }
      try {
        setLoading(true)
        const response = await fetch(`/api/books/${id}/chapters`)
        if (response.ok) {
          const data = await response.json()
          setBookData(data)
          setNumPages(data.total_pages)
        } else if (response.status === 404) {
          // [FIXED] 书籍不存在时清理 localStorage 并跳转回书库
          try {
            const progressStr = localStorage.getItem('readingProgress')
            if (progressStr) {
              const progress = JSON.parse(progressStr)
              if (progress.bookId === id) {
                localStorage.removeItem('readingProgress')
              }
            }
          } catch (e) {
            console.error('Failed to clean up reading progress:', e)
          }
          setError('书籍不存在，3秒后自动返回书库...')
          setTimeout(() => navigate('/library'), 3000)
        } else {
          const errorData = await response.json().catch(() => ({}))
          setError(errorData.detail || '无法加载书籍数据')
        }
      } catch (err) {
        setError(handleApiError(err, '网络错误，请稍后重试'))
      } finally {
        setLoading(false)
      }
    }
    fetchBookData()
    fetchBookMarks()
  }, [id, fetchBookMarks, handleApiError, navigate])

  const currentChapter = useMemo(() => {
    if (!bookData?.chapters) return null
    return bookData.chapters.find(
      ch => pageNumber >= ch.page_number && pageNumber <= ch.end_page
    ) || bookData.chapters[bookData.chapters.length - 1]
  }, [bookData, pageNumber])

  useEffect(() => {
    if (id && bookData && pageNumber > 0) {
      const progress = {
        bookId: id,
        bookTitle: bookData.title,
        page: pageNumber,
        totalPages: numPages,
        chapter: currentChapter?.title || '',
        lastReadAt: new Date().toISOString()
      }
      localStorage.setItem('readingProgress', JSON.stringify(progress))
    }
  }, [id, pageNumber, bookData, numPages, currentChapter])

  const isSingleChapterBook = useMemo(() => {
    return !bookData || bookData.total_chapters <= 1
  }, [bookData])

  const progressPercent = useMemo(() => {
    if (!numPages || pageNumber <= 1) return 0
    return Math.round((pageNumber / numPages) * 100)
  }, [pageNumber, numPages])

  const handleDocumentLoadSuccess = useCallback((pages: number) => {
    setNumPages(pages)
    setLoading(false)
  }, [])

  const handlePageChange = useCallback((page: number) => {
    setPageNumber(page)
  }, [])

  const goToPage = (page: number) => {
    setPageNumber(Math.max(1, Math.min(page, numPages)))
  }

  const zoomIn = () => setScale((prev) => Math.min(prev + 0.2, 3))
  const zoomOut = () => setScale((prev) => Math.max(prev - 0.2, 0.5))

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (showOverview) { setShowOverview(false); return }
        if (showSummary) { setShowSummary(false); return }
      }
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
      
      switch (e.key) {
        case 'ArrowLeft':
        case 'PageUp':
          e.preventDefault()
          if (pageNumber > 1) setPageNumber(prev => prev - 1)
          break
        case 'ArrowRight':
        case 'PageDown':
        case ' ':
          e.preventDefault()
          if (pageNumber < numPages) setPageNumber(prev => prev + 1)
          break
        case 'Home':
          e.preventDefault()
          setPageNumber(1)
          break
        case 'End':
          e.preventDefault()
          setPageNumber(numPages)
          break
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [numPages, pageNumber, showOverview, showSummary])

  const fetchOverview = useCallback(async () => {
    if (!id || overviewLoading) return
    if (showOverview) { setShowOverview(false); return }
    
    setOverviewLoading(true)
    setShowOverview(true)
    try {
      const response = await fetch(`/api/books/${id}/overview`)
      if (response.ok) {
        setOverview(await response.json())
      } else if (response.status === 404) {
        setOverview(null)
        const generateResponse = await fetch(`/api/books/${id}/overview`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ force_regenerate: false })
        })
        if (generateResponse.ok) {
          setOverview(await generateResponse.json())
        } else {
          const errorData = await generateResponse.json().catch(() => ({}))
          setError(errorData.detail || '生成概览失败')
          setShowOverview(false)
        }
      } else {
        const errorData = await response.json().catch(() => ({}))
        setError(errorData.detail || '获取概览失败')
        setShowOverview(false)
      }
    } catch (err) {
      setError(handleApiError(err, '网络错误'))
      setShowOverview(false)
    } finally {
      setOverviewLoading(false)
    }
  }, [id, overviewLoading, showOverview, handleApiError])

  const fetchChapterSummary = useCallback(async () => {
    if (!id || !currentChapter || summaryLoading) return
    if (showSummary) { setShowSummary(false); return }
    
    setSummaryLoading(true)
    setShowSummary(true)
    try {
      const response = await fetch(`/api/books/${id}/chapters/${currentChapter.index}/summary`)
      if (response.ok) {
        setSummary(await response.json())
      } else if (response.status === 404) {
        const generateResponse = await fetch(`/api/books/${id}/chapters/${currentChapter.index}/summary`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ force_regenerate: false })
        })
        if (generateResponse.ok) {
          setSummary(await generateResponse.json())
        } else {
          const errorData = await generateResponse.json().catch(() => ({}))
          setError(errorData.detail || '生成摘要失败')
          setShowSummary(false)
        }
      } else {
        const errorData = await response.json().catch(() => ({}))
        setError(errorData.detail || '获取摘要失败')
        setShowSummary(false)
      }
    } catch (err) {
      setError(handleApiError(err, '网络错误'))
      setShowSummary(false)
    } finally {
      setSummaryLoading(false)
    }
  }, [id, currentChapter, summaryLoading, showSummary, handleApiError])

  const toolbarProps = useMemo(() => ({
    onOverviewClick: fetchOverview,
    onSummaryClick: fetchChapterSummary,
    showOverview,
    showSummary,
    overviewLoading,
    summaryLoading,
    hasCurrentChapter: !!currentChapter
  }), [fetchOverview, fetchChapterSummary, showOverview, showSummary, overviewLoading, summaryLoading, currentChapter])

  return (
    <div className="h-[calc(100vh-4rem)] flex bg-slate-50">
      {showSidebar && bookData && (
        <ChapterSidebar
          chapters={bookData.chapters.map(ch => ({
            // [FIX] 用户标记使用 mark_id 作为 id，确保与 customMarks 匹配
            id: ch.is_user_mark && ch.mark_id ? ch.mark_id : String(ch.index),
            title: ch.title,
            startPage: ch.page_number,
            actualStartPage: ch.actual_start_page,
            level: 1
          }))}
          currentPage={pageNumber}
          isOpen={showSidebar}
          onClose={() => setShowSidebar(false)}
          onChapterClick={(chapter) => goToPage(chapter.actualStartPage ?? chapter.startPage)}
          onPageChange={goToPage}
          bookTitle={bookData.title}
          totalPages={numPages}
          bookId={bookData.book_id}
          hasNativeChapters={!isSingleChapterBook}
          customMarks={customMarks}
          isMarkingMode={isMarkingMode}
          onStartMarking={() => setIsMarkingMode(true)}
          onStopMarking={() => setIsMarkingMode(false)}
          onDeleteMarkRequest={() => setShowDeleteMenu(true)}
          onChaptersUpdated={handleChaptersUpdated}
          onShowToast={(message, type) => setToast({message, type})}
        />
      )}

      {bookData && (
        <ChapterMarker
          bookId={bookData.book_id}
          isMarkingMode={isMarkingMode}
          onMarkingModeChange={setIsMarkingMode}
          onMarkCreated={(mark) => {
            // [FIX] 不再手动更新 customMarks，由 handleChaptersUpdated 统一同步
            // 只记录日志，实际更新由后端返回的 chapters 触发
            console.log('Mark created:', mark.id)
          }}
          onMarkDeleted={(markId) => {
            // [FIX] 不再手动更新 customMarks，由 handleChaptersUpdated 统一同步
            console.log('Mark deleted:', markId)
          }}
          onChaptersUpdated={handleChaptersUpdated}
          marks={customMarks}
          currentPage={pageNumber}
          hasNativeChapters={!isSingleChapterBook}
          onReparseRequest={() => console.log('Chapters recalculated')}
          showDeleteMenu={showDeleteMenu}
          onDeleteMenuChange={setShowDeleteMenu}
          onRefreshBookData={refreshBookData} // [ADDED] 传递刷新函数
        />
      )}

      <div className="flex-1 flex flex-col overflow-hidden min-w-0">
        <div className="h-1 bg-slate-200 w-full">
          <div className="h-full bg-blue-500 transition-all duration-300" style={{ width: `${progressPercent}%` }} />
        </div>

        <div className="flex items-center justify-between px-4 py-3 bg-white border-b border-slate-200 shadow-sm">
          <div className="flex items-center space-x-3">
            <button onClick={() => setShowSidebar(!showSidebar)} className="p-2 hover:bg-slate-100 rounded-lg transition-colors">
              <svg className="w-5 h-5 text-slate-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
              </svg>
            </button>
            
            <button onClick={() => navigate('/library')} className="p-2 hover:bg-slate-100 rounded-lg transition-colors">
              <svg className="w-5 h-5 text-slate-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
              </svg>
            </button>
            
            <div className="flex flex-col">
              <span className="text-sm font-medium text-slate-800 truncate max-w-[200px] md:max-w-md">
                {bookData?.title || '加载中...'}
              </span>
              {isSingleChapterBook ? (
                <span className="text-xs text-slate-500 truncate max-w-[200px]">全文阅读 · {numPages} 页</span>
              ) : currentChapter && (
                <span className="text-xs text-slate-500 truncate max-w-[200px]">{currentChapter.title}</span>
              )}
            </div>
          </div>

          <AIToolbar {...toolbarProps} />

          <div className="flex items-center space-x-4">
            <div className="flex items-center space-x-1">
              <button onClick={zoomOut} className="p-2 hover:bg-slate-100 rounded-lg transition-colors" title="缩小">
                <svg className="w-4 h-4 text-slate-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 12H4" />
                </svg>
              </button>
              <span className="text-xs text-slate-600 w-12 text-center">{Math.round(scale * 100)}%</span>
              <button onClick={zoomIn} className="p-2 hover:bg-slate-100 rounded-lg transition-colors" title="放大">
                <svg className="w-4 h-4 text-slate-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                </svg>
              </button>
            </div>

            <div className="flex items-center space-x-1">
              <button
                onClick={() => pageNumber > 1 && setPageNumber(pageNumber - 1)}
                disabled={pageNumber <= 1}
                className="prev-page-btn p-2 hover:bg-slate-100 rounded-lg transition-colors disabled:opacity-50"
                data-testid="prev-page-btn"
              >
                <svg className="w-4 h-4 text-slate-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
                </svg>
              </button>
              <span className="page-indicator text-sm text-slate-700 px-2" data-testid="page-indicator">
                {pageNumber} / {numPages > 0 ? numPages : '-'}
              </span>
              <button
                onClick={() => numPages > 0 && pageNumber < numPages && setPageNumber(pageNumber + 1)}
                disabled={numPages > 0 && pageNumber >= numPages}
                className="next-page-btn p-2 hover:bg-slate-100 rounded-lg transition-colors disabled:opacity-50"
                data-testid="next-page-btn"
              >
                <svg className="w-4 h-4 text-slate-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </svg>
              </button>
            </div>
          </div>
        </div>

        <div className="flex-1 flex overflow-auto">
          {id && (
            <PDFViewer
              bookId={id}
              pageNumber={pageNumber}
              scale={scale}
              onDocumentLoadSuccess={handleDocumentLoadSuccess}
              onPageChange={handlePageChange}
            />
          )}

          <div className="flex flex-row-reverse">
            {showSummary && (
              <div className={`bg-white border-l border-slate-200 flex flex-col flex-shrink-0 ${isMobile ? 'fixed inset-y-0 right-0 z-50 w-full sm:w-96 shadow-2xl' : 'w-80'}`}>
                <div className="p-4 border-b border-slate-200 flex items-center justify-between">
                  <div>
                    <h3 className="font-semibold text-slate-800">章节摘要</h3>
                    <p className="text-xs text-slate-500 mt-1">{summary?.chapter_title || currentChapter?.title || '加载中...'}</p>
                  </div>
                  <button onClick={() => setShowSummary(false)} className="p-1 hover:bg-slate-100 rounded">
                    <svg className="w-5 h-5 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                    </svg>
                  </button>
                </div>
                
                <div className="p-4 flex-1 overflow-y-auto">
                  {summaryLoading ? (
                    <div className="flex flex-col items-center justify-center h-full">
                      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600 mb-4"></div>
                      <p className="text-sm text-slate-500">正在生成摘要...</p>
                    </div>
                  ) : summary ? (
                    <div className="space-y-4">
                      <div className="p-4 bg-blue-50 rounded-lg"><p className="text-sm text-slate-800 leading-relaxed">{summary.summary}</p></div>
                      <div className="text-xs text-slate-400 text-right">由 {summary.model} 生成</div>
                    </div>
                  ) : (
                    <div className="flex flex-col items-center justify-center h-full text-slate-400">
                      <svg className="w-12 h-12 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                      </svg>
                      <p className="text-sm">暂无摘要</p>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {showOverview && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={() => setShowOverview(false)}>
          <div className="bg-white rounded-xl shadow-xl max-w-2xl w-full max-h-[80vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
            <div className="p-6 border-b border-slate-200 flex items-center justify-between">
              <div>
                <h2 className="text-xl font-bold text-slate-800">AI 书籍概览</h2>
                <p className="text-sm text-slate-500">{bookData?.title}</p>
              </div>
              <button onClick={() => setShowOverview(false)} className="p-2 hover:bg-slate-100 rounded-lg transition-colors">
                <svg className="w-5 h-5 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            <div className="p-6 overflow-y-auto flex-1">
              {overviewLoading ? (
                <div className="flex flex-col items-center justify-center py-12">
                  <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-purple-600 mb-4"></div>
                  <p className="text-slate-500">正在分析书籍内容...</p>
                </div>
              ) : overview ? (
                <div className="space-y-6">
                  <div>
                    <h3 className="text-sm font-semibold text-slate-700 mb-2">简介</h3>
                    <p className="text-sm text-slate-600 leading-relaxed bg-slate-50 p-4 rounded-lg">{overview.introduction}</p>
                  </div>
                  <div>
                    <h3 className="text-sm font-semibold text-slate-700 mb-2">核心论点</h3>
                    <ul className="space-y-2">
                      {overview.key_arguments.map((arg, idx) => (
                        <li key={idx} className="flex items-start">
                          <span className="flex-shrink-0 w-5 h-5 bg-blue-100 text-blue-700 rounded-full text-xs flex items-center justify-center mr-3 mt-0.5">{idx + 1}</span>
                          <span className="text-sm text-slate-600">{arg}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <h3 className="text-sm font-semibold text-slate-700 mb-2">阅读建议</h3>
                    <p className="text-sm text-slate-600 leading-relaxed bg-green-50 p-4 rounded-lg">{overview.reading_suggestions}</p>
                  </div>
                  <div className="text-xs text-slate-400 text-right pt-4 border-t border-slate-100">
                    由 {overview.model} 生成 {overview.cached && '· 来自缓存'}
                  </div>
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center h-full text-slate-400 py-12">
                  <svg className="w-16 h-16 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                  </svg>
                  <p className="text-sm">暂无概览</p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {error && (
        <div className="fixed bottom-4 right-4 bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg shadow-lg flex items-center space-x-3 z-50">
          <svg className="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <span className="text-sm">{error}</span>
          <button onClick={() => setError(null)} className="ml-2 text-red-500 hover:text-red-700">
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      )}
      
      {toast && (
        <div className={`fixed bottom-4 left-1/2 transform -translate-x-1/2 px-6 py-3 rounded-lg shadow-lg flex items-center space-x-3 z-50 transition-opacity duration-300 ${toast.type === 'success' ? 'bg-green-50 border border-green-200 text-green-700' : 'bg-red-50 border border-red-200 text-red-700'}`}>
          <svg className="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            {toast.type === 'success' ? (
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
            ) : (
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            )}
          </svg>
          <span className="text-sm">{toast.message}</span>
        </div>
      )}
    </div>
  )
}

export default Reader
