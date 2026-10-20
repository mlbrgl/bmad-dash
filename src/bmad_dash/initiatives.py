"""Find a project's initiatives and the worktrees working on each.

A worktree works on an initiative when its own
`_bmad/custom/config.user.toml` has `[core] active_initiative = "<slug>"`.
"""

import hashlib
from concurrent.futures import ThreadPoolExecutor
import subprocess
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from bmad_dash.signals import Activity, last_activity
from bmad_dash.tree import TreeItem, build_tree, frontmatter, load_tickets


@dataclass
class Worktree:
    path: Path
    branch: str  # branch name, or "detached"


@dataclass
class Initiative:
    slug: str
    title: str
    worktrees: list[Worktree] = field(default_factory=list)
    # From one `tickets.py status` call; None when it failed. Reused by later columns.
    tickets: list[dict] | None = None
    tree: list[TreeItem] = field(default_factory=list)
    activity: Activity | None = None
    # Initiative file frontmatter `status`; "done" or "dropped" means finished.
    status: str = ""

    @property
    def finished(self) -> bool:
        return self.status in ("done", "dropped")


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


def worktree_id(path: Path) -> str:
    """A short, stable id for a worktree path, safe to put in a form."""
    return hashlib.sha256(str(path).encode()).hexdigest()[:12]


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


def make_initiative(
    root: Path, initiative_dir: Path, worktrees: list[Worktree], now: datetime
) -> Initiative:
    tickets = load_tickets(root, initiative_dir)
    return Initiative(
        slug=initiative_dir.name,
        title=read_title(initiative_dir),
        worktrees=worktrees,
        tickets=tickets,
        tree=build_tree(initiative_dir, tickets, now),
        activity=last_activity(initiative_dir, worktrees),
        status=frontmatter(initiative_dir / f"{initiative_dir.name}.md").get("status", ""),
    )


def list_initiatives(root: Path, now: datetime | None = None) -> list[Initiative]:
    """One Initiative per `_bmad-output/initiative-*` folder, sorted by slug,
    each with the worktrees whose config names it, its tickets and its tree."""
    if now is None:
        now = datetime.now()
    worktrees_by_slug: dict[str, list[Worktree]] = {}
    for worktree in list_worktrees(root):
        slug = active_initiative(worktree.path)
        if slug:
            worktrees_by_slug.setdefault(slug, []).append(worktree)

    initiative_dirs = sorted(
        (p for p in (root / "_bmad-output").glob("initiative-*") if p.is_dir()),
        key=lambda p: p.name,
    )
    # Each initiative runs its own `tickets.py` call, so build them in parallel:
    # the page then waits for the slowest call, not the sum of them.
    # map() keeps the slug order.
    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(
            lambda d: make_initiative(root, d, worktrees_by_slug.get(d.name, []), now),
            initiative_dirs,
        ))
