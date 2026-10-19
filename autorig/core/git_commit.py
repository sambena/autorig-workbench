# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: commit the models folder's changes to its git repo, push them and open a pull request.
#
#   python autorig/core/git_commit.py --root <models folder> --message "..." [--title "..."]
#   python autorig/core/git_commit.py --root <models folder> --status      what would be committed, as JSON
#
# Only the models folder is committed (git add -A -- <root>), so a models folder inside a bigger repo takes
# nothing else with it; .gitignore is respected. asset-packs keeps each model folder as a <model>.tar.zst archive
# (its tools/archives.py), so the changed folders are packed first; each pack is its own repo, a submodule of
# asset-packs, and gets its own commit, branch and pull request, apart from asset-packs' own files. On the default branch (main) the commit goes on a new branch
# autorig/<date>-<time>; on any other branch it is added there, and a pull request already open for that branch
# picks it up. The push and the pull request use the GitHub CLI (gh) when it is installed: gh's login is offered
# to git as a credential helper, so a machine where gh is signed in can push over https without anything else
# set up. Without gh the push uses git's own credentials and the log ends with a link to open the pull request.
# The File menu's "Commit to Git Repo…" runs this as a job (gui/server.py).
import argparse, datetime, json, os, re, shutil, subprocess, sys


def git(top, *args, check=True, strip=True):
    p = subprocess.run(["git", "-C", top] + list(args), capture_output=True, text=True, encoding="utf-8",
                       errors="replace", stdin=subprocess.DEVNULL)
    if check and p.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (args[0], (p.stderr or p.stdout).strip()))
    return (p.stdout.strip() if strip else p.stdout) if p.returncode == 0 else None


def toplevel(root):
    if not os.path.isdir(root): return None
    try:
        return git(root, "rev-parse", "--show-toplevel", check=False)
    except OSError:                         # no git at all
        return None


def default_branch(top):
    ref = git(top, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD", check=False)
    if ref: return ref.rsplit("/", 1)[-1]
    for b in ("main", "master"):
        if git(top, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/" + b, check=False): return b
    return "main"


def github_repo(url):
    """owner/name from a GitHub remote: https://github.com/o/n(.git), git@github.com:o/n.git or an ssh alias."""
    m = re.search(r"github[^:/]*[:/]+([^/]+)/([^/]+?)(?:\.git)?/?$", url or "")
    return "%s/%s" % m.groups() if m else None


def pull_request(top, branch):
    """The newest pull request from this branch, {state, url}, or None (or no gh)."""
    if not branch or not shutil.which("gh"): return None
    try:
        p = subprocess.run(["gh", "pr", "list", "--head", branch, "--state", "all", "--limit", "1", "--json",
                            "state,url"], cwd=top, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
        rows = json.loads(p.stdout or "[]") if p.returncode == 0 else []
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    return {"state": rows[0]["state"], "url": rows[0]["url"]} if rows else None


def submodules(top, root):
    """The checked-out git submodules inside the models folder, as full paths. asset-packs keeps each pack
    (Heartroot, Smeltdown, TornadoTitans) in its own repo, a submodule of the collection's."""
    out = git(top, "config", "-f", os.path.join(top, ".gitmodules"), "--get-regexp", r"^submodule\..*\.path$",
              check=False) or ""
    inside = os.path.normcase(os.path.normpath(root)).rstrip(os.sep) + os.sep
    found = []
    for line in out.splitlines():
        path = os.path.normpath(os.path.join(top, line.split(" ", 1)[-1]))
        if (os.path.normcase(path) + os.sep).startswith(inside) and os.path.exists(os.path.join(path, ".git")):
            found.append(path)
    return sorted(found)


def archive_tool(root):
    """asset-packs' tools/archives.py, in the models folder or a folder above it, or None."""
    d = os.path.abspath(root)
    while True:
        tool = os.path.join(d, "tools", "archives.py")
        if os.path.isfile(tool): return tool
        up = os.path.dirname(d)
        if up == d: return None
        d = up


def pack_models(root):
    """asset-packs keeps each model folder as a <model>.tar.zst archive and git ignores the folders, so a change
    reaches git only once the folder is packed again. Packs the changed folders of the packs under root."""
    tool = archive_tool(root)
    if not tool: return
    packs = [root] if os.path.isfile(os.path.join(root, "pack.json")) else \
        [os.path.join(root, d) for d in sorted(os.listdir(root)) if os.path.isfile(os.path.join(root, d, "pack.json"))]
    if packs:
        print("Packing the changed model folders into their archives.", flush=True)
        run([sys.executable, tool, "pack"] + packs, root)


def changes(top, root, skip=()):
    """[(code, path)] for every change under the models folder, paths relative to the repo. skip: folders left out
    (the submodules, which are committed in their own repos)."""
    spec = [root] + [":(exclude)" + os.path.relpath(s, top).replace(os.sep, "/") for s in skip]
    out = git(top, "status", "--porcelain", "-z", "--untracked-files=all", "--", *spec,
              strip=False) or ""   # unstripped: " M path" starts with the space of an unstaged change
    rows, items = [], out.split("\0")
    i = 0
    while i < len(items):
        it = items[i]; i += 1
        if len(it) < 4: continue
        code, path = it[:2], it[3:]
        if code[0] in "RC": i += 1          # a rename's old path follows
        rows.append((code.strip() or code, path))
    return rows


def status(root, known=None):
    """Everything the File menu's dialog shows before committing. known: model names, to say which ones changed.
    A models folder holding submodules lists each one's own status under "repos"; "count" and "models" cover all."""
    root = os.path.abspath(root)
    top = toplevel(root)
    if not top:
        return {"repo": None, "root": root, "error": "the models folder %s is not in a git repo" % root}
    top = os.path.normpath(top)
    subs = submodules(top, root)
    st = repo_status(top, root, known, subs)
    st["repos"] = [status(s, known) for s in subs]
    st["count"] += sum(r.get("count", 0) for r in st["repos"])
    st["models"] = sorted(set(st["models"]).union(*[r.get("models", ()) for r in st["repos"]]))[:200]
    return st


def repo_status(top, root, known=None, skip=()):
    branch = git(top, "branch", "--show-current", check=False) or ""
    base = default_branch(top)
    url = git(top, "remote", "get-url", "origin", check=False) or ""
    rows = changes(top, root, skip)
    pr = None if branch in ("", base) or not github_repo(url) else pull_request(top, branch)
    # a branch whose pull request is merged or closed is finished: start a fresh one from the latest base
    restart = bool(pr and pr["state"] != "OPEN")
    # a model's folder in the path, or its archive (Heartroot/the_knight.tar.zst)
    models = sorted({seg for _, p in rows for seg in p.split("/")[:-1] + [p.rsplit("/", 1)[-1][:-len(".tar.zst")]]
                     if seg in (known or ())})
    # commits not on GitHub yet: against the branch's remote copy, or against the base for a branch never pushed
    pushed = branch and git(top, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/" + branch, check=False)
    ahead = git(top, "rev-list", "--count", "%s..HEAD" % ("origin/" + (branch if pushed else base)), check=False) \
        if branch and branch != base else None
    return {"repo": top, "root": root, "branch": branch, "base": base, "remote": url, "github": github_repo(url),
            "new_branch": branch in ("", base) or restart, "restart": restart, "pr": pr, "changes": [{"code": c, "path": p} for c, p in rows],
            "count": len(rows), "models": models[:200], "unpushed": int(ahead) if ahead and ahead.isdigit() else 0,
            "gh": bool(shutil.which("gh"))}


def branch_name(message, now=None):
    now = now or datetime.datetime.now()
    slug = re.sub(r"[^a-z0-9]+", "-", message.lower()).strip("-")[:40].rstrip("-")
    return "autorig/%s%s" % (now.strftime("%Y%m%d-%H%M"), "-" + slug if slug else "")


def run(cmd, cwd):
    """Run it, its output into the log line by line as it comes (a big push runs for minutes)."""
    print("$ " + " ".join('"%s"' % a if " " in a else a for a in cmd), flush=True)
    p = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace")
    out = []
    for line in p.stdout:
        line = line.rstrip()
        out.append(line)
        print("  " + line, flush=True)
    if p.wait() != 0:
        raise RuntimeError("%s exited with %d" % (cmd[0], p.returncode))
    return "\n".join(out).strip()


def commit_push_pr(root, message, title=None, body=None):
    """Pack the changed models, then commit, push and open a pull request in each repo that has changes: every
    submodule inside the models folder (each pack's own repo), then the folder's repo for the rest. The links."""
    root = os.path.abspath(root)
    top = toplevel(root)
    if not top: raise RuntimeError("the models folder %s is not in a git repo" % root)
    pack_models(root)
    subs = submodules(os.path.normpath(top), root)
    urls = [commit_repo(s, message, title, body) for s in subs]
    urls.append(commit_repo(root, message, title, body, subs))
    return [u for u in urls if u]


def commit_repo(root, message, title=None, body=None, skip=()):
    top = os.path.normpath(toplevel(root))
    st = repo_status(top, root, skip=skip)
    top, base, branch = st["repo"], st["base"], st["branch"]
    if not st["count"] and (st["new_branch"] or not st["unpushed"]):
        print("Nothing to commit under %s." % root); return None
    # gh's login as a credential helper, after any helper git already has: a machine where only gh is signed in
    # can fetch and push over https. useSparse=false: a push after many renames otherwise takes very long (asset-packs)
    remote = ["git", "-c", "pack.useSparse=false"]
    if st["gh"] and st["github"]: remote += ["-c", "credential.https://github.com.helper=!gh auth git-credential"]
    print("Repo %s, branch %s, %d change(s) under %s" % (top, branch or "(detached)", st["count"], root), flush=True)
    # the latest base first: the push sends only what GitHub lacks, which git can work out only from a commit both
    # sides have. A base fetched long ago (or never: VM 207) made git send the whole repo, and GitHub gave up (HTTP 500)
    run(remote + ["fetch", "-q", "origin", base], top)
    if st["restart"]:
        print("%s's pull request is %s: starting a new branch from the latest %s." % (branch, st["pr"]["state"].lower(), base))
    if st["new_branch"]:
        branch = branch_name(title or message)
        # the changes come along; from a finished branch, onto the latest base
        run(["git", "switch", "-c", branch] + (["origin/" + base] if st["restart"] else []), top)
    if st["count"]:
        run(["git", "add", "-A", "--", root] + [":(exclude)" + os.path.relpath(s, top).replace(os.sep, "/")
                                                 for s in skip], top)
        run(["git", "commit", "-q", "-m", message], top)
    print("Pushing to GitHub: a big change takes minutes, and git says nothing until it is done.", flush=True)
    run(remote + ["push", "-u", "origin", branch], top)
    url = None
    if not st["github"]:
        print("origin is not on GitHub, so there is no pull request to open.")
    elif st["gh"]:
        found = subprocess.run(["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "url", "--jq",
                                ".[0].url"], cwd=top, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        url = found.stdout.strip() if found.returncode == 0 else ""
        if url:
            print("The open pull request for %s now has this commit." % branch)
        else:
            out = run(["gh", "pr", "create", "--base", base, "--head", branch, "--title", title or message,
                       "--body", body or "%s\n\nCommitted from the Autorig Workbench (File > Commit to Git Repo)." % message],
                      top)
            url = out.splitlines()[-1] if out else ""
    else:
        url = "https://github.com/%s/compare/%s...%s?expand=1" % (st["github"], base, branch)
        print("gh is not installed here: open this link to create the pull request.")
    if url: print("PR: " + url, flush=True)
    return url


def main(argv=None):
    ap = argparse.ArgumentParser(description="Commit the models folder's changes, push them and open a pull request.")
    ap.add_argument("--root", required=True, help="the models folder")
    ap.add_argument("--message", help="the commit message")
    ap.add_argument("--title", help="the pull request's title (default: the message)")
    ap.add_argument("--status", action="store_true", help="print what would be committed, as JSON, and stop")
    a = ap.parse_args(argv)
    if a.status:
        print(json.dumps(status(a.root), indent=1)); return 0
    if not (a.message or "").strip(): ap.error("--message is required")
    try:
        commit_push_pr(a.root, a.message.strip(), (a.title or "").strip() or None)
    except RuntimeError as e:
        print("!! %s" % e); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
