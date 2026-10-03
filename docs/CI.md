# Continuous Integration (CI) and Audit Thresholds

Autorig Workbench includes an automated headless Blender CI pipeline for Linux (`.github/workflows/ci.yml` and `scripts/ci_audit_thresholds.py`).

## Workflow Overview

The CI workflow:
1. Sets up Python 3.11 and Node.js 20.
2. Caches and installs Blender 5.2.2 LTS (`blender-5.2.2-linux-x64.tar.xz`).
3. Verifies Blender version detection and tested range (`5.2 LTS`).
4. Executes the full Python unit test suite (`python3 -m unittest discover -s tests -v`).
5. Executes viewer and gamepad tests under Node (`node tests/viewer_logic_test.mjs`).
6. Executes the headless Blender audit pipeline (`scripts/ci_audit_thresholds.py`), which builds a sample rig, decimate/trims it, runs `audit.py`, and validates against strict audit thresholds (`autorig/cli/audit_all.py all -render 0 -strict`).

## Where it runs

The workflow is [`.github/workflows/ci.yml`](../.github/workflows/ci.yml). It runs on every push to `main` and on every
pull request into `main`.

Besides Blender itself, the Linux runner needs Blender's runtime libraries, **including libEGL and Mesa**
(`libegl1 libgles2 libegl-mesa0 libgl1-mesa-dri`). Blender 5.2 renders off-screen through EGL, and without them every
step that renders aborts with exit code -6 after `Couldn't open libEGL.so.1`.

## Running Locally

To run the CI audit thresholds check locally without GitHub Actions:
```bash
python3 scripts/ci_audit_thresholds.py
```
To run the full test suite:
```bash
python3 -m unittest discover -s tests -v
node tests/viewer_logic_test.mjs
```
