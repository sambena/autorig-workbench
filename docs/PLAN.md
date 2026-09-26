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

### P3: click-to-place joints (8-10 days)

- The measure sheet's three orthographic views, made interactive in the page as images with the grid drawn over them,
  so there is no 3D picking to get wrong. Started: the editor's **Flat views** tab runs `measure.py` on the unsaved
  draft (the rig it would build drawn over the model) and a click in a view sets two of a point's three numbers.
- **Click to place** a chain's joints, or just a limb's tip and base (done). Pick beside `tip`, `base`, `points`,
  `head_line`, `jaw` and `rigid_to` boxes, in 3D or on the flat views; drag to move a joint in 3D or flat views,
  mirroring a placed chain across symmetry ($X \to 1 - X$) in one click with intelligent renaming, `stations` placement
  by click/input, and live redrawing of the flat views as points move.
- **`kind: "placed"`** (done): a hand-placed winged builder generalised. Its body-part rules become spec data: which bones
  each part may use (`parts`), which parts blend at a join (`blends`), which welds to rip (`rip_welds`), membranes
  riding only wing spar bones by distance gradients and cut free from the flank (`membranes`), the jaw line (`jaw`),
  and rigid islands (`rigid_islands`).

Done when a model with no spec goes from drop-in to a PASS audit using only clicks and the spec form.

### P4: suggest a skeleton, and open-source readiness (5-7 days)

- **Suggest a skeleton** (done): from survey (Tripo-style or Mixamo bones, if any), `probe_tips` (geodesic tips),
  the model's symmetry plane, and its proportions, propose an archetype and chains with tips. Pure-Python heuristics
  in `autorig/core/suggest.py`, standalone step `autorig/steps/suggest_step.py`, `POST /api/spec/suggest` endpoint, and
  **Suggest skeleton** button in the spec editor (`gui/spec_editor.html` / `gui/spec_editor.js`).
- **Samples:** 3-5 models under CC0 or CC-BY, with their licences in `samples/`: a quadruped, a hexapod, a humanoid,
  a flier and a prop.
- **Docs** (done): architecture guides, CONTRIBUTING.md, and formal JSON schema for `rig.json` (`docs/rig.schema.json`).
- **CI** (written, not switched on): a GitHub Actions workflow on Linux (`ci/ci.yml`, documented in `docs/CI.md`) with cached Blender 5.2.2 LTS, running full unit test suites, Node tests, and headless Blender audit pipeline verifying audit thresholds via `scripts/ci_audit_thresholds.py` and `autorig/cli/audit_all.py`. It sits outside `.github/workflows`, so it does not run until it is moved there.
- **Blender version check** (done): at start-up in `autorig/gui/server.py` and `autorig/cli/run.py`, warns outside tested range (5.2 LTS); inspection functions, state reporting, and tested-range checks in `autorig/core/blender.py` with tests in `tests/test_blender_version.py`.

Done when a fresh clone on a machine with only Python and Blender rigs every sample to PASS through the GUI, and CI
is green.

## R: repair pass (September 2026)

A review of everything added after the public-alpha commits found features that did not work end to end, skinning
passes that misfire on the tool's own bone names, pops in the clips and slow paths. The repair runs in six phases,
one pull request each, code first; models are run through the tool only in R6.

- **R1: bugs that stop features working** (done, untested in Blender).
  - Retarget: Blender reads mocap paths as arguments (a Windows path or a quote in it no longer breaks it, or runs
    anything). The GUI only reads mocap under the models root or the work folder. A retarget runs as a queued,
    cancellable job, and the viewer's dialog sends the action's name. Retargeted clips survive a re-bake and ship
    with the clips. Nothing the mocap file brought in is saved into the rig's file.
  - Watch folder: a drop with no rig.json is surveyed and gets the suggested one. An OBJ's material and textures, a
    glTF's buffers and an FBX's `.fbm` come with it, and whole folders work. A failing item never stops the loop or
    loops forever. A custom Python builder from a drop is never run.
  - Batch: custom builders are read from `rig.builder`. A bad rig.json fails its own model only. A failed audit never
    reports the last run's grade. Clips use the archetype the GUI would. Publishes run one at a time after a parallel
    batch.
  - Processes: Cancel and the timeouts kill the step with every process it started (`taskkill /T` on Windows). The
    reaper only retries processes the tool itself failed to stop. Time limits are per step, picked from the command
    line and far longer (rig and audit 10 minutes). A Blender step whose script raises now exits 1.
  - Auto-tune: rig.json only ever holds an accepted spec (candidates are read through `AUTORIG_SPEC_OVERRIDE`), and the
    rig is rebuilt from it when the last candidate lost. It no longer rips welds or grants itself allowances to pass.
  - Blender glue:
    - Morph targets and twist bones are built instead of failing silently.
    - Weld ripping splits once along the seam.
    - Mesh Doctor `--heal` saves the healed copy.
    - A file Mesh Doctor cannot read without Blender says so.
  - GUI:
    - Unsaved spec edits are never dropped by a job's log, and switching models asks first.
    - Re-bake and auto-tune keep the rig.json conflict check.
    - The help page's links work and it describes the menus as they are.
- **R2: broken skeletons** (done, untested in Blender).
  - Rolls: one roll reference per chain, the limb's bend plane (`core/rig_geom.py`); humanoid rolls by bone, never a
    slope threshold. Neighbours no longer flip 180 degrees, and mirrored chains mirror.
  - IK: one foot rule for building and constraining (a girdle is never in the IK chain; the old code started the chain
    a bone late with a foot). The pole angle is solved for the whole chain's rest. Pre-bend only for limbs, front or
    hind by position, perpendicular to the limb (a sprawling insect knee rises).
  - Joints: stations centred across the limb's own local direction on a percentile, with a capped shift; limbs of
    three bones or fewer are no longer smoothed straight. A joint outside the mesh is moved inside (`keep_inside`).
    Pinches read a real cross-section.
  - Naming: mirrored pairs keep their sides (a T-posed biped's legs); a separate head chain makes the only head; name
    clashes rename the auto-named bone and are logged; the audit reads `_v2` names.
  - Detection: a convention needs its left/right limb names (a creature's hips/spine/head no longer read as Unity);
    the survey records its bounds. Suggest:
    - Tripo legs are found from parents, and the hips are where the hind legs branch; up to eight legs.
    - An upright body gets a Z spine, and a T-posed biped is a humanoid.
    - Limb bases are measured at rig time, not guessed.
    - The facing the tips were measured in is kept.
    - The GUI measures the mesh first.
    - A template says low confidence.
    - No second head.
  - Humanoid: centred on the body (not the bounds), A-pose arms tracked, fingertips from the body only; optional
    five-finger hands (`digits`).
  - Twist bones on this tool's and Rigify's names, on the right half of the bone, driven by swing-twist.
  - The audit warns about joints outside the mesh, joints and rolls that do not mirror, rolls that flip in a chain,
    zero-length bones, clash-renamed bones and rest drift with IK on.
- **R3: tears** (done, untested in Blender): every weight pass fixed, gated to the bodies it is for, and switchable one
  at a time; nothing removed.
  - One body plan per rig (biped, quadruped, multi-legged, other) reaches every pass. The standing-body barrier
    rules and the pelvic seam blend run on bipeds only. The flank barrier runs on quadrupeds and multi-legged bodies,
    front and hind by where each limb leaves the body. The radial barrier runs on multi-legged bodies only: its
    pattern matched a biped's own leg_1..3 and never a hexapod's.
  - Sides are read from side tokens and roles from whole words ("ear" no longer matches ForeArm, "_l" no longer
    _lower). The tail barrier follows the tail's direction, not height. The pelvic band is the hips' width, not the
    arm span.
  - Rigid pieces, the shell, hard splits and pelvic accessories are locked, so the healer no longer blurs them. The
    healer's limit grows with edge length.
  - A write-back creates a missing vertex group instead of dropping its weight.
  - Hinge and twist relaxation are one pass, each joint once. The barrier runs once, after smoothing.
  - Automatic rigid islands and armour pieces stop at accessory size, so garments bend.
  - The humanoid step passes every skin option on.
  - Auto-tune only turns joint_blend and limb_radius when the full envelope reads them, and no longer "enables" passes
    that are on.
  - Schema and SPEC.md list every switch.
  Evidence to start from: an earlier review (closed PR #43, branch `fix/review-cleanup`) re-rigged real models with the new passes (barrier,
  sibling isolation, centreline/pelvic, hinge, twist, healer) on for every rig, and one went from 39/9 to 215/214
  tears; with them off, both real models audited as on 21 September.
- **R4: rigging and animation** (done, untested in Blender):
  - Walk: the swing starts where the stance ended (it began from the heel-strike pose, a 44-degree thigh pop at every
    toe-off), and the pelvis is lowest at heel strike. The walk-to-idle knee and elbow pops are gone. The roll stays in
    place and root motion carries the travel (it used to move the body twice).
  - Root motion: a loop's last key is the whole cycle's travel, not phase 0, and a rig with no root bone (Mixamo)
    moves its hips. The humanoid walk and run travel stride / duty a cycle, the planted foot's speed (the walk moved
    2 strides a cycle at a duty of 0.62, so its feet slid), and clips.json's walk speed says the same.
  - Creature clips: the gallop knows its front feet (every foot was a hind foot: a bound). Quadruped walk frames and
    speed are corrected, the tail wave's phase is radians throughout, and each impulse starts at its own onset.
  - Fingers curl about the right axis and are found on prefixed (mixamorig:) names.
  - Morphs: each clip gets its own shape-key action and NLA track in the clips .blend. The FBX exporter cannot solo a
    mesh's shape-key tracks per take, so the clips FBX is written with them muted and zeroed: a neutral face in every
    take, where it used to get the last clip's. Decimation leaves a mesh with shape keys at full resolution and says
    so in its result, instead of failing.
  - Constraints are keyed on at the start of every clip, so a clip after one that turned them off is not left
    without its IK.
  - Retarget: rotations transfer in world space, with rest alignment on limbs (T-pose to A-pose; the root and torso
    keep their own rest, whose direction is a rig convention). IK is muted while baking and keyed back afterwards.
    Bones evaluate ancestors first, with no one-frame lag. --fps resamples, the result reports the source frames and
    the clip's own, and --all-actions reports each failed action and carries on.
  - Export: engines get the budgeted FBX with textures beside it, where importers look for them by name. Godot takes
    the FBX (4.3+) with no made-up uid. The web package says its GLB is the viewer's full-resolution copy. The Unreal
    and Unity JSON files are labelled as notes. The Unreal FBX is not re-exported Z-up: it is written Y-up, and Unreal's
    Convert Scene turns it on import, as docs/SKELETONS.md says.
  - Not done: the creature walk and trot do not call gait's quadruped walk (the humanoid clips and the creature
    gallop do), there is no budgeted GLB for the web package, and the viewer's preview.glb may list the per-clip face
    actions as clips of their own (<clip>_morph): to check in R6.
- **R5: speed** (done; the results must not change, and tests/test_speed.py checks each new helper number for number
  against the loop it replaced). On the five bundled samples, the whole run (rig, trim, audit, clips) took 23.3 s against
  29.6 s before, the rig step 1.1-1.2 s against 1.6-1.8 s, with every audit figure and every clip key identical:
  - Skin: write_weights writes a bone's rows in a few calls (one remove, one add per distinct weight, nothing for an
    entry that already holds its value), where it made one call per row per bone. The mesh's loose pieces are found
    vectorised and remembered while the topology holds (the passes asked five or six times a rig). skin() logs each
    pass's seconds (`skin_seconds` in the rig log), for R6 to measure.
  - Skeleton: the surface graph is built with numpy, skips the seam-bridging search on a one-piece mesh, and
    remembers each surface-distance run (tips, tube and medial_axis asked for the same ends again and again).
  - Blender: the mesh's Armature modifier is off during the IK pole search (55 scene updates a leg, each re-skinning
    the mesh) and the humanoid's T-pose straightening; steps start with --factory-startup (AUTORIG_USER_PREFS=1 keeps
    the user's preferences). Pictures read and write pixels in bulk. make_clips writes each clip's bone keys a curve at
    a time (AUTORIG_SLOW_KEYS=1 goes back to keyframe_insert for comparison). -noQA skips the rig step's bend picture.
  - Audit: vertex areas, loose pieces and bleed are vectorised; the unused neighbour table is gone.
  - Retarget: what Blender reports about a mocap file or a target rig is remembered per file, size and time.
  - Server and GUI: audit grades are re-read only when the audit changes, the Blender lookup is remembered, each step
    logs its time, preview GLBs have versioned URLs the browser may cache, a dropped event stream resumes where it
    stopped, a job's end is handled once (the stream and a poll both reloaded the model), and job waits follow the
    event stream instead of fetching the whole log every second.
  - Not done: one Blender process for rig + trim + audit, skipping the rig step's FBX when trim rewrites it, and one
    weight matrix handed from pass to pass (passes that write groups directly still sit between them). All three
    change how steps fit together, so they wait for R6's timings to show they are worth it.
- **R6: measured on models** (started, on the five bundled samples only):
  - Baseline: the original tool (c13898b) cannot rig the samples (no "placed" kind), so pre-R5 main is the "before".
  - Each skin pass off in turn, rig + trim + audit: the barrier was the wyvern's whole FAIL (18 bend tears at the
    chest, 0 without it) and the biped's one tear. The cause: the wyvern counted as a biped, and the biped rules cut
    by height bands that assume an upright torso. Two legs under a level spine is now its own body plan,
    "horizontal", which skips them: the wyvern passes (0 tears), nothing else changed. The healer earns its place
    (off: the beetle goes CHECK to FAIL, 5/13 tears). Sibling isolation, centreline armour, hinge smoothing, rigid
    islands: no change on any sample; twist relaxation: 1 tear either way. No pass is worse in every case, so none
    is removed.
  - Winged clips (the wyvern) failed without a published card, and without the rig/triangles/flySpeed fields the
    editor never writes: they now default (the rig folder, the budget, no speed).
  - Four Blender tests had gone stale unseen (their suite skips without Blender): fixed to what the tool now does.
  - Still to do: real models (Sam's collection, with his go-ahead), clip-level checks (foot slide, the gallop's
    order, per-clip morphs in preview.glb, T-pose to A-pose retargeting), and skeleton warnings promoted to grades
    once there is enough data to set thresholds.

- **After R6: the tool-to-game seam** (feedback from a Smeltdown session, 2026-09-26):
  - B, the contract: every clips manifest now says each clip's slot, whether its rate follows ground speed, its
    speed in metres a second and its own wind-up end (docs/FORMATS.md, "The engine contract"). The legacy
    walkSpeed is metres a second (it was the stride times metres per cycle, right only when the rig's longest side
    was 1 unit). windUpEnd was not wrong (a fraction of the 24-frame attack: 14/24), but nothing said of what, so
    each attack clip now carries its own. Smeltdown's side (reading the contract) is its own change.
  - The batch runner and pipeline.py count a rig step that failed inside Blender (it logs the error and exits 0)
    as a failure; they counted it as a pass and went on to trim, audit and animate the stale rig.
  - tail_wave in a creature's walk spec is kept (merge_gait_params dropped every key no preset carries).
  - C, the clip audit (steps/clip_audit.py, grades in core/clip_grades.py): every baked clip played on the
    deformed mesh and graded for foot slide, floor contact, pops, loop seams and left/right reach; run after every
    Clips (GUI and batch), shown beside the skin audit. On the samples it found: the bundled specs had no IK on their
    legs, so every walk was the legless heave (fixed: the samples' legs have "ik": true, and make_clips warns when a
    walker's legs have none); with IK, planted feet still slide at about a quarter of the walk speed (canine,
    biped), the beetle's walk is far off and its feet sink 12-19% of its height in attack, block and the jumps, and
    the canine's trot and gallop pop. Those are the next fixes.

## Risks

- **Blender API drift.** The steps are tested on 5.2 LTS. `parent_set(ARMATURE_AUTO)`, the FBX exporter and
  `voxel_remesh` change between versions. Mitigation: a version check, and an audit comparison as the regression test
  before accepting a new Blender.
- **The heuristics were tuned on generated sculpts.** Other sources (other unit scales, other facings, no textures,
  other bone naming) will FAIL more often. P3's manual placement is the escape hatch, and survey flags anything unusual.
- **Long or stuck runs** (bone heat on a dense mesh, the voxel-proxy retries). A streamed log, Cancel by PID, and later a
  timeout per step.
- **GPL-3.0** (Blender's own licence, which scripts that `import bpy` generally share) may put off some contributors.
