from datetime import datetime
from pathlib import Path

from bmad_dash.initiatives import Initiative, Worktree
from bmad_dash.page import COLUMNS, render_page
from bmad_dash.tree import TreeItem


def test_one_row_per_initiative_and_one_cell_per_column():
    initiatives = [
        Initiative("initiative-a", "A", []),
        Initiative("initiative-b", "B", [Worktree(Path("/x"), "main")]),
    ]
    html = render_page(initiatives, Path("/root"))
    assert html.count("<tr>") == 1 + len(initiatives)  # header + rows
    assert html.count("<td>") == len(COLUMNS) * len(initiatives)


def test_no_match_shows_none():
    html = render_page([Initiative("initiative-a", "A", [])], Path("/root"))
    assert ">none<" in html


def test_every_value_is_escaped():
    evil = '<script>alert("x")</script>'
    initiative = Initiative(evil, evil, [Worktree(Path("/" + evil), evil)])
    html = render_page([initiative], Path("/" + evil))
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_home_paths_shown_with_tilde():
    worktree = Worktree(Path.home() / "Code" / "repo", "master")
    html = render_page([Initiative("initiative-a", "A", [worktree])], Path("/root"))
    assert "~/Code/repo" in html


def tree_initiative(tickets: list[dict] | None = []) -> Initiative:
    spec = TreeItem("spec", "spec-<b>", "initiative-a/epic-a/spec-<b>/spec-<b>.md",
                    datetime(2026, 10, 1), "finished")
    epic = TreeItem("epic", "epic-a", "initiative-a/epic-a/epic-a.md",
                    datetime(2026, 10, 2), "open", children=[spec],
                    tickets=[{"ref": "1.1", "title": "<i>T</i>", "state": "review"}],
                    done=0, total=1)
    forge = TreeItem("forge", "forge-a", None, None, "stale")
    return Initiative("initiative-a", "A", [], tickets=tickets, tree=[forge, epic])


def test_tree_renders_items_counts_and_tickets():
    html = render_page([tree_initiative()], Path("/root"))
    assert "<details><summary>3 items</summary>" in html
    assert "0/1" in html
    assert "1.1" in html
    assert 'badge-stale' in html and 'badge-review' in html
    assert "2026-10-02" in html
    assert "tickets unavailable" not in html


def test_tree_escapes_names_and_quotes_links():
    html = render_page([tree_initiative()], Path("/root"))
    assert "<b>" not in html and "<i>" not in html
    assert "spec-&lt;b&gt;" in html
    assert 'href="/file/initiative-a/epic-a/spec-%3Cb%3E/spec-%3Cb%3E.md"' in html
    assert 'href="/file/initiative-a/epic-a/epic-a.md"' in html
    assert html.count('href="/file/') == 2  # forge has no main file


def test_tree_without_tickets_shows_note():
    initiative = tree_initiative(tickets=None)
    initiative.tree[1].done = initiative.tree[1].total = None
    html = render_page([initiative], Path("/root"))
    assert "tickets unavailable" in html
    assert "?/?" in html


def test_empty_tree_says_nothing_yet():
    html = render_page([Initiative("initiative-a", "A", [])], Path("/root"))
    assert "nothing yet" in html
