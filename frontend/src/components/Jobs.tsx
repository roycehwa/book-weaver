import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { jobsApi, workspaceApi, type WorkspaceBook, type WorkspaceSourceBook, type WorkspaceTextVersion } from '../api'

const pipelineLabels: Record<WorkspaceBook['pipeline_status'], string> = {
  processing: '处理中',
  needs_translation_review: '需要翻译审阅',
  needs_chapter_confirmation: '需要确认章节',
  ready_for_knowledge: '可进入知识解析',
  failed: '处理失败',
}

const modeLabels: Record<string, string> = {
  auto: '自动',
  translate: '翻译',
  preserve: '原文保留',
}

const operationLabels: Record<string, string> = {
  translate: '已走翻译路径',
  preserve: '已走原文路径',
}

const versionKindLabels: Record<WorkspaceTextVersion['kind'], string> = {
  source: '原文版',
  translated: '译文版',
  pending: '待判断',
}

const versionStatusLabel = (version: WorkspaceTextVersion) =>
  version.status_label || pipelineLabels[version.pipeline_status]

const chapterStatusClasses: Record<WorkspaceSourceBook['chapter_structure']['status'], string> = {
  confirmed: 'bg-emerald-100 text-emerald-700',
  needs_confirmation: 'bg-amber-100 text-amber-700',
  blocked: 'bg-slate-100 text-slate-600',
}

function Jobs() {
  const [sourceBooks, setSourceBooks] = useState<WorkspaceSourceBook[]>([])
  const [jobsDir, setJobsDir] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [deletingJobId, setDeletingJobId] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    let timer: number | undefined

    const loadBooks = () => {
      setError(null)
      return workspaceApi.listBooks()
        .then((result) => {
          if (!active) return result
          setSourceBooks(result.source_books || [])
          if (result.jobs_dir) setJobsDir(result.jobs_dir)
          setLoading(false)
          return result
        })
        .catch((loadError) => {
          if (active) {
            setLoading(false)
            setError(loadError instanceof Error ? loadError.message : '加载工作台失败')
          }
          return null
        })
    }

    const poll = async () => {
      const result = await loadBooks()
      if (!active) return
      const hasInFlight = (result?.source_books || []).some((sourceBook) =>
        sourceBook.text_versions.some(
          (version) =>
            version.pipeline_status === 'processing'
            || version.job_state === 'translating'
            || version.job_state === 'pre_review'
        )
      )
      if (hasInFlight) {
        timer = window.setTimeout(poll, 3000)
      }
    }

    poll()
    return () => {
      active = false
      if (timer) window.clearTimeout(timer)
    }
  }, [])

  const deleteJob = async (jobId: string, title: string) => {
    if (!window.confirm(`从书籍工作台删除这个处理版本？\n\n${title}\n\n原始上传任务工作副本会被删除；审阅控制台从 OK 同步来的成品不会被删除。`)) {
      return
    }
    setDeletingJobId(jobId)
    setError(null)
    setMessage(null)
    try {
      await jobsApi.delete(jobId)
      setSourceBooks((current) =>
        current
          .map((sourceBook) => ({
            ...sourceBook,
            text_versions: sourceBook.text_versions.filter((version) => version.job_id !== jobId),
            task_history: sourceBook.task_history.filter((task) => task.job_id !== jobId),
            task_history_count: sourceBook.task_history_count - (
              sourceBook.task_history.some((task) => task.job_id === jobId) ? 1 : 0
            ),
          }))
          .filter((sourceBook) => sourceBook.text_versions.length > 0)
      )
      setMessage('已删除该处理版本。需要替换时，可以重新上传同一本书创建新的版本。')
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : '删除处理版本失败')
    } finally {
      setDeletingJobId(null)
    }
  }

  const visibleSourceBooks = sourceBooks

  return (
    <div className="mx-auto max-w-6xl">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">书籍工作台</h1>
          <p className="mt-1 text-sm text-slate-600">
            这里按“源书”管理：一本书一行，章节结构在源书层确认；原文版和译文版是两个文本版本，重复触发和失败任务收进任务历史。
          </p>
        </div>
        <Link to="/upload" className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white">
          上传书籍
        </Link>
      </div>

      {jobsDir && (
        <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-xs text-slate-600">
          <div className="font-medium text-slate-800">本地工作目录（所有任务与产出）</div>
          <div className="mt-1 font-mono text-slate-500">{jobsDir}/</div>
          <div className="mt-2 leading-5">
            每本书一个子文件夹（任务 ID）。其中 <span className="font-mono">source/</span> 是上传原件，
            <span className="font-mono"> artifacts/</span> 是解析与翻译产出（book.json、epub、审阅文件等）。
            在 Finder 中按 ⌘⇧G 粘贴上方路径即可打开。
          </div>
        </div>
      )}

      {error && <div className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</div>}
      {message && <div className="mt-4 rounded-lg bg-emerald-50 p-3 text-sm text-emerald-800">{message}</div>}
      <div className="mt-6 overflow-hidden rounded-xl border border-slate-200 bg-white">
        {loading && (
          <div className="px-5 py-12 text-center text-sm text-slate-500">正在加载书籍工作台…</div>
        )}
        {!loading && visibleSourceBooks.map((sourceBook) => {
          return (
          <div
            key={sourceBook.source_id}
            className="border-b border-slate-100 px-5 py-4 last:border-b-0"
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="font-medium text-slate-900">{sourceBook.title}</div>
                <div className="mt-1 flex flex-wrap gap-2 text-xs text-slate-500">
                  <span>源文件：{sourceBook.source_filename || '未知'}</span>
                  <span>{sourceBook.text_versions.length} 个文本版本</span>
                  <span>{sourceBook.task_history_count} 条任务历史</span>
                  {sourceBook.hidden_task_count > 0 && <span>已隐藏 {sourceBook.hidden_task_count} 个重复/中间任务</span>}
                </div>
              </div>
              <span className={`rounded-full px-3 py-1 text-xs font-medium ${chapterStatusClasses[sourceBook.chapter_structure.status]}`}>
                {sourceBook.chapter_structure.label}
              </span>
            </div>
            <p className="mt-3 text-sm text-slate-600">{sourceBook.chapter_structure.description}</p>

            <div className="mt-4 grid gap-3 md:grid-cols-2">
              {sourceBook.text_versions.map((version) => (
                <div key={version.job_id} className="rounded-xl border border-slate-200 bg-slate-50 p-4">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <div className="font-medium text-slate-900">{versionKindLabels[version.kind]}</div>
                      <div className="mt-1 text-xs text-slate-500">
                        {operationLabels[version.text_operation || ''] || modeLabels[version.processing_mode || ''] || '等待路径判断'} · {version.job_id}
                      </div>
                    </div>
                    <span className={`rounded-full px-2 py-1 text-xs ${
                      version.pipeline_status === 'failed'
                        ? 'bg-red-100 text-red-700'
                        : version.pipeline_status === 'needs_translation_review' || version.pipeline_status === 'needs_chapter_confirmation'
                          ? 'bg-amber-100 text-amber-700'
                          : version.pipeline_status === 'ready_for_knowledge'
                            ? 'bg-purple-100 text-purple-700'
                            : 'bg-blue-100 text-blue-700'
                    }`}>
                      {versionStatusLabel(version)}
                    </span>
                  </div>
                  <div className="mt-3 h-2 rounded-full bg-slate-200">
                    <div
                      className="h-2 rounded-full bg-primary-600"
                      style={{ width: `${version.progress_percent}%` }}
                    />
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">
                    <Link to={`/jobs/${version.job_id}`} className="font-medium text-primary-700">
                      {version.next_action?.label || '查看处理详情'}
                    </Link>
                    <button
                      type="button"
                      onClick={() => deleteJob(version.job_id, version.title)}
                      disabled={deletingJobId === version.job_id}
                      className="text-xs font-medium text-red-600 disabled:opacity-50"
                    >
                      {deletingJobId === version.job_id ? '删除中...' : '删除版本'}
                    </button>
                  </div>
                </div>
              ))}
            </div>

            <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">
              {sourceBook.chapter_structure.job_id && (
                <Link to={`/jobs/${sourceBook.chapter_structure.job_id}`} className="text-xs font-medium text-purple-700">
                  章节目录确认
                </Link>
              )}
              <span className="text-xs text-slate-500">
                任务历史默认折叠；需要替换源文件时，可删除当前文本版本后重新上传。
              </span>
            </div>
          </div>
        )})}
        {!loading && !visibleSourceBooks.length && !error && (
          <div className="px-5 py-12 text-center text-sm text-slate-500">
            还没有由 BookMate 发起的处理任务。上传 PDF 或 EPUB 后，会在这里显示全流程状态；已有译稿请到审阅控制台同步和审阅。
          </div>
        )}
      </div>
    </div>
  )
}

export default Jobs
