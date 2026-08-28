from pathlib import Path

from app.models.document import DocumentType
from app.models.trade_fulfillment import (
    TradeFulfillment,
)


def test_supported_fulfillment_target_types():
    constraint = next(
        constraint
        for constraint
        in TradeFulfillment.__table__.constraints
        if (
            constraint.name
            == (
                "ck_trade_fulfillment_"
                "warehouse_document_type"
            )
        )
    )

    sql = str(
        constraint.sqltext
    )

    assert (
        DocumentType.ISSUE.value
        in sql
    )

    assert (
        DocumentType.RECEIPT.value
        in sql
    )


def test_sales_executor_sets_issue_explicitly():
    text = Path(
        "app/services/trade_fulfillment_service.py"
    ).read_text()

    assert (
        "warehouse_document_type=(\n"
        "            DocumentType.ISSUE.value\n"
        "        )"
        in text
    )
