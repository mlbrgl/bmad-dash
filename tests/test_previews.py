import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from bmad_dash.previews import (
    PreviewConfig,
    env_port,
    load_config,
    local_url,
    probe,
    probe_batch,
    staging_host,
    staging_url,
)

GRAPHER = PreviewConfig(port_key="ADMIN_SERVER_PORT", default_port=3030)


# --- config ---

def test_missing_config_is_empty(tmp_path):
    assert load_config(tmp_path / "projects.toml") == {}


def test_bad_toml_is_empty_with_one_stderr_line(tmp_path, capsys):
    path = tmp_path / "projects.toml"
    path.write_text("[owid-grapher\nport_key = = 1\n")
    assert load_config(path) == {}
    assert len(capsys.readouterr().err.strip().splitlines()) == 1


def test_example_file_loads():
    example = Path(__file__).parents[1] / "projects.toml.example"
    config = load_config(example)
    assert config["owid-grapher"] == PreviewConfig(
        port_key="ADMIN_SERVER_PORT",
        default_port=3030,
        local_start="make up.worktree",
        staging_start="open -a Tailscale",
        staging_rule="owid-lxc",
    )
    assert config["etl"] == PreviewConfig()


def test_wrong_types_drop_only_that_entry(tmp_path, capsys):
    path = tmp_path / "projects.toml"
    path.write_text(
        '[a]\ndefault_port = "3030"\n'
        "[b]\ndefault_port = true\n"
        "[c]\ndefault_port = 70000\n"
        '[ok]\nport_key = "P"\n'
    )
    config = load_config(path)
    assert config == {"ok": PreviewConfig(port_key="P")}
    assert len(capsys.readouterr().err.strip().splitlines()) == 3


def test_non_table_entry_is_dropped(tmp_path, capsys):
    path = tmp_path / "projects.toml"
    path.write_text('stray = "x"\n[ok]\n')
    assert load_config(path) == {"ok": PreviewConfig()}
    assert "stray" in capsys.readouterr().err


# --- .env ---

def write_env(tmp_path: Path, text: str) -> Path:
    (tmp_path / ".env").write_text(text)
    return tmp_path


@pytest.mark.parametrize("line", [
    "ADMIN_SERVER_PORT=3827",
    "export ADMIN_SERVER_PORT=3827",
    "ADMIN_SERVER_PORT=3827  # optional; for the admin",
    'ADMIN_SERVER_PORT="3827"',
    "ADMIN_SERVER_PORT='3827' # quoted",
    " ADMIN_SERVER_PORT = 3827 ",
])
def test_env_port_is_read(tmp_path, line):
    worktree = write_env(tmp_path, f"OTHER=1\n{line}\n")
    assert env_port(worktree, "ADMIN_SERVER_PORT") == 3827
    assert local_url(worktree, GRAPHER) == "http://localhost:3827/"


@pytest.mark.parametrize("text", [
    None,  # no .env
    "OTHER=4000\n",  # key absent
    "# ADMIN_SERVER_PORT=4000\n",  # commented out
    "ADMIN_SERVER_PORT=abc\n",  # garbage
    "ADMIN_SERVER_PORT=3²\n",  # a digit to isdigit, not to int
    "ADMIN_SERVER_PORT=0\n",
    "ADMIN_SERVER_PORT=70000\n",
    "ADMIN_SERVER_PORT=\n",
])
def test_no_usable_env_port_falls_back_to_default(tmp_path, text):
    if text is not None:
        write_env(tmp_path, text)
    assert env_port(tmp_path, "ADMIN_SERVER_PORT") is None
    assert local_url(tmp_path, GRAPHER) == "http://localhost:3030/"


def test_entry_without_port_has_no_url(tmp_path):
    write_env(tmp_path, "ADMIN_SERVER_PORT=3827\n")
    assert local_url(tmp_path, PreviewConfig()) is None
    # A key but no default: the .env port or nothing.
    keyed = PreviewConfig(port_key="ADMIN_SERVER_PORT")
    assert local_url(tmp_path, keyed) == "http://localhost:3827/"
    assert local_url(tmp_path / "nowhere", keyed) is None


# --- probes ---

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/")
        else:
            self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def http_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def closed_url():
    """A port nothing listens on."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return f"http://127.0.0.1:{port}/"


@pytest.fixture
def hanging_urls():
    """`hanging_urls(n)`: n ports that accept connections and never answer."""
    socks = []

    def make(n: int) -> list[str]:
        urls = []
        for _ in range(n):
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            sock.listen(8)  # the kernel accepts; nobody reads or writes
            socks.append(sock)
            urls.append(f"http://127.0.0.1:{sock.getsockname()[1]}/")
        return urls

    yield make
    for sock in socks:
        sock.close()


def test_answering_server_is_up(http_url):
    assert probe(http_url + "/") is True


def test_redirect_is_up_and_not_followed(http_url):
    # The redirect points at port 1 (closed): following it would read as down.
    assert probe(http_url + "/redirect") is True


def test_closed_port_is_down(closed_url):
    assert probe(closed_url) is False


def test_unknown_host_is_down():
    assert probe("http://no-such-host.invalid/", timeout=1) is False


def test_hanging_server_is_down(hanging_urls):
    [url] = hanging_urls(1)
    assert probe(url, timeout=0.3) is False


def test_batch_returns_each_rows_urls(http_url, closed_url):
    results = probe_batch({"p/a": [http_url + "/"], "p/b": [closed_url, http_url + "/"]})
    assert results == {
        "p/a": {http_url + "/": True},
        "p/b": {closed_url: False, http_url + "/": True},
    }


def test_empty_batch():
    assert probe_batch({}) == {}
    assert probe_batch({"p/a": []}) == {"p/a": {}}


class SlowHandler(Handler):
    def do_GET(self):
        time.sleep(0.5)
        super().do_GET()


def test_batch_runs_probes_in_parallel():
    # Six servers, 0.5 s each: 3 s in sequence, past the 2 s deadline.
    servers = [ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler) for _ in range(6)]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        rows = {f"p/{i}": [f"http://127.0.0.1:{s.server_port}/"] for i, s in enumerate(servers)}
        start = time.monotonic()
        results = probe_batch(rows)
        assert time.monotonic() - start < 2
        assert all(up is True for row in results.values() for up in row.values())
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


def test_hanging_batch_is_bounded(hanging_urls):
    urls = hanging_urls(5)
    rows = {f"p/{index}": [url] for index, url in enumerate(urls)}
    start = time.monotonic()
    results = probe_batch(rows)
    assert time.monotonic() - start < 3
    assert all(up is False for row in results.values() for up in row.values())


# --- staging host ---

@pytest.mark.parametrize("branch, host", [
    ("a-very-long-branch-name-that-goes-on", "staging-site-a-very-long-branch-name-that"),
    ("mlbrgl/fix.chart_title", "staging-site-mlbrgl-fix-chart-title"),
    ("bespoke-viz-search-featured-card-v2", "staging-site-bespoke-viz-search-featured"),
    ("feat/x--", "staging-site-feat-x"),
    ("dependabot/npm/x", None),
    ("dependabot-npm-x", None),
    ("graphite-base-123", None),
    ("feat;rm", None),
])
def test_owid_lxc_staging_host(branch, host):
    assert staging_host("owid-lxc", branch) == host


def test_unknown_staging_rule_gives_no_host():
    assert staging_host("other", "x") is None


def test_staging_url():
    assert staging_url("staging-site-x") == "http://staging-site-x/"


def test_unresolvable_staging_host_is_down():
    assert probe("http://staging-site-x.invalid/", timeout=1) is False
