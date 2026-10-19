import http.client
import subprocess
import threading
from urllib.parse import urlencode

import pytest

from bmad_dash.initiatives import list_worktrees, worktree_id
from bmad_dash.server import default_launch, make_server


@pytest.fixture
def switch(project):
    """A live server with a fake launcher. Yields (server, launched paths)."""
    launched = []
    server = make_server(project, 0, launch=launched.append)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, launched
    server.shutdown()
    server.server_close()


def send(server, method="POST", body=None, headers=None, path="/switch"):
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


def test_valid_switch_launches_listed_path(switch, project):
    server, launched = switch
    status, response = send(server, body=urlencode({"id": worktree_id(wt_a(project))}))
    assert status == 303
    assert response.getheader("Location") == "/"
    assert launched == [wt_a(project)]


def test_localhost_host_and_origin_are_accepted(switch, project):
    server, launched = switch
    port = server.server_port
    headers = {"Host": f"localhost:{port}", "Origin": f"http://localhost:{port}"}
    status, _ = send(server, body=urlencode({"id": worktree_id(project)}), headers=headers)
    assert status == 303
    assert launched == [project]


def test_get_is_405(switch):
    server, launched = switch
    status, response = send(server, method="GET")
    assert status == 405
    assert response.getheader("Allow") == "POST"
    assert launched == []


@pytest.mark.parametrize("origin", ["https://evil.example", "http://127.0.0.1:1", None, "null"])
def test_foreign_or_missing_origin_is_403(switch, project, origin):
    server, launched = switch
    body = urlencode({"id": worktree_id(project)})
    status, _ = send(server, body=body, headers={"Origin": origin})
    assert status == 403
    assert launched == []


def test_bad_host_is_403(switch, project):
    server, launched = switch
    body = urlencode({"id": worktree_id(project)})
    status, _ = send(server, body=body, headers={"Host": "evil.example:8765"})
    assert status == 403
    assert launched == []


@pytest.mark.parametrize("fields", [
    {"id": "000000000000"},
    {"id": "/etc"},
    {"path": "/etc"},
    {},
])
def test_unknown_id_or_path_is_404(switch, fields):
    server, launched = switch
    status, _ = send(server, body=urlencode(fields))
    assert status == 404
    assert launched == []


def test_path_field_is_ignored_even_with_valid_id_name(switch, project):
    server, launched = switch
    body = urlencode({"id": str(project), "path": str(project)})
    status, _ = send(server, body=body)
    assert status == 404
    assert launched == []


def test_body_too_big_is_400(switch, project):
    server, launched = switch
    body = urlencode({"id": worktree_id(project), "pad": "x" * 1100})
    status, _ = send(server, body=body)
    assert status == 400
    assert launched == []


def test_negative_content_length_is_400(switch):
    # rfile.read(-1) would block until the client hangs up.
    server, launched = switch
    status, _ = send(server, headers={"Content-Length": "-1"})
    assert status == 400
    assert launched == []


def test_missing_content_length_is_400(switch):
    server, launched = switch
    status, _ = send(server)
    assert status == 400
    assert launched == []


def test_post_elsewhere_is_404(switch, project):
    server, launched = switch
    status, _ = send(server, body=urlencode({"id": worktree_id(project)}), path="/")
    assert status == 404
    assert launched == []


def test_code_missing_is_500(project):
    def broken_launch(path):
        raise FileNotFoundError("code")

    server = make_server(project, 0, launch=broken_launch)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, response = send(server, body=urlencode({"id": worktree_id(project)}))
        assert status == 500
        assert b"Could not start VS Code" in response.body
    finally:
        server.shutdown()
        server.server_close()


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
