from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.account import Account
from app.models.accounting_rule import AccountingRule
from app.models.accounting_rule_line import AccountingRuleLine
from app.models.company import Company
from app.models.document import Document
from app.models.journal_entry import JournalEntry
from app.schemas.accounting_rule import (
    AccountingRuleCreate,
    AccountingRuleResponse,
    AccountingRuleUpdate,
)


router = APIRouter(
    prefix="/companies/{company_id}/accounting-rules",
    tags=["Accounting Rules"],
)


# ---------------------------------------------------------
# GET ACCOUNTING RULE LIST
# ---------------------------------------------------------


@router.get(
    "",
    response_model=list[AccountingRuleResponse],
)
async def get_accounting_rules(
    company_id: int,
    _=Depends(
        require_company_permission(
            "accounting_rules.read"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AccountingRule)
        .options(
            selectinload(AccountingRule.lines)
        )
        .where(
            AccountingRule.company_id == company_id
        )
        .order_by(
            AccountingRule.code.asc(),
            AccountingRule.id.asc(),
        )
    )

    return result.scalars().all()


# ---------------------------------------------------------
# GET ONE ACCOUNTING RULE
# ---------------------------------------------------------


@router.get(
    "/{accounting_rule_id}",
    response_model=AccountingRuleResponse,
)
async def get_accounting_rule(
    company_id: int,
    accounting_rule_id: int,
    _=Depends(
        require_company_permission(
            "accounting_rules.read"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AccountingRule)
        .options(
            selectinload(AccountingRule.lines)
        )
        .where(
            AccountingRule.id == accounting_rule_id,
            AccountingRule.company_id == company_id,
        )
    )

    accounting_rule = result.scalar_one_or_none()

    if accounting_rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Accounting rule not found",
        )

    return accounting_rule


# ---------------------------------------------------------
# CREATE ACCOUNTING RULE
# ---------------------------------------------------------


@router.post(
    "",
    response_model=AccountingRuleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_accounting_rule(
    company_id: int,
    data: AccountingRuleCreate,
    _=Depends(
        require_company_permission(
            "accounting_rules.create"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        # Company must exist and be active.
        company_result = await db.execute(
            select(Company).where(
                Company.id == company_id,
                Company.is_active.is_(True),
            )
        )

        company = company_result.scalar_one_or_none()

        if company is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Company not found",
            )

        # Rule code must be unique inside the company.
        existing_result = await db.execute(
            select(AccountingRule.id).where(
                AccountingRule.company_id == company_id,
                AccountingRule.code == data.code,
            )
        )

        if (
            existing_result.scalar_one_or_none()
            is not None
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Accounting rule code already exists "
                    "in this company"
                ),
            )

        # All accounts used by the rule must belong
        # to the same company and must currently be active.
        account_ids = {
            line.account_id
            for line in data.lines
        }

        accounts_result = await db.execute(
            select(Account.id).where(
                Account.id.in_(account_ids),
                Account.company_id == company_id,
                Account.is_active.is_(True),
                Account.is_postable.is_(True),
            )
        )

        valid_account_ids = set(
            accounts_result.scalars().all()
        )

        invalid_account_ids = (
            account_ids - valid_account_ids
        )

        if invalid_account_ids:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Accounting rule contains invalid, "
                    "inactive, non-postable, or foreign-company accounts: "
                    f"{sorted(invalid_account_ids)}"
                ),
            )

        accounting_rule = AccountingRule(
            company_id=company_id,
            code=data.code,
            name=data.name,
            document_type=data.document_type,
            is_active=True,
        )

        accounting_rule.lines = [
            AccountingRuleLine(
                line_no=line_no,
                account_id=line.account_id,
                side=line.side,
                amount_source=line.amount_source,
                description=line.description,
            )
            for line_no, line in enumerate(
                data.lines,
                start=1,
            )
        ]

        db.add(accounting_rule)

        await db.commit()

        result = await db.execute(
            select(AccountingRule)
            .options(
                selectinload(AccountingRule.lines)
            )
            .where(
                AccountingRule.id == accounting_rule.id,
                AccountingRule.company_id == company_id,
            )
        )

        return result.scalar_one()

    except HTTPException:
        await db.rollback()
        raise

    except IntegrityError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Accounting rule could not be created "
                "because of a data conflict"
            ),
        ) from exc

    except Exception:
        await db.rollback()
        raise

    # ---------------------------------------------------------
# UPDATE ACCOUNTING RULE
# ---------------------------------------------------------


@router.patch(
    "/{accounting_rule_id}",
    response_model=AccountingRuleResponse,
)
async def update_accounting_rule(
    company_id: int,
    accounting_rule_id: int,
    data: AccountingRuleUpdate,
    _=Depends(
        require_company_permission(
            "accounting_rules.update"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await db.execute(
            select(AccountingRule)
            .where(
                AccountingRule.id == accounting_rule_id,
                AccountingRule.company_id == company_id,
            )
            .with_for_update()
        )

        accounting_rule = result.scalar_one_or_none()

        if accounting_rule is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Accounting rule not found",
            )

        # Check whether this rule has already been used.
        document_usage_result = await db.execute(
            select(Document.id)
            .where(
                Document.company_id == company_id,
                Document.accounting_rule_id
                == accounting_rule.id,
            )
            .limit(1)
        )

        journal_usage_result = await db.execute(
            select(JournalEntry.id)
            .where(
                JournalEntry.company_id == company_id,
                JournalEntry.accounting_rule_id
                == accounting_rule.id,
            )
            .limit(1)
        )

        rule_is_used = (
            document_usage_result.scalar_one_or_none()
            is not None
            or journal_usage_result.scalar_one_or_none()
            is not None
        )

        if rule_is_used:
            immutable_fields_requested = (
                data.code is not None
                or data.name is not None
                or data.document_type is not None
                or data.lines is not None
            )

            if immutable_fields_requested:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Accounting rule has already been used "
                        "and its structure cannot be changed. "
                        "Only is_active may be updated."
                    ),
                )
        update_data = data.model_dump(
            exclude_unset=True,
            exclude={"lines"},
        )

        # Check company-specific code uniqueness.
        if (
            "code" in update_data
            and update_data["code"] != accounting_rule.code
        ):
            existing_result = await db.execute(
                select(AccountingRule.id).where(
                    AccountingRule.company_id == company_id,
                    AccountingRule.code == update_data["code"],
                    AccountingRule.id != accounting_rule.id,
                )
            )

            if (
                existing_result.scalar_one_or_none()
                is not None
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Accounting rule code already exists "
                        "in this company"
                    ),
                )

        # Validate replacement lines BEFORE deleting
        # the existing rule lines.
        if data.lines is not None:
            account_ids = {
                line.account_id
                for line in data.lines
            }

            accounts_result = await db.execute(
                select(Account.id).where(
                    Account.id.in_(account_ids),
                    Account.company_id == company_id,
                    Account.is_active.is_(True),
                    Account.is_postable.is_(True),
                )
            )

            valid_account_ids = set(
                accounts_result.scalars().all()
            )

            invalid_account_ids = (
                account_ids - valid_account_ids
            )

            if invalid_account_ids:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Accounting rule contains invalid, "
                        "inactive, non-postable, or foreign-company accounts: "
                        f"{sorted(invalid_account_ids)}"
                    ),
                )

        # Update header fields.
        for field, value in update_data.items():
            setattr(
                accounting_rule,
                field,
                value,
            )

        # Replace rule lines if new lines were supplied.
        if data.lines is not None:
            await db.execute(
                delete(AccountingRuleLine).where(
                    AccountingRuleLine.accounting_rule_id
                    == accounting_rule.id
                )
            )

            for line_no, line in enumerate(
                data.lines,
                start=1,
            ):
                db.add(
                    AccountingRuleLine(
                        accounting_rule_id=accounting_rule.id,
                        line_no=line_no,
                        account_id=line.account_id,
                        side=line.side,
                        amount_source=line.amount_source,
                        description=line.description,
                    )
                )

        await db.commit()

        result = await db.execute(
            select(AccountingRule)
            .options(
                selectinload(AccountingRule.lines)
            )
            .where(
                AccountingRule.id == accounting_rule_id,
                AccountingRule.company_id == company_id,
            )
        )

        return result.scalar_one()

    except HTTPException:
        await db.rollback()
        raise

    except IntegrityError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Accounting rule could not be updated "
                "because of a data conflict"
            ),
        ) from exc

    except Exception:
        await db.rollback()
        raise