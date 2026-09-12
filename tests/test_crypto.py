"""AES and QMC2 cipher tests.

The Python port replaces the original per-byte loops with tabulated
keystreams plus a single big-integer XOR. These tests pin the fast path
against a naive reference so the optimisation cannot silently drift.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fne import aes, qmc2  # noqa: E402


# --------------------------------------------------------------------------
# AES (FIPS-197 vectors)
# --------------------------------------------------------------------------

PLAINTEXT = bytes.fromhex("00112233445566778899aabbccddeeff")


@pytest.mark.parametrize("key_hex,expected_hex", [
    ("000102030405060708090a0b0c0d0e0f",
     "69c4e0d86a7b0430d8cdb78070b4c55a"),
    ("000102030405060708090a0b0c0d0e0f1011121314151617",
     "dda97ca4864cdfe06eaf70a0ec0d7191"),
    ("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f",
     "8ea2b7ca516745bfeafc49904b496089"),
])
def test_aes_encrypt_vectors(key_hex, expected_hex):
    key = bytes.fromhex(key_hex)
    assert aes.ecb_encrypt(key, PLAINTEXT).hex() == expected_hex
    assert aes.ecb_decrypt(key, bytes.fromhex(expected_hex)) == PLAINTEXT


def test_aes_rejects_bad_key_size():
    with pytest.raises(ValueError):
        aes.ecb_encrypt(b"short", PLAINTEXT)


def test_aes_rejects_non_block_aligned_input():
    with pytest.raises(Exception):
        aes.ecb_encrypt(bytes(16), b"not sixteen bytes")


# --------------------------------------------------------------------------
# QMC2 Map cipher
# --------------------------------------------------------------------------

def naive_map_decrypt(key: bytes, offset: int, data: bytes) -> bytes:
    klen = len(key)
    out = bytearray(data)
    for i, byte in enumerate(data):
        ol = offset + i
        if ol > 0x7FFF:
            ol %= 0x7FFF
        index = (ol * ol + 71214) % klen
        rotation = (index + 4) & 7
        out[i] = byte ^ (((key[index] << rotation) | (key[index] >> (8 - rotation))) & 0xFF)
    return bytes(out)


@pytest.mark.parametrize("key_len", [16, 44, 300])
def test_map_decrypt_matches_naive(key_len):
    key = bytes((i * 7 + 3) & 0xFF for i in range(key_len))
    data = bytes((i * 13 + 5) & 0xFF for i in range(40000))   # crosses the 0x7FFF wrap
    assert qmc2.map_decrypt(key, 0, data) == naive_map_decrypt(key, 0, data)


def test_map_decrypt_with_nonzero_offset():
    key = bytes(range(1, 65))
    data = bytes((i * 31 + 11) & 0xFF for i in range(5000))
    assert qmc2.map_decrypt(key, 900, data) == naive_map_decrypt(key, 900, data)


# --------------------------------------------------------------------------
# QMC2 RC4 cipher
# --------------------------------------------------------------------------

def _decrypt_without_cache(key: bytes, data: bytes) -> bytes:
    crypto = qmc2.Qmc2Rc4Crypto(key)
    original = crypto._keystream

    def uncached(discard, length):
        crypto._keystream_cache.clear()
        return original(discard, length)

    crypto._keystream = uncached
    return crypto.decrypt(0, data)


def test_rc4_cache_does_not_change_output():
    key = bytes((i * 17 + 9) & 0xFF for i in range(320))     # > 300 -> RC4 path
    data = bytes((i * 29 + 7) & 0xFF for i in range(20000))
    cached = qmc2.Qmc2Rc4Crypto(key).decrypt(0, data)
    uncached = _decrypt_without_cache(key, data)
    assert cached == uncached


def test_rc4_decrypt_is_an_involution():
    """The cipher is a pure XOR stream, so applying it twice is identity."""
    key = bytes((i * 23 + 1) & 0xFF for i in range(400))
    data = os.urandom(15000)
    once = qmc2.Qmc2Rc4Crypto(key).decrypt(0, data)
    assert once != data
    assert qmc2.Qmc2Rc4Crypto(key).decrypt(0, once) == data


def test_hash_base_matches_reference():
    # A non-growing product stops the loop, so multiplying by 1 breaks out.
    assert qmc2.calc_hash_base(b"\x01") == 1
    assert qmc2.calc_hash_base(b"\x02\x03") == 6
    assert qmc2.calc_hash_base(b"\x01\x00\x05") == 1
    assert qmc2.calc_hash_base(b"\x00\x02\x03") == 6      # zero bytes skipped
