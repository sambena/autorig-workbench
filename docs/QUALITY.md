# The quality score

One number, 0 to 100, for how good a rigged and animated model is, with no AI and no human eyes. **PASS is 90 or
more**, CHECK is 75 to 89 (look at the contact sheets), FAIL is under 75.

```bash
python autorig/cli/run.py score Cow_Angus_Tripo,Cat_Tabby_Tripo     # some models
python autorig/cli/run.py score all --group TornadoTitans            # every rigged model in a group
python autorig/cli/run.py score all --rescore                        # score the saved measures again, no Blender
```

Options: `--out <dir>` (default `<AUTORIG_WORK>/score`), `--render 0` (numbers only, no contact sheets),
`--audit read|run|skip` (default `read`: the model's committed `rigged/audit/<model>.json`, else the work audit),
`--tiles N` (frames per sheet row, default 6). Run it on the Proxmox box like every model step.

Each model gets `<out>/<model>/`:

| file | what |
|---|---|
| `measure.json` | everything `steps/score.py` measured: rest against source, per clip stretch, squash, the worst edges and the bones that own their ends, limbs inside the body, feet, sinking |
| `score.json` | the score, grade, every check's value and what it cost, and the three checks that cost most (`worst`) |
| `sheet_rest.png`, `sheet_<clip>.png` | side view over front view, orthographic, Workbench, 6 frames across the clip |

and the table, worst first, goes in `<out>/_ranked.md` and `_ranked.json`. A partial run keeps the other models' rows.
Exit code 0 when no model it scored FAILs.

The code: `autorig/steps/score.py` measures (inside Blender, one model per Blender), `autorig/core/quality_score.py`
scores (plain Python, the limits are its `CHECKS` table), `autorig/cli/score_all.py` drives and writes the table.

## What it measures

It reads the model's `rigged/preview.glb` (the rig and every clip, as a game gets them) and its source mesh
(`layout.source_model`). Before the rest frame and before every clip, the NLA tracks are muted, every pose bone's
`matrix_basis` is reset and the objects go back to their rest matrices; only then is the clip's action set. Without
that the renders and the numbers carry the previous clip's pose and lie.

| check | measure | good | bad | weight |
|---|---|---|---|---|
| `rest_mean_pct` | mean distance from the rigged mesh at rest to the source's surface, % of the height, after lining both up (centred, feet on the floor, scaled to one height, turned to the best degree: m2m_rig turns a model to face -Y however it was modelled) | 2.5 | 6 | 10 |
| `rest_p99_pct` | the same, 99th percentile: a part that moved | 5 | 12 | 25 |
| `stretch3_permille` | edges any frame of the clip stretches past 3x, opening a gap over 0.4% of the size, per thousand edges | 5.5 | 12 | 20 |
| `max_stretch` | the worst edge's stretch | 12 | 60 | 5 |
| `squash_permille` | edges squashed under a third of their length, per thousand | 13 | 30 | 5 |
| `arm_in_torso_pct` | forearm and hand vertices that go more than 1% of the height under the surface of the rest of the body (the body without that arm) in some frame, % of them | 15 | 60 | 15 |
| `leg_cross_pct` | the same for each leg against the body without that leg (the other legs included) | 23 | 45 | 10 |
| `foot_skate` | `foot_contact.foot_skate` (as `clip-check` measures it), worst clip | 50 | 150 | 10 |
| `hover_pct` | a travelling clip whose feet never come down to the rest floor, % of the height | 1.5 | 6 | 5 |
| `sink_pct` | the mesh below the floor, % of the height | 6.5 | 15 | 5 |
| `audit_bleed_pct` | the audit's bleed | 3 | 10 | 5 |
| `audit_head_short` | how far the audit's head share is under 1.5% | 0 | 2 | 5 |

A rig that rests in its template's pose on purpose (rig.json `m2m.rest` "template": a snake laid along the
template's line) skips both rest checks; `score.json` says so in `rest_skipped`.

A check costs nothing at or under *good*, its whole weight at or past *bad*, and a straight line between. Clip checks
take the worst clip. Jumps, rolls, deaths, falls, flights, swims, landings and lying down are left out of the five
ground checks (skate, hover, sink, arms and legs in the body): they leave the ground or fold the limbs against the
body on purpose.

The self-intersection proxies only test limb vertices that stand clear of the body at rest (3% of the height), and
only to a depth of 6% of the height, on 8 frames a clip. Where a limb joins the body its skin moves with the body's,
and a deep "inside" reading against the far side of an open part means nothing. Body parts come from bone names
(`quality_score.parts`: arm, leg, torso, head, tail, wing, with sides and front/back), each vertex going with its
heaviest bone.

The audit's tear counts are not scored: its arm-raise bends fail nearly every model, people and animals that look
right included. Its bleed and head share are.

## Calibration (2026-10-18)

Against the Tornado Titans models already checked by eye, measured on VM 207:

| model | by eye | score | grade | what costs |
|---|---|---|---|---|
| Cow_Angus, Cow_Hereford, Cow_Holstein, Cow_Holstein_Tripo, Cow_Jersey | good | 98.9-100 | PASS | |
| Goat_Alpine, Horse_Bay, Sheep | good | 97.4-100 | PASS | |
| Dog_BlackTan, Dog_TanWhite | good | 94.3, 94.8 | PASS | foot skate 101-105 |
| Laree_Age32, Ice_Age26 | good | 93.8, 91.4 | PASS | arms in the body 33-35% |
| Ice_Age16, Leon_Age10, Radar_Age17 | good | 80.0 | CHECK | arms in the body 74-93% |
| Cat_Tabby | weaker | 78.0 | CHECK | stretch 10.6 per thousand, legs crossing 37% |
| Tests/test_person (robe) | bad | 64.9 | FAIL | the rest is 14.3% off the sculpt (p99), stretch 6.8, max stretch 55-67x |

What the calibration found:

- **Ice_Age16's forearms go into its jacket** in idle and walk (the front view loses the hands; `sheet_idle.png`). These
  are the A-pose humanoid rigs of 2026-10-18 (asset-packs #39), not the Mixamo rigs checked by eye before, so the three
  CHECKs are a real finding, not a calibration miss. Leon's walk looks close to fine: arms that hang tight against
  clothing read high, which is what CHECK is for.
- **Foot skate does not separate good from bad.** The accepted dogs skate 101-105, the cows 34-61, the weaker cat 23,
  the robe 11. The procedural quadruped walks really do slide: a dog's planted hind foot moves back in uneven steps
  (0.007 to 0.155 of the size a sample) where a planted foot would move evenly, and the swing barely lifts. It is
  weighted to cost a few points, and the table shows it so the walks can be fixed against it.
- **Stretch** separates: people and animals that look right stay under 5.5 per thousand, the cat is at 10.6. The worst
  edges on the cows are the brisket (spine_3 / spine_5), the same spot as the Hereford spike fixed in M9.
- **Rest against the source** is the clearest signal for a broken rig: animals stay under 0.15% (p99), people resting in
  their modelled A-pose with mirrored arms 2.4-4.7%, the robe 14.3%.

The arm and leg proxies are proxies: they read the cat and the robe correctly, but a clothed person's arms read
between 12 and 93% with no fault visible at 12 and a clear one at 93. Look at the sheets before acting on a CHECK.

## Auto's route

Auto (`m2m-rig --template auto`, the page's Auto tile) sends each model one of three ways: the humanoid rig for
people, a Mesh2Motion template for creatures, the classic rig (rerig.py and make_clips.py) for props, floaters and
machines. `core/suggest.py` reads an archetype off the tips and the body plan, then `core/m2m_templates.choose()`
reads the body plan again and overrules the archetype where the body plainly disagrees.

```bash
python autorig/cli/route_eval.py [--show wrong|all]     # Auto's route on every saved model, against the truth
blender -b --python autorig/steps/route_features.py -- --out <dir> model[,model...]   # save a model's features
```

`tests/data/route_truth.json` is the route the committed rigs of Tornado Titans, Heartroot and Smeltdown took (the
barns' M2M shark rigs, an earlier Auto's mistake, are marked classic), with a reason each and, where two routes are
both fine (a manta ray: shark or classic), the other one. `tests/data/route_features/` holds what Auto reads off each
source (tips, bounds, body plan, measures), so `route_eval.py` replays the choice in plain Python in a second;
`tests/test_route_choice.py` keeps the accuracy from slipping.

Two body-plan measures (core/body_plan.py) do most of the work:

- **flat**: the share of the surface on flat faces square to the axes. Sculpted animals and people stay under 0.14;
  buildings, vehicles, machines and signs have a median of 0.38. Over 0.15 is a prop, unless it stands on four legs
  with 0.25-0.5 of its height clear under its belly (a flat-plated hound).
- **belly**: how high the underside clears the ground along the middle. Animals on legs 0.2-0.41, vehicles low,
  buildings and lying bodies 0. It also tells a rooster (0.25) from a person (about 0), and a hen from a fish resting
  on its fins.

Besides those: one ground patch or none, not long, not person-tall is a plant, a floater or an object; a body on
three or more feet is not lying down however much of its footprint touches (the sitting hopper was a shark); a
"winged" body on two feet standing 1.4 or taller with no tail is a person holding its arms out (the ticket taker, the
robots); a "humanoid" on anything but two feet is a person only when robed-tall (2.0) with little air under the middle.

| 2026-10-18, 250 models | route | template |
|---|---|---|
| before | 117 (47%) | 106 (42%) |
| after | 235 (94%) | 230 (92%) |
| people (37) | 32 -> 37 | 32 -> 37 |
| creatures (63) | 57 -> 61 | 49 -> 57 |
| props (150) | 28 -> 137 | 25 -> 136 |

Still wrong: generated plants and produce that read as animals (Potato_Single and Corn_DryHarvest as fish and snake,
Potato_Tubers and Wheat_Green as legs, Wheat_Golden as a person), a carport and a bomber read as fliers, three
Smeltdown machines on many feet (nettle_hive, spitter, crystal_formation) read as spiders, Truck_Rally_Tripo, the
fallen tree as a fish; among creatures, the dragon (octopod), cogbug (dragon), dirt_creator (horse), sky_boss
(classic), Horse_Bay_Rigged_Tripo (its source lies on its side) and Cow_Holstein (fox: tail reach 0.60, inside the
band, the quadrupeds' measure). The limits were set on this same set, so a new collection will do somewhat worse;
add its models to the truth when they are rigged.
