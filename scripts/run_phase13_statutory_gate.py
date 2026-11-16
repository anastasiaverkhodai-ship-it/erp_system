"""Statutory provenance gate on a disposable PostgreSQL copy; never enables filing."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

from scripts.run_phase13_bank_batch_gate import source_fingerprint

SCOPE = (
    'app/services/payroll_regulatory_persisted_source_verifier.py',
    'app/services/payroll_regulatory_source_snapshot_service.py',
    'app/services/payroll_regulatory_form_row_writer.py',
    'app/services/payroll_regulatory_dated_4df_service.py',
    'tests/hr/test_payroll_statutory_provenance_postgresql.py',
    'tests/hr/test_payroll_regulatory_4df_split_writer_postgresql.py',
    'tests/hr/test_payroll_regulatory_dated_4df_persistence_postgresql.py',
    'scripts/run_phase13_statutory_gate.py',
    'docs/phase13_statutory_provenance_acceptance.md',
)
TESTS = (
    'test_payroll_statutory_provenance_postgresql',
    'test_payroll_regulatory_form_row_writer',
    'test_payroll_regulatory_source_snapshot_service',
    'test_payroll_regulatory_source_snapshot_postgresql',
    'test_payroll_regulatory_form_fields',
    'test_payroll_regulatory_form_document',
    'test_payroll_regulatory_xsd_contract',
    'test_payroll_regulatory_mapping_registry',
    'test_payroll_regulatory_normative_evidence',
    'test_payroll_regulatory_d1_positive_sources_postgresql',
    'test_payroll_regulatory_4df_real_split_payment_postgresql',
    'test_payroll_regulatory_4df_split_writer_postgresql',
    'test_payroll_regulatory_dated_4df_persistence_postgresql',
    'test_payroll_regulatory_dated_4df',
    'test_payroll_regulatory_4df_d6_acceptance_review_postgresql',
    'test_payroll_regulatory_4df_combined_source_binding_postgresql',
    'test_payroll_d5_normative_readiness_postgresql',
    'test_payroll_regulatory_d5_d6_source_postgresql',
    'test_payroll_paid_income_postgresql',
    'test_payroll_bank_batch_migration_postgresql',
)


def protected_files():
    names = subprocess.check_output(['git','ls-files','--modified','--others','--exclude-standard','-z']).decode().split('\0')
    return {p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
            for p in names if p and p not in SCOPE and Path(p).is_file()}


def main():
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    evidence = Path(tempfile.mkdtemp(prefix='erp_phase13_statutory_gate_'))
    print('EVIDENCE =', evidence, flush=True)
    before = protected_files()
    (evidence/'protected.json').write_text(json.dumps(before, indent=2))
    source_before = source_fingerprint()
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE':'1', 'PYTHONPATH':str(root)}
    with (evidence/'normative_audit.json').open('w') as log:
        audit = subprocess.run([sys.executable,'scripts/audit_payroll_reference_sources.py'], env=env, stdout=log, stderr=subprocess.STDOUT)
    with (evidence/'tests.log').open('w') as log:
        result = subprocess.run([sys.executable,'-m','scripts.run_postgresql_regression_copy',
            '--upgrade-copy', *['tests/hr/'+name+'.py' for name in TESTS],
            '--junitxml='+str(evidence/'tests.xml')], env=env, stdout=log, stderr=subprocess.STDOUT)
    source_after = source_fingerprint()
    counts = dict(tests=0, failures=0, errors=0, skipped=0)
    if (evidence/'tests.xml').exists():
        for suite in ET.parse(evidence/'tests.xml').getroot().iter('testsuite'):
            for key in counts: counts[key] += int(suite.get(key, 0))
    passed = (audit.returncode == result.returncode == 0 and counts['tests'] > 0
              and not any(counts[k] for k in ('failures','errors','skipped'))
              and before == protected_files() and source_before == source_after)
    summary = dict(status='PASS' if passed else 'FAIL', **counts,
        test_exit=result.returncode, normative_audit_exit=audit.returncode,
        protected_files=len(before), unrelated_worktree_preserved=before == protected_files(),
        production_dump_unchanged=source_before == source_after,
        production_sha256_before=source_before, production_sha256_after=source_after,
        official_generation='BLOCKED', phase13='OPEN', full_stage_acceptance=False,
        evidence=str(evidence))
    (evidence/'summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0 if passed else 1

if __name__ == '__main__':
    sys.exit(main())
