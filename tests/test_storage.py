"""The store the ledger keeps its records in, and the contract it is read against."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from ledger_core import (
    Account,
    AccountType,
    Balances,
    DuplicateEntry,
    Entry,
    Holds,
    InMemoryStorage,
    Journal,
    Money,
    Operations,
    Storage,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def eur(amount: str) -> Money:
    return Money(Decimal(amount), "EUR")


@pytest.fixture
def cash() -> Account:
    return Account("cash", AccountType.ASSET, "EUR")


@pytest.fixture
def customer() -> Account:
    return Account("customer:42", AccountType.LIABILITY, "EUR")


def deposit(
    cash: Account, customer: Account, entry_id: str = "e-1", amount: str = "25.00"
) -> Entry:
    return Entry.transfer(
        entry_id=entry_id,
        occurred_at=NOW,
        debit=cash,
        credit=customer,
        amount=eur(amount),
        memo="deposit",
    )


def test_the_in_memory_store_is_read_as_the_protocol() -> None:
    assert isinstance(InMemoryStorage(), Storage)


def test_a_journal_handed_no_store_builds_one() -> None:
    assert isinstance(Journal().storage, Storage)


def test_entries_land_in_the_store(cash: Account, customer: Account) -> None:
    storage = InMemoryStorage()
    journal = Journal(storage=storage)
    journal.append(deposit(cash, customer))
    assert storage.entry_count() == 1
    assert storage.entry("e-1") is journal.entry("e-1")
    assert storage.entry("e-9") is None


def test_a_journal_reads_what_the_store_already_holds(cash: Account, customer: Account) -> None:
    storage = InMemoryStorage()
    Journal(storage=storage).append(deposit(cash, customer))
    resumed = Journal(storage=storage)
    assert len(resumed) == 1
    assert "e-1" in resumed


def test_the_store_answers_from_a_position(cash: Account, customer: Account) -> None:
    storage = InMemoryStorage()
    journal = Journal(storage=storage)
    journal.extend([deposit(cash, customer), deposit(cash, customer, "e-2", "5.00")])
    assert [entry.entry_id for entry in journal.since(1)] == ["e-2"]
    assert storage.entries_since(2) == ()


def test_a_refused_batch_leaves_the_store_untouched(cash: Account, customer: Account) -> None:
    storage = InMemoryStorage()
    journal = Journal(storage=storage)
    journal.append(deposit(cash, customer))
    with pytest.raises(DuplicateEntry):
        journal.extend(
            [deposit(cash, customer, "e-2", "5.00"), deposit(cash, customer)]
        )
    assert storage.entry_count() == 1
    assert storage.entry("e-2") is None


def test_snapshots_are_kept_in_the_store(cash: Account, customer: Account) -> None:
    storage = InMemoryStorage()
    journal = Journal(storage=storage)
    journal.append(deposit(cash, customer))
    assert Balances(journal).balance(customer) == Decimal("25.00")
    folded = storage.snapshot("customer:42")
    assert folded is not None
    assert folded.through == 1
    assert "customer:42" in Balances(journal)


def test_holds_are_kept_in_the_store(cash: Account, customer: Account) -> None:
    storage = InMemoryStorage()
    journal = Journal(storage=storage)
    journal.append(deposit(cash, customer))
    Holds(journal).place(
        hold_id="h-1", account=customer, amount=eur("10.00"), placed_at=NOW
    )
    kept = storage.hold("h-1")
    assert kept is not None
    assert kept.amount == eur("10.00")

    later = Holds(journal)
    assert "h-1" in later
    assert later.available(customer) == Decimal("15.00")


def test_a_released_hold_replaces_the_one_in_the_store(
    cash: Account, customer: Account
) -> None:
    storage = InMemoryStorage()
    journal = Journal(storage=storage)
    journal.append(deposit(cash, customer))
    holds = Holds(journal)
    holds.place(hold_id="h-1", account=customer, amount=eur("10.00"), placed_at=NOW)
    holds.release("h-1", at=NOW)
    assert len(storage.holds()) == 1
    assert Holds(journal).held(customer) == Decimal("0")


def test_a_settled_key_stays_settled_in_the_store(cash: Account, customer: Account) -> None:
    storage = InMemoryStorage()
    topup = deposit(cash, customer)
    Operations(Journal(storage=storage)).post("callback:9f3", topup)
    settled = storage.operation("callback:9f3")
    assert settled is not None
    assert settled.entry_ids == ("e-1",)

    resumed = Journal(storage=storage)
    assert Operations(resumed).post("callback:9f3", topup).entry_id == "e-1"
    assert len(resumed) == 1
