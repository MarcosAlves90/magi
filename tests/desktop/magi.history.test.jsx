import { afterEach, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import plugin from '../../desktop/plugin.js'

afterEach(cleanup)

it('filters local action history and requires confirmation before clearing it', async () => {
  const state = { calls: [] }
  const ctx = {
    source: 'history-test',
    registerMany(registrations) { ctx.registrations = registrations },
    async rest(path, options = {}) {
      state.calls.push({ path, options })
      if (path === '/records') return { records: [], count: 0, issues: [] }
      if (path === '/history' || path.startsWith('/history?')) return {
        records: [{ id: 'abc123', timestamp: '2026-10-23T12:00:00Z', operation: 'add', target: 'memory', result: 'success' }],
        retention: { max_records: 500 }
      }
      if (path === '/history' && options.method === 'DELETE') return { success: true }
      throw new Error(`Unexpected REST call: ${path}`)
    }
  }
  plugin.register(ctx)
  const route = ctx.registrations.find(item => item.area === 'routes' && item.data.path === '/magi')
  render(route.render())

  fireEvent.click(screen.getByRole('button', { name: /History/ }))
  expect(await screen.findByText('Magi action history')).toBeTruthy()
  expect(await screen.findByText('Added · MEMORY.md')).toBeTruthy()
  expect(screen.getByText(/not an undo log/)).toBeTruthy()
  expect(screen.getByRole('combobox', { name: 'History operation' })).toBeTruthy()
  expect(screen.getByLabelText('History from date')).toBeTruthy()
  expect(screen.getByLabelText('History to date')).toBeTruthy()
  fireEvent.change(screen.getByLabelText('History operation'), { target: { value: 'add' } })
  await waitFor(() => expect(state.calls.some(call => call.path.includes('operation=add'))).toBe(true))
  expect(screen.getByText('Filters · 1 active')).toBeTruthy()
  expect(state.calls.some(call => call.path === '/history' && call.options.method === 'DELETE')).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: 'Reset filters' }))
  await waitFor(() => expect(state.calls.some(call => call.path === '/history?' && !call.path.includes('operation='))).toBe(true))
  expect(screen.getByRole('combobox', { name: 'History operation' }).value).toBe('')

  const clearHistoryButton = screen.getByRole('button', { name: 'Clear history' })
  fireEvent.click(clearHistoryButton)
  expect(state.calls.some(call => call.path === '/history' && call.options.method === 'DELETE')).toBe(false)
  expect(screen.getByRole('region', { name: 'Clear history confirmation' })).toBeTruthy()
  let cancelButton = screen.getByRole('button', { name: 'Cancel clear' })
  await waitFor(() => expect(document.activeElement).toBe(cancelButton))
  fireEvent.keyDown(window, { key: 'Escape', code: 'Escape' })
  await waitFor(() => expect(screen.queryByRole('region', { name: 'Clear history confirmation' })).toBeNull())
  expect(document.activeElement).toBe(clearHistoryButton)

  fireEvent.click(clearHistoryButton)
  cancelButton = screen.getByRole('button', { name: 'Cancel clear' })
  await waitFor(() => expect(document.activeElement).toBe(cancelButton))
  fireEvent.click(cancelButton)
  await waitFor(() => expect(screen.queryByRole('region', { name: 'Clear history confirmation' })).toBeNull())
  expect(document.activeElement).toBe(clearHistoryButton)
  expect(state.calls.some(call => call.path === '/history' && call.options.method === 'DELETE')).toBe(false)

  fireEvent.click(clearHistoryButton)
  fireEvent.click(screen.getByRole('button', { name: 'Confirm clear history' }))
  await waitFor(() => expect(state.calls.some(call => call.path === '/history' && call.options.method === 'DELETE')).toBe(true))
  await waitFor(() => expect(document.activeElement).toBe(clearHistoryButton))
})
