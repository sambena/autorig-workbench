# HTTP API (v1)

The workbench's server answers a JSON API at `/api/v1`. It is for scripts, AI agents (Claude, ChatGPT…), Unity editor
tools, and the new page. The page itself is phase M5 of docs/PLAN.md.

- **Machine-readable description:** `GET /api/v1/openapi.json` (OpenAPI 3.1, no token needed).
- **Human-readable list:** `GET /api/docs`.
- **Implementation:** `autorig/gui/api_v1.py`.
- **Steps and tools:** listed in one place, `autorig/core/steps_registry.py`. The CLI and batch runs read it too.

## Authentication

Every route except the two descriptions needs the session token, sent one of three ways:

    Authorization: Bearer <token>        (preferred)
    X-Autorig-Token: <token>
    ?t=<token>                           (links only)

The token is the one the server prints at start (`--token` or `AUTORIG_TOKEN` sets it). The server accepts only
`Host: 127.0.0.1` or `localhost`. The hosted copy's nginx sets that, so call it at its public address as usual.

## Three calls: model in, rigged and animated model out

```bash
T=<token>; S=http://127.0.0.1:8765        # or https://autorig.cougarcave.dev

# 1. Add a model, sending the file itself as the body (or JSON {"url": "https://..."}; see below)
curl -s -X POST "$S/api/v1/models?filename=Cow.glb&name=Cow&group=Farm" \
     -H "Authorization: Bearer $T" --data-binary @Cow.glb

# 2. Rig it on a Mesh2Motion skeleton: "auto" picks the template from its body plan
curl -s -X POST "$S/api/v1/models/Cow/rig" -H "Authorization: Bearer $T" \
     -H "Content-Type: application/json" -d '{"template": "auto"}'

# 3. Attach clips and export (GET /api/v1/templates/horse/clips lists them)
curl -s -X POST "$S/api/v1/models/Cow/animate" -H "Authorization: Bearer $T" \
     -H "Content-Type: application/json" \
     -d '{"clips": ["Idle", "Walk", "Run"], "format": "glb,fbx", "target": "unity"}'
```

Steps 2 and 3 return a **job** (HTTP 202).

**Following a job:**
- Poll `GET /api/v1/jobs/{id}` until `state` is `done`, `failed` or `cancelled`.
- Or stream its log from `GET /api/v1/jobs/{id}/events` (Server-Sent Events, ending with `event: end`).
- Jobs run one at a time, in order, on the same queue as the page.
- `DELETE /api/v1/jobs/{id}` cancels a job, killing exactly its own processes.

**Results:**
- A job's `results` holds the JSON lines its steps print. `m2m-rig`'s are under `M2M_RIG`: template, audit grade, clips and files.
- `GET /api/v1/models/{model}` lists the model's files, each with a download URL.

**Everything in one call:** `POST /api/v1/pipeline` with `{"url" | "model", "template", "clips", "format"}`. It adds the model (when given a URL), rigs it and animates it, as a single job.

## Python

```python
import json, time, urllib.request

S, T = "http://127.0.0.1:8765", "<token>"

def call(method, path, body=None, raw=None, ctype="application/json"):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(S + path, data=data, method=method,
                                 headers={"Authorization": "Bearer " + T, "Content-Type": ctype})
    with urllib.request.urlopen(req) as r:
        return json.load(r)

with open("Cow.glb", "rb") as fh:
    call("POST", "/api/v1/models?filename=Cow.glb&name=Cow", raw=fh.read(), ctype="application/octet-stream")
job = call("POST", "/api/v1/pipeline", {"model": "Cow", "template": "auto", "clips": ["Idle", "Walk"]})
while job["state"] in ("queued", "running"):
    time.sleep(2)
    job = call("GET", "/api/v1/jobs/%d" % job["id"])
print(job["state"], job["results"].get("M2M_RIG"))
```

## Unity

Unity's `UnityWebRequest` makes the same calls.
1. POST the model, then POST `rig` and `animate` with `"target": "unity"` and `"format": "fbx"`.
2. Poll the job until it is done.
3. Fetch the FBX from its `files` URL into `Assets/`.

The FBX comes with Unity's axes (root rotation 0, scale 1) and one take per clip. A sample editor script will be added under `examples/unity/`.

## Routes

| Method | Path | What it does |
|---|---|---|
| GET | `/api/v1` | index of routes |
| GET | `/api/v1/openapi.json` | OpenAPI description (no token) |
| GET | `/api/v1/steps` | every model step and tool, with options (the page's Advanced menu) |
| GET | `/api/v1/templates` | Mesh2Motion's nine skeletons and their clip counts |
| GET | `/api/v1/templates/{template}/clips` | a template's clips: name, duration in seconds, root motion |
| GET | `/api/v1/models` | every model and its state |
| POST | `/api/v1/models` | add a model: raw body with `?filename=&name=&group=`, or JSON `{url, name, group}` |
| GET | `/api/v1/models/{model}` | state, audit, clips and files (with download URLs) |
| GET | `/api/v1/models/{model}/files/{path}` | one file of the model's folder |
| GET | `/api/v1/models/{model}/skeleton` | the fitted skeleton: `{template, joints: {bone: [x, y, z]}}` |
| PUT | `/api/v1/models/{model}/skeleton` | edited joints: re-fit and re-skin (a job) |
| POST | `/api/v1/models/{model}/rig` | `{template: auto\|human\|fox\|horse\|bird\|dragon\|kaiju\|spider\|snake\|shark\|classic, height?, clips?}` |
| POST | `/api/v1/models/{model}/animate` | `{clips, format: glb\|fbx\|glb,fbx, naming: default\|mixamo, target: unity\|generic}` |
| POST | `/api/v1/models/{model}/steps/{step}` | one model step: survey, rig, trim, audit, clips, publish, preview, all… |
| POST | `/api/v1/tools/{tool}` | one tool with `{options: {...}}`: game-prop, fbx-maps, mixamo-import… |
| POST | `/api/v1/pipeline` | add (optional), rig and animate in one job |
| GET | `/api/v1/jobs` | recent jobs |
| GET | `/api/v1/jobs/{id}` | state, progress, `results`, and the last `?log=N` lines (default 40) |
| GET | `/api/v1/jobs/{id}/events` | the log as Server-Sent Events |
| DELETE | `/api/v1/jobs/{id}` | cancel |

## Rules

**Adding a model**
- A URL must be `https`. Downloads are capped at 1 GB.
- A file must be `.glb`, `.gltf`, `.fbx`, `.obj` or `.zip`.
- A GLB is mended on the way in (`core/tripo_glb.py`: NaN numbers, joints that all sit at the origin), so Tripo exports load.
- Model names are unique across the collection.
- Import by a generator's task id (`{"provider": "tripo", "task_id": ...}`) answers 501 until phase M8. Until then, send the download URL.

**Tools**
- Tool paths may be relative (to the models root) or absolute.
- Either way they must stay inside the models root or the work folder (otherwise 403). Input files must exist (otherwise 404).

**Error codes**
- **400:** the request is wrong, or Blender is missing.
- **401:** no token, or the wrong one.
- **404:** no such model, step, tool or job.
- **409:** the model isn't ready for that yet. The reason is in `error`, as the page shows it.
- **501:** the feature isn't in this version yet. For example, `m2m-rig` before phase M2 lands.
