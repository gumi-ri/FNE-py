"""Metadata embedding tests, verified through mutagen itself."""

import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mutagen.flac import FLAC  # noqa: E402
from mutagen.id3 import ID3, ID3NoHeaderError  # noqa: E402

from fne import qmc2, tags  # noqa: E402

PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 4 + b"IHDR"
       + struct.pack(">II", 300, 200) + bytes([8, 2]) + b"\x00" * 25)


def _jpeg(width: int, height: int) -> bytes:
    """A structurally valid JPEG header: SOI + APP0 + SOF0."""
    app0 = (b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00"
            + b"\x01\x01\x00" + struct.pack(">HH", 1, 1) + b"\x00\x00")
    sof0 = (b"\xff\xc0" + struct.pack(">H", 17) + b"\x08"
            + struct.pack(">HH", height, width) + b"\x03" + bytes(9))
    return b"\xff\xd8" + app0 + sof0


def _streaminfo(rate: int = 44100, channels: int = 2, bps: int = 16,
                total: int = 0) -> bytes:
    """A structurally valid 34-byte FLAC STREAMINFO (mutagen validates it)."""
    packed = ((rate << 44) | ((channels - 1) << 41) | ((bps - 1) << 36) | total)
    return ((4096).to_bytes(2, "big") * 2      # min / max blocksize
            + b"\x00" * 6                      # min / max framesize
            + packed.to_bytes(8, "big")
            + bytes(16))                       # MD5


def _flac_file(path, payload=b"\xff\xf8" + bytes(30)):
    data = b"fLaC" + bytes([0x80, 0x00, 0x00, 0x22]) + _streaminfo() + payload
    path.write_bytes(data)
    return bytes(data)


def test_sniff_mime():
    assert tags.sniff_mime(PNG) == "image/png"
    assert tags.sniff_mime(_jpeg(1, 1)) == "image/jpeg"
    assert tags.sniff_mime(b"GIF89a") == "image/gif"


def test_image_dimensions():
    assert tags.image_dimensions(PNG, "image/png") == (300, 200, 24, 0)
    assert tags.image_dimensions(_jpeg(160, 90), "image/jpeg") == (160, 90, 24, 0)
    assert tags.image_dimensions(b"not an image", "image/jpeg") == (0, 0, 0, 0)


def test_embed_flac_writes_tags_and_cover(tmp_path):
    path = tmp_path / "song.flac"
    original = _flac_file(path)

    tags.embed_flac(str(path), comments=[("TITLE", "曲名"), ("ARTIST", "A/B")],
                    image_data=PNG)

    audio = FLAC(str(path))
    assert audio["TITLE"] == ["曲名"]
    assert audio["ARTIST"] == ["A/B"]
    assert len(audio.pictures) == 1
    assert audio.pictures[0].type == 3
    assert audio.pictures[0].data == PNG
    assert audio.pictures[0].width == 300
    assert audio.pictures[0].height == 200

    # metadata was spliced in; the audio frames must be untouched
    payload = original[len(b"fLaC") + 38:]
    assert path.read_bytes().endswith(payload)


def test_embed_mp3_writes_id3v24(tmp_path):
    path = tmp_path / "song.mp3"
    audio = b"\xff\xfb\x90\x00" + b"mp3" * 50
    path.write_bytes(audio)

    tags.embed_mp3(str(path), title="T", artist="A", album="AL",
                   image_data=_jpeg(64, 64))

    id3 = ID3(str(path))
    assert str(id3["TIT2"]) == "T"
    assert str(id3["TPE1"]) == "A"
    assert str(id3["TALB"]) == "AL"
    picture = id3.getall("APIC")[0]          # key is 'APIC:' (empty description)
    assert picture.data[:2] == b"\xff\xd8"
    assert picture.mime == "image/jpeg"
    assert path.read_bytes().endswith(audio)


def test_embed_mp3_on_untagged_file_creates_header(tmp_path):
    path = tmp_path / "raw.mp3"
    path.write_bytes(b"\xff\xfb" + bytes(64))

    with pytest.raises(ID3NoHeaderError):
        ID3(str(path))

    tags.embed_mp3(str(path), title="New")
    assert str(ID3(str(path))["TIT2"]) == "New"


def test_musicex_footer_parsing():
    media_mid = "003abcdefg"
    filename = "C400" + media_mid + ".mflac"
    meta = bytearray(0xC0 - 16)
    struct.pack_into("<I", meta, 0, 12345)
    meta[0x0C:0x0C + len(media_mid.encode("utf-16-le"))] = media_mid.encode("utf-16-le")
    name_bytes = filename.encode("utf-16-le")
    meta[0x48:0x48 + len(name_bytes)] = name_bytes

    footer_size = len(meta) + 16
    data = (b"AUDIODATA" + bytes(meta)
            + struct.pack("<II", footer_size, 1) + b"musicex\x00")
    info = qmc2.parse_musicex_footer(data)
    assert info.song_id == 12345
    assert info.media_mid == media_mid
    assert info.filename == filename


def test_musicex_rejects_bad_magic():
    with pytest.raises(ValueError):
        qmc2.parse_musicex_footer(b"nope")


def test_output_format_mapping():
    assert qmc2.get_output_format(".mflac") == "flac"
    assert qmc2.get_output_format(".mgg") == "ogg"
    assert qmc2.get_output_format(".ogg") == "flac"
