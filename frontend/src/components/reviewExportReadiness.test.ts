import { describe, expect, test } from 'vitest'
import { reviewExportCompletionMessage, reviewExportReady } from './reviewExportReadiness'

describe('reviewExportReadiness', () => {
  test('reports quality blockers after scope completion', () => {
    const message = reviewExportCompletionMessage({
      pendingRewriteCount: 0,
      rewritesNeedingInstruction: 0,
      humanReviewMode: 'issues_only',
      isFullReviewComplete: false,
      translationQualityBlocking: true,
    })
    expect(message).toContain('质量阻断')
    expect(reviewExportReady({
      pendingRewriteCount: 0,
      rewritesNeedingInstruction: 0,
      humanReviewMode: 'issues_only',
      isFullReviewComplete: false,
      translationQualityBlocking: true,
    })).toBe(false)
  })

  test('allows export messaging when quality is not blocking', () => {
    const message = reviewExportCompletionMessage({
      pendingRewriteCount: 0,
      rewritesNeedingInstruction: 0,
      humanReviewMode: 'full',
      isFullReviewComplete: true,
      translationQualityBlocking: false,
    })
    expect(message).toContain('现在可以导出定稿')
    expect(reviewExportReady({
      pendingRewriteCount: 0,
      rewritesNeedingInstruction: 0,
      humanReviewMode: 'full',
      isFullReviewComplete: true,
      translationQualityBlocking: false,
    })).toBe(true)
  })

  test('does not offer export when the backend finds a skipped body chapter', () => {
    const input = {
      pendingRewriteCount: 0,
      rewritesNeedingInstruction: 0,
      humanReviewMode: 'issues_only' as const,
      isFullReviewComplete: false,
      translationQualityBlocking: false,
      policyCoverageBlocking: true,
      policyCoverageMessage: '导出被阻止：以下正文章节被错误跳过翻译：Body',
    }
    expect(reviewExportCompletionMessage(input)).toContain('Body')
    expect(reviewExportReady(input)).toBe(false)
  })
})
