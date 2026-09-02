"""Entries: balanced sets of postings that are written once and never changed."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Mapping

from .accounts import Account
from .invariants import UnbalancedEntry
from .postings import Posting, Side
from .protocol import MoneyLike

__all__ = ["Entry", "EntryPosting", "UnbalancedEntry"]


@dataclass(frozen=True, slots=True)
class EntryPosting:
    """A posting together with the transaction it was written under."""

    entry_id: str
    occurred_at: datetime
    posting: Posting

    @property
    def account_id(self) -> str:
        return self.posting.account_id

    @property
    def amount(self) -> MoneyLike:
        return self.posting.amount

    @property
    def side(self) -> Side:
        return self.posting.side

    @property
    def currency(self) -> str:
        return self.posting.currency

    @property
    def signed_amount(self) -> MoneyLike:
        return self.posting.signed_amount

    def __str__(self) -> str:
        return f"{self.entry_id}: {self.posting}"


@dataclass(frozen=True, slots=True)
class Entry:
    """An immutable double-entry record. Corrections are new entries."""

    entry_id: str
    occurred_at: datetime
    postings: tuple[Posting, ...]
    memo: str = ""
    corrects: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.entry_id:
            raise ValueError("entry_id must not be empty")
        postings = tuple(self.postings)
        if len(postings) < 2:
            raise ValueError("an entry needs at least two postings")
        object.__setattr__(self, "postings", postings)
        object.__setattr__(self, "metadata", dict(self.metadata))
        if self.corrects == self.entry_id:
            raise ValueError("an entry cannot correct itself")

        net: dict[str, Decimal] = {}
        for posting in postings:
            signed = posting.signed_amount
            net[signed.currency] = net.get(signed.currency, Decimal(0)) + signed.amount
        drifting = sorted(currency for currency, total in net.items() if total != 0)
        if drifting:
            raise UnbalancedEntry(
                f"entry {self.entry_id} does not balance in: {', '.join(drifting)}"
            )

    @classmethod
    def transfer(
        cls,
        *,
        entry_id: str,
        occurred_at: datetime,
        debit: Account,
        credit: Account,
        amount: MoneyLike,
        memo: str = "",
        corrects: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> Entry:
        """One movement, both of its sides, under a single entry id."""
        return cls(
            entry_id=entry_id,
            occurred_at=occurred_at,
            postings=(debit.debit(amount), credit.credit(amount)),
            memo=memo,
            corrects=corrects,
            metadata=metadata or {},
        )

    @property
    def currencies(self) -> frozenset[str]:
        return frozenset(posting.currency for posting in self.postings)

    def debits(self) -> tuple[Posting, ...]:
        return tuple(p for p in self.postings if p.side is Side.DEBIT)

    def credits(self) -> tuple[Posting, ...]:
        return tuple(p for p in self.postings if p.side is Side.CREDIT)

    def postings_for(self, account_id: str) -> tuple[Posting, ...]:
        return tuple(p for p in self.postings if p.account_id == account_id)

    def stamped(self) -> tuple[EntryPosting, ...]:
        """The postings of this entry, each carrying the transaction it shares."""
        return tuple(
            EntryPosting(self.entry_id, self.occurred_at, posting) for posting in self.postings
        )

    def reversal(self, *, entry_id: str, occurred_at: datetime, memo: str = "") -> Entry:
        """Build the correcting entry that cancels this one."""
        return Entry(
            entry_id=entry_id,
            occurred_at=occurred_at,
            postings=tuple(p.reversed() for p in self.postings),
            memo=memo or f"reversal of {self.entry_id}",
            corrects=self.entry_id,
        )
