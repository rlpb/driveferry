# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-09-07

### Added

- Connect a Google account from inside the app. A wizard drives rclone's own
  configuration state machine and hands the sign-in to Google's consent page in
  the browser, so no terminal and no password inside DriveFerry.
- The wizard takes your own OAuth client ID, and says why that matters: rclone
  reports that its shared Google Drive client is being retired during 2026.
- Forget an account from Settings. It leaves Google Drive untouched.

### Changed

- Licence moved from MIT to Apache 2.0, which adds an explicit patent grant and
  asks that the NOTICE file and a statement of changes travel with derivatives.
- The setup documentation no longer walks through `rclone config`, and no longer
  suggests leaving the client ID empty.

### Fixed

- Cancelling a connection reports a cancellation instead of rclone's internal
  error, and removes the half-written remote that `config/create` leaves behind.

## [0.1.0] - 2026-09-06

First public release.

### Added

- Two-pane browser for any pair of rclone remotes, with folder navigation,
  breadcrumbs, multi-select, keyboard navigation and drag and drop between
  panes.
- Copy across accounts, with live progress, transfer speed and ETA taken from
  rclone's own statistics.
- Move as three explicit steps: copy, verify that every file arrived, and only
  then delete the originals on a separate confirmation.
- Verification that compares file count and byte total on both sides before any
  deletion is offered.
- Dry run, which asks rclone to report what it would transfer and write
  nothing.
- Optional server-side copy (`--server-side-across-configs`), so Google moves
  the bytes directly when it allows it between the two accounts.
- Frameless native window with its own title bar, drawn by the app on Windows,
  macOS and Linux.
- Light and dark appearance, following the system or pinned in Settings.
- English and Italian interface, following the system or pinned in Settings.
- Storage meter per account, where the backend reports quota.
- New folder on either side.

### Security

- Local API bound to `127.0.0.1`, protected by a per-run session token, a
  loopback `Host` check against DNS rebinding, an `Origin` check, and no CORS
  headers.
- rclone's remote-control credentials are random per run and passed through the
  environment, never on the command line.
- Strict Content-Security-Policy on the page: no inline script or style, no
  external origins, no framing.
- Account secrets never leave the Python process; the UI only receives a remote
  name and its backend type.

[Unreleased]: https://github.com/rlpb/driveferry/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/rlpb/driveferry/releases/tag/v0.2.0
[0.1.0]: https://github.com/rlpb/driveferry/releases/tag/v0.1.0
