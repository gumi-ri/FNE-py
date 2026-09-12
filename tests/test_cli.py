"""CLI helpers: folder scanning and the incremental-skip bookkeeping."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fne import cli  # noqa: E402


def test_build_existing_set_ignores_empty_files(tmp_path):
    """A zero-byte leftover must not make the next run skip that track."""
    (tmp_path / "good.flac").write_bytes(b"x")
    (tmp_path / "empty.flac").write_bytes(b"")
    (tmp_path / "notes.txt").write_bytes(b"x")

    assert cli.build_existing_set(str(tmp_path)) == {"good"}


def test_build_existing_set_on_a_missing_directory(tmp_path):
    assert cli.build_existing_set(str(tmp_path / "nope")) == set()


def test_scan_files_splits_by_container(tmp_path):
    for name in ("b.ncm", "a.mflac", "c.mgg", "d.lrc", "e.txt"):
        (tmp_path / name).write_bytes(b"x")

    ncm_files, qmc2_files = cli.scan_files(str(tmp_path), recursive=False)

    assert [os.path.basename(p) for p in ncm_files] == ["b.ncm"]
    assert [os.path.basename(p) for p in qmc2_files] == ["a.mflac", "c.mgg"]


def test_scan_files_recurses_when_asked(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (tmp_path / "top.ncm").write_bytes(b"x")
    (sub / "deep.ncm").write_bytes(b"x")

    flat, _ = cli.scan_files(str(tmp_path), recursive=False)
    deep, _ = cli.scan_files(str(tmp_path), recursive=True)

    assert len(flat) == 1
    assert len(deep) == 2
