# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: Auto's route (core/suggest.py's archetype, then core/m2m_templates.choose) against the routes the
# committed rigs of Tornado Titans, Heartroot and Smeltdown took (tests/data/route_truth.json), replayed from the
# features steps/route_features.py saved (tests/data/route_features/). No Blender.
#
#   python -m unittest tests.test_route_choice -v
#   python autorig/cli/route_eval.py --show wrong          the misses, with Auto's reasons
import os, sys, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(REPO, "autorig", "cli"), os.path.join(REPO, "autorig", "core")]
import m2m_templates as T  # noqa: E402
import route_eval as R  # noqa: E402

DATA = os.path.join(REPO, "tests", "data", "route_features")


@unittest.skipUnless(os.path.isdir(DATA), "no saved route features")
class Accuracy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = R.evaluate()
        cls.got = {row["model"]: row["got"] for row in cls.r["rows"]}

    def test_route_accuracy(self):
        # 2026-10-18: 117/250 (47%) before the body plan's flat faces and belly; 235/250 (94%) after
        self.assertGreaterEqual(self.r["route_ok"] / self.r["models"], 0.92, self.r["route_ok"])
        self.assertGreaterEqual(self.r["template_ok"] / self.r["models"], 0.90, self.r["template_ok"])
        self.assertEqual(self.r["by_truth"]["people"]["route_ok"], self.r["by_truth"]["people"]["models"])

    def test_named_cases(self):
        for model, route in (("ticket_taker", "people"),           # a T-posed coat read as wings
                             ("Chicken_Rooster_Tripo", "m2m:bird"),  # a rooster read as a person
                             ("Barn_Grey_Big", "classic"),          # a barn lying on the ground read as a shark
                             ("Truck_Peanut", "classic"),           # four wheels read as four legs
                             ("hopper", "m2m:quad"),                # a sitting hare read as lying down
                             ("Cow_Angus_Tripo", "m2m:horse"), ("Dog_TanWhite_Tripo", "m2m:fox"),
                             ("Shark", "m2m:shark"), ("sea_serpent", "m2m:snake"), ("reef_crab", "m2m:spider"),
                             ("the_robot", "people"), ("jelly_fish", "classic"), ("Tree_Oak_Tripo", "classic")):
            if model in self.got:
                self.assertTrue(R.same_template(self.got[model], route), "%s: %s" % (model, self.got[model]))


class Choose(unittest.TestCase):
    def test_without_the_plan_nothing_is_a_prop(self):
        self.assertEqual(T.is_prop({"feet": None}), (False, ""))
        self.assertEqual(T.choose("quadruped", {"tail_reach": 0.3})[0], "horse")

    def test_built_things_are_props(self):
        t, why = T.choose("quadruped", {"feet": 4, "flat": 0.5, "belly": 0.15})
        self.assertIsNone(t)
        self.assertIn("flat faces", why)

    def test_legs_keep_a_flat_creature(self):
        # a hound whose armour plates are flat still stands on four legs with air under its belly
        self.assertEqual(T.choose("quadruped", {"feet": 4, "flat": 0.21, "belly": 0.35, "tail_reach": 0.6})[0], "fox")

    def test_sitting_on_big_feet_is_not_lying(self):
        self.assertEqual(T.choose("quadruped", {"feet": 4, "flat": 0.04, "belly": 0.02, "ground": 0.41,
                                                "tail_reach": 0.28})[0], "horse")
        self.assertEqual(T.choose("rigid", {"feet": 1, "flat": 0.1, "ground": 0.43, "length": 2.6, "stand": 0.34,
                                            "slender": 2.6})[0], "shark")

    def test_t_posed_person_and_rooster(self):
        self.assertEqual(T.choose("winged", {"feet": 2, "flat": 0.09, "belly": 0.0, "stand": 3.3, "tail": 0.0})[0],
                         "human")
        self.assertEqual(T.choose("humanoid", {"feet": 2, "flat": 0.05, "belly": 0.25, "stand": 1.14, "tail": 0.15,
                                               "length": 2.0})[0], "bird")
        self.assertTrue(T.is_person("human"))
        self.assertFalse(T.is_person(None, "winged", {"stand": 3.0, "tail": 0.0}))

    def test_plan_measures(self):
        plan = {"feet": {"count": 4, "fill": 0.1}, "wings": {"both": False}, "flat": {"share": 0.2},
                "belly": {"clear": 0.3}, "length": 2.5}
        self.assertEqual(T.plan_measures(plan), {"feet": 4, "feet_fill": 0.1, "flat": 0.2, "belly": 0.3, "length": 2.5})
        self.assertEqual(T.plan_measures(None), {})


if __name__ == "__main__":
    unittest.main()
