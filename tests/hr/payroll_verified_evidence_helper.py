from datetime import date

from app.services.payroll_tax_evidence_service import (
    create_tax_evidence,
    verify_tax_evidence,
)


async def verified_evidence(
    db,
    *,
    company_id,
    employee_id,
    created_by,
    entitlement_type,
    entitlement_code,
    valid_from=date(2026, 1, 1),
    valid_to=None,
):
    from uuid import uuid4
    from app.models.user import User

    reviewer = User(
        email=f"payroll-evidence-reviewer-{uuid4().hex}@example.test",
        password_hash="test-only-not-a-real-password",
        first_name="Evidence",
        last_name="Reviewer",
        is_active=True,
    )
    db.add(reviewer)
    await db.flush()

    reviewer_id = reviewer.id

    assert reviewer_id != created_by

    evidence = await create_tax_evidence(
        db,
        company_id=company_id,
        employee_id=employee_id,
        document_type="test_certificate",
        document_number=f"TEST-{entitlement_type}-{employee_id}-{entitlement_code}",
        issued_on=valid_from,
        valid_from=valid_from,
        valid_to=valid_to,
        entitlement_type=entitlement_type,
        entitlement_code=entitlement_code,
        created_by=created_by,
    )

    await verify_tax_evidence(
        db,
        company_id=company_id,
        evidence_id=evidence.id,
        verified_by=reviewer_id,
        approved=True,
    )

    assert evidence.verification_status == "verified"

    return evidence
