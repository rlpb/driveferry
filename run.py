"""Packaging entry point.

PyInstaller needs a plain script to start from; `python -m driveferry` stays
the way to run it from a checkout.
"""

from driveferry.app import main

if __name__ == "__main__":
    raise SystemExit(main())
