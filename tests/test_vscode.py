import http.client
import subprocess
from urllib.parse import urlencode

import pytest

from bmad_dash.initiatives import list_worktrees, worktree_id
from bmad_dash.server import default_launch


@pytest.fixture
def vscode(project, serve):
    """A live server with a fake launcher. Returns (server, launched paths)."""
    launched = []
    return serve(project, launch=launched.append), launched


def send(server, method="POST", body=None, headers=None, path="/vscode"):
    """Send a raw request; returns (status, headers)."""
    port = server.server_port
    base = {"Host": f"127.0.0.1:{port}", "Origin": f"http://127.0.0.1:{port}"}
    if body is not None:
        base["Content-Type"] = "application/x-www-form-urlencoded"
        base["Content-Length"] = str(len(body))
    base.update(headers or {})
    conn = http.client.HTTPConnection("127.0.0.1", port)
    conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    for name, value in base.items():
        if value is not None:
            conn.putheader(name, value)
    conn.endheaders(body.encode() if body is not None else None)
    response = conn.getresponse()
    response.body = response.read()
    conn.close()
    return response.status, response


def wt_a(project):
    return next(w.path for w in list_worktrees(project) if w.path.name == "wt-a")


def test_valid_open_launches_listed_path(vscode, project):
    server, launched = vscode
    status, response = send(server, body=urlencode({"id": worktree_id(wt_a(project))}))
    assert status == 303
    assert response.getheader("Location") == "/"
    assert launched == [wt_a(project)]  # called once


def test_localhost_host_and_origin_are_accepted(vscode, project):
    server, launched = vscode
    port = server.server_port
    headers = {"Host": f"localhost:{port}", "Origin": f"http://localhost:{port}"}
    status, _ = send(server, body=urlencode({"id": worktree_id(project)}), headers=headers)
    assert status == 303
    assert launched == [project]


def test_get_is_405(vscode):
    server, launched = vscode
    status, response = send(server, method="GET")
    assert status == 405
    assert response.getheader("Allow") == "POST"
    assert launched == []


@pytest.mark.parametrize("origin", ["https://evil.example", "http://127.0.0.1:1", None, "null"])
def test_foreign_or_missing_origin_is_403(vscode, project, origin):
    server, launched = vscode
    body = urlencode({"id": worktree_id(project)})
    status, _ = send(server, body=body, headers={"Origin": origin})
    assert status == 403
    assert launched == []


def test_bad_host_is_403(vscode, project):
    server, launched = vscode
    body = urlencode({"id": worktree_id(project)})
    status, _ = send(server, body=body, headers={"Host": "evil.example:8765"})
    assert status == 403
    assert launched == []


@pytest.mark.parametrize("fields", [
    {"id": "000000000000", "slug": "initiative-matched"},
    {"id": "/etc", "slug": "initiative-matched"},
    {"path": "/etc", "slug": "initiative-matched"},
    {"slug": "initiative-matched"},
])
def test_unknown_id_or_path_redirects_with_flag(vscode, fields):
    server, launched = vscode
    status, response = send(server, body=urlencode(fields))
    assert status == 303
    assert response.getheader("Location") == "/?vscode_failed=initiative-matched"
    assert launched == []


def test_refused_without_slug_redirects_with_empty_flag(vscode):
    server, launched = vscode
    status, response = send(server, body=urlencode({"id": "000000000000"}))
    assert status == 303
    assert response.getheader("Location") == "/?vscode_failed="
    assert launched == []


def test_slug_is_quoted_in_redirect(vscode):
    server, launched = vscode
    body = urlencode({"id": "nope", "slug": "a b&c=/d"})
    status, response = send(server, body=body)
    assert status == 303
    assert response.getheader("Location") == "/?vscode_failed=a%20b%26c%3D%2Fd"
    assert launched == []


def test_path_field_is_ignored_even_with_valid_id_name(vscode, project):
    server, launched = vscode
    body = urlencode({"id": str(project), "path": str(project), "slug": "x"})
    status, response = send(server, body=body)
    assert status == 303
    assert response.getheader("Location") == "/?vscode_failed=x"
    assert launched == []


def test_slug_field_never_picks_what_opens(vscode, project):
    # The slug only labels the failure; the id alone picks the worktree.
    server, launched = vscode
    body = urlencode({"id": worktree_id(wt_a(project)), "slug": "initiative-shared"})
    status, response = send(server, body=body)
    assert status == 303
    assert response.getheader("Location") == "/"
    assert launched == [wt_a(project)]


def test_body_too_big_is_400(vscode, project):
    server, launched = vscode
    body = urlencode({"id": worktree_id(project), "pad": "x" * 1100})
    status, _ = send(server, body=body)
    assert status == 400
    assert launched == []


def test_negative_content_length_is_400(vscode):
    # rfile.read(-1) would block until the client hangs up.
    server, launched = vscode
    status, _ = send(server, headers={"Content-Length": "-1"})
    assert status == 400
    assert launched == []


def test_missing_content_length_is_400(vscode):
    server, launched = vscode
    status, _ = send(server)
    assert status == 400
    assert launched == []


def test_post_elsewhere_is_404(vscode, project):
    server, launched = vscode
    status, _ = send(server, body=urlencode({"id": worktree_id(project)}), path="/")
    assert status == 404
    assert launched == []


def test_code_missing_redirects_with_flag(project, serve):
    calls = []

    def broken_launch(path):
        calls.append(path)
        raise FileNotFoundError("code")

    server = serve(project, launch=broken_launch)
    body = urlencode({"id": worktree_id(project), "slug": "initiative-shared"})
    status, response = send(server, body=body)
    assert status == 303
    assert response.getheader("Location") == "/?vscode_failed=initiative-shared"
    assert calls == [project]


def test_default_launch_runs_code_without_shell_or_wait(monkeypatch, tmp_path):
    calls = []

    class FakePopen:
        def __init__(self, args, **kwargs):
            calls.append((args, kwargs))

        def wait(self, *a, **k):
            raise AssertionError("must not wait")

        def communicate(self, *a, **k):
            raise AssertionError("must not wait")

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    default_launch(tmp_path)
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == ["code", "-n", str(tmp_path)]
    assert not kwargs.get("shell", False)
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL


def get_page(server, path):
    port = server.server_port
    conn = http.client.HTTPConnection("127.0.0.1", port)
    conn.request("GET", path, headers={"Host": f"127.0.0.1:{port}"})
    response = conn.getresponse()
    body = response.read().decode()
    conn.close()
    return response.status, body


def test_flag_shows_message_on_that_row_only(vscode):
    server, launched = vscode
    status, body = get_page(server, "/?vscode_failed=initiative-matched")
    assert status == 200
    assert body.count("Couldn't open VS Code") == 1
    # The message sits in the initiative-matched row, before the next row starts.
    rows = body.split('role="row">')
    matched = [r for r in rows if '<span class="slug">initiative-matched</span>' in r]
    assert len(matched) == 1
    assert "Couldn't open VS Code" in matched[0]
    assert launched == []


def test_flag_for_unknown_slug_renders_normally(vscode):
    server, launched = vscode
    status, body = get_page(server, "/?vscode_failed=nope")
    assert status == 200
    assert "Couldn't open VS Code" not in body
    assert launched == []
