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
    assert [t.ref for t in item.tickets] == ["1.1", "1.2"]
    assert item.word == "done"


def test_epic_partly_done_skips_dropped(initiative):
    make_folder(initiative, "epic-x")
    tickets = [ticket("1.1", "done"), ticket("1.2", "dropped"), ticket("1.3", "review")]
    item = only(build_tree(initiative, tickets, NOW))
    assert item.state == OPEN
    assert (item.done, item.total) == (1, 2)
    assert item.word == "in progress"
    assert [t.word for t in item.tickets] == ["done", "dropped", "review"]


def test_epic_without_tickets_is_open_zero_of_zero(initiative):
    make_folder(initiative, "epic-x")
    item = only(build_tree(initiative, [], NOW))
    assert item.state == OPEN
    assert (item.done, item.total) == (0, 0)
    assert item.word == ""


def test_epic_with_only_dropped_tickets_has_no_word(initiative):
    make_folder(initiative, "epic-x")
    assert only(build_tree(initiative, [ticket("1.1", "dropped")], NOW)).word == ""


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
    assert item.word == "" and item.tickets == []


# --- tickets ---


def write_plan(folder: Path, name: str, ticket_value: str, when: datetime) -> Path:
    plan = folder / name
    plan.write_text(f"---\ntitle: x\nticket: {ticket_value}\n---\n# Plan\n")
    os.utime(plan, (when.timestamp(), when.timestamp()))
    return plan


def epic_ticket(folder: Path, raw: dict):
    return only(only(build_tree(folder.parent, [raw], NOW)).tickets)


def test_ticket_links_its_plan_by_id(initiative):
    epic = make_folder(initiative, "epic-x")
    write_plan(epic, "story-other-plan.md", "'4'", NOW)
    write_plan(epic, "story-y-plan.md", "'3'", NOW - timedelta(days=2))
    t = epic_ticket(epic, {**ticket("1.3", "in-progress"), "id": 3})
    assert t.path == "initiative-x/epic-x/story-y-plan.md"
    assert t.updated == NOW - timedelta(days=2)
    assert t.word == "in progress"
    assert t.stale is False


def test_ticket_links_its_plan_by_leaf_stem(initiative):
    epic = make_folder(initiative, "epic-x")
    write_plan(epic, "story-y-plan.md", "story-y", NOW)
    t = epic_ticket(epic, {**ticket("1.3", "review"), "id": None, "file": "story-y.md"})
    assert t.path == "initiative-x/epic-x/story-y-plan.md"


def test_ticket_without_plan_uses_leaf_file(initiative):
    epic = make_folder(initiative, "epic-x")
    leaf = epic / "story-y.md"
    leaf.write_text("---\nid: 3\n---\n")
    os.utime(leaf, ((NOW - timedelta(days=3)).timestamp(),) * 2)
    t = epic_ticket(epic, {**ticket("1.3", "planned"), "id": 3, "file": "story-y.md"})
    assert t.path == "initiative-x/epic-x/story-y.md"
    assert t.updated == NOW - timedelta(days=3)


def test_ticket_without_files_uses_tickets_toml_mtime(initiative):
    epic = make_folder(initiative, "epic-x")
    toml = epic / "tickets.toml"
    toml.write_text("")
    os.utime(toml, ((NOW - timedelta(days=5)).timestamp(),) * 2)
    t = epic_ticket(epic, {**ticket("1.3", "planned"), "id": 3, "file": None})
    assert t.path is None
    assert t.title == "Ticket 1.3"
    assert t.updated == NOW - timedelta(days=5)


def test_ticket_without_any_file_has_no_updated(initiative):
    epic = make_folder(initiative, "epic-x")
    t = epic_ticket(epic, {**ticket("1.3", "planned"), "id": 3})
    assert (t.path, t.updated, t.stale) == (None, None, False)


def test_ticket_with_null_ref_has_empty_ref(initiative):
    epic = make_folder(initiative, "epic-x")
    t = epic_ticket(epic, {**ticket("1.3", "planned"), "ref": None})
    assert t.ref == ""


@pytest.mark.parametrize("state, stale", [
    ("review", True), ("planned", True), ("done", False), ("dropped", False),
])
def test_ticket_stale_after_14_days_unless_done_or_dropped(initiative, state, stale):
    epic = make_folder(initiative, "epic-x")
    write_plan(epic, "story-y-plan.md", "'3'", NOW - timedelta(days=30))
    assert epic_ticket(epic, {**ticket("1.3", state), "id": 3}).stale is stale


def test_ticket_fresh_is_not_stale(initiative):
    epic = make_folder(initiative, "epic-x")
    write_plan(epic, "story-y-plan.md", "'3'", NOW - timedelta(days=13))
    assert epic_ticket(epic, {**ticket("1.3", "review"), "id": 3}).stale is False


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


def test_forge_report_link(initiative):
    forge = make_folder(initiative, "forge-x", f"updated: {ago(1)}\n")
    (forge / "forge-report.html").write_text("<html></html>")
    make_folder(initiative, "forge-y", f"updated: {ago(1)}\n")
    reports = [i.report for i in build_tree(initiative, [], NOW)]
    assert reports == ["initiative-x/forge-x/forge-report.html", None]


# --- walkthroughs ---


def make_walkthrough(
    parent: Path, name: str, narrative: str | None, html: bool = True
) -> Path:
    """A walkthrough folder with its narrative and, optionally, its HTML page."""
    folder = parent / name
    folder.mkdir(parents=True)
    if narrative is not None:
        (folder / f"{name}.md").write_text(narrative)
    if html:
        (folder / f"{name}.html").write_text("<html></html>")
    (folder / f"{name}-log.md").write_text("- [ ] not counted\n")
    return folder


def blocks(checked: int, total: int) -> str:
    lines = [f"- [{'x' if i < checked else ' '}] Block {i + 1}" for i in range(total)]
    return "---\ntitle: w\n---\n# W\n\n**Blocks**\n\n" + "\n".join(lines) + "\n"


def test_walkthrough_html_and_narrative(initiative):
    make_walkthrough(initiative, "walkthrough-a", blocks(2, 9))
    item = only(build_tree(initiative, [], NOW))
    assert item.kind == "walk"
    assert item.path == "initiative-x/walkthrough-a/walkthrough-a.html"
    assert item.report == "initiative-x/walkthrough-a/walkthrough-a.md"
    assert item.report_label == "md"
    assert item.word == "2/9 blocks"
    assert item.state == OPEN


def test_walkthrough_narrative_only(initiative):
    make_walkthrough(initiative, "walkthrough-a", blocks(2, 9), html=False)
    item = only(build_tree(initiative, [], NOW))
    assert item.path == "initiative-x/walkthrough-a/walkthrough-a.md"
    assert item.report is None


def test_walkthrough_all_checked_is_done_and_never_stale(initiative):
    folder = make_walkthrough(initiative, "walkthrough-a", blocks(9, 9))
    set_mtime(folder, NOW - timedelta(days=20))
    item = only(build_tree(initiative, [], NOW))
    assert item.word == "done"
    assert item.state == FINISHED


def test_walkthrough_none_checked(initiative):
    make_walkthrough(initiative, "walkthrough-a", blocks(0, 9))
    assert only(build_tree(initiative, [], NOW)).word == "0/9 blocks"


def test_walkthrough_counts_top_list_only(initiative):
    narrative = blocks(1, 9) + "".join(
        f"\n## Block {i}\n\n- [x] Block {i} visited\n\ntext\n" for i in range(1, 10)
    )
    make_walkthrough(initiative, "walkthrough-a", narrative)
    assert only(build_tree(initiative, [], NOW)).word == "1/9 blocks"


def test_walkthrough_without_checkboxes(initiative):
    make_walkthrough(initiative, "walkthrough-a", "# W\n\nNo boxes.\n")
    item = only(build_tree(initiative, [], NOW))
    assert item.word == ""
    assert item.state == OPEN


def test_walkthrough_html_only(initiative):
    make_walkthrough(initiative, "walkthrough-a", None)
    item = only(build_tree(initiative, [], NOW))
    assert item.path == "initiative-x/walkthrough-a/walkthrough-a.html"
    assert item.report is None
    assert item.word == ""
    assert item.state == OPEN


def test_walkthrough_without_files_is_plain_text(initiative):
    (initiative / "walkthrough-a").mkdir()
    item = only(build_tree(initiative, [], NOW))
    assert item.path is None
    assert item.report is None


def test_walkthrough_unreadable_narrative_has_no_status(initiative):
    folder = make_walkthrough(initiative, "walkthrough-a", None)
    (folder / "walkthrough-a.md").write_bytes(b"- [x] \xff\xfe bad\n")
    item = only(build_tree(initiative, [], NOW))
    assert item.word == ""
    assert item.state == OPEN


def test_walkthrough_stale_when_open_and_old(initiative):
    folder = make_walkthrough(initiative, "walkthrough-a", blocks(2, 9))
    set_mtime(folder, NOW - timedelta(days=14))
    item = only(build_tree(initiative, [], NOW))
    assert item.state == STALE
    assert item.updated == NOW - timedelta(days=14)


def test_walkthrough_at_epic_level_after_forges_and_specs(initiative):
    epic = make_folder(initiative, "epic-x")
    make_walkthrough(epic, "walkthrough-b", blocks(0, 2))
    make_folder(epic, "spec-a", f"updated: {ago(1)}\n")
    make_folder(epic, "forge-a", f"updated: {ago(1)}\n")
    children = only(build_tree(initiative, [], NOW)).children
    assert [c.name for c in children] == ["forge-a", "spec-a", "walkthrough-b"]
    assert children[2].path == "initiative-x/epic-x/walkthrough-b/walkthrough-b.html"


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
    (initiative / "walkthrough-a").mkdir()
    names = [i.name for i in build_tree(initiative, [], NOW)]
    assert names == ["forge-a", "forge-b", "spec-b", "spec-nofile", "walkthrough-a", "epic-a"]
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
