"""Loopback-only D1 verification; never reads a real API key."""
import subprocess, os, pathlib, time, urllib.request, json, sqlite3, hashlib, importlib.util, sys, socket, uuid
from unittest.mock import patch
root=pathlib.Path(__file__).resolve().parents[3]
py=pathlib.Path(sys.executable); log=pathlib.Path(os.environ.get('WB_VERIFY_LOGS', root/'logs'/('d1-'+uuid.uuid4().hex[:8]))); log.mkdir(parents=True,exist_ok=True); tool=pathlib.Path(__file__).parent
with socket.socket() as sock:
 sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
spec=importlib.util.spec_from_file_location('spike', tool/'d1_spike.py');spike=importlib.util.module_from_spec(spec);spec.loader.exec_module(spike)
checks={}
# Clear environment entirely so no actual credential can be accessed.
with patch.dict(os.environ, {}, clear=True), patch('openai.AsyncOpenAI') as client:
 try: spike.make_model()
 except RuntimeError as e: checks['real_default_failclosed']=str(e)
 else: raise AssertionError('real path enabled by default')
 assert not client.called
with patch.dict(os.environ, {'WB_ALLOW_REAL_MUSE':'1'}, clear=True), patch('openai.AsyncOpenAI') as client:
 try: spike.make_model()
 except RuntimeError as e: checks['authorized_but_missing_key_failclosed']=str(e)
 else: raise AssertionError('missing key accepted')
 assert not client.called
with patch.dict(os.environ, {'FAKE_BASE_URL':'https://example.invalid/v1'}, clear=True), patch('openai.AsyncOpenAI') as client:
 try: spike.make_model()
 except RuntimeError as e: checks['nonloopback_fake_rejected']=str(e)
 else: raise AssertionError('non-loopback fake accepted')
 assert not client.called
# Verify both construction branches by replacing clients/providers/models; no HTTP or real credentials.
for kind,env in [('fake',{'FAKE_BASE_URL':'http://127.0.0.1:18199/v1'}),('real_mocked',{'WB_ALLOW_REAL_MUSE':'1','META_API_KEY':'test-placeholder-not-real'})]:
 with patch.dict(os.environ, env, clear=True), patch('openai.AsyncOpenAI') as client, patch.object(spike,'MetaProvider'), patch.object(spike,'OpenAIChatModel'):
  spike.make_model();assert client.call_args.kwargs['max_retries']==0
  checks[kind+'_sdk_max_retries']=client.call_args.kwargs['max_retries']
# Minimal child environment: no key copying or inspection.
env={'PATH':str(py.parent)+':/usr/bin:/bin','FAKE_BASE_URL':f'http://127.0.0.1:{port}/v1','PYDANTIC_AI_NO_BANNER':'1'}
with (log/'d1_server.log').open('w') as f:
 server=subprocess.Popen([str(py),str(tool/'fake_meta_server.py'),str(port),str(log/'d1_requests.jsonl')],env=env,stdout=f,stderr=f)
 try:
  for i in range(50):
   try: urllib.request.urlopen(f'http://127.0.0.1:{port}/docs',timeout=1);break
   except Exception: time.sleep(.1)
  else: raise RuntimeError('fake server not ready')
  summary={'endpoint':env['FAKE_BASE_URL'],'fake_only':True,'phases':[],'guard_checks':checks}
  for phase,label in [('phase1','phase1'),('phase2','phase2'),('phase2','phase2_duplicate')]:
   with (log/f'd1_{label}.log').open('w') as out:
    p=subprocess.Popen([str(py),str(tool/'d1_spike.py'),phase,str(log/'d1_rehearsal.db'),'native'],env=env,stdout=out,stderr=out);rc=p.wait(timeout=45)
   with sqlite3.connect(log/'d1_rehearsal.db') as c: status=c.execute("select status from runs where task='t1'").fetchone()
   summary['phases'].append({'phase':label,'pid':p.pid,'exit_code':rc,'db_status':status})
   assert rc==(1 if label=='phase2_duplicate' else 0)
  req=[json.loads(x) for x in (log/'d1_requests.jsonl').read_text().splitlines()]
  assert len(req)==2
  wire={'http_requests':len(req),'native_json_schema_strict':all(r.get('response_format',{}).get('type')=='json_schema' and r['response_format']['json_schema'].get('strict') is True for r in req),'phase2_contains_prior_assistant_cq':any(m.get('role')=='assistant' and 'cq-rehearsal' in str(m.get('content')) for m in req[1]['messages']),'duplicate_rejection':(log/'d1_phase2_duplicate.log').read_text().strip()}
  assert wire['native_json_schema_strict'] and wire['phase2_contains_prior_assistant_cq']
  summary['copied_spike_sha256']=hashlib.sha256((tool/'d1_spike.py').read_bytes()).hexdigest()
  original=root.parent/'hackathon_inputs/Research_Workbench_v1_8_package/hackathon_kit/08_d1_rehearsal/d1_spike.py'
  summary['original_spike_sha256']=hashlib.sha256(original.read_bytes()).hexdigest() if original.exists() else 'original archive not bundled'
  summary['limit']='Mock, not real Muse; real path construction inspected only with mocks and placeholder.'
  (log/'d1_run_summary.json').write_text(json.dumps(summary,indent=2));(log/'d1_wire_verification.json').write_text(json.dumps(wire,indent=2));print(json.dumps(summary,indent=2));print(json.dumps(wire,indent=2))
 finally: server.terminate();server.wait(timeout=5)
