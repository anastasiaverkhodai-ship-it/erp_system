"""One immutable detail package; monetary values remain in canonical subledgers."""
from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class OpeningBalanceDetail(Base):
    __tablename__ = 'opening_balance_details'
    __table_args__ = (
        CheckConstraint("stock_document_type = 'receipt'", name='ck_opening_detail_receipt'),
        UniqueConstraint('company_id', 'opening_balance_id', name='uq_opening_detail_source'),
        ForeignKeyConstraint(['company_id', 'opening_balance_id'],
            ['opening_balances.company_id', 'opening_balances.id'], name='fk_opening_detail_source', ondelete='RESTRICT'),
        ForeignKeyConstraint(['company_id', 'stock_document_id', 'stock_document_type'],
            ['documents.company_id', 'documents.id', 'documents.document_type'], name='fk_opening_detail_stock', ondelete='RESTRICT'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False)
    opening_balance_id: Mapped[int] = mapped_column(Integer, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    stock_document_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    stock_document_type: Mapped[str] = mapped_column(String(10), default='receipt', nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
