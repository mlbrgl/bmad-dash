"""Row signals: last activity, tickets done/total.

Rules (from `dashboard-status-rules.md`), structural signals only:

- Last activity: latest of any memlog `updated:` under the initiative folder,
  the mtime of any `*-plan.md` under it, and the last commit on each matched
  worktree. Ties go to the memlog, then the plan, then the commit.
  The text is display only: never derive state from it.
- Tickets: done/total, dropped tickets counted in neither.
"""

import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from bmad_dash.tree import read_memlog


@dataclass
class Activity:
    when: datetime
    text: str  # display only
    source: str  # "memlog", "plan" or "commit"


def last_bullet(memlog: Path) -> str:
    """The last body line starting with `- `, prefix stripped, or ""."""
    try:
        lines = memlog.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return ""

    # Skip the frontmatter, if any.
    body = lines
    if lines and lines[0].strip() == "---":
        for i, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                body = lines[i + 1:]
                break

    bullets = [line for line in body if line.startswith("- ")]
    if not bullets:
        return ""
    return bullets[-1].removeprefix("- ").strip()


def memlog_activities(initiative_dir: Path) -> list[Activity]:
    activities = []
    for memlog in sorted(initiative_dir.rglob(".memlog.md")):
        _, updated = read_memlog(memlog.parent)
        if updated is not None:
            activities.append(Activity(updated, last_bullet(memlog), "memlog"))
    return activities


def plan_activities(initiative_dir: Path) -> list[Activity]:
    activities = []
    for plan in sorted(initiative_dir.rglob("*-plan.md")):
        try:
            mtime = plan.stat().st_mtime
        except OSError:
            continue
        activities.append(Activity(datetime.fromtimestamp(mtime), plan.name, "plan"))
    return activities


def last_commit(worktree_path: Path) -> Activity | None:
    """The last commit on the worktree's HEAD, or None if git fails."""
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ct%x00%s"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if result.returncode != 0:
        return None
    stamp, _, subject = result.stdout.strip("\n").partition("\x00")
    try:
        when = datetime.fromtimestamp(int(stamp))
    except ValueError:
        return None
    return Activity(when, subject, "commit")


def worktree_commits(worktrees: list) -> list[Activity]:
    """The last commit of each worktree (anything with `.path`), skipping git failures."""
    commits = []
    for worktree in worktrees:
        commit = last_commit(worktree.path)
        if commit is not None:
            commits.append(commit)
    return commits


def last_activity(
    initiative_dir: Path, worktrees: list, commits: list[Activity] | None = None
) -> Activity | None:
    """The newest activity of the initiative, or None when there is none.

    `worktrees` are the initiative's matched worktrees (anything with `.path`).
    Pass `commits` from `worktree_commits` to skip calling git again.
    """
    if commits is None:
        commits = worktree_commits(worktrees)
    # Order matters for ties: memlogs first, then plans, then commits.
    candidates = memlog_activities(initiative_dir) + plan_activities(initiative_dir) + commits

    newest = None
    for activity in candidates:
        # Memlog times are to the minute, so compare at minute precision.
        # Strictly newer only, so earlier sources win ties.
        if newest is None or to_minute(activity.when) > to_minute(newest.when):
            newest = activity
    return newest


def to_minute(when: datetime) -> datetime:
    return when.replace(second=0, microsecond=0)


def ticket_counts(tickets: list[dict] | None) -> tuple[int, int] | None:
    """(done, total) with dropped tickets in neither; None when tickets are unavailable."""
    if tickets is None:
        return None
    counted = [t for t in tickets if t.get("state") != "dropped"]
    done = sum(1 for t in counted if t.get("state") == "done")
    return done, len(counted)
