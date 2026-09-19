from app.api.v1.input_vat_credit_claims import router as input_vat_credit_claims_router
from app.api.v1.tax_invoices import router as tax_invoices_router
from app.api.v1.tax_invoice_corrections import router as tax_invoice_corrections_router
from app.api.v1.output_vat_controls import router as output_vat_controls_router
from app.api.v1.order_vat_advances import router as order_vat_advances_router
from fastapi import FastAPI

from app.api.v1.accounts import router as accounts_router
from app.api.v1.accounting_periods import router as accounting_periods_router
from app.api.v1.auth import router as auth_router
from app.api.v1.companies import router as companies_router
from app.api.v1.counterparties import router as counterparties_router
from app.api.v1.payments import router as payments_router
from app.api.v1.bank_accounts import router as bank_accounts_router
from app.api.v1.bank_statements import router as bank_statements_router
from app.api.v1.cash_desks import router as cash_desks_router
from app.api.v1.cash_documents import router as cash_documents_router
from app.api.v1.counterparty_open_items import router as counterparty_open_items_router
from app.api.v1.contracts import router as contracts_router
from app.api.v1.company_users import router as company_users_router
from app.api.v1.documents import router as documents_router
from app.api.v1.trade_documents import router as trade_documents_router
from app.api.v1.products import router as products_router
from app.api.v1.warehouses import router as warehouses_router
from app.api.v1.journal_entries import router as journal_entries_router
from app.api.v1.general_ledger import router as general_ledger_router
from app.api.v1.trial_balance import router as trial_balance_router
from app.api.v1.accounting_rules import router as accounting_rules_router
from app.api.v1.purchase_landed_costs import router as purchase_landed_costs_router
from app.api.v1.purchase_quotes import router as purchase_quotes_router

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
    payments_router,
    prefix="/api/v1",
)
app.include_router(
    counterparty_open_items_router,
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
    general_ledger_router,
    prefix="/api/v1",
)

app.include_router(
    trial_balance_router,
    prefix="/api/v1",
)

app.include_router(
    accounting_rules_router,
    prefix="/api/v1",
)

app.include_router(purchase_landed_costs_router, prefix="/api/v1")
app.include_router(purchase_quotes_router, prefix="/api/v1")

@app.get("/")
async def root():
    return {"message": "ERP API is running"}


@app.get("/health")
async def health():
    return {"status": "ok"}
app.include_router(
    bank_accounts_router,
    prefix="/api/v1",
)

app.include_router(
    bank_statements_router,
    prefix="/api/v1",
)

app.include_router(
    cash_desks_router,
    prefix="/api/v1",
)
app.include_router(
    cash_documents_router,
    prefix="/api/v1",
)

from app.api.v1.company_vat_policies import router as company_vat_policies_router
from app.api.v1.vat_declarations import router as vat_declarations_router
app.include_router(company_vat_policies_router, prefix='/api/v1')

app.include_router(order_vat_advances_router, prefix='/api/v1')

app.include_router(output_vat_controls_router, prefix="/api/v1")

app.include_router(input_vat_credit_claims_router, prefix="/api/v1")
app.include_router(tax_invoices_router, prefix="/api/v1")
app.include_router(tax_invoice_corrections_router, prefix="/api/v1")
app.include_router(vat_declarations_router, prefix="/api/v1")

from app.api.v1.vat_registers import router as vat_registers_router
app.include_router(vat_registers_router, prefix="/api/v1")
