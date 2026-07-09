// @vitest-environment jsdom

import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, test, vi } from 'vitest'
import EpubViewer from './EpubViewer'

describe('EpubViewer', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  test('renders complete EPUB page documents inside an iframe srcDoc', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      if (url.endsWith('/api/jobs/job-1/epub/pages')) {
        return new Response(JSON.stringify({
          total: 1,
          pages: [
            {
              index: 1,
              page_number: 0,
              page_label: '',
              chapter_title: '1. Our Prejudices',
              chapter_href: 'ops/xhtml/ch01.xhtml',
              page_anchor: '',
              page_url: '/api/jobs/job-1/epub/page-render?chapter=ops%2Fxhtml%2Fch01.xhtml&anchor=',
            },
          ],
        }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      if (url.includes('/api/jobs/job-1/epub/page-render')) {
        return new Response(
          '<!DOCTYPE html><html><head><title>1. Our Prejudices</title></head><body><h1>1. Our Prejudices</h1><p>Body text.</p></body></html>',
          { status: 200, headers: { 'Content-Type': 'text/html' } },
        )
      }
      return new Response('not found', { status: 404 })
    })

    render(<EpubViewer url="/api/jobs/job-1/source" />)

    const frame = await screen.findByTitle('EPUB 页面预览')
    await waitFor(() => {
      expect(frame).toHaveAttribute('srcdoc', expect.stringContaining('<html>'))
      expect(frame).toHaveAttribute('srcdoc', expect.stringContaining('1. Our Prejudices'))
    })
  })
})
