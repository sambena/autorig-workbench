# Standard skeletons

Every rig the tool builds is one of these. The names, the hierarchy, the rest pose and the facing are fixed here so a
clip authored for one model of an archetype plays on another, and so any engine or tool can find "the head" or
"the front left leg" without reading a game's code.

**Games read the card, not the names.** Each rigged model's `model.json` carries `rig.skeleton`: its archetype and
which bone plays which role (written by `skeletons.describe()` from the chains the rig was built from). Use that. The
naming rules below are for people and for tools that have no card; a game that pattern-matches `StartsWith("leg")`
is guessing, and sooner or later guesses wrong.

Sections: [Conventions](#conventions-all-archetypes) · [Humanoid](#humanoid) · [Quadruped](#quadruped) ·
[Hexapod / insect](#hexapod--insect) · [Octopod / crab](#octopod--crab) · [Serpent](#serpent) ·
[Floater / tentacle](#floater--tentacle) · [Rigid](#rigid) · [Winged](#winged) ·
[The card](#the-card-rigskeleton)

## Conventions (all archetypes)

| | |
|---|---|
| Up | Blender +Z. FBX files are written Y-up (`axis_up='Y'`), so Unity, Godot and Unreal import them upright. |
| Facing | Blender **-Y** (the front view shows the face). In Unity that is **+Z**; Godot +Z (its "back": turn 180 if the node should face -Z); Unreal +X after its FBX import converts axes. |
| Sides | The creature's **left is +X**. Creature chains: `.L` / `.R` suffix. Humanoid: `Left` / `Right` prefix (Mixamo's). A chain that runs down the centre plane has **no** suffix (`tail_3`, `abdomen_1`). |
| Root | `root`, at the origin, non-deforming, parent of everything. Clips move `root` for whole-body travel (a death roll, a flyer's fall); `root` stays at the origin at rest. |
| Ground | Walkers stand on z=0 over the origin. Swimmers and flyers are centred on the origin (their cards say `origin`). |
| Units | The rig is in the source's own units (generated models often come in a unit box: the longest axis is about 1). The card's `metres` is the real longest axis; scale by `metres / longest`. `clips/<model>.fbx` is in the same units as `rigged/<model>.fbx`, so the pair always agrees. |
| Chains | `<base>_<i><side>`, `i` from 1 at the body out to the tip. **Link 0 is a girdle** (a scapula, a pelvis side, a clavicle, a coxa) where the archetype has one: `leg_front_0.L` > `leg_front_1.L` > ... A lone bone has no index: `mandible.L`, `jaw`. |
| Bones | Joint to joint, connected down each chain. Rolls: limbs and horizontal bones Z up, vertical bones Z toward -Y. |
| Controls | `ik_<chain><side>` foot targets (collection *Controls*) in the `.blend` only. The FBX carries deform bones only; the clips are baked FK. |
| Skin | At most 4 influences, normalised. Loose hard pieces (armour plates, teeth, pouches) ride one bone or two whole. |

The pipeline guarantees these (`rerig.py`, `rerig_humanoid.py`), and `audit.py` checks the weights.

## Humanoid

Mixamo's bone names **without the `mixamorig:` namespace**. Unity's Humanoid avatar maps them automatically, Unreal's IK
Retargeter has a Mixamo preset, Godot's `SkeletonProfileHumanoid` bone map auto-maps them, and Blender retargeting
add-ons (Rokoko, Auto-Rig Pro, the built-in NLA with matching names) know them. The namespace is left off because
Godot rewrites `:` to `_` on import and some tools treat it as a path separator. **Any reader strips everything up to
the last `:` before comparing**, so a Mixamo download (`mixamorig:Hips`) and any Mixamo character line up with these bone
for bone.

```
root
└ Hips
  ├ Spine ─ Spine1 ─ Spine2 ─┬ Neck ─ Head
  │                          ├ LeftShoulder ─ LeftArm ─ LeftForeArm ─ LeftHand ─ LeftHandIndex1 ─ 2 ─ 3
  │                          └ RightShoulder ─ ... (mirror)
  ├ LeftUpLeg ─ LeftLeg ─ LeftFoot ─ LeftToeBase
  └ RightUpLeg ─ ... (mirror)
```

| Bone | Unity HumanBodyBones | Unreal (Mixamo preset chain) | Joint it starts at |
|---|---|---|---|
| Hips | Hips | Root/Pelvis | the hip joints' height, centred |
| Spine, Spine1, Spine2 | Spine, Chest, UpperChest | Spine | waist, lower chest, upper chest |
| Neck, Head | Neck, Head | Head | top of the chest, base of the skull |
| LeftShoulder | LeftShoulder | LeftClavicle | off the centre line at the top of the chest |
| LeftArm, LeftForeArm, LeftHand | LeftUpperArm, LeftLowerArm, LeftHand | LeftArm | shoulder, elbow, wrist |
| LeftHandIndex1-3 | Left Index Proximal/Intermediate/Distal | LeftIndex | knuckles; **the whole hand's fingers as one chain** |
| LeftUpLeg, LeftLeg, LeftFoot, LeftToeBase | LeftUpperLeg, LeftLowerLeg, LeftFoot, LeftToes | LeftLeg | hip, knee, ankle, ball of the foot |

- **Rest pose: a clean T-pose.** Legs straight down from the hips, feet pointing forward (-Y) at their sculpted slope,
  arms straight out along ±X, palms down, fingers straight. `rerig_humanoid.py` poses the sculpt into it and applies
  that as the rest, so a sculpt that arrives mid-stride or with forearms swept forward still rests square. A Humanoid avatar and every retargeter take the rest pose as the zero of each clip: a crooked rest
  is a crooked character in every animation.
- **Fingers:** generated characters often have mitten-like gloves, so the four fingers share one chain, mapped as Index. There is
  no thumb bone. Unity leaves unmapped fingers still; a finger curl from a library clip closes the whole hand.
- **Facing** -Y, left +X, feet on z=0, hips over the origin.
- Mixamo downloads (`mixamorig:` + these names + more fingers and `HeadTop_End`) are the same family. A clip made for one plays on the other through the Humanoid avatar in Unity, the Mixamo
  retarget preset in Unreal, and by name (namespace stripped) in Blender.

## Quadruped

```
root
└ hips ─ spine_1 ─ ... ─ spine_n ─ neck(_1..k) ─ head ─ jaw
  │                  └ leg_front_0.L (scapula) ─ leg_front_1.L ─ 2 ─ 3 [─ 4]      and .R
  ├ leg_hind_0.L (pelvis side) ─ leg_hind_1.L ─ 2 ─ 3 [─ 4]                         and .R
  └ tail_1 ─ ... ─ tail_m
  ear.L / ear.R, horn_*, from head
```

- A long neck is `neck_1..k` (spec `neck: k`): every link from the withers to the skull, so a game lowering "the neck"
  lowers all of it.
- A spec with its own head chain (role `head`, as suggested specs have) keeps one head: the body chain then ends in
  `chest` (or its `neck_1..k`), and the head chain is `neck`/`neck_1..` then `head`. Bone names are never doubled: a
  clash renames the automatically named bone (`_v2`, logged as `renamed_bones`), never a name the spec gave.
- Rolls: each chain has one roll reference, so every hinge in a limb bends about the same local axis (a leg's Z points
  the way its knee bends, the plane its IK pole is in; other chains take Z up, or forward when they run up and down),
  and mirrored chains mirror. The audit warns when rolls flip inside a chain or do not mirror.
- `hips` is the pelvis and the body's root bone; the spine runs **forward** from it to the neck. The front legs hang
  from the spine bone at the shoulders, the hind legs from `hips`, the tail runs **backward** from `hips`.
- Legs: link 0 is the girdle (scapula / pelvis side), from the spine out to where the leg leaves the body; then upper
  leg, lower leg, foot (3), or upper, lower, metacarpal/metatarsal, foot (4, digitigrade). The last bone is the foot,
  and the IK control copies its rotation.
- `head` runs from the base of the skull to the front of the face; `jaw` from the hinge under the ear to the chin.
- Rest: standing as sculpted, feet on the floor, spine roughly level. Pairs are `front` / `hind`.

## Hexapod / insect

```
root
└ body (thorax) ─┬ head ─ mandible.L / mandible.R, ear_1..2.L/.R (antennae)
                 ├ abdomen_1 ─ ... ─ abdomen_n          (runs backward from the thorax)
                 └ leg1_1.L ─ leg1_2.L ─ leg1_3.L        leg1 = front pair, leg2 = middle, leg3 = hind; and .R
```

- `body` is the thorax, and the body's root. `head` runs forward from the neck joint (the front edge of the thorax) to
  the front of the face. `abdomen_*` runs backward from the waist.
- Legs are numbered pairs, front to back (`leg1` front). Each leg is 3 bones (femur, tibia, tarsus/foot) from where it
  leaves the body; a leg hangs from whichever body bone its root is nearest (`parent_nearest`). The foot is the last
  bone; IK controls `ik_leg1.L`...
- A shelled insect's legs own nothing of the shell: the envelope keeps each leg to itself (or to its own loose piece).
- Mandibles are one rigid bone each on a hinge at the face. Antennae are `ear_*` chains (role *antennae* on the card).
- Rest: standing, feet on the floor.

## Octopod / crab

```
root
└ body (the shell: rigid) ─┬ leg1_1.L ─ 2 ─ 3 ... leg4 (front to back), and .R
                           ├ claw_1.L ─ 2 ─ 3 (and .R)
                           └ eye_*.L/.R (stalks), mouth parts
```

- One rigid `body` carries the whole shell (`shell="body"` in the spec). Legs numbered pairs front to back, 3 bones
  each from the shell's edge; claws are their own chains.
- Rest: standing, legs splayed as sculpted.

## Serpent

```
root
└ hips (mid-body) ─┬ spine_1 ─ ... ─ spine_n ─ neck_1..k ─ head ─ jaw     (forward)
                   └ tail_1 ─ ... ─ tail_m                                  (backward)
  barbel_*, fin_* from the nearest body bone
```

- Rooted **in the middle of the body**, the spine running both ways, as the quadruped's does. A chain hung from the
  head (`root` > `head` > `neck` > `body_*`) makes every body wave swing the head. (make_clips' swimmer still animates
  such a head-rooted chain, for models rigged that way.)
- Rest: laid out straight along -Y where the sculpt allows; a curled sculpt keeps its curl, and the card says so.

## Floater / tentacle

```
root
└ body ─┬ bell / dome / mantle (the part that pulses)
        ├ tentacle1_1 ─ ... ─ tentacle1_k    (and tentacle2..n; .L/.R by side, none on the centre line)
        └ pod1.L ... (a drone's rigid pods), flame_*, wisp*
```

- `body` is the centre of mass and the root. Tentacles are numbered around the body; a centred one has no suffix.
- A drone's pods are rigid pieces, each wholly on its own `pod` bone; the shell is rigid on `body`.
- Rest: floating, centred on the origin.

A rooted floater (a plant pod) is `base` > `stalk` > `pod`, tendrils from `base`.

## Rigid

`root` > `body`, one bone, whole mesh: a crystal creature, a rock that hops.

A machine with moving parts is `body` plus one bone per part, each part a loose piece riding its bone whole
(`rigid_parts` and `parts` in the spec): the bone's head is the part's hub and its length the axle, so turning it about
its own Y spins the part in place, in any engine. Names say what the part is (`drill`, `fan.L`, `rotor.L`); the clip
archetype `machine` writes `idle` (what always turns) and `work` (what also turns while it works) as whole-turn loops.

## Winged

A creature with wings as a limb of their own, on top of whatever else it walks or grabs with: dragons, drakes,
wyverns, bats, griffins. A wing rig built from limb tips alone tends to run the wing chain out of the chest and give
the chest, shoulders and forelimbs to `wing_1`, so every wingbeat moves the arms; this archetype is laid out to stop
that.

```
root
└ hips ─┬ spine_1 ─ spine_2 (chest) ─┬ neck_1 ─ neck_2 ─ neck_3 ─ head ─ jaw
        │                             ├ arm_1.L ─ arm_2.L ─ arm_3.L                 forelimb: upper arm, forearm, hand
        │                             └ wing_1.L ─ wing_2.L ─ wing_3.L ─┬ wing_finger1_1.L ─ _2 ─ _3   leading spar
        │                                                                ├ wing_finger2_1.L ─ _2        middle spar
        │                                                                └ wing_finger3_1.L ─ _2        inner spar
        ├ leg_1.L ─ leg_2.L ─ leg_3.L ─ leg_4.L                             thigh, shin, foot, toes (digitigrade)
        └ tail_1 ─ ... ─ tail_n                                             (n ≥ 5)
  and every .L chain mirrored as .R
```

| Chain | Bones | Joints |
|---|---|---|
| spine | `hips`, `spine_1`, `spine_2` | pelvis → waist → **chest**. `spine_2` starts low enough to carry the whole shoulder girdle (forelimbs and wing roots), so turning the chest never hinges at a wing root. |
| neck | `neck_1..3` | base of the neck → skull. |
| head, jaw | `head`, `jaw` | `head` from the skull base **to the snout tip** (it owns the whole skull); `jaw` from the hinge behind the mouth to the chin. |
| tail | `tail_1..n` | from the rump backward to the tip, 5 or more. |
| hind legs | `leg_1..4.L` | hip → knee → ankle (heel) → ball of the foot → toe tip. IK control `ik_leg.L` in the `.blend`. |
| forelimbs | `arm_1..3.L` | shoulder (at the body's surface, not inside it) → elbow → wrist → claw tips. |
| wing arm | `wing_1..3.L` | `wing_1` is the wing's girdle, from the back's surface over the shoulder blade to the wing shoulder; `wing_2` the humerus; `wing_3` the forearm, to the wrist. |
| wing spars | `wing_finger1..3_i.L` | from the wrist: 1 the leading edge out to the wing tip, 2 through the middle of the membrane, 3 the inner spar next to the body. 2 or 3 bones each. |

Weights, which are the point of this archetype and which a winged builder enforces after bone heat (a custom builder
today; PLAN.md, P3, makes it a spec kind):

- The torso (anything inside the spine's body capsule) belongs to the spine. A limb's first bone blends in only across
  the join, and `wing_1` may never lead on a torso vertex.
- The membrane rides **only wing bones**, by distance to them (a smooth gradient between spars, so the trailing edge
  follows them). It meets the body only at the wing root; anywhere else it touches the flank it is cut free, so the
  wing flaps without dragging the body.
- The forelimbs ride only `arm_*` (and the chest at the shoulder). No wing weight on an arm, ever.
- Welds a generator fused between parts that move apart (a forearm to a thigh plate, wing tips to the tail) are split.
- Rigid armour over a joint (pauldrons) rides fixed weights, never bends.

Clips (`make_clips.py`, archetype `winged`): `fly` (loop), `idle` (a hover), `perch` (standing, wings folded),
`attack` (bite) and `attack_inhale` / `attack_rise` / `attack_rear` (breath, dive, tail sweep), `attack_windup`,
`strike`, `hit`, `death`. The attacks share one length and one wind-up end, so a game can scrub any of them by its
telegraph. Wings are posed by direction (a spread, a fold, and an elevation about the body's long axis), so the same
clip code fits any wing whose bones follow this table.

Moth wings are spar-less: `wing_*` chains only, animated by the flyer archetype.

## The card: `rig.skeleton`

`publish.py` writes it into `model.json` from the rig's own record. Every field holds bone names.

```jsonc
"skeleton": {
  "archetype": "hexapod",
  "root": "root", "body": "body", "spine": ["body"], "head": "head",
  "abdomen": ["abdomen_1", "abdomen_2"],
  "legs": [                                   // front to back, left before right
    {"name": "leg1.L", "side": "L", "bones": ["leg1_1.L", "leg1_2.L", "leg1_3.L"], "foot": "leg1_3.L",
     "ik": "ik_leg1.L", "parent": "head"},
    ...],
  "mandibles": [{"name": "mandible.L", "side": "L", "bones": ["mandible.L"], "parent": "head"}, ...],
  "antennae": [...], "tail": [...], "wings": [...], "tentacles": [...], "jaw": "jaw"
}
```

A humanoid's is `{"archetype": "humanoid", "root": "root", "humanoid": {<Unity HumanBodyBones name>: <bone>}}`, which
is exactly a Unity `HumanDescription` bone map.

## Ingestion & Universal Semantic Bone Dictionary

When importing pre-rigged models, Autorig Workbench's semantic bone engine (`autorig/core/skeletons.py`) automatically recognizes major game engine and DCC armature conventions and maps them 1:1 to canonical chains:

| Armature Convention | Signature Bone Names | Auto-Detected As |
|---|---|---|
| **Unreal Engine Mannequin** | `pelvis`, `spine_01`, `upperarm_l`, `thigh_l`, `calf_l`, `ball_l` | `unreal` |
| **Unity Mecanim** | `Hips`, `Spine`, `LeftUpperArm`, `LeftLowerArm`, `LeftUpperLeg` | `unity` |
| **Adobe Mixamo** | `mixamorig:Hips`, `mixamorig:LeftArm`, `mixamorig:LeftUpLeg` | `mixamo` |
| **Blender Rigify** | `DEF-spine`, `DEF-upper_arm.L`, `DEF-forearm.L`, `DEF-thigh.L` | `rigify` |
| **3ds Max Biped (CS)** | `Bip01 Pelvis`, `Bip01 L UpperArm`, `Bip01 L Thigh`, `Bip01 L Calf` | `biped` |
| **Reallusion AccuRig / CC** | `CC_Base_Pelvis`, `CC_Base_L_Upperarm`, `CC_Base_L_Thigh` | `accurig` |
| **Source Engine / Valve** | `ValveBiped.Bip01_Pelvis`, `ValveBiped.Bip01_L_UpperArm` | `valve` |
| **Tripo AI** | `bone_0`, `bone_1`, `bone_2`... | `tripo` |

When detected, the auto-rigger preserves existing joint coordinates directly from the source export, retaining fingers, toes, and limb hinge locations with 100% fidelity without discarding bones.

