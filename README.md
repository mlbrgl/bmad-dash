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

Repeat `--project-root` to see several projects in one table:

```sh
uv run --directory ~/Code/bmad-dash bmad-dash \
  --project-root ~/Code/owid-grapher --project-root ~/Code/bmad-dash
```

Their initiatives mix, newest activity first, with one "Show finished (n)".
The header lists every root. A missing or non-git root, or two roots with the
same folder name, stops the start with a message naming it.

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

A project is named by its root folder (`--project-root ~/Code/owid-grapher` is
`owid-grapher`); each row shows it in its own Project cell, and the slug line
is the slug alone. When one root can't reach GitHub, only its rows lose their
PR icon.

`/file/<project>/<path>` shows a `.md` (rendered) or `.html` (as is) file, with `<path>`
relative to that project's `_bmad-output/`. Rendered headings have ids, so a link can point at
a section: `/file/<project>/<path>#action-items` for `## Action items`. An unknown project, a missing path or one outside `_bmad-output/` gets
a plain 404 page. Open trees are remembered per `<project>/<slug>`. Served files
are sandboxed, so they can't trigger VS Code.

Walkthrough pages (`initiative-*/.../walkthrough-*/*.html`) are shown inside
a small dashboard wrapper page that frames the walkthrough (`?frame=1`, still
sandboxed). Their `vscode://file/<path>[:<line>]` links then run `code -g
<path>:<line>`, also in the Claude desktop browser pane: the framed page
passes the click to the wrapper, which POSTs it to `/open-file`. Only
existing files inside that initiative's worktree open; otherwise the page
shows "Couldn't open in VS Code".

Each initiative shows at most one worktree: the first one `git worktree list`
gives whose config names it. Click the title or any empty part of a row
to show its tree; click an empty part of a tree row to open its file.

- **Worktree cell**: click it to run `code -n <worktree>`, so the `code` CLI
  must be on `PATH`. It only accepts a POST from the dashboard itself, and
  only for a path `git worktree list` shows right now. If it refuses or `code`
  fails, the cell says "Couldn't open VS Code".
- **Resume** opens a new Claude Code session in that worktree
  (`claude://code/new`) with `/bmad Resume work on <slug>` prefilled.
- **#n** opens, in a new tab, the last PR of the stack that starts at the
  worktree's branch. It needs `gh` installed and signed in. In a repo
  Graphite tracks, `gt state` gives the stack (the branch, then its tracked
  children), and one `gh pr list --head <branch>` per branch of the stack,
  up to 8 at once, finds each branch's newest PR, by anyone. The button shows
  the highest branch that has a PR. Without `gt`, or when the repo isn't
  tracked or `gt` fails, it falls back to one
  `gh pr list --author @me --state all --limit 100` per page load and follows
  PRs based on each other's branch. Either way, a branch another
  initiative's worktree has checked out stops the stack, so two initiatives
  on one stack each get their own button and Last activity. No button for a
  fork in the stack, no PR on any branch, a detached worktree, or a row on
  trunk. If `gh` fails, the header says
  "Couldn't reach GitHub". Repos without a `github.com` remote skip it
  silently.

A row without a worktree says `no worktree` and has no actions.

## Test

```sh
uv run pytest
```
