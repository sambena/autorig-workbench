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
- **Results:** the audit as a graded table (each check, its threshold, any allowance and its reason, the warnings)
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

Done: the viewer, the spec editor (the spec form) and Save and re-rig (Rig again).

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
- **Graded audits and Tears overlay** (done): PASS / CHECK / FAIL from tear counts, the gap the worst tear opens, bleed
  and head share (PIPELINE.md, "Grades"); the model list's badges show the grade; **Audit all** audits every rigged model
  in turn (live log, Cancel) and shows a table worst first, each row opening its model (`run.py audit-all` from the
  command line). In the 3D viewer (`viewer.html`, `viewer.js`, `viewer_logic.js`):
  - an overlay "Tears" puts a marker at each of `tear_sites[].clusters[].at`, sized by `edges` and coloured by
    `gap_pct` (amber under the pose's CHECK gap, red over), labelled with the cluster's `bone`; the combined pose
    first, and a list of the posed bones to step through (`bone`, `rotation_deg`) that poses that one bone as the
    audit did (40 degrees about its own X, or a 60-degree twist about Y) so the tear opens on screen;
  - `at` is in the imported FBX's world space (Blender Z up, the FBX's units), mapped into the preview's glTF space,
    with a fallback to `at_bbox` against the mesh's bounding box;
  - the worst bone (`tears.worst_bone`) selected and highlighted in the skeleton overlay when the viewer opens from
    a CHECK or FAIL.
- **Source view** (`steps/source_preview.py`, done): the source export as it came, `<work>/source/<model>.glb` (mesh
  and textures, no skin) and `.json` (every source joint: name, head, tail, parent, and the joints the rig step folds
  into their parent), so the editor can show which `bone_N` is which. A model with no skeleton gets the mesh alone.
- **Spec editor** (`gui/spec_editor.html`, `spec_editor.js`, `spec_api.py`, done): **Edit spec** on a model (and in its
  Spec and card tab) opens the source model with its own skeleton drawn over it, every joint named, and the chains
  the draft makes coloured the way the rig step will read them (rerig.repair and tripo_chains, followed live:
  mirrored copies, folded joints, thrown-away bones, guessed limbs). Click a bone: what it is, what the spec makes of
  it, what the last rig made from it, and one-click jobs (head, hips, a leg, throw away, mirror from here, start a
  chain). The form is built from the schema the server sends (every `rig` field with a plain-English label and help;
  kind, facing, origin, skin style, rigid parts, tuning, audit allowances with their required reason, budget, clips,
  card size); **Pick** beside a field fills it by clicking bones, or points on the model (placed halfway through the
  part under the mouse, so a click on a leg lands in the leg). The server checks every edit against the schema and
  the source's own bones, and the Changes tab shows the diff Save will make; Save writes rig.json in the same compact
  style, keeps every field it does not know and the previous file as `rig.json.bak`, and never overwrites a file that
  changed on disk since the page loaded it. The audit's tears are drawn where they happened, with the source bone
  each torn bone came from.
- **Save and re-rig** (done) saves, keeps the current audit as "before", runs rig, trim, audit, clips (when the spec
  names an archetype) and preview as one job with the live log, then shows the audit before and after, value by value,
  and View results. Form settings for `humanoid` (facing, Z heights, X spans with 3D/flat picking) and `custom` (builder
  script) are directly editable in the form with schema validation; and animated clip playback is embedded directly
  in the editor with Source/Rigged view switching, clip selection, play/pause, and scrub bar.

Done when a user changes an option in the form, presses Rig again, and sees the new skeleton in 3D, a clip playing on
it, and the audit delta, without touching a text file (done).

### P3: click-to-place joints (mostly done)

- Pick beside `tip`, `base`, `points`, `head_line`, `jaw` and `rigid_to`, in 3D or on the flat views; drag to move a
  joint; "+ Mirror chain"; `stations` by click; the flat views redrawn as points move. Done. The "mirror .L/.R"
  checkbox (a drag on one side moving the other) was dead until the cleanup fixed its path check; unverified since.
- **`kind: "placed"`**: the winged builder generalised. `parts`, `blends` and `rip_welds` are small and work
  (`rip_welds` crashed on any real seam until the cleanup); `membranes` is half-built (`cut_flank` and `root_bone`
  are read and never used, and a forelimb near the wing root rides the spars); no sample uses any of them.

Done when a model with no spec goes from drop-in to a PASS audit using only clicks and the spec form. Not there:
the three generated creature samples need a hand-edited spec (samples/README.md).

### P4: suggest a skeleton, and open-source readiness (partly done)

- **Suggest a skeleton**: `core/suggest.py` + `steps/suggest.py`. Since the cleanup the GUI's Suggest runs the
  Blender measurement (tips, symmetry, proportions) instead of returning a template quadruped, and the spine it
  emits runs hips-to-head. Its base guesses (a humanoid's hips at z 0.70, a shoulder at hand height) and the
  facing detector (ties fall to -Y) are unproven on real sculpts.
- **Samples**: five generated from primitives (`scripts/generate_samples.py`), not sculpts: 3 PASS, 1 CHECK, 1
  FAIL after the cleanup. To be replaced by CC0 sculpts (P5).
- **Docs**: CONTRIBUTING, `docs/rig.schema.json` (loose: it lists the fields the code reads, it does not yet
  match every shape), `help.html` (describes the previous page in places).
- **CI**: `.github/workflows/ci.yml` (moved from `ci/`, where GitHub never ran it); not yet seen green. The
  audit-threshold step is a smoke test on a generated column (docs/CI.md).
- **Blender version check**: done.

## The September 2026 batch (PRs 5-42)

Thirty-eight pull requests generated with an AI coding tool in one week, ~27k lines, reviewed on 2026-09-26
(six reviews, one per area, plus a run of the real collection). The state of each after the cleanup below:

| Feature | State |
|---|---|
| Viewer: tears overlay, test poses, worst-bone select, clip switcher | kept; the pose list navigation, playback on leaving the overlay and the retarget modal were broken and are fixed |
| Viewer: "camera system", ground plane, axis locks, feet levelling | two fixed views added to the original fitter; a humanoid check that matched every rig (and threw) is fixed; the rest lives in the editor |
| Spec editor: humanoid/custom forms, embedded clips, joint drag, stations, mirror chain, flat views | kept and plausibly working |
| Spec editor: live bend preview, gait HUD, pose gizmo | half-built: nothing to bend for `tripo`, an arbitrary hinge on 2-point chains; HUD/gizmo only on `spec_editor.html` |
| Workbench page (menus, console drawer, bulk-job counter, samples gallery, re-bake) | kept; fixed: the double source job on open, a job switching the model under an edited draft, the hidden chain legend, Ctrl+F, a re-bake that force-saved and picked an archetype by itself, the disk watcher dying after one edit, a job header that said "Prev: rig PASSED", no way to the 3D viewer |
| "Universal" skinning passes (barrier, sibling isolation, centreline/pelvic, hinge, twist, closed-loop healer, rigid islands/armour) | **off by default**. On for every rig they took the gravehound from 39/9 tears to 215/214 and skinned nothing of its tail; every rule is a bone-name substring with `hips` as the sink. To be evaluated one at a time on the bench (P5) |
| Auto-tune ("self-healing rigs") | kept, minus the strategy that wrote audit allowances into rig.json (a PASS with the same rig) and the one that ripped the tearing joint's own seam; the joint nudge now works in the spec's frame; a humanoid past six iterations crashed; a rejected candidate's rig no longer stays on disk |
| Suggest skeleton | see P4 |
| Mesh doctor | OBJ diagnostics; `--heal` now writes the healed copy; non-OBJ sources are reported as not inspected (were "HEALTHY 100"); the silent heal-and-reskin retry inside the rig step is gone |
| Facing detector | opt-in (`forward: "auto"`); unreliable |
| Semantic bone dictionary (`skeletons.detect_convention`) | useful and tested; the placed rig it proposes from a known skeleton has no hand bones and an empty humanoid map |
| Clips: gait engine, root motion, agility clips, quadruped gaits | kept; fixed: the tail whipping over the first frames of every creature cycle, a gallop where every foot was a hind foot, the quadruped walk speed derived from 16 frames instead of 24, `gait: trot/gallop` doing nothing, root motion snapping to 0 at the loop seam. Humanoid feet still slide (FK only); the roll likely puts the head through the floor; unrendered |
| Morph targets, twist bones, digits | dormant or dead: never wired, the morph call had the wrong signature |
| Mocap retargeting (BVH/FBX) + "live web" modal | BVH onto an FK humanoid plausibly works (a test bakes one); FBX inspection was a Windows path injection and is fixed; still runs Blender synchronously inside request handlers, rewrites the rig .blend, ignores IK rigs; the modal's "& Play" now selects the clip |
| Watchdog | timeouts now off unless `AUTORIG_STEP_TIMEOUT` / `AUTORIG_TIMEOUT_<SCRIPT>` is set (60-180 s defaults killed real rigs, matched on labels); the kill-by-name "reap" route is removed; memory guard Linux-only |
| Batch rigger, watch folder | batch: custom builders crashed (fixed), `-j` summary wrong; watch folder: folder drops crash it or are ignored, zips with a top folder unlisted, auto-runs anything dropped. Decoration until reworked |
| Export presets (Unreal/Unity/Godot/Web) | file copies plus generated notes; no conversion; Godot/Web may ship the unrigged source GLB; the web page loads a Google CDN |
| CI | see P4 |

Reviews' remaining findings are in this branch's pull request; the ones worth fixing are in P5.

## Cleanup (2026-09-26)

Measured on the collection with the same Blender 5.2.2: before, gravehound (Tripo quadruped) FAIL 215 combined /
214 bend tears and crawler CHECK; after, both PASS at exactly their Sept 21 numbers (39/9 and 0/0). The samples,
with their invented allowances removed: beetle FAIL 1/5 (was 27/118 with allowances), canine CHECK 4/5 (was 25/62),
wyvern PASS (was FAIL), biped and pedestal PASS. Bone names from a chain called `leg_mid.L` are `leg_mid_1.L`
(were `leg_mid.L_1.L`). Tests: the export tests build their own fixture, the retargeter tests rig the biped once or
skip, the CI check reads the verdict it was ignoring. Three tests still need a look: `test_viewer.test_previews_list`,
`test_spec_editor.test_3` (its source view) and whatever `test_5` inherits from it.

## P5: the auto-rigging experience

What the reviews and the collection run say: the original pipeline (build/tripo chains, envelope skinning, the
graded audit) is sound and deterministic; what was bolted on was unmeasured. The tool is not slow to rig, it is slow
to *converge*: a real model takes a spec, a rig, an audit, a look, a fix, and round again, with no measurement
between rounds and a page that hides the next step. So, in order:

1. **A regression bench before any more rigging code** (2 days). `run.py bench`: rig, trim and audit a fixed set
   (the samples, and a collection's models when `AUTORIG_BENCH` points at one), write one table per run (grade,
   combined/bend tears, worst gap, bleed, head share, seconds) and diff it against a stored baseline
   (`bench/baseline.json`). CI fails on a model getting worse. Every change to `rerig.py` / `placed_rules.py` /
   `make_clips.py` shows its bench diff in the pull request. This is what would have caught the batch's regression
   the day it was made.
2. **Real samples** (1 day). Replace the primitive samples with five CC0 sculpt-like models (a quadruped, a
   humanoid, an insect, a flier, a prop: Quaternius and Kenney packs are CC0), with the creases, loose pieces and
   thin parts the audit exists for. The bench runs on them.
3. **One first-run path in the page** (3-4 days). Drop a model: survey runs, Suggest runs, the editor opens on the
   proposal with the three things to confirm (facing, head and hips, the legs' tips) and one primary button, **Rig**.
   The result shows the grade and the worst three problems as clickable fixes ("leg_front_1.L tears at the knee:
   move the joint / widen joint_blend"), each a one-click spec change and re-rig. Menus fold back to the P1 rule: a
   button that cannot run says why. The viewer is one click from every state, opening on the worst bone with tears
   or bleed showing. The editor and the workbench stop sharing one 3.5k-line module by `if ($("id"))` guards.
4. **Skinning, measured** (1-2 weeks, on the bench). Evaluate the gated passes one at a time; keep only what lowers
   tears or bleed on real models, and rewrite the keepers to use the chain roles the rig knows instead of bone-name
   substrings. Then the two failure modes the collection actually shows: limb-root bleed (the envelope's girdle and
   joint blend, per joint rather than global) and thin-part tears (the rip/weld case, done at welded vertices). Auto-
   tune becomes a bench-scored search over the documented levers (`joint_blend`, `limb_radius`, `girdle_blend`,
   `smooth`, joint positions in the spec's frame) and nothing else.
5. **Clips you can see** (3-4 days). Render a strip of every clip on the bench models into the run's report;
   fix the humanoid walk's sliding feet (IK targets from the gait's foot positions, which it already computes), check
   the roll and the quadruped gaits by eye, put root motion in the manifest. Retargeting becomes a job in the runner
   (log, cancel, no synchronous Blender in a handler) and writes its clip beside the rig, not into the rig .blend.
6. **Cut** (1 day). Remove what has no user until one appears: the export presets, the watch folder, twist bones,
   digits, morph targets, the humanoid "auto-framing". Less to review, less to break.

Done when a dropped sculpt reaches PASS through the page in one sitting, and the bench says so for every change.

## Risks

- **Blender API drift.** The steps are tested on 5.2 LTS. `parent_set(ARMATURE_AUTO)`, the FBX exporter and
  `voxel_remesh` change between versions. Mitigation: a version check, and an audit comparison as the regression test
  before accepting a new Blender.
- **The heuristics were tuned on generated sculpts.** Other sources (other unit scales, other facings, no textures,
  other bone naming) will FAIL more often. P3's manual placement is the escape hatch, and survey flags anything unusual.
- **Long or stuck runs** (bone heat on a dense mesh, the voxel-proxy retries). A streamed log, Cancel by PID, and later a
  timeout per step.
- **Unmeasured changes.** The September 2026 batch shows what happens without a bench: every pass looked right in
  its own test and was wrong on the collection. Nothing touches skinning again without a bench diff (P5.1).
- **GPL-3.0** (Blender's own licence, which scripts that `import bpy` generally share) may put off some contributors.
