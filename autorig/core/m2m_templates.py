# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: Mesh2Motion's skeleton templates and their clips, and which template suits which body plan.
#
# Mesh2Motion (https://github.com/Mesh2Motion/mesh2motion-app, Scott Petrovic and contributors; app MIT, rigs and
# animations CC0; CREDITS.md) ships nine skeletons and about 250 clips
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
# animations/) or anything else laid out either way: m2m_root() looks in --m2m, the third_party/mesh2motion-app
# submodule, AUTORIG_M2M, ~/src/mesh2motion-app, then the collection's _mesh2motion.
import json, os, struct

# Per template: its files under static/, the one bone (besides the root) whose position the clips keep
# (RigConfig.ts position_tracking_bone_name), what the fit treats it as, and the clips it bakes by default.
#   family    "human": joints measured as rerig_humanoid measures a person; "legged": legs level by level, the body
#             slice by slice (m2m_quadruped's fit, any number of leg pairs); "body": every joint slice by slice
#             (no legs to stand on: snake, shark), and "spider" the same with its legs splayed out to the sides
#   roles     clip roles -> the template's clip for it ("walk" and "idle" are what a game asks for first)
#   skin      the default skinning method, set by M2b's bake-off (docs/SKIN-BAKEOFF.md): "autorig" = steps/m2m_rig.skin,
#             bone heat on a voxel copy carried back by nearest face; "m2m" = Mesh2Motion's own (core/m2m_skin.py)
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
                                          "attack": "Bite"},
              # the head joint 22% of the length back from the nose (the template's is 29%: on Tripo's sharks the
              # head then owned 36-41% of the skin, the thickest part of the body; a shark's head, snout to the last
              # gill slit, is about a fifth to a quarter of it). steps/m2m_rig.fit_line
              "head_at": 0.22},
}
# M2b's bake-off (docs/SKIN-BAKEOFF.md, 2026-10-18): ours beat Mesh2Motion's skinning on all nine templates
for _t in TEMPLATES.values():
    _t.setdefault("skin", "autorig")

DEFAULT_ROLES = ("idle", "walk")
SKIN_METHODS = ("autorig", "m2m")     # m2m: Mesh2Motion's own skinning (core/m2m_skin.py, core/m2m_skin_blender.py)

# quadruped: Horse or Fox by the tail. tail_reach is how far the body reaches back past its hind feet, over the
# distance between the front and hind feet: a fox's, dog's or cat's tail is carried out behind it, a horse's, cow's
# or goat's hangs. Measured at height 1 on the box's Tripo animals (2026-10-18): horse 0.37, cows 0.30-0.41, goat
# 0.46, sheep 0.20, pig 0.28; dogs 0.70 and 0.71, cat 0.80, rabbit 0.69 (the templates' own meshes: horse 0.77, fox
# 1.23). Leg length over the body does not separate them (fox 0.64, horse 0.63, the Tripo horse 0.65, dogs
# 0.48-0.51), nor does the head's reach forward. Absolute height cannot either: Tripo's meshes come out about 1 m
# whatever the animal. A real height (the caller's --height, or the card's) only breaks a tail reach inside the
# band, standing this tall or more being a horse's size.
FOX_TAIL = 0.55
TAIL_BAND = (0.48, 0.62)
HORSE_HEIGHT = 0.9
# a body whose underside lies on the ground over this share of its footprint has no legs to stand on: a snake if
# it is this slender (length over width and height), a fish otherwise
LYING = 0.3
SERPENT_SLENDER = 5.0
# a biped with a tail longer than this share of its height, reaching back behind it, is a kaiju, not a person
KAIJU_TAIL = 0.3
# a tail this long (tail_share, past a person's depth over the height) on a body with five ground patches is the
# fifth patch: its tip on the floor (the spiders and crabs measured 0.28-0.64 with six or more patches, or fewer)
TAIL_ON_GROUND = 0.5

# archetypes (core/suggest.py, plus a few names a caller may pass) with no Mesh2Motion template: these keep the
# old path (rerig.py and make_clips.py)
NO_TEMPLATE = ("floater", "rigid", "turret", "machine", "prop", "creature")


def is_root_motion(name):
    """Mesh2Motion keeps a clip's root travel when its name marks it root motion: "Walk RM", "Roll_RM". The app tests
    `name.toLowerCase().endsWith('rm')`, which also caught any name ending in the letters (a "Swarm", a "Storm"); only
    the separate RM suffix counts here."""
    return isinstance(name, str) and (name.endswith(" RM") or name.endswith("_RM"))


# What the body plan (core/body_plan.py) says, read before the archetype is trusted. Calibrated on the committed rigs
# of Tornado Titans, Heartroot and Smeltdown (tests/data/route_truth.json, cli/route_eval.py, 2026-10-18).
# BUILT: a share of the surface on flat faces square to the axes at or over this is a built thing (a building, a
# vehicle, a machine, a sign): sculpted animals and people stay under 0.14 (one hound reaches 0.21), props have a
# median of 0.38. Four legs with air under the belly (BELLY_LEGS) keep it a creature up to BUILT_LEGS.
BUILT = 0.15
BUILT_LEGS = 0.3
BELLY_LEGS = (0.25, 0.5)     # an animal's belly clears 0.2-0.41 of its height; a table or a carport on posts more
# On one patch of ground or none, neither long (the plan's length, front to back over side to side) nor slender, and
# not standing as tall as a person: a tree, a plant, a jelly, a drone, a crystal, a rock. Long footless bodies are
# fish and serpents; tall ones on one patch are robed people.
BASE_LONG = 1.6
BASE_SLENDER = 1.15
BASE_TALL = 2.6
# A person stands at least this tall over their depth (a T-posed robot 1.5; the tallest bird, a rooster, 1.14), with
# less tail than PERSON_TAIL (birds 0.15 and up). On anything but two feet (robes, tentacles), a person stands taller
# still, PERSON_ROBED, with little air under the middle (BELLY_HANGING: a jelly's tentacles hang, a robe reaches down).
PERSON_STAND = 1.4
PERSON_TAIL = 0.12
PERSON_ROBED = 2.0
BELLY_HANGING = 0.25
# Two feet with this much air under the body (belly, over the height) stand on legs: a bird (a hen 0.19, a rooster
# 0.25). Less: a fish resting on its fins (0.05-0.16).
BIRD_BELLY = 0.17
# A footless long body standing this tall over its length or more is a serpent rearing or coiling; lower, a fish.
SERPENT_STAND = 0.7


def plan_measures(plan):
    """The body plan's measures choose() reads, from core/body_plan.measure (none for a missing plan)."""
    if not isinstance(plan, dict) or plan.get("error"): return {}
    out = {}
    feet = plan.get("feet") or {}
    if feet.get("count") is not None: out["feet"] = feet["count"]
    if feet.get("fill") is not None: out["feet_fill"] = feet["fill"]
    if (plan.get("wings") or {}).get("both"): out["wings"] = True
    if (plan.get("flat") or {}).get("share") is not None: out["flat"] = plan["flat"]["share"]
    if (plan.get("belly") or {}).get("clear") is not None: out["belly"] = plan["belly"]["clear"]
    if plan.get("length") is not None: out["length"] = plan["length"]
    return out


def is_prop(m):
    """(True, why) when the body plan says it is a prop, not a creature or a person: built (flat faces), or standing
    on one base. Without the plan's measures (plan_measures), (False, "")."""
    flat, feet, belly = m.get("flat"), m.get("feet"), m.get("belly")
    if flat is None or feet is None: return False, ""
    legs = feet == 4 and BELLY_LEGS[0] <= (belly or 0) <= BELLY_LEGS[1]
    if flat >= BUILT and not (legs and flat < BUILT_LEGS):
        return True, "built: %.0f%% of its surface on flat faces square to the axes" % (100 * flat)
    if feet <= 1 and (m.get("length") or 0) < BASE_LONG and (m.get("slender") or 0) < BASE_SLENDER \
            and (m.get("stand") or 0) < BASE_TALL:
        return True, "stands on %s, neither long nor tall: a plant, a floater or an object" % (
            "one base" if feet == 1 else "nothing")
    return False, ""


def choose(archetype, measures=None):
    """(template, reason) for an archetype, or (None, reason) when Mesh2Motion has none for it. measures, all optional:
       tail_reach  how far it reaches back past its hind feet, over the distance between the feet (quadrupeds:
                   Fox from FOX_TAIL, Horse under it)
       metres      its standing height, only when the height is real (the caller's): breaks a tail reach in the band
       feet        feet touching the ground (core/body_plan.py)
       ground      the share of its footprint lying on the ground (m2m_rig.body_measures)
       slender     length over the larger of width and height
       stand       height over length
       tail        how far it reaches back past a person's depth, over its height (a kaiju's tail)
       wings       True when wings were found
       flat, belly, feet_fill, length   the body plan's (plan_measures)
    The body's measures overrule the archetype where they plainly disagree: a built thing is a prop whatever its tips
    look like, a body lying on the ground is a snake or a fish (the sea serpent's tips read "rigid"), and two feet are
    not a quadruped's. (None, reason) for a prop sends it to the classic rig."""
    m = measures or {}
    a = (archetype or "").lower()
    why = ""
    prop, reason = is_prop(m)
    if prop:
        return None, "%s: the classic rig (was %s)" % (reason, a or "unknown")
    feet, stand, belly = m.get("feet"), m.get("stand") or 0, m.get("belly")
    if feet is not None and feet <= 1 and (m.get("length") or 0) >= BASE_LONG and a not in ("winged", "bird", "dragon"):
        if stand >= SERPENT_STAND or (m.get("slender") or 0) >= SERPENT_SLENDER:
            return "snake", "no feet, long, standing %.2f of its length: Snake (was %s)" % (stand, a or "unknown")
        return "shark", "no feet, long and low: Shark (was %s)" % (a or "unknown")
    # a body on three or more feet (a hare sitting, its big hind feet flat) is not lying down, however much of its
    # footprint touches
    if (m.get("ground") or 0) >= LYING and a not in ("humanoid", "biped", "human") and (feet is None or feet <= 2):
        if (m.get("slender") or 0) >= SERPENT_SLENDER:
            return "snake", "lies on the ground (%.0f%% of its footprint), slender %.1f: Snake (was %s)" % (
                100 * m["ground"], m["slender"], a or "unknown")
        return "shark", "lies on the ground (%.0f%% of its footprint), not slender: Shark (was %s)" % (
            100 * m["ground"], a or "unknown")
    upright = a in ("humanoid", "biped", "human")
    if upright and feet is not None and feet != 2 and (stand < PERSON_ROBED or (belly or 0) >= BELLY_HANGING):
        return None, ("on %d contact patches, standing %.1f with %.2f of its height clear under the middle: no "
                      "person, a floater or an object: the classic rig (was %s)" % (feet, stand, belly or 0, a))
    if upright and feet == 2 and stand < PERSON_STAND and belly is not None and belly >= BIRD_BELLY:
        why = " (two feet with air under a low body, %.2f of its height: was %s)" % (belly, a); a = "winged"
    elif a in ("winged", "bird") and feet == 2 and stand >= PERSON_STAND and (m.get("tail") or 0) < PERSON_TAIL:
        return "human", "two feet under an upright body (%.1f) with no tail: arms held out, not wings: Human" % stand
    elif a in ("hexapod", "octopod") and stand >= PERSON_ROBED:
        return "human", "many contact patches under a body standing %.1f, as tall as a person: Human (was %s)" % (
            stand, a)
    elif m.get("wings") and a not in ("winged", "bird", "dragon"):
        why = " (wings: was %s)" % a; a = "winged"
    elif a in ("quadruped", "", "rigid", "creature") and feet == 2:
        if stand >= 1.6:
            why = " (two feet under an upright body: was %s)" % (a or "unknown"); a = "humanoid"
        elif stand < 0.5 or (a == "quadruped" and belly is not None and belly < BIRD_BELLY):
            why = " (low on two contact patches with little air under it, a body resting on fins: was %s)" % (
                a or "unknown")
            a = "swimmer"
        else:
            why = " (two feet under a long body: was %s)" % (a or "unknown"); a = "winged"
    elif a in ("", "rigid", "creature") and feet == 5 and (m.get("tail") or 0) >= TAIL_ON_GROUND             and belly is not None and BELLY_LEGS[0] <= belly <= BELLY_LEGS[1]:
        # four feet and a tail lying on the ground behind them, with an animal's air under the belly: Heartroot's
        # thornmother is a four-legged beast whose tail tip rests on the floor, and its fifth patch made it a spider
        why = " (five ground patches, a long tail, %.2f of its height under the belly: four feet and its tail: was %s)" % (
            belly, a or "unknown")
        a = "quadruped"
    elif a in ("", "rigid", "creature") and (feet or 0) >= 5:
        a = "octopod"
    elif a in ("", "rigid", "creature") and feet == 4:
        a = "quadruped"
    t, reason = _choose(a, m)
    return t, reason + why


def is_person(template, archetype=None, measures=None):
    """Whether Auto sends it to the humanoid rig: choose() picked the human template. A T-pose's arms and coat tails
    that read as wings are choose()'s to see (two feet, upright, no tail), so a built thing it calls a prop stays one."""
    return template == "human"


def _choose(a, m):
    if a in ("humanoid", "biped", "human"):
        if (m.get("tail") or 0) > KAIJU_TAIL:
            return "kaiju", "a biped with a tail %.2f of its height: Kaiju" % m["tail"]
        return "human", "humanoid: Human"
    if a in ("quadruped", "canine", "feline", "equine", "bovine"):
        r, h = m.get("tail_reach"), m.get("metres")
        if r is not None and h is not None and TAIL_BAND[0] <= r <= TAIL_BAND[1]:
            t = "horse" if h >= HORSE_HEIGHT else "fox"
            return t, "quadruped, tail reach %.2f (between %.2f and %.2f), %.2f m tall (Horse from %.1f m): %s" % (
                r, TAIL_BAND[0], TAIL_BAND[1], h, HORSE_HEIGHT, t.capitalize())
        if r is not None:
            if r >= FOX_TAIL:
                return "fox", "quadruped carrying its tail out behind (reach %.2f, Fox from %.2f): Fox" % (r, FOX_TAIL)
            return "horse", "quadruped with a hanging tail (reach %.2f, under %.2f): Horse" % (r, FOX_TAIL)
        if h is not None:
            t = "horse" if h >= HORSE_HEIGHT else "fox"
            return t, "quadruped %.2f m tall (Horse from %.1f m): %s" % (h, HORSE_HEIGHT, t.capitalize())
        return "horse", "quadruped, shape and size unknown: Horse"
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
    cands = [given, os.path.join(here, "third_party", "mesh2motion-app"), os.environ.get("AUTORIG_M2M"),
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
