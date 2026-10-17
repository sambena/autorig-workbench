# SPDX-License-Identifier: GPL-3.0-or-later
# File > Commit to Git Repo… (core/git_commit.py): on a scratch repo with a local bare remote and no gh.
import contextlib, io, os, shutil, subprocess, sys, tempfile, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "autorig", "core"))
import git_commit  # noqa: E402


def sh(cwd, *args):
    return subprocess.run(["git"] + list(args), cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@unittest.skipUnless(shutil.which("git"), "needs git")
class GitCommitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.remote = os.path.join(self.tmp, "remote.git")
        self.top = os.path.join(self.tmp, "repo")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", self.remote], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", self.top], check=True)
        for k, v in (("user.name", "Test"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
            sh(self.top, "config", k, v)
        self.root = os.path.join(self.top, "Pack")
        os.makedirs(os.path.join(self.root, "Oak"))
        self.write("Pack/Oak/Oak.glb", "v1")
        self.write("README.md", "hi")
        sh(self.top, "add", "-A"); sh(self.top, "commit", "-q", "-m", "start")
        sh(self.top, "remote", "add", "origin", self.remote)
        sh(self.top, "push", "-q", "-u", "origin", "main")
        no_gh = mock.patch.object(git_commit.shutil, "which", lambda name: None if name == "gh" else shutil.which(name))
        no_gh.start(); self.addCleanup(no_gh.stop)

    def write(self, rel, text):
        p = os.path.join(self.top, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f: f.write(text)

    def commit(self, message):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            git_commit.commit_push_pr(self.root, message)
        return out.getvalue()

    def test_status_lists_only_the_models_folder(self):
        self.write("Pack/Oak/rig.json", "{}")
        self.write("README.md", "changed outside")
        st = git_commit.status(self.root, {"Oak"})
        self.assertEqual(st["branch"], "main")
        self.assertTrue(st["new_branch"])
        self.assertEqual([c["path"] for c in st["changes"]], ["Pack/Oak/rig.json"])
        self.assertEqual(st["models"], ["Oak"])

    def test_on_main_it_commits_to_a_new_branch_and_pushes(self):
        self.write("Pack/Oak/rig.json", "{}")
        self.write("README.md", "changed outside")
        log = self.commit("Rig the oak")
        branch = sh(self.top, "branch", "--show-current")
        self.assertTrue(branch.startswith("autorig/") and branch.endswith("-rig-the-oak"), branch)
        self.assertEqual(sh(self.remote, "log", "-1", "--format=%s", branch), "Rig the oak")
        self.assertEqual(sh(self.top, "show", "--name-only", "--format=", "HEAD"), "Pack/Oak/rig.json")
        self.assertIn("M README.md", sh(self.top, "status", "--porcelain"))      # outside the folder: left alone
        self.assertNotIn("PR: ", log)                                              # not a GitHub remote
        self.assertIn("not on GitHub", log)

    def test_a_second_commit_stays_on_the_branch(self):
        self.write("Pack/Oak/rig.json", "{}")
        self.commit("Rig the oak")
        branch = sh(self.top, "branch", "--show-current")
        self.write("Pack/Oak/rig.json", '{"x": 1}')
        self.commit("Tweak the oak")
        self.assertEqual(sh(self.top, "branch", "--show-current"), branch)
        self.assertEqual(sh(self.remote, "log", "-1", "--format=%s", branch), "Tweak the oak")

    def test_a_finished_branch_starts_again_from_the_latest_base(self):
        self.write("Pack/Oak/rig.json", "{}")
        self.commit("Rig the oak")
        old = sh(self.top, "branch", "--show-current")
        self.write("Pack/Oak/Oak.glb", "v2")
        with mock.patch.object(git_commit, "pull_request", return_value={"state": "MERGED", "url": "u"}),                 mock.patch.object(git_commit, "github_repo", return_value="me/pack"):
            self.assertTrue(git_commit.status(self.root)["restart"])
            log = self.commit("New oak mesh")
        self.assertIn("PR: https://github.com/me/pack/compare/main...autorig/", log)   # no gh: a link to open it
        new = sh(self.top, "branch", "--show-current")
        self.assertNotEqual(new, old)
        self.assertEqual(sh(self.top, "rev-parse", "HEAD~1"), sh(self.top, "rev-parse", "origin/main"))

    def test_nothing_to_commit(self):
        self.assertIn("Nothing to commit", self.commit("Nothing"))
        self.assertEqual(sh(self.top, "branch", "--show-current"), "main")

    def test_not_a_repo(self):
        st = git_commit.status(self.tmp)
        self.assertIsNone(st["repo"])


class GithubRepoTest(unittest.TestCase):
    def test_remote_forms(self):
        for url in ("https://github.com/sambena/asset-packs.git", "https://github.com/sambena/asset-packs",
                    "git@github.com:sambena/asset-packs.git", "git@github-asset-packs:sambena/asset-packs.git"):
            self.assertEqual(git_commit.github_repo(url), "sambena/asset-packs", url)
        self.assertIsNone(git_commit.github_repo("/srv/git/pack.git"))

    def test_branch_name(self):
        import datetime
        self.assertEqual(git_commit.branch_name("Rig the Oak & Pine!", datetime.datetime(2026, 10, 17, 9, 5)),
                         "autorig/20261017-0905-rig-the-oak-pine")


if __name__ == "__main__":
    unittest.main()
