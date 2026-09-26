# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: Process Watchdog, Execution Timeouts, and Resource Guard.
#
# Prevents pipeline hangs, degenerate geometry freezing, infinite bone-heat skinning,
# and memory exhaustion by actively monitoring Blender subprocesses with configurable
# optional per-step timeouts (env AUTORIG_STEP_TIMEOUT / AUTORIG_TIMEOUT_<SCRIPT>) and a memory cap (Linux).

import os
import re
import signal
import subprocess
import sys
import threading
import time

# No step has a timeout unless the user sets one: a bone-heat rig of a dense sculpt or an audit that renders its
# sheets takes as long as it takes, and a step killed at an arbitrary limit reads as a failed rig. AUTORIG_STEP_TIMEOUT
# (seconds) limits every step; AUTORIG_TIMEOUT_<SCRIPT> (RERIG, AUDIT, MAKE_CLIPS...) one script.
DEFAULT_TIMEOUTS = {}

DEFAULT_MAX_MEMORY_MB = 4096.0  # 4 GB max per step


def get_timeout_for_script(script_name, explicit_timeout=None):
    """The timeout in seconds for a step script, or 0 for none:
      1. explicit_timeout, when given
      2. AUTORIG_TIMEOUT_<SCRIPT> (the script's stem, upper case: AUTORIG_TIMEOUT_RERIG)
      3. AUTORIG_STEP_TIMEOUT, for every step
    """
    if explicit_timeout is not None and float(explicit_timeout) > 0:
        return float(explicit_timeout)

    stem = os.path.splitext(os.path.basename(str(script_name)))[0].lower().replace("-", "_")
    for var in (f"AUTORIG_TIMEOUT_{stem.upper()}", "AUTORIG_STEP_TIMEOUT"):
        raw = os.environ.get(var)
        if raw:
            try:
                val = float(raw)
                if val > 0:
                    return val
            except ValueError:
                pass
    return 0.0


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


def kill_process_tree(proc_or_pid):
    """Terminates a process and any of its children cleanly (SIGTERM then SIGKILL)."""
    pid = proc_or_pid.pid if hasattr(proc_or_pid, "pid") else int(proc_or_pid)
    if not pid or pid <= 1:
        return

    # Check child processes on Linux via /proc
    child_pids = []
    task_dir = f"/proc/{pid}/task"
    if os.path.isdir(task_dir):
        try:
            for tid in os.listdir(task_dir):
                children_file = f"/proc/{pid}/task/{tid}/children"
                if os.path.isfile(children_file):
                    with open(children_file, "r") as fh:
                        child_pids.extend([int(c) for c in fh.read().split()])
        except Exception:
            pass

    # First attempt SIGTERM on children and parent
    for cpid in child_pids:
        try:
            os.kill(cpid, signal.SIGTERM)
        except OSError:
            pass

    if hasattr(proc_or_pid, "terminate"):
        try:
            proc_or_pid.terminate()
        except OSError:
            pass
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    # Give up to 0.5s for termination
    time.sleep(0.1)

    # Force SIGKILL if still alive
    for cpid in child_pids:
        try:
            os.kill(cpid, signal.SIGKILL)
        except OSError:
            pass

    if hasattr(proc_or_pid, "kill"):
        try:
            proc_or_pid.kill()
        except OSError:
            pass
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


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
    limit_time = get_timeout_for_script(argv[0] if argv else "default", timeout)
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
        proc.wait(timeout=2.0)
    except Exception:
        kill_process_tree(proc)

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
    """Watchdog timer for streaming job runners (e.g. GUI server runner)."""
    def __init__(self, job, proc, label, timeout_seconds=None, script=None):
        self.job = job
        self.proc = proc
        self.label = label
        self.timeout = get_timeout_for_script(script or label, timeout_seconds)
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
                self.job.add(f"!! {self.label} timed out after {self.timeout:.1f}s - process killed by watchdog")
                kill_process_tree(self.proc)
                break

            self.stopped.wait(0.5)
