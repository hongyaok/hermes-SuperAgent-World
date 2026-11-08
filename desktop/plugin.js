/**
 * Superagent desktop surface — a chat-first workspace over headless chats.
 *
 * Data path: ctx.rest('/chats'), '/chats/{id}', '/chats/{id}/diff',
 * '/chats/{id}/comment-preview', '/models', '/settings', POST /chats,
 * POST /chats/{id}/messages, POST /chats/{id}/model, POST /chats/{id}/stop,
 * PATCH /chats/{id} (rename), POST /chats/{id}/archive (archive/unarchive),
 * DELETE /chats/{id} (row + transcript only — never files), and
 * POST /chats/{id}/comment, POST /settings → dashboard/plugin_api.py. The list
 * query polls at 2s while a worker runs and 8s when the board is quiet; the
 * open transcript polls at 1.5s while its own turn is live. The diff panel is
 * lazy (only while open) and refetches at 4s only while the turn is running.
 * Every mutation invalidates both so the rail, the transcript, and the worker
 * strip agree on the next paint. The per-card elapsed timer ticks on local
 * state only — it must never add a request to those budgets.
 *
 * Housekeeping lives behind a per-row ⋯ that only appears on hover/focus: a
 * rename is an inline editor in the row, archive keeps the row (dimmed, tagged)
 * and makes the composer read-only, and delete asks once before removing the
 * database rows. `?archived=all` is the "Show archived" filter, remembered in
 * ctx.storage.
 *
 * Layout: one 44px header row, then a 16rem session rail beside a chat column.
 * Live workers are a collapsible strip inside the chat column (under the
 * transcript, above the composer), and GitHub/Settings are mutually exclusive
 * overlay drawers — neither can reflow or resize the conversation.
 *
 * The composer is one rounded box, not a stack of rows: an auto-growing field
 * (two rows, eight at most) over one meta row carrying preset/model, the repo
 * for new chats and send/stop. Drafts persist per target (`draft:<id>` and
 * `draft:new`, debounced), a send paints the user's text as an optimistic
 * bubble until the server's own row arrives, and Enter / Cmd+Enter send while
 * Shift+Enter breaks the line. The backend's 8 000-character cap is the
 * contract; the counter only warns.
 *
 * The ticket pane and the status-bar chip stay (they answer "does a human need
 * to look at anything?"); the route page is chat-only.
 *
 * The transcript renders assistant answers through the app's own Markdown
 * surface (feature-detected; older shells keep pre-wrap), folds runs of audit
 * events into a collapsible Activity block, and stamps messages and events in
 * local HH:MM. Bubbles are memoized on primitive props so a 1.5s poll does not
 * re-typeset every answer on the page.
 *
 * Plain ESM, loaded uncompiled — UI is jsx()/jsxs() calls, not JSX syntax.
 * Only @hermes/plugin-sdk, react and react/jsx-runtime resolve here. The SDK is
 * imported as a namespace: a shell that predates a component (the model
 * catalog menu, StatusDot) leaves that binding undefined and the page degrades
 * instead of failing to link.
 */

import * as SDK from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { memo, useEffect, useMemo, useRef, useState } from 'react'

const {
  Tip, StatusDot, DropdownMenu, DropdownMenuTrigger, DropdownMenuContent,
  ModelCatalogMenu, ModelMenuCloseContext, cn, host,
  useQuery, useMutation, useQueryClient, ROUTES_AREA, SIDEBAR_NAV_AREA,
  // Transcript niceties. Optional: a shell that predates them leaves the
  // binding undefined and every use below is feature-detected.
  MessageTextContent, CopyButton, DisclosureCaret,
  // Session housekeeping: the app's own confirm dialog when this shell has
  // one; older shells get a two-step "Confirm delete" instead.
  ConfirmDialog,
} = SDK

const ID = 'superagent'

// Polling budgets. A live worker is worth 2s; a quiet board is not.
const LIST_POLL_RUNNING = 2000
const LIST_POLL_QUIET = 8000
// Typing in the rail is cheap; a search round-trip per keystroke is not.
const SEARCH_DEBOUNCE_MS = 250
const DETAIL_POLL_RUNNING = 1500
const DETAIL_POLL_QUIET = 6000
const TICKET_POLL = 15000
// GitHub is a secondary surface: its status/PR list is only worth a request
// while the panel is open or a session on screen carries a PR badge.
const GITHUB_POLL = 20000
// An open diff panel on a *running* session: fast enough to watch files land,
// slow enough that `git diff` never becomes the busiest thing on the machine.
const DIFF_POLL = 4000

// The live-workers column shows chats that are doing something (or that a
// human should look at). Idle/cancelled chats stay in the rail only.
const LIVE_STATUSES = ['running', 'queued', 'review', 'failed']
const MAX_WORKERS = 8

// -- towns -----------------------------------------------------------------
// One plot per worker slot. The list is deliberately static: a slot is
// allocated once by the backend and never recomputed from the current array,
// so a poll or a town switch cannot move a house. Slots past the list fall
// into a deterministic outer ring. Plots cluster around the plaza so the
// town reads as one community, not far-flung outposts.
const TOWN_PLOTS = [
  { left: 26, top: 22, scale: 0.72 },
  { left: 74, top: 22, scale: 0.72 },
  { left: 22, top: 50, scale: 0.72 },
  { left: 78, top: 50, scale: 0.72 },
  { left: 26, top: 78, scale: 0.72 },
  { left: 74, top: 78, scale: 0.72 },
  { left: 38, top: 22, scale: 0.72 },
  { left: 62, top: 22, scale: 0.72 },
  { left: 38, top: 56, scale: 0.72 },
  { left: 62, top: 56, scale: 0.72 },
  { left: 38, top: 80, scale: 0.72 },
  { left: 62, top: 80, scale: 0.72 },
]
const TOWN_MAX_WORKERS = TOWN_PLOTS.length
const TOWN_HOUSE_SPRITES = [
  'house-a.png', 'house-b.png', 'house-c.png', 'house-d.png',
  'house-e.png', 'house-f.png', 'house-g.png',
]
const TOWN_BUILD_MS = 900
const TOWN_STATUS_LABEL = {
  queued: 'queued', running: 'running', review: 'review', idle: 'idle',
  failed: 'failed', cancelled: 'cancelled', observed: 'observed',
}
const TOWN_PERSON_TONES = [0, 32, 68, 104, 148, 196, 244, 292]
const PETDEX_ROW_STATES = [
  'idle', 'running-right', 'running-left', 'waving', 'jumping',
  'failed', 'waiting', 'running', 'review',
]


// Chat statuses plus the ticket statuses the pane still renders.
const ICONS = {
  observed: '·', queued: '…', running: '▶', review: '◆', blocked: '!',
  failed: '×', done: '✓', ignored: '–', idle: '·', cancelled: '–',
}

const STATUS_TONES = {
  running: 'good', queued: 'muted', review: 'warn',
  failed: 'bad', idle: 'muted', cancelled: 'muted',
}

// Set in register(); the data hooks below need them.
let rest = null
let storage = null
let openExternal = null

// -- storage (feature-detected: older hosts may not ship ctx.storage) -------

function readStored(key, fallback) {
  try {
    if (storage && typeof storage.get === 'function') {
      const value = storage.get(key, fallback)
      return typeof value === 'string' ? value : fallback
    }
  } catch (error) {
    // Storage is a nicety; a broken key must never break the page.
  }
  return fallback
}

function writeStored(key, value) {
  try {
    if (storage && typeof storage.set === 'function') storage.set(key, value)
  } catch (error) {
    // Same: losing a remembered model is not worth an error card.
  }
}

// -- formatting helpers -----------------------------------------------------

function clip(text, max) {
  const value = String(text == null ? '' : text)
  return value.length <= max ? value : value.slice(0, Math.max(1, max - 1)) + '…'
}

/** Middle-out clip for paths: keeps the drive/root and the segment that names
 *  the repo, which is the part a reader is actually looking for. */
function clipMiddle(text, max) {
  const value = String(text == null ? '' : text)
  if (value.length <= max) return value
  const keep = Math.max(1, Math.floor((max - 1) / 2))
  return `${value.slice(0, keep)}…${value.slice(-keep)}`
}

function isBusy(session) {
  return !!session && (session.status === 'running' || session.status === 'queued')
}

/** Relative age of an epoch-seconds timestamp: 'now' / '4m' / '3h' / '2d'. */
function fmtAge(seconds) {
  if (!seconds) return ''
  const delta = Date.now() / 1000 - seconds
  if (!isFinite(delta)) return ''
  if (delta < 45) return 'now'
  if (delta < 3600) return `${Math.max(1, Math.round(delta / 60))}m`
  if (delta < 86400) return `${Math.round(delta / 3600)}h`
  return `${Math.round(delta / 86400)}d`
}

/** The same age for a GitHub ISO-8601 timestamp (e.g. a PR's updated_at). */
function fmtIsoAge(value) {
  const stamp = Date.parse(String(value || ''))
  if (!isFinite(stamp)) return ''
  return fmtAge(stamp / 1000)
}

/** Wall-clock HH:MM (24h) for one epoch-seconds stamp.
 *
 * Deliberately not toLocaleTimeString: the transcript is a fixed-width column
 * and a locale that appends AM/PM or a non-Latin digit set would jitter it.
 * Local getters, so the clock matches the user's wall clock. */
function fmtClock(seconds) {
  const stamp = Number(seconds)
  if (!isFinite(stamp) || stamp <= 0) return ''
  const date = new Date(stamp * 1000)
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
}

/** Compact duration from milliseconds: '12s' / '3m 04s' / '1h 12m'. */
function fmtDuration(ms) {
  if (!ms || !isFinite(ms) || ms < 0) return ''
  const total = Math.round(ms / 1000)
  if (total < 60) return `${total}s`
  const minutes = Math.floor(total / 60)
  if (minutes < 60) return total % 60 ? `${minutes}m ${total % 60}s` : `${minutes}m`
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}

/** Same shape as fmtDuration but from seconds, and 0 renders as '0s'. */
function fmtSeconds(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0))
  if (total < 60) return `${total}s`
  const minutes = Math.floor(total / 60)
  if (minutes < 60) return total % 60 ? `${minutes}m ${total % 60}s` : `${minutes}m`
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}

/** Seconds the current turn has been alive, recomputed against `nowMs`.
 *
 * The server sends `elapsed_seconds` for live rows, but a value frozen at the
 * last poll would make the timer stutter; ticking from `started_at` locally
 * keeps it smooth without a request. A queued turn has no start of its own, so
 * its wait is measured from `created_at`.
 */
function liveElapsedSeconds(session, nowMs) {
  if (!session) return 0
  const start = session.started_at ||
    (session.status === 'queued' ? session.created_at : 0)
  if (start) return Math.max(0, Math.floor(nowMs / 1000 - start))
  const reported = Number(session.elapsed_seconds)
  return isFinite(reported) && reported > 0 ? Math.floor(reported) : 0
}

/** Re-render once a second while `active`; purely local, never a refetch. */
function useNow(active) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return undefined
    setNow(Date.now())
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [active])
  return now
}

/** Elapsed for a live turn, duration for a finished one, age otherwise. */
function timingLabel(session) {
  if (!session) return ''
  if (isBusy(session)) {
    const start = session.started_at || session.updated_at || session.created_at
    return start ? fmtDuration(Date.now() - start * 1000) : ''
  }
  if (session.duration_ms) return fmtDuration(session.duration_ms)
  if (session.started_at && session.finished_at) {
    return fmtDuration((session.finished_at - session.started_at) * 1000)
  }
  return fmtAge(session.updated_at || session.created_at)
}

/** The composer's label: `provider: model`, the model alone, or the default. */
function modelLabel(model, provider) {
  const name = String(model || '').trim()
  const vendor = String(provider || '').trim()
  if (!name) return 'Default model'
  return vendor ? `${vendor}: ${name}` : name
}

/** A session's fixed model, or the lowercase rail copy for "nothing pinned". */
function sessionModelLabel(session) {
  const effort = String((session && session.reasoning_effort) || '').trim()
  const suffix = effort ? ` · ${reasoningLabel(effort)}` : ''
  if (!session || !String(session.model || '').trim()) return `default model${suffix}`
  return `${modelLabel(session.model, session.provider)}${suffix}`
}

function firstLine(text) {
  for (const line of String(text || '').split('\n')) {
    const trimmed = line.trim()
    if (trimmed) return trimmed
  }
  return ''
}

function sessionTitle(session) {
  if (!session) return 'Chat'
  const title = String(session.title || '').trim()
  if (title) return title
  const line = firstLine(session.prompt)
  return line ? clip(line, 72) : session.id
}

/**
 * Toast once per finished turn.
 *
 * `previous` is the status map from the last poll, or null before the first
 * one. The first pass only seeds it, so merely opening the page never replays
 * completions that happened while it was closed. A chat that goes
 * queued/running -> review/failed announces itself; `cancelled` is silent
 * because the user (or the runaway guard) already stopped it deliberately.
 * Returns the map the next poll should compare against.
 */
function announceCompletions(previous, sessions) {
  const next = new Map()
  const list = Array.isArray(sessions) ? sessions : []
  for (const session of list) next.set(session.id, String(session.status || ''))
  if (!previous) return next
  const notify = host && typeof host.notify === 'function' ? host.notify : null
  if (!notify) return next
  for (const session of list) {
    const before = previous.get(session.id)
    const after = String(session.status || '')
    if (before !== 'queued' && before !== 'running') continue
    if (after !== 'review' && after !== 'failed') continue
    const title = sessionTitle(session)
    try {
      notify({
        kind: after === 'failed' ? 'error' : 'info',
        message: after === 'failed' ? `${title} failed` : `${title} finished`,
      })
    } catch (error) {
      // A host without working toasts must not break polling.
    }
  }
  return next
}

function openUrl(url) {
  const target = String(url || '').trim()
  if (!target) return
  try {
    if (typeof openExternal === 'function') return void openExternal(target)
    if (typeof window !== 'undefined' && typeof window.open === 'function') {
      window.open(target, '_blank', 'noopener')
    }
  } catch (error) {
    // A blocked popup is not worth an error card.
  }
}

/** Save a generated document the shell already returned: the response *is*
 *  the file, so no round-trip or filesystem permission is needed. A shell
 *  without Blob just does nothing — the transcript stays on screen. */
function downloadMarkdown(filename, text) {
  try {
    const blob = new Blob([String(text || '')], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = String(filename || 'superagent-chat.md')
    document.body.appendChild(link)
    link.click()
    link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (error) {
    // Nothing to clean up: the export simply was not saved.
  }
}

function errorText(error) {
  if (!error) return ''
  return String((error && error.message) || error)
}

// -- data hooks -------------------------------------------------------------

function fetchTickets() {
  return rest ? rest('/tickets') : Promise.reject(new Error('backend not ready'))
}

/** Liveness + contract generation, so a stale gateway can explain itself. */
function fetchHealth() {
  return rest ? rest('/health') : Promise.reject(new Error('backend not ready'))
}

function fetchChats(archived, query) {
  const suffix = archived === 'all' ? '&archived=all' : ''
  const needle = String(query || '').trim()
  const term = needle ? `&q=${encodeURIComponent(needle)}` : ''
  return rest ? rest(`/chats?limit=50${suffix}${term}`) : Promise.reject(new Error('backend not ready'))
}

/** Roots with their workers. `archived=all` keeps a town on the board. */
function fetchTowns(archived) {
  const suffix = archived === 'all' ? '&archived=all' : ''
  return rest ? rest(`/towns?limit=200${suffix}`) : Promise.reject(new Error('backend not ready'))
}

/** The vendored sprites as data URLs; one fetch per page mount. */
function fetchTownAssets() {
  return rest ? rest('/town/assets') : Promise.reject(new Error('backend not ready'))
}

function fetchChat(id) {
  return rest ? rest(`/chats/${encodeURIComponent(id)}`) : Promise.reject(new Error('backend not ready'))
}

/** What this session changed: git status, stat and a capped patch. */
function fetchChatDiff(id) {
  return rest ? rest(`/chats/${encodeURIComponent(id)}/diff`) : Promise.reject(new Error('backend not ready'))
}

/** The comment body POST /comment would send (no network, no write). */
function fetchCommentPreview(id) {
  return rest ? rest(`/chats/${encodeURIComponent(id)}/comment-preview`) : Promise.reject(new Error('backend not ready'))
}

/** The transcript as Markdown; the caller turns it into a download. */
function fetchChatExport(id) {
  return rest ? rest(`/chats/${encodeURIComponent(id)}/export`) : Promise.reject(new Error('backend not ready'))
}

function fetchModels() {
  return rest ? rest('/models') : Promise.reject(new Error('backend not ready'))
}

function fetchSettings() {
  return rest ? rest('/settings') : Promise.reject(new Error('backend not ready'))
}

/** Aggregated chat activity: totals, a daily series, per-model rows. */
function fetchUsage(days) {
  const span = Math.max(1, Math.min(Number(days) || 14, 90))
  return rest ? rest(`/usage?days=${span}`) : Promise.reject(new Error('backend not ready'))
}

function fetchGithubStatus() {
  return rest ? rest('/github/status') : Promise.reject(new Error('backend not ready'))
}

function fetchGithubPrs() {
  return rest ? rest('/github/prs') : Promise.reject(new Error('backend not ready'))
}

function useTickets() {
  return useQuery({
    queryKey: [ID, 'tickets'], queryFn: fetchTickets,
    refetchInterval: TICKET_POLL, retry: false,
  })
}

/** Session list. `archived` is the server-side visibility mode: 'hide' (the
 *  default board) or 'all' (the rail's "Show archived"). The mode is part of
 *  the query key, so toggling the filter is a different, cached board. */
function useChats(archived = 'hide') {
  return useQuery({
    queryKey: [ID, 'chats', archived], queryFn: () => fetchChats(archived), retry: false,
    refetchInterval: (query) => {
      const data = query.state.data
      return data && (data.running || 0) > 0 ? LIST_POLL_RUNNING : LIST_POLL_QUIET
    },
  })
}

/** Server-side search. The query key carries the term, so a late response for
 *  an older term can never overwrite the newest one; a blank term disables the
 *  query and the rail falls back to the plain list. Results are not polled —
 *  a finished chat does not change what it said. */
function useChatSearch(term, archived = 'hide') {
  return useQuery({
    queryKey: [ID, 'chats', 'search', archived, term],
    queryFn: () => fetchChats(archived, term),
    enabled: !!term,
    retry: false,
  })
}

/** Town read model. Same polling budget as the chat board, never faster. */
function useTowns(archived = 'hide') {
  return useQuery({
    queryKey: [ID, 'towns', archived], queryFn: () => fetchTowns(archived), retry: false,
    refetchInterval: (query) => {
      const data = query.state.data
      return data && (data.running || 0) > 0 ? LIST_POLL_RUNNING : LIST_POLL_QUIET
    },
  })
}

/** Static art: stale forever once it has arrived. */
function useTownAssets() {
  return useQuery({
    queryKey: [ID, 'town', 'assets'], queryFn: fetchTownAssets,
    staleTime: Infinity, retry: false,
  })
}

function fetchTownPets(slugs) {
  if (!rest) return Promise.reject(new Error('backend not ready'))
  const query = slugs ? `?slugs=${encodeURIComponent(slugs)}` : ''
  return rest(`/town/pets${query}`)
}

function useTownPets(slugsKey) {
  return useQuery({
    queryKey: [ID, 'town', 'pets', slugsKey || 'default'],
    queryFn: () => fetchTownPets(slugsKey),
    staleTime: 600000,
    retry: false,
  })
}

function backendNeedsRestart(health) {
  if (!health) return false
  const features = Array.isArray(health.features) ? health.features : []
  return !features.includes('towns') || !features.includes('thinking')
}

/** The gateway's contract is the one route that can explain a stale process. */
function useHealth() {
  return useQuery({
    queryKey: [ID, 'health'], queryFn: fetchHealth,
    staleTime: 15000, retry: false, refetchInterval: 30000,
  })
}

function useChat(id) {
  return useQuery({
    queryKey: [ID, 'chat', id], queryFn: () => fetchChat(id), enabled: !!id, retry: false,
    refetchInterval: (query) =>
      isBusy(query.state.data && query.state.data.session) ? DETAIL_POLL_RUNNING : DETAIL_POLL_QUIET,
  })
}

/** Lazy: the diff is fetched only while its panel is open. A live turn can
 *  still be writing files, so an open panel refetches every 4s; a finished
 *  session leaves the panel alone until Refresh is pressed. */
function useChatDiff(id, enabled, running) {
  return useQuery({
    queryKey: [ID, 'chat', id, 'diff'], queryFn: () => fetchChatDiff(id),
    enabled: !!id && !!enabled, retry: false,
    refetchInterval: enabled && running ? DIFF_POLL : false,
  })
}

function useModels() {
  return useQuery({
    queryKey: [ID, 'models'], queryFn: fetchModels, staleTime: 60000, retry: false,
  })
}

/** Only fetched while the Usage drawer is open: it is a report, not a poll. */
function useUsage(enabled, days = 14) {
  return useQuery({
    queryKey: [ID, 'usage', days], queryFn: () => fetchUsage(days),
    enabled: !!enabled, staleTime: 30000, retry: false,
  })
}

/** Merged settings, fetched only while the settings panel is open. */
function useSettings(enabled) {
  return useQuery({
    queryKey: [ID, 'settings'], queryFn: fetchSettings, enabled, retry: false,
    staleTime: 5000,
  })
}

/** GitHub connection state. One fetch on mount; polls only while `polling`. */
function useGithubStatus(polling) {
  return useQuery({
    queryKey: [ID, 'github'], queryFn: fetchGithubStatus, retry: false,
    staleTime: 10000, refetchInterval: polling ? GITHUB_POLL : false,
  })
}

/** Open PRs are fetched only while the panel is open (20s while it is). */
function useGithubPrs(enabled) {
  return useQuery({
    queryKey: [ID, 'github', 'prs'], queryFn: fetchGithubPrs, enabled, retry: false,
    refetchInterval: enabled ? GITHUB_POLL : false,
  })
}

function invalidateGithub(client) {
  client.invalidateQueries({ queryKey: [ID, 'github'] })
}

function invalidateChats(client, id) {
  client.invalidateQueries({ queryKey: [ID, 'chats'] })
  client.invalidateQueries({ queryKey: [ID, 'towns'] })
  client.invalidateQueries({ queryKey: [ID, 'chat'] })
  if (id) client.invalidateQueries({ queryKey: [ID, 'chat', id] })
}

// -- small shared bits ------------------------------------------------------

function StatusGlyph({ status, pulse }) {
  return jsx('span', {
    'aria-hidden': 'true',
    className: cn('shrink-0 text-(--ui-text-quaternary)',
      pulse && status === 'running' && 'animate-pulse text-(--ui-text-secondary)'),
    children: ICONS[status] || '?',
  })
}

function ChatDot({ status }) {
  if (StatusDot) return jsx(StatusDot, { tone: STATUS_TONES[status] || 'muted' })
  return jsx('span', {
    'aria-hidden': 'true',
    className: 'inline-block shrink-0 text-(--ui-text-quaternary)',
    children: '•',
  })
}

/** `#123` chip that opens the pull request in the browser.
 *
 * A span (role=link), not a button: the rail rows and worker cards are
 * buttons themselves and a nested <button> is invalid DOM. */
function PrBadge({ session, className }) {
  const number = session && session.pr_number
  if (!number) return null
  const url = String((session && session.pr_url) || '')
  const open = (event) => {
    if (event) {
      event.stopPropagation()
      event.preventDefault()
    }
    openUrl(url)
  }
  return jsx('span', {
    role: 'link',
    tabIndex: 0,
    title: url ? `Open PR #${number} on GitHub` : `PR #${number}`,
    onClick: open,
    onKeyDown: (event) => {
      if (event && (event.key === 'Enter' || event.key === ' ')) open(event)
    },
    className: cn('inline-flex shrink-0 items-center rounded border border-(--ui-stroke-secondary)',
      'px-1 text-[0.6875rem] tabular-nums text-(--ui-text-tertiary)',
      'hover:bg-(--chrome-action-hover) hover:text-(--ui-text-secondary)', className),
    children: `#${number}`,
  })
}

/** GitHub status dot: green when a repo + token are configured. */
function GithubDot({ configured }) {
  if (StatusDot) return jsx(StatusDot, { tone: configured ? 'good' : 'muted' })
  return jsx('span', {
    'aria-hidden': 'true',
    className: cn('inline-block shrink-0 leading-none',
      configured ? 'text-(--ui-text-secondary)' : 'text-(--ui-text-quaternary)'),
    children: '●',
  })
}

// One 28px square icon button for the header. The tooltip carries the label:
// the chrome has room for one primary action (New chat) and two glyphs.
const HEADER_ICON = cn(
  'inline-flex h-7 w-7 shrink-0 items-center justify-center rounded',
  'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-(--ui-text-secondary)',
)

function HeaderIconButton({ label, tip, active, onClick, buttonRef, children }) {
  return jsx(Tip, {
    label: tip || label,
    children: jsx('button', {
      type: 'button',
      ref: buttonRef,
      onClick,
      title: tip || label,
      'aria-label': label,
      'aria-expanded': !!active,
      className: cn(HEADER_ICON, active && 'bg-(--chrome-action-hover) text-(--ui-text-secondary)'),
      children,
    }),
  })
}

/** A labelled header action for the one panel a new user must be able to find. */
function HeaderLabelButton({ label, tip, active, onClick, buttonRef, children }) {
  return jsx(Tip, {
    label: tip || label,
    children: jsx('button', {
      type: 'button',
      ref: buttonRef,
      onClick,
      title: tip || label,
      'aria-label': label,
      'aria-expanded': !!active,
      className: cn('inline-flex h-7 shrink-0 items-center gap-1 rounded border',
        'border-(--ui-stroke-secondary) px-2 text-[0.6875rem] text-(--ui-text-secondary)',
        'hover:bg-(--chrome-action-hover) hover:text-(--ui-text-primary)',
        active && 'bg-(--chrome-action-hover) text-(--ui-text-primary)'),
      children,
    }),
  })
}

/** GitHub as a glyph: its status dot is the whole affordance, the tip names
 *  the repo. Keeps the header to a single 44px line. */
function GithubHeaderButton({ open, status, onClick, buttonRef }) {
  const configured = !!(status && status.configured)
  const repo = configured ? String(status.repo || '') : ''
  const tip = configured
    ? `GitHub: ${repo || 'connected'} — connection and open pull requests`
    : 'GitHub — connect a repository to watch pull requests'
  return jsx(HeaderIconButton, {
    label: 'GitHub', tip, active: open, onClick, buttonRef,
    children: jsx(GithubDot, { configured }),
  })
}

function ModeButton({ label, active, onClick }) {
  return jsx('button', {
    type: 'button',
    'aria-pressed': !!active,
    onClick,
    className: cn('rounded px-1.5 py-0.5 text-[0.6875rem]',
      active
        ? 'bg-(--chrome-action-hover) text-(--ui-text-secondary)'
        : 'text-(--ui-text-quaternary) hover:bg-(--chrome-action-hover) hover:text-(--ui-text-secondary)'),
    children: label,
  })
}

/** Comma separated input -> trimmed non-empty strings (the server dedupes). */
function splitList(text) {
  return String(text || '')
    .split(',')
    .map((part) => part.trim())
    .filter(Boolean)
}

function GhostButton({
  children, onClick, disabled, title, className,
  'aria-label': ariaLabel, 'aria-expanded': ariaExpanded,
}) {
  return jsx('button', {
    type: 'button', onClick, disabled, title,
    'aria-label': ariaLabel, 'aria-expanded': ariaExpanded,
    className: cn('rounded px-2 py-1 text-xs text-(--ui-text-secondary) hover:bg-(--chrome-action-hover)',
      disabled && 'opacity-50', className),
    children,
  })
}

function ErrorCard({ title, error, onRetry }) {
  return jsxs('div', {
    className: 'flex flex-col gap-1.5 rounded border border-(--ui-stroke-secondary) p-2.5 text-xs',
    children: [
      jsx('div', { key: 't', className: 'text-(--ui-text-secondary)', children: title || 'Superagent backend unavailable.' }),
      jsx('div', { key: 'e', className: 'break-words text-(--ui-text-quaternary)', children: errorText(error) || 'unknown error' }),
      jsx(GhostButton, { key: 'r', className: 'self-start', onClick: onRetry, children: 'Retry' }),
    ],
  })
}

// -- sessions rail ----------------------------------------------------------

// How long the fallback "Confirm delete" stays armed (the SDK dialog, when the
// shell has one, is the real confirm step).
const DELETE_ARM_MS = 3000

/** One line in the row's ⋯ menu. Plain buttons: the SDK menu portals its
 *  content out of the row, so a nested-button row would still be invalid. */
function RailMenuItem({ label, onSelect }) {
  return jsx('button', {
    type: 'button',
    role: 'menuitem',
    onClick: (event) => {
      // Never let a menu click reach the row under it: selection must not move.
      event.stopPropagation()
      event.preventDefault()
      onSelect()
    },
    className: cn('flex w-full min-w-0 items-center rounded px-2 py-1 text-left text-xs',
      'text-(--ui-text-secondary) hover:bg-(--chrome-action-hover)'),
    children: label,
  })
}

/** SessionRow: a pointer row plus a hover/focus-only ⋯ menu.
 *
 *  The row is a div wrapping a button because the ⋯ trigger is a second
 *  control: a nested <button> is invalid DOM. The ⋯ sits exactly where the age
 *  is and the age fades out under it, so nothing shifts on hover. Rename swaps
 *  the whole row for an inline input (Enter saves, Esc cancels, blur saves when
 *  the text changed); archive toggles archived_at; delete asks first — through
 *  the SDK ConfirmDialog when the shell exports one, otherwise this button
 *  becomes "Confirm delete" for three seconds.
 */
function SessionRow({
  session, selected, onSelect, onRename, onArchive, onDelete, onDeleteAsk, opensUp, match,
  onPin,
}) {
  const archived = !!session.archived_at
  const [menuOpen, setMenuOpen] = useState(false)
  const [renaming, setRenaming] = useState(false)
  const [title, setTitle] = useState('')
  const [armed, setArmed] = useState(false)
  const armTimer = useRef(null)
  // Enter, Esc and blur can all arrive for one edit; only the first settles it.
  const settled = useRef(false)
  const age = session.status === 'running'
    ? timingLabel(session)
    : fmtAge(session.updated_at || session.created_at)
  // One line per chat: title and age. Model and repo live in the tooltip so a
  // 16rem rail stays scannable at a glance.
  const tooltip = [sessionTitle(session), sessionModelLabel(session), String(session.repo || '').trim()]
    .filter(Boolean)
    .join(' · ')

  useEffect(() => () => {
    if (armTimer.current) clearTimeout(armTimer.current)
  }, [])

  const startRename = () => {
    settled.current = false
    setTitle(sessionTitle(session))
    setRenaming(true)
  }

  const cancelRename = () => {
    settled.current = true
    setRenaming(false)
  }

  const saveRename = () => {
    if (settled.current) return
    settled.current = true
    const next = String(title || '').trim()
    setRenaming(false)
    if (!next || next === sessionTitle(session)) return
    onRename(session.id, next)
  }

  const requestDelete = () => {
    if (ConfirmDialog) {
      onDeleteAsk(session)
      return
    }
    setArmed(true)
    if (armTimer.current) clearTimeout(armTimer.current)
    armTimer.current = setTimeout(() => setArmed(false), DELETE_ARM_MS)
  }

  const confirmDelete = () => {
    if (armTimer.current) clearTimeout(armTimer.current)
    setArmed(false)
    onDelete(session.id)
  }

  const items = [
    jsx(RailMenuItem, {
      key: 'pin', label: session.pinned_at ? 'Unpin' : 'Pin to top',
      onSelect: () => { setMenuOpen(false); onPin(session.id, !session.pinned_at) },
    }),
    jsx(RailMenuItem, {
      key: 'rename', label: 'Rename',
      onSelect: () => { setMenuOpen(false); startRename() },
    }),
    jsx(RailMenuItem, {
      key: 'archive', label: archived ? 'Unarchive' : 'Archive',
      onSelect: () => { setMenuOpen(false); onArchive(session.id, !archived) },
    }),
    jsx(RailMenuItem, {
      key: 'delete', label: 'Delete',
      onSelect: () => { setMenuOpen(false); requestDelete() },
    }),
  ]

  const sdkMenu = !!(DropdownMenu && DropdownMenuTrigger && DropdownMenuContent)
  const trigger = jsx('button', {
    type: 'button',
    'aria-label': `Chat actions for ${sessionTitle(session)}`,
    'aria-haspopup': 'menu',
    'aria-expanded': !!menuOpen,
    title: 'Rename, archive or delete',
    onClick: sdkMenu
      ? (event) => event.stopPropagation()
      : (event) => { event.stopPropagation(); setMenuOpen((value) => !value) },
    className: cn('inline-flex h-5 w-5 items-center justify-center rounded leading-none',
      'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-(--ui-text-secondary)',
      menuOpen && 'bg-(--chrome-action-hover) text-(--ui-text-secondary)'),
    children: '⋯',
  })
  const more = sdkMenu
    ? jsxs(DropdownMenu, {
        open: menuOpen, onOpenChange: setMenuOpen,
        children: [
          jsx(DropdownMenuTrigger, { key: 'trigger', asChild: true, children: trigger }),
          jsx(DropdownMenuContent, {
            key: 'content', align: 'end', className: 'w-40 p-1', children: items,
          }),
        ],
      })
    : jsxs('div', {
        className: 'relative',
        // A legacy shell has no portalled menu, and the list scrolls: close on
        // focus loss / Escape, and open upwards near the bottom of the rail so
        // the list's overflow cannot clip the three items.
        onBlur: (event) => {
          if (!event.currentTarget.contains(event.relatedTarget)) setMenuOpen(false)
        },
        onKeyDown: (event) => {
          if (event.key === 'Escape') setMenuOpen(false)
        },
        children: [
          trigger,
          menuOpen
            ? jsx('div', {
                role: 'menu',
                'aria-label': 'Chat actions',
                className: cn('absolute right-0 z-30 flex w-40 flex-col gap-0.5 rounded border',
                  'border-(--ui-stroke-secondary) bg-background p-1 shadow-md',
                  opensUp ? 'bottom-6' : 'top-6'),
                children: items,
              })
            : null,
        ],
      })

  return jsxs('div', {
    'data-archived': archived ? '1' : undefined,
    className: cn('group relative flex w-full min-w-0 items-center rounded',
      selected ? 'bg-(--chrome-action-hover)' : 'hover:bg-(--chrome-action-hover)'),
    children: [
      renaming
        ? jsxs('div', {
            key: 'edit',
            className: 'flex w-full min-w-0 items-center gap-1.5 px-2 py-1',
            children: [
              jsx(StatusGlyph, { key: 'g', status: session.status }),
              jsx('input', {
                key: 'i',
                type: 'text',
                value: title,
                autoFocus: true,
                maxLength: 200,
                'aria-label': 'Rename chat',
                onMouseDown: (event) => event.stopPropagation(),
                onClick: (event) => event.stopPropagation(),
                onChange: (event) => setTitle(event.target.value),
                onKeyDown: (event) => {
                  event.stopPropagation()
                  if (event.key === 'Enter') {
                    event.preventDefault()
                    saveRename()
                  } else if (event.key === 'Escape') {
                    event.preventDefault()
                    cancelRename()
                  }
                },
                onBlur: saveRename,
                className: cn('min-w-0 flex-1 rounded border border-(--ui-stroke-secondary)',
                  'bg-transparent px-1 py-0.5 text-xs text-(--ui-text-secondary) focus:outline-none'),
              }),
            ],
          })
        : jsxs('button', {
            key: 'row',
            type: 'button',
            onClick: () => onSelect(session.id),
            title: tooltip,
            'aria-current': selected ? 'true' : undefined,
            className: cn('flex w-full min-w-0 items-baseline gap-1.5 rounded px-2 py-1 text-left',
              archived && 'opacity-60'),
            children: [
              jsx(StatusGlyph, { key: 'g', status: session.status, pulse: true }),
              jsx('span', {
                key: 't', className: 'flex min-w-0 flex-1 flex-col',
                children: [
                  jsx('span', {
                    key: 'title', className: 'truncate text-xs text-(--ui-text-secondary)',
                    children: session.pinned_at ? `▴ ${sessionTitle(session)}` : sessionTitle(session),
                  }),
                  // Why the chat matched: only while a search is active, so a
                  // normal rail row stays one line.
                  match
                    ? jsx('span', {
                        key: 'match',
                        title: match,
                        className: 'truncate text-[0.6875rem] leading-4 text-(--ui-text-quaternary)',
                        children: match,
                      })
                    : null,
                ],
              }),
              archived
                ? jsx('span', {
                    key: 'tag',
                    className: cn('shrink-0 rounded border border-(--ui-stroke-secondary) px-1',
                      'text-[0.625rem] leading-4 text-(--ui-text-quaternary)'),
                    children: 'archived',
                  })
                : null,
              jsx('span', {
                key: 'a',
                className: cn('shrink-0 text-[0.6875rem] tabular-nums text-(--ui-text-quaternary)',
                  'group-hover:opacity-0 group-focus-within:opacity-0'),
                children: age,
              }),
            ],
          }),
      renaming
        ? null
        : armed
          ? jsx('button', {
              key: 'confirm',
              type: 'button',
              onClick: (event) => { event.stopPropagation(); confirmDelete() },
              className: cn('absolute right-0.5 top-1/2 z-10 -translate-y-1/2 rounded border',
                'border-(--ui-stroke-secondary) bg-(--ui-bg-primary) px-1 py-0.5',
                'text-[0.6875rem] text-(--ui-text-secondary) shadow-sm'),
              children: 'Confirm delete',
            })
          : jsx('div', {
              key: 'more',
              className: cn('absolute right-0.5 top-1/2 flex -translate-y-1/2 items-center',
                'opacity-0 transition-opacity focus-within:opacity-100',
                'group-hover:opacity-100 group-focus-within:opacity-100',
                // An open menu must stay visible even if the pointer leaves and
                // the browser did not focus the trigger on click.
                menuOpen && 'opacity-100'),
              children: more,
            }),
    ],
  })
}

const RAIL_FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'live', label: 'Live' },
  { id: 'review', label: 'Review' },
]

/** Segment predicates. Filtering never clears the selection: the page keeps
 *  `selectedId`, so a hidden session still has its transcript on screen. */
function railFilterMatch(session, filter) {
  if (filter === 'live') return isBusy(session)
  if (filter === 'review') return session.status === 'review' || session.status === 'failed'
  return true
}

const RAIL_EMPTY = {
  all: 'No chats yet.',
  live: 'Nothing running.',
  review: 'Nothing to review.',
}

/** True under the md breakpoint (768px). Only used to fold the mobile rail
 *  after a pick; the layout itself is CSS-driven, so a wrong answer is benign. */
function isNarrowViewport() {
  try {
    if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
      return !!window.matchMedia('(max-width: 767px)').matches
    }
  } catch (error) {
    // Older shells: assume a wide window.
  }
  return false
}

function RailFilter({ value, onChange }) {
  return jsx('div', {
    role: 'group',
    'aria-label': 'Filter chats',
    className: 'flex shrink-0 items-center gap-0.5',
    children: RAIL_FILTERS.map((option) => jsx('button', {
      key: option.id,
      type: 'button',
      'aria-pressed': value === option.id,
      onClick: () => onChange(option.id),
      className: cn('rounded px-1.5 py-0.5 text-[0.6875rem]',
        value === option.id
          ? 'bg-(--chrome-action-hover) text-(--ui-text-secondary)'
          : 'text-(--ui-text-quaternary) hover:bg-(--chrome-action-hover) hover:text-(--ui-text-secondary)'),
      children: option.label,
    })),
  })
}

function SessionsRail({
  sessions, selectedId, onSelect, onRename, onArchive, onDelete,
  showArchived, onToggleArchived, archivedCount, searchRef, onPin,
  embedded = false,
}) {
  const [filter, setFilter] = useState('all')
  const [query, setQuery] = useState('')
  // The debounced term is what the server sees; `query` stays instant so the
  // input never feels laggy.
  const [term, setTerm] = useState('')
  useEffect(() => {
    const handle = setTimeout(() => setTerm(String(query || '').trim()), SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(handle)
  }, [query])
  const search = useChatSearch(term, showArchived ? 'all' : 'hide')
  const searching = !!term
  const source = searching ? ((search.data && search.data.sessions) || []) : sessions
  // The delete confirm step: the SDK dialog needs to live above the row that
  // opened it (a row can unmount as soon as the list refreshes).
  const [pendingDelete, setPendingDelete] = useState(null)
  // Under `md` the rail folds into a 'Chats' section above the chat; picking a
  // chat closes it so the transcript gets the screen. md+ ignores this state.
  const [open, setOpen] = useState(() => (embedded ? true : !selectedId))
  const shown = useMemo(
    () => source.filter((session) => railFilterMatch(session, filter)),
    [source, filter],
  )

  const choose = (id) => {
    onSelect(id)
    if (isNarrowViewport()) setOpen(false)
  }

  // j/k move through what is actually on screen, so a search or a chip filter
  // decides the order too. Typing keeps the keys, and an open drawer owns them.
  useEffect(() => {
    const onKeyDown = (event) => {
      if (!event || event.defaultPrevented) return
      if (event.ctrlKey || event.metaKey || event.altKey) return
      if (event.key !== 'j' && event.key !== 'k') return
      const target = event.target
      const tag = target && target.tagName ? String(target.tagName).toLowerCase() : ''
      if (tag === 'input' || tag === 'textarea' || tag === 'select'
        || (target && target.isContentEditable)) return
      if (typeof document !== 'undefined'
        && document.querySelector('[data-drawer], [data-shortcuts]')) return
      if (!shown.length) return
      if (typeof event.preventDefault === 'function') event.preventDefault()
      const index = shown.findIndex((row) => row.id === selectedId)
      const next = event.key === 'j'
        ? Math.min(shown.length - 1, index < 0 ? 0 : index + 1)
        : Math.max(0, index < 0 ? 0 : index - 1)
      choose(shown[next].id)
    }
    if (typeof window === 'undefined' || typeof window.addEventListener !== 'function') {
      return undefined
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [shown, selectedId, onSelect])

  const removeNow = (id) => {
    setPendingDelete(null)
    onDelete(id)
  }

  const noChats = source.length === 0
  const emptyText = searching
    ? (search.isFetching && !search.data ? 'Searching…' : 'No matching chats.')
    : showArchived
      ? (noChats ? 'No archived chats.' : 'No matching archived chats.')
      : noChats
        ? 'No chats yet.'
        : RAIL_EMPTY[filter]
  const countLabel = searching
    ? `${shown.length} match${shown.length === 1 ? '' : 'es'}`
    : shown.length === sessions.length
      ? `${sessions.length} chat${sessions.length === 1 ? '' : 's'}`
      : `${shown.length} of ${sessions.length}`
  const showToggle = !!showArchived || (Number(archivedCount) || 0) > 0

  return jsxs('div', {
    className: cn('flex min-h-0 w-full flex-col gap-2', !embedded && 'md:w-[16rem] md:shrink-0'),
    children: [
      jsxs('button', {
        key: 'fold',
        type: 'button',
        'aria-expanded': open,
        'aria-label': 'Chats',
        onClick: () => setOpen((value) => !value),
        className: cn('flex h-7 w-full items-center gap-1.5 rounded px-2 text-xs md:hidden',
          embedded && 'hidden',
          'text-(--ui-text-secondary) hover:bg-(--chrome-action-hover)'),
        children: [
          jsx(Caret, { key: 'c', open }),
          jsx('span', { key: 'l', className: 'font-medium', children: 'Chats' }),
          jsx('span', {
            key: 'n', className: 'ml-auto text-[0.6875rem] tabular-nums text-(--ui-text-quaternary)',
            children: sessions.length ? String(sessions.length) : '',
          }),
        ],
      }),
      jsxs('div', {
        key: 'body',
        className: cn('flex min-h-0 flex-col gap-2', open ? 'flex' : 'hidden md:flex'),
        children: [
          jsx('input', {
            key: 'search',
            type: 'search',
            ref: searchRef,
            value: query,
            placeholder: 'Search chats',
            title: 'Search chats (/)',
            'aria-label': 'Search chats',
            autoComplete: 'off',
            onChange: (event) => setQuery(event.target.value),
            className: cn('w-full min-w-0 rounded border border-(--ui-stroke-secondary) bg-transparent',
              'px-2 py-1 text-xs text-(--ui-text-secondary)',
              'placeholder:text-(--ui-text-quaternary) focus:outline-none'),
          }),
          jsx(RailFilter, { key: 'filter', value: filter, onChange: setFilter }),
          jsx('div', {
            key: 'list',
            className: 'flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto pr-1',
            children: shown.length
              ? shown.map((session, index) => jsx(SessionRow, {
                  key: session.id, session, selected: session.id === selectedId,
                  onSelect: choose, onRename, onArchive, onDelete, onPin,
                  onDeleteAsk: setPendingDelete,
                  match: searching ? session.match_snippet : '',
                  // The last two rows open their menu upwards so the list's
                  // overflow cannot clip it in a shell without a portal.
                  opensUp: index >= shown.length - 2,
                }))
              : jsx('div', { className: 'px-2 py-1 text-xs text-(--ui-text-tertiary)', children: emptyText }),
          }),
          jsxs('div', {
            key: 'foot',
            className: cn('flex shrink-0 items-center justify-between gap-1 px-2',
              'text-[0.6875rem] tabular-nums text-(--ui-text-quaternary)'),
            children: [
              jsx('span', { key: 'n', className: 'min-w-0 truncate', children: countLabel }),
              showToggle
                ? jsx('button', {
                    key: 'archived',
                    type: 'button',
                    'aria-pressed': !!showArchived,
                    onClick: onToggleArchived,
                    title: 'Include archived chats in the rail',
                    className: cn('shrink-0 rounded px-1 py-0.5',
                      showArchived
                        ? 'bg-(--chrome-action-hover) text-(--ui-text-secondary)'
                        : 'hover:bg-(--chrome-action-hover) hover:text-(--ui-text-secondary)'),
                    children: showArchived
                      ? 'Hide archived'
                      : `Show archived${Number(archivedCount) ? ` (${archivedCount})` : ''}`,
                  })
                : null,
            ],
          }),
        ],
      }),
      ConfirmDialog
        ? jsx(ConfirmDialog, {
            key: 'delete',
            open: !!pendingDelete,
            onClose: () => setPendingDelete(null),
            onConfirm: () => { if (pendingDelete) removeNow(pendingDelete.id) },
            title: 'Delete this chat?',
            description: pendingDelete
              ? `“${sessionTitle(pendingDelete)}” and its transcript are deleted for good. Files, worktrees and pull requests are not touched.`
              : '',
            confirmLabel: 'Delete',
            destructive: true,
            dismissOnConfirm: true,
          })
        : null,
    ],
  })
}

// -- transcript -------------------------------------------------------------

/** Prompt bubble first (the opening turn is not a chat_messages row), then
 *  messages + tool events merged on created_at. */
function buildTranscript(session) {
  if (!session) return []
  const items = []
  // An empty turn is not a bubble: a blank prompt would otherwise paint an
  // empty card and hide the "No transcript yet" state.
  if (String(session.prompt || '').trim()) {
    items.push({
      key: 'prompt', kind: 'message', role: 'user',
      content: session.prompt || '', created_at: session.created_at || 0,
    })
  }
  for (const message of session.messages || []) {
    if (!String(message.content || '').trim()) continue
    items.push({
      key: `m${message.id}`, kind: 'message', role: message.role,
      content: message.content || '', created_at: message.created_at || 0,
    })
  }
  for (const event of session.events || []) {
    items.push({
      key: `e${event.id}`, kind: 'event', eventKind: event.kind,
      detail: event.detail || '', created_at: event.created_at || 0,
    })
  }
  items.sort((a, b) => a.created_at - b.created_at)
  return items
}

/** Fold runs of consecutive audit events into one Activity block.
 *
 * Messages break a run, so the event stream still reads in wall-clock order:
 * tool, tool, assistant answer, tool, tool. A single event also becomes an
 * Activity block — a lone "bash: ls" line outside the fold would look like a
 * different kind of thing from the same line inside it. */
function groupTranscript(items) {
  const blocks = []
  let run = null
  for (const item of items) {
    if (item.kind !== 'event') {
      run = null
      blocks.push({ key: item.key, kind: 'message', role: item.role, content: item.content, created_at: item.created_at })
      continue
    }
    if (!run) {
      run = { key: `a${item.key}`, kind: 'activity', events: [], created_at: item.created_at }
      blocks.push(run)
    }
    run.events.push(item)
  }
  return blocks
}

/** 'Assistant' / 'User' — capitalized role, never the raw enum. */
function roleLabel(role) {
  const value = String(role || '').trim()
  if (!value) return 'Assistant'
  return value.charAt(0).toUpperCase() + value.slice(1)
}

/** Assistant text: the app's own Markdown pipeline when this shell ships it.
 *  Older shells keep the plain pre-wrap rendering (no lost content). */
function MessageBody({ text }) {
  const value = String(text == null ? '' : text)
  if (!value) return null
  if (MessageTextContent) return jsx(MessageTextContent, { text: value })
  return jsx('div', { className: 'whitespace-pre-wrap break-words', children: value })
}

/** Disclosure glyph: the chrome caret when exported, a text twin otherwise. */
function Caret({ open }) {
  if (DisclosureCaret) return jsx(DisclosureCaret, { open })
  return jsx('span', { 'aria-hidden': 'true', className: 'shrink-0', children: open ? '▾' : '▸' })
}

/** One-line `kind: detail` summary for a collapsed Activity block. */
function eventSummary(event) {
  const kind = String((event && event.eventKind) || '').trim()
  const detail = String((event && event.detail) || '').replace(/\s+/g, ' ').trim()
  if (!detail) return kind
  return clip(`${kind}: ${detail}`, 160)
}

/** Per-answer header: who said it, when, and a copy affordance.
 *  The timestamp is the message's own created_at, not the poll time. */
function MessageMeta({ role, createdAt, content }) {
  const clock = fmtClock(createdAt)
  return jsxs('div', {
    className: 'flex min-w-0 items-center gap-1.5 text-[0.6875rem] text-(--ui-text-quaternary)',
    children: [
      jsx('span', { key: 'r', className: 'font-medium text-(--ui-text-tertiary)', children: roleLabel(role) }),
      clock ? jsx('span', { key: 't', className: 'tabular-nums', children: clock }) : null,
      CopyButton
        ? jsx(CopyButton, { key: 'c', text: String(content == null ? '' : content), appearance: 'icon', label: 'Copy message' })
        : null,
    ],
  })
}

const Bubble = memo(function Bubble({ role, content, createdAt }) {
  const mine = role === 'user'
  const body = String(content == null ? '' : content)
  const clock = fmtClock(createdAt)
  // User turns stay verbatim: Markdown in a prompt is the user's text, not a
  // document to typeset. Only the assistant answer renders as Markdown.
  if (mine) {
    return jsxs('div', {
      className: 'flex max-w-[85%] min-w-0 flex-col items-end gap-0.5 self-end',
      children: [
        jsx('div', {
          key: 'b',
          className: cn('min-w-0 rounded-lg bg-(--chrome-action-hover) px-3 py-1.5',
            'whitespace-pre-wrap break-words text-sm leading-relaxed text-(--ui-text-secondary)'),
          children: body,
        }),
        clock
          ? jsx('span', {
              key: 't', className: 'text-[0.625rem] tabular-nums text-(--ui-text-quaternary)',
              children: clock,
            })
          : null,
      ],
    })
  }
  // An answer is a document, not a card: no border, just generous line-height
  // so a long reply reads like the main chat. Code blocks keep their own fill.
  return jsxs('div', {
    className: 'flex w-full max-w-[92%] min-w-0 flex-col gap-1 self-start',
    children: [
      jsx(MessageMeta, { key: 'h', role, createdAt, content: body }),
      jsx('div', {
        key: 'b',
        className: 'min-w-0 max-w-full text-sm leading-[1.7] text-(--ui-text-secondary)',
        children: jsx(MessageBody, { text: body }),
      }),
    ],
  })
})

/** One audit line: `kind: detail`, monospace, dim, stamped HH:MM. */
function EventRow({ item }) {
  const detail = String(item.detail || '')
  const clock = fmtClock(item.created_at)
  return jsxs('div', {
    className: 'flex min-w-0 items-baseline gap-1.5 pl-1 text-[0.6875rem] text-(--ui-text-quaternary)',
    children: [
      jsx('span', { key: 'k', className: 'shrink-0 font-mono', children: item.eventKind }),
      jsx('span', { key: 'c', className: 'shrink-0', children: ':' }),
      detail
        ? jsx('span', {
            key: 'd', title: detail,
            className: 'min-w-0 flex-1 truncate font-mono',
            children: clip(detail.replace(/\s+/g, ' '), 160),
          })
        : null,
      clock ? jsx('span', { key: 't', className: 'shrink-0 tabular-nums', children: clock }) : null,
    ],
  })
}

/** A run of audit events, collapsible. Collapsed to a 1-line summary by default
 *  so the latest message is not buried under tool noise. */
function Activity({ events, running, sessionId }) {
  const [open, setOpen] = useState(false)
  const latest = events[events.length - 1]
  const clock = fmtClock(latest && latest.created_at)
  const count = events.length

  return jsxs('div', {
    className: 'flex w-full min-w-0 flex-col gap-0.5',
    children: [
      jsxs('button', {
        key: 'head',
        type: 'button',
        'aria-expanded': open,
        'aria-label': `Activity, ${count} ${count === 1 ? 'step' : 'steps'}`,
        onClick: () => setOpen((value) => !value),
        className: cn('flex w-full min-w-0 items-center gap-1.5 self-start rounded px-1 py-0.5',
          'text-[0.6875rem] text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover)'),
        children: [
          jsx(Caret, { key: 'caret', open }),
          jsx('span', {
            key: 'label', className: 'shrink-0 font-medium',
            children: `Activity · ${count} ${count === 1 ? 'step' : 'steps'}`,
          }),
          open || !latest
            ? null
            : jsx('span', {
                key: 'summary',
                className: 'min-w-0 flex-1 truncate text-left text-(--ui-text-quaternary)',
                children: eventSummary(latest),
              }),
          clock
            ? jsx('span', { key: 'clock', className: 'ml-auto shrink-0 pl-2 tabular-nums text-(--ui-text-quaternary)', children: clock })
            : null,
        ],
      }),
      open
        ? jsx('div', {
            key: 'body',
            className: 'flex flex-col gap-0.5',
            children: events.map((event) => jsx(EventRow, { key: `${sessionId}:${event.key}`, item: event })),
          })
        : null,
    ],
  })
}

/** Skeleton-ish placeholder while a live turn has not produced an answer yet. */
function WaitingCard({ session }) {
  return jsxs('div', {
    className: 'flex max-w-[85%] min-w-0 flex-col gap-1.5 self-start',
    children: [
      jsxs('div', {
        key: 'meta',
        className: 'flex min-w-0 items-center gap-1.5 text-[0.6875rem] text-(--ui-text-tertiary)',
        children: [
          jsx('span', { key: 's', className: 'font-medium', children: session.status || 'running' }),
          jsx('span', { key: 'd', children: '·' }),
          jsx('span', { key: 'm', className: 'min-w-0 truncate', children: sessionModelLabel(session) }),
        ],
      }),
      jsx('div', {
        key: 't', className: 'text-sm text-(--ui-text-secondary)',
        children: 'Waiting for the first response…',
      }),
      jsxs('div', {
        key: 'sk', className: 'flex flex-col gap-1 pt-0.5', 'aria-hidden': 'true',
        children: [
          jsx('span', { key: 'a', className: 'h-1.5 w-40 animate-pulse rounded bg-(--chrome-action-hover)' }),
          jsx('span', { key: 'b', className: 'h-1.5 w-28 animate-pulse rounded bg-(--chrome-action-hover)' }),
          jsx('span', { key: 'c', className: 'h-1.5 w-32 animate-pulse rounded bg-(--chrome-action-hover)' }),
        ],
      }),
    ],
  })
}

function Transcript({ session, optimistic }) {
  const items = useMemo(() => buildTranscript(session), [session])
  const blocks = useMemo(() => groupTranscript(items), [items])
  const boxRef = useRef(null)
  const stickRef = useRef(true)
  const [away, setAway] = useState(false)
  const sessionId = (session && session.id) || ''
  const tail = items.length ? items[items.length - 1].key : 'empty'
  const running = isBusy(session)
  const hasAnswer = items.some((item) => item.kind === 'message' && item.role !== 'user')
  const waiting = !!session && !hasAnswer && (session.status === 'queued' || session.status === 'running')
  // An optimistic turn is a local bubble until the server's own user row
  // lands. Counting matching rows means sending the same text twice still
  // shows a bubble the second time, and never paints two for one send.
  const pending = optimistic && optimistic.text ? optimistic : null
  const pendingShown = pending
    ? items.filter((item) => item.kind === 'message' && item.role === 'user' &&
        String(item.content || '').trim() === pending.text).length
    : 0
  const showPending = !!pending && pendingShown <= (Number(pending.seen) || 0)

  // Switching chats starts at the newest line; polling never moves the box
  // unless the reader was already at the bottom.
  useEffect(() => {
    stickRef.current = true
    setAway(false)
    const el = boxRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [sessionId])
  useEffect(() => {
    const pinToBottom = () => {
      const el = boxRef.current
      if (el && stickRef.current) el.scrollTop = el.scrollHeight
    }
    pinToBottom()
    const raf = typeof requestAnimationFrame === 'function' ? requestAnimationFrame(pinToBottom) : null
    const timer1 = typeof setTimeout === 'function' ? setTimeout(pinToBottom, 40) : null
    const timer2 = typeof setTimeout === 'function' ? setTimeout(pinToBottom, 160) : null
    return () => {
      if (raf != null && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(raf)
      if (timer1 != null && typeof clearTimeout === 'function') clearTimeout(timer1)
      if (timer2 != null && typeof clearTimeout === 'function') clearTimeout(timer2)
    }
  }, [sessionId, items.length, tail, showPending, session && session.updated_at])

  const scrollToLatest = () => {
    stickRef.current = true
    setAway(false)
    const el = boxRef.current
    if (el) el.scrollTop = el.scrollHeight
  }

  const body = blocks.length
    ? blocks.map((block) => (block.kind === 'activity'
        ? jsx(Activity, {
            key: `${sessionId}:${block.key}`, events: block.events, running, sessionId,
          })
        : jsx(Bubble, {
            key: `${sessionId}:${block.key}`, role: block.role, content: block.content,
            createdAt: block.created_at,
          })))
    : []

  if (waiting) body.push(jsx(WaitingCard, { key: 'waiting', session }))
  if (showPending) {
    body.push(jsxs('div', {
      key: 'pending',
      'data-pending': '1',
      className: 'flex max-w-[85%] min-w-0 flex-col items-end gap-0.5 self-end',
      children: [
        jsx('div', {
          key: 'b',
          className: cn('min-w-0 rounded-lg bg-(--chrome-action-hover) px-3 py-1.5',
            'whitespace-pre-wrap break-words text-sm leading-relaxed text-(--ui-text-secondary) opacity-70'),
          children: pending.text,
        }),
        jsx('span', {
          key: 't', className: 'text-[0.625rem] text-(--ui-text-quaternary)', children: 'Sending…',
        }),
      ],
    }))
  }
  if (!body.length) {
    body.push(jsx('div', {
      key: 'empty', className: 'text-xs text-(--ui-text-tertiary)', children: 'No transcript yet.',
    }))
  }

  return jsxs('div', {
    className: 'relative flex min-h-0 flex-1 flex-col overflow-hidden',
    style: {
      flex: '1 1 0%',
      minHeight: 0,
      width: '100%',
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden',
    },
    children: [
      jsx('div', {
        key: 'scroll',
        'data-transcript-scroll': '1',
        ref: boxRef,
        onScroll: () => {
          const el = boxRef.current
          if (!el) return
          const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 96
          stickRef.current = atBottom
          setAway(!atBottom)
        },
        // Viewport-proportional cap on top of flex: on hosts with no definite
        // height flex-1 alone would let the column grow with the chat. A
        // max-height can only limit, never force growth, so the fixed dialog
        // is unaffected while unbounded hosts scroll instead.
        className: cn('flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto overflow-y-scroll superagent-pixel-scroll max-h-[56vh] px-2 py-1.5'),
        style: {
          flex: '1 1 0%',
          minHeight: 0,
          width: '100%',
          overflowY: 'scroll',
          overflowX: 'hidden',
          scrollbarGutter: 'stable',
        },
        children: body,
      }),
      away
        ? jsx('button', {
            key: 'jump',
            type: 'button',
            onClick: scrollToLatest,
            className: cn('absolute bottom-3 right-3 inline-flex items-center gap-1 rounded-full border',
              'border-(--ui-stroke-secondary) bg-background px-2.5 py-1 text-[0.6875rem]',
              'text-(--ui-text-secondary) shadow-sm hover:bg-(--chrome-action-hover)'),
            children: '↓ Jump to latest',
          })
        : null,
    ],
  })
}

// -- model picker -----------------------------------------------------------

// The level ladder the Hermes CLI accepts. xhigh/max/ultra are deliberately
// present: the catalog menu offers them, and the old controller silently
// dropped every effort edit, so "above high" never reached a worker.
const REASONING_LEVELS = [
  { value: '', label: 'Default' },
  { value: 'none', label: 'None' },
  { value: 'minimal', label: 'Minimal' },
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
  { value: 'xhigh', label: 'Extra high' },
  { value: 'max', label: 'Max' },
  { value: 'ultra', label: 'Ultra' },
]

function reasoningLabel(value) {
  const found = REASONING_LEVELS.find((level) => level.value === value)
  return found ? found.label : String(value || '')
}

function modelWithThinking(value) {
  const base = modelLabel(value && value.model, value && value.provider)
  const effort = String((value && value.effort) || '').trim()
  return effort ? `${base} · ${reasoningLabel(effort)}` : base
}

function PresetSelect({ value, models, onChange }) {
  const presets = (models && models.models) || []
  const current = value.model || ''
  const options = current && presets.indexOf(current) === -1 ? [current].concat(presets) : presets
  return jsxs('span', {
    className: 'flex min-w-0 items-center gap-1',
    children: [
      jsxs('select', {
        key: 'model',
        value: current,
        'aria-label': 'Model',
        onChange: (event) => onChange({
          model: event.target.value, provider: '',
          // Keep the chosen thinking level when only the model changes; the
          // fallback picker has no effort control of its own.
          effort: String(value.effort || ''),
        }),
        className: cn('max-w-[14rem] rounded border border-(--ui-stroke-secondary) bg-transparent',
          'px-1.5 py-1 text-[0.6875rem] text-(--ui-text-secondary)'),
        children: [
          jsx('option', { key: 'default', value: '', children: 'Default model' }),
          ...options.map((name) => jsx('option', { key: name, value: name, children: name })),
        ],
      }),
      jsx('select', {
        key: 'effort',
        value: String(value.effort || ''),
        'aria-label': 'Thinking level',
        title: 'Thinking level for this chat',
        onChange: (event) => onChange({
          model: current, provider: String(value.provider || ''),
          effort: String(event.target.value || ''),
        }),
        className: cn('max-w-[8rem] rounded border border-(--ui-stroke-secondary) bg-transparent',
          'px-1.5 py-1 text-[0.6875rem] text-(--ui-text-secondary)'),
        children: REASONING_LEVELS.map((level) => jsx('option', {
          key: level.value || 'default', value: level.value, children: level.label,
        })),
      }),
    ],
  })
}

/** DropdownMenu + the app's own ModelCatalogMenu driven by a detached
 *  controller (the kanban pattern): the menu renders, we own what a pick
 *  means. Falls back to a preset <select> on shells without the catalog. */
function ModelPicker({ value, models, onChange, disabled }) {
  const [open, setOpen] = useState(false)
  if (!DropdownMenu || !DropdownMenuTrigger || !DropdownMenuContent || !ModelCatalogMenu) {
    return jsx(PresetSelect, { value, models, onChange })
  }
  const controller = {
    allowSpeed: false,
    applyPreset: (preset, row) => onChange({
      model: row.model, provider: row.provider,
      // Presets are model/provider pairs; keep the independently chosen
      // thinking level unless the preset explicitly carries one.
      effort: String((preset && preset.effort) || value.effort || ''),
    }),
    current: {
      effort: String(value.effort || ''), fast: false,
      model: value.model, provider: value.provider,
    },
    presetFor: () => ({}),
    select: (model, provider) => {
      onChange({ model, provider, effort: String(value.effort || '') })
      setOpen(false)
    },
    setOptions: (patch, row) => {
      if (patch.effort === undefined) return
      onChange({
        model: row.model, provider: row.provider,
        effort: String(patch.effort || ''),
      })
    },
  }
  const menu = jsx(ModelCatalogMenu, {
    controller, includeMoa: false,
    request: host && typeof host.request === 'function' ? host.request : undefined,
  })
  const body = ModelMenuCloseContext && ModelMenuCloseContext.Provider
    ? jsx(ModelMenuCloseContext.Provider, { value: () => setOpen(false), children: menu })
    : menu

  return jsxs(DropdownMenu, {
    open, onOpenChange: setOpen,
    children: [
      jsx(DropdownMenuTrigger, {
        key: 'trigger', asChild: true,
        children: jsxs('button', {
          type: 'button', disabled,
          className: cn('flex max-w-[14rem] items-center gap-1 rounded border border-(--ui-stroke-secondary)',
            'px-1.5 py-1 text-[0.6875rem] text-(--ui-text-secondary) hover:bg-(--chrome-action-hover)',
            disabled && 'opacity-50'),
          children: [
            jsx('span', { key: 'l', className: 'min-w-0 truncate', children: modelWithThinking(value) }),
            jsx('span', { key: 'c', className: 'shrink-0 text-(--ui-text-quaternary)', children: '▾' }),
          ],
        }),
      }),
      jsx(DropdownMenuContent, { key: 'content', align: 'start', className: 'w-72 p-0', children: body }),
    ],
  })
}

/** Existing chats keep their model, but a small button swaps it for the next
 *  turn via POST /chats/{id}/model. Same ModelPicker, opened on demand. */
function SessionModelControl({ session, models, onChange, pending }) {
  const [editing, setEditing] = useState(false)
  const busy = isBusy(session)
  const disabled = busy || pending
  if (!editing) {
    return jsxs('span', {
      className: 'flex min-w-0 items-center gap-1',
      children: [
        jsx('span', { key: 'l', className: 'min-w-0 truncate', children: sessionModelLabel(session) }),
        jsx(GhostButton, {
          key: 'b',
          className: 'shrink-0',
          disabled,
          title: busy
            ? 'The model and thinking level can change when this turn finishes'
            : 'Change the model or thinking level for future turns',
          'aria-label': 'Change model or thinking level',
          onClick: () => setEditing(true),
          children: pending ? 'Saving…' : 'Change model',
        }),
      ],
    })
  }
  return jsx(ModelPicker, {
    value: {
      model: session.model || '', provider: session.provider || '',
      effort: session.reasoning_effort || '',
    },
    models,
    disabled: pending,
    onChange: (next) => {
      setEditing(false)
      if (onChange) onChange(next)
    },
  })
}

// -- settings panel ---------------------------------------------------------

const MAX_PRESET_ROWS = 12
const SETTINGS_HINT = 'Presets appear in the composer; pick one before starting a chat.'

function blankPreset() {
  return { name: '', model: '', provider: '' }
}

function presetRowsFrom(settings) {
  const rows = settings && Array.isArray(settings.model_presets) ? settings.model_presets : []
  return rows.map((preset) => ({
    name: String((preset && preset.name) || ''),
    model: String((preset && preset.model) || ''),
    provider: String((preset && preset.provider) || ''),
  }))
}

/** Compact token label for a report: 1234567 -> "1.2M". */
function shortTokens(value) {
  const number = Number(value) || 0
  if (number >= 1e6) return `${(number / 1e6).toFixed(1)}M`
  if (number >= 1e3) return `${(number / 1e3).toFixed(1)}k`
  return String(Math.round(number))
}

/** Read-only activity report: five figures, 14 daily bars, and the models
 *  that did the work. Nothing here polls — a report that refetches while you
 *  read it is noise, so it loads once when the drawer opens. */
function UsagePanel({ usage, loading, error, onRetry }) {
  if (error) {
    return jsx(ErrorCard, { title: 'Could not load usage.', error, onRetry })
  }
  if (loading || !usage) {
    return jsx('div', {
      className: 'p-3 text-xs text-(--ui-text-tertiary)', children: 'Loading usage…',
    })
  }
  const totals = usage.totals || {}
  const counts = totals.counts || {}
  const series = usage.series || []
  const models = usage.models || []
  const peak = Math.max(1, ...series.map((day) => Number(day.tokens) || 0))
  const stats = [
    ['Chats', String(totals.sessions || 0)],
    ['Turns', String(totals.turns || 0)],
    ['Tokens', shortTokens(totals.tokens)],
    ['Review', String(counts.review || 0)],
    ['Failed', String(counts.failed || 0)],
  ]
  return jsxs('div', {
    className: 'flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3 text-xs',
    children: [
      jsx('div', {
        key: 'stats', className: 'flex flex-wrap gap-2',
        children: stats.map(([label, value]) => jsxs('div', {
          key: label,
          className: cn('flex min-w-[5rem] flex-col gap-0.5 rounded border',
            'border-(--ui-stroke-secondary) px-2 py-1.5'),
          children: [
            jsx('span', {
              key: 'l',
              className: 'text-[0.625rem] uppercase tracking-wide text-(--ui-text-quaternary)',
              children: label,
            }),
            jsx('span', {
              key: 'v', className: 'text-sm tabular-nums text-(--ui-text-secondary)',
              children: value,
            }),
          ],
        })),
      }),
      jsxs('div', {
        key: 'chart', className: 'flex flex-col gap-1',
        children: [
          jsx('span', {
            key: 'h',
            className: 'text-[0.625rem] uppercase tracking-wide text-(--ui-text-quaternary)',
            children: `Tokens · last ${usage.days || series.length} days`,
          }),
          jsx('div', {
            key: 'bars', className: 'flex h-16 items-end gap-0.5',
            children: series.map((day) => {
              const tokens = Number(day.tokens) || 0
              return jsx('div', {
                key: day.date,
                title: `${day.date}: ${day.sessions || 0} chat(s) · ${tokens.toLocaleString()} tokens`,
                className: cn('flex-1 rounded-t',
                  tokens ? 'bg-(--accent) opacity-60' : 'bg-(--ui-stroke-secondary)'),
                style: {
                  height: `${Math.max(tokens ? 4 : 1, Math.round((tokens / peak) * 100))}%`,
                },
              })
            }),
          }),
          jsxs('div', {
            key: 'axis',
            className: 'flex justify-between text-[0.625rem] text-(--ui-text-quaternary)',
            children: [
              jsx('span', { key: 'a', children: series.length ? series[0].date : '' }),
              jsx('span', {
                key: 'b', children: series.length ? series[series.length - 1].date : '',
              }),
            ],
          }),
        ],
      }),
      models.length
        ? jsxs('div', {
            key: 'models', className: 'flex flex-col gap-1',
            children: [
              jsx('span', {
                key: 'h',
                className: 'text-[0.625rem] uppercase tracking-wide text-(--ui-text-quaternary)',
                children: 'By model',
              }),
              ...models.slice(0, 8).map((row) => jsxs('div', {
                key: `${row.model}|${row.provider}`,
                className: cn('flex items-baseline justify-between gap-2 rounded px-1 py-0.5',
                  'hover:bg-(--chrome-action-hover)'),
                children: [
                  jsx('span', {
                    key: 'm',
                    className: 'min-w-0 truncate font-mono text-[0.6875rem] text-(--ui-text-secondary)',
                    children: row.model || 'unknown',
                  }),
                  jsx('span', {
                    key: 'n',
                    className: 'shrink-0 tabular-nums text-[0.6875rem] text-(--ui-text-quaternary)',
                    children: `${row.sessions || 0} · ${shortTokens(row.tokens)}`,
                  }),
                ],
              })),
            ],
          })
        : null,
    ],
  })
}

/** Full-width settings surface: defaults, named presets and worker limits.
 *
 * Everything is local state until Save; the effect below reseeds from the
 * server whenever the saved values change (including after a save), so a
 * Cancel/close never leaves stale text behind on the next open.
 */
function SettingsPanel({ settings, models, loading, error, onRetry }) {
  const queryClient = useQueryClient()
  const [model, setModel] = useState(() => ({
    model: (settings && settings.default_model) || '',
    provider: (settings && settings.default_provider) || '',
    effort: (settings && settings.default_reasoning_effort) || '',
  }))
  const [rows, setRows] = useState(() => presetRowsFrom(settings))
  const [maxWorkers, setMaxWorkers] = useState(() => Number(settings && settings.max_workers) || 2)
  const [maxTurns, setMaxTurns] = useState(() => Number(settings && settings.chat_max_turns) || 120)
  const [yolo, setYolo] = useState(() => !(settings && settings.worker_yolo === false))
  const [allowWorktree, setAllowWorktree] = useState(() => !(settings && settings.allow_worker_git_worktree === false))
  const [allowPrCreate, setAllowPrCreate] = useState(() => !!(settings && settings.allow_worker_pr_create))
  const [allowPrApprove, setAllowPrApprove] = useState(() => !!(settings && settings.allow_router_pr_approve))
  const [petdexEnabled, setPetdexEnabled] = useState(() => !(settings && settings.petdex_enabled === false))
  const [notice, setNotice] = useState('')
  const [cleanupNotice, setCleanupNotice] = useState('')

  const cleanupWorktrees = useMutation({
    mutationFn: () => rest('/workspaces/cleanup', { method: 'POST', body: {} }),
    onSuccess: (res) => {
      if (res && res.ok) {
        setCleanupNotice(`Cleaned ${res.removed_count || 0} stale worktrees (${res.kept_count || 0} active kept).`)
      } else {
        setCleanupNotice('Could not clean worktrees.')
      }
    },
    onError: (err) => setCleanupNotice(errorText(err) || 'Cleanup failed.'),
  })

  const signature = useMemo(() => JSON.stringify({
    model: (settings && settings.default_model) || '',
    provider: (settings && settings.default_provider) || '',
    effort: (settings && settings.default_reasoning_effort) || '',
    presets: presetRowsFrom(settings),
    workers: Number(settings && settings.max_workers) || 2,
    turns: Number(settings && settings.chat_max_turns) || 120,
    yolo: !(settings && settings.worker_yolo === false),
    allowWorktree: !(settings && settings.allow_worker_git_worktree === false),
    allowPrCreate: !!(settings && settings.allow_worker_pr_create),
    allowPrApprove: !!(settings && settings.allow_router_pr_approve),
    petdexEnabled: !(settings && settings.petdex_enabled === false),
  }), [settings])

  useEffect(() => {
    if (!settings) return
    setModel({
      model: settings.default_model || '',
      provider: settings.default_provider || '',
      effort: settings.default_reasoning_effort || '',
    })
    setRows(presetRowsFrom(settings))
    setMaxWorkers(Number(settings.max_workers) || 2)
    setMaxTurns(Number(settings.chat_max_turns) || 120)
    setYolo(settings.worker_yolo !== false)
    setAllowWorktree(settings.allow_worker_git_worktree !== false)
    setAllowPrCreate(!!settings.allow_worker_pr_create)
    setAllowPrApprove(!!settings.allow_router_pr_approve)
    setPetdexEnabled(settings.petdex_enabled !== false)
  }, [signature]) // eslint-disable-line react-hooks/exhaustive-deps

  const edited = () => setNotice('')

  const updateRow = (index, key, value) => {
    edited()
    setRows((current) => current.map((row, position) => (
      position === index ? { ...row, [key]: value } : row
    )))
  }

  const save = useMutation({
    mutationFn: () => rest('/settings', {
      method: 'POST',
      body: {
        default_model: String(model.model || '').trim(),
        default_provider: String(model.provider || '').trim(),
        default_reasoning_effort: String(model.effort || '').trim(),
        model_presets: rows
          .map((row) => ({
            name: String(row.name || '').trim(),
            model: String(row.model || '').trim(),
            provider: String(row.provider || '').trim(),
          }))
          .filter((row) => !!row.model),
        max_workers: Number(maxWorkers) || 1,
        chat_max_turns: Number(maxTurns) || 1,
        worker_yolo: !!yolo,
        allow_worker_git_worktree: !!allowWorktree,
        allow_worker_pr_create: !!allowPrCreate,
        allow_router_pr_approve: !!allowPrApprove,
        petdex_enabled: !!petdexEnabled,
      },
    }),
    onSuccess: (result) => {
      if (result && result.ok === false) {
        setNotice(String(result.error || result.detail || 'could not save settings'))
        return
      }
      setNotice('Saved.')
      // New-town uses the remembered picker values. Saving a new default is
      // an explicit request to use it, so reset that memory with the default
      // instead of leaving the next chat on the old model/level.
      const savedSettings = result && result.settings
      if (savedSettings) {
        writeStored('lastModel', String(savedSettings.default_model || ''))
        writeStored('lastProvider', String(savedSettings.default_provider || ''))
        writeStored('lastEffort', String(savedSettings.default_reasoning_effort || ''))
      }
      queryClient.invalidateQueries({ queryKey: [ID, 'settings'] })
      queryClient.invalidateQueries({ queryKey: [ID, 'models'] })
      invalidateChats(queryClient)
    },
  })

  const failure = notice && notice !== 'Saved.'
    ? notice
    : (errorText(save.error) || errorText(error))
  const saved = notice === 'Saved.'

  return jsxs('section', {
    'aria-label': 'Settings',
    className: 'flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3',
    children: [
      jsxs('div', {
        key: 'head', className: 'flex flex-wrap items-center justify-between gap-2 text-[0.6875rem]',
        children: [
          jsx('span', {
            key: 'h', className: 'min-w-0 flex-1 truncate text-(--ui-text-tertiary)',
            children: 'Defaults apply to new chats and worker launches.',
          }),
          jsxs('div', {
            key: 'actions', className: 'flex shrink-0 items-center gap-1',
            children: [
              jsx(GhostButton, {
                key: 'save', disabled: save.isPending || loading, onClick: () => save.mutate(),
                children: save.isPending ? 'Saving…' : 'Save',
              }),
              jsx(GhostButton, {
                key: 'refresh', disabled: loading,
                onClick: () => { setNotice(''); onRetry() },
                children: 'Refresh',
              }),
            ],
          }),
        ],
      }),
      saved || failure
        ? jsx('div', {
            key: 'notice',
            className: cn('truncate text-[0.6875rem]', saved ? 'text-(--ui-text-tertiary)' : 'text-(--ui-text-secondary)'),
            title: failure || 'Saved.',
            children: saved ? 'Saved.' : failure,
          })
        : null,
      loading && !settings
        ? jsx('div', { key: 'loading', className: 'px-1 py-2 text-xs text-(--ui-text-tertiary)', children: 'Loading settings…' })
        : jsxs('div', {
            key: 'columns', className: 'flex min-h-0 flex-col gap-4',
            children: [
              jsxs('div', {
                key: 'defaults', className: 'flex min-w-0 flex-col gap-2',
                children: [
                  jsx('div', {
                    key: 'label', className: 'text-[0.6875rem] font-medium uppercase tracking-wide text-(--ui-text-tertiary)',
                    children: 'Default model',
                  }),
                  jsxs('div', {
                    key: 'picker', className: 'flex flex-wrap items-center gap-1',
                    children: [
                      jsx(ModelPicker, {
                        value: model, models, disabled: save.isPending,
                        onChange: (next) => {
                          edited()
                          setModel({
                            model: next.model || '', provider: next.provider || '',
                            effort: String(next.effort || ''),
                          })
                        },
                      }),
                      model.model
                        ? jsx(GhostButton, {
                            key: 'clear', title: 'Clear the default (let Hermes choose)',
                            onClick: () => { edited(); setModel({ model: '', provider: '', effort: '' }) },
                            children: 'Inherit',
                          })
                        : null,
                    ],
                  }),
                  jsxs('label', {
                    key: 'effort',
                    className: 'flex items-center gap-2 text-[0.6875rem] text-(--ui-text-tertiary)',
                    children: [
                      jsx('span', {
                        key: 'l', className: 'min-w-0 flex-1',
                        children: 'Default thinking',
                      }),
                      jsx('select', {
                        key: 'i',
                        value: model.effort || '',
                        'aria-label': 'Default thinking level',
                        onChange: (event) => {
                          edited()
                          setModel((current) => ({ ...current, effort: event.target.value }))
                        },
                        className: cn('rounded border border-(--ui-stroke-secondary)',
                          'bg-background px-1.5 py-1 text-[0.6875rem] text-(--ui-text-secondary)',
                          'focus:outline-none'),
                        children: REASONING_LEVELS.map((level) => jsx('option', {
                          key: level.value || 'default',
                          value: level.value,
                          children: level.value ? level.label : 'Model default',
                        })),
                      }),
                    ],
                  }),
                  jsxs('div', {
                    key: 'provider', className: 'flex flex-wrap items-center gap-2 text-[0.6875rem] text-(--ui-text-tertiary)',
                    children: [
                      jsx('span', { key: 'p', children: model.provider ? `provider: ${model.provider}` : 'provider: auto (chosen by Hermes)' }),
                      jsx('span', { key: 'n', children: 'No default means the CLI picks.' }),
                    ],
                  }),
                  jsxs('label', {
                    key: 'workers', className: 'flex items-center gap-2 text-[0.6875rem] text-(--ui-text-tertiary)',
                    children: [
                      jsx('span', { key: 'l', className: 'min-w-0 flex-1', children: 'Max parallel workers' }),
                      jsx('input', {
                        key: 'i', type: 'number', min: 1, max: 8, value: maxWorkers,
                        'aria-label': 'Max parallel workers',
                        onChange: (event) => { edited(); setMaxWorkers(event.target.value) },
                        className: 'w-16 rounded border border-(--ui-stroke-secondary) bg-transparent px-1.5 py-1 text-[0.6875rem] text-(--ui-text-secondary) focus:outline-none',
                      }),
                    ],
                  }),
                  jsxs('label', {
                    key: 'turns', className: 'flex items-center gap-2 text-[0.6875rem] text-(--ui-text-tertiary)',
                    children: [
                      jsx('span', { key: 'l', className: 'min-w-0 flex-1', children: 'Max turns per worker' }),
                      jsx('input', {
                        key: 'i', type: 'number', min: 1, max: 500, value: maxTurns,
                        'aria-label': 'Max turns per worker',
                        onChange: (event) => { edited(); setMaxTurns(event.target.value) },
                        className: 'w-16 rounded border border-(--ui-stroke-secondary) bg-transparent px-1.5 py-1 text-[0.6875rem] text-(--ui-text-secondary) focus:outline-none',
                      }),
                    ],
                  }),
                  jsxs('label', {
                    key: 'yolo', className: 'flex items-center gap-2 text-[0.6875rem] text-(--ui-text-tertiary)',
                    children: [
                      jsx('input', {
                        key: 'i', type: 'checkbox', checked: !!yolo,
                        'aria-label': 'Run workers unattended',
                        onChange: (event) => { edited(); setYolo(!!event.target.checked) },
                        className: 'accent-(--ui-accent)',
                      }),
                      jsx('span', { key: 'l', className: 'min-w-0 flex-1', children: 'Run workers unattended (--yolo)' }),
                    ],
                  }),
                  jsxs('label', {
                    key: 'worktree', className: 'flex items-center gap-2 text-[0.6875rem] text-(--ui-text-tertiary)',
                    children: [
                      jsx('input', {
                        key: 'i', type: 'checkbox', checked: !!allowWorktree,
                        'aria-label': 'Isolate workers in git worktrees',
                        onChange: (event) => { edited(); setAllowWorktree(!!event.target.checked) },
                        className: 'accent-(--ui-accent)',
                      }),
                      jsx('span', { key: 'l', className: 'min-w-0 flex-1', children: 'Isolate workers in git worktrees' }),
                    ],
                  }),
                  jsxs('label', {
                    key: 'pr-create', className: 'flex items-center gap-2 text-[0.6875rem] text-(--ui-text-tertiary)',
                    children: [
                      jsx('input', {
                        key: 'i', type: 'checkbox', checked: !!allowPrCreate,
                        'aria-label': 'Allow workers to create GitHub PRs',
                        onChange: (event) => { edited(); setAllowPrCreate(!!event.target.checked) },
                        className: 'accent-(--ui-accent)',
                      }),
                      jsx('span', { key: 'l', className: 'min-w-0 flex-1', children: 'Allow workers to create GitHub PRs' }),
                    ],
                  }),
                  jsxs('label', {
                    key: 'pr-approve', className: 'flex items-center gap-2 text-[0.6875rem] text-(--ui-text-tertiary)',
                    children: [
                      jsx('input', {
                        key: 'i', type: 'checkbox', checked: !!allowPrApprove,
                        'aria-label': 'Allow Router and workers to approve PRs',
                        onChange: (event) => { edited(); setAllowPrApprove(!!event.target.checked) },
                        className: 'accent-(--ui-accent)',
                      }),
                      jsx('span', { key: 'l', className: 'min-w-0 flex-1', children: 'Allow Router & workers to review/approve PRs' }),
                    ],
                  }),
                  jsxs('label', {
                    key: 'petdex', className: 'flex items-center gap-2 text-[0.6875rem] text-(--ui-text-tertiary)',
                    children: [
                      jsx('input', {
                        key: 'i', type: 'checkbox', checked: !!petdexEnabled,
                        'aria-label': 'Use Petdex.dev random character sprites',
                        onChange: (event) => { edited(); setPetdexEnabled(!!event.target.checked) },
                        className: 'accent-(--ui-accent)',
                      }),
                      jsx('span', { key: 'l', className: 'min-w-0 flex-1', children: 'Use Petdex.dev random character sprites' }),
                    ],
                  }),
                  jsx('div', {
                    key: 'yolo-help', className: 'text-[0.6875rem] leading-snug text-(--ui-text-tertiary)',
                    children: 'Skips approval prompts inside a turn; PR creation and PR approval stay off unless explicitly enabled above.',
                  }),
                  jsxs('div', {
                    key: 'cleanup-row', className: 'flex flex-wrap items-center gap-2 pt-1',
                    children: [
                      jsx(GhostButton, {
                        key: 'btn',
                        disabled: cleanupWorktrees.isPending,
                        onClick: () => cleanupWorktrees.mutate(),
                        title: 'Remove unused PR and worker git worktrees from disk',
                        children: cleanupWorktrees.isPending ? 'Cleaning…' : 'Clean up stale worktrees',
                      }),
                      cleanupNotice
                        ? jsx('span', {
                            key: 'msg',
                            className: 'text-[0.6875rem] text-(--ui-text-tertiary)',
                            children: cleanupNotice,
                          })
                        : null,
                    ],
                  }),
                ],
              }),
              jsxs('div', {
                key: 'presets', className: 'flex min-h-0 min-w-0 flex-1 flex-col gap-1',
                children: [
                  jsxs('div', {
                    key: 'label', className: 'flex items-baseline justify-between gap-2',
                    children: [
                      jsx('span', {
                        key: 'l', className: 'text-[0.6875rem] font-medium uppercase tracking-wide text-(--ui-text-secondary)',
                        children: 'Model presets',
                      }),
                      jsx('span', {
                        key: 'n', className: 'text-[0.6875rem] tabular-nums text-(--ui-text-tertiary)',
                        children: `${rows.length}/${MAX_PRESET_ROWS}`,
                      }),
                    ],
                  }),
                  jsx('div', {
                    key: 'hint', className: 'text-[0.6875rem] leading-snug text-(--ui-text-tertiary)',
                    children: SETTINGS_HINT,
                  }),
                  rows.length
                    ? jsx('div', {
                        key: 'rows', className: 'flex min-h-0 flex-col gap-1 overflow-y-auto pr-1',
                        children: rows.map((row, index) => jsxs('div', {
                          key: `preset-${index}`, className: 'flex min-w-0 items-center gap-1',
                          children: [
                            jsx('input', {
                              key: 'name', type: 'text', value: row.name, placeholder: 'name',
                              'aria-label': `Preset ${index + 1} name`, autoComplete: 'off',
                              onChange: (event) => updateRow(index, 'name', event.target.value),
                              className: cn(GITHUB_FIELD, 'w-24 shrink-0'),
                            }),
                            jsx('input', {
                              key: 'model', type: 'text', value: row.model, placeholder: 'model',
                              'aria-label': `Preset ${index + 1} model`, autoComplete: 'off',
                              onChange: (event) => updateRow(index, 'model', event.target.value),
                              className: cn(GITHUB_FIELD, 'min-w-0 flex-1'),
                            }),
                            jsx('input', {
                              key: 'provider', type: 'text', value: row.provider, placeholder: 'provider',
                              'aria-label': `Preset ${index + 1} provider`, autoComplete: 'off',
                              onChange: (event) => updateRow(index, 'provider', event.target.value),
                              className: cn(GITHUB_FIELD, 'w-28 shrink-0'),
                            }),
                            jsx(GhostButton, {
                              key: 'remove', className: 'shrink-0', title: 'Remove this preset',
                              onClick: () => {
                                edited()
                                setRows((current) => current.filter((_, position) => position !== index))
                              },
                              children: '×',
                            }),
                          ],
                        })),
                      })
                    : jsx('div', {
                        key: 'empty', className: 'px-1 py-2 text-xs text-(--ui-text-tertiary)',
                        children: 'No presets yet — use the model picker, or add one here.',
                      }),
                  jsx('div', {
                    key: 'add', className: 'flex items-center gap-2',
                    children: jsx(GhostButton, {
                      disabled: rows.length >= MAX_PRESET_ROWS,
                      onClick: () => { edited(); setRows((current) => current.concat([blankPreset()])) },
                      children: '+ Add preset',
                    }),
                  }),
                ],
              }),
            ],
          }),
    ],
  })
}

// -- composer ---------------------------------------------------------------

// Drafts belong to a target, never to the page: one bucket per chat plus one
// for the new-chat box, so switching chats cannot bleed text between them.
const DRAFT_NEW_KEY = 'draft:new'
const DRAFT_DEBOUNCE = 300
// The box grows with its text up to eight rows, then scrolls. Fixed numbers
// keep the height deterministic even when computed styles are unavailable.
const COMPOSER_ROWS = 2
const COMPOSER_MAX_ROWS = 8
const COMPOSER_ROW_PX = 24
const COMPOSER_PAD_PX = 16
const COMPOSER_MIN_PX = COMPOSER_ROWS * COMPOSER_ROW_PX + COMPOSER_PAD_PX
const COMPOSER_MAX_PX = COMPOSER_MAX_ROWS * COMPOSER_ROW_PX + COMPOSER_PAD_PX
// The backend cap (8000) is the contract; the counter warns, it never clips.
const COMPOSER_MAX_CHARS = 8000
const COMPOSER_WARN_CHARS = 6400
const COMPOSER_COUNTER_AT = 1000
const RECENT_REPO_LIMIT = 5
const RECENT_REPO_LIST_ID = 'superagent-recent-repos'

function draftKeyFor(sessionId) {
  return sessionId ? `draft:${sessionId}` : DRAFT_NEW_KEY
}

/** Last repos this person sent work into. A datalist, not a picker: the field
 *  stays free text and the browser only offers what was used before. */
function readRecentRepos() {
  const raw = readStored('recentRepos', '')
  if (!raw) return []
  try {
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed
      .filter((entry) => typeof entry === 'string' && entry.trim())
      .slice(0, RECENT_REPO_LIMIT)
  } catch (error) {
    // A corrupt list is not worth an error card; the field still works.
    return []
  }
}

function rememberRepo(repo) {
  const value = String(repo || '').trim()
  if (!value) return readRecentRepos()
  const next = [value]
    .concat(readRecentRepos().filter((entry) => entry !== value))
    .slice(0, RECENT_REPO_LIMIT)
  writeStored('recentRepos', JSON.stringify(next))
  return next
}

/** Grows the textarea to its content, capped at eight rows; past the cap the
 *  box scrolls instead of pushing the transcript off the page. */
function fitTextarea(el) {
  if (!el || !el.style) return
  el.style.height = 'auto'
  const full = Number(el.scrollHeight) || 0
  const next = Math.min(Math.max(full, COMPOSER_MIN_PX), COMPOSER_MAX_PX)
  el.style.height = `${next}px`
  el.style.overflowY = full > COMPOSER_MAX_PX ? 'auto' : 'hidden'
}

/** `provider: model` for a preset (mirrors the backend's label). */
function presetLabelText(preset) {
  const name = String((preset && preset.model) || '')
  const provider = String((preset && preset.provider) || '')
  if (provider && name) return `${provider}: ${name}`
  return name || provider
}

/** One chip means one preset: the name, plus a ✕ that hands the model back to
 *  the picker. With no preset picked, a native select is the way in. */
function PresetControl({ presets, active, disabled, onPick, onClear }) {
  const list = Array.isArray(presets) ? presets : []
  if (!list.length) return null
  if (active) {
    const preset = list.find((entry) => String((entry && entry.name) || '') === active)
    return jsxs('span', {
      'data-preset': active,
      title: preset ? presetLabelText(preset) : active,
      className: cn('inline-flex max-w-[12rem] min-w-0 shrink-0 items-center gap-0.5 rounded',
        'border border-(--ui-stroke-secondary) bg-(--chrome-action-hover) px-1.5 py-0.5',
        'text-[0.6875rem] text-(--ui-text-secondary)'),
      children: [
        jsx('span', { key: 'n', className: 'min-w-0 truncate', children: active }),
        jsx('button', {
          key: 'x', type: 'button', disabled,
          'aria-label': `Clear preset ${active}`, title: 'Clear preset',
          onClick: onClear,
          className: cn('shrink-0 rounded px-0.5 leading-none text-(--ui-text-quaternary)',
            'hover:text-(--ui-text-secondary) disabled:opacity-50'),
          children: '×',
        }),
      ],
    })
  }
  return jsxs('select', {
    value: '', disabled, 'aria-label': 'Model preset',
    onChange: (event) => {
      const picked = list.find((entry) => String((entry && entry.name) || '') === event.target.value)
      if (picked) onPick(picked)
    },
    className: cn('max-w-[9rem] shrink-0 rounded border border-(--ui-stroke-secondary) bg-transparent',
      'px-1 py-0.5 text-[0.6875rem] text-(--ui-text-quaternary)'),
    children: [
      jsx('option', { key: 'none', value: '', children: 'Preset…' }),
      ...list.map((preset) => jsx('option', {
        key: String((preset && preset.name) || ''),
        value: String((preset && preset.name) || ''),
        children: String((preset && preset.name) || ''),
      })),
    ],
  })
}

/** One calm box: the textarea, then a meta row that carries the model/preset,
 *  the repo (new chats only) and the send/stop action. Nothing else is
 *  bordered — an empty composer is one 64px field plus one 30px row. */
function Composer(props) {
  const { mode, session, draft, onDraft, onSend, pending, error,
    model, onModelChange, repo, onRepoChange, models, inputRef,
    onSessionModel, modelPending, presets, presetName, onPreset, onPresetClear,
    onStop, stopPending, recentRepos, onUnarchive } = props
  const busy = isBusy(session)
  // An archived chat is history: the transcript stays readable, the box does
  // not pretend it can send. Unarchive (from here or the row's ⋯) restores it.
  const archived = mode === 'session' && !!(session && session.archived_at)
  const text = String(draft == null ? '' : draft)
  const canSend = !!text.trim() && !pending && !busy && !archived
  // The one sentence that explains a disabled Send, in priority order.
  const reason = archived
    ? 'Archived — unarchive to continue'
    : pending
      ? 'Sending…'
      : busy
        ? 'Waiting for this turn to finish'
        : !text.trim() ? 'Type a message' : ''
  const placeholder = archived
    ? 'Archived — unarchive to continue'
    : mode === 'session'
      ? (busy ? 'Waiting for this turn to finish…' : 'Reply to this chat…')
      : 'Describe the work…'
  const repos = Array.isArray(recentRepos) ? recentRepos : []
  const sentRef = useRef(false)

  // Auto-grow on every value change: the field is as tall as its text (up to
  // eight rows), so the page never jumps when a draft starts.
  useEffect(() => {
    fitTextarea(inputRef && inputRef.current)
  }, [draft, mode])

  // After a send, focus stays in the box — once it is enabled again.
  useEffect(() => {
    if (!sentRef.current || pending || busy) return
    sentRef.current = false
    const el = inputRef && inputRef.current
    if (el && typeof el.focus === 'function' && typeof document !== 'undefined' && document.activeElement !== el) {
      el.focus()
    }
  }, [pending, busy, draft])

  // Leaving the chat (or closing the new-chat box) drops a pending refocus:
  // a long turn must never pull the keyboard back minutes later.
  useEffect(() => { sentRef.current = false }, [mode, session && session.id])

  const send = () => {
    sentRef.current = true
    onSend()
  }

  const left = mode === 'session'
    ? [
        archived && typeof onUnarchive === 'function'
          ? jsx(GhostButton, {
              key: 'unarchive', className: 'h-6 shrink-0 px-1.5 py-0',
              onClick: onUnarchive, title: 'Restore this chat so it can continue',
              children: 'Unarchive',
            })
          : jsx(SessionModelControl, {
              key: 'model', session, models, pending: modelPending, onChange: onSessionModel,
            }),
      ]
    : [
        jsx(PresetControl, {
          key: 'preset', presets, active: presetName, disabled: pending,
          onPick: onPreset, onClear: onPresetClear,
        }),
        jsx(ModelPicker, { key: 'picker', value: model, models, disabled: pending, onChange: onModelChange }),
        jsxs('span', {
          key: 'repo', className: 'flex min-w-0 flex-1 basis-[9rem] items-center',
          children: [
            jsx('input', {
              key: 'i', type: 'text', value: repo, list: RECENT_REPO_LIST_ID,
              placeholder: 'repo path (optional)', 'aria-label': 'Repository path',
              onChange: (event) => onRepoChange(event.target.value),
              className: cn('min-w-0 flex-1 bg-transparent px-1 py-0.5 text-[0.6875rem]',
                'text-(--ui-text-secondary) placeholder:text-(--ui-text-quaternary)',
                'focus:outline-none disabled:opacity-60'),
            }),
            jsx('datalist', {
              key: 'd', id: RECENT_REPO_LIST_ID,
              children: repos.map((entry, index) => jsx('option', { key: `${index}:${entry}`, value: entry })),
            }),
          ],
        }),
      ]

  const counter = text.length > COMPOSER_COUNTER_AT
    ? jsx('span', {
        key: 'count',
        'data-over': text.length > COMPOSER_WARN_CHARS ? '1' : '0',
        title: `${text.length} of ${COMPOSER_MAX_CHARS} characters`,
        className: cn('shrink-0 tabular-nums',
          text.length > COMPOSER_WARN_CHARS
            ? 'text-(--ui-text-secondary)'
            : 'text-(--ui-text-quaternary)'),
        children: `${text.length} / ${COMPOSER_MAX_CHARS}`,
      })
    : null

  const sendButton = jsxs('button', {
    type: 'button', disabled: !canSend, onClick: send,
    'aria-label': 'Send', title: reason || 'Send this message (Enter)',
    className: cn('inline-flex h-6 shrink-0 items-center gap-1 rounded bg-(--chrome-action-hover)',
      'px-2 text-[0.6875rem] font-medium text-(--ui-text-secondary) hover:opacity-90',
      !canSend && 'opacity-40'),
    children: [
      jsx('span', { key: 'g', 'aria-hidden': 'true', children: '↑' }),
      jsx('span', { key: 'l', className: 'hidden sm:inline', children: pending ? 'Sending…' : 'Send' }),
    ],
  })

  return jsxs('div', {
    'data-composer': mode,
    className: cn('flex shrink-0 flex-col overflow-hidden rounded-lg border',
      'border-(--ui-stroke-secondary) bg-(--ui-bg-primary)'),
    children: [
      error
        ? jsx('div', {
            key: 'err', role: 'alert',
            className: 'px-3 pt-2 text-[0.6875rem] text-(--ui-text-secondary)',
            children: error,
          })
        : null,
      jsx('textarea', {
        key: 'input', ref: inputRef, rows: COMPOSER_ROWS, value: draft,
        disabled: pending || busy || archived, placeholder,
        'aria-label': archived
          ? 'Archived chat (read-only)'
          : mode === 'session' ? 'Reply to this chat' : 'Describe the work',
        onChange: (event) => onDraft(event.target.value),
        onKeyDown: (event) => {
          if (event.key === 'Escape') {
            // Escape gives the keyboard back; it never clears the draft.
            if (inputRef && inputRef.current) inputRef.current.blur()
            return
          }
          if (event.key !== 'Enter') return
          // An IME candidate window owns Enter; never send mid-composition.
          if (event.nativeEvent && event.nativeEvent.isComposing) return
          if (event.shiftKey) return
          // Enter, Ctrl+Enter and Cmd+Enter send. Only this box listens, so no
          // global shortcut is claimed anywhere else on the page.
          event.preventDefault()
          if (!canSend) return
          send()
        },
        className: cn('min-h-16 w-full resize-none bg-transparent px-3 pt-2 pb-1',
          'text-sm leading-relaxed text-(--ui-text-secondary)',
          'placeholder:text-(--ui-text-quaternary) focus:outline-none disabled:opacity-60'),
      }),
      jsxs('div', {
        key: 'meta',
        className: cn('flex min-w-0 flex-wrap items-center gap-1.5 px-2 pt-0.5 pb-1.5',
          'text-[0.6875rem] text-(--ui-text-quaternary)'),
        children: [
          ...left,
          jsxs('div', {
            key: 'act', className: 'ml-auto flex shrink-0 items-center gap-1',
            children: [
              counter,
              busy && typeof onStop === 'function'
                ? jsx(GhostButton, {
                    key: 'stop', className: 'h-6 shrink-0 px-1.5 py-0',
                    onClick: onStop, disabled: stopPending,
                    title: 'Stop this turn', 'aria-label': 'Stop this turn',
                    children: stopPending ? 'Stopping…' : 'Stop',
                  })
                : null,
              jsx(Tip, { key: 'send', label: reason || 'Send this message (Enter)', children: sendButton }),
            ],
          }),
        ],
      }),
    ],
  })
}

// -- handoff: diff panel + comment confirm ----------------------------------

// A file's porcelain state as the one or two letters git users expect.
const DIFF_LETTERS = {
  modified: 'M', added: 'A', deleted: 'D', renamed: 'R', copied: 'C',
  untracked: '??', conflict: '!', typechange: 'T', changed: '*',
}
// A 5 000-file status is not a review: show the head and say how much is left.
const DIFF_FILE_ROWS = 200

/** Split a unified patch into one block per file, keeping each `diff --git`
 *  header so the raw text stays readable. Adds/deletes are counted per block
 *  for the header badges; a preamble before the first file (truncation notice,
 *  binary marker) becomes a pathless block instead of being dropped. */
function splitPatch(patch) {
  const text = String(patch || '')
  if (!text.trim()) return []
  const blocks = []
  let current = null
  for (const line of text.split('\n')) {
    if (line.startsWith('diff --git ')) {
      const match = line.match(/^diff --git a\/(.+?) b\/(.+)$/)
      current = { path: match ? match[2] : '', lines: [line] }
      blocks.push(current)
    } else if (current) {
      current.lines.push(line)
    } else {
      current = { path: '', lines: [line] }
      blocks.push(current)
    }
  }
  return blocks.map((block, index) => ({
    key: `b${index}`,
    path: block.path,
    body: block.lines.join('\n'),
    adds: block.lines.filter((l) => l.startsWith('+') && !l.startsWith('+++')).length,
    dels: block.lines.filter((l) => l.startsWith('-') && !l.startsWith('---')).length,
  }))
}

/** Clipboard write that never rejects at the caller: a shell without the API
 *  resolves false, so the button just does not announce "Copied". */
function copyToClipboard(value) {
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard
      && typeof navigator.clipboard.writeText === 'function') {
      return navigator.clipboard.writeText(String(value || '')).then(() => true, () => false)
    }
  } catch (error) {
    // Fall through to the no-op.
  }
  return Promise.resolve(false)
}

/** Collapsible "what did this session change" panel, between transcript and
 *  composer. The query is owned by ChatCenter (the header needs its count);
 *  this component only renders it, one collapsible section per file. */
function DiffPanel({ session, query, onClose }) {
  const data = (query && query.data) || {}
  const files = Array.isArray(data.files) ? data.files : []
  const shown = files.slice(0, DIFF_FILE_ROWS)
  const hidden = files.length - shown.length
  const failed = (data && data.ok === false && data.error) || ''
  const error = failed || errorText(query && query.error)
  const fetching = !!(query && query.isFetching)
  const blocks = useMemo(() => splitPatch(data.patch), [data.patch])
  const [collapsed, setCollapsed] = useState({})
  const [copied, setCopied] = useState('')
  const blockRefs = useRef([])
  const copyTimer = useRef(null)

  // A new chat means new files: stale collapse/copy state would point at the
  // previous diff's indexes.
  useEffect(() => {
    setCollapsed({})
    setCopied('')
  }, [session && session.id])
  useEffect(() => () => {
    if (copyTimer.current) clearTimeout(copyTimer.current)
  }, [])

  const copy = (value, tag) => {
    copyToClipboard(value).then((ok) => {
      if (!ok) return
      setCopied(tag)
      if (copyTimer.current) clearTimeout(copyTimer.current)
      copyTimer.current = setTimeout(() => setCopied(''), 1200)
    })
  }

  // `git status` paths and patch paths normally agree; a rename can disagree,
  // so fall back to a suffix match and otherwise leave the row inert.
  const blockIndex = (path) => blocks.findIndex((block) => block.path && (
    block.path === path || block.path.endsWith(`/${path}`) || path.endsWith(`/${block.path}`)
  ))

  const jump = (index) => {
    const element = blockRefs.current[index]
    if (element && typeof element.scrollIntoView === 'function') {
      element.scrollIntoView({ block: 'nearest' })
    }
  }

  return jsxs('div', {
    className: cn('flex max-h-[22rem] min-h-0 flex-col gap-1.5 overflow-y-auto rounded border',
      'border-(--ui-stroke-secondary) p-2'),
    children: [
      jsxs('div', {
        key: 'head', className: 'flex shrink-0 flex-wrap items-center justify-between gap-2 text-[0.6875rem]',
        children: [
          jsxs('div', {
            key: 'm', className: 'flex min-w-0 flex-wrap items-baseline gap-2',
            children: [
              jsx('span', { key: 't', className: 'text-(--ui-text-secondary)', children: 'Diff' }),
              jsx('span', {
                key: 'n', className: 'text-(--ui-text-quaternary)',
                children: `${files.length} file${files.length === 1 ? '' : 's'}`,
              }),
              data.truncated
                ? jsx('span', { key: 'tr', className: 'text-(--ui-text-quaternary)', children: 'patch truncated' })
                : null,
              session && session.repo
                ? jsx('span', {
                    key: 'r', className: 'min-w-0 max-w-[20rem] truncate font-mono text-(--ui-text-quaternary)',
                    children: session.repo,
                  })
                : null,
            ],
          }),
          jsxs('div', {
            key: 'a', className: 'flex shrink-0 items-center gap-1',
            children: [
              data.patch ? jsx(GhostButton, {
                key: 'copy', title: 'Copy the whole patch',
                onClick: () => copy(String(data.patch), 'patch'),
                children: copied === 'patch' ? 'Copied' : 'Copy patch',
              }) : null,
              jsx(GhostButton, {
                key: 'refresh', onClick: () => query && query.refetch(), disabled: fetching,
                children: fetching ? 'Refreshing…' : 'Refresh',
              }),
              jsx(GhostButton, { key: 'close', onClick: onClose, children: 'Close' }),
            ],
          }),
        ],
      }),
      query && query.isLoading
        ? jsx('div', { key: 'load', className: 'text-[0.6875rem] text-(--ui-text-tertiary)', children: 'Reading the diff…' })
        : null,
      error
        ? jsx('div', {
            key: 'err', className: 'break-words text-[0.6875rem] text-(--ui-text-quaternary)',
            children: error,
          })
        : null,
      !error && data.ok && !files.length
        ? jsx('div', {
            key: 'none', className: 'text-[0.6875rem] text-(--ui-text-tertiary)',
            children: 'No changes in this repository.',
          })
        : null,
      shown.length
        ? jsxs('div', {
            key: 'files', className: 'flex shrink-0 flex-col gap-0.5',
            children: [
              ...shown.map((file, index) => {
                const target = blockIndex(file.path)
                const cells = [
                  jsx('span', {
                    key: 's', className: 'w-5 shrink-0 text-(--ui-text-quaternary)',
                    children: DIFF_LETTERS[file.status] || '?',
                  }),
                  jsx('span', {
                    key: 'p', className: 'min-w-0 truncate text-(--ui-text-tertiary)',
                    children: file.path,
                  }),
                ]
                // Only a file that has a patch section is a jump target;
                // an untracked or binary row stays a plain line.
                return target >= 0
                  ? jsx('button', {
                      key: `f${index}`, type: 'button', onClick: () => jump(target),
                      title: 'Jump to this file in the patch',
                      className: cn('flex min-w-0 items-baseline gap-1.5 rounded px-0.5 text-left',
                        'font-mono text-[0.6875rem] hover:bg-(--chrome-action-hover)'),
                      children: cells,
                    })
                  : jsx('div', {
                      key: `f${index}`,
                      className: 'flex min-w-0 items-baseline gap-1.5 px-0.5 font-mono text-[0.6875rem]',
                      children: cells,
                    })
              }),
              hidden > 0
                ? jsx('div', {
                    key: 'more', className: 'text-[0.6875rem] text-(--ui-text-quaternary)',
                    children: `… ${hidden} more file${hidden === 1 ? '' : 's'}`,
                  })
                : null,
            ],
          })
        : null,
      data.stat
        ? jsx('pre', {
            key: 'stat',
            className: cn('shrink-0 overflow-x-auto whitespace-pre font-mono text-[0.6875rem]',
              'leading-[1.35] text-(--ui-text-quaternary)'),
            children: String(data.stat),
          })
        : null,
      blocks.length
        ? jsxs('div', {
            key: 'blocks', className: 'flex min-h-0 shrink-0 flex-col gap-1',
            children: blocks.map((block, index) => {
              const isCollapsed = !!collapsed[index]
              return jsxs('div', {
                key: block.key,
                ref: (element) => { blockRefs.current[index] = element },
                className: 'rounded border border-(--ui-stroke-secondary)',
                children: [
                  jsxs('div', {
                    key: 'h',
                    className: 'flex min-w-0 items-center gap-1.5 px-1.5 py-0.5 font-mono text-[0.6875rem]',
                    children: [
                      jsx('button', {
                        key: 't', type: 'button',
                        'aria-expanded': !isCollapsed,
                        title: block.path || 'patch',
                        onClick: () => setCollapsed((previous) => ({
                          ...previous, [index]: !previous[index],
                        })),
                        className: cn('min-w-0 flex-1 truncate text-left',
                          'text-(--ui-text-secondary) hover:text-(--ui-text-primary)'),
                        children: `${isCollapsed ? '▸' : '▾'} ${block.path || '(patch)'}`,
                      }),
                      block.adds
                        ? jsx('span', {
                            key: 'a', className: 'shrink-0 text-(--ui-text-quaternary)',
                            children: `+${block.adds}`,
                          })
                        : null,
                      block.dels
                        ? jsx('span', {
                            key: 'd', className: 'shrink-0 text-(--ui-text-quaternary)',
                            children: `-${block.dels}`,
                          })
                        : null,
                      block.path
                        ? jsx(GhostButton, {
                            key: 'c', title: 'Copy this path',
                            onClick: () => copy(block.path, block.key),
                            children: copied === block.key ? 'Copied' : 'Copy',
                          })
                        : null,
                    ],
                  }),
                  isCollapsed
                    ? null
                    : jsx('pre', {
                        key: 'b',
                        className: cn('overflow-x-auto whitespace-pre border-t',
                          'border-(--ui-stroke-secondary) px-1.5 py-1 font-mono',
                          'text-[0.6875rem] leading-[1.35] text-(--ui-text-quaternary)'),
                        children: block.body,
                      }),
                ],
              })
            }),
          })
        : null,
    ],
  })
}

/** Inline confirm (never window.confirm): the generated summary is editable,
 *  Post is the only thing that talks to GitHub, and the URL is shown after. */
function CommentBox({ session, onClose }) {
  const queryClient = useQueryClient()
  const preview = useQuery({
    queryKey: [ID, 'chat', session.id, 'comment'],
    queryFn: () => fetchCommentPreview(session.id),
    retry: false,
  })
  const [text, setText] = useState('')
  const [edited, setEdited] = useState(false)
  const [verdict, setVerdict] = useState('COMMENT')

  // Prefill from the server once; a preview refetch must never overwrite what
  // the user typed.
  useEffect(() => {
    if (edited || !preview.data) return
    if (typeof preview.data.body === 'string') setText(preview.data.body)
  }, [preview.data, edited])

  const post = useMutation({
    mutationFn: () => {
      if (verdict === 'COMMENT') {
        return rest(`/chats/${encodeURIComponent(session.id)}/comment`, {
          method: 'POST', body: { body: text },
        })
      }
      return rest(`/chats/${encodeURIComponent(session.id)}/review`, {
        method: 'POST', body: { event: verdict, body: text },
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [ID, 'chat', session.id] })
      queryClient.invalidateQueries({ queryKey: [ID, 'chats'] })
      queryClient.invalidateQueries({ queryKey: [ID, 'towns'] })
    },
  })

  const posted = post.data && post.data.ok ? post.data : null
  const canPost = (verdict === 'APPROVE' || !!String(text || '').trim()) && !post.isPending
  const error = errorText(post.error) || errorText(preview.error) ||
    (post.data && post.data.ok === false && post.data.error) || ''

  return jsxs('div', {
    className: cn('flex max-h-[24rem] min-h-0 flex-col gap-1.5 overflow-y-auto rounded border',
      'border-(--ui-stroke-secondary) p-2'),
    children: [
      jsxs('div', {
        key: 'head', className: 'flex shrink-0 flex-wrap items-center justify-between gap-2 text-[0.6875rem]',
        children: [
          jsx('span', {
            key: 't', className: 'text-(--ui-text-secondary)',
            children: `Comment on PR #${session.pr_number}`,
          }),
          jsxs('div', {
            key: 'verdict', className: 'flex items-center gap-1',
            children: [
              jsx(ModeButton, {
                key: 'comment', label: 'Comment',
                active: verdict === 'COMMENT', onClick: () => setVerdict('COMMENT'),
              }),
              jsx(ModeButton, {
                key: 'approve', label: 'Approve ✓',
                active: verdict === 'APPROVE', onClick: () => setVerdict('APPROVE'),
              }),
              jsx(ModeButton, {
                key: 'changes', label: 'Request changes ✕',
                active: verdict === 'REQUEST_CHANGES', onClick: () => setVerdict('REQUEST_CHANGES'),
              }),
            ],
          }),
          jsx('span', {
            key: 's', className: 'text-(--ui-text-quaternary)',
            children: 'Nothing is posted until you press Post.',
          }),
        ],
      }),
      preview.isLoading
        ? jsx('div', { key: 'load', className: 'text-[0.6875rem] text-(--ui-text-tertiary)', children: 'Preparing the summary…' })
        : jsx('textarea', {
            key: 'body', rows: 7, value: text,
            onChange: (event) => { setEdited(true); setText(event.target.value) },
            placeholder: 'What should the pull request hear?',
            className: cn('min-h-24 w-full resize-y rounded border border-(--ui-stroke-secondary)',
              'bg-transparent px-2 py-1.5 font-mono text-[0.6875rem] text-(--ui-text-secondary)',
              'placeholder:text-(--ui-text-quaternary) focus:outline-none disabled:opacity-60'),
          }),
      error
        ? jsx('div', {
            key: 'err', className: 'break-words text-[0.6875rem] text-(--ui-text-quaternary)',
            children: error,
          })
        : null,
      posted
        ? jsxs('div', {
            key: 'ok', className: 'flex flex-wrap items-center gap-2 text-[0.6875rem] text-(--ui-text-secondary)',
            children: [
              jsx('span', {
                key: 'm',
                children: verdict === 'APPROVE'
                  ? 'PR approved.'
                  : (verdict === 'REQUEST_CHANGES' ? 'Changes requested.' : 'Comment posted.'),
              }),
              posted.url
                ? jsx('button', {
                    key: 'open', type: 'button', onClick: () => openUrl(posted.url),
                    className: 'text-(--ui-text-secondary) underline',
                    children: 'Open on GitHub',
                  })
                : null,
            ],
          })
        : null,
      jsxs('div', {
        key: 'actions', className: 'flex shrink-0 items-center gap-1',
        children: [
          jsx(GhostButton, {
            key: 'post', onClick: () => { if (canPost) post.mutate() }, disabled: !canPost,
            children: post.isPending
              ? 'Posting…'
              : (verdict === 'APPROVE'
                  ? 'Approve PR'
                  : (verdict === 'REQUEST_CHANGES' ? 'Request changes' : 'Post')),
          }),
          jsx(GhostButton, {
            key: 'cancel', onClick: onClose, disabled: post.isPending, children: 'Cancel',
          }),
        ],
      }),
    ],
  })
}

function CreatePrBox({ session, onClose }) {
  const queryClient = useQueryClient()
  const [title, setTitle] = useState(() => sessionTitle(session))
  const [branch, setBranch] = useState(() => String(session.branch_name || '').trim())
  const [body, setBody] = useState(() => String(session.prompt || '').trim())
  const [draft, setDraft] = useState(false)

  const createPr = useMutation({
    mutationFn: () => rest(`/chats/${encodeURIComponent(session.id)}/pr`, {
      method: 'POST',
      body: {
        title: String(title || '').trim(),
        branch: String(branch || '').trim() || undefined,
        body: String(body || '').trim(),
        draft: !!draft,
      },
    }),
    onSuccess: () => {
      invalidateChats(queryClient, session.id)
      invalidateGithub(queryClient)
    },
  })

  const created = createPr.data && createPr.data.ok ? createPr.data : null
  const prUrl = created && created.pr && created.pr.html_url
  const canCreate = !!String(title || '').trim() && !createPr.isPending
  const error = errorText(createPr.error) ||
    (createPr.data && createPr.data.ok === false && createPr.data.error) || ''

  return jsxs('div', {
    'data-create-pr-box': session.id,
    className: cn('flex max-h-[24rem] min-h-0 flex-col gap-1.5 overflow-y-auto rounded border',
      'border-(--ui-stroke-secondary) p-2'),
    children: [
      jsxs('div', {
        key: 'head', className: 'flex shrink-0 flex-wrap items-center justify-between gap-2 text-[0.6875rem]',
        children: [
          jsx('span', { key: 't', className: 'font-medium text-(--ui-text-secondary)', children: 'Create Pull Request' }),
          jsx('span', { key: 's', className: 'text-(--ui-text-quaternary)', children: 'Commits, pushes branch & opens a GitHub PR.' }),
        ],
      }),
      jsxs('div', {
        key: 'fields', className: 'flex flex-wrap gap-1.5',
        children: [
          jsx('input', {
            key: 'title', type: 'text', value: title, placeholder: 'PR title',
            'aria-label': 'Pull request title',
            onChange: (e) => setTitle(e.target.value),
            className: cn(GITHUB_FIELD, 'flex-1'),
          }),
          jsx('input', {
            key: 'branch', type: 'text', value: branch, placeholder: 'branch (auto if blank)',
            'aria-label': 'Branch name',
            onChange: (e) => setBranch(e.target.value),
            className: cn(GITHUB_FIELD, 'w-44 font-mono'),
          }),
        ],
      }),
      jsx('textarea', {
        key: 'body', rows: 4, value: body,
        onChange: (e) => setBody(e.target.value),
        placeholder: 'Pull request description…',
        className: cn('min-h-16 w-full resize-y rounded border border-(--ui-stroke-secondary)',
          'bg-transparent px-2 py-1.5 font-mono text-[0.6875rem] text-(--ui-text-secondary)',
          'placeholder:text-(--ui-text-quaternary) focus:outline-none'),
      }),
      jsxs('label', {
        key: 'draft', className: 'flex items-center gap-1.5 text-[0.6875rem] text-(--ui-text-tertiary)',
        children: [
          jsx('input', {
            key: 'i', type: 'checkbox', checked: !!draft,
            onChange: (e) => setDraft(!!e.target.checked),
            className: 'accent-(--ui-accent)',
          }),
          jsx('span', { key: 'l', children: 'Open as draft PR' }),
        ],
      }),
      error
        ? jsx('div', { key: 'err', className: 'break-words text-[0.6875rem] text-(--ui-text-quaternary)', children: error })
        : null,
      created
        ? jsxs('div', {
            key: 'ok', className: 'flex flex-wrap items-center gap-2 text-[0.6875rem] text-(--ui-text-secondary)',
            children: [
              jsx('span', { key: 'm', children: `PR #${created.pr && created.pr.number} created on ${created.branch || 'branch'}.` }),
              prUrl
                ? jsx('button', {
                    key: 'open', type: 'button', onClick: () => openUrl(prUrl),
                    className: 'text-(--ui-text-secondary) underline',
                    children: 'Open on GitHub',
                  })
                : null,
            ],
          })
        : null,
      jsxs('div', {
        key: 'actions', className: 'flex shrink-0 items-center gap-1',
        children: [
          jsx(GhostButton, {
            key: 'create', onClick: () => { if (canCreate) createPr.mutate() }, disabled: !canCreate,
            children: createPr.isPending ? 'Creating PR…' : 'Create PR',
          }),
          jsx(GhostButton, {
            key: 'cancel', onClick: onClose, disabled: createPr.isPending, children: 'Cancel',
          }),
        ],
      }),
    ],
  })
}

// -- chat center ------------------------------------------------------------

/** Secondary chat actions behind one ⋯: the SDK menu when this shell has it,
 *  a plain popover list otherwise. Neither is a visible button, so the header
 *  keeps at most Stop / Diff / Open. */
function OverflowItem({ item }) {
  return jsx('button', {
    type: 'button',
    role: 'menuitem',
    disabled: !!item.disabled,
    onClick: item.onSelect,
    className: cn('flex w-full min-w-0 items-center rounded px-2 py-1 text-left text-xs',
      'text-(--ui-text-secondary) hover:bg-(--chrome-action-hover)',
      item.disabled && 'opacity-50'),
    children: item.label,
  })
}

function OverflowMenu({ items, label }) {
  const [open, setOpen] = useState(false)
  if (!items || !items.length) return null
  const trigger = jsx('button', {
    type: 'button',
    'aria-label': label || 'More actions',
    'aria-haspopup': 'menu',
    'aria-expanded': !!open,
    title: label || 'More actions',
    onClick: () => setOpen((value) => !value),
    className: cn('inline-flex h-6 w-6 items-center justify-center rounded leading-none',
      'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-(--ui-text-secondary)',
      open && 'bg-(--chrome-action-hover) text-(--ui-text-secondary)'),
    children: '⋯',
  })
  const entries = items.map((item) => jsx(OverflowItem, { key: item.id, item }))
  if (DropdownMenu && DropdownMenuTrigger && DropdownMenuContent) {
    return jsxs(DropdownMenu, {
      open, onOpenChange: setOpen,
      children: [
        jsx(DropdownMenuTrigger, { key: 'trigger', asChild: true, children: trigger }),
        jsx(DropdownMenuContent, { key: 'content', align: 'end', className: 'w-52 p-1', children: entries }),
      ],
    })
  }
  return jsxs('div', {
    className: 'relative',
    children: [
      trigger,
      open
        ? jsx('div', {
            role: 'menu',
            'aria-label': label || 'More actions',
            className: cn('absolute right-0 top-7 z-30 flex w-52 flex-col gap-0.5 rounded border',
              'border-(--ui-stroke-secondary) bg-background p-1 shadow-md'),
            children: entries,
          })
        : null,
    ],
  })
}

/** Two lines and three actions: title + status dot, then model · repo · PR.
 *  Comment-on-PR and the workspace tile live behind the ⋯ menu. */
function ChatHeader({ session, tile, diff, comment, createPr, retry, exportMd }) {
  const canOpen = !!session.hermes_session_id && !!(host && typeof host.openSession === 'function')
  const canTile = !tile && !!(host && typeof host.openWorkspace === 'function')
  const repo = String(session.repo || '').trim()
  const detail = String(session.error || '').trim()

  const overflow = []
  if (!tile && comment) {
    overflow.push({
      id: 'comment',
      label: comment.open ? 'Close the comment box' : `Comment on PR #${session.pr_number}`,
      disabled: comment.disabled,
      onSelect: comment.onToggle,
    })
  }
  if (!tile && createPr) {
    overflow.push({
      id: 'create-pr',
      label: createPr.open ? 'Close Create PR' : 'Create Pull Request…',
      disabled: createPr.disabled,
      onSelect: createPr.onToggle,
    })
  }
  if (exportMd) {
    overflow.push({
      id: 'export',
      label: exportMd.pending ? 'Preparing export…' : 'Export as Markdown',
      disabled: exportMd.pending,
      onSelect: exportMd.onExport,
    })
  }
  if (canTile) {
    overflow.push({ id: 'tile', label: 'Open in a workspace tile', onSelect: () => openChatTile(session) })
  }

  return jsxs('div', {
    className: 'flex items-start justify-between gap-2',
    children: [
      jsxs('div', {
        key: 'meta', className: 'flex min-w-0 flex-col gap-0.5',
        children: [
          jsxs('div', {
            key: 'title', className: 'flex min-w-0 items-center gap-1.5',
            children: [
              jsx(ChatDot, { key: 'dot', status: session.status }),
              jsx('span', {
                key: 't', title: sessionTitle(session),
                className: 'min-w-0 truncate text-sm font-medium text-(--ui-text-secondary)',
                children: sessionTitle(session),
              }),
              // Archived is a state, not a badge of honour: one quiet pill is
              // enough, and the composer explains what to do about it.
              session.archived_at
                ? jsx('span', {
                    key: 'arch',
                    title: 'Archived: read-only until you unarchive it',
                    className: cn('shrink-0 rounded border border-(--ui-stroke-secondary) px-1',
                      'text-[0.625rem] font-normal leading-4 text-(--ui-text-quaternary)'),
                    children: 'Archived',
                  })
                : null,
            ],
          }),
          jsxs('div', {
            key: 'line', className: 'flex min-w-0 items-center gap-1.5 text-[0.6875rem] text-(--ui-text-quaternary)',
            children: [
              jsx('span', { key: 'm', className: 'shrink-0', children: sessionModelLabel(session) }),
              repo
                ? jsxs('span', {
                    key: 'r', className: 'flex min-w-0 items-center gap-1.5',
                    children: [
                      jsx('span', { key: 'd', className: 'shrink-0', children: '·' }),
                      jsx('span', {
                        key: 'p', title: repo,
                        className: 'min-w-0 max-w-[18rem] truncate font-mono',
                        children: clipMiddle(repo, 48),
                      }),
                    ],
                  })
                : null,
              session.pr_number
                ? jsx(PrBadge, { key: 'pr', session })
                : (session.pr_url
                    ? jsx('button', {
                        key: 'prl', type: 'button', onClick: () => openUrl(session.pr_url),
                        className: 'shrink-0 text-(--ui-text-secondary) underline',
                        children: 'Pull request',
                      })
                    : null),
            ],
          }),
          detail || session.status === 'failed'
            ? jsx('span', {
                key: 'err',
                title: detail || 'No error text was recorded for this turn.',
                className: 'min-w-0 truncate text-[0.6875rem] text-(--ui-text-quaternary)',
                children: detail ? `error: ${clip(detail, 200)}` : 'failed: no error recorded',
              })
            : null,
        ],
      }),
      jsxs('div', {
        key: 'actions', className: 'flex shrink-0 flex-wrap items-center justify-end gap-1',
        children: [
          // Only a stopped turn offers this: the prompt is already stored, so
          // the click relaunches it instead of asking the user to retype it.
          retry ? jsx(GhostButton, {
            key: 'retry',
            title: 'Run the last prompt again in this chat',
            disabled: retry.pending,
            onClick: () => retry.onRetry(),
            children: retry.pending ? 'Retrying…' : 'Retry',
          }) : null,
          diff ? jsx(GhostButton, {
            key: 'diff',
            title: 'Show what this session changed in its repository',
            'aria-expanded': !!diff.open,
            onClick: diff.onToggle,
            children: diff.open ? 'Diff ▴' : (diff.count ? `Diff (${diff.count})` : 'Diff'),
          }) : null,
          canOpen ? jsx(GhostButton, {
            key: 'open',
            title: 'Open this chat in Hermes',
            onClick: () => { host.openSession(session.hermes_session_id).catch(() => {}) },
            children: 'Open',
          }) : null,
          jsx(OverflowMenu, { key: 'more', items: overflow, label: 'More actions' }),
        ],
      }),
    ],
  })
}

function ChatCenter(props) {
  const { summary, detail, canComment, sessions, onSelect, banner, optimistic, retry, exportMd, ...composerProps } = props
  const session = (detail && detail.data && detail.data.session) || summary
  const sessionId = session && session.id
  const busy = isBusy(session)
  const [diffOpen, setDiffOpen] = useState(false)
  const [commentOpen, setCommentOpen] = useState(false)
  const [createPrOpen, setCreatePrOpen] = useState(false)
  // Unconditional hooks: `enabled` stays false until the panel is opened.
  const diffQuery = useChatDiff(sessionId, diffOpen, busy)

  // Switching chats closes both panels: neither belongs to the next session.
  useEffect(() => {
    setDiffOpen(false)
    setCommentOpen(false)
    setCreatePrOpen(false)
  }, [sessionId])

  if (!session) {
    return jsxs('div', {
      className: 'mx-auto flex min-h-0 min-w-0 w-full w-[52vw] flex-1 flex-col gap-3 overflow-hidden',
      style: {
        width: '100%',
        minWidth: '100%',
        maxWidth: '100%',
        flex: '1 1 0%',
        minHeight: 0,
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
        boxSizing: 'border-box',
      },
      children: [
        banner ? jsx('div', { key: 'banner', className: 'shrink-0', children: banner }) : null,
        jsx('div', {
          key: 'empty',
          className: 'flex min-h-0 flex-1 items-center justify-center p-4 text-center text-xs text-(--ui-text-tertiary)',
          children: 'Start a chat — pick a model and describe the work.',
        }),
        jsx(LiveWorkerStrip, { key: 'workers', sessions, selectedId: null, onSelect }),
        jsx(Composer, { key: 'composer', mode: 'new', ...composerProps }),
      ],
    })
  }

  const hasRepo = !!String(session.repo || '').trim()
  const diffCount = diffQuery.data && Array.isArray(diffQuery.data.files)
    ? diffQuery.data.files.length
    : 0
  const diffAction = hasRepo
    ? { open: diffOpen, count: diffCount, onToggle: () => setDiffOpen((value) => !value) }
    : null
  const commentAction = session.pr_number && canComment
    ? {
        open: commentOpen, disabled: busy,
        onToggle: () => setCommentOpen((value) => !value),
      }
    : null
  const createPrAction = hasRepo && canComment
    ? {
        open: createPrOpen, disabled: busy,
        onToggle: () => setCreatePrOpen((value) => !value),
      }
    : null

  return jsxs('div', {
    className: 'mx-auto flex min-h-0 min-w-0 w-full w-[52vw] flex-1 flex-col gap-3 overflow-hidden',
    style: {
      width: '100%',
      minWidth: '100%',
      maxWidth: '100%',
      flex: '1 1 0%',
      minHeight: 0,
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden',
      boxSizing: 'border-box',
    },
    children: [
      jsx(ChatHeader, {
        key: 'head', session,
        diff: diffAction, comment: commentAction, createPr: createPrAction, retry, exportMd,
      }),
      banner ? jsx('div', { key: 'banner', className: 'shrink-0', children: banner }) : null,
      detail && detail.isError
        ? jsx(ErrorCard, {
            key: 'err', title: `Could not load ${session.id}.`, error: detail.error,
            onRetry: () => detail.refetch(),
          })
        : jsx(Transcript, { key: 'transcript', session, optimistic }),
      diffOpen && hasRepo
        ? jsx(DiffPanel, {
            key: 'diff', session, query: diffQuery, onClose: () => setDiffOpen(false),
          })
        : null,
      commentOpen && commentAction
        ? jsx(CommentBox, {
            key: 'comment', session, onClose: () => setCommentOpen(false),
          })
        : null,
      createPrOpen && createPrAction
        ? jsx(CreatePrBox, {
            key: 'create-pr', session, onClose: () => setCreatePrOpen(false),
          })
        : null,
      jsx(LiveWorkerStrip, { key: 'workers', sessions, selectedId: sessionId, onSelect }),
      jsx(Composer, { key: 'composer', mode: 'session', session, ...composerProps }),
    ],
  })
}

/** Read-only transcript in a main-area tile (newer shells). The composer
 *  stays on the page; older shells never see the ⧉ button. */
function ChatTile({ sessionId }) {
  const detail = useChat(sessionId)
  const session = detail.data && detail.data.session
  if (!session) {
    return jsx('div', { className: 'p-4 text-xs text-(--ui-text-tertiary)', children: 'Loading chat…' })
  }
  return jsxs('div', {
    className: 'mx-auto flex h-full min-h-0 w-[52vw] flex-col gap-2 p-3',
    children: [
      jsx(ChatHeader, { key: 'head', session, tile: true }),
      jsx(Transcript, { key: 'transcript', session }),
    ],
  })
}

function openChatTile(session) {
  try {
    host.openWorkspace(`superagent-chat-${session.id}`, {
      title: sessionTitle(session),
      render: () => jsx(ChatTile, { sessionId: session.id }),
    })
  } catch (error) {
    // Older shells: the button is hidden by the same typeof check.
  }
}

// -- live workers (a strip under the transcript, not a third column) --------

/** The last thing this worker did, on one line. A failed card's first
 *  question is "why", so its stored error wins when no feed arrived. */
function lastEventText(session) {
  const events = Array.isArray(session.recent_events) ? session.recent_events.filter(Boolean) : []
  const latest = events.length ? events[0] : null
  let text = ''
  if (latest) {
    const kind = String(latest.kind || '').trim()
    const detail = String(latest.detail || '').replace(/\s+/g, ' ').trim()
    text = detail ? `${kind}: ${detail}` : kind
  }
  if (!text) {
    const failure = session.status === 'failed' ? firstLine(session.error) : ''
    text = failure || String(session.last_event || '').replace(/\s+/g, ' ').trim()
  }
  if (text) return clip(text, 120)
  if (session.status === 'queued') return 'Queued — waiting for a slot'
  if (session.status === 'review') return 'Finished — ready to review'
  if (session.status === 'failed') return 'Failed'
  return ''
}

function WorkerCard({ session, selected, onSelect }) {
  const busy = isBusy(session)
  // Client-only ticker: it repaints this card every second and touches no
  // query, so polling budgets stay exactly as configured.
  const now = useNow(busy)
  const timing = busy
    ? (session.status === 'queued'
        ? `queued ${fmtSeconds(liveElapsedSeconds(session, now))}`
        : fmtSeconds(liveElapsedSeconds(session, now)))
    : timingLabel(session)
  const event = lastEventText(session)
  return jsxs('button', {
    type: 'button',
    onClick: () => onSelect(session.id),
    title: `${sessionTitle(session)} · ${sessionModelLabel(session)}`,
    'aria-current': selected ? 'true' : undefined,
    className: cn('flex h-[76px] w-56 shrink-0 flex-col gap-1 rounded border border-(--ui-stroke-secondary)',
      'p-2 text-left hover:bg-(--chrome-action-hover)',
      selected && 'bg-(--chrome-action-hover)'),
    children: [
      jsx('span', {
        key: 't', className: 'w-full truncate text-xs text-(--ui-text-secondary)',
        children: sessionTitle(session),
      }),
      jsxs('span', {
        key: 'm', className: 'flex w-full min-w-0 items-center gap-1 text-[0.6875rem] text-(--ui-text-quaternary)',
        children: [
          jsx('span', {
            key: 'dot',
            className: cn('flex shrink-0 items-center', session.status === 'running' && 'animate-pulse'),
            children: jsx(ChatDot, { status: session.status }),
          }),
          jsx('span', { key: 'model', className: 'min-w-0 flex-1 truncate', children: sessionModelLabel(session) }),
          jsx('span', { key: 'e', className: 'shrink-0 tabular-nums', children: timing }),
        ],
      }),
      jsx('span', {
        key: 'ev', title: event,
        className: 'w-full truncate text-[0.6875rem] leading-snug text-(--ui-text-quaternary)',
        children: event,
      }),
    ],
  })
}

/** Collapsible strip under the transcript: a 28px header with a chevron and
 *  one dot per live worker, then a horizontally scrolling row of cards.
 *
 *  It opens itself while anything is running or queued and folds when the
 *  board goes quiet; a manual toggle wins until the next transition and is
 *  remembered in storage so the next visit starts where the user left it. */
function LiveWorkerStrip({ sessions, selectedId, onSelect }) {
  const live = sessions
    .filter((session) => !session.archived_at && LIVE_STATUSES.indexOf(session.status) !== -1)
    .slice(0, MAX_WORKERS)
  const active = live.some((session) => isBusy(session))
  const [open, setOpen] = useState(() => active || readStored('workersOpen', '') === '1')
  const wasActive = useRef(active)

  useEffect(() => {
    if (wasActive.current === active) return
    wasActive.current = active
    setOpen(active)
  }, [active])

  // Nothing live and nobody asked to see the empty state: no strip at all.
  if (!live.length && !open) return null

  const toggle = () => setOpen((value) => {
    const next = !value
    writeStored('workersOpen', next ? '1' : '0')
    return next
  })

  return jsxs('div', {
    className: 'flex min-w-0 shrink-0 flex-col gap-1.5',
    children: [
      jsxs('button', {
        key: 'head',
        type: 'button',
        'aria-expanded': open,
        'aria-label': `Live workers, ${live.length}`,
        onClick: toggle,
        className: cn('flex h-7 w-full items-center gap-1.5 rounded px-1 text-[0.6875rem]',
          'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-(--ui-text-secondary)'),
        children: [
          jsx(Caret, { key: 'caret', open }),
          jsx('span', {
            key: 'label', className: 'shrink-0 font-medium',
            children: live.length ? `Live workers · ${live.length}` : 'Live workers',
          }),
          jsx('span', {
            key: 'dots', className: 'flex min-w-0 items-center gap-1 pl-0.5',
            children: live.slice(0, 6).map((session) =>
              jsx(ChatDot, { key: session.id, status: session.status })),
          }),
        ],
      }),
      open
        ? jsx('div', {
            key: 'body',
            className: 'flex min-w-0 gap-2 overflow-x-auto pb-1',
            children: live.length
              ? live.map((session) => jsx(WorkerCard, {
                  key: session.id, session,
                  selected: session.id === selectedId,
                  onSelect,
                }))
              : jsx('div', {
                  className: 'px-1 py-2 text-[0.6875rem] text-(--ui-text-quaternary)',
                  children: 'No live workers.',
                }),
          })
        : null,
    ],
  })
}

// -- GitHub panel -----------------------------------------------------------

const GITHUB_FIELD = cn('min-w-0 rounded border border-(--ui-stroke-secondary) bg-transparent',
  'px-1.5 py-1 text-[0.6875rem] text-(--ui-text-secondary)',
  'placeholder:text-(--ui-text-quaternary) focus:outline-none')

/** One open PR: number, two-line title, author, age, and a work button. */
function GithubPrRow({ pr, onWork, pending }) {
  const number = pr && pr.number
  const title = String((pr && pr.title) || '').trim() || '(untitled)'
  return jsxs('div', {
    className: 'flex min-w-0 items-start gap-2 rounded px-1 py-1 hover:bg-(--chrome-action-hover)',
    children: [
      jsx('button', {
        key: 'n', type: 'button', onClick: () => openUrl(pr && pr.html_url),
        title: 'Open this pull request on GitHub',
        className: 'shrink-0 font-mono text-[0.6875rem] text-(--ui-text-tertiary) hover:text-(--ui-text-secondary)',
        children: `#${number}`,
      }),
      jsxs('div', {
        key: 'body', className: 'flex min-w-0 flex-1 flex-col gap-0.5',
        children: [
          jsx('span', {
            key: 'title', className: 'line-clamp-2 break-words text-xs text-(--ui-text-secondary)',
            title, children: title,
          }),
          jsxs('span', {
            key: 'meta', className: 'flex min-w-0 items-baseline gap-1.5 text-[0.6875rem] text-(--ui-text-quaternary)',
            children: [
              jsx('span', { key: 'a', className: 'min-w-0 truncate', children: (pr && pr.author) || 'unknown' }),
              jsx('span', { key: 'd', children: '·' }),
              jsx('span', { key: 't', className: 'shrink-0 tabular-nums', children: fmtIsoAge(pr && pr.updated_at) || '—' }),
            ],
          }),
        ],
      }),
      jsx(GhostButton, {
        key: 'work', className: 'shrink-0 self-center', disabled: pending,
        title: 'Start a Superagent worker on this pull request',
        onClick: () => onWork(number),
        children: pending ? 'Starting…' : 'Work on this PR',
      }),
    ],
  })
}

/** Connect/settings form on the left, the open PR list on the right. */
function GithubPanel({ status, prs, prsLoading, onSelectSession }) {
  const queryClient = useQueryClient()
  const configured = !!(status && status.configured)
  const tokenSet = !!(status && status.token_set)
  const statusRepo = (status && status.repo) || ''
  const statusAllowed = status && Array.isArray(status.allowed_users)
    ? status.allowed_users.join(', ')
    : ''
  const statusMode = (status && status.watch_mode) || 'observe'

  const [repo, setRepo] = useState('')
  const [token, setToken] = useState('')
  const [allowed, setAllowed] = useState('')
  const [mode, setMode] = useState('observe')
  const [notice, setNotice] = useState('')

  // Seed from the server when a value changes; typing never changes these
  // inputs' server values, so a poll cannot overwrite an edit in progress.
  useEffect(() => { setRepo(statusRepo) }, [statusRepo])
  useEffect(() => { setAllowed(statusAllowed) }, [statusAllowed])
  useEffect(() => { setMode(statusMode) }, [statusMode])

  const connect = useMutation({
    mutationFn: () => rest('/github/connect', {
      method: 'POST',
      body: {
        repo: String(repo || '').trim(),
        token: String(token || '').trim(),
        allowed_users: splitList(allowed),
        watch_mode: mode,
      },
    }),
    onSuccess: (result) => {
      setToken('')  // saved server-side; never keep it in component state
      if (result && result.ok === false && result.error) setNotice(String(result.error))
      else setNotice('')
      invalidateGithub(queryClient)
      invalidateChats(queryClient)
    },
  })

  const save = useMutation({
    mutationFn: () => rest('/github/settings', {
      method: 'POST',
      body: { allowed_users: splitList(allowed), watch_mode: mode },
    }),
    onSuccess: (result) => {
      if (result && result.ok === false && result.error) setNotice(String(result.error))
      else setNotice('')
      invalidateGithub(queryClient)
    },
  })

  const poll = useMutation({
    mutationFn: () => rest('/github/poll', { method: 'POST' }),
    onSuccess: (result) => {
      const errors = (result && result.errors) || []
      setNotice(errors.length ? String(errors[0]) : '')
      invalidateGithub(queryClient)
      invalidateChats(queryClient)
    },
  })

  const disconnect = useMutation({
    mutationFn: () => rest('/github/disconnect', { method: 'POST' }),
    onSuccess: () => {
      setToken('')
      setNotice('')
      invalidateGithub(queryClient)
      invalidateChats(queryClient)
    },
  })

  const work = useMutation({
    mutationFn: (number) => rest(`/github/prs/${number}/work`, { method: 'POST', body: {} }),
    onSuccess: (result) => {
      if (result && result.ok === false) {
        setNotice(String(result.error || 'could not start work on that PR'))
        return
      }
      setNotice('')
      invalidateGithub(queryClient)
      invalidateChats(queryClient)
      if (result && result.session && result.session.id && onSelectSession) {
        onSelectSession(result.session.id)
      }
    },
  })

  const failure = notice ||
    errorText(connect.error) || errorText(save.error) || errorText(poll.error) ||
    errorText(disconnect.error) || errorText(work.error)

  const openPrs = (prs && prs.prs) || (status && status.open_prs) || []
  const lastError = (status && status.last_error) || ''
  const statusLine = configured
    ? [
        status.login ? `@${status.login}` : 'connected',
        `${openPrs.length} open PR${openPrs.length === 1 ? '' : 's'}`,
        status.watching ? 'watching' : 'paused',
        `${status.poll_seconds || '—'}s poll`,
      ].join(' · ')
    : 'not connected'

  return jsxs('section', {
    'aria-label': 'GitHub',
    className: 'flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3',
    children: [
      jsx('div', {
        key: 'head', className: 'min-w-0 text-[0.6875rem] text-(--ui-text-tertiary)',
        children: statusLine,
      }),
      failure
        ? jsx('div', { key: 'failure', className: 'truncate text-[0.6875rem] text-(--ui-text-quaternary)', children: failure })
        : null,
      lastError && lastError !== failure
        ? jsx('div', {
            key: 'last-error', className: 'truncate text-[0.6875rem] text-(--ui-text-quaternary)',
            title: lastError, children: `last error: ${clip(lastError, 200)}`,
          })
        : null,
      jsxs('div', {
        key: 'columns', className: 'flex min-h-0 flex-col gap-4',
        children: [
          jsxs('div', {
            key: 'connection', className: 'flex min-w-0 flex-col gap-1.5',
            children: [
              jsx('div', {
                key: 'label', className: 'text-[0.6875rem] font-medium uppercase tracking-wide text-(--ui-text-quaternary)',
                children: 'Connection',
              }),
              jsx('input', {
                key: 'repo', type: 'text', value: repo, placeholder: 'owner/name',
                'aria-label': 'GitHub repository', autoComplete: 'off',
                onChange: (event) => setRepo(event.target.value),
                className: GITHUB_FIELD,
              }),
              jsx('input', {
                key: 'token', type: 'password', value: token,
                placeholder: tokenSet ? 'saved token' : 'GitHub token',
                'aria-label': 'GitHub token', autoComplete: 'off',
                onChange: (event) => setToken(event.target.value),
                className: GITHUB_FIELD,
              }),
              jsx('input', {
                key: 'allowed', type: 'text', value: allowed,
                placeholder: 'allowed users (comma separated)',
                'aria-label': 'Allowed GitHub users', autoComplete: 'off',
                onChange: (event) => setAllowed(event.target.value),
                className: GITHUB_FIELD,
              }),
              jsx('div', {
                key: 'allowed-help', className: 'text-[0.6875rem] leading-snug text-(--ui-text-quaternary)',
                children: 'Only these users can trigger work; the agent ignores its own comments — add a teammate or a second account.',
              }),
              jsxs('div', {
                key: 'mode', className: 'flex items-center gap-1 text-[0.6875rem] text-(--ui-text-quaternary)',
                children: [
                  jsx('span', { key: 'l', className: 'mr-1', children: 'Watch' }),
                  jsx(ModeButton, { key: 'observe', label: 'Observe', active: mode === 'observe', onClick: () => setMode('observe') }),
                  jsx(ModeButton, { key: 'auto', label: 'Auto', active: mode === 'auto', onClick: () => setMode('auto') }),
                ],
              }),
              jsxs('div', {
                key: 'actions', className: 'flex flex-wrap items-center gap-1',
                children: [
                  jsx(GhostButton, {
                    key: 'connect', disabled: connect.isPending || !String(repo || '').trim(),
                    onClick: () => connect.mutate(),
                    children: connect.isPending ? 'Connecting…' : 'Connect',
                  }),
                  jsx(GhostButton, {
                    key: 'save', disabled: save.isPending, onClick: () => save.mutate(),
                    children: save.isPending ? 'Saving…' : 'Save settings',
                  }),
                  jsx(GhostButton, {
                    key: 'poll', disabled: poll.isPending, onClick: () => poll.mutate(),
                    children: poll.isPending ? 'Polling…' : 'Poll now',
                  }),
                  jsx(GhostButton, {
                    key: 'disconnect', disabled: disconnect.isPending || !configured,
                    onClick: () => disconnect.mutate(),
                    children: disconnect.isPending ? 'Disconnecting…' : 'Disconnect',
                  }),
                ],
              }),
            ],
          }),
          jsxs('div', {
            key: 'prs', className: 'flex min-h-0 min-w-0 flex-1 flex-col gap-1',
            children: [
              jsxs('div', {
                key: 'label', className: 'flex items-baseline justify-between gap-2',
                children: [
                  jsx('span', {
                    key: 'l', className: 'text-[0.6875rem] font-medium uppercase tracking-wide text-(--ui-text-quaternary)',
                    children: 'Open pull requests',
                  }),
                  jsx('span', {
                    key: 'n', className: 'text-[0.6875rem] tabular-nums text-(--ui-text-quaternary)',
                    children: openPrs.length ? String(openPrs.length) : '',
                  }),
                ],
              }),
              openPrs.length
                ? jsx('div', {
                    key: 'list', className: 'flex min-h-0 flex-col gap-0.5 overflow-y-auto pr-1',
                    children: openPrs.map((pr) => jsx(GithubPrRow, {
                      key: String(pr.number),
                      pr,
                      onWork: (number) => work.mutate(number),
                      pending: work.isPending && work.variables === pr.number,
                    })),
                  })
                : jsx('div', {
                    key: 'empty', className: 'px-1 py-2 text-xs text-(--ui-text-tertiary)',
                    children: prsLoading
                      ? 'Loading pull requests…'
                      : configured
                        ? 'No open pull requests.'
                        : 'Connect a repository to watch its PRs.',
                  }),
            ],
          }),
        ],
      }),
    ],
  })
}

// -- onboarding -------------------------------------------------------------

const ONBOARDING_STEPS = [
  {
    key: 'settings',
    short: 'Model preset',
    title: 'Pick a model / preset',
    hint: 'Set a default in Settings, or use the model picker in the composer.',
  },
  {
    key: 'github',
    short: 'Watch a repo',
    title: 'Connect a GitHub repo to watch PRs',
    hint: 'An empty allow-list means nobody can trigger work, and the agent ignores its own comments.',
  },
  {
    key: 'chat',
    short: 'Start a chat',
    title: 'Start a chat below',
    hint: 'Describe the work, pick a repo if you want one, then press Enter.',
  },
]

/** One slim row above the transcript: three numbered steps, each clickable,
 *  and a dismiss that is remembered. Never a second card competing with the
 *  chat. */
function OnboardingBanner({ onSettings, onGithub, onStart, onDismiss }) {
  const actions = { settings: onSettings, github: onGithub, chat: onStart }
  return jsxs('section', {
    'aria-label': 'Getting started',
    className: cn('flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 rounded border',
      'border-(--ui-stroke-secondary) px-2.5 py-1.5 text-[0.6875rem]'),
    children: [
      jsx('span', {
        key: 'l', className: 'shrink-0 font-medium text-(--ui-text-secondary)',
        children: 'Get started',
      }),
      ...ONBOARDING_STEPS.map((step, index) => jsx('button', {
        key: step.key,
        type: 'button',
        title: step.hint,
        'aria-label': `${step.title}: ${step.hint}`,
        onClick: actions[step.key],
        className: cn('flex min-w-0 items-center gap-1 rounded px-1 py-0.5',
          'text-(--ui-text-secondary) hover:bg-(--chrome-action-hover)'),
        children: [
          jsx('span', {
            key: 'n',
            className: cn('flex h-4 w-4 shrink-0 items-center justify-center rounded-full',
              'border border-(--ui-stroke-secondary) text-[0.625rem] tabular-nums text-(--ui-text-tertiary)'),
            children: String(index + 1),
          }),
          jsx('span', { key: 's', className: 'min-w-0 truncate', children: step.short }),
        ],
      })),
      jsx(GhostButton, {
        key: 'dismiss', className: 'ml-auto shrink-0 px-1 py-0',
        title: 'Dismiss getting started', 'aria-label': 'Dismiss getting started',
        onClick: onDismiss, children: '✕',
      }),
    ],
  })
}

/** The keyboard map, as a sheet rather than a tooltip nobody finds. `?` opens
 *  it, Escape or a click on the backdrop closes it. */
const SHORTCUTS = [
  ['← / →', 'Previous / next town'],
  ['n', 'New town'],
  ['/', 'Search sessions'],
  ['?', 'This list'],
  ['Esc', 'Close a panel'],
]

function ShortcutSheet({ open, onClose }) {
  const cardRef = useRef(null)
  useEffect(() => {
    if (!open) return undefined
    const el = cardRef.current
    if (el && typeof el.focus === 'function') el.focus()
    const onKeyDown = (event) => {
      if (event && event.key === 'Escape') {
        if (typeof event.preventDefault === 'function') event.preventDefault()
        onClose()
      }
    }
    if (typeof window === 'undefined' || typeof window.addEventListener !== 'function') {
      return undefined
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [open, onClose])
  if (!open) return null
  return jsx('div', {
    'data-shortcuts': 'open',
    className: 'absolute inset-0 z-50 flex items-start justify-center bg-black/45 p-6',
    style: { zIndex: 50 },
    onClick: onClose,
    children: jsxs('div', {
      ref: cardRef,
      role: 'dialog',
      'aria-modal': 'true',
      'aria-label': 'Keyboard shortcuts',
      tabIndex: -1,
      onClick: (event) => event.stopPropagation(),
      className: cn('flex w-full max-w-xs flex-col gap-1.5 rounded border',
        'border-(--ui-stroke-secondary) p-3 text-xs shadow-lg',
        'focus:outline-none'),
      style: { backgroundColor: 'var(--card, var(--background, #1c1c1e))', color: 'var(--foreground, #f5f5f5)' },
      children: [
        jsx('div', {
          key: 't',
          className: 'text-[0.625rem] uppercase tracking-wide text-(--ui-text-quaternary)',
          children: 'Keyboard',
        }),
        ...SHORTCUTS.map(([keys, label]) => jsxs('div', {
          key: keys,
          className: 'flex items-baseline justify-between gap-3',
          children: [
            jsx('kbd', {
              key: 'k',
              className: cn('rounded border border-(--ui-stroke-secondary) px-1',
                'font-mono text-[0.6875rem] text-(--ui-text-secondary)'),
              children: keys,
            }),
            jsx('span', {
              key: 'l', className: 'text-(--ui-text-tertiary)', children: label,
            }),
          ],
        })),
      ],
    }),
  })
}

// -- overlay drawers --------------------------------------------------------

/** A right-side panel over the chat. It is absolutely positioned, so opening
 *  GitHub or Settings cannot reflow the transcript, and it scrolls on its own.
 *  Escape and the ✕ close it; the page restores focus to the header trigger. */
function OverlayDrawer({ panel, title, subtitle, onClose, children }) {
  const panelRef = useRef(null)
  const closeRef = useRef(onClose)
  closeRef.current = onClose

  useEffect(() => {
    const el = panelRef.current
    if (!el) return undefined
    const focusables = () => Array.from(el.querySelectorAll(
      'button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
    )).filter((element) => element.getClientRects().length > 0)
    const first = focusables()[0]
    if (first && typeof first.focus === 'function') first.focus()
    else if (typeof el.focus === 'function') el.focus()
    const onKeyDown = (event) => {
      if (!event) return
      if (event.key === 'Escape') {
        if (typeof event.preventDefault === 'function') event.preventDefault()
        closeRef.current()
        return
      }
      if (event.key !== 'Tab') return
      const items = focusables()
      if (!items.length) return
      const head = items[0]
      const tail = items[items.length - 1]
      if (event.shiftKey && (document.activeElement === head || !el.contains(document.activeElement))) {
        if (typeof event.preventDefault === 'function') event.preventDefault()
        tail.focus()
      } else if (!event.shiftKey && document.activeElement === tail) {
        if (typeof event.preventDefault === 'function') event.preventDefault()
        head.focus()
      }
    }
    if (typeof window === 'undefined' || typeof window.addEventListener !== 'function') return undefined
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  return jsxs('div', {
    className: 'absolute inset-0 z-40 flex',
    style: { zIndex: 40 },
    children: [
      jsx('button', {
        key: 'backdrop',
        type: 'button',
        tabIndex: -1,
        'aria-label': `Close ${title}`,
        onClick: onClose,
        className: 'absolute inset-0 cursor-default bg-black/45 backdrop-blur-[2px]',
      }),
      jsxs('section', {
        key: 'panel',
        'aria-label': title,
        'aria-modal': 'true',
        role: 'dialog',
        'data-drawer': panel,
        ref: panelRef,
        tabIndex: -1,
        style: { backgroundColor: 'var(--card, var(--background, #1c1c1e))', color: 'var(--foreground, #f5f5f5)' },
        className: cn('absolute inset-y-0 right-0 z-10 flex w-full max-w-full flex-col border-l',
          'border-(--ui-stroke-secondary) shadow-2xl focus:outline-none',
          'md:w-[420px] md:max-w-[94vw]'),
        children: [
          jsxs('div', {
            key: 'head',
            className: cn('flex h-10 shrink-0 items-center justify-between gap-2 border-b',
              'border-(--ui-stroke-secondary) px-3'),
            children: [
              jsxs('div', {
                key: 't', className: 'flex min-w-0 items-baseline gap-2',
                children: [
                  jsx('span', {
                    key: 'l', className: 'shrink-0 text-sm font-medium text-(--ui-text-primary)',
                    children: title,
                  }),
                  subtitle
                    ? jsx('span', {
                        key: 's', title: subtitle,
                        className: 'min-w-0 truncate text-[0.6875rem] text-(--ui-text-tertiary)',
                        children: subtitle,
                      })
                    : null,
                ],
              }),
              jsx('button', {
                key: 'x', type: 'button', 'aria-label': `Close ${title}`, title: 'Close (Esc)',
                onClick: onClose,
                className: cn('inline-flex h-7 w-7 shrink-0 items-center justify-center rounded',
                  'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-(--ui-text-secondary)'),
                children: '✕',
              }),
            ],
          }),
          jsx('div', { key: 'body', className: 'flex min-h-0 flex-1 flex-col overflow-hidden', children }),
        ],
      }),
    ],
  })
}

// -- towns -----------------------------------------------------------------
//
// The page is a town, not a rail. A root session owns the central Router
// house; each worker session owns one house and one person on a slot assigned
// by the backend. Every position is a pure function of that slot, so polling
// cannot make a house jump.

function townKnownKey(rootId) {
  return `town:known:${rootId}`
}

function readKnownWorkers(rootId) {
  try {
    const raw = readStored(townKnownKey(rootId), '')
    const parsed = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? parsed.map(String) : []
  } catch (error) {
    return []
  }
}

function writeKnownWorkers(rootId, ids) {
  const unique = Array.from(new Set((ids || []).map(String))).slice(-200)
  writeStored(townKnownKey(rootId), JSON.stringify(unique))
}

function townHash(value) {
  const text = String(value || '')
  let hash = 2166136261 >>> 0
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index)
    hash = Math.imul(hash, 16777619) >>> 0
  }
  hash ^= hash >>> 16
  hash = Math.imul(hash, 0x85ebca6b) >>> 0
  hash ^= hash >>> 13
  hash = Math.imul(hash, 0xc2b2ae35) >>> 0
  hash ^= hash >>> 16
  return hash >>> 0
}

function townHouseSprite(sessionId) {
  return TOWN_HOUSE_SPRITES[townHash(sessionId) % TOWN_HOUSE_SPRITES.length]
}

function townPersonFilter(sessionId, isRouter) {
  if (isRouter) return 'sepia(0.28) saturate(1.25)'
  const degrees = TOWN_PERSON_TONES[townHash(sessionId) % TOWN_PERSON_TONES.length]
  return `hue-rotate(${degrees}deg) saturate(1.08)`
}

/** Plot for a worker slot: static for the first twelve, a stable ring after.
 *  When `sessionId` is supplied, adds subtle deterministic micro-jitter so
 *  houses feel organically placed along the town lanes. */
function townHousePosition(slot, sessionId) {
  const index = Math.max(0, Number(slot) || 0)
  let base
  if (index < TOWN_PLOTS.length) {
    base = TOWN_PLOTS[index]
  } else {
    const extra = index - TOWN_PLOTS.length
    const ring = Math.floor(extra / 8) + 1
    const angle = (extra % 8) * (Math.PI * 2 / 8)
    const radius = Math.max(28, 38 - ring * 4)
    base = {
      left: 50 + Math.cos(angle) * radius,
      top: 54 + Math.sin(angle) * radius * 0.62,
      scale: Math.max(0.62, 0.86 - ring * 0.08),
    }
  }
  if (!sessionId) return base
  const h = townHash(sessionId)
  const dx = (((h & 0xff) / 255) - 0.5) * 2.8
  const dy = ((((h >>> 8) & 0xff) / 255) - 0.5) * 2.2
  return {
    left: Math.round((base.left + dx) * 10) / 10,
    top: Math.round((base.top + dy) * 10) / 10,
    scale: base.scale,
  }
}

function townStatusLabel(status) {
  return TOWN_STATUS_LABEL[status] || String(status || 'unknown')
}

/** Device-clock phase for the town sky: dawn / day / dusk / night.
///
/// Takes local hours (0-24, fractions allowed) so it stays pure and
/// node-testable without a DOM. Boundaries favour day: 5-6.5 dawn,
/// 6.5-17.5 day, 17.5-19.5 dusk, otherwise night. */
function townTimePhase(hours) {
  const h = Number(hours)
  if (!isFinite(h)) return 'day'
  const wrapped = ((h % 24) + 24) % 24
  if (wrapped < 5) return 'night'
  if (wrapped < 6.5) return 'dawn'
  if (wrapped < 17.5) return 'day'
  if (wrapped < 19.5) return 'dusk'
  return 'night'
}

/** HH:MM device-clock label for the town HUD. Local getters on purpose. */
function townTimeLabel(date) {
  const hours = String(date.getHours()).padStart(2, '0')
  const minutes = String(date.getMinutes()).padStart(2, '0')
  return `${hours}:${minutes}`
}

/** Sky-overlay fill for one phase. Day is transparent; night dims the town
 *  so lamps read as light sources. */
function townTimeOverlay(phase) {
  if (phase === 'dawn') return 'rgba(255, 170, 120, 0.16)'
  if (phase === 'dusk') return 'rgba(150, 80, 160, 0.22)'
  if (phase === 'night') return 'rgba(8, 12, 52, 0.42)'
  return 'rgba(0, 0, 0, 0)'
}

/** Device clock for the town: phase + label, re-checked every 30s so the
 *  sky follows the machine without joining the 2s chat poll. */
function useTownTime() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.setInterval !== 'function') return undefined
    const timer = window.setInterval(() => setNow(new Date()), 30000)
    return () => window.clearInterval(timer)
  }, [])
  const current = now instanceof Date ? now : new Date()
  const phase = townTimePhase(current.getHours() + current.getMinutes() / 60)
  return { now: current, phase, label: townTimeLabel(current) }
}

const TOWN_CSS = `
@keyframes superagent-town-build {
  0% { opacity: 0; transform: translateY(14px) scale(0.55); }
  55% { opacity: 1; transform: translateY(-5px) scale(1.06); }
  100% { opacity: 1; transform: translateY(0) scale(1); }
}
@keyframes superagent-town-walk {
  0% { background-position-x: 0; }
  33.33% { background-position-x: calc(var(--town-person-step, 36px) * -1); }
  66.66% { background-position-x: calc(var(--town-person-step, 36px) * -2); }
  100% { background-position-x: 0; }
}
@keyframes superagent-petdex-anim {
  from { background-position-x: 0px; }
  to { background-position-x: calc(var(--pet-frame-w, 48px) * -8); }
}
@keyframes superagent-speech-float {
  0%, 100% { transform: translate(-50%, 0px); }
  50% { transform: translate(-50%, -2px); }
}
.superagent-town-build { animation: superagent-town-build 900ms cubic-bezier(.2,.8,.2,1) both; }
.superagent-town-person { animation: superagent-town-walk 900ms steps(1,end) infinite; }
.superagent-petdex-sprite { animation: superagent-petdex-anim 850ms steps(8, end) infinite; }
.superagent-speech-bubble { animation: superagent-speech-float 2200ms ease-in-out infinite; }
.superagent-town-sky { transition: background-color 800ms linear; }
.superagent-town-clock { font-variant-numeric: tabular-nums; }
/* Fixed-size bubbles scroll instead of growing the town layout.
 * Clear, high-visibility retro scrollbar for chat windows & speech bubbles. */
.superagent-pixel-scroll { scrollbar-width: auto; scrollbar-color: #f5d97a #2a2135; }
.superagent-pixel-scroll::-webkit-scrollbar { width: 12px; height: 12px; }
.superagent-pixel-scroll::-webkit-scrollbar-track { background: #2a2135; border: 2px solid #1a1a2e; }
.superagent-pixel-scroll::-webkit-scrollbar-thumb { background: #f5d97a; border: 2px solid #1a1a2e; min-height: 24px; }
.superagent-pixel-scroll::-webkit-scrollbar-thumb:hover { background: #ffe58f; }
.superagent-pixel-scroll::-webkit-scrollbar-thumb:active { background: #ffd94d; }
.superagent-actor-btn:hover [data-town-speech="1"], .superagent-actor-btn:focus-visible [data-town-speech="1"] {
  opacity: 1 !important;
  visibility: visible !important;
}
/* Pixel chrome: system pixel-font stack (no remote fetch, hermetic) plus
 * stepped 2px panels. Applied to the town canvas, HUD, bubbles and dialog
 * windows; the rest of the plugin keeps the host chrome. */
.superagent-pixel, .superagent-town-clock, .superagent-speech-bubble {
  font-family: 'VT323', 'Px437 IBM VGA 8x16', 'IBM Plex Mono', 'Courier New', monospace;
  letter-spacing: 0.02em;
}
.superagent-pixel-panel {
  border: 2px solid #1a1a2e !important;
  box-shadow: 0 2px 0 #1a1a2e, inset -2px -2px 0 rgba(0,0,0,0.18), inset 2px 2px 0 rgba(255,255,255,0.22);
}
@media (prefers-reduced-motion: reduce) {
  .superagent-town-build, .superagent-town-person, .superagent-petdex-sprite, .superagent-speech-bubble { animation: none !important; }
}
`

function TownStyles() {
  return jsx('style', { children: TOWN_CSS })
}

const TOWN_BANTER = [
  '👋 Hey neighbor!',
  'Checking the diff…',
  'Tests are green! ✨',
  'Shipping clean code!',
  'Need a PR review?',
  'On my way to Plaza!',
]

function deriveSpeechBubble(session, isRouter, socialText) {
  if (!session) return ''
  const events = Array.isArray(session.recent_events) ? session.recent_events.filter(Boolean) : []
  const latest = events.length ? events[0] : null
  if (latest) {
    const kind = String(latest.kind || '').trim()
    const detail = String(latest.detail || '').replace(/\s+/g, ' ').trim()
    if (kind === 'pr_created') return `📬 ${clip(detail || 'Opened PR!', 36)}`
    if (kind === 'pr_reviewed') return `✅ ${clip(detail || 'Reviewed PR!', 36)}`
    if (kind === 'router_message') return `📣 ${clip(detail || 'Message sent', 36)}`
    if (kind === 'spawned' && isRouter) return `🏘️ Spawned ${clip(detail, 28)}`
    if (session.status === 'running' && detail) return `⚡ ${clip(detail, 34)}`
  }
  if (session.status === 'running') {
    const raw = String(session.last_event || '').replace(/\s+/g, ' ').trim()
    return raw ? `🔧 ${clip(raw, 34)}` : (isRouter ? '🧭 Coordinating town…' : '🔨 Working on task…')
  }
  if (session.status === 'queued') return '⏳ Waiting in queue…'
  if (session.status === 'failed') {
    const err = firstLine(session.error)
    return `❌ ${clip(err || 'Turn failed', 32)}`
  }
  if (socialText) return socialText
  if (session.status === 'review') {
    return session.pr_number ? `🎉 PR #${session.pr_number} ready!` : '✨ Ready for review!'
  }
  return ''
}

function PetdexSprite({ session, isRouter, pet, personArt, animState, facingLeft, scale, reducedMotion }) {
  const s = Math.max(0.6, Number(scale) || 1)
  if (pet && pet.data_url) {
    const fw = Math.round((Number(pet.frame_width) || 48) * s)
    const fh = Math.round((Number(pet.frame_height) || 52) * s)
    const cols = Number(pet.cols) || 8
    const rows = Number(pet.rows) || 9
    const rowsMap = pet.rows_map || {}
    const rowIdx = typeof rowsMap[animState] === 'number'
      ? rowsMap[animState]
      : Math.max(0, PETDEX_ROW_STATES.indexOf(animState || 'idle'))
    const flip = facingLeft && animState !== 'running-left' && animState !== 'running-right'
    return jsx('span', {
      'data-petdex-slug': pet.slug,
      'data-petdex-state': animState || 'idle',
      'aria-hidden': 'true',
      className: cn('block select-none', !reducedMotion && 'superagent-petdex-sprite'),
      style: {
        width: `${fw}px`,
        height: `${fh}px`,
        backgroundImage: `url("${pet.data_url}")`,
        backgroundSize: `${fw * cols}px ${fh * rows}px`,
        backgroundPositionY: `${-rowIdx * fh}px`,
        '--pet-frame-w': `${fw}px`,
        imageRendering: 'pixelated',
        transform: flip ? 'scaleX(-1)' : undefined,
      },
    })
  }
  if (!personArt) return null
  const personSize = Math.round((isRouter ? 42 : 36) * s)
  const dirRow = animState === 'running-left' ? 1 : (animState === 'running-right' ? 2 : (animState === 'jumping' ? 3 : 0))
  return jsx('span', {
    'data-petdex-fallback': 'person.png',
    'aria-hidden': 'true',
    className: cn('block select-none', !reducedMotion && 'superagent-town-person'),
    style: {
      width: `${personSize}px`,
      height: `${personSize}px`,
      backgroundImage: `url("${personArt}")`,
      backgroundSize: `${personSize * 3}px ${personSize * 4}px`,
      backgroundPosition: `0 ${-dirRow * personSize}px`,
      '--town-person-step': `${personSize}px`,
      imageRendering: 'pixelated',
      filter: townPersonFilter(session.id, isRouter),
    },
  })
}

function TownSpeechBubble({ text, status, visible = true }) {
  if (!text || !visible) return null
  // Opaque high-contrast dark slate bubble with bright ivory text so it is
  // crisp on grass, stone, or night sky without ever matching the text color.
  const borderColor = status === 'failed'
    ? '#f87171'
    : (status === 'review'
        ? '#fbbf24'
        : (status === 'running' ? '#60a5fa' : '#a78bfa'))
  return jsx('span', {
    'data-town-speech': '1',
    className: cn(
      'superagent-speech-bubble superagent-pixel-scroll pointer-events-none absolute left-1/2 z-20',
      '-translate-x-1/2 overflow-x-hidden overflow-y-auto break-words rounded-md',
      'text-left font-semibold leading-snug opacity-100',
    ),
    style: {
      bottom: '100%',
      left: '50%',
      marginBottom: '6px',
      transform: 'translateX(-50%)',
      width: 'max-content',
      minWidth: '52px',
      maxWidth: '176px',
      maxHeight: '54px',
      padding: '4px 8px',
      backgroundColor: '#161326',
      color: '#fffbeb',
      borderColor,
      borderWidth: '2px',
      borderStyle: 'solid',
      boxShadow: '0 4px 10px rgba(0, 0, 0, 0.7), 0 0 0 1px #090812',
      textShadow: '0 1px 2px rgba(0, 0, 0, 0.95)',
      fontSize: '0.6875rem',
      fontWeight: 600,
      lineHeight: 1.25,
      opacity: 1,
    },
    title: text,
    children: text,
  })
}

const TOWN_WAYPOINTS = [
  { left: 50, top: 64 }, // Central Plaza North
  { left: 50, top: 75 }, // Central Fountain
  { left: 37, top: 63 }, // Noticeboard / West Plaza
  { left: 63, top: 63 }, // Wishing Well / East Plaza
  { left: 22, top: 52 }, // West Avenue Crossroad
  { left: 78, top: 52 }, // East Avenue Crossroad
  { left: 26, top: 76 }, // Southwest Stream Bridge
  { left: 74, top: 74 }, // Southeast Park Bench
  { left: 38, top: 34 }, // North Promenade West
  { left: 62, top: 34 }, // North Promenade East
]

function actorHomePosition(session, isRouter) {
  if (isRouter) return { left: 50, top: 57 }
  const pos = townHousePosition(session.town_slot, session.id)
  const towardCenter = pos.left < 50 ? 4.5 : -4.5
  return {
    left: Math.max(8, Math.min(92, pos.left + towardCenter)),
    top: Math.max(18, Math.min(90, pos.top + 6.5)),
  }
}

function defaultAnimForStatus(status) {
  if (status === 'running') return 'running'
  if (status === 'review') return 'review'
  if (status === 'queued' || status === 'observed') return 'waiting'
  if (status === 'failed' || status === 'cancelled') return 'failed'
  return 'idle'
}

function useTownSimulation(town, buildingIds, reducedMotion) {
  const simRef = useRef({})
  const [, setTick] = useState(0)

  const members = useMemo(() => {
    if (!town) return []
    const list = [{ session: town, isRouter: true }]
    for (const w of town.workers || []) {
      list.push({ session: w, isRouter: false })
    }
    return list
  }, [town])

  // Sync actors with current sessions synchronously so initial render & render probe have positions immediately.
  const stateMap = simRef.current
  const seenIds = new Set()
  for (const { session, isRouter } of members) {
    seenIds.add(session.id)
    const home = actorHomePosition(session, isRouter)
    const isBuilding = !!(buildingIds && buildingIds[session.id])
    const events = Array.isArray(session.recent_events) ? session.recent_events.filter(Boolean) : []
    const latest = events.length ? events[0] : null
    const currentEventKey = latest ? `${String(latest.kind || '').trim()}:${String(latest.detail || '').trim()}` : ''
    const existing = stateMap[session.id]
    if (!existing) {
      const h = townHash(session.id)
      const startLeft = isBuilding && !reducedMotion ? 50 : home.left
      const startTop = isBuilding && !reducedMotion ? 57 : home.top
      const startSpeaking = session.status === 'running' || session.status === 'queued' || isBuilding
      stateMap[session.id] = {
        left: startLeft,
        top: startTop,
        targetLeft: home.left,
        targetTop: home.top,
        animState: isBuilding && !reducedMotion ? 'jumping' : defaultAnimForStatus(session.status),
        facingLeft: home.left < 50,
        socialText: '',
        speechTicks: startSpeaking ? 20 : 0,
        speechCooldown: startSpeaking ? (55 + (h % 35)) : (50 + (h % 60)),
        socialCooldown: 30 + (h % 50),
        lastStatus: session.status,
        lastEventKey: currentEventKey,
        pauseTicks: (h % 14) + 4,
        stepCount: h % 7,
      }
    } else {
      existing.homeLeft = home.left
      existing.homeTop = home.top
      if (session.status !== existing.lastStatus || currentEventKey !== existing.lastEventKey) {
        existing.lastStatus = session.status
        existing.lastEventKey = currentEventKey
        if (currentEventKey || session.status === 'running' || session.status === 'review' || session.status === 'failed') {
          existing.speechTicks = 24
          existing.speechCooldown = 60
        }
      }
      if (reducedMotion) {
        existing.left = home.left
        existing.top = home.top
        existing.animState = defaultAnimForStatus(session.status)
        existing.socialText = ''
      }
    }
  }
  for (const oldId of Object.keys(stateMap)) {
    if (!seenIds.has(oldId)) delete stateMap[oldId]
  }

  useEffect(() => {
    if (reducedMotion || !members.length || typeof window === 'undefined') return undefined
    const timer = window.setInterval(() => {
      const map = simRef.current
      const ids = members.map((m) => m.session.id)
      for (const { session, isRouter } of members) {
        const actor = map[session.id]
        if (!actor) continue
        const home = actorHomePosition(session, isRouter)
        const status = session.status
        const hActor = townHash(`${session.id}:${actor.stepCount || 0}`)

        if (actor.socialCooldown > 0) actor.socialCooldown -= 1
        if (actor.speechTicks > 0) {
          actor.speechTicks -= 1
          if (actor.speechTicks === 0) {
            actor.socialText = ''
            actor.speechCooldown = (status === 'running' || status === 'queued')
              ? 55 + (hActor % 35)
              : 95 + (hActor % 65)
          }
        } else {
          if (actor.speechCooldown > 0) actor.speechCooldown -= 1
          if (actor.speechCooldown <= 0) {
            if (status === 'running' || status === 'queued') {
              actor.speechTicks = 18
              actor.speechCooldown = 60 + (hActor % 40)
            } else if (status === 'review') {
              actor.speechTicks = 14
              actor.speechCooldown = 120 + (hActor % 80)
            } else {
              actor.speechTicks = 0
              actor.speechCooldown = 100
            }
          }
        }

        const dx = actor.targetLeft - actor.left
        const dy = actor.targetTop - actor.top
        const dist = Math.hypot(dx, dy)

        if (dist > 0.9) {
          const speed = status === 'running' ? 1.35 : (status === 'review' ? 1.05 : 0.8)
          const step = Math.min(dist, speed)
          actor.left = Math.round((actor.left + (dx / dist) * step) * 100) / 100
          actor.top = Math.round((actor.top + (dy / dist) * step) * 100) / 100
          actor.facingLeft = dx < -0.05
          actor.animState = dx < -0.15 ? 'running-left' : (dx > 0.15 ? 'running-right' : 'running')
        } else {
          actor.left = actor.targetLeft
          actor.top = actor.targetTop
          if (actor.pauseTicks > 0) {
            actor.pauseTicks -= 1
            actor.animState = actor.socialText ? 'waving' : defaultAnimForStatus(status)
          } else {
            actor.stepCount = (actor.stepCount || 0) + 1
            actor.socialText = ''
            const h = townHash(`${session.id}:${actor.stepCount}`)
            if (status === 'failed' || status === 'cancelled') {
              actor.targetLeft = home.left
              actor.targetTop = home.top
              actor.pauseTicks = 25
            } else if (status === 'queued' || status === 'observed') {
              // Pace near the town signpost / noticeboard
              actor.targetLeft = 37 + ((h % 5) - 2)
              actor.targetTop = 63 + (((h >>> 4) % 5) - 2)
              actor.pauseTicks = 18
            } else if (status === 'review') {
              // Celebrate at the central plaza fountain or home
              const goPlaza = (actor.stepCount % 2) === 0
              actor.targetLeft = goPlaza ? 46 + (h % 9) : home.left
              actor.targetTop = goPlaza ? 71 + ((h >>> 3) % 6) : home.top
              actor.pauseTicks = 22
            } else if (status === 'running') {
              // Commute between workshop and plaza / Router hall
              const leg = actor.stepCount % 3
              if (leg === 0) {
                actor.targetLeft = home.left
                actor.targetTop = home.top
              } else if (leg === 1) {
                const wp = TOWN_WAYPOINTS[h % TOWN_WAYPOINTS.length]
                actor.targetLeft = wp.left
                actor.targetTop = wp.top
              } else {
                actor.targetLeft = 50 + ((h % 9) - 4)
                actor.targetTop = 61 + (((h >>> 4) % 7) - 3)
              }
              actor.pauseTicks = 12 + (h % 10)
            } else {
              // Idle / Router: stroll around town landmarks and greet neighbors
              const goHome = (actor.stepCount % 3) === 0
              if (goHome) {
                actor.targetLeft = home.left
                actor.targetTop = home.top
              } else {
                const wp = TOWN_WAYPOINTS[h % TOWN_WAYPOINTS.length]
                actor.targetLeft = wp.left + ((h % 5) - 2)
                actor.targetTop = wp.top + (((h >>> 5) % 5) - 2)
              }
              actor.pauseTicks = 18 + (h % 16)
            }
          }
        }
      }

      // Check proximity between actors for occasional friendly town banter & waving
      for (let i = 0; i < ids.length; i += 1) {
        for (let j = i + 1; j < ids.length; j += 1) {
          const a = map[ids[i]]
          const b = map[ids[j]]
          if (!a || !b) continue
          if ((a.speechTicks || 0) > 0 || (b.speechTicks || 0) > 0) continue
          if ((a.socialCooldown || 0) > 0 || (b.socialCooldown || 0) > 0) continue
          const d = Math.hypot(a.left - b.left, a.top - b.top)
          if (d > 1.5 && d < 8.5 && !a.socialText && !b.socialText) {
            const pick = TOWN_BANTER[(townHash(ids[i]) + (a.stepCount || 0)) % TOWN_BANTER.length]
            a.socialText = pick
            a.speechTicks = 15
            a.speechCooldown = 90
            a.socialCooldown = 140
            b.socialText = '👋'
            b.speechTicks = 12
            b.speechCooldown = 90
            b.socialCooldown = 140
            break
          }
        }
      }
      setTick((t) => (t + 1) % 1000000)
    }, 120)
    return () => window.clearInterval(timer)
  }, [members, reducedMotion])

  return simRef.current
}

function matchesTownFilter(session, filter, isRouter) {
  if (!filter || filter === 'all') return true
  if (isRouter) return true
  const status = townStatusLabel(session && session.status)
  if (filter === 'running') return status === 'running' || status === 'queued'
  if (filter === 'review') return status === 'review'
  if (filter === 'failed') return status === 'failed' || status === 'cancelled'
  if (filter === 'pr') return !!(session && session.pr_number)
  return true
}

function TownActor({ session, isRouter, actorState, pet, personArt, reducedMotion, dimmed, onOpen }) {
  const [hovered, setHovered] = useState(false)
  const pos = actorState || actorHomePosition(session, isRouter)
  const animState = (actorState && actorState.animState) || defaultAnimForStatus(session.status)
  const facingLeft = !!(actorState && actorState.facingLeft)
  const speech = deriveSpeechBubble(session, isRouter, actorState && actorState.socialText)
  const speechVisible = hovered || (actorState ? (actorState.speechTicks > 0 || !!actorState.socialText) : true)
  const petName = (pet && pet.display_name) || session.avatar_slug || (isRouter ? 'Router' : 'Worker')
  const zIndex = Math.max(5, Math.min(25, Math.round((Number(pos.top) || 50) / 5) + 2))

  return jsxs('button', {
    type: 'button',
    'data-town-actor': session.id,
    onClick: () => onOpen(session.id),
    onMouseEnter: () => setHovered(true),
    onMouseLeave: () => setHovered(false),
    onFocus: () => setHovered(true),
    onBlur: () => setHovered(false),
    'aria-label': `${isRouter ? 'Router' : 'Worker'} character ${session.id} (${petName})`,
    title: `${sessionTitle(session)} · ${petName} (${townStatusLabel(session.status)})`,
    className: cn(
      'superagent-actor-btn group absolute flex flex-col items-center focus:outline-none',
      'transition-[left,top,opacity] duration-150 ease-linear',
      dimmed && 'opacity-35',
    ),
    style: {
      left: `${pos.left}%`,
      top: `${pos.top}%`,
      transform: 'translate(-50%, -85%)',
      zIndex,
    },
    children: [
      jsx(TownSpeechBubble, { key: 'speech', text: speech, status: session.status, visible: speechVisible }),
      jsx(PetdexSprite, {
        key: 'sprite',
        session,
        isRouter,
        pet,
        personArt,
        animState,
        facingLeft,
        scale: isRouter ? 0.96 : 0.86,
        reducedMotion,
      }),
      jsx('span', {
        key: 'shadow',
        'aria-hidden': 'true',
        className: ' -mt-1 h-1.5 w-6 rounded-full bg-black/30',
      }),
    ],
  })
}

function TownHouse({ session, isRouter, slot, onOpen, houseRef, building, reducedMotion, assets, pet, dimmed }) {
  const sprite = isRouter ? 'house-router.png' : townHouseSprite(session.id)
  const art = assets && assets[sprite] && assets[sprite].data_url
  const position = isRouter ? { left: 50, top: 47, scale: 1.06 } : townHousePosition(slot, session.id)
  const scale = Math.max(0.5, Number(position.scale) || 1)
  const width = Math.round((isRouter ? 178 : 136) * scale)
  const fallbackHeight = Math.round((isRouter ? 194 : 140) * scale)
  const label = townStatusLabel(session.status)
  const petLabel = (pet && pet.display_name) || session.avatar_slug || ''
  const zIndex = Math.max(4, Math.min(23, Math.round((Number(position.top) || 50) / 5)))
  return jsxs('div', {
    'data-town-house': session.id,
    className: cn('absolute flex flex-col items-center transition-opacity duration-150', dimmed && 'opacity-35'),
    style: {
      left: `${position.left}%`,
      top: `${position.top}%`,
      transform: 'translate(-50%, -50%)',
      transformOrigin: '50% 100%',
      width: `${width + 22}px`,
      zIndex,
    },
    children: [
      jsxs('button', {
        key: 'house',
        ref: houseRef,
        type: 'button',
        onClick: () => onOpen(session.id),
        'aria-label': `${isRouter ? 'Router' : 'Worker'} house ${session.id}: ${sessionTitle(session)} — ${label}`,
        title: `${sessionTitle(session)} (${session.id}) · ${label}${petLabel ? ` · ${petLabel}` : ''}`,
        className: cn('relative flex w-full flex-col items-center rounded',
          'focus-visible:outline focus-visible:outline-2 focus-visible:outline-(--ui-accent)',
          'hover:brightness-110',
          building && !reducedMotion && 'superagent-town-build'),
        children: [
          art
            ? jsx('img', {
                key: 'art',
                src: art,
                alt: '',
                draggable: false,
                width,
                style: { imageRendering: 'pixelated', height: 'auto' },
              })
            : jsx('div', {
                key: 'missing',
                className: 'flex items-center justify-center rounded border border-(--ui-stroke-secondary) bg-background',
                style: { width, height: fallbackHeight },
                children: jsx('span', { className: 'px-1 text-center text-[0.625rem] text-(--ui-text-quaternary)', children: '…' }),
              }),
          jsxs('span', {
            key: 'badge',
            className: cn('absolute left-0 top-0 inline-flex items-center gap-1 rounded border',
              'border-(--ui-stroke-secondary) bg-background/95 px-1 py-0.5',
              'text-[0.625rem] leading-none text-(--ui-text-secondary) shadow-sm'),
            children: [
              jsx(ChatDot, { key: 'dot', status: session.status }),
              jsx('span', { key: 's', className: 'whitespace-nowrap', children: label }),
            ],
          }),
          session.pr_number
            ? jsx('span', {
                key: 'pr-chip',
                className: cn('absolute right-0 top-0 rounded border border-(--ui-stroke-secondary)',
                  'bg-background/95 px-1 font-mono text-[0.625rem] leading-tight text-(--ui-text-secondary) shadow-sm'),
                children: `#${session.pr_number}`,
              })
            : (building
                ? jsx('span', {
                    key: 'new',
                    className: 'absolute right-0 top-0 rounded bg-background px-1 text-[0.625rem] text-(--ui-text-tertiary)',
                    children: 'new',
                  })
                : null),
        ],
      }),
      jsx('button', {
        key: 'name',
        type: 'button',
        tabIndex: -1,
        onClick: () => onOpen(session.id),
        'aria-label': `Open ${isRouter ? 'Router' : 'worker'} ${session.id}: ${sessionTitle(session)}`,
        className: cn('mt-0.5 block max-w-[11rem] truncate rounded border px-1.5 py-0.5',
          'border-(--ui-stroke-secondary) bg-background/95 text-center text-[0.625rem]',
          'leading-tight text-(--ui-text-secondary) shadow-sm hover:bg-(--chrome-action-hover)'),
        title: sessionTitle(session),
        children: isRouter ? 'Router Hall' : sessionTitle(session),
      }),
    ],
  })
}

function bulletinTaskSnippet(task) {
  if (!task) return ''
  const events = Array.isArray(task.recent_events) ? task.recent_events.filter(Boolean) : []
  if (events.length && events[0].detail) {
    return clip(String(events[0].detail).replace(/\s+/g, ' ').trim(), 90)
  }
  const raw = String(task.last_event || '').replace(/\s+/g, ' ').trim()
  if (raw) return clip(raw, 90)
  return clip(firstLine(task.prompt), 90)
}

function TownBulletinBoard({ assets, inProgressCount, totalTasksCount, onOpenBulletin }) {
  const art = assets && assets['bulletin-board.png'] && assets['bulletin-board.png'].data_url
  const activeBadge = inProgressCount > 0 ? `${inProgressCount} active` : `${totalTasksCount} tasks`
  return jsxs('button', {
    type: 'button',
    'data-town-bulletin': '1',
    onClick: onOpenBulletin,
    'aria-label': `Town bulletin board (${inProgressCount} in progress)`,
    title: 'Click to view tasks currently in progress in this town',
    className: cn(
      'group absolute flex flex-col items-center rounded p-0.5 transition-transform duration-150',
      'hover:scale-105 hover:brightness-110',
      'focus-visible:outline focus-visible:outline-2 focus-visible:outline-(--ui-accent)',
    ),
    style: {
      left: '35%',
      top: '58%',
      transform: 'translate(-50%, -85%)',
      zIndex: 12,
    },
    children: [
      jsxs('span', {
        key: 'board-wrap',
        className: 'relative flex items-center justify-center',
        children: [
          art
            ? jsx('img', {
                key: 'art',
                src: art,
                alt: '',
                draggable: false,
                width: 44,
                style: { imageRendering: 'pixelated', height: 'auto' },
              })
            : jsx('span', {
                key: 'fallback',
                className: cn(
                  'superagent-pixel-panel flex h-[40px] w-[44px] items-center justify-center rounded',
                  'bg-[#845630] text-[0.75rem] text-[#fffbeb] shadow-sm',
                ),
                children: '📋',
              }),
          jsx('span', {
            key: 'badge',
            className: cn(
              'absolute -right-2 -top-1.5 inline-flex items-center gap-1 rounded border px-1 py-0.5',
              'text-[0.5625rem] font-semibold leading-none whitespace-nowrap shadow-sm',
              inProgressCount > 0
                ? 'border-amber-400/80 bg-[#161326] text-amber-200'
                : 'border-(--ui-stroke-secondary) bg-background/95 text-(--ui-text-secondary)',
            ),
            children: activeBadge,
          }),
        ],
      }),
      jsx('span', {
        key: 'label',
        className: cn(
          'mt-0.5 rounded border border-(--ui-stroke-secondary) bg-background/95 px-1.5 py-0.5',
          'text-[0.5625rem] font-medium leading-tight whitespace-nowrap text-(--ui-text-secondary) shadow-sm',
          'group-hover:bg-(--chrome-action-hover)',
        ),
        children: 'Bulletin Board',
      }),
    ],
  })
}

function TownBulletinModal({ town, pets, onClose, onSelectTask }) {
  const allTasks = useMemo(() => {
    if (!town) return []
    const workers = Array.isArray(town.workers) ? town.workers : []
    return [
      { task: town, isRouter: true },
      ...workers.map((w) => ({ task: w, isRouter: false })),
    ]
  }, [town])

  const inProgress = useMemo(
    () => allTasks.filter(({ task }) => task && (task.status === 'running' || task.status === 'queued')),
    [allTasks],
  )
  const otherTasks = useMemo(
    () => allTasks.filter(({ task }) => task && task.status !== 'running' && task.status !== 'queued'),
    [allTasks],
  )
  const now = useNow(inProgress.length > 0)
  const petMap = pets || {}

  useEffect(() => {
    if (typeof window === 'undefined') return undefined
    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        onClose()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  const renderTaskCard = ({ task, isRouter }) => {
    const roleLabel = isRouter
      ? 'Router'
      : `Worker #${(Number(task.town_slot) || 0) + 1}`
    const statusLabel = townStatusLabel(task.status)
    const busy = task.status === 'running' || task.status === 'queued'
    const elapsed = busy ? fmtSeconds(liveElapsedSeconds(task, now)) : timingLabel(task)
    const snippet = bulletinTaskSnippet(task)
    const pet = petMap[task.avatar_slug]
    const petName = (pet && pet.display_name) || task.avatar_slug || ''
    return jsxs('button', {
      key: task.id,
      type: 'button',
      'data-town-bulletin-task': task.id,
      onClick: () => onSelectTask(task.id),
      className: cn(
        'flex w-full flex-col gap-1 rounded-md border border-(--ui-stroke-secondary)',
        'bg-background/95 p-2.5 text-left transition-colors hover:border-(--ui-accent) hover:bg-(--chrome-action-hover)',
        'focus-visible:outline focus-visible:outline-2 focus-visible:outline-(--ui-accent)',
      ),
      children: [
        jsxs('div', {
          key: 'top',
          className: 'flex flex-wrap items-center justify-between gap-1.5 text-[0.6875rem]',
          children: [
            jsxs('div', {
              key: 'left',
              className: 'inline-flex items-center gap-1.5',
              children: [
                jsx('span', {
                  key: 'role',
                  className: cn(
                    'rounded border px-1.5 py-0.5 text-[0.625rem] font-semibold leading-none',
                    isRouter
                      ? 'border-amber-500/40 bg-amber-500/10 text-amber-300'
                      : 'border-(--ui-stroke-secondary) bg-(--chrome-action-hover) text-(--ui-text-secondary)',
                  ),
                  children: roleLabel,
                }),
                jsx('span', {
                  key: 'id',
                  className: 'font-mono text-[0.625rem] text-(--ui-text-quaternary)',
                  children: task.id,
                }),
                petName
                  ? jsx('span', {
                      key: 'pet',
                      className: 'text-[0.625rem] text-(--ui-text-tertiary)',
                      children: `· ${petName}`,
                    })
                  : null,
              ],
            }),
            jsxs('div', {
              key: 'right',
              className: 'inline-flex items-center gap-1.5 text-[0.625rem] text-(--ui-text-secondary)',
              children: [
                jsx(ChatDot, { key: 'dot', status: task.status }),
                jsx('span', { key: 'status', className: 'font-medium', children: statusLabel }),
                elapsed
                  ? jsx('span', {
                      key: 'elapsed',
                      className: 'font-mono tabular-nums text-(--ui-text-tertiary)',
                      children: `· ${elapsed}`,
                    })
                  : null,
              ],
            }),
          ],
        }),
        jsx('div', {
          key: 'title',
          className: 'truncate text-xs font-semibold text-(--ui-text-primary)',
          children: sessionTitle(task),
        }),
        snippet
          ? jsx('div', {
              key: 'snippet',
              className: 'line-clamp-2 text-[0.6875rem] leading-snug text-(--ui-text-tertiary)',
              children: snippet,
            })
          : null,
      ],
    })
  }

  return jsx('div', {
    role: 'dialog',
    'aria-modal': 'true',
    'aria-label': 'Town Bulletin Board',
    'data-town-bulletin-modal': '1',
    onClick: (event) => {
      if (event.target === event.currentTarget) onClose()
    },
    className: 'absolute inset-0 z-[35] flex items-center justify-center bg-black/60 p-3 backdrop-blur-xs',
    style: { zIndex: 35 },
    children: jsxs('div', {
      className: cn(
        'superagent-pixel superagent-pixel-panel flex max-h-[90%] w-full max-w-lg flex-col overflow-hidden rounded-lg',
        'border border-(--ui-stroke-secondary) bg-background text-(--ui-text-primary) shadow-xl',
      ),
      style: {
        backgroundColor: 'var(--card, var(--background, #1c1c1e))',
        color: 'var(--foreground, #f5f5f5)',
        width: 'min(520px, 92%)',
        maxHeight: '86%',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      },
      children: [
        jsxs('div', {
          key: 'header',
          className: 'flex shrink-0 items-start justify-between gap-2 border-b border-(--ui-stroke-secondary) px-3.5 py-2.5',
          children: [
            jsxs('div', {
              key: 'titles',
              className: 'min-w-0 flex-1',
              children: [
                jsx('h3', {
                  key: 'h',
                  className: 'text-sm font-bold text-(--ui-text-primary)',
                  children: '📋 Town Bulletin Board',
                }),
                jsx('p', {
                  key: 'sub',
                  className: 'truncate text-[0.6875rem] text-(--ui-text-tertiary)',
                  children: `${inProgress.length} task${inProgress.length === 1 ? '' : 's'} currently in progress in ${sessionTitle(town)}`,
                }),
              ],
            }),
            jsx('button', {
              key: 'close',
              type: 'button',
              onClick: onClose,
              'aria-label': 'Close bulletin board',
              title: 'Close bulletin board (Esc)',
              className: cn(
                'inline-flex h-6 w-6 shrink-0 items-center justify-center rounded',
                'text-xs text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-(--ui-text-primary)',
              ),
              children: '✕',
            }),
          ],
        }),
        jsxs('div', {
          key: 'body',
          className: 'superagent-pixel-scroll flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3.5',
          style: { flex: '1 1 0%', minHeight: 0, overflowY: 'auto' },
          children: [
            jsxs('div', {
              key: 'in-progress-section',
              className: 'flex flex-col gap-1.5',
              children: [
                jsx('h4', {
                  key: 'heading',
                  className: 'text-[0.6875rem] font-semibold uppercase tracking-wide text-(--ui-text-tertiary)',
                  children: `In Progress Tasks (${inProgress.length})`,
                }),
                inProgress.length > 0
                  ? jsx('div', {
                      key: 'list',
                      className: 'flex flex-col gap-1.5',
                      children: inProgress.map(renderTaskCard),
                    })
                  : jsx('p', {
                      key: 'empty',
                      className: 'rounded-md border border-dashed border-(--ui-stroke-secondary) px-3 py-3 text-center text-xs text-(--ui-text-tertiary)',
                      children: 'No tasks are currently running or queued in this town.',
                    }),
              ],
            }),
            otherTasks.length > 0
              ? jsxs('div', {
                  key: 'other-section',
                  className: 'flex flex-col gap-1.5',
                  children: [
                    jsx('h4', {
                      key: 'heading',
                      className: 'text-[0.6875rem] font-semibold uppercase tracking-wide text-(--ui-text-quaternary)',
                      children: `Other Town Tasks (${otherTasks.length})`,
                    }),
                    jsx('div', {
                      key: 'list',
                      className: 'flex flex-col gap-1.5',
                      children: otherTasks.map(renderTaskCard),
                    }),
                  ],
                })
              : null,
          ],
        }),
      ],
    }),
  })
}

function TownScene({ town, assets, pets, buildingIds, onOpen, registerHouse, reducedMotion }) {
  const grass = assets && assets['grass.png'] && assets['grass.png'].data_url
  const roadH = assets && assets['road-h.png'] && assets['road-h.png'].data_url
  const roadV = assets && assets['road-v.png'] && assets['road-v.png'].data_url
  const plazaStone = assets && assets['plaza-stone.png'] && assets['plaza-stone.png'].data_url
  const water = assets && assets['water.png'] && assets['water.png'].data_url
  const bridge = assets && assets['bridge.png'] && assets['bridge.png'].data_url
  const bench = assets && assets['bench.png'] && assets['bench.png'].data_url
  const well = assets && assets['well.png'] && assets['well.png'].data_url
  const flowers = assets && assets['flowers.png'] && assets['flowers.png'].data_url
  const signpost = assets && assets['signpost.png'] && assets['signpost.png'].data_url
  const treeA = assets && assets['tree-a.png'] && assets['tree-a.png'].data_url
  const treeB = assets && assets['tree-b.png'] && assets['tree-b.png'].data_url
  const treeC = assets && assets['tree-c.png'] && assets['tree-c.png'].data_url
  const bush = assets && assets['bush.png'] && assets['bush.png'].data_url
  const fountain = assets && assets['fountain.png'] && assets['fountain.png'].data_url
  const lamp = assets && assets['lamp.png'] && assets['lamp.png'].data_url
  const rock = assets && assets['rock.png'] && assets['rock.png'].data_url
  const personArt = assets && assets['person.png'] && assets['person.png'].data_url
  const workers = Array.isArray(town.workers) ? town.workers : []
  const [filter, setFilter] = useState('all')
  const [paused, setPaused] = useState(false)
  const [bulletinOpen, setBulletinOpen] = useState(false)
  useEffect(() => {
    setBulletinOpen(false)
  }, [town.id])
  const townTime = useTownTime()
  const isDark = townTime.phase === 'night' || townTime.phase === 'dusk'
  const actorMap = useTownSimulation(town, buildingIds, reducedMotion || paused)
  const counts = workers.reduce((acc, worker) => {
    const key = townStatusLabel(worker.status)
    acc[key] = (acc[key] || 0) + 1
    return acc
  }, {})
  const prCount = workers.filter((worker) => !!worker.pr_number).length
  const anyRunning = town.status === 'running' || (counts.running || 0) > 0
  const routerInProgress = town.status === 'running' || town.status === 'queued' ? 1 : 0
  const inProgressCount = routerInProgress + (counts.running || 0) + (counts.queued || 0)
  const totalTasksCount = 1 + workers.length
  const summary = [
    workers.length === 1 ? '1 worker' : `${workers.length} workers`,
    ...Object.keys(counts).sort().map((key) => `${counts[key]} ${key}`),
  ].join(' · ')
  const filterOptions = [
    { id: 'all', label: `All (${workers.length})` },
    { id: 'running', label: `Running (${(counts.running || 0) + (counts.queued || 0)})` },
    { id: 'review', label: `Review (${counts.review || 0})` },
    { id: 'failed', label: `Failed (${(counts.failed || 0) + (counts.cancelled || 0)})` },
    { id: 'pr', label: `PRs (${prCount})` },
  ]

  const trees = [
    { left: 4, top: 72, src: treeA, width: 82 },
    { left: 95, top: 68, src: treeB, width: 76 },
    { left: 3, top: 32, src: treeC, width: 58 },
    { left: 95, top: 29, src: treeA, width: 70 },
    { left: 20, top: 14, src: treeC, width: 50 },
    { left: 80, top: 14, src: treeB, width: 56 },
    { left: 44, top: 18, src: treeB, width: 62 },
    { left: 57, top: 17, src: treeC, width: 52 },
    { left: 19, top: 93, src: treeA, width: 68 },
    { left: 82, top: 93, src: treeB, width: 64 },
  ].filter((tree) => tree.src)

  const decorations = [
    { key: 'fountain', left: 50, top: 76, src: fountain, width: 84 },
    { key: 'well', left: 63, top: 63, src: well, width: 42 },
    { key: 'signpost', left: 37, top: 63, src: signpost, width: 28 },
    { key: 'bench-l', left: 43, top: 76, src: bench, width: 38 },
    { key: 'bench-r', left: 57, top: 76, src: bench, width: 38 },
    { key: 'bench-park', left: 74, top: 73, src: bench, width: 36 },
    { key: 'bridge-sw', left: 24, top: 74, src: bridge, width: 56 },
    { key: 'lamp-nw', left: 39, top: 48, src: lamp, width: 22, glow: anyRunning || isDark },
    { key: 'lamp-ne', left: 61, top: 48, src: lamp, width: 22, glow: anyRunning || isDark },
    { key: 'lamp-sw', left: 36, top: 68, src: lamp, width: 22, glow: anyRunning || isDark },
    { key: 'lamp-se', left: 64, top: 68, src: lamp, width: 22, glow: anyRunning || isDark },
    { key: 'bush-nw', left: 16, top: 32, src: bush, width: 44 },
    { key: 'bush-ne', left: 84, top: 32, src: bush, width: 44 },
    { key: 'bush-sw', left: 16, top: 66, src: bush, width: 44 },
    { key: 'bush-se', left: 84, top: 66, src: bush, width: 44 },
    { key: 'flowers-1', left: 34, top: 36, src: flowers, width: 32 },
    { key: 'flowers-2', left: 66, top: 36, src: flowers, width: 32 },
    { key: 'flowers-3', left: 40, top: 84, src: flowers, width: 32 },
    { key: 'flowers-4', left: 60, top: 84, src: flowers, width: 32 },
    { key: 'rock-l', left: 6, top: 89, src: rock, width: 25 },
    { key: 'rock-r', left: 93, top: 88, src: rock, width: 25 },
  ].filter((item) => item.src)

  const petMap = pets || {}
  const routerPet = petMap[town.avatar_slug] || null

  return jsxs('div', {
    className: 'superagent-pixel relative isolate z-0 flex min-h-0 flex-1 flex-col gap-2',
    style: { isolation: 'isolate', zIndex: 0 },
    children: [
      jsxs('div', {
        key: 'scene',
        role: 'group',
        'aria-label': `${sessionTitle(town)} — town`,
        'data-town-workers': workers.length,
        'data-town-road-network': 'cobblestone',
        className: cn('relative isolate z-0 min-h-[390px] flex-1 overflow-hidden rounded-lg border',
          'border-(--ui-stroke-secondary)'),
        style: grass
          ? {
              isolation: 'isolate',
              zIndex: 0,
              backgroundImage: `url("${grass}")`,
              backgroundSize: '128px 64px',
              backgroundRepeat: 'repeat',
              imageRendering: 'pixelated',
            }
          : { isolation: 'isolate', zIndex: 0 },
        children: [
          // Stream & meadow pond in the southwest quarter
          water
            ? jsx('div', {
                key: 'water-pond',
                'aria-hidden': 'true',
                'data-town-landmark': 'stream',
                className: 'pointer-events-none absolute z-0 rounded-full opacity-90',
                style: {
                  left: '24%', top: '73%', width: '16%', height: '14%',
                  transform: 'translate(-50%, -50%)',
                  backgroundImage: `url("${water}")`,
                  backgroundSize: '48px 48px',
                  backgroundRepeat: 'repeat',
                  imageRendering: 'pixelated',
                  boxShadow: 'inset 0 0 8px rgba(20,55,90,0.45)',
                },
              })
            : null,
          // Cobblestone road network connecting every plot to the Central Plaza
          jsx('div', {
            key: 'road-west-ave', 'aria-hidden': 'true',
            className: 'pointer-events-none absolute z-[1] opacity-90',
            style: {
              left: '20%', top: '51%', width: '24px', height: '70%',
              transform: 'translate(-50%, -50%)',
              backgroundImage: roadV ? `url("${roadV}")` : undefined,
              backgroundColor: roadV ? undefined : 'rgba(145,132,114,0.65)',
              backgroundSize: '24px 48px',
              backgroundRepeat: 'repeat-y',
              imageRendering: 'pixelated',
            },
          }),
          jsx('div', {
            key: 'road-east-ave', 'aria-hidden': 'true',
            className: 'pointer-events-none absolute z-[1] opacity-90',
            style: {
              left: '80%', top: '51%', width: '24px', height: '70%',
              transform: 'translate(-50%, -50%)',
              backgroundImage: roadV ? `url("${roadV}")` : undefined,
              backgroundColor: roadV ? undefined : 'rgba(145,132,114,0.65)',
              backgroundSize: '24px 48px',
              backgroundRepeat: 'repeat-y',
              imageRendering: 'pixelated',
            },
          }),
          jsx('div', {
            key: 'road-north-st', 'aria-hidden': 'true',
            className: 'pointer-events-none absolute z-[1] opacity-90',
            style: {
              left: '50%', top: '24%', width: '82%', height: '22px',
              transform: 'translate(-50%, -50%)',
              backgroundImage: roadH ? `url("${roadH}")` : undefined,
              backgroundColor: roadH ? undefined : 'rgba(145,132,114,0.65)',
              backgroundSize: '48px 22px',
              backgroundRepeat: 'repeat-x',
              imageRendering: 'pixelated',
            },
          }),
          jsx('div', {
            key: 'road-main-st', 'aria-hidden': 'true',
            className: 'pointer-events-none absolute z-[1] opacity-95',
            style: {
              left: '50%', top: '55%', width: '84%', height: '26px',
              transform: 'translate(-50%, -50%)',
              backgroundImage: roadH ? `url("${roadH}")` : undefined,
              backgroundColor: roadH ? undefined : 'rgba(145,132,114,0.75)',
              backgroundSize: '48px 26px',
              backgroundRepeat: 'repeat-x',
              imageRendering: 'pixelated',
            },
          }),
          jsx('div', {
            key: 'road-south-st', 'aria-hidden': 'true',
            className: 'pointer-events-none absolute z-[1] opacity-90',
            style: {
              left: '50%', top: '84%', width: '82%', height: '22px',
              transform: 'translate(-50%, -50%)',
              backgroundImage: roadH ? `url("${roadH}")` : undefined,
              backgroundColor: roadH ? undefined : 'rgba(145,132,114,0.65)',
              backgroundSize: '48px 22px',
              backgroundRepeat: 'repeat-x',
              imageRendering: 'pixelated',
            },
          }),
          jsx('div', {
            key: 'road-center-promenade', 'aria-hidden': 'true',
            className: 'pointer-events-none absolute z-[1] opacity-95',
            style: {
              left: '50%', top: '54%', width: '28px', height: '62%',
              transform: 'translate(-50%, -50%)',
              backgroundImage: roadV ? `url("${roadV}")` : undefined,
              backgroundColor: roadV ? undefined : 'rgba(145,132,114,0.75)',
              backgroundSize: '28px 48px',
              backgroundRepeat: 'repeat-y',
              imageRendering: 'pixelated',
            },
          }),
          // Paved Central Town Square around Router Hall & Fountain
          jsx('div', {
            key: 'plaza', 'aria-hidden': 'true',
            'data-town-landmark': 'plaza',
            className: 'pointer-events-none absolute z-[2] rounded-2xl border border-black/15 shadow-inner',
            style: {
              left: '50%', top: '63%', width: '32%', height: '34%',
              transform: 'translate(-50%, -50%)',
              backgroundImage: plazaStone ? `url("${plazaStone}")` : undefined,
              backgroundColor: plazaStone ? undefined : 'rgba(165,148,126,0.7)',
              backgroundSize: '48px 48px',
              backgroundRepeat: 'repeat',
              imageRendering: 'pixelated',
            },
          }),
          // Device-clock sky tint. Sits above roads/plaza (z-2) but below
          // houses (z-4+) and actors, so it tints the ground, not the UI.
          jsx('div', {
            key: 'sky',
            'aria-hidden': 'true',
            'data-town-time': townTime.phase,
            title: `Town time ${townTime.label} (${townTime.phase})`,
            className: 'superagent-town-sky pointer-events-none absolute inset-0 z-[3]',
            style: { backgroundColor: townTimeOverlay(townTime.phase) },
          }),
          // Router ↔ Worker visual connection paths
          workers.length > 0
            ? jsx('svg', {
                key: 'connections',
                'aria-hidden': 'true',
                'data-town-connections': '1',
                viewBox: '0 0 100 100',
                preserveAspectRatio: 'none',
                className: 'pointer-events-none absolute inset-0 z-[2] h-full w-full',
                children: workers.map((worker) => {
                  const pos = townHousePosition(worker.town_slot, worker.id)
                  const active = worker.status === 'running' || worker.status === 'queued'
                  const matches = matchesTownFilter(worker, filter, false)
                  return jsx('line', {
                    key: `link:${worker.id}`,
                    x1: '50',
                    y1: '47',
                    x2: String(pos.left),
                    y2: String(pos.top),
                    stroke: active ? 'rgba(255, 208, 92, 0.85)' : 'rgba(255, 255, 255, 0.25)',
                    strokeWidth: active ? '0.45' : '0.28',
                    strokeDasharray: active ? '1.2 0.8' : '0.9 1.1',
                    opacity: matches ? '1' : '0.25',
                  })
                }),
              })
            : null,
          ...decorations.map((item) => jsx('img', {
            key: `deco:${item.key}`,
            src: item.src,
            alt: '',
            'aria-hidden': 'true',
            draggable: false,
            className: 'pointer-events-none absolute -translate-x-1/2 -translate-y-full opacity-95',
            style: {
              left: `${item.left}%`, top: `${item.top}%`,
              width: item.width, height: 'auto', imageRendering: 'pixelated',
              zIndex: Math.max(3, Math.min(22, Math.round(item.top / 5))),
              filter: item.glow ? (isDark ? 'drop-shadow(0 0 8px rgba(255,214,102,0.95)) drop-shadow(0 0 2px rgba(255,240,180,0.9))' : 'drop-shadow(0 0 6px rgba(255,214,102,0.85))') : undefined,
            },
          })),
          ...trees.map((tree, index) => jsx('img', {
            key: `tree:${index}`,
            src: tree.src,
            alt: '',
            'aria-hidden': 'true',
            draggable: false,
            className: 'pointer-events-none absolute -translate-x-1/2 -translate-y-full opacity-95',
            style: {
              left: `${tree.left}%`, top: `${tree.top}%`,
              width: tree.width, height: 'auto', imageRendering: 'pixelated',
              zIndex: Math.max(3, Math.min(22, Math.round(tree.top / 5))),
            },
          })),
          jsx(TownBulletinBoard, {
            key: 'bulletin-board',
            assets,
            inProgressCount,
            totalTasksCount,
            onOpenBulletin: () => setBulletinOpen(true),
          }),
          jsx(TownHouse, {
            key: town.id,
            session: town,
            isRouter: true,
            slot: 0,
            onOpen,
            houseRef: registerHouse(town.id),
            building: false,
            reducedMotion,
            assets,
            pet: routerPet,
            dimmed: false,
          }),
          ...workers.map((worker) => jsx(TownHouse, {
            key: worker.id,
            session: worker,
            isRouter: false,
            slot: worker.town_slot,
            onOpen,
            houseRef: registerHouse(worker.id),
            building: !!(buildingIds && buildingIds[worker.id]),
            reducedMotion,
            assets,
            pet: petMap[worker.avatar_slug] || null,
            dimmed: !matchesTownFilter(worker, filter, false),
          })),
          jsx(TownActor, {
            key: `actor:${town.id}`,
            session: town,
            isRouter: true,
            actorState: actorMap[town.id],
            pet: routerPet,
            personArt,
            reducedMotion: reducedMotion || paused,
            dimmed: false,
            onOpen,
          }),
          ...workers.map((worker) => jsx(TownActor, {
            key: `actor:${worker.id}`,
            session: worker,
            isRouter: false,
            actorState: actorMap[worker.id],
            pet: petMap[worker.avatar_slug] || null,
            personArt,
            reducedMotion: reducedMotion || paused,
            dimmed: !matchesTownFilter(worker, filter, false),
            onOpen,
          })),
          // Compact Town HUD overlay (stayed strictly inside TownScene's isolated z-0 stacking context)
          jsxs('div', {
            key: 'hud',
            'data-town-hud': '1',
            className: 'pointer-events-none absolute inset-x-2.5 top-2 z-20 flex flex-wrap items-center justify-between gap-1.5',
            style: { zIndex: 24 },
            children: [
              jsxs('div', {
                key: 'filters',
                role: 'group',
                'aria-label': 'Filter town workers',
                className: cn('pointer-events-auto inline-flex flex-wrap items-center gap-0.5 rounded-md border',
                  'border-(--ui-stroke-secondary) bg-background/90 p-0.5 shadow-sm backdrop-blur-xs'),
                children: filterOptions.map((option) => {
                  const active = filter === option.id
                  return jsx('button', {
                    key: option.id,
                    type: 'button',
                    'aria-pressed': active,
                    onClick: () => setFilter(option.id),
                    className: cn(
                      'rounded px-1.5 py-0.5 text-[0.625rem] leading-tight transition-colors',
                      active
                        ? 'bg-primary font-medium text-primary-foreground'
                        : 'text-(--ui-text-secondary) hover:bg-(--chrome-action-hover)',
                    ),
                    children: option.label,
                  })
                }),
              }),
              jsxs('div', {
                key: 'right',
                className: 'pointer-events-auto inline-flex items-center gap-1.5',
                children: [
                  jsx('span', {
                    key: 'clock',
                    'data-town-clock': townTime.phase,
                    title: `Town time follows this device (${townTime.phase})`,
                    className: cn('superagent-town-clock superagent-pixel-panel inline-flex items-center gap-1 rounded-md border',
                      'border-(--ui-stroke-secondary) bg-background/90 px-2 py-0.5',
                      'text-[0.625rem] leading-tight text-(--ui-text-secondary) shadow-sm'),
                    children: `${townTime.phase === 'night' ? '🌙' : (townTime.phase === 'day' ? '☀️' : '🌤️')} ${townTime.label}`,
                  }),
                  jsx('button', {
                    key: 'pause',
                    type: 'button',
                    'aria-pressed': paused,
                    'aria-label': paused ? 'Resume character movement' : 'Pause character movement',
                    title: paused ? 'Resume autonomous character walking' : 'Pause autonomous character walking',
                    onClick: () => setPaused((prev) => !prev),
                    className: cn(
                      'pointer-events-auto rounded-md border border-(--ui-stroke-secondary) bg-background/90',
                      'px-2 py-0.5 text-[0.625rem] leading-tight text-(--ui-text-secondary) shadow-sm',
                      'hover:bg-(--chrome-action-hover)',
                    ),
                    children: paused ? '▶ Walk' : '⏸ Pause',
                  }),
                ],
              }),
            ],
          }),
          bulletinOpen
            ? jsx(TownBulletinModal, {
                key: 'bulletin-modal',
                town,
                pets: petMap,
                onClose: () => setBulletinOpen(false),
                onSelectTask: (id) => {
                  setBulletinOpen(false)
                  onOpen(id)
                },
              })
            : null,
        ],
      }),
      jsx('p', {
        key: 'status',
        role: 'status',
        className: 'shrink-0 px-1 text-[0.6875rem] text-(--ui-text-quaternary)',
        children: `${sessionTitle(town)} · ${townStatusLabel(town.status)} · ${summary}`,
      }),
    ],
  })
}

function TownSwitcher({
  towns, value, onChange, onPrev, onNext, onNew, onReroll, rerolling,
  failedCount, onRetryFailed, retryingFailed, onSessions, canPrev, canNext, sessionsRef,
}) {
  const current = towns.find((town) => town.id === value)
  return jsxs('div', {
    className: 'flex min-w-0 flex-wrap items-center gap-1.5',
    children: [
      jsxs('div', {
        key: 'picker',
        className: cn('flex shrink-0 items-center rounded border border-(--ui-stroke-secondary)',
          'bg-transparent'),
        children: [
          jsx('button', {
            key: 'prev', type: 'button', onClick: onPrev, disabled: !canPrev,
            'aria-label': 'Previous town', title: 'Previous town',
            className: cn('inline-flex h-7 w-7 items-center justify-center rounded-l',
              'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover)',
              'disabled:opacity-30 disabled:hover:bg-transparent'),
            children: '‹',
          }),
          jsx('select', {
            key: 'select',
            value: value || '',
            'aria-label': 'Select town',
            title: current ? `${sessionTitle(current)} · ${townStatusLabel(current.status)}` : 'Select town',
            onChange: (event) => onChange(event.target.value),
            className: cn('h-7 max-w-[15rem] min-w-[8rem] truncate bg-transparent px-1',
              'text-xs text-(--ui-text-secondary) focus:outline-none'),
            children: towns.length
              ? towns.map((town) => jsx('option', {
                  key: town.id, value: town.id,
                  children: `${sessionTitle(town)} · ${townStatusLabel(town.status)}`,
                }))
              : jsx('option', { key: 'none', value: '', children: 'No towns' }),
          }),
          jsx('button', {
            key: 'next', type: 'button', onClick: onNext, disabled: !canNext,
            'aria-label': 'Next town', title: 'Next town',
            className: cn('inline-flex h-7 w-7 items-center justify-center rounded-r',
              'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover)',
              'disabled:opacity-30 disabled:hover:bg-transparent'),
            children: '›',
          }),
        ],
      }),
      current
        ? jsx('span', {
            key: 'workers',
            title: `${(current.workers || []).length} of ${TOWN_MAX_WORKERS} plots used`,
            className: 'hidden shrink-0 text-[0.6875rem] text-(--ui-text-tertiary) sm:inline',
            children: `${(current.workers || []).length}/${TOWN_MAX_WORKERS} plots`,
          })
        : null,
      current && typeof onReroll === 'function'
        ? jsx('button', {
            key: 'reroll', type: 'button', onClick: onReroll, disabled: !!rerolling,
            'aria-label': 'Reroll character sprites',
            title: 'Randomise Petdex.dev character sprites for this town',
            className: cn('shrink-0 rounded border border-(--ui-stroke-secondary) px-2 py-1',
              'text-xs text-(--ui-text-secondary) hover:bg-(--chrome-action-hover) disabled:opacity-50'),
            children: rerolling ? 'Rerolling…' : '🎲 Reroll sprites',
          })
        : null,
      current && Number(failedCount) > 0 && typeof onRetryFailed === 'function'
        ? jsx('button', {
            key: 'retry-failed', type: 'button', onClick: onRetryFailed, disabled: !!retryingFailed,
            'aria-label': `Retry ${failedCount} failed worker${failedCount === 1 ? '' : 's'}`,
            title: 'Batch retry all failed or cancelled workers in this town',
            className: cn('shrink-0 rounded border border-(--ui-stroke-secondary) px-2 py-1',
              'text-xs text-(--ui-text-secondary) hover:bg-(--chrome-action-hover) disabled:opacity-50'),
            children: retryingFailed ? 'Retrying…' : `↻ Retry failed (${failedCount})`,
          })
        : null,
      jsx('button', {
        key: 'new', type: 'button', onClick: onNew,
        className: cn('shrink-0 rounded bg-primary px-2.5 py-1 text-xs font-medium',
          'text-primary-foreground hover:bg-primary/90'),
        children: 'New town',
      }),
      jsx('button', {
        key: 'sessions', type: 'button', onClick: onSessions, ref: sessionsRef,
        className: cn('shrink-0 rounded border border-(--ui-stroke-secondary) px-2 py-1',
          'text-xs text-(--ui-text-secondary) hover:bg-(--chrome-action-hover)'),
        title: 'Search, rename, pin, archive or delete any chat',
        children: 'Sessions',
      }),
    ],
  })
}

function TownChatDialog({
  sessionId, town, detail, canComment, onSelect, optimistic, canJumpRouter,
  draft, onDraft, onSend, onStop, pending, stopPending, error,
  model, onModelChange, repo, onRepoChange, models, inputRef, recentRepos,
  onSessionModel, modelPending, presets, presetName, onPreset, onPresetClear,
  retry, exportMd, onUnarchive, manage, onClose,
}) {
  const panelRef = useRef(null)
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  const session = detail && detail.data && detail.data.session
  const ready = !!(session && session.id === sessionId)
  const router = session ? session.parent_session_id == null : sessionId === town.id

  useEffect(() => {
    const panel = panelRef.current
    if (!panel) return undefined
    const focusables = () => Array.from(panel.querySelectorAll(
      'button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
    )).filter((element) => element.getClientRects().length > 0)
    const first = panel.querySelector('textarea:not([disabled]), input:not([disabled])')
      || focusables()[0]
    if (first && typeof first.focus === 'function') first.focus()
    else if (typeof panel.focus === 'function') panel.focus()
    const onKeyDown = (event) => {
      if (!event) return
      if (event.key === 'Escape') {
        if (typeof event.preventDefault === 'function') event.preventDefault()
        closeRef.current()
        return
      }
      if (event.key !== 'Tab') return
      const items = focusables()
      if (!items.length) return
      const head = items[0]
      const tail = items[items.length - 1]
      const active = typeof document !== 'undefined' ? document.activeElement : null
      const outside = !panel.contains(active)
      if (event.shiftKey && (active === head || outside)) {
        if (typeof event.preventDefault === 'function') event.preventDefault()
        tail.focus()
      } else if (!event.shiftKey && (active === tail || outside)) {
        if (typeof event.preventDefault === 'function') event.preventDefault()
        head.focus()
      }
    }
    if (typeof window === 'undefined' || typeof window.addEventListener !== 'function') {
      return undefined
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [sessionId])

  return jsx('div', {
    'data-town-dialog': sessionId,
    className: 'absolute inset-0 z-40 flex items-center justify-center bg-black/45 p-3',
    style: { zIndex: 40 },
    onClick: (event) => { if (event && event.target === event.currentTarget) onClose() },
    children: jsxs('section', {
      role: 'dialog',
      'aria-modal': 'true',
      'aria-labelledby': 'superagent-town-dialog-title',
      ref: panelRef,
      tabIndex: -1,
      // Fixed viewport-proportional popup: 54% of the viewport wide, 78%
      // tall. No content-driven sizing anywhere, so a long chat can never
      // push the frame past the viewport; the transcript scrolls inside.
      className: cn('superagent-pixel superagent-pixel-panel flex h-[78vh] w-[54vw] flex-col overflow-hidden rounded-lg',
        'border border-(--ui-stroke-secondary) shadow-xl focus:outline-none'),
      style: {
        backgroundColor: 'var(--card, var(--background, #1c1c1e))',
        color: 'var(--foreground, #f5f5f5)',
        width: '54vw',
        minWidth: '54vw',
        maxWidth: '54vw',
        height: '78vh',
        minHeight: '78vh',
        maxHeight: '78vh',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
        boxSizing: 'border-box',
      },
      children: [
        jsxs('header', {
          key: 'head',
          className: 'flex shrink-0 items-center justify-between gap-2 border-b border-(--ui-stroke-secondary) px-3 py-2',
          children: [
            jsxs('div', {
              key: 'title', className: 'flex min-w-0 items-baseline gap-2',
              children: [
                jsx('span', {
                  key: 'kind', className: 'shrink-0 rounded border border-(--ui-stroke-secondary) px-1 text-[0.625rem] text-(--ui-text-tertiary)',
                  children: router ? 'Router house' : 'Worker house',
                }),
                session
                  ? jsx('span', {
                      key: 'status', className: 'shrink-0 rounded border border-(--ui-stroke-secondary) px-1 text-[0.625rem] text-(--ui-text-secondary)',
                      children: townStatusLabel(session.status),
                    })
                  : null,
                !router && canJumpRouter && typeof onSelect === 'function'
                  ? jsx('button', {
                      key: 'router', type: 'button', onClick: () => onSelect(town.id),
                      className: cn('shrink-0 rounded border border-(--ui-stroke-secondary) px-1',
                        'text-[0.625rem] text-(--ui-text-tertiary)',
                        'hover:bg-(--chrome-action-hover) hover:text-(--ui-text-secondary)'),
                      title: 'Open the town Router',
                      children: '↗ Router',
                    })
                  : null,
                jsx('span', {
                  key: 'id', id: 'superagent-town-dialog-title',
                  className: 'min-w-0 truncate text-sm font-medium text-(--ui-text-secondary)',
                  children: sessionId,
                }),
                jsx('span', {
                  key: 'label', className: 'min-w-0 truncate text-xs text-(--ui-text-quaternary)',
                  children: session ? sessionTitle(session) : '',
                }),
              ],
            }),
            jsxs('div', {
              key: 'actions', className: 'flex shrink-0 items-center gap-1',
              children: [
                manage ? jsx(OverflowMenu, { key: 'manage', items: manage, label: 'House actions' }) : null,
                jsx('button', {
                  key: 'close', type: 'button', onClick: onClose,
                  'aria-label': 'Close chat', title: 'Close (Esc)',
                  className: cn('inline-flex h-7 w-7 items-center justify-center rounded',
                    'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-(--ui-text-secondary)'),
                  children: '✕',
                }),
              ],
            }),
          ],
        }),
        jsx('div', {
          key: 'body',
          className: 'flex min-h-0 flex-1 flex-col gap-2 overflow-hidden p-3',
          style: {
            flex: '1 1 0%',
            minHeight: 0,
            width: '100%',
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
            boxSizing: 'border-box',
          },
          children: ready
            ? jsx(ChatCenter, {
                key: sessionId,
                summary: session, detail,
                sessions: [town, ...(town.workers || [])],
                onSelect,
                banner: null, optimistic,
                draft, onDraft, onSend, onStop, pending, stopPending, error,
                canComment,
                model, onModelChange, repo, onRepoChange, models, inputRef, recentRepos,
                onSessionModel, modelPending, presets, presetName, onPreset, onPresetClear,
                retry, exportMd, onUnarchive,
              })
            : jsx('div', {
                role: 'status',
                className: 'flex flex-1 items-center justify-center text-xs text-(--ui-text-tertiary)',
                children: `Loading ${sessionId}…`,
              }),
        }),
      ],
    }),
  })
}

function TownModal({ title, subtitle, onClose, children }) {
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  const panelRef = useRef(null)
  useEffect(() => {
    const panel = panelRef.current
    if (!panel) return undefined
    const focusables = () => Array.from(panel.querySelectorAll(
      'button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
    )).filter((element) => element.getClientRects().length > 0)
    const first = panel.querySelector('textarea, input') || focusables()[0]
    if (first && typeof first.focus === 'function') first.focus()
    const onKeyDown = (event) => {
      if (!event) return
      if (event.key === 'Escape') {
        if (typeof event.preventDefault === 'function') event.preventDefault()
        closeRef.current()
        return
      }
      if (event.key !== 'Tab') return
      const items = focusables()
      if (!items.length) return
      const head = items[0]
      const tail = items[items.length - 1]
      if (event.shiftKey && (document.activeElement === head || !panel.contains(document.activeElement))) {
        if (typeof event.preventDefault === 'function') event.preventDefault()
        tail.focus()
      } else if (!event.shiftKey && (document.activeElement === tail || !panel.contains(document.activeElement))) {
        if (typeof event.preventDefault === 'function') event.preventDefault()
        head.focus()
      }
    }
    if (typeof window === 'undefined' || typeof window.addEventListener !== 'function') return undefined
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])
  return jsx('div', {
    'data-town-modal': '1',
    className: 'absolute inset-0 z-40 flex items-center justify-center bg-black/45 p-3',
    style: { zIndex: 40 },
    onClick: (event) => { if (event && event.target === event.currentTarget) onClose() },
    children: jsxs('section', {
      role: 'dialog', 'aria-modal': 'true', 'aria-label': title,
      ref: panelRef, tabIndex: -1,
      // Fixed viewport-proportional popup like the chat dialog: 40% of the
      // viewport wide, 70% tall. Body scrolls inside.
      className: cn('superagent-pixel superagent-pixel-panel superagent-pixel-scroll flex h-[70vh] w-[40vw] flex-col gap-2 overflow-y-auto rounded-lg border',
        'border-(--ui-stroke-secondary) p-3 shadow-xl focus:outline-none'),
      style: { backgroundColor: 'var(--card, var(--background, #1c1c1e))', color: 'var(--foreground, #f5f5f5)' },
      children: [
        jsxs('div', {
          key: 'head', className: 'flex items-start justify-between gap-2',
          children: [
            jsxs('div', {
              key: 'text', className: 'flex min-w-0 flex-col',
              children: [
                jsx('span', { key: 't', className: 'text-sm font-medium text-(--ui-text-secondary)', children: title }),
                subtitle ? jsx('span', { key: 's', className: 'text-[0.6875rem] text-(--ui-text-tertiary)', children: subtitle }) : null,
              ],
            }),
            jsx('button', {
              key: 'x', type: 'button', onClick: onClose, 'aria-label': `Close ${title}`,
              title: 'Close (Esc)',
              className: cn('inline-flex h-7 w-7 shrink-0 items-center justify-center rounded',
                'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-(--ui-text-secondary)'),
              children: '✕',
            }),
          ],
        }),
        jsx('div', { key: 'body', className: 'flex min-h-0 flex-col', children }),
      ],
    }),
  })
}

function TownEmpty({ onNew, banner, assets }) {
  const grass = assets && assets['grass.png'] && assets['grass.png'].data_url
  const router = assets && assets['house-router.png'] && assets['house-router.png'].data_url
  const person = assets && assets['person.png'] && assets['person.png'].data_url
  return jsxs('div', {
    className: 'flex min-h-0 flex-1 flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-(--ui-stroke-secondary) p-6 text-center',
    children: [
      jsxs('div', {
        key: 'art',
        'aria-hidden': 'true',
        className: 'relative flex h-36 w-full max-w-xs items-end justify-center overflow-hidden rounded border border-(--ui-stroke-secondary)',
        style: grass
          ? {
              backgroundImage: `url("${grass}")`,
              backgroundSize: '96px 48px',
              imageRendering: 'pixelated',
            }
          : undefined,
        children: [
          router
            ? jsx('img', {
                key: 'router', src: router, alt: '', draggable: false,
                className: 'relative z-10 mb-5',
                style: { width: 112, height: 'auto', imageRendering: 'pixelated' },
              })
            : null,
          person
            ? jsx('span', {
                key: 'person',
                className: 'absolute bottom-4 left-1/2 z-20 -translate-x-1/2',
                style: {
                  width: 28, height: 28, backgroundImage: `url("${person}")`,
                  backgroundSize: '84px 112px', imageRendering: 'pixelated',
                },
              })
            : null,
        ],
      }),
      jsx('div', { key: 'title', className: 'text-sm font-medium text-(--ui-text-secondary)', children: 'No towns yet' }),
      jsx('p', {
        key: 'copy', className: 'max-w-sm text-xs text-(--ui-text-tertiary)',
        children: 'A town starts with one Router session in the centre. Ask it for work, and every worker it spawns becomes a house.',
      }),
      jsx('button', {
        key: 'new', type: 'button', onClick: onNew,
        className: cn('rounded bg-primary px-3 py-1.5 text-xs font-medium',
          'text-primary-foreground hover:bg-primary/90'),
        children: 'New town',
      }),
      banner ? jsx('div', { key: 'banner', className: 'w-full max-w-md text-left', children: banner }) : null,
    ],
  })
}

// -- page -------------------------------------------------------------------

function SuperagentPage() {
  const queryClient = useQueryClient()
  // The rail's "Show archived" toggle: a remembered view, not a per-session
  // flag. `?archived=all` keeps archived rows on the same board so they can be
  // read, unarchived or deleted.
  const [showArchived, setShowArchived] = useState(() => readStored('showArchived', '') === '1')
  const archivedMode = showArchived ? 'all' : 'hide'
  const chatsQuery = useChats(archivedMode)
  const townsQuery = useTowns(archivedMode)
  const healthQuery = useHealth()
  const assetsQuery = useTownAssets()
  const modelsQuery = useModels()
  // `selectedId` is the chat in the dialog; `townId` is what the scene shows.
  // They are deliberately separate: switching towns closes the dialog without
  // touching any session.
  const [selectedId, setSelectedId] = useState(null)
  const [townId, setTownId] = useState(() => readStored('town:selected', ''))
  const [newTownOpen, setNewTownOpen] = useState(false)
  const [newWorkerIds, setNewWorkerIds] = useState({})
  const [reducedMotion, setReducedMotion] = useState(false)
  // The draft is per target; `draft:new` starts the page, a chat restores its
  // own bucket as soon as it is selected. Nothing here ever reads the server's
  // `prompt` field — that would overwrite what the person is typing.
  const [draft, setDraft] = useState(() => readStored(DRAFT_NEW_KEY, ''))
  const draftKeyRef = useRef(DRAFT_NEW_KEY)
  const draftRef = useRef(draft)
  draftRef.current = draft
  // The user's text, shown as a bubble until the server's own row lands.
  const [optimistic, setOptimistic] = useState(null)
  const [recentRepos, setRecentRepos] = useState(() => readRecentRepos())
  const [model, setModel] = useState(() => ({
    model: readStored('lastModel', ''),
    provider: readStored('lastProvider', ''),
    effort: readStored('lastEffort', ''),
  }))
  const [repo, setRepo] = useState(() => readStored('lastRepo', ''))
  // Exactly one drawer at a time: opening either closes the other.
  const [drawer, setDrawer] = useState(null)
  const githubOpen = drawer === 'github'
  const settingsOpen = drawer === 'settings'
  const usageOpen = drawer === 'usage'
  const sessionsOpen = drawer === 'sessions'
  const [presetName, setPresetName] = useState('')
  const [onboarded, setOnboarded] = useState(() => readStored('onboarded', '') === '1')
  const detail = useChat(selectedId)
  const settingsQuery = useSettings(settingsOpen)
  const usageQuery = useUsage(usageOpen)
  const inputRef = useRef(null)
  // Focus returns here when a drawer closes (Escape, ✕, or a pick inside it).
  const githubRef = useRef(null)
  const settingsRef = useRef(null)
  const sessionsRef = useRef(null)
  const railSearchRef = useRef(null)
  const houseRefs = useRef({})
  const houseCallbacksRef = useRef({})
  const knownWorkersRef = useRef({})
  const buildTimers = useRef({})
  const [shortcuts, setShortcuts] = useState(false)

  // Respect the OS setting for construction and walk cycles; CSS also guards
  // this, but the timer for a one-shot build must never be scheduled at all.
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined
    const media = window.matchMedia('(prefers-reduced-motion: reduce)')
    const apply = () => setReducedMotion(!!media.matches)
    apply()
    if (typeof media.addEventListener === 'function') {
      media.addEventListener('change', apply)
      return () => media.removeEventListener('change', apply)
    }
    return undefined
  }, [])

  // One bucket per target. Switching flushes the outgoing text *before* the
  // key moves, so a click 50ms after a keystroke cannot drop the draft.
  useEffect(() => {
    const key = draftKeyFor(selectedId)
    if (key === draftKeyRef.current) return
    writeStored(draftKeyRef.current, draftRef.current)
    draftKeyRef.current = key
    setDraft(readStored(key, ''))
    setOptimistic(null)
  }, [selectedId])

  // Debounced, so typing never writes storage on every keystroke.
  useEffect(() => {
    const key = draftKeyRef.current
    const timer = setTimeout(() => writeStored(key, draft), DRAFT_DEBOUNCE)
    return () => clearTimeout(timer)
  }, [draft])

  // Leaving the page keeps the last keystroke, even inside the debounce window.
  useEffect(() => () => { writeStored(draftKeyRef.current, draftRef.current) }, [])

  const data = chatsQuery.data
  const sessions = useMemo(() => {
    return ((data && data.sessions) || []).slice().sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0))
  }, [data])
  // Completion toasts: diff statuses poll by poll. The ref is seeded by the
  // first effect run, so mounting the page never replays old completions.
  const previousStatus = useRef(null)
  useEffect(() => {
    previousStatus.current = announceCompletions(previousStatus.current, sessions)
  }, [sessions])
  const counts = (data && data.counts) || {}
  const running = (data && data.running) || 0
  const queued = counts.queued || 0
  const selectedSummary = useMemo(
    () => sessions.find((session) => session.id === selectedId) || null,
    [sessions, selectedId],
  )
  const selectedSession = selectedId
    ? ((detail.data && detail.data.session && detail.data.session.id === selectedId)
        ? detail.data.session
        : (selectedSummary && selectedSummary.id === selectedId ? selectedSummary : null))
    : null

  const townData = townsQuery.data
  const towns = useMemo(
    () => ((townData && townData.towns) || []).slice(),
    [townData],
  )
  const town = useMemo(
    () => towns.find((item) => item.id === townId) || null,
    [towns, townId],
  )
  const townIndex = towns.findIndex((item) => item.id === townId)

  // Keep the remembered town valid without ever inventing one: an empty board
  // stays empty, a deleted/archived town falls back to the first visible town.
  useEffect(() => {
    if (!towns.length) {
      if (townId) {
        setTownId('')
        writeStored('town:selected', '')
      }
      return
    }
    if (!towns.some((item) => item.id === townId)) {
      const next = towns[0].id
      setTownId(next)
      writeStored('town:selected', next)
    }
  }, [towns, townId])

  // Construction is a one-shot event, not a render effect. A town's first
  // observation is a baseline; only an id that appears *after* a stored
  // baseline is built. The known list is persisted so a plugin reload does
  // not replay every house, and the timer is owned here so it can be cleared
  // on unmount.
  useEffect(() => {
    const born = {}
    for (const item of towns) {
      const ids = (item.workers || []).map((worker) => worker.id)
      const stored = readKnownWorkers(item.id)
      const hasBaseline = readStored(townKnownKey(item.id), null) !== null
      let seen = knownWorkersRef.current[item.id]
      if (!seen) {
        seen = new Set(stored)
        knownWorkersRef.current[item.id] = seen
      }
      const before = Array.from(seen).join('|')
      for (const id of ids) {
        if (seen.has(id)) continue
        seen.add(id)
        // A missing baseline is the first observation of this town (including
        // an upgrade): seed it, do not animate history. An existing baseline —
        // even an empty one — means this id is genuinely new.
        if (hasBaseline && !reducedMotion) born[id] = true
      }
      const after = Array.from(seen).join('|')
      if (!hasBaseline || after !== before) writeKnownWorkers(item.id, Array.from(seen))
    }
    const fresh = Object.keys(born)
    if (!fresh.length) return
    setNewWorkerIds((current) => {
      const next = { ...current }
      for (const id of fresh) next[id] = true
      return next
    })
    for (const id of fresh) {
      if (buildTimers.current[id]) clearTimeout(buildTimers.current[id])
      buildTimers.current[id] = setTimeout(() => {
        delete buildTimers.current[id]
        setNewWorkerIds((current) => {
          if (!current[id]) return current
          const next = { ...current }
          delete next[id]
          return next
        })
      }, TOWN_BUILD_MS)
    }
  }, [towns, reducedMotion])

  // Every timer this page owns is cleared when the page unmounts; a hot
  // reload must not leave a stray construction callback behind.
  useEffect(() => () => {
    for (const timer of Object.values(buildTimers.current)) clearTimeout(timer)
    buildTimers.current = {}
  }, [])

  const registerHouse = (id) => {
    if (!houseCallbacksRef.current[id]) {
      houseCallbacksRef.current[id] = (element) => {
        if (element) houseRefs.current[id] = element
        else delete houseRefs.current[id]
      }
    }
    return houseCallbacksRef.current[id]
  }

  const openHouse = (id) => {
    setNewTownOpen(false)
    setSelectedId(id)
  }

  const closeHouse = () => {
    const id = selectedId
    setSelectedId(null)
    if (typeof window !== 'undefined') {
      window.setTimeout(() => {
        const element = houseRefs.current[id]
        if (element && typeof element.focus === 'function') element.focus()
      }, 0)
    }
  }

  const selectTown = (id) => {
    const next = String(id || '')
    if (!next || next === townId) return
    setTownId(next)
    writeStored('town:selected', next)
    setSelectedId(null)
    setNewTownOpen(false)
  }

  // A PR badge anywhere on the board keeps the status fresh; the panel keeps
  // it fresh while open. Otherwise the status is fetched once (header dot).
  const hasPrBadge = useMemo(
    () => sessions.some((session) => !!session.pr_number),
    [sessions],
  )
  const githubQuery = useGithubStatus(githubOpen || hasPrBadge)
  const githubPrsQuery = useGithubPrs(githubOpen)

  const createChat = useMutation({
    mutationFn: (body) => rest('/chats', { method: 'POST', body }),
    onSuccess: (result) => {
      invalidateChats(queryClient)
      const created = result && result.session
      if (created && created.id) {
        // A root creates a town; a child (only possible through the API or
        // tool) belongs to the town already on screen. Either way the new
        // session opens in the dialog, so the first turn is visible.
        if (!created.parent_session_id) {
          setTownId(created.id)
          writeStored('town:selected', created.id)
          setNewTownOpen(false)
        }
        setSelectedId(created.id)
      }
    },
  })

  const sendChat = useMutation({
    mutationFn: (vars) => rest(`/chats/${encodeURIComponent(vars.id)}/messages`, {
      method: 'POST', body: { content: vars.content },
    }),
    onSuccess: (result) => {
      invalidateChats(queryClient, selectedId)
      // A session with nothing to resume is replaced by a fresh chat.
      if (result && result.session && result.session.id) setSelectedId(result.session.id)
    },
  })

  const stopChat = useMutation({
    mutationFn: (id) => rest(`/chats/${encodeURIComponent(id)}/stop`, { method: 'POST' }),
    onSuccess: () => invalidateChats(queryClient, selectedId),
  })

  // Retry reuses the prompt already in the transcript — no new message is
  // appended, so the button is only offered for a stopped turn.
  const retryChat = useMutation({
    mutationFn: (id) => rest(`/chats/${encodeURIComponent(id)}/retry`, { method: 'POST' }),
    onSuccess: () => invalidateChats(queryClient, selectedId),
  })

  // The export is a plain read: save the generated Markdown locally, no write
  // to the chat and no change to its state.
  const exportChat = useMutation({
    mutationFn: (id) => fetchChatExport(id),
    onSuccess: (data) => {
      if (data && data.markdown) {
        downloadMarkdown(data.filename || 'superagent-chat.md', data.markdown)
      }
    },
  })

  // A chat's model is fixed at creation; this swaps it for future turns.
  const setChatModel = useMutation({
    mutationFn: (vars) => rest(`/chats/${encodeURIComponent(vars.id)}/model`, {
      method: 'POST',
      body: {
        model: vars.model, provider: vars.provider,
        reasoning_effort: vars.effort || '',
      },
    }),
    onSuccess: () => invalidateChats(queryClient, selectedId),
  })

  /** Paint one session's change before the next poll (a rename, an archive
   *  stamp). A shell without setQueryData simply waits for the refetch. */
  const patchSession = (id, patch) => {
    if (!queryClient || typeof queryClient.setQueryData !== 'function') return
    const apply = (row) => (row.id === id ? { ...row, ...patch } : row)
    queryClient.setQueryData([ID, 'chats', archivedMode], (current) => (
      current && Array.isArray(current.sessions)
        ? { ...current, sessions: current.sessions.map(apply) }
        : current
    ))
    queryClient.setQueryData([ID, 'chat', id], (current) => (
      current && current.session
        ? { ...current, session: { ...current.session, ...patch } }
        : current
    ))
  }

  /** Remove one row from the cached board (delete, or archive while hiding). */
  const dropSession = (id) => {
    if (!queryClient || typeof queryClient.setQueryData !== 'function') return
    queryClient.setQueryData([ID, 'chats', archivedMode], (current) => (
      current && Array.isArray(current.sessions)
        ? { ...current, sessions: current.sessions.filter((row) => row.id !== id) }
        : current
    ))
  }

  const renameChat = useMutation({
    mutationFn: (vars) => rest(`/chats/${encodeURIComponent(vars.id)}`, {
      method: 'PATCH', body: { title: vars.title },
    }),
    // A failed rename must put the old title back, which the refetch does.
    onSuccess: () => invalidateChats(queryClient, selectedId),
    onError: () => invalidateChats(queryClient, selectedId),
  })

  const archiveChat = useMutation({
    mutationFn: (vars) => rest(`/chats/${encodeURIComponent(vars.id)}/archive`, {
      method: 'POST', body: { archived: vars.archived, cascade: !!vars.cascade },
    }),
    onSuccess: () => invalidateChats(queryClient, selectedId),
    onError: () => invalidateChats(queryClient, selectedId),
  })

  // Display-only: the server refuses nothing here, and the rail re-sorts from
  // the refetched board (pinned rows first) rather than guessing locally.
  const pinChat = useMutation({
    mutationFn: (vars) => rest(`/chats/${encodeURIComponent(vars.id)}/pin`, {
      method: 'POST', body: { pinned: vars.pinned },
    }),
    onSuccess: () => invalidateChats(queryClient, selectedId),
    onError: () => invalidateChats(queryClient, selectedId),
  })

  const deleteChat = useMutation({
    mutationFn: (id) => rest(`/chats/${encodeURIComponent(id)}`, { method: 'DELETE' }),
    onSuccess: () => invalidateChats(queryClient, selectedId),
    onError: () => invalidateChats(queryClient, selectedId),
  })

  const renameSession = (id, title) => {
    patchSession(id, { title })
    renameChat.mutate({ id, title })
  }

  const archiveSession = (id, archived, cascade = false) => {
    // A live turn cannot be filed away (the server answers 409). Painting the
    // change first would flash the row out and back, so a busy chat waits for
    // the answer and the error notice instead.
    const target = sessions.find((row) => row.id === id)
    if (!isBusy(target)) {
      if (archived && archivedMode !== 'all') dropSession(id)
      else patchSession(id, { archived_at: archived ? Date.now() / 1000 : null })
      // Hiding the town the dialog belongs to must not leave a transcript
      // floating above the next town.
      if (archived && archivedMode !== 'all' && selectedId === id) setSelectedId(null)
    }
    archiveChat.mutate({ id, archived, cascade })
  }

  const deleteSession = (id) => {
    // Same rule as archive: never hide a row the server may refuse to delete.
    const target = sessions.find((row) => row.id === id)
    if (!isBusy(target)) {
      dropSession(id)
      if (selectedId === id) setSelectedId(null)
      if (townId === id) {
        setTownId('')
        writeStored('town:selected', '')
      }
    }
    deleteChat.mutate(id)
  }

  const toggleArchived = () => {
    const next = !showArchived
    setShowArchived(next)
    writeStored('showArchived', next ? '1' : '0')
  }

  const pending = createChat.isPending || sendChat.isPending
  const stopPending = stopChat.isPending
  const busyTurn = isBusy(selectedSession)
  const notice = (createChat.data && createChat.data.ok === false && createChat.data.error) ||
    (sendChat.data && sendChat.data.ok === false && sendChat.data.error) || ''
  const failure = errorText(createChat.error) || errorText(sendChat.error) ||
    errorText(stopChat.error) || errorText(retryChat.error) ||
    errorText(exportChat.error) || errorText(setChatModel.error) ||
    errorText(renameChat.error) || errorText(archiveChat.error) ||
    errorText(pinChat.error) ||
    errorText(deleteChat.error) || notice

  const changeSessionModel = (next) => {
    if (!selectedSession || !next) return
    const pickedModel = String(next.model || '').trim()
    const pickedProvider = String(next.provider || '').trim()
    const pickedEffort = String(next.effort || '').trim()
    if (!pickedModel && !pickedProvider && !pickedEffort) return
    setChatModel.mutate({
      id: selectedSession.id, model: pickedModel, provider: pickedProvider,
      effort: pickedEffort,
    })
  }

  /** How many rows in the open transcript already carry this exact text, so an
   *  optimistic bubble is replaced — never duplicated — when the server's own
   *  user message lands. */
  const userMessageCount = (text) => {
    const session = selectedSession
    if (!session) return 0
    let count = String(session.prompt || '').trim() === text ? 1 : 0
    for (const message of session.messages || []) {
      if (message && message.role === 'user' && String(message.content || '').trim() === text) count += 1
    }
    return count
  }

  const submit = () => {
    const text = String(draft || '').trim()
    if (!text || pending || busyTurn) return
    const key = draftKeyRef.current
    const sentRepo = String(repo || '').trim()
    // The bubble goes up now; the draft stays in the (disabled) box so a
    // failed send loses nothing.
    setOptimistic({ text, seen: userMessageCount(text) })
    const clear = (nextRepos) => {
      setDraft('')
      writeStored(key, '')
      setOptimistic(null)
      if (nextRepos) setRecentRepos(nextRepos)
    }
    const failed = () => setOptimistic(null)
    if (selectedId) {
      sendChat.mutate({ id: selectedId, content: text }, {
        onSuccess: () => clear(null),
        onError: failed,
      })
      return
    }
    writeStored('lastModel', model.model)
    writeStored('lastProvider', model.provider)
    writeStored('lastEffort', model.effort || '')
    writeStored('lastRepo', repo)
    createChat.mutate(
      {
        prompt: text, model: model.model, provider: model.provider,
        reasoning_effort: model.effort || '', repo,
        preset: presetName || undefined,
      },
      {
        onSuccess: () => clear(sentRepo ? rememberRepo(sentRepo) : null),
        onError: failed,
      },
    )
  }

  // Selection restores that chat's own bucket; the effect above does the work.

  const pickPreset = (preset) => {
    if (!preset) return
    setPresetName(String(preset.name || ''))
    setModel({
      model: String(preset.model || ''),
      provider: String(preset.provider || ''),
      effort: String(model.effort || ''),
    })
  }

  const changeModel = (next) => {
    // An ad-hoc pick is no longer the named preset; the chip clears.
    setPresetName('')
    setModel(next)
  }

  const dismissOnboarding = () => {
    setOnboarded(true)
    writeStored('onboarded', '1')
  }

  const startChat = () => {
    if (typeof window !== 'undefined') {
      window.setTimeout(() => { if (inputRef.current) inputRef.current.focus() }, 0)
    }
  }

  const newChat = () => {
    // The draft is not cleared here: the new-town bucket is what the modal
    // should show, and a click 50ms after a keystroke must not drop it.
    setSelectedId(null)
    setNewTownOpen(true)
    setDrawer(null)
    setPresetName('')
    setModel({
      model: readStored('lastModel', ''),
      provider: readStored('lastProvider', ''),
      effort: readStored('lastEffort', ''),
    })
    setRepo(readStored('lastRepo', ''))
    startChat()
  }

  const presetList = (modelsQuery.data && modelsQuery.data.presets) || []

  // Page-level keys. A field keeps its own keys; the rail owns j/k and the
  // drawer owns Escape, so this only handles the three global commands.
  const newChatRef = useRef(newChat)
  newChatRef.current = newChat
  const townNavRef = useRef({ towns, townId, selectTown })
  townNavRef.current = { towns, townId, selectTown }
  useEffect(() => {
    const onKeyDown = (event) => {
      if (!event || event.defaultPrevented) return
      if (event.ctrlKey || event.metaKey || event.altKey) return
      const target = event.target
      const tag = target && target.tagName ? String(target.tagName).toLowerCase() : ''
      if (tag === 'input' || tag === 'textarea' || tag === 'select'
        || (target && target.isContentEditable)) return
      // A drawer, the dialog, the new-town modal or the sheet owns the
      // keyboard while it is open.
      if (typeof document !== 'undefined'
        && document.querySelector('[data-drawer], [data-shortcuts], [data-town-dialog], [data-town-modal]')) return
      if (event.key === '?') {
        if (typeof event.preventDefault === 'function') event.preventDefault()
        setShortcuts(true)
        return
      }
      if (event.key === '/') {
        if (typeof event.preventDefault === 'function') event.preventDefault()
        setSelectedId(null)
        setNewTownOpen(false)
        setDrawer('sessions')
        if (typeof window !== 'undefined') {
          window.setTimeout(() => {
            const element = railSearchRef.current
            if (element && typeof element.focus === 'function') element.focus()
          }, 0)
        }
        return
      }
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        const nav = townNavRef.current
        if (!nav || !nav.towns || !nav.towns.length) return
        const step = event.key === 'ArrowLeft' ? -1 : 1
        const index = nav.towns.findIndex((item) => item.id === nav.townId)
        const next = index < 0 ? 0 : index + step
        if (next < 0 || next >= nav.towns.length) return
        if (typeof event.preventDefault === 'function') event.preventDefault()
        nav.selectTown(nav.towns[next].id)
        return
      }
      if (event.key === 'n' || event.key === 'N') {
        if (typeof event.preventDefault === 'function') event.preventDefault()
        newChatRef.current()
      }
    }
    if (typeof window === 'undefined' || typeof window.addEventListener !== 'function') {
      return undefined
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  /** Toggle one drawer; the other closes because there is a single slot. */
  const toggleDrawer = (name) => setDrawer((current) => (current === name ? null : name))

  /** Close, then hand focus back to the button that opened it. */
  const closeDrawer = () => {
    const which = drawer
    setDrawer(null)
    const target = which === 'settings'
      ? settingsRef.current
      : (which === 'github'
          ? githubRef.current
          : (which === 'sessions' ? sessionsRef.current : null))
    if (target && typeof target.focus === 'function') target.focus()
  }

  const stop = () => {
    if (!selectedSession || stopPending) return
    stopChat.mutate(selectedSession.id)
  }

  // Only the segments that are non-zero: '2 running · 1 review', never '0 running'.
  const summaryParts = []
  if (running) summaryParts.push(`${running} running`)
  if (queued) summaryParts.push(`${queued} queued`)
  if (counts.review) summaryParts.push(`${counts.review} review`)
  if (counts.failed) summaryParts.push(`${counts.failed} failed`)
  const summaryLabel = summaryParts.join(' · ')

  // The first-run banner rides above the transcript, not above the whole page.
  // A board whose only chats are archived is not a first run: the rail's
  // "Show archived" toggle is the answer there, not an onboarding card.
  const archivedTotal = (data && data.archived_count) || 0
  const onboarding = !chatsQuery.isLoading && !onboarded && sessions.length === 0 && !archivedTotal
    ? jsx(OnboardingBanner, {
        onSettings: () => setDrawer('settings'),
        onGithub: () => setDrawer('github'),
        onStart: newChat,
        onDismiss: dismissOnboarding,
      })
    : null

  // The dialog may have been opened from the town scene, the Sessions drawer
  // or the GitHub panel. Resolve its owning town from the data, not from the
  // town that happened to be on screen when the click happened.
  const dialogSession = selectedSession
  const dialogOwner = useMemo(() => {
    if (!dialogSession) return null
    return towns.find((item) => item.id === dialogSession.id
      || (item.workers || []).some((worker) => worker.id === dialogSession.id)) || null
  }, [towns, dialogSession])
  const dialogTown = dialogOwner || town
  const canJumpRouter = !!(dialogOwner && dialogSession && dialogSession.id !== dialogOwner.id)

  const selectSession = (id) => {
    const owner = towns.find((item) => item.id === id
      || (item.workers || []).some((worker) => worker.id === id))
    if (owner && owner.id !== townId) selectTown(owner.id)
    setSelectedId(id)
  }

  const openSessions = () => {
    setSelectedId(null)
    setNewTownOpen(false)
    setDrawer('sessions')
    if (typeof window !== 'undefined') {
      window.setTimeout(() => {
        const element = railSearchRef.current
        if (element && typeof element.focus === 'function') element.focus()
      }, 0)
    }
  }

  const renameHouse = (session) => {
    if (!session || typeof window === 'undefined' || typeof window.prompt !== 'function') return
    const next = window.prompt('Rename this chat', sessionTitle(session))
    if (next == null) return
    const title = String(next).trim()
    if (title) renameSession(session.id, title)
  }

  const petSlugsKey = useMemo(() => {
    if (!town) return ''
    const slugs = [town.avatar_slug, ...(town.workers || []).map((w) => w.avatar_slug)]
      .filter(Boolean)
      .map(String)
    return Array.from(new Set(slugs)).sort().join(',')
  }, [town])
  const petsQuery = useTownPets(petSlugsKey)
  const petMap = (petsQuery.data && petsQuery.data.pets) || null

  const rerollPets = useMutation({
    mutationFn: (vars) => rest('/town/pets/reroll', { method: 'POST', body: vars }),
    onSuccess: () => {
      invalidateChats(queryClient, selectedId)
      queryClient.invalidateQueries({ queryKey: [ID, 'town', 'pets'] })
    },
  })

  const dispatchWorker = useMutation({
    mutationFn: (vars) => rest(`/towns/${encodeURIComponent(vars.rootId)}/dispatch`, {
      method: 'POST',
      body: {
        worker_session_id: vars.workerId,
        action: vars.action,
        pr_number: vars.prNumber || undefined,
        message: vars.message || undefined,
      },
    }),
    onSuccess: () => invalidateChats(queryClient, selectedId),
  })

  const retryFailedWorkers = useMutation({
    mutationFn: (rootId) => rest(`/towns/${encodeURIComponent(rootId)}/retry-failed`, {
      method: 'POST',
    }),
    onSuccess: () => invalidateChats(queryClient, selectedId),
  })

  const failedWorkersCount = useMemo(() => {
    if (!town || !Array.isArray(town.workers)) return 0
    return town.workers.filter((w) => w.status === 'failed' || w.status === 'cancelled').length
  }, [town])

  const houseMenu = dialogSession ? [
    { id: 'rename', label: 'Rename…', onSelect: () => renameHouse(dialogSession) },
    {
      id: 'reroll-sprite',
      label: '🎲 Reroll character sprite',
      disabled: rerollPets.isPending,
      onSelect: () => rerollPets.mutate({ session_id: dialogSession.id }),
    },
    ...(dialogSession.parent_session_id && dialogTown
      ? [
          {
            id: 'dispatch-create-pr',
            label: 'Instruct worker to open PR',
            disabled: isBusy(dialogSession) || dispatchWorker.isPending,
            onSelect: () => dispatchWorker.mutate({
              rootId: dialogTown.id,
              workerId: dialogSession.id,
              action: 'create_pr',
            }),
          },
          {
            id: 'dispatch-approve-pr',
            label: 'Instruct worker to approve PR',
            disabled: isBusy(dialogSession) || !dialogSession.pr_number || dispatchWorker.isPending,
            onSelect: () => dispatchWorker.mutate({
              rootId: dialogTown.id,
              workerId: dialogSession.id,
              action: 'approve_pr',
              prNumber: dialogSession.pr_number,
            }),
          },
        ]
      : []),
    {
      id: 'pin',
      label: dialogSession.pinned_at ? 'Unpin from the top' : 'Pin to the top',
      onSelect: () => pinChat.mutate({ id: dialogSession.id, pinned: !dialogSession.pinned_at }),
    },
    {
      id: 'archive',
      label: dialogSession.archived_at ? 'Unarchive this chat' : 'Archive this chat',
      onSelect: () => archiveSession(dialogSession.id, !dialogSession.archived_at),
    },
    ...(!dialogSession.parent_session_id && dialogTown && Array.isArray(dialogTown.workers) && dialogTown.workers.length > 0
      ? [{
          id: 'archive-cascade',
          label: dialogSession.archived_at ? 'Unarchive town + workers' : 'Archive town + workers',
          onSelect: () => archiveSession(dialogSession.id, !dialogSession.archived_at, true),
        }]
      : []),
    { id: 'sessions', label: 'Search all sessions…', onSelect: openSessions },
  ] : null

  const composerProps = {
    draft, onDraft: setDraft, onSend: submit, onStop: stop, pending, stopPending, error: failure,
    model, onModelChange: changeModel, repo, onRepoChange: setRepo,
    models: modelsQuery.data, inputRef, recentRepos,
    onSessionModel: changeSessionModel, modelPending: setChatModel.isPending,
    presets: presetList, presetName, onPreset: pickPreset,
    onPresetClear: () => setPresetName(''),
  }
  const assetMap = (assetsQuery.data && assetsQuery.data.assets) || null
  const health = healthQuery.data || null
  const staleBackend = backendNeedsRestart(health)
  const townCount = (townData && townData.town_count) || towns.length
  const orphaned = (townData && townData.orphaned_count) || 0

  return jsxs('div', {
    className: 'relative flex h-full min-h-0 flex-col gap-3 overflow-hidden p-4 text-sm',
    children: [
      jsx(TownStyles, { key: 'styles' }),
      jsxs('div', {
        key: 'header', className: 'flex shrink-0 flex-wrap items-center justify-between gap-2',
        children: [
          jsxs('div', {
            key: 'title', className: 'flex min-w-0 items-baseline gap-2',
            children: [
              jsx('span', { key: 'name', className: 'shrink-0 text-sm font-medium', children: 'Superagent Towns' }),
              jsx('span', {
                key: 'towns', className: 'shrink-0 text-xs text-(--ui-text-tertiary)',
                children: townCount ? `${townCount} town${townCount === 1 ? '' : 's'}` : '',
              }),
              summaryLabel
                ? jsx('span', {
                    key: 'counts', className: 'min-w-0 truncate text-xs text-(--ui-text-tertiary)',
                    children: `· ${summaryLabel}`,
                  })
                : null,
              orphaned
                ? jsx('button', {
                    key: 'orphans', type: 'button', onClick: openSessions,
                    title: 'Worker sessions whose root was deleted are kept in Sessions, never drawn as towns.',
                    className: cn('shrink-0 rounded px-1 text-xs text-(--ui-text-secondary)',
                      'hover:bg-(--chrome-action-hover) hover:text-(--ui-text-primary)'),
                    children: `· ${orphaned} orphaned`,
                  })
                : null,
            ],
          }),
          jsx(TownSwitcher, {
            key: 'switcher', towns, value: townId, onChange: selectTown,
            onPrev: () => { if (townIndex > 0) selectTown(towns[townIndex - 1].id) },
            onNext: () => { if (townIndex >= 0 && townIndex < towns.length - 1) selectTown(towns[townIndex + 1].id) },
            onNew: newChat,
            onReroll: town ? () => rerollPets.mutate({ town_id: town.id }) : undefined,
            rerolling: rerollPets.isPending,
            failedCount: failedWorkersCount,
            onRetryFailed: town && failedWorkersCount > 0 ? () => retryFailedWorkers.mutate(town.id) : undefined,
            retryingFailed: retryFailedWorkers.isPending,
            onSessions: openSessions,
            canPrev: townIndex > 0,
            canNext: townIndex >= 0 && townIndex < towns.length - 1,
            sessionsRef,
          }),
          jsxs('div', {
            key: 'actions', className: 'flex shrink-0 items-center gap-1.5',
            children: [
              jsx(GithubHeaderButton, {
                key: 'github', open: githubOpen, status: githubQuery.data,
                onClick: () => toggleDrawer('github'), buttonRef: githubRef,
              }),
              jsx(HeaderIconButton, {
                key: 'usage', label: 'Usage',
                tip: 'Usage: chats, tokens and models for the last 14 days',
                active: usageOpen,
                onClick: () => toggleDrawer('usage'),
                children: '▤',
              }),
              jsx(HeaderLabelButton, {
                key: 'settings', label: 'Settings',
                tip: 'Settings: default model, thinking level, presets and worker limits',
                active: settingsOpen,
                onClick: () => toggleDrawer('settings'),
                buttonRef: settingsRef,
                children: [
                  jsx('span', { key: 'g', 'aria-hidden': 'true', children: '⚙' }),
                  jsx('span', { key: 't', children: 'Settings' }),
                ],
              }),
            ],
          }),
        ],
      }),
      chatsQuery.isError || townsQuery.isError
        ? jsx('div', {
            key: 'error', className: 'shrink-0',
            children: jsx(ErrorCard, {
              title: townsQuery.isError && /404/.test(errorText(townsQuery.error))
                ? 'Towns route missing. Restart the gateway from an outside shell, then reload desktop plugins.'
                : 'Superagent backend unavailable. Enable the plugin first.',
              error: chatsQuery.error || townsQuery.error,
              onRetry: () => { chatsQuery.refetch(); townsQuery.refetch() },
            }),
          })
        : null,
      staleBackend
        ? jsx('div', {
            key: 'stale-backend', 'data-backend-stale': true,
            className: cn('shrink-0 rounded border border-(--ui-stroke-secondary)',
              'bg-background px-3 py-2 text-xs text-(--ui-text-secondary)'),
            children: [
              jsx('span', { key: 'lead', className: 'font-medium', children: 'Gateway needs a restart. ' }),
              jsx('span', {
                key: 'detail',
                children: `This desktop expects towns and thinking levels, but the gateway loaded API v${health.api_version == null ? '?' : health.api_version}. `,
              }),
              jsx('code', {
                key: 'cmd',
                className: 'rounded bg-(--chrome-action-hover) px-1 py-0.5 font-mono text-[0.6875rem]',
                children: 'hermes gateway restart',
              }),
              jsx('span', {
                key: 'tail',
                children: ' from an outside shell, then reload desktop plugins.',
              }),
            ],
          })
        : null,
      jsx('div', {
        key: 'body',
        className: 'relative isolate z-0 flex min-h-0 flex-1 flex-col',
        style: { isolation: 'isolate', zIndex: 0 },
        children: townsQuery.isLoading && !townData
          ? jsx('div', {
              className: 'flex flex-1 items-center justify-center text-xs text-(--ui-text-tertiary)',
              children: 'Loading towns…',
            })
          : !town
            ? jsx(TownEmpty, { onNew: newChat, banner: onboarding, assets: assetMap })
            : jsx(TownScene, {
                town, assets: assetMap, pets: petMap, buildingIds: newWorkerIds,
                onOpen: openHouse, registerHouse, reducedMotion,
              }),
      }),
      newTownOpen
        ? jsx(TownModal, {
            key: 'new-town',
            title: 'New town',
            subtitle: 'One root Router session starts the town. Ask for one job or several; the Router can spawn real workers with superagent_spawn.',
            onClose: () => setNewTownOpen(false),
            children: jsx(Composer, { mode: 'new', ...composerProps }),
          })
        : null,
      selectedId && dialogTown
        ? jsx(TownChatDialog, {
            key: `dialog:${selectedId}`,
            sessionId: selectedId,
            town: dialogTown,
            detail,
            canComment: !!(githubQuery.data && githubQuery.data.token_set),
            canJumpRouter,
            onSelect: selectSession,
            optimistic,
            ...composerProps,
            retry: dialogSession
              && (dialogSession.status === 'failed' || dialogSession.status === 'cancelled')
              ? { pending: retryChat.isPending, onRetry: () => retryChat.mutate(dialogSession.id) }
              : null,
            exportMd: dialogSession
              ? { pending: exportChat.isPending, onExport: () => exportChat.mutate(dialogSession.id) }
              : null,
            onUnarchive: dialogSession
              ? () => archiveSession(dialogSession.id, false)
              : null,
            manage: houseMenu,
            onClose: closeHouse,
          })
        : null,
      jsx(ShortcutSheet, {
        key: 'shortcuts', open: shortcuts, onClose: () => setShortcuts(false),
      }),
      sessionsOpen
        ? jsx(OverlayDrawer, {
            key: 'sessions-drawer',
            panel: 'sessions',
            title: 'Sessions',
            subtitle: `${sessions.length} chat${sessions.length === 1 ? '' : 's'} · search, rename, pin, archive, delete`,
            onClose: closeDrawer,
            children: jsx(SessionsRail, {
              sessions, selectedId,
              onSelect: (id) => { selectSession(id); setDrawer(null) },
              onRename: renameSession, onArchive: archiveSession, onDelete: deleteSession,
              showArchived, onToggleArchived: toggleArchived,
              archivedCount: archivedTotal, searchRef: railSearchRef,
              onPin: (id, pinned) => pinChat.mutate({ id, pinned }),
              embedded: true,
            }),
          })
        : null,
      githubOpen
        ? jsx(OverlayDrawer, {
            key: 'github-drawer',
            panel: 'github',
            title: 'GitHub',
            subtitle: (githubQuery.data && githubQuery.data.repo) || 'no repository',
            onClose: closeDrawer,
            children: jsx(GithubPanel, {
              status: githubQuery.data,
              prs: githubPrsQuery.data, prsLoading: githubPrsQuery.isLoading,
              onSelectSession: (id) => { selectSession(id); closeDrawer() },
            }),
          })
        : null,
      settingsOpen
        ? jsx(OverlayDrawer, {
            key: 'settings-drawer',
            panel: 'settings',
            title: 'Settings',
            subtitle: 'defaults, presets, worker limits',
            onClose: closeDrawer,
            children: jsx(SettingsPanel, {
              settings: settingsQuery.data, models: modelsQuery.data,
              loading: settingsQuery.isLoading, error: settingsQuery.error,
              onRetry: () => settingsQuery.refetch(),
            }),
          })
        : null,
      usageOpen
        ? jsx(OverlayDrawer, {
            key: 'usage-drawer',
            panel: 'usage',
            title: 'Usage',
            subtitle: 'last 14 days',
            onClose: closeDrawer,
            children: jsx(UsagePanel, {
              usage: usageQuery.data, loading: usageQuery.isLoading,
              error: usageQuery.error, onRetry: () => usageQuery.refetch(),
            }),
          })
        : null,
    ],
  })
}

// -- ticket pane + chip (iteration 1 surface, kept) -------------------------

function TicketRow({ ticket }) {
  return jsxs('div', {
    className: 'flex flex-col gap-0.5 rounded px-2 py-1.5 text-xs',
    children: [
      jsxs('div', {
        key: 'head', className: 'flex items-baseline gap-1.5',
        children: [
          jsx('span', { key: 'icon', className: 'text-(--ui-text-quaternary)', children: ICONS[ticket.status] || '?' }),
          jsx('span', { key: 'id', className: 'font-mono text-(--ui-text-tertiary)', children: ticket.id }),
          jsx('span', { key: 'status', className: 'text-(--ui-text-tertiary)', children: ticket.status }),
          jsx('span', { key: 'req', className: 'truncate text-(--ui-text-secondary)', children: ticket.request }),
        ],
      }),
      ticket.reason
        ? jsx('div', { key: 'reason', className: 'truncate pl-4 text-(--ui-text-quaternary)', children: ticket.reason })
        : null,
    ],
  })
}

function ChatMiniRow({ session }) {
  return jsxs('div', {
    className: 'flex min-w-0 items-baseline gap-1.5 px-2 text-xs',
    children: [
      jsx(StatusGlyph, { key: 'g', status: session.status, pulse: true }),
      jsx('span', { key: 't', className: 'min-w-0 flex-1 truncate text-(--ui-text-secondary)', children: sessionTitle(session) }),
      jsx('span', { key: 'a', className: 'shrink-0 text-(--ui-text-quaternary)', children: timingLabel(session) }),
    ],
  })
}

function SuperagentPane() {
  const { data, isLoading, isError, error, refetch } = useTickets()
  const chats = useChats()

  if (isLoading && !data) {
    return jsx('div', { className: 'p-3 text-xs text-(--ui-text-tertiary)', children: 'Loading tickets…' })
  }

  if (isError) {
    return jsxs('div', {
      className: 'flex flex-col gap-2 p-3 text-xs',
      children: [
        jsx('div', {
          key: 'msg', className: 'text-(--ui-text-tertiary)',
          children: 'Superagent backend unavailable. Enable the plugin first.',
        }),
        jsx('div', { key: 'err', className: 'truncate text-(--ui-text-quaternary)', children: errorText(error) }),
        jsx('button', {
          key: 'retry', type: 'button',
          className: cn('self-start rounded px-2 py-1', 'text-(--ui-text-secondary) hover:bg-(--chrome-action-hover)'),
          onClick: () => refetch(),
          children: 'Retry',
        }),
      ],
    })
  }

  const attention = (data && data.attention) || []
  const tickets = (data && data.tickets) || []
  const chatSessions = (chats.data && chats.data.sessions) || []
  const chatRunning = (chats.data && chats.data.running) || 0
  const live = chatSessions.filter((session) => isBusy(session) || session.status === 'review').slice(0, 4)

  return jsxs('div', {
    className: 'flex h-full flex-col gap-2 overflow-y-auto p-3 text-sm',
    children: [
      jsx('div', {
        key: 'head', className: 'flex items-center justify-between text-xs text-(--ui-text-tertiary)',
        children: [
          jsx('span', {
            key: 'counts',
            children: `chats ${chatRunning} running · tickets ${(data && data.running) || 0} running`,
          }),
          jsx('button', {
            key: 'refresh', type: 'button',
            className: 'rounded px-1.5 py-0.5 hover:bg-(--chrome-action-hover) hover:text-foreground',
            onClick: () => { refetch(); chats.refetch() },
            children: 'Refresh',
          }),
        ],
      }),
      live.length
        ? jsxs('div', {
            key: 'chats', className: 'flex flex-col gap-1',
            children: [
              jsx('div', { key: 'label', className: 'text-xs font-medium text-(--ui-text-tertiary)', children: 'Latest chats' }),
              ...live.map((session) => jsx(ChatMiniRow, { key: session.id, session })),
            ],
          })
        : null,
      attention.length
        ? jsxs('div', {
            key: 'needs', className: 'flex flex-col gap-1',
            children: [
              jsx('div', { key: 'label', className: 'text-xs font-medium', children: 'Needs you' }),
              ...attention.map((ticket) => jsx(TicketRow, { key: ticket.id, ticket })),
            ],
          })
        : jsx('div', { key: 'clear', className: 'text-xs text-(--ui-text-tertiary)', children: 'Nothing needs you.' }),
      tickets.length
        ? jsxs('div', {
            key: 'recent', className: 'flex flex-col gap-1',
            children: [
              jsx('div', { key: 'label', className: 'text-xs font-medium text-(--ui-text-tertiary)', children: 'Recent tickets' }),
              ...tickets.slice(0, 12).map((ticket) => jsx(TicketRow, { key: ticket.id, ticket })),
            ],
          })
        : null,
    ],
  })
}

function SuperagentChip() {
  const tickets = useTickets()
  const chats = useChats()
  const chatData = chats.data
  const counts = (chatData && chatData.counts) || {}
  const running = (chatData && chatData.running) || 0
  const waiting = (counts.review || 0) + (counts.failed || 0)
  // Chats are the primary surface: the chip speaks for them as soon as the
  // list returns. Ticket attention is only the fallback for a backend that has
  // not answered at all (disabled plugin, first paint, older data source).
  const ticketAttention = (tickets.data && tickets.data.attention && tickets.data.attention.length) || 0
  const attention = chatData ? waiting : ticketAttention
  const count = running + attention
  const parts = []
  if (running) parts.push(`▶ ${running}`)
  if (attention) parts.push(`◆ ${attention}`)
  const label = parts.length ? parts.join(' ') : 'superagent'
  const unit = chatData ? 'chat' : 'ticket'
  const plural = (n) => `${n} ${unit}${n === 1 ? '' : 's'}`
  const tip = running && attention
    ? `${plural(running)} running · ${plural(attention)} need you`
    : running
      ? `${plural(running)} running`
      : attention > 0
        ? (chatData ? `${plural(attention)} need review` : `${plural(attention)} need a human`)
        : 'Superagent: nothing needs you'
  return jsx(Tip, {
    label: tip,
    children: jsx('span', {
      className: cn('inline-flex h-full items-center px-1.5 text-[0.6875rem]',
        count > 0 ? 'text-foreground' : 'text-(--ui-text-tertiary)'),
      children: label,
    }),
  })
}

// Named exports for the render probe. The app only consumes the default
// export; these let a Node test render the town tree without Electron.
export {
  SuperagentPage, SettingsPanel, OverlayDrawer, ModelPicker, PresetSelect,
  modelWithThinking, reasoningLabel, backendNeedsRestart,
  TownScene, TownHouse, TownActor, PetdexSprite, TownSpeechBubble,
  TownBulletinBoard, TownBulletinModal,
  TownSwitcher, TownChatDialog, TownEmpty, CommentBox, CreatePrBox,
  townHousePosition, townHouseSprite, townStatusLabel, deriveSpeechBubble, TOWN_PLOTS,
}

export default {
  id: ID, // must match the folder name
  name: 'Superagent',
  register(ctx) {
    rest = ctx.rest
    storage = ctx.storage && typeof ctx.storage.get === 'function' ? ctx.storage : null
    openExternal = ctx.os && typeof ctx.os.openExternal === 'function' ? ctx.os.openExternal : null
    ctx.register({
      id: 'pane',
      area: 'panes',
      title: 'Superagent',
      data: { placement: 'right', width: '300px' },
      render: () => jsx(SuperagentPane, {}),
    })
    ctx.register({
      id: 'chip',
      area: 'statusBar.right',
      order: 130,
      render: () => jsx(SuperagentChip, {}),
    })
    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/superagent' },
        render: () => jsx(SuperagentPage, {}),
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        data: { path: '/superagent', label: 'Superagent', codicon: 'project' },
      },
    ])
  },
}
