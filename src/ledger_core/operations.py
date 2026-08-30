"""Operations: a write asked for twice happens once.

Deposit callbacks arrive more than once, requests are retried, queues redeliver
what they already delivered. The entry cannot make that safe on its own: a
second attempt at the same entry id is refused, and a refusal is not the same
as knowing the first attempt landed. The key the write was asked for under can.
Under it the first attempt writes, and every attempt after returns what the
first one wrote.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Iterator

from .entries import Entry
from .journal import Journal

__all__ = [
    "Operation",
    "OperationMismatch",
    "Operations",
    "UnknownOperation",
    "fingerprint_of",
]

_FIELD = "\x1f"
_RECORD = "\x1e"


class OperationMismatch(ValueError):
    """Raised when an operation key comes back carrying different work."""


class UnknownOperation(KeyError):
    """Raised when an operation key was never recorded."""


def fingerprint_of(entries: Iterable[Entry]) -> str:
    """A stable digest of what an operation asked the journal to write."""
    digest = hashlib.sha256()
    for entry in entries:
        parts = [
            entry.entry_id,
            entry.occurred_at.isoformat(),
            entry.memo,
            entry.corrects or "",
        ]
        for posting in entry.postings:
            parts += [
                posting.account_id,
                # normalized, so a retry that rebuilt 25.0 as 25.00 is the same work
                str(posting.amount.amount.normalize()),
                posting.currency,
                posting.side.value,
            ]
        for name in sorted(entry.metadata):
            parts += [name, entry.metadata[name]]
        digest.update(_FIELD.join(parts).encode("utf-8"))
        digest.update(_RECORD.encode("utf-8"))
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class Operation:
    """What one key wrote, kept so the same key never writes it again."""

    key: str
    entry_ids: tuple[str, ...]
    fingerprint: str
    as_of: datetime | None = None

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("operation key must not be empty")
        entry_ids = tuple(self.entry_ids)
        if not entry_ids:
            raise ValueError("an operation writes at least one entry")
        if not self.fingerprint:
            raise ValueError("fingerprint must not be empty")
        object.__setattr__(self, "entry_ids", entry_ids)

    @classmethod
    def of(cls, key: str, entries: tuple[Entry, ...]) -> Operation:
        """The record of a batch that has just been written under a key."""
        return cls(
            key=key,
            entry_ids=tuple(entry.entry_id for entry in entries),
            fingerprint=fingerprint_of(entries),
            as_of=entries[-1].occurred_at,
        )

    def __str__(self) -> str:
        return f"{self.key}: {', '.join(self.entry_ids)}"


class Operations:
    """The keys a journal has already been written under.

    A record is a plain value: it can be stored beside the journal and handed
    back later, so a key settled in one process stays settled in the next.
    """

    __slots__ = ("_journal", "_records")

    def __init__(self, journal: Journal, records: Iterable[Operation] = ()) -> None:
        self._journal = journal
        self._records: dict[str, Operation] = {}
        for record in records:
            self.restore(record)

    @property
    def journal(self) -> Journal:
        return self._journal

    def post(self, key: str, entry: Entry) -> Entry:
        """Write an entry under a key, once however often it is asked for."""
        return self.post_many(key, (entry,))[0]

    def post_many(self, key: str, entries: Iterable[Entry]) -> tuple[Entry, ...]:
        """Write a batch under one key, under the journal's rule: all or none.

        A key already settled writes nothing and answers with the entries it
        wrote the first time. Applying it to different work is refused: the key
        says which write this is, so two writes cannot share one.
        """
        if not key:
            raise ValueError("operation key must not be empty")
        batch = tuple(entries)
        if not batch:
            raise ValueError("an operation writes at least one entry")
        mark = fingerprint_of(batch)
        known = self._records.get(key)
        if known is not None:
            if known.fingerprint != mark:
                raise OperationMismatch(
                    f"operation {key} already wrote {', '.join(known.entry_ids)}; "
                    f"the same key cannot carry different entries"
                )
            return self.entries(key)
        written = self._journal.extend(batch)
        self._records[key] = Operation.of(key, written)
        return written

    def entries(self, key: str) -> tuple[Entry, ...]:
        """What was written under a key, read back from the journal."""
        record = self.operation(key)
        return tuple(self._journal.entry(entry_id) for entry_id in record.entry_ids)

    def operation(self, key: str) -> Operation:
        try:
            return self._records[key]
        except KeyError:
            raise UnknownOperation(key) from None

    def operations(self) -> tuple[Operation, ...]:
        """Every key settled so far, ready to be stored and handed back."""
        return tuple(self._records[key] for key in sorted(self._records))

    def restore(self, record: Operation) -> Operation:
        """Take back a record written earlier, here or in another process.

        A record naming entries the journal does not hold describes some other
        journal, and is refused rather than settling a key nothing was written
        under.
        """
        missing = [entry_id for entry_id in record.entry_ids if entry_id not in self._journal]
        if missing:
            raise OperationMismatch(
                f"operation {record.key} claims entries the journal does not hold: "
                f"{', '.join(missing)}"
            )
        known = self._records.get(record.key)
        if known is None:
            self._records[record.key] = record
            return record
        if known.fingerprint != record.fingerprint:
            raise OperationMismatch(
                f"operation {record.key} is already recorded against "
                f"{', '.join(known.entry_ids)}"
            )
        return known

    def __len__(self) -> int:
        return len(self._records)

    def __iter__(self) -> Iterator[Operation]:
        return iter(self.operations())

    def __contains__(self, key: object) -> bool:
        return key in self._records
