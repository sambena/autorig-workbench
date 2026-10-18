// SPDX-License-Identifier: GPL-3.0-or-later
// Autorig Workbench: the page's model preview. three.js (vendored, vendor/three/VERSION.txt) shows a GLB or FBX
// with orbit controls, frames it, and plays its clips; the skeleton can be shown over it.
//
//   const v = createViewer(element, { urlModifier })
//   await v.load(url, 'glb' | 'fbx')   ->  { clips: [names], rigged: bool, triangles }
//   v.play(name)  v.showSkeleton(on)  v.clear()
import * as THREE from '/vendor/three/build/three.module.js'
import { OrbitControls } from '/vendor/three/examples/jsm/controls/OrbitControls.js'
import { GLTFLoader } from '/vendor/three/examples/jsm/loaders/GLTFLoader.js'
import { FBXLoader } from '/vendor/three/examples/jsm/loaders/FBXLoader.js'
import { TGALoader } from '/vendor/three/examples/jsm/loaders/TGALoader.js'

export function createViewer (el, opts = {}) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  renderer.outputColorSpace = THREE.SRGBColorSpace
  renderer.setClearColor(0x15171b)
  el.append(renderer.domElement)
  renderer.domElement.style.display = 'block'
  renderer.domElement.style.width = '100%'
  renderer.domElement.style.height = '100%'

  const scene = new THREE.Scene()
  scene.add(new THREE.HemisphereLight(0xffffff, 0x404050, 2.2))
  const sun = new THREE.DirectionalLight(0xffffff, 2.0)
  sun.position.set(3, 6, 4)
  scene.add(sun)
  const grid = new THREE.GridHelper(10, 20, 0x3a4150, 0x262a31)
  scene.add(grid)

  const camera = new THREE.PerspectiveCamera(40, 1, 0.01, 1000)
  camera.position.set(2.5, 1.8, 3.5)
  const controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true

  const manager = new THREE.LoadingManager()
  if (opts.urlModifier) manager.setURLModifier(opts.urlModifier)       // file URLs carry the page's token
  manager.addHandler(/\.tga$/i, new TGALoader(manager))

  const clock = new THREE.Clock()
  let root = null
  let mixer = null
  let actions = {}
  let skeleton = null

  function resize () {
    const w = el.clientWidth || 1
    const h = el.clientHeight || 1
    renderer.setSize(w, h, false)
    camera.aspect = w / h
    camera.updateProjectionMatrix()
  }
  new ResizeObserver(resize).observe(el)
  resize()

  renderer.setAnimationLoop(() => {
    const dt = clock.getDelta()
    if (mixer) mixer.update(dt)
    controls.update()
    renderer.render(scene, camera)
  })

  function clear () {
    if (root) {
      scene.remove(root)
      root.traverse((o) => {
        o.geometry?.dispose?.()
        for (const mat of [].concat(o.material || [])) {
          for (const k in mat) if (mat[k]?.isTexture) mat[k].dispose()
          mat.dispose?.()
        }
      })
    }
    if (skeleton) scene.remove(skeleton)
    root = mixer = skeleton = null
    actions = {}
  }

  // the camera looks at the model from three-quarters, whatever its size and units
  function frame (obj) {
    const box = new THREE.Box3().setFromObject(obj)
    if (box.isEmpty()) return
    const size = box.getSize(new THREE.Vector3())
    const center = box.getCenter(new THREE.Vector3())
    const radius = Math.max(size.x, size.y, size.z) * 0.5 || 1
    obj.position.y -= box.min.y                        // stand it on the grid
    center.y -= box.min.y
    const dist = radius / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2)) * 1.15
    camera.near = dist / 200
    camera.far = dist * 50
    camera.updateProjectionMatrix()
    camera.position.copy(center).add(new THREE.Vector3(0.6, 0.45, 1).normalize().multiplyScalar(dist))
    controls.target.copy(center)
    grid.scale.setScalar(Math.max(radius * 0.6, 0.05))
  }

  async function load (url, kind) {
    clear()
    const ext = (kind || url.split('?')[0].split('.').pop()).toLowerCase()
    let obj, clips
    if (ext === 'fbx') {
      obj = await new FBXLoader(manager).loadAsync(url)
      clips = obj.animations || []
    } else {
      const g = await new GLTFLoader(manager).loadAsync(url)
      obj = g.scene
      clips = g.animations || []
    }
    root = obj
    scene.add(obj)
    let tris = 0
    let rigged = false
    obj.traverse((o) => {
      if (o.isSkinnedMesh) rigged = true
      if (o.isMesh && o.geometry) tris += (o.geometry.index ? o.geometry.index.count : o.geometry.attributes.position.count) / 3
    })
    frame(obj)
    if (rigged) {
      skeleton = new THREE.SkeletonHelper(obj)
      skeleton.visible = false
      scene.add(skeleton)
    }
    if (clips.length) {
      mixer = new THREE.AnimationMixer(obj)
      for (const c of clips) actions[c.name] = mixer.clipAction(c)
    }
    return { clips: clips.map((c) => c.name), rigged, triangles: Math.round(tris) }
  }

  function play (name) {
    for (const a of Object.values(actions)) a.stop()
    if (name && actions[name]) actions[name].reset().play()
  }

  function showSkeleton (on) { if (skeleton) skeleton.visible = !!on }

  return { load, play, showSkeleton, clear, resize }
}
