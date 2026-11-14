"""SQL dispatch guard for this lab's nonlocking default, not a general SQL validator.

Fixture DDL/DML and ordinary business writes remain allowed. Pending-DDL/ANALYZE
contention injections are removed from callers; this lexical guard is an additional
fail-closed barrier for explicit locking and count-to-DML forms.
"""
import re


class ExcludedLockProbe(ValueError):
    pass


def assert_nonlocking_sql(sql):
    # Keep quoted literals separate so monitoring SQL may search for forbidden text.
    # MySQL executable comments are checked as code, including version comments.
    source = sql
    # Match literals/quoted identifiers before comments, including '#' inside names.
    lexical = r"'(?:''|\\.|[^'\\])*'|\"(?:\"\"|\\.|[^\"\\])*\"|`(?:``|[^`])*`|/\*.*?\*/|--[^\n]*|\#[^\n]*"
    def strip(match):
        token = match[0]
        if token.startswith(("'", '\"')):
            return "''"
        if token.startswith('`'):
            return token
        if token.startswith(('/*!', '/*+')):
            return re.sub(r'^/\*[!+]\d*|\*/$', ' ', token)
        return ' '
    tokens = re.sub(lexical, strip, source, flags=re.S)
    code = ' '.join(tokens.upper().split())
    if (re.search(r'\bFOR\s+(?:SHARE|UPDATE)\b|\bLOCK\s+IN\s+SHARE\s+MODE\b', code)
            or re.search(r'\b(?:UNLOCK|LOCK)\s+TABLES\b|\bFLUSH\b[^;]*\bTABLES\b', code)):
        raise ExcludedLockProbe('explicit row/table/global-lock probe excluded by user scope')
    # All SERIALIZABLE setup is excluded rather than attempting to infer later trx state.
    if code.startswith('SET ') and re.search(r'\bSERIALIZABLE\b', source, re.I):
        raise ExcludedLockProbe('SERIALIZABLE probe excluded by user scope')
    if re.search(r'\bCOUNT\s*\(', code):
        if (re.match(r'(?:INSERT|REPLACE|UPDATE|DELETE|SET|DO)\b', code)
                or re.match(r'CREATE\s+(?:TEMPORARY\s+)?TABLE\b', code)
                or ':=' in code
                or (code.startswith('WITH ') and re.search(r'\b(?:INSERT|REPLACE|UPDATE|DELETE)\b', code))):
            raise ExcludedLockProbe('count-to-DML/CTAS/assignment probe excluded; receive count on client')
    if code.startswith('PREPARE '):
        match = re.search(r"\bFROM\s+'((?:''|\\.|[^'\\])*)'\s*;?\s*$", source, re.I | re.S)
        if not match:
            raise ExcludedLockProbe('dynamic PREPARE cannot be checked by nonlocking lab guard')
        assert_nonlocking_sql(match[1].replace("''", "'").replace("\\'", "'"))
