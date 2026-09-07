# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.0] - 2026-09-07

The transfer engine was rebuilt after watching a 4 GB job on two real Google
accounts. All four problems had the same root: one rclone job per selected
item.

### Changed

- **Items are transferred one at a time.** Five items meant five jobs with four
  transfers each, so twenty parallel uploads: Google throttled them, the speed
  swung between 520 kB/s and 1.3 MB/s, and cancelling had twenty things to
  stop. Cancelling now stops one.
- **The job is sized before the bar is drawn against it.** Each job used to
  discover its own tree while running, so the total grew underneath: the same
  187 MB read as 65% of 287 MB and then 3.7% of 5.0 GB. The scan runs alongside
  the first copy, so nothing waits for it, and once it lands the denominator
  never moves again.
- The estimated time left is computed from the real remaining bytes instead of
  rclone's own guess, which was based on the same moving total.

### Fixed

- `job not found` during long transfers. rclone forgets a finished job after a
  minute, and the app kept asking about jobs that had completed early. The
  daemon now keeps them for the session, and a job that has gone missing counts
  as finished rather than as a failure.
- The forward-only progress bar added in 0.4.1 is gone. It hid the lurching by
  making the bar disagree with the numbers printed next to it; with a fixed
  total there is nothing to hide.

## [0.5.0] - 2026-09-07

### Added

- **Compare**, next to the transfer button. Select items and it counts them on
  both sides, read only, and shows the two totals against each other. The
  verification that runs after a transfer already said "everything arrived",
  and people still opened Drive in a browser to check by eye; this is the same
  answer, on demand, with the numbers visible.

## [0.4.1] - 2026-09-07

### Fixed

- The progress bar could step backwards. rclone keeps discovering files while
  it transfers, so the total it is measured against grows, and a plain
  bytes-over-total shrinks when it does. The bar only moves forward now: when
  the total grows it pauses instead of retreating.
- Cancelling a running transfer was a full-width red slab under a perfectly
  healthy progress bar. The label stays red, the button does not.

## [0.4.0] - 2026-09-07

Everything here came from the first run against two real Google accounts.

### Fixed

- Server-side copy is off by default, and the documentation no longer claims
  rclone falls back when Google refuses it. It does not: the copy request
  carries the destination account's credentials, so Google answers `404 File
  not found` on the source and the transfer stops, leaving a folder created and
  empty. A failed server-side transfer now says so and offers to run again
  without it.
- The progress and storage bars were rounded rectangles inside a stretched
  viewBox, which turned their corners into flattened ovals. They are drawn
  square now and clipped by a rounded wrapper.
- The progress bar sat at zero while rclone listed and compared, which on a
  real Drive looks like a hang. It shows a moving stripe and a running count of
  what has been looked at until there is a size to measure against.
- The file list now says it is loading from Google Drive rather than showing
  bare grey bars.
- The storage line read like a contradiction, because Gmail and Photos share
  the same quota as Drive. It states free space against the total instead.

## [0.3.0] - 2026-09-07

### Changed

- Connecting an account is one button. Give it a name, press Sign in with
  Google, approve in the browser. The client ID is no longer asked for up
  front.

### Added

- A five-step walkthrough for creating your own Google client ID, each step
  with the button that opens exactly the Google page it describes. Set it up
  once in Settings and every account connected afterwards uses it, which is
  what keeps sign-in working when rclone's shared client is retired.
- The client ID and secret are stored with the other settings, so they are
  entered once rather than per account.

### Security

- The stored client secret is never sent to the page, and never appears in any
  API response.
- The walkthrough's buttons ask the window to open a page by position in a
  fixed list, so the page cannot steer the browser anywhere else.

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

[Unreleased]: https://github.com/rlpb/driveferry/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/rlpb/driveferry/releases/tag/v0.6.0
[0.5.0]: https://github.com/rlpb/driveferry/releases/tag/v0.5.0
[0.4.1]: https://github.com/rlpb/driveferry/releases/tag/v0.4.1
[0.4.0]: https://github.com/rlpb/driveferry/releases/tag/v0.4.0
[0.3.0]: https://github.com/rlpb/driveferry/releases/tag/v0.3.0
[0.2.0]: https://github.com/rlpb/driveferry/releases/tag/v0.2.0
[0.1.0]: https://github.com/rlpb/driveferry/releases/tag/v0.1.0
