"""Mã hóa E04 bằng các biến chỉ báo chính xác hai chiều."""
from dataclasses import dataclass
from core.validation.credit_checks import request_map
from core.objectives.rest_checks import rest_windows

@dataclass
class RestModel:
    windows: object
    terms_a: tuple
    terms_b: tuple
    obj_a: object
    obj_b: object


def lex_objective(terms,bounds):
    weight=1; objective=0
    for v,b in reversed(tuple(zip(terms,bounds))):
        objective+=weight*v; weight*=b+1
    if weight>=2**62: raise ValueError('E04: trọng số quá lớn.')
    return objective


def add_rest_balance(ctx,data):
    m=ctx.model; w=rest_windows(data,ctx.days); req=request_map(data)
    current=set(ctx.days); ids=[e.id for e in data.employees if e.group=='SHIFT']
    n=len(ids); D=len(ctx.days); W=len(w.weekends)
    def both(items,name):
        b=m.new_bool_var(name)
        for v in items: m.add(b<=v)
        m.add(b>=sum(items)-len(items)+1)
        return b
    def total(items,cap,name):
        v=m.new_int_var(0,cap,name); m.add(v==sum(items)); return v
    def maximum(items,cap,name):
        v=m.new_int_var(0,cap,name)
        if items: m.add_max_equality(v,items)
        else: m.add(v==0)
        return v
    def gap(items,cap,name):
        if not items: return total([],0,name)
        hi=maximum(items,cap,name+'_hi'); lo=m.new_int_var(0,cap,name+'_lo'); m.add_min_equality(lo,items)
        v=m.new_int_var(0,cap,name); m.add(v==hi-lo); return v
    singles=[]; extras=[]; blocks=[]; weekends=[]; zeros=[]
    for i in ids:
        def off(d):
            if d not in current: return int(data.previous[i,d]=='OFF')
            if req.get((i,d)) in ('CT','DT','BKD'): return 0
            return 1-ctx.worked[i,d]
        def no_shift(d,s):
            if d not in current: return int(data.previous[i,d]!=str(s))
            return 1-ctx.x[i,d,s]
        singles.append(total([both([1-off(a),off(b),1-off(c)],f'e04_single_{i}_{b}') for a,b,c in w.singles],D,f'e04_singles_{i}'))
        extras.append(total([both([off(d) for d in ds],f'e04_long_{i}_{ds[-1]}') for ds in w.long_windows],D,f'e04_extra_{i}'))
        blocks.append(total([both([1-off(a),off(b),off(c)],f'e04_block_{i}_{c}') for a,b,c in w.blocks],D,f'e04_blocks_{i}'))
        v=total([both([off(sat),off(sun)]+[no_shift(d,s) for d,s in pairs],f'e04_weekend_{i}_{sun}') for sat,sun,pairs in w.weekends],W,f'e04_weekends_{i}')
        weekends.append(v)
        if W:
            z=m.new_bool_var(f'e04_no_weekend_{i}'); m.add(v==0).only_enforce_if(z); m.add(v>=1).only_enforce_if(~z); zeros.append(z)
    a=(total(singles,n*D,'e04_single_total'),maximum(singles,D,'e04_single_worst'),total(extras,n*D,'e04_long_total'))
    missing=m.new_int_var(0,n*W,'e04_weekend_missing'); m.add(missing==n*W-sum(weekends))
    b=(total(zeros,n,'e04_no_weekend'),missing,gap(weekends,W,'e04_weekend_gap'),gap(blocks,D,'e04_block_gap'))
    return RestModel(w,a,b,lex_objective(a,(n*D,D,n*D)),lex_objective(b,(n,n*W,W,D)))
