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

## Which steps use them

| Extra | Used by |
|---|---|
| pymeshlab | trim, game-prop and Studio LODs (quadric collapse), game-prop `--atlas`, Mesh Doctor `--repair` |
| pymeshfix | Mesh Doctor `--watertight` |
| xatlas | game-prop `--atlas`, Mesh Doctor `--watertight --bake` |
| coacd | game-prop `--colliders` |
| manifold3d | game-parts `"cut"` parts, game-prop `--carve-wheels`, Mesh Doctor `--watertight` (joins the shells) |

[GAME-TOOLS.md](GAME-TOOLS.md), "Mesh prep with the extras", has what each does and the before/after numbers. The
library calls go through `core/mesh_prep.py` (numpy arrays only) and `core/mesh_bridge.py` (Blender meshes in and
out). `tests/test_mesh_prep.py` runs `tests/mesh_prep_libs.py` in Blender's Python with the extras when both are
installed, and skips it otherwise.

Known trouble: PyMeshLab's `meshing_close_holes` crashed the process (an access violation) on a mesh whose
`MeshSet` had been measured first; `mesh_prep.repair` measures a copy. PyMeshLab's texture-aware collapse only takes
UVs that came from a file, so `mesh_prep.decimate` hands it a temporary OBJ.

## For step authors

- **Inside a Blender step:** call `extras.activate()` first. It puts the folder at the *end* of `sys.path`, so
  Blender's own numpy, which bpy is built against, still wins.
- **Optional use:** check `extras.have("pymeshlab")`. When the extra is missing, fall back to the plain path.
- **Required use:** `extras.need("pymeshlab", "texture-safe decimation")` stops with the install command.
- **Outside Blender:** `extras.run_python([...])` runs Blender's Python (no bpy) with the extras on its path.
- **New extras:** add the extra to `EXTRAS` in `core/extras.py`. Its licence must fit the tool's GPL-3.0; the
  tests check the list.
