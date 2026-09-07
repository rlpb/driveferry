"""Connecting a Google account: what the wizard answers, and what it refuses."""

import time

import pytest
from conftest import FakeDaemon

from driveferry.accounts import AccountSetup
from driveferry.rclone import RcloneError
from driveferry.server import Api, ApiError

CLIENT_ID_STATE = {
    "State": "client_id_warning",
    "Option": {"Name": "config_shared_client_id", "Type": "bool"},
    "Error": "",
}
IS_LOCAL_STATE = {
    "State": "*oauth-islocal,teamdrive,oauth,",
    "Option": {"Name": "config_is_local", "Type": "bool"},
    "Error": "",
}
FINISHED = {"State": "", "Option": None, "Error": ""}


def scripted(steps):
    """Return a config/create handler that walks the given states in order."""
    remaining = list(steps)
    seen = []

    def handler(params):
        seen.append(params)
        return remaining.pop(0) if remaining else FINISHED

    handler.seen = seen
    return handler


def settle(setup, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        stage = setup.status()["stage"]
        if stage not in ("starting", "browser"):
            return setup.status()
        time.sleep(0.02)
    raise AssertionError("the wizard never finished; last status {}".format(setup.status()))


@pytest.mark.parametrize(
    "name",
    ["", " ", "-nope", "a" * 40, "we/ird", "back\\slash", "new\nline", "quote'd"],
)
def test_a_bad_account_name_is_refused_before_anything_runs(name):
    daemon = FakeDaemon()
    setup = AccountSetup(daemon)
    with pytest.raises(ValueError):
        setup.start(name, client_id="id", client_secret="secret")
    assert not [call for call in daemon.calls if call[0] == "config/create"]


@pytest.mark.parametrize("name", ["Personal", "Work 2", "old-account", "a", "A_b-c 1"])
def test_reasonable_names_are_accepted(name):
    daemon = FakeDaemon({"config/create": scripted([FINISHED]), "config/dump": {name: {"token": "x"}}})
    setup = AccountSetup(daemon)
    setup.start(name, client_id="id", client_secret="secret")
    assert settle(setup)["stage"] == "done"


def test_an_existing_name_is_refused():
    setup = AccountSetup(FakeDaemon())
    with pytest.raises(ValueError):
        setup.start("alpha", client_id="id", client_secret="secret")


def test_the_shared_client_needs_an_explicit_choice():
    setup = AccountSetup(FakeDaemon())
    with pytest.raises(ValueError):
        setup.start("New", client_id="", client_secret="")


def test_a_client_id_with_a_newline_is_refused():
    setup = AccountSetup(FakeDaemon())
    with pytest.raises(ValueError):
        setup.start("New", client_id="abc\ndef", client_secret="s")


def test_the_walk_answers_both_questions_and_reports_done():
    handler = scripted([CLIENT_ID_STATE, IS_LOCAL_STATE, FINISHED])
    daemon = FakeDaemon({"config/create": handler, "config/dump": {"New": {"token": "{...}"}}})
    setup = AccountSetup(daemon)
    setup.start("New", allow_shared_client=True)
    assert settle(setup)["stage"] == "done"

    answers = [call["opt"].get("result") for call in handler.seen if call["opt"].get("continue")]
    assert answers == ["true", "true"]  # take the shared client, and use this browser
    assert all(call["opt"]["nonInteractive"] for call in handler.seen)
    assert handler.seen[0]["type"] == "drive"


def test_an_own_client_id_is_passed_to_rclone_and_never_echoed_back():
    handler = scripted([IS_LOCAL_STATE, FINISHED])
    daemon = FakeDaemon({"config/create": handler, "config/dump": {"New": {"token": "{...}"}}})
    setup = AccountSetup(daemon)
    setup.start("New", client_id="my-id.apps.googleusercontent.com", client_secret="sh h")
    status = settle(setup)
    assert status["stage"] == "done"
    assert handler.seen[0]["parameters"]["client_id"] == "my-id.apps.googleusercontent.com"
    assert handler.seen[0]["parameters"]["client_secret"] == "sh h"
    assert "sh h" not in repr(status)


def test_a_walk_that_never_gets_a_token_fails_and_removes_the_half_made_remote():
    daemon = FakeDaemon({"config/create": scripted([FINISHED]), "config/dump": {"New": {"type": "drive"}}})
    setup = AccountSetup(daemon)
    setup.start("New", allow_shared_client=True)
    status = settle(setup)
    assert status["stage"] == "error"
    assert "not authorised" in status["error"]
    assert ("config/delete", {"name": "New"}) in daemon.calls


def test_an_unexpected_question_stops_instead_of_guessing():
    surprise = {
        "State": "something_new",
        "Option": {"Name": "config_service_account", "Type": "string"},
        "Error": "",
    }
    daemon = FakeDaemon({"config/create": scripted([surprise])})
    setup = AccountSetup(daemon)
    setup.start("New", allow_shared_client=True)
    status = settle(setup)
    assert status["stage"] == "error"
    assert "config_service_account" in status["error"]
    assert ("config/oauthstop", {}) in daemon.calls


def test_rclone_reporting_an_error_is_surfaced():
    daemon = FakeDaemon({"config/create": {"Error": "couldn't fetch token", "State": ""}})
    setup = AccountSetup(daemon)
    setup.start("New", allow_shared_client=True)
    status = settle(setup)
    assert status["stage"] == "error"
    assert "couldn't fetch token" in status["error"]


def test_two_connections_at_once_are_refused():
    slow = {"State": "*oauth-islocal,teamdrive,oauth,", "Option": {"Name": "config_is_local"}, "Error": ""}

    def handler(params):
        time.sleep(0.4)
        return slow

    setup = AccountSetup(FakeDaemon({"config/create": handler}))
    setup.start("First", allow_shared_client=True)
    with pytest.raises(ValueError):
        setup.start("Second", allow_shared_client=True)


def test_cancelling_reports_a_cancellation_not_an_error():
    """rclone raises when its OAuth server is stopped; that is not a failure."""

    def handler(params):
        if params["opt"].get("continue"):
            time.sleep(0.3)
            raise RcloneError("config failed to refresh token: oauth authentication was cancelled")
        return IS_LOCAL_STATE

    setup = AccountSetup(FakeDaemon({"config/create": handler}))
    setup.start("New", allow_shared_client=True)
    while setup.status()["stage"] != "browser":
        time.sleep(0.02)
    status = setup.cancel()
    assert status["stage"] == "cancelled"
    assert status["error"] == ""
    assert settle(setup)["stage"] == "cancelled"


def test_forgetting_an_account_needs_the_confirmation():
    daemon = FakeDaemon()
    api = Api(daemon)
    with pytest.raises(ApiError):
        api.op_account_forget({"name": "alpha"})
    assert not [call for call in daemon.calls if call[0] == "config/delete"]


def test_forgetting_an_unknown_account_is_refused():
    api = Api(FakeDaemon())
    with pytest.raises(ApiError):
        api.op_account_forget({"name": "nope", "confirm": "FORGET"})


def test_forgetting_removes_the_remote():
    daemon = FakeDaemon()
    api = Api(daemon)
    assert api.op_account_forget({"name": "alpha", "confirm": "FORGET"}) == {"forgotten": "alpha"}
    assert ("config/delete", {"name": "alpha"}) in daemon.calls
