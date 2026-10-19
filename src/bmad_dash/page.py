"""Build the dashboard HTML."""

from html import escape
from pathlib import Path
from urllib.parse import quote

from bmad_dash.initiatives import Initiative, Worktree
from bmad_dash.tree import TreeItem, count_items


def display_path(path: Path) -> str:
    """Show paths under the home folder as `~/...`."""
    home = Path.home()
    if path == home:
        return "~"
    if home in path.parents:
        return "~/" + str(path.relative_to(home))
    return str(path)


def initiative_cell(initiative: Initiative) -> str:
    return (
        f"<strong>{escape(initiative.title)}</strong>"
        f"<br><code>{escape(initiative.slug)}</code>"
    )


def worktree_line(worktree: Worktree) -> str:
    return (
        f"<code>{escape(display_path(worktree.path))}</code>"
        f" on <code>{escape(worktree.branch)}</code>"
    )


def worktrees_cell(initiative: Initiative) -> str:
    if not initiative.worktrees:
        return '<span class="none">none</span>'
    return "<br>".join(worktree_line(w) for w in initiative.worktrees)


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


def tree_cell(initiative: Initiative) -> str:
    if not initiative.tree:
        return '<span class="none">nothing yet</span>'
    count = count_items(initiative.tree)
    noun = "item" if count == 1 else "items"
    items = "".join(tree_item_html(item) for item in initiative.tree)
    note = ""
    if initiative.tickets is None:
        note = '<p class="none">tickets unavailable</p>'
    return (
        f"<details><summary>{count} {noun}</summary>"
        f'{note}<ul class="tree">{items}</ul></details>'
    )


# One (header, cell_fn) per column. Later stories append to this list.
# Each cell_fn takes an Initiative and returns already-escaped HTML.
COLUMNS = [
    ("Initiative", initiative_cell),
    ("Worktree / branch", worktrees_cell),
    ("Contents", tree_cell),
]

STYLE = """
body { font-family: system-ui, sans-serif; margin: 2rem; color: #222; }
table { border-collapse: collapse; }
th, td { text-align: left; vertical-align: top; padding: 0.5rem 1rem;
         border-bottom: 1px solid #ddd; }
code { font-size: 0.9em; }
.none { color: #888; }
.tree, .tree ul { margin: 0.25rem 0; padding-left: 1.2rem; }
.updated { color: #888; font-size: 0.85em; }
.badge { font-size: 0.8em; padding: 0 0.4em; border-radius: 0.6em;
         background: #eee; color: #444; }
.badge-open, .badge-in-progress, .badge-review, .badge-planned,
.badge-backlog { background: #dbeafe; color: #1e40af; }
.badge-finished, .badge-done { background: #dcfce7; color: #166534; }
.badge-stale { background: #fef3c7; color: #92400e; }
.badge-dropped { background: #f3f4f6; color: #9ca3af; text-decoration: line-through; }
"""


def render_page(initiatives: list[Initiative], root: Path) -> str:
    header_cells = "".join(f"<th>{escape(header)}</th>" for header, _ in COLUMNS)
    rows = "\n".join(
        "<tr>" + "".join(f"<td>{cell(i)}</td>" for _, cell in COLUMNS) + "</tr>"
        for i in initiatives
    )
    root_text = escape(display_path(root))
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>bmad-dash: {root_text}</title>
<style>{STYLE}</style>
</head>
<body>
<h1>Initiatives in <code>{root_text}</code></h1>
<table>
<thead><tr>{header_cells}</tr></thead>
<tbody>
{rows}
</tbody>
</table>
</body>
</html>
"""
