import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'

interface Book {
  book_id: string
  title: string
  total_chapters: number
  total_pages?: number
}

const Library = () => {
  const [books, setBooks] = useState<Book[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [deletingBookId, setDeletingBookId] = useState<string | null>(null)
  const [showConfirmDialog, setShowConfirmDialog] = useState<string | null>(null)

  useEffect(() => {
    const fetchBooks = async () => {
      try {
        const response = await fetch('/api/books')
        if (!response.ok) {
          throw new Error('Failed to fetch books')
        }
        const data = await response.json()
        setBooks(data.books || [])
      } catch (err) {
        console.error('Failed to fetch books:', err)
        setError('无法加载书籍列表')
      } finally {
        setLoading(false)
      }
    }

    fetchBooks()
  }, [])

  const handleDeleteClick = (e: React.MouseEvent, bookId: string) => {
    e.preventDefault()
    e.stopPropagation()
    setShowConfirmDialog(bookId)
  }

  const confirmDelete = async (bookId: string) => {
    setDeletingBookId(bookId)
    setShowConfirmDialog(null)
    
    try {
      // [FIXED] 添加 /api/ 前缀统一 API 路径
      const response = await fetch(`/api/books/${bookId}`, {
        method: 'DELETE',
      })
      
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(errorData.detail || 'Failed to delete book')
      }
      
      // Remove book from list
      setBooks(books.filter(book => book.book_id !== bookId))
      
      // [FIXED] 清理 localStorage 中的阅读进度（如果删除的是当前正在阅读的书籍）
      try {
        const progressStr = localStorage.getItem('readingProgress')
        if (progressStr) {
          const progress = JSON.parse(progressStr)
          if (progress.bookId === bookId) {
            localStorage.removeItem('readingProgress')
          }
        }
      } catch (e) {
        console.error('Failed to clean up reading progress:', e)
      }
    } catch (err) {
      console.error('Failed to delete book:', err)
      const errorMessage = err instanceof Error ? err.message : '删除失败，请重试'
      setError(errorMessage)
    } finally {
      setDeletingBookId(null)
    }
  }

  const cancelDelete = () => {
    setShowConfirmDialog(null)
  }

  if (loading) {
    return (
      <div className="flex justify-center items-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="text-center py-16">
        <p className="text-red-500 mb-4">{error}</p>
        <button 
          onClick={() => window.location.reload()}
          className="text-primary-600 hover:text-primary-700 font-medium"
        >
          刷新页面重试
        </button>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h2 className="text-2xl font-bold text-slate-900">我的书库</h2>
        <Link
          to="/upload"
          className="px-4 py-2 bg-primary-600 text-white font-medium rounded-lg hover:bg-primary-700 transition-colors"
        >
          + 上传新书
        </Link>
      </div>

      {books.length === 0 ? (
        <div className="text-center py-16 bg-white rounded-xl border border-dashed border-slate-300">
          <p className="text-slate-500 mb-4">书库空空如也</p>
          <Link
            to="/upload"
            className="text-primary-600 hover:text-primary-700 font-medium"
          >
            上传您的第一本书 →
          </Link>
        </div>
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-6">
          {books.map((book) => (
            <div
              key={book.book_id}
              className="group bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden hover:shadow-md transition-shadow relative"
            >
              {/* Delete button - top right corner */}
              <button
                onClick={(e) => handleDeleteClick(e, book.book_id)}
                disabled={deletingBookId === book.book_id}
                className="absolute top-2 right-2 z-20 p-2 bg-white/90 hover:bg-red-50 text-slate-400 hover:text-red-500 rounded-full shadow-sm opacity-0 group-hover:opacity-100 transition-all duration-200"
                title="删除书籍"
              >
                {deletingBookId === book.book_id ? (
                  <svg className="w-4 h-4 animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                ) : (
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                  </svg>
                )}
              </button>

              <Link to={`/book/${book.book_id}`} className="block">
                <div className="aspect-[3/4] bg-slate-100 flex items-center justify-center relative">
                  <div className="text-slate-400">
                    <svg className="w-16 h-16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
                    </svg>
                  </div>
                  {/* Chapter count badge */}
                  <div className="absolute top-2 left-2">
                    {book.total_chapters <= 1 ? (
                      <span className="inline-flex items-center px-2 py-1 rounded-md text-xs font-medium bg-blue-100 text-blue-700">
                        全文
                      </span>
                    ) : (
                      <span className="inline-flex items-center px-2 py-1 rounded-md text-xs font-medium bg-green-100 text-green-700">
                        {book.total_chapters} 章
                      </span>
                    )}
                  </div>
                </div>
                <div className="p-4">
                  <h3 className="font-semibold text-slate-900 truncate group-hover:text-primary-600 transition-colors">
                    {book.title}
                  </h3>
                  <p className="text-sm text-slate-500 mt-1">
                    {book.total_pages && book.total_pages > 0 ? `${book.total_pages} 页` : ''}
                  </p>
                </div>
              </Link>
            </div>
          ))}
        </div>
      )}

      {/* Confirm Dialog */}
      {showConfirmDialog && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 max-w-sm w-full mx-4 shadow-xl">
            <h3 className="text-lg font-semibold text-slate-900 mb-2">确认删除</h3>
            <p className="text-slate-600 mb-6">
              确定要删除《{books.find(b => b.book_id === showConfirmDialog)?.title}》吗？此操作不可恢复。
            </p>
            <div className="flex gap-3 justify-end">
              <button
                onClick={cancelDelete}
                className="px-4 py-2 text-slate-600 hover:text-slate-800 font-medium transition-colors"
              >
                取消
              </button>
              <button
                onClick={() => confirmDelete(showConfirmDialog)}
                className="px-4 py-2 bg-red-500 hover:bg-red-600 text-white font-medium rounded-lg transition-colors"
              >
                删除
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default Library
