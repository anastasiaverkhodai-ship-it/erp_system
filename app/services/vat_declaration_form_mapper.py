"""
Map an immutable VatDeclaration snapshot to the canonical S11 form document.

This layer is deliberately independent from XML transport syntax.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.services.vat_declaration_form_catalog import (
    VatDeclarationFormDefinition,
)


class VatDeclarationFormMappingError(ValueError):
    pass


@dataclass(frozen=True)
class VatDeclarationFormDocument:
    form_code: str
    form_version: int

    declaration_id: int
    declaration_snapshot_version: int

    reporting_year: int
    reporting_month: int

    company_name: str
    company_edrpou: str
    company_vat_number: str

    output_taxable_base: Decimal
    output_vat: Decimal
    input_taxable_base: Decimal
    input_vat_credit: Decimal
    opening_negative_carry: Decimal
    vat_payable: Decimal
    current_period_negative: Decimal
    closing_negative_carry: Decimal

    currency_code: str


def _required_text(value: Any, *, field: str) -> str:
    text = "" if value is None else str(value).strip()

    if not text:
        raise VatDeclarationFormMappingError(
            f"{field} is required for VAT declaration export"
        )

    return text


def map_vat_declaration_to_form(
    *,
    declaration: Any,
    company: Any,
    company_vat_policy: Any,
    form: VatDeclarationFormDefinition,
) -> VatDeclarationFormDocument:
    if declaration.company_id != company.id:
        raise VatDeclarationFormMappingError(
            "Declaration/company tenant mismatch"
        )

    if company_vat_policy.company_id != company.id:
        raise VatDeclarationFormMappingError(
            "VAT policy/company tenant mismatch"
        )

    if company_vat_policy.payer_status != "vat_payer":
        raise VatDeclarationFormMappingError(
            "VAT declaration export requires VAT payer status"
        )

    if declaration.currency_code != "UAH":
        raise VatDeclarationFormMappingError(
            "VAT declaration export supports UAH only"
        )

    if not (
        company_vat_policy.effective_from
        <= declaration.period_end
    ):
        raise VatDeclarationFormMappingError(
            "VAT policy is not effective for declaration period"
        )

    return VatDeclarationFormDocument(
        form_code=form.form_code,
        form_version=form.form_version,
        declaration_id=declaration.id,
        declaration_snapshot_version=(
            declaration.snapshot_version
        ),
        reporting_year=declaration.reporting_year,
        reporting_month=declaration.reporting_month,
        company_name=_required_text(
            company.name,
            field="company.name",
        ),
        company_edrpou=_required_text(
            company.edrpou,
            field="company.edrpou",
        ),
        company_vat_number=_required_text(
            company_vat_policy.vat_number,
            field="company_vat_policy.vat_number",
        ),
        output_taxable_base=declaration.output_taxable_base,
        output_vat=declaration.output_vat,
        input_taxable_base=declaration.input_taxable_base,
        input_vat_credit=declaration.input_vat_credit,
        opening_negative_carry=declaration.opening_negative_carry,
        vat_payable=declaration.vat_payable,
        current_period_negative=(
            declaration.current_period_negative
        ),
        closing_negative_carry=(
            declaration.closing_negative_carry
        ),
        currency_code=declaration.currency_code,
    )
