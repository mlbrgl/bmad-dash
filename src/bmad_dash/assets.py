"""Read the CSS and JS files shipped in `bmad_dash/assets/`.

Pages inline them in `<style>` / `<script>`, so no route serves them.
"""

from functools import cache
from importlib.resources import files


@cache
def read(name: str) -> str:
    """The text of `assets/<name>`, read once."""
    return (files("bmad_dash") / "assets" / name).read_text(encoding="utf-8")
