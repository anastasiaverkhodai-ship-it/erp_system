"""D5 empty inventory delivery gate; isolated copy, no filing approval."""
from scripts import run_phase13_statutory_gate as gate

SCOPE = (
    'app/services/payroll_d5_period_coverage.py',
    'app/services/payroll_d5_event_sources.py',
    'tests/hr/test_payroll_d5_empty_period_postgresql.py',
    'scripts/run_phase13_d5_empty_gate.py',
    'docs/phase13_d5_empty_period_acceptance.md',
    'docs/phase13_d5_period_coverage.md',
)

if __name__ == '__main__':
    gate.SCOPE = SCOPE
    gate.TESTS += (
        'test_payroll_d5_period_coverage',
        'test_payroll_d5_period_coverage_postgresql',
        'test_payroll_d5_empty_period_postgresql',
        'test_payroll_d5_event_sources_postgresql',
        'test_payroll_d5_verified_review_snapshot_postgresql',
        'test_payroll_d5_regulatory_review_bridge_postgresql',
    )
    raise SystemExit(gate.main())
