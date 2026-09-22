# Continuous Integration (CI) and Audit Thresholds

Autorig Workbench includes an automated headless Blender CI pipeline for Linux (`ci/ci.yml` and `scripts/ci_audit_thresholds.py`).

## Workflow Overview

The CI workflow:
1. Sets up Python 3.11 and Node.js 20.
2. Caches and installs Blender 5.2.2 LTS (`blender-5.2.2-linux-x64.tar.xz`).
3. Verifies Blender version detection and tested range (`5.2 LTS`).
4. Executes the full Python unit test suite (`python3 -m unittest discover -s tests -v`).
5. Executes viewer and gamepad tests under Node (`node tests/viewer_logic_test.mjs`).
6. Executes the headless Blender audit pipeline (`scripts/ci_audit_thresholds.py`), which builds a sample rig, decimate/trims it, runs `audit.py`, and validates against strict audit thresholds (`autorig/cli/audit_all.py all -render 0 -strict`).

## Enabling in GitHub Actions

The workflow definition is located in [`ci/ci.yml`](file:///var/home/cosmo/Work/autorig-workbench/ci/ci.yml). To activate it in GitHub Actions:
```bash
mkdir -p .github/workflows
cp ci/ci.yml .github/workflows/ci.yml
git add .github/workflows/ci.yml
git commit -m "ci: activate GitHub Actions workflow"
git push
```
*(Note: Pushing `.github/workflows/` files requires the `workflow` OAuth scope on your GitHub token or personal access token).*

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
