# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/'autorig'/'core'))
import studio
import blender


class Settings(unittest.TestCase):
    def test_invalid_lods(self):
        for ratios in ([.4,1],[1,.4,.6],[1,0],[1,float('nan')],[True,.4]):
            with self.subTest(ratios=ratios),self.assertRaises(ValueError):studio.validate('lods',{'ratios':ratios})

    def test_validation_and_defaults(self):
        for tool in studio.TOOLS:self.assertEqual(studio.validate(tool,{}),studio.DEFAULTS[tool])
        for tool,values in [('vehicle',{'assignments':{'FL':'wheel','FR':'wheel'}}),('materials',{'asset':'../../out'}),('generate',{'candidates':0}),('foliage',{'seed':1.5})]:
            with self.subTest(tool=tool),self.assertRaises(ValueError):studio.validate(tool,values)

    def test_confinement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'models';root.mkdir();refs=root/'refs';refs.mkdir()
            self.assertEqual(studio.input_folder('refs',str(root),[str(root)]),str(refs))
            with self.assertRaises(ValueError):studio.input_folder('..',str(root),[str(root)])
            studio.atomic_json(root/'studio.json',{'lods':{'ratios':[1,.5]}})
            self.assertEqual(studio.load(root)['lods']['ratios'],[1,.5])


class Generation(unittest.TestCase):
    def test_resume_does_not_submit_again(self):
        import studio_generation as g
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);refs=root/'references';refs.mkdir();out=root/'out';out.mkdir()
            for v in ('Front','Side','Back'):(refs/(v+'.png')).write_bytes(v.encode())
            s=studio.validate('generate',{})
            fingerprint=hashlib.sha256(json.dumps({'tier':s['tier'],'quality':s['quality'],'photos':[hashlib.sha256(p.read_bytes()).hexdigest() for p in g.photo_set(refs)]},sort_keys=True).encode()).hexdigest()
            dest=out/'candidate_1';dest.mkdir();(dest/'model.glb').write_bytes(b'already downloaded')
            studio.atomic_json(dest/'generation.json',{'fingerprint':fingerprint,'generation_id':'saved-id','status':'submitted'})
            calls=[]
            def command(args):
                calls.append(args)
                return {'files':[{'name':'model.glb','url':'https://example.com/model.glb'}]} if args[0]=='result' else {}
            with patch.object(g,'command',side_effect=command):g.generate(s,str(root),out)
            self.assertEqual([c[0] for c in calls],['poll','result'])
            self.assertEqual(json.loads((dest/'generation.json').read_text())['status'],'complete')
            with patch.object(g,'command') as command:g.generate(s,str(root),out);command.assert_not_called()

    def test_ambiguous_submission_never_pays_again(self):
        import studio_generation as g
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);refs=root/'references';refs.mkdir();out=root/'out';out.mkdir()
            for v in ('Front','Side','Back'):(refs/(v+'.png')).write_bytes(v.encode())
            with patch.object(g,'command',side_effect=RuntimeError('network lost')):
                with self.assertRaises(RuntimeError):g.generate(studio.DEFAULTS['generate'],str(root),out)
            with patch.object(g,'command') as command:
                with self.assertRaisesRegex(ValueError,'interrupted'):g.generate(studio.DEFAULTS['generate'],str(root),out)
                command.assert_not_called()


try:
    import numpy as np
    import deformation
except ImportError:np=None

try:
    from PIL import Image
except ImportError:Image=None


@unittest.skipUnless(Image is not None,'optional Pillow unavailable')
class Images(unittest.TestCase):
    def test_atlas_and_cards(self):
        import studio_images as images
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);images.foliage(studio.validate('foliage',{'cell':64}),out)
            with Image.open(out/'foliage.png') as atlas:self.assertEqual(atlas.size,(256,192))
            data=json.loads((out/'atlas.json').read_text());self.assertEqual(len(data['cells']),12)
            self.assertIn('grass_dry',[c['name'] for c in data['cells']])
            self.assertEqual((out/'foliage.obj').read_text().count('\nf '),24)

    def test_lossless_material_channels_and_provenance(self):
        import io
        import studio_images as images
        files={k:{'2k':{'png':{'url':'https://example.com/'+k}}} for k in ('diff','nor_gl','rough','ao','disp')}
        def fetch(url):
            if '/files/' in url:return json.dumps(files).encode()
            value=17 if url.endswith('/ao') else 123 if url.endswith('/rough') else 240
            stream=io.BytesIO();Image.new('RGB',(2,2),(value,)*3).save(stream,format='PNG');return stream.getvalue()
        with tempfile.TemporaryDirectory() as tmp,patch.object(images,'fetch',side_effect=fetch):
            out=Path(tmp);images.material(studio.validate('materials',{}),out)
            dest=out/'bark_brown_02_2k_orm'
            with Image.open(dest/'orm.png') as im:self.assertEqual(im.getpixel((0,0)),(17,123,0))
            self.assertEqual(json.loads((dest/'material.json').read_text())['license'],'CC0')


@unittest.skipUnless(np is not None,'numpy unavailable')
class Deformation(unittest.TestCase):
    def test_tear_and_compression(self):
        p=np.array([[0,0,0],[1,0,0],[0,1,0]],dtype=float);q=p.copy();q[1]=[3,0,0]
        result=deformation.metrics(p,q,[[0,1],[1,2]],[[0,1,2]])
        self.assertGreater(result['tear_edges'],0)
        self.assertFalse(deformation.no_regression([deformation.metrics(p,p,[[0,1]])],[result]))

    def test_intentional_seams_excluded(self):
        p=np.array([[0,0,0],[0,0,0],[1,0,0]])
        self.assertEqual(len(deformation.seam_pairs(p)),1)
        self.assertEqual(len(deformation.seam_pairs(p,ignored=[True,True,False])),0)


@unittest.skipUnless(blender.find(required=False),'Blender unavailable')
class BlenderPorts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='autorig-studio-');cls.root=Path(cls.tmp.name)
        cls.env=dict(os.environ,AUTORIG_MODELS=str(cls.root),AUTORIG_WORK=str(cls.root/'_work'))
        cls.fixture=cls.root/'fixture.py';cls.fixture.write_text(FIXTURE.replace('__REPO__',REPO.as_posix()),encoding='utf-8')
        r=subprocess.run(blender.command(str(cls.fixture),str(cls.root)),env=cls.env,capture_output=True,text=True,timeout=120)
        if r.returncode:raise RuntimeError(r.stdout[-5000:]+r.stderr[-1000:])

    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def run_tool(self,tool,model='prop',values=None):
        snapshot=self.root/(tool+'.json');studio.atomic_json(snapshot,studio.validate(tool,values or {}))
        r=subprocess.run([sys.executable,str(REPO/'autorig'/'steps'/'studio_job.py'),tool,model,'--settings',str(snapshot)],env=self.env,capture_output=True,text=True,timeout=180)
        self.assertEqual(r.returncode,0,r.stdout[-5000:]+r.stderr[-1000:])
        return self.root/model/'_studio'/tool

    def test_static_and_skinned_lods(self):
        for model in ('prop','skin'):
            with self.subTest(model=model):
                out=self.run_tool('lods',model,{'ratios':[1,.4]})
                report=json.loads((out/'lods.json').read_text())
                self.assertEqual(len(report['levels']),2)
                self.assertTrue((out/(model+'_LOD1.glb')).is_file())
                self.assertLessEqual(report['levels'][1]['meshes'][0]['surface_error'],.02+1e-6)

    def test_vehicle(self):
        out=self.run_tool('vehicle','truck')
        report=json.loads((out/'parts.json').read_text())
        self.assertEqual(report['missing_wheels'],[])
        self.assertEqual(len(report['pivots']),4)
        self.assertTrue((out/'truck.glb').exists())

    def test_normalized_static_prop(self):
        out=self.run_tool('lods',values={'ratios':[1], 'normalize':True,'height':3})
        lo,hi=json.loads((out/'lods.json').read_text())['bounds_metres']
        self.assertAlmostEqual(lo[2],0,places=5)
        self.assertAlmostEqual(hi[2],3,places=5)

    def test_reference(self):
        try:import PIL
        except ImportError:self.skipTest('optional Pillow unavailable')
        out=self.run_tool('reference',values={'pixels':128})
        self.assertTrue((out/'comparison.png').exists())

    def test_each_clip_and_secondary_mesh(self):
        report=self.root/'clip-report.json'
        r=subprocess.run(blender.command('clip_check.py',str(self.root/'clips.glb'),'--report',str(report)),env=self.env,capture_output=True,text=True,timeout=120)
        self.assertTrue(report.is_file(),r.stdout[-4000:]+r.stderr[-3000:])
        data=json.loads(report.read_text())
        self.assertEqual(len(data['clips']),2)
        self.assertEqual(len(data['meshes']),2)
        self.assertEqual(r.returncode,1)
        broken=next(c for c in data['clips'] if 'crack' in c['name'])
        quiet=next(c for c in data['clips'] if 'quiet' in c['name'])
        self.assertIn('seam crack',broken['errors'])
        self.assertNotIn('seam crack',quiet['errors'])
        self.assertGreater(broken['frames'],2)

    def test_explicit_cut_tag_survives_glb(self):
        report=self.root/'tagged-report.json'
        r=subprocess.run(blender.command('clip_check.py',str(self.root/'tagged.glb'),'--report',str(report)),env=self.env,capture_output=True,text=True,timeout=120)
        self.assertEqual(r.returncode,0,r.stdout[-3000:]+r.stderr[-2000:])
        self.assertTrue(json.loads(report.read_text())['pass'])


FIXTURE=r'''
import bpy,sys,os,math
from pathlib import Path
root=Path(sys.argv[sys.argv.index('--')+1])
for name in ('prop','truck','skin'):
 folder=root/name;folder.mkdir();(folder/'rig.json').write_text('{"schema":"autorig-spec/1"}')
 bpy.ops.wm.read_factory_settings(use_empty=True)
 if name=='truck':
  bpy.ops.mesh.primitive_cube_add(size=2,location=(0,0,1.6));body=bpy.context.object;body.scale=(1,2,.4)
  for x,y in ((-1,-1.3),(1,-1.3),(-1,1.3),(1,1.3)):
   bpy.ops.mesh.primitive_cylinder_add(vertices=32,radius=.5,depth=.25,location=(x,y,.5),rotation=(0,math.pi/2,0))
 elif name=='prop':bpy.ops.mesh.primitive_uv_sphere_add(segments=24,ring_count=16)
 else:
  bpy.ops.mesh.primitive_uv_sphere_add(segments=24,ring_count=16);mesh=bpy.context.object
  data=bpy.data.armatures.new('Rig');arm=bpy.data.objects.new('Rig',data);bpy.context.collection.objects.link(arm)
  bpy.context.view_layer.objects.active=arm;arm.select_set(True);bpy.ops.object.mode_set(mode='EDIT')
  a=data.edit_bones.new('root');a.head=(0,0,-1);a.tail=(0,0,0)
  b=data.edit_bones.new('tip');b.head=(0,0,0);b.tail=(0,0,1);b.parent=a
  bpy.ops.object.mode_set(mode='OBJECT');mesh.parent=arm;mod=mesh.modifiers.new('Skin','ARMATURE');mod.object=arm
  g0=mesh.vertex_groups.new(name='root');g1=mesh.vertex_groups.new(name='tip')
  for v in mesh.data.vertices:
   t=max(0,min(1,(v.co.z+.5)));g0.add([v.index],1-t,'REPLACE');g1.add([v.index],t,'REPLACE')
  rd=folder/'rigged';rd.mkdir();bpy.ops.wm.save_as_mainfile(filepath=str(rd/'skin.blend'))
 bpy.ops.export_scene.gltf(filepath=str(folder/(name+'.glb')),export_format='GLB',export_animations=False)
# Two independent takes; only a secondary mesh cracks in the second take.
for v in mesh.data.vertices:g0.add([v.index],1,'REPLACE');g1.add([v.index],0,'REPLACE')
data=bpy.data.meshes.new('SmallSeam');data.from_pydata([(0,.3,0),(1,.3,0),(0,.6,0),(0,.3,0),(1,.3,0),(0,.6,0)],[],[(0,1,2),(3,4,5)])
small=bpy.data.objects.new('SmallSeam',data);bpy.context.collection.objects.link(small);small.parent=arm
mod=small.modifiers.new('Skin','ARMATURE');mod.object=arm
for name,ids in [('root',[0,1,2]),('tip',[3,4,5])]:small.vertex_groups.new(name=name).add(ids,1,'REPLACE')
arm.animation_data_create()
for name in ('quiet','crack'):
 act=bpy.data.actions.new(name);act.use_fake_user=True;arm.animation_data.action=act
 for b in arm.pose.bones:b.rotation_mode='XYZ';b.rotation_euler=(0,0,0);b.location=(0,0,0)
 for f in (1,10):
  arm.pose.bones['root'].location.x=.1*(f-1)/9
  arm.pose.bones['root'].keyframe_insert(data_path='location',frame=f)
  arm.pose.bones['tip'].rotation_euler.x=.7*(f-1)/9 if name=='crack' else 0
  arm.pose.bones['tip'].keyframe_insert(data_path='rotation_euler',frame=f)
arm.animation_data.action=None
for b in arm.pose.bones:b.matrix_basis.identity()
bpy.ops.export_scene.gltf(filepath=str(root/'clips.glb'),export_format='GLB',export_animations=True,export_animation_mode='ACTIONS',export_frame_range=False,export_force_sampling=True,export_attributes=True)
cut=small.data.attributes.new(name='_AUTORIG_CUT',type='FLOAT',domain='POINT')
for v in cut.data:v.value=1
bpy.ops.export_scene.gltf(filepath=str(root/'tagged.glb'),export_format='GLB',export_animations=True,export_animation_mode='ACTIONS',export_frame_range=False,export_force_sampling=True,export_attributes=True)
sys.path.insert(0,'__REPO__/autorig/core')
import mesh_doctor
before=len(small.data.vertices)
mesh_doctor.heal_mesh_object(small,{'preserve_seams':True})
assert len(small.data.vertices)==before,'healing reconnected protected seam'
'''
