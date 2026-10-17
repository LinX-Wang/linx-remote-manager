"""Remote worker: secrets arrive on stdin and never appear in argv/output."""
import base64, datetime, hashlib, json, os, re, socket, sqlite3, struct, subprocess, sys, tempfile, time
import urllib.error, urllib.parse, urllib.request
from pathlib import Path
class Rpc:
 def __init__(self,home):
  self.s=socket.socket(socket.AF_UNIX);self.s.settimeout(12)
  self.s.connect(str(home/'app-server-control/app-server-control.sock'))
  key=base64.b64encode(os.urandom(16)).decode()
  self.s.sendall(((chr(13)+chr(10)).join(['GET / HTTP/1.1','Host: localhost','Upgrade: websocket','Connection: Upgrade','Sec-WebSocket-Key: '+key,'Sec-WebSocket-Version: 13','',''])).encode())
  head=b''
  while not head.endswith(bytes([13,10,13,10])):
   b=self.s.recv(1)
   if not b or len(head)>65536:raise ValueError('WebSocket handshake failed')
   head+=b
  expected=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest())
  if not head.startswith(b'HTTP/1.1 101 ') or expected not in head:raise ValueError('WebSocket upgrade rejected')
  self.i=0;self.rpc('initialize',{'clientInfo':{'name':'cindy-remote-kit','version':'4'},'capabilities':{'experimentalApi':True}})
 def read(self,n):
  b=b''
  while len(b)<n:
   v=self.s.recv(n-len(b))
   if not v:raise ValueError('Remote Codex disconnected')
   b+=v
  return b
 def send(self,data,op=1):
  raw=data.encode() if isinstance(data,str) else data
  n=len(raw);mask=os.urandom(4);head=bytes([128|op,128|n]) if n<126 else bytes([128|op,254])+struct.pack('!H',n) if n<65536 else bytes([128|op,255])+struct.pack('!Q',n)
  self.s.sendall(head+mask+bytes(c^mask[i%4] for i,c in enumerate(raw)))
 def receive(self):
  parts=[]
  while True:
   a,b=self.read(2);op=a&15;n=b&127
   if n==126:n=struct.unpack('!H',self.read(2))[0]
   elif n==127:n=struct.unpack('!Q',self.read(8))[0]
   if n>32*1024*1024:raise ValueError('Oversized Codex frame')
   mask=self.read(4) if b&128 else None;raw=self.read(n)
   if mask:raw=bytes(c^mask[i%4] for i,c in enumerate(raw))
   if op==8:raise ValueError('Remote Codex closed WebSocket')
   if op==9:self.send(raw,10);continue
   if op==10:continue
   parts.append(raw)
   if a&128:return json.loads(b''.join(parts))
 def rpc(self,method,params):
  self.i+=1;i=self.i;self.send(json.dumps({'id':i,'method':method,'params':params}))
  end=time.monotonic()+20
  while time.monotonic()<end:
   d=self.receive()
   if d.get('id')==i:
    if 'error' in d:raise ValueError(method+': '+str(d['error']))
    return d.get('result')
  raise ValueError('Codex RPC timeout')
 def close(self):self.s.close()


PROVIDER_ID = 'cindy_portable'
START = '# >>> cindy-portable-provider >>>'
END = '# <<< cindy-portable-provider <<<'
CATALOG_KEY = 'model_catalog_json'
# Cindy currently reserves this exact ID for its internal reviewer. Other IDs,
# including non-GPT/custom IDs, are not filtered by its native remote mapper.
CINDY_INTERNAL_MODEL_IDS = ('codex-auto-review',)
TOOL_COMPAT_REVISION = 2
COMPAT_BEGIN = '# >>> linx-function-tools '
COMPAT_END = '# <<< linx-function-tools <<<'

def restore_tool_settings(config):
    pattern=r'^'+re.escape(COMPAT_BEGIN)+r'([A-Za-z0-9+/=]+) >>>\n.*?^'+re.escape(COMPAT_END)+r'\n?'
    def restore(match):
        try:
            value=json.loads(base64.b64decode(match.group(1),validate=True).decode('utf-8'))
            if not isinstance(value,str):raise ValueError('invalid original settings')
            return value
        except (ValueError,UnicodeError):raise ValueError('函数工具兼容配置备份不完整，未修改配置')
    restored=re.sub(pattern,restore,config,flags=re.M|re.S)
    if COMPAT_BEGIN in restored or COMPAT_END in restored:raise ValueError('函数工具兼容配置块不完整，未修改配置')
    return restored

def function_tool_settings(config,enabled):
    """Reversible overrides; retain original user feature/search preferences."""
    config=restore_tool_settings(config)
    if not enabled:return config
    lines=config.splitlines(keepends=True);sections=[];section=''
    for line in lines:
        if line.lstrip().startswith('['):section=line.strip()
        sections.append(section)
    if any(not section and re.match(r'\s*features\s*=',line) for line,section in zip(lines,sections)):
        raise ValueError('当前 features 使用内联表，无法安全应用函数工具兼容；未修改配置')
    table=next((i for i,s in enumerate(sections) if s=='[features]'),None)
    def block(original,settings):
        encoded=base64.b64encode(json.dumps(original).encode()).decode()
        return COMPAT_BEGIN+encoded+' >>>\n'+settings+COMPAT_END+'\n'
    root_keys=('web_search','features.multi_agent')
    originals='';kept=[]
    for line,section in zip(lines,sections):
        if not section and any(re.match(r'\s*'+re.escape(k)+r'\s*=',line) for k in root_keys):originals+=line
        else:kept.append(line)
    root_settings='web_search = "disabled"\n'+('features.multi_agent = false\n' if table is None else '')
    result=block(originals,root_settings)+''.join(kept)
    if table is not None:
        lines=result.splitlines(keepends=True);out=[];originals='';inside=False;insert=None
        for line in lines:
            if line.lstrip().startswith('['):
                inside=line.strip()=='[features]'
                if inside:insert=len(out)+1
            if inside and re.match(r'\s*multi_agent\s*=',line):originals+=line
            else:out.append(line)
        out.insert(insert,block(originals,'multi_agent = false\n'));result=''.join(out)
    return result

def has_function_tool_settings(config):
    root={};features={};section=''
    for line in config.splitlines():
        if line.lstrip().startswith('['):section=line.strip()
        match=re.match(r'\s*([\w.]+)\s*=\s*(.*?)\s*(?:#.*)?$',line)
        if match:
            key,value=match.groups()
            if not section:root[key]=value
            elif section=='[features]':features[key]=value
    return root.get('web_search')=='"disabled"' and (root.get('features.multi_agent') or features.get('multi_agent'))=='false'

def upstream_signature(profile):
    return hashlib.sha256(json.dumps({k:profile.get(k) for k in ('id','name','base_url','key','mode')},sort_keys=True).encode()).hexdigest()

def routed_profile(profile,proxy):
    if not proxy or proxy.get('upstream_signature')!=upstream_signature(profile):return profile
    copy=dict(profile);copy['base_url']='http://127.0.0.1:%s/%s/v1'%(proxy['port'],proxy['snapshot'])
    return copy

def compatibility_proxy_alive(record):
    if not record:return False
    try:
        if not isinstance(record.get('pid'),int) or not isinstance(record.get('port'),int):return False
        os.kill(record['pid'],0)
        with urllib.request.urlopen('http://127.0.0.1:%s/health'%record['port'],timeout=2) as r:data=json.load(r)
        return data.get('linx_compat_version')==1 and data.get('pid')==record['pid']
    except (OSError,ValueError,urllib.error.URLError):return False

def ensure_compatibility_proxy(home,state,profile):
    directory=state/'responses-compat';directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    status=directory/'status.json'
    try:record=json.loads(status.read_text())
    except (OSError,ValueError):record=None
    bridge_source=LINX_COMPAT_PROXY_SOURCE
    stale_source=True
    try: stale_source=directory.joinpath('bridge.py').read_text() != bridge_source
    except OSError: pass
    if stale_source and record and compatibility_proxy_alive(record):
        try: os.kill(record['pid'],15)
        except OSError: pass
        for _ in range(30):
            if not compatibility_proxy_alive(record): break
            time.sleep(.1)
        record=None
    if not compatibility_proxy_alive(record):
        private(directory/'bridge.py',bridge_source)
        if status.exists():status.unlink()
        with (directory/'service.log').open('ab') as log:
            os.chmod(str(directory/'service.log'),0o600)
            process=subprocess.Popen([sys.executable,str(directory/'bridge.py'),str(directory)],stdin=subprocess.DEVNULL,
                stdout=log,stderr=log,start_new_session=True,close_fds=True)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            try:record=json.loads(status.read_text())
            except (OSError,ValueError):record=None
            if compatibility_proxy_alive(record):break
            if process.poll() is not None:raise ValueError('服务器工具兼容服务未能启动；未应用配置')
            time.sleep(.1)
        else:raise ValueError('服务器工具兼容服务启动超时；未应用配置')
    snapshot=json.dumps({'base_url':profile['base_url'],'key':profile['key']},sort_keys=True)
    digest=hashlib.sha256(snapshot.encode()).hexdigest()
    private(directory/'configs'/(digest+'.json'),snapshot)
    return {'pid':record['pid'],'port':record['port'],'snapshot':digest,'upstream_signature':upstream_signature(profile)}

def model_menu_visibility(models):
    visible=[m for m in models if m not in CINDY_INTERNAL_MODEL_IDS]
    hidden=[m for m in models if m in CINDY_INTERNAL_MODEL_IDS]
    return {'menu_visible_count':len(visible),'menu_hidden_models':hidden}

def codex_version(home):
    result=subprocess.run([str(home/'bin/codex'),'--version'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                          universal_newlines=True,timeout=10)
    if result.returncode:raise ValueError('无法读取远程 Codex 版本，未修改配置')
    return result.stdout.strip()[:120]

def bundled_model_metadata(home):
    # Preserve the installed version's own instructions and capabilities for known models.
    # This is read-only; never patch the executable or copy metadata from a newer version.
    import mmap
    with (home/'bin/codex').open('rb') as stream:
        with mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_READ) as binary:
            for marker in (b'{\n  "models": [',b'{"models":['):
                offset=binary.find(marker)
                if offset<0:continue
                try:
                    data,_=json.JSONDecoder().raw_decode(bytes(binary[offset:offset+8*1024*1024]).decode('utf-8','replace'))
                    rows=data['models']
                    if rows and all(isinstance(x,dict) and isinstance(x.get('slug'),str) for x in rows):
                        return {row['slug']:row for row in rows}
                except (ValueError,KeyError,TypeError):pass
    raise ValueError('当前 Codex 的内置模型元数据格式已变化；未修改现有配置，请更新管理器适配')

def generic_model_metadata(model,index):
    # Unknown models get conservative capabilities, never a renamed GPT capability profile.
    return {'slug':model,'display_name':model,'description':'通过当前供应商使用的自定义 Responses 模型',
            'default_reasoning_level':'medium','supported_reasoning_levels':[],
            'shell_type':'shell_command','visibility':'list','supported_in_api':True,'priority':index,
            'base_instructions':'You are a helpful coding assistant. Follow the user instructions and use available tools when needed.',
            'supports_reasoning_summaries':False,'support_verbosity':False,
            'supports_parallel_tool_calls':False,'input_modalities':['text'],
            'context_window':32768,'experimental_supported_tools':[],
            'truncation_policy':{'mode':'tokens','limit':10000}}

def make_model_catalog(home,profile):
    import copy
    builtin=bundled_model_metadata(home)
    rows=[]
    for index,model in enumerate(selected_models(profile)):
        row=copy.deepcopy(builtin[model]) if model in builtin else generic_model_metadata(model,index)
        row.update(slug=model,visibility='list',supported_in_api=True,priority=index)
        # A filtered catalogue must not advertise upgrades to models outside this supplier.
        row['upgrade']=None
        rows.append(row)
    return {'models':rows}

def probe_model_catalog(home,catalog,expected,return_rows=False):
    """Validate config/schema and model discovery with a disposable, offline app-server."""
    import selectors
    with tempfile.TemporaryDirectory(prefix='linx-model-catalog-check-') as tmp:
        work=Path(tmp);catalog_path=work/'catalog.json'
        private(catalog_path,json.dumps(catalog,ensure_ascii=False))
        config='model_provider = "linx_catalog_probe"\nmodel = '+quote(expected[0])+'\nmodel_catalog_json = '+quote(str(catalog_path))+'\n'
        config+='[model_providers.linx_catalog_probe]\nname = "LinX offline metadata check"\nbase_url = "http://127.0.0.1:9/v1"\nwire_api = "responses"\nrequires_openai_auth = false\n'
        private(work/'config.toml',config)
        # No API credentials and no upstream access; this is not a billable model test.
        env={k:v for k,v in os.environ.items() if not any(x in k.upper() for x in ('API_KEY','TOKEN','SECRET','PASSWORD'))}
        env['CODEX_HOME']=tmp
        with (work/'stderr.log').open('w+') as errors:
            process=subprocess.Popen([str(home/'bin/codex'),'app-server'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                     stderr=errors,env=env,cwd=tmp,universal_newlines=True,bufsize=1)
            selector=selectors.DefaultSelector();selector.register(process.stdout,selectors.EVENT_READ)
            def request(number,method,params):
                process.stdin.write(json.dumps({'id':number,'method':method,'params':params})+'\n');process.stdin.flush()
                deadline=time.monotonic()+20
                while time.monotonic()<deadline:
                    if not selector.select(max(0,deadline-time.monotonic())):break
                    line=process.stdout.readline()
                    if not line:break
                    data=json.loads(line)
                    if data.get('id')==number:
                        if 'error' in data:raise ValueError('Codex 拒绝模型目录：'+str(data['error'])[:600])
                        return data.get('result',{})
                errors.flush();errors.seek(0)
                raise ValueError('模型目录离线校验未完成；现有配置保持不变。'+errors.read()[-1000:])
            try:
                request(1,'initialize',{'clientInfo':{'name':'linx_catalog_check','version':'1'},'capabilities':{'experimentalApi':True}})
                rows=[];cursor=None;seen=set()
                for number in range(2,12):
                    result=request(number,'model/list',{'cursor':cursor,'limit':100,'includeHidden':False})
                    rows.extend(result.get('data',[]));cursor=result.get('nextCursor')
                    if not cursor:break
                    if cursor in seen:raise ValueError('模型目录分页重复；未修改现有配置')
                    seen.add(cursor)
                observed=[row.get('model',row.get('id')) for row in rows]
                if len(observed)!=len(set(observed)) or set(observed)!=set(expected):
                    errors.flush();errors.seek(0)
                    raise ValueError('Codex 返回的模型与所选模型不一致；未修改现有配置。'+errors.read()[-1600:])
                return rows if return_rows else observed
            finally:
                selector.close()
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait()
                process.stdin.close();process.stdout.close()

def prepare_model_catalog(home,state,profile):
    identity=catalog_binary_identity(home);models=selected_models(profile)
    cache_key=fingerprint({'binary':identity,'models':models,'revision':TOOL_COMPAT_REVISION})
    cache=state/'catalog-cache'/(cache_key+'.json')
    try:
        record=json.loads(cache.read_text())
        target=state/'catalogs'/(record['hash']+'.json')
        content=target.read_bytes()
        rows=json.loads(content)['models']
        compatibility='functions' if any(row.get('description')=='通过当前供应商使用的自定义 Responses 模型' for row in rows) else 'native'
        if (record.get('binary')==identity and record.get('models')==models
                and record.get('compatibility_revision')==TOOL_COMPAT_REVISION
                and record.get('path')==str(target) and record.get('tool_compatibility')==compatibility
                and hashlib.sha256(content).hexdigest()==record['hash']
                and [row['slug'] for row in rows]==models and catalog_binary_identity(home)==identity):
            return record
    except (OSError,ValueError,KeyError,TypeError):pass
    version=codex_version(home)
    catalog=make_model_catalog(home,profile)
    observed=probe_model_catalog(home,catalog,models)
    if catalog_binary_identity(home)!=identity:raise ValueError('校验期间 Codex 已更新，请重新同步；未修改现有配置')
    content=json.dumps(catalog,ensure_ascii=False,sort_keys=True)
    digest=hashlib.sha256(content.encode()).hexdigest()
    target=state/'catalogs'/(digest+'.json')
    # Content-addressed files keep every backup's referenced catalogue immutable.
    if not target.exists() or target.read_bytes()!=content.encode():private(target,content)
    custom=any(row.get('description')=='通过当前供应商使用的自定义 Responses 模型' for row in catalog['models'])
    record={'path':str(target),'hash':digest,'version':version,'models':models,'binary':identity,
            'tool_compatibility':'functions' if custom else 'native','compatibility_revision':TOOL_COMPAT_REVISION}
    private(cache,json.dumps(record,ensure_ascii=False))
    return record

def catalog_binary_identity(home):
    executable=(home/'bin/codex').resolve();stat=executable.stat()
    return {'path':str(executable),'size':stat.st_size,'mtime_ns':stat.st_mtime_ns}

def configured_catalog(config):
    first=re.search(r'^\s*\[',config,re.M);root=config[:first.start()] if first else config
    found=re.search(r'^\s*model_catalog_json\s*=\s*("(?:[^"\\]|\\.)*")\s*$',root,re.M)
    if not found:return None
    try:return json.loads(found.group(1))
    except ValueError:return None

def runtime_model_ids(home):
    connection=Rpc(home)
    try:
        models=[];cursor=None;seen=set()
        for _ in range(10):
            page=connection.rpc('model/list',{'cursor':cursor,'limit':100,'includeHidden':False})
            models.extend(row.get('model',row.get('id')) for row in page.get('data',[]))
            cursor=page.get('nextCursor')
            if not cursor:return models
            if cursor in seen:break
            seen.add(cursor)
        raise ValueError('模型目录分页未完成')
    finally:connection.close()

def catalog_status(home,profile,record,config,runtime_ok):
    if not profile:return {'status':'not_synced','can_sync':False,'note':'先启用一个供应商'}
    result={'status':'not_synced','can_sync':runtime_ok,'note':'启用配置时会同步所选模型'}
    if not record:return result
    result.update(version=record.get('version'),count=len(record.get('models',[])))
    result['tool_compatibility']=record.get('tool_compatibility','native')
    if record.get('compatibility_revision')!=TOOL_COMPAT_REVISION:
        return dict(result,status='needs_check',note='需要校验当前供应商的工具格式兼容性')
    if record.get('tool_compatibility')=='functions' and not has_function_tool_settings(config):
        return dict(result,status='needs_sync',note='标准函数工具兼容设置尚未应用')
    if record.get('tool_compatibility')=='functions' and not compatibility_proxy_alive(record.get('compat_proxy')):
        return dict(result,status='needs_sync',note='服务器工具兼容服务需要恢复')
    if record.get('binary')!=catalog_binary_identity(home):
        return dict(result,status='needs_check',note='远程 Codex 已更新，需要重新校验模型目录')
    if configured_catalog(config)!=record.get('path') or record.get('models')!=selected_models(profile):
        return dict(result,status='needs_sync',note='所选模型或目录配置已改变，需要同步')
    try:
        content=Path(record['path']).read_bytes()
        if hashlib.sha256(content).hexdigest()!=record.get('hash'):raise ValueError('目录已改变')
        if runtime_ok:
            observed=runtime_model_ids(home)
            if len(observed)!=len(record['models']) or set(observed)!=set(record['models']):
                return dict(result,status='needs_sync',note='运行中的模型列表尚未匹配所选模型')
            return dict(result,status='synced',note='远端原生模型列表已匹配当前供应商的所选模型')
        return dict(result,status='pending',note='目录已保存，等待远程服务确认')
    except (OSError,ValueError,KeyError):
        return dict(result,status='needs_sync',note='模型目录缺失或校验未通过，需要重新同步')

def private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.write-', dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8', newline=chr(10)) as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(name, str(path))
    finally:
        if os.path.exists(name): os.unlink(name)

def quote(value): return json.dumps(value, ensure_ascii=False)

def selected_models(p):
    values = p.get('selected_models')
    if values is None: values = [p.get('model', '')]
    if not isinstance(values, list) or not all(isinstance(x, str) for x in values):
        raise ValueError('模型需要为 ID 列表')
    values = list(dict.fromkeys(x.strip() for x in values if x.strip()))
    if not values or len(values) > 500 or any(len(x) > 256 or any(ord(c) < 32 for c in x) for x in values):
        raise ValueError('请至少勾选一个模型，最多 500 个，模型 ID 不能包含控制字符')
    return values

def validate(p):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,48}', p.get('id', '')):
        raise ValueError('配置 ID 只能包含字母、数字、下划线、短横线，最多 48 位')
    p['selected_models'] = selected_models(p)
    if p.get('model') not in p['selected_models']: p['model'] = p['selected_models'][0]
    for k in ('name', 'base_url', 'model', 'key'):
        if not isinstance(p.get(k), str) or not p[k].strip(): raise ValueError('缺少字段：' + k)
    u = urllib.parse.urlsplit(p['base_url'])
    if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password or u.query or u.fragment:
        raise ValueError('接口地址应为 http(s) API 根地址，不含密码、查询参数或 #')
    if p.get('mode') not in ('direct', 'tunnel'): raise ValueError('连接方式无效')
    if p['mode'] == 'tunnel' and u.hostname != '127.0.0.1': raise ValueError('隧道地址必须指向服务器 127.0.0.1')
    p['base_url'] = p['base_url'].rstrip('/')
    return p

def render(original, p, aliases=(), catalog_path=None,function_tools_only=False):
    text = restore_tool_settings(original)
    for a, b in ((START, END), ('# >>> ws-sub2api >>>', '# <<< ws-sub2api <<<')):
        if text.count(a) != text.count(b): raise ValueError('配置块不完整，已停止写入 config.toml')
        text = re.sub(re.escape(a) + r'.*?' + re.escape(b) + r'\n?', '', text, flags=re.S)
    first = re.search(r'^\s*\[', text, re.M)
    pos = first.start() if first else len(text)
    top, rest = text[:pos], text[pos:]
    top = re.sub(r'^\s*(model|model_provider)\s*=.*(?:\n|$)', '', top, flags=re.M)
    pid = PROVIDER_ID
    root = START + '\nmodel_provider = ' + quote(pid) + '\nmodel = ' + quote(p['model']) + '\n' + END + '\n'
    if catalog_path is not None:
        # Explicit synchronization owns this root setting; backups preserve any prior value.
        top=re.sub(r'^\s*model_catalog_json\s*=.*(?:\n|$)','',top,flags=re.M)
        root=root.replace('\n'+END,'\nmodel_catalog_json = '+quote(str(catalog_path))+'\n'+END,1)
    ids = sorted(set([pid] + list(aliases)))
    table = '\n' + START + '\n'
    for provider in ids:
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', provider): raise ValueError('供应商标签无效')
        table += ('[model_providers.' + provider + ']\nname = ' + quote(p['name'])
                  + '\nbase_url = ' + quote(p['base_url'])
                  + '\nwire_api = "responses"\nrequires_openai_auth = true\nsupports_websockets = false\n')
    table += END + '\n'
    return function_tool_settings(root + top + rest + table,function_tools_only)


def route_view(config):
    # Inspect routing fields only: Cindy owns and refreshes its separate MCP block.
    root, tables, section = {}, {}, ''
    for line in config.splitlines():
        line = line.strip()
        if not line or line.startswith('#'): continue
        if line.startswith('['):
            section = line.strip('[]').strip()
            continue
        m = re.match(r'([a-zA-Z0-9_]+)\s*=\s*(.*)', line)
        if not m: continue
        k, raw = m.groups()
        try: value = json.loads(raw)
        except ValueError: value = raw
        if not section and k in ('model_provider', 'model'): root[k] = value
        elif section.startswith('model_providers.') and k in ('name','base_url','wire_api','requires_openai_auth','supports_websockets'):
            tables.setdefault(section[len('model_providers.'):], {})[k] = value
    return {'root': root, 'tables': tables}

def daemon_identity(home):
    try:
        d = json.loads((home / 'app-server-daemon/daemon.pid').read_text())
        os.kill(int(d['pid']), 0)
        return {k:d[k] for k in ('pid','processIdentity') if k in d}
    except (OSError, ValueError, KeyError): return None

def runtime_snapshot(home):
    identity = daemon_identity(home)
    if not identity: return {'running': False, 'threads': [], 'identity': None}
    c = Rpc(home)
    try:
        threads, cursor = [], None
        while True:
            page = c.rpc('thread/loaded/list', {'cursor':cursor})
            for tid in page.get('data', []):
                t = c.rpc('thread/read', {'threadId':tid,'includeTurns':False})['thread']
                row={'id':tid,'provider':t.get('modelProvider'),'status':t.get('status',{}).get('type')}
                if row['status']=='systemError':
                    detail=c.rpc('thread/read',{'threadId':tid,'includeTurns':True})['thread']
                    turns=detail.get('turns',[])
                    row['terminal_error']=bool(detail.get('status',{}).get('type')=='systemError' and turns
                        and all(turn.get('status') in ('completed','failed','interrupted') for turn in turns))
                threads.append(row)
            cursor = page.get('nextCursor')
            if not cursor: break
        return {'running':True,'threads':threads,'identity':identity}
    finally: c.close()

def thread_is_idle(thread):
    return thread.get('status') in ('idle','notLoaded') or (thread.get('status')=='systemError' and thread.get('terminal_error') is True)

def idle_snapshot(home):
    snap = runtime_snapshot(home)
    if any(not thread_is_idle(t) for t in snap['threads']):
        raise ValueError('服务器还有正在生成或等待处理的对话；本次未切换。请等所有远程任务结束后再启用')
    # Read IDs/provider metadata only. Use the native resume API to restore idle saved sessions.
    snap['saved_threads']=[]
    dbfile=home/'state_5.sqlite'
    if dbfile.is_file():
        connection=sqlite3.connect('file:'+urllib.parse.quote(str(dbfile))+'?mode=ro',uri=True)
        try:
            columns={row[1] for row in connection.execute('pragma table_info(threads)')}
            model_column='model' if 'model' in columns else 'NULL'
            effort_column='reasoning_effort' if 'reasoning_effort' in columns else 'NULL'
            query='select id,model_provider,'+model_column+','+effort_column+' from threads where archived=0 order by updated_at desc limit 200'
            snap['saved_threads']=[{'id':row[0],'provider':row[1],'model':row[2],'effort':row[3],'status':'notLoaded'} for row in connection.execute(query)]
        finally:connection.close()
    return snap

def refresh_runtime(home, before, provider, model, eligible,preserve_models=False,allowed_models=None):
    if not before['running']: return {'running':False,'verified':False,'note':'配置已保存，远程 Codex 启动时生效'}
    # Recheck immediately before restarting; preserve the existing MCP/proxy environment.
    idle_snapshot(home)
    pid = before['identity']['pid']
    env = dict(os.environ, CODEX_HOME=str(home))
    try:
        for entry in (Path('/proc') / str(pid) / 'environ').read_bytes().split(bytes([0])):
            if b'=' in entry:
                k,v=entry.split(b'=',1);env[k.decode()]=v.decode()
    except OSError: raise ValueError('无法保留远程服务环境，已停止刷新；未终止任何任务')
    env['CODEX_HOME']=str(home)
    r=subprocess.run([str(home/'bin/codex'),'app-server','daemon','restart'],env=env,
                     stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=35)
    if r.returncode: raise ValueError('远程 Codex 服务刷新未完成；请检查服务器服务状态')
    c=Rpc(home)
    try:
        restored=[]
        candidates={t['id']:t for t in before.get('saved_threads',[])}
        for thread in before['threads']:candidates[thread['id']]=dict(candidates.get(thread['id'],{}),**thread)
        for t in candidates.values():
            if t['provider'] not in eligible: continue
            params={'threadId':t['id'],'modelProvider':provider,'excludeTurns':True}
            if not preserve_models:params['model']=model
            else:
                retained=t.get('model') and (allowed_models is None or t['model'] in allowed_models)
                if retained:params['model']=t['model']
                elif allowed_models is not None:params['model']=model
                if retained and t.get('effort'):params['config']={'model_reasoning_effort':t['effort']}
            result=c.rpc('thread/resume',params)
            if result.get('modelProvider') != provider: raise ValueError('旧对话未确认切换，不能标为已生效')
            restored.append(t['id'])
        return {'running':True,'verified':True,'identity':daemon_identity(home),'restored':restored}
    finally:c.close()

def summary(p): return dict({k: v for k, v in p.items() if k != 'key'}, selected_models=selected_models(p))
def fingerprint(p): return hashlib.sha256(json.dumps(p, sort_keys=True).encode()).hexdigest()
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None

def models(p):
    req = urllib.request.Request(p['base_url'] + '/models', headers={
        'Authorization': 'Bearer ' + p['key'],
        'Accept': 'application/json',
        'User-Agent': 'LinX-Remote/1.5.2',
    })
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=25) as r:
            raw=r.read(4*1024*1024+1)
        if len(raw)>4*1024*1024:raise ValueError('模型列表超过大小限制；请确认 API 地址正确')
        try:data=json.loads(raw)
        except (ValueError,UnicodeError):raise ValueError('模型接口返回非 JSON 内容；请检查 API 根地址或站点防护设置') from None
        if not isinstance(data,dict) or not isinstance(data.get('data'),list):
            raise ValueError('模型接口未返回标准 data 模型列表；请检查 API 地址及服务提供的模型列表格式')
        return sorted({x['id'] for x in data.get('data', []) if isinstance(x, dict) and isinstance(x.get('id'), str)})
    except urllib.error.HTTPError as e:
        raw=e.read(65536)
        try:detail=json.loads(raw)
        except (ValueError,UnicodeError):detail={}
        if not isinstance(detail,dict):detail={}
        cloudflare=detail.get('cloudflare_error') is True or detail.get('error_code')==1010
        if cloudflare:
            raise ValueError('获取模型返回 HTTP %s：Cloudflare 站点防护拒绝请求%s；请联系中转站检查客户端或服务器出口放行规则，不代表 API Key 错误' % (e.code,'（1010，客户端标识被拦截）' if detail.get('error_code')==1010 else '')) from None
        if e.code==401:
            raise ValueError('获取模型返回 HTTP 401：中转站未接受此 API Key；请核对该站点的 Key 与权限') from None
        if e.code==403:
            raise ValueError('获取模型返回 HTTP 403：中转站拒绝服务器请求；请检查服务器出口限制、站点防护与 Key 权限。本机获取成功不代表服务器也被允许访问') from None
        if 300<=e.code<400:
            raise ValueError('模型接口返回 HTTP %s 跳转；为保护 API Key 未跟随跳转，请填写最终 API 根地址' % e.code) from None
        raise ValueError('获取模型返回 HTTP %s；请检查 API 地址、服务状态与权限' % e.code) from None

def run(req):
    home = Path(req['home']).expanduser()
    if not home.is_absolute() or not (home / 'bin/codex').is_file():
        raise ValueError('Codex 目录应为含 bin/codex 的 Cindy 远程 Codex 目录')
    state = home / 'portable-providers'
    state.mkdir(mode=0o700, exist_ok=True); os.chmod(str(state), 0o700)
    import fcntl
    with open(str(state / 'lock'), 'a') as lock:
        os.chmod(str(state / 'lock'), 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        return locked(req, home, state)

def locked(req, home, state):
    path = state / 'profiles.json'
    db = json.loads(path.read_text()) if path.exists() else {'version': 1, 'profiles': [], 'active': None}
    action = req['action']
    def save(): private(path, json.dumps(db, ensure_ascii=False, indent=2))
    def get(pid): return next(p for p in db['profiles'] if p['id'] == pid)
    def info():
        config = (home / 'config.toml').read_text()
        active = db.get('active')
        current = next((p for p in db['profiles'] if p['id'] == active), None)
        aliases = ['sub2api_ws'] + ['portable_' + p['id'] for p in db['profiles']]
        matches = False
        if current:
            expected=route_view(render('',routed_profile(current,(db.get('model_catalog') or {}).get('compat_proxy')),aliases))
            actual=route_view(config)
            matches = (actual['root']==expected['root'] and actual['tables'].get(PROVIDER_ID)==expected['tables'][PROVIDER_ID]
                       and all(actual['tables'][k]==expected['tables'][k] for k in aliases if k in actual['tables']))
            try: matches = matches and json.loads((home/'auth.json').read_text()).get('OPENAI_API_KEY')==current['key']
            except (OSError,ValueError): matches=False
        try: runtime=runtime_snapshot(home)
        except Exception: runtime={'running':True,'threads':[],'unavailable':True,'identity':None}
        eligible=set(aliases+[PROVIDER_ID])
        applied=db.get('runtime_applied_identity') or {}
        observed=runtime.get('identity') or {}
        a,b=applied.get('processIdentity',{}),observed.get('processIdentity',{})
        # Cindy may legitimately rebootstrap its daemon after refreshing MCP credentials.
        # A newer daemon on this boot starts from the already verified selected routing.
        cache_fresh=(bool(applied) and applied==observed) or bool(a.get('bootId') and a.get('bootId')==b.get('bootId')
                    and isinstance(a.get('startTicks'),int) and isinstance(b.get('startTicks'),int)
                    and b['startTicks']>a['startTicks'])
        runtime_ok=bool(matches and runtime['running'] and not runtime.get('unavailable')
                        and cache_fresh and all(t['provider'] in eligible for t in runtime['threads']))
        catalogue=catalog_status(home,current,db.get('model_catalog'),config,runtime['running'] and not runtime.get('unavailable'))
        catalogue['can_sync']=bool(current and matches)
        catalogue['can_auto_sync']=bool(current and matches and runtime['running'] and not runtime.get('unavailable')
                                        and db.get('model_catalog') and db['model_catalog'].get('models')==selected_models(current)
                                        and all(thread_is_idle(t) for t in runtime['threads']))
        catalogue.update(model_menu_visibility((db.get('model_catalog') or {}).get('models',selected_models(current) if current else [])))
        return {'profiles':[summary(p) for p in db['profiles']],'active':active,'home':str(home),
                'config_matches_active':matches,'runtime_matches_active':runtime_ok,'runtime':runtime,
                'last_runtime_apply':db.get('last_runtime_apply'),
                'model_menu_policy':{'hidden_model_ids':list(CINDY_INTERNAL_MODEL_IDS),'custom_model_ids_supported':True},
                'model_catalog':catalogue}
    if action == 'tunnel_check':
        port = int(req['port'])
        if not 1 <= port <= 65535: raise ValueError('端口无效')
        with urllib.request.build_opener(NoRedirect()).open('http://127.0.0.1:%d/health' % port, timeout=10) as r:
            health = json.load(r)
        sockets = subprocess.run(['ss', '-ltnH'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        listeners = [x.split()[3] for x in sockets.stdout.splitlines() if len(x.split()) > 3 and x.split()[3].endswith(':'+str(port))]
        public = [x for x in listeners if not (x.startswith('127.0.0.1:') or x.startswith('[::1]:') or x.startswith('::1:'))]
        if public: raise ValueError('隧道端口出现非回环监听：'+', '.join(public)+'；请停止隧道并联系管理员检查 GatewayPorts')
        return {'health': health, 'listeners': listeners, 'note': '服务器已通过隧道访问本机 Sub2API'}
    if action == 'import_current':
        if not db['profiles']:
            config = (home / 'config.toml').read_text()
            url = re.search(r'^base_url\s*=\s*"([^"]+)"', config, re.M)
            model = re.search(r'^model\s*=\s*"([^"]+)"', config, re.M)
            if not url or not model or url.group(1) != 'http://127.0.0.1:18765/v1':
                raise ValueError('当前配置与预期 Sub2API 隧道不符，请手动添加，未导入任何密钥')
            auth = json.loads((home / 'auth.json').read_text())
            p = validate({'id': 'imported_sub2api', 'name': 'Sub2API · 本机服务', 'base_url': url.group(1),
                          'model': model.group(1), 'key': auth.get('OPENAI_API_KEY'), 'mode': 'tunnel'})
            db['profiles'].append(p); save()
        return info()
    if action == 'list': return info()
    if action == 'preview_models':
        p = dict(req['profile'])
        p['name'] = p.get('name') or '新供应商'
        existing = next((v for v in db['profiles'] if v['id'] == p.get('id')), None)
        if not p.get('key') and existing: p['key'] = existing['key']
        p['model'] = p.get('model') or 'pending-selection'
        p.pop('selected_models', None)
        return {'models': models(validate(p))}
    candidate=None
    editing_active=False
    if action == 'save':
        p = dict(req['profile'])
        existing = next((v for v in db['profiles'] if v['id'] == p.get('id')), None)
        if not p.get('key') and existing: p['key'] = existing['key']
        validate(p)
        p = {k: p[k] for k in ('id', 'name', 'base_url', 'model', 'key', 'mode', 'selected_models')}
        editing_active=p['id']==db.get('active')
        if not editing_active:
            db['profiles'] = [v for v in db['profiles'] if v['id'] != p['id']] + [p]
            save()
            return {'saved':p['id'],'auto_applied':False,'note':'已保存；当前供应商保持不变','state':info()}
        # Keep the old profile committed until config, credentials and live catalogue verify.
        candidate=p
        action='activate'
        req=dict(req,id=p['id'])
    if action == 'models': return {'models': models(get(req['id']))}
    if action in ('activate','catalog_sync'):
        sync_only=action=='catalog_sync'
        if sync_only:
            current=info()
            if not current['config_matches_active']:raise ValueError('当前配置与已保存供应商不一致，请先启用要使用的供应商')
            if current['model_catalog']['status']=='synced':
                return {'model_catalog':current['model_catalog'],'note':'原生模型列表校验通过，无需重新加载远程服务'}
        p = validate(dict(candidate or get(db['active'] if sync_only else req['id'])))
        if not sync_only and p['id']==db.get('active'):
            current=info()
            old=get(p['id'])
            same_route=all(p.get(k)==old.get(k) for k in ('base_url','model','key','selected_models'))
            if same_route and current['config_matches_active'] and current['runtime_matches_active'] and current['model_catalog']['status']=='synced':
                if candidate:
                    db['profiles']=[p if v['id']==p['id'] else v for v in db['profiles']]
                    db['applied_profile_hash']=fingerprint(p);save()
                    current=info()
                return {'saved':p['id'],'active':p['id'],'auto_applied':editing_active,
                        'unchanged':True,'state':current,'note':'当前配置与模型目录已同步，无需重新加载'}
        before = idle_snapshot(home)
        cfg, auth = home / 'config.toml', home / 'auth.json'
        old_cfg=cfg.read_text();old_auth=auth.read_text() if auth.exists() else None
        catalogue=prepare_model_catalog(home,state,p)
        if catalogue.get('tool_compatibility')=='functions':catalogue['compat_proxy']=ensure_compatibility_proxy(home,state,p)
        aliases = ['sub2api_ws'] + ['portable_' + v['id'] for v in db['profiles']]
        output = render(old_cfg, routed_profile(p,catalogue.get('compat_proxy')), aliases,catalogue['path'],catalogue.get('tool_compatibility')=='functions')
        try: import tomllib
        except ImportError: pass
        else: tomllib.loads(output)
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        backup = state / 'backups' / stamp
        backup.mkdir(parents=True, mode=0o700)
        private(backup / 'config.toml', old_cfg)
        if old_auth is not None: private(backup / 'auth.json', old_auth)
        private(backup / 'meta.json', json.dumps({'auth_existed': old_auth is not None, 'active': db.get('active'),
                                                'model_catalog':db.get('model_catalog')}))
        before=idle_snapshot(home)
        if cfg.read_text()!=old_cfg or (auth.read_text() if auth.exists() else None)!=old_auth:
            raise ValueError('校验期间配置被其他程序修改，已停止应用，请重试')
        try:
            if not sync_only:private(auth, json.dumps({'OPENAI_API_KEY': p['key']}))
            private(cfg, output)
            arguments=(home,before,PROVIDER_ID,p['model'],set(aliases+[PROVIDER_ID]))
            options=({'preserve_models':True} if sync_only else
                     {'preserve_models':True,'allowed_models':selected_models(p)} if editing_active else {})
            refreshed = refresh_runtime(*arguments,**options)
            if refreshed.get('running') and set(runtime_model_ids(home))!=set(catalogue['models']):
                raise ValueError('远程服务未返回所选模型目录，本次修改已回退')
        except Exception as apply_error:
            private(cfg, old_cfg)
            if old_auth is not None: private(auth, old_auth)
            elif auth.exists(): auth.unlink()
            if before['running'] and daemon_identity(home) != before['identity']:
                try:
                    old_root=route_view(old_cfg)['root']
                    previous=runtime_snapshot(home)
                    # Restore the same thread IDs, even if the failed apply only resumed some.
                    previous['threads']=before['threads']
                    previous['saved_threads']=before.get('saved_threads',[])
                    refresh_runtime(home,previous,old_root.get('model_provider','openai'),old_root.get('model',p['model']),set(aliases+[PROVIDER_ID]),
                                    **({'preserve_models':True} if sync_only or editing_active else {}))
                except Exception:
                    raise ValueError('切换未完成，原配置已恢复，但远程服务恢复仍需检查；未标记为已启用')
            raise apply_error
        db['profiles'] = [p if v['id'] == p['id'] else v for v in db['profiles']]
        db['active'] = p['id']; db['last_backup'] = stamp
        db['applied_profile_hash'] = fingerprint(p)
        db['applied_auth_hash'] = hashlib.sha256(auth.read_bytes()).hexdigest()
        db['applied_hash'] = hashlib.sha256(output.encode()).hexdigest()
        db['runtime_applied_identity'] = refreshed.get('identity')
        db['last_runtime_apply'] = refreshed
        db['model_catalog']=catalogue
        save()
        return {'active': p['id'], 'model': p['model'], 'backup': str(backup),
                'saved':p['id'],'auto_applied':editing_active,'state':info(),
                'runtime': refreshed,'model_catalog':catalogue,
                'note': ('已同步 '+str(len(catalogue['models']))+' 个模型到远端原生列表；现有供应商保持不变' if sync_only else
                         '当前供应商已保存并自动同步，所选模型已刷新' if editing_active else
                         '供应商已启用，所选模型已同步到远端原生列表') if refreshed.get('verified') else refreshed['note']}
    if action == 'rollback':
        if not db.get('last_backup'): raise ValueError('没有本工具创建的可回退备份')
        before=idle_snapshot(home)
        cfg,auth=home/'config.toml',home/'auth.json'
        backup=state/'backups'/db['last_backup'];meta=json.loads((backup/'meta.json').read_text())
        old_cfg=cfg.read_text();old_auth=auth.read_text() if auth.exists() else None
        aliases=['sub2api_ws']+['portable_'+v['id'] for v in db['profiles']]
        current=get(db['active'])
        expected=render((backup/'config.toml').read_text(),routed_profile(current,(db.get('model_catalog') or {}).get('compat_proxy')),aliases,(db.get('model_catalog') or {}).get('path'),(db.get('model_catalog') or {}).get('tool_compatibility')=='functions')
        def without_mcp(text):
            return re.sub(r'# >>> cindy-remote-mcp.*?# <<< cindy-remote-mcp <<<', '',text,flags=re.S).strip()
        if without_mcp(old_cfg)!=without_mcp(expected):
            raise ValueError('配置被其他程序修改，已阻止回退覆盖；请先检查设置')
        if not auth.exists() or hashlib.sha256(auth.read_bytes()).hexdigest()!=db.get('applied_auth_hash'):
            raise ValueError('认证在切换后已改变，已阻止回退覆盖')
        latest_mcp=re.search(r'# >>> cindy-remote-mcp.*?# <<< cindy-remote-mcp <<<',old_cfg,re.S)
        restored=without_mcp((backup/'config.toml').read_text())+chr(10)
        if latest_mcp:restored+=chr(10)+latest_mcp.group(0)+chr(10)
        previous=route_view(restored)['root']
        try:
            private(cfg,restored)
            if meta['auth_existed']:private(auth,(backup/'auth.json').read_text())
            elif auth.exists():auth.unlink()
            refreshed=refresh_runtime(home,before,previous.get('model_provider','openai'),previous.get('model',current['model']),set(aliases+[PROVIDER_ID]))
        except Exception:
            private(cfg,old_cfg)
            if old_auth is not None:private(auth,old_auth)
            raise ValueError('回退未完成，已恢复回退前的文件；请检查远程服务运行状态')
        db['active']=meta.get('active');db.pop('last_backup',None);db.pop('applied_hash',None)
        if meta.get('model_catalog'):db['model_catalog']=meta['model_catalog']
        else:db.pop('model_catalog',None)
        db['runtime_applied_identity']=refreshed.get('identity');db['last_runtime_apply']=refreshed;save()
        return {'runtime':refreshed,'note':'已回退并刷新远程服务，当前 MCP 连接配置已保留'}
    if action == 'test_active':
        current=info()
        if not current['runtime_matches_active']: raise ValueError('运行中的路由尚未确认，请先启用该供应商')
        p=dict(get(db['active']))
        p['model']=selected_test_model(p,req.get('model'))
        c=Rpc(home);tid=None;turn=None
        try:
            with tempfile.TemporaryDirectory(prefix='cindy-live-route-test-') as tmp:
                result=c.rpc('thread/start',{'cwd':tmp,'ephemeral':True,'modelProvider':PROVIDER_ID,
                    'model':p['model'],'approvalPolicy':'never','sandbox':'read-only',
                    'developerInstructions':'Reply with exactly OK. Do not run any tools.'})
                tid=result['thread']['id']
                if result.get('modelProvider')!=PROVIDER_ID:raise ValueError('实际运行的供应商未匹配，测试已停止')
                result=c.rpc('turn/start',{'threadId':tid,'input':[{'type':'text','text':'Reply with exactly OK. Do not run any tools.'}]})
                turn=result['turn']['id'];deadline=time.monotonic()+75;reply=''
                while time.monotonic()<deadline:
                    d=c.receive();params=d.get('params',{})
                    if 'id' in d and 'method' in d:
                        c.send(json.dumps({'id':d['id'],'error':{'code':-32601,'message':'No tools available in this connectivity test'}}));continue
                    if params.get('threadId')!=tid:continue
                    if d.get('method')=='item/agentMessage/delta':reply+=params.get('delta','')
                    if d.get('method')=='turn/completed':
                        if params['turn'].get('status')!='completed':
                            error=params['turn'].get('error') or {};message=error.get('message') if isinstance(error,dict) else str(error)
                            raise ValueError('实际对话请求失败：'+(message or '请检查供应商状态').replace(p['key'],'[已隐藏密钥]')[:1200])
                        return {'ok':True,'model':p['model'],'provider':p['name'],'base_url':p['base_url'],
                                'model_provider':PROVIDER_ID,'reply':reply[:100],
                                'note':'已通过当前远程 Codex 服务完成实际请求，未使用独立 CLI'}
                raise ValueError('实际对话测试超时')
        finally:
            if tid:
                try:
                    if turn:c.rpc('turn/interrupt',{'threadId':tid,'turnId':turn})
                except Exception:pass
                try:c.rpc('thread/unsubscribe',{'threadId':tid})
                except Exception:pass
            c.close()
    if action == 'test':
        p = validate(dict(get(req['id'])))
        p['model'] = selected_test_model(p,req.get('model'))
        catalogue=prepare_model_catalog(home,state,p)
        if catalogue.get('tool_compatibility')=='functions':catalogue['compat_proxy']=ensure_compatibility_proxy(home,state,p)
        with tempfile.TemporaryDirectory(prefix='cindy-provider-test-') as tmp:
            h = Path(tmp)
            private(h / 'auth.json', json.dumps({'OPENAI_API_KEY': p['key']}))
            private(h / 'config.toml', render('',routed_profile(p,catalogue.get('compat_proxy')),catalog_path=catalogue['path'],function_tools_only=catalogue.get('tool_compatibility')=='functions'))
            env = os.environ.copy(); env['CODEX_HOME'] = tmp; env.pop('OPENAI_API_KEY', None)
            pid = PROVIDER_ID
            cmd = [str(home / 'bin/codex'), 'exec', '--ephemeral', '--skip-git-repo-check', '--ignore-rules',
                   '-s', 'read-only', '-C', tmp, '-c', 'model_providers.' + pid + '.request_max_retries=0',
                   '-c', 'model_providers.' + pid + '.stream_max_retries=0', 'Reply with exactly OK. Do not run any tools.']
            try:
                r = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=env, universal_newlines=True, timeout=75, cwd=tmp)
            except subprocess.TimeoutExpired:
                raise ValueError('模型实测超过 75 秒；请检查隧道、服务器出网和上游账号状态')
            if r.returncode != 0:
                detail = (r.stderr + '\n' + r.stdout).replace(p['key'], '[已隐藏密钥]')[-2400:]
                raise ValueError('Codex 模型实测失败：\n' + detail)
            return {'ok': True, 'model': p['model'], 'reply': r.stdout.strip()[:500], 'note': '独立测试成功，不影响运行中的会话'}
    raise ValueError('未知操作')

def selected_test_model(profile, requested):
    model = profile['model'] if requested is None else requested
    if not isinstance(model,str) or not model or model not in (profile.get('selected_models') or [profile['model']]):
        raise ValueError('请选择该供应商已保存的一个模型进行测试')
    return model

LINX_COMPAT_PROXY_SOURCE = '"""Loopback-only Responses namespace adapter. No inference or tool execution here."""\nimport copy,hashlib,hmac,json,os,re,sys,urllib.request,urllib.error\nfrom pathlib import Path\nfrom http.server import BaseHTTPRequestHandler,ThreadingHTTPServer\n\nVERSION=1\nclass NoRedirect(urllib.request.HTTPRedirectHandler):\n def redirect_request(self,*args):return None\n\ndef normalize_request(payload):\n payload=copy.deepcopy(payload);aliases={};forward={}\n def alias(namespace,name,custom=False):\n  key=(namespace or \'\',name,custom)\n  if key in forward:return forward[key]\n  candidate=(namespace+\'__\' if namespace else \'\')+name\n  if not re.fullmatch(r\'[A-Za-z0-9_-]{1,64}\',candidate):candidate=\'linx_\'+hashlib.sha256((str(key)).encode()).hexdigest()[:32]\n  if candidate in aliases and aliases[candidate]!=key:candidate=\'linx_\'+hashlib.sha256(str(key).encode()).hexdigest()[:48]\n  aliases[candidate]=key;forward[key]=candidate;return candidate\n def flatten(rows,namespace=\'\'):\n  result=[]\n  for original in rows:\n   row=copy.deepcopy(original);kind=row.get(\'type\')\n   if kind==\'namespace\':\n    child=row.get(\'name\',\'\');ns=namespace+\'.\'+child if namespace else child\n    result.extend(flatten(row.get(\'tools\',[]),ns));continue\n   if kind not in (\'function\',\'custom\'):raise ValueError(\'上游仅支持标准函数工具，当前仍有不兼容的内置工具类型：\'+str(kind))\n   name=row.get(\'name\')\n   if not isinstance(name,str):raise ValueError(\'工具缺少名称\')\n   custom=kind==\'custom\';name_alias=alias(namespace,name,custom)\n   if custom:\n    row={\'type\':\'function\',\'name\':name_alias,\'description\':row.get(\'description\',\'\'),\'parameters\':{\'type\':\'object\',\'properties\':{\'input\':{\'type\':\'string\',\'description\':\'Exact text input for this tool.\'}},\'required\':[\'input\'],\'additionalProperties\':False},\'strict\':True}\n   else:row[\'name\']=name_alias\n   if namespace:row[\'description\']=\'Tool group \'+namespace+\'. \'+row.get(\'description\',\'\')\n   result.append(row)\n  return result\n if \'tools\' in payload:payload[\'tools\']=flatten(payload[\'tools\'])\n id_map={}\n def response_id(value):\n  if not isinstance(value,str) or value.startswith(\'fc_\'):return value\n  if value not in id_map:id_map[value]=\'fc_\'+hashlib.sha256(value.encode()).hexdigest()[:32]\n  return id_map[value]\n def history(item):\n  if not isinstance(item,dict):return item\n  item=copy.deepcopy(item);kind=item.get(\'type\')\n  if kind in (\'function_call\',\'custom_tool_call\'):\n   namespace=item.pop(\'namespace\',None);custom=kind==\'custom_tool_call\';name=item.get(\'name\',\'\')\n   item[\'name\']=alias(namespace,name,custom)\n   if isinstance(item.get(\'id\'),str):item[\'id\']=response_id(item[\'id\'])\n   if custom:item[\'type\']=\'function_call\';item[\'arguments\']=json.dumps({\'input\':item.pop(\'input\',\'\')})\n  elif kind==\'custom_tool_call_output\':\n   item[\'type\']=\'function_call_output\'\n   if isinstance(item.get(\'call_id\'),str):item[\'call_id\']=response_id(item[\'call_id\'])\n  elif kind==\'function_call_output\' and isinstance(item.get(\'call_id\'),str):item[\'call_id\']=response_id(item[\'call_id\'])\n  return item\n if isinstance(payload.get(\'input\'),list):payload[\'input\']=[history(item) for item in payload[\'input\']]\n choice=payload.get(\'tool_choice\')\n if isinstance(choice,dict) and choice.get(\'type\') in (\'function\',\'custom\'):\n  choice[\'name\']=alias(choice.pop(\'namespace\',None),choice.get(\'name\',\'\'),choice.get(\'type\')==\'custom\');choice[\'type\']=\'function\'\n return payload,aliases\n\ndef restore_response(value,aliases):\n if isinstance(value,list):return [restore_response(v,aliases) for v in value]\n if not isinstance(value,dict):return value\n result={k:restore_response(v,aliases) for k,v in value.items()}\n if result.get(\'type\')==\'function_call\' and result.get(\'name\') in aliases:\n  namespace,name,custom=aliases[result[\'name\']];result[\'name\']=name\n  if namespace:result[\'namespace\']=namespace\n  if custom:\n   arguments=json.loads(result.pop(\'arguments\',\'{}\') or \'{}\')\n   result[\'type\']=\'custom_tool_call\';result[\'input\']=arguments.get(\'input\',\'\')\n return result\n\ndef private(path,data):\n path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)\n temp=path.with_name(path.name+\'.\'+str(os.getpid())+\'.tmp\')\n fd=os.open(str(temp),os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)\n with os.fdopen(fd,\'w\') as f:f.write(data)\n os.replace(str(temp),str(path))\n\nclass Handler(BaseHTTPRequestHandler):\n protocol_version=\'HTTP/1.1\'\n def log_message(self,*args):pass\n def reply(self,status,data):\n  raw=json.dumps(data,ensure_ascii=False).encode();self.send_response(status);self.send_header(\'Content-Type\',\'application/json\');self.send_header(\'Content-Length\',str(len(raw)));self.end_headers();self.wfile.write(raw)\n def do_GET(self):\n  if self.path==\'/health\':return self.reply(200,{\'linx_compat_version\':VERSION,\'pid\':os.getpid()})\n  self.forward()\n def do_POST(self):self.forward()\n def forward(self):\n  try:\n   match=re.fullmatch(r\'/([a-f0-9]{64})/v1(/(?:responses|models)(?:/[A-Za-z0-9_-]+)*(?:\\?[^\\r\\n]*)?)\',self.path)\n   if not match:return self.reply(404,{\'error\':{\'message\':\'Unknown LinX compatibility route\'}})\n   path=self.server.state/\'configs\'/(match.group(1)+\'.json\')\n   if not path.is_file():return self.reply(404,{\'error\':{\'message\':\'Compatibility snapshot not found\'}})\n   profile=json.loads(path.read_text());expected=\'Bearer \'+profile[\'key\']\n   if not hmac.compare_digest(self.headers.get(\'Authorization\',\'\'),expected):return self.reply(401,{\'error\':{\'message\':\'Invalid compatibility authorization\'}})\n   headers={\'Authorization\':expected,\'User-Agent\':\'LinX-Responses-Compat/1.6\',\'Accept\':\'text/event-stream, application/json\',\'Accept-Encoding\':\'identity\'}\n   for name in (\'OpenAI-Beta\',\'OpenAI-Organization\',\'OpenAI-Project\'):\n    if self.headers.get(name):headers[name]=self.headers[name]\n   body=None;aliases={}\n   if self.command==\'POST\':\n    size=int(self.headers.get(\'Content-Length\',\'0\'))\n    if size>64*1024*1024:return self.reply(413,{\'error\':{\'message\':\'Request too large\'}})\n    if self.headers.get(\'Content-Encoding\',\'identity\')!=\'identity\':raise ValueError(\'不支持压缩请求，请关闭该供应商的请求压缩\')\n    body=json.loads(self.rfile.read(size));body,aliases=normalize_request(body);body=json.dumps(body,ensure_ascii=False).encode();headers[\'Content-Type\']=\'application/json\'\n   request=urllib.request.Request(profile[\'base_url\'].rstrip(\'/\')+match.group(2),data=body,headers=headers,method=self.command)\n   try:upstream=urllib.request.build_opener(NoRedirect()).open(request,timeout=120)\n   except urllib.error.HTTPError as e:\n    data=e.read(1024*1024);self.send_response(e.code);self.send_header(\'Content-Type\',e.headers.get(\'Content-Type\',\'application/json\'));self.send_header(\'Content-Length\',str(len(data)));self.end_headers();self.wfile.write(data);return\n   with upstream:\n    content_type=upstream.headers.get(\'Content-Type\',\'application/json\')\n    if \'text/event-stream\' in content_type:\n     self.send_response(upstream.status);self.send_header(\'Content-Type\',\'text/event-stream\');self.send_header(\'Cache-Control\',\'no-cache\');self.send_header(\'Connection\',\'close\');self.end_headers();self.close_connection=True\n     block=[];custom_ids=set()\n     for line in upstream:\n      if line.strip():block.append(line);continue\n      if not block:self.wfile.write(b\'\\n\');self.wfile.flush();continue\n      fields=[v.decode(\'utf-8\') for v in block];data=\'\\n\'.join(v[5:].lstrip(\' \') for v in fields if v.startswith(\'data:\'))\n      if data and data!=\'[DONE]\':\n       event=json.loads(data);item=event.get(\'item\',{})\n       if isinstance(item,dict) and item.get(\'name\') in aliases and aliases[item[\'name\']][2]:custom_ids.add(item.get(\'id\'))\n       if event.get(\'type\',\'\').startswith(\'response.function_call_arguments.\') and event.get(\'item_id\') in custom_ids:block=[];continue\n       restored=restore_response(event,aliases)\n       fields=[v for v in fields if not v.startswith(\'data:\')];fields.append(\'data: \'+json.dumps(restored,ensure_ascii=False))\n      self.wfile.write((\'\\n\'.join(fields)+\'\\n\\n\').encode());self.wfile.flush();block=[]\n    else:\n     data=upstream.read(64*1024*1024)\n     if \'json\' in content_type:data=json.dumps(restore_response(json.loads(data),aliases),ensure_ascii=False).encode()\n     self.send_response(upstream.status);self.send_header(\'Content-Type\',content_type);self.send_header(\'Content-Length\',str(len(data)));self.end_headers();self.wfile.write(data)\n  except (BrokenPipeError,ConnectionResetError):pass\n  except ValueError as e:self.reply(400,{\'error\':{\'message\':\'LinX 工具兼容：\'+str(e)[:300]}})\n  except Exception:self.reply(502,{\'error\':{\'message\':\'LinX 兼容服务无法完成上游请求，请检查中转站连接\'}})\n\ndef main():\n state=Path(sys.argv[1]);server=ThreadingHTTPServer((\'127.0.0.1\',0),Handler);server.state=state;server.daemon_threads=True\n private(state/\'status.json\',json.dumps({\'pid\':os.getpid(),\'port\':server.server_port,\'version\':VERSION}))\n server.serve_forever()\n\nif __name__==\'__main__\':main()\n'

if __name__ == '__main__':
    try:
        result = run(json.load(sys.stdin)); print(json.dumps({'ok': True, 'data': result}, ensure_ascii=False))
    except Exception as e:
        message = str(e) if isinstance(e, (ValueError, StopIteration)) else (type(e).__name__ + '；请检查连接、地址和文件权限')
        print(json.dumps({'ok': False, 'error': message or '配置不存在，请重新读取列表'}, ensure_ascii=False))
