"""Portable Chinese UI for remote Cindy Codex providers and reverse SSH."""
import argparse, base64, json, os, queue, re, shlex, shutil, socket, subprocess, sys, tempfile, threading, time, uuid, zipfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent
FROZEN = bool(getattr(sys, 'frozen', False))
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else ROOT
SETTINGS = APP_DIR / 'settings.json'
WINDOWS_APP = 'LinX Remote.exe'
DEFAULT = {'alias': '', 'host': '', 'user': '', 'port': 22, 'identity': '', 'auth_method': 'agent', 'home': '', 'remote_port': 18765, 'local_port': 8080, 'connection_source': 'cindy', 'cindy_host': ''}

def state_root(platform=None):
    platform = platform or sys.platform
    if platform == 'darwin': return Path.home() / 'Library/Application Support/CindyRemoteKit'
    if platform == 'win32': return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'CindyRemoteKit'
    return Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))) / 'CindyRemoteKit'
STATE = state_root()
HIDDEN = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
ENV_LOCK = threading.Lock()
SSH_PASSWORDS = {}
ENV_JOB = None
UPDATES_JOB = None
PACKAGE_FILES = ('connection_detection.py', 'download_bridge.py', 'cindy_connections.py', 'remote_environment.py', 'server_monitor.py', 'manager.py', 'remote_manager.py', 'web_app.py', 'manager_ui.html', 'launch.pyw', '启动管理器.vbs', '启动管理器.cmd', '诊断启动.cmd', '启动管理器.command', '使用说明.md', 'requirements.txt', 'LinX.svg', 'LinX.png', 'LinX.ico', 'LinX.icns', 'LinX 远程模型管理.app/Contents/Info.plist', 'LinX 远程模型管理.app/Contents/MacOS/launch', 'LinX 远程模型管理.app/Contents/Resources/LinX.icns')

def export_package(s, target):
    # Mark Unix permissions explicitly: Windows-built ZIPs must preserve the Mac launcher executable bit.
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as z:
        for name in PACKAGE_FILES:
            info = zipfile.ZipInfo('CindyRemoteKit/' + name)
            info.create_system = 3
            info.external_attr = ((0o100755 if (name.endswith('.command') or '/MacOS/' in name) else 0o100644) << 16)
            info.compress_type = zipfile.ZIP_DEFLATED
            content = (ROOT / name).read_bytes()
            if name.endswith('.cmd'):
                content = content.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
            elif name.endswith('.command'):
                content = content.replace(b'\r\n', b'\n')
            z.writestr(info, content)
        executable = Path(sys.executable) if FROZEN else APP_DIR / WINDOWS_APP
        if os.name == 'nt' and executable.is_file():
            z.write(executable, 'CindyRemoteKit/' + WINDOWS_APP)
        z.writestr('CindyRemoteKit/settings.json', json.dumps(s, ensure_ascii=False, indent=2))

def open_ssh_terminal(s):
    cmd, dest = ssh_base(s, batch=False)
    if os.name == 'nt':
        subprocess.Popen(cmd + [dest], creationflags=subprocess.CREATE_NEW_CONSOLE)
    elif sys.platform == 'darwin':
        # Arguments are shell quoted, never interpolated into AppleScript. No passwords/API keys.
        STATE.mkdir(parents=True, exist_ok=True)
        path = STATE / ('ssh-login-' + uuid.uuid4().hex + '.command')
        path.write_text('#!/bin/sh\nexec ' + shlex.join(cmd + [dest]) + '\n', encoding='utf-8')
        path.chmod(0o700)
        subprocess.run(['/usr/bin/open', '-a', 'Terminal', str(path)], check=True)
    else:
        return shlex.join(cmd + [dest])
    return None

def read_settings():
    path = SETTINGS if SETTINGS.exists() else ROOT / 'settings.json'
    return dict(DEFAULT, **(json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}))

def write_settings(s):
    SETTINGS.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding='utf-8')

def cindy_connections():
    from cindy_connections import read_connections
    return read_connections()

def cindy_selection(s,alias):
    found=next((h for h in cindy_connections()['hosts'] if h['id']==alias),None)
    if not found:raise ValueError('Cindy 中的这台服务器已移除或改名，请刷新服务器列表后重新选择')
    return dict(s,connection_source='cindy',cindy_host=alias,alias=alias,host='',identity='',auth_method='agent',user=found['user'] or s.get('user',''),port=found['port'])

def ssh_key(s):
    return '|'.join(str(s.get(k,'') or '') for k in ('connection_source','alias','host','user','port','identity'))

def set_ssh_password(s,password):
    key=ssh_key(s)
    if password:SSH_PASSWORDS[key]=str(password)
    elif s.get('auth_method')!='password':SSH_PASSWORDS.pop(key,None)

def ssh_auth_environment(s):
    if s.get('auth_method')!='password':return None,None
    password=SSH_PASSWORDS.get(ssh_key(s)) or os.environ.get('LINX_REMOTE_SSH_PASSWORD')
    if not password:raise ValueError('请输入本次运行使用的 SSH 账号密码；密码不会保存到设置或迁移包')
    STATE.mkdir(parents=True,exist_ok=True)
    suffix='.cmd' if os.name=='nt' else '.sh'
    fd,name=tempfile.mkstemp(prefix='ssh-askpass-',suffix=suffix,dir=STATE)
    path=Path(name)
    try:
        with os.fdopen(fd,'w',encoding='utf-8',newline='') as f:
            f.write('@echo off\r\npowershell -NoProfile -Command "[Console]::Write($env:LINX_REMOTE_SSH_PASSWORD)"\r\n' if os.name=='nt' else "#!/bin/sh\nexec printf '%s' \"$LINX_REMOTE_SSH_PASSWORD\"\n")
        if os.name!='nt':path.chmod(0o700)
        env=dict(os.environ,SSH_ASKPASS=str(path),SSH_ASKPASS_REQUIRE='force',DISPLAY='1',LINX_REMOTE_SSH_PASSWORD=password)
        return env,path
    except Exception:
        path.unlink(missing_ok=True);raise

def ssh_run(s,args,**kwargs):
    env,path=ssh_auth_environment(s)
    if env is not None:
        merged=dict(os.environ);merged.update(kwargs.get('env',{}));merged.update(env);kwargs['env']=merged
    try:return subprocess.run(args,**kwargs)
    finally:
        if path:path.unlink(missing_ok=True)

def ssh_popen(s,args,**kwargs):
    env,path=ssh_auth_environment(s)
    if env is not None:
        merged=dict(os.environ);merged.update(kwargs.get('env',{}));merged.update(env);kwargs['env']=merged
    try:return subprocess.Popen(args,**kwargs),path
    except Exception:
        if path:path.unlink(missing_ok=True)
        raise

def detect_connection(s,previous=None):
    from connection_detection import detect
    return detect(s,read_settings() if previous is None else previous,sys.modules[__name__])

def detect_remote_home(s):
    cmd,dest=ssh_base(s)
    script="import json,os,pwd;from pathlib import Path;a=pwd.getpwuid(os.geteuid());print(json.dumps({'home':str(Path(a.pw_dir).resolve()/'.xdt-server/v1/codex-home'),'user':a.pw_name}))"
    try:r=ssh_run(s,cmd+[dest,'python3 -c '+shlex.quote(script)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,encoding='utf-8',errors='replace',timeout=25,**HIDDEN)
    except subprocess.TimeoutExpired:raise ValueError('服务器连接超时，未修改原连接；请先在 Cindy 中确认服务器可登录后重试')
    if r.returncode:raise ValueError('无法复用此服务器的 SSH 登录，未修改原连接。请先用“打开 SSH 登录终端”确认主机指纹，并解锁密钥或加载 SSH Agent。')
    try:
        result=json.loads(r.stdout)
        if not isinstance(result['home'],str) or not result['home'].startswith('/'):raise ValueError()
        return result
    except (ValueError,KeyError,TypeError):raise ValueError('服务器未返回个人目录，请确认 Python 3 可用及 SSH 登录脚本没有额外输出')

def ssh_base(s, batch=True):
    if not s.get('host') and not (s.get('cindy_host') or s.get('alias')):raise ValueError('请先在“服务器连接”中选择或添加服务器')
    if s.get('connection_source')=='cindy':s=cindy_selection(s,s.get('cindy_host') or s['alias'])
    exe = shutil.which('ssh')
    if not exe:
        hint = '请安装 Windows 可选功能 OpenSSH 客户端' if os.name == 'nt' else 'macOS 通常自带 /usr/bin/ssh，请检查系统与 PATH'
        raise ValueError('找不到 OpenSSH 客户端；' + hint)
    cmd = [exe, '-o', 'ConnectTimeout=12', '-o', 'ServerAliveInterval=20', '-o', 'ServerAliveCountMax=3']
    if batch and s.get('auth_method')!='password': cmd += ['-o', 'BatchMode=yes']
    if batch and s.get('auth_method')=='password':cmd += ['-o','PreferredAuthentications=password','-o','PubkeyAuthentication=no','-o','NumberOfPasswordPrompts=1','-o','StrictHostKeyChecking=accept-new']
    if s.get('host'):
        host, user = s['host'].strip(), s['user'].strip()
        if not re.fullmatch(r'[A-Za-z0-9_.:-]+', host) or host.startswith('-'): raise ValueError('服务器地址格式无效')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', user) or user.startswith('-'): raise ValueError('用户名格式无效')
        cmd += ['-p', str(int(s['port']))]
        if s.get('identity'):
            identity = Path(s['identity']).expanduser()
            if not identity.is_file(): raise ValueError('SSH 私钥文件不存在，请在连接设置中重新选择')
            cmd += ['-i', str(identity), '-o', 'IdentitiesOnly=yes']
        destination = user + '@' + host
    else:
        destination = s.get('alias', '').strip()
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', destination) or destination.startswith('-'): raise ValueError('SSH 别名格式无效')
    return cmd, destination

def remote(s, action, **data):
    cmd, dest = ssh_base(s)
    worker = base64.b64encode((ROOT / 'remote_manager.py').read_bytes()).decode('ascii')
    script = "import base64,sys;exec(compile(base64.b64decode(sys.stdin.readline()),'<cindy-remote-kit>','exec'))"
    cmd += [dest, 'python3 -c ' + shlex.quote(script)]
    request = dict(data, action=action, home=s['home'])
    try:
        r = ssh_run(s,cmd, input=worker + chr(10) + json.dumps(request), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           encoding='utf-8', errors='replace', timeout=110, **HIDDEN)
    except subprocess.TimeoutExpired: raise ValueError('SSH 操作超过 110 秒，请检查网络和服务器状态')
    if r.returncode:
        raise ValueError('SSH 连接失败：\n' + r.stderr[-1800:] + '\n新电脑请先点“SSH 登录”，确认主机指纹并完成免密登录。')
    try: reply = json.loads(r.stdout)
    except ValueError: raise ValueError('服务器未返回有效数据；请检查 Python 3 和 SSH 登录脚本输出。')
    if not reply.get('ok'): raise ValueError(reply.get('error', '远程操作失败'))
    return reply['data']

def environment_status():
    with ENV_LOCK:
        return json.loads(json.dumps(ENV_JOB or {'state':'idle','events':[]}))

def start_environment(s,update=None):
    global ENV_JOB
    with ENV_LOCK:
        if ENV_JOB and ENV_JOB['state']=='running':return json.loads(json.dumps(ENV_JOB))
        job={'id':uuid.uuid4().hex,'state':'running','events':[],'settings':{k:s.get(k) for k in ('alias','host','user','port','home')},'tool':update.get('tool') if update else None}
        ENV_JOB=job
    def work():
        proc=None;timer=None
        def progress(message,**fields):
            with ENV_LOCK:
                job['events'].append(dict(message=message,**fields));job['events']=job['events'][-60:]
        try:
            cmd,dest=ssh_base(s)
            source=base64.b64encode((ROOT/'remote_environment.py').read_bytes()).decode('ascii')
            script="import base64,sys;exec(compile(base64.b64decode(sys.stdin.readline()),'<linx-environment>','exec'))"
            request={'home':s.get('home')}
            if update:
                runtime=(ROOT/'remote_manager.py').read_text(encoding='utf-8').split("if __name__ == '__main__':")[0]
                request.update(update,action='install_update',runtime_source=base64.b64encode(runtime.encode()).decode('ascii'))
            for attempt in range(2):
                fallback=None
                proc,askpass=ssh_popen(s,cmd+[dest,'python3 -u -c '+shlex.quote(script)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,encoding='utf-8',errors='replace',**HIDDEN)
                threading.Thread(target=lambda p=proc:p.stderr.read(),daemon=True).start()
                proc.stdin.write(source+'\n'+json.dumps(request)+'\n');proc.stdin.close()
                timer=threading.Timer(1300,proc.kill);timer.daemon=True;timer.start()
                for line in proc.stdout:
                    try:event=json.loads(line)
                    except ValueError:continue
                    if not isinstance(event,dict) or not isinstance(event.get('message'),str):continue
                    if event.get('download_request') and attempt==0:
                        fallback=event['download_request']
                        progress('服务器下载不可达，正在切换本机下载与 SSH 传输…',phase='download')
                        continue
                    with ENV_LOCK:
                        job['events'].append(event);job['events']=job['events'][-60:]
                        if event.get('done'):job['result']=event.get('result');job['state']='done'
                        if event.get('failed'):job['error']=event['message'];job['state']='failed'
                proc.wait(timeout=10);timer.cancel();timer=None;askpass and askpass.unlink(missing_ok=True)
                if askpass:askpass.unlink(missing_ok=True)
                if fallback:
                    from download_bridge import bridge
                    bridge(fallback,STATE,cmd+[dest],HIDDEN,progress)
                    continue
                break
            with ENV_LOCK:
                if job['state']=='running':raise ValueError('SSH 连接中断或任务超时；请确认服务器连接后重试，Codex 下载缓存会保留。')
        except Exception as e:
            with ENV_LOCK:
                job['state']='failed';job['error']=str(e) if isinstance(e,ValueError) else '无法准备远程环境；请先通过“SSH 登录”确认免密连接和服务器 Python 3，再重试。'
        finally:
            if timer:timer.cancel()
            if proc and proc.poll() is None:proc.kill()
    threading.Thread(target=work,daemon=True).start()
    return environment_status()

def updates_status():
    with ENV_LOCK:return json.loads(json.dumps(UPDATES_JOB or {'state':'idle','items':[]}))

def numeric_version(value):
    m=re.search(r'(\d+)\.(\d+)(?:\.(\d+))?',value or '')
    return tuple(int(x or 0) for x in m.groups()) if m else None

def start_install_update(s,tool):
    job=updates_status()
    if tool not in ('Codex','gawk'):raise ValueError('不支持更新此工具')
    if job.get('state')!='done' or job.get('settings')!={k:s.get(k) for k in ('alias','host','user','port','home')}:raise ValueError('请先为当前服务器检查更新')
    item=next((x for x in job['items'] if x['name']==tool and x['update']),None)
    if not item:raise ValueError('当前没有此工具的可用更新，请先重新检查')
    if tool=='Codex' and not item.get('package'):raise ValueError('官方更新包缺少匹配架构或 SHA256，暂不安装')
    return start_environment(s,{'tool':tool,'package':item.get('package')})

def start_updates(s,force=False):
    global UPDATES_JOB
    connection={k:s.get(k) for k in ('alias','host','user','port','home')}
    with ENV_LOCK:
        if UPDATES_JOB and UPDATES_JOB.get('settings')==connection:
            if UPDATES_JOB['state']=='running' or (not force and time.time()-UPDATES_JOB['checked_at']<6*3600):return json.loads(json.dumps(UPDATES_JOB))
        job={'state':'running','items':[],'settings':connection,'checked_at':time.time()};UPDATES_JOB=job
    def work():
        import urllib.request
        try:
            cmd,dest=ssh_base(s)
            source=base64.b64encode((ROOT/'remote_environment.py').read_bytes()).decode('ascii')
            script="import base64,sys;exec(compile(base64.b64decode(sys.stdin.readline()),'<linx-update-check>','exec'))"
            r=ssh_run(s,cmd+[dest,'python3 -u -c '+shlex.quote(script)],input=source+'\n'+json.dumps({'home':s['home'],'action':'updates'})+'\n',stdout=subprocess.PIPE,stderr=subprocess.PIPE,encoding='utf-8',errors='replace',timeout=65,**HIDDEN)
            messages=[]
            for line in r.stdout.splitlines():
                try:messages.append(json.loads(line))
                except ValueError:pass
            result=next((m['result'] for m in messages if m.get('done')),None)
            if r.returncode or not result:raise ValueError('更新检查未能连接服务器；请确认 SSH 登录后重试。')
            items=[];latest=None;note='';package=None
            try:
                from download_bridge import latest_package
                latest,package=latest_package(result['system'],result['machine'])
            except Exception:note='Codex 更新源暂时无法访问，可稍后手动检查。'
            for name,installed,available in [('Codex',result['codex'],latest),('gawk',result['gawk'],result['gawk_candidate'])]:
                current=numeric_version(installed);new=numeric_version(available)
                items.append({'name':name,'installed':installed,'available':available,'update':bool(current and new and new>current),'source':'GitHub 官方稳定版' if name=='Codex' else '服务器现有 APT 索引','package':package if name=='Codex' else None})
            with ENV_LOCK:job.update(state='done',items=items,note=note,checked_at=time.time())
        except Exception as e:
            with ENV_LOCK:job.update(state='failed',error=str(e) if isinstance(e,ValueError) else '更新检查未完成；请检查网络后重试。')
    threading.Thread(target=work,daemon=True).start()
    return updates_status()

def tunnel_dir(s):
    import hashlib
    connection = {k:s.get(k) for k in ('alias','host','user','port','remote_port','local_port')}
    token = hashlib.sha256(json.dumps(connection, sort_keys=True).encode()).hexdigest()[:16]
    return STATE / token

def state_write(d, data):
    tmp = d / 'status.tmp'
    tmp.write_text(json.dumps(dict(data, updated=time.time()), ensure_ascii=False), encoding='utf-8')
    os.replace(str(tmp), str(d / 'status.json'))

def tunnel_worker(settings_path):
    s = json.loads(Path(settings_path).read_text(encoding='utf-8')); d = tunnel_dir(s)
    d.mkdir(parents=True, exist_ok=True)
    # OS lock prevents duplicate supervisors; no process-name or broad PID killing.
    lock = open(d / 'worker.lock', 'a+b'); lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError: return
    stop = d / 'stop'
    if stop.exists(): stop.unlink()
    child = None
    fatal = None
    try:
        while not stop.exists():
            cmd, dest = ssh_base(s)
            cmd += ['-o', 'ExitOnForwardFailure=yes', '-o', 'LogLevel=ERROR', '-N', '-T', '-R',
                    '127.0.0.1:%d:127.0.0.1:%d' % (int(s['remote_port']), int(s['local_port'])), dest]
            with open(d / 'ssh.log', 'w', encoding='utf-8') as log:
                child,askpass = ssh_popen(s,cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log, **HIDDEN)
                started = time.time()
                while child.poll() is None and not stop.exists():
                    state_write(d, {'pid':os.getpid(), 'ssh_pid':child.pid,
                                    'state':'SSH 运行中（请用检查隧道验证转发）' if time.time()-started > 3 else '正在连接'})
                    time.sleep(1)
                if askpass:askpass.unlink(missing_ok=True)
                if stop.exists(): break
                state_write(d, {'pid':os.getpid(), 'state':'连接断开，5 秒后重连', 'error':(d/'ssh.log').read_text(encoding='utf-8',errors='replace')[-1600:]})
                for _ in range(5):
                    if stop.exists(): break
                    time.sleep(1)
    except Exception as e: fatal = str(e)
    finally:
        if child and child.poll() is None:
            child.terminate()
            try: child.wait(timeout=5)
            except subprocess.TimeoutExpired: child.kill(); child.wait()
        if 'askpass' in locals() and askpass:askpass.unlink(missing_ok=True)
        state_write(d, dict({'state':'已停止'}, **({'error':fatal} if fatal else {}))); lock.close()

def start_tunnel(s):
    ssh_base(s)
    d = tunnel_dir(s); d.mkdir(parents=True, exist_ok=True)
    status = tunnel_status(s)
    if status.get('fresh'): return {'note':'本工具的隧道已运行；无需重复启动', **status}
    if (d/'stop').exists(): (d/'stop').unlink()
    snapshot = d / 'connection.json'
    snapshot.write_text(json.dumps(s,ensure_ascii=False),encoding='utf-8')
    python = sys.executable
    if os.name == 'nt' and (Path(python).parent/'pythonw.exe').exists(): python=str(Path(python).parent/'pythonw.exe')
    command = [sys.executable, '--tunnel-worker', str(snapshot)] if FROZEN else [python, str(ROOT/'manager.py'), '--tunnel-worker', str(snapshot)]
    environment = dict(os.environ)
    if s.get('auth_method')=='password':environment['LINX_REMOTE_SSH_PASSWORD']=SSH_PASSWORDS.get(ssh_key(s),'')
    if FROZEN: environment['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    subprocess.Popen(command, env=environment,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     **(HIDDEN if os.name == 'nt' else {'start_new_session': True}))
    time.sleep(4)
    return {'note':'后台隧道已启动；窗口关闭后仍运行，断线自动重连，重启电脑后需再次启动', **tunnel_status(s)}

def tunnel_status(s):
    p = tunnel_dir(s)/'status.json'
    if not p.exists(): return {'state':'没有本工具管理的隧道；也可能有旧的手动隧道'}
    try: data=json.loads(p.read_text(encoding='utf-8'))
    except ValueError: return {'state':'正在刷新状态，请重试'}
    data['fresh'] = time.time()-data.get('updated',0)<8 and data.get('state')!='已停止'
    return data

def stop_tunnel(s):
    d=tunnel_dir(s); d.mkdir(parents=True,exist_ok=True); (d/'stop').touch()
    time.sleep(2)
    return {'note':'已请求停止本工具拥有的隧道，不会关闭其他 SSH 连接', **tunnel_status(s)}

def gui():
    from web_app import serve
    serve()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--tunnel-worker');args=parser.parse_args()
    if args.tunnel_worker:tunnel_worker(args.tunnel_worker)
    else:gui()
