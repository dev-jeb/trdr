from typing import List, Tuple, Optional
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
import asyncio
import yfinance as yf
import yfinance.shared  # noqa: F401  yfinance>=1.x no longer auto-binds yf.shared; import it so yf.shared._ERRORS resolves
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

MARKET_TZ = ZoneInfo("America/New_York")
# A day's daily bar is treated as final this long after the 16:00 ET close.
SESSION_SETTLED_AT = time(16, 30)
# Daily history to fetch: ~275 trading days, enough for MA200 plus crossovers' 1-bar offset.
HISTORY_CALENDAR_DAYS = 400


def _market_now() -> datetime:
    """Current time in the exchange's timezone (a function so tests can pin the clock)."""
    return datetime.now(MARKET_TZ)


class YFBarProvider(BaseBarProvider):
    _completed_sessions_only = False

    def __init__(
        self,
        *args,
        **kwargs,
    ):
        """Disabled constructor - use YFBarProvider.create() instead."""
        raise TypeError("Use YFBarProvider.create() instead to create a new bar provider")

    @classmethod
    async def create(
        cls,
        symbols: List[str],
        tracer: Optional[trace.Tracer] = trace.NoOpTracer(),
        completed_sessions_only: bool = False,
    ) -> "YFBarProvider":
        """Create and load a Yahoo Finance bar provider.

        Args:
            symbols: Tickers to load.
            tracer: OpenTelemetry tracer.
            completed_sessions_only: Build every value from finished daily sessions, which is
                what a daily backtest sees when it decides on a session's close and fills at the
                next open. The latest completed session becomes the current bar (CURRENT_PRICE is
                its close, CURRENT_VOLUME its full-day volume) and history ends the session
                before it. Today's in-progress session is ignored and no intraday data is
                fetched. Default False keeps the intraday "session so far" current bar.
        """
        self = cls.__new__(cls)
        BaseBarProvider.__init__(self, tracer)
        self._completed_sessions_only = completed_sessions_only
        self._current_bars = {}
        with self._tracer.start_as_current_span("BaseBarProvider.create") as span:
            span.set_attribute("completed_sessions_only", completed_sessions_only)
            try:
                await self._initialize(symbols)
            except Exception as e:
                span.record_exception(e)
                span.set_status(trace.StatusCode.ERROR)
                raise
            else:
                span.set_status(trace.StatusCode.OK)
        return self

    @staticmethod
    def _completed_session_bars(bars: List[Bar], now: datetime) -> List[Bar]:
        """Drop the bar for today's session unless it has settled (see SESSION_SETTLED_AT)."""
        today = now.date()
        settled = now.time() >= SESSION_SETTLED_AT
        return [
            bar
            for bar in bars
            if bar.trading_datetime.trading_date < today or (bar.trading_datetime.trading_date == today and settled)
        ]

    async def _initialize(self, symbols: List[str]) -> None:
        """
        Initialize the YFBarProvider instance.

        This function is called by the T.create() method implemented in the base class.
        Use that method to create a new bar provider.
        """
        with self._tracer.start_as_current_span("YFBarProvider._initialize") as span:
            self._no_data_errors = ["YFTzMissingError", "YFPricesMissingError", "JSONDecodeError"]
            self._rate_limit_errors = ["YFRateLimitError"]
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
                now = _market_now()
                for symbol in symbols_with_data:
                    try:
                        symbol_data = data.xs(symbol, level=0, axis=1)
                        bars = self._convert_df_to_bars(symbol, symbol_data)
                        if self._completed_sessions_only:
                            bars = self._completed_session_bars(bars, now)
                            if len(bars) < 2:
                                continue
                            self._current_bars[symbol] = bars[-1]
                            bars = bars[:-1]
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
        batch_size: int = 15,
        max_retries: int = 2,
    ) -> Tuple[List[str], pd.DataFrame]:
        """
        Fetch batch stock data from Yahoo Finance in chunks to avoid rate limiting.

        Downloads symbols in batches, retrying rate-limited symbols up to max_retries times.

        Returns:
            Tuple of (symbols_with_data, combined DataFrame)
        """
        with self._tracer.start_as_current_span("YFBarProvider._fetch_batch_stock_data") as span:
            end_datetime = TradingDateTime.now().timestamp
            start_datetime = end_datetime - timedelta(days=HISTORY_CALENDAR_DAYS)
            span.set_attribute("total_symbols_to_fetch_data_for", len(symbols))
            span.set_attribute("batch_size", batch_size)

            all_data_frames = []
            successful_symbols = []
            remaining_symbols = symbols.copy()

            for attempt in range(max_retries + 1):
                if not remaining_symbols:
                    break

                if attempt > 0:
                    delay = 5 * attempt
                    span.add_event(
                        "retry_attempt",
                        attributes={
                            "attempt": attempt,
                            "delay_seconds": delay,
                            "remaining_symbols": len(remaining_symbols),
                        },
                    )
                    await asyncio.sleep(delay)

                batches = [
                    remaining_symbols[i : i + batch_size]
                    for i in range(0, len(remaining_symbols), batch_size)
                ]
                rate_limited_symbols = []

                for batch_idx, batch in enumerate(batches):
                    if batch_idx > 0:
                        await asyncio.sleep(2)

                    span.add_event(
                        "fetching_batch",
                        attributes={
                            "batch_index": batch_idx + 1,
                            "batch_count": len(batches),
                            "batch_size": len(batch),
                        },
                    )
                    yf.shared._ERRORS = {}
                    data = yf.download(
                        batch,
                        start=start_datetime,
                        end=end_datetime,
                        group_by="ticker",
                        interval=Timeframe.d1.to_yf_interval(),
                        progress=False,
                    )

                    batch_successful = batch.copy()

                    if yf.shared._ERRORS:
                        symbols_with_no_data = [
                            symbol
                            for symbol, error in yf.shared._ERRORS.items()
                            if any(e in error for e in self._no_data_errors)
                        ]
                        for symbol in symbols_with_no_data:
                            if symbol in batch_successful:
                                batch_successful.remove(symbol)

                        rate_limited_in_batch = [
                            symbol
                            for symbol, error in yf.shared._ERRORS.items()
                            if any(e in error for e in self._rate_limit_errors)
                        ]
                        rate_limited_symbols.extend(rate_limited_in_batch)
                        for symbol in rate_limited_in_batch:
                            if symbol in batch_successful:
                                batch_successful.remove(symbol)

                        other_errors = [
                            error
                            for symbol, error in yf.shared._ERRORS.items()
                            if not any(e in error for e in self._no_data_errors)
                            and not any(e in error for e in self._rate_limit_errors)
                        ]
                        if other_errors:
                            error_msg = "; ".join(other_errors)
                            span.set_status(trace.Status(trace.StatusCode.ERROR))
                            e = BarProviderException(f"YFinance errors: {error_msg}")
                            span.record_exception(e)
                            raise e

                    if not data.empty and batch_successful:
                        all_data_frames.append(data)
                        successful_symbols.extend(batch_successful)

                remaining_symbols = rate_limited_symbols
                if rate_limited_symbols:
                    span.add_event("symbols_rate_limited", attributes={"count": len(rate_limited_symbols)})

            yf.shared._ERRORS = {}

            if not all_data_frames:
                span.set_status(trace.Status(trace.StatusCode.ERROR))
                e = BarProviderException("No data received for any symbols")
                span.record_exception(e)
                raise e

            combined = pd.concat(all_data_frames, axis=1)
            # Remove duplicate columns that may appear from overlapping batches
            combined = combined.loc[:, ~combined.columns.duplicated()]

            span.set_attribute("symbols_with_data", len(successful_symbols))
            if remaining_symbols:
                span.set_attribute("symbols_still_rate_limited", remaining_symbols)
            span.set_status(trace.Status(trace.StatusCode.OK))

            return successful_symbols, combined

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
            if self._completed_sessions_only:
                current_bar = self._current_bars.get(symbol)
                if current_bar is None:
                    span.set_status(trace.Status(trace.StatusCode.ERROR))
                    e = NoBarsForSymbolException(symbol)
                    span.record_exception(e)
                    raise e
                span.set_attribute("current_bar.date", current_bar.trading_datetime.trading_date.isoformat())
                span.set_status(trace.Status(trace.StatusCode.OK))
                return current_bar
            span.add_event("begin_current_bar_data_fetch")
            data = yf.download(
                symbol,
                period=Timeframe.d1.to_yf_interval(),
                interval=Timeframe.m15.to_yf_interval(),
                group_by="ticker",
                progress=False,
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
                intraday_bars = self._convert_df_to_bars(symbol, symbol_data)
                # Aggregate the day's intraday (15-min) candles into a single "current day
                # so far" bar. close = the latest 15-min close (a fresh proxy for the live
                # price, so CURRENT_PRICE stays current), while volume = the accumulated
                # session volume — summed across every candle we have today — so that
                # CURRENT_VOLUME is unit-comparable to the daily AV* averages it is measured
                # against. (Previously only bars[-1] was kept, comparing ~15 minutes of
                # volume to a full-day average, which was effectively never true.)
                current_bar = Bar(
                    trading_datetime=TradingDateTime.now(),
                    open=intraday_bars[0].open,
                    high=Money(amount=max(b.high.amount for b in intraday_bars)),
                    low=Money(amount=min(b.low.amount for b in intraday_bars)),
                    close=intraday_bars[-1].close,
                    volume=sum(b.volume for b in intraday_bars),
                )
                span.set_attribute("intraday_candles_aggregated", len(intraday_bars))
                span.set_attribute("accumulated_session_volume", current_bar.volume)
            except Exception as e:
                span.set_status(trace.Status(trace.StatusCode.ERROR))
                raise e
            else:
                span.set_status(trace.Status(trace.StatusCode.OK))
                return current_bar

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
