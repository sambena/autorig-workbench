// SPDX-License-Identifier: GPL-3.0-or-later
import * as THREE from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
const $=s=>document.querySelector(s), token=window.AUTORIG_TOKEN;
const names={lods:'LOD export',vehicle:'Vehicle parts & pivots',reference:'Reference comparison',materials:'Material library',foliage:'Foliage & wind',generate:'Generate from references',validate:'Animation quality',package:'Engine package'};
const descriptions={lods:'Export static or skinned meshes at several detail levels. Joint protection and sampled surface comparisons back off reduction when quality falls. Morph meshes retain their geometry.',vehicle:'Prepare a static vehicle in metres, detect wheel parts and fit hub pivots. Object assignments and pivot overrides use JSON. Inspect the result with wheel spin below.',reference:'Render front, side, back and face views, with an optional reference comparison sheet. Reference paths are relative to the model folder, or under the collection/work folders.',materials:'Download a Poly Haven surface by asset ID. Outputs include separate maps, a lossless packed map and provenance. ORM = occlusion / roughness / metallic; RAH = roughness / occlusion / height.',foliage:'Build a reproducible 12-cell foliage atlas and crossed cards. Optionally export the model with a vertex-alpha wind mask for a compatible runtime shader.',generate:'Submit reference photos to your configured Hyper3D CLI. This uses provider credits. Front, Side and Back images are required; Face is optional. Saved generation IDs resume after cancellation. Downloaded candidates can be imported through the Workbench.'};
const labels={ratios:'LOD ratios',format:'Export format',protect_joints:'Protect bending regions',max_error:'Maximum surface error / size',height:'Height (metres; 0 = unchanged)',length:'Length (metres)',front:'Source faces',assignments:'Wheel object names by corner',pivots:'Pivot coordinates by corner',straighten:'Straighten wheel axles',carve_missing:'Carve missing mirrored wheels',folder:'Reference folder',pixels:'Render pixels',turn:'Turn (degrees)',asset:'Poly Haven asset ID',resolution:'Resolution',packing:'Packed channels',seed:'Random seed',cell:'Atlas cell pixels',wind_base:'Wind at base (0Ã¢â‚¬â€œ1)',wind_tip:'Wind at tip (0Ã¢â‚¬â€œ1)',apply_wind:'Export model with wind mask',candidates:'Candidate count',tier:'Generation tier',quality:'Target mesh quality'};
const choices={format:['glb','fbx'],front:['-y','+y','+x','-x'],resolution:['1k','2k','4k'],packing:['orm','rah']};
let data,tool='lods',job=null,dirty=false,disposePreview=()=>{};
descriptions.validate='Check the current Preview GLB one clip at a time across every mesh. Reports seam gaps, stretching, collapsed triangles, floor penetration and near-ground foot speeds. Add clip names to loops to check endpoint continuity after root travel is removed. Run Preview in the Workbench first.';
descriptions.package='Zip the rigged model for an engine (web, Unity, Unreal or Godot), with its Studio LODs when Include LODs is ticked: run LOD export first, in the format the engine takes (GLB for web, FBX for Unity and Unreal).';
Object.assign(labels,{target:'Engine',include_lods:'Include Studio LODs'});choices.target=['web','unity','unreal','godot'];
Object.assign(labels,{every:'Frame sampling interval',loops:'Looping clip names (JSON)',seam_mm:'Maximum seam gap (mm)'});
Object.assign(labels,{triangles:'Base triangle budget (0 = full)',normalize:'Normalize facing, ground & size'});
const url=p=>p+(p.includes('?')?'&':'?')+'t='+encodeURIComponent(token);
async function api(path,body){const r=await fetch(path,{headers:{'X-Autorig-Token':token,'Content-Type':'application/json'},...(body===undefined?{}:{method:'POST',body:JSON.stringify(body)})});const j=await r.json();if(!r.ok)throw Error(j.error||r.statusText);return j;}
function busy(value){for(const e of document.querySelectorAll('#tools button,#settings input,#settings select,#settings textarea,#save,#run,#model'))e.disabled=value;$('#cancel').disabled=!value;}
function status(message){$('#status').textContent=message;}
function renderForm(){
 $('#title').textContent=names[tool];$('#description').textContent=descriptions[tool];$('#settings').replaceChildren();
 for(const [key,value] of Object.entries(data.settings[tool])){
  const label=document.createElement('label'),span=document.createElement('span');span.textContent=labels[key]||key;label.append(span);
  let input;
  if(choices[key]){input=document.createElement('select');for(const choice of choices[key])input.add(new Option(choice,choice));input.value=value;}
  else if(typeof value==='object'){input=document.createElement('textarea');input.value=JSON.stringify(value);input.dataset.json='true';}
  else{input=document.createElement('input');input.type=typeof value==='boolean'?'checkbox':typeof value==='number'?'number':'text';if(input.type==='checkbox')input.checked=value;else input.value=value;if(input.type==='number')input.step='any';}
  input.name=key;input.addEventListener('input',()=>dirty=true);label.append(input);$('#settings').append(label);
 }
 for(const b of $('#tools').children)b.classList.toggle('active',b.dataset.tool===tool);
 $('#run').textContent=tool==='generate'?'Generate / resume (provider credits)':'Run tool';
}
function values(){const out={};for(const input of $('#settings').elements){out[input.name]=input.dataset.json?JSON.parse(input.value):input.type==='checkbox'?input.checked:input.type==='number'?Number(input.value):input.value;}return out;}
async function load(){if(!$('#model').value)return;data=await api('/api/studio?model='+encodeURIComponent($('#model').value));dirty=false;renderForm();renderArtifacts();}
function renderArtifacts(){
 $('#artifacts').replaceChildren();
 for(const item of data.artifacts){const card=document.createElement('div');card.className='artifact';const a=document.createElement('a');a.textContent=item.path;a.href=url(item.url);a.target='_blank';card.append(a);
  if(/\.png$/i.test(item.path)){const img=document.createElement('img');img.loading='lazy';img.alt=item.path;img.src=url(item.url)+'&v='+Date.now();card.prepend(img);}
  if(/\.glb$/i.test(item.path)){const b=document.createElement('button');b.textContent='Inspect in 3D';b.onclick=()=>preview(item).catch(e=>status(e.message));card.append(document.createElement('br'),b);}
  $('#artifacts').append(card);
 }
}
async function preview(item){
 disposePreview();const host=$('#preview');host.replaceChildren();
 const renderer=new THREE.WebGLRenderer({antialias:true});renderer.setPixelRatio(Math.min(devicePixelRatio,2));host.append(renderer.domElement);
 const scene=new THREE.Scene();scene.background=new THREE.Color('#10151d');const camera=new THREE.PerspectiveCamera(40,1,.001,10000);const controls=new OrbitControls(camera,renderer.domElement);
 scene.add(new THREE.HemisphereLight(0xffffff,0x667788,3));const light=new THREE.DirectionalLight(0xffffff,3);light.position.set(3,8,4);scene.add(light);
 let frame=0,object,dead=false;const observer=new ResizeObserver(()=>{const w=host.clientWidth;renderer.setSize(w,340);camera.aspect=w/340;camera.updateProjectionMatrix();});observer.observe(host);
 disposePreview=()=>{dead=true;cancelAnimationFrame(frame);observer.disconnect();controls.dispose();if(object)object.traverse(o=>{o.geometry?.dispose();for(const m of Array.isArray(o.material)?o.material:o.material?[o.material]:[]){for(const v of Object.values(m))if(v?.isTexture)v.dispose();m.dispose();}});renderer.dispose();host.replaceChildren();};
 const gltf=await new GLTFLoader().loadAsync(url(item.url)+'&v='+Date.now());if(dead)return;object=gltf.scene;scene.add(object);
 const box=new THREE.Box3().setFromObject(object),center=box.getCenter(new THREE.Vector3()),size=box.getSize(new THREE.Vector3()).length()||1;controls.target.copy(center);camera.position.copy(center).add(new THREE.Vector3(size*.8,size*.5,size*.9));camera.far=size*100;camera.updateProjectionMatrix();
 const wheels=[];object.traverse(o=>{if(o.name.startsWith('Wheel_'))wheels.push({o,q:o.quaternion.clone()});});let spin=false;
 if(wheels.length){const b=document.createElement('button');b.textContent='Spin wheels';b.onclick=()=>{spin=!spin;b.textContent=spin?'Stop wheels':'Spin wheels';};host.prepend(b);}
 const start=performance.now();function draw(){if(dead)return;const t=(performance.now()-start)/1000;for(const {o,q} of wheels){o.quaternion.copy(q);if(spin)o.rotateX(t*3);}controls.update();renderer.render(scene,camera);frame=requestAnimationFrame(draw);}draw();
}
async function watch(){
 if(!job)return;try{const info=await api('/api/jobs/'+job.id);
 status(names[job.tool]+': '+info.state);
 if(!['queued','running'].includes(info.state)){job=null;busy(false);$('#cancel').disabled=true;$('#run').disabled=false;$('#model').disabled=false;await load();return;}
 }catch(e){status(e.message);}setTimeout(watch,1500);
}
$('#save').onclick=async()=>{try{const s=values();await api('/api/studio/save',{model:$('#model').value,tool,settings:s});data.settings[tool]=s;dirty=false;status('Settings saved.');}catch(e){status(e.message);}};
$('#run').onclick=async()=>{try{const s=values(),r=await api('/api/studio/run',{model:$('#model').value,tool,settings:s});job={...r,tool};busy(true);data.settings[tool]=s;dirty=false;$('#run').disabled=true;$('#model').disabled=true;$('#cancel').disabled=false;$('#log').hidden=false;$('#log').textContent='';const events=new EventSource(url('/api/jobs/'+job.id+'/events'));events.onmessage=e=>{try{$('#log').textContent+=JSON.parse(e.data)+'\n';$('#log').scrollTop=$('#log').scrollHeight;}catch{}};events.addEventListener('end',()=>{events.close();$('#model').disabled=false;});watch();}catch(e){status(e.message);}};
$('#cancel').onclick=()=>api('/api/cancel',{job:job.id}).catch(e=>status(e.message));
let previousModel='';$('#model').onchange=()=>{if(dirty&&!confirm('Discard unsaved Studio settings?')){$('#model').value=previousModel;return;}previousModel=$('#model').value;disposePreview();load().catch(e=>status(e.message));};
for(const [id,name]of Object.entries(names)){const b=document.createElement('button');b.textContent=name;b.dataset.tool=id;b.onclick=()=>{if(dirty&&!confirm('Discard unsaved Studio settings?'))return;tool=id;dirty=false;renderForm();};$('#tools').append(b);}
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
$('#home').href=url('/');
try{const state=await api('/api/state');for(const m of state.models)$('#model').add(new Option(m.name,m.name));const desired=new URLSearchParams(location.search).get('model');if(desired&&[...$('#model').options].some(o=>o.value===desired))$('#model').value=desired;previousModel=$('#model').value;if(!previousModel)status('Import a model in the Workbench to begin.');else await load();}catch(e){status(e.message);}
