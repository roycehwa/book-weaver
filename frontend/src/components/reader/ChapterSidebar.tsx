import React, { useState, useCallback, useMemo } from 'react'
import type { ChapterMark } from './ChapterMarker'

interface Chapter {
  id: string
  title: string
  startPage: number
  level: number
  actualStartPage?: number
}

interface ChapterSidebarProps {
  chapters: Chapter[]
  currentPage: number
  isOpen: boolean
  onClose: () => void
  onChapterClick: (chapter: Chapter) => void
  onPageChange?: (page: number) => void
  bookTitle?: string
  totalPages?: number
  bookId?: string
  // 章节标记相关
  hasNativeChapters: boolean
  customMarks: ChapterMark[]
  isMarkingMode: boolean
  onStartMarking: () => void
  onStopMarking: () => void
  onDeleteMarkRequest: () => void
  onChaptersUpdated?: (chapters: {index: number, title: string, page_number: number, end_page: number, content: string}[]) => void
  onShowToast?: (message: string, type: 'success' | 'error') => void
}

const ChapterSidebar: React.FC<ChapterSidebarProps> = ({
  chapters,
  currentPage,
  isOpen,
  onClose,
  onChapterClick,
  onPageChange,
  bookTitle: _bookTitle,
  totalPages = 0,
  bookId,
  hasNativeChapters: _hasNativeChapters,
  customMarks,
  isMarkingMode,
  onStartMarking,
  onStopMarking,
  onDeleteMarkRequest,
  onChaptersUpdated,
  onShowToast
}) => {
  // Check if this is a single-chapter book (no real chapters or only 1 chapter)
  const isSingleChapter = chapters.length <= 1 && customMarks.length === 0
  
  // Page calibration state
  const [showCalibrateModal, setShowCalibrateModal] = useState(false)
  const [pageOffset, setPageOffset] = useState(0)
  const [calibrateLoading, setCalibrateLoading] = useState(false)
  const [currentOffset, setCurrentOffset] = useState(0)

  // Fetch current offset when modal opens
  const openCalibrateModal = useCallback(async () => {
    if (bookId) {
      try {
        const response = await fetch(`/api/books/${bookId}/info`)
        if (response.ok) {
          const data = await response.json()
          setCurrentOffset(data.page_offset || 0)
          setPageOffset(data.page_offset || 0)
        }
      } catch (err) {
        console.error('Failed to fetch book info:', err)
      }
    }
    setShowCalibrateModal(true)
  }, [bookId])

  // Save calibration
  const handleCalibrate = useCallback(async () => {
    if (!bookId) return
    
    setCalibrateLoading(true)
    try {
      const response = await fetch(`/api/books/${bookId}/calibrate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ page_offset: pageOffset })
      })

      if (response.ok) {
        const data = await response.json()
        setCurrentOffset(pageOffset)
        
        // Refresh chapters if updated
        if (data.chapters && onChaptersUpdated) {
          onChaptersUpdated(data.chapters)
        }
        
        setShowCalibrateModal(false)
        // Show success toast
        if (onShowToast) {
          onShowToast('页码校准已更新', 'success')
        }
      } else {
        const error = await response.json()
        alert('校准失败: ' + (error.detail || '未知错误'))
      }
    } catch (error) {
      alert('校准失败，请检查网络连接')
    } finally {
      setCalibrateLoading(false)
    }
  }, [bookId, pageOffset, onChaptersUpdated])
  
  // Find current chapter based on page number
  const getCurrentChapterId = (): string | null => {
    let currentChapter: Chapter | null = null
    for (const chapter of chapters) {
      if (chapter.startPage <= currentPage) {
        if (!currentChapter || chapter.startPage > currentChapter.startPage) {
          currentChapter = chapter
        }
      }
    }
    return currentChapter?.id || null
  }

  const currentChapterId = getCurrentChapterId()

  // [FIX] 使用 useMemo 确保章节列表在 chapters 或 customMarks 变化时正确重新计算
  const allChapters = useMemo(() => {
    const result: Chapter[] = [...chapters]
    
    // 检查 chapters 是否已包含用户标记（通过 ID 匹配）
    const existingIds = new Set(chapters.map(ch => ch.id))
    
    // 只添加尚未在 chapters 中的自定义标记
    if (customMarks.length > 0) {
      customMarks.forEach((mark) => {
        if (!existingIds.has(mark.id)) {
          result.push({
            id: mark.id,
            title: mark.title,
            startPage: mark.page,
            level: 1
          })
        }
      })
      // 按页码排序
      result.sort((a, b) => a.startPage - b.startPage)
    }
    
    return result
  }, [chapters, customMarks])

  return (
    <>
      {/* Overlay for mobile */}
      {isOpen && (
        <div 
          className="chapter-sidebar-overlay"
          onClick={onClose}
        />
      )}
      
      {/* Calibration Modal */}
      {showCalibrateModal && (
        <div 
          className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
          onClick={() => setShowCalibrateModal(false)}
        >
          <div 
            className="bg-white rounded-xl shadow-xl max-w-md w-full p-6"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-lg font-semibold text-slate-800 mb-2">页码校准</h3>
            <p className="text-sm text-slate-500 mb-4">
              如果PDF页码与实际内容页码不一致，可设置偏移量进行校准。
            </p>
            
            <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 mb-4">
              <p className="text-sm text-amber-700">
                <span className="font-medium">示例：</span>PDF第5页是实际第1页，偏移量为 -4
              </p>
            </div>

            {currentOffset !== 0 && (
              <p className="text-sm text-slate-600 mb-4">
                当前偏移量：<span className="font-medium">{currentOffset > 0 ? '+' : ''}{currentOffset}</span> 页
              </p>
            )}

            <div className="mb-6">
              <label className="block text-sm font-medium text-slate-700 mb-2">
                偏移量（页）
              </label>
              <input
                type="number"
                value={pageOffset}
                onChange={(e) => setPageOffset(parseInt(e.target.value) || 0)}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="输入偏移量，可为负数"
              />
            </div>

            <div className="flex space-x-3">
              <button
                onClick={() => setShowCalibrateModal(false)}
                className="flex-1 px-4 py-2 border border-slate-300 text-slate-700 rounded-lg hover:bg-slate-50 transition-colors"
              >
                取消
              </button>
              <button
                onClick={handleCalibrate}
                disabled={calibrateLoading}
                className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors flex items-center justify-center"
              >
                {calibrateLoading ? (
                  <>
                    <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white mr-2" />
                    保存中...
                  </>
                ) : (
                  '保存'
                )}
              </button>
            </div>
          </div>
        </div>
      )}
      
      {/* Sidebar */}
      <aside className={`chapter-sidebar ${isOpen ? 'open' : ''}`}>
        <div className="chapter-sidebar-header">
          <h3 className="chapter-sidebar-title">
            {isSingleChapter && !_hasNativeChapters ? (
              <>
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
                </svg>
                全文阅读
              </>
            ) : (
              <>
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 10h16M4 14h16M4 18h16" />
                </svg>
                目录
              </>
            )}
          </h3>
          <button
            onClick={onClose}
            className="chapter-sidebar-close"
            title="关闭"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="chapter-sidebar-content">
          {allChapters.length === 0 ? (
            <div className="chapter-empty">
              <svg className="w-12 h-12 mx-auto mb-2 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              <p>暂无章节信息</p>
            </div>
          ) : (
            <>
              {/* 标记章节提示 - 现在对所有书籍都显示 */}
              <div className="chapter-mark-prompt mb-3">
                <p className="text-sm text-slate-600 mb-2 text-center">
                  {_hasNativeChapters 
                    ? "书籍已有目录，您也可以添加自定义标记" 
                    : "未检测到章节结构，您可以手动标记"}
                </p>
              </div>

              {/* 章节列表 */}
              <ul className="chapter-list">
                {allChapters.map((chapter) => {
                  const isActive = chapter.id === currentChapterId
                  const isPast = chapter.startPage < currentPage
                  const isCustomMark = customMarks.some(m => m.id === chapter.id)
                  
                  return (
                    <li
                      key={chapter.id}
                      className={`
                        chapter-item
                        ${isActive ? 'active' : ''}
                        ${isPast ? 'past' : ''}
                        level-${chapter.level}
                        ${isCustomMark ? 'custom-mark' : ''}
                      `}
                    >
                      <button
                        onClick={() => onChapterClick(chapter)}
                        className="chapter-button"
                      >
                        <span className={`chapter-indicator ${isCustomMark ? 'custom' : ''}`}></span>
                        <span className="chapter-title-text">{chapter.title}</span>
                        <span className="chapter-page">{chapter.actualStartPage ?? chapter.startPage}</span>
                      </button>
                    </li>
                  )
                })}
              </ul>

              {/* 章节管理按钮区域 */}
              <div className="chapter-management">
                  {isMarkingMode ? (
                    <button
                      onClick={onStopMarking}
                      className="chapter-manage-btn chapter-manage-btn-cancel"
                    >
                      <svg className="w-4 h-4 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                      </svg>
                      退出标记模式
                    </button>
                  ) : (
                    <button
                      onClick={onStartMarking}
                      className="chapter-manage-btn chapter-manage-btn-add"
                    >
                      <svg className="w-4 h-4 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                      </svg>
                      标记章节
                    </button>
                  )}
                  
                  {customMarks.length > 0 && (
                    <button
                      onClick={onDeleteMarkRequest}
                      className="chapter-manage-btn chapter-manage-btn-delete"
                    >
                      <svg className="w-4 h-4 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                      </svg>
                      删除标记 ({customMarks.length})
                    </button>
                                    )}
                </div>
            </>
          )}
        </div>

        <div className="chapter-sidebar-footer">
          {/* Page Calibration Button */}
          <div className="px-4 py-2 border-t border-slate-200">
            <button
              onClick={openCalibrateModal}
              className="w-full flex items-center justify-center space-x-2 px-3 py-2 text-sm text-slate-600 hover:bg-slate-100 rounded-lg transition-colors"
              title="页码校准"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
              <span>页码校准</span>
              {currentOffset !== 0 && (
                <span className="text-xs text-blue-600">({currentOffset > 0 ? '+' : ''}{currentOffset})</span>
              )}
            </button>
          </div>

          {/* 进度条 - 使用 Tailwind 样式确保显示 */}
          <div className="reading-progress mt-3 pt-3 border-t border-slate-200">
            <div 
              className="h-1.5 bg-slate-200 rounded-full overflow-hidden cursor-pointer"
              onClick={(e) => {
                if (!onPageChange || totalPages <= 0) return
                const rect = e.currentTarget.getBoundingClientRect()
                const clickX = e.clientX - rect.left
                const percentage = clickX / rect.width
                const targetPage = Math.ceil(percentage * totalPages)
                if (targetPage > 0 && targetPage <= totalPages) {
                  onPageChange(targetPage)
                }
              }}
            >
              <div 
                className="h-full bg-blue-500 rounded-full transition-all duration-300"
                style={{ 
                  width: `${totalPages > 0 ? (currentPage / totalPages) * 100 : 0}%`,
                  cursor: onPageChange && totalPages > 0 ? 'pointer' : 'default'
                }}
              />
            </div>
            <div className="flex justify-between items-center mt-2 text-xs text-slate-500">
              <span>阅读进度</span>
              <span>第 {currentPage} / {totalPages > 0 ? totalPages : '-'} 页</span>
            </div>
          </div>
        </div>
      </aside>
    </>
  )
}

export default ChapterSidebar
