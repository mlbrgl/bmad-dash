import json
import subprocess
from pathlib import Path

import pytest

from bmad_dash import commands as commands_module
from bmad_dash.prs import GH_COMMAND, PullRequest, fetch_branch_pr, last_pr
from bmad_dash.stack import load_topology


def pr(number, head, base="main", state="open"):
    return PullRequest(number, head, base, state, f"https://github.com/o/r/pull/{number}")


# --- last_pr ---

def test_lone_pr():
    assert last_pr([pr(7, "b")], "b").number == 7


def test_stack_walks_to_the_top():
    prs = [pr(7, "b"), pr(8, "c", base="b"), pr(9, "d", base="c"), pr(3, "x")]
    assert last_pr(prs, "b").number == 9


def test_stack_from_the_middle():
    prs = [pr(7, "b"), pr(8, "c", base="b"), pr(9, "d", base="c")]
    assert last_pr(prs, "c").number == 9


def test_any_state_counts():
    prs = [pr(7, "b", state="merged"), pr(8, "c", base="b", state="merged")]
    top = last_pr(prs, "b")
    assert (top.number, top.state) == (8, "merged")


def test_several_prs_from_the_branch_takes_highest_number():
    prs = [pr(5, "b", state="closed"), pr(12, "b"), pr(9, "b", state="closed")]
    assert last_pr(prs, "b").number == 12


def test_same_head_twice_above_is_not_a_fork():
    prs = [pr(7, "b"), pr(8, "c", base="b", state="closed"), pr(10, "c", base="b")]
    assert last_pr(prs, "b").number == 10


def test_retargeted_pr_from_next_head_is_not_followed():
    # #8 c->b was closed, #10 reuses head c but targets main: the stack stops at #8.
    prs = [pr(7, "b"), pr(8, "c", base="b", state="closed"), pr(10, "c"),
           pr(11, "d", base="c")]
    assert last_pr(prs, "b").number == 11
    prs = [pr(7, "b"), pr(8, "c", base="b", state="closed"), pr(10, "c")]
    assert last_pr(prs, "b").number == 8


def test_fork_gives_none():
    prs = [pr(7, "b"), pr(8, "c", base="b"), pr(9, "d", base="b")]
    assert last_pr(prs, "b") is None


def test_fork_higher_up_gives_none():
    prs = [pr(7, "b"), pr(8, "c", base="b"), pr(9, "d", base="c"), pr(10, "e", base="c")]
    assert last_pr(prs, "b") is None


def test_no_pr_for_branch():
    assert last_pr([pr(7, "other")], "b") is None
    assert last_pr([], "b") is None


def test_claimed_head_stops_the_walk():
    prs = [pr(7, "a"), pr(8, "b", base="a"), pr(9, "c", base="b")]
    assert last_pr(prs, "a", {"b"}).number == 7
    assert last_pr(prs, "b", {"b"}).number == 9  # own branch never claimed


def test_claimed_head_beside_a_free_one_is_not_a_fork():
    prs = [pr(7, "a"), pr(8, "b", base="a"), pr(9, "c", base="a")]
    assert last_pr(prs, "a") is None
    assert last_pr(prs, "a", {"b"}).number == 9


def test_cycle_gives_none():
    prs = [pr(7, "b", base="c"), pr(8, "c", base="b")]
    assert last_pr(prs, "b") is None


# --- load_topology without Graphite: the user's PRs ---

GH_JSON = json.dumps([
    {"number": 7, "headRefName": "b", "baseRefName": "main", "state": "OPEN",
     "isDraft": False, "url": "https://github.com/o/r/pull/7"},
    {"number": 8, "headRefName": "c", "baseRefName": "b", "state": "OPEN",
     "isDraft": True, "url": "https://github.com/o/r/pull/8"},
    {"number": 9, "headRefName": "d", "baseRefName": "main", "state": "MERGED",
     "isDraft": False, "url": "https://github.com/o/r/pull/9"},
    {"number": 10, "headRefName": "e", "baseRefName": "main", "state": "CLOSED",
     "isDraft": False, "url": "https://github.com/o/r/pull/10"},
])
REMOTE = "origin\tgit@github.com:o/r.git (fetch)\norigin\tgit@github.com:o/r.git (push)\n"


def fake_run(calls, remote=REMOTE, gh=None):
    """A `commands.run_process` stand-in. `gh` is a CompletedProcess or an exception."""
    def run(args, **kwargs):
        calls.append((args, kwargs))
        if args[:2] == ["git", "remote"]:
            return subprocess.CompletedProcess(args, 0, remote, "")
        if isinstance(gh, BaseException):
            raise gh
        return gh
    return run


def done(stdout="", code=0):
    return subprocess.CompletedProcess([], code, stdout, "")


def test_fetch_parses_prs_and_states(monkeypatch):
    calls = []
    monkeypatch.setattr(commands_module, "run_process", fake_run(calls, gh=done(GH_JSON)))
    result = load_topology(Path("/root"), None)
    assert [(p.number, p.head, p.base, p.state) for p in result] == [
        (7, "b", "main", "open"), (8, "c", "b", "draft"),
        (9, "d", "main", "merged"), (10, "e", "main", "closed"),
    ]
    assert result[0].url == "https://github.com/o/r/pull/7"
    args, kwargs = calls[1]
    assert args == GH_COMMAND
    assert kwargs["cwd"] == Path("/root")
    assert kwargs["timeout"] == 10


def test_no_github_remote_is_empty_and_runs_no_gh(monkeypatch):
    calls = []
    monkeypatch.setattr(commands_module, "run_process",
                        fake_run(calls, remote="origin\tgit@gitlab.com:o/r.git (fetch)\n"))
    assert load_topology(Path("/root"), None) == []
    assert len(calls) == 1  # only git remote


def test_no_remote_at_all_is_empty(monkeypatch):
    calls = []
    monkeypatch.setattr(commands_module, "run_process", fake_run(calls, remote=""))
    assert load_topology(Path("/root"), None) == []


@pytest.mark.parametrize("gh", [
    done("", code=1),
    FileNotFoundError("gh"),
    subprocess.TimeoutExpired("gh", 10),
    done("not json"),
    done('[{"number": 1}]'),  # missing fields
    done('{"a": 1}'),  # not a list of objects
    UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
])
def test_gh_failures_give_none(monkeypatch, gh):
    monkeypatch.setattr(commands_module, "run_process", fake_run([], gh=gh))
    assert load_topology(Path("/root"), None) is None


@pytest.mark.parametrize("error", [
    UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
    subprocess.TimeoutExpired("git", 10),
    FileNotFoundError("git"),
])
def test_git_remote_failure_gives_none_and_runs_no_gh(monkeypatch, error):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        raise error

    monkeypatch.setattr(commands_module, "run_process", run)
    assert load_topology(Path("/root"), None) is None
    assert calls == [["git", "remote", "-v"]]


def test_real_repo_without_remote_is_empty(project):
    assert load_topology(project, None) == []


# --- fetch_branch_pr ---

def test_branch_pr_by_head_any_author_any_state(monkeypatch):
    calls = []
    one = json.dumps([json.loads(GH_JSON)[1]])
    monkeypatch.setattr(commands_module, "run_process", fake_run(calls, gh=done(one)))
    result = fetch_branch_pr(Path("/root"), "c")
    assert [(p.number, p.head, p.state) for p in result] == [(8, "c", "draft")]
    args, kwargs = calls[0]
    assert args == ["gh", "pr", "list", "--head", "c", "--state", "all", "--limit", "1",
                    "--json", "number,headRefName,baseRefName,state,isDraft,url"]
    assert "--author" not in args
    assert kwargs["timeout"] == 10


def test_branch_without_pr_is_empty_not_failure(monkeypatch):
    monkeypatch.setattr(commands_module, "run_process", fake_run([], gh=done("[]")))
    assert fetch_branch_pr(Path("/root"), "c") == []


@pytest.mark.parametrize("gh", [
    done("", code=1),
    FileNotFoundError("gh"),
    subprocess.TimeoutExpired("gh", 10),
    done("not json"),
])
def test_branch_pr_failures_give_none(monkeypatch, gh):
    monkeypatch.setattr(commands_module, "run_process", fake_run([], gh=gh))
    assert fetch_branch_pr(Path("/root"), "c") is None
