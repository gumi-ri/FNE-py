"""FNE-py -- Python port of FNE.

Converts Netease Cloud Music (.ncm) and QQ Music QMC2 (.mflac/.mgg)
encrypted files into plain FLAC / MP3 / OGG while preserving tags and
cover art.

Modules:
    aes       AES-ECB via the *cryptography* package
    tea       TEA and Tencent TC-TEA (no library exists for this variant)
    tags      metadata embedding via mutagen
    ncm       NCM container decryption
    qmc2      QMC2 container decryption
    qqmusic   client credentials and API access
    win32     process memory scanning and file times (Windows only)
    cli       configuration and the conversion pipeline
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
