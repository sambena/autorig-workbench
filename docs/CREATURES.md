# Creatures on Mesh2Motion's skeletons (M11)

The creatures that are neither people nor four-legged animals: birds, dragons and fliers, fish and sharks, eels and
serpents, crabs, insects and spiders. Each fits one of Mesh2Motion's templates (`steps/m2m_rig.py`): bird, dragon,
shark, snake, spider, kaiju. Bodies with no template (jellies, squid, drones, wisps, blobs, chests) keep the old path
(`rerig.py`, `make_clips.py`). Their spec comes from `suggest.py`, and they were not changed here (below, "Still to do").

## What changed (2026-10-19)

- **Spider family: the legs are found on the body** (`fit_spider_legs`, `limb_tips`, `place_chain`). The template's
  legs were set by its own body's proportions, slice by slice, which put a crab's leg bones in the air over its shell
  and its abdomen under the floor. Now each limb tip off the centre plane is followed in to where it meets the body
  (`geo.Surface.junction`) and down its centre line (`medial_axis`). A leg's joints go by the template's bone
  proportions, its knee on the line's sharpest corner. Limbs are matched across the centre plane (generated bodies
  are symmetric, the tip search is not), and one tip is kept per limb. A pair with no limb leaving the body (a shell's
  rim) or under half as long as the standing legs (a spike) is dropped. Per side, front to back, the legs take
  the template's a-d: three pairs a, b, c (a tripod gait), two pairs a, d (a trot). A thick pair reaching forward (a
  crab's claws) goes on the fangs, so a bite closes them. A template leg with no match is folded inside the body, with
  no skin. The body's own bones go on its centre line.
- **Spider family: feet planted by IK** (`plant_legs`). Turning each leg bone as the template's turns drove legs that
  stand at another angle through the floor and across it. Now each frame, each foot follows the template foot's
  path from its own rest, scaled by its leg length against the template leg's, and the leg is solved onto it by FABRIK
  (`core/limb_ik.py`) from its own rest under the body, so the knee stays on its side.
- **Birds face by their toes** (`toes_point`). With the template named (the Bird tile), the old rule put the head at
  the taller end, and a hen's tail stands taller than her head: both chickens came out backwards.
- **Birds walk the template's stride** (`stride_gain`). The same leg turns on a hen's short legs moved her feet 0.5 of
  her hip height a step against the template's 1.1 ("the walk barely moves"). Each walking clip is rebaked once on a
  copy, its feet's sweep measured, and the leg turns scaled to match (1 to 1.6) before the real rebake, so the feet
  still land. `--no-stride` turns it off.
- **Folded wings flap less** (`--folded-flap`, 0.35). A bird standing on its toes whose wings reach out of its body less
  than 0.4 of its height (a hen 0.29, a rooster 0.22) flaps them that much, since its wings are a patch of its side.
- **Long bodies: the head end, the neck, the heading** (`mesh_line`, `neck_share`, `fit_line`). The head is the end
  that's thick across its narrower way, weighted by the ring's size: by the mean radius, the glass eel's flat tail fin
  won. The line starts where the head does, not at a whisker ahead of it (the sea serpent's head bone was on its
  whisker). A snake's head reaches back to its neck, the narrowest ring behind the thickest in the front third (the
  template's head is a stub). A body is turned by the way its head points, and snapped to the nearest quarter turn when
  its chord or head is within 25 degrees of one (the great white was turned 12 degrees off the way it was modelled).
- **The audit's head share** counts the head bone and everything hung from it, stopping at a chain along the body
  (neck, spine, tail, hips). On the bird and dragon the beak's bones own the face, and a hen whose whole head turned
  with her head bone read 0.7%.

## Before and after

Measured on Penny (the Shark matched VM 207 to the digit), each with its right template, rigged by main (d6c3b0a) and by
this change, both scored by `run.py score --audit run` with the same audit. Score 0-100 (PASS from 90,
docs/QUALITY.md), then the audit's grade and combined tears.

| model | template | score before | after | audit before | after |
|---|---|---|---|---|---|
| reef_crab | spider | 86.6 CHECK | **100 PASS** | FAIL 102 | FAIL 50 |
| the_hermit | spider | 79.4 CHECK | **94.6 PASS** | FAIL 686 | FAIL 17 |
| crawler | spider | 85.9 CHECK | **100 PASS** | FAIL 590 | CHECK 1 |
| dirt_creator | spider | 81.2 CHECK | **96.8 PASS** | FAIL 203 | FAIL 19 |
| gloomcrawler | spider | 82.3 CHECK | **97.8 PASS** | FAIL 433 | FAIL 138 |
| cogbug | spider | 86.2 CHECK | **97.5 PASS** | FAIL 229 | FAIL 30 |
| Chicken_Hen_Tripo | bird | 89.3 CHECK | **100 PASS** | FAIL 522 | FAIL 816 |
| Chicken_Rooster_Tripo | bird | 94.8 PASS | 95.0 PASS | FAIL 1333 | FAIL 1336 |
| candle_moth | bird | 57.3 FAIL | 86.3 CHECK | FAIL 560 | FAIL 194 |
| dragon (Smeltdown) | bird | 94.4 PASS | 98.5 PASS | FAIL 1321 | FAIL 1377 |
| drake | bird | 100 PASS | 100 PASS | FAIL 172 | FAIL 172 |
| sky_boss | dragon | 93.7 PASS | 93.7 PASS | FAIL 177 | FAIL 177 |
| ray | bird | 75.4 CHECK | 75.4 CHECK | FAIL 184 | FAIL 184 |
| glowfish | shark | 80.6 CHECK | **100 PASS** | PASS 0 | CHECK 1 |
| the_greatwhale | shark | 67.0 FAIL | **100 PASS** | CHECK 9 | PASS 0 |
| the_megalodon | shark | 65.0 FAIL | 65.0 FAIL | PASS 0 | PASS 0 |
| gulper | shark | 100 PASS | 98.8 PASS | CHECK 2 | FAIL 20 |
| Shark, Shark_Tripo, snapper | shark | 100 PASS | 100 PASS | PASS / CHECK | PASS / CHECK |
| glass_eel | snake | 65.0 FAIL | 61.5 FAIL | FAIL (head 0.7%) | CHECK (head 10.7%) |
| sea_serpent | snake | 65.0 FAIL | 65.0 FAIL | FAIL (head 0.1%) | PASS (head 17.6%) |
| sea_turtle | horse | 100 PASS | 100 PASS | FAIL (head 0%) | PASS |

The audit's tear counts on the birds and dragons swing from run to run (±30%) and are dominated by the wings' 40-degree
bends; the scorer leaves tear counts out for that reason (docs/QUALITY.md).

## Still failing, and why

- **The template choice** (`m2m_templates.choose`, `suggest.py`, the score-and-choice work): the megalodon, glowfish
  and gulper go to the bird or the human, the eels to the human, the cogbug to the dragon, the jellies, squid and
  drones to the human or the kaiju. The numbers above are with the right template named.
- **Snakes rest straightened** on the template's line by design (`--rest template`), so the scorer's rest-against-source
  check fails them. The rigs themselves hold (audit CHECK and PASS).
- **The megalodon** is modelled heading diagonally, and the scorer lines the rest up with the source in quarter turns
  only.
- **The ray** on the bird: bleed 31% and stretch in its flap. A ray's wing is its body.
- **The candle moth** (CHECK 86): legs crossing in the walk, no head share (its head is a sphere on the thorax).
- **Bodies with no template** (jellies, squid, drones, wisps, blobs, chests, the Smeltdown boss): their specs come from
  `suggest.py`, which calls a jelly a hexapod. Their shipped rigs in asset-packs were built from specs measured by hand.
