"""Opt-in integration checks against a user-selected SSH settings file.

Creates only a uniquely named temporary directory and short-lived test process.
Does not inspect user file contents or affect existing processes.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shlex
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import manager
import remote_tools
from monitor_features import Features

def main():
    parser=argparse.ArgumentParser();parser.add_argument('settings');args=parser.parse_args()
    settings=json.loads(Path(args.settings).read_text(encoding='utf-8-sig'))
    ssh,destination=manager.ssh_base(settings)
    def remote(code):
        result=manager.ssh_run(settings,ssh+[destination,'python3 -c '+shlex.quote(code)],capture_output=True,timeout=15,**manager.HIDDEN)
        if result.returncode:raise RuntimeError('Remote test setup failed')
        return json.loads(result.stdout)
    setup=r'''import json,os,tempfile,subprocess,time
folder=tempfile.mkdtemp(prefix='linx-tools-verify-')
with open(folder+'/preserve.txt','wb') as f:f.write(b'preserve original')
with open(folder+'/empty','wb'):pass
os.mkdir(folder+'/directory')
os.mkfifo(folder+'/fifo')
os.symlink(folder+'/preserve.txt',folder+'/link')
print(json.dumps(dict(folder=folder)))
'''
    folder=remote(setup)['folder'];transfers=remote_tools.Transfers();ids=[]
    try:
        listing=remote_tools.list_files(settings,folder)
        kinds={e['name']:e['kind'] for e in listing['entries']}
        assert kinds['directory']=='directory' and kinds['fifo']=='special' and kinds['link']=='link'
        try:remote_tools.list_files(settings,folder+'/missing')
        except ValueError:pass
        else:raise AssertionError('Missing directory accepted')
        print('PASS: real directory listing, file kinds and missing path')
        content=bytes(range(256))*8193
        upload=transfers.begin(settings,'upload',directory=folder,name='round trip.txt',size=len(content));ids.append(upload['id'])
        result=transfers.upload(upload['id'],io.BytesIO(content),len(content))
        assert result['state']=='done',result.get('error')
        assert result['sha256']==hashlib.sha256(content).hexdigest()
        download=transfers.begin(settings,'download',path=folder+'/round trip.txt');ids.append(download['id'])
        for _ in range(150):
            result=transfers.status(download['id'])
            if result['state'] not in ('pending','running'):break
            time.sleep(.1)
        assert result['state']=='done',result
        local=Path(transfers.jobs[download['id']]['local'])
        assert local.read_bytes()==content
        assert result['sha256']==hashlib.sha256(content).hexdigest()
        print('PASS: streamed binary upload/download > 2 MiB, exact bytes and SHA256')
        for name,data in [('preserve.txt',b'overwrite'),('interrupted.txt',b'short')]:
            size=len(data) if name=='preserve.txt' else 100
            job=transfers.begin(settings,'upload',directory=folder,name=name,size=size);ids.append(job['id'])
            reply=transfers.upload(job['id'],io.BytesIO(data),size)
            assert reply['state']=='failed'
        listing=remote_tools.list_files(settings,folder)
        names=[e['name'] for e in listing['entries']]
        assert 'interrupted.txt' not in names and not any(n.startswith('.linx-upload-') for n in names)
        preserved=remote('import json;print(json.dumps(open('+repr(folder+'/preserve.txt')+').read()))')
        assert preserved=='preserve original'
        print('PASS: overwrite refused, interrupted upload leaves no target or partial file')
        for path in (folder+'/link',folder+'/fifo','/proc/self/fd/1'):
            try:remote_tools.request(settings,'stat',path=path)
            except ValueError:pass
            else:raise AssertionError('Special file accepted')
        print('PASS: symlinks, FIFO and proc pipes rejected without reading')
        job=transfers.begin(settings,'download',path=folder+'/empty');ids.append(job['id'])
        for _ in range(100):
            result=transfers.status(job['id'])
            if result['state'] not in ('pending','running'):break
            time.sleep(.1)
        assert result['state']=='done' and result['sha256']==hashlib.sha256(b'').hexdigest()
        print('PASS: zero-byte download')
        probe=remote_tools.diagnostics(settings)
        assert any(r['name']=='SSH / Python' and r['status']=='ok' for r in probe['rows'])
        assert any(r['name']=='Codex 文件' for r in probe['rows'])
        print('PASS: live segmented diagnostics; statuses:',','.join(r['status'] for r in probe['rows']))
        process=remote(r'''import subprocess,json
p=subprocess.Popen(['python3','-c','import time;time.sleep(5)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
with open('/proc/'+str(p.pid)+'/stat') as f:ticks=f.read().rsplit(')',1)[1].split()[19]
print(json.dumps(dict(pid=p.pid,ticks=ticks)))''')
        features=Features();features.add(settings,process['pid'],process['ticks'],'test')
        features.tick(settings);assert not features.status(settings)['events']
        time.sleep(6)
        features.tick(settings);assert not features.status(settings)['events']
        features.tick(settings);assert len(features.status(settings)['events'])==1
        features.tick(settings);assert len(features.status(settings)['events'])==1
        print('PASS: dedicated short-lived process exit confirmed twice, single event')
    finally:
        for key in ids:
            try:transfers.discard(key)
            except Exception:pass
        remote('import shutil,json,os;p='+repr(folder)+';assert os.path.realpath(p)==p and p.startswith("/tmp/linx-tools-verify-");shutil.rmtree(p);print(json.dumps(True))')
        print('Temporary test files removed')

if __name__=='__main__':main()
