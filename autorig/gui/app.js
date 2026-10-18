// SPDX-License-Identifier: GPL-3.0-or-later
// Autorig Workbench: the page's code (index.html). Plain ES module, no build step.
// It talks only to the HTTP API (/api/v1, docs/API.md) and to the embedded Mesh2Motion app through its
// postMessage bridge (docs/M2M-BRIDGE.md).

const TOKEN = document.querySelector('meta[name="autorig-token"]').content
const $ = (id) => document.getElementById(id)
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))

// ---------------------------------------------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------------------------------------------

class ApiError extends Error {
  constructor (status, message) { super(message); this.status = status }
}

async function api (method, path, body) {
  const headers = { Authorization: 'Bearer ' + TOKEN }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const r = await fetch('/api/v1' + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
  let data = null
  try { data = await r.json() } catch { /* empty or not JSON */ }
  if (!r.ok) throw new ApiError(r.status, (data && data.error) || `${method} ${path}: HTTP ${r.status}`)
  return data
}

// links the browser opens itself (images, downloads, event streams) carry the token in the query string
function withToken (url) {
  return url + (url.includes('?') ? '&' : '?') + 't=' + encodeURIComponent(TOKEN)
}

const m = (name) => '/models/' + encodeURIComponent(name)

function toast (text, isError = false) {
  const t = $('toast')
  t.textContent = text
  t.className = isError ? 'err' : ''
  t.hidden = false
  clearTimeout(toast.timer)
  toast.timer = setTimeout(() => { t.hidden = true }, isError ? 8000 : 4000)
}

// ---------------------------------------------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------------------------------------------

const state = {
  step: 'load',
  models: [],
  model: null, // name
  details: null, // GET /api/v1/models/{model}
  audit: null, // the model's audit verdict: grade and checks
  skeleton: null, // GET .../skeleton, or null
  templates: [],
  picked: [], // clip names picked in Mesh2Motion
  lastExport: null // the files the last export job wrote
}

// ---------------------------------------------------------------------------------------------------------------
// Jobs: one panel follows one job over its event stream
// ---------------------------------------------------------------------------------------------------------------

function followJob (panel, job, title) {
  return new Promise((resolve) => {
    panel.hidden = false
    panel.innerHTML = `<div class="head"><span class="title"></span><button class="link cancel">Cancel</button></div>
      <progress max="1"></progress><pre></pre>`
    const titleEl = panel.querySelector('.title')
    const bar = panel.querySelector('progress')
    const pre = panel.querySelector('pre')
    const cancel = panel.querySelector('.cancel')
    const lines = []
    const show = (info) => {
      titleEl.textContent = `${title}: ${info.state}`
      if (info.total) { bar.max = info.total; bar.value = Math.min(info.completed ?? info.current ?? 0, info.total) } else if (info.state === 'running' || info.state === 'queued') bar.removeAttribute('value')
    }
    show(job)
    cancel.onclick = () => { api('DELETE', `/jobs/${job.id}`).catch((e) => toast(e.message, true)) }

    const es = new EventSource(withToken(`/api/v1/jobs/${job.id}/events`))
    es.onmessage = (e) => {
      lines.push(JSON.parse(e.data))
      if (lines.length > 400) lines.splice(0, lines.length - 400)
      pre.textContent = lines.join('\n')
      pre.scrollTop = pre.scrollHeight
      if (titleEl.textContent.endsWith('queued')) titleEl.textContent = `${title}: running`
    }
    es.addEventListener('end', async (e) => {
      es.close()
      cancel.hidden = true
      let view = JSON.parse(e.data)
      try { view = await api('GET', `/jobs/${job.id}?log=0`) } catch { /* keep the end event's info */ }
      show(view)
      bar.max = 1; bar.value = 1
      resolve(view)
    })
  })
}

// ---------------------------------------------------------------------------------------------------------------
// Mesh2Motion bridge client (docs/M2M-BRIDGE.md)
// ---------------------------------------------------------------------------------------------------------------

const bridge = {
  frame: $('m2m'),
  holder: $('m2m-holder'),
  next: 1,
  pending: new Map(),
  handlers: {},
  readyResolve: null,
  ready: null,

  /** Puts the Mesh2Motion frame in this stage. Moving it reloads it, so wait for `ready` again. */
  show (stage) {
    if (this.holder.parentElement !== stage || this.ready === null) {
      this.ready = new Promise((resolve) => { this.readyResolve = resolve })
      for (const p of this.pending.values()) p.reject(new Error('Mesh2Motion reloaded'))
      this.pending.clear()
      if (this.frame.getAttribute('src') === null) this.frame.src = '/m2m/create.html?embed=1'
      stage.appendChild(this.holder)
    }
    this.holder.hidden = false
    return this.ready
  },

  hide () {
    this.holder.hidden = true
  },

  call (msg) {
    const id = this.next++
    this.frame.contentWindow.postMessage({ v: 1, id, ...msg }, location.origin)
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }))
  },

  on (type, fn) { this.handlers[type] = fn }
}

window.addEventListener('message', (e) => {
  if (e.origin !== location.origin || e.source !== bridge.frame.contentWindow) return
  const msg = e.data
  if (!msg || msg.v !== 1) return
  if (msg.id !== undefined && bridge.pending.has(msg.id)) {
    const p = bridge.pending.get(msg.id)
    bridge.pending.delete(msg.id)
    if (msg.type === 'error') p.reject(new Error(msg.message)); else p.resolve(msg)
    return
  }
  if (msg.type === 'ready') { bridge.readyResolve?.(msg); return }
  if (msg.type === 'error') { toast('Mesh2Motion: ' + msg.message, true); return }
  bridge.handlers[msg.type]?.(msg)
})

// ---------------------------------------------------------------------------------------------------------------
// Steps
// ---------------------------------------------------------------------------------------------------------------

function goStep (step) {
  if (step !== 'load' && !state.model) { toast('Load or pick a model first'); step = 'load' }
  state.step = step
  for (const b of document.querySelectorAll('#steps button')) {
    if (b.dataset.step === step) b.setAttribute('aria-current', 'step'); else b.removeAttribute('aria-current')
  }
  for (const s of ['load', 'rig', 'animate']) $('step-' + s).hidden = s !== step
  if (step === 'rig') enterRig()
  if (step === 'animate') enterAnimate()
}

for (const b of document.querySelectorAll('#steps button')) b.onclick = () => goStep(b.dataset.step)

// ---------------------------------------------------------------------------------------------------------------
// 1. Load
// ---------------------------------------------------------------------------------------------------------------

async function loadLibrary () {
  try {
    state.models = (await api('GET', '/models')).models
  } catch (e) {
    $('library').innerHTML = `<div id="library-empty">${esc(e.message)}</div>`
    return
  }
  renderLibrary()
}

function renderLibrary () {
  const q = $('search').value.trim().toLowerCase()
  const shown = state.models.filter((x) => !q || x.name.toLowerCase().includes(q) || (x.group || '').toLowerCase().includes(q))
  if (shown.length === 0) {
    $('library').innerHTML = `<div id="library-empty">${state.models.length ? 'No model matches.' : 'No models yet: drop one on the left.'}</div>`
    return
  }
  $('library').innerHTML = shown.slice(0, 400).map((x) => {
    const thumb = x.thumbnail ? `<img src="${esc(withToken(x.thumbnail))}" alt="" loading="lazy">` : '◇'
    const grade = x.audit && x.audit !== '?' ? `<span class="badge ${esc(x.audit)}">${esc(x.audit)}</span>` : ''
    const what = x.m2m_template ? `${x.m2m_template} rig` : (x.rigged || x.rigged_fbx) ? 'rigged' : 'not rigged'
    return `<button class="card" data-name="${esc(x.name)}" aria-pressed="${x.name === state.model}">
      <div class="thumb">${thumb}</div>
      <div class="meta"><span class="name" title="${esc(x.name)}">${esc(x.name)}</span>
      <span class="sub">${esc(x.group || 'no group')} · ${esc(what)} ${grade}</span></div></button>`
  }).join('')
}

$('search').oninput = renderLibrary
$('library').onclick = async (e) => {
  const card = e.target.closest('.card')
  if (!card) return
  await selectModel(card.dataset.name)
  goStep('rig')
}

async function selectModel (name) {
  state.model = name
  state.picked = []
  state.lastExport = null
  for (const id of ['rig-job', 'animate-job']) $(id).hidden = true
  $('model-chip').innerHTML = `Model: <b>${esc(name)}</b>`
  await refreshModel()
  renderLibrary()
  renderAdvancedSteps()
  if ($('adv-ai').open) refreshAI()
}

async function refreshModel () {
  const name = state.model
  try {
    state.details = await api('GET', m(name))
  } catch (e) {
    toast(e.message, true)
    state.details = null
  }
  try {
    state.skeleton = await api('GET', m(name) + '/skeleton')
  } catch {
    state.skeleton = null
  }
  state.audit = state.details?.audit || await m2mAudit()
}

// m2m-rig keeps its audit beside the rig (<rig folder>/audit/<name>.json) rather than in the work folder
async function m2mAudit () {
  const st = state.details?.status
  const f = (state.details?.files || []).find((x) => x.path === `${st?.rig_folder || 'rigged'}/audit/${state.model}.json`)
  if (!f) return null
  try {
    const r = await fetch(f.url, { headers: { Authorization: 'Bearer ' + TOKEN } })
    const v = (await r.json()).verdict
    return {
      grade: v.grade,
      checks: Object.entries(v.checks || {}).map(([check, c]) => ({ check, value: c.value, limit: c.limit, grade: c.grade || (c.ok ? 'PASS' : 'FAIL') }))
    }
  } catch {
    return null
  }
}

// dropping or choosing a file adds it (POST /api/v1/models with the file as the body)
const drop = $('drop')
drop.onclick = () => $('file-input').click()
drop.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('file-input').click() } }
drop.ondragover = (e) => { e.preventDefault(); drop.classList.add('over') }
drop.ondragleave = () => drop.classList.remove('over')
drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove('over'); if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]) }
$('file-input').onchange = (e) => { if (e.target.files[0]) upload(e.target.files[0]); e.target.value = '' }

function upload (file) {
  const name = file.name.replace(/\.[^.]+$/, '').replace(/[^A-Za-z0-9_-]+/g, '_') || 'model'
  const panel = $('upload-job')
  const bar = $('upload-progress')
  panel.hidden = false
  $('upload-text').textContent = `Uploading ${file.name}`
  bar.max = file.size || 1
  bar.value = 0
  const xhr = new XMLHttpRequest()
  xhr.open('POST', `/api/v1/models?filename=${encodeURIComponent(file.name)}&name=${encodeURIComponent(name)}`)
  xhr.setRequestHeader('Authorization', 'Bearer ' + TOKEN)
  xhr.setRequestHeader('Content-Type', 'application/octet-stream')
  xhr.upload.onprogress = (e) => { if (e.lengthComputable) bar.value = e.loaded }
  xhr.onload = async () => {
    let data = null
    try { data = JSON.parse(xhr.responseText) } catch { /* not JSON */ }
    if (xhr.status >= 300) {
      $('upload-text').textContent = `Upload failed: ${(data && data.error) || 'HTTP ' + xhr.status}`
      return
    }
    panel.hidden = true
    toast(`Added ${data.model.name}`)
    await loadLibrary()
    await selectModel(data.model.name)
    goStep('rig')
  }
  xhr.onerror = () => { $('upload-text').textContent = 'Upload failed: the connection dropped' }
  xhr.send(file)
}

// ---- picking a file that is already on the server (GET /api/v1/files): only folders the server allows
const browser = { path: null }

async function browse (path) {
  let d
  try {
    d = await api('GET', '/files' + (path ? '?path=' + encodeURIComponent(path) : ''))
  } catch (e) {
    toast(e.message, true)
    return
  }
  browser.path = d.path
  browser.parent = d.parent
  $('library').hidden = true
  $('browser').hidden = false
  $('browser-path').textContent = d.path || 'Folders on the server you may use'
  $('browser-up').disabled = !d.path
  $('browser-use').hidden = !d.path
  $('browser-list').innerHTML = d.entries.length
    ? d.entries.map((x, i) => `<button class="entry" data-i="${i}" type="button"><span class="ico">${x.type === 'dir' ? (x.model ? '◆' : '▸') : '◇'}</span>
        <span class="nm">${esc(x.name)}</span><span class="sz">${x.model ? 'model folder' : x.bytes ? (x.bytes / 1048576).toFixed(1) + ' MB' : ''}</span></button>`).join('')
    : '<p class="hint" style="padding:10px">No models or folders here.</p>'
  for (const b of $('browser-list').querySelectorAll('.entry')) {
    const x = d.entries[Number(b.dataset.i)]
    b.onclick = () => (x.type === 'dir' ? browse(x.path) : useServerPath(x.path))   // a folder opens; Use this folder takes it
  }
}

async function useServerPath (path) {
  try {
    const r = await api('POST', '/models', { path })
    toast(r.existing ? `Opened ${r.model.name}` : `Added ${r.model.name}`)
    closeBrowser()
    await loadLibrary()
    await selectModel(r.model.name)
    goStep('rig')
  } catch (e) {
    toast(e.message, true)
  }
}

function closeBrowser () {
  $('browser').hidden = true
  $('library').hidden = false
}

$('server-pick').onclick = () => browse(null)
$('browser-up').onclick = () => browse(browser.parent || null)
$('browser-use').onclick = () => useServerPath(browser.path)
$('browser-close').onclick = closeBrowser

// ---- the collection the page works on (GET /api/v1/collections): a picker in the header when there is a choice
let cols = null

async function loadCollections () {
  try {
    cols = await api('GET', '/collections')
  } catch {
    return
  }
  const sel = $('collection')
  sel.innerHTML = cols.collections.map((c) =>
    `<option value="${esc(c.path)}" ${c.current ? 'selected' : ''}>${esc(c.name)} (${c.models})</option>`).join('')
  sel.hidden = cols.collections.length < 2
  renderCollectionsPane()
}

async function openCollection (path) {
  try {
    cols = await api('POST', '/collections/select', { path })
  } catch (e) {
    toast(e.message, true)
    await loadCollections()
    return
  }
  state.model = null
  state.details = null
  $('model-chip').textContent = 'No model yet'
  await loadCollections()
  await loadLibrary()
  goStep('load')
  toast('Opened ' + (cols.collections.find((c) => c.current)?.name || path))
}

$('collection').onchange = (e) => openCollection(e.target.value)

// ---------------------------------------------------------------------------------------------------------------
// 2. Rig
// ---------------------------------------------------------------------------------------------------------------

const CHECKS = {
  bleed_pct: (c) => `${c.value}% of the skin follows the wrong bone (limit ${c.limit}%)`,
  combined_tears: (c) => `the mesh tears in ${c.value} places when posed`,
  bend_tears: (c) => `${c.value} tears when the joints bend`,
  head_pct: (c) => `the head pulls ${c.value}% of the body along (limit ${c.limit}%)`,
  max_influences: (c) => `up to ${c.value} bones move one point (games allow ${c.limit})`
}

function gradeReason (audit) {
  const rank = { FAIL: 0, CHECK: 1, PASS: 2 }
  const bad = (audit.checks || []).filter((c) => c.grade !== 'PASS').sort((a, b) => rank[a.grade] - rank[b.grade])
  if (bad.length === 0) return 'Skin weights, bends and tears are all within limits.'
  const said = new Set()
  const parts = []
  for (const c of bad) {
    const text = (CHECKS[c.check] || ((x) => `${x.check} is ${x.value} (limit ${x.limit})`))(c)
    if (c.check.endsWith('tears') && said.has('tears')) continue
    if (c.check.endsWith('tears')) said.add('tears')
    parts.push(text)
    if (parts.length === 2) break
  }
  const s = parts.join('; ')
  return s.charAt(0).toUpperCase() + s.slice(1) + '.'
}

function renderGrade () {
  const el = $('grade')
  const audit = state.audit
  if (!audit || !audit.grade) {
    const st = state.details?.status
    el.innerHTML = state.skeleton
      ? `<p class="hint">Fitted to the ${esc(state.skeleton.template)} skeleton, not audited yet.</p>`
      : st?.rigged || st?.rigged_fbx
        ? '<p class="hint">Rigged, not audited yet (Advanced → Audit).</p>'
        : '<p class="hint">Not rigged yet.</p>'
    return
  }
  const template = state.skeleton ? ` on the ${esc(state.skeleton.template)} skeleton` : ''
  el.innerHTML = `<div class="grade ${esc(audit.grade)}"><span class="big">${esc(audit.grade)}</span>
    <span class="hint">Audit${template}</span><span>${esc(gradeReason(audit))}</span></div>`
}

function note (el, html, isError = false) {
  el.className = 'note' + (isError ? ' err' : '')
  el.innerHTML = html
  el.hidden = !html
}

function renderTemplates () {
  const sel = $('template')
  const keep = sel.value
  sel.innerHTML = '<option value="auto">Auto: pick from the body</option>' +
    state.templates.map((t) => `<option value="${esc(t.name)}">${esc(t.title || t.name)}</option>`).join('') +
    '<option value="classic">Classic (our own skeleton, from rig.json)</option>'
  if (keep) sel.value = keep
}

function rigPlaceholder () {
  const x = state.models.find((y) => y.name === state.model)
  const img = x?.thumbnail ? `<img src="${esc(withToken(x.thumbnail))}" alt="">` : ''
  $('rig-placeholder').innerHTML = `<div>${img}<p>${state.skeleton
    ? 'Adjust joints opens the Mesh2Motion editor here with our fitted skeleton.'
    : 'Auto rig fits a Mesh2Motion skeleton to the model, skins it and audits it.'}</p></div>`
}

function enterRig () {
  bridge.hide()
  $('rig-placeholder').hidden = false
  setAdjusting(false)
  note($('rig-note'), '')
  renderGrade()
  rigPlaceholder()
  $('adjust').disabled = !state.skeleton
  $('adjust').title = state.skeleton ? '' : 'Rig the model first'
}

async function runRig (template) {
  note($('rig-note'), '')
  let job
  try {
    job = await api('POST', m(state.model) + '/rig', { template })
  } catch (e) {
    if (e.status === 501) {
      note($('rig-note'), `Mesh2Motion rigging arrives with M2 (it isn't on this server yet).
        <br><button id="classic-fallback">Use the classic rig instead</button>`)
      $('classic-fallback').onclick = () => runRig('classic')
    } else {
      note($('rig-note'), esc(e.message), true)
    }
    return
  }
  const title = template === 'classic' ? 'Classic rig' : `Rig (${template})`
  await rigJobDone(await followJob($('rig-job'), job, title))
}

async function rigJobDone (view) {
  if (view.state !== 'done') {
    note($('rig-note'), view.state === 'cancelled' ? 'Cancelled.' : 'The rig failed: see the log above.', view.state !== 'cancelled')
  }
  await refreshModel()
  await loadLibrary()
  enterRig()
  $('rig-job').hidden = false
}

$('auto-rig').onclick = () => runRig($('template').value)

// The model Mesh2Motion opens: the rigged GLB m2m-rig wrote, whose coordinates the fitted joints are in (turned to
// face the template's way, standing on the ground); the source itself only when there is no rigged GLB yet
function m2mModelUrl () {
  const st = state.details?.status
  const files = state.details?.files || []
  const rigged = files.find((f) => f.path === `${st?.rig_folder || 'rigged'}/${state.model}.glb`)
  if (rigged) return withToken(rigged.url)
  if (!st?.source || !['.glb', '.fbx'].includes(st.source_ext)) return null
  return withToken(`/api/v1${m(state.model)}/files/${st.source.split('/').map(encodeURIComponent).join('/')}`)
}

function setAdjusting (on) {
  $('adjust').hidden = on
  $('apply').hidden = !on
  $('cancel-adjust').hidden = !on
  $('auto-rig').disabled = on
}

// Opens Mesh2Motion in a stage with our model and fitted skeleton, at `step` (edit or animate)
async function openInM2M (stage, placeholder, step) {
  const model = m2mModelUrl()
  if (!model) throw new Error(`Mesh2Motion opens GLB or FBX; this model's source is ${state.details?.status?.source_ext || 'missing'}`)
  placeholder.hidden = true
  await bridge.show(stage)
  await bridge.call({ type: 'load', model, template: state.skeleton.template, skeleton: { template: state.skeleton.template, joints: state.skeleton.joints }, step })
}

$('adjust').onclick = async () => {
  if (!state.skeleton) return
  setAdjusting(true)
  try {
    await openInM2M($('rig-stage'), $('rig-placeholder'), 'edit')
  } catch (e) {
    note($('rig-note'), esc(e.message), true)
    enterRig()
  }
}

$('cancel-adjust').onclick = () => enterRig()

$('apply').onclick = async () => {
  $('apply').disabled = true
  try {
    const { template, joints } = await bridge.call({ type: 'getJoints' })
    const job = await api('PUT', m(state.model) + '/skeleton', { template, joints })
    await rigJobDone(await followJob($('rig-job'), job, 'Re-skin with the edited joints'))
  } catch (e) {
    note($('rig-note'), e.status === 501 ? 'Re-skinning edited joints arrives with M2 (not on this server yet).' : esc(e.message), true)
  } finally {
    $('apply').disabled = false
  }
}

// ---------------------------------------------------------------------------------------------------------------
// 3. Animate
// ---------------------------------------------------------------------------------------------------------------

function renderPicked () {
  const n = state.picked.length
  $('picked').textContent = n === 0 ? 'No clips picked' : `${n} clip${n === 1 ? '' : 's'}: ${state.picked.slice(0, 6).join(', ')}${n > 6 ? '…' : ''}`
  $('export').disabled = n === 0
}

bridge.on('clips', (msg) => { state.picked = msg.selected; renderPicked() })

async function enterAnimate () {
  renderPicked()
  renderFiles()
  note($('animate-note'), '')
  if (!state.skeleton) {
    bridge.hide()
    $('animate-placeholder').hidden = false
    $('animate-placeholder').innerHTML = '<p>Rig this model on a Mesh2Motion skeleton first (step 2): its clips play on that skeleton.</p>'
    $('export').disabled = true
    return
  }
  try {
    await openInM2M($('animate-stage'), $('animate-placeholder'), 'animate')
  } catch (e) {
    $('animate-placeholder').hidden = false
    $('animate-placeholder').innerHTML = `<p>${esc(e.message)}</p>`
  }
}

$('export').onclick = async () => {
  note($('animate-note'), '')
  let job
  try {
    job = await api('POST', m(state.model) + '/animate', {
      clips: state.picked, format: $('format').value, target: $('target').value, naming: $('naming').value
    })
  } catch (e) {
    note($('animate-note'), e.status === 501 ? 'Mesh2Motion clips arrive with M2 (not on this server yet).' : esc(e.message), true)
    return
  }
  const view = await followJob($('animate-job'), job, 'Export')
  if (view.state !== 'done') note($('animate-note'), 'The export failed: see the log above.', true)
  const out = view.results?.M2M_RIG?.out || {}
  state.lastExport = ['glb', 'fbx'].map((k) => out[k]).filter(Boolean)
  if (state.lastExport.length === 0) state.lastExport = null
  await refreshModel()
  renderFiles()
}

function renderFiles () {
  const files = state.details?.files || []
  const wanted = state.lastExport
    ? files.filter((f) => state.lastExport.some((p) => String(p).replace(/\\/g, '/').endsWith('/' + f.path)))
    : files.filter((f) => /\.(glb|fbx)$/i.test(f.path) && !/(^|\/)(prepared\/|preview\.glb$)/.test(f.path) && f.path !== state.details?.status?.source)
  $('files').innerHTML = wanted.length === 0
    ? ''
    : '<span class="hint">Files</span>' + wanted.map((f) =>
      `<a href="${esc(withToken(f.url))}" download>${esc(f.path)}</a> <span class="hint">${(f.bytes / 1048576).toFixed(1)} MB</span>`).join('')
}

// ---------------------------------------------------------------------------------------------------------------
// Advanced: every step and tool from GET /api/v1/steps, with a form from each one's options
// ---------------------------------------------------------------------------------------------------------------

let registry = null

async function loadAdvanced () {
  if (registry) return
  try {
    registry = await api('GET', '/steps')
  } catch (e) {
    $('adv-tools').innerHTML = `<p class="hint">${esc(e.message)}</p>`
    return
  }
  renderAdvancedSteps()
  $('adv-tools').innerHTML = registry.tools.map((t, i) => `
    <details class="adv-item"><summary><b>${esc(t.name)}</b><span class="help">${esc(t.help)}</span></summary>
      <form class="adv-form" data-tool="${i}">
        ${t.options.map((o) => field(t.name, o)).join('')}
        <div class="wide row"><button class="primary" ${t.available ? '' : 'disabled title="not installed in this version"'}>Run ${esc(t.name)}</button>
        ${t.available ? '' : '<span class="hint">not installed in this version</span>'}</div>
      </form></details>`).join('')
  for (const form of $('adv-tools').querySelectorAll('form')) form.onsubmit = runTool
}

function field (tool, o) {
  const id = `opt-${tool}-${o.name}`
  const label = `<label for="${esc(id)}" title="${esc(o.help)}">${esc(o.name)}${o.required ? ' *' : ''}</label>`
  const common = `id="${esc(id)}" name="${esc(o.name)}" data-type="${esc(o.type)}" title="${esc(o.help)}"`
  if (o.type === 'boolean') return `${label}<input type="checkbox" ${common} ${o.default ? 'checked' : ''}>`
  if (o.choices) {
    return `${label}<select ${common}>${o.required || o.default !== undefined ? '' : '<option value=""></option>'}${o.choices.map((c) =>
      `<option ${String(c) === String(o.default) ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select>`
  }
  const type = o.type === 'number' || o.type === 'integer' ? 'number' : 'text'
  const step = o.type === 'integer' ? '1' : 'any'
  const ph = o.path ? (o.path === 'out' ? 'output path' : 'input path') : (o.default !== undefined ? String(o.default) : o.help)
  return `${label}<input type="${type}" ${type === 'number' ? `step="${step}"` : ''} ${common} placeholder="${esc(ph)}">`
}

function renderAdvancedSteps () {
  if (!registry) return
  const why = state.details?.status?.steps || {}
  $('adv-steps').innerHTML = registry.model_steps.map((s) => {
    const reason = !state.model ? 'Pick a model first' : why[s.name] || ''
    return `<button data-step="${esc(s.name)}" title="${esc(reason || s.help)}" ${reason ? 'disabled' : ''}>${esc(s.title)}</button>`
  }).join('')
  for (const b of $('adv-steps').querySelectorAll('button')) b.onclick = () => runStep(b.dataset.step)
}

async function runStep (step) {
  try {
    const job = await api('POST', `${m(state.model)}/steps/${encodeURIComponent(step)}`)
    await followJob($('adv-job'), job, `${step} on ${state.model}`)
    await refreshModel()
    renderAdvancedSteps()
    if (state.step === 'rig') enterRig()
  } catch (e) {
    toast(e.message, true)
  }
}

async function runTool (e) {
  e.preventDefault()
  const tool = registry.tools[Number(e.target.dataset.tool)]
  const options = {}
  for (const el of e.target.querySelectorAll('[name]')) {
    const type = el.dataset.type
    if (type === 'boolean') { if (el.checked) options[el.name] = true; continue }
    const v = el.value.trim()
    if (v === '') continue
    options[el.name] = type === 'number' || type === 'integer' ? Number(v) : v
  }
  try {
    const job = await api('POST', `/tools/${encodeURIComponent(tool.name)}`, { options })
    await followJob($('adv-job'), job, tool.name)
  } catch (err) {
    toast(err.message, true)
  }
}

$('advanced').ontoggle = () => { if ($('advanced').open) loadAdvanced() }

// ---- Settings: collections and folders
function renderCollectionsPane () {
  const pane = $('collections-pane')
  if (!cols) return
  pane.innerHTML = `<p class="hint">Open: <b>${esc(cols.current)}</b><br>Work folder: ${esc(cols.work)}</p>
    <table>${cols.collections.map((c) => `<tr><td>${esc(c.name)}</td><td>${c.models} models</td>
      <td>${c.current ? '<b>open</b>' : `<button class="link" data-path="${esc(c.path)}" type="button">Open</button>`}</td></tr>`).join('')}</table>
    ${cols.allow_any_folder
      ? `<form class="row" id="add-collection"><input type="text" name="path" placeholder="a folder on the server" style="flex:1">
         <button>Add</button></form>`
      : `<p class="hint">This server lists its collections and browsable folders in <code>.autorig_server.json</code> next to the tool
         (collections, browse). Running your own copy? Start it with <code>--allow-any-folder</code> to add any folder here.</p>`}`
  for (const b of pane.querySelectorAll('[data-path]')) b.onclick = () => openCollection(b.dataset.path)
  const form = $('add-collection')
  if (form) {
    form.onsubmit = async (e) => {
      e.preventDefault()
      try {
        cols = await api('POST', '/collections', { path: form.path.value.trim() })
        await loadCollections()
      } catch (err) { toast(err.message, true) }
    }
  }
}

// ---- Settings: AI Assist (GET/POST /api/v1/ai/...; docs/AI-ASSIST.md). A request costs money: the button says how
// much at most before it is pressed, and nothing is sent without that press.
const money = (x) => (x > 0 && x < 0.01 ? '$' + x.toFixed(3) : '$' + Number(x || 0).toFixed(2))
const ai = { catalog: null, settings: null, conversation: '', job: '', shown: 0, timer: 0 }

async function loadAI () {
  try {
    const d = await api('GET', '/ai/settings')
    ai.catalog = d.catalog
    ai.settings = d.settings
    fillAISettings()
  } catch (e) {
    $('ai-status').textContent = e.message
    return
  }
  loadAIUsage()
  refreshAI()
}

function fillAISettings () {
  const s = ai.settings
  const sel = $('ai-provider')
  sel.innerHTML = '<option value="off">Off</option>' + Object.entries(ai.catalog.providers).map(([k, label]) =>
    `<option value="${esc(k)}">${esc(label)}</option>`).join('')
  sel.value = s.provider
  $('ai-limit').value = s.monthly_limit
  const fields = () => {
    const p = sel.value
    const box = $('ai-provider-fields')
    if (p === 'off') { box.innerHTML = ''; return }
    if (p === 'ollama') {
      box.innerHTML = `<label for="ai-ollama-url">Ollama address</label><input type="text" id="ai-ollama-url" value="${esc(s.ollama_url)}">
        <label for="ai-model">Model</label><input type="text" id="ai-model" value="${esc(s.ollama_model)}">`
      return
    }
    const models = ai.catalog.models.filter((x) => x.provider === p)
    const keyState = s[p + '_key']
    box.innerHTML = `<label for="ai-model">Model</label><select id="ai-model">${models.map((x) =>
      `<option value="${esc(x.id)}">${esc(x.label)}: $${x.in} in / $${x.out} out per M tokens</option>`).join('')}</select>
      <label for="ai-key">API key</label><input type="password" id="ai-key" autocomplete="off"
        placeholder="${keyState ? esc(keyState) + ' (empty keeps it)' : 'paste the key'}">`
    $('ai-model').value = s[p + '_model']
  }
  sel.onchange = fields
  fields()
}

$('ai-settings').onsubmit = async (e) => {
  e.preventDefault()
  const p = $('ai-provider').value
  const body = { provider: p, monthly_limit: $('ai-limit').value }
  if (p === 'ollama') {
    body.ollama_url = $('ai-ollama-url').value
    body.ollama_model = $('ai-model').value
  } else if (p !== 'off') {
    body[p + '_model'] = $('ai-model').value
    body[p + '_key'] = $('ai-key').value
  }
  try {
    const d = await api('POST', '/ai/settings', body)
    ai.settings = d.settings
    fillAISettings()
    $('ai-settings-msg').textContent = p === 'off' ? 'AI is off.' : d.status.enabled ? `Saved: ${d.status.model_label}.` : d.status.problem
    refreshAI()
  } catch (err) {
    $('ai-settings-msg').textContent = err.message
  }
}

async function loadAIUsage () {
  try {
    const u = await api('GET', '/ai/usage')
    $('ai-usage').innerHTML = `Spent ${money(u.spent)} of the ${money(u.limit)} monthly limit.` + (u.recent.length
      ? `<table>${u.recent.slice(0, 8).map((r) => `<tr><td>${esc(new Date(r.at * 1000).toLocaleString())}</td><td>${esc(r.model)}</td><td>${money(r.cost)}</td></tr>`).join('')}</table>`
      : ' No calls yet.')
  } catch (e) { $('ai-usage').textContent = e.message }
}

// the status line, the free steps the model is missing, and what pressing Ask may cost
async function refreshAI () {
  const btn = $('ai-run')
  btn.disabled = true
  $('ai-model-hint').hidden = !!state.model
  try {
    const st = await api('GET', '/ai/status')
    $('ai-status').textContent = st.enabled ? `${st.model_label} · this month ${money(st.spent)} of ${money(st.limit)}` : st.problem
    if (!st.enabled || !state.model) { $('ai-estimate').textContent = ''; $('ai-todo').innerHTML = ''; return }
    const todo = (await api('GET', '/ai/todo?' + new URLSearchParams({ task: 'fix-rig', model: state.model }))).steps
    $('ai-todo').innerHTML = todo.length
      ? `<p class="hint">First, free (on this server): ${todo.map((s) => `<button type="button" data-step="${esc(s.step)}" title="${esc(s.why)}">${esc(s.label)}</button>`).join(' ')}</p>`
      : ''
    for (const b of $('ai-todo').querySelectorAll('[data-step]')) b.onclick = () => aiRunSteps([b.dataset.step])
    const q = new URLSearchParams({ task: 'fix-rig', model: state.model, message: $('ai-message').value, conversation: ai.conversation })
    const est = await api('GET', '/ai/estimate?' + q)
    btn.textContent = est.free ? `Ask ${est.model_label} (free)` : `Ask ${est.model_label} (up to ${money(est.worst)})`
    $('ai-estimate').textContent = est.allowed ? (est.free ? '' : `usually about ${money(est.typical)}`) : est.reason
    btn.disabled = !est.allowed || !!ai.job || !$('ai-message').value.trim()
  } catch (e) {
    $('ai-status').textContent = e.message
  }
}

async function aiRunSteps (steps) {
  try {
    const r = await api('POST', '/ai/run-steps', { task: 'fix-rig', model: state.model, steps })
    await followJob($('adv-job'), r.job, steps.join(', ') + ' on ' + state.model)
    await refreshModel()
    refreshAI()
  } catch (e) { toast(e.message, true) }
}

function aiLog (text, cls = '') {
  const div = document.createElement('div')
  div.className = cls
  div.textContent = text
  $('ai-log').append(div)
  return div
}

$('ai-run').onclick = async () => {
  const message = $('ai-message').value.trim()
  if (!message || !state.model) return
  $('ai-run').disabled = true
  $('ai-plan').innerHTML = ''
  aiLog(message, 'you')
  try {
    const r = await api('POST', '/ai/start', { task: 'fix-rig', model: state.model, message, conversation: ai.conversation })
    ai.conversation = r.conversation
    ai.job = r.job
    ai.shown = 0
    ai.live = aiLog('Working…')
    $('ai-message').value = ''
    aiPoll()
  } catch (e) {
    aiLog(e.message, 'err')
    refreshAI()
  }
}

async function aiPoll () {
  let j
  try {
    j = await api('GET', '/ai/job?id=' + encodeURIComponent(ai.job))
  } catch (e) {
    aiLog(e.message, 'err')
    ai.job = ''
    return refreshAI()
  }
  const steps = j.steps || []
  for (let k = ai.shown; k < steps.length; k++) {
    if (k === steps.length - 1 && steps[k].running && !j.done) break
    ai.live.before(Object.assign(document.createElement('div'), { className: steps[k].error ? 'err' : '', textContent: steps[k].summary }))
    ai.shown = k + 1
  }
  ai.live.textContent = `Working… (${money(j.cost || 0)} so far)`
  if (!j.done) { ai.timer = setTimeout(aiPoll, 1500); return }
  ai.live.remove()
  ai.job = ''
  if (j.error) aiLog(j.error, 'err')
  if (j.reply) aiLog(j.reply)
  if (j.question) aiLog(j.question.text + (j.question.options?.length ? ' (' + j.question.options.join(' / ') + ')' : ''))
  if (j.plan) showAIPlan(j.plan)
  loadAIUsage()
  refreshAI()
}

function showAIPlan (plan) {
  const box = $('ai-plan')
  box.innerHTML = '<b>Plan</b>' + plan.changes.map((c) =>
    `<label class="row"><input type="checkbox" data-n="${c.n}" checked>${esc(c.summary)}</label>`).join('') +
    '<div class="row"><button class="primary" id="ai-apply" type="button">Apply and re-rig</button><span class="hint">free: saves rig.json (kept for Undo), then rigs, trims and audits</span></div>'
  $('ai-apply').onclick = async () => {
    const skip = [...box.querySelectorAll('[data-n]')].filter((c) => !c.checked).map((c) => Number(c.dataset.n))
    try {
      const r = await api('POST', '/ai/apply', { plan: plan.id, skip })
      $('ai-apply').disabled = true
      if (r.error) { toast(r.error, true); return }
      const undo = Object.assign(document.createElement('button'), { type: 'button', textContent: 'Undo' })
      undo.onclick = async () => {
        try { await api('POST', '/ai/undo', { task: 'fix-rig', journal: r.journal }); toast('Undone: rig.json is back'); undo.disabled = true } catch (e) { toast(e.message, true) }
      }
      box.append(undo)
      if (r.job) await followJob($('adv-job'), r.job, 'AI fix: re-rig ' + state.model)
      await refreshModel()
    } catch (e) { toast(e.message, true) }
  }
}

$('ai-new').onclick = () => { clearTimeout(ai.timer); Object.assign(ai, { conversation: '', job: '', shown: 0 }); $('ai-log').innerHTML = ''; $('ai-plan').innerHTML = ''; refreshAI() }
let aiTyping = 0
$('ai-message').oninput = () => { clearTimeout(aiTyping); aiTyping = setTimeout(refreshAI, 400) }
$('adv-ai').ontoggle = () => { if ($('adv-ai').open) loadAI() }
$('adv-collections').ontoggle = () => { if ($('adv-collections').open) loadCollections() }


// ---------------------------------------------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------------------------------------------

async function start () {
  loadLibrary()
  loadCollections()
  try {
    state.templates = (await api('GET', '/templates')).templates
  } catch { /* Mesh2Motion not checked out: auto and classic still work */ }
  renderTemplates()
}

start()
