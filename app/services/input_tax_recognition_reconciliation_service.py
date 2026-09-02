from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tax_calculation import TaxCalculation
from app.models.tax_credit_evidence import TaxCreditEvidence
from app.models.tax_recognition_event import TaxRecognitionEvent
from app.services.input_tax_recognition_candidate_loader_service import (
    load_active_input_tax_recognition_candidates,
)
from app.services.input_tax_recognition_evidence_allocation_service import (
    InputTaxRecognitionEvidenceTarget,
    build_input_tax_recognition_evidence_targets,
)
from app.services.input_tax_recognition_persistence_service import (
    InputTaxRecognitionPersistenceTarget,
    reconcile_input_tax_recognition_source,
)
from app.services.tax_recognition_orchestration_service import (
    TaxRecognitionCandidate,
)


ZERO = Decimal("0")


class InputTaxRecognitionReconciliationError(
    Exception
):
    """Base INPUT VAT reconciliation error."""


class InputTaxRecognitionReconciliationStateError(
    InputTaxRecognitionReconciliationError
):
    """Current or desired INPUT recognition state is invalid."""


class InputTaxRecognitionReconciliationIntegrityError(
    InputTaxRecognitionReconciliationError
):
    """Persisted INPUT recognition history is inconsistent."""


class InputTaxRecognitionReconciliationNotFoundError(
    InputTaxRecognitionReconciliationError
):
    """TaxCalculation does not exist."""


@dataclass(
    frozen=True,
    slots=True,
)
class InputTaxRecognitionReconciliationResult:
    tax_calculation_id: int

    economic_candidates: tuple[
        TaxRecognitionCandidate,
        ...,
    ]

    current_targets: tuple[
        InputTaxRecognitionPersistenceTarget,
        ...,
    ]

    desired_targets: tuple[
        InputTaxRecognitionPersistenceTarget,
        ...,
    ]

    adjustments: tuple[
        InputTaxRecognitionPersistenceTarget,
        ...,
    ]

    created_events: tuple[
        TaxRecognitionEvent,
        ...,
    ]


def _decimal(
    value,
    *,
    field: str,
) -> Decimal:
    try:
        result = Decimal(
            str(value)
        )
    except Exception as exc:
        raise (
            InputTaxRecognitionReconciliationIntegrityError(
                f"{field} must be a decimal amount"
            )
        ) from exc

    if not result.is_finite():
        raise (
            InputTaxRecognitionReconciliationIntegrityError(
                f"{field} must be finite"
            )
        )

    return result


def _positive_id(
    value: int,
    *,
    field: str,
) -> int:
    if (
        isinstance(
            value,
            bool,
        )
        or not isinstance(
            value,
            int,
        )
        or value <= 0
    ):
        raise ValueError(
            f"{field} must be greater than zero"
        )

    return value


def _active_original_events(
    events: Iterable,
) -> tuple:
    event_tuple = tuple(
        events
    )

    by_id = {}

    for event in event_tuple:
        event_id = getattr(
            event,
            "id",
            None,
        )

        if (
            event_id is None
            or event_id <= 0
        ):
            raise (
                InputTaxRecognitionReconciliationIntegrityError(
                    "Persistent TaxRecognitionEvent "
                    "must have a positive id"
                )
            )

        if event_id in by_id:
            raise (
                InputTaxRecognitionReconciliationIntegrityError(
                    "Duplicate TaxRecognitionEvent id"
                )
            )

        by_id[
            event_id
        ] = event

    reversed_ids = set()

    for event in event_tuple:
        reversal_of_id = getattr(
            event,
            "reversal_of_id",
            None,
        )

        if reversal_of_id is None:
            continue

        original = by_id.get(
            reversal_of_id
        )

        if original is None:
            raise (
                InputTaxRecognitionReconciliationIntegrityError(
                    "Recognition reversal references "
                    "missing original"
                )
            )

        if (
            getattr(
                original,
                "reversal_of_id",
                None,
            )
            is not None
        ):
            raise (
                InputTaxRecognitionReconciliationIntegrityError(
                    "Recognition reversal cannot "
                    "reverse another reversal"
                )
            )

        if reversal_of_id in reversed_ids:
            raise (
                InputTaxRecognitionReconciliationIntegrityError(
                    "Recognition original has "
                    "multiple reversals"
                )
            )

        copied_fields = (
            "company_id",
            "tax_calculation_id",
            "invoice_fulfillment_allocation_id",
            "payment_settlement_allocation_id",
            "tax_credit_evidence_id",
            "recognized_taxable_base",
            "recognized_tax_amount",
            "currency_code",
        )

        for field in copied_fields:
            if (
                getattr(
                    event,
                    field,
                    None,
                )
                != getattr(
                    original,
                    field,
                    None,
                )
            ):
                raise (
                    InputTaxRecognitionReconciliationIntegrityError(
                        "Recognition reversal must copy "
                        "original source and amounts"
                    )
                )

        if (
            event.recognition_date
            < original.recognition_date
        ):
            raise (
                InputTaxRecognitionReconciliationIntegrityError(
                    "Recognition reversal date "
                    "precedes original"
                )
            )

        reversed_ids.add(
            reversal_of_id
        )

    return tuple(
        event
        for event in event_tuple
        if (
            getattr(
                event,
                "reversal_of_id",
                None,
            )
            is None
            and event.id
            not in reversed_ids
        )
    )


def build_current_input_tax_recognition_targets(
    events: Iterable,
) -> tuple[
    InputTaxRecognitionPersistenceTarget,
    ...,
]:
    """
    Derive current ACTIVE INPUT VAT recognition state from
    immutable TaxRecognitionEvent history.

    Automatic INPUT events must have exactly one typed source:
        tax_credit_evidence_id.
    """

    active = _active_original_events(
        events
    )

    result = []
    seen_tranches = set()

    for event in active:
        evidence_id = getattr(
            event,
            "tax_credit_evidence_id",
            None,
        )

        if (
            evidence_id is None
            or evidence_id <= 0
        ):
            raise (
                InputTaxRecognitionReconciliationIntegrityError(
                    "ACTIVE automatic INPUT recognition "
                    "must use TaxCreditEvidence source"
                )
            )

        if (
            getattr(
                event,
                "invoice_fulfillment_allocation_id",
                None,
            )
            is not None
            or getattr(
                event,
                "payment_settlement_allocation_id",
                None,
            )
            is not None
        ):
            raise (
                InputTaxRecognitionReconciliationIntegrityError(
                    "ACTIVE INPUT recognition cannot "
                    "contain OUTPUT typed source"
                )
            )

        tranche_key = (
            evidence_id,
            event.recognition_date,
        )

        if tranche_key in seen_tranches:
            raise (
                InputTaxRecognitionReconciliationIntegrityError(
                    "Multiple ACTIVE INPUT recognition "
                    "events for one evidence/date tranche"
                )
            )

        seen_tranches.add(
            tranche_key
        )

        base = _decimal(
            event.recognized_taxable_base,
            field=(
                "recognized_taxable_base"
            ),
        )

        tax = _decimal(
            event.recognized_tax_amount,
            field="recognized_tax_amount",
        )

        if (
            base < ZERO
            or tax < ZERO
            or (
                base == ZERO
                and tax == ZERO
            )
        ):
            raise (
                InputTaxRecognitionReconciliationIntegrityError(
                    "ACTIVE automatic INPUT recognition "
                    "must contain positive amounts"
                )
            )

        result.append(
            InputTaxRecognitionPersistenceTarget(
                tax_credit_evidence_id=(
                    evidence_id
                ),
                recognition_date=(
                    event.recognition_date
                ),
                taxable_base=base,
                tax_amount=tax,
            )
        )

    return tuple(
        sorted(
            result,
            key=lambda item: (
                item.recognition_date,
                item.tax_credit_evidence_id,
            ),
        )
    )


def _convert_desired_targets(
    targets: Iterable[
        InputTaxRecognitionEvidenceTarget
    ],
) -> tuple[
    InputTaxRecognitionPersistenceTarget,
    ...,
]:
    result = []

    seen = set()

    for target in targets:
        source_id = (
            target.tax_credit_evidence_id
        )

        tranche_key = (
            source_id,
            target.event_date,
        )

        if tranche_key in seen:
            raise (
                InputTaxRecognitionReconciliationStateError(
                    "Duplicate desired INPUT recognition "
                    "evidence/date tranche"
                )
            )

        seen.add(
            tranche_key
        )

        result.append(
            InputTaxRecognitionPersistenceTarget(
                tax_credit_evidence_id=(
                    source_id
                ),
                recognition_date=(
                    target.event_date
                ),
                taxable_base=(
                    target.taxable_base
                ),
                tax_amount=(
                    target.tax_amount
                ),
            )
        )

    return tuple(
        sorted(
            result,
            key=lambda item: (
                item.recognition_date,
                item.tax_credit_evidence_id,
            ),
        )
    )


def _target_map(
    targets: Iterable[
        InputTaxRecognitionPersistenceTarget
    ],
) -> dict[
    tuple[
        int,
        date,
    ],
    InputTaxRecognitionPersistenceTarget,
]:
    result = {}

    for target in targets:
        tranche_key = (
            target.tax_credit_evidence_id,
            target.recognition_date,
        )

        if tranche_key in result:
            raise (
                InputTaxRecognitionReconciliationStateError(
                    "Duplicate INPUT recognition "
                    "evidence/date tranche"
                )
            )

        result[
            tranche_key
        ] = target

    return result


def order_input_tax_recognition_reconciliations(
    *,
    current_targets: Iterable[
        InputTaxRecognitionPersistenceTarget
    ],
    desired_targets: Iterable[
        InputTaxRecognitionPersistenceTarget
    ],
    adjustment_date: date,
) -> tuple[
    InputTaxRecognitionPersistenceTarget,
    ...,
]:
    """
    Reconcile immutable INPUT VAT tranches.

    Tranche identity:
        (
            tax_credit_evidence_id,
            recognition_date,
        )

    Consequences:

    - same evidence may have multiple active dates;
    - changing D1 -> D2 is NOT mutation of one row;
      it becomes removal of the D1 tranche followed by
      creation of the D2 tranche;
    - decreases/removals execute before increases so temporary
      recognition never exceeds TaxCalculation/evidence capacity.

    A zero target retains the ORIGINAL recognition_date because
    it identifies the tranche being removed. adjustment_date is
    the separate business date on which its reversal is posted.
    """

    if not isinstance(
        adjustment_date,
        date,
    ):
        raise (
            InputTaxRecognitionReconciliationStateError(
                "adjustment_date must be a date"
            )
        )

    current = _target_map(
        current_targets
    )

    desired = _target_map(
        desired_targets
    )

    for desired_target in desired.values():
        if (
            desired_target.recognition_date
            > adjustment_date
        ):
            raise (
                InputTaxRecognitionReconciliationStateError(
                    "Desired INPUT recognition tranche "
                    "cannot be in the future relative "
                    "to adjustment_date"
                )
            )

    decreases = []
    increases = []

    tranche_keys = (
        set(current)
        | set(desired)
    )

    for tranche_key in tranche_keys:
        current_target = current.get(
            tranche_key
        )

        desired_target = desired.get(
            tranche_key
        )

        if current_target is None:
            if desired_target is not None:
                increases.append(
                    desired_target
                )

            continue

        if desired_target is None:
            if (
                adjustment_date
                < current_target.recognition_date
            ):
                raise (
                    InputTaxRecognitionReconciliationStateError(
                        "INPUT recognition tranche "
                        "cannot be reversed before its "
                        "recognition date"
                    )
                )

            decreases.append(
                InputTaxRecognitionPersistenceTarget(
                    tax_credit_evidence_id=(
                        current_target
                        .tax_credit_evidence_id
                    ),
                    recognition_date=(
                        current_target
                        .recognition_date
                    ),
                    taxable_base=ZERO,
                    tax_amount=ZERO,
                )
            )

            continue

        if (
            current_target.taxable_base
            == desired_target.taxable_base
            and current_target.tax_amount
            == desired_target.tax_amount
        ):
            continue

        base_decreased = (
            desired_target.taxable_base
            < current_target.taxable_base
        )

        tax_decreased = (
            desired_target.tax_amount
            < current_target.tax_amount
        )

        base_increased = (
            desired_target.taxable_base
            > current_target.taxable_base
        )

        tax_increased = (
            desired_target.tax_amount
            > current_target.tax_amount
        )

        if (
            (
                base_decreased
                and tax_increased
            )
            or (
                tax_decreased
                and base_increased
            )
        ):
            raise (
                InputTaxRecognitionReconciliationStateError(
                    "INPUT tranche cannot require mixed "
                    "increase/decrease adjustment"
                )
            )

        if (
            base_decreased
            or tax_decreased
        ):
            decreases.append(
                desired_target
            )

        elif (
            base_increased
            or tax_increased
        ):
            increases.append(
                desired_target
            )

    sort_key = lambda item: (
        item.recognition_date,
        item.tax_credit_evidence_id,
    )

    decreases.sort(
        key=sort_key
    )

    increases.sort(
        key=sort_key
    )

    return tuple(
        decreases
        + increases
    )


async def _lock_tax_calculation(
    db: AsyncSession,
    *,
    company_id: int,
    tax_calculation_id: int,
) -> TaxCalculation:
    calculation = (
        await db.execute(
            select(
                TaxCalculation
            )
            .where(
                TaxCalculation.company_id
                == company_id,
                TaxCalculation.id
                == tax_calculation_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if calculation is None:
        raise (
            InputTaxRecognitionReconciliationNotFoundError(
                "TaxCalculation not found"
            )
        )

    return calculation


async def _load_locked_evidence_history(
    db: AsyncSession,
    *,
    company_id: int,
    tax_calculation_id: int,
) -> tuple[
    TaxCreditEvidence,
    ...,
]:
    return tuple(
        (
            await db.execute(
                select(
                    TaxCreditEvidence
                )
                .where(
                    TaxCreditEvidence.company_id
                    == company_id,
                    TaxCreditEvidence.tax_calculation_id
                    == tax_calculation_id,
                )
                .order_by(
                    TaxCreditEvidence.id
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )


async def _load_locked_recognition_history(
    db: AsyncSession,
    *,
    company_id: int,
    tax_calculation_id: int,
) -> tuple[
    TaxRecognitionEvent,
    ...,
]:
    return tuple(
        (
            await db.execute(
                select(
                    TaxRecognitionEvent
                )
                .where(
                    TaxRecognitionEvent.company_id
                    == company_id,
                    TaxRecognitionEvent.tax_calculation_id
                    == tax_calculation_id,
                )
                .order_by(
                    TaxRecognitionEvent.id
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )


async def reconcile_input_tax_calculation_from_candidates(
    db: AsyncSession,
    *,
    company_id: int,
    tax_calculation_id: int,
    economic_candidates: Iterable[
        TaxRecognitionCandidate
    ],
    adjustment_date: date,
    created_by: int,
) -> InputTaxRecognitionReconciliationResult:
    """
    Full INPUT VAT reconciliation once economic candidates
    are known.

    Pipeline:
        economic candidates
        + locked TaxCreditEvidence history
        -> desired evidence targets
        -> current immutable recognition state
        -> decreases before increases
        -> TaxRecognitionEvent create/reversal executor.

    Caller owns commit/rollback.
    """

    company_id = _positive_id(
        company_id,
        field="company_id",
    )

    tax_calculation_id = _positive_id(
        tax_calculation_id,
        field="tax_calculation_id",
    )

    created_by = _positive_id(
        created_by,
        field="created_by",
    )

    if not isinstance(
        adjustment_date,
        date,
    ):
        raise ValueError(
            "adjustment_date must be a date"
        )

    candidate_tuple = tuple(
        economic_candidates
    )

    calculation = await _lock_tax_calculation(
        db,
        company_id=company_id,
        tax_calculation_id=(
            tax_calculation_id
        ),
    )

    evidence_events = (
        await _load_locked_evidence_history(
            db,
            company_id=company_id,
            tax_calculation_id=(
                tax_calculation_id
            ),
        )
    )

    recognition_events = (
        await _load_locked_recognition_history(
            db,
            company_id=company_id,
            tax_calculation_id=(
                tax_calculation_id
            ),
        )
    )

    desired_evidence_targets = (
        build_input_tax_recognition_evidence_targets(
            calculation=calculation,
            economic_candidates=(
                candidate_tuple
            ),
            evidence_events=(
                evidence_events
            ),
            as_of_date=(
                adjustment_date
            ),
        )
    )

    desired_targets = (
        _convert_desired_targets(
            desired_evidence_targets
        )
    )

    current_targets = (
        build_current_input_tax_recognition_targets(
            recognition_events
        )
    )

    adjustments = (
        order_input_tax_recognition_reconciliations(
            current_targets=(
                current_targets
            ),
            desired_targets=(
                desired_targets
            ),
            adjustment_date=(
                adjustment_date
            ),
        )
    )

    created_events = []

    for target in adjustments:
        created = (
            await reconcile_input_tax_recognition_source(
                db,
                company_id=company_id,
                tax_calculation_id=(
                    tax_calculation_id
                ),
                target=target,
                created_by=created_by,
                reversal_date=(
                    adjustment_date
                ),
            )
        )

        created_events.extend(
            created
        )

    return (
        InputTaxRecognitionReconciliationResult(
            tax_calculation_id=(
                tax_calculation_id
            ),
            economic_candidates=(
                candidate_tuple
            ),
            current_targets=(
                current_targets
            ),
            desired_targets=(
                desired_targets
            ),
            adjustments=(
                adjustments
            ),
            created_events=tuple(
                created_events
            ),
        )
    )

async def reconcile_input_tax_calculation_from_active_sources(
    db: AsyncSession,
    *,
    company_id: int,
    tax_calculation_id: int,
    adjustment_date: date,
    created_by: int,
) -> InputTaxRecognitionReconciliationResult:
    """
    Reconcile automatic INPUT VAT directly from persistent
    purchase-side economic sources.

    Pipeline:

        locked TaxCalculation
        -> active PURCHASE economic candidates
           (RECEIPT / OUTGOING settlement)
        -> TaxCreditEvidence gate
        -> evidence allocation
        -> immutable TaxRecognitionEvent reconciliation.

    Caller owns commit/rollback.
    """

    company_id = _positive_id(
        company_id,
        field="company_id",
    )

    tax_calculation_id = _positive_id(
        tax_calculation_id,
        field="tax_calculation_id",
    )

    created_by = _positive_id(
        created_by,
        field="created_by",
    )

    if not isinstance(
        adjustment_date,
        date,
    ):
        raise ValueError(
            "adjustment_date must be a date"
        )

    calculation = await _lock_tax_calculation(
        db,
        company_id=company_id,
        tax_calculation_id=(
            tax_calculation_id
        ),
    )

    economic_candidates = (
        await load_active_input_tax_recognition_candidates(
            db,
            calculation=calculation,
        )
    )

    return (
        await reconcile_input_tax_calculation_from_candidates(
            db,
            company_id=company_id,
            tax_calculation_id=(
                tax_calculation_id
            ),
            economic_candidates=(
                economic_candidates
            ),
            adjustment_date=(
                adjustment_date
            ),
            created_by=created_by,
        )
    )
