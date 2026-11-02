"""Ukrainian payroll reporting version-selection review.

Grounded reference:
Order of the Ministry of Finance No. 4, as amended by No. 243.
Effective: 17 July 2026.
First legal-entity reporting month under the amended form: July 2026.

This service does not authorize filing or map official form fields.
Historical corrections are deliberately kept in manual review.
"""

from dataclasses import dataclass
from datetime import date
from enum import Enum


ORDER_4 = "https://zakon.rada.gov.ua/laws/show/z0111-15"
FIRST_NEW_REPORT_MONTH = date(2026, 7, 1)
AMENDMENT_EFFECTIVE_DATE = date(2026, 7, 17)

FORM_KINDS = frozenset({"d1", "4df", "d5", "d6"})


class FormVersionError(ValueError):
    pass


class VersionDecision(str, Enum):
    NEW_2026_CANDIDATE = "NEW_2026_CANDIDATE"
    HISTORICAL_REVIEW = "HISTORICAL_REVIEW"
    CORRECTION_REVIEW = "CORRECTION_REVIEW"


@dataclass(frozen=True)
class FormVersionAssessment:
    report_kind: str
    report_month: date
    submission_date: date
    submission_type: str
    legal_entity: bool
    decision: VersionDecision
    source_url: str
    official_generation: str = "BLOCKED"


def assess_form_version(
    *,
    report_kind,
    report_month,
    submission_date,
    submission_type="initial",
    legal_entity=True,
):
    if report_kind not in FORM_KINDS:
        raise FormVersionError("Unsupported reporting form")

    if type(report_month) is not date or report_month.day != 1:
        raise FormVersionError("First day of reporting month required")

    if type(submission_date) is not date:
        raise FormVersionError("Submission date required")

    if submission_date < report_month:
        raise FormVersionError("Submission cannot precede report month")

    if submission_type not in {"initial", "new", "correction"}:
        raise FormVersionError("Invalid submission type")

    if type(legal_entity) is not bool:
        raise FormVersionError("Legal-entity flag must be boolean")

    if not legal_entity:
        raise FormVersionError(
            "This version policy applies only to legal entities"
        )

    if submission_type != "initial":
        decision = VersionDecision.CORRECTION_REVIEW
    elif report_month < FIRST_NEW_REPORT_MONTH:
        decision = VersionDecision.HISTORICAL_REVIEW
    elif submission_date < AMENDMENT_EFFECTIVE_DATE:
        decision = VersionDecision.HISTORICAL_REVIEW
    else:
        decision = VersionDecision.NEW_2026_CANDIDATE

    return FormVersionAssessment(
        report_kind=report_kind,
        report_month=report_month,
        submission_date=submission_date,
        submission_type=submission_type,
        legal_entity=legal_entity,
        decision=decision,
        source_url=ORDER_4,
    )


def assess_package(
    *,
    report_month,
    submission_date,
    submission_type="initial",
    legal_entity=True,
):
    forms = {
        kind: assess_form_version(
            report_kind=kind,
            report_month=report_month,
            submission_date=submission_date,
            submission_type=submission_type,
            legal_entity=legal_entity,
        )
        for kind in sorted(FORM_KINDS)
    }

    return {
        "forms": forms,
        "d5_applicability": "REQUIRES_EVENT_EVIDENCE",
        "d6_applicability": "REQUIRES_SPECIAL_SERVICE_EVIDENCE",
        "actual_form_artifact_verified": False,
        "mapping_approved": False,
        "official_generation": "BLOCKED",
    }
