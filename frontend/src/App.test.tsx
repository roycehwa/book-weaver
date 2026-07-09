// @vitest-environment jsdom

import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import App from './App'

vi.mock('./components/Library', () => ({ default: () => <div>Library</div> }))
vi.mock('./components/Reader', () => ({ default: () => <div>Reader</div> }))
vi.mock('./components/Review', () => ({ default: () => <div>Review</div> }))
vi.mock('./components/ReviewCenter', () => ({ default: () => <div>ReviewCenter</div> }))
vi.mock('./components/Upload', () => ({ default: () => <div>UploadRoot</div> }))
vi.mock('./components/Jobs', () => ({ default: () => <div>JobsRedirect</div> }))
vi.mock('./components/JobDetail', () => ({ default: () => <div>JobDetail</div> }))

describe('App routes', () => {
  it('redirects the root page to the Phase A upload workspace', async () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    )

    expect(await screen.findByText('UploadRoot')).toBeInTheDocument()
  })
})
