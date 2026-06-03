class BrokerException(Exception):
    """Base class for broker exceptions."""


class BrokerInitializationException(BrokerException):
    """Exception raised when broker initialization fails."""


class InsufficientFundsException(BrokerException):
    """Exception raised when an order's cost exceeds available cash."""
