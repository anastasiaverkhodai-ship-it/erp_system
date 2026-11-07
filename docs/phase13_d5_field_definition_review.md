# ERP 13.14 — D5 Field Definition Review

## Purpose

Separate actual D5 field-definition statements from
cross-references to D1, D6 and other form sections.

The review detects single and combined field definitions,
including the combined profession-code/name definition
for fields 13 and 14.

## Evidence

A real-document review produces an external JSON register
with field numbers, original source SHA-256, offsets,
excerpts, excerpt hashes, missing fields and duplicates.

## Limits

Detected definitions are candidates, not legally approved
statutory mappings.

Before official generation, independently verify:

- The original official form and applicable edition.
- Exact field names and accepted values.
- Relevant effective dates and classifier amendments.
- Required fields for every employment event.
- Corrections, cancellation and reversal rules.
- Source-bound PostgreSQL statutory row tests.

OFFICIAL D5 GENERATION = BLOCKED.
PHASE 13 = OPEN.
