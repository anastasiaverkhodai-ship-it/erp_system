"""Conservative D5 candidate event reconciliation.

No normative approval, legal authenticity claim, database mutation,
or statutory report generation.

A missing amendment makes the affected transition uncertain.
The initial profession state is never inferred from a partial chain.
"""

from hashlib import sha256
import json

from app.services.payroll_d5_annex_chain_review import (
    review_d5_annex_chain,
)


class D5AnnexEventReconciliationError(ValueError):
    pass


def _digest(value):
    return sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def reconcile_d5_annex_events(*, source_root, manifest_paths):
    chain = review_d5_annex_chain(
        source_root=source_root,
        manifest_paths=manifest_paths,
    )

    documents = chain["documents"]
    events = chain["events"]

    doc_by_number = {
        document["number"]: document
        for document in documents
    }

    if len(doc_by_number) != len(documents):
        raise D5AnnexEventReconciliationError(
            "Duplicate amendment identity"
        )

    valid_kinds = {"introduced", "removed", "restored"}
    grouped = {}

    for event in events:
        number = event["amendment_number"]
        document = doc_by_number.get(number)

        if (
            document is None
            or event["source_reference"] != document["source_reference"]
            or event["source_sha256"] != document["source_sha256"]
            or event["effective_date"] != document["effective_date"]
            or event["change_kind"] not in valid_kinds
        ):
            raise D5AnnexEventReconciliationError(
                "Candidate event has inconsistent source binding"
            )

        grouped.setdefault(
            event["profession_code"], []
        ).append(event)

    all_numbers = set(doc_by_number)
    issues = []
    outcomes = []

    for code, code_events in sorted(grouped.items()):
        code_events.sort(
            key=lambda event: (
                event["amendment_number"],
                event["effective_date"],
            )
        )

        known_state = None
        prior_number = None
        uncertain = False

        for event in code_events:
            number = event["amendment_number"]
            action = event["change_kind"]

            if prior_number is not None:
                intervening = range(prior_number + 1, number)
                if any(n not in all_numbers for n in intervening):
                    known_state = None
                    uncertain = True

            previous_state = known_state
            contradiction = None

            if previous_state == "active":
                if action in ("introduced", "restored"):
                    contradiction = "ACTIVE_PROFESSION_REACTIVATED"
                elif action == "removed":
                    known_state = "inactive"

            elif previous_state == "inactive":
                if action == "removed":
                    contradiction = "INACTIVE_PROFESSION_REMOVED_AGAIN"
                elif action == "introduced":
                    # A later introduction may reflect a revised source
                    # classification. Flag for review, not a legal rejection.
                    uncertain = True
                    known_state = "active"
                elif action == "restored":
                    known_state = "active"

            else:
                # We cannot infer the state before the first observed
                # event, nor across an omitted amendment.
                if action == "introduced":
                    known_state = "active"
                elif action == "removed":
                    known_state = "inactive"
                    uncertain = True
                elif action == "restored":
                    known_state = "active"
                    uncertain = True

            if contradiction:
                issues.append({
                    "profession_code": code,
                    "amendment_number": number,
                    "issue": contradiction,
                })
                known_state = None
                uncertain = True

            prior_number = number

        outcomes.append({
            "profession_code": code,
            "last_observed_state": known_state or "unknown",
            "historical_completeness": "NOT_VERIFIED",
            "requires_review": uncertain,
            "observed_events": len(code_events),
        })

    report = {
        "schema": "D5_CANDIDATE_EVENT_RECONCILIATION_V1",
        "chain_sha256": chain["chain_sha256"],
        "candidate_event_count": len(events),
        "profession_count": len(grouped),
        "issues": issues,
        "profession_reviews": outcomes,
        "missing_amendment_numbers": list(
            chain["missing_amendment_numbers"]
        ),
    }

    blockers = list(chain["blockers"])

    if issues:
        blockers.append(
            "D5_CANDIDATE_EVENT_TRANSITION_CONFLICT"
        )

    if any(item["requires_review"] for item in outcomes):
        blockers.append(
            "D5_CANDIDATE_EVENT_HISTORY_UNCERTAIN"
        )

    return {
        **report,
        "reconciliation_sha256": _digest(report),
        "status": "REVIEW_REQUIRED",
        "full_classifier_history": "NOT_VERIFIED",
        "independent_authenticity": "NOT_VERIFIED",
        "normative_approval": "NOT_APPROVED",
        "blockers": tuple(dict.fromkeys(blockers)),
        "official_generation": "BLOCKED",
        "official_rows_generated": 0,
    }
