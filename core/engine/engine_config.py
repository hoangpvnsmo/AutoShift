"""Stable public configuration and ordered objective registry for AutoShift."""
from dataclasses import dataclass
from math import isfinite
from secrets import randbits

@dataclass(frozen=True)
class StageSpec:
    key: str
    label: str
    flag: str | None
    budget: str

STAGES = (
    StageSpec('workforce', 'giai đoạn 3', None, 'base_time'),
    StageSpec('e01', 'E01', 'balance_shift_types', 'quality_time'),
    StageSpec('e02', 'E02', 'balance_night_shifts', 'night_time'),
    StageSpec('holiday', 'E03 lễ', 'balance_holiday_shifts', 'holiday_time'),
    StageSpec('weekend', 'E03 cuối tuần', 'balance_weekend_shifts', 'weekend_time'),
    StageSpec('rest_a', 'E04a cụm nghỉ', 'balance_rest_days', 'rest_time'),
    StageSpec('rest_b', 'E04b cuối tuần nghỉ', 'balance_rest_days', 'rest_weekend_time'),
    StageSpec('rotation', 'E05 luân phiên linh hoạt', 'minimize_shift_changes', 'rotation_time'),
    StageSpec('preferences', 'E06 nguyện vọng mềm', 'honor_preferences', 'preference_time'),
)
KEYS = tuple(s.key for s in STAGES[1:])

@dataclass(frozen=True)
class EngineOptions:
    base_time: float = 120
    quality_time: float = 120
    night_time: float = 120
    holiday_time: float = 120
    weekend_time: float = 120
    rest_time: float = 60
    rest_weekend_time: float = 60
    rotation_time: float = 60
    preference_time: float = 30
    ideal_time: float = 20
    validation_time: float = 10
    fresh: bool = False
    lns_fraction: float = .5
    lns_slice: float = 5.
    lns_seed: int | None = None
    adaptive: bool = True
    stall_limit: int = 5

    def __post_init__(self):
        for name in (*[s.budget for s in STAGES], 'validation_time', 'lns_slice'):
            x = getattr(self, name)
            if not isfinite(x) or x <= 0: raise ValueError(f'{name} phải hữu hạn và > 0.')
        if not isfinite(self.ideal_time) or self.ideal_time < 0:
            raise ValueError('ideal_time phải hữu hạn và >= 0.')
        if not isfinite(self.lns_fraction) or not 0 <= self.lns_fraction <= .9:
            raise ValueError('lns_fraction phải trong 0..0.9.')
        if type(self.stall_limit) is not int or self.stall_limit < 3:
            raise ValueError('stall_limit phải là số nguyên >= 3.')
        if self.lns_seed is None: object.__setattr__(self, 'lns_seed', randbits(32))
        elif type(self.lns_seed) is not int: raise ValueError('lns_seed phải là số nguyên.')

    def budget_for(self, key):
        return getattr(self, next(s.budget for s in STAGES if s.key == key))

    @property
    def search_mode(self):
        return 'GLOBAL' if self.lns_fraction == 0 else 'ALNS' if self.adaptive else 'LNS'
