from decimal import Decimal

import pytest

import app.models  # noqa: F401
from app.core.database import Base
from app.services.tax_invoice_input_persistence_service import (
    InputTaxInvoiceLineAttestation,
    InputTaxInvoiceSnapshotError,
    validate_input_line_attestations,
)
from app.services.tax_invoice_registration_lifecycle_service import (
    TaxInvoiceRegistrationTransitionError,
    validate_tax_invoice_registration_transition,
)


def _line(
    *,
    line_number=1,
    claim_id=10,
    quantity="2",
    kind="uktzed",
):
    return InputTaxInvoiceLineAttestation(
        line_number=line_number,
        claim_id=claim_id,
        description="Product",
        quantity=Decimal(quantity),
        uom_code="pcs",
        classification_kind=kind,
        statutory_code="1234567890",
    )


def test_credit_evidence_link_model_contract():
    table = Base.metadata.tables[
        "tax_invoice_credit_evidence_links"
    ]

    assert {
        "id",
        "company_id",
        "tax_invoice_id",
        "tax_credit_evidence_id",
        "tax_calculation_id",
    } == set(table.c.keys())

    names = {
        item.name
        for item in table.constraints
        if item.name
    }

    assert "uq_ticel_evidence" in names
    assert "uq_ticel_invoice_calc" in names
    assert "fk_ticel_invoice" in names
    assert "fk_ticel_evidence" in names


def test_input_lines_are_sorted_by_legal_line_number():
    result = validate_input_line_attestations(
        [
            _line(
                line_number=2,
                claim_id=20,
            ),
            _line(
                line_number=1,
                claim_id=10,
            ),
        ]
    )

    assert [
        item.line_number
        for item in result
    ] == [1, 2]


def test_duplicate_claim_is_rejected():
    with pytest.raises(
        InputTaxInvoiceSnapshotError,
        match="claim_id values must be unique",
    ):
        validate_input_line_attestations(
            [
                _line(
                    line_number=1,
                    claim_id=10,
                ),
                _line(
                    line_number=2,
                    claim_id=10,
                ),
            ]
        )


def test_duplicate_line_number_is_rejected():
    with pytest.raises(
        InputTaxInvoiceSnapshotError,
        match="line_number values must be unique",
    ):
        validate_input_line_attestations(
            [
                _line(
                    line_number=1,
                    claim_id=10,
                ),
                _line(
                    line_number=1,
                    claim_id=20,
                ),
            ]
        )


@pytest.mark.parametrize(
    "kind",
    [
        "unknown",
        "",
    ],
)
def test_invalid_statutory_classification_is_rejected(
    kind,
):
    with pytest.raises(
        InputTaxInvoiceSnapshotError
    ):
        validate_input_line_attestations(
            [
                _line(
                    kind=kind,
                )
            ]
        )


def test_input_registration_can_start_registered():
    validate_tax_invoice_registration_transition(
        previous_status=None,
        new_status="registered",
        direction="input",
    )


@pytest.mark.parametrize(
    "status",
    [
        "prepared",
        "submitted",
        "suspended",
        "rejected",
    ],
)
def test_input_v1_cannot_start_nonregistered(
    status,
):
    with pytest.raises(
        TaxInvoiceRegistrationTransitionError
    ):
        validate_tax_invoice_registration_transition(
            previous_status=None,
            new_status=status,
            direction="input",
        )


def test_output_still_must_start_prepared():
    validate_tax_invoice_registration_transition(
        previous_status=None,
        new_status="prepared",
        direction="output",
    )

    with pytest.raises(
        TaxInvoiceRegistrationTransitionError
    ):
        validate_tax_invoice_registration_transition(
            previous_status=None,
            new_status="registered",
            direction="output",
        )
