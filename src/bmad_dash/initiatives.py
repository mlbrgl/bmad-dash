"""Find a project's initiatives and the worktrees working on each.

A worktree works on an initiative when its own
`_bmad/custom/config.user.toml` has `[core] active_initiative = "<slug>"`.
"""

import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Worktree:
    path: Path
    branch: str  # branch name, or "detached"


@dataclass
class Initiative:
    slug: str
    title: str
    worktrees: list[Worktree] = field(default_factory=list)


def list_worktrees(root: Path) -> list[Worktree]:
    """All worktrees of the repo at `root`, main checkout included."""
    output = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    worktrees = []
    # Each worktree is a block of lines separated by a blank line.
    for block in output.strip().split("\n\n"):
        path = None
        branch = "detached"
        for line in block.splitlines():
            if line.startswith("worktree "):
                path = Path(line.removeprefix("worktree "))
            elif line.startswith("branch "):
                branch = line.removeprefix("branch ").removeprefix("refs/heads/")
        if path is not None:
            worktrees.append(Worktree(path=path, branch=branch))
    return worktrees


def active_initiative(worktree_path: Path) -> str | None:
    """The initiative slug this worktree's config points at, or None."""
    config_path = worktree_path / "_bmad" / "custom" / "config.user.toml"
    try:
        with config_path.open("rb") as f:
            config = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return None

    core = config.get("core")
    if not isinstance(core, dict):
        return None
    slug = core.get("active_initiative")
    return slug if isinstance(slug, str) and slug else None


def read_title(initiative_dir: Path) -> str:
    """The `title:` from the initiative file's frontmatter, or the folder name."""
    slug = initiative_dir.name
    initiative_file = initiative_dir / f"{slug}.md"
    try:
        lines = initiative_file.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return slug

    if not lines or lines[0].strip() != "---":
        return slug
    for line in lines[1:]:
        if line.strip() == "---":
            break  # end of frontmatter
        if line.startswith("title:"):
            title = line.removeprefix("title:").strip().strip("\"'")
            return title or slug
    return slug


def list_initiatives(root: Path) -> list[Initiative]:
    """One Initiative per `_bmad-output/initiative-*` folder, sorted by slug,
    each with the worktrees whose config names it."""
    worktrees_by_slug: dict[str, list[Worktree]] = {}
    for worktree in list_worktrees(root):
        slug = active_initiative(worktree.path)
        if slug:
            worktrees_by_slug.setdefault(slug, []).append(worktree)

    initiative_dirs = sorted(
        (p for p in (root / "_bmad-output").glob("initiative-*") if p.is_dir()),
        key=lambda p: p.name,
    )
    return [
        Initiative(
            slug=d.name,
            title=read_title(d),
            worktrees=worktrees_by_slug.get(d.name, []),
        )
        for d in initiative_dirs
    ]
