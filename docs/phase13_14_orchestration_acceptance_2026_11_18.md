# Phase 13.14 orchestration acceptance — 2026-11-18

**Technical checkpoint PASS. Phase 13 OPEN; final 13.15 NOT STARTED.**
APPROVED_MAPPINGS=0; official statutory generation BLOCKED.
Base branch `main`, base HEAD `49f8e26d32930d5711e88d561657b7092cc62a0c`.
This report supersedes the remaining concurrency-coverage limitations in the
previous live-period acceptance only for the explicit scenarios below.
It does not supersede the authoritative overall Phase 13 acceptance baseline.

## Exact execution counters

| Gate | PASS | FAIL | ERROR | SKIP |
|---|---:|---:|---:|---:|
| snapshot_concurrency | 16 | 0 | 0 | 0 |
| mixed_attribution | 38 | 0 | 0 | 0 |
| multi_revision | 82 | 0 | 0 | 0 |
| live_period | 4 | 0 | 0 | 0 |
| baseline | 227 | 0 | 0 | 0 |
| mixed_corrections | 124 | 0 | 0 | 0 |
| category_attribution | 4 | 0 | 0 | 0 |
| clean_commit_candidate | 48 | 0 | 0 | 0 |
| **Total executions** | **543** | **0** | **0** | **0** |

There are **412 distinct test IDs**. The first seven gates have 495 executions;
the isolated commit candidate repeats 48 affected tests. Overlap is intentional;
543 is not a count of unique tests. Existing 475/475 evidence is historical and
is not added again to these counters. No failures were repaired or hidden in this
pass. No test was skipped. Pytest warnings remain visible in the retained logs.

- Eight disposable copy upgrades + Alembic schema checks: 8/8 PASS.
- Eight source Alembic revision checks: 8/8 PASS.
- Eight disposable database cleanups: 8/8 PASS.
- Existing normative source audit: 2/2 exit 0; this is not mapping approval.
- Production normalized full logical dump SHA256 before/after each package:
  `3a37fbbac88d48308d0a9ea8944db632424bac483e9ec4f3faf8b5c2c9de429c`.
- Protected original files: 119/119 unchanged.
- All 2,636 start-of-pass files were byte-identical after execution, before the
  intentional documentation appendices. No pre-existing source file was edited.
- Clean candidate: `git archive` of base HEAD plus exactly five inherited service
  changes and seven test files; no other dirty worktree changes were copied.
  The 48 affected tests pass without the unrelated readiness/audit modifications.
  Private configuration and the disposable candidate directory were removed.

### Scope of this orchestration pass

No additional production-code or database-schema edits were needed in this pass.
The five inherited production changes were reviewed and tested: refreshed live
period provenance; immediate-predecessor correction delta; v2 sealed history;
company serialization before snapshot reads/insertion; predecessor exposure.
The existing uncommitted changes are retained; no guard was weakened.

Two new test modules extend acceptance:

- 16 real PostgreSQL cases (D1/4DF × positive/negative delta × four interleavings).
  Both initial snapshot requests are proven blocked by PostgreSQL before release;
  exactly one inserts. Writer-first snapshots use the committed new revision.
  Reader-first snapshots retain the old revision unchanged before a separate v2
  snapshot is appended. A committed revision missing dated statutory evidence
  rejects snapshot insertion. Server-side contention is observed with
  `pg_blocking_pids`; timing alone is not the assertion.
- Four committed financial attribution cases: salary + leave compensation,
  salary + compensation + documented severance, vacation paid in the same month,
  and vacation paid next month. They verify source IDs, exact gross totals,
  replay without duplicate payment, fresh-session read-back, and foreign-company
  rejection. The next-month vacation case remains INCOMPLETE_SOURCE_DATA.
  Compensation is produced by the existing termination/leave services; severance
  is an existing documented-source fixture, and vacation uses the existing
  source fixture. These are not five-category statutory approval tests or a new
  independent validation of the vacation/severance average-earnings formulas.

The earlier positive/negative multi-revision and mixed-income E2E were rerun,
including immutable UPDATE/DELETE rejection, committed v1/v2 hash retention,
payment reversal/re-payment, source-slice conservation and unsupported month
classification. October/November remain genuine fixture accounting periods.

### Gap decisions after audit

| Area | Decision / residual boundary |
|---|---|
| Single-contract multi-revision financial delta and v2 snapshot | Supported scope verified; immediate predecessor is the baseline, older history is retained but not subtracted again |
| Concurrent first insert and correction-writer/snapshot-reader | Four specified interleavings covered for both delta signs and both forms; not a proof of every possible database isolation level or all writers |
| Five-category financial source attribution | Concrete salary/sick/vacation/compensation/severance fixtures covered; legal income codes and exceptional contexts remain unapproved |
| Live period and tenant provenance | Refreshed persisted period and negative identity checks retained |
| Immutability/idempotency | Existing database guards and sealed snapshots retained and tested; no trigger bypass or deletion of immutable public rows |
| Multiple contracts in one aggregate correction snapshot | Still rejected by the one-original/one-replacement envelope. No aggregate storage contract introduced; this extension is not claimed complete |
| Official corrections | Accepted filing/row identity and regulator acceptance are still required; internal financial delta is not an official correction row |
| D1/4DF/D5/D6 statutory integration | Blocked on current effective-dated normative mappings and independent approval; D5 trusted classification / D6 applicability remain separate acceptance work |
| Production deployment, reader credentials, owner separation | Not changed or certified by the disposable-copy tests |

The `accrual_month` carried by current component evidence is a technical source
month derived from existing payroll/dating sources. It does not independently
establish the accounting accrual month required for every official 4DF case.
No default income code, payroll-period substitution or guessed effective date was
introduced. The authoritative `phase13_final_acceptance.md` requirement 13.14
remains open; this package is a safe intermediate technical checkpoint.


## Evidence and local commit boundary

Complete logs, JUnit XML, exact module lists, source-scope hashes, summaries,
preservation manifests and the executed runners are retained under
`docs/normative_evidence/2026_11_18_4df/acceptance/orchestration/`.
`candidate/scope.json` defines the twelve source/test files in the technical
commit. The commit also includes this report and the evidence directory.
The two older working reports are appended locally and remain outside this
technical commit, alongside all unrelated pre-existing work.

The full main-worktree package was tested as 495 executions; only the 48 affected
tests were repeated in the clean candidate. This does not claim a full clean-tree
Phase 13 regression. There are no new migrations or production database writes.

Current user authorization permits this separate safe local checkpoint after
PASS. Push and remote verification have not been performed and require permission.
Closure still requires unresolved normative evidence/approval, statutory field
integration, accepted-filing correction provenance, and final 13.15 acceptance.


## Orchestration audit — 18.11.2026: чинність, категорії та межі доказів

APPROVED_MAPPINGS=0; цей додаток не надає незалежного погодження. Перевірка
веб-тексту, отримання оригінальних байтів і затвердження mapping — три окремі стани.

### Уточнений ланцюжок редакцій

Перелік актів для перевірки доповнено **наказом №284 від 26.05.2026**
(реєстрація 29.05.2026 №780/46174), який змінює наказ №243 від 07.05.2026
(реєстрація 26.05.2026 №751/46145). Офіційне повідомлення ДПС від 22.07.2026
вказує публікацію обох наказів 17.06.2026 в Офіційному віснику №49:
[ДПС, ланцюжок змін](https://lg.tax.gov.ua/media-ark/news-ark/1032588.html).
Це підтвердження реквізитів у роз'ясненні, а не незалежна звірка всіх положень
оригіналів та додатків. Не можна затверджувати редакцію, перевіривши лише №243.

Для ЮО роз'яснення ДПС підтверджує чинність з 17.07.2026 та перший звітний місяць
липень 2026: [офіційне роз'яснення](https://ck.tax.gov.ua/media-ark/news-ark/print-1040327.html).
Дата публікації, дата чинності та перший звітний період залишаються різними полями.
Поточна [відповідь ЗІР 44686](https://zir.tax.gov.ua/main/bz/view/?id=44686&src=ques)
стосується ФОП/НПД та ФІЗ-4ДФ; вона не є самостійним підтвердженням колонок
або періодичності форми ЮО. Вона також вимагає чинної на день подання форми для
уточнення. Внутрішній v2 financial snapshot не містить прийняття декларації ДПС.

Повні оригінали ПКУ, Інструкції №5, консолідованого Порядку №4, наказів №243
і №284 не отримані. Відсутність пізніших змін станом на 18.11.2026 не доведена.
Жодних нових effective_from/effective_to для executable mappings не встановлено.

### Перевірка п'яти категорій

| Категорія | Перевірений матеріал / межа | Що не дозволено виводити автоматично |
|---|---|---|
| salary | Поточний [ЗІР 44723](https://zir.tax.gov.ua/main/bz/view/?id=44723&src=ques) підтримує кандидат 101 для трудового договору, у т.ч. сумісництва | ЦПД не можна підвести під зарплату; початок історичної дії mapping не встановлено |
| sick pay | [ДПС 05.10.2026](https://svp.tax.gov.ua/media-ark/news-ark/print-1040423.html) підтримує кандидат 101 та розрізняє допомогу/перші п'ять днів і податкові періоди | Належність/неналежність до фонду оплати праці сама по собі не визначає код 4ДФ; maternity і післязвільнювальні випадки не покриті |
| vacation | Знайдено [лист ДФС 12.12.2016](https://rv.tax.gov.ua/baner/podatkovi-konsultatsii/konsultatsii-dlya-fizichnih-osib/70545.html), що прямо називає 101 для відпускних у старій 1ДФ | Історичний лист не доводить чинну редакцію 4ДФ 2026 або next-month exception. Поточне пряме категорійне підтвердження залишається OPEN |
| severance | [ДПС 29.09.2026](https://zp.tax.gov.ua/media-ark/news-ark/1054474.html) підтримує кандидат 127 для вихідної допомоги при звільненні | Фінансовий захист суми та її податкова категорія не затверджують statutory projection і місяць виплати |
| leave compensation | [ДПС 24.09.2026](https://zak.tax.gov.ua/media-ark/news-ark/1053206.html) у висновку обмежує 127 нарахуванням у наступних місяцях після звільнення | Вступ згадує також виплату; його не можна використати для перекласифікації раніше нарахованої суми лише через пізню оплату. Нарахування при звільненні та пізня виплата потребують окремого поточного підтвердження |

У [ЗІР 44664](https://zir.tax.gov.ua/main/bz/view/?id=44664&src=ques) окремо
перевірено графи нарахованого доходу, виплаченого gross і перерахованих податків.
Зарплатний виняток наступного місяця має умову дотримання строків. Звідси технічна
вимога (висновок аудиту): не використовувати `payment_date > termination_date`
як достатню умову 127; потрібні підтверджене нарахування та його дата, дата
звільнення, статус договору, вид доходу, строк виплати, тип податкового агента
і чинна форма. Для неповного набору доказів залишати результат непідтвердженим.

### Отримання та збереження

Виконано 14 прямих запитів до офіційних URL: **0 отриманих оригіналів,
14 FETCH_UNAVAILABLE** (помилки/тайм-аути зафіксовані в manifest). Це обмеження
доступу в цьому запуску, не доказ відсутності актів. Старі збережені HTML не
перезаписувалися. Веб-тексти офіційних роз'яснень були доступні окремим каналом;
їх короткі висновки наведені вище, без позначення оригіналів CONTENT_VERIFIED.
Manifest: `docs/normative_evidence/2026_11_18_4df/acceptance/orchestration/normative/manifest.json`.

Залишаються OPEN: повні чинні акти з додатками та історією; поточні vacation/
leave-compensation підстави; межі застосування next-month винятку; спеціальні
режими; незалежний reviewer і прийняті декларації для офіційних виправлень.
Фінансові E2E не змінюють ці статуси. Phase 13 OPEN; final 13.15 NOT STARTED.

### Додаткова перевірка первинних PDF Мінфіну

Після запитів до ВРУ знайдено альтернативний офіційний каталог:
[накази Мінфіну за 2026 рік](https://mof.gov.ua/uk/orders_of_the_Ministry_of_Finance_of_Ukraine_in_2026.).
Перевірено веб-витяг повного основного тексту трьох PDF:

- [Наказ №243, 4 сторінки](https://mof.gov.ua/storage/files/%D0%9D%D0%B0%D0%BA%D0%B0%D0%B7%20243.pdf): пункт 2 викладає форму та Порядок у новій редакції; пункт 4.1 прив'язує перше подання ЮО до місяця набрання чинності; пункт 6 встановлює 30 днів після офіційної публікації. Основний текст посилається на окремі додатки і не замінює їх.
- [Наказ №284, 3 сторінки](https://mof.gov.ua/storage/files/%D0%9D%D0%B0%D0%BA%D0%B0%D0%B7%20284.pdf): окремо змінює №243, форми ЮО/ФОП та затверджує зміни до Порядку; пункт 6 також визначає 30 днів від публікації. Не можна вважати цей файл повним консолідованим Порядком.
- [Зміни до Порядку, 3 сторінки](https://mof.gov.ua/storage/files/%D0%97%D0%BC%D1%96%D0%BD%D0%B8%20%D0%BD%D0%B0%D0%BA%D0%B0%D0%B7%20284.pdf): містять зміни посилань і формулювань у розділах I–III. Виявлено **невирішену розбіжність метаданих**: витяг грифа містить 27.05.2026, тоді як офіційний каталог та повідомлення ДПС називають 26.05.2026. Не виправляти дату здогадкою; потрібні візуальна звірка оригіналу та зареєстрована редакція ВРУ. Зображення сторінки не вдалося отримати для локальної перевірки.

Ще 4 прямі запити (3 PDF та каталог) завершилися FETCH_UNAVAILABLE.
Разом пряме отримання цього проходу: **18 спроб / 0 збережених оригіналів / 18 недоступних**.
Це не скасовує доступність тексту PDF через веб-витяг. Окремий manifest:
`.../orchestration/normative/ministry/manifest.json`.
Поточна повна консолідація Порядку, його довідники, ПКУ та Інструкція №5,
історичні інтервали й незалежне погодження все ще не підтверджені.
