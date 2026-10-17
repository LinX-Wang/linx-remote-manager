"""Official-package fallback: local resumable download and verified SSH upload."""
import hashlib,json,os,re,shlex,socket,subprocess,time,urllib.request
from pathlib import Path

def digest(path,limit=None):
 h=hashlib.sha256()
 with path.open('rb') as f:
  while limit is None or limit>0:
   b=f.read(min(1024*1024,limit) if limit is not None else 1024*1024)
   if not b:break
   h.update(b)
   if limit is not None:limit-=len(b)
 return h.hexdigest()

def validate(spec):
 url=spec.get('url','');name=spec.get('name','')
 m=re.fullmatch(r'https://github.com/openai/codex/releases/download/rust-v(\d+\.\d+\.\d+)/codex-package-([A-Za-z0-9_.-]+)\.tar\.gz',url)
 if not m or name!='codex-package-'+m[1]+'-'+m[2]+'.tar.gz' or not re.fullmatch('[0-9a-f]{64}',spec.get('sha256','')):raise ValueError('备用下载的官方完整包信息无效，请重新检查更新')
 return name

class OfficialRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,req,fp,code,msg,headers,newurl):
  from urllib.parse import urlsplit
  u=urlsplit(newurl)
  if u.scheme!='https' or not (u.hostname=='github.com' or (u.hostname or '').endswith(('.github.com','.githubusercontent.com'))):raise ValueError('官方包重定向地址不受信任，已停止下载')
  return super().redirect_request(req,fp,code,msg,headers,newurl)

def openers():
 configured=urllib.request.getproxies()
 if configured.get('https'):yield urllib.request.build_opener(urllib.request.ProxyHandler(configured),OfficialRedirect())
 # Local desktop proxy applications may run without enabling the OS-wide proxy.
 for port in (7897,7890,10809):
  try:
   with socket.create_connection(('127.0.0.1',port),timeout=.2):pass
  except OSError:continue
  yield urllib.request.build_opener(urllib.request.ProxyHandler({'https':'http://127.0.0.1:'+str(port)}),OfficialRedirect())
 yield urllib.request.build_opener(urllib.request.ProxyHandler({}),OfficialRedirect())

def local_download(spec,state,emit):
 name=validate(spec);cache=state/'downloads';cache.mkdir(parents=True,exist_ok=True)
 path=cache/name;size=int(spec.get('size') or 0)
 if path.exists() and digest(path)==spec['sha256']:
  emit('复用本机已校验的官方包，准备传输到服务器',phase='verify');return path
 emit('服务器无法直连 GitHub，改由本机下载官方包',phase='download')
 for opener in openers():
  for attempt in range(2):
   offset=path.stat().st_size if path.exists() else 0
   headers={'User-Agent':'LinX-Remote/1.7','Accept-Encoding':'identity'}
   if offset:headers['Range']='bytes='+str(offset)+'-'
   fresh=path.with_suffix('.fresh')
   try:
    with opener.open(urllib.request.Request(spec['url'],headers=headers),timeout=15) as r:
     if not size:
      content_range=re.search(r'/(\d+)$',r.headers.get('Content-Range',''))
      size=int(content_range[1]) if content_range else int(r.headers.get('Content-Length') or 0)
     ranged=r.status==206
     if ranged and not re.match('bytes '+str(offset)+'-',r.headers.get('Content-Range','')):raise ValueError('下载源返回的续传位置不匹配')
     target=path if not offset or ranged else fresh
     downloaded=offset if ranged else 0;last=0
     with target.open('ab' if ranged else 'wb') as f:
      while True:
       chunk=r.read(256*1024)
       if not chunk:break
       f.write(chunk);downloaded+=len(chunk)
       if size and downloaded>size:raise ValueError('官方包大小超过更新元数据，已停止下载')
       if time.monotonic()-last>.5:
        emit('本机下载官方包 %.1f MB'%(downloaded/1048576),phase='download',downloaded_bytes=downloaded,total_bytes=size);last=time.monotonic()
     if target==fresh:os.replace(str(fresh),str(path))
    if digest(path)!=spec['sha256']:
     path.unlink();raise ValueError('本机官方包 SHA256 校验失败，已移除错误文件')
    emit('本机官方包 SHA256 校验通过',phase='verify',downloaded_bytes=path.stat().st_size,total_bytes=size)
    return path
   except ValueError:raise
   except Exception:
    # A complete cached file may have produced HTTP 416 on a retried request.
    if path.exists() and digest(path)==spec['sha256']:return path
    emit('本机下载暂时中断，保留缓存并重试',phase='download')
 raise ValueError('服务器和本机均未能下载官方包；请确认本机代理可访问 GitHub 后重试，下载缓存已保留。')

UPLOAD_SCRIPT=r'''
import os,pwd,json,hashlib,fcntl,sys
from pathlib import Path
os.umask(0o077)
spec=json.loads(sys.argv[1]);home=Path(pwd.getpwuid(os.geteuid()).pw_dir).resolve();cache=home/'.cache/linx-remote'
cache.mkdir(parents=True,exist_ok=True)
assert home in cache.resolve().parents,'cache outside account home'
name=spec['name'];assert '/' not in name and '\\' not in name and name.startswith('codex-')
archive=cache/name;part=cache/(name+'.bridge.part');size=spec['size'];sha=spec['sha256']
assert not archive.is_symlink() and not part.is_symlink(),'cache symlink rejected'
def hashed(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
with (cache/'environment.lock').open('a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 complete=archive.exists() and hashed(archive)==sha
 offset=size if complete else part.stat().st_size if part.exists() else 0
 print(json.dumps({'offset':offset,'sha256':sha if complete else hashed(part) if part.exists() else hashlib.sha256(b'').hexdigest()}),flush=True)
 control=json.loads(sys.stdin.buffer.readline())
 if not complete:
  if control.get('reset'):offset=0
  assert 0<=offset<=size
  with part.open('ab' if offset else 'wb') as f:
   left=size-offset
   while left:
    b=sys.stdin.buffer.read(min(262144,left))
    if not b:raise ValueError('SSH upload interrupted; partial cache retained')
    f.write(b);left-=len(b)
  assert hashed(part)==sha,'SSH upload SHA256 mismatch'
  os.replace(str(part),str(archive))
 print(json.dumps({'verified':True}),flush=True)
'''

def upload(spec,path,ssh,hidden,emit):
 validate(spec);payload=dict(spec,size=path.stat().st_size)
 command='python3 -u -c '+shlex.quote(UPLOAD_SCRIPT)+' '+shlex.quote(json.dumps(payload))
 proc=subprocess.Popen(ssh+[command],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,**hidden)
 import threading
 timer=threading.Timer(900,proc.kill);timer.daemon=True;timer.start()
 try:
  ready=json.loads(proc.stdout.readline());offset=int(ready['offset']);size=path.stat().st_size
  reset=offset>size or offset<0 or digest(path,offset)!=ready.get('sha256')
  if reset:offset=0
  proc.stdin.write((json.dumps({'reset':reset})+'\n').encode());proc.stdin.flush()
  emit('通过 SSH 传输官方包到个人目录缓存',phase='download',transfer=True,downloaded_bytes=offset,total_bytes=size)
  with path.open('rb') as f:
   f.seek(offset);sent=offset;last=0
   while True:
    chunk=f.read(256*1024)
    if not chunk:break
    proc.stdin.write(chunk);sent+=len(chunk)
    if time.monotonic()-last>.5:
     emit('SSH 已传输 %.1f MB / %.1f MB'%(sent/1048576,size/1048576),phase='download',transfer=True,downloaded_bytes=sent,total_bytes=size);last=time.monotonic()
  proc.stdin.close();verified=json.loads(proc.stdout.readline());code=proc.wait(timeout=60)
  if code or not verified.get('verified'):raise ValueError('服务器未能确认官方包校验结果')
  emit('SSH 传输及服务器 SHA256 校验通过，继续安装',phase='verify',downloaded_bytes=size,total_bytes=size)
 except Exception as e:
  raise ValueError('SSH 传输未完成；本机和服务器缓存已保留，请检查连接后重试。') from e
 finally:
  timer.cancel()
  if proc.poll() is None:proc.kill();proc.wait()

def bridge(spec,state,ssh,hidden,emit):
 path=local_download(spec,state,emit);upload(spec,path,ssh,hidden,emit)

def latest_package(system,machine):
 targets={('Linux','x86_64'):'x86_64-unknown-linux-musl',('Linux','aarch64'):'aarch64-unknown-linux-musl',('Darwin','x86_64'):'x86_64-apple-darwin',('Darwin','arm64'):'aarch64-apple-darwin'}
 target=targets.get((system,machine))
 for opener in openers():
  try:
   req=urllib.request.Request('https://api.github.com/repos/openai/codex/releases/latest',headers={'User-Agent':'LinX-Remote/1.7','Accept':'application/vnd.github+json'})
   with opener.open(req,timeout=12) as r:release=json.load(r)
   tag=release.get('tag_name','')
   if not re.fullmatch(r'rust-v\d+\.\d+\.\d+',tag) or release.get('prerelease'):continue
   ver=tag[6:];asset=next((a for a in release.get('assets',[]) if a['name']=='codex-package-'+str(target)+'.tar.gz'),None)
   if asset and re.fullmatch(r'sha256:[0-9a-f]{64}',asset.get('digest') or ''):return ver,dict(version=ver,target=target,sha256=asset['digest'][7:],size=asset.get('size',0))
  except Exception:pass
  # GitHub API limits are separate from the official release website.
  try:
   with opener.open('https://github.com/openai/codex/releases/latest',timeout=12) as r:url=r.geturl()
   m=re.fullmatch(r'https://github.com/openai/codex/releases/tag/(rust-v\d+\.\d+\.\d+)',url)
   if not m:continue
   tag=m[1];ver=tag[6:];asset='codex-package-'+str(target)+'.tar.gz'
   with opener.open('https://github.com/openai/codex/releases/expanded_assets/'+tag,timeout=15) as r:html=r.read(2*1024*1024).decode('utf-8')
   rows=re.findall(r'<li\b[^>]*>.*?</li>',html,re.S)
   row=next((row for row in rows if 'href="/openai/codex/releases/download/'+tag+'/'+asset+'"' in row),None)
   sha=re.search(r'sha256:([0-9a-f]{64})',row or '')
   if sha:return ver,dict(version=ver,target=target,sha256=sha[1],size=0)
  except Exception:pass
 raise ValueError('GitHub 官方更新信息暂时不可用，请确认本机代理后重试。')
