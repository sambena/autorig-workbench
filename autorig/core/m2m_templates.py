# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: Mesh2Motion's skeleton templates and their clips, and which template suits which body plan.
#
# Mesh2Motion (https://mesh2motion.org; app MIT, rigs and animations CC0) ships nine skeletons and about 250 clips
# made for them. A clip plays only on the skeleton it was made for, so steps/m2m_rig.py fits one of these skeletons
# to a mesh (the bones moved into the body, names and hierarchy kept) and the clips play as they are.
#
# Plain Python (no bpy): the server, the API and the tests read the same metadata. Everything about a template is
# read from the app's own files, static/rigs/rig-<t>.glb (the bones) and static/animations/<t>-animations.glb (the
# clips, with the rig and a sample mesh), so a newer Mesh2Motion brings its new clips without a change here.
#
#   choose(archetype, measures)   the template for a suggest.py archetype, with the reason
#   bones(t), clips(t)            a template's bones (name, parent) and clips (name, file, seconds, root motion)
#   is_root_motion(name)          a clip that keeps its root's travel: its name ends in " RM" or "_RM"
#   resolve_clips(t, spec)        "walk,idle" / "Walk=walk" / "all" -> [(clip, engine name)]
#
# The M2M folder is the app checkout (static/rigs, static/animations), asset-packs' _mesh2motion copy (rigs/,
# animations/) or anything else laid out either way: m2m_root() looks in --m2m, AUTORIG_M2M, then the usual places.
import json, os, struct

# Per template: its files under static/, the one bone (besides the root) whose position the clips keep
# (RigConfig.ts position_tracking_bone_name), what the fit treats it as, and the clips it bakes by default.
#   family    "human": joints measured as rerig_humanoid measures a person; "legged": legs level by level, the body
#             slice by slice (m2m_quadruped's fit, any number of leg pairs); "body": every joint slice by slice
#             (no legs to stand on: snake, shark), and "spider" the same with its legs splayed out to the sides
#   roles     clip roles -> the template's clip for it ("walk" and "idle" are what a game asks for first)
#   skin      the default skinning method (P2b's bake-off decides; "autorig" = steps/m2m_rig.skin, bone heat on a
#             voxel copy carried back by nearest face)
TEMPLATES = {
    "human": {"title": "Human", "rig": "rig-human.glb",
              "animations": ["human-base-animations.glb", "human-addon-animations.glb", "human-mocap-animations.glb"],
              "tracking": "pelvis", "family": "human",
              "roles": {"idle": "Idle_A", "walk": "Walk", "run": "Jog", "sprint": "Sprint", "jump": "Jump_Start"}},
    "fox": {"title": "Fox", "rig": "rig-fox.glb", "animations": ["fox-animations.glb"], "tracking": "Hips",
            "family": "legged", "roles": {"idle": "Idle", "walk": "Walk", "run": "Run", "jump": "Jump"}},
    "horse": {"title": "Horse", "rig": "rig-horse.glb", "animations": ["horse-animations.glb"], "tracking": "hips",
              "family": "legged", "roles": {"idle": "Idle", "walk": "Walk", "trot": "Trot", "run": "Run"}},
    "bird": {"title": "Bird", "rig": "rig-bird.glb", "animations": ["bird-animations.glb"], "tracking": "hips",
             "family": "legged", "roles": {"idle": "Idle", "walk": "Walk", "fly": "Flap", "glide": "Glide"}},
    "dragon": {"title": "Dragon", "rig": "rig-dragon.glb", "animations": ["dragon-animations.glb"], "tracking": "hips",
               "family": "legged", "roles": {"idle": "Idle", "walk": "Walk", "fly": "Fly Flap", "glide": "Fly Glide"}},
    "kaiju": {"title": "Kaiju", "rig": "rig-kaiju.glb", "animations": ["kaiju-animations.glb"], "tracking": "Hips",
              "family": "legged", "roles": {"idle": "Idle", "walk": "Walk", "attack": "Attack", "roar": "Roar"}},
    "spider": {"title": "Spider", "rig": "rig-spider.glb", "animations": ["spider-animations.glb"], "tracking": "hips",
               "family": "spider", "roles": {"idle": "Idle", "walk": "Walk", "attack": "Attack", "jump": "Jump"}},
    "snake": {"title": "Snake", "rig": "rig-snake.glb", "animations": ["snake-animations.glb"], "tracking": "head",
              "family": "body", "roles": {"idle": "Idle", "walk": "Side winding", "attack": "Bite"}},
    "shark": {"title": "Shark", "rig": "rig-shark.glb", "animations": ["shark-animations.glb"], "tracking": "pelvis",
              "family": "body", "roles": {"idle": "Idle", "walk": "Swim Horizontal", "swim": "Swim Horizontal",
                                          "attack": "Bite"}},
}
for _t in TEMPLATES.values():
    _t.setdefault("skin", "autorig")

DEFAULT_ROLES = ("idle", "walk")
SKIN_METHODS = ("autorig",)            # "m2m" arrives with P2b (core/m2m_skin.py)

# quadruped: Horse or Fox by size. Their sample meshes have the same build (legs 0.64 and 0.63 of the body between
# the feet), so the shape cannot tell a cow from a dog; standing this tall or more (metres) is a horse's size.
HORSE_HEIGHT = 0.9
# a body whose underside lies on the ground over this share of its footprint has no legs to stand on: a snake if
# it is this slender (length over width and height), a fish otherwise
LYING = 0.3
SERPENT_SLENDER = 5.0
# a biped with a tail longer than this share of its height, reaching back behind it, is a kaiju, not a person
KAIJU_TAIL = 0.3

# archetypes (core/suggest.py, plus a few names a caller may pass) with no Mesh2Motion template: these keep the
# old path (rerig.py and make_clips.py)
NO_TEMPLATE = ("floater", "rigid", "turret", "machine", "prop", "creature")


def is_root_motion(name):
    """Mesh2Motion keeps a clip's root travel when its name marks it root motion: "Walk RM", "Roll_RM". The app tests
    `name.toLowerCase().endsWith('rm')`, which also caught any name ending in the letters (a "Swarm", a "Storm"); only
    the separate RM suffix counts here."""
    return isinstance(name, str) and (name.endswith(" RM") or name.endswith("_RM"))


def choose(archetype, measures=None):
    """(template, reason) for an archetype, or (None, reason) when Mesh2Motion has none for it. measures, all optional:
       metres      its standing height, when the height is real (quadrupeds: Horse or Fox)
       feet        feet touching the ground (core/body_plan.py)
       ground      the share of its footprint lying on the ground (m2m_rig.body_measures)
       slender     length over the larger of width and height
       stand       height over length
       tail        how far it reaches back past a person's depth, over its height (a kaiju's tail)
       wings       True when wings were found
    The body's measures overrule the archetype where they plainly disagree: a body lying on the ground is a snake or
    a fish whatever the tips said (the sea serpent's tips read "rigid"), and two feet are not a quadruped's."""
    m = measures or {}
    a = (archetype or "").lower()
    why = ""
    if (m.get("ground") or 0) >= LYING and a not in ("humanoid", "biped", "human"):
        if (m.get("slender") or 0) >= SERPENT_SLENDER:
            return "snake", "lies on the ground (%.0f%% of its footprint), slender %.1f: Snake (was %s)" % (
                100 * m["ground"], m["slender"], a or "unknown")
        return "shark", "lies on the ground (%.0f%% of its footprint), not slender: Shark (was %s)" % (
            100 * m["ground"], a or "unknown")
    if m.get("wings") and a not in ("winged", "bird", "dragon"):
        why = " (wings: was %s)" % a; a = "winged"
    elif a in ("quadruped", "", "rigid", "creature") and m.get("feet") == 2:
        if (m.get("stand") or 1) < 0.5:
            why = " (long and low on two contact patches, a body resting on fins: was %s)" % (a or "unknown")
            a = "swimmer"
        elif (m.get("stand") or 0) >= 1.6:
            why = " (two feet under an upright body: was %s)" % (a or "unknown"); a = "humanoid"
        else:
            why = " (two feet under a long body: was %s)" % (a or "unknown"); a = "winged"
    elif a in ("", "rigid", "creature") and (m.get("feet") or 0) >= 5:
        a = "octopod"
    elif a in ("", "rigid", "creature") and m.get("feet") == 4:
        a = "quadruped"
    t, reason = _choose(a, m)
    return t, reason + why


def _choose(a, m):
    if a in ("humanoid", "biped", "human"):
        if (m.get("tail") or 0) > KAIJU_TAIL:
            return "kaiju", "a biped with a tail %.2f of its height: Kaiju" % m["tail"]
        return "human", "humanoid: Human"
    if a in ("quadruped", "canine", "feline", "equine", "bovine"):
        h = m.get("metres")
        if h is None:
            return "horse", "quadruped of unknown size: Horse"
        if h >= HORSE_HEIGHT:
            return "horse", "quadruped %.2f m tall (Horse from %.1f m): Horse" % (h, HORSE_HEIGHT)
        return "fox", "quadruped %.2f m tall (under %.1f m): Fox" % (h, HORSE_HEIGHT)
    if a in ("winged", "bird", "dragon", "flyer"):
        if (m.get("feet") or 0) >= 4 or a == "dragon":
            return "dragon", "winged on four feet: Dragon"
        return "bird", "winged on two feet: Bird"
    if a in ("serpent", "snake", "worm"):
        return "snake", "serpent: Snake"
    if a in ("swimmer", "fish", "shark"):
        return "shark", "swimmer: Shark"
    if a in ("hexapod", "octopod", "spider", "insect", "crab"):
        return "spider", "%s: Spider (eight legs; six-legged bodies use its front three pairs)" % a
    if a in ("kaiju",):
        return "kaiju", "kaiju"
    if a in TEMPLATES:
        return a, "named template"
    return None, "no Mesh2Motion template for %r: rerig.py and make_clips.py" % (a or "nothing")


# ---------------------------------------------------------------- the M2M files

def m2m_root(given=None, models=None):
    """The first folder that holds Mesh2Motion's rigs and animations, or None."""
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cands = [given, os.environ.get("AUTORIG_M2M"),
             os.path.join(here, "third_party", "mesh2motion-app"),
             os.path.expanduser("~/src/mesh2motion-app"),
             os.path.join(models or os.environ.get("AUTORIG_MODELS") or "", "_mesh2motion") if (models or os.environ.get("AUTORIG_MODELS")) else None,
             os.path.expanduser("~/src/asset-packs/_mesh2motion")]
    for c in cands:
        if c and dirs(c): return os.path.abspath(c)
    return None


def dirs(root):
    """(rigs folder, animations folder) under an M2M folder laid out either way, or None."""
    if not root: return None
    for base in (os.path.join(root, "static"), root):
        r, a = os.path.join(base, "rigs"), os.path.join(base, "animations")
        if os.path.isdir(r) and os.path.isdir(a): return r, a
    return None


def files(t, root):
    """(rig GLB, [animation GLBs]) for a template, paths under root."""
    d = dirs(root)
    if d is None: raise FileNotFoundError("no Mesh2Motion rigs/animations under %s" % root)
    info = TEMPLATES[t]
    return os.path.join(d[0], info["rig"]), [os.path.join(d[1], f) for f in info["animations"]]


def read_gltf(path):
    """A GLB's JSON chunk (or a .gltf file's JSON)."""
    with open(path, "rb") as fh:
        head = fh.read(12)
        if head[:4] != b"glTF":
            fh.seek(0)
            return json.loads(fh.read().decode("utf-8"))
        n, kind = struct.unpack("<I4s", fh.read(8))
        if kind != b"JSON": raise ValueError("%s: first chunk is not JSON" % path)
        return json.loads(fh.read(n).decode("utf-8"))


def gltf_bones(g):
    """[(name, parent name or None)] of the first skin's joints, parents first, as the file lists them."""
    nodes = g.get("nodes") or []
    skins = g.get("skins") or []
    joints = skins[0]["joints"] if skins else list(range(len(nodes)))
    parent = {}
    for i, n in enumerate(nodes):
        for c in n.get("children", []): parent[c] = i
    js = set(joints)
    out = []
    for j in joints:
        p = parent.get(j)
        out.append((nodes[j].get("name", "joint_%d" % j), nodes[p].get("name") if p in js else None))
    return out


def gltf_clips(g):
    """[{name, seconds}] of every animation in a parsed glTF."""
    acc = g.get("accessors") or []
    out = []
    for a in g.get("animations") or []:
        t = 0.0
        for s in a.get("samplers", []):
            mx = (acc[s["input"]].get("max") or [0.0]) if s.get("input") is not None and s["input"] < len(acc) else [0.0]
            t = max(t, float(mx[0]))
        out.append({"name": a.get("name", ""), "seconds": round(t, 3)})
    return out


def bones(t, root=None):
    rig, _ = files(t, root or m2m_root())
    return gltf_bones(read_gltf(rig))


def clips(t, root=None):
    """Every clip of a template: name, the file it is in, its length, whether it keeps root motion, and whether it is
    the rig's own rest pose (Rest Pose / Rest_Pose, which a game never plays)."""
    _, anims = files(t, root or m2m_root())
    out, seen = [], set()
    for f in anims:
        if not os.path.exists(f): continue
        for c in gltf_clips(read_gltf(f)):
            if c["name"] in seen: continue
            seen.add(c["name"])
            out.append(dict(c, file=os.path.basename(f), root_motion=is_root_motion(c["name"]),
                            rest=c["name"].replace("_", " ").lower() == "rest pose"))
    return out


def summary(root=None):
    """Every template with its bone and clip counts, for a listing."""
    root = root or m2m_root()
    out = []
    for t, info in TEMPLATES.items():
        try:
            b, c = bones(t, root), clips(t, root)
        except (OSError, ValueError, KeyError):
            continue
        out.append({"template": t, "title": info["title"], "bones": len(b), "clips": len(c),
                    "family": info["family"], "skin": info["skin"], "roles": dict(info["roles"])})
    return out


def resolve_clips(t, spec, available=None):
    """The clips to bake for template t, as [(M2M clip name, engine name)]. spec is a comma list; each item a role
    ("walk"), a clip's own name ("Side winding"), either with "=engine name" ("Walk=walk"), or "all" (every clip but
    the rest pose). Empty means the default roles. available: the template's clip names, to check against."""
    roles = TEMPLATES[t]["roles"]
    have = None if available is None else list(available)
    items = [s.strip() for s in (spec or ",".join(DEFAULT_ROLES)).split(",") if s.strip()]
    out = []
    for it in items:
        if it.lower() == "all":
            for n in have or []:
                if n.replace("_", " ").lower() != "rest pose" and n not in [o[0] for o in out]: out.append((n, n))
            continue
        src, _, dst = it.partition("=")
        src, dst = src.strip(), dst.strip()
        name = roles.get(src.lower(), src)
        if have is not None and name not in have:
            low = {n.lower(): n for n in have}
            if name.lower() not in low:
                raise KeyError("%s has no clip %r (roles: %s)" % (t, src, ", ".join(sorted(roles))))
            name = low[name.lower()]
        engine = dst or (src.lower() if src.lower() in roles else name)
        if name not in [o[0] for o in out]: out.append((name, engine))
    return out


# Mesh2Motion human bone -> Mixamo's name (without the "mixamorig:" namespace, as rerig_humanoid writes them)
MIXAMO_HUMAN = {"pelvis": "Hips", "spine_01": "Spine", "spine_02": "Spine1", "spine_03": "Spine2", "neck_01": "Neck",
                "head": "Head", "head_leaf": "HeadTop_End"}
for _s, _S in (("l", "Left"), ("r", "Right")):
    MIXAMO_HUMAN.update({"clavicle_" + _s: _S + "Shoulder", "upperarm_" + _s: _S + "Arm",
                         "lowerarm_" + _s: _S + "ForeArm", "hand_" + _s: _S + "Hand",
                         "thigh_" + _s: _S + "UpLeg", "calf_" + _s: _S + "Leg", "foot_" + _s: _S + "Foot",
                         "ball_" + _s: _S + "ToeBase", "ball_leaf_" + _s: _S + "Toe_End"})
    for _f in ("thumb", "index", "middle", "ring", "pinky"):
        for _k in (1, 2, 3):
            MIXAMO_HUMAN["%s_0%d_%s" % (_f, _k, _s)] = "%sHand%s%d" % (_S, _f.capitalize(), _k)
        MIXAMO_HUMAN["%s_04_leaf_%s" % (_f, _s)] = "%sHand%s4" % (_S, _f.capitalize())


def to_gltf(p):
    """A Blender point (Z up, facing -Y) in glTF's axes (Y up, facing +Z), as three.js and Mesh2Motion see it."""
    return [round(float(p[0]), 5), round(float(p[2]), 5), round(float(-p[1]), 5)]


def from_gltf(p):
    return [float(p[0]), float(-p[2]), float(p[1])]
