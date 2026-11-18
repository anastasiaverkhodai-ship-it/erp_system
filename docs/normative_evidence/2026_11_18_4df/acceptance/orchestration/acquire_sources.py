import concurrent.futures, hashlib, json, subprocess
from pathlib import Path
out=Path('/private/tmp/erp_1314_orchestration/normative');out.mkdir(exist_ok=True)
urls={
'order4_current':'https://zakon.rada.gov.ua/laws/show/z0111-15',
'order4_dated':'https://zakon.rada.gov.ua/laws/show/z0111-15/ed20260717',
'order243':'https://zakon.rada.gov.ua/laws/show/z0751-26',
'order284':'https://zakon.rada.gov.ua/laws/show/z0780-26',
'code':'https://zakon.rada.gov.ua/laws/show/2755-17',
'instruction5':'https://zakon.rada.gov.ua/laws/show/z0114-04',
'salary':'https://zir.tax.gov.ua/main/bz/view/?id=44723&src=ques',
'periods':'https://zir.tax.gov.ua/main/bz/view/?id=44664&src=ques',
'sick':'https://svp.tax.gov.ua/media-ark/news-ark/print-1040423.html',
'severance':'https://zir.tax.gov.ua/main/bz/view/?id=44727&src=ques',
'post_termination':'https://zak.tax.gov.ua/media-ark/news-ark/1053206.html',
'form_effective':'https://ck.tax.gov.ua/media-ark/news-ark/print-1040327.html',
'correction_effective':'https://zir.tax.gov.ua/main/bz/view/?id=44686&src=ques',
'vacation_historical':'https://rv.tax.gov.ua/baner/podatkovi-konsultatsii/konsultatsii-dlya-fizichnih-osib/70545.html',
}
def fetch(item):
 key,url=item;path=out/(key+'.html')
 r=subprocess.run(['curl','--fail','--location','--silent','--show-error','--connect-timeout','8','--max-time','20',url],capture_output=True)
 row=dict(id=key,url=url,checked_on='2026-11-18',approved=False,exit_code=r.returncode)
 if r.returncode==0 and r.stdout:
  path.write_bytes(r.stdout);row.update(status='BYTES_ACQUIRED_NOT_CONTENT_APPROVED',file=path.name,bytes=len(r.stdout),sha256=hashlib.sha256(r.stdout).hexdigest())
 else:row.update(status='FETCH_UNAVAILABLE')
 return row
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:rows=list(pool.map(fetch,urls.items()))
(out/'manifest.json').write_text(json.dumps(dict(checked_on='2026-11-18',approved_mappings=0,official_generation='BLOCKED',sources=rows),indent=2))
print(json.dumps([{'id':r['id'],'status':r['status'],'bytes':r.get('bytes')} for r in rows],indent=2))
