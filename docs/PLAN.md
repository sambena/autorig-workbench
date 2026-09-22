# Plan

Goal: load a model, press a few buttons, and see whether the rig is good, without ever opening Blender (it runs
headless underneath). Later the tool becomes a free auto-rigger: place or draw the skeleton, and the tool does the
rest.

The steps exist and are proven (PIPELINE.md). The GUI adds no rigging logic: it runs the steps, shows what they wrote,
and from P2 on it edits the spec. Every phase ships something usable on its own.

## Stack: a local Python server and one web page

A standard-library Python server (`http.server.ThreadingHTTPServer`) serves one HTML page, opened in the default
browser. It runs each step as a headless Blender subprocess and streams its log to the page with Server-Sent Events.
From P2, three.js draws the model, skeleton and clips from a GLB the tool exports.

- **No tkinter.** It cannot draw 3D, so P2 would mean a rewrite or a second window. A browser tab gives 3D, images,
  drag-and-drop and layout for free, on every OS.
- **three.js, not `<model-viewer>`.** `<model-viewer>` plays clips well but cannot draw bones, pick joints or show an
  overlay, and P2 and P3 need all three.
- **GLB, not FBX, in the browser.** three.js's FBXLoader is unreliable with Blender's FBX output (axes, scale,
  takes). One small Blender step writes `preview.glb` beside the rig.
- **Vendored, not from a CDN:** three.js (MIT) is under `autorig/gui/vendor/three/` (0.186.0, from the npm package;
  VERSION.txt), so the tool works offline. Plain ES modules and an import map: no build step.
- **Nothing installed with pip.** Python 3.9+ and Blender. Pillow is only for the optional `qa_sheets.py` and
  `qa_overview.py`.
- **Safety:** localhost only, a random session token, a Host check, whitelisted steps, and files served only from
  under the models root and the work folder. Cancel kills the running step by its PID and nothing else.

## Phases

### P1: the GUI (done)

- `python -m autorig`, `run.cmd` or `run.sh` starts the server on 127.0.0.1 and opens the page.
- **Models list:** every model under `AUTORIG_MODELS`, grouped, each marked with what it has: spec, rig, audit
  verdict, clips, card.
- **Add a model:** drop files or a folder (an FBX with its `.fbm`, a GLB, glTF, OBJ, or a zip), pick them, or give
  paths on disk (copied, never moved). The model lands in `<root>/<group>/<name>/`; zips are unpacked.
- **Buttons:** Survey (survey and facing views), Rig (the builder the spec's kind picks), Trim, Audit, Make clips (with
  preview frames), Publish, and Run all. A button without what it needs is greyed out and says why.
- **Live log** from the running step, with Cancel.
- **Results:** the audit as a PASS/FAIL table (each check, its threshold, any allowance and its reason, the warnings)
  with the skin and bend sheets; the bend test; the clip frames as one strip per clip; the survey facts and facing
  views; the spec, its notes and the card. **Open output folder** opens the model's folder.
- Specs are data: one `rig.json` per model and an `autorig.json` for the collection (SPEC.md). Formats: FORMATS.md.
- Tests: `tests/test_server.py`, a headless run of the server's API against one generated model.

### S: switching an existing collection to the tool

A collection that was rigged with its own copy of these scripts moves over in one pass:

1. **The collection owns its data, the tool owns the code.** Each model's spec becomes a `rig.json` beside it
   (comments kept as `notes`); collection-wide settings go in `autorig.json`. A hand-written builder for one model
   stays with that model (`kind: "custom"`).
2. **A launcher in the collection** sets `AUTORIG_MODELS` to it and `AUTORIG_WORK` to its work folder, finds the tool
   (`AUTORIG_HOME`, default a sibling checkout), and starts the GUI or runs `cli/pipeline.py`.
3. **Work files move once** into the new work folder (`qa/`, `audit/`).
4. **Consumers follow:** anything that read the old script folder or the old format names is repointed.

Checked by rebuilding a sample of models through the tool and comparing audits value for value.

### P2: 3D view and editable spec (5-7 days)

The viewer half is done; the spec form and Rig again are next.

- **A preview step** (`steps/preview_glb.py`, done) writes `<rig folder>/preview.glb`: the full-resolution mesh with
  its materials and textures, the armature, and every clip as a named glTF animation (sampled every frame, so IK and
  other constraints are baked in), plus `preview.json` (the source file, clips and frames, bone lengths, deform bones,
  the card's real size). It reads the rig's `.blend` and takes the clips from `clips/<model>_clips.blend`; with no
  `.blend` it imports the rigged FBX and its takes. Run all ends with it; `run.py preview` and `pipeline.py -preview`
  run it from the command line.
- **3D view** (`gui/viewer.html`, `viewer.js`, done): a dark stage with a floor ruled every unit and a 1.8 m reference
  figure; the model centred on the floor, side-on facing +X (the game view) with a key to a free orbit; previous/next
  model across every rigged model and previous/next clip (keys and gamepad); play, pause, frame step, speed and loop;
  the overlay cycles off, skeleton (bones coloured by role, as the QA pictures are, drawn over the mesh), bone names,
  and weights (the mesh coloured by one bone's influence; click a bone or step with [ ]). Checks: real height against
  the figure, facing (+X by the head, toes or tail), bone count, clips, zero-length clips, a preview older than its rig.
  A model with no clips shows its bind pose; one with no preview offers to run Preview. `/api/previews` lists them,
  and **View results** opens it from a model and from a finished run.
- **Viewer polish** (done):
  - *Framing:* the side view and **Frame: model** fit the model, over every clip (each sampled through, not only
    the bind pose), and the whole figure with its label into the largest part of the window the HUD leaves clear
    (every panel's edges are tried as the frame's), for any size of model and on a narrow window. The figure stands
    a short gap to the model's left, the gap scaled to the larger of the two. **Frame: stage** adds the floor and
    the ruler round them. The side view refits when the window changes.
  - *Scrub bar* over the controls: a tick a frame (sparser when they crowd), numbered ticks, the clip's keys, the
    playhead; drag to any frame, and it pauses while dragging (and plays on after if it was playing). Home and End
    too.
  - *The audit:* its PASS/FAIL and the failed checks in the checks panel; the worst bones listed (`viewer_api.py`
    `worst_bones`: a bend that tears, a bleed pair's owner, a head that owns too little, twist tears, collateral,
    reach, hard joints) and tinted on the skeleton, red where a failed check blames them and amber for a warning;
    click one to select it, which also drives the weights overlay. Too many influences is per mesh in the audit, so
    it is listed by mesh.
  - *Bleed view* (an overlay): the audit's bleed rule run again on the preview, vertex by vertex (owned by a bone
    that is neither its nearest, next to it, nor upstream of it, and clearly further away), area-weighted as the
    audit weighs it, so its figure matches the audit's; plus the loose parts that ride the wrong bone, and the
    audit's bleed pairs to click.
  - *Loose parts* in the weights view: every mesh island (welded across UV seams), the bone that carries it and
    how much, flagged when a lot of it is bleed, when one side's bone carries a part that crosses the middle (a
    collar on one leg), or when it rides a bone far from the one it sits by.
  - *Gamepad:* the Gamepad API's standard mapping (Xbox and PlayStation pads), a fallback for other pads (a hat on
    axes 6/7 or one hat axis), the left stick with a radial deadzone moving through models and clips like the
    D-pad, held directions repeating, triggers stepping frames. The logic is `gui/viewer_logic.js`, plain and
    tested under Node (`tests/viewer_logic_test.mjs`); a page can replace `navigator.getGamepads` to simulate a pad.
- **Spec form**, generated from a schema: kind and archetype, forward and origin; the skinning fixes and their tuning
  knobs; audit allowances, each with its reason (required); clip settings as sliders; budget and real size.
- **Rig again** re-runs from the form and shows the audit as a before/after delta.

Done when a user changes an option in the form, presses Rig again, and sees the new skeleton in 3D, a clip playing on
it, and the audit delta, without touching a text file.

### P3: click-to-place joints (8-10 days)

- The measure sheet's three orthographic views, made interactive in the page as images with the grid drawn over them,
  so there is no 3D picking to get wrong.
- **Click to place** a chain's joints, or just a limb's tip and base. Drag to move a joint. The other two views update,
  and mirroring (`.L` to `.R`) is one click. The points go straight into the spec (`points`, `tip`, `base`,
  `stations`); a quick "sticks only" run of `measure.py` redraws the rig in about 2 s.
- **`kind: "placed"`**: a hand-placed winged builder generalised. Its body-part rules become spec data: which bones
  each part may use, which parts blend at a join, which welds to rip, membranes, the jaw line, rigid islands.

Done when a model with no spec goes from drop-in to a PASS audit using only clicks and the spec form.

### P4: suggest a skeleton, and open-source readiness (5-7 days)

- **Suggest a skeleton**, optional: from survey (Tripo-style or Mixamo bones, if any), `probe_tips` (the geodesic
  tips), the model's symmetry plane and its proportions, propose an archetype and chains with tips. Heuristics first,
  no ML or network. Success means fewer clicks, not zero.
- **Samples:** 3-5 models under CC0 or CC-BY, with their licences in `samples/`: a quadruped, a hexapod, a humanoid,
  a flier and a prop.
- **Docs:** screenshots in the README, CONTRIBUTING, and a JSON schema for `rig.json`.
- **CI:** GitHub Actions running headless Blender on Linux against the samples, with the audit thresholds as the test.
- **Blender version check:** at start-up the tool warns outside the tested range (5.2).

Done when a fresh clone on a machine with only Python and Blender rigs every sample to PASS through the GUI, and CI
is green.

## Risks

- **Blender API drift.** The steps are tested on 5.2 LTS. `parent_set(ARMATURE_AUTO)`, the FBX exporter and
  `voxel_remesh` change between versions. Mitigation: a version check, and an audit comparison as the regression test
  before accepting a new Blender.
- **The heuristics were tuned on generated sculpts.** Other sources (other unit scales, other facings, no textures,
  other bone naming) will FAIL more often. P3's manual placement is the escape hatch, and survey flags anything unusual.
- **Long or stuck runs** (bone heat on a dense mesh, the voxel-proxy retries). A streamed log, Cancel by PID, and later a
  timeout per step.
- **GPL-3.0** (Blender's own licence, which scripts that `import bpy` generally share) may put off some contributors.
