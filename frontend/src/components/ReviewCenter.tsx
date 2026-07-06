import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { reviewApi, type ReviewProjectListItem } from '../api'

const statusLabel: Record<ReviewProjectListItem['review_status'], string> = {
  unreviewed: '未审阅',
  in_review: '审阅中',
  reviewed: '审阅完成',
  exported: '已输出',
}

const statusClass: Record<ReviewProjectListItem['review_status'], string> = {
  unreviewed: 'bg-slate-100 text-slate-700',
  in_review: 'bg-amber-100 text-amber-800',
  reviewed: 'bg-emerald-100 text-emerald-800',
  exported: 'bg-purple-100 text-purple-800',
}

function formatTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString('zh-CN', { hour12: false })
}

function ReviewCenter() {
  const navigate = useNavigate()
  const [projects, setProjects] = useState<ReviewProjectListItem[]>([])
  const [loading, setLoading] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<'all' | ReviewProjectListItem['review_status']>('all')
  const [sortMode, setSortMode] = useState<'updated' | 'issues' | 'progress'>('updated')
  const [pendingRemoval, setPendingRemoval] = useState<{
    project: ReviewProjectListItem
    mode: 'hide' | 'delete'
  } | null>(null)
  const [removing, setRemoving] = useState(false)

  const loadProjects = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await reviewApi.listProjects()
      setProjects(data.projects || [])
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载审阅项目失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadProjects()
  }, [])

  const filteredProjects = useMemo(() => {
    const normalized = query.trim().toLowerCase()
    const filtered = projects.filter((project) => {
      if (statusFilter !== 'all' && project.review_status !== statusFilter) return false
      if (!normalized) return true
      return (
        project.title.toLowerCase().includes(normalized) ||
        project.run_dir.toLowerCase().includes(normalized) ||
        (project.source_path || '').toLowerCase().includes(normalized)
      )
    })
    const sorted = [...filtered]
    if (sortMode === 'issues') {
      sorted.sort((a, b) => b.qa_items_open - a.qa_items_open || b.updated_at.localeCompare(a.updated_at))
    } else if (sortMode === 'progress') {
      sorted.sort((a, b) => a.progress_percent - b.progress_percent || b.updated_at.localeCompare(a.updated_at))
    } else {
      sorted.sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    }
    return sorted
  }, [projects, query, statusFilter, sortMode])

  const openReview = (runDir: string) => {
    navigate(`/review?runDir=${encodeURIComponent(runDir)}`)
  }

  const syncProjects = async () => {
    setSyncing(true)
    setError(null)
    setMessage(null)
    try {
      const result = await reviewApi.syncProjects()
      await loadProjects()
      setMessage(`同步完成：新增 ${result.imported}，跳过 ${result.skipped}，失败 ${result.failed}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : '同步新书失败')
    } finally {
      setSyncing(false)
    }
  }

  const removeProject = async () => {
    if (!pendingRemoval) return
    setRemoving(true)
    setError(null)
    setMessage(null)
    try {
      await reviewApi.removeProject(pendingRemoval.project.run_dir, pendingRemoval.mode)
      setProjects((current) =>
        current.filter((project) => project.run_dir !== pendingRemoval.project.run_dir)
      )
      setMessage(
        pendingRemoval.mode === 'delete'
          ? '工作副本已删除；原始书籍和最终导出均已保留。'
          : '项目已从审阅控制台移除，磁盘文件未删除。'
      )
      setPendingRemoval(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : '处理项目失败')
    } finally {
      setRemoving(false)
    }
  }

  return (
    <div className="mx-auto max-w-6xl px-4 py-6">
      <div className="rounded-xl border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-xl font-semibold text-slate-900">审阅控制台</h1>
          <button
            onClick={() => navigate('/')}
            className="rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-700 hover:bg-slate-50"
          >
            返回主页面
          </button>
        </div>
        <p className="mt-1 text-sm text-slate-600">
          这里显示可审阅的译稿：包括 BookMate 任务生成的审阅副本，以及从 Desktop/文档/OK 同步来的历史输出。
          它不是上传任务清单；需要查看一本书从上传到知识解析的处理状态，请回到书籍工作台。
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          <input
            className="min-w-64 flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm"
            placeholder="搜索书名或目录..."
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <select
            className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value as 'all' | ReviewProjectListItem['review_status'])}
          >
            <option value="all">全部状态</option>
            <option value="unreviewed">未审阅</option>
            <option value="in_review">审阅中未输出</option>
            <option value="reviewed">审阅完成</option>
            <option value="exported">已输出</option>
          </select>
          <select
            className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
            value={sortMode}
            onChange={(event) => setSortMode(event.target.value as 'updated' | 'issues' | 'progress')}
          >
            <option value="updated">最近更新</option>
            <option value="issues">待处理问题优先</option>
            <option value="progress">进度从低到高</option>
          </select>
          <button
            onClick={syncProjects}
            disabled={syncing}
            className="rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            {syncing ? '同步中...' : '同步新书'}
          </button>
        </div>
      </div>

      {loading && <div className="mt-4 text-sm text-slate-600">正在加载项目列表...</div>}
      {error && <div className="mt-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
      {message && <div className="mt-4 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{message}</div>}

      {!loading && !error && (
        <div className="mt-4 overflow-hidden rounded-xl border border-slate-200 bg-white">
          <div className="border-b border-slate-200 px-4 py-3 text-sm text-slate-600">
            共 {filteredProjects.length} 本（总项目 {projects.length}）
          </div>
          <div className="divide-y divide-slate-100">
            {filteredProjects.map((project) => (
              <div key={project.run_dir} className="px-4 py-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold text-slate-900">{project.title}</div>
                    <div className="truncate text-xs text-slate-500">{project.run_dir}</div>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className={`rounded-full px-2 py-1 text-xs ${statusClass[project.review_status]}`}>
                      {statusLabel[project.review_status]}
                    </span>
                    {project.workspace_job_id ? (
                      <button
                        onClick={() => navigate(`/jobs/${project.workspace_job_id}`)}
                        className="rounded-lg border border-purple-300 px-3 py-1.5 text-xs text-purple-700"
                      >
                        回到工作台
                      </button>
                    ) : (
                      <span className="rounded-full bg-slate-100 px-2 py-1 text-xs text-slate-600">
                        历史译稿
                      </span>
                    )}
                    <button
                      onClick={() => openReview(project.run_dir)}
                      className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white"
                    >
                      {project.review_completed ? '查看审阅' : project.review_status === 'unreviewed' ? '开始审阅' : '继续审阅'}
                    </button>
                    <button
                      onClick={() => setPendingRemoval({ project, mode: 'hide' })}
                      className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs text-slate-700"
                    >
                      移除
                    </button>
                    <button
                      onClick={() => setPendingRemoval({ project, mode: 'delete' })}
                      className="rounded-lg border border-red-300 px-3 py-1.5 text-xs text-red-700"
                    >
                      删除工作副本
                    </button>
                  </div>
                </div>
                <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600">
                  <span>审阅范围 {project.reviewed_scope_segments}/{project.review_scope_segments || project.total_segments}（{project.progress_percent}%）</span>
                  <span>问题 {project.qa_items_open}/{project.qa_items_total}</span>
                  <span>{project.export_completed ? '已生成目标文件' : '尚未导出目标文件'}</span>
                  {project.pending_rewrites > 0 && (
                    <span className="font-medium text-amber-700">
                      {project.rewrites_needing_instruction > 0
                        ? `待补充重译要求 ${project.rewrites_needing_instruction} 段`
                        : `待执行模型重译 ${project.pending_rewrites} 段`}
                    </span>
                  )}
                  <span>最近更新 {formatTime(project.updated_at)}</span>
                  {project.workspace_job_id ? (
                    <span>可回到书籍工作台继续章节确认</span>
                  ) : (
                    <span>从 OK 目录同步：可审阅、重译、导出；暂不进入工作台式章节确认</span>
                  )}
                  {project.latest_version && <span>最新输出 {project.latest_version}</span>}
                </div>
              </div>
            ))}
            {!filteredProjects.length && (
              <div className="px-4 py-8 text-center text-sm text-slate-500">暂无匹配项目。</div>
            )}
          </div>
        </div>
      )}

      {pendingRemoval && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 px-4">
          <div className="w-full max-w-md rounded-xl bg-white p-5 shadow-xl">
            <h2 className="text-lg font-semibold text-slate-900">
              {pendingRemoval.mode === 'delete' ? '删除工作副本？' : '从列表移除？'}
            </h2>
            <p className="mt-2 text-sm text-slate-600">{pendingRemoval.project.title}</p>
            <p className="mt-3 text-sm text-slate-700">
              {pendingRemoval.mode === 'delete'
                ? '将删除桌面 Bookmate/Jobs 中的审阅数据。原始书籍和 Translated 中的最终导出不会删除。'
                : '只从审阅控制台隐藏，磁盘上的审阅数据、原始书籍和导出文件都不会删除。'}
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button
                onClick={() => setPendingRemoval(null)}
                disabled={removing}
                className="rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-700"
              >
                取消
              </button>
              <button
                onClick={removeProject}
                disabled={removing}
                className={`rounded-lg px-3 py-2 text-sm font-medium text-white disabled:opacity-50 ${
                  pendingRemoval.mode === 'delete' ? 'bg-red-600' : 'bg-slate-700'
                }`}
              >
                {removing ? '处理中...' : pendingRemoval.mode === 'delete' ? '确认删除' : '确认移除'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default ReviewCenter
