#!/usr/bin/env python3
"""Exercise the actual timer block with early query completion and a reassigned caller connection."""
import ast, json, types
from pathlib import Path
source=Path(__file__).parent/'requested/12-EXP-6.py'
tree=ast.parse(source.read_text())
section=next(n for n in tree.body if isinstance(n,ast.With) and isinstance(n.items[0].context_expr,ast.Call) and n.items[0].context_expr.args and isinstance(n.items[0].context_expr.args[0],ast.Constant) and n.items[0].context_expr.args[0].value=='session_limit_and_lost_hint')
first=next(i for i,n in enumerate(section.body) if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name) and n.targets[0].id=='target_id')
last=next(i for i,n in enumerate(section.body) if isinstance(n,ast.Assign) and isinstance(n.value,ast.Name) and n.value.id=='kill_record')
block=ast.Module(body=section.body[first:last+1],type_ignores=[])
class Connection:
 def __init__(self,ident):self.ident=ident;self.closed=False
 def thread_id(self):return self.ident
 def close(self):self.closed=True
class Timer:
 def __init__(self,seconds,callback):self.callback=callback;self.cancelled=False;self.joined=False
 def start(self):pass
 def cancel(self):self.cancelled=True
 def join(self):self.joined=True
kills=[]
def execute(c,sql):
 if sql.startswith('KILL'):kills.append(sql)
 return {'ok':True}
original=Connection(11)
ns={'s':original,'d':{},'long_sql':'COUNT(*) FROM fixture','threading':types.SimpleNamespace(Timer=Timer),'connect':lambda **kw:Connection(99),'sql_try':execute}
exec(compile(block,str(source),'exec'),ns)
assert ns['timer'].cancelled and ns['timer'].joined and original.closed and not kills
ns['s']=Connection(22);ns['target_id']=22
ns['timer'].callback()  # even a callback already queued at cancellation keeps the captured target
assert kills==['KILL QUERY 11']
print(json.dumps({'early_completion_cancels_and_joins':True,'reassigned_connection_not_killed':True,'kill_queries':kills}))
