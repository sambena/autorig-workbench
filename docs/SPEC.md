# Specs: `rig.json` and `autorig.json`

Everything the tool knows about a model that it cannot measure lives in one file beside the model, `rig.json`.
Settings for the whole collection live in `autorig.json` at the models root. Both are plain JSON (no comments: put
reasons in `notes`). The code reads them in one place, `autorig/core/spec_store.py`.

    <models root>/
      autorig.json                 collection settings (optional)
      Creatures/                   a group (optional: models may sit directly under the root)
        wolf/
          wolf.fbx  wolf.fbm/      the source export (or .glb, .gltf, .obj)
          rig.json                 the spec
          rigged/                  written by the tool
          clips/                   written by the tool
          model.json               the card, written by publish

A model is named by its folder, and names are unique across the root.

## `rig.json`

```json
{
  "schema": "autorig-spec/1",
  "rig":      { "kind": "build", "forward": [0, -1, 0], "chains": [ ... ] },
  "humanoid": { ... },
  "budget":   2000,
  "clips":    { "archetype": "walker", "display": "Wolf", "category": "Creatures", "attack": "bite" },
  "card":     { "metres": 1.6, "role": "creature" },
  "notes":    { "rig.audit": "why the allowance", "budget": "why this budget" }
}
```

Every section is optional. A model with no `rig` section can still be surveyed and its facing rendered; the GUI greys
out Rig and says why.

**The spec editor** (Edit spec in the GUI, README) edits this file by clicking: bones on the source model's own
skeleton, points on the model or on the flat views. Its form is built from the schema in `gui/spec_api.py`
(`RIG_FIELDS`, with a label and help for each field below), which also checks a spec before it is saved: the
`schema` line, each field's type and range, the source's own bone names (with the `<bone>_m` copies `mirror` makes),
a head and hips that are two bones, a build chain placed exactly one way, and an audit allowance with its reason in
`notes["rig.audit"]`. Fields it does not know are kept and only warned about. It writes the file two-space indented,
one key a line for the top level and each section, anything that fits in 160 columns on one line, and a list of
chains one chain a line (the style of the files in this document); the file it replaces is kept as `rig.json.bak`.

### Coordinates

Points in a spec are `[x, y, z]` in 0..1 of the model's bounds **after** the tool has turned it to face -Y
(`forward`, below): x 0 is its right, 1 its left; y 0 is the nose, 1 the tail; z 0 the bottom, 1 the top.
`measure.py` draws this grid over three orthographic views, with the rig the spec currently builds on top; that is how
points are read.

### `rig`: how to rig it

`kind` picks the builder:

| kind | For | Script |
|---|---|---|
| `tripo` | a model that came with a Tripo-style animal skeleton (`bone_0`, `bone_1`...): its joints are reused as positions only, and a new armature is built from them, re-rooted at the hips | `rerig.py` |
| `build` | a model with no skeleton (or a useless one): chains are traced through the mesh | `rerig.py` |
| `placed` | hand-placed chains with body-part rules: which bones each part may use, blending at joins, welds to rip, membranes riding only wing spar bones and cut free from the flank, jaw line, rigid islands | `rerig.py` |
| `humanoid` | a person: the standard humanoid skeleton (SKELETONS.md), joints measured at the heights in the `humanoid` section | `rerig_humanoid.py` |
| `custom` | a hand-written builder for one model, `builder`: a script in the model's folder, run through `run_builder.py` | yours |

Common to all kinds:

| Field | Meaning |
|---|---|
| `forward` | the way the model faces in its source file, e.g. `[1, 0, 0]`. Read it off the facing views (Survey), never guess: wrong, and the tips land on the wrong limbs while the rig still builds. |
| `straighten` | sculpted at an angle: turn it exactly (otherwise to the nearest quarter turn) |
| `origin` | `"center"`: centre the model on the origin (swimmers, fliers). Default: feet on z=0. |
| `skeleton` | the archetype written on the card (`quadruped`, `hexapod`, `octopod`, `serpent`, `winged`, `floater`, `rigid`, `humanoid`) |
| `rig_folder` | where the rig is written and read (default `rigged`) |
| `audit` | allowances: `{"combined_tears": 8, "bend_tears": 4}` loosens (or tightens) audit.py's pass limits for this model, and moves its CHECK band with them (PIPELINE.md, "Grades"). Always say why in `notes["rig.audit"]`. A model with no `rig` section of its own (lifted from another pack, rigged there and only cut to budget here) puts `audit` at the top level of its rig.json, with the reason in `notes["audit"]`. |
| `neck` | `k`: the last `k` links of the body chain before the head are the neck, named `neck_1..k` (SKELETONS.md, quadruped), so the card lists all of them as the neck. Without it only the top link is `neck`, and the rest count as spine. |
| `keep_inside` | default `true`: a joint that ends up more than 1% of the model's size outside the mesh (a chain's last point, a tip, excepted) is moved into the middle of the part it is beside, and listed in the rig log as `joints_moved_inside`. `false` keeps joints exactly where they were placed. |
| `twist_bones` | `true`: twist bones on the upper arms, forearms and thighs (Mixamo, Rigify and this tool's own names), driven by the twist alone (swing-twist), each on the half of its bone its weights cover |
| `morph_targets` | `true`: facial shape keys (blinks, jaw open, smile, `viseme_aa`) on a model with a head bone |

`kind: "tripo"`:

| Field | Meaning |
|---|---|
| `head`, `hips` | joints at the two ends of the body (which way it faces; the spine runs between them) |
| `chains` | `{role: [first joints]}`: each role lists the first joint of every limb that plays it (both wings' first joints for a pair); a chain runs from its first joint down the deepest line of joints below it, and stops at another named chain or a leg. Chains not named are guessed: off the head an ear or a jaw, off the rump a tail, otherwise an extra chain |
| `delete` | joints (with everything under them) to throw away |
| `mirror` | chains to copy across the centre plane (the new joints are named `<joint>_m`) |
| `legs` | joints at the top of each leg; these get IK foot controls |
| `shell` | a rigid body on legs: everything the legs do not own goes to this joint's bone |
| `nodeform` | joints whose bones carry no skin |
| `body: "single"` | a shell on legs: one rigid body bone with the listed limbs hanging off it; needs `forward` |
| `body_height` | for `body: "single"`, the body bone's height (0..1) |
| `add_tail` | `{"from": 0..1 along the body, "bones": n, "width", "above"}`: a tail the source skeleton gave no bones |
| `head_line` | `[start, tip]`: the head bone runs from `start` (the spine is cut there) to `tip` (rule A) |
| `jaw` | `{"hinge": [x,y,z], "tip": [x,y,z], "band": 0.15}`: a jaw bone; under the mouth line ahead of the hinge moves from the head to the jaw |
| `girdle`, `girdle_reach` | roles whose first bone is really a girdle (scapula): `["leg_front"]` |
| `move` | `{joint: [x, y, z]}`: a source joint put where it belongs (measured), before anything is built from it. For a joint the generator planted outside the body (a scapula's top on the spikes over the withers) |
| `reparent` | `{joint: parent joint}`: a chain the source hung from the wrong joint (a moth's wing tails hung from the hips). Applied after `mirror`, so `<joint>_m` can be named |

`kind: "build"`: `chains` is a list; the first chain is the body. Each chain is one of

| Chain | Meaning |
|---|---|
| `slice: [y0, y1]` | the body's middle, station by station along y (a spine, a tail); `stations` gives the joints' y outright |
| `tube: [start, end]` | the middle of whatever joins two surface points (a serpent, a tail) |
| `tip: [x,y,z]` | a limb known by where it ends: one bone runs straight from the body, or several follow the limb. `base` is where it leaves the body (measured); without it, a limb that is its own loose piece starts where the piece leaves the body, and `base_f` is the old fallback fraction. |
| `points: [[x,y,z], ...]` | joints given outright |

and takes `name`, `role` (defaults to the name), `bones`, `names` (bone names instead of numbered ones), `parent:
[chain, bone index]`, `parent_nearest` (hang from the body bone its root is nearest), `ik` (an IK foot control; give a
leg 3 bones so it has a foot to hang it on), `width`, `first`, `snap_end`, `centre` (force a centred / sided name).

Skinning options (both kinds):

| Field | Meaning |
|---|---|
| `envelope` | after bone heat: `"full"` (the torso belongs to the spine, each limb to a capsule round itself, rule B; `joint_blend` and `limb_radius` tune it), `"root"` (bone heat kept, but no limb weight behind the plane where it starts: it fades in over the first third of the limb's first bone, or of a torso bone's length when the first bone is shorter, and what it gives up goes to the torso bones, shared by what the vertex already had and by distance), `false` (plain bone heat). Default: `"root"` for every kind. |
| `rigid_pieces` | loose pieces up to this fraction of the largest ride one bone whole (default 0.12; with armour on, `rigid_island_max_extent`, 0.25, so a garment still bends) |
| `rigid_single`, `soft` | every loose piece on one bone; chains exempt from rigid pieces |
| `rigid_to` | `[[bone, [x0,y0,z0], [x1,y1,z1]], ...]`: a loose piece whose middle is in the box rides that bone |
| `rigid_parts` | a machine of parts: the main piece rides the body, every other piece its nearest bone. `"listed"`: only the pieces in `parts` ride their bones, everything else the body |
| `parts` | `[{"bone", "at": [x, y, z], "verts": n}, ...]`: the loose piece of `n` vertices (within 2%) whose bounds centre is nearest `at` rides `bone` whole. For a moving part inside another (a fan's rotor in its duct), where nearest-bone cannot tell them apart. Give each part its own chain, head at its hub and length along its axle, so turning the bone about its own Y spins the part in place |
| `hard_split` | `{"bone", "else", "above"}`: everything above a height on one bone, the rest on another (a lid) |
| `skinning` | `"bone-heat"` (the default: bone heat on the mesh itself; a watertight voxel copy is skinned instead only when bone heat leaves over 5% of the mesh bare, its weights copied back by nearest point) or `"proxy-transfer"`: always skin the voxel copy, and carry its weights back by robust transfer (core/weight_transfer.py, after Abdrashitov et al. 2023): a vertex within `transfer_distance` (a fraction of the longest side; default 2.5 voxels) of the copy's surface whose normal agrees within `transfer_angle` (30 degrees, either way round) interpolates the closest point's weights; the rest (inner shells, folds, pieces dipping inside the body) are inpainted smoothly from those. `proxy_voxel` sets the voxel size tried first. The skin passes below run after it as usual. Measured 2026-10-17: it does not fix the bend-test tears of the samples or a Tripo rooster, because those come from the passes below, not from bone heat (see docs/PLAN.md, "Skinning: where the bend tears come from") |
| `rest_arms` | Humanoids (rerig_humanoid): `"t-pose"` (the default: the arms are straightened out along X and that is the rest pose, mesh and all), `"as-modelled"` (the upper arms keep the slope they were sculpted at, flattened into the body's plane; the forearms and hands are lined up with them), or a number: the arms rest straight that many degrees below horizontal (0-80). The nearer the rest is to how an engine holds the arms, the less the shoulders fold there, and the more a raised arm tears. Tornado Titans' Ice_Age26_Tripo through the full pipeline (stand folds per 10k triangles, combined/bend tears, bleed): t-pose 21.7, 34/42; as-modelled 17.0, 45/40; 50 10.3, 62/83; 70 0.3, 65/94, bleed 2.27%. For a character that stands, walks and talks (arms below shoulder height), 50-60 is a good trade |
| `smooth` | weight smoothing passes (a thick body's patchy bone heat); run before the barrier, so its cuts stay cuts |
| tuning | `joint_blend`, `girdle_blend`, `limb_radius` (these three: the full envelope only), `spike_reach`, `envelope_skip`, `head_to_snout` (false to keep the head where it is), `centre` |

The skin passes after bone heat, in the order they run. Each has its own switch, so they can be tried on and off one
at a time; all are on unless said. They know the model's body plan (biped, quadruped, multi-legged, other, from its
legs per side; `rig_humanoid` is a biped), and a rule meant for one plan runs only on it. Whatever a pass cuts or binds
rigidly on purpose (a hard split, the shell, a rigid piece or island, a pelvic accessory) is locked: the passes after it,
and the healer at the end, leave those vertices alone.

| Field | Pass |
|---|---|
| `barrier` | air gaps: no left leg on the right side (and so on), a seam fade between the legs to the hips (`crotch_barrier`, width; by default 3% of the size but never more than half the distance from the middle to a leg, so legs close together under a robe keep their feet), arms off the head and neck, appendages (ears, antennae, fins: whole words, so never a *fore*arm) kept near their root. The standing-body rules (arms off the hips and legs by height, legs off the chest, the head's top, feet off the head) run for bipeds only. Sub-switches: `armpit_barrier`, `flank_barrier` (front limbs off the rump and hind limbs off the chest: quadrupeds and multi-legged, front and hind by where each limb leaves the body; faded over the middle half between the front and hind limbs, the weight going to the torso bones beside each vertex), `tail_barrier` (the tail off the thighs, and off the body in front of its root, along the tail's own direction), `radial_barrier` (each leg of a multi-legged body in its own sector) |
| `sibling_isolation` | parallel appendages (legs of one side, tentacles, wing fingers) off each other's far ends |
| `centerline_armor` | small loose pieces across the pelvis (a fauld, a buckle: under 10% of the mesh) ride the hips; on a biped the crotch seam blends to the hips across the hips' own width |
| `skirt` | off unless set (`true`, or `{"radius", "fade", "floor", "seam", "knee_blend"}`, fractions of the height): a long robe, gown or skirt sculpted as one mesh with the legs. Below the hips, cloth more than `radius` (0.045) from a leg's axis (hip, knee, ankle) and from its foot (ankle, ball, toe) shares itself between the legs and the hips: the leg's share falls over `fade` (0.12) to `floor` (0.3), the two legs blend across the middle over `seam` (0.04) either side, and below the knee the share moves to the shin over `knee_blend` (0.15) of the leg. The legs and boots themselves are left as bone heat made them. Pairs with the humanoid `x.leg` |
| `arm_reach`, `feet_on_legs`, `pin` | body-part rules for a dressed humanoid, each off unless set (fractions of the height). `arm_reach` (e.g. 0.14): arm bones only own vertices within this distance of the upper arm, forearm or hand, and never the torso column (nearer the middle than the shoulder joints and below them); the rest goes to the nearest spine, neck or head bone, so hair and a robe's chest stop lifting with a raised arm. `feet_on_legs` (`true` = 0.035, or a distance): below the knee, vertices near a shin, foot or toe bone belong to those alone, shin above the ankle, so a boot holds together. `pin`: `[[bone, [x0,y0,z0], [x1,y1,z1]], ...]` boxes (0..1 of the bounds) whose vertices ride the bone whole, for pieces welded into the rest (a rosary, a buckle); `rigid_to` needs loose pieces |
| `drop_floating` | a fraction of the model's size (e.g. `0.03`): loose pieces whose nearest point is farther than this from the body are deleted before rigging. A trimmed generated model leaves scraps behind (a sleeve cut short left two 15-17 cm off a robe); bone heat hands them to the nearest bone and they hang in the air. Off by default; every piece more than 1.5% of the size off the body is named in the rig log (`floating`) either way. A rosary, buckle or badge lying on the body is never more than a few millimetres off and stays. |
| `follow` | `[[[x0,y0,z0], [x1,y1,z1]], ...]` boxes (0..1 of the bounds): loose pieces whose middle is in a box take the weights of the body under them (the mean of the 8 nearest body vertices; a small piece, under a tenth of the size, one set for all of it so it keeps its shape), after every other pass, except boots `feet_on_legs` claimed, so a piece laid over the body (a hood over the back of the head, a plate on the chest) moves exactly with the surface under it. Without it such a piece rides one bone whole and parts from a surface that bends (12 cm on a hood over a neck-weighted head). |
| `limb_pieces` | `true` (humanoids): every loose piece but the largest (a sleeve, a boot, hair, a hood modelled as parts of their own, as Tripo makes them) belongs to one limb: the one carrying most of it (an arm, a leg or the body), plus where it joins the body (an arm the upper spine, a leg the hips). Bone heat let a bell sleeve's hem by the hips take the hips and a thigh (its edges stretched 8x) and the hood take an arm. Locked, so the healer leaves it. |
| `hinge_smoothing`, `twist_relaxation` | steep weight steps eased across each joint, once per joint: shafts that twist (spine, neck, arms, legs) with the twist settings (`twist_passes`, `twist_max_gradient`, `twist_radius_scale`), other joints (tails, wings, fingers) with the hinge ones (`hinge_passes`, `hinge_max_gradient`, `hinge_radius_scale`); `hinges` / `twist_pairs` name the pairs outright (a joint listed in `hinges` takes the hinge settings) |
| `rigid_islands` | `"auto"` (the humanoid default; off for the other kinds unless set, or `rigid_armor` / `armor` / `accessories` is): small loose pieces ride one bone (at most `rigid_island_max_share` of the vertices, 0.1, and `rigid_island_max_extent` of the model's longest side, 0.25, so garments still bend); a list names them; `false` / `"off"` for none. `rigid_armor`, `armor`, `accessories` turn it on too |
| `auto_heal` | the last pass: a weight step steeper than `heal_max_gradient` (0.2, more on a long edge, less on a short one) across an edge is eased (`heal_passes`, `heal_blend`), except on locked vertices |

### `placed` body-part rules

For `kind: "placed"`, hand-placed chains (like `build`) are augmented with body-part rules that stop bone heat from bleeding across limbs and membranes:

| Field | Meaning |
|---|---|
| `parts` | `{"<part>": {"bones": [...], "allow": [...], "deny": [...]}}` or `{"<part>": ["bone1", "bone2"]}`: which bones each part may use. Disallows cross-bleed (e.g. forelimbs never take wing weights, torso limited to spine) |
| `blends` | `[{"bone": child, "with": parent, "radius": r, "fade": f}, ...]`: smooth weight blending across specified joins (e.g. limb root to torso capsule) |
| `rip_welds` | `[["boneA", "boneB"], ...]`: splits coincident welded vertices along seams between parts that move apart (forearm to thigh, wing tips to tail) so bone heat and mesh trim do not pull across the gap |
| `membranes` | `[{"name", "bones": [...], "root_bone": root, "cut_flank": true}, ...]`: wing and web membranes riding only wing spar bones by distance gradients, cut free from the flank outside the wing root |
| `rigid_islands` | `[{"bone", "at": [x,y,z]}, ...]`: loose pieces or armour plates (pauldrons) that ride a bone 100% rigid without bending |
| `jaw` | `{"hinge": [x,y,z], "tip": [x,y,z], "band": 0.08}`: jaw hinge and chin line |

### `humanoid`

For `kind: "humanoid"`: `forward`, then joint heights `z` (`hip`, `knee`, `ankle`, `spine`, `spine1`, `spine2`,
`neck`, `head`, `top`, `arm`) and spans `x` (`shoulder`, `elbow`, `wrist`, `knuckle`, `tip`) as 0..1 of the turned
model's bounds, read off `measure.py`'s front view. Everything else about a joint is measured from the mesh: the
body's middle across the legs (a held prop does not move the spine), each arm followed out from the shoulder (an
A-pose's sloping arm is tracked), the fingertips from the body's own pieces. `digits: true` gives each hand five
three-bone fingers (`LeftHandThumb1..3` ... `LeftHandPinky1..3`) in place of the single index chain; they are built on the model's own five fingertips when the mesh has them (the farthest points past the wrist, each the tip of its own finger, sorted thumb to little finger across the palm), and on a template otherwise; `twist_bones`,
`morph_targets` and `keep_inside` apply as for the other kinds.

`joints: "source"`: a model that came with a Mixamo-named skeleton (Tripo's, Mixamo's own; a `mixamorig:` prefix is
dropped) keeps that skeleton's joints wherever it has them (hips, spine, neck, head and its top, each leg's hip, knee,
ankle, ball and toe, each arm's shoulder, elbow, wrist, knuckle and fingertip, and with `digits` the five fingertips),
in place of the measured ones. The heights and spans are still needed for measuring the rest. Measuring follows the
body's cross-sections, and a robe's long bell sleeves pulled one measured arm 90 cm down from the model's own hand.

`x.leg` (optional) is for a long robe, gown or skirt: each leg's middle, measured out from the body's middle (0..1 of
the width, e.g. `0.035` for boots 6 cm either side on a 1.9 m-wide T-pose). Without it a leg is the middle of
everything on that side below the hips, which on a robe is out at the hem; with it the hips, knees, ankles and feet are
read only in a band around that line. Read it off the front view's feet. The legs then stand straight down under the
body's middle (a slab's depth is the cloth's, and a train trailing behind would slant them back), and the toe is kept
within a foot's length ahead of the ankle.

`straight_arms: true` (optional) is for a T-pose with wide sleeves that hang below the arm: each slice's middle is
then the sleeve's and the arm tracker follows it down. With it the shoulder, elbow, wrist and knuckle lie on the
straight line from the shoulder (the arm height, the torso's depth) to the measured fingertip.

### `budget`

The engine triangle budget. `trim` (decimate.py) cuts the rigged FBX to it with at most four influences per vertex;
`null` keeps full resolution. Without one, the collection's default applies (below).

### `clips`

| Field | Meaning |
|---|---|
| `archetype` | `walker`, `flyer`, `exploder`, `swimmer`, `turret`, `machine` (write `clips/`) or `winged` (writes an export beside the rig) |
| `gait` | walker: `walk` (4-beat lateral sequence for quadrupeds), `trot` (2-beat diagonal suspension), or `gallop` (rotary gallop with gathered/extended suspension flight phases) |
| `walk` | procedural gait tuning: `{preset, stride, cadence, sway, bob, lean, arm_swing, duty_factor}`. Presets: `natural`, `soldier`, `swagger`, `stealth`, `heavy`, `run`, `sprint`, `quadruped_walk`, `quadruped_trot`, `quadruped_gallop`. Humanoids author `walk`, `run`, `idle_to_walk`, `walk_to_idle`; quadrupeds author `walk`, `trot`, `gallop`. |
| `display`, `category` | a display name and a category, carried into the clip files |
| `attack` | walker: `bite` (rear with forelegs raised, lunge), `discharge` (rear onto planted legs, tail arched over, a crackle, a whip forward), `shoot` / `smash` (humanoids) |
| `windup`, `hit_rear` | walker: `windup: "head_down"` braces for a bite with the front end dropped and the whole neck (the card's neck bones) and head low, then lunges low, instead of rearing; `hit_rear` scales how far a hit rocks it back and up (default 1) |
| `spin`, `work`, `loop` | machine: `{bone: whole turns per loop}` turned always (`spin`, clip `idle`) and also while working (`work`, clip `work`), about each bone's own length; `loop` frames (default 24) |
| `beats`, `flap` | flyer: wingbeats per loop, the wing root's swing in degrees |
| `loop`, `ripple`, `flicker`, `pulse` | flyer: loop length in frames, rippling wings, flickering flames, the bone that pulses (a bell) |
| `body` | the body bone, when it is not the one the card names |
| `secondary` | springs baked into the clips (CLIP-MOTION.md): a list of `{bones: [root first] or chain: "stem", stiffness, damping, gravity, iterations, clips}`, e.g. `[{"chain": "tail", "stiffness": 150, "damping": 2}, {"chain": "ear"}]`. Defaults are Wiggle 2's: stiffness 400, damping 1, gravity 1, 2 iterations; every clip |
| `rig`, `triangles`, `flySpeed`, `splitWalkSpeed` | winged: the rig folder (default the model's rig folder), the export's triangle budget (default the model's budget, else full resolution), the fly loop's speed in metres a second (left out of the manifest when not given), and the `walkSpeed` written with `--split-clips` |

### Suggesting a skeleton

The tool can propose a full `rig` spec and archetype automatically:
- **From an existing bone hierarchy**: Tripo skeletons (`bone_0`...) or Mixamo humanoids are recognized and mapped into `tripo` or `humanoid` specs.
- **From mesh heuristics**: For boneless models, survey's `probe_tips` (geodesic tips from the mesh extremities) are grouped across the symmetry plane $X=0.5$ into centerline features (snout, jaw, tail) and paired limbs. Proportions and tip positions propose an archetype (`quadruped`, `hexapod`, `octopod`, `serpent`, `winged`, `floater`, `rigid`, `humanoid`) and placed chains with tip/base coordinates, jaw rules, and wing membranes.
- **Body plan** (with the `trimesh` extra, docs/EXTRAS.md): `core/body_plan.py` measures the whole shape: connected
  parts, how mirror-symmetric it is left to right and front to back, how many separate feet touch the ground and how
  much of the footprint they fill (a base fills it), thin flat wing sheets out to both sides, height against length
  and depth, and whether it rests stably standing as it is (trimesh's stable poses).
- **Picture vote** (with the `open-clip` extra): the step renders the model from four sides and OpenCLIP scores the
  pictures, on the CPU and with no training for this tool, against plain descriptions ("a four-legged animal", "a
  winged dragon", "a statue on a pedestal", ...), each voting for an archetype (`core/picture_vote.py`).
- **Combining them** (`suggest.combine`): the tips' pick counts 3, 2 or 1 by its confidence; the body plan adds or
  takes points (4 feet on the ground: quadruped +2; 2 feet under a body much taller than it is deep: humanoid +2; one
  solid base: rigid +2; the tips' legs not matching the feet on the ground: -1.5 ...); the picture vote adds 3 times
  its chance. Archetypes within 0.75 of the best are a tie, which the pictures break. The suggestion's `reasons`
  start with `picked <archetype>: ...` listing every opinion and what decided, and `votes` holds the scores, the
  measures and the picture chances. With neither extra the tips decide alone, as before.
- **Facing**: the suggester measures the tips in the frame `forward: "auto"` turns the model to. That facing now
  also asks which way the model is a mirror image of itself, so a T-posed figure (as wide as it is tall) or a stick
  figure is not turned side-on, which used to pair its front with its back.
- **Access**: Via CLI (`blender -b --python autorig/steps/suggest_step.py -- <model> [-no-vote]`, or
  `python autorig/cli/run.py suggest <model>`), HTTP API (`POST /api/spec/suggest`), or the **Suggest skeleton**
  button in the spec editor. The watch folder logs each suggestion's reasons (`WATCH_SUGGEST <model>: why: ...`).

### `card`

Fields copied onto the model card by publish: `metres` (the real size of the longest axis: generated models come in a
unit box, and this turns one back into a creature), `role`, `tint` (a base colour for a model with no texture), and
anything else the collection lists.

### `notes`

Free text keyed by field (`"rig"`, `"rig.audit"`, `"budget"`, `"clips"`, `"card.metres"`...). The GUI shows the
reason for an audit allowance beside it.

## `autorig.json`

```json
{
  "schema": "autorig-collection/1",
  "budget": 2000,
  "full_resolution_groups": ["Showcase"],
  "licence": "the licence line written into clip manifests",
  "card": {
    "group_field": "group",
    "fields": {"tint": null, "metres": null, "role": "creature"},
    "sidecars": {"origin": "origin.json"},
    "notes": {
      "when": {"origin": "Copied from {origin[group]}/{origin[model]} ({rig_kind})."},
      "builder": "Rig built by {builder} into {rig_folder}/.",
      "rigged": "Rig built by the tool ({rig_kind}).",
      "static": "Not rigged."
    }
  },
  "notes": {"budget": "why this default"}
}
```

| Field | Meaning |
|---|---|
| `budget` | the default budget (`null`, the default: keep full resolution) |
| `full_resolution_groups` | groups whose models keep full resolution unless they set a budget of their own |
| `licence` | written into clip manifests |
| `card.group_field` | the card key (and `pack.json` key) naming the model's group (default `group`) |
| `card.fields` | card fields, in order, with their defaults; each model's `card` section overrides them |
| `card.sidecars` | `{field: file}`: a JSON file beside a model whose contents fill that field |
| `card.notes` | the card's `note`: `when` texts are used when that field is set, then `builder`, `rigged`, `static`. Templates over the card's fields plus `rig_kind`, `builder`, `rig_folder`. |
