# Mesh2Motion embedding bridge (protocol v1)

The workbench page embeds Mesh2Motion's "Use Your Model" page (`/m2m/create.html`) in an iframe. It drives the page
through URL parameters and `window.postMessage`. The page reports back what the user does: steps, edited joints,
clip choices and exported files. This is phase M4 of the overhaul (see [PLAN.md](PLAN.md), section M).

The bridge code is in our fork [sambena/mesh2motion-app](https://github.com/sambena/mesh2motion-app), branch
`workbench-bridge`, folder [`src/lib/bridge/`](../third_party/mesh2motion-app/src/lib/bridge/). It is written as
upstream-style code so it could go upstream. `BridgeProtocol.ts` there has the types, and this document must match
it.

Test page: `/m2m/bridge-test.html` (source [autorig/gui/m2m_bridge_test.html](../autorig/gui/m2m_bridge_test.html),
copied into the build by `scripts/build_m2m.py`). It has a button for every message, logs every reply and keeps
them all in `window.bridgeLog`.

## Steps

Mesh2Motion works in four steps. The bridge names them like this:

| Bridge name | Mesh2Motion step | Notes |
|---|---|---|
| `load` | Load Model | Where the page starts. |
| `skeleton` | Load Skeleton (pick a template) | Passed through during a `load`. Shown to the user only when the model URL came without a template. |
| `edit` | Edit Skeleton (move joints) | |
| `animate` | Animations listing and export | Entering it skins the model (Mesh2Motion's "Bind pose"). |

Only `edit` and `animate` can be requested. All four are reported.

## URL parameters

`/m2m/create.html?embed=1&model=<url>&template=<name>&skeleton=<url>&step=edit|animate`

| Parameter | Meaning |
|---|---|
| `embed=1` | Embedded mode. Hides Mesh2Motion's top bar, build label and "Request an animation" links. Exports go to the parent as a Blob, never as a browser download, and the "download complete" dialog is skipped. Any value except `0` or `false` turns it on. |
| `model` | GLB or FBX URL to load (URL-encoded). Relative URLs resolve against `/m2m/`, so use an absolute path such as `/api/v1/...` for our files. The type comes from the extension (ignoring `?query`). No extension means GLB. |
| `template` | `human`, `fox`, `horse`, `bird`, `dragon`, `kaiju`, `spider`, `snake` or `shark`. Case is ignored, and Mesh2Motion's own name `fish` is accepted for `shark`. Without a template the page loads the model and stops at `skeleton` for the user to pick one. |
| `skeleton` | URL of a fitted-skeleton JSON (format below). Needs `template`. |
| `step` | `edit` (the default when a template is given) or `animate`, which skins and goes straight to the clip list. |

The URL load starts as soon as the page is ready and behaves like a `load` message with no `id`. Its completion
therefore shows up only as `stepChanged` (and `clips` for `animate`), and a failure as an `error` with no `id`.
Unknown `template` or `step` values are reported as `error` messages after `ready`.

Use either the URL parameters or a `load` message, not both at once: a URL load is not queued with messages.

## Fitted-skeleton JSON

```json
{"template": "fox", "joints": {"hips": [0.0, 0.42, -0.05], "spine_01": [0.0, 0.45, 0.02]}}
```

- **`template`**: the template the joints belong to. A `load` fails if it names a different template. It may be
  `""`.
- **`joints`**: bone name to `[x, y, z]` world position in metres, in the model file's own coordinates (glTF: Y up,
  +Z forward), exactly as `getJoints` returns them. Bone names are those of Mesh2Motion's rig GLB as three.js reads
  it: `root`, `pelvis`, `spine_01`, `head` and so on for the human. Call `getJoints` after loading a template for
  the full list.
- **Partial sets work.** A bone with no entry keeps its template offset from its parent. Names that match no bone
  are ignored and reported in one `error` (no `id`).
- **Placement.** Bones are placed parents first. Each one is translated to its position and keeps the template's
  rest rotation, the same as moving joints by hand in Mesh2Motion. Before placing, the whole template is scaled by
  the median ratio of fitted to template bone lengths. Mesh2Motion uses that scale for root-motion keyframes, so
  clips travel the right distance for the model's size.
- **Oversized models.** Mesh2Motion shrinks any model taller than 2 m on import. The bridge applies the same factor
  to joints going in and coming out, so the parent always works in file coordinates.

## Messages

Every message, both ways, is an object `{v: 1, type, id?, ...fields}`. The app accepts only messages whose
`event.origin` equals its own origin. The workbench serves both pages from one origin, under `/` and `/m2m/`. The app
posts to `window.parent` with its own origin as the target. Data that has no `v` and string `type` is ignored, so
other postMessage traffic is harmless. A message with a `v` other than 1 gets an `error`.

`id` is optional, and either a string or a number. Requests run one at a time, in arrival order. Every request
that carries an `id` gets exactly one reply with that same `id`:

- **Data requests** get their data message: `getJoints` gets `joints`, `selectClips` gets `clips`, and `export`
  gets `exported`.
- **Other requests** (`load`, `goto`, `setJoints`) get `done`.
- **Any failure**, including a malformed request, gets `error`.

Messages without an `id` are events: things the user did, or progress during a request.

### Parent → app

| type | Fields | What it does | Reply |
|---|---|---|---|
| `load` | `model` (URL), `template`, `skeleton?` (URL string **or** the fitted-skeleton object inline), `step?` (`edit` default, or `animate`) | Starts over from the model step, loads the model and the template rig, places the fitted joints if given, and goes to `step`. For `animate` it replies only once the clip list is loaded. | `done {request:'load'}` |
| `goto` | `step`: `edit` or `animate` | `edit` from `animate` goes back to joint editing (Mesh2Motion's Back button). `animate` from `edit` skins and lists the clips, replying once they are loaded. Going to the current step is a no-op. Fails before a `load`. | `done {request:'goto'}` |
| `getJoints` | none | Reads the current joints. These are the edit-skeleton joints, also while animating: the playing animation does not move them. Fails before a template is loaded. | `joints` |
| `setJoints` | `joints`: bone name → `[x,y,z]` | Moves those bones (parents first, no rescale), makes the result the new rest pose and clears undo. In `animate` it goes back to `edit`, applies the joints and re-skins, so `stepChanged` events follow. | `done {request:'setJoints'}` |
| `selectClips` | `names`: string[] | Checks exactly these clips in the list and unchecks the rest. Names match exactly first, then ignoring case with spaces and underscores treated alike. `[]` clears the selection. Waits for the clip list if it is still loading. Fails outside `animate`. | `clips` (with `unknown` if any names matched nothing) |
| `export` | `format`: `glb` (default) or `fbx`; `clips?`: names; `naming`: `default` or `mixamo`; `mirror?`: boolean; `fbxPreset?`: `unity` (default) or `unreal` | Exports the skinned model with those clips. Without `clips` it uses the current selection, with the mirror mode the user picked per clip. `mirror: true` exports each clip plus a mirrored copy. `mixamo` renames the human bones (and their tracks) to Mixamo names and leaves other templates unchanged. Fails outside `animate`, with no clips, or with unknown clip names. | `exported` |

### App → parent

| type | Fields | When |
|---|---|---|
| `ready` | `templates`: the template names this build has | Once, when the page has started and is listening. Wait for it before sending. |
| `stepChanged` | `step`: `load`, `skeleton`, `edit` or `animate` | Whenever the step changes, whether by the user or by a request. |
| `joints` | `template`, `joints` (as in the skeleton JSON) | As the reply to `getJoints`. Also as an event when the user finishes an edit (releases a drag, or undo/redo) and the joints changed, and when the user leaves `edit` for `animate`. Not sent for joints the bridge placed itself. |
| `clips` | `available`: `[{name, duration, rootMotion}]`; `selected`: names; `unknown?`: names | As the reply to `selectClips`. Also as an event when the clip list has loaded and whenever the user changes the selection. `duration` is in seconds (3 decimals). `rootMotion` is true when the clip travels: some position track ends more than 0.25 m across the floor from where it started (`Walk_RM`, rolls, dodges). In-place loops and lunges that come back are false. |
| `exported` | `format`, `filename` (the model's file name with a `.glb` or `.fbx` extension), `blob` (a `Blob`) | As the reply to `export`. In embedded mode, also as an event (no `id`) when the user presses Mesh2Motion's own Download button. |
| `done` | `request`: the request type | Reply to `load`, `goto` and `setJoints`. |
| `error` | `message` | Reply to a request that failed. Also as an event (no `id`) for bad URL parameters, a failed URL load and unknown joint names. |

## Example

```js
const frame = document.querySelector('iframe')
frame.src = '/m2m/create.html?embed=1'
let next = 1
const pending = new Map()
function call (msg) {
  const id = next++
  frame.contentWindow.postMessage({ v: 1, id, ...msg }, location.origin)
  return new Promise((resolve, reject) => pending.set(id, { resolve, reject }))
}
window.addEventListener('message', (e) => {
  if (e.origin !== location.origin || e.source !== frame.contentWindow) return
  const m = e.data
  if (m.id !== undefined && pending.has(m.id)) {
    const p = pending.get(m.id); pending.delete(m.id)
    m.type === 'error' ? p.reject(new Error(m.message)) : p.resolve(m)
  } else if (m.type === 'ready') {
    start()
  } // else: stepChanged / joints / clips / exported / error events
})
async function start () {
  await call({ type: 'load', model: '/api/v1/models/fox/model.glb', template: 'fox', skeleton: '/api/v1/models/fox/m2m_joints.json', step: 'animate' })
  await call({ type: 'selectClips', names: ['Walk', 'Run'] })
  const { blob, filename } = await call({ type: 'export', format: 'glb' })
  // upload blob, or offer it as a download
}
```

## Limits

- The joints are positions only. Bone rotations stay the template's, so a fit cannot twist a bone about its own
  axis.
- Mesh2Motion keeps one model at a time. A new `load` throws away the previous model, skinning and selection.
- Custom clips the user imports with Mesh2Motion's upload button appear in `available` like the library clips.
- Mesh2Motion's model position gizmo, rotate buttons and hand-skeleton options apply only when the user drives the
  page by hand. A `load` uses the model as it is, with every finger of the human template.
