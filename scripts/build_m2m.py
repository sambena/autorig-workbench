"""Builds the Mesh2Motion web app (third_party/mesh2motion-app, a git submodule) into autorig/gui/m2m/,
where the GUI server serves it at /m2m/.  python scripts/build_m2m.py [--skip-install]

Needs Node 24 and npm. The submodule must be checked out first: git submodule update --init.
The build gets no secrets: Mesh2Motion's vite.config.js (our fork) passes only two CI values to the page."""
import argparse, os, shutil, subprocess, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(REPO, "third_party", "mesh2motion-app")
OUT = os.path.join(REPO, "autorig", "gui", "m2m")


def npm(*args):
    exe = shutil.which("npm") or shutil.which("npm.cmd")
    if not exe: sys.exit("build_m2m: npm not found; install Node 24")
    env = {k: v for k, v in os.environ.items() if not k.startswith("AUTORIG_")}
    subprocess.run([exe, *args], cwd=APP, check=True, env=env)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--skip-install", action="store_true", help="reuse the existing node_modules")
    a = ap.parse_args()
    if not os.path.isfile(os.path.join(APP, "package.json")):
        sys.exit("build_m2m: the submodule is missing; run: git submodule update --init third_party/mesh2motion-app")
    if not a.skip_install or not os.path.isdir(os.path.join(APP, "node_modules")):
        npm("ci", "--no-audit", "--no-fund")
    npm("run", "build")
    dist = os.path.join(APP, "dist")
    if os.path.isdir(OUT): shutil.rmtree(OUT)
    shutil.copytree(dist, OUT, ignore=shutil.ignore_patterns("*.map"))
    print("build_m2m: Mesh2Motion built into", OUT)


if __name__ == "__main__":
    main()
