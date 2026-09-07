"""Run one transfer: size the job while it moves, one item at a time.

The obvious way to copy several items is to start one rclone job per item and
watch the combined statistics. It reads well and behaves badly:

* each job discovers its own tree while it runs, so the total to measure
  progress against keeps growing. A bar drawn from bytes over total lurches,
  and clamping it to only move forward just makes it disagree with the numbers
  printed beside it.
* five jobs with four transfers each means twenty parallel uploads. Google
  throttles, the speed swings, and cancelling has twenty things to stop.

So the whole job is sized in the background, which gives a denominator that
stops moving once it lands, and the items are transferred in order. The scan
runs alongside the first copy rather than before it: listing a real Drive
folder is slow, and nothing should be waiting on it.
"""

from __future__ import annotations

import secrets
import threading
import time

from .rclone import RcloneError

POLL_SECONDS = 0.4


class TransferRun:
    """One transfer at a time, driven on a background thread."""

    def __init__(self, daemon):
        self.daemon = daemon
        self._lock = threading.Lock()
        self._thread = None
        self._cancelled = threading.Event()
        self._state = self._idle()

    @staticmethod
    def _idle():
        return {
            "stage": "idle",
            "group": "",
            "total_bytes": 0,
            "total_files": 0,
            "measured": 0,
            "current": "",
            "items": [],
            "error": "",
            "dry_run": False,
            "server_side": False,
        }

    # -- reporting ---------------------------------------------------------

    def _set(self, **fields):
        with self._lock:
            self._state.update(fields)

    def _set_item(self, name, **fields):
        with self._lock:
            for item in self._state["items"]:
                if item["name"] == name:
                    item.update(fields)
                    break

    def status(self):
        with self._lock:
            state = dict(self._state)
            state["items"] = [dict(item) for item in self._state["items"]]
        state["stats"] = self._stats(state["group"]) if state["group"] else {}
        return state

    def _stats(self, group):
        try:
            stats = self.daemon.call("core/stats", {"group": group}, timeout=15)
        except RcloneError:
            return {}
        return {
            "bytes": stats.get("bytes", 0),
            "speed": stats.get("speed", 0),
            "errors": stats.get("errors", 0),
            "transferring": [
                {
                    "name": item.get("name"),
                    "bytes": item.get("bytes", 0),
                    "size": item.get("size", 0),
                }
                for item in (stats.get("transferring") or [])
            ],
        }

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    # -- starting ----------------------------------------------------------

    def start(self, plan):
        if self.running:
            raise ValueError("a transfer is already running")
        self._cancelled.clear()
        group = "df-" + secrets.token_hex(6)
        with self._lock:
            self._state = self._idle()
            self._state.update(
                stage="measuring",
                group=group,
                dry_run=bool(plan["dry_run"]),
                server_side=bool(plan["server_side"]),
                items=[{"name": name, "state": "waiting", "error": "", "bytes": 0} for name in plan["names"]],
            )
        self._thread = threading.Thread(
            target=self._run, args=(plan, group), name="driveferry-transfer", daemon=True
        )
        self._thread.start()
        return self.status()

    # -- the run -----------------------------------------------------------

    def _measure(self, plan):
        """Size every selected item before moving anything.

        This is what gives the progress bar a denominator that does not move.
        """
        total_bytes = 0
        total_files = 0
        file_sizes = {}
        if any(name not in plan["dirs"] for name in plan["names"]):
            listing = self.daemon.call("operations/list", {"fs": plan["src_fs"], "remote": ""}, timeout=120)
            for entry in listing.get("list") or []:
                if not entry.get("IsDir"):
                    file_sizes[entry.get("Name")] = entry.get("Size", 0)

        for name in plan["names"]:
            if self._cancelled.is_set():
                return 0, 0
            if name in plan["dirs"]:
                size = self.daemon.call(
                    "operations/size", {"fs": plan["src_fs"].rstrip("/") + "/" + name}, timeout=600
                )
                item_bytes = size.get("bytes", 0) or 0
                item_files = size.get("count", 0) or 0
            else:
                item_bytes = file_sizes.get(name, 0) or 0
                item_files = 1
            total_bytes += item_bytes
            total_files += item_files
            self._set_item(name, bytes=item_bytes)
            self._set(measured=total_bytes, total_bytes=total_bytes, total_files=total_files)
        return total_bytes, total_files

    def _measure_quietly(self, plan):
        """The scan must never be the reason a transfer reports a failure."""
        try:
            self._measure(plan)
        except Exception:
            pass  # a missing total costs a progress bar, never a transfer

    def _copy_one(self, plan, group, name):
        is_dir = name in plan["dirs"]
        if is_dir:
            method = "sync/copy"
            request = {
                "srcFs": plan["src_fs"].rstrip("/") + "/" + name,
                "dstFs": plan["dst_fs"].rstrip("/") + "/" + name,
            }
        else:
            method = "operations/copyfile"
            request = {
                "srcFs": plan["src_fs"],
                "srcRemote": name,
                "dstFs": plan["dst_fs"],
                "dstRemote": name,
            }
        request.update({"_async": True, "_group": group, "_config": plan["options"]})
        job = self.daemon.call(method, request, timeout=60)
        jobid = job["jobid"]

        while True:
            if self._cancelled.is_set():
                try:
                    self.daemon.call("job/stop", {"jobid": jobid}, timeout=15)
                except RcloneError:
                    pass
                return False, "cancelled"
            try:
                status = self.daemon.call("job/status", {"jobid": jobid}, timeout=30)
            except RcloneError as exc:
                # rclone forgets finished jobs after a while. Treat a job that
                # has gone missing as finished rather than as a failure: the
                # daemon is started with a long expiry, so this is the tail of
                # a race, not a lost transfer.
                if "not found" in str(exc).lower():
                    return True, ""
                raise
            if status.get("finished"):
                return bool(status.get("success")), status.get("error") or ""
            time.sleep(POLL_SECONDS)

    def _run(self, plan, group):
        try:
            # Measuring a real Drive folder means listing it, which takes long
            # enough to feel like the app is stuck if nothing moves meanwhile.
            # So the scan runs alongside the first copy: the bar is
            # indeterminate until the total lands, and fixed from then on.
            measurer = threading.Thread(
                target=self._measure_quietly, args=(plan,), name="driveferry-measure", daemon=True
            )
            measurer.start()

            self._set(stage="copying")
            failed = False
            for name in plan["names"]:
                if self._cancelled.is_set():
                    break
                self._set(current=name)
                self._set_item(name, state="running")
                ok, error = self._copy_one(plan, group, name)
                if error == "cancelled":
                    self._set_item(name, state="cancelled")
                    break
                self._set_item(name, state="done" if ok else "failed", error=error)
                if not ok:
                    failed = True

            if self._cancelled.is_set():
                self._set(stage="cancelled", current="")
            else:
                self._set(stage="failed" if failed else "done", current="")
        except RcloneError as exc:
            self._set(stage="failed", error=str(exc), current="")
        except Exception as exc:  # never leave the thread silently dead
            self._set(stage="failed", error=repr(exc), current="")

    # -- stopping ----------------------------------------------------------

    def cancel(self):
        """Ask the run to stop. Returns at once; the thread settles on its own."""
        self._cancelled.set()
        self._set(stage="cancelling")
        return self.status()
