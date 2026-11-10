from dataclasses import dataclass
from enum import Enum, auto
from typing import List

from ..core.shared.models import ContextIdentifier


class ReservedKeyword(str, Enum):
    """
    Reserved keywords in the DSL language syntax.
    These are distinct from ContextIdentifier which represents runtime variables.
    """

    STRATEGY = "STRATEGY"
    NAME = "NAME"
    DESCRIPTION = "DESCRIPTION"
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    SIZING = "SIZING"
    RULE = "RULE"
    CONDITION = "CONDITION"
    ANY_OF = "ANY_OF"
    ALL_OF = "ALL_OF"
    CROSSED_ABOVE = "CROSSED_ABOVE"
    CROSSED_BELOW = "CROSSED_BELOW"
    DOLLAR_AMOUNT = "DOLLAR_AMOUNT"


# Valid identifiers include both context identifiers and reserved keywords
VALID_IDENTIFIERS = list(ContextIdentifier.__members__.keys()) + [kw.value for kw in ReservedKeyword]

ARITHMETIC_OPERATOR_CHARS = "+-*/"
COMPARISON_OPERATOR_CHARS = "><!="


class TokenType(Enum):
    INDENT = auto()
    DEDENT = auto()
    IDENTIFIER = auto()
    STRING = auto()
    NUMBER = auto()
    OPERATOR = auto()
    LEFT_PAREN = auto()
    RIGHT_PAREN = auto()
    EOF = auto()


@dataclass
class Token:
    type: TokenType
    value: str
    line: int

    def __str__(self):
        return f"Token({self.type.value}, '{self.value}', line {self.line})"


class LexerError(Exception):
    """Raised when the lexer encounters invalid input"""

    def __init__(self, message: str, line: int):
        self.line = line
        super().__init__(f"Line {line}: {message}")


class Lexer:
    def __init__(self, text: str):
        self.text = text
        self.pos = 0
        self.line = 1
        self.indent_stack = [0]  # Stack to track indentation levels
        self.at_line_start = True

    def tokenize(self) -> List[Token]:
        tokens = []
        while self.pos < len(self.text):
            char = self.text[self.pos]

            # Handle indentation at start of line
            if self.at_line_start:
                indent_level = self._count_indent()
                # A comment-only line carries no code, so (as in Python) its indentation
                # must not open or close a block.
                if self.pos < len(self.text) and self.text[self.pos] == "#":
                    self._skip_comment()
                    continue
                tokens.extend(self._handle_indentation(indent_level))
                self.at_line_start = False
                continue

            # Comments run to the end of the line
            if char == "#":
                self._skip_comment()
                continue

            # Skip whitespace
            if char.isspace() and char != "\n":
                self._advance()
                continue

            # Handle different token types
            if char == "(":
                tokens.append(Token(TokenType.LEFT_PAREN, "(", self.line))
                self._advance()

            elif char == ")":
                tokens.append(Token(TokenType.RIGHT_PAREN, ")", self.line))
                self._advance()

            elif char == '"':
                tokens.append(self._tokenize_string())

            elif char.isdigit():
                tokens.append(self._tokenize_number())

            elif char.isalpha():
                tokens.append(self._tokenize_identifier())

            elif char in ARITHMETIC_OPERATOR_CHARS:
                # Always single-character, so `* -1` lexes as two operators.
                tokens.append(Token(TokenType.OPERATOR, char, self.line))
                self._advance()

            elif char in COMPARISON_OPERATOR_CHARS:
                tokens.append(self._tokenize_operator())

            elif char == "\n":
                self.line += 1
                self.at_line_start = True
                self._advance()

            else:
                raise LexerError(f"Unexpected character '{char}' at line {self.line}", self.line)

        # Handle any remaining dedents at EOF
        tokens.extend(self._handle_indentation(0))
        tokens.append(Token(TokenType.EOF, "", self.line))

        return tokens

    def _advance(self):
        """Advance position"""
        self.pos += 1

    def _count_indent(self) -> int:
        """Count the indentation level of the current line.

        Blank lines are consumed here too, so the count is that of the next line with content.
        """
        indent = 0
        while self.pos < len(self.text) and self.text[self.pos].isspace():
            if self.text[self.pos] == " ":
                indent += 1
            elif self.text[self.pos] == "\t":
                indent += 8  # Convert tabs to spaces (standard Python behavior)
            elif self.text[self.pos] == "\n":
                indent = 0  # a blank line: restart the count on the next line
                self.line += 1
            self._advance()
        return indent

    def _skip_comment(self):
        """Advance to the newline that ends the current comment (the newline itself is kept)."""
        while self.pos < len(self.text) and self.text[self.pos] != "\n":
            self._advance()

    def _handle_indentation(self, indent_level: int) -> List[Token]:
        """Handle indentation changes and emit INDENT/DEDENT tokens"""
        tokens = []
        current_indent = self.indent_stack[-1]

        if indent_level > current_indent:
            tokens.append(Token(TokenType.INDENT, "", self.line))
            self.indent_stack.append(indent_level)

        while indent_level < current_indent:
            tokens.append(Token(TokenType.DEDENT, "", self.line))
            self.indent_stack.pop()
            current_indent = self.indent_stack[-1]

            if indent_level > current_indent:
                raise LexerError(f"Invalid dedent at line {self.line}", self.line)

        return tokens

    def _tokenize_string(self) -> Token:
        """Tokenize a string literal"""
        start_pos = self.pos
        self._advance()  # Skip opening quote

        while self.pos < len(self.text) and self.text[self.pos] != '"':
            if self.text[self.pos] == "\n":
                raise LexerError("Unterminated string at line {self.line}", self.line)
            self._advance()

        if self.pos >= len(self.text):
            raise LexerError("Unterminated string at line {self.line}", self.line)

        self._advance()  # Skip closing quote
        value = self.text[start_pos : self.pos]
        return Token(TokenType.STRING, value, self.line)

    def _tokenize_number(self) -> Token:
        """Tokenize a number"""
        start_pos = self.pos
        while self.pos < len(self.text) and (self.text[self.pos].isdigit() or self.text[self.pos] == "."):
            self._advance()

        value = self.text[start_pos : self.pos]
        return Token(TokenType.NUMBER, value, self.line)

    def _tokenize_identifier(self) -> Token:
        """Tokenize an identifier"""
        start_pos = self.pos
        while self.pos < len(self.text) and (self.text[self.pos].isalnum() or self.text[self.pos] == "_"):
            self._advance()

        value = self.text[start_pos : self.pos]
        if value in VALID_IDENTIFIERS:
            return Token(TokenType.IDENTIFIER, value, self.line)
        else:
            raise LexerError(f"Invalid identifier '{value}' at line {self.line}", self.line)

    def _tokenize_operator(self) -> Token:
        """Tokenize a comparison operator: >, <, ==, >=, <=, !="""
        start_pos = self.pos
        while self.pos < len(self.text) and self.text[self.pos] in COMPARISON_OPERATOR_CHARS:
            self._advance()

        value = self.text[start_pos : self.pos]
        return Token(TokenType.OPERATOR, value, self.line)
