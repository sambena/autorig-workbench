# Model Studio

Open **Model → Model Studio** in the workbench. Studio shares the existing model collection,
session authentication and cancellable job queue. Settings live in each model's `studio.json`;
outputs live in `<model>/_studio/<tool>/`. These folders are excluded from source discovery.
Original source files, rigs and clips are not overwritten by Studio tools.

The same operations are available through `python autorig/cli/run.py studio <tool> <model>`.
Use `--settings settings.json` to run a validated settings object without changing saved settings.
Each queued job captures its own settings snapshot, so subsequent form edits do not change it.

## Tools

| Tool | Result | Notes |
| --- | --- | --- |
| `lods` | Separate GLB or FBX bind meshes per LOD, plus `lods.json` | Static and skeletal meshes; default ratios 1 / .4 / .15. Mixed weights and bone endpoints receive protection. Compare rest, bend and twist surfaces; retry at less reduction if error exceeds 2% of mesh size. Shape-key meshes retain full geometry. |
| `vehicle` | GLB, FBX, editable Blender file, `parts.json` | Static source only. Detect four wheels, fit hubs and optionally straighten axles. Override wheel names by corner and pivots in prepared Blender metres. Missing mirrored wheel carving is opt-in. Preview includes wheel spin. |
| `reference` | Front, Side, Back, Face PNGs and `comparison.png` | Optional reference directory. Filenames end in Front, Side, Back, Face; Portrait can stand in for Face. Rendering needs Blender; sheet layout needs Pillow. |
| `materials` | Poly Haven maps, lossless packed PNG and provenance | Requires network and Pillow. ORM = AO / roughness / metallic (zero); RAH = roughness / AO / height. Normal maps use OpenGL convention. Assign maps in your DCC/engine; downloading does not replace baked textures on a model. |
| `foliage` | Seeded 12-cell alpha atlas, rectangles, crossed-card OBJ/MTL | Includes tree sprays, litter, twigs, flowers, weeds, corn and green/dry grass. Optional `wind.glb` stores a configurable height curve in vertex alpha. A compatible shader must consume the mask. |
| `generate` | Downloaded candidate models and durable `generation.json` | Requires configured Hyper3D CLI. Front/Side/Back references required, Face optional. New submissions use provider credits; no paid generation is part of tests. Existing IDs are polled/downloaded again without resubmission. |
| `engine` | One engine-ready FBX, maps in `<Name>.fbm/`, `engine.json` | Reads the `lods` tool's GLB levels (run it with format `glb`). `auto` picks the layout from LOD0. Materials are flattened to Principled + images so the FBX keeps its textures. Add to Unity/Unreal export packages with `--engine-lods`. See below. |
| `validate` | `quality.json` from the current Preview GLB | Evaluates every imported take separately, including all model meshes and morph owners. Default sampling every frame. Reports motion, seam gaps, stretching, triangle collapse, floor penetration and near-ground foot speed. Explicitly list looping clip names to check endpoints with root travel removed. |

Pillow is optional and needed only for image tools (`python -m pip install Pillow` in the
Python environment that launches the workbench). Blender's bundled Python supplies NumPy
for geometry tools. No network is used by LODs, vehicles, references or validation.
`AUTORIG_TIMEOUT_STUDIO` controls the outer job limit (default 2100 seconds); cancellation
terminates the job's subprocess tree. Provider work already submitted may continue remotely.

Reference folders in the browser must be under the collection or work folder. Use the existing
Import flow to bring reference files into a model folder. Downloaded generation candidates
remain under `_studio` until explicitly imported as a source/model; this avoids silently replacing
a rig's input. Interrupted submissions with no saved generation ID stop for recovery rather
than automatically spending again. A changed reference set/tier/quality requires archiving the
old candidate directory. Recover the provider ID into its `generation.json` before resuming.

## Engine LOD FBX

The `lods` tool writes one GLB per level, and the export packages copy them loose. The `engine`
tool merges them into the one FBX an engine imports, in one of three layouts:

| Layout | Hierarchy | Notes |
| --- | --- | --- |
| `static` | `<Name>` root, `<Name>_LOD0..n` children | Each level's meshes are joined. The space transform is baked, so Unity builds its LOD Group from the names on import. |
| `skinned` | `<Name>` root, the armature, `<Name>_LOD0..n` skinned to it | Every level must have the same bone set. Clips are taken from `animation` (default: the model's `rigged/preview.glb`, every clip the workbench made) and baked one take per clip. A clip that animates a bone the prepared rig lacks is refused. |
| `vehicle` | `<Name>` root, `Body` and `Wheel_FL/FR/RL/RR` empties at the pivots, `<Part>_LOD0..n` under each | For a model whose source is the `vehicle` tool's output. Pivots come from `parts` (a `parts.json`), else this model's `_studio/vehicle/parts.json`, else the wheel origins in LOD0. Wheel levels are re-centred on the hub (X is the axle); a level finer than the one before reuses the coarser mesh. |

Game-specific choices are settings, never constants: `name` (the resource name; blank = model),
`clips` (which source clips to keep; empty = all), `clip` (rename a single kept clip, e.g. `walk`),
`fps` (scene rate for import and bake; default 24), `wind` (`auto` keeps the one colour channel whose
alpha falls below `wind_alpha` as COLOR0 when there is exactly one, `require` fails without it, `off`
leaves colours alone) and `roughness` (the flattened materials' roughness, default 0.75). Paths in
`animation` and `parts` are relative to the model folder and must stay under the models or work folder.

    python autorig/cli/run.py studio engine canine --settings engine.json
    python autorig/steps/export.py canine --target unity --engine-lods     # adds LODs/<Name>.fbx, .fbm, engine.json

`--engine-lods` applies to the Unity and Unreal packages (with `--target all`, only those two); Godot
and Web packages are unchanged. The FBX uses Unity's axes (-Z forward, Y up); Unreal's importer
converts them. In the Unity package the FBX goes through the `unity-fbx` pass ([EXPORT.md](EXPORT.md)): a rig root
that would import at -90 X is rewritten so every root reads rotation (0,0,0) and scale 1. FBX takes are named `<armature>|<clip>` by Blender's exporter.

Adapted from storm-chaser `Tools/blender/package_autorig_lods.py`, `package_skinned_lods.py` and
`package_vehicle_lods.py` (written 2026-10-16 for the farm model pilots). Those scripts hard-coded
the clip name `walk`, 24 fps and a required wind channel; here they are the settings above.

## Deformation improvements

Geometry healing now precedes deliberate seam splitting. Bone-heat recovery preserves those
splits. Deliberate cuts receive the `_AUTORIG_CUT` point attribute, retained in GLB previews;
the animation checker excludes explicitly tagged cut vertices. Different weights by themselves
never exempt a coincident seam: differing weights are often the cause of the crack.
Bone custom-shape helpers created by Blender's importer are excluded from model measurements.

The closing weight healer trials its candidate against up to twelve deform joints in bend
and twist poses, rejecting a candidate that worsens sampled stretch, compression, collapse
or seam gaps. Existing deliberate vertex locks still apply. `rig.heal_validate` defaults to
true; false retains the old gradient-only behavior. These probes do not cover every possible
pose, collision or animation. Existing engine trimming also protects joint regions.

Audits include seam-gap and collapsed-triangle diagnostics. To test additional motion ranges:

```json
{"rig": {"audit_motion_ranges": {"arm_2.L": {"bend": [-40, 40, 90], "twist": [-60, 60]}}}}
```

Each list holds 1–12 angles between -180 and 180 degrees. Existing PASS/CHECK/FAIL thresholds
are preserved; twist, seam and collapse measurements in the rig audit remain diagnostics.
Animation validation fails on still takes, seam cracks, stretched edges or requested loop
mismatches. Floor and foot-speed figures are diagnostics: in-place animation needs intended
travel speed to interpret sliding. The checker measures collapse, not a general proof of
triangle inversion or self-intersection. Custom seam attributes may not survive FBX; the
GLB preview is the authority for marked cuts.

LOD surface error uses a bounded vertex sample in both directions and sampled bend/twist
poses. A requested ratio is a target, not a guaranteed triangle budget. Reports record actual
triangle counts and accepted ratios. LOD exports contain bind meshes; use existing animation
exports alongside them. A morph mesh can therefore exceed a requested budget.
The optional base triangle budget applies before the level ratios. Optional normalization
turns the source front to -Y, grounds and centers the hierarchy, and uniformly fits either
height or footprint length in metres, preserving the armature and part hierarchy. It is off
by default. Coincident UV splits with matching weights weld before reduction; tagged
articulation cuts are protected.

## Port provenance

Adapted with Sam's authorization from `storm-chaser/Tools` at local HEAD
`822a3481bb68d5fd2a4062b0b8774632be1d9e36`, including the working-copy additions inspected
on 2026-10-16. Vehicle geometry helpers and foliage artwork are isolated under
`autorig/core/studio_ports/`; reference, surface and generation workflows were adapted
into reusable workbench modules. No private generated models, reference photos, downloaded
textures, provider credentials or game assets are included in this repository.

The other three Tools directories mostly duplicate these files. The main project includes
tree wind weights/base triangle budgets and the green/dry grass cells. The original
`gen_models_nature.py` and `gen_models_farm.py` create game-specific texture bands; their
3D geometry is implemented in the game's C# builders, outside Tools. Those Unity runtime
builders, voice generators and promotional artwork are not part of this model-tool port.
