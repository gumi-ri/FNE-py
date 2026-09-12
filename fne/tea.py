"""TEA and Tencent's TC-TEA (oi_symmetry_decrypt2).

Ported from the Go implementation. Everything is 32-bit unsigned, so each
step has to be masked back into range -- Python integers would otherwise
grow without bound.
"""

from __future__ import annotations

import struct

MASK32 = 0xFFFFFFFF
DELTA = 0x9E3779B9

SALT_LEN = 2
ZERO_LEN = 7


def simple_make_key(seed: int, size: int) -> bytes:
    """Derive a small key from a seed: byte(100 * |tan(seed + i*0.1)|)."""
    import math
    return bytes(int(100.0 * abs(math.tan(seed + i * 0.1))) & 0xFF
                 for i in range(size))


def derive_tea_key(ekey_header: bytes) -> bytes:
    """Interleave the simple key with the ekey header to form a 16-byte TEA key."""
    simple = simple_make_key(106, 8)
    key = bytearray(16)
    for i in range(0, 16, 2):
        key[i] = simple[i // 2]
        key[i + 1] = ekey_header[i // 2]
    return bytes(key)


def tea_decrypt_ecb(block: bytes, key: bytes) -> bytes:
    """Decrypt one 8-byte TEA block (big-endian words, 16 rounds)."""
    y, z = struct.unpack(">II", block)
    k = struct.unpack(">4I", key)

    sum_ = (DELTA * 16) & MASK32
    for _ in range(16):
        z = (z - ((((y << 4) + k[2]) & MASK32) ^ ((y + sum_) & MASK32)
                  ^ (((y >> 5) + k[3]) & MASK32))) & MASK32
        y = (y - ((((z << 4) + k[0]) & MASK32) ^ ((z + sum_) & MASK32)
                  ^ (((z >> 5) + k[1]) & MASK32))) & MASK32
        sum_ = (sum_ - DELTA) & MASK32

    return struct.pack(">II", y, z)


def tc_tea_decrypt(data: bytes, key: bytes) -> bytes:
    """Decrypt data encrypted with Tencent's TC-TEA (CBC-flavoured) scheme."""
    if len(key) != 16:
        raise ValueError("TEA key must be 16 bytes")
    if len(data) < 16 or len(data) % 8 != 0:
        raise ValueError("TEA data must be a multiple of 8 bytes and >= 16")

    dest_buf = bytearray(tea_decrypt_ecb(data[0:8], key))
    pad_len = dest_buf[0] & 0x07

    plain_len = len(data) - 1 - pad_len - SALT_LEN - ZERO_LEN
    if plain_len < 0:
        raise ValueError("invalid TC-TEA padding")

    iv_pre = b"\x00" * 8
    iv_cur = data[0:8]

    pos = 8          # position within the *remaining* ciphertext
    dest_i = 1 + pad_len

    def xor_with_iv(buf: bytearray, iv: bytes) -> None:
        for j in range(8):
            buf[j] ^= iv[j]

    def next_block() -> None:
        """Advance to the next cipher block. Returns False if data ran out."""
        nonlocal pos, dest_i, iv_pre, iv_cur
        if len(data) - pos < 8:
            return False
        iv_pre = iv_cur
        iv_cur = data[pos:pos + 8]
        xor_with_iv(dest_buf, data[pos:pos + 8])
        dest_buf[:] = tea_decrypt_ecb(bytes(dest_buf), key)
        pos += 8
        dest_i = 0
        return True

    # Skip salt
    i = 1
    while i <= SALT_LEN:
        if dest_i < 8:
            dest_i += 1
            i += 1
        elif not next_block():
            raise ValueError("invalid TC-TEA data")

    out = bytearray()
    while plain_len > 0:
        if dest_i < 8:
            out.append(dest_buf[dest_i] ^ iv_pre[dest_i])
            dest_i += 1
            plain_len -= 1
        elif not next_block():
            raise ValueError("invalid TC-TEA data")

    # Verify trailing zero padding
    i = 1
    while i <= ZERO_LEN:
        if dest_i < 8:
            if dest_buf[dest_i] ^ iv_pre[dest_i] != 0:
                raise ValueError("invalid TC-TEA zero padding")
            dest_i += 1
            i += 1
        elif not next_block():
            break

    return bytes(out)
