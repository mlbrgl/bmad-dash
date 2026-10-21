import subprocess
import threading
from pathlib import Path
from urllib.parse import urlencode

import pytest

from bmad_dash.initiatives import list_worktrees
from bmad_dash.opener import inject_relay, is_walkthrough_page, read_title, wrapper_page
from bmad_dash.server import default_open_file, make_server

from conftest import git
from test_vscode import get_page, send

PAGE = "/file/initiative-matched/walkthrough-x/walkthrough-x.html"
WALKTHROUGH_HTML = (
    "<!doctype html><html><head><title>Walk &amp; <b>talk</b></title></head>"
    "<body><h1>Walk</h1></body></html>\n"
)


def wt_a(project: Path) -> Path:
    return next(w.path for w in list_worktrees(project) if w.path.name == "wt-a")


@pytest.fixture
def walk_project(files_project: Path) -> Path:
    """`files_project` plus walkthroughs and files to open.

    - initiative-matched (claimed by wt-a): walkthrough-x/walkthrough-x.html,
      walkthrough-x/notes.md, walkthrough-x/untitled.html (no <title>),
      misc/page.html (not in a walkthrough folder)
    - initiative-orphan (claimed by none): walkthrough-o/walkthrough-o.html
    - wt-a/src/a.py, wt-a/src/folder/, wt-a/link-out.py -> <root>/secret.md
    - initiative-shared (claimed by the main checkout): walkthrough-s/walkthrough-s.html
    - <root>/src/b.py, and a worktree nested in the main checkout at
      <root>/.claude/worktrees/nest with nest.py
    """
    out = files_project / "_bmad-output"
    walk = out / "initiative-matched" / "walkthrough-x"
    walk.mkdir()
    (walk / "walkthrough-x.html").write_text(WALKTHROUGH_HTML)
    (walk / "untitled.html").write_text("<p>no title</p>")
    (walk / "notes.md").write_text("# Notes\n")
    (out / "initiative-matched" / "misc").mkdir()
    (out / "initiative-matched" / "misc" / "page.html").write_text(WALKTHROUGH_HTML)
    orphan = out / "initiative-orphan" / "walkthrough-o"
    orphan.mkdir()
    (orphan / "walkthrough-o.html").write_text(WALKTHROUGH_HTML)

    wt = wt_a(files_project)
    (wt / "src" / "folder").mkdir(parents=True)
    (wt / "src" / "a.py").write_text("print('a')\n")
    (wt / "link-out.py").symlink_to(files_project / "secret.md")

    shared = out / "initiative-shared" / "walkthrough-s"
    shared.mkdir()
    (shared / "walkthrough-s.html").write_text(WALKTHROUGH_HTML)
    (files_project / "src").mkdir()
    (files_project / "src" / "b.py").write_text("b\n")
    nest = files_project / ".claude" / "worktrees" / "nest"
    git(files_project, "worktree", "add", "--detach", str(nest))
    (nest / "nest.py").write_text("n\n")
    return files_project


@pytest.fixture
def opener(walk_project):
    """A live server with a fake file opener. Yields (server, opened calls)."""
    opened = []
    server = make_server(walk_project, 0, open_file=lambda path, line: opened.append((path, line)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, opened
    server.shutdown()
    server.server_close()


def post_open(server, fields=None, headers=None, wt_file=None):
    """POST /open-file as the wrapper page does (same origin, by default)."""
    form = {"page": PAGE, "path": str(wt_file), "line": "20"}
    form.update(fields or {})
    form = {k: v for k, v in form.items() if v is not None}
    return send(server, body=urlencode(form), headers=headers, path="/open-file")


def get(server, path):
    """GET `path`; returns (status, response with .body as text)."""
    status, response = send(server, method="GET", path=path, headers={"Origin": None})
    response.body = response.body.decode()
    return status, response


def a_py(project: Path) -> Path:
    return wt_a(project) / "src" / "a.py"


# --- wrapper and framed pages ---


def test_walkthrough_is_served_as_wrapper(opener):
    server, _ = opener
    status, response = get(server, PAGE)
    body = response.body
    assert status == 200
    assert response.getheader("Content-Security-Policy") == "frame-ancestors 'self'"
    assert f'<iframe id="bmad-dash-frame" src="{PAGE}?frame=1"' in body
    assert "sandbox" not in body.split("<iframe", 1)[1].split(">", 1)[0]
    assert "<title>Walk &amp; &lt;b&gt;talk&lt;/b&gt;</title>" in body
    assert "bmad-dash-open-snack" in body
    assert "Couldn't open in VS Code" in body
    assert 'event.source !== frame.contentWindow || event.origin !== "null"' in body
    assert "/open-file" in body
    assert "<h1>Walk</h1>" not in body  # the walkthrough itself is only framed


def test_wrapper_title_falls_back_to_file_name(opener):
    server, _ = opener
    _, response = get(server, "/file/initiative-matched/walkthrough-x/untitled.html")
    assert "<title>untitled.html</title>" in response.body


def test_wrapper_src_is_quoted(walk_project, opener):
    server, _ = opener
    walk = walk_project / "_bmad-output" / "initiative-matched" / "walkthrough-x"
    (walk / 'a "b"&<c>.html').write_text("<p>x</p>")
    _, response = get(server, "/file/initiative-matched/walkthrough-x/a%20%22b%22%26%3Cc%3E.html")
    assert 'src="/file/initiative-matched/walkthrough-x/a%20%22b%22%26%3Cc%3E.html?frame=1"' in response.body


def test_framed_walkthrough_has_relay_and_is_sandboxed(opener):
    server, _ = opener
    status, response = get(server, PAGE + "?frame=1")
    body = response.body
    assert status == 200
    assert response.getheader("Content-Security-Policy") == (
        "sandbox allow-scripts; frame-ancestors 'self'"
    )
    assert "<h1>Walk</h1>" in body
    assert "parent.postMessage" in body
    assert "bmad-dash:open-file" in body
    assert "<iframe" not in body
    assert body.index("<script>") < body.rindex("</body>")


@pytest.mark.parametrize("path", [
    "/file/initiative-x/spec-x/spec-x.md",
    "/file/initiative-matched/walkthrough-x/notes.md",
    "/file/initiative-x/forge-x/forge-report.html",
    "/file/initiative-matched/misc/page.html",
])
@pytest.mark.parametrize("query", ["", "?frame=1"])
def test_other_files_are_untouched(opener, path, query):
    server, _ = opener
    status, response = get(server, path + query)
    assert status == 200
    assert response.getheader("Content-Security-Policy") == "sandbox allow-scripts"
    assert "postMessage" not in response.body
    assert "bmad-dash-frame" not in response.body


def test_dashboard_has_no_relay_or_wrapper(opener):
    server, _ = opener
    _, body = get_page(server, "/")
    assert "bmad-dash:open-file" not in body
    assert "bmad-dash-frame" not in body


def test_walkthrough_file_on_disk_is_unchanged(opener, walk_project):
    server, _ = opener
    get(server, PAGE)
    get(server, PAGE + "?frame=1")
    page = walk_project / "_bmad-output" / PAGE.removeprefix("/file/")
    assert page.read_text() == WALKTHROUGH_HTML


# --- /open-file matrix ---


def test_happy_opens_resolved_path_at_line(opener, walk_project):
    server, opened = opener
    status, response = post_open(server, wt_file=a_py(walk_project))
    assert status == 204
    assert response.getheader("Access-Control-Allow-Origin") is None
    assert response.body == b""
    assert opened == [(a_py(walk_project).resolve(), "20")]


def test_no_line_opens_without_line(opener, walk_project):
    server, opened = opener
    status, _ = post_open(server, {"line": ""}, wt_file=a_py(walk_project))
    assert status == 204
    assert opened == [(a_py(walk_project).resolve(), "")]


def test_dotdot_inside_worktree_is_resolved(opener, walk_project):
    server, opened = opener
    raw = str(wt_a(walk_project) / "src" / "folder" / ".." / "a.py")
    status, _ = post_open(server, {"path": raw})
    assert status == 204
    assert opened == [(a_py(walk_project).resolve(), "20")]


def test_localhost_host_and_origin_are_accepted(opener, walk_project):
    server, opened = opener
    port = server.server_port
    headers = {"Host": f"localhost:{port}", "Origin": f"http://localhost:{port}"}
    status, _ = post_open(server, headers=headers, wt_file=a_py(walk_project))
    assert status == 204
    assert len(opened) == 1


def test_get_is_405(opener):
    server, opened = opener
    status, response = send(server, method="GET", path="/open-file")
    assert status == 405
    assert response.getheader("Allow") == "POST"
    assert opened == []


@pytest.mark.parametrize("origin", ["null", "https://evil.example", "http://127.0.0.1:1", None])
def test_foreign_origin_is_403(opener, walk_project, origin):
    server, opened = opener
    status, response = post_open(server, headers={"Origin": origin}, wt_file=a_py(walk_project))
    assert status == 403
    assert response.body == b""
    assert opened == []


def test_foreign_host_is_403(opener, walk_project):
    server, opened = opener
    status, _ = post_open(server, headers={"Host": "evil.example:8765"}, wt_file=a_py(walk_project))
    assert status == 403
    assert opened == []


@pytest.mark.parametrize("raw", [
    "DOTDOT",  # ../ out of the worktree into the main checkout
    "SYMLINK",  # wt-a/link-out.py -> <root>/secret.md
    "MAIN",  # a real file, but in another worktree
    "/etc/hosts",
])
def test_escape_is_404(opener, walk_project, raw):
    server, opened = opener
    wt = wt_a(walk_project)
    raw = {
        "DOTDOT": str(wt / ".." / ".." / "project" / "secret.md"),
        "SYMLINK": str(wt / "link-out.py"),
        "MAIN": str(walk_project / "secret.md"),
    }.get(raw, raw)
    status, _ = post_open(server, {"path": raw})
    assert status == 404
    assert opened == []


@pytest.mark.parametrize("raw", ["MISSING", "FOLDER", "", "src/a.py", "/a\x00b"])
def test_missing_or_folder_is_404(opener, walk_project, raw):
    server, opened = opener
    wt = wt_a(walk_project)
    raw = {"MISSING": str(wt / "nope.py"), "FOLDER": str(wt / "src" / "folder")}.get(raw, raw)
    status, _ = post_open(server, {"path": raw})
    assert status == 404
    assert opened == []


SHARED_PAGE = "/file/initiative-shared/walkthrough-s/walkthrough-s.html"


def test_main_checkout_file_opens(opener, walk_project):
    server, opened = opener
    status, _ = post_open(server, {"page": SHARED_PAGE, "path": str(walk_project / "src" / "b.py")})
    assert status == 204
    assert opened == [((walk_project / "src" / "b.py").resolve(), "20")]


@pytest.mark.parametrize("rel", [".git/config", ".git/HEAD", ".claude/worktrees/nest/nest.py"])
def test_git_folder_and_nested_worktree_are_404(opener, walk_project, rel):
    server, opened = opener
    assert (walk_project / rel).is_file()
    status, _ = post_open(server, {"page": SHARED_PAGE, "path": str(walk_project / rel)})
    assert status == 404
    assert opened == []


def test_no_worktree_is_404(opener, walk_project):
    server, opened = opener
    page = "/file/initiative-orphan/walkthrough-o/walkthrough-o.html"
    status, _ = post_open(server, {"page": page}, wt_file=a_py(walk_project))
    assert status == 404
    assert opened == []


@pytest.mark.parametrize("page", [
    "/file/initiative-x/spec-x/spec-x.md",
    "/file/initiative-matched/walkthrough-x/notes.md",
    "/file/initiative-matched/misc/page.html",
    "/file/initiative-x/forge-x/forge-report.html",
    "/file/initiative-matched/walkthrough-x/nope.html",
    "/file/../secret.md",
    "initiative-matched/walkthrough-x/walkthrough-x.html",  # no /file/ prefix
    "",
])
def test_not_a_walkthrough_is_404(opener, walk_project, page):
    server, opened = opener
    status, _ = post_open(server, {"page": page}, wt_file=a_py(walk_project))
    assert status == 404
    assert opened == []


@pytest.mark.parametrize("line", ["1;rm", "-1", "1:2", "a", " 1"])
def test_bad_line_is_400(opener, walk_project, line):
    server, opened = opener
    status, _ = post_open(server, {"line": line}, wt_file=a_py(walk_project))
    assert status == 400
    assert opened == []


def test_body_too_big_is_400(opener, walk_project):
    server, opened = opener
    status, _ = post_open(server, {"pad": "x" * 1100}, wt_file=a_py(walk_project))
    assert status == 400
    assert opened == []


def test_code_missing_is_500(walk_project):
    calls = []

    def broken(path, line):
        calls.append(path)
        raise FileNotFoundError("code")

    server = make_server(walk_project, 0, open_file=broken)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, response = post_open(server, wt_file=a_py(walk_project))
        assert status == 500
        assert response.body == b""
        assert calls == [a_py(walk_project).resolve()]
    finally:
        server.shutdown()
        server.server_close()


def test_vscode_still_refuses_null_origin(opener):
    server, _ = opener
    status, _ = send(server, body="id=x", headers={"Origin": "null"})
    assert status == 403


# --- helpers ---


@pytest.mark.parametrize("rel,expected", [
    ("initiative-a/walkthrough-b/walkthrough-b.html", True),
    ("initiative-a/epic/walkthrough-b/x.html", True),
    ("initiative-a/walkthrough-b/x.md", False),
    ("initiative-a/forge-b/x.html", False),
    ("walkthrough-b/x.html", False),
    ("other/walkthrough-b/x.html", False),
    ("initiative-a/x.html", False),
])
def test_is_walkthrough_page(rel, expected):
    assert is_walkthrough_page(Path(rel)) is expected


def test_inject_before_last_body_case_insensitive():
    html = b"<body><p>a</p></BODY><!-- </body> -->"
    out = inject_relay(html)
    assert out.startswith(b"<body><p>a</p></BODY><!-- ")
    assert out.endswith(b"</body> -->")
    assert b"parent.postMessage" in out


def test_inject_appends_without_body():
    out = inject_relay(b"<p>x</p>")
    assert out.startswith(b"<p>x</p>")
    assert out.rstrip().endswith(b"</script>")


@pytest.mark.parametrize("html,expected", [
    (b"<title>A &amp; B</title>", "A & B"),
    (b"<TITLE lang='en'>\n  Hi\n</TITLE>", "Hi"),
    (b"<title>  </title>", "fallback"),
    (b"<p>none</p>", "fallback"),
])
def test_read_title(html, expected):
    assert read_title(html, "fallback") == expected


def test_wrapper_page_escapes_title():
    out = wrapper_page("/file/x.html", '</title><script>alert(1)</script>')
    assert "<script>alert(1)</script>" not in out
    assert "&lt;/title&gt;" in out


def test_default_open_file_runs_code_g_without_shell(monkeypatch, tmp_path):
    calls = []

    class FakePopen:
        def __init__(self, args, **kwargs):
            calls.append((args, kwargs))

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    default_open_file(tmp_path / "a.py", "20")
    default_open_file(tmp_path / "a.py", "")
    assert [c[0] for c in calls] == [
        ["code", "-g", f"{tmp_path / 'a.py'}:20"],
        ["code", "-g", str(tmp_path / "a.py")],
    ]
    for _, kwargs in calls:
        assert not kwargs.get("shell", False)
        assert kwargs["stdin"] is subprocess.DEVNULL
        assert kwargs["stdout"] is subprocess.DEVNULL
        assert kwargs["stderr"] is subprocess.DEVNULL
