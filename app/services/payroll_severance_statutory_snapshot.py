"""Immutable severance statutory classification evidence.

This is a pure-data contract. It is NOT an approved legal rule
registry, a persistent database record, or financial authorization.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import hashlib
import json

from app.services.payroll_severance_income_classification import (
    PayrollIncomeSplit,
)
from app.services.payroll_severance_tax_classification import (
    SeveranceTaxBaseSnapshot,
    SeveranceTaxClassificationError,
)


VERSION = 1
ZERO = Decimal("0.00")


class SeveranceSnapshotError(ValueError):
    """Severance statutory evidence cannot be trusted."""


@dataclass(frozen=True)
class SeveranceStatutorySnapshot:
    version: int
    company_id: int
    payroll_calculation_id: int
    income_date: date
    currency_code: str
    ordinary_gross: Decimal
    severance_gross: Decimal
    total_gross: Decimal
    severance_source_ids: tuple[int, ...]
    policy_code: str
    policy_valid_from: date
    policy_valid_to: date | None
    source_reference: str
    income_code_4df: str
    personal_income_tax_base: Decimal
    military_levy_base: Decimal
    unified_social_contribution_base: Decimal
    fingerprint_sha256: str


def _positive_id(value, label):
    if type(value) is not int or value <= 0:
        raise SeveranceSnapshotError(
            f"{label} requires a positive integer"
        )
    return value


def _decimal(value, label):
    if not isinstance(value, Decimal):
        raise SeveranceSnapshotError(
            f"{label} requires Decimal"
        )
    if not value.is_finite():
        raise SeveranceSnapshotError(
            f"{label} must be finite"
        )
    if value.as_tuple().exponent < -2:
        raise SeveranceSnapshotError(
            f"{label} has fractional kopecks"
        )
    return value.quantize(Decimal("0.01"))


def _payload(snapshot):
    return {
        "version": snapshot.version,
        "company_id": snapshot.company_id,
        "payroll_calculation_id": snapshot.payroll_calculation_id,
        "income_date": snapshot.income_date.isoformat(),
        "currency_code": snapshot.currency_code,
        "ordinary_gross": str(snapshot.ordinary_gross),
        "severance_gross": str(snapshot.severance_gross),
        "total_gross": str(snapshot.total_gross),
        "severance_source_ids": list(
            snapshot.severance_source_ids
        ),
        "policy_code": snapshot.policy_code,
        "policy_valid_from": (
            snapshot.policy_valid_from.isoformat()
        ),
        "policy_valid_to": (
            snapshot.policy_valid_to.isoformat()
            if snapshot.policy_valid_to else None
        ),
        "source_reference": snapshot.source_reference,
        "income_code_4df": snapshot.income_code_4df,
        "personal_income_tax_base": str(
            snapshot.personal_income_tax_base
        ),
        "military_levy_base": str(
            snapshot.military_levy_base
        ),
        "unified_social_contribution_base": str(
            snapshot.unified_social_contribution_base
        ),
    }


def _fingerprint(payload):
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def build_severance_statutory_snapshot(
    *,
    company_id: int,
    payroll_calculation_id: int,
    income_date: date,
    income_split: PayrollIncomeSplit,
    tax_bases: SeveranceTaxBaseSnapshot,
    currency_code: str = "UAH",
) -> SeveranceStatutorySnapshot:
    """Build deterministic evidence for a documented severance source."""

    _positive_id(company_id, "company_id")
    _positive_id(
        payroll_calculation_id, "payroll_calculation_id"
    )

    if type(income_date) is not date:
        raise SeveranceSnapshotError(
            "Income date must be a calendar date"
        )

    if currency_code != "UAH":
        raise SeveranceSnapshotError(
            "Severance snapshot requires UAH"
        )

    if not isinstance(income_split, PayrollIncomeSplit):
        raise SeveranceSnapshotError(
            "Income split contract is required"
        )

    if not isinstance(tax_bases, SeveranceTaxBaseSnapshot):
        raise SeveranceSnapshotError(
            "Tax base classification contract is required"
        )

    ordinary = _decimal(
        income_split.ordinary_gross, "ordinary_gross"
    )
    severance = _decimal(
        income_split.severance_gross, "severance_gross"
    )
    total = _decimal(
        income_split.total_gross, "total_gross"
    )

    if ordinary < ZERO or severance <= ZERO:
        raise SeveranceSnapshotError(
            "Positive classified severance and nonnegative "
            "ordinary gross are required"
        )

    if ordinary + severance != total:
        raise SeveranceSnapshotError(
            "Classified gross mismatch"
        )

    ids = income_split.severance_source_ids

    if not isinstance(ids, tuple) or not ids:
        raise SeveranceSnapshotError(
            "Documented severance sources are required"
        )

    for item in ids:
        _positive_id(item, "severance source")

    if len(ids) != len(set(ids)):
        raise SeveranceSnapshotError(
            "Duplicate severance sources"
        )

    if (
        tax_bases.ordinary_gross != ordinary
        or tax_bases.severance_gross != severance
    ):
        raise SeveranceSnapshotError(
            "Income and tax classifications disagree"
        )

    bases = tuple(
        _decimal(value, label)
        for label, value in (
            (
                "personal_income_tax_base",
                tax_bases.personal_income_tax_base,
            ),
            (
                "military_levy_base",
                tax_bases.military_levy_base,
            ),
            (
                "unified_social_contribution_base",
                tax_bases.unified_social_contribution_base,
            ),
        )
    )

    if any(value < ZERO or value > total for value in bases):
        raise SeveranceSnapshotError(
            "Invalid classified component base"
        )

    if not tax_bases.policy_code.strip():
        raise SeveranceSnapshotError("Policy code required")

    if not tax_bases.source_reference.strip():
        raise SeveranceSnapshotError(
            "Policy source reference required"
        )

    if type(tax_bases.policy_valid_from) is not date:
        raise SeveranceSnapshotError(
            "Policy effective date required"
        )

    if (
        tax_bases.policy_valid_to is not None
        and type(tax_bases.policy_valid_to) is not date
    ):
        raise SeveranceSnapshotError(
            "Invalid policy end date"
        )

    if not (
        tax_bases.policy_valid_from <= income_date
        and (
            tax_bases.policy_valid_to is None
            or income_date <= tax_bases.policy_valid_to
        )
    ):
        raise SeveranceSnapshotError(
            "Policy not applicable on income date"
        )

    if tax_bases.income_code_4df != "127":
        raise SeveranceSnapshotError(
            "Unexpected severance income code"
        )

    fields = {
        "version": VERSION,
        "company_id": company_id,
        "payroll_calculation_id": payroll_calculation_id,
        "income_date": income_date,
        "currency_code": currency_code,
        "ordinary_gross": ordinary,
        "severance_gross": severance,
        "total_gross": total,
        "severance_source_ids": ids,
        "policy_code": tax_bases.policy_code,
        "policy_valid_from": tax_bases.policy_valid_from,
        "policy_valid_to": tax_bases.policy_valid_to,
        "source_reference": tax_bases.source_reference,
        "income_code_4df": tax_bases.income_code_4df,
        "personal_income_tax_base": bases[0],
        "military_levy_base": bases[1],
        "unified_social_contribution_base": bases[2],
    }

    unsigned = SeveranceStatutorySnapshot(
        **fields, fingerprint_sha256=""
    )
    return SeveranceStatutorySnapshot(
        **fields,
        fingerprint_sha256=_fingerprint(_payload(unsigned)),
    )


def verify_severance_statutory_snapshot(snapshot):
    """Verify integrity, not legal validity or DB provenance."""

    if not isinstance(snapshot, SeveranceStatutorySnapshot):
        raise SeveranceSnapshotError(
            "Invalid snapshot object"
        )

    expected = _fingerprint(_payload(snapshot))

    if expected != snapshot.fingerprint_sha256:
        raise SeveranceSnapshotError(
            "Statutory snapshot integrity mismatch"
        )

    return True
