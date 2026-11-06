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

## Sources

- Ministry workbook: https://me.gov.ua/download/47467434-c638-46d8-a4ca-f6924f6acf11/file.xlsx
- Amendment 3 scan: https://onu.edu.ua/pub/bank/userfiles/files/nauk%20method%20rada/new_classificator.pdf
- Order 1328 copy: https://csm.kiev.ua/images/stories/2016/document/nakazmert_1328_2016.doc
- Order 241 copy: https://www.zakon.cc/law/document/read/z0514-20
- Official corroboration: https://od.tax.gov.ua/media-ark/news-ark/424224.html

The last source was read through the web tool; direct byte download returned 403.
No local official HTML snapshot or hash is claimed for it.
