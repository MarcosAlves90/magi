import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import plugin from '../../desktop/plugin.js'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

function mountMagi() {
  const state = { entries: { memory: ['Existing fact'], user: ['User fact'] }, requests: [] }
  const ctx = {
    source: 'transfer-test',
    registerMany(registrations) { ctx.registrations = registrations },
    async rest(path, options = {}) {
      state.requests.push({ path, options })
      if (path === '/records') return { records: [], count: 0, issues: [] }
      if (path === '/memory') {
        const targets = Object.fromEntries(Object.entries(state.entries).map(([id, entries]) => [id, {
          entries: entries.map((content, index) => ({ content, index })), count: entries.length,
          used_chars: 20, char_limit: 500, usage_percent: 4, estimated_tokens: 10
        }]))
        return { targets }
      }
      if (path === '/memory/import/preview') return {
        success: true, target: 'memory', source_fingerprints: { memory: 'fingerprint-1' },
        targets: { memory: { additions: ['Café\\nnew line'], existing_count: 1, skipped_count: 1 } }
      }
      if (path === '/memory/import') {
        state.entries.memory.push('Café\\nnew line')
        return { success: true, partial: false, outcomes: { memory: { status: 'applied', added_count: 1 } } }
      }
      throw new Error(`Unexpected REST call: ${path}`)
    }
  }
  plugin.register(ctx)
  const route = ctx.registrations.find(item => item.area === 'routes' && item.data.path === '/magi')
  render(route.render())
  return state
}

it('reviews an imported JSON file and writes only after explicit confirmation', async () => {
  vi.stubGlobal('fetch', () => { throw new Error('Unexpected external network access') })
  const state = mountMagi()
  fireEvent.click(screen.getByRole('button', { name: /Stored memory/ }))
  await screen.findByRole('heading', { name: 'Stored memory' })
  fireEvent.click(screen.getByRole('button', { name: 'Memory transfer options' }))
  await screen.findByRole('menuitem', { name: 'Export both stores (MEMORY.md + USER.md)' })
  const payload = { format_version: 1, target: 'memory', entries: ['Existing fact', 'Café\\nnew line'] }
  const file = { name: 'memory.json', size: 100, text: async () => JSON.stringify(payload) }
  fireEvent.change(screen.getByLabelText('Import memory JSON'), { target: { files: [file] } })
  const preview = await screen.findByRole('region', { name: 'Memory import preview' })
  expect(preview.textContent).toContain('1 to add · 1 already present')
  expect(state.requests.filter(request => request.path === '/memory/import')).toHaveLength(0)
  fireEvent.click(screen.getByRole('button', { name: 'Confirm import' }))
  await waitFor(() => expect(state.requests.filter(request => request.path === '/memory/import')).toHaveLength(1))
  expect(state.requests.find(request => request.path === '/memory/import').options.body).toEqual({
    ...payload, source_fingerprints: { memory: 'fingerprint-1' }, confirmed: true
  })
  expect(await screen.findByText('memory: applied (1 added)')).toBeTruthy()
})

it('creates a versioned JSON download locally without a network export request', async () => {
  vi.stubGlobal('fetch', () => { throw new Error('Unexpected external network access') })
  const state = mountMagi()
  fireEvent.click(screen.getByRole('button', { name: /Stored memory/ }))
  await screen.findByRole('heading', { name: 'Stored memory' })
  fireEvent.click(screen.getByRole('button', { name: 'Memory transfer options' }))
  await screen.findByRole('menuitem', { name: 'Export both stores (MEMORY.md + USER.md)' })
  const link = await screen.findByRole('menuitem', { name: /Export memory/ })
  expect(link.getAttribute('download')).toBe('magi-memory-memory.json')
  expect(JSON.parse(decodeURIComponent(link.getAttribute('href').split(',')[1]))).toEqual({
    format_version: 1, target: 'memory', entries: ['Existing fact']
  })
  fireEvent.click(link)
  expect(state.requests.some(request => request.path.includes('export'))).toBe(false)
})

it('updates the accessible name and download when the target store changes', async () => {
  const state = mountMagi()
  fireEvent.click(screen.getByRole('button', { name: /Stored memory/ }))
  await screen.findByRole('heading', { name: 'Stored memory' })

  // Switch to User store
  fireEvent.click(screen.getByRole('button', { name: /User · 4%/ }))

  fireEvent.click(screen.getByRole('button', { name: 'Memory transfer options' }))
  const link = await screen.findByRole('menuitem', { name: 'Export memory (USER.md)' })
  expect(link.getAttribute('download')).toBe('magi-memory-user.json')
  expect(link.getAttribute('aria-label')).toBe('Export memory (USER.md)')
})

it('disables export actions while an import is in progress', async () => {
  const state = mountMagi()
  fireEvent.click(screen.getByRole('button', { name: /Stored memory/ }))
  await screen.findByRole('heading', { name: 'Stored memory' })

  // Trigger import to enter importing state
  const payload = { format_version: 1, target: 'memory', entries: ['New'] }
  const file = { name: 'm.json', size: 10, text: async () => JSON.stringify(payload) }
  fireEvent.change(screen.getByLabelText('Import memory JSON'), { target: { files: [file] } })
  await screen.findByRole('region', { name: 'Memory import preview' })

  fireEvent.click(screen.getByRole('button', { name: 'Confirm import' }))

  // Open transfer menu during pending import
  fireEvent.click(screen.getByRole('button', { name: 'Memory transfer options' }))
  const memoryExport = screen.getByRole('menuitem', { name: /Export memory/ })
  const bothExport = screen.getByRole('menuitem', { name: 'Export both stores (MEMORY.md + USER.md)' })

  expect(memoryExport.getAttribute('aria-disabled')).toBe('true')
  expect(memoryExport.getAttribute('href')).toBeNull()
  expect(memoryExport.className).toContain('pointer-events-none')
  expect(bothExport.getAttribute('aria-disabled')).toBe('true')
})

it('manages focus and keyboard navigation for the transfer menu', async () => {
  const state = mountMagi()
  const user = userEvent.setup()
  fireEvent.click(screen.getByRole('button', { name: /Stored memory/ }))
  await screen.findByRole('heading', { name: 'Stored memory' })

  const trigger = screen.getByRole('button', { name: 'Memory transfer options' })
  trigger.focus()

  // ArrowDown opens menu and focuses first item
  await user.keyboard('{ArrowDown}')
  const firstItem = await screen.findByRole('menuitem', { name: /Export memory/, timeout: 10000 })
  await waitFor(() => expect(firstItem).toBe(document.activeElement), { timeout: 5000 })

  // Escape closes menu and returns focus to trigger
  await user.keyboard('{Escape}')
  await waitFor(() => expect(screen.queryByRole('menu')).toBeNull(), { timeout: 10000 })
  await waitFor(() => expect(document.activeElement).toBe(trigger), { timeout: 5000 })
})

it('keeps transfer actions discoverable via dropdown without nested details', async () => {
  mountMagi()
  fireEvent.click(screen.getByRole('button', { name: /Stored memory/ }))
  await screen.findByRole('heading', { name: 'Stored memory' })

  const transferButton = screen.getByRole('button', { name: 'Memory transfer options' })
  expect(transferButton).toBeTruthy()
  expect(transferButton.getAttribute('aria-expanded')).toBe('false')

  fireEvent.click(transferButton)
  await screen.findByRole('menu', { name: 'Memory transfer options' })

  expect(screen.getByRole('menuitem', { name: /Export memory \(MEMORY.md\)/ })).toBeTruthy()
  expect(screen.getByRole('menuitem', { name: 'Export both stores (MEMORY.md + USER.md)' })).toBeTruthy()
  expect(screen.getByRole('menuitem', { name: 'Import JSON' })).toBeTruthy()

  fireEvent.click(transferButton)
  expect(transferButton.getAttribute('aria-expanded')).toBe('false')

  expect(screen.getByRole('button', { name: 'Compact with AI' })).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Review memory with AI' })).toBeTruthy()
  expect(screen.getByText('Estimated tokens')).toBeTruthy()
  expect(screen.getByText('Add entry')).toBeTruthy()
  expect(screen.getByRole('region', { name: 'Stored memory workspace' }).querySelectorAll('details')).toHaveLength(0)
})