"""Entry point: start rclone, start the local API, open the app window.

The window is frameless: the title bar, the traffic lights and the drag region
are drawn by the page, so DriveFerry looks the same on every OS. The small
bridge below is what lets those painted buttons actually move the real window.
"""

from __future__ import annotations

import argparse
import sys
import time
import webbrowser

from . import __version__
from .rclone import RcloneDaemon, RcloneError, find_rclone, install_hint
from .server import Api, DriveFerryServer

WINDOW_TITLE = "DriveFerry"
WINDOW_SIZE = (1220, 780)
MIN_WINDOW_SIZE = (940, 620)


class WindowBridge:
    """The painted window buttons, wired to the real window.

    pywebview exposes this object to the page as ``window.pywebview.api``.
    Every method acts on DriveFerry's own window and nothing else.
    """

    def __init__(self):
        self.window = None
        self._maximised = False

    def minimize(self):
        if self.window:
            self.window.minimize()
        return True

    def toggle_maximize(self):
        if not self.window:
            return False
        if self._maximised:
            self.window.restore()
        else:
            self.window.maximize()
        self._maximised = not self._maximised
        return self._maximised

    def close(self):
        if self.window:
            self.window.destroy()
        return True

    def platform(self):
        return sys.platform


def build_parser():
    parser = argparse.ArgumentParser(
        prog="driveferry",
        description="Move folders between Google Drive accounts, visually.",
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
            bridge = WindowBridge()
            bridge.window = webview.create_window(
                WINDOW_TITLE,
                url,
                width=WINDOW_SIZE[0],
                height=WINDOW_SIZE[1],
                min_size=MIN_WINDOW_SIZE,
                frameless=not framed,
                easy_drag=False,  # the page marks its own drag region
                background_color="#F2F2F7",
                js_api=bridge,
            )
            webview.start()
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

        server = DriveFerryServer(Api(daemon), port=args.port, verbose=args.verbose)
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
