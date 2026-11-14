"""Source-anchored candidate catalogue for Ukrainian payroll forms.

This catalogue records identified form fields and applicability rules.
It is NOT a legally approved statutory mapping.

Important separation:

* A source URL is not a verified document artifact.
* A field label is not a verified payroll-to-form allocation.
* A publication/revision date is not automatically the effective date.
* An internal review is not approval for statutory generation.

The production approved mapping registry and D5 classifier approval
remain independently authoritative and fail closed.
"""

from dataclasses import dataclass
from datetime import date
from typing import Literal


SOURCE_ORDER_4 = (
    "https://zakon.rada.gov.ua/laws/show/z0111-15"
)

SOURCE_AMENDMENT_243 = (
    "https://zakon.rada.gov.ua/laws/show/z0751-26"
)

CATALOGUE_STATUS = "CANDIDATE"

KINDS = ("d1", "4df", "d5", "d6")


class OfficialFieldCatalogueError(ValueError):
    pass


@dataclass(frozen=True)
class CandidateField:
    report_kind: Literal["d1", "4df", "d5", "d6"]
    field_key: str
    official_label: str
    source_url: str
    source_location: str
    candidate_source_key: str | None
    rule: str
    status: str = "CANDIDATE"


# These are REVIEW CANDIDATES, not approved mappings.
# The source location is a human-readable anchor for verification.
# It is not a certified PDF page/paragraph or artifact digest.

FIELD_CANDIDATES = (
    CandidateField(
        report_kind="4df",
        field_key="section_i_column_2",
        official_label="РНОКПП або передбачені паспортні дані",
        source_url=SOURCE_AMENDMENT_243,
        source_location="Порядок, додаток 4ДФ, розділ I, графа 2",
        candidate_source_key="taxpayer_identity",
        rule="Ідентифікація платника за підтвердженими даними",
    ),
    CandidateField(
        report_kind="4df",
        field_key="section_i_column_3a",
        official_label="Сума нарахованого доходу",
        source_url=SOURCE_AMENDMENT_243,
        source_location="Порядок, додаток 4ДФ, розділ I, графа 3а",
        candidate_source_key="gross_income_review",
        rule="Нарахований дохід не залежить від факту виплати",
    ),
    CandidateField(
        report_kind="4df",
        field_key="section_i_column_3",
        official_label="Сума виплаченого доходу",
        source_url=SOURCE_AMENDMENT_243,
        source_location="Порядок, додаток 4ДФ, розділ I, графа 3",
        candidate_source_key="dated_income_payment_review",
        rule="Потребує перевірки правил періоду та фактичної виплати",
    ),
    CandidateField(
        report_kind="4df",
        field_key="section_i_column_4a",
        official_label="Сума нарахованого ПДФО",
        source_url=SOURCE_AMENDMENT_243,
        source_location="Порядок, додаток 4ДФ, розділ I, графа 4а",
        candidate_source_key="pit_accrual_review",
        rule="Визначається за підтвердженим нарахуванням ПДФО",
    ),
    CandidateField(
        report_kind="4df",
        field_key="section_i_column_6",
        official_label="Ознака доходу",
        source_url=SOURCE_AMENDMENT_243,
        source_location="Порядок, додаток 4ДФ, розділ I, графа 6",
        candidate_source_key=None,
        rule="Код лише за перевіреним довідником ознак доходів",
    ),
    CandidateField(
        report_kind="d1",
        field_key="insured_person_accrual_review",
        official_label="Відомості про нарахування доходу застрахованим особам",
        source_url=SOURCE_ORDER_4,
        source_location="Розрахунок ЮО, додаток Д1",
        candidate_source_key="d1_dated_sources_review",
        rule="Номери граф і коди категорій потребують окремої перевірки",
    ),
    CandidateField(
        report_kind="d5",
        field_key="employment_event_applicability",
        official_label="Відомості про трудові відносини",
        source_url=SOURCE_AMENDMENT_243,
        source_location="Порядок, додаток Д5, правила формування",
        candidate_source_key="verified_employment_event_review",
        rule="Форма застосовується за передбаченими кадровими подіями",
    ),
    CandidateField(
        report_kind="d5",
        field_key="termination_legal_ground_review",
        official_label="Правова підстава припинення трудових відносин",
        source_url=SOURCE_AMENDMENT_243,
        source_location="Порядок, додаток Д5, правила звільнення",
        candidate_source_key=None,
        rule="Формулювання й посилання на закон мають бути точними",
    ),
    CandidateField(
        report_kind="d6",
        field_key="special_service_period_applicability",
        official_label="Підстави для обліку спеціального стажу",
        source_url=SOURCE_AMENDMENT_243,
        source_location="Порядок, додаток Д6, призначення",
        candidate_source_key=None,
        rule="Не можна виводити застосовність лише з payroll income",
    ),
)


def validate_catalogue(records=FIELD_CANDIDATES):
    records = tuple(records)
    seen = set()

    for row in records:
        if not isinstance(row, CandidateField):
            raise OfficialFieldCatalogueError(
                "CandidateField required"
            )

        if row.report_kind not in KINDS:
            raise OfficialFieldCatalogueError(
                "Unsupported form kind"
            )

        if row.status != "CANDIDATE":
            raise OfficialFieldCatalogueError(
                "Catalogue cannot approve statutory mappings"
            )

        for value in (
            row.field_key,
            row.official_label,
            row.source_location,
            row.rule,
        ):
            if not isinstance(value, str) or not value.strip():
                raise OfficialFieldCatalogueError(
                    "Incomplete candidate field"
                )

        if row.source_url not in {
            SOURCE_ORDER_4,
            SOURCE_AMENDMENT_243,
        }:
            raise OfficialFieldCatalogueError(
                "Unrecognized source anchor"
            )

        if (
            row.candidate_source_key is not None
            and (
                not isinstance(row.candidate_source_key, str)
                or not row.candidate_source_key.strip()
            )
        ):
            raise OfficialFieldCatalogueError(
                "Invalid source candidate"
            )

        identity = (row.report_kind, row.field_key)
        if identity in seen:
            raise OfficialFieldCatalogueError(
                "Duplicate field candidate"
            )
        seen.add(identity)

    return records


def catalogue_for_form(kind: str):
    if kind not in KINDS:
        raise OfficialFieldCatalogueError(
            "Unsupported form kind"
        )

    validate_catalogue()

    return tuple(
        row for row in FIELD_CANDIDATES
        if row.report_kind == kind
    )


def field_review_status(
    *,
    period_start: date,
    period_end: date,
):
    if (
        not isinstance(period_start, date)
        or not isinstance(period_end, date)
        or period_start > period_end
    ):
        raise OfficialFieldCatalogueError(
            "Invalid reporting period"
        )

    validate_catalogue()

    forms = {}

    for kind in KINDS:
        candidates = catalogue_for_form(kind)

        from app.services.payroll_regulatory_xsd_contract import (
            FormSchemaError, form_structure,
        )
        try:
            structure = form_structure(kind)
            schema_artifact = {
                "status": "OFFICIAL_BYTES_VERIFIED",
                "form_code": structure["form_code"],
                "schema_sha256": structure["schema_sha256"],
                "common_types_sha256": structure["common_types_sha256"],
                "statutory_mapping_approved": False,
            }
        except FormSchemaError:
            schema_artifact = {
                "status": "UNAVAILABLE_OR_CHANGED",
                "statutory_mapping_approved": False,
            }

        from app.services.payroll_regulatory_form_fields import (
            FormFieldError, field_contract,
        )
        try:
            verified_locations = field_contract(kind)
            location_count = len(verified_locations["fields"])
            location_source = "OFFICIAL_DPS_LABELLED_PDF"
        except (FormFieldError, FormSchemaError, OSError):
            location_count = 0
            location_source = "UNAVAILABLE_OR_CHANGED"

        forms[kind] = {
            "candidate_fields": len(candidates),
            "source_anchors": sorted({
                row.source_url for row in candidates
            }),
            "document_artifacts_verified": 3,
            "verified_field_locations": location_count,
            "field_location_source": location_source,
            "effective_date_coverage_verified": False,
            "field_mapping_approved": 0,
            "complete_form_schema_verified": False,
            "electronic_schema_artifact": schema_artifact,
            "official_generation": "BLOCKED",
        }

    return {
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "catalogue_status": CATALOGUE_STATUS,
        "forms": forms,
        "official_generation": "BLOCKED",
    }


def verify_catalogue_not_authoritative():
    """Prevent accidental use as the production mapping registry."""

    from app.services import payroll_regulatory_mapping_registry
    from app.services import payroll_d5_classifier_guard

    if payroll_regulatory_mapping_registry.APPROVED_MAPPINGS:
        raise OfficialFieldCatalogueError(
            "Production mapping approvals require separate audit"
        )

    # Catalogue never treats candidate field keys as official codes.
    for row in validate_catalogue():
        if row.status != "CANDIDATE":
            raise OfficialFieldCatalogueError(
                "Unauthorized candidate promotion"
            )

    return True
