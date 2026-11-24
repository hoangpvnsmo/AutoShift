"""E06: điểm phạt nguyên của nguyện vọng không được đáp ứng."""
from core.validation.credit_checks import request_map, NON_SHIFT_CODES
from core.objectives.preference_checks import no_shift_code


def add_preferences(ctx,data,prefs):
    m=ctx.model; people={e.id:e for e in data.employees}; req=request_map(data); penalties=[]
    for p in prefs:
        i,d=p.employee_id,p.day
        if p.kind=='PREFER_SHIFT': ok=ctx.x[i,d,p.shift]
        elif p.kind=='AVOID_SHIFT': ok=1-ctx.x[i,d,p.shift]
        elif p.kind=='PREFER_WORK': ok=ctx.worked[i,d]
        elif p.kind=='PREFER_OFF':
            fixed=req.get((i,d))
            if fixed in NON_SHIFT_CODES: ok=int(fixed=='OFF')
            elif no_shift_code(people[i],d,data,fixed)=='OFF': ok=1-ctx.worked[i,d]
            else: ok=0
        else: raise ValueError(p.kind)
        penalties.append(p.weight*(1-ok))
    cap=sum(p.weight for p in prefs)
    v=m.new_int_var(0,cap,'e06_unmet_weight'); m.add(v==sum(penalties))
    return (v,),v,(cap,)
