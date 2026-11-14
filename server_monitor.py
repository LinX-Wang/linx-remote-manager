"""Read-only server metrics collected over the manager's existing SSH connection."""
from __future__ import annotations
import csv
import json
import re
import shlex
import subprocess
import threading
import time
from typing import Any

DISK_PATH_SCRIPT = r'''
import json, os, pwd, subprocess
targets = [('user_home', pwd.getpwuid(os.geteuid()).pw_dir),
           ('codex_home', os.environ.get('LINX_MONITOR_HOME', ''))]
rows = []
for kind, path in targets:
    if not path or not os.path.exists(path):
        continue
    resolved = os.path.realpath(path)
    try:
        result = subprocess.run(['df', '-Pk', resolved], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, universal_newlines=True, timeout=3)
        fields = result.stdout.splitlines()[-1].split(None, 5)
        if result.returncode == 0 and len(fields) == 6 and fields[4].endswith('%'):
            rows.append(dict(kind=kind, path=resolved, filesystem=fields[0],
                             size_kb=int(fields[1]), used_kb=int(fields[2]),
                             available_kb=int(fields[3]), percent=int(fields[4][:-1]),
                             mount=fields[5]))
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        pass
print(json.dumps(rows))
'''

REMOTE_SCRIPT = r"""
printf '__GPU__\n'
command -v nvidia-smi >/dev/null 2>&1 && timeout 8 nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv,noheader,nounits 2>&1 || printf 'nvidia-smi unavailable\n'
printf '__PMON__\n'
LINX_PMON=$(command -v nvidia-smi >/dev/null 2>&1 && timeout 8 nvidia-smi pmon -c 1 -s um 2>/dev/null)
printf '%s\n' "$LINX_PMON"
printf '__MEM__\n'
free -m 2>&1
printf '__LOAD__\n'
cat /proc/loadavg 2>&1
printf '__CPU__\n'
getconf _NPROCESSORS_ONLN 2>/dev/null || nproc 2>/dev/null || grep -c '^processor' /proc/cpuinfo 2>/dev/null || printf '0\n'
printf '__DISK__\n'
df -Pk -x tmpfs -x devtmpfs 2>/dev/null
printf '__PATHS__\n'
LINX_MONITOR_HOME=__LINX_CODEX_HOME__ python3 -c __LINX_DISK_PATH_SCRIPT__ 2>/dev/null || :
printf '__PS__\n'
LINX_PS=$(
ps -eo user=,pid=,pcpu=,pmem=,comm=,args= --sort=-pcpu 2>&1 | head -n 300
ps -u "$(id -un)" -o user=,pid=,pcpu=,pmem=,comm=,args= --sort=-pcpu 2>&1
LINX_GPU_PIDS=$(printf '%s\n' "$LINX_PMON" | awk '$1 ~ /^[0-9]+$/ && $2 ~ /^[0-9]+$/ {print $2}' | sort -un | paste -sd, -)
if [ -n "$LINX_GPU_PIDS" ]; then
    ps -p "$LINX_GPU_PIDS" -o user=,pid=,pcpu=,pmem=,comm=,args= 2>/dev/null || :
fi
)
printf '%s\n' "$LINX_PS"
printf '__TIMING__\n'
printf '%s\n' "$LINX_PS" | python3 -c __LINX_TIMING_SCRIPT__ 2>/dev/null || :
"""

# One small /proc read per process; never attach, signal, or open a process FD.
TIMING_SCRIPT = r'''
import json, os, sys, time
clock = os.sysconf('SC_CLK_TCK')
with open('/proc/uptime') as stream:
    uptime = float(stream.read().split()[0])
now = time.time()
rows = {}
names = dict.fromkeys(line.split()[1] for line in sys.stdin if len(line.split()) > 1)
for name in list(names)[:2000]:
    if not name.isdigit():
        continue
    try:
        with open('/proc/' + name + '/stat') as stream:
            fields = stream.read().rsplit(')', 1)[1].split()
        ticks = int(fields[19])
        elapsed = max(0, uptime - ticks / clock)
        rows[name] = dict(started_at=int(now - elapsed), elapsed_seconds=int(elapsed),
                          start_ticks=str(ticks))
    except (OSError, ValueError, IndexError):
        pass
print(json.dumps(dict(sampled_at=int(now), processes=rows)))
'''

def _number(value: str) -> float | int | None:
    try:
        n = float(value.strip().replace("%", ""))
        return int(n) if n.is_integer() else n
    except (ValueError, TypeError):
        return None

def _sections(raw: str) -> dict[str, str]:
    names = ("GPU", "PMON", "MEM", "LOAD", "CPU", "DISK", "PATHS", "PS", "TIMING")
    found = {}
    for i, name in enumerate(names):
        start = raw.find("__" + name + "__")
        if start < 0:
            found[name] = ""
            continue
        start += len(name) + 4
        end = min((pos for other in names[i + 1:] if (pos := raw.find("__" + other + "__", start)) >= 0), default=len(raw))
        found[name] = raw[start:end].strip()
    return found

_SECRET_PATTERNS = (
    re.compile(r"(?i)((?<![A-Za-z0-9_])(?:--?(?:api[-_]?key|token|password|passwd|secret|authorization)|(?:API_KEY|ACCESS_TOKEN|PASSWORD|SECRET))\s*(?:=|\s)\s*)[^\s,;]+"),
    re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)([?&](?:key|api[-_]?key|token|access_token|password|secret)=)[^&#\s]+"),
)
def _redact_args(value: str) -> str:
    value = re.sub(r"(?i)(https?://)[^/@\s:]+:[^/@\s]+@", r"\1[已隐藏]@", value)
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub(r"\1[已隐藏]", value)
    return value[:180]

def _processes(raw: str) -> dict[str, dict[str, Any]]:
    result = {}
    for line in raw.splitlines():
        fields = line.strip().split(None, 5)
        if len(fields) < 5 or not fields[1].isdigit():
            continue
        user, pid, cpu, memory, command = fields[:5]
        args = _redact_args(fields[5] if len(fields) > 5 else command)
        result[pid] = {"user": user, "pid": int(pid), "cpu": _number(cpu) or 0, "memory": _number(memory) or 0, "command": command, "args": args}
    return result

def parse_snapshot(raw: str) -> dict[str, Any]:
    part = _sections(raw)
    gpus = []
    for row in csv.reader(part["GPU"].splitlines(), skipinitialspace=True):
        if len(row) < 6 or not row[0].strip().isdigit():
            continue
        gpus.append({"index": int(row[0]), "name": row[1].strip(), "utilization": _number(row[2]) or 0, "memory_used": _number(row[3]) or 0, "memory_total": _number(row[4]) or 0, "temperature": _number(row[5]), "processes": []})
    processes = _processes(part["PS"])
    try:
        timing = json.loads(part['TIMING'] or '{}')
        timing = timing.get('processes', {})
        if not isinstance(timing, dict):
            timing = {}
    except (ValueError, TypeError, AttributeError):
        timing = {}
    for pid, proc in processes.items():
        row = timing.get(pid, {})
        if (isinstance(row, dict) and type(row.get('started_at')) is int
                and row['started_at'] > 0 and type(row.get('elapsed_seconds')) is int
                and row['elapsed_seconds'] >= 0 and str(row.get('start_ticks', '')).isdigit()):
            proc.update({key: row[key] for key in ('started_at', 'elapsed_seconds', 'start_ticks')})
    for line in part["PMON"].splitlines():
        fields = line.split()
        if len(fields) < 3 or not fields[0].isdigit() or not fields[1].isdigit():
            continue
        gpu_index, pid = int(fields[0]), fields[1]
        proc = dict(processes.get(pid, {"pid": int(pid), "user": "?", "command": fields[-1], "args": fields[-1]}))
        proc["gpu_memory"] = _number(fields[9]) if len(fields) > 9 else None
        proc["gpu"] = gpu_index
        for gpu in gpus:
            if gpu["index"] == gpu_index:
                gpu["processes"].append(proc)
                break
    memory = {}
    for line in part["MEM"].splitlines():
        fields = line.split()
        if fields and fields[0].lower().startswith("mem:") and len(fields) >= 7:
            memory = {key: _number(value) or 0 for key, value in zip(("total", "used", "free", "shared", "cache", "available"), fields[1:7])}
    load = [_number(x) for x in part["LOAD"].split()[:3]]
    cpu_cores = _number(part["CPU"].splitlines()[0]) if part["CPU"].splitlines() else None
    disks = []
    for line in part["DISK"].splitlines():
        fields = line.split(None, 5)
        if len(fields) >= 6 and fields[4].endswith("%"):
            disks.append({"filesystem": fields[0], "size_kb": _number(fields[1]) or 0, "used_kb": _number(fields[2]) or 0, "available_kb": _number(fields[3]) or 0, "percent": _number(fields[4]) or 0, "mount": fields[5]})
    try:
        paths = json.loads(part['PATHS'] or '[]')
    except (ValueError, TypeError):
        paths = []
    for target in paths if isinstance(paths, list) else []:
        if not isinstance(target, dict) or not all(k in target for k in ('mount', 'filesystem', 'path', 'kind')):
            continue
        disk = next((d for d in disks if d['mount'] == target['mount'] and d['filesystem'] == target['filesystem']), None)
        if disk is None:
            if not all(k in target for k in ('size_kb', 'used_kb', 'available_kb', 'percent')):
                continue
            disk = {k: target[k] for k in ('filesystem', 'mount', 'size_kb', 'used_kb', 'available_kb', 'percent')}
            disks.append(disk)
        disk['focus'] = True
        disk.setdefault('related_paths', []).append({'kind': target['kind'], 'path': target['path']})
    top = sorted(processes.values(), key=lambda p: float(p.get("cpu", 0)), reverse=True)[:300]
    return {"gpus": gpus, "memory": memory, "load": load, "cpu_cores": cpu_cores, "disks": disks, "processes": top, "collected_at": int(time.time())}

def prioritize_processes(rows, user, watched, limit=100):
    identities={(str(row.get('pid')),str(row.get('start_ticks',''))):row for row in rows}
    pinned=[identities[(pid,ticks)] for pid,ticks in watched.items() if (pid,ticks) in identities]
    pinned_ids={str(row.get('pid')) for row in pinned}
    remaining=[row for row in rows if str(row.get('pid')) not in pinned_ids]
    mine=[row for row in remaining if row.get('user')==user]
    others=[row for row in remaining if row.get('user')!=user]
    capacity=max(0,limit-len(pinned))
    return pinned+mine[:capacity]+others[:max(0,capacity-len(mine[:capacity]))]


class Monitor:
    def __init__(self):
        self.lock = threading.Lock()
        self.cache_lock = threading.Lock()
        self.cache: dict[str, Any] | None = None
        self.cache_key = None
        self.cache_time = 0.0

    @staticmethod
    def _key(settings):
        return tuple(settings.get(k) for k in ('connection_source','cindy_host','host','alias','user','port','identity','home','auth_method'))

    def cached(self, settings):
        """Return a truthful last sample without waiting for network collection."""
        with self.cache_lock:
            if self.cache is None or self.cache_key != self._key(settings):
                return None
            data = self.cache
            age = max(0, time.monotonic() - self.cache_time)
        result = self._prioritized(data, settings)
        result.update(snapshot_age_seconds=round(age, 2), refreshing=self.lock.locked())
        if age > 15:
            result['stale'] = True
        return result

    def _prioritized(self,data,settings):
        from monitor_features import features
        result=dict(data)
        result['processes']=prioritize_processes(data.get('processes',[]),settings.get('user',''),features.watched_map(settings))
        return result

    def collect(self, settings: dict[str, Any], force: bool = False) -> dict[str, Any]:
        import manager
        requested = time.monotonic()
        key = self._key(settings)
        cached = self.cached(settings)
        if cached is not None and not force and cached['snapshot_age_seconds'] < 4:
            return cached
        with self.lock:
            # A concurrent caller reuses the sample completed while it waited.
            with self.cache_lock:
                reuse = self.cache is not None and self.cache_key == key and (self.cache_time >= requested or not force and time.monotonic() - self.cache_time < 4)
            if reuse:
                return self.cached(settings)
            ssh, destination = manager.ssh_base(settings)
            try:
                # Pass bytes so Windows does not translate LF to CRLF before SSH
                # forwards the script to POSIX sh (CR characters break shell syntax).
                script = REMOTE_SCRIPT.replace('__LINX_CODEX_HOME__', shlex.quote(settings.get('home', ''))).replace('__LINX_DISK_PATH_SCRIPT__', shlex.quote(DISK_PATH_SCRIPT)).replace('__LINX_TIMING_SCRIPT__', shlex.quote(TIMING_SCRIPT))
                result = manager.ssh_run(settings, ssh + [destination, "sh -s"], input=script.encode("utf-8"), capture_output=True, timeout=28, **manager.HIDDEN)
                if result.returncode:
                    error = result.stderr or result.stdout or "SSH 连接失败"
                    if isinstance(error, bytes):
                        error = error.decode("utf-8", errors="replace")
                    raise ValueError(error.strip()[-1000:])
                data = parse_snapshot(result.stdout.decode("utf-8", errors="replace"))
                user = settings.get("user", "")
                rows = data.get("processes", [])
                data["processes"] = rows
                data.update({"connection": {"profile": destination, "host": settings.get("host") or destination, "user": settings.get("user", ""), "port": settings.get("port", 22)}, "current_user": settings.get("user", ""), "stale": False, "error": ""})
                with self.cache_lock:
                    self.cache, self.cache_key, self.cache_time = data, key, time.monotonic()
                return self._prioritized(data,settings)
            except Exception as exc:
                if self.cache is not None and self.cache_key == key:
                    stale = self._prioritized(self.cache,settings); stale.update(stale=True, error=str(exc)[:1000]); return stale
                return {"gpus": [], "memory": {}, "load": [], "cpu_cores": None, "disks": [], "processes": [], "collected_at": int(time.time()), "connection": {"profile": destination, "host": settings.get("host") or destination, "user": settings.get("user", ""), "port": settings.get("port", 22)}, "current_user": settings.get("user", ""), "stale": False, "error": str(exc)[:1000]}

monitor = Monitor()
