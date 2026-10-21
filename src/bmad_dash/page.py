"""Build the dashboard HTML.

The page is a server-rendered ARIA grid. Each initiative is one row of five
cells, with its tree as a full-width panel under it. The only script opens
and closes panels and remembers which ones are open.
"""

from collections.abc import Sequence
from datetime import datetime
from html import escape
from pathlib import Path
from urllib.parse import quote, urlencode

from bmad_dash.initiatives import Initiative, Worktree, worktree_id
from bmad_dash.prs import PullRequest, last_pr
from bmad_dash.signals import ticket_counts
from bmad_dash.tree import FINISHED, STALE, STALE_AFTER, Ticket, TreeItem, status_word


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
) -> str:
    """The name, linked to its file when there is one. Forges may add a `report`
    link, walkthroughs an `md` link."""
    if path is None:
        inner = f'<span class="name-text">{escape(name)}</span>'
    else:
        inner = file_link(path, name, "name-link")
    if report is not None:
        inner += file_link(report, report_label, "report-link")
    return f'<span role="cell" class="t-name d{depth}">{inner}</span>'


def word_cell(word: str) -> str:
    """One status word: done and dropped muted, anything else pending (accent)."""
    if not word:
        return '<span role="cell"></span>'
    css_class = "st-done" if word in ("done", "dropped") else "st-pending"
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


def ticket_row(ticket: Ticket, depth: int, now: datetime) -> str:
    return tree_row(
        ticket.ref,
        name_cell(ticket.title, ticket.path, depth),
        word_cell(ticket.word),
        updated_cell(ticket.updated, ticket.stale, now),
    )


def item_rows(item: TreeItem, depth: int, now: datetime) -> list[str]:
    """The item's row, then its children's, then its tickets'.

    An epic with exactly one spec nests its tickets under that spec, right
    after the spec's row (before any walkthroughs).
    """
    rows = [tree_row(
        item.kind,
        name_cell(item.name, item.path, depth, item.report, item.report_label),
        word_cell(item.word),
        updated_cell(item.updated, item.state == STALE, now),
    )]
    specs = [child for child in item.children if child.kind == "spec"]
    nest_under = specs[0] if len(specs) == 1 else None
    for child in item.children:
        rows += item_rows(child, depth + 1, now)
        if child is nest_under:
            rows += [ticket_row(t, depth + 2, now) for t in item.tickets]
    if nest_under is None:
        rows += [ticket_row(t, depth + 1, now) for t in item.tickets]
    return rows


TREE_HEADS = ["kind", "name", "status", "updated"]


def panel_content(initiative: Initiative, now: datetime) -> str:
    note = ""
    if initiative.tickets is None:
        note = '<p class="muted t-note">tickets unavailable</p>'
    heads = "".join(f'<span role="columnheader">{h}</span>' for h in TREE_HEADS)
    rows = [f'<div class="t-row t-head" role="row">{heads}</div>']
    for item in initiative.tree:
        rows += item_rows(item, 0, now)
    label = escape(f"{initiative.title} contents")
    return f'{note}<div class="tree" role="table" aria-label="{label}">{"".join(rows)}</div>'


# --- row cells ---

def initiative_cell(initiative: Initiative, panel_id: str, is_open: bool) -> str:
    """Title with slug below. When there is a tree to show, a caret button
    before the title toggles it; the title itself is plain text."""
    title = escape(initiative.title)
    if initiative.tree:
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

# Not-done ticket states, in the order the Status sentence lists them.
WAITING_STATES = ["review", "in-progress", "planned", "backlog"]
MAX_WAITING = 2
TICKETS_FAILED = "Couldn't read tickets: tickets.py failed"
# Shown when nothing is open but the initiative file isn't marked done.
CONFIRM_CLOSE = "confirm close or add work"


def is_stale(initiative: Initiative, now: datetime) -> bool:
    """Last activity is STALE_AFTER old or older. Finished initiatives never are."""
    activity = initiative.activity
    return (not initiative.finished and activity is not None
            and now - activity.when >= STALE_AFTER)


def has_open(items: list[TreeItem], kind: str) -> bool:
    """Any item of `kind`, at any depth, not finished (stale counts as open)."""
    return any(
        (item.kind == kind and item.state != FINISHED) or has_open(item.children, kind)
        for item in items
    )


def waiting_items(initiative: Initiative) -> list[str]:
    """Ticket counts by status word, then doc cues."""
    items = []
    for state in WAITING_STATES:
        count = sum(1 for t in initiative.tickets if t.get("state") == state)
        if count:
            items.append(f"{count} {status_word(state)}")
    if has_open(initiative.tree, "forge"):
        items.append("forge open")
    if has_open(initiative.tree, "spec"):
        items.append("spec to slice")
    return items


def status_cell(initiative: Initiative, now: datetime) -> str:
    """The Status sentence: up to 2 waiting items then `+n`, `x/y done`, stale tag.

    A finished initiative shows its status word (`done` / `dropped`) instead of
    waiting items; it comes from the initiative file, so it shows even when
    `tickets.py` failed.
    """
    if initiative.finished:
        parts = [f'<span class="done-n">{escape(initiative.status)}</span>']
    elif initiative.tickets is None:
        parts = [f'<span class="err">{TICKETS_FAILED}</span>']
    else:
        waiting = waiting_items(initiative) or [CONFIRM_CLOSE]
        shown = waiting[:MAX_WAITING]
        hidden = len(waiting) - len(shown)
        if hidden:
            shown.append(f"+{hidden}")
        parts = [f'<span class="wait">{escape(item)}</span>' for item in shown]
    counts = ticket_counts(initiative.tickets)
    if counts and counts[1] > 0:  # no `x/y done` without counted tickets
        parts.append(f'<span class="done-n">{counts[0]}/{counts[1]} done</span>')
    tag = '<span class="stale-tag">stale</span>' if is_stale(initiative, now) else ""
    return f'<div class="c-status" role="cell">{SEP.join(parts)}{tag}</div>'


def resume_href(worktree: Worktree, slug: str) -> str:
    """A `claude://` link that opens a new session in the worktree with the
    BMad resume prompt prefilled. Spaces encode as %20, not `+`."""
    query = urlencode(
        {"folder": str(worktree.path), "q": f"/bmad Resume work on {slug}"},
        quote_via=quote,
    )
    return f"claude://code/new?{query}"


def resume_link(worktree: Worktree, slug: str) -> str:
    href = escape(resume_href(worktree, slug))
    label = escape(f"Resume work on {slug}")
    return f'<a class="btn btn-1" href="{href}" aria-label="{label}">Resume</a>'


# GitHub's 16px octicons, path data from @primer/octicons 19.15.0 (MIT).
PR_ICONS = {
    "open": "M1.5 3.25a2.25 2.25 0 1 1 3 2.122v5.256a2.251 2.251 0 1 1-1.5 0V5.372A2.25 2.25 0 0 1 1.5 3.25Zm5.677-.177L9.573.677A.25.25 0 0 1 10 .854V2.5h1A2.5 2.5 0 0 1 13.5 5v5.628a2.251 2.251 0 1 1-1.5 0V5a1 1 0 0 0-1-1h-1v1.646a.25.25 0 0 1-.427.177L7.177 3.427a.25.25 0 0 1 0-.354ZM3.75 2.5a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Zm0 9.5a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Zm8.25.75a.75.75 0 1 0 1.5 0 .75.75 0 0 0-1.5 0Z",
    "draft": "M3.25 1A2.25 2.25 0 0 1 4 5.372v5.256a2.251 2.251 0 1 1-1.5 0V5.372A2.251 2.251 0 0 1 3.25 1Zm9.5 14a2.25 2.25 0 1 1 0-4.5 2.25 2.25 0 0 1 0 4.5ZM2.5 3.25a.75.75 0 1 0 1.5 0 .75.75 0 0 0-1.5 0ZM3.25 12a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Zm9.5 0a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5ZM14 7.5a1.25 1.25 0 1 1-2.5 0 1.25 1.25 0 0 1 2.5 0Zm0-4.25a1.25 1.25 0 1 1-2.5 0 1.25 1.25 0 0 1 2.5 0Z",
    "merged": "M5.45 5.154A4.25 4.25 0 0 0 9.25 7.5h1.378a2.251 2.251 0 1 1 0 1.5H9.25A5.734 5.734 0 0 1 5 7.123v3.505a2.25 2.25 0 1 1-1.5 0V5.372a2.25 2.25 0 1 1 1.95-.218ZM4.25 13.5a.75.75 0 1 0 0-1.5.75.75 0 0 0 0 1.5Zm8.5-4.5a.75.75 0 1 0 0-1.5.75.75 0 0 0 0 1.5ZM5 3.25a.75.75 0 1 0 0 .005V3.25Z",
    "closed": "M3.25 1A2.25 2.25 0 0 1 4 5.372v5.256a2.251 2.251 0 1 1-1.5 0V5.372A2.251 2.251 0 0 1 3.25 1Zm9.5 5.5a.75.75 0 0 1 .75.75v3.378a2.251 2.251 0 1 1-1.5 0V7.25a.75.75 0 0 1 .75-.75Zm-2.03-5.273a.75.75 0 0 1 1.06 0l.97.97.97-.97a.748.748 0 0 1 1.265.332.75.75 0 0 1-.205.729l-.97.97.97.97a.751.751 0 0 1-.018 1.042.751.751 0 0 1-1.042.018l-.97-.97-.97.97a.749.749 0 0 1-1.275-.326.749.749 0 0 1 .215-.734l.97-.97-.97-.97a.75.75 0 0 1 0-1.06ZM2.5 3.25a.75.75 0 1 0 1.5 0 .75.75 0 0 0-1.5 0ZM3.25 12a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Zm9.5 0a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Z",
}
GITHUB_FAILED = "Couldn't reach GitHub"


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


def row_pr(initiative: Initiative, prs: Sequence[PullRequest] | None) -> PullRequest | None:
    """The last PR of the stack starting at the row's worktree branch, or None."""
    if not prs or not initiative.worktrees:
        return None
    branch = initiative.worktrees[0].branch
    if branch == "detached":
        return None
    return last_pr(list(prs), branch)


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
    prs: Sequence[PullRequest] | None = (),
) -> str:
    """One grid row. DOM order: the five cells, then the panel, so Tab runs
    caret -> worktree -> Resume -> PR -> tree links. CSS puts the panel on
    grid row 2."""
    panel_id = f"panel-{index}"
    is_open = is_open and bool(initiative.tree)
    cells = [
        initiative_cell(initiative, panel_id, is_open),
        worktree_cell(initiative, vscode_failed),
        activity_cell(initiative, now),
        status_cell(initiative, now),
        actions_cell(initiative, row_pr(initiative, prs)),
    ]
    if initiative.tree:
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


def by_activity(initiatives: list[Initiative]) -> list[Initiative]:
    """Newest last activity first, then those without activity.
    Sorting is stable, so ties keep their incoming (slug) order."""
    active = [i for i in initiatives if i.activity is not None]
    quiet = [i for i in initiatives if i.activity is None]
    return sorted(active, key=lambda i: i.activity.when, reverse=True) + quiet


def newest_index(initiatives: list[Initiative]) -> int | None:
    """Index of the initiative with a tree and the newest activity (first on
    ties), or None."""
    best = None
    for index, initiative in enumerate(initiatives):
        if initiative.activity is None or not initiative.tree:
            continue
        if best is None or initiative.activity.when > initiatives[best].activity.when:
            best = index
    return best


# Tokens from DESIGN.md frontmatter.
STYLE = """
:root {
  --surface: #FFFFFF; --surface-outer: #F4F4F5; --panel: #F0F0F2; --line: #E4E4E7;
  --ink: #18181B; --ink-2: #52525B; --ink-muted: #6B6B74;
  --accent: #2563EB; --accent-fg: #FFFFFF; --accent-soft: #EFF4FF;
  --stale: #A34A06; --stale-soft: #FEF6E7;
  --cols: minmax(0, 2.4fr) 170px minmax(0, 1.8fr) minmax(0, 2.2fr) 150px;
  --tcols: 64px minmax(0, 1fr) 120px 110px;
  --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, sans-serif;
  --mono: ui-monospace, SFMono-Regular, Menlo, monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    --surface: #141417; --surface-outer: #0B0B0D; --panel: #1F1F24; --line: #2A2A30;
    --ink: #F4F4F5; --ink-2: #A1A1AA; --ink-muted: #8B8B94;
    --accent: #6B9BFF; --accent-fg: #0B0B0D; --accent-soft: #18233A;
    --stale: #F0B060; --stale-soft: #2E2414;
  }
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--surface-outer); color: var(--ink);
       font: 14px/1.45 var(--sans); padding: 32px 24px 64px; }
a { color: inherit; text-decoration: none; }
a:hover { text-decoration: underline; }
a:focus-visible, button:focus-visible {
  outline: 2px solid var(--accent); outline-offset: 2px;
}
code { font-family: var(--mono); font-size: 12px; }
.muted { color: var(--ink-muted); }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden;
           clip: rect(0 0 0 0); white-space: nowrap; }

.page { max-width: 1200px; margin: 0 auto; background: var(--surface);
        border: 1px solid var(--line); border-radius: 10px; padding: 36px 40px 28px; }
.hdr { display: flex; align-items: baseline; gap: 14px; margin-bottom: 22px; }
.hdr h1 { font-size: 24px; font-weight: 600; letter-spacing: -0.01em; }
.path { color: var(--ink-muted); font: 12px var(--mono); }
.hdr .err { margin-left: auto; font-size: 13px; }

/* Initiative grid: header and rows share one column template. */
.col-head, .init { display: grid; grid-template-columns: var(--cols);
                   column-gap: 24px; align-items: start; }
.col-head { font-size: 12px; color: var(--ink-muted); padding: 0 0 8px;
            border-bottom: 1px solid var(--line); }
.init { padding: 16px 0; border-bottom: 1px solid var(--line); }
.init > [role="cell"] { grid-row: 1; }
.init > .panel { grid-row: 2; grid-column: 1 / -1; }
.c-title, .c-wt, .c-act, .wt { display: flex; flex-direction: column; min-width: 0; }
.ttl { display: flex; gap: 8px; align-items: baseline; font-weight: 600; font-size: 14.5px; }
.caret { color: var(--ink-muted); font-size: 10px; width: 10px; flex: none; }
/* A 24x24 hit area around the 10px caret, without moving the title. */
.toggle { display: flex; align-items: center; justify-content: center; flex: none;
          width: 24px; height: 24px; margin: -5px -7px; background: none; border: 0;
          padding: 0; border-radius: 4px; cursor: pointer; }
.toggle:hover .caret { color: var(--ink); }
.toggle[aria-expanded="false"] .caret::before { content: "\\25B6"; }
.toggle[aria-expanded="true"] .caret::before { content: "\\25BC"; }
.slug { font: 12px var(--mono); color: var(--ink-muted); margin: 2px 0 0 18px; }
form.vscode { margin: 0; min-width: 0; }
form.vscode + form.vscode { margin-top: 6px; }
/* The whole worktree block is a button that opens VS Code. */
.wt { width: 100%; background: none; border: 0; padding: 0; font: inherit;
      color: inherit; text-align: left; cursor: pointer; }
.wt:hover .wt-name { text-decoration: underline; }
.c-wt .err { font-size: 12px; margin-top: 2px; }
.wt-name { font: 13px var(--mono); }
.wt-branch { font-size: 12px; color: var(--ink-muted); margin-top: 2px; }
.c-wt .muted, .c-act .muted { font-size: 13px; }
.slug, .wt-name, .wt-branch, .what { overflow: hidden; text-overflow: ellipsis;
                                     white-space: nowrap; }
.when { font-size: 13px; font-variant-numeric: tabular-nums; }
.what { font-size: 12px; color: var(--ink-2); margin-top: 2px; }
.c-status { font-size: 13px; font-variant-numeric: tabular-nums; }
.sep { color: var(--ink-muted); padding: 0 4px; }
.wait { color: var(--accent); }
.done-n { color: var(--ink-muted); }
.err { color: var(--ink-2); }
.when.is-stale { color: var(--stale); }
.is-finished, .is-finished .what, .is-finished .wait { color: var(--ink-muted); }

/* Empty states and the Show finished link */
.empty { padding: 16px 0; color: var(--ink-2); }
.show-finished { margin-top: 18px; padding: 0; background: none; border: 0;
                 font: inherit; font-size: 13px; color: var(--ink-2); cursor: pointer; }
.show-finished:hover { text-decoration: underline; }
#finished { margin-top: 8px; }

/* Actions */
/* Buttons side by side on one line, right-aligned. Like the mockup, the pair
   may be a few px wider than the column and spill into the gap on its left. */
.c-btns { display: flex; justify-content: flex-end; align-items: center; gap: 6px; }
.btn { display: inline-block; font: inherit; font-size: 13px; font-weight: 500;
       line-height: 1.45; padding: 6px 12px; border-radius: 6px; white-space: nowrap;
       cursor: pointer; border: 1px solid transparent; }
.btn:hover { text-decoration: none; }
.btn-1 { background: var(--accent); color: var(--accent-fg); }
.btn-2 { background: transparent; border-color: var(--line); color: var(--ink); }
.pr-icon { vertical-align: -3px; margin-right: 4px; }
.pr-open { color: var(--accent); }
.pr-muted { color: var(--ink-muted); }

/* Tree panel: its own grid, same for every row. */
.panel { margin-top: 16px; padding: 8px 16px; background: var(--panel);
         border-radius: 8px; font-size: 13px; }
.panel[hidden] { display: none; }
.t-note { font-size: 12px; padding: 4px 0 6px; }
.t-row { display: grid; grid-template-columns: var(--tcols); column-gap: 16px;
         align-items: baseline; padding: 6px 0; }
.t-head { font-size: 12px; color: var(--ink-muted); border-bottom: 1px solid var(--line);
          padding-bottom: 6px; margin-bottom: 2px; }
.t-kind { font: 12px var(--mono); color: var(--ink-muted); min-width: 0; overflow: hidden;
          text-overflow: ellipsis; white-space: nowrap; }
/* Only the name truncates; the report link stays visible. */
.t-name { display: flex; align-items: baseline; min-width: 0; }
.name-link, .name-text { min-width: 0; overflow: hidden; text-overflow: ellipsis;
                         white-space: nowrap; }
.d1 { padding-left: 20px; } .d2 { padding-left: 40px; } .d3 { padding-left: 60px; }
.name-link { color: var(--ink); }
.report-link { flex: none; color: var(--ink-muted); margin-left: 8px; font-size: 12px; }
.st-pending { color: var(--accent); }
.st-done { color: var(--ink-muted); }
/* The time never breaks; the stale tag wraps below it when the column is too narrow. */
.upd { font-size: 12px; color: var(--ink-muted); font-variant-numeric: tabular-nums; }
.upd-time { white-space: nowrap; }
.stale-tag { display: inline-block; margin-left: 8px; font-size: 11px; font-weight: 600; color: var(--stale);
             background: var(--stale-soft); padding: 1px 6px; border-radius: 4px; }
"""

# Opens and closes panels; saves open slugs as a JSON list. An empty list
# counts as saved. Storage can throw (blocked, private mode): ignore it.
SCRIPT = """
(function () {
  var key = document.querySelector("[data-storage-key]").getAttribute("data-storage-key");
  var toggles = Array.prototype.slice.call(document.querySelectorAll("button.toggle"));

  function isOpen(button) {
    return button.getAttribute("aria-expanded") === "true";
  }
  function setOpen(button, open) {
    button.setAttribute("aria-expanded", open ? "true" : "false");
    document.getElementById(button.getAttribute("aria-controls")).hidden = !open;
  }
  function save() {
    var slugs = toggles.filter(isOpen).map(function (b) { return b.dataset.slug; });
    try { localStorage.setItem(key, JSON.stringify(slugs)); } catch (e) {}
  }

  var saved = null;
  try {
    var raw = localStorage.getItem(key);
    if (raw !== null) {
      var value = JSON.parse(raw);
      if (Array.isArray(value)) saved = value;
    }
  } catch (e) {}
  // Finished rows always start closed.
  if (saved !== null) {
    toggles.forEach(function (b) {
      var finished = b.closest(".is-finished") !== null;
      setOpen(b, !finished && saved.indexOf(b.dataset.slug) !== -1);
    });
  }

  toggles.forEach(function (button) {
    button.addEventListener("click", function () {
      setOpen(button, !isOpen(button));
      save();
    });
  });

  // Show finished (n) <-> Hide finished. Not remembered.
  var more = document.getElementById("show-finished");
  if (more) {
    var label = more.textContent;
    more.addEventListener("click", function () {
      var open = !isOpen(more);
      setOpen(more, open);
      more.textContent = open ? "Hide finished" : label;
    });
  }
})();
"""


def page_content(
    initiatives: list[Initiative],
    root_text: str,
    now: datetime,
    vscode_failed: str = "",
    prs: Sequence[PullRequest] | None = (),
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
    prs: Sequence[PullRequest] | None = (),
) -> str:
    """The whole page. `vscode_failed` is a slug from the URL; it only picks
    which row shows the failure message. `prs` is None when GitHub couldn't be
    reached: the header says so and no row gets a PR button."""
    if now is None:
        now = datetime.now()
    root_text = escape(display_path(root))
    content = page_content(
        by_activity(initiatives), root_text, now, vscode_failed, prs
    )
    github = f'<span class="err">{GITHUB_FAILED}</span>' if prs is None else ""
    storage_key = escape("bmad-dash:open:" + str(root))
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>bmad-dash: {root_text}</title>
<style>{STYLE}</style>
</head>
<body>
<main class="page" data-storage-key="{storage_key}">
<header class="hdr"><h1>Initiatives</h1><span class="path">{root_text}</span>{github}</header>
{content}
<script>{SCRIPT}</script>
</main>
</body>
</html>
"""
