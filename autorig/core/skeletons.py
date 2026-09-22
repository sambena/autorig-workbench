# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the standard skeletons (SKELETONS.md) as data: which archetype a rig is, and which of its bones plays which role.
#
# rerig.py and rerig_humanoid.py write describe()'s result into each model's QA log; publish.py copies it into the
# card (model.json, "rig" > "skeleton"); make_clips.py and any engine importer read it there. A reader asks "the head", "the
# left legs, front to back", "the tail", and gets bone names. It never parses a name to guess a role.

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
        if c.get("ik") and len(bones) >= 2: entry["ik"] = "ik_" + c.get("base", "") + c.get("side", "")
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
