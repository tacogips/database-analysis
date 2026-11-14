#!/usr/bin/env python3
"""Deterministic regression: one service query spans six scheduled arrivals.
No DB, real worker thread, timer, sleep or network is used.
"""
import hashlib, importlib, json, sys, types, threading
from pathlib import Path

# The host can run this pure scheduler test without installing the lab's database client.
try:
    import pymysql
except ImportError:
    client = types.ModuleType('pymysql')
    cursors = types.ModuleType('pymysql.cursors')
    cursors.Cursor = cursors.DictCursor = object
    client.cursors = cursors
    sys.modules['pymysql'] = client
    sys.modules['pymysql.cursors'] = cursors
import lablib

clock = [0.0]
stop = threading.Event()
class Worker:
    def __init__(self, target, args=(), **kwargs): self.target, self.args = target, args
    def start(self): self.target(*self.args)
    def join(self, *args, **kwargs): pass
    def is_alive(self): return False
class Connection:
    def thread_id(self): return 123
    def close(self): pass
class Pipe:
    def send(self, payload):
        self.samples = payload["samples"] if isinstance(payload,dict) else payload
        self.accounting = payload.get("accounting") if isinstance(payload,dict) else None
pipe = Pipe()
def query(conn, sql, args=None):
    if not sql.startswith('SET '):
        clock[0] = .055
        stop.set()
    return []
lablib.pin = lambda cpu: None
lablib.connect = lambda *args, **kwargs: Connection()
lablib.query = query
lablib.time = types.SimpleNamespace(monotonic=lambda: clock[0], time=lambda: 1000+clock[0], sleep=lambda t: None)
lablib.random = types.SimpleNamespace(seed=lambda s: None, random=lambda: 0.0, randint=lambda a,b: a)
lablib.threading = types.SimpleNamespace(Thread=Worker, Lock=threading.Lock)
cfg = dict(host='primary',readers=1,writers=0,rate=100,head=1,max_id=1,read_sql='SELECT',write_sql='UPDATE')
lablib._workload_main(cfg, stop, pipe)
result = {'test':'six arrivals while first query runs', 'scheduled_arrivals':6,
          'observed_samples':len(pipe.samples),'samples':pipe.samples,'accounting':pipe.accounting,
          'source_sha256':hashlib.sha256(Path(lablib.__file__).read_bytes()).hexdigest(),
          'no_database_or_real_threads':True}
print(json.dumps(result,ensure_ascii=False,indent=2))
raise SystemExit(0 if len(pipe.samples)==6 and pipe.accounting and pipe.accounting["consistent"] else 1)
