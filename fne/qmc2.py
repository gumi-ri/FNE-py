"""QMC2 (QQ Music) decryption and conversion.

The audio itself is never re-encoded: the container's payload is decrypted
in place and written straight back out as FLAC or OGG.

Performance note: the Go original XORs byte by byte, which a naive Python
port would turn into a multi-minute crawl for a 20 MB track. Both ciphers
are periodic, so the keystream is tabulated once and applied with a single
big-integer XOR instead.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass

from . import qqmusic, tags, util, win32
from .qqmusic import parse_ekey

FIRST_SEGMENT_SIZE = 0x80
OTHER_SEGMENT_SIZE = 0x1400
MAP_PERIOD = 0x7FFF          # offset wraps at 0x7FFF

QMC2_EXTENSIONS = {".mflac", ".mflac0", ".mflach", ".mgg", ".mgg0",
                   ".mgg1", ".mggl"}


@dataclass
class MusicexInfo:
    song_id: int
    media_mid: str
    filename: str
    footer_size: int


def is_qmc2(ext: str) -> bool:
    return ext.lower() in QMC2_EXTENSIONS


def get_output_format(ext: str) -> str:
    ext = ext.lower()
    if ext in (".mflac", ".mflac0", ".mflach"):
        return "flac"
    if ext in (".mgg", ".mgg0", ".mgg1", ".mggl"):
        return "ogg"
    return "flac"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _xor(data: bytes, mask: bytes) -> bytes:
    n = len(data)
    return (int.from_bytes(data, "big") ^ int.from_bytes(mask[:n], "big")).to_bytes(n, "big")


def _read_utf16le(data: bytes, offset: int, max_len: int) -> str:
    end = min(offset + max_len, len(data))
    chars = []
    for i in range(offset, end - 1, 2):
        code = struct.unpack_from("<H", data, i)[0]
        if code == 0:
            break
        chars.append(chr(code))
    return "".join(chars)


def parse_musicex_footer(data: bytes) -> MusicexInfo:
    if len(data) < 16 or data[-8:] != b"musicex\x00":
        raise ValueError("no musicex footer found")

    magic_start = len(data) - 8
    version_start = magic_start - 4
    footer_size_start = version_start - 4
    if footer_size_start < 4:
        raise ValueError("musicex footer too short")

    version = struct.unpack_from("<I", data, version_start)[0]
    footer_size = struct.unpack_from("<I", data, footer_size_start)[0]

    metadata_size = footer_size - 16
    if metadata_size <= 0 or metadata_size > footer_size_start:
        raise ValueError(f"invalid musicex footer: version={version}, "
                         f"footer_size={footer_size}")
    if version != 1:
        raise ValueError(f"unsupported musicex version: {version}")

    footer_start = len(data) - footer_size
    meta = data[footer_start:footer_size_start]

    song_id = struct.unpack_from("<I", meta, 0)[0] if len(meta) >= 4 else 0
    media_mid = _read_utf16le(meta, 0x0C, 60)
    filename = _read_utf16le(meta, 0x48, 68)
    if not media_mid or not filename:
        raise ValueError("could not extract media_mid or filename from footer")

    return MusicexInfo(song_id=song_id, media_mid=media_mid,
                       filename=filename, footer_size=footer_size)


# --------------------------------------------------------------------------
# Map cipher (key length <= 300)
# --------------------------------------------------------------------------

def _scramble_by_index(value: int, index: int) -> int:
    rotation = (index + 4) & 7
    left = value << rotation
    right = value >> (8 - rotation)
    return (left | right) & 0xFF


def _build_map_table(key: bytes) -> bytes:
    klen = len(key)
    return bytes(_scramble_by_index(key[(ol * ol + 71214) % klen],
                                    (ol * ol + 71214) % klen)
                 for ol in range(MAP_PERIOD + 1))


def _map_mask(table: bytes, offset: int, n: int) -> bytes:
    out = bytearray()
    head = 0
    if offset <= MAP_PERIOD:
        head = max(0, min(n, MAP_PERIOD + 1 - offset))
        out += table[offset:offset + head]
    rest = n - len(out)
    if rest > 0:
        start = (offset + head) % MAP_PERIOD
        cycle = table[:MAP_PERIOD]
        pattern = cycle[start:] + cycle[:start]
        reps = -(-rest // MAP_PERIOD)
        out += (pattern * reps)[:rest]
    return bytes(out)


def map_decrypt(key: bytes, offset: int, data: bytes) -> bytes:
    table = _build_map_table(key)
    total = len(data)
    out = bytearray(total)
    # Chunked so the mask never grows as large as a whole track.
    for start in range(0, total, util.XOR_CHUNK):
        end = min(start + util.XOR_CHUNK, total)
        out[start:end] = _xor(data[start:end],
                              _map_mask(table, offset + start, end - start))
    return bytes(out)


# --------------------------------------------------------------------------
# RC4 cipher (key length > 300)
# --------------------------------------------------------------------------

def calc_hash_base(data: bytes) -> int:
    h = 1
    for value in data:
        if value == 0:
            continue
        nxt = (h * value) & 0xFFFFFFFF
        if nxt == 0 or nxt <= h:
            break
        h = nxt
    return h


class Qmc2Rc4Crypto:
    """The segmented RC4 variant QMC2 uses for long keys."""

    def __init__(self, key: bytes) -> None:
        n = len(key)
        s = bytearray(i & 0xFF for i in range(n))
        j = 0
        for i in range(n):
            j = (j + s[i] + key[i]) % n
            s[i], s[j] = s[j], s[i]
        self.key = key
        self.n = n
        self.s0 = bytes(s)
        self.hash = calc_hash_base(key)
        self._keystream_cache: dict[int, bytes] = {}

    def calc_segment_key(self, seg_id: int, seed: int) -> int:
        dividend = float(self.hash)
        divisor = float(((seg_id + 1) * seed) % (1 << 64))
        if divisor == 0.0:
            divisor = 1.0
        return int(dividend / divisor * 100.0) & 0xFFFFFFFFFFFFFFFF

    def _keystream(self, discard: int, length: int) -> bytes:
        cached = self._keystream_cache.get(discard)
        if cached is not None and len(cached) >= length:
            return cached[:length]

        s = bytearray(self.s0)
        n = self.n
        j = k = 0
        for _ in range(discard):
            j = (j + 1) % n
            k = (s[j] + k) % n
            s[j], s[k] = s[k], s[j]
        out = bytearray(length)
        for i in range(length):
            j = (j + 1) % n
            k = (s[j] + k) % n
            s[j], s[k] = s[k], s[j]
            out[i] = s[(s[j] + s[k]) % n]
        ks = bytes(out)

        # Full aligned segments always discard < 512 bytes, so their
        # keystreams are reusable across the thousands of segments in a file.
        if discard <= 0x1FF and length == OTHER_SEGMENT_SIZE:
            self._keystream_cache[discard] = ks
        return ks

    def encode_first_segment(self, offset: int, buf: bytes) -> bytes:
        n = self.n
        key = self.key
        out = bytearray(len(buf))
        for i, byte in enumerate(buf):
            key1 = key[offset % n]
            key2 = self.calc_segment_key(offset, key1)
            out[i] = byte ^ key[key2 % n]
            offset += 1
        return bytes(out)

    def encode_other_segment(self, offset: int, buf: bytes) -> bytes:
        length = len(buf)
        seg_id = offset // OTHER_SEGMENT_SIZE
        seg_small = seg_id & 0x1FF
        discard = self.calc_segment_key(seg_id, self.key[seg_small]) & 0x1FF
        discard += offset % OTHER_SEGMENT_SIZE
        return _xor(buf, self._keystream(discard, length))

    def decrypt(self, offset: int, data: bytes) -> bytes:
        buf = bytearray(data)
        i = 0
        remaining = len(buf)

        if offset < FIRST_SEGMENT_SIZE:
            processed = min(remaining, FIRST_SEGMENT_SIZE - offset)
            buf[i:i + processed] = self.encode_first_segment(
                offset, bytes(buf[i:i + processed]))
            i += processed
            remaining -= processed
            offset += processed

        to_align = offset % OTHER_SEGMENT_SIZE
        if to_align != 0 and remaining > 0:
            processed = min(remaining, OTHER_SEGMENT_SIZE - to_align)
            buf[i:i + processed] = self.encode_other_segment(
                offset, bytes(buf[i:i + processed]))
            i += processed
            remaining -= processed
            offset += processed

        while remaining > OTHER_SEGMENT_SIZE:
            buf[i:i + OTHER_SEGMENT_SIZE] = self.encode_other_segment(
                offset, bytes(buf[i:i + OTHER_SEGMENT_SIZE]))
            i += OTHER_SEGMENT_SIZE
            remaining -= OTHER_SEGMENT_SIZE
            offset += OTHER_SEGMENT_SIZE

        if remaining > 0:
            buf[i:i + remaining] = self.encode_other_segment(
                offset, bytes(buf[i:i + remaining]))
        return bytes(buf)


# --------------------------------------------------------------------------
# conversion
# --------------------------------------------------------------------------

def convert_file(input_path: str, output_dir: str) -> str:
    """Decrypt a QMC2 file. Returns the output extension ('flac' or 'ogg')."""
    input_ext = os.path.splitext(input_path)[1].lower()

    with open(input_path, "rb") as fh:
        data = fh.read()

    info = parse_musicex_footer(data)

    api_filename = info.filename
    if not is_qmc2(os.path.splitext(api_filename)[1]):
        api_filename += input_ext

    ekey = qqmusic.ekey_for(api_filename, info.media_mid)
    key = parse_ekey(ekey)

    # parse_musicex_footer already validated the footer, so reuse the size it
    # read rather than parsing the same field a second time.
    audio_end = len(data) - info.footer_size
    if audio_end <= 0:
        raise ValueError("invalid footer size")

    audio = data[:audio_end]
    if len(key) > 300:
        audio = Qmc2Rc4Crypto(key).decrypt(0, audio)
    else:
        audio = map_decrypt(key, 0, audio)

    output_ext = get_output_format(input_ext)
    base = os.path.splitext(os.path.basename(input_path))[0]
    output_path = os.path.join(output_dir, base + "." + output_ext)

    util.write_atomic(output_path, audio)

    if output_ext == "flac":
        try:
            cover = qqmusic.fetch_album_cover(info.media_mid)
        except Exception:
            cover = b""
        if cover:
            tags.embed_flac(output_path, image_data=cover)

    win32.copy_creation_time(input_path, output_path)
    return output_ext
