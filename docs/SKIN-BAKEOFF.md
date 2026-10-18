# Skinning bake-off: Mesh2Motion's skinning against ours (M2b), and the fade that followed (M9)

2026-10-18. Every model was fitted to its Mesh2Motion template once by `steps/m2m_rig.py`, then skinned both ways
on that same skeleton (the second run takes the first run's joints through `--joints`; the heads match exactly):

- **autorig**: `m2m_rig.skin()`. Bone heat on a watertight voxel copy, carried back by nearest face, then the
  nearest-bone gate, six smoothing passes and, for legs, the side and tail rules, the confine below the belly and the weld cut. Girdle bones carry no skin.
- **m2m**: Mesh2Motion's own solver, ported faithfully in `core/m2m_skin.py`. It matches the TypeScript exactly on
  Mesh2Motion's samples (`scripts/m2m_skin_parity.py`). Closest bone midpoint, the human crotch rule, the knuckle fix,
  3-ring torso smoothing, and limbs that blend toward the child only. The arm and head planes stay off, as they are
  by default in the app.

Each result was graded by `steps/audit.py` and its GLB checked by `steps/clip_check.py` on three of the template's
clips (idle, walk and one more). The models are Mesh2Motion's own samples plus two asset-packs models per template
where one exists. The asset-packs ones are mostly messy Tripo meshes. No asset-packs model is a kaiju, so that
template has only its sample.
Re-run on the box with `python3 scripts/skin_bakeoff.py --out ~/bakeoff`. The full numbers, per clip, are in
[skin-bakeoff-results.json](skin-bakeoff-results.json).

## M9 (2026-10-18): the fade, clip bends and heads

M2b's "Being honest" list below, worked in order and measured with `scripts/skin_bakeoff.py` before and after each
change. The before column is main as of #123 (the cow's calibrated limbs, side and tail rules, weld cut), run the
same day on the same box; the after column is this branch. Full numbers: [skin-bakeoff-results.json](skin-bakeoff-results.json).

**Grades, our skinning, 25 models: before 0 PASS, 3 CHECK, 22 FAIL; after 5 PASS, 3 CHECK, 17 FAIL.** Two of the
passes are asset-packs models (the Tripo horse and the megalodon); the Tripo shark is CHECK. Tears in the audit's
poses fell from 13498 combined and 5389 single-bend to 4737 and 531, and the tears in the played clips (which the
audit does not grade) fell on most models (the human sample is the exception, below).

### What changed

1. **Buried joints and tails: the fade** (`core/skin_fade.py`, `steps/m2m_rig.skin`). The tears were not at the
   joints. A bend of bone j moves a vertex by about its distance from j's head times F_j, the weight it takes from j
   and everything below it, so far from a joint a small step in F_j tears: bending the hen's spine_1 tore her tail
   0.6 of the model away, where the tail took 5-20% from the wings in steps (the gate's cuts and the four-influence
   limit). Each F_j is now made as smooth as a 40-degree bend there needs and no smoother (a Lipschitz envelope over
   the mesh's edges, with the audit's own tear rule as the bound), the bones' weights rebuilt from the F's, and then
   limited to four influences by handing the smallest weight to its bone's parent. The limit cuts steps of its own
   where the fade overlapped many bones (on the hen 59% of vertices had more than four), so the two alternate for 20
   rounds (`--fade-rounds`); each limit gathers weight into fewer bones and each fade needs fewer. The leg confine
   and #123's side and tail rules now run before the fade, so it smooths their cuts too. Snakes and sharks also keep
   each vertex's weight to its near bones each round (a soft gate: all of it within 1.8x the nearest bone's
   distance, none past 3x), which cut their bleed; on the legged templates it brought the tears back, so they go
   without. `--fade 0` is the old ring smoothing.
2. **Clip bends in the audit** (`steps/audit.py`, `steps/m2m_rig.clip_bends`). See "The audit's rule" below.
3. **Heads** (`steps/m2m_rig.py`). The snake's head bone is a 1.3 cm stub with its tip bone 13 cm on: a bone whose
   leaf tip lies straight ahead of it now reaches the tip (same direction, so the same frame and clip turns); the
   snake sample's head share went from 0.3% to 3.0% (PASS). Spider heads reach forward to the front of the body on
   its middle line (`reach_front`): the crab went from 0% to 10.1%, the crawler from 0% to 5.9%. The shark template's
   head joint goes 22% of the length back from the nose instead of the template's 29% (`head_at` in
   `core/m2m_templates.py`): the head share went from 32-41% to 20-21%.
4. **Bleed** fell on the sharks (megalodon 10.76% to 0.45%, the Tripo shark 4.59% to 1.50%) and the dragon sample
   (10.08% to 0), but rose on the legged animals the fade blends most: see "Still failing".

### The audit's rule: bends as far as the clips turn (changed, and why)

The single-bone bend was 40 degrees for every bone and the combined pose 30 on limbs. A shoulder blade, a thigh in
the haunch or a hen's spine turns a few degrees in its clips, so 40 tested poses the model never takes. A rig whose
clips are known now records how far each bone turns in them (`rig.json` `rig.audit_clip_bends`: every frame of every
clip it ships with, times 1.25 headroom, never under 15 degrees, for a game's own turns, and never over 40), and the
audit bends each bone that far, and no further in the combined pose. The grades, their limits and the tear rule are
unchanged, and a rig without the record is tested exactly as before. Re-rig with more clips and it is measured again.
What it buys, honestly: on this branch's skin it took Ice_Age26 from 23 combined tears to 3 and halved the bend
tears of the many-boned models; most of the drop in the tables is the fade's. The fade itself works at 40 degrees
whatever the clips say: fading each bone only for its own clip angle under-smoothed, because the combined pose adds
every bone's turn at once (a person tore 1543 edges).

### Before and after (our skinning)

Clip tears: stretched edges summed over three played clips (each clip's worst frame).

| Model | Before: grade | bleed % | combined / bend | head % | clip tears | After: grade | bleed % | combined / bend | head % | clip tears |
|---|---|---|---|---|---|---|---|---|---|---|
| human_sample | CHECK | 0.00 | 6 / 4 | 6.8 | 33 | CHECK | 0.00 | 1 / 0 | 6.67 | 319 |
| Ice_Age26 | FAIL | 0.01 | 48 / 47 | 7.51 | 360 | FAIL | 0.04 | 3 / 1 | 7.41 | 236 |
| Laree_Age32 | FAIL | 0.00 | 19 / 25 | 10.67 | 305 | FAIL | 0.00 | 3 / 4 | 10.62 | 140 |
| horse_sample | FAIL | 0.00 | 4 / 4 | 4.19 | 42 | PASS | 0.09 | 0 / 0 | 3.98 | 10 |
| Horse_Bay | FAIL | 0.09 | 31 / 20 | 4.16 | 300 | PASS | 0.14 | 0 / 0 | 3.55 | 164 |
| Cow_Holstein | FAIL | 0.83 | 168 / 83 | 3.98 | 466 | FAIL | 4.27 | 30 / 4 | 2.88 | 179 |
| fox_sample | FAIL | 0.00 | 15 / 4 | 4.33 | 25 | PASS | 0.00 | 0 / 0 | 4.03 | 5 |
| Dog_BlackTan | FAIL | 0.16 | 413 / 184 | 3.88 | 1087 | FAIL | 1.47 | 19 / 3 | 4.63 | 568 |
| Cat_Tabby | FAIL | 0.00 | 531 / 218 | 4.01 | 1255 | FAIL | 6.32 | 274 / 55 | 3.52 | 1183 |
| bird_sample | FAIL | 1.67 | 15 / 14 | 3.15 | 34 | CHECK | 0.78 | 2 / 2 | 3.12 | 19 |
| Chicken_Hen | FAIL | 3.37 | 1751 / 611 | 16.79 | 2140 | FAIL | 6.06 | 571 / 58 | 3.1 | 1841 |
| Chicken_Rooster | FAIL | 2.26 | 3230 / 1381 | 1.82 | 3289 | FAIL | 4.55 | 1531 / 168 | 2.22 | 951 |
| dragon_sample | FAIL | 10.08 | 4 / 2 | 0.59 | 4 | FAIL | 0.00 | 0 / 0 | 0.63 | 4 |
| Dragon | FAIL | 6.32 | 3791 / 806 | 0.27 | 3290 | FAIL | 10.01 | 1211 / 161 | 0 | 1246 |
| Drake | FAIL | 2.23 | 686 / 456 | 2.66 | 1137 | FAIL | 3.80 | 389 / 42 | 2.61 | 402 |
| kaiju_sample | CHECK | 0.00 | 4 / 4 | 1.6 | 9 | FAIL | 0.00 | 0 / 0 | 1.4 | 2 |
| spider_sample | CHECK | 1.45 | 0 / 0 | 1.93 | 0 | FAIL | 1.06 | 0 / 0 | 1.14 | 0 |
| Crawler | FAIL | 0.73 | 2051 / 1155 | 0 | 2859 | FAIL | 6.68 | 602 / 11 | 5.9 | 507 |
| reef_crab | FAIL | 2.08 | 658 / 350 | 0 | 972 | FAIL | 2.27 | 100 / 14 | 10.12 | 311 |
| snake_sample | FAIL | 0.00 | 0 / 0 | 0.32 | 0 | PASS | 0.00 | 0 / 0 | 3.0 | 0 |
| glass_eel | FAIL | 1.27 | 5 / 3 | 0 | 105 | FAIL | 0.27 | 0 / 0 | 0.76 | 3 |
| sea_serpent | FAIL | 0.43 | 1 / 1 | 0 | 27 | FAIL | 0.32 | 0 / 0 | 0.15 | 7 |
| shark_sample | FAIL | 1.29 | 30 / 1 | 31.87 | 0 | FAIL | 4.56 | 0 / 1 | 21.21 | 1 |
| Shark | FAIL | 4.59 | 35 / 14 | 39.52 | 37 | CHECK | 1.50 | 1 / 7 | 19.85 | 0 |
| megalodon | FAIL | 10.76 | 2 / 2 | 40.95 | 1 | PASS | 0.45 | 0 / 0 | 20.48 | 0 |

### Still failing (being honest)

- **17 of 25 FAIL.** The Tripo people fail on the standing pose's folds (Ice_Age26 63, Laree_Age32 52 per 10k
  triangles; the limit is 8, CHECK to 20), not on tears (3 each). The Tripo birds, the Smeltdown dragon, drake and
  crawler, the cat and the dog still tear (hen 571 combined, rooster 1531, dragon 1211, crawler 602, drake 389, cat
  274), and the many-boned ones bleed more than before: cat 0 to 6.3%, hen 3.4 to 6.1%, rooster 2.3 to 4.6%, dragon
  6.3 to 10.0%, drake 2.2 to 3.8%, crawler 0.7 to 6.7%, cow 0.8 to 4.3%. The bleed is mostly near-ties where parts
  touch (the cat's right thigh, left lower leg and tail at 35/35/30); the gate that removes it brings the tears back.
- **Two samples got worse:** the kaiju and spider samples went from CHECK to FAIL on the head share alone (1.6% to
  1.4%, 1.9% to 1.1%): the fade blurs a small head into its neck. The human sample stays CHECK but tears more in its
  played clips (33 to 319; the Tripo people tear less: 360 to 236, 305 to 140).
- **The hen and rooster are noisy:** two runs that differed only in float rounding gave the hen 909 and 689 combined
  tears and the rooster 1125 and 1522; the limit's choices are chaotic on those meshes.
- **Slower:** the skinning step went from 79 s over all 25 models to 655 s (the slowest, the Smeltdown dragon, 78 s).
  `--fade-rounds 10` is about half that, and somewhat worse on the birds and dragons.
- The eel and the sea serpent still have almost no head share (0.8%, 0.1%).
- Foot skate was left alone, as planned: it is the fit's and the clips'.

### What did not work

Each was measured over the whole bake-off and dropped: a diffusion whose width grows with the distance from the
joint (it blurred the limbs' own skin); measuring the reach from each bone's bend axis instead of its head (a
person's spine went unfaded along the arms: 1033 tears on the human sample); a soft limit taking the fifth weight
off all (it divides by little where many bones overlap alike); moving weight up the chain in rounds inside the limit
(the cascade multiplied small moves); dropping the influence whose loss costs least by distance; and the hard gate
every round (bleed near 0, tears back to 20459).

### Full table, both methods

The m2m rows are Mesh2Motion's skinning on the same fitted joints, graded by the same audit (with clip bends).

| Model | Template | Method | Grade | Bleed % | Combined / bend tears | Head % | Stand folds | Clip tears / worst skate | Skin s |
|---|---|---|---|---|---|---|---|---|---|
| human_sample | human | autorig | CHECK | 0.00 | 1 / 0 | 6.7 | 14.5 | 319 / 7.14 | 11.2 |
| human_sample | human | m2m | FAIL | 3.51 | 38 / 39 | 4.5 | 8.7 | 653 / 7.21 | 0.5 |
| Ice_Age26 | human | autorig | FAIL | 0.04 | 3 / 1 | 7.4 | 62.7 | 236 / 4.04 | 32.5 |
| Ice_Age26 | human | m2m | FAIL | 1.02 | 242 / 145 | 5.5 | 171.7 | 2473 / 4.23 | 0.6 |
| Laree_Age32 | human | autorig | FAIL | 0.00 | 3 / 4 | 10.6 | 52.0 | 140 / 3.62 | 24.6 |
| Laree_Age32 | human | m2m | FAIL | 1.53 | 262 / 287 | 7.3 | 146.6 | 1781 / 4.41 | 0.7 |
| horse_sample | horse | autorig | PASS | 0.09 | 0 / 0 | 4.0 | - | 10 / 27.84 | 1.5 |
| horse_sample | horse | m2m | FAIL | 1.61 | 55 / 16 | 3.3 | - | 65 / 28.07 | 0.1 |
| Horse_Bay | horse | autorig | PASS | 0.14 | 0 / 0 | 3.5 | - | 164 / 29.15 | 22.8 |
| Horse_Bay | horse | m2m | FAIL | 0.52 | 910 / 332 | 2.6 | - | 1504 / 29.03 | 0.5 |
| Cow_Holstein | horse | autorig | FAIL | 4.27 | 30 / 4 | 2.9 | - | 179 / 44.57 | 35.3 |
| Cow_Holstein | horse | m2m | FAIL | 0.30 | 960 / 207 | 4.3 | - | 1561 / 45.55 | 0.6 |
| fox_sample | fox | autorig | PASS | 0.00 | 0 / 0 | 4.0 | - | 5 / 39.02 | 1.4 |
| fox_sample | fox | m2m | FAIL | 5.08 | 19 / 4 | 5.0 | - | 58 / 39.46 | 0.1 |
| Dog_BlackTan | fox | autorig | FAIL | 1.47 | 19 / 3 | 4.6 | - | 568 / 55.11 | 36.8 |
| Dog_BlackTan | fox | m2m | FAIL | 1.91 | 749 / 130 | 2.3 | - | 1002 / 53.33 | 0.5 |
| Cat_Tabby | fox | autorig | FAIL | 6.32 | 274 / 55 | 3.5 | - | 1183 / 23.21 | 47.6 |
| Cat_Tabby | fox | m2m | FAIL | 1.88 | 819 / 157 | 2.8 | - | 1188 / 21.17 | 0.5 |
| bird_sample | bird | autorig | CHECK | 0.78 | 2 / 2 | 3.1 | - | 19 / 23.16 | 3.1 |
| bird_sample | bird | m2m | FAIL | 1.78 | 26 / 4 | 2.4 | - | 151 / 23.16 | 0.1 |
| Chicken_Hen | bird | autorig | FAIL | 6.06 | 571 / 58 | 3.1 | - | 1841 / 82.00 | 69.8 |
| Chicken_Hen | bird | m2m | FAIL | 2.10 | 875 / 135 | 7.1 | - | 1821 / 81.67 | 0.5 |
| Chicken_Rooster | bird | autorig | FAIL | 4.55 | 1531 / 168 | 2.2 | - | 951 / 28.28 | 70.9 |
| Chicken_Rooster | bird | m2m | FAIL | 3.31 | 659 / 106 | 3.5 | - | 1337 / 33.42 | 0.5 |
| dragon_sample | dragon | autorig | FAIL | 0.00 | 0 / 0 | 0.6 | - | 4 / 12.15 | 6.0 |
| dragon_sample | dragon | m2m | FAIL | 3.47 | 57 / 10 | 1.5 | - | 80 / 12.10 | 0.2 |
| Dragon | dragon | autorig | FAIL | 10.01 | 1211 / 161 | 0 | - | 1246 / 5.33 | 77.7 |
| Dragon | dragon | m2m | FAIL | 10.77 | 1408 / 103 | 4.3 | - | 2027 / 7.84 | 0.5 |
| Drake | dragon | autorig | FAIL | 3.80 | 389 / 42 | 2.6 | - | 402 / 14.92 | 58.7 |
| Drake | dragon | m2m | FAIL | 5.37 | 898 / 118 | 3.0 | - | 911 / 1.05 | 0.5 |
| kaiju_sample | kaiju | autorig | FAIL | 0.00 | 0 / 0 | 1.4 | - | 2 / 10.29 | 1.2 |
| kaiju_sample | kaiju | m2m | FAIL | 1.80 | 33 / 10 | 2.1 | - | 72 / 10.33 | 0.1 |
| spider_sample | spider | autorig | FAIL | 1.06 | 0 / 0 | 1.1 | - | 0 / 139.84 | 3.8 |
| spider_sample | spider | m2m | FAIL | 7.20 | 15 / 2 | 2.8 | - | 11 / 139.84 | 0.1 |
| Crawler | spider | autorig | FAIL | 6.68 | 602 / 11 | 5.9 | - | 507 / 51.21 | 54.1 |
| Crawler | spider | m2m | FAIL | 4.92 | 567 / 88 | 2.7 | - | 1045 / 49.17 | 0.4 |
| reef_crab | spider | autorig | FAIL | 2.27 | 100 / 14 | 10.1 | - | 311 / 56.43 | 14.1 |
| reef_crab | spider | m2m | FAIL | 7.89 | 250 / 62 | 6.6 | - | 465 / 65.16 | 0.2 |
| snake_sample | snake | autorig | PASS | 0.00 | 0 / 0 | 3.0 | - | 0 / - | 34.8 |
| snake_sample | snake | m2m | PASS | 0.00 | 0 / 0 | 4.3 | - | 2 / - | 0.0 |
| glass_eel | snake | autorig | FAIL | 0.27 | 0 / 0 | 0.8 | - | 3 / - | 3.3 |
| glass_eel | snake | m2m | FAIL | 0.00 | 16 / 27 | 4.9 | - | 687 / - | 0.2 |
| sea_serpent | snake | autorig | FAIL | 0.32 | 0 / 0 | 0.1 | - | 7 / - | 3.0 |
| sea_serpent | snake | m2m | FAIL | 0.53 | 2 / 29 | 1.0 | - | 81 / - | 0.3 |
| shark_sample | shark | autorig | FAIL | 4.56 | 0 / 1 | 21.2 | - | 1 / - | 4.8 |
| shark_sample | shark | m2m | FAIL | 8.96 | 11 / 3 | 20.4 | - | 6 / - | 0.2 |
| Shark | shark | autorig | CHECK | 1.50 | 1 / 7 | 19.9 | - | 0 / - | 20.5 |
| Shark | shark | m2m | FAIL | 2.22 | 76 / 91 | 16.5 | - | 99 / - | 0.5 |
| megalodon | shark | autorig | PASS | 0.45 | 0 / 0 | 20.5 | - | 0 / - | 15.1 |
| megalodon | shark | m2m | FAIL | 4.25 | 36 / 98 | 19.8 | - | 96 / - | 0.5 |

## M2b results (before M9)


Columns:
- **Clip tears**: stretched edges summed over the three clips (each clip's worst frame).
- **Worst skate**: the worst foot-skate score of the three clips.
- **Skin s**: the skinning step alone, in seconds.

| Model | Template | Method | Grade | Bleed % | Combined / bend tears | Head % | Stand folds | Clip tears / worst skate | Skin s |
|---|---|---|---|---|---|---|---|---|---|
| human_sample | human | autorig | CHECK | 0.00 | 6 / 4 | 6.8 | 5.8 | 33 / 7.22 | 1.1 |
| human_sample | human | m2m | FAIL | 3.51 | 61 / 39 | 4.5 | 8.7 | 666 / 7.21 | 0.1 |
| Ice_Age26 | human | autorig | FAIL | 0.01 | 48 / 47 | 7.5 | 64.0 | 361 / 4.13 | 1.4 |
| Ice_Age26 | human | m2m | FAIL | 1.35 | 465 / 259 | 5.3 | 260.7 | 3338 / 4.23 | 0.2 |
| Laree_Age32 | human | autorig | FAIL | 0.00 | 19 / 25 | 10.7 | 50.0 | 306 / 3.59 | 1.4 |
| Laree_Age32 | human | m2m | FAIL | 2.06 | 506 / 314 | 7.2 | 242.0 | 2292 / 4.41 | 0.2 |
| horse_sample | horse | autorig | CHECK | 0.00 | 4 / 3 | 4.2 | - | 17 / 27.77 | 0.7 |
| horse_sample | horse | m2m | FAIL | 1.61 | 91 / 34 | 3.3 | - | 65 / 28.01 | 0.0 |
| Horse_Bay | horse | autorig | FAIL | 0.14 | 30 / 21 | 4.2 | - | 264 / 44.35 | 1.1 |
| Horse_Bay | horse | m2m | FAIL | 0.58 | 1624 / 703 | 2.5 | - | 3478 / 46.95 | 0.1 |
| Cow_Holstein | horse | autorig | FAIL | 1.23 | 234 / 143 | 3.9 | - | 924 / 41.58 | 2.3 |
| Cow_Holstein | horse | m2m | FAIL | 0.30 | 1744 / 558 | 4.1 | - | 3458 / 41.80 | 0.1 |
| fox_sample | fox | autorig | FAIL | 0.00 | 17 / 4 | 4.3 | - | 33 / 25.01 | 1.1 |
| fox_sample | fox | m2m | FAIL | 5.08 | 67 / 32 | 5.0 | - | 97 / 35.40 | 0.0 |
| Dog_BlackTan | fox | autorig | FAIL | 0.16 | 591 / 346 | 3.7 | - | 2018 / 58.29 | 1.3 |
| Dog_BlackTan | fox | m2m | FAIL | 1.78 | 2210 / 509 | 2.0 | - | 3829 / 54.78 | 0.1 |
| Cat_Tabby | fox | autorig | FAIL | 1.08 | 1031 / 693 | 3.7 | - | 3685 / 25.90 | 1.0 |
| Cat_Tabby | fox | m2m | FAIL | 3.14 | 2692 / 708 | 2.2 | - | 5804 / 29.86 | 0.1 |
| bird_sample | bird | autorig | CHECK | 1.66 | 9 / 8 | 3.1 | - | 16 / 23.87 | 2.1 |
| bird_sample | bird | m2m | FAIL | 1.77 | 32 / 28 | 2.4 | - | 158 / 23.14 | 0.0 |
| Chicken_Hen | bird | autorig | FAIL | 3.53 | 2869 / 1227 | 15.8 | - | 6117 / 70.63 | 1.9 |
| Chicken_Hen | bird | m2m | FAIL | 4.21 | 2315 / 482 | 7.0 | - | 6612 / 72.86 | 0.1 |
| Chicken_Rooster | bird | autorig | FAIL | 2.43 | 3406 / 1516 | 1.8 | - | 4052 / 31.14 | 0.9 |
| Chicken_Rooster | bird | m2m | FAIL | 5.31 | 1835 / 669 | 3.2 | - | 5391 / 35.58 | 0.1 |
| dragon_sample | dragon | autorig | FAIL | 9.84 | 4 / 2 | 0.6 | - | 5 / 13.02 | 3.4 |
| dragon_sample | dragon | m2m | FAIL | 3.46 | 89 / 24 | 1.5 | - | 102 / 12.73 | 0.0 |
| Dragon | dragon | autorig | FAIL | 6.10 | 4388 / 880 | 0.3 | - | 5520 / 0.11 | 1.1 |
| Dragon | dragon | m2m | FAIL | 12.53 | 3907 / 1242 | 3.9 | - | 11235 / 9.13 | 0.1 |
| Drake | dragon | autorig | FAIL | 2.27 | 948 / 708 | 2.6 | - | 2659 / 7.20 | 1.2 |
| Drake | dragon | m2m | FAIL | 5.29 | 2228 / 850 | 2.7 | - | 5237 / 16.16 | 0.1 |
| kaiju_sample | kaiju | autorig | CHECK | 0.72 | 4 / 4 | 1.6 | - | 11 / 10.26 | 0.6 |
| kaiju_sample | kaiju | m2m | FAIL | 1.80 | 55 / 15 | 2.1 | - | 61 / 10.12 | 0.0 |
| spider_sample | spider | autorig | CHECK | 1.45 | 0 / 0 | 1.9 | - | 0 / 140.98 | 3.4 |
| spider_sample | spider | m2m | FAIL | 7.20 | 28 / 10 | 2.8 | - | 11 / 139.84 | 0.0 |
| Crawler | spider | autorig | FAIL | 0.80 | 2354 / 1235 | 0 | - | 3943 / 51.97 | 2.0 |
| Crawler | spider | m2m | FAIL | 6.30 | 2981 / 876 | 2.4 | - | 9172 / 48.83 | 0.1 |
| reef_crab | spider | autorig | FAIL | 2.12 | 804 / 395 | 0 | - | 1484 / 54.49 | 2.7 |
| reef_crab | spider | m2m | FAIL | 8.33 | 1538 / 626 | 5.8 | - | 3899 / 64.34 | 0.1 |
| snake_sample | snake | autorig | FAIL | 0.00 | 0 / 0 | 0.3 | - | 0 / - | 31.0 |
| snake_sample | snake | m2m | PASS | 0.00 | 0 / 0 | 3.9 | - | 2 / - | 0.0 |
| glass_eel | snake | autorig | FAIL | 1.35 | 5 / 3 | 0 | - | 125 / - | 0.4 |
| glass_eel | snake | m2m | FAIL | 0.00 | 17 / 27 | 4.7 | - | 714 / - | 0.0 |
| sea_serpent | snake | autorig | FAIL | 0.43 | 1 / 1 | 0 | - | 27 / - | 0.5 |
| sea_serpent | snake | m2m | FAIL | 0.53 | 3 / 29 | 1.2 | - | 81 / - | 0.0 |
| shark_sample | shark | autorig | FAIL | 1.29 | 30 / 1 | 31.9 | - | 0 / - | 2.2 |
| shark_sample | shark | m2m | FAIL | 6.72 | 46 / 93 | 30.4 | - | 73 / - | 0.0 |
| Shark | shark | autorig | FAIL | 4.61 | 35 / 14 | 39.5 | - | 37 / - | 1.3 |
| Shark | shark | m2m | FAIL | 6.26 | 149 / 115 | 22.1 | - | 45 / - | 0.1 |
| megalodon | shark | autorig | FAIL | 10.76 | 2 / 2 | 41.0 | - | 1 / - | 1.2 |
| megalodon | shark | m2m | FAIL | 10.75 | 30 / 38 | 29.0 | - | 3 / - | 0.1 |

## Verdict: ours stays the default on every template

| Template | Default | Why |
|---|---|---|
| human | autorig | The sample is CHECK against FAIL. On the two Tripo people, Mesh2Motion's skin tears about 10x more (465 and 506 combined against 48 and 19), and folds about 4x more when standing. |
| horse | autorig | The sample is CHECK against FAIL. On the horse and the cow, 7-50x the combined tears. Mesh2Motion bleeds less only on the cow (0.30% against 1.23%). |
| fox | autorig | Fewer tears and less bleed on all three models. |
| bird | autorig | Mixed. Mesh2Motion tears less in the audit's poses on the hen and the rooster (2315 against 2869, 1835 against 3406). It bleeds more on all three models, though, and tears more in the played clips (6612 against 6117, 5391 against 4052). The sample is CHECK against FAIL. Both methods are far off a pass on real birds. |
| dragon | autorig | Fewer tears on the sample and the drake; on the big Smeltdown dragon both tear about 4000. Ours bleeds more on the sample (9.8% against 3.5%) and the dragon's head share is too low (0.3-0.6%). |
| kaiju | autorig | Sample only: CHECK against FAIL. |
| spider | autorig | Sample CHECK (0 tears) against FAIL; less bleed on the real ones. Ours gives the head 0% on the crawler and the crab. |
| snake | autorig | Mesh2Motion passes the sample and ours fails it, but only on the head share. The snake's head bone is a stub, so bone heat gives it almost nothing. On the eel and the sea serpent ours tears less (5 against 17, 1 against 3 combined) and tears less in the clips. The head share is a fit fix, not a reason to switch. |
| shark | autorig | Fewer tears on all three. Both give the head 22-41% of the surface. |

Mesh2Motion's skinning is 10-20 times faster (0.0-0.2 s against 0.4-3.4 s; the snake sample's bone heat took 31 s).
No template was a tie, so the tie rule (ties go to Mesh2Motion's, the faster) never applied.
The browser's "Use M2M skinning" path loses on every template, so per the plan (P2b) P5 drops it.

## Being honest: both methods FAIL most real meshes

Only the Mesh2Motion samples reach CHECK, and only with ours. Mesh2Motion's skinning passes the snake sample alone.
Every asset-packs model FAILs both ways. The worst are Tripo's birds and Smeltdown's dragon and crawler, with
2000-4400 combined tears either way. Closest-bone skinning isn't the fix. Its hard boundaries tear more, and its
one-ring blends smear across Tripo's loose fragments. The next skinning work should go on ours (M9, above, worked
through this list; skin-bakeoff-results.json now holds M9's numbers, M2b's are in its git history):

1. **The animals' bend tears.** The tears come from the 40-degree single-bone bends on bones buried in the body
   (girdles, the shoulder and the thigh) and on tails (the hen 2869, the cat 1031, the dog 591). They need a fade
   of weight across those joints, or a bend test that reflects how far those bones actually turn in the clips.
2. **Heads.** On snakes the head share is 0-0.3% because the fitted head bone is a stub (give it the head's
   length). On spiders and crabs it is 0%, and on sharks 30-41% (the line fit's head reaches too far back).
3. **Bleed.** Ours bleeds over 2% on the megalodon (10.8%), the dragon sample (9.8%), the Smeltdown dragon (6.1%), the
   shark (4.6%), the hen (3.5%), the rooster (2.4%), the Drake (2.3%) and the crab (2.1%).
4. **Foot skate** is the same under both skins (25-58 on quadrupeds, 141 on the spider sample), so it comes from
   the fit and the clips, not the skin. It belongs to M2's clip work.

Not done here: skin and bend pictures per model (`audit.py` without `-render 0` makes them), and more models per
template.
