"""Compare the *audio frames* of two sets of FLAC files.

Tag, padding and cover blocks legitimately differ between builds; the
decrypted audio stream must not. Parse past the metadata block chain and hash
what remains, so a refactor that changes tag layout does not drown out a
refactor that changed the audio.

Usage::

    python tools/compare_audio.py <目录A> <目录B>

Exits non-zero if any file differs, so a release check can gate on it.
"""

import hashlib
import os
import sys

TYPE_NAMES = {0: "STREAMINFO", 1: "PADDING", 2: "APPLICATION", 3: "SEEKTABLE",
              4: "VORBIS_COMMENT", 5: "CUESHEET", 6: "PICTURE"}


def split_flac(path: str) -> tuple[list[tuple[int, int, int]], bytes]:
    """Return the metadata blocks and the audio frames after them."""
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:4] != b"fLaC":
        raise ValueError(f"{path}: not a FLAC file")
    pos = 4
    blocks: list[tuple[int, int, int]] = []  # (is_last, type, length)
    while True:
        header = data[pos]
        is_last = bool(header & 0x80)
        btype = header & 0x7F
        length = int.from_bytes(data[pos + 1:pos + 4], "big")
        blocks.append((int(is_last), btype, length))
        pos += 4 + length
        # STREAMINFO must come first and alone, so a malformed chain cannot
        # spin here forever.
        if is_last or pos >= len(data):
            break
    return blocks, data[pos:]


def _label(directory: str) -> str:
    return os.path.basename(os.path.normpath(os.path.abspath(directory))) or directory


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("用法: python tools/compare_audio.py <目录A> <目录B>")
        print("比较两个目录里同名 FLAC 的音频帧，忽略标签与封面块。")
        return 2

    left, right = argv[1], argv[2]
    label_a, label_b = _label(left), _label(right)
    names = sorted(n for n in os.listdir(left) if n.lower().endswith(".flac"))

    identical = compared = 0
    for name in names:
        pa, pb = os.path.join(left, name), os.path.join(right, name)
        if not os.path.isfile(pb):
            print(f"[MISSING   ] {name}  (not in {label_b})")
            continue
        compared += 1
        ba, fa = split_flac(pa)
        bb, fb = split_flac(pb)
        sha_a = hashlib.sha1(fa).hexdigest()
        sha_b = hashlib.sha1(fb).hexdigest()
        same = sha_a == sha_b
        identical += same
        print(f"[{'AUDIO SAME' if same else 'AUDIO DIFF'}] {name}")
        print(f"        {label_a:>12}: {len(fa):>10} bytes  sha1={sha_a}")
        print(f"        {label_b:>12}: {len(fb):>10} bytes  sha1={sha_b}")
        # Only worth printing when something differs - otherwise it is noise.
        if not same:
            print(f"        {label_a:>12}: {[(TYPE_NAMES.get(t, t), n) for _l, t, n in ba]}")
            print(f"        {label_b:>12}: {[(TYPE_NAMES.get(t, t), n) for _l, t, n in bb]}")
        print()

    print(f"{identical}/{compared} audio streams identical")
    return 0 if compared and identical == compared else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
