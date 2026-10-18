# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: deformation.fold_edges, the audit's stand-pose fold count, on a strip folded by hand.
#
#   python -m unittest tests.test_fold_edges -v
import math, os, sys, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "autorig", "core"))
try:
    import numpy as np
    from deformation import fold_edges
except ImportError:  # numpy: run with Blender's Python (CONTRIBUTING.md)
    np = None

REST = [[0, 0, 0], [1, 0, 0], [2, 0, 0], [0, 1, 0], [1, 1, 0], [2, 1, 0]]
TRIS = [(0, 1, 4), (0, 4, 3), (1, 2, 5), (1, 5, 4)]


def folded(degrees):
    q = [list(p) for p in REST]
    for i in (2, 5):
        q[i] = [1 + math.cos(math.radians(degrees)), q[i][1], math.sin(math.radians(degrees))]
    return q


@unittest.skipIf(np is None, "needs numpy")
class FoldEdges(unittest.TestCase):
    def test_flat_and_gently_bent_strips_have_no_folds(self):
        self.assertEqual(fold_edges(REST, REST, TRIS)[0], 0)
        self.assertEqual(fold_edges(REST, folded(30), TRIS)[0], 0)

    def test_a_crease_is_one_fold_on_the_shared_edge(self):
        n, pairs = fold_edges(REST, folded(150), TRIS)
        self.assertEqual(n, 1)
        self.assertEqual(sorted(pairs[0].tolist()), [1, 4])

    def test_the_whole_strip_turned_rigidly_is_not_folded(self):
        turned = [[-p[1], p[0], p[2]] for p in REST]
        self.assertEqual(fold_edges(REST, turned, TRIS)[0], 0)

    def test_no_triangles(self):
        self.assertEqual(fold_edges(REST, REST, [])[0], 0)


if __name__ == "__main__":
    unittest.main()
