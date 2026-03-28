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
