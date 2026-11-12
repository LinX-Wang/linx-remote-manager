"""Explicit installed-app smoke test using a private temporary remote directory."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import time
import urllib.parse
import urllib.request

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import manager

def main():
    from playwright.sync_api import sync_playwright
    parser=argparse.ArgumentParser();parser.add_argument('settings');args=parser.parse_args()
    config=Path(args.settings);settings=json.loads(config.read_text(encoding='utf-8-sig'));original=hashlib.sha256(config.read_bytes()).hexdigest()
    info=json.loads((manager.STATE/'ui-instance.json').read_text());url=urllib.parse.urlsplit(info['url'])
    def api(op,**data):
        request=urllib.request.Request('http://127.0.0.1:'+str(url.port)+'/api',data=json.dumps(dict(op=op,**data)).encode(),headers={'Content-Type':'application/json','X-Kit-Token':url.fragment})
        with urllib.request.urlopen(request,timeout=45) as f:reply=json.load(f)
        assert reply.get('ok'),'Installed API request failed'
        return reply['data']
    ssh,destination=manager.ssh_base(settings)
    def remote(code):
        result=manager.ssh_run(settings,ssh+[destination,'python3 -c '+shlex.quote(code)],capture_output=True,timeout=15,**manager.HIDDEN)
        assert result.returncode==0,'Remote test setup failed'
        return json.loads(result.stdout)
    data=api('monitor_status',force=True);assert not data.get('error')
    identities={p['pid']:p['start_ticks'] for g in data['gpus'] for p in g['processes']}
    folder=remote('import tempfile,json;print(json.dumps(tempfile.mkdtemp(prefix="linx-installed-verify-")))')
    watch_id=None;job_ids=[]
    try:
        with tempfile.TemporaryDirectory() as local,sync_playwright() as pw:
            browser=pw.chromium.launch(channel='chrome');page=browser.new_page(viewport={'width':1440,'height':1000},bypass_csp=True,accept_downloads=True)
            page.add_init_script('window.showSaveFilePicker=undefined')
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            allowed=('bootstrap','list','ping','monitor_status','monitor_history','watch_status','watch_add','watch_remove','connection_diagnostics','files_list','transfer_begin','transfer_status','transfer_discard','cindy_connections')
            def route(r):
                op=r.request.post_data_json.get('op')
                if op in allowed:
                    response=r.fetch();reply=response.json()
                    if op=='list' and reply.get('ok'):
                        c=reply['data'].get('model_catalog',{});c['can_auto_sync']=False;c['can_sync']=False
                    return r.fulfill(response=response,json=reply)
                return r.fulfill(json={'ok':True,'data':{}})
            page.route('**/api',route);page.goto(info['url']);page.wait_for_function('S.loaded && !S.busy',timeout=45000)
            page.evaluate("navigate('connection')");page.locator('#tools-diagnose').click();page.wait_for_function('Tools.diagnostics && !Tools.diagnosing',timeout=45000)
            assert page.locator('.diagnostic-item').count()>=5
            print('PASS: installed diagnostics button and segmented real SSH results')
            page.evaluate("async()=>{S.page='monitor';await collectMonitor(true);await pollTools()}")
            page.wait_for_function('Tools.history.length>=1')
            page.wait_for_timeout(5100);page.evaluate('async()=>{await collectMonitor(true);await pollTools()}')
            page.wait_for_function('Tools.history.length>=2')
            card_height=page.locator('.monitor-gpus').bounding_box()['height']
            page.locator('[data-gpu-trend]').first.click()
            assert page.locator('#trend-chart svg').count()==1
            assert page.locator('#trend-chart svg circle').count()>=2
            assert abs(page.locator('.monitor-gpus').bounding_box()['height']-card_height)<1
            page.keyboard.press('Escape')
            assert page.locator('#monitor-processes th').count()==8
            print('PASS: installed GPU history uses multiple live samples; timing columns retained')
            page.evaluate("navigate('files')");page.wait_for_function('!Tools.fileBusy && Tools.files')
            page.evaluate('(path)=>loadFiles(path)',folder);page.wait_for_function('!Tools.fileBusy')
            payload=bytes(range(256))*4097
            page.locator('#files-upload').set_input_files(dict(name='round trip.bin',mimeType='application/octet-stream',buffer=payload))
            page.wait_for_function("[...Tools.jobs.values()].some(j=>j.direction==='upload' && j.state==='done')",timeout=45000)
            page.wait_for_function("!Tools.fileBusy && Tools.files.entries.some(e=>e.name==='round trip.bin')")
            row=page.locator('.files-table tr').filter(has_text='round trip.bin');row.locator('[data-file-download]').click()
            page.wait_for_function("[...Tools.jobs.values()].some(j=>j.direction==='download' && j.state==='done')",timeout=45000)
            page.wait_for_selector('[data-transfer-save]')
            job_ids=page.evaluate('[...Tools.jobs.keys()]')
            with page.expect_download(timeout=15000) as event:page.locator('[data-transfer-save]').click()
            download=event.value;target=Path(local)/'result.bin';download.save_as(target)
            assert target.read_bytes()==payload
            assert download.suggested_filename=='round trip.bin'

            print('PASS: installed browser upload, download, progress, SHA256 and saved file bytes')
            for width in (320,390,768,1440):
                page.set_viewport_size({'width':width,'height':900})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),width
            page.set_viewport_size({'width':1440,'height':1000})
            probe=remote(r'''import subprocess,json
p=subprocess.Popen(['python3','-c','import time;time.sleep(4)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
with open('/proc/'+str(p.pid)+'/stat') as f:ticks=f.read().rsplit(')',1)[1].split()[19]
print(json.dumps(dict(pid=p.pid,ticks=ticks)))''')
            state=api('watch_add',pid=probe['pid'],ticks=probe['ticks'],user='test')
            watch_id=next(w['id'] for w in state['watches'] if w['pid']==probe['pid'])
            page.evaluate("async()=>{S.page='monitor';await pollTools();render()}")
            for _ in range(40):
                state=api('watch_status')
                if any(e['pid']==probe['pid'] for e in state['events']):break
                page.wait_for_timeout(1000)
            else:raise AssertionError('Installed background watch produced no exit event')
            assert len([e for e in state['events'] if e['pid']==probe['pid']])==1
            page.evaluate('pollTools()');page.wait_for_function('(pid)=>Tools.events.some(e=>e.pid===pid)',arg=probe['pid'])
            assert not errors,errors
            browser.close()
            print('PASS: installed background watch reports real test-process exit once; UI receives event')
        after=api('monitor_status',force=True);current={p['pid']:p['start_ticks'] for g in after['gpus'] for p in g['processes']}
        assert all(current.get(pid)==ticks for pid,ticks in identities.items()),'GPU task identity changed during test'
        assert hashlib.sha256(config.read_bytes()).hexdigest()==original
        print('PASS: existing GPU tasks and settings unchanged')
    finally:
        if watch_id:api('watch_remove',id=watch_id)
        for key in job_ids:
            try:api('transfer_discard',id=key)
            except Exception:pass
        remote('import os,shutil,json;p='+repr(folder)+';assert os.path.realpath(p)==p and p.startswith("/tmp/linx-installed-verify-");shutil.rmtree(p);print(json.dumps(True))')
        print('Installed integration test records and temporary files removed')

if __name__=='__main__':main()
