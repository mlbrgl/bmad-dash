import os
import subprocess
import time
import urllib.request
from urllib.parse import urlencode

import pytest

from bmad_dash import commands
from bmad_dash.initiatives import list_worktrees, worktree_id
from bmad_dash.server import validate_root

from test_vscode import send

UNDECODABLE = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

# Every way a command can fail: exits non-zero, hangs, is missing, prints non-UTF-8.
FAILURES = {
    "exit 1": subprocess.CompletedProcess([], 1, "", "boom"),
    "timeout": subprocess.TimeoutExpired("cmd", 10),
    "missing": FileNotFoundError("cmd"),
    "undecodable": UNDECODABLE,
}


def failing_run(failure):
    """A `commands.run_process` stand-in that always fails with `failure`."""
    def run(args, **kwargs):
        if isinstance(failure, BaseException):
            raise failure
        return failure
    return run


@pytest.fixture(params=list(FAILURES))
def fail_commands(request, monkeypatch):
    """Make every command fail one way. Call it after git setup is done."""
    return lambda: monkeypatch.setattr(
        commands, "run_process", failing_run(FAILURES[request.param])
    )


# --- the helper ---


def test_run_gives_stdout_on_success(tmp_path):
    assert commands.run(["echo", "hi"], tmp_path, 5) == "hi\n"


def test_run_passes_cwd_and_timeout(monkeypatch, tmp_path):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "out", "")

    monkeypatch.setattr(commands, "run_process", run)
    assert commands.run(["git", "x"], tmp_path, 7) == "out"
    args, kwargs = calls[0]
    assert args == ["git", "x"]
    assert kwargs["cwd"] == tmp_path
    assert kwargs["timeout"] == 7
    assert kwargs["capture_output"] and kwargs["text"]
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert kwargs["env"]["GH_PROMPT_DISABLED"] == "1"


@pytest.mark.parametrize("failure", list(FAILURES.values()), ids=list(FAILURES))
def test_run_failure_gives_none(monkeypatch, failure, tmp_path):
    monkeypatch.setattr(commands, "run_process", failing_run(failure))
    assert commands.run(["git", "x"], tmp_path, 5) is None


def test_real_nonzero_and_missing_give_none(tmp_path):
    assert commands.run(["git", "rev-parse"], tmp_path, 5) is None  # not a repo
    assert commands.run(["no-such-command-xyz"], tmp_path, 5) is None


def test_real_timeout_gives_none(tmp_path):
    assert commands.run(["sleep", "5"], tmp_path, 0.2) is None


def test_stdin_is_closed(tmp_path):
    # `cat` waits on an open stdin; a closed one gives EOF at once.
    started = time.monotonic()
    assert commands.run(["cat"], tmp_path, 5) == ""
    assert time.monotonic() - started < 2


def test_prompts_are_disabled_and_path_kept(tmp_path):
    script = 'echo "$GIT_TERMINAL_PROMPT $GH_PROMPT_DISABLED"; echo "$PATH"'
    out = commands.run(["sh", "-c", script], tmp_path, 5)
    assert out == f"0 1\n{os.environ['PATH']}\n"


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_timeout_leaves_no_child_behind(tmp_path):
    # The child sleeps long after its parent times out, holding stdout open.
    script = "sleep 30 & echo $! > child; echo $$ > parent; wait"
    started = time.monotonic()
    assert commands.run(["sh", "-c", script], tmp_path, 0.5) is None
    assert time.monotonic() - started < 5
    pids = [int((tmp_path / name).read_text()) for name in ("parent", "child")]
    deadline = time.monotonic() + 5  # init reaps the orphaned child
    while any(alive(pid) for pid in pids) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not any(alive(pid) for pid in pids)


# --- callers fail soft ---


def test_list_worktrees_failure_is_none(project, fail_commands):
    fail_commands()
    assert list_worktrees(project) is None


def test_validate_root_failure_is_not_a_git_repo(project, fail_commands):
    fail_commands()
    assert validate_root(project) == f"{project} is not a git repository"


def test_page_renders_when_everything_fails(project, fail_commands, serve):
    # A tickets.py to run, so its failure (not its absence) is what shows.
    script = project / "_bmad" / "method" / "scripts" / "tickets.py"
    script.parent.mkdir(parents=True)
    script.write_text("")
    fail_commands()
    server = serve(project, launch=lambda path: None)
    url = f"http://127.0.0.1:{server.server_port}/"
    with urllib.request.urlopen(url) as response:
        assert response.status == 200
        html = response.read().decode()
    assert "Matched one" in html
    assert "feature-a" not in html
    assert html.count("no worktree") == 5  # every row
    assert "Couldn't reach GitHub" in html
    assert "Couldn't read tickets: tickets.py failed" in html


def test_vscode_post_when_git_fails_redirects_to_failure(project, fail_commands, serve):
    body = urlencode({"id": worktree_id(project), "slug": "initiative-shared"})
    fail_commands()
    server = serve(project, launch=lambda path: None)
    status, response = send(server, body=body)
    assert status == 303
    assert response.getheader("Location") == "/?vscode_failed=initiative-shared"
