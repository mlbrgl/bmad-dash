"""Build the dashboard HTML."""

from html import escape
from pathlib import Path

from bmad_dash.initiatives import Initiative, Worktree


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


# One (header, cell_fn) per column. Later stories append to this list.
# Each cell_fn takes an Initiative and returns already-escaped HTML.
COLUMNS = [
    ("Initiative", initiative_cell),
    ("Worktree / branch", worktrees_cell),
]

STYLE = """
body { font-family: system-ui, sans-serif; margin: 2rem; color: #222; }
table { border-collapse: collapse; }
th, td { text-align: left; vertical-align: top; padding: 0.5rem 1rem;
         border-bottom: 1px solid #ddd; }
code { font-size: 0.9em; }
.none { color: #888; }
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
