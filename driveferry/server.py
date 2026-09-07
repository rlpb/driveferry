"""The local HTTP API the DriveFerry window talks to.

Everything is served from 127.0.0.1 on a random port. Two guards keep other
programs on the machine out:

* every ``/api/`` request must carry the session token, which only ever exists
  inside the page DriveFerry itself served;
* the ``Host`` header must be a loopback name, which stops a hostile website
  from reaching this server by pointing a domain at 127.0.0.1 (DNS rebinding).

The browser cannot set the token header on a cross-origin request without a
preflight, and no CORS headers are ever sent, so the preflight fails.
"""

from __future__ import annotations

import json
import mimetypes
import posixpath
import re
import secrets
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .accounts import AccountSetup
from .rclone import RcloneError

WEB_ROOT = Path(__file__).parent / "web"

#: Served without a token; anything else 404s. An explicit list beats path
#: sanitising: there is no traversal to get wrong.
STATIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/app.css": "app.css",
    "/app.js": "app.js",
    "/i18n.js": "i18n.js",
    "/icon.svg": "icon.svg",
}

MAX_BODY = 1 << 20  # 1 MiB: every legitimate request is a few hundred bytes.

#: A refusal is only useful if the client can read it. Answering before the
#: body has been sent resets the connection, so an over-long or unauthorised
#: request is drained first - up to this cap, past which the peer is not
#: worth the courtesy.
DRAIN_CAP = 16 << 20
DRAIN_CHUNK = 64 << 10


class ApiError(Exception):
    def __init__(self, message, status=HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.status = status


def _clean_path(value):
    """Normalise a path inside a remote to rclone's ``a/b/c`` form."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ApiError("path must be a string")
    if "\x00" in value:
        raise ApiError("path contains a null byte")
    parts = []
    for part in value.replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise ApiError("path may not contain '..'")
        parts.append(part)
    return "/".join(parts)


def _join(path, name):
    return posixpath.join(path, name) if path else name


#: Interface preferences, with the only values each one accepts. The page never
#: keeps them in browser storage: the local server listens on a fresh port every
#: run, and browser storage is per origin, so a new port means an empty store
#: and settings that silently reset at every launch.
PREF_VALUES = {
    "theme": {"system", "light", "dark"},
    "locale": {"system", "en", "it"},
}

#: Free-text preferences, with the shape each one must have. The Google client
#: is kept here so it is set up once and every later account reuses it: that is
#: what makes signing in work forever without depending on rclone's shared
#: client. A desktop OAuth client secret is not a real secret (Google calls
#: these public clients, and it ships inside every desktop app that uses one),
#: which is why storing it beside the other settings is acceptable.
PREF_TEXT = {
    "google_client_id": re.compile(r"^[A-Za-z0-9._-]{0,200}$"),
    "google_client_secret": re.compile(r"^[A-Za-z0-9._~-]{0,200}$"),
}

DEFAULT_PREFS = {"theme": "system", "locale": "system", "google_client_id": "", "google_client_secret": ""}

#: Never sent to the page. The client id is shown so the user can check it; the
#: secret only ever travels towards rclone.
PRIVATE_PREFS = ("google_client_secret",)


class Api:
    """The operations the UI is allowed to ask for, and nothing else."""

    def __init__(self, daemon, prefs_path=None):
        self.daemon = daemon
        self.prefs_path = Path(prefs_path) if prefs_path else None
        self._remotes_cache = None
        self.prefs = self._load_prefs()
        self.setup = AccountSetup(daemon)

    # -- preferences -------------------------------------------------------

    def _load_prefs(self):
        prefs = dict(DEFAULT_PREFS)
        if not self.prefs_path or not self.prefs_path.is_file():
            return prefs
        try:
            stored = json.loads(self.prefs_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return prefs  # a corrupt file is not worth failing startup over
        if isinstance(stored, dict):
            for key, allowed in PREF_VALUES.items():
                if stored.get(key) in allowed:
                    prefs[key] = stored[key]
            for key, pattern in PREF_TEXT.items():
                value = stored.get(key)
                if isinstance(value, str) and pattern.match(value):
                    prefs[key] = value
        return prefs

    def public_prefs(self):
        """What the page is allowed to see."""
        return {key: value for key, value in self.prefs.items() if key not in PRIVATE_PREFS}

    def op_prefs_set(self, payload):
        for key, allowed in PREF_VALUES.items():
            if key in payload:
                if payload[key] not in allowed:
                    raise ApiError("invalid value for {}".format(key))
                self.prefs[key] = payload[key]
        for key, pattern in PREF_TEXT.items():
            if key in payload:
                value = payload[key]
                if not isinstance(value, str) or not pattern.match(value.strip()):
                    raise ApiError("that {} does not look right".format(key.replace("_", " ")))
                self.prefs[key] = value.strip()
        if self.prefs_path:
            try:
                self.prefs_path.parent.mkdir(parents=True, exist_ok=True)
                self.prefs_path.write_text(json.dumps(self.prefs, indent=2), encoding="utf-8")
            except OSError as exc:
                raise ApiError("could not save settings: {}".format(exc)) from exc
        return self.public_prefs()

    # -- helpers -----------------------------------------------------------

    def remotes(self, refresh=False):
        if self._remotes_cache is None or refresh:
            names = self.daemon.call("config/listremotes").get("remotes") or []
            dump = self.daemon.call("config/dump")
            self._remotes_cache = [
                {"name": name, "type": (dump.get(name) or {}).get("type", "unknown")} for name in names
            ]
        return self._remotes_cache

    def _fs(self, remote, path):
        """Build an rclone ``remote:path`` string from validated pieces.

        The remote name is checked against the configured ones so the UI can
        never hand rclone an arbitrary filesystem (a URL, a local drive) that
        the user did not set up.
        """
        if not isinstance(remote, str) or not remote:
            raise ApiError("missing remote")
        if remote not in {entry["name"] for entry in self.remotes()}:
            self.remotes(refresh=True)
            if remote not in {entry["name"] for entry in self.remotes()}:
                raise ApiError("unknown remote: {}".format(remote), HTTPStatus.NOT_FOUND)
        return "{}:{}".format(remote, _clean_path(path))

    @staticmethod
    def _names(payload):
        names = payload.get("names") or []
        if not isinstance(names, list) or not names:
            raise ApiError("select at least one item")
        clean = []
        for name in names:
            if not isinstance(name, str) or not name or "/" in name or "\\" in name or name in (".", ".."):
                raise ApiError("invalid item name: {!r}".format(name))
            clean.append(name)
        return clean

    def _transfer_config(self, payload):
        options = {"DryRun": bool(payload.get("dry_run"))}
        if payload.get("server_side"):
            # Verified against rclone v1.75 `options/get`: this is the exact
            # key name of the global --server-side-across-configs flag.
            options["ServerSideAcrossConfigs"] = True
        return options

    # -- operations --------------------------------------------------------

    def op_state(self, payload):
        version = self.daemon.version()
        return {
            "rclone_version": version.get("version"),
            "rclone_binary": self.daemon.binary,
            "remotes": self.remotes(refresh=True),
            "google_client_id": self.prefs.get("google_client_id", ""),
        }

    def op_list(self, payload):
        fs = self._fs(payload.get("remote"), payload.get("path"))
        listing = self.daemon.call("operations/list", {"fs": fs, "remote": ""})
        entries = []
        for item in listing.get("list") or []:
            entries.append(
                {
                    "name": item.get("Name"),
                    "is_dir": bool(item.get("IsDir")),
                    "size": item.get("Size", 0),
                    "mtime": item.get("ModTime"),
                    "mime": item.get("MimeType", ""),
                }
            )
        entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
        return {"entries": entries, "path": _clean_path(payload.get("path"))}

    def op_about(self, payload):
        try:
            about = self.daemon.call("operations/about", {"fs": self._fs(payload.get("remote"), "")})
        except RcloneError as exc:
            return {"supported": False, "error": str(exc)}
        return {
            "supported": True,
            "total": about.get("total"),
            "used": about.get("used"),
            "free": about.get("free"),
            "trashed": about.get("trashed"),
        }

    def op_mkdir(self, payload):
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip() or "/" in name or "\\" in name:
            raise ApiError("invalid folder name")
        base = _clean_path(payload.get("path"))
        self.daemon.call(
            "operations/mkdir",
            {"fs": self._fs(payload.get("remote"), ""), "remote": _join(base, name.strip())},
        )
        return {"ok": True}

    def op_transfer(self, payload):
        """Start one rclone job per selected item, under a shared stats group."""
        names = self._names(payload)
        src_path = _clean_path(payload.get("src_path"))
        dst_path = _clean_path(payload.get("dst_path"))
        src_remote = payload.get("src_remote")
        dst_remote = payload.get("dst_remote")
        options = self._transfer_config(payload)
        group = "df-" + secrets.token_hex(6)

        jobs = []
        dirs = set(payload.get("dirs") or [])
        for name in names:
            is_dir = name in dirs
            if is_dir:
                request = {
                    "srcFs": self._fs(src_remote, _join(src_path, name)),
                    "dstFs": self._fs(dst_remote, _join(dst_path, name)),
                }
                method = "sync/copy"
            else:
                request = {
                    "srcFs": self._fs(src_remote, src_path),
                    "srcRemote": name,
                    "dstFs": self._fs(dst_remote, dst_path),
                    "dstRemote": name,
                }
                method = "operations/copyfile"
            request.update({"_async": True, "_group": group, "_config": options})
            result = self.daemon.call(method, request)
            jobs.append({"jobid": result["jobid"], "name": name, "is_dir": is_dir})

        return {
            "group": group,
            "jobs": jobs,
            "dry_run": bool(payload.get("dry_run")),
            "server_side": bool(payload.get("server_side")),
        }

    def op_transfer_status(self, payload):
        group = payload.get("group")
        if not isinstance(group, str) or not group.startswith("df-"):
            raise ApiError("invalid transfer group")
        jobs = payload.get("jobs") or []
        statuses = []
        for jobid in jobs:
            if not isinstance(jobid, int):
                raise ApiError("invalid job id")
            status = self.daemon.call("job/status", {"jobid": jobid})
            statuses.append(
                {
                    "jobid": jobid,
                    "finished": bool(status.get("finished")),
                    "success": bool(status.get("success")),
                    "error": status.get("error") or "",
                    "duration": status.get("duration"),
                }
            )
        try:
            stats = self.daemon.call("core/stats", {"group": group})
        except RcloneError:
            stats = {}
        return {
            "jobs": statuses,
            "finished": all(entry["finished"] for entry in statuses) if statuses else True,
            "failed": [entry for entry in statuses if entry["finished"] and not entry["success"]],
            "stats": {
                "bytes": stats.get("bytes", 0),
                "total_bytes": stats.get("totalBytes", 0),
                "speed": stats.get("speed", 0),
                "eta": stats.get("eta"),
                "errors": stats.get("errors", 0),
                "transfers": stats.get("transfers", 0),
                "total_transfers": stats.get("totalTransfers", 0),
                "server_side_copies": stats.get("serverSideCopies", 0),
                "transferring": [
                    {
                        "name": item.get("name"),
                        "bytes": item.get("bytes", 0),
                        "size": item.get("size", 0),
                        "speed": item.get("speed", 0),
                        "percentage": item.get("percentage", 0),
                    }
                    for item in (stats.get("transferring") or [])
                ],
            },
        }

    def op_transfer_cancel(self, payload):
        stopped = []
        for jobid in payload.get("jobs") or []:
            if not isinstance(jobid, int):
                raise ApiError("invalid job id")
            try:
                self.daemon.call("job/stop", {"jobid": jobid})
                stopped.append(jobid)
            except RcloneError:
                pass  # already finished
        return {"stopped": stopped}

    def op_verify(self, payload):
        """Compare file count and byte total for each transferred item.

        rclone already checksums every file as it transfers, so this answers
        the remaining question: did every file arrive?
        """
        names = self._names(payload)
        dirs = set(payload.get("dirs") or [])
        src_path = _clean_path(payload.get("src_path"))
        dst_path = _clean_path(payload.get("dst_path"))
        results = []
        for name in names:
            if name in dirs:
                src_fs = self._fs(payload.get("src_remote"), _join(src_path, name))
                dst_fs = self._fs(payload.get("dst_remote"), _join(dst_path, name))
                src = self.daemon.call("operations/size", {"fs": src_fs})
                try:
                    dst = self.daemon.call("operations/size", {"fs": dst_fs})
                except RcloneError as exc:
                    results.append({"name": name, "ok": False, "error": str(exc)})
                    continue
            else:
                src = self._file_size(payload.get("src_remote"), src_path, name)
                dst = self._file_size(payload.get("dst_remote"), dst_path, name)
                if dst is None:
                    results.append({"name": name, "ok": False, "error": "missing at destination"})
                    continue
            ok = src.get("count") == dst.get("count") and src.get("bytes") == dst.get("bytes")
            results.append(
                {
                    "name": name,
                    "ok": ok,
                    "src": {"count": src.get("count"), "bytes": src.get("bytes")},
                    "dst": {"count": dst.get("count"), "bytes": dst.get("bytes")},
                }
            )
        return {"results": results, "ok": all(entry["ok"] for entry in results)}

    def _file_size(self, remote, path, name):
        listing = self.daemon.call("operations/list", {"fs": self._fs(remote, path), "remote": ""})
        for item in listing.get("list") or []:
            if item.get("Name") == name and not item.get("IsDir"):
                return {"count": 1, "bytes": item.get("Size", 0)}
        return None

    def op_delete(self, payload):
        """Delete items at the source. Only ever called by the guarded move flow."""
        if payload.get("confirm") != "DELETE":
            raise ApiError("delete requires an explicit confirmation")
        names = self._names(payload)
        dirs = set(payload.get("dirs") or [])
        path = _clean_path(payload.get("path"))
        remote = payload.get("remote")
        deleted, failed = [], []
        for name in names:
            try:
                method = "operations/purge" if name in dirs else "operations/deletefile"
                self.daemon.call(method, {"fs": self._fs(remote, ""), "remote": _join(path, name)})
                deleted.append(name)
            except RcloneError as exc:
                failed.append({"name": name, "error": str(exc)})
        return {"deleted": deleted, "failed": failed}

    # -- connecting an account --------------------------------------------

    def op_account_connect(self, payload):
        """Start rclone's Drive setup. Google's consent page opens in the
        browser, which is where a password belongs; nothing is typed here."""
        # A client saved in Settings is set up once and reused by every later
        # account, which is what keeps signing in working without depending on
        # rclone's shared client.
        client_id = (payload.get("client_id") or self.prefs.get("google_client_id") or "").strip()
        client_secret = (
            payload.get("client_secret")
            if payload.get("client_id")
            else self.prefs.get("google_client_secret")
        ) or ""
        try:
            self.setup.start(
                payload.get("name") or "",
                client_id=client_id,
                client_secret=client_secret.strip(),
                allow_shared_client=bool(payload.get("allow_shared_client")) or not client_id,
            )
        except ValueError as exc:
            raise ApiError(str(exc)) from exc
        return self.setup.status()

    def op_account_status(self, payload):
        status = self.setup.status()
        if status.get("stage") == "done":
            self.remotes(refresh=True)
        return status

    def op_account_cancel(self, payload):
        return self.setup.cancel()

    def op_account_forget(self, payload):
        """Remove an account from rclone's config. The data in Drive is untouched."""
        if payload.get("confirm") != "FORGET":
            raise ApiError("forgetting an account requires an explicit confirmation")
        name = payload.get("name")
        if name not in {entry["name"] for entry in self.remotes(refresh=True)}:
            raise ApiError("unknown account: {}".format(name), HTTPStatus.NOT_FOUND)
        self.daemon.call("config/delete", {"name": name})
        self.remotes(refresh=True)
        return {"forgotten": name}

    OPERATIONS = {
        "state": op_state,
        "account_connect": op_account_connect,
        "account_status": op_account_status,
        "account_cancel": op_account_cancel,
        "account_forget": op_account_forget,
        "list": op_list,
        "about": op_about,
        "mkdir": op_mkdir,
        "transfer": op_transfer,
        "transfer_status": op_transfer_status,
        "transfer_cancel": op_transfer_cancel,
        "verify": op_verify,
        "delete": op_delete,
        "prefs_set": op_prefs_set,
    }

    def dispatch(self, name, payload):
        handler = self.OPERATIONS.get(name)
        if handler is None:
            raise ApiError("unknown operation: {}".format(name), HTTPStatus.NOT_FOUND)
        return handler(self, payload)


class _Handler(BaseHTTPRequestHandler):
    server_version = "DriveFerry"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # -- guards ------------------------------------------------------------

    def _host_is_loopback(self):
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return host in ("127.0.0.1", "localhost", "::1")

    def _origin_ok(self):
        origin = self.headers.get("Origin")
        if origin is None:
            return True  # same-origin fetch from our own page sends no Origin
        return origin in self.server.allowed_origins

    def _authorised(self):
        return secrets.compare_digest(self.headers.get("X-DriveFerry-Token", ""), self.server.token)

    # -- plumbing ----------------------------------------------------------

    def log_message(self, fmt, *args):
        if self.server.verbose:
            super().log_message(fmt, *args)

    def _send(self, status, body, content_type="application/json; charset=utf-8"):
        payload = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        # No inline styles anywhere: dynamic sizing uses CSS custom properties
        # set through classes, and SVG presentation attributes.
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def do_GET(self):
        if not self._host_is_loopback():
            return self._send(HTTPStatus.FORBIDDEN, {"error": "non-loopback host"})
        route = self.path.split("?", 1)[0]
        filename = STATIC_FILES.get(route)
        if filename is None:
            return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
        try:
            data = (WEB_ROOT / filename).read_bytes()
        except OSError:
            return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
        if filename == "index.html":
            data = data.replace(b"__DRIVEFERRY_TOKEN__", self.server.token.encode())
            data = data.replace(b"__DRIVEFERRY_PLATFORM__", self.server.platform.encode())
            # Values come from a fixed whitelist or a validated pattern, so
            # there is nothing to escape and nothing a stored file could inject
            # into the page. The client secret is not among them.
            for key, value in self.server.api.public_prefs().items():
                data = data.replace("__DRIVEFERRY_{}__".format(key.upper()).encode(), value.encode())
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return self._send(HTTPStatus.OK, data, content_type + "; charset=utf-8")

    def _refusals(self, length):
        """Every reason to turn a POST away, in order. Yields at most one."""
        if not self._host_is_loopback():
            yield HTTPStatus.FORBIDDEN, {"error": "non-loopback host"}
        elif not self._origin_ok():
            yield HTTPStatus.FORBIDDEN, {"error": "bad origin"}
        elif not self.path.startswith("/api/"):
            yield HTTPStatus.NOT_FOUND, {"error": "not found"}
        elif not self._authorised():
            yield HTTPStatus.UNAUTHORIZED, {"error": "bad session token"}
        elif length > MAX_BODY:
            yield HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "body too large"}

    def _drain(self, length):
        """Swallow a body we are about to refuse, so the client can read the
        answer instead of a connection reset."""
        if length > DRAIN_CAP:
            self.close_connection = True
            return
        remaining = length
        while remaining > 0:
            chunk = self.rfile.read(min(DRAIN_CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._send(HTTPStatus.BAD_REQUEST, {"error": "bad content length"})
        if length < 0:
            return self._send(HTTPStatus.BAD_REQUEST, {"error": "bad content length"})

        for refusal in self._refusals(length):
            self._drain(length)
            return self._send(*refusal)

        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            return self._send(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON"})
        if not isinstance(payload, dict):
            return self._send(HTTPStatus.BAD_REQUEST, {"error": "payload must be an object"})

        operation = self.path[len("/api/") :].split("?", 1)[0]
        try:
            return self._send(HTTPStatus.OK, self.server.api.dispatch(operation, payload))
        except ApiError as exc:
            return self._send(exc.status, {"error": str(exc)})
        except RcloneError as exc:
            return self._send(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
        except Exception as exc:  # keep the window alive on an unexpected fault
            return self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": repr(exc)})


class DriveFerryServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, api, port=0, verbose=False):
        super().__init__(("127.0.0.1", port), _Handler)
        self.api = api
        self.token = secrets.token_urlsafe(32)
        self.verbose = verbose
        # The page draws the window controls, and where they go depends on
        # the platform: traffic lights on the left for macOS, the usual
        # minimise/maximise/close on the right everywhere else.
        self.platform = sys.platform
        self.allowed_origins = {
            "http://127.0.0.1:{}".format(self.server_port),
            "http://localhost:{}".format(self.server_port),
        }

    @property
    def url(self):
        return "http://127.0.0.1:{}/".format(self.server_port)

    def serve_in_background(self):
        thread = threading.Thread(target=self.serve_forever, name="driveferry-http", daemon=True)
        thread.start()
        return thread
