import pytest

from bmad_dash.files import NotFound, Refused, render_markdown, resolve_file


def test_markdown_resolves_and_renders(files_project):
    path = resolve_file(files_project, "initiative-x/spec-x/spec-x.md")
    html = render_markdown(path)
    assert "<title>spec-x.md</title>" in html
    assert "Initiatives</a>" not in html  # no back link
    assert '<h1 id="spec-heading">Spec heading</h1>' in html
    assert "<table>" in html and "<td>one</td>" in html
    assert '<code class="language-python">' in html
    # Frontmatter is a block, escaped, and not rendered as body text.
    assert '<pre class="frontmatter">' in html
    assert "Spec &lt;b&gt;&amp;&lt;/b&gt; x" in html
    assert "<b>&</b>" not in html
    assert "<hr" not in html


def test_markdown_without_frontmatter(files_project):
    html = render_markdown(resolve_file(files_project, "initiative-x/with space.md"))
    assert '<pre class="frontmatter">' not in html
    assert '<h1 id="spaced">Spaced</h1>' in html


def test_html_resolves(files_project):
    path = resolve_file(files_project, "initiative-x/forge-x/forge-report.html")
    assert path.name == "forge-report.html"


@pytest.mark.parametrize("rel", ["../secret.md", "a/../../secret.md", "../nope.md", "/etc/hosts"])
def test_outside_is_refused(files_project, rel):
    with pytest.raises(Refused):
        resolve_file(files_project, rel)


def test_symlink_escape_is_refused(files_project):
    with pytest.raises(Refused):
        resolve_file(files_project, "initiative-x/link-out.md")


def test_symlink_inside_is_served(files_project):
    path = resolve_file(files_project, "initiative-x/link-in.md")
    assert path.name == "spec-x.md"


@pytest.mark.parametrize("rel", [
    "initiative-x/missing.md",
    "initiative-x",
    "initiative-x/spec-x",
    "",
    "initiative-x/config.toml",
    "initiative-x/data.json",
    "initiative-x/noext",
    "initiative-x/a\x00b.md",
])
def test_not_found(files_project, rel):
    with pytest.raises(NotFound):
        resolve_file(files_project, rel)


def test_non_utf8_markdown_renders(files_project):
    path = files_project / "_bmad-output" / "initiative-x" / "bad.md"
    path.write_bytes(b"\xff\xfe# Bad bytes\n")
    html = render_markdown(resolve_file(files_project, "initiative-x/bad.md"))
    assert "Bad bytes" in html


def test_symlink_with_other_suffix_is_not_found(files_project):
    out = files_project / "_bmad-output" / "initiative-x"
    (out / "alias.toml").symlink_to(out / "spec-x" / "spec-x.md")
    with pytest.raises(NotFound):
        resolve_file(files_project, "initiative-x/alias.toml")


def test_markdown_headings_get_anchors(tmp_path):
    page = tmp_path / "retro.md"
    page.write_text("# Retro\n\n## Action items\n\n## Action items\n", encoding="utf-8")
    html = render_markdown(page)
    assert '<h2 id="action-items">Action items</h2>' in html
    # A repeated heading gets its own id, so each section stays linkable.
    assert '<h2 id="action-items-1">Action items</h2>' in html


def test_markdown_nests_lists_indented_by_two_or_three_spaces(tmp_path):
    page = tmp_path / "lists.md"
    page.write_text(
        "- outer\n"
        "  - two spaces\n"
        "\n"
        "Between the lists.\n"
        "\n"
        "1. first\n"
        "   1. three spaces\n"
        "\n"
        "```\n"
        "fenced\n"
        "```\n"
        "\n"
        "    indented code\n",
        encoding="utf-8",
    )
    html = render_markdown(page)
    assert "<li>outer\n<ul>\n<li>two spaces</li>" in html
    assert "<li>first\n<ol>\n<li>three spaces</li>" in html
    # Code still renders as code.
    assert "<pre><code>fenced\n</code></pre>" in html
    assert "<pre><code>indented code\n</code></pre>" in html


def test_markdown_list_starts_right_under_a_line_of_text(tmp_path):
    # As BMad plans write it: a bold label, then a list with no blank line.
    # And an item's own paragraph, then the next item with no blank line.
    page = tmp_path / "plan.md"
    page.write_text(
        "**Approach:**\n"
        "- one\n"
        "\n"
        "5. five\n"
        "\n"
        "   Owner: build.\n"
        "6. six\n"
        "   - sub\n",
        encoding="utf-8",
    )
    html = render_markdown(page)
    assert "<p><strong>Approach:</strong></p>\n<ul>\n<li>one</li>" in html
    assert "<p>Owner: build.</p>\n</li>\n<li>\n<p>six</p>\n<ul>\n<li>sub</li>" in html


def test_markdown_heading_ids_match_the_old_renderer(tmp_path):
    # Python-Markdown's toc ids, so links written before the switch still land.
    page = tmp_path / "steps.md"
    page.write_text("## Step 1 — Clarify & route\n\n## Café `code`\n", encoding="utf-8")
    html = render_markdown(page)
    assert 'id="step-1-clarify-route"' in html
    assert 'id="cafe-code"' in html


def test_markdown_heading_without_ascii_words_gets_an_id(tmp_path):
    page = tmp_path / "emoji.md"
    page.write_text("## ✅\n\n## 日本語\n", encoding="utf-8")
    html = render_markdown(page)
    assert 'id=""' not in html
    assert '<h2 id="section">✅</h2>' in html
    assert '<h2 id="section-1">日本語</h2>' in html
