from copy import deepcopy
from datetime import date

import pytest

from app.services.payroll_regulatory_withholding_source_reconciliation import (
    WithholdingReconciliationError,
    reconcile_sources,
    digest,
    verify_source_reconciliation,
    SCHEMA,
)


def args():
    return dict(
        gross="1000.00",
        statutory_net="770.00",
        statutory_withholding="230.00",
        pit_accrued="180.00",
        levy_accrued="50.00",
        posted_payments=[
            {
                "id": 10,
                "payment_date": date(2026, 9, 15),
                "amount": "300.00",
                "non_tax_withheld": "20.00",
            },
            {
                "id": 11,
                "payment_date": date(2026, 9, 30),
                "amount": "400.00",
                "non_tax_withheld": "10.00",
            },
        ],
    )


def test_partial_payments_reconcile():
    result = reconcile_sources(**args())
    assert result["posted_cash_paid"] == "700.00"
    assert result["non_tax_withheld"] == "30.00"
    assert result[
        "remaining_net_before_other_settlements"
    ] == "40.00"
    assert result["posted_payment_count"] == 2
    assert result["payment_specific_withholding"] == "NOT_VERIFIED"
    assert result["gross_paid_attribution"] == "NOT_APPROVED"


def test_no_pit_inferred_from_non_tax_withholding():
    result = reconcile_sources(**args())
    assert all(
        row["pit_at_this_payment"] == "NOT_VERIFIED"
        for row in result["payments"]
    )


def test_duplicate_payment_rejected():
    data = args()
    data["posted_payments"].append(
        deepcopy(data["posted_payments"][0])
    )
    with pytest.raises(
        WithholdingReconciliationError, match="Duplicate"
    ):
        reconcile_sources(**data)


def test_excess_paid_rejected():
    data = args()
    data["posted_payments"][1]["amount"] = "500.00"
    with pytest.raises(
        WithholdingReconciliationError, match="exceed"
    ):
        reconcile_sources(**data)


def test_wrong_tax_components_rejected():
    data = args()
    data["pit_accrued"] = "179.99"
    with pytest.raises(
        WithholdingReconciliationError, match="PIT/levy"
    ):
        reconcile_sources(**data)


def test_wrong_gross_net_rejected():
    data = args()
    data["statutory_net"] = "771.00"
    with pytest.raises(
        WithholdingReconciliationError, match="gross/net"
    ):
        reconcile_sources(**data)


def test_no_payment_is_not_tax_approval():
    data = args()
    data["posted_payments"] = []
    result = reconcile_sources(**data)
    assert result["posted_payment_count"] == 0
    assert result["gross_paid_attribution"] == "NOT_APPROVED"


def test_unsupported_float_rejected():
    data = args()
    data["pit_accrued"] = 180.0
    with pytest.raises(WithholdingReconciliationError):
        reconcile_sources(**data)


def test_multiple_payment_dates_preserved():
    result = reconcile_sources(**args())
    assert result["posted_payment_dates"] == [
        "2026-09-15", "2026-09-30"
    ]


def test_document_approval_tamper_rejected():
    body = {
        "schema": SCHEMA,
        "read_only": True,
        "official_generation": "BLOCKED",
        "reconciliation": {
            "payment_specific_withholding": "NOT_VERIFIED",
            "gross_paid_attribution": "NOT_APPROVED",
            "official_generation": "BLOCKED",
        },
    }
    document = {"body": body, "sha256": digest(body)}
    assert verify_source_reconciliation(document)

    changed = deepcopy(document)
    changed["body"]["reconciliation"][
        "gross_paid_attribution"
    ] = "APPROVED"
    changed["sha256"] = digest(changed["body"])

    with pytest.raises(WithholdingReconciliationError):
        verify_source_reconciliation(changed)
