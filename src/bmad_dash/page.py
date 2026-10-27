"""Build the dashboard HTML.

The page is a server-rendered ARIA grid. Each initiative is one row of four
cells (Initiative, Project, Last activity, Actions), with its tree as a
full-width panel under it. The only script opens and closes panels, reveals
closed tickets, and remembers which panels are open.

What a row says comes from `rules.py`; this module only turns it into HTML.
The CSS and JS live in `assets/` and are inlined.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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
    action_css,
    action_prompt,
    by_activity,
    doc_actions,
    is_stale,
    item_actions,
    item_word,
    resume_prompt,
    status_class,
    ticket_action,
    tree_shows_stale,
)
from bmad_dash.tree import CLOSED_STATES, STALE, Ticket, TreeItem


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
# Its own grid: kind · name · status · updated. No indent: the docs band,
# bold epic headers and the kind column carry the grouping.

def file_href(project: str, path: str, anchor: str = "") -> str:
    """The one place a file URL is built: `/file/<project>/<path>`, plus
    `#<anchor>` when given. Not escaped."""
    url = "/file/" + quote(project) + "/" + quote(path)
    if anchor:
        url += "#" + quote(anchor)
    return url


def file_link(
    project: str, path: str, text: str, css_class: str, title: str = "", anchor: str = ""
) -> str:
    """A link to a file (see `file_href`)."""
    href = escape(file_href(project, path, anchor))
    title_attr = f' title="{escape(title)}"' if title else ""
    return f'<a class="{css_class}" href="{href}"{title_attr}>{escape(text)}</a>'


def name_cell(
    project: str,
    name: str,
    path: str | None,
    report: str | None = None,
    report_label: str = "report",
) -> str:
    """The name, linked to its file when there is one. Forges may add a `report`
    link, walkthroughs an `md` link."""
    if path is None:
        inner = f'<span class="name-text">{escape(name)}</span>'
    else:
        inner = file_link(project, path, name, "name-link")
    if report is not None:
        inner += file_link(project, report, report_label, "report-link")
    return f'<span role="cell" class="t-name">{inner}</span>'


def word_cell(word: str) -> str:
    """One status word, always grey."""
    if not word:
        return '<span role="cell"></span>'
    return f'<span role="cell" class="{MUTED}">{escape(word)}</span>'


def updated_cell(updated: datetime | None, stale: bool, now: datetime) -> str:
    if updated is None:
        return '<span role="cell" class="upd"></span>'
    tag = '<span class="stale-tag">stale</span>' if stale else ""
    return (
        f'<span role="cell" class="upd" title="{updated:%Y-%m-%d %H:%M}">'
        f'<span class="upd-time">{relative_time(updated, now)}</span>{tag}</span>'
    )


def tree_row(
    kind: str, name: str, word: str, updated: str, css_class: str = "", href: str = ""
) -> str:
    """One tree row. With `href` (the name link's URL, unescaped), the row is
    marked `opens` and carries it in `data-href`: the page script opens it on
    a click on an empty part of the row."""
    classes = ["t-row"] + ([css_class] if css_class else []) + (["opens"] if href else [])
    href_attr = f' data-href="{escape(href)}"' if href else ""
    return (
        f'<div class="{" ".join(classes)}" role="row"{href_attr}><span role="cell"'
        f' class="t-kind">{escape(kind)}</span>{name}{word}{updated}</div>'
    )


def ticket_cell(ticket: Ticket, worktree: Worktree | None) -> str:
    """The ticket's action as a link when the initiative has a worktree and
    the ticket has one, else its word as a status (never a link)."""
    action = ticket_action(ticket) if worktree is not None else None
    if worktree is None or action is None:
        css_class = status_class(ticket.word)
        return f'<span role="cell" class="{css_class}">{escape(ticket.word)}</span>'
    css_class = action_css(action)
    label = f"{action} {ticket.ref} {ticket.title}"
    link = action_link(worktree, action_prompt(ticket, action), action, css_class, label)
    return f'<span role="cell">{link}</span>'


def item_cell(item: TreeItem, epic: TreeItem | None, worktree: Worktree | None) -> str:
    """An epic's or retro's actions as links when the initiative
    has a worktree and there are any, else its word as a grey status."""
    actions = item_actions(item, epic) if worktree is not None else []
    if not actions:
        return word_cell(item_word(item, epic))
    links = [action_link(worktree, prompt, text, LINK, label)
             for text, prompt, label in actions]
    return f'<span role="cell">{"".join(links)}</span>'


ENTRY_TOOLTIP = "No plan yet: opens its entry in tickets.toml"


def ticket_name_cell(project: str, ticket: Ticket) -> str:
    """The ticket's name, linked to its plan or leaf file; with neither, to
    its entry in `tickets.toml` (CAP-14); with no entry id, plain text."""
    if ticket.path is None and ticket.entry is not None:
        toml_path, entry_id = ticket.entry
        link = file_link(project, toml_path, ticket.title, "name-link",
                         ENTRY_TOOLTIP, entry_id)
        return f'<span role="cell" class="t-name">{link}</span>'
    return name_cell(project, ticket.title, ticket.path)


def ticket_href(project: str, ticket: Ticket) -> str:
    """The URL the ticket's name links to (as `ticket_name_cell`), or "" when
    its name is plain text."""
    if ticket.path is not None:
        return file_href(project, ticket.path)
    if ticket.entry is not None:
        toml_path, entry_id = ticket.entry
        return file_href(project, toml_path, entry_id)
    return ""


def ticket_row(
    project: str,
    ticket: Ticket,
    now: datetime,
    show_stale: bool,
    worktree: Worktree | None = None,
) -> str:
    return tree_row(
        ticket.ref,
        ticket_name_cell(project, ticket),
        ticket_cell(ticket, worktree),
        updated_cell(ticket.updated, show_stale and ticket.stale, now),
        href=ticket_href(project, ticket),
    )


def closed_ticket_rows(
    project: str,
    tickets: list[Ticket],
    now: datetime,
    show_stale: bool,
    worktree: Worktree | None,
    group_id: str,
) -> list[str]:
    """Open ticket rows, then a hidden group of the done and dropped ones, then
    one button row that shows them. No button when none is closed. Each
    part keeps the tickets' order."""
    open_rows = [ticket_row(project, t, now, show_stale, worktree)
                 for t in tickets if t.word not in CLOSED_STATES]
    closed = [t for t in tickets if t.word in CLOSED_STATES]
    if not closed:
        return open_rows
    count = len(closed)
    noun = "1 closed ticket" if count == 1 else f"{count} closed tickets"
    group = "".join(ticket_row(project, t, now, show_stale, worktree) for t in closed)
    return open_rows + hidden_group(group_id, group, f"Show {noun}", "Hide closed tickets")


def hidden_group(group_id: str, group: str, show: str, hide: str,
                 role: str = ' role="rowgroup"') -> list[str]:
    """A hidden group, then one button row that shows it. The page script's
    `closed-toggle` handler swaps the label and the group's `hidden`. Pass
    `role=""` when the group holds other rowgroups: a rowgroup owns only rows."""
    button = (
        f'<button type="button" class="closed-toggle" aria-expanded="false"'
        f' aria-controls="{escape(group_id)}" data-show="{escape(show)}"'
        f' data-hide="{escape(hide)}">{escape(show)}</button>'
    )
    # Marked `t-toggle`: a click on its empty part opens nothing.
    button_row = (
        f'<div class="t-row t-toggle" role="row"><span role="cell"></span>'
        f'<span role="cell" class="t-name">{button}</span>'
        f'<span role="cell"></span><span role="cell"></span></div>'
    )
    return [f'<div{role} id="{escape(group_id)}" hidden>{group}</div>', button_row]


def is_done_epic(item: TreeItem) -> bool:
    return item.kind == "epic" and item.word in CLOSED_STATES


def done_epic_rows(
    project: str,
    epics: list[TreeItem],
    now: datetime,
    show_stale: bool,
    worktree: Worktree | None,
    group_id: str,
    closed_prefix: str,
) -> list[str]:
    """A hidden group of the done and dropped epics, each rendered as it would
    be open, then one button row that shows them. Nothing when none."""
    if not epics:
        return []
    count = len(epics)
    noun = "1 done epic" if count == 1 else f"{count} done epics"
    group = "".join(
        row for epic in epics
        for row in item_rows(project, epic, now, show_stale, worktree,
                             closed_prefix=closed_prefix)
    )
    # No role: it holds the epics' closed-tickets rowgroups.
    return hidden_group(group_id, group, f"Show {noun}", "Hide done epics", role="")


def item_rows(
    project: str,
    item: TreeItem,
    now: datetime,
    show_stale: bool,
    worktree: Worktree | None = None,
    epic: TreeItem | None = None,
    closed_prefix: str = "closed",
) -> list[str]:
    """The item's row, then its children's, then its tickets'. With
    `show_stale` off, no row gets a stale tag. An epic's closed tickets hide
    behind one button after its last ticket row; `closed_prefix` keeps the
    group's id unique on the page.

    An epic with exactly one spec nests its tickets under that spec, right
    after the spec's row (before any walkthroughs).
    """
    head = tree_row(
        item.kind,
        name_cell(project, item.name, item.path, item.report, item.report_label),
        item_cell(item, epic, worktree),
        updated_cell(item.updated, show_stale and item.state == STALE, now),
        "t-epic" if item.kind == "epic" else "",
        file_href(project, item.path) if item.path is not None else "",
    )
    group_id = f"{closed_prefix}-{item.name}"
    rows = []
    specs = [child for child in item.children if child.kind == "spec"]
    nest_under = specs[0] if len(specs) == 1 else None
    for child in item.children:
        rows += item_rows(project, child, now, show_stale, worktree, item, closed_prefix)
        if child is nest_under:
            rows += closed_ticket_rows(project, item.tickets, now, show_stale, worktree, group_id)
    if nest_under is None:
        rows += closed_ticket_rows(project, item.tickets, now, show_stale, worktree, group_id)
    return [head] + rows


TREE_HEADS = ["kind", "name", "status", "updated"]
TICKETS_FAILED = "Couldn't read tickets: tickets.py failed"


def has_panel(initiative: Initiative) -> bool:
    """A row has a tree panel (and a caret) when its tree or its
    initiative-level tickets have anything to show, or when tickets failed:
    the panel then says so."""
    return bool(initiative.tree or initiative.top_tickets or initiative.tickets is None)


def panel_content(initiative: Initiative, now: datetime) -> str:
    note = ""
    if initiative.tickets is None:
        note = f'<p class="err t-note">{TICKETS_FAILED}</p>'
    heads = "".join(f'<span role="columnheader">{h}</span>' for h in TREE_HEADS)
    rows = [f'<div class="t-row t-head" role="row">{heads}</div>']
    show_stale = tree_shows_stale(initiative)
    worktree = initiative.worktrees[0] if initiative.worktrees else None
    project = initiative.project
    closed_prefix = f"closed-{initiative.key}"
    docs = [item for item in initiative.tree if item.kind != "epic"]
    epics = [item for item in initiative.tree if item.kind == "epic"]
    # The docs band: forges, specs, walkthroughs, closed by a divider.
    doc_rows = [row for item in docs
                for row in item_rows(project, item, now, show_stale, worktree,
                                     closed_prefix=closed_prefix)]
    if doc_rows:
        rows.append(f'<div class="t-docs" role="rowgroup">{"".join(doc_rows)}</div>')
    for item in epics:
        if not is_done_epic(item):
            rows += item_rows(project, item, now, show_stale, worktree,
                              closed_prefix=closed_prefix)
    # Initiative-level tickets come after the open epics.
    rows += closed_ticket_rows(project, initiative.top_tickets, now, show_stale, worktree,
                               closed_prefix)
    # Done and dropped epics fold behind one button, the tree's last row.
    rows += done_epic_rows(project, [e for e in epics if is_done_epic(e)], now, show_stale,
                           worktree, f"done-epics-{initiative.key}", closed_prefix)
    label = escape(f"{initiative.title} contents")
    return f'{note}<div class="tree" role="table" aria-label="{label}">{"".join(rows)}</div>'


# --- row cells ---

def initiative_cell(initiative: Initiative, panel_id: str, is_open: bool) -> str:
    """Title with slug below. When there is a tree to show, one toggle button
    holds the caret and the title; else an empty caret slot, then the title
    as plain text."""
    title = escape(initiative.title)
    if has_panel(initiative):
        expanded = "true" if is_open else "false"
        heading = (
            f'<span class="ttl"><button type="button" class="toggle" aria-expanded="{expanded}"'
            f' aria-controls="{panel_id}" data-key="{escape(initiative.key)}"'
            f' aria-label="Show tree: {title}">'
            f'<span class="caret" aria-hidden="true"></span>{title}</button></span>'
        )
    else:
        heading = f'<span class="ttl"><span class="caret"></span>{title}</span>'
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


def vscode_label(worktree: Worktree, project: str) -> str:
    """Tooltip and accessible name of the VS Code icon. A worktree folder
    named like its project reads as the main checkout."""
    if worktree.path.name == project:
        return f"Open main checkout (branch {worktree.branch}) in VS Code"
    return f"Open worktree {worktree.path.name} (branch {worktree.branch}) in VS Code"


def vscode_form(worktree: Worktree, project: str, slug: str) -> str:
    """The VS Code place icon: a submit button in a form. POSTs the project,
    the worktree id (what to open) and the slug (with the project, where to
    show a failure)."""
    label = escape(vscode_label(worktree, project))
    return (
        f'<form class="vscode" method="post" action="/vscode">'
        f'<input type="hidden" name="project" value="{escape(project)}">'
        f'<input type="hidden" name="id" value="{worktree_id(worktree.path)}">'
        f'<input type="hidden" name="slug" value="{escape(slug)}">'
        f'<button type="submit" class="place" title="{label}" aria-label="{label}">'
        f"{VSCODE_ICON}</button></form>"
    )


# A slot with nothing in it: keeps the four tracks, announces nothing.
EMPTY_PLACE = '<span class="place" aria-hidden="true"></span>'


@dataclass(frozen=True)
class LocalPreview:
    """A worktree's local server: its port, and whether it answered."""

    port: int
    up: bool
    start: str | None = None  # the project's `local_start`, for `resume`


# Default `previews`: no row has a local server. Read-only, so safe as a default.
NO_PREVIEWS: Mapping[str, LocalPreview] = MappingProxyType({})

# The laptop (mockups/dashboard.html, `local`), inlined: colour follows the text.
LAPTOP_ICON = (
    '<svg class="i" aria-hidden="true" viewBox="0 0 16 16">'
    '<rect x="3" y="3" width="10" height="7" rx="1"/><path d="M1.5 12.5h13"/></svg>'
)


def local_place(preview: LocalPreview | None) -> str:
    """The local place icon. Up: a link to the server, in a new tab. Down: a
    muted laptop that isn't a link but takes focus, so its tooltip can be read.
    No preview (no config for the project): an empty slot."""
    if preview is None:
        return EMPTY_PLACE
    host = f"localhost:{preview.port}"
    if preview.up:
        label = escape(f"Open {host} (opens in new tab)")
        return (
            f'<a class="place local" href="http://{host}/" target="_blank" rel="noopener"'
            f' title="{label}" aria-label="{label}">{LAPTOP_ICON}</a>'
        )
    # No start command: `resume` offers nothing, so the tooltip doesn't either.
    label = f"{host} isn't answering"
    if preview.start:
        label += ": resume offers to start it"
    label = escape(label)
    return (
        f'<span class="place local is-down" tabindex="0" role="img"'
        f' title="{label}" aria-label="{label}">{LAPTOP_ICON}</span>'
    )


@dataclass(frozen=True)
class StagingPreview:
    """A row's staging server: its host, and whether it answered."""

    host: str
    up: bool
    start: str | None = None  # the project's `staging_start`, for `resume`


# Default `stagings`: no row has a staging server. Read-only, so safe as a default.
NO_STAGINGS: Mapping[str, StagingPreview] = MappingProxyType({})

# The globe (mockups/dashboard.html, `staging`), inlined: colour follows the text.
GLOBE_ICON = (
    '<svg class="i" aria-hidden="true" viewBox="0 0 16 16">'
    '<circle cx="8" cy="8" r="6"/>'
    '<path d="M2 8h12M8 2c1.8 1.7 2.6 3.7 2.6 6S9.8 12.3 8 14M8 2C6.2 3.7 5.4 5.7 5.4 8s.8 4.3 2.6 6"/>'
    "</svg>"
)


def staging_place(preview: StagingPreview | None) -> str:
    """The staging place icon. Up: a link to the server, in a new tab. Down: a
    muted globe that isn't a link but takes focus, so its tooltip can be read.
    No preview (no rule, no host, or no open PR): an empty slot."""
    if preview is None:
        return EMPTY_PLACE
    host = preview.host
    if preview.up:
        label = escape(f"Open staging {host} (opens in new tab)")
        return (
            f'<a class="place staging" href="{escape(f"http://{host}/")}" target="_blank"'
            f' rel="noopener" title="{label}" aria-label="{label}">{GLOBE_ICON}</a>'
        )
    label = f"{host} isn't answering (Tailscale off, or the server is building or asleep)"
    if preview.start:
        label += ": resume offers to start Tailscale"
    label = escape(label)
    return (
        f'<span class="place staging is-down" tabindex="0" role="img"'
        f' title="{label}" aria-label="{label}">{GLOBE_ICON}</span>'
    )


def project_cell(
    initiative: Initiative,
    pr: PullRequest | None = None,
    vscode_failed: str = "",
    preview: LocalPreview | None = None,
    staging: StagingPreview | None = None,
) -> str:
    """The project name, then four place slots (VS Code, local, staging, PR),
    or `no worktree`. Local shows `preview` and staging shows `staging`; each
    is empty without one. The failure message shows even when the worktree
    has gone since."""
    inner = f'<span class="proj">{escape(initiative.project)}</span>'
    if not initiative.worktrees:
        inner += '<span class="muted no-wt">no worktree</span>'
    else:
        worktree = initiative.worktrees[0]
        slots = [
            vscode_form(worktree, initiative.project, initiative.slug),
            local_place(preview),
            staging_place(staging),
            pr_link(pr) if pr is not None else EMPTY_PLACE,
        ]
        inner += f'<span class="places">{"".join(slots)}</span>'
    if vscode_failed and initiative.key == vscode_failed:
        inner += f'<span class="err">{VSCODE_FAILED}</span>'
    return f'<div class="c-proj" role="cell">{inner}</div>'


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
        # A memlog entry or plan links to its file; a commit subject is plain text.
        if activity.text and activity.path:
            inner += file_link(
                initiative.project, activity.path, activity.text, "what", activity.text
            )
        elif activity.text:
            text = escape(activity.text)
            inner += f'<span class="what" title="{text}">{text}</span>'
    return f'<div class="c-act" role="cell">{inner}</div>'


# The session bubble (mockups/dashboard.html, `session`), inlined: colour follows the text.
SESSION_ICON = (
    '<svg class="i" aria-hidden="true" viewBox="0 0 16 16">'
    '<path d="M3 3.5h10a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H7l-3 2.5v-2.5H3a1 1 0 0 1-1-1v-6a1 1 0 0 1 1-1z"/>'
    '<path d="m5.5 6.5 1.5 1.25-1.5 1.25M8.5 9h2"/></svg>'
)

# The VS Code ribbon (mockups/dashboard.html, `vscode`), inlined the same way.
VSCODE_ICON = (
    '<svg class="i" aria-hidden="true" viewBox="0 0 16 16">'
    '<path d="M11.5 1.8 14 3v10l-2.5 1.2L4.2 8.4 2 10.2 1 9.6V6.4l1-.6 2.2 1.8z"/>'
    '<path d="M11.5 1.8v12.4M4.2 7.6 11.5 4M4.2 8.4l7.3 3.6"/></svg>'
)


def claude_href(worktree: Worktree, prompt: str) -> str:
    """A `claude://` link that opens a new session in the worktree with the
    prompt prefilled. Spaces encode as %20, not `+`."""
    query = urlencode({"folder": str(worktree.path), "q": prompt}, quote_via=quote)
    return f"claude://code/new?{query}"


def action_link(
    worktree: Worktree, prompt: str, text: str, css_class: str, label: str
) -> str:
    """An action as a chip: the verb, then the session icon. `css_class` adds
    the colour (or the Resume fill)."""
    href = escape(claude_href(worktree, prompt))
    return (
        f'<a class="{css_class} chip" href="{href}" aria-label="{escape(label)}">'
        f"{escape(text)}{SESSION_ICON}</a>"
    )


def down_line(where: str, start: str | None) -> str:
    """The `resume` line for a preview that isn't answering. No (or an empty)
    start command: just say it's down."""
    if not start:
        return f"{where} isn't answering"
    return f"{where} isn't answering: offer to start it with `{start}`"


def resume_link(
    worktree: Worktree,
    slug: str,
    preview: LocalPreview | None = None,
    staging: StagingPreview | None = None,
) -> str:
    """`resume`: its prompt adds one line per preview that is down."""
    lines = [resume_prompt(slug)]
    if preview is not None and not preview.up:
        lines.append(down_line(f"localhost:{preview.port}", preview.start))
    if staging is not None and not staging.up:
        lines.append(down_line(staging.host, staging.start))
    prompt = "\n".join(lines)
    return action_link(worktree, prompt, "resume", "btn btn-1",
                       f"resume {slug} in a new session")


# GitHub's 16px octicons, path data from @primer/octicons 19.15.0 (MIT).
PR_ICONS = {
    "open": "M1.5 3.25a2.25 2.25 0 1 1 3 2.122v5.256a2.251 2.251 0 1 1-1.5 0V5.372A2.25 2.25 0 0 1 1.5 3.25Zm5.677-.177L9.573.677A.25.25 0 0 1 10 .854V2.5h1A2.5 2.5 0 0 1 13.5 5v5.628a2.251 2.251 0 1 1-1.5 0V5a1 1 0 0 0-1-1h-1v1.646a.25.25 0 0 1-.427.177L7.177 3.427a.25.25 0 0 1 0-.354ZM3.75 2.5a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Zm0 9.5a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Zm8.25.75a.75.75 0 1 0 1.5 0 .75.75 0 0 0-1.5 0Z",
    "draft": "M3.25 1A2.25 2.25 0 0 1 4 5.372v5.256a2.251 2.251 0 1 1-1.5 0V5.372A2.251 2.251 0 0 1 3.25 1Zm9.5 14a2.25 2.25 0 1 1 0-4.5 2.25 2.25 0 0 1 0 4.5ZM2.5 3.25a.75.75 0 1 0 1.5 0 .75.75 0 0 0-1.5 0ZM3.25 12a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Zm9.5 0a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5ZM14 7.5a1.25 1.25 0 1 1-2.5 0 1.25 1.25 0 0 1 2.5 0Zm0-4.25a1.25 1.25 0 1 1-2.5 0 1.25 1.25 0 0 1 2.5 0Z",
    "merged": "M5.45 5.154A4.25 4.25 0 0 0 9.25 7.5h1.378a2.251 2.251 0 1 1 0 1.5H9.25A5.734 5.734 0 0 1 5 7.123v3.505a2.25 2.25 0 1 1-1.5 0V5.372a2.25 2.25 0 1 1 1.95-.218ZM4.25 13.5a.75.75 0 1 0 0-1.5.75.75 0 0 0 0 1.5Zm8.5-4.5a.75.75 0 1 0 0-1.5.75.75 0 0 0 0 1.5ZM5 3.25a.75.75 0 1 0 0 .005V3.25Z",
    "closed": "M3.25 1A2.25 2.25 0 0 1 4 5.372v5.256a2.251 2.251 0 1 1-1.5 0V5.372A2.251 2.251 0 0 1 3.25 1Zm9.5 5.5a.75.75 0 0 1 .75.75v3.378a2.251 2.251 0 1 1-1.5 0V7.25a.75.75 0 0 1 .75-.75Zm-2.03-5.273a.75.75 0 0 1 1.06 0l.97.97.97-.97a.748.748 0 0 1 1.265.332.75.75 0 0 1-.205.729l-.97.97.97.97a.751.751 0 0 1-.018 1.042.751.751 0 0 1-1.042.018l-.97-.97-.97.97a.749.749 0 0 1-1.275-.326.749.749 0 0 1 .215-.734l.97-.97-.97-.97a.75.75 0 0 1 0-1.06ZM2.5 3.25a.75.75 0 1 0 1.5 0 .75.75 0 0 0-1.5 0ZM3.25 12a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Zm9.5 0a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Z",
}
GITHUB_FAILED = "Couldn't reach GitHub"
ROOT_FAILED = "Couldn't load"
# Default `prs`: no row has a PR. Read-only, so safe as a default argument.
NO_PRS: Mapping[str, PullRequest] = MappingProxyType({})


def pr_link(pr: PullRequest) -> str:
    """The PR place icon: its state icon alone, opening the PR in a new tab.
    The number is in the tooltip and the accessible name."""
    state = pr.state if pr.state in PR_ICONS else "open"
    colour = "pr-open" if state == "open" else "pr-muted"
    icon = (
        f'<svg class="i {colour}" aria-hidden="true" viewBox="0 0 16 16">'
        f'<path d="{PR_ICONS[state]}"/></svg>'
    )
    title = escape(f"{state.capitalize()} PR #{pr.number}, top of the stack, on GitHub")
    label = escape(f"{state} pull request #{pr.number} (opens in new tab)")
    return (
        f'<a class="place pr" href="{escape(pr.url)}" target="_blank" rel="noopener"'
        f' title="{title}" aria-label="{label}">{icon}</a>'
    )


def actions_cell(
    initiative: Initiative,
    preview: LocalPreview | None = None,
    staging: StagingPreview | None = None,
) -> str:
    """`resume` on top, the doc actions stacked under it. Empty without a
    worktree. `preview` and `staging` are the row's servers, for `resume`."""
    if not initiative.worktrees:
        return '<div class="c-btns" role="cell"></div>'
    worktree = initiative.worktrees[0]
    lines = [resume_link(worktree, initiative.slug, preview, staging)]
    for text, prompt, label in doc_actions(initiative):
        lines.append(action_link(worktree, prompt, text, LINK, label))
    return f'<div class="c-btns" role="cell">{"".join(lines)}</div>'


def initiative_row(
    initiative: Initiative,
    index: int,
    is_open: bool,
    now: datetime,
    vscode_failed: str = "",
    prs: Mapping[str, PullRequest] = NO_PRS,
    previews: Mapping[str, LocalPreview] = NO_PREVIEWS,
    stagings: Mapping[str, StagingPreview] = NO_STAGINGS,
) -> str:
    """One grid row. DOM order: the four cells, then the panel, so Tab runs
    title toggle -> VS Code -> local -> staging -> PR -> resume -> doc chips -> tree links. CSS puts the
    panel on grid row 2."""
    panel_id = f"panel-{index}"
    is_open = is_open and has_panel(initiative)
    cells = [
        initiative_cell(initiative, panel_id, is_open),
        project_cell(
            initiative,
            prs.get(initiative.key),
            vscode_failed,
            previews.get(initiative.key),
            stagings.get(initiative.key),
        ),
        activity_cell(initiative, now),
        actions_cell(
            initiative, previews.get(initiative.key), stagings.get(initiative.key)
        ),
    ]
    if has_panel(initiative):
        cells.append(panel_cell(initiative, panel_id, is_open, now))
    css_class = "init is-finished" if initiative.finished else "init"
    # The page script keeps a close-only row closed, even when saved open.
    closed_start = " data-closed-start" if only_close(initiative) else ""
    return f'<div class="{css_class}" role="row"{closed_start}>{"".join(cells)}</div>'


COLUMN_HEADS = ["Initiative", "Project", "Last activity", "Actions"]


def header_row() -> str:
    cells = []
    for head in COLUMN_HEADS:
        # The mockup shows no Actions head; keep it for screen readers only.
        text = f'<span class="sr-only">{head}</span>' if head == "Actions" else head
        cells.append(f'<div role="columnheader">{text}</div>')
    return f'<div class="col-head" role="row">{"".join(cells)}</div>'


def only_close(initiative: Initiative) -> bool:
    """The initiative's only doc action is `close`: its row always starts
    closed."""
    actions = doc_actions(initiative)
    return len(actions) == 1 and actions[0][0] == "close"


def newest_index(initiatives: list[Initiative]) -> int | None:
    """Index of the initiative with a tree and the newest activity (first on
    ties), or None. Close-only initiatives are skipped."""
    best = None
    for index, initiative in enumerate(initiatives):
        if initiative.activity is None or not has_panel(initiative) or only_close(initiative):
            continue
        if best is None or initiative.activity.when > initiatives[best].activity.when:
            best = index
    return best


def page_content(
    initiatives: list[Initiative],
    roots_text: list[str],
    now: datetime,
    vscode_failed: str = "",
    prs: Mapping[str, PullRequest] = NO_PRS,
    previews: Mapping[str, LocalPreview] = NO_PREVIEWS,
    stagings: Mapping[str, StagingPreview] = NO_STAGINGS,
) -> str:
    """Unfinished rows in the table, finished ones behind Show finished (n).
    `initiatives` come sorted; `roots_text` are already escaped, in CLI order.
    `vscode_failed` is the `<project>/<slug>` whose row shows "Couldn't open
    VS Code"."""
    if not initiatives:
        # One line per root, so each project says it is empty.
        return "\n".join(
            f'<p class="empty">No initiatives in <code>{root_text}/_bmad-output/</code>.</p>'
            for root_text in roots_text
        )

    current = [i for i in initiatives if not i.finished]
    finished = [i for i in initiatives if i.finished]
    open_index = newest_index(current)
    if current:
        rows = "\n".join(
            initiative_row(
                initiative, index, index == open_index, now, vscode_failed, prs, previews,
                stagings,
            )
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
                initiative, len(current) + index, False, now, vscode_failed, prs,
                previews, stagings,
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
    roots: list[Path],
    now: datetime | None = None,
    vscode_failed: str = "",
    prs: Mapping[str, PullRequest] = NO_PRS,
    github_failed: bool = False,
    failed_roots: Sequence[Path] = (),
    previews: Mapping[str, LocalPreview] = NO_PREVIEWS,
    stagings: Mapping[str, StagingPreview] = NO_STAGINGS,
) -> str:
    """The whole page for every root in `roots` (CLI order). `vscode_failed`
    is a `<project>/<slug>` from the URL; it only picks which row shows the
    failure message. `prs` maps an initiative key (`<project>/<slug>`) to the
    PR its row shows (from `stack.stack_prs`); a root GitHub couldn't reach
    has no keys there. `github_failed`: some root couldn't reach GitHub, so
    the header says so. `failed_roots` couldn't load at all: the header names
    each, and the empty state leaves them out. `previews` maps an initiative
    key to its local server (port and up/down), `stagings` to its staging
    server (host and up/down); a key without one shows an empty slot."""
    if now is None:
        now = datetime.now()
    roots_text = [escape(display_path(root)) for root in roots]
    loaded_text = [escape(display_path(root)) for root in roots if root not in failed_roots]
    content = page_content(
        by_activity(initiatives), loaded_text, now, vscode_failed, prs, previews,
        stagings,
    )
    paths = "".join(f'<span class="path">{text}</span>' for text in roots_text)
    errors = f'<span class="err">{GITHUB_FAILED}</span>' if github_failed else ""
    errors += "".join(
        f'<span class="err">{ROOT_FAILED} <code>{escape(display_path(root))}</code></span>'
        for root in failed_roots
    )
    storage_key = "bmad-dash:open"
    # Colour tokens (from DESIGN.md frontmatter) first, then the page's own rules.
    style = assets.read("tokens.css") + assets.read("page.css")
    # Opens and closes panels; saves open `<project>/<slug>` keys as a JSON list. An empty list
    # counts as saved. Storage can throw (blocked, private mode): it is ignored.
    script = assets.read("page.js")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>bmad-dash: {", ".join(roots_text)}</title>
<style>
{style}</style>
</head>
<body>
<main class="page" data-storage-key="{storage_key}">
<header class="hdr"><h1>Initiatives</h1>{paths}{errors}</header>
{content}
<script>
{script}</script>
</main>
</body>
</html>
"""
