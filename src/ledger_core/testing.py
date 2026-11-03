"""The contract a store answers, as a suite any backend can run.

:class:`~ledger_core.storage.Storage` says what the ledger asks of a store;
this is where a store is held to it. Subclass :class:`StorageConformance`, say
which store it builds, and pytest runs the whole contract against yours::

    from ledger_core.testing import StorageConformance

    class TestMyStorage(StorageConformance):
        def new_storage(self):
            return MyStorage()

Only what the ledger relies on is checked, so a backend keeps its records
however it likes as long as it answers the same questions the same way. Records
are compared by value and never by identity: a store that rebuilds one from a
row conforms exactly as far as one that hands back the object it was given.

The suite is written against pytest, which ``pip install "ledger-core[testing]"``
pulls in. :func:`conformance_failures` runs the same checks outside a pytest
session and answers with the ones a store does not pass.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from ._stand_in import Money
from .accounts import Account, AccountType
from .balances import Snapshot
from .entries import Entry
from .holds import Hold
from .operations import Operation
from .storage import Storage

__all__ = [
    "StorageConformance",
    "a_hold",
    "a_snapshot",
    "an_entry",
    "an_operation",
    "checks",
    "conformance_failures",
]

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
CASH = Account("cash", AccountType.ASSET, "EUR")
CUSTOMER = Account("customer:42", AccountType.LIABILITY, "EUR")


def _eur(amount: str) -> Money:
    return Money(Decimal(amount), "EUR")


def an_entry(entry_id: str, amount: str = "25.00") -> Entry:
    """A balanced entry a store can be asked to keep."""
    return Entry.transfer(
        entry_id=entry_id,
        occurred_at=NOW,
        debit=CASH,
        credit=CUSTOMER,
        amount=_eur(amount),
    )


def a_snapshot(
    account_id: str = CUSTOMER.account_id, *, through: int = 0, total: str = "0"
) -> Snapshot:
    """A fold of an account, as a value written under its account id."""
    return Snapshot(
        account_id=account_id,
        currency=CUSTOMER.currency,
        side=CUSTOMER.normal_side,
        total=Decimal(total),
        through=through,
    )


def a_hold(hold_id: str, amount: str = "10.00") -> Hold:
    """An open reservation, as a value written under its hold id."""
    return Hold(
        hold_id=hold_id,
        account=CUSTOMER,
        amount=_eur(amount),
        placed_at=NOW,
    )


def an_operation(
    key: str, entry_ids: tuple[str, ...] = ("e-1",), fingerprint: str = "f-1"
) -> Operation:
    """A settled key, as a value written under that key."""
    return Operation(key=key, entry_ids=entry_ids, fingerprint=fingerprint)


@contextmanager
def _refused(kind: type[BaseException]) -> Iterator[None]:
    try:
        yield
    except kind:
        return
    raise AssertionError(f"the block should have been left by {kind.__name__}")


class StorageConformance:
    """The contract, as checks pytest runs against the store a subclass builds."""

    def new_storage(self) -> Storage:
        """A store with nothing in it. Override this; every check follows."""
        raise NotImplementedError("a conformance suite says which store it runs")

    @pytest.fixture
    def storage(self) -> Storage:
        return self.new_storage()

    def test_the_store_answers_everything_the_protocol_asks(self, storage: Storage) -> None:
        assert isinstance(storage, Storage), "the store is missing part of the contract"
        assert storage.entry_count() == 0
        assert len(storage.entries()) == 0
        assert len(storage.snapshots()) == 0
        assert len(storage.holds()) == 0
        assert len(storage.operations()) == 0

    def test_entries_come_back_in_the_order_they_were_appended(self, storage: Storage) -> None:
        storage.append_entries((an_entry("e-1"), an_entry("e-2", "5.00")))
        storage.append_entries((an_entry("e-3", "1.00"),))
        assert [entry.entry_id for entry in storage.entries()] == ["e-1", "e-2", "e-3"]
        assert storage.entry_count() == 3
        storage.append_entries(())
        assert storage.entry_count() == 3, "an empty batch keeps nothing"

    def test_an_entry_is_read_back_by_its_id(self, storage: Storage) -> None:
        written = an_entry("e-1")
        storage.append_entries((written,))
        assert storage.entry("e-1") == written
        assert storage.has_entry("e-1")

    def test_entries_from_a_position_past_the_end_are_empty(self, storage: Storage) -> None:
        storage.append_entries((an_entry("e-1"), an_entry("e-2", "5.00")))
        assert [entry.entry_id for entry in storage.entries_since(0)] == ["e-1", "e-2"]
        assert [entry.entry_id for entry in storage.entries_since(1)] == ["e-2"]
        assert len(storage.entries_since(2)) == 0, "a position at the head answers with nothing"
        assert len(storage.entries_since(9)) == 0, "a position past the head is empty, not an error"

    def test_an_unknown_key_is_answered_with_none(self, storage: Storage) -> None:
        assert storage.entry("e-404") is None
        assert not storage.has_entry("e-404")
        assert storage.snapshot("nobody") is None
        assert storage.hold("h-404") is None
        assert storage.operation("never settled") is None

    def test_a_snapshot_is_replaced_by_the_one_under_its_key(self, storage: Storage) -> None:
        folded = a_snapshot(through=1, total="25.00")
        storage.put_snapshot(folded)
        assert storage.snapshot(CUSTOMER.account_id) == folded
        further = a_snapshot(through=2, total="30.00")
        storage.put_snapshot(further)
        assert storage.snapshot(CUSTOMER.account_id) == further
        assert len(storage.snapshots()) == 1, "a key holds one snapshot, not a history of them"
        storage.put_snapshot(a_snapshot(CASH.account_id, through=2, total="30.00"))
        assert len(storage.snapshots()) == 2

    def test_a_hold_is_replaced_by_the_one_under_its_key(self, storage: Storage) -> None:
        placed = a_hold("h-1")
        storage.put_hold(placed)
        assert storage.hold("h-1") == placed
        settled = placed.released(at=NOW + timedelta(hours=1))
        storage.put_hold(settled)
        assert storage.hold("h-1") == settled
        assert len(storage.holds()) == 1, "a settled hold replaces the one it settles"
        storage.put_hold(a_hold("h-2"))
        assert len(storage.holds()) == 2

    def test_an_operation_is_replaced_by_the_one_under_its_key(self, storage: Storage) -> None:
        settled = an_operation("k-1")
        storage.put_operation(settled)
        assert storage.operation("k-1") == settled
        rewritten = an_operation("k-1", ("e-1", "e-2"), "f-2")
        storage.put_operation(rewritten)
        assert storage.operation("k-1") == rewritten
        assert len(storage.operations()) == 1, "a key holds one record"
        storage.put_operation(an_operation("k-2"))
        assert len(storage.operations()) == 2

    def test_a_read_of_the_entries_is_a_copy(self, storage: Storage) -> None:
        storage.append_entries((an_entry("e-1"),))
        kept = storage.entries()
        assert isinstance(kept, tuple), "a read answers with a tuple, so it cannot be written back"
        storage.append_entries((an_entry("e-2", "5.00"),))
        assert len(kept) == 1, "the read grew with the store: it was the store's own collection"

    def test_a_read_of_the_keyed_records_is_a_copy(self, storage: Storage) -> None:
        storage.put_snapshot(a_snapshot())
        storage.put_hold(a_hold("h-1"))
        storage.put_operation(an_operation("k-1"))
        snapshots = storage.snapshots()
        holds = storage.holds()
        operations = storage.operations()
        assert isinstance(snapshots, tuple)
        assert isinstance(holds, tuple)
        assert isinstance(operations, tuple)
        storage.put_snapshot(a_snapshot(CASH.account_id))
        storage.put_hold(a_hold("h-2"))
        storage.put_operation(an_operation("k-2"))
        assert len(snapshots) == 1, "the snapshots read grew with the store"
        assert len(holds) == 1, "the holds read grew with the store"
        assert len(operations) == 1, "the operations read grew with the store"

    def test_a_block_that_finishes_keeps_what_it_wrote(self, storage: Storage) -> None:
        with storage.atomic():
            storage.append_entries((an_entry("e-1"),))
            storage.put_hold(a_hold("h-1"))
        assert storage.entry_count() == 1
        assert storage.hold("h-1") is not None

    def test_a_block_left_by_an_error_keeps_nothing_of_it(self, storage: Storage) -> None:
        storage.append_entries((an_entry("e-1"),))
        with _refused(RuntimeError):
            with storage.atomic():
                storage.append_entries((an_entry("e-2", "5.00"),))
                storage.put_hold(a_hold("h-1"))
                raise RuntimeError("halfway through")
        assert storage.entry_count() == 1
        assert storage.entry("e-2") is None
        assert storage.hold("h-1") is None

    def test_a_block_inside_another_joins_the_one_around_it(self, storage: Storage) -> None:
        with _refused(RuntimeError):
            with storage.atomic():
                storage.append_entries((an_entry("e-1"),))
                with storage.atomic():
                    storage.append_entries((an_entry("e-2", "5.00"),))
                raise RuntimeError("after the inner block")
        assert storage.entry_count() == 0, "the inner block settled on its own"


def checks() -> tuple[str, ...]:
    """The names of every check in the suite."""
    return tuple(sorted(name for name in dir(StorageConformance) if name.startswith("test_")))


def conformance_failures(new_storage: Callable[[], Storage]) -> tuple[str, ...]:
    """Run the suite outside a pytest session, naming the checks a store fails.

    Each check is handed a store of its own from the factory, so a store that
    fails one is still read against the rest. An empty answer is a store that
    keeps the contract.
    """
    suite = StorageConformance()
    return tuple(name for name in checks() if _fails(getattr(suite, name), new_storage()))


def _fails(check: Callable[[Storage], None], storage: Storage) -> bool:
    try:
        check(storage)
    except AssertionError:
        return True
    return False
