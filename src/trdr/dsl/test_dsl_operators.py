"""Arithmetic, comparison operators and comments, end to end: text -> lexer -> parser -> evaluate."""

import asyncio
from decimal import Decimal

import pytest

from .lexer import Lexer, LexerError
from .parser import Parser, ParserError
from ..core.shared.models import ContextIdentifier
from ..core.trading_context.exceptions import MissingContextValue


class StubContext:
    """Serves identifier values from a dict, like TradingContext.get_value_for_identifier."""

    current_symbol = "TEST"

    def __init__(self, **values):
        self.values = {ContextIdentifier(k): Decimal(str(v)) for k, v in values.items()}

    async def get_value_for_identifier(self, identifier):
        return self.values[identifier]


def _strategy(entry_lines: list[str], header_comment: str = "") -> str:
    entry = "\n".join(f"            {line}" for line in entry_lines)
    return f"""{header_comment}STRATEGY
    NAME "t"
    DESCRIPTION "t"
    ENTRY
        ALL_OF
{entry}
    EXIT
        ANY_OF
            CURRENT_PRICE > (AVERAGE_COST * 1.1)
    SIZING
        RULE
            CONDITION
                ALL_OF
                    NUMBER_OF_OPEN_POSITIONS < 4
            DOLLAR_AMOUNT
                (AVAILABLE_CASH * 0.25)
"""


def _entry_result(entry_lines: list[str], **values) -> bool:
    ast = Parser(Lexer(_strategy(entry_lines)).tokenize()).parse()
    return asyncio.run(ast.entry.evaluate(StubContext(**values)))


VALUES = {"CURRENT_PRICE": 90, "MA50": 100, "ATR14": 4, "AVERAGE_COST": 100, "CURRENT_VOLUME": 300, "AV50": 200}


@pytest.mark.parametrize(
    "condition, expected",
    [
        ("CURRENT_PRICE < (MA50 - ATR14 * 2)", True),  # 90 < 92: * binds tighter than -
        ("CURRENT_PRICE < (MA50 - ATR14 * 3)", False),  # 90 < 88
        ("CURRENT_PRICE > (MA50 + ATR14)", False),
        ("(CURRENT_VOLUME / AV50) > 1.4", True),  # 1.5
        ("(MA50 - CURRENT_PRICE) / MA50 > 0.09", True),  # 0.10
        ("CURRENT_PRICE > MA50 * -1", True),  # unary minus
        ("CURRENT_PRICE > -ATR14", True),
        ("CURRENT_PRICE >= 90", True),
        ("CURRENT_PRICE <= 89.99", False),
        ("CURRENT_PRICE != 90", False),
        ("CURRENT_PRICE == 90", True),
    ],
)
def test_operators(condition, expected):
    assert _entry_result([condition], **VALUES) is expected


def test_division_by_zero_is_a_missing_value_not_a_crash():
    with pytest.raises(MissingContextValue, match="Division by zero"):
        _entry_result(["(CURRENT_VOLUME / AV50) > 1"], **{**VALUES, "AV50": 0})


def test_comments_are_ignored_including_comment_lines_at_other_indentation():
    text = _strategy(
        [
            "CURRENT_PRICE < MA50  # trailing comment",
            "# comment-only line, indented like a condition",
            "MA50 > ATR14",
        ],
        header_comment="# leading comment\n",
    )
    text = text.replace("    EXIT\n", "  # comment-only line at an odd indentation\n    EXIT\n")
    ast = Parser(Lexer(text).tokenize()).parse()
    assert asyncio.run(ast.entry.evaluate(StubContext(**VALUES))) is True


def test_blank_lines_with_trailing_spaces_do_not_break_indentation():
    text = _strategy(["CURRENT_PRICE < MA50", "", "MA50 > ATR14"]).replace("\n\n", "\n      \n")
    ast = Parser(Lexer(text).tokenize()).parse()
    assert asyncio.run(ast.entry.evaluate(StubContext(**VALUES))) is True


def test_line_numbers_count_blank_lines():
    text = _strategy(["CURRENT_PRICE < MA50", "", "", "CURRENT_PRICE @ 1"])
    with pytest.raises(LexerError) as exc:
        Lexer(text).tokenize()
    assert exc.value.line == text.splitlines().index("            CURRENT_PRICE @ 1") + 1


def test_unknown_comparison_operator_is_rejected():
    with pytest.raises(ParserError, match="Unknown operator"):
        Parser(Lexer(_strategy(["CURRENT_PRICE => 1"])).tokenize()).parse()
