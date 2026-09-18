"""Random streams of writes, and what has to hold after every one of them.

The promises are checked against the postings themselves rather than against
the projection that is being tested, so a fold that drifts from the journal is
caught by the stream that produced it.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from ledger_core import (
    Account,
    AccountType,
    Balances,
    Entry,
    HoldState,
    Holds,
    InsufficientFunds,
    Journal,
    Money,
    Operations,
    Side,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
SEEDS = tuple(range(16))
STEPS = 40

CASH = Account("cash", AccountType.ASSET, "EUR")
CUSTOMERS = tuple(
    Account(f"customer:{n}", AccountType.LIABILITY, "EUR") for n in range(3)
)
ACCOUNTS = (CASH, *CUSTOMERS)

ACTIONS = (
    "deposit",
    "deposit",
    "deposit",
    "withdraw",
    "place",
    "place",
    "release",
    "capture",
    "reverse",
)


def replayed(journal: Journal, account: Account) -> Decimal:
    """The balance counted from the postings alone, owing nothing to a snapshot."""
    total = Decimal(0)
    for posting in journal.postings(account.account_id):
        amount = posting.amount.amount
        total += amount if posting.side is account.normal_side else -amount
    return total


class Stream:
    """A ledger driven by a seeded stream of writes, checked after each one."""

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.journal = Journal()
        self.balances = Balances(self.journal)
        self.holds = Holds(self.journal, balances=self.balances)
        self.operations = Operations(self.journal)
        self.posted: list[tuple[str, tuple[Entry, ...]]] = []
        self.deposits: list[tuple[Entry, Account]] = []
        self.steps = 0

    def run(self, steps: int) -> Stream:
        for _ in range(steps):
            getattr(self, self.rng.choice(ACTIONS))()
            self.check()
        return self

    def totals(self) -> dict[str, Decimal]:
        return {
            account.account_id: self.balances.balance(account) for account in ACCOUNTS
        }

    def check(self) -> None:
        counted = sum((self._raw(account) for account in ACCOUNTS), Decimal(0))
        assert counted == 0
        for account in ACCOUNTS:
            assert self.balances.balance(account) == replayed(self.journal, account)
        for customer in CUSTOMERS:
            reserved = sum(
                (hold.amount.amount for hold in self.holds.open_on(customer)),
                Decimal(0),
            )
            assert self.holds.held(customer) == reserved
            assert self.holds.available(customer) >= 0
        assert self.balances.snapshot(CASH).through == len(self.journal)

    def deposit(self) -> None:
        entry = Entry.transfer(
            entry_id=self._id("e"),
            occurred_at=self._when(),
            debit=CASH,
            credit=(customer := self.rng.choice(CUSTOMERS)),
            amount=self._amount(),
        )
        key = self._id("op")
        self.posted.append((key, self.operations.post_many(key, (entry,))))
        self.deposits.append((entry, customer))

    def withdraw(self) -> None:
        customer = self.rng.choice(CUSTOMERS)
        entry = Entry.transfer(
            entry_id=self._id("w"),
            occurred_at=self._when(),
            debit=customer,
            credit=CASH,
            amount=self._amount(),
        )
        self._guarded(entry, customer)

    def reverse(self) -> None:
        if not self.deposits:
            return
        entry, customer = self.rng.choice(self.deposits)
        undo = entry.reversal(entry_id=self._id("r"), occurred_at=self._when())
        self._guarded(undo, customer)

    def place(self) -> None:
        customer = self.rng.choice(CUSTOMERS)
        hold_id = self._id("h")
        before = len(self.journal)
        try:
            self.holds.place(
                hold_id=hold_id,
                account=customer,
                amount=self._amount(),
                placed_at=self._when(),
            )
        except InsufficientFunds:
            assert hold_id not in self.holds
        assert len(self.journal) == before

    def release(self) -> None:
        hold = self._an_open_hold()
        if hold is None:
            return
        released = self.holds.release(hold.hold_id, at=self._when())
        assert released.state is HoldState.RELEASED

    def capture(self) -> None:
        hold = self._an_open_hold()
        if hold is None:
            return
        entry = Entry.transfer(
            entry_id=self._id("c"),
            occurred_at=self._when(),
            debit=hold.account,
            credit=CASH,
            amount=hold.amount,
        )
        settled = self.holds.capture(hold.hold_id, entry)
        assert settled.state is HoldState.CAPTURED
        assert settled.settled_by == entry.entry_id

    def _guarded(self, entry: Entry, account: Account) -> None:
        before = len(self.journal)
        try:
            self.holds.spend(entry, account=account)
        except InsufficientFunds:
            assert entry.entry_id not in self.journal
            assert len(self.journal) == before

    def _an_open_hold(self):
        open_holds = [hold for hold in self.holds if hold.is_open]
        return self.rng.choice(open_holds) if open_holds else None

    def _raw(self, account: Account) -> Decimal:
        total = self.balances.balance(account)
        return total if account.normal_side is Side.DEBIT else -total

    def _amount(self) -> Money:
        return Money(Decimal(self.rng.randint(1, 5000)) / 100, "EUR")

    def _id(self, prefix: str) -> str:
        self.steps += 1
        return f"{prefix}-{self.steps}"

    def _when(self) -> datetime:
        return NOW + timedelta(minutes=self.steps)


@pytest.mark.parametrize("seed", SEEDS)
def test_balances_never_drift_across_a_random_stream(seed: int) -> None:
    stream = Stream(seed).run(STEPS)
    assert len(stream.journal) > 0


@pytest.mark.parametrize("seed", SEEDS)
def test_a_resumed_fold_agrees_with_a_full_replay(seed: int) -> None:
    stream = Stream(seed).run(STEPS)
    rebuilt = Balances(Journal(stream.journal.entries))
    for account in ACCOUNTS:
        assert rebuilt.balance(account) == stream.balances.balance(account)


@pytest.mark.parametrize("seed", SEEDS)
def test_a_stale_snapshot_costs_a_replay_never_a_wrong_answer(seed: int) -> None:
    stream = Stream(seed).run(STEPS // 2)
    stale = [stream.balances.snapshot(account) for account in ACCOUNTS]
    stream.run(STEPS // 2)

    resumed = Balances(Journal(stream.journal.entries), stale)
    for account in ACCOUNTS:
        assert resumed.balance(account) == stream.balances.balance(account)
        assert resumed.snapshot(account).through == len(stream.journal)


@pytest.mark.parametrize("seed", SEEDS)
def test_every_key_in_a_stream_replays_without_writing(seed: int) -> None:
    stream = Stream(seed).run(STEPS)
    before = len(stream.journal)
    totals = stream.totals()

    replay = list(stream.posted)
    stream.rng.shuffle(replay)
    for key, written in replay:
        assert stream.operations.post_many(key, written) == written

    assert len(stream.journal) == before
    assert stream.totals() == totals


@pytest.mark.parametrize("seed", SEEDS)
def test_holds_never_reserve_more_than_the_account_holds(seed: int) -> None:
    stream = Stream(seed).run(STEPS)
    for customer in CUSTOMERS:
        assert stream.holds.held(customer) <= replayed(stream.journal, customer)
