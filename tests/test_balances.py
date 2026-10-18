"""Snapshots: what a fold remembers, and what it refuses to resume from."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from ledger_core import (
    Account,
    AccountType,
    Balances,
    CurrencyMismatch,
    Entry,
    Journal,
    Money,
    Side,
    Snapshot,
    SnapshotMismatch,
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


@pytest.fixture
def funded(cash: Account, customer: Account) -> Journal:
    journal = Journal()
    journal.append(
        Entry.transfer(
            entry_id="e-1",
            occurred_at=NOW,
            debit=cash,
            credit=customer,
            amount=eur("25.00"),
            memo="deposit",
        )
    )
    return journal


def test_a_snapshot_records_the_side_it_was_folded_in(
    funded: Journal, cash: Account, customer: Account
) -> None:
    balances = Balances(funded)
    assert balances.snapshot(cash).side is Side.DEBIT
    assert balances.snapshot(customer).side is Side.CREDIT
    assert Snapshot.empty(customer).side is Side.CREDIT


def test_the_same_id_read_in_the_other_direction_is_refused(
    funded: Journal, customer: Account
) -> None:
    balances = Balances(funded)
    assert balances.balance(customer) == Decimal("25.00")
    mirrored = Account(customer.account_id, AccountType.ASSET, customer.currency)
    with pytest.raises(SnapshotMismatch) as refused:
        balances.balance(mirrored)
    assert "customer:42" in str(refused.value)
    assert balances.balance(customer) == Decimal("25.00")


def test_a_snapshot_folded_in_the_other_direction_is_not_restored(
    funded: Journal, customer: Account
) -> None:
    balances = Balances(funded)
    folded = balances.snapshot(customer)
    flipped = Snapshot(
        account_id=folded.account_id,
        currency=folded.currency,
        side=Side.DEBIT,
        total=folded.total,
        through=folded.through,
    )
    with pytest.raises(SnapshotMismatch):
        balances.restore(flipped)
    assert balances.snapshot(customer).side is Side.CREDIT
    assert balances.balance(customer) == Decimal("25.00")


def test_a_snapshot_folded_in_another_currency_is_not_restored(
    funded: Journal, customer: Account
) -> None:
    balances = Balances(funded)
    folded = balances.snapshot(customer)
    with pytest.raises(CurrencyMismatch):
        balances.restore(
            Snapshot(
                account_id=folded.account_id,
                currency="USD",
                side=folded.side,
                total=folded.total,
                through=folded.through,
            )
        )


def test_a_side_read_back_from_a_store_is_the_value_it_was_written_as(
    customer: Account,
) -> None:
    stored = Snapshot(
        account_id=customer.account_id,
        currency=customer.currency,
        side=Side("credit"),
        total=Decimal("25.00"),
        through=1,
    )
    assert stored.side is Side.CREDIT
    with pytest.raises(ValueError):
        Side("sideways")


def test_a_snapshot_carries_a_fold_into_another_process(
    funded: Journal, customer: Account
) -> None:
    folded = Balances(funded).snapshot(customer)
    resumed = Balances(Journal(funded.entries), [folded])
    assert resumed.balance(customer) == Decimal("25.00")
    assert resumed.snapshot(customer).through == len(funded)
