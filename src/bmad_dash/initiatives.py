"""Find a project's initiatives and the worktree working on each.

A worktree works on an initiative when its own
`_bmad/custom/config.user.toml` has `[core] active_initiative = "<slug>"`.

The project's `_bmad-output/backlog/` holds tickets with no initiative or
epic. It reads as one more row (`load_backlog`): its tickets at the top
level, its links opening in the main checkout, since it belongs to the
project, not an initiative. No row when the folder is missing or holds no
ticket.
"""

from __future__ import annotations

import hashlib
from collections.abc import Set
from concurrent.futures import ThreadPoolExecutor
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from bmad_dash import commands
from bmad_dash.rules import claimed_branches, stack_tips
from bmad_dash.signals import Activity, last_activity, worktree_commits
from bmad_dash.tree import (
    CLOSED_STATES,
    NextInfo,
    Ticket,
    TreeItem,
    build_tree,
    frontmatter,
    initiative_tickets,
    load_next,
    load_tickets,
)

if TYPE_CHECKING:  # types only: stack.py imports this module
    from bmad_dash.stack import GraphiteStack


@dataclass
class Worktree:
    path: Path
    branch: str  # branch name, or "detached"


@dataclass
class Initiative:
    slug: str
    title: str
    # At most one: the first worktree `git worktree list` gives that claims
    # this initiative. Kept a list so empty and present read the same everywhere.
    worktrees: list[Worktree] = field(default_factory=list)
    # From one `tickets.py status` call; None when it failed. Shared by the tree and Status.
    tickets: list[dict] | None = None
    # From one `tickets.py next` call; None when it failed (then nothing
    # `waits on` and nothing is ready). Shared by the tree and Status.
    next_info: NextInfo | None = None
    tree: list[TreeItem] = field(default_factory=list)
    # Tickets directly in the initiative folder (no epic); shown after the epics.
    top_tickets: list[Ticket] = field(default_factory=list)
    activity: Activity | None = None
    # Initiative file frontmatter `status`; "done" or "dropped" means finished.
    status: str = ""
    # The project's folder name (`root.name`); set by `make_initiative`.
    project: str = ""
    # The project's backlog row, not an initiative (`load_backlog`).
    backlog: bool = False

    @property
    def key(self) -> str:
        """`<project>/<slug>`: names this row for open trees and failures."""
        return f"{self.project}/{self.slug}"

    @property
    def finished(self) -> bool:
        return self.status in CLOSED_STATES


def list_worktrees(root: Path) -> list[Worktree] | None:
    """All worktrees of the repo at `root`, main checkout included.
    None when git fails."""
    output = commands.run(
        ["git", "worktree", "list", "--porcelain"], root, commands.GIT_TIMEOUT
    )
    if output is None:
        return None

    worktrees = []
    # Each worktree is a block of lines separated by a blank line.
    for block in output.strip().split("\n\n"):
        path = None
        branch = "detached"
        for line in block.splitlines():
            if line.startswith("worktree "):
                path = Path(line.removeprefix("worktree "))
            elif line.startswith("branch "):
                branch = line.removeprefix("branch ").removeprefix("refs/heads/")
        if path is not None:
            worktrees.append(Worktree(path=path, branch=branch))
    return worktrees


def worktree_id(path: Path) -> str:
    """A short, stable id for a worktree path, safe to put in a form."""
    return hashlib.sha256(str(path).encode()).hexdigest()[:12]


def active_initiative(worktree_path: Path) -> str | None:
    """The initiative slug this worktree's config points at, or None."""
    config_path = worktree_path / "_bmad" / "custom" / "config.user.toml"
    try:
        with config_path.open("rb") as f:
            config = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return None

    core = config.get("core")
    if not isinstance(core, dict):
        return None
    slug = core.get("active_initiative")
    return slug if isinstance(slug, str) and slug else None


def initiative_fields(initiative_dir: Path) -> dict[str, str]:
    """The initiative file's frontmatter; empty when unreadable or missing."""
    return frontmatter(initiative_dir / f"{initiative_dir.name}.md")


def read_title(initiative_dir: Path, fields: dict[str, str] | None = None) -> str:
    """The `title:` from the initiative file's frontmatter, or the folder name.
    Pass `fields` from `initiative_fields` to skip reading the file again."""
    if fields is None:
        fields = initiative_fields(initiative_dir)
    return fields.get("title") or initiative_dir.name


def make_initiative(
    root: Path,
    initiative_dir: Path,
    worktrees: list[Worktree],
    now: datetime,
    stack: GraphiteStack | None = None,
    claimed: Set[str] = frozenset(),
) -> Initiative:
    """`stack` is from `gt_stack`; with it, the last commit is the newest on
    the stack's branches, not just the worktree's HEAD. `claimed` holds the
    branches initiatives' worktrees have checked out; the walk skips them,
    except this initiative's own branch."""
    # The two `tickets.py` calls are independent: run them side by side.
    with ThreadPoolExecutor(max_workers=1) as pool:
        next_future = pool.submit(load_next, root, initiative_dir)
        tickets_data = load_tickets(root, initiative_dir)
        next_info = next_future.result()
    tickets = tickets_data.tickets if tickets_data else None
    epic_order = tickets_data.epic_order if tickets_data else []
    branches = None
    if worktrees:
        own = worktrees[0].branch
        branches = stack_tips(stack, own, claimed - {own})
    # One git call, shared by the tree (walkthrough staleness) and Last activity.
    commits = worktree_commits(worktrees, branches)
    last_commit = max((c.when for c in commits), default=None)
    fields = initiative_fields(initiative_dir)  # read once, for title and status
    return Initiative(
        slug=initiative_dir.name,
        title=read_title(initiative_dir, fields),
        worktrees=worktrees,
        tickets=tickets,
        next_info=next_info,
        tree=build_tree(initiative_dir, tickets, now, last_commit, next_info, epic_order),
        top_tickets=initiative_tickets(initiative_dir, tickets, now, next_info),
        activity=last_activity(initiative_dir, worktrees, commits),
        status=fields.get("status", ""),
        project=root.name,
    )


def worktrees_by_slug(
    root: Path, worktrees: list[Worktree] | None = None
) -> dict[str, Worktree]:
    """Initiative slug -> the worktree working on it.
    One worktree per initiative: the first one `git worktree list` gives wins,
    but never a child over its parent. A child is a worktree parallel auto
    made, named `<parent's folder name>-<ref>`: it mirrors one of the parent's
    tickets, so the row stays the parent's.
    Pass `worktrees` from `list_worktrees` to skip calling git again.
    When git fails, no initiative has a worktree."""
    if worktrees is None:
        worktrees = list_worktrees(root) or []
    claimants: dict[str, list[Worktree]] = {}
    for worktree in worktrees:
        slug = active_initiative(worktree.path)
        if slug:
            claimants.setdefault(slug, []).append(worktree)
    claimed: dict[str, Worktree] = {}
    for slug, candidates in claimants.items():
        # A shortest folder name is never a child, so one always remains.
        parents = [w for w in candidates
                   if not any(is_child(w, other) for other in candidates)]
        claimed[slug] = parents[0]
    return claimed


def is_child(worktree: Worktree, other: Worktree) -> bool:
    """True when `worktree`'s folder is named `<other's folder name>-<suffix>`."""
    return worktree.path.name.startswith(other.path.name + "-")


def initiative_worktree(
    root: Path, slug: str, worktrees: list[Worktree] | None = None
) -> Worktree | None:
    """The worktree working on initiative `slug`, or None if none claims it.
    Pass `worktrees` from `list_worktrees` to skip calling git again."""
    return worktrees_by_slug(root, worktrees).get(slug)


def list_initiatives(
    root: Path, now: datetime | None = None, stack: GraphiteStack | None = None
) -> list[Initiative]:
    """One Initiative per `_bmad-output/initiative-*` folder, sorted by slug,
    each with the first worktree whose config names it, its tickets and its tree.
    Pass `stack` from `gt_stack` so commits come from the Graphite stack."""
    if now is None:
        now = datetime.now()
    claimed = worktrees_by_slug(root)

    initiative_dirs = sorted(
        (p for p in (root / "_bmad-output").glob("initiative-*") if p.is_dir()),
        key=lambda p: p.name,
    )
    # Branches the rows' worktrees have checked out stop the stack walk;
    # same set as `stack_prs` builds from these rows.
    branches = claimed_branches(claimed[d.name] for d in initiative_dirs if d.name in claimed)
    # Each initiative runs its own `tickets.py` call, so build them in parallel:
    # the page then waits for the slowest call, not the sum of them.
    # map() keeps the slug order.
    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(
            lambda d: make_initiative(
                root, d, [claimed[d.name]] if d.name in claimed else [], now, stack,
                branches,
            ),
            initiative_dirs,
        ))


BACKLOG = "backlog"


def load_backlog(root: Path, now: datetime | None = None) -> Initiative | None:
    """The project's `_bmad-output/backlog/` as a row, or None when the folder
    is missing or holds no ticket. Its tickets sit at the top level; its
    worktree is the main checkout (the first `git worktree list` gives).
    When `tickets.py` fails the row stays, so its panel can say so."""
    if now is None:
        now = datetime.now()
    backlog_dir = root / "_bmad-output" / BACKLOG
    if not backlog_dir.is_dir():
        return None
    with ThreadPoolExecutor(max_workers=1) as pool:
        next_future = pool.submit(load_next, root, backlog_dir)
        tickets_data = load_tickets(root, backlog_dir)
        next_info = next_future.result()
    tickets = tickets_data.tickets if tickets_data else None
    top_tickets = initiative_tickets(backlog_dir, tickets, now, next_info)
    if tickets is not None and not top_tickets:
        return None
    worktrees = list_worktrees(root) or []
    return Initiative(
        slug=BACKLOG,
        title="Backlog",
        worktrees=worktrees[:1],
        tickets=tickets,
        next_info=next_info,
        top_tickets=top_tickets,
        # Its plans only: the main checkout's commits aren't backlog work.
        activity=last_activity(backlog_dir, [], []),
        project=root.name,
        backlog=True,
    )
