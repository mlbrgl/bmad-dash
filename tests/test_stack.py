import json
import subprocess
from pathlib import Path

import pytest

from bmad_dash import commands as commands_module
from bmad_dash.initiatives import Initiative, Worktree
from bmad_dash.rules import stack_branches, stack_tips, walked_branches
from bmad_dash.stack import GraphiteStack, gt_stack, gt_tracked, load_topology, stack_prs

REMOTE = "origin\tgit@github.com:o/r.git (fetch)\n"


def gt_state(edges):
    """`gt state` JSON from (child, parent) pairs, with trunk main."""
    state = {"main": {"trunk": True}}
    for child, parent in edges:
        state[child] = {"trunk": False, "needs_restack": False,
                        "parents": [{"ref": parent, "sha": "0"}]}
    return json.dumps(state)


STACK = gt_state([("a", "main"), ("b", "a"), ("c", "b")])


def pr_json(number, head, base="main", state="OPEN"):
    return {"number": number, "headRefName": head, "baseRefName": base,
            "state": state, "isDraft": False,
            "url": f"https://github.com/o/r/pull/{number}"}


class Fake:
    """Stands in for `subprocess.run`. Records every command in `calls`.

    - `tracked`: whether `.graphite_repo_config` exists in the common dir
    - `gt`: `gt state` stdout, or an exception to raise
    - `by_head`: head branch -> list of PR dicts for `gh pr list --head`
    - `mine`: PR dicts for `gh pr list --author @me`
    - `gh_error`: an exception every gh call raises
    """

    def __init__(self, tmp_path, tracked=True, gt=STACK, by_head=None, mine=(),
                 remote=REMOTE, gh_error=None):
        self.calls = []
        self.common = tmp_path / "common"
        self.common.mkdir()
        if tracked:
            (self.common / ".graphite_repo_config").write_text("{}")
        self.gt = gt
        self.by_head = by_head or {}
        self.mine = list(mine)
        self.remote = remote
        self.gh_error = gh_error

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        if args[:2] == ["git", "remote"]:
            return self.done(self.remote)
        if args[:2] == ["git", "rev-parse"]:
            return self.done(f"{self.common}\n")
        if args[0] == "gt":
            if isinstance(self.gt, BaseException):
                raise self.gt
            return self.done(self.gt)
        if args[0] == "gh":
            if self.gh_error is not None:
                raise self.gh_error
            if "--head" in args:
                head = args[args.index("--head") + 1]
                return self.done(json.dumps(self.by_head.get(head, [])))
            return self.done(json.dumps(self.mine))
        raise AssertionError(f"unexpected command {args}")

    @staticmethod
    def done(stdout):
        return subprocess.CompletedProcess([], 0, stdout, "")

    def ran(self, name):
        return [args for args in self.calls if args[0] == name]


@pytest.fixture
def fake(tmp_path, monkeypatch):
    def make(**kwargs):
        fake = Fake(tmp_path, **kwargs)
        monkeypatch.setattr(commands_module.subprocess, "run", fake)
        return fake
    return make


def row(branch="a", slug="initiative-a", worktrees=None):
    if worktrees is None:
        worktrees = [Worktree(Path("/wt"), branch)]
    return Initiative(slug, "A", worktrees)


ROOT = Path("/root")


def shown(rows):
    """slug -> PR number, or None when GitHub failed."""
    result = stack_prs(ROOT, rows, load_topology(ROOT, gt_stack(ROOT)))
    return None if result is None else {slug: pr.number for slug, pr in result.items()}


# --- stack_branches ---

def test_stack_follows_single_children():
    children = {"main": ["a"], "a": ["b"], "b": ["c"]}
    assert stack_branches(children, "a") == ["a", "b", "c"]
    assert stack_branches(children, "b") == ["b", "c"]


def test_branch_not_in_stack_is_its_own_top():
    assert stack_branches({"main": ["a"]}, "x") == ["x"]


def test_fork_anywhere_gives_none():
    assert stack_branches({"a": ["b", "x"]}, "a") is None
    assert stack_branches({"a": ["b"], "b": ["c", "d"]}, "a") is None


def test_cycle_gives_none():
    assert stack_branches({"a": ["b"], "b": ["a"]}, "a") is None


# --- gt_tracked / gt_stack ---

def test_relative_common_dir_resolves_against_root(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / ".graphite_repo_config").write_text("{}")
    monkeypatch.setattr(commands_module.subprocess, "run",
                        lambda args, **kw: subprocess.CompletedProcess(args, 0, ".git\n", ""))
    assert gt_tracked(tmp_path)
    assert not gt_tracked(tmp_path / "elsewhere")


def test_real_repo_without_graphite_is_untracked(project):
    assert not gt_tracked(project)
    (project / ".git" / ".graphite_repo_config").write_text("{}")
    assert gt_tracked(project)


def test_gt_stack_maps_parent_to_children_and_keeps_trunk(fake):
    fake(gt=gt_state([("a", "main"), ("b", "a"), ("x", "a")]))
    assert gt_stack(ROOT) == GraphiteStack({"main": ["a"], "a": ["b", "x"]}, {"main"})


def test_untracked_repo_never_runs_gt(fake):
    f = fake(tracked=False)
    assert gt_stack(ROOT) is None
    assert f.ran("gt") == []


@pytest.mark.parametrize("gt", [
    FileNotFoundError("gt"),
    subprocess.TimeoutExpired("gt", 10),
    "not json",
    "[1, 2]",
    '{"a": "not an object"}',
    '{"a": {"trunk": false, "parents": [{"sha": "no ref"}]}}',
])
def test_gt_failures_give_none(fake, gt):
    fake(gt=gt)
    assert gt_stack(ROOT) is None


# --- stack_prs, gt path (one test per matrix row) ---

def test_three_branch_stack_looks_up_every_branch_and_shows_highest_pr(fake):
    f = fake(by_head={"c": [pr_json(31, "c", base="b")]})
    assert shown([row("a")]) == {"initiative-a": 31}
    assert sorted(f.ran("gh")) == [[
        "gh", "pr", "list", "--head", head, "--state", "all", "--limit", "1",
        "--json", "number,headRefName,baseRefName,state,isDraft,url",
    ] for head in ["a", "b", "c"]]


def test_middle_of_stack_shows_top_pr(fake):
    fake(by_head={"c": [pr_json(31, "c", base="b")]})
    assert shown([row("b")]) == {"initiative-a": 31}


def test_old_or_foreign_top_pr_is_found_by_head(fake):
    # Not among "my" PRs at all: the head lookup finds it anyway.
    f = fake(by_head={"c": [pr_json(3, "c", base="b")]}, mine=[])
    assert shown([row("a")]) == {"initiative-a": 3}
    assert not any("--author" in args for args in f.calls)


def test_closed_sibling_pr_is_not_a_fork(fake):
    fake(gt=gt_state([("a", "main"), ("b", "a")]),
         by_head={"b": [pr_json(12, "b", base="a")]},
         mine=[pr_json(12, "b", base="a"), pr_json(9, "x", base="a", state="CLOSED")])
    assert shown([row("a")]) == {"initiative-a": 12}


def test_fork_gives_no_button_and_no_gh_call(fake):
    f = fake(gt=gt_state([("a", "main"), ("b", "a"), ("x", "a")]),
             by_head={"b": [pr_json(12, "b")], "x": [pr_json(13, "x")]})
    assert shown([row("a")]) == {}
    assert f.ran("gh") == []


def test_untracked_branch_is_its_own_top(fake):
    f = fake(by_head={"lone": [pr_json(5, "lone")]})
    assert shown([row("lone")]) == {"initiative-a": 5}
    assert [args[4] for args in f.ran("gh")] == ["lone"]


def test_top_without_pr_shows_the_highest_branch_with_one(fake):
    fake(by_head={"a": [pr_json(7, "a")], "b": [pr_json(8, "b", base="a")]})
    assert shown([row("a")]) == {"initiative-a": 8}


def test_no_pr_on_any_branch_is_no_button_not_a_failure(fake):
    f = fake(by_head={})
    assert shown([row("a")]) == {}
    assert sorted(args[4] for args in f.ran("gh")) == ["a", "b", "c"]


@pytest.mark.parametrize("error", [
    FileNotFoundError("gh"),
    subprocess.TimeoutExpired("gh", 10),
])
def test_gh_failure_in_gt_path_gives_none(fake, error):
    fake(gh_error=error)
    assert shown([row("a")]) is None


def test_one_failed_lookup_fails_the_whole_root(fake, monkeypatch):
    f = fake(by_head={"c": [pr_json(31, "c")]})
    real = f.__call__

    def run(args, **kwargs):
        if args[0] == "gh" and "lone" in args:
            return subprocess.CompletedProcess(args, 1, "", "boom")
        return real(args, **kwargs)

    monkeypatch.setattr(commands_module.subprocess, "run", run)
    assert shown([row("a"), row("lone", slug="initiative-b")]) is None


def test_rows_on_one_stack_each_get_their_own_pr(fake):
    # A on a, B on b: b is claimed, so A's stack is just a.
    f = fake(gt=gt_state([("a", "main"), ("b", "a")]),
             by_head={"a": [pr_json(7, "a")], "b": [pr_json(8, "b", base="a")]})
    rows = [row("a"), row("b", slug="initiative-b")]
    assert shown(rows) == {"initiative-a": 7, "initiative-b": 8}
    # Each distinct branch looked up once.
    assert sorted(args[4] for args in f.ran("gh")) == ["a", "b"]


def test_claimed_child_beside_a_free_one_is_a_stop_not_a_fork(fake):
    gt = gt_state([("a", "main"), ("b", "a"), ("c", "a")])
    rows = [row("a"), row("b", slug="initiative-b")]
    fake(gt=gt, by_head={"a": [pr_json(7, "a")], "b": [pr_json(8, "b", base="a")],
                         "c": [pr_json(9, "c", base="a")]})
    assert shown(rows) == {"initiative-a": 9, "initiative-b": 8}


def test_claimed_child_beside_a_free_one_without_pr_falls_back_to_own(fake):
    gt = gt_state([("a", "main"), ("b", "a"), ("c", "a")])
    rows = [row("a"), row("b", slug="initiative-b")]
    fake(gt=gt, by_head={"a": [pr_json(7, "a")], "b": [pr_json(8, "b", base="a")]})
    assert shown(rows) == {"initiative-a": 7, "initiative-b": 8}


def test_without_claims_a_fork_is_still_a_fork(fake):
    f = fake(gt=gt_state([("a", "main"), ("b", "a"), ("c", "a")]))
    assert shown([row("a")]) == {}
    assert f.ran("gh") == []


@pytest.mark.parametrize("gt", [
    gt_state([("a", "main")]),  # one tracked child
    gt_state([]),  # none
])
def test_row_on_trunk_gets_no_button_and_no_gh_call(fake, gt):
    f = fake(gt=gt, by_head={"main": [pr_json(66, "main")], "a": [pr_json(7, "a")]})
    assert shown([row("main")]) == {}
    assert f.ran("gh") == []


def test_detached_or_no_worktree_runs_no_gh(fake):
    f = fake(by_head={"detached": [pr_json(5, "detached")]})
    assert shown([row("detached"), row(slug="initiative-b", worktrees=[])]) == {}
    assert f.ran("gh") == []


# --- fallback walk: gt missing, repo untracked, gt fails, bad JSON ---

FALLBACKS = [
    {"gt": FileNotFoundError("gt")},
    {"tracked": False},
    {"gt": subprocess.TimeoutExpired("gt", 10)},
    {"gt": "not json"},
]
STACK_PRS = [pr_json(7, "a"), pr_json(8, "b", base="a"), pr_json(9, "c", base="b"),
             pr_json(4, "m", state="MERGED"),
             {**pr_json(5, "d"), "isDraft": True},
             pr_json(10, "f"), pr_json(11, "g", base="f"), pr_json(12, "h", base="f")]


@pytest.mark.parametrize("setup", FALLBACKS)
def test_fallback_walks_my_prs(fake, setup):
    f = fake(mine=STACK_PRS, **setup)
    rows = [row("a", "lone-or-stack"), row("m", "merged"), row("d", "draft"),
            row("f", "fork"), row("none", "no-pr"), row("detached", "detached")]
    result = stack_prs(ROOT, rows, load_topology(ROOT, gt_stack(ROOT)))
    assert {slug: (pr.number, pr.state) for slug, pr in result.items()} == {
        "lone-or-stack": (9, "open"), "merged": (4, "merged"), "draft": (5, "draft"),
    }
    assert not any("--head" in args for args in f.calls)
    if setup.get("tracked") is False:
        assert f.ran("gt") == []


@pytest.mark.parametrize("setup", FALLBACKS)
def test_fallback_gh_failure_gives_none(fake, setup):
    fake(gh_error=FileNotFoundError("gh"), **setup)
    assert shown([row("a")]) is None


@pytest.mark.parametrize("setup", FALLBACKS)
def test_fallback_shared_stack_each_row_its_own_pr(fake, setup):
    fake(mine=[pr_json(7, "a"), pr_json(8, "b", base="a")], **setup)
    rows = [row("a"), row("b", slug="initiative-b")]
    assert shown(rows) == {"initiative-a": 7, "initiative-b": 8}


def test_no_github_remote_runs_no_gh(fake):
    f = fake(remote="origin\tgit@gitlab.com:o/r.git (fetch)\n")
    assert shown([row("a")]) == {}
    assert f.ran("gh") == []


def test_no_github_remote_still_gives_the_gt_stack(fake):
    # The stack is local topology: the commits use it even without GitHub.
    fake(remote="origin\tgit@gitlab.com:o/r.git (fetch)\n")
    stack = gt_stack(ROOT)
    assert stack == GraphiteStack({"main": ["a"], "a": ["b"], "b": ["c"]}, {"main"})
    assert load_topology(ROOT, stack) == []


# --- walked_branches / stack_tips (the initiative's last commit) ---

CHAIN = GraphiteStack({"main": ["a"], "a": ["b"], "b": ["c"]}, {"main"})


def test_walk_goes_to_the_top():
    assert walked_branches(CHAIN.children, "a") == ["a", "b", "c"]
    assert walked_branches(CHAIN.children, "b") == ["b", "c"]


def test_claimed_branches_are_not_children():
    forked = {"main": ["a"], "a": ["b", "c"], "c": ["d"]}
    assert walked_branches(CHAIN.children, "a", {"b"}) == ["a"]
    assert walked_branches(forked, "a", {"b"}) == ["a", "c", "d"]
    assert stack_branches(forked, "a", {"b"}) == ["a", "c", "d"]
    assert stack_branches(forked, "a") is None
    # A row's own branch is never claimed against itself.
    assert walked_branches(CHAIN.children, "b", {"b"}) == ["b", "c"]
    assert stack_tips(CHAIN, "a", {"b"}) == ["a"]


def test_walk_stops_at_a_fork_and_a_cycle():
    assert walked_branches({"a": ["b", "x"]}, "a") == ["a"]
    assert walked_branches({"a": ["b"], "b": ["c", "d"]}, "a") == ["a", "b"]
    assert walked_branches({"a": ["b"], "b": ["a"]}, "a") == ["a", "b"]


@pytest.mark.parametrize("stack, branch, tips", [
    (CHAIN, "a", ["a", "b", "c"]),
    (CHAIN, "b", ["b", "c"]),
    (GraphiteStack({"main": ["a"], "a": ["b", "x"]}, {"main"}), "a", ["a"]),  # fork
    (CHAIN, "main", None),  # trunk: its own HEAD
    (CHAIN, "detached", None),
    (None, "a", None),  # no gt, untracked, or gt failed
    (CHAIN, "untracked-branch", ["untracked-branch"]),
])
def test_stack_tips(stack, branch, tips):
    assert stack_tips(stack, branch) == tips
