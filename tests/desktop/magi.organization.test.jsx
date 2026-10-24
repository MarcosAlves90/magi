import { afterEach, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import plugin from '../../desktop/plugin.js'

afterEach(cleanup)

function renderMagi(analysisResult) {
  const state = { calls: [] }
  const entries = [
    { index: 0, content: 'Prefers tea in the morning.' },
    { index: 1, content: 'Likes tea every morning.' }
  ]
  const ctx = {
    source: 'organization-test',
    registerMany(registrations) { ctx.registrations = registrations },
    async rest(path, options = {}) {
      state.calls.push({ path, options })
      if (path === '/records') return { records: [], count: 0, issues: [] }
      if (path === '/memory') return {
        targets: {
          memory: { entries, count: entries.length, used_chars: 50, char_limit: 500, usage_percent: 10 },
          user: { entries: [], count: 0, used_chars: 0, char_limit: 500, usage_percent: 0 }
        }
      }
      if (path === '/memory/memory/organization/analyze') return analysisResult
      throw new Error(`Unexpected REST call: ${path}`)
    }
  }
  plugin.register(ctx)
  const route = ctx.registrations.find(item => item.area === 'routes' && item.data.path === '/magi')
  render(route.render())
  return state
}

it('runs organization analysis only after an explicit action and displays source-backed suggestions', async () => {
  const state = renderMagi({
    success: true,
    target: 'memory',
    suggestions: [{
      kind: 'duplicate',
      entry_indexes: [1, 2],
      reason: 'Both entries describe the same preference.',
      evidence: [
        { entry_index: 1, quote: 'Prefers tea in the morning.' },
        { entry_index: 2, quote: 'Likes tea every morning.' }
      ],
      uncertainty: 'Check whether the time-of-day distinction matters.'
    }],
    sources: [
      { entry_index: 1, content: 'Prefers tea in the morning.' },
      { entry_index: 2, content: 'Likes tea every morning.' }
    ],
    discarded_suggestions: 0
  })

  fireEvent.click(screen.getByRole('button', { name: /Stored memory/ }))
  await screen.findByRole('heading', { name: 'Stored memory' })
  expect(state.calls.some(call => call.path.endsWith('/organization/analyze'))).toBe(false)
  fireEvent.click(await screen.findByRole('button', { name: 'Analyze organization' }))

  expect(await screen.findByText('Memory organization suggestions')).toBeTruthy()
  expect(screen.getByText('Both entries describe the same preference.')).toBeTruthy()
  expect(screen.getByText(/Check whether the time-of-day distinction matters/)).toBeTruthy()
  expect(screen.getByText(/Original entry 1: Prefers tea in the morning/)).toBeTruthy()
  await waitFor(() => expect(state.calls.filter(call => call.path === '/memory/memory/organization/analyze')).toHaveLength(1))
  expect(state.calls.find(call => call.path === '/memory/memory/organization/analyze').options.method).toBe('POST')
  expect(state.calls.filter(call => ['PUT', 'DELETE'].includes(call.options.method)).length).toBe(0)
})

it('shows a safe analysis error without issuing a mutation request', async () => {
  const state = renderMagi({ success: false, error: 'AI organization analysis failed; memory was not changed.' })

  fireEvent.click(screen.getByRole('button', { name: /Stored memory/ }))
  await screen.findByRole('heading', { name: 'Stored memory' })
  fireEvent.click(await screen.findByRole('button', { name: 'Analyze organization' }))

  expect(await screen.findByRole('alert')).toBeTruthy()
  expect(screen.getByRole('alert').textContent).toContain('memory was not changed')
  expect(state.calls.filter(call => ['PUT', 'DELETE'].includes(call.options.method)).length).toBe(0)
})
