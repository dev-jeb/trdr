# TRDR - Trading Framework

## Build & Test Commands

```bash
# Install all dependencies (dev included)
uv sync --all-extras

# Run all tests
uv run pytest

# Run specific test file
uv run pytest src/trdr/path/to/test_file.py

# Run specific test
uv run pytest src/trdr/path/to/test_file.py::TestClass::test_method
```

## Code Style Guidelines

- **Naming**: Classes=PascalCase, functions/methods=snake_case, constants=UPPER_SNAKE_CASE
- **Imports**: Standard lib → third-party → local, specific imports preferred
- **Types**: Always use type annotations for parameters and return values
- **Async**: Use async/await pattern with factory `create()` methods
- **Error handling**: Custom exception hierarchy in each module
- **Documentation**: Descriptive docstrings for public methods
- **DSL**: Trading strategies defined in `.trdr` files with STRATEGY, ENTRY, EXIT sections
- **Async testing**: use asyncio.run() to run async calls in tests

## Project Structure

- `src/trdr/core/`: Core trading components (broker, bar_provider, strategy)
- `src/trdr/dsl/`: Domain-specific language for strategy definition
- `src/trdr/conftest.py`: pytest fixtures
- `src/trdr/test_utils/`: Test utilities

## DSL Keywords

### Technical Indicators
- **SMA**: `MA5`, `MA20`, `MA50`, `MA100`, `MA200`
- **EMA**: `EMA5`, `EMA12`, `EMA20`, `EMA26`, `EMA50`
- **RSI**: `RSI7`, `RSI14`, `RSI21` (0-100 scale)
- **MACD**: `MACD_LINE`, `MACD_SIGNAL`, `MACD_HISTOGRAM`
- **ATR**: `ATR14`
- **Bollinger Bands**: `BBAND_UPPER`, `BBAND_LOWER` (20-period, 2 std dev)

### Volume
- **Average Volume**: `AV5`, `AV20`, `AV50`, `AV100`, `AV200`

### Price Action
- `CURRENT_PRICE`, `CURRENT_VOLUME`
- `DAILY_HIGH`, `DAILY_LOW`
- `PERCENT_CHANGE` (daily % change)

### Account
- `ACCOUNT_EXPOSURE`, `AVAILABLE_CASH`, `AVERAGE_COST`, `NUMBER_OF_OPEN_POSITIONS`

### Operators
- Crossovers: `CROSSED_ABOVE`, `CROSSED_BELOW` (MA identifiers only)
- Logic: `ALL_OF`, `ANY_OF`
- Comparison: `>`, `<`, `>=`, `<=`, `==`
- Arithmetic: `+`, `-`, `*`, `/`
