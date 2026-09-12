"""Shared helper tests: periodic XOR and atomic file writes."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fne import util  # noqa: E402


class _NarrowStream:
    """A stream that behaves like a Windows pipe on an English install.

    Development interpreters run in UTF-8 mode, so they cannot reproduce what
    the frozen build does; this stands in for the ANSI code page it falls back
    to, where writing Chinese raises UnicodeEncodeError.
    """

    def __init__(self):
        self.encoding = "cp1252"
        self.errors = "strict"
        self.written = ""

    def reconfigure(self, encoding=None, errors=None):
        self.encoding = encoding
        self.errors = errors

    def write(self, text):
        self.written += text.encode(self.encoding, self.errors).decode(self.encoding)


def test_force_utf8_streams_rescues_a_narrow_stream(monkeypatch):
    stream = _NarrowStream()
    monkeypatch.setattr(util.sys, "stdout", stream)

    with pytest.raises(UnicodeEncodeError):
        stream.write("选择输入文件夹")             # the pre-fix behaviour

    util.force_utf8_streams()
    stream.write("选择输入文件夹")                # must no longer raise

    assert stream.encoding == "utf-8"
    assert "选择输入文件夹" in stream.written


def test_force_utf8_streams_tolerates_a_stream_it_cannot_touch(monkeypatch):
    """Capture fixtures and subprocess pipes replace these streams entirely."""
    monkeypatch.setattr(util.sys, "stdout", object())

    util.force_utf8_streams()                     # must not raise


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


# --- where the tool keeps its own files ------------------------------------
#
# A frozen single-file build unpacks into a temp directory that is deleted on
# exit, so anything anchored to __file__ is lost at the end of every run. The
# authst cache lives in that directory, which silently turns a 0.5 ms cache
# hit back into a multi-second process-memory scan - so this must hold.

def test_app_dir_sits_beside_the_package_when_running_from_source(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)

    assert not util.is_frozen()
    assert util.app_dir() == os.path.dirname(
        os.path.dirname(os.path.abspath(util.__file__)))


def test_app_dir_follows_the_executable_when_frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "fne.exe"))

    assert util.is_frozen()
    assert util.app_dir() == str(tmp_path)
    # The package directory is *not* used - it would be the temp unpack dir.
    assert util.app_dir() != os.path.dirname(
        os.path.dirname(os.path.abspath(util.__file__)))


def test_cache_lands_beside_the_executable_when_frozen(monkeypatch, tmp_path):
    """Re-import with ``sys.frozen`` set, so the real derivation is exercised.

    Both the correct and the buggy implementation agree while running from
    source, so only a frozen re-import can tell them apart.
    """
    import importlib

    from fne import qqmusic

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "fne.exe"))
    try:
        reloaded = importlib.reload(qqmusic)
        assert os.path.dirname(reloaded._CACHE_DIR) == str(tmp_path)
        assert os.path.dirname(reloaded.CACHE_PATH) == reloaded._CACHE_DIR
    finally:
        monkeypatch.undo()
        importlib.reload(qqmusic)          # leave the module as we found it
