"""The tree of an initiative's forges, specs and epics, each with a state.

Rules (from `dashboard-status-rules.md`), structural signals only:

- Forge: finished when its memlog has `status: complete`, else open.
- Spec: finished when a `tickets.toml` sits beside it (in its parent folder), else open.
- Epic: finished when it has tickets and every non-dropped one is `done`, else open.
- Any open item untouched for `STALE_AFTER` is stale instead. Finished is never stale.
"""

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

STALE_AFTER = timedelta(days=14)

OPEN = "open"
FINISHED = "finished"
STALE = "stale"


@dataclass
class TreeItem:
    kind: str  # "forge", "spec" or "epic"
    name: str  # folder name
    path: str | None  # link path relative to `_bmad-output/`, or None if no main file
    updated: datetime | None
    state: str  # OPEN, FINISHED or STALE
    children: list["TreeItem"] = field(default_factory=list)
    tickets: list[dict] = field(default_factory=list)  # epics only
    done: int | None = None  # epics only; None when tickets are unavailable
    total: int | None = None  # epics only; None when tickets are unavailable


def load_tickets(root: Path, initiative_dir: Path) -> list[dict] | None:
    """All tickets of the initiative from `tickets.py status`, or None on any failure."""
    script = root / "_bmad" / "method" / "scripts" / "tickets.py"
    if not script.is_file():
        return None
    try:
        result = subprocess.run(
            ["uv", "run", str(script), "--project-root", str(root),
             "status", str(initiative_dir)],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        # ValueError covers UnicodeDecodeError on undecodable output.
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    tickets = data.get("tickets")
    if not isinstance(tickets, list):
        return None
    return [t for t in tickets if isinstance(t, dict)]


def parse_time(text: str) -> datetime | None:
    """`YYYY-MM-DDTHH:MM` or a date, as a naive local datetime. None if unparsable."""
    try:
        value = datetime.fromisoformat(text.strip().strip("\"'"))
    except ValueError:
        return None
    if value.tzinfo is not None:
        value = value.astimezone().replace(tzinfo=None)
    return value


def read_memlog(folder: Path) -> tuple[str | None, datetime | None]:
    """`status` and `updated` from the folder's `.memlog.md` frontmatter.

    Each is None when missing or unusable. Only the frontmatter is read.
    """
    try:
        lines = (folder / ".memlog.md").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None, None
    if not lines or lines[0].strip() != "---":
        return None, None

    status = None
    updated = None
    for line in lines[1:]:
        if line.strip() == "---":
            break  # end of frontmatter
        if line.startswith("status:"):
            status = line.removeprefix("status:").strip().strip("\"'") or None
        elif line.startswith("updated:"):
            updated = parse_time(line.removeprefix("updated:"))
    return status, updated


def newest_mtime(folder: Path) -> datetime | None:
    """Newest mtime of the files directly in `folder`, or the folder's own mtime."""
    try:
        times = [p.stat().st_mtime for p in folder.iterdir() if p.is_file()]
        if not times:
            times = [folder.stat().st_mtime]
    except OSError:
        return None
    return datetime.fromtimestamp(max(times))


def with_staleness(state: str, updated: datetime | None, now: datetime) -> str:
    """Open items untouched for STALE_AFTER become stale."""
    if state == OPEN and updated is not None and now - updated >= STALE_AFTER:
        return STALE
    return state


def link_path(folder: Path, output_dir: Path) -> str | None:
    """`<folder>/<folder>.md` relative to `_bmad-output/`, or None if it's missing."""
    main_file = folder / f"{folder.name}.md"
    if not main_file.is_file():
        return None
    return main_file.relative_to(output_dir).as_posix()


def sub_dirs(folder: Path, prefix: str) -> list[Path]:
    return sorted(
        (p for p in folder.glob(f"{prefix}*") if p.is_dir()),
        key=lambda p: p.name,
    )


def forge_item(folder: Path, output_dir: Path, now: datetime) -> TreeItem:
    status, updated = read_memlog(folder)
    updated = updated or newest_mtime(folder)
    state = FINISHED if status == "complete" else OPEN
    return TreeItem(
        kind="forge",
        name=folder.name,
        path=link_path(folder, output_dir),
        updated=updated,
        state=with_staleness(state, updated, now),
    )


def spec_item(folder: Path, output_dir: Path, now: datetime) -> TreeItem:
    _, updated = read_memlog(folder)
    updated = updated or newest_mtime(folder)
    sliced = (folder.parent / "tickets.toml").is_file()
    state = FINISHED if sliced else OPEN
    return TreeItem(
        kind="spec",
        name=folder.name,
        path=link_path(folder, output_dir),
        updated=updated,
        state=with_staleness(state, updated, now),
    )


def forges_and_specs(folder: Path, output_dir: Path, now: datetime) -> list[TreeItem]:
    """Forges then specs directly inside `folder`, each sorted by name."""
    return [forge_item(d, output_dir, now) for d in sub_dirs(folder, "forge-")] + [
        spec_item(d, output_dir, now) for d in sub_dirs(folder, "spec-")
    ]


def epic_item(
    folder: Path, output_dir: Path, tickets: list[dict] | None, now: datetime
) -> TreeItem:
    updated = newest_mtime(folder)
    children = forges_and_specs(folder, output_dir, now)

    if tickets is None:
        # Tickets unavailable: counts unknown, so we can't call it finished.
        epic_tickets, done, total = [], None, None
        state = OPEN
    else:
        epic_tickets = [t for t in tickets if t.get("epic") == folder.name]
        counted = [t for t in epic_tickets if t.get("state") != "dropped"]
        done = sum(1 for t in counted if t.get("state") == "done")
        total = len(counted)
        state = FINISHED if total > 0 and done == total else OPEN

    return TreeItem(
        kind="epic",
        name=folder.name,
        path=link_path(folder, output_dir),
        updated=updated,
        state=with_staleness(state, updated, now),
        children=children,
        tickets=epic_tickets,
        done=done,
        total=total,
    )


def build_tree(
    initiative_dir: Path, tickets: list[dict] | None, now: datetime
) -> list[TreeItem]:
    """Forges, specs, then epics at initiative level; each epic holds its own
    forges and specs. Link paths are relative to the initiative's parent
    (`_bmad-output/`)."""
    output_dir = initiative_dir.parent
    epics = [
        epic_item(d, output_dir, tickets, now) for d in sub_dirs(initiative_dir, "epic-")
    ]
    return forges_and_specs(initiative_dir, output_dir, now) + epics


def count_items(items: list[TreeItem]) -> int:
    """Number of items in the tree, nested ones included (tickets excluded)."""
    return sum(1 + count_items(item.children) for item in items)
