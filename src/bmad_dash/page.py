"""Build the dashboard HTML.

The page is a server-rendered ARIA grid. Each initiative is one row of five
cells, with its tree as a full-width panel under it. The only script opens
and closes panels and remembers which ones are open.
"""

from datetime import datetime
from html import escape
from pathlib import Path
from urllib.parse import quote

from bmad_dash.initiatives import Initiative, Worktree, worktree_id
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


def name_cell(name: str, path: str | None, depth: int, report: str | None = None) -> str:
    """The name, linked to its file when there is one. Forges may add a `report` link."""
    if path is None:
        inner = f'<span class="name-text">{escape(name)}</span>'
    else:
        inner = file_link(path, name, "name-link")
    if report is not None:
        inner += file_link(report, "report", "report-link")
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

    An epic with exactly one spec nests its tickets under that spec.
    """
    rows = [tree_row(
        item.kind,
        name_cell(item.name, item.path, depth, item.report),
        word_cell(item.word),
        updated_cell(item.updated, item.state == STALE, now),
    )]
    for child in item.children:
        rows += item_rows(child, depth + 1, now)
    specs = [child for child in item.children if child.kind == "spec"]
    ticket_depth = depth + 2 if len(specs) == 1 else depth + 1
    rows += [ticket_row(t, ticket_depth, now) for t in item.tickets]
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
    """Title with slug below. A caret toggle when there is a tree to show."""
    title = escape(initiative.title)
    if initiative.tree:
        expanded = "true" if is_open else "false"
        heading = (
            f'<button type="button" class="toggle" aria-expanded="{expanded}"'
            f' aria-controls="{panel_id}" data-slug="{escape(initiative.slug)}">'
            f'<span class="caret" aria-hidden="true"></span>{title}</button>'
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


def worktree_block(worktree: Worktree) -> str:
    return (
        f'<div class="wt" title="{escape(str(worktree.path))}">'
        f'<span class="wt-name">{escape(worktree.path.name)}</span>'
        f'<span class="wt-branch">{escape(worktree.branch)}</span></div>'
    )


def worktree_cell(initiative: Initiative) -> str:
    if not initiative.worktrees:
        inner = '<span class="muted">no worktree</span>'
    else:
        inner = "".join(worktree_block(w) for w in initiative.worktrees)
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


def switch_form(worktree: Worktree) -> str:
    label = escape(f"Switch to {worktree.path.name}")
    return (
        f'<form class="switch" method="post" action="/switch">'
        f'<input type="hidden" name="id" value="{worktree_id(worktree.path)}">'
        f'<button type="submit" class="btn" aria-label="{label}">switch</button></form>'
    )


def actions_cell(initiative: Initiative) -> str:
    """Until story 10: one switch form per worktree."""
    forms = "".join(switch_form(w) for w in initiative.worktrees)
    return f'<div class="c-btns" role="cell">{forms}</div>'


def initiative_row(
    initiative: Initiative, index: int, is_open: bool, now: datetime
) -> str:
    """One grid row. DOM order: title, panel, then the other cells, so Tab runs
    toggle -> tree links -> actions. CSS puts the panel on grid row 2."""
    panel_id = f"panel-{index}"
    is_open = is_open and bool(initiative.tree)
    cells = [initiative_cell(initiative, panel_id, is_open)]
    if initiative.tree:
        cells.append(panel_cell(initiative, panel_id, is_open, now))
    cells += [
        worktree_cell(initiative),
        activity_cell(initiative, now),
        status_cell(initiative, now),
        actions_cell(initiative),
    ]
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

/* Initiative grid: header and rows share one column template. */
.col-head, .init { display: grid; grid-template-columns: var(--cols);
                   column-gap: 24px; align-items: start; }
.col-head { font-size: 12px; color: var(--ink-muted); padding: 0 0 8px;
            border-bottom: 1px solid var(--line); }
.init { padding: 16px 0; border-bottom: 1px solid var(--line); }
.init > [role="cell"] { grid-row: 1; }
.init > .panel { grid-row: 2; grid-column: 1 / -1; }
.c-title, .c-wt, .c-act, .wt { display: flex; flex-direction: column; min-width: 0; }
.init:has(.toggle) { cursor: pointer; }
.toggle, .ttl { display: flex; gap: 8px; align-items: baseline; font: inherit;
                font-weight: 600; font-size: 14.5px; color: inherit; text-align: left; }
.toggle { background: none; border: 0; padding: 0; cursor: pointer; }
.caret { color: var(--ink-muted); font-size: 10px; width: 10px; flex: none; }
.toggle[aria-expanded="false"] .caret::before { content: "\\25B6"; }
.toggle[aria-expanded="true"] .caret::before { content: "\\25BC"; }
.slug { font: 12px var(--mono); color: var(--ink-muted); margin: 2px 0 0 18px; }
.wt + .wt { margin-top: 6px; }
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
.c-btns { display: flex; flex-direction: column; gap: 6px; align-items: flex-end; }
form.switch { margin: 0; }
.btn { font: inherit; font-size: 13px; font-weight: 500; padding: 6px 12px;
       border-radius: 6px; white-space: nowrap; cursor: pointer;
       background: transparent; border: 1px solid var(--line); color: var(--ink); }

/* Tree panel: its own grid, same for every row. */
.panel { margin-top: 16px; padding: 8px 16px; background: var(--panel);
         border-radius: 8px; font-size: 13px; cursor: auto; }
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
    // Clicks on the row, outside links, buttons, forms and the panel, toggle too.
    var row = button.closest(".init");
    row.addEventListener("click", function (event) {
      if (event.target.closest("a, button, form, .panel")) return;
      var selection = window.getSelection();
      if (selection && selection.toString()) return;
      button.click();
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


def page_content(initiatives: list[Initiative], root_text: str, now: datetime) -> str:
    """Unfinished rows in the table, finished ones behind Show finished (n).
    `initiatives` come sorted; `root_text` is already escaped."""
    if not initiatives:
        return f'<p class="empty">No initiatives in <code>{root_text}/_bmad-output/</code>.</p>'

    current = [i for i in initiatives if not i.finished]
    finished = [i for i in initiatives if i.finished]
    open_index = newest_index(current)
    if current:
        rows = "\n".join(
            initiative_row(initiative, index, index == open_index, now)
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
            initiative_row(initiative, len(current) + index, False, now)
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
    initiatives: list[Initiative], root: Path, now: datetime | None = None
) -> str:
    if now is None:
        now = datetime.now()
    root_text = escape(display_path(root))
    content = page_content(by_activity(initiatives), root_text, now)
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
<header class="hdr"><h1>Initiatives</h1><span class="path">{root_text}</span></header>
{content}
<script>{SCRIPT}</script>
</main>
</body>
</html>
"""
