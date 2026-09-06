# Contributing to DriveFerry

Thanks for taking a look. Issues, ideas and pull requests are all welcome.

## Getting set up

```bash
git clone https://github.com/rlpb/driveferry
cd driveferry
python -m venv .venv
# Windows:  .venv\Scripts\activate
source .venv/bin/activate
pip install -e ".[dev]"
python -m driveferry
```

You also need rclone on the machine:

| Platform | Command |
| --- | --- |
| Windows | `winget install --id Rclone.Rclone` |
| macOS | `brew install rclone` |
| Linux | `sudo -v ; curl https://rclone.org/install.sh \| sudo bash` |

## Running the tests

```bash
python -m pytest
```

The suite has two halves:

- unit tests with a fake rclone, which check what DriveFerry *asks* rclone to
  do and every refusal the local server makes;
- end-to-end tests in `tests/test_end_to_end.py` that start a real rclone
  daemon, copy real files between two local folders standing in for two
  accounts, verify them and delete them.

The end-to-end tests skip themselves when rclone is missing. CI fails if they
skip, because a green suite that never touched the transfer path is worse than
no suite.

## House rules

**Read the code before changing it.** In particular, `driveferry/server.py`
holds the security guards; if you change one, add or update the test that
covers it in `tests/test_server_security.py`.

**No inline styles in the web UI.** The page is served under a
Content-Security-Policy that forbids `style` attributes, including the ones
JavaScript writes. Anything dynamic is a class or an SVG geometry attribute.
This is not a preference: `element.style.width = ...` silently does nothing
under that policy, and the page keeps working, so the bug looks like a layout
mistake.

**No new runtime dependency without a reason in the pull request.** The app is
the Python standard library plus pywebview. Tests need pytest, nothing else.

**Never make deletion easier.** Move is deliberately three steps: copy, verify,
then delete on a separate confirmation. Changes that collapse them will be
declined.

**Interface strings live in `driveferry/web/i18n.js`.** English is the source
language. Adding a locale means copying the `en` block, translating it and
adding a plural table entry; the language picker finds it automatically.

## Style

Python is formatted and linted with `ruff`:

```bash
ruff check .
ruff format .
```

Comments explain why, not what. Commit messages are written in the imperative:
`fix the storage bar on backends without quota`, not `fixed` or `fixes`.

## Pull requests

- One topic per pull request.
- Say what you tested, and paste the output.
- Screenshots for anything visual, in both light and dark appearance.
