// @vitest-environment jsdom

import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import PhaseAWorkspace from './PhaseAWorkspace'

describe('PhaseAWorkspace', () => {
  it('presents preparation and review as one Phase A workflow', () => {
    render(
      <MemoryRouter initialEntries={['/review?runDir=/tmp/run&jobId=job-1']}>
        <PhaseAWorkspace panel="review">
          <div>审阅正文</div>
        </PhaseAWorkspace>
      </MemoryRouter>,
    )

    expect(screen.getByText('Phase A')).toBeInTheDocument()
    expect(screen.getByText('解析')).toBeInTheDocument()
    expect(screen.getByText('结构')).toBeInTheDocument()
    expect(screen.getByText('术语')).toBeInTheDocument()
    expect(screen.getByText('翻译')).toBeInTheDocument()
    expect(screen.getByText('审阅')).toBeInTheDocument()
    expect(screen.getByText('导出')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '准备与结构' })).toHaveAttribute(
      'href',
      '/jobs/job-1',
    )
    expect(screen.getByText('审阅正文')).toBeInTheDocument()
  })
})
