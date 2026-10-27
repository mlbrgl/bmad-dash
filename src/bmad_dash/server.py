"""Local HTTP server for the dashboard. Binds 127.0.0.1 only."""

import argparse
import re
import subprocess
import sys
import time
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, quote, unquote, urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from bmad_dash import commands
from bmad_dash.files import (
    NotFound,
    Refused,
    TICKETS_TOML,
    not_found_page,
    render_markdown,
    render_tickets_toml,
    resolve_file,
    split_project,
)
from bmad_dash.initiatives import (
    Initiative,
    initiative_worktree,
    list_initiatives,
    list_worktrees,
    worktree_id,
)
from bmad_dash.opener import (
    inject_relay,
    is_walkthrough_page,
    read_title,
    resolve_open_path,
    wrapper_page,
)
from bmad_dash.page import LocalPreview, render_page
from bmad_dash.previews import PreviewConfig, load_config, local_port, probe_batch
from bmad_dash.prs import PullRequest
from bmad_dash.stack import gt_stack, load_topology, stack_prs

HOST = "127.0.0.1"
MAX_FORM_BYTES = 1024
# Seconds a connection may sit idle, e.g. a POST that sends less body than
# its Content-Length. Then the connection closes and the thread is freed.
REQUEST_TIMEOUT = 10
# Seconds a POST body may take in total. Stops a client that sends one byte
# at a time, each under REQUEST_TIMEOUT, from holding a thread for hours.
REQUEST_DEADLINE = 10
# bmad-dash's own preview config, beside pyproject.toml (gitignored).
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "projects.toml"


def validate_root(root: Path) -> str | None:
    """An error message if `root` is not a usable project, else None."""
    if not (root / "_bmad-output").is_dir():
        return f"{root} has no _bmad-output/ folder"
    output = commands.run(
        ["git", "rev-parse", "--is-inside-work-tree"], root, commands.GIT_TIMEOUT
    )
    if output is None:
        return f"{root} is not a git repository"
    return None


def duplicate_names(roots: list[Path]) -> str | None:
    """An error message if two roots share a folder name, else None.
    The folder name is the project name in every link, so it must be unique.
    Names differing only in case clash too: on macOS they are the same folder."""
    seen: dict[str, Path] = {}
    for root in roots:
        name = root.name.casefold()
        if name in seen:
            return f"{seen[name]} and {root} are both named {root.name}"
        seen[name] = root
    return None


def load_root(root: Path) -> tuple[list[Initiative], dict[str, PullRequest] | None]:
    """One root's initiatives and PRs (None when GitHub couldn't be reached).
    1. `gt state` once: the PR icons and the initiatives' commits share it.
    2. GitHub (remote check, maybe the user's PRs) loads while the
       initiatives do, so initiatives never wait for gh.
    3. The per-stack PR lookups need the initiatives, so they come last."""
    stack = gt_stack(root)
    with ThreadPoolExecutor(max_workers=1) as pool:
        topology = pool.submit(load_topology, root, stack)
        initiatives = list_initiatives(root, stack=stack)
        prs = stack_prs(root, initiatives, topology.result())
    return initiatives, prs


def load_root_or_none(
    root: Path,
) -> tuple[list[Initiative], dict[str, PullRequest] | None] | None:
    """`load_root`, or None when it raised. The traceback goes to stderr and
    the page names the root, so one broken root never blanks the others."""
    try:
        return load_root(root)
    except Exception:
        print(f"bmad-dash: couldn't load {root}", file=sys.stderr)
        traceback.print_exc()
        return None


def local_previews(
    initiatives: list[Initiative], config: dict[str, PreviewConfig]
) -> dict[str, LocalPreview]:
    """Initiative key -> its local server, probed now. Only initiatives with
    a worktree and a config entry that gives a port get one. All probes run
    in one parallel batch, so the page waits about 2 s at most."""
    ports: dict[str, int] = {}
    for initiative in initiatives:
        entry = config.get(initiative.project)
        if entry is None or not initiative.worktrees:
            continue
        port = local_port(initiative.worktrees[0].path, entry)
        if port is not None:
            ports[initiative.key] = port
    urls = {key: f"http://localhost:{port}/" for key, port in ports.items()}
    results = probe_batch({key: [url] for key, url in urls.items()})
    return {
        key: LocalPreview(port, results[key][urls[key]]) for key, port in ports.items()
    }


def _run_code(args: list[str]) -> None:
    """Run `code <args>` with no shell and no output. Does not wait."""
    subprocess.Popen(
        ["code", *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def default_launch(path: Path) -> None:
    """Open (or focus) a VS Code window on `path`. Does not wait."""
    _run_code(["-n", str(path)])


def default_open_file(path: Path, line: str) -> None:
    """Open `path` in VS Code, at `line` when given. Does not wait."""
    _run_code(["-g", f"{path}:{line}" if line else str(path)])


def make_server(
    roots: list[Path],
    port: int,
    launch: Callable[[Path], None] = default_launch,
    open_file: Callable[[Path, str], None] = default_open_file,
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> ThreadingHTTPServer:
    """A server for `roots` on 127.0.0.1:`port` (0 picks a free port).
    Root names must be unique (`main` checks). `launch` opens a worktree and
    `open_file` opens one file; tests pass fakes. `config_path` is the
    preview config (`projects.toml`), read on every page load."""
    # Project name -> root. The name is the root folder's name.
    projects = {root.name: root for root in roots}

    class Handler(BaseHTTPRequestHandler):
        # Socket timeout for each connection (from StreamRequestHandler).
        timeout = REQUEST_TIMEOUT

        def do_GET(self):
            if not self.is_own_host():
                self.send_error(403)
                return
            url = urlsplit(self.path)
            path = url.path
            if path in ("/vscode", "/open-file"):
                self.send_response(405)
                self.send_header("Allow", "POST")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if path.startswith("/file/"):
                framed = parse_qs(url.query).get("frame", [""])[0] == "1"
                self.serve_file(unquote(path.removeprefix("/file/")), framed)
                return
            if path != "/":
                self.send_error(404)
                return
            # Only picks which row says "Couldn't open VS Code"; runs nothing.
            vscode_failed = parse_qs(url.query).get("vscode_failed", [""])[0]
            # Built on each request so the page is always current. Roots are
            # independent, so they load in parallel: the page waits for the
            # slowest root, not the sum.
            with ThreadPoolExecutor(max_workers=len(roots)) as pool:
                results = list(pool.map(load_root_or_none, roots))
            failed_roots = [root for root, result in zip(roots, results) if result is None]
            loaded = [result for result in results if result is not None]
            initiatives = [i for root_initiatives, _ in loaded for i in root_initiatives]
            prs: dict[str, PullRequest] = {}
            for _, root_prs in loaded:
                prs.update(root_prs or {})  # keys are `<project>/<slug>`: no clashes
            github_failed = any(root_prs is None for _, root_prs in loaded)
            # Read fresh each load, after the initiatives: reload re-probes.
            previews = local_previews(initiatives, load_config(config_path))
            body = render_page(
                initiatives,
                roots,
                vscode_failed=vscode_failed,
                prs=prs,
                github_failed=github_failed,
                failed_roots=failed_roots,
                previews=previews,
            ).encode("utf-8")
            self.send_html(body)

        def do_POST(self):
            path = urlsplit(self.path).path
            if path == "/vscode":
                self.open_worktree()
            elif path == "/open-file":
                self.open_walkthrough_link()
            else:
                self.send_error(404)

        def open_worktree(self):
            """The dashboard's worktree cell: `code -n <worktree>`."""
            if not self.is_same_origin():
                self.send_error(403)
                return
            form = self.read_form()
            if form is None:
                self.refuse_form(self.send_error)
                return
            wanted = form.get("id", [""])[0]
            project = form.get("project", [""])[0]
            slug = form.get("slug", [""])[0]
            failed = "/?vscode_failed=" + quote(f"{project}/{slug}", safe="")
            project_root = projects.get(project)
            if project_root is None:
                self.redirect(failed)
                return
            # Fresh list each time: only a path git lists right now can run.
            worktrees = list_worktrees(project_root)
            if worktrees is None:  # git failed
                self.redirect(failed)
                return
            matches = [w.path for w in worktrees if worktree_id(w.path) == wanted]
            if not matches:
                self.redirect(failed)
                return
            try:
                launch(matches[0])
            except OSError:  # e.g. `code` not on PATH
                self.redirect(failed)
                return
            self.redirect("/")

        def open_walkthrough_link(self):
            """Open a walkthrough's `vscode://file/` link with `code -g`.
            Sent by the walkthrough's wrapper page, so same-origin only."""
            if not self.is_same_origin():
                self.send_empty(403)
                return
            form = self.read_form()
            if form is None:
                self.refuse_form(self.send_empty)
                return
            line = form.get("line", [""])[0]
            if not re.fullmatch(r"[0-9]*", line):
                self.send_empty(400)
                return
            # The page decides which worktree a path may come from.
            page = form.get("page", [""])[0]
            if not page.startswith("/file/"):
                self.send_empty(404)
                return
            try:
                project_root, rest = split_project(
                    projects, unquote(page.removeprefix("/file/"))
                )
                page_path = resolve_file(project_root, rest)
            except (Refused, NotFound):
                self.send_empty(404)
                return
            output_base = (project_root / "_bmad-output").resolve()
            page_rel = page_path.relative_to(output_base)
            if not is_walkthrough_page(page_rel):
                self.send_empty(404)
                return
            worktrees = list_worktrees(project_root)  # one git call for both uses
            if worktrees is None:  # git failed: the wrapper shows its snackbar
                self.send_empty(500)
                return
            worktree = initiative_worktree(project_root, page_rel.parts[0], worktrees)
            if worktree is None:
                self.send_empty(404)
                return
            others = [w.path for w in worktrees]
            target = resolve_open_path(worktree.path, form.get("path", [""])[0], others)
            if target is None:
                self.send_empty(404)
                return
            try:
                open_file(target, line)
            except OSError:  # e.g. `code` not on PATH
                self.send_empty(500)
                return
            self.send_empty(204)

        def send_empty(self, status: int):
            """A response with no body: the wrapper only reads the status."""
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def redirect(self, location: str):
            """303 See Other, so the browser GETs `location`."""
            self.send_response(303)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def own_hosts(self) -> set[str]:
            port = self.server.server_port
            return {f"127.0.0.1:{port}", f"localhost:{port}"}

        def is_own_host(self) -> bool:
            """Host must be this server. Blocks DNS rebinding on every request."""
            return self.headers.get("Host") in self.own_hosts()

        def is_same_origin(self) -> bool:
            """Host and Origin must both be this server on 127.0.0.1 or localhost.
            Blocks other sites (Origin) and DNS rebinding (Host)."""
            origins = {f"http://{h}" for h in self.own_hosts()}
            return self.is_own_host() and self.headers.get("Origin") in origins

        def read_form(self) -> dict[str, list[str]] | None:
            """The urlencoded body, or None if its size is missing or too big,
            or it doesn't all arrive. If it timed out, `body_timed_out` is set."""
            self.body_timed_out = False
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return None
            if length < 0 or length > MAX_FORM_BYTES:
                return None
            body = self.read_body(length)
            if body is None:
                self.close_connection = True
                return None
            return parse_qs(body.decode("utf-8", errors="replace"))

        def read_body(self, length: int) -> bytes | None:
            """Exactly `length` bytes of body, or None if they don't all
            arrive before REQUEST_DEADLINE or the client goes away.
            Sets `body_timed_out` when the deadline or a socket timeout hit."""
            deadline = time.monotonic() + REQUEST_DEADLINE
            chunks = []
            received = 0
            try:
                while received < length:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self.body_timed_out = True
                        return None
                    # Each read waits at most until the deadline.
                    self.connection.settimeout(min(remaining, self.timeout))
                    # read1: at most one socket read, so the timeout above holds.
                    chunk = self.rfile.read1(length - received)
                    if not chunk:  # client closed early
                        return None
                    chunks.append(chunk)
                    received += len(chunk)
            except TimeoutError:
                self.body_timed_out = True
                return None
            except OSError:  # e.g. connection reset
                return None
            finally:
                self.connection.settimeout(self.timeout)
            return b"".join(chunks)

        def refuse_form(self, send: Callable[[int], None]):
            """Answer a bad form with 400, using `send` (send_error or
            send_empty). After a body timeout, send nothing: the connection
            just closes, and the client may be gone anyway."""
            if self.body_timed_out:
                return
            try:
                send(400)
            except OSError:  # client already gone
                self.close_connection = True

        def serve_file(self, rel: str, framed: bool = False):
            """`rel` is `<project>/<path>`: a file under that project's
            `_bmad-output/`. A walkthrough page is served as a
            wrapper page that frames it; `framed` serves the walkthrough itself."""
            try:
                project_root, rest = split_project(projects, rel)
                target = resolve_file(project_root, rest)
            except (Refused, NotFound):
                # Outside paths and unknown projects get the same 404 as
                # missing files: no hint about what exists beyond _bmad-output/.
                self.send_html(not_found_page().encode("utf-8"), status=404)
                return
            if target.name == TICKETS_TOML:
                body = render_tickets_toml(target).encode("utf-8")
            elif target.suffix == ".md":
                body = render_markdown(target).encode("utf-8")
            else:
                body = target.read_bytes()
            if is_walkthrough_page(target.relative_to((project_root / "_bmad-output").resolve())):
                if not framed:
                    # Dashboard code, not project content: no sandbox, so it
                    # can POST /open-file for links the frame relays to it.
                    page_path = "/file/" + quote(rel)
                    wrapper = wrapper_page(page_path, read_title(body, target.name))
                    # Only the dashboard itself may frame the wrapper.
                    self.send_html(
                        wrapper.encode("utf-8"),
                        {"Content-Security-Policy": "frame-ancestors 'self'"},
                    )
                    return
                body = inject_relay(body)
                # Only the wrapper (same origin) may frame the walkthrough.
                csp = "sandbox allow-scripts; frame-ancestors 'self'"
                self.send_html(body, {"Content-Security-Policy": csp})
                return
            # Served files are project content, not dashboard code. Sandboxing
            # gives them an opaque origin, so their requests to /vscode and
            # /open-file carry `Origin: null` and are refused.
            self.send_html(body, {"Content-Security-Policy": "sandbox allow-scripts"})

        def send_html(
            self,
            body: bytes,
            extra_headers: dict[str, str] | None = None,
            status: int = 200,
        ):
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            for name, value in (extra_headers or {}).items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer((HOST, port), Handler)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dashboard of BMad projects' initiatives."
    )
    parser.add_argument(
        "--project-root",
        required=True,
        type=Path,
        action="append",
        help="a project to show; repeat for several",
    )
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    roots = [root.expanduser().resolve() for root in args.project_root]
    errors = [error for error in map(validate_root, roots) if error]
    duplicate = duplicate_names(roots)
    if duplicate:
        errors.append(duplicate)
    if errors:
        for error in errors:
            print(f"bmad-dash: {error}", file=sys.stderr)
        sys.exit(1)

    server = make_server(roots, args.port)
    url = f"http://{HOST}:{server.server_port}/"
    print(f"bmad-dash serving {', '.join(map(str, roots))} at {url}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
