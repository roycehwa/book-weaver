export type HumanReviewMode = 'issues_only' | 'full'

export interface ReviewExportReadinessInput {
  pendingRewriteCount: number
  rewritesNeedingInstruction: number
  humanReviewMode: HumanReviewMode
  isFullReviewComplete: boolean
  translationQualityBlocking: boolean
  policyCoverageBlocking?: boolean
  policyCoverageMessage?: string | null
}

export function reviewExportCompletionMessage(input: ReviewExportReadinessInput): string {
  if (input.pendingRewriteCount > 0) {
    if (input.rewritesNeedingInstruction > 0) {
      return `有 ${input.rewritesNeedingInstruction} 段尚未填写重译要求。当前已自动定位，请在下方填写后执行。`
    }
    return `有 ${input.pendingRewriteCount} 段已保存模型重译要求。请先生成并确认候选译文，再导出定稿。`
  }
  if (input.translationQualityBlocking) {
    return '审阅决定已保存，但翻译质量阻断项仍未解除；请处理剩余质量阻断后再导出定稿。'
  }
  if (input.policyCoverageBlocking) {
    return input.policyCoverageMessage || '章节翻译策略检查未通过，请先处理被跳过的章节。'
  }
  if (input.humanReviewMode === 'issues_only' && !input.isFullReviewComplete) {
    return '现在可以直接导出，也可以切换到全书逐段继续检查。'
  }
  return '现在可以导出定稿 EPUB/PDF，或结束本次审阅稍后继续。'
}

export function reviewExportReady(input: ReviewExportReadinessInput): boolean {
  return (
    !input.translationQualityBlocking
    && !input.policyCoverageBlocking
    && input.pendingRewriteCount === 0
    && input.rewritesNeedingInstruction === 0
  )
}
