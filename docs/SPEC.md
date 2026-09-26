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
| `envelope` | after bone heat: `"full"` (the torso belongs to the spine, each limb to a capsule round itself, rule B), `"root"` (bone heat kept, but no limb weight behind the plane where it starts; people), `false` (plain bone heat). Default: full for creatures. |
| `rigid_pieces` | loose pieces up to this fraction of the largest ride one bone whole (default 0.12) |
| `rigid_single`, `soft` | every loose piece on one bone; chains exempt from rigid pieces |
| `rigid_to` | `[[bone, [x0,y0,z0], [x1,y1,z1]], ...]`: a loose piece whose middle is in the box rides that bone |
| `rigid_parts` | a machine of parts: the main piece rides the body, every other piece its nearest bone. `"listed"`: only the pieces in `parts` ride their bones, everything else the body |
| `parts` | `[{"bone", "at": [x, y, z], "verts": n}, ...]`: the loose piece of `n` vertices (within 2%) whose bounds centre is nearest `at` rides `bone` whole. For a moving part inside another (a fan's rotor in its duct), where nearest-bone cannot tell them apart. Give each part its own chain, head at its hub and length along its axle, so turning the bone about its own Y spins the part in place |
| `hard_split` | `{"bone", "else", "above"}`: everything above a height on one bone, the rest on another (a lid) |
| `smooth` | weight smoothing passes (a thick body's patchy bone heat) |
| tuning | `joint_blend`, `girdle_blend`, `limb_radius`, `spike_reach`, `envelope_skip`, `head_to_snout` (false to keep the head where it is), `centre` |

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
three-bone fingers (`LeftHandThumb1..3` ... `LeftHandPinky1..3`) in place of the single index chain; `twist_bones`,
`morph_targets` and `keep_inside` apply as for the other kinds.

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
| `rig`, `triangles`, `flySpeed`, `splitWalkSpeed` | winged: the rig folder, the export's triangle budget, the fly loop's speed in metres a second, and the `walkSpeed` written with `--split-clips` |

### Suggesting a skeleton

The tool can propose a full `rig` spec and archetype automatically:
- **From an existing bone hierarchy**: Tripo skeletons (`bone_0`...) or Mixamo humanoids are recognized and mapped into `tripo` or `humanoid` specs.
- **From mesh heuristics**: For boneless models, survey's `probe_tips` (geodesic tips from the mesh extremities) are grouped across the symmetry plane $X=0.5$ into centerline features (snout, jaw, tail) and paired limbs. Proportions and tip positions propose an archetype (`quadruped`, `hexapod`, `octopod`, `serpent`, `winged`, `floater`, `rigid`, `humanoid`) and placed chains with tip/base coordinates, jaw rules, and wing membranes.
- **Access**: Via CLI (`blender -b --python autorig/steps/suggest_step.py -- <model>`), HTTP API (`POST /api/spec/suggest`), or the **Suggest skeleton** button in the spec editor.

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
