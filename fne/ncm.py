"""NCM (Netease Cloud Music) decryption and conversion.

The payload is scrambled with an RC4-like key box; the metadata block is
AES-ECB encrypted with two hardcoded keys. As with QMC2, the audio stream
is never re-encoded.

The keystream repeats every 256 bytes, so it is tabulated once and applied
with a single big-integer XOR rather than a per-byte Python loop.
"""

from __future__ import annotations

import base64
import json
import os
import struct

from . import tags, util, win32
from .aes import ecb_decrypt

CORE_KEY = bytes.fromhex("687A4852416D736F356B496E62617857")
META_KEY = bytes.fromhex("2331346C6A6B5F215C5D2630553C2728")

HEADER = b"CTENFDAM"
KEY_XOR = 0x64
META_XOR = 0x63
KEY_STRIP = 17        # "neteasecloudmusic" prefix inside the decrypted blob
META_STRIP = 6        # "music:" prefix inside the decrypted blob
META_PREFIX = b"163 key(Don't modify):"   # wrapper in front of the metadata blob
AUDIO_GAP = 9         # CRC32 (4 bytes) + 5 reserved bytes before the cover
SUPPORTED_FORMATS = ("flac", "mp3")


def _unpad(src: bytes) -> bytes:
    if not src:
        return src
    count = src[-1]
    if count > len(src):
        return src
    return src[:-count]


def _build_key_box(key_data: bytes) -> bytes:
    box = bytearray(range(256))
    last_byte = 0
    key_offset = 0
    key_len = len(key_data)
    for i in range(256):
        c = (box[i] + last_byte + key_data[key_offset]) & 0xFF
        key_offset = (key_offset + 1) % key_len
        box[i], box[c] = box[c], box[i]
        last_byte = c
    return bytes(box)


def _build_xor_table(key_box: bytes) -> bytes:
    """Byte i of the keystream is keyBox[(keyBox[j] + keyBox[(keyBox[j]+j) & 0xff]) & 0xff]."""
    table = bytearray(256)
    for i in range(256):
        j = (i + 1) & 0xFF
        table[i] = key_box[(key_box[j] + key_box[(key_box[j] + j) & 0xFF]) & 0xFF]
    return bytes(table)


def _xor_stream(data: bytes, key_box: bytes) -> bytes:
    # _build_xor_table already folds in the (i + 1) offset, so the table is
    # applied as-is from index 0.
    return util.xor_repeating(data, _build_xor_table(key_box))


def _read_u32(data: bytes, pos: int, what: str) -> int:
    """Read a little-endian u32, with a clear error on truncated input."""
    if pos + 4 > len(data):
        raise ValueError(f"truncated ncm: expected a 4-byte {what} at offset {pos}")
    return struct.unpack_from("<I", data, pos)[0]


def parse_artist(raw) -> str:
    """NCM stores artists either as [[name, id], ...] or as a flat list."""
    if raw is None:
        return "Unknown Artist"
    if isinstance(raw, list):
        names = []
        for item in raw:
            if isinstance(item, list) and item:
                names.append(str(item[0]))
            elif isinstance(item, str):
                names.append(item)
            elif isinstance(item, (int, float)):
                names.append(str(int(item)))
        return "/".join(names) if names else "Unknown Artist"
    if isinstance(raw, str):
        return raw
    if isinstance(raw, (int, float)):
        return str(int(raw))
    return "Unknown Artist"


def convert_file(input_path: str, output_dir: str) -> str:
    """Decrypt an NCM file. Returns the output extension ('flac' or 'mp3')."""
    with open(input_path, "rb") as fh:
        data = fh.read()

    if len(data) < len(HEADER) + 2:
        raise ValueError("truncated ncm: file is shorter than the header")
    if not data.startswith(HEADER):
        raise ValueError("invalid ncm header")
    pos = len(HEADER) + 2

    key_len = _read_u32(data, pos, "key length")
    pos += 4
    if pos + key_len > len(data):
        raise ValueError("truncated ncm: key block runs past end of file")
    key_enc = bytes(b ^ KEY_XOR for b in data[pos:pos + key_len])
    pos += key_len
    key_data = _unpad(ecb_decrypt(CORE_KEY, key_enc))[KEY_STRIP:]
    if not key_data:
        raise ValueError("empty key data")

    key_box = _build_key_box(key_data)

    meta_len = _read_u32(data, pos, "metadata length")
    pos += 4
    if pos + meta_len > len(data):
        raise ValueError("truncated ncm: metadata block runs past end of file")
    # The whole block - wrapper included - is XOR scrambled, so the plain
    # "163 key(...)" wrapper only becomes visible after decrypting.
    meta_enc = bytes(b ^ META_XOR for b in data[pos:pos + meta_len])
    pos += meta_len
    if not meta_enc.startswith(META_PREFIX):
        raise ValueError("unexpected ncm metadata prefix")
    meta_raw = base64.b64decode(meta_enc[len(META_PREFIX):])
    meta_json = _unpad(ecb_decrypt(META_KEY, meta_raw))[META_STRIP:]
    meta = json.loads(meta_json.decode("utf-8"))

    pos += AUDIO_GAP
    image_size = _read_u32(data, pos, "cover size")
    pos += 4
    if pos + image_size > len(data):
        raise ValueError("truncated ncm: cover block runs past end of file")
    image_data = data[pos:pos + image_size]
    pos += image_size

    audio = _xor_stream(data[pos:], key_box)

    # ``format`` comes from the file's own metadata, so it is untrusted: it
    # must never be pasted into a filename unchecked.
    fmt = str(meta.get("format") or "flac").lower()
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"unsupported ncm audio format: {fmt!r}")

    base = os.path.splitext(os.path.basename(input_path))[0]
    output_path = os.path.join(output_dir, base + "." + fmt)

    util.write_atomic(output_path, audio)

    artist = parse_artist(meta.get("artist"))
    if fmt == "mp3":
        tags.embed_mp3(output_path, title=meta.get("musicName", ""),
                       artist=artist, album=meta.get("album", ""),
                       image_data=image_data or None,
                       mime=tags.sniff_mime(image_data) if image_data else "image/jpeg")
    else:
        tags.embed_flac(output_path,
                        comments=[("TITLE", meta.get("musicName", "")),
                                  ("ALBUM", meta.get("album", "")),
                                  ("ARTIST", artist)],
                        image_data=image_data or None)

    win32.copy_creation_time(input_path, output_path)
    return fmt
