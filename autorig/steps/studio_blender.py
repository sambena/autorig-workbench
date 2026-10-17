# SPDX-License-Identifier: GPL-3.0-or-later
"""Scene operations for Model Studio; runs headlessly inside Blender."""
import argparse
import json
import math
import os
import sys
from pathlib import Path
import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "core"), str(HERE)]
import layout
import studio
from rig_quality import coords, probe_pose, protect_joints


def load(model, rig=False):
    source = layout.source_model(model)
    if rig:
        blend = Path(layout.rigged_dir(model)) / (layout.leaf(model)+".blend")
        if blend.exists(): source = str(blend)
    if not source: raise ValueError("model has no source")
    if source.lower().endswith(".blend"): bpy.ops.wm.open_mainfile(filepath=source)
    else:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        ext = Path(source).suffix.lower()
        # Preserve authored mesh boundaries and skeletons. The rig importer's weld is intentionally bypassed.
        if ext in (".glb", ".gltf"): bpy.ops.import_scene.gltf(filepath=source, merge_vertices=False)
        elif ext == ".fbx": bpy.ops.import_scene.fbx(filepath=source, automatic_bone_orientation=False)
        else: bpy.ops.wm.obj_import(filepath=source)
    helpers={b.custom_shape for a in bpy.context.scene.objects if a.type=='ARMATURE' for b in a.pose.bones if b.custom_shape}
    for helper in helpers: helper.hide_render=True; helper.hide_set(True)
    return [o for o in bpy.context.scene.objects if o.type == 'MESH' and o not in helpers and not o.name.startswith('qa_')]


def export(objects, path):
    # Keep parent transforms even when the imported source has empty grouping nodes.
    objects = list(objects)
    for obj in list(objects):
        parent=obj.parent
        while parent:
            if parent not in objects:objects.append(parent)
            parent=parent.parent
    bpy.ops.object.select_all(action='DESELECT')
    for o in objects: o.hide_set(False); o.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    if path.suffix == '.glb':
        bpy.ops.export_scene.gltf(filepath=str(path), export_format='GLB', use_selection=True,
            export_animations=False, export_extras=True, export_attributes=True, export_skins=True, export_morph=True)
    else:
        bpy.ops.export_scene.fbx(filepath=str(path), use_selection=True, axis_forward='-Z', axis_up='Y',
            add_leaf_bones=False, bake_anim=False, path_mode='COPY', embed_textures=False)


def surface_error(original, candidate):
    original.data.calc_loop_triangles()
    pts = coords(original); q = coords(candidate)
    tris = [t.vertices[:] for t in original.data.loop_triangles]
    if not tris or not len(q): return float('inf')
    tree = BVHTree.FromPolygons([Vector(p) for p in pts], tris, all_triangles=True)
    size = max(float(np.ptp(pts,axis=0).max()),1e-9)
    forward = max((tree.find_nearest(Vector(p))[3] or 0 for p in q[::max(1,len(q)//2048)]), default=0)
    candidate.data.calc_loop_triangles()
    reverse_tree=BVHTree.FromPolygons([Vector(p) for p in q], [t.vertices[:] for t in candidate.data.loop_triangles], all_triangles=True)
    reverse=max((reverse_tree.find_nearest(Vector(p))[3] or 0 for p in pts[::max(1,len(pts)//2048)]),default=0)
    return max(forward,reverse) / size


def lod_error(original, candidate):
    arm = original.find_armature()
    if not arm: return surface_error(original,candidate)
    with probe_pose(arm):
        worst = surface_error(original,candidate)
        bones = sorted((b for b in arm.pose.bones if b.bone.use_deform and b.parent),key=lambda b:(-b.length,b.name))[:12]
        for b in bones:
            for axis, angle in ((0,40),(1,60)):
                b.rotation_euler[axis] = math.radians(angle); bpy.context.view_layer.update()
                worst = max(worst,surface_error(original,candidate))
                b.rotation_euler[axis] = 0; bpy.context.view_layer.update()
        return worst


def lods(model, s, out):
    from studio_assets import lod_inputs, finish_lod_report
    inputs = lod_inputs(model)
    meshes = load(model,rig=True)
    if not meshes: raise ValueError("no meshes")
    # Bake no animation into bind meshes. Derived files retain the skeleton and shape keys.
    arms = list({m.find_armature() for m in meshes if m.find_armature()})
    for arm in arms:
        if arm.animation_data:
            arm.animation_data.action = None
            for track in arm.animation_data.nla_tracks: track.mute = True
        for bone in arm.pose.bones: bone.matrix_basis.identity()
        for bone in arm.pose.bones:
            for constraint in bone.constraints: constraint.mute=True
    bpy.context.view_layer.update()
    if s['normalize']:
        roots=set()
        for obj in meshes+arms:
            while obj.parent:obj=obj.parent
            roots.add(obj)
        root=bpy.data.objects.new('StudioMetres',None);bpy.context.scene.collection.objects.link(root)
        for obj in roots:obj.parent=root
        root.rotation_euler.z={'-y':0,'+y':math.pi,'+x':math.pi/2,'-x':-math.pi/2}[s['front']]
        bpy.context.view_layer.update()
        points=np.concatenate([coords(m) for m in meshes]);span=np.ptp(points,axis=0)
        factor=s['height']/max(span[2],1e-9) if s['height'] else s['length']/max(span[0],span[1],1e-9) if s['length'] else 1.0
        root.scale=(factor,)*3;bpy.context.view_layer.update()
        points=np.concatenate([coords(m) for m in meshes]);lo,hi=points.min(0),points.max(0)
        root.location=(-(lo[0]+hi[0])/2,-(lo[1]+hi[1])/2,-lo[2]);bpy.context.view_layer.update()
    total=0
    for m in meshes:m.data.calc_loop_triangles();total+=len(m.data.loop_triangles)
    base_ratio=min(1,s['triangles']/max(total,1)) if s['triangles'] else 1
    report = []
    for level, ratio in enumerate(s['ratios']):
        copies, details = [], []
        for original in meshes:
            requested=ratio*base_ratio
            chosen, used, err = None, requested, 0.0
            original.data.calc_loop_triangles(); before = len(original.data.loop_triangles)
            for attempt in range(5):
                candidate = original.copy(); candidate.data = original.data.copy()
                bpy.context.scene.collection.objects.link(candidate)
                candidate.name = original.name + '_LOD%d' % level
                used = 1-(1-requested)/(2**attempt) if attempt < 4 else 1.0
                note = None
                if used < 1 and candidate.data.shape_keys:
                    used = 1.0; note = 'shape keys preserved at full resolution'
                if used < 1:
                    from decimate import weld_same_weights
                    weld_same_weights(candidate, max(max(candidate.dimensions),1)*1e-6)
                    bpy.ops.object.select_all(action='DESELECT'); candidate.select_set(True)
                    bpy.context.view_layer.objects.active = candidate
                    group = protect_joints(candidate) if s['protect_joints'] else None
                    mod = candidate.modifiers.new('Studio LOD','DECIMATE'); mod.ratio=used; mod.use_collapse_triangulate=True
                    group_name=group.name if group else None
                    if group_name: mod.vertex_group=group_name; mod.vertex_group_factor=10
                    # Reduction in bind geometry, before the skin modifier.
                    while candidate.modifiers[0] != mod: bpy.ops.object.modifier_move_up(modifier=mod.name)
                    bpy.ops.object.modifier_apply(modifier=mod.name)
                    if group_name and candidate.vertex_groups.get(group_name): candidate.vertex_groups.remove(candidate.vertex_groups[group_name])
                    if candidate.find_armature():
                        bpy.ops.object.vertex_group_limit_total(group_select_mode='BONE_DEFORM',limit=4)
                        bpy.ops.object.vertex_group_normalize_all(group_select_mode='BONE_DEFORM',lock_active=False)
                err = lod_error(original,candidate)
                if err <= s['max_error'] or used == 1:
                    chosen=candidate; break
                data=candidate.data; bpy.data.objects.remove(candidate,do_unlink=True); bpy.data.meshes.remove(data)
            copies.append(chosen); chosen.data.calc_loop_triangles()
            details.append({'mesh':original.name,'triangles_before':before,'triangles':len(chosen.data.loop_triangles),
                            'requested_ratio':requested,'accepted_ratio':used,'surface_error':err,'note':note})
        remap=dict(zip(meshes,copies))
        for original,copy in remap.items():
            if original.parent in remap:copy.parent=remap[original.parent]
        path=out/(model+'_LOD%d.'%level+s['format'])
        export(copies+arms,path)
        report.append({'level':level,'file':path.name,'meshes':details})
        for c in copies:
            data=c.data; bpy.data.objects.remove(c,do_unlink=True)
            if data.users == 0: bpy.data.meshes.remove(data)
    points=np.concatenate([coords(m) for m in meshes])
    finish_lod_report(model, {'levels':report,'error_limit':s['max_error'],'bounds_metres':[points.min(0).tolist(),points.max(0).tolist()], 'animations':'bind meshes; use the existing animation exports'}, out, inputs=inputs)


def vehicle(model,s,out):
    from studio_ports import vehicle as port
    meshes=load(model)
    if any(o.type=='ARMATURE' for o in bpy.context.scene.objects): raise ValueError('Vehicle preparation needs a static source; skeletal models use LOD export')
    meshes=port.all_meshes(); port.face_minus_y(meshes,s['front'])
    port.size_to(meshes,s['length'],s['height'] or None); port.stand_on_ground(meshes)
    source_names=[m.name for m in meshes]
    if s['assignments']:
        by_name={o.name:o for o in meshes}
        if any(n not in by_name for n in s['assignments'].values()): raise ValueError('wheel assignment names a missing source object: '+', '.join(source_names))
        wheels={corner:by_name[name] for corner,name in s['assignments'].items()}
        rest=[m for m in meshes if m not in wheels.values()]
        if not rest: raise ValueError('vehicle must have a body')
        body=port.join(rest)
    else:
        # Automatic detection, with geometric carving explicitly opt-in.
        original_carve=port.carve_wheel
        if not s['carve_missing']: port.carve_wheel=lambda *args:None
        try: body,wheels=port.split_wheels(meshes)
        finally: port.carve_wheel=original_carve
    pivots={}
    for corner,w in wheels.items():
        if corner in s['pivots']:
            pivot=Vector(s['pivots'][corner])
            for v in w.data.vertices: v.co-=pivot
            w.location=pivot
        elif s['straighten']: port.pivot_at_centre(w)
        else:
            pivot=port.hub_centre(w)
            for v in w.data.vertices:v.co-=pivot
            w.location=pivot
        w.name='Wheel_'+corner; pivots[corner]=list(w.location)
        w['studio_part']='wheel'; w['studio_axle']=[1,0,0]
    body.name='Body'
    root=bpy.data.objects.new(model,None); bpy.context.scene.collection.objects.link(root)
    for o in [body]+list(wheels.values()):o.parent=root
    export([root,body]+list(wheels.values()),out/(model+'.glb'))
    export([root,body]+list(wheels.values()),out/(model+'.fbx'))
    bpy.ops.wm.save_as_mainfile(filepath=str(out/(model+'.blend')))
    studio.atomic_json(out/'parts.json',{'source_objects':source_names,'pivots':pivots,'missing_wheels':sorted(set(('FL','FR','RL','RR'))-set(wheels)),
        'coordinates':'Blender metres, Z up, front -Y, left +X','review':'Inspect wheel separation and spin before export to a game'})


def reference(model,s,out):
    meshes=load(model)
    if not meshes:raise ValueError('no meshes')
    root=bpy.data.objects.new('StudioReference',None);bpy.context.scene.collection.objects.link(root)
    for o in list(bpy.context.scene.objects):
        if o.parent is None and o is not root:o.parent=root
    root.rotation_euler.z=math.radians(s['turn']);bpy.context.view_layer.update()
    points=np.concatenate([coords(m) for m in meshes]);lo,hi=points.min(0),points.max(0)
    center=(lo+hi)/2; height=max(hi[2]-lo[2],.001); span=max(hi-lo)
    scene=bpy.context.scene;scene.render.engine='BLENDER_WORKBENCH'
    scene.display.shading.light='STUDIO';scene.display.shading.color_type='TEXTURE'
    scene.display.shading.background_type='VIEWPORT';scene.display.shading.background_color=(1,1,1)
    scene.view_settings.view_transform='Standard';scene.render.image_settings.file_format='PNG'
    scene.render.resolution_x=s['pixels'];scene.render.resolution_y=s['pixels'];scene.render.resolution_percentage=100
    data=bpy.data.cameras.new('StudioCamera');data.type='ORTHO'
    camera=bpy.data.objects.new('StudioCamera',data);scene.collection.objects.link(camera);scene.camera=camera
    for name,direction in [('Front',(0,-1,0)),('Side',(1,0,0)),('Back',(0,1,0)),('Face',(0,-1,0))]:
        target=Vector(center);data.ortho_scale=span*1.15
        if name=='Face':target.z=lo[2]+.91*height;data.ortho_scale=height*.23
        d=Vector(direction);camera.location=target+d*max(10,span*3)
        camera.rotation_euler=(-d).to_track_quat('-Z','Y').to_euler();data.clip_end=max(1000,span*10)
        scene.render.filepath=str(out/(name+'.png'));bpy.ops.render.render(write_still=True)


def wind(model,s,out):
    meshes=load(model,rig=True)
    for o in meshes:
        attr=o.data.color_attributes.get('StudioWind')
        if attr:o.data.color_attributes.remove(attr)
        source=o.data.color_attributes.active_color
        attr=o.data.color_attributes.new(name='StudioWind',type='FLOAT_COLOR',domain='CORNER')
        for loop in o.data.loops:
            z=(o.matrix_world@o.data.vertices[loop.vertex_index].co).z
            t=max(0,min(1,z/s['height']));t=t*t*(3-2*t)
            rgb=source.data[loop.vertex_index if source.domain=='POINT' else loop.index].color[:3] if source else (1,1,1)
            attr.data[loop.index].color=(*rgb,s['wind_base']+(s['wind_tip']-s['wind_base'])*t)
        o.data.color_attributes.active_color=attr
    arms=list({o.find_armature() for o in meshes if o.find_armature()})
    export(meshes+arms,out/'wind.glb')
    studio.atomic_json(out/'wind.json',{'attribute':'COLOR_0 alpha','range':[s['wind_base'],s['wind_tip']],
        'height_metres':s['height'],'requires':'A runtime shader consuming vertex alpha; no wind animation is baked'})


def main():
    ap=argparse.ArgumentParser();ap.add_argument('tool');ap.add_argument('model');ap.add_argument('--settings',required=True)
    a=ap.parse_args(sys.argv[sys.argv.index('--')+1:]);s=studio.validate(a.tool,json.loads(Path(a.settings).read_text()))
    out=Path(layout.pack_dir(a.model))/'_studio'/a.tool;out.mkdir(parents=True,exist_ok=True)
    {'lods':lods,'vehicle':vehicle,'reference':reference,'foliage':wind}[a.tool](a.model,s,out)


if __name__=='__main__':main()
