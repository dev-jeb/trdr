from typing import List, Any, Optional, Union
from decimal import Decimal
from enum import Enum

from opentelemetry import trace

from ..core.trading_context.trading_context import TradingContext
from ..core.shared.models import ContextIdentifier
from ..core.broker.models import Money
from .lexer import ReservedKeyword


def _record_condition(
    description: str,
    result: Any,
    left_val: Optional[Decimal] = None,
    right_val: Optional[Decimal] = None,
) -> None:
    """
    Attach a structured 'condition_evaluated' event to the active span so the
    decision behind an entry/exit signal is visible in the trace backend.

    Records the human-readable condition, the operand values (when numeric), and
    the boolean outcome. A no-op when there is no recording span (e.g. tests,
    NoOpTracer), so it is always safe to call.
    """
    span = trace.get_current_span()
    if not span or not span.is_recording():
        return
    attributes: dict = {"condition": description, "result": bool(result)}
    if left_val is not None:
        attributes["left"] = float(left_val)
    if right_val is not None:
        attributes["right"] = float(right_val)
    span.add_event("condition_evaluated", attributes=attributes)


class BinaryOperator(Enum):
    """Enum representing the binary operators supported in the DSL"""

    GREATER = ">"
    LESS = "<"
    EQUAL = "=="
    PLUS = "+"
    MINUS = "-"
    MULTIPLY = "*"
    DIVIDE = "/"

    @classmethod
    def from_string(cls, operator: str) -> "BinaryOperator":
        """Convert string operator to enum member"""
        for op in cls:
            if op.value == operator:
                return op
        raise ValueError(f"Invalid binary operator: {operator}")


# Operators that produce a boolean decision (as opposed to arithmetic). Only
# these are recorded as conditions in the trace; arithmetic operators are
# sub-expressions whose values surface via their parent comparison.
COMPARISON_OPERATORS = frozenset(
    {BinaryOperator.GREATER, BinaryOperator.LESS, BinaryOperator.EQUAL}
)


# A helper function for tree printing.
def tree_line(label: str, content: str, indent: int) -> str:
    spacer = "    " * indent
    return f"{spacer}{label}: {content}"


def format_child_lines(child_text: str, base_indent: str, extra_space: int, connector: str) -> List[str]:
    """
    Formats the multi-line string child_text so that the first line is
    prefixed with connector and any subsequent lines are indented further.
    """
    lines = child_text.splitlines()
    if not lines:
        return []
    formatted = []
    # First line: add connector.
    formatted.append(f"{base_indent}{' ' * extra_space}{connector} {lines[0].strip()}")
    # Subsequent lines: indent an extra 3 spaces.
    subsequent_indent = base_indent + " " * (extra_space + 3)
    for line in lines[1:]:
        formatted.append(f"{subsequent_indent}{line.strip()}")
    return formatted


# Base class for all expressions
class Expression:
    def evaluate(self, context: Optional[TradingContext]) -> Decimal:

        raise NotImplementedError()

    def describe(self) -> str:
        """Compact, single-line, human-readable form (e.g. 'MA5 > MA20')."""
        raise NotImplementedError()

    def to_pretty_string(self, indent: int = 0) -> str:
        raise NotImplementedError()

    def __str__(self) -> str:
        return self.to_pretty_string()


class Literal(Expression):
    def __init__(self, value: Any):
        self.value = value

    async def evaluate(self, context: Optional[TradingContext]) -> Decimal:
        return Decimal(self.value)

    def describe(self) -> str:
        return str(self.value)

    def to_pretty_string(self, indent: int = 0) -> str:
        return tree_line("Literal", str(self.value), indent)


class Identifier(Expression):
    def __init__(self, identifier: Union[str, ContextIdentifier]):
        # If passed a string, convert to ContextIdentifier when possible
        if isinstance(identifier, str):
            try:
                self.context_identifier = ContextIdentifier(identifier)
            except ValueError:
                # If it's not a valid ContextIdentifier, keep as string
                # This allows for custom identifiers that might be added later
                self.context_identifier = identifier
        else:
            self.context_identifier = identifier

    async def evaluate(self, context: Optional[TradingContext]) -> Decimal:
        # Retrieve the identifier's value from the context
        if not context:
            raise ValueError("Context is required for identifier evaluation")
        value = await context.get_value_for_identifier(self.context_identifier)
        return value

    def describe(self) -> str:
        if isinstance(self.context_identifier, ContextIdentifier):
            return self.context_identifier.value
        return str(self.context_identifier)

    def to_pretty_string(self, indent: int = 0) -> str:
        return tree_line("Identifier", str(self.context_identifier), indent)


class BinaryExpression(Expression):
    def __init__(self, left: Expression, operator: Union[str, BinaryOperator], right: Expression):
        self.left = left
        # Convert string operator to enum if needed
        if isinstance(operator, str):
            self.operator = BinaryOperator.from_string(operator)
        else:
            self.operator = operator
        self.right = right

    async def evaluate(self, context: Optional[TradingContext]) -> bool:
        if not context:
            raise ValueError("Context is required for binary expression evaluation")

        left_val = await self.left.evaluate(context)
        right_val = await self.right.evaluate(context)

        if self.operator == BinaryOperator.GREATER:
            result = left_val > right_val
        elif self.operator == BinaryOperator.LESS:
            result = left_val < right_val
        elif self.operator == BinaryOperator.EQUAL:
            result = left_val == right_val
        elif self.operator == BinaryOperator.PLUS:
            return left_val + right_val
        elif self.operator == BinaryOperator.MINUS:
            return left_val - right_val
        elif self.operator == BinaryOperator.MULTIPLY:
            return left_val * right_val
        elif self.operator == BinaryOperator.DIVIDE:
            return left_val / right_val
        else:
            raise ValueError(f"Unsupported operator {self.operator}")

        # Only comparisons are decisions worth tracing; arithmetic operands
        # surface as the left/right values of their enclosing comparison.
        _record_condition(self.describe(), result, left_val, right_val)
        return result

    def describe(self) -> str:
        return f"{self.left.describe()} {self.operator.value} {self.right.describe()}"

    def to_pretty_string(self, indent: int = 0) -> str:
        base = "    " * indent
        lines = [f"{base}BinaryExpression"]
        lines.append(f"{base}    ├─ operator: {self.operator.value}")

        # Left operand
        left_str = self.left.to_pretty_string(indent + 2)
        left_lines = left_str.splitlines()
        if len(left_lines) == 1:
            lines.append(f"{base}    ├─ left: {left_lines[0].strip()}")
        else:
            lines.append(f"{base}    ├─ left:")
            for line in left_lines:
                lines.append(f"{base}        {line.strip()}")

        # Right operand
        right_str = self.right.to_pretty_string(indent + 2)
        right_lines = right_str.splitlines()
        if len(right_lines) == 1:
            lines.append(f"{base}    └─ right: {right_lines[0].strip()}")
        else:
            lines.append(f"{base}    └─ right:")
            for line in right_lines:
                lines.append(f"{base}        {line.strip()}")

        return "\n".join(lines)


class CrossoverExpression(Expression):
    def __init__(self, left: Identifier, operator: Union[str, ReservedKeyword], right: Identifier):
        self.left = left
        # Convert string to ReservedKeyword if needed
        if isinstance(operator, str):
            try:
                self.operator = ReservedKeyword(operator)
            except ValueError:
                raise ValueError(f"Invalid crossover operator: {operator}")
        else:
            self.operator = operator
        self.right = right

    async def evaluate(self, context: Optional[TradingContext]) -> bool:
        if not context:
            raise ValueError("Context is required for crossover expression evaluation")

        # Validate both identifiers are moving averages
        left_identifier = self.left.context_identifier
        right_identifier = self.right.context_identifier

        if not ContextIdentifier.is_moving_average(left_identifier) or not ContextIdentifier.is_moving_average(
            right_identifier
        ):
            raise ValueError(
                f"Unsupported moving average crossover {left_identifier} {right_identifier}. You may have forgotten to add the moving average to the ContextIdentifier enum."
            )

        # Convert ContextIdentifiers to Timeframes
        left_timeframe = left_identifier.to_timeframe()
        right_timeframe = right_identifier.to_timeframe()

        if not left_timeframe or not right_timeframe:
            raise ValueError(f"Could not convert {left_identifier} or {right_identifier} to timeframes")

        # Call appropriate method based on operator
        if self.operator == ReservedKeyword.CROSSED_ABOVE:
            result = context.current_security.has_bullish_moving_average_crossover(left_timeframe, right_timeframe)
        elif self.operator == ReservedKeyword.CROSSED_BELOW:
            result = context.current_security.has_bearish_moving_average_crossover(left_timeframe, right_timeframe)
        else:
            raise ValueError(f"Unsupported crossover operator {self.operator}")

        _record_condition(self.describe(), result)
        return result

    def describe(self) -> str:
        op = self.operator.value if isinstance(self.operator, ReservedKeyword) else str(self.operator)
        return f"{self.left.describe()} {op} {self.right.describe()}"

    def to_pretty_string(self, indent: int = 0) -> str:
        base = "    " * indent
        lines = [f"{base}CrossoverExpression"]
        lines.append(f"{base}    ├─ operator: {self.operator}")

        # Left operand
        left_str = self.left.to_pretty_string(indent + 2)
        left_lines = left_str.splitlines()
        if len(left_lines) == 1:
            lines.append(f"{base}    ├─ left: {left_lines[0].strip()}")
        else:
            lines.append(f"{base}    ├─ left:")
            for line in left_lines:
                lines.append(f"{base}        {line.strip()}")

        # Right operand
        right_str = self.right.to_pretty_string(indent + 2)
        right_lines = right_str.splitlines()
        if len(right_lines) == 1:
            lines.append(f"{base}    └─ right: {right_lines[0].strip()}")
        else:
            lines.append(f"{base}    └─ right:")
            for line in right_lines:
                lines.append(f"{base}        {line.strip()}")

        return "\n".join(lines)


class AllOf(Expression):
    def __init__(self, conditions: List[Expression]):
        self.conditions = conditions

    async def evaluate(self, context: Optional[TradingContext]) -> bool:
        if not context:
            raise ValueError("Context is required for all of evaluation")

        # First gather all results, then check if all are true
        results = [await condition.evaluate(context) for condition in self.conditions]
        outcome = all(results)
        span = trace.get_current_span()
        if span and span.is_recording():
            failed = [self.conditions[i].describe() for i, r in enumerate(results) if not r]
            span.add_event(
                "all_of_evaluated",
                attributes={
                    "result": outcome,
                    "passed": sum(1 for r in results if r),
                    "total": len(results),
                    "failed_conditions": failed,
                },
            )
        return outcome

    def describe(self) -> str:
        return "ALL_OF[" + ", ".join(c.describe() for c in self.conditions) + "]"

    def to_pretty_string(self, indent: int = 0) -> str:
        base = "    " * indent
        lines = [f"{base}AllOf"]
        for i, cond in enumerate(self.conditions):
            connector = "└─" if i == len(self.conditions) - 1 else "├─"
            cond_text = cond.to_pretty_string(indent + 2)
            child_lines = format_child_lines(cond_text, base, 4, connector)
            lines.extend(child_lines)
        return "\n".join(lines)


class AnyOf(Expression):
    def __init__(self, conditions: List[Expression]):
        self.conditions = conditions

    async def evaluate(self, context: Optional[TradingContext]) -> bool:
        if not context:
            raise ValueError("Context is required for any of evaluation")
        results = [await condition.evaluate(context) for condition in self.conditions]
        outcome = any(results)
        span = trace.get_current_span()
        if span and span.is_recording():
            passed = [self.conditions[i].describe() for i, r in enumerate(results) if r]
            span.add_event(
                "any_of_evaluated",
                attributes={
                    "result": outcome,
                    "passed": sum(1 for r in results if r),
                    "total": len(results),
                    "passed_conditions": passed,
                },
            )
        return outcome

    def describe(self) -> str:
        return "ANY_OF[" + ", ".join(c.describe() for c in self.conditions) + "]"

    def to_pretty_string(self, indent: int = 0) -> str:
        base = "    " * indent
        lines = [f"{base}AnyOf"]
        for i, cond in enumerate(self.conditions):
            connector = "└─" if i == len(self.conditions) - 1 else "├─"
            cond_text = cond.to_pretty_string(indent + 2)
            child_lines = format_child_lines(cond_text, base, 4, connector)
            lines.extend(child_lines)
        return "\n".join(lines)


class SizingRule:
    def __init__(self, condition: Expression, value: Expression):
        self.condition = condition  # Optional; can be None.
        self.value = value

    def to_pretty_string(self, indent: int = 0) -> str:
        base = "    " * indent
        lines = [f"{base}SizingRule"]
        sub = "    " * (indent + 1)
        if self.condition is not None:
            cond_text = self.condition.to_pretty_string(0)
            cond_lines = cond_text.splitlines()
            if len(cond_lines) == 1:
                lines.append(f"{sub}├─ condition: {cond_lines[0].strip()}")
            else:
                lines.append(f"{sub}├─ condition:")
                for line in cond_lines:
                    lines.append(f"{sub}   {line.strip()}")
        else:
            lines.append(f"{sub}├─ condition: (none)")
        # Value:
        val_text = self.value.to_pretty_string(0)
        val_lines = val_text.splitlines()
        if len(val_lines) == 1:
            lines.append(f"{sub}└─ value: {val_lines[0].strip()}")
        else:
            lines.append(f"{sub}└─ value:")
            for line in val_lines:
                lines.append(f"{sub}   {line.strip()}")
        return "\n".join(lines)


class NoSizingRuleMatched(Exception):
    """Raised when no SIZING rule's condition holds for the current context.

    This is an expected per-security outcome (e.g. fully invested / over the
    exposure cap), not a run-fatal error — the engine catches it and simply
    skips entering that one name.
    """


class Sizing(Expression):
    def __init__(self, rules: List[SizingRule]):
        self.rules = rules

    async def evaluate(self, context: Optional[TradingContext]) -> Decimal:
        if not context:
            raise ValueError("Context is required for sizing evaluation")
        for rule in self.rules:
            # A rule with no condition is an unconditional default — it always
            # matches. Check that first so we never dereference a None condition
            # (the `or` short-circuits before evaluating it).
            if rule.condition is None or await rule.condition.evaluate(context):
                return await rule.value.evaluate(context)
        raise NoSizingRuleMatched("No sizing rule matched the context.")

    def to_pretty_string(self, indent: int = 0) -> str:
        base = "    " * indent
        lines = []
        for i, rule in enumerate(self.rules):
            connector = "└─" if i == len(self.rules) - 1 else "├─"
            rule_text = rule.to_pretty_string(indent + 2)
            child_lines = format_child_lines(rule_text, base, 4, connector)
            lines.extend(child_lines)
        return "\n".join(lines)


class StrategyAST:
    def __init__(
        self,
        name: str,
        description: str,
        entry: Expression,
        exit: Expression,
        sizing: Expression,
    ):
        self.name = name
        self.description = description
        self.entry = entry
        self.exit = exit
        self.sizing = sizing

    async def evaluate_entry(self, context: TradingContext) -> bool:
        return await self.entry.evaluate(context)

    async def evaluate_exit(self, context: TradingContext) -> bool:
        return await self.exit.evaluate(context)

    async def evaluate_sizing(self, context: TradingContext) -> Money:
        return await self.sizing.evaluate(context)

    async def to_pretty_string(self, indent: int = 0) -> str:
        base = "    " * indent
        lines = [
            f"{base}StrategyAST: {self.name}",
            f"{base}├─ Description: {self.description}",
            f"{base}├─ Entry:",
            self.entry.to_pretty_string(indent + 2),
            f"{base}├─ Exit:",
            self.exit.to_pretty_string(indent + 2),
            f"{base}└─ Sizing:",
            self.sizing.to_pretty_string(indent + 2),
        ]
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.to_pretty_string()
