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


def test_transfer_uses_sync_copy_for_folders_and_copyfile_for_files():
    api, daemon = make_api()
    result = api.op_transfer(
        {
            "src_remote": "alpha",
            "src_path": "Photos",
            "dst_remote": "beta",
            "dst_path": "Backup",
            "names": ["2024", "note.txt"],
            "dirs": ["2024"],
        }
    )
    transfers = [call for call in daemon.calls if call[0] in ("sync/copy", "operations/copyfile")]
    assert [call[0] for call in transfers] == ["sync/copy", "operations/copyfile"]

    folder = transfers[0][1]
    assert folder["srcFs"] == "alpha:Photos/2024"
    assert folder["dstFs"] == "beta:Backup/2024"

    single = transfers[1][1]
    assert single["srcFs"] == "alpha:Photos"
    assert single["srcRemote"] == "note.txt"
    assert single["dstFs"] == "beta:Backup"
    assert single["dstRemote"] == "note.txt"

    assert all(call[1]["_async"] is True for call in transfers)
    assert len({call[1]["_group"] for call in transfers}) == 1
    assert result["group"].startswith("df-")
    assert [job["jobid"] for job in result["jobs"]] == [1, 2]


def test_transfer_passes_dry_run_and_server_side_flags():
    api, daemon = make_api()
    api.op_transfer(
        {
            "src_remote": "alpha",
            "src_path": "",
            "dst_remote": "beta",
            "dst_path": "",
            "names": ["a.txt"],
            "dirs": [],
            "dry_run": True,
            "server_side": True,
        }
    )
    options = daemon.calls[-1][1]["_config"]
    # Key names verified against `options/get` on rclone v1.75.1.
    assert options == {"DryRun": True, "ServerSideAcrossConfigs": True}


def test_transfer_defaults_to_writing_for_real_but_without_server_side():
    api, daemon = make_api()
    api.op_transfer(
        {
            "src_remote": "alpha",
            "src_path": "",
            "dst_remote": "beta",
            "dst_path": "",
            "names": ["a.txt"],
            "dirs": [],
        }
    )
    assert daemon.calls[-1][1]["_config"] == {"DryRun": False}


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


def test_transfer_status_rejects_a_foreign_group():
    api, _ = make_api()
    with pytest.raises(ApiError):
        api.op_transfer_status({"group": "not-ours", "jobs": [1]})
