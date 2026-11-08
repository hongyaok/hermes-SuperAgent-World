# Superagent Towns — progress and next steps

**Date:** 2026-11-08
**Status:** the vertical slice is merged on `main` at **0.5.7**. Source, tests,
docs and the 12-plot town layout are complete. The installed desktop pass is the
only acceptance item still blocked: the gateway hosting this chat has the old
route table, and restarting it from this session would terminate the session.

## Landed on `main`

| PR | What it did |
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

## What is implemented

### Backend

- `chat_sessions.parent_session_id` is NULL for a root town and a root id for
  one worker. `town_slot` is allocated inside the insert transaction, reuses
  the lowest free plot, and is clamped to `0…11` on read. The hierarchy is one
  level; workers cannot spawn workers.
- `GET /towns` groups roots with workers, reports status counts, a separate
  `orphaned` list/count and `archived_count`. `/chats` stays flat and
  compatible. A missing root leaves an orphan, never a second town.
- `superagent_spawn` creates a real child chat and launches it through the same
  process-wide `Runner`/semaphore. The child inherits the Router's model,
  provider, thinking level and repository.
- `default_reasoning_effort`, per-session `reasoning_effort`, API validation,
  worker inheritance and `--reasoning` argv are wired end to end. The accepted
  ladder is `none, minimal, low, medium, high, xhigh, max, ultra`.
- `/health` exposes API v3 with features `towns` and `thinking`; `/models`
  exposes `default_reasoning_effort`; `/town/assets` serves only the 14-file
  allowlist through data URLs and bytes.

### Desktop

- The page body is the selected town: central Router house, one house and one
  person per worker, stable 4×3 plots for all 12 backend slots, text status
  badges (`queued/running/review/failed/cancelled`) and a dirt plaza with trees,
  fountain, lamp, rocks and bushes.
- Construction is a one-shot event keyed to a persisted known-worker baseline;
  a town first observed empty still animates its first worker. Reduced motion
  skips both animations.
- Town switcher: select, previous/next, **New town**, **Sessions**. Arrow keys
  switch only when focus is not in a field; the selected id persists.
- House dialog reuses `ChatHeader`/`Transcript`/`Composer`, so stop, retry,
  follow-up, model/thinking changes, diff, export and PR comment go through the
  same mutations. Escape/backdrop close, Tab is trapped from inside or outside,
  focus starts in the composer and returns to the house. The worker dialog has a
  **↗ Router** action only when the owner is known.
- Settings, Usage, GitHub and Sessions are opaque drawers. Settings sits on
  `--card` with a solid fallback, a `bg-black/45` backdrop, 420 px width and a
  labelled “⚙ Settings” header action. Default thinking, presets, worker limits
  and yolo are there.
- Assets are vendored PNGs (14): Hermes Town MIT buildings/trees/props, CC-BY-SA
  grass from hilau/George Bailey/bluecarrot16, CC0 person from Luis Zuno.
  See `assets/town/ATTRIBUTION.md`.

### Tests and tooling

```text
python -m pytest -q                         517 passed
node --check desktop/plugin.js              ok
node tests/towns_render_probe.mjs           town render probe: ok
hermes plugins validate .                   Validation passed.
hermes plugins doctor --ci .                5 tools, 0 hooks
```

The 992×460 DOM probe with 12 houses reports **zero overlaps and zero
clipping**. The installed plugin is a directory link at
`~/.hermes/plugins/superagent -> C:\Users\hongy\Desktop\hermes-superagent`, and
`hermes plugins show superagent` reports **0.5.7**.

## Not verified / blockers

1. **Installed desktop UI.** `dashboard/plugin_api.py` is loaded when the
   gateway boots. The gateway hosting this chat still has the old route table,
   so `/towns` and `/town/assets` return 404 until the gateway is restarted from
   an **outside shell**. Do not run the restart from this chat. After the
   restart, use **Reload desktop plugins** so `desktop/plugin.js` is fresh.
   The page now detects this and shows a “Gateway needs a restart” banner; a
   `/towns` 404 also names the restart instead of claiming the plugin is
   disabled.
2. **Real Router spawn.** `superagent_spawn` is registered and tested with a
   fake Runner, but no real headless `hermes chat` turn has called it against
   this gateway. Verify: create a town, prompt the Router to spawn two workers,
   confirm two houses appear and both child sessions launch through the shared
   Runner.
3. **Visual pass in the app.** The scene has been DOM-probed and the sprites
   inspected, but not seen in Electron at 1024 px. Check house/person scale,
   labels, animation, reduced motion, focus trap and reload persistence.
4. **PR-only paths.** The dialog's diff/comment controls depend on a finished
   session with a repository/PR; they reuse existing code but have not been
   exercised in a town dialog.

## Resuming

```bash
cd C:/Users/hongy/Desktop/hermes-superagent
git status --short          # should be clean on main
git log --oneline -5
# outside this chat:
hermes gateway restart
# in the app: Reload desktop plugins
```

Then:

1. Create two towns. Ask the first Router to “spawn two workers: one fixes the
   parser, one adds a test”.
2. Confirm the Router is centred, two houses appear with construction, and each
   house opens its own transcript.
3. Switch towns and use previous/next; confirm no session is stopped.
4. Open a worker, follow up, change thinking to `xhigh`, then retry a failed
   chat.
5. Check the console for 404s, remote image URLs, focus escapes or horizontal
   scroll at 1024 px.
6. Only then consider the installed slice verified.

The task brief is in `tasks/2026-11-07-superagent-towns-brief.txt`.
