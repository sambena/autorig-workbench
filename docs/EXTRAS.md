# Optional extras

The tool needs nothing from pip. A few steps do better with a library. These are the **extras**: optional
packages installed for Blender's own Python, into one folder per Python version.

    python autorig/cli/run.py extras                          list them, and what is installed
    python autorig/cli/run.py extras install pymeshlab trimesh
    python autorig/cli/run.py extras install all

The folder is `%LOCALAPPDATA%\autorig\extras\py313` on Windows and `~/.local/share/autorig/extras/py313`
elsewhere. Set `AUTORIG_EXTRAS` to use a different one. Deleting the folder uninstalls everything.

| Extra | Licence | For |
|---|---|---|
| pymeshlab | GPL-3.0 | texture-safe decimation, mesh repair, isotropic remesh, connected parts |
| pymeshfix | GPL-3.0 | one watertight surface from a messy multi-shell mesh |
| trimesh | MIT | mesh measures: parts, stable poses, symmetry, convex hulls |
| xatlas | MIT | automatic UV unwrap and atlas packing |
| coacd | MIT | convex pieces for game colliders and rough part splits |
| manifold3d | Apache-2.0 | robust booleans: carving parts, merging shells |
| robust-laplacian | MIT | weight smoothing that copes with non-manifold meshes |
| open-clip | MIT | a picture classifier, on the CPU, for the suggest step (pulls in PyTorch, about 1 GB) |

## For step authors

- **Inside a Blender step:** call `extras.activate()` first. It puts the folder at the *end* of `sys.path`, so
  Blender's own numpy, which bpy is built against, still wins.
- **Optional use:** check `extras.have("pymeshlab")`. When the extra is missing, fall back to the plain path.
- **Required use:** `extras.need("pymeshlab", "texture-safe decimation")` stops with the install command.
- **Outside Blender:** `extras.run_python([...])` runs Blender's Python (no bpy) with the extras on its path.
- **New extras:** add the extra to `EXTRAS` in `core/extras.py`. Its licence must fit the tool's GPL-3.0; the
  tests check the list.

## Tools: programs for the export passes

Two optional programs are installed the same way. They are the projects' own release binaries, downloaded from
their GitHub releases into a `tools` folder beside the extras (`%LOCALAPPDATA%\autorig\extras\tools`, or
`AUTORIG_TOOLS`). Nothing is installed system-wide and no Node is needed. A program of the same name on `PATH` is
used too.

    python autorig/cli/run.py extras install gltfpack gltf-validator

| Tool | Version | Licence | For |
|---|---|---|---|
| gltfpack ([meshoptimizer](https://github.com/zeux/meshoptimizer)) | 1.3 | MIT | optimises Web and Godot GLBs in export packages |
| gltf-validator ([Khronos](https://github.com/KhronosGroup/glTF-Validator)) | 2.0.0-dev.3.10 | Apache-2.0 | the pass/fail gate on every GLB in an export package |

`run.py extras` lists them with the extras. Without them, export works as before and each package says the pass
was skipped. See [EXPORT.md](EXPORT.md). Versions and download links are pinned in `TOOLS` in
`core/export_passes.py`.
