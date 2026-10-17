"""Read Cindy SSH sources without executing Match/ProxyCommand or reading keys."""
import fnmatch,glob,json,os,re,shlex,sys
from pathlib import Path

def cindy_data_dir():
    if sys.platform=='darwin':return Path.home()/'Library/Application Support/Cindy'
    if os.name=='nt':return Path(os.environ.get('APPDATA',str(Path.home()/'AppData/Roaming')))/'Cindy'
    return Path(os.environ.get('XDG_CONFIG_HOME',str(Path.home()/'.config')))/'Cindy'

def read_connections(ssh_dir=None,data_dir=None):
    ssh=Path(ssh_dir) if ssh_dir else Path.home()/'.ssh'
    data=Path(data_dir) if data_dir else cindy_data_dir()
    config=ssh/'config';managed=(ssh/'cindy.conf').resolve()
    entries=[];declarations={};warnings=[];visited=set();total=0
    def expand(value):
        value=re.sub(r'\x24\{([A-Za-z_][A-Za-z0-9_]*)\}',lambda m:os.environ.get(m[1],m[0]),value)
        if value.startswith('~/') or value.startswith('~\\'):return str(ssh.parent/value[2:])
        return value
    def walk(path,scope=None,conditional=False,depth=0):
        nonlocal total
        resolved=path.resolve()
        if resolved in visited:return scope,conditional
        if depth>12 or len(visited)>=100:raise ValueError('SSH Include 层级或文件数量过多')
        visited.add(resolved)
        try:content=path.read_bytes();total+=len(content)
        except FileNotFoundError:return scope,conditional
        if total>2*1024*1024:raise ValueError('SSH 配置内容超出读取上限')
        text=re.sub(r'\\\r?\n','',content.decode('utf-8-sig'))
        for line in text.splitlines():
            lex=shlex.shlex(line,posix=True);lex.whitespace_split=True;lex.commenters='#'
            try:words=list(lex)
            except ValueError:raise ValueError('SSH 配置引号错误')
            if not words:continue
            if '=' in words[0]:
                key,value=words[0].split('=',1);words=[key]+([value] if value else [])+words[1:]
            if len(words)>1 and words[1]=='=':words.pop(1)
            key=words[0].lower();values=words[1:]
            if key=='host':
                scope=values;conditional=False
                for alias in values:
                    if re.fullmatch(r'[A-Za-z0-9_.-]+',alias) and not alias.startswith('-'):declarations.setdefault(alias,set()).add(str(resolved))
                continue
            if key=='match':
                conditional=values!=['all'];scope=None
                if conditional:warnings.append('部分 Match 条件仅在连接时由 OpenSSH 解析，列表概览可能不完整。')
                continue
            if key=='include':
                if conditional:warnings.append('条件 Include 不在列表中展开；实际连接仍由 OpenSSH 处理。');continue
                for value in values:
                    expanded=expand(value)
                    if chr(36)+'{' in expanded:continue
                    pattern=Path(expanded)
                    if not pattern.is_absolute():pattern=ssh/pattern
                    for match in sorted(glob.glob(str(pattern))):scope,conditional=walk(Path(match),scope,conditional,depth+1)
                continue
            if not conditional and values and key in ('hostname','user','port','identityfile','identityagent','proxyjump','proxycommand','identitiesonly'):entries.append((scope,key,' '.join(values)))
        return scope,conditional
    try:
        walk(config)
        if managed.exists() and managed not in visited:warnings.append('Cindy 主机文件未被 SSH config 的 Include 引用，请先在 Cindy 中保存连接设置。')
        prefs={}
        try:
            p=data/'ssh-host-prefs.json'
            if p.stat().st_size<=1024*1024:prefs=json.loads(p.read_text(encoding='utf-8'))
        except (OSError,ValueError):pass
        hosts=[]
        def matches(patterns,alias):
            if patterns is None:return True
            positive=False
            for pattern in patterns:
                neg=pattern.startswith('!');pattern=pattern[1:] if neg else pattern
                if fnmatch.fnmatchcase(alias.lower(),pattern.lower()):
                    if neg:return False
                    positive=True
            return positive
        for alias,sources in declarations.items():
            fields={}
            for scope,key,value in entries:
                if matches(scope,alias):fields.setdefault(key,value)
            address=fields.get('hostname',alias).replace('%h',alias).replace('%%','%')
            try:port=int(fields.get('port',22))
            except ValueError:port=22
            pref=prefs.get(alias,{}) if isinstance(prefs,dict) else {};title=pref.get('displayName') if isinstance(pref,dict) else None
            hosts.append({'id':alias,'name':title if isinstance(title,str) and title.strip() else alias,'hostname':address,'port':port,'user':fields.get('user',''),'managed_by_cindy':str(managed) in sources,'auth':'SSH 配置 / Agent','has_identity':bool(fields.get('identityfile')),'jump':bool(fields.get('proxyjump') or fields.get('proxycommand'))})
        return {'hosts':sorted(hosts,key=lambda h:(not h['managed_by_cindy'],h['name'].lower())),'warnings':list(dict.fromkeys(warnings)),'source':'Cindy / OpenSSH','available':bool(hosts)}
    except (OSError,UnicodeError,ValueError):return {'hosts':[],'warnings':['无法读取 Cindy 使用的 SSH 配置，请在 Cindy 中检查配置后刷新；仍可手动添加连接。'],'source':'Cindy / OpenSSH','available':False}
