import subprocess
import sys
import threading
import urllib.error
import urllib.request

import pytest

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
    assert "detached" in html
    assert html.count("<tr>") == 1 + 5


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
