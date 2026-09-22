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
| `audit` | allowances: `{"combined_tears": 8, "bend_tears": 4}` loosens (or tightens) audit.py's thresholds for this model. Always say why in `notes["rig.audit"]`. |

`kind: "tripo"`:

| Field | Meaning |
|---|---|
| `head`, `hips` | joints at the two ends of the body (which way it faces; the spine runs between them) |
| `delete` | joints (with everything under them) to throw away |
| `mirror` | chains to copy across the centre plane (the new joints are named `<joint>_m`) |
| `legs` | joints at the top of each leg; these get IK foot controls |
| `chains` | `{role: [top joints]}` to name other chains (tail, ear, jaw, wing...); unnamed ones are guessed |
| `shell` | a rigid body on legs: everything the legs do not own goes to this joint's bone |
| `nodeform` | joints whose bones carry no skin |
| `body: "single"` | a shell on legs: one rigid body bone with the listed limbs hanging off it; needs `forward` |
| `body_height` | for `body: "single"`, the body bone's height (0..1) |
| `add_tail` | `{"from": 0..1 along the body, "bones": n, "width", "above"}`: a tail the source skeleton gave no bones |
| `head_line` | `[start, tip]`: the head bone runs from `start` (the spine is cut there) to `tip` (rule A) |
| `jaw` | `{"hinge": [x,y,z], "tip": [x,y,z], "band": 0.15}`: a jaw bone; under the mouth line ahead of the hinge moves from the head to the jaw |
| `girdle`, `girdle_reach` | roles whose first bone is really a girdle (scapula): `["leg_front"]` |

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
| `rigid_parts` | a machine of parts: the main piece rides the body, every other piece its nearest bone |
| `hard_split` | `{"bone", "else", "above"}`: everything above a height on one bone, the rest on another (a lid) |
| `smooth` | weight smoothing passes (a thick body's patchy bone heat) |
| tuning | `joint_blend`, `girdle_blend`, `limb_radius`, `spike_reach`, `envelope_skip`, `head_to_snout` (false to keep the head where it is), `centre` |

### `humanoid`

For `kind: "humanoid"`: `forward`, then joint heights `z` (`hip`, `knee`, `ankle`, `spine`, `spine1`, `spine2`,
`neck`, `head`, `top`, `arm`) and spans `x` (`shoulder`, `elbow`, `wrist`, `knuckle`, `tip`) as 0..1 of the turned
model's bounds, read off `measure.py`'s front view. Everything else about a joint is measured from the mesh.

### `budget`

The engine triangle budget. `trim` (decimate.py) cuts the rigged FBX to it with at most four influences per vertex;
`null` keeps full resolution. Without one, the collection's default applies (below).

### `clips`

| Field | Meaning |
|---|---|
| `archetype` | `walker`, `flyer`, `exploder`, `swimmer`, `turret` (write `clips/`) or `winged` (writes an export beside the rig) |
| `display`, `category` | a display name and a category, carried into the clip files |
| `attack` | walker: `bite` (rear with forelegs raised, lunge), `discharge` (rear onto planted legs, tail arched over, a crackle, a whip forward), `shoot` / `smash` (humanoids) |
| `beats`, `flap` | flyer: wingbeats per loop, the wing root's swing in degrees |
| `loop`, `ripple`, `flicker`, `pulse` | flyer: loop length in frames, rippling wings, flickering flames, the bone that pulses (a bell) |
| `body` | the body bone, when it is not the one the card names |
| `rig`, `triangles`, `flySpeed`, `splitWalkSpeed` | winged: the rig folder, the export's triangle budget, the fly loop's speed in metres a second, and the `walkSpeed` written with `--split-clips` |

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
