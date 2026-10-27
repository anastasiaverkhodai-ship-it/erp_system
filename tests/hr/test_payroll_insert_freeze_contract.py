"""Phase 13.8-E C4-B1: forward migration and lock contracts."""
import ast
import importlib.util
from pathlib import Path
from alembic.config import Config
from alembic.script import ScriptDirectory

PATH = Path('alembic/versions/13d4a7b8c027_payroll_insert_freeze.py')
spec = importlib.util.spec_from_file_location('insert_freeze', PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def test_single_expected_head():
    script = ScriptDirectory.from_config(Config('alembic.ini'))
    assert script.get_heads() == ['13d4a7b8c063']
    assert script.get_revision('13d4a7b8c028').down_revision == '13d4a7b8c027'
    assert migration.down_revision == '13d4a7b8c026'


def test_membership_guard_covers_move_and_all_write_operations(monkeypatch):
    sql = []
    monkeypatch.setattr(migration.op, 'execute', sql.append)
    migration.upgrade()
    joined = '\n'.join(sql)
    assert 'BEFORE INSERT OR UPDATE OR DELETE ON payroll_input_salary_slices' in joined
    assert 'OLD.payroll_input_id' in joined and 'NEW.payroll_input_id' in joined
    assert 'ORDER BY id FOR UPDATE' in joined
    assert 'history_epoch = history_epoch + 1' in joined


def test_legacy_calculations_are_not_assigned_creation_transaction(monkeypatch):
    sql = []
    monkeypatch.setattr(migration.op, 'execute', sql.append)
    migration.upgrade()
    assert 'ALTER TABLE payroll_calculations ADD COLUMN history_creation_xid xid8' in sql
    assert not any('UPDATE payroll_calculations' in item for item in sql)
    assert 'IS DISTINCT FROM pg_current_xact_id()' in migration.LINE_GUARD_SQL
    assert 'NEW.history_creation_xid := pg_current_xact_id()' in migration.CALCULATION_GUARD_SQL


def test_service_locks_before_source_derivation():
    from app.services.payroll_calculation_service import calculate_payroll_input
    import inspect
    source = inspect.getsource(calculate_payroll_input)
    assert source.index('.with_for_update()') < source.index('await validate_calculation_source')


def test_transaction_ownership():
    from app.services.payroll_calculation_service import calculate_payroll_input
    import inspect
    for source in (PATH.read_text(), inspect.getsource(calculate_payroll_input)):
        calls = [node.func.attr for node in ast.walk(ast.parse(source))
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
        assert 'commit' not in calls
        assert 'rollback' not in calls


def test_insert_freeze_does_not_grant_historical_provenance():
    from app.services.payroll_correction_source_evidence_service import capture_payroll_correction_source
    import inspect
    assert '"source_evidence_complete": False' in inspect.getsource(capture_payroll_correction_source)
