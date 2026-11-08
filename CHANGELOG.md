# Changelog

All notable changes to the `superagent` Hermes plugin. Plain bullets, newest
first.

## 0.6.0 — 2026-11-08

Superagent Living Towns & Router↔Worker PR Workflows: randomised Petdex
character sprites, a cobblestone pixel-art town with 7 building archetypes and
water features, autonomous waypoint movement with live telemetry speech bubbles,
isolated per-worker git worktrees, and opt-in PR creation/review tools.

### Added

- **Randomised Petdex character sprites (`dashboard/petdex_assets.py`).** Each
  Router and worker session is assigned a durable `avatar_slug` at creation time.
  The backend proxies and downsamples `petdex.dev` 8×9 spritesheets 4× to
  `384×468` WebP (`48×52` per cell) with disk caching (`GET /town/pets`), ships 8
  curated bundled sheets under `assets/town/pets/` for offline/hermetic use, and
  exposes `POST /town/pets/reroll` plus a **🎲 Reroll sprites** button in the
  town switcher.
- **Living pixel-art town (`assets/town/`, `dashboard/town_assets.py`).**
  Expanded the vendored town asset allowlist from 14 to 26 PNGs: all 7 worker
  building archetypes (`house-a` through `house-g`: cottage, tavern, workshop,
  forge, library, post office, observatory), tileable cobblestone roads
  (`road-h`, `road-v`), a paved town square (`plaza-stone`), an artisan stream
  (`water`) with a wooden footbridge (`bridge`), a wishing well (`well`),
  noticeboard (`signpost`), benches (`bench`), wildflowers (`flowers`), and warm
  streetlamp glows while workers are running.
- **Autonomous waypoint movement & speech bubbles (`desktop/plugin.js`).**
  Characters are decoupled from house buttons into a depth-sorted `TownActor`
  layer driven by `useTownSimulation` (`requestAnimationFrame` throttled to
  25 fps, skipped under `prefers-reduced-motion`). Inhabitants walk between
  their home doorstep, the central plaza, fountain, noticeboard and Router Hall
  based on session status (`queued`, `running`, `review`, `failed`, `idle`), play
  all 9 Petdex animation rows (`idle`, `running-right`, `running-left`, `waving`,
  `jumping`, `failed`, `waiting`, `running`, `review`), pause to wave when
  passing nearby sprites, and display clickable `TownSpeechBubble` pills showing
  live tool/activity telemetry (`deriveSpeechBubble`) or status banter.
- **Router↔Worker coordination & opt-in PR workflows (`sa_tools.py`,
  `sa_github.py`, `dashboard/plugin_api.py`).** Added four agent tools:
  `superagent_town_status` (inspect all workers in a town with diffs and PR
  links), `superagent_message_worker` (send follow-up instructions, PR creation,
  or PR review/approval tasks from the Router to an existing worker),
  `superagent_pr_create` (commit worktree changes, push a `superagent/<id>`
  branch, and open/update a GitHub PR, gated by `allow_worker_pr_create`), and
  `superagent_pr_review` (submit `APPROVE`, `REQUEST_CHANGES`, or `COMMENT`
  reviews with automatic self-approval 422 fallback, gated by
  `allow_router_pr_approve`). Added `POST /chats/{id}/pr`,
  `POST /chats/{id}/review`, and `POST /towns/{root_id}/dispatch` endpoints plus
  **Create PR**, review verdict radios, and Router dispatch actions in the
  house dialog.
- **Isolated per-worker git worktrees (`sa_github.prepare_worker_worktree`).**
  When `allow_worker_git_worktree` is enabled (default `true`), `superagent_spawn`
  provisions a dedicated branch and worktree under
  `<data_dir>/workspaces/worker-<session_id>` so parallel workers never collide
  in the shared working tree.

### Changed

- **Randomised plot allocation (`sa_chats.py`).** New workers pick uniformly at
  random from the available free slots (`0…11`) via `secrets.choice` (persisted
  on `chat_sessions.town_slot` so houses never move after creation), with
  `randomize_slot=False` available for deterministic lowest-free-slot callers.
- **Hash mixing (`desktop/plugin.js`).** `townHash` now uses 32-bit Murmur3
  avalanche finalisation so sequential session IDs (`S-0001`, `S-0002`, `S-0003`)
  produce uncorrelated house archetypes and doorstep offsets.
- **Town read model (`sa_chats.list_towns`).** Root and worker rows in
  `GET /towns` now include `message_count`, `recent_events`, `avatar_slug`,
  `branch_name`, and `worktree_path`.

### Fixed

- **`GitHubSource` fail-closed allow-list (`sa_sources.py`).** `GitHubSource._to_inbound`
  previously checked `if self.allowed_users and login not in self.allowed_users`,
  which failed open when `github_allowed_users` was empty; it now rejects all
  GitHub comments when the allow-list is empty, matching `GitHubWatcher` and
  `DESIGN.md`.
- **`ArrowLeft` / `ArrowRight` town switching (`desktop/plugin.js`).** Fixed a
  stale mount closure in the window `keydown` listener by reading `{ towns,
  townId, selectTown }` through `townNavRef.current`.
- **House ref churn (`desktop/plugin.js`).** Cached `registerHouse(id)` callbacks
  per session ID so React does not detach and re-attach every house button ref on
  each 2 s poll.
- **`reasoning_effort` propagation (`dashboard/plugin_api.py`, `sa_watcher.py`).**
  Preserved `reasoning_effort` when creating a replacement session in
  `POST /chats/{id}/messages` and applied `default_reasoning_effort` in
  `POST /github/prs/{n}/work` and `GitHubWatcher`.
- **Windows CRLF handling in render probe (`tests/towns_render_probe.mjs`).**
  Replaced `.replace('\r\n', '\n')` with `.replaceAll('\r\n', '\n')`.

## 0.5.10 — 2026-11-08

### Added

- Regression tests that a Router gets `superagent` in its toolset and “Town
  mode” in its prompt while a worker gets neither, and that a new chat with no
  explicit level stores the saved default.
- The shortcut sheet now lists the town keys (←/→, n, /).

### Changed

- The house name is a pointer affordance only (`tabIndex=-1`); keyboard users
  keep one tab stop per house on the house button itself.
- The operator skill version follows the plugin.

### Fixed

- The shortcut sheet advertised `j/k` chat navigation that the town page does
  not implement; it now describes the town controls that actually work.

## 0.5.9 — 2026-11-08

### Added

- The fallback model picker (shells without the SDK catalog menu) now includes a
  **Thinking level** select, so `xhigh`, `max` and `ultra` are reachable even
  there.

### Changed

- The existing-chat “Change model” control names the thinking level in its
  tooltip and accessible label; it edits both through the same picker.
- Saving Settings resets the remembered new-chat picker to the saved defaults,
  so the next **New town** actually uses the model and thinking level just set.

### Fixed

- A shell without `ModelCatalogMenu` could choose a model but had no way to
  choose a thinking level from the composer; the fallback now exposes both.
- New chats kept using the last-used model/level after Settings was saved;
  saving a default now takes effect on the next new town.

## 0.5.8 — 2026-11-08

### Added

- The town switcher reports `N/12 plots` used, with a tooltip spelling out the
  cap, so the fixed grid is visible before a spawn fails.

### Changed

- Changing a model or applying a preset keeps the independently chosen thinking
  level. Presets are model/provider pairs; effort is not silently reset. This
  covers both the catalog menu and the fallback `<select>`.
- The README now documents `/health` and the actual `town_count` key (it said
  `towns_count`) and states that the grid has twelve plots.

### Fixed

- The fallback model picker dropped `effort` when a model was selected; it now
  preserves the current level.
- The CLI's `models`/`settings` thinking output has a regression test.

## 0.5.7 — 2026-11-08

### Added

- `hermes superagent models` and `settings` print the resolved default thinking
  level (`model default` when unset).

### Changed

- `settings` places the thinking line directly under model/provider, where the
  other chat defaults are read.

### Fixed

- The CLI no longer hides a configured `default_reasoning_effort` from the two
  commands people use to inspect it.

## 0.5.6 — 2026-11-08

### Added

- Regression tests that fill all twelve plots, and render-probe checks for the
  twelfth plot and the worker-only Router link.

### Changed

- Opaque surfaces use a CSS fallback chain (`--card` → `--background` →
  `#1c1c1e`), so Settings, the house dialog and the new-town modal cannot fall
  back to transparent if a theme omits `--card`.
- A dialog labels its session as Router from `parent_session_id`, not from the
  currently displayed town; drawer subtitles use the tertiary text colour.

### Fixed

- An orphaned worker opened from Sessions no longer offers a Router jump to an
  unrelated town.
- A newly created root whose town list has not refetched yet is labelled
  "Router house" instead of "Worker house".

## 0.5.5 — 2026-11-08

### Added

- `hermes superagent chat --reasoning <level>` selects `none…ultra` for one CLI
  session; an explicit flag wins over the configured default. The session row
  and the runner both carry the resolved level, and `show`/`chat` print it.

### Changed

- The CLI `models` payload and the README configuration tables now include
  `default_reasoning_effort`, matching `GET /models` and the Settings panel.

### Fixed

- A CLI chat created with a configured default thinking level left the session
  row blank and relied on the runner fallback; it now stores the effective
  level so `show` and a later resume see the same value.

## 0.5.4 — 2026-11-08

### Added

- A worker's dialog has a **↗ Router** action that switches the same dialog to
  the town's root session.

### Changed

- The town header's count/summary, worker count and switcher tooltip use the
  tertiary text colour instead of the faint quaternary one, matching the
  Settings readability pass.
- The stale-gateway banner names the exact command (`hermes gateway restart`)
  and a `/towns` 404 gets the same explanation instead of the generic
  "enable the plugin" card.

### Fixed

- A missing towns route no longer reads as a disabled plugin when `/chats`
  still answers; the error card distinguishes the two.

## 0.5.3 — 2026-11-08

### Added

- Regression coverage for slot reuse after a delete, archived orphans, and a
  legacy slot outside the desktop grid.

### Changed

- A new worker takes the lowest free plot (`0…11`) instead of `MAX(town_slot)+1`.
  Deleting a worker no longer leaves a permanent gap that can push the next
  worker past the twelve-house grid.

### Fixed

- `/towns` clamps a legacy/out-of-range `town_slot` to the grid instead of
  handing the desktop an index it cannot draw.
- Archived orphans no longer inflate the header's orphan count on the default
  `archived=hide` board; `archived=all`/`only` still reports them.

## 0.5.2 — 2026-11-08

### Added

- **A stale gateway explains itself.** `/health` now advertises the `thinking`
  feature alongside `towns`, and the page checks both on mount. Instead of a
  generic error, it says the gateway loaded an older API and needs a restart
  before the new routes exist.
- **All 12 worker slots fit.** The scene uses a fixed four-column × three-row
  plot grid at the backend's `MAX_TOWN_WORKERS = 12`; a 992×460 DOM probe
  reports no house overlaps and no clipping, including the Router in the centre.

### Changed

- **Labels no longer shrink with the house.** House sprites are sized directly
  instead of scaling the whole wrapper, so status badges and names stay at
  their readable 10 px size. The person sprite is scaled to match.
- **Empty towns look like towns.** The first-run state paints grass and shows
  the Router and its person instead of a bare dashed box.
- **Settings is easier to read.** Head hints, save/error notices and the
  provider note use the tertiary/secondary text colours; a failed save is no
  longer the faintest text in the drawer.
- **House names are clickable**, the house dialog focuses the composer rather
  than the close button, Tab is trapped even if focus escapes, and the new-town
  modal is capped to the viewport with its own scroll.
- `GET /models` exposes `default_reasoning_effort` so every client can show the
  same default.

### Fixed

- A town whose first observation had zero workers never animated its first
  worker; an empty baseline is now persisted, so the next id counts as new.
- House labels, status badges and people scaled down with the plot and became
  unreadable at 10 px; only the sprite is scaled now.
- Tab could escape the house dialog or new-town modal if focus started outside
  the panel; both now wrap in either direction.

## 0.5.1 — 2026-11-08

### Added

- **Thinking levels through the whole stack.** `chat_sessions.reasoning_effort`
  stores one of Hermes' levels (`none, minimal, low, medium, high, xhigh, max,
  ultra`). New chats and worker spawns inherit it, `POST /chats/{id}/model` can
  change it, and the runner passes `--reasoning <level>` to the headless turn.
  The model picker now accepts effort edits instead of dropping them; Settings
  exposes a "Default thinking" selector with Extra high, Max and Ultra.

### Changed

- **Settings is an opaque surface.** The drawer had `--ui-bg-primary`, which is
  translucent in the desktop chrome; it now sits on `--card` over a dimmed
  backdrop, traps Tab, and is 420px wide so preset rows fit. The header action
  is labelled "Settings" instead of a bare gear.
- **Town art from Hermes Town.** The coarse tileset crops are replaced by the
  original MIT-licensed buildings, trees, fountain, lamp and rock from
  `sxuff/hermes-town`; the scene gains a plaza and more vegetation. The
  extraction script and per-file attribution live in the repository.

### Fixed

- **The model picker dropped effort edits.** Selecting Extra high, Max or Ultra
  in the catalog changed nothing because the controller ignored `patch.effort`;
  it now writes the level to the chat row.
- **Settings showed the town through its form.** The drawer used a translucent
  chrome variable; it now has an opaque card surface and a dimmed backdrop.

## 0.5.0 — 2026-11-07

Superagent Towns: a root chat is a town, a spawned worker is a house, and the
desktop page draws the real session hierarchy instead of a session rail.

### Added

- **Town hierarchy.** `chat_sessions` gains an additive `parent_session_id`
  and a stable `town_slot`. NULL is a root town; a root id is one worker. The
  migration treats every pre-existing parentless session as a root and never
  infers a relationship from time, title or repository.
- **`GET /towns`.** A read-only grouping of roots with their workers, status
  counts, stable plot numbers and an `orphaned` report for workers whose parent
  was deleted. `/chats` keeps its flat contract.
- **`superagent_spawn`.** A root/router chat can create and launch a real
  headless worker through the shared `sa_runner.Runner`, inheriting the router's
  model, provider and repository. One level only, with the existing worker cap.
- **Town desktop page.** A grass-and-houses scene whose router house sits in
  the centre and one worker house/person appears per child session, with
  text status badges, a town switcher, a new-town composer and a chat dialog
  that reuses the existing transcript and composer.

### Changed

- **Root chats are routers.** A root session is launched with the plugin's
  toolset so it can call `superagent_spawn`; worker sessions keep the narrow
  `file,terminal` toolsets and cannot spawn more workers.
- **One Runner per process.** The dashboard and the spawn tool now share
  `sa_runtime.shared_runner`, so `max_workers` remains a single semaphore.

### Fixed

- **No phantom towns.** Archived roots hide their town, archived workers hide
  their house, and a deleted root never promotes its remaining workers into
  towns; they are reported as orphaned sessions.

## 0.4.0 — 2026-11-06

The first release that ships the whole surface together: a chat workspace you
can talk to, a live board of parallel workers, per-session models and presets,
one watched GitHub repository with isolated PR checkouts, and a review handoff
that never commits or pushes for you.

### Added

- **Chat sessions.** The desktop **Superagent** tab starts one headless
  `hermes chat` process per turn, streams its JSONL into a transcript that
  interleaves assistant text with tool calls and errors, and stores every
  message and audit event in SQLite. Follow-ups resume the same Hermes session;
  a turn can be stopped; a finished turn ends in `review` with its changes left
  in the working tree.
- **Live multi-session grid.** Every queued, running, review or failed chat is
  a card with status, model, a client-side elapsed timer, token count and a
  three-event mini-feed. The rail filters **All / Live / Review** without
  unmounting the open transcript, and the list polls every 2 s while a worker
  is live (8 s when quiet).
- **Per-session model.** A session records its own `model` and `provider`;
  `POST /chats/{id}/model` (and the header's model control) swaps them for
  future turns without disturbing a queued or running one.
- **Model presets.** Named `(model, provider)` pairs in three spellings
  (`name=provider:model`, plain model strings, `{name, model, provider}`),
  capped at 12 with unique names. Presets appear as composer chips, travel as
  `preset` on `POST /chats`, and are reported by `GET /models`,
  `hermes superagent models` and `hermes superagent chat --preset`.
- **Settings panel and onboarding.** The ⚙ panel edits the default
  model/provider, presets, `mode`, `repo_path`, `max_workers`, `worker_yolo`
  and `chat_max_turns` through `GET/POST /settings`; the three-step **Get
  started** card points a new user at a model, GitHub and the composer and
  remembers its dismissal.
- **GitHub connect and PR/comment watch.** Connect one repository with a
  token, an allow-list and an **Observe / Auto** mode. The watcher polls open
  PRs, turns allowed mentions into sessions, optionally starts one review
  session per new head commit, lists open PRs with **Work on this PR**, and
  keeps every seen comment/head in `gh_seen`. The first poll is a baseline, so
  connecting a busy repository does not replay its history; bots and the
  agent's own comments are ignored.
- **Isolated PR worktrees.** In `auto` mode the watcher fetches
  `refs/pull/<n>/head` into the local clone and checks it out as a detached
  `git worktree` under the plugin data directory, so the user's checkout and
  branch are never touched. A failed checkout falls back to `repo_path` with a
  warning event.
- **Session diff and human-confirmed PR comment.** A lazy **Diff** panel shows
  `git status`, `git diff --stat` (plus staged totals) and a 60 000-character
  capped patch. **Comment on PR** previews an editable summary, then posts one
  comment only after **Post**; nothing is committed, pushed, merged or edited.
- **CLI for the whole surface.** `hermes superagent chat|sessions|show|stop|
  models|settings|github|diff|comment` beside the existing `status|tick|ask|
  run|doctor`, all with `--json` and stable exit codes (`0` ok, `1` runtime,
  `2` usage). `chat` resolves presets, `github` drives the watcher without
  HTTP, and `diff`/`comment` work from the chat tables alone.
- **Completion toasts.** While the page is mounted, a chat that moves from
  `queued`/`running` to `review` or `failed` raises one `host.notify` toast
  (`<title> finished` / `<title> failed`). The first poll seeds the status map,
  so opening the page never replays old completions, and `cancelled` is
  silent.

### Changed

- **Headless `hermes chat` worker runner.** Chat turns are subprocesses, not
  subagents. Hermes only binds a subagent parent during an active agent turn
  (`bind_subagent_parent`), and the dashboard backend is an HTTP handler with
  no turn to bind, so `sa_runner.Runner` uses the supported pattern: spawn
  `hermes chat -Q --format stream-json` and read its stream. Ticket workers
  still use `ctx.subagent_lifecycle` from a tool or cron turn; the two paths
  share one database, one settings loader and one attention budget.
- **Observe by default for GitHub too.** `github_watch_mode` starts at
  `observe`: PR mentions become `observed` sessions until a human promotes one
  (or switches to `auto`). Ticket intake remains `observe` by default as well.
- **Secrets and size caps are enforced on every write.** GitHub/Slack/OpenAI
  tokens and `Authorization` values are replaced with `[redacted]` before a
  message, event, error, prompt or ticket field is stored; prompts are limited
  to 20 000 characters, events to 2 000, assistant text to 20 000, diffs to
  60 000 and posted comments to 20 000.
- **The status chip speaks for chats.** `▶ N` running chats and `◆ M`
  chats in `review`/`failed`; ticket attention is only the fallback when the
  chats query has no data. The side pane shows latest chats ahead of ticket
  rows.
- **Polling has a budget.** Chats 2 s live / 8 s quiet, an open chat
  1.5 s / 6 s, the diff 4 s only while its panel is open on a running turn, and
  GitHub 20 s only while the panel or a PR badge is visible.
- **The ticket path is kept, not replaced.** Deterministic zero-token triage,
  `superagent_ask/tick/status/run`, `/superagent`, the **Superagent watch**
  cron blueprint, `/tickets` and the ticket session grid all continue to work
  beside the chat surface.

### Fixed

- **Settings survive the plugin host.** The settings loader now reads the
  Hermes config through `hermes_cli.config.load_config()` when it resolves to
  the same file, with the PyYAML read as a fallback. The plugin host's
  interpreter does not ship PyYAML, so the old loader silently returned
  `DEFAULTS` there: the settings panel showed empty defaults and a worker
  could start without the configured model even though `ctx.get_config` saw
  the real values. The loader only borrows the global reader when both paths
  agree, so tests and isolated homes stay hermetic.
- **No fallback presets.** `GET /models` and the composer no longer advertise
  the hardcoded `MODEL_PRESETS` list. With nothing configured, `presets` is
  `[]` and the chip row is hidden; `models` is the preset models in order plus
  the configured `default_model`, never a model the machine may have no
  credentials for. `hermes superagent models` follows the same rule.
- **Precise runaway-turn wording.** A cancelled turn now names the phase:
  `turn timed out after 1900s while queued (limit 1800s)` or `... while
  running ...`. Status and behaviour are unchanged.
- **Failed sessions say why.** The transcript header renders the stored
  `error` whenever a session is `failed`, and a failed live-worker card with
  no recent events shows the error's first line in its mini-feed.

## 0.3.0 — 2026-11-06

- **Chat sessions (v0.2).** The desktop Superagent tab starts one headless
  `hermes chat` process per turn, streams JSONL into a transcript, and supports
  follow-ups, resume, model changes between turns and cancel.
- **Live worker grid.** Every queued/running chat as a card with status, model,
  a client-side elapsed timer, tokens and a three-event mini-feed; rail filters
  All / Live / Review without unmounting the open transcript.
- **Model presets and settings.** Named `(model, provider)` presets in three
  spellings, a ⚙ Settings panel for default model, presets, `max_workers`,
  `chat_max_turns` and `worker_yolo`, plus composer preset chips and a
  three-step first-run onboarding card.
- **GitHub connect and PR watch (v0.3).** Connect one repository with a token
  and allow-list, choose `observe` or `auto`, watch PR mentions and new head
  commits, list open PRs and start work on one. The first poll is a baseline;
  dedupe lives in `gh_seen`.
- **Isolated PR checkouts.** `auto` fetches `refs/pull/<n>/head` and checks it
  out as a detached `git worktree` under the plugin data directory, leaving the
  user's clone and branch untouched.
- **Review handoff.** A lazy diff panel (`git status`, `--stat`, capped patch)
  and a human-confirmed PR comment with an editable preview. Nothing is
  committed, pushed, merged or posted without an explicit click.
- **CLI.** `hermes superagent chat|sessions|stop|show|models|settings|diff|comment|github|doctor`,
  all with `--json` and stable exit codes, sharing the same database, settings
  loader and worker pool as the desktop backend.
- **Ticket path kept.** Deterministic zero-token triage, `superagent_ask/tick/
  status/run` tools, `/superagent`, the **Superagent watch** cron blueprint and
  the attention pane all continue to work beside the chat surface.
- **Guardrails.** Repository containment, 20k prompt/response caps, 2k event
  caps, token redaction, `max_workers` semaphore and the runaway-turn reaper.
