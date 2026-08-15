"""Postings: the immutable legs of a ledger entry."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .money import Money

__all__ = ["Posting", "Side"]


class Side(Enum):
    DEBIT = "debit"
    CREDIT = "credit"

    @property
    def opposite(self) -> Side:
        return Side.CREDIT if self is Side.DEBIT else Side.DEBIT


@dataclass(frozen=True, slots=True)
class Posting:
    """A single debit or credit against one account."""

    account_id: str
    amount: Money
    side: Side

    def __post_init__(self) -> None:
        if not self.account_id:
            raise ValueError("account_id must not be empty")
        if not self.amount.is_positive:
            raise ValueError(f"posting amount must be positive, got {self.amount}")

    @property
    def currency(self) -> str:
        return self.amount.currency

    @property
    def signed_amount(self) -> Money:
        return self.amount if self.side is Side.DEBIT else -self.amount

    def reversed(self) -> Posting:
        return Posting(self.account_id, self.amount, self.side.opposite)

    def __str__(self) -> str:
        return f"{self.side.value} {self.amount} {self.account_id}"
