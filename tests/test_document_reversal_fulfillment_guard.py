from datetime import date

import pytest

from app.models.document import (
    Document,
    DocumentStatus,
    DocumentType,
)
from app.services.document_reversal import (
    DocumentReversalFulfillmentLinkedError,
    reverse_document,
)


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeDB:
    def __init__(
        self,
        results,
    ):
        self.results = list(results)
        self.execute_count = 0
        self.flush_count = 0

    async def execute(
        self,
        statement,
    ):
        self.execute_count += 1

        if not self.results:
            raise AssertionError(
                "Unexpected database execute"
            )

        return FakeResult(
            self.results.pop(0)
        )

    async def flush(self):
        self.flush_count += 1


def make_posted_issue() -> Document:
    document = Document(
        company_id=1,
        accounting_rule_id=5,
        number="FULFILL-ISSUE-1",
        document_type=DocumentType.ISSUE,
        document_date=date(2026, 8, 27),
        status=DocumentStatus.POSTED,
        created_by=1,
    )

    document.id = 100

    return document


@pytest.mark.asyncio
async def test_linked_fulfillment_issue_cannot_be_generically_reversed():
    document = make_posted_issue()

    # First query:
    #   locked warehouse Document
    #
    # Second query:
    #   matching TradeFulfillment.id
    db = FakeDB(
        [
            document,
            500,
        ]
    )

    with pytest.raises(
        DocumentReversalFulfillmentLinkedError,
        match=(
            "linked to a trade fulfillment"
        ),
    ):
        await reverse_document(
            db=db,
            company_id=1,
            document_id=100,
            reversal_date=date(
                2026,
                8,
                27,
            ),
            reversed_by=1,
        )

    # Guard must stop execution immediately after:
    #
    #   1. document lock
    #   2. fulfillment lookup
    #
    # No accounting-period query, stock reversal,
    # FIFO reversal or accounting reversal may begin.
    assert db.execute_count == 2

    assert db.flush_count == 0

    assert (
        document.status
        == DocumentStatus.POSTED
    )


@pytest.mark.asyncio
async def test_fulfillment_reversal_guard_error_is_business_error():
    from app.services.document_reversal import (
        DocumentReversalError,
    )

    assert issubclass(
        DocumentReversalFulfillmentLinkedError,
        DocumentReversalError,
    )
