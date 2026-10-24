import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import plugin from '../../desktop/plugin.js'

beforeEach(() => vi.stubGlobal('fetch', () => { throw new Error('Unexpected external network access') }))
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

function fixture() {
  const state = {
    records: [
      { id: 'rec-one', summary: 'Save the alpha', action: 'add', target: 'memory', origin: 'foreground', created_at: 1, target_status: { state: 'ready', can_apply: true } },
      { id: 'rec-two', summary: 'Update the beta', action: 'replace', target: 'user', origin: 'auto_review', created_at: 2, target_status: { state: 'ready', can_apply: true } }
    ],
    entries: { memory: ['One durable fact', 'Another fact'], user: ['User preference'] },
    requests: [], failNext: new Map(), fingerprint: 'fingerprint-1'
  }
  const ctx = {
    source: 'test-profile',
    registerMany(registrations) { ctx.registrations = registrations },
    async rest(path, options = {}) {
      state.requests.push({ path, method: options.method || 'GET', body: options.body })
      if (state.failNext.get(path)) {
        state.failNext.delete(path)
        throw new Error('Network unavailable')
      }
      if (path === '/records' && !options.method) return { records: [...state.records], count: state.records.length, issues: [] }
      const detail = path.match(/^\/records\/(.+)$/)
      if (detail && !options.method) {
        const record = state.records.find(record => record.id === decodeURIComponent(detail[1]))
        return { record, payload: { action: record.action, target: record.target, content: record.summary }, proposal: 'Proposal text', diff: 'Diff text', raw: '{}', verify: 'Verified' }
      }
      if (detail && options.method === 'POST') {
        const id = decodeURIComponent(detail[1]).replace(/\/decision$/, '')
        state.records = id === 'all' ? [] : state.records.filter(record => record.id !== id)
        return { success: true, output: `${options.body.action} complete` }
      }
      if (path === '/memory' && !options.method) {
        const targets = {}
        for (const target of ['memory', 'user']) {
          const entries = state.entries[target].map((content, index) => ({ content, index }))
          targets[target] = {
            entries, count: entries.length, used_chars: 20, char_limit: 500, usage_percent: 4,
            estimated_tokens: 10, estimated_token_limit: 100, source_fingerprint: state.fingerprint,
            diagnostics: {
              entry_count: entries.length, used_chars: 20, remaining_chars: 480, over_budget_chars: 0,
              size_distribution: { '0-100': entries.length }, long_entry_indices: [],
              exact_duplicate_groups: [], long_entry_threshold_chars: 500
            }
          }
        }
        return { targets }
      }
      const targetMatch = path.match(/^\/memory\/(memory|user)(?:\/(.*))?$/)
      if (targetMatch) {
        const [, target, suffix] = targetMatch
        if (!suffix && options.method === 'PUT') {
          const index = state.entries[target].indexOf(options.body.old_text)
          if (index === -1) throw new Error('stale source')
          state.entries[target][index] = options.body.content
          return { success: true, result: { message: 'Updated entry' } }
        }
        if (suffix === 'entries' && options.method === 'POST') {
          state.entries[target].push(options.body.content)
          return { success: true, result: { message: 'Added entry' } }
        }
        if (suffix === 'entries' && options.method === 'DELETE') {
          state.entries[target] = state.entries[target].filter(item => item !== options.body.old_text)
          return { success: true, result: { message: 'Deleted entry' } }
        }
        if (suffix === 'compact/preview') return {
          success: true, target, source_fingerprint: state.fingerprint,
          source_entries: [...state.entries[target]], proposed_entries: ['Condensed fact'], before_entry_count: 2, after_entry_count: 1,
          before_tokens: 15, after_tokens: 5, reduction_percent: 60
        }
        if (suffix === 'compact') {
          if (options.body.source_fingerprint !== state.fingerprint) throw new Error('Source changed: stale fingerprint')
          state.entries[target] = [...options.body.entries]
          return { success: true, result: { message: 'Compaction applied' } }
        }
      }
      throw new Error(`Unexpected REST call: ${path}`)
    }
  }
  plugin.register(ctx)
  const route = ctx.registrations.find(item => item.area === 'routes' && item.data.path === '/magi')
  return { state, ctx, mount: () => render(route.render()) }
}

function btn(name) { return screen.getByRole('button', { name: name.includes(' ') ? new RegExp(name.replaceAll(' ', '\\s*')) : name, exact: true }) }
function calls(state, path, method = 'POST') { return state.requests.filter(request => request.path === path && request.method === method) }

describe('Magi actual Desktop rendering and host SDK requests', () => {
  it('keeps the selected memory editor beside a responsive entry list and fills its pane', async () => {
    const { mount } = fixture()
    mount()
    fireEvent.click(btn('Stored memory Durable context'))

    const workspace = screen.queryByRole('region', { name: 'Stored memory workspace' })
    expect(workspace, 'responsive stored-memory split layout is exposed in the workspace').toBeTruthy()
    expect(workspace.style.gridTemplateColumns).toContain('auto-fit')
    expect(workspace.style.gridTemplateColumns).toContain('minmax')
    expect(workspace.style.gridAutoRows).toBe('minmax(22rem, 1fr)')
    expect(workspace.style.overflow).toBe('auto')
    expect(within(workspace).getByRole('complementary', { name: 'Memory entries' })).toBeTruthy()
    const editor = within(workspace).getByRole('region', { name: 'Memory entry editor' })
    const textarea = within(editor).getByRole('textbox', { name: 'Edit memory memory entry' })
    expect(textarea.style.flex).toContain('1')
    expect(screen.getByRole('group', { name: 'Memory usage summary' }).style.gridTemplateColumns).toContain('auto-fit')
  })

  it('keeps stacked review and memory panes usable at narrow viewport widths', async () => {
    const originalWidth = Object.getOwnPropertyDescriptor(window, 'innerWidth')
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 })
    try {
      const { mount } = fixture()
      mount()
      await screen.findAllByText('Save the alpha')
      const pendingWorkspace = screen.getByRole('region', { name: 'Pending review workspace' })
      await waitFor(() => expect(pendingWorkspace.style.minHeight).toBe('44rem'))
      expect(pendingWorkspace.style.overflow).toBe('auto')

      fireEvent.click(btn('Stored memory Durable context'))
      const memoryWorkspace = await screen.findByRole('region', { name: 'Stored memory workspace' })
      await waitFor(() => expect(memoryWorkspace.style.minHeight).toBe('44rem'))
      expect(memoryWorkspace.style.overflow).toBe('auto')
    } finally {
      if (originalWidth) Object.defineProperty(window, 'innerWidth', originalWidth)
      else delete window.innerWidth
    }
  })

  it('makes memory estimates, diagnostics, transfer, and AI tools directly discoverable', async () => {
    const { mount } = fixture()
    mount()
    fireEvent.click(btn('Stored memory Durable context'))
    await screen.findByRole('heading', { name: 'Stored memory' })

    expect(screen.getByText('Estimated tokens')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Inspect diagnostics' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Import JSON' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Export both stores' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Compact with AI' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Review memory with AI' })).toBeTruthy()
    const exportButton = screen.getByRole('button', { name: 'Export memory' })
    const exportTooltip = document.getElementById(exportButton.getAttribute('aria-describedby'))
    expect(exportTooltip?.textContent).toMatch(/does not upload export files/)
    const workspace = screen.getByRole('region', { name: 'Stored memory workspace' })
    expect(workspace.querySelectorAll('details')).toHaveLength(0)

    fireEvent.click(screen.getByRole('button', { name: 'Inspect diagnostics' }))
    expect(await screen.findByText(/Entry sizes \(characters\)/)).toBeTruthy()
  })

  it('keeps non-obvious memory actions described by accessible tooltips instead of a side notice', async () => {
    const { mount } = fixture()
    mount()
    fireEvent.click(btn('Stored memory Durable context'))
    await screen.findByRole('heading', { name: 'Stored memory' })

    const toolbar = screen.queryByRole('group', { name: 'Memory actions and data handling' })
    expect(toolbar, 'all transfer and AI controls share one compact accessible region').toBeTruthy()
    expect(within(toolbar).getByRole('group', { name: 'Memory transfer' })).toBeTruthy()
    expect(within(toolbar).getByRole('group', { name: 'Memory AI actions' })).toBeTruthy()
    expect(within(toolbar).queryByRole('note'), 'no adjacent note; all non-obvious actions have accessible tooltips').toBeNull()

    const expectTooltip = (button, expectedText) => {
      const tooltipId = button.getAttribute('aria-describedby')
      expect(tooltipId).toBeTruthy()
      const tooltip = document.getElementById(tooltipId)
      expect(tooltip?.getAttribute('role')).toBe('tooltip')
      expect(tooltip?.textContent).toMatch(expectedText)
      expect(tooltip?.className).toContain('bg-(--ui-bg-elevated)')
      expect(tooltip?.className).toContain('group-hover:opacity-100')
      expect(tooltip?.className).toContain('group-focus-within:opacity-100')
      return tooltip
    }

    expectTooltip(within(toolbar).getByRole('button', { name: 'Export memory' }), /private.*local.*does not upload export files/i)
    expectTooltip(within(toolbar).getByRole('button', { name: 'Export both stores' }), /private.*local.*not uploaded/i)
    expectTooltip(within(toolbar).getByRole('button', { name: 'Import JSON' }), /preview.*confirm/i)
    expectTooltip(within(toolbar).getByRole('button', { name: 'Compact with AI' }), /configured AI.*review.*apply.*nothing changes automatically/i)
    expectTooltip(within(toolbar).getByRole('button', { name: 'Review memory with AI' }), /selected memory.*suggestions.*nothing is applied automatically/i)
    expectTooltip(screen.getByRole('button', { name: 'Inspect diagnostics' }), /read-only.*entry sizes.*duplicate/i)
    expect(screen.getByRole('button', { name: 'Add entry' }).getAttribute('aria-describedby')).toBeNull()
  })

  it('activates the real navigation with keyboard Enter and exposes selected state', async () => {
    const { mount } = fixture()
    mount()
    const user = userEvent.setup()
    const stored = btn('Stored memory Durable context')
    stored.focus()
    expect(document.activeElement).toBe(stored)
    await user.keyboard('{Enter}')
    expect(stored.getAttribute('aria-pressed')).toBe('true')
    expect(await screen.findByRole('textbox', { name: 'Edit memory memory entry' })).toBeTruthy()
  })

  it('hides pending filters until requested and marks active filters', async () => {
    const { mount } = fixture()
    mount()
    await screen.findAllByText('Save the alpha')
    const pendingWorkspace = screen.getByRole('region', { name: 'Pending review workspace' })
    expect(pendingWorkspace.style.gridAutoRows).toBe('minmax(22rem, 1fr)')
    expect(pendingWorkspace.style.overflow).toBe('auto')

    expect(screen.queryByRole('combobox', { name: 'Pending action filter' })).toBeNull()
    const filters = screen.getByRole('button', { name: 'Filters' })
    expect(filters.getAttribute('aria-expanded')).toBe('false')
    const filtersPanelId = filters.getAttribute('aria-controls')
    expect(filtersPanelId).toBeTruthy()
    const filtersPanel = document.getElementById(filtersPanelId)
    expect(filtersPanel).toBeTruthy()
    expect(filtersPanel.hidden).toBe(true)
    filters.focus()
    expect(document.activeElement).toBe(filters)
    const user = userEvent.setup()
    await user.keyboard('{Enter}')
    expect(filters.getAttribute('aria-expanded')).toBe('true')
    expect(filtersPanel.hidden).toBe(false)
    await user.keyboard(' ')
    expect(filters.getAttribute('aria-expanded')).toBe('false')
    expect(filtersPanel.hidden).toBe(true)
    await user.keyboard('{Enter}')
    expect(filters.getAttribute('aria-expanded')).toBe('true')
    expect(filtersPanel.hidden).toBe(false)

    const actionFilter = await screen.findByRole('combobox', { name: 'Pending action filter' })
    fireEvent.change(actionFilter, { target: { value: 'add' } })
    expect(await screen.findByText('Filters · 1 active')).toBeTruthy()
    fireEvent.click(btn('Clear filters'))
    expect(actionFilter.value).toBe('')
    expect(screen.getByRole('button', { name: 'Filters' }).getAttribute('aria-expanded')).toBe('true')

    expect(screen.queryByRole('button', { name: 'Reject all' })).toBeNull()
    const bulkActions = screen.getByRole('button', { name: 'Actions for all pending items' })
    expect(bulkActions.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(bulkActions)
    expect(bulkActions.getAttribute('aria-expanded')).toBe('true')
    expect(btn('Reject all')).toBeTruthy()
    expect(screen.getByText(/including proposals hidden by filters/i)).toBeTruthy()
  })

  it('filters proposals, switches selection using real clicks, and refreshes the queue', async () => {
    const { state, mount } = fixture()
    mount()
    await screen.findAllByText('Save the alpha')
    expect(screen.getByRole('navigation', { name: 'Magi sections' })).toBeTruthy()
    expect(btn('Pending writes Review queue').getAttribute('aria-pressed')).toBe('true')
    fireEvent.change(screen.getByRole('searchbox', { name: /search pending/i }), { target: { value: 'beta' } })
    expect(screen.getByText('1 of 2 shown')).toBeTruthy()
    expect(within(screen.getByRole('complementary')).queryAllByText('Save the alpha')).toHaveLength(0)
    fireEvent.click(screen.getByText('Filters'))
    fireEvent.click(btn('Clear filters'))
    fireEvent.click(screen.getByRole('button', { name: /rec-two/ }))
    await screen.findAllByText('Update the beta')
    fireEvent.click(btn('Diff'))
    expect(await screen.findByText('Diff text')).toBeTruthy()
    fireEvent.click(btn('Refresh'))
    await waitFor(() => expect(calls(state, '/records', 'GET').length).toBeGreaterThan(1))
  })

  it('makes one approval request and requires explicit bulk rejection confirmation', async () => {
    const { state, mount } = fixture()
    mount()
    await screen.findAllByText('Save the alpha')
    await waitFor(() => expect(btn('Approve').disabled).toBe(false))
    fireEvent.click(btn('Approve'))
    await waitFor(() => expect(calls(state, '/records/rec-one/decision')).toHaveLength(1))
    expect(calls(state, '/records/rec-one/decision')[0].body).toEqual({ action: 'approve' })
    await screen.findByText('1 pending')
    fireEvent.click(screen.getByRole('button', { name: 'Actions for all pending items' }))
    fireEvent.click(btn('Reject all'))
    expect(calls(state, '/records/all/decision')).toHaveLength(0)
    fireEvent.click(btn('Cancel'))
    expect(calls(state, '/records/all/decision')).toHaveLength(0)
    fireEvent.click(btn('Reject all'))
    fireEvent.click(btn('Confirm reject all'))
    await waitFor(() => expect(calls(state, '/records/all/decision')).toHaveLength(1))
    expect(calls(state, '/records/all/decision')[0].body).toEqual({ action: 'reject' })
    expect(await screen.findByText('Nothing to review')).toBeTruthy()
  })

  it('renders an error state and retries an unavailable backend instead of claiming success', async () => {
    const { state, mount } = fixture()
    state.failNext.set('/records', true)
    mount()
    await screen.findByText('Magi backend unavailable')
    fireEvent.click(btn('Retry'))
    expect(await screen.findAllByText('Save the alpha')).not.toHaveLength(0)
  })

  it('edits and deletes stored entries with explicit confirmation and accessible controls', async () => {
    const { state, mount } = fixture()
    mount()
    fireEvent.click(btn('Stored memory Durable context'))
    const editor = await screen.findByRole('textbox', { name: 'Edit memory memory entry' })
    expect(btn('Save changes').disabled).toBe(true)
    fireEvent.change(editor, { target: { value: 'One durable fact updated' } })
    expect(btn('Save changes').disabled).toBe(false)
    fireEvent.click(btn('Save changes'))
    await waitFor(() => expect(calls(state, '/memory/memory', 'PUT')).toHaveLength(1))
    expect(calls(state, '/memory/memory', 'PUT')[0].body).toEqual({ old_text: 'One durable fact', content: 'One durable fact updated' })
    expect(await screen.findByText('Updated entry')).toBeTruthy()
    fireEvent.click(btn('Delete entry'))
    expect(calls(state, '/memory/memory/entries', 'DELETE')).toHaveLength(0)
    fireEvent.click(btn('Confirm delete'))
    await waitFor(() => expect(calls(state, '/memory/memory/entries', 'DELETE')).toHaveLength(1))
  })

  it('adds a User entry and shows the correct target and disabled empty submission', async () => {
    const { state, mount } = fixture()
    mount()
    fireEvent.click(btn('Stored memory Durable context'))
    await screen.findByRole('textbox', { name: 'Edit memory memory entry' })
    fireEvent.click(btn('User · 4%'))
    fireEvent.click(screen.getAllByRole('button', { name: 'Add entry' })[0])
    expect(screen.getAllByRole('button', { name: 'Add entry' })[1].disabled).toBe(true)
    fireEvent.change(screen.getByRole('textbox', { name: 'Add user memory entry' }), { target: { value: 'My choice' } })
    const addButtons = screen.getAllByRole('button', { name: 'Add entry' })
    expect(addButtons[1].disabled).toBe(false)
    fireEvent.click(addButtons[1])
    await waitFor(() => expect(calls(state, '/memory/user/entries')).toHaveLength(1))
    expect(calls(state, '/memory/user/entries')[0].body).toEqual({ content: 'My choice' })
  })

  it('previews, cancels without writing, then explicitly applies; backend stale conflict stays visible', async () => {
    const { state, mount } = fixture()
    mount()
    fireEvent.click(btn('Stored memory Durable context'))
    await screen.findByRole('textbox', { name: 'Edit memory memory entry' })
    fireEvent.click(btn('Compact with AI'))
    expect(await screen.findByText('Condensed fact')).toBeTruthy()
    expect(calls(state, '/memory/memory/compact')).toHaveLength(0)
    fireEvent.click(btn('Cancel preview'))
    expect(screen.queryByText('Condensed fact')).toBeNull()
    expect(calls(state, '/memory/memory/compact')).toHaveLength(0)
    fireEvent.click(btn('Compact with AI'))
    await screen.findByText('Condensed fact')
    state.fingerprint = 'new-fingerprint'
    fireEvent.click(btn('Apply compaction'))
    expect(await screen.findByText('Source changed: stale fingerprint')).toBeTruthy()
    expect(state.entries.memory).toEqual(['One durable fact', 'Another fact'])
    state.fingerprint = 'fingerprint-1'
    fireEvent.click(btn('Apply compaction'))
    expect(await screen.findByText('Compaction applied')).toBeTruthy()
    const apply = calls(state, '/memory/memory/compact')
    expect(apply).toHaveLength(2)
    expect(apply[1].body).toEqual({ source_fingerprint: 'fingerprint-1', entries: ['Condensed fact'] })
  })
})
