# ledger-core

Double-entry ledger for custodial balances: paired postings, holds as a
two-phase reserve, and balances that cannot drift.

## Status

Pre-alpha. Accounts, postings and entries are in place; holds and balance
projection are not implemented yet.

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

```python
from datetime import datetime, timezone
from decimal import Decimal

from ledger_core import Account, AccountType, Entry, Money

cash = Account("cash", AccountType.ASSET, "EUR")
customer = Account("customer:42", AccountType.LIABILITY, "EUR")
amount = Money(Decimal("25.00"), "EUR")

deposit = Entry(
    entry_id="e-1",
    occurred_at=datetime.now(timezone.utc),
    postings=(cash.debit(amount), customer.credit(amount)),
    memo="card deposit",
)
```

Entries are never edited or deleted. To undo one, write the correcting entry:

```python
refund = deposit.reversal(
    entry_id="e-2",
    occurred_at=datetime.now(timezone.utc),
)
```

## Development

```bash
pip install -e .
```

## License

MIT — a [polycratia](https://polycratia.com) project.
