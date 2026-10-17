# SPDX-License-Identifier: GPL-3.0-or-later
"""Merge Studio's separate LOD GLBs into one engine-ready FBX; runs headlessly inside Blender.

Adapted from storm-chaser/Tools/blender/package_autorig_lods.py, package_skinned_lods.py and
package_vehicle_lods.py (see docs/MODEL-STUDIO.md). Three layouts:

  static   <Name> root with <Name>_LOD0..n children, one joined mesh per level
  skinned  every level skinned to one armature, the source clips moved onto it and baked
  vehicle  Body and Wheel_FL/FR/RL/RR empties at the wheel pivots, <Part>_LOD0..n under each

Materials are flattened to Principled BSDF + image textures (glTF's vertex-colour graphs do not
survive FBX), and the maps are written to <Name>.fbm beside the FBX. Nothing is rigged, reduced or
rescaled here: that is the job of the lods and vehicle tools. Called by core/engine_package.py with
-- --job <job.json>; prints ENGINE_PACKAGE {report}.
"""
import argparse
import json
import re
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "core")]

CORNERS = ("FL", "FR", "RL", "RR")
PARTS = ("Body",) + tuple("Wheel_" + c for c in CORNERS)


def image_for(socket, seen=None):
    """The image feeding a socket, through any glTF mixing nodes in between."""
    seen = set() if seen is None else seen
    for link in socket.links:
        node = link.from_node
        if node in seen:
            continue
        seen.add(node)
        if node.type == 'TEX_IMAGE' and node.image:
            return node.image
        for child in node.inputs:
            found = image_for(child, seen)
            if found:
                return found
    return None


def flatten_materials(materials, folder, name, roughness):
    """Principled + images only, maps saved as PNG in folder; returns the files written."""
    folder.mkdir(parents=True, exist_ok=True)
    written, saved = [], {}
    for index, material in enumerate(materials):
        if material is None or not material.use_nodes:
            continue
        nodes, links = material.node_tree.nodes, material.node_tree.links
        bsdf = next((n for n in nodes if n.type == 'BSDF_PRINCIPLED'), None)
        if bsdf is None:
            continue
        color = tuple(bsdf.inputs['Base Color'].default_value)
        maps = {key: image_for(bsdf.inputs[socket]) for key, socket in
                (('basecolor', 'Base Color'), ('normal', 'Normal'))}
        for key, image in maps.items():
            if image is None or image in saved:
                continue
            tail = '' if len(materials) == 1 else '_' + str(index)
            path = (folder / (name + tail + '_' + key + '.png')).resolve()
            image.filepath_raw = str(path)
            image.file_format = 'PNG'
            image.save()
            saved[image] = path
            written.append(path.name)
        nodes.clear()
        output = nodes.new('ShaderNodeOutputMaterial')
        bsdf = nodes.new('ShaderNodeBsdfPrincipled')
        links.new(bsdf.outputs['BSDF'], output.inputs['Surface'])
        bsdf.inputs['Base Color'].default_value = color
        bsdf.inputs['Roughness'].default_value = roughness
        if maps['basecolor']:
            tex = nodes.new('ShaderNodeTexImage')
            tex.image = maps['basecolor']
            links.new(tex.outputs['Color'], bsdf.inputs['Base Color'])
        if maps['normal']:
            tex = nodes.new('ShaderNodeTexImage')
            tex.image = maps['normal']
            tex.image.colorspace_settings.name = 'Non-Color'
            normal = nodes.new('ShaderNodeNormalMap')
            links.new(tex.outputs['Color'], normal.inputs['Color'])
            links.new(normal.outputs['Normal'], bsdf.inputs['Normal'])
    return written


def import_glb(path):
    before = set(bpy.context.scene.objects)
    bpy.ops.import_scene.gltf(filepath=str(path), merge_vertices=False)
    added = set(bpy.context.scene.objects) - before
    helpers = {b.custom_shape for a in added if a.type == 'ARMATURE' for b in a.pose.bones if b.custom_shape}
    return added, helpers


def remove(objects):
    for obj in objects:
        if obj.name in bpy.data.objects:
            bpy.data.objects.remove(obj, do_unlink=True)


def bake_to_world(obj):
    world = obj.matrix_world.copy()
    obj.parent = None
    obj.data.transform(world)
    obj.matrix_world = Matrix.Identity(4)


def join(meshes):
    bpy.ops.object.select_all(action='DESELECT')
    for obj in meshes:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    return bpy.context.view_layer.objects.active


def share_materials(obj, materials, where):
    if len(obj.data.materials) != len(materials):
        raise ValueError('material layout differs from LOD0 in ' + where)
    for i, material in enumerate(materials):
        obj.data.materials[i] = material


def triangles(obj):
    obj.data.calc_loop_triangles()
    return len(obj.data.loop_triangles)


def detect(paths):
    """skinned if LOD0 holds an armature, vehicle if its meshes are Body + Wheel_*, else static."""
    added, helpers = import_glb(paths[0])
    names = {re.sub(r'_LOD\d+(\.\d+)?$', '', o.name) for o in added if o.type == 'MESH' and o not in helpers}
    kind = 'skinned' if any(o.type == 'ARMATURE' for o in added) else 'vehicle' if names == set(PARTS) else 'static'
    bpy.ops.wm.read_factory_settings(use_empty=True)
    return kind


def wind_channel(obj, mode, threshold, where):
    """Keep the authored wind channel (the colour attribute whose alpha varies) as the only one."""
    if mode == 'off':
        return
    attributes = obj.data.color_attributes
    wind = [c for c in attributes if any(v.color[3] < threshold for v in c.data)]
    if len(wind) != 1:
        if mode == 'require':
            raise ValueError('expected exactly one authored wind colour channel in ' + where)
        return
    for color in list(attributes):
        if color != wind[0]:
            attributes.remove(color)
    attributes.active_color = wind[0]
    attributes.render_color_index = 0


def static(job, paths):
    name, s = job['name'], job['settings']
    lods, materials, report = [], [], []
    for level, path in enumerate(paths):
        added, helpers = import_glb(path)
        meshes = [o for o in added if o.type == 'MESH' and o not in helpers]
        if not meshes:
            raise ValueError('no mesh in ' + path.name)
        for obj in meshes:
            wind_channel(obj, s['wind'], s['wind_alpha'], path.name)
            bake_to_world(obj)
        remove(added - set(meshes))
        obj = join(meshes)
        obj.name = obj.data.name = name + '_LOD%d' % level
        if level == 0:
            materials = list(obj.data.materials)
        elif len(obj.data.materials) == len(materials):
            share_materials(obj, materials, path.name)
        lods.append(obj)
    root = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(root)
    for lod in lods:
        lod.parent = root
    for lod in lods:
        colors = lod.data.color_attributes.active_color
        alpha = [v.color[3] for v in colors.data] if colors else []
        report.append({'name': lod.name, 'triangles': triangles(lod),
                       'wind_alpha': [min(alpha), max(alpha)] if alpha and min(alpha) < s['wind_alpha'] else None})
    return materials, {'lods': report}, False


def actions_of(armature):
    """(action, slot) pairs on an imported armature: the active one and any NLA strips."""
    found, data = [], armature.animation_data
    if data is None:
        return found
    if data.action:
        found.append((data.action, getattr(data, 'action_slot', None)))
    for track in data.nla_tracks:
        for strip in track.strips:
            if strip.action and all(strip.action is not a for a, _ in found):
                found.append((strip.action, getattr(strip, 'action_slot', None)))
    return found


def animated_bones(action):
    names = set()
    curves = list(getattr(action, 'fcurves', []) or [])
    for layer in getattr(action, 'layers', []):
        for strip in layer.strips:
            for bag in getattr(strip, 'channelbags', []):
                curves += list(bag.fcurves)
    for curve in curves:
        m = re.match(r'pose\.bones\["(.+?)"\]', curve.data_path)
        if m:
            names.add(m.group(1))
    return names


def skinned(job, paths):
    name, s = job['name'], job['settings']
    rig, materials, meshes = None, [], []
    for level, path in enumerate(paths):
        added, helpers = import_glb(path)
        arms = [o for o in added if o.type == 'ARMATURE']
        parts = [o for o in added if o.type == 'MESH' and o not in helpers]
        if len(arms) != 1 or not parts:
            raise ValueError('expected one armature and its skinned meshes in ' + path.name)
        arm = arms[0]
        if level == 0:
            rig = arm
            remove(helpers)
        elif set(rig.data.bones.keys()) != set(arm.data.bones.keys()):
            raise ValueError('LOD skeletons differ: ' + path.name)
        for mesh in parts:
            world = mesh.matrix_world.copy()
            mesh.parent = rig
            mesh.matrix_world = world
            for modifier in mesh.modifiers:
                if modifier.type == 'ARMATURE':
                    modifier.object = rig
        if level:
            remove(added - set(parts))
        mesh = join(parts)
        mesh.name = mesh.data.name = name + '_LOD%d' % level
        if level == 0:
            materials = list(mesh.data.materials)
        else:
            share_materials(mesh, materials, path.name)
        meshes.append(mesh)
    clips = []
    if job.get('animation'):
        # glTF seconds become frames at the scene rate in force while importing.
        bpy.context.scene.render.fps = s['fps']
        added, helpers = import_glb(job['animation'])
        sources = [o for o in added if o.type == 'ARMATURE' and actions_of(o)]
        if len(sources) != 1:
            raise ValueError('expected one animated armature in ' + Path(job['animation']).name)
        found = actions_of(sources[0])
        if s['clips']:
            missing = set(s['clips']) - {a.name for a, _ in found}
            if missing:
                raise ValueError('clips not in the animation source: ' + ', '.join(sorted(missing)))
            found = [(a, slot) for a, slot in found if a.name in s['clips']]
        if s['clip'] and len(found) != 1:
            raise ValueError('a clip name renames exactly one clip; select it with clips')
        bones = set(rig.data.bones.keys())
        for action, slot in found:
            stray = animated_bones(action) - bones
            if stray:
                raise ValueError('clip %s animates bones the prepared rig lacks: %s' % (action.name, ', '.join(sorted(stray)[:8])))
        # Each clip becomes one muted NLA strip, so the FBX holds one take per clip, named after it.
        rig.animation_data_create()
        rig.animation_data.action = None
        for action, slot in found:
            if s['clip']:
                action.name = s['clip']
            action.use_fake_user = True
            track = rig.animation_data.nla_tracks.new()
            track.name = action.name
            strip = track.strips.new(action.name, int(action.frame_range[0]), action)
            if slot is not None and hasattr(strip, 'action_slot'):
                strip.action_slot = slot
            clips.append({'name': action.name, 'frames': [float(f) for f in action.frame_range]})
        remove(added)
    root = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(root)
    rig.parent = root
    bpy.context.scene.render.fps = s['fps']
    if clips:
        bpy.context.scene.frame_start = int(min(c['frames'][0] for c in clips))
        bpy.context.scene.frame_end = int(max(c['frames'][1] for c in clips)) + 1
    report = {'bones': len(rig.data.bones), 'clips': clips, 'fps': s['fps'],
              'lods': [{'name': m.name, 'triangles': triangles(m)} for m in meshes]}
    return materials, report, bool(clips)


def pivots_from(lod0):
    """Wheel pivots from the first level's wheel origins, when no parts.json is given."""
    added, _ = import_glb(lod0)
    found = {}
    for obj in added:
        part = re.sub(r'_LOD\d+(\.\d+)?$', '', obj.name)
        if obj.type == 'MESH' and part.startswith('Wheel_'):
            found[part[6:]] = list(obj.matrix_world.translation)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    return found


def vehicle(job, paths):
    from studio_ports import vehicle as port
    name, s = job['name'], job['settings']
    pivots = job['pivots'] or pivots_from(paths[0])
    if set(pivots) != set(CORNERS):
        raise ValueError('all four wheel pivots are required, found: ' + ', '.join(sorted(pivots)))
    root = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(root)
    groups = {}
    for part in PARTS:
        group = bpy.data.objects.new(part, None)
        bpy.context.scene.collection.objects.link(group)
        group.parent = root
        if part.startswith('Wheel_'):
            group.location = Vector(pivots[part[6:]])
        groups[part] = group
    materials, meshes, previous = [], [], {}
    for level, path in enumerate(paths):
        added, helpers = import_glb(path)
        parts = [o for o in added if o.type == 'MESH' and o not in helpers]
        seen = set()
        for obj in parts:
            part = re.sub(r'_LOD\d+(\.\d+)?$', '', obj.name)
            if part not in groups or part in seen:
                raise ValueError('unexpected prepared part: ' + obj.name)
            seen.add(part)
            bake_to_world(obj)
            group = groups[part]
            obj.data.transform(Matrix.Translation(-group.location))
            obj.parent = group
            obj.name = obj.data.name = part + '_LOD%d' % level
            # Studio keeps a finer level when a small wheel exceeds its error limit. Reuse the
            # coarser mesh already prepared rather than add distant cost; nothing more is reduced.
            if part in previous and len(obj.data.polygons) > len(previous[part].data.polygons):
                obj.data = previous[part].data.copy()
                obj.data.name = obj.name
            elif part.startswith('Wheel_') and s['recentre_wheels']:
                # Keep every level on the same hub, so a small bounds shift after reduction
                # does not become an orbiting wheel. X is the axle.
                center, _ = port.centre(obj)
                obj.data.transform(Matrix.Translation(Vector((0, -center.y, -center.z))))
            previous[part] = obj
            if not materials:
                materials = list(obj.data.materials)
            share_materials(obj, materials, path.name)
            meshes.append(obj)
        if seen != set(groups):
            raise ValueError(path.name + ' is missing parts: ' + ', '.join(sorted(set(groups) - seen)))
        remove(added - set(parts))
    report = {'pivots': pivots, 'lods': {m.name: triangles(m) for m in meshes}}
    return materials, report, False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--job', required=True)
    a = ap.parse_args(sys.argv[sys.argv.index('--') + 1:])
    job = json.loads(Path(a.job).read_text(encoding='utf-8'))
    paths = [Path(p) for p in job['lods']]
    out, name, s = Path(job['out']), job['name'], job['settings']
    bpy.ops.wm.read_factory_settings(use_empty=True)
    kind = detect(paths) if s['kind'] == 'auto' else s['kind']
    materials, report, animated = {'static': static, 'skinned': skinned, 'vehicle': vehicle}[kind](job, paths)
    maps = flatten_materials(materials, out / (name + '.fbm'), name, s['roughness'])
    bpy.ops.object.select_all(action='SELECT')
    target = out / (name + '.fbx')
    # Unity's axes; Unreal's importer converts them. Static bakes the space transform into the
    # meshes so the LOD children import with identity rotations; empties and rigs keep theirs.
    bpy.ops.export_scene.fbx(filepath=str(target.resolve()), use_selection=True,
        axis_forward='-Z', axis_up='Y', apply_unit_scale=True, apply_scale_options='FBX_SCALE_ALL',
        bake_space_transform=kind == 'static', mesh_smooth_type='FACE', add_leaf_bones=False,
        bake_anim=animated, bake_anim_use_nla_strips=animated, bake_anim_use_all_actions=False,
        bake_anim_force_startend_keying=True, bake_anim_simplify_factor=0,
        path_mode='COPY', embed_textures=False)
    report = dict(report, kind=kind, name=name, file=target.name, maps=maps)
    print('ENGINE_PACKAGE ' + json.dumps(report))
    (out / 'engine.json').write_text(json.dumps(report, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
