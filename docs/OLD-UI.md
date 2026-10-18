# The old UI, and where its features went

Phase M5 (docs/PLAN.md, section M) replaced the old page with one three-step page, Load → Rig → Animate:
`autorig/gui/index.html` and `app.js`. It is built only on the HTTP API ([API.md](API.md)) and the embedded
Mesh2Motion app ([M2M-BRIDGE.md](M2M-BRIDGE.md)). Every step and tool also sits in its **Advanced** menu, at the
foot of the page.

These old pages and scripts were deleted:
- `workbench.js` (the old page's code)
- `spec_editor.html` / `.js`
- `studio.html` / `.js`
- `viewer.html` / `.js` and `viewer_logic.js`
- `help.html`
- `ai_assist.js`
- the vendored three.js under `gui/vendor/`

The back ends behind them stay. The table says how to reach each feature now.

**Legacy routes.** The old page's JSON routes (`/api/state`, `/api/run`, `/api/spec`, `/api/ai/...`,
`/api/studio/...` and the rest) went in phase M7, with the code only they used. Every feature they served is on
`/api/v1` ([API.md](API.md)), on the CLI, or both, as the table says. The page's extras are in its collapsed
**Advanced** panel: **This model** (the steps, Manage, Retarget, Model Studio, rig.json) and **Collection** (Batch,
Samples, Clean up).

| Old UI | Now: page | Now: API | Now: CLI |
|---|---|---|---|
| Drop or choose a model, paste paths | Load: drop or choose a file, the library (one card per model), or **New files on the server** (one card per model in the inbox folder; the best file picked) | `POST /api/v1/models` (file body, or JSON `{url}` or `{path}`); `GET /api/v1/files/models` | copy it into `<models>/<group>/<name>/` |
| Model list, search, group menu | Load: library with search and thumbnails | `GET /api/v1/models` | `run.py help`, the folder itself |
| (new) Recent models | the Recent models dropdown in the header: the last ten opened in this browser, with a ✕ to clear (picking one opens its collection first) | (browser only: localStorage) | — |
| Prepare, Survey, Rig, Trim, Audit, Make clips, Publish, Preview, Re-bake clips, Mesh Doctor, Run all | Advanced → Steps on this model (a greyed button says what is missing) | `POST /api/v1/models/{m}/steps/{step}` | `run.py <step> <model>`, `run.py <model>` (rig → trim → audit) |
| Cancel | Cancel on the job panel | `DELETE /api/v1/jobs/{id}` | Ctrl+C |
| Live log | the job panel under each action | `GET /api/v1/jobs/{id}/events` | the console |
| Audit grade and table | Rig: the grade with its reason | `GET /api/v1/models/{m}` (`audit`) | `run.py audit-all` (table worst first) |
| Audit all, Rig all, Clips all, Source all, audit the failed ones, rig or audit selected | Advanced → Collection → Batch (keep going past a failure), with the audit table worst first | `POST /api/v1/batch/{audit-all\|rig-all\|clips-all\|audit-failed\|source-all}` with `{models?, keep_going?}`; `GET /api/v1/audits` | `run.py audit-all`, `run.py batch all --steps ...` |
| Edit spec (spec editor), Suggest skeleton | Rig: Auto rig on a Mesh2Motion skeleton, Adjust joints in the Mesh2Motion editor; the classic rig's rig.json in Advanced → This model → rig.json (checked, saved over the text you loaded only, the old one kept as rig.json.bak) | `POST .../rig`, `GET`/`PUT .../skeleton`; `GET`/`PUT .../spec` for rig.json | `run.py suggest <model>`, `python autorig/steps/auto_tune.py <model>`, edit `rig.json` by hand ([SPEC.md](SPEC.md)) |
| View results (3D viewer) | the preview in the Rig step: any model shows the moment it is picked (its rig and clips when it has them; GLB or FBX), and Animate plays clips in the Mesh2Motion frame | `GET .../files/{path}` for `preview.glb`; `GET .../audit` (the whole audit, and the bones it blames worst first) | `run.py preview <model>`, then open the GLB in any viewer |
| Export (File → Export) | Animate: Export (GLB / FBX, Unity or other engines, Mixamo names); an engine package (Unity, Unreal, Godot, web) is Model Studio's package tool | `POST .../animate`, `POST .../studio/package`, `POST /api/v1/tools/{tool}` | `python autorig/steps/export.py <model> --target unity\|unreal\|godot\|web\|all` |
| Retarget a mocap clip | Advanced → This model → Retarget a mocap clip (upload a BVH or FBX, Plan, Retarget) | `POST /api/v1/mocap` (the file as the body), `GET /api/v1/mocap`, `GET`/`POST .../retarget` | `run.py retarget <model> <clip>` |
| Model Studio (LODs, vehicle, reference, materials, foliage, clip check, engine package, engine FBX) | Advanced → This model → Model Studio (a tool's settings as JSON, Run or Save; its outputs, each one importable as a new model). Generate is not there: it spends money and comes with M8 | `GET .../studio`, `POST .../studio/{tool}` (`{settings, save?}`), `POST .../studio/import` | `run.py studio <tool> <model>` |
| AI Assist (Fix this rig, settings, spend) | Advanced → AI Assist (the cost shown before every request) | `/api/v1/ai/*` | none yet ([AI-ASSIST.md](AI-ASSIST.md)) |
| Game tools (game-prop, game-parts, tripo-clips, crop-cards, fbx-maps, Mixamo steps, smoke flipbook) | Advanced → Tools (a form from each tool's options) | `POST /api/v1/tools/{tool}` | `run.py <tool> ...` |
| Model Manager (move, rename, duplicate, delete) | Advanced → This model → Manage (Delete asks again on the page) | `POST .../rename`, `.../move`, `.../duplicate`; `DELETE /api/v1/models/{m}?confirm={m}`; `GET /api/v1/groups` | move or rename the folder (work files are keyed on the name) |
| Working folder switch | the collection picker in the header, and Advanced → Collections and folders (only the folders the server's `.autorig_server.json` lists, or any with `--allow-any-folder`) | `GET /api/v1/collections`, `POST /api/v1/collections/select` | `--models` or `AUTORIG_MODELS` |
| Samples gallery | Advanced → Collection → Samples (Load copies one into the open collection) | `GET /api/v1/samples`, `POST /api/v1/samples/{name}` | `samples/` is the default models root |
| Clean up generated files | Advanced → Collection → Clean up generated files (sizes by kind; Delete asks again) | `GET /api/v1/clean`, `POST /api/v1/clean` with `{kinds}` | `python autorig/core/clean_generated.py --root <models> [--delete ...]` |
| Commit to Git | (none: CLI only. On the hosted copy it would push from the server with the server's own credentials) | (none) | `python autorig/core/git_commit.py --root <models> --message ...` (branch, commit, push, pull request), or `git` |
| Mesh Doctor report, Blender version, step time limits | Advanced → Steps (Mesh Doctor: repair, watertight) | `GET .../doctor`, `GET /api/v1/server` | `run.py doctor <model>` |
| Open the folder, reap stray Blender processes | (none: desktop-only) | (none) | your file manager; `sudo systemctl restart autorig.service` on the box |
| Help & Guide (`help.html`) | (none) | `GET /api/docs`, `/api/v1/openapi.json` | README and `docs/` |
