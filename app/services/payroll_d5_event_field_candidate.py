"""Review-only D5 event-to-field candidate projection.

Field semantics are based on an extracted, not independently
approved normative text. Inputs must come from verified ERP
evidence before any operational use.

This module does not write statutory rows or authorize filing.
"""

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
import re


class D5FieldCandidateError(ValueError):
    pass


KINDS = frozenset({
    "hire",
    "transfer",
    "termination",
})

CATEGORIES = frozenset({1, 2})
SHA = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class D5EventFacts:
    company_id: int
    employee_id: int
    contract_id: int
    employment_event_id: int

    event_kind: str
    effective_date: date

    person_category: int
    confirmed_identity: str
    full_name: str

    profession_code: str
    profession_name: str
    position_title: str

    order_reference: str
    source_sha256: str

    termination_basis: str | None = None
    new_workplace_date: date | None = None


@dataclass(frozen=True)
class D5CategoryChangeFacts:
    previous: D5EventFacts
    current: D5EventFacts
    previous_end_date: date
    current_start_date: date


def _required(value, name):
    if not isinstance(value, str) or not value.strip():
        raise D5FieldCandidateError(
            f"Missing {name}"
        )

    return value.strip()


def _valid_date(value, name):
    if type(value) is not date:
        raise D5FieldCandidateError(
            f"Invalid {name}"
        )
    return value


def _validate(facts):
    if type(facts) is not D5EventFacts:
        raise D5FieldCandidateError(
            "Typed event facts required"
        )

    for name in (
        "company_id",
        "employee_id",
        "contract_id",
        "employment_event_id",
    ):
        value = getattr(facts, name)

        if type(value) is not int or value <= 0:
            raise D5FieldCandidateError(
                f"Invalid {name}"
            )

    if facts.event_kind not in KINDS:
        raise D5FieldCandidateError(
            "Unsupported event kind"
        )

    _valid_date(
        facts.effective_date,
        "effective_date",
    )

    if facts.person_category not in CATEGORIES:
        raise D5FieldCandidateError(
            "Unsupported category for this projection"
        )

    for name in (
        "confirmed_identity",
        "full_name",
        "profession_code",
        "profession_name",
        "position_title",
        "order_reference",
    ):
        _required(getattr(facts, name), name)

    if (
        not isinstance(facts.source_sha256, str)
        or SHA.fullmatch(facts.source_sha256) is None
    ):
        raise D5FieldCandidateError(
            "Valid source SHA256 required"
        )

    if facts.event_kind == "termination":
        _required(
            facts.termination_basis,
            "termination_basis",
        )
    elif facts.termination_basis is not None:
        raise D5FieldCandidateError(
            "Termination basis on non-termination event"
        )

    if facts.new_workplace_date is not None:
        _valid_date(
            facts.new_workplace_date,
            "new_workplace_date",
        )

        if facts.new_workplace_date > facts.effective_date:
            raise D5FieldCandidateError(
                "New workplace created after employment event"
            )


def _candidate(facts, start, end, transfer_flag):
    _validate(facts)

    if start is not None:
        _valid_date(start, "start")

    if end is not None:
        _valid_date(end, "end")

    if start is not None and end is not None and start > end:
        raise D5FieldCandidateError(
            "Start after end"
        )

    if type(transfer_flag) is not bool:
        raise D5FieldCandidateError(
            "Explicit transfer flag required"
        )

    # These keys are candidate field values, not a filed D5 row.
    fields = {
        "07": facts.person_category,
        "08": facts.confirmed_identity.strip(),
        "09": facts.full_name.strip(),
        "10": {
            "start": (
                start.strftime("%d%m%Y")
                if start else None
            ),
            "end": (
                end.strftime("%d%m%Y")
                if end else None
            ),
        },
        "12": 1 if transfer_flag else 0,
        "13": facts.profession_code.strip(),
        "14": facts.profession_name.strip(),
        "15": facts.position_title.strip(),
        "16": facts.order_reference.strip(),
        "17": (
            facts.termination_basis.strip()
            if facts.termination_basis else None
        ),
        "18": (
            facts.new_workplace_date.strftime("%d%m%Y")
            if facts.new_workplace_date else None
        ),
    }

    identity = {
        "company_id": facts.company_id,
        "employee_id": facts.employee_id,
        "contract_id": facts.contract_id,
        "employment_event_id": facts.employment_event_id,
        "event_kind": facts.event_kind,
        "effective_date": facts.effective_date.isoformat(),
        "source_sha256": facts.source_sha256,
    }

    canonical = json.dumps(
        {
            "identity": identity,
            "fields": fields,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    return {
        "identity": identity,
        "fields": fields,
        "candidate_sha256": sha256(
            canonical.encode("utf-8")
        ).hexdigest(),
        "source_binding": "CANDIDATE_REQUIRES_DB_VERIFICATION",
        "normative_mapping": "NOT_APPROVED",
        "official_generation": "BLOCKED",
        "official_row": False,
    }


def project_d5_event_candidate(facts):
    """Candidate projection for one ordinary HR event."""

    _validate(facts)

    if facts.event_kind == "hire":
        start, end, transfer = (
            facts.effective_date, None, False
        )
    elif facts.event_kind == "termination":
        start, end, transfer = (
            None, facts.effective_date, False
        )
    else:
        start, end, transfer = (
            None, None, True
        )

    return _candidate(
        facts,
        start=start,
        end=end,
        transfer_flag=transfer,
    )


def project_d5_category_change_candidate(change):
    """Two candidates for primary/secondary employment change.

    Requires explicitly supplied dates; does not infer dates
    from payroll or assume approval of the underlying event.
    """

    if type(change) is not D5CategoryChangeFacts:
        raise D5FieldCandidateError(
            "Typed category change required"
        )

    before = change.previous
    after = change.current

    _validate(before)
    _validate(after)

    if (
        before.company_id != after.company_id
        or before.employee_id != after.employee_id
        or before.contract_id != after.contract_id
    ):
        raise D5FieldCandidateError(
            "Employment identity mismatch"
        )

    if before.person_category == after.person_category:
        raise D5FieldCandidateError(
            "Category did not change"
        )

    if (
        before.event_kind != "transfer"
        or after.event_kind != "transfer"
        or before.employment_event_id
           != after.employment_event_id
    ):
        raise D5FieldCandidateError(
            "Same verified transfer event required"
        )

    if (
        before.effective_date != after.effective_date
    ):
        raise D5FieldCandidateError(
            "Transfer effective dates differ"
        )

    end_date = _valid_date(
        change.previous_end_date,
        "previous_end_date",
    )
    start_date = _valid_date(
        change.current_start_date,
        "current_start_date",
    )

    if start_date < end_date:
        raise D5FieldCandidateError(
            "New category starts before prior category ends"
        )

    if start_date != after.effective_date:
        raise D5FieldCandidateError(
            "New category date differs from transfer event"
        )

    first = _candidate(
        before,
        start=None,
        end=end_date,
        transfer_flag=True,
    )

    second = _candidate(
        after,
        start=start_date,
        end=None,
        transfer_flag=True,
    )

    return {
        "candidate_count": 2,
        "candidate_rows": [first, second],
        "event_kind": "primary_secondary_change",
        "normative_mapping": "NOT_APPROVED",
        "official_generation": "BLOCKED",
        "official_rows_generated": 0,
    }
