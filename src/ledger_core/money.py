"""Monetary amounts that carry their own currency."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

__all__ = ["CurrencyMismatch", "Money", "validate_currency"]


class CurrencyMismatch(ValueError):
    """Raised when amounts in different currencies are combined."""


def validate_currency(code: str) -> str:
    if not isinstance(code, str) or len(code) != 3 or not code.isalpha() or not code.isupper():
        raise ValueError(f"currency must be a 3-letter uppercase ISO 4217 code, got {code!r}")
    return code


@dataclass(frozen=True, slots=True)
class Money:
    """An exact amount in a single currency."""

    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        if isinstance(self.amount, float):
            raise TypeError("float amounts are not exact; pass Decimal, int or str")
        object.__setattr__(self, "amount", Decimal(self.amount))
        object.__setattr__(self, "currency", validate_currency(self.currency))

    @classmethod
    def zero(cls, currency: str) -> Money:
        return cls(Decimal(0), currency)

    @property
    def is_zero(self) -> bool:
        return self.amount == 0

    @property
    def is_positive(self) -> bool:
        return self.amount > 0

    def _require_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatch(f"cannot combine {self.currency} with {other.currency}")

    def __add__(self, other: Money) -> Money:
        self._require_same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._require_same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"
