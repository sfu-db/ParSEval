#!/usr/bin/env python3
"""Hand-built SMT workloads inspired by postgres.csv; no query compiler/scheduler.

Only Solver.solve is timed. Catalog DDL is setup. SAT results are independently
checked after timing. See SOURCES for pattern provenance, not SQL equivalence.
"""
from __future__ import annotations
import argparse
from datetime import date
import json
import multiprocessing as mp
import platform
import time
import z3
from benchmark_smt_operators import build_case as operator_case
from parseval.catalog import Catalog
from parseval.coverage.model import CoverageSite, CoverageTarget, WitnessedObligation
from parseval.coverage import target_is_covered
from parseval.smt import Solver, SolveStatus
from parseval.terms.arena import TermArena
from parseval.terms.builder import IRBuilder, AggregateCall, WindowCall
from parseval.terms.context import AggregateKind, AggregateSpec
from parseval.terms.decls import RowShape
from parseval.terms.sorts import RowSort, ScalarSort
from parseval.terms.sorts import INTEGER, STRING, DECIMAL, DATE
from parseval.terms.terms import WindowFunctionKind, WindowFrame, WindowFrameMode, WindowBoundary, WindowBoundaryKind
from parseval.uexpr import validate_instance
from parseval.uexpr.observation import WeightCondition, BagCardinalityCondition
from parseval.uexpr.witness import UnitWitnessPlan

SOURCES = {
    'join_exists_distinct': 'so CSV index 154 q2: six-table join, correlated EXISTS, COUNT(DISTINCT)',
    'group_sum_case': 'dsb CSV index 174 q2: grouped SUM(CASE WHEN ...)',
    'union_aggregate': 'tpcds CSV index 320 q2: UNION ALL aggregate inputs',
    'cte_reuse': 'so CSV index 151 q2: relation bindings and repeated access',
    'min_join': 'tpch CSV index 280 q2: join and grouped minimum',
}
CASES = tuple(SOURCES) + ('like_literal','like_dynamic','ilike','lower','date_cast',
    'stddev','order_limit','window','support_diversity','product_counts','exact_join','nested_absence','shared_dag')

def build_case(name, size):
    if name in ('support_diversity','product_counts','exact_join','nested_absence','shared_dag'):
        return operator_case(name,size)
    catalog=Catalog.from_ddl(';'.join(f'CREATE TABLE r{i}(x INT, v INT, s TEXT)' for i in range(max(7,size))),dialect='postgres')
    arena=TermArena(catalog.context);b=IRBuilder(arena);ctx=catalog.context
    rels=list(ctx.relations());schema=rels[0][1].schema;bases=[b.base(r) for r,_ in rels]
    conditions=[];expected=SolveStatus.SAT
    def require(term,lo=1,hi=None):conditions.append(WeightCondition(b.finish(term),lo,hi))
    def count(source):return b.sum(RowSort(schema),lambda r:b.at(source,r))
    def aggregate(source,kind,argument=None,distinct=False):
        out=ScalarSort(DECIMAL if kind=='avg' else INTEGER,kind!='count')
        spec=ctx.intern_aggregate(AggregateSpec(None if argument is None else ScalarSort(INTEGER,True),out,kind=AggregateKind(kind) if kind!='stddev' else None,operator=kind))
        out_schema=ctx.intern_schema(RowShape((out,)))
        return b.global_fold(source,(AggregateCall(spec,argument,distinct=distinct),),out_schema),out_schema
    def aggregate_value(source,kind,expected_value,argument=lambda r:b.field(r,0),distinct=False):
        folded,fs=aggregate(source,kind,argument,distinct)
        good=b.sum(RowSort(fs),lambda r:b.mul(b.at(folded,r),b.indicator(b.eq3(b.field(r,0),b.literal(expected_value,INTEGER)))))
        require(good);return folded
    if name=='join_exists_distinct':
        # Six equal-key tables; seventh table supplies correlated score witness.
        def chain(i,first,previous):
            if i==6:
                exists=b.sum(RowSort(schema),lambda c:b.mul(b.at(bases[6],c),b.indicator(b.eq3(b.field(c,0),b.field(first,0))),b.indicator(b.lt3(b.field(first,1),b.field(c,1)))))
                return b.squash(exists)
            return b.sum(RowSort(schema),lambda r:b.mul(b.at(bases[i],r),b.indicator(b.eq3(b.field(previous,0),b.field(r,0))),chain(i+1,first,r)))
        joined=b.bag_lam(schema,lambda out:b.mul(b.at(bases[0],out),chain(1,out,out)))
        term=aggregate_value(joined,'count',size,distinct=True)
    elif name=='group_sum_case':
        keys=ctx.intern_schema(RowShape((ScalarSort(INTEGER,True),)))
        out=ctx.intern_schema(RowShape((ScalarSort(INTEGER,True),ScalarSort(INTEGER,True))))
        spec=ctx.intern_aggregate(AggregateSpec(ScalarSort(INTEGER,True),ScalarSort(INTEGER,True),kind=AggregateKind.SUM,operator='sum'))
        term=b.group_fold(bases[0],lambda r:b.row(keys,(b.field(r,0),)),(AggregateCall(spec,lambda r:b.case(b.lt3(b.literal(0,INTEGER),b.field(r,1)),b.field(r,1),b.null(INTEGER))),),out)
        for value in range(size):
            require(b.sum(RowSort(out),lambda r,value=value:b.mul(b.at(term,r),b.indicator(b.eq3(b.field(r,0),b.literal(value,INTEGER))),b.indicator(b.eq3(b.field(r,1),b.literal(10,INTEGER))))))
    elif name=='union_aggregate':
        union=b.bag_lam(schema,lambda r:b.add(*(b.at(base,r) for base in bases[:size])))
        for base in bases[:size]:require(count(base),2,2)
        term=aggregate_value(union,'count',2*size,argument=None)
    elif name=='cte_reuse':
        term=b.let_rel(bases[0],lambda rel:b.bag_lam(schema,lambda r:b.mul(b.at(rel,r),b.sum(RowSort(schema),lambda other:b.mul(b.at(rel,other),b.indicator(b.eq3(b.field(r,0),b.field(other,0))))))))
        require(count(bases[0]),size,size)
        conditions.append(BagCardinalityCondition(b.finish(term),size*size,size*size))
    elif name=='min_join':
        joined=b.bag_lam(schema,lambda r:b.mul(b.at(bases[0],r),b.sum(RowSort(schema),lambda s:b.mul(b.at(bases[1],s),b.indicator(b.eq3(b.field(r,0),b.field(s,0)))))))
        require(count(bases[0]),size,size);require(count(bases[1]),size,size)
        term=aggregate_value(joined,'min',7,argument=lambda r:b.field(r,1))
    elif name=='stddev':
        term=aggregate_value(bases[0],'stddev',1)
    elif name in ('order_limit','window'):
        if name=='order_limit':
            term=b.forget_order(b.take(b.literal(size,INTEGER),b.order_by(bases[0],(b.order_key(lambda r:b.field(r,0)),))))
        else:
            out=ctx.intern_schema(RowShape((*ctx.schema(schema).fields,ScalarSort(INTEGER))))
            frame=WindowFrame(WindowFrameMode.ROWS,WindowBoundary(WindowBoundaryKind.UNBOUNDED_PRECEDING),WindowBoundary(WindowBoundaryKind.CURRENT_ROW))
            term=b.window(bases[0],(WindowCall(WindowFunctionKind.ROW_NUMBER,ScalarSort(INTEGER),(),(),(),frame),),out)
        conditions.append(BagCardinalityCondition(b.finish(term),1));expected=SolveStatus.UNSUPPORTED
    else:
        def predicate(r):
            value=b.field(r,2)
            if name=='like_literal':return b.like3(value,b.literal('%cloud%',STRING))
            if name=='like_dynamic':return b.like3(value,value)
            if name=='ilike':return b.ilike3(value,b.literal('%CLOUD%',STRING))
            if name=='lower':return b.eq3(b.apply('lower',(value,),ScalarSort(STRING,True)),b.literal('cloud',STRING))
            return b.eq3(b.apply('cast_string_to_date',(value,),ScalarSort(DATE,True)),b.literal(date(2024,2,29),DATE))
        term=b.sum(RowSort(schema),lambda r:b.mul(b.at(bases[0],r),b.indicator(predicate(r))))
        require(term,size,size)
    root=b.finish(term)
    target=CoverageTarget(f'{name}:{size}',CoverageSite(root,()),WitnessedObligation(UnitWitnessPlan(),tuple(conditions)),name)
    return catalog,arena,target,expected

def worker(sender,name,size,options):
    latest={}
    try:
        catalog,arena,target,expected=build_case(name,size)
        solver=Solver(catalog,**options)
        sender.send({'stage':'solve'})
        original=z3.Solver.check;check_times=[]
        def check(self,*a,**kw):
            start=time.perf_counter()
            try:return original(self,*a,**kw)
            finally:check_times.append(time.perf_counter()-start)
        z3.Solver.check=check
        start=time.perf_counter();result=solver.solve(arena,target);elapsed=time.perf_counter()-start
        latest={'stage':'validate','status':result.status.value,'expected':expected.value,'reason':result.reason,
                'solve_s':elapsed,'z3_s':sum(check_times),'z3_checks':len(check_times),
                'attempts':result.statistics.attempts,'encoding_steps':result.statistics.encoding_steps,
                'circuit_nodes':result.statistics.circuit_nodes,'support':[(r.value,n) for r,n in result.statistics.support],
                'rows':None if result.instance is None else sum(len(result.instance.rows(r)) for r,_ in catalog.context.relations())}
        sender.send(latest)
        start=time.perf_counter()
        valid=None
        if result.status is SolveStatus.SAT:
            valid=not validate_instance(result.instance) and target_is_covered(arena,result.instance,target)
        sender.send({**latest,'stage':'done','validated':valid,'validation_s':time.perf_counter()-start})
    except Exception as e:sender.send({**latest,'stage':'error','error':type(e).__name__,'reason':str(e)})
    finally:sender.close()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cases',nargs='+',choices=CASES,default=list(CASES));p.add_argument('--sizes',nargs='+',type=int,default=[1,2,4,8]);p.add_argument('--repeat',type=int,default=3)
    p.add_argument('--timeout-ms',type=int,default=3000);p.add_argument('--wall-s',type=float,default=10)
    p.add_argument('--max-support',type=int,default=8);p.add_argument('--max-rows',type=int,default=10000);p.add_argument('--max-encoding-steps',type=int,default=100000);p.add_argument('--minimize',action='store_true')
    args=p.parse_args()
    if min(*args.sizes,args.repeat,args.timeout_ms,args.wall_s,args.max_support,args.max_rows,args.max_encoding_steps)<=0:p.error('sizes, repetitions and limits must be positive')
    options={k:getattr(args,k) for k in ('timeout_ms','max_support','max_rows','max_encoding_steps','minimize')};ctx=mp.get_context('spawn')
    for name in args.cases:
        for size in args.sizes:
            for repeat in range(args.repeat):
                receiver,sender=ctx.Pipe(False);process=ctx.Process(target=worker,args=(sender,name,size,options));process.start();sender.close();deadline=time.monotonic()+args.wall_s;latest={'stage':'build'}
                while receiver.poll(max(0,deadline-time.monotonic())):
                    try:latest=receiver.recv()
                    except EOFError:break
                    if latest['stage'] in ('done','error'):break
                if latest['stage'] not in ('done','error'):latest={**latest,'last_stage':latest['stage'],'stage':'wall_timeout' if process.is_alive() else 'worker_exit'}
                if process.is_alive():process.terminate()
                process.join();receiver.close()
                print(json.dumps({'case':name,'size':size,'repeat':repeat,'limits':options,'wall_s':args.wall_s,'source':SOURCES.get(name),'python':platform.python_version(),'z3':z3.get_version_string(),**latest}),flush=True)
if __name__=='__main__':main()
