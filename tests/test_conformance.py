"""The conformance suite, run against the store the protocol is read against."""

from __future__ import annotations

import pytest

from ledger_core import Entry, InMemoryStorage, Storage
from ledger_core.testing import StorageConformance, checks, conformance_failures


class TestInMemoryStorage(StorageConformance):
    """The reference store answers the whole contract."""

    def new_storage(self) -> Storage:
        return InMemoryStorage()


class Leaky(InMemoryStorage):
    """A double that hands out the list it keeps its entries in."""

    def entries(self) -> tuple[Entry, ...]:
        return self._entries  # type: ignore[return-value]


def test_the_reference_store_fails_nothing() -> None:
    assert conformance_failures(InMemoryStorage) == ()
    assert len(checks()) > 1


def test_a_store_handing_out_its_own_list_is_caught() -> None:
    assert conformance_failures(Leaky) == ("test_a_read_of_the_entries_is_a_copy",)


def test_a_suite_that_names_no_store_says_so() -> None:
    with pytest.raises(NotImplementedError):
        StorageConformance().new_storage()
