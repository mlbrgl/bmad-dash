import subprocess
import threading
import urllib.request
from urllib.parse import urlencode

import pytest

from bmad_dash import commands
from bmad_dash.initiatives import list_worktrees, worktree_id
from bmad_dash.server import make_server, validate_root

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
    """A `subprocess.run` stand-in that always fails with `failure`."""
    def run(args, **kwargs):
        if isinstance(failure, BaseException):
            raise failure
        return failure
    return run


@pytest.fixture(params=list(FAILURES))
def fail_commands(request, monkeypatch):
    """Make every command fail one way. Call it after git setup is done."""
    return lambda: monkeypatch.setattr(
        commands.subprocess, "run", failing_run(FAILURES[request.param])
    )


# --- the helper ---


def test_run_gives_stdout_on_success(tmp_path):
    assert commands.run(["echo", "hi"], tmp_path, 5) == "hi\n"


def test_run_passes_cwd_and_timeout(monkeypatch, tmp_path):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "out", "")

    monkeypatch.setattr(commands.subprocess, "run", run)
    assert commands.run(["git", "x"], tmp_path, 7) == "out"
    args, kwargs = calls[0]
    assert args == ["git", "x"]
    assert kwargs["cwd"] == tmp_path
    assert kwargs["timeout"] == 7
    assert kwargs["capture_output"] and kwargs["text"]
    assert kwargs["encoding"] == "utf-8"


@pytest.mark.parametrize("failure", list(FAILURES.values()), ids=list(FAILURES))
def test_run_failure_gives_none(monkeypatch, failure, tmp_path):
    monkeypatch.setattr(commands.subprocess, "run", failing_run(failure))
    assert commands.run(["git", "x"], tmp_path, 5) is None


def test_real_nonzero_and_missing_give_none(tmp_path):
    assert commands.run(["git", "rev-parse"], tmp_path, 5) is None  # not a repo
    assert commands.run(["no-such-command-xyz"], tmp_path, 5) is None


def test_real_timeout_gives_none(tmp_path):
    assert commands.run(["sleep", "5"], tmp_path, 0.2) is None


# --- callers fail soft ---


def test_list_worktrees_failure_is_none(project, fail_commands):
    fail_commands()
    assert list_worktrees(project) is None


def test_validate_root_failure_is_not_a_git_repo(project, fail_commands):
    fail_commands()
    assert validate_root(project) == f"{project} is not a git repository"


def serve(project):
    server = make_server(project, 0, launch=lambda path: None)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_page_renders_when_everything_fails(project, fail_commands):
    # A tickets.py to run, so its failure (not its absence) is what shows.
    script = project / "_bmad" / "method" / "scripts" / "tickets.py"
    script.parent.mkdir(parents=True)
    script.write_text("")
    fail_commands()
    server = serve(project)
    try:
        url = f"http://127.0.0.1:{server.server_port}/"
        with urllib.request.urlopen(url) as response:
            assert response.status == 200
            html = response.read().decode()
    finally:
        server.shutdown()
        server.server_close()
    assert "Matched one" in html
    assert "feature-a" not in html
    assert html.count("no worktree") == 5  # every row
    assert "Couldn't reach GitHub" in html
    assert "Couldn't read tickets: tickets.py failed" in html


def test_vscode_post_when_git_fails_redirects_to_failure(project, fail_commands):
    body = urlencode({"id": worktree_id(project), "slug": "initiative-shared"})
    fail_commands()
    server = serve(project)
    try:
        status, response = send(server, body=body)
    finally:
        server.shutdown()
        server.server_close()
    assert status == 303
    assert response.getheader("Location") == "/?vscode_failed=initiative-shared"
