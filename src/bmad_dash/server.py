"""Local HTTP server for the dashboard. Binds 127.0.0.1 only."""

import argparse
import subprocess
import sys
from urllib.parse import unquote, urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from bmad_dash.files import NotFound, Refused, render_markdown, resolve_file
from bmad_dash.initiatives import list_initiatives
from bmad_dash.page import render_page

HOST = "127.0.0.1"


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


def make_server(root: Path, port: int) -> ThreadingHTTPServer:
    """A server for `root` on 127.0.0.1:`port` (0 picks a free port)."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            if path.startswith("/file/"):
                self.serve_file(unquote(path.removeprefix("/file/")))
                return
            if path != "/":
                self.send_error(404)
                return
            # Built on each request so the page is always current.
            body = render_page(list_initiatives(root), root).encode("utf-8")
            self.send_html(body)

        def serve_file(self, rel: str):
            try:
                target = resolve_file(root, rel)
            except Refused:
                self.send_error(403)
                return
            except NotFound:
                self.send_error(404)
                return
            if target.suffix == ".md":
                body = render_markdown(target).encode("utf-8")
            else:
                body = target.read_bytes()
            self.send_html(body)

        def send_html(self, body: bytes):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
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
