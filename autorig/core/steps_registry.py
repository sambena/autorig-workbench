# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: every step the tool can run, in one place. Plain Python (no bpy, no server).
#
# Before this, the steps were listed three times, and the lists did not agree: the GUI server's commands(), the CLI's
# run.py, and the batch runner. Now each of them asks this registry. So does the HTTP API (gui/api_v1.py), which
# lists the steps with their options for the page's Advanced menu and for scripts.
#
# Two kinds of step:
#   - model steps run on one model of the collection by name (survey, rig, trim, audit, clips, publish, preview...).
#     model_commands() says which scripts a step runs for a model, as Cmd tuples; the caller turns them into
#     processes (the server streams them as a job, the batch runner runs them in turn).
#   - tools take files in and write files out (game-prop, fbx-maps, m2m-rig...). Their arguments are command-line
#     flags, as `python autorig/cli/run.py <tool> ...` takes them; `options` describes them for the API and the page.
#
# A Cmd is (label, runner, script, args, prepare): runner "blender" runs steps/<script> headless in Blender, "python"
# runs it with this Python; script is a file name in steps/ or an absolute path (a model's custom builder);
# prepare is None or a function called just before the command starts.
import os
from collections import namedtuple

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
STEPS_DIR = os.path.join(PKG, "steps")
REPO = os.path.dirname(PKG)
M2M_APP = os.path.join(REPO, "third_party", "mesh2motion-app")   # the Mesh2Motion submodule (rigs and clips)

Cmd = namedtuple("Cmd", "label runner script args prepare")


def opt(name, help, type="string", default=None, choices=None, path=None, required=False):
    """One option of a tool: --name. path is "in" or "out" when the value is a file or folder (the API keeps those
    inside the models root or the work folder)."""
    o = {"name": name, "type": type, "help": help}
    if default is not None: o["default"] = default
    if choices: o["choices"] = list(choices)
    if path: o["path"] = path
    if required: o["required"] = True
    return o


# ---------------------------------------------------------------------------------------------------------------
# Model steps, in pipeline order
# ---------------------------------------------------------------------------------------------------------------

MODEL_STEPS = [
    {"name": "prepare", "title": "Prepare source", "help": "a clean copy of the source (one mesh, metres, facing)"},
    {"name": "survey", "title": "Survey", "help": "measure the source and render the facing views"},
    {"name": "rig", "title": "Rig", "help": "build and skin the skeleton rig.json describes (classic rig)"},
    {"name": "trim", "title": "Trim to budget", "help": "decimate the rigged model to its triangle budget"},
    {"name": "audit", "title": "Audit", "help": "grade the rig: bleed, tears, bends (PASS / CHECK / FAIL)"},
    {"name": "clips", "title": "Make clips", "help": "procedural clips for the archetype (shapes Mesh2Motion has no skeleton for)"},
    {"name": "publish", "title": "Publish card", "help": "write the model card (model.json) and the pack list"},
    {"name": "preview", "title": "Preview", "help": "the 3D viewer's preview.glb"},
    {"name": "rebake-clips", "title": "Re-bake clips", "help": "make clips, then the preview again"},
    {"name": "doctor-repair", "title": "Mesh Doctor: repair", "help": "PyMeshLab repairs on a copy in work/doctor"},
    {"name": "doctor-watertight", "title": "Mesh Doctor: watertight", "help": "one watertight surface (PyMeshFix), maps baked"},
    {"name": "fix-skin", "title": "Fix skin", "help": "re-skin a Mesh2Motion rig several ways on its fitted skeleton "
                                                     "and keep the one the audit grades best"},
    {"name": "all", "title": "Run all (classic)", "help": "rig, trim, audit, publish, clips when it has an archetype, preview"},
]
MODEL_STEP_NAMES = tuple(s["name"] for s in MODEL_STEPS) + ("rebake_clips",)      # the old spelling is still taken


def rig_script(rig, model_dir):
    """The script that rigs a model from its rig.json "rig" section (an absolute path for a custom builder)."""
    kind = (rig or {}).get("kind")
    if kind == "humanoid": return "rerig_humanoid.py"
    if kind == "custom": return os.path.join(model_dir, rig["builder"])
    return "rerig.py"


def clip_archetype(spec, spec_store=None):
    """The clips step's archetype: rig.json's, else the one spec_store infers from the skeleton."""
    a = (spec.get("clips") or (spec.get("rig") or {}).get("clips") or {}).get("archetype")
    if not a and spec_store is not None:
        a = spec_store.infer_clip_archetype(spec)
    return a


def model_commands(step, name, group, spec, work, model_dir, spec_store=None, rmtree=None):
    """What one model step runs for this model, as a list of Cmd. Raises ValueError for a step it does not know."""
    rig = spec.get("rig") or {}

    def prepare_step():
        return [Cmd("prepare", "blender", "prepare.py", ["-only", name], None)]

    def survey():
        return [Cmd("survey", "blender", "survey.py", ["-only", name], None),
                Cmd("facing views", "blender", "facing.py", ["-only", name, "-out", os.path.join(work, "facing")], None)]

    def rig_step():
        return [Cmd("rig (%s)" % (rig.get("kind") or "?"), "blender", rig_script(rig, model_dir),
                    ["-only", name, "-qa", os.path.join(work, "qa")], None)]

    def trim():
        return [Cmd("trim to budget", "blender", "decimate.py", ["-only", name], None)]

    def audit():
        return [Cmd("audit", "blender", "audit.py", ["-model", name, "-out", os.path.join(work, "audit")], None)]

    def clips():
        pv = os.path.join(work, "clips", name)
        arch = clip_archetype(spec, spec_store)
        args = [name, "--preview", pv] + (["--archetype", arch] if arch else [])
        prep = (lambda: rmtree(pv, ignore_errors=True)) if rmtree else None
        return [Cmd("make clips (%s)" % (arch or "auto"), "blender", "make_clips.py", args, prep)]

    def publish():
        return [Cmd("publish card", "python", "publish.py", [group or ".", "-only", name], None)]

    def preview():
        return [Cmd("preview for the viewer", "blender", "preview_glb.py", ["-only", name], None)]

    if step == "prepare": return prepare_step()
    if step == "survey": return survey()
    if step == "rig": return rig_step()
    if step == "trim": return trim()
    if step == "audit": return audit()
    if step == "clips": return clips()
    if step in ("rebake-clips", "rebake_clips"): return clips() + preview()
    if step == "publish": return publish()
    if step == "preview": return preview()
    if step == "doctor-repair":
        return [Cmd("mesh doctor: repair (PyMeshLab)", "blender", "mesh_doctor.py", [name, "--repair"], None)]
    if step == "doctor-watertight":
        return [Cmd("mesh doctor: watertight (PyMeshFix), baked", "blender", "mesh_doctor.py",
                    [name, "--watertight", "--bake", "1024"], None)]
    if step == "fix-skin":
        return [Cmd("fix skin (try four skins, keep the best)", "python", "m2m_fix.py", [name], None)]
    if step == "all":
        # the card goes before the clips (they read its size) and again after (it lists them)
        out = rig_step() + trim() + audit() + publish()
        if clip_archetype(spec, spec_store):
            out += clips() + publish()
        return out + preview()                     # the viewer's copy, last: it carries the clips
    raise ValueError("unknown step %r" % step)


# ---------------------------------------------------------------------------------------------------------------
# Tools: files in, files out
# ---------------------------------------------------------------------------------------------------------------

M2M_TEMPLATES = ("human", "fox", "horse", "bird", "dragon", "kaiju", "spider", "snake", "shark")

TOOLS = {
    "m2m-rig": {
        "title": "Rig on a Mesh2Motion skeleton", "runner": "blender", "script": "m2m_rig.py",
        "help": "fit one of Mesh2Motion's skeletons to the mesh, skin and audit it, and attach its clips",
        "options": [opt("model", "an asset-packs model (its source, and its rig folder for the results)"),
                    opt("in", "the source mesh, when not --model", path="in"),
                    opt("name", "output name"),
                    opt("out", "output folder", path="out"),
                    opt("template", "the skeleton", default="auto", choices=("auto",) + M2M_TEMPLATES),
                    opt("height", "height in metres (0: as it is)", type="number", default=0),
                    opt("clips", "clips by role or name, optionally =engine name; or all"),
                    opt("joints", "edited joints (rig.json m2m.joints, or {bone: [x, y, z]})", path="in"),
                    opt("naming", "bone names", default="default", choices=("default", "mixamo")),
                    opt("target", "engine axes", default="unity", choices=("unity", "generic")),
                    opt("format", "outputs", default="glb,fbx"),
                    opt("skin", "skinning method: autorig or m2m (default: the template's)"),
                    opt("triangles", "triangle budget", type="integer", default=15000),
                    opt("swing", "scale the legs' swing in the clips (1 as made; a cow ~0.75)", type="number", default=1),
                    opt("tail-swing", "scale the tail's swing", type="number", default=0.4),
                    opt("archetype", "what it is, when the template is auto (quadruped, humanoid...)"),
                    opt("choose-only", "print the template auto would pick, and why, and stop", type="boolean")]},
    "m2m-quadruped": {
        "title": "Four-legged animal on Mesh2Motion", "runner": "blender", "script": "m2m_quadruped.py",
        "help": "the first Mesh2Motion fit: horse, fox or bird skeleton on a four-legged mesh (m2m-rig supersedes it)",
        "options": [opt("in", "the source mesh", path="in", required=True), opt("name", "output name", required=True),
                    opt("rig", "Mesh2Motion's <kind>-animations.glb", path="in", required=True),
                    opt("out", "output folder", path="out", required=True),
                    opt("height", "height in metres", type="number"),
                    opt("clips", "Engine=clip pairs, e.g. Idle=idle,Walk=walk")]},
    "m2m-clips": {
        "title": "Mesh2Motion clips on a Mixamo person", "runner": "python", "script": "m2m_clips.py",
        "help": "Mesh2Motion's human clips baked onto a Mixamo-rigged person as takes, beside the shipped file",
        "options": [opt("clips", 'clip names, e.g. "Idle,Walk,Run" or "Idle_A,Walk,Jog,Roll_RM"', required=True),
                    opt("rig", "Mixamo's download", path="in", required=True), opt("name", "game name", required=True),
                    opt("source", "the GLB source, for its maps", path="in"),
                    opt("out", "output folder", path="out", required=True),
                    opt("root-motion", "*_RM clips keep their travel", type="boolean"),
                    opt("replace", "write <Name>.fbx over the shipped file", type="boolean"),
                    opt("fps", "frame rate", type="number", default=30)]},
    "game-prop": {
        "title": "Game prop", "runner": "blender", "script": "game_prop.py",
        "help": "a generated prop as a game prop: metres, facing, LODs, maps, wheels",
        "options": [opt("in", "the generated mesh", path="in", required=True), opt("name", "prop name", required=True),
                    opt("length", "longest side in metres", type="number"), opt("out", "output folder", path="out", required=True),
                    opt("front", "which way its front points", choices=("+x", "-x", "+y", "-y")),
                    opt("carve-wheels", "cut the wheels out to spin", type="boolean")]},
    "game-parts": {
        "title": "Game parts", "runner": "blender", "script": "game_parts.py",
        "help": "a game prop's moving parts cut out of LOD0, each its own mesh at its pivot",
        "options": [opt("in", "the game FBX", path="in", required=True), opt("spec", "parts.json", path="in", required=True),
                    opt("out", "output folder", path="out", required=True), opt("renders", "render folder", path="out"),
                    opt("survey", "survey only", type="boolean")]},
    "tripo-clips": {
        "title": "Clips on a Tripo skeleton", "runner": "blender", "script": "tripo_clips.py",
        "help": "idle, walk, trot and gallop on a Tripo-rigged animal's own skeleton",
        "options": [opt("in", "Tripo's rigged GLB", path="in", required=True), opt("name", "output name", required=True),
                    opt("height", "height in metres", type="number"), opt("out", "output folder", path="out", required=True)]},
    "fbx-maps": {
        "title": "FBX maps", "runner": "blender", "script": "fbx_maps.py",
        "help": "a rigged FBX given its GLB source's colour and normal maps",
        "options": [opt("fbx", "the rigged FBX", path="in", required=True), opt("source", "the GLB source", path="in", required=True),
                    opt("name", "output name", required=True), opt("out", "output folder", path="out", required=True)]},
    "mixamo-upload": {
        "title": "Pack for Mixamo", "runner": "blender", "script": "mixamo_upload.py",
        "help": "a generated character cleaned and packed for Mixamo's auto-rigger",
        "options": [opt("in", "the character", path="in", required=True), opt("out", "the FBX to write", path="out", required=True),
                    opt("height", "height in metres", type="number"), opt("triangles", "triangle budget", type="integer")]},
    "mixamo-import": {
        "title": "Import from Mixamo", "runner": "python", "script": "mixamo_import.py",
        "help": "the Mixamo rig with full maps and Unity's axes, as a game file",
        "options": [opt("rig", "Mixamo's download", path="in", required=True), opt("source", "the GLB source", path="in", required=True),
                    opt("name", "output name", required=True), opt("out", "output folder", path="out", required=True)]},
    "crop-cards": {
        "title": "Crop cards", "runner": "blender", "script": "crop_cards.py",
        "help": "flat picture cards of plants, for a foliage atlas",
        "options": [opt("out", "output folder", path="out", required=True),
                    opt("card", "<name>=<plant.glb>[:<view>[:<wide>]] (repeat the flag for more)", path="in")]},
    "smoke-flipbook": {
        "title": "Smoke flipbook", "runner": "blender", "script": "smoke_flipbook.py",
        "help": "a puff of smoke simulated and rendered as a flipbook sheet",
        "options": [opt("out", "the PNG to write", path="out", required=True), opt("grid", "frames per side", type="integer"),
                    opt("size", "pixels per frame", type="integer"), opt("res", "simulation resolution", type="integer")]},
}

# Tools whose arguments are positional (no flags): only from the command line, not the API
CLI_ONLY = {
    "clip-check": {"runner": "blender", "script": "clip_check.py", "help": "does it move, do seams hold, where are the hands"},
    "smpl-to-bvh": {"runner": "blender", "script": "smpl_to_bvh.py", "help": "EDGE / SMPL dance -> BVH for retarget"},
}


def step_info():
    """The registry as data: what GET /api/v1/steps returns, and what the page's Advanced menu lists."""
    tools = []
    for name, t in TOOLS.items():
        tools.append({"name": name, "title": t["title"], "help": t["help"], "runner": t["runner"],
                      "available": os.path.isfile(os.path.join(STEPS_DIR, t["script"])), "options": t["options"]})
    return {"model_steps": MODEL_STEPS, "tools": tools}


def tool_script(name):
    """(runner, script) of a tool, or raises ValueError. A Blender tool's script is its file name in steps/ (as
    blender.command takes it: a path there would mean a model's own builder); a Python tool's is its full path."""
    t = TOOLS.get(name) or CLI_ONLY.get(name)
    if not t: raise ValueError("unknown tool %r" % name)
    p = os.path.join(STEPS_DIR, t["script"])
    if not os.path.isfile(p): raise ValueError("%s is not installed in this version (%s missing)" % (name, t["script"]))
    return t["runner"], (t["script"] if t["runner"] == "blender" else p)


def tool_args(name, values):
    """Command-line flags for a tool from an options object ({"in": "...", "carve-wheels": true}). Unknown options
    and wrong types raise ValueError. Path options are returned as given: the caller confines them."""
    t = TOOLS.get(name)
    if not t: raise ValueError("unknown tool %r" % name)
    known = {o["name"]: o for o in t["options"]}
    for o in t["options"]:
        if o.get("required") and values.get(o["name"]) in (None, ""):
            raise ValueError("%s needs --%s" % (name, o["name"]))
    args = []
    for k, v in values.items():
        o = known.get(k)
        if o is None: raise ValueError("%s has no option %r" % (name, k))
        if v is None or v == "": continue
        if o["type"] == "boolean":
            if not isinstance(v, bool): raise ValueError("--%s is true or false" % k)
            if v: args.append("--" + k)
            continue
        if o["type"] in ("number", "integer"):
            if isinstance(v, bool) or not isinstance(v, (int, float)): raise ValueError("--%s is a number" % k)
            if o["type"] == "integer" and int(v) != v: raise ValueError("--%s is a whole number" % k)
        vals = v if isinstance(v, list) and k == "card" else [v]
        for one in vals:
            if not isinstance(one, (str, int, float)): raise ValueError("--%s takes a single value" % k)
            if o.get("choices") and str(one) not in o["choices"]:
                raise ValueError("--%s is one of %s" % (k, ", ".join(o["choices"])))
            args += ["--" + k, str(one)]
    return args


def m2m_app():
    """The Mesh2Motion app checkout whose static/rigs and static/animations the m2m steps read: AUTORIG_M2M, else
    the submodule. None when neither is there."""
    for p in (os.environ.get("AUTORIG_M2M"), M2M_APP):
        if p and os.path.isdir(os.path.join(p, "static", "animations")):
            return p
    return None
