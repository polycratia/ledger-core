"""The money contract the ledger relies on.

The ledger owns no money type: it accepts any value that carries an exact
amount, names the asset it is denominated in and supports the arithmetic
declared here. A dedicated implementation lives in a package of its own:
https://github.com/polycratia/cryptomoney
"""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol, TypeVar, runtime_checkable

__all__ = ["CurrencyMismatch", "MoneyLike", "validate_currency"]


class CurrencyMismatch(ValueError):
    """Raised when amounts in different currencies are combined."""


def validate_currency(code: str) -> str:
    if not isinstance(code, str) or len(code) != 3 or not code.isalpha() or not code.isupper():
        raise ValueError(f"currency must be a 3-letter uppercase ISO 4217 code, got {code!r}")
    return code


M = TypeVar("M", bound="MoneyLike")


@runtime_checkable
class MoneyLike(Protocol):
    """An exact amount tied to the asset it is denominated in."""

    @property
    def amount(self) -> Decimal: ...

    @property
    def currency(self) -> str: ...

    def __add__(self: M, other: M) -> M: ...

    def __sub__(self: M, other: M) -> M: ...

    def __neg__(self: M) -> M: ...

    def __lt__(self: M, other: M) -> bool: ...
