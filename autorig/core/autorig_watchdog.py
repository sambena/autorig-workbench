# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: Process Watchdog, Execution Timeouts, and Resource Guard.
#
# Keeps a hung step from blocking the pipeline: every Blender subprocess runs under a per-step time limit (and, where
# the platform reports memory, a memory cap), and a step that goes over is killed together with every process it
# started. Named autorig_watchdog so it never shadows the PyPI "watchdog" package.
#
# Limits are looked up by the step's script (argv), never by a job's free-text label, so a model named "trimmer" or
# "survey_drone" does not borrow another step's limit.

import os
import signal
import subprocess
import sys
import threading
import time

# Seconds per step, keyed by step name. Generous: bone heat on a dense mesh, an audit that renders its sheets or a
# clip bake with preview frames can take minutes on a real model. 0 means no limit of its own: the wrappers that
# only start other steps (auto_tune.py, retarget.py) are bounded by the limits of the Blender steps they run.
DEFAULT_TIMEOUTS = {
    "survey": 300.0,
    "facing": 300.0,
    "suggest": 300.0,
    "source_preview": 300.0,
    "rig": 600.0,
    "trim": 300.0,
    "audit": 600.0,
    "audit_all": 0.0,
    "clips": 600.0,
    "preview": 300.0,
    "publish": 120.0,
    "doctor": 300.0,
    "retarget_probe": 120.0,
    "retarget_worker": 1800.0,
    "retarget": 0.0,
    "auto_tune": 0.0,
    "default": 600.0,
}

# script stem -> step name (a builder run through run_builder.py is a rig)
SCRIPT_STEPS = {
    "rerig": "rig", "rerig_humanoid": "rig", "run_builder": "rig",
    "decimate": "trim", "make_clips": "clips", "preview_glb": "preview", "mesh_doctor": "doctor",
}

DEFAULT_MAX_MEMORY_MB = 4096.0  # 4 GB max per step

_KILL = getattr(signal, "SIGKILL", signal.SIGTERM)          # Windows has no SIGKILL


def step_of(script_or_argv):
    """The step name for a script path, or for a command line (the first .py in it: blender ... --python
    steps/x.py -- ..., or python -u steps/x.py ...)."""
    script = script_or_argv
    if isinstance(script_or_argv, (list, tuple)):
        script = next((a for a in script_or_argv if str(a).lower().endswith(".py")), "")
    stem = os.path.splitext(os.path.basename(str(script)))[0].lower().replace("-", "_")
    return SCRIPT_STEPS.get(stem, stem)


def _env_seconds(var):
    val = os.environ.get(var)
    if not val:
        return None
    try:
        v = float(val)
        return v if v > 0 else None
    except ValueError:
        return None


def get_timeout_for_script(script_name, explicit_timeout=None):
    """Determines the appropriate timeout in seconds for a given step script (or command line).
    Precedence:
      1. explicit_timeout argument if provided
      2. AUTORIG_STEP_TIMEOUT environment variable (global override)
      3. AUTORIG_TIMEOUT_<STEP> environment variable (e.g. AUTORIG_TIMEOUT_RIG, or the script's own stem)
      4. DEFAULT_TIMEOUTS for the step
      5. DEFAULT_TIMEOUTS['default']
    Returns 0 for "no limit".
    """
    if explicit_timeout is not None and float(explicit_timeout) > 0:
        return float(explicit_timeout)

    step = step_of(script_name)
    # a wrapper that only starts other steps is bounded by theirs: a per-step limit on it would cut a whole
    # multi-step run short (the global override included); its own AUTORIG_TIMEOUT_<STEP> can still set one
    wrapper = DEFAULT_TIMEOUTS.get(step) == 0.0

    g = _env_seconds("AUTORIG_STEP_TIMEOUT")
    if g and not wrapper:
        return g
    script = script_name if not isinstance(script_name, (list, tuple)) else step
    stem = os.path.splitext(os.path.basename(str(script)))[0].lower().replace("-", "_")
    for name in dict.fromkeys((step, stem)):
        v = _env_seconds(f"AUTORIG_TIMEOUT_{name.upper()}")
        if v:
            return v

    return DEFAULT_TIMEOUTS.get(step, DEFAULT_TIMEOUTS["default"])


def get_max_memory_mb(explicit_limit=None):
    """Returns max memory threshold in MB. 0 or None disables memory limit."""
    if explicit_limit is not None:
        return float(explicit_limit)
    env_val = os.environ.get("AUTORIG_MAX_MEMORY_MB")
    if env_val:
        try:
            val = float(env_val)
            if val >= 0:
                return val
        except ValueError:
            pass
    return DEFAULT_MAX_MEMORY_MB


def get_process_rss_mb(pid):
    """Returns resident set size (RSS) memory in MB for a process PID."""
    if not pid or pid <= 0:
        return 0.0

    # Linux /proc/<pid>/status
    proc_status = f"/proc/{pid}/status"
    if os.path.isfile(proc_status):
        try:
            with open(proc_status, "r", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("VmRSS:"):
                        parts = line.split()
                        if len(parts) >= 2:
                            kb = float(parts[1])
                            return round(kb / 1024.0, 2)
        except (OSError, ValueError):
            return 0.0

    # macOS / BSD fallback via ps
    if sys.platform == "darwin":
        try:
            r = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, timeout=1)
            if r.returncode == 0 and r.stdout.strip():
                kb = float(r.stdout.strip().split()[0])
                return round(kb / 1024.0, 2)
        except Exception:
            pass

    return 0.0


def _children_posix(pid):
    """Every descendant PID of pid, deepest first, from /proc (Linux). Empty where /proc is missing."""
    out = []
    task_dir = f"/proc/{pid}/task"
    if not os.path.isdir(task_dir):
        return out
    try:
        for tid in os.listdir(task_dir):
            with open(f"{task_dir}/{tid}/children") as fh:
                for c in fh.read().split():
                    out = _children_posix(int(c)) + [int(c)] + out
    except (OSError, ValueError):
        pass
    return out


def kill_process_tree(proc_or_pid):
    """Kills a process and every process it started (a Python wrapper's Blender, Blender's own children).
    Windows: taskkill /T /F walks the tree. Elsewhere: the descendants from /proc, SIGTERM then SIGKILL."""
    pid = proc_or_pid.pid if hasattr(proc_or_pid, "pid") else int(proc_or_pid)
    if not pid or pid <= 1 or pid == os.getpid():
        return

    if sys.platform == "win32":
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, timeout=15,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            pass
        if hasattr(proc_or_pid, "kill"):                  # the process itself, if taskkill could not
            try:
                proc_or_pid.kill()
            except OSError:
                pass
        return

    children = _children_posix(pid)
    for sig in (signal.SIGTERM, _KILL):
        for cpid in children:
            try:
                os.kill(cpid, sig)
            except OSError:
                pass
        try:
            if hasattr(proc_or_pid, "send_signal"):
                proc_or_pid.send_signal(sig)
            else:
                os.kill(pid, sig)
        except OSError:
            pass
        if sig == signal.SIGTERM:
            time.sleep(0.2)


# Processes this server or tool started and could not stop: a kill that did not take (the process survived its
# tree kill). The reaper retries exactly these and nothing else: it never picks processes by name or command line.
_abandoned = {}
_abandoned_lock = threading.Lock()


def _alive(pid):
    if sys.platform == "win32":
        try:
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True,
                               timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return str(pid) in r.stdout
        except (OSError, subprocess.SubprocessError):
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def abandon(proc):
    """Remembers a process that outlived its kill, for reap_orphaned_blender_processes to try again."""
    if proc is not None and proc.poll() is None:
        with _abandoned_lock:
            _abandoned[proc.pid] = proc


def reap_orphaned_blender_processes():
    """Kills again the processes this tool started that survived being killed (see abandon). Returns the PIDs it
    killed. Processes it did not start are never touched."""
    killed = []
    with _abandoned_lock:
        items = list(_abandoned.items())
    for pid, proc in items:
        if proc.poll() is None:
            kill_process_tree(proc)
            killed.append(pid)
        with _abandoned_lock:
            _abandoned.pop(pid, None)
    return killed


def run_with_watchdog(argv, timeout=None, memory_limit_mb=None, poll_interval=0.25,
                      capture_output=True, **kw):
    """Executes a command with active watchdog timeout and memory guard.
    Returns: subprocess.CompletedProcess instance with added diagnostics attributes:
      - .timed_out (bool)
      - .memory_exceeded (bool)
      - .duration (float)
      - .peak_rss_mb (float)
    """
    t0 = time.time()
    limit_time = get_timeout_for_script(argv or "default", timeout)
    limit_mem = get_max_memory_mb(memory_limit_mb)

    # Pop kwargs that shouldn't conflict with Popen
    kw_copy = dict(kw)
    kw_copy.pop("timeout", None)

    stdout_dest = subprocess.PIPE if capture_output else None
    stderr_dest = subprocess.PIPE if capture_output else None

    try:
        proc = subprocess.Popen(
            argv,
            stdout=stdout_dest,
            stderr=stderr_dest,
            text=True,
            errors="replace",
            **kw_copy
        )
    except Exception as e:
        cp = subprocess.CompletedProcess(args=argv, returncode=1, stdout="", stderr=str(e))
        cp.timed_out = False
        cp.memory_exceeded = False
        cp.duration = 0.0
        cp.peak_rss_mb = 0.0
        return cp

    timed_out = False
    memory_exceeded = False
    peak_rss = 0.0
    stdout_chunks = []
    stderr_chunks = []

    def _reader(stream, chunks):
        try:
            for chunk in iter(lambda: stream.read(4096), ""):
                chunks.append(chunk)
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    t_out = None
    t_err = None
    if proc.stdout is not None:
        t_out = threading.Thread(target=_reader, args=(proc.stdout, stdout_chunks), daemon=True)
        t_out.start()
    if proc.stderr is not None:
        t_err = threading.Thread(target=_reader, args=(proc.stderr, stderr_chunks), daemon=True)
        t_err.start()

    while True:
        rc = proc.poll()
        if rc is not None:
            break

        elapsed = time.time() - t0

        # Memory inspection
        rss = get_process_rss_mb(proc.pid)
        if rss > peak_rss:
            peak_rss = rss

        # Check memory limit
        if limit_mem > 0 and rss > limit_mem:
            memory_exceeded = True
            kill_process_tree(proc)
            break

        # Check timeout limit
        if limit_time > 0 and elapsed > limit_time:
            timed_out = True
            kill_process_tree(proc)
            break

        time.sleep(poll_interval)

    duration = round(time.time() - t0, 2)

    try:
        proc.wait(timeout=5.0)
    except Exception:
        kill_process_tree(proc)
        try:
            proc.wait(timeout=5.0)
        except Exception:
            abandon(proc)

    if t_out is not None:
        t_out.join(timeout=2.0)
    if t_err is not None:
        t_err.join(timeout=2.0)

    stdout_data = "".join(stdout_chunks)
    stderr_data = "".join(stderr_chunks)

    final_rc = proc.returncode

    if timed_out:
        final_rc = 124  # Standard timeout exit code
        msg = f"\n!! TIMEOUT: Step execution timed out after {limit_time:.1f}s - process killed by watchdog\n"
        stderr_data += msg
        stdout_data += msg
    elif memory_exceeded:
        final_rc = 137  # OOM kill exit code
        msg = f"\n!! MEMORY_LIMIT: Process exceeded memory limit ({peak_rss:.1f}MB > {limit_mem:.1f}MB) - killed by watchdog\n"
        stderr_data += msg
        stdout_data += msg

    cp = subprocess.CompletedProcess(
        args=argv,
        returncode=final_rc if final_rc is not None else 1,
        stdout=stdout_data,
        stderr=stderr_data,
    )
    cp.timed_out = timed_out
    cp.memory_exceeded = memory_exceeded
    cp.duration = duration
    cp.peak_rss_mb = peak_rss
    return cp


class JobWatchdog:
    """Watchdog timer for streaming job runners (e.g. GUI server runner). The limit comes from the command line's
    script (argv), the label is only for the log."""
    def __init__(self, job, proc, label, timeout_seconds=None, argv=None):
        self.job = job
        self.proc = proc
        self.label = label
        self.timeout = get_timeout_for_script(argv or getattr(proc, "args", None) or label, timeout_seconds)
        self.mem_limit = get_max_memory_mb()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._monitor, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stopped.set()

    def _monitor(self):
        t0 = time.time()
        while not self.stopped.is_set():
            if self.proc.poll() is not None:
                break

            elapsed = time.time() - t0
            rss = get_process_rss_mb(self.proc.pid)

            if self.mem_limit > 0 and rss > self.mem_limit:
                self.job.add(f"!! {self.label} exceeded memory threshold ({rss:.1f}MB > {self.mem_limit:.1f}MB) - killing PID {self.proc.pid}")
                kill_process_tree(self.proc)
                break

            if self.timeout > 0 and elapsed > self.timeout:
                self.job.add(f"!! {self.label} timed out after {self.timeout:.1f}s - process killed by watchdog "
                             f"(raise AUTORIG_TIMEOUT_{step_of(self.proc.args if hasattr(self.proc, 'args') else self.label).upper()} "
                             f"for bigger models)")
                kill_process_tree(self.proc)
                break

            self.stopped.wait(0.5)
