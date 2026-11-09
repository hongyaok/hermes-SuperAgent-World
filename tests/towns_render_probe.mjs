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
    data: {
      files: [], sessions: [], presets: [], models: [],
      features: ['towns', 'thinking'], api_version: 3,
    },
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
  .replaceAll('\r\n', '\n')

if (/^import\s/m.test(rewritten) || /^import .*@hermes\/plugin-sdk/m.test(rewritten)) {
  throw new Error('probe failed to rewrite every import')
}

const dir = await mkdtemp(join(tmpdir(), 'superagent-town-probe-'))
const probePath = join(dir, 'plugin.mjs')
await writeFile(probePath, rewritten, 'utf8')
const plugin = await import(pathToFileURL(probePath).href)

// Unique data URLs per sprite: tests can prove which tile an element paints
// without fetching anything, and a missing sprite is still distinguishable.
const assetUrl = (name) => `data:image/png;base64,${Buffer.from(`png:${name}`).toString('base64')}`
const assets = Object.fromEntries(
  ['grass.png', 'grass-b.png', 'grass-c.png', 'plot-empty.png',
    'house-router.png', 'house-a.png', 'house-b.png', 'house-c.png', 'house-d.png',
    'person.png', 'tree-a.png', 'tree-b.png', 'tree-c.png', 'bush.png', 'fountain.png', 'rock.png', 'lamp.png']
    .map((name) => [name, { data_url: assetUrl(name) }]),
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
  parent_session_id: root.id, town_slot: 0, messages: [], events: [], harness: 'mini',
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
  for (const key of [
    'aria-label', 'aria-haspopup', 'aria-expanded', 'role',
    'data-town-picker', 'data-town-option', 'data-town-id',
    'data-town-house', 'data-town-actor', 'data-town-speech',
    'data-petdex-slug', 'data-petdex-state', 'data-town-road-network',
    'data-town-stage', 'data-town-landmark', 'data-town-hud', 'data-town-connections',
    'data-town-time', 'data-town-clock',
    'data-town-plot', 'data-town-empty-plot',
    'data-town-bulletin', 'data-town-bulletin-modal', 'data-town-bulletin-task',
    'data-transcript-scroll',
    'data-create-pr-box', 'data-town-dialog',
    'data-drawer', 'id',
  ]) {
    if (props[key] != null) bits.push(`${key}="${props[key]}"`)
  }
  if (props.className) bits.push(`class="${props.className}"`)
  if (props.style && props.style.backgroundColor) bits.push(`bg="${props.style.backgroundColor}"`)
  if (props.style && props.style.width) bits.push(`width:${props.style.width}`)
  if (props.style && props.style.height) bits.push(`height:${props.style.height}`)
  if (props.style && props.style.overflowY) bits.push(`overflow-y:${props.style.overflowY}`)
  if (props.style && props.style.isolation) bits.push(`isolation:${props.style.isolation}`)
  if (props.style && props.style.zIndex != null) bits.push(`z-index:${props.style.zIndex}`)
  // Left/top are serialized so a regression from stage pixels back to
  // percentages is visible to the assertions below.
  if (props.style && props.style.left) bits.push(`left:${props.style.left}`)
  if (props.style && props.style.top) bits.push(`top:${props.style.top}`)
  if (props.style && props.style.transform) bits.push(`transform:${props.style.transform}`)
  const children = props.children
  if (children != null) bits.push(Array.isArray(children) ? children.map(render).join(' ') : render(children))
  return bits.join(' ')
}

const h = (type, props) => ({ type, props: props || {} })

/** First rendered element carrying `key === value`, expanding function
 *  components the same way render() does. Lets the probe prove containment
 *  (the HUD is outside the scaled stage), not just string presence. */
function findElement(node, key, value) {
  if (node == null || typeof node !== 'object') return null
  if (Array.isArray(node)) {
    for (const child of node) {
      const hit = findElement(child, key, value)
      if (hit) return hit
    }
    return null
  }
  if (typeof node.type === 'function') return findElement(node.type(node.props || {}), key, value)
  if (!node.type) return null
  const props = node.props || {}
  if (props[key] === value) return node
  return findElement(props.children, key, value)
}

const pets = {
  'hermes-kody': {
    slug: 'hermes-kody',
    display_name: 'Hermes Kody',
    cols: 8,
    rows: 9,
    frame_width: 48,
    frame_height: 52,
    rows_map: { idle: 0, 'running-right': 1, 'running-left': 2, waving: 3, jumping: 4, failed: 5, waiting: 6, running: 7, review: 8 },
    data_url: 'data:image/webp;base64,AA==',
  },
  'coding-cat-pet': {
    slug: 'coding-cat-pet',
    display_name: 'Coding Cat',
    cols: 8,
    rows: 9,
    frame_width: 48,
    frame_height: 52,
    rows_map: { idle: 0, 'running-right': 1, 'running-left': 2, waving: 3, jumping: 4, failed: 5, waiting: 6, running: 7, review: 8 },
    data_url: 'data:image/webp;base64,AA==',
  },
}
root.avatar_slug = 'hermes-kody'
workerOne.avatar_slug = 'coding-cat-pet'
workerOne.recent_events = [{ kind: 'tool', detail: 'pytest -q' }]

const sceneAssets = {
  ...assets,
  'water.png': { data_url: assetUrl('water.png') },
  'road-h.png': { data_url: assetUrl('road-h.png') },
  'road-v.png': { data_url: assetUrl('road-v.png') },
  'plaza-stone.png': { data_url: assetUrl('plaza-stone.png') },
}
const sceneNode = h(plugin.TownScene, {
  town: root,
  assets: sceneAssets,
  pets,
  buildingIds: { 'S-0002': true },
  onOpen: () => undefined,
  registerHouse: () => () => undefined,
  reducedMotion: false,
})
const scene = render(sceneNode)
assert.match(scene, /Router house S-0001/)
assert.match(scene, /Worker house S-0002/)
assert.match(scene, /Worker house S-0003/)
assert.match(scene, /running/)
assert.match(scene, /review/)
assert.match(scene, /superagent-town-build/)
assert.match(scene, /data-town-house="S-0002"/)
assert.match(scene, /data-town-road-network="cobblestone"/)
assert.match(scene, /data-town-landmark="plaza"/)
assert.match(scene, /data-town-landmark="stream"/)
assert.match(scene, /data-town-actor="S-0001"/)
assert.match(scene, /data-town-actor="S-0002"/)
assert.match(scene, /data-petdex-slug="hermes-kody"/)
assert.match(scene, /data-petdex-slug="coding-cat-pet"/)
assert.match(scene, /data-town-speech="1"/)
// Bubbles are fixed-size and scrollable: long telemetry must not stretch the town.
assert.match(scene, /overflow-y-auto/)
assert.match(scene, /superagent-pixel-scroll/)
// Pixel chrome: town canvas carries the pixel font; dialogs/panels use stepped borders.
assert.match(scene, /superagent-pixel/)
assert.match(scene, /data-town-time="/)
assert.match(scene, /pytest -q/)
assert.match(scene, /data-town-hud="1"/)
assert.match(scene, /data-town-connections="1"/)
assert.match(scene, /Filter town workers/)
assert.match(scene, /Pause character movement/)
// Stacking context isolation so town houses/sprites never escape over Settings/Drawers/Dialogs
assert.match(scene, /isolation:isolate z-index:0/)
for (const match of scene.matchAll(/z-index:(\d+)/g)) {
  assert.ok(Number(match[1]) <= 25, `TownScene internal z-index ${match[1]} must stay <= 25`)
}

// -- responsive stage ------------------------------------------------------
// One 1200x700 logical canvas, scaled to fit but clamped at both ends: the
// floor keeps labels readable (the viewport scrolls instead) and the ceiling
// stops a wide monitor from spreading the estate out again.
assert.equal(plugin.townStageScale(992, 460), 0.8)
assert.equal(plugin.townStageScale(2000, 1000), 1.25)
assert.equal(plugin.townStageScale(1200, 700), 1)
assert.ok(Math.abs(plugin.townStageScale(1586, 871) - 871 / 700) < 1e-9)
assert.equal(plugin.townStageScale(0, 0), 0.8)
assert.equal(plugin.townStageScale(null, undefined), 0.8)
assert.equal(plugin.townStageScale('nonsense', {}), 0.8)
assert.ok(Number.isFinite(plugin.townStageScale(Number.NaN, Number.POSITIVE_INFINITY)))

// The stage carries the logical size; the HUD must stay outside it, or the
// scale transform would enlarge the filter row and scrolling would drag it
// off screen with the terrain.
const stageNode = findElement(sceneNode, 'data-town-stage', '1200x700')
assert.ok(stageNode, 'the scene renders a 1200x700 logical stage')
assert.equal((scene.match(/data-town-stage=/g) || []).length, 1)
const stageTree = render(stageNode)
assert.match(stageTree, /transform:scale\(/)
assert.doesNotMatch(stageTree, /data-town-hud="1"/, 'the HUD must not be inside the scaled stage')
assert.doesNotMatch(stageTree, /role="status"/, 'the status line must not be inside the scaled stage')
assert.equal((scene.match(/data-town-hud="1"/g) || []).length, 1)
assert.match(scene, /data-town-hud="1"/)
assert.match(scene, /overflow-auto/)
// Entities inside the stage: houses/actors on their plots and the ground
// layers converted to the same stage-pixel system.
assert.match(stageTree, /data-town-house="S-0002"/)
assert.match(stageTree, /data-town-actor="S-0002"/)
assert.match(stageTree, /data-town-landmark="stream"/)
assert.match(stageTree, /left:70px top:510px/, 'the pond uses its authored stage rect')
assert.match(stageTree, /left:600px top:441px/, 'the plaza is anchored in stage pixels')
assert.match(stageTree, /left:240px top:357px/, 'the west lane is anchored in stage pixels')
assert.doesNotMatch(stageTree, /left:24%/, 'the old percentage pond position is gone')

const quiet = render(h(plugin.TownScene, {
  town: root,
  assets,
  buildingIds: { 'S-0002': true },
  onOpen: () => undefined,
  registerHouse: () => () => undefined,
  reducedMotion: true,
}))
assert.doesNotMatch(quiet, /superagent-town-build/)

// -- fixed estate geometry -------------------------------------------------
// Every slot is an authored parcel on a 1200x700 logical stage. These
// assertions are the contract: a slot's rectangle must never depend on the
// session id, a poll, or the window size.
assert.deepEqual(plugin.TOWN_STAGE, { width: 1200, height: 700 })
assert.equal(plugin.TOWN_STAGE_MIN_SCALE, 0.8)
assert.equal(plugin.TOWN_STAGE_MAX_SCALE, 1.25)
assert.deepEqual(plugin.TOWN_ROUTER, { left: 600, top: 325, scale: 0.9 })
assert.equal(plugin.TOWN_PLOTS.length, 12)
assert.equal(new Set(plugin.TOWN_PLOTS.map((plot) => plot.id)).size, 12, 'plot ids are unique')
assert.equal(new Set(plugin.TOWN_PLOTS.map((plot) => `${plot.left}:${plot.top}`)).size, 12, 'plot anchors are unique')
for (const plot of plugin.TOWN_PLOTS) {
  assert.ok(plot.id && plot.region, `plot ${plot.id} has an id and a region`)
  const box = plugin.townPlotRect(plot)
  assert.deepEqual(box, { x: plot.left - 56, y: plot.top - 65, w: 112, h: 130 })
  assert.ok(box.x >= 0 && box.x + box.w <= plugin.TOWN_STAGE.width, `${plot.id} fits the stage horizontally`)
  assert.ok(box.y >= 0 && box.y + box.h <= plugin.TOWN_STAGE.height, `${plot.id} fits the stage vertically`)
}
// Same slot, different sessions, identical position. This is the whole point:
// the old implementation hashed the session id into a sub-percent offset, so
// every reload moved the house.
assert.deepEqual(plugin.townHousePosition(4, 'S-0002'), plugin.townHousePosition(4, 'S-9999'))
assert.deepEqual(plugin.townHousePosition(4), plugin.TOWN_PLOTS[4])
assert.deepEqual(plugin.townHousePosition(3), plugin.TOWN_PLOTS[3])
assert.deepEqual(plugin.townHousePosition(11), plugin.TOWN_PLOTS[11])
assert.deepEqual(plugin.townHousePosition(99), plugin.TOWN_PLOTS[11], 'out-of-range slots clamp to the last plot')
assert.deepEqual(plugin.townHousePosition(-2), plugin.TOWN_PLOTS[0])
assert.deepEqual(plugin.townHousePosition('nonsense'), plugin.TOWN_PLOTS[0])
// The lookup returns a copy, so a caller cannot edit the authored table.
const plotCopy = plugin.townHousePosition(0)
plotCopy.left = 9999
assert.equal(plugin.TOWN_PLOTS[0].left, 330)

// Strict positive-area overlap: a shared edge is legal (a drive meets a
// lane), a real overlap is not.
assert.equal(plugin.rectsOverlap({ x: 0, y: 0, w: 10, h: 10 }, { x: 10, y: 0, w: 10, h: 10 }), false)
assert.equal(plugin.rectsOverlap({ x: 0, y: 0, w: 10, h: 10 }, { x: 9, y: 9, w: 10, h: 10 }), true)

// No parcel may sit on the Router, the pond, or a civic prop, and no two
// reserved boxes may overlap either.
const reserved = plugin.TOWN_RESERVED_RECTS
assert.ok(Array.isArray(reserved) && reserved.length >= 10, 'reserved boxes are exported')
for (let i = 0; i < reserved.length; i += 1) {
  for (let j = i + 1; j < reserved.length; j += 1) {
    assert.equal(plugin.rectsOverlap(reserved[i], reserved[j]), false,
      `reserved boxes overlap: ${JSON.stringify(reserved[i])} / ${JSON.stringify(reserved[j])}`)
  }
}
for (const plot of plugin.TOWN_PLOTS) {
  for (const rect of reserved) {
    assert.equal(plugin.rectsOverlap(plugin.townPlotRect(plot), rect), false,
      `${plot.id} overlaps ${JSON.stringify(rect)}`)
  }
}
// The screenshot bug: slot 4 used to stand in the pond.
assert.equal(
  plugin.rectsOverlap(plugin.townPlotRect(plugin.townHousePosition(4)), plugin.TOWN_POND_RECT),
  false,
)

// Houses and actors render at stage pixels, not percentages.
const plotHouse = render(h(plugin.TownHouse, {
  session: workerOne, isRouter: false, slot: 0, onOpen: () => undefined,
  houseRef: () => undefined, building: false, reducedMotion: false, assets, pet: null, dimmed: false,
}))
assert.match(plotHouse, /left:330px/)
assert.match(plotHouse, /top:125px/)
assert.doesNotMatch(plotHouse, /left:26%/)
const routerHouse = render(h(plugin.TownHouse, {
  session: root, isRouter: true, slot: 0, onOpen: () => undefined,
  houseRef: () => undefined, building: false, reducedMotion: false, assets, pet: null, dimmed: false,
}))
assert.match(routerHouse, /left:600px/)
assert.match(routerHouse, /top:325px/)
const homeActor = render(h(plugin.TownActor, {
  session: workerOne, isRouter: false, actorState: null, pet: null, personArt: null,
  reducedMotion: false, dimmed: false, onOpen: () => undefined,
}))
assert.match(homeActor, /left:330px/)
assert.match(homeActor, /top:190px/, 'a north actor waits on its doorstep, not on a percentage offset')

// -- twelve authored parcels, occupied or deliberately vacant --------------
// The fixture has workers in slots 0 and 1, so exactly ten lots are empty.
// Slot 0 is an ordinary parcel: the Router is a civic building, not a plot
// occupant, so it neither takes nor blocks a lot.
assert.equal((scene.match(/data-town-plot="/g) || []).length, 12, 'every parcel renders one marker')
assert.equal((scene.match(/data-town-empty-plot="/g) || []).length, 10, 'ten parcels are vacant')
assert.match(scene, /data-town-empty-plot="north-3"/)
assert.doesNotMatch(scene, /data-town-empty-plot="north-1"/)
assert.doesNotMatch(scene, /data-town-empty-plot="north-2"/)
assert.match(scene, /data-town-plot="north-3" data-town-empty-plot="north-3"/, 'a vacant parcel carries both markers')
assert.match(scene, /data-town-house="S-0002" data-town-plot="north-1"/)
assert.match(scene, /data-town-house="S-0003" data-town-plot="north-2"/)
// Occupancy is read from each worker's own slot, deduped and clamped.
assert.deepEqual([...plugin.townOccupiedSlots(root.workers)].sort((a, b) => a - b), [0, 1])
assert.deepEqual(plugin.townVacantPlots(root.workers).map((plot) => plot.id),
  ['north-3', 'north-4', 'west-1', 'west-2', 'east-1', 'east-2', 'south-1', 'south-2', 'south-3', 'south-4'])
assert.equal(plugin.townVacantPlots(null).length, 12, 'an empty town still draws all twelve parcels')
assert.deepEqual([...plugin.townOccupiedSlots([{ id: 'S-0009', town_slot: 99 }])], [11],
  'a legacy slot clamps onto a real parcel instead of inventing a thirteenth')
assert.deepEqual([...plugin.townOccupiedSlots([{ id: 'S-0009', town_slot: 4 }, { id: 'S-0010', town_slot: 4 }])], [4],
  'a duplicated slot reserves one parcel')
assert.deepEqual([...plugin.townOccupiedSlots([{ town_slot: 2 }])], [],
  'a worker without an id reserves nothing, because it renders no house')

// A town with only its Router: twelve vacant lots, zero worker houses.
const routerOnly = render(h(plugin.TownScene, {
  town: { ...root, workers: [] },
  assets: sceneAssets,
  pets,
  buildingIds: {},
  onOpen: () => undefined,
  registerHouse: () => () => undefined,
  reducedMotion: false,
}))
assert.equal((routerOnly.match(/data-town-empty-plot="/g) || []).length, 12)
assert.match(routerOnly, /Router house S-0001/)
assert.doesNotMatch(routerOnly, /data-town-house="S-0002"/)
assert.doesNotMatch(routerOnly, /Worker house/)

const switcher = render(h(plugin.TownSwitcher, {
  towns: [root],
  value: root.id,
  onChange: () => undefined,
  onPrev: () => undefined,
  onNext: () => undefined,
  onNew: () => undefined,
  onReroll: () => undefined,
  rerolling: false,
  failedCount: 2,
  onRetryFailed: () => undefined,
  retryingFailed: false,
  onSessions: () => undefined,
  canPrev: false,
  canNext: false,
}))
assert.match(switcher, /Previous town/)
assert.match(switcher, /Next town/)
assert.match(switcher, /New town/)
assert.match(switcher, /Reroll sprites/)
assert.match(switcher, /Retry failed \(2\)/)
assert.match(switcher, /2\/12 plots/)
assert.match(switcher, /Sessions/)

// The native <select> painted a white OS popup that hid the option text, so
// the picker is a button + themed panel now. The trigger carries the ARIA menu
// wiring; the rows live in TownPickerList (rendered here directly, because a
// closed popover has nothing to walk).
assert.match(switcher, /data-town-picker="1"/)
assert.match(switcher, /aria-label="Select town"/)
assert.match(switcher, /aria-haspopup="menu"/)
assert.match(switcher, /aria-expanded="false"/)
assert.match(switcher, /Build the town · review/)
// Closed popover: no rows until it opens, and definitely no worker rows.
assert.doesNotMatch(switcher, /data-town-option/)

// Only roots are towns: townChoices drops a worker row and never reads
// town.workers, so a leaked session list cannot become nine picker entries.
assert.deepEqual(plugin.townChoices([root, workerOne]).map((town) => town.id), ['S-0001'])
assert.deepEqual(plugin.townChoices(null), [])
const taintedTown = { ...root, id: 'S-0009', workers: [workerOne, workerTwo] }
assert.deepEqual(
  plugin.townChoices([taintedTown, workerTwo]).map((town) => town.id),
  ['S-0009'],
)

const picker = render(h(plugin.TownPickerList, {
  towns: [root, workerOne],
  value: root.id,
  onSelect: () => undefined,
}))
assert.match(picker, /Towns \(1\)/)
assert.match(picker, /data-town-option="1"/)
assert.match(picker, /data-town-id="S-0001"/)
assert.match(picker, /2 workers/)
assert.doesNotMatch(picker, /data-town-id="S-0002"/)
assert.doesNotMatch(picker, /Fix the parser/)

const emptyPicker = render(h(plugin.TownPickerList, {
  towns: [],
  value: '',
  onSelect: () => undefined,
}))
assert.match(emptyPicker, /Towns \(0\)/)
assert.match(emptyPicker, /No towns/)

// Fallback panel chrome: themed surface, explicit border and a height cap so
// the list can never paint light text on an unstyled white box.
const popover = render(h(plugin.TownPickerPopover, {
  towns: [root, workerOne],
  value: root.id,
  onSelect: () => undefined,
}))
assert.match(popover, /bg-background/)
assert.match(popover, /border-\(--ui-stroke-secondary\)/)
assert.match(popover, /max-h-72/)
assert.match(popover, /min-h-7/)
assert.match(popover, /data-town-id="S-0001"/)
assert.doesNotMatch(popover, /data-town-id="S-0002"/)

// A worker id as the selected value must not become a town label.
const workerSelected = render(h(plugin.TownSwitcher, {
  towns: [root, workerOne],
  value: workerOne.id,
  onChange: () => undefined,
  onPrev: () => undefined,
  onNext: () => undefined,
  onNew: () => undefined,
  onSessions: () => undefined,
  canPrev: false,
  canNext: false,
}))
assert.doesNotMatch(workerSelected, /Fix the parser/)
assert.match(workerSelected, /No towns/)

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

const workerDialog = render(h(plugin.TownChatDialog, {
  ...dialogBase,
  sessionId: workerOne.id,
  detail: { data: { session: workerOne }, isLoading: false, isError: false, error: null, refetch: () => undefined },
  canJumpRouter: true,
}))
assert.match(workerDialog, /Worker house/)
assert.match(workerDialog, /↗ Router/)
// The harness badge is a worker-only signal: this fixture is a mini worker.
assert.match(workerDialog, /aria-label="Worker harness: mini"/)
const orphanDialog = render(h(plugin.TownChatDialog, {
  ...dialogBase,
  sessionId: workerOne.id,
  detail: { data: { session: workerOne }, isLoading: false, isError: false, error: null, refetch: () => undefined },
  canJumpRouter: false,
}))
assert.doesNotMatch(orphanDialog, /↗ Router/)

const empty = render(h(plugin.TownEmpty, { onNew: () => undefined, banner: null }))
assert.match(empty, /No towns yet/)

const page = render(h(plugin.SuperagentPage, {}))
assert.match(page, /Superagent Towns/)
assert.match(page, /No towns yet/)

const drawer = render(h(plugin.OverlayDrawer, {
  panel: 'settings', title: 'Settings', subtitle: 'defaults', onClose: () => undefined, children: 'body',
}))
assert.match(drawer, /data-drawer="settings"/)
assert.match(drawer, /bg="var\(--card/)
assert.match(drawer, /md:w-\[420px\]/)
assert.match(drawer, /z-index:40/)
assert.match(dialog, /z-index:40/)
assert.match(dialog, /w-\[54vw\]/)
assert.match(dialog, /h-\[78vh\]/)
// The chat column itself is a fixed viewport proportion on every host.
assert.match(dialog, /w-\[52vw\]/)
// The transcript caps at a viewport proportion so unbounded hosts scroll.
assert.match(dialog, /max-h-\[56vh\]/)

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
assert.match(settingsPanel, /Worker harness/)
assert.match(settingsPanel, /mini-swe-agent style \(bash-only\)/)
assert.match(settingsPanel, /Max parallel workers/)
assert.match(settingsPanel, /Isolate workers in git worktrees/)
assert.match(settingsPanel, /Allow workers to create GitHub PRs/)
assert.match(settingsPanel, /Allow Router & workers to review\/approve PRs/)
assert.match(settingsPanel, /Use Petdex\.dev random character sprites/)
assert.match(settingsPanel, /Clean up stale worktrees/)

const commentBox = render(h(plugin.CommentBox, {
  session: { ...workerTwo, pr_number: 42 },
  onClose: () => undefined,
}))
assert.match(commentBox, /Comment on PR #42/)
assert.match(commentBox, /Approve ✓/)
assert.match(commentBox, /Request changes ✕/)

const createPrBox = render(h(plugin.CreatePrBox, {
  session: { ...workerOne, repo: '/tmp/repo', branch_name: 'superagent/s-0002-fix' },
  onClose: () => undefined,
}))
assert.match(createPrBox, /data-create-pr-box="S-0002"/)
assert.match(createPrBox, /Create Pull Request/)

assert.equal(plugin.reasoningLabel('xhigh'), 'Extra high')
const fallbackPicker = render(h(plugin.PresetSelect, {
  value: { model: 'm', provider: 'p', effort: 'xhigh' },
  models: { models: ['m', 'n'] },
  onChange: () => undefined,
}))
assert.match(fallbackPicker, /Thinking level/)
assert.match(fallbackPicker, /Extra high/)
assert.match(fallbackPicker, /Default model/)
assert.equal(plugin.modelWithThinking({ model: 'm', provider: 'p', effort: 'max' }), 'p: m · Max')
assert.equal(plugin.modelWithThinking({ model: 'm', provider: 'p', effort: '' }), 'p: m')
assert.equal(plugin.backendNeedsRestart(null), false)
assert.equal(plugin.backendNeedsRestart({ features: ['towns', 'thinking'] }), false)
assert.equal(plugin.backendNeedsRestart({ features: ['towns'] }), true)
assert.equal(plugin.backendNeedsRestart({ features: [] }), true)
assert.doesNotMatch(page, /data-backend-stale/)

// Bulletin board in TownScene and modal showing in-progress tasks
assert.match(scene, /data-town-bulletin="1"/)
assert.match(scene, /Bulletin Board/)
assert.match(scene, /1 active/)
const bulletinModal = render(h(plugin.TownBulletinModal, {
  town: root,
  pets,
  assets,
  onClose: () => undefined,
  onOpenSession: () => undefined,
}))
assert.match(bulletinModal, /data-town-bulletin-modal="1"/)
assert.match(bulletinModal, /Town Bulletin Board/)
assert.match(bulletinModal, /In Progress Tasks \(1\)/)
assert.match(bulletinModal, /data-town-bulletin-task="S-0002"/)
assert.match(bulletinModal, /Fix the parser/)

// Speech bubble visibility & high-contrast dark background
assert.equal(render(h(plugin.TownSpeechBubble, { text: 'Hidden text', status: 'idle', visible: false })), '')
const visibleBubble = render(h(plugin.TownSpeechBubble, { text: 'Visible text', status: 'running', visible: true }))
assert.match(visibleBubble, /data-town-speech="1"/)
assert.match(visibleBubble, /bg="#161326"/)
assert.match(visibleBubble, /Visible text/)

// Uniform chat window dimensions and visible scrollbar for both Router and Worker
assert.match(dialog, /width:54vw/)
assert.match(dialog, /height:78vh/)
assert.match(dialog, /data-transcript-scroll="1"/)
assert.match(dialog, /overflow-y:scroll/)
assert.match(workerDialog, /width:54vw/)
assert.match(workerDialog, /height:78vh/)
assert.match(workerDialog, /data-transcript-scroll="1"/)
assert.match(workerDialog, /overflow-y:scroll/)

console.log('town render probe: ok')
