"""Accounts the ledger posts against."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .postings import Posting, Side
from .protocol import MoneyLike, validate_currency

__all__ = ["Account", "AccountType"]


class AccountType(Enum):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    REVENUE = "revenue"
    EXPENSE = "expense"

    @property
    def normal_side(self) -> Side:
        if self in (AccountType.ASSET, AccountType.EXPENSE):
            return Side.DEBIT
        return Side.CREDIT


@dataclass(frozen=True, slots=True)
class Account:
    """A named place to post against, fixed to one currency."""

    account_id: str
    type: AccountType
    currency: str
    name: str = ""

    def __post_init__(self) -> None:
        if not self.account_id:
            raise ValueError("account_id must not be empty")
        object.__setattr__(self, "currency", validate_currency(self.currency))

    @property
    def normal_side(self) -> Side:
        return self.type.normal_side

    def debit(self, amount: MoneyLike) -> Posting:
        return self._posting(amount, Side.DEBIT)

    def credit(self, amount: MoneyLike) -> Posting:
        return self._posting(amount, Side.CREDIT)

    def _posting(self, amount: MoneyLike, side: Side) -> Posting:
        if amount.currency != self.currency:
            raise ValueError(
                f"account {self.account_id} holds {self.currency}, got {amount.currency}"
            )
        return Posting(self.account_id, amount, side)
