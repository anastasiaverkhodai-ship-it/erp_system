# Source-to-GL controls and detailed cutover

The consolidated accounting-control endpoint now implements AR, AP, cash/bank,
inventory and INPUT VAT alongside the existing OUTPUT VAT comparator.

## What is checked

- AR: economic sales recognition, returns and advance clearing; legacy warehouse
  document journals are reconstructed from their original rule and source values.
- AP/inventory: receipt/issue rules, immutable issue cost, returns, VAT fulfillment
  bridges, supplier clearing, purchase value corrections and landed-cost valuation.
- Cash/bank: confirmed payment and cancellation journals, actual configured money
  accounts, source amounts and business dates. Unattributed money movements are shown.
- VAT: recognition events and their linked journals, including reversals. INPUT VAT
  additionally requires the source's legal-credit evidence. This is a recognition
  comparator, not a blanket certification of all 641/643/644 postings or official XML.
- Detailed opening packages: imported stock value and debt principal independently
  compared with their opening journal legs; opening-debt settlements checked separately.

Missing/duplicate/draft journals, wrong accounts/amounts, shifted dates and broken
reversal links remain errors even when aggregate differences cancel. Same-company
warehouse transfers and opening stock receipts intentionally have no separate GL;
the exception requires typed provenance. Ruleless ordinary documents are not waived.

AR/AP reports show commercial balances separately from recognized economic balances.
The arithmetic bridge is an explanation, not automatic approval of every difference.
Inventory includes quantities by product and warehouse; its monetary control verifies
source accounting plans, not an independent rebuild of every historical valuation layer.
GL-only openings remain an explicit baseline until their detail is attached.

The HTTP control route executes in one repeatable-read, read-only transaction,
including authorization. Service callers must establish their own consistent snapshot.
Company boundaries, UAH scope, date-range and source-volume limits are enforced;
unsupported currencies are never silently combined without FX conversion.

## Historical data

Do not delete posted records to make a control green. Existing manual test journals,
unmapped historical warehouse documents or incomplete provenance require review.
The separately approved isolated foreign-account draft `TEST-JE-FOREIGN-ACCOUNT-001`
was backed up outside the repository and deleted through the canonical draft API.
No other business/test history is authorized for cleanup by this change.

## Local audit — 2026-09-24

After migration `d1552cd41bf1`, company 1's 2026 snapshot has:

- AR, cash/bank, INPUT VAT and OUTPUT VAT controls matched.
- AP and inventory each show GL 1,905.00 versus reconstructable sources 0.00.
  Receipt documents 1 and 5 and journals 8 and 9 have neither document nor
  journal accounting-rule provenance. Reversed issue documents 3 and 7 lack
  reconstructable accounting provenance. Manual journals 1, 5, 6 and 7 are
  separately identified as unattributed; offsetting entries are not silently waived.
- Zero unbalanced/empty posted journals and zero posted lines using another
  company's accounts. The specifically approved foreign-account draft is absent.
- One other draft remains. No blanket test-history deletion or authorization to
  close a real year is implied by passing the software regression.

The AP/inventory difference is an unresolved historical-data finding, not a claim
that the newly implemented controls or the complete local database are all green.

## Extension verification — 2026-09-24

- Full regression with PostgreSQL enabled: **3557 passed**, no failures or skips
  (499.83 seconds). Existing warnings remain; passing tests does not erase the
  historical-data findings documented above.
- Local database and code head: `d1552cd41bf1`; Alembic reports no schema drift.
- Detailed cutover tests cover both creation/attachment modes, FIFO/moving-average
  receipt and issue, AR/AP settlement without VAT, coherent reversal, atomic rollback,
  company isolation, permissions, concurrent retry and migration preservation.
