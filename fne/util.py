"""Shared low-level helpers: periodic XOR and atomic file writes.

Both are deliberately in one tiny module rather than duplicated in the NCM
and QMC2 converters, which is where they used to live.
"""

from __future__ import annotations

import os
import tempfile

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
