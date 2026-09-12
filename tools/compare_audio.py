"""Compare the *audio frames* of two sets of FLAC files.

Tag, padding and cover blocks legitimately differ between implementations;
the decrypted audio stream must not. Parse past the metadata block chain and
hash what remains.
"""

import hashlib
import os
import sys


def split_flac(path: str) -> tuple[bytes, list[tuple[int, int, int]], bytes]:
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
        if is_last:
            break
    return data[:pos], blocks, data[pos:]


left, right = sys.argv[1], sys.argv[2]
names = sorted(n for n in os.listdir(left) if n.lower().endswith(".flac"))

TYPE_NAMES = {0: "STREAMINFO", 1: "PADDING", 2: "APPLICATION", 3: "SEEKTABLE",
              4: "VORBIS_COMMENT", 5: "CUESHEET", 6: "PICTURE"}

identical = 0
for name in names:
    pa, pb = os.path.join(left, name), os.path.join(right, name)
    if not os.path.isfile(pa) or not os.path.isfile(pb):
        continue
    _ha, ba, fa = split_flac(pa)
    _hb, bb, fb = split_flac(pb)
    sha_a = hashlib.sha1(fa).hexdigest()
    sha_b = hashlib.sha1(fb).hexdigest()
    verdict = "AUDIO SAME" if sha_a == sha_b else "AUDIO DIFF"
    if sha_a == sha_b:
        identical += 1
    print(f"[{verdict}] {name}")
    print(f"        frames py: {len(fa):>10} bytes  sha1={sha_a}")
    print(f"        frames go: {len(fb):>10} bytes  sha1={sha_b}")
    print(f"        blocks py: {[(TYPE_NAMES.get(t, t), n) for _l, t, n in ba]}")
    print(f"        blocks go: {[(TYPE_NAMES.get(t, t), n) for _l, t, n in bb]}")
    print()

print(f"{identical}/{len(names)} audio streams identical")
