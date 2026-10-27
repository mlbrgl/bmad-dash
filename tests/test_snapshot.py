"""The whole dashboard page, compared byte for byte with a golden file.

Refresh the golden file only when the output changes on purpose:
`UPDATE_SNAPSHOT=1 uv run pytest tests/test_snapshot.py`, then commit it.
"""

import os
from datetime import datetime, timedelta
from pathlib import Path

from bmad_dash.initiatives import Initiative, Worktree
from bmad_dash.page import render_page
from bmad_dash.prs import PullRequest
from bmad_dash.signals import Activity
from bmad_dash.tree import FINISHED, OPEN, STALE, Ticket, TreeItem

GOLDEN = Path(__file__).parent / "snapshots" / "page.html"
NOW = datetime(2026, 10, 21, 12, 0)
ROOT = Path("/tmp/snapshot-root")


def ago(**kwargs) -> datetime:
    return NOW - timedelta(**kwargs)


def busy_tree() -> list[TreeItem]:
    """A forge, a spec, a walkthrough, an epic with 2 tickets and a done epic."""
    tickets = [
        Ticket("1.1", "First ticket", "done", "initiative-busy/epic-one/story-1-plan.md",
               ago(days=3), False),
        Ticket("1.2", "Second <ticket>", "in progress", None, ago(days=20), True,
               entry=("initiative-busy/epic-one/tickets.toml", "2")),
    ]
    return [
        TreeItem("forge", "forge-idea", "initiative-busy/forge-idea/forge-idea.md",
                 ago(days=15), STALE,
                 report="initiative-busy/forge-idea/forge-report.html"),
        TreeItem("spec", "spec-busy", "initiative-busy/spec-busy/spec-busy.md",
                 ago(hours=5), OPEN),
        TreeItem("walk", "walkthrough-one",
                 "initiative-busy/walkthrough-one/walkthrough-one.html",
                 ago(minutes=30), OPEN,
                 report="initiative-busy/walkthrough-one/walkthrough-one.md",
                 report_label="md"),
        TreeItem("epic", "epic-one", "initiative-busy/epic-one/epic-one.md",
                 ago(days=1), OPEN, tickets=tickets, word="in progress"),
        TreeItem("epic", "epic-zero", "initiative-busy/epic-zero/epic-zero.md",
                 ago(days=9), FINISHED,
                 tickets=[Ticket("0.1", "Early ticket", "done", None, ago(days=10), False)],
                 word="done"),
    ]


def initiatives() -> list[Initiative]:
    states = ["review", "in-progress", "in-progress", "planned", "backlog", "done", "dropped",
              "planned"]  # the last one is the top-level ticket
    return [
        Initiative(
            slug="initiative-busy",
            project="snapshot-root",
            title="Busy & <one>",
            worktrees=[Worktree(Path("/tmp/snapshot-root/wt busy"), "feature-busy")],
            tickets=[{"state": s} for s in states],
            tree=busy_tree(),
            top_tickets=[Ticket("7", "Top-level ticket", "planned",
                                "initiative-busy/story-top-plan.md", ago(days=2), False)],
            activity=Activity(ago(minutes=12), "Wrote the plan", "memlog",
                              "initiative-busy/forge-idea/.memlog.md"),
            status="active",
        ),
        Initiative(
            slug="initiative-stale",
            project="snapshot-root",
            title="Stale one",
            worktrees=[Worktree(Path("/tmp/snapshot-root/wt-stale"), "feature-stale")],
            tickets=[],
            activity=Activity(ago(days=30), "feat: a commit subject", "commit"),
        ),
        Initiative(
            slug="initiative-quiet",
            project="snapshot-root",
            title="Quiet one",
            tickets=None,
        ),
        Initiative(
            slug="initiative-closed",
            project="snapshot-root",
            title="Closed one",
            tickets=[{"state": "done"}, {"state": "dropped"}],
            # The forge is STALE, but a finished initiative's tree shows no stale tag.
            tree=[TreeItem("forge", "forge-closed", None, ago(days=40), STALE),
                  TreeItem("spec", "spec-closed", None, ago(days=40), FINISHED)],
            activity=Activity(ago(days=2), "Closed it", "memlog"),
            status="done",
        ),
    ]


PRS = {"snapshot-root/initiative-busy": PullRequest(42, "feature-busy", "main", "draft", "https://github.com/o/r/pull/42")}


def test_page_matches_golden_file():
    html = render_page(initiatives(), [ROOT], NOW, vscode_failed="snapshot-root/initiative-stale", prs=PRS)
    if os.environ.get("UPDATE_SNAPSHOT") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(html, encoding="utf-8")
    assert html == GOLDEN.read_text(encoding="utf-8")


def test_empty_page_says_no_initiatives():
    html = render_page([], [ROOT], NOW)
    assert '<p class="empty">No initiatives in <code>/tmp/snapshot-root/_bmad-output/</code>.</p>' in html


def test_github_failure_shows_in_header():
    html = render_page(initiatives(), [ROOT], NOW, vscode_failed="snapshot-root/initiative-stale", github_failed=True)
    expected = GOLDEN.read_text(encoding="utf-8")
    # Same page, but the header says GitHub failed and no row has a PR icon.
    assert '<span class="err">Couldn\'t reach GitHub</span></header>' in html
    assert 'class="place pr"' not in html
    assert 'class="place pr"' in expected
