# Contributing to Autorig Workbench

Thank you for your interest in contributing to Autorig Workbench! We welcome contributions, bug reports, and suggestions.

Autorig Workbench provides automated rigging, skinning, decimation, and quality audits for 3D sculpts and procedural models using headless Blender and a local web GUI.

---

## Design Principles

1. **Pure Python & Headless Blender**: The core server and CLI are built with the Python standard library. Blender runs exclusively in the background (headless) as subprocesses and never opens an interactive window.
2. **Zero `pip` Dependencies**: Standard library only for core and GUI. Pillow is only optional for sheet layout scripts.
3. **Offline & Self-Contained**: `three.js` is vendored locally under [`autorig/gui/vendor/three/`](autorig/gui/vendor/three/).
4. **Safety by Default**:
   - The GUI server binds only to `127.0.0.1`.
   - Requires random session token per session.
   - Host checks prevent DNS rebinding attacks.
   - Strict path confinement ensures files are read/written only within `AUTORIG_MODELS` and `AUTORIG_WORK`.
   - Step cancellations kill only the specific process PID.
5. **Audited Quality**: Every rig is measured against strict mathematical thresholds (tears, bleed percentage, surface ownership, joint bends) and graded **PASS**, **CHECK**, or **FAIL**.

---

## Prerequisites

- **Python**: 3.9 or higher (standard library).
- **Blender**: 5.2 LTS (tested range). Accessible via `blender` on `PATH`, `/usr/bin/blender`, `/snap/bin/blender`, or the `AUTORIG_BLENDER` environment variable.
- **Node.js**: 18+ (used for testing the gamepad and viewer logic in [`tests/viewer_logic_test.mjs`](tests/viewer_logic_test.mjs)).

---

## Getting Started

1. **Clone the repository**:
   ```bash
   git clone https://github.com/sambena/autorig-workbench.git
   cd autorig-workbench
   ```

2. **Start the local GUI**:
   ```bash
   python -m autorig
   ```
   Or with custom paths:
   ```bash
   python -m autorig --models /path/to/models --work /path/to/work --port 8765
   ```

3. **CLI usage**:
   ```bash
   # Run pipeline for specific models
   python autorig/cli/run.py model_name

   # Run audit across all models
   python autorig/cli/run.py audit-all -strict
   ```

---

## Running Tests

Before submitting changes, run all test suites:

```bash
# 1. Full Python unit test suite
python3 -m unittest discover -s tests -v

# 1b. The weight-pass and skinning tests need numpy. With no numpy in your Python, run the suite with the Python
#     that ships inside Blender (it has numpy; Blender itself does not start), e.g. on Windows:
#     "C:\Program Files\Blender Foundation\Blender 5.2\5.2\python\bin\python.exe" -m unittest discover -s tests
# AUTORIG_NO_BLENDER=1 makes either run behave as if Blender were not installed (its headless tests skip).

# 2. Viewer & Gamepad Node logic tests
node tests/viewer_logic_test.mjs

# 3. Headless Blender audit pipeline verification
python3 scripts/ci_audit_thresholds.py
```

All 48+ Python tests, 10 Node tests, and the headless Blender pipeline check must pass.

---

## Project Structure

- [`autorig/core/`](autorig/core/): Pure-Python utilities (`layout`, `spec_store`, `blender`, `grades`, `suggest`, `placed_rules`). Kept free of `bpy` and `numpy` imports where possible so GUI server and tests remain fast.
- [`autorig/steps/`](autorig/steps/): Pipeline steps executed inside Blender (`survey.py`, `rerig.py`, `decimate.py`, `audit.py`, `make_clips.py`, `preview_glb.py`, `suggest_step.py`).
- [`autorig/gui/`](autorig/gui/): Local HTTP server (`server.py`), 3D results viewer (`viewer.html`, `viewer.js`), and spec editor (`spec_editor.html`, `spec_editor.js`, `spec_api.py`).
- [`autorig/cli/`](autorig/cli/): Command-line drivers (`run.py`, `pipeline.py`, `audit_all.py`).
- [`docs/`](docs/): Architecture guides ([`PLAN.md`](docs/PLAN.md), [`PIPELINE.md`](docs/PIPELINE.md)), specs ([`SPEC.md`](docs/SPEC.md), [`FORMATS.md`](docs/FORMATS.md)), CI ([`CI.md`](docs/CI.md)), and formal schema ([`rig.schema.json`](docs/rig.schema.json)).
- [`samples/`](samples/): Freely licensed CC0/CC-BY sample models for testing.

---

## Contributing Workflow

1. Create a descriptive feature branch off `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```
2. Write clean, readable code preserving existing comments and documentation style.
3. Add unit tests for your changes under `tests/`.
4. Run all test suites and ensure all checks pass.
5. Commit using [Conventional Commits](https://www.conventionalcommits.org/) (e.g. `feat:`, `fix:`, `docs:`, `test:`).
6. Push to your fork/branch and open a Pull Request against `main`.

---

## Licensing

Autorig Workbench is licensed under the **GNU General Public License v3.0 or later** ([GPL-3.0-or-later](LICENSE)). All contributions submitted will be covered under this license.
