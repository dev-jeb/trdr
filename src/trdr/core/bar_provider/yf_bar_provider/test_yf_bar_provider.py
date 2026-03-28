import pytest
import asyncio
import yfinance as yf

from .yf_bar_provider import YFBarProvider
from ..exceptions import NoBarsForSymbolException, BarProviderException, InsufficientBarsException
from ....test_utils.fake_yf_download import fake_yf_download


def test_that_only_symbols_with_data_are_in_the_data_cache(yf_bar_provider_with_fake_data):
    data_cache_symbols = set(yf_bar_provider_with_fake_data._data_cache.keys())
    assert data_cache_symbols == {"AAPL", "MSFT"}


def test_get_symbols_returns_a_list_of_symbols(yf_bar_provider_with_fake_data):
    expected_symbols = {"AAPL", "MSFT"}
    actual_symbols = yf_bar_provider_with_fake_data.get_symbols()
    assert isinstance(actual_symbols, list)
    # convert to set so order doesn't matter when we compare
    actual_symbols = set(actual_symbols)
    assert actual_symbols == expected_symbols


def test_get_symbols_returns_only_symbols_that_have_bars(yf_bar_provider_with_fake_data):
    expected_symbols = {"AAPL", "MSFT"}
    actual_symbols = set(yf_bar_provider_with_fake_data.get_symbols())
    assert actual_symbols == expected_symbols


def test_gets_bars_throws_exception_when_no_bars_are_available_for_a_symbol(yf_bar_provider_with_fake_data):
    with pytest.raises(NoBarsForSymbolException):
        bars = asyncio.run(yf_bar_provider_with_fake_data.get_bars("ABCDEFG"))


def test_get_bars_throws_exception_when_symbol_is_not_in_data_cache(yf_bar_provider_with_fake_data):
    with pytest.raises(NoBarsForSymbolException):
        bars = asyncio.run(yf_bar_provider_with_fake_data.get_bars("ABCDEFG"))


def test_provider_throws_exception_when_data_source_returns_error(monkeypatch):
    # ensure we raise an exception when the data source returns an error that is not a missing symbol error
    def fake_download_with_error(*args, **kwargs):
        result = fake_yf_download(*args, **kwargs)
        yf.shared._ERRORS = {"ABCDEFG": "RandomYFError()"}
        return result

    with pytest.raises(BarProviderException):
        monkeypatch.setattr(yf, "download", fake_download_with_error)
        bars = asyncio.run(YFBarProvider.create(["ABCDEFG"]))


def test_get_bars_throws_exception_when_lookback_is_greater_than_the_number_of_bars_available(
    yf_bar_provider_with_fake_data,
):
    with pytest.raises(InsufficientBarsException):
        bars = asyncio.run(yf_bar_provider_with_fake_data.get_bars("AAPL", 7))


def test_get_current_bar_throws_exception_when_no_bars_are_available_for_a_symbol(monkeypatch):
    with pytest.raises(NoBarsForSymbolException):
        """
        Any time yf.download() is called, we clear the yf.shared._ERRORS dictionary after we are done inspecting it. Therefore, when we call yf.download() in the get_current_bar() method, we need to set the yf.shared._ERRORS dictionary again to the expected value of the test.
        """
        monkeypatch.setattr(yf, "download", fake_yf_download)
        monkeypatch.setattr(yf.shared, "_ERRORS", {"ABCDEFG": "YFTzMissingError()"})
        yf_bar_provider = asyncio.run(YFBarProvider.create(["ABCDEFG"]))
        # yf.shared._ERRORS is currently {} as we reset it after the yf.download() call that occurs during initialization of the bar provider.
        monkeypatch.setattr(yf.shared, "_ERRORS", {"ABCDEFG": "YFTzMissingError()"})
        bar = asyncio.run(yf_bar_provider.get_current_bar("ABCDEFG"))


def test_get_current_bar_throws_exception_when_data_source_returns_error(monkeypatch):
    with pytest.raises(BarProviderException):
        monkeypatch.setattr(yf, "download", fake_yf_download)
        yf_bar_provider = asyncio.run(YFBarProvider.create(["ABCDEFG"]))
        monkeypatch.setattr(yf.shared, "_ERRORS", {"ABCDEFG": "RandomYFError()"})
        bar = asyncio.run(yf_bar_provider.get_current_bar("ABCDEFG"))
