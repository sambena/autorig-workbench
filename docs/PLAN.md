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

### P3: click-to-place joints (done, with rough edges)

- The editor's **Flat views** tab runs `measure.py` on the unsaved draft, and a click in a view sets two of a point's
  three numbers. Pick beside `tip`, `base`, `points`, `head_line`, `jaw` and `rigid_to` boxes, in 3D or on the flat
  views; drag to move a joint; **+ Mirror chain** copies a chain across the middle; `stations` by click.
- **`kind: "placed"`**: chains with body-part rules as spec data (`parts`, `blends`, `rip_welds`, `membranes`,
  `rigid_islands`, `jaw`). `parts` and `blends` are small and sound; `membranes` is half-built (`cut_flank` and
  `root_bone` are read and never used, a forelimb near the wing root can take spar weight); `rip_welds` rips the
  nearest-bone boundary rather than welded vertices. No sample uses any of them.
- Not done: the live "mirror .L/.R" edit toggle was dead until the cleanup; the live bend preview bends only
  `points`/`tip` chains (nothing for a `tripo` rig) about an arbitrary midpoint hinge.

### P4: suggest a skeleton, and open-source readiness (partly done)

- **Suggest** (`core/suggest.py`, `steps/suggest.py`): pure-Python heuristics over the mesh's geodesic tips, symmetry
  and proportions, measured under Blender by the step; the editor's button runs that step (a few seconds). Until
  the cleanup the button passed no measurements at all and returned a fixed quadruped template, and the step's
  spine ran nose-to-rump (hips at the nose). Quality on real sculpts: unmeasured.
- **Samples**: five generated in `samples/` (beetle, biped, canine, pedestal, wyvern) from primitives. They have no
  creases, loose pieces or thin parts, so they exercise the pipeline, not the audit. After the cleanup: biped,
  pedestal and wyvern PASS, canine CHECK, beetle FAIL (1 combined, 5 bend tears). The freely licensed sculpts this
  phase asked for are still to find.
- **Docs**: `docs/rig.schema.json`, CONTRIBUTING.md, help.html (which still describes the earlier page in places).
- **CI**: `.github/workflows/ci.yml` (moved there in the cleanup: it lived in `ci/`, where GitHub does not look, and
  its "threshold check" could not fail). Not yet seen green on GitHub. docs/CI.md.
- **Blender version check**: done (`core/blender.py`, at start-up and in the CLI).

## The September 2026 batch (PRs 5-42)

Thirty-eight pull requests generated with an AI coding tool landed between 22 and 26 September 2026 (~27k lines).
A review on 26 September (six read-only passes over the code, the tests run, and the real collection re-rigged with
the old and the new code) found the batch had not been run on a real model: the collection's audits were all older
than it. Where each feature stands after the cleanup below:

| Feature | State | Notes |
|---|---|---|
| Tears overlay and test poses in the viewer | works | list navigation and the combined pose were broken until the cleanup |
| Viewer camera views (hero / front / side / orbit), clip select, keys 1-9 | works | "humanoid" auto-framing threw on load and matched every quadruped until the cleanup; the view is now chosen once |
| Spec editor: humanoid and custom forms, embedded clip playback, stations, joint dragging, mirror chain | works | |
| Workbench page (menus, console drawer, bulk job counter, samples gallery, rebake buttons) | works, sprawling | one 3.5k-line module serves two pages; feedback lives in a closable drawer; no greyed-out reasons; help.html describes the earlier page |
| Graded audit, tear sites, Audit all | works (unchanged from P2) | the "static prop" case now warns instead of passing silently |
| Extra skinning passes (`placed_rules.py`: skin barrier, sibling isolation, centreline/pelvic garment, hinge smoothing, twist relaxation, closed-loop healer, rigid armour) | **off by default** | on for every rig they took the gravehound from 39 / 9 tears to 215 / 214 and froze its tail; name-based rules with the hips as a universal sink. Opt-in fields, SPEC.md "Experimental skinning passes"; each needs a measured gain before it goes back on |
| Auto-tune ("self-healing rigs") | half-built | its last strategy wrote audit allowances into rig.json to reach PASS (removed); it nudged joints in the wrong coordinate frame (fixed); it left rejected rigs on disk (fixed); its remaining levers are the documented tuning fields |
| Suggest skeleton | half-built | see P4 |
| Mesh doctor | half-built | OBJ diagnostics are real; the GUI said "HEALTHY 100" for every FBX/GLB (now "not inspected"); `--heal` never saved (now writes `<model>_healed.obj` in the work folder); the silent heal-and-reskin retry in the rig step is gone |
| Universal semantic bone dictionary | works | `detect_convention` / `map_bone_to_canonical` are useful; the placed rig built from a recognised skeleton is not (girdle prepended twice, no hand bone, empty humanoid map): SKELETONS.md no longer claims otherwise |
| Twist bones, digits, morph targets | dormant | nothing sets `twist_bones`; `digits` is called by no step; the morph call had the wrong signature (fixed) and no clip keys a shape key |
| Gait engine and clips (run, trot, gallop, jump, roll, block, dodge, transitions) | half-built | fixed: the tail whipped at the start of every creature cycle (a radians/phase mix-up), the gallop treated every foot as a hind foot, the quadruped walk's speed came from 16 frames not 24, `gait: trot/gallop` did nothing, root motion snapped to 0 at the loop seam. Unverified by eye; feet slide on humanoids (FK only) |
| Mocap retargeting (BVH / FBX, multi-action, the viewer's modal) | half-built | BVH onto an FK humanoid bakes; FBX inspection spliced the file path into Python source (broken on Windows, fixed: argv); the routes took any path on disk (now only uploaded files); the worker saves the rig .blend in place and changed its frame rate (fps no longer changed); IK rigs are not handled; runs synchronously inside the request |
| Engine export presets (Unreal / Unity / Godot / web) | decoration | file copies plus generated Markdown and JSON; no axis or format conversion; Godot/web can ship the unrigged source |
| Batch rigger, watch folder daemon | half-built / decoration | custom builders always errored (fixed); folder drops crash or are ignored; zips with a top folder are not recognised |
| Process watchdog | kept, disarmed | it applied 60-180 s timeouts to every Blender run, matched by human label (a model named "auditor" got 60 s); now no timeout unless `AUTORIG_STEP_TIMEOUT` / `AUTORIG_TIMEOUT_<SCRIPT>` says so. Its kill-by-name "reap" route is gone |
| CI workflow | decoration until seen green | see P4 |

### Cleanup (26 September 2026, `fix/review-cleanup`)

What changed, and the measure of it: with the batch as merged, the gravehound (a Tripo quadruped in the collection)
audited FAIL at 215 combined / 214 bend tears with two skinless tail bones; the crawler CHECK. After the cleanup both
rig to exactly their 21 September audits (PASS 39 / 9 and PASS 0 / 0), and the samples, with their invented
allowances removed, go from 3 FAIL / 1 CHECK / 1 PASS to 3 PASS / 1 CHECK / 1 FAIL.

- Rig step: the experimental passes opt-in; the silent mesh-heal retry removed; a placed chain named `leg_mid.L`
  no longer produces `leg_mid.L_1.L`; `rip_welds` no longer crashes on a face that touches two seam vertices.
- Auto-tune: no allowance writing, no rip-weld strategy, joints nudged in the spec's frame, the humanoid path no
  longer crashes at iteration 7, the rig on disk is re-made when the last candidate was rejected.
- Suggest: the editor's button measures the mesh under Blender; the spine runs hips to head; the proposal carries
  the facing it was measured in.
- Clips: the five gait fixes above; the archetype inferred for a `rigid` skeleton is none (a prop), for a winged one
  `flyer`.
- Server: `/api/watchdog/reap` and `/status` removed; mocap routes take only uploaded files; a missing Blender is an
  error the page shows, not a dropped connection; model names are no longer lower-cased on export; a failing job
  `prep` no longer kills the runner; job stdout is closed.
- Viewer: the load crash (`Box3.getSize` with a plain object), humanoid detection, the tears list, playback after
  leaving Tears, the retarget modal's model name, a cross-model audit race, the bbox fallback mapping; a CHECK/FAIL
  opens on the skeleton overlay; the workbench's View menu links to the viewer again.
- Workbench: no second source-view job on open; "unsaved changes" only when the spec differs (not its formatting);
  a running job no longer switches the page away from an edited draft; the chain legend shows; mirror .L/.R edits
  work; Ctrl+F is the browser's again; re-bake saves like Save (no force, no silent archetype); the disk watcher
  keeps running after the first edit; the job header escapes model names.
- Tests: no fixtures from another machine or from gitignored folders; the watchdog tests match its new defaults.
- Docs: SPEC.md lists the opt-in passes and the fields the code reads; the schema has them; FORMATS.md has the new
  clip and event names; CONTRIBUTING and CI.md lost their `file:///var/home/...` links; samples/README.md is true.

Left for later (found in the review, not fixed here): the editor job's doubled log lines (two SSE streams and a
poll); "Render & Test" with no preview.glb shows an empty stage; the model list is stale after Save; drop-on-page
opens the import dialog instead of importing; point-pick CSS missing on the workbench page; undo needs two presses
after Add stations; the batch runner's `-j` summary and stop-on-error; the watch daemon's folder and zip drops; the
retargeter running Blender inside the request handler; the memory guard is Linux-only; the 20-odd magic numbers in
each skinning rule; `viewer_logic.js` holding 770 lines of editor and workbench code; help.html.

## P5: the auto-rigging experience

The pipeline's core (the `tripo` and `build` skeletons, envelope skinning, the graded audit) is sound and
deterministic: the same model audits to the same numbers a week apart. What makes the app frustrating is
everything around it: a model dropped in with no skeleton needs a spec that takes many decisions, each rig-and-audit
loop takes half a minute, the page shows forty menu items and no next step, and the September batch added fifty
heuristics nobody measured. The plan, in order, each step measured before the next:

1. **A regression bench, first.** `run.py bench`: rig, trim and audit a fixed set (the five samples, and any
   collection the machine has: `AUTORIG_BENCH_MODELS`), compare grade, tears, gap, bleed and head share to a stored
   baseline, and print what moved. Every change to skinning or the audit shows its bench before it merges; CI runs
   it on the samples and fails on a regression. This is what would have caught the batch in its first PR.
2. **Real samples.** Replace or add to the primitives with five freely licensed sculpt-like models (CC0: a
   quadruped, a humanoid, an insect, a flier, a prop) so the tool is tuned on what people drop in. The collection's
   own creatures stay the private bench.
3. **One first-run path in the page.** Drop a model; Survey and Suggest run; the editor shows the proposal and the
   three things to confirm (facing, head and hips, the legs); Rig; the grade with its worst three problems, each a
   click to the joint or the field that fixes it. One primary button per state, greyed with the reason (P1 had
   this); progress and errors where the eye is, not in a drawer. Trim the menus to what that path needs; the rest
   is a "More" menu. Rewrite help.html for the page that exists.
4. **Skinning, measured.** Re-enable each experimental pass on its own against the bench and keep only what lowers
   tears or bleed on real models; drive every rule from the chain roles the rig already knows, never from bone-name
   substrings; then the two failure modes the collection actually shows, limb-root bleed and thin-part tears, with
   a fix each that the bench proves. Auto-tune becomes a search over the documented levers (`joint_blend`,
   `limb_radius`, `girdle_blend`, `smooth`, a joint moved in the spec's frame) scored by the bench, and never by an
   allowance.
5. **The viewer as the QA seat.** Reachable from every state; Tears or Bleed on by default when the grade is not
   PASS; a before/after toggle after a re-rig; the clip that plays is the one the audit posed.
6. **Animation.** Look at every creature walk after the gait fixes (render strips, not just numbers); IK feet for
   the humanoid gait so they stop sliding; root motion recorded in the manifest; retargeting as a proper job with
   Cancel, and IK muted on creature rigs.
7. **Cut what nothing uses.** Export presets, the watch daemon, twist bones, digits, morph targets and the mocap
   modal move to `contrib/` or go, until a user asks for them. Less to keep true.

Done when a fresh model of each bench archetype goes from drop-in to PASS with at most three confirmations and one
re-rig, and the bench is green on every merge.

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
- **Generated code without a bench.** The September batch shows the failure: plausible, tested-in-isolation
  heuristics that wreck real rigs. P5 step 1 is the mitigation; nothing touching skinning merges without its bench.
