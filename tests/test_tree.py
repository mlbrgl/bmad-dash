import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from bmad_dash.initiatives import list_initiatives
from bmad_dash.page import render_page
from bmad_dash.tree import (
    FINISHED,
    OPEN,
    STALE,
    build_tree,
    load_tickets,
    read_memlog,
)

NOW = datetime(2026, 10, 19, 12, 0)


def ago(days: int) -> str:
    return (NOW - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M")


def make_folder(parent: Path, name: str, memlog: str | None = None) -> Path:
    """A folder with its main file and, optionally, a memlog frontmatter body."""
    folder = parent / name
    folder.mkdir(parents=True)
    (folder / f"{name}.md").write_text(f"# {name}\n")
    if memlog is not None:
        (folder / ".memlog.md").write_text(f"---\n{memlog}---\n\n- (note) text\n")
    return folder


def set_mtime(folder: Path, when: datetime) -> None:
    stamp = when.timestamp()
    for p in folder.iterdir():
        os.utime(p, (stamp, stamp))


@pytest.fixture
def initiative(tmp_path: Path) -> Path:
    folder = tmp_path / "_bmad-output" / "initiative-x"
    folder.mkdir(parents=True)
    return folder


def only(items):
    assert len(items) == 1
    return items[0]


# --- forges ---


def test_forge_complete_is_finished(initiative):
    make_folder(initiative, "forge-x", f"status: complete\nupdated: {ago(1)}\n")
    assert only(build_tree(initiative, [], NOW)).state == FINISHED


def test_forge_without_status_is_open(initiative):
    make_folder(initiative, "forge-x", f"updated: {ago(3)}\n")
    item = only(build_tree(initiative, [], NOW))
    assert item.kind == "forge"
    assert item.state == OPEN
    assert item.updated == NOW - timedelta(days=3)


def test_forge_other_status_is_open(initiative):
    make_folder(initiative, "forge-x", f"status: in-progress\nupdated: {ago(3)}\n")
    assert only(build_tree(initiative, [], NOW)).state == OPEN


def test_open_forge_untouched_14_days_is_stale(initiative):
    make_folder(initiative, "forge-x", f"updated: {ago(14)}\n")
    assert only(build_tree(initiative, [], NOW)).state == STALE


def test_finished_item_is_never_stale(initiative):
    make_folder(initiative, "forge-x", f"status: complete\nupdated: {ago(30)}\n")
    assert only(build_tree(initiative, [], NOW)).state == FINISHED


# --- specs ---


def test_spec_with_tickets_toml_beside_it_is_finished(initiative):
    make_folder(initiative, "spec-x", f"updated: {ago(1)}\n")
    (initiative / "tickets.toml").write_text("")
    item = only(build_tree(initiative, [], NOW))
    assert item.kind == "spec"
    assert item.state == FINISHED


def test_spec_without_tickets_toml_is_open(initiative):
    make_folder(initiative, "spec-x", f"updated: {ago(1)}\n")
    assert only(build_tree(initiative, [], NOW)).state == OPEN


def test_spec_in_epic_is_nested_and_uses_epic_tickets_toml(initiative):
    epic = make_folder(initiative, "epic-x")
    make_folder(epic, "spec-y", f"updated: {ago(1)}\n")
    (epic / "tickets.toml").write_text("")
    epic_item = only(build_tree(initiative, [], NOW))
    spec = only(epic_item.children)
    assert spec.name == "spec-y"
    assert spec.state == FINISHED
    assert spec.path == "initiative-x/epic-x/spec-y/spec-y.md"


def test_spec_in_epic_without_epic_tickets_toml_is_open(initiative):
    epic = make_folder(initiative, "epic-x")
    make_folder(epic, "spec-y", f"updated: {ago(1)}\n")
    (initiative / "tickets.toml").write_text("")  # wrong level: not beside the spec
    assert only(only(build_tree(initiative, [], NOW)).children).state == OPEN


# --- epics ---


def ticket(ref: str, state: str, epic: str = "epic-x") -> dict:
    return {"epic": epic, "ref": ref, "title": f"Ticket {ref}", "state": state}


def test_epic_all_done_is_finished(initiative):
    make_folder(initiative, "epic-x")
    tickets = [ticket("1.1", "done"), ticket("1.2", "done"), ticket("2.1", "backlog", "epic-y")]
    item = only(build_tree(initiative, tickets, NOW))
    assert item.state == FINISHED
    assert (item.done, item.total) == (2, 2)
    assert [t["ref"] for t in item.tickets] == ["1.1", "1.2"]


def test_epic_partly_done_skips_dropped(initiative):
    make_folder(initiative, "epic-x")
    tickets = [ticket("1.1", "done"), ticket("1.2", "dropped"), ticket("1.3", "review")]
    item = only(build_tree(initiative, tickets, NOW))
    assert item.state == OPEN
    assert (item.done, item.total) == (1, 2)


def test_epic_without_tickets_is_open_zero_of_zero(initiative):
    make_folder(initiative, "epic-x")
    item = only(build_tree(initiative, [], NOW))
    assert item.state == OPEN
    assert (item.done, item.total) == (0, 0)


def test_open_epic_uses_file_mtime_for_staleness(initiative):
    epic = make_folder(initiative, "epic-x")
    set_mtime(epic, NOW - timedelta(days=20))
    item = only(build_tree(initiative, [], NOW))
    assert item.updated == NOW - timedelta(days=20)
    assert item.state == STALE


def test_epic_counts_unknown_when_tickets_unavailable(initiative):
    make_folder(initiative, "epic-x")
    item = only(build_tree(initiative, None, NOW))
    assert (item.done, item.total) == (None, None)
    assert item.state == OPEN


# --- updated time fallbacks ---


@pytest.mark.parametrize("memlog", [None, "updated: not-a-date\n", "status: draft\n"])
def test_missing_or_bad_updated_falls_back_to_newest_mtime(initiative, memlog):
    folder = make_folder(initiative, "forge-x", memlog)
    set_mtime(folder, NOW - timedelta(days=2))
    item = only(build_tree(initiative, [], NOW))
    assert item.updated == NOW - timedelta(days=2)


def test_read_memlog_accepts_a_plain_date(tmp_path):
    (tmp_path / ".memlog.md").write_text("---\nstatus: complete\nupdated: 2026-10-01\n---\n")
    assert read_memlog(tmp_path) == ("complete", datetime(2026, 10, 1))


def test_read_memlog_ignores_bullets(tmp_path):
    (tmp_path / ".memlog.md").write_text("---\ntopic: x\n---\nstatus: complete\n")
    assert read_memlog(tmp_path) == (None, None)


# --- tree shape ---


def test_empty_initiative_has_empty_tree(initiative):
    (initiative / "notes").mkdir()
    (initiative / "initiative-x.md").write_text("")
    assert build_tree(initiative, [], NOW) == []


def test_kinds_sorted_and_links(initiative):
    make_folder(initiative, "spec-b", f"updated: {ago(1)}\n")
    make_folder(initiative, "forge-b", f"updated: {ago(1)}\n")
    make_folder(initiative, "forge-a", f"updated: {ago(1)}\n")
    make_folder(initiative, "epic-a")
    (initiative / "spec-nofile").mkdir()
    names = [i.name for i in build_tree(initiative, [], NOW)]
    assert names == ["forge-a", "forge-b", "spec-b", "spec-nofile", "epic-a"]
    paths = {i.name: i.path for i in build_tree(initiative, [], NOW)}
    assert paths["forge-a"] == "initiative-x/forge-a/forge-a.md"
    assert paths["spec-nofile"] is None


# --- tickets.py ---


def write_fake_tickets_py(root: Path, body: str) -> None:
    script = root / "_bmad" / "method" / "scripts" / "tickets.py"
    script.parent.mkdir(parents=True)
    script.write_text(body)


def test_load_tickets_reads_json(tmp_path, initiative):
    data = {"tickets": [ticket("1.1", "done")]}
    write_fake_tickets_py(tmp_path, f"print({json.dumps(json.dumps(data))})\n")
    assert load_tickets(tmp_path, initiative) == data["tickets"]


def test_load_tickets_script_failure_is_none(tmp_path, initiative):
    write_fake_tickets_py(tmp_path, "import sys\nsys.exit(1)\n")
    assert load_tickets(tmp_path, initiative) is None


def test_load_tickets_bad_json_is_none(tmp_path, initiative):
    write_fake_tickets_py(tmp_path, "print('not json')\n")
    assert load_tickets(tmp_path, initiative) is None


def test_load_tickets_missing_script_is_none(tmp_path, initiative):
    assert load_tickets(tmp_path, initiative) is None


def test_failing_tickets_py_shows_unavailable_on_page(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    initiative = root / "_bmad-output" / "initiative-x"
    make_folder(initiative, "epic-x")
    write_fake_tickets_py(root, "import sys\nsys.exit(1)\n")

    html = render_page(list_initiatives(root, now=NOW), root)
    assert "tickets unavailable" in html
    assert "?/?" in html
