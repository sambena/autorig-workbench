# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise authenticated settings, queued tools and source exclusion through HTTP."""
import json
from pathlib import Path
import unittest
import test_server as harness


class StudioAPI(unittest.TestCase):
    call=harness.ServerTest.call
    status=harness.ServerTest.status
    wait=harness.ServerTest.wait
    get=harness.ServerTest.get

    @classmethod
    def setUpClass(cls):
        harness.ServerTest.setUpClass.__func__(cls)
        folder=Path(cls.models)/'column';folder.mkdir(parents=True)
        harness.column_obj(str(folder/'column.obj'))
        (folder/'rig.json').write_text(json.dumps(harness.SPEC))

    @classmethod
    def tearDownClass(cls):harness.ServerTest.tearDownClass.__func__(cls)

    def test_guard_and_settings(self):
        self.assertEqual(self.status('GET','/api/v1/models/column/studio',token=None),401)
        payload={'settings':{'ratios':[1,.5]},'save':True}
        self.assertTrue(self.call('POST','/api/v1/models/column/studio/lods',payload)['saved'])
        view=self.call('GET','/api/v1/models/column/studio')
        self.assertEqual(view['settings']['lods']['ratios'],[1,.5])
        self.assertNotIn('generate',view['tools'])                     # paid: phase M8
        self.assertEqual(self.status('POST','/api/v1/models/column/studio/lods',body={'settings':{'ratios':[.5,1]}}),400)
        self.assertEqual(self.status('POST','/api/v1/models/column/studio/generate',body={'settings':{}}),501)
        self.assertEqual(self.status('POST','/api/v1/models/column/studio/nothing',body={}),404)
        self.assertEqual(self.status('POST','/api/v1/models/column/studio/reference',body={'settings':{'folder':'../../'}}),400)

    @unittest.skipUnless(harness.HAVE_BLENDER,'Blender unavailable')
    def test_queued_export_artifacts_and_source(self):
        job=self.call('POST','/api/v1/models/column/studio/lods',{'settings':{'ratios':[1,.5]}})
        self.assertEqual(self.wait(job)['state'],'done')
        result=self.call('GET','/api/v1/models/column/studio')
        lod=next(a for a in result['artifacts'] if a['path'].endswith('column_LOD1.glb'))
        self.assertIn('column.obj',self.call('GET','/api/v1/models/column')['status'].get('source',''))
        # an output brought in as a model of its own
        made=self.call('POST','/api/v1/models/column/studio/import',{'artifact':lod['path'],'name':'column_lod1'})
        self.assertEqual(made['model']['name'],'column_lod1')
        self.assertEqual(self.status('POST','/api/v1/models/column/studio/import',body={'artifact':lod['path'],'name':'column_lod1'}),400)


if __name__ == '__main__':
    unittest.main()
