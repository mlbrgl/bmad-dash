import subprocess
from pathlib import Path

import pytest


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
