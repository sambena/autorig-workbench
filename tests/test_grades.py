# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the audit's grades (autorig/core/grades.py) on made-up audit numbers, and the GUI server's
# collection audit (/api/audits, /api/audit, /api/audit-all) on made-up audit files.
#
#   python -m unittest tests.test_grades -v
#
# No Blender is needed except for the last test, which runs Audit all over two models whose rigged FBX is not an FBX
# and checks that the job goes on past the first failure, audits both, and leaves them ungraded in the table.
import json, os, secrets, shutil, subprocess, sys, tempfile, time, unittest, urllib.error, urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "autorig", "core"))
import blender  # noqa: E402
import grades  # noqa: E402
from grades import CHECK, FAIL, PASS  # noqa: E402

HAVE_BLENDER = blender.find(required=False) is not None
TOKEN = "grades-" + secrets.token_hex(4)
CLEAN = {"bleed_pct": 0.5, "combined_tears": 0, "bend_tears": 0, "head_pct": 8.0, "max_influences": 4}


def grade(allowances=None, gaps=None, **values):
    return grades.grade_audit(dict(CLEAN, **values), allowances, gaps)


class Grades(unittest.TestCase):
    def test_clean_rig_passes(self):
        v = grade()
        self.assertEqual((v["grade"], v["pass"]), (PASS, True))
        self.assertTrue(all(c["ok"] and c["grade"] == PASS for c in v["checks"].values()))

    def test_tears_counts_and_gaps(self):
        small = {"bend_tears": 1.0, "combined_tears": 1.0}
        # one tear on one bend, a small gap: look at it, not broken
        self.assertEqual(grade(bend_tears=1, gaps=small)["grade"], CHECK)
        self.assertEqual(grade(combined_tears=4, gaps=small)["grade"], CHECK)
        self.assertEqual(grade(bend_tears=12, combined_tears=12, gaps=small)["grade"], CHECK)
        # past the warn band: broken however small the gaps
        self.assertEqual(grade(bend_tears=13, gaps=small)["grade"], FAIL)
        self.assertEqual(grade(combined_tears=57, bend_tears=22, gaps=small)["grade"], FAIL)
        # few tears, but one opens a hole
        self.assertEqual(grade(bend_tears=1, gaps={"bend_tears": 5.1})["grade"], FAIL)
        self.assertEqual(grade(combined_tears=3, gaps={"combined_tears": 6.9})["grade"], CHECK)
        self.assertEqual(grade(combined_tears=3, gaps={"combined_tears": 8.5})["grade"], FAIL)
        # an old audit with no gap sizes: the count alone decides
        self.assertEqual(grade(bend_tears=3)["grade"], CHECK)

    def test_bleed_head_influences(self):
        self.assertEqual(grade(bleed_pct=2.0)["grade"], PASS)
        self.assertEqual(grade(bleed_pct=2.6)["grade"], CHECK)
        self.assertEqual(grade(bleed_pct=4.08)["grade"], FAIL)
        self.assertEqual(grade(head_pct=2.5)["grade"], PASS)
        self.assertEqual(grade(head_pct=1.8)["grade"], CHECK)
        self.assertEqual(grade(head_pct=0.42)["grade"], FAIL)
        self.assertEqual(grade(head_pct=None)["grade"], PASS)              # no head: nothing to own it
        self.assertEqual(grade(max_influences=5)["grade"], FAIL)           # no band: an engine drops the fifth

    def test_worst_check_decides_and_pass_is_strict(self):
        v = grade(bleed_pct=2.5, bend_tears=40, gaps={"bend_tears": 1.0})
        self.assertEqual(v["grade"], FAIL)
        self.assertEqual(v["checks"]["bleed_pct"]["grade"], CHECK)
        self.assertEqual(v["checks"]["bend_tears"]["grade"], FAIL)
        c = grade(bend_tears=2, gaps={"bend_tears": 1.0})
        self.assertEqual((c["grade"], c["pass"], c["checks"]["bend_tears"]["ok"]), (CHECK, False, False))

    def test_allowances_move_the_limit_and_the_band(self):
        allow = {"combined_tears": 32, "bend_tears": 9}
        big = {"combined_tears": 15.6, "bend_tears": 6.8}
        # within its allowance: PASS whatever the gap (the allowance was written knowing them)
        self.assertEqual(grade(allow, big, combined_tears=32, bend_tears=9)["grade"], PASS)
        self.assertEqual(grade(allow, {"bend_tears": 1.0}, bend_tears=15)["grade"], CHECK)
        self.assertEqual(grade(allow, {"bend_tears": 1.0}, bend_tears=22)["grade"], FAIL)
        v = grade({"thresholds": {"bleed_pct": 3.0}}, bleed_pct=2.9)        # the nested form reads the same
        self.assertEqual((v["grade"], v["checks"]["bleed_pct"]["limit"]), (PASS, 3.0))
        self.assertEqual(grades.limits({"why": "text", "bend_tears": True})["bend_tears"], 0)   # not numbers: ignored

    def test_old_verdicts_are_graded_from_their_values(self):
        old = {"pass": False, "checks": {"bleed_pct": {"value": 0.2, "ok": True, "limit": 2.0},
                                         "bend_tears": {"value": 1, "ok": False, "limit": 0}}}
        self.assertEqual(grades.grade_of(old), CHECK)
        allowed = {"pass": True, "checks": {"bend_tears": {"value": 1, "ok": True, "limit": 1}}}
        self.assertEqual(grades.grade_of(allowed), PASS)
        self.assertEqual(grades.grade_of({"grade": FAIL, "pass": False}), FAIL)
        self.assertIsNone(grades.grade_of(None))

    def test_table_sorts_worst_first(self):
        rows = [{"model": "a", "grade": PASS}, {"model": "b", "grade": FAIL, "combined_tears": 3},
                {"model": "c", "grade": CHECK, "bend_tears": 1}, {"model": "d", "grade": FAIL, "combined_tears": 150},
                {"model": "e", "grade": None}]
        self.assertEqual([r["model"] for r in sorted(rows, key=grades.severity)], ["e", "d", "b", "c", "a"])


def fake_audit(bend_tears, combined, gap, head=6.0, allow=None):
    """An audit JSON as audit.py writes it, graded by grades.py."""
    values = {"bleed_pct": 0.4, "combined_tears": combined, "bend_tears": bend_tears, "head_pct": head, "max_influences": 4}
    v = grades.grade_audit(values, allow, {"combined_tears": gap, "bend_tears": gap})
    site = {"pose": "bend", "bone": "leg_1.L", "rotation_deg": [40, 0, 0], "edges": bend_tears, "worst_gap_pct": gap,
            "clusters": [{"at": [0.1, 0.2, 0.3], "at_bbox": [0.5, 0.5, 0.5], "edges": bend_tears, "gap_pct": gap,
                          "bone": "leg_1.L", "owners": ["leg_1.L"]}], "points": [[0.1, 0.2, 0.3, gap]]}
    return {"verdict": dict(v, warnings=[]), "fbx": "x.fbx",
            "tears": {"worst_gap_pct": gap, "worst_bone": "leg_1.L" if bend_tears else None, "bones_tearing": 1 if bend_tears else 0,
                      "by_bone": [{"bone": "leg_1.L", "bend": bend_tears, "bend_gap_pct": gap, "twist": 0, "twist_gap_pct": 0}] if bend_tears else []},
            "tear_sites": [site] if bend_tears else []}


class CollectionApi(unittest.TestCase):
    MODELS = {"clean": (0, 0, 0.0), "few": (2, 0, 1.2), "broken": (60, 90, 12.0)}

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="autorig-grades-")
        cls.models = os.path.join(cls.tmp, "models")
        audit = os.path.join(cls.models, "_autorig", "audit")
        os.makedirs(audit)
        for name, (b, c, gap) in list(cls.MODELS.items()) + [("never", (0, 0, 0.0))]:
            d = os.path.join(cls.models, "Group", name, "rigged")
            os.makedirs(d)
            with open(os.path.join(d, name + ".fbx"), "w") as fh: fh.write("not an FBX")
            with open(os.path.join(os.path.dirname(d), name + ".obj"), "w") as fh: fh.write("v 0 0 0\n")
            if name != "never":
                with open(os.path.join(audit, name + ".json"), "w") as fh: json.dump(fake_audit(b, c, gap), fh)
        url_file = os.path.join(cls.tmp, "url.txt")
        cls.proc = subprocess.Popen([sys.executable, "-m", "autorig", "--models", cls.models, "--port", "0",
                                     "--no-browser", "--token", TOKEN], cwd=REPO,
                                    env=dict(os.environ, AUTORIG_URL_FILE=url_file),
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        url = ""
        for _ in range(100):
            if os.path.exists(url_file):
                with open(url_file) as fh: url = fh.read()
                if url: break
            time.sleep(0.1)
        cls.base = url.split("/?")[0]

    @classmethod
    def tearDownClass(cls):
        cls.proc.kill()
        cls.proc.wait()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, path, body=None, token=TOKEN):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                                     method="POST" if body is not None else "GET",
                                     headers={"Content-Type": "application/json", "X-Autorig-Token": token})
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)

    def code(self, path, **kw):
        try:
            self.call(path, **kw); return 200
        except urllib.error.HTTPError as e:
            return e.code

    def test_1_table_worst_first(self):
        rows = self.call("/api/audits")["models"]
        self.assertEqual([(r["model"], r["grade"]) for r in rows],
                         [("never", None), ("broken", FAIL), ("few", CHECK), ("clean", PASS)])
        few = rows[2]
        self.assertEqual((few["bend_tears"], few["worst_gap_pct"], few["worst_bone"], few["group"]), (2, 1.2, "leg_1.L", "Group"))
        self.assertEqual(self.code("/api/audits", token="wrong"), 403)

    def test_2_badges_and_details(self):
        st = {m["name"]: m["audit"] for m in self.call("/api/state")["models"]}
        self.assertEqual(st, {"broken": FAIL, "clean": PASS, "few": CHECK, "never": None})
        a = self.call("/api/model?name=few")["audit"]
        self.assertEqual((a["grade"], a["pass"], a["tear_sites"]), (CHECK, False, 1))
        bend = next(c for c in a["checks"] if c["check"] == "bend_tears")
        self.assertEqual((bend["grade"], bend["limit"], bend["check_limit"]), (CHECK, 0, 12))

    def test_3_tear_sites_for_a_viewer(self):
        full = self.call("/api/audit?name=broken")
        site = full["tear_sites"][0]
        self.assertEqual((site["pose"], site["bone"], len(site["clusters"][0]["at"])), ("bend", "leg_1.L", 3))
        self.assertEqual(self.code("/api/audit?name=never"), 404)
        self.assertEqual(self.code("/api/audit?name=../../etc"), 404)

    @unittest.skipUnless(HAVE_BLENDER, "Blender not found")
    def test_4_audit_all_goes_on_past_a_broken_model(self):
        job = self.call("/api/audit-all", {})
        self.assertEqual(job["step"], "audit-all")
        t0 = time.time()
        while time.time() - t0 < 300:
            j = self.call("/api/jobs/%d" % job["id"])
            if j["state"] not in ("queued", "running"): break
            time.sleep(0.5)
        self.assertEqual(j["state"], "failed")                 # none of these FBX files can be read
        started = [l for l in j["log"] if l.startswith("== audit ") and "/" in l and "done" not in l]
        self.assertEqual(len(started), 4, "\n".join(j["log"][-20:]))   # every model tried, one after another
        # an audit that did not finish leaves no grade behind, rather than the one before it
        self.assertEqual({r["grade"] for r in self.call("/api/audits")["models"]}, {None})

    @unittest.skipUnless(HAVE_BLENDER, "Blender not found")
    def test_5_audit_failed(self):
        audit_dir = os.path.join(self.models, "_autorig", "audit")
        with open(os.path.join(audit_dir, "broken.json"), "w") as fh:
            json.dump(fake_audit(20, 20, 15.0), fh)
        job = self.call("/api/audit-failed", {})
        self.assertEqual(job["step"], "audit-failed")
        self.assertEqual(job["model"], "(all failed models)")
        t0 = time.time()
        while time.time() - t0 < 300:
            j = self.call("/api/jobs/%d" % job["id"])
            if j["state"] not in ("queued", "running"): break
            time.sleep(0.5)


if __name__ == "__main__":
    unittest.main()
