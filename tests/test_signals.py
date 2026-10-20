import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from bmad_dash.initiatives import Worktree, list_initiatives
from bmad_dash.signals import last_activity, ticket_counts

T_OLD = datetime(2026, 10, 1, 9, 0)
T_MID = datetime(2026, 10, 5, 9, 0)
T_NEW = datetime(2026, 10, 10, 9, 0)


@pytest.fixture
def initiative(tmp_path: Path) -> Path:
    folder = tmp_path / "_bmad-output" / "initiative-x"
    folder.mkdir(parents=True)
    return folder


def write_memlog(folder: Path, updated: datetime, body: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ".memlog.md").write_text(
        f"---\nupdated: {updated:%Y-%m-%dT%H:%M}\n---\n\n{body}"
    )


def write_plan(folder: Path, name: str, mtime: datetime) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    plan = folder / name
    plan.write_text("# plan\n")
    os.utime(plan, (mtime.timestamp(), mtime.timestamp()))


def make_repo(path: Path, commit_time: datetime | None, subject: str = "") -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    if commit_time is not None:
        stamp = f"@{int(commit_time.timestamp())}"
        env = {**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit",
             "--allow-empty", "-m", subject],
            cwd=path, check=True, capture_output=True, env=env,
        )
    return path


# --- last activity ---


def test_memlog_newest_gives_its_time_and_last_bullet(initiative, tmp_path):
    write_memlog(initiative / "epic-a" / "forge-a", T_NEW,
                 "- (note) first\nnot a bullet\n- (lock) last one\n")
    write_plan(initiative / "epic-a", "story-a-plan.md", T_MID)
    repo = make_repo(tmp_path / "repo", T_OLD, "old commit")

    activity = last_activity(initiative, [Worktree(repo, "main")])
    assert (activity.when, activity.text, activity.source) == (
        T_NEW, "(lock) last one", "memlog")


def test_plan_newest_gives_its_time_and_file_name(initiative, tmp_path):
    write_memlog(initiative / "forge-a", T_OLD, "- (note) x\n")
    write_plan(initiative / "epic-a", "story-a-plan.md", T_NEW)
    repo = make_repo(tmp_path / "repo", T_MID, "commit")

    activity = last_activity(initiative, [Worktree(repo, "main")])
    assert (activity.when, activity.text, activity.source) == (
        T_NEW, "story-a-plan.md", "plan")


def test_commit_newest_gives_its_time_and_subject(initiative, tmp_path):
    write_memlog(initiative / "forge-a", T_OLD, "- (note) x\n")
    write_plan(initiative / "epic-a", "story-a-plan.md", T_MID)
    repo = make_repo(tmp_path / "repo", T_NEW, "feat: <b>new</b>")

    activity = last_activity(initiative, [Worktree(repo, "main")])
    assert (activity.when, activity.text, activity.source) == (
        T_NEW, "feat: <b>new</b>", "commit")


def test_no_worktree_uses_memlogs_and_plans_only(initiative):
    write_memlog(initiative / "spec-a", T_MID, "- (event) spec done\n")
    write_plan(initiative / "epic-a", "story-a-plan.md", T_OLD)

    activity = last_activity(initiative, [])
    assert (activity.when, activity.text) == (T_MID, "(event) spec done")


def test_nothing_at_all_is_none(initiative):
    (initiative / "initiative-x.md").write_text("# x\n")
    assert last_activity(initiative, []) is None


def test_memlog_without_bullets_gives_empty_text(initiative):
    write_memlog(initiative / "forge-a", T_NEW, "just prose\n")
    activity = last_activity(initiative, [])
    assert (activity.when, activity.text) == (T_NEW, "")


def test_frontmatter_lines_are_not_bullets(initiative):
    folder = initiative / "forge-a"
    folder.mkdir()
    (folder / ".memlog.md").write_text(
        f"---\nupdated: {T_NEW:%Y-%m-%dT%H:%M}\ntags:\n- not-a-bullet\n---\nbody\n")
    assert last_activity(initiative, []).text == ""


def test_ties_go_to_memlog_then_plan_then_commit(initiative, tmp_path):
    repo = make_repo(tmp_path / "repo", T_NEW, "commit")
    write_plan(initiative, "story-a-plan.md", T_NEW)
    assert last_activity(initiative, [Worktree(repo, "main")]).source == "plan"
    write_memlog(initiative / "forge-a", T_NEW, "- memlog\n")
    assert last_activity(initiative, [Worktree(repo, "main")]).source == "memlog"


@pytest.mark.parametrize("kind", ["no commits", "gone"])
def test_git_failure_ignores_that_worktree(initiative, tmp_path, kind):
    write_memlog(initiative / "forge-a", T_OLD, "- (note) x\n")
    if kind == "no commits":
        path = make_repo(tmp_path / "repo", None)
    else:
        path = tmp_path / "gone"
    activity = last_activity(initiative, [Worktree(path, "main")])
    assert (activity.when, activity.source) == (T_OLD, "memlog")


def test_project_fixture_matched_worktree_uses_its_commit(project):
    by_slug = {i.slug: i for i in list_initiatives(project)}
    matched = by_slug["initiative-matched"].activity
    assert matched.source == "commit"
    assert matched.text == "init"
    assert by_slug["initiative-orphan"].activity is None


# --- tickets ---


def test_tickets_skip_dropped():
    tickets = [{"state": "done"}, {"state": "done"}, {"state": "review"}, {"state": "dropped"}]
    assert ticket_counts(tickets) == (2, 3)


def test_no_tickets_is_zero_of_zero():
    assert ticket_counts([]) == (0, 0)


def test_tickets_unavailable_is_none():
    assert ticket_counts(None) is None


def test_tickets_py_called_once_per_initiative(project):
    calls = project / "calls.log"
    script = project / "_bmad" / "method" / "scripts" / "tickets.py"
    script.parent.mkdir(parents=True)
    data = {"tickets": [{"state": "done"}, {"state": "dropped"}, {"state": "planned"}]}
    script.write_text(
        f"open({str(calls)!r}, 'a').write('x')\nprint({json.dumps(json.dumps(data))})\n")

    initiatives = list_initiatives(project)
    assert calls.read_text() == "x" * len(initiatives)
    assert {ticket_counts(i.tickets) for i in initiatives} == {(1, 2)}


def test_tie_is_judged_to_the_minute(initiative):
    write_memlog(initiative / "forge-a", T_NEW, "- memlog\n")
    write_plan(initiative, "story-a-plan.md", T_NEW.replace(second=30))
    activity = last_activity(initiative, [])
    assert (activity.source, activity.when) == ("memlog", T_NEW)
