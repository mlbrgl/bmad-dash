# bmad-dash

A small local dashboard for a BMad project. It lists every
`_bmad-output/initiative-*` with its title and the worktree(s) and branch
working on it, read from each worktree's `_bmad/custom/config.user.toml`
(`[core] active_initiative`). Initiatives no worktree points at show "none".

It only reads the project, binds `127.0.0.1` only, and rebuilds the page on
every request.

## Run

```sh
uv run --directory ~/Code/bmad-dash bmad-dash --project-root ~/Code/owid-grapher
```

Then open http://127.0.0.1:8765/ (change the port with `--port`).

Each row shows last activity and a Status sentence: what is waiting (ticket
counts by status, `forge open`, `spec to slice`) and tickets done/total, from
the project's `_bmad/method/scripts/tickets.py status` (run with `uv`). When
that call fails the Status cell says "Couldn't read tickets: tickets.py
failed". Items untouched for 14 days are stale. Rows are sorted newest
activity first. Initiatives whose file has `status: done` or `dropped` sit
behind "Show finished (n)"; one with nothing open and no such status says
`confirm close or add work`.
Expand a row to see its forges, specs and epics.

`/file/<path>` shows a `.md` (rendered) or `.html` (as is) file, with `<path>`
relative to `_bmad-output/`. Rendered `.md` pages get a `← Initiatives` back
link; a missing path or one outside `_bmad-output/` gets a plain 404 page with
the same link. Served files
are sandboxed, so they can't trigger the VS Code button.

Each initiative shows at most one worktree: the first one `git worktree list`
gives whose config names it. A row with a worktree has two actions:

- **Resume** opens a new Claude Code session in that worktree
  (`claude://code/new`) with `/bmad Resume work on <slug>` prefilled.
- **VS Code** runs `code -n <worktree>`, so the `code` CLI must be on `PATH`.
  It only accepts a POST from the dashboard itself, and only for a path
  `git worktree list` shows right now. If it refuses or `code` fails, the row
  says "Couldn't open VS Code".

A row without a worktree says `no worktree` and has no actions.

## Test

```sh
uv run pytest
```
