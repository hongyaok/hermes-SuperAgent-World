# Changelog

All notable changes to the `superagent` Hermes plugin. Plain bullets, newest
first.

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
