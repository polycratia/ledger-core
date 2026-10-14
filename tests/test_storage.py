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
    Hold,
    Holds,
    InMemoryStorage,
    Journal,
    Money,
    Operation,
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


def withdrawal(
    cash: Account, customer: Account, entry_id: str = "e-2", amount: str = "10.00"
) -> Entry:
    return Entry.transfer(
        entry_id=entry_id,
        occurred_at=NOW,
        debit=customer,
        credit=cash,
        amount=eur(amount),
        memo="withdrawal",
    )


class Refusing(InMemoryStorage):
    """A store that can be made to refuse one kind of record."""

    def __init__(self) -> None:
        super().__init__()
        self.refuses: str | None = None

    def put_hold(self, hold: Hold) -> None:
        self._check("hold")
        super().put_hold(hold)

    def put_operation(self, operation: Operation) -> None:
        self._check("operation")
        super().put_operation(operation)

    def _check(self, kind: str) -> None:
        if self.refuses == kind:
            raise RuntimeError(f"the store refused the {kind}")


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


def test_a_block_that_finishes_keeps_what_it_wrote(cash: Account, customer: Account) -> None:
    storage = InMemoryStorage()
    journal = Journal(storage=storage)
    with storage.atomic():
        journal.append(deposit(cash, customer))
        journal.append(deposit(cash, customer, "e-2", "5.00"))
    assert storage.entry_count() == 2
    assert Balances(journal).balance(customer) == Decimal("30.00")


def test_a_block_left_by_an_error_writes_nothing(cash: Account, customer: Account) -> None:
    storage = InMemoryStorage()
    journal = Journal(storage=storage)
    journal.append(deposit(cash, customer))
    with pytest.raises(RuntimeError):
        with storage.atomic():
            journal.append(deposit(cash, customer, "e-2", "5.00"))
            Balances(journal).balance(customer)
            raise RuntimeError("halfway through")
    assert storage.entry_count() == 1
    assert storage.entry("e-2") is None
    assert Balances(journal).balance(customer) == Decimal("25.00")


def test_a_nested_block_joins_the_outer_one(cash: Account, customer: Account) -> None:
    storage = InMemoryStorage()
    journal = Journal(storage=storage)
    with pytest.raises(RuntimeError):
        with storage.atomic():
            journal.append(deposit(cash, customer))
            with storage.atomic():
                journal.append(deposit(cash, customer, "e-2", "5.00"))
            raise RuntimeError("after the inner block")
    assert storage.entry_count() == 0


def test_an_inner_block_does_not_settle_on_its_own(
    cash: Account, customer: Account
) -> None:
    storage = InMemoryStorage()
    journal = Journal(storage=storage)
    with storage.atomic():
        journal.append(deposit(cash, customer))
        try:
            with storage.atomic():
                journal.append(deposit(cash, customer, "e-2", "5.00"))
                raise RuntimeError("inside the inner block")
        except RuntimeError:
            pass
    assert storage.entry_count() == 2


def test_a_guarded_write_widens_to_the_block_around_it(
    cash: Account, customer: Account
) -> None:
    storage = InMemoryStorage()
    journal = Journal(storage=storage)
    journal.append(deposit(cash, customer))
    holds = Holds(journal)
    with pytest.raises(RuntimeError):
        with storage.atomic():
            holds.place(
                hold_id="h-1", account=customer, amount=eur("10.00"), placed_at=NOW
            )
            holds.spend(withdrawal(cash, customer), account=customer)
            raise RuntimeError("after both writes")
    assert storage.hold("h-1") is None
    assert storage.entry("e-2") is None
    assert Holds(journal).available(customer) == Decimal("25.00")


def test_a_hold_the_store_refuses_leaves_nothing_behind(
    cash: Account, customer: Account
) -> None:
    storage = Refusing()
    journal = Journal(storage=storage)
    journal.append(deposit(cash, customer))
    holds = Holds(journal)
    storage.refuses = "hold"
    with pytest.raises(RuntimeError):
        holds.place(hold_id="h-1", account=customer, amount=eur("10.00"), placed_at=NOW)
    storage.refuses = None
    assert storage.hold("h-1") is None
    assert Holds(journal).available(customer) == Decimal("25.00")


def test_a_capture_the_store_refuses_leaves_no_entry(
    cash: Account, customer: Account
) -> None:
    storage = Refusing()
    journal = Journal(storage=storage)
    journal.append(deposit(cash, customer))
    holds = Holds(journal)
    holds.place(hold_id="h-1", account=customer, amount=eur("10.00"), placed_at=NOW)
    storage.refuses = "hold"
    with pytest.raises(RuntimeError):
        holds.capture("h-1", withdrawal(cash, customer))
    storage.refuses = None
    assert storage.entry("e-2") is None
    assert storage.entry_count() == 1
    assert holds.hold("h-1").is_open
    assert holds.available(customer) == Decimal("15.00")


def test_an_operation_record_the_store_refuses_leaves_no_entry(
    cash: Account, customer: Account
) -> None:
    storage = Refusing()
    journal = Journal(storage=storage)
    operations = Operations(journal)
    storage.refuses = "operation"
    with pytest.raises(RuntimeError):
        operations.post("callback:9f3", deposit(cash, customer))
    storage.refuses = None
    assert storage.entry_count() == 0
    assert storage.entry("e-1") is None
    assert storage.operation("callback:9f3") is None
