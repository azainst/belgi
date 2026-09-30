import json, os, pathlib, shutil, socket, subprocess, tempfile, time, urllib.request
root = pathlib.Path(__file__).resolve().parent.parent
python = root/'.venv/Scripts/python.exe'
with tempfile.TemporaryDirectory(prefix='landmonitor-test-') as folder:
    test = pathlib.Path(folder)
    shutil.copytree(root/'app', test/'app', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(root/'tools', test/'tools', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy2(root/'run.py', test/'run.py')
    (test/'data').mkdir()
    shutil.copy2(root/'data/parcels.geojson', test/'data/parcels.geojson')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port = sock.getsockname()[1]
    (test/'.env').write_text(f'PORT={port}\nSIGNAL_MERGE_RADIUS_M=321\nBOT_TOKEN=\nINSPECTOR_PASSWORD=\n')
    env = os.environ.copy()
    for key in ['PORT','SIGNAL_MERGE_RADIUS_M','BOT_TOKEN','INSPECTOR_PASSWORD']:
        env.pop(key, None)
    env['PYTHONUTF8']='1'
    subprocess.run([str(python),'-B','-c','from app import services; assert services.MERGE_RADIUS_M == 321; print(".env radius OK")'],cwd=test,env=env,check=True)
    with (test/'server.log').open('w+',encoding='utf-8') as log:
        proc = subprocess.Popen([str(python),'-B','run.py'],cwd=test,env=env,stdout=log,stderr=subprocess.STDOUT)
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def call(path, body=None):
                req = urllib.request.Request(f'http://127.0.0.1:{port}'+path, data=None if body is None else json.dumps(body).encode(), headers={'Content-Type':'application/json'})
                with opener.open(req,timeout=5) as response:
                    data=response.read()
                    return json.loads(data) if 'application/json' in response.headers.get('Content-Type','') else data
            for _ in range(80):
                if proc.poll() is not None: raise RuntimeError('Server exited')
                try:
                    assert call('/healthz')['ok']
                    break
                except OSError: time.sleep(.25)
            else: raise RuntimeError('Server timeout')
            assert b'leaflet' in call('/')
            features=call('/api/parcels')['features']
            assert len(features)==14
            call('/api/meta'); call('/api/stats')
            created=call('/api/demo/signal',{})
            pid=created['parcel_id']
            assert call(f'/api/parcels/{pid}')['signals_count']>=1
            updated=call(f'/api/parcels/{pid}/status',{'status':'in_progress','note':'Smoke test'})
            assert updated['status']=='in_progress'
            assert call(f'/api/parcels/{pid}/act')
            print('PASS: custom PORT, health, panel, 14 parcels, meta, stats, create signal, change status, act')
        except Exception:
            log.flush(); log.seek(0); print(log.read()); raise
        finally:
            proc.terminate(); proc.wait(timeout=10)
