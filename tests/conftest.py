import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class FakeDaemon:
    """Records rc calls and answers them from a scripted table."""

    binary = "/fake/rclone"

    def __init__(self, responses=None):
        self.calls = []
        self.responses = {
            "core/version": {"version": "v1.75.1"},
            "config/listremotes": {"remotes": ["alpha", "beta"]},
            "config/dump": {
                "alpha": {"type": "drive", "token": "SECRET"},
                "beta": {"type": "drive", "token": "SECRET"},
            },
        }
        self.responses.update(responses or {})
        self.job_counter = 0

    def call(self, method, params=None, timeout=None):
        self.calls.append((method, params or {}))
        value = self.responses.get(method)
        if callable(value):
            return value(params or {})
        if value is not None:
            return value
        if method in ("sync/copy", "operations/copyfile"):
            self.job_counter += 1
            return {"jobid": self.job_counter}
        return {}

    def version(self):
        return self.responses["core/version"]
