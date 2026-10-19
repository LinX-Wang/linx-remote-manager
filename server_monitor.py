"""Read-only server metrics collected over the manager's existing SSH connection."""
from __future__ import annotations
import csv
import re
import shlex
import subprocess
import threading
import time
from typing import Any

REMOTE_SCRIPT = r"""
printf '__GPU__\n'
command -v nvidia-smi >/dev/null 2>&1 && timeout 8 nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv,noheader,nounits 2>&1 || printf 'nvidia-smi unavailable\n'
printf '__PMON__\n'
command -v nvidia-smi >/dev/null 2>&1 && timeout 8 nvidia-smi pmon -c 1 -s um 2>&1 || :
printf '__MEM__\n'
free -m 2>&1
printf '__LOAD__\n'
cat /proc/loadavg 2>&1
printf '__CPU__\n'
getconf _NPROCESSORS_ONLN 2>/dev/null || nproc 2>/dev/null || grep -c '^processor' /proc/cpuinfo 2>/dev/null || printf '0\n'
printf '__DISK__\n'
df -Pk -x tmpfs -x devtmpfs 2>/dev/null
printf '__PS__\n'
ps -eo user=,pid=,pcpu=,pmem=,comm=,args= --sort=-pcpu 2>&1 | head -n 300
ps -u "$(id -un)" -o user=,pid=,pcpu=,pmem=,comm=,args= --sort=-pcpu 2>&1
"""

def _number(value: str) -> float | int | None:
    try:
        n = float(value.strip().replace("%", ""))
        return int(n) if n.is_integer() else n
    except (ValueError, TypeError):
        return None

def _sections(raw: str) -> dict[str, str]:
    names = ("GPU", "PMON", "MEM", "LOAD", "CPU", "DISK", "PS")
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
    top = sorted(processes.values(), key=lambda p: float(p.get("cpu", 0)), reverse=True)[:300]
    return {"gpus": gpus, "memory": memory, "load": load, "cpu_cores": cpu_cores, "disks": disks, "processes": top, "collected_at": int(time.time())}

class Monitor:
    def __init__(self):
        self.lock = threading.Lock()
        self.cache: dict[str, Any] | None = None
        self.cache_key = None
        self.cache_time = 0.0

    def collect(self, settings: dict[str, Any], force: bool = False) -> dict[str, Any]:
        import manager
        ssh, destination = manager.ssh_base(settings)
        identity = settings.get("identity", "") if settings.get("host") else settings.get("alias", "")
        key = (destination, settings.get("user", ""), settings.get("port"), identity)
        with self.lock:
            if self.cache is not None and self.cache_key == key and not force and time.monotonic() - self.cache_time < 4:
                return dict(self.cache)
            try:
                # Pass bytes so Windows does not translate LF to CRLF before SSH
                # forwards the script to POSIX sh (CR characters break shell syntax).
                result = manager.ssh_run(settings, ssh + [destination, "sh -s"], input=REMOTE_SCRIPT.encode("utf-8"), capture_output=True, timeout=28, **manager.HIDDEN)
                if result.returncode:
                    error = result.stderr or result.stdout or "SSH 连接失败"
                    if isinstance(error, bytes):
                        error = error.decode("utf-8", errors="replace")
                    raise ValueError(error.strip()[-1000:])
                data = parse_snapshot(result.stdout.decode("utf-8", errors="replace"))
                user = settings.get("user", "")
                rows = data.get("processes", [])
                mine = [row for row in rows if row.get("user") == user]
                others = [row for row in rows if row.get("user") != user]
                data["processes"] = mine[:100] + others[:max(0, 100 - len(mine[:100]))]
                data.update({"connection": {"profile": destination, "host": settings.get("host") or destination, "user": settings.get("user", ""), "port": settings.get("port", 22)}, "current_user": settings.get("user", ""), "stale": False, "error": ""})
                self.cache, self.cache_key, self.cache_time = data, key, time.monotonic()
                return dict(data)
            except Exception as exc:
                if self.cache is not None and self.cache_key == key:
                    stale = dict(self.cache); stale.update(stale=True, error=str(exc)[:1000]); return stale
                return {"gpus": [], "memory": {}, "load": [], "cpu_cores": None, "disks": [], "processes": [], "collected_at": int(time.time()), "connection": {"profile": destination, "host": settings.get("host") or destination, "user": settings.get("user", ""), "port": settings.get("port", 22)}, "current_user": settings.get("user", ""), "stale": False, "error": str(exc)[:1000]}

monitor = Monitor()
