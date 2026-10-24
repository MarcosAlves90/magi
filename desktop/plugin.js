import { ROUTES_AREA, SIDEBAR_NAV_AREA, useQuery } from '@hermes/plugin-sdk'
import { useEffect, useMemo, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const VIEWS = [
  ['overview', 'Overview'],
  ['proposal', 'Proposal'],
  ['diff', 'Diff'],
  ['raw', 'Raw'],
  ['verify', 'Verify']
]

const STORED_TARGETS = [
  ['memory', 'Memory'],
  ['user', 'User']
]

const EMPTY_PENDING_FILTERS = { action: '', target: '', origin: '', state: '', fromDate: '', toDate: '' }

function recordLabel(record) {
  return `${record.action}/${record.target}`
}

function formatCreated(value) {
  const date = value == null || value === '' ? null : new Date(Number(value) * 1000)
  return !date || !Number.isFinite(date.getTime()) ? 'Unknown date' : date.toLocaleString()
}

// Compare UTC calendar days so inclusive bounds are independent of the host timezone.
function pendingCreatedDate(value) {
  if (value == null || value === '') return null
  const seconds = Number(value)
  if (!Number.isFinite(seconds)) return null
  const date = new Date(seconds * 1000)
  return Number.isFinite(date.getTime()) ? date.toISOString().slice(0, 10) : null
}

function filterPendingRecords(records, filters = {}) {
  const query = String(filters.query || '').trim().toLocaleLowerCase()
  return records.filter(record => {
    if (query && !`${record.id} ${record.action} ${record.target} ${record.origin} ${record.summary}`.toLocaleLowerCase().includes(query)) return false
    if (filters.action && record.action !== filters.action) return false
    if (filters.target && record.target !== filters.target) return false
    if (filters.origin && String(record.origin || 'unknown') !== filters.origin) return false
    if (filters.state && (record.target_status?.state || 'unknown') !== filters.state) return false
    if (filters.fromDate || filters.toDate) {
      const date = pendingCreatedDate(record.created_at)
      if (!date || (filters.fromDate && date < filters.fromDate) || (filters.toDate && date > filters.toDate)) return false
    }
    return true
  })
}

function normalizeDecisionResult(report) {
  const items = (Array.isArray(report?.items) ? report.items : []).map((item, index) => {
    const status = ['applied', 'rejected', 'failed', 'applied_cleanup_failed'].includes(item?.status)
      ? item.status : 'failed'
    const error = status === 'applied_cleanup_failed'
      ? 'Memory may already be applied; reconcile the pending record before any further action.'
      : status === 'failed'
        ? 'This operation failed. Inspect the pending record and its current state.'
        : ''
    return { id: String(item?.id || '(unknown)'), status, error, index }
  })
  return {
    action: report?.action === 'reject' ? 'reject' : 'approve',
    outcome: ['complete', 'partial', 'failed'].includes(report?.outcome) ? report.outcome : 'failed',
    attempted: Math.max(0, Number(report?.attempted) || 0),
    applied: Math.max(0, Number(report?.applied) || 0),
    rejected: Math.max(0, Number(report?.rejected) || 0),
    failed: Math.max(0, Number(report?.failed) || 0),
    reconciliation: items.filter(item => item.status === 'applied_cleanup_failed').length,
    items
  }
}

function canRetryDecisionItem(item, report, records) {
  return report?.action === 'reject' &&
    item?.status === 'failed' &&
    Array.isArray(records) &&
    records.some(record => record.id === item.id)
}

// Line-aligned LCS for ordinary previews; bounded fallback keeps huge inputs linear.
// Each row includes a word label so highlighting never relies on color alone.
function buildCompactionDiff(beforeEntries, afterEntries) {
  const before = beforeEntries.join('\n§\n').split('\n')
  const after = afterEntries.join('\n§\n').split('\n')
  const rows = []
  const put = (kind, text) => rows.push({ kind, text })
  if (before.length * after.length <= 80000) {
    const table = Array.from({ length: before.length + 1 }, () => new Uint32Array(after.length + 1))
    for (let i = before.length - 1; i >= 0; i--) {
      for (let j = after.length - 1; j >= 0; j--) {
        table[i][j] = before[i] === after[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1])
      }
    }
    let i = 0, j = 0
    while (i < before.length || j < after.length) {
      if (i < before.length && j < after.length && before[i] === after[j]) { put('unchanged', before[i]); i++; j++ }
      else if (i < before.length && (j === after.length || table[i + 1][j] >= table[i][j + 1])) put('removed', before[i++])
      else put('added', after[j++])
    }
  } else {
    let start = 0
    while (start < before.length && start < after.length && before[start] === after[start]) put('unchanged', before[start++])
    let endBefore = before.length - 1, endAfter = after.length - 1
    while (endBefore >= start && endAfter >= start && before[endBefore] === after[endAfter]) { endBefore--; endAfter-- }
    for (let i = start; i <= endBefore; i++) put('removed', before[i])
    for (let j = start; j <= endAfter; j++) put('added', after[j])
    for (let i = endBefore + 1; i < before.length; i++) put('unchanged', before[i])
  }
  return rows
}

function validateCompactionEdits(sourceEntries, editedEntries) {
  const delimiter = '\n§\n'
  const countChars = value => Array.from(value).length
  const beforeChars = countChars(sourceEntries.join(delimiter))
  const normalized = editedEntries.map(entry => String(entry).trim())
  const afterChars = countChars(normalized.join(delimiter))
  const maxEntries = Math.max(1, Math.min(6, Math.floor((sourceEntries.length + 3) / 4)))
  const maxChars = Math.max(1, Math.floor(beforeChars * 0.60))
  const errors = []
  if (!normalized.length || normalized.some(entry => !entry)) errors.push('Entries must not be empty.')
  if (normalized.length > maxEntries) errors.push(`At most ${maxEntries} entries allowed.`)
  if (new Set(normalized).size !== normalized.length) errors.push('Exact duplicate entries are not allowed.')
  if (normalized.some(entry => entry.includes(delimiter))) errors.push('The Hermes memory delimiter is not allowed.')
  if (afterChars >= beforeChars || afterChars > maxChars) errors.push(`Proposal must be shorter and no more than ${maxChars} characters.`)
  return { valid: errors.length === 0, errors, normalized, beforeChars, afterChars, maxEntries, maxChars,
    reductionPercent: beforeChars ? Math.round((1 - afterChars / beforeChars) * 1000) / 10 : 0 }
}

function resolveStoredDraft(slots, key, base, fingerprint) {
  const slot = slots[key]
  const text = slot ? slot.text : base
  const dirty = Boolean(slot && text !== slot.base)
  return { text, dirty, conflict: Boolean(dirty && slot.sourceFingerprint && fingerprint !== slot.sourceFingerprint) }
}

function isActiveCompactionRequest(requestId, currentId) {
  return requestId === currentId
}

function classifyCompactionFailure(error) {
  const message = error instanceof Error ? error.message : String(error?.message || error)
  if (/timeout|timed out|deadline/i.test(message)) {
    return `Request timed out while waiting: ${message}. Hermes or the model/provider may still finish; use Compact with AI to retry manually.`
  }
  if (/network|fetch|connect|unreachable|offline/i.test(message)) {
    return `Network error: ${message}. Check the Hermes connection and retry manually.`
  }
  if (error?.kind === 'validation') {
    return `Compaction validation failed: ${message}. Review the constraints before retrying.`
  }
  return `Compaction request failed: ${message}. No success was confirmed; check the stored memory before retrying.`
}

const CONTROL_TRANSITION = 'transition-all duration-100 ease-out motion-reduce:transition-none'
const PANEL_TRANSITION = 'transition-[background-color,border-color,opacity] duration-150 ease-out motion-reduce:transition-none'
const RESIZABLE_ENTRY_VIEWER = 'h-[48vh] min-h-40 max-h-[72vh] flex-none resize-y overflow-auto'

function StatusDot({ tone = 'default' }) {
  const toneClass =
    tone === 'danger'
      ? 'bg-(--ui-danger,#f87171)'
      : tone === 'accent'
        ? 'bg-(--ui-accent)'
        : 'bg-(--ui-text-quaternary)'
  return jsx('span', { className: `inline-block size-1.5 shrink-0 rounded-full ${toneClass}` })
}

function Badge({ children, tone = 'default' }) {
  const toneClass =
    tone === 'danger'
      ? 'border-(--ui-danger,#f87171) text-(--ui-danger,#f87171)'
      : tone === 'accent'
        ? 'border-(--ui-accent) text-(--ui-accent)'
        : 'border-(--ui-stroke-secondary) text-(--ui-text-tertiary)'
  return jsx('span', {
    className: `inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[0.6875rem] font-medium ${toneClass}`,
    children
  })
}

function SegmentedChoice({ selected, disabled, onClick, children, className = '' }) {
  return jsxs('button', {
    type: 'button',
    disabled,
    onClick,
    'aria-pressed': selected,
    className: `inline-flex min-w-0 items-center gap-2 rounded-md border px-3 py-1.5 text-left text-xs font-medium ${CONTROL_TRANSITION} focus-visible:outline-2 focus-visible:outline-(--ui-accent) disabled:pointer-events-none disabled:opacity-50 ${
      selected
        ? 'border-(--ui-accent) bg-(--ui-bg-primary) text-(--ui-text-primary) ring-1 ring-(--ui-accent) shadow-sm'
        : 'border-transparent text-(--ui-text-secondary) hover:border-(--ui-stroke-secondary) hover:bg-(--chrome-action-hover) hover:text-(--ui-text-primary)'
    } ${className}`,
    children: [
      jsx('span', {
        'aria-hidden': 'true',
        className: `size-1.5 shrink-0 rounded-full bg-(--ui-accent) ${selected ? 'opacity-100' : 'opacity-0'}`
      }),
      jsx('span', { className: 'min-w-0', children })
    ]
  })
}

function ActionButton({ children, tone = 'default', className = '', ...props }) {
  const toneClass =
    tone === 'danger'
      ? 'text-(--ui-danger,#f87171) hover:border-(--ui-danger,#f87171)'
      : tone === 'primary'
        ? 'border-(--ui-accent) bg-(--chrome-action-hover) text-(--ui-text-primary)'
        : 'text-(--ui-text-secondary)'
  return jsx('button', {
    type: 'button',
    ...props,
    className: `inline-flex items-center justify-center rounded-md border border-(--ui-stroke-secondary) px-2.5 py-1.5 text-xs font-medium ${CONTROL_TRANSITION} ${toneClass} hover:bg-(--chrome-action-hover) active:scale-95 disabled:pointer-events-none disabled:opacity-50 ${className}`,
    children
  })
}

function DecisionResults({ report, records, openPendingResult, decide, busy }) {
  if (!report) return null
  const result = normalizeDecisionResult(report)
  return jsxs('section', {
    className: 'shrink-0 border-b border-(--ui-stroke-secondary) px-4 py-3 text-xs',
    'aria-label': 'Decision results',
    children: [
      jsxs('div', {
        className: 'flex flex-wrap items-center gap-x-4 gap-y-2',
        children: [
          jsx('span', {
            className: 'font-medium',
            children: result.outcome === 'complete' ? 'Decision completed' : result.outcome === 'partial' ? 'Partial decision' : 'Decision failed'
          }),
          jsx('span', { children: `${result.applied} applied` }),
          jsx('span', { children: `${result.rejected} rejected` }),
          jsx('span', { children: `${result.failed} failed` }),
          result.reconciliation
            ? jsx('span', { className: 'text-(--ui-danger,#f87171)', children: `${result.reconciliation} need reconciliation` })
            : null
        ]
      }),
      result.items.length
        ? jsxs('details', {
            className: 'mt-2',
            children: [
              jsx('summary', {
                className: 'cursor-pointer font-medium focus-visible:outline-2 focus-visible:outline-(--ui-accent)',
                children: `Review ${result.items.length} individual result(s)`
              }),
              jsx('ul', {
                className: 'mt-2 space-y-2',
                children: result.items.map((item, index) => {
                  const exists = records.some(record => record.id === item.id)
                  return jsxs('li', {
                    className: 'flex flex-wrap items-center gap-x-3 gap-y-2 border-l-2 border-(--ui-stroke-secondary) pl-3',
                    children: [
                      jsx('span', { className: 'font-mono font-medium', children: item.id }),
                      jsx('span', { className: item.status === 'failed' || item.status === 'applied_cleanup_failed' ? 'text-(--ui-danger,#f87171)' : 'text-(--ui-text-tertiary)', children: item.status }),
                      item.error ? jsx('span', { className: 'basis-full text-(--ui-text-tertiary)', children: item.error }) : null,
                      exists ? jsx(ActionButton, {
                        disabled: busy,
                        onClick: () => openPendingResult(item.id),
                        children: 'Open pending record'
                      }) : null,
                      canRetryDecisionItem(item, result, records)
                        ? jsx(ActionButton, {
                            disabled: busy,
                            onClick: () => decide('reject', item.id),
                            children: 'Retry rejection'
                          })
                        : null
                    ]
                  }, `${item.id}:${index}`)
                })
              })
            ]
          })
        : null
    ]
  })
}

function SearchBox({ value, onChange, placeholder, ariaLabel }) {
  return jsx('input', {
    type: 'search',
    value,
    onChange,
    placeholder,
    'aria-label': ariaLabel,
    className: `w-full rounded-md border border-(--ui-stroke-secondary) bg-transparent px-3 py-2 text-sm outline-none ${PANEL_TRANSITION} placeholder:text-(--ui-text-quaternary) focus:border-(--ui-accent) focus:bg-(--chrome-action-hover)`
  })
}

function StatCard({ label, value, detail, tone = 'default' }) {
  return jsxs('div', {
    className: `min-w-0 rounded-lg border border-(--ui-stroke-secondary) bg-(--chrome-action-hover) px-3 py-2 ${PANEL_TRANSITION}`,
    children: [
      jsxs('div', {
        className: 'flex items-center gap-2',
        children: [
          jsx(StatusDot, { tone }),
          jsx('span', { className: 'text-[0.6875rem] font-medium uppercase tracking-wide text-(--ui-text-tertiary)', children: label })
        ]
      }),
      jsx('div', { className: 'mt-1 text-base font-semibold tabular-nums', children: String(value ?? 0) }),
      detail ? jsx('div', { className: 'mt-0.5 truncate text-[0.6875rem] text-(--ui-text-quaternary)', children: detail }) : null
    ]
  })
}

function CompactStat({ label, value, detail }) {
  return jsxs('div', {
    className: 'min-w-0',
    children: [
      jsx('div', { className: 'truncate text-[0.625rem] font-medium uppercase tracking-wide text-(--ui-text-tertiary)', children: label }),
      jsxs('div', {
        className: 'mt-0.5 flex min-w-0 items-baseline gap-1.5',
        children: [
          jsx('span', { className: 'shrink-0 text-sm font-semibold tabular-nums', children: String(value ?? 0) }),
          detail ? jsx('span', { className: 'min-w-0 truncate text-[0.625rem] text-(--ui-text-quaternary)', children: detail }) : null
        ]
      })
    ]
  })
}

function EmptyState({ title, description, action }) {
  return jsxs('div', {
    className: 'flex min-h-40 flex-col items-center justify-center px-6 py-10 text-center',
    children: [
      jsx('div', {
        className: 'mb-3 flex size-9 items-center justify-center rounded-full border border-(--ui-stroke-secondary) bg-(--chrome-action-hover) text-sm text-(--ui-text-tertiary)',
        children: '·'
      }),
      jsx('div', { className: 'text-sm font-medium', children: title }),
      description ? jsx('div', { className: 'mt-1 max-w-sm text-xs leading-relaxed text-(--ui-text-tertiary)', children: description }) : null,
      action ? jsx('div', { className: 'mt-3', children: action }) : null
    ]
  })
}

function LoadingRows() {
  return jsx('div', {
    className: 'space-y-2 p-3',
    children: [0, 1, 2].map(index =>
      jsxs('div', {
        className: 'animate-pulse rounded-lg border border-(--ui-stroke-secondary) p-3',
        children: [
          jsx('div', { className: 'h-2.5 w-2/5 rounded bg-(--chrome-action-hover)' }),
          jsx('div', { className: 'mt-2 h-2 w-4/5 rounded bg-(--chrome-action-hover)' })
        ]
      }, `loading:${index}`)
    )
  })
}

function UsageBar({ percent = 0 }) {
  const bounded = Math.max(0, Math.min(100, Number(percent) || 0))
  return jsx('div', {
    className: 'h-1.5 overflow-hidden rounded-full bg-(--chrome-action-hover)',
    role: 'progressbar',
    'aria-label': 'Character budget used',
    'aria-valuemin': 0,
    'aria-valuemax': 100,
    'aria-valuenow': bounded,
    children: jsx('div', {
      className: 'h-full rounded-full bg-(--ui-accent) transition-[width] duration-500 ease-out motion-reduce:transition-none',
      style: { width: `${bounded}%` }
    })
  })
}

function MetaField({ label, value }) {
  return jsxs('div', {
    className: `min-w-0 rounded-lg border border-(--ui-stroke-secondary) bg-(--chrome-action-hover) p-3 ${PANEL_TRANSITION}`,
    children: [
      jsx('div', { className: 'text-[0.6875rem] uppercase tracking-wide text-(--ui-text-tertiary)', children: label }),
      jsx('div', { className: 'mt-1.5 break-words text-sm font-medium', children: value || '—' })
    ]
  })
}

function ContentBlock({ label, text, side }) {
  if (!text) return null
  return jsxs('section', {
    className: 'mt-3 min-w-0',
    'data-change-side': side,
    children: [
      jsx('h4', { className: 'mb-1 text-xs font-medium text-(--ui-text-tertiary)', children: label }),
      jsx('div', {
        className: `h-full whitespace-pre-wrap break-words rounded-lg border border-(--ui-stroke-secondary) bg-(--chrome-action-hover) p-3 text-sm leading-relaxed ${side === 'after' ? 'border-l-2 border-l-(--ui-accent)' : ''}`,
        children: String(text)
      })
    ]
  })
}

function OperationCard({ op, index, total }) {
  const action = String(op?.action || 'unknown')
  const title = total > 1 ? `Operation ${index + 1} · ${action}` : action
  const before = op?.matched_entry || op?.old_text || ''
  const content = op?.content || ''
  const tone = action === 'remove' ? 'danger' : action === 'add' ? 'accent' : 'default'

  return jsxs('section', {
    className: `rounded-lg border border-(--ui-stroke-secondary) p-3.5 ${PANEL_TRANSITION}`,
    children: [
      jsxs('div', {
        className: 'flex items-center gap-2',
        children: [
          jsx('h3', { className: 'text-sm font-semibold capitalize', children: title }),
          jsx(Badge, { tone, children: action })
        ]
      }),
      action === 'add' ? jsx(ContentBlock, { label: 'Content to add', text: content }) : null,
      action === 'replace'
        ? jsx('div', {
            className: 'grid gap-3 xl:grid-cols-2',
            children: [
              jsx(ContentBlock, { label: 'Before · current entry', text: before || 'Original entry unavailable in this proposal', side: 'before' }, 'before'),
              jsx(ContentBlock, { label: 'After · proposed replacement', text: content || 'Replacement content unavailable in this proposal', side: 'after' }, 'after')
            ]
          })
        : null,
      action === 'remove' ? jsx(ContentBlock, { label: 'Entry to remove', text: before }) : null,
      !['add', 'replace', 'remove'].includes(action)
        ? jsx('pre', {
            className: 'mt-3 overflow-auto whitespace-pre-wrap break-words rounded border border-(--ui-stroke-secondary) p-3 font-mono text-xs',
            children: JSON.stringify(op, null, 2)
          })
        : null
    ]
  })
}

function OverviewView({ detail }) {
  const record = detail.record || {}
  const payload = detail.payload || {}
  const operations = payload.action === 'batch' && Array.isArray(payload.operations) ? payload.operations : [payload]
  const status = record.target_status || {}

  return jsxs('div', {
    className: `${RESIZABLE_ENTRY_VIEWER} m-3 p-4 animate-in fade-in-0 duration-150`,
    'data-selectable-text': 'true',
    'data-resizable-entry-viewer': 'true',
    title: 'Drag the lower edge or corner to resize this entry viewer.',
    children: [
      jsxs('section', {
        className: 'mb-4 rounded-lg border border-(--ui-stroke-secondary) bg-(--chrome-action-hover) p-4',
        children: [
          jsxs('div', {
            className: 'flex flex-wrap items-center gap-2',
            children: [
              jsx('h2', { className: 'mr-auto text-sm font-semibold', children: record.summary || 'Pending memory change' }),
              jsx(Badge, {
                tone: status.can_apply === false ? 'danger' : 'accent',
                children: status.state === 'missing' ? 'obsolete' : status.can_apply === false ? 'unverifiable' : 'ready'
              })
            ]
          }),
          jsx('p', {
            className: 'mt-2 text-xs leading-relaxed text-(--ui-text-tertiary)',
            children: status.can_apply === false
              ? (status.message || 'This proposal cannot be verified safely.')
              : 'Review the proposed change below, then approve or reject it from the action bar.'
          })
        ]
      }),
      jsx('div', {
        className: 'grid gap-2 sm:grid-cols-2 xl:grid-cols-4',
        children: [
          jsx(MetaField, { label: 'Action', value: record.action }, 'action'),
          jsx(MetaField, { label: 'Target', value: record.target }, 'target'),
          jsx(MetaField, { label: 'Origin', value: record.origin }, 'origin'),
          jsx(MetaField, { label: 'Created', value: formatCreated(record.created_at) }, 'created')
        ]
      }),
      jsxs('section', {
        className: 'mt-4 rounded-lg border border-(--ui-stroke-secondary) p-3.5',
        children: [
          jsx('h3', { className: 'mb-1 text-[0.6875rem] font-medium uppercase tracking-wide text-(--ui-text-tertiary)', children: 'Why this was proposed' }),
          jsx('div', { className: 'text-sm leading-relaxed', children: record.summary || '(no summary provided)' })
        ]
      }),
      jsx('div', {
        className: 'mt-4 grid gap-3',
        children: operations.map((op, index) => jsx(OperationCard, { op, index, total: operations.length }, index))
      })
    ]
  })
}

function PendingWritesPage({
  loadRecords, loadDetail, runDecision, source, onOpenStoredMemory,
  decisionReport, setDecisionReport, feedback, setFeedback, bulkConfirm, setBulkConfirm
}) {
  const [search, setSearch] = useState('')
  const [filters, setFilters] = useState({ ...EMPTY_PENDING_FILTERS })
  const [selected, setSelected] = useState('')
  const [view, setView] = useState('overview')
  const [busyAction, setBusyAction] = useState('')

  const listing = useQuery({
    queryKey: [source, 'records'],
    queryFn: loadRecords,
    refetchInterval: 5_000
  })

  const records = listing.data?.records || []
  const missingTargetCount = records.filter(record => record.target_status?.state === 'missing').length
  const blockedApprovalCount = records.filter(record => record.target_status?.can_apply === false).length
  const readyCount = records.filter(record => record.target_status?.can_apply !== false).length
  const query = search.trim().toLocaleLowerCase()
  const hasFilters = Boolean(query || Object.values(filters).some(Boolean))
  const origins = [...new Set(records.map(record => String(record.origin || 'unknown')))].sort()
  const filtered = useMemo(
    () => filterPendingRecords(records, { ...filters, query }),
    [records, filters, query]
  )
  const clearFilters = () => {
    setSearch('')
    setFilters({ ...EMPTY_PENDING_FILTERS })
  }
  const openPendingResult = id => {
    if (!records.some(record => record.id === id)) return
    clearFilters()
    setSelected(id)
    setView('overview')
  }
  const selectedVisible = filtered.some(record => record.id === selected)
  const currentId = selectedVisible ? selected : filtered[0]?.id || ''
  const currentRecord = records.find(record => record.id === currentId) || null

  const detail = useQuery({
    queryKey: [source, 'record', currentId],
    queryFn: () => loadDetail(currentId),
    enabled: Boolean(currentId),
    refetchInterval: 5_000
  })

  const refresh = () => {
    listing.refetch()
    if (currentId) detail.refetch()
  }

  const decide = async (action, target) => {
    const key = `${action}:${target}`
    setBusyAction(key)
    setFeedback(null)
    try {
      const result = await runDecision(action, target)
      const outcome = result?.outcome || (result?.success ? 'complete' : 'failed')
      const normalized = normalizeDecisionResult({ ...result, action })
      setDecisionReport(normalized)
      const outcomeFeedback = {
        complete: { kind: 'success' },
        partial: { kind: 'warning' },
        failed: { kind: 'error' }
      }
      setFeedback({
        ...(outcomeFeedback[outcome] || outcomeFeedback.failed),
        message: outcome === 'complete'
          ? (result?.output || `${action} completed.`)
          : `${normalized.failed} operation(s) need attention. Review the individual results below.`
      })
      if (outcome === 'complete') setSelected('')
      setBulkConfirm('')
      await listing.refetch()
    } catch (error) {
      setFeedback({ kind: 'error', message: 'The decision could not be completed. Refresh the queue and inspect the pending record.' })
    } finally {
      setBusyAction('')
    }
  }

  if (listing.isError) {
    return jsxs('div', {
      className: 'flex h-full flex-col items-center justify-center gap-3 p-8 text-center',
      children: [
        jsx('div', { className: 'text-base font-medium', children: 'Magi backend unavailable' }),
        jsx('div', {
          className: 'max-w-lg text-sm text-(--ui-text-tertiary)',
          children: 'Enable the Agent half of Magi for this profile, then retry.'
        }),
        jsx('button', {
          type: 'button',
          className: 'rounded border border-(--ui-stroke-secondary) px-3 py-1.5 text-sm hover:bg-(--chrome-action-hover)',
          onClick: refresh,
          children: 'Retry'
        })
      ]
    })
  }

  return jsxs('div', {
    className: 'flex h-full min-h-0 flex-col',
    children: [
      jsxs('header', {
        className: 'shrink-0 border-b border-(--ui-stroke-secondary) px-4 py-4',
        children: [
          jsxs('div', {
            className: 'flex flex-wrap items-start gap-3',
            children: [
              jsxs('div', {
                className: 'min-w-0 flex-1',
                children: [
                  jsxs('div', {
                    className: 'flex flex-wrap items-center gap-2',
                    children: [
                      jsx('h1', { className: 'text-base font-semibold', children: 'Pending review' }),
                      listing.isFetching
                        ? jsx(Badge, { children: jsxs('span', { className: 'flex items-center gap-1.5', children: [jsx(StatusDot, { tone: 'accent' }), 'syncing'] }) })
                        : null
                    ]
                  }),
                  jsx('p', {
                    className: 'mt-0.5 text-xs leading-relaxed text-(--ui-text-tertiary)',
                    children: 'Compare each proposed change with the existing memory, then approve or reject it.'
                  })
                ]
              }),
              jsx(ActionButton, { onClick: refresh, disabled: listing.isFetching, children: listing.isFetching ? 'Refreshing…' : 'Refresh' })
            ]
          }),
          jsx('div', {
            'aria-label': 'Pending queue summary',
            className: 'mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-xs',
            children: [
              jsxs('span', { className: 'font-medium tabular-nums', children: [listing.data?.count ?? 0, ' pending'] }),
              jsxs('span', { className: 'flex items-center gap-1.5 text-(--ui-text-tertiary)', children: [jsx(StatusDot, { tone: readyCount ? 'accent' : 'default' }), `${readyCount} ready to approve`] }),
              jsxs('span', { className: `flex items-center gap-1.5 ${blockedApprovalCount ? 'text-(--ui-danger,#f87171)' : 'text-(--ui-text-tertiary)'}`, children: [jsx(StatusDot, { tone: blockedApprovalCount ? 'danger' : 'default' }), `${blockedApprovalCount} need attention${missingTargetCount ? ` · ${missingTargetCount} obsolete` : ''}`] })
            ]
          })
        ]
      }),
      records.length
        ? bulkConfirm
          ? jsxs('div', {
              className: 'flex shrink-0 flex-wrap items-center gap-2 border-b border-(--ui-stroke-secondary) bg-(--chrome-action-hover) px-4 py-2.5 text-xs animate-in fade-in-0 duration-150',
              children: [
                jsx('span', {
                  className: 'mr-auto font-medium',
                  children: `${bulkConfirm === 'approve' ? 'Approve' : 'Reject'} all ${records.length} pending write(s) in the whole queue, including filtered-out proposals?`
                }),
                jsx(ActionButton, {
                  tone: bulkConfirm === 'reject' ? 'danger' : 'primary',
                  disabled: Boolean(busyAction),
                  onClick: () => decide(bulkConfirm, 'all'),
                  children: busyAction ? 'Working…' : `Confirm ${bulkConfirm} all`
                }),
                jsx(ActionButton, {
                  disabled: Boolean(busyAction),
                  onClick: () => setBulkConfirm(''),
                  children: 'Cancel'
                })
              ]
            })
          : jsxs('div', {
              className: 'flex shrink-0 flex-wrap items-center gap-2 border-b border-(--ui-stroke-secondary) px-4 py-2',
              children: [
                jsx('span', { className: 'mr-auto text-xs text-(--ui-text-tertiary)', children: 'Bulk actions affect the whole queue, including proposals hidden by filters.' }),
                jsx(ActionButton, {
                  tone: 'primary',
                  disabled: Boolean(busyAction),
                  title: blockedApprovalCount ? 'Valid proposals will be attempted; blocked proposals will be reported individually.' : undefined,
                  onClick: () => setBulkConfirm('approve'),
                  children: 'Approve all'
                }),
                jsx(ActionButton, {
                  tone: 'danger',
                  disabled: Boolean(busyAction),
                  onClick: () => setBulkConfirm('reject'),
                  children: 'Reject all'
                })
              ]
            })
        : null,
      decisionReport
        ? jsx(DecisionResults, {
            report: decisionReport,
            records,
            busy: Boolean(busyAction),
            openPendingResult,
            decide
          })
        : null,
      records.length === 0 && !listing.isLoading
        ? jsx(EmptyState, {
            title: 'Nothing to review',
            description: 'New memory proposals will appear here. You can inspect and edit the durable entries in Stored memory.',
            action: jsx(ActionButton, { onClick: onOpenStoredMemory, children: 'Stored memory' })
          })
        : jsxs('div', {
        className: 'grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[minmax(15rem,22rem)_1fr]',
        children: [
          jsxs('aside', {
            className: 'flex min-h-0 flex-col border-b border-(--ui-stroke-secondary) md:border-b-0 md:border-r',
            children: [
              jsxs('div', {
                className: 'p-3',
                children: [
                  jsx(SearchBox, {
                    value: search,
                    onChange: event => setSearch(event.target.value),
                    placeholder: 'Search by id, action, target, or summary',
                    ariaLabel: 'Search pending memory'
                  }),
                  jsx('div', {
                    className: 'mt-2 grid grid-cols-2 gap-2',
                    children: [
                      [['action', 'Pending action filter', [['', 'All actions'], ['add', 'Add'], ['replace', 'Replace'], ['remove', 'Remove'], ['batch', 'Batch']]],
                        ['target', 'Pending target filter', [['', 'Both stores'], ['memory', 'Memory'], ['user', 'User']]],
                        ['origin', 'Pending origin filter', [['', 'All origins'], ...origins.map(value => [value, value === 'background_review' ? 'Automatic review' : value === 'foreground' ? 'Foreground' : value])]],
                        ['state', 'Pending status filter', [['', 'All statuses'], ['ready', 'Ready'], ['missing', 'Missing'], ['unverifiable', 'Unverifiable'], ['ambiguous', 'Already stored / ambiguous'], ['reconciliation_required', 'Reconciliation required'], ['unknown', 'Unknown']]]].map(([key, label, options]) =>
                        jsx('select', {
                          'aria-label': label,
                          value: filters[key],
                          onChange: event => setFilters(previous => ({ ...previous, [key]: event.target.value })),
                          className: 'min-w-0 rounded-md border border-(--ui-stroke-secondary) bg-(--ui-bg-primary) px-2 py-1.5 text-xs',
                          children: options.map(([value, text]) => jsx('option', { value, children: text }, value))
                        }, key)
                      ),
                      [['fromDate', 'Pending date from'], ['toDate', 'Pending date to']].map(([key, label]) =>
                        jsxs('label', {
                          className: 'min-w-0 text-[0.6875rem] text-(--ui-text-tertiary)',
                          children: [
                            jsx('span', { children: key === 'fromDate' ? 'From (UTC)' : 'To (UTC)' }),
                            jsx('input', {
                              type: 'date',
                              'aria-label': label,
                              value: filters[key],
                              onChange: event => setFilters(previous => ({ ...previous, [key]: event.target.value })),
                              className: 'mt-1 block w-full min-w-0 rounded-md border border-(--ui-stroke-secondary) bg-(--ui-bg-primary) px-2 py-1.5 text-xs'
                            })
                          ]
                        }, key)
                      )
                    ]
                  }),
                  jsx('div', {
                    className: 'mt-2 flex items-center justify-between text-[0.6875rem] text-(--ui-text-quaternary)',
                    children: [
                      jsx('span', { 'aria-live': 'polite', children: `${filtered.length} of ${records.length} shown` }),
                      hasFilters ? jsx('button', { type: 'button', onClick: clearFilters, className: 'hover:text-(--ui-text-primary)', children: 'Clear filters' }) : null
                    ]
                  })
                ]
              }),
              listing.isLoading
                ? jsx(LoadingRows, {})
                : filtered.length === 0
                  ? jsx(EmptyState, {
                      title: records.length ? 'No matching proposals' : 'Queue is clear',
                      description: records.length ? 'Try broader criteria or clear filters to see the whole queue.' : 'There are no pending memory changes to review.',
                      action: hasFilters ? jsx(ActionButton, { onClick: clearFilters, children: 'Clear filters' }) : null
                    })
                  : jsx('div', {
                      className: 'min-h-0 flex-1 overflow-auto px-2 pb-2',
                      children: filtered.map(record =>
                        jsxs('button', {
                          type: 'button',
                          onClick: () => setSelected(record.id),
                          'aria-current': currentId === record.id ? 'true' : undefined,
                          className: `mb-1.5 block w-full rounded-lg border px-3 py-2.5 text-left ${PANEL_TRANSITION} ${
                            currentId === record.id
                              ? 'border-(--ui-accent) bg-(--chrome-action-hover)'
                              : 'border-transparent hover:border-(--ui-stroke-secondary) hover:bg-(--chrome-action-hover)'
                          }`,
                          children: [
                            jsxs('div', {
                              className: 'flex items-center gap-2',
                              children: [
                                jsx(StatusDot, { tone: record.target_status?.can_apply === false ? 'danger' : 'accent' }),
                                jsx('span', { className: 'truncate text-xs font-semibold', children: record.id }),
                                jsx(Badge, { tone: record.action === 'remove' ? 'danger' : record.action === 'add' ? 'accent' : 'default', children: recordLabel(record) })
                              ]
                            }),
                            jsx('div', {
                              className: 'mt-1.5 line-clamp-2 text-xs leading-relaxed text-(--ui-text-tertiary)',
                              children: record.summary || '(no summary)'
                            }),
                            jsxs('div', {
                              className: 'mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[0.6875rem] text-(--ui-text-quaternary)',
                              children: [
                                jsx('span', { children: record.origin === 'background_review' ? 'automatic review' : 'foreground' }),
                                jsx('span', { children: formatCreated(record.created_at) }),
                                record.target_status?.state === 'missing'
                                  ? jsx('span', { className: 'font-medium text-(--ui-danger,#f87171)', children: 'target missing · obsolete proposal' })
                                  : record.target_status?.can_apply === false
                                    ? jsx('span', { className: 'font-medium text-(--ui-danger,#f87171)', children: 'approval unavailable' })
                                    : jsx('span', { children: 'ready to review' })
                              ]
                            })
                          ]
                        }, record.id)
                      )
                    })
            ]
          }),
          filtered.length ? jsxs('main', {
            className: 'flex min-h-0 min-w-0 flex-col overflow-auto',
            children: [
              currentId
                ? jsxs('div', {
                    className: 'shrink-0 border-b border-(--ui-stroke-secondary)',
                    children: [
                      jsxs('div', {
                        className: 'flex flex-wrap items-center gap-3 px-4 py-3',
                        children: [
                          jsxs('div', {
                            className: 'min-w-0 flex-1',
                            children: [
                              jsxs('div', {
                                className: 'flex flex-wrap items-center gap-2',
                                children: [
                                  jsx('span', { className: 'truncate text-sm font-semibold', children: currentRecord?.id || currentId }),
                                  currentRecord ? jsx(Badge, { tone: currentRecord.target_status?.can_apply === false ? 'danger' : 'accent', children: currentRecord.target_status?.can_apply === false ? 'needs attention' : 'ready' }) : null
                                ]
                              }),
                              jsx('div', { className: 'mt-0.5 truncate text-xs text-(--ui-text-tertiary)', children: currentRecord?.summary || 'Loading proposal details…' })
                            ]
                          }),
                          jsx(ActionButton, {
                            tone: 'primary',
                            disabled:
                              Boolean(busyAction) ||
                              detail.isLoading ||
                              !detail.data ||
                              detail.data.record?.target_status?.can_apply === false,
                            onClick: () => decide('approve', currentId),
                            children:
                              detail.data?.record?.target_status?.can_apply === false
                                ? 'Cannot approve'
                                : busyAction === `approve:${currentId}`
                                  ? 'Approving…'
                                  : 'Approve'
                          }, 'approve'),
                          jsx(ActionButton, {
                            tone: 'danger',
                            disabled: Boolean(busyAction),
                            onClick: () => decide('reject', currentId),
                            children:
                              busyAction === `reject:${currentId}`
                                ? detail.data?.record?.target_status?.state === 'missing' ? 'Deleting…' : 'Rejecting…'
                                : detail.data?.record?.target_status?.state === 'missing' ? 'Delete obsolete' : 'Reject'
                          }, 'reject')
                        ]
                      }),
                      jsx('div', {
                        className: 'flex gap-1 overflow-x-auto px-3 pb-2',
                        children: VIEWS.map(([id, label]) =>
                          jsx('button', {
                            type: 'button',
                            onClick: () => setView(id),
                            'aria-pressed': view === id,
                            className: `shrink-0 rounded-md px-2.5 py-1.5 text-xs ${CONTROL_TRANSITION} ${
                              view === id
                                ? 'bg-(--chrome-action-hover) font-medium text-(--ui-text-primary)'
                                : 'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-(--ui-text-primary)'
                            }`,
                            children: label
                          }, id)
                        )
                      })
                    ]
                  })
                : null,
              feedback
                ? jsx('div', {
                    role: 'status',
                    'aria-live': 'polite',
                    className: `border-b border-(--ui-stroke-secondary) bg-(--chrome-action-hover) px-4 py-2.5 text-xs animate-in fade-in-0 duration-150 ${
                      feedback.kind === 'error'
                        ? 'text-(--ui-danger,#f87171)'
                        : feedback.kind === 'warning'
                          ? 'text-(--ui-warning,#fbbf24)'
                          : 'text-(--ui-text-tertiary)'
                    }`,
                    children: feedback.message
                  })
                : null,
              detail.data?.record?.target_status?.state === 'missing'
                ? jsx('div', {
                    className: 'border-b border-(--ui-stroke-secondary) px-4 py-2 text-xs text-(--ui-danger,#f87171)',
                    children:
                      detail.data.record.target_status.message ||
                      'Target entry no longer exists. Reject this obsolete proposal to delete it.'
                  })
                : detail.data?.record?.target_status?.can_apply === false
                  ? jsx('div', {
                      className: 'border-b border-(--ui-stroke-secondary) px-4 py-2 text-xs text-(--ui-danger,#f87171)',
                      children: detail.data.record.target_status.message
                    })
                  : null,
              detail.isError
                ? jsx(EmptyState, {
                    title: 'Could not load this proposal',
                    description: 'It may have been resolved since the last refresh. Refresh the queue to reconcile the view.',
                    action: jsx(ActionButton, { onClick: refresh, children: 'Refresh queue' })
                  })
                : detail.isLoading && currentId
                  ? jsx(LoadingRows, {})
                  : currentId && detail.data
                    ? view === 'overview'
                      ? jsx(OverviewView, { detail: detail.data })
                      : jsx('pre', {
                          className: `${RESIZABLE_ENTRY_VIEWER} m-3 whitespace-pre-wrap break-words rounded-lg border border-(--ui-stroke-secondary) bg-(--chrome-action-hover) p-4 font-mono text-xs leading-relaxed animate-in fade-in-0 duration-150`,
                          'data-selectable-text': 'true',
                          'data-resizable-entry-viewer': 'true',
                          title: 'Drag the lower edge or corner to resize this entry viewer.',
                          children: detail.data[view] || ''
                        })
                    : jsx(EmptyState, {
                        title: 'Select a proposal',
                        description: 'Choose a pending write from the queue to inspect its operations and decide what should happen.'
                      })
            ]
          }) : null
        ]
      }),
      listing.data?.issues?.length
        ? jsx('div', {
            className: 'border-t border-(--ui-stroke-secondary) px-4 py-2 text-xs text-(--ui-text-tertiary)',
            children: `${listing.data.issues.length} malformed pending file(s) were skipped.`
          })
        : null
    ]
  })
}

function MemoryDiagnostics({ diagnostics, targetLabel, onSelectEntry }) {
  if (!diagnostics) return null
  const longIndices = diagnostics.long_entry_indices || []
  const duplicateGroups = diagnostics.exact_duplicate_groups || []
  const duplicateIndices = duplicateGroups.flat()
  const flaggedIndices = [...new Set([...longIndices, ...duplicateIndices])].sort((a, b) => a - b)
  return jsxs('details', {
    className: 'shrink-0 border-b border-(--ui-stroke-secondary) px-4 py-2 text-xs',
    children: [
      jsxs('summary', {
        className: 'cursor-pointer font-medium focus-visible:outline-2 focus-visible:outline-(--ui-accent)',
        children: ['Memory diagnostics · ', targetLabel, ' · ', longIndices.length, ' long · ', duplicateGroups.length, ' Exact duplicate groups']
      }),
      jsxs('div', {
        className: 'mt-2 space-y-2 text-(--ui-text-tertiary)',
        children: [
          jsx('p', {
            children: 'Read-only, informational text heuristics. Exact duplicates mean identical text, not semantic redundancy; length does not establish quality or contradictions. No AI is used.'
          }),
          jsx('p', {
            children: `${diagnostics.entry_count} entries · ${diagnostics.used_chars} chars used · ${diagnostics.remaining_chars} remaining${diagnostics.over_budget_chars ? ` · ${diagnostics.over_budget_chars} above budget` : ''}`
          }),
          jsx('p', {
            children: `Entry sizes (characters): ${Object.entries(diagnostics.size_distribution || {}).map(([range, count]) => `${range}: ${count}`).join(' · ')}. Long entries exceed ${diagnostics.long_entry_threshold_chars} characters.`
          }),
          jsx('p', { children: duplicateGroups.length ? `Exact duplicate entry groups: ${duplicateGroups.map(group => group.map(index => index + 1).join(', ')).join(' · ')}` : 'No exact duplicate texts found.' }),
          flaggedIndices.length
            ? jsx('div', {
                className: 'flex flex-wrap gap-2',
                children: flaggedIndices.map(index =>
                  jsx(ActionButton, {
                    onClick: () => onSelectEntry(index),
                    children: `View entry ${index + 1} · ${longIndices.includes(index) ? 'long' : ''}${longIndices.includes(index) && duplicateIndices.includes(index) ? ', ' : ''}${duplicateIndices.includes(index) ? 'exact duplicate' : ''}`
                  }, index)
                )
              })
            : jsx('p', { children: 'No long or exact duplicate entries to inspect.' })
        ]
      })
    ]
  })
}

function StoredMemoryPage({ loadStoredMemory, saveStoredMemory, addStoredMemory, deleteStoredMemory, requestCompaction, applyCompaction, previewMemoryImport, applyMemoryImport, analyzeOrganization, requestSemanticReview, source, navigationGuard }) {
  const [target, setTarget] = useState('memory')
  const [search, setSearch] = useState('')
  const [selectedText, setSelectedText] = useState('')
  const [draftSlots, setDraftSlots] = useState({})
  const [pendingNavigation, setPendingNavigation] = useState(null)
  const [adding, setAdding] = useState(false)
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [deleteConfirm, setDeleteConfirm] = useState(false)
  const [compacting, setCompacting] = useState(false)
  const [compactionElapsed, setCompactionElapsed] = useState(0)
  const compactionRequestId = useRef(0)
  const [applyingCompaction, setApplyingCompaction] = useState(false)
  const [compactionPreview, setCompactionPreview] = useState(null)
  const [previewView, setPreviewView] = useState('entries')
  const [proposalEntries, setProposalEntries] = useState([])
  const [proposalEditing, setProposalEditing] = useState(false)
  const [confirmEdited, setConfirmEdited] = useState(false)
  const [organizationAnalysis, setOrganizationAnalysis] = useState(null)
  const [analyzing, setAnalyzing] = useState(false)
  const [transferPreview, setTransferPreview] = useState(null)
  const [importing, setImporting] = useState(false)
  const [reviewConfirm, setReviewConfirm] = useState(false)
  const [reviewingPreservation, setReviewingPreservation] = useState(false)
  const [semanticReport, setSemanticReport] = useState(null)
  const reviewRequestId = useRef(0)
  const [feedback, setFeedback] = useState(null)

  const stored = useQuery({
    queryKey: [source, 'stored-memory'],
    queryFn: loadStoredMemory,
    refetchInterval: 5_000
  })

  const targetData = stored.data?.targets?.[target] || { count: 0, entries: [] }
  const entries = targetData.entries || []
  const query = search.trim().toLocaleLowerCase()
  const filtered = useMemo(
    () =>
      query
        ? entries.filter(entry => String(entry.content || '').toLocaleLowerCase().includes(query))
        : entries,
    [entries, query]
  )
  const selectedEntry = selectedText ? entries.find(entry => entry.content === selectedText) : null
  const current = adding
    ? null
    : selectedText
      ? selectedEntry || { index: -1, content: selectedText, stale: true }
      : filtered[0] || entries[0] || null
  const draftKey = `${target}:${adding ? 'new' : (current?.content || 'none')}`
  const activeDraft = resolveStoredDraft(draftSlots, draftKey, adding ? '' : (current?.content || ''), targetData.source_fingerprint)
  const draft = activeDraft.text
  const isDirty = activeDraft.dirty
  const draftConflict = isDirty && Boolean(current?.stale || activeDraft.conflict)
  const setDraft = text => {
    const base = adding ? '' : (current?.content || '')
    setDraftSlots(previous => ({ ...previous, [draftKey]: {
      base: previous[draftKey]?.base ?? base,
      sourceFingerprint: previous[draftKey]?.sourceFingerprint ?? targetData.source_fingerprint,
      text
    } }))
    // Pin the implicit first selection so the draft survives list reordering.
    if (!adding && !selectedText && current?.content) setSelectedText(current.content)
  }
  const targetLabel = target === 'memory' ? 'MEMORY.md' : 'USER.md'
  const previewDiff = useMemo(
    () => compactionPreview ? buildCompactionDiff(compactionPreview.source_entries || [], proposalEntries) : [],
    [compactionPreview, proposalEntries]
  )
  const proposalValidation = useMemo(
    () => compactionPreview ? validateCompactionEdits(compactionPreview.source_entries || [], proposalEntries) : null,
    [compactionPreview, proposalEntries]
  )
  const manuallyEdited = Boolean(compactionPreview && JSON.stringify(proposalEntries) !== JSON.stringify(compactionPreview.proposed_entries))

  const changeProposalEntries = next => {
    setProposalEntries(next)
    setConfirmEdited(false)
    reviewRequestId.current += 1
    setSemanticReport(null)
    setReviewConfirm(false)
  }

  const splitProposalEntry = index => {
    const text = proposalEntries[index]
    const newline = text.indexOf('\n')
    const at = newline > 0 ? newline : text.lastIndexOf(' ', Math.floor(text.length / 2))
    if (at <= 0 || at >= text.length - 1) return
    changeProposalEntries([...proposalEntries.slice(0, index), text.slice(0, at).trim(), text.slice(at + 1).trim(), ...proposalEntries.slice(index + 1)])
  }
  const mergeProposalEntry = index => {
    if (index + 1 >= proposalEntries.length) return
    changeProposalEntries([...proposalEntries.slice(0, index), `${proposalEntries[index]}\n${proposalEntries[index + 1]}`, ...proposalEntries.slice(index + 2)])
  }
  const currentPreviewFingerprint = compactionPreview && stored.data?.targets?.[compactionPreview.target]?.source_fingerprint
  const isPreviewStale = Boolean(compactionPreview && currentPreviewFingerprint && currentPreviewFingerprint !== compactionPreview.source_fingerprint)

  useEffect(() => {
    if (!compacting) return undefined
    const startedAt = Date.now()
    const updateElapsed = () => setCompactionElapsed(Math.floor((Date.now() - startedAt) / 1000))
    updateElapsed()
    const timer = setInterval(updateElapsed, 1000)
    return () => clearInterval(timer)
  }, [compacting])

  const guardNavigation = action => {
    if (isDirty) setPendingNavigation(() => action)
    else action()
  }

  useEffect(() => {
    if (!navigationGuard) return undefined
    navigationGuard.current = { dirty: isDirty, busy: compacting || applyingCompaction, requestLeave: guardNavigation }
    return () => { navigationGuard.current = null }
  }, [navigationGuard, isDirty, draftKey, draftConflict, compacting, applyingCompaction])

  const selectTarget = nextTarget => {
    guardNavigation(() => {
      setTarget(nextTarget)
      setSelectedText('')
      setSearch('')
      setAdding(false)
      setDeleteConfirm(false)
      setCompactionPreview(null)
      setOrganizationAnalysis(null)
      reviewRequestId.current += 1
      setSemanticReport(null)
      setReviewingPreservation(false)
      setReviewConfirm(false)
      setFeedback(null)
    })
  }

  const selectEntry = content => {
    guardNavigation(() => {
      setAdding(false)
      setDeleteConfirm(false)
      setSelectedText(content)
    })
  }

  const navigateToDiagnosticEntry = index => {
    if (!entries[index]) return
    setSearch('')
    selectEntry(entries[index].content)
  }

  const exportMemory = selectedTarget => {
    const selectedTargets = selectedTarget === 'both' ? STORED_TARGETS.map(([id]) => id) : [selectedTarget]
    const entries = Object.fromEntries(selectedTargets.map(id => [
      id,
      (stored.data?.targets?.[id]?.entries || []).map(entry => entry.content)
    ]))
    return encodeURIComponent(JSON.stringify({
      format_version: 1,
      target: selectedTarget,
      entries: selectedTarget === 'both' ? entries : entries[selectedTarget]
    }, null, 2))
  }

  const previewImportFile = async event => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    setFeedback(null)
    setTransferPreview(null)
    try {
      if (file.size > 1_000_000) throw new Error('Import file exceeds the 1 MB limit.')
      const payload = JSON.parse(await file.text())
      const preview = await previewMemoryImport(payload)
      if (!preview?.success) throw new Error(preview?.error || 'Memory import preview failed.')
      setTransferPreview({ payload, preview })
    } catch (error) {
      setFeedback({ kind: 'error', message: error instanceof Error ? error.message : String(error) })
    }
  }

  const confirmImport = async () => {
    if (!transferPreview) return
    setImporting(true)
    setFeedback(null)
    try {
      const result = await applyMemoryImport(transferPreview.payload, transferPreview.preview.source_fingerprints)
      const statuses = Object.entries(result?.outcomes || {}).map(([id, outcome]) => `${id}: ${outcome.status}${outcome.added_count ? ` (${outcome.added_count} added)` : ''}${outcome.error ? ` — ${outcome.error}` : ''}${outcome.audit_warning ? ` — ${outcome.audit_warning}` : ''}`)
      setFeedback({ kind: result?.success ? 'success' : 'error', message: statuses.join(' · ') || 'Import completed.' })
      if (result?.success) setTransferPreview(null)
      await stored.refetch()
    } catch (error) {
      setFeedback({ kind: 'error', message: error instanceof Error ? error.message : String(error) })
    } finally {
      setImporting(false)
    }
  }

  const analyze = async () => {
    setAnalyzing(true)
    setOrganizationAnalysis(null)
    setFeedback(null)
    try {
      const result = await analyzeOrganization(target)
      if (!result?.success) throw new Error(result?.error || 'AI organization analysis failed; memory was not changed.')
      setOrganizationAnalysis(result)
    } catch (error) {
      setFeedback({ kind: 'error', message: error instanceof Error ? error.message : String(error) })
    } finally {
      setAnalyzing(false)
    }
  }

  const beginAdd = () => {
    guardNavigation(() => {
      setAdding(true)
      setSelectedText('')
      setDraftSlots(previous => ({ ...previous, [`${target}:new`]: { base: '', text: '', sourceFingerprint: targetData.source_fingerprint } }))
      setDeleteConfirm(false)
      setCompactionPreview(null)
      setFeedback(null)
    })
  }

  const add = async () => {
    const content = draft.trim()
    if (!content) return false
    setSaving(true)
    setFeedback(null)
    try {
      const result = await addStoredMemory(target, content)
      setFeedback({ kind: result?.audit_warning ? 'info' : 'success', message: [result?.result?.message || 'Memory entry added.', result?.audit_warning].filter(Boolean).join(' ') })
      setAdding(false)
      setSelectedText(content)
      await stored.refetch()
      setDraftSlots(previous => {
        const next = { ...previous }
        delete next[`${target}:new`]
        return next
      })
      return true
    } catch (error) {
      setFeedback({
        kind: 'error',
        message: error instanceof Error ? error.message : String(error)
      })
      return false
    } finally {
      setSaving(false)
    }
  }

  const save = async () => {
    if (!current) return false
    if (draftConflict) {
      setFeedback({ kind: 'error', message: 'Draft source changed. Review the current entry before saving; your text was preserved.' })
      return false
    }
    setSaving(true)
    setFeedback(null)
    try {
      const result = await saveStoredMemory(target, current.content, draft)
      setFeedback({ kind: result?.audit_warning ? 'info' : 'success', message: [result?.result?.message || 'Memory entry updated.', result?.audit_warning].filter(Boolean).join(' ') })
      setSelectedText(draft.trim())
      setDeleteConfirm(false)
      await stored.refetch()
      setDraftSlots(previous => {
        const next = { ...previous }
        delete next[draftKey]
        return next
      })
      return true
    } catch (error) {
      setFeedback({
        kind: 'error',
        message: error instanceof Error ? error.message : String(error)
      })
      return false
    } finally {
      setSaving(false)
    }
  }

  const discardAndContinue = () => {
    const action = pendingNavigation
    setDraftSlots(previous => {
      const next = { ...previous }
      delete next[draftKey]
      return next
    })
    setPendingNavigation(null)
    action?.()
  }

  const saveAndContinue = async () => {
    const action = pendingNavigation
    const success = adding ? await add() : await save()
    if (success) {
      setPendingNavigation(null)
      action?.()
    }
  }

  const remove = async () => {
    if (!current || current.stale) return
    if (!deleteConfirm) {
      setDeleteConfirm(true)
      return
    }
    setDeleting(true)
    setFeedback(null)
    try {
      const result = await deleteStoredMemory(target, current.content)
      setFeedback({ kind: result?.audit_warning ? 'info' : 'success', message: [result?.result?.message || 'Memory entry removed.', result?.audit_warning].filter(Boolean).join(' ') })
      setSelectedText('')
      setDeleteConfirm(false)
      await stored.refetch()
    } catch (error) {
      setFeedback({
        kind: 'error',
        message: error instanceof Error ? error.message : String(error)
      })
    } finally {
      setDeleting(false)
    }
  }

  const compact = async () => {
    if (applyingCompaction || isDirty) return
    const requestId = ++compactionRequestId.current
    setCompactionElapsed(0)
    setCompacting(true)
    setCompactionPreview(null)
    reviewRequestId.current += 1
    setSemanticReport(null)
    setReviewingPreservation(false)
    setReviewConfirm(false)
    setFeedback(null)
    try {
      const preview = await requestCompaction(target)
      if (!isActiveCompactionRequest(requestId, compactionRequestId.current)) return
      if (preview?.outcome === 'no_change') {
        setFeedback({
          kind: 'info',
          message: [preview.message, preview.reason ? `AI assessment: ${preview.reason}` : null].filter(Boolean).join(' ')
        })
        return
      }
      setCompactionPreview(preview)
      setSemanticReport(null)
      setPreviewView('entries')
      setProposalEntries([...(preview.proposed_entries || [])])
      setProposalEditing(false)
      setConfirmEdited(false)
      // The polling snapshot may predate this preview; refresh once to compare
      // against data at least as recent as the request (no extra AI call).
      await stored.refetch()
    } catch (error) {
      if (!isActiveCompactionRequest(requestId, compactionRequestId.current)) return
      setFeedback({ kind: 'error', message: classifyCompactionFailure(error) })
    } finally {
      if (isActiveCompactionRequest(requestId, compactionRequestId.current)) setCompacting(false)
    }
  }

  const stopWaiting = () => {
    compactionRequestId.current += 1
    setCompacting(false)
    setFeedback({
      kind: 'info',
      message: 'This stops waiting in Desktop; the existing request may continue in Hermes or the model/provider. Its late reply will be ignored, and token costs may still occur. Retry manually if needed.'
    })
  }

  const applyPreview = async () => {
    if (!compactionPreview) return
    if (isPreviewStale) return
    if (!proposalValidation?.valid) return
    if (manuallyEdited && !confirmEdited) {
      // Make normalization visible before asking for the final user click.
      setProposalEntries(proposalValidation.normalized)
      setProposalEditing(false)
      setConfirmEdited(true)
      return
    }
    setApplyingCompaction(true)
    setFeedback(null)
    try {
      const result = await applyCompaction(
        compactionPreview.target,
        compactionPreview.source_fingerprint,
        proposalEntries
      )
      setFeedback({ kind: result?.audit_warning ? 'info' : 'success', message: [result?.result?.message || 'Memory compaction applied.', result?.audit_warning].filter(Boolean).join(' ') })
      setCompactionPreview(null)
      reviewRequestId.current += 1
      setSemanticReport(null)
      setReviewingPreservation(false)
      setReviewConfirm(false)
      setConfirmEdited(false)
      setSelectedText('')
      await stored.refetch()
    } catch (error) {
      setFeedback({
        kind: 'error',
        message: error instanceof Error ? error.message : String(error)
      })
    } finally {
      setApplyingCompaction(false)
    }
  }

  const checkPreservation = async () => {
    if (!compactionPreview || isPreviewStale || !proposalValidation?.valid) return
    const id = ++reviewRequestId.current
    setReviewConfirm(false)
    setReviewingPreservation(true)
    setSemanticReport(null)
    try {
      const response = await requestSemanticReview(
        compactionPreview.target,
        compactionPreview.source_fingerprint,
        compactionPreview.source_entries,
        proposalValidation.normalized
      )
      if (reviewRequestId.current === id) setSemanticReport(response.report)
    } catch (error) {
      if (reviewRequestId.current === id) setFeedback({ kind: 'error', message: error instanceof Error ? error.message : String(error) })
    } finally {
      if (reviewRequestId.current === id) setReviewingPreservation(false)
    }
  }

  if (stored.isError) {
    return jsxs('div', {
      className: 'flex h-full flex-col items-center justify-center gap-3 p-8 text-center',
      children: [
        jsx('div', { className: 'text-base font-medium', children: 'Stored memory unavailable' }),
        jsx('div', {
          className: 'max-w-lg text-sm text-(--ui-text-tertiary)',
          children: 'Enable the Agent half of Magi for this profile, then retry.'
        }),
        jsx('button', {
          type: 'button',
          className: 'rounded border border-(--ui-stroke-secondary) px-3 py-1.5 text-sm hover:bg-(--chrome-action-hover)',
          onClick: () => stored.refetch(),
          children: 'Retry'
        })
      ]
    })
  }

  return jsxs('div', {
    className: 'flex h-full min-h-0 flex-col overflow-hidden',
    children: [
      jsxs('header', {
        className: 'shrink-0 border-b border-(--ui-stroke-secondary) px-4 py-3',
        children: [
          jsxs('div', {
            className: 'flex flex-wrap items-start gap-3',
            children: [
              jsxs('div', {
                className: 'min-w-0 flex-1',
                children: [
                  jsxs('div', {
                    className: 'flex flex-wrap items-center gap-2',
                    children: [
                      jsx('h1', { className: 'text-base font-semibold', children: 'Stored memory' }),
                      stored.isFetching ? jsx(Badge, { children: 'syncing' }) : null
                    ]
                  }),
                  jsx('p', {
                    className: 'mt-0.5 text-xs leading-relaxed text-(--ui-text-tertiary)',
                    children: 'Maintain the durable facts Hermes carries across sessions and keep the memory budget lean.'
                  })
                ]
              }),
              jsx(ActionButton, { onClick: () => stored.refetch(), disabled: stored.isFetching, children: stored.isFetching ? 'Refreshing…' : 'Refresh' })
            ]
          }),
          jsx('div', {
            className: 'mt-2 rounded-lg border border-(--ui-stroke-secondary) bg-(--chrome-action-hover) px-3 py-2',
            children: [
              jsx('div', {
                className: 'grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3',
                children: [
                  jsx(CompactStat, { label: 'Entries', value: targetData.count ?? 0, detail: targetLabel }, 'entries'),
                  jsx(CompactStat, { label: 'Estimated tokens', value: `~${targetData.estimated_tokens ?? 0}`, detail: `/ ~${targetData.estimated_token_limit ?? 0} budget · tokens estimated` }, 'tokens'),
                  jsx(CompactStat, { label: 'Characters', value: targetData.used_chars ?? 0, detail: `/ ${targetData.char_limit ?? 0}` }, 'characters')
                ]
              }),
              jsxs('div', {
                className: 'mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-(--ui-stroke-secondary) pt-2',
                children: [
                  jsxs('div', {
                    className: 'flex shrink-0 items-baseline gap-2',
                    children: [
                      jsx('span', { className: 'text-[0.625rem] font-medium uppercase tracking-wide text-(--ui-text-tertiary)', children: 'Character budget' }),
                      jsx('span', { className: 'shrink-0 text-sm font-semibold tabular-nums', children: `${targetData.usage_percent ?? 0}%` })
                    ]
                  }),
                  jsx('div', { className: 'min-w-20 flex-1', children: jsx(UsageBar, { percent: targetData.usage_percent }) }),
                  jsx('div', { className: 'shrink-0 text-[0.625rem] text-(--ui-text-tertiary)', children: targetData.usage_percent >= 80 ? 'Consider compacting soon' : `${Math.max(0, (targetData.char_limit ?? 0) - (targetData.used_chars ?? 0))} chars free` })
                ]
              }, 'budget')
            ]
          })
        ]
      }),
      jsx(MemoryDiagnostics, {
        diagnostics: targetData.diagnostics,
        targetLabel,
        onSelectEntry: navigateToDiagnosticEntry
      }),
      jsxs('div', {
        className: 'flex shrink-0 flex-wrap items-center gap-2 border-b border-(--ui-stroke-secondary) px-3 py-2',
        children: [
          jsx('div', {
            className: 'min-w-48 flex-1',
            children: jsx(SearchBox, {
              value: search,
              onChange: event => setSearch(event.target.value),
              placeholder: `Search ${targetLabel}`,
              ariaLabel: 'Search stored memory'
            })
          }),
          jsx('div', {
            role: 'group',
            'aria-label': 'Stored memory target',
            className: 'flex shrink-0 gap-1 rounded-lg bg-(--chrome-action-hover) p-1',
            children: STORED_TARGETS.map(([id, label]) =>
              jsx(SegmentedChoice, {
                disabled: compacting || applyingCompaction || saving || deleting,
                onClick: () => selectTarget(id),
                selected: target === id,
                children: `${label} · ${stored.data?.targets?.[id]?.usage_percent ?? 0}%`
              }, id)
            )
          }),
          jsx('a', {
            role: 'button',
            'aria-disabled': importing || !stored.data,
            tabIndex: importing || !stored.data ? -1 : 0,
            className: `inline-flex items-center rounded-md border border-(--ui-stroke-secondary) px-3 py-1.5 text-xs font-medium hover:bg-(--chrome-action-hover) ${importing || !stored.data ? 'pointer-events-none opacity-50' : ''}`,
            href: stored.data && !importing ? `data:application/json;charset=utf-8,${exportMemory(target)}` : undefined,
            download: `magi-memory-${target}.json`,
            children: `Export ${target}`
          }),
          jsx('a', {
            role: 'button',
            'aria-disabled': importing || !stored.data,
            tabIndex: importing || !stored.data ? -1 : 0,
            className: `inline-flex items-center rounded-md border border-(--ui-stroke-secondary) px-3 py-1.5 text-xs font-medium hover:bg-(--chrome-action-hover) ${importing || !stored.data ? 'pointer-events-none opacity-50' : ''}`,
            href: stored.data && !importing ? `data:application/json;charset=utf-8,${exportMemory('both')}` : undefined,
            download: 'magi-memory-both.json',
            children: 'Export both'
          }),
          jsxs('label', {
            className: 'inline-flex cursor-pointer items-center rounded-md border border-(--ui-stroke-secondary) px-3 py-1.5 text-xs font-medium hover:bg-(--chrome-action-hover)',
            children: [
              jsx('span', { children: 'Import JSON' }, 'label'),
              jsx('input', {
                type: 'file', accept: 'application/json,.json', 'aria-label': 'Import memory JSON',
                className: 'sr-only', onChange: previewImportFile
              }, 'input')
            ]
          }),
          jsx(ActionButton, {
            disabled: compacting || applyingCompaction || saving || deleting,
            onClick: beginAdd,
            className: 'focus-visible:outline-2 focus-visible:outline-(--ui-accent)',
            children: 'Add entry'
          }),
          jsx(ActionButton, {
            tone: 'primary',
            disabled: compacting || applyingCompaction || saving || deleting || !targetData.count || isDirty,
            onClick: compact,
            className: 'focus-visible:outline-2 focus-visible:outline-(--ui-accent)',
            children: jsxs('span', {
              className: 'inline-flex items-center gap-1.5',
              children: [
                jsx('svg', {
                  'aria-hidden': 'true',
                  focusable: 'false',
                  viewBox: '0 0 24 24',
                  className: 'size-3.5 shrink-0',
                  fill: 'none',
                  stroke: 'currentColor',
                  strokeWidth: 1.75,
                  strokeLinecap: 'round',
                  strokeLinejoin: 'round',
                  children: [
                    jsx('path', { d: 'M4 20 17 7' }),
                    jsx('path', { d: 'm15 3 1.5 2.5L19 7l-2.5 1.5L15 11l-1.5-2.5L11 7l2.5-1.5L15 3Z' }),
                    jsx('path', { d: 'm20 13 .5 1.5L22 15l-1.5.5L20 17l-.5-1.5L18 15l1.5-.5L20 13Z' })
                  ]
                }),
                jsx('span', { children: compacting ? 'Compacting…' : 'Compact with AI' })
              ]
            })
          }),
          jsx(ActionButton, {
            disabled: analyzing || compacting || applyingCompaction || saving || deleting || !targetData.count,
            onClick: analyze,
            children: analyzing ? 'Analyzing…' : 'Analyze organization'
          })
        ]
      }),
      jsx('p', {
        role: 'note',
        className: 'border-b border-(--ui-stroke-secondary) px-4 py-2 text-xs text-(--ui-text-tertiary)',
        children: 'Memory exports may contain private information; Magi creates downloads locally and does not upload export files. AI analysis sends this selected store to the configured model only when you choose Analyze organization; it does not change memory.'
      }),
      feedback
        ? jsx('div', {
            role: feedback.kind === 'error' ? 'alert' : 'status',
            className: `shrink-0 border-b border-(--ui-stroke-secondary) bg-(--chrome-action-hover) px-4 py-2.5 text-xs animate-in fade-in-0 duration-150 ${
              feedback.kind === 'error' ? 'text-(--ui-danger,#f87171)' : 'text-(--ui-text-tertiary)'
            }`,
            children: feedback.message
          })
        : null,
      pendingNavigation
        ? jsxs('div', {
            role: 'alertdialog',
            'aria-label': 'Unsaved memory draft',
            className: 'flex shrink-0 flex-wrap items-center gap-2 border-b border-(--ui-stroke-secondary) bg-(--chrome-action-hover) px-4 py-3 text-xs',
            children: [
              jsx('span', { className: 'mr-auto font-medium', children: 'Unsaved memory draft. Save or discard before leaving this entry.' }),
              jsx(ActionButton, { onClick: () => setPendingNavigation(null), children: 'Continue editing' }),
              jsx(ActionButton, { tone: 'danger', onClick: discardAndContinue, children: 'Discard draft' }),
              jsx(ActionButton, { tone: 'primary', disabled: saving || draftConflict || !draft.trim(), onClick: saveAndContinue, children: 'Save and continue' })
            ]
          })
        : null,
      organizationAnalysis
        ? jsxs('section', {
            'aria-label': 'Memory organization suggestions',
            className: 'shrink-0 max-h-[40vh] overflow-auto border-b border-(--ui-stroke-secondary) bg-(--chrome-action-hover) px-4 py-3',
            children: [
              jsx('h2', { className: 'text-sm font-semibold', children: 'Memory organization suggestions' }),
              jsx('p', { className: 'mt-1 text-xs text-(--ui-text-tertiary)', children: 'AI findings are uncertain suggestions. They have not been applied; possible contradictions may instead reflect dates or context.' }),
              organizationAnalysis.discarded_suggestions
                ? jsx('p', { className: 'mt-1 text-xs text-(--ui-text-tertiary)', children: `${organizationAnalysis.discarded_suggestions} suggestion(s) with unsupported sources were discarded.` })
                : null,
              organizationAnalysis.suggestions?.length
                ? jsx('ol', {
                    className: 'mt-2 space-y-2',
                    children: organizationAnalysis.suggestions.map((suggestion, index) =>
                      jsxs('li', {
                        className: 'rounded-md border border-(--ui-stroke-secondary) p-3 text-xs',
                        children: [
                          jsx('div', { className: 'font-semibold capitalize', children: suggestion.kind.replaceAll('_', ' ') }, 'kind'),
                          jsx('p', { className: 'mt-1', children: suggestion.reason }, 'reason'),
                          jsx('ul', { className: 'mt-2 space-y-1', children: suggestion.evidence.map((citation, citationIndex) => jsx('li', { className: 'text-(--ui-text-tertiary)', children: `Evidence · entry ${citation.entry_index}: “${citation.quote}”` }, citationIndex)) }, 'evidence'),
                          jsx('ul', { className: 'mt-2 space-y-1', children: suggestion.entry_indexes.map(entryIndex => {
                            const sourceEntry = organizationAnalysis.sources?.find(item => item.entry_index === entryIndex)
                            return sourceEntry ? jsx('li', { className: 'whitespace-pre-wrap break-words rounded bg-(--ui-bg-secondary) p-2', children: `Original entry ${sourceEntry.entry_index}: ${sourceEntry.content}` }, sourceEntry.entry_index) : null
                          }) }, 'sources'),
                          jsx('p', { className: 'mt-2 text-(--ui-text-tertiary)', children: `Uncertainty: ${suggestion.uncertainty}` }, 'uncertainty')
                        ]
                      }, `${suggestion.kind}:${index}`)
                    )
                  })
                : jsx('p', { className: 'mt-2 text-xs text-(--ui-text-tertiary)', children: 'No source-verified suggestions were returned.' })
            ]
          })
        : null,
      transferPreview
        ? jsxs('section', {
            'aria-label': 'Memory import preview',
            className: 'shrink-0 border-b border-(--ui-stroke-secondary) bg-(--chrome-action-hover) px-4 py-3',
            children: [
              jsx('h2', { className: 'text-sm font-semibold', children: 'Import preview · missing entries only' }),
              jsx('p', { className: 'mt-1 text-xs text-(--ui-text-tertiary)', children: 'Existing entries will be kept. Nothing is written until you confirm. Targets are applied separately and may not be atomic together.' }),
              Object.entries(transferPreview.preview.targets || {}).map(([id, plan]) =>
                jsxs('div', {
                  className: 'mt-2 rounded-md border border-(--ui-stroke-secondary) p-2 text-xs',
                  children: [
                    jsx('div', { className: 'font-medium', children: `${id}: ${plan.additions.length} to add · ${plan.skipped_count} already present` }),
                    plan.additions.length
                      ? jsx('ul', { className: 'mt-1 list-disc space-y-1 pl-4', children: plan.additions.map((entry, index) => jsx('li', { className: 'whitespace-pre-wrap break-words', children: entry }, `${id}:${index}`)) })
                      : jsx('p', { className: 'mt-1 text-(--ui-text-tertiary)', children: 'No missing entries.' })
                  ]
                }, id)
              ),
              jsxs('div', {
                className: 'mt-3 flex justify-end gap-2',
                children: [
                  jsx(ActionButton, { disabled: importing, onClick: () => setTransferPreview(null), children: 'Cancel import' }),
                  jsx(ActionButton, { tone: 'primary', disabled: importing, onClick: confirmImport, children: importing ? 'Importing…' : 'Confirm import' })
                ]
              })
            ]
          })
        : null,
      compacting
        ? jsxs('section', {
            className: 'shrink-0 border-b border-(--ui-stroke-secondary) bg-(--chrome-action-hover) px-4 py-3 animate-in fade-in-0 duration-150',
            children: [
              jsxs('div', {
                className: 'flex items-center gap-2 text-sm font-medium',
                children: [
                  jsx('span', { className: 'inline-block h-2 w-2 animate-pulse rounded-full bg-(--ui-accent)' }),
                  jsx('span', { children: 'AI compaction in progress' })
                ]
              }),
              jsx('div', {
                className: 'mt-1 text-xs text-(--ui-text-tertiary)',
                children: `Request sent to Hermes · ${compactionElapsed}s elapsed`
              }),
              jsx('div', {
                className: 'mt-2 text-xs text-(--ui-text-tertiary)',
                children: 'Waiting for the plugin backend response. No progress percentage is available; the backend may automatically retry up to three times within this request. Desktop will not start another request.'
              }),
              jsx(ActionButton, { className: 'mt-2', onClick: stopWaiting, children: 'Stop waiting' })
            ]
          })
        : null,
      compactionPreview
        ? jsxs('section', {
            className: 'flex max-h-[44vh] shrink-0 flex-col overflow-hidden border-b border-(--ui-stroke-secondary) bg-(--chrome-action-hover) animate-in fade-in-0 duration-150',
            children: [
              jsxs('div', {
                className: 'shrink-0 px-4 pt-3',
                children: [
                  isPreviewStale ? jsxs('div', {
                    className: 'mb-2 flex flex-wrap items-center gap-2 text-xs text-(--ui-danger,#f87171)',
                    role: 'alert',
                    children: [
                      jsx('strong', { children: 'Preview outdated' }),
                      jsx('span', { children: 'Stored memory changed since the reviewed snapshot.' }),
                      jsx(ActionButton, { disabled: compacting || applyingCompaction, onClick: compact, children: 'Create a new preview' })
                    ]
                  }) : null,
                  jsxs('div', {
                    className: 'flex flex-wrap items-start gap-2',
                    children: [
                      jsxs('div', {
                        className: 'mr-auto',
                        children: [
                          jsx('h2', { className: 'text-sm font-semibold', children: 'AI compaction preview' }),
                          jsx('p', { className: 'mt-0.5 text-xs text-(--ui-text-tertiary)', children: 'Compare current and proposed memory before applying. Nothing has been written yet.' })
                        ]
                      }),
                      jsx(Badge, { tone: 'accent', children: `${compactionPreview.reduction_percent ?? '?'}% smaller` }),
                      jsx(Badge, { children: `${compactionPreview.attempts || 1} attempt${(compactionPreview.attempts || 1) === 1 ? '' : 's'}` })
                    ]
                  }),
                  jsx('div', {
                    className: 'mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4',
                    children: [
                      jsx(MetaField, { label: 'Current · before', value: `${compactionPreview.before_entry_count ?? '?'} entries · ~${compactionPreview.before_tokens} tokens` }, 'before'),
                      jsx(MetaField, { label: 'Proposed · after', value: `${compactionPreview.after_entry_count ?? '?'} entries · ~${compactionPreview.after_tokens} tokens` }, 'after'),
                      jsx(MetaField, { label: 'Estimated reduction', value: `${compactionPreview.reduction_percent ?? '?'}%` }, 'reduction'),
                      jsx(MetaField, { label: 'Model', value: [compactionPreview.provider, compactionPreview.model].filter(Boolean).join(' / ') || 'Default model' }, 'model')
                    ]
                  })
                ]
              }),
              jsxs('div', {
                className: 'min-h-0 flex-1 overflow-auto px-4 py-3',
                children: [
                  jsx('div', { className: 'mb-2 flex items-center gap-2', children: [
                    previewView === 'diff'
                      ? jsx('h3', { className: 'mr-auto text-xs font-semibold', children: 'Compaction diff' })
                      : jsx('h3', { className: 'mr-auto text-xs font-semibold', children: 'Proposed memory entries' }),
                    jsx(ActionButton, { onClick: () => setPreviewView('entries'), 'aria-pressed': previewView === 'entries', children: 'Entries' }),
                    jsx(ActionButton, { onClick: () => setPreviewView('diff'), 'aria-pressed': previewView === 'diff', children: 'Diff' }),
                    jsx(ActionButton, {
                      disabled: applyingCompaction,
                      onClick: () => { setProposalEditing(!proposalEditing); setPreviewView('entries') },
                      'aria-pressed': proposalEditing,
                      children: proposalEditing ? 'Done editing' : 'Edit proposal'
                    })
                  ] }),
                  jsx('div', {
                    className: 'mb-2 text-xs text-(--ui-text-tertiary)',
                    'aria-live': 'polite',
                    children: proposalValidation ? `${proposalEntries.length}/${proposalValidation.maxEntries} entries · ${proposalValidation.afterChars}/${proposalValidation.maxChars} characters · estimated ${proposalValidation.reductionPercent}% reduction (local estimate, backend validates)` : ''
                  }),
                  proposalValidation && !proposalValidation.valid
                    ? jsx('p', { role: 'alert', className: 'mb-2 text-xs text-(--ui-danger,#f87171)', children: proposalValidation.errors.join(' ') })
                    : null,
                  previewView === 'diff'
                    ? jsx('div', { 'aria-label': 'Compaction diff', 'data-selectable-text': 'true', className: 'space-y-0.5 font-mono text-xs select-text', children: previewDiff.map((row, index) =>
                        jsxs('div', {
                          className: 'whitespace-pre-wrap break-words border-l-2 border-(--ui-stroke-secondary) px-2 py-0.5',
                          children: [jsx('strong', { className: 'mr-2', children: row.kind === 'removed' ? 'Removed −' : row.kind === 'added' ? 'Added +' : 'Unchanged =' }), row.text]
                        }, index)
                      ) })
                    : jsx('div', { className: 'space-y-2', children: proposalEntries.map((entry, index) =>
                  jsxs('div', {
                    className: `rounded-lg border border-(--ui-stroke-secondary) bg-(--ui-bg-primary,transparent) p-3 ${PANEL_TRANSITION}`,
                    children: [
                      jsx('div', {
                        className: 'mb-1 text-[0.6875rem] uppercase tracking-wide text-(--ui-text-tertiary)',
                        children: `Proposed entry ${index + 1}`
                      }),
                      proposalEditing
                        ? jsxs('div', { className: 'space-y-2', children: [
                            jsx('textarea', {
                              value: entry,
                              disabled: applyingCompaction,
                              onChange: event => changeProposalEntries(proposalEntries.map((value, position) => position === index ? event.target.value : value)),
                              'aria-label': `Edit proposed entry ${index + 1}`,
                              className: 'h-24 w-full resize-y rounded border border-(--ui-stroke-secondary) bg-transparent p-2 text-sm'
                            }),
                            jsx('div', { className: 'flex flex-wrap gap-2', children: [
                              jsx(ActionButton, { disabled: applyingCompaction, onClick: () => splitProposalEntry(index), children: 'Split' }),
                              jsx(ActionButton, { disabled: applyingCompaction || index === proposalEntries.length - 1, onClick: () => mergeProposalEntry(index), children: 'Merge next' }),
                              jsx(ActionButton, { disabled: applyingCompaction, onClick: () => changeProposalEntries(proposalEntries.filter((_, position) => position !== index)), children: 'Remove entry' })
                            ] })
                          ] })
                        : jsx('div', { 'data-selectable-text': 'true', className: 'whitespace-pre-wrap break-words text-sm select-text', children: entry })
                    ]
                  }, `compact:${index}`)
                ) }),
                  proposalEditing ? jsx(ActionButton, { disabled: applyingCompaction, onClick: () => changeProposalEntries([...proposalEntries, '']), children: 'Add entry' }) : null
                ]
              }),
              semanticReport
                ? jsxs('section', {
                    className: 'shrink-0 border-t border-(--ui-stroke-secondary) px-4 py-2 text-xs',
                    'aria-label': 'Suspicions from AI',
                    children: [
                      jsx('h3', { className: 'font-semibold', children: 'Suspicions from AI · not proof of preservation' }),
                      jsx('p', { className: 'mt-1', children: `Uncertainty: ${semanticReport.overall_uncertainty || 'Unknown; a model cannot certify safety.'}` }),
                      jsx('div', {
                        className: 'mt-2 max-h-32 space-y-2 overflow-y-auto',
                        children: (semanticReport.suspicions || []).length
                          ? semanticReport.suspicions.map((finding, index) =>
                            jsxs('article', { className: 'rounded border border-(--ui-stroke-secondary) p-2', children: [
                              jsx('strong', { children: `${finding.category}: ${finding.description}` }),
                              jsx('p', { className: 'whitespace-pre-wrap', children: `Original excerpt: ${finding.source_excerpt || '(not supplied)'}` }),
                              jsx('p', { className: 'whitespace-pre-wrap', children: `Proposed excerpt: ${finding.proposal_excerpt || '(not supplied)'}` }),
                              jsx('p', { children: `Reason: ${finding.explanation}` })
                            ] }, index)
                          ) : jsx('p', { children: 'No issues suspected by this model; this does not establish that all facts were preserved.' })
                      })
                    ]
                  })
                : null,
              reviewConfirm
                ? jsxs('div', {
                    role: 'alertdialog',
                    'aria-label': 'AI semantic review confirmation',
                    className: 'shrink-0 border-t border-(--ui-stroke-secondary) px-4 py-2 text-xs',
                    children: [
                      jsx('p', { children: 'This sends the original and edited proposal to the model/provider configured in Hermes. Findings are suspicions, not evidence of completeness. No memory writes occur.' }),
                      jsx('div', { className: 'mt-2 flex gap-2', children: [
                        jsx(ActionButton, { tone: 'primary', disabled: reviewingPreservation, onClick: checkPreservation, children: 'Confirm sending to model' }),
                        jsx(ActionButton, { onClick: () => setReviewConfirm(false), children: 'Cancel review' })
                      ] })
                    ]
                  })
                : null,
              jsxs('div', {
                className: 'flex shrink-0 flex-wrap items-center gap-2 border-t border-(--ui-stroke-secondary) px-4 py-3',
                children: [
                  jsx(ActionButton, {
                    tone: 'primary',
                    disabled: applyingCompaction || isPreviewStale || !proposalValidation?.valid,
                    onClick: applyPreview,
                    children: applyingCompaction ? 'Applying…' : manuallyEdited && !confirmEdited ? 'Review edited proposal' : manuallyEdited ? 'Confirm edited compaction' : 'Apply compaction'
                  }),
                  jsx(ActionButton, {
                    disabled: reviewingPreservation || applyingCompaction || isPreviewStale || !proposalValidation?.valid,
                    onClick: () => setReviewConfirm(true),
                    children: reviewingPreservation ? 'Reviewing…' : 'Check preservation'
                  }),
                  jsx(ActionButton, {
                    disabled: applyingCompaction,
                    onClick: () => {
                      reviewRequestId.current += 1
                      setCompactionPreview(null)
                      setReviewConfirm(false)
                      setSemanticReport(null)
                      setReviewingPreservation(false)
                    },
                    children: 'Cancel preview'
                  }),
                  jsx('span', {
                    className: 'text-xs text-(--ui-text-tertiary)',
                    children: manuallyEdited ? 'Manual edits have not been semantically verified. Confirm final proposal explicitly; invalid edits cannot be applied.' : 'No stored memory changes until Apply compaction is pressed.'
                  })
                ]
              })
            ]
          })
        : null,
      jsxs('div', {
        className: 'grid min-h-0 flex-1 grid-cols-1 overflow-hidden md:grid-cols-[minmax(15rem,22rem)_1fr]',
        children: [
          jsxs('aside', {
            className: 'flex min-h-0 flex-col border-b border-(--ui-stroke-secondary) md:border-b-0 md:border-r',
            children: [
              jsxs('div', {
                className: 'px-3 py-2',
                children: [
                  jsx('div', {
                    className: 'flex items-center justify-between gap-2 text-[0.6875rem] text-(--ui-text-quaternary)',
                    'aria-live': 'polite',
                    'aria-atomic': 'true',
                    children: [
                      jsx('span', { children: query ? `${filtered.length} of ${entries.length} shown` : `${entries.length} entries` }),
                      query ? jsx('button', { type: 'button', onClick: () => setSearch(''), className: 'rounded focus-visible:outline-2 focus-visible:outline-(--ui-accent) hover:text-(--ui-text-primary)', children: 'Clear' }) : null
                    ]
                  })
                ]
              }),
              stored.isLoading
                ? jsx(LoadingRows, {})
                : filtered.length === 0
                  ? jsx(EmptyState, {
                      title: entries.length ? 'No matching entries' : `${targetLabel} is empty`,
                      description: entries.length ? 'Try a broader search or clear the filter.' : 'Add only durable facts that should remain useful across future sessions.',
                      action: entries.length ? null : jsx(ActionButton, { onClick: beginAdd, children: 'Add first entry' })
                    })
                  : jsx('div', {
                      className: 'min-h-0 flex-1 overflow-auto px-2 pb-2',
                      children: filtered.map(entry =>
                        jsxs('button', {
                          type: 'button',
                          disabled: applyingCompaction,
                          onClick: () => selectEntry(entry.content),
                          'aria-current': current?.content === entry.content ? 'true' : undefined,
                          className: `mb-1.5 block w-full rounded-lg border px-3 py-2.5 text-left ${PANEL_TRANSITION} ${
                            current?.content === entry.content
                              ? 'border-(--ui-accent) bg-(--chrome-action-hover)'
                              : 'border-transparent hover:border-(--ui-stroke-secondary) hover:bg-(--chrome-action-hover)'
                          }`,
                          children: [
                            jsxs('div', {
                              className: 'flex items-center gap-2 text-[0.6875rem] text-(--ui-text-quaternary)',
                              children: [
                                jsx(StatusDot, { tone: current?.content === entry.content ? 'accent' : 'default' }),
                                jsx('span', { children: `Entry ${entry.index + 1}` }),
                                jsx('span', { className: 'ml-auto tabular-nums', children: `${String(entry.content || '').length} chars` })
                              ]
                            }),
                            jsx('div', {
                              className: 'mt-1.5 line-clamp-3 whitespace-pre-wrap break-words text-xs leading-relaxed text-(--ui-text-tertiary)',
                              children: entry.content || '(empty entry)'
                            })
                          ]
                        }, `${target}:${entry.index}`)
                      )
                    })
            ]
          }),
          adding
            ? jsxs('main', {
                className: 'flex min-h-0 min-w-0 flex-col overflow-auto animate-in fade-in-0 duration-150',
                children: [
                  jsxs('div', {
                    className: 'flex flex-wrap items-center gap-2 border-b border-(--ui-stroke-secondary) px-4 py-3',
                    children: [
                      jsxs('div', {
                        className: 'mr-auto',
                        children: [
                          jsx('div', { className: 'text-sm font-semibold', children: `New ${targetLabel} entry` }),
                          jsx('div', { className: 'mt-0.5 text-xs text-(--ui-text-tertiary)', children: 'Keep it compact, durable, and useful across unrelated sessions.' })
                        ]
                      }),
                      jsx(ActionButton, {
                        disabled: saving || applyingCompaction,
                        onClick: () => guardNavigation(() => setAdding(false)),
                        children: 'Cancel'
                      }),
                      jsx(ActionButton, {
                        tone: 'primary',
                        disabled: saving || applyingCompaction || !draft.trim(),
                        onClick: add,
                        children: saving ? 'Adding…' : 'Add entry'
                      })
                    ]
                  }),
                  jsx('textarea', {
                    value: draft,
                    disabled: applyingCompaction,
                    onChange: event => {
                      setDraft(event.target.value)
                      setDeleteConfirm(false)
                    },
                    'aria-label': `Add ${target} memory entry`,
                    placeholder: 'Enter a durable memory entry…',
                    className: `${RESIZABLE_ENTRY_VIEWER} m-3 rounded-lg border border-(--ui-stroke-secondary) bg-transparent p-4 text-sm leading-relaxed outline-none transition-[border-color,background-color] duration-150 focus:border-(--ui-accent) focus:bg-(--chrome-action-hover)`,
                    'data-resizable-entry-viewer': 'true',
                    title: 'Drag the lower edge or corner to resize this entry editor.',
                    spellCheck: false
                  }),
                  jsx('div', {
                    className: 'flex items-center gap-2 border-t border-(--ui-stroke-secondary) px-4 py-2 text-xs text-(--ui-text-tertiary)',
                    children: [
                      jsx('span', { children: `${draft.length} chars` }),
                      jsx('span', { children: '·' }),
                      jsx('span', { children: 'Hermes validates limits, locking, and persistence before writing.' })
                    ]
                  })
                ]
              })
            : current
              ? jsxs('main', {
                className: 'flex min-h-0 min-w-0 flex-col overflow-auto animate-in fade-in-0 duration-150',
                children: [
                  jsxs('div', {
                    className: 'flex flex-wrap items-center gap-2 border-b border-(--ui-stroke-secondary) px-4 py-3',
                    children: [
                      jsxs('div', {
                        className: 'mr-auto min-w-0',
                        children: [
                          jsxs('div', {
                            className: 'flex flex-wrap items-center gap-2',
                            children: [
                              jsx('span', { className: 'text-sm font-semibold', children: current.stale ? `${targetLabel} entry changed externally` : `${targetLabel} · Entry ${current.index + 1}` }),
                              current.stale ? jsx(Badge, { tone: 'danger', children: 'stale' }) : isDirty ? jsx(Badge, { tone: 'accent', children: 'unsaved changes' }) : jsx(Badge, { children: 'saved' })
                            ]
                          }),
                          jsx('div', { className: 'mt-0.5 text-xs text-(--ui-text-tertiary)', children: `${draft.length} chars in editor` })
                        ]
                      }),
                      jsx(ActionButton, {
                        tone: isDirty ? 'primary' : 'default',
                        disabled: saving || applyingCompaction || draftConflict || !isDirty,
                        onClick: save,
                        children: saving ? 'Saving…' : 'Save changes'
                      }),
                      jsx(ActionButton, {
                        tone: deleteConfirm ? 'danger' : 'default',
                        disabled: saving || deleting || applyingCompaction || current.stale,
                        onClick: () => guardNavigation(remove),
                        children: deleting ? 'Deleting…' : deleteConfirm ? 'Confirm delete' : 'Delete entry'
                      })
                    ]
                  }),
                  jsx('textarea', {
                    value: draft,
                    disabled: applyingCompaction,
                    onChange: event => {
                      setDraft(event.target.value)
                      setDeleteConfirm(false)
                    },
                    'aria-label': `Edit ${target} memory entry`,
                    className: `${RESIZABLE_ENTRY_VIEWER} m-3 rounded-lg border border-(--ui-stroke-secondary) bg-transparent p-4 text-sm leading-relaxed outline-none transition-[border-color,background-color] duration-150 focus:border-(--ui-accent) focus:bg-(--chrome-action-hover)`,
                    'data-resizable-entry-viewer': 'true',
                    title: 'Drag the lower edge or corner to resize this entry editor.',
                    spellCheck: false
                  }),
                  draftConflict
                    ? jsx('div', {
                        className: 'border-t border-(--ui-stroke-secondary) px-4 py-2 text-xs text-(--ui-danger,#f87171)',
                        role: 'alert',
                        children: 'Draft source changed since editing began. Your draft is preserved. Save is blocked until you review or discard the stale draft.'
                      })
                    : null,
                  jsx('div', {
                    className: 'flex flex-wrap items-center gap-2 border-t border-(--ui-stroke-secondary) px-4 py-2 text-xs text-(--ui-text-tertiary)',
                    children: [
                      jsx('span', { children: isDirty ? 'Draft differs from stored entry.' : 'No unsaved changes.' }),
                      jsx('span', { children: 'Hermes validates drift and persists atomically on save.' })
                    ]
                  })
                ]
              })
              : jsx(EmptyState, {
                title: `No ${targetLabel} entry selected`,
                description: 'Choose an entry from the list or add a new durable memory item.',
                action: jsx(ActionButton, { onClick: beginAdd, children: 'Add entry' })
              })
        ]
      })
    ]
  })
}

function HistoryPage({ loadHistory, clearHistory, source }) {
  const [operation, setOperation] = useState('')
  const [result, setResult] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [clearConfirm, setClearConfirm] = useState(false)
  const [feedback, setFeedback] = useState(null)
  const history = useQuery({
    queryKey: [source, 'history', operation, result, dateFrom, dateTo],
    queryFn: () => loadHistory({ operation, result, dateFrom, dateTo }),
    refetchInterval: 10_000
  })
  const clear = async () => {
    if (!clearConfirm) {
      setClearConfirm(true)
      return
    }
    try {
      await clearHistory()
      setClearConfirm(false)
      setFeedback('Local Magi history cleared.')
      await history.refetch()
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : String(error))
    }
  }
  const records = history.data?.records || []
  return jsxs('main', {
    className: 'flex h-full min-h-0 flex-col overflow-auto',
    children: [
      jsxs('header', {
        className: 'flex flex-wrap items-center gap-3 border-b border-(--ui-stroke-secondary) px-4 py-3',
        children: [
          jsxs('div', { className: 'mr-auto', children: [
            jsx('h1', { className: 'text-base font-semibold', children: 'Magi action history' }),
            jsx('p', { className: 'mt-1 text-xs text-(--ui-text-tertiary)', children: 'Metadata-only audit of actions started in Magi. This is not an undo log and does not include changes made directly in Hermes.' })
          ] }),
          jsx(ActionButton, { disabled: history.isFetching, onClick: () => history.refetch(), children: 'Refresh history' }),
          jsx(ActionButton, { tone: clearConfirm ? 'danger' : 'default', disabled: history.isFetching, onClick: clear, children: clearConfirm ? 'Confirm clear history' : 'Clear history' })
        ]
      }),
      jsx('div', { className: 'flex flex-wrap items-end gap-3 border-b border-(--ui-stroke-secondary) px-4 py-3', children: [
        jsx('label', { className: 'grid gap-1 text-xs', children: [
          jsx('span', { children: 'From' }, 'label'),
          jsx('input', { type: 'date', 'aria-label': 'History from date', value: dateFrom, onChange: event => setDateFrom(event.target.value), className: 'rounded border border-(--ui-stroke-secondary) bg-transparent px-2 py-1' }, 'input')
        ] }),
        jsx('label', { className: 'grid gap-1 text-xs', children: [
          jsx('span', { children: 'To' }, 'label'),
          jsx('input', { type: 'date', 'aria-label': 'History to date', value: dateTo, onChange: event => setDateTo(event.target.value), className: 'rounded border border-(--ui-stroke-secondary) bg-transparent px-2 py-1' }, 'input')
        ] }),
        jsx('label', { className: 'grid gap-1 text-xs', children: [
          jsx('span', { children: 'Operation' }, 'label'),
          jsx('select', { 'aria-label': 'History operation', value: operation, onChange: event => setOperation(event.target.value), className: 'rounded border border-(--ui-stroke-secondary) bg-transparent px-2 py-1', children: [
            jsx('option', { value: '', children: 'All operations' }, 'all'),
            ...['approve', 'reject', 'add', 'edit', 'delete', 'compact', 'import'].map(item => jsx('option', { value: item, children: item }, item))
          ] }, 'select')
        ] }),
        jsx('label', { className: 'grid gap-1 text-xs', children: [
          jsx('span', { children: 'Result' }, 'label'),
          jsx('select', { 'aria-label': 'History result', value: result, onChange: event => setResult(event.target.value), className: 'rounded border border-(--ui-stroke-secondary) bg-transparent px-2 py-1', children: [
            jsx('option', { value: '', children: 'All results' }, 'all'),
            ...['success', 'failure', 'partial'].map(item => jsx('option', { value: item, children: item }, item))
          ] }, 'select')
        ] })
      ] }),
      feedback ? jsx('p', { role: 'status', className: 'px-4 py-2 text-xs', children: feedback }) : null,
      history.error ? jsx('p', { role: 'alert', className: 'px-4 py-2 text-xs text-(--ui-danger,#f87171)', children: 'Local history could not be loaded.' }) : null,
      jsx('section', { className: 'min-h-0 flex-1 overflow-auto p-4', children: records.length
        ? jsx('ol', { className: 'space-y-2', children: records.map(record => {
          const action = ({ add: 'Added', edit: 'Edited', delete: 'Deleted', compact: 'Compacted', import: 'Imported', approve: 'Approved', reject: 'Rejected' })[record.operation] || record.operation
          const target = record.target === 'memory' ? 'MEMORY.md' : record.target === 'user' ? 'USER.md' : record.target
          return jsx('li', { className: 'rounded-md border border-(--ui-stroke-secondary) px-3 py-2 text-sm', children: [
            jsx('div', { className: 'font-medium', children: `${action} · ${target}` }, 'summary'),
            jsx('div', { className: 'mt-1 text-xs', children: `Result: ${record.result}` }, 'result'),
            jsx('div', { className: 'mt-1 text-xs text-(--ui-text-tertiary)', children: `${new Date(record.timestamp).toLocaleString()} · ${record.id}` }, 'meta'),
            record.error ? jsx('div', { className: 'mt-1 text-xs text-(--ui-danger,#f87171)', children: record.error }, 'error') : null
          ] }, record.id)
        }) })
        : jsx('p', { className: 'text-sm text-(--ui-text-tertiary)', children: history.isFetching ? 'Loading history…' : 'No Magi action history matches these filters.' })
      })
    ]
  })
}


function MagiPage(props) {
  const [mode, setMode] = useState('pending')
  const [decisionReport, setDecisionReport] = useState(null)
  const [feedback, setFeedback] = useState(null)
  const [bulkConfirm, setBulkConfirm] = useState('')
  const navigationGuard = useRef(null)
  const selectMode = id => {
    if (mode === 'stored' && navigationGuard.current?.busy) return
    if (mode === 'stored' && navigationGuard.current?.dirty) navigationGuard.current.requestLeave(() => setMode(id))
    else setMode(id)
  }
  return jsxs('div', {
    className: 'flex h-full min-h-0 flex-col',
    children: [
      jsxs('div', {
        className: 'flex shrink-0 flex-wrap items-center gap-3 border-b border-(--ui-stroke-secondary) px-3 py-2',
        children: [
          jsxs('div', {
            className: 'hidden min-w-0 sm:block',
            children: [
              jsx('div', { className: 'text-xs font-semibold', children: 'Magi Workspace' }),
              jsx('div', { className: 'text-[0.6875rem] text-(--ui-text-quaternary)', children: 'Review proposals and maintain durable context' })
            ]
          }),
          jsx('nav', {
            'aria-label': 'Magi sections',
            className: 'ml-auto flex gap-1 rounded-lg bg-(--chrome-action-hover) p-1',
            children: [
              ['pending', 'Pending writes', 'Review queue'],
              ['stored', 'Stored memory', 'Durable context'],
              ['history', 'History', 'Local audit']
            ].map(([id, label, hint]) =>
              jsx(SegmentedChoice, {
                onClick: () => selectMode(id),
                selected: mode === id,
                children: jsxs('span', {
                  className: 'min-w-0',
                  children: [
                    jsx('span', { className: 'block text-xs font-medium', children: label }),
                    jsx('span', { className: `hidden text-[0.625rem] md:block ${mode === id ? 'text-(--ui-text-secondary)' : 'text-(--ui-text-quaternary)'}`, children: hint })
                  ]
                })
              }, id)
            )
          })
        ]
      }),
      jsx('div', {
        className: 'min-h-0 flex-1 overflow-hidden',
        children:
          mode === 'pending'
            ? jsx(PendingWritesPage, {
                ...props,
                decisionReport, setDecisionReport,
                feedback, setFeedback,
                bulkConfirm, setBulkConfirm,
                onOpenStoredMemory: () => setMode('stored')
              })
            : mode === 'stored' ? jsx(StoredMemoryPage, {
                loadStoredMemory: props.loadStoredMemory,
                saveStoredMemory: props.saveStoredMemory,
                addStoredMemory: props.addStoredMemory,
                deleteStoredMemory: props.deleteStoredMemory,
                requestCompaction: props.requestCompaction,
                applyCompaction: props.applyCompaction,
                previewMemoryImport: props.previewMemoryImport,
                applyMemoryImport: props.applyMemoryImport,
                analyzeOrganization: props.analyzeOrganization,
                requestSemanticReview: props.requestSemanticReview,
                source: props.source,
                navigationGuard
              }) : jsx(HistoryPage, { loadHistory: props.loadHistory, clearHistory: props.clearHistory, source: props.source })
      })
    ]
  })
}

export default {
  id: 'magi',
  name: 'Magi',
  description: 'Review pending Hermes memory proposals and inspect or edit stored memory.',
  defaultEnabled: false,
  register(ctx) {
    const loadRecords = () => ctx.rest('/records')
    const loadDetail = id => ctx.rest(`/records/${encodeURIComponent(id)}`)
    const loadStoredMemory = () => ctx.rest('/memory')
    const loadHistory = filters => {
      const query = new URLSearchParams()
      if (filters.operation) query.set('operation', filters.operation)
      if (filters.result) query.set('result', filters.result)
      if (filters.dateFrom) query.set('date_from', filters.dateFrom)
      if (filters.dateTo) query.set('date_to', filters.dateTo)
      return ctx.rest(`/history?${query.toString()}`)
    }
    const clearHistory = () => ctx.rest('/history', { method: 'DELETE', body: { confirmed: true } })
    const saveStoredMemory = (target, oldText, content) =>
      ctx.rest(`/memory/${encodeURIComponent(target)}`, {
        method: 'PUT',
        body: { old_text: oldText, content }
      })
    const addStoredMemory = (target, content) =>
      ctx.rest(`/memory/${encodeURIComponent(target)}/entries`, {
        method: 'POST',
        body: { content }
      })
    const deleteStoredMemory = (target, oldText) =>
      ctx.rest(`/memory/${encodeURIComponent(target)}/entries`, {
        method: 'DELETE',
        body: { old_text: oldText }
      })
    const requestCompaction = async target => {
      const parsed = await ctx.rest(`/memory/${encodeURIComponent(target)}/compact/preview`, {
        method: 'POST'
      })
      if (!parsed?.success) {
        const error = new Error(parsed?.error || 'AI compaction preview failed.')
        error.kind = 'validation'
        throw error
      }
      return parsed
    }
    const applyCompaction = (target, sourceFingerprint, entries) =>
      ctx.rest(`/memory/${encodeURIComponent(target)}/compact`, {
        method: 'POST',
        body: { source_fingerprint: sourceFingerprint, entries }
      })
    const requestSemanticReview = async (target, sourceFingerprint, sourceEntries, proposedEntries) => {
      const parsed = await ctx.rest(`/memory/${encodeURIComponent(target)}/compact/review`, {
        method: 'POST',
        body: { source_fingerprint: sourceFingerprint, source_entries: sourceEntries, proposed_entries: proposedEntries }
      })
      if (!parsed?.success) throw new Error(parsed?.error || 'Preservation review failed.')
      return parsed
    }
    const previewMemoryImport = payload =>
      ctx.rest('/memory/import/preview', { method: 'POST', body: payload })
    const applyMemoryImport = (payload, sourceFingerprints) =>
      ctx.rest('/memory/import', {
        method: 'POST',
        body: { ...payload, source_fingerprints: sourceFingerprints, confirmed: true }
      })
    const analyzeOrganization = async target => {
      const result = await ctx.rest(`/memory/${encodeURIComponent(target)}/organization/analyze`, { method: 'POST' })
      if (!result?.success) throw new Error(result?.error || 'AI organization analysis failed; memory was not changed.')
      return result
    }
    const runDecision = async (action, target) => {
      const parsed = await ctx.rest(`/records/${encodeURIComponent(target)}/decision`, {
        method: 'POST',
        body: { action }
      })
      if (!parsed?.success && !['partial', 'failed'].includes(parsed?.outcome)) {
        throw new Error(parsed?.error || `${action} failed.`)
      }
      if (parsed.audit_warning) parsed.output = [parsed.output, parsed.audit_warning].filter(Boolean).join('\n')
      return parsed
    }

    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/magi' },
        render: () =>
          jsx(MagiPage, {
            loadRecords,
            loadDetail,
            loadStoredMemory,
            loadHistory,
            clearHistory,
            saveStoredMemory,
            addStoredMemory,
            deleteStoredMemory,
            requestCompaction,
            applyCompaction,
            previewMemoryImport,
            applyMemoryImport,
            analyzeOrganization,
            requestSemanticReview,
            runDecision,
            source: ctx.source
          })
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        order: 55,
        data: { path: '/magi', label: 'Magi', codicon: 'wand' }
      }
    ])
  }
}
