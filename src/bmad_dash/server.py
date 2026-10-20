"""Local HTTP server for the dashboard. Binds 127.0.0.1 only."""

import argparse
import subprocess
import sys
from collections.abc import Callable
from urllib.parse import parse_qs, unquote, urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from bmad_dash.files import NotFound, Refused, not_found_page, render_markdown, resolve_file
from bmad_dash.initiatives import list_initiatives, list_worktrees, worktree_id
from bmad_dash.page import render_page

HOST = "127.0.0.1"
MAX_FORM_BYTES = 1024


def validate_root(root: Path) -> str | None:
    """An error message if `root` is not a usable project, else None."""
    if not (root / "_bmad-output").is_dir():
        return f"{root} has no _bmad-output/ folder"
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return f"{root} is not a git repository"
    return None


def default_launch(path: Path) -> None:
    """Open (or focus) a VS Code window on `path`. Does not wait."""
    subprocess.Popen(
        ["code", "-n", str(path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def make_server(
    root: Path, port: int, launch: Callable[[Path], None] = default_launch
) -> ThreadingHTTPServer:
    """A server for `root` on 127.0.0.1:`port` (0 picks a free port).
    `launch` opens a worktree; tests pass a fake."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if not self.is_own_host():
                self.send_error(403)
                return
            path = urlsplit(self.path).path
            if path == "/switch":
                self.send_response(405)
                self.send_header("Allow", "POST")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if path.startswith("/file/"):
                self.serve_file(unquote(path.removeprefix("/file/")))
                return
            if path != "/":
                self.send_error(404)
                return
            # Built on each request so the page is always current.
            body = render_page(list_initiatives(root), root).encode("utf-8")
            self.send_html(body)

        def do_POST(self):
            if urlsplit(self.path).path != "/switch":
                self.send_error(404)
                return
            if not self.is_same_origin():
                self.send_error(403)
                return
            form = self.read_form()
            if form is None:
                self.send_error(400)
                return
            wanted = form.get("id", [""])[0]
            # Fresh list each time: only a path git lists right now can run.
            matches = [w.path for w in list_worktrees(root) if worktree_id(w.path) == wanted]
            if not matches:
                self.send_error(404)
                return
            try:
                launch(matches[0])
            except OSError:
                self.send_error(500, "Could not start VS Code (is `code` on PATH?)")
                return
            self.send_response(303)
            self.send_header("Location", "/")
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
            """The urlencoded body, or None if its size is missing or too big."""
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return None
            if length < 0 or length > MAX_FORM_BYTES:
                return None
            body = self.rfile.read(length).decode("utf-8", errors="replace")
            return parse_qs(body)

        def serve_file(self, rel: str):
            try:
                target = resolve_file(root, rel)
            except (Refused, NotFound):
                # Outside paths get the same 404 as missing ones: no hint
                # about what exists beyond _bmad-output/.
                self.send_html(not_found_page().encode("utf-8"), status=404)
                return
            if target.suffix == ".md":
                body = render_markdown(target).encode("utf-8")
            else:
                body = target.read_bytes()
            # Served files are project content, not dashboard code. Sandboxing
            # gives them an opaque origin, so their requests to /switch carry
            # `Origin: null` and are refused.
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
        description="Dashboard of a BMad project's initiatives."
    )
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    root = args.project_root.expanduser().resolve()
    error = validate_root(root)
    if error:
        print(f"bmad-dash: {error}", file=sys.stderr)
        sys.exit(1)

    server = make_server(root, args.port)
    print(f"bmad-dash serving {root} at http://{HOST}:{server.server_port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
