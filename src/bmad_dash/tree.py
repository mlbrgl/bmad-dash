"""The tree of an initiative's forges, specs, walkthroughs and epics, each with a state.

Rules (from `dashboard-status-rules.md`), structural signals only:

- Forge: finished when its memlog has `status: complete`, else open.
- Spec: finished when a `tickets.toml` sits beside it (in its parent folder), else open.
- Walkthrough: finished when every block in the narrative's top checklist is
  checked, else open. Status word `n/m blocks`, or `done` when all are checked.
- Epic: finished when it has tickets and every non-dropped one is `done`, else open.
- Any open item untouched for `STALE_AFTER` is stale instead. Finished is never stale.
- Ticket: stale when not done or dropped and untouched for `STALE_AFTER`.
"""

import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

STALE_AFTER = timedelta(days=14)

CHECKBOX = re.compile(r"^\s*- \[( |x|X)\] ")

OPEN = "open"
FINISHED = "finished"
STALE = "stale"


@dataclass
class Ticket:
    ref: str  # e.g. "1.8"
    title: str
    word: str  # status word, e.g. "in progress"
    path: str | None  # plan, else leaf file, relative to `_bmad-output/`; None if neither
    updated: datetime | None
    stale: bool


@dataclass
class TreeItem:
    kind: str  # "forge", "spec", "walk" (walkthrough) or "epic"
    name: str  # folder name
    path: str | None  # link path relative to `_bmad-output/`, or None if no main file
    updated: datetime | None
    state: str  # OPEN, FINISHED or STALE
    children: list["TreeItem"] = field(default_factory=list)
    tickets: list[Ticket] = field(default_factory=list)  # epics only
    done: int | None = None  # epics only; None when tickets are unavailable
    total: int | None = None  # epics only; None when tickets are unavailable
    # Epics: "done", "in progress", or "" when no counted tickets.
    # Walkthroughs: "n/m blocks", "done", or "" when no checkboxes.
    word: str = ""
    # Extra link beside the name: a forge's `forge-report.html`, or a
    # walkthrough's `.md` narrative when its name links to the `.html` page.
    report: str | None = None
    report_label: str = "report"


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


def frontmatter(path: Path) -> dict[str, str]:
    """Top-level `key: value` lines of a file's frontmatter, quotes stripped.

    Empty when the file is unreadable or has no frontmatter.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return {}
    if not lines or lines[0].strip() != "---":
        return {}

    fields = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break  # end of frontmatter
        key, sep, value = line.partition(":")
        if sep and key and not key[0].isspace():
            fields[key] = value.strip().strip("\"'")
    return fields


def read_memlog(folder: Path) -> tuple[str | None, datetime | None]:
    """`status` and `updated` from the folder's `.memlog.md` frontmatter.

    Each is None when missing or unusable. Only the frontmatter is read.
    """
    fields = frontmatter(folder / ".memlog.md")
    status = fields.get("status") or None
    updated = parse_time(fields["updated"]) if "updated" in fields else None
    return status, updated


def file_mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return None


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
    report = folder / "forge-report.html"
    return TreeItem(
        kind="forge",
        name=folder.name,
        path=link_path(folder, output_dir),
        updated=updated,
        state=with_staleness(state, updated, now),
        report=report.relative_to(output_dir).as_posix() if report.is_file() else None,
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


def count_blocks(narrative: Path) -> tuple[int, int]:
    """`(checked, total)` of the first run of `- [ ]` / `- [x]` lines in the body.

    Frontmatter is skipped. Later checkboxes (one under each block heading) are
    ignored. `(0, 0)` when the file is unreadable or has no checkboxes.
    """
    try:
        lines = narrative.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return 0, 0

    # Skip the frontmatter, if any.
    body = lines
    if lines and lines[0].strip() == "---":
        for i, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                body = lines[i + 1:]
                break

    checked = total = 0
    for line in body:
        box = CHECKBOX.match(line)
        if box:
            total += 1
            if box.group(1) in "xX":
                checked += 1
        elif total:
            break  # end of the first run
    return checked, total


def walkthrough_item(folder: Path, output_dir: Path, now: datetime) -> TreeItem:
    """Name links to the `.html` page, with an `md` link to the narrative beside
    it; with no page, the name links to the narrative."""
    updated = newest_mtime(folder)
    narrative = folder / f"{folder.name}.md"
    html = folder / f"{folder.name}.html"
    md_path = link_path(folder, output_dir)
    html_path = html.relative_to(output_dir).as_posix() if html.is_file() else None

    checked, total = count_blocks(narrative)
    finished = total > 0 and checked == total
    if total == 0:
        word = ""
    elif finished:
        word = "done"
    else:
        word = f"{checked}/{total} blocks"

    return TreeItem(
        kind="walk",
        name=folder.name,
        path=html_path or md_path,
        updated=updated,
        state=with_staleness(FINISHED if finished else OPEN, updated, now),
        word=word,
        report=md_path if html_path else None,
        report_label="md",
    )


def doc_items(folder: Path, output_dir: Path, now: datetime) -> list[TreeItem]:
    """Forges, specs, then walkthroughs directly inside `folder`, each sorted by name."""
    return (
        [forge_item(d, output_dir, now) for d in sub_dirs(folder, "forge-")]
        + [spec_item(d, output_dir, now) for d in sub_dirs(folder, "spec-")]
        + [walkthrough_item(d, output_dir, now) for d in sub_dirs(folder, "walkthrough-")]
    )


def plans_by_ticket(folder: Path) -> dict[str, Path]:
    """`*-plan.md` files in `folder`, keyed by their frontmatter `ticket`."""
    plans = {}
    for plan in sorted(folder.glob("*-plan.md")):
        ticket = frontmatter(plan).get("ticket")
        if ticket:
            plans.setdefault(ticket, plan)
    return plans


def status_word(state: str) -> str:
    """`in-progress` -> `in progress`; other states as they are."""
    return state.replace("-", " ")


def make_ticket(
    raw: dict, folder: Path, output_dir: Path, plans: dict[str, Path], now: datetime
) -> Ticket:
    """One ticket row. Its file is its plan, else its leaf file. Its updated time
    is that file's mtime, else the epic's `tickets.toml` mtime."""
    leaf_name = raw.get("file")
    leaf = folder / leaf_name if isinstance(leaf_name, str) and leaf_name else None

    plan = None
    if raw.get("id") is not None:
        plan = plans.get(str(raw["id"]))
    if plan is None and leaf is not None:
        plan = plans.get(leaf.stem)

    file = plan or (leaf if leaf is not None and leaf.is_file() else None)
    updated = file_mtime(file) if file else file_mtime(folder / "tickets.toml")

    state = str(raw.get("state", ""))
    open_ticket = state not in ("done", "dropped")
    return Ticket(
        ref=str(raw.get("ref") or ""),
        title=str(raw.get("title", "")),
        word=status_word(state),
        path=file.relative_to(output_dir).as_posix() if file else None,
        updated=updated,
        stale=open_ticket and updated is not None and now - updated >= STALE_AFTER,
    )


def epic_item(
    folder: Path, output_dir: Path, tickets: list[dict] | None, now: datetime
) -> TreeItem:
    updated = newest_mtime(folder)
    children = doc_items(folder, output_dir, now)

    if tickets is None:
        # Tickets unavailable: counts unknown, so we can't call it finished.
        epic_tickets, done, total = [], None, None
        state = OPEN
        word = ""
    else:
        raw_tickets = [t for t in tickets if t.get("epic") == folder.name]
        counted = [t for t in raw_tickets if t.get("state") != "dropped"]
        done = sum(1 for t in counted if t.get("state") == "done")
        total = len(counted)
        finished = total > 0 and done == total
        state = FINISHED if finished else OPEN
        word = "" if total == 0 else ("done" if finished else "in progress")
        plans = plans_by_ticket(folder)
        epic_tickets = [make_ticket(t, folder, output_dir, plans, now) for t in raw_tickets]

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
        word=word,
    )


def build_tree(
    initiative_dir: Path, tickets: list[dict] | None, now: datetime
) -> list[TreeItem]:
    """Forges, specs, walkthroughs, then epics at initiative level; each epic
    holds its own forges, specs and walkthroughs. Link paths are relative to the
    initiative's parent (`_bmad-output/`)."""
    output_dir = initiative_dir.parent
    epics = [
        epic_item(d, output_dir, tickets, now) for d in sub_dirs(initiative_dir, "epic-")
    ]
    return doc_items(initiative_dir, output_dir, now) + epics


def count_items(items: list[TreeItem]) -> int:
    """Number of items in the tree, nested ones included (tickets excluded)."""
    return sum(1 + count_items(item.children) for item in items)
