"""Bounded in-memory GPU history and read-only, identity-aware process watches."""
from collections import deque
import hashlib
import json
import threading
import time

def connection_key(settings):
    # Do not expose connection details as keys or put credentials in history.
    keys=('connection_source','cindy_host','host','alias','user','port','identity','home')
    return hashlib.sha256(json.dumps({k:settings.get(k) for k in keys},sort_keys=True).encode()).hexdigest()

class Features:
    def __init__(self):
        self.lock=threading.RLock();self.histories={};self.watches={};self.events=deque(maxlen=50);self.sequence=0
    def record(self,settings,data):
        if data.get('error') or data.get('stale') or not data.get('collected_at'):return
        key=connection_key(settings);now=int(data['collected_at'])
        with self.lock:
            if key not in self.histories:
                if len(self.histories)>=8:del self.histories[next(iter(self.histories))]
                self.histories[key]=deque(maxlen=360)
            history=self.histories[key]
            if history and now-history[-1]['at']<4:return
            history.append(dict(at=now,gpus=[{k:g.get(k) for k in ('index','utilization','memory_used','memory_total','temperature')} for g in data.get('gpus',[])]))
            while history and now-history[0]['at']>1800:history.popleft()
    def history(self,settings):
        with self.lock:return dict(samples=list(self.histories.get(connection_key(settings),[])),interval=5,retention_minutes=30)
    def add(self,settings,pid,ticks,user=''):
        from remote_tools import request
        pid=int(pid)
        if pid<=0 or not str(ticks).isdigit():raise ValueError('进程身份无效，请刷新监控')
        probe=request(settings,'identities',pids=[pid]);row=probe['processes'].get(str(pid),{})
        if row.get('status')!='running' or row.get('ticks')!=ticks:raise ValueError('进程已结束或 PID 已变化，请刷新监控')
        key=connection_key(settings)+':'+str(pid)+':'+ticks
        with self.lock:
            if len(self.watches)>=32 and key not in self.watches:raise ValueError('最多同时关注 32 个进程')
            self.watches[key]=dict(id=key,connection=connection_key(settings),pid=pid,ticks=ticks,boot=probe['boot_id'],user=str(user)[:64],state='running',misses=0,created_at=int(time.time()))
        return self.status(settings)
    def remove(self,key):
        with self.lock:
            self.watches.pop(key,None)
            self.events=deque((event for event in self.events if event.get('watch_id')!=key),maxlen=50)
    def status(self,settings):
        key=connection_key(settings)
        with self.lock:
            return dict(watches=[{k:v for k,v in w.items() if k not in ('connection','boot','misses')} for w in self.watches.values() if w['connection']==key],events=[dict(e) for e in self.events if e['connection']==key],enabled=bool(self.watches))
    def tick(self,settings):
        from remote_tools import request
        key=connection_key(settings)
        with self.lock:
            active=[dict(w) for w in self.watches.values() if w['connection']==key and w['state'] not in ('exited','restarted')]
        if not active:return
        try:probe=request(settings,'identities',pids=[w['pid'] for w in active])
        except Exception:
            with self.lock:
                for w in active:
                    current=self.watches.get(w['id'])
                    if current:current.update(state='disconnected',misses=0)
            return
        with self.lock:
            for old in active:
                w=self.watches.get(old['id'])
                if not w:continue
                row=probe.get('processes',{}).get(str(w['pid']),{})
                state=row.get('status','unknown')
                if probe.get('boot_id')!=w['boot']:
                    w['state']='restarted';message='服务器已重启，PID '+str(w['pid'])+' 的原进程身份已失效'
                elif state=='unknown':w.update(state='unknown',misses=0);continue
                elif state=='running' and row.get('ticks')==w['ticks']:w.update(state='running',misses=0);continue
                else:
                    w['misses']+=1;w['state']='checking'
                    if w['misses']<2:continue
                    w['state']='exited';message='关注的进程 PID '+str(w['pid'])+' 已退出；任务是否成功需查看其结果'
                self.sequence+=1
                self.events.append(dict(id=self.sequence,watch_id=w['id'],connection=key,pid=w['pid'],message=message,at=int(time.time())))
    def loop(self,stop):
        import manager
        import server_monitor
        next_watch=next_cleanup=0.0
        while not stop.is_set():
            started=time.monotonic()
            try:
                settings=manager.read_settings()
                if settings.get('host') or settings.get('alias'):
                    # The Python service owns sampling, independent of browser
                    # visibility, active page, timers or process subscriptions.
                    self.record(settings,server_monitor.monitor.collect(settings))
                if started>=next_watch:
                    next_watch=started+10
                    self.tick(settings)
            except Exception:pass
            if started>=next_cleanup:
                next_cleanup=started+60
                try:
                    from remote_tools import transfers
                    transfers.cleanup()
                except Exception:pass
            # Account for SSH duration; never overlap workers or catch up with
            # a burst of requests after a slow/disconnected server.
            stop.wait(max(0.1,5-(time.monotonic()-started)))

features=Features()
