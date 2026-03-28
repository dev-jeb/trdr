from typing import List, Tuple, Optional
from datetime import timedelta, timezone
import yfinance as yf
from decimal import Decimal
import pandas as pd
from opentelemetry import trace
import logging

from ..exceptions import (
    BarProviderException,
    InsufficientBarsException,
    NoBarsForSymbolException,
    BarConversionException,
)
from ..base_bar_provider import BaseBarProvider
from ..models import Bar, TradingDateTime, Money
from ...shared.models import Timeframe

# Disable yfinance logging
logger = logging.getLogger("yfinance")
logger.disabled = True


class YFBarProvider(BaseBarProvider):
    def __init__(
        self,
        *args,
        **kwargs,
    ):
        """Disabled constructor - use YFBarProvider.create() instead."""
        raise TypeError("Use YFBarProvider.create() instead to create a new bar provider")

    async def _initialize(self, symbols: List[str]) -> None:
        """
        Initialize the YFBarProvider instance.

        This function is called by the T.create() method implemented in the base class.
        Use that method to create a new bar provider.
        """
        with self._tracer.start_as_current_span("YFBarProvider._initialize") as span:
            self._no_data_errors = ["YFTzMissingError", "YFPricesMissingError", "JSONDecodeError", "YFRateLimitError"]
            if not symbols:
                span.set_status(trace.Status(trace.StatusCode.ERROR))
                e = BarProviderException("Symbols must contain at least one symbol")
                span.record_exception(e)
                raise e
            try:
                await self._refresh_data(symbols)
            except Exception as e:
                span.add_event("refresh_data_error")
                span.set_status(trace.Status(trace.StatusCode.ERROR))
                raise
            else:
                span.set_status(trace.Status(trace.StatusCode.OK))

    async def _refresh_data(self, symbols: List[str]) -> None:
        """
        Refresh the stock data for the symbols.

        This function is called by the factory method in the base class upon instantiation
        of a new provider.
        """
        with self._tracer.start_as_current_span("YFBarProvider._refresh_data") as span:
            span.set_attribute("number_of_symbols_requested", len(symbols))
            try:
                symbols_with_data, data = await self._fetch_batch_stock_data(symbols.copy())
                span.set_attribute("number_of_symbols_with_no_data", len(symbols) - len(symbols_with_data))
            except Exception as e:
                span.add_event("data_fetch_error")
                span.set_status(trace.Status(trace.StatusCode.ERROR))
                raise e
            else:
                for symbol in symbols_with_data:
                    try:
                        symbol_data = data.xs(symbol, level=0, axis=1)
                        bars = self._convert_df_to_bars(symbol, symbol_data)
                        self._data_cache[symbol] = bars
                    except BarConversionException as e:
                        continue
                    except Exception as e:
                        span.set_status(trace.Status(trace.StatusCode.ERROR))
                        raise e
                span.set_attribute(
                    "number_of_symbols_with_data_that_failed_to_convert_to_bars",
                    len(symbols_with_data) - len(self._data_cache.keys()),
                )
                span.set_attribute("number_of_symbols_with_data", len(self._data_cache.keys()))
                span.set_status(trace.Status(trace.StatusCode.OK))
                span.add_event("refresh_complete")

    async def _fetch_batch_stock_data(
        self,
        symbols: List[str],
    ) -> Tuple[List[str], pd.DataFrame]:
        """
        Fetch batch stock data from Yahoo Finance.

        Returns:
            pd.DataFrame: DataFrame containing the stock data.
        """
        with self._tracer.start_as_current_span("YFBarProvider._fetch_batch_stock_data") as span:
            end_datetime = TradingDateTime.now().timestamp
            start_datetime = end_datetime - timedelta(days=300)
            span.set_attribute("total_symbols_to_fetch_data_for", len(symbols))
            span.set_attribute("start_datetime", str(start_datetime))
            span.set_attribute("end_datetime", str(end_datetime))
            span.set_attribute("date_range_days", (end_datetime - start_datetime).days)
            span.add_event("begin_data_fetch")
            data = yf.download(
                symbols,
                start=start_datetime,
                end=end_datetime,
                group_by="ticker",
                interval=Timeframe.d1.to_yf_interval(),
            )
            span.add_event("data_fetch_complete")
            if yf.shared._ERRORS:
                """
                If a symbol has one of the following errors associated with it in the yf.shared._ERRORS dictionary, it means that we received no data for that symbol. We should therefore remove it from self._symbols.
                The JSONDecodeError is the manifestation of a rate limit.
                """

                symbols_with_no_data = [
                    symbol
                    for symbol, error in yf.shared._ERRORS.items()
                    if any(no_data_error in error for no_data_error in self._no_data_errors)
                ]
                for symbol in symbols_with_no_data:
                    if symbol in symbols:
                        symbols.remove(symbol)

                # Gather any other errors.
                other_errors = [
                    error
                    for error in yf.shared._ERRORS.values()
                    if all(no_data_error not in error for no_data_error in self._no_data_errors)
                ]
                if other_errors:
                    error_msg = "; ".join(other_errors)
                    span.set_status(trace.Status(trace.StatusCode.ERROR))
                    e = BarProviderException(f"YFinance errors: {error_msg}")
                    span.record_exception(e)
                    raise e
                else:
                    span.set_status(trace.Status(trace.StatusCode.OK))
                    if symbols_with_no_data:
                        span.set_attribute("symbols_with_no_data", symbols_with_no_data)

                """
                Our yf object is long lived across the lifetime of the bar provider. Therefore, we should reset the errors dictionary. We need it clean as we will need to inspect errors produced by calls to yf.download()
                """
                yf.shared._ERRORS = {}

            return symbols, data

    def _convert_df_to_bars(self, symbol: str, df: pd.DataFrame) -> List[Bar]:
        """
        Convert a DataFrame to a list of Bar objects.

        Args:
            df (pd.DataFrame): DataFrame containing the stock data.

        Returns:
            List[Bar]: List of Bar objects.
        """
        with self._tracer.start_as_current_span("YFBarProvider._convert_df_to_bars") as span:
            span.set_attribute("symbol", symbol)

            bars = []
            total_rows = len(df)
            bar_creation_errors = 0

            for date, row in df.iterrows():
                try:
                    utc_timestamp = pd.Timestamp(date).to_pydatetime().replace(tzinfo=timezone.utc)
                    bar = Bar(
                        trading_datetime=TradingDateTime.from_utc(utc_timestamp),
                        open=Money(amount=Decimal(float(row["Open"]))),
                        high=Money(amount=Decimal(float(row["High"]))),
                        low=Money(amount=Decimal(float(row["Low"]))),
                        close=Money(amount=Decimal(float(row["Close"]))),
                        volume=int(row["Volume"]),
                    )
                    bars.append(bar)
                except Exception as lower_e:
                    bar_creation_errors += 1
                    if bar_creation_errors / total_rows > 0.05:
                        span.set_status(trace.Status(trace.StatusCode.ERROR))
                        e = BarConversionException(
                            f"failed to convert {bar_creation_errors} out of {total_rows} rows to Bars"
                        )
                        span.set_attribute("exception.cause", lower_e)
                        span.record_exception(e)
                        raise e from lower_e
            span.set_attribute("bars_created", len(bars))
            span.set_attribute("bar_creation_errors", bar_creation_errors)
            span.set_status(trace.Status(trace.StatusCode.OK))

            return bars

    def get_symbols(self) -> List[str]:
        return list(self._data_cache.keys())

    async def get_current_bar(self, symbol: str) -> Bar:
        with self._tracer.start_as_current_span("YFBarProvider.get_current_bar") as span:
            span.set_attribute("symbol", symbol)
            span.add_event("begin_current_bar_data_fetch")
            data = yf.download(
                symbol,
                period=Timeframe.d1.to_yf_interval(),
                interval=Timeframe.m15.to_yf_interval(),
                group_by="ticker",
            )
            span.add_event("current_bar_data_fetch_complete")
            if yf.shared._ERRORS:
                error = yf.shared._ERRORS.get(symbol, None)
                if not error or not any(no_data_error in error for no_data_error in self._no_data_errors):
                    """
                    If we receive an error not associated with the symbol of interest, we should raise an exception.
                    """
                    # Gather any other errors.
                    errors = [error for error in yf.shared._ERRORS.values()]
                    error_msg = "; ".join(errors)
                    span.set_status(trace.Status(trace.StatusCode.ERROR))
                    e = BarProviderException(f"Received an error not related to no data errors: {error_msg}")
                    span.record_exception(e)
                    raise e
                if any(no_data_error in error for no_data_error in self._no_data_errors):
                    """
                    If we didn't receive any data for the symbol of interest we can't construct the current bar.
                    """
                    span.set_status(trace.Status(trace.StatusCode.ERROR))
                    e = NoBarsForSymbolException(f"{symbol}")
                    span.record_exception(e)
                    raise e
                """
                Our yf object is long lived across the lifetime of the bar provider. Therefore, we should reset the errors dictionary. We need it clean as we will need to inspect errors produced by calls to yf.download()
                """
                yf.shared._ERRORS = {}

            try:
                symbol_data = data.xs(symbol, level=0, axis=1)
                bars = self._convert_df_to_bars(symbol, symbol_data)
                most_recent_bar = bars[-1]
                most_recent_bar.trading_datetime = TradingDateTime.now()
            except Exception as e:
                span.set_status(trace.Status(trace.StatusCode.ERROR))
                raise e
            else:
                span.set_status(trace.Status(trace.StatusCode.OK))
                return most_recent_bar

    async def get_bars(
        self,
        symbol: str,
        lookback: Optional[int] = None,
    ) -> List[Bar]:
        """
        Get the bars for a symbol with a specified lookback period.

        Args:
            symbol (str): The symbol to get the bars for.
            lookback (int): The number of bars to look back.

        Returns:
            List[Bar]: List of bars for the symbol.

        Raises:
            NoBarsForSymbolException: If we didn't receive any data for the symbol of interest.
            InsufficientBarsException: If the number of bars requested is greater than the number of bars available.
        """
        with self._tracer.start_as_current_span("YFBarProvider.get_bars") as span:
            span.set_attribute("symbol", symbol)
            if lookback is not None:
                span.set_attribute("requested_lookback", lookback)
            if not self._data_cache.get(symbol, None):
                span.add_event("no_data_found_for_symbol")
                span.set_status(trace.Status(trace.StatusCode.ERROR))
                e = NoBarsForSymbolException(symbol)
                span.record_exception(e)
                raise e
            if lookback is None:
                lookback = len(self._data_cache[symbol])
            if len(self._data_cache[symbol]) < lookback:
                span.set_attribute("lookback_available_for_symbol", len(self._data_cache[symbol]))
                span.add_event("lookback_too_large_for_symbol")
                span.set_status(trace.Status(trace.StatusCode.ERROR))
                e = InsufficientBarsException(
                    f"Only {len(self._data_cache[symbol])} bars available for symbol: {symbol}"
                )
                span.record_exception(e)
                raise e
            try:
                bars = self._data_cache[symbol][-lookback:]
            except Exception as e:
                span.set_status(trace.Status(trace.StatusCode.ERROR))
                span.record_exception(e)
                raise
            else:
                span.set_status(trace.Status(trace.StatusCode.OK))
                return bars
