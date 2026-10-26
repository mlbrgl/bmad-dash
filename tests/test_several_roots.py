"""Several `--project-root`s served by one dashboard."""

import re
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import pytest

import bmad_dash.server as server_module
from bmad_dash.initiatives import Initiative, Worktree, list_worktrees, worktree_id
from bmad_dash.page import render_page
from bmad_dash.prs import PullRequest
from bmad_dash.signals import Activity
from conftest import git, write_config, write_initiative
from test_server import run_main
from test_vscode import send

NOW = datetime(2026, 10, 19, 12, 0)
WALK_HTML = "<!doctype html><html><head><title>W</title></head><body></body></html>\n"


def make_repo(root: Path) -> Path:
    """A git repo at `root` with one empty commit and `_bmad-output/`."""
    root.mkdir(parents=True)
    git(root, "init", "-b", "main")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit",
        "--allow-empty", "-m", "init")
    (root / "_bmad-output").mkdir()
    return root


@pytest.fixture
def other(tmp_path: Path) -> Path:
    """Root B, named `other`, next to `project` (root A).

    - initiative-matched (same slug as in A), claimed by wt-o (branch feature-o)
      with a walkthrough and wt-o/src/o.py to open
    - initiative-b-only
    """
    root = make_repo(tmp_path.resolve() / "b" / "other")
    wt_o = root.parent / "worktrees" / "wt-o"
    git(root, "worktree", "add", "-b", "feature-o", str(wt_o))
    write_config(wt_o, '[core]\nactive_initiative = "initiative-matched"\n')
    write_initiative(root, "initiative-matched", '---\ntitle: "B matched"\n---\n')
    write_initiative(root, "initiative-b-only", '---\ntitle: "Only in B"\n---\n')
    walk = root / "_bmad-output" / "initiative-matched" / "walkthrough-b"
    walk.mkdir()
    (walk / "walkthrough-b.html").write_text(WALK_HTML)
    (wt_o / "src").mkdir()
    (wt_o / "src" / "o.py").write_text("o\n")
    return root


def get_page(server) -> str:
    url = f"http://127.0.0.1:{server.server_port}/"
    with urllib.request.urlopen(url) as response:
        return response.read().decode()


def rows(html: str) -> list[str]:
    """The `<project> · <slug>` of each row, in page order: the slug from the
    Initiative cell, the project from the Project cell after it."""
    pairs = re.findall(r'<span class="slug">([^<]+)</span></div>'
                       r'<div class="c-proj" role="cell"><span class="proj">([^<]+)</span>', html)
    return [f"{project} · {slug}" for slug, project in pairs]


def row_of(html: str, name: str) -> str:
    """The markup of the one row whose `rows()` entry is `name`."""
    project, slug = name.split(" · ")
    found = [row for row in html.split('role="row">')
             if f'<span class="slug">{slug}</span>' in row
             and f'<span class="proj">{project}</span>' in row]
    assert len(found) == 1, name
    return found[0]


# --- page ---


def initiative(project, slug, hours_ago, status=""):
    return Initiative(slug, slug, project=project, status=status,
                      activity=Activity(NOW - timedelta(hours=hours_ago), "", "commit"))


def test_two_roots_interleave_newest_first_with_one_show_finished():
    a = [initiative("a", "initiative-1", 1), initiative("a", "initiative-3", 3),
         initiative("a", "initiative-done", 2, status="done")]
    b = [initiative("b", "initiative-2", 2), initiative("b", "initiative-4", 4),
         initiative("b", "initiative-gone", 5, status="dropped")]
    html = render_page(a + b, [Path("/x/a"), Path("/x/b")], now=NOW)
    current = html.split('id="finished"')[0]
    assert rows(current) == ["a · initiative-1", "b · initiative-2",
                             "a · initiative-3", "b · initiative-4"]
    assert html.count('id="show-finished"') == 1
    assert "Show finished (2)" in html


def test_header_and_title_list_every_root_in_order():
    html = render_page([], [Path("/x/b"), Path("/x/a")], now=NOW)
    assert ('<h1>Initiatives</h1><span class="path">/x/b</span>'
            '<span class="path">/x/a</span></header>') in html
    assert "<title>bmad-dash: /x/b, /x/a</title>" in html


def test_empty_state_names_each_root():
    html = render_page([], [Path("/x/a"), Path("/x/b")], now=NOW)
    assert html.count('<p class="empty">') == 2
    first = html.index("<code>/x/a/_bmad-output/</code>")
    assert first < html.index("<code>/x/b/_bmad-output/</code>")


def test_one_empty_root_still_in_header():
    html = render_page([initiative("a", "initiative-1", 1)], [Path("/x/a"), Path("/x/b")], now=NOW)
    assert rows(html) == ["a · initiative-1"]
    assert '<span class="path">/x/b</span>' in html
    assert '<p class="empty">' not in html


def test_pr_is_looked_up_by_project_and_slug():
    wt = [Worktree(Path("/wt"), "b")]
    a = Initiative("initiative-x", "A", wt, project="a")
    b = Initiative("initiative-x", "B", wt, project="b")
    pr = PullRequest(7, "b", "main", "open", "https://github.com/o/r/pull/7")
    html = render_page([a, b], [Path("/x/a"), Path("/x/b")], now=NOW, prs={"b/initiative-x": pr})
    assert html.count('class="place pr"') == 1
    assert 'class="place pr"' in row_of(html, "b · initiative-x")


# --- server ---


def test_same_slug_in_two_roots_gives_two_rows(project, other, serve):
    html = get_page(serve([project, other]))
    assert "project · initiative-matched" in rows(html)
    assert "other · initiative-matched" in rows(html)
    assert "other · initiative-b-only" in rows(html)
    # Each row's VS Code form names its own project.
    assert '<input type="hidden" name="project" value="other">' in html
    assert '<input type="hidden" name="project" value="project">' in html
    # B's tree links go to B.
    assert 'href="/file/other/initiative-matched/walkthrough-b/walkthrough-b.html"' in html


def write_memlog(root: Path, slug: str, updated: str) -> None:
    """Gives `slug` in `root` its last activity at `updated` (far future, so
    it beats the fixtures' commits)."""
    folder = root / "_bmad-output" / slug / "forge-z"
    folder.mkdir(parents=True)
    (folder / ".memlog.md").write_text(f"---\nupdated: {updated}\n---\n\n- (note) x\n")


def test_server_merges_roots_newest_first_with_one_show_finished(project, other, serve):
    write_memlog(project, "initiative-orphan", "2099-01-04T10:00")
    write_memlog(other, "initiative-b-only", "2099-01-03T10:00")
    write_memlog(project, "initiative-matched", "2099-01-02T10:00")
    write_memlog(other, "initiative-matched", "2099-01-01T10:00")
    write_initiative(project, "initiative-a-done", "---\ntitle: A done\nstatus: done\n---\n")
    write_initiative(other, "initiative-b-done", "---\ntitle: B done\nstatus: dropped\n---\n")
    html = get_page(serve([project, other]))
    current = rows(html.split('id="finished"')[0])
    assert current[:4] == ["project · initiative-orphan", "other · initiative-b-only",
                           "project · initiative-matched", "other · initiative-matched"]
    assert html.count('id="show-finished"') == 1
    assert "Show finished (2)" in html
    finished = rows(html.split('id="finished"')[1])
    assert sorted(finished) == ["other · initiative-b-done", "project · initiative-a-done"]


def test_file_route_serves_from_each_root(project, other, serve):
    (project / "_bmad-output" / "initiative-matched" / "a.md").write_text("# From A\n")
    (other / "_bmad-output" / "initiative-matched" / "a.md").write_text("# From B\n")
    server = serve([project, other])
    status_a, a = send(server, method="GET", path="/file/project/initiative-matched/a.md",
                       headers={"Origin": None})
    status_b, b = send(server, method="GET", path="/file/other/initiative-matched/a.md",
                       headers={"Origin": None})
    assert (status_a, status_b) == (200, 200)
    assert b"From A" in a.body and b"From B" not in a.body
    assert b"From B" in b.body


def test_vscode_post_for_b_opens_b_worktree(project, other, serve):
    launched = []
    server = serve([project, other], launch=launched.append)
    wt_o = next(w.path for w in list_worktrees(other) if w.path.name == "wt-o")
    body = urlencode({"project": "other", "id": worktree_id(wt_o), "slug": "initiative-matched"})
    status, response = send(server, body=body)
    assert status == 303
    assert response.getheader("Location") == "/"
    assert launched == [wt_o]
    # A's id under B's name is not B's worktree: refused.
    body = urlencode({"project": "other", "id": worktree_id(project), "slug": "initiative-matched"})
    status, response = send(server, body=body)
    assert response.getheader("Location") == "/?vscode_failed=other%2Finitiative-matched"
    assert launched == [wt_o]


def test_open_file_for_b_walkthrough_opens_in_b_worktree(project, other, serve):
    opened = []
    server = serve([project, other], open_file=lambda path, line: opened.append((path, line)))
    wt_o = next(w.path for w in list_worktrees(other) if w.path.name == "wt-o")
    form = {"page": "/file/other/initiative-matched/walkthrough-b/walkthrough-b.html",
            "path": str(wt_o / "src" / "o.py"), "line": "3"}
    status, _ = send(server, body=urlencode(form), path="/open-file")
    assert status == 204
    assert opened == [((wt_o / "src" / "o.py").resolve(), "3")]


def test_github_failure_in_one_root_keeps_the_others_buttons(project, other, serve, monkeypatch):
    pr = PullRequest(7, "feature-a", "main", "open", "https://github.com/o/r/pull/7")

    def topology(root, stack):
        return [pr] if root == project else None  # B's GitHub fails

    monkeypatch.setattr(server_module, "load_topology", topology)
    html = get_page(serve([project, other]))
    assert html.count("Couldn't reach GitHub") == 1
    assert html.count('class="place pr"') == 1
    a_row = row_of(html, "project · initiative-matched")
    assert 'href="https://github.com/o/r/pull/7"' in a_row


def test_each_root_loads_once(project, other, serve, monkeypatch):
    real = server_module.load_root
    seen = []

    def load_root(root):
        seen.append(root)
        return real(root)

    monkeypatch.setattr(server_module, "load_root", load_root)
    get_page(serve([project, other]))
    assert sorted(seen) == sorted([project, other])


# --- CLI ---


def test_second_bad_root_exits_naming_it(project, tmp_path):
    bad = tmp_path / "nope"
    bad.mkdir()
    result = run_main("--project-root", str(project), "--project-root", str(bad))
    assert result.returncode == 1
    assert f"bmad-dash: {bad.resolve()} has no _bmad-output/ folder" in result.stderr
    assert str(project) not in result.stderr


def test_duplicate_name_exits_naming_both(project, tmp_path):
    twin = make_repo(tmp_path.resolve() / "elsewhere" / "project")
    result = run_main("--project-root", str(project), "--project-root", str(twin))
    assert result.returncode == 1
    assert str(project) in result.stderr and str(twin) in result.stderr
    assert "named project" in result.stderr


def test_same_root_twice_exits(project):
    result = run_main("--project-root", str(project), "--project-root", str(project))
    assert result.returncode == 1
    assert "named project" in result.stderr


def test_duplicate_names_ignore_case():
    # On macOS ~/Code/Foo and ~/Code/foo are the same folder.
    error = server_module.duplicate_names([Path("/a/Foo"), Path("/b/foo")])
    assert error == "/a/Foo and /b/foo are both named foo"


def test_case_variant_root_exits_naming_both(project):
    variant = project.parent / project.name.upper()
    if not variant.exists():
        pytest.skip("case-sensitive file system: the variant is another folder")
    result = run_main("--project-root", str(project), "--project-root", str(variant))
    assert result.returncode == 1
    assert str(project) in result.stderr and str(variant.resolve()) in result.stderr


def test_root_that_fails_to_load_leaves_the_others(project, other, serve, monkeypatch, capfd):
    real = server_module.load_root

    def load_root(root):
        if root == other:
            raise OSError("boom")
        return real(root)

    monkeypatch.setattr(server_module, "load_root", load_root)
    html = get_page(serve([project, other]))
    assert "project · initiative-matched" in rows(html)
    assert not [row for row in rows(html) if row.startswith("other · ")]
    assert html.count("Couldn't load") == 1
    assert f"Couldn't load <code>{other}</code>" in html
    err = capfd.readouterr().err
    assert f"bmad-dash: couldn't load {other}" in err and "OSError: boom" in err


def test_failed_root_is_left_out_of_the_empty_state():
    html = render_page([], [Path("/r/a"), Path("/r/b")], now=NOW,
                       failed_roots=[Path("/r/b")])
    assert "No initiatives in <code>/r/a/_bmad-output/</code>" in html
    assert "No initiatives in <code>/r/b" not in html
    assert "Couldn't load <code>/r/b</code>" in html


def test_vscode_failed_names_one_project_when_slugs_match():
    html = render_page(
        [initiative("project", "initiative-x", 1), initiative("other", "initiative-x", 2)],
        [Path("/r/project"), Path("/r/other")],
        now=NOW,
        vscode_failed="other/initiative-x",
    )
    assert html.count("Couldn't open VS Code") == 1
    other_row = row_of(html, "other · initiative-x")
    assert "Couldn't open VS Code" in other_row
