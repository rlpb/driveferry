"""Entry point: start rclone, start the local API, open the app window.

The window is frameless: the title bar, the traffic lights and the drag region
are drawn by the page, so DriveFerry looks the same on every OS. The small
bridge below is what lets those painted buttons actually move the real window.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import webbrowser
from pathlib import Path

from . import __version__
from .rclone import RcloneDaemon, RcloneError, find_rclone, install_hint
from .server import Api, DriveFerryServer

WINDOW_TITLE = "DriveFerry"
WINDOW_SIZE = (1220, 780)
MIN_WINDOW_SIZE = (940, 620)


#: The live window, kept out of the bridge object on purpose. pywebview walks
#: the attributes of whatever is passed as `js_api`, and a Window holds a
#: native handle whose property chain is effectively infinite: storing it on
#: the bridge makes pywebview recurse until it raises, which silently breaks
#: the whole JavaScript bridge and with it the painted window buttons.
_WINDOW = None
_MAXIMISED = False


class WindowBridge:
    """The painted window buttons, wired to the real window.

    pywebview exposes this object to the page as ``window.pywebview.api``.
    Every method acts on DriveFerry's own window and nothing else. Keep it
    free of instance attributes.
    """

    def minimize(self):
        if _WINDOW:
            _WINDOW.minimize()
        return True

    def toggle_maximize(self):
        global _MAXIMISED
        if not _WINDOW:
            return False
        if _MAXIMISED:
            _WINDOW.restore()
        else:
            _WINDOW.maximize()
        _MAXIMISED = not _MAXIMISED
        return _MAXIMISED

    def close(self):
        if _WINDOW:
            _WINDOW.destroy()
        return True


def storage_path():
    """Where the window keeps its own state.

    pywebview starts in private mode, which throws the web view's storage away
    on exit: the appearance and language chosen in Settings would be forgotten
    at every launch. Pointing it at a real folder is what makes those settings
    stick. Nothing secret is kept here; the OAuth tokens stay in rclone's own
    config.
    """
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    path = Path(base) / "DriveFerry"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="driveferry",
        description="Move files and folders between Google Drive accounts, visually.",
    )
    parser.add_argument(
        "--browser", action="store_true", help="open in the default browser instead of the app window"
    )
    parser.add_argument("--framed", action="store_true", help="keep the operating system title bar")
    parser.add_argument("--port", type=int, default=0, help="fixed port for the local API")
    parser.add_argument("--rclone", help="path to the rclone binary")
    parser.add_argument("--config", help="path to an rclone config file")
    parser.add_argument("--verbose", action="store_true", help="log every HTTP request")
    parser.add_argument("--version", action="version", version="DriveFerry " + __version__)
    return parser


def open_window(url, use_browser=False, framed=False):
    """Show the app. Returns the name of the surface actually used."""
    if not use_browser:
        try:
            import webview
        except ImportError:
            print(
                "pywebview is not installed, falling back to the browser "
                "(pip install pywebview for the app window).",
                file=sys.stderr,
            )
        else:
            global _WINDOW
            _WINDOW = webview.create_window(
                WINDOW_TITLE,
                url,
                width=WINDOW_SIZE[0],
                height=WINDOW_SIZE[1],
                min_size=MIN_WINDOW_SIZE,
                frameless=not framed,
                easy_drag=False,  # the page marks its own drag region
                background_color="#F2F2F7",
                js_api=WindowBridge(),
            )
            webview.start(private_mode=False, storage_path=storage_path())
            return "webview"
    webbrowser.open(url)
    print("DriveFerry is running at {} - press Ctrl+C to stop.".format(url))
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    return "browser"


def main(argv=None):
    args = build_parser().parse_args(argv)

    binary = find_rclone(args.rclone)
    if not binary:
        print(
            "DriveFerry needs rclone, and could not find it.\n\n"
            "  Install it with:  {}\n\n"
            "Then start DriveFerry again. If rclone is installed somewhere unusual,\n"
            "point at it with:  driveferry --rclone /path/to/rclone".format(install_hint()),
            file=sys.stderr,
        )
        return 2

    daemon = RcloneDaemon(binary=binary, config_path=args.config)
    try:
        daemon.start()
    except RcloneError as exc:
        print("rclone failed to start: {}".format(exc), file=sys.stderr)
        return 3

    try:
        missing = daemon.check_supported()
        if missing:
            print(
                "This rclone build is missing API methods DriveFerry needs: {}.\n"
                "Update rclone and try again.".format(", ".join(missing)),
                file=sys.stderr,
            )
            return 4

        api = Api(daemon, prefs_path=Path(storage_path()) / "settings.json")
        server = DriveFerryServer(api, port=args.port, verbose=args.verbose)
        server.serve_in_background()
        print(
            "DriveFerry {} - rclone {} - {}".format(__version__, daemon.version().get("version"), server.url)
        )
        try:
            open_window(server.url, use_browser=args.browser, framed=args.framed)
        finally:
            server.shutdown()
            server.server_close()
    finally:
        daemon.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
