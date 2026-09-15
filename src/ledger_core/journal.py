"""The journal: entries land whole, or not at all."""

from __future__ import annotations

from typing import Iterable, Iterator

from .entries import Entry, EntryPosting
from .storage import InMemoryStorage, Storage

__all__ = ["DuplicateEntry", "Journal", "UnknownEntry"]


class DuplicateEntry(ValueError):
    """Raised when an entry id has already been written."""


class UnknownEntry(KeyError):
    """Raised when an entry id is not in the journal."""


class Journal:
    """An append-only record of entries.

    An entry balances before it can be built at all, and a write is checked in
    full before any of it is kept, so the sides of a movement never become
    visible one at a time.

    Where the entries are kept is a :class:`~ledger_core.storage.Storage`, and
    a journal handed none builds an in-memory one for itself.
    """

    __slots__ = ("_storage",)

    def __init__(self, entries: Iterable[Entry] = (), *, storage: Storage | None = None) -> None:
        self._storage: Storage = storage if storage is not None else InMemoryStorage()
        self.extend(entries)

    @property
    def storage(self) -> Storage:
        """The store this journal and its projections keep their records in."""
        return self._storage

    def append(self, entry: Entry) -> Entry:
        self.extend((entry,))
        return entry

    def extend(self, entries: Iterable[Entry]) -> tuple[Entry, ...]:
        """Write a batch under one rule: all of it, or none of it."""
        batch = tuple(entries)
        incoming: dict[str, Entry] = {}
        for entry in batch:
            if self._storage.has_entry(entry.entry_id) or entry.entry_id in incoming:
                raise DuplicateEntry(f"entry {entry.entry_id} is already written")
            incoming[entry.entry_id] = entry
        for entry in batch:
            corrects = entry.corrects
            if (
                corrects is not None
                and not self._storage.has_entry(corrects)
                and corrects not in incoming
            ):
                raise UnknownEntry(
                    f"entry {entry.entry_id} corrects {corrects}, which is not written"
                )
        self._storage.append_entries(batch)
        return batch

    def entry(self, entry_id: str) -> Entry:
        found = self._storage.entry(entry_id)
        if found is None:
            raise UnknownEntry(entry_id)
        return found

    @property
    def entries(self) -> tuple[Entry, ...]:
        return self._storage.entries()

    def since(self, index: int) -> tuple[Entry, ...]:
        """The entries written from a position onward.

        Nothing before the head is ever rewritten, so a position taken earlier
        keeps meaning what it meant: everything up to it has been accounted
        for, everything from it has not.
        """
        if index < 0:
            raise ValueError("index must not be negative")
        return self._storage.entries_since(index)

    def postings(self, account_id: str | None = None) -> tuple[EntryPosting, ...]:
        """Every posting written so far, each stamped with its transaction."""
        return tuple(
            stamped
            for entry in self._storage.entries()
            for stamped in entry.stamped()
            if account_id is None or stamped.account_id == account_id
        )

    def __len__(self) -> int:
        return self._storage.entry_count()

    def __iter__(self) -> Iterator[Entry]:
        return iter(self._storage.entries())

    def __contains__(self, entry_id: object) -> bool:
        return isinstance(entry_id, str) and self._storage.has_entry(entry_id)
