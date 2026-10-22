import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote

from bmad_dash.initiatives import Initiative, Worktree, worktree_id
from bmad_dash.page import relative_time, render_page
from bmad_dash.prs import PullRequest
from bmad_dash.rules import waiting_items
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
    assert f'<button type="submit" class="wt" title="{path}">' in html


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
                    word="in progress")
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


def test_tickets_stay_under_the_spec_when_a_walkthrough_follows():
    initiative = tree_initiative()
    initiative.tree[1].children.append(walkthrough())
    rows = rows_of(render([initiative]))[1:]
    kinds = [re.search(r'class="t-kind">(.*?)</span>', r).group(1) for r in rows]
    depths = [re.search(r'class="t-name (d\d)"', r).group(1) for r in rows]
    assert kinds == ["forge", "epic", "spec", "1.1", "1.2", "walk"]
    assert depths == ["d0", "d0", "d1", "d2", "d2", "d1"]


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
    assert '<span role="cell" class="st-muted">in progress</span>' in epic
    assert '<span role="cell" class="st-muted">in progress</span>' in pending
    assert '<span role="cell" class="st-muted">done</span>' in done


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


def walkthrough(state="open") -> TreeItem:
    return TreeItem("walk", "walkthrough-a",
                    "initiative-a/walkthrough-a/walkthrough-a.html",
                    NOW - timedelta(days=1), state,
                    report="initiative-a/walkthrough-a/walkthrough-a.md",
                    report_label="md")


def test_walkthrough_md_link_and_no_status_word():
    initiative = tree_initiative()
    initiative.tree.insert(1, walkthrough())
    row = rows_of(render([initiative]))[2]
    assert ('<a class="name-link" href="/file/initiative-a/walkthrough-a/walkthrough-a.html">'
            'walkthrough-a</a><a class="report-link" '
            'href="/file/initiative-a/walkthrough-a/walkthrough-a.md">md</a>') in row
    assert "blocks" not in row
    assert '<span role="cell" class="st-' not in row
    assert '<span role="cell" class="t-kind">walk</span>' in row


def test_retro_row_shows_its_verdict_in_grey_and_no_state_when_none():
    for word, cell in [("open items", '<span role="cell" class="st-muted">open items</span>'),
                       ("", '<span role="cell"></span>')]:
        initiative = tree_initiative()
        initiative.tree.insert(1, TreeItem(
            "retro", "epic-a-retrospective", "initiative-a/epic-a/epic-a-retrospective.md",
            NOW - timedelta(days=1), "finished", word=word))
        row = rows_of(render([initiative]))[2]
        assert '<span role="cell" class="t-kind">retro</span>' in row
        assert 'href="/file/initiative-a/epic-a/epic-a-retrospective.md">epic-a-retrospective</a>' in row
        assert cell in row


def test_stale_walkthrough_shows_stale_tag():
    initiative = tree_initiative()
    initiative.tree.insert(1, walkthrough("stale"))
    row = rows_of(render([initiative]))[2]
    assert '1 day ago</span><span class="stale-tag">stale</span>' in row


def test_tree_without_tickets_shows_note_first():
    initiative = tree_initiative(tickets=None)
    epic = initiative.tree[1]
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


def test_panel_comes_last_in_dom():
    html = render([tree_initiative()])
    row = html.split('<div class="init" role="row">')[1]
    order = [row.index(f'class="{c}"') for c in
             ("c-title", "c-wt", "c-act", "c-status", "c-btns", "panel")]
    assert order == sorted(order)


def test_tab_order_caret_worktree_resume_pr_tree():
    initiative = tree_initiative()
    initiative.worktrees = [Worktree(Path("/x/wt"), "b")]
    initiative.tree[0].path = "a/epic.md"
    html = render_page([initiative], Path("/root"), now=NOW, prs={"initiative-a": pr(7, "b")})
    row = html.split('<div class="init" role="row">')[1]
    order = [row.index(marker) for marker in
             ('class="toggle"', 'class="wt"', ">Resume<", ">#7<", 'class="name-link"')]
    assert order == sorted(order)


def test_caret_alone_is_the_toggle():
    html = render([tree_initiative()])
    title = cell(html, "c-title")
    assert re.search(
        r'<button type="button" class="toggle"[^>]* aria-label="Show tree: A">'
        r'<span class="caret" aria-hidden="true"></span></button>A</span>',
        title,
    )


def test_no_row_wide_click_toggle():
    html = render([tree_initiative()])
    assert "closest(\".init\")" not in html
    assert ":has(.toggle)" not in html


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
    assert '<button type="submit" class="wt" title="/x/repo-feature">' in html
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
    tickets = ([{"state": "planned"}] + [{"state": "review", "status": "built"}] * 5
               + [{"state": "dropped"}])
    html = render([finished_tree(signals_initiative(tickets))])
    # No `next` info: a planned ticket is not ready, so it is grey.
    assert cell(html, "c-status") == (
        '<span class="st-built">5 built</span><span class="sep">·</span>'
        '<span class="st-muted">1 planned</span><span class="sep">·</span>'
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


def test_open_walkthrough_leaves_status_unchanged():
    tickets = [{"state": "done"}] * 2
    plain = render([finished_tree(signals_initiative(tickets))])
    initiative = finished_tree(signals_initiative(tickets))
    initiative.tree.insert(1, walkthrough("stale"))
    assert cell(render([initiative]), "c-status") == cell(plain, "c-status")


def test_status_overflow_collapses_to_plus_n():
    tickets = [{"state": "review", "status": "built"}, {"state": "planned"},
               {"state": "backlog"}]
    html = render([signals_initiative(tickets)])
    # built, planned, backlog, forge open: 2 shown, 2 hidden.
    assert status_text(html) == "1 built·1 planned·+2·0/3 done"
    assert '<span class="st-link">+2</span>' in html


def test_status_all_dropped_asks_to_confirm_close_without_done_count():
    html = render([finished_tree(signals_initiative([{"state": "dropped"}]))])
    assert status_text(html) == "confirm close or add work"


def test_nothing_open_without_status_asks_to_confirm_close():
    tickets = [{"state": "done"}] * 4
    html = render([finished_tree(signals_initiative(tickets))])
    assert cell(html, "c-status") == (
        '<span class="st-link">confirm close or add work</span><span class="sep">·</span>'
        '<span class="done-n">4/4 done</span>'
    )
    assert "show-finished" not in markup(html)


def test_unknown_open_state_is_listed_not_confirm_close():
    html = render([finished_tree(signals_initiative([{"state": "stuck"}]))])
    assert status_text(html) == "1 stuck·0/1 done"
    assert "confirm close" not in html


def test_unknown_states_follow_backlog_in_first_appearance_order():
    tickets = [{"state": "stuck"}, {"state": "backlog"}, {"state": "lost"},
               {"state": "stuck"}, {"state": "done"}]
    initiative = finished_tree(signals_initiative(tickets))
    assert waiting_items(initiative) == [
        ("1 backlog", "st-muted"), ("2 stuck", "st-muted"), ("1 lost", "st-muted")]


def test_null_and_missing_state_count_as_no_state():
    tickets = [{"state": None}, {}, {"state": "backlog"}]
    initiative = finished_tree(signals_initiative(tickets))
    assert waiting_items(initiative) == [("1 backlog", "st-muted"), ("2 no state", "st-link")]


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


def test_finished_initiative_tree_has_no_stale_tags():
    initiative = tree_initiative([{"state": "done"}])  # stale forge, stale ticket
    initiative.top_tickets = [Ticket("9", "Top", "planned", None,
                                     NOW - timedelta(days=30), True)]
    assert 'class="stale-tag"' in render([initiative])
    initiative.status = "done"
    html = render([initiative])
    assert "stale-tag" not in html.split('class="panel"')[1]


def test_top_tickets_come_after_epics_at_depth_zero():
    initiative = tree_initiative()
    initiative.top_tickets = [Ticket("9", "Top <t>", "planned",
                                     "initiative-a/story-top-plan.md", NOW, False)]
    rows = rows_of(render([initiative]))
    assert '<span role="cell" class="t-kind">9</span>' in rows[-1]
    assert 'class="t-name d0"' in rows[-1]
    assert 'href="/file/initiative-a/story-top-plan.md"' in rows[-1]
    assert "Top &lt;t&gt;" in rows[-1]


def test_only_top_tickets_still_get_a_caret_and_panel():
    initiative = Initiative("initiative-a", "A", [], tickets=[{"state": "planned"}])
    initiative.top_tickets = [Ticket("1", "Top", "planned", None, NOW, False)]
    html = render([initiative])
    assert 'class="toggle"' in html
    assert 'class="panel"' in html
    assert "Top" in rows_of(html)[-1]


def test_tickets_unavailable_has_no_top_tickets_and_shows_note():
    initiative = tree_initiative(None)
    html = render([initiative])
    assert "tickets unavailable" in html
    assert initiative.top_tickets == []


def test_script_drops_vscode_failed_from_url():
    html = render([])
    assert "history.replaceState" in html
    assert 'params.has("vscode_failed")' in html


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

WT = Path("/x/my wt")


def test_resume_link_opens_claude_in_worktree_with_prompt():
    html = render([Initiative("initiative-a", "A", [Worktree(WT, "main")])])
    actions = cell(html, "c-btns")
    href = ("claude://code/new?folder=%2Fx%2Fmy%20wt"
            "&amp;q=%2Fbmad%20Resume%20work%20on%20initiative-a")
    assert f'<a class="btn btn-1" href="{href}"' in actions
    assert ">Resume</a>" in actions


def test_worktree_cell_posts_id_and_slug():
    html = render([Initiative("initiative-a", "A", [Worktree(WT, "main")])])
    worktree = cell(html, "c-wt")
    assert worktree.startswith('<form class="vscode" method="post" action="/vscode">')
    assert f'name="id" value="{worktree_id(WT)}"' in worktree
    assert 'name="slug" value="initiative-a"' in worktree
    assert '<span class="wt-name">my wt</span><span class="wt-branch">main</span></button>' in worktree


def test_no_separate_vscode_button():
    html = render([Initiative("initiative-a", "A", [Worktree(WT, "main")])])
    assert "VS Code</button>" not in html
    assert "<form" not in cell(html, "c-btns")


def test_no_actions_without_worktree():
    html = render([Initiative("initiative-a", "A", [])])
    assert cell(html, "c-btns") == ""
    assert "no worktree" in cell(html, "c-wt")


def test_slug_in_actions_and_form_is_escaped():
    html = render([Initiative('i-<b>"', "A", [Worktree(WT, "main")])])
    actions = cell(html, "c-btns")
    assert "<b>" not in actions
    assert "i-%3Cb%3E%22" in actions
    assert 'value="i-&lt;b&gt;&quot;"' in cell(html, "c-wt")


def test_failure_message_on_flagged_row_only():
    initiatives = [
        Initiative("initiative-a", "A", [Worktree(WT, "main")]),
        Initiative("initiative-b", "B", [Worktree(Path("/x/b"), "dev")]),
    ]
    html = render_page(initiatives, Path("/root"), now=NOW, vscode_failed="initiative-b")
    assert html.count("Couldn't open VS Code") == 1
    worktrees = re.findall(r'<div class="c-wt" role="cell">(.*?)</div>', html)
    assert "Couldn't open VS Code" not in worktrees[0]
    # Under the branch, in the Worktree cell.
    assert worktrees[1].endswith('</form><span class="err">Couldn\'t open VS Code</span>')


def test_failure_message_shows_even_without_worktree():
    # The worktree vanished between render and click.
    initiatives = [Initiative("initiative-a", "A", [])]
    html = render_page(initiatives, Path("/root"), now=NOW, vscode_failed="initiative-a")
    assert cell(html, "c-wt") == (
        '<span class="muted">no worktree</span>'
        '<span class="err">Couldn\'t open VS Code</span>'
    )
    assert cell(html, "c-btns") == ""


def test_unknown_flag_shows_no_message():
    initiatives = [Initiative("initiative-a", "A", [Worktree(WT, "main")])]
    html = render_page(initiatives, Path("/root"), now=NOW, vscode_failed="nope")
    assert "Couldn't open VS Code" not in html


def test_exactly_one_script():
    html = render([Initiative("initiative-a", "A", [Worktree(WT, "main")])])
    assert html.count("<script") == 1


def test_now_defaults_to_current_time():
    initiative = signals_initiative([], Activity(datetime.now(), "", "plan"))
    html = render_page([initiative], Path("/root"))
    assert ">just now<" in html


# --- PR button ---

def pr(number, head, base="main", state="open"):
    return PullRequest(number, head, base, state, f"https://github.com/o/r/pull/{number}")


def with_prs(prs, branch="b", worktrees=None):
    """Render one row, initiative-a. `prs` maps slug -> PR, or None (GitHub failed)."""
    if worktrees is None:
        worktrees = [Worktree(WT, branch)]
    initiative = Initiative("initiative-a", "A", worktrees)
    return render_page([initiative], Path("/root"), now=NOW, prs=prs)


def with_pr(pr_, **kwargs):
    return with_prs({"initiative-a": pr_}, **kwargs)


def test_pr_button_opens_lone_pr_in_new_tab():
    actions = cell(with_pr(pr(7, "b")), "c-btns")
    assert ('<a class="btn btn-2 pr" href="https://github.com/o/r/pull/7"'
            ' target="_blank" rel="noopener">') in actions
    assert '<svg class="pr-icon pr-open" aria-hidden="true"' in actions
    assert '<span class="sr-only">open pull request </span>#7' in actions
    assert '<span class="sr-only"> (opens in new tab)</span>' in actions
    assert actions.index(">Resume<") < actions.index(">#7<")


def test_pr_button_shows_the_mapped_pr():
    # Which PR is the stack's top is stack.stack_prs's job; the page shows it.
    actions = cell(with_pr(pr(9, "d", base="c")), "c-btns")
    assert ">#9<" in actions


def test_merged_closed_and_draft_tops_are_muted():
    for state in ("merged", "closed", "draft"):
        actions = cell(with_pr(pr(7, "b", state=state)), "c-btns")
        assert '<svg class="pr-icon pr-muted"' in actions
        assert f'<span class="sr-only">{state} pull request </span>#7' in actions


def test_each_state_has_its_own_icon():
    paths = set()
    for state in ("open", "draft", "merged", "closed"):
        actions = cell(with_pr(pr(7, "b", state=state)), "c-btns")
        paths.add(re.search(r'<path fill="currentColor" d="([^"]+)"', actions).group(1))
    assert len(paths) == 4


def test_no_pr_button_without_a_mapped_pr_or_worktree():
    assert 'class="btn btn-2 pr"' not in with_prs({})
    assert 'class="btn btn-2 pr"' not in with_prs({"other-slug": pr(7, "b")})
    assert 'class="btn btn-2 pr"' not in with_pr(pr(7, "b"), worktrees=[])


def test_pr_url_is_escaped():
    evil = PullRequest(7, "b", "main", "open", 'https://x/"><script>')
    html = with_pr(evil)
    assert '"><script>' not in html
    assert 'href="https://x/&quot;&gt;&lt;script&gt;"' in html


def test_github_failure_shows_header_line_and_no_pr_buttons():
    html = with_prs(None)
    header = html.split('<header class="hdr">')[1].split("</header>")[0]
    assert header.endswith('<span class="err">Couldn\'t reach GitHub</span>')
    assert 'class="btn btn-2 pr"' not in html
    assert ">Resume<" in html  # rest of the page unchanged


def test_no_github_line_when_prs_known_or_empty():
    assert "Couldn't reach GitHub" not in with_prs({})
    assert "Couldn't reach GitHub" not in with_pr(pr(7, "b"))
    assert "Couldn't reach GitHub" not in render([])


# --- ticket actions ---

def ticket_action_html(word, ready=False, worktrees=True, **extra):
    ticket = Ticket("4.2", "Ticket actions", word, None, None, False, ready, **extra)
    item = TreeItem("epic", "epic-x", None, None, "open", tickets=[ticket], word="in progress")
    wts = [Worktree(WT, "main")] if worktrees else []
    html = render([Initiative("initiative-a", "A", wts, tree=[item])])
    row = re.search(r'<span role="cell" class="t-kind">4\.2</span>(.*?)</div>', html).group(1)
    after_name = row[row.index("</span></span>") + len("</span></span>"):]
    return after_name[:after_name.index('<span role="cell" class="upd"')]


FOLDER = "folder=%2Fx%2Fmy%20wt"


def test_ready_ticket_shows_start_link():
    html = ticket_action_html("planned", ready=True)
    assert html == (
        f'<span role="cell"><a class="st-link" href="claude://code/new?{FOLDER}'
        '&amp;q=%2Fbmad-build%204.2%20Ticket%20actions" '
        'aria-label="start 4.2 Ticket actions">start</a></span>')
    assert ">start</a>" in ticket_action_html("backlog", ready=True)


def test_not_ready_planned_is_a_status():
    assert ticket_action_html("planned") == '<span role="cell" class="st-muted">planned</span>'


def test_in_progress_and_in_review_show_resume():
    for word in ("in progress", "in review"):
        html = ticket_action_html(word)
        assert ('q=%2Fbmad-build%20Resume%204.2%20Ticket%20actions" '
                'aria-label="resume 4.2 Ticket actions">resume</a>') in html
        assert 'class="st-link"' in html


def test_blocked_shows_warm_unblock_with_reason():
    html = ticket_action_html("blocked", blocked_reason="needs x")
    assert 'class="st-warm"' in html and ">unblock</a>" in html
    assert 'aria-label="unblock 4.2 Ticket actions"' in html
    assert "Resume%204.2%20Ticket%20actions%3A%20blocked%3A%20needs%20x" in html
    bare = ticket_action_html("blocked")
    assert "Actions%22" not in bare and "blocked%3A" not in bare


def test_no_state_shows_fix():
    html = ticket_action_html("no state")
    assert ">fix</a>" in html and 'class="st-link"' in html
    assert "q=%2Fbmad-ticket%20Check%204.2%20Ticket%20actions%3A%20it%20has%20no%20state" in html


def test_other_ticket_words_are_statuses_never_links():
    expected = {"built": "st-built", "waits on 3.1": "st-muted", "done": "st-muted",
                "dropped": "st-muted", "mystery": "st-muted"}
    for word, css_class in expected.items():
        assert ticket_action_html(word) == f'<span role="cell" class="{css_class}">{word}</span>'


def test_no_ticket_link_without_worktree():
    for word, ready in [("planned", True), ("in progress", False), ("blocked", False),
                        ("no state", False)]:
        assert "<a " not in ticket_action_html(word, ready, worktrees=False)


def test_no_worktree_statuses_keep_built_green_blocked_warm_rest_grey():
    expected = {"built": "st-built", "blocked": "st-warm", "planned": "st-muted",
                "in progress": "st-muted", "no state": "st-muted"}
    for word, css_class in expected.items():
        html = ticket_action_html(word, ready=True, worktrees=False)
        assert html == f'<span role="cell" class="{css_class}">{word}</span>'


# --- epic and retro actions ---

def epic_item(word, name="epic-x", retro_word=None, tickets=()):
    children = []
    if retro_word is not None:
        children.append(TreeItem("retro", f"{name}-retrospective",
                                 f"initiative-a/{name}/{name}-retrospective.md",
                                 None, "finished", word=retro_word))
    return TreeItem("epic", name, f"initiative-a/{name}/{name}.md", None, "open",
                    children=children, tickets=list(tickets), word=word)


def item_cells(item, worktrees=True):
    wts = [Worktree(WT, "main")] if worktrees else []
    html = render([Initiative("initiative-a", "A", wts, tree=[item])])
    cells = []
    for row in rows_of(html)[1:]:
        end = re.search(r"</(?:a|span)></span>", row).end()
        after = row[end:]
        cells.append(after[:after.index('<span role="cell" class="upd"')])
    return cells


def hrefs(cell_html):
    return [unquote(h.replace("&amp;", "&").split("q=", 1)[1])
            for h in re.findall(r'href="(claude://[^"]+)"', cell_html)]


def test_to_close_epic_is_one_close_link():
    html = item_cells(epic_item("to close", "epic-dashboard"))[0]
    assert html == (
        f'<span role="cell"><a class="st-link" href="claude://code/new?{FOLDER}'
        '&amp;q=%2Fbmad-ticket%20Close%20epic-dashboard" '
        'aria-label="close: close epic-dashboard in a new session">close</a></span>')


def test_not_started_epic_has_start_and_auto():
    html = item_cells(epic_item("not started", "epic-several-projects"))[0]
    assert ">start</a>" in html and ">auto</a>" in html
    assert 'class="sep">·</span>' in html
    start, auto = hrefs(html)
    assert start == "/bmad Start work on epic-several-projects"
    assert auto.startswith("/loop Build epic-several-projects unattended")
    assert auto.endswith("Stop for no other reason.")


def test_in_progress_epic_has_resume_and_auto_and_addresses_rejected_retro():
    html = item_cells(epic_item("in progress"))[0]
    assert ">resume</a>" in html and ">auto</a>" in html
    assert hrefs(html)[0] == "/bmad Resume work on epic-x"
    rejected = item_cells(epic_item("in progress", retro_word="rejected"))[0]
    assert hrefs(rejected)[0] == (
        "/bmad Resume work on epic-x: address _bmad-output/initiative-a/epic-x/epic-x-retrospective.md")


def test_to_retro_epic_has_retro_only():
    html = item_cells(epic_item("to retro"))[0]
    assert hrefs(html) == ["/bmad-retrospective epic-x"]
    assert 'aria-label="retro: run the retrospective for epic-x in a new session"' in html
    assert "auto" not in html


def test_close_with_built_ticket_names_the_retro():
    built = Ticket("1.1", "T", "built", None, None, False)
    html = item_cells(epic_item("to close", retro_word="accepted", tickets=[built]))[0]
    assert hrefs(html) == [
        "/bmad-ticket Close epic-x: mark its built tickets done, per "
        "_bmad-output/initiative-a/epic-x/epic-x-retrospective.md"]
    no_retro = item_cells(epic_item("to close", tickets=[built]))[0]
    assert hrefs(no_retro) == ["/bmad-ticket Close epic-x"]


def test_done_dropped_and_empty_epics_are_statuses():
    for word in ("done", "dropped"):
        assert item_cells(epic_item(word))[0] == f'<span role="cell" class="st-muted">{word}</span>'
    assert item_cells(epic_item(""))[0] == '<span role="cell"></span>'


def test_retro_open_items_shows_plan_items():
    html = item_cells(epic_item("to close", retro_word="open items"))[1]
    assert hrefs(html) == [
        "/bmad-ticket Turn the action items in "
        "_bmad-output/initiative-a/epic-x/epic-x-retrospective.md into tickets"]
    assert ">plan items</a>" in html and 'class="st-link"' in html


def test_retro_rejected_shows_fix_with_the_resume_prompt():
    html = item_cells(epic_item("in progress", retro_word="rejected"))[1]
    assert ">fix</a>" in html
    assert hrefs(html) == [
        "/bmad Resume work on epic-x: address "
        "_bmad-output/initiative-a/epic-x/epic-x-retrospective.md"]


def test_retro_accepted_or_empty_is_a_status():
    assert item_cells(epic_item("to close", retro_word="accepted"))[1] == (
        '<span role="cell" class="st-muted">accepted</span>')
    assert item_cells(epic_item("to close", retro_word=""))[1] == '<span role="cell"></span>'


def test_no_epic_or_retro_link_without_worktree():
    for word in ("not started", "in progress", "to retro", "to close"):
        for html in item_cells(epic_item(word, retro_word="rejected"), worktrees=False):
            assert "<a " not in html
    assert item_cells(epic_item("to retro", retro_word="open items"), worktrees=False)[1] == (
        '<span role="cell" class="st-muted">open items</span>')
