---
name: superagent-operator
description: Operate Superagent chats, workers and GitHub watch.
version: 0.6.2
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [superagent, operator, github, chat, workers]
---

# Superagent Operator Guide

Use this guide when operating, triaging, or tuning the `superagent` plugin in Hermes.

`superagent` has two execution paths that share one SQLite database and one
attention budget:

- **Chat sessions** — the desktop **Superagent** page draws a pixel-art town: one root
  chat is the Router Town Hall, every worker it spawns is a house (`house-a` through `house-g`) with its own
  Petdex character sprite (`GET /town/pets`), speech bubble and transcript. The page starts one headless `hermes chat` process per turn; you
  watch, resume, stop, or open any house's dialog.
- **Tickets** — a deterministic, zero-token router turns accepted
  requests into tickets; worker subagents run inside a Hermes agent turn.

By default, neither path commits or pushes for you: both end in `review` with the changes
uncommitted in the working tree (or isolated worker worktree). Opt-in settings (`allow_worker_pr_create`, `allow_router_pr_approve`) let workers/Routers open PRs and submit PR reviews when explicitly enabled.

**Take as little human attention as possible, deliver as much verified value as
possible.**

---

## 1. Surfaces at a Glance

| Surface | What it is for |
| :--- | :--- |
| Desktop **Superagent** page (`/superagent`) | Town scene (Router + worker houses, wandering Petdex sprites, speech bubbles, HUD filter pills, pause toggle, **🎲 Reroll sprites**, **↻ Retry failed**), house chat dialog, town switcher, Sessions drawer, Settings, GitHub, diff/comment/PR create/review. |
| Status-bar chip + side pane | `▶ N` running chats and `◆ M` chats in `review`/`failed`; ticket attention only as a fallback. |
| Agent tools | Ticket tools (`superagent_ask`, `superagent_tick`, `superagent_status`, `superagent_run`) and Town/PR tools (`superagent_spawn`, `superagent_town_status`, `superagent_message_worker`, `superagent_pr_create`, `superagent_pr_review`). |
| CLI | `hermes superagent …` for status, chat, sessions, presets, GitHub, diff, comment and worktree cleanup (`hermes superagent cleanup`). |
| Slash command | `/superagent status\|ask\|tick\|run\|help`. |
| Cron blueprint | **Superagent watch** every 5 minutes; runs `superagent_tick(wait=true)` as a real agent turn, so it may launch ticket workers. |

---

## 2. Start Narrow (`observe` First)

Never enable unattended execution on a repository until you have watched the
router's real traffic.

1. **Point at one repository clone:**
   ```bash
   hermes config set plugins.entries.superagent.settings.repo_path "/absolute/path/to/repo"
   ```
2. **Stay in `observe` for tickets and PRs:**
   ```bash
   hermes config set plugins.entries.superagent.settings.mode observe
   hermes config set plugins.entries.superagent.settings.github_watch_mode observe
   ```
   `observe` records `observed` sessions/tickets and launches nothing.
3. **Keep the GitHub allow-list small and explicit.** Set `github_repo`
   (`owner/name`), `github_agent_login`, and `github_allowed_users` to yourself
   or a small trusted group. An empty allow-list means **nobody** can trigger
   work, and the agent ignores comments authored by its own login — add a
   teammate or a second account if you want comment-driven work.
4. **Promote one at a time.** In `observe`, use `superagent_run(ticket_id=…)` or
   **Work on this PR** to launch a single item before switching to `auto`.

---

## 3. Run a Chat Town

The chat path is the normal way to ask for work; it needs no ticket and no
GitHub connection.

1. Open the **Superagent** page. A town starts with one root **Router** chat;
   **New town** opens the composer for it.
2. Pick a model and thinking level (or a preset chip), optionally set a repo
   path, and describe the work. The first turn creates `S-0001` and launches a
   headless worker.
3. When the Router needs parallel work it calls `superagent_spawn` with
   `parent_session_id=S-0001`; each child gets one house (`house-a` through `house-g`) at a random free plot (`0…11`), a Petdex character sprite, and (when `allow_worker_git_worktree=true`) an isolated git worktree under `<data_dir>/workspaces/worker-<id>`. Ask for
   the split explicitly if the Router does not do it, e.g. "spawn two workers:
   one fixes the parser, one adds a test".
4. The Router can inspect its workers via `superagent_town_status` and send
   follow-up instructions or PR tasks to an existing worker via
   `superagent_message_worker` (`action="follow_up"|"create_pr"|"review_pr"|"approve_pr"`).
5. Click the Router or any worker house (or its wandering Petdex character / speech bubble) to open its transcript/composer dialog.
   **Stop** cancels that session; a follow-up resumes its Hermes session. The
   dialog has **Retry** after `failed`/`cancelled`, model/thinking changes, **Diff**,
   **Create PR**, **Comment / Approve PR**, export, and Router dispatch actions. Closing it never stops the worker.
6. Switch towns with the town picker (**Select town** button → `Towns (N)` panel of roots with status and worker count, never worker sessions) or left/right arrows; click **🎲 Reroll sprites** (`POST /town/pets/reroll`) to pick fresh Petdex characters for the town. Switching only changes
   the view, never a session. **Sessions** has search, rename, pin, archive and
   delete for rows that are archived or orphaned.
7. A turn that ends in `review` or `failed` raises one desktop toast
   (`<title> finished` / `<title> failed`) while the page is open. Opening the
   page does not replay old completions, and `cancelled` is silent.

**From the terminal:**

```bash
hermes superagent chat "Add a regression test for the parser" --preset fast
hermes superagent chat "Try the same fix again" --detach
hermes superagent sessions
hermes superagent show S-0001
hermes superagent stop S-0001
```

**Non-negotiables:**

- The worker is bounded by `chat_max_turns`, the repo containment check and the
  runaway guard (`turn_timeout_seconds`, default 1800 s). A timed-out turn is
  cancelled with a reason that names the phase: `turn timed out after 1900s
  while queued (limit 1800s)` or `... while running ...`.
- `worker_yolo` only skips approval prompts inside the turn. Commit/PR creation and PR review approval remain gated behind `allow_worker_pr_create` and `allow_router_pr_approve` (both `false` by default).
- A failed session shows its stored `error` in the transcript header and on its
  worker card, so you do not have to open a log to see why it stopped.

---

## 4. Models and Presets

- **Default model** (`default_model` / `default_provider`) applies to new chats
  when a request or preset does not pick one. Empty means the CLI chooses.
- **Thinking level** (`default_reasoning_effort`) applies to new chats and is
  inherited by spawned workers. The ladder is `none`, `minimal`, `low`,
  `medium`, `high`, `xhigh`, `max`, `ultra`; `xhigh`/`max`/`ultra` are above
  high. Empty means the model/CLI default. The model picker edits the same
  level, and the Runner passes it as `--reasoning`.
- **Presets** are named `(model, provider)` pairs. Accepted spellings are
  `name=provider:model`, `strong=model`, plain `model`, or
  `{name, model, provider}` objects. At most 12; names unique; every preset
  needs a model.
- **No presets means no chips.** The composer hides the preset row and the
  model picker remains. There is no hardcoded fallback catalogue — never
  advertise a model id the machine may have no credentials for.
- `GET /models` returns `presets` plus a flat `models` list: preset models in
  order, then the default model. `hermes superagent models` prints the same
  view; `hermes superagent settings` prints the merged settings.

```bash
hermes config set plugins.entries.superagent.settings.model_presets \
  '["fast=opencode-go:qwen/qwen3.8-27b", "strong=z-ai/glm-5.3-flash"]'
hermes superagent models
```

The ⚙ Settings panel edits the same values and invalidates the models/chats
queries on save, so chips and new sessions agree immediately.

### Contributor (data-training) tiers

A model id carrying a `contributor` dash-token (Meta's `…-contributor` ids)
lets the vendor train on prompts. Hermes refuses that `-m` override in **every
unattended run** — which is what every worker is — unless `config.yaml` sets:

```yaml
security:
  allow_data_training_tiers_noninteractive: true
```

- Superagent refuses the model **before** creating a session, in all four
  places (desktop/API, `superagent_spawn`, CLI chat, and the runner backstop for
  retry/resume), and the message names the key, the command and the trade-off.
- Turn it on from ⚙ Settings → **Allow contributor (data-training) models in
  workers** (saved immediately, not with Save), or from a terminal:
  `hermes config set security.allow_data_training_tiers_noninteractive true`.
- `hermes superagent settings` shows `data-tier models: allowed for workers` or
  `blocked (enable with: …)`; `GET /settings` reports
  `data_training_tiers_allowed`.
- The opt-in is **global to Hermes**, not per-Superagent: it applies to every
  non-interactive run on the machine.

---

## 5. GitHub Connect & PR Watch

Header → **GitHub**, or the CLI:

```bash
hermes superagent github status
hermes superagent github test --repo owner/name --token <token>
hermes superagent github connect --repo owner/name --token <token> --allowed alice,bob
hermes superagent github prs
hermes superagent github poll
hermes superagent github work 7 --request "add a regression test"
hermes superagent github disconnect
```

**What triggers work.** A PR comment that mentions the agent (`@<login>`,
`@superagent`, `/superagent`, or a line starting `superagent:`) from an allowed
user. With `github_watch_new_prs` on, a new head commit starts one review
session per SHA.

**observe vs auto.**

- `observe` (default) records the request as an `observed` session and launches
  nothing. A human promotes it or uses **Work on this PR**.
- `auto` creates the session as `queued` and launches it. With `repo_path` set,
  the PR is fetched and checked out as a detached `git worktree` under
  `<data_dir>/workspaces/pr-<n>`, leaving your clone and branch untouched. A
  checkout failure falls back to `repo_path` with a warning event; no usable
  repository marks the session `failed`.

**Rules that prevent noise.**

- The first successful poll is a **baseline**: existing comments and PR heads
  are recorded as seen, so connecting a busy repository does not replay history.
- Dedupe lives in `gh_seen`, so restarts do not duplicate work.
- Bots and the agent's own comments are ignored. Empty allow-list = nobody.
- Poll interval is `github_poll_seconds` (minimum 5, default 90; the desktop/API
  form accepts 15–3600); each poll is capped by `github_pr_limit`.

---

## 6. Review Handoff: Diff, Comment, and Opt-In PR Workflows

By default, a finished session leaves the work uncommitted in its repository or isolated worktree. Review it before anything leaves your machine.

1. Select the session → **Diff** in the chat header (`GET
   /chats/{id}/diff`). It is lazy, refetches only while the turn runs, and shows
   `git status` letters, `git diff --stat` and the capped patch. The CLI
   equivalent is `hermes superagent diff S-0001`.
2. Read the transcript's final summary and run the tests yourself.
3. If the session has no PR yet and you enabled `allow_worker_pr_create`, click **Create PR** (`POST /chats/{id}/pr`) or let the worker/Router call `superagent_pr_create` to commit, push a `superagent/<session_id>` branch, and open a PR.
4. If the session has a PR, **Comment on PR** opens an editable preview
   (`GET /chats/{id}/comment-preview`) with review verdict radios (**Comment**, **Approve ✅**, **Request changes ❌**). `APPROVE` and `REQUEST_CHANGES` call `POST /chats/{id}/review` (or `superagent_pr_review`, gated by `allow_router_pr_approve`). Nothing is posted until you press **Post**; `hermes superagent comment S-0001` does the same from a terminal.

`diff` exits `1` for an unknown session or one without a repository. `comment`
exits `1` without a PR, repo or saved token, and `2` for bad usage.

---

## 7. Tickets (still supported)

### Daily triage inside a Hermes chat turn

1. `superagent_status()` — focus on the `attention` list (`observed`, `blocked`,
   `review`, `failed`).
2. `superagent_status(ticket_id="T-0001")` — full request, verdict, result and
   event log.
3. `superagent_run(ticket_id="T-0001", wait=True)` — promote an `observed`,
   `blocked` or `failed` ticket and launch it now.
4. `superagent_tick(wait=True)` — poll GitHub (if configured), launch queued
   workers in `auto` mode, reap results and cancel timed-out workers.
5. `superagent_ask(request="Fix XYZ and add a unit test")` — queue new work.

### Tuning for less noise

- Keep `max_workers` at `1` or `2`. Ticket workers edit the same working tree;
  more parallelism means overlapping edits unless each has its own checkout.
- Use `[skip-agent]`, `[no-agent]`, `@agent ignore` or `/noagent` when you
  mention the agent in a discussion that should not create a ticket.
- Leave `allow_proactive: false` on general repositories; enable it only on
  dedicated triage repositories.
- Design questions (`"should we…"`, `"which approach…"`) stop in `blocked`.
  Decide, then submit a concrete ask or promote the ticket.

### The launch constraint

Ticket workers can **only** launch from an active Hermes agent turn. If
`hermes superagent tick` or `/superagent tick` prints
`note: workers are launched only from a Hermes agent turn (chat or cron)`, that
is expected. Use the `superagent_tick`/`superagent_run` tools in a chat, or
enable the **Superagent watch** cron blueprint (every 5 minutes) so a real agent
turn does the launching.

### Inspect and queue from the CLI

```bash
hermes superagent status
hermes superagent status --json
hermes superagent ask "Add validation for empty payloads"
hermes superagent tick
hermes superagent run T-0001
hermes superagent show T-0001
hermes superagent doctor
```

`run` promotes to `queued`; the worker starts on the next agent turn. Every
subcommand accepts `--json`. Exit codes: `0` ok, `1` runtime/configuration
error, `2` usage.

---

## 8. Configuration Cheat Sheet

| Setting | Default | Effect |
| :--- | :--- | :--- |
| `mode` | `observe` | `off` / `observe` / `auto` for ticket work. |
| `repo_path` | `""` | Repository every worker runs inside. |
| `default_model` / `default_provider` | `""` | New-chat default; empty = CLI choice. |
| `default_reasoning_effort` | `""` | New-chat thinking level (`none…ultra`); empty = model/CLI default. |
| `model_presets` | `[]` | Composer chips; empty = no chips. |
| `max_workers` | `2` | Parallel chat/ticket workers. |
| `chat_max_turns` | `120` | Agent turns per headless chat worker. |
| `worker_yolo` | `true` | Pass `--yolo`; still no commits/pushes unless opted in. |
| `turn_timeout_seconds` | `1800` | Runaway guard; `0` disables. |
| `github_repo` | `""` | `owner/name` to watch. |
| `github_allowed_users` | `[]` | Who may trigger work; empty = nobody. |
| `github_watch_mode` | `observe` | `observe` or `auto` for PR mentions. |
| `github_watch_new_prs` | `false` | Review every new PR head commit. |
| `github_poll_seconds` | `90` | Background poll interval. |
| `allow_worker_git_worktree` | `true` | Isolate each spawned town worker into its own git worktree. |
| `allow_worker_pr_create` | `false` | Allow workers/Router to commit, push feature branches, and open GitHub PRs. |
| `allow_router_pr_approve` | `false` | Allow Router/workers to submit `APPROVE` / `REQUEST_CHANGES` PR reviews. |
| `github_pr_base_branch` | `""` | Default base branch for worker-created PRs (empty = repo `default_branch`). |
| `github_pr_draft_by_default` | `false` | Open worker-created PRs as drafts by default. |
| `petdex_enabled` | `true` | Fetch and cache random character sprites from `petdex.dev`. |

Settings layers, lowest to highest: `DEFAULTS` (mirrors `plugin.yaml`) <
`plugins.entries.superagent.settings` in `config.yaml` <
`<HERMES_HOME>/plugin-data/superagent/settings.json` (written by the desktop
panels) < environment variables (`SUPERAGENT_*`). The GitHub token is only ever
reported as `token_set: true`.

---

## 9. Verification and Troubleshooting

- **Plugin tab missing** — `hermes plugins enable superagent`, enable it under
  **Desktop → Settings → Plugins**, then **Reload desktop plugins**.
- **Backend error card** — the plugin is not enabled for the gateway; the card
  shows the error and a **Retry**.
- **No preset chips** — no `model_presets` are configured. That is intentional;
  use the model picker or add presets in ⚙ Settings.
- **A chat sits in `queued`** — waiting for a worker slot (`max_workers`) or for
  the next reap; a turn older than `turn_timeout_seconds` is cancelled with a
  `while queued` reason.
- **A contributor model is refused** — a headless worker needs the host opt-in
  (`security.allow_data_training_tiers_noninteractive: true`); the error names
  the key and the command, and ⚙ Settings has the checkbox.
- **A ticket never starts** — see the launch constraint above.
- **No toasts** — completion notifications need the page mounted and a host that
  exposes `host.notify`; the transcript still updates without them.
- **Validate the package** — `hermes plugins validate .` from the plugin root.
