import re
from datetime import datetime, timedelta
from pathlib import Path

from bmad_dash.initiatives import Initiative, Worktree, worktree_id
from bmad_dash.page import relative_time, render_page
from bmad_dash.signals import Activity
from bmad_dash.tree import Ticket, TreeItem

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
    html = render([Initiative("initiative-a", "A", [])])
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
                    NOW - timedelta(minutes=27), "open", children=[spec],
                    tickets=[Ticket("1.1", "<i>T</i>", "in progress",
                                    "initiative-a/epic-a/story-t-plan.md",
                                    NOW - timedelta(days=20), True),
                             Ticket("1.2", "Old", "done", None,
                                    NOW - timedelta(days=30), False)],
                    done=1, total=2, word="in progress")
    forge = TreeItem("forge", "forge-a", None, None, "stale")
    return Initiative(slug, "A", [], tickets=tickets, tree=[forge, epic])


def rows_of(html: str) -> list[str]:
    """Each tree row's markup, header first."""
    return re.findall(r'<div class="t-row[^"]*" role="row">(.*?)</div>', html)


def test_tree_is_a_table_with_header_row():
    html = render([tree_initiative()])
    assert '<div class="tree" role="table" aria-label="A contents">' in html
    header = rows_of(html)[0]
    heads = re.findall(r'<span role="columnheader">(.*?)</span>', header)
    assert heads == ["kind", "name", "status", "updated"]


def test_every_tree_row_has_four_cells():
    rows = rows_of(render([tree_initiative()]))
    assert len(rows) == 1 + 5  # header, forge, epic, spec, two tickets
    for row in rows:
        assert row.count('role="cell"') + row.count('role="columnheader"') == 4


def test_tree_row_order_kinds_and_indent():
    rows = rows_of(render([tree_initiative()]))[1:]
    kinds = [re.search(r'class="t-kind">(.*?)</span>', r).group(1) for r in rows]
    depths = [re.search(r'class="t-name (d\d)"', r).group(1) for r in rows]
    assert kinds == ["forge", "epic", "spec", "1.1", "1.2"]
    # One spec in the epic: tickets nest under it.
    assert depths == ["d0", "d0", "d1", "d2", "d2"]


def test_tickets_sit_under_epic_without_a_single_spec():
    initiative = tree_initiative()
    initiative.tree[1].children = []
    rows = rows_of(render([initiative]))[1:]
    depths = [re.search(r'class="t-name (d\d)"', r).group(1) for r in rows]
    assert depths == ["d0", "d0", "d1", "d1"]


def test_status_words_and_empty_doc_status():
    rows = rows_of(render([tree_initiative()]))[1:]
    forge, epic, spec, pending, done = rows
    assert '<span role="cell"></span>' in forge
    assert '<span role="cell"></span>' in spec
    assert '<span role="cell" class="st-pending">in progress</span>' in epic
    assert '<span role="cell" class="st-pending">in progress</span>' in pending
    assert '<span role="cell" class="st-done">done</span>' in done


def test_updated_is_relative_with_exact_title_and_stale_tag():
    rows = rows_of(render([tree_initiative()]))[1:]
    forge, epic, spec, pending, done = rows
    assert '<span role="cell" class="upd"></span>' in forge  # unknown: empty, no tag
    assert "27 min ago" in epic and "stale-tag" not in epic
    assert ('title="2026-09-29 12:00"><span class="upd-time">20 days ago</span>'
            '<span class="stale-tag">stale</span>') in pending
    assert "30 days ago" in done and "stale-tag" not in done


def test_stale_doc_shows_stale_tag():
    initiative = tree_initiative()
    initiative.tree[0] = TreeItem("forge", "forge-a", None, NOW - timedelta(days=20), "stale")
    forge = rows_of(render([initiative]))[1]
    assert '20 days ago</span><span class="stale-tag">stale</span>' in forge


def test_tree_escapes_names_and_quotes_links():
    html = render([tree_initiative()])
    assert "<b>" not in html and "<i>" not in html
    assert "spec-&lt;b&gt;" in html
    assert 'href="/file/initiative-a/epic-a/spec-%3Cb%3E/spec-%3Cb%3E.md"' in html
    assert 'href="/file/initiative-a/epic-a/epic-a.md"' in html
    assert ('<a class="name-link" href="/file/initiative-a/epic-a/story-t-plan.md">'
            "&lt;i&gt;T&lt;/i&gt;</a>") in html
    assert html.count('href="/file/') == 3  # forge and ticket 1.2 have no file


def test_plain_names_without_files():
    rows = rows_of(render([tree_initiative()]))[1:]
    assert '<span role="cell" class="t-name d0"><span class="name-text">forge-a</span>' in rows[0]
    assert '<span role="cell" class="t-name d2"><span class="name-text">Old</span>' in rows[4]


def test_forge_report_link():
    initiative = tree_initiative()
    initiative.tree[0].path = "initiative-a/forge-a/forge-a.md"
    initiative.tree[0].report = "initiative-a/forge-a/forge-report.html"
    forge = rows_of(render([initiative]))[1]
    assert ('<a class="name-link" href="/file/initiative-a/forge-a/forge-a.md">forge-a</a>'
            '<a class="report-link" href="/file/initiative-a/forge-a/forge-report.html">'
            "report</a>") in forge


def test_tree_without_tickets_shows_note_first():
    initiative = tree_initiative(tickets=None)
    epic = initiative.tree[1]
    epic.done = epic.total = None
    epic.tickets, epic.word = [], ""
    html = render([initiative])
    panel = html.split('<div class="panel" role="cell"')[1]
    assert panel.index("tickets unavailable") < panel.index('role="table"')
    rows = rows_of(html)[1:]
    assert len(rows) == 3  # forge, epic, spec: no ticket rows
    assert '<span role="cell"></span>' in rows[1]


def test_tree_has_no_old_badges_or_dates():
    html = grid(render([tree_initiative()]))
    assert "badge" not in html
    assert "2026-10-01<" not in html
    assert "<code>" not in html


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
    assert re.search(r'aria-expanded="true" aria-controls="panel-0" data-slug="new"', html)
    assert html.count(" hidden>") == 2
    assert '<div class="panel" role="cell" id="panel-0">' in html


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
    assert '<main class="page" data-storage-key=' in html
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

def status_text(html: str) -> str:
    """The Status cell's visible text."""
    return re.sub(r"<[^>]+>", "", cell(html, "c-status"))


def finished_tree(initiative: Initiative) -> Initiative:
    """Finish the forge (the spec already is) so only ticket items show."""
    initiative.tree[0].state = "finished"
    return initiative


def test_status_ticket_counts_in_order_then_done():
    tickets = ([{"state": "planned"}] + [{"state": "review"}] * 5
               + [{"state": "dropped"}])
    html = render([finished_tree(signals_initiative(tickets))])
    assert cell(html, "c-status") == (
        '<span class="wait">5 review</span><span class="sep">·</span>'
        '<span class="wait">1 planned</span><span class="sep">·</span>'
        '<span class="done-n">0/6 done</span>'
    )


def test_status_in_progress_word_and_order():
    tickets = [{"state": "backlog"}, {"state": "in-progress"}, {"state": "done"}]
    html = render([finished_tree(signals_initiative(tickets))])
    assert status_text(html) == "1 in progress·1 backlog·1/3 done"


def test_status_doc_cues_without_tickets():
    initiative = signals_initiative([])
    initiative.tree[1].children[0].state = "stale"  # open spec inside the epic
    html = render([initiative])
    # The forge is stale (still open); no `x/y done` without tickets.
    assert status_text(html) == "forge open·spec to slice"


def test_status_overflow_collapses_to_plus_n():
    tickets = [{"state": "review"}, {"state": "planned"}, {"state": "backlog"}]
    html = render([signals_initiative(tickets)])
    # review, planned, backlog, forge open: 2 shown, 2 hidden.
    assert status_text(html) == "1 review·1 planned·+2·0/3 done"
    assert '<span class="wait">+2</span>' in html


def test_status_all_dropped_asks_to_confirm_close_without_done_count():
    html = render([finished_tree(signals_initiative([{"state": "dropped"}]))])
    assert status_text(html) == "confirm close or add work"


def test_nothing_open_without_status_asks_to_confirm_close():
    tickets = [{"state": "done"}] * 4
    html = render([finished_tree(signals_initiative(tickets))])
    assert cell(html, "c-status") == (
        '<span class="wait">confirm close or add work</span><span class="sep">·</span>'
        '<span class="done-n">4/4 done</span>'
    )
    assert "show-finished" not in markup(html)


def test_no_confirm_cue_when_tickets_unavailable():
    html = render([finished_tree(signals_initiative(None))])
    assert "confirm close" not in html


# --- sort, finished, empty states ---

def at(slug: str, hours_ago: float | None, status: str = "") -> Initiative:
    initiative = Initiative(slug, slug.upper(), [], tickets=[], status=status)
    if hours_ago is not None:
        initiative.activity = Activity(NOW - timedelta(hours=hours_ago), "", "plan")
    return initiative


def markup(html: str) -> str:
    """The page without its CSS and script."""
    html = re.sub(r"<style>.*?</style>", "", html, flags=re.S)
    return re.sub(r"<script>.*?</script>", "", html, flags=re.S)


def slugs_in(html: str) -> list[str]:
    return re.findall(r'<span class="slug">(.*?)</span>', html)


def test_rows_sort_newest_activity_first_without_activity_last():
    html = render([at("a", None), at("b", 5), at("c", 1), at("d", 5)])
    assert slugs_in(html) == ["c", "b", "d", "a"]  # ties keep their order


def test_finished_initiatives_hide_behind_show_finished():
    html = render([at("live", 2), at("closed", 1, "done"), at("gone", 3, "dropped")])
    table, _, rest = html.partition('<button type="button" class="show-finished"')
    assert slugs_in(table) == ["live"]
    assert 'aria-expanded="false" aria-controls="finished">Show finished (2)</button>' in rest
    assert '<div id="finished" role="table" aria-label="Finished initiatives" hidden>' in rest
    assert slugs_in(rest) == ["closed", "gone"]
    assert rest.count('<div class="init is-finished" role="row">') == 2
    assert '<div class="c-status" role="cell"><span class="done-n">done</span></div>' in rest
    assert '"Hide finished"' in html  # the script swaps the label


def test_finished_row_is_never_stale_and_ignores_tickets_failure():
    closed = at("closed", 24 * 30, "dropped")
    closed.tickets = None
    html = markup(render([closed]))
    assert '<div class="c-status" role="cell"><span class="done-n">dropped</span></div>' in html
    assert "stale" not in html


def test_finished_row_keeps_done_count():
    closed = at("closed", 1, "done")
    closed.tickets = [{"state": "done"}, {"state": "dropped"}]
    assert status_text(render([closed])) == "done·1/1 done"


def test_script_keeps_finished_rows_closed_on_restore():
    assert 'closest(".is-finished")' in render([])


def test_finished_row_never_starts_open():
    closed = tree_initiative(slug="closed")
    closed.status = "done"
    closed.activity = Activity(NOW, "", "plan")
    html = render([closed])
    assert 'aria-expanded="true"' not in markup(html)


def test_all_finished_says_nothing_in_progress():
    html = render([at("closed", 1, "done")])
    assert '<p class="empty">Nothing in progress.</p>' in html
    assert 'id="initiatives"' not in html
    assert "Show finished (1)" in html


def test_no_initiatives_says_so_with_root():
    html = markup(render([], Path("/some/project")))
    assert ('<p class="empty">No initiatives in <code>/some/project/_bmad-output/</code>.</p>'
            in html)
    assert 'role="table"' not in html and "show-finished" not in html


def test_status_tickets_unavailable_keeps_rest_of_row():
    activity = Activity(NOW - timedelta(hours=3), "did", "memlog")
    html = render([signals_initiative(None, activity)])
    assert cell(html, "c-status") == (
        '<span class="err">Couldn\'t read tickets: tickets.py failed</span>')
    assert ">3 h ago<" in html
    assert 'class="wt-name"' not in html and "no worktree" in html


def test_stale_activity_tags_status_and_tints_time():
    activity = Activity(NOW - timedelta(days=14), "", "memlog")
    html = render([signals_initiative([{"state": "review"}], activity)])
    assert cell(html, "c-status").endswith('<span class="stale-tag">stale</span>')
    assert status_text(html) == "1 review·forge open·0/1 donestale"
    assert '<span class="when is-stale"' in html


def test_fresh_activity_has_no_stale_tag():
    activity = Activity(NOW - timedelta(days=13), "", "memlog")
    html = render([signals_initiative([], activity)])
    assert "stale-tag" not in cell(html, "c-status")
    assert '<span class="when"' in html


def test_tickets_failed_still_shows_stale_tag():
    activity = Activity(NOW - timedelta(days=20), "", "memlog")
    html = render([signals_initiative(None, activity)])
    assert cell(html, "c-status").endswith('<span class="stale-tag">stale</span>')


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
