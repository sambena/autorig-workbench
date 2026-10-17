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
| trimesh | MIT | mesh measures for the suggest step's body plan: parts, stable poses, symmetry, convex hulls (installs scipy, BSD-3-Clause, with it) |
| xatlas | MIT | automatic UV unwrap and atlas packing |
| coacd | MIT | convex pieces for game colliders and rough part splits |
| manifold3d | Apache-2.0 | robust booleans: carving parts, merging shells |
| robust-laplacian | MIT | weight smoothing that copes with non-manifold meshes |
| open-clip | MIT | a picture classifier, on the CPU, for the suggest step's picture vote (pulls in PyTorch, about 1 GB; its weights, about 600 MB, download free from Hugging Face on first use into the extras folder's `models/`) |

## For step authors

- **Inside a Blender step:** call `extras.activate()` first. It puts the folder at the *end* of `sys.path`, so
  Blender's own numpy, which bpy is built against, still wins.
- **Optional use:** check `extras.have("pymeshlab")`. When the extra is missing, fall back to the plain path.
- **Required use:** `extras.need("pymeshlab", "texture-safe decimation")` stops with the install command.
- **Outside Blender:** `extras.run_python([...])` runs Blender's Python (no bpy) with the extras on its path.
- **More than one package:** an extra's pip field may list several, space-separated (`"trimesh scipy"`).
- **New extras:** add the extra to `EXTRAS` in `core/extras.py`. Its licence must fit the tool's GPL-3.0; the
  tests check the list.
