import { useEffect, useRef, useState } from 'react'
import { jobsApi, type JobChapterDraft, type SourceBlock, type SourceWorkspace } from '../api'

const labels: Record<string, string> = {
  hyphenated_line_break: '疑似跨行断词',
  possible_continuation: '段落排版待核对',
  possible_heading_error: '疑似标题误识别',
}
const issueStatusLabels: Record<string, string> = { open: '待核对', accepted: '已注明接受理由', reconciled: '已由续接决策核对' }

interface SourceWorkbenchProps {
  jobId: string
  page: number
  onSaved: () => void
  onSelectPage?: (page: number) => void
  onDirtyChange?: (dirty: boolean) => void
  chapterTitle?: string
  chapterPolicy?: JobChapterDraft['content_policy']
  targetBlockIndex?: number
}

const chapterPolicyLabel = (policy: JobChapterDraft['content_policy'] | undefined): string | null => {
  if (policy === 'preserve') return '保留原文（不翻译）'
  if (policy === 'exclude') return '略过（不翻译、不导出）'
  if (policy === 'translate') return '翻译'
  return null
}

const effectiveChapterPolicy = (
  policy: JobChapterDraft['content_policy'] | undefined,
): SourceBlock['policy'] => (
  policy === 'preserve' || policy === 'exclude' || policy === 'translate' ? policy : 'translate'
)

const sourceBlocksEqual = (left: SourceBlock[], right: SourceBlock[]): boolean => (
  left.length === right.length && left.every((block, index) => {
    const other = right[index]
    return other !== undefined
      && block.id === other.id
      && block.text === other.text
      && block.policy === other.policy
      && block.reason === other.reason
  })
)

export default function SourceWorkbench({
  jobId,
  page,
  onSaved,
  onSelectPage,
  onDirtyChange,
  chapterTitle,
  chapterPolicy,
  targetBlockIndex,
}: SourceWorkbenchProps) {
  const [data, setData] = useState<SourceWorkspace | null>(null)
  const [blocks, setBlocks] = useState<SourceBlock[]>([])
  const [baselineBlocks, setBaselineBlocks] = useState<SourceBlock[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const selection = useRef<HTMLTextAreaElement | null>(null)
  const requestSequence = useRef(0)
  const [selected, setSelected] = useState(0)
  const [explicitOverrideIds, setExplicitOverrideIds] = useState<Set<string>>(new Set())
  const dirty = explicitOverrideIds.size > 0 || !sourceBlocksEqual(blocks, baselineBlocks)
  const load = async () => {
    const sequence = ++requestSequence.current
    setBusy(true); setError('')
    try { const result = await jobsApi.sourceWorkspace(jobId, page); if (sequence !== requestSequence.current) return; setData(result); setBlocks(result.blocks); setBaselineBlocks(result.blocks); setSelected(0); setExplicitOverrideIds(new Set()) }
    catch (e) { if (sequence === requestSequence.current) { setData(null); setError(e instanceof Error ? e.message : '读取原文失败') } }
    finally { if (sequence === requestSequence.current) setBusy(false) }
  }
  useEffect(() => { if (!dirty) void load() }, [jobId, page]) // Unsaved edits stay pinned to their original page.
  useEffect(() => {
    if (!data || data.page !== page || !targetBlockIndex) return
    setSelected(Math.min(Math.max(targetBlockIndex - 1, 0), Math.max(blocks.length - 1, 0)))
  }, [blocks.length, data, page, targetBlockIndex])
  useEffect(() => { onDirtyChange?.(dirty) }, [dirty, onDirtyChange])
  const update = (next: SourceBlock[]) => {
    setBlocks(next)
    setError('')
    setMessage('')
  }
  const change = (patch: Partial<SourceBlock>) => update(blocks.map((b, i) => i === selected ? { ...b, ...patch } : b))
  const save = async (undo = false) => {
    if (!data) return
    if (!undo) {
      const missingReasonIndex = blocks.findIndex(block => (
        explicitOverrideIds.has(block.id) && !block.reason.trim()
      ))
      if (missingReasonIndex >= 0) {
        setSelected(missingReasonIndex)
        setError(`第 ${missingReasonIndex + 1} 段已设为例外，请填写这一段的理由。`)
        return
      }
    }
    setBusy(true); setError(''); setMessage('')
    try {
      const result = await jobsApi.saveSourceWorkspace(jobId, { page: data.page, blocks, expected_revision: data.revision, request_id: crypto.randomUUID(), undo })
      setData(result); setBlocks(result.blocks); setBaselineBlocks(result.blocks); setSelected(0); setExplicitOverrideIds(new Set())
      setMessage('已保存本页全部修改。请重新确认原文与章节；旧译文不会被覆盖。'); onSaved()
    } catch (e) { setError(e instanceof Error ? e.message : '保存失败，编辑内容已保留') }
    finally { setBusy(false) }
  }
  const split = () => {
    const block = blocks[selected], offset = selection.current?.selectionStart ?? 0
    if (!block || offset <= 0 || offset >= block.text.length) { setError('请把光标放到需要拆分的位置。'); return }
    const first = block.text.slice(0, offset).trim(), second = block.text.slice(offset).trim()
    if (!first || !second) return
    update([...blocks.slice(0, selected), { ...block, text: first }, { ...block, id: crypto.randomUUID(), text: second }, ...blocks.slice(selected + 1)])
  }
  const merge = () => {
    const a = blocks[selected], b = blocks[selected + 1]
    if (!b) return
    if (a.policy !== b.policy) { setError('不同处理方式的段落不能直接合并。'); return }
    update([...blocks.slice(0, selected), { ...a, text: `${a.text.trim()} ${b.text.trim()}`, reason: [a.reason, b.reason].filter(Boolean).join('；') }, ...blocks.slice(selected + 2)])
  }
  const move = (delta: number) => {
    const target = selected + delta
    if (target < 0 || target >= blocks.length) return
    const next = [...blocks]; [next[selected], next[target]] = [next[target], next[selected]]; update(next); setSelected(target)
  }
  const current = blocks[selected]
  const inheritedPolicy = effectiveChapterPolicy(chapterPolicy)
  const inheritedPolicyLabel = chapterPolicyLabel(inheritedPolicy) || '翻译'
  const storedOverride = Boolean(
    current?.reason.trim() && current?.policy !== inheritedPolicy,
  )
  const overrideActive = Boolean(
    current && (explicitOverrideIds.has(current.id) || storedOverride),
  )
  const exceptionPolicies = (['translate', 'preserve', 'exclude'] as const).filter(
    policy => policy !== inheritedPolicy,
  )
  const startOverride = () => {
    if (!current || chapterPolicy === 'exclude') return
    setExplicitOverrideIds(ids => new Set(ids).add(current.id))
    change({ policy: exceptionPolicies[0], reason: '' })
  }
  const cancelOverride = () => {
    if (!current) return
    const wasNewOverride = explicitOverrideIds.has(current.id)
    const baseline = baselineBlocks.find(block => block.id === current.id)
    setExplicitOverrideIds(ids => {
      const next = new Set(ids)
      next.delete(current.id)
      return next
    })
    if (wasNewOverride && baseline) {
      change({ policy: baseline.policy, reason: baseline.reason })
      return
    }
    // Existing exceptions are removed with the API's neutral sentinel. The
    // confirmed chapter policy then remains in force.
    change({ policy: 'translate', reason: '' })
  }
  useEffect(() => {
    if (
      (error.includes('必须填写理由') || error.includes('请填写这一段的理由'))
      && overrideActive
      && current?.reason.trim()
    ) {
      setError('')
    }
  }, [current?.reason, error, overrideActive])
  const activeChapterPolicyLabel = chapterPolicyLabel(chapterPolicy)
  return <section className="mt-4 rounded-lg border border-slate-300 bg-white p-3" aria-label="原文修正台">
    <h3 className="font-semibold">高级原文修正 · 第 {data?.page ?? page} 页</h3>
    <p className="my-2 text-xs text-slate-500">
      这里用于个别段落的文字、顺序和去留修正。跨页是否续接请以上方边界记录为准；整章是否翻译由章节列表决定。
    </p>
    <p className="my-2 rounded-md bg-slate-50 px-3 py-2 text-xs leading-5 text-slate-700">
      操作方式：可在本页切换不同段落，完成所有文字修正和少量段落例外后，一次保存本页全部修改。不需要每改一段就保存。
    </p>
    {activeChapterPolicyLabel && (
      <div className="my-2 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs leading-5 text-emerald-900">
        当前章节「{chapterTitle || '未命名章节'}」已设为「{activeChapterPolicyLabel}」。
        {chapterPolicy === 'preserve' || chapterPolicy === 'exclude'
          ? '无需在这里逐段重复设置；直接确认源书章节目录即可应用整章策略。'
          : '如果只有个别段落例外，可在下方单独设置。'}
      </div>
    )}
    {!!data?.issue_groups?.length && <label className="block text-xs">按问题跳转<select aria-label="待核对问题页" className="my-2 w-full rounded border p-2" value="" disabled={dirty || busy} onChange={e => onSelectPage?.(Number(e.target.value))}>
      <option value="">选择待核对页（同类问题已归组）</option>
      {data.issue_groups.map(group => <optgroup key={group.code} label={`${labels[group.code] || group.code} · ${group.count} 处`}>
        {group.pages.map(p => <option key={p} value={p}>第 {p} 页</option>)}
      </optgroup>)}
    </select></label>}
    {dirty && data?.page !== page && <p className="text-amber-700">还有未保存修改，编辑区仍停在第 {data?.page} 页。保存或放弃后再读取当前页。</p>}
    {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    {message && <p role="status" className="text-sm text-green-700">{message}</p>}
    {data && current && <fieldset disabled={busy}>
      <label className="block text-sm">段落<select aria-label="选择原文段落" className="my-2 w-full rounded border p-2" value={selected} onChange={e => setSelected(Number(e.target.value))}>
        {blocks.map((block, i) => <option key={block.id} value={i}>{i + 1}. {block.text.slice(0, 65)}</option>)}
      </select></label>
      {data.issues.filter(i => i.block_id === current.id).map((issue, i) => <p key={i} className="text-xs text-amber-700">{labels[issue.code] || issue.code} · {issueStatusLabels[issue.status] || issue.status}</p>)}
      <textarea aria-label="原文段落内容" ref={selection} rows={9} className="w-full rounded border p-2 text-sm" value={current.text} onChange={e => change({ text: e.target.value })} />
      <div className="my-2 flex flex-wrap gap-2 text-xs">
        <button type="button" onClick={split}>在光标处分段</button><button type="button" onClick={merge} disabled={selected >= blocks.length - 1}>合并下一段</button>
        <button type="button" onClick={() => move(-1)} disabled={selected === 0}>上移</button><button type="button" onClick={() => move(1)} disabled={selected === blocks.length - 1}>下移</button>
        <button type="button" onClick={() => change({ text: /^#{1,6} /.test(current.text) ? current.text.replace(/^#{1,6} /, '') : `## ${current.text}` })}>标题／正文</button>
      </div>
      <div className="mt-3 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span>内容去留：<span className="font-medium">{overrideActive ? chapterPolicyLabel(current.policy) : inheritedPolicyLabel}</span></span>
          <span className="text-xs text-slate-500">{overrideActive ? '本段例外' : `继承章节「${chapterTitle || '未命名章节'}」`}</span>
        </div>
        {!overrideActive && chapterPolicy !== 'exclude' && (
          <button type="button" onClick={startOverride} className="mt-2 rounded border border-slate-300 bg-white px-2 py-1.5 text-xs text-slate-700">为本段设置例外</button>
        )}
        {!overrideActive && chapterPolicy === 'exclude' && (
          <p className="mt-2 text-xs text-slate-600">整章已略过，段落例外不会生效。如需保留个别内容，请先在章节列表中改为「保留原文」或「翻译」。</p>
        )}
        {overrideActive && (
          <div className="mt-2 space-y-2">
            <label className="block text-sm">
              本段改为
              <select aria-label="原文处理方式" className="ml-2 rounded border p-1" value={current.policy} onChange={e => change({ policy: e.target.value as SourceBlock['policy'] })}>
                {exceptionPolicies.map(policy => <option key={policy} value={policy}>{chapterPolicyLabel(policy)}</option>)}
              </select>
            </label>
            <label className="block text-sm">
              例外理由（必填）
              <input aria-label="修正或接受理由" className="mt-1 w-full rounded border p-2 text-sm" placeholder="说明为什么这一段不按整章策略处理" value={current.reason} onChange={e => change({ reason: e.target.value })} />
            </label>
            <button type="button" onClick={cancelOverride} className="rounded border border-slate-300 bg-white px-2 py-1.5 text-xs text-slate-700">取消本段例外，恢复继承章节</button>
          </div>
        )}
      </div>
    </fieldset>}
    {dirty && <p className="mt-2 text-xs font-medium text-amber-800">当前页有未保存的逐段修改。请先保存或放弃，再确认章节目录。</p>}
    <div className="sticky bottom-0 -mx-3 mt-2 flex flex-wrap gap-2 border-t border-slate-200 bg-white/95 px-3 py-3 text-sm shadow-[0_-4px_10px_rgba(15,23,42,0.06)] backdrop-blur">
      <button disabled={!dirty || busy} onClick={() => void save()} className="rounded bg-blue-600 px-3 py-2 text-white disabled:opacity-40">保存本页全部修改</button>
      <button disabled={busy} onClick={() => { if (!dirty || window.confirm('放弃未保存修改并读取当前页？')) void load() }} className="rounded border px-3 py-2">{dirty ? '放弃未保存修改' : '重新读取当前页'}</button>
      <button disabled={!data?.can_undo || dirty || busy} onClick={() => void save(true)} className="rounded border px-3 py-2">撤销最近一次保存</button>
    </div>
  </section>
}
