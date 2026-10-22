"""Loopback-only UI. No dependencies, no public listener, no credential request logging."""
import base64, hashlib, json, os, secrets, subprocess, sys, tempfile, threading, time, urllib.parse, urllib.request, webbrowser
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import manager as core
LOCK = threading.RLock()
REOPEN = 0
ICON_HANDLES = {}
WINDOW_TITLES = ('LinX Remote', 'LinX · 远程模型管理', 'LinX 远程模型管理', 'Cindy · 远程模型管理')
WINDOW_INSTANCE_PROPERTY = 'LinXRemoteKit.Port'
APP_USER_MODEL_ID = 'LinX.Remote.ModelManager'

def runtime_build_id():
    digest=hashlib.sha256()
    for name in core.PACKAGE_FILES:
        path=core.ROOT/name
        try: content=path.read_bytes()
        except OSError: continue
        digest.update(name.encode('utf-8'));digest.update(b'\0');digest.update(content);digest.update(b'\0')
    return digest.hexdigest()

RUNTIME_BUILD_ID=runtime_build_id()

def window_taskbar_identity(hwnd):
    """Set identity on the browser-hosted window, not on the Python launcher."""
    import ctypes, uuid
    from ctypes import wintypes
    class GUID(ctypes.Structure):
        _fields_=[('raw',ctypes.c_ubyte*16)]
        def __init__(self,value):
            super().__init__();self.raw[:]=uuid.UUID(value).bytes_le
    class PROPERTYKEY(ctypes.Structure):
        _fields_=[('fmtid',GUID),('pid',wintypes.DWORD)]
    class VALUE(ctypes.Union):
        _fields_=[('text',wintypes.LPWSTR),('padding',ctypes.c_ubyte*16)]
    class PROPVARIANT(ctypes.Structure):
        _fields_=[('vt',ctypes.c_ushort),('reserved',ctypes.c_ushort*3),('value',VALUE)]
    ole=ctypes.WinDLL('ole32');shell=ctypes.WinDLL('shell32')
    ole.CoInitializeEx.argtypes=[ctypes.c_void_p,wintypes.DWORD]
    initialized=ole.CoInitializeEx(None,2)>=0
    store=ctypes.c_void_p()
    try:
        shell.SHGetPropertyStoreForWindow.argtypes=[wintypes.HWND,ctypes.POINTER(GUID),ctypes.POINTER(ctypes.c_void_p)]
        shell.SHGetPropertyStoreForWindow.restype=ctypes.c_long
        iid=GUID('886d8eeb-8cf2-4446-8d02-cdba1dbdcf99')
        if shell.SHGetPropertyStoreForWindow(hwnd,ctypes.byref(iid),ctypes.byref(store))<0:return False
        vtable=ctypes.cast(store,ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        getvalue=ctypes.WINFUNCTYPE(ctypes.c_long,ctypes.c_void_p,ctypes.POINTER(PROPERTYKEY),ctypes.POINTER(PROPVARIANT))(vtable[5])
        identitykey=PROPERTYKEY(GUID('9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3'),5)
        current=PROPVARIANT()
        ole.PropVariantClear.argtypes=[ctypes.POINTER(PROPVARIANT)]
        try:
            if getvalue(store,ctypes.byref(identitykey),ctypes.byref(current))>=0 and current.vt==31 and current.value.text==APP_USER_MODEL_ID:
                return True
        finally:ole.PropVariantClear(ctypes.byref(current))
        setvalue=ctypes.WINFUNCTYPE(ctypes.c_long,ctypes.c_void_p,ctypes.POINTER(PROPERTYKEY),ctypes.POINTER(PROPVARIANT))(vtable[6])
        commit=ctypes.WINFUNCTYPE(ctypes.c_long,ctypes.c_void_p)(vtable[7])
        values={5:APP_USER_MODEL_ID,4:'LinX Remote'}
        # Use the portable EXE for taskbar relaunch and its embedded icon.
        exe=core.APP_DIR/core.WINDOWS_APP
        if exe.is_file():values.update({2:subprocess.list2cmdline([str(exe)]),3:str(exe)+',0'})
        ok=True
        for pid,text in values.items():
            key=PROPERTYKEY(GUID('9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3'),pid)
            buffer=ctypes.create_unicode_buffer(text)
            value=PROPVARIANT();value.vt=31;value.value.text=ctypes.cast(buffer,wintypes.LPWSTR)
            if setvalue(store,ctypes.byref(key),ctypes.byref(value))<0:ok=False
        # Some window property stores apply immediately and do not implement Commit.
        commit(store)
        return ok
    finally:
        if store:
            release=ctypes.WINFUNCTYPE(ctypes.c_ulong,ctypes.c_void_p)(vtable[2]);release(store)
        if initialized:ole.CoUninitialize()

def maintain_window_identity(port,stop):
    """Refresh taskbar metadata without ever restoring a minimized window."""
    import ctypes
    from ctypes import wintypes
    u=ctypes.WinDLL('user32')
    u.GetPropW.argtypes=[wintypes.HWND,wintypes.LPCWSTR];u.GetPropW.restype=wintypes.HANDLE
    u.IsIconic.argtypes=[wintypes.HWND];u.IsIconic.restype=wintypes.BOOL
    while not stop.wait(2):
        for hwnd,title in manager_windows()[1]:
            if u.GetPropW(hwnd,WINDOW_INSTANCE_PROPERTY)==port and not u.IsIconic(hwnd):
                apply_window_icon(hwnd)

def ui_preferences():
    try:
        mode=json.loads((core.STATE/'ui-preferences.json').read_text(encoding='utf-8')).get('mode','auto')
    except (OSError,ValueError,AttributeError):mode='auto'
    return {'mode':mode if mode in ('auto','light','dark') else 'auto'}

def resolved_theme(mode):
    return ('light' if 7 <= time.localtime().tm_hour < 19 else 'dark') if mode=='auto' else mode

def native_appearance(hwnd):
    if os.name!='nt':return
    import ctypes
    from ctypes import wintypes
    dark=ctypes.c_int(resolved_theme(ui_preferences()['mode'])=='dark')
    try:
        dwm=ctypes.WinDLL('dwmapi')
        dwm.DwmSetWindowAttribute.argtypes=[wintypes.HWND,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD]
        dwm.DwmSetWindowAttribute(hwnd,20,ctypes.byref(dark),ctypes.sizeof(dark))
    except OSError:pass

def save_ui_preferences(mode):
    if mode not in ('auto','light','dark'):raise ValueError('请选择自动、日间或夜间模式')
    core.STATE.mkdir(parents=True,exist_ok=True)
    with LOCK:
        fd,temp=tempfile.mkstemp(prefix='ui-preferences-',suffix='.json',dir=str(core.STATE))
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump({'mode':mode},f)
            os.replace(temp,str(core.STATE/'ui-preferences.json'))
        finally:
            if os.path.exists(temp):os.unlink(temp)
    if os.name=='nt':
        for hwnd,title in manager_windows()[1]:native_appearance(hwnd)
    return {'mode':mode,'effective':resolved_theme(mode)}

@contextmanager
def launch_lock():
    core.STATE.mkdir(parents=True, exist_ok=True)
    with (core.STATE / 'ui-launch.lock').open('a+b') as f:
        f.seek(0); f.write(b'0'); f.flush()
        deadline = time.monotonic() + 20
        while True:
            try:
                f.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() > deadline: raise ValueError('管理器正在启动，请稍后再试')
                time.sleep(.1)
        try: yield
        finally:
            f.seek(0)
            if os.name == 'nt': msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else: fcntl.flock(f.fileno(), fcntl.LOCK_UN)

def is_manager_window_title(title):
    return any(title == name or (title.startswith(name+' [') and title.endswith(']') and title[len(name+' ['):-1].isdigit()) for name in WINDOW_TITLES)

def manager_windows():
    import ctypes
    from ctypes import wintypes
    u = ctypes.WinDLL('user32', use_last_error=True)
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    u.EnumWindows.argtypes = [callback, wintypes.LPARAM]
    u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    found = []
    @callback
    def visit(hwnd, unused):
        title = ctypes.create_unicode_buffer(1024); cls = ctypes.create_unicode_buffer(256)
        u.GetWindowTextW(hwnd, title, len(title)); u.GetClassNameW(hwnd, cls, len(cls))
        # Ordinary browser tabs append a browser name; never close those.
        if cls.value == 'Chrome_WidgetWin_1' and is_manager_window_title(title.value): found.append((hwnd,title.value))
        return True
    u.EnumWindows(visit, 0)
    return u, found

def focus_manager(url):
    import ctypes
    from ctypes import wintypes
    u, found = manager_windows()
    port=urllib.parse.urlsplit(url).port
    u.GetPropW.argtypes=[wintypes.HWND,wintypes.LPCWSTR]
    u.GetPropW.restype=wintypes.HANDLE
    u.SetPropW.argtypes=[wintypes.HWND,wintypes.LPCWSTR,wintypes.HANDLE]
    # Reuse only a window already attached to this exact server instance. An
    # unmarked window may belong to a previous process whose localhost server
    # has exited; adopting it leaves the UI permanently showing its old page.
    matching=[h for h,t in found if u.GetPropW(h,WINDOW_INSTANCE_PROPERTY)==port]
    u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u.SetForegroundWindow.argtypes = [wintypes.HWND]
    u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    keep = matching[0] if matching else None
    for hwnd,title in found:
        if hwnd != keep: u.PostMessageW(hwnd, 0x0010, 0, 0)
    if keep is None: return False
    u.SetPropW(keep,WINDOW_INSTANCE_PROPERTY,port)
    apply_window_icon(keep)
    u.ShowWindow(keep, 3)
    u.SetForegroundWindow(keep)
    return True

def apply_window_icon(hwnd):
    import ctypes
    from ctypes import wintypes
    u=ctypes.WinDLL('user32',use_last_error=True)
    u.IsIconic.argtypes=[wintypes.HWND];u.IsIconic.restype=wintypes.BOOL
    # Windows can expose a minimized app window to icon refreshes. Leave it
    # untouched until the user restores it; metadata is refreshed afterwards.
    if u.IsIconic(hwnd):return False
    u.LoadImageW.argtypes=[wintypes.HINSTANCE,wintypes.LPCWSTR,wintypes.UINT,ctypes.c_int,ctypes.c_int,wintypes.UINT]
    u.LoadImageW.restype=wintypes.HANDLE
    u.SendMessageTimeoutW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM,wintypes.UINT,wintypes.UINT,ctypes.POINTER(ctypes.c_size_t)]
    window_taskbar_identity(hwnd)
    for kind,size in ((0,16),(1,32),(2,16)):
        if kind not in ICON_HANDLES:
            ICON_HANDLES[kind]=u.LoadImageW(None,str(core.ROOT/'LinX.ico'),1,size,size,0x10)
        if ICON_HANDLES[kind]:
            result=ctypes.c_size_t()
            u.SendMessageTimeoutW(hwnd,0x80,kind,ICON_HANDLES[kind],2,250,ctypes.byref(result))
    native_appearance(hwnd)
    return True

def app_window_is_ready(width,height,visible):
    """Reject Chrome's small hidden helper windows before adopting the app."""
    return bool(visible and width>=600 and height>=400)

def await_window_icon(url):
    import ctypes
    from ctypes import wintypes
    port=urllib.parse.urlsplit(url).port
    u=ctypes.WinDLL('user32')
    u.GetPropW.argtypes=[wintypes.HWND,wintypes.LPCWSTR];u.GetPropW.restype=wintypes.HANDLE
    u.SetPropW.argtypes=[wintypes.HWND,wintypes.LPCWSTR,wintypes.HANDLE]
    u.GetWindowRect.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.RECT)]
    u.IsWindowVisible.argtypes=[wintypes.HWND];u.IsWindowVisible.restype=wintypes.BOOL
    u.IsIconic.argtypes=[wintypes.HWND];u.IsIconic.restype=wintypes.BOOL
    u.ShowWindow.argtypes=[wintypes.HWND,ctypes.c_int]
    u.SetWindowPos.argtypes=[wintypes.HWND,wintypes.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.UINT]
    work=wintypes.RECT()
    has_work=bool(u.SystemParametersInfoW(0x0030,0,ctypes.byref(work),0))
    startup_deadline=time.monotonic()+3
    restored=set()
    for _ in range(200):
        candidate=None
        candidate_area=0
        minimized=[]
        for hwnd,title in manager_windows()[1]:
            if u.GetPropW(hwnd,WINDOW_INSTANCE_PROPERTY) not in (None,port):
                continue
            rect=wintypes.RECT()
            if not u.GetWindowRect(hwnd,ctypes.byref(rect)):
                continue
            width,height=rect.right-rect.left,rect.bottom-rect.top
            if u.IsIconic(hwnd):
                minimized.append(hwnd)
                continue
            if not app_window_is_ready(width,height,bool(u.IsWindowVisible(hwnd))):
                continue
            area=width*height
            if area>candidate_area:
                candidate,candidate_area=hwnd,area
        if candidate:
            u.SetPropW(candidate,WINDOW_INSTANCE_PROPERTY,port)
            if has_work:
                # NOACTIVATE + NOOWNERZORDER: size the real app once without
                # restoring it, stealing focus, or competing with Edge's state.
                u.SetWindowPos(candidate,0,work.left,work.top,work.right-work.left,work.bottom-work.top,0x0010|0x0200)
            apply_window_icon(candidate)
            return
        # Edge may reuse the app window left minimized by the previous server.
        # Restore it only during launch; after this window of time, minimizing
        # is always left under the user's control.
        if time.monotonic()<startup_deadline:
            for hwnd in minimized:
                if hwnd not in restored:
                    restored.add(hwnd)
                    u.ShowWindow(hwnd,3)
                    break
        elif minimized:
            return
        time.sleep(.15)

def mac_focus(url):
    origin = url.split('/#', 1)[0] + '/'
    for app in ('Google Chrome', 'Microsoft Edge', 'Safari'):
        selection = 'set current tab of w to t' if app == 'Safari' else 'set active tab index of w to i'
        script = '\n'.join([
            'if application '+json.dumps(app)+' is running then',
            'tell application '+json.dumps(app), 'repeat with w in windows',
            'repeat with i from 1 to count of tabs of w', 'set t to tab i of w',
            'if URL of t starts with '+json.dumps(origin)+' then', selection,
            'set index of w to 1', 'activate', 'return "found"',
            'end if', 'end repeat', 'end repeat', 'end tell', 'end if'])
        try:
            r = subprocess.run(['/usr/bin/osascript','-'], input=script, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=8)
            if r.returncode == 0 and r.stdout.strip() == 'found': return True
        except (OSError,subprocess.TimeoutExpired): pass
    return False

def browser(url, reuse=False):
    if os.name == 'nt':
        if focus_manager(url): return
        if reuse:
            for _ in range(15):
                time.sleep(.1)
                if focus_manager(url): return
        candidates = [Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe',
                      Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'Microsoft/Edge/Application/msedge.exe',
                      Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'Google/Chrome/Application/chrome.exe']
        for exe in candidates:
            if exe.is_file():
                subprocess.Popen([str(exe), '--app='+url, '--no-first-run', '--start-maximized'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **core.HIDDEN)
                if reuse:await_window_icon(url)
                else:threading.Thread(target=await_window_icon,args=(url,),daemon=True).start()
                return
    elif sys.platform == 'darwin' and reuse:
        if mac_focus(url): return
    webbrowser.open(url)

def instance_path(): return core.STATE / 'ui-instance.json'

def instance_call(url,op,timeout=2):
    u=urllib.parse.urlsplit(url)
    req=urllib.request.Request('http://127.0.0.1:%d/api' % u.port,
        data=json.dumps({'op':op}).encode('utf-8'),
        headers={'Content-Type':'application/json','X-Kit-Token':u.fragment})
    with urllib.request.urlopen(req,timeout=timeout) as response:return json.load(response)

def existing():
    try:
        instance = json.loads(instance_path().read_text(encoding='utf-8'))
        u = urllib.parse.urlsplit(instance['url'])
        if u.scheme != 'http' or u.hostname != '127.0.0.1': return None
        status=instance_call(instance['url'],'ping')
        if not status.get('ok'):return None
        # Every new launch takes ownership of the UI. Stop the older server
        # cleanly before replacing its page and window; never leave two managers open.
        instance_call(instance['url'],'quit',timeout=3)
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            try:instance_call(instance['url'],'ping',timeout=1)
            except Exception:return None
            time.sleep(.1)
        raise RuntimeError('检测到旧版管理器，但它没有在 10 秒内退出。请从任务管理器结束旧版 LinX 后重试。')
    except RuntimeError:raise
    except Exception: pass
    return None

def profile(data):
    p = {k:str(data.get(k,'')).strip() for k in ('id','name','base_url','model','key','mode')}
    values=data.get('selected_models')
    if values is not None:
        if not isinstance(values,list) or not all(isinstance(x,str) for x in values):raise ValueError('模型需要为 ID 列表')
        p['selected_models']=list(dict.fromkeys(x.strip() for x in values if x.strip()))
        if p['model'] not in p['selected_models']:p['model']=next(iter(p['selected_models']), '')
    p['base_url'] = p['base_url'].rstrip('/')
    if not p['id']: raise ValueError('请重新打开添加供应商窗口')
    if not p['base_url']: raise ValueError('请先填写接口地址，例如 https://example.com/v1')
    if p['base_url'].endswith(('/responses','/models')): raise ValueError('请填写 API 根地址（通常以 /v1 结尾），不要带 /responses 或 /models')
    p['name'] = p['name'] or ('本机 Sub2API' if p['mode']=='tunnel' else urllib.parse.urlsplit(p['base_url']).hostname or '新供应商')
    return p

def operation(data):
    global REOPEN
    op = data.get('op')
    with LOCK: s=core.read_settings()
    if op == 'ping': return {'version':8, 'build':RUNTIME_BUILD_ID, 'reopen':REOPEN}
    if op == 'appearance':return save_ui_preferences(data.get('mode'))
    if op == 'appearance_sync':
        if os.name=='nt':
            for hwnd,title in manager_windows()[1]:native_appearance(hwnd)
        return ui_preferences()
    if op == 'window_reopen':
        REOPEN += 1
        return {'reopen':REOPEN}
    if op == 'bootstrap': return {'settings':s, 'tunnel':core.tunnel_status(s), 'platform':sys.platform,'environment':core.environment_status(),'cindy_connections':core.cindy_connections()}
    if op == 'cindy_connections': return core.cindy_connections()
    if op == 'monitor_status':
        import server_monitor
        return server_monitor.monitor.collect(s, force=bool(data.get('force')))
    if op == 'connection_detect':
        draft={k:data.get('settings',s).get(k,core.DEFAULT[k]) for k in core.DEFAULT}
        if draft.get('auth_method')=='password':core.remember_ssh_password(draft,data.get('ssh_password'))
        return core.detect_connection(draft,s)
    if op == 'environment_start': return core.start_environment(s)
    if op == 'environment_status':
        job=core.environment_status()
        if job.get('state')=='done' and job.get('result'):
            target=job['result'].get('codex_home',s['home'])
            with LOCK:
                current=core.read_settings()
                if all(current.get(k)==v for k,v in job['settings'].items()) and target!=current['home']:
                    current['home']=target;core.write_settings(current)
                job['current_settings']=current
        return job
    if op == 'updates_check': return core.start_updates(s,force=bool(data.get('force')))
    if op == 'updates_status': return core.updates_status()
    if op == 'updates_install': return core.start_install_update(s,data.get('tool'))
    if op == 'list': return core.remote(s,'list')
    if op == 'models': return core.remote(s,'preview_models',profile=profile(data['profile']))
    if op == 'save':
        p=profile(data['profile'])
        if not p['model']: raise ValueError('请至少勾选或添加一个模型')
        return core.remote(s,'save',profile=p)
    if op == 'activate': return core.remote(s,op,id=data['id'])
    if op == 'catalog_sync': return core.remote(s,op)
    if op == 'test': return core.remote(s,op,id=data['id'],model=data.get('model'))
    if op == 'test_active': return core.remote(s,'test_active',model=data.get('model'))
    if op == 'rollback': return core.remote(s,'rollback')
    if op == 'tunnel_check':
        result=core.remote(s,'tunnel_check',port=int(s['remote_port']))
        return dict(result,local=core.tunnel_status(s))
    if op == 'tunnel_start': return core.start_tunnel(s)
    if op == 'tunnel_stop': return core.stop_tunnel(s)
    if op == 'tunnel_status': return core.tunnel_status(s)
    if op == 'ssh_login':
        draft={k:data.get('settings',s).get(k,core.DEFAULT[k]) for k in core.DEFAULT}
        if draft['connection_source']=='cindy':draft=core.cindy_selection(draft,draft['cindy_host'] or draft['alias'])
        try:draft['port']=int(draft['port'])
        except (ValueError,TypeError):raise ValueError('SSH 端口需要填写 1 至 65535 的数字')
        if not 1<=draft['port']<=65535:raise ValueError('SSH 端口需要填写 1 至 65535 的数字')
        command=core.open_ssh_terminal(draft)
        return {'message':'已打开 SSH 登录终端；登录完成后可关闭终端，再回来连接。' if not command else '请在终端执行：'+command}
    if op == 'settings':
        new={k:data['settings'].get(k,core.DEFAULT[k]) for k in core.DEFAULT}
        if new.get('auth_method') not in ('agent','password'):raise ValueError('请选择有效的 SSH 认证方式')
        if new['connection_source']=='cindy':
            new=core.cindy_selection(new,new['cindy_host'] or new['alias'])
        # Register credentials against the normalized connection selected by the user.
        # Detection may fill in the actual account name, so migrate the in-memory
        # entry to that final SSH identity before returning. Never write it to disk.
        initial_ssh_key=core.ssh_key(new)
        supplied_password=data.get('ssh_password')
        cached_password=core.SSH_PASSWORDS.get(initial_ssh_key)
        core.set_ssh_password(new,supplied_password)
        detection=None
        if data.get('auto_detect'):
            detection=core.detect_connection(new,s)
            if detection['directory']['status']=='choose' or detection['tunnel']['status'] in ('choose','conflict'):
                return {'settings':s,'detection':detection,'needs_selection':True}
            new=detection['settings']
        elif not new['home'] or any(new.get(k)!=s.get(k) for k in ('host','alias','user','port')):
            resolved=core.detect_remote_home(new);new['home']=resolved['home'];new['user']=resolved['user']
        for k in ('port','remote_port','local_port'):
            try:new[k]=int(new[k])
            except (TypeError,ValueError):raise ValueError('端口需要填写 1 至 65535 的数字')
            if not 1<=new[k]<=65535:raise ValueError('端口需要填写 1 至 65535 的数字')
        core.ssh_base(new)
        if not new['home'].startswith('/'):raise ValueError('远程 Codex 目录需要使用服务器上的绝对路径')
        if any(new[k]!=s[k] for k in ('alias','host','user','port','identity','home','remote_port','local_port')) and core.tunnel_status(s).get('fresh'):raise ValueError('请先到“本机 Sub2API”停止旧隧道，再修改连接设置')
        final_ssh_key=core.ssh_key(new)
        if new.get('auth_method')=='password':
            password=supplied_password or cached_password
            if password:core.set_ssh_password(new,password)
            if final_ssh_key!=initial_ssh_key:core.SSH_PASSWORDS.pop(initial_ssh_key,None)
        elif final_ssh_key!=initial_ssh_key:
            core.SSH_PASSWORDS.pop(initial_ssh_key,None)
        with LOCK:core.write_settings(new)
        return {'settings':new,'detection':detection}
    if op == 'export':
        fd,path=tempfile.mkstemp(suffix='.zip');os.close(fd)
        try:
            core.export_package(s,path)
            return {'filename':'CindyRemoteKit-portable.zip','archive':base64.b64encode(Path(path).read_bytes()).decode('ascii')}
        finally:Path(path).unlink()
    raise ValueError('操作不存在，请刷新界面')

def serve(open_browser=True):
    with launch_lock():
        server = prepare_server(open_browser)
    if server is None: return
    try:
        server.serve_forever()
    finally:
        server.server_close()
        try:
            if json.loads(instance_path().read_text())['pid']==os.getpid():instance_path().unlink()
        except (OSError,ValueError,KeyError):pass

def prepare_server(open_browser):
    old=existing()
    if old:
        if open_browser:
            u = urllib.parse.urlsplit(old)
            req = urllib.request.Request('http://127.0.0.1:%d/api' % u.port, data=b'{"op":"window_reopen"}',
                headers={'Content-Type':'application/json','X-Kit-Token':u.fragment})
            with urllib.request.urlopen(req,timeout=3) as r: r.read()
            browser(old, reuse=True)
        return
    token=secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def host_ok(self):return self.headers.get('Host')=='127.0.0.1:'+str(self.server.server_port)
        def send(self,code,body,typ='application/json; charset=utf-8'):
            self.send_response(code)
            self.send_header('Content-Type',typ);self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers();self.wfile.write(body)
        def do_GET(self):
            if not self.host_ok():return self.send(404,b'{}')
            if self.path=='/favicon.ico':return self.send(200,(core.ROOT/'LinX.ico').read_bytes(),'image/x-icon')
            if self.path!='/':return self.send(404,b'{}')
            html=(core.ROOT/'manager_ui.html').read_text(encoding='utf-8')
            html=html.replace('name="linx-theme" content="auto"','name="linx-theme" content="'+ui_preferences()['mode']+'"')
            self.send(200,html.encode('utf-8'),'text/html; charset=utf-8')
        def do_POST(self):
            expected='http://127.0.0.1:'+str(self.server.server_port)
            if self.path!='/api' or not self.host_ok() or self.headers.get('Origin',expected)!=expected or not secrets.compare_digest(self.headers.get('X-Kit-Token',''),token):
                return self.send(403,b'{"ok":false}')
            if self.headers.get_content_type()!='application/json':return self.send(415,b'{"ok":false}')
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<262144:raise ValueError('请求过大或为空')
                data=json.loads(self.rfile.read(length))
                if data.get('op')=='quit':
                    result={'message':'管理界面已退出，后台隧道继续运行'}
                    threading.Timer(0.4,self.server.shutdown).start()
                else:result=operation(data)
                reply={'ok':True,'data':result}
            except Exception as e:
                message=str(e) if isinstance(e,ValueError) else '操作未完成，请检查网络、文件权限和连接设置'
                p=data.get('profile') if 'data' in locals() and isinstance(data,dict) else None
                key=p.get('key') if isinstance(p,dict) else None
                if key:message=message.replace(key,'[已隐藏密钥]')
                reply={'ok':False,'error':message}
            try:self.send(200,json.dumps(reply,ensure_ascii=False).encode('utf-8'))
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    server.daemon_threads=True
    if os.name=='nt':
        stop=threading.Event()
        close=server.server_close
        def close_with_identity_stop():
            stop.set();close()
        server.server_close=close_with_identity_stop
        threading.Thread(target=maintain_window_identity,args=(server.server_port,stop),daemon=True).start()
    url='http://127.0.0.1:%d/#%s' % (server.server_port,token)
    core.STATE.mkdir(parents=True,exist_ok=True)
    instance_path().write_text(json.dumps({'url':url,'pid':os.getpid()}),encoding='utf-8')
    if os.name!='nt':instance_path().chmod(0o600)
    if open_browser:browser(url)
    return server

