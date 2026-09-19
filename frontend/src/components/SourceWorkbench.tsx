import { useEffect, useRef, useState } from 'react'
import { jobsApi, type SourceBlock, type SourceWorkspace } from '../api'

const labels: Record<string, string> = { hyphenated_line_break: '疑似跨行断词', possible_continuation: '疑似接续下一段', possible_heading_error: '疑似标题误识别' }

export default function SourceWorkbench({ jobId, page, onSaved, onSelectPage }: { jobId: string; page: number; onSaved: () => void; onSelectPage?: (page: number) => void }) {
  const [data, setData] = useState<SourceWorkspace | null>(null)
  const [blocks, setBlocks] = useState<SourceBlock[]>([])
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const selection = useRef<HTMLTextAreaElement | null>(null)
  const requestSequence = useRef(0)
  const [selected, setSelected] = useState(0)
  const load = async () => {
    const sequence = ++requestSequence.current
    setBusy(true); setError('')
    try { const result = await jobsApi.sourceWorkspace(jobId, page); if (sequence !== requestSequence.current) return; setData(result); setBlocks(result.blocks); setDirty(false); setSelected(0) }
    catch (e) { if (sequence === requestSequence.current) { setData(null); setError(e instanceof Error ? e.message : '读取原文失败') } }
    finally { if (sequence === requestSequence.current) setBusy(false) }
  }
  useEffect(() => { if (!dirty) void load() }, [jobId, page]) // Unsaved edits stay pinned to their original page.
  const update = (next: SourceBlock[]) => { setBlocks(next); setDirty(true); setMessage('') }
  const change = (patch: Partial<SourceBlock>) => update(blocks.map((b, i) => i === selected ? { ...b, ...patch } : b))
  const save = async (undo = false) => {
    if (!data) return
    setBusy(true); setError(''); setMessage('')
    try {
      const result = await jobsApi.saveSourceWorkspace(jobId, { page: data.page, blocks, expected_revision: data.revision, request_id: crypto.randomUUID(), undo })
      setData(result); setBlocks(result.blocks); setDirty(false); setSelected(0)
      setMessage('已保存。请重新确认原文与章节；旧译文不会被覆盖。'); onSaved()
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
  return <section className="mt-4 rounded-lg border border-slate-300 bg-white p-3" aria-label="原文修正台">
    <h3 className="font-semibold">原文修正 · 第 {data?.page ?? page} 页</h3>
    <p className="my-2 text-xs text-slate-500">核对右侧原页后修正。这里只调整原文，不会替你确认章节。排除、保留原文或接受疑点时请说明理由。</p>
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
      {data.issues.filter(i => i.block_id === current.id).map((issue, i) => <p key={i} className="text-xs text-amber-700">{labels[issue.code] || issue.code} · {issue.status === 'accepted' ? '已注明接受理由' : '待核对'}</p>)}
      <textarea aria-label="原文段落内容" ref={selection} rows={9} className="w-full rounded border p-2 text-sm" value={current.text} onChange={e => change({ text: e.target.value })} />
      <div className="my-2 flex flex-wrap gap-2 text-xs">
        <button type="button" onClick={split}>在光标处分段</button><button type="button" onClick={merge} disabled={selected >= blocks.length - 1}>合并下一段</button>
        <button type="button" onClick={() => move(-1)} disabled={selected === 0}>上移</button><button type="button" onClick={() => move(1)} disabled={selected === blocks.length - 1}>下移</button>
        <button type="button" onClick={() => change({ text: /^#{1,6} /.test(current.text) ? current.text.replace(/^#{1,6} /, '') : `## ${current.text}` })}>标题／正文</button>
      </div>
      <label className="block text-sm">处理方式<select aria-label="原文处理方式" className="ml-2 rounded border p-1" value={current.policy} onChange={e => change({ policy: e.target.value as SourceBlock['policy'] })}>
        <option value="translate">翻译</option><option value="preserve">保留原文</option><option value="exclude">排除非正文</option>
      </select></label>
      <input aria-label="修正或接受理由" className="my-2 w-full rounded border p-2 text-sm" placeholder="修正／接受理由（保留或排除必填）" value={current.reason} onChange={e => change({ reason: e.target.value })} />
    </fieldset>}
    <div className="mt-2 flex flex-wrap gap-2 text-sm">
      <button disabled={!dirty || busy} onClick={() => void save()} className="rounded bg-blue-600 px-3 py-2 text-white disabled:opacity-40">保存原文修正</button>
      <button disabled={busy} onClick={() => { if (!dirty || window.confirm('放弃未保存修改并读取当前页？')) void load() }} className="rounded border px-3 py-2">读取当前页／放弃修改</button>
      <button disabled={!data?.can_undo || dirty || busy} onClick={() => void save(true)} className="rounded border px-3 py-2">撤销最近一次保存</button>
    </div>
  </section>
}
