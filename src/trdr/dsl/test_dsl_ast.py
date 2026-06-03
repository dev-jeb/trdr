import asyncio

import pytest

from .dsl_ast import Sizing, SizingRule, Literal, BinaryExpression, NoSizingRuleMatched


class _Ctx:
    """Truthy stand-in context; Literal/BinaryExpression operands here ignore it."""


def test_sizing_rule_with_no_condition_is_unconditional_default():
    # A SizingRule(condition=None, ...) must match without dereferencing the
    # (absent) condition — regression test for the None-ordering bug.
    sizing = Sizing([SizingRule(condition=None, value=Literal(500))])
    assert asyncio.run(sizing.evaluate(_Ctx())) == 500


def test_sizing_falls_through_to_default_rule():
    # First rule's condition is false; the unconditional default rule wins.
    false_rule = SizingRule(condition=BinaryExpression(Literal(1), "<", Literal(0)), value=Literal(100))
    default_rule = SizingRule(condition=None, value=Literal(750))
    sizing = Sizing([false_rule, default_rule])
    assert asyncio.run(sizing.evaluate(_Ctx())) == 750


def test_sizing_raises_when_no_rule_matches():
    false_rule = SizingRule(condition=BinaryExpression(Literal(1), "<", Literal(0)), value=Literal(100))
    sizing = Sizing([false_rule])
    with pytest.raises(NoSizingRuleMatched):
        asyncio.run(sizing.evaluate(_Ctx()))
