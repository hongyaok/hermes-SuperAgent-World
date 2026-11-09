# Superagent (`superagent`)

`superagent` is an attention-frugal router/worker coding plugin for Hermes, inspired by DeepSeek's `dsh-superagent`.

It separates **routing** from **execution**, and keeps every result behind one review gate: nothing is committed, pushed or merged for you.

## Quick start (5 minutes)

1. **Enable the plugin** and reload desktop plugins:
   ```bash
   hermes plugins enable superagent
   ```
   Then **Desktop → Settings → Plugins → Superagent** → enable, and run **Reload desktop plugins** from ⌘K if the tab is not there yet. Enabling live-reloads tool/command registration; a Python/backend change (for example `dashboard/plugin_api.py` or a route) needs `hermes gateway restart` before the desktop sees it.
2. **Open the Superagent tab** (sidebar → Superagent, or `/superagent`).
3. **Pick a model or preset.** Use the composer's model picker, click a preset chip if one is configured, or open **⚙ Settings** to save a default model and named presets. With nothing configured the chip row stays hidden — the picker still works and a new chat follows the CLI default.
4. **Chat.** Describe the work, optionally set a repo path, press Enter. The turn runs as a headless `hermes chat` worker; the rail, transcript and **Live workers** grid update while it runs, and a toast tells you when it finishes or fails.
5. **(Optional) Connect GitHub.** Header → **GitHub** → repository, token, allowed users → **Connect**. `observe` records PR mentions as sessions; `auto` launches a worker on each one. **Work on this PR** starts one by hand.
6. **Review before anything leaves your machine.** Open a finished session → **Diff** shows `git status`, `--stat` and the patch → **Comment on PR** previews an editable summary and posts only when you press **Post**. Nothing is committed, pushed or merged.

## What you get

- **A living pixel-art town, not a dashboard** — the page draws one pixel-art town per root chat: a central Router Town Hall on a paved cobblestone plaza, 7 distinct worker building archetypes (`house-a` through `house-g`: cottage, tavern, workshop, forge, library, post office, observatory) connected by stone roads, a stream with a wooden bridge, wishing well, benches, wildflowers, and glowing streetlamps.
- **Randomised Petdex characters that walk and talk** — each Router and worker gets a durable character/creature sprite from [petdex.dev](https://petdex.dev/) (proxied and 4× downsampled to `384×468` WebP with 8 bundled offline sheets). Characters wander autonomously along town roads, visit the plaza and Router Hall, wave to passing neighbours, and display clickable speech bubbles with live tool telemetry (`🔧 read_file`, `▶ Running: pytest…`) and status banter.
- **Real workers & Router↔Worker coordination** — a Router can call `superagent_spawn` to create and launch child sessions (with isolated per-worker `git worktree` checkouts), inspect progress via `superagent_town_status`, and send follow-up instructions or PR tasks to existing workers via `superagent_message_worker`.
- **Opt-in PR creation & PR review/approval** — by default nothing is committed or pushed. When you explicitly enable `allow_worker_pr_create` or `allow_router_pr_approve` in **⚙ Settings**, workers and the Router can commit worktree changes, push a `superagent/<session_id>` branch, open a Pull Request (`superagent_pr_create` / **Create PR**), and submit `APPROVE`, `REQUEST_CHANGES`, or `COMMENT` reviews (`superagent_pr_review` / **Comment on PR**).
- **Model presets, thinking levels and defaults** — named `(model, provider)` pairs saved from the Settings panel, CLI or `config.yaml`, offered as composer chips; the picker and Settings expose `none … high, xhigh, max, ultra`, and the chosen level reaches the headless turn as `--reasoning`.
- **Completion toasts** — one desktop notification per turn that ends in `review` or `failed` (no replay on page load, silent for cancelled turns).
- **A usage report** — the header's **Usage** drawer shows chats, turns, tokens, review/failed counts, a 14-day token bar chart and the per-model split, all from one read-only `GET /usage` (`hermes superagent usage`).
- **GitHub connect & PR watch** — one repository, an allow-list, `observe` or `auto` mode, open-PR list with **Work on this PR**, and one isolated `git worktree` per pull request.
- **Review handoff** — a lazy diff panel, a human-confirmed PR comment/review, an optional **Create PR** sheet, plus `hermes superagent diff|comment`.
- **Tickets for everything else** — deterministic zero-token triage, `superagent_ask/tick/status/run` tools, `/superagent`, and the **Superagent watch** cron blueprint.
- **A CLI for the same state** — `hermes superagent status|chat|sessions|towns|search|retry|export|usage|pin|unpin|show|stop|models|settings|github|diff|comment|doctor`, all with `--json` and stable exit codes.

## How it works

- **Sources** (`LocalSource` for chat/CLI requests and `GitHubSource` for GitHub issue comment mentions) produce inbound requests.
- A **deterministic, zero-token router** (`sa_assessor.py`) classifies each request (`accept`, `ignore`, `needs_human`).
- **SQLite** (`<HERMES_HOME>/plugin-data/superagent/superagent.db`) persists tickets (`T-0001`, `T-0002`, ...), event history, source deduplication, and polling cursors, plus the chat tables (sessions, messages, events) and GitHub dedupe.
- **Ticket worker subagents** launched via `ctx.subagent_lifecycle` perform the coding work inside your repository and leave uncommitted changes in the working tree for human review (`review` state).
- **Headless chat workers** — the desktop Superagent tab starts one `hermes chat` process per turn, streams its JSON output into the session transcript, and lets you resume, watch or stop it. A root/Router session gets the plugin toolset, so it can call `superagent_spawn` to create real child sessions; workers cannot spawn further workers. Spawned workers default to the bash-only `mini` harness (mini-swe-agent style: `-t terminal`, one command per step, explicit completion marker); `worker_harness: hermes` restores the full file + terminal toolset. Chat workers follow the same rule as ticket workers: changes stay uncommitted for review.
- **GitHub PR watch** — connect a repository, and pull-request comments that mention the agent become sessions. `auto` mode checks each PR out as an isolated `git worktree` and launches a worker on it; `observe` (the default) only records the request.
- **Session handoff** — a finished session can show exactly what the worker changed (`git status`/`--stat`/patch, capped) and post one human-reviewed summary comment back to its pull request. Nothing is committed, pushed or merged.

Router-side triage (the assessor) runs no LLM, and polling is plain REST plus SQLite. Ticket workers are subagents launched only inside an agent turn; chat turns — including ones the GitHub watcher starts in `auto` — are headless `hermes chat` subprocesses bounded by the caps and watchdog described in [Chat and models API](#8-chat-and-models-api). By default, the plugin runs in `observe` mode so it only records tickets until you explicitly approve them or switch to `auto`.

### Architecture

The principles, the two worker mechanisms and the safety model are explained in [DESIGN.md](DESIGN.md). This README is the reference for every endpoint, CLI command, setting and review flow.

---

## Installation & Verification

Enable the plugin and run the built-in diagnostics from the plugin directory:

```bash
hermes plugins enable superagent
hermes plugins doctor .
hermes plugins validate .
```

Both commands are safe to run from the plugin directory. A Python/backend change (route, handler, runner) is only picked up by the gateway after `hermes gateway restart`; a change to `desktop/plugin.js` only needs ⌘K → **Reload desktop plugins**. New agent tools (`superagent_ask`, `superagent_tick`, `superagent_status`, `superagent_run`) become available in **new** Hermes chat or cron sessions started after enabling the plugin. Release notes live in [`CHANGELOG.md`](CHANGELOG.md).

---

## Configuration

Configure `superagent` via the Hermes CLI (`hermes config set plugins.entries.superagent.settings.<key> <value>`) or in **Desktop -> Settings -> Plugins -> Superagent**.

```bash
# Inspect current settings
hermes config get plugins.entries.superagent.settings

# Point workers at a local repository clone
hermes config set plugins.entries.superagent.settings.repo_path "/path/to/your/repo"

# Switch between observe (default), auto, or off
hermes config set plugins.entries.superagent.settings.mode observe
```

The same values are editable without the CLI: the Superagent page's **⚙ Settings** panel writes `<HERMES_HOME>/plugin-data/superagent/settings.json` (default model/provider, default thinking level, a separate worker model/provider/thinking level, worker harness, named model presets, `max_workers`, `chat_max_turns`, `worker_yolo`). The panel, `GET/POST /settings` and the CLI all read through `sa_settings.py`, so they agree on one merged view.

### Model presets

`model_presets` is a list of named `(model, provider)` pairs. Three spellings are accepted, and the loader normalises all of them to `[{name, model, provider}]`:

```yaml
model_presets:
  - fast=opencode-go:qwen/qwen3.8-27b     # name=provider:model
  - strong=z-ai/glm-5.3-flash             # provider optional
  - deepseek-v4.1-flash                   # legacy plain string: name = model
  - {name: local, model: qwen3-8b, provider: ollama}
```

At most **12** presets are kept, names must be unique (case-insensitive), and every preset needs a `model`. An empty list means **no presets**: the composer hides its chip row and you choose with the model picker (or the configured default). You are never shown a hardcoded model you may have no credentials for. `GET /models` returns `presets` plus a flat `models` list — the preset models in configured order, then the configured `default_model` if it is not already there. The composer shows one chip per preset before a chat starts, and `POST /chats` accepts `preset: "<name>"` (an explicit `model`/`provider` still wins). `hermes superagent chat --preset <name>` resolves the same catalog from the terminal.

### Worker harness

Spawned workers default to `worker_harness: mini`, a bash-only harness modelled on the
[mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) **architecture**. It is the
architecture, not the external package: there is no new Python dependency, no `mini`
binary and no separate model client. A worker is still a normal headless `hermes chat`
turn, but it runs with:

- **one action tool** — the turn is launched with `-t terminal`, so bash is the only
  way to act. (The `superagent` plugin toolset is added when the turn may create or
  approve a PR, because those tools are its only way to publish.)
- **one command per step, in a linear transcript** — the prompt requires exactly one
  terminal command per assistant response, inspection before edits, and the relevant
  checks before it declares the work done.
- **an explicit completion marker** — the final action must be
  `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`; the runner records one `submitted`
  event when it sees the string, then the worker writes a summary of at most 200 words.
- **a hard step cap** — `chat_max_turns` reaches Hermes as `--max-turns`, and the
  existing per-turn timeout/watchdog still applies.

The point is cost and predictability: a bash-only worker has a much smaller tool
schema to reason about, cannot wander into file-tool retries, and produces a transcript
that reads as one command → one observation per step. Set `worker_harness: hermes` to
give workers the full Hermes file + terminal toolset instead. The setting is stored on
each session row (`harness`), so a change applies to workers spawned afterwards, not to
ones already running; `NULL` (every pre-harness row) behaves exactly like `hermes`.

**Roots never run mini.** The Router/root session always keeps the full Hermes toolset
(plus the `superagent` plugin toolset) because it has to spawn and steer workers. `mini`
is a worker-only mode even if a row somehow carries it.

```bash
hermes config set plugins.entries.superagent.settings.worker_harness hermes
# or the equivalent environment override
SUPERAGENT_WORKER_HARNESS=mini
```

### All Settings (`plugin.yaml`)

| Setting | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `mode` | `str` (`off` \| `observe` \| `auto`) | `observe` | `off` = do nothing; `observe` = record requests only; `auto` = launch worker subagents for accepted tickets. |
| `repo_path` | `str` | `""` | Local repository path that worker subagents should work in. |
| `github_repo` | `str` | `""` | `owner/name` repository to poll for `@mentions`. Leave empty to use local requests only. |
| `github_agent_login` | `str` | `""` | The GitHub account login the agent acts as; `@mentions` of this login become requests. |
| `github_allowed_users` | `list` | `[]` | Only these GitHub logins may request work. An empty list means nobody on GitHub is allowed. |
| `github_token` | `secret` | env `SUPERAGENT_GITHUB_TOKEN` | Fine-grained GitHub personal access token with Issues read/write permission on `github_repo`. |
| `max_workers` | `int` | `2` | Maximum number of worker subagents allowed to run in parallel (minimum `1`). |
| `worker_toolsets` | `list` | `["file", "terminal"]` | Toolsets granted to each leaf worker subagent. |
| `allow_proactive` | `bool` | `false` | Let the router pick up unaddressed GitHub comments that match an actionable verb or code/error pattern. |
| `max_runtime_seconds` | `int` | `1800` | Worker runtime limit in seconds (minimum `60`). Workers exceeding this limit are cancelled by the watchdog. |
| `poll_limit` | `int` | `30` | Maximum number of GitHub issue comments fetched per polling pass (`1`–`100`). |
| `default_model` | `str` | `""` | Model used by desktop chat sessions when the request does not pick one. Empty means the CLI default. Env: `SUPERAGENT_DEFAULT_MODEL`. |
| `default_provider` | `str` | `""` | Provider for the default chat model (for example `opencode-go`). Empty means the CLI default. Env: `SUPERAGENT_DEFAULT_PROVIDER`. |
| `default_reasoning_effort` | `str` | `""` | Thinking level for new chats and the fallback for spawned workers when neither `worker_reasoning_effort` nor the router session names one: `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` or `ultra`. Empty leaves the model/CLI default. Env: `SUPERAGENT_DEFAULT_REASONING_EFFORT`. |
| `worker_model` | `str` | `""` | Model spawned workers use. Empty means each worker inherits its router session's model (the behaviour before this setting existed). A `superagent_spawn` `model` argument still wins. Env: `SUPERAGENT_WORKER_MODEL`. |
| `worker_provider` | `str` | `""` | Provider for the worker model (for example `opencode-go`). Empty means workers inherit the router session's provider. Env: `SUPERAGENT_WORKER_PROVIDER`. |
| `worker_reasoning_effort` | `str` | `""` | Thinking level for spawned workers (`none…ultra`). Empty means each worker inherits its router session's level. A `superagent_spawn` `reasoning_effort` argument still wins. Env: `SUPERAGENT_WORKER_REASONING_EFFORT`. |
| `worker_harness` | `str` (`mini` \| `hermes`) | `mini` | Toolset contract for spawned workers. `mini` = mini-swe-agent style bash-only worker (`-t terminal`, one command per step, explicit completion marker); `hermes` = the full file + terminal toolset. Roots always keep the full Hermes toolset. Env: `SUPERAGENT_WORKER_HARNESS`. |
| `model_presets` | `list` | `[]` | Named models offered as composer chips. Empty means no chips (the model picker still works). Env: `SUPERAGENT_MODEL_PRESETS` (comma separated). |
| `worker_yolo` | `bool` | `true` | Pass `--yolo` to headless chat workers so they do not wait for approval prompts. Env: `SUPERAGENT_WORKER_YOLO`. |
| `chat_max_turns` | `int` | `120` | Maximum agent turns for one headless chat worker. Env: `SUPERAGENT_CHAT_MAX_TURNS`. |
| `github_watch_mode` | `str` (`observe` \| `auto`) | `observe` | `observe` records PR mentions as `observed` sessions only; `auto` queues and launches a worker for each one. Env: `SUPERAGENT_GITHUB_WATCH_MODE`. |
| `github_watch_new_prs` | `bool` | `false` | Start a review session whenever a watched pull request gets a new head commit. Env: `SUPERAGENT_GITHUB_WATCH_NEW_PRS`. |
| `github_poll_seconds` | `int` | `90` | Seconds between background GitHub polls (minimum `5`). Env: `SUPERAGENT_GITHUB_POLL_SECONDS`. |
| `github_pr_limit` | `int` | `10` | Maximum open pull requests checked per poll (`1`–`100`). Env: `SUPERAGENT_GITHUB_PR_LIMIT`. |
| `turn_timeout_seconds` | `int` | `1800` | A queued or running chat turn older than this is cancelled by the next reap or chat-list read. `0` disables the guard. Env: `SUPERAGENT_TURN_TIMEOUT_SECONDS`. |
| `allow_worker_git_worktree` | `bool` | `true` | Isolate each spawned town worker into its own git worktree (`<data_dir>/workspaces/worker-<session_id>`) so parallel workers do not collide. Env: `SUPERAGENT_ALLOW_WORKER_WORKTREE`. |
| `allow_worker_pr_create` | `bool` | `false` | Allow workers/Router to commit worktree changes, push a `superagent/<session_id>` branch, and open a GitHub PR via `superagent_pr_create` or **Create PR**. Env: `SUPERAGENT_ALLOW_WORKER_PR_CREATE`. |
| `allow_router_pr_approve` | `bool` | `false` | Allow Router/workers to submit `APPROVE` and `REQUEST_CHANGES` reviews on GitHub PRs via `superagent_pr_review` or the review panel. Env: `SUPERAGENT_ALLOW_ROUTER_PR_APPROVE`. |
| `github_pr_base_branch` | `str` | `""` | Base branch for worker-created PRs (empty uses the repository's `default_branch`). Env: `SUPERAGENT_GITHUB_PR_BASE_BRANCH`. |
| `github_pr_draft_by_default` | `bool` | `false` | Open worker-created PRs as drafts by default. Env: `SUPERAGENT_GITHUB_PR_DRAFT`. |
| `petdex_enabled` | `bool` | `true` | Fetch and cache random character sprites from `petdex.dev` (falls back to bundled offline sheets when `false` or offline). Env: `SUPERAGENT_PETDEX_ENABLED`. |

### Environment overrides

The dashboard backend is loaded outside the plugin package, so it reads these variables directly. They mirror the settings above and win when both are present:

| Variable | Meaning |
| :--- | :--- |
| `SUPERAGENT_DEFAULT_MODEL` | Default chat model. |
| `SUPERAGENT_DEFAULT_PROVIDER` | Default chat provider. |
| `SUPERAGENT_DEFAULT_REASONING_EFFORT` | Default thinking level (`none…ultra`). |
| `SUPERAGENT_WORKER_MODEL` | Model for spawned workers (empty = inherit the router). |
| `SUPERAGENT_WORKER_PROVIDER` | Provider for the worker model (empty = inherit the router). |
| `SUPERAGENT_WORKER_REASONING_EFFORT` | Thinking level for spawned workers (empty = inherit the router). |
| `SUPERAGENT_WORKER_HARNESS` | Worker harness: `mini` (bash-only, the default) or `hermes` (full file + terminal). |
| `SUPERAGENT_MODEL_PRESETS` | Comma-separated model picker presets. |
| `SUPERAGENT_WORKER_YOLO` | `1`/`true`/`yes`/`on` to pass `--yolo`. |
| `SUPERAGENT_CHAT_MAX_TURNS` | Maximum agent turns per chat worker. |
| `SUPERAGENT_TURN_TIMEOUT_SECONDS` | Runaway-turn limit; `0` disables it. |
| `SUPERAGENT_REPO_ROOT` | Root directory every per-session repo must stay inside (defaults to `SUPERAGENT_REPO`). |
| `SUPERAGENT_REPO` | Default repository for chat workers and the containment root. |
| `SUPERAGENT_MAX_WORKERS` | Parallel chat workers per database. |
| `SUPERAGENT_WORKER_TOOLSETS` | Comma-separated toolsets for chat workers. |
| `SUPERAGENT_HERMES_BIN` | `hermes` executable used for chat workers. |
| `SUPERAGENT_CHAT_SOURCE` | Source tag passed to `hermes chat` (default `tool`). |
| `SUPERAGENT_GITHUB_REPO` | `owner/name` watched for pull requests. |
| `SUPERAGENT_GITHUB_TOKEN` | GitHub token; also the fallback for a `${...}` placeholder in `plugin.yaml`. |
| `SUPERAGENT_GITHUB_AGENT_LOGIN` | Login whose `@mention` requests work (otherwise resolved from the token). |
| `SUPERAGENT_GITHUB_ALLOWED_USERS` | Comma-separated logins allowed to request work (empty = nobody). |
| `SUPERAGENT_GITHUB_WATCH_MODE` | `observe` or `auto` for the GitHub watcher. |
| `SUPERAGENT_GITHUB_WATCH_NEW_PRS` | `1`/`true` to review every new PR head commit. |
| `SUPERAGENT_GITHUB_POLL_SECONDS` | Background poll interval (minimum `5`). |
| `SUPERAGENT_GITHUB_PR_LIMIT` | Open pull requests checked per poll (`1`–`100`). |
| `SUPERAGENT_ALLOW_WORKER_WORKTREE` | `1`/`true` to create an isolated git worktree per spawned worker. |
| `SUPERAGENT_ALLOW_WORKER_PR_CREATE` | `1`/`true` to allow workers/Router to commit, push feature branches, and open GitHub PRs. |
| `SUPERAGENT_ALLOW_ROUTER_PR_APPROVE` | `1`/`true` to allow Router/workers to submit `APPROVE`/`REQUEST_CHANGES` PR reviews. |
| `SUPERAGENT_GITHUB_PR_BASE_BRANCH` | Default base branch for worker-created PRs. |
| `SUPERAGENT_GITHUB_PR_DRAFT` | `1`/`true` to open worker-created PRs as drafts by default. |
| `SUPERAGENT_PETDEX_ENABLED` | `1`/`true` to enable live `petdex.dev` manifest and sprite fetching. |

---

## Usage Across Surfaces

> **Important Architectural Rule:** Ticket worker subagents can **only** be launched from an active Hermes agent turn (an interactive chat session or a scheduled cron turn). `ctx.subagent_lifecycle` requires an active parent session (`bind_subagent_parent`). The CLI, slash commands, and ticket REST endpoints can inspect state and queue tickets, but they never launch ticket workers directly (`launch=False`). The chat endpoints are separate: they start a headless `hermes chat` process, which needs no parent turn and is not a subagent.

### 1. Agent Tools (Chat Session or Cron Turn)

Inside a Hermes session, the agent has nine tools in the `superagent` toolset (four for tickets, five for chat towns and GitHub PR workflows):

- **`superagent_ask(request)`**: Record a coding request as a ticket (`observed` in `observe` mode, `queued` in `auto` mode, or `blocked` if it looks like a design question).
- **`superagent_tick(wait=false)`**: Run one full routing pass: poll GitHub (if configured), deduplicate and assess new comments, launch worker subagents for `queued` tickets (in `auto` mode, up to `max_workers`), reap finished workers into `review` or `failed`, and cancel timed-out workers. Pass `wait=true` to block until active workers finish.
- **`superagent_status(ticket_id=None)`**: Show summary counts, recent tickets, and the `"Needs you"` attention queue (`observed`, `blocked`, `review`, `failed`). Pass `ticket_id` (e.g. `"T-0001"`) to inspect full ticket details and its audit event log.
- **`superagent_run(ticket_id, wait=false)`**: Human-in-the-loop promotion. Approves an `observed`, `blocked`, or `failed` ticket, moves it to `queued`, and immediately attempts to launch a worker subagent for it.
- **`superagent_spawn(parent_session_id, prompt, title=None, pr_number=None, use_worktree=None, model=None, provider=None, reasoning_effort=None)`**: Spawn a real child worker session under a root/Router session (`parent_session_id`), allocating a random free town plot (`0…11`), a Petdex `avatar_slug`, and (when `allow_worker_git_worktree` is on) an isolated git worktree (`<data_dir>/workspaces/worker-<id>` or PR checkout). `model`/`provider`/`reasoning_effort` are optional per-spawn overrides: each resolves as explicit argument → `worker_*` setting → the router session's value, so a worker can run a cheaper or faster model than the router.
- **`superagent_town_status(parent_session_id, worker_session_id=None, include_diff=False)`**: Inspect the live status of all workers in a town (or detailed messages, diff stats, and PR links for a single worker).
- **`superagent_message_worker(parent_session_id, worker_session_id, message, action="follow_up", pr_number=None)`**: Send follow-up instructions, PR creation requests (`action="create_pr"`), or PR review/approval requests (`action="review_pr"` / `"approve_pr"`) from a Router to one of its idle/review/failed workers.
- **`superagent_pr_create(session_id, title, body="", branch="", base="", draft=None, commit_message="")`**: Commit changes in a session's repository/worktree, push a `superagent/<session_id>` branch, and open (or update) a GitHub Pull Request. Gated by `allow_worker_pr_create`.
- **`superagent_pr_review(session_id, event="COMMENT", body="", pr_number=None)`**: Submit a GitHub Pull Request review (`COMMENT`, `APPROVE`, or `REQUEST_CHANGES`, with automatic fallback to an approval comment if GitHub returns HTTP 422 on self-approval). `APPROVE` and `REQUEST_CHANGES` are gated by `allow_router_pr_approve`.

### 2. CLI (`hermes superagent`)

Use the CLI from any terminal to inspect tickets, queue work, or check health (`--json` is supported on all subcommands):

```bash
# Show mode, running/queued counts, "Needs you" attention list, and recent tickets
hermes superagent status
hermes superagent status --json

# Queue a new request from the command line
hermes superagent ask "Add unit tests for token expiration edge cases"

# Poll sources and record new tickets (does not launch workers from CLI)
hermes superagent tick

# Approve a ticket (moves to queued; worker starts on the next agent turn)
hermes superagent run T-0001

# Show full detail and history for one ticket
hermes superagent show T-0001

# Chat: pick a named preset, or list/inspect the merged settings
hermes superagent chat "Add a regression test" --preset fast
hermes superagent models
# `settings` includes the worker model line (`cheap · opencode-go · low`),
# or `worker model  inherit router` when no worker override is configured.
hermes superagent settings
hermes superagent settings --json

# Find a chat by what was said in it, not just its title
hermes superagent search "rollback never ran"
hermes superagent search "rollback" --archived all --json

# See the router/worker town hierarchy: slots, statuses and linked PRs
hermes superagent towns
hermes superagent towns --limit 10 --show-archived --json

# Re-run the last prompt of a failed or cancelled chat
hermes superagent retry S-0001
hermes superagent retry S-0001 --detach

# Save a transcript as Markdown (stdout, a file, or a directory)
hermes superagent export S-0001
hermes superagent export S-0001 --out review.md
hermes superagent export S-0001 --out ./exports

# Activity report: chats, turns, tokens, per-day bars and the model split
hermes superagent usage
hermes superagent usage --days 30 --json

# Keep a chat at the top of the rail, or release it
hermes superagent pin S-0001
hermes superagent unpin S-0001

# Review what a finished session changed, then comment on its PR
hermes superagent diff S-0001
hermes superagent comment S-0001
hermes superagent comment S-0001 --body-file review.md

# Diagnose the install read-only: version/API, database totals, hermes binary,
# repository (branch + dirty count), worktrees, model choices and the data-tier
# gate. Never starts an engine, a worker or a network call.
hermes superagent doctor
hermes superagent doctor --json
```

`hermes superagent towns` prints the same hierarchy the desktop town view draws: each root town (newest activity first) with its worker and worker-status counts, then one indented line per worker showing the plot slot, session id, status, elapsed time and any linked PR number and URL. The default limit is 50 (`--limit`, clamped to 1–200); archived towns and workers are hidden unless `--show-archived` is passed, and orphaned workers (whose parent town row is gone) are counted at the end.

| Command | Behaviour |
| :--- | :--- |
| `hermes superagent status` | Mode, running/queued counts, the "Needs you" list and recent tickets. |
| `hermes superagent tick` | One routing pass: polls sources and records tickets (never launches a worker from the CLI). |
| `hermes superagent ask "<request>"` | Record a request as a ticket. |
| `hermes superagent run <ticket-id>` | Promote an `observed`/`blocked`/`failed` ticket to `queued`. |
| `hermes superagent show <id>` | A ticket's full detail, or a chat session (`S-…`) with messages and events. |
| `hermes superagent chat "<prompt>"` | Start a headless chat turn (blocking; `--detach` returns immediately). `--preset <name>` resolves a named preset; `--model`/`--provider` still win. `--reasoning <level>` selects `none…ultra` for this session. |
| `hermes superagent sessions` / `stop <id>` | List chat sessions (archived ones are hidden; `--all` shows them) / cancel a queued or running turn. |
| `hermes superagent towns` | Router towns with their worker houses: worker/status counts, then slot, session id, status, elapsed time and linked PR per worker. `--limit` (1-200), `--show-archived`, `--json`. |
| `hermes superagent diff <id>` | `git status` + `git diff --stat` + the (capped) patch for the session's repository; `--json` returns the full dict. `1` for an unknown session or one without a repository. |
| `hermes superagent comment <id> [--body TEXT \| --body-file PATH]` | Post one summary comment to the session's pull request and print its URL. `1` without a PR/token or when GitHub refuses, `2` for bad usage. |
| `hermes superagent models` | Default model/provider plus the preset section (`name -> provider: model`). |
| `hermes superagent settings [--json]` | Print the merged settings (the GitHub token is masked to `set`/`not set`); read-only. |
| `hermes superagent github [status\|test\|connect\|disconnect\|prs\|poll\|work <n>]` | Connect a repository, manage the watch settings, list open PRs, and start work on one (see [GitHub connect & PR watch](#9-github-connect--pr-watch-v03)). |
| `hermes superagent cleanup [--json]` | Prune stale `pr-*` and `worker-*` git worktree directories under `<data_dir>/workspaces` that are not referenced by any active (non-archived) session. |
| `hermes superagent doctor` | Read-only installation diagnosis: plugin/API version, database totals (towns, workers, running, archived), the resolved `hermes` binary, the configured repository (exists, git work branch, dirty count), stale `worker-*`/`pr-*` worktrees, default/worker models and the data-tier gate. `ok`/`warn`/`FAIL` per check; exit `1` when any check fails. Never writes, launches a worker or touches the network. |

Every subcommand accepts `--json`. Exit codes: `0` ok, `1` runtime error, `2` usage error.

### 3. Slash Command (`/superagent`)

Inside Hermes chat interfaces, use `/superagent` for quick inspection and queueing:

```text
/superagent status
/superagent ask Fix off-by-one pagination bug in list_users
/superagent tick
/superagent run T-0001
/superagent help
```

Note: `/superagent run <id>` promotes the ticket to `queued`; if called outside an active agent tool turn, the worker starts on the next `superagent_tick` tool call.

### 4. Automation Blueprint (`Superagent watch`)

`superagent` registers a cron blueprint named **`Superagent watch`** (`watch`) scheduled every 5 minutes (`*/5 * * * *`).

Because cron automations execute as full Hermes agent turns, `Superagent watch` calls `superagent_tick` with `wait=true`, launches and reaps worker subagents automatically, and reports **only** tickets that require human attention (or replies `"Superagent: nothing needs you."`).

### 5. Desktop Panel & Dashboard API

The plugin ships a unified desktop extension (`desktop/plugin.js`) and a FastAPI backend (`dashboard/plugin_api.py` mounted at `/api/plugins/superagent`). GET ticket routes are read-only; `POST /ask`, `/run` and `/tick` record or queue tickets but cannot launch a ticket worker from REST. The chat, settings and GitHub routes drive the page:

- `GET /api/plugins/superagent/health` — plugin version, `api_version`, the `features` list, `pid`, `started_at` (plugin load time), SQLite database status and `archived_count`.
- `GET /api/plugins/superagent/tickets?limit=30` — status counts, `"Needs you"` attention list, and recent tickets.
- `GET /api/plugins/superagent/tickets/{ticket_id}` — full ticket details and event log.
- `GET /api/plugins/superagent/sessions` — enriched ticket rows (the ticket sessions grid; not chat sessions).
- `POST /api/plugins/superagent/ask` · `/run` · `/tick` — record a ticket, queue a promotion, run one routing pass.
- `GET|POST /api/plugins/superagent/chats` — list chat sessions / start a new headless chat.
- `GET /api/plugins/superagent/chats/{session_id}` — one chat with its transcript and events.
- `PATCH /api/plugins/superagent/chats/{session_id}` — rename a chat (`{title}`, 1–200 chars; `400` empty/oversized, `404` unknown).
- `POST /api/plugins/superagent/chats/{session_id}/archive` — archive (`{archived: true}`, the default) or restore one chat; `409` while its turn is queued/running.
- `DELETE /api/plugins/superagent/chats/{session_id}` — delete a chat and its transcript (`409` while queued/running). Files, worktrees and PRs are never touched.
- `POST /api/plugins/superagent/chats/{session_id}/messages` — send a follow-up and resume the Hermes session.
- `POST /api/plugins/superagent/chats/{session_id}/model` — set model/provider for future turns.
- `POST /api/plugins/superagent/chats/{session_id}/stop` — cancel the running turn.
- `GET /api/plugins/superagent/chats/{session_id}/diff` — git status, stat and a capped patch for the session's repository.
- `GET /api/plugins/superagent/chats/{session_id}/comment-preview` — the comment body a post would send (no network, no write).
- `POST /api/plugins/superagent/chats/{session_id}/comment` — post one human-confirmed comment to the session's pull request.
- `GET|POST /api/plugins/superagent/settings` — merged public settings / save the panel-editable keys (the token is never returned).
- `GET /api/plugins/superagent/models` — default model/provider and the configured presets (`[]` when none are configured).
- `GET /api/plugins/superagent/usage?days=14` — read-only activity report: totals (`sessions`, `turns`, `tokens`, `duration_ms`, status `counts`), one bucket per local day, and the per-model split. `days` is clamped to `1–90`.
- `GET /api/plugins/superagent/github/status` · `POST /github/{test,connect,settings,disconnect,poll}` · `GET /github/prs` · `POST /github/prs/{n}/work` — connect one repository, watch PRs and start a session on one (see [GitHub connect & PR watch](#9-github-connect--pr-watch)).

The desktop page at `/superagent` leads with the chat workspace (see [Desktop chat workspace](#7-desktop-chat-workspace)); the ticket surface lives beside it:

- **Chat workspace** — a chat rail, a live transcript, a composer with model picker and optional repo path, and the **Live workers** grid. The first prompt starts a headless `hermes chat` worker; follow-ups resume it; **Stop** cancels it.
- **Ticket pane** (right dock) — the `"Needs you"` attention list (`observed` / `blocked` / `review` / `failed`) and recent tickets from `GET /tickets`. The status-bar chip only falls back to this list when the chats query has no data.
- **Settings and GitHub panels** — full-width collapsible panels above the workspace, opened from the header.

Enable the desktop view under **Desktop → Settings → Plugins → Superagent** (then reload desktop plugins) to see the status bar chip and the Superagent pane.

### 6. Ticket sessions API (`Engine.sessions` / `get_ticket_detail`)

For programmatic and UI use, the engine exposes a read-only sessions layer (no behavior change to `ask` / `tick` / `promote`):

```python
engine.sessions(limit=50, status=None)   # {"sessions": [...], "counts": {...}, "running": N, "queued": N}
engine.get_ticket_detail("T-0001")       # full ticket dict + "events" list; raises KeyError when missing
```

Each session row contains `id`, `status`, `source`, `requester`, `request` (160-char preview), `reason`, `error`, `handle`, `result_preview` (200-char preview), `event_count`, `created_at` / `updated_at` / `started_at`, and `elapsed_secs` (now minus `started_at`, falling back to `created_at`). The store helpers behind it (`Store.list_tickets(status, limit)`, `Store.get_events(ticket_id)`, `Store.count_by_status()`) are pure `SELECT`s with no schema change.

---

### 7. Desktop town workspace

The Superagent page (`/superagent`) is town-first. One **root** chat is one town;
the workers it spawns are houses in that town. Everything is backed by `/towns`
and `/chats`, so the scene cannot invent activity that has no session row.

- **Town scene & environment** — the centre is the Router Town Hall (`house-router`) on a paved cobblestone square (`plaza-stone`), with a central fountain, wishing well, noticeboard, park benches, wildflowers, an artisan stream (`water`) crossed by a wooden footbridge (`bridge`), cobblestone avenues (`road-h`, `road-v`) branching to all 12 worker plots (`TOWN_PLOTS`, clustered 22–78% around the plaza so neighbours feel close), and an SVG overlay (`data-town-connections="1"`) drawing dashed Router↔worker connection paths that glow warm gold while a worker runs. A device-clock sky tint (`data-town-time="dawn|day|dusk|night"`, re-checked every 30 s from `new Date()`) washes the ground warm at dawn, violet at dusk and deep blue at night; streetlamps glow whenever workers run and always after dusk. Every worker gets one house (`house-a` through `house-g`: cottage, tavern, workshop, forge, library, post office, observatory) placed at the random free `town_slot` (`0…11`) allocated when the child row was created, so polling, a town switch or a reload never moves a house. Houses carry text status badges (`queued`, `running`, `review`, `failed`, `cancelled`), not just animation; streetlamps emit a warm glow when workers are running; the switcher shows how many of the twelve plots are used. `TownScene` forms an isolated CSS stacking context (`isolation: isolate; z-index: 0` with internal elements at `3..25`) so town houses and sprites never bleed over Settings, GitHub, Usage, Sessions (`z-40`) or shortcut sheets (`z-50`).
- **Town HUD filter bar, device clock & simulation pause** — a compact HUD at the top of the town canvas provides filter pills (`All`, `Running`, `Review`, `Failed`, `PRs`) to spotlight matching workers and dim non-matching ones (`opacity-35`), a live device-clock chip (`data-town-clock`, `☀️/🌤️/🌙 HH:MM`) showing the phase the sky follows, plus a **⏸ Pause / ▶ Walk** button to pause or resume autonomous character walking at any time.
- **Petdex characters, autonomous movement & speech bubbles** — each Router and worker is assigned a durable `avatar_slug` from [petdex.dev](https://petdex.dev/) (`GET /town/pets`). Characters render in a depth-sorted `TownActor` layer driven by `useTownSimulation` (`requestAnimationFrame` throttled to 25 fps, disabled when `prefers-reduced-motion` is active). They walk between their house doorstep, the central plaza, fountain, noticeboard and Router Hall based on session status, play all 9 Petdex animation rows (`idle`, `running-right`, `running-left`, `waving`, `jumping`, `failed`, `waiting`, `running`, `review`), pause to wave to passing neighbours, and display clickable `TownSpeechBubble` pills (opaque `var(--card)` fill, never translucent, so the sky tint cannot show through; fixed `w-36 h-14` with `overflow-y-auto` so long telemetry scrolls instead of stretching the town) showing live tool telemetry or status banter. Clicking any character or bubble opens that session's chat dialog.
- **One-shot construction** — a house animates only when its id appears after
  the initial snapshot; the known-worker ids are persisted per town, so a
  refresh does not replay every build. `prefers-reduced-motion` skips the
  animation and its timer.
- **Pixel chrome (font, scrollbars, windows)** — the town canvas, HUD clock, speech bubbles and house dialogs use a system pixel-font stack (`VT323`, `Px437 IBM VGA`, `IBM Plex Mono`, monospace fallback — no remote fetch) plus stepped 2px `superagent-pixel-panel` borders and retro `superagent-pixel-scroll` scrollbars (gold thumb on dark track, `scrollbar-color` + `::-webkit-scrollbar`). Bubbles keep their tone borders; dialogs keep host colours.
- **Town switcher** — a compact town picker (`data-town-picker="1"`: a button showing the current town and status, opening a themed `Towns (N)` popover panel — `bg-background`, `border-(--ui-stroke-secondary)`, `max-h-72`, `superagent-pixel-scroll` — with one 28px row per town showing its truncated title, status and worker count, the selected row highlighted; roots only, so a worker session can never appear as an option), previous/next buttons, **🎲 Reroll sprites** (`POST /town/pets/reroll`), **↻ Retry failed (N)** (`POST /towns/{root_id}/retry-failed`, shown when any worker in the town failed or was cancelled), **New town** and
  **Sessions**. Switching changes only what is displayed: no session is
  stopped, archived or modified. Arrow-left/right do the same from the page
  when focus is not in a field; the selected town id is persisted.
- **House dialog** — clicking any house (or its walking sprite/bubble) opens that session's transcript and
  composer in a fixed viewport-proportional popup (`54vw × 78vh`, `40vw × 70vh`
  for generic modals, no content-driven sizing) with the transcript scrolling
  inside (`superagent-pixel-scroll`), so a long chat never covers the town and
  the top is always visible. The chat column itself is a fixed `52vw` on every
  host (dialog, workspace tile, full page), and the transcript caps at `56vh`,
  so no surface can grow with the chat — it scrolls instead. It reuses the existing `ChatHeader`,
  `Transcript`, live-worker strip and `Composer`, so follow-ups, stop, retry,
  model/thinking changes, **Diff**, **Create PR**, **Comment / Approve PR**, export and Router dispatch actions behave consistently. Escape/backdrop close it, Tab is trapped,
  focus returns to the house, and a session-id guard prevents a previous
  session's transcript from flashing while the next one loads. Closing never
  stops the worker.
- **House actions** — the dialog's `⋯` menu renames, pins, archives, dispatches Router→Worker review/PR actions (`POST /towns/{root_id}/dispatch`), and opens
  the Sessions drawer. The drawer is the complete inventory: search, rename,
  pin, archive/unarchive and delete still work for rows that have no town or
  house (orphaned workers, archived chats, failed launches).
- **Settings** — an opaque drawer with default model, default thinking,
  a **Worker model** block (spawned workers may use a different model/provider/thinking level, or **Inherit router**),
  presets, `max_workers`, `worker_yolo`, `chat_max_turns`, plus worker git worktree isolation (`allow_worker_git_worktree`), worker PR creation (`allow_worker_pr_create`), and Router/worker PR review approval (`allow_router_pr_approve`). It sits on the
  `--card` surface over a dimmed backdrop, traps Tab and is 420 px wide.
- **Header and status** — the header keeps GitHub, Usage and the labelled
  Settings action; completion toasts and the status-bar chip still speak for
  chats. An orphan count opens the Sessions drawer instead of drawing a
  phantom town.
- **Transcript and composer** — the prompt is the first bubble, assistant text
  interleaves with tool events, a failed turn shows its stored error, and the
  composer is disabled while a turn is queued/running. Archived chats are
  read-only until unarchived.
- **Assets** — the 26 vendored town PNGs are served by `GET /town/assets` (data-URL manifest + binary route), and Petdex character sheets are proxied, 4× downsampled (`384×468` WebP), cached on disk and served by `GET /town/pets` (with 8 bundled offline sheets under `assets/town/pets/`). Attribution and licences are in
  `assets/town/ATTRIBUTION.md`.
- **Polling** — the town uses the existing chat cadence (2 s while anything
  runs, 8 s when quiet); opening a town does not start a second network poll loop.

A chat turn's lifecycle is `queued` → `running` → `review` (or `failed` /
`cancelled`). `review` means the worker finished and its changes are waiting in
the working tree (or isolated worker worktree); sending another message resumes the same Hermes session when
`hermes_session_id` is known. Root sessions get the `superagent` toolset so
they can call `superagent_spawn`, `superagent_town_status`, `superagent_message_worker`, `superagent_pr_create`, and `superagent_pr_review`; worker sessions get the `superagent` toolset when `allow_worker_pr_create` or `allow_router_pr_approve` is enabled (while `superagent_spawn` and `superagent_message_worker` remain restricted to root Routers).

---

### 8. Chat and models API

| Method | Path | Behaviour |
| :--- | :--- | :--- |
| `GET` | `/models` | `{default_model, default_provider, default_reasoning_effort, models, presets}` from settings/env. `presets` is `[{name, model, provider}]` and is `[]` when none are configured; `models` is the flat list of preset models in order plus the configured `default_model`, never a hardcoded catalogue. |
| `GET` | `/health` | `{ok, version, api_version, features, pid, started_at, db_exists, db_path, archived_count}`. `features` advertises `towns`, `thinking` and `data_training_tiers`; the page uses it to explain an older gateway instead of a generic error. |
| `GET` | `/towns?limit=200&archived=hide` | `{db_exists, towns, town_count, worker_count, running, counts, orphaned, orphaned_count, archived_count}`. A town is a root (`parent_session_id` NULL) with its workers; a worker is never returned as a town. Ordering is pinned roots first, then newest activity. Root and worker rows carry `town_slot`, `avatar_slug`, `branch_name`, `worktree_path`, `message_count`, and `recent_events`. |
| `GET` | `/town/assets` | `{ok, assets}` — a data-URL manifest of the 26 allowlisted town PNGs. No filesystem path is accepted. |
| `GET` | `/town/assets/{name}` | The PNG bytes with `image/png` and a one-day cache; an unknown name is `404`. Only the explicit allowlist can be served. |
| `GET` | `/town/pets?slugs=s1,s2` | `{ok, pets}` — downsampled (`384×468` WebP, `48×52` per frame, `8×9` grid) Petdex sprite sheets and metadata (`display_name`, `kind`, `submitted_by`, `data_url`) for the requested slugs. Falls back to bundled offline pets if unknown or offline. |
| `POST` | `/town/pets/reroll` | `{session_id?, root_id?}` — re-randomize `avatar_slug` for a single session or all sessions in a town (`root_id`), keeping inhabitants distinct. Returns `{ok, updated}`. |
| `POST` | `/towns/{root_id}/dispatch` | `{worker_session_id, message?, action?, pr_number?}` — dispatch a follow-up instruction, `create_pr`, `review_pr`, or `approve_pr` task from the town's Router (`root_id`) to a specific worker (`worker_session_id`). |
| `POST` | `/towns/{root_id}/retry-failed` | Batch-retry all `failed` or `cancelled` workers belonging to `root_id`. Returns `{ok, root_id, retried, count, skipped, errors}`. |
| `POST` | `/workspaces/cleanup` | Prune stale `pr-*` and `worker-*` git worktrees under `<data_dir>/workspaces` that are not referenced by any active (non-archived) session. Returns `{ok, removed, kept, removed_count, kept_count}`. |
| `GET` | `/usage?days=14` | `{ok, db_exists, days, since, totals, series, models}` — chats and turns created in the window, summed `tokens`/`duration_ms`, status `counts`, one `{date, sessions, tokens}` bucket per **local** day (filled to `days`), and `{model, provider, sessions, tokens}` rows busiest first. `days` is clamped to `1–90`. |
| `GET` | `/settings` | Merged, public settings (`mode`, `repo_path`, defaults including `default_reasoning_effort`, worker model overrides (`worker_model`, `worker_provider`, `worker_reasoning_effort`), the worker harness (`worker_harness`), normalised `model_presets`, worker limits, GitHub watch fields, worktree/PR capability toggles) plus `github_token_set`; never the token. `data_training_tiers_allowed` reports the **host** state of `security.allow_data_training_tiers_noninteractive` in `config.yaml` — it is not a Superagent setting. |
| `POST` | `/settings/data-training-tier` | `{allowed: bool}` — write the host opt-in through `hermes config set security.allow_data_training_tiers_noninteractive`, never by editing YAML. Returns `{ok, allowed, config_path, settings}` with the **observed** state after the write. `400` for a missing/non-boolean `allowed` or a failed write (the command's stderr tail is reported). |
| `POST` | `/settings` | Validate and save `mode`, `repo_path`, `default_model`, `default_provider`, `default_reasoning_effort`, `worker_model`, `worker_provider`, `worker_reasoning_effort`, `worker_harness` (`mini` or `hermes`), `model_presets`, `max_workers` (1–8), `worker_yolo`, `chat_max_turns` (1–500), `allow_worker_git_worktree`, `allow_worker_pr_create`, `allow_router_pr_approve`, `github_pr_base_branch`, `github_pr_draft_by_default`, `petdex_enabled`; unknown keys and out-of-range values are `400`. Drops the Runner singleton so the next turn uses the new values. Returns `{ok, settings}`. |
| `GET` | `/chats?limit=50&q=` | `{db_exists, sessions, counts, running, archived_count}`; also runs the runaway watchdog before answering. An optional `archived` query takes `hide` (default: no archived rows), `only` or `all`; an unknown value is `400`. An optional `q` searches titles, prompts and every stored message instead of listing, adding `match_field` (`title`/`prompt`/`message`) and `match_snippet` (≤160 chars) per row; a term over 200 chars is `400`. `counts` describe the visible board, not the hit list. |
| `POST` | `/chats` | Validate, create and launch a chat. Optional `preset` selects a named model (explicit `model`/`provider` still win; unknown preset is `400` listing the available names) and records a `preset` event. `parent_session_id` creates a real worker under a root (one level, known/root parent only). When a worker request omits `model`, `provider` or `reasoning_effort`, the `worker_*` setting is used before the router session's value; with those settings empty the worker inherits the router's model, provider and `reasoning_effort`, and an explicit request value still wins. A root row is always stored with `harness="hermes"`; a worker takes `worker_harness` (`mini` by default). `reasoning_effort` accepts `none…ultra`. `400` on an empty/oversized prompt, an invalid repo, or a data-training tier the host has not opted into (see Guardrails); `200 {ok:false}` when the worker cannot start (the session is stored as `failed`). |
| `PATCH` | `/chats/{id}` | Rename: `{title}` is trimmed, whitespace-collapsed and capped at 200 chars. `400` empty/oversized/non-string, `404` unknown id. Returns `{ok, session}`; allowed while a turn runs. |
| `POST` | `/chats/{id}/pin` | `{pinned?: true}` pins a chat above the timeline; `{pinned: false}` releases it. Display-only: allowed while a turn runs, never touches `updated_at`, so unpinning returns the row to its real place. `404` unknown. Returns `{ok, session}`. |
| `POST` | `/chats/{id}/archive` | `{archived?: true}` archives (stamps `archived_at` once, leaves `updated_at` alone) and `{archived: false}` restores. `409` while queued/running, `404` unknown. Returns `{ok, session}`. |
| `DELETE` | `/chats/{id}` | Delete the row, its `chat_messages` and its `chat_events` in one transaction (`gh_seen` is kept so a GitHub comment is not replayed). `409` while queued/running, `404` unknown. Never deletes files. Returns `{ok, id}`. |
| `GET` | `/chats/{id}` | Session with `messages` and `events`; `404` for an unknown id. |
| `POST` | `/chats/{id}/messages` | Append the user message and resume the Hermes session. `409` while a turn is queued/running; `400` when the stored repo is no longer valid; with no `hermes_session_id` a fresh chat is created and `previous_session_id` is returned. |
| `POST` | `/chats/{id}/model` | Set `{model, provider, reasoning_effort}` for future turns. Any one may be supplied; model/provider still write as a pair (a provider-only change clears the pinned model), while an effort-only change leaves the model alone. `400` when all three are absent or the level is unknown, `409` while queued/running, `404` for an unknown id. |
| `POST` | `/chats/{id}/stop` | Cancel a queued/running turn; `409` when nothing is running. |
| `POST` | `/chats/{id}/retry` | Re-run the last stored user prompt of a `failed`/`cancelled` chat, resuming its Hermes session when one exists. `409` while a turn is live or for any other status (a `review` chat is a result to read, not to repeat), `400` with no stored user message, `404` unknown. Clears the failed attempt's `error`/`tokens`/`duration_ms` and records one `retry` event; it never appends a second copy of the prompt. |
| `POST` | `/chats/{id}/pr` | `{title, body?, branch?, base?, draft?, commit_message?}` — commit changes in the session's repository/worktree, push a `superagent/<session_id>` branch, and open or update a GitHub PR. Requires `allow_worker_pr_create=true` (`403` when disabled). Updates `pr_number`/`pr_url` on the session. |
| `POST` | `/chats/{id}/review` | `{event: "COMMENT"\|"APPROVE"\|"REQUEST_CHANGES", body?, pr_number?}` — submit a GitHub PR review on the session's PR. `APPROVE` and `REQUEST_CHANGES` require `allow_router_pr_approve=true` (`403` when disabled); falls back to a formal comment review if GitHub returns HTTP 422 on self-approval. |

#### Guardrails

- **Repository containment.** Before a turn is queued, the target repo is resolved with `Path.resolve(strict=True)`. A path that does not exist, is not a directory, contains `..`, or resolves (including through a symlink/junction) outside `SUPERAGENT_REPO_ROOT` (which defaults to `SUPERAGENT_REPO`) raises `ValueError`, which the handlers turn into `400`. Only the resolved path is passed to `--in` and used as the subprocess `cwd`; the raw request string never reaches `argv`.
- **Caps.** Prompts are limited to 20 000 characters (checked by the runner and by `POST /chats` / `POST /chats/{id}/messages`), stored assistant text is clipped to 20 000, and every event detail is clipped to 2 000. Over-limit prompts are rejected with `400`; over-limit output is truncated before the write, so a runaway worker cannot bloat the database.
- **Secret redaction.** `ghp_`, `gho_` and `github_pat_` tokens, `sk-` and `xoxb-`/`xoxp-` style keys, `Authorization:` header values and bare `Bearer` tokens are replaced with `[redacted]` before chat messages, events, errors, prompts and ticket fields are persisted. The same scrub runs on the preview strings the UI reads.
- **Runaway guard.** A queued or running turn older than `turn_timeout_seconds` (env `SUPERAGENT_TURN_TIMEOUT_SECONDS`, default `1800`; `0` disables) is cancelled by `Runner.reap()`. `GET /chats` calls `reap()` before answering, so the status becomes `cancelled` and a `cancelled` event records the reason — `turn timed out after Ns while queued` or `... while running` — followed by `(limit Ns)`, even if the server restarted and no process handle is left.
- **Data-training tier preflight.** A model whose id carries a `contributor` dash-token (Meta's data-training tier) is refused **before** a session or a queue slot exists, because Hermes refuses that `-m` override in every non-interactive run unless `config.yaml` sets `security.allow_data_training_tiers_noninteractive: true`. `POST /chats`, `superagent_spawn`, `hermes superagent chat` and `Runner.launch_turn` (the backstop for retry/resume) all consult the same `sa_model_policy` check, so the failure names the key, the command and the trade-off instead of leaving a house that died on a consent prompt. `POST /settings/data-training-tier` (and the panel checkbox) flips the host key through `hermes config set`.
- **Workers never own a terminal.** Each turn is spawned with a `stdin` **pipe that is closed immediately**, not `DEVNULL`: on Windows `isatty()` on the NUL device reports true, so a DEVNULL child believes it has a terminal, prints a consent prompt and cancels itself. A closed pipe is the one signal Hermes reads as "no terminal" on every platform, so a prompt can neither hang a turn nor silently cancel it.
- **Consistent triage.** `POST /ask` runs the same `sa_assessor.assess(..., explicit=True)` rules as the router instead of hardcoding `accept`: a skip marker or acknowledgement is recorded as `ignored`, a design question becomes `blocked` (`needs_human`), and an actionable request becomes `observed` (`accept`). The verdict and reason are stored on the ticket and in its `assessed` event.

### 9. GitHub connect & PR watch

Connect one repository and Superagent watches its pull requests. A comment that mentions the agent (`@<login>`, `@superagent`, `/superagent`, or a line starting `superagent:`) from an allowed user becomes a session; when `github_watch_new_prs` is on, a new head commit on a watched PR starts a review session once per SHA.

| Method | Path | Behaviour |
| :--- | :--- | :--- |
| `GET` | `/github/status` | `{configured, repo, login, token_set, open_prs, last_poll, last_error, watching, watch_mode, watch_new_prs, allowed_users, poll_seconds, sessions_created}`; also starts the background poller (idempotent). |
| `POST` | `/github/test` | `{repo?, token?}` — validate a token/repository pair without saving. Returns `{ok, login, full_name, default_branch, private, open_pr_count}`; `400` on a bad repo or rejected token. |
| `POST` | `/github/connect` | `{repo, token?, allowed_users?, watch_mode?, watch_new_prs?, poll_seconds?}` — validate with the supplied or saved token, save the connection and the optional watch settings in one write, reset the baseline, start watching, return the status. `400` on a malformed repo/setting or an auth failure. |
| `POST` | `/github/settings` | `{allowed_users?, watch_mode?, watch_new_prs?, poll_seconds?, agent_login?}` — save watch settings without touching the repository or token; the watcher is rebuilt so the next poll uses them. `400` on an unknown mode, a `poll_seconds` outside `15`–`3600`, or a malformed login. Returns `{ok, status}` (never the token). |
| `POST` | `/github/disconnect` | Clear `github_repo` and `github_token` in `settings.json`, stop the poller. |
| `POST` | `/github/poll` | One polling pass now: `{configured, polled, created, errors}`. |
| `GET` | `/github/prs` | `{configured, prs}` — the cached open PR list. |
| `POST` | `/github/prs/{number}/work` | `{request?}` — create a session for that PR and launch a worker (an explicit human action always launches). `400` when GitHub is not connected, `404` for an unknown PR. |

**Desktop panel.** The Superagent page has a **GitHub** button in the header with a status dot (green when a repository and token are configured) and the repository's short name. It opens a full-width panel above the chat workspace:

- **Connection (left)** — repository (`owner/name`), a password field for the token (blank keeps the saved one; the placeholder says `saved token`), the comma-separated allowed-user list, and an Observe/Auto segmented control. **Connect** saves the whole form in one `POST /github/connect`; **Save settings** sends only the watch fields to `POST /github/settings`; **Poll now** runs one pass; **Disconnect** forgets the repository and token. The panel shows the resolved login, open-PR count, watching state, poll interval and the watcher's `last_error`. The token is never rendered.
- **Open pull requests (right)** — the repository's open PRs with `#number`, a two-line title, author, updated age, and **Work on this PR** (`POST /github/prs/{n}/work`). The returned session is selected immediately, so the transcript opens in the centre column.
- **PR badges** — a session created from a PR carries `pr_number`/`pr_url`, shown as a `#123` badge in the chat header, the session rail and the live-worker cards. Clicking it opens the PR in the browser.
- **Polling** — `/github/prs` refreshes every 20 s only while the panel is open; `/github/status` refreshes at the same interval only while the panel is open or a session with a PR badge is on screen. Otherwise the status is fetched once for the header dot, so the chat workspace keeps its existing polling budget.

**CLI.** `hermes superagent github ...` uses `sa_settings`/`sa_github`/`GitHubWatcher` directly (no HTTP, no dashboard needed):

```bash
# Status (default action), open PRs and one polling pass
hermes superagent github
hermes superagent github prs
hermes superagent github poll

# Validate a token/repository pair without saving anything
hermes superagent github test --repo owner/name --token <token>

# Connect (saves repo/token/login and optional watch settings)
hermes superagent github connect --repo owner/name --token <token> \
  --allowed alice,bob --watch-mode observe

# Start work on a PR (blocking turn; prints the session id and transcript)
hermes superagent github work 7 --request "add a regression test"

# Forget the repository and token
hermes superagent github disconnect
```

`--json` works on every action. Exit codes are `0` (ok), `1` (runtime or configuration error) and `2` (usage: unknown action, missing/invalid `--repo`, bad `--watch-mode`, missing PR number). The token is written to `settings.json` and never echoed.

**Settings precedence.** `DEFAULTS` (matching `plugin.yaml`) < `plugins.entries.superagent.settings` in `config.yaml` < `<HERMES_HOME>/plugin-data/superagent/settings.json` (what the connect form writes) < environment variables. A token that is empty or a `${...}` placeholder falls back to `SUPERAGENT_GITHUB_TOKEN`. `settings.json` is written atomically and chmod `600` where the platform supports it; the token is never returned by an endpoint (`token_set: true` says whether one exists).

**Watch semantics.**

- The **first successful poll is a baseline**: every existing comment and PR head is recorded as seen without creating a session, so connecting a busy repository does not replay its history.
- Dedupe lives in the `gh_seen(key, seen_at)` table, so restarts do not duplicate work. Every unseen comment is marked seen before it is judged; a malformed comment cannot replay forever.
- Bots (`*[bot]`, `github-actions`, `dependabot`) and the agent's own comments are ignored. `github_allowed_users` is an allow-list; empty means **nobody**.
- `github_watch_mode: observe` records sessions as `observed` and launches nothing. A human promotes one later (the session is a normal chat session with its PR prompt and checkout path).
- `github_watch_mode: auto` creates the session as `queued` and launches it. With `repo_path` configured, the PR is fetched into that clone (`+refs/pull/<n>/head:refs/superagent/pr-<n>`) and checked out as a detached worktree at `<data_dir>/workspaces/pr-<n>`, so the user's checkout and branch are untouched. If the checkout fails the session launches in `repo_path` with a `github` warning event; with no usable repository the session is marked `failed`.

---

### 10. Settings, presets and first-run onboarding

A new user should be able to choose models and get started without reading the docs.

**Header gear.** The ⚙ button in the Superagent header toggles a full-width **Settings** panel above the chat workspace (the same collapsible pattern as the GitHub panel). It edits:

- **Default model** — the same picker as the composer, plus **Inherit** to clear the pin (the provider is shown, or `auto (chosen by Hermes)`).
- **Worker model** — a separate picker and **Worker thinking** select for spawned workers, plus **Inherit router** to clear all three fields. Empty fields mean a worker follows its router session; a per-call `superagent_spawn` model/provider/reasoning_effort override still wins. This is the control to use when workers should be cheaper or faster than the router.
- **Worker harness** — a `mini` / `hermes` select. `mini` (the default) gives spawned workers the bash-only mini-swe-agent style harness: one terminal command per step and an explicit completion marker. `hermes` gives them the full file + terminal toolset. The help line under the picker explains the trade-off; roots always keep the full Hermes toolset.
- **Model presets** — rows of `name`, `model`, `provider` with a remove button and **+ Add preset** (12 max); the hint is *"Presets appear in the composer; pick one before starting a chat."*
- **Max parallel workers** (`1–8`), **Max turns per worker** (`1–500`) and **Run workers unattended (`--yolo`)** with help text.
- **Allow contributor (data-training) models in workers** — a checkbox that writes `security.allow_data_training_tiers_noninteractive: true` to `config.yaml` **immediately** (not with Save), because it is a host setting Hermes reads, not a Superagent setting. The helper line states the trade-off: contributor tiers let the vendor train on worker prompts, and a headless worker is refused without the opt-in. The control is hidden on a gateway that does not report `data_training_tiers_allowed`.
- **Save** posts `POST /settings` and invalidates the settings, models and chats queries so the chips and every new session agree; **Refresh** re-reads and **Cancel** closes.

**Composer preset chips.** Before a chat starts, one chip per preset sits above the textarea. Clicking a chip fills the model/provider and highlights it; the normal ModelPicker stays available for an ad-hoc choice (which clears the chip). The selected name is sent as `preset` with `POST /chats`, together with the resolved `model`/`provider`. With **no presets configured the chip row is not rendered at all** — there is no built-in fallback list, because a chip for a model you have no credentials for can only start a failed worker. The picker and the default model still work.

**First-run onboarding.** While there are no chat sessions yet, a compact **Get started** card lists three numbered steps: *Pick a model / preset* (opens Settings), *Connect a GitHub repo to watch PRs* (opens the GitHub panel; the hint states that an empty allow-list means nobody can trigger work and the agent ignores its own comments), and *Start a chat below*. Dismissing it writes `onboarded` through `ctx.storage`, so it does not come back.

**GitHub panel.** The allow-list field now carries the helper copy *"Only these users can trigger work; the agent ignores its own comments — add a teammate or a second account."*

**CLI.** `hermes superagent settings [--json]` prints the merged view (token masked to `set`/`not set`), including `data-tier models: allowed for workers` or the exact `hermes config set …` command that would allow them; `hermes superagent chat --preset <name>` resolves the same preset catalog and exits `2` with the available names when the preset is unknown. `hermes superagent models` prints `name -> provider: model` for every preset.

---

### 11. Review a finished session (diff, PR comment, PR review & opt-in PR creation)

By default, closing the loop does not need a push: a finished session leaves its changes in the working tree (or its isolated worker worktree); the plugin can show them and post a comment on the pull request. When you explicitly enable `allow_worker_pr_create` or `allow_router_pr_approve` in **⚙ Settings**, you and your agents can also create feature-branch PRs and submit formal PR reviews (`APPROVE` / `REQUEST_CHANGES`).

**Diff button (chat header).** Visible whenever the session has a repository. The diff is *lazy*: nothing is fetched until the panel is open, and it is refetched every 4 s only while the turn is `queued`/`running` (a finished session stays still until **Refresh**). The collapsible panel sits between the transcript and the composer (`max-h-22rem`, scrolls) and shows:

- the file count, the repository and a *patch truncated* note when the cap hit;
- the `git diff --stat` block (plus a `staged:` block when the index has changes);
- one row per file with its porcelain letter (`M`, `A`, `D`, `R`, `??`, `!`); past 200 files it says how many were not listed;
- **one collapsible section per file**: the path, its `+n/-n` counts, and the file's own patch. Clicking a file row jumps to its section; **Copy** copies that path. **Copy patch** copies the whole patch, and a pathless preamble (truncation note, binary marker) still renders rather than disappearing.

**Create PR button.** Visible when the session has a repository and no `pr_number` is linked yet. Opens an inline `CreatePrBox` with editable PR title, branch name (`superagent/<session_id>-...`), base branch, draft checkbox, and body. Submitting calls `POST /chats/{id}/pr` (requires `allow_worker_pr_create=true`).

**Comment / Review on PR button.** Visible when the session carries a PR number *and* a GitHub token is saved. It opens an inline confirm — never `window.confirm` — with a textarea prefilled from the preview endpoint, review verdict radios (**Comment**, **Approve ✅**, **Request changes ❌**), **Post** and **Cancel**. Selecting **Approve** or **Request changes** calls `POST /chats/{id}/review` (requires `allow_router_pr_approve=true`).

| Method | Path | Behaviour |
| :--- | :--- | :--- |
| `GET` | `/chats/{id}/diff` | `{ok, repo, files: [{status, path}], stat, patch, truncated, error}`. `404` unknown session; `400` when the session has no repository or it no longer exists / is not a git checkout. The patch is capped at 60 000 characters and ends with `… diff truncated …`. |
| `GET` | `/chats/{id}/comment-preview` | `{ok, session_id, pr_number, github_repo, url, body, length}` — exactly what a post would send, with no network call and no token requirement. `400` when the session has no PR. |
| `GET` | `/chats/{id}/export` | `{ok, session_id, filename, markdown, length}` — the transcript as a self-contained Markdown document (facts, prompt, messages, activity), rendered from one stored read. Capped at 400 000 characters with a visible truncation note. `404` unknown. |
| `POST` | `/chats/{id}/comment` | Post `{body?}` (default: the generated summary). `400` without a PR, a repository, a saved token or a readable repo; `502` when GitHub refuses (message redacted). Records a `commented` event with the comment URL and returns `{ok, url, pr_number, body_preview, session}`. |
| `POST` | `/chats/{id}/pr` | Commit worktree changes, push a `superagent/<session_id>` branch, and open/update a GitHub PR (`{title, body?, branch?, base?, draft?, commit_message?}`). Requires `allow_worker_pr_create=true`. |
| `POST` | `/chats/{id}/review` | Submit a GitHub PR review (`{event: "COMMENT"\|"APPROVE"\|"REQUEST_CHANGES", body?, pr_number?}`). `APPROVE`/`REQUEST_CHANGES` require `allow_router_pr_approve=true`. |

The generated body is `Superagent finished <id> — <title>`, the last assistant message, `Files changed: N (+X/-Y)` from the diff stat, and `_Generated by Superagent._`; missing pieces are omitted and the whole thing is clipped to 20 000 characters (the assistant summary shrinks first).

**CLI.** Both commands read the chat tables directly, so they work without a dashboard or a ticket engine:

```bash
hermes superagent diff S-0001                 # stat, file list, patch (exit 1: unknown/no repo)
hermes superagent diff S-0001 --json          # {ok, session_id, diff}
hermes superagent comment S-0001              # generated summary → the PR, prints the URL
hermes superagent comment S-0001 --body "LGTM"        # your text instead
hermes superagent comment S-0001 --body-file review.md
```

`comment` exits `2` for usage (both body sources, unreadable/empty file, over 20 000 chars), `1` for an unknown session, a session without a PR, no repo/token or a GitHub failure, and `0` with the comment URL on success.

**Guardrails.** `collect_diff` runs git as an argv list with no shell and never raises: a missing directory, a plain directory, a missing `git` binary and a timeout are all `{ok: false, error}` with stderr redacted. The repository path is resolved through the same containment check as a worker turn, so a session cannot point the diff at an arbitrary directory. By default (`allow_worker_pr_create=false`, `allow_router_pr_approve=false`), no commit, push, merge or PR creation can happen anywhere in this flow; when `allow_worker_pr_create` is enabled, pushes use a one-shot `http.extraHeader` credential and reject protected branch names (`main`, `master`, `production`, `release`).

---

## Known limitations

- **Workers never commit, push, or open a PR unless `allow_worker_pr_create` is explicitly enabled.** By default they edit the working tree (or isolated worker worktree) and the session ends in `review`; publishing is an explicit opt-in decision.
- **`mini` is the mini-swe-agent architecture, not the `mini-swe-agent` package.** There is no extra Python dependency, `mini` binary or model client: Hermes still owns the model loop, and the worker's step cap is the ordinary `chat_max_turns` / `--max-turns` limit. The toolset guarantees bash is the only action tool; the "exactly one command per step" rule is enforced by the prompt, and the completion marker is a convention recorded as a `submitted` event, not a hard process-level submit.
- **Contributor-tier models need a host opt-in.** Meta's `…-contributor` (data-training) ids are refused by Hermes in any unattended run unless `config.yaml` has `security.allow_data_training_tiers_noninteractive: true`. Superagent reports that state in `hermes superagent settings` and the panel, refuses the model early with the reason, and can flip the key for you — it does not silently enable a tier that lets the vendor train on your prompts. The opt-in is global to Hermes, not per-Superagent.
- **The GitHub allow-list is the security boundary.** Empty `github_allowed_users` means **nobody** can trigger work (both for `GitHubWatcher` and `GitHubSource`), and the agent ignores comments from its own login — testing with the account that owns the token produces no sessions.
- **GitHub self-approval constraint.** GitHub's REST API rejects `APPROVE` on a PR authored by the same GitHub account that owns the token (`422 Unprocessable Entity`). `GitHubClient.create_review` automatically falls back to a formal `COMMENT` review prefixed with `✅ **Approved by Superagent**` when this happens.
- **One watched repository and one token per install.** Polling covers open PRs only, capped by `github_pr_limit`.
- **Backend edits need `hermes gateway restart`.** The desktop **Reload desktop plugins** action only reloads `desktop/plugin.js`.
- **`max_workers` is an in-process semaphore.** One gateway process is assumed; two gateways against the same database would each allow their own workers.
- **Ticket workers still need an agent turn.** CLI, REST and slash commands only queue; `superagent_tick` / `superagent_run` in a chat or the **Superagent watch** cron launch them.
- **The chat UI is poll-based**, not token-streaming, and a transcript is read back capped at 200 messages and 50 events per request.
- **The town art is third-party.** Buildings/trees/props/terrain are MIT-licensed derivatives of Hermes Town; the grass is CC-BY-SA 3.0, the person is CC0, and the bundled Petdex spritesheets in `assets/town/pets/` are credited to their Petdex community authors. See `assets/town/ATTRIBUTION.md`; the repository's root `LICENSE` covers only the plugin source.
- **PR and worker worktrees accumulate** under `<data_dir>/workspaces/pr-<n>` and `<data_dir>/workspaces/worker-<id>`; nothing prunes them automatically.
- **Windows UTF-8.** Persisted text is UTF-8 and subprocess/git output is decoded with replacement, so a tool emitting a legacy OEM code page can leave `�` in captured output.

---

## Testing

Run the test suite from the repository root:

```bash
pytest -q
```

The suite is hermetic (604 tests): `tests/test_runner.py` and `tests/test_hardening.py` replace `Popen` with a fake, so no real `hermes` process is spawned. `tests/test_hardening.py` covers repo containment (missing/file/dot-dot/symlink escape), secret redaction, the 20k/2k caps, every `POST /ask` triage branch, and the runaway watchdog. `tests/test_towns.py` covers the parent/worker migration, randomised and deterministic slot allocation, parent rejection, orphan/archive behaviour, real spawning and the 26-file town asset allowlist; `tests/test_petdex_assets.py` covers the bundled Petdex catalog, 4× Pillow downsampling (`1536×1872 → 384×468`), domain allowlist enforcement, offline fallback and `/town/pets` + `/town/pets/reroll`; `tests/test_reasoning.py` covers the thinking ladder from storage to `--reasoning`; `tests/test_towns_render.py` runs the Node render probe (`tests/towns_render_probe.mjs`) for the town scene, cobblestone roads/plaza, Petdex sprites, waypoint simulation, speech bubbles, switcher, house dialog, settings drawer and one-shot construction. The GitHub and tool tests (`tests/test_github.py`, `tests/test_tools.py`, `tests/test_watcher.py`, `tests/test_github_api.py`, `tests/test_diff.py`, `tests/test_handoff_api.py`, `tests/test_cli_handoff.py`) cover worktree creation, `commit_and_push_session`, `create_pr`, `create_review` (including 422 self-approval fallback), and the Router/Worker coordination tools (`superagent_town_status`, `superagent_message_worker`, `superagent_pr_create`, `superagent_pr_review`) completely offline.

Validate the plugin manifest and its registrations before shipping:

```bash
hermes plugins doctor --ci .
hermes plugins validate .
```
