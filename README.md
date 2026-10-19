# Autorig Workbench

Auto-rigging for sculpted and generated models, run headless in Blender, with a local web page. Load a model,
rig it, animate it: the tool fits a skeleton and skins it, audits the rig (PASS / CHECK / FAIL), attaches clips and
exports a game-ready GLB or FBX. Animation comes from [Mesh2Motion](https://github.com/Mesh2Motion), whose editor is
built into the page. Blender never opens a window.

**Status: early (alpha).** The page is new (phase M5 of docs/PLAN.md): three steps, Load → Rig → Animate, on the
HTTP API and the embedded Mesh2Motion app. The old page, its 3D viewer and its spec editor are gone;
[docs/OLD-UI.md](docs/OLD-UI.md) says where each of their features went. A repair
pass is under way (docs/PLAN.md, "R"): R1 (retarget, the watch folder, batch runs, Cancel, auto-tune), R2
(skeletons: rolls, IK, joint placement, naming, detection, skeleton checks in the audit), R3 (the skin passes:
fixed, gated to the body plans they are for, each switchable) and R4 (clips without pops, root motion, gallop,
retarget quality, per-clip morphs, honest export packages) are in and not yet re-tested in
Blender; R5 (speed: the same results, about a fifth faster on the samples) is measured. `run.py m2m-rig` (M2) fits
any of Mesh2Motion's nine skeletons to a mesh (picked from the body plan, or named), skins and audits it, and bakes
its clips into GLB and FBX. Its skinning now fades every joint as far as a bend there needs (M9): on the bake-off's
25 models 6 PASS and 3 CHECK (none passed before), two of them generated asset-packs meshes, but most generated
meshes still FAIL (docs/SKIN-BAKEOFF.md). Expect rough edges; issues and pull requests are welcome.


## Quick start

Needs Python 3.9+ and Blender (tested on 5.2; found via `AUTORIG_BLENDER`, `BLENDER`, `PATH`, or the usual install
folder). Nothing to install with pip.

    run.cmd                          (Windows)   or   ./run.sh   or   python -m autorig
    python -m autorig --models D:\models --work D:\models\_autorig --port 8765 --no-browser

The server binds to 127.0.0.1 and opens the page on a link carrying a session token (it also prints it).

Each step has a time limit (rig and audit 10 minutes, clips 10, trim and preview 5); a step past it is stopped with
the processes it started. Raise one for big models with `AUTORIG_TIMEOUT_<STEP>` (e.g. `AUTORIG_TIMEOUT_RIG=1800`), or
all of them with `AUTORIG_STEP_TIMEOUT`. `AUTORIG_MAX_MEMORY_MB` caps a step's memory where the platform reports it
(Linux, macOS; 4096 by default, 0 for none).

The page has three steps, and about ten controls:

1. **Load.** Drop a GLB, FBX, glTF, OBJ or ZIP on the page (or choose a file), or pick a model from the library,
   with search and a picture where the model has one. A new model lands in `<models>/<name>/`.
2. **Rig.**
   - **Auto rig** fits one of Mesh2Motion's nine skeletons (human, fox, horse, bird, dragon, kaiju, spider, snake,
     shark), picked from the body plan or chosen in the menu. It then skins the model and audits it, with the live
     log and Cancel.
   - The result is the audit's grade: **PASS** (within every limit), **CHECK** (past a limit, inside its warn band)
     or **FAIL**, with the reason in a sentence (docs/PIPELINE.md, "Grades").
   - **Adjust joints** opens the Mesh2Motion editor on the page with our fitted skeleton. Move joints there, then
     **Apply** to re-skin.
   - **Classic** in the menu runs our own skeleton from `rig.json` instead ([SPEC.md](docs/SPEC.md)).
3. **Animate.** Mesh2Motion's clip list plays on the rigged model. Tick clips, choose GLB or FBX, Unity or other
   engines, and Mesh2Motion or Mixamo bone names, then **Export**. The files land in the model's folder, with
   download links.

**Advanced**, at the foot of the page and closed by default, lists every step and tool the workbench has:
- **Steps on the current model:** prepare, survey, classic rig, trim, audit, clips, publish, preview, Mesh Doctor
  and run all.
- **Tools:** game-prop, fbx-maps, the Mixamo steps and the rest, each with a form made from its options.
- **Job log:** for whatever is running.

On a server without Mesh2Motion rigging (`m2m-rig`, phase M2), Auto rig, Apply and Export say so, and offer the
classic rig instead.

## Game tools

`run.py game-prop` (a generated prop as a game prop: metres, facing, LODs, maps, a one-piece truck's tyres cut out),
`run.py game-parts` (a game prop's moving parts, such as a pumpjack's beam or a windpump's fan, cut out of LOD0 as
their own meshes at their pivots), `run.py m2m-rig` (any body on one of Mesh2Motion's nine skeletons, playing its clips: docs/PIPELINE.md), `run.py tripo-clips` (idle/walk/trot/gallop on a Tripo-rigged animal's own skeleton, after mending its GLB) and
`run.py crop-cards` (flat picture cards of plants for a foliage atlas). See [docs/GAME-TOOLS.md](docs/GAME-TOOLS.md).

## Working folder

The server works on one models root: `--models` or `AUTORIG_MODELS` (default `samples/`). A model is a folder with
a source export, at the root or one level down in a group. Specs and work files are keyed on the model's name, so a
model can move between groups with nothing else changing.

## Command line

    set AUTORIG_MODELS=D:\models
    python autorig/cli/pipeline.py wolf,moth           rig -> trim -> audit, with a graded table
    python autorig/cli/run.py audit-all                 audit every rigged model, worst first (exit 1 on any FAIL)
    python autorig/cli/run.py score all                 0-100 quality score per rigged model, ranked, with contact sheets (docs/QUALITY.md)
    python autorig/steps/publish.py Creatures           model cards and the group's pack.json
    python autorig/cli/run.py preview wolf              rigged/preview.glb, a GLB of the rig and its clips
    blender -b --python autorig/steps/suggest_step.py -- -only wolf           suggest archetype and skeleton
    python autorig/cli/run.py help                      every step by name, for a launcher in the collection

- `AUTORIG_MODELS`: the models root (default `samples/`). A model is a folder with a source export, at the root or
  one level down in a group.
- `AUTORIG_WORK`: QA pictures, rig logs, audits, survey results, clip frames (default `<models>/_autorig`, or
  the parent folder's `_autorig` when only that exists).
- `AUTORIG_BLENDER`: the Blender executable, when it is not found by itself (tested on 5.2 LTS; warns on other versions).

## Layout

    autorig/gui/     server.py  index.html  app.js         the local server and the page (Load, Rig, Animate)
                     api_v1.py                             the HTTP API (docs/API.md)
                     m2m/                                  the built Mesh2Motion app (scripts/build_m2m.py)
    autorig/core/    layout  spec_store  blender  source_io  geo  skeletons  grades  suggest  placed_rules
    autorig/steps/   survey  facing  measure  probe_tips    inspect   (run inside Blender)
                     rerig  rerig_humanoid  run_builder     rig
                     decimate  audit  make_clips  suggest   trim, check, animate, suggest
                     preview_glb                           a GLB copy of a rig and its clips
                     publish                                cards     (plain Python)
    autorig/cli/     pipeline  audit_all  qa_sheets  qa_overview
    docs/            PIPELINE  SPEC  FORMATS  SKELETONS  EXPORT  PLAN  CI  rig.schema.json
    tests/           test_server.py  test_api_v1.py  test_viewer.py  test_grades.py  test_spec_editor.py
                     test_suggest.py  test_placed_rules.py  test_blender_version.py
                     test_audit_thresholds_ci.py  test_json_schema.py
    ci/              ci.yml                                GitHub Actions Linux workflow
    samples/         the default models root

Docs: [PIPELINE](docs/PIPELINE.md) (the steps and their rules), [SPEC](docs/SPEC.md) (`rig.json` and
`autorig.json`), [FORMATS](docs/FORMATS.md) (cards, clip manifests), [SKELETONS](docs/SKELETONS.md) (the bone
conventions), [EXPORT](docs/EXPORT.md) (engine packages), [PLAN](docs/PLAN.md), [API](docs/API.md),
[M2M-BRIDGE](docs/M2M-BRIDGE.md), [OLD-UI](docs/OLD-UI.md), [CI](docs/CI.md), [Schema](docs/rig.schema.json), [CONTRIBUTING](CONTRIBUTING.md), [CREDITS](CREDITS.md).

## Mesh2Motion

The workbench is moving its animation onto [Mesh2Motion](https://github.com/Mesh2Motion) (docs/PLAN.md, "M"). Its
web app is a git submodule (`third_party/mesh2motion-app`, our fork) that the server serves at `/m2m/`:

    git submodule update --init third_party/mesh2motion-app
    python scripts/build_m2m.py          (needs Node 24 and npm; writes autorig/gui/m2m/)

Then open `http://127.0.0.1:8765/m2m/`.

**Collections and server files.** The page can switch collections (models roots) and pick files already on the
server, but only the folders the server allows: list them in `.autorig_server.json` next to the tool
(`collections`, `browse`), or run your own copy with `--allow-any-folder` to open any folder. docs/API.md has the
format. AI Assist (Fix this rig) is in the page's Advanced panel, with the cost shown before every request.

**The page.** Pick a model from the library (or drop one, or take one from the server's inbox) and it shows at once in
a 3D preview: its rig and clips when it has them. Mesh2Motion's nine skeletons are picture tiles beside it; Auto
picks one from the body. three.js 0.186 is vendored for the preview (`autorig/gui/vendor/three/VERSION.txt`).

**API.** Scripts, AI agents and Unity drive the workbench through `/api/v1` with a Bearer token: add a model, rig it on a
Mesh2Motion skeleton, attach clips, export. [docs/API.md](docs/API.md) has examples; `/api/v1/openapi.json` describes
every route. Every step and tool is listed once, in `autorig/core/steps_registry.py`. Without the build, `/m2m/` answers 404 and the rest of the workbench is
unaffected.

The fork adds an embedding bridge, so a workbench page can drive Mesh2Motion in an iframe. URL parameters
(`create.html?embed=1&model=...&template=...&skeleton=...&step=animate`) and `postMessage` requests load our fitted
joints into a template, read back the joints the user edits, pick clips and return exports as a Blob. The protocol
is in [docs/M2M-BRIDGE.md](docs/M2M-BRIDGE.md), and `/m2m/bridge-test.html` exercises every message.

**Clips on Mixamo people.** A person already rigged on Mixamo can play Mesh2Motion's 178 human clips without being
rigged again. The clips are written as takes into `<Name>_m2m.fbx`, beside the shipped file, which is left alone:

    python autorig/cli/run.py m2m-clips TornadoTitans/Ice_Age26_Tripo --clips "Idle,Walk,Run"
    python autorig/cli/run.py m2m-clips --list

The feet stay planted (leg IK onto the clip's ankles). `clip-check` scores each take. Only `*_RM` clips travel, and
only with `--root-motion`. See docs/PLAN.md, "M6 notes".

## Clip motion

Clips are keyed, then planted feet are held where they land; `clip-check` scores foot skate. See
[docs/CLIP-MOTION.md](docs/CLIP-MOTION.md).

## Optional extras

Some steps do better with a pip library (the suggester's body plan and picture vote, and the mesh prep below).
These install for Blender's own Python with `python autorig/cli/run.py extras install <name|all>`. The export
passes' two programs (gltfpack, glTF-Validator) install the same way. See [docs/EXTRAS.md](docs/EXTRAS.md). Mesh prep
in use: PyMeshLab decimation for LODs and untextured meshes, game-prop `--atlas` (xatlas re-unwrap and bake) and
`--colliders` (CoACD), Mesh Doctor repair and watertight copies (PyMeshLab, PyMeshFix, manifold3d), and manifold3d
cuts in game-parts and wheel carving ([docs/GAME-TOOLS.md](docs/GAME-TOOLS.md), with before/after numbers).

## Tests

    python -m unittest discover -s tests -v

The server test starts the server with no browser against a temporary models folder. It checks the page and its
code (token and Host rules) and that the old pages are gone. It uploads a generated OBJ model with its `rig.json`,
runs every step through the API, cancels a running step, and checks the audit and the card. The API test covers
`/api/v1`: tokens, OpenAPI, steps, templates and clips, adding models with their files and thumbnails, rig,
skeleton, tools and jobs.

The viewer test checks the previews list with its audit digest and the worst bones an audit blames. It also runs the
preview step on a generated two-bone rig with two actions, and reads the GLB back: one skin, both clips as
animations.

The grades test checks PASS / CHECK / FAIL on made-up audit numbers (bands, gaps, allowances, old audits). It also
checks the collection endpoints on made-up audit files: the table worst first, the badges, the tear sites, and Audit
all going on past a model it cannot read.

The spec test checks the rig.json writer (a hand-written file comes back byte for byte), the schema checks and the
spec routes. With Blender, a generated model with a `bone_N` skeleton is re-rigged twice through them.

The Blender parts are skipped when Blender is not installed.

## Licence

GPL-3.0-or-later (LICENSE). The steps run inside Blender and import `bpy`, which is GPL; one licence keeps it simple.
Mesh2Motion, under `third_party/mesh2motion-app/`, is MIT for its code and CC0 for its models, rigs and animations
(`LICENSE-MIT.MD` and `LICENSE-CC0.MD` there), by Scott Petrovic and contributors.

**[CREDITS.md](CREDITS.md)** lists every project, paper, library, data set and service the workbench borrows from or
works with, who made it, what was taken and under which licence, with links to each.

## AI Assist

AI Assist is in the page's Advanced panel, and its routes are `/api/v1/ai/*` (docs/OLD-UI.md, docs/API.md). It lets an AI (Claude, OpenAI, Gemini, or Ollama on your own machine) drive the workbench's
tools. The first one is **Fix this rig**: the AI reads the model's audit and QA pictures, tries candidate `rig.json`
files on a scratch copy (rigging and auditing them on this computer), and proposes the best one. You apply it, which
saves `rig.json` and re-rigs, or not; an applied fix can be undone. When it needs a decision it asks with answer
buttons, and the dialog lists the steps the model is missing (survey, suggest a skeleton, rig, audit, clips, preview)
as buttons in order, with **Fix all**: the workbench's own steps, free.

AI is off until one is picked in its settings. Every request shows its worst-case cost on the
button before it runs. Paid AI stops at a monthly limit ($5 by default), and every call's cost is logged. Keys stay
on this computer, in `.autorig_ai.json`, which git ignores. See [docs/AI-ASSIST.md](docs/AI-ASSIST.md), which also
lists the next tools planned for it.

## Generate

`POST /api/v1/generate` makes a model with [Tripo](https://www.tripo3d.ai), [Meshy](https://www.meshy.ai) or
[Hyper3D Rodin](https://hyper3d.ai) and drops it into the open collection; `POST /api/v1/pipeline` does that and then
rigs and animates it. Every generation is priced first and spent only by a second call that confirms that price.
Generation is off until the server's owner turns it on (`python scripts/generate_settings.py --on`), and each
provider has a daily and monthly cap in its own credits. Keys stay in `.autorig_generate.json`, which git ignores. A
model already made on one of those accounts imports by its task id, free. See [docs/GENERATE.md](docs/GENERATE.md).
The page's Generate tab is next.

## Model Studio

Model Studio (from the command line since the old page went) makes static/skinned LOD exports, one merged engine LOD FBX (static, skinned with
its clips, or vehicle parts on wheel pivots), vehicle wheel pivots and a spin preview, reference comparison sheets, Poly Haven materials, seeded foliage/card generation, resumable Hyper3D
jobs and per-clip animation quality checks. Settings stay with the model in `studio.json`; derived files
stay in `_studio/`, outside source discovery. Image tools optionally need Pillow; paid generation is
only submitted when explicitly started. See [Model Studio](docs/MODEL-STUDIO.md) for setup and limits.

    python autorig/cli/run.py studio lods wolf
    python autorig/cli/run.py studio reference wolf
    python autorig/cli/run.py studio validate wolf
    python autorig/cli/run.py studio engine wolf      # after studio lods (glb): one FBX with every level
    python autorig/steps/export.py wolf --target unity --engine-lods

## Engine export

`python autorig/steps/export.py <model> --target unreal|unity|godot|web|all` writes a zipped package per engine
(the page's Export covers GLB and Unity FBX). Unity's FBX is rewritten so every root imports with rotation (0,0,0) and scale 1, and checked by
reading the file back. Web and Godot GLBs go through gltfpack and the Khronos glTF-Validator when those are
installed (`run.py extras install gltfpack gltf-validator`); a GLB with validator errors stops the export. Each
package lists its passes in `export_report.json`. See [docs/EXPORT.md](docs/EXPORT.md).
