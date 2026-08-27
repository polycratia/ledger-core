"""Balances: what the entries add up to, kept fast by snapshots.

A balance is not a column the ledger edits. It is a fold over the journal, so
reading one writes nothing and two readers cannot race each other into losing
a movement. A snapshot records only how far a fold already got: keeping a
stale one costs the replay of the entries it is missing, never a wrong answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable

from .accounts import Account
from .entries import Entry
from .journal import Journal
from .postings import Side
from .protocol import CurrencyMismatch, validate_currency

__all__ = ["Balances", "Snapshot", "SnapshotMismatch", "effect_on"]


class SnapshotMismatch(ValueError):
    """Raised when a snapshot does not describe the journal it is used against."""


def effect_on(entry: Entry, account: Account) -> Decimal:
    """What one entry does to an account, counted in its normal direction."""
    total = Decimal(0)
    for posting in entry.postings_for(account.account_id):
        if posting.currency != account.currency:
            raise CurrencyMismatch(
                f"account {account.account_id} holds {account.currency}, "
                f"entry {entry.entry_id} posts {posting.currency}"
            )
        total += posting.signed_amount.amount
    return total if account.normal_side is Side.DEBIT else -total


@dataclass(frozen=True, slots=True)
class Snapshot:
    """A balance together with the stretch of journal it was folded from."""

    account_id: str
    currency: str
    total: Decimal
    through: int = 0
    as_of: datetime | None = None

    def __post_init__(self) -> None:
        if not self.account_id:
            raise ValueError("account_id must not be empty")
        if isinstance(self.total, float):
            raise TypeError("float totals are not exact; pass Decimal, int or str")
        if self.through < 0:
            raise ValueError("through must not be negative")
        object.__setattr__(self, "total", Decimal(self.total))
        object.__setattr__(self, "currency", validate_currency(self.currency))

    @classmethod
    def empty(cls, account: Account) -> Snapshot:
        """Where an account stands before a single entry is counted."""
        return cls(account.account_id, account.currency, Decimal(0))

    def __str__(self) -> str:
        return (
            f"{self.account_id}: {self.total} {self.currency} "
            f"through {self.through} entries"
        )


class Balances:
    """The balance projection of a journal.

    Amounts come back as :class:`~decimal.Decimal` in the account's own
    currency: the ledger owns no money type, so it cannot mint the zero an
    empty balance would need.
    """

    __slots__ = ("_journal", "_snapshots")

    def __init__(self, journal: Journal, snapshots: Iterable[Snapshot] = ()) -> None:
        self._journal = journal
        self._snapshots: dict[str, Snapshot] = {}
        for snapshot in snapshots:
            self.restore(snapshot)

    @property
    def journal(self) -> Journal:
        return self._journal

    def balance(self, account: Account) -> Decimal:
        """What the account holds, counted in its normal direction."""
        return self.snapshot(account).total

    def snapshot(self, account: Account) -> Snapshot:
        """Fold the journal to its head, resuming from what was folded before."""
        base = self._base_for(account)
        pending = self._journal.since(base.through)
        total = base.total
        as_of = base.as_of
        for entry in pending:
            total += effect_on(entry, account)
            as_of = entry.occurred_at
        fresh = Snapshot(
            account_id=account.account_id,
            currency=account.currency,
            total=total,
            through=base.through + len(pending),
            as_of=as_of,
        )
        self._snapshots[account.account_id] = fresh
        return fresh

    def snapshots(self) -> tuple[Snapshot, ...]:
        """Everything folded so far, ready to be stored and handed back."""
        return tuple(self._snapshots[key] for key in sorted(self._snapshots))

    def restore(self, snapshot: Snapshot) -> Snapshot:
        """Resume from a snapshot folded earlier, here or in another process.

        A snapshot is only ever replaced by one that reaches further, so a
        shorter or older one cannot walk a balance backwards.
        """
        if snapshot.through > len(self._journal):
            raise SnapshotMismatch(
                f"snapshot of {snapshot.account_id} counts {snapshot.through} entries, "
                f"the journal holds {len(self._journal)}"
            )
        known = self._snapshots.get(snapshot.account_id)
        if known is None:
            self._snapshots[snapshot.account_id] = snapshot
            return snapshot
        if known.currency != snapshot.currency:
            raise CurrencyMismatch(
                f"account {snapshot.account_id} is folded in {known.currency}, "
                f"snapshot carries {snapshot.currency}"
            )
        if known.through >= snapshot.through:
            return known
        self._snapshots[snapshot.account_id] = snapshot
        return snapshot

    def at(self, account: Account, when: datetime) -> Decimal:
        """The balance as of a moment, replayed in full.

        Entries may be written in any order, so a position in the journal says
        nothing about a point in time: this one cannot use snapshots.
        """
        total = Decimal(0)
        for entry in self._journal:
            if entry.occurred_at <= when:
                total += effect_on(entry, account)
        return total

    def _base_for(self, account: Account) -> Snapshot:
        known = self._snapshots.get(account.account_id)
        if known is None:
            return Snapshot.empty(account)
        if known.currency != account.currency:
            raise CurrencyMismatch(
                f"account {account.account_id} holds {account.currency}, "
                f"its snapshot is folded in {known.currency}"
            )
        if known.through > len(self._journal):
            raise SnapshotMismatch(
                f"snapshot of {account.account_id} counts {known.through} entries, "
                f"the journal holds {len(self._journal)}"
            )
        return known

    def __len__(self) -> int:
        return len(self._snapshots)

    def __contains__(self, account_id: object) -> bool:
        return account_id in self._snapshots
