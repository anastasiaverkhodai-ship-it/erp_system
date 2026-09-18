"""
Deterministic S11 VAT declaration export.

Important:
This produces the ERP's versioned canonical XML artifact.
It MUST NOT be represented as official-DPS-XSD validated while
official_xsd_verified is False for the selected form contract.
"""

from dataclasses import asdict
from decimal import Decimal
import hashlib
from xml.etree import ElementTree as ET

from app.services.vat_declaration_form_catalog import (
    VatDeclarationFormDefinition,
)
from app.services.vat_declaration_form_mapper import (
    VatDeclarationFormDocument,
)


class VatDeclarationExportError(ValueError):
    pass


def _decimal(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


def validate_form_document(
    *,
    document: VatDeclarationFormDocument,
    form: VatDeclarationFormDefinition,
) -> None:
    if document.form_code != form.form_code:
        raise VatDeclarationExportError(
            "Form code does not match selected form contract"
        )

    if document.form_version != form.form_version:
        raise VatDeclarationExportError(
            "Form version does not match selected form contract"
        )

    if document.currency_code != "UAH":
        raise VatDeclarationExportError(
            "Official VAT declaration export supports UAH only"
        )

    if len(document.company_edrpou) != 8:
        raise VatDeclarationExportError(
            "Company EDRPOU must contain exactly 8 characters"
        )

    if not document.company_edrpou.isdigit():
        raise VatDeclarationExportError(
            "Company EDRPOU must contain digits only"
        )

    if not document.company_vat_number.strip():
        raise VatDeclarationExportError(
            "Company VAT number is required"
        )


def render_canonical_xml(
    *,
    document: VatDeclarationFormDocument,
    form: VatDeclarationFormDefinition,
) -> bytes:
    validate_form_document(
        document=document,
        form=form,
    )

    root = ET.Element(
        "VAT_DECLARATION",
        {
            "form": document.form_code,
            "version": str(document.form_version),
        },
    )

    header = ET.SubElement(root, "HEADER")

    values = (
        ("DECLARATION_ID", str(document.declaration_id)),
        (
            "SNAPSHOT_VERSION",
            str(document.declaration_snapshot_version),
        ),
        ("REPORTING_YEAR", str(document.reporting_year)),
        ("REPORTING_MONTH", str(document.reporting_month)),
        ("COMPANY_NAME", document.company_name),
        ("COMPANY_EDRPOU", document.company_edrpou),
        ("COMPANY_VAT_NUMBER", document.company_vat_number),
        ("CURRENCY", document.currency_code),
    )

    for tag, value in values:
        ET.SubElement(header, tag).text = value

    body = ET.SubElement(root, "BODY")

    amounts = (
        (
            "OUTPUT_TAXABLE_BASE",
            document.output_taxable_base,
        ),
        ("OUTPUT_VAT", document.output_vat),
        (
            "INPUT_TAXABLE_BASE",
            document.input_taxable_base,
        ),
        (
            "INPUT_VAT_CREDIT",
            document.input_vat_credit,
        ),
        (
            "OPENING_NEGATIVE_CARRY",
            document.opening_negative_carry,
        ),
        ("VAT_PAYABLE", document.vat_payable),
        (
            "CURRENT_PERIOD_NEGATIVE",
            document.current_period_negative,
        ),
        (
            "CLOSING_NEGATIVE_CARRY",
            document.closing_negative_carry,
        ),
    )

    for tag, value in amounts:
        ET.SubElement(body, tag).text = _decimal(value)

    ET.indent(root, space="  ")

    payload = ET.tostring(
        root,
        encoding="utf-8",
        xml_declaration=True,
        short_empty_elements=True,
    )

    return payload


def export_sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def export_metadata(
    *,
    document: VatDeclarationFormDocument,
    form: VatDeclarationFormDefinition,
    payload: bytes,
) -> dict[str, object]:
    return {
        "form_code": form.form_code,
        "form_version": form.form_version,
        "format": form.export_format,
        "official_xsd_verified": (
            form.official_xsd_verified
        ),
        "declaration_id": document.declaration_id,
        "declaration_snapshot_version": (
            document.declaration_snapshot_version
        ),
        "sha256": export_sha256(payload),
        "size_bytes": len(payload),
    }
