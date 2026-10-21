"""Dated, evidence-backed severance tax-base classification.

Pure policy logic only. No database writes, rates or financial authorization.
The financial guard must remain fail-closed until the complete integration
and its positive acceptance tests are implemented.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


class SeveranceTaxClassificationError(ValueError):
    """A dated tax classification is missing, invalid or ambiguous."""


@dataclass(frozen=True)
class SeveranceTaxPolicy:
    code: str
    valid_from: date
    valid_to: date | None
    source_reference: str
    personal_income_tax_included: bool
    military_levy_included: bool
    unified_social_contribution_included: bool
    income_code_4df: str


@dataclass(frozen=True)
class SeveranceTaxBaseSnapshot:
    policy_code: str
    policy_valid_from: date
    policy_valid_to: date | None
    source_reference: str
    income_code_4df: str
    personal_income_tax_base: Decimal
    military_levy_base: Decimal
    unified_social_contribution_base: Decimal
    ordinary_gross: Decimal
    severance_gross: Decimal


def _validate(policy: SeveranceTaxPolicy) -> None:
    if not isinstance(policy, SeveranceTaxPolicy):
        raise SeveranceTaxClassificationError(
            "Invalid severance tax policy"
        )
    if not isinstance(policy.code, str) or not policy.code.strip():
        raise SeveranceTaxClassificationError(
            "Policy code is required"
        )
    if not isinstance(policy.source_reference, str) or not policy.source_reference.strip():
        raise SeveranceTaxClassificationError(
            "Verifiable legal source reference is required"
        )
    if not isinstance(policy.valid_from, date):
        raise SeveranceTaxClassificationError(
            "Effective start date is required"
        )
    if policy.valid_to is not None and (
        not isinstance(policy.valid_to, date)
        or policy.valid_to < policy.valid_from
    ):
        raise SeveranceTaxClassificationError(
            "Invalid policy validity window"
        )
    if policy.income_code_4df != "127":
        raise SeveranceTaxClassificationError(
            "Ordinary termination severance requires verified 4DF code 127"
        )
    for field in (
        "personal_income_tax_included",
        "military_levy_included",
        "unified_social_contribution_included",
    ):
        if type(getattr(policy, field)) is not bool:
            raise SeveranceTaxClassificationError(
                f"Explicit boolean classification required: {field}"
            )


def select_severance_tax_policy(
    *,
    income_date: date,
    policies,
) -> SeveranceTaxPolicy:
    """Require exactly one applicable dated policy, never a guessed fallback."""

    if not isinstance(income_date, date):
        raise SeveranceTaxClassificationError(
            "Income date is required"
        )

    matching = []
    for policy in policies:
        _validate(policy)
        if policy.valid_from <= income_date and (
            policy.valid_to is None
            or income_date <= policy.valid_to
        ):
            matching.append(policy)

    if len(matching) != 1:
        raise SeveranceTaxClassificationError(
            "Exactly one dated severance tax policy is required"
        )
    return matching[0]


def classify_severance_tax_bases(
    *,
    split,
    income_date: date,
    policies,
) -> SeveranceTaxBaseSnapshot:
    """Return gross bases only; tax amounts/rates remain elsewhere."""

    policy = select_severance_tax_policy(
        income_date=income_date,
        policies=policies,
    )

    ordinary = split.ordinary_gross
    severance = split.severance_gross

    if not isinstance(ordinary, Decimal) or not isinstance(
        severance, Decimal
    ):
        raise SeveranceTaxClassificationError(
            "Income split must use Decimal"
        )
    if ordinary < 0 or severance < 0:
        raise SeveranceTaxClassificationError(
            "Negative income category requires a separate correction policy"
        )
    if ordinary + severance != split.total_gross:
        raise SeveranceTaxClassificationError(
            "Income classification total mismatch"
        )

    # This function does not decide ordinary-income benefits,
    # minimum contribution floors, maximum ceilings or tax rates.
    return SeveranceTaxBaseSnapshot(
        policy_code=policy.code,
        policy_valid_from=policy.valid_from,
        policy_valid_to=policy.valid_to,
        source_reference=policy.source_reference,
        income_code_4df=policy.income_code_4df,
        personal_income_tax_base=(
            ordinary + severance
            if policy.personal_income_tax_included else ordinary
        ),
        military_levy_base=(
            ordinary + severance
            if policy.military_levy_included else ordinary
        ),
        unified_social_contribution_base=(
            ordinary + severance
            if policy.unified_social_contribution_included else ordinary
        ),
        ordinary_gross=ordinary,
        severance_gross=severance,
    )
