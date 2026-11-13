# Civil profession registry admission — 13 November 2026

## Decision and scope

Admit the reviewed civil-profession dataset for ERP event dates from 2026-01-01,
with amendment 17 applied from **2026-04-14**. This is a source-controlled ERP
implementation review, not a certificate or ruling issued by a public authority.
It approves profession identities and their supported intervals only. It does not
approve D5 field mappings, tax calculations, official XML, signing or submission.

The beginning of baseline coverage, 2026-01-01, is not the original introduction
date of the professions. Older events are explicitly rejected. Military section 0
is outside the agreed private-enterprise payroll scope.

## Commencement evidence and the previous blocker

Primary evidence is the signed Ministry order 4946 of 14 April 2026 and its final
amendment 17 annex, already pinned in the 2026_11_12 evidence directory:

- https://me.gov.ua/download/f9bb84e1-0f94-426f-a354-83c5b4387d73/file.pdf
- https://me.gov.ua/download/26517981-207f-4d5a-8bd7-a8372288873c/file.pdf

The original annex's commencement field is unfilled. This review does **not**
claim that an explicit commencement date was found in that field, and does not
modify the original. The operational date is a corroborated interpretation of
the primary order, supported by independent legal/payroll publications:

1. «Професійні видання», 2026 legislative changes table, Ministry April section:
   order 4946 is listed as effective from 14.04.2026.
   https://profpressa.com/reference-works/zmini-v-zakonodavstvi-2026
2. «Планета-інформ», 5 May 2026: distinguishes web publication on 27 April from
   commencement on the day of issue. Exact short excerpt:
   «Наказ № 4946 набрав чинності з дня його видання».
   https://planeta-inform.com.ua/news/the-ministry-of-economy-has-made-changes-to-the-classification-of-occupations/
3. LIGA:ZAKON classifier metadata identifies the current edition as 14.04.2026.
   This metadata was available through indexed search; the direct page renderer
   did not expose its text, so it is supplementary corroboration, not sole proof.
   https://ips.ligazakon.net/document/FIN5940J
4. Prokadry's amendment-17 explanation also identifies application from signature,
   14.04.2026.
   https://prokadry.com.ua/article/19086-zmina-17-do-klasyfikatora-profesii-shcho-onovylo-minekonomiky-ta-iak-diiaty-kadrovyku

The contrary Obликбюджет news item makes a generic registration/publication claim
without identifying a registration number or an alternative calendar date. It is
retained as a conflicting secondary statement, but is not used to replace the
specific date independently corroborated above:
https://oblikbudget.com.ua/news/118414-zmina-17-do-klasyfikatora-profesii-analiz-zmin-u-tablytsiakh

Thus the earlier requirement for a filled annex date is superseded by this
explicit multi-source review. This is not a newly found Ministry clarification.
A later authoritative clarification would require a new reviewed registry version;
previous employee evidence snapshots must not be silently rewritten.

The Ministry's current classifier search lists amendment 17 as its latest final
amendment; draft announcements are distinct from final changes:
https://me.gov.ua/Search/Result?lang=uk-UA&pageNumber=1&query=%D0%94%D0%9A+003%3A2010
Review horizon: 2026-11-13; this is not a guarantee about future amendments.

## Data and runtime admission

The complete content review is in ../2026_11_12/REVIEW.md. Starting from 9,145
civil professions, amendment 17 closes nine old identities at 2026-04-13 and
opens fifteen identities at 2026-04-14. Two old 1229.3 titles explicitly merge
into one target. The dataset has **9,160 intervals; 9,151 current professions**.

- File: app/data/d5_civil_classifier_2026.json
- Version: civil-2026-a17-20261112 (identifies the source-content build)
- SHA-256: 5c08794bcd7212cf8baaee5bbdb6f9642b5654868b0e261bdfa438f0b30f4a1c
- Builder: `python -m scripts.build_d5_2026_registry`
- Runtime trust anchor: payroll_d5_trusted_classifier.APPROVED_SOURCES
- Production entry point: create_approved_d5_professional_evidence

The builder does not self-approve its output. The separate runtime hash rejects
changed bytes. Lookup uses both code and exact profession name, not code alone.
The write entry point uses the existing source binding, company/employee/event
checks, idempotency and PostgreSQL immutable-evidence protections. Caller-owned
transactions are preserved. The original generic entry point still rejects
missing classifier evidence and missing source-bound history.

## Acceptance result

13 November 2026: **500 tests passed**, including **30 real PostgreSQL cases**
in the isolated temporary cluster. The new production-registry E2E cases use
actual approved bytes (no monkeypatched approval), exercise the existing full
HR-event intake, verify repeat-request idempotency, reject company mismatch,
and confirm the actual c048 immutable-evidence trigger rejects updates.

Every one of the 17 amendment operations is checked on both sides of
13/14 April. The full dated dataset is reproducible from pinned content inputs.
Byte tampering, unknown names, military scope and pre-2026 events are rejected.
Multiple professions under one classification code are checked independently.
All names fit the existing database field size (maximum 208 characters).
No schema change, production migration or production-data write is required.
