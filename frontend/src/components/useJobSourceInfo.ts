import { useEffect, useState } from 'react'

export type JobSourceKind = 'pdf' | 'epub' | 'other'

type SourceInfoLoader = (jobId: string) => Promise<{ kind: JobSourceKind }>

export function useJobSourceInfo(
  jobId: string | null,
  loadSourceInfo: SourceInfoLoader,
): { kind: JobSourceKind | null; loaded: boolean } {
  const [kind, setKind] = useState<JobSourceKind | null>(null)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    if (!jobId) {
      setKind(null)
      setLoaded(false)
      return
    }

    let cancelled = false
    setLoaded(false)
    loadSourceInfo(jobId)
      .then((info) => {
        if (cancelled) return
        setKind(info.kind)
        setLoaded(true)
      })
      .catch(() => {
        if (cancelled) return
        setKind('other')
        setLoaded(true)
      })

    return () => {
      cancelled = true
    }
  }, [jobId, loadSourceInfo])

  return { kind, loaded }
}
