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
    from sampling_bounds import confidence_band
    if not table.replace('_','').replace('.','').isalnum():
        raise ValueError('lab identifier only')
    if width<=0 or not 0<alpha<1 or not 0<target<1 or tuple(sorted(set(looks)))!=tuple(looks) or not looks or looks[0]<=0:
        raise ValueError('invalid sampling parameters')
    db,name=table.split('.')
    start=time.monotonic()
    out={'seed':seed,'width':width,'looks':list(looks),'target_relative_error':target,'family_alpha':alpha,
         'method':'uniform integer-key cells with replacement; simultaneous Hoeffding/empirical Bernstein bands',
         'budget_scope':'first SQL through rollback; connection established separately by caller',
         'budget_mode':'cooperative checks at query boundaries, not a hard server shutdown deadline',
         'max_seconds':max_seconds,'checkpoints':[],'attempted_draws':0,'completed_draws':0,
         'last_valid_checkpoint_draws':0,'accuracyStatus':'not_assessed','budgetStatus':'within_budget',
         'cleanupStatus':'not_open','status':'not_started'}
    opened=False
    expired=lambda: time.monotonic()-start > max_seconds
    try:
        query(c,'SET SESSION max_execution_time=100')
        query(c,'SET SESSION lock_wait_timeout=2')
        query(c,'SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        key=query(c,"SELECT s.COLUMN_NAME,c.DATA_TYPE FROM information_schema.STATISTICS s JOIN information_schema.COLUMNS c ON c.TABLE_SCHEMA=s.TABLE_SCHEMA AND c.TABLE_NAME=s.TABLE_NAME AND c.COLUMN_NAME=s.COLUMN_NAME WHERE s.TABLE_SCHEMA=%s AND s.TABLE_NAME=%s AND s.INDEX_NAME='PRIMARY' ORDER BY s.SEQ_IN_INDEX",(db,name))
        engine=query(c,'SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s',(db,name))
        if len(key)!=1 or key[0][0]!='id' or key[0][1] not in ('tinyint','smallint','mediumint','int','bigint') or engine!= (('InnoDB',),):
            out.update(status='inapplicable',reason='requires InnoDB and a single integer primary key named id')
            return out
        if expired():
            out['status']='budget_exhausted'
            return out
        query(c,'START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY')
        opened=True
        lo,hi=query(c,f'SELECT MIN(id),MAX(id) FROM {table}')[0]
        # Touch the target first: its transaction-scoped MDL prevents replacement between the key check and draws.
        key=query(c,"SELECT s.COLUMN_NAME,c.DATA_TYPE FROM information_schema.STATISTICS s JOIN information_schema.COLUMNS c ON c.TABLE_SCHEMA=s.TABLE_SCHEMA AND c.TABLE_NAME=s.TABLE_NAME AND c.COLUMN_NAME=s.COLUMN_NAME WHERE s.TABLE_SCHEMA=%s AND s.TABLE_NAME=%s AND s.INDEX_NAME='PRIMARY' ORDER BY s.SEQ_IN_INDEX",(db,name))
        engine=query(c,'SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s',(db,name))
        if expired():
            out['status']='budget_exhausted'
        elif len(key)!=1 or key[0][0]!='id' or key[0][1] not in ('tinyint','smallint','mediumint','int','bigint') or engine!= (('InnoDB',),):
            out.update(status='inapplicable',reason='requires InnoDB and a single integer primary key named id')
        elif lo is None:
            out.update(status='exact_empty',estimate='0',lower=0,upper=0,accuracyStatus='exact')
        else:
            lo,hi=int(lo),int(hi);domain=hi-lo+1;cells=(domain+width-1)//width
            out.update(min_id=lo,max_id=hi,domain=domain,cells=cells)
            rng=random.Random(seed);total=0;sum_squares=0
            for n in range(1,max(looks)+1):
                if expired():
                    out['status']='budget_exhausted';break
                left=lo+rng.randrange(cells)*width
                out['attempted_draws']+=1
                cell_count=query(c,f'SELECT COUNT(*) FROM {table} FORCE INDEX(PRIMARY) WHERE id>=%s AND id<=%s',(left,min(left+width-1,hi)))[0][0]
                if not 0<=cell_count<=width:
                    raise RuntimeError('cell bound violated')
                out['completed_draws']+=1
                total+=cell_count;sum_squares+=cell_count**2
                if expired():
                    out['status']='budget_exhausted';break
                if n in looks:
                    k=looks.index(n)+1
                    look_alpha=Decimal(str(alpha))/Decimal(k*(k+1))
                    band=confidence_band(total,sum_squares,n,cells,width,domain,look_alpha)
                    band.update(checkpoint_draws=n,sampled_rows_with_repeats=total,sampled_squared_counts=sum_squares)
                    out['checkpoints'].append(band);out.update(band)
                    out['last_valid_checkpoint_draws']=n
                    out['accuracyStatus']='precision_not_reached'
                    precise_margin=max(Decimal(band['estimate'])-band['lower'],Decimal(band['upper'])-Decimal(band['estimate']))
                    if band['lower']>0 and precise_margin<=Decimal(band['lower'])*Decimal(str(target)):
                        out.update(status='target_reached',accuracyStatus='target_reached');break
            else:
                out['status']='precision_not_reached'
    except pymysql.MySQLError as error:
        out.update(status='query_failed',errno=error.args[0])
    finally:
        if opened:
            try:
                query(c,'ROLLBACK')
                out['cleanupStatus']='rolled_back'
            except pymysql.MySQLError as error:
                out.update(cleanupStatus='failed',cleanup_errno=error.args[0],status='cleanup_failed')
        out['seconds']=round(time.monotonic()-start,4)
        out['draws']=out['completed_draws']  # compatibility; interval size is explicitly last_valid_checkpoint_draws
        if expired():
            out['budgetStatus']='exhausted'
            if out['status'] in ('target_reached','exact_empty','precision_not_reached','not_started'):
                out['status']='budget_exhausted'
    return out
