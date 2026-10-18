# Generate a model in the workbench

Phase M8 of [PLAN.md](PLAN.md): from a text prompt or a few pictures to a rigged, animated model in as few steps as
possible. The workbench asks [Tripo](https://www.tripo3d.ai), [Meshy](https://www.meshy.ai) or
[Hyper3D Rodin](https://hyper3d.ai) for the model through each one's official HTTP API, downloads it into the open
collection, mends it (`core/tripo_glb.py`), and can go straight on to rig and animate it (`m2m-rig`).

This is the back end. The page's Generate tab comes after M7.

**Generating costs money.** Every generation is priced first, and nothing is spent until a second call confirms that
price. The server can't spend at all until its owner turns generation on, and each provider has a daily and a
monthly cap.

## Two calls: price, then spend

```bash
# 1. The estimate: free, nothing is sent to the provider but a balance check
curl -s -X POST -H "Authorization: Bearer $T" -H "Content-Type: application/json" \
  -d '{"provider": "tripo", "prompt": "a brown and white cow, four legs, standing, side view", "name": "Cow"}' \
  $URL/api/v1/generate
# {"estimate": "est_...", "credits": 20, "unit": "credits",
#  "breakdown": [{"what": "text to model (H3.1)", "credits": 10}, {"what": "standard texture", "credits": 10}],
#  "balance": 480.0, "caps": {...}, "can_spend": true, "reason": "", "expires_in": 900, ...}

# 2. The confirm: spends exactly what was estimated, once
curl -s -X POST -H "Authorization: Bearer $T" -H "Content-Type: application/json" \
  -d '{"confirm": "est_..."}' $URL/api/v1/generate
# 202 {"job": {"id": "gen_...", "state": "submitted", "task_id": "...", "credits_held": 20, ...}}

# 3. Follow it
curl -s -H "Authorization: Bearer $T" $URL/api/v1/generate/gen_...
# {"state": "done", "progress": 100, "model": "cow", "credits_charged": 20.0, "links": {"model": "/api/v1/models/cow"}}
```

States: `submitted`, `queued`, `running`, `downloading`, `done`, `failed`, `timed_out`. A job is saved under the work
folder's `generate_jobs/`, so a paid task's id survives a restart. A task that fails is refunded by the provider and
released from the cap. One that takes longer than `timeout_minutes` (30 by default) can be fetched later at no cost,
by its task id (below).

### Generate, rig and animate in one go

```bash
curl -s -X POST ... -d '{"provider": "meshy", "prompt": "a grey wolf, four legs", "name": "Wolf",
                          "template": "auto", "clips": ["Idle", "Walk", "Run"], "format": "glb,fbx"}' \
  $URL/api/v1/pipeline                         # -> an estimate, as above
curl -s -X POST ... -d '{"confirm": "est_..."}' $URL/api/v1/pipeline
# 202 {"job": {"id": "gen_...", "pipeline": true, ...}}; when the model lands, GET /api/v1/generate/gen_... shows
# "rig_job": 12, an ordinary job (GET /api/v1/jobs/12, its events stream, its GLB and FBX)
```

The rig options (`template`, `height`, `clips`, `naming`, `target`, `format`) are checked before anything is spent.

### A model you generated already

```bash
curl -s -X POST ... -d '{"provider": "tripo", "task_id": "<the task id>", "name": "Horse"}' $URL/api/v1/models
```

This downloads a model that is already made and paid for on the account whose key the server holds. It spends
nothing. `POST /api/v1/pipeline` takes the same body (plus the rig options) to import and rig in one call.
- **Tripo:** the task id from the API. Tasks made on the web app at studio.tripo3d.ai belong to that app, and its
  ids may not be visible to the API. For those, use the download link or the DCC Bridge (below).
- **Meshy:** any text, picture or multi-picture task id. The workbench asks each kind in turn.
- **Rodin:** the task `uuid` the generate reply gave (not the subscription key).

## Providers and prices

Prices are in each provider's own credits, checked on 2026-10-18. Options that would change the price in ways the
docs don't list are refused (400), so an estimate can't be wrong by an option it didn't know.

| Provider | Input | Default | Credits | Options taken |
|---|---|---|---|---|
| Tripo ([API v3](https://developers.tripo3d.com/en/docs/introduction)) | `prompt`, or 1 picture, or 2–4 views | H3.1 (`v3.1-20260211`), PBR texture, standard quality | text 10, pictures 20; texture +10 (standard or fast), +20 (detailed), +30 (extreme, assumed); detailed geometry +20, quad +5, smart low-poly +10, parts +20 ([H3.1 prices](https://developers.tripo3d.com/en/models/v3-1)) | `texture`, `pbr`, `texture_quality`, `geometry_quality`, `face_limit`, `quad`, `smart_low_poly`, `generate_parts`, `auto_size`, `negative_prompt`, `*_seed`, `export_uv`, `texture_version`, `delight` |
| Meshy ([API](https://docs.meshy.ai/en/api)) | `prompt`, or 1–4 pictures | `meshy-7.1`, textured at 2k | text: mesh 20 (meshy-6-lite 5) + texture 10 (8k: 15); pictures: 20 mesh only, 30 textured (8k 35; meshy-6-lite 5 / 15); Ultra geometry +5 ([pricing](https://docs.meshy.ai/en/api/pricing)) | `ai_model`, `texture`, `enable_pbr`, `texture_resolution`, `geometry_resolution`, `target_polycount`, `should_remesh`, `pose_mode`, `texture_prompt` |
| Rodin ([Gen-2.5](https://docs.hyper3d.ai/en/api-specification/rodin-gen2-5)) | `prompt`, and/or 1–5 pictures | `Gen-2.5-Medium`, GLB | 0.5; Extreme-High tier +0.5; `texture_mode` extreme-high +2.0 | `tier`, `texture_mode`, `quality`, `mesh_mode`, `geometry_instruct_mode`, `uhd_texture`, `addons`, `seed` |

Pictures are `images: [...]`, each an `https://` link or a `data:image/png;base64,...` URI (PNG, JPEG or WebP, up to
20 MB). Give Tripo views as `{"front": ...}`, `{"left": ...}`, `{"back": ...}`, `{"right": ...}`. The front view is
needed, and plain pictures are taken in that order. A Meshy text model is two tasks, the mesh and then its texture.
Both are covered by the one confirmed estimate.

Tips for prompts that rig well: spell out the body plan, proportions,
pose and colours, and avoid negations. Meshy's `pose_mode: "a-pose"` or `"t-pose"` helps people rig.

## Turning it on

Settings live in `.autorig_generate.json` beside the repo (git ignores it). The spend log is
`.autorig_generate_usage.jsonl`. The HTTP API only reads them, so visitors to the hosted copy can't turn generation
on, raise a cap or read a key. On the server:

```bash
python scripts/generate_settings.py --key tripo          # asks for the key without echoing it
python scripts/generate_settings.py --cap tripo_daily=40 --cap tripo_monthly=200
python scripts/generate_settings.py --on                 # --off turns it off again
python scripts/generate_settings.py                      # show (keys only as "saved")
```

| Setting | Default | Meaning |
|---|---|---|
| `enabled` | false | nothing can be spent while false |
| `tripo_key`, `meshy_key`, `rodin_key` | empty | API keys. An empty key falls back to `TRIPO_API_KEY`, `MESHY_API_KEY` or `HYPER3D_API_KEY` |
| `tripo_daily` / `tripo_monthly` | 40 / 200 | credits a local day and a calendar month |
| `meshy_daily` / `meshy_monthly` | 60 / 300 | |
| `rodin_daily` / `rodin_monthly` | 2 / 10 | |
| `poll_seconds`, `timeout_minutes` | 5, 30 | how often a job asks, and when it gives up waiting (the task id stays) |

`AUTORIG_GENERATE_FILE` and `AUTORIG_GENERATE_USAGE_FILE` move the two files (the tests use them). `GET /api/v1/generate`
shows whether generation is on, which keys are set (never the keys), the caps, what has been spent today and this
month, and the recent jobs.

## What makes spending hard to do by accident

- **Two calls.** The only call to a provider's paid endpoint is in the confirm, which needs an estimate id from an
  earlier call. The id works once and lasts 15 minutes. The confirm spends the request the estimate priced. A confirm
  that sends a different prompt, pictures, options or provider is refused (409).
- **Off by default.** With `enabled` false, a confirm answers 403. Without the provider's key it answers 412.
- **Caps.** The estimate's credits are held against the provider's daily and monthly caps before the task is sent
  (402 when it would pass one). They are corrected to what the provider reports it charged, or released when the task
  fails. One confirm at a time passes the cap check.
- **The balance.** When the provider reports one, an estimate the account can't pay says so.
- **Keys** go only to the provider's API host. They never go to the download links, the logs, the job records or any
  reply.
- **Tests can't spend.** `tests/test_generate.py` points all three providers at a stub server on 127.0.0.1 and
  checks that estimates, refusals and imports create no task, and that one confirm creates exactly one.

A real end-to-end test spends one generation per provider, so it needs Sam's yes for each.

## Tripo's DCC Bridge

Tripo's web app ([Tripo Studio](https://studio.tripo3d.ai)) has a **DCC Bridge** menu that sends a model to Blender,
Unity, Unreal, Godot, Maya, 3ds Max, Cocos, Roblox or ZBrush. We looked at whether the workbench could be one of
those targets. The plug-ins are linked from Studio's own page, under `https://tripo-public.tripo3d.ai/plugins/`. The
Blender (1.0.34), Unity and Godot (1.0.1) zips were downloaded with curl into empty scratch folders and read. None of
them was run.

**How it works.** Each plug-in runs a WebSocket server on the user's own machine, at 127.0.0.1. The ports are Blender
60600, Unity 60610 and Godot 60650. The Studio page, open in the browser, connects to `ws://127.0.0.1:<port>`, and the
protocol is the same in every plug-in:
- The page sends `{"type": "handshake", "payload": {"clientName", "protocolVersion"}}`. The plug-in answers with
  `handshake_ack` and its own `clientName`, `dccVersion`, `pluginVersion` and `protocolVersion` ("1.0.0").
- After that, the page sends a `ping` each second and the plug-in answers `pong`. The page gives up after 30 seconds
  of silence.
- A model arrives as binary frames of 5 MB chunks. Each frame is a JSON header,
  `{"type": "file_transfer", "payload": {"fileId", "fileName", "fileType", "chunkIndex", "chunkTotal", "chunkSize"}}`,
  followed straight away by the chunk's bytes. The plug-in finds the end of the JSON by counting braces.
- Each chunk gets a `file_transfer_ack`. The last one also gets `file_transfer_complete` (status "importing"), and
  `import_complete` follows once the editor has loaded it.

The formats are chosen per target. Unity takes fbx, obj and zip. Godot takes fbx, obj, glb, gltf and zip. Blender
loads obj, fbx, glb, gltf, usdz and stl. None of the plug-ins checks who connects: there is no token and no Origin
check, so any web page open in the browser could push a file to them.

**Can the workbench receive it?** Yes, but not directly on the hosted copy. The browser connects to 127.0.0.1 on the
machine it runs on, so a bridge receiver has to run on Sam's PC, not on VM 207. The protocol is small enough to
write in about 150 lines of Python with no dependencies beyond a WebSocket handshake. The useful part is that it
uses the web app's credits and its own preview and editing tools. It needs no API key, and the API's task ids may not
reach web-app models anyway.

**Recommendation:** build a small receiver as a follow-up (M8b), `scripts/tripo_bridge.py`, run on Penny:
- It listens on 127.0.0.1:60650, Godot's port, a target Sam doesn't use, so the real Blender and Unity bridges stay
  free.
- It answers the handshake as the Godot plug-in does, and refuses any connection whose `Origin` isn't a Tripo Studio
  page.
- It reassembles the chunks, then sends the file to the workbench with `POST /api/v1/models` (the local server, or the
  hosted copy with its token). It can also send it to `POST /api/v1/pipeline` to rig and animate as soon as it lands.

Before writing it, do one free live check with Sam's account (sending an already-made model spends nothing). Confirm
that Studio offers Godot without an installed plug-in check, and see which format it sends there (GLB is hoped for).
Two caveats. The protocol is undocumented and could change with a Studio update. Chrome may ask once for permission
to let studio.tripo3d.ai reach a local address (Private Network Access). The zips carry no licence, so none of their
code is copied: the receiver is written from the protocol described here.
