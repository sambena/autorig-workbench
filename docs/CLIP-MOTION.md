# Clip motion: feet, steps, variants, secondary motion

What `make_clips.py` does to a clip after it is keyed, and how `clip-check` measures it. Each part is ported from an
open-source project, credited in the code that carries it.

## Foot skate: the measure

    python autorig/cli/run.py clip-check <model | file.glb> [--max-skate S]

Every clip in the report has `foot_skate`:

```jsonc
"foot_skate": {"score": 0.55, "ratio": 0.04, "worst": "canine_rig:leg_front.L_2.L",
               "per_foot": {...}, "ground_speed": 0.0, "floor": 0.0}
```

- **Feet.** The end of every leg: a leaf bone with a leg word in its name or an ancestor's (`leg`, `foot`, `ankle`,
  `toe`, `paw`, `hoof`), not an IK control. A glTF keeps no bone tails, so the point measured is the mesh: the lowest
  tenth (at rest) of the vertices skinned mostly to that bone.
- **Floor.** The feet at rest. A clip's own lowest feet are not it: a flier hovers.
- **Ground.** Still, except under clips that travel in place (`walk`, `trot`, `gallop`, `run` and the like): their
  ground moves back at the speed the planted feet share, estimated from the feet and reported as `ground_speed`
  (units a second). Compare it with the manifest's walk `speed`.
- **Score.** MoMask's `compute_foot_sliding` in the ground's frame: each frame step, a foot's horizontal slip `d`
  while its height above the floor `h` is under `H` (3% of the model's size, MoMask's 5 cm on a person) counts
  `d x (2 - 2^(h/H))`. The sum over feet and frames, divided by the frames, in thousandths of the model's size a frame.
  0 is planted. A well-planted walk keeps about 1 from its liftoff and touchdown frames; a foot sliding a tenth of a
  stride a step is several. `ratio` is the share of low foot frames slipping more than 0.25% of the size a frame.

`--max-skate S` fails a clip scoring above S. Without it the score is a diagnostic, like floor penetration.

## Anti-slide: the fix

After the clips are keyed, every authored clip of a rig with leg chains gets the pass (`steps/clip_passes.py`,
`core/foot_contact.py`, `core/limb_ik.py`):

1. **Contacts** (MoMask `remove_fs.py`, GenMM `fix_contact.py`): a foot is down when it is below the floor plus 4% of
   the size and moves less than 1.2% of the size a frame, in the ground's frame. GenMM's `continuous_filter` drops
   contact flickers of two frames or fewer. Loops wrap round.
2. **Pins**: each contact run holds the foot at its mean position, on the floor. The four frames either side ease
   between the pin and the clip with MoMask's `alpha(t) = 2t^3 - 3t^2 + 1`.
3. **Re-solve**: each leg is first swung whole about its hip toward its pinned foot, then FABRIK takes up the change
   in distance, from the leg's own pose, so the knee stays on its side (a straight leg bends forward). No bone turns more than 35 degrees from the authored pose. Only the leg bones'
   rotation keys are rewritten.
4. **Kept only if better**: the clip is re-measured. Unless the skate dropped by a tenth and the mesh tears no more
   (no new torn edges, stretch up by at most 0.25), the authored keys go back.

The foot is the same point clip-check measures (the lowest skinned vertices of the leg's last bone, carried by that
bone): a placed rig's bones need not run down the middle of its mesh's legs. Legs come from the card's `rig.skeleton.legs` (or the QA log for a model without a card), else a Mixamo humanoid's
thigh and shin (its foot keeps its orientation). Legs driven by a live IK target, and retargeted mocap, are left
alone. `--no-anti-slide` skips the pass. The manifest's `footSkate` lists each clip's score before and after
(in-Blender, measured at the bone tails).

On the samples (clip-check scores; `--no-anti-slide` build first, then the pass):

| Clip | canine | beetle |
|---|---|---|
| idle | 3.00 -> 0.30 | 3.61 -> 3.38 |
| hit | 5.31 -> 0.55 | 3.32 (kept) |
| attack_windup | 2.84 -> 0.23 | 2.65 (kept) |
| walk | 32.5 -> 24.3 | 44.5 (kept) |

The wyvern's clips are all in the air (the feet never come within 3% of the rest floor): 0 before and after. A clip
whose body lifts out of the legs' reach (a rear, a jump, a dodge) cannot hold its feet with the legs alone; the pass
leaves it as authored rather than worse.

## Stepping legs: planting each foot in turn

A rig pipeline rig (`kind: placed`) has FK legs and no IK targets. Its walk used to keep them still while the body
rocked. Now the travelling clips step (`core/leg_planting.py`, after Spider Walker):

- **Which legs step when.** Four legs (two of them front): the lateral-sequence walk (LH, LF, RH, RF), the diagonal
  trot and the rotary gallop, timed as `core/gait.py` times them. Six: the alternating tripod. Any other count:
  Spider Walker's round robin, front to back down one side and then the other, offset by half a step.
- **Where a foot lands.** Spider Walker's predicted target: the foot's rest spot under where the body will be, ahead
  by half a stride, raycast down onto the ground (a flat floor at the rest feet; `step_cycle` takes any raycast).
- **The step.** From one plant to the next by smoothstep, lifted along the surface normal by `sin(pi t) x height`.
- **Looping.** Each leg steps once a cycle at its phase, and the drift that triggers it is one cycle's travel, so
  the last frame is the first. A planted foot moves back at `stride / (duty x frames)` a frame: the speed the
  manifest publishes.
- **Legs.** Re-solved onto the steps (swing, then FABRIK, up to 70 degrees a bone). A stride is at most 0.9 of the
  shortest leg on four legs and 0.55 on more. A stepping leg that tears the mesh more than the clip did tries 70%
  and then 50% of the stride, and then gives up: the clip keeps its legs still (the beetle, whose hips fail the rig
  audit's bends, does). The walk's published stride and footfall events follow what was planted.

Clips: `walk` always, and `trot` and `gallop` for four legs, as for IK rigs. `--no-plant` skips the stepping.
Footfall events name the legs that land together.

## Variants: takes that do not repeat

    python autorig/cli/run.py clips <model> --variants N [--seed S]

adds N takes of the idle and of each gait (`walk`, `trot`, `gallop`, `fly`, `swim`, `run`): `idle_v1`, `walk_v1`
and so on, each a loop twice its clip's length, in the FBX, the `.blend`, the preview and the manifest (a walk's
take carries the walk's `speed`). Same skeleton, any skeleton; on the CPU, in seconds, with nothing to install.

They come from GenMM (generative motion matching, Apache-2.0), vendored in `autorig/core/genmm/` and ported from
PyTorch to NumPy (see its NOTICE). A take is the clip's own patches rearranged and blended, coarse to fine: never a
copy, made only of what the clip does. Each channel is a bone property over the clip: rotations as GenMM's 6D, a
location divided by the model's size. The seed is `S + 1000 x take`, so a rebake makes the same takes.

- **Feet stay the gait's.** GenMM blends patches, and a blended stance no longer moves back at the walk's speed. In a
  gait the leg bones and IK foot targets play as the clip does, cycle after cycle; the body, head and tail vary. FK
  legs are then planted again for the take's own body (two steps a leg). A gait take whose legs cannot step without
  tearing the mesh is dropped, not published sliding.
- **Then the passes.** Anti-slide runs on the takes as on any clip, at their clip's ground speed.

On the canine (`--variants 2`): `idle_v1`/`idle_v2` skate 0.04, `walk_v1`/`walk_v2` 3.2/2.9 (the walk 3.9),
`trot_v1`/`trot_v2` 3.2/3.2 (the trot 2.6). Its gallop takes bound further than its legs can step and are dropped.

## Secondary motion: springs on tails, ears, antennae, membranes

Bones are opted in through rig.json:

```json
"clips": {"archetype": "walker",
          "secondary": [{"chain": "tail", "stiffness": 150, "damping": 2},
                        {"chain": "wing", "stiffness": 300, "damping": 3, "gravity": 0.3, "clips": ["fly", "idle"]}]}
```

An entry names a chain's `bones` (root first) or a `chain` stem (every chain of that name, each side its own:
`tail_1`.., `ear_1.L`.. and `ear_1.R`.., the placed rigs' `wing.L_1.L`..). Settings (SPEC.md): `stiffness`, `damping`,
`gravity`, `iterations`, and `clips` to limit it to some clips (their variants follow). Gravity is scaled by the
card's `metres`.

The pass (`core/spring_bones.py`, after Wiggle 2's move / constrain / velocity update) runs last, on every clip and
variant: each bone's tail is a point with a velocity, damped and pulled by gravity, then sprung toward where the clip
puts it, carried by its parent's simulated turn, so the chain lags link by link; each bone keeps its length. A loop is
run four times so it settles, the last cycle is kept and the gap left at its seam is spread back across it, so the
clip still loops exactly. A one-shot starts at rest. The bones' rotation keys are rewritten. A clip the springs make
tear (more torn edges, or stretch up by more than 0.25) keeps its authored motion. `--no-secondary` skips it; the
manifest lists the chains under `secondary`.

On the samples, opted in on a copy (canine `tail`; wyvern `tail` and `wing`): every clip of both still loops
exactly (`clip-check --loop`), the canine's tail tip trails by up to 0.15 of the model's size in its idle and walk,
and its `attack` and `death` (a tail whipped by a fall would tear) keep their authored tails.
