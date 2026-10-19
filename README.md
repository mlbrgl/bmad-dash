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

Each row shows last activity, open items, and tickets done/total from the
project's `_bmad/method/scripts/tickets.py status` (run with `uv`). When that
call fails the Tickets cell shows "?". Items untouched for 14 days are stale.
Expand a row to see its forges, specs and epics.

`/file/<path>` shows a `.md` (rendered) or `.html` (as is) file, with `<path>`
relative to `_bmad-output/`; anything outside it is refused (403). Served files
are sandboxed, so they can't trigger the switch button.

The **switch** button on a worktree runs `code -n <worktree>`, so the `code`
CLI must be on `PATH`. It only accepts a POST from the dashboard itself, and
only for a path `git worktree list` shows.

## Test

```sh
uv run pytest
```
