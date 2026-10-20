"""Serve files from a project's `_bmad-output/` folder.

Only `.md` (rendered to HTML) and `.html` (served as is) files are allowed,
and only when they resolve inside `_bmad-output/`.
"""

from html import escape
from pathlib import Path

import markdown

ALLOWED_SUFFIXES = {".md", ".html"}


class Refused(Exception):
    """The path resolves outside `_bmad-output/`."""


class NotFound(Exception):
    """No such file, a folder, or a file type we don't serve."""


def resolve_file(root: Path, rel: str) -> Path:
    """The real path of `rel` (relative to `<root>/_bmad-output/`).

    Raises Refused if it resolves outside `_bmad-output/`, NotFound if it is
    missing, a folder, or not a `.md` / `.html` file.
    """
    base = (root / "_bmad-output").resolve()
    # resolve() follows symlinks and collapses "..", so both escapes fail here.
    # Checked before existence, so an outside path is refused even if missing.
    try:
        target = (base / rel).resolve()
    except (OSError, ValueError):  # e.g. a NUL byte from "%00"
        raise NotFound(rel)
    if not target.is_relative_to(base):
        raise Refused(rel)
    # Check both names, so e.g. a `config.toml -> spec.md` link stays 404.
    suffixes = {Path(rel).suffix, target.suffix}
    if not target.is_file() or not suffixes <= ALLOWED_SUFFIXES:
        raise NotFound(rel)
    return target


def split_frontmatter(text: str) -> tuple[str | None, str]:
    """(frontmatter, body). Frontmatter is None when the file has none."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None, text
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "".join(lines[1:i]), "".join(lines[i + 1:])
    return None, text  # no closing "---": treat it all as body


STYLE = """
body { font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 60rem;
       padding: 0 1rem; color: #222; line-height: 1.5; }
table { border-collapse: collapse; }
th, td { text-align: left; vertical-align: top; padding: 0.3rem 0.8rem;
         border: 1px solid #ddd; }
pre { background: #f5f5f5; padding: 0.8rem; overflow-x: auto; }
pre.frontmatter { border-left: 3px solid #bbb; }
code { font-size: 0.9em; }
a.back { color: #52525B; text-decoration: none; }
a.back:hover { text-decoration: underline; }
"""

BACK_LINK = '<p><a class="back" href="/">&larr; Initiatives</a></p>'


def html_page(title: str, body: str) -> str:
    """A full HTML page with the back link on top. `title` must be escaped."""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>{STYLE}</style>
</head>
<body>
{BACK_LINK}
{body}
</body>
</html>
"""


def not_found_page() -> str:
    """The plain 404 page for a missing or refused file."""
    return html_page("Not found", "<h1>Not found</h1>\n<p>No such file in _bmad-output/.</p>")


def render_markdown(path: Path) -> str:
    """A full HTML page for the markdown file at `path`."""
    frontmatter, body = split_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    body_html = markdown.markdown(body, extensions=["tables", "fenced_code"])
    frontmatter_html = (
        f'<pre class="frontmatter">{escape(frontmatter)}</pre>'
        if frontmatter is not None
        else ""
    )
    return html_page(escape(path.name), f"{frontmatter_html}\n{body_html}")
