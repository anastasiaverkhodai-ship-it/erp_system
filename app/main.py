from fastapi import FastAPI

from app.api.v1.accounts import router as accounts_router
from app.api.v1.accounting_periods import router as accounting_periods_router
from app.api.v1.auth import router as auth_router
from app.api.v1.companies import router as companies_router
from app.api.v1.counterparties import router as counterparties_router
from app.api.v1.contracts import router as contracts_router
from app.api.v1.company_users import router as company_users_router
from app.api.v1.documents import router as documents_router
from app.api.v1.trade_documents import router as trade_documents_router
from app.api.v1.products import router as products_router
from app.api.v1.warehouses import router as warehouses_router
from app.api.v1.journal_entries import router as journal_entries_router
from app.api.v1.accounting_rules import router as accounting_rules_router

app = FastAPI(
    title="ERP System API",
    version="1.0.0",
)


app.include_router(
    auth_router,
    prefix="/api/v1",
)

app.include_router(
    companies_router,
    prefix="/api/v1",
)

app.include_router(
    company_users_router,
    prefix="/api/v1",
)

app.include_router(
    accounting_periods_router,
    prefix="/api/v1",
)

app.include_router(
    accounts_router,
    prefix="/api/v1",
)

app.include_router(
    documents_router,
    prefix="/api/v1",
)

app.include_router(
    products_router,
    prefix="/api/v1",
)

app.include_router(
    counterparties_router,
    prefix="/api/v1",
)


app.include_router(
    contracts_router,
    prefix="/api/v1",
)

app.include_router(
    trade_documents_router,
    prefix="/api/v1",
)
app.include_router(
    warehouses_router,
    prefix="/api/v1",
)

app.include_router(
    journal_entries_router,
    prefix="/api/v1",
)

app.include_router(
    accounting_rules_router,
    prefix="/api/v1",
)

@app.get("/")
async def root():
    return {"message": "ERP API is running"}


@app.get("/health")
async def health():
    return {"status": "ok"}