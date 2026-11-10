# Reference verification — 2026-11-06

Status: **PARTIAL; not a completion certificate for item 1 or block 13.**

## Verified this delivery

- The Ministry's linked working XLSX was downloaded successfully. Its A and B
  indices each contain 9,102 unique code/name pairs. The complete pair sets agree;
  codes pass syntax checks. The workbook has no appendix V, and its modification
  metadata is 2021-11-05, not proof of a current 2026 edition or legal validity dates.
- The university-hosted scan of amendment 3 contains the order, cover and all nine
  declared annex pages. All 11 PDF pages were visually reviewed. Both order and
  annex state commencement on 2015-10-01. This removes the missing-full-scan issue
  for amendment 3, but is not approval of a transcribed historical classifier.
- Income code 150's replacement starts on 2020-07-01. The copied act, order 241,
  explicitly gives that date; the Odesa tax authority's publication independently
  confirms the change to code 150 from that date. The original raw table remains
  unchanged; the reviewed correction is recorded separately.
- Applying the four documented date resolutions to 158 income rows / 112 codes
  leaves no reversed or overlapping intervals. This checks interval consistency,
  not every label, legal reference, or the completeness of the current dictionary.
- The standardization centre's copy of order 1328 confirms general commencement
  on 2016-08-15 and separate commencement for the two private-enforcement
  professions. It does **not** include the amendment 5 annex.

## Reproduction

Run `python scripts/audit_payroll_reference_sources.py` from the repository.
It verifies source hashes, both full classifier indices, uniqueness, code syntax,
and all income intervals after the explicitly recorded resolutions.
`docs/normative_evidence/2026_11_06/review.json` records exact review limits.

## Remaining before approval

Complete and reconcile the base/appendix V and amendment 4/5 content; establish
the dated profession history through all applicable amendments; finish semantic
verification of the other annex dictionaries. The Ministry working spreadsheet
cannot silently replace this dated history. Registry approvals remain unchanged.
No database, migration, or financial calculation was changed in this delivery.

## Subsequent recovery in the same review

The full amendment 5 PDF was recovered from the Internet Archive capture of
the Ministry of Social Policy file (2022-01-21 14:15:48). All seven PDF pages
were visually reviewed: signed order, cover, and five declared annex pages.
The missing-annex issue for amendment 5 is resolved. General commencement is
2016-08-15. The two private-enforcement professions commence 2016-10-05:
clause 3 refers to commencement of law 1403-VIII, independently corroborated by
the Commercial Court of Ternopil's publication of 2016-10-26. This is distinct
from the later introduction of provisions governing private enforcement activity.

A complete publisher DOCX of amendment 4 was also recovered. All seven tables
were extracted; appendix A and B agree on nine cancellations, five replacements,
and twelve additions. However, the order says 2016-03-01 while the annex says
2016-04-01 and retains blank approval fields. Full text availability is now
resolved; authoritative commencement reconciliation is **not** resolved.
No guessed date was entered into the trusted classifier.

Thus the earlier missing-file statements above describe the initial review,
not the latest acquisition state. The remaining blockers are dated-history
verification, the amendment 4 discrepancy, base/appendix V provenance, and
semantic verification of the other dictionaries.

Recovered sources:

- https://web.archive.org/web/20220121141548id_/https://www.msp.gov.ua/files/5.pdf
- https://vk24.ua/sites/default/files/npa/nakaz-394.docx
- https://www.zakon.cc/law/document/read/1403-19
- https://te.arbitr.gov.ua/sud5022/pres-centr/news/302671/

## Sources

- Ministry workbook: https://me.gov.ua/download/47467434-c638-46d8-a4ca-f6924f6acf11/file.xlsx
- Amendment 3 scan: https://onu.edu.ua/pub/bank/userfiles/files/nauk%20method%20rada/new_classificator.pdf
- Order 1328 copy: https://csm.kiev.ua/images/stories/2016/document/nakazmert_1328_2016.doc
- Order 241 copy: https://www.zakon.cc/law/document/read/z0514-20
- Official corroboration: https://od.tax.gov.ua/media-ark/news-ark/424224.html

The last source was read through the web tool; direct byte download returned 403.
No local official HTML snapshot or hash is claimed for it.

## Follow-up review — 2026-11-10

The amendment 4 commencement discrepancy is resolved from clause 1 of the
archived official Rada order: 2016-03-01. Its exact bytes and digest are retained
in `normative_evidence/2026_11_06/sources.json`. The publisher annex's April date
is retained as a discrepancy, not used as a second commencement date.

Original Rada-linked Word attachments A and B/V were recovered from archived
2018 responses. They are pinned in `normative_evidence/2026_11_09/base_sources.json`.
Acquisition does not establish their edition or a complete effective-date history.
Table extraction of A contains 8,742 rows, including six malformed codes; see
`base_content_review.json`. No guessed corrections or current validity dates
were imported. Full historical reconstruction and form mappings remain unapproved.

The importer incorrectly grouped overlap checks by code alone. It now checks
the code/name identity, allowing different professions in the same classification
group while still rejecting overlapping versions of the same identity.
Validation: 53 classifier/guard/normative-admission tests passed.
