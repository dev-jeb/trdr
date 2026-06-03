# Changelog

All notable changes to `trdr` are documented here.

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
