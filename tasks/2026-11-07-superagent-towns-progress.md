# Superagent Towns — progress and next steps

**Date:** 2026-11-08
**Status:** the Living Towns & Router↔Worker PR Workflows evolution (`IMPROVEMENTS.md`) is merged on `main` at **0.6.0**. Source, tests (`538 passed`), docs, 26 town environment PNGs, 8 bundled Petdex spritesheets, autonomous waypoint simulation with speech bubbles, per-worker git worktree isolation, and opt-in PR creation/review workflows are complete. The installed desktop pass only needs an external `hermes gateway restart` + **Reload desktop plugins**.

## Landed on `main`

| PR / Commit | What it did |
| :--- | :--- |
| #24 | Preserved the pin/unpin work and recorded the task brief. |
| #25 | Vendored the first eight sprites under `assets/town/` with per-file provenance and licences. |
| #26 | Additive `parent_session_id` + `town_slot`, migration, `GET /towns`, one-level validation, shared `sa_runtime` Runner, `superagent_spawn`, router prompt/toolset. |
| #27 | Desktop town scene, Router/worker houses, chat dialog, `GET /town/assets`, opaque Settings drawer and labelled header button. |
| #28 | Replaced the coarse crops with MIT Hermes Town buildings/trees/props; root MIT `LICENSE`; rewritten `ATTRIBUTION.md`; reproducible extraction script. |
| #29 | Thinking levels through the whole stack (`reasoning_effort` column, settings, API, runner `--reasoning`, model picker, Settings selector above high). |
| #30 | Fixed 12-plot grid, opaque empty state/dialogs, 992×460 layout proof, README/DESIGN/operator docs, `GET /models` default effort. |
| #31 | Sprite-only scaling so labels/badges stay readable; empty-baseline construction fix; composer focus + Tab trap; stale-gateway banner; version `0.5.2`. |
| #32 | Lowest-free-slot allocation, legacy slot clamping, archived-aware orphan count; version `0.5.3`. |
| #33 | Worker dialog “↗ Router”, readable town header, `/towns` 404 explanation; version `0.5.4`. |
| #34 | CLI `chat --reasoning none…ultra`, stored/printed level, `models_payload` default effort; version `0.5.5`. |
| #35 | Solid colour fallbacks (`--card` → `--background` → neutral), parent-based Router/Worker labels, owner-only Router jump, all-12 slot test; version `0.5.6`. |
| #36 | Handover updated to the merged 0.5.6 state. |
| #37 | CLI `models`/`settings` print the resolved default thinking level; version `0.5.7`. |
| #38 | Handover updated to 0.5.7. |
| #39 | Thinking survives model/preset changes; switcher shows `N/12 plots`; README `/health` and `town_count`; version `0.5.8`. |
| #40 | Fallback model picker exposes a Thinking level select; version `0.5.9`. |
| #41 | Saving Settings resets the remembered new-town picker to the saved defaults. |
| #42 | Root/worker toolset + default-level tests; town shortcut sheet; version `0.5.10`. |
| `ebeed22` (`0.6.0`) | Randomised slot spawning (`secrets.choice`), Petdex character proxy & 8 bundled `384×468` WebP sheets (`dashboard/petdex_assets.py`, `GET /town/pets`, `POST /town/pets/reroll`), 26 town PNGs (7 worker houses, cobblestone roads, stone plaza, water stream, bridge, well, benches, flowers, signpost), `useTownSimulation` waypoint movement + `TownSpeechBubble` telemetry, isolated worker git worktrees (`prepare_worker_worktree`), Router↔Worker tools (`superagent_town_status`, `superagent_message_worker`, `superagent_pr_create`, `superagent_pr_review`), UI PR creation & review verdicts, and bugfixes (`GitHubSource` fail-closed, `townNavRef`, `registerHouse` ref cache, `reasoning_effort` propagation). |

## What is implemented

### Backend

- `chat_sessions.parent_session_id` is NULL for a root town and a root id for
  one worker. `town_slot` is allocated inside the insert transaction by picking
  uniformly at random from the available free slots (`0…11`) via `secrets.choice`
  (or lowest free slot when `randomize_slot=False`), and is clamped to `0…11`
  on read. The hierarchy is one level; workers cannot spawn workers.
- `chat_sessions` also stores `avatar_slug`, `branch_name`, and `worktree_path`
  (additive migration in `ChatStore._migrate`).
- `GET /towns` groups roots with workers, attaches `message_count` and
  `recent_events` for live telemetry bubbles, and reports status counts, a
  separate `orphaned` list/count and `archived_count`. `/chats` stays flat and
  compatible. A missing root leaves an orphan, never a second town.
- `dashboard/petdex_assets.py` serves `GET /town/pets?slugs=...` and
  `POST /town/pets/reroll`, downsampling `petdex.dev` `1536×1872` sheets 4× to
  `384×468` WebP (`48×52` per cell) with disk caching and an 8-pet bundled
  offline pool (`assets/town/pets/`).
- `superagent_spawn` creates a real child chat, provisions an isolated git
  worktree under `<data_dir>/workspaces/worker-<id>` when
  `allow_worker_git_worktree=True` (or checks out `pr_number` when supplied),
  and launches it through the shared process-wide `Runner`/semaphore.
- Four new coordination & PR tools (`sa_schemas.py`, `sa_tools.py`,
  `__init__.py`): `superagent_town_status`, `superagent_message_worker`,
  `superagent_pr_create` (gated by `allow_worker_pr_create`), and
  `superagent_pr_review` (gated by `allow_router_pr_approve`, with automatic
  HTTP 422 self-approval fallback).
- REST endpoints `POST /chats/{id}/pr`, `POST /chats/{id}/review`, and
  `POST /towns/{root_id}/dispatch` drive the same workflows from the desktop UI.
- `default_reasoning_effort`, per-session `reasoning_effort`, API validation,
  worker inheritance and `--reasoning` argv are wired end to end (`none…ultra`),
  including replacement sessions and GitHub PR work sessions.
- `/health` exposes API v3 with features `towns` and `thinking`; `/models`
  exposes `default_reasoning_effort`; `/town/assets` serves the 26-file
  allowlist through data URLs and bytes.

### Desktop

- The page body is the selected town: central Router Town Hall on a paved
  cobblestone square (`plaza-stone`), tileable East–West and North–South
  cobblestone roads (`road-h`, `road-v`) with front walkways to all 12 plots,
  7 worker house archetypes (`house-a` through `house-g`: cottage, tavern,
  workshop, forge, library, post office, observatory), an artisan stream
  (`water`) with a wooden footbridge (`bridge`), fountain, wishing well (`well`),
  noticeboard (`signpost`), benches (`bench`), wildflowers (`flowers`), and warm
  streetlamp glows when workers are running.
- Characters are rendered in a depth-sorted `TownActor` layer driven by
  `useTownSimulation` (`requestAnimationFrame` throttled to 25 fps, skipped
  under `prefers-reduced-motion`). Inhabitants walk between their home doorstep,
  the central plaza, fountain, noticeboard and Router Hall according to session
  status, play all 9 Petdex animation rows (`PetdexSprite`), wave to nearby
  neighbours, and display clickable `TownSpeechBubble` telemetry pills.
- Construction is a one-shot event keyed to a persisted known-worker baseline;
  a town first observed empty still animates its first worker. Reduced motion
  skips both animations.
- Town switcher: select, previous/next, **🎲 Reroll sprites**, **New town**,
  **Sessions**. Arrow keys switch via `townNavRef` only when focus is not in a
  field; the selected id persists.
- House dialog reuses `ChatHeader`/`Transcript`/`Composer`, adding **Create PR**
  (`CreatePrBox`), review verdict radios (**Comment**, **Approve ✅**,
  **Request changes ❌**) inside `CommentBox`, and Router→Worker dispatch actions
  in the `⋯` menu.
- Assets are 26 vendored town PNGs + 8 bundled Petdex WebP sheets. See
  `assets/town/ATTRIBUTION.md`.

### Tests and tooling

```text
py -m pytest -q                             538 passed
node --check desktop/plugin.js              ok
node tests/towns_render_probe.mjs           town render probe: ok
hermes plugins validate .                   Validation passed.
hermes plugins doctor --ci .                9 tools, 0 hooks
```

The 992×460 DOM probe with 12 houses reports **zero overlaps and zero
clipping**, and verifies the cobblestone roads, Petdex sprite rows, speech
bubbles, reroll button, and PR creation/review controls. The installed plugin is
a directory link at
`~/.hermes/plugins/superagent -> C:\Users\hongy\Desktop\hermes-superagent`, and
`hermes plugins show superagent` reports **0.6.0**.

## Not verified / blockers

1. **Installed desktop UI.** `dashboard/plugin_api.py` is loaded when the
   gateway boots. Restart the gateway from an **outside shell** (`hermes gateway
   restart` — never run this from inside the session), then run **Reload desktop
   plugins** in the app so the new `/town/pets`, `/chats/{id}/pr`,
   `/chats/{id}/review`, and `/towns/{id}/dispatch` routes and `desktop/plugin.js`
   are active.
2. **Live Router↔Worker & PR pass.** Create a town, prompt the Router to spawn
   two workers, watch their Petdex sprites walk along the roads and show tool
   speech bubbles, then test **🎲 Reroll sprites**, `superagent_town_status`,
   `superagent_message_worker`, and (after enabling the toggles in **⚙
   Settings**) **Create PR** and **Approve ✅**.

## Resuming

```bash
cd C:/Users/hongy/Desktop/hermes-superagent
git status --short          # should be clean on main
git log --oneline -5
# outside this chat:
hermes gateway restart
# in the app: Reload desktop plugins
```

The original task brief is in `tasks/2026-11-07-superagent-towns-brief.txt` and
the full v0.6.0 architecture & implementation specification is in
`IMPROVEMENTS.md`.
