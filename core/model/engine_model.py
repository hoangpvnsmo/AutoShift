"""Canonical hard-model assembly, same constraints as run_stage3.build_stage3."""
from core.model.model_builder import build_model
from core.constraints.hard_constraints import (add_staffing_constraints,add_shift_eligibility,
    add_previous_day_continuity,add_consecutive_shift_constraints,add_shift_transition_constraints)
from core.constraints.credit_constraints import add_demand_constraints
from core.validation.credit_checks import target_credit, request_map
from core.constraints.admin_constraints import add_admin_constraints,add_all_request_constraints,add_workforce_objective

def build_stage3(data):
    ctx=build_model(data)
    for fn in (add_staffing_constraints,add_shift_eligibility,add_previous_day_continuity,
               add_consecutive_shift_constraints,add_shift_transition_constraints):
        fn(ctx,data)
    demand=add_demand_constraints(ctx,data)
    policy=add_admin_constraints(ctx,data)
    add_all_request_constraints(ctx,data)
    credit=add_workforce_objective(ctx,data)
    # Cận dưới công vượt SHIFT từ cân đối tổng nhu cầu, giúp solver chứng minh tối ưu.
    shift_ids={e.id for e in data.employees if e.group=='SHIFT'}
    fixed_credit=sum(c in {'CT','DT'} for (i,d),c in request_map(data).items() if i in shift_ids)
    capacity=sum(target_credit(data,e) for e in data.employees if e.id in shift_ids)
    ctx.model.add(credit.shift_excess + credit.admin_total >= sum(demand.values())+fixed_credit-capacity)
    return ctx,demand,policy,credit

