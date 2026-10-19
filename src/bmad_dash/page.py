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
from bmad_dash.signals import open_items, ticket_counts
from bmad_dash.tree import TreeItem


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


# --- tree (story 8 replaces these) ---

def badge(state: str) -> str:
    """A coloured state label. `state` comes from our rules or from tickets.py."""
    css_class = "badge-" + "".join(c for c in state if c.isalnum() or c == "-")
    return f'<span class="badge {css_class}">{escape(state)}</span>'


def item_name(item: TreeItem) -> str:
    """The folder name, linked to its main file when there is one."""
    name = f"<code>{escape(item.name)}</code>"
    if item.path is None:
        return name
    href = "/file/" + quote(item.path)
    return f'<a href="{escape(href)}">{name}</a>'


def ticket_line(ticket: dict) -> str:
    ref = escape(str(ticket.get("ref", "")))
    title = escape(str(ticket.get("title", "")))
    state = str(ticket.get("state", ""))
    return f"<li>{ref} {title} {badge(state)}</li>"


def tree_item_html(item: TreeItem) -> str:
    parts = [escape(item.kind), item_name(item), badge(item.state)]
    if item.kind == "epic":
        if item.done is None or item.total is None:
            parts.append("?/?")
        else:
            parts.append(f"{item.done}/{item.total}")
    if item.updated is not None:
        parts.append(f'<span class="updated">{item.updated:%Y-%m-%d}</span>')

    nested = [tree_item_html(child) for child in item.children]
    nested += [ticket_line(t) for t in item.tickets]
    inner = f"<ul>{''.join(nested)}</ul>" if nested else ""
    return f"<li>{' '.join(parts)}{inner}</li>"


def panel_content(initiative: Initiative) -> str:
    note = ""
    if initiative.tickets is None:
        note = '<p class="muted">tickets unavailable</p>'
    items = "".join(tree_item_html(item) for item in initiative.tree)
    return f'{note}<ul class="tree">{items}</ul>'


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


def panel_cell(initiative: Initiative, panel_id: str, is_open: bool) -> str:
    hidden = "" if is_open else " hidden"
    return (
        f'<div class="panel" role="cell" id="{panel_id}"{hidden}>'
        f"{panel_content(initiative)}</div>"
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
        inner = (
            f'<span class="when" title="{activity.when:%Y-%m-%d %H:%M}">'
            f"{relative_time(activity.when, now)}</span>"
        )
        if activity.text:
            text = escape(activity.text)
            inner += f'<span class="what" title="{text}">{text}</span>'
    return f'<div class="c-act" role="cell">{inner}</div>'


SEP = '<span class="sep">·</span>'


def status_cell(initiative: Initiative) -> str:
    """Until story 9: `<n> open · <done>/<total> done`."""
    parts = [f"{open_items(initiative.tree)} open"]
    counts = ticket_counts(initiative.tickets)
    if counts is None:
        parts.append("tickets ?")
    else:
        done, total = counts
        if total > 0:  # no `x/y done` when there are no counted tickets
            parts.append(f"{done}/{total} done")
    return f'<div class="c-status" role="cell">{SEP.join(parts)}</div>'


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
        cells.append(panel_cell(initiative, panel_id, is_open))
    cells += [
        worktree_cell(initiative),
        activity_cell(initiative, now),
        status_cell(initiative),
        actions_cell(initiative),
    ]
    return f'<div class="init" role="row">{"".join(cells)}</div>'


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

/* Actions */
.c-btns { display: flex; flex-direction: column; gap: 6px; align-items: flex-end; }
form.switch { margin: 0; }
.btn { font: inherit; font-size: 13px; font-weight: 500; padding: 6px 12px;
       border-radius: 6px; white-space: nowrap; cursor: pointer;
       background: transparent; border: 1px solid var(--line); color: var(--ink); }

/* Tree panel (story 8 replaces the content) */
.panel { margin-top: 16px; padding: 8px 16px; background: var(--panel);
         border-radius: 8px; font-size: 13px; cursor: auto; }
.panel[hidden] { display: none; }
.tree, .tree ul { list-style: none; }
.tree ul { padding-left: 20px; }
.tree li { padding: 3px 0; }
.updated { color: var(--ink-muted); font-size: 12px; font-variant-numeric: tabular-nums; }
.badge { font-size: 12px; color: var(--ink-muted); }
.badge-open, .badge-in-progress, .badge-review, .badge-planned,
.badge-backlog { color: var(--accent); }
.badge-stale { font-size: 11px; font-weight: 600; color: var(--stale);
               background: var(--stale-soft); padding: 1px 6px; border-radius: 4px; }
"""

# Opens and closes panels; saves open slugs as a JSON list. An empty list
# counts as saved. Storage can throw (blocked, private mode): ignore it.
SCRIPT = """
(function () {
  var grid = document.getElementById("initiatives");
  var key = grid.getAttribute("data-storage-key");
  var toggles = Array.prototype.slice.call(grid.querySelectorAll("button.toggle"));

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
  if (saved !== null) {
    toggles.forEach(function (b) { setOpen(b, saved.indexOf(b.dataset.slug) !== -1); });
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
})();
"""


def render_page(
    initiatives: list[Initiative], root: Path, now: datetime | None = None
) -> str:
    if now is None:
        now = datetime.now()
    open_index = newest_index(initiatives)
    rows = "\n".join(
        initiative_row(initiative, index, index == open_index, now)
        for index, initiative in enumerate(initiatives)
    )
    root_text = escape(display_path(root))
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
<main class="page">
<header class="hdr"><h1>Initiatives</h1><span class="path">{root_text}</span></header>
<div id="initiatives" role="table" aria-label="Initiatives" data-storage-key="{storage_key}">
{header_row()}
{rows}
</div>
<script>{SCRIPT}</script>
</main>
</body>
</html>
"""
