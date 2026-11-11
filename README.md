# TRDR - Trading Framework

TRDR is a framework for algorithmic trading in Python. It features a custom Domain-Specific Language (DSL) for expressing trading strategies in a clear, concise manner.

## 🌟 Features

- **Custom DSL**: Define trading strategies with a readable, declarative syntax
- **Modular Architecture**: Easily swap components like brokers and data providers
- **Async First**: Built from the ground up with Python's async/await pattern
- **Mock Trading**: Test strategies with a mock broker before using real money
- **Decision-level observability**: First-class OpenTelemetry tracing that records *why* a
  trade did or didn't happen — every entry/exit condition with its operands and result, and
  every order rejection with its reason. Export to any OTLP backend (Honeycomb, Grafana
  Tempo, etc.) with a single env var. See [Observability](#-observability).
- **Pattern Day Trading Controls**: Built-in [PDT rule compliance strategies](src/trdr/core/broker/pdt/README.md) (NunStrategy, WiggleStrategy, YoloStrategy)

## 📦 Installation

```bash
# Basic installation
pip install trdr

# Development installation (with testing tools)
pip install -e ".[dev]"
```

## 🚀 Quick Start

### 1. Define Your Strategy

Create a file `my-strategy.trdr` with your trading strategy:

```
STRATEGY
    NAME "Moving Average Crossover"
    DESCRIPTION "Basic MA crossover strategy with risk management"
    ENTRY
        ALL_OF
            MA5 CROSSED_ABOVE MA20
            MA20 > MA50
            CURRENT_PRICE > 100
    EXIT
        ANY_OF
            CURRENT_PRICE > (AVERAGE_COST * 1.06)  # 6% profit target
            CURRENT_PRICE < (AVERAGE_COST * 0.98)  # 2% stop loss
    SIZING
        RULE
            CONDITION
                ALL_OF
                    ACCOUNT_EXPOSURE < 0.5
                    NUMBER_OF_OPEN_POSITIONS < 3
            DOLLAR_AMOUNT 
                (AVAILABLE_CASH * 0.20)
```

### 2. Run Your Strategy

```python
import asyncio
from trdr.core.bar_provider.yf_bar_provider.yf_bar_provider import YFBarProvider
from trdr.core.security_provider.security_provider import SecurityProvider
from trdr.core.broker.mock_broker.mock_broker import MockBroker
from trdr.core.trading_engine.trading_engine import TradingEngine
from trdr.core.trading_context.trading_context import TradingContext
from trdr.core.broker.pdt.nun_strategy import NunStrategy

async def main():
    try:
        pdt_strategy = NunStrategy.create()
        async with await MockBroker.create(pdt_strategy=pdt_strategy) as broker:
            bar_provider = await YFBarProvider.create(["TSLA"])
            security_provider = await SecurityProvider.create(bar_provider)
            context = await TradingContext.create(security_provider, broker)
            engine = await TradingEngine.create("my-strategy", context)
            await engine.execute()
    except Exception as e:
        print(e)

if __name__ == "__main__":
    asyncio.run(main())
```

## 🔭 Observability

The trading engine is fully instrumented with OpenTelemetry, so you can answer questions like
*"why didn't we buy AAPL today?"* directly from your traces instead of guessing.

Each run produces a span tree where every security records the entry/exit decision, and each
DSL condition emits an event with its operands and result:

- `condition_evaluated` — one per comparison/crossover, with `condition`, `left`, `right`,
  `result`, and `symbol`
- `all_of_evaluated` / `any_of_evaluated` — which conditions passed/failed
- `order_rejected` / `order_allowed` — order outcomes with the numbers behind them
  (cash shortfall, PDT counts)
- `TradingEngine.execute` carries per-run counts (`signals.entry`, `orders.rejected`, …)

Every span is stamped with `service.version` (so you can compare behavior across releases)
and, via `OTEL_RESOURCE_ATTRIBUTES`, `deployment.environment`.

### Enabling it

Tracing is configured from standard `OTEL_*` environment variables, so the backend is pure
configuration — no code change to switch vendors. With no endpoint set, it's a no-op.

```bash
export OTEL_SERVICE_NAME=trdr
export OTEL_EXPORTER_OTLP_ENDPOINT=https://api.honeycomb.io   # any OTLP backend
export OTEL_EXPORTER_OTLP_HEADERS="x-honeycomb-team=YOUR_KEY"
export OTEL_RESOURCE_ATTRIBUTES="deployment.environment=local"
```

```python
from trdr.telemetry import configure_tracing, flush_tracing, shutdown_tracing

tracer = configure_tracing()  # reads the env vars above; returns a tracer
# ... pass `tracer` into the components (see examples/with_telemetry) ...
shutdown_tracing()            # flush + tear down for a normal process
```

**AWS Lambda:** configure the tracer once at module scope and call `flush_tracing()` before
the handler returns — otherwise the runtime freezes before the batched spans export. A
complete handler is in [`examples/lambda/handler.py`](examples/lambda/handler.py). The HTTP
OTLP exporter is used so it survives the Lambda freeze/thaw lifecycle.

## 🛠️ Architecture

TRDR is built with a modular, component-based architecture:

- **Bar Provider**: Supplies price/volume data (Yahoo Finance implementation included)
- **Security Provider**: Manages available securities for trading
- **Broker**: Handles order execution
  - [Mock Broker](src/trdr/core/broker/mock_broker/) - Local simulation for testing
  - [Alpaca Broker](src/trdr/core/broker/alpaca_broker/README.md) - Real trading with Alpaca API
- **Trading Context**: Coordinates components and maintains state
- **Trading Engine**: Executes strategies using the DSL parser
- **[PDT Strategies](src/trdr/core/broker/pdt/README.md)**: Enforces Pattern Day Trading rules with multiple compliance strategies

## 📊 DSL Reference

The TRDR Domain Specific Language provides a clean (I hope) syntax for expressing trading logic:

### Strategy Structure

Indentation is significant. `#` starts a comment that runs to the end of the line.

```
STRATEGY
    NAME "Strategy Name"
    DESCRIPTION "Strategy Description"
    ENTRY
        # Entry conditions
    EXIT
        # Exit conditions
    SIZING
        # Position sizing rules
```

### Logical Operators

```
ALL_OF          # All conditions must be true
ANY_OF          # Any condition can be true
```

### Technical Indicators

```
MA{period}      # Simple moving average:  MA5, MA20, MA50, MA100, MA200
EMA{period}     # Exponential moving avg:  EMA5, EMA12, EMA20, EMA26, EMA50
AV{period}      # Average (daily) volume:  AV5, AV20, AV50, AV100, AV200
RSI{period}     # Relative strength index (0-100):  RSI7, RSI14, RSI21
MACD_LINE       # MACD line, signal line, and histogram
MACD_SIGNAL
MACD_HISTOGRAM
ATR14           # Average true range (14-period)
BBAND_UPPER     # Bollinger Bands (20-period, 2 std dev)
BBAND_LOWER
```

### Comparison & Crossover Operators

```
>               # Greater than
<               # Less than
>=              # Greater than or equal to
<=              # Less than or equal to
==              # Equal to
!=              # Not equal to
CROSSED_ABOVE   # One moving average crossed above another (MA identifiers only)
CROSSED_BELOW   # One moving average crossed below another (MA identifiers only)
```

### Price Metrics

```
CURRENT_PRICE   # Latest price (most recent intraday close)
CURRENT_VOLUME  # Accumulated volume so far in the current session
DAILY_HIGH      # Session high
DAILY_LOW       # Session low
PERCENT_CHANGE  # Daily percent change
```

### Account Metrics

```
ACCOUNT_EQUITY           # Total account value (cash + positions), e.g. (ACCOUNT_EQUITY / 10) for equal weight
ACCOUNT_EXPOSURE         # Percentage of account exposed to market
AVAILABLE_CASH           # Available cash for trading
AVERAGE_COST             # Average cost of current position
NUMBER_OF_OPEN_POSITIONS # Number of currently open positions
```

### Returns, Volatility and Ranking

```
RETURN_5D RETURN_21D RETURN_63D RETURN_126D RETURN_252D   # fractional return over N sessions (0.05 = +5%)
VOLATILITY_21D VOLATILITY_63D                             # annualized std dev of daily returns
UNIVERSE_SIZE                                             # tradable symbols with data this run
RANK(expression)                                          # this symbol's rank across the universe, 1 = largest
```

`RANK` makes cross-sectional strategies possible: compare each symbol with the rest of the
universe instead of judging it alone.

```
RANK((RETURN_252D + 1) / (RETURN_21D + 1)) <= 10   # top 10 by 12-1 month momentum
RANK(-VOLATILITY_63D) <= 10                        # the 10 least volatile
RANK(RETURN_126D) <= (UNIVERSE_SIZE * 0.2)         # top quintile
```

The engine runs exits before entries each run, so a rotation strategy frees slots and cash before
it buys. A symbol sold in a run is not re-bought in that run.

### Calendar

The decision session's date (the current bar's), the same for every security:

```
DAY_OF_WEEK                  # 1 = Monday ... 5 = Friday
DAY_OF_MONTH  MONTH
TRADING_DAY_OF_MONTH         # 1 = first session of the month
TRADING_DAYS_LEFT_IN_MONTH   # 0 = last session of the month
```

Pass the exchange's session dates (`TradingContext.create(..., sessions=[...])`) so the trading-day
counts skip holidays; without them, weekdays are counted. With a daily backtest or
`completed_sessions_only`, orders decided on a session fill at the next open: entering when
`TRADING_DAYS_LEFT_IN_MONTH == 1` holds from the open of the month's last session.

### Market Regime

Values from a benchmark (e.g. SPY), the same for every security, so a strategy can stand aside
when the broad market is in a downtrend:

```
MARKET_PRICE    # Benchmark's current price
MARKET_MA50     # Benchmark's 50-day moving average
MARKET_MA200    # Benchmark's 200-day moving average
```

Pass the benchmark to the context and include it in the bar provider. It is not traded unless
you list it in `universe`:

```python
bar_provider = await YFBarProvider.create(symbols + ["SPY"])
context = await TradingContext.create(security_provider, broker, market_symbol="SPY")
```

Example filter: `MARKET_PRICE > MARKET_MA200`.

### Mathematical Operators

```
+               # Addition
-               # Subtraction (also unary minus: -ATR14)
*               # Multiplication
/               # Division (dividing by zero skips the security, like a missing indicator)
(expression)    # Parentheses for grouping expressions
```

`*` and `/` bind tighter than `+` and `-`, e.g. `CURRENT_PRICE < (AVERAGE_COST - ATR14 * 2)`.

### Several strategies in one account (sleeves)

Give each strategy a share of the account and it runs in a *sleeve*. Inside a sleeve the account
identifiers mean "this strategy's share": `ACCOUNT_EQUITY` = weight x account equity,
`AVAILABLE_CASH` = that budget minus the strategy's own holdings (never more than the real cash),
and `NUMBER_OF_OPEN_POSITIONS` / `ACCOUNT_EXPOSURE` count only its own positions. Strategy files
don't change, and a strategy at weight 1.0 behaves as if it had the account to itself. Budgets
track current account equity, so each new buy sizes the sleeves back toward their weights.

```python
context = await TradingContext.create(security_provider, broker, sleeve_weight=0.6)
```

Orders are tagged with their strategy, so each strategy's holdings, cost and P&L can be rebuilt
from the broker's order history: `broker.get_strategy_orders(name)` (Alpaca) and
`trdr.core.broker.performance.strategy_performance(orders, marks)`.

### Fractional shares

By default a buy is rounded down to whole shares, so a $140 position in a $465 stock buys nothing.
With `fractional_shares=True` the engine buys the exact dollar amount, to 6 decimal places:

```python
engine = await TradingEngine.create("my-strategy", context, fractional_shares=True)
```

Use it in backtests too, so both size the same way. A buy too small for one whole share (or, with
fractional shares, under the broker's $1 minimum) is skipped instead of sent. Market orders are day
orders, as Alpaca requires for fractions. Exact sizes leave no rounding slack, so a strategy that
spends the whole account should keep a little cash (e.g. `(ACCOUNT_EQUITY * 0.098)` for ten
positions): buys are sized at the last close but fill at the open, and a gap up would otherwise
leave the last buy short of cash.

### Matching a daily backtest

A daily backtest decides on each session's close and fills at the next open. To trade the same
way live, run once a day shortly after the open with completed sessions only:

```python
bar_provider = await YFBarProvider.create(symbols, completed_sessions_only=True)
```

The latest finished session is then the current bar (`CURRENT_PRICE` is its close and
`CURRENT_VOLUME` its full-day volume), and today's in-progress session is ignored.

## 📚 Examples

Check the `examples/` directory for complete examples:

- **[`no_telemetry/`](examples/no_telemetry/script.py)**: Basic usage, no tracing
- **[`with_telemetry/`](examples/with_telemetry/script.py)**: Tracing via `configure_tracing`, exported to an OTLP backend
- **[`lambda/`](examples/lambda/handler.py)**: Scheduled AWS Lambda entrypoint with the required `flush_tracing()` pattern
- **[`strategies/`](examples/strategies/)**: Sample `.trdr` strategy files

## 🧪 Testing

```bash
# Run all tests
pytest

# Run specific test file
pytest src/trdr/path/to/test_file.py

# Run specific test
pytest src/trdr/path/to/test_file.py::TestClass::test_method
```

## 📝 License

[MIT License](LICENSE)

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
