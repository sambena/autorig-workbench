# The rig pipeline

Source exports are never touched. Every model gets a `rigged/` folder beside its source:

    <models root>/<model>/rigged/<model>.blend   open and animate (mesh, armature, texture)
    <models root>/<model>/rigged/<model>.fbx     for the engine: deform bones only, no controls

`<work>/qa/<model>.png` is each model's bend test (top row: the new skeleton at rest, from the side, from above and
from the front quarter; bottom row: posed). `qa_overview.py` puts all of them on one page. The models root is
`AUTORIG_MODELS`, the work folder `AUTORIG_WORK` (default `<models root>/_autorig`).

## The steps

| Step | Script | Runs in | What it does |
|---|---|---|---|
| Survey | `survey.py`, `facing.py` | Blender | what the source holds (skeleton kind, bones, size, how much is skinned) and four views to read `forward` from |
| Measure | `measure.py`, `probe_tips.py` | Blender | orthographic sheets on the spec's 0..1 grid with the current rig drawn over them; where a boneless model's limbs end |
| Rig | `rerig.py`, `rerig_humanoid.py`, or a custom builder | Blender | builds the armature from the spec, skins it, writes the `.blend`, the `.fbx` and the bend test |
| Trim | `decimate.py` | Blender | cuts the FBX to the budget, at most four influences per vertex, and writes it at the rest pose |
| Audit | `audit.py` | Blender | PASS, CHECK or FAIL on the engine FBX (below) |
| Clips | `make_clips.py` | Blender | authors the archetype's clips and writes them for engines (FORMATS.md). A standing model's `death` / `explode` ends with its lowest point on the floor, measured on the skinned mesh frame by frame (never under it on the way down) |
| Publish | `publish.py` | Python | writes the model card and the group's index |
| Preview | `preview_glb.py` | Blender | `<rig folder>/preview.glb` (mesh, armature, every clip) and `preview.json`, for the GUI's 3D viewer only |
| Source view | `source_preview.py` | Blender | `<work>/source/<model>.glb` (the source mesh as it came, no skin) and `.json` (its own joints), for the spec editor only |

`cli/pipeline.py` chains rig, trim and audit for several models (`-preview` adds the preview); the GUI's Run all adds
publish, clips and the preview.

## Conventions of a rebuilt rig

- Faces Blender's **-Y** (front view shows its face); its left is **+X**, so `.L`/`.R` bones mirror properly
  (pose-paste-flipped works). Walkers stand on z=0 over the origin; swimmers and fliers are centred on it.
- `root` > `hips` > `spine_N` > `neck` > `head`, `leg_front_N.L`, `leg_hind_N.L` (or `leg1..n` for many-legged),
  `tail_N`, `ear.L`, `jaw`, `wing_*`, `fin_*`, `tentacleN_*`... Shell creatures and props have one rigid `body`.
  SKELETONS.md has every archetype.
- Bones run joint to joint and are connected down each chain; rolls are consistent (legs: z forward; spine: z up).
- Every leg has an **IK foot control** `ik_<leg>` (bone collection *Controls*): move it and the leg follows; the foot
  copies its rotation. Disable the IK constraint on the shin for FK. Controls are not exported to FBX.
- Skin: Blender's bone-heat weights, through a watertight voxel copy where a sculpt's loose pieces defeat the solver.
  Small loose pieces (buckles, teeth, a lamp) ride one bone whole so they never stretch; shells are rigid on `body`;
  at most 4 influences per vertex, normalised. Where bone heat hands loose pieces to the wrong bone, `rigid_to` in the
  spec assigns them by region.

## Sources

FBX (with its `.fbm` texture folder), GLB, glTF and OBJ. A model folder holding several exports uses the one with its
own textures (an FBX beside its `.fbm`, or a GLB), then the shallowest, then FBX before GLB before glTF before OBJ,
and says so in the log. glTF's split vertices are welded back on import, so bone heat sees one surface. Tripo-style
exports are supported directly: a `bone_N` animal skeleton is reused as joint positions (`kind: "tripo"`), a Mixamo
humanoid skeleton is recognised by survey, and the two downloads Tripo offers (textured and bare mesh) are told apart.

## Running it

    python -m autorig                                              the GUI (README)
    python autorig/cli/pipeline.py wolf,moth                       rig -> trim -> audit, with a table
    python autorig/cli/run.py rig wolf                             one step by name (run.py help lists them)
    python autorig/cli/audit_all.py wolf -render 0                 audit only
    python autorig/cli/run.py audit-all                            audit every rigged model, a table worst first
    blender -b --python autorig/steps/survey.py -- [<group>]      what each source export holds
    blender -b --python autorig/steps/facing.py -- -only wolf     four views, to read `forward`
    blender -b --python autorig/steps/measure.py -- wolf          the measuring sheet
    blender -b --python autorig/steps/probe_tips.py -- wolf       where a boneless model's limbs end
    blender -b --python autorig/steps/make_clips.py -- wolf --preview <dir>
    python autorig/steps/publish.py Creatures -only wolf
    blender -b --python autorig/steps/preview_glb.py -- -only wolf    the viewer's GLB (or -file <rig.blend|fbx>)
    python autorig/cli/qa_sheets.py <work>/qa <dir> 2              labelled sheets of the bend tests (needs Pillow)

A new model needs a `rig.json` (SPEC.md). For a boneless one: survey it, read `forward` off the facing views, run
`probe_tips.py`, match the printed tips to the creature's limbs, list them, and check the result on `measure.py`'s
sheet.

## The rules (A-F)

A one-pose bend test passes rigs that are still broken in ways a number catches: a head bone owning 0.4% of the
surface, leg bones owning a shell, a leg rooted inside the belly. These rules are built into the rig step, so every
model gets them:

- **A. Head to snout, jaw.** A forward-facing head bone is extended along its own line to the front of the face
  (`head_to_snout`), or cut from the spine at a measured `head_line`. A spec can add `jaw` (measured): everything under
  the mouth line ahead of the hinge moves from the head to the jaw, blended at the lips.
- **B. Torso envelope.** After bone heat, the torso (spine chain, abdomen, head, neck, girdles) is weighted along the
  spine by projection, split between neighbours at the plane bisecting each joint and blended across it
  (`joint_blend`, default 0.4 of each bone). Each limb owns only a capsule round itself, radius measured from the limb,
  plus spikes and claws whose nearest bone is its own, faded in over the first third of its first bone. A limb that is
  its own loose piece owns exactly that piece. Two bones leaving one point in opposite directions (a thorax and an
  abdomen) split along the plane bisecting that V. Girdles blend in by distance.
  `envelope: "root"` (people) keeps bone heat, which is already the smooth diffusion an armpit and a hip need, and
  applies only the root rule: no limb weight behind the plane where the limb starts. A girdle pass then gives each
  clavicle a share of the spine's weight around it.
- **C. Limbs from the body surface.** A `tip` limb starts at `base`, measured with `measure.py`, or, for a limb that is
  its own loose piece, where that piece comes out of the body. `base_f` is only the fallback for specs not yet
  measured. A girdle adds a shoulder or pelvis bone, link 0 of the chain (`leg_front_0.L`), from the spine out to the
  root. `parent_nearest` hangs a limb from the body bone its root is nearest.
- **D. Names.** A chain near the centre plane gets no side suffix (`tail_3`, not `tail_3.R`); `centre` overrides.
  `root` is reserved for the armature's root, so a spine bone can never become `root.001`.
- **E. Four influences.** Trim cleans, limits to four and normalises after the cut. Static models also get a planar
  dissolve before the collapse (flat panels kept flat, no spikes), and so do rigs of rigid parts (every vertex on one
  bone: nothing bends); other rigged ones do not, because long dissolved triangles tear across joints. On a rigged
  mesh trim merges coincident vertices only where their weights agree, so a seam a builder cut on purpose (the
  Dragon's hand from its thigh, its membrane from its forearm) stays cut; the plain merge it used before re-welded
  them with averaged weights. The FBX is written at the rest pose, whatever clips the `.blend` holds.
- **F. Mirrored chains match.** The audit warns when a chain and its mirror differ in bone count.

## QA: audit.py

`audit.py` measures the engine FBX against five pass limits: weight bleed at most 2%, no tear edge (an edge stretched
past 2x) in the combined pose or in any single-joint 40-degree bend, a head (+ jaw) owning at least 2.5% of the
surface, and at most 4 influences. Twist tears, limb reach over 0.2 and asymmetric mirrored chains are warnings. A
spec can set `audit: {...}` allowances, with the reason in its notes. A tear must also open a real gap (over 0.4% of
the model's size): a micro-edge where two surfaces of a sculpt meet reaches 10x on a weight difference of 0.05 and
opens nothing visible (`tear_edges_raw` keeps the plain count). People are best held to a Mixamo rig's own measured score instead of
zero: the combined pose puts both arms overhead on an arched back, where linear-blend skinning stretches the shoulder
blades on any humanoid. Bleed counts a vertex owned by a bone that is neither its nearest, next to it, nor upstream
of it; `bleed_legacy_pct` also counts upstream ownership.

### Grades

A pass limit of zero tears made nearly every rig a red FAIL, though one tear on one extreme bend and 150 across the
body are very different rigs. Each check is graded instead (`autorig/core/grades.py`), and the audit takes its worst:

- **PASS**: within its limit (allowances included). Exactly the old pass.
- **CHECK**: past its limit but inside a warn band. Look at it in the viewer before trusting it.
- **FAIL**: past the band. Clearly broken.

| Check | PASS | CHECK | FAIL |
|---|---|---|---|
| Tears, combined pose | 0 | 1-12, and the widest gap at most 8% of the model | more than 12, or a gap over 8% |
| Tears, single bends (the worst bone) | 0 | 1-12, and the widest gap at most 5% of the model | more than 12, or a gap over 5% |
| Bleed | at most 2% | at most 3% | over 3% |
| Head (+ jaw) share, where there is a head | at least 2.5% | at least 1.5% | under 1.5% |
| Influences per vertex | at most 4 | (no band) | over 4 |

Why these numbers. They come from auditing a collection of about twenty generated creatures and people. Tear
counts fell into two groups: 1, 3-4, 8, 10-11 (a weld under an arm, a wing root on a full 40 degrees, rigs already
accepted with an allowance), and 22-57, 32-85, about 100, 126-159 (a whole region moving with the wrong bone). The
band ends at 12, between the two. The gap is how far the worst tear edge grows, as a share of the model's longest
side: a count cannot tell one sliver from one hole. In the first group the widest gap was 1-3.5% on a single bend
and 5.5-7% in the combined pose, which bends every joint at once; in the second, 9-15%. So a single bend may open
5% and the combined pose 8% before even one tear fails. Bleed and head share get a narrow band (one point) because
both were failures a picture missed: a head at 0.4% still fails, one at 1.8% is worth a look. Influences have no
band: an engine takes four and drops the rest.

An allowance moves the whole band: a model allowed 8 combined tears passes at 8 and is CHECK up to 20. Tears within
an allowance pass whatever their gap, since the allowance was written knowing them. The raw numbers are never
softened; the JSON keeps every count and gap.

`pass` in the verdict and on the card is still the strict result, `grade == "PASS"`, so anything that read it reads
the same answer as before; `grade` is new beside it. `audit_all.py` (and `pipeline.py`, which ends with it) exits 1
only when a model FAILs; `-strict` makes CHECK fail it too. An audit written before the grades is graded from its
counts when read (no gaps were recorded, so the count alone decides).

### Where the tears are

For a viewer to mark them, the audit JSON also carries:

- `tears`: `worst_gap_pct`, `worst_bone` (the bone whose single bend tears most, else the bone owning the combined
  pose's biggest cluster), `bones_tearing`, and `by_bone` (each bone's bend and twist tear counts and gaps).
- `tear_sites`: one entry per pose that tears: the combined pose first (`pose: "combined"`, with the angles of every
  bone in `combined_pose.angles_deg`), then each single bend or twist (`pose: "bend"` or `"twist"`, `bone`,
  `rotation_deg` about the bone's own axes). Each holds `edges`, `worst_gap_pct`, up to 12 `clusters` (tear edges
  within 5% of the model's size of each other: `at` in the imported FBX's world space, Blender Z up, `at_bbox` as
  fractions of the model's bounding box, `edges`, `gap_pct`, the owning `bone` and top `owners`), and up to 40
  `points` (`[x, y, z, gap_pct]`, widest first). Positions are the edges' midpoints at rest.

The GUI's `/api/audit?name=<model>` serves the whole JSON.

## Known rough edges

- Paper-thin loose strips (ribbons, fins) follow the nearest chain; give them chains of their own if they must move.
- Robes and skirts sculpted as one mesh with the legs stretch at the hem on a long stride. That is the sculpt, not the
  weights; cloth bones would be the fix.
- Bend tests are stills of one strong pose. They catch tearing and wrong ownership, not everything an animation will.
