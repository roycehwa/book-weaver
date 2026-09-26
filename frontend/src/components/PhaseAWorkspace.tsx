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
    <main className="min-h-screen">
      <header className="sticky top-0 z-30 border-b border-[#3a2a22] bg-[#241c16] text-[#f6efe4] shadow-sheet">
        <div className="mx-auto flex max-w-[1680px] flex-wrap items-center gap-4 px-4 py-3 lg:px-6">
          <div className="flex items-center gap-3">
            <div
              aria-hidden="true"
              className="flex h-11 w-11 items-center justify-center rounded-sm border border-[#e7c7b2]/50 bg-[#8c3b2a] font-serif text-lg text-[#fbf7f0] shadow-[inset_0_0_0_2px_rgba(251,247,240,0.25)]"
            >
              织
            </div>
            <div>
              <div className="text-[10px] font-semibold uppercase tracking-[0.28em] text-[#e7c7b2]">
                Phase A
              </div>
              <div className="bw-serif text-base text-[#fbf7f0]">书籍整理与翻译审阅</div>
            </div>
          </div>

          <ol className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto py-1">
            {stages.map((stage, index) => {
              const active =
                (panel === 'workbench' && index < 4) ||
                (panel === 'review' && index >= 4)
              return (
                <li key={stage} className="flex items-center gap-1">
                  {index > 0 && <span aria-hidden="true" className="h-px w-3 bg-[#6f6258]" />}
                  <span
                    className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-1 text-xs ${
                      active
                        ? 'bg-[#fbf7f0] font-medium text-[#6f2d20]'
                        : 'text-[#d9cbb8]'
                    }`}
                  >
                    <span
                      className={`inline-flex h-4 w-4 items-center justify-center rounded-full text-[10px] ${
                        active ? 'bg-[#8c3b2a] text-[#fbf7f0]' : 'bg-[#3b2a22] text-[#e7dccb]'
                      }`}
                    >
                      {index + 1}
                    </span>
                    {stage}
                  </span>
                </li>
              )
            })}
          </ol>

          <nav className="flex rounded-full bg-[#3b2a22] p-1 text-sm">
            {resolvedJobId ? (
              <Link
                to={`/jobs/${resolvedJobId}`}
                className={`rounded-full px-3 py-1.5 ${
                  panel === 'workbench'
                    ? 'bg-[#fbf7f0] font-medium text-[#241c16]'
                    : 'text-[#e7dccb] hover:text-white'
                }`}
              >
                准备与结构
              </Link>
            ) : (
              <span className="cursor-not-allowed rounded-full px-3 py-1.5 text-[#8a7768]">
                准备与结构
              </span>
            )}
            {runDir ? (
              <Link
                to={reviewHref}
                className={`rounded-full px-3 py-1.5 ${
                  panel === 'review'
                    ? 'bg-[#fbf7f0] font-medium text-[#241c16]'
                    : 'text-[#e7dccb] hover:text-white'
                }`}
              >
                译文审阅
              </Link>
            ) : (
              <span className="cursor-not-allowed rounded-full px-3 py-1.5 text-[#8a7768]">
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
