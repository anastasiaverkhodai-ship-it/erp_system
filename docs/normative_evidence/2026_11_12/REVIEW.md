# D5 civil-profession reconciliation — 12 November 2026

**Subsequent decision (13 November):** the commencement review and production
registry admission are recorded in [APPROVAL.md](../2026_11_13/APPROVAL.md).
The NOT APPROVED section below records the earlier state, retained for audit.

Scope agreed by the user: event/report dates from 2026-01-01; earlier dates
explicitly unsupported. Private-enterprise payroll uses civil sections 1–9.
Military section 0 is outside this delivery. The start of supported coverage
is not asserted to be the original legal introduction date of a profession.

## Evidence and content reconciliation

The official Ministry workbook of 28 March 2023 has 9,119 entries in each of
Appendices A and B. Every Appendix A row was compared with the original XLS.
Its one A/B spelling discrepancy (2144.1, missing hyphen in Appendix B) is
resolved by amendment 11, PDF page 14. All 85 amendment-11 operations are
confirmed in the consolidated workbook: old names absent, new names present.

Nine primary PDFs were downloaded again from me.gov.ua on 12 November 2026.
Amendments 11–17 exactly match the previously acquired files, byte for byte.
The separately acquired signed orders 3073 and 4946 are also retained. URLs
and SHA-256 values are in sources.json; all reconciliation inputs are bound
by reconciliation_inputs.json. Hashes establish integrity, not legal approval.

| Applied amendment | Civil operations in A / B | Resulting entries |
| --- | ---: | ---: |
| 12 | 50 / 50 | 9,138 |
| 13 | 62 / 62 | 9,171 |
| 14 | 169 / 169 | 9,180 |
| 16 | 158 / 158 | 9,145 |
| 17 | 17 / 17 | 9,151 |

Amendment 15 introduces military section 0. Amendment 16's separate military
section begins on PDF page 19; those rows are not civil-profession amendments.

The original PDF tables, including page numbers and bounding boxes, are
retained beside the derived operations. The extraction script reproduces both
appendices, and tests compare the extracted operations with their pinned files.
No missing-old-name or active-target conflict remains in the civil sequence.

Appendix A is retained as the source spelling. Comparison normalizes whitespace,
apostrophe variants, dash spacing, and the abbreviation «т. ін.» without changing
the output spelling. Reviewed differences in amendment 14:

- PDF page 6: table extraction split «Державний уповноважений» across cells.
  Reassembled from the original page and corroborated by Appendix B.
- A removes «Слідчий в особливо важливих справах»; B says «важких». A matches the
  existing consolidated identity; both original readings are retained in the audit.
- Dash spacing differs in the new title of the head of the anti-corruption
  prosecutor's office. The Appendix A spelling is retained.

Amendment 17 PDF page 3 explicitly replaces two distinct 1229.3 titles with
one «Керуючий (секретар) виконавчого комітету (апарату)». The atomic amendment
algorithm accepts this specific kind of many-to-one replacement, but rejects
repeated source identities, duplicate additions, or collisions with unchanged
professions. Multiple professions sharing one code remain separate identities.

## Normative status: NOT APPROVED

Content reconciliation is not dated production authorization. The signed order
4946 is dated 14 April 2026, while the annex's commencement field is unfilled.
The reviewed primary order does not specify a separate commencement clause.
Secondary explanations generally give 14 April, but at least one gives a
registration/publication condition. They are not substituted for a verified
applicable primary rule. No guessed commencement date is added to the registry.

Primary order: https://me.gov.ua/download/f9bb84e1-0f94-426f-a354-83c5b4387d73/file.pdf
Primary annex: https://me.gov.ua/download/26517981-207f-4d5a-8bd7-a8372288873c/file.pdf
Classifier development procedure: https://zakon.rada.gov.ua/laws/show/z0124-18

Corroboration examined, not treated as independent primary approval:
https://prokadry.com.ua/article/19086-zmina-17-do-klasyfikatora-profesii-shcho-onovylo-minekonomiky-ta-iak-diiaty-kadrovyku
https://profpressa.com/reference-works/zmini-v-zakonodavstvi-2026
Conflicting explanation:
https://oblikbudget.com.ua/news/118414-zmina-17-do-klasyfikatora-profesii-analiz-zmin-u-tablytsiakh

The production classifier and form-field mapping approval registries remain
unchanged. Neither this audit nor its candidate JSON authorizes statutory rows.
Remaining work: establish the applicable commencement evidence, confirm the
complete current amendment chain, then pin dated production entries and verify
the actual production import/write path. This does not close all of block 13.

## Reproduction

`python scripts/reconcile_d5_2026_classifier.py`

No database connection is used. Unit tests cover the whole pinned content chain,
atomic operations, source corruption, and the 2026 support boundary. Separate
PostgreSQL regressions use a new temporary cluster, not the ERP production DB.

Final validation on 12 November 2026: **475 passed**, including 27 PostgreSQL
scenarios in a newly initialized isolated cluster. Five existing scenarios used
2025 seed dates; their events were moved into the agreed 2026 support period
while keeping all original negative-path assertions. Separate tests reject
pre-2026 event/report dates. Existing UTC deprecation warnings remain unrelated.
No production migration or change of ERP database privileges was performed.
