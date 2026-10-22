# bmad-dash

A small local dashboard for a BMad project. It lists every
`_bmad-output/initiative-*` with its title and the worktree(s) and branch
working on it, read from each worktree's `_bmad/custom/config.user.toml`
(`[core] active_initiative`). Initiatives no worktree points at show "no worktree".

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
Expand a row to see its forges, specs, walkthroughs and epics, then any tickets
that sit directly in the initiative folder.

`/file/<path>` shows a `.md` (rendered) or `.html` (as is) file, with `<path>`
relative to `_bmad-output/`. A missing path or one outside `_bmad-output/` gets
a plain 404 page. Served files
are sandboxed, so they can't trigger VS Code.

Walkthrough pages (`initiative-*/.../walkthrough-*/*.html`) are shown inside
a small dashboard wrapper page that frames the walkthrough (`?frame=1`, still
sandboxed). Their `vscode://file/<path>[:<line>]` links then run `code -g
<path>:<line>`, also in the Claude desktop browser pane: the framed page
passes the click to the wrapper, which POSTs it to `/open-file`. Only
existing files inside that initiative's worktree open; otherwise the page
shows "Couldn't open in VS Code".

Each initiative shows at most one worktree: the first one `git worktree list`
gives whose config names it. Click the caret to show a row's tree.

- **Worktree cell**: click it to run `code -n <worktree>`, so the `code` CLI
  must be on `PATH`. It only accepts a POST from the dashboard itself, and
  only for a path `git worktree list` shows right now. If it refuses or `code`
  fails, the cell says "Couldn't open VS Code".
- **Resume** opens a new Claude Code session in that worktree
  (`claude://code/new`) with `/bmad Resume work on <slug>` prefilled.
- **#n** opens, in a new tab, the last PR of the stack that starts at the
  worktree's branch (following PRs based on each other's branch). It needs
  `gh` installed and signed in; PRs come from one
  `gh pr list --author @me --state all --limit 100` per page load. No button
  for a fork in the stack, no PR, or a detached worktree. If `gh` fails, the
  header says "Couldn't reach GitHub". Repos without a `github.com` remote
  skip it silently.

A row without a worktree says `no worktree` and has no actions.

## Test

```sh
uv run pytest
```
