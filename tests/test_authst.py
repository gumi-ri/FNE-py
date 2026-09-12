"""Regression tests for the authst token extraction.

These mirror the Go tests added with the QQ Music 22.x fix. The token is
serialised in several layouts depending on which component owns the buffer,
and stale/templated copies live alongside the real one, so both parsing
and candidate ranking are covered.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fne import qqmusic  # noqa: E402

FAKE = "Q_H_L_5k3NM3AgwJagAlfrjRMarUf6xXN4XtBuu0i5hrXKx2cuaURLMlKfP4Wzr"


def utf16le(s: str) -> bytes:
    return s.encode("utf-16-le")


@pytest.mark.parametrize("text,expected", [
    ('":"' + FAKE + '","ct":"19"', FAKE),          # JSON, compact
    ('" : "' + FAKE + '",\n', FAKE),               # JSON, spaced
    ('" : "",', ""),                               # empty template
    (">" + FAKE + "</authst>", FAKE),              # XML
    ("=" + FAKE + "&cv=2201", FAKE),               # query string
    ("xxxx", ""),                                  # no separator
    ("", ""),                                      # truncated
])
def test_parse_authst_after_key(text, expected):
    assert qqmusic.parse_authst_after_key(text) == expected


@pytest.mark.parametrize("data,expected", [
    (b'{"authst":"' + FAKE.encode() + b'","ct":"19"}', FAKE),
    (b'{ "authst" : "' + FAKE.encode() + b'" }', FAKE),
    (b"<item><authst>" + FAKE.encode() + b"</authst></item>", FAKE),
    (b"cmd=x&authst=" + FAKE.encode() + b"&cv=1", FAKE),
    (b"authst" + bytes([0x12, len(FAKE)]) + FAKE.encode(), FAKE),   # binary blob
    (utf16le('{"authst":"' + FAKE + '"}'), FAKE),
    (utf16le("<authst>" + FAKE + "</authst>"), FAKE),
    (b'{ "authst" : "" }', ""),                    # empty value rejected
    (b'{"ct":"19"}', ""),                          # no key
    (b'{"authst":"abc"}', ""),                     # too short
])
def test_extract_authst_generic(data, expected):
    assert qqmusic.extract_authst_generic(data) == expected


def test_prefers_live_token_over_stale_copy():
    uin = "292670183"
    stale = "A" * 200
    data = (b'{"authst":"' + stale.encode() + b'","qq":"10000"}'
            + b"<authst>" + FAKE.encode() + b'</authst><QQ="' + uin.encode() + b'">')
    assert qqmusic.extract_authst_generic(data, uin) == FAKE


def test_valid_authst_rules():
    assert qqmusic.is_valid_authst(FAKE)
    assert not qqmusic.is_valid_authst("short")
    assert not qqmusic.is_valid_authst("has spaces in it " + FAKE)
    assert qqmusic.is_valid_authst("abc-def_ghi+/=0123456789")


def test_score_prefers_qhl_prefix_and_uin_proximity():
    uin = "12345"
    plain = "B" * 100
    assert (qqmusic.score_authst(FAKE, uin, "") >
            qqmusic.score_authst(plain, uin, ""))
    assert (qqmusic.score_authst(plain, uin, "uin " + uin) >
            qqmusic.score_authst(plain, uin, ""))
    assert qqmusic.score_authst("abc", uin, "") == -1


def test_extract_all_authst_keeps_the_whole_set():
    stale = "A" * 120
    data = (b'{"authst":"' + stale.encode() + b'"}'
            + b"<authst>" + FAKE.encode() + b"</authst>")
    found = [value for value, _ in qqmusic.extract_all_authst(data)]
    assert found[0] == FAKE          # Q_H_L_ prefix outranks a long stale blob
    assert stale in found
    assert len(found) == 2


# --- cache behaviour -------------------------------------------------------

BAD = "B" * 120


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """Isolate the module-level cache state and redirect it to a temp file."""
    path = tmp_path / "authst.json"
    monkeypatch.setattr(qqmusic, "CACHE_PATH", str(path))
    monkeypatch.setattr(qqmusic, "_history", [])
    monkeypatch.setattr(qqmusic, "_history_loaded", False)
    monkeypatch.setattr(qqmusic, "_failed", set())
    monkeypatch.setattr(qqmusic, "_cache", None)
    return path


def _no_scan(monkeypatch):
    def boom(uin):
        raise AssertionError("live scan must not run while the cache is warm")
    monkeypatch.setattr(qqmusic, "collect_authst_candidates", boom)


def test_remember_writes_through_to_disk(cache):
    qqmusic.remember_authst([FAKE])
    assert qqmusic._history == [FAKE]
    assert json.loads(cache.read_text(encoding="utf-8"))["authst"] == [FAKE]


def test_remember_orders_newest_first_and_dedupes(cache):
    qqmusic.remember_authst([BAD])
    qqmusic.remember_authst([FAKE, BAD])
    assert qqmusic._history == [FAKE, BAD]


def test_remember_ignores_invalid_tokens(cache):
    qqmusic.remember_authst(["short", "has spaces " + FAKE, FAKE])
    assert qqmusic._history == [FAKE]


def test_remember_caps_the_history(cache):
    values = ["%s%03d" % ("Q" * 40, i) for i in range(qqmusic.MAX_HISTORY + 5)]
    qqmusic.remember_authst(values)
    assert len(qqmusic._history) == qqmusic.MAX_HISTORY


def test_cached_list_short_circuits_the_scan(cache, monkeypatch):
    _no_scan(monkeypatch)
    qqmusic.remember_authst([FAKE])
    assert qqmusic.authst_candidates("1") == [FAKE]


def test_retired_token_falls_through_to_the_next(cache, monkeypatch):
    _no_scan(monkeypatch)
    qqmusic.remember_authst([FAKE, BAD])          # best-first, order preserved
    assert qqmusic.authst_candidates("1") == [FAKE, BAD]

    qqmusic.mark_authst_invalid(FAKE)
    assert qqmusic.authst_candidates("1") == [BAD]
    assert json.loads(cache.read_text(encoding="utf-8"))["authst"] == [BAD]


def test_scan_is_only_used_when_the_cache_is_empty(cache, monkeypatch):
    calls = []

    def scan(uin):
        calls.append(uin)
        return [FAKE]

    monkeypatch.setattr(qqmusic, "collect_authst_candidates", scan)
    assert qqmusic.authst_candidates("1") == [FAKE]
    assert qqmusic.authst_candidates("1") == [FAKE]
    assert calls == ["1"]                          # second call served by cache


def test_retiring_the_active_token_drops_the_credentials(cache):
    qqmusic.remember_authst([FAKE])
    qqmusic._cache = qqmusic.Credentials(uin="1", authst=FAKE)

    qqmusic.mark_authst_invalid(FAKE)

    assert qqmusic._cache is None
    assert qqmusic._history == []


def test_get_credentials_prefers_the_cached_token(cache, monkeypatch):
    monkeypatch.setattr(qqmusic, "read_uin_from_config", lambda: "292670183")
    _no_scan(monkeypatch)
    qqmusic.remember_authst([FAKE])

    creds = qqmusic.get_credentials()
    assert creds.uin == "292670183"
    assert creds.authst == FAKE


def test_get_credentials_raises_when_nothing_is_usable(cache, monkeypatch):
    monkeypatch.setattr(qqmusic, "read_uin_from_config", lambda: "1")
    monkeypatch.setattr(qqmusic, "collect_authst_candidates", lambda uin: [])

    with pytest.raises(RuntimeError, match="no usable authst"):
        qqmusic.get_credentials()


def test_ekey_for_rotates_past_a_stale_token(cache, monkeypatch):
    monkeypatch.setattr(qqmusic, "read_uin_from_config", lambda: "1")
    qqmusic.remember_authst([FAKE, BAD])

    seen = []

    def fake_fetch(creds, filename, songmid):
        seen.append(creds.authst)
        if creds.authst == FAKE:
            raise qqmusic.AuthError("stale")
        return "EKEY"

    monkeypatch.setattr(qqmusic, "fetch_ekey", fake_fetch)
    assert qqmusic.ekey_for("song.mflac", "media_mid") == "EKEY"
    assert seen == [FAKE, BAD]


def test_ekey_for_gives_up_once_the_tokens_run_out(cache, monkeypatch):
    monkeypatch.setattr(qqmusic, "read_uin_from_config", lambda: "1")
    monkeypatch.setattr(qqmusic, "collect_authst_candidates", lambda uin: [])
    qqmusic.remember_authst([FAKE])

    def always_stale(*args, **kwargs):
        raise qqmusic.AuthError("stale")

    monkeypatch.setattr(qqmusic, "fetch_ekey", always_stale)
    with pytest.raises(RuntimeError, match="no usable authst"):
        qqmusic.ekey_for("song.mflac", "media_mid")


# --- the real API seam -----------------------------------------------------
#
# The rotation tests above stub out ``fetch_ekey`` itself, which means they
# would still pass if the two sides disagreed about the exception type. These
# drive the actual function against a stubbed HTTP layer instead.

@pytest.fixture
def fast_limiter(monkeypatch):
    """Take the rate limiter's sleep out of the test runtime."""
    monkeypatch.setattr(qqmusic, "limiter", qqmusic.RateLimiter(2, 0, 0))


def _api_reply(code, ekey=""):
    return {"req_1": {"code": code,
                      "data": {"midurlinfo": [{"ekey": ekey}]}}}


def _creds():
    return qqmusic.Credentials(uin="1", authst=FAKE)


def test_fetch_ekey_raises_auth_error_on_nonzero_code(monkeypatch, fast_limiter):
    """Rotation only works if this seam raises AuthError, not RuntimeError."""
    monkeypatch.setattr(qqmusic, "_post", lambda url, payload: _api_reply(2000))

    with pytest.raises(qqmusic.AuthError):
        qqmusic.fetch_ekey(_creds(), "song.mflac", "mid")


def test_fetch_ekey_does_not_blame_the_token_for_an_empty_key(
        monkeypatch, fast_limiter):
    """An empty ekey means "no permission", not "stale token"."""
    monkeypatch.setattr(qqmusic, "_post", lambda url, payload: _api_reply(0, ""))

    with pytest.raises(RuntimeError) as excinfo:
        qqmusic.fetch_ekey(_creds(), "song.mflac", "mid")
    assert not isinstance(excinfo.value, qqmusic.AuthError)


def test_fetch_ekey_returns_the_key(monkeypatch, fast_limiter):
    monkeypatch.setattr(qqmusic, "_post",
                        lambda url, payload: _api_reply(0, "EKEY"))
    assert qqmusic.fetch_ekey(_creds(), "song.mflac", "mid") == "EKEY"


def test_ekey_for_rotates_through_the_real_seam(cache, monkeypatch, fast_limiter):
    """A token the API rejects must fall through to the next cached one."""
    monkeypatch.setattr(qqmusic, "read_uin_from_config", lambda: "1")
    qqmusic.remember_authst([FAKE, BAD])

    replies = {FAKE: _api_reply(2000), BAD: _api_reply(0, "EKEY")}
    seen = []

    def fake_post(url, payload):
        token = payload["comm"]["authst"]
        seen.append(token)
        return replies[token]

    monkeypatch.setattr(qqmusic, "_post", fake_post)

    assert qqmusic.ekey_for("song.mflac", "mid") == "EKEY"
    assert seen == [FAKE, BAD]                 # rotated, in priority order
    assert json.loads(cache.read_text(encoding="utf-8"))["authst"] == [BAD]


# --- cache durability ------------------------------------------------------
#
# ``_persist`` claims to be "best effort - never fatal", but nothing checked
# that. The claim matters once this ships as a wheel: the cache sits beside
# the package, and a system-wide install leaves that directory read-only.

def _isolate(monkeypatch, path):
    monkeypatch.setattr(qqmusic, "CACHE_PATH", str(path))
    monkeypatch.setattr(qqmusic, "_history", [])
    monkeypatch.setattr(qqmusic, "_history_loaded", False)
    monkeypatch.setattr(qqmusic, "_failed", set())


def test_unwritable_cache_does_not_break_conversion(tmp_path, monkeypatch):
    """A read-only package directory must degrade, not crash."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    _isolate(monkeypatch, blocker / "authst.json")

    assert qqmusic.remember_authst([FAKE]) == [FAKE]   # no raise
    assert qqmusic.authst_candidates("1") == [FAKE]    # still usable in-process


def test_corrupt_cache_file_is_ignored(tmp_path, monkeypatch):
    """Half-written or hand-edited JSON resets the cache instead of failing."""
    path = tmp_path / "authst.json"
    path.write_text("{ not json at all", encoding="utf-8")
    _isolate(monkeypatch, path)
    monkeypatch.setattr(qqmusic, "collect_authst_candidates", lambda uin: [FAKE])

    assert qqmusic.authst_candidates("1") == [FAKE]
    assert json.loads(path.read_text(encoding="utf-8"))["authst"] == [FAKE]


def test_cache_round_trips_across_processes(tmp_path, monkeypatch):
    """The whole point of the cache: a second run skips the memory scan."""
    path = tmp_path / "authst.json"
    _isolate(monkeypatch, path)
    qqmusic.remember_authst([FAKE, BAD])

    _isolate(monkeypatch, path)          # fresh process, same file
    qqmusic._ensure_loaded()
    assert qqmusic._history == [FAKE, BAD]
