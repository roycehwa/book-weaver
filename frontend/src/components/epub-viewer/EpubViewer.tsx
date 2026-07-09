import React, { useCallback, useEffect, useMemo, useState } from 'react'
import './EpubViewer.css'

export interface EpubViewerProps {
  url: string
  onPageChange?: (page: number) => void
  onDocumentLoad?: (numPages: number) => void
  initialPage?: number
  initialScale?: number
  className?: string
}

interface PageEntry {
  index: number
  pageNumber: number
  pageLabel: string
  chapterTitle: string
  chapterHref: string
  pageAnchor: string
  pageUrl: string
}

const MIN_SCALE = 0.5
const MAX_SCALE = 2.0
const SCALE_STEP = 0.1

function buildJobId(url: string): string | null {
  const match = url.match(/\/api\/jobs\/([^/]+)\/source/i)
  return match ? decodeURIComponent(match[1]) : null
}

function apiBase(): string {
  return (import.meta.env?.VITE_API_BASE as string | undefined) || '/api'
}

function buildPagesUrl(jobId: string): string {
  return `${apiBase()}/jobs/${encodeURIComponent(jobId)}/epub/pages`
}

function pageLabelText(label: string, num: number): string {
  if (label) return label
  if (num > 0) return String(num)
  return '—'
}

export const EpubViewer: React.FC<EpubViewerProps> = ({
  url,
  onPageChange,
  onDocumentLoad,
  initialPage = 1,
  initialScale = 1.0,
  className = '',
}) => {
  const [page, setPage] = useState<number>(initialPage > 0 ? initialPage : 1)
  const [scale, setScale] = useState<number>(initialScale)
  const [pages, setPages] = useState<PageEntry[]>([])
  const [loading, setLoading] = useState<boolean>(true)
  const [error, setError] = useState<string | null>(null)
  const [inputPage, setInputPage] = useState<string>(String(initialPage))
  const [pageHtml, setPageHtml] = useState<string>('')
  const [pageLoading, setPageLoading] = useState<boolean>(false)

  // 加载页索引
  useEffect(() => {
    const jobId = buildJobId(url)
    if (!jobId) {
      setError('无法识别 job id（URL 格式异常）')
      setLoading(false)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    setPages([])
    ;(async () => {
      try {
        const res = await fetch(buildPagesUrl(jobId), { credentials: 'omit', cache: 'no-store' })
        if (!res.ok) throw new Error('HTTP ' + res.status + ' ' + (res.statusText || ''))
        const payload = (await res.json()) as {
          total: number
          pages: Array<{
            index: number
            page_number: number
            page_label?: string
            chapter_title: string
            chapter_href: string
            page_anchor: string
            page_url: string
          }>
        }
        if (cancelled) return
        const list: PageEntry[] = (payload.pages || []).map((p) => ({
          index: p.index,
          pageNumber: p.page_number,
          pageLabel: p.page_label || '',
          chapterTitle: p.chapter_title,
          chapterHref: p.chapter_href,
          pageAnchor: p.page_anchor,
          pageUrl: p.page_url,
        }))
        if (list.length === 0) throw new Error('EPUB 页索引为空')
        setPages(list)
        setLoading(false)
        onDocumentLoad?.(list.length)
      } catch (err) {
        if (cancelled) return
        setError(err instanceof Error ? err.message : 'EPUB 页索引加载失败')
        setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [url, onDocumentLoad])

  // 边界同步：进入页面时使用 initialPage（1-based）；若超出范围则夹紧
  useEffect(() => {
    if (pages.length === 0) return
    const bounded = Math.min(Math.max(1, initialPage), pages.length)
    setPage(bounded)
    setInputPage(String(bounded))
  }, [initialPage, pages.length])

  useEffect(() => {
    onPageChange?.(page)
  }, [page, onPageChange])

  const current = useMemo(() => pages[page - 1], [pages, page])

  // 加载当前页 HTML（直接 fetch 后端渲染好的 HTML，注入到 div，避免 iframe 闪烁）
  useEffect(() => {
    if (!current) return
    let cancelled = false
    setPageLoading(true)
    setPageHtml('')
    ;(async () => {
      try {
        const res = await fetch(current.pageUrl, { credentials: 'omit', cache: 'no-store' })
        if (!res.ok) throw new Error('HTTP ' + res.status + ' ' + (res.statusText || ''))
        const html = await res.text()
        if (cancelled) return
        setPageHtml(html)
        setPageLoading(false)
      } catch (err) {
        if (cancelled) return
        setPageHtml(
          `<div style="padding:24px;color:#b91c1c;font-family:sans-serif;">页面加载失败：${err instanceof Error ? err.message : 'unknown'}</div>`,
        )
        setPageLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [current])

  const goToPrev = useCallback(() => {
    setPage((current) => {
      const next = Math.max(1, current - 1)
      setInputPage(String(next))
      return next
    })
  }, [])

  const goToNext = useCallback(() => {
    setPage((current) => {
      const next = Math.min(pages.length, current + 1)
      setInputPage(String(next))
      return next
    })
  }, [pages.length])

  const handlePageSubmit = useCallback(
    (value: string) => {
      const parsed = parseInt(value, 10)
      if (Number.isNaN(parsed)) {
        setInputPage(String(page))
        return
      }
      const bounded = Math.max(1, Math.min(pages.length, parsed))
      setPage(bounded)
      setInputPage(String(bounded))
    },
    [page, pages.length],
  )

  const zoomIn = useCallback(() => setScale((s) => Math.min(MAX_SCALE, s + SCALE_STEP)), [])
  const zoomOut = useCallback(() => setScale((s) => Math.max(MIN_SCALE, s - SCALE_STEP)), [])
  const resetZoom = useCallback(() => setScale(1.0), [])

  return (
    <div className={`epub-viewer ${className}`}>
      <div className="epub-viewer__toolbar">
        <div className="epub-viewer__page-nav">
          <button
            type="button"
            className="epub-viewer__btn"
            onClick={goToPrev}
            disabled={page <= 1}
            aria-label="上一页"
            title="上一页"
          >
            ‹
          </button>
          <div className="epub-viewer__page-info">
            <input
              type="text"
              className="epub-viewer__page-input"
              value={inputPage}
              onChange={(event) => setInputPage(event.target.value.replace(/[^0-9]/g, ''))}
              onKeyDown={(event) => {
                if (event.key === 'Enter') handlePageSubmit(inputPage)
              }}
              onBlur={() => handlePageSubmit(inputPage)}
              aria-label="页码"
            />
            <span className="epub-viewer__page-separator">/</span>
            <span className="epub-viewer__page-total">{pages.length || '—'}</span>
          </div>
          <button
            type="button"
            className="epub-viewer__btn"
            onClick={goToNext}
            disabled={page >= pages.length}
            aria-label="下一页"
            title="下一页"
          >
            ›
          </button>
        </div>
        <div className="epub-viewer__zoom-controls">
          <button
            type="button"
            className="epub-viewer__btn"
            onClick={zoomOut}
            disabled={scale <= MIN_SCALE}
            aria-label="缩小"
            title="缩小"
          >
            −
          </button>
          <button
            type="button"
            className="epub-viewer__btn epub-viewer__btn--reset"
            onClick={resetZoom}
            title="重置缩放"
          >
            {Math.round(scale * 100)}%
          </button>
          <button
            type="button"
            className="epub-viewer__btn"
            onClick={zoomIn}
            disabled={scale >= MAX_SCALE}
            aria-label="放大"
            title="放大"
          >
            +
          </button>
        </div>
      </div>
      {loading && (
        <div className="epub-viewer__loading">
          <div className="epub-viewer__spinner" />
          <p>正在加载 EPUB…</p>
        </div>
      )}
      {error && (
        <div className="epub-viewer__error">
          <p className="epub-viewer__error-title">加载失败</p>
          <p className="epub-viewer__error-message">{error}</p>
          <p className="epub-viewer__error-message" style={{ wordBreak: 'break-all' }}>URL：{url}</p>
        </div>
      )}
      {!loading && !error && current && (
        <div className="epub-viewer__container">
          {pageLoading && (
            <div className="epub-viewer__page-loading">
              <div className="epub-viewer__spinner epub-viewer__spinner--small" />
            </div>
          )}
          <div
            className="epub-viewer__page"
            style={{ transform: `scale(${scale})`, transformOrigin: 'top center' }}
            data-page-index={current.index}
          >
            <iframe
              title="EPUB 页面预览"
              className={`epub-viewer__content epub-viewer__frame ${pageLoading ? 'is-loading' : 'is-ready'}`}
              srcDoc={pageHtml}
              sandbox=""
            />
          </div>
        </div>
      )}
      {!loading && !error && current && (
        <div className="epub-viewer__footer">
          <span>第 {page} 页 / 共 {pages.length} 页</span>
          <span className="epub-viewer__footer-title" title={current.chapterTitle}>
            原书第 {pageLabelText(current.pageLabel, current.pageNumber)} 页 · {current.chapterTitle}
          </span>
        </div>
      )}
    </div>
  )
}

export default EpubViewer
