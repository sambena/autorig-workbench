# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: the speed pass (R5) must not change results. Each vectorised or cached helper is checked number
# for number against the loop it replaced, on random inputs.
import json, os, random, sys, tempfile, unittest
from unittest.mock import patch

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(REPO, "autorig", "core"), os.path.join(REPO, "autorig", "steps"),
                os.path.join(REPO, "autorig", "gui")]
import blender
import retargeter

try:
    import numpy as np
    import placed_rules
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False
    np = placed_rules = None


def old_islands(n, edges):
    """rerig.islands_of before R5: one edge at a time."""
    parent = list(range(n))
    def find(x):
        while parent[x] != x: parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for a0, b0 in edges:
        a, b = find(a0), find(b0)
        if a != b: parent[a] = b
    out = {}
    for i in range(n): out.setdefault(find(i), []).append(i)
    return list(out.values())


class FakeGroup:
    """A Blender vertex group's add/remove over a dict, storing float32 as Blender does."""
    def __init__(self, name, store):
        self.name, self.store, self.calls = name, store, 0

    def add(self, idx, w, mode):
        assert mode == 'REPLACE'
        self.calls += 1
        for i in idx: self.store[(i, self.name)] = float(np.float32(w))

    def remove(self, idx):
        self.calls += 1
        for i in idx: self.store.pop((i, self.name), None)


class FakeGroups:
    def __init__(self, names, store):
        self.store, self.g = store, {}
        for nm in names: self.new(name=nm)

    def get(self, name): return self.g.get(name)

    def new(self, name):
        self.g[name] = FakeGroup(name, self.store)
        return self.g[name]


class FakeMesh:
    def __init__(self, names, store): self.vertex_groups = FakeGroups(names, store)


def old_write_weights(mesh, bone_names, W_old, W_new, eps=1e-3, locked=None):
    """placed_rules.write_weights before R5: one call per row per bone."""
    vg = mesh.vertex_groups
    diff = np.abs(W_new - W_old).sum(axis=1)
    rows = np.where(diff > eps)[0]
    if locked:
        rows = np.array([r for r in rows if int(r) not in locked], dtype=int)
    if len(rows) == 0:
        return 0
    groups = {}
    for bi, name in enumerate(bone_names):
        g = vg.get(name)
        if g is None and float(W_new[rows, bi].max()) > 1e-4:
            g = vg.new(name=name)
        if g is not None:
            groups[bi] = g
    for vi in rows:
        for bi, g in groups.items():
            w = float(W_new[vi, bi])
            if w > 1e-4:
                g.add([int(vi)], w, 'REPLACE')
            else:
                g.remove([int(vi)])
    return int(len(rows))


@unittest.skipUnless(HAVE_NUMPY, "numpy not installed in system Python (available in Blender)")
class VectorisedSameAsLoops(unittest.TestCase):
    def test_islands_match_the_old_union_find(self):
        rnd = random.Random(5)
        for _ in range(400):
            n = rnd.randint(0, 80); m = rnd.randint(0, 120)
            e = [(rnd.randrange(n), rnd.randrange(n)) for _ in range(m)] if n else []
            self.assertEqual(placed_rules.islands_from_edges(n, np.array(e, dtype=int).reshape(-1, 2)),
                             old_islands(n, e))

    def test_islands_long_chain_converges(self):
        n = 5000
        e = np.array([(i + 1, i) for i in range(n - 1)][::-1])
        self.assertEqual(placed_rules.islands_from_edges(n, e), [list(range(n))])

    def test_write_weights_leaves_the_same_groups(self):
        rnd = np.random.default_rng(3)
        names = ["b%d" % k for k in range(7)]
        for trial in range(60):
            n = 40
            W_old = rnd.random((n, 7)) * (rnd.random((n, 7)) > 0.5)
            W_old = W_old.astype(np.float32).astype(np.float64)          # what reading a vertex group gives
            W_new = W_old.copy()
            ch = rnd.random(n) < 0.6
            W_new[ch] = rnd.random((ch.sum(), 7)) * (rnd.random((ch.sum(), 7)) > 0.4)
            W_new[rnd.random((n, 7)) < 0.1] = 1.0                        # rigid rows share one weight
            W_new[rnd.random((n, 7)) < 0.05] = 5e-5                      # under the cut: removed
            locked = set(int(i) for i in rnd.choice(n, 5, replace=False)) if trial % 2 else None
            present = [nm for k, nm in enumerate(names) if k != trial % 7]    # one group missing: made on demand
            results = []
            for fn in (old_write_weights, placed_rules.write_weights):
                store = {}
                for i in range(n):
                    for k, nm in enumerate(names):
                        if nm in present and W_old[i, k] > 0: store[(i, nm)] = float(W_old[i, k])
                mesh = FakeMesh(present, store)
                rows = fn(mesh, names, W_old, W_new, locked=locked)
                results.append((rows, store, sorted(mesh.vertex_groups.g)))
            self.assertEqual(results[0], results[1])

    def test_write_weights_makes_fewer_calls(self):
        n, names = 500, ["a", "b", "c", "d"]
        W_old = np.zeros((n, 4)); W_new = np.zeros((n, 4)); W_new[:, 0] = 1.0
        mesh = FakeMesh(names, {})
        placed_rules.write_weights(mesh, names, W_old, W_new)
        self.assertLessEqual(sum(g.calls for g in mesh.vertex_groups.g.values()), 4)    # was 2000


class CachedLookups(unittest.TestCase):
    def test_probe_is_asked_once_per_file_version(self):
        calls = []

        def fake(mode, path, tag):
            calls.append(path)
            return {"joints": ["Hips"]}
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "walk.fbx")
            with open(f, "wb") as fh: fh.write(b"x")
            with patch.object(retargeter, "_probe_blender", fake):
                a = retargeter._probe("fbx", f, "T"); a["joints"].append("changed")
                b = retargeter._probe("fbx", f, "T")
                self.assertEqual(len(calls), 1)
                self.assertEqual(b["joints"], ["Hips"])                 # a copy: the caller's change stays theirs
                with open(f, "wb") as fh: fh.write(b"xy")               # a new size: asked again
                retargeter._probe("fbx", f, "T")
                self.assertEqual(len(calls), 2)
                missing = os.path.join(d, "missing.fbx")
                retargeter._probe("fbx", missing, "T"); retargeter._probe("fbx", missing, "T")
                self.assertEqual(len(calls), 4)                         # no file to stat: never cached

    def test_blender_command_uses_factory_startup(self):
        with patch.object(blender, "find", return_value="blender"), patch.dict(os.environ, {}):
            os.environ.pop("AUTORIG_USER_PREFS", None)
            argv = blender.command("rerig.py", "-only", "wolf")
            self.assertEqual(argv[:3], ["blender", "-b", "--factory-startup"])
            self.assertEqual(argv[-3:], ["--", "-only", "wolf"])
            self.assertIn("--python-exit-code", argv)
            os.environ["AUTORIG_USER_PREFS"] = "1"
            self.assertNotIn("--factory-startup", blender.command("rerig.py"))

    def test_audit_grade_rereads_only_on_change(self):
        import server, grades
        with tempfile.TemporaryDirectory() as d, patch.object(server, "grades", grades):   # main() imports it
            p = os.path.join(d, "wolf.json")
            with open(p, "w") as fh: json.dump({"verdict": {"grade": "PASS"}}, fh)
            reads = []
            real = server.grades.grade_of

            def spy(v, o):
                reads.append(1)
                return real(v, o)
            with patch.object(server.grades, "grade_of", spy):
                self.assertEqual(server.audit_grade(p, None), server.audit_grade(p, None))
                self.assertEqual(len(reads), 1)
                server.audit_grade(p, {"bleed_pct": 1})                 # other thresholds: graded again
                self.assertEqual(len(reads), 2)
                with open(p, "w") as fh: json.dump({"verdict": {"grade": "FAIL", "pad": "x" * 50}}, fh)
                server.audit_grade(p, {"bleed_pct": 1})                 # the file changed
                self.assertEqual(len(reads), 3)
            os.remove(p)
            self.assertEqual(server.audit_grade(p, None), "?")


if __name__ == "__main__":
    unittest.main()
