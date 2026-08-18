from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.document import DocumentStatus, DocumentType


class DocumentLineCreate(BaseModel):
    product_id: int
    warehouse_id: int

    quantity: Decimal = Field(
        decimal_places=4,
    )

    price: Decimal = Field(
        default=Decimal("0"),
        ge=Decimal("0"),
        decimal_places=4,
    )


class DocumentCreate(BaseModel):
    number: str = Field(
        min_length=1,
        max_length=50,
    )

    document_type: DocumentType
    document_date: date

    lines: list[DocumentLineCreate] = Field(
        min_length=1,
    )

class DocumentUpdate(BaseModel):
    number: str | None = Field(
        default=None,
        min_length=1,
        max_length=50,
    )

    document_type: DocumentType | None = None
    document_date: date | None = None

    lines: list[DocumentLineCreate] | None = Field(
    default=None,
    min_length=1,
)

class DocumentPostRequest(BaseModel):
    accounting_rule_id: int

class DocumentReverseRequest(BaseModel):
    reversal_date: date

class DocumentLineResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: int
    product_id: int
    warehouse_id: int
    quantity: Decimal
    price: Decimal


class DocumentResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: int
    company_id: int
    accounting_rule_id: int | None
    number: str
    document_type: DocumentType
    document_date: date
    status: DocumentStatus
    created_by: int
    created_at: datetime
    posted_at: datetime | None
    reversed_at: datetime | None
    reversed_by: int | None

    lines: list[DocumentLineResponse]
