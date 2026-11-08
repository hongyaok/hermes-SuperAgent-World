# Superagent Towns — progress and next steps

**Date:** 2026-11-07
**Status:** vertical slice implemented in source; backend and asset work are on
`main`; the desktop town page is on `feat/towns-desktop`, not yet exercised in
the installed app. The gateway route table is stale and restarting it from this
gateway-hosted chat would terminate the session, so the final desktop pass is
the first item below.

## Landed on `main`

| Commit / PR | What it does |
| :--- | :--- |
| PR #24 | Preserves the pin/unpin work and records the task brief. |
| PR #26 | Additive `parent_session_id` + `town_slot`, migration, `GET /towns`, one-level validation, shared `sa_runtime` Runner, `superagent_spawn`, router prompt/toolset. |
| PR #25 | Vendors the eight pixel-art sprites under `assets/town/` with per-file provenance and licences. |

Backend/version details:

- `chat_sessions.parent_session_id` is NULL for a root town and a root id for
  one worker. `town_slot` is allocated once, inside the insert transaction.
- `GET /towns` groups roots with workers; `/chats` remains flat and compatible.
- `superagent_spawn` creates a real child chat and launches it through the same
  `Runner` (one process-wide semaphore), inheriting the router's model,
  provider and repository.
- `plugin.yaml`, `dashboard/manifest.json`, `dashboard/plugin_api.py` and
  `CHANGELOG.md` are at **0.5.0**.

## Current branch `feat/towns-desktop`

- `dashboard/town_assets.py` + `GET /town/assets` and
  `GET /town/assets/{name}`: explicit allowlist, real `image/png` MIME, no
  request path reaches the filesystem. The manifest returns data URLs so the
  renderer works over local and remote gateways.
- `desktop/plugin.js`: the page body is now the selected town.
  - central Router house + one person; one house + person per worker, keyed by
    stable session id and `town_slot`.
  - text status badges for queued/running/review/failed/cancelled, not just
    animation.
  - compact town switcher (select, previous/next, New town, Sessions).
  - accessible chat dialog reusing `ChatHeader`, `Transcript` and `Composer`;
    Escape/backdrop close, focus trap, focus returned to the house, and a
    stale-data guard that never shows another session's transcript.
  - one-shot construction only for ids that appear after a persisted baseline;
    `prefers-reduced-motion` and timer cleanup are handled.
  - rename/pin/archive from the house menu; search/rename/archive/delete/pin
    stay available in the Sessions drawer; Settings, GitHub, Usage, toasts and
    the status-bar chip remain.
- `tests/towns_render_probe.mjs` + `tests/test_towns_render.py`: renders the
  real desktop tree with stubbed SDK/React imports. Covers the router/worker
  houses, status text, one-shot build class, reduced motion, switcher, dialog,
  stale-transcript guard and the empty page.
- Backend asset allowlist test added to `tests/test_towns.py`.

### Verification already run

```text
python -m pytest -q                        505 passed
node --check desktop/plugin.js             ok
node tests/towns_render_probe.mjs          town render probe: ok
hermes plugins validate .                  Validation passed.
hermes plugins doctor --ci .               OK: runtime discovery, manifest parsing,
                                           import, registration
```

## Not verified / blockers

1. **Installed desktop UI.** `dashboard/plugin_api.py` is loaded when the
   gateway boots. The gateway hosting this chat still has the 0.4 route table,
   so `/towns` and `/town/assets` are 404 until `hermes gateway restart`. Do
   not run that from this chat. Restart outside the session, then **Reload
   desktop plugins**.
2. **Real router spawn.** The tool is registered and the router prompt/toolset
   are wired, but a real headless `hermes chat -t file,terminal,superagent`
   turn has not yet called `superagent_spawn` against this gateway. Verify that
   the plugin toolset is exposed to the headless turn and that the child
   launches through the shared Runner.
3. **Visual pass.** The house/person crops were verified programmatically
   (dimensions, alpha, licence provenance) but not seen in the app. Check the
   1024px layout, house/person scale, label overlap, animation, reduced motion,
   dialog focus trap and reload persistence.
4. **Docs.** README, DESIGN.md, `skills/superagent-operator/SKILL.md` and the
   plugin manifest description still need the Towns section (version/changelog
   are done). The task brief is in `tasks/2026-11-07-superagent-towns-brief.txt`.
5. **Follow-ups.** Replace the `window.prompt` rename stopgap with an inline
   editor if it feels cheap; give the New-town modal a full Tab trap; decide
   whether deleting a root should offer cascade/reattach instead of leaving
   orphaned workers; add a pagination/asset-cache test.

## Resuming

```bash
git checkout feat/towns-desktop
# outside this chat:
hermes gateway restart
# in the app: Reload desktop plugins
```

Then: create two towns, send a Router prompt that asks it to call
`superagent_spawn` twice, switch towns, open the Router and both worker chats,
send a follow-up, and verify the console has no 404s or remote image URLs.
