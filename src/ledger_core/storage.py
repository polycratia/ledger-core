"""Where the ledger keeps what it has been told.

The core keeps no records of its own. It asks a store for the entries,
snapshots, holds and operation records it needs, and hands back the ones it
makes, so the same ledger runs over a dict in a test and over rows in a
database in production without a line of it changing.

:class:`Storage` is that contract. :class:`InMemoryStorage` is the
implementation it is read against: what a journal builds for itself when it is
handed none, and the double a test runs on.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .balances import Snapshot
    from .entries import Entry
    from .holds import Hold
    from .operations import Operation

__all__ = ["InMemoryStorage", "Storage"]


@runtime_checkable
class Storage(Protocol):
    """The records a ledger keeps, and the only way it reaches them.

    Entries are append-only and read by position or by id. Snapshots, holds and
    operation records are values written under a key and replaced by a newer
    one. Reads answer with what is there, or ``None`` — a missing record is not
    an error here; which refusal it deserves is the caller's to decide.

    Writes that belong together are made under :meth:`atomic`, which is where a
    check and the write it guards become one step.
    """

    def atomic(self) -> AbstractContextManager[None]:
        """One unit of writing: everything under the block, or none of it.

        A guarded write is two steps — asking whether the funds are free or the
        key already settled, and then writing — and a refusal in the second must
        not leave the first behind. Writes made under the block land together;
        leaving it by an exception undoes them. A block opened inside another
        joins it rather than settling on its own, so a guarded write called from
        inside a wider one widens to it.
        """

    def append_entries(self, entries: Sequence[Entry]) -> None:
        """Keep a batch that has already been checked, in the order given."""

    def entry(self, entry_id: str) -> Entry | None: ...

    def has_entry(self, entry_id: str) -> bool: ...

    def entries(self) -> tuple[Entry, ...]: ...

    def entries_since(self, index: int) -> tuple[Entry, ...]:
        """The entries kept from a position onward."""

    def entry_count(self) -> int: ...

    def put_snapshot(self, snapshot: Snapshot) -> None: ...

    def snapshot(self, account_id: str) -> Snapshot | None: ...

    def snapshots(self) -> tuple[Snapshot, ...]: ...

    def put_hold(self, hold: Hold) -> None: ...

    def hold(self, hold_id: str) -> Hold | None: ...

    def holds(self) -> tuple[Hold, ...]: ...

    def put_operation(self, operation: Operation) -> None: ...

    def operation(self, key: str) -> Operation | None: ...

    def operations(self) -> tuple[Operation, ...]: ...


class InMemoryStorage:
    """The reference store: dicts, for as long as the process lives.

    Reads answer with tuples, so what a caller holds cannot be written back
    into the store behind its own interface.

    :meth:`atomic` keeps the records as they stood and puts them back if the
    block is left by an exception, and admits one writer at a time: for a store
    with no transaction of its own, that is what makes a check and the write it
    guards a single step.
    """

    __slots__ = (
        "_entries",
        "_by_id",
        "_snapshots",
        "_holds",
        "_operations",
        "_lock",
        "_depth",
    )

    def __init__(self) -> None:
        self._entries: list[Entry] = []
        self._by_id: dict[str, Entry] = {}
        self._snapshots: dict[str, Snapshot] = {}
        self._holds: dict[str, Hold] = {}
        self._operations: dict[str, Operation] = {}
        self._lock = threading.RLock()
        self._depth = 0

    @contextmanager
    def atomic(self) -> Iterator[None]:
        """Writes under this block land together, or not at all."""
        with self._lock:
            if self._depth:
                self._depth += 1
                try:
                    yield
                finally:
                    self._depth -= 1
                return
            # records are values, so a shallow copy of each shelf is a whole one
            kept = (
                list(self._entries),
                dict(self._by_id),
                dict(self._snapshots),
                dict(self._holds),
                dict(self._operations),
            )
            self._depth = 1
            try:
                yield
            except BaseException:
                (
                    self._entries,
                    self._by_id,
                    self._snapshots,
                    self._holds,
                    self._operations,
                ) = kept
                raise
            finally:
                self._depth = 0

    def append_entries(self, entries: Sequence[Entry]) -> None:
        for entry in entries:
            self._entries.append(entry)
            self._by_id[entry.entry_id] = entry

    def entry(self, entry_id: str) -> Entry | None:
        return self._by_id.get(entry_id)

    def has_entry(self, entry_id: str) -> bool:
        return entry_id in self._by_id

    def entries(self) -> tuple[Entry, ...]:
        return tuple(self._entries)

    def entries_since(self, index: int) -> tuple[Entry, ...]:
        return tuple(self._entries[index:])

    def entry_count(self) -> int:
        return len(self._entries)

    def put_snapshot(self, snapshot: Snapshot) -> None:
        self._snapshots[snapshot.account_id] = snapshot

    def snapshot(self, account_id: str) -> Snapshot | None:
        return self._snapshots.get(account_id)

    def snapshots(self) -> tuple[Snapshot, ...]:
        return tuple(self._snapshots[key] for key in sorted(self._snapshots))

    def put_hold(self, hold: Hold) -> None:
        self._holds[hold.hold_id] = hold

    def hold(self, hold_id: str) -> Hold | None:
        return self._holds.get(hold_id)

    def holds(self) -> tuple[Hold, ...]:
        return tuple(self._holds.values())

    def put_operation(self, operation: Operation) -> None:
        self._operations[operation.key] = operation

    def operation(self, key: str) -> Operation | None:
        return self._operations.get(key)

    def operations(self) -> tuple[Operation, ...]:
        return tuple(self._operations[key] for key in sorted(self._operations))

    def __str__(self) -> str:
        return (
            f"in-memory store: {len(self._entries)} entries, "
            f"{len(self._holds)} holds, {len(self._operations)} operations"
        )
