from dataclasses import dataclass
from datetime import date
from ortools.sat.python import cp_model
from core.model.model_builder import ScheduleModel


@dataclass
class SolveResult:
    status: str
    # None nếu chưa có nghiệm; 0 nghĩa là không trực (chưa phân loại CT/ĐT/T...).
    assignments: dict[tuple[int, date], int] | None
    wall_time: float


def solve(ctx: ScheduleModel, time_limit: float = 10.0) -> SolveResult:
    if time_limit <= 0:
        raise ValueError('time_limit phải lớn hơn 0.')
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    status = solver.solve(ctx.model)
    assignments = None
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        assignments = {
            (i, d): next((s for s in ctx.shifts if solver.value(ctx.x[i, d, s]) == 1), 0)
            for i in ctx.employee_ids for d in ctx.days
        }
    return SolveResult(solver.status_name(status), assignments, solver.wall_time)
