# Security policy

## Reporting a vulnerability

Report privately through GitHub: open the repository's **Security** tab and use
**Report a vulnerability**. That creates a draft advisory only the maintainers
can see.

Please do not open a public issue for a security problem.

You can expect a first reply within 7 days. If a fix is needed, the advisory
stays private until a patched release is out, and you are credited in it unless
you ask otherwise.

## Supported versions

The latest release is supported. DriveFerry is pre-1.0, so fixes land in a new
minor release rather than as patches to older ones.

## What DriveFerry does with your data

DriveFerry never sees a Google password and never stores an OAuth token itself.
Authentication is entirely rclone's: tokens live in rclone's own config file
(`rclone config file` prints its path), and DriveFerry only asks rclone to
perform operations.

Concretely:

- The rclone daemon listens on `127.0.0.1` on a port chosen at startup, with a
  random username and password generated per run. They are passed to rclone
  through environment variables, not on the command line, so they do not appear
  in the machine's process list.
- The app's own HTTP server also binds `127.0.0.1` only. Every `/api/` request
  must carry a session token that exists only inside the page DriveFerry
  served.
- The `Host` header must be a loopback name. This blocks DNS rebinding, where a
  hostile domain resolves to `127.0.0.1` so that a web page can reach servers
  on your machine.
- No CORS header is ever sent, and a request carrying a foreign `Origin` is
  refused, so a page on another site cannot call the API from your browser.
- The page runs under a strict Content-Security-Policy: no inline script, no
  inline style, no external origin, no framing.
- The API surface is an explicit list of ten operations. Remote names are
  validated against your configured rclone remotes, so the UI cannot make
  rclone open an arbitrary filesystem, and paths containing `..` are rejected.
- Deleting anything requires an explicit confirmation value in the request. The
  UI only sends it after a copy has been verified.
- Account secrets never reach the page: only the remote's name and backend type
  are exposed.

## Threat model, stated plainly

DriveFerry assumes the machine it runs on is trusted. Another program running
as your user on the same machine can read the app's memory and rclone's config,
and no local HTTP server can defend against that. The guards above are aimed at
web pages, other users, and network neighbours, not at malware already running
as you.
