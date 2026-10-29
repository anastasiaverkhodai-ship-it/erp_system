"""Verify the stored automatic-advance tax source without posting taxes.

This module proves source arithmetic only. It does not establish that
taxes were remitted or cleared against a later monthly calculation.
"""
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


class AdvanceTaxSourceProofError(ValueError):
    pass


CENT = Decimal("0.01")


def _require(ok, message):
    if not ok:
        raise AdvanceTaxSourceProofError(message)


def _decimal(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AdvanceTaxSourceProofError(
            "Invalid tax source decimal"
        ) from exc
    _require(amount.is_finite(), "Non-finite tax source")
    return amount


def _money(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class VerifiedAdvanceTaxComponent:
    component: str
    source_rate_id: int
    source_tax_evidence_id: int | None
    source_reference: str | None
    base_amount: Decimal
    rate: Decimal
    amount: Decimal


@dataclass(frozen=True)
class VerifiedAdvanceTaxSource:
    advance_id: int
    company_id: int
    payroll_period_id: int
    employment_contract_id: int
    payment_date: str
    gross_amount: Decimal
    withholding_amount: Decimal
    net_amount: Decimal
    components: tuple[VerifiedAdvanceTaxComponent, ...]
    tax_remittance_confirmed: bool = False
    monthly_tax_clearing_confirmed: bool = False


def verify_automatic_advance_tax_source(advance):
    """Fail closed on corrupt or inconsistent automatic-advance evidence."""
    raw = getattr(advance, "calculation_snapshot_json", None)
    _require(isinstance(raw, str) and bool(raw),
             "Automatic advance snapshot required")
    try:
        snapshot = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise AdvanceTaxSourceProofError(
            "Invalid automatic advance snapshot"
        ) from exc

    _require(isinstance(snapshot, dict)
             and snapshot.get("version") == 1,
             "Unsupported advance snapshot version")

    for name in (
        "id", "company_id", "payroll_period_id",
        "employment_contract_id", "bank_account_id",
    ):
        value = getattr(advance, name, None)
        _require(type(value) is int and value > 0,
                 f"Valid advance {name} required")

    request = snapshot.get("request")
    _require(isinstance(request, dict),
             "Documented advance request required")
    expected = {
        "company_id": advance.company_id,
        "payroll_period_id": advance.payroll_period_id,
        "employment_contract_id": advance.employment_contract_id,
        "bank_account_id": advance.bank_account_id,
        "advance_percentage": format(
            _decimal(advance.advance_percentage), ".4f"
        ),
        "payment_date": advance.payment_date.isoformat(),
    }
    _require(request == expected, "Advance request snapshot mismatch")
    _require(advance.currency_code == "UAH",
             "Verified advance must be UAH")

    gross = _decimal(snapshot.get("gross_amount"))
    withheld = _decimal(snapshot.get("withholding_amount"))
    net = _decimal(snapshot.get("net_amount"))
    minimum_net = _decimal(snapshot.get("minimum_net_amount"))
    paid = _decimal(advance.paid_amount)
    calculated = _decimal(advance.calculated_amount)

    _require(
        gross > 0
        and gross == calculated
        and withheld >= 0
        and net > 0
        and _money(gross - withheld) == net
        and paid == net
        and minimum_net >= 0
        and net >= minimum_net,
        "Advance gross, withholding and payment do not reconcile",
    )

    # PayrollAdvance.withheld_amount belongs to the separate
    # enforcement/deduction lifecycle. Never reinterpret it as PIT/VZ.
    rows = snapshot.get("tax_lines")
    _require(isinstance(rows, list) and bool(rows),
             "Statutory advance components required")

    components = []
    observed = set()
    for row in rows:
        _require(isinstance(row, dict), "Invalid tax component")
        name = row.get("component")
        _require(isinstance(name, str) and bool(name.strip())
                 and name not in observed,
                 "Duplicate or invalid tax component")
        observed.add(name)
        source_id = row.get("source_rate_id")
        evidence_id = row.get("source_tax_evidence_id")
        reference = row.get("source_reference")
        _require(type(source_id) is int and source_id > 0,
                 "Tax component has no historical rate")
        _require(
            evidence_id is None
            or (type(evidence_id) is int and evidence_id > 0),
            "Invalid tax evidence identifier",
        )
        _require(
            reference is None
            or (isinstance(reference, str) and bool(reference.strip())),
            "Invalid tax reference",
        )

        base = _decimal(row.get("base_amount"))
        rate = _decimal(row.get("rate"))
        amount = _decimal(row.get("amount"))
        _require(base >= 0 and rate >= 0 and amount >= 0,
                 "Negative tax component not allowed")
        _require(_money(base * rate) == amount,
                 "Saved component does not match the rate and base")

        components.append(
            VerifiedAdvanceTaxComponent(
                component=name,
                source_rate_id=source_id,
                source_tax_evidence_id=evidence_id,
                source_reference=(
                    reference.strip() if reference is not None else None
                ),
                base_amount=base,
                rate=rate,
                amount=amount,
            )
        )

    # The source service excludes USC from employee withholding.
    usc = "unified_social_contribution"
    employee_withheld = sum(
        (item.amount for item in components
         if item.component != usc),
        Decimal("0"),
    )
    _require(
        employee_withheld == withheld,
        "Advance employee tax components do not reconcile",
    )

    return VerifiedAdvanceTaxSource(
        advance_id=advance.id,
        company_id=advance.company_id,
        payroll_period_id=advance.payroll_period_id,
        employment_contract_id=advance.employment_contract_id,
        payment_date=advance.payment_date.isoformat(),
        gross_amount=gross,
        withholding_amount=withheld,
        net_amount=net,
        components=tuple(components),
    )


async def verify_automatic_advance_tax_source_in_db(db, advance):
    """Verify snapshot arithmetic AND each historical rate in PostgreSQL.

    A missing reference is permitted for a company-wide statutory rate,
    never for an individual employee's statutory rate.
    """
    from sqlalchemy import select
    from app.models.payroll_statutory import PayrollStatutoryRate

    proof = verify_automatic_advance_tax_source(advance)
    snapshot = json.loads(advance.calculation_snapshot_json)

    employee_id = snapshot.get("employee_id")
    _require(
        type(employee_id) is int and employee_id > 0,
        "Advance snapshot requires source employee",
    )

    from app.models.employment_contract import EmploymentContract
    contract = await db.scalar(select(EmploymentContract).where(
        EmploymentContract.company_id == proof.company_id,
        EmploymentContract.id == proof.employment_contract_id,
    ))
    _require(contract is not None and contract.employee_id == employee_id,
             "Advance snapshot employee differs from employment contract")

    rows = snapshot["tax_lines"]

    for component, recorded in zip(proof.components, rows):
        rate = await db.scalar(
            select(PayrollStatutoryRate).where(
                PayrollStatutoryRate.id
                == component.source_rate_id,
                PayrollStatutoryRate.company_id
                == proof.company_id,
            )
        )
        _require(
            rate is not None,
            "Historical statutory tax rate not found in company",
        )

        persisted_component = getattr(rate.component, "value", rate.component)

        _require(
            persisted_component == component.component,
            "Historical statutory component mismatch",
        )
        _require(
            rate.employee_id is None
            or rate.employee_id == employee_id,
            "Historical statutory rate belongs to another employee",
        )
        _require(
            rate.effective_from <= advance.payment_date
            and (
                rate.effective_to is None
                or advance.payment_date <= rate.effective_to
            ),
            "Historical statutory rate is not effective on payment date",
        )
        _require(
            str(rate.effective_from)
            == str(recorded.get("rate_effective_from"))
            and (
                str(rate.effective_to)
                if rate.effective_to is not None
                else None
            ) == recorded.get("rate_effective_to"),
            "Snapshot historical rate dates mismatch",
        )
        _require(
            _decimal(rate.rate) == component.rate,
            "Snapshot statutory tax rate changed",
        )
        _require(
            rate.tax_evidence_id
            == component.source_tax_evidence_id,
            "Snapshot statutory tax evidence mismatch",
        )

        persisted_reference = (
            rate.source_reference.strip()
            if isinstance(rate.source_reference, str)
            else None
        )
        _require(
            persisted_reference == component.source_reference,
            "Snapshot statutory documentary reference mismatch",
        )

        if rate.employee_id is not None:
            _require(
                bool(persisted_reference),
                "Individual statutory rate requires documentary reference",
            )

    return proof
