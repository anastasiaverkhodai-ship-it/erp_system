"""One transaction, four forms, three verified evidence bindings.

The caller owns the transaction. This entrypoint creates exactly
one internal snapshot after all existing independent source gates
have passed.

It does not authorize official statutory generation.
"""

from sqlalchemy import text

from app.services.payroll_regulatory_pit_levy_evidence_writer import (
    bind_independent_pit_levy_evidence,
    verify_anchor as verify_pit_levy_anchor,
)

from app.services.payroll_regulatory_d1_evidence_bound_writer import (
    bind_d1_aggregate_evidence,
    deserialize_anchor as verify_d1_anchor,
)

from app.services.payroll_regulatory_4df_payment_bound_writer import (
    bind_4df_posted_payment_evidence,
    verify_anchor as verify_4df_anchor,
)

from app.services.payroll_regulatory_d5_d6_evidence_bound_writer import (
    bind_d5_d6_hr_evidence,
    verify_anchor as verify_hr_anchor,
)

from app.services.payroll_regulatory_verified_gross_writer import (
    persist_gross_verified_internal_four_form_review,
)

from app.services.payroll_regulatory_internal_reconciliation import (
    InternalSnapshot,
    verify_internal_snapshot,
)


class CombinedEvidenceError(ValueError):
    pass


async def verify_combined_persisted_snapshot(
    db,
    *,
    company_id,
    payroll_period_id,
    payroll_period,
    snapshot_id,
    d1_anchor,
    tax_anchors,
    payment_anchors,
    hr_anchor,
):
    stored = (
        await db.execute(
            text("""
                SELECT version, payload_json, snapshot_sha256
                FROM payroll_regulatory_internal_snapshots
                WHERE company_id = :cid
                  AND payroll_period_id = :pid
                  AND id = :sid
            """),
            {
                "cid": company_id,
                "pid": payroll_period_id,
                "sid": snapshot_id,
            },
        )
    ).mappings().one()

    content = verify_internal_snapshot(
        InternalSnapshot(
            company_id=company_id,
            payroll_period=payroll_period,
            version=stored["version"],
            canonical_json=stored["payload_json"],
            sha256=stored["snapshot_sha256"].strip(),
        )
    )

    rows_by_form = {
        form: []
        for form in ("D1", "4DF", "D5", "D6")
    }

    for row in content["rows"]:
        form = row["form"]

        if form not in rows_by_form:
            raise CombinedEvidenceError(
                "Unexpected internal form"
            )

        rows_by_form[form].append(row)

    if any(not rows for rows in rows_by_form.values()):
        raise CombinedEvidenceError(
            "Incomplete four-form snapshot"
        )

    verified_tax_calculations = set()

    for row in rows_by_form["D1"]:
        actual = row["attributes"].get(
            "d1_aggregate_evidence"
        )

        verified = verify_d1_anchor(actual)

        if verified != d1_anchor:
            raise CombinedEvidenceError(
                "Persisted D1 evidence mismatch"
            )

        calculation_id = row["source_id"]

        if (
            calculation_id not in tax_anchors
            or calculation_id in verified_tax_calculations
        ):
            raise CombinedEvidenceError(
                "D1 tax calculation coverage mismatch"
            )

        verified_tax_calculations.add(calculation_id)

        tax_value = row["attributes"].get(
            "pit_levy_statutory_evidence"
        )

        checked = verify_pit_levy_anchor(tax_value)

        if (
            tax_value != tax_anchors[calculation_id]
            or checked["calculation"] != calculation_id
            or checked["company"] != company_id
            or checked["period"] != payroll_period_id
        ):
            raise CombinedEvidenceError(
                "Persisted PIT/levy source mismatch"
            )

    if verified_tax_calculations != set(tax_anchors):
        raise CombinedEvidenceError(
            "Incomplete PIT/levy evidence"
        )

    observed_calculations = set()

    for row in rows_by_form["4DF"]:
        calculation_id = row["source_id"]

        if (
            calculation_id not in payment_anchors
            or calculation_id in observed_calculations
        ):
            raise CombinedEvidenceError(
                "4DF calculation source mismatch"
            )

        observed_calculations.add(calculation_id)

        actual = row["attributes"].get(
            "fourdf_posted_payment_evidence"
        )

        verify_4df_anchor(actual)

        if actual != payment_anchors[calculation_id]:
            raise CombinedEvidenceError(
                "Persisted 4DF payment evidence mismatch"
            )

    if observed_calculations != set(payment_anchors):
        raise CombinedEvidenceError(
            "Incomplete 4DF payment coverage"
        )

    for form in ("D5", "D6"):
        for row in rows_by_form[form]:
            actual = row["attributes"].get(
                "hr_inventory_evidence"
            )

            verify_hr_anchor(actual)

            if actual != hr_anchor:
                raise CombinedEvidenceError(
                    f"Persisted {form} HR evidence mismatch"
                )

    return content


async def persist_combined_four_form_review(
    db,
    *,
    company_id,
    payroll_period_id,
    payroll_period,
    report_references,
    reviewed_allocations,
    request_key,
):
    """Bind all verified sources before one immutable snapshot."""

    if (
        not isinstance(report_references, dict)
        or set(report_references)
        != {"D1", "4DF", "D5", "D6"}
    ):
        raise CombinedEvidenceError(
            "Exactly four report references required"
        )

    # Acquire the shared period lock at the beginning.
    period = (
        await db.execute(
            text("""
                SELECT id, year, month
                FROM payroll_periods
                WHERE company_id = :cid
                  AND id = :pid
                FOR UPDATE
            """),
            {
                "cid": company_id,
                "pid": payroll_period_id,
            },
        )
    ).mappings().one_or_none()

    if period is None:
        raise CombinedEvidenceError(
            "Payroll period not found"
        )

    actual_period = (
        f"{period['year']:04d}-{period['month']:02d}"
    )

    if actual_period != payroll_period:
        raise CombinedEvidenceError(
            "Payroll period label mismatch"
        )

    d1_anchor = await bind_d1_aggregate_evidence(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        payroll_period=payroll_period,
        report_references=report_references,
    )

    tax_anchors = await bind_independent_pit_levy_evidence(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        payroll_period=payroll_period,
        report_references=report_references,
    )

    payment_anchors = await bind_4df_posted_payment_evidence(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        payroll_period=payroll_period,
        report_references=report_references,
        reviewed_allocations=reviewed_allocations,
    )

    hr_anchor = await bind_d5_d6_hr_evidence(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        payroll_period=payroll_period,
        report_references=report_references,
    )

    # ONE writer invocation, after all three evidence gates.
    result = await persist_gross_verified_internal_four_form_review(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        payroll_period=payroll_period,
        report_references=report_references,
        request_key=request_key,
    )

    await verify_combined_persisted_snapshot(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        payroll_period=payroll_period,
        snapshot_id=result["id"],
        d1_anchor=d1_anchor,
        tax_anchors=tax_anchors,
        payment_anchors=payment_anchors,
        hr_anchor=hr_anchor,
    )

    if result.get("official_generation") != "BLOCKED":
        raise CombinedEvidenceError(
            "Official generation must remain blocked"
        )

    return {
        **result,
        "all_four_forms_in_one_snapshot": True,
        "d1_aggregate_evidence_verified": True,
        "pit_levy_statutory_components_verified": True,
        "pit_levy_statutory_evidence_in_snapshot": True,
        "fourdf_posted_payment_evidence_verified": True,
        "d5_d6_hr_inventory_verified": True,
        "separate_pit_statutory_mapping_verified": False,
        "separate_military_levy_mapping_verified": False,
        "d5_statutory_mapping_verified": False,
        "d6_applicability_verified": False,
        "official_generation": "BLOCKED",
    }
