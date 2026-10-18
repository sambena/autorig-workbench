// SPDX-License-Identifier: GPL-3.0-or-later
// Autorig Workbench: the reference side of the M2b parity check. Reads the cases scripts/m2m_skin_parity.py wrote
// (M2M_PARITY_IN), runs Mesh2Motion's SkinningAlgorithm (MIT, https://github.com/Mesh2Motion/mesh2motion-app) on each,
// and writes the skin indices and weights to M2M_PARITY_OUT. Each bone is pinned at the world head the Python side
// computed, so both sides see the very same numbers.
import { readFileSync, writeFileSync } from 'node:fs'
import { Bone, BufferGeometry, Float32BufferAttribute, Object3D, Uint32BufferAttribute } from 'three'
import SkinningAlgorithm from '../../third_party/mesh2motion-app/src/lib/solvers/SkinningAlgorithm.ts'
import { SkeletonType } from '../../third_party/mesh2motion-app/src/lib/enums/SkeletonType.ts'

interface Case {
  name: string
  template: string
  bones: Array<{ name: string, parent: number, head: number[] }>
  positions: number[]
  index: number[] | null
  head_height: number | null
  arm_offset: number | null
}

function rig (c: Case): Bone {
  const armature = new Object3D() // the root bone's parent is not a bone, as in M2M (armature.children[0])
  const bones = c.bones.map(b => {
    const bone = new Bone()
    bone.name = b.name
    bone.matrixAutoUpdate = false
    bone.matrixWorldAutoUpdate = false
    bone.matrixWorld.makeTranslation(b.head[0], b.head[1], b.head[2])
    return bone
  })
  c.bones.forEach((b, i) => { (b.parent < 0 ? armature : bones[b.parent]).add(bones[i]) })
  return armature.children[0] as Bone
}

test('mesh2motion skin solvers', () => {
  const cases: Case[] = JSON.parse(readFileSync(process.env.M2M_PARITY_IN as string, 'utf8'))
  const out: Record<string, { indices: number[], weights: number[] }> = {}
  for (const c of cases) {
    const geom = new BufferGeometry()
    geom.setAttribute('position', new Float32BufferAttribute(c.positions, 3))
    if (c.index !== null) geom.setIndex(new Uint32BufferAttribute(c.index, 1))
    const type = c.template === 'shark' ? SkeletonType.Fish : c.template as SkeletonType
    const algo = new SkinningAlgorithm(rig(c), type)
    algo.set_geometry(geom)
    algo.set_head_weight_correction_enabled(c.head_height !== null)
    if (c.head_height !== null) algo.set_preview_plane_height(c.head_height)
    algo.set_arm_plane_correction_enabled(c.arm_offset !== null)
    if (c.arm_offset !== null) algo.set_arm_plane_offset(c.arm_offset)
    const [indices, weights] = algo.calculate_indexes_and_weights()
    out[c.name] = { indices, weights }
  }
  writeFileSync(process.env.M2M_PARITY_OUT as string, JSON.stringify(out))
})
