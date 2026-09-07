"""The transfer run: measure first, then one item at a time."""

import threading
import time

import pytest

from driveferry.rclone import RcloneError
from driveferry.transfers import TransferRun


class TransferDaemon:
    """An rclone that reports sizes, hands out jobs, and finishes them."""

    binary = "/fake/rclone"

    def __init__(self, sizes=None, outcomes=None, listing=None):
        self.calls = []
        self.sizes = sizes or {}
        self.outcomes = outcomes or {}
        self.listing = listing or []
        self.jobs = {}
        self.next_job = 0
        self.live = []  # jobs started but not yet reported finished
        self.max_live = 0
        self.lock = threading.Lock()

    def call(self, method, params=None, timeout=None):
        params = params or {}
        self.calls.append((method, params))

        if method == "operations/size":
            name = params["fs"].rsplit("/", 1)[-1]
            return self.sizes.get(name, {"bytes": 0, "count": 0})

        if method == "operations/list":
            return {"list": self.listing}

        if method in ("sync/copy", "operations/copyfile"):
            self.next_job += 1
            name = params.get("srcRemote") or params["srcFs"].rsplit("/", 1)[-1]
            ok, error = self.outcomes.get(name, (True, ""))
            with self.lock:
                self.live.append(self.next_job)
                self.max_live = max(self.max_live, len(self.live))
            self.jobs[self.next_job] = {"name": name, "polls": 0, "ok": ok, "error": error}
            return {"jobid": self.next_job}

        if method == "job/status":
            job = self.jobs[params["jobid"]]
            job["polls"] += 1
            if job["error"] == "vanish":
                raise RcloneError("job not found")
            if job["polls"] < 2:
                return {"finished": False}
            with self.lock:
                if params["jobid"] in self.live:
                    self.live.remove(params["jobid"])
            return {"finished": True, "success": job["ok"], "error": job["error"]}

        if method == "job/stop":
            with self.lock:
                if params["jobid"] in self.live:
                    self.live.remove(params["jobid"])
            return {}

        if method == "core/stats":
            return {"bytes": 1234, "speed": 100.0, "errors": 0}

        return {}


def plan(names, dirs=None, **extra):
    body = {
        "src_fs": "alpha:Photos",
        "dst_fs": "beta:Backup",
        "names": names,
        "dirs": set(dirs if dirs is not None else names),
        "dry_run": False,
        "server_side": False,
        "options": {"DryRun": False},
    }
    body.update(extra)
    return body


def settle(run, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        stage = run.status()["stage"]
        if stage in ("done", "failed", "cancelled"):
            return run.status()
        time.sleep(0.02)
    raise AssertionError("the run never settled; last status {}".format(run.status()))


def test_the_whole_job_is_measured():
    daemon = TransferDaemon(sizes={"A": {"bytes": 100, "count": 2}, "B": {"bytes": 400, "count": 3}})
    run = TransferRun(daemon)
    run.start(plan(["A", "B"]))
    status = settle(run)
    assert status["total_bytes"] == 500
    assert status["total_files"] == 5


def test_measuring_does_not_delay_the_first_copy():
    """Scanning a real Drive folder is slow; nothing should wait for it."""

    class SlowSize(TransferDaemon):
        def call(self, method, params=None, timeout=None):
            if method == "operations/size":
                time.sleep(0.6)
            return super().call(method, params, timeout)

    daemon = SlowSize(sizes={"A": {"bytes": 100, "count": 1}, "B": {"bytes": 400, "count": 1}})
    run = TransferRun(daemon)
    started = time.monotonic()
    run.start(plan(["A", "B"]))
    while not [m for m, _ in daemon.calls if m == "sync/copy"]:
        time.sleep(0.01)
        assert time.monotonic() - started < 0.5, "the copy waited for the scan"
    settle(run)


def test_the_total_stops_changing_once_copying_starts():
    """The whole point: a denominator that cannot move under the progress bar."""
    daemon = TransferDaemon(sizes={"A": {"bytes": 100, "count": 1}, "B": {"bytes": 400, "count": 1}})
    run = TransferRun(daemon)
    run.start(plan(["A", "B"]))
    totals = set()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = run.status()
        if status["stage"] in ("copying", "done"):
            totals.add(status["total_bytes"])
        if status["stage"] in ("done", "failed", "cancelled"):
            break
        time.sleep(0.02)
    assert totals == {500}


def test_items_are_transferred_one_at_a_time():
    """Five jobs at once means twenty parallel uploads and a throttled Drive."""
    daemon = TransferDaemon(sizes={n: {"bytes": 10, "count": 1} for n in "ABCDE"})
    run = TransferRun(daemon)
    run.start(plan(list("ABCDE")))
    settle(run)
    assert daemon.max_live == 1
    copied = [p["srcFs"].rsplit("/", 1)[-1] for m, p in daemon.calls if m == "sync/copy"]
    assert copied == list("ABCDE")


def test_a_single_file_uses_copyfile_and_its_size_comes_from_the_listing():
    daemon = TransferDaemon(listing=[{"Name": "note.txt", "IsDir": False, "Size": 42}])
    run = TransferRun(daemon)
    run.start(plan(["note.txt"], dirs=[]))
    status = settle(run)
    assert status["total_bytes"] == 42
    assert status["total_files"] == 1
    assert [m for m, _ in daemon.calls if m in ("sync/copy", "operations/copyfile")] == [
        "operations/copyfile"
    ]


def test_a_failed_item_is_reported_without_stopping_the_rest():
    daemon = TransferDaemon(
        sizes={n: {"bytes": 10, "count": 1} for n in "ABC"},
        outcomes={"B": (False, "quota exceeded")},
    )
    run = TransferRun(daemon)
    run.start(plan(list("ABC")))
    status = settle(run)
    states = {item["name"]: item["state"] for item in status["items"]}
    assert states == {"A": "done", "B": "failed", "C": "done"}
    assert status["stage"] == "failed"
    assert "quota exceeded" in [item["error"] for item in status["items"]][1]


def test_a_job_that_rclone_has_forgotten_counts_as_finished():
    """rclone drops finished jobs from its list; that is not a lost transfer."""
    daemon = TransferDaemon(sizes={"A": {"bytes": 10, "count": 1}}, outcomes={"A": (True, "vanish")})
    run = TransferRun(daemon)
    run.start(plan(["A"]))
    status = settle(run)
    assert status["stage"] == "done"
    assert status["items"][0]["state"] == "done"


def test_cancelling_stops_the_running_job_and_reports_a_cancellation():
    daemon = TransferDaemon(sizes={n: {"bytes": 10, "count": 1} for n in "ABCDE"})
    run = TransferRun(daemon)
    run.start(plan(list("ABCDE")))
    while run.status()["stage"] != "copying":
        time.sleep(0.01)
    run.cancel()
    status = settle(run)
    assert status["stage"] == "cancelled"
    assert ("job/stop", {"jobid": 1}) in daemon.calls
    # the queue stops where it was: later items were never started
    assert len([m for m, _ in daemon.calls if m == "sync/copy"]) < 5


def test_the_options_reach_rclone():
    daemon = TransferDaemon(sizes={"A": {"bytes": 1, "count": 1}})
    run = TransferRun(daemon)
    run.start(plan(["A"], options={"DryRun": True, "ServerSideAcrossConfigs": True}))
    settle(run)
    copy = next(p for m, p in daemon.calls if m == "sync/copy")
    assert copy["_config"] == {"DryRun": True, "ServerSideAcrossConfigs": True}
    assert copy["_async"] is True
    assert copy["_group"].startswith("df-")


def test_two_runs_at_once_are_refused():
    daemon = TransferDaemon(sizes={n: {"bytes": 10, "count": 1} for n in "ABCDE"})
    run = TransferRun(daemon)
    run.start(plan(list("ABCDE")))
    with pytest.raises(ValueError):
        run.start(plan(["A"]))
    run.cancel()


def test_a_failing_scan_does_not_fail_the_transfer():
    """The total is a nicety; losing it must not lose the copy."""

    class NoSize(TransferDaemon):
        def call(self, method, params=None, timeout=None):
            if method == "operations/size":
                raise RcloneError("directory not found")
            return super().call(method, params, timeout)

    daemon = NoSize(sizes={"A": {"bytes": 10, "count": 1}})
    run = TransferRun(daemon)
    run.start(plan(["A"]))
    status = settle(run)
    assert status["stage"] == "done"
    assert status["total_bytes"] == 0
