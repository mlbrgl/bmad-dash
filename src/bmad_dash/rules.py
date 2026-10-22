"""Status rules for a dashboard row, as plain data (no HTML).

Rules from `_bmad-output/initiative-bmad-dash/spec-bmad-dash/dashboard-status-rules.md`
(Row, Status sentence, Stack's last PR):

- Rows sort by newest last activity first, then those without activity.
- A row is stale when its last activity is `STALE_AFTER` old or older,
  unless the initiative is finished.
- The Status sentence lists waiting items: ticket counts by state, then
  `forge open` and `spec to slice`. At most `MAX_WAITING` show, then `+n`.
  With nothing waiting it says `CONFIRM_CLOSE`.
- The PR button shows the last PR of the stack starting at the row's branch.
"""

from collections.abc import Sequence
from datetime import datetime

from bmad_dash.initiatives import Initiative
from bmad_dash.prs import PullRequest, last_pr
from bmad_dash.tree import FINISHED, TreeItem, is_old, status_word, ticket_counts

# Not-done ticket states, in the order the Status sentence lists them.
WAITING_STATES = ["review", "in-progress", "planned", "backlog"]
MAX_WAITING = 2
# Shown when nothing is open but the initiative file isn't marked done.
CONFIRM_CLOSE = "confirm close or add work"


def is_stale(initiative: Initiative, now: datetime) -> bool:
    """Last activity is STALE_AFTER old or older. Finished initiatives never are."""
    activity = initiative.activity
    return (not initiative.finished and activity is not None
            and is_old(activity.when, now))


def has_open(items: list[TreeItem], kind: str) -> bool:
    """Any item of `kind`, at any depth, not finished (stale counts as open)."""
    return any(
        (item.kind == kind and item.state != FINISHED) or has_open(item.children, kind)
        for item in items
    )


def waiting_items(initiative: Initiative) -> list[str]:
    """Ticket counts by status word, then doc cues."""
    items = []
    for state in WAITING_STATES:
        count = sum(1 for t in initiative.tickets if t.get("state") == state)
        if count:
            items.append(f"{count} {status_word(state)}")
    if has_open(initiative.tree, "forge"):
        items.append("forge open")
    if has_open(initiative.tree, "spec"):
        items.append("spec to slice")
    return items


def status_parts(initiative: Initiative) -> tuple[list[str], tuple[int, int] | None]:
    """What the Status sentence says, as plain strings.

    Returns (waiting, done_count):
    - waiting: up to `MAX_WAITING` waiting items then `+n`, or `[CONFIRM_CLOSE]`.
      Only meaningful when tickets loaded; empty when they didn't.
    - done_count: (done, total), or None when there are no counted tickets.
    """
    waiting = []
    if initiative.tickets is not None:
        all_waiting = waiting_items(initiative) or [CONFIRM_CLOSE]
        waiting = all_waiting[:MAX_WAITING]
        hidden = len(all_waiting) - len(waiting)
        if hidden:
            waiting.append(f"+{hidden}")
    counts = ticket_counts(initiative.tickets)
    done_count = counts if counts and counts[1] > 0 else None
    return waiting, done_count


def by_activity(initiatives: list[Initiative]) -> list[Initiative]:
    """Newest last activity first, then those without activity.
    Sorting is stable, so ties keep their incoming (slug) order."""
    active = [i for i in initiatives if i.activity is not None]
    quiet = [i for i in initiatives if i.activity is None]
    return sorted(active, key=lambda i: i.activity.when, reverse=True) + quiet


def row_pr(initiative: Initiative, prs: Sequence[PullRequest] | None) -> PullRequest | None:
    """The last PR of the stack starting at the row's worktree branch, or None."""
    if not prs or not initiative.worktrees:
        return None
    branch = initiative.worktrees[0].branch
    if branch == "detached":
        return None
    return last_pr(list(prs), branch)
