"""The tree of an initiative's forges, specs, walkthroughs and epics, each with a state.

Rules (from `dashboard-status-rules.md`), structural signals only:

- Forge: finished when its memlog has `status: complete`, else open.
- Spec: finished when a `tickets.toml` sits beside it (in its parent folder), else open.
- Walkthrough: never finished, no status word. Stale when untouched for
  `STALE_AFTER`, or when the initiative's last commit (the one Last activity
  uses) is strictly newer than its updated time.
- Epic: finished when it has tickets and every non-dropped one is `done`, else open.
  Epics list in the initiative's `tickets.toml` build order (`tickets.py status`
  `epics`); folders not listed there follow, by name. Without it, all by name.
- Retro: `epic-<slug>-retrospective.md` in an epic folder, first under its epic.
  Its word is its frontmatter `verdict` (`accepted`, `open items` for
  `accepted-with-open-items`, `rejected`), else none. A doc: never open, never stale.
- Any open item untouched for `STALE_AFTER` is stale instead. Finished is never stale.
- Ticket: stale when not done or dropped and untouched for `STALE_AFTER`.
  A missing or null state reads as "" (`ticket_state`) and shows as `no state`.
  Its word (`ticket_word`), first match: `blocked` (plan status `blocked` or
  `blocked_at` set), `waits on <refs>` (`tickets.py next` lists it as blocked),
  `built` or `in review` (plan status), else the state word.
  Done and dropped tickets always read as their state.
- Ticket counts: done/total, dropped tickets counted in neither.
- Initiative-level tickets (those `tickets.py` lists with the initiative folder
  as their `epic`) show after the epics, at the initiative level.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from bmad_dash import commands

STALE_AFTER = timedelta(days=14)

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
    # In `tickets.py next` `ready_to_start`; only planned and backlog words use it.
    ready: bool = False
    # The plan's reason for a `blocked` ticket; the unblock prompt quotes it.
    blocked_reason: str = ""


@dataclass
class NextInfo:
    """What `tickets.py next` says about an initiative, by ticket ref."""
    waiting: dict[str, list[str]]  # blocked ticket -> the refs it waits on
    ready: set[str]  # tickets ready to start


@dataclass
class TreeItem:
    kind: str  # "forge", "spec", "walk" (walkthrough), "retro" or "epic"
    name: str  # folder name
    path: str | None  # link path relative to `_bmad-output/`, or None if no main file
    updated: datetime | None
    state: str  # OPEN, FINISHED or STALE
    children: list["TreeItem"] = field(default_factory=list)
    tickets: list[Ticket] = field(default_factory=list)  # epics only
    # Epics: "done", "in progress", or "" when no counted tickets.
    # Retros: their verdict, or "". Walkthroughs: always "".
    word: str = ""
    # Extra link beside the name: a forge's `forge-report.html`, or a
    # walkthrough's `.md` narrative when its name links to the `.html` page.
    report: str | None = None
    report_label: str = "report"


@dataclass
class TicketsData:
    """What `tickets.py status` says about an initiative."""
    tickets: list[dict]
    # Epic slugs in the initiative's `tickets.toml` build order;
    # empty when `epics` is missing or malformed (then epics sort by name).
    epic_order: list[str]


def epic_slugs(data: dict) -> list[str]:
    """The `slug` of each `epics` row, in order; [] on a wrong shape."""
    rows = next_rows(data, "epics")
    if rows is None or not all(isinstance(r.get("slug"), str) for r in rows):
        return []
    return [r["slug"] for r in rows]


def load_tickets(root: Path, initiative_dir: Path) -> TicketsData | None:
    """All tickets of the initiative and its epic order from `tickets.py status`,
    or None on any failure."""
    script = root / "_bmad" / "method" / "scripts" / "tickets.py"
    if not script.is_file():
        return None
    output = commands.run(
        ["uv", "run", str(script), "--project-root", str(root),
         "status", str(initiative_dir)],
        root,
        commands.TICKETS_TIMEOUT,
    )
    if output is None:
        return None
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    tickets = data.get("tickets")
    if not isinstance(tickets, list):
        return None
    return TicketsData(
        tickets=[t for t in tickets if isinstance(t, dict)],
        epic_order=epic_slugs(data),
    )


def next_rows(data: dict, key: str) -> list[dict] | None:
    """`data[key]` when it is a list of objects, else None."""
    rows = data.get(key)
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        return None
    return rows


def waiting_refs(row: dict) -> list[str] | None:
    """The refs a `next` blocked row waits on. A local id (int) means a ticket
    of the row's own epic: `<epic prefix of its ref>.<id>`. None on a wrong shape."""
    ref = row.get("ref")
    waiting_on = row.get("waiting_on", [])  # absent when blocked by `unknown` or status
    if not isinstance(ref, str) or not isinstance(waiting_on, list):
        return None
    prefix = ref.rpartition(".")[0]
    refs = []
    for item in waiting_on:
        if isinstance(item, bool) or not isinstance(item, (int, str)):
            return None
        refs.append(f"{prefix}.{item}" if isinstance(item, int) and prefix else str(item))
    return refs


def load_next(root: Path, initiative_dir: Path) -> NextInfo | None:
    """Which tickets wait on what and which are ready, from `tickets.py next`.
    None on any failure (no script, non-zero exit, bad JSON, wrong shape)."""
    script = root / "_bmad" / "method" / "scripts" / "tickets.py"
    if not script.is_file():
        return None
    output = commands.run(
        ["uv", "run", str(script), "--project-root", str(root),
         "next", str(initiative_dir)],
        root,
        commands.TICKETS_TIMEOUT,
    )
    if output is None:
        return None
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    ready_rows = next_rows(data, "ready_to_start")
    blocked_rows = next_rows(data, "blocked")
    if ready_rows is None or blocked_rows is None:
        return None

    waiting = {}
    for row in blocked_rows:
        refs = waiting_refs(row)
        if refs is None:
            return None
        if refs:
            waiting[row["ref"]] = refs
    ready = set()
    for row in ready_rows:
        if not isinstance(row.get("ref"), str):
            return None
        ready.add(row["ref"])
    return NextInfo(waiting=waiting, ready=ready)


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


def is_old(updated: datetime, now: datetime) -> bool:
    """Untouched for STALE_AFTER or longer."""
    return now - updated >= STALE_AFTER


def ticket_state(raw: dict) -> str:
    """A `tickets.py` row's state; missing or null reads as ""."""
    return str(raw.get("state") or "")


def status_word(state: str) -> str:
    """`in-progress` -> `in progress`, "" -> `no state`; other states as they are."""
    return state.replace("-", " ") if state else "no state"


def waits_on_word(refs: list[str]) -> str:
    """`waits on 3.10`, or `waits on 3.10 +1` for several."""
    extra = f" +{len(refs) - 1}" if len(refs) > 1 else ""
    return f"waits on {refs[0]}{extra}"


def ticket_word(raw: dict, next_info: NextInfo | None = None) -> str:
    """A `tickets.py status` row's word, shared by the tree and Status.
    Done and dropped read as they are; else the first match of: `blocked`,
    `waits on <refs>`, `built`, `in review`, the state word.
    Without `next_info` nothing waits."""
    state = ticket_state(raw)
    if state in ("done", "dropped"):
        return state
    if raw.get("status") == "blocked" or raw.get("blocked_at"):
        return "blocked"
    if next_info is not None:
        refs = next_info.waiting.get(str(raw.get("ref") or ""))
        if refs:
            return waits_on_word(refs)
    if raw.get("status") == "built":
        return "built"
    if raw.get("status") == "in-review":
        return "in review"
    return status_word(state)


def ticket_counts(tickets: list[dict] | None) -> tuple[int, int] | None:
    """(done, total) with dropped tickets in neither; None when tickets are unavailable."""
    if tickets is None:
        return None
    counted = [t for t in tickets if ticket_state(t) != "dropped"]
    done = sum(1 for t in counted if ticket_state(t) == "done")
    return done, len(counted)


def with_staleness(state: str, updated: datetime | None, now: datetime) -> str:
    """Open items untouched for STALE_AFTER become stale."""
    if state == OPEN and updated is not None and is_old(updated, now):
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


def walkthrough_item(
    folder: Path, output_dir: Path, now: datetime, last_commit: datetime | None = None
) -> TreeItem:
    """Name links to the `.html` page, with an `md` link to the narrative beside
    it; with no page, the name links to the narrative.

    Stale when old, or when `last_commit` is strictly newer than its updated time:
    the code it walks through has changed since."""
    updated = newest_mtime(folder)
    html = folder / f"{folder.name}.html"
    md_path = link_path(folder, output_dir)
    html_path = html.relative_to(output_dir).as_posix() if html.is_file() else None

    state = with_staleness(OPEN, updated, now)
    if last_commit is not None and updated is not None and last_commit > updated:
        state = STALE

    return TreeItem(
        kind="walk",
        name=folder.name,
        path=html_path or md_path,
        updated=updated,
        state=state,
        report=md_path if html_path else None,
        report_label="md",
    )


def doc_items(
    folder: Path, output_dir: Path, now: datetime, last_commit: datetime | None = None
) -> list[TreeItem]:
    """Forges, specs, then walkthroughs directly inside `folder`, each sorted by name."""
    return (
        [forge_item(d, output_dir, now) for d in sub_dirs(folder, "forge-")]
        + [spec_item(d, output_dir, now) for d in sub_dirs(folder, "spec-")]
        + [
            walkthrough_item(d, output_dir, now, last_commit)
            for d in sub_dirs(folder, "walkthrough-")
        ]
    )


RETRO_VERDICTS = {
    "accepted": "accepted",
    "accepted-with-open-items": "open items",
    "rejected": "rejected",
}


def retro_items(folder: Path, output_dir: Path) -> list[TreeItem]:
    """The epic folder's `epic-<slug>-retrospective.md` as a retro row, or []."""
    file = folder / f"{folder.name}-retrospective.md"
    if not file.is_file():
        return []
    verdict = frontmatter(file).get("verdict", "")
    return [TreeItem(
        kind="retro",
        name=file.stem,
        path=file.relative_to(output_dir).as_posix(),
        updated=file_mtime(file),
        state=FINISHED,  # a doc: never open, so never stale
        word=RETRO_VERDICTS.get(verdict, ""),
    )]


def plans_by_ticket(folder: Path) -> dict[str, Path]:
    """`*-plan.md` files in `folder`, keyed by their frontmatter `ticket`."""
    plans = {}
    for plan in sorted(folder.glob("*-plan.md")):
        ticket = frontmatter(plan).get("ticket")
        if ticket:
            plans.setdefault(ticket, plan)
    return plans


def make_ticket(
    raw: dict,
    folder: Path,
    output_dir: Path,
    plans: dict[str, Path],
    now: datetime,
    next_info: NextInfo | None = None,
) -> Ticket:
    """One ticket row. Its file is its plan, else its leaf file. Its updated time
    is that file's mtime, else the `tickets.toml` mtime of `folder` (its epic,
    or the initiative for initiative-level tickets)."""
    leaf_name = raw.get("file")
    leaf = folder / leaf_name if isinstance(leaf_name, str) and leaf_name else None

    plan = None
    if raw.get("id") is not None:
        plan = plans.get(str(raw["id"]))
    if plan is None and leaf is not None:
        plan = plans.get(leaf.stem)

    file = plan or (leaf if leaf is not None and leaf.is_file() else None)
    updated = file_mtime(file) if file else file_mtime(folder / "tickets.toml")

    state = ticket_state(raw)
    open_ticket = state not in ("done", "dropped")
    ref = str(raw.get("ref") or "")
    return Ticket(
        ref=ref,
        title=str(raw.get("title", "")),
        word=ticket_word(raw, next_info),
        path=file.relative_to(output_dir).as_posix() if file else None,
        updated=updated,
        stale=open_ticket and updated is not None and is_old(updated, now),
        ready=next_info is not None and ref in next_info.ready,
        blocked_reason=str(raw.get("blocked_reason") or ""),
    )


def epic_item(
    folder: Path,
    output_dir: Path,
    tickets: list[dict] | None,
    now: datetime,
    last_commit: datetime | None = None,
    next_info: NextInfo | None = None,
) -> TreeItem:
    updated = newest_mtime(folder)
    children = retro_items(folder, output_dir) + doc_items(folder, output_dir, now, last_commit)

    if tickets is None:
        # Tickets unavailable: counts unknown, so we can't call it finished.
        epic_tickets = []
        state = OPEN
        word = ""
    else:
        raw_tickets = [t for t in tickets if t.get("epic") == folder.name]
        done, total = ticket_counts(raw_tickets)
        finished = total > 0 and done == total
        state = FINISHED if finished else OPEN
        word = "" if total == 0 else ("done" if finished else "in progress")
        plans = plans_by_ticket(folder)
        epic_tickets = [make_ticket(t, folder, output_dir, plans, now, next_info) for t in raw_tickets]

    return TreeItem(
        kind="epic",
        name=folder.name,
        path=link_path(folder, output_dir),
        updated=updated,
        state=with_staleness(state, updated, now),
        children=children,
        tickets=epic_tickets,
        word=word,
    )


def build_tree(
    initiative_dir: Path,
    tickets: list[dict] | None,
    now: datetime,
    last_commit: datetime | None = None,
    next_info: NextInfo | None = None,
    epic_order: list[str] | None = None,
) -> list[TreeItem]:
    """Forges, specs, walkthroughs, then epics at initiative level; each epic
    holds its own forges, specs and walkthroughs. Link paths are relative to the
    initiative's parent (`_bmad-output/`).

    Epics follow `epic_order` (slugs in build order); folders not in it come
    after, by name. With no `epic_order`, all are by name.

    `last_commit` is the time of the initiative's last commit (None when there is
    no worktree or git failed); walkthroughs older than it are stale."""
    output_dir = initiative_dir.parent
    position = {}
    for i, slug in enumerate(epic_order or []):
        position.setdefault(slug, i)  # a repeated slug keeps its first place
    unlisted = len(epic_order or [])  # after every listed place
    epic_dirs = sorted(
        sub_dirs(initiative_dir, "epic-"),  # by name, so unlisted ones stay by name
        key=lambda d: position.get(d.name, unlisted),
    )
    epics = [
        epic_item(d, output_dir, tickets, now, last_commit, next_info)
        for d in epic_dirs
    ]
    return doc_items(initiative_dir, output_dir, now, last_commit) + epics


def initiative_tickets(
    initiative_dir: Path,
    tickets: list[dict] | None,
    now: datetime,
    next_info: NextInfo | None = None,
) -> list[Ticket]:
    """Tickets that sit directly in the initiative folder, not in an epic.
    `tickets.py` gives them the initiative folder name as their `epic`.
    Empty when tickets are unavailable."""
    if tickets is None:
        return []
    plans = plans_by_ticket(initiative_dir)
    return [
        make_ticket(t, initiative_dir, initiative_dir.parent, plans, now, next_info)
        for t in tickets
        if t.get("epic") == initiative_dir.name
    ]
