"""Preview config and probes: where each initiative's servers run, and
whether they answer.

The config is bmad-dash's own `projects.toml`, one table per project
(named by the project's root folder). Read on every page load, never cached.
The dashboard only reads: it never writes to a project and starts nothing.
"""

import http.client
import re
import sys
import tomllib
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, fields
from pathlib import Path
from urllib.parse import urlsplit

# Seconds for one probe, and for the whole batch.
PROBE_TIMEOUT = 2.0


@dataclass(frozen=True)
class PreviewConfig:
    """One project's entry in `projects.toml`. Every key is optional."""

    port_key: str | None = None  # the `.env` key holding the local port
    default_port: int | None = None  # used when `.env` doesn't give one
    local_start: str | None = None  # what `resume` offers to start it
    staging_start: str | None = None
    staging_rule: str | None = None


def _warn(message: str) -> None:
    print(f"bmad-dash: {message}", file=sys.stderr)


def _is_port(value: object) -> bool:
    # bool is an int subclass: `true` is not a port.
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 65535


def _entry(name: str, table: object) -> PreviewConfig | None:
    """One project's table as a PreviewConfig, or None (with a warning)
    when it or one of its keys has the wrong type."""
    if not isinstance(table, dict):
        _warn(f"projects.toml: [{name}] is not a table; ignored")
        return None
    values = {}
    for field in fields(PreviewConfig):
        if field.name not in table:
            continue
        value = table[field.name]
        if field.name == "default_port":
            ok = _is_port(value)
        else:
            ok = isinstance(value, str)
        if not ok:
            _warn(f"projects.toml: [{name}] {field.name} has the wrong type; entry ignored")
            return None
        values[field.name] = value
    return PreviewConfig(**values)


def load_config(path: Path) -> dict[str, PreviewConfig]:
    """Project name -> its config. Empty when the file is missing or isn't
    valid TOML; a bad entry is left out. Problems go to stderr, one line each."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as error:
        _warn(f"couldn't read {path}: {error}")
        return {}
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        _warn(f"{path} is not valid TOML ({error}); ignored")
        return {}
    config = {}
    for name, table in data.items():
        entry = _entry(name, table)
        if entry is not None:
            config[name] = entry
    return config


def _env_value(raw: str) -> str:
    """A `.env` value without quotes or a trailing ` # comment`."""
    raw = raw.strip()
    if raw[:1] in ("'", '"'):
        end = raw.find(raw[0], 1)
        return raw[1:end] if end != -1 else raw[1:]
    for marker in (" #", "\t#"):
        if marker in raw:
            raw = raw.split(marker, 1)[0]
    return raw.strip()


def env_port(worktree_path: Path, key: str) -> int | None:
    """The port `key` sets in the worktree's `.env`, or None when the file
    is missing, the key absent, or the value isn't a port (1-65535).
    Lines look like `KEY=3827`, `export KEY=3827` or `KEY=3030  # note`.
    The last line setting `key` wins, as in a shell."""
    try:
        text = (worktree_path / ".env").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    value = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        name, sep, raw = line.partition("=")
        if sep and name.strip() == key:
            value = _env_value(raw)
    # ASCII digits only: `isdigit` accepts `²`, which `int` rejects.
    if value is None or not (value.isascii() and value.isdecimal()):
        return None
    port = int(value)
    return port if _is_port(port) else None


def local_port(worktree_path: Path, config: PreviewConfig) -> int | None:
    """The worktree's local port: its `.env` value, else `default_port`.
    None when the entry gives neither a key nor a default."""
    port = env_port(worktree_path, config.port_key) if config.port_key else None
    return port if port is not None else config.default_port


def local_url(worktree_path: Path, config: PreviewConfig) -> str | None:
    """`http://localhost:<port>/` for the worktree, or None (no port)."""
    port = local_port(worktree_path, config)
    return f"http://localhost:{port}/" if port is not None else None


def probe(url: str, timeout: float = PROBE_TIMEOUT) -> bool:
    """True when `url` answers with any HTTP response. One GET; redirects
    aren't followed (`http.client` never does), so a 302 counts as up.
    Refused, timeout, DNS failure or a broken response: False."""
    parts = urlsplit(url)
    connection_class = (
        http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    )
    try:
        connection = connection_class(parts.hostname or "", parts.port, timeout=timeout)
    except (ValueError, OSError):
        return False
    try:
        connection.request("GET", parts.path or "/")
        connection.getresponse()
        return True
    except (OSError, http.client.HTTPException, ValueError):
        return False
    finally:
        connection.close()


def probe_batch(
    rows: Mapping[str, list[str]], timeout: float = PROBE_TIMEOUT
) -> dict[str, dict[str, bool]]:
    """Probe every row's URLs at once: row key -> {url: up}.
    The whole batch takes at most about `timeout`: a probe still running
    then counts as down, and the pool isn't waited on (each probe has the
    same socket timeout, so stray threads end soon after)."""
    jobs = [(key, url) for key, urls in rows.items() for url in urls]
    results: dict[str, dict[str, bool]] = {key: {} for key in rows}
    if not jobs:
        return results
    pool = ThreadPoolExecutor(max_workers=len(jobs))
    try:
        futures = {(key, url): pool.submit(probe, url, timeout) for key, url in jobs}
        wait(futures.values(), timeout=timeout)
        for (key, url), future in futures.items():
            results[key][url] = future.done() and future.result()
    finally:
        pool.shutdown(wait=False)
    return results


# Branches the owid-lxc rule gives no staging server.
_NO_STAGING_PREFIXES = ("dependabot/", "dependabot-", "graphite-base-")


def _owid_lxc_host(branch: str) -> str | None:
    """owid/ops' container name for `branch`. Copied from `get_container_name`
    in owid/ops `shared/shared.sh`: keep the two in step by hand."""
    if branch.startswith(_NO_STAGING_PREFIXES):
        return None
    name = branch.replace("/", "-").replace(".", "-").replace("_", "-")
    host = ("staging-site-" + name[:28]).rstrip("-")
    # Ops refuses anything else too; it also keeps the href and probe safe.
    if not re.fullmatch(r"[A-Za-z0-9-]+", host):
        return None
    return host


def staging_host(rule: str, branch: str) -> str | None:
    """The staging host `rule` gives for `branch`, or None (no server, or
    an unknown rule). Only `owid-lxc` is known."""
    if rule == "owid-lxc":
        return _owid_lxc_host(branch)
    return None


def staging_url(host: str) -> str:
    """`http://<host>/`."""
    return f"http://{host}/"
