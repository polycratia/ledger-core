"""A stand-in money type, kept only so the ledger runs and is testable alone.

Anything satisfying :class:`~ledger_core.protocol.MoneyLike` can be posted
instead; ``pip install "ledger-core[crypto]"`` pulls the dedicated one.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from functools import total_ordering

from .protocol import CurrencyMismatch, validate_currency

__all__ = ["Money"]


@total_ordering
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

    def __lt__(self, other: Money) -> bool:
        self._require_same_currency(other)
        return self.amount < other.amount

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"
