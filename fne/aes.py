"""AES-ECB via the *cryptography* package.

The NCM container only encrypts a few hundred bytes of key and metadata,
but a hand-rolled AES was exactly the kind of code that invited subtle
bugs, so the audited implementation is used instead.
"""

from __future__ import annotations

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def _cipher(key: bytes) -> Cipher:
    return Cipher(algorithms.AES(key), modes.ECB())


def ecb_decrypt(key: bytes, data: bytes) -> bytes:
    """AES-ECB decrypt. Input length must be a multiple of 16."""
    if len(key) not in (16, 24, 32):
        raise ValueError(f"key must be 16/24/32 bytes, got {len(key)}")
    decryptor = _cipher(key).decryptor()
    return decryptor.update(data) + decryptor.finalize()


def ecb_encrypt(key: bytes, data: bytes) -> bytes:
    """AES-ECB encrypt. Input length must be a multiple of 16."""
    if len(key) not in (16, 24, 32):
        raise ValueError(f"key must be 16/24/32 bytes, got {len(key)}")
    encryptor = _cipher(key).encryptor()
    return encryptor.update(data) + encryptor.finalize()
