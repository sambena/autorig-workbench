# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the standard skeletons (SKELETONS.md) as data: which archetype a rig is, and which of its bones plays which role.
#
# rerig.py and rerig_humanoid.py write describe()'s result into each model's QA log; publish.py copies it into the
# card (model.json, "rig" > "skeleton"); make_clips.py and any engine importer read it there. A reader asks "the head", "the
# left legs, front to back", "the tail", and gets bone names. It never parses a name to guess a role.

import re

ARCHETYPES = ("humanoid", "quadruped", "hexapod", "octopod", "serpent", "winged", "floater", "rigid")

# Unity HumanBodyBones name -> our bone name (Mixamo's, without the namespace). The humanoid "roles" are these.
HUMANOID = {
    "Hips": "Hips", "Spine": "Spine", "Chest": "Spine1", "UpperChest": "Spine2", "Neck": "Neck", "Head": "Head",
    "LeftShoulder": "LeftShoulder", "LeftUpperArm": "LeftArm", "LeftLowerArm": "LeftForeArm", "LeftHand": "LeftHand",
    "RightShoulder": "RightShoulder", "RightUpperArm": "RightArm", "RightLowerArm": "RightForeArm",
    "RightHand": "RightHand",
    "LeftUpperLeg": "LeftUpLeg", "LeftLowerLeg": "LeftLeg", "LeftFoot": "LeftFoot", "LeftToes": "LeftToeBase",
    "RightUpperLeg": "RightUpLeg", "RightLowerLeg": "RightLeg", "RightFoot": "RightFoot", "RightToes": "RightToeBase",
    "Left Index Proximal": "LeftHandIndex1", "Left Index Intermediate": "LeftHandIndex2",
    "Left Index Distal": "LeftHandIndex3",
    "Right Index Proximal": "RightHandIndex1", "Right Index Intermediate": "RightHandIndex2",
    "Right Index Distal": "RightHandIndex3",
}

# chain roles (as rerig names them) -> the role list they are reported under
GROUP = {"leg": "legs", "arm": "arms", "wing": "wings", "tail": "tail", "mandible": "mandibles", "jaw": "jaw",
         "ear": "antennae", "antenna": "antennae", "tentacle": "tentacles", "tendril": "tentacles",
         "claw": "claws", "fin": "fins", "flipper": "fins", "fluke": "fins", "pod": "pods", "abdomen": "abdomen",
         # trailing chains: they follow the body's motion a beat late
         "streamer": "tentacles", "oral": "tentacles", "wisp": "tentacles", "flame": "tentacles", "lure": "tentacles",
         "barbel": "tentacles"}


def side_of(name):
    if name.endswith(".L") or name.startswith("Left"): return "L"
    if name.endswith(".R") or name.startswith("Right"): return "R"
    return ""


def describe(chains, archetype):
    """Roles to bones, from the chains a rig was built from (the rig's own record, not its names)."""
    if archetype == "humanoid":
        names = {b for c in chains for b in c["bones"]}
        return {"archetype": "humanoid", "root": "root",
                "humanoid": {k: v for k, v in HUMANOID.items() if v in names}}
    out = {"archetype": archetype or "creature", "root": "root"}
    spine = chains[0]["bones"]
    out["body"] = spine[0]
    torso = [b for b in spine if b != "head"]
    out["spine"] = torso
    for c in chains:
        if c["role"] in ("head", "neck") or "head" in c["bones"]:
            for b in c["bones"]:
                if b == "head": out["head"] = b
                elif b.startswith("neck"): out.setdefault("neck", []).append(b)
    groups = {}
    for ci, c in enumerate(chains):
        if ci == 0: continue
        role = c["role"].split("_")[0]
        g = GROUP.get(role, "extras")
        bones = list(c["bones"])
        entry = {"name": c.get("base", role) + (("." + side_of(bones[0])) if side_of(bones[0]) else ""),
                 "side": side_of(bones[0]), "bones": bones}
        if c.get("girdle"): entry["girdle"] = bones[0]; entry["bones"] = bones[1:]
        if c.get("ik") and len(bones) >= 2:
            entry["ik"] = "ik_" + c.get("base", "") + c.get("side", "")
            entry["pole"] = "pole_" + c.get("base", "") + c.get("side", "")
        if c["parent"] is not None:
            pc, pi = c["parent"]; entry["parent"] = chains[pc]["bones"][pi]
        if role == "leg":
            entry["foot"] = bones[-1]
            entry["along"] = round(float(-c["points"][-1].y), 4)   # how far forward its foot stands (-Y is forward)
        groups.setdefault(g, []).append(entry)
    if "legs" in groups:  # front to back, left before right: pairs line up
        groups["legs"].sort(key=lambda e: (-e["along"], e["side"] != "L"))
        for e in groups["legs"]: e.pop("along")
    for g, v in groups.items():
        if g in ("tail", "abdomen", "jaw"):
            out[g] = [b for e in v for b in e["bones"]] if g != "jaw" else v[0]["bones"][0]
        else:
            out[g] = v
    return out


# ---------------------------------------------------------------------------------------------------------------
# Universal Semantic Bone Dictionary & Skeleton Auto-Mapping Engine
# ---------------------------------------------------------------------------------------------------------------

# Prefix patterns to strip when analyzing bone names across DCCs and game engines
PREFIX_RE = re.compile(r'^(mixamorig:|cc_base_|valvebiped\.|bip\d*[\s_]|def[-_]|org[-_]|mch[-_])+', re.IGNORECASE)
# a bone name that says which side it is on (left..., l_..., "l ...", ..._l, ....l)
SIDED_RE = re.compile(r'^(left|right|[lr][\s_])|[._][lr]$', re.IGNORECASE)

# Signatures for major armature conventions
CONVENTIONS = {
    "unreal": {
        "pelvis", "spine_01", "spine_02", "spine_03", "neck_01", "neck_02", "head",
        "clavicle_l", "clavicle_r", "upperarm_l", "upperarm_r", "lowerarm_l", "lowerarm_r",
        "hand_l", "hand_r", "thigh_l", "thigh_r", "calf_l", "calf_r", "foot_l", "foot_r", "ball_l", "ball_r"
    },
    "unity": {
        "hips", "spine", "chest", "upperchest", "neck", "head", "jaw",
        "leftshoulder", "rightshoulder", "leftupperarm", "rightupperarm",
        "leftlowerarm", "rightlowerarm", "lefthand", "righthand",
        "leftupperleg", "rightupperleg", "leftlowerleg", "rightlowerleg",
        "leftfoot", "rightfoot", "lefttoes", "righttoes"
    },
    "mixamo": {
        "hips", "spine", "spine1", "spine2", "neck", "head",
        "leftshoulder", "rightshoulder", "leftarm", "rightarm",
        "leftforearm", "rightforearm", "lefthand", "righthand",
        "leftupleg", "rightupleg", "leftleg", "rightleg",
        "leftfoot", "rightfoot", "lefttoebase", "righttoebase"
    },
    "biped": {
        "pelvis", "spine", "spine1", "spine2", "neck", "head",
        "l clavicle", "r clavicle", "l upperarm", "r upperarm",
        "l forearm", "r forearm", "l hand", "r hand",
        "l thigh", "r thigh", "l calf", "r calf",
        "l foot", "r foot", "l toe0", "r toe0"
    },
    "accurig": {
        "pelvis", "waist", "spine01", "spine02", "necktwist01", "head",
        "l_clavicle", "r_clavicle", "l_upperarm", "r_upperarm",
        "l_forearm", "r_forearm", "l_hand", "r_hand",
        "l_thigh", "r_thigh", "l_calf", "r_calf",
        "l_foot", "r_foot", "l_toebase", "r_toebase"
    },
    "rigify": {
        "spine", "spine.001", "spine.002", "spine.003", "neck", "head",
        "shoulder.l", "shoulder.r", "upper_arm.l", "upper_arm.r",
        "forearm.l", "forearm.r", "hand.l", "hand.r",
        "thigh.l", "thigh.r", "shin.l", "shin.r",
        "foot.l", "foot.r", "toe.l", "toe.r"
    },
    "valve": {
        "bip01_pelvis", "bip01_spine", "bip01_spine1", "bip01_spine2", "bip01_neck1", "bip01_head1",
        "bip01_l_clavicle", "bip01_r_clavicle", "bip01_l_upperarm", "bip01_r_upperarm",
        "bip01_l_forearm", "bip01_r_forearm", "bip01_l_hand", "bip01_r_hand",
        "bip01_l_thigh", "bip01_r_thigh", "bip01_l_calf", "bip01_r_calf",
        "bip01_l_foot", "bip01_r_foot", "bip01_l_toe0", "bip01_r_toe0"
    }
}

CORE_ROLES = {
    # Pelvis / Hips / Root
    "hips": "hips", "pelvis": "hips", "root": "hips", "waist": "hips", "body": "hips",
    # Spine levels
    "spine": "spine", "spine01": "spine", "spine001": "spine", "lowerbody": "spine",
    "spine1": "spine1", "spine02": "spine1", "chest": "spine1", "spine002": "spine1", "midbody": "spine1",
    "spine2": "spine2", "spine03": "spine2", "spine3": "spine2", "upperchest": "spine2", "spine003": "spine2", "thorax": "spine2",
    # Neck / Head / Face
    "neck": "neck", "neck01": "neck", "neck1": "neck", "necktwist01": "neck", "spine004": "neck",
    "head": "head", "head1": "head", "spine005": "head", "spine006": "head", "skull": "head",
    "jaw": "jaw", "jawroot": "jaw", "mandible": "jaw", "chin": "jaw",
    # Shoulder / Clavicle / Girdle
    "clavicle": "shoulder", "shoulder": "shoulder", "scapula": "shoulder", "collar": "shoulder",
    # Arm
    "upperarm": "arm", "arm": "arm", "humerus": "arm", "bicep": "arm",
    "lowerarm": "forearm", "forearm": "forearm", "radius": "forearm", "ulna": "forearm", "elbow": "forearm",
    "hand": "hand", "wrist": "hand", "palm": "hand",
    # Leg
    "thigh": "thigh", "upperleg": "thigh", "femur": "thigh", "upleg": "thigh", "hipjoint": "thigh",
    "calf": "shin", "shin": "shin", "lowerleg": "shin", "leg": "shin", "tibia": "shin", "knee": "shin",
    "foot": "foot", "ankle": "foot",
    "toebase": "toe", "toe": "toe", "toes": "toe", "ball": "toe", "toe0": "toe",
    "toeend": "toetip", "toesend": "toetip", "toebaseend": "toetip",
}


def detect_convention(bone_names):
    """Identifies the armature naming convention from a collection of bone names.
    Returns: (convention_name, confidence)
    where convention_name is in ('unreal', 'unity', 'mixamo', 'rigify', 'biped', 'accurig', 'valve', 'tripo', 'other', 'none')."""
    if not bone_names:
        return "none", 0.0
    if all(re.match(r"^bone_\d+", n) for n in bone_names):
        return "tripo", 1.0
    if any(n.lower().startswith("mixamorig:") for n in bone_names):
        return "mixamo", 1.0
    if any("valvebiped" in n.lower() for n in bone_names):
        return "valve", 1.0

    scores = {}
    total = len(bone_names)
    for conv, key_set in CONVENTIONS.items():
        matches = sided = 0
        for n in bone_names:
            clean = PREFIX_RE.sub("", n.strip()).lower()
            if clean in key_set or n.lower() in key_set:
                matches += 1
                if SIDED_RE.search(clean):
                    sided += 1
        if matches > 0:
            scores[conv] = (matches, sided)

    if not scores:
        return "other", 0.0
    # most matches first; a tie goes to the convention with more left/right limb names, then the smaller set (the
    # more specific one), never to whichever the table lists first
    best_conv, (best_count, best_sided) = max(scores.items(),
                                              key=lambda kv: (kv[1][0], kv[1][1], -len(CONVENTIONS[kv[0]])))
    ratio = best_count / min(len(CONVENTIONS[best_conv]), max(1, total))
    confidence = min(1.0, round(ratio, 2))
    # Generic names alone (hips, spine, neck, head: any creature's) say nothing about the convention: without at
    # least two of its left/right limb names it stays under the 0.25 that survey and suggest route on
    if best_sided < 2:
        confidence = min(confidence, 0.2)
    return best_conv, confidence


def map_bone_to_canonical(raw_name):
    """Maps an arbitrary DCC/game-engine bone name to a canonical role name.
    Canonical names format: 'role' (for central bones) or 'role.L' / 'role.R' (for limbs/lateral bones).
    Examples:
      'thigh_l' -> 'thigh.L'
      'DEF-upper_arm.R' -> 'arm.R'
      'Bip01 L Forearm' -> 'forearm.L'
      'LeftIndexProximal' -> 'index1.L'
      'spine_01' -> 'spine'
    """
    n = raw_name.strip()
    n = PREFIX_RE.sub("", n)

    side = ""
    # Side prefix check: Left/Right, L_/R_, L /R 
    m_pref = re.match(r"^(left|right|l[\s_]|r[\s_])", n, re.IGNORECASE)
    if m_pref:
        pref = m_pref.group(1).lower()
        side = "L" if pref.startswith("l") else "R"
        n = n[m_pref.end():]
    else:
        # Side suffix check: .L, .R, _l, _r, -l, -r, _left, _right
        m_suf = re.search(r"([._-](l|r)|[._-](left|right))$", n, re.IGNORECASE)
        if m_suf:
            suf = m_suf.group(1).lower()
            side = "L" if "l" in suf else "R"
            n = n[:m_suf.start()]

    # Inner side check (e.g. arm.L_1 or leg_r_2)
    m_inner = re.search(r"[._-]([lr]|left|right)[._-]", n, re.IGNORECASE)
    if m_inner:
        if not side:
            inner_suf = m_inner.group(1).lower()
            side = "L" if "l" in inner_suf else "R"
        n = n[:m_inner.start()] + "_" + n[m_inner.end():]

    core = re.sub(r"[^a-zA-Z0-9]", "", n).lower()

    # Limb chain index check: e.g. arm1, arm2, leg1, leg2, leg3 (Autorig placed chains)
    m_limb = re.search(r"^(arm|leg|wing)[_.]*(\d+)$", core)
    if m_limb:
        limb = m_limb.group(1)
        idx = int(m_limb.group(2))
        if limb == "arm":
            role = {1: "arm", 2: "forearm", 3: "hand"}.get(idx, f"arm_{idx}")
        elif limb == "leg":
            role = {1: "thigh", 2: "shin", 3: "foot", 4: "toe"}.get(idx, f"leg_{idx}")
        elif limb == "wing":
            role = f"wing_{idx}"
        return f"{role}.{side}" if side else role

    # Finger check: numbered index (e.g. index1, thumb_02, CC_Base_L_Mid1)
    m_f = re.search(r"(thumb|index|mid|middle|ring|pinky|little)[_.]*(\d+)", core)
    if m_f:
        finger = m_f.group(1)
        idx = int(m_f.group(2))
        f_norm = {"thumb": "thumb", "index": "index", "mid": "middle", "middle": "middle",
                  "ring": "ring", "pinky": "pinky", "little": "pinky"}[finger]
        role = f"{f_norm}{idx}"
        return f"{role}.{side}" if side else role

    # Unity-style fingers: Proximal (1), Intermediate (2), Distal (3)
    m_f2 = re.search(r"(thumb|index|mid|middle|ring|pinky|little)(proximal|intermediate|distal)", core)
    if m_f2:
        finger = m_f2.group(1)
        digit = {"proximal": 1, "intermediate": 2, "distal": 3}[m_f2.group(2)]
        f_norm = {"thumb": "thumb", "index": "index", "mid": "middle", "middle": "middle",
                  "ring": "ring", "pinky": "pinky", "little": "pinky"}[finger]
        role = f"{f_norm}{digit}"
        return f"{role}.{side}" if side else role

    # Tail check: tail_1, tail_2...
    m_t = re.search(r"tail[_.]*(\d+)", core)
    if m_t:
        return f"tail_{int(m_t.group(1))}"

    # Core anatomical role
    canon = CORE_ROLES.get(core)
    if canon:
        return f"{canon}.{side}" if side else canon

    return None


def extract_chains_from_joints(joints, lo=None, hi=None, size=None):
    """Reconstructs standard rig chains from existing source armature joints.
    Retains 1:1 fidelity with the source skeleton, extracting exact 3D station coordinates.
    joints: list of dicts with 'name', 'head', 'tail', 'parent'
    lo, hi, size: optional bounding box coordinates for 0..1 normalization.
    Returns: (chains, role_to_joint, mapped_count)"""
    if not joints:
        return [], {}, 0

    all_heads = [j["head"] for j in joints if "head" in j]
    if not all_heads:
        return [], {}, 0

    if lo is None or size is None:
        lo = [min(h[i] for h in all_heads) for i in range(3)]
        hi = [max(h[i] for h in all_heads) for i in range(3)]
        size = [max(1e-5, hi[i] - lo[i]) for i in range(3)]

    def to_u(pt):
        return [round((pt[i] - lo[i]) / size[i], 3) for i in range(3)]

    role_to_joint = {}
    for j in joints:
        c_name = map_bone_to_canonical(j["name"])
        if c_name and c_name not in role_to_joint:
            role_to_joint[c_name] = j

    chains = []

    # 1. Spine / Centerline chain
    spine_roles = ["hips", "spine", "spine1", "spine2", "neck", "head"]
    spine_pts = [to_u(role_to_joint[r]["head"]) for r in spine_roles if r in role_to_joint]
    if len(spine_pts) >= 2:
        # If head has a valid tail coordinate, append it as the top of head station
        if "head" in role_to_joint and "tail" in role_to_joint["head"]:
            head_tail = to_u(role_to_joint["head"]["tail"])
            if (head_tail[0] != spine_pts[-1][0] or head_tail[1] != spine_pts[-1][1] or head_tail[2] != spine_pts[-1][2]):
                spine_pts.append(head_tail)

        chains.append({
            "name": "spine",
            "role": "spine",
            "points": spine_pts,
            "bones": len(spine_pts) - 1,
            "parent": None
        })

    # Find chest or neck index on spine to parent arms
    chest_idx = 1
    if "spine" in [c["name"] for c in chains]:
        sp_len = len(chains[0]["points"])
        chest_idx = max(1, sp_len - 2)

    # 2. Arms (Left & Right)
    for side in ("L", "R"):
        arm_roles = [f"shoulder.{side}", f"arm.{side}", f"forearm.{side}", f"hand.{side}"]
        arm_pts = [to_u(role_to_joint[r]["head"]) for r in arm_roles if r in role_to_joint]
        if len(arm_pts) >= 2:
            has_girdle = f"shoulder.{side}" in role_to_joint
            chains.append({
                "name": f"arm.{side}",
                "role": "arm",
                "points": arm_pts,
                "bones": len(arm_pts) - 1,
                "girdle": has_girdle,
                "parent": ["spine", chest_idx]
            })

    # 3. Legs (Left & Right)
    for side in ("L", "R"):
        leg_roles = [f"thigh.{side}", f"shin.{side}", f"foot.{side}", f"toe.{side}"]
        leg_pts = [to_u(role_to_joint[r]["head"]) for r in leg_roles if r in role_to_joint]
        tip_role = f"toetip.{side}"
        if tip_role in role_to_joint and "head" in role_to_joint[tip_role]:
            leg_pts.append(to_u(role_to_joint[tip_role]["head"]))
        elif f"toe.{side}" in role_to_joint and "tail" in role_to_joint[f"toe.{side}"]:
            t_coord = to_u(role_to_joint[f"toe.{side}"]["tail"])
            if leg_pts and t_coord != leg_pts[-1]:
                leg_pts.append(t_coord)
        if len(leg_pts) >= 2:
            chains.append({
                "name": f"leg.{side}",
                "role": "leg",
                "points": leg_pts,
                "bones": len(leg_pts) - 1,
                "ik": True,
                "parent": ["spine", 0]
            })

    # 4. Fingers (Left & Right)
    for side in ("L", "R"):
        arm_name = f"arm.{side}"
        for f_type in ("thumb", "index", "middle", "ring", "pinky"):
            f_roles = [f"{f_type}1.{side}", f"{f_type}2.{side}", f"{f_type}3.{side}"]
            if f"{f_type}4.{side}" in role_to_joint:
                f_roles.append(f"{f_type}4.{side}")
            f_pts = [to_u(role_to_joint[r]["head"]) for r in f_roles if r in role_to_joint]
            if len(f_pts) >= 2:
                chains.append({
                    "name": f"f_{f_type}.{side}",
                    "role": "finger",
                    "points": f_pts,
                    "bones": len(f_pts) - 1,
                    "parent": [arm_name, -1]
                })

    # 5. Tail (if present)
    tail_roles = sorted([r for r in role_to_joint if r.startswith("tail_")],
                        key=lambda r: int(r.split("_")[1]) if r.split("_")[1].isdigit() else 0)
    if len(tail_roles) >= 2:
        tail_pts = [to_u(role_to_joint[r]["head"]) for r in tail_roles]
        chains.append({
            "name": "tail",
            "role": "tail",
            "points": tail_pts,
            "bones": len(tail_pts) - 1,
            "medial": True,
            "parent": ["spine", 0]
        })

    return chains, role_to_joint, len(role_to_joint)


def suggest_from_known_skeleton(joints, convention, lo=None, hi=None, size=None):
    """Constructs a complete placed rig spec from a recognized source skeleton hierarchy.
    Extracts 1:1 joint positions and builds canonical chains for spine, limbs, and fingers.
    Returns: suggest dictionary with 'archetype', 'confidence', 'reasons', 'rig', and 'spec'."""
    chains, role_to_joint, mapped_count = extract_chains_from_joints(joints, lo=lo, hi=hi, size=size)

    has_arms = any(c["role"] == "arm" for c in chains)
    has_legs = any(c["role"] == "leg" for c in chains)
    archetype = "humanoid" if (has_arms and has_legs) else ("quadruped" if len([c for c in chains if c["role"] == "leg"]) >= 4 else "creature")

    rig_spec = {
        "kind": "placed",
        "forward": [0, -1, 0],
        "skeleton": archetype,
        "chains": chains,
        "clips": {"archetype": "walker" if archetype == "humanoid" else "quadruped"}
    }

    reasons = [
        f"Detected {convention.capitalize()} armature with {len(joints)} joints",
        f"Mapped {mapped_count} bones to canonical chains with 1:1 joint retention"
    ]

    res = {
        "archetype": archetype,
        "confidence": "high",
        "reasons": reasons,
        "rig": rig_spec,
        "spec": {
            "schema": "autorig-spec/1",
            "rig": rig_spec
        }
    }

    # If humanoid, also provide calibrated humanoid parameters
    if archetype == "humanoid" and "hips" in role_to_joint and "head" in role_to_joint:
        all_heads = [j["head"] for j in joints if "head" in j]
        if lo is None or size is None:
            lo = [min(h[i] for h in all_heads) for i in range(3)]
            hi = [max(h[i] for h in all_heads) for i in range(3)]
            size = [max(1e-5, hi[i] - lo[i]) for i in range(3)]
        def to_u(pt): return [round((pt[i] - lo[i]) / size[i], 3) for i in range(3)]

        def get_z(roles, default_val):
            for r in roles:
                if r in role_to_joint and "head" in role_to_joint[r]:
                    return round(to_u(role_to_joint[r]["head"])[2], 3)
            return default_val

        # Extract calibrated heights
        ankle_z = get_z(["foot.L", "foot.R", "toe.L", "toe.R"], 0.08)
        knee_z = get_z(["shin.L", "shin.R"], 0.28)
        hip_z = get_z(["hips"], 0.52)
        spine_z = get_z(["spine"], 0.57)
        spine1_z = get_z(["spine1"], 0.65)
        spine2_z = get_z(["spine2"], 0.72)
        arm_z = get_z(["shoulder.L", "shoulder.R", "arm.L", "arm.R"], 0.77)
        neck_z = get_z(["neck"], 0.83)
        head_z = get_z(["head"], 0.92)

        # Enforce monotonic z ordering and range clamping [0.01, 1.0]
        ankle_z = max(0.01, min(0.20, ankle_z))
        knee_z = max(ankle_z + 0.05, min(0.45, knee_z))
        hip_z = max(knee_z + 0.05, min(0.60, hip_z))
        spine_z = max(hip_z + 0.02, min(0.70, spine_z))
        spine1_z = max(spine_z + 0.02, min(0.78, spine1_z))
        spine2_z = max(spine1_z + 0.02, min(0.85, spine2_z))
        arm_z = max(spine2_z, min(0.88, arm_z))
        neck_z = max(arm_z + 0.02, min(0.92, neck_z))
        head_z = max(neck_z + 0.02, min(0.98, head_z))

        z_params = {
            "top": 1.0,
            "head": round(head_z, 3),
            "neck": round(neck_z, 3),
            "arm": round(arm_z, 3),
            "spine2": round(spine2_z, 3),
            "spine1": round(spine1_z, 3),
            "spine": round(spine_z, 3),
            "hip": round(hip_z, 3),
            "knee": round(knee_z, 3),
            "ankle": round(ankle_z, 3)
        }

        def get_span(roles, default_val):
            for r in roles:
                if r in role_to_joint and "head" in role_to_joint[r]:
                    x_coord = to_u(role_to_joint[r]["head"])[0]
                    # Inward distance from outer lateral edge (0.0 = outer edge, 0.5 = centerline)
                    return max(0.0, min(0.49, 0.5 - abs(x_coord - 0.5)))
            return default_val

        raw_sh = get_span(["shoulder.L", "shoulder.R", "arm.L", "arm.R"], 0.38)
        raw_el = get_span(["forearm.L", "forearm.R"], 0.23)
        raw_wr = get_span(["hand.L", "hand.R"], 0.11)
        raw_kn = 0.05
        raw_tip = 0.0

        # Monotonically order spans: 0.0 <= tip <= knuckle <= wrist <= elbow <= shoulder <= 0.49
        sh = max(0.20, min(0.49, raw_sh))
        el = max(0.10, min(sh - 0.02, raw_el))
        wr = max(0.04, min(el - 0.02, raw_wr))
        kn = max(0.01, min(wr - 0.01, raw_kn))
        tip = max(0.0, min(kn - 0.005, raw_tip))

        x_params = {
            "shoulder": round(sh, 3),
            "elbow": round(el, 3),
            "wrist": round(wr, 3),
            "knuckle": round(kn, 3),
            "tip": round(tip, 3)
        }
        res["spec"]["humanoid"] = {
            "forward": [0, -1, 0],
            "z": z_params,
            "x": x_params
        }

    return res
