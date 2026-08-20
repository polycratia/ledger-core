"""The journal: entries land whole, or not at all."""

from __future__ import annotations

from typing import Iterable, Iterator

from .entries import Entry, EntryPosting

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
    """

    __slots__ = ("_entries", "_by_id")

    def __init__(self, entries: Iterable[Entry] = ()) -> None:
        self._entries: list[Entry] = []
        self._by_id: dict[str, Entry] = {}
        self.extend(entries)

    def append(self, entry: Entry) -> Entry:
        self.extend((entry,))
        return entry

    def extend(self, entries: Iterable[Entry]) -> tuple[Entry, ...]:
        """Write a batch under one rule: all of it, or none of it."""
        batch = tuple(entries)
        incoming: dict[str, Entry] = {}
        for entry in batch:
            if entry.entry_id in self._by_id or entry.entry_id in incoming:
                raise DuplicateEntry(f"entry {entry.entry_id} is already written")
            incoming[entry.entry_id] = entry
        for entry in batch:
            corrects = entry.corrects
            if corrects is not None and corrects not in self._by_id and corrects not in incoming:
                raise UnknownEntry(
                    f"entry {entry.entry_id} corrects {corrects}, which is not written"
                )
        self._entries.extend(batch)
        self._by_id.update(incoming)
        return batch

    def entry(self, entry_id: str) -> Entry:
        try:
            return self._by_id[entry_id]
        except KeyError:
            raise UnknownEntry(entry_id) from None

    @property
    def entries(self) -> tuple[Entry, ...]:
        return tuple(self._entries)

    def postings(self, account_id: str | None = None) -> tuple[EntryPosting, ...]:
        """Every posting written so far, each stamped with its transaction."""
        return tuple(
            stamped
            for entry in self._entries
            for stamped in entry.stamped()
            if account_id is None or stamped.account_id == account_id
        )

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[Entry]:
        return iter(self._entries)

    def __contains__(self, entry_id: object) -> bool:
        return entry_id in self._by_id
