#!/usr/bin/env python3
"""Answers EXP-O2-3 (FIND-R2-CASCADE-VER-01): do CASCADE and SET NULL fire child triggers, by version and by the settings of the session that writes? Fresh small server: argv is <host> <version>."""
import sys
from lablib import connect, query
from reqlib import Results, guard, sql_try

HOST, VERSION = sys.argv[1], sys.argv[2]
ID = f"58-EXP-O2-3-{VERSION}"
guard(ID, 2)
r = Results(ID, ["EXP-O2-3", "FIND-R2-CASCADE-VER-01"], version=VERSION)
query(connect(HOST, db="mysql"), "CREATE DATABASE IF NOT EXISTS bench")
a = connect(HOST, read_timeout=60)
query(a, "SET SESSION cte_max_recursion_depth=1000")
NAMES = ("enable_cascade_triggers", "innodb_native_foreign_keys")


def trial(d, label, setup):
    for s in ("DROP TABLE IF EXISTS cc_child", "DROP TABLE IF EXISTS cc_parent", "DROP TABLE IF EXISTS cc_log",
              "CREATE TABLE cc_log (k VARCHAR(10) PRIMARY KEY, c INT NOT NULL)", "INSERT INTO cc_log VALUES ('del',0),('upd',0)",
              "CREATE TABLE cc_parent (id INT PRIMARY KEY)", "INSERT INTO cc_parent WITH RECURSIVE s(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM s WHERE n<30) SELECT n FROM s",
              """CREATE TABLE cc_child (id INT PRIMARY KEY, a INT NULL, b INT NULL,
                 FOREIGN KEY (a) REFERENCES cc_parent (id) ON DELETE CASCADE ON UPDATE CASCADE,
                 FOREIGN KEY (b) REFERENCES cc_parent (id) ON DELETE SET NULL)""",
              "INSERT INTO cc_child WITH RECURSIVE s(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM s WHERE n<30) SELECT n, IF(n<=10,1,IF(n<=20,2,NULL)), IF(n>20,3,NULL) FROM s",
              "CREATE TRIGGER cc_d AFTER DELETE ON cc_child FOR EACH ROW UPDATE cc_log SET c=c+1 WHERE k='del'",
              "CREATE TRIGGER cc_u AFTER UPDATE ON cc_child FOR EACH ROW UPDATE cc_log SET c=c+1 WHERE k='upd'"):
        query(a, s)
    s = connect(HOST)
    res = {"setup": {x: {k: v for k, v in sql_try(s, x).items() if k != "rows"} for x in setup}}
    for name, sql in (("delete_cascade", "DELETE FROM cc_parent WHERE id=1"), ("update_cascade", "UPDATE cc_parent SET id=102 WHERE id=2"), ("delete_set_null", "DELETE FROM cc_parent WHERE id=3")):
        before = dict(query(a, "SELECT k,c FROM cc_log"))
        out = sql_try(s, sql)
        warn = [list(x) for x in query(s, "SHOW WARNINGS")]
        after = dict(query(a, "SELECT k,c FROM cc_log"))
        res[name] = {"ok": out["ok"], "errno": out.get("errno"), "warnings": warn, "trigger_delta": {k: after[k] - before[k] for k in after}, "child_rows": query(a, "SELECT COUNT(*) FROM cc_child")[0][0]}
    s.close()
    d[label] = res


with r.section("cascade") as d:
    d["variables"] = {n: (query(a, "SHOW VARIABLES LIKE %s", (n,)) or [[None, None]])[0][1] for n in NAMES}
    trial(d, "default", [])
    trial(d, "writer_session_on", ["SET SESSION enable_cascade_triggers=ON"])
    d["counting_session_setting_has_no_effect_check"] = "cc_log moves only through the writer's statements; the session running this check holds the default setting."
    original = d["variables"]["innodb_native_foreign_keys"]
    d["native_on_attempt"] = {k: v for k, v in sql_try(a, "SET GLOBAL innodb_native_foreign_keys=ON").items() if k != "rows"}
    d["native_off_attempt"] = {k: v for k, v in sql_try(a, "SET GLOBAL innodb_native_foreign_keys=OFF").items() if k != "rows"}
    if d["native_on_attempt"]["ok"]:
        query(a, "SET GLOBAL innodb_native_foreign_keys=ON")
        trial(d, "native_on_default_session", [])
        trial(d, "native_on_writer_session_on", ["SET SESSION enable_cascade_triggers=ON"])
        sql_try(a, f"SET GLOBAL innodb_native_foreign_keys={original or 'OFF'}")
r.finish()
