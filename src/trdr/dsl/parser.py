from typing import List
from .lexer import Token, TokenType, ReservedKeyword
from .dsl_ast import (
    StrategyAST,
    Expression,
    Literal,
    Identifier,
    BinaryExpression,
    BinaryOperator,
    CrossoverExpression,
    RankExpression,
    AllOf,
    AnyOf,
    Sizing,
    SizingRule,
)


class ParserError(Exception):
    def __init__(self, message: str, line: int = None):
        self.line = line
        super().__init__(f"Line {line}: {message}" if line is not None else message)


class Parser:
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0

    def current(self) -> Token:
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        # Return an artificial EOF token if at the end.
        last_line = self.tokens[-1].line if self.tokens else 0
        return Token(TokenType.EOF, "", last_line)

    def advance(self):
        self.pos += 1

    def expect(self, token_type: TokenType, value: str = None) -> Token:
        token = self.current()
        if token.type != token_type or (value is not None and token.value != value):
            raise ParserError(
                f"Expected {token_type.value} with value '{value}', but got {token.type.value} with value '{token.value}'",
                token.line,
            )
        self.advance()
        return token

    def parse(self) -> StrategyAST:
        return self.parse_strategy()

    def parse_strategy(self) -> StrategyAST:
        self.expect(TokenType.IDENTIFIER, ReservedKeyword.STRATEGY)
        self.expect(TokenType.INDENT)
        name = None
        description = None
        entry_expr = None
        exit_expr = None
        sizing_expr = None

        while self.current().type != TokenType.DEDENT and self.current().type != TokenType.EOF:
            field_token = self.expect(TokenType.IDENTIFIER)
            field_name = field_token.value.upper()
            if field_name in {ReservedKeyword.NAME, ReservedKeyword.DESCRIPTION}:
                value_token = self.expect(TokenType.STRING)
                if field_name == ReservedKeyword.NAME:
                    name = value_token.value.strip('"')
                else:
                    description = value_token.value.strip('"')
            elif field_name == ReservedKeyword.ENTRY:
                entry_expr = self.parse_entry_or_exit()
            elif field_name == ReservedKeyword.EXIT:
                exit_expr = self.parse_entry_or_exit()
            elif field_name == ReservedKeyword.SIZING:
                sizing_expr = self.parse_sizing()
            else:
                raise ParserError(f"Unknown field '{field_token.value}'", field_token.line)

        self.expect(TokenType.DEDENT)
        return StrategyAST(
            name=name if name is not None else "",
            description=description if description is not None else "",
            entry=entry_expr,
            exit=exit_expr,
            sizing=sizing_expr,
        )

    def parse_entry_or_exit(self) -> Expression:
        """
        Parse the entry/exit block expecting that it starts with a composite operator
        ('all_of' or 'any_of') and contains exactly one composite expression.
        """
        self.expect(TokenType.INDENT)
        # Enforce that the first token is an identifier and its value is a composite operator.
        token = self.current()
        if token.type != TokenType.IDENTIFIER or token.value.upper() not in {
            ReservedKeyword.ALL_OF,
            ReservedKeyword.ANY_OF,
        }:
            raise ParserError(
                f"Entry/Exit block must start with a composite operator ('{ReservedKeyword.ALL_OF}' or '{ReservedKeyword.ANY_OF}').",
                token.line,
            )
        expr = self.parse_expression()
        if self.current().type != TokenType.DEDENT:
            raise ParserError(
                "Entry/Exit block must contain a single composite expression "
                "(wrap multiple conditions inside all_of or any_of).",
                token.line,
            )
        self.expect(TokenType.DEDENT)
        return expr

    def parse_block_expression(self) -> Expression:
        # A block expression consists of an INDENT, one or more expressions, and a DEDENT.
        self.expect(TokenType.INDENT)
        exprs = []
        while self.current().type != TokenType.DEDENT and self.current().type != TokenType.EOF:
            expr = self.parse_expression()
            exprs.append(expr)
        self.expect(TokenType.DEDENT)
        if len(exprs) == 1:
            return exprs[0]
        else:
            return AllOf(exprs)

    def parse_expression(self) -> Expression:
        token = self.current()
        if token.type == TokenType.IDENTIFIER and token.value.upper() in {
            ReservedKeyword.ALL_OF,
            ReservedKeyword.ANY_OF,
        }:
            comp = token.value.upper()
            self.advance()  # consume 'all_of' or 'any_of'
            # Instead of using parse_block_expression, get a list of expressions directly.
            exprs = self.parse_composite_block()
            if comp == ReservedKeyword.ALL_OF:
                return AllOf(exprs)
            elif comp == ReservedKeyword.ANY_OF:
                return AnyOf(exprs)
            else:
                raise ParserError(f"Unknown composite operator '{comp}'", token.line)
        else:
            return self.parse_comparison()

    def parse_composite_block(self) -> List[Expression]:
        self.expect(TokenType.INDENT)
        exprs = []
        while self.current().type != TokenType.DEDENT and self.current().type != TokenType.EOF:
            expr = self.parse_expression()
            exprs.append(expr)
        self.expect(TokenType.DEDENT)
        return exprs

    def parse_comparison(self) -> Expression:
        left = self.parse_arithmetic()
        token = self.current()

        # Handle comparison operators (arithmetic was already consumed by parse_arithmetic)
        if token.type == TokenType.OPERATOR and token.value in {">", "<", "==", ">=", "<=", "!="}:
            op = BinaryOperator.from_string(token.value)
            self.advance()
            right = self.parse_arithmetic()
            return BinaryExpression(left, op, right)
        if token.type == TokenType.OPERATOR and token.value not in {"+", "-", "*", "/"}:
            raise ParserError(f"Unknown operator '{token.value}'", token.line)

        # Handle crossover operators (which are identifiers, not operators)
        elif token.type == TokenType.IDENTIFIER and token.value.upper() in {
            ReservedKeyword.CROSSED_ABOVE,
            ReservedKeyword.CROSSED_BELOW,
        }:
            op = ReservedKeyword(token.value.upper())
            self.advance()
            right = self.parse_arithmetic()

            if not isinstance(left, Identifier) or not isinstance(right, Identifier):
                raise ParserError(f"Crossover operators require identifiers on both sides", token.line)
            return CrossoverExpression(left, op, right)

        return left

    def parse_arithmetic(self) -> Expression:
        expr = self.parse_term()
        while self.current().type == TokenType.OPERATOR and self.current().value in {"+", "-"}:
            op_str = self.current().value
            op = BinaryOperator.from_string(op_str)
            self.advance()
            right = self.parse_term()
            expr = BinaryExpression(expr, op, right)
        return expr

    def parse_term(self) -> Expression:
        expr = self.parse_factor()
        while self.current().type == TokenType.OPERATOR and self.current().value in {"*", "/"}:
            op_str = self.current().value
            op = BinaryOperator.from_string(op_str)
            self.advance()
            right = self.parse_factor()
            expr = BinaryExpression(expr, op, right)
        return expr

    def parse_factor(self) -> Expression:
        token = self.current()
        if token.type == TokenType.NUMBER:
            self.advance()
            try:
                if "." in token.value:
                    value = float(token.value)
                else:
                    value = int(token.value)
            except Exception:
                raise ParserError(f"Invalid number format '{token.value}'", token.line)
            return Literal(value)
        elif token.type == TokenType.STRING:
            self.advance()
            return Literal(token.value.strip('"'))
        elif token.type == TokenType.IDENTIFIER and token.value.upper() == ReservedKeyword.RANK:
            # RANK(<arithmetic>): this symbol's rank of the value across the universe, 1 = largest
            self.advance()
            self.expect(TokenType.LEFT_PAREN)
            inner = self.parse_arithmetic()
            self.expect(TokenType.RIGHT_PAREN)
            return RankExpression(inner)
        elif token.type == TokenType.IDENTIFIER:
            self.advance()
            return Identifier(token.value)
        elif token.type == TokenType.LEFT_PAREN:
            self.advance()
            expr = self.parse_arithmetic()
            self.expect(TokenType.RIGHT_PAREN)
            return expr
        elif token.type == TokenType.OPERATOR and token.value == "-":
            # Unary minus, e.g. `-0.5` or `-ATR14`: evaluated as (0 - operand)
            self.advance()
            return BinaryExpression(Literal(0), BinaryOperator.MINUS, self.parse_factor())
        else:
            raise ParserError(f"Unexpected token {token.type.value} with value '{token.value}'", token.line)

    def parse_sizing(self) -> Sizing:
        self.expect(TokenType.INDENT)
        rules = []
        while self.current().type != TokenType.DEDENT and self.current().type != TokenType.EOF:
            self.expect(TokenType.IDENTIFIER, ReservedKeyword.RULE)
            rule = self.parse_sizing_rule()
            rules.append(rule)
        self.expect(TokenType.DEDENT)
        return Sizing(rules)

    def parse_sizing_rule(self) -> SizingRule:
        self.expect(TokenType.INDENT)
        condition = None
        value = None

        while self.current().type != TokenType.DEDENT and self.current().type != TokenType.EOF:
            field_token = self.expect(TokenType.IDENTIFIER)
            field_name = field_token.value.upper()

            if field_name == ReservedKeyword.CONDITION:
                condition = self.parse_condition_block()
            elif field_name == ReservedKeyword.DOLLAR_AMOUNT:
                # Handle the case where AMOUNT is followed by an indented value
                self.expect(TokenType.INDENT)
                value = self.parse_expression()
                self.expect(TokenType.DEDENT)
            else:
                raise ParserError(f"Unexpected field '{field_token.value}' in sizing rule", field_token.line)

        self.expect(TokenType.DEDENT)

        if value is None:
            raise ParserError("Sizing rule must have a dollar amount.")
        return SizingRule(condition, value)

    def parse_condition_block(self) -> Expression:
        """
        Parse a condition block, which may start with a composite operator
        ('ALL_OF' or 'ANY_OF') and contain one or more expressions.
        """
        self.expect(TokenType.INDENT)
        token = self.current()
        if token.type == TokenType.IDENTIFIER and token.value.upper() in {
            ReservedKeyword.ALL_OF,
            ReservedKeyword.ANY_OF,
        }:
            comp = token.value.upper()
            self.advance()  # consume 'all_of' or 'any_of'
            exprs = self.parse_composite_block()
            self.expect(TokenType.DEDENT)
            if comp == ReservedKeyword.ALL_OF:
                return AllOf(exprs)
            elif comp == ReservedKeyword.ANY_OF:
                return AnyOf(exprs)
        else:
            # If not a composite, parse a single expression
            expr = self.parse_expression()
            self.expect(TokenType.DEDENT)
            return expr
