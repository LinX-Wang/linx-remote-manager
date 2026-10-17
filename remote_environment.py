"""Account-scoped environment preparation, Python 3.8+. No sudo/model requests."""
import fcntl,hashlib,json,os,platform,pwd,re,shlex,shutil,subprocess,sys,tarfile,tempfile,time
from pathlib import Path
VERSION='0.159.2'
DOWNLOAD_TOTAL=0
class DownloadError(ValueError):
 def __init__(self,url,path,digest,code):
  super().__init__('服务器下载官方包失败（curl '+str(code)+'）；将尝试由本机下载并通过 SSH 传输。')
  self.download_request={'url':url,'name':path.name,'sha256':digest,'size':DOWNLOAD_TOTAL,'curl_code':code}
PACKAGE_SIZES={'x86_64-unknown-linux-musl':159961162,'aarch64-unknown-linux-musl':150270060,'aarch64-apple-darwin':129522674,'x86_64-apple-darwin':140813958}
# Complete codex-package archives; hashes verified against official GitHub assets.
ASSETS={
 ('Linux','x86_64'):('x86_64-unknown-linux-musl','9e2d29a713b94478b240dec2f10e11324cd05fad76dc43e7c639bdf8a1337a6b'),
 ('Linux','aarch64'):('aarch64-unknown-linux-musl','05a524a463cadf7e3e22c7f923539c0d0b74c3e78b1f5f1fab52e50e6fb3312f'),
 ('Darwin','arm64'):('aarch64-apple-darwin','38aaf6dce63099fd10988948d03bbc6c0474253aef6961fcbe60f8d154b39101'),
 ('Darwin','x86_64'):('x86_64-apple-darwin','6b9b38bfad6ac8019aa6a243ee3ab11d3e22889eafd5458b0344cf20e797e680')}
def emit(message,**data):
 if 'phase' not in data:
  if data.get('done'):data['phase']='done'
  elif '校验通过' in message:data['phase']='verify'
  elif '切换新版' in message or '已安装' in message:data['phase']='install'
  elif '下载' in message or '断点续传' in message:data['phase']='download'
  else:data['phase']='check'
 print(json.dumps(dict(message=message,**data),ensure_ascii=False),flush=True)
def run(argv,**kw):return subprocess.run([str(x) for x in argv],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=kw.pop('timeout',40),**kw)
def version(path,kind):
 try:
  r=run([path,'--version'],timeout=12);line=r.stdout.splitlines()[0][:160]
  if r.returncode or (kind=='gawk' and not line.startswith('GNU Awk')) or (kind=='codex' and not re.search(r'codex\S*\s+\d+\.',line)):return None
  return line
 except (OSError,subprocess.TimeoutExpired,IndexError):return None
def interval_ok(path):
 try:return run([path,'BEGIN {if ("aaaa" ~ /^a{4}$/) exit 0;exit 1}'],timeout=8).returncode==0
 except (OSError,subprocess.TimeoutExpired,TypeError):return False
def within(path,home):return path==home or home in path.parents
def private_write(path,content,mode=0o600):
 path.parent.mkdir(parents=True,exist_ok=True);fd,tmp=tempfile.mkstemp(prefix='.linx-',dir=str(path.parent))
 try:
  with os.fdopen(fd,'w') as f:f.write(content)
  os.chmod(tmp,mode);os.replace(tmp,str(path))
 finally:
  if os.path.exists(tmp):os.unlink(tmp)
def safe_locations(home,paths):
 for p in paths:
  if not within(p.resolve(),home):raise ValueError('个人目录中的路径指向其他账号或系统目录，未修改：'+str(p))
def shell_path(home):
 begin='# >>> LinX personal tools >>>';block=begin+'\nexport PATH="$HOME/.local/bin:$PATH"\n# <<< LinX personal tools <<<\n';changed=[]
 for name in ('.profile','.bashrc'):
  p=home/name
  if p.is_symlink():raise ValueError(name+' 是符号链接，未修改；请自行将 ~/.local/bin 加入 SSH 的 PATH')
  raw=p.read_text() if p.exists() else ''
  if begin in raw:
   if block not in raw:raise ValueError(name+' 的 LinX 环境片段被修改，请先检查该片段')
   continue
  if p.exists():shutil.copy2(str(p),str(home/(name+'.linx-backup-'+str(time.time_ns()))))
  private_write(p,block+raw,p.stat().st_mode & 0o777 if p.exists() else 0o644);changed.append(name)
 return changed
def gawk_env_ok(path,env):
 try:
  r=run([path,'--version'],env=env,timeout=10)
  return r.returncode==0 and r.stdout.startswith('GNU Awk') and run([path,'BEGIN {exit !("aaaa" ~ /^a{4}$/)}'],env=env).returncode==0
 except (OSError,subprocess.TimeoutExpired):return False
def ensure_gawk(home,force=False):
 emit('检查 gawk 与 awk 语法支持…');paths=[home/'.local/bin/gawk',home/'bin/gawk']
 if shutil.which('gawk'):paths.append(Path(shutil.which('gawk')))
 found=None if force else next((p for p in paths if version(p,'gawk') and interval_ok(p)),None);installed=False
 if found is None:
  if platform.system()!='Linux' or not shutil.which('apt-get') or not shutil.which('dpkg-deb'):raise ValueError('未找到可用 gawk；自动安装目前支持 Debian/Ubuntu（apt-get 与 dpkg-deb）。其他系统请先在个人目录安装 GNU gawk，再重试。')
  emit('缺少 gawk，下载软件包并解压到个人目录…');root=home/'.local/share/linx-tools';root.mkdir(parents=True,exist_ok=True)
  pkgcache=home/'.cache/linx-remote/gawk';pkgcache.mkdir(parents=True,exist_ok=True)
  r=run(['apt-get','-o','Debug::NoLocking=1','-o','Acquire::Retries=3','download','gawk','libsigsegv2'],cwd=str(pkgcache),timeout=160)
  if r.returncode:raise ValueError('gawk 软件包下载失败；请检查软件源和网络后重试，个人目录缓存已保留。未执行系统安装。')
  with tempfile.TemporaryDirectory(prefix='gawk-',dir=str(root)) as td:
   stage=Path(td);os.chmod(td,0o755)
   extract=stage/'root';extract.mkdir()
   for pkg in sorted(pkgcache.glob('*.deb'),key=lambda p:p.stat().st_mtime):
    if run(['dpkg-deb','--extract',pkg,extract]).returncode:raise ValueError('gawk 软件包解压失败')
   libs=[str(p) for p in extract.rglob('*') if p.is_dir() and ('lib' in p.parts or 'lib64' in p.parts)]
   if not gawk_env_ok(extract/'usr/bin/gawk',dict(os.environ,LD_LIBRARY_PATH=':'.join(libs))):raise ValueError('gawk 缺少匹配的运行库，未启用；请由管理员提供个人目录版本。')
   dest=root/('gawk-'+str(time.time_ns()));os.replace(str(extract),str(dest))
   relibs=[str(dest/Path(p).relative_to(extract)) for p in libs];found=root/'gawk'
   private_write(found,'#!/bin/sh\nif [ -n "$LD_LIBRARY_PATH" ]; then\n export LD_LIBRARY_PATH='+shlex.quote(':'.join(relibs))+':"$LD_LIBRARY_PATH"\nelse\n export LD_LIBRARY_PATH='+shlex.quote(':'.join(relibs))+'\nfi\nexec '+shlex.quote(str(dest/'usr/bin/gawk'))+' "$@"\n',0o700);installed=True
  emit('gawk 已安装到个人目录')
 bindir=home/'.local/bin';changed=[]
 for alias,kind in ((bindir/'gawk','gawk'),(bindir/'awk','awk')):
  if force:
   if alias.is_symlink() and alias.resolve()==found.resolve():continue
   if os.path.lexists(str(alias)):
    backup=alias.with_name(alias.name+'.linx-backup-'+str(time.time_ns()));os.replace(str(alias),str(backup))
   bindir.mkdir(parents=True,exist_ok=True);alias.symlink_to(found);changed.append(str(alias));continue
  if interval_ok(alias) and (kind=='awk' or version(alias,'gawk')):continue
  if os.path.lexists(str(alias)):raise ValueError(str(alias)+' 已存在但不可用，未覆盖；请先检查此文件。')
  default=shutil.which(kind)
  if default and interval_ok(default) and (kind=='awk' or version(default,'gawk')):continue
  bindir.mkdir(parents=True,exist_ok=True);alias.symlink_to(found);changed.append(str(alias))
 default=shutil.which('awk');profiles=[] if default and interval_ok(default) else shell_path(home)
 effective=bindir/'awk' if (bindir/'awk').exists() else default
 if not interval_ok(effective):raise ValueError('gawk 可运行，但 awk 入口尚未就绪；请检查个人目录 PATH')
 return dict(path=str(found),version=version(found,'gawk'),installed=installed,aliases=changed,shell_files=profiles)
def layout(root):
 required=['codex-package.json','bin/codex','bin/codex-code-mode-host','codex-path/rg','codex-resources']
 if platform.system()=='Linux':required.append('codex-resources/bwrap')
 if not all((root/p).exists() for p in required):return False
 try:return json.loads((root/'codex-package.json').read_text()).get('layoutVersion')==1 and bool(version(root/'bin/codex','codex')) and all(os.access(str(root/p),os.X_OK) for p in required if p!='codex-package.json')
 except (OSError,ValueError):return False
def unpack(archive,dest):
 with tarfile.open(str(archive),'r:gz') as tf:
  entries=tf.getmembers()
  if len(entries)>20000 or sum(m.size for m in entries)>2*1024**3:raise ValueError('Codex 安装包超出预期大小')
  for m in entries:
   target=(dest/m.name).resolve()
   if not within(target,dest.resolve()) or m.isdev() or m.isfifo():raise ValueError('Codex 安装包包含不安全路径')
   if m.issym() or m.islnk():
    link=(target.parent/m.linkname if m.issym() else dest/m.linkname).resolve()
    if not within(link,dest.resolve()):raise ValueError('Codex 安装包包含越界链接')
   tf.extract(m,str(dest))
def fetch_with_progress(argv,path):
 proc=subprocess.Popen([str(x) for x in argv]+['--write-out','%{http_code}'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
 deadline=time.monotonic()+520;last=-1
 try:
  while proc.poll() is None:
   if time.monotonic()>deadline:proc.kill();proc.wait();return 28
   size=path.stat().st_size if path.exists() else 0
   if size!=last:emit('Codex 已下载 %.1f MB；网络中断后可继续'%(size/1024**2),downloaded_bytes=size,total_bytes=DOWNLOAD_TOTAL,phase='download');last=size
   time.sleep(2)
  status=proc.stdout.read().decode('ascii',errors='ignore')[-3:]
  return 33 if proc.returncode==22 and status=='416' else proc.returncode
 finally:
  if proc.poll() is None:proc.kill();proc.wait()
def download(url,path,digest):
 if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()==digest:return
 curl=shutil.which('curl')
 if not curl:raise ValueError('服务器缺少 curl，暂时无法下载 Codex；请先提供 curl 后重试')
 emit('下载官方完整包 '+path.name+'，首次下载可能需要几分钟…')
 args=[curl,'--fail','--location','--proto','=https','--proto-redir','=https','--connect-timeout','10','--max-time','360','--retry','1','--retry-max-time','400']
 if path.exists():emit('发现下载缓存 %.1f MB，尝试断点续传…'%(path.stat().st_size/1024**2))
 code=fetch_with_progress(args+['--continue-at','-','--output',path,url],path)
 if code in (33,36):
  emit('下载源不支持断点续传，改用完整下载并保留原缓存…');fresh=path.with_suffix('.fresh')
  code=fetch_with_progress(args+['--output',fresh,url],fresh)
  if code==0:os.replace(str(fresh),str(path))
 if code:raise DownloadError(url,path,digest,code)
 emit('下载完成，正在验证 SHA256…',phase='verify',downloaded_bytes=path.stat().st_size,total_bytes=DOWNLOAD_TOTAL)
 if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:path.unlink();raise ValueError('Codex SHA256 校验失败，已移除下载文件；请重试。')
def link_absent(path,target):
 if os.path.lexists(str(path)):
  if path.resolve()==target.resolve():return
  raise ValueError(str(path)+' 已存在，未覆盖；请检查此安装入口')
 path.parent.mkdir(parents=True,exist_ok=True);path.symlink_to(target)
def finish_codex(home,codex_home,package):
 current=codex_home/'packages/standalone/current'
 link_absent(current,package)
 entry=package/'codex'
 if not entry.exists():
  if not within(package.resolve(),home):raise ValueError('可复用的 Codex 不含完整兼容入口；未修改系统安装')
  link_absent(entry,package/'bin/codex')
 link_absent(codex_home/'bin/codex',package/'bin/codex')
 if codex_home==home/'.xdt-server/v1/codex-home':
  sentinel=codex_home.parent/'.installed-codex'
  if not sentinel.exists():sentinel.touch()
def ensure_codex(home,codex_home):
 global DOWNLOAD_TOTAL
 emit('检查 Codex 完整安装与运行能力…');binary=codex_home/'bin/codex';roots=[codex_home/'packages/standalone/current']
 if binary.exists():roots.append(binary.resolve().parent.parent)
 valid=next((p for p in roots if layout(p)),None)
 if version(binary,'codex') and valid:
  finish_codex(home,codex_home,valid.resolve());return dict(path=str(binary),version=version(binary,'codex'),installed=False)
 if os.path.lexists(str(binary)):raise ValueError('现有 Codex 不完整或无法运行，已保留原文件；请检查：'+str(codex_home))
 system=shutil.which('codex')
 if system and layout(Path(system).resolve().parent.parent):valid=Path(system).resolve().parent.parent
 if valid:
  finish_codex(home,codex_home,valid.resolve());return dict(path=str(binary),version=version(binary,'codex'),installed=False,reused=True)
 asset=ASSETS.get((platform.system(),platform.machine()))
 if not asset:raise ValueError('暂不支持此服务器架构自动安装 Codex：'+platform.system()+'/'+platform.machine())
 target,digest=asset;DOWNLOAD_TOTAL=PACKAGE_SIZES.get(target,0);cache=home/'.cache/linx-remote';cache.mkdir(parents=True,exist_ok=True);archive=cache/('codex-package-'+VERSION+'-'+target+'.tar.gz')
 base=codex_home/'packages/standalone'
 if os.path.lexists(str(base/'current')):raise ValueError('Codex current 已存在但不完整，未覆盖；请检查 '+str(base/'current'))
 download('https://github.com/openai/codex/releases/download/rust-v'+VERSION+'/codex-package-'+target+'.tar.gz',archive,digest)
 emit('校验通过，准备 Codex 安装目录…');base.mkdir(parents=True,exist_ok=True)
 with tempfile.TemporaryDirectory(prefix='.prepare-',dir=str(base)) as td:
  stage=Path(td);unpack(archive,stage);choices=[stage]+[p.parent for p in stage.glob('*/codex-package.json')]
  package=next((p for p in choices if layout(p)),None)
  if package is None:raise ValueError('Codex 包布局或运行检查失败，未启用安装')
  release=base/'releases'/(VERSION+'-'+target+'-linx-'+str(time.time_ns()));release.parent.mkdir(parents=True,exist_ok=True);os.replace(str(package),str(release))
 finish_codex(home,codex_home,release)
 emit('Codex 已安装并通过运行检查');return dict(path=str(binary),version=version(binary,'codex'),installed=True)
def prepare(request):
 account=pwd.getpwuid(os.geteuid());home=Path(account.pw_dir).resolve();user=account.pw_name
 codex_home=Path(request.get('home') or str(home/'.xdt-server/v1/codex-home')).expanduser().resolve()
 if not within(codex_home,home) or codex_home==home:
  codex_home=home/'.xdt-server/v1/codex-home';emit('连接设置中的旧路径不属于当前账号，改用 '+str(codex_home))
 safe_locations(home,[home/'.local/bin',home/'.local/share/linx-tools',home/'.cache/linx-remote',codex_home/'bin',codex_home/'packages/standalone'])
 emit('已识别登录账号 '+user+'，个人目录 '+str(home),user=user,home=str(home),codex_home=str(codex_home))
 lockdir=home/'.cache/linx-remote';lockdir.mkdir(parents=True,exist_ok=True)
 with (lockdir/'environment.lock').open('a') as f:
  try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:raise ValueError('该账号已有环境准备任务，请稍后查看结果')
  gawk=ensure_gawk(home);codex=ensure_codex(home,codex_home)
 return dict(user=user,home=str(home),codex_home=str(codex_home),gawk=gawk,codex=codex)
def inspect_updates(request):
 account=pwd.getpwuid(os.geteuid());home=Path(account.pw_dir).resolve()
 c=Path(request.get('home') or str(home/'.xdt-server/v1/codex-home')).resolve()
 if not within(c,home):c=home/'.xdt-server/v1/codex-home'
 g=next((p for p in [home/'.local/bin/gawk',home/'bin/gawk',shutil.which('gawk')] if p and version(p,'gawk')),None)
 candidate=None
 if shutil.which('apt-cache'):
  try:
   r=run(['apt-cache','policy','gawk'],env=dict(os.environ,LC_ALL='C'),timeout=15)
   m=re.search(r'Candidate:\s*(\S+)',r.stdout);candidate=m.group(1) if m and m.group(1)!='(none)' else None
  except (OSError,subprocess.TimeoutExpired):pass
 return dict(user=account.pw_name,home=str(home),codex=version(c/'bin/codex','codex'),gawk=version(g,'gawk') if g else None,gawk_candidate=candidate,system=platform.system(),machine=platform.machine())
def switch_link(path,target):
 tmp=path.with_name('.linx-link-'+str(time.time_ns()));tmp.symlink_to(target)
 try:os.replace(str(tmp),str(path))
 finally:
  if tmp.is_symlink():tmp.unlink()
def install_update(request):
 global DOWNLOAD_TOTAL
 import base64
 account=pwd.getpwuid(os.geteuid());home=Path(account.pw_dir).resolve();c=Path(request['home']).resolve()
 if not within(c,home) or c==home:raise ValueError('更新目录必须在当前登录账号的个人目录内')
 safe_locations(home,[home/'.local/bin',home/'.local/share/linx-tools',home/'.cache/linx-remote',c/'bin',c/'packages/standalone'])
 cache=home/'.cache/linx-remote';cache.mkdir(parents=True,exist_ok=True)
 with (cache/'environment.lock').open('a') as lock:
  try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:raise ValueError('已有环境准备或更新任务，请稍后重试')
  if request['tool']=='gawk':
   emit('正在将新版 gawk 安装到个人目录，原入口会保留备份…')
   r=ensure_gawk(home,force=True);return dict(tool='gawk',version=r['version'],updated=True)
  spec=request['package'];ver=spec['version'];target=spec['target'];digest=spec['sha256'];DOWNLOAD_TOTAL=int(spec.get('size') or 0)
  if not re.fullmatch(r'\d+\.\d+\.\d+',ver) or not re.fullmatch(r'[A-Za-z0-9_.-]+',target) or not re.fullmatch(r'[0-9a-f]{64}',digest):raise ValueError('更新包元数据无效，请重新检查更新')
  ns={'__name__':'linx_update_support'};exec(compile(base64.b64decode(request['runtime_source']),'<linx-runtime-support>','exec'),ns)
  ns['idle_snapshot'](c)
  archive=cache/('codex-package-'+ver+'-'+target+'.tar.gz')
  download('https://github.com/openai/codex/releases/download/rust-v'+ver+'/codex-package-'+target+'.tar.gz',archive,digest)
  base=c/'packages/standalone';base.mkdir(parents=True,exist_ok=True)
  with tempfile.TemporaryDirectory(prefix='.update-',dir=str(base)) as td:
   stage=Path(td);unpack(archive,stage);package=next((p for p in [stage]+[x.parent for x in stage.glob('*/codex-package.json')] if layout(p)),None)
   if package is None or version(package/'bin/codex','codex').split()[-1]!=ver:raise ValueError('新 Codex 包运行或版本检查失败，旧版保持不变')
   release=base/'releases'/(ver+'-'+target+'-linx-'+str(time.time_ns()));release.parent.mkdir(parents=True,exist_ok=True);os.replace(str(package),str(release))
  if not (release/'codex').exists():link_absent(release/'codex',release/'bin/codex')
  state=c/'portable-providers';state.mkdir(mode=0o700,exist_ok=True)
  with (state/'lock').open('a') as provider_lock:
   try:fcntl.flock(provider_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except BlockingIOError:raise ValueError('供应商配置正在修改，下载包已保留，请稍后重试更新')
   before=ns['idle_snapshot'](c);current=base/'current';binary=c/'bin/codex'
   if not current.is_symlink() or not binary.is_symlink():raise ValueError('现有 Codex 入口不是受管理的符号链接，未覆盖；请先检查安装')
   previous=current.resolve();previous_bin=binary.resolve()
   if not within(previous,home) or not within(previous_bin,home):raise ValueError('现有 Codex 指向个人目录之外，未修改系统安装')
   route=ns['route_view']((c/'config.toml').read_text())['root'];provider=route.get('model_provider','openai');model=route.get('model','')
   eligible={t['provider'] for t in before.get('saved_threads',[])+before['threads'] if t.get('provider')}
   emit('远程任务已空闲，切换新版 Codex 并恢复原有对话…')
   try:
    switch_link(current,release);switch_link(binary,current/'bin/codex')
    ns['refresh_runtime'](c,before,provider,model,eligible,preserve_models=True)
   except Exception:
    switch_link(current,previous);switch_link(binary,previous_bin)
    try:ns['refresh_runtime'](c,ns['idle_snapshot'](c),provider,model,eligible,preserve_models=True)
    except Exception:raise ValueError('新版启用失败，旧安装入口已恢复；服务恢复未确认，请检查远程连接后重试')
    raise ValueError('新版启用失败，已恢复旧版安装入口与服务，请检查兼容性后重试')
   private_write(cache/'last-codex-update.json',json.dumps({'old':str(previous),'new':str(release),'version':ver}))
 return dict(tool='Codex',version=version(c/'bin/codex','codex'),updated=True,previous=str(previous))
if __name__=='__main__':
 try:
  request=json.loads(sys.stdin.readline());result=inspect_updates(request) if request.get('action')=='updates' else install_update(request) if request.get('action')=='install_update' else prepare(request)
  emit('检查完成',done=True,result=result)
 except Exception as e:emit(str(e) if isinstance(e,ValueError) else '环境准备未完成：'+type(e).__name__+'；请检查目录权限、网络及剩余空间后重试。',failed=True,**({'download_request':e.download_request} if isinstance(e,DownloadError) else {}))
