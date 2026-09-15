"""Source-document identity for an actual landed-cost capitalization."""
from sqlalchemy import ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PurchaseLandedCostCapitalization(Base):
    __tablename__ = "purchase_landed_cost_capitalizations"
    __table_args__ = (
        UniqueConstraint("company_id", "request_key", name="uq_plcc_request"),
        UniqueConstraint("landed_cost_event_id", name="uq_plcc_event"),
        ForeignKeyConstraint(
            ["company_id", "landed_cost_event_id"],
            ["purchase_landed_cost_events.company_id", "purchase_landed_cost_events.id"],
            name="fk_plcc_event", ondelete="RESTRICT",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(nullable=False)
    landed_cost_event_id: Mapped[int] = mapped_column(nullable=False)
    source_journal_entry_line_id: Mapped[int] = mapped_column(
        ForeignKey("journal_entry_lines.id", ondelete="RESTRICT"), nullable=False,
    )
    request_key: Mapped[str] = mapped_column(String(100), nullable=False)
