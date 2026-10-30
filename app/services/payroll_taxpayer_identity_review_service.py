"""Independent identity review service for internal regulatory evidence."""
import re
from datetime import date, datetime, timezone

from sqlalchemy import select

from app.models.employee import Employee
from app.models.payroll_taxpayer_identity_review import (
    PayrollTaxpayerIdentityReview,
)


class TaxpayerIdentityReviewError(ValueError):
    pass


async def create_identity_review(
    db,
    *,
    company_id: int,
    employee_id: int,
    tax_number: str,
    evidence_reference: str,
    valid_from: date,
    valid_to: date | None,
    created_by: int,
):
    if (
        type(tax_number) is not str
        or re.fullmatch(r"[0-9]{10}", tax_number) is None
    ):
        raise TaxpayerIdentityReviewError(
            "Reviewed RNOКPP must contain exactly ten digits"
        )

    if (
        not isinstance(evidence_reference, str)
        or not evidence_reference.strip()
        or valid_to is not None and valid_to < valid_from
    ):
        raise TaxpayerIdentityReviewError(
            "Invalid identity documentary evidence"
        )

    employee = await db.scalar(
        select(Employee).where(
            Employee.company_id == company_id,
            Employee.id == employee_id,
        )
    )

    if employee is None:
        raise TaxpayerIdentityReviewError(
            "Employee not found in company"
        )

    if employee.tax_number != tax_number:
        raise TaxpayerIdentityReviewError(
            "Reviewed identity differs from employee source"
        )

    row = PayrollTaxpayerIdentityReview(
        company_id=company_id,
        employee_id=employee_id,
        identity_kind="rnokpp",
        tax_number=tax_number,
        evidence_reference=evidence_reference.strip(),
        valid_from=valid_from,
        valid_to=valid_to,
        review_status="pending",
        created_by=created_by,
    )

    db.add(row)
    await db.flush()
    return row


async def decide_identity_review(
    db,
    *,
    company_id: int,
    review_id: int,
    reviewed_by: int,
    approved: bool,
):
    row = await db.scalar(
        select(PayrollTaxpayerIdentityReview)
        .where(
            PayrollTaxpayerIdentityReview.company_id == company_id,
            PayrollTaxpayerIdentityReview.id == review_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    if row is None:
        raise TaxpayerIdentityReviewError(
            "Identity review not found"
        )

    if row.review_status != "pending":
        raise TaxpayerIdentityReviewError(
            "Identity review already decided"
        )

    if row.created_by == reviewed_by:
        raise TaxpayerIdentityReviewError(
            "Independent reviewer required"
        )

    employee = await db.scalar(
        select(Employee).where(
            Employee.company_id == company_id,
            Employee.id == row.employee_id,
        )
    )

    if employee is None or employee.tax_number != row.tax_number:
        raise TaxpayerIdentityReviewError(
            "Employee identity changed before review"
        )

    row.review_status = "verified" if approved else "rejected"
    row.reviewed_by = reviewed_by
    row.reviewed_at = datetime.now(timezone.utc)

    await db.flush()
    return row


async def require_reviewed_identity(
    db,
    *,
    company_id: int,
    employee_id: int,
    review_id: int,
    effective_on: date,
    expected_tax_number: str,
):
    row = await db.scalar(
        select(PayrollTaxpayerIdentityReview).where(
            PayrollTaxpayerIdentityReview.company_id == company_id,
            PayrollTaxpayerIdentityReview.employee_id == employee_id,
            PayrollTaxpayerIdentityReview.id == review_id,
        )
    )

    if row is None or row.review_status != "verified":
        raise TaxpayerIdentityReviewError(
            "Verified employee identity evidence required"
        )

    if (
        row.identity_kind != "rnokpp"
        or row.tax_number != expected_tax_number
        or effective_on < row.valid_from
        or row.valid_to is not None
            and effective_on > row.valid_to
    ):
        raise TaxpayerIdentityReviewError(
            "Identity evidence not valid for reporting source"
        )

    return row
