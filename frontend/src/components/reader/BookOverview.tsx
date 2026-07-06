import React from 'react'

interface BookInfo {
  title: string
  author: string
  description: string
  keyPoints: string[]
  readingSuggestions: string[]
}

interface BookOverviewProps {
  bookInfo: BookInfo
  isOpen: boolean
  onClose: () => void
}

const BookOverview: React.FC<BookOverviewProps> = ({
  bookInfo,
  isOpen,
  onClose
}) => {
  return (
    <>
      {/* Overlay */}
      {isOpen && (
        <div 
          className="book-overview-overlay"
          onClick={onClose}
        />
      )}
      
      {/* Panel */}
      <div className={`book-overview ${isOpen ? 'open' : ''}`}>
        <div className="book-overview-header">
          <div className="book-overview-title">
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <span>AI 概览</span>
          </div>
          <button
            onClick={onClose}
            className="book-overview-close"
            title="关闭"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="book-overview-content">
          {/* Book Info Card */}
          <div className="overview-card book-info-card">
            <div className="book-cover-placeholder">
              <svg className="w-10 h-10" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
              </svg>
            </div>
            <div className="book-meta">
              <h3 className="book-title">{bookInfo.title}</h3>
              <p className="book-author">
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                </svg>
                {bookInfo.author}
              </p>
            </div>
          </div>

          {/* AI Description */}
          <div className="overview-card">
            <div className="card-header">
              <div className="card-icon ai-icon">
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
                </svg>
              </div>
              <h4 className="card-title">AI 简介</h4>
            </div>
            <p className="card-content">
              {bookInfo.description}
            </p>
          </div>

          {/* Key Points */}
          <div className="overview-card">
            <div className="card-header">
              <div className="card-icon keypoints-icon">
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
                </svg>
              </div>
              <h4 className="card-title">关键论点</h4>
            </div>
            <ul className="keypoints-list">
              {bookInfo.keyPoints.map((point, index) => (
                <li key={index} className="keypoint-item">
                  <span className="keypoint-number">{index + 1}</span>
                  <span className="keypoint-text">{point}</span>
                </li>
              ))}
            </ul>
          </div>

          {/* Reading Suggestions */}
          <div className="overview-card">
            <div className="card-header">
              <div className="card-icon suggestions-icon">
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
                </svg>
              </div>
              <h4 className="card-title">阅读建议</h4>
            </div>
            <ul className="suggestions-list">
              {bookInfo.readingSuggestions.map((suggestion, index) => (
                <li key={index} className="suggestion-item">
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  <span>{suggestion}</span>
                </li>
              ))}
            </ul>
          </div>

          {/* Footer Note */}
          <div className="overview-footer">
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
            </svg>
            <span>由 AI 自动生成，仅供参考</span>
          </div>
        </div>
      </div>
    </>
  )
}

export default BookOverview
