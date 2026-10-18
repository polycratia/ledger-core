# ledger-core

Double-entry ledger for custodial balances: paired postings, holds as a
two-phase reserve, and balances that cannot drift.

## Status

Pre-alpha. Accounts, postings, entries, the journal, balances, holds,
idempotent operations, the storage protocol they keep their records in — with
guarded writes landing as one step — and the invariants behind them are in
place.

## Installation

```bash
pip install ledger-core
```

## The rules

Three ideas carry everything below: why a movement is written twice, what a
hold stands in front of, and what stays true when calls overlap.

### Why double entry

Money is not created by a balance going up. It moves, and a movement has a
side it left and a side it arrived at. A ledger that keeps one number per
account records only the result, so a number that drifted looks exactly like a
number that is right, and nothing else in the data disagrees with it.

Double entry records the movement instead. The unit of writing is the entry:
the postings it is made of, summing to zero in every currency they touch. That
sum is checked when the entry is built, so a one-sided movement never becomes
an object, and never reaches the journal to be discovered later.

A balance follows from the entries rather than being kept beside them — it is
what an account's postings add up to, counted in the direction the account
normally moves. Nothing edits it, so nothing can edit it wrongly. A mistake is
fixed the way a paper ledger fixes one: by writing the entry that cancels the
first, which leaves both of them readable afterwards.

### What a hold is

A withdrawal is not one moment but two. The funds stop being spendable when
the customer asks for them; they leave when the payout confirms — hours later,
or never. Treating those as one moment either lets the same funds be spent
twice in between, or takes them away before they are gone.

A hold covers the gap. It reserves an amount against an account and writes
nothing at all: the balance does not move, and what changes is what may be
spent. Available is the balance less every open hold, and a reservation that
does not fit in it is refused rather than granted.

The second phase settles the hold, once. A release gives the reservation back
— the withdrawal fell through, and the ledger says nothing happened, because
nothing did. A capture settles it against the entry that moves the money, and
that entry has to take exactly what was held. Either way the hold closes for
good, so a redelivered instruction cannot spend one reservation twice.

### What holds when calls overlap

Reading a balance writes nothing, and that is the whole of the first
guarantee. The way a stored balance loses a movement is two writers reading
the same number, each adding their own amount, each writing back a total the
other never saw. There is no number here to write back.

The journal is append-only and nothing already in it is rewritten, so a
position in it keeps meaning what it meant: everything before it is accounted
for, everything from it is not. A snapshot is exactly that — how far a fold
got, and what it had by then. It is only ever replaced by one that reaches
further, and one claiming more entries than the journal holds is refused. A
stale snapshot therefore costs the replay of what it is missing, never a wrong
answer, and losing every snapshot costs time and nothing else.

Retries are carried by the key a write was asked for under, not by the entry:
the first attempt writes, every attempt after it returns what the first one
wrote, and the same key coming back with different work is refused instead of
posted.

A guarded write is two steps: asking whether the funds are free, whether the
key is already settled, whether the hold is still open — and then writing.
Both run under one block the store opens, `Storage.atomic`, so they land
together or not at all: a store that refuses the hold leaves no entry behind.
How wide that promise is belongs to the store. The in-memory one puts its
records back and lets one writer through a block at a time; a backend that
keeps rows in a database makes it the transaction it already has.

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
snapshot.side     # the direction it was counted in
```

Snapshots are plain values, so they can be stored and handed back later:

```python
balances = Balances(journal, [snapshot])
```

A snapshot is only ever replaced by one that reaches further, never edited,
and one claiming more entries than the journal holds is refused with
`SnapshotMismatch`. So is one folded in the other direction: the same account
id read as an asset and then as a liability would otherwise resume from a base
whose sign has flipped. A stale snapshot therefore costs a replay, never a
wrong answer.

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

## Idempotency

Deposit callbacks arrive more than once. Retrying the entry is no answer: the
journal refuses a duplicate id, and a refusal does not tell the caller whether
the money landed. What repeats safely is the key the write was asked for
under:

```python
from ledger_core import Operations

operations = Operations(journal)

topup = Entry.transfer(
    entry_id="e-4",
    occurred_at=datetime.now(timezone.utc),
    debit=cash,
    credit=customer,
    amount=Money(Decimal("40.00"), "EUR"),
    memo="deposit callback",
)

operations.post("callback:9f3", topup)  # writes the entry
operations.post("callback:9f3", topup)  # returns it, writes nothing
```

`Operations.post_many` puts a whole batch under one key, under the journal's
rule: all of it or none of it.

A key is bound to the work it was first applied to. Coming back with the same
key and different entries raises `OperationMismatch`, rather than posting them
or answering with the wrong ones.

Records are plain values, so a key settled in one process stays settled in the
next:

```python
record = operations.operation("callback:9f3")
record.entry_ids
record.as_of

operations = Operations(journal, [record])
```

A record naming entries the journal does not hold is refused with
`OperationMismatch`, so a key can never be settled by a write nobody made.

## Storage

Nothing above keeps records of its own. Entries, snapshots, holds and settled
keys all go through one narrow contract — `Storage` — and a journal handed none
builds an `InMemoryStorage` for itself:

```python
from ledger_core import InMemoryStorage, Journal

storage = InMemoryStorage()
journal = Journal(storage=storage)
```

The projections write through the store their journal stands on, so a
`Balances`, a `Holds` and an `Operations` over one journal share what each of
them learned — including the ones built afterwards:

```python
holds = Holds(journal)
holds.place(
    hold_id="h-9",
    account=customer,
    amount=Money(Decimal("5.00"), "EUR"),
    placed_at=datetime.now(timezone.utc),
)

Holds(journal).held(customer)  # Decimal("5.00") — the same reservation
```

`InMemoryStorage` is the reference the protocol is read against, and the double
the tests run on. A backend that keeps rows in a database conforms by answering
the same questions the same way; nothing above it changes.

### Writes that land together

A check and the write it guards have to be one step, or a refusal in the
second leaves the first behind. `Storage.atomic` is that step:

```python
with storage.atomic():
    journal.append(deposit)
    journal.append(refund)
```

Leaving the block by an exception undoes everything written inside it, and a
block opened inside another joins it rather than settling on its own.
`Holds.place`, `Holds.spend`, `Holds.capture` and `Operations.post_many`
already run their check and their write inside one, so a store that refuses
the hold or the operation record keeps no entry from the attempt.

## Invariants

Three things hold whatever order the calls arrive in. Each is checked where it
can be broken rather than where a caller remembered to look, each refuses the
write instead of recording it, and each refusal says which promise it kept:

| Refusal | What it protects |
| --- | --- |
| `UnbalancedEntry` | every movement carries both of its sides, so no write creates or destroys money |
| `InsufficientFunds` | available balances stay at or above zero, so the same funds are never spent twice |
| `ClosedAccount` | a closed account is final, so its balance cannot move after it is settled |

All three are `InvariantViolated`, so code that only needs to know the write
did not happen catches one thing:

```python
from ledger_core import InvariantViolated

try:
    holds.spend(payout, account=customer)
except InvariantViolated as refused:
    print(refused)  # ... — protects: ...
```

`Holds.spend` is the guarded write: an entry balances on its own, but only the
holds know whether what it takes is still free. Appending it directly answers
the first question and not the second.

Closing an account is a value, not an edit — the closed account is a new one,
and it builds no postings:

```python
settled = customer.close(at=datetime.now(timezone.utc))
settled.is_open                             # False
settled.debit(Money(Decimal("1.00"), "EUR"))  # ClosedAccount
```

History already written stays readable: a closed account still has a balance,
it simply stops receiving movements.

## Development

```bash
pip install -e ".[dev]"
python -m pytest
```

## License

MIT — a [polycratia](https://polycratia.com) project.
