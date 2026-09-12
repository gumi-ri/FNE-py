"""NCM path tests: synthetic round trip plus input-validation guards.

The round trip builds a container with the exact inverse of the reader, which
exercises AES, the key box, chunk boundaries and metadata embedding together.
Real licensed files are verified out of band (see README); these tests keep
the fast path honest without shipping a sample.
"""

import base64
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mutagen.flac import FLAC  # noqa: E402
from mutagen.id3 import ID3  # noqa: E402

from fne import aes, ncm  # noqa: E402

CORE_KEY = bytes.fromhex("687A4852416D736F356B496E62617857")
META_KEY = bytes.fromhex("2331346C6A6B5F215C5D2630553C2728")
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 120


def _pkcs7(data: bytes) -> bytes:
    pad = 16 - len(data) % 16
    return data + bytes([pad]) * pad


def build_ncm(path: str, audio: bytes, meta: dict, image: bytes,
              key_data: bytes) -> None:
    """Write an NCM container that ncm.convert_file should read back."""
    key_enc = bytes(b ^ 0x64 for b in
                    aes.ecb_encrypt(CORE_KEY, _pkcs7(b"neteasecloudmusic" + key_data)))

    # Real files scramble the wrapper together with the payload, so the plain
    # "163 key(...)" prefix only appears after the XOR - verified against a
    # licensed sample. Building it the other way round would let the reader
    # and this builder agree with each other while both disagree with reality.
    meta_plain = b"163 key(Don't modify):" + base64.b64encode(
        aes.ecb_encrypt(META_KEY, _pkcs7(b"music:" + json.dumps(meta).encode())))
    meta_enc = bytes(b ^ 0x63 for b in meta_plain)

    scrambled = ncm._xor_stream(audio, ncm._build_key_box(key_data))

    out = bytearray(b"CTENFDAM" + b"\x00\x00")
    out += len(key_enc).to_bytes(4, "little") + key_enc
    out += len(meta_enc).to_bytes(4, "little") + meta_enc
    out += b"\x00" * 9                       # CRC32 + 5 byte gap
    out += len(image).to_bytes(4, "little") + image
    out += scrambled

    with open(path, "wb") as fh:
        fh.write(bytes(out))


def _streaminfo(rate: int = 44100, channels: int = 2, bps: int = 16,
                total: int = 0) -> bytes:
    """A structurally valid 34-byte FLAC STREAMINFO (mutagen validates it)."""
    packed = ((rate << 44) | ((channels - 1) << 41) | ((bps - 1) << 36) | total)
    return ((4096).to_bytes(2, "big") * 2 + b"\x00" * 6
            + packed.to_bytes(8, "big") + bytes(16))


def _synth_flac(payload: bytes) -> bytes:
    """magic + STREAMINFO + payload."""
    return (b"fLaC" + bytes([0x80, 0x00, 0x00, 0x22])
            + _streaminfo() + payload)


def test_key_box_stream_matches_naive():
    key_data = bytes(range(1, 33))
    box = ncm._build_key_box(key_data)

    data = bytes(i & 0xFF for i in range(600))
    expected = bytearray()
    for i, byte in enumerate(data):
        j = (i + 1) & 0xFF
        expected.append(byte ^ box[(box[j] + box[(box[j] + j) & 0xFF]) & 0xFF])

    assert ncm._xor_stream(data, box) == bytes(expected)


def test_ncm_roundtrip_flac(tmp_path):
    payload = b"\xff\xf8" + bytes(30)
    audio = _synth_flac(payload)
    meta = {"musicName": "テスト曲", "album": "Album X",
            "artist": [["Artist A", 1], ["Artist B", 2]], "format": "flac"}

    src = tmp_path / "song.ncm"
    build_ncm(str(src), audio, meta, JPEG, os.urandom(64))

    assert ncm.convert_file(str(src), str(tmp_path)) == "flac"

    written = (tmp_path / "song.flac").read_bytes()
    assert written.endswith(payload)                    # payload untouched

    out = FLAC(str(tmp_path / "song.flac"))
    assert out["TITLE"] == ["テスト曲"]
    assert out["ALBUM"] == ["Album X"]
    assert out["ARTIST"] == ["Artist A/Artist B"]
    assert out.pictures and out.pictures[0].data == JPEG


def test_ncm_roundtrip_mp3(tmp_path):
    audio = b"\xff\xfb\x90\x00" + b"mp3" * 500          # must not start with "ID3"
    meta = {"musicName": "MP3 Song", "album": "Album Y",
            "artist": ["Solo Artist"], "format": "mp3"}

    src = tmp_path / "song.ncm"
    build_ncm(str(src), audio, meta, JPEG, os.urandom(48))

    assert ncm.convert_file(str(src), str(tmp_path)) == "mp3"

    path = tmp_path / "song.mp3"
    data = path.read_bytes()
    assert data.endswith(audio)
    id3 = ID3(str(path))
    assert str(id3["TIT2"]) == "MP3 Song"
    assert str(id3["TPE1"]) == "Solo Artist"
    assert id3.getall("APIC")[0].data[:2] == b"\xff\xd8"


def test_ncm_large_payload_crosses_chunk_boundary(tmp_path):
    """The Go reader restarts its counter per 32 KiB chunk; make sure we match."""
    payload = b"\xff\xf8" + bytes(30)
    audio = _synth_flac(payload) + os.urandom(90000)
    meta = {"musicName": "Big", "album": "Big", "artist": [["A", 1]],
            "format": "flac"}

    src = tmp_path / "big.ncm"
    build_ncm(str(src), audio, meta, b"", os.urandom(32))

    ncm.convert_file(str(src), str(tmp_path))
    written = (tmp_path / "big.flac").read_bytes()
    assert written.endswith(audio[len(b"fLaC") + 38:])


def test_parse_artist_variants():
    assert ncm.parse_artist([["A", 1], ["B", 2]]) == "A/B"
    assert ncm.parse_artist(["A", "B"]) == "A/B"
    assert ncm.parse_artist("Solo") == "Solo"
    assert ncm.parse_artist(12345) == "12345"
    assert ncm.parse_artist(None) == "Unknown Artist"


# --- hostile input ----------------------------------------------------------

def test_ncm_rejects_an_unsafe_format(tmp_path):
    """``format`` comes from the file, so it must not reach the filename as-is."""
    audio = _synth_flac(b"\xff\xf8" + bytes(30))
    meta = {"musicName": "x", "album": "y", "artist": ["z"],
            "format": "../../evil"}

    src = tmp_path / "song.ncm"
    build_ncm(str(src), audio, meta, JPEG, os.urandom(32))

    with pytest.raises(ValueError, match="unsupported ncm audio format"):
        ncm.convert_file(str(src), str(tmp_path))


def test_ncm_rejects_a_bad_header(tmp_path):
    src = tmp_path / "bad.ncm"
    src.write_bytes(b"NOTANNCM" + bytes(64))

    with pytest.raises(ValueError, match="invalid ncm header"):
        ncm.convert_file(str(src), str(tmp_path))


def test_ncm_rejects_truncated_lengths(tmp_path):
    """A corrupt length must give a clear error, not a struct.error."""
    src = tmp_path / "cut.ncm"
    src.write_bytes(b"CTENFDAM" + b"\x00\x00" + b"\xff\xff\xff\xff")

    with pytest.raises(ValueError, match="truncated ncm"):
        ncm.convert_file(str(src), str(tmp_path))


def test_ncm_rejects_a_file_shorter_than_the_header(tmp_path):
    src = tmp_path / "tiny.ncm"
    src.write_bytes(b"CTEN")

    with pytest.raises(ValueError, match="truncated ncm"):
        ncm.convert_file(str(src), str(tmp_path))
