"""Bounded, read-only connection discovery. No credentials or global port scans."""
import concurrent.futures
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import urllib.parse
import urllib.request


def port_number(value):
    try: number = int(value)
    except (TypeError, ValueError): raise ValueError('端口需要填写 1 至 65535 的数字') from None
    if not 1 <= number <= 65535: raise ValueError('端口需要填写 1 至 65535 的数字')
    return number


def port_available(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(('127.0.0.1', port))
        return True
    except OSError: return False


def inspect_remote(request, account_home=None, username=None):
    if account_home is None:
        import pwd
        account = pwd.getpwuid(os.geteuid())
        account_home, username = account.pw_dir, account.pw_name
    base = Path(account_home).resolve()
    expected = base/'.xdt-server/v1/codex-home'
    explicit = Path(request['home']) if request.get('home') else None
    locations = [explicit] if explicit and explicit.is_absolute() else []
    locations += [expected, base/'.xdt-server/codex-home', base/'.codex']
    locations += sorted((base/'.xdt-server').glob('*/codex-home'))[:32]
    existing = []
    for candidate in locations:
        try:
            if (candidate/'bin/codex').is_file() and os.access(candidate/'bin/codex', os.X_OK):
                resolved = str(candidate.resolve())
                if resolved not in existing: existing.append(resolved)
        except OSError: pass
    if explicit and str(explicit.resolve()) in existing:
        chosen, status, note = str(explicit.resolve()), 'verified', '已核验所填 Codex 目录'
    elif str(expected) in existing:
        chosen, status, note = str(expected), 'verified', '已找到当前账号的 Cindy Codex 安装'
    elif len(existing) == 1:
        chosen, status, note = existing[0], 'verified', '已找到可用的 Codex 安装'
    elif len(existing) > 1:
        chosen, status, note = '', 'choose', '发现多个 Codex 目录，请选择与 Cindy 远程项目对应的目录'
    else:
        chosen, status, note = str(explicit if explicit and explicit.is_absolute() else expected), 'missing', '未发现可用安装；已建议个人目录，可点击“一键检查并准备”安装'
    directory = dict(status=status, value=chosen, candidates=existing, note=note)
    registered = set()
    if chosen:
        try:
            profile_file = Path(chosen)/'portable-providers/profiles.json'
            if profile_file.stat().st_size <= 2*1024*1024:
                data = json.loads(profile_file.read_text())
                for profile in data.get('profiles', []):
                    url = urllib.parse.urlsplit(profile.get('base_url', ''))
                    if profile.get('mode') == 'tunnel' and url.hostname in ('127.0.0.1', 'localhost', '::1') and url.port:
                        registered.add(url.port)
        except (OSError, ValueError, TypeError, AttributeError): pass
    preferred = port_number(request.get('remote_port', 18765))
    # Reuse the port referenced by existing server-side providers when unambiguous.
    selected = next(iter(registered)) if len(registered) == 1 and preferred not in registered else preferred
    if len(registered) > 1 and preferred not in registered:
        tunnel = dict(status='choose', value=preferred, note='现有供应商使用多个隧道端口，请选择本次要连接的端口')
    elif port_available(selected):
        tunnel = dict(status='available', value=selected, note='端口当前空闲；建立隧道时会再次检查')
    elif request.get('owned_tunnel') and selected == preferred:
        tunnel = dict(status='managed', value=selected, note='当前管理器的后台隧道正在使用，无需更换')
    elif selected in registered:
        tunnel = dict(status='in_use', value=selected, note='现有供应商使用此端口，但已有监听；可能是另一台电脑的隧道，请先确认并释放，再连接本机服务')
    else:
        choices = list(dict.fromkeys([preferred]+list(range(18765, 18797))))
        free = next((p for p in choices if port_available(p)), None)
        tunnel = (dict(status='available', value=free, note='原端口已占用，已建议空闲端口；未终止任何连接') if free else
                  dict(status='conflict', value=preferred, note='候选端口均已占用，请手动填写其他端口'))
    tunnel['registered_ports'] = sorted(registered)
    return dict(user=username, directory=directory, tunnel=tunnel)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None


def local_response(port, path):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request('http://127.0.0.1:%d%s' % (port,path), headers={'User-Agent':'LinX-Remote-Discovery','Accept':'application/json,text/html'})
    with opener.open(request, timeout=1.2) as response:
        return response.read(65536)


def docker_candidates(hidden=None):
    candidates = set()
    try:
        result = subprocess.run(['docker','ps','--format','{{.Names}}\t{{.Image}}\t{{.Ports}}'],
                                stdout=subprocess.PIPE,stderr=subprocess.PIPE,encoding='utf-8',errors='replace',timeout=4,**(hidden or {}))
        if result.returncode: return candidates, 'Docker 暂不可用，已继续检查本机常用端口'
        for line in result.stdout.splitlines()[:256]:
            parts=line.split('\t',2)
            if len(parts)==3 and 'sub2api' in (parts[0]+' '+parts[1]).lower():
                candidates.update(int(p) for p in re.findall(r':(\d+)->\d+/tcp',parts[2]))
        return candidates, ''
    except (OSError,subprocess.TimeoutExpired):
        return candidates, '未读取到 Docker 映射，已继续检查本机常用端口'


def inspect_local(preferred=8080, hidden=None):
    preferred=port_number(preferred)
    docker_ports, docker_note=docker_candidates(hidden)
    ports=list(dict.fromkeys([preferred]+sorted(docker_ports)[:16]+[8080,8081,8000,3000]))
    def probe(port):
        try:
            health=json.loads(local_response(port,'/health'))
            if not isinstance(health,dict) or health.get('status') not in ('ok','healthy'): return None
            evidence='Docker 容器与健康检查' if port in docker_ports else None
            if not evidence:
                page=local_response(port,'/').decode('utf-8',errors='replace')
                if re.search(r'\bsub2api\b',page,re.I): evidence='页面标识与健康检查'
            return dict(port=port,source=evidence) if evidence else None
        except (OSError,ValueError): return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        found=[row for row in pool.map(probe,ports) if row]
    found.sort(key=lambda row:row['port'])
    if any(row['port']==preferred for row in found):
        return dict(status='verified',value=preferred,candidates=found,note='已确认当前端口的 Sub2API 服务',docker_note=docker_note)
    if len(found)==1:
        return dict(status='verified',value=found[0]['port'],candidates=found,note='已自动识别本机 Sub2API 端口',docker_note=docker_note)
    if len(found)>1:
        return dict(status='choose',value=preferred,candidates=found,note='发现多个 Sub2API 实例，请选择本次要使用的端口',docker_note=docker_note)
    return dict(status='not_found',value=preferred,candidates=[],note='未识别到本机 Sub2API；请启动服务或手动填写端口。直连供应商可忽略',docker_note=docker_note)


def detect(settings, previous, core):
    draft=dict(settings)
    for key in ('port','remote_port','local_port'): draft[key]=port_number(draft[key])
    if draft['connection_source']=='cindy':draft=core.cindy_selection(draft,draft['cindy_host'] or draft['alias'])
    same=all(draft.get(k)==previous.get(k) for k in ('host','alias','user','port','identity'))
    owned=bool(same and core.tunnel_status(previous).get('fresh'))
    # Ignore an inherited previous-server path, but retain an explicitly edited custom path.
    preferred_home=draft['home'] if same or draft['home']!=previous.get('home') else ''
    payload=dict(home=preferred_home,remote_port=draft['remote_port'],owned_tunnel=owned and draft['remote_port']==previous.get('remote_port'))
    cmd,destination=core.ssh_base(draft)
    import base64,shlex
    source=Path(__file__).read_bytes()
    runner="import base64,json,sys;ns={'__name__':'linx_detection'};exec(compile(base64.b64decode(sys.stdin.readline()),'<linx-detection>','exec'),ns);print(json.dumps(ns['inspect_remote'](json.loads(sys.stdin.readline()))))"
    def remote():
        try:
            result=subprocess.run(cmd+[destination,'python3 -c '+shlex.quote(runner)],input=base64.b64encode(source).decode()+'\n'+json.dumps(payload)+'\n',
                                  stdout=subprocess.PIPE,stderr=subprocess.PIPE,encoding='utf-8',errors='replace',timeout=25,**core.HIDDEN)
        except subprocess.TimeoutExpired: raise ValueError('自动检测连接超时，原设置未修改；请确认服务器可通过 SSH 登录后重试') from None
        if result.returncode: raise ValueError('无法检测服务器，原设置未修改；请先打开 SSH 登录终端确认认证，并检查服务器 Python 3')
        try:
            info=json.loads(result.stdout)
            if not isinstance(info['directory']['candidates'],list) or not info['user']: raise ValueError()
            return info
        except (ValueError,KeyError,TypeError): raise ValueError('服务器检测结果无法读取，原设置未修改；请检查 SSH 登录脚本是否有额外输出') from None
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        remote_task=pool.submit(remote);local_task=pool.submit(inspect_local,draft['local_port'],core.HIDDEN)
        info=remote_task.result();local=local_task.result()
    draft.update(user=info['user'],home=info['directory']['value'],remote_port=info['tunnel']['value'],local_port=local['value'])
    if owned:
        # Changing a live worker's identity would orphan its state; only suggest the detected replacement.
        if draft['local_port']!=previous['local_port']:
            local=dict(local,status='restart_required',note='已发现其他本机端口；请先停止旧隧道，再重新检测并连接')
        draft['local_port']=previous['local_port'];draft['remote_port']=previous['remote_port']
        info['tunnel']=dict(info['tunnel'],status='managed',value=previous['remote_port'],note='当前后台隧道正在使用此端口；如需修改端口，请先停止旧隧道')
    return dict(settings=draft,directory=info['directory'],tunnel=info['tunnel'],local=local,
                note='检测完成；结果尚未保存。可核对后点击“使用并连接”')
