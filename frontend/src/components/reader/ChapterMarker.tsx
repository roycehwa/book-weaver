import React, { useState, useCallback, useEffect } from 'react'

export interface ChapterMark {
  id: string
  page: number
  title: string
  yPosition: number
  createdAt: string
}

interface Chapter {
  index: number
  title: string
  content: string
  page_number: number
  end_page: number
}

interface ChapterMarkerProps {
  bookId: string
  isMarkingMode: boolean
  onMarkingModeChange: (isActive: boolean) => void
  onMarkCreated: (mark: ChapterMark) => void
  onMarkDeleted: (markId: string) => void
  onChaptersUpdated?: (chapters: Chapter[]) => void
  marks: ChapterMark[]
  currentPage: number
  hasNativeChapters: boolean
  onReparseRequest: () => void
  showDeleteMenu?: boolean
  onDeleteMenuChange?: (show: boolean) => void
  onRefreshBookData?: () => Promise<void> // [ADDED] 重新获取书籍详情的回调
}

interface MarkModalState {
  isOpen: boolean
  page: number
  yPosition: number
  suggestedTitle: string
}

// [ADDED] localStorage key for hint dismissal tracking
const HINT_DISMISSED_KEY = (bookId: string) => `bookmate_hint_dismissed_${bookId}`

// Toast 类型定义
type ToastType = 'success' | 'error' | 'info'
interface Toast {
  id: string
  message: string
  type: ToastType
}

const ChapterMarker: React.FC<ChapterMarkerProps> = ({
  bookId,
  isMarkingMode,
  onMarkingModeChange,
  onMarkCreated,
  onMarkDeleted,
  onChaptersUpdated,
  marks,
  currentPage,
  hasNativeChapters,
  onReparseRequest,
  showDeleteMenu: externalShowDeleteMenu,
  onDeleteMenuChange,
  onRefreshBookData // [ADDED]
}) => {
  const [showHint, setShowHint] = useState(false)
  const [modalState, setModalState] = useState<MarkModalState>({
    isOpen: false,
    page: 0,
    yPosition: 0,
    suggestedTitle: ''
  })
  const [chapterTitle, setChapterTitle] = useState('')
  const [internalShowDeleteMenu, setInternalShowDeleteMenu] = useState(false)
  // Toast 状态
  const [toasts, setToasts] = useState<Toast[]>([])

  const showDeleteMenu = externalShowDeleteMenu !== undefined ? externalShowDeleteMenu : internalShowDeleteMenu
  const setShowDeleteMenu = (show: boolean) => {
    if (onDeleteMenuChange) {
      onDeleteMenuChange(show)
    } else {
      setInternalShowDeleteMenu(show)
    }
  }

  // Toast 辅助函数
  const showToast = useCallback((message: string, type: ToastType = 'info') => {
    const id = `toast-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`
    setToasts(prev => [...prev, { id, message, type }])
    
    // 3秒后自动移除
    setTimeout(() => {
      setToasts(prev => prev.filter(t => t.id !== id))
    }, 3000)
  }, [])

  const removeToast = useCallback((id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id))
  }, [])

  // [FIXED] 使用 localStorage 记录用户是否已关闭提示，避免每次打开都显示
  useEffect(() => {
    if (!hasNativeChapters && marks.length === 0) {
      // 检查用户是否已经关闭过此书籍的提示
      const isDismissed = localStorage.getItem(HINT_DISMISSED_KEY(bookId))
      if (isDismissed) return // 用户已关闭过，不再显示

      const timer = setTimeout(() => setShowHint(true), 1500)
      return () => clearTimeout(timer)
    }
  }, [hasNativeChapters, marks.length, bookId])

  useEffect(() => {
    if (onReparseRequest && marks.length > 0) {
      onReparseRequest()
    }
  }, [marks.length, onReparseRequest])

  // ESC 快捷键监听：退出标记模式
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isMarkingMode && !modalState.isOpen) {
        exitMarkingMode()
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isMarkingMode, modalState.isOpen])

  const handleContainerClick = useCallback((e: React.MouseEvent) => {
    if (!isMarkingMode) return
    
    e.stopPropagation()
    e.preventDefault()
    
    const pageElement = document.querySelector('.react-pdf__Page') as HTMLElement
    if (!pageElement) {
      const yPosition = (e.clientY / window.innerHeight) * 100
      const suggestedTitle = `第 ${marks.length + 1} 章`
      setModalState({ isOpen: true, page: currentPage, yPosition, suggestedTitle })
      setChapterTitle(suggestedTitle)
      return
    }
    
    const pageRect = pageElement.getBoundingClientRect()
    const relativeY = e.clientY - pageRect.top
    const yPosition = Math.max(0, Math.min(100, (relativeY / pageRect.height) * 100))
    
    let suggestedTitle = `第 ${marks.length + 1} 章`
    
    const textLayer = document.querySelector('.react-pdf__Page__textContent')
    if (textLayer) {
      const textElements = textLayer.querySelectorAll('span')
      let closestText = ''
      let closestDistance = Infinity
      
      for (const el of textElements) {
        const elRect = el.getBoundingClientRect()
        const elY = elRect.top + elRect.height / 2
        const distance = Math.abs(elY - e.clientY)
        
        if (distance < closestDistance && distance < 100) {
          const text = el.textContent?.trim() || ''
          if (text.length > 2 && text.length < 100) {
            closestDistance = distance
            closestText = text
          }
        }
      }
      
      if (closestText) suggestedTitle = closestText
    }

    setModalState({ isOpen: true, page: currentPage, yPosition, suggestedTitle })
    setChapterTitle(suggestedTitle)
  }, [isMarkingMode, currentPage, marks.length])

  const handleSaveMark = useCallback(async () => {
    if (!chapterTitle.trim()) return

    try {
      const response = await fetch(`/api/books/${bookId}/chapters/mark`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          page_number: modalState.page,
          y_position: modalState.yPosition / 100,
          chapter_name: chapterTitle.trim()
        })
      })

      if (!response.ok) {
        const error = await response.json()
        alert('保存标记失败: ' + (error.detail || '未知错误'))
        return
      }

      const data = await response.json()

      const newMark: ChapterMark = {
        id: data.mark.mark_id,
        page: data.mark.page_number,
        title: data.mark.chapter_name || chapterTitle.trim(),
        yPosition: data.mark.y_position * 100,
        createdAt: data.mark.created_at
      }

      onMarkCreated(newMark)

      if (onChaptersUpdated && data.chapters) {
        onChaptersUpdated(data.chapters)
      }

      // [FIXED] 添加后不清空标记模式，支持连续添加
      setModalState(prev => ({ ...prev, isOpen: false }))
      setChapterTitle('')
      // 不调用 onMarkingModeChange(false)，保持标记模式开启以支持连续添加

      // [FIXED] 自动刷新书籍详情，确保新章节立即显示
      if (onRefreshBookData) {
        await onRefreshBookData()
      }

      // 显示 Toast 提示
      showToast('标记已保存！点击页面其他位置继续添加下一个章节', 'success')
    } catch (error) {
      alert('保存标记失败，请检查网络连接')
    }
  }, [chapterTitle, modalState, bookId, onMarkCreated, onMarkingModeChange, onChaptersUpdated, showToast])

  const handleCancelMark = useCallback(() => {
    setModalState(prev => ({ ...prev, isOpen: false }))
    setChapterTitle('')
  }, [])

  const handleDeleteMark = useCallback(async (markId: string) => {
    try {
      const response = await fetch(`/api/books/${bookId}/marks/${markId}`, { method: 'DELETE' })

      if (!response.ok) {
        const error = await response.json()
        alert('删除标记失败: ' + (error.detail || '未知错误'))
        return
      }

      const data = await response.json()

      onMarkDeleted(markId)

      if (onChaptersUpdated && data.chapters) {
        onChaptersUpdated(data.chapters)
      }

      // [FIXED] 自动刷新书籍详情，确保章节更新立即显示
      if (onRefreshBookData) {
        await onRefreshBookData()
      }

      if (marks.length <= 1) {
        setShowDeleteMenu(false)
      }
    } catch (error) {
      alert('删除标记失败，请检查网络连接')
    }
  }, [onMarkDeleted, marks.length, bookId, onChaptersUpdated, setShowDeleteMenu])

  const startMarkingMode = useCallback(() => {
    onMarkingModeChange(true)
    setShowHint(false)
    // [ADDED] 记录用户已查看过提示
    localStorage.setItem(HINT_DISMISSED_KEY(bookId), 'true')
  }, [onMarkingModeChange, bookId])

  const exitMarkingMode = useCallback(() => {
    onMarkingModeChange(false)
  }, [onMarkingModeChange])

  return (
    <>
      {/* Toast 容器 */}
      <div className="chapter-marker-toast-container">
        {toasts.map(toast => (
          <div
            key={toast.id}
            className={`chapter-marker-toast chapter-marker-toast-${toast.type}`}
            onClick={() => removeToast(toast.id)}
          >
            {toast.type === 'success' && (
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
            )}
            <span>{toast.message}</span>
          </div>
        ))}
      </div>

      {isMarkingMode && (
        <div 
          className="chapter-marker-click-layer"
          onClick={handleContainerClick}
          style={{
            position: 'fixed',
            top: '56px',
            left: 0,
            right: 0,
            bottom: 0,
            zIndex: 55,
            cursor: 'crosshair'
          }}
        />
      )}

      {isMarkingMode && (
        <div className="chapter-marker-overlay">
          {/* 常驻提示条 - 顶部固定 */}
          <div className="chapter-marker-banner">
            <div className="chapter-marker-banner-left">
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
              </svg>
              <span className="chapter-marker-banner-text">标记模式：点击页面任意位置添加章节标记</span>
            </div>
            <div className="chapter-marker-banner-center">
              <span className="chapter-marker-count">已标记 {marks.length} 个章节</span>
            </div>
            <div className="chapter-marker-banner-right">
              <span className="chapter-marker-esc-hint">按 ESC 退出</span>
              <button onClick={exitMarkingMode} className="chapter-marker-exit-btn">退出标记模式</button>
            </div>
          </div>
        </div>
      )}

      {showHint && !hasNativeChapters && (
        <div className="chapter-hint-popup" onClick={() => setShowHint(false)}>
          <div className="chapter-hint-content" onClick={e => e.stopPropagation()}>
            <svg className="w-8 h-8 text-amber-500 mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
            <p className="chapter-hint-text">
              未检测到书籍章节结构<br />
              <span className="text-sm text-slate-500">您可以手动标记章节以便导航</span>
            </p>
            <div className="chapter-hint-actions">
              <button onClick={startMarkingMode} className="chapter-hint-primary-btn">
                <svg className="w-4 h-4 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                </svg>
                标记章节
              </button>
              <button onClick={() => {
                setShowHint(false)
                // [ADDED] 记录用户已关闭提示
                localStorage.setItem(HINT_DISMISSED_KEY(bookId), 'true')
              }} className="chapter-hint-secondary-btn">稍后再说</button>
            </div>
          </div>
        </div>
      )}

      {modalState.isOpen && (
        <div className="chapter-mark-modal-overlay" onClick={handleCancelMark}>
          <div className="chapter-mark-modal" onClick={e => e.stopPropagation()}>
            <h3 className="chapter-mark-modal-title">标记章节</h3>
            <p className="chapter-mark-modal-subtitle">第 {modalState.page} 页</p>
            <div className="chapter-mark-form">
              <label className="chapter-mark-label">章节名称</label>
              <input
                type="text"
                value={chapterTitle}
                onChange={(e) => setChapterTitle(e.target.value)}
                placeholder="输入章节名称"
                className="chapter-mark-input"
                autoFocus
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleSaveMark()
                  if (e.key === 'Escape') handleCancelMark()
                }}
              />
              <p className="chapter-mark-hint">已自动提取点击位置的文本，您可以直接编辑</p>
            </div>
            <div className="chapter-mark-actions">
              <button onClick={handleCancelMark} className="chapter-mark-btn-secondary">取消</button>
              <button onClick={handleSaveMark} className="chapter-mark-btn-primary" disabled={!chapterTitle.trim()}>
                保存标记
              </button>
            </div>
          </div>
        </div>
      )}

      {showDeleteMenu && marks.length > 0 && (
        <div className="chapter-delete-menu-overlay" onClick={() => setShowDeleteMenu(false)}>
          <div className="chapter-delete-menu" onClick={e => e.stopPropagation()}>
            <h4 className="chapter-delete-title">删除标记</h4>
            <ul className="chapter-delete-list">
              {marks.map(mark => (
                <li key={mark.id} className="chapter-delete-item">
                  <span className="chapter-delete-name">{mark.title}</span>
                  <span className="chapter-delete-page">P{mark.page}</span>
                  <button onClick={() => handleDeleteMark(mark.id)} className="chapter-delete-btn" title="删除此标记">
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                    </svg>
                  </button>
                </li>
              ))}
            </ul>
            <button onClick={() => setShowDeleteMenu(false)} className="chapter-delete-close">关闭</button>
          </div>
        </div>
      )}
    </>
  )
}

export default ChapterMarker
