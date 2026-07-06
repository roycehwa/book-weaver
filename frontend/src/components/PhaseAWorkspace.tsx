import type { ReactNode } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'

type PhaseAPanel = 'workbench' | 'review'

const stages = ['解析', '结构', '术语', '翻译', '审阅', '导出']

function PhaseAWorkspace({
  panel,
  jobId,
  children,
}: {
  panel: PhaseAPanel
  jobId?: string
  children: ReactNode
}) {
  const params = useParams()
  const [searchParams] = useSearchParams()
  const resolvedJobId = jobId || params.id || searchParams.get('jobId') || ''
  const runDir = searchParams.get('runDir') || ''
  const reviewHref = runDir
    ? `/review?runDir=${encodeURIComponent(runDir)}${resolvedJobId ? `&jobId=${encodeURIComponent(resolvedJobId)}` : ''}`
    : '#'

  return (
    <main className="min-h-screen bg-slate-100">
      <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/95 backdrop-blur">
        <div className="mx-auto flex max-w-[1680px] flex-wrap items-center gap-4 px-4 py-3 lg:px-6">
          <div className="mr-2">
            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">
              Phase A
            </div>
            <div className="text-sm font-semibold text-slate-900">书籍整理与翻译审阅</div>
          </div>

          <ol className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto">
            {stages.map((stage, index) => {
              const active =
                (panel === 'workbench' && index < 4) ||
                (panel === 'review' && index >= 4)
              return (
                <li
                  key={stage}
                  className={`whitespace-nowrap rounded-full border px-3 py-1 text-xs font-medium ${
                    active
                      ? 'border-indigo-200 bg-indigo-50 text-indigo-800'
                      : 'border-slate-200 bg-slate-50 text-slate-500'
                  }`}
                >
                  {stage}
                </li>
              )
            })}
          </ol>

          <nav className="flex rounded-lg border border-slate-200 bg-slate-50 p-1 text-sm">
            {resolvedJobId ? (
              <Link
                to={`/jobs/${resolvedJobId}`}
                className={`rounded-md px-3 py-1.5 ${
                  panel === 'workbench'
                    ? 'bg-white font-medium text-slate-900 shadow-sm'
                    : 'text-slate-600 hover:text-slate-900'
                }`}
              >
                准备与结构
              </Link>
            ) : (
              <span className="cursor-not-allowed rounded-md px-3 py-1.5 text-slate-400">
                准备与结构
              </span>
            )}
            {runDir ? (
              <Link
                to={reviewHref}
                className={`rounded-md px-3 py-1.5 ${
                  panel === 'review'
                    ? 'bg-white font-medium text-slate-900 shadow-sm'
                    : 'text-slate-600 hover:text-slate-900'
                }`}
              >
                译文审阅
              </Link>
            ) : (
              <span className="cursor-not-allowed rounded-md px-3 py-1.5 text-slate-400">
                译文审阅
              </span>
            )}
          </nav>
        </div>
      </header>
      <div className="mx-auto max-w-[1680px]">{children}</div>
    </main>
  )
}

export default PhaseAWorkspace
