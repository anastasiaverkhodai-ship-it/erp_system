import hashlib,json,os,shutil,subprocess,sys,tempfile
from pathlib import Path
import xml.etree.ElementTree as ET
root=Path('/Users/anastasia/Projects/erp_system');os.chdir(root);sys.path.insert(0,str(root))
from scripts.run_phase13_bank_batch_gate import source_fingerprint
out=Path('/private/tmp/erp_1314_orchestration/candidate');out.mkdir(exist_ok=True)
assert json.loads((out.parent/'categories/summary.json').read_text())['status']=='PASS'
paths=['app/services/payroll_paid_income_service.py','app/services/payroll_regulatory_correction_delta.py','app/services/payroll_regulatory_correction_projection.py','app/services/payroll_regulatory_correction_snapshot_store.py','app/services/payroll_regulatory_revision_chain_review.py']
tests=['tests/hr/'+n+'.py' for n in ['test_payroll_correction_multirevision_delta','test_payroll_correction_multirevision_snapshot','test_payroll_correction_multirevision_postgresql','test_payroll_paid_income_live_period_postgresql','test_payroll_paid_income_mixed_components_postgresql','test_payroll_correction_snapshot_concurrency_postgresql','test_payroll_paid_income_categories_postgresql']]
paths+=tests
(out/'scope.json').write_text(json.dumps({p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in paths},indent=2))
summary={'phase13':'OPEN','official_generation':'BLOCKED','base_head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'test_modules':tests,'status':'FAIL','production_sha256_before':source_fingerprint()}
try:
 with tempfile.TemporaryDirectory(prefix='erp_phase13_candidate_') as name:
  candidate=Path(name);os.chmod(candidate,0o700)
  archive=out/'candidate.tar'
  with archive.open('wb') as f:subprocess.run(['git','archive','HEAD'],stdout=f,check=True)
  subprocess.run(['tar','-xf',str(archive),'-C',str(candidate)],check=True);archive.unlink()
  for path in paths:
   shutil.copyfile(root/path,candidate/path)
  # Local configuration is private and never retained in acceptance evidence.
  if (root/'.env').is_file():
   shutil.copyfile(root/'.env',candidate/'.env');os.chmod(candidate/'.env',0o600)
  env={**os.environ,'PYTHONPATH':str(candidate),'PYTHONDONTWRITEBYTECODE':'1'}
  with (out/'tests.log').open('w') as log:
   rc=subprocess.run([sys.executable,'-m','scripts.run_postgresql_regression_copy','--upgrade-copy',*tests,'-x','--junitxml='+str(out/'tests.xml')],cwd=candidate,env=env,stdout=log,stderr=subprocess.STDOUT).returncode
  counts={k:0 for k in ('tests','failures','errors','skipped')}
  if (out/'tests.xml').exists():
   for s in ET.parse(out/'tests.xml').iter('testsuite'):
    for k in counts:counts[k]+=int(s.get(k,0))
  log=(out/'tests.log').read_text()
  checks={s:s in log for s in ('No new upgrade operations detected.','SOURCE ALEMBIC REVISION UNCHANGED = PASS','ISOLATED DATABASE REMOVED = PASS')}
  summary.update(exit=rc,checks=checks,**counts)
  if rc or not all(checks.values()) or counts['tests']!=48 or any(counts[k] for k in ('failures','errors','skipped')):raise RuntimeError('STOP: clean candidate')
  summary['status']='PASS'
except Exception as e:summary['stop_reason']=str(e)
finally:
 summary['production_sha256_after']=source_fingerprint()
 summary['production_unchanged']=summary['production_sha256_before']==summary['production_sha256_after']
 before=json.loads((out.parent/'start_files.json').read_text())
 summary['changed_existing_files']=[p for p,h in before.items() if not (root/p).is_file() or hashlib.sha256((root/p).read_bytes()).hexdigest()!=h]
 summary['source_scope_unchanged']=all(hashlib.sha256((root/p).read_bytes()).hexdigest()==h for p,h in json.loads((out/'scope.json').read_text()).items())
 if not summary['production_unchanged'] or summary['changed_existing_files'] or not summary['source_scope_unchanged']:summary['status']='FAIL'
 (out/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2),flush=True)
sys.exit(0 if summary['status']=='PASS' else 1)
