"""Build the dashboard HTML.

The page is a server-rendered ARIA grid. Each initiative is one row of five
cells, with its tree as a full-width panel under it. The only script opens
and closes panels and remembers which ones are open.

What a row says comes from `rules.py`; this module only turns it into HTML.
The CSS and JS live in `assets/` and are inlined.
"""

from collections.abc import Mapping
from datetime import datetime
from html import escape
from pathlib import Path
from types import MappingProxyType
from urllib.parse import quote, urlencode

from bmad_dash import assets
from bmad_dash.initiatives import Initiative, Worktree, worktree_id
from bmad_dash.prs import PullRequest
from bmad_dash.rules import (
    LINK,
    MUTED,
    WARM,
    StatusItem,
    action_prompt,
    by_activity,
    epic_actions,
    is_stale,
    retro_actions,
    status_class,
    status_parts,
    ticket_action,
    tree_shows_stale,
    word_class,
)
from bmad_dash.tree import FINISHED, STALE, Ticket, TreeItem


def display_path(path: Path) -> str:
    """Show paths under the home folder as `~/...`."""
    home = Path.home()
    if path == home:
        return "~"
    if home in path.parents:
        return "~/" + str(path.relative_to(home))
    return str(path)


def relative_time(when: datetime, now: datetime) -> str:
    """'just now', '27 min ago', '3 h ago' or '3 days ago'."""
    seconds = (now - when).total_seconds()
    if seconds < 60:
        return "just now"  # also covers times slightly in the future
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} h ago"
    days = hours // 24
    return "1 day ago" if days == 1 else f"{days} days ago"


# --- tree panel ---
# Its own grid: kind · name · status · updated. Nesting indents the name only.

def file_link(path: str, text: str, css_class: str) -> str:
    href = escape("/file/" + quote(path))
    return f'<a class="{css_class}" href="{href}">{escape(text)}</a>'


def name_cell(
    name: str,
    path: str | None,
    depth: int,
    report: str | None = None,
    report_label: str = "report",
    fold: str = "",
) -> str:
    """The name, linked to its file when there is one. Forges may add a `report`
    link, walkthroughs an `md` link. `fold` is an epic's caret button."""
    if path is None:
        inner = f'<span class="name-text">{escape(name)}</span>'
    else:
        inner = file_link(path, name, "name-link")
    if report is not None:
        inner += file_link(report, report_label, "report-link")
    return f'<span role="cell" class="t-name d{depth}">{fold}{inner}</span>'


def fold_button(epic: TreeItem, group_id: str) -> str:
    """An epic's caret: folds its children. Not remembered, so unlike the
    initiative's `toggle` it carries no slug."""
    expanded = "false" if epic.state == FINISHED else "true"
    return (
        f'<button type="button" class="fold" aria-expanded="{expanded}"'
        f' aria-controls="{escape(group_id)}" aria-label="Show epic: {escape(epic.name)}">'
        f'<span class="caret" aria-hidden="true"></span></button>'
    )


def word_cell(word: str, ready: bool = False, status_only: bool = False) -> str:
    """One status word, coloured by `word_class`; `ready` is a ticket's.
    `status_only` is always grey."""
    if not word:
        return '<span role="cell"></span>'
    css_class = MUTED if status_only else word_class(word, ready)
    return f'<span role="cell" class="{css_class}">{escape(word)}</span>'


def updated_cell(updated: datetime | None, stale: bool, now: datetime) -> str:
    if updated is None:
        return '<span role="cell" class="upd"></span>'
    tag = '<span class="stale-tag">stale</span>' if stale else ""
    return (
        f'<span role="cell" class="upd" title="{updated:%Y-%m-%d %H:%M}">'
        f'<span class="upd-time">{relative_time(updated, now)}</span>{tag}</span>'
    )


def tree_row(kind: str, name: str, word: str, updated: str) -> str:
    return (
        f'<div class="t-row" role="row"><span role="cell" class="t-kind">'
        f"{escape(kind)}</span>{name}{word}{updated}</div>"
    )


def ticket_cell(ticket: Ticket, worktree: Worktree | None) -> str:
    """The ticket's action as a link when the initiative has a worktree and
    the ticket has one, else its word as a status (never a link)."""
    action = ticket_action(ticket) if worktree is not None else None
    if worktree is None or action is None:
        css_class = status_class(ticket.word)
        return f'<span role="cell" class="{css_class}">{escape(ticket.word)}</span>'
    css_class = WARM if action == "unblock" else LINK
    label = f"{action} {ticket.ref} {ticket.title}"
    link = action_link(worktree, action_prompt(ticket, action), action, css_class, label)
    return f'<span role="cell">{link}</span>'


def item_cell(item: TreeItem, epic: TreeItem | None, worktree: Worktree | None) -> str:
    """An epic's or retro's actions as links split by `SEP` when the initiative
    has a worktree and there are any, else its word as a grey status."""
    actions = []
    if worktree is not None:
        if item.kind == "epic":
            actions = epic_actions(item)
        elif item.kind == "retro" and epic is not None:
            actions = retro_actions(item, epic)
    if not actions:
        return word_cell(item.word, status_only=True)
    links = [action_link(worktree, prompt, text, LINK, label)
             for text, prompt, label in actions]
    return f'<span role="cell">{SEP.join(links)}</span>'


def ticket_row(
    ticket: Ticket,
    depth: int,
    now: datetime,
    show_stale: bool,
    worktree: Worktree | None = None,
) -> str:
    return tree_row(
        ticket.ref,
        name_cell(ticket.title, ticket.path, depth),
        ticket_cell(ticket, worktree),
        updated_cell(ticket.updated, show_stale and ticket.stale, now),
    )


def item_rows(
    item: TreeItem,
    depth: int,
    now: datetime,
    show_stale: bool,
    worktree: Worktree | None = None,
    epic: TreeItem | None = None,
    fold_prefix: str = "",
) -> list[str]:
    """The item's row, then its children's, then its tickets'. With
    `show_stale` off, no row gets a stale tag. An epic's children and tickets
    sit in a row group its caret folds; `fold_prefix` keeps the group's id
    unique on the page.

    An epic with exactly one spec nests its tickets under that spec, right
    after the spec's row (before any walkthroughs).
    """
    group_id = f"{fold_prefix}-{item.name}"
    fold = fold_button(item, group_id) if item.kind == "epic" else ""
    head = tree_row(
        item.kind,
        name_cell(item.name, item.path, depth, item.report, item.report_label, fold),
        item_cell(item, epic, worktree),
        updated_cell(item.updated, show_stale and item.state == STALE, now),
    )
    rows = []
    specs = [child for child in item.children if child.kind == "spec"]
    nest_under = specs[0] if len(specs) == 1 else None
    for child in item.children:
        rows += item_rows(child, depth + 1, now, show_stale, worktree, item)
        if child is nest_under:
            rows += [ticket_row(t, depth + 2, now, show_stale, worktree) for t in item.tickets]
    if nest_under is None:
        rows += [ticket_row(t, depth + 1, now, show_stale, worktree) for t in item.tickets]
    if not fold:
        return [head] + rows
    hidden = " hidden" if item.state == FINISHED else ""
    group = f'<div role="rowgroup" id="{escape(group_id)}"{hidden}>{"".join(rows)}</div>'
    return [head, group]


TREE_HEADS = ["kind", "name", "status", "updated"]


def has_panel(initiative: Initiative) -> bool:
    """A row has a tree panel (and a caret) when its tree or its
    initiative-level tickets have anything to show."""
    return bool(initiative.tree or initiative.top_tickets)


def panel_content(initiative: Initiative, now: datetime) -> str:
    note = ""
    if initiative.tickets is None:
        note = '<p class="muted t-note">tickets unavailable</p>'
    heads = "".join(f'<span role="columnheader">{h}</span>' for h in TREE_HEADS)
    rows = [f'<div class="t-row t-head" role="row">{heads}</div>']
    show_stale = tree_shows_stale(initiative)
    worktree = initiative.worktrees[0] if initiative.worktrees else None
    for item in initiative.tree:
        rows += item_rows(item, 0, now, show_stale, worktree,
                          fold_prefix=f"fold-{initiative.slug}")
    # Initiative-level tickets come after the epics, at the top level.
    rows += [ticket_row(t, 0, now, show_stale, worktree) for t in initiative.top_tickets]
    label = escape(f"{initiative.title} contents")
    return f'{note}<div class="tree" role="table" aria-label="{label}">{"".join(rows)}</div>'


# --- row cells ---

def initiative_cell(initiative: Initiative, panel_id: str, is_open: bool) -> str:
    """Title with slug below. When there is a tree to show, a caret button
    before the title toggles it; the title itself is plain text."""
    title = escape(initiative.title)
    if has_panel(initiative):
        expanded = "true" if is_open else "false"
        caret = (
            f'<button type="button" class="toggle" aria-expanded="{expanded}"'
            f' aria-controls="{panel_id}" data-slug="{escape(initiative.slug)}"'
            f' aria-label="Show tree: {title}">'
            f'<span class="caret" aria-hidden="true"></span></button>'
        )
    else:
        caret = '<span class="caret"></span>'
    heading = f'<span class="ttl">{caret}{title}</span>'
    return (
        f'<div class="c-title" role="cell">{heading}'
        f'<span class="slug">{escape(initiative.slug)}</span></div>'
    )


def panel_cell(
    initiative: Initiative, panel_id: str, is_open: bool, now: datetime
) -> str:
    hidden = "" if is_open else " hidden"
    return (
        f'<div class="panel" role="cell" id="{panel_id}"{hidden}>'
        f"{panel_content(initiative, now)}</div>"
    )


VSCODE_FAILED = "Couldn't open VS Code"


def vscode_form(worktree: Worktree, slug: str) -> str:
    """The whole worktree block is the submit button that opens it in VS Code.
    POSTs the worktree id (what to open) and the slug (where to show a failure)."""
    return (
        f'<form class="vscode" method="post" action="/vscode">'
        f'<input type="hidden" name="id" value="{worktree_id(worktree.path)}">'
        f'<input type="hidden" name="slug" value="{escape(slug)}">'
        f'<button type="submit" class="wt" title="{escape(str(worktree.path))}">'
        f'<span class="sr-only">Open in VS Code: </span>'
        f'<span class="wt-name">{escape(worktree.path.name)}</span>'
        f'<span class="wt-branch">{escape(worktree.branch)}</span></button>'
        f"</form>"
    )


def worktree_cell(initiative: Initiative, vscode_failed: str = "") -> str:
    """The worktree (a VS Code button) or `no worktree`. The failure message
    shows even when the worktree has gone since."""
    if not initiative.worktrees:
        inner = '<span class="muted">no worktree</span>'
    else:
        inner = "".join(vscode_form(w, initiative.slug) for w in initiative.worktrees)
    if vscode_failed and initiative.slug == vscode_failed:
        inner += f'<span class="err">{VSCODE_FAILED}</span>'
    return f'<div class="c-wt" role="cell">{inner}</div>'


def activity_cell(initiative: Initiative, now: datetime) -> str:
    activity = initiative.activity
    if activity is None:
        inner = '<span class="muted">none</span>'
    else:
        when_class = "when is-stale" if is_stale(initiative, now) else "when"
        inner = (
            f'<span class="{when_class}" title="{activity.when:%Y-%m-%d %H:%M}">'
            f"{relative_time(activity.when, now)}</span>"
        )
        if activity.text:
            text = escape(activity.text)
            inner += f'<span class="what" title="{text}">{text}</span>'
    return f'<div class="c-act" role="cell">{inner}</div>'


SEP = '<span class="sep">·</span>'

TICKETS_FAILED = "Couldn't read tickets: tickets.py failed"


def status_item(item: StatusItem, worktree: Worktree | None, panel_id: str | None) -> str:
    """A count as a span; a doc action as a link with a worktree, else its old
    word as a grey status; `+n` as a button that opens the row's tree."""
    if item.action is not None:
        if worktree is None:
            return f'<span class="{MUTED}">{escape(item.fallback)}</span>'
        text, prompt, label = item.action
        return action_link(worktree, prompt, text, item.css_class, label)
    if item.text.startswith("+") and panel_id is not None:
        return (
            f'<button type="button" class="more {item.css_class}"'
            f' data-open-panel="{panel_id}">{escape(item.text)}</button>'
        )
    return f'<span class="{item.css_class}">{escape(item.text)}</span>'


def status_cell(initiative: Initiative, now: datetime, panel_id: str | None = None) -> str:
    """The Status sentence: up to 2 waiting items then `+n`, `x/y done`, stale tag.

    A finished initiative shows its status word (`done` / `dropped`) instead of
    waiting items; it comes from the initiative file, so it shows even when
    `tickets.py` failed.
    """
    waiting, done_count = status_parts(initiative)
    if initiative.finished:
        parts = [f'<span class="done-n">{escape(initiative.status)}</span>']
    elif initiative.tickets is None:
        parts = [f'<span class="err">{TICKETS_FAILED}</span>']
    else:
        worktree = initiative.worktrees[0] if initiative.worktrees else None
        parts = [status_item(item, worktree, panel_id) for item in waiting]
    if done_count is not None:  # no `x/y done` without counted tickets
        parts.append(f'<span class="done-n">{done_count[0]}/{done_count[1]} done</span>')
    tag = '<span class="stale-tag">stale</span>' if is_stale(initiative, now) else ""
    return f'<div class="c-status" role="cell">{SEP.join(parts)}{tag}</div>'


def claude_href(worktree: Worktree, prompt: str) -> str:
    """A `claude://` link that opens a new session in the worktree with the
    prompt prefilled. Spaces encode as %20, not `+`."""
    query = urlencode({"folder": str(worktree.path), "q": prompt}, quote_via=quote)
    return f"claude://code/new?{query}"


def action_link(
    worktree: Worktree, prompt: str, text: str, css_class: str, label: str
) -> str:
    href = escape(claude_href(worktree, prompt))
    return (
        f'<a class="{css_class}" href="{href}" aria-label="{escape(label)}">'
        f"{escape(text)}</a>"
    )


def resume_link(worktree: Worktree, slug: str) -> str:
    label = f"Resume work on {slug}"
    return action_link(worktree, f"/bmad Resume work on {slug}", "Resume", "btn btn-1", label)


# GitHub's 16px octicons, path data from @primer/octicons 19.15.0 (MIT).
PR_ICONS = {
    "open": "M1.5 3.25a2.25 2.25 0 1 1 3 2.122v5.256a2.251 2.251 0 1 1-1.5 0V5.372A2.25 2.25 0 0 1 1.5 3.25Zm5.677-.177L9.573.677A.25.25 0 0 1 10 .854V2.5h1A2.5 2.5 0 0 1 13.5 5v5.628a2.251 2.251 0 1 1-1.5 0V5a1 1 0 0 0-1-1h-1v1.646a.25.25 0 0 1-.427.177L7.177 3.427a.25.25 0 0 1 0-.354ZM3.75 2.5a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Zm0 9.5a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Zm8.25.75a.75.75 0 1 0 1.5 0 .75.75 0 0 0-1.5 0Z",
    "draft": "M3.25 1A2.25 2.25 0 0 1 4 5.372v5.256a2.251 2.251 0 1 1-1.5 0V5.372A2.251 2.251 0 0 1 3.25 1Zm9.5 14a2.25 2.25 0 1 1 0-4.5 2.25 2.25 0 0 1 0 4.5ZM2.5 3.25a.75.75 0 1 0 1.5 0 .75.75 0 0 0-1.5 0ZM3.25 12a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Zm9.5 0a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5ZM14 7.5a1.25 1.25 0 1 1-2.5 0 1.25 1.25 0 0 1 2.5 0Zm0-4.25a1.25 1.25 0 1 1-2.5 0 1.25 1.25 0 0 1 2.5 0Z",
    "merged": "M5.45 5.154A4.25 4.25 0 0 0 9.25 7.5h1.378a2.251 2.251 0 1 1 0 1.5H9.25A5.734 5.734 0 0 1 5 7.123v3.505a2.25 2.25 0 1 1-1.5 0V5.372a2.25 2.25 0 1 1 1.95-.218ZM4.25 13.5a.75.75 0 1 0 0-1.5.75.75 0 0 0 0 1.5Zm8.5-4.5a.75.75 0 1 0 0-1.5.75.75 0 0 0 0 1.5ZM5 3.25a.75.75 0 1 0 0 .005V3.25Z",
    "closed": "M3.25 1A2.25 2.25 0 0 1 4 5.372v5.256a2.251 2.251 0 1 1-1.5 0V5.372A2.251 2.251 0 0 1 3.25 1Zm9.5 5.5a.75.75 0 0 1 .75.75v3.378a2.251 2.251 0 1 1-1.5 0V7.25a.75.75 0 0 1 .75-.75Zm-2.03-5.273a.75.75 0 0 1 1.06 0l.97.97.97-.97a.748.748 0 0 1 1.265.332.75.75 0 0 1-.205.729l-.97.97.97.97a.751.751 0 0 1-.018 1.042.751.751 0 0 1-1.042.018l-.97-.97-.97.97a.749.749 0 0 1-1.275-.326.749.749 0 0 1 .215-.734l.97-.97-.97-.97a.75.75 0 0 1 0-1.06ZM2.5 3.25a.75.75 0 1 0 1.5 0 .75.75 0 0 0-1.5 0ZM3.25 12a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Zm9.5 0a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Z",
}
GITHUB_FAILED = "Couldn't reach GitHub"
# Default `prs`: no row has a PR. Read-only, so safe as a default argument.
NO_PRS: Mapping[str, PullRequest] = MappingProxyType({})


def pr_link(pr: PullRequest) -> str:
    """`#<number>` with its state icon; opens the PR in a new tab."""
    state = pr.state if pr.state in PR_ICONS else "open"
    colour = "pr-open" if state == "open" else "pr-muted"
    icon = (
        f'<svg class="pr-icon {colour}" aria-hidden="true" width="16" height="16"'
        f' viewBox="0 0 16 16"><path fill="currentColor" d="{PR_ICONS[state]}"/></svg>'
    )
    return (
        f'<a class="btn btn-2 pr" href="{escape(pr.url)}" target="_blank" rel="noopener">'
        f'{icon}<span class="sr-only">{state} pull request </span>#{pr.number}'
        f'<span class="sr-only"> (opens in new tab)</span></a>'
    )


def actions_cell(initiative: Initiative, pr: PullRequest | None = None) -> str:
    """Resume and, when known, the stack's last PR. Nothing without a worktree."""
    parts = []
    if initiative.worktrees:
        parts.append(resume_link(initiative.worktrees[0], initiative.slug))
        if pr is not None:
            parts.append(pr_link(pr))
    return f'<div class="c-btns" role="cell">{"".join(parts)}</div>'


def initiative_row(
    initiative: Initiative,
    index: int,
    is_open: bool,
    now: datetime,
    vscode_failed: str = "",
    prs: Mapping[str, PullRequest] | None = NO_PRS,
) -> str:
    """One grid row. DOM order: the five cells, then the panel, so Tab runs
    caret -> worktree -> Resume -> PR -> tree links. CSS puts the panel on
    grid row 2."""
    panel_id = f"panel-{index}"
    is_open = is_open and has_panel(initiative)
    cells = [
        initiative_cell(initiative, panel_id, is_open),
        worktree_cell(initiative, vscode_failed),
        activity_cell(initiative, now),
        status_cell(initiative, now, panel_id if has_panel(initiative) else None),
        actions_cell(initiative, prs.get(initiative.slug) if prs else None),
    ]
    if has_panel(initiative):
        cells.append(panel_cell(initiative, panel_id, is_open, now))
    css_class = "init is-finished" if initiative.finished else "init"
    return f'<div class="{css_class}" role="row">{"".join(cells)}</div>'


COLUMN_HEADS = ["Initiative", "Worktree", "Last activity", "Status", "Actions"]


def header_row() -> str:
    cells = []
    for head in COLUMN_HEADS:
        # The mockup shows no Actions head; keep it for screen readers only.
        text = f'<span class="sr-only">{head}</span>' if head == "Actions" else head
        cells.append(f'<div role="columnheader">{text}</div>')
    return f'<div class="col-head" role="row">{"".join(cells)}</div>'


def newest_index(initiatives: list[Initiative]) -> int | None:
    """Index of the initiative with a tree and the newest activity (first on
    ties), or None."""
    best = None
    for index, initiative in enumerate(initiatives):
        if initiative.activity is None or not has_panel(initiative):
            continue
        if best is None or initiative.activity.when > initiatives[best].activity.when:
            best = index
    return best


def page_content(
    initiatives: list[Initiative],
    root_text: str,
    now: datetime,
    vscode_failed: str = "",
    prs: Mapping[str, PullRequest] | None = NO_PRS,
) -> str:
    """Unfinished rows in the table, finished ones behind Show finished (n).
    `initiatives` come sorted; `root_text` is already escaped. `vscode_failed`
    is the slug whose row shows "Couldn't open VS Code"."""
    if not initiatives:
        return f'<p class="empty">No initiatives in <code>{root_text}/_bmad-output/</code>.</p>'

    current = [i for i in initiatives if not i.finished]
    finished = [i for i in initiatives if i.finished]
    open_index = newest_index(current)
    if current:
        rows = "\n".join(
            initiative_row(initiative, index, index == open_index, now, vscode_failed, prs)
            for index, initiative in enumerate(current)
        )
        parts = [
            '<div id="initiatives" role="table" aria-label="Initiatives">'
            f"\n{header_row()}\n{rows}\n</div>"
        ]
    else:
        parts = ['<p class="empty">Nothing in progress.</p>']

    if finished:
        # Panel ids continue after the current rows so they stay unique.
        rows = "\n".join(
            initiative_row(
                initiative, len(current) + index, False, now, vscode_failed, prs
            )
            for index, initiative in enumerate(finished)
        )
        parts.append(
            '<button type="button" class="show-finished" id="show-finished"'
            f' aria-expanded="false" aria-controls="finished">'
            f"Show finished ({len(finished)})</button>"
        )
        parts.append(
            '<div id="finished" role="table" aria-label="Finished initiatives" hidden>'
            f"\n{header_row()}\n{rows}\n</div>"
        )
    return "\n".join(parts)


def render_page(
    initiatives: list[Initiative],
    root: Path,
    now: datetime | None = None,
    vscode_failed: str = "",
    prs: Mapping[str, PullRequest] | None = NO_PRS,
) -> str:
    """The whole page. `vscode_failed` is a slug from the URL; it only picks
    which row shows the failure message. `prs` maps a slug to the PR its row
    shows (from `stack.stack_prs`). It is None when GitHub couldn't be
    reached: the header says so and no row gets a PR button."""
    if now is None:
        now = datetime.now()
    root_text = escape(display_path(root))
    content = page_content(
        by_activity(initiatives), root_text, now, vscode_failed, prs
    )
    github = f'<span class="err">{GITHUB_FAILED}</span>' if prs is None else ""
    storage_key = escape("bmad-dash:open:" + str(root))
    # Colour tokens (from DESIGN.md frontmatter) first, then the page's own rules.
    style = assets.read("tokens.css") + assets.read("page.css")
    # Opens and closes panels; saves open slugs as a JSON list. An empty list
    # counts as saved. Storage can throw (blocked, private mode): it is ignored.
    script = assets.read("page.js")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>bmad-dash: {root_text}</title>
<style>
{style}</style>
</head>
<body>
<main class="page" data-storage-key="{storage_key}">
<header class="hdr"><h1>Initiatives</h1><span class="path">{root_text}</span>{github}</header>
{content}
<script>
{script}</script>
</main>
</body>
</html>
"""
