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

from app.api.deps import get_current_user
from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.company import Company
from app.models.document import Document, DocumentStatus
from app.models.document_line import DocumentLine
from app.models.product import Product
from app.models.user import User
from app.models.warehouse import Warehouse
from app.schemas.document import (
    DocumentCreate,
    DocumentPostRequest,
    DocumentResponse,
    DocumentReverseRequest,
    DocumentUpdate,
)
from app.services.document_posting import (
    DocumentNotFoundError,
    DocumentPostingError,
    post_document,
)



from app.services.document_reversal import (
    DocumentReversalError,
    DocumentReversalNotFoundError,
    reverse_document,
)

router = APIRouter(
    prefix="/companies/{company_id}/documents",
    tags=["Documents"],
)


# ---------------------------------------------------------
# GET DOCUMENT LIST
# ---------------------------------------------------------


@router.get(
    "",
    response_model=list[DocumentResponse],
)
async def get_documents(
    company_id: int,
    _=Depends(
        require_company_permission(
            "documents.read"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Document)
        .options(
            selectinload(Document.lines)
        )
        .where(
            Document.company_id == company_id,
        )
        .order_by(
            Document.document_date.desc(),
            Document.id.desc(),
        )
    )

    return result.scalars().all()


# ---------------------------------------------------------
# GET ONE DOCUMENT
# ---------------------------------------------------------


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
)
async def get_document(
    company_id: int,
    document_id: int,
    _=Depends(
        require_company_permission(
            "documents.read"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Document)
        .options(
            selectinload(Document.lines)
        )
        .where(
            Document.id == document_id,
            Document.company_id == company_id,
        )
    )

    document = result.scalar_one_or_none()

    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    return document


# ---------------------------------------------------------
# CREATE DOCUMENT
# ---------------------------------------------------------


@router.post(
    "",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_document(
    company_id: int,
    data: DocumentCreate,
    _=Depends(
        require_company_permission(
            "documents.create"
        )
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
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

        existing_result = await db.execute(
            select(Document.id).where(
                Document.company_id == company_id,
                Document.number == data.number,
            )
        )

        if (
            existing_result.scalar_one_or_none()
            is not None
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Document number already exists "
                    "in this company"
                ),
            )

        document = Document(
            company_id=company_id,
            number=data.number,
            document_type=data.document_type,
            document_date=data.document_date,
            status=DocumentStatus.DRAFT,
            created_by=current_user.id,
        )

        db.add(document)

        await db.flush()

        for line_data in data.lines:
            product_result = await db.execute(
                select(Product).where(
                    Product.id
                    == line_data.product_id,
                    Product.company_id
                    == company_id,
                    Product.is_active.is_(True),
                )
            )

            product = (
                product_result.scalar_one_or_none()
            )

            if product is None:
                raise HTTPException(
                    status_code=(
                        status.HTTP_400_BAD_REQUEST
                    ),
                    detail=(
                        f"Product "
                        f"{line_data.product_id} "
                        f"is invalid or does not belong "
                        f"to this company"
                    ),
                )

            warehouse_result = await db.execute(
                select(Warehouse).where(
                    Warehouse.id
                    == line_data.warehouse_id,
                    Warehouse.company_id
                    == company_id,
                    Warehouse.is_active.is_(True),
                )
            )

            warehouse = (
                warehouse_result.scalar_one_or_none()
            )

            if warehouse is None:
                raise HTTPException(
                    status_code=(
                        status.HTTP_400_BAD_REQUEST
                    ),
                    detail=(
                        f"Warehouse "
                        f"{line_data.warehouse_id} "
                        f"is invalid or does not belong "
                        f"to this company"
                    ),
                )

            db.add(
                DocumentLine(
                    document_id=document.id,
                    product_id=line_data.product_id,
                    warehouse_id=(
                        line_data.warehouse_id
                    ),
                    quantity=line_data.quantity,
                    price=line_data.price,
                )
            )

        await db.commit()

        result = await db.execute(
            select(Document)
            .options(
                selectinload(Document.lines)
            )
            .where(
                Document.id == document.id,
                Document.company_id == company_id,
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
                "Document could not be created "
                "because of a data conflict"
            ),
        ) from exc

    except Exception:
        await db.rollback()
        raise


# ---------------------------------------------------------
# UPDATE DRAFT DOCUMENT
# ---------------------------------------------------------


@router.patch(
    "/{document_id}",
    response_model=DocumentResponse,
)
async def update_document(
    company_id: int,
    document_id: int,
    data: DocumentUpdate,
    _=Depends(
        require_company_permission(
            "documents.update"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await db.execute(
            select(Document)
            .where(
                Document.id == document_id,
                Document.company_id == company_id,
            )
            .with_for_update()
        )

        document = result.scalar_one_or_none()

        if document is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found",
            )

        if document.status != DocumentStatus.DRAFT:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only draft documents can be edited",
            )

        update_data = data.model_dump(
            exclude_unset=True
        )

        # -------------------------------------------------
        # DOCUMENT NUMBER
        # -------------------------------------------------

        if (
            "number" in update_data
            and update_data["number"] != document.number
        ):
            existing_result = await db.execute(
                select(Document.id).where(
                    Document.company_id == company_id,
                    Document.number
                    == update_data["number"],
                    Document.id != document.id,
                )
            )

            if (
                existing_result.scalar_one_or_none()
                is not None
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Document number already exists "
                        "in this company"
                    ),
                )

            document.number = update_data["number"]

        # -------------------------------------------------
        # HEADER FIELDS
        # -------------------------------------------------

        if "document_type" in update_data:
            document.document_type = (
                update_data["document_type"]
            )

        if "document_date" in update_data:
            document.document_date = (
                update_data["document_date"]
            )

        # -------------------------------------------------
        # REPLACE DOCUMENT LINES
        # -------------------------------------------------

        if "lines" in update_data:
            new_lines = data.lines

            if new_lines is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Document lines are required",
                )

            # Validate every new line BEFORE deleting old ones.
            for line_data in new_lines:
                product_result = await db.execute(
                    select(Product).where(
                        Product.id
                        == line_data.product_id,
                        Product.company_id
                        == company_id,
                        Product.is_active.is_(True),
                    )
                )

                product = (
                    product_result.scalar_one_or_none()
                )

                if product is None:
                    raise HTTPException(
                        status_code=(
                            status.HTTP_400_BAD_REQUEST
                        ),
                        detail=(
                            f"Product "
                            f"{line_data.product_id} "
                            f"is invalid or does not belong "
                            f"to this company"
                        ),
                    )

                warehouse_result = await db.execute(
                    select(Warehouse).where(
                        Warehouse.id
                        == line_data.warehouse_id,
                        Warehouse.company_id
                        == company_id,
                        Warehouse.is_active.is_(True),
                    )
                )

                warehouse = (
                    warehouse_result.scalar_one_or_none()
                )

                if warehouse is None:
                    raise HTTPException(
                        status_code=(
                            status.HTTP_400_BAD_REQUEST
                        ),
                        detail=(
                            f"Warehouse "
                            f"{line_data.warehouse_id} "
                            f"is invalid or does not belong "
                            f"to this company"
                        ),
                    )

            await db.execute(
                delete(DocumentLine).where(
                    DocumentLine.document_id
                    == document.id
                )
            )

            for line_data in new_lines:
                db.add(
                    DocumentLine(
                        document_id=document.id,
                        product_id=line_data.product_id,
                        warehouse_id=(
                            line_data.warehouse_id
                        ),
                        quantity=line_data.quantity,
                        price=line_data.price,
                    )
                )

        await db.commit()

        updated_result = await db.execute(
            select(Document)
            .options(
                selectinload(Document.lines)
            )
            .where(
                Document.id == document_id,
                Document.company_id == company_id,
            )
        )

        return updated_result.scalar_one()

    except HTTPException:
        await db.rollback()
        raise

    except IntegrityError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Document could not be updated "
                "because of a data conflict"
            ),
        ) from exc

    except Exception:
        await db.rollback()
        raise


# ---------------------------------------------------------
# DELETE DRAFT DOCUMENT
# ---------------------------------------------------------


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_document(
    company_id: int,
    document_id: int,
    _=Depends(
        require_company_permission(
            "documents.delete"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await db.execute(
            select(Document)
            .where(
                Document.id == document_id,
                Document.company_id == company_id,
            )
            .with_for_update()
        )

        document = result.scalar_one_or_none()

        if document is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found",
            )

        if document.status != DocumentStatus.DRAFT:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only draft documents can be deleted",
            )

        await db.delete(document)

        await db.commit()

    except HTTPException:
        await db.rollback()
        raise

    except IntegrityError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Document could not be deleted "
                "because it is referenced by other data"
            ),
        ) from exc

    except Exception:
        await db.rollback()
        raise
# ---------------------------------------------------------
# POST DOCUMENT
# ---------------------------------------------------------


@router.post(
    "/{document_id}/post",
    status_code=status.HTTP_200_OK,
)
async def post_document_endpoint(
    company_id: int,
    document_id: int,
    data: DocumentPostRequest,
    _=Depends(
        require_company_permission(
            "documents.approve"
        )
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        # -------------------------------------------------
        # 1. Post warehouse document
        # -------------------------------------------------
        document, journal_entry = await post_document(
            db=db,
            company_id=company_id,
            document_id=document_id,
            accounting_rule_id=data.accounting_rule_id,
            created_by=current_user.id,
        )

        journal_entry_id = journal_entry.id


        # -------------------------------------------------
        # 3. One COMMIT for warehouse + accounting
        # -------------------------------------------------

        await db.commit()
        await db.refresh(document)

        return {
            "id": document.id,
            "company_id": document.company_id,
            "number": document.number,
            "document_type": document.document_type,
            "status": document.status,
            "document_date": document.document_date,
            "posted_at": document.posted_at,
            "accounting_rule_id": data.accounting_rule_id,
            "journal_entry_id": journal_entry_id,
        }

    except DocumentNotFoundError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    except DocumentPostingError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    except IntegrityError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document posting conflict",
        ) from exc

    except HTTPException:
        await db.rollback()
        raise

    except Exception:
        await db.rollback()
        raise
    # ---------------------------------------------------------
# REVERSE POSTED DOCUMENT
# ---------------------------------------------------------


@router.post(
    "/{document_id}/reverse",
    response_model=DocumentResponse,
    status_code=status.HTTP_200_OK,
)
async def reverse_document_endpoint(
    company_id: int,
    document_id: int,
    data: DocumentReverseRequest,
    _=Depends(
        require_company_permission(
            "documents.reverse"
        )
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        document = await reverse_document(
            db=db,
            company_id=company_id,
            document_id=document_id,
            reversal_date=data.reversal_date,
            reversed_by=current_user.id,
        )

        await db.commit()

        result = await db.execute(
            select(Document)
            .options(
                selectinload(Document.lines)
            )
            .where(
                Document.id == document.id,
                Document.company_id == company_id,
            )
        )

        return result.scalar_one()

    except DocumentReversalNotFoundError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    except DocumentReversalError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    except HTTPException:
        await db.rollback()
        raise

    except Exception:
        await db.rollback()
        raise