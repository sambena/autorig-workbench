# Autorig Workbench

Auto-rigging for sculpted and generated models, run headless in Blender, with a local GUI. Drop in a model, press
**Run all**, and see whether the rig is good: the tool surveys the source, builds a named skeleton and skins it, trims
it to an engine budget, audits the rig (PASS / CHECK / FAIL, with pictures), authors clips for its archetype, and writes a model
card an engine importer can read. Blender never opens a window.

**Status: early (alpha).** The pipeline, the GUI and the 3D results viewer work. Writing a model's `rig.json` is
still done by hand; a point-and-click spec editor (pick bones in 3D, save and re-rig, before/after audit) is being
built next. Audits are graded PASS / CHECK / FAIL, and **Audit all** grades the whole collection; the viewer does not
mark tear sites yet. Expect rough edges; issues and pull requests are welcome.

## Quick start

Needs Python 3.9+ and Blender (tested on 5.2; found via `AUTORIG_BLENDER`, `BLENDER`, `PATH`, or the usual install
folder). Nothing to install with pip.

    run.cmd                          (Windows)   or   ./run.sh   or   python -m autorig
    python -m autorig --models D:\models --work D:\models\_autorig --port 8765 --no-browser

The server binds to 127.0.0.1 and opens the page on a link carrying a session token (it also prints it).

1. **Add a model**: drop an FBX with its `.fbm` folder, a GLB, glTF, OBJ, a zip, or a whole model folder on the page;
   or choose files or a folder; or paste paths on this computer (copied, never moved). It lands in
   `<models>/<group>/<name>/`.
2. **Survey** shows what the source holds and renders four views to read its facing from.
3. Write its **`rig.json`** (docs/SPEC.md) beside it: the kind of rig, the way it faces, its chains.
4. **Run all**: rig, trim, audit, publish, clips if the spec names a clip archetype, and the viewer's preview. Or press the steps one at a
   time. A greyed-out button says what it is missing. **Cancel** stops the running step (its own process only).
5. Read the **results**: the audit table and its sheets, the bend test, one strip of frames per clip, the survey, the
   spec and the card. **Open output folder** shows the files. The model list's badge is the audit's grade: **PASS**
   (green) within every limit, **CHECK** (amber) past a limit but inside its warn band, so look at it in the viewer,
   **FAIL** (red) clearly broken (docs/PIPELINE.md, "Grades").
6. **Audit all** (above the model list) audits every rigged model one after another, with the live log and Cancel,
   then shows a table worst first: grade, tears, the widest gap, bleed, head share, the worst bone. A row opens its
   model; **Table** shows the last results again.
7. **View results** opens the 3D viewer (`viewer.html`) on the model, and every other rigged model: the rig on a stage
   beside a 1.8 m figure, side-on facing +X as a side-on game shows it (or free orbit), with its clips, a skeleton /
   bone names / weights overlay, and checks (real height, facing, bones, clips). Keys: Left/Right model, Up/Down clip,
   Space play, R overlay, [ ] bone, V view, F frame, L loop; a gamepad's D-pad, A, X, Y, LB and RB do the same. It
   reads `<rig folder>/preview.glb`, which **Preview** writes (a model without one says "run Preview first").

## Command line

    set AUTORIG_MODELS=D:\models
    python autorig/cli/pipeline.py wolf,moth           rig -> trim -> audit, with a graded table
    python autorig/cli/run.py audit-all                 audit every rigged model, worst first (exit 1 on any FAIL)
    python autorig/steps/publish.py Creatures           model cards and the group's pack.json
    python autorig/cli/run.py preview wolf              rigged/preview.glb for the 3D viewer (pipeline.py -preview too)
    python autorig/cli/run.py help                      every step by name, for a launcher in the collection

- `AUTORIG_MODELS`: the models root (default `samples/`). A model is a folder with a source export, at the root or
  one level down in a group.
- `AUTORIG_WORK`: QA pictures, rig logs, audits, survey results, clip frames (default `<models>/_autorig`).
- `AUTORIG_BLENDER`: the Blender executable, when it is not found by itself.

## Layout

    autorig/gui/     server.py  index.html                 the local GUI
                     viewer.html  viewer.js  viewer_api.py  the 3D results viewer
                     vendor/three/                         three.js 0.186.0 (MIT), vendored so it works offline
    autorig/core/    layout  spec_store  blender  source_io  geo  skeletons  grades
    autorig/steps/   survey  facing  measure  probe_tips    inspect   (run inside Blender)
                     rerig  rerig_humanoid  run_builder     rig
                     decimate  audit  make_clips            trim, check, animate
                     preview_glb                           the viewer's GLB copy of a rig and its clips
                     publish                                cards     (plain Python)
    autorig/cli/     pipeline  audit_all  qa_sheets  qa_overview
    docs/            PIPELINE  SPEC  FORMATS  SKELETONS  PLAN
    tests/           test_server.py  test_viewer.py  test_grades.py
    samples/         the default models root (empty)

Docs: [PIPELINE](docs/PIPELINE.md) (the steps and their rules), [SPEC](docs/SPEC.md) (`rig.json` and
`autorig.json`), [FORMATS](docs/FORMATS.md) (cards, clip manifests), [SKELETONS](docs/SKELETONS.md) (the bone
conventions), [PLAN](docs/PLAN.md).

## Tests

    python -m unittest discover -s tests -v

The server test starts the GUI's server with no browser against a temporary models folder, uploads a generated OBJ
model with its `rig.json` the way the page does, runs every step through the API, cancels a running step, and checks
the audit and the card. The viewer test checks the viewer's page, code and vendored three.js (token and Host rules,
no way out of the folder), the list of previews, and runs the preview step on a generated two-bone rig with two
actions, reading the GLB back: one skin, both clips as animations. The Blender parts are skipped when Blender is not
installed. The grades test checks PASS / CHECK / FAIL on made-up audit numbers (bands, gaps, allowances, old
audits), and the collection endpoints on made-up audit files: the table worst first, the badges, the tear sites, and
Audit all going on past a model it cannot read.

## Licence

GPL-3.0-or-later (LICENSE). The steps run inside Blender and import `bpy`, which is GPL; one licence keeps it simple.
three.js, under `autorig/gui/vendor/three/`, is MIT (its own LICENSE there; VERSION.txt says where it came from).
