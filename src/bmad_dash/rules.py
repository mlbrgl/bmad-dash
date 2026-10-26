"""Status rules for a dashboard row, as plain data (no HTML).

Rules from `_bmad-output/initiative-bmad-dash/spec-bmad-dash/dashboard-status-rules.md`
(Row, Stack's last PR):

- Rows sort by newest last activity first, then those without activity.
- A row is stale when its last activity is `STALE_AFTER` old or older,
  unless the initiative is finished.
- The row's Actions are its doc actions (`doc_actions`), stacked under
  `resume`: `resume forge` (any open forge), `slice spec` (any spec with no
  `tickets.toml` beside it), and `close`. `close` shows only when the
  initiative is not finished, its tickets loaded, no ticket open at any level
  (the tree or the initiative folder), and no epic open. Counts play no part.
  Without a worktree the row shows no actions.
- Statuses have three colours (`status_class`): green built, grey (the
  default) nothing to do, warm blocked. Actions are blue, warm for `unblock`
  (`action_css`). Tree cells only: the row has no status words.
- A retro's `open items` reads `accepted` under a finished epic (`retro_word`).
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
    CLOSED_STATES,
    FINISHED,
    Ticket,
    TreeItem,
    is_old,
)

if TYPE_CHECKING:  # types only: initiatives.py imports this module
    from bmad_dash.initiatives import Initiative, Worktree
    from bmad_dash.stack import GraphiteStack

# CSS classes of the four word colours.
LINK = "st-link"  # blue: act
BUILT = "st-built"  # green
MUTED = "st-muted"  # grey: nothing to do
WARM = "st-warm"  # needs attention


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


AUTO_PROMPT = (
    "/loop Build {epic} unattended, one fresh headless run per ticket. "
    "Each round: run tickets.py next. Stop and report if a ticket of this epic "
    "has plan status in-progress, or is halted (plan status blocked, or "
    "blocked_at set); built and in-review tickets count as finished, and a "
    "ticket only waiting on prerequisites is not halted. If the first ready "
    "ticket of this epic is hitl, stop. If one is ready, start "
    "claude -p \"/bmad-build-auto ticket <ref>\" in the background in this "
    "worktree; when it exits, check the ticket's plan status and its last "
    "commit, then schedule the next round. If none is ready, stop. "
    "Stop for no other reason."
)

# An action is (link text, prompt, aria-label).
Action = tuple[str, str, str]


def output_path(path: str) -> str:
    """A tree item's path as a prompt names it, relative to the worktree."""
    return f"_bmad-output/{path}"


def epic_retro(epic: TreeItem) -> TreeItem | None:
    """The epic's retro child, if any."""
    return next((c for c in epic.children if c.kind == "retro"), None)


def make_action(text: str, prompt: str, step: str) -> Action:
    return (text, prompt, f"{text}: {step} in a new session")


def resume_prompt(slug: str) -> str:
    """The prompt that resumes work on an epic or initiative."""
    return f"/bmad Resume work on {slug}"


def resume_action(epic: TreeItem) -> Action:
    """`resume`; after a current rejected retro it also says to address it."""
    retro = epic_retro(epic)
    prompt = resume_prompt(epic.name)
    step = f"resume work on {epic.name}"
    if retro is not None and retro.word == "rejected" and retro.current and retro.path:
        prompt += f": address {output_path(retro.path)}"
        step += " and address its rejected retro"
    return make_action("resume", prompt, step)


def epic_actions(epic: TreeItem) -> list[Action]:
    """The actions an epic's cell offers; [] means it shows a status."""
    slug = epic.name
    auto = make_action("auto", AUTO_PROMPT.format(epic=slug), f"build {slug} unattended")
    if epic.word == "not started":
        start = make_action("start", f"/bmad Start work on {slug}", f"start work on {slug}")
        return [start, auto]
    if epic.word == "in progress":
        return [resume_action(epic), auto]
    if epic.word == "to retro":
        return [make_action("retro", f"/bmad-retrospective {slug}",
                            f"run the retrospective for {slug}")]
    if epic.word == "to close":
        prompt = f"/bmad-ticket Close {slug}"
        retro = epic_retro(epic)
        if any(t.word == "built" for t in epic.tickets) and retro is not None and retro.path:
            prompt += f": mark its built tickets done, per {output_path(retro.path)}"
        return [make_action("close", prompt, f"close {slug}")]
    return []


def retro_actions(retro: TreeItem, epic: TreeItem) -> list[Action]:
    """The actions a retro's cell offers; [] means it shows a status.
    A finished or dropped epic's retro needs nothing, nor does a stale one."""
    if (not retro.path or epic.state == FINISHED or epic.word == "dropped"
            or not retro.current):
        return []
    if retro.word == "open items":
        prompt = f"/bmad-ticket Turn the action items in {output_path(retro.path)} into tickets"
        return [make_action("plan items", prompt,
                            f"turn the action items of {epic.name} into tickets")]
    if retro.word == "rejected":
        _, prompt, _ = resume_action(epic)
        return [make_action("fix", prompt, f"address the rejected retro of {epic.name}")]
    return []


def retro_word(retro: TreeItem, epic: TreeItem) -> str:
    """The word a retro's cell shows: `open items` reads `accepted` under a
    finished epic (closing it settled them), else the retro's own word."""
    if retro.word == "open items" and epic.state == FINISHED:
        return "accepted"
    return retro.word


def item_word(item: TreeItem, epic: TreeItem | None) -> str:
    """The word an epic's or retro's cell shows as a status."""
    if item.kind == "retro" and epic is not None:
        return retro_word(item, epic)
    return item.word


def action_css(action: str) -> str:
    """The colour of an action link: `unblock` warm, every other blue."""
    return WARM if action == "unblock" else LINK


def item_actions(item: TreeItem, epic: TreeItem | None) -> list[Action]:
    """The actions an epic's or retro's cell offers: an epic's own, a retro's
    (needs its epic), else none."""
    if item.kind == "epic":
        return epic_actions(item)
    if item.kind == "retro" and epic is not None:
        return retro_actions(item, epic)
    return []


def status_class(word: str) -> str:
    """The colour of a status: `blocked` warm, `built` green, else grey.
    A status is never blue."""
    if word == "blocked":
        return WARM
    if word == "built":
        return BUILT
    return MUTED


def has_open_ticket(items: list[TreeItem]) -> bool:
    """Any epic ticket at any depth that is not done or dropped."""
    return any(
        any(t.word not in CLOSED_STATES for t in item.tickets) or has_open_ticket(item.children)
        for item in items
    )


def can_close(initiative: Initiative) -> bool:
    """`close` needs: tickets loaded, not finished, no open ticket in the tree
    or at the initiative level, and no open epic. Forges and specs are checked
    by the caller (they are actions of their own)."""
    return (
        initiative.tickets is not None
        and not initiative.finished
        and not any(t.word not in CLOSED_STATES for t in initiative.top_tickets)
        and not has_open_ticket(initiative.tree)
        and not has_open(initiative.tree, "epic")
    )


def open_paths(items: list[TreeItem], kind: str) -> list[str]:
    """`_bmad-output/<path>` of every item of `kind`, at any depth, not
    finished (as `has_open`). Items without a file are skipped."""
    paths = []
    for item in items:
        if item.kind == kind and item.state != FINISHED and item.path:
            paths.append(output_path(item.path))
        paths += open_paths(item.children, kind)
    return paths


def doc_actions(initiative: Initiative) -> list[Action]:
    """The row's actions, stacked under `resume`: `resume forge`, `slice spec`,
    and `close` (only when `can_close`, and no forge or spec is open).
    A finished initiative has none."""
    if initiative.finished:
        return []
    actions = []
    forges = open_paths(initiative.tree, "forge")
    specs = open_paths(initiative.tree, "spec")
    if has_open(initiative.tree, "forge"):
        actions.append(make_action(
            "resume forge", f"/bmad-forge-idea Resume {', '.join(forges)}",
            "resume the open forge"))
    if has_open(initiative.tree, "spec"):
        actions.append(make_action(
            "slice spec", f"/bmad-ticket Slice {', '.join(specs)}",
            "slice the open spec"))
    if not actions and can_close(initiative):
        actions.append(make_action(
            "close", f"/bmad-ticket Close {initiative.slug}, or plan more work",
            f"close {initiative.slug} or plan more work"))
    return actions


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
