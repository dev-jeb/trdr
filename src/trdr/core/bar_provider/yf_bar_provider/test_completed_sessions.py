"""YFBarProvider(completed_sessions_only=True): the live view must match a daily backtest's."""

import asyncio
from datetime import date, datetime

import pandas as pd
import pytest
import yfinance as yf

from . import yf_bar_provider as module
from .yf_bar_provider import MARKET_TZ, YFBarProvider
from ..exceptions import NoBarsForSymbolException
from ....test_utils.fake_yf_download import fake_yf_download

# fake_yf_download returns daily bars for Mon 2026-03-23 .. Fri 2026-03-27.


def _provider(monkeypatch, now: datetime) -> YFBarProvider:
    monkeypatch.setattr(yf, "download", fake_yf_download)
    monkeypatch.setattr(yf.shared, "_ERRORS", {"ABCDEFG": "YFTzMissingError()", "AMZN": "JSONDecodeError()"})
    monkeypatch.setattr(module, "_market_now", lambda: now)
    return asyncio.run(YFBarProvider.create(["AAPL", "MSFT"], completed_sessions_only=True))


def _dates(bars) -> list[date]:
    return [b.trading_datetime.trading_date for b in bars]


def test_during_the_session_todays_partial_bar_is_ignored(monkeypatch):
    provider = _provider(monkeypatch, datetime(2026, 3, 27, 9, 35, tzinfo=MARKET_TZ))

    current = asyncio.run(provider.get_current_bar("AAPL"))
    history = asyncio.run(provider.get_bars("AAPL"))

    assert current.trading_datetime.trading_date == date(2026, 3, 26)  # yesterday's full session
    assert _dates(history) == [date(2026, 3, 23), date(2026, 3, 24), date(2026, 3, 25)]


def test_next_morning_uses_the_previous_session_as_current(monkeypatch):
    provider = _provider(monkeypatch, datetime(2026, 3, 30, 9, 35, tzinfo=MARKET_TZ))

    current = asyncio.run(provider.get_current_bar("AAPL"))
    history = asyncio.run(provider.get_bars("AAPL"))

    assert current.trading_datetime.trading_date == date(2026, 3, 27)
    assert current.volume == 1400  # the full day's volume, not a partial session
    assert _dates(history)[-1] == date(2026, 3, 26)


def test_after_the_close_settles_todays_bar_counts(monkeypatch):
    provider = _provider(monkeypatch, datetime(2026, 3, 27, 16, 45, tzinfo=MARKET_TZ))
    assert asyncio.run(provider.get_current_bar("AAPL")).trading_datetime.trading_date == date(2026, 3, 27)


def test_current_bar_needs_no_intraday_download(monkeypatch):
    provider = _provider(monkeypatch, datetime(2026, 3, 30, 9, 35, tzinfo=MARKET_TZ))

    def no_download(*args, **kwargs):
        raise AssertionError("completed-sessions mode must not fetch intraday data")

    monkeypatch.setattr(yf, "download", no_download)
    asyncio.run(provider.get_current_bar("MSFT"))


def test_unknown_symbol_has_no_current_bar(monkeypatch):
    provider = _provider(monkeypatch, datetime(2026, 3, 30, 9, 35, tzinfo=MARKET_TZ))
    with pytest.raises(NoBarsForSymbolException):
        asyncio.run(provider.get_current_bar("ABCDEFG"))


def test_symbol_missing_the_latest_session_is_dropped(monkeypatch):
    """Yahoo can publish a just-closed day with NaN prices; that row is skipped in conversion,
    which would leave the symbol a session behind the rest."""

    def download_with_nan_last_row_for_msft(*args, **kwargs):
        # 30 sessions, so one bad row stays under the 5% conversion-failure threshold and only the
        # staleness check can remove MSFT.
        dates = pd.bdate_range(end=date(2026, 3, 27), periods=30)
        cols = {(sym, f): [100.0 + i for i in range(30)] for sym in ("AAPL", "MSFT") for f in ("Open", "High", "Low", "Close")}
        cols.update({(sym, "Volume"): [1000] * 30 for sym in ("AAPL", "MSFT")})
        df = pd.DataFrame(cols, index=dates)
        df.loc[df.index[-1], [("MSFT", f) for f in ("Open", "High", "Low", "Close")]] = float("nan")
        return df

    monkeypatch.setattr(yf, "download", download_with_nan_last_row_for_msft)
    monkeypatch.setattr(yf.shared, "_ERRORS", {})
    monkeypatch.setattr(module, "_market_now", lambda: datetime(2026, 3, 30, 9, 35, tzinfo=MARKET_TZ))

    provider = asyncio.run(YFBarProvider.create(["AAPL", "MSFT"], completed_sessions_only=True))

    assert provider.get_symbols() == ["AAPL"]
    assert asyncio.run(provider.get_current_bar("AAPL")).trading_datetime.trading_date == date(2026, 3, 27)
    with pytest.raises(NoBarsForSymbolException):
        asyncio.run(provider.get_current_bar("MSFT"))
