import asyncio

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.document import Document
from app.models.stock_balance import StockBalance
from app.models.stock_ledger import StockLedger
from app.services.document_posting import (
    DocumentPostingError,
    post_document,
)


COMPANY_ID = 1

DOCUMENT_NUMBERS = (
    "CONC-ISS-001",
    "CONC-ISS-002",
)


async def post_one_document(
    document_number: str,
) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Document.id).where(
                Document.company_id == COMPANY_ID,
                Document.number == document_number,
            )
        )

        document_id = result.scalar_one_or_none()

        if document_id is None:
            print(
                f"{document_number}: NOT FOUND"
            )
            return

        try:
            await post_document(
                db=db,
                company_id=COMPANY_ID,
                document_id=document_id,
            )

            await db.commit()

            print(
                f"{document_number}: POSTED"
            )

        except DocumentPostingError as exc:
            await db.rollback()

            print(
                f"{document_number}: REJECTED -> {exc}"
            )

        except Exception as exc:
            await db.rollback()

            print(
                f"{document_number}: ERROR -> {exc!r}"
            )


async def show_final_state() -> None:
    async with AsyncSessionLocal() as db:
        documents_result = await db.execute(
            select(
                Document.number,
                Document.status,
            ).where(
                Document.company_id == COMPANY_ID,
                Document.number.in_(
                    DOCUMENT_NUMBERS
                ),
            )
        )

        print("\nDOCUMENTS:")

        for number, status in documents_result.all():
            print(
                f"{number}: {status}"
            )

        balance_result = await db.execute(
            select(
                StockBalance.quantity
            ).where(
                StockBalance.company_id == COMPANY_ID,
                StockBalance.product_id == 1,
                StockBalance.warehouse_id == 1,
            )
        )

        balance = balance_result.scalar_one()

        ledger_result = await db.execute(
            select(
                StockLedger.quantity
            ).where(
                StockLedger.company_id == COMPANY_ID,
                StockLedger.product_id == 1,
                StockLedger.warehouse_id == 1,
            )
        )

        ledger_balance = sum(
            (
                row[0]
                for row in ledger_result.all()
            ),
            start=0,
        )

        print("\nBALANCES:")
        print(
            f"StockBalance = {balance}"
        )
        print(
            f"StockLedger SUM = {ledger_balance}"
        )


async def main() -> None:
    print(
        "Starting two postings concurrently...\n"
    )

    await asyncio.gather(
        post_one_document(
            "CONC-ISS-001"
        ),
        post_one_document(
            "CONC-ISS-002"
        ),
    )

    await show_final_state()


if __name__ == "__main__":
    asyncio.run(main())