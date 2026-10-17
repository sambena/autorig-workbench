# SPDX-License-Identifier: GPL-3.0-or-later
# The suggester's three opinions (core/suggest.py combine): the tips, the body plan (core/body_plan.py) and the
# picture vote (core/picture_vote.py, OpenCLIP). The picture scores are made up here: no model is loaded.
import io, json, os, sys, unittest
from contextlib import redirect_stdout
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(REPO, "autorig", "core")]
import suggest  # noqa: E402
import picture_vote  # noqa: E402
import extras  # noqa: E402


def vote(**chances):
    """A picture vote as picture_vote.score() returns it, with these archetype chances."""
    return {"archetypes": chances, "labels": {}, "model": "mock"}


# body plans as core/body_plan.py measured them on the samples and asset-packs models (trimmed)
PEDESTAL = {"height": 2.27, "stand": 2.27, "feet": {"count": 1, "fill": 0.55}, "mirror": {"x": 1.0, "y": 1.0},
            "wings": {"both": False}, "rest": {"upright": 0.036, "best": 0.045}}
WYVERN = {"height": 0.63, "stand": 0.63, "feet": {"count": 2, "fill": 0.02}, "mirror": {"x": 1.0, "y": 0.59},
          "wings": {"both": False}, "rest": {"upright": 0.0, "best": 0.15}}
DOG = {"height": 0.59, "stand": 0.59, "feet": {"count": 4, "fill": 0.08}, "mirror": {"x": 1.0, "y": 0.5},
       "wings": {"both": False}, "rest": {"upright": 0.07, "best": 0.21}}
BIPED = {"height": 1.35, "stand": 5.3, "feet": {"count": 2, "fill": 0.12}, "mirror": {"x": 1.0, "y": 0.99},
         "wings": {"both": False}, "rest": {"upright": 0.005, "best": 0.48}}
MOTH = {"height": 0.7, "stand": 1.8, "feet": {"count": 2, "fill": 0.01}, "mirror": {"x": 1.0, "y": 0.41},
        "wings": {"both": True}, "rest": {"upright": 0.0, "best": 0.34}}


class CombineTest(unittest.TestCase):
    def test_pedestal_is_rigid_not_winged(self):
        v = suggest.combine("winged", "high", PEDESTAL, vote(rigid=0.99, winged=0.01))
        self.assertEqual(v["picked"], "rigid")
        self.assertIn("rigid overrides the tips' winged", v["why"])
        self.assertTrue(any("solid base" in w for w in v["why"]))

    def test_pedestal_body_plan_alone_is_enough(self):
        self.assertEqual(suggest.combine("winged", "high", PEDESTAL)["picked"], "rigid")

    def test_dog_tips_read_as_winged(self):
        # the watch case: a dog OBJ whose narrow body made a wide pair look like wings
        v = suggest.combine("winged", "high", DOG, vote(quadruped=0.97, winged=0.02))
        self.assertEqual(v["picked"], "quadruped")
        self.assertEqual(v["confidence"], "high")
        # the body plan alone cannot tell it from a dragon on four feet with its wings folded: level, so the tips
        # stand; the pictures are what tell a dog
        self.assertEqual(suggest.combine("winged", "high", DOG)["picked"], "winged")

    def test_the_picture_vote_breaks_a_tie(self):
        # the wyvern sample: the tips call its rod wings arms; the body plan says a bird's stance, not a person's
        v = suggest.combine("humanoid", "high", WYVERN, vote(winged=0.43, quadruped=0.17, humanoid=0.0))
        self.assertEqual(v["picked"], "winged")
        self.assertTrue(any(w.startswith("tie between") and w.endswith("for winged") for w in v["why"]))

    def test_a_tie_with_no_picture_vote_goes_to_the_best_score(self):
        self.assertEqual(suggest.combine("humanoid", "high", WYVERN)["picked"], "humanoid")    # 1.5 against 0.5
        with mock.patch.object(suggest, "plan_votes", return_value=({"rigid": 2.5}, ["rigid +2.5: test"])):
            v = suggest.combine("quadruped", "high", {"x": 1})
        self.assertEqual(v["picked"], "quadruped")              # 3 against 2.5: close, and no pictures
        self.assertTrue(any("no picture vote" in w for w in v["why"]))
        # exactly level: the tips' pick
        with mock.patch.object(suggest, "plan_votes", return_value=({"rigid": 3.0}, ["rigid +3: test"])):
            self.assertEqual(suggest.combine("quadruped", "high", {"x": 1})["picked"], "quadruped")

    def test_agreeing_opinions_keep_the_tips_confidence(self):
        v = suggest.combine("humanoid", "high", BIPED, vote(humanoid=0.63, rigid=0.16))
        self.assertEqual((v["picked"], v["confidence"]), ("humanoid", "high"))

    def test_a_picture_vote_alone_counts(self):
        v = suggest.combine("octopod", "high", None, vote(rigid=1.0))
        self.assertEqual(v["picked"], "rigid")                  # 3 against 3: a tie the pictures break

    def test_plan_votes(self):
        pts, why = suggest.plan_votes(DOG, "winged")
        self.assertEqual(pts["quadruped"], 2)
        self.assertEqual(pts["winged"], -1)                     # no wing sheets (4 feet: a dragon may stand so)
        pts, _ = suggest.plan_votes(DOG, "humanoid")
        self.assertEqual(pts["humanoid"], -1.5)                 # the tips' humanoid legs, but 4 feet down
        pts, _ = suggest.plan_votes(BIPED, "hexapod")
        self.assertEqual(pts["humanoid"], 2)
        self.assertEqual(pts["hexapod"], -1.5)                  # the tips' hexapod legs, but 2 feet down
        pts, _ = suggest.plan_votes(MOTH)
        self.assertNotIn("humanoid", pts)                       # wing sheets: a tall thin flier is no person
        self.assertEqual(pts["winged"], 2)
        pts, _ = suggest.plan_votes({"height": 0.2, "length": 4.0, "feet": {"count": 1, "fill": 0.1}})
        self.assertEqual(pts["serpent"], 2)
        self.assertEqual(suggest.plan_votes({}), ({}, []))


class SuggestSkeletonTest(unittest.TestCase):
    # a wyvern's tips: rod wings held out, feet down, head and tail on the centre line (0..1 bounds, facing -Y)
    TIPS = [[0.5, 0.05, 0.75], [0.5, 0.95, 0.3], [0.62, 0.45, 0.02], [0.38, 0.45, 0.02],
            [0.97, 0.5, 0.75], [0.03, 0.5, 0.75]]
    SIZE = [1.0, 1.6, 1.0]

    def test_without_the_extras_nothing_changes(self):
        plain = suggest.suggest_skeleton("w", tips=self.TIPS, proportions=self.SIZE)
        same = suggest.suggest_skeleton("w", tips=self.TIPS, proportions=self.SIZE, body_plan=None, picture=None)
        self.assertEqual(plain, same)
        self.assertNotIn("votes", plain)
        self.assertFalse(plain["reasons"][0].startswith("picked"))

    def test_the_vote_changes_the_archetype_and_the_chains(self):
        res = suggest.suggest_skeleton("w", tips=self.TIPS, proportions=self.SIZE, body_plan=PEDESTAL,
                                       picture=vote(rigid=1.0))
        self.assertEqual(res["archetype"], "rigid")
        self.assertEqual(res["spec"]["clips"]["archetype"], "turret")
        self.assertTrue(res["reasons"][0].startswith("picked rigid: tips: "))
        self.assertEqual(res["votes"]["picked"], "rigid")
        roles = [c["role"] for c in res["rig"]["chains"]]
        self.assertEqual(roles, ["spine"])                      # a prop: no legs, arms, wings, head or tail
        json.dumps(res)                                         # written to <model>_suggest.json as it is

    def test_overridden_to_winged_gets_wing_chains(self):
        res = suggest.suggest_skeleton("w", tips=self.TIPS, proportions=self.SIZE, body_plan=WYVERN,
                                       picture=vote(winged=0.9))
        self.assertEqual(res["archetype"], "winged")
        names = [c["name"] for c in res["rig"]["chains"]]
        self.assertIn("wing.L", names)
        self.assertIn("leg.L", names)
        self.assertEqual(len(names), len(set(names)))

    def test_chain_names_are_unique(self):
        # two arm pairs (hands and elbows) on a humanoid, four on a quadruped: never two arm.L
        tips = [[0.5, 0.5, 0.98], [0.6, 0.5, 0.02], [0.4, 0.5, 0.02], [0.95, 0.5, 0.7], [0.05, 0.5, 0.7],
                [0.8, 0.5, 0.55], [0.2, 0.5, 0.55]]
        res = suggest.suggest_skeleton("b", tips=tips, proportions=[1.0, 0.3, 1.8])
        names = [c["name"] for c in res["rig"]["chains"]]
        self.assertEqual(res["archetype"], "humanoid")
        self.assertEqual(len(names), len(set(names)), names)
        self.assertEqual(names.count("arm.L"), 1)
        arms = suggest.build_suggested_chains({"legs": [], "wings": [], "arms": [
            {"left": [0.9, 0.3, 0.5], "right": [0.1, 0.3, 0.5], "y": 0.3, "z": 0.5, "span": 0.8},
            {"left": [0.9, 0.6, 0.5], "right": [0.1, 0.6, 0.5], "y": 0.6, "z": 0.5, "span": 0.8}]}, {}, "quadruped")
        self.assertEqual([c["name"] for c in arms if c["role"] == "arm"], ["arm.L", "arm.R", "arm_2.L", "arm_2.R"])

    def test_reassign_limbs(self):
        pair = lambda z, span, y=0.5: {"left": [0.5 + span / 2, y, z], "right": [0.5 - span / 2, y, z], "y": y,
                                        "z": z, "span": span}
        limbs = {"legs": [], "arms": [pair(0.05, 0.3), pair(0.6, 0.9), pair(0.5, 0.4)], "wings": []}
        h = suggest.reassign_limbs(limbs, "humanoid")
        self.assertEqual([p["z"] for p in h["legs"]], [0.05])
        self.assertEqual([p["span"] for p in h["arms"]], [0.9])
        q = suggest.reassign_limbs({"legs": [pair(0.05, 0.3, 0.8), pair(0.06, 0.3, 0.2)], "arms": [pair(0.6, 0.9)],
                                    "wings": []}, "quadruped")
        self.assertEqual([p["y"] for p in q["legs"]], [0.2, 0.8])   # front to back
        self.assertEqual(q["arms"], [])
        self.assertEqual(suggest.reassign_limbs({"legs": [pair(0.1, 0.3)]}, "rigid")["legs"], [])


class FacingTest(unittest.TestCase):
    @staticmethod
    def box(x0, x1, y0, y1, z0, z1, n=6):
        return [[x0 + (x1 - x0) * i / (n - 1), y0 + (y1 - y0) * j / (n - 1), z0 + (z1 - z0) * k / (n - 1)]
                for i in range(n) for j in range(n) for k in range(n)]

    def test_lateral_axis(self):
        # a dog along x: legs mirror left to right (across y), not front to back (the head is at one end)
        dog = (self.box(-1, 1, -0.2, 0.2, 0.5, 0.8) + self.box(-1.4, -1, -0.15, 0.15, 0.6, 1.0) +
               self.box(-0.9, -0.8, -0.2, -0.1, 0, 0.5) + self.box(-0.9, -0.8, 0.1, 0.2, 0, 0.5) +
               self.box(0.8, 0.9, -0.2, -0.1, 0, 0.5) + self.box(0.8, 0.9, 0.1, 0.2, 0, 0.5))
        self.assertEqual(suggest.lateral_axis(dog), 1)
        self.assertIn(suggest.detect_mesh_forward(dog), ([1, 0, 0], [-1, 0, 0]))
        # a stick figure facing -Y, arms out along x, symmetric both ways: upright, so across is the wider way
        fig = (self.box(-0.15, 0.15, -0.1, 0.1, 0.9, 1.5) + self.box(-0.1, -0.05, -0.05, 0.05, 0, 0.9) +
               self.box(0.05, 0.1, -0.05, 0.05, 0, 0.9) + self.box(-0.6, 0.6, -0.05, 0.05, 1.35, 1.45) +
               self.box(-0.1, 0.1, -0.1, 0.1, 1.5, 1.8))
        self.assertEqual(suggest.lateral_axis(fig), 0)
        self.assertIn(suggest.detect_mesh_forward(fig), ([0, 1, 0], [0, -1, 0]))
        # a sphere-ish blob says nothing: the protrusion scores decide as before
        blob = self.box(-1, 1, -1, 1, 0, 2)
        self.assertIsNone(suggest.lateral_axis(blob))


class PictureVoteTest(unittest.TestCase):
    def test_by_archetype_adds_descriptions(self):
        labels = {t: 0.0 for t, _ in picture_vote.LABELS}
        labels["a bird"], labels["a winged dragon"], labels["a vehicle"] = 0.3, 0.4, 0.3
        got = picture_vote.by_archetype(labels)
        self.assertAlmostEqual(got["winged"], 0.7)
        self.assertAlmostEqual(got["rigid"], 0.3)
        self.assertEqual(set(got), {a for _, a in picture_vote.LABELS})

    def test_every_description_votes_for_a_known_archetype(self):
        known = {"humanoid", "quadruped", "winged", "hexapod", "octopod", "serpent", "floater", "rigid"}
        self.assertTrue({a for _, a in picture_vote.LABELS} <= known)

    def test_no_pictures_is_an_error_not_a_crash(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            picture_vote.main(["no-such-file.png"])
        line = buf.getvalue().strip()
        self.assertTrue(line.startswith("PICTURE_VOTE "))
        self.assertIn("error", json.loads(line[len("PICTURE_VOTE "):]))


class ExtrasRequirementsTest(unittest.TestCase):
    def test_an_extra_may_need_two_packages(self):
        with mock.patch.object(extras.subprocess, "call", return_value=0) as call, \
                mock.patch.object(extras, "folder", return_value=os.devnull + "_x"), \
                mock.patch.object(extras.os, "makedirs"), redirect_stdout(io.StringIO()):
            extras.install(["trimesh"], python="py")
        cmd = call.call_args[0][0]
        self.assertEqual(cmd[-2:], ["trimesh", "scipy"])


try:
    import numpy  # noqa: F401
    import trimesh  # noqa: F401
    HAVE_TRIMESH = True
except Exception:
    HAVE_TRIMESH = False


@unittest.skipUnless(HAVE_TRIMESH, "needs numpy and trimesh (run with Blender's Python and the extras on the path)")
class BodyPlanTest(unittest.TestCase):
    def test_measures(self):
        import trimesh
        import body_plan

        def boxes(*specs):
            parts = []
            for (cx, cy, cz, sx, sy, sz) in specs:
                b = trimesh.creation.box(extents=(sx, sy, sz))
                b.apply_translation((cx, cy, cz))
                parts.append(b)
            m = trimesh.util.concatenate(parts)
            return m.vertices, m.faces

        # a table-ish animal: a body on four legs
        v, f = boxes((0, 0, 0.7, 0.5, 1.6, 0.3), (-0.2, -0.7, 0.3, 0.1, 0.1, 0.6), (0.2, -0.7, 0.3, 0.1, 0.1, 0.6),
                     (-0.2, 0.7, 0.3, 0.1, 0.1, 0.6), (0.2, 0.7, 0.3, 0.1, 0.1, 0.6))
        plan = body_plan.measure(v, f)
        self.assertEqual(plan["feet"]["count"], 4)
        self.assertEqual(plan["parts"]["count"], 5)
        self.assertGreater(plan["mirror"]["x"], 0.95)
        self.assertFalse(plan["wings"]["both"])
        # a column on a wide base
        v, f = boxes((0, 0, 0.05, 1.0, 1.0, 0.1), (0, 0, 1.0, 0.4, 0.4, 1.8))
        plan = body_plan.measure(v, f)
        self.assertEqual(plan["feet"]["count"], 1)
        self.assertGreater(plan["feet"]["fill"], 0.8)
        self.assertGreater(plan["height"], 1.5)
        self.assertEqual(suggest.combine("winged", "high", plan)["picked"], "rigid")
        # a body with two thin flat sheets out to the sides (wings spread)
        v, f = boxes((0, 0, 0.6, 0.3, 1.0, 0.3), (-0.9, 0, 0.75, 1.4, 0.6, 0.02), (0.9, 0, 0.75, 1.4, 0.6, 0.02),
                     (-0.1, 0, 0.25, 0.06, 0.06, 0.5), (0.1, 0, 0.25, 0.06, 0.06, 0.5))
        plan = body_plan.measure(v, f)
        self.assertTrue(plan["wings"]["both"], plan["wings"])


if __name__ == "__main__":
    unittest.main()
