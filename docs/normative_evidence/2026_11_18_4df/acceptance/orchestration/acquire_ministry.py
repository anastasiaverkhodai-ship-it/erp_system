import concurrent.futures, hashlib, json, subprocess
from pathlib import Path
out=Path('/private/tmp/erp_1314_orchestration/normative/ministry');out.mkdir(exist_ok=True)
urls={
'order243_ministry':'https://mof.gov.ua/storage/files/%D0%9D%D0%B0%D0%BA%D0%B0%D0%B7%20243.pdf',
'order284_ministry':'https://mof.gov.ua/storage/files/%D0%9D%D0%B0%D0%BA%D0%B0%D0%B7%20284.pdf',
'amendment284_ministry':'https://mof.gov.ua/storage/files/%D0%97%D0%BC%D1%96%D0%BD%D0%B8%20%D0%BD%D0%B0%D0%BA%D0%B0%D0%B7%20284.pdf',
'ministry_index':'https://mof.gov.ua/uk/orders_of_the_Ministry_of_Finance_of_Ukraine_in_2026.',
}

def fetch(item):
 key,url=item;path=out/(key+('.pdf' if url.endswith('.pdf') else '.html'))
 r=subprocess.run(['curl','--fail','--location','--silent','--show-error','--connect-timeout','8','--max-time','20',url],capture_output=True)
 row=dict(id=key,url=url,checked_on='2026-11-18',approved=False,exit_code=r.returncode)
 if r.returncode==0 and r.stdout:
  path.write_bytes(r.stdout);row.update(status='BYTES_ACQUIRED_NOT_CONTENT_APPROVED',file=path.name,bytes=len(r.stdout),sha256=hashlib.sha256(r.stdout).hexdigest())
 else:row.update(status='FETCH_UNAVAILABLE')
 return row
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:rows=list(pool.map(fetch,urls.items()))
(out/'manifest.json').write_text(json.dumps(dict(checked_on='2026-11-18',approved_mappings=0,official_generation='BLOCKED',sources=rows),indent=2))
print(json.dumps([{'id':r['id'],'status':r['status'],'bytes':r.get('bytes')} for r in rows],indent=2))
