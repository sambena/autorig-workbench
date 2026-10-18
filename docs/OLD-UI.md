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
`/api/studio/...` and the rest) still answer for the session token. Nothing in the repo calls them any more. They
are kept so that no feature is lost before it has an `/api/v1` route, and they go in phase M7. New scripts should use
`/api/v1` or `run.py`.

| Old UI | Now: page | Now: API | Now: CLI |
|---|---|---|---|
| Drop or choose a model, paste paths | Load: drop or choose a file | `POST /api/v1/models` (file body, or JSON `{url}`) | copy it into `<models>/<group>/<name>/` |
| Model list, search, group menu | Load: library with search and thumbnails | `GET /api/v1/models` | `run.py help`, the folder itself |
| Prepare, Survey, Rig, Trim, Audit, Make clips, Publish, Preview, Re-bake clips, Mesh Doctor, Run all | Advanced → Steps on this model (a greyed button says what is missing) | `POST /api/v1/models/{m}/steps/{step}` | `run.py <step> <model>`, `run.py <model>` (rig → trim → audit) |
| Cancel | Cancel on the job panel | `DELETE /api/v1/jobs/{id}` | Ctrl+C |
| Live log | the job panel under each action | `GET /api/v1/jobs/{id}/events` | the console |
| Audit grade and table | Rig: the grade with its reason | `GET /api/v1/models/{m}` (`audit`) | `run.py audit-all` (table worst first) |
| Audit all, Rig all, Clips all, Source all | (none yet) | one job per model via `.../steps/{step}`; legacy `POST /api/audit-all` etc. | `run.py audit-all`, `run.py batch all --steps ...` |
| Edit spec (spec editor), Suggest skeleton | Rig: Auto rig on a Mesh2Motion skeleton, Adjust joints in the Mesh2Motion editor | `POST .../rig`, `GET`/`PUT .../skeleton`; legacy `/api/spec*` for rig.json | `run.py suggest <model>`, edit `rig.json` by hand ([SPEC.md](SPEC.md)) |
| View results (3D viewer) | Animate: the rig plays its clips in the Mesh2Motion frame | `GET .../files/{path}` for `preview.glb`; legacy `GET /api/previews` (audit digest, worst bones) | `run.py preview <model>`, then open the GLB in any viewer |
| Export (File → Export) | Animate: Export (GLB / FBX, Unity or other engines, Mixamo names) | `POST .../animate`, `POST /api/v1/tools/{tool}` | `python autorig/steps/export.py <model> --target unity\|unreal\|godot\|web\|all` |
| Retarget a mocap clip | (none yet) | legacy `/api/retarget*` | `run.py retarget <model> <clip>` |
| Model Studio (LODs, reference, engine FBX, materials) | (none yet) | legacy `/api/studio/*` | `run.py studio <tool> <model>` |
| AI Assist (Fix this rig) | (none yet) | legacy `/api/ai/*` | none yet ([AI-ASSIST.md](AI-ASSIST.md)) |
| Game tools (game-prop, game-parts, tripo-clips, crop-cards, fbx-maps, Mixamo steps, smoke flipbook) | Advanced → Tools (a form from each tool's options) | `POST /api/v1/tools/{tool}` | `run.py <tool> ...` |
| Model Manager (move, rename, duplicate, delete) | (none yet) | legacy `/api/model/move` etc. | move or rename the folder (work files are keyed on the name) |
| Working folder switch | (one folder per server) | legacy `/api/workspace/*` | `--models` or `AUTORIG_MODELS` |
| Samples gallery | (none yet) | legacy `/api/samples` | `samples/` is the default models root |
| Commit to Git, Clean up generated files | (none yet) | legacy `/api/git/*`, `/api/clean*` | `git`, `autorig/core/clean_generated.py` |
| Help & Guide (`help.html`) | (none) | `GET /api/docs`, `/api/v1/openapi.json` | README and `docs/` |
