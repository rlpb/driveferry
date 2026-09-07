"""End-to-end against a real rclone process.

Two local folders stand in for two Drive accounts. Everything else is the
production path: the real daemon, the real rc API, the real HTTP server, the
same JSON the window sends. Skipped when rclone is not installed.
"""

import json
import time
import urllib.error
import urllib.request

import pytest

from driveferry.rclone import RcloneDaemon, find_rclone
from driveferry.server import Api, DriveFerryServer

pytestmark = pytest.mark.skipif(find_rclone() is None, reason="rclone is not installed")

TREE = {
    "report.txt": "quarterly report\n" * 40,
    "Photos/summer.jpg": "not really a jpeg" * 300,
    "Photos/2024/party.png": "still not an image" * 120,
    "Photos/2024/notes.md": "# notes\n",
}


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    root = tmp_path_factory.mktemp("driveferry-e2e")
    source = root / "account-a"
    destination = root / "account-b"
    for relative, content in TREE.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    destination.mkdir()

    config = root / "rclone.conf"
    config.write_text(
        "[accountA]\ntype = alias\nremote = {}\n\n[accountB]\ntype = alias\nremote = {}\n".format(
            source, destination
        ),
        encoding="utf-8",
    )

    daemon = RcloneDaemon(config_path=config)
    daemon.start()
    server = DriveFerryServer(Api(daemon), port=0)
    server.serve_in_background()
    try:
        yield {"daemon": daemon, "server": server, "src": source, "dst": destination}
    finally:
        server.shutdown()
        server.server_close()
        daemon.stop()


def call(live, operation, payload=None):
    request = urllib.request.Request(
        live["server"].url.rstrip("/") + "/api/" + operation,
        data=json.dumps(payload or {}).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "X-DriveFerry-Token": live["server"].token},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def wait_for(live, started, timeout=60):
    """The run reports one stage; anything settled ends the wait."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = call(live, "transfer_status")
        if status["stage"] in ("done", "failed", "cancelled"):
            return status
        time.sleep(0.2)
    raise AssertionError("transfer did not finish within {}s".format(timeout))


def failures(status):
    return [item for item in status["items"] if item["state"] == "failed"]


def test_the_required_rc_methods_exist_in_this_rclone(live):
    assert live["daemon"].check_supported() == []


def test_optional_methods_are_not_treated_as_required(live):
    """`config/oauthstop` is missing from older distribution packages of rclone.

    It only makes cancelling an unfinished sign-in tidier, so it must never be
    the reason the app refuses to start.
    """
    from driveferry.rclone import OPTIONAL_RC_METHODS, REQUIRED_RC_METHODS

    assert set(OPTIONAL_RC_METHODS).isdisjoint(REQUIRED_RC_METHODS)


def test_state_lists_both_accounts(live):
    state = call(live, "state")
    assert {remote["name"] for remote in state["remotes"]} == {"accountA", "accountB"}
    assert state["rclone_version"].startswith("v")


def test_listing_is_folders_first(live):
    entries = call(live, "list", {"remote": "accountA", "path": ""})["entries"]
    assert [entry["name"] for entry in entries] == ["Photos", "report.txt"]
    assert entries[0]["is_dir"] is True
    # on-disk size, not len() of the string: Windows writes \r\n for every \n
    assert entries[1]["size"] == (live["src"] / "report.txt").stat().st_size


def test_dry_run_writes_nothing(live):
    started = call(
        live,
        "transfer",
        {
            "src_remote": "accountA",
            "src_path": "",
            "dst_remote": "accountB",
            "dst_path": "dry",
            "names": ["Photos"],
            "dirs": ["Photos"],
            "dry_run": True,
        },
    )
    status = wait_for(live, started)
    assert failures(status) == []
    assert not (live["dst"] / "dry").exists()


def test_copy_then_verify_then_delete(live):
    started = call(
        live,
        "transfer",
        {
            "src_remote": "accountA",
            "src_path": "",
            "dst_remote": "accountB",
            "dst_path": "",
            "names": ["Photos", "report.txt"],
            "dirs": ["Photos"],
        },
    )
    status = wait_for(live, started)
    assert failures(status) == []

    # every file landed, with its nesting intact
    assert (live["dst"] / "Photos" / "2024" / "party.png").is_file()
    assert (live["dst"] / "report.txt").read_text(encoding="utf-8") == TREE["report.txt"]

    verified = call(
        live,
        "verify",
        {
            "src_remote": "accountA",
            "src_path": "",
            "dst_remote": "accountB",
            "dst_path": "",
            "names": ["Photos", "report.txt"],
            "dirs": ["Photos"],
        },
    )
    assert verified["ok"] is True
    folder = next(item for item in verified["results"] if item["name"] == "Photos")
    assert folder["src"]["count"] == folder["dst"]["count"] == 3

    # the source is still intact: copy never removes anything
    assert (live["src"] / "Photos" / "2024" / "party.png").is_file()

    deleted = call(
        live,
        "delete",
        {
            "remote": "accountA",
            "path": "",
            "names": ["report.txt"],
            "dirs": [],
            "confirm": "DELETE",
        },
    )
    assert deleted["failed"] == []
    assert not (live["src"] / "report.txt").exists()
    assert (live["src"] / "Photos").is_dir()


def test_verify_catches_a_partial_copy(live):
    (live["src"] / "Partial").mkdir()
    (live["src"] / "Partial" / "one.txt").write_text("one", encoding="utf-8")
    (live["src"] / "Partial" / "two.txt").write_text("two", encoding="utf-8")
    (live["dst"] / "Partial").mkdir()
    (live["dst"] / "Partial" / "one.txt").write_text("one", encoding="utf-8")

    verified = call(
        live,
        "verify",
        {
            "src_remote": "accountA",
            "src_path": "",
            "dst_remote": "accountB",
            "dst_path": "",
            "names": ["Partial"],
            "dirs": ["Partial"],
        },
    )
    assert verified["ok"] is False
    assert verified["results"][0]["src"]["count"] == 2
    assert verified["results"][0]["dst"]["count"] == 1


def test_mkdir_creates_the_folder(live):
    call(live, "mkdir", {"remote": "accountB", "path": "", "name": "New Folder"})
    assert (live["dst"] / "New Folder").is_dir()


def test_a_traversal_attempt_is_refused_by_the_live_server(live):
    with pytest.raises(urllib.error.HTTPError) as exc:
        call(live, "list", {"remote": "accountA", "path": "../.."})
    assert exc.value.code == 400
