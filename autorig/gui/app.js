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

// ---------------------------------------------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------------------------------------------

async function start () {
  loadLibrary()
  try {
    state.templates = (await api('GET', '/templates')).templates
  } catch { /* Mesh2Motion not checked out: auto and classic still work */ }
  renderTemplates()
}

start()
