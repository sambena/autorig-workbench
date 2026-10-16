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

    @classmethod
    def setUpClass(cls):
        harness.ServerTest.setUpClass.__func__(cls)
        folder=Path(cls.models)/'column';folder.mkdir(parents=True)
        harness.column_obj(str(folder/'column.obj'))
        (folder/'rig.json').write_text(json.dumps(harness.SPEC))

    @classmethod
    def tearDownClass(cls):harness.ServerTest.tearDownClass.__func__(cls)

    def test_guard_and_settings(self):
        self.assertEqual(self.status('/api/studio?model=column',token=None),403)
        payload={'model':'column','tool':'lods','settings':{'ratios':[1,.5]}}
        self.assertTrue(self.call('/api/studio/save',payload)['saved'])
        self.assertEqual(self.call('/api/studio?model=column')['settings']['lods']['ratios'],[1,.5])
        payload['settings']={'ratios':[.5,1]}
        self.assertEqual(self.status('/api/studio/run',body=payload),400)
        self.assertEqual(self.status('/api/studio/run',body={'model':'column','tool':'generate','settings':{'folder':'../../'}}),400)

    @unittest.skipUnless(harness.HAVE_BLENDER,'Blender unavailable')
    def test_queued_export_artifacts_and_source(self):
        job=self.call('/api/studio/run',{'model':'column','tool':'lods','settings':{'ratios':[1,.5]}})
        self.assertEqual(self.wait(job)['state'],'done')
        result=self.call('/api/studio?model=column')
        self.assertTrue(any(a['path'].endswith('column_LOD1.glb') for a in result['artifacts']))
        self.assertIn('column.obj',self.call('/api/model?name=column')['status'].get('source',''))
