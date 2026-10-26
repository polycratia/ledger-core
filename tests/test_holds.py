"""Holds: a reserve in two phases, settled once and never reopened."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from ledger_core import (
    Account,
    AccountType,
    Balances,
    CaptureMismatch,
    ClosedAccount,
    CurrencyMismatch,
    DuplicateHold,
    Entry,
    HoldNotOpen,
    Holds,
    HoldState,
    InsufficientFunds,
    Journal,
    Money,
    UnknownHold,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
LATER = NOW + timedelta(hours=1)
MUCH_LATER = NOW + timedelta(days=1)


def eur(amount: str) -> Money:
    return Money(Decimal(amount), "EUR")


def usd(amount: str) -> Money:
    return Money(Decimal(amount), "USD")


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
            amount=eur("100.00"),
            memo="deposit",
        )
    )
    return journal


@pytest.fixture
def holds(funded: Journal) -> Holds:
    return Holds(funded)


def payout(
    cash: Account, customer: Account, amount: str, entry_id: str = "e-2"
) -> Entry:
    return Entry.transfer(
        entry_id=entry_id,
        occurred_at=LATER,
        debit=customer,
        credit=cash,
        amount=eur(amount),
        memo="withdrawal",
    )


def test_a_hold_reserves_without_writing(
    funded: Journal, holds: Holds, customer: Account
) -> None:
    holds.place(hold_id="h-1", account=customer, amount=eur("30.00"), placed_at=NOW)
    assert len(funded) == 1
    assert holds.balance(customer) == Decimal("100.00")
    assert holds.held(customer) == Decimal("30.00")
    assert holds.available(customer) == Decimal("70.00")


def test_available_is_the_balance_less_every_open_hold(
    holds: Holds, customer: Account
) -> None:
    holds.place(hold_id="h-1", account=customer, amount=eur("30.00"), placed_at=NOW)
    holds.place(hold_id="h-2", account=customer, amount=eur("25.50"), placed_at=NOW)
    assert holds.held(customer) == Decimal("55.50")
    assert holds.available(customer) == Decimal("44.50")
    assert {hold.hold_id for hold in holds.open_on(customer)} == {"h-1", "h-2"}


def test_a_hold_id_is_placed_once(holds: Holds, customer: Account) -> None:
    holds.place(hold_id="h-1", account=customer, amount=eur("30.00"), placed_at=NOW)
    with pytest.raises(DuplicateHold):
        holds.place(hold_id="h-1", account=customer, amount=eur("5.00"), placed_at=NOW)
    assert holds.held(customer) == Decimal("30.00")
    assert len(holds) == 1


def test_a_hold_nobody_placed_is_unknown(holds: Holds) -> None:
    with pytest.raises(UnknownHold):
        holds.hold("h-9")
    with pytest.raises(UnknownHold):
        holds.release("h-9", at=LATER)
    assert "h-9" not in holds


def test_a_hold_in_another_currency_is_refused(
    holds: Holds, customer: Account
) -> None:
    with pytest.raises(CurrencyMismatch):
        holds.place(hold_id="h-1", account=customer, amount=usd("5.00"), placed_at=NOW)
    assert "h-1" not in holds


@pytest.mark.parametrize("amount", ["0.00", "-5.00"])
def test_a_hold_reserves_a_positive_amount(
    holds: Holds, customer: Account, amount: str
) -> None:
    with pytest.raises(ValueError):
        holds.place(
            hold_id="h-1", account=customer, amount=eur(amount), placed_at=NOW
        )
    assert "h-1" not in holds


def test_a_closed_account_reserves_nothing(holds: Holds, customer: Account) -> None:
    with pytest.raises(ClosedAccount):
        holds.place(
            hold_id="h-1",
            account=customer.close(at=NOW),
            amount=eur("5.00"),
            placed_at=NOW,
        )


def test_a_release_gives_the_reservation_back(
    funded: Journal, holds: Holds, customer: Account
) -> None:
    holds.place(hold_id="h-1", account=customer, amount=eur("30.00"), placed_at=NOW)
    released = holds.release("h-1", at=LATER)
    assert released.state is HoldState.RELEASED
    assert released.state.is_settled
    assert released.settled_at == LATER
    assert released.settled_by is None
    assert len(funded) == 1
    assert holds.held(customer) == Decimal("0")
    assert holds.available(customer) == Decimal("100.00")
    assert holds.open_on(customer) == ()


def test_a_settled_hold_never_reopens(holds: Holds, customer: Account) -> None:
    holds.place(hold_id="h-1", account=customer, amount=eur("30.00"), placed_at=NOW)
    holds.release("h-1", at=LATER)
    with pytest.raises(HoldNotOpen):
        holds.release("h-1", at=LATER)
    assert len(holds) == 1


def test_capture_moves_exactly_what_was_held(
    funded: Journal, holds: Holds, cash: Account, customer: Account
) -> None:
    holds.place(hold_id="h-1", account=customer, amount=eur("30.00"), placed_at=NOW)
    available = holds.available(customer)
    settled = holds.capture("h-1", payout(cash, customer, "30.00"))
    assert settled.state is HoldState.CAPTURED
    assert settled.settled_by == "e-2"
    assert settled.settled_at == LATER
    assert "e-2" in funded
    assert holds.balance(customer) == Decimal("70.00")
    assert holds.held(customer) == Decimal("0")
    assert holds.available(customer) == available


def test_a_capture_that_moves_something_else_writes_nothing(
    funded: Journal, holds: Holds, cash: Account, customer: Account
) -> None:
    holds.place(hold_id="h-1", account=customer, amount=eur("30.00"), placed_at=NOW)
    with pytest.raises(CaptureMismatch):
        holds.capture("h-1", payout(cash, customer, "20.00"))
    assert "e-2" not in funded
    assert len(funded) == 1
    assert holds.hold("h-1").is_open
    assert holds.available(customer) == Decimal("70.00")


def test_a_capture_in_the_wrong_direction_is_refused(
    funded: Journal, holds: Holds, cash: Account, customer: Account
) -> None:
    holds.place(hold_id="h-1", account=customer, amount=eur("30.00"), placed_at=NOW)
    into = Entry.transfer(
        entry_id="e-2",
        occurred_at=LATER,
        debit=cash,
        credit=customer,
        amount=eur("30.00"),
    )
    with pytest.raises(CaptureMismatch):
        holds.capture("h-1", into)
    assert "e-2" not in funded


def test_a_hold_is_captured_once(
    funded: Journal, holds: Holds, cash: Account, customer: Account
) -> None:
    holds.place(hold_id="h-1", account=customer, amount=eur("30.00"), placed_at=NOW)
    holds.capture("h-1", payout(cash, customer, "30.00"))
    with pytest.raises(HoldNotOpen):
        holds.capture("h-1", payout(cash, customer, "30.00", "e-3"))
    assert "e-3" not in funded
    assert holds.balance(customer) == Decimal("70.00")


def test_a_released_hold_is_not_captured(
    funded: Journal, holds: Holds, cash: Account, customer: Account
) -> None:
    holds.place(hold_id="h-1", account=customer, amount=eur("30.00"), placed_at=NOW)
    holds.release("h-1", at=LATER)
    with pytest.raises(HoldNotOpen):
        holds.capture("h-1", payout(cash, customer, "30.00"))
    assert "e-2" not in funded
    assert holds.balance(customer) == Decimal("100.00")


def test_a_reservation_leaves_nothing_spendable_twice(
    holds: Holds, cash: Account, customer: Account
) -> None:
    holds.place(hold_id="h-1", account=customer, amount=eur("100.00"), placed_at=NOW)
    assert holds.available(customer) == Decimal("0.00")
    with pytest.raises(InsufficientFunds):
        holds.spend(payout(cash, customer, "0.01"), account=customer)


def test_holds_over_one_journal_stand_in_front_of_every_balance(
    funded: Journal, holds: Holds, customer: Account
) -> None:
    holds.place(hold_id="h-1", account=customer, amount=eur("30.00"), placed_at=NOW)
    other = Holds(funded)
    assert "h-1" in other
    assert other.held(customer) == Decimal("30.00")
    assert other.available(customer) == Decimal("70.00")


def test_holds_share_the_projection_they_are_handed(funded: Journal) -> None:
    balances = Balances(funded)
    holds = Holds(funded, balances=balances)
    assert holds.balances is balances
    with pytest.raises(ValueError):
        Holds(Journal(), balances=balances)


def test_a_hold_may_carry_an_expiry(holds: Holds, customer: Account) -> None:
    placed = holds.place(
        hold_id="h-1",
        account=customer,
        amount=eur("30.00"),
        placed_at=NOW,
        expires_at=LATER,
    )
    assert placed.expires_at == LATER
    assert placed.lapses_by(LATER)
    assert placed.lapses_by(MUCH_LATER)
    assert not placed.lapses_by(NOW)


@pytest.mark.parametrize("expires_at", [NOW, NOW - timedelta(hours=1)])
def test_an_expiry_comes_after_the_hold_was_placed(
    holds: Holds, customer: Account, expires_at: datetime
) -> None:
    with pytest.raises(ValueError):
        holds.place(
            hold_id="h-1",
            account=customer,
            amount=eur("30.00"),
            placed_at=NOW,
            expires_at=expires_at,
        )
    assert "h-1" not in holds


def test_a_hold_without_an_expiry_never_lapses(
    holds: Holds, customer: Account
) -> None:
    placed = holds.place(
        hold_id="h-1", account=customer, amount=eur("30.00"), placed_at=NOW
    )
    assert placed.expires_at is None
    assert not placed.lapses_by(MUCH_LATER)
    assert holds.expire(at=MUCH_LATER) == ()
    assert holds.held(customer) == Decimal("30.00")


def test_nothing_lapses_until_a_sweep_is_asked_for(
    funded: Journal, holds: Holds, customer: Account
) -> None:
    holds.place(
        hold_id="h-1",
        account=customer,
        amount=eur("30.00"),
        placed_at=NOW,
        expires_at=LATER,
    )
    assert holds.hold("h-1").is_open
    assert holds.held(customer) == Decimal("30.00")
    assert holds.available(customer) == Decimal("70.00")

    swept = holds.expire(at=MUCH_LATER)
    assert [hold.hold_id for hold in swept] == ["h-1"]
    assert holds.held(customer) == Decimal("0")
    assert holds.available(customer) == Decimal("100.00")
    assert len(funded) == 1


def test_a_sweep_closes_what_lapsed_as_expired(
    holds: Holds, customer: Account
) -> None:
    holds.place(
        hold_id="h-1",
        account=customer,
        amount=eur("30.00"),
        placed_at=NOW,
        expires_at=LATER,
    )
    (swept,) = holds.expire(at=MUCH_LATER)
    assert swept.state is HoldState.EXPIRED
    assert swept.state.is_settled
    assert swept.settled_at == MUCH_LATER
    assert swept.settled_by is None
    assert swept.expires_at == LATER
    assert holds.hold("h-1").state is HoldState.EXPIRED
    assert holds.open_on(customer) == ()


def test_an_expiry_at_the_sweep_lapses(holds: Holds, customer: Account) -> None:
    holds.place(
        hold_id="h-1",
        account=customer,
        amount=eur("30.00"),
        placed_at=NOW,
        expires_at=LATER,
    )
    assert [hold.hold_id for hold in holds.expire(at=LATER)] == ["h-1"]


def test_a_hold_outliving_the_sweep_stays_open(
    holds: Holds, customer: Account
) -> None:
    holds.place(
        hold_id="h-1",
        account=customer,
        amount=eur("30.00"),
        placed_at=NOW,
        expires_at=MUCH_LATER,
    )
    assert holds.expire(at=LATER) == ()
    assert holds.hold("h-1").is_open
    assert holds.held(customer) == Decimal("30.00")


def test_a_sweep_closes_only_what_lapsed(holds: Holds, customer: Account) -> None:
    holds.place(
        hold_id="h-1",
        account=customer,
        amount=eur("10.00"),
        placed_at=NOW,
        expires_at=LATER,
    )
    holds.place(
        hold_id="h-2",
        account=customer,
        amount=eur("20.00"),
        placed_at=NOW,
        expires_at=MUCH_LATER,
    )
    holds.place(hold_id="h-3", account=customer, amount=eur("30.00"), placed_at=NOW)

    assert [hold.hold_id for hold in holds.expire(at=LATER)] == ["h-1"]
    assert holds.held(customer) == Decimal("50.00")
    assert {hold.hold_id for hold in holds.open_on(customer)} == {"h-2", "h-3"}


def test_a_capture_before_the_sweep_still_settles(
    funded: Journal, holds: Holds, cash: Account, customer: Account
) -> None:
    holds.place(
        hold_id="h-1",
        account=customer,
        amount=eur("30.00"),
        placed_at=NOW,
        expires_at=LATER,
    )
    settled = holds.capture("h-1", payout(cash, customer, "30.00"))
    assert settled.state is HoldState.CAPTURED
    assert holds.expire(at=MUCH_LATER) == ()
    assert holds.hold("h-1").state is HoldState.CAPTURED
    assert "e-2" in funded
    assert holds.balance(customer) == Decimal("70.00")


def test_a_capture_after_the_sweep_is_refused(
    funded: Journal, holds: Holds, cash: Account, customer: Account
) -> None:
    holds.place(
        hold_id="h-1",
        account=customer,
        amount=eur("30.00"),
        placed_at=NOW,
        expires_at=LATER,
    )
    holds.expire(at=MUCH_LATER)
    with pytest.raises(HoldNotOpen):
        holds.capture("h-1", payout(cash, customer, "30.00"))
    assert "e-2" not in funded
    assert len(funded) == 1
    assert holds.balance(customer) == Decimal("100.00")


def test_an_expired_hold_never_reopens(holds: Holds, customer: Account) -> None:
    holds.place(
        hold_id="h-1",
        account=customer,
        amount=eur("30.00"),
        placed_at=NOW,
        expires_at=LATER,
    )
    holds.expire(at=MUCH_LATER)
    with pytest.raises(HoldNotOpen):
        holds.release("h-1", at=MUCH_LATER)
    assert holds.expire(at=MUCH_LATER) == ()
    assert holds.hold("h-1").settled_at == MUCH_LATER
    assert len(holds) == 1
