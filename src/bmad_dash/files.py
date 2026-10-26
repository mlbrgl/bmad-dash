"""Serve files from a project's `_bmad-output/` folder.

Only `.md` (rendered to HTML), `.html` (served as is) and files named
`tickets.toml` (shown as escaped text, one block per entry) are allowed,
and only when they resolve inside `_bmad-output/`.
"""

import re
import tomllib
import unicodedata
from html import escape
from pathlib import Path

from markdown_it import MarkdownIt
from mdit_py_plugins.anchors import anchors_plugin

ALLOWED_SUFFIXES = {".md", ".html"}
# The one `.toml` file served, by exact name.
TICKETS_TOML = "tickets.toml"


class Refused(Exception):
    """The path resolves outside `_bmad-output/`."""


class NotFound(Exception):
    """No such file, a folder, or a file type we don't serve."""


def split_project(projects: dict[str, Path], rel: str) -> tuple[Path, str]:
    """`<project>/<path>` -> (that project's root, `<path>`).
    Raises NotFound when the project is unknown or there is no path."""
    name, _, rest = rel.partition("/")
    if name not in projects or not rest:
        raise NotFound(rel)
    return projects[name], rest


def resolve_file(root: Path, rel: str) -> Path:
    """The real path of `rel` (relative to `<root>/_bmad-output/`).

    Raises Refused if it resolves outside `_bmad-output/`, NotFound if it is
    missing, a folder, or not a `.md` / `.html` / `tickets.toml` file.
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
    # Check both names, so e.g. a `config.toml -> spec.md` link stays 404,
    # and so do `tickets.toml -> x.md` and `alias.toml -> tickets.toml`.
    names = {Path(rel).name, target.name}
    suffixes = {Path(rel).suffix, target.suffix}
    allowed = names == {TICKETS_TOML} or suffixes <= ALLOWED_SUFFIXES
    if not target.is_file() or not allowed:
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
"""

# Only the tickets.toml page: the entry a link points at stands out.
TOML_STYLE = """
pre.entry { margin: 0 0 0.6rem; border-left: 3px solid transparent; }
pre.entry:target { background: #fff6d6; border-left-color: #e0b000; }
"""

def html_page(title: str, body: str, extra_style: str = "") -> str:
    """A full HTML page. `title` must be escaped."""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>{STYLE}{extra_style}</style>
</head>
<body>
{body}
</body>
</html>
"""


def not_found_page() -> str:
    """The plain 404 page for a missing or refused file."""
    return html_page("Not found", "<h1>Not found</h1>\n<p>No such file in _bmad-output/.</p>")


def heading_id(title: str) -> str:
    """The `id` for a heading: `Action items` -> `action-items`. Same ids as
    Python-Markdown's `toc`, which the file view used before, so old links
    keep working. A heading with no ASCII letters or digits (`## ✅`) gets
    `section`, never an empty id."""
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    words = re.sub(r"[^\w\s-]", "", ascii_title).strip().lower()
    return re.sub(r"[-\s]+", "-", words) or "section"


# CommonMark, as GitHub and VS Code render it: lists nest by 2 or 3 spaces
# and can start right under a line of text. Raw HTML passes through.
MARKDOWN = (
    MarkdownIt("commonmark")
    .enable("table")
    .use(anchors_plugin, min_level=1, max_level=6, slug_func=heading_id)
)


def render_markdown(path: Path) -> str:
    """A full HTML page for the markdown file at `path`. Each heading gets an
    `id` (e.g. `## Action items` -> `id="action-items"`), so a link can point
    at a section: `/file/<project>/<path>#action-items`. A repeated heading gets
    `action-items-1`, `action-items-2`, ..."""
    frontmatter, body = split_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    body_html = MARKDOWN.render(body)
    frontmatter_html = (
        f'<pre class="frontmatter">{escape(frontmatter)}</pre>'
        if frontmatter is not None
        else ""
    )
    return html_page(escape(path.name), f"{frontmatter_html}\n{body_html}")


def split_entries(text: str) -> list[str]:
    """The text cut before each line that is exactly `[[entry]]`. The text
    before the first one (if any) is its own block. Nothing is lost:
    joining the blocks gives the text back."""
    blocks = [""]
    for line in text.splitlines(keepends=True):
        if line.rstrip("\r\n") == "[[entry]]" and blocks[-1]:
            blocks.append("")
        blocks[-1] += line
    return [b for b in blocks if b]


def entry_id(block: str) -> str | None:
    """The `id` of the one `[[entry]]` in `block`, or None when the block
    has no entry, no id, or doesn't parse."""
    try:
        entries = tomllib.loads(block).get("entry")
        value = entries[0].get("id")
    except (tomllib.TOMLDecodeError, TypeError, IndexError, KeyError, AttributeError):
        return None
    return None if value is None else str(value)


def render_tickets_toml(path: Path) -> str:
    """A full HTML page for a `tickets.toml`: its text as written, one `<pre>`
    block per `[[entry]]`, each with `id="<entry id>"`, so
    `/file/<project>/<folder>/tickets.toml#3` scrolls to entry 3 and the CSS
    highlights it. No script: the browser does the scrolling."""
    text = path.read_text(encoding="utf-8", errors="replace")
    blocks = []
    for block in split_entries(text):
        block_id = entry_id(block) if block.startswith("[[entry]]") else None
        id_attr = f' id="{escape(block_id)}"' if block_id is not None else ""
        blocks.append(f'<pre class="entry"{id_attr}>{escape(block)}</pre>')
    return html_page(escape(path.name), "\n".join(blocks), TOML_STYLE)
