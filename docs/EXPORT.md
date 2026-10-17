# Engine export packages

**File > Export** in the GUI, or on the command line:

    python autorig/steps/export.py wolf --target unity          # unreal | unity | godot | web | all (default)
    python autorig/steps/export.py wolf --target web --no-optimise --no-validate
    python autorig/steps/export.py wolf --target unity --engine-lods

Each package is a folder in `<work>/export/<model>/<target>/` and a zip, `<work>/export/<model>_<target>.zip`
(`--target all` also writes `<model>_all_presets.zip`).

| Target | Model files | Also |
|---|---|---|
| Unreal 4 / 5 | the budgeted FBX and the clips FBX | Mannequin bone map, import-dialog notes, guide |
| Unity | the budgeted FBX and the clips FBX, **rewritten for Unity's axes** (below) | Humanoid slot notes, guide |
| Godot 4 | the budgeted FBX and the clips FBX (the viewer's GLB only when there is no FBX) | `.import`, a controller script, guide |
| Web | the viewer's GLB (full resolution, every clip) | manifest, a `<model-viewer>` page, guide |

## Passes

Every package runs its passes before it is zipped, and lists what each did in `export_report.json` (also printed as
`PASS` lines and shown under the File > Export dialog). A pass whose program is not installed says
**skipped** and why; the package is otherwise exactly what it was without it.

| Pass | Packages | What it does | Needs |
|---|---|---|---|
| `unity-fbx` | Unity (and its `LODs/` FBX) | Reads each FBX as Unity does. When a root would import rotated or scaled, rewrites it so every root reads rotation (0,0,0) and scale 1, then checks the result again. | Blender, only for a file that needs the rewrite |
| `gltf-validator` | Web, Godot | The Khronos glTF-Validator on every GLB. **A GLB with errors stops the export** (warnings are listed). Runs again after gltfpack. | `gltf-validator` |
| `gltfpack` | Web, Godot | Optimises the model GLB in place with gltfpack. Web: meshopt-compressed and quantised (`-cc`; three.js and `<model-viewer>` decode it). Godot: merged and reordered only (`-noq`), since Godot reads neither extension. Named nodes and materials are kept (`-kn -km`). | `gltfpack` |

Install the two programs (official release binaries from each project, about 4 MB, into the tools folder beside
the extras; no Node and nothing system-wide):

    python autorig/cli/run.py extras install gltfpack gltf-validator

`--no-optimise` and `--no-validate` turn the glTF passes off for one export.

### Why Unity needs its own FBX

The rig, trim and clips steps write one FBX for every engine: -Z forward, Y up, units applied,
`bake_space_transform`. Blender bakes that space transform into meshes but not into an armature, so in Unity the
rig's root object showed **-90 on X** (`core/fbx_inspect.py` reads it straight from the file:
`python autorig/core/fbx_inspect.py rigged/wolf.fbx`). `steps/unity_fbx.py` imports the FBX into an empty scene and
writes it again with the method of EdyJ's *Unity FBX format* exporter
([blender-to-unity-fbx-exporter](https://github.com/EdyJ/blender-to-unity-fbx-exporter), MIT): each root's rotation is
applied, a -90 X rotation is applied to its data and +90 X put back on the object, so the exporter's own Z-up to
Y-up turn cancels it. Units are written as metres (UnitScaleFactor 100): with **Convert Units** on, the model
imports at scale 1. Forward is unchanged: a model facing -Y in Blender faces +Z, Unity's forward.

The rewrite keeps the pose and the motion. The tests (`tests/test_export_passes.py`) sample every bone's head and
tail and the deformed mesh at five frames of every take in both files and require them to agree to 0.1 mm; on the
armoured guard (15 takes, 29 bones) the largest difference was under 0.001 mm. Takes keep their names. Object-level
keys on the rig (which carried the old -90) are dropped; root motion lives on the root bone and is kept.

A file that already imports unrotated (a static Studio engine FBX) is copied as it is. Without Blender, a file that
needs the rewrite is copied as it is and the pass says so.

Unreal (Convert Scene) and Godot (its FBX importer) convert axes themselves and get the shared FBX unchanged.
