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

MIT
