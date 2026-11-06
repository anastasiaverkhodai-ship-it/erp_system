"""Candidate effective-dated profession history for D5.

No statutory approval, classifier import, database persistence,
or authorization of official report generation occurs here.

A hash in an event is an evidence reference, not proof that the
document was authenticated or normatively approved.
"""

from dataclasses import dataclass
from datetime import date, timedelta
import re


DIGEST = re.compile(r"^[0-9a-f]{64}$")
CODE = re.compile(r"^[0-9]{1,4}(?:\.[0-9]{1,2})?$")

OPERATIONS = frozenset({
    "add",
    "rename",
    "cancel",
})


class D5HistoryError(ValueError):
    pass


@dataclass(frozen=True)
class ProfessionEvent:
    profession_code: str
    operation: str
    effective_date: date | None
    candidate_dates: tuple[date, ...]
    title: str | None
    source_id: str
    source_sha256: str
    source_reference: str


@dataclass(frozen=True)
class ProfessionPeriod:
    profession_code: str
    title: str
    valid_from: date
    valid_to: date | None
    source_id: str
    source_sha256: str


@dataclass(frozen=True)
class HistoryResult:
    periods: tuple[ProfessionPeriod, ...]
    event_count: int
    normative_approved: bool = False
    official_generation_authorized: bool = False


def validate_event(event: ProfessionEvent) -> None:
    if not isinstance(event, ProfessionEvent):
        raise D5HistoryError("ProfessionEvent required")

    if not isinstance(event.profession_code, str) or not (
        CODE.fullmatch(event.profession_code)
    ):
        raise D5HistoryError("Invalid profession code")

    if event.operation not in OPERATIONS:
        raise D5HistoryError("Invalid profession operation")

    for value in (event.source_id, event.source_reference):
        if not isinstance(value, str) or not value.strip():
            raise D5HistoryError("Source reference required")

    if not isinstance(event.source_sha256, str) or not (
        DIGEST.fullmatch(event.source_sha256)
    ):
        raise D5HistoryError("Pinned SHA256 required")

    if not isinstance(event.candidate_dates, tuple) or not (
        event.candidate_dates
    ):
        raise D5HistoryError("Candidate dates required")

    if any(
        not isinstance(value, date)
        for value in event.candidate_dates
    ):
        raise D5HistoryError("Invalid candidate date")

    if len(set(event.candidate_dates)) != len(
        event.candidate_dates
    ):
        raise D5HistoryError("Duplicate candidate dates")

    if event.effective_date is not None and not isinstance(
        event.effective_date, date
    ):
        raise D5HistoryError("Invalid effective date")

    if event.effective_date is not None and (
        event.effective_date not in event.candidate_dates
    ):
        raise D5HistoryError(
            "Effective date absent from evidence candidates"
        )

    if event.operation in {"add", "rename"}:
        if not isinstance(event.title, str) or not event.title.strip():
            raise D5HistoryError("Profession title required")
    elif event.title is not None:
        raise D5HistoryError(
            "Cancellation cannot introduce a new title"
        )


def unresolved_date_conflicts(events) -> tuple[dict, ...]:
    """Return unresolved date conflicts without resolving them."""
    result = []

    for event in events:
        validate_event(event)

        if (
            len(event.candidate_dates) > 1
            or event.effective_date is None
        ):
            result.append({
                "profession_code": event.profession_code,
                "operation": event.operation,
                "source_id": event.source_id,
                "candidate_dates": tuple(
                    value.isoformat()
                    for value in event.candidate_dates
                ),
                "chosen_date": (
                    event.effective_date.isoformat()
                    if event.effective_date else None
                ),
                "resolution": "BLOCKED",
            })

    return tuple(result)


def build_candidate_history(events) -> HistoryResult:
    """Build a technical candidate history, not a trusted registry.

    Requires a unique candidate date for each event. A disputed
    date remains blocked even if a caller supplies a chosen date.
    """
    events = tuple(events)

    for event in events:
        validate_event(event)

    conflicts = unresolved_date_conflicts(events)
    if conflicts:
        raise D5HistoryError(
            "Unresolved normative effective-date conflict"
        )

    grouped = {}
    for event in events:
        grouped.setdefault(event.profession_code, []).append(event)

    periods = []

    for code, items in sorted(grouped.items()):
        items.sort(key=lambda item: item.effective_date)

        dates = [item.effective_date for item in items]
        if len(dates) != len(set(dates)):
            raise D5HistoryError(
                "Multiple changes on one effective date"
            )

        if items[0].operation != "add":
            raise D5HistoryError(
                "History must begin with an addition"
            )

        active = False
        last_title = None

        for index, event in enumerate(items):
            if event.operation == "add":
                if active:
                    raise D5HistoryError(
                        "Profession already active"
                    )
                active = True
                last_title = event.title.strip()

            elif event.operation == "rename":
                if not active:
                    raise D5HistoryError(
                        "Cannot rename inactive profession"
                    )
                last_title = event.title.strip()

            elif event.operation == "cancel":
                if not active:
                    raise D5HistoryError(
                        "Cannot cancel inactive profession"
                    )
                active = False

            if not active:
                continue

            next_date = (
                items[index + 1].effective_date
                if index + 1 < len(items)
                else None
            )

            end = (
                next_date - timedelta(days=1)
                if next_date else None
            )

            if end is not None and end < event.effective_date:
                raise D5HistoryError(
                    "Invalid history interval"
                )

            periods.append(ProfessionPeriod(
                profession_code=code,
                title=last_title,
                valid_from=event.effective_date,
                valid_to=end,
                source_id=event.source_id,
                source_sha256=event.source_sha256,
            ))

    return HistoryResult(
        periods=tuple(periods),
        event_count=len(events),
    )


def candidate_profession_on_date(
    result: HistoryResult,
    *,
    code: str,
    on_date: date,
) -> ProfessionPeriod | None:
    """Candidate lookup only; never use as statutory approval."""
    if not isinstance(result, HistoryResult):
        raise D5HistoryError("HistoryResult required")

    if not isinstance(on_date, date):
        raise D5HistoryError("Invalid lookup date")

    matches = [
        period for period in result.periods
        if period.profession_code == code
        and period.valid_from <= on_date
        and (
            period.valid_to is None
            or on_date <= period.valid_to
        )
    ]

    if len(matches) > 1:
        raise D5HistoryError(
            "Overlapping profession periods"
        )

    return matches[0] if matches else None
