# ledger-core

Double-entry ledger for custodial balances: paired postings, holds as a
two-phase reserve, and balances that cannot drift.

## Status

Pre-alpha. Accounts, postings, entries, the journal, balances and holds are in
place.

## Installation

```bash
pip install ledger-core
```

## Money

The ledger does not own a money type. It posts any value that exposes an exact
`amount`, the `currency` it is denominated in, and `+`, `-`, unary `-` and
ordering — the `MoneyLike` protocol. The recommended pairing is
[cryptomoney](https://github.com/polycratia/cryptomoney):

```bash
pip install "ledger-core[crypto]"
```

Until that package is released, `ledger_core.Money` is a small stand-in so the
ledger stays usable and testable on its own. Swapping it out is an import
change; nothing else in the API moves.

## Usage

A movement has two sides and one id. `Entry.transfer` writes both:

```python
from datetime import datetime, timezone
from decimal import Decimal

from ledger_core import Account, AccountType, Entry, Journal, Money

cash = Account("cash", AccountType.ASSET, "EUR")
customer = Account("customer:42", AccountType.LIABILITY, "EUR")

deposit = Entry.transfer(
    entry_id="e-1",
    occurred_at=datetime.now(timezone.utc),
    debit=cash,
    credit=customer,
    amount=Money(Decimal("25.00"), "EUR"),
    memo="card deposit",
)
```

An entry that does not net to zero in every currency it touches raises
`UnbalancedEntry` at construction, so a one-sided movement never exists as an
object. Entries with more than two postings are fine as long as they sum to
zero.

The journal keeps them, and keeps them whole:

```python
journal = Journal()
journal.append(deposit)

for posting in journal.postings("cash"):
    print(posting.entry_id, posting.side.value, posting.amount)
```

`Journal.extend` writes a batch under the same rule: if any entry in it is
rejected — a duplicate id, a correction of something nobody wrote — none of
the batch is kept.

Entries are never edited or deleted. To undo one, write the correcting entry:

```python
refund = deposit.reversal(
    entry_id="e-2",
    occurred_at=datetime.now(timezone.utc),
)
journal.append(refund)
```

## Balances

A balance is not a stored number the ledger edits. It is what the entries add
up to, counted in the account's normal direction:

```python
from ledger_core import Balances

balances = Balances(journal)
balances.balance(cash)  # Decimal("25.00")
```

Reading a balance writes nothing, so two readers cannot overwrite each other's
arithmetic — the way a mutable column updated by read-modify-write loses a
movement.

Folding the whole journal on every read gets slower as the journal grows, so
each fold is kept as a snapshot: how far it got, and what it had by then. The
next read resumes from there and replays only what was written since.

```python
snapshot = balances.snapshot(customer)
snapshot.total    # what the account held
snapshot.through  # how many entries are folded into it
snapshot.as_of    # when the last of them occurred
```

Snapshots are plain values, so they can be stored and handed back later:

```python
balances = Balances(journal, [snapshot])
```

A snapshot is only ever replaced by one that reaches further, never edited,
and one claiming more entries than the journal holds is refused with
`SnapshotMismatch`. A stale snapshot therefore costs a replay, never a wrong
answer.

A balance as of a moment ignores snapshots — a position in the journal says
nothing about a point in time — and replays in full:

```python
balances.at(customer, datetime.now(timezone.utc))
```

## Holds

A withdrawal is not one moment but two: the funds stop being spendable now,
and they leave later — or not at all. A hold covers the gap. It writes
nothing; it only stands in front of the balance.

```python
from ledger_core import Holds

holds = Holds(journal)

holds.place(
    hold_id="h-1",
    account=customer,
    amount=Money(Decimal("10.00"), "EUR"),
    placed_at=datetime.now(timezone.utc),
    memo="withdrawal to IBAN",
)

holds.balance(customer)    # what the account holds
holds.held(customer)       # what open holds have reserved
holds.available(customer)  # balance minus holds
```

Holds read through the same projection: pass `Holds(journal, balances=balances)`
to share snapshots with an existing one, or let it build its own.

A hold that does not fit in the available balance raises `InsufficientFunds`:
the reservation is refused rather than the account going short. Amounts come
back as `Decimal` in the account's currency — the ledger owns no money type,
so it cannot mint the zero an empty balance would need.

If the withdrawal falls through, the reservation goes back:

```python
holds.release("h-1", at=datetime.now(timezone.utc))
```

If it goes through, the hold is settled by the entry that moves the money:

```python
payout = Entry.transfer(
    entry_id="e-3",
    occurred_at=datetime.now(timezone.utc),
    debit=customer,
    credit=cash,
    amount=Money(Decimal("10.00"), "EUR"),
    memo="withdrawal settled",
)
holds.capture("h-1", payout)
```

The entry has to take exactly what was held, or `CaptureMismatch` is raised
and nothing is written. A settled hold never reopens: releasing or capturing
it twice raises `HoldNotOpen`.

## Development

```bash
pip install -e .
```

## License

MIT — a [polycratia](https://polycratia.com) project.
