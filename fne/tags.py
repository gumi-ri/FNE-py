"""Audio metadata embedding via mutagen.

Replaces the hand-written FLAC block splicer and ID3v2 writer. mutagen is
the de-facto standard for tag manipulation and is battle-tested against
far more player quirks than a from-scratch implementation ever will be.
"""

from __future__ import annotations


from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, ID3NoHeaderError, TALB, TIT2, TPE1

PICTURE_TYPE_FRONT_COVER = 3


def sniff_mime(image_data: bytes) -> str:
    if image_data.startswith(b"\x89\x50"):
        return "image/png"
    if image_data.startswith(b"RIFF") and image_data[8:12] == b"WEBP":
        return "image/webp"
    if image_data.startswith(b"GIF8"):
        return "image/gif"
    return "image/jpeg"


def image_dimensions(image_data: bytes, mime: str) -> tuple[int, int, int, int]:
    """Best-effort (width, height, colour depth, palette size) for a cover."""
    try:
        if mime == "image/png" and image_data.startswith(b"\x89PNG\r\n\x1a\n"):
            if len(image_data) >= 26 and image_data[12:16] == b"IHDR":
                width = int.from_bytes(image_data[16:20], "big")
                height = int.from_bytes(image_data[20:24], "big")
                bit_depth = image_data[24]
                colour_type = image_data[25]
                channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(colour_type, 0)
                return width, height, bit_depth * channels, 0

        if mime == "image/jpeg" and image_data[:2] == b"\xff\xd8":
            pos = 2
            while pos + 9 < len(image_data):
                if image_data[pos] != 0xFF:
                    pos += 1
                    continue
                marker = image_data[pos + 1]
                # SOF0..SOF15, excluding DHT/JPG/DAC which share the range
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    height = int.from_bytes(image_data[pos + 5:pos + 7], "big")
                    width = int.from_bytes(image_data[pos + 7:pos + 9], "big")
                    return width, height, 24, 0
                if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                    pos += 2
                    continue
                pos += 2 + int.from_bytes(image_data[pos + 2:pos + 4], "big")

        if mime == "image/gif" and image_data[:3] == b"GIF":
            if len(image_data) >= 10:
                width = int.from_bytes(image_data[6:8], "little")
                height = int.from_bytes(image_data[8:10], "little")
                return width, height, 24, 0
    except Exception:
        pass
    return 0, 0, 0, 0


def _no_padding(_info) -> int:
    """mutagen only accepts a callable for the ``padding`` argument."""
    return 0


def embed_flac(path: str, comments: list[tuple[str, str]] | None = None,
               image_data: bytes | None = None) -> None:
    """Append Vorbis Comment fields and/or a front-cover Picture to a FLAC file."""
    audio = FLAC(path)

    if comments:
        for key, value in comments:
            audio[key] = value

    if image_data:
        mime = sniff_mime(image_data)
        picture = Picture()
        picture.type = PICTURE_TYPE_FRONT_COVER
        picture.mime = mime
        picture.desc = ""
        picture.data = image_data
        picture.width, picture.height, picture.depth, picture.colors = \
            image_dimensions(image_data, mime)
        audio.add_picture(picture)

    # Keep the output tight - the default reserves several tens of KB that a
    # freshly converted file has no use for.
    audio.save(padding=_no_padding)


def embed_mp3(path: str, title: str = "", artist: str = "", album: str = "",
              image_data: bytes | None = None,
              mime: str | None = None) -> None:
    """Write ID3v2.4 tags (title/artist/album plus optional cover) to an MP3."""
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()

    if title:
        tags.setall("TIT2", [TIT2(encoding=3, text=title)])
    if artist:
        tags.setall("TPE1", [TPE1(encoding=3, text=artist)])
    if album:
        tags.setall("TALB", [TALB(encoding=3, text=album)])
    if image_data:
        mime = mime or sniff_mime(image_data)
        tags.setall("APIC", [APIC(encoding=3, mime=mime,
                                  type=PICTURE_TYPE_FRONT_COVER, desc="",
                                  data=image_data)])

    tags.save(path, v2_version=4)
