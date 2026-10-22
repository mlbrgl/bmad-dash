import subprocess
import threading
from pathlib import Path

import pytest

from bmad_dash.server import make_server


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def write_config(worktree: Path, text: str) -> None:
    config = worktree / "_bmad" / "custom" / "config.user.toml"
    config.parent.mkdir(parents=True)
    config.write_text(text)


def write_initiative(root: Path, slug: str, text: str | None) -> None:
    folder = root / "_bmad-output" / slug
    folder.mkdir(parents=True)
    if text is not None:
        (folder / f"{slug}.md").write_text(text)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A git repo with worktrees and initiatives covering the plan's matrix.

    - main checkout (branch main)  -> initiative-shared
    - wt-a (branch feature-a)      -> initiative-matched
    - wt-b (detached)              -> initiative-shared
    - wt-c                         -> invalid TOML (ignored)
    - wt-d                         -> config without active_initiative (ignored)
    - wt-e                         -> no config at all (ignored)
    """
    root = tmp_path.resolve() / "project"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit",
        "--allow-empty", "-m", "init")

    worktrees = root.parent / "worktrees"
    git(root, "worktree", "add", "-b", "feature-a", str(worktrees / "wt-a"))
    git(root, "worktree", "add", "--detach", str(worktrees / "wt-b"))
    for name in ["wt-c", "wt-d", "wt-e"]:
        git(root, "worktree", "add", "-b", name, str(worktrees / name))

    write_config(root, '[core]\nactive_initiative = "initiative-shared"\n')
    write_config(worktrees / "wt-a", '[core]\nactive_initiative = "initiative-matched"\n')
    write_config(worktrees / "wt-b", "[core]\nactive_initiative = 'initiative-shared'\n")
    write_config(worktrees / "wt-c", "[core\nnot toml = = \n")
    write_config(worktrees / "wt-d", '[core]\nother = "x"\n')

    write_initiative(root, "initiative-matched",
                     '---\ntype: initiative\ntitle: "Matched one"\n---\nBody\n')
    write_initiative(root, "initiative-shared",
                     "---\ntitle: 'Shared <b>&</b> one'\n---\n")
    write_initiative(root, "initiative-orphan", '---\ntitle: "Nobody here"\n---\n')
    write_initiative(root, "initiative-untitled", "---\ntype: initiative\n---\ntitle: not frontmatter\n")
    write_initiative(root, "initiative-nofile", None)
    (root / "_bmad-output" / "planning-artifacts").mkdir()
    return root


SPEC_MD = """---
title: 'Spec <b>&</b> x'
status: draft
---
# Spec heading

| Col A | Col B |
|-------|-------|
| one   | two   |

```python
print("hi")
```
"""

REPORT_HTML = "<!doctype html><html><head><style>h1{color:red}</style></head><body><h1>Report</h1></body></html>\n"


@pytest.fixture
def files_project(project: Path) -> Path:
    """`project` plus files under `_bmad-output/` for the /file/ route.

    - initiative-x/spec-x/spec-x.md        markdown with frontmatter
    - initiative-x/forge-x/forge-report.html
    - initiative-x/with space.md           name needing %20
    - initiative-x/config.toml, data.json, noext   types we don't serve
    - initiative-x/link-in.md  -> spec-x/spec-x.md (inside)
    - initiative-x/link-out.md -> <root>/secret.md (outside)
    - <root>/secret.md and <root>/README.md outside _bmad-output/
    """
    out = project / "_bmad-output" / "initiative-x"
    (out / "spec-x").mkdir(parents=True)
    (out / "forge-x").mkdir()
    (out / "spec-x" / "spec-x.md").write_text(SPEC_MD)
    (out / "forge-x" / "forge-report.html").write_text(REPORT_HTML)
    (out / "with space.md").write_text("# Spaced\n")
    (out / "config.toml").write_text("a = 1\n")
    (out / "data.json").write_text("{}\n")
    (out / "noext").write_text("x\n")
    (project / "secret.md").write_text("# Secret\n")
    (project / "README.md").write_text("# Readme\n")
    (out / "link-in.md").symlink_to(out / "spec-x" / "spec-x.md")
    (out / "link-out.md").symlink_to(project / "secret.md")
    return project


@pytest.fixture
def serve():
    """Start servers: `serve(root, **kwargs)` runs `make_server(root, 0, **kwargs)`
    on a daemon thread and returns it. Every server is shut down at teardown."""
    servers = []

    def start(root: Path, **kwargs):
        server = make_server(root, 0, **kwargs)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()
