"""What the API asks rclone to do, given what the UI asked for."""

import pytest
from conftest import FakeDaemon

from driveferry.server import Api, ApiError


def make_api(**responses):
    daemon = FakeDaemon(responses)
    return Api(daemon), daemon


def test_remotes_never_expose_tokens():
    api, _ = make_api()
    remotes = api.remotes()
    assert remotes == [{"name": "alpha", "type": "drive"}, {"name": "beta", "type": "drive"}]
    assert "SECRET" not in repr(remotes)


def test_unknown_remote_is_rejected():
    api, _ = make_api()
    with pytest.raises(ApiError):
        api.op_list({"remote": "not-configured", "path": ""})


def test_list_sorts_folders_first_then_case_insensitively():
    listing = {
        "list": [
            {"Name": "zebra.txt", "IsDir": False, "Size": 10, "ModTime": "2026-01-01T00:00:00Z"},
            {"Name": "Alpha", "IsDir": True, "Size": 0, "ModTime": "2026-01-01T00:00:00Z"},
            {"Name": "apple.txt", "IsDir": False, "Size": 5, "ModTime": "2026-01-01T00:00:00Z"},
            {"Name": "beta", "IsDir": True, "Size": 0, "ModTime": "2026-01-01T00:00:00Z"},
        ]
    }
    api, _ = make_api(**{"operations/list": listing})
    names = [entry["name"] for entry in api.op_list({"remote": "alpha", "path": ""})["entries"]]
    assert names == ["Alpha", "beta", "apple.txt", "zebra.txt"]


@pytest.mark.parametrize("names", [[], ["../etc"], ["a/b"], ["."], [""], ["a\\b"], [None]])
def test_transfer_rejects_bad_selections(names):
    api, _ = make_api()
    with pytest.raises(ApiError):
        api.op_transfer(
            {
                "src_remote": "alpha",
                "src_path": "",
                "dst_remote": "beta",
                "dst_path": "",
                "names": names,
                "dirs": [],
            }
        )


def test_verify_flags_a_size_mismatch():
    sizes = iter([{"count": 3, "bytes": 900}, {"count": 3, "bytes": 100}])
    api, _ = make_api(**{"operations/size": lambda params: next(sizes)})
    result = api.op_verify(
        {
            "src_remote": "alpha",
            "src_path": "",
            "dst_remote": "beta",
            "dst_path": "",
            "names": ["Photos"],
            "dirs": ["Photos"],
        }
    )
    assert result["ok"] is False
    assert result["results"][0]["src"]["bytes"] == 900
    assert result["results"][0]["dst"]["bytes"] == 100


def test_verify_passes_when_counts_and_bytes_match():
    api, _ = make_api(**{"operations/size": {"count": 3, "bytes": 900}})
    result = api.op_verify(
        {
            "src_remote": "alpha",
            "src_path": "",
            "dst_remote": "beta",
            "dst_path": "",
            "names": ["Photos"],
            "dirs": ["Photos"],
        }
    )
    assert result["ok"] is True


def test_verify_reports_a_missing_file_instead_of_claiming_success():
    api, _ = make_api(**{"operations/list": {"list": [{"Name": "a.txt", "IsDir": False, "Size": 12}]}})
    result = api.op_verify(
        {
            "src_remote": "alpha",
            "src_path": "",
            "dst_remote": "beta",
            "dst_path": "",
            "names": ["b.txt"],
            "dirs": [],
        }
    )
    assert result["ok"] is False
    assert result["results"][0]["error"] == "missing at destination"


def test_delete_requires_the_explicit_confirmation():
    api, daemon = make_api()
    with pytest.raises(ApiError):
        api.op_delete({"remote": "alpha", "path": "", "names": ["Photos"], "dirs": ["Photos"]})
    assert not [call for call in daemon.calls if call[0].startswith("operations/purge")]


def test_delete_uses_purge_for_folders_and_deletefile_for_files():
    api, daemon = make_api()
    api.op_delete(
        {
            "remote": "alpha",
            "path": "Photos",
            "names": ["2024", "note.txt"],
            "dirs": ["2024"],
            "confirm": "DELETE",
        }
    )
    used = [(call[0], call[1].get("remote")) for call in daemon.calls if call[0].startswith("operations/")]
    assert ("operations/purge", "Photos/2024") in used
    assert ("operations/deletefile", "Photos/note.txt") in used


def test_mkdir_rejects_a_name_with_a_separator():
    api, _ = make_api()
    for bad in ["a/b", "a\\b", "  ", ""]:
        with pytest.raises(ApiError):
            api.op_mkdir({"remote": "alpha", "path": "", "name": bad})


def test_about_reports_unsupported_instead_of_failing():
    from driveferry.rclone import RcloneError

    def boom(params):
        raise RcloneError("about not supported by this backend")

    api, _ = make_api(**{"operations/about": boom})
    assert api.op_about({"remote": "alpha"})["supported"] is False


def test_prefs_default_to_following_the_system():
    api, _ = make_api()
    assert api.prefs["theme"] == "system"
    assert api.prefs["locale"] == "system"
    assert api.prefs["google_client_id"] == ""


def test_the_client_secret_never_reaches_the_page(tmp_path):
    from driveferry.server import Api

    api = Api(FakeDaemon(), prefs_path=tmp_path / "settings.json")
    api.op_prefs_set({"google_client_id": "abc.apps.googleusercontent.com", "google_client_secret": "s3cr3t"})
    assert "s3cr3t" not in repr(api.public_prefs())
    assert "s3cr3t" not in repr(api.op_prefs_set({"theme": "dark"}))
    assert "s3cr3t" not in repr(api.op_state({}))
    assert api.op_state({})["google_client_id"] == "abc.apps.googleusercontent.com"


def test_a_saved_client_is_reused_by_the_next_account(tmp_path):
    from driveferry.server import Api

    daemon = FakeDaemon({"config/create": {"State": "", "Error": ""}, "config/dump": {"New": {"token": "t"}}})
    api = Api(daemon, prefs_path=tmp_path / "settings.json")
    api.op_prefs_set({"google_client_id": "mine.apps.googleusercontent.com", "google_client_secret": "sh"})
    api.op_account_connect({"name": "New"})
    api.setup._thread.join(timeout=5)
    created = next(call for call in daemon.calls if call[0] == "config/create")
    assert created[1]["parameters"]["client_id"] == "mine.apps.googleusercontent.com"


@pytest.mark.parametrize("bad", ["has space", "quote'", "new\nline", "a" * 300, 5, None])
def test_a_malformed_client_id_is_refused(bad, tmp_path):
    from driveferry.server import Api

    api = Api(FakeDaemon(), prefs_path=tmp_path / "settings.json")
    with pytest.raises(ApiError):
        api.op_prefs_set({"google_client_id": bad})


def test_prefs_are_written_and_read_back_by_a_fresh_api(tmp_path):
    from driveferry.server import Api

    path = tmp_path / "settings.json"
    first = Api(FakeDaemon(), prefs_path=path)
    first.op_prefs_set({"theme": "dark", "locale": "it"})

    second = Api(FakeDaemon(), prefs_path=path)
    assert second.prefs["theme"] == "dark"
    assert second.prefs["locale"] == "it"


@pytest.mark.parametrize(
    "payload",
    [{"theme": "neon"}, {"locale": "xx"}, {"theme": ""}, {"locale": None}, {"theme": 1}],
)
def test_prefs_reject_values_outside_the_whitelist(payload, tmp_path):
    from driveferry.server import Api

    api = Api(FakeDaemon(), prefs_path=tmp_path / "settings.json")
    with pytest.raises(ApiError):
        api.op_prefs_set(payload)
    assert not (tmp_path / "settings.json").exists()


def test_prefs_ignore_unknown_keys(tmp_path):
    from driveferry.server import Api

    api = Api(FakeDaemon(), prefs_path=tmp_path / "settings.json")
    api.op_prefs_set({"theme": "light", "rclone_binary": "/tmp/evil"})
    assert api.prefs["theme"] == "light"
    assert "rclone_binary" not in api.prefs


def test_a_corrupt_settings_file_falls_back_to_defaults(tmp_path):
    from driveferry.server import Api

    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    prefs = Api(FakeDaemon(), prefs_path=path).prefs
    assert prefs["theme"] == "system" and prefs["locale"] == "system"
