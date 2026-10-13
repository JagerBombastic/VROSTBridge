import hashlib,json,os,pathlib,subprocess,tempfile,time,urllib.request,urllib.error
root=pathlib.Path('/workspace/scratch/647ea6e3ae16')
installer=root/'build-tools/release-current/bazzite/VROSTBridge-Bazzite-Installer'
result={'release':'0.2.2','platform':'Linux container; no physical headset','checks':{}}
with tempfile.TemporaryDirectory(prefix='VROSTBridge test Ü ') as td:
 base=pathlib.Path(td);env=dict(os.environ,XDG_DATA_HOME=str(base/'data files'),XDG_STATE_HOME=str(base/'state files'),VROSTBRIDGE_INSTALL_NO_LAUNCH='1',BROWSER='/bin/true')
 # Installer tests use real bundled launcher and actual hash manifests.
 for stage in ['install','repeat_install']:
  p=subprocess.run(['bash',str(installer/'install.sh')],env=env,capture_output=True,text=True,timeout=60)
  assert p.returncode==0,(stage,p.stdout,p.stderr)
  result['checks'][stage]='pass'
 current=base/'data files/vrostbridge/current';exe=current/'VROSTBridge-Client'
 assert (base/'data files/applications/io.vrostbridge.Client.desktop').is_file()
 assert (base/'data files/icons/hicolor/256x256/apps/io.vrostbridge.Client.png').read_bytes()==(root/'work/VROSTBridge-0.2.0/launcher/brand/logo.png').read_bytes()
 result['checks']['spaces_non_ascii_and_logo']='pass'
 with (base/'launcher-output.txt').open('w') as out:
  app=subprocess.Popen([str(exe),'--role','client'],env=env,stdout=out,stderr=subprocess.STDOUT)
  try:
   state=base/'state files/VROSTBridge/client-desktop.json'
   for _ in range(100):
    if state.exists():break
    assert app.poll() is None,(base/'launcher-output.txt').read_text()
    time.sleep(.1)
   record=json.loads(state.read_text());url='http://127.0.0.1:'+str(record['port'])
   def request(path,data=None,token=True):
    headers={'X-VROSTBridge-Token':record['token']} if token else {}
    if data is not None:headers['Content-Type']='application/json';data=json.dumps(data).encode()
    return urllib.request.urlopen(urllib.request.Request(url+path,data=data,headers=headers),timeout=5)
   with request('/') as r:assert b'VROSTBridge' in r.read()
   with request('/brand/logo.png') as r:assert r.read()==(root/'work/VROSTBridge-0.2.0/launcher/brand/logo.png').read_bytes()
   with request('/status') as r:
    status=json.load(r);assert status['packaged'] and status['available'] and status['role']=='client'
   with request('/report') as r:assert json.load(r)['role']=='client'
   try:request('/status',token=False);raise AssertionError('Missing token accepted')
   except urllib.error.HTTPError as e:assert e.code==403
   reopen=subprocess.run([str(exe),'--role','client'],env=env,capture_output=True,text=True,timeout=12)
   assert reopen.returncode==0,reopen.stderr
   assert app.poll() is None
   result['checks']['frozen_http_logo_token_report_and_reopen']='pass'
   with request('/action',{'action':'quit'}) as r:assert json.load(r)['ok']
   assert app.wait(timeout=15)==0
   assert not state.exists()
   result['checks']['quit_releases_instance']='pass'
  finally:
   if app.poll() is None:app.terminate();app.wait(timeout=10)
 # Corrupt only the temporary installed copy; prove self-test fails and log records why.
 (current/'brand/logo.png').write_bytes(b'bad')
 p=subprocess.run([str(exe),'--role','client','--self-test'],env=env,capture_output=True,text=True,timeout=20)
 assert p.returncode!=0
 assert 'damaged or changed' in (p.stdout+p.stderr).lower(),p.stderr
 assert (base/'state files/VROSTBridge/client-startup.log').is_file()
 result['checks']['damaged_package_rejected_and_logged']='pass'
path=root/'build-tools/release-smoke-0.2.2.json';path.write_text(json.dumps(result,indent=2)+'\n');print(path.read_text())
