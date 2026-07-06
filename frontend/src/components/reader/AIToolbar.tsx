import React from 'react'

interface AIToolbarProps {
  onOverviewClick: () => void
  onSummaryClick: () => void
  showOverview: boolean
  showSummary: boolean
  overviewLoading: boolean
  summaryLoading: boolean
  hasCurrentChapter: boolean
  isSingleChapter?: boolean
  onChapterSplitClick?: () => void
  chapterSplitLoading?: boolean
  chapterSplitProgress?: number
}

const AIToolbar: React.FC<AIToolbarProps> = ({
  onOverviewClick,
  onSummaryClick,
  showOverview,
  showSummary,
  overviewLoading,
  summaryLoading,
  hasCurrentChapter
  // Note: isSingleChapter, onChapterSplitClick, chapterSplitLoading, chapterSplitProgress reserved for future use
}) => {
  return (
    <div className="flex items-center space-x-2">
      {/* AI Overview Button */}
      <button
        onClick={onOverviewClick}
        disabled={overviewLoading}
        className={`flex items-center space-x-1 px-3 py-2 rounded-lg transition-all duration-200 disabled:opacity-50 ${
          showOverview 
            ? 'bg-purple-100 text-purple-700 ring-2 ring-purple-300' 
            : 'bg-purple-50 text-purple-700 hover:bg-purple-100 hover:shadow-sm'
        }`}
        title="AI 全文概览"
        data-testid="ai-overview"
      >
        {overviewLoading ? (
          <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-purple-700"></div>
        ) : (
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
          </svg>
        )}
        <span className="text-sm font-medium hidden md:inline">AI概览</span>
      </button>

      {/* Chapter Summary Button */}
      <button
        onClick={onSummaryClick}
        disabled={summaryLoading || !hasCurrentChapter}
        className={`flex items-center space-x-1 px-3 py-2 rounded-lg transition-all duration-200 disabled:opacity-50 ${
          showSummary 
            ? 'bg-blue-100 text-blue-700 ring-2 ring-blue-300' 
            : 'bg-blue-50 text-blue-700 hover:bg-blue-100 hover:shadow-sm'
        }`}
        title="章节摘要"
        data-testid="chapter-summary"
      >
        {summaryLoading ? (
          <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-blue-700"></div>
        ) : (
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
        )}
        <span className="text-sm font-medium hidden md:inline">章节摘要</span>
      </button>
    </div>
  )
}

export default AIToolbar
