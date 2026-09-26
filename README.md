# Autorig Workbench

Auto-rigging for sculpted and generated models, run headless in Blender, with a local GUI. Drop in a model, press
**Run all**, and see whether the rig is good: the tool surveys the source, builds a named skeleton and skins it, trims
it to an engine budget, audits the rig (PASS / CHECK / FAIL, with pictures), authors clips for its archetype, and writes a model
card an engine importer can read. Blender never opens a window.

**Status: early (alpha).** The pipeline, the GUI, the 3D results viewer and the spec editor work: pick bones in
3D, Save and re-rig, and see the audit before and after. Audits are graded PASS / CHECK / FAIL, and **Audit all**
grades the whole collection. Click-to-place joints for models with no skeleton has started in the editor. A repair
pass is under way (docs/PLAN.md, "R"): R1, the fixes to retarget, the watch folder, batch runs, Cancel and auto-tune,
is in and not yet re-tested in Blender. Expect rough edges; issues and pull requests are welcome.


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

1. **Add a model**: drop an FBX with its `.fbm` folder, a GLB, glTF, OBJ, a zip, or a whole model folder on the page;
   or choose files or a folder; or paste paths on this computer (copied, never moved). It lands in
   `<models>/<group>/<name>/`.
2. **Survey** shows what the source holds and renders four views to read its facing from.
3. Press **Edit spec** to make or fix its **`rig.json`** by clicking (below), or write it by hand (docs/SPEC.md).
4. **Run all**: rig, trim, audit, publish, clips if the spec names a clip archetype, and the viewer's preview. Or press the steps one at a
   time. A greyed-out button says what it is missing. **Cancel** stops the running step (its own process and the ones it started, nothing else).
5. Read the **results**: the audit table and its sheets, the bend test, one strip of frames per clip, the survey, the
   spec and the card. **Open output folder** shows the files. The model list's badge is the audit's grade: **PASS**
   (green) within every limit, **CHECK** (amber) past a limit but inside its warn band, so look at it in the viewer,
   **FAIL** (red) clearly broken (docs/PIPELINE.md, "Grades").
6. **Audit all** (above the model list) audits every rigged model one after another, with the live log and Cancel,
   then shows a table worst first: grade, tears, the widest gap, bleed, head share, the worst bone. A row opens its
   model; **Table** shows the last results again.
7. **View results** opens the 3D viewer (`viewer.html`) on the model, and every other rigged model: the rig on a stage
   beside a 1.8 m figure, side-on facing +X as a side-on game shows it (or free orbit), both always framed whole
   whatever the model's size; its clips with a scrub bar (drag to any frame; it pauses while you drag); a skeleton /
   bone names / weights / bleed overlay; the audit's verdict and its worst bones, tinted red (a failed check) or
   amber (a warning) on the skeleton and listed to click; in the weights view, every loose part of the mesh and the
   bone that carries it, flagged when it rides the wrong one; and checks (real height, facing, bones, clips, the
   audit). Keys: Left/Right model, Up/Down clip, Space play, R overlay, [ ] bone, V view, F frame, L loop, `,` `.`
   step, Home/End. A gamepad (standard mapping: Xbox or PlayStation): D-pad or left stick model/clip, A play, Y
   overlay, X view, B frame, LB/RB bone, LT/RT step. It reads `<rig folder>/preview.glb`, which **Preview** writes
   (a model without one says "run Preview first"), and the audit from `<AUTORIG_WORK>/audit/<model>.json`.

## Documentation and help guide

The local GUI server includes a comprehensive, interactive HTML guide with diagrams, button walkthroughs, bone placement instructions, and troubleshooting tips:

- Open via the **Help & Guide ↗** button in the workbench top bar, or visit `/help.html` (served with session token).
- **Button breakdown**: Detailed reference for every action (`Survey`, `Edit spec`, `Rig`, `Trim`, `Audit`, `Make clips`, `Publish`, `Preview`, `Run all`, `Audit all`, `Table`, `Open output folder`, `Cancel`).
- **How to place bones**: Coordinate system conventions (0..1 bounding box space facing -Y), 1-click **Suggest skeleton**, raycast click-to-place ("points in the middle"), and constructive build chains (`slice`, `tube`, `tip`, `points`).
- **How to fix rigs & audits**: Diagnostic guide for reversed facing vectors, combined and single-bend mesh tears, weight bleed (> 2%), low head share (< 2.5%), rip welds, rigid piece assignment, and audit allowances.
- **Visual reference gallery**: Includes rendered QA bend test sheets and skin ownership audit sheets from sample models.

## The spec editor

**Edit spec** (on a model, and in its Spec and card tab) opens `spec_editor.html`: the source model as it came, with its
own skeleton drawn over it and every bone named (the first time, a few seconds' Blender step makes that view), and
the spec as a form beside it. Nothing needs Blender or JSON:

- **Click a bone** to see what it is and what the spec makes of it, with one-click jobs: make it the head or the hips,
  a leg, throw it away (with everything under it), mirror a one-sided limb from it, or start a chain (wing, leg,
  tail...) at it. The chains the spec makes are coloured on the skeleton the way the rig step will read them; limbs
  it would only guess are grey.
- **Pick** beside a field, then click bones (Head bone, Chains, Legs, Mirror...) or points on the model (a head line,
  a jaw, a limb's tip, the corners of a rigid part). A point lands halfway through the part under the mouse. The
  **Flat views** tab draws the side, front and top of the model on the 0..1 grid with the rig the draft would build,
  and clicks there set points exactly.
- Every change is checked (a bone the source does not have, the head and hips the same bone, an audit allowance
  with no reason); the **Changes** tab shows the diff. **Save** writes rig.json beside the model (the old one kept as
  `rig.json.bak`; fields the form does not know are kept). **Undo** and **Revert** take changes back.
- **Suggest skeleton**: One click proposes an archetype and placed bone chains from geodesic extremities (`probe_tips`),
  symmetry, and proportions for boneless models, or maps existing Tripo and Mixamo joint hierarchies.
- **Placed builder (`kind: "placed"`)**: Body-part rules (allowed/denied bones), join blending radii, weld seam ripping
  to prevent stretch between limbs, distance gradient wing membranes with flank cutoffs, jaw hinges, and rigid islands.
- **Save and re-rig** saves, then runs rig, trim, audit, clips and preview with the live log, and shows the new audit
  next to the one before, value by value; the red balls in the view are where the audit's bends tore, each with the
  source bone it came from. **View results** opens the 3D viewer.

## Command line

    set AUTORIG_MODELS=D:\models
    python autorig/cli/pipeline.py wolf,moth           rig -> trim -> audit, with a graded table
    python autorig/cli/run.py audit-all                 audit every rigged model, worst first (exit 1 on any FAIL)
    python autorig/steps/publish.py Creatures           model cards and the group's pack.json
    python autorig/cli/run.py preview wolf              rigged/preview.glb for the 3D viewer (pipeline.py -preview too)
    blender -b --python autorig/steps/source_preview.py -- -only wolf    the spec editor's view of the source
    blender -b --python autorig/steps/suggest.py -- -only wolf           suggest archetype and skeleton
    python autorig/cli/run.py help                      every step by name, for a launcher in the collection

- `AUTORIG_MODELS`: the models root (default `samples/`). A model is a folder with a source export, at the root or
  one level down in a group.
- `AUTORIG_WORK`: QA pictures, rig logs, audits, survey results, clip frames (default `<models>/_autorig`).
- `AUTORIG_BLENDER`: the Blender executable, when it is not found by itself (tested on 5.2 LTS; warns on other versions).

## Layout

    autorig/gui/     server.py  index.html                 the local GUI
                     viewer.html  viewer.js  viewer_logic.js  viewer_api.py
                                                           the 3D results viewer
                     spec_editor.html  .js  spec_api.py    the spec editor
                     vendor/three/                         three.js 0.186.0 (MIT), vendored so it works offline
    autorig/core/    layout  spec_store  blender  source_io  geo  skeletons  grades  suggest  placed_rules
    autorig/steps/   survey  facing  measure  probe_tips    inspect   (run inside Blender)
                     rerig  rerig_humanoid  run_builder     rig
                     decimate  audit  make_clips  suggest   trim, check, animate, suggest
                     preview_glb                           the viewer's GLB copy of a rig and its clips
                     source_preview                        the editor's GLB of the source and its own skeleton
                     publish                                cards     (plain Python)
    autorig/cli/     pipeline  audit_all  qa_sheets  qa_overview
    docs/            PIPELINE  SPEC  FORMATS  SKELETONS  PLAN  CI  rig.schema.json
    tests/           test_server.py  test_viewer.py  test_grades.py  test_spec_editor.py
                     test_suggest.py  test_placed_rules.py  test_blender_version.py
                     test_audit_thresholds_ci.py  test_json_schema.py  viewer_logic_test.mjs
    ci/              ci.yml                                GitHub Actions Linux workflow
    samples/         the default models root

Docs: [PIPELINE](docs/PIPELINE.md) (the steps and their rules), [SPEC](docs/SPEC.md) (`rig.json` and
`autorig.json`), [FORMATS](docs/FORMATS.md) (cards, clip manifests), [SKELETONS](docs/SKELETONS.md) (the bone
conventions), [PLAN](docs/PLAN.md), [CI](docs/CI.md), [Schema](docs/rig.schema.json), [CONTRIBUTING](CONTRIBUTING.md).

## Tests

    python -m unittest discover -s tests -v

The server test starts the GUI's server with no browser against a temporary models folder, uploads a generated OBJ
model with its `rig.json` the way the page does, runs every step through the API, cancels a running step, and checks
the audit and the card. The viewer test checks the viewer's page, code and vendored three.js (token and Host rules,
no way out of the folder), the list of previews with its audit digest, the worst bones an audit blames, and runs the
preview step on a generated two-bone rig with two actions, reading the GLB back: one skin, both clips as animations.
With Node it also runs `tests/viewer_logic_test.mjs`: gamepad mapping and deadzone, the scrub bar, the bleed rule and
mesh islands. The Blender and Node parts are skipped when either is not installed. The grades test checks PASS / CHECK / FAIL on made-up audit numbers (bands, gaps, allowances, old
audits), and the collection endpoints on made-up audit files: the table worst first, the badges, the tear sites, and
Audit all going on past a model it cannot read. The spec editor test checks the rig.json writer (a hand-written file comes back byte for byte) and
the schema checks, then the editor's API: the token, the diff, a stale or broken save refused, `.bak` and unknown
fields kept; with Blender, a generated model with a `bone_N` skeleton goes through the source view and Save and
re-rig twice, the second run carrying the first one's audit as "before".

## Licence

GPL-3.0-or-later (LICENSE). The steps run inside Blender and import `bpy`, which is GPL; one licence keeps it simple.
three.js, under `autorig/gui/vendor/three/`, is MIT (its own LICENSE there; VERSION.txt says where it came from).
