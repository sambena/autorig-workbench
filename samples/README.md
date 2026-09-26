Five generated models to try the tool on, one folder each with its `rig.json` (docs/SPEC.md) and licence: a beetle
(hexapod), a biped, a canine (quadruped), a pedestal (a static prop) and a wyvern (winged). `scripts/generate_samples.py`
made them from primitives, so they have no creases, loose pieces or thin parts: they show the tool running, not the
failure modes the audit exists for, and as of the cleanup (docs/PLAN.md) only the pedestal PASSes its audit.

This is the default models root (`AUTORIG_MODELS`). What the tool writes here (`rigged/`, `clips/`, `_autorig/`) is
not committed. Your own models go in a folder of your own: `python -m autorig --models <folder>`.
