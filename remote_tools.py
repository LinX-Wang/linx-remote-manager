"""SSH-only files, streamed transfers, diagnostics and process identity probes."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import secrets
import shlex
import subprocess
import tempfile
import threading
import time
import urllib.request

WORKER = r'''
import hashlib,json,os,socket,stat,sys,time,urllib.request,urllib.error
from pathlib import Path
request=json.loads(sys.stdin.buffer.readline(65536))
action=request['action']
def reply(value):
    print(json.dumps(value,ensure_ascii=True),flush=True)
def regular(path):
    path=os.path.abspath(os.path.expanduser(path))
    real=os.path.realpath(path)
    if any(real==p or real.startswith(p+'/') for p in ('/proc','/dev','/sys')):
        raise ValueError('不能传输设备或进程文件')
    return path
def info(path):
    path=regular(path)
    fd=os.open(path,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW)
    f=os.fdopen(fd,'rb');st=os.fstat(f.fileno())
    if not stat.S_ISREG(st.st_mode):
        f.close();raise ValueError('只能下载普通文件，不支持符号链接或管道')
    return f,st
try:
    if action=='list':
        path=os.path.realpath(os.path.expanduser(request.get('path') or '~'))
        offset=max(0,int(request.get('offset',0)))
        entries=[];count=0
        with os.scandir(path) as directory:
            for item in directory:
                if count>=offset and len(entries)<200:
                    try:
                        st=item.stat(follow_symlinks=False)
                        kind='directory' if stat.S_ISDIR(st.st_mode) else 'file' if stat.S_ISREG(st.st_mode) else 'link' if stat.S_ISLNK(st.st_mode) else 'special'
                        entries.append(dict(name=item.name,path=item.path,kind=kind,size=st.st_size,modified_at=st.st_mtime))
                    except OSError:pass
                count+=1
                if count>=offset+201:break
        entries.sort(key=lambda x:(x['kind']!='directory',x['name'].casefold()))
        reply(dict(path=path,parent=os.path.dirname(path),entries=entries,offset=offset,more=count>offset+200))
    elif action=='stat':
        f,st=info(request['path']);f.close()
        reply(dict(size=st.st_size,modified_ns=st.st_mtime_ns,inode=st.st_ino,device=st.st_dev,name=os.path.basename(request['path'])))
    elif action=='download':
        f,st=info(request['path']);expected=request['expected']
        if any(getattr(st,k)!=expected[v] for k,v in [('st_size','size'),('st_mtime_ns','modified_ns'),('st_ino','inode'),('st_dev','device')]):
            f.close();raise ValueError('文件已变化，请刷新后重新下载')
        digest=hashlib.sha256()
        with f:
            remaining=st.st_size
            while remaining:
                chunk=f.read(min(1024*1024,remaining))
                if not chunk:raise ValueError('文件在传输中被截断')
                sys.stdout.buffer.write(chunk);digest.update(chunk);remaining-=len(chunk)
            after=os.fstat(f.fileno())
            if after.st_size!=st.st_size or after.st_mtime_ns!=st.st_mtime_ns:raise ValueError('文件在传输中被修改')
        sys.stdout.buffer.flush()
        sys.stderr.write(json.dumps(dict(sha256=digest.hexdigest())))
    elif action=='upload':
        folder=os.path.realpath(request['directory']);name=request['name']
        if not name or name in ('.','..') or any(c in name for c in ('/',chr(92),chr(0))):raise ValueError('文件名无效')
        destination=regular(os.path.join(folder,name))
        if os.path.lexists(destination):raise ValueError('同名文件已存在，请改名后上传')
        size=int(request['size']);temporary=os.path.join(folder,'.linx-upload-'+request['id']+'.part')
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        try:
            digest=hashlib.sha256();remaining=size
            with os.fdopen(fd,'wb') as f:
                while remaining:
                    chunk=sys.stdin.buffer.read(min(1024*1024,remaining))
                    if not chunk:raise ValueError('上传中断，临时文件已移除')
                    f.write(chunk);digest.update(chunk);remaining-=len(chunk)
                f.flush();os.fsync(f.fileno())
            expected=sys.stdin.buffer.readline(100).decode().strip()
            if digest.hexdigest()!=expected:raise ValueError('SHA256 校验失败，未写入目标文件')
            os.link(temporary,destination)
            reply(dict(sha256=digest.hexdigest(),size=size))
        finally:os.unlink(temporary)
    elif action=='identities':
        result={}
        with open('/proc/mounts') as f:
            hidden=any('hidepid=' in line and not 'hidepid=0' in line for line in f if ' /proc ' in line)
        for pid in request['pids'][:64]:
            pid=str(int(pid))
            try:
                with open('/proc/'+pid+'/stat') as f:fields=f.read().rsplit(')',1)[1].split()
                result[pid]=dict(status='exited' if fields[0] in ('Z','X') else 'running',ticks=fields[19])
            except FileNotFoundError:result[pid]=dict(status='unknown' if hidden else 'missing')
            except (OSError,IndexError):result[pid]=dict(status='unknown')
        with open('/proc/sys/kernel/random/boot_id') as f:boot=f.read().strip()
        reply(dict(boot_id=boot,processes=result))
    elif action=='diagnostics':
        home=Path(request.get('home',''));rows=[dict(name='SSH / Python',status='ok',detail='已完成 SSH 登录并运行只读检查')]
        def row(name,status,detail):rows.append(dict(name=name,status=status,detail=detail))
        def get(url,key=None):
            headers={'User-Agent':'LinX-Diagnostics/1'}
            if key:headers['Authorization']='Bearer '+key
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self,*args):return None
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
            try:
                with opener.open(urllib.request.Request(url,headers=headers),timeout=4) as f:return f.status,f.read(65536)
            except urllib.error.HTTPError as e:return e.code,b''
            except urllib.error.URLError as e:
                reason=e.reason
                if isinstance(reason,socket.gaierror):raise ValueError('DNS 解析失败，请检查服务器 DNS 或供应商域名')
                if isinstance(reason,(TimeoutError,socket.timeout)):raise ValueError('连接超时，请检查服务器到中转站的网络')
                if isinstance(reason,ConnectionRefusedError):raise ValueError('目标端口拒绝连接，请检查服务和隧道是否运行')
                raise ValueError('连接失败，请检查 TLS 证书、服务地址和网络')
            except (TimeoutError,socket.timeout):raise ValueError('响应超时，请检查中转站状态')
        available=os.access(str(home/'bin/codex'),os.X_OK)
        row('Codex 文件','ok' if available else 'warning','现有可执行文件可访问' if available else '未找到可执行 Codex；诊断不会安装')
        try:
            db=json.loads((home/'portable-providers/profiles.json').read_text())
            profile=next((p for p in db.get('profiles',[]) if p['id']==db.get('active')),None)
        except (OSError,ValueError):db={};profile=None
        bridge=(db.get('model_catalog') or {}).get('compat_proxy')
        if bridge:
            try:
                code,raw=get('http://127.0.0.1:'+str(int(bridge['port']))+'/health');health=json.loads(raw)
                valid=code==200 and health.get('linx_compat_version') and health.get('pid')==bridge.get('pid')
                row('远端兼容桥','ok' if valid else 'error','身份及健康检查通过' if valid else '端口响应与预期兼容桥不符')
            except Exception:row('远端兼容桥','error','健康检查失败；检查兼容桥是否仍在运行')
        else:row('远端兼容桥','skip','当前配置没有记录兼容桥，未探测其他端口')
        if profile:
            try:
                start=time.monotonic();code,raw=get(profile['base_url'].rstrip('/')+'/models',profile.get('key'))
                status='ok' if code==200 else 'warning' if code in (404,405) else 'error'
                detail='HTTP '+str(code)+' · '+str(round((time.monotonic()-start)*1000))+' ms；仅检查模型列表，不生成推理请求'
                if code in (401,403):detail+='；检查供应商密钥或权限'
                if code==502:detail+='；上游或中转暂不可用'
                if code==200:
                    try:
                        body=json.loads(raw)
                        if not isinstance(body,dict) or not isinstance(body.get('data'),list):status='warning';detail+='；返回内容不是标准模型列表'
                    except ValueError:status='warning';detail+='；返回内容不是 JSON 模型列表'
                row('远端 → 当前供应商',status,detail)
            except Exception as e:row('远端 → 当前供应商','error',str(e) if isinstance(e,ValueError) else '连接失败或超时；检查供应商地址、DNS 和中转站连通性')
            if profile.get('mode')=='tunnel':
                try:
                    code,raw=get('http://127.0.0.1:'+str(int(request['remote_port']))+'/health')
                    row('远端 → 本机隧道','ok' if code==200 else 'error','HTTP '+str(code)+'；健康路径可达，需结合本机服务检查')
                except Exception as e:row('远端 → 本机隧道','error',str(e) if isinstance(e,ValueError) else '无法经远端回环端口访问本机服务')
            else:row('远端 → 本机隧道','skip','当前供应商为直连模式，无需本机隧道')
        else:row('远端 → 当前供应商','skip','尚未启用供应商')
        reply(dict(rows=rows))
    else:raise ValueError('未知操作')
except Exception as exc:
    if action=='download':
        sys.stderr.write(json.dumps(dict(error=str(exc) if isinstance(exc,ValueError) else '文件不可读或权限不足')));sys.exit(1)
    reply(dict(error=str(exc) if isinstance(exc,ValueError) else '路径不存在、没有权限或连接不可用'))
'''

def command(settings):
    import manager
    ssh,destination=manager.ssh_base(settings)
    code='import base64;exec(compile(base64.b64decode('+repr(base64.b64encode(WORKER.encode()).decode())+'),"<linx-tools>","exec"))'
    return ssh+[destination,'python3 -c '+shlex.quote(code)]

def request(settings,action,**data):
    import manager
    try:
        result=manager.ssh_run(settings,command(settings),input=(json.dumps(dict(action=action,**data))+'\n').encode(),capture_output=True,timeout=28,**manager.HIDDEN)
        if result.returncode:raise ValueError('SSH 检查未完成，请确认登录和网络状态')
        reply=json.loads(result.stdout)
        if reply.get('error'):raise ValueError(reply['error'])
        return reply
    except subprocess.TimeoutExpired:raise ValueError('SSH 操作超时，请检查连接后重试')

def list_files(settings,path='',offset=0):
    if not isinstance(path,str) or len(path)>4096 or '\x00' in path:raise ValueError('目录路径无效')
    return request(settings,'list',path=path,offset=max(0,int(offset)))

def diagnostics(settings):
    import manager
    rows=[]
    try:rows.extend(request(settings,'diagnostics',home=settings.get('home',''),remote_port=settings.get('remote_port',18765))['rows'])
    except Exception:rows.append(dict(name='SSH / Python',status='error',detail='登录、网络或 Python 检查失败；先确认 SSH 登录和主机指纹'))
    tunnel=manager.tunnel_status(settings)
    rows.append(dict(name='本机隧道守护',status='ok' if tunnel.get('fresh') else 'skip',detail='本工具的隧道守护正在运行' if tunnel.get('fresh') else '没有活动的本工具隧道；直连模式无需启动'))
    try:
        url='http://127.0.0.1:'+str(int(settings.get('local_port',8080)))+'/health'
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url,timeout=3) as f:code=f.status;f.read(1024)
        rows.append(dict(name='本机中转服务',status='ok' if code==200 else 'warning',detail='健康接口 HTTP '+str(code)+'；仅证明该端口服务可达'))
    except Exception:rows.append(dict(name='本机中转服务',status='warning',detail='本机设置端口无健康响应；直连供应商可忽略此项'))
    return dict(rows=rows,checked_at=int(time.time()))

class Transfers:
    def __init__(self):
        self.lock=threading.RLock();self.jobs={}
    def public(self,job):
        return {k:v for k,v in job.items() if k not in ('settings','local','remote','directory')}
    def cleanup(self):
        import manager
        with self.lock:
            for key,job in list(self.jobs.items()):
                if job['state'] not in ('running','saving') and time.time()-job['updated']>3600:
                    if job.get('local'):Path(job['local']).unlink(missing_ok=True)
                    del self.jobs[key]
            active={j.get('local') for j in self.jobs.values()}
            for path in (manager.STATE/'transfers').glob('linx-download-*'):
                try:
                    if str(path) not in active and time.time()-path.stat().st_mtime>3600:path.unlink()
                except OSError:pass
    def begin(self,settings,direction,path='',directory='',name='',size=0):
        self.cleanup()
        if direction not in ('upload','download'):raise ValueError('传输方向无效')
        if direction=='upload':
            if not name or name in ('.','..') or any(c in name for c in ('/',chr(92),chr(0))):raise ValueError('文件名无效')
            if not isinstance(directory,str) or not directory.startswith('/') or '\x00' in directory:raise ValueError('请先选择上传目录')
            size=int(size)
            if not 0<=size<=100*1024**3:raise ValueError('单文件大小须在 0 至 100 GiB 之间')
        else:
            metadata=request(settings,'stat',path=path);size=metadata['size'];name=metadata['name']
        with self.lock:
            if len(self.jobs)>=32 or sum(j['state'] in ('running','pending') for j in self.jobs.values())>=4:raise ValueError('传输队列已满，请完成或清理现有传输')
            key=secrets.token_hex(32)
            job=dict(id=key,direction=direction,name=name,total=size,transferred=0,state='pending',updated=time.time(),settings=copy.deepcopy(settings),remote=path,directory=directory,sha256='',error='')
            self.jobs[key]=job
        if direction=='download':threading.Thread(target=self.download,args=(job,metadata),daemon=True).start()
        return self.public(job)
    def status(self,key):
        with self.lock:
            job=self.jobs.get(key)
            if not job:raise ValueError('传输不存在或已过期')
            return self.public(job)
    def update(self,job,**values):
        with self.lock:job.update(values,updated=time.time())
    def fail(self,job):
        self.update(job,state='failed',error='传输中断或校验失败，请检查连接和文件权限后重试；原目标未覆盖')
    def watchdog(self,job,process):
        # Only our dedicated SSH file-transfer child, never a server workload.
        while process.poll() is None:
            if time.time()-job['updated']>120:
                process.kill();return
            time.sleep(2)
    def download(self,job,metadata):
        import manager
        process=None;askpass=None
        self.update(job,state='running')
        directory=manager.STATE/'transfers';directory.mkdir(parents=True,exist_ok=True)
        fd,path=tempfile.mkstemp(prefix='linx-download-',dir=directory);job['local']=path
        try:
            with os.fdopen(fd,'wb') as target,tempfile.TemporaryFile() as errors:
                process,askpass=manager.ssh_popen(job['settings'],command(job['settings']),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=errors,**manager.HIDDEN)
                threading.Thread(target=self.watchdog,args=(job,process),daemon=True).start()
                process.stdin.write((json.dumps(dict(action='download',path=job['remote'],expected=metadata))+'\n').encode());process.stdin.close()
                digest=hashlib.sha256()
                while True:
                    chunk=process.stdout.read(1024*1024)
                    if not chunk:break
                    target.write(chunk);digest.update(chunk);self.update(job,transferred=job['transferred']+len(chunk))
                    if job['transferred']>job['total']:raise ValueError('文件大小变化')
                if process.wait(timeout=20):raise ValueError('下载中断')
                errors.seek(0);result=json.loads(errors.read()[-4096:])
                if job['transferred']!=job['total'] or result.get('sha256')!=digest.hexdigest():raise ValueError('校验失败')
            self.update(job,state='done',sha256=digest.hexdigest())
        except Exception:
            Path(path).unlink(missing_ok=True);self.fail(job)
        finally:
            if process:
                if process.poll() is None:process.kill();process.wait()
                if process.stdout:process.stdout.close()
            if askpass:askpass.unlink(missing_ok=True)
    def upload(self,key,source,length):
        import manager
        with self.lock:
            job=self.jobs.get(key)
            if not job or job['direction']!='upload' or job['state']!='pending' or length!=job['total']:raise ValueError('上传请求与已选文件不符')
            self.update(job,state='running')
        process=None;askpass=None
        try:
            with tempfile.TemporaryFile() as errors:
                process,askpass=manager.ssh_popen(job['settings'],command(job['settings']),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=errors,**manager.HIDDEN)
                threading.Thread(target=self.watchdog,args=(job,process),daemon=True).start()
                process.stdin.write((json.dumps(dict(action='upload',directory=job['directory'],name=job['name'],size=length,id=key))+'\n').encode())
                digest=hashlib.sha256();remaining=length
                while remaining:
                    chunk=source.read(min(1024*1024,remaining))
                    if not chunk:raise ValueError('上传连接已断开')
                    process.stdin.write(chunk);digest.update(chunk);remaining-=len(chunk);self.update(job,transferred=length-remaining)
                process.stdin.write((digest.hexdigest()+'\n').encode());process.stdin.close()
                raw=process.stdout.read(4096)
                if process.wait(timeout=20):raise ValueError('上传未完成')
                result=json.loads(raw)
                if result.get('error'):raise ValueError(result['error'])
                if result.get('sha256')!=digest.hexdigest():raise ValueError('校验失败')
                self.update(job,state='done',sha256=digest.hexdigest())
        except Exception as exc:
            self.fail(job)
            if isinstance(exc,ValueError):self.update(job,error=str(exc))
        finally:
            if process:
                if process.stdin and not process.stdin.closed:
                    try:process.stdin.close()
                    except (BrokenPipeError,OSError):pass
                if process.poll() is None:process.kill();process.wait()
                if process.stdout:process.stdout.close()
            if askpass:askpass.unlink(missing_ok=True)
        return self.public(job)
    def take(self,key):
        with self.lock:
            job=self.jobs.get(key)
            if not job or job['direction']!='download' or job['state']!='done' or time.time()-job['updated']>3600:raise ValueError('下载尚未完成或已过期')
            self.update(job,state='saving')
            return job
    def discard(self,key):
        with self.lock:
            job=self.jobs.get(key)
            if not job:return {}
            if job['state'] in ('running','saving'):raise ValueError('传输进行中，请等待完成后清理')
            if job.get('local'):Path(job['local']).unlink(missing_ok=True)
            del self.jobs[key]
        return {}

transfers=Transfers()
