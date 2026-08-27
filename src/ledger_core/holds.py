"""Holds: funds reserved against an account until released or captured.

A hold moves nothing. It stands between a balance and what may be spent from
it: available is the balance less everything still reserved, and a reservation
that does not fit is refused. The movement itself appears only on capture, as
an ordinary entry in the journal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Iterator, Mapping

from .accounts import Account
from .balances import Balances, effect_on
from .entries import Entry
from .journal import Journal
from .protocol import CurrencyMismatch, MoneyLike

__all__ = [
    "CaptureMismatch",
    "DuplicateHold",
    "Hold",
    "HoldNotOpen",
    "HoldState",
    "Holds",
    "InsufficientFunds",
    "UnknownHold",
]


class DuplicateHold(ValueError):
    """Raised when a hold id has already been placed."""


class UnknownHold(KeyError):
    """Raised when a hold id was never placed."""


class HoldNotOpen(ValueError):
    """Raised when a hold that is already settled is released or captured."""


class InsufficientFunds(ValueError):
    """Raised when a hold would reserve more than the account has available."""


class CaptureMismatch(ValueError):
    """Raised when the settling entry does not move exactly what was held."""


class HoldState(Enum):
    OPEN = "open"
    RELEASED = "released"
    CAPTURED = "captured"

    @property
    def is_settled(self) -> bool:
        return self is not HoldState.OPEN


@dataclass(frozen=True, slots=True)
class Hold:
    """A reservation against one account, settled once and never reopened."""

    hold_id: str
    account: Account
    amount: MoneyLike
    placed_at: datetime
    memo: str = ""
    state: HoldState = HoldState.OPEN
    settled_at: datetime | None = None
    settled_by: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.hold_id:
            raise ValueError("hold_id must not be empty")
        if self.amount.amount <= 0:
            raise ValueError(f"a hold must reserve a positive amount, got {self.amount}")
        if self.amount.currency != self.account.currency:
            raise CurrencyMismatch(
                f"account {self.account.account_id} holds {self.account.currency}, "
                f"got {self.amount.currency}"
            )
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def account_id(self) -> str:
        return self.account.account_id

    @property
    def currency(self) -> str:
        return self.amount.currency

    @property
    def is_open(self) -> bool:
        return self.state is HoldState.OPEN

    def released(self, *, at: datetime) -> Hold:
        """The same hold, no longer reserving anything."""
        return self._settled(HoldState.RELEASED, at, None)

    def captured(self, *, at: datetime, entry_id: str) -> Hold:
        """The same hold, settled by the entry that moved the funds."""
        return self._settled(HoldState.CAPTURED, at, entry_id)

    def _settled(self, state: HoldState, at: datetime, entry_id: str | None) -> Hold:
        if not self.is_open:
            raise HoldNotOpen(f"hold {self.hold_id} is already {self.state.value}")
        return Hold(
            hold_id=self.hold_id,
            account=self.account,
            amount=self.amount,
            placed_at=self.placed_at,
            memo=self.memo,
            state=state,
            settled_at=at,
            settled_by=entry_id,
            metadata=self.metadata,
        )

    def __str__(self) -> str:
        return f"{self.hold_id}: {self.amount} on {self.account_id} ({self.state.value})"


class Holds:
    """The reservations standing against a journal.

    Amounts come back as :class:`~decimal.Decimal` in the account's own
    currency: the ledger owns no money type, so it cannot mint the zero an
    empty balance would need.
    """

    __slots__ = ("_journal", "_balances", "_holds")

    def __init__(self, journal: Journal, *, balances: Balances | None = None) -> None:
        if balances is not None and balances.journal is not journal:
            raise ValueError("balances must project the journal the holds stand against")
        self._journal = journal
        self._balances = balances if balances is not None else Balances(journal)
        self._holds: dict[str, Hold] = {}

    @property
    def journal(self) -> Journal:
        return self._journal

    @property
    def balances(self) -> Balances:
        return self._balances

    def balance(self, account: Account) -> Decimal:
        """What the account holds, counted in its normal direction."""
        return self._balances.balance(account)

    def held(self, account: Account) -> Decimal:
        """What open holds have reserved and nothing else may spend."""
        total = Decimal(0)
        for hold in self._holds.values():
            if not hold.is_open or hold.account_id != account.account_id:
                continue
            if hold.currency != account.currency:
                raise CurrencyMismatch(
                    f"account {account.account_id} holds {account.currency}, "
                    f"hold {hold.hold_id} reserves {hold.currency}"
                )
            total += hold.amount.amount
        return total

    def available(self, account: Account) -> Decimal:
        """Balance less holds: the most the next hold or withdrawal may take."""
        return self.balance(account) - self.held(account)

    def place(
        self,
        *,
        hold_id: str,
        account: Account,
        amount: MoneyLike,
        placed_at: datetime,
        memo: str = "",
        metadata: Mapping[str, str] | None = None,
    ) -> Hold:
        """Reserve funds. Refused if they are not there to reserve."""
        if hold_id in self._holds:
            raise DuplicateHold(f"hold {hold_id} is already placed")
        hold = Hold(
            hold_id=hold_id,
            account=account,
            amount=amount,
            placed_at=placed_at,
            memo=memo,
            metadata=metadata or {},
        )
        available = self.available(account)
        if hold.amount.amount > available:
            raise InsufficientFunds(
                f"hold {hold_id} reserves {amount}, "
                f"{available} {account.currency} is available on {account.account_id}"
            )
        self._holds[hold_id] = hold
        return hold

    def release(self, hold_id: str, *, at: datetime) -> Hold:
        """Give the reservation back; nothing is written."""
        released = self.hold(hold_id).released(at=at)
        self._holds[hold_id] = released
        return released

    def capture(self, hold_id: str, entry: Entry) -> Hold:
        """Settle a hold with the movement it was reserved for.

        The entry has to take exactly the held amount out of the account. It is
        written to the journal and the hold closes against it; if either step is
        refused, neither happens.
        """
        hold = self.hold(hold_id)
        if not hold.is_open:
            raise HoldNotOpen(f"hold {hold_id} is already {hold.state.value}")
        taken = -effect_on(entry, hold.account)
        if taken != hold.amount.amount:
            raise CaptureMismatch(
                f"hold {hold_id} reserved {hold.amount}, "
                f"entry {entry.entry_id} takes {taken} {hold.currency}"
            )
        self._journal.append(entry)
        settled = hold.captured(at=entry.occurred_at, entry_id=entry.entry_id)
        self._holds[hold_id] = settled
        return settled

    def hold(self, hold_id: str) -> Hold:
        try:
            return self._holds[hold_id]
        except KeyError:
            raise UnknownHold(hold_id) from None

    def open_on(self, account: Account) -> tuple[Hold, ...]:
        return tuple(
            hold
            for hold in self._holds.values()
            if hold.is_open and hold.account_id == account.account_id
        )

    def __len__(self) -> int:
        return len(self._holds)

    def __iter__(self) -> Iterator[Hold]:
        return iter(tuple(self._holds.values()))

    def __contains__(self, hold_id: object) -> bool:
        return hold_id in self._holds
