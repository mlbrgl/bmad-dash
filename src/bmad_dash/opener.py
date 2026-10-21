"""Let walkthrough pages open their `vscode://file/...` links.

Served files are sandboxed (opaque origin), and the Claude desktop browser
pane blocks every request such a page makes. So a walkthrough is served as a
small wrapper page (dashboard origin) that shows the walkthrough in an iframe
(`?frame=1`, still sandboxed by CSP). A relay script in the frame sends
`file:line` clicks to the wrapper with `postMessage`; the wrapper POSTs them
same-origin to `/open-file`.
"""

import re
from html import escape, unescape
from pathlib import Path

MESSAGE_TYPE = "bmad-dash:open-file"

# Injected into the framed walkthrough. It can't call any route itself: it
# only tells the wrapper (its parent) which link was clicked.
RELAY_SCRIPT = """
<script>
(function () {
  var LINK = /^vscode:\\/\\/file(\\/.*?)(?::(\\d+))?(?::\\d+)?$/;
  document.addEventListener("click", function (event) {
    var link = event.target.closest && event.target.closest("a");
    if (!link) return;
    // SVG links keep their target in href or xlink:href, not in .href.
    var href = link.getAttribute("href") || link.getAttribute("xlink:href") || "";
    if (href.indexOf("vscode://file/") !== 0) return;
    event.preventDefault();
    var match = null;
    try {
      match = LINK.exec(decodeURIComponent(href));
    } catch (e) {}
    // An unreadable link still reaches the wrapper, which then says it failed.
    parent.postMessage({
      type: "bmad-dash:open-file",
      path: match ? match[1] : "",
      line: match && match[2] ? match[2] : ""
    }, "*");
  }, true);
})();
</script>
"""

# Snackbar: fixed bottom-centre, ink background, surface text, rounded.md, 14px.
WRAPPER_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
html, body {{ margin: 0; height: 100%; }}
#bmad-dash-frame {{ display: block; width: 100%; height: 100vh; border: 0; }}
#bmad-dash-open-snack {{
  position: fixed; left: 50%; bottom: 24px; transform: translateX(-50%);
  z-index: 2147483647; padding: 10px 16px; border-radius: 6px;
  background: #18181B; color: #FFFFFF;
  font: 400 14px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, sans-serif;
  opacity: 0; pointer-events: none; transition: opacity 0.15s;
}}
#bmad-dash-open-snack.bmad-dash-open-snack-on {{ opacity: 1; }}
@media (prefers-color-scheme: dark) {{
  #bmad-dash-open-snack {{ background: #F4F4F5; color: #141417; }}
}}
</style>
</head>
<body>
<iframe id="bmad-dash-frame" src="{frame_src}" title="{title}"></iframe>
<div id="bmad-dash-open-snack" role="status">Couldn't open in VS Code</div>
<script>
(function () {{
  var frame = document.getElementById("bmad-dash-frame");
  var snack = document.getElementById("bmad-dash-open-snack");
  var hideTimer = null;

  function showSnack() {{
    snack.classList.add("bmad-dash-open-snack-on");
    clearTimeout(hideTimer);
    hideTimer = setTimeout(function () {{
      snack.classList.remove("bmad-dash-open-snack-on");
    }}, 3000);
  }}

  window.addEventListener("message", function (event) {{
    // Only our own iframe, and only the one message shape.
    // The framed walkthrough is sandboxed, so its origin is "null".
    if (event.source !== frame.contentWindow || event.origin !== "null") return;
    var data = event.data;
    if (!data || data.type !== "{message_type}") return;
    if (typeof data.path !== "string" || typeof data.line !== "string") return;
    if (!data.path || !/^[0-9]*$/.test(data.line)) {{
      showSnack();
      return;
    }}
    var body = new URLSearchParams({{
      page: location.pathname,
      path: data.path,
      line: data.line
    }});
    fetch("/open-file", {{ method: "POST", body: body }})
      .then(function (response) {{ if (!response.ok) showSnack(); }})
      .catch(showSnack);
  }});
}})();
</script>
</body>
</html>
"""


def is_walkthrough_page(rel: Path) -> bool:
    """True for `initiative-*/.../walkthrough-*/<name>.html`.
    `rel` is relative to `_bmad-output/`."""
    return (
        rel.suffix == ".html"
        and len(rel.parts) >= 3
        and rel.parts[0].startswith("initiative-")
        and rel.parent.name.startswith("walkthrough-")
    )


def inject_relay(html: bytes) -> bytes:
    """`html` with the relay script before its last `</body>`, else at the end."""
    script = RELAY_SCRIPT.encode("utf-8")
    at = html.lower().rfind(b"</body>")
    if at == -1:
        return html + script
    return html[:at] + script + html[at:]


def read_title(html: bytes, fallback: str) -> str:
    """The text of the page's `<title>`, or `fallback` when it has none."""
    match = re.search(rb"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if match:
        title = unescape(match.group(1).decode("utf-8", errors="replace")).strip()
        if title:
            return title
    return fallback


def wrapper_page(page_path: str, title: str) -> str:
    """The dashboard page that frames the walkthrough at `page_path`
    (a URL path like `/file/...`) and opens its links. `title` is plain text."""
    return WRAPPER_TEMPLATE.format(
        title=escape(title),
        frame_src=escape(page_path + "?frame=1"),
        message_type=MESSAGE_TYPE,
    )


def resolve_open_path(worktree: Path, raw: str, other_worktrees: list[Path]) -> Path | None:
    """The real path of `raw` if it is an existing file inside `worktree`, else None.
    `..` and symlinks are followed first, so neither can leave the worktree.
    Files under a `.git` folder, or inside another worktree nested in this
    one (e.g. `.claude/worktrees/x` under the main checkout), are refused."""
    if not raw.startswith("/"):
        return None
    try:
        target = Path(raw).resolve()
        base = worktree.resolve()
    except (OSError, ValueError):  # e.g. a NUL byte
        return None
    if not target.is_relative_to(base) or not target.is_file():
        return None
    if ".git" in target.relative_to(base).parts:
        return None
    for other in other_worktrees:
        try:
            other = other.resolve()
        except (OSError, ValueError):
            continue
        if other != base and other.is_relative_to(base) and target.is_relative_to(other):
            return None
    return target
