"""Run a command and get its output, or None if anything goes wrong.

The only place that starts a waited-on command, so every call has a
timeout, never prompts, leaves nothing behind and fails soft the same way.
"""

import os
import signal
import subprocess
from pathlib import Path

GIT_TIMEOUT = 10  # seconds
TICKETS_TIMEOUT = 30  # seconds

# Added to the dashboard's own environment, so PATH and the rest stay.
NO_PROMPTS = {"GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1"}


def run(args: list[str], cwd: Path, timeout: float) -> str | None:
    """The command's stdout if it exits 0, else None.
    None also when it can't start, runs past `timeout`, or prints text
    that isn't UTF-8."""
    try:
        result = run_process(
            args,
            cwd=cwd,
            stdin=subprocess.DEVNULL,  # a prompt reads EOF instead of waiting
            env={**os.environ, **NO_PROMPTS},
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        # OSError: command not installed. SubprocessError: timeout.
        # ValueError: UnicodeDecodeError on undecodable output.
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def run_process(args, *, timeout, capture_output=False, **popen_kwargs):
    """Like `subprocess.run`, but a timeout kills the command's whole
    process group, not just the command, so its children don't linger.
    Tests replace this to fake commands."""
    popen_kwargs["start_new_session"] = True  # own process group, killed as one
    if capture_output:
        popen_kwargs["stdout"] = subprocess.PIPE
        popen_kwargs["stderr"] = subprocess.PIPE
    with subprocess.Popen(args, **popen_kwargs) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass  # the group already exited
            # Reap it. Not `communicate()`: a child that left the group
            # could hold the pipes open forever.
            process.wait()
            raise
    return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
