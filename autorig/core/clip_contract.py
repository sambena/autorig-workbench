# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: what every clips manifest tells an engine about each clip (docs/FORMATS.md, "The engine
# contract"), so a game plays a clip the way it was authored without a name map of its own:
#
#   slot              where it goes: idle, locomotion, attack, attack_windup, attack_strike, hit, death, arm, extra
#   rateFollowsSpeed  true when the clip is sped up or slowed to match ground speed (a walk: feet stay planted);
#                     false when it keeps its own rate however fast the body goes (a wingbeat, a hover)
#   speed             for a locomotion clip: metres a second at the manifest's `metres` size, when known
#   windUpEnd         for an attack clip: the fraction of THIS clip where the wind-up ends (the held telegraph
#                     pose), and windUpSeconds the same in seconds; attack_windup is all wind-up (1), strike none (0)
#
# Pure Python: make_clips.py (under Blender) and the tests both use it.

CONTRACT = "autorig-clips-contract/1"

SPEED_UNITS = ("metres per second at the manifest's 'metres' size; an engine that shows the model at another size "
               "scales speeds by its size / metres")

SLOTS = ("idle", "locomotion", "attack", "attack_windup", "attack_strike", "hit", "death", "arm", "extra")


def slot_of(name, hovers=False):
    """The slot a clip named `name` fills. `hovers`: the creature also has an idle or hover clip, so its `fly` is
    what it plays travelling (locomotion) rather than all the time (idle)."""
    n = name.lower()
    if n in ("idle", "hover"):
        return "idle"
    if n in ("walk", "swim", "run"):
        return "locomotion"
    if n == "fly":
        return "locomotion" if hovers else "idle"
    if n == "attack_windup":
        return "attack_windup"
    if n == "strike":
        return "attack_strike"
    if n == "attack" or n.startswith("attack_"):
        return "attack"
    if n == "hit":
        return "hit"
    if n in ("death", "die", "explode"):
        return "death"
    if n == "arm":
        return "arm"
    return "extra"            # jump_*, dodge, block, roll, perch...: authored for engines that want them


def clip_fields(name, seconds, names, windup_fraction=None, speed=None):
    """The contract's fields for one clip. `names`: every clip in the manifest (to tell a hovering flyer);
    `windup_fraction`: where the wind-up ends in each full attack clip, as a fraction of it; `speed`: metres a
    second for a locomotion clip."""
    hovers = any(x.lower() in ("idle", "hover") for x in names)
    slot = slot_of(name, hovers)
    out = {"slot": slot, "rateFollowsSpeed": slot == "locomotion" and name.lower() != "fly"}
    if slot == "locomotion" and speed:
        out["speed"] = round(float(speed), 4)
    if slot == "attack" and windup_fraction is not None:
        out["windUpEnd"] = round(float(windup_fraction), 4)
        out["windUpSeconds"] = round(float(windup_fraction) * float(seconds), 4)
    elif slot == "attack_windup":
        out["windUpEnd"], out["windUpSeconds"] = 1.0, round(float(seconds), 4)
    elif slot == "attack_strike":
        out["windUpEnd"], out["windUpSeconds"] = 0.0, 0.0
    return out


def header():
    """The manifest-level fields that say which contract it follows and what its speeds mean."""
    return {"contract": CONTRACT, "speedUnits": SPEED_UNITS}
