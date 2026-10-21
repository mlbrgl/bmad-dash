import pytest

from bmad_dash.files import NotFound, Refused, render_markdown, resolve_file


def test_markdown_resolves_and_renders(files_project):
    path = resolve_file(files_project, "initiative-x/spec-x/spec-x.md")
    html = render_markdown(path)
    assert "<title>spec-x.md</title>" in html
    assert "Initiatives</a>" not in html  # no back link
    assert "<h1>Spec heading</h1>" in html
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
    assert "<h1>Spaced</h1>" in html


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
