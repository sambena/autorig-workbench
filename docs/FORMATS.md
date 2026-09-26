# Output formats

What the tool writes for an engine importer to read. Units: the rig is in the source's own units unless a file says
metres; `metres` on the card is the real length of the model's longest axis, so `metres / longest` scales it.
Facing: Blender -Y, written to FBX as Y up, facing +Z (Unity's forward; Godot's +Z; Unreal's +X after its FBX import).

## The card: `<model>/model.json` (publish.py)

```jsonc
{
  "name": "wolf",
  "group": "Creatures",              // the key is the collection's card.group_field
  "tint": null, "metres": 1.6, "role": "creature",   // the collection's card.fields, then rig.json "card"
  "source_fbx": "wolf.fbx",          // the source export (any format; the key keeps its name)
  "textures": ["wolf.fbm/wolf_basecolor.jpg"],
  "rigged_fbx": "rigged/wolf.fbx",   // mesh and deform bones, at the budget
  "rigged_blend": "rigged/wolf.blend",
  "rig": {
    "kind": "build", "bones": 31, "deform_bones": 24, "rest_shift": 0.0,
    "skeleton": { "archetype": "quadruped", ... },     // which bone plays which role: SKELETONS.md, "The card"
    "audit": { "pass": true, "grade": "PASS", "bleed_pct": 1.2, "combined_tears": 0, "bend_tears": 0, "head_pct": 9.1, "max_influences": 4 },
    //          grade PASS / CHECK / FAIL (PIPELINE.md, "Grades"); pass is the strict result, grade == "PASS"
    "clips": { "fbx": "clips/wolf.fbx", "blend": "clips/wolf_clips.blend", "json": "clips/wolf_clips.json", "names": [...] },
    "qa": "rigged/wolf_qa.png",
    "builder": "my_builder.py"       // only for kind "custom"
  },
  "note": "..."                      // the collection's card.notes
}
```

`<group>/pack.json` is `{"<group_field>": "<group>", "models": [every card in the group]}`.

## Creature clips: `autorig-clips/1` (make_clips.py, archetypes walker, flyer, exploder, swimmer, turret)

In the model's `clips/` folder:

| File | Holds |
|---|---|
| `<model>.fbx` | the rig, the mesh at its budget, and every clip as a take named for it, in the same units and bone space as `rigged/<model>.fbx`, so either file's takes bind to the other |
| `<model>_clips.blend` | the rig with every clip as an action: the source to animate further from |
| `<model>_clips.json` | the manifest below |

```jsonc
{
  "format": "autorig-clips/1",
  "model": "wolf", "display": "Wolf", "category": "Creatures",
  "fbx": "wolf.fbx", "blend": "wolf_clips.blend", "rig": "rigged/wolf.fbx",
  "units": "...", "metres": 1.6, "unitsPerMetre": 0.62,
  "fps": 24, "archetype": "walker",
  "skeleton": { ... },                                   // the card's rig.skeleton
  "contract": "autorig-clips-contract/1", "speedUnits": "metres per second at the manifest's 'metres' size; ...",
  "clips": [ {"name": "walk", "take": "walk", "frames": 16, "seconds": 0.6667, "loops": true,
              "slot": "locomotion", "rateFollowsSpeed": true, "speed": 1.9,
              "events": [{"name": "footfall", "time": 0.0, "detail": "leg1.L,..."}]},
             {"name": "attack", "take": "attack", "frames": 24, "seconds": 1.0, "loops": false,
              "slot": "attack", "rateFollowsSpeed": false, "windUpEnd": 0.5833, "windUpSeconds": 0.5833, "events": [...]}, ... ],
  "windUpEnd": 0.5833,                                   // a fraction of "attack" (each attack clip has its own)
  "walkSpeed": 1.9,                                      // the walk's speed in metres a second, as its clip's "speed"
  "decimation": {"triangles_before": 19000, "triangles": 2000, "max_influences": 4},
  "licence": "..."                                       // the collection's licence line
}
```

Clip names: `idle`, `walk` (or a swimmer's swim), `attack`, `hit`, `death`, plus `attack_windup` (the wind-up to full,
held) and `strike` (from the release on) cut from `attack`; flyers have `fly`, exploders `arm` and `explode`. Events:
`footfall`, `windup_full`, `strike_release`, `strike_impact`, `hit`, `death_rest`, `armed`.

## Winged export: `autorig-export/1` (make_clips.py, archetype winged)

Beside the rig, in `<rig folder>/<Display>/`:

| File | Holds |
|---|---|
| `<Display>.fbx` | mesh (at `triangles`), rig and every clip as a take, **in metres** |
| `<Display>.jpg` | the base colour texture |
| `<Display>.json` | the manifest below |

and `<rig folder>/<model>.blend` gains every clip as an action.

```jsonc
{
  "format": "autorig-export/1",
  "name": "Wyvern", "category": "Creatures", "fbx": "Wyvern.fbx",
  "units": "metres ...", "metres": 9.0, "size": {"length": 9.0, "height": 4.1, "width": 7.5},
  "facing": "+Z ...", "pivot": "feet", "pivotNote": "...",
  "skeleton": "winged/1 (SKELETONS.md)",
  "bones": {"root": "root", "spine": [...], "neck": [...], "head": "head", "jaw": "jaw", "tail": [...],
            "leg_left": [...], "arm_left": [...], "wing_left": [...], "wing_fingers_left": [[...], ...], ...},
  "motion": "authored clips (...)", "flies": true, "frameRate": 30,
  "contract": "autorig-clips-contract/1", "speedUnits": "...",
  "clips": [ {"name": "fly", "take": "fly", "length": 1.0, "frames": 30, "loop": true,
              "slot": "locomotion", "rateFollowsSpeed": false, "speed": 6.2, "events": [...]}, ... ],
  "windUpEnd": 0.5556, "notes": {...},
  "textures": [{"file": "Wyvern.jpg", "role": "baseColor", "material": "..."}],
  "decimation": {...}, "provenance": {"pack": "...", "rig": "...", "card": {...}},
  "licence": "...", "limits": ["..."]
}
```

With `--split-clips <dir>`, make_clips also writes a split pair for engines that bind takes to a separate mesh:
`<dir>/<Display>.fbx` (armature and takes only, the rig's own units), `<dir>/<Display>_model.fbx` (the mesh on the
same armature) and `<dir>/<Display>_clips.json` (`contract`, `speedUnits`, `clips` with `seconds`, `loops` and the
contract's fields, `fps`, `walkSpeed` from the spec's `splitWalkSpeed`, `windUpEnd`).

## The engine contract: `autorig-clips-contract/1` (core/clip_contract.py)

Every clips manifest above (creature, winged, split pair) says, for each clip, how an engine should play it, so a
game needs no name map or timing guesses of its own:

| Field | Meaning |
|---|---|
| `slot` | where the clip goes: `idle`, `locomotion`, `attack` (any full attack, `attack_<kind>` included), `attack_windup` (the rear to full, held), `attack_strike` (the release on), `hit`, `death`, `arm`, or `extra` (jumps, dodge, block, roll, perch: authored for engines that want them, safe to ignore) |
| `rateFollowsSpeed` | `true`: speed it up or slow it to match ground speed (a walk: rate = ground speed / `speed`, so planted feet stay planted). `false`: always play at its own rate, however fast the body goes (a flyer's `fly` and `idle`: a wingbeat slowed to travel speed looked half-hearted) |
| `speed` | on locomotion clips, when known: metres a second at the manifest's `metres` size |
| `windUpEnd`, `windUpSeconds` | on attack clips: where the wind-up ends in **this** clip, as a fraction of it and in seconds. Scrub `[0, windUpSeconds]` from the telegraph, then play the rest as the strike. `attack_windup` is all wind-up (1), `strike` none (0) |

Manifest level: `contract` names this contract, and `speedUnits` says what speeds are in. **An engine that shows
the model at a size other than `metres` scales every speed by its size / `metres`**: a model fitted to 2 m that the
manifest measures at 1 m walks twice as fast in metres a second. The top-level `windUpEnd` (a fraction of the plain
`attack`) and `walkSpeed` stay for engines that already read them.

## Clip audit: `autorig-clip-audit/1` (clip_audit.py)

`<AUTORIG_WORK>/clip_audit/<model>.json`, written after Clips (the GUI and the batch run it straight after
make_clips; `python autorig/cli/run.py clipaudit <model>` on its own). Every clip in the manifest is played frame
by frame on the deformed mesh:

```jsonc
{
  "format": "autorig-clip-audit/1", "model": "canine", "manifest": "canine/clips/canine_clips.json",
  "feet": ["leg_front.L_2.L", ...], "body": "root", "grounded": true,
  "grade": "FAIL",                               // the worst clip's, "extra" clips not counted
  "clips": [ {"name": "walk", "slot": "locomotion", "frames": 17, "grade": "FAIL",
              "checks": {"slide": {"value": 0.257, "grade": "FAIL", "planted_share": 0.53, "root_motion": false},
                         "floor": {"value": 1.2, "grade": "CHECK", "hover_pct": 0.0},
                         "pops": {"value": 0, "grade": "PASS", "worst_step_deg": 9.1, "at": {...}},
                         "seam": {"value": 0.0, "grade": "PASS", "jump_deg": 0.0},
                         "symmetry": {"value": 0.03, "grade": "PASS"}}}, ... ],
  "limits": {...}, "seconds": 0.2
}
```

A foot's contact point is the tip of its leg's last bone (what the walk plants on its IK target); its sole is the
lowest point of the skin that bone owns near that tip, which the floor checks read. `slide` is the median speed of planted feet over the ground as a share of the walk's `speed` (a clip played
in place is moved at that speed, one carrying its own travel is taken as it is); `floor` is the deepest a foot goes
under where it stands at rest, and on a walker's walk and idle how high the lowest foot hovers, in % of the height;
`pops` counts frames where a bone turns far more than it usually does in a frame (hits and deaths get more room);
`seam` is a loop's last-to-first pose, and how much more its motion changes there than anywhere else in the clip;
`symmetry` compares left and right feet's reach. Limits: core/clip_grades.py.

## Work files (`AUTORIG_WORK`)

| Folder | Holds |
|---|---|
| `qa/<model>.json`, `qa/<model>.png` | the rig log and the bend test (top row: the skeleton at rest; bottom: posed) |
| `audit/<model>.json`, `_skin.png`, `_bend.png` | the audit: every measure; `verdict` (`grade`, `pass`, `checks` with `value`, `limit`, `check_limit`, `ok`, `grade` and for tears `gap_pct`, and `warnings`); `tears` and `tear_sites`, where the tears are (PIPELINE.md) |
| `audit/_collection.json` | the last `audit_all.py` table, worst first |
| `survey/<model>.json` | what the source export holds |
| `facing/<model>__<view>.png` | four views, for reading `forward` |
| `measure/` | measuring sheets |
| `clips/<model>/<clip>_<frame>.png` | clip preview frames |
