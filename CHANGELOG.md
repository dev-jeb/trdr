# Changelog

All notable changes to `trdr` are documented here.

## 0.0.15

### Added
- **Fractional shares.** `TradingEngine.create(..., fractional_shares=True)` buys the sized dollar
  amount exactly (rounded down to 6 decimal places) instead of whole shares. Small accounts can
  then hold every position: at $1,400 split ten ways, a $465 stock was a 0-share order. Default off,
  so existing backtests reproduce.

### Changed
- **Market orders are day orders** (were good-til-canceled). Alpaca requires `day` for fractional
  orders, and an unfilled market order should expire with the session rather than fill on a later
  day on a stale decision.

### Fixed
- **Buys too small for one share are skipped, not sent.** A sizing below one whole share sent a
  0-share order, which Alpaca rejects with an error that failed the whole strategy run. Such entries
  (and fractional buys under Alpaca's $1 minimum) now count as skipped.

## 0.0.14

### Added
- **Sleeves: several strategies in one account.** `TradingContext.create(..., sleeve_weight=w)`
  scopes the account identifiers to the strategy's share: `ACCOUNT_EQUITY` = w x account
  equity, `AVAILABLE_CASH` = that budget minus the strategy's own holdings (capped by the
  account's real cash), and `NUMBER_OF_OPEN_POSITIONS` / `ACCOUNT_EXPOSURE` count only its own
  positions. Holdings are marked at the current bar's close. Strategy files are unchanged. With
  no `sleeve_weight`, behavior is exactly as before.
- **`broker.performance.strategy_performance(orders, marks)`**: one strategy's holdings, average
  cost, realized / unrealized / total P&L from its own tagged fills.
- **`AlpacaBroker.get_strategy_orders(name)`**: every filled order tagged with a strategy, across
  all symbols and time.

## 0.0.13

### Fixed
- **Cost basis ignores earlier round trips.** Alpaca positions are rebuilt from the symbol's
  whole order history, and `Position.average_cost` summed all of it. Buying at 50, selling at
  56 and later re-buying at 45 reported an average cost of 39, so a +12% target fired on the
  next run. `average_cost` and `get_average_cost_for_strategy` now use only the orders since the
  position was last flat, in fill order. The backtest broker drops flat positions, so
  backtests were not affected: this was a live-only divergence.
- **`AVERAGE_COST` is the running strategy's own cost.** In a position shared by strategies, the
  DSL used the blended cost of everyone's shares (e.g. 54.97 instead of the strategy's 58.52).
  `TradingEngine` now sets `TradingContext.strategy_name`, and `AVERAGE_COST` uses that
  strategy's orders. Holding none of the position is a missing value.

### Added
- **`AlpacaBroker.create(..., api_key=, secret_key=, base_url=)`** for explicit credentials, so
  one process can trade several Alpaca accounts (e.g. one per strategy). Defaults remain the
  `ALPACA_*` environment variables. `BaseBroker.create(**options)` passes implementation options
  to a new `_configure()` hook, and unknown options raise `TypeError`.

## 0.0.12

New building blocks for three strategy classes that the DSL could not express before:
cross-sectional ranking, multi-asset allocation and calendar effects.

### Added
- **`RANK(expr)`**: the current symbol's rank of any arithmetic expression across the
  trading universe (1 = largest; negate for smallest-first). Each symbol's value is computed
  once per run; symbols without a value are left out. Examples: `RANK(RETURN_252D) <= 10`,
  `RANK(-VOLATILITY_63D) <= 10`, `RANK((RETURN_252D + 1) / (RETURN_21D + 1)) <= 10`
  (12-1 momentum).
- **Returns and volatility:** `RETURN_5D/21D/63D/126D/252D` (fractional N-session return) and
  `VOLATILITY_21D/63D` (annualized std dev of daily returns). `UNIVERSE_SIZE`.
- **Calendar:** `DAY_OF_WEEK`, `DAY_OF_MONTH`, `MONTH`, `TRADING_DAY_OF_MONTH`,
  `TRADING_DAYS_LEFT_IN_MONTH`, from the decision session's date. Trading-day counts use
  `TradingContext.create(sessions=...)` (exchange calendar); without it they count weekdays.
- **`ACCOUNT_EQUITY`**, for equal-weight sizing such as `(ACCOUNT_EQUITY / 10)`. Before, only
  `AVAILABLE_CASH` existed, so equal weights had to be approximated.
- **`TradingContext.create(universe=[...])`**: the explicit list of symbols to trade and rank,
  in order. The market benchmark can now also be traded (e.g. SPY in an ETF allocation) by
  listing it.

### Changed
- **The engine runs exits before entries.** `TradingEngine.execute` makes an exit pass over
  held symbols, then an entry pass. Before, symbols were handled in list order, so in a
  rotation strategy a buy early in the list was blocked by a "max positions" rule even when a
  sell later in the list freed the slot that same run. A symbol sold in a run is not re-bought
  in it (no same-run round trips). Per-run counts are unchanged.
- `TradingContext` caches each symbol's `Security` for the run (the entry pass and `RANK` reuse
  it).
- Condition descriptions in traces parenthesize nested arithmetic: `(a + b) * c`.

### Fixed
- **A fully invested account can sell.** `BaseBroker._validate_pre_order` applied the
  insufficient-cash check to every order, including sells that close a position. With
  little cash left (e.g. equal-weight, fully invested), every exit was rejected and positions
  could never be closed, live (Alpaca) as well as in backtests. Earlier strategies kept at
  least 40% cash, so this never showed. Now only orders that open or add to a position need
  cash, and the PDT gate counts a closing order's proceeds as cash *after* the order instead of
  subtracting them.
- **`AlpacaBroker` waits up to 60s for an order to settle** (was 10s). On 2026-11-10, paper
  orders sent at 9:35:41 and 9:35:50 both filled at 9:36:09, so the 10s wait timed out and
  the second order was sized from pre-fill cash.

## 0.0.11

### Added
- **Market regime identifiers: `MARKET_PRICE`, `MARKET_MA50`, `MARKET_MA200`.** They come
  from a benchmark passed as `TradingContext.create(..., market_symbol="SPY")` (it must also
  be in the bar provider), so a strategy can filter on the broad market, e.g.
  `MARKET_PRICE > MARKET_MA200`. The benchmark is removed from the traded symbols. Using a
  `MARKET_*` identifier without a `market_symbol` raises, because that is a configuration
  error rather than missing data.
- **`YFBarProvider.create(..., completed_sessions_only=True)`** to match a daily backtest that
  decides on a session's close and fills at the next open. The latest finished session is the
  current bar (full-day volume), history ends the session before it, today's in-progress bar
  is ignored, and no intraday data is fetched. A session's bar counts once it is 16:30 ET.
  Symbols whose latest finished session is older than the rest of the batch (Yahoo can
  publish a just-closed day with NaN prices) are dropped instead of being traded on stale data.
- **`AlpacaBroker` waits for placed orders to settle** (polls for up to 10s). Before, the
  next symbol could be evaluated before a market order filled, sizing it off pre-fill cash
  and leaving the new position out of the exposure cap. A daily backtest books each fill
  before its next decision.

### Fixed
- **A symbol with no data no longer aborts the run.** `TradingContext.next_symbol` crashed
  with `'NoneType' object has no attribute 'symbol'` when `get_security()` returned `None`
  (e.g. a one-day gap in a symbol's bars). That stopped every remaining symbol, including
  exits. In backtests the whole simulated day was dropped, so earlier results silently
  skipped those days. The symbol is now skipped with a `skipped_symbol_without_data` event.
- **DSL arithmetic `+`, `-` and `/` now work.** They were documented, and the parser and AST
  handled them, but the lexer only tokenized `*`. Unary minus (`-ATR14`) is supported, and
  dividing by zero skips the security (like a missing indicator) instead of aborting the run.
- **Comparisons `>=`, `<=` and `!=` now work.** Only `>`, `<` and `==` parsed before; an
  unknown operator such as `=>` is now a clear parse error.
- **`#` comments.** The README example used them, but the lexer rejected them. A comment-only
  line's indentation no longer affects blocks.
- **Lexer line numbers count blank lines.** Blank lines with trailing spaces no longer shift
  the next line's indentation.

### Changed
- `YFBarProvider` fetches 400 calendar days of daily history (was 300, about 200 trading days,
  leaving MA200 crossovers with almost no margin).
- New dependency: `tzdata`, so the America/New_York session clock works where the OS has no
  timezone database (e.g. slim container images).

## 0.0.10

### Fixed
- **`AlpacaBroker` works with Alpaca's current account API.** Alpaca stopped returning
  `daytrade_count` from `/v2/account` (first seen 2026-07-06), so `_refresh_day_trade_count`
  left the count unset and `BaseBroker.create()` raised `Day trade count is not initialized`
  on every run. Nothing could trade. When the field is missing, the broker now derives the
  rolling 5-business-day count from the account's filled orders. It errs high, so PDT
  strategies only get more cautious. If Alpaca does report the field, it is still used. The
  span attribute `day_trade_count.source` (`account` / `order_history`) shows which path ran.
- **Order history pagination works.** With 500+ orders on held symbols, `_refresh_positions`
  used to crash: it parsed fractional-second timestamps with a format that has no `%f`. It also
  sent `submitted_after` (not an Alpaca parameter) while paging in the wrong direction, so a
  second page would just repeat the first. Orders now come through one helper, `_list_orders`,
  which pages backwards with `until`, de-dupes by order id, and always terminates. The
  day-trade count goes through it as well, so its history is no longer capped at 500 orders.
- **Never-filled orders no longer crash position loading.** Alpaca sends
  `filled_avg_price: null` for canceled, rejected and pending orders, and
  `Decimal(None)` raised. Every run cancels pending orders first, so these show up in
  held symbols' history.
- **No leaked HTTP session when broker creation fails.** `BaseBroker.create()` now closes its
  `aiohttp` session before re-raising, removing the `Unclosed client session` warnings.

## 0.0.9

### Changed
- **One canonical `symbol` column.** `Strategy.process_security` previously tagged the
  ticker as `trading_context.current_symbol` — the only span using that verbose name. It now
  uses `symbol`, matching every other span/event (`condition_evaluated`,
  `get_value_for_identifier`, bar provider, security provider). A single `symbol` filter now
  covers the whole trace.

## 0.0.8

Instrumentation quality pass (informed by reviewing the actual spans in Honeycomb).

### Changed
- **`service.version` is now stamped on every span** (read from the installed trdr
  package), and `deployment.environment` (plus any other deploy metadata) can be set via
  the standard `OTEL_RESOURCE_ATTRIBUTES` env var. Spans are now segmentable by release
  and environment — e.g. comparing behavior before/after a deploy.
- **`get_value_for_identifier` spans are now useful.** They were emitted ~6×/security with
  *no* attributes (couldn't tell which identifier or what value). They now carry
  `identifier`, `symbol`, and the resolved `value`.
- **Decision events carry `symbol`.** `condition_evaluated` / `all_of_evaluated` /
  `any_of_evaluated` now include the symbol, so failures can be broken down per-ticker
  without a parent-span join.
- **Stable event names + attributes** instead of data baked into event-name strings:
  `pdt_check` (`cash_after_order`), `fetching_batch` (`batch_index`/`batch_count`/`batch_size`),
  `retry_attempt` (`attempt`/`delay_seconds`/`remaining_symbols`), `symbols_rate_limited`
  (`count`). Previously each distinct value created a new un-aggregatable "span" name.
- **`TradingEngine.execute` now returns its run counts** (`processed`/`skipped`/`entry`/
  `exit`/`rejected`) so callers can roll them up onto a parent/root span. Additive —
  existing callers that ignore the return value are unaffected.

## 0.0.7

### Fixed
- **Sizing no longer aborts the whole run.** `Sizing.evaluate` now raises a dedicated
  `NoSizingRuleMatched` exception (instead of a bare `ValueError`) when no rule's condition
  holds. `TradingEngine.execute` catches it per-security, records an
  `entry_skipped_no_sizing_rule` span event, and continues — so a fully-invested /
  over-exposure-cap day skips just that one entry rather than killing every remaining
  symbol's exits and the equity snapshot.
- **Sizing default rules with no condition.** Fixed an ordering bug where a `SizingRule`
  with `condition=None` (an unconditional default) raised `AttributeError` instead of
  matching. The `None` check now short-circuits before evaluating the condition.

### Changed
- **`CURRENT_VOLUME` is now accumulated session volume.** `get_current_bar` aggregates the
  day's 15-minute candles into a single "current day so far" bar: `close` is the latest
  15-min close (so `CURRENT_PRICE` stays fresh) while `volume` is the **sum** of all candles.
  Previously only the last 15-min candle was kept, so `CURRENT_VOLUME > AV*` compared ~15
  minutes of volume against a full-day average and was effectively never true. New span
  attributes `intraday_candles_aggregated` and `accumulated_session_volume` expose this.
  - Note: this is an intraday RVOL *approximation* — accumulated-so-far vs a full-day
    average — so it is biased low early in the session and converges to the true daily
    volume by close. Time-of-day normalization is not yet implemented.

### Notes
- Backtests run before 0.0.7 executed under the old sizing behavior (missed exits on
  full days); re-run the suite after upgrading, as returns/drawdown will shift.

## 0.0.6

### Added
- **DSL decision tracing.** Leaf comparisons/crossovers emit `condition_evaluated` span
  events (operands + result); `ALL_OF`/`ANY_OF` emit summary events naming the
  failed/passed conditions — answering "why did/didn't we trade X".
- **Broker/PDT decision tracing.** `place_order` records order intent; `_validate_pre_order`
  emits `order_rejected`/`order_allowed` events with the numbers behind the decision
  (cash shortfall, PDT counts).
- **`trdr.telemetry`** module (`configure_tracing` / `flush_tracing` / `shutdown_tracing`)
  wiring OTLP export to any backend via standard `OTEL_*` env vars (Lambda-safe HTTP exporter).
- `InsufficientFundsException` for clean, catchable cash rejections.

### Changed
- **Order rejections are non-fatal.** `TradingEngine.execute` catches
  `InsufficientFundsException` / `PDTRuleViolationException` per-security and continues,
  recording an `order_rejected` event and an `orders.rejected` count.
- Silenced yfinance download progress bars (`progress=False`).
- Pinned minimum dependency versions.

### Fixed
- Compatibility with yfinance ≥1.4 (`yf.shared` no longer auto-bound) via explicit
  `import yfinance.shared`.
- Pydantic v2.12 deprecation: `Security` after-validator converted to an instance method.
