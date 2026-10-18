# Skinning bake-off (M2b): Mesh2Motion's skinning against ours

2026-10-18. Every model was fitted to its Mesh2Motion template once by `steps/m2m_rig.py`, then skinned both ways
on that same skeleton (the second run takes the first run's joints through `--joints`; the heads match exactly):

- **autorig**: `m2m_rig.skin()`. Bone heat on a watertight voxel copy, carried back by nearest face, then the
  nearest-bone gate, six smoothing passes and, for legs, the confine below the belly. Girdle bones carry no skin.
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

## Results

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
one-ring blends smear across Tripo's loose fragments. The next skinning work should go on ours:

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
