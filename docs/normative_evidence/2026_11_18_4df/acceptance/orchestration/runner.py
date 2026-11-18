import os, sys, json, hashlib, subprocess, tempfile
from pathlib import Path
import xml.etree.ElementTree as ET
root=Path('/Users/anastasia/Projects/erp_system'); os.chdir(root);sys.path.insert(0,str(root))
from scripts.run_phase13_bank_batch_gate import source_fingerprint
out=Path('/private/tmp/erp_1314_orchestration');print('EVIDENCE =',out,flush=True)
before=json.loads(Path('/private/tmp/erp_live_period_before.json').read_text())
allowed={'tests/hr/test_payroll_paid_income_live_period_postgresql.py'}
protected={k:v for k,v in before.items() if k not in allowed}
env={**os.environ,'PYTHONPATH':str(root),'PYTHONDONTWRITEBYTECODE':'1'}
summary={'phase13':'OPEN','official_generation':'BLOCKED','gates':[]}
source_before=source_fingerprint()
try:
 with (out/'normative.log').open('w') as log:
  rc=subprocess.run([sys.executable,'scripts/audit_payroll_reference_sources.py'],env=env,stdout=log,stderr=subprocess.STDOUT).returncode
 summary['normative_audit_exit']=rc
 if rc: raise RuntimeError('STOP: normative audit')
 old=root/'docs/normative_evidence/2026_11_18_4df/acceptance/resume'
 def modules(folder):
  return sorted({t.get('classname').replace('.','/')+'.py' for t in ET.parse(old/(folder+'.xml')).iter('testcase')})
 groups=[('mixed_attribution', ['tests/hr/test_payroll_paid_income_mixed_components_postgresql.py', 'tests/hr/test_phase13_paid_income_component_negative.py', 'tests/hr/test_phase13_paid_income_delivery_guards.py', 'tests/hr/test_payroll_regulatory_4df_real_split_payment_postgresql.py', 'tests/hr/test_payroll_regulatory_4df_split_writer_postgresql.py'])]
 groups += [('multi_revision', ['tests/hr/test_payroll_correction_multirevision_delta.py', 'tests/hr/test_payroll_correction_multirevision_snapshot.py', 'tests/hr/test_payroll_regulatory_correction_projection.py', 'tests/hr/test_payroll_regulatory_correction_snapshot_store.py', 'tests/hr/test_payroll_correction_multirevision_postgresql.py', 'tests/hr/test_payroll_regulatory_correction_delta.py', 'tests/hr/test_payroll_regulatory_revision_chain_review.py', 'tests/hr/test_payroll_regulatory_correction_snapshot_store_postgresql.py', 'tests/hr/test_payroll_paid_income_mixed_components_postgresql.py']), ('live_period',['tests/hr/test_payroll_paid_income_live_period_postgresql.py']),('baseline',modules('baseline')),('mixed_corrections',modules('mixed_corrections'))]
 groups.insert(0, ('snapshot_concurrency', ['tests/hr/test_payroll_correction_snapshot_concurrency_postgresql.py']))
 (out/'modules.json').write_text(json.dumps(groups,indent=2))
 for name,paths in groups:
  print('START',name,flush=True)
  with (out/(name+'.log')).open('w') as log:
   rc=subprocess.run([sys.executable,'-m','scripts.run_postgresql_regression_copy','--upgrade-copy',*paths,'-x','--junitxml='+str(out/(name+'.xml'))],env=env,stdout=log,stderr=subprocess.STDOUT).returncode
  counts=dict(tests=0,failures=0,errors=0,skipped=0)
  if (out/(name+'.xml')).exists():
   for s in ET.parse(out/(name+'.xml')).iter('testsuite'):
    for k in counts: counts[k]+=int(s.get(k,0))
  output=(out/(name+'.log')).read_text(); checks={marker: marker in output for marker in ('No new upgrade operations detected.', 'SOURCE ALEMBIC REVISION UNCHANGED = PASS', 'ISOLATED DATABASE REMOVED = PASS')}; summary['gates'].append(dict(name=name,exit=rc,checks=checks,**counts)); print(summary['gates'][-1],flush=True)
  if rc or not all(checks.values()) or not counts['tests'] or any(counts[k] for k in ('failures','errors','skipped')):raise RuntimeError('STOP: '+name)
 summary['status']='PASS'
except Exception as exc:
 summary['status']='FAIL';summary['stop_reason']=str(exc)
finally:
 summary['production_sha256_before']=source_before
 summary['production_sha256_after']=source_fingerprint()
 summary['production_unchanged']=summary['production_sha256_before']==summary['production_sha256_after']
 summary['protected_files']=len(protected)
 start=json.loads((out/'start_files.json').read_text())
 summary['start_files_checked']=len(start)
 summary['changed_existing_files']=[p for p,h in start.items() if not (root/p).is_file() or hashlib.sha256((root/p).read_bytes()).hexdigest()!=h]
 summary['unrelated_worktree_preserved']=all((root/k).is_file() and hashlib.sha256((root/k).read_bytes()).hexdigest()==v for k,v in protected.items())
 if summary['changed_existing_files'] or not summary['production_unchanged'] or not summary['unrelated_worktree_preserved']:summary['status']='FAIL'
 (out/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2),flush=True)
sys.exit(0 if summary['status']=='PASS' else 1)
