"""Shared helper tests: periodic XOR and atomic file writes."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fne import util  # noqa: E402


def naive_xor(data: bytes, pattern: bytes) -> bytes:
    return bytes(b ^ pattern[i % len(pattern)] for i, b in enumerate(data))


@pytest.mark.parametrize("pattern_len", [1, 16, 256, 4096])
def test_xor_repeating_matches_naive(pattern_len):
    pattern = bytes((i * 7 + 3) & 0xFF for i in range(pattern_len))
    data = bytes((i * 13 + 5) & 0xFF for i in range(5000))
    assert util.xor_repeating(data, pattern) == naive_xor(data, pattern)


def test_xor_repeating_crosses_chunk_boundaries(monkeypatch):
    """The fast path works in chunks; the seams must not drift."""
    monkeypatch.setattr(util, "XOR_CHUNK", 1024)
    pattern = bytes((i * 11 + 1) & 0xFF for i in range(64))
    data = bytes((i * 29 + 7) & 0xFF for i in range(3000))   # ~3 chunks
    assert util.xor_repeating(data, pattern) == naive_xor(data, pattern)


def test_xor_repeating_handles_empty_input():
    assert util.xor_repeating(b"", b"abc") == b""


def test_xor_repeating_rejects_empty_pattern():
    with pytest.raises(ValueError):
        util.xor_repeating(b"abc", b"")


def test_write_atomic_creates_missing_directories(tmp_path):
    target = tmp_path / "nested" / "song.flac"
    util.write_atomic(str(target), b"payload")
    assert target.read_bytes() == b"payload"


def test_write_atomic_overwrites_an_existing_file(tmp_path):
    target = tmp_path / "song.flac"
    target.write_bytes(b"old")
    util.write_atomic(str(target), b"new")
    assert target.read_bytes() == b"new"


def test_write_atomic_leaves_no_debris_on_failure(tmp_path, monkeypatch):
    """A failed write must not leave a partial file behind to be skipped."""
    target = tmp_path / "song.flac"

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        util.write_atomic(str(target), b"payload")

    assert not target.exists()
    assert os.listdir(tmp_path) == []      # the temp file was cleaned up
