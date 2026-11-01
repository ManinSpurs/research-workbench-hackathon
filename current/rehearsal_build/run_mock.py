"""Start the local rehearsal only. Never selects a real provider or reads a key.

The default rehearsal is mock-only: no cloud provider and no model runtime is
started, and ``WB_ALLOW_REAL_MUSE`` stays ``0``. One opt-in exception exists and
is never implied: ``--local-runtime`` (or ``WB_LOCAL_RUNTIME=1``) additionally
starts the *locally installed* llama.cpp server from this app's own private
configuration file. That is local compute only — it reads no cloud credential
(it generates its own per-run bearer for the model server it starts), adds no
cloud route, and the cloud adapter remains the scripted fake one.
"""
import os, signal, socket, subprocess, sys, time, webbrowser
from pathlib import Path
ROOT=Path(__file__).resolve().parent
ports=(8301,8123,5173)
for port in ports:
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        try:s.bind(('127.0.0.1',port))
        except OSError:raise SystemExit(f'Port {port} is in use. Close the previous rehearsal before starting another.')
#: Every environment name that carries a local *run* binding or its credential.
#: These are per-launch facts this supervisor publishes, so they must never be
#: inherited from an ambient environment: a stale WB_LOCAL_API_KEY or origin in
#: the operator's shell would otherwise be handed to the static file server and
#: the desktop shell, and would describe a run this process did not verify.
LOCAL_RUN_ENV=('WB_LOCAL_BASE_URL','WB_LOCAL_MODEL','WB_LOCAL_CONTEXT','WB_LOCAL_RUN_ID',
               'WB_LOCAL_SERVER_PID','WB_LOCAL_WEIGHTS_SHA256','WB_LOCAL_TEMPLATE_OVERHEAD_TOKENS',
               'WB_LOCAL_OBSERVED_ALIAS','WB_LOCAL_OBSERVED_CTX','WB_LOCAL_OBSERVED_CTX_TRAIN',
               'WB_LOCAL_API_KEY')
#: The shared environment. Every local run binding/secret is stripped from the
#: inherited copy first, then the rehearsal settings are added: the basis for
#: the fake provider, the static file server and the desktop shell.
env={k:v for k,v in os.environ.items() if k not in LOCAL_RUN_ENV}
env.update({'FAKE_BASE_URL':'http://127.0.0.1:8301/v1','WB_ALLOW_REAL_MUSE':'0',
     'WB_DB':os.environ.get('WB_DB',str(ROOT/'local_rehearsal.db')),'PYDANTIC_AI_NO_BANNER':'1','NO_PROXY':'localhost,127.0.0.1','no_proxy':'localhost,127.0.0.1'})
APP_ORIGIN='http://localhost:5173'
#: (argv, cwd, gets_the_api_env). Only the API process is marked; the static
#: file server and the desktop shell never receive a local model credential.
commands=[([sys.executable,'-m','app.fake_meta','8301'],ROOT,False),
          ([sys.executable,'-m','uvicorn','app.api:app','--host','127.0.0.1','--port','8123'],ROOT,True),
          ([sys.executable,'-m','http.server','5173','--bind','127.0.0.1'],ROOT/'frontend',False)]
#: The API child's environment. It starts as a copy of the shared one and, when
#: a local model is launched, receives the per-run binding (bearer included) too.
#: Deliberately a separate dict: the static file server and the desktop shell are
#: started from the shared env, so the model's credential is never visible to
#: them or to anything they spawn.
api_env=env
processes=[]
# Opt-in local model runtime. It is started *inside* the try block below, after
# the signal handlers are installed, so an interrupt during model load is caught
# by this supervisor's own cleanup instead of orphanning a loading child.
_local_config=None
local_server=None
local_wanted=('--local-runtime' in sys.argv) or os.environ.get('WB_LOCAL_RUNTIME')=='1'
if local_wanted:
    if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
    from app.ai import local_config, local_launcher
    try:
        _local_config=local_config.read()
    except ValueError as exc:
        raise SystemExit(f'Local runtime configuration is invalid: {exc}. No server was started.')
    if not _local_config:
        raise SystemExit('Local runtime was requested, but no configuration file exists. No server was started.')
def stop(*_): raise KeyboardInterrupt
# Handlers first: nothing that can spawn a child may run before them, otherwise
# SIGTERM/SIGINT during model load would kill this supervisor and leave the
# llama-server child running.
signal.signal(signal.SIGTERM,stop)
if hasattr(signal,'SIGHUP'):signal.signal(signal.SIGHUP,stop)
try:
    if _local_config is not None:
        # Same contract as the other servers: bounded preflight, spawn, readiness.
        # The server is authenticated with a per-run bearer and must prove its own
        # alias and context before this supervisor treats it as running.
        local_server=local_launcher.LocalServer(_local_config,log_path=str(ROOT/'local_runtime.log'),
                                                app_origin=APP_ORIGIN)
        try:
            local_server.start()
        except local_launcher.LocalLaunchError as exc:
            raise SystemExit(f'Local runtime did not start: {exc}')
        # Publish the verified binding to the API child only: origin, alias,
        # context, run id, launched pid, weights digest, measured template
        # overhead and the per-run bearer. The static server and the desktop
        # shell keep the shared env, which never carries the model credential.
        api_env={**env,**local_launcher.binding_environment(local_server)}
        print(f'\nLOCAL MODEL: {_local_config["base_url"]} (alias {_local_config["alias"]}, '
              f'context {_local_config["context"]}, template overhead '
              f'{local_server.template_overhead_tokens} tokens). '
              f'Local compute only, zero API cost.\n',flush=True)
    for cmd,cwd,wants_api_env in commands:
        processes.append(subprocess.Popen(cmd,cwd=cwd,env=api_env if wants_api_env else env))
    time.sleep(3)
    if any(p.poll() is not None for p in processes):raise RuntimeError('A rehearsal server failed to start; see the terminal output.')
    print('\nMOCK ONLY: http://localhost:5173/?api=http://localhost:8123\nNo Muse calls. Press Ctrl+C to stop all servers.\n',flush=True)
    if '--desktop' in sys.argv:
        electron=ROOT/'node_modules'/'.bin'/'electron'
        if not electron.exists():
            raise RuntimeError('Desktop runtime is missing. No installation was attempted. Run python3 run_mock.py for browser rehearsal.')
        desktop_args=[str(electron),'.','--quit-on-window-close']
        if os.environ.get('WB_DESKTOP_PROFILE'):
            desktop_args.append('--user-data-dir='+os.environ['WB_DESKTOP_PROFILE'])
        if '--smoke' in sys.argv:desktop_args.append('--smoke')
        processes.append(subprocess.Popen(desktop_args,cwd=ROOT,env=env))
    else:
        webbrowser.open('http://localhost:5173/?api=http://localhost:8123')
    while all(p.poll() is None for p in processes):time.sleep(1)
except KeyboardInterrupt:pass
finally:
    # Cleanup runs for every exit path, including SystemExit from a failed start
    # and an interrupt during model load. A cleanup failure is reported on stderr
    # and never replaces the exception (or exit status) that ended the run.
    for p in processes:
        if p.poll() is None:p.terminate()
    for p in processes:
        try:p.wait(timeout=5)
        except subprocess.TimeoutExpired:p.kill()
    # Owned child only: this supervisor stops the server it started itself and
    # never signals a process it did not spawn.
    if local_server is not None:
        try:
            from app.ai import local_launcher
            local_launcher.stop_preserving(local_server)
        except BaseException as exc:
            print(f'Local runtime cleanup could not run: {exc}',file=sys.stderr,flush=True)
