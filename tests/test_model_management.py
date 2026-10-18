# SPDX-License-Identifier: GPL-3.0-or-later
"""Autorig Workbench: choosing the working folder (models root), and organising models in it."""
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "autorig", "core"))
import layout


def _model(root, *parts, files=None):
    d = os.path.join(root, *parts)
    os.makedirs(d)
    for name, body in (files or {parts[-1] + ".obj": "v 0 0 0\n"}).items():
        path = os.path.join(d, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
    return d


class LayoutTestCase(unittest.TestCase):
    """Each test gets its own models root, work folder and recent-roots file."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="autorig-org-")
        self.root = os.path.join(self.tmp, "models")
        self.work = os.path.join(self.tmp, "work")
        os.makedirs(self.root)
        self._saved = (layout.ROOT, layout.WORK, os.environ.get("AUTORIG_ROOTS_FILE"),
                       os.environ.get("AUTORIG_MODELS"), os.environ.get("AUTORIG_WORK"))
        os.environ["AUTORIG_ROOTS_FILE"] = os.path.join(self.tmp, "roots.json")
        layout.ROOT, layout.WORK = self.root, self.work

    def tearDown(self):
        layout.ROOT, layout.WORK = self._saved[0], self._saved[1]
        for key, value in zip(("AUTORIG_ROOTS_FILE", "AUTORIG_MODELS", "AUTORIG_WORK"), self._saved[2:]):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.tmp, ignore_errors=True)


class RootTest(LayoutTestCase):
    def test_set_root_switches_and_remembers(self):
        pack = os.path.join(self.tmp, "packs", "Heartroot")
        _model(pack, "Creatures", "wolf")
        _model(pack, "moth")
        info = layout.set_root(pack)
        self.assertEqual(os.path.normcase(layout.ROOT), os.path.normcase(pack))
        self.assertEqual(info["models"], 2)
        self.assertEqual(os.environ["AUTORIG_MODELS"], layout.ROOT)
        self.assertEqual(layout.active_root()[0], layout.ROOT)
        self.assertEqual([r["path"] for r in layout.recent_roots()], [layout.ROOT])

    def test_a_pack_shares_its_collections_work_folder(self):
        collection = os.path.join(self.tmp, "packs")
        os.makedirs(os.path.join(collection, "_autorig"))
        _model(collection, "Heartroot", "wolf")
        layout.set_root(os.path.join(collection, "Heartroot"))
        self.assertEqual(os.path.normcase(layout.WORK), os.path.normcase(os.path.join(collection, "_autorig")))

    def test_a_short_path_is_found_under_the_current_root(self):
        _model(self.root, "Creatures", "wolf")
        layout.set_root("Creatures")
        self.assertEqual(os.path.normcase(layout.ROOT), os.path.normcase(os.path.join(self.root, "Creatures")))

    def test_a_missing_folder_is_refused(self):
        with self.assertRaises(ValueError):
            layout.set_root(os.path.join(self.tmp, "nowhere"))
        self.assertEqual(layout.ROOT, self.root)
        with self.assertRaises(ValueError):
            layout.set_root("  ")

    def test_recent_roots_skip_folders_that_have_gone(self):
        a, b = os.path.join(self.tmp, "a"), os.path.join(self.tmp, "b")
        _model(a, "wolf"); _model(b, "moth")
        layout.set_root(a); layout.set_root(b); layout.set_root(a)
        self.assertEqual([os.path.basename(r["path"]) for r in layout.recent_roots()], ["a", "b"])
        shutil.rmtree(b)
        self.assertEqual([os.path.basename(r["path"]) for r in layout.recent_roots()], ["a"])

    def test_discover_offers_the_root_and_its_groups(self):
        _model(self.root, "Creatures", "wolf")
        _model(self.root, "Props", "crate")
        found = {r["name"]: r for r in layout.discover_roots()}
        self.assertTrue(found["models"]["current"])
        self.assertEqual(found["Creatures"]["kind"], "group")
        self.assertEqual(found["Creatures"]["models"], 1)
        self.assertIn("samples", found)


class OrganiseTest(LayoutTestCase):
    def test_group_move_rename_duplicate_delete(self):
        _model(self.root, "wolf")
        self.assertTrue(layout.create_group("Enemies")["created"])
        self.assertIn("Enemies", layout.groups())

        self.assertTrue(layout.move_model("wolf", "Enemies")["moved"])
        self.assertEqual(layout.group_of("wolf"), "Enemies")

        self.assertEqual(layout.duplicate_model("wolf", "wolf2")["name"], "wolf2")
        self.assertEqual(layout.group_of("wolf2"), "Enemies")

        self.assertTrue(layout.rename_model("wolf2", "dire_wolf")["renamed"])
        self.assertIn(("Enemies", "dire_wolf"), layout.all_models())
        self.assertNotIn(("Enemies", "wolf2"), layout.all_models())

        self.assertTrue(layout.move_model("dire_wolf", "")["moved"])
        self.assertEqual(layout.group_of("dire_wolf"), "")

        layout.delete_model("dire_wolf")
        self.assertEqual(layout.all_models(), [("Enemies", "wolf")])

    def test_moving_into_a_new_group_creates_it_and_an_emptied_group_goes(self):
        _model(self.root, "Old", "wolf")
        layout.move_model("wolf", "New")
        self.assertEqual(layout.groups(), ["New"])

    def test_rename_takes_the_rig_outputs_and_work_files_along(self):
        _model(self.root, "wolf", files={"wolf.obj": "v 0 0 0\n", "rigged/wolf.fbx": "x", "rigged/wolf.blend": "x"})
        for rel in ("audit/wolf.json", "qa/wolf_run_retarget.json", "audit/wolfhound.json"):
            path = os.path.join(self.work, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "w").close()
        layout.rename_model("wolf", "warg")
        d = os.path.join(self.root, "warg")
        self.assertEqual(sorted(os.listdir(os.path.join(d, "rigged"))), ["warg.blend", "warg.fbx"])
        self.assertTrue(os.path.isfile(os.path.join(self.work, "audit", "warg.json")))
        self.assertTrue(os.path.isfile(os.path.join(self.work, "qa", "warg_run_retarget.json")))
        self.assertTrue(os.path.isfile(os.path.join(self.work, "audit", "wolfhound.json")))   # another model's

    def test_duplicate_copies_the_source_and_spec_not_the_outputs(self):
        _model(self.root, "wolf", files={"wolf.obj": "v 0 0 0\n", "rig.json": "{}", "rigged/wolf.fbx": "x",
                                          "clips/wolf_clips.json": "{}", "textures/fur.png": "x"})
        layout.duplicate_model("wolf", "warg")
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "warg"))), ["rig.json", "textures", "wolf.obj"])

    def test_names_are_checked(self):
        _model(self.root, "wolf")
        _model(self.root, "moth")
        for bad in ("", "../x", "a b", "_hidden", "x/y"):
            with self.assertRaises(ValueError, msg=bad):
                layout.rename_model("wolf", bad)
            with self.assertRaises(ValueError, msg=bad):
                layout.create_group(bad)
        with self.assertRaises(ValueError):
            layout.rename_model("wolf", "Moth")              # names are keys: no two differing only in case
        with self.assertRaises(ValueError):
            layout.duplicate_model("wolf", "moth")
        with self.assertRaises(ValueError):
            layout.move_model("nobody", "Enemies")
        with self.assertRaises(ValueError):
            layout.create_group("wolf")                      # a model, not a group


class ServerOrganiseTest(unittest.TestCase):
    """The model manager's routes (/api/v1, gui/api_manage.py), as the page's Advanced > Manage sends them."""

    @classmethod
    def setUpClass(cls):
        cls.token = "test-" + secrets.token_hex(4)
        cls.tmp = tempfile.mkdtemp(prefix="autorig-org-api-")
        cls.models = os.path.join(cls.tmp, "models")
        _model(cls.models, "hero")
        cls.other = os.path.join(cls.tmp, "other")
        _model(cls.other, "Props", "crate")
        url_file = os.path.join(cls.tmp, "url.txt")
        config = os.path.join(cls.tmp, "server.json")       # the second collection the server offers
        with open(config, "w") as fh:
            json.dump({"collections": [{"name": "other", "path": cls.other}]}, fh)
        env = dict(os.environ, AUTORIG_URL_FILE=url_file, AUTORIG_ROOTS_FILE=os.path.join(cls.tmp, "roots.json"),
                   AUTORIG_CONFIG=config)
        cls.proc = subprocess.Popen([sys.executable, "-m", "autorig", "--models", cls.models, "--port", "0",
                                     "--no-browser", "--token", cls.token], cwd=REPO, env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        url = ""
        for _ in range(100):
            if os.path.exists(url_file):
                with open(url_file) as fh:
                    url = fh.read()
                if url:
                    break
            time.sleep(0.1)
        cls.base = url.split("/?")[0]

    @classmethod
    def tearDownClass(cls):
        cls.proc.kill()
        cls.proc.wait()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.token})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)

    def code(self, method, path, body=None):
        try:
            self.call(method, path, body); return 200
        except urllib.error.HTTPError as e:
            return e.code

    def names(self):
        return {m["name"]: m["group"] for m in self.call("GET", "/api/v1/models")["models"]}

    def test_organise_then_switch_collection(self):
        r = self.call("POST", "/api/v1/models/hero/move", {"group": "Champions"})
        self.assertTrue(r["moved"])
        self.assertEqual(r["model"]["group"], "Champions")
        self.assertEqual(self.names(), {"hero": "Champions"})
        self.assertIn("Champions", self.call("GET", "/api/v1/groups")["groups"])
        self.assertEqual(self.code("POST", "/api/v1/models/hero/move", {"group": "../up"}), 400)

        self.assertEqual(self.call("POST", "/api/v1/models/hero/duplicate", {"name": "villain"})["model"]["name"], "villain")
        r = self.call("POST", "/api/v1/models/villain/rename", {"name": "boss"})
        self.assertEqual((r["new_name"], r["model"]["name"]), ("boss", "boss"))
        self.assertEqual(self.names(), {"hero": "Champions", "boss": "Champions"})
        self.assertEqual(self.code("POST", "/api/v1/models/boss/rename", {"name": "hero"}), 400)
        self.assertEqual(self.code("POST", "/api/v1/models/nobody/rename", {"name": "x"}), 404)

        # deleting wants the name repeated
        self.assertEqual(self.code("DELETE", "/api/v1/models/boss"), 400)
        self.assertEqual(self.code("DELETE", "/api/v1/models/boss?confirm=hero"), 400)
        self.assertEqual(self.names(), {"hero": "Champions", "boss": "Champions"})
        self.call("DELETE", "/api/v1/models/boss?confirm=boss")
        self.assertEqual(self.names(), {"hero": "Champions"})
        self.assertEqual(self.code("DELETE", "/api/v1/models/boss?confirm=boss"), 404)

        info = self.call("POST", "/api/v1/collections/select", {"path": self.other})
        self.assertTrue(os.path.samefile(info["current"], self.other))
        self.assertEqual(self.names(), {"crate": "Props"})


if __name__ == "__main__":
    unittest.main()
