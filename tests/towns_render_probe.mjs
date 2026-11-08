/**
 * Render probe for the Superagent Towns desktop tree.
 *
 * The plugin is uncompiled ESM that imports the app's SDK and React. This
 * script rewrites exactly those three import specifiers to local stubs,
 * imports the real file, and walks the returned element tree to text. It
 * catches ReferenceErrors, unguarded nulls and wrong prop wiring without
 * Electron; layout, pixel rendering and polling timing still need the app.
 */

import assert from 'node:assert/strict'
import { mkdtemp, readFile, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

const sourcePath = new URL('../desktop/plugin.js', import.meta.url)
const source = await readFile(sourcePath, 'utf8')

const react = {
  memo: (fn) => fn,
  useEffect: () => undefined,
  useMemo: (fn) => fn(),
  useRef: (value) => ({ current: value }),
  useState: (value) => [typeof value === 'function' ? value() : value, () => undefined],
  useCallback: (fn) => fn,
}

const sdk = {
  Tip: ({ children }) => children,
  StatusDot: undefined,
  DropdownMenu: undefined,
  DropdownMenuTrigger: undefined,
  DropdownMenuContent: undefined,
  ModelCatalogMenu: undefined,
  ModelMenuCloseContext: undefined,
  cn: (...parts) => parts.flat().filter(Boolean).join(' '),
  host: {
    notify: () => undefined,
    openSession: () => Promise.resolve(),
    openWorkspace: () => undefined,
    state: {},
    request: () => Promise.resolve({}),
    onEvent: () => () => undefined,
  },
  useQuery: () => ({
    data: { files: [], sessions: [], presets: [], models: [] },
    isLoading: false,
    isError: false,
    isFetching: false,
    error: null,
    refetch: () => undefined,
  }),
  useMutation: () => ({ mutate: () => undefined, isPending: false, error: null }),
  useQueryClient: () => ({ invalidateQueries: () => undefined, setQueryData: () => undefined }),
  ROUTES_AREA: 'routes',
  SIDEBAR_NAV_AREA: 'sidebar',
  MessageTextContent: undefined,
  CopyButton: undefined,
  DisclosureCaret: undefined,
  ConfirmDialog: undefined,
}

globalThis.__SDK = sdk
globalThis.__React = react
globalThis.__JSX = {
  jsx: (type, props) => ({ type, props: props || {} }),
  jsxs: (type, props) => ({ type, props: props || {} }),
}

let rewritten = source
  .replace("import * as SDK from '@hermes/plugin-sdk'", 'const SDK = globalThis.__SDK')
  .replace("import { jsx, jsxs } from 'react/jsx-runtime'", 'const { jsx, jsxs } = globalThis.__JSX')
  .replace(
    "import { memo, useEffect, useMemo, useRef, useState } from 'react'",
    'const { memo, useEffect, useMemo, useRef, useState } = globalThis.__React',
  )
  .replace('\r\n', '\n')

if (/^import\s/m.test(rewritten) || /^import .*@hermes\/plugin-sdk/m.test(rewritten)) {
  throw new Error('probe failed to rewrite every import')
}

const dir = await mkdtemp(join(tmpdir(), 'superagent-town-probe-'))
const probePath = join(dir, 'plugin.mjs')
await writeFile(probePath, rewritten, 'utf8')
const plugin = await import(pathToFileURL(probePath).href)

const assets = Object.fromEntries(
  ['grass.png', 'house-router.png', 'house-a.png', 'house-b.png', 'house-c.png', 'house-d.png',
    'person.png', 'tree-a.png', 'tree-b.png', 'tree-c.png', 'bush.png', 'fountain.png', 'rock.png', 'lamp.png']
    .map((name) => [name, { data_url: 'data:image/png;base64,AA==' }]),
)

const root = {
  id: 'S-0001',
  title: 'Build the town',
  prompt: 'Build the town',
  status: 'review',
  model: 'test-model',
  provider: 'test',
  repo: '',
  messages: [{ role: 'assistant', content: 'Router summary.', created_at: 2 }],
  events: [],
  workers: [],
  town_slot: null,
}
const workerOne = {
  id: 'S-0002', title: 'Fix the parser', prompt: 'Fix the parser', status: 'running',
  parent_session_id: root.id, town_slot: 0, messages: [], events: [],
}
const workerTwo = {
  id: 'S-0003', title: 'Add a test', prompt: 'Add a test', status: 'review',
  parent_session_id: root.id, town_slot: 1, messages: [], events: [],
}
root.workers = [workerOne, workerTwo]

function render(node) {
  if (node == null || node === false || node === true) return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(render).join(' ')
  if (typeof node.type === 'function') return render(node.type(node.props || {}))
  if (!node.type || node.type === 'style') return ''
  const props = node.props || {}
  const bits = []
  for (const key of ['aria-label', 'data-town-house', 'data-town-dialog', 'data-drawer', 'id', 'role']) {
    if (props[key] != null) bits.push(`${key}="${props[key]}"`)
  }
  if (props.className) bits.push(`class="${props.className}"`)
  if (props.style && props.style.backgroundColor) bits.push(`bg="${props.style.backgroundColor}"`)
  const children = props.children
  if (children != null) bits.push(Array.isArray(children) ? children.map(render).join(' ') : render(children))
  return bits.join(' ')
}

const h = (type, props) => ({ type, props: props || {} })

const scene = render(h(plugin.TownScene, {
  town: root,
  assets,
  buildingIds: { 'S-0002': true },
  onOpen: () => undefined,
  registerHouse: () => () => undefined,
  reducedMotion: false,
}))
assert.match(scene, /Router house S-0001/)
assert.match(scene, /Worker house S-0002/)
assert.match(scene, /Worker house S-0003/)
assert.match(scene, /running/)
assert.match(scene, /review/)
assert.match(scene, /superagent-town-build/)
assert.match(scene, /data-town-house="S-0002"/)

const quiet = render(h(plugin.TownScene, {
  town: root,
  assets,
  buildingIds: { 'S-0002': true },
  onOpen: () => undefined,
  registerHouse: () => () => undefined,
  reducedMotion: true,
}))
assert.doesNotMatch(quiet, /superagent-town-build/)

assert.deepEqual(plugin.townHousePosition(0), plugin.townHousePosition(0))
assert.deepEqual(plugin.townHousePosition(3), { left: 90, top: 50, scale: 0.72 })
assert.ok(plugin.townHousePosition(12).left >= 0)

const switcher = render(h(plugin.TownSwitcher, {
  towns: [root],
  value: root.id,
  onChange: () => undefined,
  onPrev: () => undefined,
  onNext: () => undefined,
  onNew: () => undefined,
  onSessions: () => undefined,
  canPrev: false,
  canNext: false,
}))
assert.match(switcher, /Previous town/)
assert.match(switcher, /Next town/)
assert.match(switcher, /New town/)
assert.match(switcher, /Sessions/)

const dialogBase = {
  sessionId: root.id,
  town: root,
  detail: { data: { session: root }, isLoading: false, isError: false, error: null, refetch: () => undefined },
  canComment: false,
  onSelect: () => undefined,
  optimistic: null,
  draft: '',
  onDraft: () => undefined,
  onSend: () => undefined,
  onStop: () => undefined,
  pending: false,
  stopPending: false,
  error: '',
  model: { model: 'test-model', provider: 'test' },
  onModelChange: () => undefined,
  repo: '',
  onRepoChange: () => undefined,
  models: { models: ['test-model'], presets: [] },
  inputRef: { current: null },
  recentRepos: [],
  onSessionModel: () => undefined,
  modelPending: false,
  presets: [],
  presetName: '',
  onPreset: () => undefined,
  onPresetClear: () => undefined,
  retry: null,
  exportMd: null,
  onUnarchive: null,
  manage: [],
  onClose: () => undefined,
}
const dialog = render(h(plugin.TownChatDialog, dialogBase))
assert.match(dialog, /Router house/)
assert.match(dialog, /Build the town/)
assert.match(dialog, /Router summary\./)

const stale = render(h(plugin.TownChatDialog, {
  ...dialogBase,
  detail: { data: { session: workerOne }, isLoading: false, isError: false, error: null, refetch: () => undefined },
}))
assert.match(stale, /Loading S-0001/)
assert.doesNotMatch(stale, /Router summary\./)

const empty = render(h(plugin.TownEmpty, { onNew: () => undefined, banner: null }))
assert.match(empty, /No towns yet/)

const page = render(h(plugin.SuperagentPage, {}))
assert.match(page, /Superagent Towns/)
assert.match(page, /No towns yet/)

const drawer = render(h(plugin.OverlayDrawer, {
  panel: 'settings', title: 'Settings', subtitle: 'defaults', onClose: () => undefined, children: 'body',
}))
assert.match(drawer, /data-drawer="settings"/)
assert.match(drawer, /bg="var\(--card\)"/)
assert.match(drawer, /md:w-\[420px\]/)

const settingsPanel = render(h(plugin.SettingsPanel, {
  settings: {
    default_model: 'test-model', default_provider: 'test',
    default_reasoning_effort: 'xhigh', model_presets: [],
    max_workers: 2, chat_max_turns: 120, worker_yolo: true,
  },
  models: { models: ['test-model'], presets: [] },
  loading: false,
  error: null,
  onRetry: () => undefined,
}))
assert.match(settingsPanel, /Default model/)
assert.match(settingsPanel, /Default thinking/)
assert.match(settingsPanel, /Extra high/)
assert.match(settingsPanel, /Ultra/)
assert.match(settingsPanel, /Model presets/)
assert.match(settingsPanel, /Max parallel workers/)

assert.equal(plugin.reasoningLabel('xhigh'), 'Extra high')
assert.equal(plugin.modelWithThinking({ model: 'm', provider: 'p', effort: 'max' }), 'p: m · Max')
assert.equal(plugin.modelWithThinking({ model: 'm', provider: 'p', effort: '' }), 'p: m')

console.log('town render probe: ok')
