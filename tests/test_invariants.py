"""The three rules a write is refused for, and what each of them protects."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from ledger_core import (
    Account,
    AccountType,
    ClosedAccount,
    Entry,
    Holds,
    InsufficientFunds,
    InvariantViolated,
    Journal,
    Money,
    UnbalancedEntry,
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


@pytest.mark.parametrize("violation", [UnbalancedEntry, InsufficientFunds, ClosedAccount])
def test_every_violation_names_what_it_protects(violation: type[InvariantViolated]) -> None:
    assert issubclass(violation, InvariantViolated)
    assert violation.protects != InvariantViolated.protects
    assert violation.protects in str(violation("refused"))


def test_an_unbalanced_entry_never_becomes_an_object(cash: Account, customer: Account) -> None:
    with pytest.raises(UnbalancedEntry) as refused:
        Entry(
            entry_id="e-x",
            occurred_at=NOW,
            postings=(cash.debit(eur("5.00")), customer.credit(eur("4.00"))),
        )
    assert "EUR" in str(refused.value)
    assert "protects" in str(refused.value)


def test_closing_an_account_is_a_new_value(customer: Account) -> None:
    settled = customer.close(at=NOW)
    assert customer.is_open
    assert not settled.is_open
    assert settled.closed_at == NOW
    assert customer.debit(eur("1.00")).amount == eur("1.00")


def test_a_closed_account_refuses_a_posting(customer: Account) -> None:
    settled = customer.close(at=NOW)
    with pytest.raises(ClosedAccount) as refused:
        settled.debit(eur("1.00"))
    assert "customer:42" in str(refused.value)


def test_a_closed_account_refuses_a_transfer(cash: Account, customer: Account) -> None:
    with pytest.raises(ClosedAccount):
        Entry.transfer(
            entry_id="e-2",
            occurred_at=NOW,
            debit=cash.close(at=NOW),
            credit=customer,
            amount=eur("1.00"),
        )


def test_an_account_closes_once(customer: Account) -> None:
    settled = customer.close(at=NOW)
    with pytest.raises(ClosedAccount):
        settled.close(at=NOW + timedelta(days=1))


def test_a_closed_account_takes_no_holds(funded: Journal, customer: Account) -> None:
    holds = Holds(funded)
    with pytest.raises(ClosedAccount):
        holds.place(
            hold_id="h-1",
            account=customer.close(at=NOW),
            amount=eur("1.00"),
            placed_at=NOW,
        )
    assert "h-1" not in holds


def test_a_hold_beyond_available_is_refused(funded: Journal, customer: Account) -> None:
    holds = Holds(funded)
    holds.place(hold_id="h-1", account=customer, amount=eur("10.00"), placed_at=NOW)
    assert holds.available(customer) == Decimal("15.00")
    with pytest.raises(InsufficientFunds) as refused:
        holds.place(hold_id="h-2", account=customer, amount=eur("20.00"), placed_at=NOW)
    assert "15.00" in str(refused.value)
    assert "h-2" not in holds


def test_spending_more_than_is_available_writes_nothing(
    funded: Journal, cash: Account, customer: Account
) -> None:
    holds = Holds(funded)
    holds.place(hold_id="h-1", account=customer, amount=eur("10.00"), placed_at=NOW)
    payout = Entry.transfer(
        entry_id="e-2",
        occurred_at=NOW,
        debit=customer,
        credit=cash,
        amount=eur("20.00"),
    )
    with pytest.raises(InsufficientFunds):
        holds.spend(payout, account=customer)
    assert "e-2" not in funded
    assert len(funded) == 1
    assert holds.available(customer) == Decimal("15.00")


def test_spending_within_available_is_written(
    funded: Journal, cash: Account, customer: Account
) -> None:
    holds = Holds(funded)
    holds.place(hold_id="h-1", account=customer, amount=eur("10.00"), placed_at=NOW)
    payout = Entry.transfer(
        entry_id="e-2",
        occurred_at=NOW,
        debit=customer,
        credit=cash,
        amount=eur("15.00"),
    )
    holds.spend(payout, account=customer)
    assert "e-2" in funded
    assert holds.balance(customer) == Decimal("10.00")
    assert holds.available(customer) == Decimal("0.00")
