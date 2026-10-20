from pathlib import Path

from conftest import write_initiative

from bmad_dash.initiatives import Worktree, active_initiative, list_initiatives


def by_slug(root: Path) -> dict:
    return {i.slug: i for i in list_initiatives(root)}


def test_lists_only_initiative_folders_sorted(project):
    slugs = [i.slug for i in list_initiatives(project)]
    assert slugs == [
        "initiative-matched",
        "initiative-nofile",
        "initiative-orphan",
        "initiative-shared",
        "initiative-untitled",
    ]


def test_single_match_shows_path_and_branch(project):
    worktrees = by_slug(project)["initiative-matched"].worktrees
    assert worktrees == [Worktree(project.parent / "worktrees" / "wt-a", "feature-a")]


def test_several_matches_keep_only_the_first_listed(project):
    # Main checkout and wt-b both claim it; git lists the main checkout first.
    worktrees = by_slug(project)["initiative-shared"].worktrees
    assert worktrees == [Worktree(project, "main")]


def test_no_match_has_no_worktrees(project):
    assert by_slug(project)["initiative-orphan"].worktrees == []


def test_bad_or_missing_configs_are_ignored(project):
    worktrees = project.parent / "worktrees"
    assert active_initiative(worktrees / "wt-c") is None
    assert active_initiative(worktrees / "wt-d") is None
    assert active_initiative(worktrees / "wt-e") is None


def test_titles_fall_back_to_folder_name(project):
    initiatives = by_slug(project)
    assert initiatives["initiative-matched"].title == "Matched one"
    assert initiatives["initiative-shared"].title == "Shared <b>&</b> one"
    assert initiatives["initiative-untitled"].title == "initiative-untitled"
    assert initiatives["initiative-nofile"].title == "initiative-nofile"


def test_status_read_from_initiative_frontmatter(project):
    write_initiative(project, "initiative-closed", "---\ntitle: C\nstatus: done\n---\n")
    write_initiative(project, "initiative-gone", "---\nstatus: 'dropped'\n---\n")
    initiatives = by_slug(project)
    assert initiatives["initiative-closed"].finished
    assert initiatives["initiative-gone"].status == "dropped"
    assert initiatives["initiative-gone"].finished
    assert initiatives["initiative-matched"].status == ""
    assert not initiatives["initiative-nofile"].finished
