# SPDX-License-Identifier: GPL-3.0-or-later
"""Evaluate each imported glTF take independently, including every mesh and morph owner."""
import argparse
import json
import math
import os
from pathlib import Path
import bpy
import numpy as np
from deformation import metrics, seam_pairs
from rig_quality import coords
import foot_contact
import studio

LEG_WORDS = ('leg', 'foot', 'ankle', 'toe', 'paw', 'hoof', 'claw_foot')


def foot_ends(arms):
    """The end of every leg: a leaf bone with a leg word in its name or an ancestor's (not an IK control), whatever
    the rig calls its bones."""
    out = []
    for arm in arms:
        for b in arm.pose.bones:
            if b.children: continue
            chain = [b] + list(b.parent_recursive)
            names = [c.name.lower() for c in chain]
            if any('ik' in n.split('_')[0] or 'pole' in n for n in names[:1]): continue
            if any(w in n for n in names for w in LEG_WORDS): out.append((arm, b))
    return out


def foot_points(ends, obs, rest):
    """Where each leg meets the ground: the lowest tenth (at rest) of the vertices skinned mostly to its foot bone, or
    to the nearest bone up the leg that has any. A glTF keeps no bone tails (the importer guesses a leaf's length), so
    the mesh is the measure. {key: vertex indices into the concatenated meshes}."""
    owned={};offset=0
    for o in obs:
        names={g.index:g.name for g in o.vertex_groups}
        for v in o.data.vertices:
            if v.groups:owned.setdefault(names.get(max(v.groups,key=lambda g:g.weight).group,''),[]).append(offset+v.index)
        offset+=len(o.data.vertices)
    out={}
    for arm,b in ends:
        bone=b
        while bone is not None and bone.name not in owned:bone=bone.parent
        if bone is None:continue
        idx=np.array(owned[bone.name]);idx=idx[np.argsort(rest[idx,2])][:max(3,len(idx)//10)]
        out[arm.name+':'+b.name]=idx
    return out


def owners():
    result=list(bpy.context.scene.objects)
    result += [o.data.shape_keys for o in bpy.context.scene.objects if o.type=='MESH' and o.data.shape_keys]
    return [o for o in result if o.animation_data]


def check(argv):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source');ap.add_argument('--every',type=int,default=1)
    ap.add_argument('--report');ap.add_argument('--loop',action='append',default=[],help='Clip name expected to loop; repeat for multiple clips')
    ap.add_argument('--seam-mm',type=float,default=1)
    ap.add_argument('--max-skate',type=float,help='Fail a clip whose foot-skate score is above this')
    a=ap.parse_args(argv)
    if a.every<1 or not math.isfinite(a.seam_mm) or a.seam_mm<=0:raise ValueError('sampling and seam limits must be positive')
    source=a.source
    if not source.lower().endswith(('.glb','.gltf')):
        import layout
        source=os.path.join(layout.rigged_dir(source),'preview.glb')
    bpy.ops.wm.read_factory_settings(use_empty=True);bpy.ops.import_scene.gltf(filepath=source,merge_vertices=False)
    obs=[o for o in bpy.context.scene.objects if o.type=='MESH']
    arms=[o for o in bpy.context.scene.objects if o.type=='ARMATURE']
    helpers={b.custom_shape for arm in arms for b in arm.pose.bones if b.custom_shape}
    obs=[o for o in obs if o not in helpers]
    if not obs:raise ValueError('no mesh in animation file')
    animated=owners();takes={}
    object_rest={o:o.matrix_basis.copy() for o in bpy.context.scene.objects}
    for owner in animated:
        ad=owner.animation_data;ad.action=None;ad.use_nla=False
        for track in ad.nla_tracks:
            track.is_solo=False;track.mute=True
            if track.strips:takes.setdefault(track.name,[]).append((owner,track))
    if not takes:raise ValueError('no imported animation tracks to evaluate')
    for name in a.loop:
        if name not in takes:raise ValueError('unknown loop clip: '+name)
    for arm in arms:arm.data.pose_position='REST'
    bpy.context.view_layer.update()
    rest=np.concatenate([coords(o) for o in obs]);scale=max(float(np.ptp(rest,axis=0).max()),1e-9)
    edges=[];triangles=[];offset=0
    labels=[];ignored=[]
    for oi,o in enumerate(obs):
        edges.extend((np.array(e.vertices)+offset).tolist() for e in o.data.edges)
        o.data.calc_loop_triangles();triangles.extend((np.array(t.vertices)+offset).tolist() for t in o.data.loop_triangles)
        arm=o.find_armature()
        labels.extend([arm.name if arm else o.name]*len(o.data.vertices))
        cut=o.data.attributes.get('_AUTORIG_CUT')
        ignored.extend([v.value>.5 for v in cut.data] if cut else [False]*len(o.data.vertices))
        offset+=len(o.data.vertices)
    pairs=seam_pairs(rest,ignored=ignored,labels=labels)
    ends=foot_points(foot_ends(arms),obs,rest)
    foot_floor=min((float(rest[i,2].mean()) for i in ends.values()),default=None)
    for arm in arms:arm.data.pose_position='POSE'
    feet=[(arm,b) for arm in arms for b in arm.pose.bones if any(k in b.name.lower() for k in ('foot','ankle')) and 'ik' not in b.name.lower()]
    # Retain the old hand/body cross-section diagnostic, with arbitrary armature prefixes.
    body_indices=[];offset=0
    for o in obs:
        names={g.index:g.name.lower() for g in o.vertex_groups}
        for v in o.data.vertices:
            name=names.get(max(v.groups,key=lambda g:g.weight).group,'') if v.groups else ''
            if any(k in name for k in ('hips','pelvis','spine','chest','torso','upleg','leg')) and 'hand' not in name:
                body_indices.append(offset+v.index)
        offset+=len(o.data.vertices)
    hands=[]
    for arm in arms:
        spine=next((b for b in arm.pose.bones if 'spine' in b.name.lower() or 'chest' in b.name.lower()),None)
        if spine:
            hands.extend((arm,b,spine) for b in arm.pose.bones if b.name.lower().split(':')[-1] in ('lefthand','righthand','hand.l','hand.r'))
    report={'format':'autorig-clip-quality/1','meshes':[o.name for o in obs],'seam_pairs':len(pairs),'clips':[]}
    failed=False
    for name,bindings in sorted(takes.items()):
        for obj,matrix in object_rest.items():obj.matrix_basis=matrix
        for arm in arms:
            for bone in arm.pose.bones:bone.matrix_basis.identity()
        for o in obs:
            if o.data.shape_keys:
                for key in o.data.shape_keys.key_blocks:key.value=0
        for owner,track in bindings:
            if len(track.strips)!=1:raise ValueError('expected one imported glTF strip per clip owner')
            strip=track.strips[0];owner.animation_data.action=strip.action
            if strip.action_slot:owner.animation_data.action_slot=strip.action_slot
        # the action's own keyed range: the importer lays a strip at 1..N+1 over an action keyed 0..N, and the action is
        # what is evaluated (sampling the strip's range read key 1 against a held last key: every loop mismatched)
        start=int(math.floor(min(st.action_frame_start for _,tr in bindings for st in tr.strips)))
        end=int(math.ceil(max(st.action_frame_end for _,tr in bindings for st in tr.strips)))
        frames=sorted(set(range(start,end+1,a.every))|{start,end})
        worst={};previous=None;first=None;last=None;foot_previous={};foot_slide={};ground=float(rest[:,2].min());movement=0.0;penetration=0.0
        root_ends=[];hand_inside={arm.name+':'+b.name:0 for arm,b,_ in hands};end_tracks={k:[] for k in ends}
        for frame in frames:
            bpy.context.scene.frame_set(frame);bpy.context.view_layer.update()
            p=np.concatenate([coords(o) for o in obs])
            if first is None:first=p.copy()
            last=p.copy()
            if previous is not None:movement=max(movement,float(np.linalg.norm(p-previous,axis=1).max(initial=0)))
            previous=p
            m=metrics(rest,p,edges,triangles,pairs,scale)
            for k,v in m.items():worst[k]=max(worst.get(k,0),v)
            penetration=max(penetration,ground-float(p[:,2].min()))
            for arm,hand,spine in hands:
                hp=np.array(arm.matrix_world@hand.tail);sp=np.array(arm.matrix_world@spine.head)
                body=p[body_indices];sl=body[np.abs(body[:,2]-hp[2])<.025*scale]
                if len(sl)<20:continue
                d=hp[:2]-sp[:2];length=float(np.linalg.norm(d))
                if length<1e-6:hand_inside[arm.name+':'+hand.name]+=1;continue
                u=d/length;rel=sl[:,:2]-sp[:2];along=rel@u;across=np.abs(rel@np.array([-u[1],u[0]]))
                wedge=along[(across<.03*scale)&(along>0)]
                if len(wedge) and length<float(np.percentile(wedge,90))-.01*scale:hand_inside[arm.name+':'+hand.name]+=1
            for arm,b in feet:
                pos=np.array(arm.matrix_world@b.head);key=arm.name+':'+b.name
                if key in foot_previous:
                    old,old_frame=foot_previous[key]
                    if pos[2]<ground+.04*scale and old[2]<ground+.04*scale and abs(pos[2]-old[2])<.005*scale:
                        dt=(frame-old_frame)/bpy.context.scene.render.fps
                        foot_slide[key]=max(foot_slide.get(key,0),float(np.linalg.norm(pos[:2]-old[:2]))/max(dt,1e-9))
                foot_previous[key]=(pos,frame)
            for k,i in ends.items():end_tracks[k].append(tuple(float(x) for x in p[i].mean(axis=0)))
            roots=[arm.matrix_world@next((b for b in arm.pose.bones if b.parent is None),arm.pose.bones[0]).head for arm in arms if len(arm.pose.bones)]
            if frame in (start,end):root_ends.append(np.array(roots))
        loop_error=None
        if name in a.loop:
            displacement=np.median(root_ends[-1]-root_ends[0],axis=0) if roots else np.median(last-first,axis=0)
            loop_error=float(np.linalg.norm((last-first)-displacement,axis=1).max(initial=0))
        skate=foot_contact.foot_skate(end_tracks,scale,fps=bpy.context.scene.render.fps/a.every,velocity=None if foot_contact.travels(name) else (0.0,0.0,0.0),floor=foot_floor) if ends else None
        errors=[]
        if movement<1e-7*scale:errors.append('no evaluated mesh movement')
        if worst.get('seam_gap',0)>a.seam_mm/1000:errors.append('seam crack')
        if worst.get('tear_edges',0)>0:errors.append('stretched edges')
        if loop_error is not None and loop_error>.001*scale:errors.append('loop pose mismatch')
        if skate and a.max_skate is not None and skate['score']>a.max_skate:errors.append('foot skate')
        entry={'name':name,'frames':len(frames),'range':[start,end],**worst,'floor_penetration':max(0,penetration),
               'near_ground_foot_speed':foot_slide,'foot_skate':skate,'hand_inside_body_frames':hand_inside,'loop_error':loop_error,'errors':errors}
        report['clips'].append(entry);failed|=bool(errors)
        print('CLIP_CHECK '+json.dumps(entry),flush=True)
        for owner,track in bindings:owner.animation_data.action=None
    report['pass']=not failed
    report['notes']=['Foot speed is a near-ground diagnostic; in-place clips require intended travel speed to interpret sliding.',
                     'foot_skate is measured against the ground: still, or under an in-place walk/trot/run moving at a speed estimated '
                     'from the feet '
                     '(ground_speed): thousandths of the model size a frame, 0 when planted feet stay put (MoMask weighting).',
                     'Floor penetration and compression are diagnostics; loop checks apply only to explicitly named --loop clips.']
    dest=Path(a.report) if a.report else Path(source).with_suffix('.quality.json')
    studio.atomic_json(dest,report)
    return int(failed)
