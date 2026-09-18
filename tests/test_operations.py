"""Idempotency: a write asked for twice under one key happens once."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from ledger_core import (
    Account,
    AccountType,
    Balances,
    DuplicateEntry,
    Entry,
    Journal,
    Money,
    Operation,
    OperationMismatch,
    Operations,
    UnknownOperation,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
LATER = NOW + timedelta(hours=1)
KEY = "callback:9f3"


def eur(amount: str) -> Money:
    return Money(Decimal(amount), "EUR")


@pytest.fixture
def cash() -> Account:
    return Account("cash", AccountType.ASSET, "EUR")


@pytest.fixture
def customer() -> Account:
    return Account("customer:42", AccountType.LIABILITY, "EUR")


@pytest.fixture
def journal() -> Journal:
    return Journal()


@pytest.fixture
def operations(journal: Journal) -> Operations:
    return Operations(journal)


def topup(
    cash: Account,
    customer: Account,
    entry_id: str = "e-1",
    amount: str = "25.00",
    occurred_at: datetime = NOW,
) -> Entry:
    return Entry.transfer(
        entry_id=entry_id,
        occurred_at=occurred_at,
        debit=cash,
        credit=customer,
        amount=eur(amount),
        memo="deposit callback",
    )


def test_a_key_writes_once_however_often_it_is_asked_for(
    journal: Journal, operations: Operations, cash: Account, customer: Account
) -> None:
    entry = topup(cash, customer)
    written = operations.post(KEY, entry)
    for _ in range(5):
        assert operations.post(KEY, entry) is written
    assert len(journal) == 1
    assert Balances(journal).balance(customer) == Decimal("25.00")
    assert KEY in operations
    assert len(operations) == 1


def test_a_batch_under_one_key_is_written_once(
    journal: Journal, operations: Operations, cash: Account, customer: Account
) -> None:
    batch = (
        topup(cash, customer, "e-1", "25.00"),
        topup(cash, customer, "e-2", "5.00", LATER),
    )
    written = operations.post_many(KEY, batch)
    assert [entry.entry_id for entry in written] == ["e-1", "e-2"]
    assert operations.post_many(KEY, batch) == written
    assert len(journal) == 2
    assert operations.operation(KEY).entry_ids == ("e-1", "e-2")
    assert operations.operation(KEY).as_of == LATER


def test_a_retry_that_rebuilt_the_amount_is_the_same_work(
    journal: Journal, operations: Operations, cash: Account, customer: Account
) -> None:
    written = operations.post(KEY, topup(cash, customer, amount="25.00"))
    assert operations.post(KEY, topup(cash, customer, amount="25.0")) is written
    assert len(journal) == 1


def test_a_key_carrying_different_work_is_refused(
    journal: Journal, operations: Operations, cash: Account, customer: Account
) -> None:
    operations.post(KEY, topup(cash, customer))
    with pytest.raises(OperationMismatch):
        operations.post(KEY, topup(cash, customer, amount="30.00"))
    assert len(journal) == 1
    assert Balances(journal).balance(customer) == Decimal("25.00")


def test_a_refused_batch_settles_no_key(
    journal: Journal, operations: Operations, cash: Account, customer: Account
) -> None:
    operations.post("first", topup(cash, customer))
    with pytest.raises(DuplicateEntry):
        operations.post_many(
            KEY,
            (
                topup(cash, customer, "e-2", "5.00", LATER),
                topup(cash, customer, "e-1", "25.00"),
            ),
        )
    assert len(journal) == 1
    assert "e-2" not in journal
    assert KEY not in operations


@pytest.mark.parametrize("key", ["", None])
def test_a_write_needs_a_key(
    operations: Operations, cash: Account, customer: Account, key: object
) -> None:
    with pytest.raises(ValueError):
        operations.post_many(key or "", (topup(cash, customer),))


def test_an_operation_writes_at_least_one_entry(operations: Operations) -> None:
    with pytest.raises(ValueError):
        operations.post_many(KEY, ())


def test_a_key_nobody_used_is_unknown(operations: Operations) -> None:
    with pytest.raises(UnknownOperation):
        operations.operation("nothing")
    assert "nothing" not in operations


def test_a_settled_key_stays_settled_for_every_reader(
    journal: Journal, operations: Operations, cash: Account, customer: Account
) -> None:
    written = operations.post(KEY, topup(cash, customer))
    later = Operations(journal)
    assert KEY in later
    assert later.post(KEY, topup(cash, customer)) is written
    assert len(journal) == 1


def test_a_record_carries_a_key_into_another_process(
    journal: Journal, operations: Operations, cash: Account, customer: Account
) -> None:
    entry = topup(cash, customer)
    operations.post(KEY, entry)
    records = operations.operations()

    resumed = Journal(journal.entries)
    rebuilt = Operations(resumed, records)
    assert rebuilt.post(KEY, entry) is resumed.entry("e-1")
    assert len(resumed) == 1


def test_a_record_naming_entries_nobody_wrote_is_refused(
    operations: Operations,
) -> None:
    stray = Operation(key=KEY, entry_ids=("e-404",), fingerprint="whatever")
    with pytest.raises(OperationMismatch):
        operations.restore(stray)
    assert KEY not in operations


def test_a_key_already_recorded_against_other_entries_is_refused(
    journal: Journal, operations: Operations, cash: Account, customer: Account
) -> None:
    first = operations.post_many(KEY, (topup(cash, customer),))
    second = operations.post_many(
        "other", (topup(cash, customer, "e-2", "5.00", LATER),)
    )
    assert operations.restore(Operation.of(KEY, first)).entry_ids == ("e-1",)
    with pytest.raises(OperationMismatch):
        operations.restore(Operation.of(KEY, second))
    assert operations.operation(KEY).entry_ids == ("e-1",)


def test_the_entries_of_a_key_are_read_back_from_the_journal(
    journal: Journal, operations: Operations, cash: Account, customer: Account
) -> None:
    operations.post(KEY, topup(cash, customer))
    assert operations.entries(KEY) == (journal.entry("e-1"),)
    assert [record.key for record in operations] == [KEY]
