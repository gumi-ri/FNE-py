"""QQ Music credentials and API access.

This is where the original bug lived (see ``extract_authst_candidates``):
the token has to be recovered from the running client because the cookie
files are encrypted. The Go version only recognised one serialisation of
it, which is why conversion failed on QQ Music 22.x.
"""

from __future__ import annotations

import base64
import json
import os
import random
import re
import threading
import time
from dataclasses import dataclass
from typing import Iterable, Iterator

import requests

from . import util, win32

MUSICU_URL = "https://u.y.qq.com/cgi-bin/musicu.fcg"
COVER_URL = "https://y.gtimg.cn/music/photo_new/T002R800x800M000{}.jpg"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

QQMUSIC_EXE = "QQMusic.exe"

_AUTHST_KEY_ASCII = b"authst"
_AUTHST_KEY_UTF16 = "authst".encode("utf-16-le")

_WINDOW_ASCII = 4096
_WINDOW_UTF16 = 8192

_VALID_RE = re.compile(r"^[A-Za-z0-9+/=_-]+$")


# --------------------------------------------------------------------------
# rate limiting (mirrors apiConfig in the Go version)
# --------------------------------------------------------------------------

class RateLimiter:
    def __init__(self, concurrent: int = 3, delay_min: int = 200,
                 delay_max: int = 800) -> None:
        self.concurrent = max(1, concurrent)
        self.delay_min = max(0, delay_min)
        self.delay_max = max(self.delay_min, delay_max)
        self._sem = threading.Semaphore(self.concurrent)

    def __enter__(self) -> "RateLimiter":
        self._sem.acquire()
        delay = self.delay_min
        if self.delay_max > self.delay_min:
            delay += random.randint(0, self.delay_max - self.delay_min)
        time.sleep(delay / 1000.0)
        return self

    def __exit__(self, *exc) -> None:
        self._sem.release()


limiter = RateLimiter()


def configure(concurrent: int, delay_min: int, delay_max: int) -> None:
    global limiter
    limiter = RateLimiter(concurrent, delay_min, delay_max)


# --------------------------------------------------------------------------
# authst extraction
# --------------------------------------------------------------------------

def is_valid_authst(value: str) -> bool:
    return len(value) >= 20 and bool(_VALID_RE.match(value))


def score_authst(value: str, uin: str, window: str) -> int:
    """Rank a candidate token.

    QQ Music keeps several authst-shaped blobs alive at once, so the score
    encodes what distinguishes the live login token from stale or
    templated copies: the ``Q_H_L_`` prefix, co-location with the logged in
    UIN, and length. ``-1`` marks an unusable candidate.
    """
    if not is_valid_authst(value):
        return -1
    score = len(value)
    if value.startswith("Q_H_L_"):
        score += 100000
    if uin and uin in window:
        score += 5000
    return score


def _iter_hits(data: bytes, pattern: bytes) -> Iterator[int]:
    start = 0
    while True:
        i = data.find(pattern, start)
        if i < 0:
            return
        yield i
        start = i + len(pattern)


def _parse_value(s: str) -> str:
    """Read the value following the ':' of a key/value pair."""
    i = 0
    while i < len(s) and s[i] in " \t":
        i += 1
    if i >= len(s):
        return ""
    if s[i] == '"':
        end = s.find('"', i + 1)
        return s[i + 1:end] if end >= 0 else ""
    # ``_find_any`` returns -1 when there is no terminator at all, but 0 is a
    # legitimate hit (the value is empty), so the test must be >= 0.
    end = _find_any(s, ",&\r\n }", i)
    return s[i:end] if end >= 0 else s[i:]


def _find_any(s: str, chars: str, start: int) -> int:
    for i in range(start, len(s)):
        if s[i] in chars:
            return i
    return -1


def parse_authst_after_key(s: str) -> str:
    """Extract the token from the text immediately following an ``authst`` key.

    QQ Music stores the token in several layouts depending on which
    component owns the buffer:

        "authst":"<v>"        JSON, compact
        "authst" : "<v>"      JSON, spaced
        <authst><v></authst>  XML
        authst=<v>&           query string
        authst<len><v>        length-prefixed binary blob
    """
    i = 0
    while i < len(s) and s[i] in " \t\r\n":
        i += 1
    if i >= len(s):
        return ""

    ch = s[i]
    if ch == '"':
        j = i + 1
        while j < len(s) and s[j] in " \t":
            j += 1
        if j < len(s) and s[j] == ":":
            return _parse_value(s[j + 1:])
    elif ch == ":":
        return _parse_value(s[i + 1:])
    elif ch == ">":
        end = s.find("<", i + 1)
        return s[i + 1:end] if end > 0 else ""
    elif ch == "=":
        end = _find_any(s, "&\r\n \"", i + 1)
        return s[i + 1:end] if end > 0 else ""
    else:
        # Length-prefixed blob: skip the framing bytes (up to two) then
        # read the length byte in front of the token.
        for d in range(3):
            if i + d >= len(s):
                break
            n = s[i + d]
            if not isinstance(n, int):
                n = ord(n)
            if n >= 20 and i + d + 1 + n <= len(s):
                return s[i + d + 1:i + d + 1 + n]
    return ""


def extract_all_authst(data: bytes, uin: str = "") -> list[tuple[str, int]]:
    """Every distinct ``authst`` token in *data*, best score first.

    The client keeps several of these alive at once, so a scan has to keep
    the whole set: when one turns out to be expired the caller falls through
    to the next rather than re-reading process memory from scratch.
    """
    scores: dict[str, int] = {}

    def consider(value: str, window: str) -> None:
        s = score_authst(value, uin, window)
        if s > scores.get(value, -1):
            scores[value] = s

    for pos in _iter_hits(data, _AUTHST_KEY_ASCII):
        end = min(pos + _WINDOW_ASCII, len(data))
        window = data[pos:end].decode("latin-1")
        consider(parse_authst_after_key(window[len(_AUTHST_KEY_ASCII):]), window)

    for pos in _iter_hits(data, _AUTHST_KEY_UTF16):
        end = min(pos + _WINDOW_UTF16, len(data))
        if (end - pos) % 2:
            end -= 1
        window = data[pos:end].decode("utf-16-le", errors="ignore")
        if window.startswith("authst"):
            consider(parse_authst_after_key(window[6:]), window)

    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


def extract_authst_candidates(data: bytes, uin: str = "") -> tuple[str, int]:
    """Scan *data* for every ``authst`` token and return the best one."""
    found = extract_all_authst(data, uin)
    return found[0] if found else ("", 0)


def extract_authst_generic(data: bytes, uin: str = "") -> str:
    return extract_authst_candidates(data, uin)[0]


def extract_authst_from_json(data: bytes) -> str:
    """Legacy compact-JSON lookup, kept for the cookie path."""
    for marker in (b'"authst":"', b'"authst": "'):
        idx = data.find(marker)
        if idx >= 0:
            start = idx + len(marker)
            end = data.find(b'"', start)
            if end - start >= 10:
                value = data[start:end].decode("latin-1")
                if is_valid_authst(value):
                    return value
    return ""


def find_best_authst(data: bytes) -> str:
    """Longest run of base64-looking characters in *data*."""
    best: list[int] = []
    current: list[int] = []
    for b in data:
        if (65 <= b <= 90 or 97 <= b <= 122 or 48 <= b <= 57
                or b in (43, 47, 61)):
            current.append(b)
        else:
            if len(current) > len(best) and len(current) >= 30:
                best = current
            current = []
    if len(current) > len(best) and len(current) >= 30:
        best = current
    value = bytes(best).decode("latin-1")
    return value if is_valid_authst(value) else ""


# --------------------------------------------------------------------------
# credentials
# --------------------------------------------------------------------------

@dataclass
class Credentials:
    uin: str
    authst: str


def read_uin_from_config() -> str:
    """Read the logged-in UIN from the client's service config file."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError(
            "APPDATA is not set - cannot locate the QQ Music config file")
    path = os.path.join(appdata, "Tencent", "QQMusic", "QQMusicServiceConfig.ini")
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            lines = fh.readlines()
    except OSError as exc:
        raise RuntimeError(
            f"could not read the QQ Music config at {path} "
            f"({exc.strerror or exc}). Start QQ Music, log in once, then retry."
        ) from exc
    for line in lines:
        line = line.strip()
        if line.upper().startswith("UIN="):
            uin = line[4:].strip()
            if uin and uin != "0":
                return uin
    raise RuntimeError(f"no logged-in UIN found in {path}")


def read_authst_from_cookie_files(uin: str) -> str:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA environment variable not found")
    base = os.path.join(appdata, "Tencent", "QQMusic")
    for name in ("SetCookie.dat", "_SetCookie.dat"):
        path = os.path.join(base, name)
        if not os.path.exists(path):
            continue
        with open(path, "rb") as fh:
            data = fh.read()
        for extractor in (extract_authst_from_json,
                          lambda d: extract_authst_generic(d, uin),
                          find_best_authst):
            value = extractor(data)
            if value:
                return value
    raise RuntimeError("authst not found in cookie files")


def read_authst_from_process_memory(uin: str) -> str:
    pids = win32.find_pids_by_name(QQMUSIC_EXE)
    if not pids:
        raise RuntimeError(f"{QQMUSIC_EXE} not found - please start QQ Music")
    best_value, best_score = "", 0
    for pid in pids:
        for chunk in win32.iter_committed_regions(pid):
            value, score = extract_authst_candidates(chunk, uin)
            if score > best_score:
                best_value, best_score = value, score
    if not best_value:
        raise RuntimeError("authst not found in QQ Music process memory - "
                           "ensure QQ Music is running and logged in")
    return best_value


# --------------------------------------------------------------------------
# authst cache: an ordered list of every token we have ever read
# --------------------------------------------------------------------------

# The cache lives in ``.fne`` beside the tool rather than in the user's home
# directory. ``app_dir()`` resolves that to the executable's own folder in a
# frozen build, because a single-file bundle's package directory is a temp
# directory deleted on exit - a cache written there would not survive a run.
_CACHE_DIR = os.path.join(util.app_dir(), ".fne")
CACHE_PATH = os.path.join(_CACHE_DIR, "authst_cache.json")
MAX_HISTORY = 16
MAX_AUTH_ATTEMPTS = 12

# Most recently read first. Persisted so a later run can skip the (slow and
# fragile) process-memory scan entirely.
_history: list[str] = []
_history_loaded = False
_failed: set[str] = set()
_history_lock = threading.Lock()


class AuthError(RuntimeError):
    """The API rejected the current authst (expired, or the wrong account)."""


def _ensure_loaded() -> None:
    """Read the on-disk cache once per process."""
    global _history, _history_loaded
    if _history_loaded:
        return
    _history_loaded = True
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as fh:
            stored = json.load(fh)
    except (OSError, ValueError):
        return
    values = stored.get("authst", []) if isinstance(stored, dict) else stored
    _history = [v for v in values if isinstance(v, str) and is_valid_authst(v)]


def _persist() -> None:
    """Write the cache back atomically. Best effort - never fatal."""
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "authst": _history}, fh, indent=2)
        os.replace(tmp, CACHE_PATH)
    except OSError:
        pass


def remember_authst(values: Iterable[str]) -> list[str]:
    """Record freshly read tokens at the front of the cache, order preserved.

    *values* arrives best-first (from a scan or from the caller), so the block
    is spliced in as a whole rather than inserted one at a time.
    """
    with _history_lock:
        _ensure_loaded()
        fresh = [v for v in values if is_valid_authst(v)]
        for value in fresh:
            if value in _history:
                _history.remove(value)
        _history[:0] = fresh
        del _history[MAX_HISTORY:]
        _persist()
        return list(_history)


def mark_authst_invalid(value: str) -> None:
    """Retire a token: drop it from the cache and forget the active credentials."""
    global _cache
    with _history_lock:
        _ensure_loaded()
        _failed.add(value)
        try:
            _history.remove(value)
        except ValueError:
            pass
        _persist()
    with _cache_lock:
        if _cache is not None and _cache.authst == value:
            _cache = None


def collect_authst_candidates(uin: str) -> list[str]:
    """Scan cookie files and process memory for *every* authst token.

    Ordered best-first. The original implementation only kept the winner;
    keeping the whole set is what lets an expired token fall through to the
    next one instead of re-scanning from scratch.
    """
    scores: dict[str, int] = {}

    def absorb(data: bytes) -> None:
        for value, score in extract_all_authst(data, uin):
            if score > scores.get(value, -1):
                scores[value] = score

    appdata = os.environ.get("APPDATA")
    if appdata:
        base = os.path.join(appdata, "Tencent", "QQMusic")
        for name in ("SetCookie.dat", "_SetCookie.dat"):
            try:
                with open(os.path.join(base, name), "rb") as fh:
                    absorb(fh.read())
            except OSError:
                continue

    for pid in win32.find_pids_by_name(QQMUSIC_EXE):
        try:
            for chunk in win32.iter_committed_regions(pid):
                absorb(chunk)
        except OSError:
            continue

    return [v for v, _ in sorted(scores.items(), key=lambda kv: kv[1],
                                 reverse=True)]


def authst_candidates(uin: str) -> list[str]:
    """Ordered usable tokens: cached history first, live scan as a fallback."""
    with _history_lock:
        _ensure_loaded()
        cached = [v for v in _history if v not in _failed]
    if cached:
        return cached

    # Cache empty or fully retired - pay for a fresh scan.
    scanned = [v for v in collect_authst_candidates(uin) if v not in _failed]
    if scanned:
        remember_authst(scanned)
    return scanned


_cache: Credentials | None = None
_cache_lock = threading.Lock()


def get_credentials(force_refresh: bool = False) -> Credentials:
    """Resolve the UIN/authst pair, preferring the cached token list."""
    global _cache
    with _cache_lock:
        if _cache is not None and not force_refresh:
            return _cache
        uin = read_uin_from_config()
        candidates = authst_candidates(uin)
        if not candidates:
            raise RuntimeError(
                "no usable authst found - make sure QQ Music is running and "
                f"logged in, or clear {CACHE_PATH}")
        _cache = Credentials(uin=uin, authst=candidates[0])
        return _cache


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

_thread_local = threading.local()


def _session() -> requests.Session:
    """Return this thread's HTTP session.

    ``requests.Session`` is not documented as thread safe, and the converter
    runs several workers in parallel, so each thread gets its own.
    """
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": UA,
                                "Referer": "https://y.qq.com/"})
        _thread_local.session = session
    return session


def _post(url: str, payload: dict) -> dict:
    resp = _session().post(url, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_ekey(creds: Credentials, filename: str, songmid: str) -> str:
    with limiter:
        payload = {
            "comm": {"authst": creds.authst, "ct": "19", "cv": "1859",
                     "uin": creds.uin, "tmeLoginType": "3"},
            "req_1": {
                "module": "music.vkey.GetEVkey",
                "method": "CgiGetEVkey",
                "param": {"filename": [filename], "guid": "10000",
                          "songmid": [songmid], "songtype": [1],
                          "uin": creds.uin, "loginflag": 1,
                          "platform": "27", "ctx": 1},
            },
        }
        result = _post(MUSICU_URL, payload)

    req_1 = result.get("req_1") or {}
    code = req_1.get("code")
    if code is None or code != 0:
        # A non-zero code from this endpoint almost always means the token was
        # rejected. Raising AuthError (not RuntimeError) is what lets
        # ``ekey_for`` rotate to the next cached token instead of giving up.
        raise AuthError(f"QQ Music API returned code {code}")
    midurl = ((req_1.get("data") or {}).get("midurlinfo") or [])
    if not midurl:
        raise RuntimeError("missing midurlinfo in API response")
    ekey = midurl[0].get("ekey") or ""
    if not ekey:
        raise RuntimeError("empty ekey in API response")
    return ekey


def ekey_for(filename: str, songmid: str) -> str:
    """Fetch an ekey, rotating through known tokens when one is rejected.

    Tokens expire and the machine usually still holds a handful of old ones.
    Walking the cache is cheaper - and much less annoying - than making the
    user log in again, and it self-heals the cache as a side effect.
    """
    last_error: Exception | None = None
    for _ in range(MAX_AUTH_ATTEMPTS):
        creds = get_credentials()
        try:
            return fetch_ekey(creds, filename, songmid)
        except AuthError as exc:
            last_error = exc
            mark_authst_invalid(creds.authst)
    if last_error is not None:
        raise last_error
    raise RuntimeError("could not obtain an ekey")


_album_mid_cache: dict[str, str] = {}
_cover_cache: dict[str, bytes] = {}
_cover_lock = threading.Lock()


def fetch_album_cover(song_mid: str) -> bytes:
    """Look up (and memoise) the front cover for a song.

    The lock guards only the dictionaries. Network I/O stays outside it:
    holding it across a request would serialise every worker behind the rate
    limiter, making covers the slowest part of a batch.
    """
    with _cover_lock:
        album_mid = _album_mid_cache.get(song_mid)
        if album_mid is not None and album_mid in _cover_cache:
            return _cover_cache[album_mid]

    if album_mid is None:
        album_mid = _lookup_album_mid(song_mid)
        with _cover_lock:
            _album_mid_cache[song_mid] = album_mid

    with _cover_lock:
        cached = _cover_cache.get(album_mid)
    if cached is not None:
        return cached

    cover = _download_cover(album_mid)
    with _cover_lock:
        _cover_cache[album_mid] = cover
    return cover


def _lookup_album_mid(song_mid: str) -> str:
    payload = {
        "comm": {"ct": 19, "cv": 1859},
        "songinfo": {
            "method": "get_song_detail_yqq",
            "module": "music.pf_song_detail_svr",
            "param": {"song_mid": song_mid, "song_type": 0},
        },
    }
    result = _post(MUSICU_URL, payload)
    album_mid = (result.get("songinfo", {}).get("data", {})
                 .get("track_info", {}).get("album", {}).get("mid", ""))
    if not album_mid:
        raise RuntimeError(f"album_mid not found for song {song_mid}")
    return album_mid


def _download_cover(album_mid: str) -> bytes:
    with limiter:
        resp = _session().get(COVER_URL.format(album_mid), timeout=30)
        resp.raise_for_status()
        return resp.content


def parse_ekey(ekey: str) -> bytes:
    """Decode an ekey into the raw QMC2 decryption key."""
    from . import tea

    decoded = base64.b64decode(ekey.rstrip("\x00"))
    if len(decoded) < 8:
        raise ValueError(f"decoded key too short ({len(decoded)} bytes)")

    prefix = b"QQMusic EncV2,Key:"
    if decoded.startswith(prefix):
        blob = decoded[len(prefix):]
        stage1 = tea.tc_tea_decrypt(blob, b"386ZJY!@#*$%^&)(")
        stage2 = tea.tc_tea_decrypt(stage1, b"**#!(#$%&^a1cZ,T")
        decoded = base64.b64decode(stage2)

    if len(decoded) < 8:
        raise ValueError("decoded key too short after EncV2 processing")

    header = decoded[:8]
    body = decoded[8:]
    if not body:
        return header

    # The 8-byte header seeds the TEA key; the plaintext is header + body.
    tea_key = tea.derive_tea_key(header)
    try:
        return header + tea.tc_tea_decrypt(body, tea_key)
    except Exception:
        # TC-TEA failed - the key is stored raw, which is also valid.
        return decoded
