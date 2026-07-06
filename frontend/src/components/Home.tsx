import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { workspaceApi, type WorkspaceBook } from '../api'

interface ReadingProgress {
  bookId: string
  bookTitle: string
  page: number
  lastReadAt: string
}

const Home = () => {
  const navigate = useNavigate()
  const [books, setBooks] = useState<WorkspaceBook[]>([])
  const [recentProgress, setRecentProgress] = useState<ReadingProgress | null>(null)
  const [stats, setStats] = useState({
    totalBooks: 0,
    processing: 0,
    actionRequired: 0,
    readyForKnowledge: 0,
  })

  useEffect(() => {
    const fetchBooks = async () => {
      try {
        const data = await workspaceApi.listBooks()
        const booksList = data.books || []
        setBooks(booksList)
        setStats({
          totalBooks: booksList.length,
          processing: booksList.filter((book) => book.pipeline_status === 'processing').length,
          actionRequired: booksList.filter((book) =>
            book.pipeline_status === 'needs_translation_review' ||
            book.pipeline_status === 'needs_chapter_confirmation'
          ).length,
          readyForKnowledge: booksList.filter((book) => book.pipeline_status === 'ready_for_knowledge').length,
        })
      } catch (error) {
        console.error('Failed to fetch workspace books:', error)
      }
    }

    // Load recent reading progress from localStorage
    const loadRecentProgress = () => {
      try {
        const progress = localStorage.getItem('readingProgress')
        if (progress) {
          setRecentProgress(JSON.parse(progress))
        }
      } catch (error) {
        console.error('Failed to load reading progress:', error)
      }
    }

    fetchBooks()
    loadRecentProgress()
  }, [])

  // Get the most recently added book
  const getMostRecentBook = () => {
    if (books.length === 0) return null
    return [...books].sort((a, b) => {
      const dateA = a.updated_at ? new Date(a.updated_at).getTime() : 0
      const dateB = b.updated_at ? new Date(b.updated_at).getTime() : 0
      return dateB - dateA
    })[0]
  }

  const handleContinueReading = () => {
    if (recentProgress) {
      navigate(`/reader/${recentProgress.bookId}`)
    } else {
      const recentBook = getMostRecentBook()
      if (recentBook) {
        navigate(`/jobs/${recentBook.book_id}`)
      } else {
        navigate('/jobs')
      }
    }
  }

  const recentBook = getMostRecentBook()

  return (
    <div className="space-y-8">
      {/* Hero Section */}
      <section className="text-center py-12 bg-gradient-to-br from-primary-50 to-white rounded-2xl border border-primary-100">
        <h1 className="text-4xl font-bold text-slate-900 mb-4">
          欢迎来到 BookMate
        </h1>
        <p className="text-lg text-slate-600 mb-8 max-w-2xl mx-auto px-4">
          以书籍为中心管理导入、解析、翻译、审阅、章节确认和知识解析前准备。
        </p>
        <div className="flex justify-center space-x-4">
          <Link
            to="/jobs"
            className="px-6 py-3 bg-primary-600 text-white font-medium rounded-lg hover:bg-primary-700 transition-colors shadow-sm"
          >
            进入书籍工作台
          </Link>
          <Link
            to="/upload"
            className="px-6 py-3 bg-white text-slate-700 font-medium rounded-lg border border-slate-300 hover:bg-slate-50 transition-colors"
          >
            上传书籍
          </Link>
        </div>
      </section>

      {/* Continue Reading Card - Show if there's recent progress */}
      {(recentProgress || recentBook) && (
        <section className="bg-gradient-to-r from-amber-50 to-orange-50 rounded-xl p-6 border border-amber-200">
          <div className="flex items-center justify-between flex-wrap gap-4">
            <div className="flex items-center space-x-4">
              <div className="w-12 h-12 bg-amber-100 rounded-lg flex items-center justify-center">
                <svg className="w-6 h-6 text-amber-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
                </svg>
              </div>
              <div>
                <h2 className="text-lg font-semibold text-slate-900">
                  继续阅读
                </h2>
                <p className="text-slate-600">
                  {recentProgress ? (
                    <>
                      《{recentProgress.bookTitle}》- 第 {recentProgress.page} 页
                      <span className="text-sm text-slate-400 ml-2">
                        (上次阅读: {new Date(recentProgress.lastReadAt).toLocaleDateString()})
                      </span>
                    </>
                  ) : (
                    <>最近处理: 《{recentBook?.title}》</>
                  )}
                </p>
              </div>
            </div>
            <button
              onClick={handleContinueReading}
              className="px-5 py-2 bg-amber-500 text-white font-medium rounded-lg hover:bg-amber-600 transition-colors"
            >
              继续阅读 →
            </button>
          </div>
        </section>
      )}

      {/* Stats Overview */}
      <section className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="bg-white rounded-xl p-4 border border-slate-200 text-center">
          <div className="text-2xl font-bold text-primary-600">{stats.totalBooks}</div>
          <div className="text-sm text-slate-500">工作台书籍</div>
        </div>
        <div className="bg-white rounded-xl p-4 border border-slate-200 text-center">
          <div className="text-2xl font-bold text-primary-600">{stats.processing}</div>
          <div className="text-sm text-slate-500">处理中</div>
        </div>
        <div className="bg-white rounded-xl p-4 border border-slate-200 text-center">
          <div className="text-2xl font-bold text-primary-600">{stats.actionRequired}</div>
          <div className="text-sm text-slate-500">等待处理</div>
        </div>
        <div className="bg-white rounded-xl p-4 border border-slate-200 text-center">
          <div className="text-2xl font-bold text-primary-600">{stats.readyForKnowledge}</div>
          <div className="text-sm text-slate-500">知识就绪</div>
        </div>
      </section>

      {/* Functional Feature Cards */}
      <section>
        <h2 className="text-xl font-bold text-slate-900 mb-4">快捷操作</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {/* Feature 1: Book Management */}
          <button
            onClick={() => navigate('/jobs')}
            className="text-left p-6 bg-white rounded-xl shadow-sm border border-slate-200 hover:shadow-md hover:border-primary-300 transition-all group"
          >
            <div className="w-12 h-12 bg-primary-100 rounded-lg flex items-center justify-center mb-4 group-hover:bg-primary-200 transition-colors">
              <svg className="w-6 h-6 text-primary-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
              </svg>
            </div>
            <h3 className="text-lg font-semibold text-slate-900 mb-2 group-hover:text-primary-600 transition-colors">
              海量书籍管理
            </h3>
            <p className="text-slate-600 text-sm mb-3">
              快速查看多本书的处理状态，并切换到下一步动作。
            </p>
            <span className="text-primary-600 text-sm font-medium group-hover:underline">
              进入工作台 →
            </span>
          </button>

          {/* Feature 2: Immersive Reading */}
          <button
            onClick={handleContinueReading}
            disabled={!recentBook && !recentProgress}
            className="text-left p-6 bg-white rounded-xl shadow-sm border border-slate-200 hover:shadow-md hover:border-primary-300 transition-all group disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:border-slate-200"
          >
            <div className="w-12 h-12 bg-primary-100 rounded-lg flex items-center justify-center mb-4 group-hover:bg-primary-200 transition-colors">
              <svg className="w-6 h-6 text-primary-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
              </svg>
            </div>
            <h3 className="text-lg font-semibold text-slate-900 mb-2 group-hover:text-primary-600 transition-colors">
              沉浸式阅读
            </h3>
            <p className="text-slate-600 text-sm mb-3">
              {recentProgress ? (
                <>继续阅读《{recentProgress.bookTitle}》</>
              ) : recentBook ? (
                <>查看最近处理的《{recentBook.title}》</>
              ) : (
                <>您还没有书籍，先上传一本吧</>
              )}
            </p>
            <span className="text-primary-600 text-sm font-medium group-hover:underline">
              {recentBook || recentProgress ? '开始阅读 →' : '暂无书籍'}
            </span>
          </button>

          {/* Feature 3: Upload */}
          <button
            onClick={() => navigate('/upload')}
            className="text-left p-6 bg-white rounded-xl shadow-sm border border-slate-200 hover:shadow-md hover:border-primary-300 transition-all group"
          >
            <div className="w-12 h-12 bg-primary-100 rounded-lg flex items-center justify-center mb-4 group-hover:bg-primary-200 transition-colors">
              <svg className="w-6 h-6 text-primary-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
              </svg>
            </div>
            <h3 className="text-lg font-semibold text-slate-900 mb-2 group-hover:text-primary-600 transition-colors">
              上传并处理
            </h3>
            <p className="text-slate-600 text-sm mb-3">
              上传 PDF 或 EPUB，先检查是否已有处理记录，再选择翻译、自动判断或原文保留路径。
            </p>
            <span className="text-primary-600 text-sm font-medium group-hover:underline">
              上传书籍 →
            </span>
          </button>
        </div>
      </section>

      {/* Quick Tips */}
      {books.length === 0 && (
        <section className="bg-blue-50 rounded-xl p-6 border border-blue-200">
          <div className="flex items-start space-x-4">
            <div className="w-10 h-10 bg-blue-100 rounded-lg flex items-center justify-center flex-shrink-0">
              <svg className="w-5 h-5 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <div>
              <h3 className="font-semibold text-slate-900 mb-1">快速开始</h3>
              <p className="text-slate-600 text-sm">
                工作台还是空的。上传第一本 PDF 或 EPUB 后，这里会显示从导入到知识解析入口的完整状态。
              </p>
            </div>
          </div>
        </section>
      )}
    </div>
  )
}

export default Home
