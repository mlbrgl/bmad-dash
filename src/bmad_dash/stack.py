"""The PR each row's button shows, from Graphite's stack when it knows it.

Rules in `dashboard-status-rules.md` (Stack's last PR):

- In a repo Graphite tracks, `gt state` gives the stack: it starts at the
  row's branch and follows tracked children. The button is the newest PR
  from the top branch, found with one `gh pr list --head` call.
- `gt` only runs when `<git common dir>/.graphite_repo_config` exists. In an
  untracked repo `gt state` sets Graphite up and writes into `.git`.
- Without `gt`, or in an untracked repo, the user's PRs are walked instead.
- `gt_stack` runs once per page load. Its result feeds both the PR buttons
  and the initiatives' last commit (Row: Last activity).
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from bmad_dash import commands
from bmad_dash.initiatives import Initiative
from bmad_dash.prs import PullRequest, fetch_branch_pr, has_github_remote, list_my_prs
from bmad_dash.rules import row_pr, stack_branches

GT_TIMEOUT = 10  # seconds
GT_CONFIG = ".graphite_repo_config"
MAX_LOOKUPS = 8  # gh calls at once



@dataclass
class GraphiteStack:
    """What `gt state` says about the repo's branches."""
    children: dict[str, list[str]] = field(default_factory=dict)  # parent -> children
    trunks: set[str] = field(default_factory=set)  # e.g. main: never a row's stack


# What `load_topology` found:
# - GraphiteStack: from `gt state`
# - list: the user's PRs, for the fallback walk ([] without a GitHub remote)
# - None: git or gh failed
Topology = GraphiteStack | list[PullRequest] | None


def gt_tracked(root: Path) -> bool:
    """True when Graphite tracks the repo at `root`."""
    output = commands.run(["git", "rev-parse", "--git-common-dir"], root, commands.GIT_TIMEOUT)
    if output is None or not output.strip():
        return False
    # A relative path is relative to `root`; an absolute one stays as is.
    return (root / output.strip() / GT_CONFIG).is_file()


def gt_stack(root: Path) -> GraphiteStack | None:
    """Trunk branches and each branch's tracked children, from `gt state`.
    None when the repo isn't tracked (gt is not run), gt fails, or the JSON is bad."""
    if not gt_tracked(root):
        return None
    output = commands.run(["gt", "state"], root, GT_TIMEOUT)
    if output is None:
        return None
    try:
        state = json.loads(output)
        stack = GraphiteStack()
        for branch, info in state.items():
            if info.get("trunk"):
                stack.trunks.add(str(branch))
                continue
            for parent in info.get("parents", []):
                stack.children.setdefault(str(parent["ref"]), []).append(str(branch))
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    return stack


def load_topology(root: Path, stack: GraphiteStack | None) -> Topology:
    """What `stack_prs` needs, before the initiatives are known.
    `stack` is from `gt_stack`, run once per page load and shared with the
    initiatives. No GitHub remote: `[]`, and gh doesn't run."""
    remote = has_github_remote(root)
    if remote is None:
        return None
    if not remote:
        return []
    if stack is not None:
        return stack
    return list_my_prs(root)


def stack_prs(
    root: Path, initiatives: Sequence[Initiative], topology: Topology
) -> dict[str, PullRequest] | None:
    """Initiative slug -> the PR its button shows. A missing slug gets no
    button. None when GitHub couldn't be reached."""
    if topology is None:
        return None
    if isinstance(topology, list):  # fallback walk over the user's PRs
        found = {i.slug: row_pr(i, topology) for i in initiatives}
        return {slug: pr for slug, pr in found.items() if pr is not None}

    tops: dict[str, str] = {}  # slug -> top branch
    for initiative in initiatives:
        if not initiative.worktrees:
            continue
        branch = initiative.worktrees[0].branch
        # On trunk (e.g. main) there is no stack of the row's own: no button.
        if branch == "detached" or branch in topology.trunks:
            continue
        stack = stack_branches(topology.children, branch)
        if stack is not None:  # None: fork
            tops[initiative.slug] = stack[-1]
    if not tops:
        return {}

    # One gh call per distinct top, run in parallel, so a slow GitHub costs
    # about one timeout, not one per row.
    branches = sorted(set(tops.values()))
    with ThreadPoolExecutor(max_workers=min(len(branches), MAX_LOOKUPS)) as pool:
        results = dict(zip(branches, pool.map(lambda b: fetch_branch_pr(root, b), branches)))
    if any(result is None for result in results.values()):
        return None
    return {slug: results[top][0] for slug, top in tops.items() if results[top]}
