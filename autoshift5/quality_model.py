"""Reuse the existing Stage 3 / E01-E06 expressions, one scalar at a time."""
from core.objectives.soft_constraints import add_shift_balance
from core.objectives.night_constraints import add_night_balance
from core.objectives.day_balance_v2 import add_day_balance_v2
from core.objectives.day_balance_checks import day_sets
from core.objectives.rest_constraints import add_rest_balance
from core.objectives.flexible_rotation import add_flexible_rotation
from core.objectives.preference_constraints import add_preferences
from core.engine.engine_config import STAGES


def quality_terms(ctx, data, credit, prefs):
    terms = [(credit.shift_excess, credit.admin_total, credit.shift_spread, credit.shift_deviation, credit.admin_deviation)]
    balance = add_shift_balance(ctx, data)
    night = add_night_balance(ctx, data, balance.counts)
    holiday, weekend = day_sets(data, ctx.days)
    _, ht, _ = add_day_balance_v2(ctx, data, holiday, 'holiday')
    _, wt, _ = add_day_balance_v2(ctx, data, weekend, 'weekend')
    rest = add_rest_balance(ctx, data)
    rotation = add_flexible_rotation(ctx, data)
    pt, _, _ = add_preferences(ctx, data, prefs)
    terms.extend([(balance.worst_gap, balance.total_gap), (night.worst_gap, night.total_gap), ht, wt,
                  rest.terms_a, rest.terms_b, rotation.terms, pt])
    active = [spec.flag is None or data.rules.get('optimization', {}).get(spec.flag, True) for spec in STAGES]
    return terms, active
