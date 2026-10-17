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
