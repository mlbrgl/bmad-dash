import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from conftest import STACK_TIMES, add_walkthrough, git, write_config, write_initiative

from bmad_dash.initiatives import (
    Worktree,
    active_initiative,
    list_initiatives,
    load_backlog,
    make_initiative,
    worktrees_by_slug,
)
from bmad_dash.page import render_page
from bmad_dash.stack import GraphiteStack
from bmad_dash.tree import OPEN, STALE


def by_slug(root: Path) -> dict:
    return {i.slug: i for i in list_initiatives(root)}


def test_lists_only_initiative_folders_sorted(project):
    slugs = [i.slug for i in list_initiatives(project)]
    assert slugs == [
        "initiative-matched",
        "initiative-nofile",
        "initiative-orphan",
        "initiative-shared",
        "initiative-untitled",
    ]


def test_single_match_shows_path_and_branch(project):
    worktrees = by_slug(project)["initiative-matched"].worktrees
    assert worktrees == [Worktree(project.parent / "worktrees" / "wt-a", "feature-a")]


def test_several_matches_keep_only_the_first_listed(project):
    # Main checkout and wt-b both claim it; git lists the main checkout first.
    worktrees = by_slug(project)["initiative-shared"].worktrees
    assert worktrees == [Worktree(project, "main")]


def test_parent_wins_over_its_child_worktrees(tmp_path):
    # Parallel auto's children are named `<parent folder>-<ref>`; whichever
    # order git lists them in, the parent keeps the row.
    parent = tmp_path / "x"
    children = [tmp_path / "x-3", tmp_path / "x-12"]
    for path in (parent, *children):
        write_config(path, '[core]\nactive_initiative = "initiative-a"\n')
    worktrees = [Worktree(children[0], "worktree-x-3"), Worktree(parent, "feature"),
                 Worktree(children[1], "worktree-x-12")]
    assert worktrees_by_slug(tmp_path, worktrees) == {"initiative-a": worktrees[1]}
    # Unrelated names still go by git's order.
    other = tmp_path / "y"
    write_config(other, '[core]\nactive_initiative = "initiative-a"\n')
    assert worktrees_by_slug(tmp_path, [Worktree(other, "y"), worktrees[1]]) == {
        "initiative-a": Worktree(other, "y")}


def test_no_match_has_no_worktrees(project):
    assert by_slug(project)["initiative-orphan"].worktrees == []


def test_bad_or_missing_configs_are_ignored(project):
    worktrees = project.parent / "worktrees"
    assert active_initiative(worktrees / "wt-c") is None
    assert active_initiative(worktrees / "wt-d") is None
    assert active_initiative(worktrees / "wt-e") is None


def test_titles_fall_back_to_folder_name(project):
    initiatives = by_slug(project)
    assert initiatives["initiative-matched"].title == "Matched one"
    assert initiatives["initiative-shared"].title == "Shared <b>&</b> one"
    assert initiatives["initiative-untitled"].title == "initiative-untitled"
    assert initiatives["initiative-nofile"].title == "initiative-nofile"


def test_status_read_from_initiative_frontmatter(project):
    write_initiative(project, "initiative-closed", "---\ntitle: C\nstatus: done\n---\n")
    write_initiative(project, "initiative-gone", "---\nstatus: 'dropped'\n---\n")
    initiatives = by_slug(project)
    assert initiatives["initiative-closed"].finished
    assert initiatives["initiative-gone"].status == "dropped"
    assert initiatives["initiative-gone"].finished
    assert initiatives["initiative-matched"].status == ""
    assert not initiatives["initiative-nofile"].finished


def test_worktree_commit_makes_older_walkthrough_stale(project):
    # The worktree's commit is from now; the walkthrough was touched 2 days ago.
    folder = project / "_bmad-output" / "initiative-matched" / "walkthrough-a"
    folder.mkdir()
    (folder / "walkthrough-a.md").write_text("# W\n")
    stamp = (datetime.now() - timedelta(days=2)).timestamp()
    os.utime(folder / "walkthrough-a.md", (stamp, stamp))

    initiatives = by_slug(project)
    matched = initiatives["initiative-matched"]
    assert matched.tree[0].state == STALE
    assert matched.activity.source == "commit"


def test_no_worktree_walkthrough_uses_age_only(project):
    folder = project / "_bmad-output" / "initiative-orphan" / "walkthrough-a"
    folder.mkdir()
    (folder / "walkthrough-a.md").write_text("# W\n")
    stamp = (datetime.now() - timedelta(days=2)).timestamp()
    os.utime(folder / "walkthrough-a.md", (stamp, stamp))

    assert by_slug(project)["initiative-orphan"].tree[0].state == OPEN


def test_initiative_level_ticket_shows_in_tree_and_counts(project):
    # A fake tickets.py that lists one ticket sitting in the initiative folder.
    data = {"tickets": [{"epic": "initiative-orphan", "ref": "1", "title": "Top one",
                         "state": "planned", "id": 1}]}
    script = project / "_bmad" / "method" / "scripts" / "tickets.py"
    script.parent.mkdir(parents=True)
    script.write_text(f"print({json.dumps(json.dumps(data))})\n")

    orphan = by_slug(project)["initiative-orphan"]
    assert orphan.tree == []
    assert [t.title for t in orphan.top_tickets] == ["Top one"]

    html = render_page([orphan], [project])
    assert "Top one" in html
    assert 'class="toggle"' in html  # only a top ticket, still a caret


# --- last commit from the Graphite stack ---

# feature-a -> b -> c, as built by `stacked_project`.
CHAIN = GraphiteStack({"main": ["feature-a"], "feature-a": ["b"], "b": ["c"]}, {"main"})


def walkthrough_state(initiative) -> str:
    return next(item.state for item in initiative.tree if item.name == "walkthrough-x")


def test_stack_bottom_uses_the_newest_tip(stacked_project):
    # Touched after feature-a's commit, before c's.
    add_walkthrough(stacked_project, STACK_TIMES["feature-a"] + timedelta(hours=1))
    matched = {i.slug: i for i in list_initiatives(stacked_project, stack=CHAIN)}["initiative-matched"]
    assert (matched.activity.text, matched.activity.when) == ("c work", STACK_TIMES["c"])
    assert walkthrough_state(matched) == STALE


def test_without_stack_uses_the_worktree_head(stacked_project):
    add_walkthrough(stacked_project, STACK_TIMES["feature-a"] + timedelta(hours=1))
    matched = by_slug(stacked_project)["initiative-matched"]
    assert matched.activity.text == "feature-a work"
    assert walkthrough_state(matched) == OPEN


def make_matched(project: Path, worktree: Worktree, stack: GraphiteStack):
    folder = project / "_bmad-output" / "initiative-matched"
    return make_initiative(project, folder, [worktree], datetime.now(), stack)


def test_middle_of_stack_uses_newest_above(stacked_project):
    wt = stacked_project.parent / "worktrees" / "wt-a"
    assert make_matched(stacked_project, Worktree(wt, "b"), CHAIN).activity.text == "c work"


def test_fork_uses_branches_up_to_the_fork(stacked_project):
    fork = GraphiteStack({"main": ["feature-a"], "feature-a": ["b", "x"], "b": ["c"]}, {"main"})
    wt = stacked_project.parent / "worktrees" / "wt-a"
    assert make_matched(stacked_project, Worktree(wt, "feature-a"), fork).activity.text == "feature-a work"


def test_missing_local_branch_is_skipped(stacked_project):
    stack = GraphiteStack({"main": ["feature-a"], "feature-a": ["b"], "b": ["gone"]}, {"main"})
    wt = stacked_project.parent / "worktrees" / "wt-a"
    assert make_matched(stacked_project, Worktree(wt, "feature-a"), stack).activity.text == "b work"


def test_claimed_branch_stops_the_last_commit(stacked_project):
    # Another initiative's worktree has b: feature-a's stack is feature-a alone.
    wt = stacked_project.parent / "worktrees" / "wt-a"
    folder = stacked_project / "_bmad-output" / "initiative-matched"
    matched = make_initiative(stacked_project, folder, [Worktree(wt, "feature-a")],
                              datetime.now(), CHAIN, {"feature-a", "b"})
    assert matched.activity.text == "feature-a work"


def test_list_initiatives_passes_claimed_branches(stacked_project):
    # A worktree on b claims initiative-shared-b: initiative-matched stops at feature-a.
    root = stacked_project
    wt_b = root.parent / "worktrees" / "wt-on-b"
    git(root, "worktree", "add", str(wt_b), "b")
    write_config(wt_b, '[core]\nactive_initiative = "initiative-on-b"\n')
    write_initiative(root, "initiative-on-b", "---\ntitle: B\n---\n")
    found = {i.slug: i for i in list_initiatives(root, stack=CHAIN)}
    assert found["initiative-matched"].activity.text == "feature-a work"
    assert found["initiative-on-b"].activity.text == "c work"


def test_trunk_keeps_its_own_commit(stacked_project):
    # The main checkout is on main; c is newer but not main's.
    shared = {i.slug: i for i in list_initiatives(stacked_project, stack=CHAIN)}["initiative-shared"]
    assert shared.activity.text == "init"


def test_detached_keeps_its_own_commit(stacked_project):
    wt_b = stacked_project.parent / "worktrees" / "wt-b"
    assert make_matched(stacked_project, Worktree(wt_b, "detached"), CHAIN).activity.text == "init"


# --- backlog ---

def write_fake_backlog_tickets(project: Path, tickets: list[dict], refine: list[dict]) -> None:
    """A fake tickets.py: `status` lists `tickets`, `next` lists `refine` as ready to refine."""
    status = json.dumps({"tickets": tickets})
    next_ = json.dumps({"ready_to_start": [], "blocked": [], "ready_to_refine": refine})
    script = project / "_bmad" / "method" / "scripts" / "tickets.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "import sys\n"
        f"print({next_!r} if 'next' in sys.argv else {status!r})\n"
    )


BACKLOG_TICKET = {"epic": "backlog", "ref": "2", "id": 2, "title": "One-off",
                  "state": "backlog", "file": "story-one-off.md"}


def test_backlog_row_holds_its_tickets_and_opens_in_main_checkout(project):
    (project / "_bmad-output" / "backlog").mkdir()
    (project / "_bmad-output" / "backlog" / "story-one-off.md").write_text("# One-off\n")
    write_fake_backlog_tickets(project, [BACKLOG_TICKET], [{"ref": "2"}])

    backlog = load_backlog(project)
    assert (backlog.slug, backlog.title, backlog.key) == ("backlog", "Backlog", "project/backlog")
    assert backlog.backlog and backlog.tree == []
    assert backlog.worktrees == [Worktree(project, "main")]
    [ticket] = backlog.top_tickets
    assert (ticket.ref, ticket.path, ticket.refine) == ("2", "backlog/story-one-off.md", True)


def test_no_backlog_row_without_folder_or_tickets(project):
    write_fake_backlog_tickets(project, [], [])
    assert load_backlog(project) is None
    (project / "_bmad-output" / "backlog").mkdir()
    assert load_backlog(project) is None


def test_backlog_row_stays_when_tickets_fail(project):
    (project / "_bmad-output" / "backlog").mkdir()
    backlog = load_backlog(project)  # no tickets.py
    assert backlog.tickets is None


def test_backlog_is_not_an_initiative(project):
    (project / "_bmad-output" / "backlog").mkdir()
    write_fake_backlog_tickets(project, [BACKLOG_TICKET], [])
    assert "backlog" not in by_slug(project)
