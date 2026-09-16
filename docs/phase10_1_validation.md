# 10.1 — Перевірки аудиту

Дата: 2026-09-16. Код, який перевірявся: `ca821db81492defa38592fff8f8689b9ba7c0c0d`.
У checkpoint додано аудит і read-only reproducer; production-код не змінено.

## Попередній regression baseline

```sh
RUN_POSTGRES_E2E=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --tb=short
```

Результат: **3058 passed, 1273 warnings in 76.07s**, exit 0.
Помилок і пропусків немає. Попередження включають застарілі datetime.utcnow
та TestClient/httpx API; вони не приховувалися.

Цей результат не підтверджує правильність нових acceptance-сценаріїв 10.1.
Зокрема, старі тести першої події містять неправильне очікування для
перекриття часткових подій — див. P10-01.

## Незалежний приклад P10-01

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m scripts.audit_phase10_first_event
```

Фактичний результат:

```text
output payment_first=True: actual base=100.00 VAT=20.00; expected base=70.00 VAT=14.00; DEFECT P10-01
output payment_first=False: actual base=100.00 VAT=20.00; expected base=70.00 VAT=14.00; DEFECT P10-01
input payment_first=True: actual base=100.00 VAT=20.00; expected base=70.00 VAT=14.00; DEFECT P10-01
input payment_first=False: actual base=100.00 VAT=20.00; expected base=70.00 VAT=14.00; DEFECT P10-01
```

**Exit 1: дефект відтворено, не виправлено.** Скрипт не включено в pytest
як «успішний тест», він не потребує підключення до БД і не змінює дані.
INPUT використовує достатній доказ як верхню межу для ізоляції економічного
розрахунку. Це не повний юридичний сценарій видачі ПН на аванс.

## База та міграції

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m alembic current
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m alembic heads
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m alembic check
```

- DB current = code head = `d1f8b4c6e935`.
- `No new upgrade operations detected.`
- Нової міграції немає; upgrade/downgrade у цьому checkpoint не виконувалися.
- `git diff --check`: PASS.

## Підсумок

10.1 завершено: фактичний baseline відомий, P10-01 має відтворення,
прогалини й критерії приймання описано в [аудиті](phase10_1_audit.md).
Повний блок 10 залишається відкритим. Наступний запланований етап — 10.2;
P10-01 обов'язково виправити в 10.3 до приймання податкових результатів.
