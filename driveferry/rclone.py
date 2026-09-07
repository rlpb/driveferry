"""Find rclone, run it as a local API daemon, and talk to its remote-control API.

DriveFerry never re-implements Google Drive: it drives rclone, which already
handles OAuth, resumable uploads, retries, checksums and server-side copy.

Security notes
--------------
* The daemon listens on 127.0.0.1 only, on a port picked at startup.
* Credentials are random per run and passed through the environment, never on
  the command line, so they do not show up in the machine's process list.
* rclone's own config file (with the OAuth tokens) is never read by DriveFerry.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from glob import glob
from pathlib import Path

#: rc methods DriveFerry calls. Checked against the live daemon by
#: :meth:`RcloneDaemon.check_supported`, so a missing method fails loudly at
#: startup instead of silently at the moment a user presses "Copy".
REQUIRED_RC_METHODS = (
    "config/create",
    "config/delete",
    "config/listremotes",
    "config/dump",
    "config/oauthstop",
    "core/stats",
    "core/version",
    "job/status",
    "job/stop",
    "operations/about",
    "operations/copyfile",
    "operations/deletefile",
    "operations/list",
    "operations/mkdir",
    "operations/purge",
    "operations/size",
    "sync/copy",
)

STARTUP_TIMEOUT = 25.0
CALL_TIMEOUT = 180.0


class RcloneError(RuntimeError):
    """An rclone call failed. ``payload`` holds rclone's own JSON error body."""

    def __init__(self, message, status=None, payload=None):
        super().__init__(message)
        self.status = status
        self.payload = payload or {}


class RcloneNotFound(RcloneError):
    """The rclone binary could not be located on this machine."""


def _windows_candidates():
    local = os.environ.get("LOCALAPPDATA", "")
    home = os.path.expanduser("~")
    found = []
    if local:
        found.append(os.path.join(local, "Microsoft", "WinGet", "Links", "rclone.exe"))
        # winget keeps the real binary in a versioned package folder and only
        # links it into Links/ once the shell PATH has been refreshed.
        found += sorted(
            glob(os.path.join(local, "Microsoft", "WinGet", "Packages", "Rclone.Rclone*", "*", "rclone.exe"))
        )
    found += [
        os.path.join(home, "scoop", "shims", "rclone.exe"),
        r"C:\ProgramData\chocolatey\bin\rclone.exe",
        r"C:\Program Files\rclone\rclone.exe",
        r"C:\rclone\rclone.exe",
    ]
    return found


def _unix_candidates():
    home = os.path.expanduser("~")
    return [
        "/opt/homebrew/bin/rclone",
        "/usr/local/bin/rclone",
        "/usr/bin/rclone",
        "/bin/rclone",
        "/snap/bin/rclone",
        os.path.join(home, ".local", "bin", "rclone"),
        os.path.join(home, "bin", "rclone"),
    ]


def find_rclone(explicit=None):
    """Return the path to an rclone binary, or ``None`` if there isn't one.

    A freshly installed rclone is often absent from ``PATH`` until the shell is
    restarted, so ``which`` alone is not enough: the well-known install
    locations of every mainstream package manager are searched as well.
    """
    for candidate in (explicit, os.environ.get("DRIVEFERRY_RCLONE")):
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())

    on_path = shutil.which("rclone")
    if on_path:
        return str(Path(on_path).resolve())

    candidates = _windows_candidates() if os.name == "nt" else _unix_candidates()
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    return None


def install_hint():
    """A copy-pasteable install command for the current platform."""
    if os.name == "nt":
        return "winget install --id Rclone.Rclone"
    if sys.platform == "darwin":
        return "brew install rclone"
    return "sudo -v ; curl https://rclone.org/install.sh | sudo bash"


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _no_window_kwargs():
    """Keep Windows from flashing a console window behind the app."""
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {
        "startupinfo": startupinfo,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }


class RcloneDaemon:
    """Own an ``rclone rcd`` process and speak its JSON API."""

    def __init__(self, binary=None, config_path=None, port=None):
        self.binary = binary or find_rclone()
        if not self.binary:
            raise RcloneNotFound("rclone was not found on this computer. Install it with: " + install_hint())
        self.config_path = config_path
        self.port = port or _free_port()
        self.user = "driveferry"
        self.password = secrets.token_urlsafe(32)
        self.process = None
        self._log = deque(maxlen=200)
        self._log_thread = None
        self._auth = "Basic " + base64.b64encode("{}:{}".format(self.user, self.password).encode()).decode()

    # -- process lifecycle -------------------------------------------------

    @property
    def base_url(self):
        return "http://127.0.0.1:{}".format(self.port)

    def start(self, timeout=STARTUP_TIMEOUT):
        if self.process and self.process.poll() is None:
            return self
        args = [self.binary, "rcd", "--rc-addr", "127.0.0.1:{}".format(self.port)]
        if self.config_path:
            args += ["--config", str(self.config_path)]
        env = dict(os.environ)
        # Credentials via env, not argv: argv is world-readable on this machine.
        env["RCLONE_RC_USER"] = self.user
        env["RCLONE_RC_PASS"] = self.password
        self.process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            text=True,
            bufsize=1,
            **_no_window_kwargs(),
        )
        self._log_thread = threading.Thread(target=self._drain_log, daemon=True)
        self._log_thread.start()
        self._wait_ready(timeout)
        return self

    def _drain_log(self):
        # rclone writes to the pipe continuously; an undrained pipe eventually
        # blocks the daemon itself.
        stream = self.process.stdout
        if stream is None:
            return
        for line in stream:
            self._log.append(line.rstrip())

    def _wait_ready(self, timeout):
        deadline = time.monotonic() + timeout
        last_error = None
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RcloneError(
                    "rclone exited during startup (code {}):\n{}".format(
                        self.process.returncode, self.log_tail()
                    )
                )
            try:
                self.call("rc/noop", timeout=2.0)
                return
            except RcloneError as exc:  # not up yet, or auth still initialising
                last_error = exc
                time.sleep(0.15)
        raise RcloneError(
            "rclone did not answer on {} within {:.0f}s ({}).\n{}".format(
                self.base_url, timeout, last_error, self.log_tail()
            )
        )

    def log_tail(self, lines=20):
        return "\n".join(list(self._log)[-lines:])

    def stop(self, timeout=5.0):
        if not self.process:
            return
        if self.process.poll() is None:
            try:
                self.call("core/quit", timeout=2.0)
            except RcloneError:
                self.process.terminate()
            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=timeout)
        self.process = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc_info):
        self.stop()
        return False

    # -- the API -----------------------------------------------------------

    def call(self, method, params=None, timeout=CALL_TIMEOUT):
        """POST to an rc method and return the decoded JSON body."""
        body = json.dumps(params or {}).encode("utf-8")
        request = urllib.request.Request(
            "{}/{}".format(self.base_url, method.lstrip("/")),
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": self._auth},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = {"error": raw.strip()}
            raise RcloneError(
                payload.get("error") or "rclone returned HTTP {}".format(exc.code),
                status=exc.code,
                payload=payload,
            ) from exc
        except urllib.error.URLError as exc:
            raise RcloneError("cannot reach rclone at {}: {}".format(self.base_url, exc.reason)) from exc
        except socket.timeout as exc:
            raise RcloneError("rclone did not answer {} within {:.0f}s".format(method, timeout)) from exc

    def check_supported(self):
        """Return the required rc methods this rclone build does not expose."""
        listed = self.call("rc/list").get("commands", [])
        available = {entry.get("Path") for entry in listed if isinstance(entry, dict)}
        if not available:
            # rc/list changed shape: treat as "cannot verify" rather than
            # inventing a pass, and let the caller decide.
            return None
        return [method for method in REQUIRED_RC_METHODS if method not in available]

    def version(self):
        return self.call("core/version")


def config_option_names(daemon):
    """The exact key names accepted inside a call's ``_config`` block.

    Those names are Go struct fields and have changed across rclone releases,
    so they are read from the running binary instead of hardcoded.
    """
    options = daemon.call("options/get")
    for key in ("main", "Main"):
        if isinstance(options.get(key), dict):
            return set(options[key])
    return set()
