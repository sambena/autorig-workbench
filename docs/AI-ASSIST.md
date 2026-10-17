# AI Assist

**AI Assist** in the menu bar hands one of the workbench's tools to an AI: Claude, OpenAI, Google Gemini, or a model
running on Ollama on your own machine. The AI looks at the model through read tools, tries changes with the
workbench's own steps, and proposes a fix. Nothing changes until you apply the plan, and an applied plan can be
undone.

## Spending

AI is off until you pick one in **AI Assist > AI settings & spending**. It runs only when you press a button.

- **Before a request:** the button says what the request could cost, for example *Ask Claude Sonnet 5.5 (up to
  $1.20)*. That figure is the worst case: every model call allowed, each one writing its longest allowed answer.
  Beside the button is the usual cost.
- **Before each model call:** the call is priced at its worst case. It is refused if it could take this month's
  spending past the monthly limit (default $5).
- **After each call:** what it actually billed is written to `.autorig_ai_usage.jsonl`. The settings dialog shows
  the month's total and the recent calls.
- **Models:** only models with a known price can be picked, so every call can be capped. Ollama is free. A Gemini
  key without billing can be marked as the free tier.
- **No fallback to another model:** Claude's server-side fallback, which re-runs a declined request on another
  model, is deliberately left off. It could bill a model the gate didn't price.

Keys are saved on this computer only, in `.autorig_ai.json`, which git ignores. The page never receives them back.
An empty key uses `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` or `GEMINI_API_KEY`. `AUTORIG_AI_FILE` and
`AUTORIG_AI_USAGE_FILE` move the two files.

## Fix this rig

Select a model and choose **AI Assist > Fix this rig**. You can edit the request before sending it; the default asks
the AI to make the model pass its audit, worst tears first. The AI then:

1. **Reads** the audit (each check against its limit, the worst bends with where they tear, each bone's share), the
   current `rig.json`, the survey and the rig log. It also looks at the bend test, the skin sheet, the QA render and
   the facing views, and can quote `docs/SPEC.md` and the grades in `docs/PIPELINE.md`.
2. **Tries** candidate specs with `trial_rig`. Each one rigs, trims and audits a scratch copy of the model under
   `<work>/ai_trials/<model>/<n>/`, using hard links for the source and its textures. The real model is not touched.
   Trials run on this computer and cost nothing but time. The AI can look at each trial's QA render.
3. **Proposes** the best `rig.json` it found. The plan shows the diff.

**Apply and re-rig** saves `rig.json` through the spec editor's own save: the spec is checked, `rig.json.bak` is
kept, and the save is refused if `rig.json` changed since the proposal. It then runs rig, trim, audit, clips (when the
spec has them) and preview, like Save & Re-rig. **Undo** puts the old `rig.json` back and re-rigs. It is refused if
`rig.json` was edited again after the fix. Undo records are kept under `<work>/ai/journal/`.

A conversation carries on: ask a follow-up and it keeps what it has seen. **New conversation** starts afresh.

## How it is built

| File | What it does |
|---|---|
| `autorig/core/ai_providers.py` | One neutral message format, turned into each provider's request and back. Native tool calling, with pictures in tool results. Plain `urllib`, nothing to install. Ported from Cougar Player. |
| `autorig/core/ai_cost.py` | Settings, the price list, the worst-case estimate, the monthly gate and the spend log. Ported from Deal Hunter and Cougar Player. |
| `autorig/core/ai_assist.py` | The loop: background requests the page polls, plans, apply and undo. It knows nothing about rigging; a *task* supplies the tools. |
| `autorig/gui/ai_rig.py` | The first task, Fix this rig: its tools, its prompt, apply and undo. |
| `autorig/gui/ai_api.py`, `ai_assist.js` | The routes (`/api/ai/*`) and the two dialogs. |

To add a task, write a class like `FixRig` with:
- `name`, `label` and `system` (the prompt);
- `defs`: the tools as `{name, description, schema}`;
- `run(name, args, ctx)`, returning `{content, images?, change?}`;
- `step_label`, `context_note`, `apply` and `undo`.

Then register it in `ai_api.assistant()`.

## Next tasks

These workbench tools gain most from an AI driving them. Each reuses the loop, the cost gate and the plan/undo
above.

| # | Task | The AI uses | Proposes |
|---|---|---|---|
| 1 | **Fix this rig** (done) | audit, bend test, skin sheet, `trial_rig` | a new `rig.json` |
| 2 | **Facing & orientation**: which way is the front | the four survey views | `rig.forward` / up axis |
| 3 | **Suggest a skeleton**: archetype and chains for a model with no spec. Today's suggester reads the biped sample as rigid and the pedestal as winged. | survey, front/side renders, geodesic tips | a starting `rig.json` |
| 4 | **Place joints** on a boneless model | measure sheets on the 0..1 grid, `probe` tips | chain points |
| 5 | **Clip doctor**: feet that slide, floor penetration, seams opening in a clip | Studio *Animation quality* report, clip strips | gait parameters, clip settings |
| 6 | **Mesh prep**: decide what Prepare and Mesh Doctor should do | doctor report, UV and joint-band checks | prepare options (unwrap, loops) |
| 7 | **Retarget mapping**: bones the auto-map left out | mocap and rig bone lists | a bone map |
| 8 | **Vehicle parts**: wheels fused into the body | renders, connected components | wheel hint boxes and pivots |
| 9 | **LOD settings** per asset | triangle counts, per-level surface error | LOD ratios and budget |
| 10 | **Collection triage**: what to fix first | the Audit all table | an ordered fix list, each linked to task 1 |
| 11 | **Reference check**: does the model match its photos | Studio reference sheet | notes before any paid generation |
| 12 | **Generation prompts** for Hyper3D or Tripo | references, the model's role | a specific prompt. The AI never submits a paid generation: that stays a button you press. |
