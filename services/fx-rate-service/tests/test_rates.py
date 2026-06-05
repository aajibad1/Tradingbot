"""Rate resolution: fallback, NGN multi-rate, caching, conversion."""

from datetime import datetime

import pytest

import rates as R
from models import NgnRates, RatesSnapshot


class _Stub:
    def __init__(self, name: str, data: dict | None = None, fail: bool = False):
        self.name = name
        self.data = data or {}
        self.fail = fail

    def fetch(self, symbols=None) -> dict:
        if self.fail:
            raise RuntimeError("provider down")
        return dict(self.data)


class _FakeRedis:
    def __init__(self):
        self.kv: dict[str, str] = {}

    def get(self, k):
        return self.kv.get(k)

    def set(self, k, v):
        self.kv[k] = str(v)

    def setex(self, k, _ttl, v):
        self.kv[k] = str(v)


_FULL = {"ZAR": 18.5, "NGN": 1600.0, "KES": 129.0}
_FB = {"ZAR": 18.6, "NGN": 1750.0, "KES": 130.0}


def test_fetch_composes_ngn_multirate() -> None:
    snap = R.fetch_rates(_Stub("oer", _FULL), _Stub("erh", _FB))
    assert snap.source == "oer"
    assert snap.ngn.official == 1600.0   # primary = CBN official
    assert snap.ngn.parallel == 1750.0   # fallback = parallel
    assert snap.rates["ZAR"] == 18.5


def test_primary_down_falls_back() -> None:
    snap = R.fetch_rates(_Stub("oer", fail=True), _Stub("erh", _FB))
    assert snap.source == "erh"
    assert snap.rates["NGN"] == 1750.0


def test_all_providers_down_raises() -> None:
    with pytest.raises(RuntimeError):
        R.fetch_rates(_Stub("a", fail=True), _Stub("b", fail=True))


def test_p2p_rate_read_from_redis() -> None:
    r = _FakeRedis()
    r.set(R.NGN_P2P_KEY, "1850.0")
    snap = R.fetch_rates(_Stub("oer", _FULL), _Stub("erh", _FB), r)
    assert snap.ngn.p2p == 1850.0


def test_cache_serves_stale_within_ttl() -> None:
    r = _FakeRedis()
    primary, fallback = _Stub("oer", _FULL), _Stub("erh", _FB)
    R.get_rates(primary, fallback, r)
    primary.data["ZAR"] = 99.0   # provider moves...
    again = R.get_rates(primary, fallback, r)
    assert again.rates["ZAR"] == 18.5  # ...but cache served the prior snapshot


def _snap() -> RatesSnapshot:
    return RatesSnapshot(rates={"ZAR": 18.5, "KES": 129.0},
                         ngn=NgnRates(official=1600.0, parallel=1750.0, p2p=1850.0),
                         source="x", fetched_at=datetime(2026, 1, 1))


def test_convert_currencies_and_ngn_rate_types() -> None:
    s = _snap()
    assert R.convert(s, 100.0, "USD").amount_local == 100.0
    assert R.convert(s, 10.0, "ZAR").amount_local == 185.0
    assert R.convert(s, 1.0, "NGN", "official").amount_local == 1600.0
    assert R.convert(s, 1.0, "NGN", "parallel").amount_local == 1750.0
    assert R.convert(s, 1.0, "NGN", "p2p").amount_local == 1850.0
    with pytest.raises(ValueError):
        R.convert(s, 1.0, "GBP")
