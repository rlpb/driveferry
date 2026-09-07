"""Connect a Google account from inside the app.

rclone's own `config create` is a state machine: it asks a question, you answer,
it asks the next one. Over the remote-control API each hop is a `config/create`
call carrying the previous state. Two questions stand between a new remote and
Google's consent screen, and this module answers both from what the user typed
in the wizard, so nobody has to open a terminal.

Sequence, as walked against rclone v1.75.1:

    state 'client_id_warning'                 asks config_shared_client_id
    state '*oauth-islocal,teamdrive,oauth,'   asks config_is_local
    (rclone opens the browser and blocks until Google answers)

The call that opens the browser does not return until consent is finished, so
the whole walk runs on its own thread and the window polls for progress.
"""

from __future__ import annotations

import re
import threading
import time

from .rclone import RcloneError

#: rclone accepts a wider set, but a name people will read in two pickers is
#: better kept plain, and this is also what stops a name from being smuggled
#: into a config file as something else.
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,31}$")

#: How long to wait for someone to finish the Google consent screen.
CONSENT_TIMEOUT = 10 * 60

#: Answers to rclone's questions. Anything not listed here is a question this
#: wizard has not been taught, and it stops rather than guessing.
FIXED_ANSWERS = {
    "config_is_local": "true",  # this machine has the browser
    "config_change_team_drive": "false",
    "config_fs_advanced": "false",
}

MAX_HOPS = 12


class AccountSetup:
    """One connection attempt at a time, driven on a background thread."""

    def __init__(self, daemon):
        self.daemon = daemon
        self._lock = threading.Lock()
        self._thread = None
        self._cancelled = threading.Event()
        self._state = {"stage": "idle", "name": None, "error": "", "question": ""}

    # -- reporting ---------------------------------------------------------

    def _set(self, **fields):
        with self._lock:
            self._state.update(fields)

    def _fail(self, message):
        """Report a failure, unless the user is the one who stopped it.

        Cancelling makes rclone raise ("oauth authentication was cancelled"),
        and the walking thread would otherwise turn the user's own choice into
        a red error.
        """
        if self._cancelled.is_set():
            self._set(stage="cancelled", error="", question="")
        else:
            self._set(stage="error", error=message, question="")

    def status(self):
        with self._lock:
            return dict(self._state)

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    # -- the walk ----------------------------------------------------------

    def start(self, name, client_id="", client_secret="", allow_shared_client=False):
        if self.running:
            raise ValueError("a connection is already in progress")
        if not NAME_PATTERN.match(name or ""):
            raise ValueError(
                "the name may use letters, digits, spaces, hyphens and underscores, "
                "and must start with a letter or digit"
            )
        existing = self.daemon.call("config/listremotes").get("remotes") or []
        if name in existing:
            raise ValueError("there is already an account called {!r}".format(name))
        for label, value in (("client ID", client_id), ("client secret", client_secret)):
            if value and (len(value) > 400 or "\n" in value or "\x00" in value):
                raise ValueError("that {} does not look right".format(label))
        if not client_id and not allow_shared_client:
            raise ValueError("either provide your own client ID or accept the shared one")

        self._cancelled.clear()
        self._set(stage="starting", name=name, error="", question="")
        self._thread = threading.Thread(
            target=self._run,
            args=(name, client_id.strip(), client_secret.strip(), allow_shared_client),
            name="driveferry-connect",
            daemon=True,
        )
        self._thread.start()

    def _run(self, name, client_id, client_secret, allow_shared_client):
        parameters = {"scope": "drive"}
        if client_id:
            parameters["client_id"] = client_id
            parameters["client_secret"] = client_secret
        answers = dict(FIXED_ANSWERS)
        answers["config_shared_client_id"] = "true" if allow_shared_client else "false"

        base = {"name": name, "type": "drive", "parameters": parameters}
        deadline = time.monotonic() + CONSENT_TIMEOUT

        try:
            result = self.daemon.call(
                "config/create",
                dict(base, opt={"nonInteractive": True, "noObscure": True}),
                timeout=60,
            )
            for _ in range(MAX_HOPS):
                if result.get("Error"):
                    raise RuntimeError(result["Error"])
                state = result.get("State")
                if not state:
                    break

                question = (result.get("Option") or {}).get("Name")
                answer = answers.get(question)
                if answer is None:
                    raise RuntimeError(
                        "rclone asked something this wizard does not know how to answer "
                        "({!r}). Connect this account with `rclone config` instead.".format(question)
                    )

                # The hop that carries config_is_local is the one that opens the
                # browser and blocks until Google answers, so say so first.
                if question == "config_is_local":
                    self._set(stage="browser", question=question)

                remaining = max(30.0, deadline - time.monotonic())
                result = self.daemon.call(
                    "config/create",
                    dict(
                        base,
                        opt={
                            "nonInteractive": True,
                            "noObscure": True,
                            "continue": True,
                            "state": state,
                            "result": answer,
                        },
                    ),
                    timeout=remaining,
                )
            else:
                raise RuntimeError("rclone asked more questions than expected")

            # An unfinished walk still leaves a remote behind, so success means
            # the remote exists AND carries a token.
            config = self.daemon.call("config/dump").get(name) or {}
            if not config.get("token"):
                raise RuntimeError("the account was not authorised, so nothing was saved")
            if self._cancelled.is_set():
                self._set(stage="cancelled", error="", question="")
                self._cleanup(name)
            else:
                self._set(stage="done", error="", question="")
        except (RcloneError, RuntimeError) as exc:
            self._fail(str(exc))
            self._cleanup(name)
        except Exception as exc:  # never leave the thread silently dead
            self._fail(repr(exc))
            self._cleanup(name)

    # -- giving up ---------------------------------------------------------

    def _cleanup(self, name):
        """Remove the half-written remote rclone creates before the walk ends."""
        for method, params in (
            ("config/oauthstop", {}),
            ("config/delete", {"name": name}),
        ):
            try:
                self.daemon.call(method, params, timeout=10)
            except RcloneError:
                pass

    def cancel(self):
        self._cancelled.set()
        name = self.status().get("name")
        self._set(stage="cancelled", error="", question="")
        if name:
            self._cleanup(name)
        # The walking thread is unblocked by the stop above and will settle on
        # "cancelled" through _fail; give it a moment so the answer is final.
        if self._thread is not None:
            self._thread.join(timeout=5)
        return self.status()
