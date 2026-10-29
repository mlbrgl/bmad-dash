import http.client
import json
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import timedelta
from urllib.parse import unquote

import pytest
from conftest import STACK_TIMES, add_walkthrough

import bmad_dash.server as server_module
from bmad_dash import commands as commands_module
from bmad_dash.initiatives import Initiative, Worktree
from bmad_dash.page import LocalPreview
from bmad_dash.previews import PreviewConfig


@pytest.fixture
def server(project, serve):
    return serve(project)


def test_binds_localhost_only(server):
    assert server.server_address[0] == "127.0.0.1"


def test_serves_dashboard_at_root(server):
    url = f"http://127.0.0.1:{server.server_port}/"
    with urllib.request.urlopen(url) as response:
        assert response.status == 200
        assert response.headers["Content-Type"].startswith("text/html")
        html = response.read().decode()
    assert "Matched one" in html
    assert "feature-a" in html
    # wt-b (detached) loses initiative-shared to the main checkout, listed first.
    assert "detached" not in html
    # No tickets.py here: each row has a failed-tickets panel with its own header row.
    assert html.count('class="init') == 5


def test_no_github_remote_shows_no_github_line(server):
    url = f"http://127.0.0.1:{server.server_port}/"
    with urllib.request.urlopen(url) as response:
        html = response.read().decode()
    assert "Couldn't reach GitHub" not in html
    assert 'class="place pr"' not in html


REMOTE = "origin\tgit@github.com:o/r.git (fetch)\n"
# gt state for feature-a -> b -> c (feature-a is initiative-matched's branch).
GT_STATE = json.dumps({
    "main": {"trunk": True},
    "feature-a": {"trunk": False, "parents": [{"ref": "main", "sha": "1"}]},
    "b": {"trunk": False, "parents": [{"ref": "feature-a", "sha": "2"}]},
    "c": {"trunk": False, "parents": [{"ref": "b", "sha": "3"}]},
})


def pr_json(number, head, base="main", state="OPEN", draft=False):
    return {"number": number, "headRefName": head, "baseRefName": base,
            "state": state, "isDraft": draft,
            "url": f"https://github.com/o/r/pull/{number}"}


def fake_github(monkeypatch, calls, gh):
    """Real git, except a GitHub remote; fake `gt state` and `gh`.
    `gh(args)` gives (exit code, stdout)."""
    real_run = subprocess.run

    def run(args, **kwargs):
        if args[:2] == ["git", "remote"]:
            return subprocess.CompletedProcess(args, 0, REMOTE, "")
        if args[0] in ("gt", "gh"):
            calls.append(args)
            code, stdout = (0, GT_STATE) if args[0] == "gt" else gh(args)
            return subprocess.CompletedProcess(args, code, stdout, "")
        return real_run(args, **kwargs)

    monkeypatch.setattr(commands_module, "run_process", run)


def get_page(srv):
    url = f"http://127.0.0.1:{srv.server_port}/"
    with urllib.request.urlopen(url) as response:
        return response.read().decode()


def track_with_graphite(project):
    (project / ".git" / ".graphite_repo_config").write_text("{}")


def test_gt_stack_top_pr_reaches_the_page(project, monkeypatch, serve):
    track_with_graphite(project)
    calls = []

    def gh(args):
        # Only c has a PR; a lookup by head finds it whoever wrote it.
        head = args[args.index("--head") + 1]
        return 0, json.dumps([pr_json(31, "c", base="b")] if head == "c" else [])

    fake_github(monkeypatch, calls, gh)
    html = get_page(serve(project))
    assert 'href="https://github.com/o/r/pull/31"' in html
    assert "Couldn't reach GitHub" not in html
    assert ["gt", "state"] in calls
    assert not any("--author" in args for args in calls)  # no fallback walk


def test_untracked_repo_runs_no_gt_and_walks_my_prs(project, monkeypatch, serve):
    calls = []
    fake_github(monkeypatch, calls, lambda args: (0, json.dumps([pr_json(7, "feature-a")])))
    html = get_page(serve(project))
    assert not any(args[0] == "gt" for args in calls)
    assert any("--author" in args for args in calls)
    assert 'href="https://github.com/o/r/pull/7"' in html


def test_gh_failure_in_gt_path_shows_header_and_no_pr_icons(project, monkeypatch, serve):
    track_with_graphite(project)
    fake_github(monkeypatch, [], lambda args: (1, ""))
    html = get_page(serve(project))
    assert "Couldn't reach GitHub" in html
    assert 'class="place pr"' not in html
    assert "Matched one" in html


def test_github_failure_keeps_page(project, monkeypatch, serve):
    monkeypatch.setattr(server_module, "load_topology", lambda root, stack: None)
    html = get_page(serve(project))
    assert "Couldn't reach GitHub" in html
    assert "Matched one" in html


def test_query_string_is_ignored(server):
    url = f"http://127.0.0.1:{server.server_port}/?x=1"
    with urllib.request.urlopen(url) as response:
        assert response.status == 200


def test_other_paths_are_404(server):
    url = f"http://127.0.0.1:{server.server_port}/file/x"
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(url)
    assert error.value.code == 404


def run_main(*args):
    return subprocess.run(
        [sys.executable, "-c", "from bmad_dash.server import main; main()", *args],
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_root_without_bmad_output_exits_with_message(tmp_path):
    result = run_main("--project-root", str(tmp_path))
    assert result.returncode != 0
    assert "_bmad-output" in result.stderr


def test_root_not_a_git_repo_exits_with_message(tmp_path):
    (tmp_path / "_bmad-output").mkdir()
    result = run_main("--project-root", str(tmp_path))
    assert result.returncode != 0
    assert "not a git repository" in result.stderr


@pytest.fixture
def files_server(files_project, serve):
    return serve(files_project)


def raw_get(server, path, host=None):
    """GET `path` exactly as given (no client-side normalising)."""
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
    conn.request("GET", path, headers={"Host": host} if host else {})
    response = conn.getresponse()
    body = response.read()
    conn.close()
    return response, body


def test_file_markdown_is_rendered(files_server):
    response, body = raw_get(files_server, "/file/project/initiative-x/spec-x/spec-x.md?x=1")
    assert response.status == 200
    assert response.getheader("Content-Type").startswith("text/html")
    assert b"<table>" in body
    assert b'<pre class="frontmatter">' in body
    assert b"Initiatives</a>" not in body  # no back link


def test_file_html_is_served_as_is(files_server, files_project):
    response, body = raw_get(files_server, "/file/project/initiative-x/forge-x/forge-report.html")
    assert response.status == 200
    assert response.getheader("Content-Type").startswith("text/html")
    expected = files_project / "_bmad-output/initiative-x/forge-x/forge-report.html"
    assert body == expected.read_bytes()


def test_file_encoded_name_is_decoded(files_server):
    response, body = raw_get(files_server, "/file/project/initiative-x/with%20space.md")
    assert response.status == 200
    assert b'<h1 id="spaced">Spaced</h1>' in body


@pytest.mark.parametrize("path", [
    "/file/project/../README.md",
    "/file/project/a/%2e%2e/%2e%2e/README.md",
    "/file/project/initiative-x/link-out.md",
    "/file/project/initiative-x/missing.md",
    "/file/project/initiative-x/config.toml",
    "/file/project/initiative-x",
    "/file/project/initiative-x/a%00b.md",
    "/file/nope/initiative-x/spec-x/spec-x.md",  # unknown project
    "/file/initiative-x/spec-x/spec-x.md",  # old link: no project segment
    "/file/project",
    "/file/project/",
])
def test_file_not_served_is_404_page(files_server, path):
    # Outside paths get the same 404 as missing ones.
    response, body = raw_get(files_server, path)
    assert response.status == 404
    assert response.getheader("Content-Type").startswith("text/html")
    assert b"Not found" in body
    assert b"Initiatives</a>" not in body  # no back link


def test_file_tickets_toml_is_shown_as_entry_blocks(files_server):
    response, body = raw_get(files_server, "/file/project/initiative-x/epic-a/tickets.toml")
    assert response.status == 200
    assert response.getheader("Content-Type").startswith("text/html")
    assert b'<pre class="entry" id="3">' in body
    assert b"&lt;b&gt;&amp;&lt;/b&gt;" in body
    assert b"pre.entry:target" in body


def test_file_other_toml_names_stay_404(files_server, files_project):
    out = files_project / "_bmad-output" / "initiative-x"
    (out / "alias.toml").symlink_to(out / "epic-a" / "tickets.toml")
    (out / "tickets.toml").symlink_to(out / "spec-x" / "spec-x.md")
    for path in ["alias.toml", "tickets.toml", "config.toml"]:
        response, _ = raw_get(files_server, f"/file/project/initiative-x/{path}")
        assert response.status == 404, path


@pytest.mark.parametrize("path", [
    "/file/project/initiative-x/spec-x/spec-x.md",
    "/file/project/initiative-x/forge-x/forge-report.html",
    "/file/project/initiative-x/epic-a/tickets.toml",
    "/file/project/initiative-x/spec-x/spec-x.md?frame=1",
    "/file/project/initiative-x/forge-x/forge-report.html?frame=1",
])
def test_served_files_are_sandboxed(files_server, path):
    # An opaque origin means a served file can't pass /vscode's Origin check.
    # Walkthrough pages are the one exception: see test_open_file.py.
    response, _ = raw_get(files_server, path)
    assert response.getheader("Content-Security-Policy") == "sandbox allow-scripts"


def test_dashboard_is_not_sandboxed(files_server):
    response, _ = raw_get(files_server, "/")
    assert response.getheader("Content-Security-Policy") is None


@pytest.mark.parametrize("path", ["/", "/file/project/initiative-x/spec-x/spec-x.md"])
def test_get_with_foreign_host_is_403(files_server, path):
    # DNS rebinding: the browser sends the attacker's host name.
    response, _ = raw_get(files_server, path, host="evil.example:80")
    assert response.status == 403


def test_short_body_times_out_and_server_keeps_serving(project, monkeypatch, serve):
    # A POST that sends less body than its Content-Length must not hold its
    # thread forever: the socket timeout closes it, and no command runs.
    monkeypatch.setattr(server_module, "REQUEST_TIMEOUT", 0.5)
    launched = []
    server = serve(project, launch=launched.append)
    port = server.server_port
    stalled = socket.create_connection(("127.0.0.1", port))
    stalled.settimeout(5)
    stalled.sendall(
        b"POST /vscode HTTP/1.1\r\n"
        + f"Host: 127.0.0.1:{port}\r\n".encode()
        + f"Origin: http://127.0.0.1:{port}\r\n".encode()
        + b"Content-Type: application/x-www-form-urlencoded\r\n"
        + b"Content-Length: 100\r\n\r\n"
        + b"id=abc&slu"  # 10 of the 100 bytes, then nothing
    )
    started = time.monotonic()

    # Another request is answered while the first one is stalled.
    response, _ = raw_get(server, "/no-such-page")
    assert response.status == 404

    # The stalled connection is closed by the server, with no response.
    assert stalled.recv(1024) == b""
    assert time.monotonic() - started >= 0.4
    stalled.close()
    assert launched == []


def test_slow_trickle_is_cut_at_the_deadline(project, monkeypatch, serve):
    # One byte at a time, each well under the socket timeout: the whole-body
    # deadline still closes the connection, and no command runs.
    monkeypatch.setattr(server_module, "REQUEST_TIMEOUT", 5)
    monkeypatch.setattr(server_module, "REQUEST_DEADLINE", 0.6)
    launched = []
    server = serve(project, launch=launched.append)
    port = server.server_port
    slow = socket.create_connection(("127.0.0.1", port))
    slow.settimeout(5)
    slow.sendall(
        b"POST /vscode HTTP/1.1\r\n"
        + f"Host: 127.0.0.1:{port}\r\n".encode()
        + f"Origin: http://127.0.0.1:{port}\r\n".encode()
        + b"Content-Type: application/x-www-form-urlencoded\r\n"
        + b"Content-Length: 100\r\n\r\n"
    )
    started = time.monotonic()
    closed = False
    for _ in range(100):
        try:
            slow.sendall(b"x")
        except OSError:  # server already closed it
            closed = True
            break
        time.sleep(0.1)
        if time.monotonic() - started > 2:
            break
    if not closed:
        try:
            closed = slow.recv(1024) == b""
        except ConnectionResetError:
            closed = True
    elapsed = time.monotonic() - started
    slow.close()
    assert closed
    assert elapsed < 3  # well before 100 bytes at 0.1 s each
    assert launched == []

    # The server keeps serving.
    response, _ = raw_get(server, "/no-such-page")
    assert response.status == 404


def test_validate_root_accepts_a_repo(project):
    assert server_module.validate_root(project) is None


def test_body_cut_short_by_client_is_400(project, serve):
    # The client sends less than Content-Length, then closes its side:
    # not a timeout, so it gets the usual 400 and nothing runs.
    launched = []
    server = serve(project, launch=launched.append)
    port = server.server_port
    conn = socket.create_connection(("127.0.0.1", port))
    conn.settimeout(5)
    conn.sendall(
        b"POST /vscode HTTP/1.1\r\n"
        + f"Host: 127.0.0.1:{port}\r\n".encode()
        + f"Origin: http://127.0.0.1:{port}\r\n".encode()
        + b"Content-Type: application/x-www-form-urlencoded\r\n"
        + b"Content-Length: 100\r\n\r\n"
        + b"id=abc"
    )
    conn.shutdown(socket.SHUT_WR)
    reply = conn.recv(1024)
    conn.close()
    assert reply.startswith(b"HTTP/1.0 400") or reply.startswith(b"HTTP/1.1 400")
    assert launched == []


# --- last commit from the Graphite stack ---


def walkthrough_row(html: str) -> str:
    return next(r for r in html.split('<div class="t-row') if "walkthrough-x" in r)


def fake_gt(monkeypatch, calls, gt):
    """Real git (no GitHub remote); `gt` is gt's stdout, or an exception to raise."""
    real_run = subprocess.run

    def run(args, **kwargs):
        if args[0] == "gt":
            calls.append(args)
            if isinstance(gt, BaseException):
                raise gt
            return subprocess.CompletedProcess(args, 0, gt, "")
        if args[0] == "gh":
            raise AssertionError("gh must not run without a GitHub remote")
        return real_run(args, **kwargs)

    monkeypatch.setattr(commands_module, "run_process", run)


def test_stack_newest_commit_reaches_last_activity_and_walkthrough(
    stacked_project, monkeypatch, serve
):
    track_with_graphite(stacked_project)
    add_walkthrough(stacked_project, STACK_TIMES["feature-a"] + timedelta(hours=1))
    calls = []
    fake_gt(monkeypatch, calls, GT_STATE)  # also: no GitHub remote, stack still used
    html = get_page(serve(stacked_project))
    assert "c work" in html and "feature-a work" not in html
    assert "stale-tag" in walkthrough_row(html)
    assert calls == [["gt", "state"]]  # once per request
    assert 'class="place pr"' not in html and "Couldn't reach GitHub" not in html


def test_gt_state_runs_once_with_github(stacked_project, monkeypatch, serve):
    track_with_graphite(stacked_project)
    calls = []
    fake_github(monkeypatch, calls, lambda args: (0, "[]"))
    html = get_page(serve(stacked_project))
    assert "c work" in html
    assert [args for args in calls if args[0] == "gt"] == [["gt", "state"]]


def test_gt_missing_uses_the_worktree_head(stacked_project, monkeypatch, serve):
    track_with_graphite(stacked_project)
    add_walkthrough(stacked_project, STACK_TIMES["feature-a"] + timedelta(hours=1))
    fake_gt(monkeypatch, [], FileNotFoundError("gt"))
    html = get_page(serve(stacked_project))
    assert "feature-a work" in html and "c work" not in html
    assert "stale-tag" not in walkthrough_row(html)


# --- local preview ---

def fetch_page(server) -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/") as response:
        return response.read().decode()


def test_local_slot_links_to_running_worktree_server(project, serve, tmp_path):
    # Any HTTP server will do as the worktree's local server: another dashboard.
    local = serve(project)
    worktree = project.parent / "worktrees" / "wt-a"
    (worktree / ".env").write_text(f"ADMIN_SERVER_PORT={local.server_port}  # local\n")
    config = tmp_path / "projects.toml"
    config.write_text('[project]\nport_key = "ADMIN_SERVER_PORT"\n')
    html = fetch_page(serve(project, config_path=config))
    href = f'href="http://localhost:{local.server_port}/"'
    assert html.count('class="place local"') == 1  # only wt-a's row has a port
    assert f'<a class="place local" {href} target="_blank"' in html


def test_local_slot_muted_when_nothing_answers(project, serve, tmp_path):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    config = tmp_path / "projects.toml"
    config.write_text(f"[project]\ndefault_port = {port}\n")
    html = fetch_page(serve(project, config_path=config))
    assert f"localhost:{port} isn&#x27;t answering" in html
    assert 'class="place local"' not in html


def test_no_config_leaves_local_slots_empty(server):
    html = fetch_page(server)
    assert "place local" not in html


# --- staging preview ---

STAGING_CONFIG = '[project]\nstaging_rule = "owid-lxc"\n'


def closed_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def staging_page(project, serve, tmp_path, monkeypatch, port, state="OPEN",
                 draft=False, config=STAGING_CONFIG):
    """The page with feature-a's PR in `state`, and every staging host sent
    to 127.0.0.1:`port` instead of the network."""
    pr = pr_json(7, "feature-a", state=state, draft=draft)
    fake_github(monkeypatch, [], lambda args: (0, json.dumps([pr])))
    monkeypatch.setattr(
        server_module.previews, "staging_url", lambda host: f"http://127.0.0.1:{port}/"
    )
    path = tmp_path / "projects.toml"
    path.write_text(config)
    return fetch_page(serve(project, config_path=path))


def test_staging_slot_links_when_host_answers(project, serve, tmp_path, monkeypatch):
    up = serve(project)  # any HTTP server will do as the staging server
    html = staging_page(project, serve, tmp_path, monkeypatch, up.server_port, draft=True)
    assert html.count('class="place staging"') == 1
    assert "Open staging staging-site-feature-a (opens in new tab)" in html


def test_staging_slot_muted_when_host_is_down(project, serve, tmp_path, monkeypatch):
    html = staging_page(project, serve, tmp_path, monkeypatch, closed_port())
    assert 'class="place staging is-down"' in html
    assert "staging-site-feature-a isn&#x27;t answering (Tailscale off" in html


def test_merged_pr_leaves_staging_slot_empty(project, serve, tmp_path, monkeypatch):
    up = serve(project)
    html = staging_page(project, serve, tmp_path, monkeypatch, up.server_port, state="MERGED")
    assert 'href="https://github.com/o/r/pull/7"' in html  # the PR icon still shows
    assert "place staging" not in html


def test_no_staging_rule_leaves_staging_slot_empty(project, serve, tmp_path, monkeypatch):
    up = serve(project)
    html = staging_page(project, serve, tmp_path, monkeypatch, up.server_port,
                        config="[project]\ndefault_port = 3030\n")
    assert 'href="https://github.com/o/r/pull/7"' in html
    assert "place staging" not in html


def test_closed_pr_leaves_staging_slot_empty(project, serve, tmp_path, monkeypatch):
    up = serve(project)
    html = staging_page(project, serve, tmp_path, monkeypatch, up.server_port, state="CLOSED")
    assert 'href="https://github.com/o/r/pull/7"' in html
    assert "place staging" not in html


def test_no_pr_leaves_staging_slot_empty(project, serve, tmp_path, monkeypatch):
    fake_github(monkeypatch, [], lambda args: (0, "[]"))
    path = tmp_path / "projects.toml"
    path.write_text(STAGING_CONFIG)
    html = fetch_page(serve(project, config_path=path))
    assert "place staging" not in html


def test_no_config_leaves_staging_slots_empty(project, serve, monkeypatch):
    pr = pr_json(7, "feature-a")
    fake_github(monkeypatch, [], lambda args: (0, json.dumps([pr])))
    html = fetch_page(serve(project))
    assert 'href="https://github.com/o/r/pull/7"' in html
    assert "place staging" not in html


def test_closed_draft_pr_leaves_staging_slot_empty(project, serve, tmp_path, monkeypatch):
    up = serve(project)
    html = staging_page(project, serve, tmp_path, monkeypatch, up.server_port,
                        state="CLOSED", draft=True)
    assert 'href="https://github.com/o/r/pull/7"' in html
    assert "place staging" not in html


def test_local_down_and_staging_up_in_one_row(project, serve, tmp_path, monkeypatch):
    up = serve(project)
    port = closed_port()
    config = f'[project]\ndefault_port = {port}\nstaging_rule = "owid-lxc"\n'
    html = staging_page(project, serve, tmp_path, monkeypatch, up.server_port, config=config)
    # feature-a's row probes both URLs in one batch; each keeps its own result.
    row = next(r for r in html.split('role="row"') if "staging-site-feature-a" in r)
    assert f"localhost:{port} isn&#x27;t answering" in row
    assert 'class="place local"' not in row
    assert '<a class="place staging"' in row


def test_resume_offers_to_start_both_down_previews(project, serve, tmp_path, monkeypatch):
    port = closed_port()
    config = (f'[project]\ndefault_port = {port}\nlocal_start = "make up.worktree"\n'
              'staging_rule = "owid-lxc"\nstaging_start = "open -a Tailscale"\n')
    html = staging_page(project, serve, tmp_path, monkeypatch, closed_port(), config=config)
    row = next(r for r in html.split('role="row"') if "staging-site-feature-a" in r)
    href = re.search(r'<a class="btn btn-1 chip" href="([^"]+)"', row).group(1)
    prompt = unquote(href.replace("&amp;", "&").split("q=", 1)[1])
    assert prompt.endswith(
        f"\nlocalhost:{port} isn't answering: offer to start it with `make up.worktree`"
        "\nstaging-site-feature-a isn't answering: offer to start it with `open -a Tailscale`")


def test_local_starts_follow_each_project_entry():
    initiatives = [Initiative("a", "A", project="with"), Initiative("b", "B", project="without"),
                   Initiative("c", "C", project="unlisted")]
    config = {"with": PreviewConfig(local_start="make up"), "without": PreviewConfig()}
    assert server_module.local_starts(initiatives, config) == {"with/a": "make up"}


def test_worktree_without_env_port_offers_to_start(project, serve, tmp_path, monkeypatch):
    config = ('[project]\nport_key = "ADMIN_SERVER_PORT"\ndefault_port = 3030\n'
              'local_start = "make up.worktree"\n')
    html = staging_page(project, serve, tmp_path, monkeypatch, closed_port(), config=config)
    row = next(r for r in html.split('role="row"') if "initiative-matched" in r)
    assert "the local server isn&#x27;t answering: resume offers to start it" in row
    href = re.search(r'<a class="btn btn-1 chip" href="([^"]+)"', row).group(1)
    prompt = unquote(href.replace("&amp;", "&").split("q=", 1)[1])
    assert prompt.endswith(
        "\nthe local server isn't answering: offer to start it with `make up.worktree`")
    # The main checkout keeps `default_port`.
    main_row = next(r for r in html.split('role="row"') if "initiative-shared" in r)
    assert "localhost:3030" in main_row


def test_main_checkout_without_any_port_keeps_an_empty_slot(tmp_path):
    # Only a linked worktree (its `.git` is a file) shows down without a port.
    main, linked = tmp_path / "main", tmp_path / "linked"
    (main / ".git").mkdir(parents=True)
    linked.mkdir()
    (linked / ".git").write_text("gitdir: /elsewhere\n")
    initiatives = [Initiative("a", "A", [Worktree(main, "main")], project="p"),
                   Initiative("b", "B", [Worktree(linked, "b")], project="p")]
    config = {"p": PreviewConfig(port_key="ADMIN_SERVER_PORT", local_start="make up")}
    local, _ = server_module.place_previews(initiatives, config, {})
    assert local == {"p/b": LocalPreview(None, False, "make up")}
