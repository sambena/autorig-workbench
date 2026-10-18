# Credits

Autorig Workbench stands on other people's work. This page lists every project, paper, library, data set and
service it borrows from or works with, what it took, and under which licence. If you find something missing,
please open an issue: credit is owed, not optional.

The workbench itself is GPL-3.0-or-later (see [Licence](#licence) below).

## Code ported into the workbench

Code (or a close reimplementation of it) that lives in this repository.

| Project | Authors | Licence | What we took | Where |
|---|---|---|---|---|
| [Mesh2Motion](https://github.com/Mesh2Motion/mesh2motion-app) | [Scott Petrovic](https://github.com/scottpetrovic) and contributors | MIT (code), CC0 (models, rigs, animations) | The whole web app as a submodule (our fork [sambena/mesh2motion-app](https://github.com/sambena/mesh2motion-app)); its skeleton templates and animation library for fitting and clips; its Mixamo bone table (`src/retarget/bone-automap/MixamoMapper.ts`) as the cross-check of our `mesh2motion` convention, and its 178 human clips played on Mixamo-rigged people | `third_party/mesh2motion-app` (our embedding bridge is in its `src/lib/bridge/`, see `docs/M2M-BRIDGE.md`), `autorig/steps/m2m_rig.py`, `autorig/core/m2m_templates.py`, `autorig/steps/m2m_quadruped.py`, `autorig/core/m2m_catalog.py` (its RigConfig file list), `autorig/core/skeletons.py`, `autorig/steps/m2m_clips.py` |
| [Mesh2Motion](https://github.com/Mesh2Motion/mesh2motion-app) skin solver | [Scott Petrovic](https://github.com/scottpetrovic) and contributors | MIT | Its skin-weight solver (`src/lib/solvers/`: closest bone midpoint, the human crotch rule, the knuckle fix, the optional arm and head planes, 3-ring torso smoothing, limbs blending toward the child only, normalisation), ported faithfully to NumPy for the M2b skinning bake-off and checked against the TypeScript on M2M's own sample models | `autorig/core/m2m_skin.py`, `autorig/core/m2m_skin_blender.py`, `scripts/m2m_skin_parity.py` |
| [GenMM](https://github.com/liyuuuu98/GenMM) | [Weiyu Li](https://github.com/liyuuuu98), Xuelin Chen, Peizhuo Li, Olga Sorkine-Hornung, Baoquan Chen | Apache-2.0 | Generative motion matching (the pyramid, match-and-blend, patch-coherent loss), ported from PyTorch to NumPy; contact fixing ideas | `autorig/core/genmm/` (its own LICENSE and NOTICE), `autorig/core/foot_contact.py` |
| [Efficient-GPNN](https://github.com/ariel415el/Efficient-GPNN) | [Ariel Elnekave](https://github.com/ariel415el) | Apache-2.0 | Patch extraction and nearest-neighbour helpers, via GenMM | `autorig/core/genmm/patches.py` |
| [Spider Walker](https://github.com/iltaen/spider_walker) | [Roman Gibadullin](https://github.com/iltaen) | MIT | Predicted foot targets, ground raycast, smoothstep step with lift, round-robin legs | `autorig/core/leg_planting.py` |
| [Wiggle 2](https://github.com/shteeve3d/blender-wiggle-2) | [Steve Miller](https://github.com/shteeve3d) | GPL-3.0 | Spring-bone physics (move, constrain, velocity update) for tails, ears and antennae | `autorig/core/spring_bones.py` |
| [Robust Skin Weights Transfer](https://github.com/rin-23/RobustSkinWeightsTransferCode) | [Rinat Abdrashitov](https://github.com/rin-23), Kim Raichstat, Jared Monsen, David Hill | MIT | Weight transfer by matching and inpainting, reimplemented in NumPy | `autorig/core/weight_transfer.py` |
| [MoMask](https://github.com/EricGuo5513/momask-codes) | [Chuan Guo](https://github.com/EricGuo5513) and co-authors | MIT | Foot contact detection (low and slow), pin-and-ease clean-up, height-weighted foot-slide measure | `autorig/core/foot_contact.py` |
| [blender-to-unity-fbx-exporter](https://github.com/EdyJ/blender-to-unity-fbx-exporter) | Angel García ([EdyJ](https://github.com/EdyJ)) | MIT | The axis fix that makes Unity import root objects with rotation 0 and scale 1 | `autorig/steps/unity_fbx.py` |
| [EDGE](https://github.com/Stanford-TML/EDGE) | Jonathan Tseng, Rodrigo Castellon, C. Karen Liu | MIT | The SMPL motion layout and neutral rest offsets used to turn its output into BVH | `autorig/steps/smpl_to_bvh.py` |

## Algorithms and papers

| Work | Used for |
|---|---|
| I. Baran, J. Popović, [*Automatic Rigging and Animation of 3D Characters*](https://doi.org/10.1145/1276377.1276467) (SIGGRAPH 2007) | Bone-heat skin weights, through Blender's automatic weights, on a voxel proxy |
| R. Abdrashitov et al., [*Robust Skin Weights Transfer via Weight Inpainting*](https://github.com/rin-23/RobustSkinWeightsTransferCode) (SIGGRAPH Asia 2023 Technical Communications) | Moving weights from the proxy onto the real mesh |
| A. Aristidou, J. Lasenby, [*FABRIK: A fast, iterative solver for the Inverse Kinematics problem*](https://doi.org/10.1016/j.gmod.2011.05.003) (Graphical Models, 2011) | Leg IK in clip clean-up |
| W. Li et al., [*Example-based Motion Synthesis via Generative Motion Matching*](https://weiyuli.xyz/GenMM/) (SIGGRAPH 2023) | Clip variants |
| C. Guo et al., [*MoMask: Generative Masked Modeling of 3D Human Motions*](https://github.com/EricGuo5513/momask-codes) (CVPR 2024) | Foot contact and skate measures |
| M. Loper et al., [*SMPL: A Skinned Multi-Person Linear Model*](https://smpl.is.tue.mpg.de) (SIGGRAPH Asia 2015) | The joint layout of SMPL motion data. No SMPL model files are included; those are under the SMPL licence from the Max Planck Institute |

## Bundled third-party files

| Project | Licence | Where |
|---|---|---|
| [three.js](https://github.com/mrdoob/three.js) ([mrdoob](https://github.com/mrdoob) and contributors), with its [fflate](https://github.com/101arrowz/fflate) ([101arrowz](https://github.com/101arrowz)) for FBX | MIT | `autorig/gui/vendor/three/` (its LICENSE and VERSION.txt there): the page's 3D preview. Also inside the Mesh2Motion app, as its npm dependency. |
| [Mesh2Motion](https://github.com/Mesh2Motion/mesh2motion-app) | MIT / CC0 | `third_party/mesh2motion-app` (`LICENSE-MIT.MD`, `LICENSE-CC0.MD` there) |

## Programs and libraries used at run time (not bundled)

| Project | Licence | Used for |
|---|---|---|
| [Blender](https://www.blender.org) (Blender Foundation) | GPL-2.0-or-later | Every rig, skin, audit, clip and export step runs headless in it |
| [Python](https://www.python.org) | PSF | The server, CLI and pure-Python core |
| [NumPy](https://numpy.org) | BSD-3-Clause | Maths in the steps (comes with Blender) |
| [Pillow](https://python-pillow.org) | MIT-CMU | QA contact sheets (optional) |
| [jsonschema](https://github.com/python-jsonschema/jsonschema) | MIT | Spec validation in tests and CI |
| [Node.js](https://nodejs.org), [Vite](https://vite.dev), [TypeScript](https://www.typescriptlang.org) | MIT / Apache-2.0 | Building Mesh2Motion |
| [PyMeshLab](https://github.com/cnr-isti-vclab/PyMeshLab) | GPL-3.0 | Decimation, repair, remesh (optional extra) |
| [PyMeshFix](https://github.com/pyvista/pymeshfix) | GPL-3.0 | Watertight repair (optional extra) |
| [trimesh](https://github.com/mikedh/trimesh) and [SciPy](https://scipy.org) | MIT, BSD-3-Clause | Body-plan measures (optional extra) |
| [xatlas](https://github.com/jpcy/xatlas) | MIT | UV unwrap and atlas packing (optional extra) |
| [CoACD](https://github.com/SarahWeiii/CoACD) | MIT | Convex pieces for game colliders (optional extra) |
| [Manifold](https://github.com/elalish/manifold) (manifold3d) | Apache-2.0 | Robust booleans (optional extra) |
| [robust-laplacian](https://github.com/nmwsharp/robust-laplacians-py) | MIT | Weight smoothing on non-manifold meshes (optional extra) |
| [OpenCLIP](https://github.com/mlfoundations/open_clip) with LAION-2B ViT-B/32 weights, on [PyTorch](https://pytorch.org) | MIT, BSD-3-Clause | The picture vote in the suggest step (optional extra) |
| [gltfpack / meshoptimizer](https://github.com/zeux/meshoptimizer) ([Arseny Kapoulkine](https://github.com/zeux)) | MIT | Optimising Web and Godot GLBs (optional) |
| [glTF-Validator](https://github.com/KhronosGroup/glTF-Validator) (Khronos Group) | Apache-2.0 | Checking every exported GLB (optional) |
| [model-viewer](https://github.com/google/model-viewer) (Google) | Apache-2.0 | The preview page in Web export packages, loaded from Google's CDN |

## Cross-checks and conventions

- The bone-name tables in `autorig/core/skeletons.py` are written from each convention's published joint list.
  - The mapping tables of [Rokoko Studio Live for Blender](https://github.com/Rokoko/rokoko-studio-live-blender) (LGPL-3.0) were the cross-check.
  - So were those of [Expy Kit](https://github.com/pKrime/Expy-Kit) by [pKrime](https://github.com/pKrime).
  - Neither project's code is copied.
- Skeleton and naming conventions the workbench reads or writes: [Mixamo](https://www.mixamo.com) (Adobe), [Unreal Engine](https://www.unrealengine.com) (Epic Games), [Unity](https://unity.com) humanoid (Unity Technologies), [Rigify](https://docs.blender.org/manual/en/4.1/addons/rigging/rigify/index.html) (Blender), [AccuRIG](https://actorcore.reallusion.com/auto-rig) (Reallusion), [SMPL](https://smpl.is.tue.mpg.de) / [SMPL-X](https://smpl-x.is.tue.mpg.de) (MPI), [DAZ](https://www.daz3d.com) (Daz 3D), Valve, [VRM](https://vrm.dev/en/) / [VRoid](https://vroid.com/en/studio), [Mesh2Motion](https://mesh2motion.org), and BVH. Names belong to their owners. They are used here only so files move between tools.

## Services the workbench can call

These run on other people's servers, under their own terms. Some cost money.

- **3D generation:** [Tripo](https://www.tripo3d.ai), [Hyper3D Rodin](https://hyper3d.ai) and [Meshy](https://www.meshy.ai). Model Studio and game-prop work with their output. `autorig/core/generators/` calls their official HTTP APIs, written from their docs: [Tripo API v3](https://developers.tripo3d.com/en/docs/introduction) ([H3.1 prices](https://developers.tripo3d.com/en/models/v3-1)), [Meshy API](https://docs.meshy.ai/en/api) ([pricing](https://docs.meshy.ai/en/api/pricing)) and [Rodin Gen-2.5](https://docs.hyper3d.ai/en/api-specification/rodin-gen2-5) ([quick start](https://docs.hyper3d.ai/en/get-started/quick-start)).
- **Rigging:** [Mixamo](https://www.mixamo.com) (Adobe). `mixamo-upload` and `mixamo-import` prepare files for it and read them back.
- **Textures:** [Poly Haven](https://polyhaven.com), CC0. Model Studio fetches materials from it.
- **AI Assist:** [Anthropic](https://www.anthropic.com) (Claude), [OpenAI](https://openai.com), [Google Gemini](https://ai.google.dev), or a local [Ollama](https://ollama.com).
- **Motion capture formats:** retargeting reads [Mixamo](https://www.mixamo.com), [Rokoko](https://www.rokoko.com), [CMU](http://mocap.cs.cmu.edu) and [SMPL](https://smpl.is.tue.mpg.de)-based clips. Credit the source of any mocap you use. For the [CMU Graphics Lab Motion Capture Database](http://mocap.cs.cmu.edu), CMU asks for this line: "The data used in this project was obtained from mocap.cs.cmu.edu. The database was created with funding from NSF EIA-0196217."

## From the author's other projects

Some modules started life in Sam Bena's own projects and were carried over:
- `autorig/core/ai_*`: the AI loop, providers and cost gate, from Cougar Player (private) and [Deal Hunter](https://github.com/sambena/deal-hunter).
- Model Studio's generation, image, foliage, vehicle and LOD-packaging helpers: from Storm Chaser's tools (private).

## Sample models

The models in `samples/` are generated by `scripts/generate_samples.py` and dedicated to the public domain (CC0 1.0).

## Licence

GPL-3.0-or-later (LICENSE). It has to be, for three reasons:
- The steps run inside Blender and import `bpy`, which is GPL.
- `spring_bones.py` is a port of Wiggle 2, which is GPL-3.0.
- The PyMeshLab and PyMeshFix extras are GPL-3.0.

Everything else above is MIT, Apache-2.0, BSD or CC0, which GPL-3.0 can include. Apache-2.0 code keeps its own licence and NOTICE (`autorig/core/genmm/`).
