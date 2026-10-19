import re
from datetime import datetime, timedelta
from pathlib import Path

from bmad_dash.initiatives import Initiative, Worktree, worktree_id
from bmad_dash.page import relative_time, render_page
from bmad_dash.signals import Activity
from bmad_dash.tree import TreeItem

NOW = datetime(2026, 10, 19, 12, 0)


def render(initiatives, root=Path("/root")):
    return render_page(initiatives, root, now=NOW)


def grid(html: str) -> str:
    """The grid markup only, without CSS and script."""
    return html.split('<div id="initiatives"')[1].split("<script>")[0]


def cell(html: str, css_class: str) -> str:
    """Inner HTML of the first `<div class="<css_class>" role="cell">`."""
    match = re.search(rf'<div class="{css_class}" role="cell">(.*?)</div>', html)
    assert match, css_class
    return match.group(1)


def test_one_row_per_initiative_plus_header():
    initiatives = [
        Initiative("initiative-a", "A", []),
        Initiative("initiative-b", "B", [Worktree(Path("/x"), "main")]),
    ]
    html = render(initiatives)
    assert html.count('role="row"') == 1 + len(initiatives)
    assert html.count('role="table"') == 1
    assert "<table" not in html and "<tr" not in html and "<details" not in html


def test_five_column_heads():
    html = render([])
    heads = re.findall(r'<div role="columnheader">(.*?)</div>', html)
    texts = [re.sub(r"<[^>]+>", "", h) for h in heads]
    assert texts == ["Initiative", "Worktree", "Last activity", "Status", "Actions"]


def test_every_value_is_escaped():
    evil = '<script>alert("x")</script>'
    initiative = Initiative(evil, evil, [Worktree(Path("/" + evil), evil)])
    html = render([initiative], Path("/" + evil))
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_home_paths_shown_with_tilde():
    html = render([], Path.home() / "Code" / "repo")
    assert '<span class="path">~/Code/repo</span>' in html


def test_worktree_title_is_full_path_even_under_home():
    path = Path.home() / "Code" / "repo"
    html = render([Initiative("initiative-a", "A", [Worktree(path, "master")])])
    assert f'<div class="wt" title="{path}">' in html


def test_dark_tokens_present():
    html = render([])
    assert "prefers-color-scheme: dark" in html
    assert "#141417" in html  # surface-dark


# --- tree panel ---

def tree_initiative(tickets: list[dict] | None = [], slug="initiative-a") -> Initiative:
    spec = TreeItem("spec", "spec-<b>", "initiative-a/epic-a/spec-<b>/spec-<b>.md",
                    datetime(2026, 10, 1), "finished")
    epic = TreeItem("epic", "epic-a", "initiative-a/epic-a/epic-a.md",
                    datetime(2026, 10, 2), "open", children=[spec],
                    tickets=[{"ref": "1.1", "title": "<i>T</i>", "state": "review"}],
                    done=0, total=1)
    forge = TreeItem("forge", "forge-a", None, None, "stale")
    return Initiative(slug, "A", [], tickets=tickets, tree=[forge, epic])


def test_tree_renders_items_counts_and_tickets():
    html = render([tree_initiative()])
    assert "0/1" in html
    assert "1.1" in html
    assert 'badge-stale' in html and 'badge-review' in html
    assert "2026-10-02" in html
    assert "tickets unavailable" not in html


def test_tree_escapes_names_and_quotes_links():
    html = render([tree_initiative()])
    assert "<b>" not in html and "<i>" not in html
    assert "spec-&lt;b&gt;" in html
    assert 'href="/file/initiative-a/epic-a/spec-%3Cb%3E/spec-%3Cb%3E.md"' in html
    assert 'href="/file/initiative-a/epic-a/epic-a.md"' in html
    assert html.count('href="/file/') == 2  # forge has no main file


def test_tree_without_tickets_shows_note():
    initiative = tree_initiative(tickets=None)
    initiative.tree[1].done = initiative.tree[1].total = None
    html = render([initiative])
    assert "tickets unavailable" in html
    assert "?/?" in html


def test_one_toggle_per_non_empty_row_controlling_its_panel():
    initiatives = [tree_initiative(slug="a"), Initiative("b", "B", []),
                   tree_initiative(slug="c")]
    html = render(initiatives)
    controls = re.findall(r'class="toggle" aria-expanded="\w+" aria-controls="([\w-]+)"', html)
    assert len(controls) == 2
    for panel_id in controls:
        assert f'<div class="panel" role="cell" id="{panel_id}"' in html


def test_empty_tree_has_no_toggle_or_panel():
    html = render([Initiative("initiative-a", "A", [])])
    assert "toggle" not in grid(html)
    assert 'class="panel"' not in html


def test_panel_follows_title_cell_in_dom():
    html = render([tree_initiative()])
    row = html.split('<div class="init" role="row">')[1]
    order = [row.index(f'class="{c}"') for c in
             ("c-title", "panel", "c-wt", "c-act", "c-status", "c-btns")]
    assert order == sorted(order)


def test_only_newest_activity_row_starts_open():
    old = tree_initiative(slug="old")
    old.activity = Activity(NOW - timedelta(days=3), "", "plan")
    new = tree_initiative(slug="new")
    new.activity = Activity(NOW - timedelta(hours=1), "", "plan")
    none = tree_initiative(slug="none")
    html = grid(render([old, new, none]))
    assert html.count('aria-expanded="true"') == 1
    assert html.count('aria-expanded="false"') == 2
    assert re.search(r'aria-expanded="true" aria-controls="panel-1" data-slug="new"', html)
    assert html.count(" hidden>") == 2
    assert '<div class="panel" role="cell" id="panel-1">' in html


def test_newest_row_with_a_tree_opens_when_newest_has_none():
    empty = Initiative("empty", "E", [])
    empty.activity = Activity(NOW - timedelta(minutes=5), "", "commit")
    older = tree_initiative(slug="older")
    older.activity = Activity(NOW - timedelta(days=2), "", "plan")
    html = grid(render([empty, older]))
    assert html.count('aria-expanded="true"') == 1
    assert 'aria-expanded="true" aria-controls="panel-1" data-slug="older"' in html


def test_nothing_open_without_activity():
    html = grid(render([tree_initiative(slug="a"), tree_initiative(slug="b")]))
    assert 'aria-expanded="true"' not in html
    assert html.count(" hidden>") == 2


def test_storage_key_is_per_project_root():
    html = render([], Path("/some/project"))
    assert 'data-storage-key="bmad-dash:open:/some/project"' in html


# --- worktree cell ---

def test_no_worktree_shows_muted_text():
    html = render([Initiative("initiative-a", "A", [])])
    assert cell(html, "c-wt") == '<span class="muted">no worktree</span>'


def test_worktree_name_with_branch_and_path_in_title():
    worktree = Worktree(Path("/x/repo-feature"), "feature-branch")
    html = render([Initiative("initiative-a", "A", [worktree])])
    assert '<div class="wt" title="/x/repo-feature">' in html
    assert '<span class="wt-name">repo-feature</span>' in html
    assert '<span class="wt-branch">feature-branch</span>' in html


# --- last activity ---

def signals_initiative(tickets, activity=None) -> Initiative:
    initiative = tree_initiative(tickets)
    initiative.activity = activity
    return initiative


def test_relative_time():
    assert relative_time(NOW - timedelta(seconds=30), NOW) == "just now"
    assert relative_time(NOW - timedelta(minutes=27), NOW) == "27 min ago"
    assert relative_time(NOW - timedelta(hours=3, minutes=10), NOW) == "3 h ago"
    assert relative_time(NOW - timedelta(days=3, hours=2), NOW) == "3 days ago"
    assert relative_time(NOW - timedelta(days=1), NOW) == "1 day ago"
    assert relative_time(NOW + timedelta(minutes=1), NOW) == "just now"


def test_activity_cell_shows_relative_time_exact_title_and_escaped_text():
    activity = Activity(datetime(2026, 10, 19, 11, 33), "<b>did</b> \"x\"", "memlog")
    html = render([signals_initiative([], activity)])
    assert '<span class="when" title="2026-10-19 11:33">27 min ago</span>' in html
    assert "<b>did</b>" not in html
    assert "&lt;b&gt;did&lt;/b&gt; &quot;x&quot;" in html


def test_activity_without_text_shows_time_only():
    activity = Activity(datetime(2026, 10, 19, 9, 0), "", "memlog")
    html = render([signals_initiative([], activity)])
    assert cell(html, "c-act") == '<span class="when" title="2026-10-19 09:00">3 h ago</span>'


def test_no_activity_shows_none():
    html = render([signals_initiative([])])
    assert cell(html, "c-act") == '<span class="muted">none</span>'


# --- status cell ---

def test_status_open_and_ticket_counts():
    tickets = [{"state": "done"}, {"state": "done"}, {"state": "review"}, {"state": "dropped"}]
    html = render([signals_initiative(tickets)])
    # Only the epic is open in tree_initiative.
    assert cell(html, "c-status") == '1 open<span class="sep">·</span>2/3 done'


def test_status_tickets_unavailable():
    html = render([signals_initiative(None)])
    assert cell(html, "c-status") == '1 open<span class="sep">·</span>tickets ?'


def test_status_without_tickets_shows_open_only():
    html = render([signals_initiative([])])
    assert cell(html, "c-status") == "1 open"


def test_status_all_dropped_shows_open_only():
    html = render([signals_initiative([{"state": "dropped"}])])
    assert cell(html, "c-status") == "1 open"


# --- actions cell ---

def test_switch_button_per_worktree_with_its_id():
    a, b = Path("/x/a"), Path("/x/b")
    initiative = Initiative("initiative-a", "A", [Worktree(a, "main"), Worktree(b, "dev")])
    html = render([initiative])
    assert html.count('action="/switch"') == 2
    assert f'name="id" value="{worktree_id(a)}"' in html
    assert f'name="id" value="{worktree_id(b)}"' in html
    assert 'method="post"' in html
    assert 'aria-label="Switch to a"' in html
    assert 'aria-label="Switch to b"' in html


def test_no_switch_button_without_worktree():
    html = render([Initiative("initiative-a", "A", [])])
    assert "/switch" not in html


def test_now_defaults_to_current_time():
    initiative = signals_initiative([], Activity(datetime.now(), "", "plan"))
    html = render_page([initiative], Path("/root"))
    assert ">just now<" in html
