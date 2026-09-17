from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.services.tax_invoice_correction_registration_lifecycle_service import (
    MAX_RK_REGISTRATION_AGE_DAYS,
    TaxInvoiceCorrectionRegistrationAgeError,
    TaxInvoiceCorrectionRegistrationTransitionError,
    validate_tax_invoice_correction_document_date,
    validate_tax_invoice_correction_registration_age,
    validate_tax_invoice_correction_registration_transition,
)
from app.services.tax_invoice_correction_source_resolver_service import (
    TaxInvoiceCorrectionLineSource,
    TaxInvoiceCorrectionSourceError,
    _purchase_adjustment_deltas,
    _recognition_reversal_deltas,
    _return_deltas,
    _value_deltas,
    normalize_correction_line_sources,
    validate_correction_source_kind_for_direction,
)


def D(value) -> Decimal:
    return Decimal(str(value))


def test_sales_return_signed_deltas():
    result = _return_deltas(
        returned_quantity=D("2"),
        returned_gross_amount=D("24"),
        returned_tax_amount=D("4"),
    )

    assert result.quantity_delta == D("-2")
    assert result.unit_price_without_vat_delta == D("0")
    assert result.taxable_base_delta == D("-20")
    assert result.tax_amount_delta == D("-4")
    assert result.total_with_vat_delta == D("-24")


def test_sales_value_correction_signed_deltas():
    result = _value_deltas(
        original_gross_amount=D("120"),
        original_tax_amount=D("20"),
        corrected_gross_amount=D("96"),
        corrected_tax_amount=D("16"),
        original_quantity=D("10"),
    )

    assert result.quantity_delta == D("0")
    assert result.unit_price_without_vat_delta == D("-2.000000")
    assert result.taxable_base_delta == D("-20")
    assert result.tax_amount_delta == D("-4")
    assert result.total_with_vat_delta == D("-24")


def test_purchase_value_increase_signed_deltas():
    result = _purchase_adjustment_deltas(
        adjustment_kind="increase",
        adjusted_taxable_base=D("20"),
        adjusted_tax_amount=D("4"),
        original_quantity=D("10"),
    )

    assert result.unit_price_without_vat_delta == D("2.000000")
    assert result.taxable_base_delta == D("20")
    assert result.tax_amount_delta == D("4")
    assert result.total_with_vat_delta == D("24")


def test_purchase_value_decrease_signed_deltas():
    result = _purchase_adjustment_deltas(
        adjustment_kind="decrease",
        adjusted_taxable_base=D("20"),
        adjusted_tax_amount=D("4"),
        original_quantity=D("10"),
    )

    assert result.unit_price_without_vat_delta == D("-2.000000")
    assert result.taxable_base_delta == D("-20")
    assert result.tax_amount_delta == D("-4")
    assert result.total_with_vat_delta == D("-24")


def test_recognition_reversal_signed_deltas():
    result = _recognition_reversal_deltas(
        original_line_quantity=D("6"),
        recognized_taxable_base=D("60"),
        recognized_tax_amount=D("12"),
    )

    assert result.quantity_delta == D("-6")
    assert result.taxable_base_delta == D("-60")
    assert result.tax_amount_delta == D("-12")
    assert result.total_with_vat_delta == D("-72")


@pytest.mark.parametrize(
    "source_kind",
    [
        "sales_return",
        "sales_value_correction",
        "recognition_reversal",
    ],
)
def test_output_source_matrix_accepts_output_sources(
    source_kind,
):
    validate_correction_source_kind_for_direction(
        direction="output",
        source_kind=source_kind,
    )


@pytest.mark.parametrize(
    "source_kind",
    [
        "purchase_return",
        "purchase_value_correction",
    ],
)
def test_input_source_matrix_accepts_input_sources(
    source_kind,
):
    validate_correction_source_kind_for_direction(
        direction="input",
        source_kind=source_kind,
    )


def test_source_matrix_rejects_input_source_for_output():
    with pytest.raises(
        TaxInvoiceCorrectionSourceError
    ):
        validate_correction_source_kind_for_direction(
            direction="output",
            source_kind="purchase_return",
        )


def test_source_matrix_rejects_output_source_for_input():
    with pytest.raises(
        TaxInvoiceCorrectionSourceError
    ):
        validate_correction_source_kind_for_direction(
            direction="input",
            source_kind="sales_return",
        )


def test_source_normalization_sorts_line_numbers():
    result = normalize_correction_line_sources(
        [
            TaxInvoiceCorrectionLineSource(
                line_number=2,
                source_kind="sales_return",
                source_id=20,
                reason_code="return",
            ),
            TaxInvoiceCorrectionLineSource(
                line_number=1,
                source_kind="sales_return",
                source_id=10,
                reason_code="return",
            ),
        ]
    )

    assert [
        item.line_number
        for item in result
    ] == [1, 2]


def test_duplicate_source_is_rejected():
    with pytest.raises(
        TaxInvoiceCorrectionSourceError,
        match="economic sources must be unique",
    ):
        normalize_correction_line_sources(
            [
                TaxInvoiceCorrectionLineSource(
                    line_number=1,
                    source_kind="sales_return",
                    source_id=10,
                    reason_code="return",
                ),
                TaxInvoiceCorrectionLineSource(
                    line_number=2,
                    source_kind="sales_return",
                    source_id=10,
                    reason_code="return",
                ),
            ]
        )


def test_duplicate_line_number_is_rejected():
    with pytest.raises(
        TaxInvoiceCorrectionSourceError,
        match="line_number values must be unique",
    ):
        normalize_correction_line_sources(
            [
                TaxInvoiceCorrectionLineSource(
                    line_number=1,
                    source_kind="sales_return",
                    source_id=10,
                    reason_code="return",
                ),
                TaxInvoiceCorrectionLineSource(
                    line_number=1,
                    source_kind="sales_return",
                    source_id=20,
                    reason_code="return",
                ),
            ]
        )


def test_output_registration_starts_prepared():
    validate_tax_invoice_correction_registration_transition(
        previous_status=None,
        new_status="prepared",
        direction="output",
    )

    with pytest.raises(
        TaxInvoiceCorrectionRegistrationTransitionError
    ):
        validate_tax_invoice_correction_registration_transition(
            previous_status=None,
            new_status="registered",
            direction="output",
        )


def test_input_registration_starts_registered():
    validate_tax_invoice_correction_registration_transition(
        previous_status=None,
        new_status="registered",
        direction="input",
    )

    with pytest.raises(
        TaxInvoiceCorrectionRegistrationTransitionError
    ):
        validate_tax_invoice_correction_registration_transition(
            previous_status=None,
            new_status="prepared",
            direction="input",
        )


def test_registered_rk_is_terminal():
    with pytest.raises(
        TaxInvoiceCorrectionRegistrationTransitionError
    ):
        validate_tax_invoice_correction_registration_transition(
            previous_status="registered",
            new_status="submitted",
            direction="output",
        )


def test_rk_document_1095_day_boundary_is_allowed():
    original = date(
        2023,
        9,
        18,
    )

    correction = (
        original
        + timedelta(
            days=MAX_RK_REGISTRATION_AGE_DAYS
        )
    )

    validate_tax_invoice_correction_document_date(
        original_invoice_date=original,
        correction_document_date=correction,
    )


def test_rk_document_1096_day_boundary_is_rejected():
    original = date(
        2023,
        9,
        18,
    )

    correction = (
        original
        + timedelta(
            days=(
                MAX_RK_REGISTRATION_AGE_DAYS
                + 1
            )
        )
    )

    with pytest.raises(
        TaxInvoiceCorrectionRegistrationAgeError
    ):
        validate_tax_invoice_correction_document_date(
            original_invoice_date=original,
            correction_document_date=correction,
        )


def test_rk_registration_1095_day_boundary_is_allowed():
    original = date(
        2023,
        9,
        18,
    )

    registration = (
        original
        + timedelta(
            days=MAX_RK_REGISTRATION_AGE_DAYS
        )
    )

    validate_tax_invoice_correction_registration_age(
        original_invoice_date=original,
        registration_date=registration,
    )


def test_rk_registration_1096_day_boundary_is_rejected():
    original = date(
        2023,
        9,
        18,
    )

    registration = (
        original
        + timedelta(
            days=(
                MAX_RK_REGISTRATION_AGE_DAYS
                + 1
            )
        )
    )

    with pytest.raises(
        TaxInvoiceCorrectionRegistrationAgeError
    ):
        validate_tax_invoice_correction_registration_age(
            original_invoice_date=original,
            registration_date=registration,
        )
