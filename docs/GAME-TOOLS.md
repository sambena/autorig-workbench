# Game tools: generated models made ready for a game

Four headless Blender steps that take a generator's model (Rodin, Tripo, Meshy) to what a game imports. They came
from importing Tornado Titans' Tripo batch (Sam, 2026-10-17: the tools live here, not in the game's repository), and
every engine can use them: they write FBX in metres, foot on the ground, facing -Y in Blender (+Z in Unity).

| Command | Step | For |
|---|---|---|
| `run.py game-prop` | `steps/game_prop.py` | a prop, building or vehicle: one FBX with LOD0-2 |
| `run.py game-parts` | `steps/game_parts.py` | a game-prop FBX whose moving parts a game animates: each its own mesh |
| `run.py tripo-clips` | `steps/tripo_clips.py` | an animal Tripo rigged: its own skeleton and skin, with clips |
| `run.py crop-cards` | `steps/crop_cards.py` | plants a field draws by the thousand: flat picture cards |

## game-prop

```
python autorig/cli/run.py game-prop --in barn.glb --name Barn --length 18 --out <folder>
        [--height 9] [--front -y|+y|+x|-x] [--triangles 30000] [--lods 0.4,0.15] [--wind] [--carve-wheels | --wheels]
```

- `--front` is the way the model faces as it comes; it is turned to face -Y. (Before 2026-10-17 the turn was silently
  ignored for every GLB, since a glTF import leaves objects in quaternion mode, and +x and -x were swapped; Model
  Studio's vehicle port had the same table and is fixed with it.)
- `--length` sizes the longest footprint side, `--height` the height.
- A GLB's colour and normal maps are saved as `<Name>.fbm/<Name>_basecolor.png` and `_normal.png` and wired straight
  into the material: FBX's exporter does not follow glTF's node graph and dropped the colour map.
- `--carve-wheels`: a truck generated in one piece (Tripo's are thousands of fused fragments) has its tyres cut out.
  Each ground contact is a tyre; the chord of its circle a little above the ground, from the mesh's edges (so a
  low-poly tyre works), gives its radius; an axle's two tyres share their place and size; a corner hidden by a low
  door is mirrored from the other side; a second rear axle becomes `Wheel_RL2`/`Wheel_RR2`. When a corner cannot be
  told from the body (the Peanut's tyres sit up under its bumper) the FBX carries an empty `Wheels_Baked` instead, so
  a game can hide its own wheels rather than show two sets. `--wheels` takes a truck already split into parts.
- `--wind`: height-based vertex alpha for a wind shader (trees, bushes).

## game-parts

```
python autorig/cli/run.py game-parts --in Pumpjack.fbx --spec autorig/specs/tornado_titans_parts.json --out <folder>
        [--name Pumpjack] [--renders <folder>] [--survey]
```

A generated model is one mesh, often thousands of loose fragments, so nothing on it can move. This takes the FBX
game-prop wrote and a JSON spec of named parts, and writes the FBX again with each part cut out of `<Name>_LOD0` as
its own mesh object, `<Name>_<Part>` (`Pumpjack_Beam`, `Windpump_Fan`), beside the LODs under the root, its origin
at the part's pivot. LOD1 and LOD2 stay whole (far off, nothing needs to move), so an engine adds the parts to its
LOD0 renderers itself. Export is game-prop's (-Z forward, Y up, unit scale applied, no leaf bones); the maps stay in
`<Name>.fbm` beside the FBX (copied there when `--out` is another folder) and the FBX names them there. Run on its
own output, the parts are joined back into LOD0 first, so a spec can be tuned and run again.

A part takes whole fragments (connected components; a face is never split) whose centre lies in one of its regions
and in none of its `exclude` regions; the first part listed that takes a fragment keeps it. Coordinates are
Blender's: metres, Z up, front -Y.

```json
{"Windpump": {"parts": [
  {"name": "Fan", "disc": {"centre": [0.02, -1.4, 7.645], "axis": "y", "radius": 2.7, "slab": [-0.48, 0.36]},
   "exclude": [{"box": {"min": [-3, -2, 0], "max": [3, -0.9, 5.5]}}],
   "max_size": [9, 9, 1.6], "pivot": [0.02, -1.4, 7.645]}]}}
```

- a region is `"box": {"min", "max"}` or `"disc": {"centre", "axis" (x, y, z or a vector), "radius", "slab": [from,
  to] along the axis}`; several go in `"regions": [...]`;
- `max_size: [dx, dy, dz]` leaves out a fragment bigger than that (a tower's legs run up behind a fan: long where
  its blades are short);
- `pivot` is a point, or `"centre"`: the middle of what the part took (for a disc, across its axis only, unless
  `"recentre_axis": true`). A lopsided generated fan is best given its hub as a point.

`--survey` writes every fragment's centre, bounds and face count to `<renders>/<Name>_fragments.json`, for finding
the numbers; `--renders` draws the model before (textured) and after (each part its own colour), the model left
behind, and each part alone, from the side and the front over a metre grid whose axes are drawn thicker. Check
those before shipping a cut: everything of the part in it, and nothing that should stay.

`autorig/specs/tornado_titans_parts.json` holds Tornado Titans' five (Rodin's and Tripo's pumpjacks and three
windpumps): pumpjack Beam (walking beam and horse head, pivot at the bearing), Rod (bridle and polished rod under the
head), Link (Rodin's slanted rear frame, pivot at its foot so the game can stretch it to follow the tail) or Pitman
(Tripo's arms, pivot at the tail), and windpump Fan (pivot at the hub; the tail vane, gearbox and tower stay).

## tripo-clips

```
python autorig/cli/run.py tripo-clips --in Dog_rigged.glb --name Dog --height 0.62 --out <folder> [--triangles 15000] [--front +x]
```

Tripo's animals are fragments, so re-skinning them (`rig.json` kind `tripo`) tears them apart, while Tripo's own
weights hold. This keeps Tripo's skeleton and skin and keys **idle, walk, trot and gallop** on its leg chains
(`<n>_Left_Limb_0..`; the pair nearer the head are the front legs; two legs make a biped). The FBX imports as a Generic
rig with the clips as takes (`<Name>|walk`).

The GLB is mended first by `core/tripo_glb.py` (plain Python, `tests/test_tripo_glb.py`):

- NaN numbers in its JSON and NaN texture coordinates become 0;
- a skeleton whose joints all sit at the origin, its rest pose only in the inverse bind matrices (Tripo's horse), gets
  that rest pose back, joint by joint: world = inverse(bind), local = inverse(parent world) x world.

When most of the bind matrices themselves are garbage (Tripo's cat, rabbit and goat of 2026-10-17, unchanged on a
second download), nothing can recover the rig: use the static model, or rig it afresh.

## crop-cards

```
python autorig/cli/run.py crop-cards --out <folder> --card corn_green_front=Corn_MatureGreen.glb:0:0.45 --card wheat_gold=Wheat_Golden.glb
```

`--card <name>=<model>[:<view degrees>[:<wide>]]` writes `<name>.png`: the plant rendered flat (its own colour, no
light; the game lights the card) on transparency, its foot at the bottom middle, 1024 px tall and `wide` times as wide.
A card a game draws on a quad narrower than it is tall but keeps in a square atlas cell is rendered that narrow, so the
atlas stretches it square and the quad squeezes it back.
