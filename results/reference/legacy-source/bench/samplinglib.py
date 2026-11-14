"""Bounded integer-key cell sampling for a consistent snapshot in the compose lab.

Each uniform draw (with replacement) counts one cell of at most width integer primary keys.
Hoeffding bounds use that hard upper bound, not an observed sample variance. A union bound
across predeclared looks preserves the family confidence when stopping early.
Not a general row sampler: requires one integer unique primary key and a consistent snapshot.
"""
from decimal import Decimal, localcontext, ROUND_FLOOR, ROUND_CEILING
import random, time
import pymysql
from lablib import query


def bounds(total, n, cells, width, domain, alpha):
    with localcontext() as ctx:
        ctx.prec=70
        estimate=Decimal(cells)*Decimal(total)/Decimal(n)
        margin=Decimal(cells*width)*((Decimal(2)/alpha).ln()/Decimal(2*n)).sqrt()
        lo=max(0,int((estimate-margin).to_integral_value(rounding=ROUND_FLOOR)))
        hi=min(domain,int((estimate+margin).to_integral_value(rounding=ROUND_CEILING)))
        # Relative error is measured against the unknown truth. Bound it using the lower confidence endpoint.
        relative=None if lo<=0 else float(max(estimate-lo,hi-estimate)/Decimal(lo))
        return {'estimate':str(estimate),'lower':lo,'upper':hi,'relative_error_bound':relative,'look_alpha':str(alpha)}


def sample(c, table, seed, width=200, looks=(512,2048,8192), target=.03, alpha=.05, max_seconds=90):
    if not table.replace('_','').replace('.','').isalnum():raise ValueError('lab identifier only')
    if width<=0 or not 0<alpha<1 or not 0<target<1 or tuple(sorted(set(looks)))!=tuple(looks) or not looks or looks[0]<=0:
        raise ValueError('invalid sampling parameters')
    db,name=table.split('.')
    key=query(c,"SELECT s.COLUMN_NAME,c.DATA_TYPE FROM information_schema.STATISTICS s JOIN information_schema.COLUMNS c ON c.TABLE_SCHEMA=s.TABLE_SCHEMA AND c.TABLE_NAME=s.TABLE_NAME AND c.COLUMN_NAME=s.COLUMN_NAME WHERE s.TABLE_SCHEMA=%s AND s.TABLE_NAME=%s AND s.INDEX_NAME='PRIMARY' ORDER BY s.SEQ_IN_INDEX",(db,name))
    engine=query(c,'SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s',(db,name))
    if len(key)!=1 or key[0][0]!='id' or key[0][1] not in ('tinyint','smallint','mediumint','int','bigint') or engine!= (('InnoDB',),):
        return {'status':'inapplicable','seconds':0,'reason':'requires InnoDB with a single integer primary key named id in this lab implementation'}
    start=time.monotonic()
    # A new dedicated connection is supplied by the caller. Never inherit a service transaction.
    query(c,'SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ')
    query(c,'SET SESSION max_execution_time=100')
    query(c,'SET SESSION lock_wait_timeout=2')
    query(c,'START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY')
    out={'seed':seed,'width':width,'looks':list(looks),'target_relative_error':target,'family_alpha':alpha,
         'method':'uniform integer-key cells with replacement; Hoeffding plus union bound over declared looks','checkpoints':[]}
    try:
        lo,hi=query(c,f'SELECT MIN(id),MAX(id) FROM {table}')[0]
        if lo is None:return {**out,'status':'exact_empty','estimate':'0','lower':0,'upper':0,'seconds':round(time.monotonic()-start,4)}
        lo,hi=int(lo),int(hi);domain=hi-lo+1;cells=(domain+width-1)//width
        out.update(min_id=lo,max_id=hi,domain=domain,cells=cells)
        rng=random.Random(seed);total=0
        for n in range(1,max(looks)+1):
            if time.monotonic()-start>max_seconds:
                out.update(status='budget_exhausted',draws=n-1);break
            left=lo+rng.randrange(cells)*width
            cell_count=query(c,f'SELECT COUNT(*) FROM {table} FORCE INDEX(PRIMARY) WHERE id>=%s AND id<=%s',(left,min(left+width-1,hi)))[0][0]
            if not 0<=cell_count<=width:raise RuntimeError('cell bound violated')
            total+=cell_count
            if n in looks:
                k=looks.index(n)+1
                # sum 1/(k*(k+1)) <= 1: all reported intervals jointly cover with probability >= 1-alpha.
                look_alpha=Decimal(str(alpha))/Decimal(k*(k+1))
                band=bounds(total,n,cells,width,domain,look_alpha)
                band.update(draws=n,sampled_rows_with_repeats=total)
                out['checkpoints'].append(band);out.update(band)
                if band['relative_error_bound'] is not None and band['relative_error_bound']<=target:
                    out['status']='target_reached';break
        else:out['status']='precision_not_reached'
    except pymysql.MySQLError as e:
        out.update(status='query_failed',errno=e.args[0])
    finally:
        query(c,'ROLLBACK')
    out['seconds']=round(time.monotonic()-start,4)
    return out
