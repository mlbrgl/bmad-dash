"""Run a command and get its output, or None if anything goes wrong.

The only place that calls `subprocess.run`, so every call has a timeout
and fails soft the same way.
"""

import subprocess
from pathlib import Path

GIT_TIMEOUT = 10  # seconds
TICKETS_TIMEOUT = 30  # seconds


def run(args: list[str], cwd: Path, timeout: float) -> str | None:
    """The command's stdout if it exits 0, else None.
    None also when it can't start, runs past `timeout`, or prints text
    that isn't UTF-8."""
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
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
