"""Path handling is the boundary where a malformed name becomes an rclone
argument, so every rejection here is a real guard, not a formality."""

import pytest

from driveferry.server import ApiError, _clean_path, _join


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, ""),
        ("", ""),
        ("/", ""),
        ("Photos", "Photos"),
        ("/Photos/", "Photos"),
        ("Photos//2024", "Photos/2024"),
        ("Photos\\2024", "Photos/2024"),
        ("./Photos", "Photos"),
        ("Photos/./2024", "Photos/2024"),
    ],
)
def test_clean_path_normalises(raw, expected):
    assert _clean_path(raw) == expected


@pytest.mark.parametrize("raw", ["../etc", "Photos/../../etc", "a/b/..", ".."])
def test_clean_path_rejects_traversal(raw):
    with pytest.raises(ApiError):
        _clean_path(raw)


def test_clean_path_rejects_null_byte():
    with pytest.raises(ApiError):
        _clean_path("Photos\x00evil")


def test_clean_path_rejects_non_string():
    with pytest.raises(ApiError):
        _clean_path(42)


def test_clean_path_keeps_unicode_and_spaces():
    assert _clean_path("/Foto di Nonna/estate 2024/") == "Foto di Nonna/estate 2024"


def test_join():
    assert _join("", "a") == "a"
    assert _join("a", "b") == "a/b"
