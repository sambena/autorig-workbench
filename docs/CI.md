# Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request to `main`, on Linux:

1. Python 3.11 and Node 20; Blender 5.2.2 LTS downloaded into `/opt/blender` (cached) with its runtime libraries;
   `numpy` and `jsonschema` installed for the tests only (the tool needs nothing from pip).
2. The Blender version check (`autorig/core/blender.py`, the tested range is 5.2 LTS).
3. `python3 -m unittest discover -s tests -v` and `node tests/viewer_logic_test.mjs`.
4. `scripts/ci_audit_thresholds.py`: rigs, trims and audits a generated two-bone column and reads the verdict the
   audit wrote, then `audit_all -strict` over it.

Step 4 is a smoke test of the pipeline, not a quality gate: the column cannot tear. The samples in `samples/` are
rigged by the tests but their grades are not asserted yet, because only three of the five PASS (samples/README.md).
The gate the tool needs is the regression bench in docs/PLAN.md, P5: every skinning change measured against a stored
baseline of real models.

The workflow has not yet been seen green on GitHub: it was moved under `.github/workflows/` in the September 2026
cleanup (it lived in `ci/`, where GitHub does not look) and its Blender install steps were rewritten unrun.

Locally:

    python3 scripts/ci_audit_thresholds.py
    python3 -m unittest discover -s tests -v
    node tests/viewer_logic_test.mjs
