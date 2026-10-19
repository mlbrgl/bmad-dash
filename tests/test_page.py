from pathlib import Path

from bmad_dash.initiatives import Initiative, Worktree
from bmad_dash.page import COLUMNS, render_page


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
