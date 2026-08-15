"""Entries: balanced sets of postings that are written once and never changed."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Iterable, Mapping

from .postings import Posting, Side

__all__ = ["Entry", "UnbalancedEntry"]


class UnbalancedEntry(ValueError):
    """Raised when debits and credits of an entry do not cancel out."""


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

    @property
    def currencies(self) -> frozenset[str]:
        return frozenset(posting.currency for posting in self.postings)

    def debits(self) -> tuple[Posting, ...]:
        return tuple(p for p in self.postings if p.side is Side.DEBIT)

    def credits(self) -> tuple[Posting, ...]:
        return tuple(p for p in self.postings if p.side is Side.CREDIT)

    def postings_for(self, account_id: str) -> tuple[Posting, ...]:
        return tuple(p for p in self.postings if p.account_id == account_id)

    def reversal(self, *, entry_id: str, occurred_at: datetime, memo: str = "") -> Entry:
        """Build the correcting entry that cancels this one."""
        return Entry(
            entry_id=entry_id,
            occurred_at=occurred_at,
            postings=tuple(p.reversed() for p in self.postings),
            memo=memo or f"reversal of {self.entry_id}",
            corrects=self.entry_id,
        )


def _unused(postings: Iterable[Posting]) -> None:
    raise NotImplementedError
