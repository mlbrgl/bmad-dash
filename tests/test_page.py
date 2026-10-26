import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote, unquote

from bmad_dash.initiatives import Initiative, Worktree, worktree_id
from bmad_dash.page import PR_ICONS, relative_time, render_page as render_page_for
from bmad_dash.prs import PullRequest
from bmad_dash.rules import (
    action_css,
    doc_actions,
    item_actions,
    resume_prompt,
    retro_word,
)
from bmad_dash.signals import Activity
from bmad_dash.tree import Ticket, TreeItem

NOW = datetime(2026, 10, 19, 12, 0)
PROJECT = "proj"


def render_page(initiatives, *args, **kwargs):
    """The page, with every initiative named after PROJECT unless it has one."""
    for initiative in initiatives:
        initiative.project = initiative.project or PROJECT
    return render_page_for(initiatives, *args, **kwargs)


def render(initiatives, root=Path("/root")):
    return render_page(initiatives, [root], now=NOW)


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
        Initiative("initiative-a", "A", [], tickets=[]),
        Initiative("initiative-b", "B", [Worktree(Path("/x"), "main")], tickets=[]),
    ]
    html = render(initiatives)
    assert html.count('role="row"') == 1 + len(initiatives)
    assert html.count('role="table"') == 1
    assert "<table" not in html and "<tr" not in html and "<details" not in html


def test_four_column_heads():
    html = render([Initiative("initiative-a", "A", [])])
    heads = re.findall(r'<div role="columnheader">(.*?)</div>', html)
    texts = [re.sub(r"<[^>]+>", "", h) for h in heads]
    assert texts == ["Initiative", "Project", "Last activity", "Actions"]


def test_every_value_is_escaped():
    evil = '<script>alert("x")</script>'
    initiative = Initiative(evil, evil, [Worktree(Path("/" + evil), evil)])
    html = render([initiative], Path("/" + evil))
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_home_paths_shown_with_tilde():
    html = render([], Path.home() / "Code" / "repo")
    assert '<span class="path">~/Code/repo</span>' in html


def test_vscode_button_names_worktree_and_branch():
    path = Path.home() / "Code" / "repo-feature"
    html = render([Initiative("initiative-a", "A", [Worktree(path, "master")])])
    label = "Open worktree repo-feature (branch master) in VS Code"
    assert (f'<button type="submit" class="place" title="{label}"'
            f' aria-label="{label}"><svg class="i"') in html


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
                             Ticket("1.2", "Old", "backlog", None,
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
    assert '<span role="cell" class="st-muted">backlog</span>' in done


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
    assert 'href="/file/proj/initiative-a/epic-a/spec-%3Cb%3E/spec-%3Cb%3E.md"' in html
    assert 'href="/file/proj/initiative-a/epic-a/epic-a.md"' in html
    assert ('<a class="name-link" href="/file/proj/initiative-a/epic-a/story-t-plan.md">'
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
    assert ('<a class="name-link" href="/file/proj/initiative-a/forge-a/forge-a.md">forge-a</a>'
            '<a class="report-link" href="/file/proj/initiative-a/forge-a/forge-report.html">'
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
    assert ('<a class="name-link" href="/file/proj/initiative-a/walkthrough-a/walkthrough-a.html">'
            'walkthrough-a</a><a class="report-link" '
            'href="/file/proj/initiative-a/walkthrough-a/walkthrough-a.md">md</a>') in row
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
        assert 'href="/file/proj/initiative-a/epic-a/epic-a-retrospective.md">epic-a-retrospective</a>' in row
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
    assert panel.index("Couldn't read tickets: tickets.py failed") < panel.index('role="table"')
    rows = rows_of(html)[1:]
    assert len(rows) == 3  # forge, epic, spec: no ticket rows
    assert '<span role="cell"></span>' in rows[1]


def test_tree_has_no_old_badges_or_dates():
    html = grid(render([tree_initiative()]))
    assert "badge" not in html
    assert "2026-10-01<" not in html
    assert "<code>" not in html


def test_one_toggle_per_non_empty_row_controlling_its_panel():
    initiatives = [tree_initiative(slug="a"), Initiative("b", "B", [], tickets=[]),
                   tree_initiative(slug="c")]
    html = render(initiatives)
    controls = re.findall(r'class="toggle" aria-expanded="\w+" aria-controls="([\w-]+)"', html)
    assert len(controls) == 2
    for panel_id in controls:
        assert f'<div class="panel" role="cell" id="{panel_id}"' in html


def test_empty_tree_has_no_toggle_or_panel():
    html = render([Initiative("initiative-a", "A", [], tickets=[])])
    assert "toggle" not in grid(html)
    assert 'class="panel"' not in html


def test_panel_comes_last_in_dom():
    html = render([tree_initiative()])
    row = html.split('<div class="init" role="row">')[1]
    order = [row.index(f'class="{c}"') for c in
             ("c-title", "c-proj", "c-act", "c-btns", "panel")]
    assert order == sorted(order)


def test_tab_order_caret_vscode_pr_resume_tree():
    initiative = tree_initiative()
    initiative.worktrees = [Worktree(Path("/x/wt"), "b")]
    initiative.tree[0].path = "a/epic.md"
    html = render_page([initiative], [Path("/root")], now=NOW, prs={"proj/initiative-a": pr(7, "b")})
    row = html.split('<div class="init" role="row">')[1]
    order = [row.index(marker) for marker in
             ('class="toggle"', 'button type="submit" class="place"', 'class="place pr"',
              ">resume<", 'class="name-link"')]
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
    assert html.count('class="toggle" aria-expanded="true"') == 1
    assert html.count('class="toggle" aria-expanded="false"') == 2
    assert re.search(r'aria-expanded="true" aria-controls="panel-0" data-key="proj/new"', html)
    assert html.count(" hidden>") == 2
    assert '<div class="panel" role="cell" id="panel-0">' in html


def test_newest_row_with_a_tree_opens_when_newest_has_none():
    empty = Initiative("empty", "E", [], tickets=[])
    empty.activity = Activity(NOW - timedelta(minutes=5), "", "commit")
    older = tree_initiative(slug="older")
    older.activity = Activity(NOW - timedelta(days=2), "", "plan")
    html = grid(render([empty, older]))
    assert html.count('class="toggle" aria-expanded="true"') == 1
    assert 'aria-expanded="true" aria-controls="panel-1" data-key="proj/older"' in html


def test_nothing_open_without_activity():
    html = grid(render([tree_initiative(slug="a"), tree_initiative(slug="b")]))
    assert 'class="toggle" aria-expanded="true"' not in html
    assert html.count(" hidden>") == 2


def test_storage_key_is_one_key_for_all_projects():
    html = render([], Path("/some/project"))
    assert '<main class="page" data-storage-key=' in html
    assert 'data-storage-key="bmad-dash:open"' in html


# --- project cell ---

EMPTY_SLOT = '<span class="place" aria-hidden="true"></span>'


def slots(html: str) -> list[str]:
    """The place slots of the first Project cell, in order."""
    places = cell(html, "c-proj").split('<span class="places">')[1]
    return re.findall(r'<form class="vscode".*?</form>|<span class="place"[^>]*></span>'
                      r'|<a class="place pr".*?</a>', places)


def test_no_worktree_shows_name_and_muted_text_and_no_slots():
    html = render([Initiative("initiative-a", "A", [])])
    assert cell(html, "c-proj") == (
        f'<span class="proj">{PROJECT}</span>'
        '<span class="muted no-wt">no worktree</span>'
    )
    assert 'class="place' not in row_html(html)


def test_worktree_and_pr_fill_four_slots_in_order():
    html = with_pr(pr(7, "b"))
    assert cell(html, "c-proj").startswith(f'<span class="proj">{PROJECT}</span><span class="places">')
    found = slots(html)
    assert len(found) == 4
    assert found[0].startswith('<form class="vscode"')
    assert found[1] == found[2] == EMPTY_SLOT
    assert found[3].startswith('<a class="place pr"')


def test_worktree_without_pr_has_vscode_then_three_empty_slots():
    found = slots(with_prs({}))
    assert len(found) == 4
    assert found[0].startswith('<form class="vscode"')
    assert found[1:] == [EMPTY_SLOT] * 3


def test_main_checkout_title():
    initiative = Initiative("initiative-a", "A", [Worktree(Path("/x/proj"), "main")])
    html = render([initiative])
    label = "Open main checkout (branch main) in VS Code"
    assert f'title="{label}" aria-label="{label}"' in html


def test_vscode_label_is_escaped():
    initiative = Initiative("initiative-a", "A", [Worktree(Path('/x/w<"'), 'b<"')])
    proj = cell(render([initiative]), "c-proj")
    label = "Open worktree w&lt;&quot; (branch b&lt;&quot;) in VS Code"
    assert f'title="{label}" aria-label="{label}"' in proj
    assert '<"' not in proj


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


def activity_initiative(activity: Activity) -> Initiative:
    initiative = signals_initiative([], activity)
    initiative.project = "my proj"
    return initiative


def test_memlog_activity_links_to_its_memlog():
    activity = Activity(NOW - timedelta(hours=1), "<b>did</b>", "memlog",
                        "initiative-a/forge-a/.memlog.md")
    html = render([activity_initiative(activity)])
    assert cell(html, "c-act").endswith(
        '<a class="what" href="/file/my%20proj/initiative-a/forge-a/.memlog.md"'
        ' title="&lt;b&gt;did&lt;/b&gt;">&lt;b&gt;did&lt;/b&gt;</a>')


def test_plan_activity_links_to_its_plan():
    activity = Activity(NOW - timedelta(hours=1), "story-a-plan.md", "plan",
                        "initiative-a/epic-a/story-a-plan.md")
    html = render([activity_initiative(activity)])
    assert ('<a class="what" href="/file/my%20proj/initiative-a/epic-a/story-a-plan.md"'
            ' title="story-a-plan.md">story-a-plan.md</a>') in cell(html, "c-act")


def test_commit_activity_is_plain_text():
    activity = Activity(NOW - timedelta(hours=1), "feat: x", "commit")
    html = render([activity_initiative(activity)])
    assert cell(html, "c-act").endswith('<span class="what" title="feat: x">feat: x</span>')
    assert "<a" not in cell(html, "c-act")


def test_what_changed_clamps_to_two_lines():
    css = (Path(__file__).parents[1] / "src/bmad_dash/assets/page.css").read_text()
    what = re.search(r"\n\.what \{([^}]*)\}", css).group(1)
    assert "-webkit-line-clamp: 2" in what
    assert "display: -webkit-box" in what
    assert "nowrap" not in what


def test_no_activity_shows_none():
    html = render([signals_initiative([])])
    assert cell(html, "c-act") == '<span class="muted">none</span>'


# --- row: four cells, resume on top, doc chips under it ---

def finished_tree(initiative: Initiative) -> Initiative:
    """Finish the forge (the spec already is) so only ticket items show."""
    initiative.tree[0].state = "finished"
    for item in initiative.tree:
        if item.kind == "epic":
            item.state = "finished"
    return initiative


def row_html(html: str) -> str:
    """One initiative row's markup, without its tree panel."""
    return html.split('class="init')[1].split('<div class="panel"')[0]


def done_epic(name: str = "epic-a") -> TreeItem:
    done = Ticket("1.1", "Done", "done", None, None, False)
    return TreeItem("epic", name, f"initiative-a/{name}/{name}.md", None, "finished",
                    tickets=[done], word="done")


def plain(**kwargs) -> Initiative:
    """A worktree, tickets loaded, one epic done and nothing else open."""
    kwargs.setdefault("tickets", [])
    kwargs.setdefault("tree", [done_epic()])
    return Initiative("initiative-a", "A", [Worktree(WT, "main")], **kwargs)


def test_row_has_four_cells_then_the_panel():
    html = render([tree_initiative()])
    row = html.split('<div class="init" role="row">')[1]
    order = [row.index(f'class="{c}"') for c in ("c-title", "c-proj", "c-act", "c-btns", "panel")]
    assert order == sorted(order)
    assert row_html(html).count('role="cell"') == 4
    assert "c-status" not in html


def test_row_shows_no_counts_or_status_words():
    initiative = tree_initiative([{"state": "done"}, {"state": "in-progress"}])
    initiative.top_tickets = [Ticket("9", "Top", "planned", None, None, False)]
    row = row_html(render([initiative]))
    assert not re.search(r"\d+/\d+ done|\+\d", row)
    for word in ("done", "dropped", "in progress", "planned", "built", "backlog", "stale"):
        assert word not in row


def test_finished_row_shows_no_done_or_dropped_word_and_no_close():
    for status in ("done", "dropped"):
        row = row_html(render([plain(status=status)]))
        assert status not in row
        assert ">close<" not in row
        assert ">resume<" in row


def test_finished_row_shows_no_doc_actions():
    forge = TreeItem("forge", "forge-c", "initiative-a/forge-c/forge-c.md", NOW, "open")
    row = row_html(render([plain(status="done", tree=[forge])]))
    assert ">resume forge<" not in row
    assert ">resume<" in row


def test_plain_row_has_resume_on_top_and_close_under_it():
    row = row_html(render([plain()]))
    actions = cell(row, "c-btns")
    assert actions.startswith('<a class="btn btn-1 chip"')
    assert actions.index(">resume<") < actions.index(">close<")


def test_any_open_work_hides_close():
    open_ticket = Ticket("1.2", "Open", "in progress", None, None, False)
    cases = [
        plain(top_tickets=[open_ticket]),
        plain(tree=[TreeItem("epic", "epic-b", None, None, "open", tickets=[open_ticket])]),
        plain(tree=[TreeItem("epic", "epic-c", None, None, "finished", tickets=[open_ticket])]),
        plain(tree=[TreeItem("epic", "epic-d", None, None, "open")]),
        plain(tree=[TreeItem("forge", "forge-a", "initiative-a/forge-a.md", None, "open")]),
    ]
    for initiative in cases:
        assert ">close<" not in row_html(render([initiative]))


def test_failed_tickets_keep_the_row_and_its_panel_with_the_failure_first():
    html = render([plain(tickets=None, tree=[])])
    row = row_html(html)
    assert ">close<" not in row and ">resume<" in row
    assert 'class="toggle"' in html
    panel = html.split('<div class="panel" role="cell"')[1]
    assert (panel.index("Couldn't read tickets: tickets.py failed")
            < panel.index('role="table"'))


def test_no_worktree_has_an_empty_actions_cell():
    html = render([Initiative("initiative-a", "A", [], tickets=[], tree=[done_epic()])])
    assert '<div class="c-btns" role="cell"></div>' in html
    assert "no worktree" in row_html(html)


def test_stale_activity_shows_stale_time_and_no_tag_on_the_row():
    activity = Activity(NOW - timedelta(days=14), "", "memlog")
    html = render([signals_initiative([{"state": "review"}], activity)])
    assert '<span class="when is-stale"' in html
    assert "stale-tag" not in row_html(html)


def test_fresh_activity_is_not_stale():
    activity = Activity(NOW - timedelta(days=13), "", "memlog")
    html = render([signals_initiative([], activity)])
    assert '<span class="when"' in html
    assert "is-stale" not in row_html(html)


def test_failed_tickets_keep_the_rest_of_the_row():
    activity = Activity(NOW - timedelta(hours=3), "did", "memlog")
    html = render([signals_initiative(None, activity)])
    row = row_html(html)
    assert ">3 h ago<" in row and "no worktree" in row
    assert "Couldn't read" not in row
    assert '<span class="when is-stale"' not in html


# --- row actions: doc chips stacked under resume ---

def spec_initiative(tickets=()) -> Initiative:
    spec = TreeItem("spec", "spec-x", "initiative-a/spec-x/spec-x.md", NOW, "open")
    return Initiative("initiative-a", "A", [], tickets=list(tickets), tree=[spec])


def test_open_spec_and_forge_chips_stack_under_resume():
    spec = TreeItem("spec", "spec-x", "initiative-a/spec-x/spec-x.md", NOW, "open")
    forge = TreeItem("forge", "forge-a", "initiative-a/forge-a/forge-a.md", NOW, "open")
    initiative = Initiative("initiative-a", "A", [Worktree(WT, "main")],
                            tickets=[], tree=[forge, spec])
    row = row_html(render([initiative]))
    assert cell(row, "c-btns").startswith('<a class="btn btn-1 chip"')
    assert row.index(">resume<") < row.index(">resume forge<") < row.index(">slice spec<")
    assert ">close<" not in row
    assert 'class="st-link chip"' in row
    slice_prompt = "/bmad-ticket Slice _bmad-output/initiative-a/spec-x/spec-x.md"
    assert "q=" + quote(slice_prompt, safe="") in row


def test_resume_forge_lists_every_open_forge_path():
    forges = [TreeItem("forge", f"forge-{n}", f"initiative-a/forge-{n}/forge-{n}.md", NOW, "open")
              for n in "ab"]
    forges.append(TreeItem("forge", "forge-c", "initiative-a/forge-c/forge-c.md", NOW, "finished"))
    initiative = Initiative("initiative-a", "A", [Worktree(WT, "main")], tickets=[], tree=forges)
    prompt = ("/bmad-forge-idea Resume _bmad-output/initiative-a/forge-a/forge-a.md, "
              "_bmad-output/initiative-a/forge-b/forge-b.md")
    row = row_html(render([initiative]))
    assert ">resume forge<" in row
    assert "q=" + quote(prompt, safe="") in row


def test_close_chip_names_the_initiative_and_its_label():
    row = row_html(render([plain()]))
    assert "q=" + quote("/bmad-ticket Close initiative-a, or plan more work", safe="") in row
    assert 'aria-label="close: close initiative-a or plan more work in a new session"' in row


def test_doc_chips_have_the_session_icon():
    actions = cell(row_html(render([plain()])), "c-btns")
    assert actions.count('<svg class="i" aria-hidden="true"') == 2  # resume and close


def test_no_worktree_shows_no_doc_actions_and_no_status_words():
    html = render([spec_initiative()])
    assert "claude://" not in html
    assert "spec to slice" not in html and "confirm close" not in html


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
    assert "c-status" not in rest
    assert '"Hide finished"' in html  # the script swaps the label


def test_finished_row_is_never_stale_and_ignores_tickets_failure():
    closed = at("closed", 24 * 30, "dropped")
    closed.tickets = None
    html = markup(render([closed]))
    assert "dropped" not in html
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
    assert 'href="/file/proj/initiative-a/story-top-plan.md"' in rows[-1]
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
    assert "Couldn't read tickets: tickets.py failed" in html
    assert initiative.top_tickets == []


def test_script_drops_vscode_failed_from_url():
    html = render([])
    assert "history.replaceState" in html
    assert 'params.has("vscode_failed")' in html


def test_script_keeps_finished_rows_closed_on_restore():
    assert 'closest(".is-finished")' in render([])


def test_finished_row_never_starts_open():
    closed = tree_initiative(slug="closed")
    closed.status = "done"
    closed.activity = Activity(NOW, "", "plan")
    html = render([closed])
    assert 'class="toggle" aria-expanded="true"' not in markup(html)


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


# --- actions cell ---

WT = Path("/x/my wt")


def test_resume_link_opens_claude_in_worktree_with_prompt():
    html = render([Initiative("initiative-a", "A", [Worktree(WT, "main")])])
    actions = cell(html, "c-btns")
    href = ("claude://code/new?folder=%2Fx%2Fmy%20wt"
            "&amp;q=%2Fbmad%20Resume%20work%20on%20initiative-a")
    assert f'<a class="btn btn-1 chip" href="{href}"' in actions
    assert ">resume<" in actions


def test_vscode_form_posts_project_id_and_slug():
    html = render([Initiative("initiative-a", "A", [Worktree(WT, "main")])])
    form = slots(html)[0]
    assert form.startswith('<form class="vscode" method="post" action="/vscode">')
    assert f'<input type="hidden" name="project" value="{PROJECT}">' in form
    assert f'name="id" value="{worktree_id(WT)}"' in form
    assert 'name="slug" value="initiative-a"' in form
    assert form.endswith("</svg></button></form>")


def test_no_separate_vscode_button():
    html = render([Initiative("initiative-a", "A", [Worktree(WT, "main")])])
    assert "VS Code</button>" not in html
    assert "<form" not in cell(html, "c-btns")


def test_no_actions_without_worktree():
    html = render([Initiative("initiative-a", "A", [])])
    assert cell(html, "c-btns") == ""
    assert "no worktree" in cell(html, "c-proj")


def test_slug_in_actions_and_form_is_escaped():
    html = render([Initiative('i-<b>"', "A", [Worktree(WT, "main")])])
    actions = cell(html, "c-btns")
    assert "<b>" not in actions
    assert "i-%3Cb%3E%22" in actions
    assert 'value="i-&lt;b&gt;&quot;"' in cell(html, "c-proj")


def test_failure_message_on_flagged_row_only():
    initiatives = [
        Initiative("initiative-a", "A", [Worktree(WT, "main")]),
        Initiative("initiative-b", "B", [Worktree(Path("/x/b"), "dev")]),
    ]
    html = render_page(initiatives, [Path("/root")], now=NOW, vscode_failed="proj/initiative-b")
    assert html.count("Couldn't open VS Code") == 1
    projects = re.findall(r'<div class="c-proj" role="cell">(.*?)</div>', html)
    assert "Couldn't open VS Code" not in projects[0]
    # Under the slots, in the Project cell.
    assert projects[1].endswith('</span></span><span class="err">Couldn\'t open VS Code</span>')


def test_failure_message_shows_even_without_worktree():
    # The worktree vanished between render and click.
    initiatives = [Initiative("initiative-a", "A", [])]
    html = render_page(initiatives, [Path("/root")], now=NOW, vscode_failed="proj/initiative-a")
    assert cell(html, "c-proj") == (
        f'<span class="proj">{PROJECT}</span>'
        '<span class="muted no-wt">no worktree</span>'
        '<span class="err">Couldn\'t open VS Code</span>'
    )
    assert cell(html, "c-btns") == ""


def test_unknown_flag_shows_no_message():
    initiatives = [Initiative("initiative-a", "A", [Worktree(WT, "main")])]
    html = render_page(initiatives, [Path("/root")], now=NOW, vscode_failed="nope")
    assert "Couldn't open VS Code" not in html


def test_exactly_one_script():
    html = render([Initiative("initiative-a", "A", [Worktree(WT, "main")])])
    assert html.count("<script") == 1


def test_now_defaults_to_current_time():
    initiative = signals_initiative([], Activity(datetime.now(), "", "plan"))
    html = render_page([initiative], [Path("/root")])
    assert ">just now<" in html


# --- PR button ---

def pr(number, head, base="main", state="open"):
    return PullRequest(number, head, base, state, f"https://github.com/o/r/pull/{number}")


def with_prs(prs, branch="b", worktrees=None):
    """Render one row, proj/initiative-a. `prs` maps `<project>/<slug>` -> PR,
    or None (GitHub failed: the header line and no PRs)."""
    if worktrees is None:
        worktrees = [Worktree(WT, branch)]
    initiative = Initiative("initiative-a", "A", worktrees)
    if prs is None:
        return render_page([initiative], [Path("/root")], now=NOW, github_failed=True)
    return render_page([initiative], [Path("/root")], now=NOW, prs=prs)


def with_pr(pr_, **kwargs):
    return with_prs({"proj/initiative-a": pr_}, **kwargs)


def pr_slot(html: str) -> str:
    return slots(html)[3]


def test_pr_slot_opens_lone_pr_in_new_tab_with_icon_only():
    html = with_pr(pr(7, "b"))
    link = pr_slot(html)
    assert link == (
        '<a class="place pr" href="https://github.com/o/r/pull/7" target="_blank" rel="noopener"'
        ' title="Open PR #7, top of the stack, on GitHub"'
        ' aria-label="open pull request #7 (opens in new tab)">'
        '<svg class="i pr-open" aria-hidden="true" viewBox="0 0 16 16">'
        f'<path d="{PR_ICONS["open"]}"/></svg></a>'
    )
    assert "#7<" not in html


def test_no_pr_in_the_actions_cell():
    actions = cell(with_pr(pr(7, "b")), "c-btns")
    assert "github.com" not in actions and "pull request" not in actions


def test_pr_slot_shows_the_mapped_pr():
    # Which PR is the stack's top is stack.stack_prs's job; the page shows it.
    assert 'href="https://github.com/o/r/pull/9"' in pr_slot(with_pr(pr(9, "d", base="c")))


def test_merged_closed_and_draft_tops_are_muted():
    for state in ("merged", "closed", "draft"):
        link = pr_slot(with_pr(pr(7, "b", state=state)))
        assert '<svg class="i pr-muted"' in link
        assert f'title="{state.capitalize()} PR #7, top of the stack, on GitHub"' in link
        assert f'aria-label="{state} pull request #7 (opens in new tab)"' in link


def test_unknown_state_shows_the_open_icon():
    link = pr_slot(with_pr(pr(7, "b", state="weird")))
    assert '<svg class="i pr-open"' in link
    assert 'aria-label="open pull request #7 (opens in new tab)"' in link


def test_each_state_has_its_own_icon():
    paths = set()
    for state in ("open", "draft", "merged", "closed"):
        link = pr_slot(with_pr(pr(7, "b", state=state)))
        paths.add(re.search(r'<path d="([^"]+)"', link).group(1))
    assert len(paths) == 4


def test_no_pr_slot_without_a_mapped_pr_or_worktree():
    assert 'class="place pr"' not in with_prs({})
    assert 'class="place pr"' not in with_prs({"other-slug": pr(7, "b")})
    assert 'class="place pr"' not in with_pr(pr(7, "b"), worktrees=[])


def test_pr_url_is_escaped():
    evil = PullRequest(7, "b", "main", "open", 'https://x/"><script>')
    html = with_pr(evil)
    assert '"><script>' not in html
    assert 'href="https://x/&quot;&gt;&lt;script&gt;"' in html


def test_github_failure_shows_header_line_and_no_pr_buttons():
    html = with_prs(None)
    header = html.split('<header class="hdr">')[1].split("</header>")[0]
    assert header.endswith('<span class="err">Couldn\'t reach GitHub</span>')
    assert 'class="place pr"' not in html
    assert ">resume<" in html  # rest of the page unchanged


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


ICON = '<svg class="i" aria-hidden="true" viewBox="0 0 16 16"><path d="M3 3.5h10a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H7l-3 2.5v-2.5H3a1 1 0 0 1-1-1v-6a1 1 0 0 1 1-1z"/><path d="m5.5 6.5 1.5 1.25-1.5 1.25M8.5 9h2"/></svg>'


def test_ready_ticket_shows_start_link():
    html = ticket_action_html("planned", ready=True)
    assert html == (
        f'<span role="cell"><a class="st-link chip" href="claude://code/new?{FOLDER}'
        '&amp;q=%2Fbmad-build%204.2%20Ticket%20actions" '
        'aria-label="start 4.2 Ticket actions">start' + ICON + '</a></span>')
    assert ">start<svg" in ticket_action_html("backlog", ready=True)


def test_not_ready_planned_is_a_status():
    assert ticket_action_html("planned") == '<span role="cell" class="st-muted">planned</span>'


def test_in_progress_and_in_review_show_resume():
    for word in ("in progress", "in review"):
        html = ticket_action_html(word)
        assert ('q=%2Fbmad-build%20Resume%204.2%20Ticket%20actions" '
                'aria-label="resume 4.2 Ticket actions">resume<svg') in html
        assert 'class="st-link chip"' in html


def test_blocked_shows_warm_unblock_with_reason():
    html = ticket_action_html("blocked", blocked_reason="needs x")
    assert 'class="st-warm chip"' in html and ">unblock<svg" in html
    assert 'aria-label="unblock 4.2 Ticket actions"' in html
    assert "Resume%204.2%20Ticket%20actions%3A%20blocked%3A%20needs%20x" in html
    bare = ticket_action_html("blocked")
    assert "Actions%22" not in bare and "blocked%3A" not in bare


def test_no_state_shows_fix():
    html = ticket_action_html("no state")
    assert ">fix<svg" in html and 'class="st-link chip"' in html
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

def epic_item(word, name="epic-x", retro_word=None, tickets=(), state="open", current=True):
    children = []
    if retro_word is not None:
        children.append(TreeItem("retro", f"{name}-retrospective",
                                 f"initiative-a/{name}/{name}-retrospective.md",
                                 None, "finished", word=retro_word, current=current))
    return TreeItem("epic", name, f"initiative-a/{name}/{name}.md", None, state,
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
        f'<span role="cell"><a class="st-link chip" href="claude://code/new?{FOLDER}'
        '&amp;q=%2Fbmad-ticket%20Close%20epic-dashboard" '
        'aria-label="close: close epic-dashboard in a new session">close' + ICON + '</a></span>')


def test_not_started_epic_has_start_and_auto():
    html = item_cells(epic_item("not started", "epic-several-projects"))[0]
    assert ">start<svg" in html and ">auto<svg" in html
    assert 'class="sep">·</span>' in html
    start, auto = hrefs(html)
    assert start == "/bmad Start work on epic-several-projects"
    assert auto.startswith("/loop Build epic-several-projects unattended")
    assert auto.endswith("Stop for no other reason.")


def test_in_progress_epic_has_resume_and_auto_and_addresses_rejected_retro():
    html = item_cells(epic_item("in progress"))[0]
    assert ">resume<svg" in html and ">auto<svg" in html
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
    assert ">plan items<svg" in html and 'class="st-link chip"' in html


def test_retro_rejected_shows_fix_with_the_resume_prompt():
    html = item_cells(epic_item("in progress", retro_word="rejected"))[1]
    assert ">fix<svg" in html
    assert hrefs(html) == [
        "/bmad Resume work on epic-x: address "
        "_bmad-output/initiative-a/epic-x/epic-x-retrospective.md"]


def test_retro_accepted_or_empty_is_a_status():
    assert item_cells(epic_item("to close", retro_word="accepted"))[1] == (
        '<span role="cell" class="st-muted">accepted</span>')
    assert item_cells(epic_item("to close", retro_word=""))[1] == '<span role="cell"></span>'


def test_finished_epics_retro_is_a_grey_status():
    for word in ["accepted", "rejected"]:
        html = item_cells(epic_item("done", retro_word=word, state="finished"))[1]
        assert html == f'<span role="cell" class="st-muted">{word}</span>'


def test_finished_epics_retro_open_items_reads_accepted_in_the_cell():
    for state in ("done", "dropped"):
        html = item_cells(epic_item(state, retro_word="open items", state="finished"))[1]
        assert html == '<span role="cell" class="st-muted">accepted</span>'


def test_retro_word_open_items_is_accepted_only_under_a_finished_epic():
    done = epic_item("done", retro_word="open items", state="finished")
    open_epic = epic_item("in progress", retro_word="open items")
    assert retro_word(done.children[0], done) == "accepted"
    assert retro_word(open_epic.children[0], open_epic) == "open items"
    rejected = epic_item("done", retro_word="rejected", state="finished")
    assert retro_word(rejected.children[0], rejected) == "rejected"


def test_item_actions_by_kind():
    epic = epic_item("to retro", retro_word="open items")
    retro = epic.children[0]
    assert item_actions(epic, None) == item_actions(epic, epic) != []
    assert [a[0] for a in item_actions(retro, epic)] == ["plan items"]
    assert item_actions(retro, None) == []
    doc = TreeItem("doc", "d", "p", None, "open")
    assert item_actions(doc, epic) == []


def test_action_css_unblock_is_warm_else_blue():
    assert action_css("unblock") == "st-warm"
    for action in ("start", "resume", "fix"):
        assert action_css(action) == "st-link"


def test_resume_prompt():
    assert resume_prompt("epic-x") == "/bmad Resume work on epic-x"


def test_no_epic_or_retro_link_without_worktree():
    for word in ("not started", "in progress", "to retro", "to close"):
        for html in item_cells(epic_item(word, retro_word="rejected"), worktrees=False):
            assert "<a " not in html
    assert item_cells(epic_item("to retro", retro_word="open items"), worktrees=False)[1] == (
        '<span role="cell" class="st-muted">open items</span>')


# --- closed tickets behind a button ---

def tk(ref: str, word: str) -> Ticket:
    return Ticket(ref, f"T{ref}", word, None, NOW, False)


def closed_initiative(tickets: list[Ticket], top: list[Ticket] | None = None) -> Initiative:
    epic = TreeItem("epic", "epic-a", "initiative-a/epic-a/epic-a.md", NOW, "open",
                    tickets=tickets)
    initiative = Initiative("initiative-a", "A", [], tickets=[], tree=[epic])
    initiative.top_tickets = top or []
    return initiative


def test_mixed_epic_hides_closed_tickets_behind_one_button():
    html = render([closed_initiative([tk("1.1", "done"), tk("1.2", "in progress"),
                                      tk("1.3", "dropped")])])
    assert 'class="fold"' not in html
    assert html.count('class="closed-toggle"') == 1
    assert ('<button type="button" class="closed-toggle" aria-expanded="false"'
            ' aria-controls="closed-proj/initiative-a-epic-a"') in html
    assert ">Show 2 closed tickets</button>" in html
    group = html.split('id="closed-proj/initiative-a-epic-a" hidden>')[1].split("</div></div>")[0]
    assert "T1.1" in group and "T1.3" in group and "T1.2" not in group
    # The open row comes first, the button row last.
    assert html.index("T1.2") < html.index("closed-proj/initiative-a-epic-a")
    assert html.index("closed-proj/initiative-a-epic-a") < html.index('class="closed-toggle"')


def test_closed_tickets_read_their_state_as_a_grey_status():
    html = render([closed_initiative([tk("1.1", "done"), tk("1.2", "dropped")])])
    done = [r for r in rows_of(html) if "T1.1" in r][0]
    dropped = [r for r in rows_of(html) if "T1.2" in r][0]
    assert '<span role="cell" class="st-muted">done</span>' in done
    assert '<span role="cell" class="st-muted">dropped</span>' in dropped


def test_one_closed_ticket_reads_singular():
    html = render([closed_initiative([tk("1.1", "done")])])
    assert ">Show 1 closed ticket</button>" in html
    assert 'data-hide="Hide closed tickets"' in html


def test_no_button_or_group_without_closed_tickets():
    html = render([closed_initiative([tk("1.1", "in progress"), tk("1.2", "backlog")])])
    assert 'class="closed-toggle"' not in html
    assert 'role="rowgroup"' not in html


def test_button_sits_under_a_lone_spec_after_the_last_ticket():
    initiative = closed_initiative([tk("1.1", "done"), tk("1.2", "planned")])
    spec = TreeItem("spec", "spec-a", None, NOW, "finished")
    initiative.tree[0].children = [spec, walkthrough()]
    rows = rows_of(render([initiative]))[1:]
    kinds = [(re.search(r'class="t-kind">(.*?)</span>', r) or [None, "button"])[1] for r in rows]
    assert kinds == ["epic", "spec", "1.2", "1.1", "button", "walk"]
    assert 'class="t-name d2"><button' in rows[4]


def test_initiative_level_tickets_get_their_own_button():
    initiative = closed_initiative([tk("1.1", "done")], top=[tk("9", "done"), tk("10", "planned")])
    html = render([initiative])
    assert html.count('class="closed-toggle"') == 2
    assert 'id="closed-proj/initiative-a" hidden>' in html
    assert 'aria-controls="closed-proj/initiative-a"' in html


def test_closed_buttons_are_not_remembered_and_epics_have_no_fold_script():
    html = render([closed_initiative([tk("1.1", "done")])])
    button = html.split('class="closed-toggle"')[1].split(">")[0]
    assert "data-key" not in button
    assert 'querySelectorAll("button.toggle")' in html
    assert 'querySelectorAll("button.closed-toggle")' in html
    assert 'button.fold' not in html


# --- rules agree: stale retros, dropped epics, close needs finished epics ---

def test_stale_rejected_retro_is_grey_and_resume_has_no_address_suffix():
    item = epic_item("in progress", retro_word="rejected", current=False)
    assert item_cells(item)[1] == '<span role="cell" class="st-muted">rejected</span>'
    assert hrefs(item_cells(item)[0])[0] == "/bmad Resume work on epic-x"


def test_stale_open_items_retro_is_grey_without_plan_items():
    item = epic_item("in progress", retro_word="open items", current=False)
    assert item_cells(item)[1] == '<span role="cell" class="st-muted">open items</span>'


def test_dropped_epic_with_open_tickets_is_grey_and_so_is_its_retro():
    open_ticket = Ticket("1.1", "T", "in progress", None, None, False)
    assert item_cells(epic_item("dropped", tickets=[open_ticket]))[0] == (
        '<span role="cell" class="st-muted">dropped</span>')
    for word in ("rejected", "open items"):
        item = epic_item("dropped", retro_word=word, tickets=[open_ticket])
        assert item_cells(item)[1] == f'<span role="cell" class="st-muted">{word}</span>'


def test_open_epic_blocks_initiative_close():
    initiative = finished_tree(signals_initiative([{"state": "done"}]))
    initiative.tree[1].state = "open"
    assert "close" not in [a[0] for a in doc_actions(initiative)]


def test_finished_epics_allow_initiative_close():
    initiative = finished_tree(signals_initiative([{"state": "done"}]))
    for item in initiative.tree:  # every ticket of the epic done, nothing open
        if item.kind == "epic":
            item.tickets = [tk("1.1", "done")]
    assert [a[0] for a in doc_actions(initiative)] == ["close"]


def test_slug_line_is_the_slug_alone_and_the_project_has_its_cell():
    initiative = Initiative("foo", "Foo", [], project="owid-grapher")
    html = render_page_for([initiative], [Path("/x/owid-grapher")], now=NOW)
    assert '<span class="slug">foo</span>' in html
    assert cell(html, "c-proj").startswith('<span class="proj">owid-grapher</span>')


def test_closed_group_ids_differ_between_projects():
    def group_id(project):
        initiative = closed_initiative([tk("1.1", "done")])
        initiative.project = project
        html = render_page_for([initiative], [Path("/x")], now=NOW)
        return re.search(r'aria-controls="(closed-[^"]*)"', html).group(1)

    assert group_id("one") != group_id("two")


def test_vscode_form_names_the_project():
    initiative = Initiative("foo", "Foo", [Worktree(Path("/x"), "main")], project="owid-grapher")
    html = render_page_for([initiative], [Path("/x")], now=NOW)
    assert '<input type="hidden" name="project" value="owid-grapher">' in html
