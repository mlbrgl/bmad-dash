"""Status rules for a dashboard row, as plain data (no HTML).

Rules from `_bmad-output/initiative-bmad-dash/spec-bmad-dash/dashboard-status-rules.md`
(Row, Status sentence, Stack's last PR):

- Rows sort by newest last activity first, then those without activity.
- A row is stale when its last activity is `STALE_AFTER` old or older,
  unless the initiative is finished.
- The Status sentence lists waiting items: ticket counts by word (`ticket_word`,
  the tree's word), then `forge open` and `spec to slice`. At most
  `MAX_WAITING` show, then `+n`. Done and dropped are not counted. Words go
  in `WORD_ORDER` first (every `waits on ...` counts as `waiting`), then any
  other word in the order it first appears. With nothing waiting it says
  `CONFIRM_CLOSE`.
- Words have four colours (`word_class`): blue to act, green built, grey
  (the default) nothing to do, warm blocked. A count takes its word's colour.
- A finished initiative's tree shows no stale tags.
- The PR button shows the PR of the highest branch that has one, in the
  stack starting at the row's branch (Stack's last PR). With Graphite, the
  stack follows `gt state`'s tracked children (`stack_branches`); without it,
  the user's PRs (`row_pr`).
- A branch another initiative's worktree has checked out (`claimed`) is not
  a child: a->[b claimed] stops at a, a->[b claimed, c] continues to c. It is
  dropped before counting children, so it is a stop, not a fork.
- The initiative's last commit is the newest one on the local tips of the
  branches walked from the row's branch (`stack_tips`, up to a fork).
  Without a stack, detached, or on trunk, it is the worktree's own HEAD.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence, Set
from datetime import datetime
from typing import TYPE_CHECKING

from bmad_dash.prs import PullRequest, last_pr
from bmad_dash.tree import (
    FINISHED,
    Ticket,
    TreeItem,
    is_old,
    ticket_counts,
    ticket_word,
)

if TYPE_CHECKING:  # types only: initiatives.py imports this module
    from bmad_dash.initiatives import Initiative, Worktree
    from bmad_dash.stack import GraphiteStack

# Ticket words, in the order the Status sentence lists them.
WORD_ORDER = ["blocked", "built", "in review", "in progress", "planned", "backlog", "waiting"]
# CSS classes of the four word colours.
LINK = "st-link"  # blue: act
BUILT = "st-built"  # green
MUTED = "st-muted"  # grey: nothing to do
WARM = "st-warm"  # needs attention
WAITS_ON = "waits on "
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


# Words that read blue: ticket words with an action, and epic words (epics keep
# their look until they get their own actions).
LINK_WORDS = {"in progress", "in review", "no state", "not started", "to retro", "to close"}


def word_class(word: str, ready: bool = False) -> str:
    """The colour class of a status word. Grey is the default; blue for
    `LINK_WORDS`, and for `planned` / `backlog` when `ready` to start."""
    if word == "blocked":
        return WARM
    if word == "built":
        return BUILT
    if word in ("planned", "backlog"):
        return LINK if ready else MUTED
    if word in LINK_WORDS:
        return LINK
    return MUTED


def ticket_action(ticket: Ticket) -> str | None:
    """The verb a ticket's cell offers, or None when it only shows a status."""
    if ticket.word in ("planned", "backlog"):
        return "start" if ticket.ready else None
    if ticket.word in ("in progress", "in review"):
        return "resume"
    if ticket.word == "blocked":
        return "unblock"
    if ticket.word == "no state":
        return "fix"
    return None


def action_prompt(ticket: Ticket, action: str) -> str:
    """The prompt a ticket's action opens a session with."""
    name = f"{ticket.ref} {ticket.title}"
    if action == "start":
        return f"/bmad-build {name}"
    if action == "fix":
        return f"/bmad-ticket Check {name}: it has no state"
    prompt = f"/bmad-build Resume {name}"
    if action == "unblock" and ticket.blocked_reason:
        prompt += f": blocked: {ticket.blocked_reason}"
    return prompt


def count_word(word: str) -> str:
    """The word a ticket is counted under: every `waits on ...` is `waiting`."""
    return "waiting" if word.startswith(WAITS_ON) else word


def waiting_items(initiative: Initiative) -> list[tuple[str, str]]:
    """Ticket counts by word, then doc cues, as (text, colour class)."""
    next_info = initiative.next_info
    counts: dict[str, int] = {}
    ready: set[str] = set()  # counted words with a ticket ready to start
    for ticket in initiative.tickets:
        word = ticket_word(ticket, next_info)
        if word in ("done", "dropped"):
            continue
        key = count_word(word)
        counts[key] = counts.get(key, 0) + 1
        if next_info is not None and ticket.get("ref") in next_info.ready:
            ready.add(key)
    others = [word for word in counts if word not in WORD_ORDER]
    items = [
        (f"{counts[word]} {word}", word_class(word, word in ready))
        for word in WORD_ORDER + others
        if word in counts
    ]
    if has_open(initiative.tree, "forge"):
        items.append(("forge open", LINK))
    if has_open(initiative.tree, "spec"):
        items.append(("spec to slice", LINK))
    return items


def status_parts(
    initiative: Initiative,
) -> tuple[list[tuple[str, str]], tuple[int, int] | None]:
    """What the Status sentence says, as (text, colour class) pairs.

    Returns (waiting, done_count):
    - waiting: up to `MAX_WAITING` waiting items then `+n`, or `[CONFIRM_CLOSE]`.
      Only meaningful when tickets loaded; empty when they didn't.
    - done_count: (done, total), or None when there are no counted tickets.
    """
    waiting = []
    if initiative.tickets is not None:
        all_waiting = waiting_items(initiative) or [(CONFIRM_CLOSE, LINK)]
        waiting = all_waiting[:MAX_WAITING]
        hidden = len(all_waiting) - len(waiting)
        if hidden:
            waiting.append((f"+{hidden}", LINK))
    counts = ticket_counts(initiative.tickets)
    done_count = counts if counts and counts[1] > 0 else None
    return waiting, done_count


def tree_shows_stale(initiative: Initiative) -> bool:
    """Stale tags show in the tree unless the initiative is finished.
    Items keep their STALE state either way; only the tag is hidden."""
    return not initiative.finished


def by_activity(initiatives: list[Initiative]) -> list[Initiative]:
    """Newest last activity first, then those without activity.
    Sorting is stable, so ties keep their incoming (slug) order."""
    active = [i for i in initiatives if i.activity is not None]
    quiet = [i for i in initiatives if i.activity is None]
    return sorted(active, key=lambda i: i.activity.when, reverse=True) + quiet


NO_CLAIMS: frozenset[str] = frozenset()


def claimed_branches(worktrees: Iterable[Worktree]) -> frozenset[str]:
    """The branches the initiatives' worktrees have checked out (not "detached").
    Pass only worktrees of initiatives whose folder exists."""
    return frozenset(w.branch for w in worktrees if w.branch != "detached")


def highest_pr(
    stack: Sequence[str], results: Mapping[str, Sequence[PullRequest]]
) -> PullRequest | None:
    """The PR of the highest branch in `stack` that has one, or None.
    `results` maps each branch to its PRs (newest first, possibly empty)."""
    return next((results[b][0] for b in reversed(stack) if results[b]), None)


def row_pr(
    initiative: Initiative,
    prs: Sequence[PullRequest] | None,
    claimed: Set[str] = NO_CLAIMS,
) -> PullRequest | None:
    """The last PR of the stack starting at the row's worktree branch, or None.
    PRs from `claimed` branches (other initiatives' worktrees) are not followed."""
    if not prs or not initiative.worktrees:
        return None
    branch = initiative.worktrees[0].branch
    if branch == "detached":
        return None
    return last_pr(list(prs), branch, claimed - {branch})


def free_children(
    children: Mapping[str, Sequence[str]], branch: str, claimed: Set[str]
) -> list[str]:
    """`branch`'s tracked children, without the `claimed` ones."""
    return [child for child in children.get(branch, []) if child not in claimed]


def walked_branches(
    children: Mapping[str, Sequence[str]], branch: str, claimed: Set[str] = NO_CLAIMS
) -> list[str]:
    """The branches walked from `branch` up: `[branch, ...]`, following the one
    child of each branch. Stops at the top, before a fork, or before a cycle.
    `children` maps a branch to its tracked children. `claimed` children are
    dropped first (a stop, not a fork); `branch` itself never counts as claimed."""
    claimed = claimed - {branch}
    walked = [branch]
    while True:
        next_branches = free_children(children, walked[-1], claimed)
        if len(next_branches) != 1:  # top (none) or fork (two or more)
            return walked
        if next_branches[0] in walked:  # cycle
            return walked
        walked.append(next_branches[0])


def stack_branches(
    children: Mapping[str, Sequence[str]], branch: str, claimed: Set[str] = NO_CLAIMS
) -> list[str] | None:
    """The stack from `branch` up: `[branch, ..., top]`, without `claimed` branches.
    None on a fork (two or more free children) or a cycle: the walk stopped early."""
    claimed = claimed - {branch}
    walked = walked_branches(children, branch, claimed)
    if free_children(children, walked[-1], claimed):  # children left: fork or cycle
        return None
    return walked


def stack_tips(
    stack: GraphiteStack | None, branch: str, claimed: Set[str] = NO_CLAIMS
) -> list[str] | None:
    """The branches whose newest commit is the initiative's last commit
    (Last activity, walkthrough staleness): the ones walked from `branch`,
    skipping `claimed` branches (other initiatives' worktrees).
    None means use the worktree's own HEAD: no stack, detached, or on trunk."""
    if stack is None or branch == "detached" or branch in stack.trunks:
        return None
    return walked_branches(stack.children, branch, claimed)
