"""Shared low-level helpers: periodic XOR and atomic file writes.

Both are deliberately in one tiny module rather than duplicated in the NCM
and QMC2 converters, which is where they used to live.
"""

from __future__ import annotations

import os
import sys
import tempfile


def is_frozen() -> bool:
    """True when running from a PyInstaller-style single-file bundle."""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> str:
    """The directory this tool should treat as "its own".

    Frozen single-file builds unpack themselves into a temporary directory
    that Windows deletes when the process exits, so ``__file__`` - and with
    it the package directory - points somewhere ephemeral. Anything that has
    to outlive the run (the authst cache, and the config the user drops
    beside the tool) must therefore be anchored to the executable instead.
    """
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def force_utf8_streams() -> None:
    """Make the standard streams accept non-ASCII text on Windows.

    A frozen build is the reason this exists. Development interpreters here
    happen to run in UTF-8 mode, but the interpreter inside a PyInstaller
    bundle does not, and when ``stdout`` is a pipe, a file or a CI log rather
    than a console there is no console code page to fall back on: Python uses
    the system ANSI code page, cp1252 on an English install. Every Chinese
    message then raises ``UnicodeEncodeError`` and kills the process
    mid-run - including the folder prompts, which is how it was found.

    Reconfiguring to UTF-8 makes the frozen build behave like the interpreters
    it was tested on. ``errors="replace"`` keeps a stream that genuinely
    cannot represent a character from ever taking the process down again.
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            # Replaced by a wrapper (capture fixtures, subprocess pipes, a
            # stream that refuses reconfiguration) - leave it as it is.
            pass


# Chunk size for the big-integer XOR. Both ciphers are periodic, so a
# whole-chunk int XOR is far faster than a per-byte loop; chunking keeps the
# temporary integers small instead of materialising one as large as the file.
XOR_CHUNK = 4 * 1024 * 1024


def xor_repeating(data: bytes, pattern: bytes) -> bytes:
    """XOR *data* with *pattern*, which repeats for the whole length.

    The pattern is tabulated once by the caller (a 256-byte key box for NCM,
    a 32 KiB table for the QMC2 map cipher), so this stays a pure memory
    operation - no per-byte Python loop.
    """
    if not pattern:
        raise ValueError("pattern must not be empty")
    total = len(data)
    if total == 0:
        return b""

    period = len(pattern)
    out = bytearray(total)
    for start in range(0, total, XOR_CHUNK):
        end = min(start + XOR_CHUNK, total)
        length = end - start

        offset = start % period
        window = pattern if offset == 0 else pattern[offset:] + pattern[:offset]
        mask = (window * (-(-length // period)))[:length]

        out[start:end] = (
            int.from_bytes(data[start:end], "big")
            ^ int.from_bytes(mask, "big")
        ).to_bytes(length, "big")
    return bytes(out)


def write_atomic(path: str, data: bytes) -> None:
    """Write *data* to *path* in one step.

    Conversion writes straight into the output folder, and the next run skips
    any output file whose name it recognises. A half-written file left behind
    by a crash, a full disk or a Ctrl+C would therefore look "already
    converted" forever. Writing to a temp file and renaming makes the file
    appear only once it is complete.
    """
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".fne-", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
