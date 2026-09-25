# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the spec editor's side of the local server (gui/server.py routes to it).
#
#   GET  /spec_editor.html?model=<name>&t=<token>   the editor page (token required)
#   GET  /spec_editor.js                            its code (static, Host check only, like viewer.js)
#   GET  /api/spec?name=<model>                     everything the editor needs: rig.json (text and parsed), the
#                                                   schema, the source view (steps/source_preview.py), the rig
#                                                   step's record of which new bone came from which source joint,
#                                                   the audit's failing spots, the flat views, the before/after pair
#   POST /api/spec/check    {model, spec}           schema errors and warnings, and the diff against the file
#   POST /api/spec/save     {model, spec, base}     writes rig.json (the old one kept as rig.json.bak); `base` is the
#                                                   hash of the text the page loaded, so a file changed on disk
#                                                   meanwhile is never overwritten blind
#   POST /api/spec/rerig    {model, spec, base}     save, keep the current audit as "before", then rig, trim, audit,
#                                                   clips (when the spec names an archetype) and preview, one job
#   POST /api/spec/source   {model}                 run source_preview.py (the source model and its skeleton)
#   POST /api/spec/measure  {model, spec}           run measure.py on the draft (not saved) spec: the flat views
#
# The editor edits a copy of the parsed file: fields it does not know are kept as they are, key order is kept, and
# the file is written in the same compact style people write it in (short lists and objects on one line).
import difflib, hashlib, json, math, os, re, shutil, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_TYPES = {".js": "text/javascript; charset=utf-8"}
CSP = ("default-src 'self'; img-src 'self' data: blob:; connect-src 'self' data: blob:; "
       "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
SCHEMA_ID = "autorig-spec/1"
KINDS = ("tripo", "build", "placed", "humanoid", "custom")
ARCHETYPES = ("quadruped", "hexapod", "octopod", "serpent", "winged", "floater", "rigid", "humanoid")
CLIP_ARCHETYPES = ("walker", "quadruped", "flyer", "exploder", "swimmer", "turret", "winged")
WALK_PRESETS = ("natural", "soldier", "swagger", "stealth", "heavy", "quadruped_walk", "quadruped_trot")
THRESHOLD_KEYS = ("bleed_pct", "combined_tears", "bend_tears", "head_pct", "max_influences")
ROLE = re.compile(r"^[a-z][a-z0-9_]*$")

# ---------------------------------------------------------------------------------------------------------------
# The schema: every rig.json field the editor offers, with a label and plain-English help. The page builds its form
# from this; check() validates against it. `kinds` limits a field to those rig kinds. Types:
#   enum bool number int text texts joint joints joint_chains role_joints vec3 point point_pair box_list bone
#   build_chains allowances jaw add_tail hard_split parts_rules join_blends rip_welds membranes rigid_islands
# ---------------------------------------------------------------------------------------------------------------

F = lambda key, label, type_, help_, **kw: dict(key=key, label=label, type=type_, help=help_, **kw)
RIG_FIELDS = [
    F("kind", "Kind of rig", "enum", "How the skeleton is made. Tripo-style: the model came with a skeleton "
      "(bone_0, bone_1...) and its joints are reused. Build: the model has no skeleton, so chains are traced through "
      "the mesh from points you place. Placed: hand-placed chains with body-part rules (winged/creatures: membranes, "
      "rip welds, blends). Humanoid: a person. Custom: a script of your own.", options=list(KINDS),
      group="Basics", required=True),
    F("forward", "Facing", "vec3", "The way the model faces in its source file. Read it off the survey's facing "
      "views. Left empty on a Tripo-style rig, it is worked out from the head and hips bones.", group="Basics"),
    F("straighten", "Straighten exactly", "bool", "Sculpted at an angle: turn it exactly, instead of to the nearest "
      "quarter turn.", group="Basics"),
    F("origin", "Centre on the origin", "enum", "\"center\" for swimmers and fliers: the model is centred on the "
      "origin. Left empty, it stands with its feet on the floor.", options=["", "center"], group="Basics"),
    F("skeleton", "Archetype on the card", "enum", "Which standard skeleton this is (docs/SKELETONS.md). Only "
      "written on the card.", options=[""] + list(ARCHETYPES), group="Basics"),
    F("builder", "Builder script", "text", "The Python script in the model folder that rigs this model (e.g. rig_boss.py).",
      kinds=["custom"], group="Basics", required=True),

    F("head", "Head bone", "joint", "The bone at the front end of the body: click the head's bone. The spine runs "
      "from the hips bone to this one.", kinds=["tripo"], group="Body", required=True),
    F("hips", "Hips bone", "joint", "The bone at the back end of the body, where the spine starts: click it. "
      "The new skeleton is rooted here.", kinds=["tripo"], group="Body", required=True),
    F("body", "Body", "enum", "\"single\": a shell on legs. One rigid body bone with the listed limbs hanging off it "
      "(needs Facing). Empty: an ordinary spine from hips to head.", options=["", "single"], kinds=["tripo"],
      group="Body"),
    F("body_height", "Body bone height", "number", "For a single body: how high the body bone sits, 0 (floor) to "
      "1 (top).", min=0, max=1, kinds=["tripo"], group="Body"),
    F("shell", "Shell bone", "joint", "A rigid body on legs: everything the legs do not own goes to this bone.",
      kinds=["tripo"], group="Body"),

    F("chains", "Chains", "role_joints", "Name the limbs. Each role (wing_fore, leg, tail, abdomen...) lists the "
      "FIRST bone of every limb that plays it: pick both wings' first bones for a pair. Everything below a picked "
      "bone follows it down to its tip. Limbs you do not name are guessed.", kinds=["tripo"], group="Chains"),
    F("legs", "Legs", "joints", "The first bone of each leg. These get IK foot controls.", kinds=["tripo"],
      group="Chains"),
    F("mirror", "Mirror", "joint_chains", "A limb the model has on one side only: pick its bones from the body out, "
      "and a copy is made on the other side (named <bone>_m). Name the copy in Chains like any other bone.",
      kinds=["tripo"], group="Chains"),
    F("delete", "Throw away", "joints", "Bones to throw away, with everything under them.", kinds=["tripo"],
      group="Chains"),
    F("nodeform", "Carry no skin", "joints", "Bones that stay in the skeleton but move no part of the mesh.",
      kinds=["tripo"], group="Chains"),
    F("girdle", "Girdles", "texts", "Roles whose first bone is really a shoulder or pelvis bone: e.g. leg_front.",
      kinds=["tripo"], group="Chains"),
    F("add_tail", "Add a tail", "add_tail", "A tail the source skeleton gave no bones: from (0..1 along the body) "
      "to the tip, in this many bones.", kinds=["tripo"], group="Chains"),
    F("head_line", "Head line", "point_pair", "The head bone runs from the first point (the spine is cut there) to "
      "the second (rule A). Click the two points on the model.", kinds=["tripo", "placed"], group="Head"),
    F("jaw", "Jaw", "jaw", "A jaw bone: the hinge and the chin tip. Everything under the mouth line ahead of the "
      "hinge moves with it.", kinds=["tripo", "build", "placed"], group="Head"),
    F("head_to_snout", "Stretch the head to the snout", "bool", "On (the default): a forward-facing head bone is "
      "extended to the front of the face. Off: it stays where it is.", default=True, kinds=["tripo", "placed"], group="Head"),

    F("chains_build", "Chains", "build_chains", "The first chain is the body. Each chain is placed by clicking on "
      "the model: a limb by its tip (and where it leaves the body), a spine by a slice along its length, or "
      "joints given outright.", kinds=["build", "placed"], group="Chains", store="chains"),

    F("parts", "Body parts", "parts_rules", "Which bones each part may use. Enforces rules like arm bones only on "
      "forelimbs, preventing wing bone cross-bleed.", kinds=["placed"], group="Parts"),
    F("blends", "Blended joins", "join_blends", "Which parts blend at a join: child bone, parent bone, transition "
      "radius and fade.", kinds=["placed"], group="Parts"),
    F("rip_welds", "Rip welds", "rip_welds", "Welds to split along seams between parts that move apart (e.g. forearm "
      "to thigh, wing tip to tail) so bone heat does not pull across.", kinds=["placed"], group="Parts"),
    F("membranes", "Membranes", "membranes", "Wing and web membranes riding only wing bones by distance gradients "
      "and cut free from the flank.", kinds=["placed"], group="Parts"),
    F("rigid_islands", "Rigid islands", "rigid_islands", "Loose pieces or armour plates (pauldrons) that ride a "
      "bone whole and never bend.", kinds=["placed", "build", "tripo"], group="Skin"),

    F("envelope", "Skin style", "enum", "full: the torso belongs to the spine and each limb to a capsule round it "
      "(creatures). root: bone heat kept, but no limb weight behind where the limb starts (people). false: plain "
      "bone heat.", options=["", "full", "root", False], group="Skin"),
    F("rigid_pieces", "Rigid piece size", "number", "Loose pieces up to this share of the largest ride one bone "
      "whole (default 0.12).", min=0, max=1, group="Skin"),
    F("rigid_single", "Every loose piece on one bone", "bool", "Each loose piece rides a single bone, never two.",
      group="Skin"),
    F("soft", "Soft chains", "texts", "Chains (by role or name) whose loose pieces bend instead of riding whole.",
      group="Skin"),
    F("rigid_to", "Rigid parts", "box_list", "A loose piece whose middle is inside the box rides that bone whole. "
      "Pick the bone, then click two opposite corners on the model (or the flat views).", group="Skin"),
    F("rigid_parts", "A machine of parts", "bool", "The main piece rides the body, every other piece its nearest "
      "bone, whole.", group="Skin"),
    F("hard_split", "Hard split", "hard_split", "Everything above a height on one bone, the rest on another (a lid).",
      group="Skin"),
    F("smooth", "Smoothing passes", "int", "Weight smoothing passes, for a thick body's patchy weights.", min=0,
      max=20, group="Skin"),
    F("joint_blend", "Joint blend", "number", "How far either side of a joint the torso blends (share of the shorter "
      "bone; default 0.4).", min=0, max=2, group="Tuning"),
    F("girdle_blend", "Girdle blend", "number", "How far round a girdle it reaches (default 0.9).", min=0, max=3,
      group="Tuning"),
    F("girdle_reach", "Girdle reach", "number", "(default 1.6)", min=0, max=5, group="Tuning"),
    F("limb_radius", "Limb radius", "number", "Scales each limb's capsule (default 1).", min=0.1, max=5,
      group="Tuning"),
    F("spike_reach", "Spike reach", "number", "How far out spikes and claws still belong to their limb (default 3).",
      min=0, max=10, group="Tuning"),
    F("envelope_skip", "Envelope skips", "texts", "Bones the envelope leaves alone.", group="Tuning"),
    F("rig_folder", "Rig folder", "text", "Where the rig is written (default rigged).", group="Tuning"),
    F("audit", "Audit allowances", "allowances", "Loosen (or tighten) the audit for this model only. Every allowance "
      "needs a reason, written in the note below it.", group="Audit"),
]
BUILD_CHAIN_FIELDS = {"name", "role", "bones", "slice", "stations", "tube", "tip", "base", "base_f", "points",
                      "names", "parent", "parent_nearest", "ik", "width", "first", "snap_end", "centre", "girdle", "pre_bend"}
OTHER_FIELDS = [
    F("budget", "Triangle budget", "int_or_null", "The engine's triangle budget. Empty: the collection's default; "
      "\"full\" keeps full resolution.", section="budget"),
    F("clips.archetype", "Clips", "enum", "Which clips to author.", options=[""] + list(CLIP_ARCHETYPES),
      section="clips"),
    F("clips.gait", "Gait", "enum", "Walking gait: lateral sequence walk or trot.", options=["", "walk", "trot"],
      section="clips"),
    F("clips.walk.preset", "Walk style preset", "enum", "Biomechanical gait style preset.",
      options=[""] + list(WALK_PRESETS), section="clips"),
    F("clips.walk.stride", "Walk stride scale", "number", "Step length multiplier (0.2 .. 2.5, default 1.0).",
      min=0.2, max=2.5, step=0.05, section="clips"),
    F("clips.walk.cadence", "Walk cadence scale", "number", "Step frequency multiplier (0.2 .. 3.0, default 1.0).",
      min=0.2, max=3.0, step=0.05, section="clips"),
    F("clips.walk.sway", "Pelvis sway scale", "number", "Lateral pelvis weight shifting (0.0 .. 3.5, default 1.0).",
      min=0.0, max=3.5, step=0.05, section="clips"),
    F("clips.walk.bob", "Pelvis bounce scale", "number", "Vertical bounce amplitude (0.0 .. 3.0, default 1.0).",
      min=0.0, max=3.0, step=0.05, section="clips"),
    F("clips.walk.lean", "Forward lean (deg)", "number", "Trunk forward tilt angle (-10° .. 35°, default 3.5°).",
      min=-10.0, max=35.0, step=0.5, section="clips"),
    F("clips.walk.arm_swing", "Arm swing scale", "number", "Reciprocal arm swing amplitude (0.0 .. 3.0, default 1.0).",
      min=0.0, max=3.0, step=0.05, section="clips"),
    F("clips.display", "Display name", "text", "", section="clips"),
    F("clips.category", "Category", "text", "", section="clips"),
    F("card.metres", "Real size (m)", "number", "The real length of the longest axis, in metres.", min=0,
      section="card"),
    F("card.role", "Role", "text", "What it is on the card: creature, prop...", section="card"),
]
KNOWN_RIG = {f.get("store", f["key"]) for f in RIG_FIELDS} | {"builder", "centre"}
KNOWN_TOP = {"schema", "rig", "humanoid", "budget", "clips", "card", "notes"}
HUMANOID_Z_KEYS = ("ankle", "knee", "hip", "spine", "spine1", "spine2", "arm", "neck", "head", "top")
HUMANOID_X_KEYS = ("tip", "knuckle", "wrist", "elbow", "shoulder")

HUMANOID_FIELDS = [
    F("forward", "Facing", "vec3", "The way the model faces in its source file (e.g. [0, -1, 0] or [0, 1, 0]).",
      group="Humanoid"),
    F("z.top", "Top of head", "number", "Height of the top of the head (0 floor .. 1 top).", min=0, max=1,
      group="Humanoid Heights", step=0.005),
    F("z.head", "Head height", "number", "Height of the head joint.", min=0, max=1,
      group="Humanoid Heights", step=0.005),
    F("z.neck", "Neck height", "number", "Height of the neck joint.", min=0, max=1,
      group="Humanoid Heights", step=0.005),
    F("z.arm", "Arm height", "number", "Height of the arm / shoulder line.", min=0, max=1,
      group="Humanoid Heights", step=0.005),
    F("z.spine2", "Spine 2 height", "number", "Height of the upper spine (chest).", min=0, max=1,
      group="Humanoid Heights", step=0.005),
    F("z.spine1", "Spine 1 height", "number", "Height of the mid spine.", min=0, max=1,
      group="Humanoid Heights", step=0.005),
    F("z.spine", "Spine height", "number", "Height of the lower spine (waist).", min=0, max=1,
      group="Humanoid Heights", step=0.005),
    F("z.hip", "Hip height", "number", "Height of the hip joint (pelvis).", min=0, max=1,
      group="Humanoid Heights", step=0.005),
    F("z.knee", "Knee height", "number", "Height of the knee joint.", min=0, max=1,
      group="Humanoid Heights", step=0.005),
    F("z.ankle", "Ankle height", "number", "Height of the ankle joint.", min=0, max=1,
      group="Humanoid Heights", step=0.005),
    F("x.shoulder", "Shoulder span", "number", "Span from center to shoulder (0 tip .. 0.5 center).", min=0, max=0.5,
      group="Humanoid Spans", step=0.005),
    F("x.elbow", "Elbow span", "number", "Span from center to elbow.", min=0, max=0.5,
      group="Humanoid Spans", step=0.005),
    F("x.wrist", "Wrist span", "number", "Span from center to wrist.", min=0, max=0.5,
      group="Humanoid Spans", step=0.005),
    F("x.knuckle", "Knuckle span", "number", "Span from center to knuckles.", min=0, max=0.5,
      group="Humanoid Spans", step=0.005),
    F("x.tip", "Fingertip span", "number", "Span to fingertips (0 outermost .. 0.5 center).", min=0, max=0.5,
      group="Humanoid Spans", step=0.005),
]


def schema():
    return {"schema": SCHEMA_ID, "rig": RIG_FIELDS, "other": OTHER_FIELDS, "humanoid": HUMANOID_FIELDS,
            "thresholds": list(THRESHOLD_KEYS), "build_chain_fields": sorted(BUILD_CHAIN_FIELDS)}


# ---------------------------------------------------------------------------------------------------------------
# Writing rig.json the way people write it
# ---------------------------------------------------------------------------------------------------------------

def dumps(obj, width=160):
    """JSON with two-space indents, where anything that fits on its line stays on one line (lists of bones, a
    chain, a point), and the top level and each section are always one key a line."""

    def one(o):
        return json.dumps(o, ensure_ascii=False, separators=(", ", ": "))

    def fmt(o, ind, col, force=False):
        # ind: the indent of the line the value starts on; col: the column it starts at
        s = one(o)
        rows = isinstance(o, list) and any(isinstance(x, dict) for x in o)       # a list of chains: one a line
        if not force and not rows and (col + len(s) <= width or not isinstance(o, (dict, list)) or not o):
            return s
        pad, inner = " " * ind, " " * (ind + 2)
        if isinstance(o, dict):
            items = ["%s%s: %s" % (inner, one(k), fmt(v, ind + 2, ind + 2 + len(one(k)) + 2)) for k, v in o.items()]
            return "{\n" + ",\n".join(items) + "\n" + pad + "}"
        items = [inner + fmt(v, ind + 2, ind + 2) for v in o]
        return "[\n" + ",\n".join(items) + "\n" + pad + "]"

    if not isinstance(obj, dict):
        return fmt(obj, 0, 0, True) + "\n"
    lines = []
    for k, v in obj.items():
        force = isinstance(v, dict) and bool(v)
        lines.append("  %s: %s" % (one(k), fmt(v, 2, 2 + len(one(k)) + 2, force)))
    return "{\n" + ",\n".join(lines) + "\n}\n"


def text_hash(text):
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:16]


def diff(old, new, name="rig.json"):
    return "".join(difflib.unified_diff((old or "").splitlines(True), new.splitlines(True),
                                        "%s (on disk)" % name, "%s (edited)" % name, n=2))


# ---------------------------------------------------------------------------------------------------------------
# Checking a spec
# ---------------------------------------------------------------------------------------------------------------

def _num(v): return isinstance(v, (int, float)) and not isinstance(v, bool)
def _vec(v, n=3): return isinstance(v, list) and len(v) == n and all(_num(x) for x in v)


def joint_names(source):
    """The joints a tripo spec may name: the source's, plus the mirrored copies its mirror field makes."""
    return {j["name"] for j in (source or {}).get("joints", [])}


def check(spec, source=None):
    """(errors, warnings): lists of {"path", "message"}. Errors stop a save; warnings do not."""
    errs, warns = [], []
    E = lambda p, m: errs.append({"path": p, "message": m})
    W = lambda p, m: warns.append({"path": p, "message": m})
    if not isinstance(spec, dict): E("", "the spec must be a JSON object"); return errs, warns
    if spec.get("schema") != SCHEMA_ID: E("schema", "schema must be %r" % SCHEMA_ID)
    for k in spec:
        if k not in KNOWN_TOP: W(k, "not a section the editor knows; it is kept as it is")
    rig = spec.get("rig")
    if rig is not None and not isinstance(rig, dict): E("rig", "rig must be an object"); rig = None
    rig = rig or {}
    notes = spec.get("notes") or {}
    if not isinstance(notes, dict): E("notes", "notes must be an object of texts"); notes = {}

    kind = rig.get("kind")
    if rig and kind not in KINDS: E("rig.kind", "pick a kind: " + ", ".join(KINDS))
    for k in rig:
        if k not in KNOWN_RIG: W("rig." + k, "not a field the editor knows; it is kept as it is")
    fields = {f.get("store", f["key"]): f for f in RIG_FIELDS if not f.get("kinds") or kind in f["kinds"]}
    for key, f in fields.items():
        if key not in rig: continue
        v, p, t = rig[key], "rig." + key, f["type"]
        if t == "enum" and v not in f["options"]: E(p, "one of " + ", ".join(repr(o) for o in f["options"] if o != ""))
        elif t == "bool" and not isinstance(v, bool): E(p, "on or off (true or false)")
        elif t in ("number", "int"):
            if not _num(v) or (t == "int" and int(v) != v): E(p, "a %s" % ("whole number" if t == "int" else "number"))
            elif ("min" in f and v < f["min"]) or ("max" in f and v > f["max"]):
                E(p, "between %s and %s" % (f.get("min", "-"), f.get("max", "-")))
        elif t == "vec3" and not (key == "forward" and v == "auto") and not (_vec(v) and any(v)): E(p, "a direction [x, y, z] or 'auto', e.g. [0, -1, 0]")
        elif t in ("text",) and not isinstance(v, str): E(p, "a text")
        elif t in ("texts", "joints") and not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
            E(p, "a list of names")
        elif t == "joint" and not isinstance(v, str): E(p, "a bone name")
        elif t == "point_pair" and not (isinstance(v, list) and len(v) == 2 and all(_vec(x) for x in v)):
            E(p, "two points [[x, y, z], [x, y, z]]")
        elif t == "role_joints":
            if not isinstance(v, dict): E(p, "roles with their first bones: {\"wing\": [\"bone_5\", \"bone_7\"]}")
            else:
                for role, tops in v.items():
                    if not ROLE.match(role): E(p + "." + role, "a role is lower-case letters, digits and _")
                    if not (isinstance(tops, list) and tops and all(isinstance(x, str) for x in tops)):
                        E(p + "." + role, "pick at least one bone")
        elif t == "joint_chains" and not (isinstance(v, list) and all(isinstance(c, list) and c and all(isinstance(x, str) for x in c) for c in v)):
            E(p, "a list of chains, each a list of bones: [[\"bone_18\", \"bone_19\"]]")
        elif t == "box_list":
            if not isinstance(v, list): E(p, "a list of [bone, [x0, y0, z0], [x1, y1, z1]]")
            else:
                for i, b in enumerate(v):
                    if not (isinstance(b, list) and len(b) == 3 and isinstance(b[0], str) and b[0] and _vec(b[1]) and _vec(b[2])):
                        E("%s.%d" % (p, i), "a bone and two corners: [bone, [x0, y0, z0], [x1, y1, z1]]"); continue
                    if not all(b[1][k] < b[2][k] for k in range(3)):
                        E("%s.%d" % (p, i), "the first corner must be below the second on every axis")
                    if any(not -0.001 <= c <= 1.001 for c in b[1] + b[2]):
                        W("%s.%d" % (p, i), "a corner outside the model's 0..1 box")
        elif t == "jaw" and not (isinstance(v, dict) and _vec(v.get("hinge")) and _vec(v.get("tip"))):
            E(p, "a hinge point and a tip point")
        elif t == "add_tail" and not (isinstance(v, dict) and _num(v.get("from"))):
            E(p, "at least \"from\" (0..1 along the body)")
        elif t == "hard_split" and not (isinstance(v, dict) and isinstance(v.get("bone"), str) and isinstance(v.get("else"), str) and _num(v.get("above"))):
            E(p, "a bone, the other bone (\"else\") and a height (\"above\", 0..1)")
        elif t == "allowances":
            if not isinstance(v, dict): E(p, "allowances by check name")
            else:
                inner = v.get("thresholds", v)
                for k2, x in inner.items():
                    if k2 not in THRESHOLD_KEYS: E(p + "." + k2, "not an audit check: " + ", ".join(THRESHOLD_KEYS))
                    elif not _num(x): E(p + "." + k2, "a number")
                if inner and not str(notes.get("rig.audit", "")).strip():
                    E("notes.rig.audit", "an audit allowance needs its reason: write it in the note")
        elif t == "parts_rules":
            if not isinstance(v, (dict, list)):
                E(p, "body parts must be a list of parts or a mapping of part name to rules/bones")
            elif isinstance(v, dict):
                for pname, pval in v.items():
                    if not isinstance(pval, (list, dict)):
                        E(p + "." + pname, "bones list or object with bones/allow/deny")
                    elif isinstance(pval, list) and not all(isinstance(x, str) for x in pval):
                        E(p + "." + pname, "list of bone names")
            elif isinstance(v, list):
                for i, r in enumerate(v):
                    if not isinstance(r, dict):
                        E("%s.%d" % (p, i), "part rule must be an object")
                    elif "bones" in r and not (isinstance(r["bones"], list) and all(isinstance(x, str) for x in r["bones"])):
                        E("%s.%d.bones" % (p, i), "list of bone names")
        elif t == "join_blends":
            if not isinstance(v, list):
                E(p, "a list of join blends: [{\"bone\": \"wing_1.L\", \"with\": \"spine_2\", \"radius\": 0.15}]")
            else:
                for i, b in enumerate(v):
                    if isinstance(b, dict):
                        if not (isinstance(b.get("bone"), str) and isinstance(b.get("with"), str)):
                            E("%s.%d" % (p, i), "blend must have 'bone' and 'with' bone names")
                        if "radius" in b and not _num(b["radius"]):
                            E("%s.%d.radius" % (p, i), "radius must be a number")
                        if "fade" in b and not _num(b["fade"]):
                            E("%s.%d.fade" % (p, i), "fade must be a number")
                    elif isinstance(b, (list, tuple)):
                        if len(b) < 2 or not all(isinstance(x, str) for x in b[:2]):
                            E("%s.%d" % (p, i), "blend must specify [bone, with]")
                    else:
                        E("%s.%d" % (p, i), "join blend must be an object or pair")
        elif t == "rip_welds":
            if not isinstance(v, list):
                E(p, "a list of weld seams to rip: [[\"arm_3.L\", \"leg_1.L\"]]")
            else:
                for i, r in enumerate(v):
                    if isinstance(r, (list, tuple)):
                        if len(r) != 2 or not all(isinstance(x, str) for x in r):
                            E("%s.%d" % (p, i), "rip weld pair must be two bone names: [boneA, boneB]")
                    elif isinstance(r, dict):
                        if "bones" not in r or not (isinstance(r["bones"], list) and len(r["bones"]) == 2 and all(isinstance(x, str) for x in r["bones"])):
                            E("%s.%d" % (p, i), "rip weld object must have 'bones': [boneA, boneB]")
                    else:
                        E("%s.%d" % (p, i), "rip weld must be a pair or object")
        elif t == "membranes":
            if not isinstance(v, list):
                E(p, "a list of membrane definitions")
            else:
                for i, m in enumerate(v):
                    if not isinstance(m, dict):
                        E("%s.%d" % (p, i), "membrane must be an object")
                    elif "bones" in m and not (isinstance(m["bones"], list) and all(isinstance(x, str) for x in m["bones"])):
                        E("%s.%d.bones" % (p, i), "bones must be a list of spar bone names")
        elif t == "rigid_islands":
            if v in ("auto", True, False):
                pass
            elif not isinstance(v, list):
                E(p, "a list of rigid island rules or \"auto\": [{\"bone\": \"spine_2\", \"at\": [x, y, z]}]")
            else:
                for i, r in enumerate(v):
                    if not isinstance(r, dict) or not isinstance(r.get("bone"), str):
                        E("%s.%d" % (p, i), "rigid island must specify target 'bone'")

    if kind == "tripo":
        _check_tripo(rig, source, E, W)
    elif kind == "build":
        _check_build(rig, E, W)
    elif kind == "placed":
        _check_placed(rig, E, W)
    elif kind == "humanoid":
        _check_humanoid(spec, E, W)
    elif kind == "custom":
        if not rig.get("builder"):
            E("rig.builder", "a custom rig names its builder script")
        elif not isinstance(rig["builder"], str):
            E("rig.builder", "builder script must be a filename (e.g. rig_boss.py)")

    if kind != "humanoid" and "humanoid" in spec and spec["humanoid"] is not None:
        _check_humanoid(spec, E, W)

    b = spec.get("budget", "absent")
    if b != "absent" and b is not None and not (_num(b) and int(b) == b and b > 0):
        E("budget", "a whole number of triangles, or empty for full resolution")
    clips = spec.get("clips")
    if clips is not None:
        if not isinstance(clips, dict): E("clips", "clips must be an object")
        elif clips.get("archetype") not in (None,) + CLIP_ARCHETYPES:
            E("clips.archetype", "one of " + ", ".join(CLIP_ARCHETYPES))
        if isinstance(clips, dict) and "walk" in clips:
            w = clips["walk"]
            if not isinstance(w, dict):
                E("clips.walk", "walk must be an object")
            else:
                if "preset" in w and w["preset"] and w["preset"] not in WALK_PRESETS:
                    W("clips.walk.preset", "unknown walk preset %r" % w["preset"])
                for k in ("stride", "cadence", "sway", "bob", "lean", "hip_drop", "counter_twist", "arm_swing", "foot_lift", "duty_factor"):
                    if k in w and w[k] is not None and not _num(w[k]):
                        E("clips.walk." + k, "must be a number")
    card = spec.get("card")
    if isinstance(card, dict) and "metres" in card and card["metres"] is not None and not (_num(card["metres"]) and card["metres"] > 0):
        E("card.metres", "a size in metres, above 0")
    return errs, warns


def _check_tripo(rig, source, E, W):
    known = joint_names(source)
    have_source = bool(known)
    if source is not None and not known:
        W("rig.kind", "the source has no skeleton: a Tripo-style rig needs one (use Build)")
    made = set()
    for i, chain in enumerate(rig.get("mirror") or []):
        if not isinstance(chain, list): continue
        for n in chain:
            if have_source and n not in known: E("rig.mirror.%d" % i, "no bone %s in the source" % n)
            made.add(str(n) + "_m")
    names = known | made
    folds = {j["name"]: j["folds_into"] for j in (source or {}).get("joints", []) if j.get("folds_into")}

    def bone(p, n):
        if not have_source or not isinstance(n, str): return
        if n not in names: E(p, "no bone %s in the source%s" % (n, " (a mirrored copy needs its bone in Mirror)" if n.endswith("_m") else ""))
        elif n in folds: W(p, "%s sits on %s (a bone of no length): the rig uses %s" % (n, folds[n], folds[n]))

    single = rig.get("body") == "single"
    for k in ("head", "hips"):
        if not rig.get(k) and not single: E("rig." + k, "pick the %s bone" % k)
        elif rig.get(k): bone("rig." + k, rig[k])
    if not single and rig.get("head") and rig.get("head") == rig.get("hips"):
        E("rig.head", "the head and hips must be different bones")
    if single and not rig.get("forward"): E("rig.forward", "a single body needs its facing")
    if rig.get("shell"): bone("rig.shell", rig["shell"])
    for k in ("legs", "delete", "nodeform"):
        for n in rig.get(k) or []: bone("rig." + k, n)
    seen = {}
    for role, tops in (rig.get("chains") or {}).items():
        if not isinstance(tops, list): continue
        for n in tops:
            bone("rig.chains." + role, n)
            if n in seen: E("rig.chains." + role, "%s is already the first bone of %s" % (n, seen[n]))
            seen[n] = role
            if n in (rig.get("head"), rig.get("hips")): E("rig.chains." + role, "%s is the head or hips bone" % n)
    deleted = set(rig.get("delete") or [])
    for k in ("head", "hips"):
        if rig.get(k) in deleted: E("rig." + k, "%s is thrown away (Throw away)" % rig[k])


def _check_build(rig, E, W):
    chains = rig.get("chains")
    if not isinstance(chains, list) or not chains:
        E("rig.chains", "a build rig needs at least one chain (the body)"); return
    names = []
    for i, c in enumerate(chains):
        p = "rig.chains.%d" % i
        if not isinstance(c, dict): E(p, "a chain is an object"); continue
        if not c.get("name"): E(p, "give the chain a name")
        ways = [k for k in ("slice", "tube", "tip", "points") if k in c]
        if len(ways) != 1: E(p, "place it one way: slice, tube, tip or points"); continue
        w = ways[0]
        if w == "slice" and not (isinstance(c["slice"], list) and len(c["slice"]) == 2 and all(_num(x) for x in c["slice"])):
            E(p + ".slice", "from and to along the body, 0..1")
        if w == "tube" and not (isinstance(c["tube"], list) and len(c["tube"]) == 2 and all(_vec(x) for x in c["tube"])):
            E(p + ".tube", "two points")
        if w == "tip" and not _vec(c["tip"]): E(p + ".tip", "click the limb's tip")
        if w == "points" and not (isinstance(c["points"], list) and len(c["points"]) >= 2 and all(_vec(x) for x in c["points"])):
            E(p + ".points", "at least two joints")
        for k in ("tip", "base", "first"):
            if k in c and _vec(c[k]) and any(not -0.05 <= x <= 1.05 for x in c[k]): W(p + "." + k, "outside the model's 0..1 box")
        if "bones" in c and not (_num(c["bones"]) and int(c["bones"]) == c["bones"] and c["bones"] >= 1):
            E(p + ".bones", "a whole number of bones, 1 or more")
        if "stations" in c:
            st = c["stations"]
            if not (isinstance(st, list) and len(st) >= 2 and all(_num(x) for x in st)):
                E(p + ".stations", "stations must be a list of numbers along the body, e.g. [0.15, 0.4, 0.85]")
            else:
                for j, x in enumerate(st):
                    if not (-0.05 <= x <= 1.05):
                        W("%s.stations.%d" % (p, j), "station outside the model's 0..1 range")
                if "bones" in c and _num(c["bones"]) and len(st) != int(c["bones"]) + 1:
                    W(p + ".stations", "stations count (%d) should match bones + 1 (%d)" % (len(st), int(c["bones"]) + 1))
                if len(st) >= 2:
                    diffs = [st[k + 1] - st[k] for k in range(len(st) - 1)]
                    if any(d == 0 for d in diffs) or (any(d > 0 for d in diffs) and any(d < 0 for d in diffs)):
                        W(p + ".stations", "stations should be strictly in order along the body")
                if "slice" in c and isinstance(c["slice"], list) and len(c["slice"]) == 2 and all(_num(x) for x in c["slice"]):
                    s0, s1 = min(c["slice"]), max(c["slice"])
                    if any(x < s0 - 0.05 or x > s1 + 0.05 for x in st):
                        W(p + ".stations", "stations outside the chain's slice range [%.2f, %.2f]" % (s0, s1))
        if c.get("parent"):
            pr = c["parent"]
            if not (isinstance(pr, list) and len(pr) == 2 and pr[0] in names):
                E(p + ".parent", "[an earlier chain's name, bone index]")
        for k in c:
            if k not in BUILD_CHAIN_FIELDS: W(p + "." + k, "not a chain field the editor knows; kept as it is")
        names.append(c.get("name"))


def _check_placed(rig, E, W):
    chains = rig.get("chains")
    if not isinstance(chains, list) or not chains:
        E("rig.chains", "a placed rig needs at least one chain (the body)")
    else:
        _check_build(rig, E, W)


def _check_humanoid(spec, E, W):
    h = spec.get("humanoid")
    if h is None:
        E("humanoid", "a humanoid rig needs a humanoid section with z heights and x spans")
        return
    if not isinstance(h, dict):
        E("humanoid", "humanoid must be an object")
        return
    for k in h:
        if k not in ("forward", "z", "x"):
            W("humanoid." + k, "not a humanoid setting the editor knows; kept as it is")
    if "forward" in h and h["forward"] != "auto" and not (_vec(h["forward"]) and any(h["forward"])):
        E("humanoid.forward", "a direction [x, y, z] or 'auto', e.g. [0, -1, 0]")
    if "z" not in h or not isinstance(h["z"], dict):
        E("humanoid.z", "z must be an object of heights: " + ", ".join(HUMANOID_Z_KEYS))
    else:
        z = h["z"]
        for k in HUMANOID_Z_KEYS:
            if k not in z:
                E("humanoid.z." + k, "missing height %s" % k)
            elif not _num(z[k]):
                E("humanoid.z." + k, "a number (0 floor .. 1 top)")
            elif z[k] < 0.0 or z[k] > 1.05:
                E("humanoid.z." + k, "heights must be within 0.0 (floor) and 1.0 (top)")
        for k in z:
            if k not in HUMANOID_Z_KEYS:
                W("humanoid.z." + k, "not a standard humanoid height")
        if all(k in z and _num(z[k]) for k in ("ankle", "knee", "hip")):
            if not (z["ankle"] < z["knee"] < z["hip"]):
                W("humanoid.z", "ankle should be below knee, and knee below hip")
        if all(k in z and _num(z[k]) for k in ("hip", "spine", "spine1", "spine2", "neck", "head", "top")):
            if not (z["hip"] <= z["spine"] < z["spine1"] < z["spine2"] < z["neck"] < z["head"] <= z["top"]):
                W("humanoid.z", "spine heights should increase from hip to top of head")
        if all(k in z and _num(z[k]) for k in ("spine1", "arm", "top")):
            if not (z["spine1"] <= z["arm"] <= z["top"]):
                W("humanoid.z.arm", "arm height is usually near the shoulders, between spine1 and neck")

    if "x" not in h or not isinstance(h["x"], dict):
        E("humanoid.x", "x must be an object of spans: " + ", ".join(HUMANOID_X_KEYS))
    else:
        x = h["x"]
        for k in HUMANOID_X_KEYS:
            if k not in x:
                E("humanoid.x." + k, "missing span %s" % k)
            elif not _num(x[k]):
                E("humanoid.x." + k, "a number (0 tip .. 0.5 center)")
            elif x[k] < 0.0 or x[k] > 0.55:
                E("humanoid.x." + k, "spans must be between 0.0 (outer tip) and 0.5 (center)")
        for k in x:
            if k not in HUMANOID_X_KEYS:
                W("humanoid.x." + k, "not a standard humanoid span")
        if all(k in x and _num(x[k]) for k in ("tip", "knuckle", "wrist", "elbow", "shoulder")):
            if not (x["tip"] <= x["knuckle"] <= x["wrist"] <= x["elbow"] <= x["shoulder"]):
                W("humanoid.x", "spans should increase inward: tip <= knuckle <= wrist <= elbow <= shoulder")


# ---------------------------------------------------------------------------------------------------------------
# Files: where the editor's own things live
# ---------------------------------------------------------------------------------------------------------------

def _load(path):
    try:
        with open(path, encoding="utf-8") as fh: return json.load(fh)
    except Exception:
        return None


def editor_dir(layout, name):
    return os.path.join(layout.WORK, "spec_editor", name)


def work_url(layout, path):
    rel = os.path.relpath(path, layout.WORK).replace("\\", "/")
    return "/files/work/" + rel


def read_spec_text(srv, name):
    g = srv.find_group(name)
    p = os.path.join(srv.model_dir(g, name), "rig.json")
    if not os.path.exists(p): return p, None
    with open(p, encoding="utf-8") as fh: return p, fh.read()


def audit_summary(a):
    """The numbers the before/after table shows, from an audit file."""
    if not a: return None
    v = a.get("verdict") or {}
    return {"pass": v.get("pass"), "checks": {k: {"value": c.get("value"), "ok": c.get("ok"), "limit": c.get("limit")}
                                              for k, c in (v.get("checks") or {}).items()},
            "warnings": v.get("warnings", [])}


def hot_spots(a):
    """Where the audit's tears are: each bend that tore, at the worst edge's spot (0..1 of the rig's box)."""
    out = []
    for b in (a or {}).get("bends", []):
        if b.get("tear_edges"):
            out.append({"bone": b["bone"], "mode": b["mode"], "tears": b["tear_edges"], "at": b.get("worst_at"),
                        "owners": b.get("worst_edge_owners", []), "stretch": b.get("max_stretch")})
    out.sort(key=lambda s: (s["mode"] != "bend", -s["tears"]))
    share = {x.get("bone"): x.get("area_pct") for x in (a or {}).get("dominant_share", []) if isinstance(x, dict)}
    return out, share


def bundle(srv, name):
    """Everything the editor page loads for one model."""
    layout = srv.layout
    g = srv.find_group(name)
    path, text = read_spec_text(srv, name)
    spec, parse_error = None, None
    if text is not None:
        try: spec = json.loads(text)
        except ValueError as e: parse_error = str(e)
    src_json = os.path.join(layout.WORK, "source", name + ".json")
    source = _load(src_json)
    src_file = srv._quiet(layout.source_model, name)
    if source:
        source["glb_url"] = work_url(layout, os.path.join(layout.WORK, "source", source.get("glb", name + ".glb")))
        source["stale"] = bool(src_file and os.path.getmtime(src_file) > source.get("source_mtime", 0) + 1)
    rig_log = _load(os.path.join(layout.WORK, "qa", name + ".json")) or {}
    bone_from = {}
    for c in rig_log.get("chains") or []:
        for b, j in zip(c.get("bones", []), c.get("from", [])): bone_from[b] = j
    rig_bones = [b for c in rig_log.get("chains") or [] for b in c.get("bones", [])]
    audit = _load(os.path.join(layout.WORK, "audit", name + ".json"))
    spots, share = hot_spots(audit)
    ed = editor_dir(layout, name)
    before = _load(os.path.join(ed, "before.json"))
    measure = os.path.join(ed, name + ".png")
    survey = _load(os.path.join(layout.WORK, "survey", name + ".json"))
    rd = srv._quiet(layout.rigged_dir, name)
    preview_url, preview_info = None, None
    if rd:
        glb = os.path.join(rd, "preview.glb")
        if os.path.exists(glb):
            rel = os.path.relpath(glb, layout.ROOT).replace("\\", "/")
            preview_url = "/files/models/" + rel + "?v=%d" % int(os.path.getmtime(glb))
            pj = os.path.join(rd, "preview.json")
            if os.path.exists(pj):
                preview_info = _load(pj)
    return {"name": name, "group": g, "path": path, "text": text, "base": text_hash(text), "spec": spec,
            "parse_error": parse_error, "schema": schema(), "source": source, "source_file": src_file and os.path.basename(src_file),
            "survey": survey, "rig_bones": rig_bones, "bone_from": bone_from, "rig_error": rig_log.get("error"),
            "audit": audit_summary(audit), "audit_time": audit and os.path.getmtime(os.path.join(layout.WORK, "audit", name + ".json")),
            "spots": spots, "share": share, "before": before,
            "measure": work_url(layout, measure) + "?v=%d" % int(os.path.getmtime(measure)) if os.path.exists(measure) else None,
            "preview_url": preview_url, "preview_info": preview_info,
            "backup": os.path.exists(path + ".bak")}


def write_spec(srv, name, spec, base, force=False):
    """Validates and writes rig.json, keeping the file it replaces as rig.json.bak. Returns (text, errors, warns)."""
    path, old = read_spec_text(srv, name)
    text = dumps(spec)
    if not force and base is not None and text_hash(old) != base:
        if old is not None and text_hash(old) == text_hash(text):
            srv.spec_store.reload()
            src = _load(os.path.join(srv.layout.WORK, "source", name + ".json"))
            errs, warns = check(spec, src)
            return text, errs, warns
        raise Conflict("rig.json changed on disk since the editor loaded it: reload or overwrite to continue (your edit is "
                       "not lost; copy it from the JSON view)", disk_base=text_hash(old))
    src = _load(os.path.join(srv.layout.WORK, "source", name + ".json"))
    errs, warns = check(spec, src)
    if errs: return None, errs, warns
    if old is not None:
        shutil.copy2(path, path + ".bak")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh: fh.write(text)
    os.replace(tmp, path)
    srv.spec_store.reload()
    return text, errs, warns


class Conflict(Exception):
    def __init__(self, message, disk_base=None):
        super().__init__(message)
        self.disk_base = disk_base


# ---------------------------------------------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------------------------------------------

def static_file(path):
    if path != "/spec_editor.js": return None
    with open(os.path.join(HERE, "spec_editor.js"), "rb") as fh:
        return fh.read(), STATIC_TYPES[".js"]


def page(token):
    with open(os.path.join(HERE, "spec_editor.html"), encoding="utf-8") as fh:
        return fh.read().replace("__AUTORIG_TOKEN__", token)


def get(h, app, srv, path, q):
    """GET routes under the token. True when handled."""
    if path == "/api/anim/presets":
        try:
            import gait
        except ImportError:
            from autorig.core import gait
        h._send(200, {"presets": gait.GAIT_PRESETS})
        return True
    if path == "/api/spec/hash":
        name = (q.get("name") or [""])[0]
        if srv.find_group(name) is None:
            h._send(404, {"error": "no model " + name}); return True
        path_file, text = read_spec_text(srv, name)
        mtime = os.path.getmtime(path_file) if path_file and os.path.exists(path_file) else 0
        h._send(200, {"name": name, "base": text_hash(text), "mtime": mtime})
        return True
    if path != "/api/spec": return False
    name = (q.get("name") or [""])[0]
    if srv.find_group(name) is None:
        h._send(404, {"error": "no model " + name}); return True
    srv.spec_store.reload()
    h._send(200, bundle(srv, name))
    return True


def _job(app, srv, name, label, cmds):
    job = app.runner.submit(srv.Job(name, srv.find_group(name), label, cmds))
    return job.info()


def post(h, app, srv, path, body):
    """POST routes under the token. True when handled."""
    if path == "/api/anim/evaluate":
        try:
            import gait
        except ImportError:
            from autorig.core import gait
        preset_name = body.get("preset", "natural")
        overrides = body.get("overrides", {})
        num_frames = int(body.get("frames", 24))
        is_biped = body.get("type", "biped") == "biped"
        params = gait.merge_gait_params(preset_name, overrides)
        frames_out = []
        for i in range(num_frames):
            phase = i / float(num_frames)
            if is_biped:
                action = body.get("action") or (preset_name if preset_name in ("attack", "hit", "death") else None)
                if action == "attack":
                    st = gait.evaluate_biped_attack(phase, 1.0, 1.0, is_shooter=bool(body.get("shooter", False)))
                elif action == "hit":
                    st = gait.evaluate_biped_hit(phase, 1.0, 1.0, direction=body.get("direction", "front"))
                elif action == "death":
                    st = gait.evaluate_biped_death(phase, 1.0, 1.0)
                elif preset_name in ("run", "sprint"):
                    st = gait.evaluate_biped_run(phase, 1.0, 1.0, params, is_shooter=bool(body.get("shooter", False)))
                else:
                    st = gait.evaluate_biped_walk(phase, 1.0, 1.0, params, is_shooter=bool(body.get("shooter", False)))
            else:
                if preset_name == "quadruped_gallop":
                    st = gait.evaluate_quadruped_gallop(phase, 1.0, 1.0, feet_info=body.get("feet"), params=params)
                else:
                    st = gait.evaluate_quadruped_walk(phase, 1.0, 1.0, feet_info=body.get("feet"), params=params)
            frames_out.append(st)
        thigh_swing = 22.0 * params.get("stride", 1.0)
        stride = 2.0 * math.sin(math.radians(thigh_swing))
        cadence = params.get("cadence", 1.0)
        h._send(200, {
            "preset": preset_name,
            "params": params,
            "frames": frames_out,
            "stride": stride,
            "cadence": cadence,
            "walk_frames": max(10, int(round(24.0 / cadence)))
        })
        return True

    if not path.startswith("/api/spec/"): return False
    name = body.get("model", "")
    g = srv.find_group(name)
    if g is None:
        h._send(404, {"error": "no model " + name}); return True
    layout = srv.layout
    what = path[len("/api/spec/"):]
    try:
        if what == "check":
            _, old = read_spec_text(srv, name)
            errs, warns = check(body.get("spec"), _load(os.path.join(layout.WORK, "source", name + ".json")))
            new = dumps(body.get("spec")) if isinstance(body.get("spec"), dict) else ""
            h._send(200, {"errors": errs, "warnings": warns, "text": new, "diff": diff(old, new),
                          "changed": (old or "") != new})
        elif what in ("save", "rerig", "rebake_clips"):
            force = bool(body.get("force", False))
            text, errs, warns = write_spec(srv, name, body.get("spec"), body.get("base"), force=force)
            if errs:
                h._send(400, {"error": "the spec has problems: " + "; ".join("%s: %s" % (e["path"], e["message"]) for e in errs[:5]),
                              "errors": errs, "warnings": warns}); return True
            out = {"saved": True, "text": text, "base": text_hash(text), "warnings": warns}
            if what == "rerig":
                ed = editor_dir(layout, name); os.makedirs(ed, exist_ok=True)
                cur = _load(os.path.join(layout.WORK, "audit", name + ".json"))
                if cur:              # the audit this edit is measured against
                    with open(os.path.join(ed, "before.json"), "w", encoding="utf-8") as fh:
                        json.dump(dict(audit_summary(cur), time=time.time(), spots=hot_spots(cur)[0][:12]), fh, indent=1)
                spec = srv.spec_store.model(name)
                st = srv.status(g, name)
                if st["steps"].get("rig"):
                    h._send(409, {"error": st["steps"]["rig"]}); return True
                c_arch = (spec.get("clips") or (spec.get("rig") or {}).get("clips") or {}).get("archetype")
                steps = ["rig", "trim", "audit"] + (["clips"] if c_arch else []) + ["preview"]
                cmds = [c for s in steps for c in srv.commands(g, name, s, spec)]
                out["job"] = _job(app, srv, name, "save and re-rig", cmds)
            elif what == "rebake_clips":
                spec = srv.spec_store.model(name)
                infer_fn = getattr(srv.spec_store, "infer_clip_archetype", lambda s: None)
                c_arch = (spec.get("clips") or (spec.get("rig") or {}).get("clips") or {}).get("archetype") or infer_fn(spec)
                if not c_arch:
                    c_arch = "walker"
                steps = ["clips", "preview"]
                cmds = [c for s in steps for c in srv.commands(g, name, s, spec)]
                out["job"] = _job(app, srv, name, "re-bake clips", cmds)
            h._send(200, out)
        elif what == "auto-tune":
            if body.get("spec"):
                force = bool(body.get("force", True))
                text, errs, warns = write_spec(srv, name, body.get("spec"), body.get("base"), force=force)
                if errs:
                    h._send(400, {"error": "the spec has problems: " + "; ".join("%s: %s" % (e["path"], e["message"]) for e in errs[:5]),
                                  "errors": errs, "warnings": warns}); return True
            ed = editor_dir(layout, name); os.makedirs(ed, exist_ok=True)
            cur = _load(os.path.join(layout.WORK, "audit", name + ".json"))
            if cur:
                with open(os.path.join(ed, "before.json"), "w", encoding="utf-8") as fh:
                    json.dump(dict(audit_summary(cur), time=time.time(), spots=hot_spots(cur)[0][:12]), fh, indent=1)
            st = srv.status(g, name)
            if st["steps"].get("rig"):
                h._send(409, {"error": st["steps"]["rig"]}); return True
            auto_tune_script = os.path.join(os.path.dirname(HERE), "steps", "auto_tune.py")
            max_iter = str(body.get("max_iterations", 3))
            cmd = (f"auto-tune ({max_iter} iter)", [sys.executable, "-u", auto_tune_script, name, "-max-iter", max_iter], None)
            preview_cmd = ("preview", srv.blender_cmd("preview_glb.py", "-only", name), None)
            job = _job(app, srv, name, "auto-tune", [cmd, preview_cmd])
            h._send(200, {"job": job})
        elif what == "source":
            if not srv._quiet(layout.source_model, name):
                h._send(409, {"error": "no source export in the model folder"}); return True
            h._send(200, _job(app, srv, name, "source view",
                              [("source view", srv.blender_cmd("source_preview.py", "-only", name), None)]))
        elif what == "measure":
            spec = body.get("spec")
            errs, _ = check(spec, _load(os.path.join(layout.WORK, "source", name + ".json")))
            if errs:
                h._send(400, {"error": "fix the spec first: " + errs[0]["path"] + ": " + errs[0]["message"]}); return True
            ed = editor_dir(layout, name); os.makedirs(ed, exist_ok=True)
            draft = os.path.join(ed, "draft.json")
            with open(draft, "w", encoding="utf-8") as fh: fh.write(dumps(spec))
            h._send(200, _job(app, srv, name, "flat views",
                              [("flat views of the draft", srv.blender_cmd("measure.py", name, "-out", ed, "-spec", draft), None)]))
        elif what == "suggest":
            try:
                import suggest
            except ImportError:
                from autorig.core import suggest
            src = body.get("source") or _load(os.path.join(layout.WORK, "source", name + ".json"))
            surv = body.get("survey") or _load(os.path.join(layout.WORK, "survey", name + ".json"))
            proposal = suggest.suggest_skeleton(name, source_data=src, survey_data=surv)
            h._send(200, proposal)
        else:
            h._send(404, {"error": "not found"})
    except Conflict as e:
        h._send(409, {"error": str(e), "conflict": True, "disk_base": getattr(e, "disk_base", None)})
    return True
