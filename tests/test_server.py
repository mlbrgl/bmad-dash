import http.client
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

import bmad_dash.server as server_module
from bmad_dash.server import make_server


@pytest.fixture
def server(project):
    server = make_server(project, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


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
    assert html.count('role="row"') == 1 + 5


def test_no_github_remote_shows_no_github_line(server):
    url = f"http://127.0.0.1:{server.server_port}/"
    with urllib.request.urlopen(url) as response:
        html = response.read().decode()
    assert "Couldn't reach GitHub" not in html
    assert 'class="btn btn-2 pr"' not in html


def test_fetched_prs_reach_the_page(project, monkeypatch):
    from bmad_dash import server as server_module
    from bmad_dash.prs import PullRequest

    calls = []

    def fake_fetch(root):
        calls.append(root)
        return [PullRequest(7, "feature-a", "main", "open", "https://github.com/o/r/pull/7")]

    monkeypatch.setattr(server_module, "fetch_prs", fake_fetch)
    srv = make_server(project, 0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{srv.server_port}/"
        with urllib.request.urlopen(url) as response:
            html = response.read().decode()
    finally:
        srv.shutdown()
        srv.server_close()
    assert calls == [project]
    assert 'href="https://github.com/o/r/pull/7"' in html


def test_github_failure_keeps_page(project, monkeypatch):
    from bmad_dash import server as server_module

    monkeypatch.setattr(server_module, "fetch_prs", lambda root: None)
    srv = make_server(project, 0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{srv.server_port}/"
        with urllib.request.urlopen(url) as response:
            html = response.read().decode()
    finally:
        srv.shutdown()
        srv.server_close()
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
def files_server(files_project):
    server = make_server(files_project, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def raw_get(server, path, host=None):
    """GET `path` exactly as given (no client-side normalising)."""
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
    conn.request("GET", path, headers={"Host": host} if host else {})
    response = conn.getresponse()
    body = response.read()
    conn.close()
    return response, body


def test_file_markdown_is_rendered(files_server):
    response, body = raw_get(files_server, "/file/initiative-x/spec-x/spec-x.md?x=1")
    assert response.status == 200
    assert response.getheader("Content-Type").startswith("text/html")
    assert b"<table>" in body
    assert b'<pre class="frontmatter">' in body
    assert b'<a class="back" href="/">&larr; Initiatives</a>' in body


def test_file_html_is_served_as_is(files_server, files_project):
    response, body = raw_get(files_server, "/file/initiative-x/forge-x/forge-report.html")
    assert response.status == 200
    assert response.getheader("Content-Type").startswith("text/html")
    expected = files_project / "_bmad-output/initiative-x/forge-x/forge-report.html"
    assert body == expected.read_bytes()


def test_file_encoded_name_is_decoded(files_server):
    response, body = raw_get(files_server, "/file/initiative-x/with%20space.md")
    assert response.status == 200
    assert b"<h1>Spaced</h1>" in body


@pytest.mark.parametrize("path", [
    "/file/../README.md",
    "/file/a/%2e%2e/%2e%2e/README.md",
    "/file/initiative-x/link-out.md",
    "/file/initiative-x/missing.md",
    "/file/initiative-x/config.toml",
    "/file/initiative-x",
    "/file/initiative-x/a%00b.md",
])
def test_file_not_served_is_404_page_with_back_link(files_server, path):
    # Outside paths get the same 404 as missing ones.
    response, body = raw_get(files_server, path)
    assert response.status == 404
    assert response.getheader("Content-Type").startswith("text/html")
    assert b"Not found" in body
    assert b'<a class="back" href="/">&larr; Initiatives</a>' in body


@pytest.mark.parametrize("path", [
    "/file/initiative-x/spec-x/spec-x.md",
    "/file/initiative-x/forge-x/forge-report.html",
    "/file/initiative-x/spec-x/spec-x.md?frame=1",
    "/file/initiative-x/forge-x/forge-report.html?frame=1",
])
def test_served_files_are_sandboxed(files_server, path):
    # An opaque origin means a served file can't pass /vscode's Origin check.
    # Walkthrough pages are the one exception: see test_open_file.py.
    response, _ = raw_get(files_server, path)
    assert response.getheader("Content-Security-Policy") == "sandbox allow-scripts"


def test_dashboard_is_not_sandboxed(files_server):
    response, _ = raw_get(files_server, "/")
    assert response.getheader("Content-Security-Policy") is None


@pytest.mark.parametrize("path", ["/", "/file/initiative-x/spec-x/spec-x.md"])
def test_get_with_foreign_host_is_403(files_server, path):
    # DNS rebinding: the browser sends the attacker's host name.
    response, _ = raw_get(files_server, path, host="evil.example:80")
    assert response.status == 403


def test_short_body_times_out_and_server_keeps_serving(project, monkeypatch):
    # A POST that sends less body than its Content-Length must not hold its
    # thread forever: the socket timeout closes it, and no command runs.
    monkeypatch.setattr(server_module, "REQUEST_TIMEOUT", 0.5)
    launched = []
    server = make_server(project, 0, launch=launched.append)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port
    try:
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
    finally:
        server.shutdown()
        server.server_close()
