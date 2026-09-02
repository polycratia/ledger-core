"""The rules a write is refused for, each naming what it keeps true.

A ledger is worth reading only if what it promises holds everywhere, not where
the caller remembered to check. Each rule below is enforced at the one place it
can be broken, refuses the write instead of recording it, and says which promise
refused it.
"""

from __future__ import annotations

from typing import ClassVar

__all__ = [
    "ClosedAccount",
    "InsufficientFunds",
    "InvariantViolated",
    "UnbalancedEntry",
]


class InvariantViolated(ValueError):
    """A refused write, carrying the promise that refused it."""

    protects: ClassVar[str] = "a rule of the ledger"

    def __init__(self, detail: str) -> None:
        super().__init__(f"{detail} \u2014 protects: {self.protects}")
        self.detail = detail


class UnbalancedEntry(InvariantViolated):
    """Raised when debits and credits of an entry do not cancel out."""

    protects: ClassVar[str] = (
        "every movement carries both of its sides, so no write creates or destroys money"
    )


class InsufficientFunds(InvariantViolated):
    """Raised when a reservation or a movement takes more than is available."""

    protects: ClassVar[str] = (
        "available balances stay at or above zero, so the same funds are never spent twice"
    )


class ClosedAccount(InvariantViolated):
    """Raised when an account that has been closed is posted to."""

    protects: ClassVar[str] = (
        "a closed account is final, so its balance cannot move after it is settled"
    )
