import { useCallback, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { jobsApi, type CreateJobOptions, type DuplicateBookCheckResponse, type DuplicateBookMatch } from '../api'

const duplicateReasonLabels: Record<DuplicateBookMatch['reason'], string> = {
  same_file: '同一源文件',
  same_filename: '同名文件',
  same_title: '疑似同书名',
}

const duplicateKindLabels: Record<DuplicateBookMatch['kind'], string> = {
  workspace_job: '书籍工作台',
  review_project: '审阅控制台',
}

const duplicateStatusLabels: Record<string, string> = {
  processing: '处理中',
  needs_translation_review: '需要翻译审阅',
  needs_chapter_confirmation: '需要确认章节',
  ready_for_knowledge: '可进入知识解析',
  failed: '处理失败',
  unreviewed: '未审阅',
  in_review: '审阅中',
  reviewed: '审阅完成',
  exported: '已输出',
}

const Upload = () => {
  const navigate = useNavigate()
  const [dragActive, setDragActive] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState(0)
  const [file, setFile] = useState<File | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [checkingDuplicate, setCheckingDuplicate] = useState(false)
  const [duplicateCheck, setDuplicateCheck] = useState<DuplicateBookCheckResponse | null>(null)
  const [allowDuplicate, setAllowDuplicate] = useState(false)
  const [options, setOptions] = useState<CreateJobOptions>({
    processingMode: 'translate',
    targetLanguage: 'zh-CN',
    translator: 'minimax',
    outputFormat: 'epub',
  })
  const isPreserveMode = options.processingMode === 'preserve'
  const isTranslateMode = options.processingMode === 'translate'

  const acceptFile = async (candidate: File) => {
    if (!/\.(pdf|epub)$/i.test(candidate.name)) {
      setError('请选择 PDF 或 EPUB 文件。')
      return
    }
    setError(null)
    setFile(candidate)
    setDuplicateCheck(null)
    setAllowDuplicate(false)
    setCheckingDuplicate(true)
    try {
      const result = await jobsApi.checkDuplicates(candidate)
      setDuplicateCheck(result)
    } catch (checkError) {
      setError(checkError instanceof Error ? checkError.message : '重复检测失败，请稍后重试')
    } finally {
      setCheckingDuplicate(false)
    }
  }

  const updateProcessingMode = (processingMode: CreateJobOptions['processingMode']) => {
    setOptions((current) => ({
      ...current,
      processingMode,
      sourceLanguage: processingMode === 'preserve' ? undefined : current.sourceLanguage,
      translator:
        processingMode === 'preserve'
          ? 'mock'
          : current.translator === 'mock'
            ? 'minimax'
            : current.translator,
    }))
  }

  const handleDrag = useCallback((event: React.DragEvent) => {
    event.preventDefault()
    event.stopPropagation()
    setDragActive(event.type === 'dragenter' || event.type === 'dragover')
  }, [])

  const handleDrop = useCallback((event: React.DragEvent) => {
    event.preventDefault()
    event.stopPropagation()
    setDragActive(false)
    if (event.dataTransfer.files?.[0]) acceptFile(event.dataTransfer.files[0])
  }, [])

  const handleUpload = async () => {
    if (!file) return
    if (duplicateCheck?.has_matches && !allowDuplicate) {
      setError('这本书已经存在处理记录。请先打开现有项目，或勾选“仍然创建新版”后再开始处理。')
      return
    }
    setUploading(true)
    setError(null)
    try {
      const job = await jobsApi.create(
        file,
        isPreserveMode
          ? { ...options, sourceLanguage: undefined, translator: 'mock' }
          : options,
        setUploadProgress,
        allowDuplicate
      )
      navigate(`/jobs/${job.job_id}`)
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : '创建任务失败')
    } finally {
      setUploading(false)
      setUploadProgress(0)
    }
  }

  return (
    <div className="mx-auto max-w-3xl">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-slate-900">上传书籍并进入工作台</h1>
        <p className="mt-2 text-sm text-slate-600">
          先选择处理方式，再上传 PDF 或 EPUB。译本路径会走术语定稿、翻译、章节确认与人工审阅。
        </p>
      </div>

      <div className="mb-6 rounded-xl border border-slate-200 bg-white p-4">
        <div className="text-sm font-semibold text-slate-900">处理方式（必选）</div>
        <p className="mt-1 text-xs text-slate-500">
          这是上传前最重要的选项。选错会导致只解析原文、不生成中文译本。
        </p>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label
            className={`cursor-pointer rounded-xl border-2 p-4 transition-colors ${
              isTranslateMode
                ? 'border-primary-500 bg-primary-50'
                : 'border-slate-200 bg-slate-50 hover:border-slate-300'
            }`}
          >
            <input
              type="radio"
              name="processingMode"
              value="translate"
              checked={options.processingMode === 'translate'}
              onChange={() => updateProcessingMode('translate')}
              className="sr-only"
            />
            <div className="font-medium text-slate-900">翻译并进入审阅</div>
            <p className="mt-1 text-xs leading-5 text-slate-600">
              生成中文译本，使用 MiniMax 翻译，并进入术语定稿与审阅流程。<strong>推荐用于英文书。</strong>
            </p>
          </label>
          <label
            className={`cursor-pointer rounded-xl border-2 p-4 transition-colors ${
              isPreserveMode
                ? 'border-emerald-500 bg-emerald-50'
                : 'border-slate-200 bg-slate-50 hover:border-slate-300'
            }`}
          >
            <input
              type="radio"
              name="processingMode"
              value="preserve"
              checked={options.processingMode === 'preserve'}
              onChange={() => updateProcessingMode('preserve')}
              className="sr-only"
            />
            <div className="font-medium text-slate-900">只解析结构，保留原文</div>
            <p className="mt-1 text-xs leading-5 text-slate-600">
              不调用翻译，只提取章节与正文结构，适合已是中文或仅需结构分析的书。
            </p>
          </label>
        </div>
        <label className="mt-3 flex items-start gap-2 text-xs text-slate-600">
          <input
            type="radio"
            name="processingMode"
            value="auto"
            checked={options.processingMode === 'auto'}
            onChange={() => updateProcessingMode('auto')}
            className="mt-0.5"
          />
          <span>
            <span className="font-medium text-slate-800">自动判断</span>
            ：解析后根据源语言决定是否翻译（不确定时建议直接选上方「翻译并进入审阅」）。
          </span>
        </label>
        {isTranslateMode && (
          <div className="mt-3 rounded-lg border border-primary-200 bg-primary-50/80 px-3 py-2 text-xs text-primary-900">
            已选择译本路径：目标语言 {options.targetLanguage}，翻译引擎 MiniMax。
          </div>
        )}
        {isPreserveMode && (
          <div className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50/80 px-3 py-2 text-xs text-emerald-900">
            已选择原文路径：不会生成 zh-CN 译文，也不会消耗翻译额度。
          </div>
        )}
      </div>

      <div
        className={`relative rounded-xl border-2 border-dashed p-10 text-center transition-colors ${
          dragActive ? 'border-primary-500 bg-primary-50' : 'border-slate-300 bg-white'
        }`}
        onDragEnter={handleDrag}
        onDragLeave={handleDrag}
        onDragOver={handleDrag}
        onDrop={handleDrop}
      >
        <input
          type="file"
          accept=".pdf,.epub,application/pdf,application/epub+zip"
            onChange={(event) => {
              if (event.target.files?.[0]) void acceptFile(event.target.files[0])
            }}
          className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
        />
        <div className="text-lg font-medium text-slate-900">
          {file ? file.name : '拖拽文件到此处，或点击选择'}
        </div>
        <div className="mt-2 text-sm text-slate-500">
          {file ? `${(file.size / 1024 / 1024).toFixed(2)} MB` : '支持 PDF 和 EPUB，最大 50MB'}
        </div>
      </div>

      {checkingDuplicate && (
        <div className="mt-4 rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-800">
          正在检查这本书是否已经处理过...
        </div>
      )}

      {duplicateCheck?.has_matches && (
        <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-4">
          <div className="text-sm font-semibold text-amber-900">
            检测到这本书已有处理记录
          </div>
          <p className="mt-1 text-sm text-amber-800">
            默认建议继续现有项目，避免同一本书再次完整翻译。只有在需要换模型、换参数或保留一个新版本时，才创建新版。
          </p>
          <div className="mt-3 space-y-2">
            {duplicateCheck.matches.slice(0, 5).map((match) => (
              <div
                key={`${match.kind}-${match.id}`}
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-200 bg-white px-3 py-2 text-sm"
              >
                <div className="min-w-0">
                  <div className="truncate font-medium text-slate-900">{match.title}</div>
                  <div className="text-xs text-slate-500">
                    {duplicateKindLabels[match.kind]} · {duplicateReasonLabels[match.reason]} · {duplicateStatusLabels[match.status] || match.status}
                  </div>
                </div>
                <button
                  onClick={() => navigate(match.href)}
                  className="rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-medium text-white"
                >
                  打开现有项目
                </button>
              </div>
            ))}
          </div>
          <label className="mt-3 flex items-start gap-2 text-sm text-amber-900">
            <input
              type="checkbox"
              checked={allowDuplicate}
              onChange={(event) => {
                setAllowDuplicate(event.target.checked)
                if (event.target.checked) setError(null)
              }}
              className="mt-1"
            />
            <span>我确认要为同一本书创建一个新的处理版本，并可能再次产生翻译任务。</span>
          </label>
        </div>
      )}

      <div className="mt-6 grid gap-4 rounded-xl border border-slate-200 bg-white p-5 sm:grid-cols-2">
        {!isPreserveMode && (
          <>
            <label className="text-sm font-medium text-slate-700">
              源语言（可选）
              <input
                value={options.sourceLanguage || ''}
                onChange={(event) => setOptions({ ...options, sourceLanguage: event.target.value })}
                placeholder="例如 en、zh-CN；留空则自动判断"
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>
            <label className="text-sm font-medium text-slate-700">
              目标语言
              <input
                value={options.targetLanguage}
                onChange={(event) => setOptions({ ...options, targetLanguage: event.target.value })}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>
          </>
        )}
        <label className="text-sm font-medium text-slate-700">
          输出格式
          <select
            value={options.outputFormat}
            onChange={(event) =>
              setOptions({ ...options, outputFormat: event.target.value as CreateJobOptions['outputFormat'] })
            }
            className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
          >
            <option value="epub">EPUB</option>
            <option value="pdf">PDF</option>
            <option value="both">EPUB + PDF</option>
          </select>
        </label>
        {!isPreserveMode && (
          <label className="text-sm font-medium text-slate-700">
            翻译服务
            <select
              value={options.translator}
              onChange={(event) =>
                setOptions({ ...options, translator: event.target.value as CreateJobOptions['translator'] })
              }
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
            >
              <option value="minimax">MiniMax</option>
              <option value="openai">OpenAI</option>
              <option value="compatible">兼容接口</option>
              <option value="mock">测试模式</option>
            </select>
          </label>
        )}
      </div>

      {error && (
        <div className="mt-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {uploading && (
        <div className="mt-4">
          <div className="mb-2 flex justify-between text-sm text-slate-600">
            <span>正在上传并创建任务</span>
            <span>{uploadProgress}%</span>
          </div>
          <div className="h-2 rounded-full bg-slate-200">
            <div className="h-2 rounded-full bg-primary-600" style={{ width: `${uploadProgress}%` }} />
          </div>
        </div>
      )}

      <div className="mt-6 flex gap-3">
        <button
          onClick={handleUpload}
          disabled={!file || uploading || checkingDuplicate}
          className="rounded-lg bg-primary-600 px-6 py-3 font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
        >
          {uploading
            ? '创建中...'
            : duplicateCheck?.has_matches && !allowDuplicate
              ? '先处理已有项目'
              : isTranslateMode
                ? '上传并创建译本任务'
                : isPreserveMode
                  ? '上传并解析原文'
                  : '上传并解析'}
        </button>
        {file && (
          <button
            onClick={() => {
              setFile(null)
              setDuplicateCheck(null)
              setAllowDuplicate(false)
              setError(null)
            }}
            disabled={uploading}
            className="rounded-lg border border-slate-300 bg-white px-6 py-3 font-medium text-slate-700"
          >
            清除
          </button>
        )}
      </div>
    </div>
  )
}

export default Upload
