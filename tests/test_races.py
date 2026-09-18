"""What holds when the calls do not arrive one at a time."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from decimal import Decimal

from ledger_core import (
    Account,
    AccountType,
    Balances,
    DuplicateEntry,
    Entry,
    Holds,
    Journal,
    Money,
    Operations,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
CASH = Account("cash", AccountType.ASSET, "EUR")
CUSTOMER = Account("customer:42", AccountType.LIABILITY, "EUR")


def eur(amount: str) -> Money:
    return Money(Decimal(amount), "EUR")


def deposit(entry_id: str, amount: str = "1.00") -> Entry:
    return Entry.transfer(
        entry_id=entry_id,
        occurred_at=NOW,
        debit=CASH,
        credit=CUSTOMER,
        amount=eur(amount),
    )


def run(threads: list[threading.Thread]) -> None:
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def test_a_reader_never_sees_a_balance_that_was_never_true() -> None:
    journal = Journal()
    written = 80
    readers = 4
    ready = threading.Barrier(readers + 1)
    lock = threading.Lock()
    seen: list[list[Decimal]] = []

    def write() -> None:
        ready.wait()
        for n in range(written):
            journal.append(deposit(f"e-{n}"))

    def read() -> None:
        balances = Balances(journal)
        ready.wait()
        mine = [balances.balance(CUSTOMER) for _ in range(written)]
        with lock:
            seen.append(mine)

    run([threading.Thread(target=read) for _ in range(readers)] + [
        threading.Thread(target=write)
    ])

    valid = {Decimal(n) for n in range(written + 1)}
    for mine in seen:
        assert all(total in valid for total in mine)
        assert mine == sorted(mine)
    assert Balances(journal).balance(CUSTOMER) == Decimal(written)


def test_concurrent_writers_lose_no_movement() -> None:
    journal = Journal()
    writers, each = 4, 25
    ready = threading.Barrier(writers)

    def write(writer: int) -> None:
        ready.wait()
        for n in range(each):
            journal.append(deposit(f"e-{writer}-{n}"))

    run([threading.Thread(target=write, args=(w,)) for w in range(writers)])

    total = Decimal(writers * each)
    assert len(journal) == writers * each
    assert len({entry.entry_id for entry in journal}) == writers * each
    assert Balances(journal).balance(CUSTOMER) == total
    assert Balances(Journal(journal.entries)).balance(CASH) == total


def test_a_key_posted_from_every_thread_lands_once() -> None:
    journal = Journal()
    operations = Operations(journal)
    topup = deposit("e-1", "25.00")
    callers = 6
    ready = threading.Barrier(callers)
    lock = threading.Lock()
    outcomes: list[object] = []

    def post() -> None:
        ready.wait()
        try:
            outcome: object = operations.post("callback:9f3", topup)
        except DuplicateEntry as refused:
            outcome = refused
        with lock:
            outcomes.append(outcome)

    run([threading.Thread(target=post) for _ in range(callers)])

    assert len(journal) == 1
    assert Balances(journal).balance(CUSTOMER) == Decimal("25.00")
    landed = [outcome for outcome in outcomes if isinstance(outcome, Entry)]
    refused = [outcome for outcome in outcomes if isinstance(outcome, DuplicateEntry)]
    assert landed
    assert all(entry is journal.entry("e-1") for entry in landed)
    assert len(landed) + len(refused) == callers


def test_every_hold_placed_at_once_is_counted() -> None:
    journal = Journal()
    journal.append(deposit("e-1", "1000.00"))
    holds = Holds(journal)
    placers, each = 5, 4
    ready = threading.Barrier(placers)

    def place(placer: int) -> None:
        ready.wait()
        for n in range(each):
            holds.place(
                hold_id=f"h-{placer}-{n}",
                account=CUSTOMER,
                amount=eur("1.00"),
                placed_at=NOW,
            )

    run([threading.Thread(target=place, args=(p,)) for p in range(placers)])

    reserved = Decimal(placers * each)
    assert len(holds) == placers * each
    assert holds.held(CUSTOMER) == reserved
    assert holds.available(CUSTOMER) == Decimal("1000.00") - reserved
    assert Holds(journal).held(CUSTOMER) == reserved
