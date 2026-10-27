"""GitHub pull requests: the user's recent ones, or the one from a branch,
and the last PR of a branch's stack."""

import json
from collections.abc import Set
from dataclasses import dataclass
from pathlib import Path

from bmad_dash import commands

GH_TIMEOUT = 10  # seconds
GH_FIELDS = "number,headRefName,baseRefName,state,isDraft,url"
GH_COMMAND = [
    "gh", "pr", "list", "--author", "@me", "--state", "all", "--limit", "100",
    "--json", GH_FIELDS,
]


def branch_pr_command(branch: str) -> list[str]:
    """`gh pr list` for the newest PR from `branch`, by anyone, in any state.
    Exits 0 with `[]` when there is none, unlike `gh pr view`."""
    return [
        "gh", "pr", "list", "--head", branch, "--state", "all", "--limit", "1",
        "--json", GH_FIELDS,
    ]


@dataclass
class PullRequest:
    number: int
    head: str  # branch the PR comes from
    base: str  # branch the PR merges into
    state: str  # "draft", "open", "merged" or "closed"
    url: str


def has_github_remote(root: Path) -> bool | None:
    """True if any remote of the repo at `root` points at github.com.
    None when git fails."""
    output = commands.run(["git", "remote", "-v"], root, GH_TIMEOUT)
    if output is None:
        return None
    return "github.com" in output


def shown_state(raw: dict) -> str:
    state = str(raw.get("state", "")).lower()  # gh says OPEN, MERGED, CLOSED
    # GitHub keeps `isDraft` after a draft is merged or closed: those win.
    if state in ("merged", "closed"):
        return state
    return "draft" if raw.get("isDraft") else state


def parse_prs(output: str | None) -> list[PullRequest] | None:
    """PRs from `gh pr list --json` output. None when gh failed or the JSON
    isn't a list of PRs."""
    if output is None:
        return None
    try:
        return [
            PullRequest(
                number=int(raw["number"]),
                head=str(raw["headRefName"]),
                base=str(raw["baseRefName"]),
                state=shown_state(raw),
                url=str(raw["url"]),
            )
            for raw in json.loads(output)
        ]
    except (ValueError, TypeError, KeyError):
        return None


def list_my_prs(root: Path) -> list[PullRequest] | None:
    """The user's newest 100 PRs in the repo at `root`. None when gh fails.
    Doesn't check the remote: callers do."""
    return parse_prs(commands.run(GH_COMMAND, root, GH_TIMEOUT))


def fetch_branch_pr(root: Path, branch: str) -> list[PullRequest] | None:
    """The newest PR from `branch` as a one-item list, whoever wrote it.
    `[]` when the branch has no PR, None when gh fails."""
    return parse_prs(commands.run(branch_pr_command(branch), root, GH_TIMEOUT))


def last_pr(
    prs: list[PullRequest], branch: str, claimed: Set[str] = frozenset()
) -> PullRequest | None:
    """The topmost PR of the stack that starts at `branch`, or None.

    Start at the PR from `branch` (highest number if several). Then follow the
    PR based on the current PR's head, until none is. Two PRs from different
    heads based on the same branch is a fork: None. PRs in any state count.
    PRs from `claimed` heads (other initiatives' worktrees) are skipped before
    counting, so they stop the walk rather than fork it.
    """
    def newest_from(head: str) -> PullRequest | None:
        matches = [pr for pr in prs if pr.head == head]
        return max(matches, key=lambda pr: pr.number) if matches else None

    claimed = claimed - {branch}
    current = newest_from(branch)
    visited = set()
    while current is not None:
        visited.add(current.head)
        next_heads = {pr.head for pr in prs
                      if pr.base == current.head and pr.head not in claimed}
        if not next_heads:
            return current
        if len(next_heads) > 1:
            return None  # fork
        next_head = next_heads.pop()
        if next_head in visited:
            return None  # cycle: no sensible top
        # Only PRs from that head stacked on this one: a re-targeted PR
        # from the same head (other base) is not part of this stack.
        current = max(
            (pr for pr in prs if pr.head == next_head and pr.base == current.head),
            key=lambda pr: pr.number,
        )
    return None
