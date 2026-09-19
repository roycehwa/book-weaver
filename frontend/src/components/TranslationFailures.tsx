import { useEffect, useState } from 'react'

type Failure = { chapter_title: string; source: string; error: string; source_pages: number[]; resolution?: { kind: string; text: string; reason?: string } }
type Ledger = { revision: number; items: Record<string, Failure> }

export default function TranslationFailures({ jobId, stopped }: { jobId: string; stopped: boolean }) {
  const [ledger, setLedger] = useState<Ledger>({ revision: 0, items: {} })
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [reasons, setReasons] = useState<Record<string, string>>({})
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    let active = true
    const load = () => fetch(`/api/jobs/${jobId}/translation-failures`).then(r => {
      if (!r.ok) throw new Error('暂时无法读取失败清单')
      return r.json()
    }).then(data => { if (active) { setLedger(data); setError('') } }).catch(e => { if (active) setError(String(e)) })
    void load()
    const timer = !stopped ? window.setInterval(load, 5000) : undefined
    return () => { active = false; if (timer) window.clearInterval(timer) }
  }, [jobId, stopped, refresh])
  async function save(key: string, preserve = false) {
    setSaving(true); setError('')
    try {
      const r = await fetch(`/api/jobs/${jobId}/translation-failures`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key, revision: ledger.revision, text: drafts[key] || '',
          ...(preserve ? { kind: 'preserve_source', reason: reasons[key] } : {}) }),
      })
      const data = await r.json()
      if (!r.ok) throw new Error(data.detail || '保存失败')
      setLedger(data)
      setDrafts(previous => { const next = { ...previous }; delete next[key]; return next })
    } catch (e) { setError(String(e)) } finally { setSaving(false) }
  }
  if (!Object.keys(ledger.items).length) return error ? <p role="alert">{error}</p> : null
  return <section className="my-4 w-full min-w-0 rounded-lg border border-amber-300 bg-amber-50 p-4">
    <h3 className="font-semibold">翻译待处理片段</h3>
    <button className="my-2 rounded border bg-white px-3 py-1 text-sm" onClick={() => setRefresh(refresh + 1)} disabled={saving}>刷新清单（保留未保存文字）</button>
    <p>{stopped ? '成功片段已保留。可填写人工译文，或使用下方恢复按钮重试失败片段。' : '后续内容仍在处理。请等待结束或先暂停任务，再处理以下片段。'}全部处理完成前不能导出。</p>
    {error && <p role="alert">{error}</p>}
    {Object.entries(ledger.items).map(([key, item]) => <details key={key} className="my-3">
      <summary>{item.chapter_title} · {item.source_pages.length ? `页 ${item.source_pages.join(', ')}` : '脚注内容'} · {item.resolution?.kind === 'preserve_source' ? '已明确保留原文' : item.resolution ? '已保存人工译文' : '待处理'}</summary>
      {item.resolution?.reason && <p>处理理由：{item.resolution.reason}</p>}
      <p className="text-sm text-red-700">{item.error}</p>
      <pre className="my-2 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded bg-white p-3 text-sm">{item.source}</pre>
      <textarea aria-label="人工译文" className="w-full border p-2" rows={6} disabled={!stopped || saving}
        value={drafts[key] ?? item.resolution?.text ?? ''} onChange={e => setDrafts({ ...drafts, [key]: e.target.value })} />
      <button className="rounded bg-blue-600 px-3 py-2 text-white disabled:opacity-50" disabled={!stopped || saving || !drafts[key]?.trim()} onClick={() => save(key)}>保存人工译文</button>
      <input aria-label="保留原文的理由" placeholder="若决定保留原文，请填写理由" className="ml-3 border p-2" disabled={!stopped || saving}
        value={reasons[key] || ''} onChange={e => setReasons({ ...reasons, [key]: e.target.value })} />
      <button disabled={!stopped || saving || !reasons[key]?.trim()} onClick={() => save(key, true)}>明确保留原文（非翻译成功）</button>
    </details>)}
  </section>
}
