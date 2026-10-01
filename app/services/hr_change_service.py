from decimal import Decimal
from datetime import date, datetime
from enum import Enum
from sqlalchemy import inspect, select
from app.models.hr_change import HRChange


def snapshot(row):
    result = {}
    for column in inspect(type(row)).columns:
        if column.name in ('created_at', 'updated_at'):
            continue
        value = getattr(row, column.name)
        if isinstance(value, Enum):
            value = value.value
        elif isinstance(value, (date, datetime)):
            value = value.isoformat()
        elif isinstance(value, Decimal):
            value = str(value)
        result[column.name] = value
    return result


async def record_change(db, row, entity_type, before, changed_by):
    after = snapshot(row)
    if before != after:
        db.add(HRChange(company_id=row.company_id, entity_type=entity_type,
            entity_id=row.id, before_state=before, after_state=after, changed_by=changed_by))
        await db.flush()


async def list_changes(db, company_id, entity_type, entity_id):
    return list((await db.scalars(select(HRChange).where(HRChange.company_id == company_id,
        HRChange.entity_type == entity_type, HRChange.entity_id == entity_id).order_by(HRChange.id))).all())
