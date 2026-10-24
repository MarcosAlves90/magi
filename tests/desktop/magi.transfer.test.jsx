import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
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
        targets: { memory: { additions: ['Café\nnew line'], existing_count: 1, skipped_count: 1 } }
      }
      if (path === '/memory/import') {
        state.entries.memory.push('Café\nnew line')
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
  await screen.findByText('Export both stores')
  const payload = { format_version: 1, target: 'memory', entries: ['Existing fact', 'Café\nnew line'] }
  const file = { name: 'memory.json', size: 100, text: async () => JSON.stringify(payload) }
  fireEvent.change(screen.getByLabelText('Import memory JSON'), { target: { files: [file] } })
  const preview = await screen.findByRole('region', { name: 'Memory import preview' })
  expect(preview.textContent).toContain('1 to add · 1 already present')
  expect(state.requests.filter(request => request.path === '/memory/import')).toHaveLength(0)
  expect(screen.getByText(/does not upload export files/)).toBeTruthy()
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
  await screen.findByText('Export both stores')
  const link = screen.getByRole('button', { name: 'Export memory' })
  expect(link.getAttribute('download')).toBe('magi-memory-memory.json')
  expect(JSON.parse(decodeURIComponent(link.getAttribute('href').split(',')[1]))).toEqual({
    format_version: 1, target: 'memory', entries: ['Existing fact']
  })
  fireEvent.click(link)
  expect(state.requests.some(request => request.path.includes('export'))).toBe(false)
})

it('keeps transfer, token estimates, and AI actions directly discoverable without nested menus', async () => {
  mountMagi()
  fireEvent.click(screen.getByRole('button', { name: /Stored memory/ }))
  await screen.findByRole('heading', { name: 'Stored memory' })

  expect(screen.getByRole('button', { name: 'Import JSON' })).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Export memory' })).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Export both stores' })).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Compact with AI' })).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Review memory with AI' })).toBeTruthy()
  expect(screen.getByText('Estimated tokens')).toBeTruthy()
  expect(screen.getByText('Add entry')).toBeTruthy()
  expect(screen.getByRole('region', { name: 'Stored memory workspace' }).querySelectorAll('details')).toHaveLength(0)
})
