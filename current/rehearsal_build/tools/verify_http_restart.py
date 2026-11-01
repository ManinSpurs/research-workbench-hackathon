"""Fake-only real-loopback HTTP + fresh-process restart acceptance (not a browser test)."""
import json, os, socket, subprocess, sys, tempfile, time
from pathlib import Path
from urllib.request import Request, build_opener, ProxyHandler
ROOT=Path(__file__).resolve().parents[1]
LOGS=Path(os.environ.get('WB_VERIFY_LOGS',ROOT.parent/'logs'));LOGS.mkdir(exist_ok=True)
def port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
fp,bp=port(),port();env={**os.environ,'FAKE_BASE_URL':f'http://127.0.0.1:{fp}/v1','PYDANTIC_AI_NO_BANNER':'1','NO_PROXY':'localhost,127.0.0.1','no_proxy':'localhost,127.0.0.1'}
procs=[];opener=build_opener(ProxyHandler({}));trace=[]
def call(method,path,body=None):
    data=json.dumps(body).encode() if body is not None else None
    req=Request(f'http://127.0.0.1:{bp}'+path,data=data,method=method,headers={'Content-Type':'application/json'})
    with opener.open(req,timeout=30) as r:out=json.load(r)
    trace.append({'method':method,'path':path,'response':out});return out
def start_backend():
    p=subprocess.Popen([sys.executable,'-m','uvicorn','app.api:app','--host','127.0.0.1','--port',str(bp),'--log-level','warning'],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);procs.append(p)
    for _ in range(100):
        try:call('GET','/projects');return p
        except Exception:
            if p.poll() is not None:raise RuntimeError('backend exited')
            time.sleep(.1)
    raise RuntimeError('backend readiness timed out')
try:
    with tempfile.TemporaryDirectory(prefix='wb-http-') as tmp:
        env['WB_DB']=str(Path(tmp)/'test.db')
        fake=subprocess.Popen([sys.executable,'-m','app.fake_meta',str(fp)],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);procs.append(fake)
        backend=start_backend()
        registry=call('GET','/domain-packs')
        assert len(registry['packs'])==2 and registry['h2_frame_resolve']=='NOT_IMPLEMENTED'
        assert next(c for c in registry['capabilities'] if c['id']=='meta_text')['status']=='blocked'
        pid=call('POST','/projects',{'kind':'nyc'})['project_id']
        q=call('POST',f'/projects/{pid}/questions',{'text':'Can I study how long it takes from filing to the start of construction?'})
        qid=q['question_id'];pin=call('GET',f'/questions/{qid}/context');assert pin['pack_id']=='social_science'
        r=call('POST',f'/questions/{qid}/run');assert r['adapter']=='mock'
        a=call('POST',f"/clarifications/{r['cq_id']}/responses",{'kind':'unknown'})
        assert a['outcome']=='supported' and a['evidence'][0]['source_version']
        reason='HTTP restart rehearsal: construction-start evidence is not present'
        call('POST',f'/projects/{pid}/decisions',{'question_id':qid,'verdict':'defer','reason':reason,'client_action_id':'http-1'})
        shadow=call('POST',f'/questions/{qid}/shadow');assert shadow['adapter']=='mock'
        c=call('GET',f'/questions/{qid}/canvas');assert c['revision']==0
        draft={'expected_revision':0,'nodes':[{'label':'Filing','dataset_id':'nyc/bis_2024_subset.csv','variable':'filing_date','note':'HTTP-CANVAS-NOTE-7285'},{'label':'Construction starts','dataset_id':None,'variable':None,'note':'Unmapped'}]}
        saved=call('PUT',f'/questions/{qid}/canvas',draft);assert saved['revision']==1 and saved['nodes'][0]['status']=='column_linked'
        before=call('GET',f'/projects/{pid}/usage')['usage']
        first_pid=backend.pid;backend.terminate();backend.wait(timeout=10)
        backend=start_backend();assert backend.pid!=first_pid
        restored=call('GET',f'/questions/{qid}/canvas');assert restored['revision']==1 and restored['nodes'][0]['note']=='HTTP-CANVAS-NOTE-7285'
        assert call('GET',f'/questions/{qid}/context')==pin
        assert not call('GET',f'/projects/{pid}/pack-memory?pack_id=engineering_measurement')['questions']
        assert reason not in json.dumps(call('GET',f'/projects/{pid}/briefing?pack_id=engineering_measurement'))
        assert reason in json.dumps(call('GET',f'/projects/{pid}/briefing?pack_id=social_science'))
        b=call('GET',f'/projects/{pid}/briefing');assert reason in json.dumps(b) and b['model_calls']==0
        assert call('GET',f'/projects/{pid}/usage')['usage']==before
        deletion=call('DELETE',f'/questions/{qid}');assert deletion['verified']
        assert reason not in json.dumps(call('GET',f'/projects/{pid}/briefing'))
        compared=subprocess.run([sys.executable,'-m','app.compare',str(Path(tmp)/'compare.db')],cwd=ROOT,env=env,capture_output=True,text=True,timeout=120)
        (LOGS/'comparison_stderr.log').write_text(compared.stderr)
        if compared.returncode:raise RuntimeError('comparison failed: '+compared.stderr[-1000:])
        report=json.loads(compared.stdout);assert report['harness_matches']==8 and report['adapter']=='mock'
        (LOGS/'comparison_actual.json').write_text(json.dumps(report,indent=2))
        summary={'result':'PASS','adapter':'mock','first_backend_pid':first_pid,'restarted_backend_pid':backend.pid,
                 'restart_briefing_model_calls':0,'delete_verified':True,'comparison_matches':8,'comparison_cases':8,'canvas_survives_restart':True,'pack_pin_survives_restart':True,'pack_briefing_isolation':True,
                 'not_verified':['real Muse','browser clicks','model quality','provider cost']}
        (LOGS/'http_restart_summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary))
finally:
    (LOGS/'http_restart_trace.json').write_text(json.dumps(trace,indent=2))
    for p in procs:
        if p.poll() is None:p.terminate()
    for p in procs:
        try:p.wait(timeout=5)
        except subprocess.TimeoutExpired:p.kill()
